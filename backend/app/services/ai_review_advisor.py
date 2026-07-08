"""AI-powered review advisor — calls LLM to recommend words for today.

Runs once per day (triggered after study time or manually from the
dashboard). Feeds the LLM with 7-day review stats and asks it to
pick words that need the most attention.

Output: {"recommended_words": [...], "reasoning": "...", "suggested_mode": "..."}

The result is stored in AiDailyReport.review_recommendations alongside
the existing daily summary, so the dashboard can read both at once.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.models.ai_daily_report import AiDailyReport
from app.models.learning_item import LearningItem
from app.models.review_log import ReviewLog
from app.models.word_memory_state import WordMemoryState
from app.services.llm_translation import LlmTranslationSettings, call_llm_generate


logger = logging.getLogger(__name__)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _build_7day_profile(db: Session, user_id: UUID, now: datetime) -> dict[str, object]:
    """Aggregate the child's last-7-day review data into a compact profile.

    Returns a dict suitable for inclusion in the LLM prompt. Kept small
    on purpose — the LLM prompt has a token budget and we want it to
    focus on patterns, not raw rows.
    """
    week_ago = now - timedelta(days=7)

    # Per-word error breakdown (most recent 7 days)
    word_rows = db.execute(
        select(
            ReviewLog.learning_item_id,
            func.count(ReviewLog.id).label("total"),
            func.sum(func.cast(ReviewLog.is_correct, func.Integer)).label("correct"),
            func.array_agg(ReviewLog.error_type.distinct()).label("error_types"),
        )
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= week_ago,
        )
        .group_by(ReviewLog.learning_item_id)
        .order_by(func.count(ReviewLog.id).desc())
        .limit(30)
    ).all()

    # Map learning_item_id → english_text
    item_ids = [row.learning_item_id for row in word_rows if row.learning_item_id]
    item_texts: dict[UUID, str] = {}
    if item_ids:
        for item in db.scalars(
            select(LearningItem).where(LearningItem.id.in_(item_ids))
        ).all():
            item_texts[item.id] = item.english_text or "?"

    # Build per-word list
    word_list: list[dict[str, object]] = []
    for li_id, total, correct, error_types in word_rows:
        english = item_texts.get(li_id, str(li_id)[:8])
        accuracy = round(int(correct or 0) / int(total), 2) if total else 0.0
        word_list.append({
            "word": english,
            "reviews": int(total),
            "accuracy": accuracy,
            "error_types": [str(et) for et in (error_types or []) if et],
        })

    # Total stats
    totals_row = db.execute(
        select(
            func.count(ReviewLog.id).label("total"),
            func.sum(func.cast(ReviewLog.is_correct, func.Integer)).label("correct"),
        )
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= week_ago)
    ).one()
    total_reviews = int(totals_row.total)
    total_correct = int(totals_row.correct or 0)
    overall_accuracy = round(total_correct / total_reviews, 3) if total_reviews else 0.0

    # Per-type accuracy
    type_rows = db.execute(
        select(
            ReviewLog.review_mode,
            func.count(ReviewLog.id).label("total"),
            func.sum(func.cast(ReviewLog.is_correct, func.Integer)).label("correct"),
        )
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= week_ago)
        .group_by(ReviewLog.review_mode)
        .order_by(func.count(ReviewLog.id).desc())
        .limit(10)
    ).all()

    type_breakdown: list[dict[str, object]] = []
    for mode, total, correct in type_rows:
        acc = round((int(correct or 0) / int(total)) * 100) if total else 0
        kind = "spelling" if any(k in (mode or "").lower() for k in ("spell", "recall", "preview", "missing", "hidden")) else "choice"
        type_breakdown.append({
            "mode": mode or "other",
            "total": int(total),
            "accuracy_pct": acc,
            "kind": kind,
        })

    # Words with high forget risk (from WordMemoryState)
    risky_words = [
        {
            "word": ws.word,
            "strength": ws.memory_strength,
            "risk": ws.forget_risk,
            "consecutive_errors": ws.consecutive_error_count,
        }
        for ws in db.scalars(
            select(WordMemoryState)
            .where(
                WordMemoryState.user_id == user_id,
                WordMemoryState.forget_risk >= 0.5,
            )
            .order_by(WordMemoryState.forget_risk.desc())
            .limit(20)
        ).all()
    ]

    return {
        "period": "last 7 days",
        "total_reviews": total_reviews,
        "overall_accuracy": overall_accuracy,
        "type_breakdown": type_breakdown,
        "top_words": word_list,
        "high_risk_words": risky_words,
    }


def _build_prompt(profile: dict[str, object]) -> str:
    """Build the LLM prompt from the 7-day profile dict."""
    return (
        "你是一个儿童英语学习助手。请你分析一个孩子的最近7天学习数据，"
        "帮孩子选出今天最需要复习的单词。\n\n"
        "**学习数据分析**\n"
        f"- 7天总复习次数：{profile['total_reviews']}\n"
        f"- 整体正确率：{profile['overall_accuracy']}\n\n"
        "**按题型正确率**\n"
        + "\n".join(
            f"  - {t['mode']}（{'拼写' if t['kind'] == 'spelling' else '选择'}）：{t['total']}次，正确率 {t['accuracy_pct']}%"
            for t in profile.get('type_breakdown', [])
        ) + "\n\n"
        "**高频词（最近7天复习次数最多的词，含正确率）**\n"
        + "\n".join(
            f"  - {w['word']}：复习{w['reviews']}次，正确率{w['accuracy']}，"
            f"错误类型：{', '.join(map(str, w.get('error_types', []))) or '无'}"
            for w in profile.get('top_words', [])[:15]
        ) + "\n\n"
        "**高遗忘风险词**\n"
        + "\n".join(
            f"  - {w['word']}：记忆强度 {w['strength']}，遗忘风险 {w['risk']}，连续错误 {w['consecutive_errors']}次"
            for w in profile.get('high_risk_words', [])[:10]
        ) + "\n\n"
        "**你的任务**\n"
        "1. 从以上数据中选出 3-8 个今天最需要复习的单词。优先选择：\n"
        "   - 遗忘风险高（risk >= 0.6）的词\n"
        "   - 正确率低（accuracy < 0.6）的词\n"
        "   - 某种错误类型反复出现的词（如 meaning 错误多说明孩子不理解中文意思）\n"
        "2. 给出简短的中文理由（1-2句话，家长可读）\n"
        '3. 建议优先练习的题型（如\'先做英选中选择题加深理解，再拼写\'）\n\n'
        "**输出格式**（严格的 JSON，不要任何额外文字）：\n"
        '{"recommended_words":["word1","word2",...],"reasoning":"中文理由","suggested_mode":"建议练法"}'
    )


def generate_review_advice(
    db: Session,
    user_id: UUID,
    settings: LlmTranslationSettings,
    *,
    force: bool = False,
) -> dict[str, object]:
    """Run the AI review advisor and store the result.

    If a recommendation already exists for today and force=False, return
    the cached result. This keeps the LLM cost at once-per-day.

    Returns the review_recommendations dict (same schema written to DB).
    """
    now = datetime.now(LOCAL_TIMEZONE)
    today = now.date()

    # Check for existing
    existing = db.scalar(
        select(AiDailyReport.review_recommendations).where(
            AiDailyReport.user_id == user_id,
            AiDailyReport.report_date == today,
        )
    )
    if existing is not None and existing and not force:
        return dict(existing) if isinstance(existing, dict) else {}

    # Build data profile and prompt
    profile = _build_7day_profile(db, user_id, now)
    prompt = _build_prompt(profile)

    # Call LLM
    raw = call_llm_generate(settings, prompt)
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    # Parse JSON
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI review advisor returned invalid JSON: %s", text[:200])
        return {"error": "LLM returned invalid JSON", "raw": text[:500]}

    # Normalize
    recommendations: dict[str, object] = {
        "recommended_words": result.get("recommended_words", []) or [],
        "reasoning": str(result.get("reasoning", "") or ""),
        "suggested_mode": str(result.get("suggested_mode", "") or ""),
        "generated_at": now.isoformat(),
        "profile_snapshot": {
            "total_reviews": profile.get("total_reviews", 0),
            "overall_accuracy": profile.get("overall_accuracy", 0),
        },
    }

    # Store alongside the daily report
    report = db.scalar(
        select(AiDailyReport).where(
            AiDailyReport.user_id == user_id,
            AiDailyReport.report_date == today,
        )
    )
    if report is not None:
        report.review_recommendations = recommendations
        db.add(report)
        db.commit()
    else:
        # No daily report yet — create a minimal one just for the recommendations
        report = create_minimal_report_for_recommendations(db, user_id, today, recommendations)

    return recommendations


def create_minimal_report_for_recommendations(
    db: Session,
    user_id: UUID,
    report_date,
    recommendations: dict[str, object],
) -> AiDailyReport:
    """Create a minimal AiDailyReport row just to store review recommendations.

    Used when the daily report hasn't been generated yet (e.g. parent
    clicks "AI 分析" before 8pm).
    """
    report = AiDailyReport(
        user_id=user_id,
        report_date=report_date,
        review_recommendations=recommendations,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def get_todays_recommendations(
    db: Session,
    user_id: UUID,
) -> dict[str, object] | None:
    """Return today's stored recommendations, or None if not yet generated."""
    now = datetime.now(LOCAL_TIMEZONE)
    today = now.date()
    existing = db.scalar(
        select(AiDailyReport.review_recommendations).where(
            AiDailyReport.user_id == user_id,
            AiDailyReport.report_date == today,
        )
    )
    if existing is None or not existing:
        return None
    return dict(existing) if isinstance(existing, dict) else None


def generate_review_advice_if_needed(
    db: Session,
    user_id: UUID,
    settings: LlmTranslationSettings,
) -> bool:
    """Called by the daily report system at 8pm. Generates advice if not
    already cached. Returns True if new advice was generated."""
    existing = get_todays_recommendations(db, user_id)
    if existing is not None and existing.get("recommended_words"):
        return False
    try:
        generate_review_advice(db, user_id, settings, force=True)
        return True
    except Exception as exc:
        logger.warning("AI review advisor failed for user=%s: %s", user_id, exc)
        return False
