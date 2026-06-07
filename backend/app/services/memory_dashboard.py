from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.ai_daily_report import AiDailyReport
from app.models.daily_plan import DailyPlan
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.models.user_model_settings import UserModelSettings
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.schemas.memory import MemoryDashboardResponse, ReviewBucket, StudyTimeSummary, WordMasterySummary
from app.services.fsrs_fitting import MIN_FSRS_TRAINING_REVIEWS
from app.services.memory_scheduler import FSRS_WEIGHTS_SETTING_KEY, calculate_current_forget_risk
from app.utils import average, extract_mistake_words, parse_datetime_setting, tokenize_words


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
WORD_MEMORY_SOURCE = "word-memory"


@dataclass
class WordStats:
    word: str
    strengths: list[float]
    risks: list[float]
    intervals: list[int]
    next_reviews: list[datetime]
    direct_strengths: list[float]
    direct_risks: list[float]
    direct_intervals: list[int]
    direct_next_reviews: list[datetime]
    review_count: int = 0
    mistake_count: int = 0
    recall_correct_count: int = 0
    hinted_correct_count: int = 0
    preview_correct_count: int = 0
    context_correct_count: int = 0
    hidden_recall_correct_count: int = 0
    no_hint_correct_date_count: int = 0
    consecutive_correct_count: int = 0
    consecutive_error_count: int = 0
    last_reviewed_at: datetime | None = None
    error_type_counts: dict[str, int] | None = None
    scheduled_task_count: int = 0
    due_task_count: int = 0
    queue_rank: int | None = None


