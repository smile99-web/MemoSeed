"""Learning Replay System: event recording, heatmap, hour/minute replay."""

from datetime import UTC, date, datetime, timedelta
from typing import Iterable
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.models.learning_event import LearningEvent, LearningMinuteStat
from app.models.review_log import ReviewLog
from app.models.mistake_log import MistakeLog
from app.models.learning_item import LearningItem
from app.models.study_time_log import StudyTimeLog
from app.models.user import User

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Color intensity bands (minutes of study per day)
COLOR_LEVELS = [
    (0, 0, "#ebedf0"),       # gray
    (1, 15, "#9be9a8"),      # light green
    (16, 30, "#40c463"),     # mid green
    (31, 45, "#30a14e"),     # dark green
    (46, 9999, "#216e39"),   # deepest green
]


def color_for_minutes(minutes: int) -> str:
    for lo, hi, color in COLOR_LEVELS:
        if lo <= minutes <= hi:
            return color
    return COLOR_LEVELS[0][2]


def categorize_review_mode(review_mode: str | None) -> str:
    """Map review_mode to one of: spelling, english_to_chinese, chinese_to_english, phrase, sentence, other."""
    if not review_mode:
        return "other"
    rm = review_mode.lower()
    if "chinese_to_english" in rm or "listen_spell" in rm or "hidden_recall" in rm or "missing_letter" in rm:
        return "chinese_to_english"
    if "english_to_chinese" in rm or "listen_choose" in rm or "match_translation" in rm:
        return "english_to_chinese"
    if "sentence" in rm:
        return "sentence"
    if "phrase" in rm:
        return "phrase"
    if "spell" in rm or "recall" in rm or "preview" in rm or "context" in rm or "hinted" in rm:
        return "spelling"
    return "other"


def record_learning_event(
    db: Session,
    user_id: UUID,
    review_log: ReviewLog,
    learning_item: LearningItem | None,
    duration_ms: int = 0,
) -> LearningEvent | None:
    """Persist a single review attempt as a learning event + increment minute stat.

    Returns None if the review was unscored (no real attempt).
    """
    if review_log.score is None or review_log.reviewed_at is None:
        return None

    effective_duration_ms = duration_ms if duration_ms > 0 else int(max(review_log.duration_seconds or 0, 0) * 1000)
    local_dt = review_log.reviewed_at.astimezone(LOCAL_TIMEZONE)
    event = LearningEvent(
        user_id=user_id,
        learning_item_id=review_log.learning_item_id,
        review_log_id=review_log.id,
        occurred_at=review_log.reviewed_at,
        event_date=local_dt.date(),
        event_hour=local_dt.hour,
        event_minute=local_dt.minute,
        event_week=local_dt.isocalendar()[1],
        event_year=local_dt.year,
        item_type=(learning_item.item_type if learning_item else "word"),
        review_mode=review_log.review_mode,
        is_correct=review_log.is_correct,
        score=review_log.score,
        english_text=(learning_item.english_text if learning_item else ""),
        chinese_text=(learning_item.chinese_text if learning_item else None),
        response_text=review_log.response_text,
        duration_ms=max(0, effective_duration_ms),
        error_type=review_log.error_type,
    )
    db.add(event)
    db.flush()
    _increment_minute_stat(db, event)
    return event


def _increment_minute_stat(db: Session, event: LearningEvent) -> None:
    """Upsert pre-aggregated minute stats — fast heatmap/histogram queries."""
    category = categorize_review_mode(event.review_mode)
    stmt = select(LearningMinuteStat).where(
        LearningMinuteStat.user_id == event.user_id,
        LearningMinuteStat.stat_date == event.event_date,
        LearningMinuteStat.stat_hour == event.event_hour,
        LearningMinuteStat.stat_minute == event.event_minute,
    )
    stat = db.scalar(stmt)
    if stat is None:
        stat = LearningMinuteStat(
            user_id=event.user_id,
            stat_date=event.event_date,
            stat_hour=event.event_hour,
            stat_minute=event.event_minute,
            total_events=0,
            spelling_events=0,
            english_to_chinese_events=0,
            chinese_to_english_events=0,
            phrase_events=0,
            sentence_events=0,
            correct_events=0,
            incorrect_events=0,
            study_duration_ms=0,
        )
        db.add(stat)
        db.flush()
    stat.total_events = (stat.total_events or 0) + 1
    if category == "spelling":
        stat.spelling_events = (stat.spelling_events or 0) + 1
    elif category == "english_to_chinese":
        stat.english_to_chinese_events = (stat.english_to_chinese_events or 0) + 1
    elif category == "chinese_to_english":
        stat.chinese_to_english_events = (stat.chinese_to_english_events or 0) + 1
    elif category == "phrase":
        stat.phrase_events = (stat.phrase_events or 0) + 1
    elif category == "sentence":
        stat.sentence_events = (stat.sentence_events or 0) + 1
    if event.is_correct:
        stat.correct_events = (stat.correct_events or 0) + 1
    else:
        stat.incorrect_events = (stat.incorrect_events or 0) + 1
    stat.study_duration_ms = (stat.study_duration_ms or 0) + event.duration_ms
    stat.updated_at = datetime.now(UTC)


