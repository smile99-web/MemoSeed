from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil, exp, log
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
from app.models.user_model_settings import UserModelSettings
from app.utils import clamp, normalize_word, tokenize_words

FSRS_AGAIN = 1
FSRS_HARD = 2
FSRS_GOOD = 3
FSRS_EASY = 4
FSRS_DECAY = -0.5
FSRS_FACTOR = 19 / 81
FSRS_TARGET_RETENTION = 0.9
MIN_FSRS_DIFFICULTY = 1.3
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

# Child-calibrated FSRS weights: initial stability values reduced ~50% vs adult defaults.
# Indices 0-3 correspond to Again / Hard / Good / Easy initial stability in days.
CHILD_FSRS_WEIGHTS = (
    0.20,       # Again
    0.70,       # Hard
    1.50,       # Good (adult: 3.173)
    5.00,       # Easy (adult: 15.691)
    7.1949,     # w[4] — initial difficulty baseline (unchanged)
    0.5345,     # w[5]
    1.4604,     # w[6]
    0.0046,     # w[7]
    1.54575,    # w[8]
    0.1192,     # w[9]
    1.01925,    # w[10]
    1.9395,     # w[11]
    0.11,       # w[12]
    0.29605,    # w[13]
    2.2698,     # w[14]
    0.2315,     # w[15]
    2.9898,     # w[16]
    0.51655,    # w[17]
    0.6621,     # w[18]
)

CHILD_TARGET_RETENTION = 0.90

# Slow-learner FSRS weights: further reduced stability growth for children who
# "remember slowly, forget quickly". Initial stabilities same as child profile,
# but stability grows ~40% slower after correct reviews and drops more after lapses.
# This results in shorter, more frequent review intervals for struggling learners.
SLOW_LEARNER_FSRS_WEIGHTS = (
    0.20,       # w[0]  — Again initial stability (same as child)
    0.70,       # w[1]  — Hard initial stability (same as child)
    1.50,       # w[2]  — Good initial stability (same as child)
    5.00,       # w[3]  — Easy initial stability (same as child)
    7.1949,     # w[4]  — initial difficulty baseline
    0.5345,     # w[5]
    1.4604,     # w[6]
    0.0046,     # w[7]
    0.90,       # w[8]  — stability growth log-intercept (child: 1.54575, -42%)
    0.16,       # w[9]  — stability growth stability-exponent (child: 0.1192, +34%)
    0.65,       # w[10] — stability growth retrievability factor (child: 1.01925, -36%)
    1.10,       # w[11] — post-lapse stability factor (child: 1.9395, -43%)
    0.15,       # w[12] — post-lapse difficulty exponent (child: 0.11, +36%)
    0.38,       # w[13] — post-lapse stability exponent (child: 0.29605, +28%)
    1.30,       # w[14] — post-lapse retrievability factor (child: 2.2698, -43%)
    0.2315,     # w[15] — hard penalty multiplier
    2.9898,     # w[16] — easy bonus multiplier
    0.51655,    # w[17] — STS weight 1
    0.6621,     # w[18] — STS weight 2
)
SLOW_LEARNER_TARGET_RETENTION = 0.92

# Child STS (short-term stability) constants for intraday memory scheduling.
# STS models the rapidly decaying working-memory trace that operates on a
# minutes-to-hours timescale, as opposed to the days-to-months timescale of
# the long-term FSRS stability.
CHILD_STS_DECAY = -0.8                 # steeper than long-term FSRS_DECAY=-0.5
CHILD_STS_HALF_LIFE_MINUTES = 30       # STS half-life in minutes
CHILD_STS_TO_LTS_TRANSFER_RATE = 0.15  # fraction of STS converted to LTS per review

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
WORD_MEMORY_SOURCE = "word-memory"
EASY_FUNCTION_WORDS = {
    "a",
    "an",
    "am",
    "are",
    "be",
    "can",
    "do",
    "go",
    "he",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "she",
    "the",
    "to",
    "we",
    "you",
}
HARD_ABSTRACT_WORDS = {
    "because",
    "beautiful",
    "different",
    "important",
    "interesting",
    "remember",
    "student",
    "teacher",
    "together",
}

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