def error_count_value(value: object) -> int:
    if isinstance(value, dict):
        value = value.get("count", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def dominant_error_type(error_type_counts: dict[str, object] | None) -> str | None:
    if not error_type_counts:
        return None
    return max(error_type_counts.items(), key=lambda item: error_count_value(item[1]))[0]


MASTERY_STATUS_LABELS = {
    "difficult": "困难词",
    "teaching": "教学中",
    "consolidating": "巩固中",
    "near_mastered": "接近掌握",
    "mastered": "已掌握",
}

ERROR_TYPE_LABELS = {
    "first-letter": "首字母错误",
    "meaning": "词义理解错误",
    "middle": "中间结构错误",
    "ending": "词尾错误",
    "sequence": "字母顺序错误",
    "missing-letter": "漏字母",
    "extra-letter": "多字母",
    "unknown": "完全不会",
    "spelling": "拼写错误",
}


def get_word_stats(word_stats: dict[str, WordStats], word: str) -> WordStats:
    return word_stats.setdefault(
        word,
        WordStats(
            word=word,
            strengths=[],
            risks=[],
            intervals=[],
            next_reviews=[],
            direct_strengths=[],
            direct_risks=[],
            direct_intervals=[],
            direct_next_reviews=[],
        ),
    )


def build_memory_dashboard(db: Session, user_id: UUID, course_id: UUID | None = None) -> MemoryDashboardResponse:
    now = datetime.now(UTC)
    item_query = select(LearningItem, MemoryState).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(LearningItem.user_id == user_id)
    if course_id is not None:
        item_query = item_query.where(LearningItem.course_id == course_id)
    item_rows = db.execute(item_query).all()
    memory_states = [memory_state for _, memory_state in item_rows if memory_state is not None]
    memory_state_by_id = {memory_state.id: memory_state for memory_state in memory_states}

    word_stats: dict[str, WordStats] = {}
    for learning_item, memory_state in item_rows:
        if memory_state is None:
            continue
        current_forget_risk = calculate_current_forget_risk(memory_state, now)
        current_memory_strength = round(1 - current_forget_risk, 2)
        item_words = set(tokenize_words(learning_item.english_text))
        is_word_memory_item = learning_item.item_type == "word" and learning_item.source == WORD_MEMORY_SOURCE
        for word in item_words:
            stats = get_word_stats(word_stats, word)
            stats.strengths.append(current_memory_strength)
            stats.risks.append(current_forget_risk)
            stats.intervals.append(memory_state.interval_days)
            stats.next_reviews.append(memory_state.next_review_at)
            if is_word_memory_item:
                stats.direct_strengths.append(current_memory_strength)
                stats.direct_risks.append(current_forget_risk)
                stats.direct_intervals.append(memory_state.interval_days)
                stats.direct_next_reviews.append(memory_state.next_review_at)

    word_state_rows = db.scalars(select(WordMemoryState).where(WordMemoryState.user_id == user_id)).all()
    for word_state in word_state_rows:
        stats = get_word_stats(word_stats, word_state.word)
        linked_memory_state = memory_state_by_id.get(word_state.memory_state_id)
        if linked_memory_state is not None:
            current_word_risk = calculate_current_forget_risk(linked_memory_state, now)
            current_word_strength = round(1 - current_word_risk, 2)
        else:
            current_word_risk = word_state.forget_risk
            current_word_strength = word_state.memory_strength
        stats.direct_strengths.append(current_word_strength)
        stats.direct_risks.append(current_word_risk)
        if word_state.next_micro_review_at is not None:
            stats.direct_next_reviews.append(word_state.next_micro_review_at)
            stats.next_reviews.append(word_state.next_micro_review_at)
        stats.consecutive_correct_count = max(stats.consecutive_correct_count, word_state.consecutive_correct_count)
        stats.consecutive_error_count = max(stats.consecutive_error_count, word_state.consecutive_error_count)
        stats.recall_correct_count = max(stats.recall_correct_count, word_state.recall_correct_count)
        stats.hinted_correct_count = max(stats.hinted_correct_count, word_state.hinted_correct_count)
        stats.preview_correct_count = max(stats.preview_correct_count, word_state.preview_correct_count)
        stats.context_correct_count = max(stats.context_correct_count, word_state.context_correct_count)
        stats.hidden_recall_correct_count = max(stats.hidden_recall_correct_count, word_state.hidden_recall_correct_count)
        stats.no_hint_correct_date_count = max(stats.no_hint_correct_date_count, word_state.no_hint_correct_date_count)
        stats.last_reviewed_at = word_state.last_reviewed_at or stats.last_reviewed_at
        stats.error_type_counts = stats.error_type_counts or {}
        for key, value in (word_state.error_type_counts or {}).items():
            stats.error_type_counts[str(key)] = stats.error_type_counts.get(str(key), 0) + error_count_value(value)

    pending_task_counts = dict(
        db.execute(
            select(WordReviewTask.word, func.count(WordReviewTask.id))
            .where(WordReviewTask.user_id == user_id, WordReviewTask.status == "pending")
            .group_by(WordReviewTask.word)
        ).all()
    )
    for word, count in pending_task_counts.items():
        get_word_stats(word_stats, word).scheduled_task_count = int(count or 0)

    due_task_rows = db.execute(
        select(WordReviewTask.word)
        .where(
            WordReviewTask.user_id == user_id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at <= now,
        )
        .order_by(WordReviewTask.priority_score.desc(), WordReviewTask.due_at.asc())
    ).all()
    seen_due_words: set[str] = set()
    for row in due_task_rows:
        word = str(row[0] or "").strip().lower()
        if not word:
            continue
        stats = get_word_stats(word_stats, word)
        stats.due_task_count += 1
        if word not in seen_due_words:
            seen_due_words.add(word)
            stats.queue_rank = len(seen_due_words)

    item_word_map = {learning_item.id: set(tokenize_words(learning_item.english_text)) for learning_item, _ in item_rows}
    direct_word_item_ids = {
        learning_item.id
        for learning_item, _ in item_rows
        if learning_item.item_type == "word" and learning_item.source == WORD_MEMORY_SOURCE
    }
    review_rows = db.execute(
        select(ReviewLog.learning_item_id, ReviewLog.review_mode, ReviewLog.error_type, ReviewLog.is_correct, ReviewLog.reviewed_at)
        .where(ReviewLog.user_id == user_id, ReviewLog.learning_item_id.in_(direct_word_item_ids))
        .order_by(ReviewLog.reviewed_at.asc())
    ).all()
    for learning_item_id, review_mode, error_type, is_correct, reviewed_at in review_rows:
        for word in item_word_map.get(learning_item_id, set()):
            stats = get_word_stats(word_stats, word)
            stats.error_type_counts = stats.error_type_counts or {}
            if error_type:
                stats.error_type_counts[error_type] = stats.error_type_counts.get(error_type, 0) + 1
            stats.review_count += 1
            stats.last_reviewed_at = reviewed_at
            if not is_correct:
                stats.consecutive_error_count += 1
                stats.consecutive_correct_count = 0
                continue
            stats.consecutive_correct_count += 1
            stats.consecutive_error_count = 0
            if review_mode.startswith("word-recall"):
                stats.recall_correct_count += 1
            elif review_mode.startswith("word-hinted"):
                stats.hinted_correct_count += 1
            elif review_mode.startswith("word-preview"):
                stats.preview_correct_count += 1
            elif review_mode.startswith("word-context"):
                stats.context_correct_count += 1

    mistake_query = select(
        MistakeLog.learning_item_id, MistakeLog.mistake_type, MistakeLog.error_type, MistakeLog.expected_answer, MistakeLog.actual_answer
    ).where(
        MistakeLog.user_id == user_id,
        MistakeLog.is_resolved.is_(False),
    )
    if course_id is not None:
        course_li_ids = list(db.scalars(select(LearningItem.id).where(LearningItem.user_id == user_id, LearningItem.course_id == course_id)).all())
        mistake_query = mistake_query.where(MistakeLog.learning_item_id.in_(course_li_ids))
    mistake_rows = db.execute(mistake_query).all()
    for learning_item_id, mistake_type, error_type, expected_answer, actual_answer in mistake_rows:
        mistake_words = extract_mistake_words(mistake_type, expected_answer, actual_answer)
        if len(mistake_words) == 0 and mistake_type == "word-spelling":
            mistake_words = list(item_word_map.get(learning_item_id, set()))
        for word in mistake_words:
            stats = get_word_stats(word_stats, word)
            stats.mistake_count += 1
            stats.error_type_counts = stats.error_type_counts or {}
            if error_type:
                stats.error_type_counts[error_type] = stats.error_type_counts.get(error_type, 0) + 2

    summaries = [summarize_word(stats, now) for stats in word_stats.values()]
    mastered_words = len([summary for summary in summaries if summary.status == "mastered"])
    weak_words = len([summary for summary in summaries if summary.status in {"difficult", "teaching"}])
    learning_words = max(len(summaries) - mastered_words - weak_words, 0)

    current_forget_risks = [calculate_current_forget_risk(state, now) for state in memory_states]
    current_memory_strengths = [round(1 - risk, 2) for risk in current_forget_risks]

    review_base = select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id)
    mistake_base = select(func.count(MistakeLog.id)).where(MistakeLog.user_id == user_id)
    if course_id is not None:
        course_item_ids = list(db.scalars(select(LearningItem.id).where(LearningItem.user_id == user_id, LearningItem.course_id == course_id)).all())
        review_base = review_base.where(ReviewLog.learning_item_id.in_(course_item_ids))
        mistake_base = mistake_base.where(MistakeLog.learning_item_id.in_(course_item_ids))
    total_reviews = db.scalar(review_base) or 0
    correct_reviews = db.scalar(review_base.where(ReviewLog.is_correct.is_(True))) or 0
    total_mistakes = db.scalar(mistake_base) or 0
    unresolved_mistakes = db.scalar(mistake_base.where(MistakeLog.is_resolved.is_(False))) or 0

    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    except ProgrammingError:
        stored_settings = None
    fsrs_settings = stored_settings.settings if stored_settings is not None else {}
    fsrs_fitted_at = parse_datetime_setting(fsrs_settings.get("fsrsFittedAt"))
    next_review_at = min((state.next_review_at for state in memory_states), default=None)
    study_time = build_study_time_summary(db, user_id)

    return MemoryDashboardResponse(
        total_items=len(item_rows),
        total_words=len(summaries),
        mastered_words=mastered_words,
        learning_words=learning_words,
        weak_words=weak_words,
        due_now_count=len([s for s in summaries if s.next_review_at is not None and s.next_review_at <= now]),
        overdue_count=len([state for state in memory_states if state.next_review_at < now - timedelta(hours=1)]),
        average_memory_strength=round(average(current_memory_strengths), 2),
        average_forget_risk=round(average(current_forget_risks), 2),
        average_interval_days=round(average([state.interval_days for state in memory_states]), 1),
        total_reviews=total_reviews,
        correct_reviews=correct_reviews,
        accuracy_rate=round(correct_reviews / total_reviews, 2) if total_reviews else 0.0,
        total_mistakes=total_mistakes,
        unresolved_mistakes=unresolved_mistakes,
        fsrs_parameters_source="user_fitted" if isinstance(fsrs_settings.get(FSRS_WEIGHTS_SETTING_KEY), list) else "built_in",
        fsrs_min_training_reviews=MIN_FSRS_TRAINING_REVIEWS,
        fsrs_training_review_count=int(fsrs_settings.get("fsrsTrainingReviewCount") or 0),
        fsrs_training_pair_count=int(fsrs_settings.get("fsrsTrainingPairCount") or 0),
        fsrs_fitted_at=fsrs_fitted_at,
        next_review_at=next_review_at,
        study_time=study_time,
        review_buckets=build_review_buckets(memory_states, now),
        weakest_words=sorted(summaries, key=lambda summary: (-summary.priority_score, summary.memory_strength, -summary.forget_risk)),
        strongest_words=sorted(summaries, key=lambda summary: (-summary.memory_strength, summary.mistake_count, summary.forget_risk)),
    )


