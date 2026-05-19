from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog

MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5


@dataclass(frozen=True)
class MemoryScheduleResult:
    memory_state: MemoryState
    review_log: ReviewLog
    mistake_log: MistakeLog | None


def calculate_ease_factor(current_ease_factor: float, score: int) -> float:
    adjusted_ease_factor = current_ease_factor + (0.1 - (5 - score) * (0.08 + (5 - score) * 0.02))
    return max(MIN_EASE_FACTOR, round(adjusted_ease_factor, 2))


def calculate_interval_days(score: int, repetition_count: int, ease_factor: float, item_type: str) -> int:
    if score < 3:
        return 0
    if repetition_count <= 1:
        base_interval = 1
    elif repetition_count == 2:
        base_interval = 6
    else:
        base_interval = round((repetition_count - 1) * ease_factor * 3)

    if item_type == "sentence":
        base_interval = max(1, round(base_interval * 0.7))
    elif item_type == "phrase":
        base_interval = max(1, round(base_interval * 0.85))

    return max(1, base_interval)


def calculate_forget_risk(score: int, interval_days: int, item_type: str) -> float:
    if score < 3:
        return 1.0

    score_risk = (5 - score) / 5
    interval_risk = min(interval_days / 30, 1.0) * 0.35
    type_risk = 0.15 if item_type == "sentence" else 0.08 if item_type == "phrase" else 0.0
    return round(min(max(score_risk + interval_risk + type_risk, 0.0), 1.0), 2)


def calculate_memory_strength(score: int, forget_risk: float) -> float:
    if score < 3:
        return round(max(score / 5 * 0.4, 0.0), 2)
    return round(min(max((score / 5) * (1 - forget_risk * 0.4), 0.0), 1.0), 2)


def get_next_review_at(now: datetime, score: int, interval_days: int) -> datetime:
    if score < 3:
        return now + timedelta(hours=4)
    return now + timedelta(days=interval_days)


def get_or_create_memory_state(db: Session, learning_item: LearningItem, now: datetime) -> MemoryState:
    memory_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == learning_item.id))
    if memory_state is not None:
        return memory_state

    memory_state = MemoryState(
        learning_item_id=learning_item.id,
        interval_days=0,
        ease_factor=DEFAULT_EASE_FACTOR,
        memory_strength=0.0,
        forget_risk=1.0,
        repetition_count=0,
        lapse_count=0,
        next_review_at=now,
    )
    db.add(memory_state)
    db.flush()
    return memory_state


def schedule_memory_review(
    db: Session,
    user_id: UUID,
    learning_item_id: UUID,
    score: int,
    review_mode: str,
    response_text: str | None,
    duration_seconds: int,
) -> MemoryScheduleResult:
    if score < 0 or score > 5:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Score must be between 0 and 5")

    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == learning_item_id, LearningItem.user_id == user_id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    now = datetime.now(UTC)
    memory_state = get_or_create_memory_state(db, learning_item, now)
    is_correct = score >= 3

    if is_correct:
        memory_state.repetition_count += 1
        memory_state.ease_factor = calculate_ease_factor(memory_state.ease_factor, score)
    else:
        memory_state.repetition_count = 0
        memory_state.lapse_count += 1
        memory_state.ease_factor = max(MIN_EASE_FACTOR, round(memory_state.ease_factor - 0.2, 2))

    memory_state.interval_days = calculate_interval_days(score, memory_state.repetition_count, memory_state.ease_factor, learning_item.item_type)
    memory_state.forget_risk = calculate_forget_risk(score, memory_state.interval_days, learning_item.item_type)
    memory_state.memory_strength = calculate_memory_strength(score, memory_state.forget_risk)
    memory_state.last_reviewed_at = now
    memory_state.next_review_at = get_next_review_at(now, score, memory_state.interval_days)

    review_log = ReviewLog(
        user_id=user_id,
        learning_item_id=learning_item.id,
        review_mode=review_mode,
        score=score,
        is_correct=is_correct,
        response_text=response_text,
        duration_seconds=duration_seconds,
    )
    db.add(review_log)

    mistake_log = None
    if not is_correct:
        mistake_log = MistakeLog(
            user_id=user_id,
            learning_item_id=learning_item.id,
            mistake_type=review_mode,
            expected_answer=learning_item.english_text,
            actual_answer=response_text or "",
            is_resolved=False,
        )
        db.add(mistake_log)

    db.add(memory_state)
    db.commit()
    db.refresh(memory_state)
    db.refresh(review_log)
    if mistake_log is not None:
        db.refresh(mistake_log)

    return MemoryScheduleResult(memory_state=memory_state, review_log=review_log, mistake_log=mistake_log)