def get_effective_fsrs_params(db: Session, user_id: UUID) -> tuple[tuple[float, ...], float]:
    """Return (fsrs_weights, target_retention) based on user profile and any personalized fit.

    Defaults to child-calibrated parameters (useChildProfile=True) since this is a child app.
    Personalized weights from a prior fitting always take precedence.
    """
    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    except ProgrammingError:
        return (CHILD_FSRS_WEIGHTS, CHILD_TARGET_RETENTION)

    if stored_settings is None:
        return (CHILD_FSRS_WEIGHTS, CHILD_TARGET_RETENTION)

    settings = stored_settings.settings or {}
    use_child_profile = settings.get("useChildProfile", True)
    use_slow_learner = settings.get("useSlowLearnerProfile", False)

    personalized_weights = normalize_fsrs_weights(settings.get(FSRS_WEIGHTS_SETTING_KEY))
    if personalized_weights is not None:
        if use_slow_learner:
            return (personalized_weights, SLOW_LEARNER_TARGET_RETENTION)
        target_retention = CHILD_TARGET_RETENTION if use_child_profile else FSRS_TARGET_RETENTION
        return (personalized_weights, target_retention)

    if use_slow_learner:
        return (SLOW_LEARNER_FSRS_WEIGHTS, SLOW_LEARNER_TARGET_RETENTION)
    if use_child_profile:
        return (CHILD_FSRS_WEIGHTS, CHILD_TARGET_RETENTION)
    return (FSRS_WEIGHTS, FSRS_TARGET_RETENTION)


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
    error_boost = min(memory_state.consecutive_error_count * 0.08, 0.24)
    recent_practice_penalty = 0.0
    if memory_state.last_reviewed_at is not None:
        minutes_since_review = max((now - memory_state.last_reviewed_at).total_seconds() / 60, 0.0)
        if minutes_since_review < 3:
            recent_practice_penalty = 0.22
        elif minutes_since_review < 30:
            recent_practice_penalty = 0.12
        elif minutes_since_review < 120:
            recent_practice_penalty = 0.06
    if memory_state.consecutive_error_count > 0:
        recent_practice_penalty *= 0.35
    return round(clamp(current_risk + overdue_boost + lapse_boost + error_boost - recent_practice_penalty, 0.0, 1.0), 4)


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


def calculate_fsrs_interval(stability_days: float, target_retention: float = FSRS_TARGET_RETENTION) -> timedelta:
    interval_days = stability_days / FSRS_FACTOR * (target_retention ** (1 / FSRS_DECAY) - 1)
    return timedelta(days=constrain_stability(interval_days))


def compute_short_term_stability(memory_state: MemoryState, now: datetime) -> float:
    """Return the current short-term stability (STS) after applying exponential decay.

    STS = initial_stability_short * exp(-elapsed_minutes * ln(2) / CHILD_STS_HALF_LIFE_MINUTES)
    """
    if memory_state.short_term_stability is None:
        return 1.0
    if memory_state.last_short_term_updated_at is None:
        return float(memory_state.short_term_stability)
    elapsed_minutes = (now - memory_state.last_short_term_updated_at).total_seconds() / 60.0
    if elapsed_minutes <= 0:
        return float(memory_state.short_term_stability)
    decay_rate = log(2) / CHILD_STS_HALF_LIFE_MINUTES
    current_sts = float(memory_state.short_term_stability) * exp(-elapsed_minutes * decay_rate)
    return round(clamp(current_sts, 0.0, 1.0), 4)


def same_day_next_interval(sts: float, target_retrievability: float) -> timedelta:
    """Compute the same-day review interval derived from STS exponential decay.

    Solves  STS * exp(-t * ln(2) / half_life) = target_retrievability  for t,
    clamped to [2, 360] minutes.
    """
    if sts <= target_retrievability:
        return timedelta(minutes=2.0)
    interval_minutes = -(CHILD_STS_HALF_LIFE_MINUTES / log(2)) * log(target_retrievability / sts)
    clamped_minutes = clamp(interval_minutes, 2.0, 360.0)
    return timedelta(minutes=clamped_minutes)