def build_study_time_summary(db: Session, user_id: UUID) -> StudyTimeSummary:
    now_local = datetime.now(LOCAL_TIMEZONE)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    year_start = today_start.replace(month=1, day=1)

    def sum_since(start: datetime | None = None) -> int:
        statement = select(func.coalesce(func.sum(StudyTimeLog.duration_seconds), 0)).where(StudyTimeLog.user_id == user_id)
        if start is not None:
            statement = statement.where(StudyTimeLog.recorded_at >= start.astimezone(UTC))
        return int(db.scalar(statement) or 0)

    return StudyTimeSummary(
        today_seconds=sum_since(today_start),
        week_seconds=sum_since(week_start),
        month_seconds=sum_since(month_start),
        year_seconds=sum_since(year_start),
        total_seconds=sum_since(),
    )


def summarize_word(stats: WordStats, now: datetime) -> WordMasterySummary:
    strengths = stats.direct_strengths or stats.strengths
    risks = stats.direct_risks or stats.risks
    intervals = stats.direct_intervals or stats.intervals
    next_reviews = stats.direct_next_reviews or stats.next_reviews
    strength = min(strengths) if strengths else 0.0
    risk = max(risks) if risks else 1.0
    interval = average(intervals)
    next_review_at = min(next_reviews) if next_reviews else None
    priority_score = calculate_word_priority(stats, strength, risk, next_review_at, now)
    dominant_error_type = get_dominant_error_type(stats)
    # Mastered: strong memory + enough correct reviews + proven retention over time
    if strength >= 0.80 and stats.recall_correct_count >= 3 and stats.no_hint_correct_date_count >= 3 and stats.consecutive_correct_count >= 5:
        status = "mastered"
    # Near-mastered: good memory + some correct reviews + no recent errors
    elif strength >= 0.68 and stats.recall_correct_count >= 2 and stats.no_hint_correct_date_count >= 2 and stats.consecutive_error_count == 0:
        status = "near_mastered"
    elif stats.consecutive_error_count >= 3 or priority_score >= 0.78:
        status = "difficult"
    elif stats.mistake_count > 0 or (stats.preview_correct_count > 0 and stats.recall_correct_count == 0):
        status = "teaching"
    else:
        status = "consolidating"

    return WordMasterySummary(
        word=stats.word,
        status=status,
        status_label=MASTERY_STATUS_LABELS[status],
        memory_strength=round(strength, 2),
        forget_risk=round(risk, 2),
        priority_score=priority_score,
        review_count=stats.review_count,
        mistake_count=stats.mistake_count,
        consecutive_correct_count=stats.consecutive_correct_count,
        consecutive_error_count=stats.consecutive_error_count,
        recall_correct_count=stats.recall_correct_count,
        hinted_correct_count=stats.hinted_correct_count,
        preview_correct_count=stats.preview_correct_count,
        context_correct_count=stats.context_correct_count,
        hidden_recall_correct_count=stats.hidden_recall_correct_count,
        no_hint_correct_date_count=stats.no_hint_correct_date_count,
        dominant_error_type=dominant_error_type,
        review_reason=build_review_reason(stats, risk, next_review_at, now, dominant_error_type),
        review_status_note=build_review_status_note(stats, next_review_at, now),
        recommended_task=build_recommended_task(stats, dominant_error_type),
        scheduled_task_count=stats.scheduled_task_count,
        interval_days=round(interval, 1),
        next_review_at=next_review_at,
    )


def get_dominant_error_type(stats: WordStats) -> str | None:
    if not stats.error_type_counts:
        return None
    return max(stats.error_type_counts.items(), key=lambda item: item[1])[0]


def build_review_reason(stats: WordStats, risk: float, next_review_at: datetime | None, now: datetime, error_type: str | None) -> str:
    reasons: list[str] = []
    if stats.consecutive_error_count >= 2:
        reasons.append(f"连续错 {stats.consecutive_error_count} 次")
    elif stats.mistake_count > 0:
        reasons.append(f"还有 {stats.mistake_count} 次未解决错词记录")
    if error_type:
        reasons.append(ERROR_TYPE_LABELS.get(error_type, "拼写错误") + "较多")
    if next_review_at is not None and next_review_at <= now:
        overdue_hours = max((now - next_review_at).total_seconds() / 3600, 0.0)
        if overdue_hours >= 1:
            reasons.append(f"已超期 {round(overdue_hours, 1)} 小时")
        else:
            reasons.append("已经到复习时间")
    elif risk >= 0.75:
        reasons.append(f"遗忘风险 {round(risk * 100)}%")
    if stats.preview_correct_count > 0 and stats.recall_correct_count == 0:
        reasons.append("看答案后拼对，还没有无提示拼对")
    if stats.no_hint_correct_date_count > 0 and stats.no_hint_correct_date_count < 3:
        reasons.append(f"已有 {stats.no_hint_correct_date_count}/3 个不同日期无提示拼对")
    if stats.scheduled_task_count > 0:
        reasons.append(f"已安排 {stats.scheduled_task_count} 个专项任务")
    if not reasons:
        if stats.consecutive_correct_count >= 3:
            return "连续无提示拼对，当前比较稳定。"
        return "需要继续巩固，等待更多无提示拼写记录。"
    return "，".join(reasons) + "。"


def build_review_status_note(stats: WordStats, next_review_at: datetime | None, now: datetime) -> str:
    if stats.queue_rank is not None:
        due_count_text = f"，同词还有 {stats.due_task_count} 个到期任务" if stats.due_task_count > 1 else ""
        return f"已到期，当前复习队列第 {stats.queue_rank} 位{due_count_text}。"

    if stats.last_reviewed_at is not None:
        local_review_date = stats.last_reviewed_at.astimezone(LOCAL_TIMEZONE).date()
        local_today = now.astimezone(LOCAL_TIMEZONE).date()
        if local_review_date == local_today and next_review_at is not None and next_review_at > now:
            local_next = next_review_at.astimezone(LOCAL_TIMEZONE).strftime("%m/%d %H:%M")
            return f"今天已复习，下一次安排在 {local_next}。"

    if next_review_at is not None:
        if next_review_at <= now:
            return "已到期，但暂未生成专项任务；开始学习时会重新检查并插入。"
        local_next = next_review_at.astimezone(LOCAL_TIMEZONE).strftime("%m/%d %H:%M")
        return f"暂不需要复习，下一次安排在 {local_next}。"

    if stats.scheduled_task_count > 0:
        return f"已安排 {stats.scheduled_task_count} 个专项任务，等待到期。"
    return "还没有形成明确的复习时间，需要更多答题记录。"


def build_recommended_task(stats: WordStats, error_type: str | None) -> str:
    if error_type == "first-letter":
        return "先确认词义，再听首音拼首字母"
    if error_type == "meaning":
        return "英文选中文，再看中文拼英文"
    if error_type in {"middle", "sequence"}:
        return "按音节分段，再做缺字母题"
    if error_type in {"ending", "missing-letter", "extra-letter"}:
        return "重点练词尾和字母数量"
    if error_type == "unknown" or stats.consecutive_error_count >= 3:
        return "看 5 秒后隐藏，再凭记忆重拼"
    if stats.recall_correct_count == 0 and stats.hinted_correct_count + stats.preview_correct_count > 0:
        return "无提示看中文拼英文"
    if stats.context_correct_count > stats.recall_correct_count:
        return "脱离句子单独拼写"
    return "放到短句里填空复习"


