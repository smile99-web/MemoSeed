import math
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.review_log import ReviewLog
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.services.memory_dashboard import calculate_word_priority
from app.services.memory_scheduler import (
    ASSISTED_REVIEW_MODES,
    FSRS_DECAY,
    FSRS_FACTOR,
    MIN_STABILITY_DAYS,
    compute_short_term_stability,
    same_day_next_interval,
    scheduled_stability_days,
)
from app.utils import clamp, normalize_word

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

TASK_TYPE_LABELS = {
    "chinese_to_english": "看中文拼英文",
    "listen_spell": "听英文拼英文",
    "listen_choose_chinese": "听英文选中文",
    "english_to_chinese": "英文选中文",
    "match_translation": "中英文配对",
    "missing_letter": "缺字母填空",
    "hidden_recall": "看 3 秒后隐藏重拼",
}

ERROR_TYPE_TASK_STRATEGIES = {
    "first-letter": ["listen_choose_chinese", "english_to_chinese", "chinese_to_english", "listen_spell"],
    "meaning": ["listen_choose_chinese", "english_to_chinese", "chinese_to_english", "match_translation"],
    "middle": ["english_to_chinese", "missing_letter", "hidden_recall", "listen_spell"],
    "sequence": ["english_to_chinese", "missing_letter", "hidden_recall", "listen_spell"],
    "ending": ["english_to_chinese", "missing_letter", "hidden_recall", "chinese_to_english"],
    "missing-letter": ["english_to_chinese", "missing_letter", "hidden_recall", "listen_spell"],
    "extra-letter": ["english_to_chinese", "missing_letter", "hidden_recall", "listen_spell"],
    "unknown": ["english_to_chinese", "hidden_recall", "chinese_to_english", "listen_spell", "missing_letter"],
    "spelling": ["english_to_chinese", "missing_letter", "listen_spell", "chinese_to_english"],
    "spelling-spelling": ["english_to_chinese", "missing_letter", "listen_spell", "chinese_to_english"],
}

TEACHING_TIPS = {
    "first-letter": "先确认中文意思，再听首音，最后拼首字母。",
    "meaning": "先把中文意思和英文单词配上，再进入拼写。",
    "middle": "按音节或字母块拆开，中间部分慢慢拼。",
    "sequence": "先看清字母顺序，再用缺字母题固定视觉记忆。",
    "ending": "重点看词尾，注意后缀、时态或单复数。",
    "missing-letter": "先数清字母个数，再补缺失位置。",
    "extra-letter": "先数清字母个数，去掉多余字母。",
    "unknown": "先看 5 秒建立印象，再隐藏重拼。",
}


def get_or_create_word_memory_state(
    db: Session,
    user_id: UUID,
    word: str,
    learning_item_id: UUID | None = None,
    memory_state_id: UUID | None = None,
) -> WordMemoryState:
    normalized_word = normalize_word(word)
    word_state = db.scalar(select(WordMemoryState).where(WordMemoryState.user_id == user_id, WordMemoryState.word == normalized_word))
    if word_state is not None:
        # IMPORTANT: only set the FK link if it isn't already established.
        # Overwriting an existing learning_item_id would silently destroy the
        # word's history: sync_word_memory_from_review later copies values
        # (memory_strength, recall_correct_count, etc.) FROM the *new*
        # memory_state — which may be a different (weaker) item — overwriting
        # counters the child built up against the original item. This was the
        # root cause of the "mastered count drops" bug for words that appear
        # in multiple learning_items.
        if word_state.learning_item_id is None and learning_item_id is not None:
            word_state.learning_item_id = learning_item_id
        if word_state.memory_state_id is None and memory_state_id is not None:
            word_state.memory_state_id = memory_state_id
        return word_state

    word_state = WordMemoryState(
        user_id=user_id,
        word=normalized_word,
        learning_item_id=learning_item_id,
        memory_state_id=memory_state_id,
    )
    db.add(word_state)
    db.flush()
    return word_state


