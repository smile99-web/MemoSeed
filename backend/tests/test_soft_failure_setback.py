"""
Tests for the Phase-1 scheduler reform (P7 soft failure setback, P8 mastery
recency gate, P2 daily budget / backlog smoothing).

Background
----------
Production data (2026-07-18) showed the old lapse handling collapsed every
word to interval_days=1 after ANY mistake: post-lapse FSRS stability drops
below 1 day via the w[11] formula, and adjust_delay_for_learning_item then
clamped ALL delays to >= 1 day. 169 of ~250 words were due every single day;
mastered words ate 54% of review time ('it': 118 unassisted successes in 14
days, still interval=1 because 29 slips kept resetting it); chronically
failing words with strong cumulative counters ('early': 152 lapses, 33%
recent accuracy) displayed as "mastered".

The reform:
  - P7: a lapse applies a proportional stability setback (60%/45%/30% floor
    by consecutive-error streak); mature words (interval >= 3d) keep a
    multi-day setback interval; learning-phase failures retry the same day
    with a 10-minute floor (no more 2-minute rapid-fire loops).
  - P8: derive_word_status takes an optional recent_accuracy gate.
  - P2: smooth_overdue_backlog spreads the >budget overflow over +1..+3 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.services.memory_scheduler import (
    MIN_FAILURE_RETRY_MINUTES,
    schedule_memory_review,
    smooth_overdue_backlog,
)
from app.services.word_memory import derive_word_status, get_recent_word_accuracy


# --- Mock db -------------------------------------------------------------------
class _MockDB:
    """Pop-list scalar mock (same pattern as tests/test_grammar_history.py).

    schedule_memory_review issues, in order:
      1. scalar(LearningItem)            -> the item
      2. scalar(UserModelSettings)       -> None (get_effective_fsrs_params)
      3. scalar(UserModelSettings)       -> None (metadata's params lookup)
      4. scalar(UserModelSettings)       -> None (metadata's settings lookup)
      5. scalar(MemoryState)             -> the pre-built state
    """

    def __init__(self, scalar_results):
        self._scalar_results = list(scalar_results)
        self.added = []
        self.commit_count = 0

    def scalar(self, _stmt):
        return self._scalar_results.pop(0) if self._scalar_results else None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        pass


def _make_item(user_id):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        english_text="example",
        chinese_text="例子",
        item_type="word",
        difficulty_level=3,
    )


def _make_state(item, *, now, interval_days, repetition_count, lapse_count=0,
                consecutive_error_count=0, consecutive_correct_count=0,
                last_reviewed_at=None, next_review_at=None):
    last_reviewed_at = last_reviewed_at if last_reviewed_at is not None else now - timedelta(days=max(interval_days, 1))
    next_review_at = next_review_at if next_review_at is not None else now
    return SimpleNamespace(
        learning_item_id=item.id,
        interval_days=interval_days,
        ease_factor=5.0,
        memory_strength=0.9,
        forget_risk=0.1,
        repetition_count=repetition_count,
        lapse_count=lapse_count,
        consecutive_correct_count=consecutive_correct_count,
        consecutive_error_count=consecutive_error_count,
        recall_correct_count=10,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
        last_reviewed_at=last_reviewed_at,
        next_review_at=next_review_at,
        short_term_stability=1.0,
        last_short_term_updated_at=last_reviewed_at,
        scheduler_type=None,
        algorithm_version=None,
        fsrs_params_snapshot=None,
    )


def _run_failure(db, item, state, user_id):
    return schedule_memory_review(
        db=db,
        user_id=user_id,
        learning_item_id=item.id,
        score=1,
        review_mode="word-spelling",
        response_text="exampel",
        duration_seconds=5,
        error_type="missing-letter",
    )


# --- P7: soft failure setback ---------------------------------------------------
class TestMatureWordSlipKeepsMultiDayInterval:
    def test_first_slip_halves_interval_instead_of_resetting_to_one(self):
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        state = _make_state(item, now=now, interval_days=14, repetition_count=10, lapse_count=5)
        db = _MockDB([item, None, None, None, state])

        result = _run_failure(db, item, state, user_id)

        # Old behavior: interval_days collapsed to 1 after ANY mistake.
        # New behavior: ~half of the 14-day interval (60% stability floor).
        assert result.memory_state.interval_days > 3, (
            f"Mature word's first slip must keep a multi-day interval, "
            f"got interval_days={result.memory_state.interval_days}"
        )
        assert result.memory_state.interval_days < 14, (
            "A lapse must still shorten the interval (no free pass)"
        )
        gap_days = (result.memory_state.next_review_at - now).total_seconds() / 86400
        assert gap_days > 3, f"next_review_at should be days away, got {gap_days:.2f}d"
        assert result.review_log.new_interval == result.memory_state.interval_days

    def test_consecutive_failures_step_down_gradually(self):
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        state = _make_state(item, now=now, interval_days=14, repetition_count=10, lapse_count=5)

        intervals = []
        gaps = []
        for _ in range(3):
            db = _MockDB([item, None, None, None, state])
            result = _run_failure(db, item, state, user_id)
            intervals.append(result.memory_state.interval_days)
            gaps.append((result.memory_state.next_review_at - result.memory_state.last_reviewed_at).total_seconds())

        assert intervals[0] > intervals[1] > intervals[2], (
            f"Intervals must step down across consecutive failures, got {intervals}"
        )
        assert gaps[0] > gaps[1] > gaps[2], (
            f"Next-review gaps must shrink across consecutive failures, got {gaps}"
        )


class TestLearningWordFailureRetriesSameDay:
    def test_learning_failure_retries_with_ten_minute_floor(self):
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        # interval=1d word, fully decayed STS -> raw delay hits the floor.
        state = _make_state(item, now=now, interval_days=1, repetition_count=2, lapse_count=0)
        db = _MockDB([item, None, None, None, state])

        result = _run_failure(db, item, state, user_id)

        gap = result.memory_state.next_review_at - result.memory_state.last_reviewed_at
        assert gap >= timedelta(minutes=MIN_FAILURE_RETRY_MINUTES), (
            f"Same-day retry must respect the 10-minute spacing floor, got {gap}"
        )
        assert gap < timedelta(days=1), (
            f"Learning-phase failure must retry the same day, not tomorrow. Got {gap}"
        )

    def test_failure_delay_is_not_clamped_to_one_day(self):
        """Regression: adjust_delay_for_learning_item used to clamp EVERY delay
        to >= 1 day, destroying same-day retry spacing."""
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        state = _make_state(item, now=now, interval_days=1, repetition_count=2, lapse_count=5)
        db = _MockDB([item, None, None, None, state])

        result = _run_failure(db, item, state, user_id)
        # lapse_count becomes 6 (>= 3) -> 2h stuck-loop escape, well under 1 day.
        gap = result.memory_state.next_review_at - result.memory_state.last_reviewed_at
        assert gap < timedelta(days=1), f"Failure delay must not be clamped to 1 day, got {gap}"


# --- P8: mastery recency gate ----------------------------------------------------
def _mastered_namespace(**overrides):
    base = dict(
        word="early",
        memory_strength=0.85,
        forget_risk=0.10,
        priority_score=0.20,
        consecutive_correct_count=5,
        consecutive_error_count=0,
        recall_correct_count=10,
        hinted_correct_count=2,
        preview_correct_count=2,
        context_correct_count=1,
        hidden_recall_correct_count=1,
        no_hint_correct_date_count=5,
        last_answer_seen_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDeriveWordStatusRecencyGate:
    def test_no_recency_data_keeps_legacy_behavior(self):
        assert derive_word_status(_mastered_namespace()) == "mastered"

    def test_high_recent_accuracy_stays_mastered(self):
        assert derive_word_status(_mastered_namespace(), recent_accuracy=0.9) == "mastered"

    def test_chronic_failure_demoted_despite_cumulative_gates(self):
        """'early': 152 lifetime lapses, 33% accuracy over the last 10 reviews —
        cumulative gates all pass, but it is NOT mastered."""
        status = derive_word_status(_mastered_namespace(), recent_accuracy=0.33)
        assert status != "mastered"
        assert status != "near_mastered"

    def test_borderline_recency_blocks_mastered_but_allows_demotion_path(self):
        # 0.72 >= DEMOTION (0.60) but < MASTERED_MIN (0.75): not mastered,
        # not force-demoted either — falls through to the streak/type checks.
        status = derive_word_status(_mastered_namespace(), recent_accuracy=0.72)
        assert status != "mastered"

    def test_near_mastered_recency_threshold(self):
        state = _mastered_namespace(
            memory_strength=0.73, recall_correct_count=2, no_hint_correct_date_count=2,
        )
        assert derive_word_status(state, recent_accuracy=0.7) == "near_mastered"
        assert derive_word_status(state, recent_accuracy=0.5) != "near_mastered"


class _ScalarsAllMockDB(_MockDB):
    def __init__(self, rows):
        super().__init__([])
        self._rows = rows

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: list(self._rows))


class TestGetRecentWordAccuracy:
    def test_ratio_over_window(self):
        db = _ScalarsAllMockDB([True, False, True, True])
        accuracy = get_recent_word_accuracy(db, uuid4(), uuid4())
        assert accuracy == 0.75

    def test_no_rows_returns_none(self):
        db = _ScalarsAllMockDB([])
        assert get_recent_word_accuracy(db, uuid4(), uuid4()) is None

    def test_no_item_returns_none(self):
        db = _ScalarsAllMockDB([True])
        assert get_recent_word_accuracy(db, uuid4(), None) is None


# --- P2: backlog smoothing -------------------------------------------------------
class TestSmoothOverdueBacklog:
    def _overdue_states(self, count, now):
        return [
            SimpleNamespace(
                learning_item_id=uuid4(),
                next_review_at=now - timedelta(days=2 + index),
            )
            for index in range(count)
        ]

    def test_overflow_is_pushed_to_future(self):
        now = datetime.now(UTC)
        states = self._overdue_states(95, now)  # budget 90 -> overflow 5
        db = _ScalarsAllMockDB(states)

        pushed = smooth_overdue_backlog(db, uuid4(), now)

        assert pushed == 5
        untouched = states[:90]
        overflow = states[90:]
        assert all(s.next_review_at < now for s in untouched), (
            "Highest-priority (oldest-due) items must keep their slot"
        )
        assert all(s.next_review_at > now for s in overflow), (
            "Overflow items must be pushed into the future"
        )
        # Spread over +1..+3 days, round-robin.
        days = sorted(round((s.next_review_at - now).total_seconds() / 86400) for s in overflow)
        assert set(days) <= {1, 2, 3}

    def test_within_budget_is_noop(self):
        now = datetime.now(UTC)
        states = self._overdue_states(10, now)
        db = _ScalarsAllMockDB(states)
        assert smooth_overdue_backlog(db, uuid4(), now) == 0
        assert all(s.next_review_at < now for s in states)