def build_heatmap(db: Session, user_id: UUID, year: int | None = None) -> dict:
    """Return year-view heatmap: minutes studied per day for the given year."""
    target_year = year or datetime.now(LOCAL_TIMEZONE).year
    start = date(target_year, 1, 1)
    end = date(target_year, 12, 31)

    rows = db.execute(
        select(
            LearningMinuteStat.stat_date,
            func.sum(LearningMinuteStat.study_duration_ms).label("ms"),
            func.sum(LearningMinuteStat.total_events).label("events"),
        )
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date >= start,
            LearningMinuteStat.stat_date <= end,
        )
        .group_by(LearningMinuteStat.stat_date)
    ).all()

    # Also get StudyTimeLog data for accurate daily study minutes
    study_rows = db.execute(
        select(
            func.date(StudyTimeLog.recorded_at).label("d"),
            func.sum(StudyTimeLog.duration_seconds).label("secs"),
        )
        .where(
            StudyTimeLog.user_id == user_id,
            func.date(StudyTimeLog.recorded_at) >= start,
            func.date(StudyTimeLog.recorded_at) <= end,
        )
        .group_by(func.date(StudyTimeLog.recorded_at))
    ).all()
    study_by_date: dict[str, float] = {str(d): float(s or 0) for d, s in study_rows}

    event_by_date: dict[str, tuple[float, int]] = {}
    for d, ms, events in rows:
        event_by_date[str(d)] = (round((ms or 0) / 60000, 1), int(events or 0))

    # Merge: use the MAX of event-measured minutes and StudyTimeLog minutes
    first_day = date(target_year, 1, 1)
    year_days = 366 if (target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0)) else 365
    days = []
    for i in range(year_days):
        d = (first_day + timedelta(days=i)).isoformat()
        ev_min, ev_count = event_by_date.get(d, (0.0, 0))
        st_min = round(study_by_date.get(d, 0) / 60, 1)
        real_min = max(ev_min, st_min)
        days.append({
            "date": d,
            "minutes": real_min,
            "events": ev_count,
            "color": color_for_minutes(int(real_min)),
        })

    total_minutes = sum(d["minutes"] for d in days)
    total_days = sum(1 for d in days if d["events"] > 0 or d["minutes"] > 0)
    return {
        "year": target_year,
        "days": days,
        "total_minutes": round(total_minutes, 1),
        "active_days": total_days,
    }


