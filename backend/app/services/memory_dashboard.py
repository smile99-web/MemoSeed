from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.models.user_model_settings import UserModelSettings
from app.schemas.memory import MemoryDashboardResponse, ReviewBucket, StudyTimeSummary, WordMasterySummary
from app.services.fsrs_fitting import MIN_FSRS_TRAINING_REVIEWS
from app.services.memory_scheduler import FSRS_WEIGHTS_SETTING_KEY, calculate_current_forget_risk
from app.utils import average, extract_mistake_words, normalize_word, parse_datetime_setting, tokenize_words


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
    consecutive_correct_count: int = 0
    consecutive_error_count: int = 0
    last_reviewed_at: datetime | None = None


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


def build_memory_dashboard(db: Session, user_id: UUID) -> MemoryDashboardResponse:
    now = datetime.now(UTC)
    item_rows = db.execute(
        select(LearningItem, MemoryState).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(LearningItem.user_id == user_id)
    ).all()

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

    item_word_map = {learning_item.id: set(tokenize_words(learning_item.english_text)) for learning_item, _ in item_rows}
    direct_word_item_ids = {
        learning_item.id
        for learning_item, _ in item_rows
        if learning_item.item_type == "word" and learning_item.source == WORD_MEMORY_SOURCE
    }
    review_rows = db.execute(
        select(ReviewLog.learning_item_id, ReviewLog.review_mode, ReviewLog.is_correct, ReviewLog.reviewed_at)
        .where(ReviewLog.user_id == user_id, ReviewLog.learning_item_id.in_(direct_word_item_ids))
        .order_by(ReviewLog.reviewed_at.asc())
    ).all()
    for learning_item_id, review_mode, is_correct, reviewed_at in review_rows:
        for word in item_word_map.get(learning_item_id, set()):
            stats = get_word_stats(word_stats, word)
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

    mistake_rows = db.execute(
        select(MistakeLog.learning_item_id, MistakeLog.mistake_type, MistakeLog.expected_answer, MistakeLog.actual_answer).where(
            MistakeLog.user_id == user_id,
            MistakeLog.is_resolved.is_(False),
        )
    ).all()
    for learning_item_id, mistake_type, expected_answer, actual_answer in mistake_rows:
        mistake_words = extract_mistake_words(mistake_type, expected_answer, actual_answer)
        if len(mistake_words) == 0 and mistake_type == "word-spelling":
            mistake_words = list(item_word_map.get(learning_item_id, set()))
        for word in mistake_words:
            get_word_stats(word_stats, word).mistake_count += 1

    summaries = [summarize_word(stats, now) for stats in word_stats.values()]
    mastered_words = len([summary for summary in summaries if summary.status == "mastered"])
    weak_words = len([summary for summary in summaries if summary.status == "weak"])
    learning_words = max(len(summaries) - mastered_words - weak_words, 0)

    memory_states = [memory_state for _, memory_state in item_rows if memory_state is not None]
    current_forget_risks = [calculate_current_forget_risk(state, now) for state in memory_states]
    current_memory_strengths = [round(1 - risk, 2) for risk in current_forget_risks]

    total_reviews = db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id)) or 0
    correct_reviews = db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id, ReviewLog.is_correct.is_(True))) or 0
    total_mistakes = db.scalar(select(func.count(MistakeLog.id)).where(MistakeLog.user_id == user_id)) or 0
    unresolved_mistakes = db.scalar(select(func.count(MistakeLog.id)).where(MistakeLog.user_id == user_id, MistakeLog.is_resolved.is_(False))) or 0

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
        due_now_count=len([state for state in memory_states if state.next_review_at <= now]),
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
    if strength >= 0.75 and stats.recall_correct_count >= 2 and stats.mistake_count == 0:
        status = "mastered"
    elif stats.mistake_count > 0 or priority_score >= 0.65:
        status = "weak"
    else:
        status = "learning"

    return WordMasterySummary(
        word=stats.word,
        status=status,
        memory_strength=round(strength, 2),
        forget_risk=round(risk, 2),
        priority_score=priority_score,
        review_count=stats.review_count,
        mistake_count=stats.mistake_count,
        recall_correct_count=stats.recall_correct_count,
        hinted_correct_count=stats.hinted_correct_count,
        preview_correct_count=stats.preview_correct_count,
        interval_days=round(interval, 1),
        next_review_at=next_review_at,
    )


def calculate_word_priority(stats: WordStats, strength: float, risk: float, next_review_at: datetime | None, now: datetime) -> float:
    overdue_score = 0.0
    if next_review_at is not None:
        overdue_hours = max((now - next_review_at).total_seconds() / 3600, 0.0)
        overdue_score = min(overdue_hours / 24, 1.0)
    mistake_score = min(stats.mistake_count / 5, 1.0)
    consecutive_error_score = min(stats.consecutive_error_count / 3, 1.0)
    low_strength_score = 1 - strength
    hint_dependency_score = 1.0 if stats.preview_correct_count > 0 and stats.recall_correct_count == 0 else 0.0
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
        risk * 0.34
        + overdue_score * 0.2
        + mistake_score * 0.2
        + consecutive_error_score * 0.14
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