def sync_word_memory_from_review(
    db: Session,
    user_id: UUID,
    word: str,
    memory_state: MemoryState,
    review_mode: str,
    is_correct: bool,
    error_type: str | None,
    now: datetime | None = None,
) -> WordMemoryState:
    now = now or datetime.now(UTC)
    word_state = get_or_create_word_memory_state(db, user_id, word, memory_state.learning_item_id, memory_state.id)
    word_state.memory_strength = memory_state.memory_strength
    word_state.forget_risk = memory_state.forget_risk
    word_state.consecutive_correct_count = memory_state.consecutive_correct_count
    word_state.consecutive_error_count = memory_state.consecutive_error_count
    word_state.recall_correct_count = memory_state.recall_correct_count
    word_state.hinted_correct_count = memory_state.hinted_correct_count
    word_state.preview_correct_count = memory_state.preview_correct_count
    word_state.context_correct_count = memory_state.context_correct_count
    word_state.last_reviewed_at = now
    word_state.next_micro_review_at = memory_state.next_review_at

    if is_correct and review_mode.startswith("word-recall"):
        local_date = now.astimezone(LOCAL_TIMEZONE).date()
        if word_state.last_no_hint_correct_date != local_date:
            word_state.no_hint_correct_date_count += 1
            word_state.last_no_hint_correct_date = local_date
    if is_correct and review_mode.startswith("word-preview"):
        word_state.hidden_recall_correct_count += 1
        word_state.last_answer_seen_at = now
    if error_type:
        counts = dict(word_state.error_type_counts or {})
        existing = counts.get(error_type)
        if isinstance(existing, dict):
            new_count = int(existing.get("count", 0)) + (1 if is_correct else 2)
        else:
            new_count = int(existing or 0) + (1 if is_correct else 2)
        counts[error_type] = {"count": new_count, "last": now.isoformat()}
        word_state.error_type_counts = counts

    word_state.priority_score = calculate_word_memory_priority(word_state, now)
    # P8: recent accuracy gates mastery. Cumulative counters never decrease,
    # so chronic failures with a lucky history (e.g. 'early': 152 lapses,
    # 33% recent accuracy) used to display as "mastered". Computed lazily —
    # only when the word is anywhere near mastery territory.
    # P16/P21: stats now come from REAL tests only (assisted phases excluded)
    # and include the spaced-proof signals (distinct correct days, test count)
    # that P21's graduation gate requires.
    recent_accuracy: float | None = None
    recent_correct_days: int | None = None
    recent_test_count: int | None = None
    if (word_state.memory_strength or 0) >= 0.5:
        stats = get_recent_word_test_stats(db, user_id, memory_state.learning_item_id)
        if stats is not None:
            recent_accuracy, recent_correct_days, recent_test_count = stats
    word_state.status = derive_word_status(word_state, recent_accuracy, recent_correct_days, recent_test_count)
    db.add(word_state)
    return word_state


# P15/P16: ASSISTED_REVIEW_MODES (imported from memory_scheduler above) lists
# the review modes that show the answer (or heavy hints) BEFORE the child
# responds. They are *assisted* phases, not tests: they can never fail (100%
# "correct") and used to make up 63% of all review_logs — feeding fake scores
# to FSRS and polluting every accuracy metric. After P15 they are telemetry
# only: no review_log, no FSRS mutation, no accuracy contribution.

# P8: mastery recency gates (window = last N REAL-test reviews for the item)
RECENT_ACCURACY_WINDOW = 10
# P21: graduation now requires PROOF, not just cumulative counters. Mastered =
# enough real tests, decent accuracy, and correct on at least 2 distinct local
# days (spaced proof — a same-day cram session no longer graduates a word).
MASTERED_MIN_TEST_COUNT = 5
MASTERED_MIN_RECENT_ACCURACY = 0.70
MASTERED_MIN_CORRECT_DAYS = 2
NEAR_MASTERED_MIN_TEST_COUNT = 3
NEAR_MASTERED_MIN_RECENT_ACCURACY = 0.60
DEMOTION_RECENT_ACCURACY = 0.60


