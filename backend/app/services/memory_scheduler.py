from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil, exp
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.user_model_settings import UserModelSettings
from app.utils import clamp

FSRS_AGAIN = 1
FSRS_HARD = 2
FSRS_GOOD = 3
FSRS_EASY = 4
FSRS_DECAY = -0.5
FSRS_FACTOR = 19 / 81
FSRS_TARGET_RETENTION = 0.9
MIN_FSRS_DIFFICULTY = 1.0
MAX_FSRS_DIFFICULTY = 10.0
DEFAULT_FSRS_DIFFICULTY = 5.0
MIN_STABILITY_DAYS = 5 / 1440
MAX_STABILITY_DAYS = 3650.0

# Default FSRS v4 weights. The app stores the resulting difficulty/stability in
# existing memory_state fields so we can upgrade scheduling without a migration.
FSRS_WEIGHTS = (
    0.40255,
    1.18385,
    3.173,
    15.69105,
    7.1949,
    0.5345,
    1.4604,
    0.0046,
    1.54575,
    0.1192,
    1.01925,
    1.9395,
    0.11,
    0.29605,
    2.2698,
    0.2315,
    2.9898,
    0.51655,
    0.6621,
)
FSRS_WEIGHTS_SETTING_KEY = "fsrsWeights"

FIRST_SUCCESS_DELAYS = {
    FSRS_HARD: timedelta(minutes=10),
    FSRS_GOOD: timedelta(days=1),
    FSRS_EASY: timedelta(days=3),
}


@dataclass(frozen=True)
class MemoryScheduleResult:
    memory_state: MemoryState
    review_log: ReviewLog
    mistake_log: MistakeLog | None


def score_to_fsrs_rating(score: int) -> int:
    if score < 3:
        return FSRS_AGAIN
    if score == 3:
        return FSRS_HARD
    if score == 4:
        return FSRS_GOOD
    return FSRS_EASY


def constrain_difficulty(difficulty: float) -> float:
    return round(clamp(difficulty, MIN_FSRS_DIFFICULTY, MAX_FSRS_DIFFICULTY), 2)


def constrain_stability(stability_days: float) -> float:
    return clamp(stability_days, MIN_STABILITY_DAYS, MAX_STABILITY_DAYS)