def build_day_detail(db: Session, user_id: UUID, day: date) -> dict:
    """All events for a single day, grouped by hour."""
    rows = db.execute(
        select(LearningMinuteStat)
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date == day,
        )
        .order_by(LearningMinuteStat.stat_hour.asc(), LearningMinuteStat.stat_minute.asc())
    ).scalars().all()
    total_events = sum(r.total_events for r in rows)
    total_correct = sum(r.correct_events for r in rows)
    total_ms = sum(r.study_duration_ms for r in rows)
    total_minutes = round(total_ms / 60000, 1)
    accuracy = round((total_correct / total_events) * 100, 1) if total_events else 0.0
    mistake_count = db.scalar(
        select(func.count(MistakeLog.id)).where(
            MistakeLog.user_id == user_id,
            MistakeLog.occurred_at >= datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TIMEZONE),
            MistakeLog.occurred_at < datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TIMEZONE),
        )
    ) or 0

    hours_map: dict[int, list[dict]] = {}
    for r in rows:
        hours_map.setdefault(r.stat_hour, []).append({
            "minute": r.stat_minute,
            "spelling": r.spelling_events,
            "english_to_chinese": r.english_to_chinese_events,
            "chinese_to_english": r.chinese_to_english_events,
            "phrase": r.phrase_events,
            "sentence": r.sentence_events,
            "total": r.total_events,
            "correct": r.correct_events,
            "incorrect": r.incorrect_events,
            "accuracy": round((r.correct_events / r.total_events) * 100, 1) if r.total_events else 0.0,
        })

    hours = []
    for h, minutes in sorted(hours_map.items()):
        total_ev = sum(m["total"] for m in minutes)
        correct_ev = sum(m["correct"] for m in minutes)
        hour_accuracy = round((correct_ev / total_ev) * 100, 1) if total_ev else 0.0
        hour_study_sec = sum(m.get("study_seconds", 0) for m in minutes)
        hours.append({
            "hour": h,
            "label": f"{h:02d}:00-{h+1:02d}:00",
            "minutes": minutes,
            "modes": _build_mode_breakdown_for_hour(db, user_id, day, h),
            "total_events": total_ev,
            "accuracy": hour_accuracy,
            "study_minutes": round(hour_study_sec / 60, 1),
        })
    return {
        "date": day.isoformat(),
        "study_minutes": total_minutes,
        "total_events": total_events,
        "accuracy": accuracy,
        "mistake_count": mistake_count,
        "hours": hours,
        "day_modes": _aggregate_modes_by_day(db, user_id, day),
    }


MODE_LABELS = {
    "word-recall": "无提示拼写",
    "word-hinted": "有提示拼写",
    "word-preview": "预览后拼写",
    "word-context": "句中拼写",
    "word-listen_spell": "听音拼写",
    "word-chinese_to_english": "看中文拼写",
    "word-missing_letter": "缺字母填空",
    "word-hidden_recall": "隐藏拼写",
    "word-english_to_chinese": "英选中",
    "word-listen_choose_chinese": "听音选中文",
    "word-match_translation": "中英配对",
    "sentence-spelling": "整句拼写",
    "sentence-cloze": "句子挖空",
    "word-spelling": "拼写",
    "word-spelling-spelling": "拼写",
}


def _build_mode_breakdown_for_hour(db: Session, user_id: UUID, day: date, hour: int) -> list[dict]:
    """Per-mode count for a specific hour."""
    rows = db.execute(
        select(LearningEvent.review_mode, func.count(LearningEvent.id))
        .where(
            LearningEvent.user_id == user_id,
            LearningEvent.event_date == day,
            LearningEvent.event_hour == hour,
            LearningEvent.review_mode.isnot(None),
        )
        .group_by(LearningEvent.review_mode)
        .order_by(func.count(LearningEvent.id).desc())
    ).all()
    return [{"mode": m, "label": MODE_LABELS.get(m or "", m or ""), "count": int(c)} for m, c in rows]


def _aggregate_modes_by_minute(db: Session, user_id: UUID, day: date, hour: int) -> list[dict]:
    """Per-minute per-mode breakdown: which review_mode types occurred in each minute."""
    rows = db.execute(
        select(
            LearningEvent.event_minute,
            LearningEvent.review_mode,
            func.count(LearningEvent.id),
        )
        .where(
            LearningEvent.user_id == user_id,
            LearningEvent.event_date == day,
            LearningEvent.event_hour == hour,
            LearningEvent.review_mode.isnot(None),
        )
        .group_by(LearningEvent.event_minute, LearningEvent.review_mode)
        .order_by(LearningEvent.event_minute.asc())
    ).all()
    per_minute: dict[int, dict[str, int]] = {}
    for minute, mode, count in rows:
        per_minute.setdefault(minute, {})[mode] = int(count)
    return [
        {"minute": minute, "modes": modes}
        for minute, modes in sorted(per_minute.items())
    ]


def _aggregate_modes_by_day(db: Session, user_id: UUID, day: date) -> list[dict]:
    """Per-mode count for the whole day."""
    rows = db.execute(
        select(LearningEvent.review_mode, func.count(LearningEvent.id))
        .where(
            LearningEvent.user_id == user_id,
            LearningEvent.event_date == day,
            LearningEvent.review_mode.isnot(None),
        )
        .group_by(LearningEvent.review_mode)
        .order_by(func.count(LearningEvent.id).desc())
    ).all()
    return [{"mode": m, "label": MODE_LABELS.get(m or "", m or ""), "count": int(c)} for m, c in rows]