def _real_word_test_clause():
    return (
        ReviewLog.review_mode.like("word-%"),
        ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES)),
    )


def get_recent_word_test_stats(
    db: Session,
    user_id: UUID,
    learning_item_id: UUID | None,
    limit: int = RECENT_ACCURACY_WINDOW,
) -> tuple[float, int, int] | None:
    """P16: stats over the item's last `limit` REAL word tests (assisted modes
    excluded): (accuracy, correct_days, test_count).

    correct_days = number of DISTINCT local dates among the correct tests —
    the P21 "spaced proof" signal. Returns None when there are no real tests
    yet (callers fall back to cumulative-only behavior).
    """
    if learning_item_id is None:
        return None
    rows = db.execute(
        select(ReviewLog.is_correct, ReviewLog.reviewed_at)
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.learning_item_id == learning_item_id,
            *_real_word_test_clause(),
        )
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(limit)
    ).all()
    if not rows:
        return None
    correct_dates = {
        reviewed_at.astimezone(LOCAL_TIMEZONE).date()
        for is_correct, reviewed_at in rows
        if is_correct
    }
    accuracy = sum(1 for is_correct, _ in rows if is_correct) / len(rows)
    return accuracy, len(correct_dates), len(rows)


def get_recent_word_accuracy(
    db: Session,
    user_id: UUID,
    learning_item_id: UUID | None,
    limit: int = RECENT_ACCURACY_WINDOW,
) -> float | None:
    """Return the is_correct ratio over the item's last `limit` REAL word tests.

    None when there is no data (new word) — callers treat that as "no recency
    evidence" and fall back to cumulative-only behavior.
    """
    stats = get_recent_word_test_stats(db, user_id, learning_item_id, limit)
    return stats[0] if stats is not None else None


def calculate_word_memory_priority(word_state: WordMemoryState, now: datetime) -> float:
    stats = type(
        "WordPriorityStats",
        (),
        {
            "mistake_count": max(word_state.consecutive_error_count, 0),
            "consecutive_error_count": word_state.consecutive_error_count,
            "preview_correct_count": word_state.preview_correct_count,
            "recall_correct_count": word_state.recall_correct_count,
            "last_reviewed_at": word_state.last_reviewed_at,
            "error_type_counts": word_state.error_type_counts or {},
        },
    )()
    return calculate_word_priority(stats, word_state.memory_strength, word_state.forget_risk, word_state.next_micro_review_at, now)