def calculate_word_priority(stats: WordStats, strength: float, risk: float, next_review_at: datetime | None, now: datetime) -> float:
    overdue_score = 0.0
    if next_review_at is not None:
        overdue_hours = max((now - next_review_at).total_seconds() / 3600, 0.0)
        overdue_score = min(overdue_hours / 24, 1.0)
    mistake_score = min(stats.mistake_count / 5, 1.0)
    consecutive_error_score = min(stats.consecutive_error_count / 3, 1.0)
    low_strength_score = 1 - strength
    hint_dependency_score = 1.0 if stats.preview_correct_count > 0 and stats.recall_correct_count == 0 else 0.0

    # Error-type boost: words with meaning errors get higher priority since
    # they represent the most common failure mode for young learners.
    error_type_counts = getattr(stats, "error_type_counts", None) or {}
    meaning_errors = error_count_value(error_type_counts.get("meaning"))
    meaning_error_score = min(meaning_errors / 3, 1.0) * 0.15
    spelling_errors = sum(
        error_count_value(error_type_counts.get(k))
        for k in ("spelling", "first-letter", "middle", "ending", "missing-letter", "extra-letter", "sequence")
    )
    spelling_error_score = min(spelling_errors / 5, 1.0) * 0.10

    recent_practice_penalty = 0.0
    if stats.last_reviewed_at is not None:
        minutes_since_review = max((now - stats.last_reviewed_at).total_seconds() / 60, 0.0)
        if minutes_since_review < 30:
            recent_practice_penalty = 0.18
        elif minutes_since_review < 120:
            recent_practice_penalty = 0.1
        elif minutes_since_review < 24 * 60:
            recent_practice_penalty = 0.04
    if stats.consecutive_error_count > 0:
        recent_practice_penalty *= 0.35
    priority = (
        risk * 0.30
        + overdue_score * 0.18
        + mistake_score * 0.15
        + consecutive_error_score * 0.10
        + meaning_error_score
        + spelling_error_score
        + low_strength_score * 0.06
        + hint_dependency_score * 0.06
        - recent_practice_penalty
    )
    return round(min(max(priority, 0.0), 1.0), 2)


def build_review_buckets(memory_states: list[MemoryState], now: datetime) -> list[ReviewBucket]:
    buckets = [
        ("已到期", lambda state: state.next_review_at <= now),
        ("10分钟内", lambda state: now < state.next_review_at <= now + timedelta(minutes=10)),
        ("30分钟内", lambda state: now + timedelta(minutes=10) < state.next_review_at <= now + timedelta(minutes=30)),
        ("2小时内", lambda state: now + timedelta(minutes=30) < state.next_review_at <= now + timedelta(hours=2)),
        ("今日巩固", lambda state: now + timedelta(hours=2) < state.next_review_at <= now + timedelta(days=1)),
        ("明日复习", lambda state: now + timedelta(days=1) < state.next_review_at <= now + timedelta(days=2)),
        ("3天内", lambda state: now + timedelta(days=2) < state.next_review_at <= now + timedelta(days=3)),
        ("7天内", lambda state: now + timedelta(days=3) < state.next_review_at <= now + timedelta(days=7)),
        ("14天内", lambda state: now + timedelta(days=7) < state.next_review_at <= now + timedelta(days=14)),
        ("30天内", lambda state: now + timedelta(days=14) < state.next_review_at <= now + timedelta(days=30)),
        ("长期保持", lambda state: state.next_review_at > now + timedelta(days=30)),
    ]
    return [ReviewBucket(label=label, count=len([state for state in memory_states if predicate(state)])) for label, predicate in buckets]


def compute_study_streak(db: Session, user_id: UUID) -> dict[str, object]:
    now_local = datetime.now(LOCAL_TIMEZONE)
    today = now_local.date()

    all_logs = db.execute(
        select(StudyTimeLog.recorded_at, StudyTimeLog.duration_seconds)
        .where(StudyTimeLog.user_id == user_id)
        .order_by(StudyTimeLog.recorded_at.desc())
    ).all()

    study_dates: dict[date, int] = {}
    for recorded_at, duration_seconds in all_logs:
        if recorded_at is None:
            continue
        local_date = recorded_at.astimezone(LOCAL_TIMEZONE).date()
        study_dates[local_date] = study_dates.get(local_date, 0) + int(duration_seconds or 0)

    active_dates = {d for d, secs in study_dates.items() if secs >= 300}

    current_streak = 0
    check_date = today
    while check_date in active_dates:
        current_streak += 1
        check_date = check_date - timedelta(days=1)

    longest_streak = 0
    sorted_dates = sorted(active_dates)
    current_run = 0
    prev_date: date | None = None
    for d in sorted_dates:
        if prev_date is not None and (d - prev_date).days == 1:
            current_run += 1
        else:
            current_run = 1
        longest_streak = max(longest_streak, current_run)
        prev_date = d

    streak_start = today - timedelta(days=current_streak - 1) if current_streak > 0 else None

    return {
        "current_streak_days": current_streak,
        "longest_streak_days": longest_streak,
        "streak_start_date": streak_start,
        "total_study_days": len(active_dates),
        "today_studied": today in active_dates,
    }