def calculate_failure_delay(score: int, lapse_count: int, now: datetime, memory_state: MemoryState) -> timedelta:
    """STS-based failure delay replacing the hardcoded 3/10/30-minute ladder.

    Each failure reduces the short-term stability by 40 % and the next
    interval is computed from the reduced STS via same_day_next_interval.
    """
    current_sts = compute_short_term_stability(memory_state, now)

    # Cumulative STS reduction: each consecutive failure knocks off 40 %.
    reduced_sts = current_sts * (0.6 ** lapse_count)

    # Use a failure target that decreases with more failures, giving longer
    # recovery intervals for repeated lapses.
    failure_target = max(0.05, FSRS_TARGET_RETENTION * (0.7 ** (lapse_count - 1)))

    delay = same_day_next_interval(reduced_sts, failure_target)

    # Stuck-loop escape: when a child keeps failing the same word (3+ lapses),
    # give it a much longer timeout so other words get a chance.
    if lapse_count >= 3:
        delay = max(delay, timedelta(hours=2))
    else:
        local_now = now.astimezone(LOCAL_TIMEZONE)
        end_of_day = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
        if end_of_day <= local_now:
            end_of_day = local_now.replace(hour=21, minute=30, second=0, microsecond=0)
        end_of_day_delay = end_of_day.astimezone(UTC) - now
        delay = max(min(delay, end_of_day_delay), timedelta(minutes=2))

    return delay


def recompute_item_difficulty(learning_item: LearningItem, memory_state: MemoryState) -> int:
    """Dynamically estimate item difficulty (1-5) based on actual child performance.

    Previously all items defaulted to difficulty=1, which incorrectly treated
    every word as easy (boosting intervals by 1.1x).  Now difficulty reflects
    real struggle patterns: lapse count, memory weakness, and error persistence.
    """
    base_score = 2.0  # start at moderate

    # Lapse penalty: each lapse raises difficulty
    lapse_count = memory_state.lapse_count or 0
    base_score += min(lapse_count * 0.8, 2.0)

    # Low memory strength = harder
    strength = memory_state.memory_strength or 0.0
    if strength < 0.4:
        base_score += 1.5
    elif strength < 0.6:
        base_score += 0.8
    elif strength < 0.8:
        base_score += 0.2

    # Word-length heuristic
    words = tokenize_words(learning_item.english_text)
    if learning_item.item_type == "word" and words:
        word = normalize_word(words[0])
        if len(word) >= 8:
            base_score += 0.8
        elif len(word) >= 6:
            base_score += 0.3
        if word in EASY_FUNCTION_WORDS:
            base_score -= 1.0

    # Item type
    if learning_item.item_type == "sentence":
        base_score += 1.0
    elif learning_item.item_type == "phrase":
        base_score += 0.5

    # High error count = harder
    if (memory_state.consecutive_error_count or 0) >= 2:
        base_score += 1.0

    return max(1, min(5, round(base_score)))


def estimate_item_difficulty_adjustment(learning_item: LearningItem, rating: int) -> float:
    words = tokenize_words(learning_item.english_text)
    adjustment = (learning_item.difficulty_level - 3) * 0.35
    if learning_item.item_type == "sentence":
        adjustment += 0.9
    elif learning_item.item_type == "phrase":
        adjustment += 0.45
    elif learning_item.item_type == "word":
        word = normalize_word(words[0]) if words else ""
        if word in EASY_FUNCTION_WORDS or len(word) <= 3:
            adjustment -= 1.0
        if len(word) >= 8:
            adjustment += 0.8
        elif len(word) >= 6:
            adjustment += 0.35
        if word in HARD_ABSTRACT_WORDS:
            adjustment += 0.75
    if rating == FSRS_AGAIN:
        adjustment += 0.5
    return adjustment


def adjust_delay_for_item_type(delay: timedelta, item_type: str) -> timedelta:
    if delay < timedelta(days=1):
        return delay

    if item_type == "sentence":
        return max(timedelta(days=1), delay * 0.7)
    if item_type == "phrase":
        return max(timedelta(days=1), delay * 0.85)
    return delay