def derive_word_status(
    word_state: WordMemoryState,
    recent_accuracy: float | None = None,
    recent_correct_days: int | None = None,
    recent_test_count: int | None = None,
) -> str:
    # Mastered: cumulative recall evidence + spaced practice + (mostly) clean
    # recent error streak.
    #
    # NOTE: previously required `consecutive_correct_count >= 3 AND
    # consecutive_error_count == 0`. Both are reset-on-error counters: any
    # single mistake would demote a mastered word. This caused the
    # "mastered count drops as child learns more" bug — see
    # docs/fsrs_verification_report.md and tests/test_mastery_status.py.
    #
    # Minimum fix: collapse the two buggy reset-based conditions into the
    # single non-reset equivalent `consecutive_error_count <= 1` (tolerate
    # one natural slip).
    #
    # P21: when real-test stats are available (recent_test_count is not None),
    # graduation requires PROOF instead of cumulative counters:
    #   mastered      = ≥5 real tests, ≥70% accuracy, correct on ≥2 distinct
    #                   days (spaced proof), ≤1 consecutive error, strength≥0.6
    #   near_mastered = ≥3 real tests, ≥60% accuracy, strength≥0.55
    # Accuracy here is REAL-test only (P16: assisted 100%-correct phases are
    # excluded), so these numbers reflect actual retrieval.
    # When stats are absent (recent_test_count is None — e.g. legacy tests or
    # a word with no real tests yet) the pre-P21 cumulative path below runs
    # unchanged.
    recency_blocks_mastery = recent_accuracy is not None and recent_accuracy < DEMOTION_RECENT_ACCURACY
    if recent_test_count is not None:
        if (
            not recency_blocks_mastery
            and recent_test_count >= MASTERED_MIN_TEST_COUNT
            and (recent_accuracy or 0) >= MASTERED_MIN_RECENT_ACCURACY
            and (recent_correct_days or 0) >= MASTERED_MIN_CORRECT_DAYS
            and word_state.consecutive_error_count <= 1
            and word_state.memory_strength >= 0.6
        ):
            return "mastered"
        if (
            not recency_blocks_mastery
            and recent_test_count >= NEAR_MASTERED_MIN_TEST_COUNT
            and (recent_accuracy or 0) >= NEAR_MASTERED_MIN_RECENT_ACCURACY
            and word_state.memory_strength >= 0.55
        ):
            return "near_mastered"
    else:
        if (
            not recency_blocks_mastery
            and word_state.memory_strength >= 0.75
            and word_state.recall_correct_count >= 3
            and word_state.no_hint_correct_date_count >= 3
            and word_state.consecutive_error_count <= 1
            and (recent_accuracy is None or recent_accuracy >= MASTERED_MIN_RECENT_ACCURACY)
        ):
            return "mastered"
        if (
            not recency_blocks_mastery
            and word_state.memory_strength >= 0.72
            and word_state.recall_correct_count >= 2
            and word_state.no_hint_correct_date_count >= 2
            and word_state.consecutive_error_count == 0
            and (recent_accuracy is None or recent_accuracy >= NEAR_MASTERED_MIN_RECENT_ACCURACY)
        ):
            return "near_mastered"
    if word_state.consecutive_error_count >= 3 or word_state.priority_score >= 0.78:
        return "difficult"
    if word_state.preview_correct_count > word_state.recall_correct_count or word_state.last_answer_seen_at is not None:
        return "teaching"
    return "consolidating"