def build_daily_report(db: Session, user_id: UUID, report_date: date | None = None) -> dict[str, object]:
    now_local = datetime.now(LOCAL_TIMEZONE)
    target_date = report_date or now_local.date()
    day_start_utc = datetime(target_date.year, target_date.month, target_date.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    day_end_utc = day_start_utc + timedelta(days=1)

    today_reviews = db.execute(
        select(ReviewLog)
        .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= day_start_utc, ReviewLog.reviewed_at < day_end_utc)
        .order_by(ReviewLog.reviewed_at.asc())
    ).scalars().all()

    review_count = len(today_reviews)
    correct_count = sum(1 for r in today_reviews if r.is_correct)
    accuracy_rate = round(correct_count / review_count, 2) if review_count else 0.0
    total_duration_seconds = sum(r.duration_seconds for r in today_reviews)
    study_minutes = total_duration_seconds // 60
    words_practiced = len({r.learning_item_id for r in today_reviews})

    today_mistakes = db.execute(
        select(func.count(MistakeLog.id))
        .where(MistakeLog.user_id == user_id, MistakeLog.occurred_at >= day_start_utc, MistakeLog.occurred_at < day_end_utc)
    ).scalar() or 0

    streak_data = compute_study_streak(db, user_id)

    word_states = db.scalars(select(WordMemoryState).where(WordMemoryState.user_id == user_id)).all()
    struggling = sorted(
        [ws for ws in word_states if ws.priority_score >= 0.3],
        key=lambda ws: (-ws.priority_score, -ws.consecutive_error_count),
    )[:3]
    struggling_words = [
        {
            "word": ws.word,
            "priority_score": ws.priority_score,
            "memory_strength": ws.memory_strength,
            "mistake_count": ws.consecutive_error_count,
            "error_type": dominant_error_type(ws.error_type_counts),
            "recommendation": ERROR_TYPE_LABELS.get(
                dominant_error_type(ws.error_type_counts) or "",
                "需要多练习",
            ) if ws.error_type_counts else "需要多练习",
        }
        for ws in struggling
    ]

    spelling_errors = db.scalar(
        select(func.count(MistakeLog.id))
        .where(
            MistakeLog.user_id == user_id,
            MistakeLog.occurred_at >= day_start_utc,
            MistakeLog.occurred_at < day_end_utc,
            MistakeLog.error_type.in_(["spelling", "missing-letter", "extra-letter", "sequence", "first-letter", "middle", "ending"]),
        )
    ) or 0
    spelling_error_rate = round(spelling_errors / review_count, 2) if review_count else 0.0

    sentence_errors = db.scalar(
        select(func.count(MistakeLog.id))
        .where(
            MistakeLog.user_id == user_id,
            MistakeLog.occurred_at >= day_start_utc,
            MistakeLog.occurred_at < day_end_utc,
            MistakeLog.error_type.in_(["meaning", "unknown"]),
        )
    ) or 0
    sentence_error_rate = round(sentence_errors / review_count, 2) if review_count else 0.0

    now = datetime.now(UTC)
    memory_states = db.scalars(
        select(MemoryState).join(LearningItem, MemoryState.learning_item_id == LearningItem.id).where(
            LearningItem.user_id == user_id,
            MemoryState.next_review_at <= now,
        )
    ).all()
    review_backlog = len(memory_states)
    high_forget_risk = len([s for s in memory_states if s.forget_risk >= 0.7])

    summary = _generate_daily_summary(
        review_count, correct_count, accuracy_rate, study_minutes,
        words_practiced, today_mistakes, struggling_words,
        streak_data["current_streak_days"], db, user_id,
    )

    next_day_strategy: dict[str, object] = {}
    if struggling_words:
        next_day_strategy["focus_words"] = [sw["word"] for sw in struggling_words]
        next_day_strategy["suggestion"] = f"重点复习：{'、'.join(next_day_strategy['focus_words'])}"

    return {
        "report_date": target_date,
        "review_count": review_count,
        "correct_count": correct_count,
        "accuracy_rate": accuracy_rate,
        "study_duration_minutes": study_minutes,
        "words_practiced": words_practiced,
        "mistake_count": today_mistakes,
        "streak_days": streak_data["current_streak_days"],
        "struggling_words": struggling_words,
        "summary": summary,
        "next_day_strategy": next_day_strategy,
        "_raw": {
            "spelling_error_rate": spelling_error_rate,
            "sentence_error_rate": sentence_error_rate,
            "review_backlog_count": review_backlog,
            "high_forget_risk_count": high_forget_risk,
        },
    }


def _generate_daily_summary(
    review_count: int,
    correct_count: int,
    accuracy_rate: float,
    study_minutes: int,
    words_practiced: int,
    mistake_count: int,
    struggling_words: list[dict[str, object]],
    streak_days: int,
    db: Session,
    user_id: UUID,
) -> str:
    try:
        llm_summary = _generate_llm_summary(
            review_count, correct_count, accuracy_rate, study_minutes,
            words_practiced, mistake_count, struggling_words, streak_days,
            db, user_id,
        )
        if llm_summary:
            return llm_summary
    except Exception:
        pass
    return _build_fallback_summary(review_count, correct_count, accuracy_rate, study_minutes, mistake_count, struggling_words, streak_days)


def _generate_llm_summary(
    review_count: int,
    correct_count: int,
    accuracy_rate: float,
    study_minutes: int,
    words_practiced: int,
    mistake_count: int,
    struggling_words: list[dict[str, object]],
    streak_days: int,
    db: Session,
    user_id: UUID,
) -> str | None:
    from app.services.llm_translation import LlmTranslationSettings, call_llm_generate
    from app.services.secure_model_settings import get_private_model_settings

    settings = get_private_model_settings(db, user_id)
    provider = str(settings.get("llmProvider") or "ollama").strip()
    base_url = str(settings.get("llmBaseUrl") or "").strip()
    model = str(settings.get("llmModel") or "").strip()
    api_key = str(settings.get("llmApiKey") or "").strip()

    if not base_url or not model:
        return None

    llm_settings = LlmTranslationSettings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )

    struggling_detail = ""
    if struggling_words:
        word_list = "、".join(str(sw.get("word", "")) for sw in struggling_words[:3])
        struggling_detail = f"今天困难词：{word_list}。"
        for sw in struggling_words[:3]:
            error_label = str(sw.get("recommendation", ""))
            if error_label:
                struggling_detail += f"{sw.get('word', '')}容易在{error_label}上出错。"

    prompt = (
        "你是一个儿童英语学习助手的家长报告生成器。请用温暖、鼓励的中文语气，为家长写一段今日学习总结，2-4句话即可。\n\n"
        f"今天复习了{review_count}次，其中{correct_count}次正确，准确率{round(accuracy_rate * 100)}%。\n"
        f"学习了{study_minutes}分钟，练习了{words_practiced}个单词。\n"
        f"今天有{mistake_count}个新的拼写或理解错误。\n"
        f"连续打卡{streak_days}天。\n"
        f"{struggling_detail}\n\n"
        "请在总结后给出1条具体的今日学习建议（例如：'今天可以重点练习 -ight 结尾的拼写规律，这些词容易出错：light、night、right。'）。\n"
        "不要使用markdown格式，直接输出纯文本，语气亲切自然。"
    )

    try:
        response = call_llm_generate(llm_settings, prompt)
        return response.strip().strip('"').strip()
    except Exception:
        return None


def _build_fallback_summary(
    review_count: int,
    correct_count: int,
    accuracy_rate: float,
    study_minutes: int,
    mistake_count: int,
    struggling_words: list[dict[str, object]],
    streak_days: int,
) -> str:
    parts = [f"今天完成了{review_count}次复习，正确率{round(accuracy_rate * 100)}%，学习时长{study_minutes}分钟。"]
    if struggling_words:
        word_list = "、".join(str(sw.get("word", "")) for sw in struggling_words[:3])
        error_info = struggling_words[0].get("recommendation", "多练习") if struggling_words else "多练习"
        parts.append(f"困难词：{word_list}，建议重点练习{error_info}。")
    if mistake_count > 0:
        parts.append(f"今天有{mistake_count}个新错误需要注意。")
    if streak_days > 0:
        parts.append(f"已经连续学习{streak_days}天，继续保持！")
    return "".join(parts)