def normalize_fsrs_weights(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != len(FSRS_WEIGHTS):
        return None
    try:
        weights = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if any(weight <= 0 for weight in weights[:4]):
        return None
    return weights


def get_user_fsrs_weights(db: Session, user_id: UUID) -> tuple[float, ...]:
    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    except ProgrammingError:
        return FSRS_WEIGHTS
    if stored_settings is None:
        return FSRS_WEIGHTS
    return normalize_fsrs_weights(stored_settings.settings.get(FSRS_WEIGHTS_SETTING_KEY)) or FSRS_WEIGHTS


def initial_fsrs_difficulty(rating: int, weights: tuple[float, ...] = FSRS_WEIGHTS) -> float:
    return constrain_difficulty(weights[4] - exp((rating - 1) * weights[5]) + 1)


def initial_fsrs_stability(rating: int, weights: tuple[float, ...] = FSRS_WEIGHTS) -> float:
    return constrain_stability(weights[rating - 1])


def elapsed_days_since_last_review(memory_state: MemoryState, now: datetime) -> float:
    if memory_state.last_reviewed_at is None:
        return 0.0
    return max((now - memory_state.last_reviewed_at).total_seconds() / 86400, 0.0)


def scheduled_stability_days(memory_state: MemoryState) -> float:
    if memory_state.last_reviewed_at is not None:
        scheduled_days = max((memory_state.next_review_at - memory_state.last_reviewed_at).total_seconds() / 86400, 0.0)
        if scheduled_days > 0:
            return constrain_stability(scheduled_days)

    if memory_state.interval_days > 0:
        return constrain_stability(float(memory_state.interval_days))

    return MIN_STABILITY_DAYS


def calculate_fsrs_retrievability(elapsed_days: float, stability_days: float) -> float:
    if elapsed_days <= 0:
        return 1.0
    return round(clamp((1 + FSRS_FACTOR * elapsed_days / max(stability_days, MIN_STABILITY_DAYS)) ** FSRS_DECAY, 0.0, 1.0), 4)


def calculate_current_forget_risk(memory_state: MemoryState, now: datetime) -> float:
    stability_days = scheduled_stability_days(memory_state)
    retrievability = calculate_fsrs_retrievability(elapsed_days_since_last_review(memory_state, now), stability_days)
    lapse_penalty = min(memory_state.lapse_count * 0.03, 0.15)
    forget_risk = 1 - retrievability + lapse_penalty
    if memory_state.repetition_count == 0 and memory_state.lapse_count > 0:
        forget_risk = max(forget_risk, 0.75)
    return round(clamp(forget_risk, 0.0, 1.0), 2)


def calculate_review_priority(memory_state: MemoryState, now: datetime) -> float:
    current_risk = calculate_current_forget_risk(memory_state, now)
    overdue_hours = max((now - memory_state.next_review_at).total_seconds() / 3600, 0.0)
    overdue_boost = min(overdue_hours / 24, 1.0) * 0.25
    lapse_boost = min(memory_state.lapse_count * 0.04, 0.2)
    return round(clamp(current_risk + overdue_boost + lapse_boost, 0.0, 1.0), 4)


def next_fsrs_difficulty(current_difficulty: float, rating: int, weights: tuple[float, ...] = FSRS_WEIGHTS) -> float:
    next_difficulty = current_difficulty - weights[6] * (rating - FSRS_GOOD)
    easy_baseline = initial_fsrs_difficulty(FSRS_EASY, weights)
    mean_reverted = weights[7] * easy_baseline + (1 - weights[7]) * next_difficulty
    return constrain_difficulty(mean_reverted)


def next_fsrs_recall_stability(difficulty: float, stability_days: float, retrievability: float, rating: int, weights: tuple[float, ...] = FSRS_WEIGHTS) -> float:
    hard_penalty = weights[15] if rating == FSRS_HARD else 1.0
    easy_bonus = weights[16] if rating == FSRS_EASY else 1.0
    growth = (
        exp(weights[8])
        * (11 - difficulty)
        * stability_days ** (-weights[9])
        * (exp((1 - retrievability) * weights[10]) - 1)
        * hard_penalty
        * easy_bonus
    )
    if growth <= 0:
        growth = {FSRS_HARD: 0.2, FSRS_GOOD: 0.8, FSRS_EASY: 1.6}.get(rating, 0.2)
    return constrain_stability(stability_days * (1 + growth))


def next_fsrs_forget_stability(difficulty: float, stability_days: float, retrievability: float, weights: tuple[float, ...] = FSRS_WEIGHTS) -> float:
    next_stability = (
        weights[11]
        * difficulty ** (-weights[12])
        * ((stability_days + 1) ** weights[13] - 1)
        * exp((1 - retrievability) * weights[14])
    )
    return constrain_stability(min(next_stability, stability_days))


def calculate_fsrs_interval(stability_days: float) -> timedelta:
    interval_days = stability_days / FSRS_FACTOR * (FSRS_TARGET_RETENTION ** (1 / FSRS_DECAY) - 1)
    return timedelta(days=constrain_stability(interval_days))


def calculate_failure_delay(score: int, lapse_count: int) -> timedelta:
    if score <= 1 or lapse_count >= 3:
        return timedelta(minutes=5)
    if lapse_count == 2:
        return timedelta(minutes=20)
    return timedelta(minutes=30)


def adjust_delay_for_item_type(delay: timedelta, item_type: str) -> timedelta:
    if delay < timedelta(days=1):
        return delay

    if item_type == "sentence":
        return max(timedelta(days=1), delay * 0.7)
    if item_type == "phrase":
        return max(timedelta(days=1), delay * 0.85)
    return delay


def calculate_interval_days(delay: timedelta) -> int:
    if delay < timedelta(days=1):
        return 0
    return max(1, ceil(delay.total_seconds() / 86400))


def get_or_create_memory_state(db: Session, learning_item: LearningItem, now: datetime) -> MemoryState:
    memory_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == learning_item.id))
    if memory_state is not None:
        return memory_state

    memory_state = MemoryState(
        learning_item_id=learning_item.id,
        interval_days=0,
        ease_factor=DEFAULT_FSRS_DIFFICULTY,
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
    fsrs_weights = get_user_fsrs_weights(db, user_id)
    memory_state = get_or_create_memory_state(db, learning_item, now)
    is_correct = score >= 3
    rating = score_to_fsrs_rating(score)
    previous_repetition_count = memory_state.repetition_count
    previous_stability_days = scheduled_stability_days(memory_state)
    previous_retrievability = calculate_fsrs_retrievability(elapsed_days_since_last_review(memory_state, now), previous_stability_days)
    current_difficulty = constrain_difficulty(memory_state.ease_factor or DEFAULT_FSRS_DIFFICULTY)
    next_difficulty = next_fsrs_difficulty(current_difficulty, rating, fsrs_weights)

    if is_correct:
        memory_state.repetition_count += 1
        if score >= 5:
            unresolved_mistakes = db.scalars(
                select(MistakeLog).where(
                    MistakeLog.user_id == user_id,
                    MistakeLog.learning_item_id == learning_item.id,
                    MistakeLog.is_resolved.is_(False),
                )
            ).all()
            for mistake in unresolved_mistakes:
                mistake.is_resolved = True
    else:
        memory_state.repetition_count = 0
        memory_state.lapse_count += 1

    if previous_repetition_count == 0:
        next_stability_days = initial_fsrs_stability(rating, fsrs_weights)
    elif is_correct:
        next_stability_days = next_fsrs_recall_stability(next_difficulty, previous_stability_days, previous_retrievability, rating, fsrs_weights)
    else:
        next_stability_days = next_fsrs_forget_stability(next_difficulty, previous_stability_days, previous_retrievability, fsrs_weights)

    memory_state.ease_factor = next_difficulty
    if not is_correct:
        review_delay = calculate_failure_delay(score, memory_state.lapse_count)
    elif previous_repetition_count == 0:
        review_delay = FIRST_SUCCESS_DELAYS.get(rating, timedelta(days=1))
    else:
        review_delay = calculate_fsrs_interval(next_stability_days)

    review_delay = adjust_delay_for_item_type(review_delay, learning_item.item_type)
    memory_state.interval_days = calculate_interval_days(review_delay)
    if is_correct:
        next_retrievability_at_due = calculate_fsrs_retrievability(review_delay.total_seconds() / 86400, next_stability_days)
        memory_state.forget_risk = round(clamp(1 - next_retrievability_at_due + min(memory_state.lapse_count * 0.03, 0.15), 0.0, 1.0), 2)
        memory_state.memory_strength = round(1 - memory_state.forget_risk, 2)
    else:
        memory_state.forget_risk = 1.0
        memory_state.memory_strength = round(max(score / 5 * 0.35, 0.0), 2)
    memory_state.last_reviewed_at = now
    memory_state.next_review_at = now + review_delay

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