def schedule_micro_review_tasks_for_mistake(
    db: Session,
    user_id: UUID,
    word_state: WordMemoryState,
    prompt_text: str,
    source_learning_item_id: UUID | None,
    error_type: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    cancel_future_pending_tasks(db, user_id, word_state.word)

    memory_state = None
    if word_state.memory_state_id is not None:
        memory_state = db.scalar(select(MemoryState).where(MemoryState.id == word_state.memory_state_id))

    plan = build_micro_review_plan(now, word_state, error_type, memory_state)
    word_state.micro_review_stage += 1
    word_state.next_micro_review_at = plan[0][1] if plan else word_state.next_micro_review_at

    task_counts = dict(word_state.task_type_counts or {})
    for task_type, due_at, priority_multiplier in plan:
        task_counts[task_type] = int(task_counts.get(task_type, 0)) + 1
        db.add(
            WordReviewTask(
                user_id=user_id,
                word_memory_state_id=word_state.id,
                learning_item_id=source_learning_item_id,
                word=word_state.word,
                task_type=task_type,
                prompt_text=build_task_prompt(task_type, word_state.word, prompt_text),
                expected_answer=word_state.word,
                choices=build_task_choices(db, user_id, task_type, word_state.word, prompt_text),
                priority_score=round(min(max(word_state.priority_score * priority_multiplier, 0.05), 1.0), 2),
                status="pending",
                source=f"word-memory:{error_type}:{TEACHING_TIPS.get(error_type, '专项复习')}",
                due_at=due_at,
            )
        )
    word_state.task_type_counts = task_counts


def cancel_future_pending_tasks(db: Session, user_id: UUID, word: str) -> None:
    tasks = db.scalars(
        select(WordReviewTask).where(
            WordReviewTask.user_id == user_id,
            WordReviewTask.word == word,
            WordReviewTask.status == "pending",
        )
    ).all()
    for task in tasks:
        task.status = "superseded"


def supersede_stale_pending_tasks_for_reviewed_words(db: Session, user_id: UUID, now: datetime | None = None) -> bool:
    """Clear old pending word tasks after the word has already been reviewed.

    A word can be reviewed through another task or sentence flow while an older
    due WordReviewTask remains pending. If the word's next review has moved into
    the future and the task was due before the latest review, that pending task
    is stale and should no longer block/reappear in the review queue.
    """
    now = now or datetime.now(UTC)
    rows = db.execute(
        select(WordReviewTask, WordMemoryState)
        .join(WordMemoryState, WordMemoryState.id == WordReviewTask.word_memory_state_id)
        .where(
            WordReviewTask.user_id == user_id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at <= now,
            WordMemoryState.last_reviewed_at.isnot(None),
            WordMemoryState.next_micro_review_at.isnot(None),
            WordMemoryState.last_reviewed_at >= WordReviewTask.due_at,
            WordMemoryState.next_micro_review_at > now,
        )
    ).all()
    if not rows:
        return False

    for task, _word_state in rows:
        task.status = "superseded"
        task.completed_at = now
        db.add(task)
    db.flush()
    return True


CHILD_TARGET_RETENTION = 0.80
SAME_DAY_TARGET_RETRIEVABILITIES = (0.998, 0.99, 0.97, 0.94, 0.90)
LONG_TERM_MIN_DAYS = 0.5
LONG_TERM_MAX_DAYS = 30.0
DEFAULT_FALLBACK_STABILITY_DAYS = 1.0


def compute_micro_review_interval(
    word_state: WordMemoryState,
    target_retrievability: float,
    is_same_day: bool,
    now: datetime,
    memory_state: MemoryState | None = None,
) -> timedelta:
    """Compute an adaptive micro-review interval using the FSRS forgetting curve.

    For long-term tasks, uses full long-term stability (LTS) with the child's target
    retention to derive an optimal interval in days.  For same-day tasks, uses the
    STS exponential-decay model (compute_short_term_stability + same_day_next_interval)
    to match the Ebbinghaus forgetting curve during the critical first hours.
    """
    if is_same_day:
        if memory_state is not None:
            sts = compute_short_term_stability(memory_state, now)
        else:
            sts = max(word_state.memory_strength * 0.3, 0.01)
        return same_day_next_interval(sts, target_retrievability)

    if memory_state is not None:
        stability_days = scheduled_stability_days(memory_state)
    else:
        stability_days = max(word_state.memory_strength * 5.0, MIN_STABILITY_DAYS)
    if stability_days <= 0:
        stability_days = DEFAULT_FALLBACK_STABILITY_DAYS

    exponent = 1.0 / FSRS_DECAY  # -2.0
    raw_interval = stability_days * ((target_retrievability ** exponent - 1.0) / FSRS_FACTOR)
    clamped_days = clamp(raw_interval, LONG_TERM_MIN_DAYS, LONG_TERM_MAX_DAYS)
    return timedelta(days=clamped_days)


def build_micro_review_plan(
    now: datetime,
    word_state: WordMemoryState,
    error_type: str,
    memory_state: MemoryState | None = None,
) -> list[tuple[str, datetime, float]]:
    """Build an adaptive micro-review plan with FSRS-based interval scheduling.

    Same-day tasks receive intervals derived from a short-term stability (STS)
    factor applied to the word's long-term stability.  Long-term tasks use the
    last 3-4 tasks from the deduplicated task sequence with full LTS intervals.
    """
    local_now = now.astimezone(LOCAL_TIMEZONE)
    end_of_day = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
    if end_of_day <= local_now:
        end_of_day = local_now + timedelta(hours=2)
    end_of_day_utc = end_of_day.astimezone(UTC)

    task_sequence = choose_task_sequence(word_state, error_type)
    # P0-2: Meaning comprehension boost — for meaning errors (63% of child's errors),
    # prepend extra meaning-focused tasks to the sequence.
    if error_type == "meaning":
        meaning_tasks = ["english_to_chinese", "listen_choose_chinese"]
        for mt in meaning_tasks:
            if mt not in task_sequence:
                task_sequence.insert(1, mt)
    # P0-1: Reduce task count — 16,432 tasks were being superseded (96% waste).
    # Lower from 6+4 to 3+2 to give each task a real chance of being completed.
    num_same_day = min(3, len(task_sequence))
    long_term_count = min(2, len(task_sequence) - num_same_day)
    if long_term_count < 1:
        long_term_count = 1

    plan: list[tuple[str, datetime, float]] = []

    # Slot 0 — fires immediately for correction (timedelta(0) stays)
    plan.append((task_sequence[0], now, 1.0))

    # Same-day adaptive intervals (slots 1-5)
    for i in range(1, num_same_day):
        rt_index = i - 1
        if rt_index >= len(SAME_DAY_TARGET_RETRIEVABILITIES):
            rt_index = len(SAME_DAY_TARGET_RETRIEVABILITIES) - 1
        target_rt = SAME_DAY_TARGET_RETRIEVABILITIES[rt_index]
        interval = compute_micro_review_interval(word_state, target_rt, is_same_day=True, now=now, memory_state=memory_state)
        due = now + interval
        if due > end_of_day_utc:
            due = end_of_day_utc
        if i > 1:
            prev_due = plan[-1][1]
            if due <= prev_due:
                due = prev_due + timedelta(minutes=1)
        plan.append((task_sequence[i], due, max(1.0 - i * 0.08, 0.62)))

    # Long-term tasks — last 3-4 from the deduplicated sequence
    long_term_start = len(task_sequence) - long_term_count
    if long_term_start < num_same_day:
        long_term_start = num_same_day
    for i, task_type in enumerate(task_sequence[long_term_start:]):
        interval = compute_micro_review_interval(word_state, CHILD_TARGET_RETENTION, is_same_day=False, now=now, memory_state=memory_state)
        plan.append((task_type, now + interval, max(0.72 - i * 0.08, 0.5)))

    return plan


ERROR_TYPE_DECAY_HALF_LIFE_DAYS = 30.0


def get_decayed_error_weights(word_state: WordMemoryState, now: datetime) -> dict[str, float]:
    """Compute temporally decayed error weights.

    Errors older than ERROR_TYPE_DECAY_HALF_LIFE_DAYS have exponentially
    reduced influence.  Legacy integer counts (no timestamp) are treated
    as fully fresh until the first dict-format update.
    """
    raw = word_state.error_type_counts or {}
    decayed: dict[str, float] = {}
    for error_type, data in raw.items():
        if isinstance(data, dict):
            count = float(data.get("count", 0))
            last_str = str(data.get("last", ""))
            days = 0.0
            if last_str:
                try:
                    last_dt = datetime.fromisoformat(last_str)
                    days = max((now - last_dt).total_seconds() / 86400.0, 0.0)
                except (ValueError, TypeError):
                    pass
            weight = count * math.exp(-days / ERROR_TYPE_DECAY_HALF_LIFE_DAYS)
        else:
            weight = float(data or 0)
        decayed[error_type] = weight
    return decayed


def choose_task_sequence(word_state: WordMemoryState, error_type: str) -> list[str]:
    now_utc = datetime.now(UTC)
    base_sequence = ERROR_TYPE_TASK_STRATEGIES.get(error_type, ["chinese_to_english", "listen_spell", "missing_letter"])
    # Check the WHOLE base_sequence for hidden_recall, not just [:2]. The
    # previous `[:2]` check missed e.g. error_type="unknown" whose strategy
    # places hidden_recall at index 1, producing
    # ["hidden_recall", "english_to_chinese", "hidden_recall", ...] — the
    # later dedup loop strips the duplicate but in doing so shifts the
    # intended strategy order. Now we simply don't add a duplicate.
    if word_state.consecutive_error_count >= 3 and "hidden_recall" not in base_sequence:
        base_sequence = ["hidden_recall", *base_sequence]

    # --- Demote underperforming words to an easier mode ---
    # Two triggers compete, with R2 (first-failure) taking priority:
    #   R2: consecutive_error >= 3  →  force listen_choose_chinese (55% acc)
    #        The data shows 2522 correct answers came after 11+ prior failures.
    #        Switching sooner prevents unnecessary frustration.
    #   Q3: consecutive_error >= 2  →  demote to easiest unpracticed mode
    #        Sentence-spelling sits at 33% — the worst real task type.
    demoted_to_easier: str | None = None
    ce = word_state.consecutive_error_count or 0
    task_counts_for_pick = {str(k): int(v or 0) for k, v in (word_state.task_type_counts or {}).items()}
    if ce >= 3:
        # R2: Hard failure streak. Force the highest-success mode.
        demoted_to_easier = "listen_choose_chinese"
    elif ce >= 2:
        # Q3: Moderate failure streak. Pick easiest unpracticed option.
        candidates = ["listen_choose_chinese", "english_to_chinese", "match_translation"]
        demoted_to_easier = min(candidates, key=lambda t: task_counts_for_pick.get(t, 0))

    # --- R3: Time-of-day mode selection ---
    # Data shows 08-10h and 14-15h are low-efficiency (11-30% accuracy),
    # while 22-23h is the golden hour (55%). When demotion doesn't apply,
    # bias the first task toward the difficulty level the child can succeed
    # at given the current time.
    local_hour = now_utc.astimezone(LOCAL_TIMEZONE).hour
    low_efficiency = local_hour in (8, 9, 10, 14, 15)
    peak_hour = local_hour in (22, 23)
    easy_modes = ("listen_choose_chinese", "english_to_chinese", "match_translation", "hidden_recall")

    task_counts = {str(key): int(value or 0) for key, value in (word_state.task_type_counts or {}).items()}
    decayed_errors = get_decayed_error_weights(word_state, now_utc)

    deduped_sequence: list[str] = []
    for task_type in base_sequence:
        if task_type not in deduped_sequence:
            deduped_sequence.append(task_type)
    fallback_tasks = ["listen_choose_chinese", "english_to_chinese", "chinese_to_english", "listen_spell", "missing_letter"]
    for task_type in fallback_tasks:
        if task_type not in deduped_sequence:
            deduped_sequence.append(task_type)

    # Use decayed error weights as a tie-breaker: higher error weight → higher priority
    # Task types with recent errors get picked LESS often (they've been practiced enough)
    # Task types with low/no recent errors are promoted to ensure variety
    error_map = {
        "listen_choose_chinese": ["meaning", "first-letter"],
        "english_to_chinese": ["first-letter", "meaning"],
        "chinese_to_english": ["first-letter", "meaning"],
        "match_translation": ["meaning"],
        "listen_spell": ["first-letter", "middle", "sequence", "missing-letter", "extra-letter"],
        "missing_letter": ["middle", "sequence", "missing-letter", "extra-letter", "ending"],
        "hidden_recall": ["middle", "sequence", "ending", "unknown"],
    }
    task_error_weight: dict[str, float] = {}
    for t in deduped_sequence[:4]:
        relevant_errors = error_map.get(t, [])
        total_weight = sum(decayed_errors.get(e, 0.0) for e in relevant_errors)
        task_error_weight[t] = total_weight

    # Priority chain: R2 demotion > Q3 demotion > R3 time-of-day > default
    if demoted_to_easier is not None and demoted_to_easier in deduped_sequence:
        first_task = demoted_to_easier
    elif low_efficiency and not peak_hour:
        for easy in easy_modes:
            if easy in deduped_sequence[:4]:
                first_task = easy
                break
        else:
            first_task = min(
                deduped_sequence[:4],
                key=lambda t: (task_counts.get(t, 0), task_error_weight.get(t, 0.0)),
            )
    else:
        first_task = min(
            deduped_sequence[:4],
            key=lambda t: (task_counts.get(t, 0), task_error_weight.get(t, 0.0)),
        )
    return [first_task, *[task_type for task_type in deduped_sequence if task_type != first_task]]


def build_task_prompt(task_type: str, word: str, fallback_prompt: str) -> str:
    if task_type == "listen_spell":
        return f"听英文发音，拼写这个单词：{word}"
    if task_type == "listen_choose_chinese":
        return "听英文发音，选择正确的中文意思"
    if task_type == "english_to_chinese":
        return f"选择 {word} 的中文意思"
    if task_type == "match_translation":
        return f"把 {word} 和正确中文配对"
    if task_type == "missing_letter":
        return f"补全缺失字母：{mask_learning_letters(word)}"
    if task_type == "hidden_recall":
        return f"先看 5 秒，再隐藏重拼：{word}"
    return f"根据中文意思拼写英文：{word}"


def build_task_choices(
    db: Session,
    user_id: UUID,
    task_type: str,
    word: str,
    correct_chinese: str,
) -> list[str]:
    """Build choice options for multi-choice review tasks.

    Fetches up to 5 random Chinese translations from the user's other learning
    items as distractors.  If insufficient distractors are found, returns fewer
    items — the caller (build_micro_task_learning_item) will enrich via LLM.
    """
    if task_type not in {"listen_choose_chinese", "english_to_chinese", "match_translation"}:
        return []
    if not correct_chinese:
        return [word]

    distractor_rows = db.execute(
        select(LearningItem.chinese_text)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.chinese_text.isnot(None),
            LearningItem.chinese_text != "",
            func.length(LearningItem.chinese_text) <= 24,
            LearningItem.chinese_text != correct_chinese,
        )
        .order_by(func.random())
        .limit(10)
    ).scalars().all()

    unique_distractors: list[str] = []
    for text in distractor_rows:
        text = str(text).strip()
        if text and len(text) <= 24 and text not in unique_distractors and text != correct_chinese:
            unique_distractors.append(text)
        if len(unique_distractors) >= 5:
            break

    return [correct_chinese, *unique_distractors]