def build_today_plan(db: Session, user_id: UUID) -> dict[str, object]:
    now_utc = datetime.now(UTC)
    now_local = datetime.now(LOCAL_TIMEZONE)
    today = now_local.date()

    due_count = db.scalar(
        select(func.count(MemoryState.id))
        .join(LearningItem, MemoryState.learning_item_id == LearningItem.id)
        .where(LearningItem.user_id == user_id, MemoryState.next_review_at <= now_utc)
    ) or 0

    new_word_items = db.scalar(
        select(func.count(LearningItem.id))
        .outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
            (MemoryState.id.is_(None)) | (MemoryState.repetition_count == 0),
        )
    ) or 0

    unresolved_mistake_count = db.scalar(
        select(func.count(MistakeLog.id))
        .where(MistakeLog.user_id == user_id, MistakeLog.is_resolved.is_(False))
    ) or 0

    plan = db.scalar(select(DailyPlan).where(DailyPlan.user_id == user_id, DailyPlan.plan_date == today))
    if plan is None:
        plan = DailyPlan(
            user_id=user_id,
            plan_date=today,
            warmup_review_minutes=10,
            new_learning_minutes=20,
            sentence_training_minutes=20,
            mistake_reinforcement_minutes=10,
            new_word_limit=10,
            new_phrase_limit=5,
        )

    time_budget = {
        "warmup_review_minutes": plan.warmup_review_minutes,
        "new_learning_minutes": plan.new_learning_minutes,
        "sentence_training_minutes": plan.sentence_training_minutes,
        "mistake_reinforcement_minutes": plan.mistake_reinforcement_minutes,
    }

    items: list[dict[str, object]] = []
    if due_count > 0:
        items.append({
            "task_type": "due_review",
            "task_description": f"{due_count}个待复习单词或短句",
            "estimated_minutes": plan.warmup_review_minutes,
            "item_count": due_count,
        })
    if new_word_items > 0:
        new_limit = min(new_word_items, max(plan.new_word_limit, 1) if plan.new_word_limit > 0 else new_word_items)
        items.append({
            "task_type": "new_words",
            "task_description": f"{new_limit}个新单词学习",
            "estimated_minutes": plan.new_learning_minutes,
            "item_count": new_limit,
        })
    else:
        items.append({
            "task_type": "sentence_training",
            "task_description": "句子训练",
            "estimated_minutes": plan.sentence_training_minutes,
            "item_count": 0,
        })
    if unresolved_mistake_count > 0:
        items.append({
            "task_type": "mistake_reinforcement",
            "task_description": f"{unresolved_mistake_count}个错词强化练习",
            "estimated_minutes": plan.mistake_reinforcement_minutes,
            "item_count": unresolved_mistake_count,
        })

    total_minutes = sum(int(item["estimated_minutes"]) for item in items)  # type: ignore[arg-type]

    return {
        "plan_date": today,
        "total_minutes": total_minutes,
        "due_review_count": due_count,
        "new_words_ready": new_word_items,
        "unresolved_mistake_count": unresolved_mistake_count,
        "items": items,
        "time_budget": time_budget,
    }


def build_word_history(db: Session, user_id: UUID, word: str) -> dict[str, object]:
    normalized_word = word.strip().lower()
    if not normalized_word:
        raise ValueError("Word must not be empty")

    item_ids = list(db.scalars(
        select(LearningItem.id).where(LearningItem.user_id == user_id)
    ).all())

    review_logs = db.execute(
        select(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.learning_item_id.in_(item_ids),
        ).order_by(ReviewLog.reviewed_at.asc())
    ).scalars().all()

    item_texts: dict[UUID, str] = {}
    for item_id in item_ids:
        li = db.scalar(select(LearningItem).where(LearningItem.id == item_id))
        if li is not None:
            item_texts[item_id] = li.english_text.lower()

    word_item_ids = {lid for lid, text in item_texts.items() if normalized_word in text.split()}

    mistake_logs = db.execute(
        select(MistakeLog).where(
            MistakeLog.user_id == user_id,
            MistakeLog.learning_item_id.in_(list(word_item_ids)),
        ).order_by(MistakeLog.occurred_at.asc())
    ).scalars().all()

    events: list[dict[str, object]] = []

    for rl in review_logs:
        if rl.learning_item_id in word_item_ids:
            events.append({
                "timestamp": rl.reviewed_at,
                "event_type": "review",
                "score": rl.score,
                "is_correct": rl.is_correct,
                "error_type": rl.error_type,
                "memory_strength": None,
                "detail": f"评分{int(rl.score)}/{'正确' if rl.is_correct else '错误'}",
            })

    for ml in mistake_logs:
        events.append({
            "timestamp": ml.occurred_at,
            "event_type": "mistake",
            "score": None,
            "is_correct": False,
            "error_type": ml.error_type,
            "memory_strength": None,
            "detail": f"{ml.mistake_type}: {ml.actual_answer} (应为: {ml.expected_answer})",
        })

    word_state = db.scalar(select(WordMemoryState).where(WordMemoryState.user_id == user_id, WordMemoryState.word == normalized_word))
    current_strength = word_state.memory_strength if word_state is not None else 0.0
    current_risk = word_state.forget_risk if word_state is not None else 1.0

    events.sort(key=lambda e: e["timestamp"])  # type: ignore[arg-type, return-value]

    return {
        "word": normalized_word,
        "events": events,
        "current_strength": current_strength,
        "current_risk": current_risk,
        "review_count": len([e for e in events if e["event_type"] == "review"]),
        "mistake_count": len([e for e in events if e["event_type"] == "mistake"]),
    }


def build_retention_curve(db: Session, user_id: UUID, course_id: UUID | None = None) -> dict[str, object]:
    bucket_labels = [0.5, 1, 2, 3, 5, 7, 14, 30, 60, 90]
    bucket_max: dict[float, float] = {0.5: 0.5, 1: 1, 2: 2, 3: 3, 5: 5, 7: 7, 14: 14, 30: 30, 60: 60, 90: 90}

    item_query = select(LearningItem.id).where(LearningItem.user_id == user_id)
    if course_id is not None:
        item_query = item_query.where(LearningItem.course_id == course_id)
    item_ids = list(db.scalars(item_query).all())

    if not item_ids:
        return {"bins": [{"elapsed_days_label": str(bl), "elapsed_days": bl, "total_reviews": 0, "correct_reviews": 0, "recall_rate": 0.0} for bl in bucket_labels], "course_id": course_id}

    # Group reviews by learning_item_id
    reviews_by_item: dict[UUID, list[tuple[datetime, bool]]] = {}
    review_item_rows = db.execute(
        select(ReviewLog.learning_item_id, ReviewLog.reviewed_at, ReviewLog.is_correct).where(
            ReviewLog.user_id == user_id,
            ReviewLog.learning_item_id.in_(item_ids),
        ).order_by(ReviewLog.reviewed_at.asc())
    ).all()
    for li_id, r_at, is_c in review_item_rows:
        reviews_by_item.setdefault(li_id, []).append((r_at, is_c))

    bucket_counts: dict[float, int] = {}
    bucket_correct: dict[float, int] = {}

    for li_id, reviews in reviews_by_item.items():
        prev_at: datetime | None = None
        for r_at, is_c in reviews:
            if prev_at is not None:
                elapsed_days = (r_at - prev_at).total_seconds() / 86400
                bucket = 90.0
                for bl in sorted(bucket_labels):
                    if elapsed_days <= bucket_max[bl]:
                        bucket = bl
                        break
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
                if is_c:
                    bucket_correct[bucket] = bucket_correct.get(bucket, 0) + 1
            prev_at = r_at

    bins: list[dict[str, object]] = []
    for bl in bucket_labels:
        total = bucket_counts.get(bl, 0)
        correct = bucket_correct.get(bl, 0)
        bins.append({
            "elapsed_days_label": str(bl),
            "elapsed_days": bl,
            "total_reviews": total,
            "correct_reviews": correct,
            "recall_rate": round(correct / total, 2) if total else 0.0,
        })

    return {"bins": bins, "course_id": course_id}