def build_hour_detail(db: Session, user_id: UUID, day: date, hour: int) -> dict:
    """One minute breakdown for a given hour."""
    rows = db.execute(
        select(LearningMinuteStat)
        .where(
            LearningMinuteStat.user_id == user_id,
            LearningMinuteStat.stat_date == day,
            LearningMinuteStat.stat_hour == hour,
        )
        .order_by(LearningMinuteStat.stat_minute.asc())
    ).scalars().all()
    return {
        "date": day.isoformat(),
        "hour": hour,
        "minutes": [
            {
                "minute": r.stat_minute,
                "spelling": r.spelling_events,
                "english_to_chinese": r.english_to_chinese_events,
                "chinese_to_english": r.chinese_to_english_events,
                "phrase": r.phrase_events,
                "sentence": r.sentence_events,
                "total": r.total_events,
                "correct": r.correct_events,
                "incorrect": r.incorrect_events,
                "accuracy": round((r.correct_events / r.total_events) * 100, 1) if r.total_events else 0.0,
                "study_seconds": round(r.study_duration_ms / 1000, 1),
            }
            for r in rows
        ],
        "minute_modes": _aggregate_modes_by_minute(db, user_id, day, hour),
    }


def build_minute_events(db: Session, user_id: UUID, day: date, hour: int, minute: int) -> list[dict]:
    """All learning events for a specific minute."""
    rows = db.execute(
        select(LearningEvent)
        .where(
            LearningEvent.user_id == user_id,
            LearningEvent.event_date == day,
            LearningEvent.event_hour == hour,
            LearningEvent.event_minute == minute,
        )
        .order_by(LearningEvent.occurred_at.asc())
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "english_text": r.english_text,
            "chinese_text": r.chinese_text,
            "response_text": r.response_text,
            "is_correct": r.is_correct,
            "score": r.score,
            "review_mode": r.review_mode,
            "duration_ms": r.duration_ms,
            "error_type": r.error_type,
        }
        for r in rows
    ]


def backfill_events_from_review_logs(db: Session, user_id: UUID) -> int:
    """Convert missing review logs into replay events and repair zero-duration stats."""
    existing_events = {
        event.review_log_id: event
        for event in db.scalars(
            select(LearningEvent).where(
                LearningEvent.user_id == user_id,
                LearningEvent.review_log_id.isnot(None),
            )
        ).all()
        if event.review_log_id is not None
    }
    logs = db.execute(
        select(ReviewLog, LearningItem)
        .outerjoin(LearningItem, LearningItem.id == ReviewLog.learning_item_id)
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at.isnot(None))
        .order_by(ReviewLog.reviewed_at.asc())
    ).all()
    count = 0
    prev_time = None
    for log, item in logs:
        if log.score is None:
            continue
        # Use real duration if logged, else estimate from gap to previous event
        if log.duration_seconds and log.duration_seconds > 0:
            expected_duration_ms = int(log.duration_seconds * 1000)
        elif prev_time and log.reviewed_at:
            gap_seconds = (log.reviewed_at - prev_time).total_seconds()
            if 0 < gap_seconds < 300:
                expected_duration_ms = int(min(gap_seconds, 60) * 1000)
            else:
                expected_duration_ms = 20000
        else:
            expected_duration_ms = 20000
        existing_event = existing_events.get(log.id)
        if existing_event is not None:
            delta_ms = max(expected_duration_ms - (existing_event.duration_ms or 0), 0)
            if delta_ms > 0:
                existing_event.duration_ms = expected_duration_ms
            if _repair_existing_event_minute_stat(db, existing_event, delta_ms):
                count += 1
            continue
        record_learning_event(db, user_id, log, item, duration_ms=expected_duration_ms)
        count += 1
        if log.reviewed_at:
            prev_time = log.reviewed_at
    db.commit()
    return count


def _repair_existing_event_minute_stat(db: Session, event: LearningEvent, delta_ms: int) -> bool:
    stat = db.scalar(
        select(LearningMinuteStat).where(
            LearningMinuteStat.user_id == event.user_id,
            LearningMinuteStat.stat_date == event.event_date,
            LearningMinuteStat.stat_hour == event.event_hour,
            LearningMinuteStat.stat_minute == event.event_minute,
        )
    )
    if stat is None:
        _increment_minute_stat(db, event)
        return True
    if delta_ms <= 0:
        return False
    stat.study_duration_ms = (stat.study_duration_ms or 0) + delta_ms
    stat.updated_at = datetime.now(UTC)
    return True