def adjust_delay_for_learning_item(delay: timedelta, learning_item: LearningItem, memory_state: MemoryState) -> timedelta:
    delay = adjust_delay_for_item_type(delay, learning_item.item_type)
    if delay < timedelta(days=1):
        return delay

    words = tokenize_words(learning_item.english_text)
    if learning_item.item_type == "word":
        word = normalize_word(words[0]) if words else ""
        if word in EASY_FUNCTION_WORDS and memory_state.lapse_count == 0:
            delay *= 1.25
        if len(word) >= 8 or word in HARD_ABSTRACT_WORDS or memory_state.lapse_count >= 2:
            delay *= 0.75
    if learning_item.difficulty_level >= 4:
        delay *= 0.85
    elif learning_item.difficulty_level <= 2 and memory_state.lapse_count == 0:
        delay *= 1.1

    # P0-1: Time-aware scheduling — adjust based on historical time-of-day efficiency.
    # Data shows children perform best 22:00-23:00 (60%) and 10:00-11:00 (57%),
    # worst 12:00-16:00 (40%) and 15:00-16:00 (10%).
    now_local = datetime.now(LOCAL_TIMEZONE)
    current_hour = now_local.hour
    if current_hour in (10, 22, 23):
        # Peak efficiency window — can handle harder words
        delay *= 1.0  # keep as-is
    elif current_hour in (11, 17, 18, 19, 21):
        # Normal window — slight boost for high-risk items
        if (memory_state.forget_risk or 0) >= 0.7:
            delay *= 0.85  # shorten for risky words
    elif current_hour in (12, 13, 14, 15, 16, 20):
        # Low efficiency window — easier review, shorter intervals
        delay *= 0.75  # only serve easy/review words
    # (hours 0-9: no adjustment, assume not studying)

    # P0-2: Hint-dependency penalty — if child consistently needs hints,
    # force more frequent reviews to break the dependency.
    total_no_preview = (memory_state.recall_correct_count or 0) + (memory_state.hinted_correct_count or 0)
    if total_no_preview >= 5:
        hint_ratio = (memory_state.hinted_correct_count or 0) / total_no_preview
        if hint_ratio >= 0.5:
            # Word is heavily hint-dependent — review 30% sooner
            delay *= 0.7

    return max(timedelta(days=1), delay)


def update_memory_counters(memory_state: MemoryState, is_correct: bool, review_mode: str) -> None:
    if is_correct:
        memory_state.consecutive_correct_count += 1
        memory_state.consecutive_error_count = 0
        if review_mode.startswith("word-recall"):
            memory_state.recall_correct_count += 1
        elif review_mode.startswith("word-hinted"):
            memory_state.hinted_correct_count += 1
        elif review_mode.startswith("word-preview"):
            memory_state.preview_correct_count += 1
        elif review_mode.startswith("word-context"):
            memory_state.context_correct_count += 1
        return

    memory_state.consecutive_error_count += 1
    memory_state.consecutive_correct_count = 0


def calculate_interval_days(delay: timedelta) -> int:
    if delay < timedelta(days=1):
        return 0
    return max(1, ceil(delay.total_seconds() / 86400))