def build_error_breakdown(db: Session, user_id: UUID) -> dict[str, object]:
    now_local = datetime.now(LOCAL_TIMEZONE)
    today = now_local.date()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)

    this_week_start_utc = datetime(week_start.year, week_start.month, week_start.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    last_week_start_utc = datetime(last_week_start.year, last_week_start.month, last_week_start.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    last_week_end_utc = datetime(last_week_end.year, last_week_end.month, last_week_end.day, 23, 59, 59, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)

    def count_errors_by_type(start_utc: datetime, end_utc: datetime) -> dict[str, int]:
        rows = db.execute(
            select(MistakeLog.error_type, func.count(MistakeLog.id))
            .where(
                MistakeLog.user_id == user_id,
                MistakeLog.occurred_at >= start_utc,
                MistakeLog.occurred_at <= end_utc,
                MistakeLog.error_type.isnot(None),
            )
            .group_by(MistakeLog.error_type)
        ).all()
        return {str(et): int(cnt) for et, cnt in rows if et}

    this_week = count_errors_by_type(this_week_start_utc, datetime.now(UTC))
    last_week = count_errors_by_type(last_week_start_utc, last_week_end_utc)

    all_types = set(this_week.keys()) | set(last_week.keys())
    items: list[dict[str, object]] = []
    for et in sorted(all_types):
        tw = this_week.get(et, 0)
        lw = last_week.get(et, 0)
        if tw > lw:
            trend = "up"
        elif tw < lw:
            trend = "down"
        else:
            trend = "stable"
        items.append({
            "error_type": et,
            "error_label": ERROR_TYPE_LABELS.get(et, et),
            "this_week_count": tw,
            "last_week_count": lw,
            "trend": trend,
        })

    return {
        "items": items,
        "total_this_week": sum(this_week.values()),
        "total_last_week": sum(last_week.values()),
    }


def generate_ai_daily_report(db: Session, user_id: UUID, report_date: date | None = None) -> dict[str, object]:
    report_data = build_daily_report(db, user_id, report_date)
    raw = report_data.pop("_raw", {})

    existing = db.scalar(
        select(AiDailyReport).where(
            AiDailyReport.user_id == user_id,
            AiDailyReport.report_date == report_data["report_date"],
        )
    )

    if existing is not None:
        existing.accuracy_rate = report_data["accuracy_rate"]
        existing.spelling_error_rate = raw.get("spelling_error_rate", 0.0)  # type: ignore[arg-type]
        existing.sentence_error_rate = raw.get("sentence_error_rate", 0.0)  # type: ignore[arg-type]
        existing.study_duration_minutes = report_data["study_duration_minutes"]
        existing.review_backlog_count = raw.get("review_backlog_count", 0)  # type: ignore[arg-type]
        existing.high_forget_risk_count = raw.get("high_forget_risk_count", 0)  # type: ignore[arg-type]
        existing.summary = report_data["summary"]
        existing.next_day_strategy = report_data["next_day_strategy"]
    else:
        db.add(
            AiDailyReport(
                user_id=user_id,
                report_date=report_data["report_date"],
                accuracy_rate=report_data["accuracy_rate"],
                spelling_error_rate=raw.get("spelling_error_rate", 0.0),  # type: ignore[arg-type]
                sentence_error_rate=raw.get("sentence_error_rate", 0.0),  # type: ignore[arg-type]
                study_duration_minutes=report_data["study_duration_minutes"],
                review_backlog_count=raw.get("review_backlog_count", 0),  # type: ignore[arg-type]
                high_forget_risk_count=raw.get("high_forget_risk_count", 0),  # type: ignore[arg-type]
                summary=report_data["summary"],
                next_day_strategy=report_data["next_day_strategy"],
            )
        )
    db.commit()

    return report_data


def check_and_generate_daily_report(db: Session, user_id: UUID) -> bool:
    now_local = datetime.now(LOCAL_TIMEZONE)
    if now_local.hour < 20:
        return False

    today = now_local.date()
    today_start_utc = datetime(today.year, today.month, today.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    existing = db.scalar(
        select(AiDailyReport).where(
            AiDailyReport.user_id == user_id,
            AiDailyReport.report_date == today,
        )
    )
    if existing is not None:
        return False

    today_has_study = db.scalar(
        select(func.count(StudyTimeLog.id)).where(StudyTimeLog.user_id == user_id, StudyTimeLog.recorded_at >= today_start_utc)
    ) or 0

    if today_has_study == 0:
        return False

    generate_ai_daily_report(db, user_id, today)
    return True


def _build_word_due_map(db: Session, user_id: UUID, now: datetime) -> tuple[dict[str, datetime], dict[str, float]]:
    """Build a word-level due map: one word in 5 sentences = 1 entry, not 5."""
    state_rows = db.execute(
        select(MemoryState, LearningItem).join(LearningItem, MemoryState.learning_item_id == LearningItem.id).where(
            LearningItem.user_id == user_id,
            MemoryState.next_review_at.isnot(None),
        )
    ).all()
    word_due: dict[str, datetime] = {}
    word_forget_risks: dict[str, float] = {}
    for state, item in state_rows:
        for word in tokenize_words(item.english_text):
            w = word.lower()
            existing = word_due.get(w)
            if existing is None or state.next_review_at < existing:
                word_due[w] = state.next_review_at
                word_forget_risks[w] = state.forget_risk or 1.0
    return word_due, word_forget_risks


def build_review_forecast(db: Session, user_id: UUID) -> dict[str, object]:
    """Predict review workload and suggest optimal study schedule."""
    now = datetime.now(UTC)
    now_local = datetime.now(LOCAL_TIMEZONE)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)
    tomorrow_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=LOCAL_TIMEZONE)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    week_end = today + timedelta(days=7)
    week_end_dt = datetime(week_end.year, week_end.month, week_end.day, tzinfo=LOCAL_TIMEZONE)

    # Use word-level deduplication
    word_due, word_forget_risks = _build_word_due_map(db, user_id, now)

    # Today's remaining: words scheduled FOR today (not the entire backlog)
    today_start = datetime(today.year, today.month, today.day, tzinfo=LOCAL_TIMEZONE).astimezone(UTC)
    today_count = len([t for t in word_due.values() if today_start <= t <= now])

    # Total backlog: all overdue unique words
    total_backlog = len([t for t in word_due.values() if t <= now])

    # Tomorrow's due words
    tomorrow_count = len([t for t in word_due.values() if tomorrow_start.astimezone(UTC) <= t < tomorrow_end.astimezone(UTC)])
    tomorrow_high_risk = len([w for w, t in word_due.items() if tomorrow_start.astimezone(UTC) <= t < tomorrow_end.astimezone(UTC) and word_forget_risks.get(w, 1.0) >= 0.7])

    # Weekly forecast
    week_due_times = [t for t in word_due.values() if now <= t < week_end_dt.astimezone(UTC)]
    week_count = len(week_due_times)

    # Peak day
    day_counts: dict[str, int] = {}
    for t in week_due_times:
        day_key = t.astimezone(LOCAL_TIMEZONE).strftime("%m-%d")
        day_counts[day_key] = day_counts.get(day_key, 0) + 1
    peak_day = max(day_counts.items(), key=lambda x: x[1]) if day_counts else ("-", 0)

    # Efficiency: compute from real study time (not review_log.duration_seconds which is usually 0)
    week_ago = now - timedelta(days=7)
    recent_study = db.scalars(
        select(StudyTimeLog.duration_seconds).where(
            StudyTimeLog.user_id == user_id,
            StudyTimeLog.recorded_at >= week_ago,
        )
    ).all()
    total_study_seconds = sum(recent_study) if recent_study else 0
    avg_daily_seconds = round(total_study_seconds / 7)
    avg_daily_minutes = round(avg_daily_seconds / 60)

    # Count reviews in the same period
    total_recent = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= week_ago,
        )
    ) or 0
    correct_recent = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= week_ago,
            ReviewLog.is_correct.is_(True),
        )
    ) or 0
    recent_accuracy = round(correct_recent / total_recent, 2) if total_recent else 0.0

    # Real seconds per item from study time / review count
    seconds_per_item = round(total_study_seconds / total_recent) if total_recent > 0 else 20
    seconds_per_item = max(seconds_per_item, 8)  # floor: at least 8s per word
    optimistic_min = max(1, round(tomorrow_count * seconds_per_item * 0.7 / 60))
    conservative_min = max(1, round(tomorrow_count * seconds_per_item * 1.2 / 60))

    # Load level
    if tomorrow_count <= 10:
        load_level = "light"
    elif tomorrow_count <= 25:
        load_level = "moderate"
    elif tomorrow_count <= 50:
        load_level = "heavy"
    else:
        load_level = "overload"

    # Smart suggestions
    actions: list[str] = []
    if total_backlog > 50:
        actions.append(f"复习积压{total_backlog}词（{today_count}词今日到期），建议每天坚持复习清理积压")
    elif load_level == "overload":
        actions.append(f"明日{week_count}词到期压力较大，建议今天提前复习一部分分散压力")
    elif load_level == "heavy":
        actions.append(f"明日{tomorrow_count}词到期，建议今天多复习15分钟减轻明天负担")
    if tomorrow_high_risk > 0:
        actions.append(f"有{tomorrow_high_risk}个词遗忘风险高，建议优先复习")
    if today_count > 0:
        actions.append(f"今日到期{today_count}词，预计还需{max(1, round(today_count * seconds_per_item * 0.7 / 60))}~{max(1, round(today_count * seconds_per_item * 1.2 / 60))}分钟")
    if avg_daily_minutes > 0 and recent_accuracy < 0.7:
        actions.append("近期正确率偏低，建议降低新词量，增加复习频率")
    # P1-1: Daily target suggestion based on due items and efficiency
    suggested_daily_minutes = max(10, round(today_count * seconds_per_item * 0.7 / 60))
    if suggested_daily_minutes > 60:
        suggested_daily_minutes = 60  # cap at 60 min for young learners
    if total_backlog > 30:
        suggested_daily_minutes = min(suggested_daily_minutes + 10, 60)

    if len(actions) == 0:
        actions.append("复习节奏良好，保持当前学习计划即可")
    actions.append(f"建议今日目标 {suggested_daily_minutes} 分钟")

    return {
        "backlog_count": total_backlog,
        "suggested_daily_minutes": suggested_daily_minutes,
        "today": {
            "remaining_count": today_count,
            "remaining_minutes_low": max(1, round(today_count * seconds_per_item * 0.7 / 60)),
            "remaining_minutes_high": max(1, round(today_count * seconds_per_item * 1.2 / 60)),
        },
        "tomorrow": {
            "due_count": tomorrow_count,
            "estimated_minutes": [optimistic_min, conservative_min],
            "high_risk_count": tomorrow_high_risk,
        },
        "week": {
            "due_count": week_count,
            "daily_average": round(week_count / 7, 1),
            "peak_day": peak_day[0],
            "peak_count": peak_day[1],
        },
        "load_level": load_level,
        "suggested_actions": actions,
        "efficiency": {
            "avg_seconds_per_item": seconds_per_item,
            "recent_accuracy": recent_accuracy,
            "avg_daily_minutes": avg_daily_minutes,
        },
    }