def mask_middle_letters(word: str) -> str:
    if len(word) <= 2:
        return "_ " * len(word)
    return " ".join([word[0], *("_" for _ in word[1:-1]), word[-1]])


def mask_learning_letters(word: str) -> str:
    """Render a word as a "fill-in-the-blank" hint for the missing_letter task.

    Pattern: show first and last letter, hide the middle. The number of
    visible middle letters scales with word length so children still get a
    length cue but the spelling challenge stays meaningful.
    """
    n = len(word)
    if n <= 2:
        return "_ " * n
    if n <= 5:
        # 3-5 letters: show first letter only.
        return " ".join([word[0], *("_" for _ in word[1:])])
    # 6+ letters: show first letter, ONE middle letter, and last letter.
    # The previous formula used word[3:-2] which leaks 1-2 letters for
    # 6-7 letter words (e.g. 'abcdef' → 'a _ _ d _ f') — too easy.
    # Show one anchored middle letter (position n//2) so the child can
    # count letters but still has to recall most of the word.
    mid = n // 2
    parts: list[str] = [word[0]]
    for i in range(1, n - 1):
        if i == mid:
            parts.append(word[i])
        else:
            parts.append("_")
    parts.append(word[-1])
    return " ".join(parts)


def complete_word_review_task(db: Session, user_id: UUID, task_id: UUID | None, is_correct: bool) -> None:
    if task_id is None:
        return
    task = db.scalar(select(WordReviewTask).where(WordReviewTask.id == task_id, WordReviewTask.user_id == user_id))
    if task is None or task.status != "pending":
        return
    task.status = "completed" if is_correct else "failed"
    task.completed_at = datetime.now(UTC)