def get_or_create_memory_state(db: Session, learning_item: LearningItem, now: datetime) -> MemoryState:
    memory_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == learning_item.id))
    if memory_state is not None:
        return memory_state

    # New items get a 2-hour grace window before appearing as "due"
    # so they don't immediately count as overdue on import.
    memory_state = MemoryState(
        learning_item_id=learning_item.id,
        interval_days=0,
        ease_factor=DEFAULT_FSRS_DIFFICULTY,
        memory_strength=0.0,
        forget_risk=1.0,
        repetition_count=0,
        lapse_count=0,
        next_review_at=now + timedelta(hours=2),
        short_term_stability=1.0,
        last_short_term_updated_at=now,
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
    error_type: str | None = None,
    encoding_stage: str | None = None,
    encoding_duration_ms: int = 0,
) -> MemoryScheduleResult:
    if score < 0 or score > 5:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Score must be between 0 and 5")

    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == learning_item_id, LearningItem.user_id == user_id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    now = datetime.now(UTC)
    fsrs_weights, fsrs_target_retention = get_effective_fsrs_params(db, user_id)
    memory_state = get_or_create_memory_state(db, learning_item, now)
    current_sts = compute_short_term_stability(memory_state, now)
    is_correct = score >= 3
    rating = score_to_fsrs_rating(score)
    previous_repetition_count = memory_state.repetition_count
    previous_stability_days = scheduled_stability_days(memory_state)
    previous_retrievability = calculate_fsrs_retrievability(elapsed_days_since_last_review(memory_state, now), previous_stability_days)
    current_difficulty = constrain_difficulty(memory_state.ease_factor or DEFAULT_FSRS_DIFFICULTY)
    next_difficulty = next_fsrs_difficulty(current_difficulty, rating, fsrs_weights)
    next_difficulty = constrain_difficulty(next_difficulty + estimate_item_difficulty_adjustment(learning_item, rating) * (0.4 if previous_repetition_count == 0 else 0.15))

    if is_correct:
        memory_state.repetition_count += 1
        if score >= 4:
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
    update_memory_counters(memory_state, is_correct, review_mode)

    if previous_repetition_count == 0:
        next_stability_days = initial_fsrs_stability(rating, fsrs_weights)
    elif is_correct:
        next_stability_days = next_fsrs_recall_stability(next_difficulty, previous_stability_days, previous_retrievability, rating, fsrs_weights)
    else:
        next_stability_days = next_fsrs_forget_stability(next_difficulty, previous_stability_days, previous_retrievability, fsrs_weights)

    # STS-to-LTS transfer: convert a fraction of current short-term stability
    # into durable long-term stability on each successful review.
    if is_correct and current_sts > 0:
        next_stability_days = constrain_stability(
            next_stability_days + CHILD_STS_TO_LTS_TRANSFER_RATE * current_sts
        )

    memory_state.ease_factor = next_difficulty
    if not is_correct:
        review_delay = calculate_failure_delay(score, memory_state.lapse_count, now, memory_state)
    elif previous_repetition_count == 0:
        # Use STS-derived interval for same-day (sub-24-hour) first successes
        # instead of the hardcoded FIRST_SUCCESS_DELAYS lookup table.
        same_day_targets = {
            FSRS_HARD: 0.5,
            FSRS_GOOD: 0.35,
            FSRS_EASY: 0.2,
        }
        same_day_target = same_day_targets.get(rating, FSRS_TARGET_RETENTION)
        sts_delay = same_day_next_interval(current_sts, same_day_target)
        if sts_delay < timedelta(hours=24):
            review_delay = sts_delay
        else:
            review_delay = FIRST_SUCCESS_DELAYS.get(rating, timedelta(days=1))
    else:
        review_delay = calculate_fsrs_interval(next_stability_days, fsrs_target_retention)

    review_delay = adjust_delay_for_learning_item(review_delay, learning_item, memory_state)
    memory_state.interval_days = calculate_interval_days(review_delay)
    if is_correct:
        next_retrievability_at_due = calculate_fsrs_retrievability(review_delay.total_seconds() / 86400, next_stability_days)
        memory_state.forget_risk = round(clamp(1 - next_retrievability_at_due + min(memory_state.lapse_count * 0.03, 0.15), 0.0, 1.0), 2)
        memory_state.memory_strength = round(1 - memory_state.forget_risk, 2)
        # Reset STS after successful review: memory has reconsolidated.
        memory_state.short_term_stability = 1.0
    else:
        memory_state.forget_risk = 1.0
        memory_state.memory_strength = round(max(score / 5 * 0.35, 0.0), 2)
        # Each failure reduces STS by 40 % for the next interval.
        memory_state.short_term_stability = round(current_sts * 0.6, 4)
    memory_state.last_short_term_updated_at = now
    memory_state.last_reviewed_at = now
    memory_state.next_review_at = now + review_delay

    review_log = ReviewLog(
        user_id=user_id,
        learning_item_id=learning_item.id,
        review_mode=review_mode,
        error_type=error_type,
        score=score,
        is_correct=is_correct,
        response_text=response_text,
        duration_seconds=duration_seconds,
        encoding_stage=encoding_stage,
        encoding_duration_ms=encoding_duration_ms,
    )
    db.add(review_log)

    mistake_log = None
    if not is_correct:
        mistake_log = MistakeLog(
            user_id=user_id,
            learning_item_id=learning_item.id,
            mistake_type=review_mode,
            error_type=error_type,
            expected_answer=learning_item.english_text,
            actual_answer=response_text or "",
            is_resolved=False,
        )
        db.add(mistake_log)

    # Dynamic difficulty estimation — updates learning_item.difficulty_level
    # based on actual child performance so the scheduler can adjust intervals.
    new_difficulty = recompute_item_difficulty(learning_item, memory_state)
    if new_difficulty != learning_item.difficulty_level:
        learning_item.difficulty_level = new_difficulty
        db.add(learning_item)
    db.add(memory_state)
    db.commit()
    db.refresh(memory_state)
    db.refresh(review_log)
    if mistake_log is not None:
        db.refresh(mistake_log)

    return MemoryScheduleResult(memory_state=memory_state, review_log=review_log, mistake_log=mistake_log)