def build_today_progress(db: Session, user_id: UUID) -> dict[str, object]:
    """Compare today's planned vs actual study progress."""
    now = datetime.now(UTC)
    today_start = datetime.now(LOCAL_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    # Planned: unique words due (deduplicated by word, not per-item)
    word_due_map = _build_word_due_map(db, user_id, now)
    planned_reviews = len([t for t in word_due_map.values() if t <= now])

    # Completed reviews today
    completed_reviews = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= today_start,
        )
    ) or 0

    # Unique items reviewed today
    unique_items_reviewed = db.scalar(
        select(func.count(func.distinct(ReviewLog.learning_item_id))).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= today_start,
        )
    ) or 0

    # New words planned (never-reviewed items)
    planned_new = db.scalar(
        select(func.count(LearningItem.id))
        .outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
            (MemoryState.id.is_(None)) | (MemoryState.repetition_count == 0),
        )
    ) or 0

    # New words actually studied today (first-time review)
    completed_new = db.scalar(
        select(func.count(func.distinct(ReviewLog.learning_item_id))).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.learning_item_id.in_(
                select(LearningItem.id).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(
                    LearningItem.user_id == user_id,
                    LearningItem.item_type == "word",
                    (MemoryState.id.is_(None)) | (MemoryState.repetition_count == 0),
                )
            ),
        )
    ) or 0

    # Mistake practice
    planned_mistakes = db.scalar(
        select(func.count(MistakeLog.id)).where(
            MistakeLog.user_id == user_id,
            MistakeLog.is_resolved.is_(False),
        )
    ) or 0

    completed_mistakes = db.scalar(
        select(func.count(MistakeLog.id)).where(
            MistakeLog.user_id == user_id,
            MistakeLog.is_resolved.is_(True),
            MistakeLog.occurred_at >= today_start,
        )
    ) or 0

    return {
        "review": {
            "planned": planned_reviews,
            "completed_items": unique_items_reviewed,
            "completed_reviews": completed_reviews,
            "remaining": max(0, planned_reviews - unique_items_reviewed),
        },
        "new_words": {
            "planned": planned_new,
            "completed": completed_new,
            "remaining": max(0, planned_new - completed_new),
        },
        "mistakes": {
            "planned": planned_mistakes,
            "completed": completed_mistakes,
            "remaining": max(0, planned_mistakes - completed_mistakes),
        },
    }
