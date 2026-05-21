import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.schemas.memory import MemoryDashboardResponse, ReviewBucket, StudyTimeSummary, WordMasterySummary


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass
class WordStats:
    word: str
    strengths: list[float]
    risks: list[float]
    intervals: list[int]
    next_reviews: list[datetime]
    review_count: int = 0
    mistake_count: int = 0


def normalize_word(value: str) -> str:
    return re.sub(r"^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$", "", value).lower()


def tokenize_words(value: str) -> list[str]:
    return [word for word in (normalize_word(part) for part in value.split()) if word]


def build_memory_dashboard(db: Session, user_id: UUID) -> MemoryDashboardResponse:
    now = datetime.now(UTC)
    item_rows = db.execute(
        select(LearningItem, MemoryState).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(LearningItem.user_id == user_id)
    ).all()

    word_stats: dict[str, WordStats] = {}
    for learning_item, memory_state in item_rows:
        if memory_state is None:
            continue
        item_words = set(tokenize_words(learning_item.english_text))
        for word in item_words:
            stats = word_stats.setdefault(word, WordStats(word=word, strengths=[], risks=[], intervals=[], next_reviews=[]))
            stats.strengths.append(memory_state.memory_strength)
            stats.risks.append(memory_state.forget_risk)
            stats.intervals.append(memory_state.interval_days)
            stats.next_reviews.append(memory_state.next_review_at)

    review_rows = db.execute(select(ReviewLog.learning_item_id, ReviewLog.is_correct).where(ReviewLog.user_id == user_id)).all()
    item_word_map = {learning_item.id: set(tokenize_words(learning_item.english_text)) for learning_item, _ in item_rows}
    for learning_item_id, _is_correct in review_rows:
        for word in item_word_map.get(learning_item_id, set()):
            word_stats.setdefault(word, WordStats(word=word, strengths=[], risks=[], intervals=[], next_reviews=[])).review_count += 1

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
            word_stats.setdefault(word, WordStats(word=word, strengths=[], risks=[], intervals=[], next_reviews=[])).mistake_count += 1

    summaries = [summarize_word(stats) for stats in word_stats.values()]
    mastered_words = len([summary for summary in summaries if summary.status == "mastered"])
    weak_words = len([summary for summary in summaries if summary.status == "weak"])
    learning_words = max(len(summaries) - mastered_words - weak_words, 0)

    memory_states = [memory_state for _, memory_state in item_rows if memory_state is not None]
    total_reviews = db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id)) or 0
    correct_reviews = db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user_id, ReviewLog.is_correct.is_(True))) or 0
    total_mistakes = db.scalar(select(func.count(MistakeLog.id)).where(MistakeLog.user_id == user_id)) or 0
    unresolved_mistakes = db.scalar(select(func.count(MistakeLog.id)).where(MistakeLog.user_id == user_id, MistakeLog.is_resolved.is_(False))) or 0
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
        average_memory_strength=round(average([state.memory_strength for state in memory_states]), 2),
        average_forget_risk=round(average([state.forget_risk for state in memory_states]), 2),
        average_interval_days=round(average([state.interval_days for state in memory_states]), 1),
        total_reviews=total_reviews,
        correct_reviews=correct_reviews,
        accuracy_rate=round(correct_reviews / total_reviews, 2) if total_reviews else 0.0,
        total_mistakes=total_mistakes,
        unresolved_mistakes=unresolved_mistakes,
        next_review_at=next_review_at,
        study_time=study_time,
        review_buckets=build_review_buckets(memory_states, now),
        weakest_words=sorted(summaries, key=lambda summary: (summary.status != "weak", -summary.mistake_count, summary.memory_strength, -summary.forget_risk)),
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


def summarize_word(stats: WordStats) -> WordMasterySummary:
    strength = max(stats.strengths) if stats.strengths else 0.0
    risk = max(stats.risks) if stats.risks else 1.0
    interval = average(stats.intervals)
    next_review_at = min(stats.next_reviews) if stats.next_reviews else None
    if strength >= 0.75 and stats.review_count >= 3 and stats.mistake_count == 0:
        status = "mastered"
    elif stats.mistake_count > 0:
        status = "weak"
    else:
        status = "learning"

    return WordMasterySummary(
        word=stats.word,
        status=status,
        memory_strength=round(strength, 2),
        forget_risk=round(risk, 2),
        review_count=stats.review_count,
        mistake_count=stats.mistake_count,
        interval_days=round(interval, 1),
        next_review_at=next_review_at,
    )


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


def average(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def extract_mistake_words(mistake_type: str, expected_answer: str, actual_answer: str) -> list[str]:
    if mistake_type == "word-spelling":
        return tokenize_words(expected_answer)
    if "错词：" in actual_answer:
        _, words_text = actual_answer.split("错词：", 1)
        return tokenize_words(words_text.replace(",", " "))
    return []
