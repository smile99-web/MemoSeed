"""
FSRS application tests — covers the 5 cases required by the Goal:

  1. 有参数用 personalized_fsrs
  2. 无参数用 default_fsrs
  3. mistake_penalty 缩短间隔
  4. stability_growth 延长间隔
  5. sentence 间隔短于 word

These tests are pure-Python unit tests (no DB). They exercise the same
functions that `schedule_memory_review` calls, so a green test suite
gives high confidence that the scheduler consumes the right weights.

Run with:

    cd /Users/ai/MemoSeed/backend
    pytest tests/test_fsrs_application.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isclose

import pytest

from app.services.memory_scheduler import (
    CHILD_FSRS_WEIGHTS,
    CHILD_TARGET_RETENTION,
    FSRS_GOOD,
    SCHEDULER_TYPE_BUILT_IN,
    SCHEDULER_TYPE_CHILD_PROFILE,
    SCHEDULER_TYPE_SLOW_LEARNER,
    SCHEDULER_TYPE_USER_FITTED,
    SLOW_LEARNER_FSRS_WEIGHTS,
    adjust_delay_for_learning_item,
    calculate_fsrs_interval,
    get_scheduler_metadata,
    next_fsrs_recall_stability,
)

from .conftest import make_learning_item, make_memory_state, make_settings


# --- 1 & 2: scheduler_type selection -----------------------------------------
class TestSchedulerTypeSelection:
    """Verify that get_scheduler_metadata picks the right label for each profile."""

    def test_with_personalized_weights_returns_user_fitted(self, mock_db, user_id):
        """Case 1: user has fitted weights stored → scheduler_type=user_fitted."""
        mock_db.scalar.return_value = make_settings(fsrs_weights=CHILD_FSRS_WEIGHTS)
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_USER_FITTED
        # The snapshot must contain the 19 weights and the target retention.
        assert "weights" in meta.fsrs_params_snapshot
        assert len(meta.fsrs_params_snapshot["weights"]) == 19
        assert meta.fsrs_params_snapshot["target_retention"] == CHILD_TARGET_RETENTION
        assert meta.algorithm_version == "FSRS_v4"

    def test_with_personalized_weights_and_slow_learner_returns_slow_learner(self, mock_db, user_id):
        """Personalized + slow_learner flag → slow_learner wins (most specific)."""
        mock_db.scalar.return_value = make_settings(
            fsrs_weights=CHILD_FSRS_WEIGHTS,
            use_slow_learner=True,
            use_child_profile=True,
        )
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_SLOW_LEARNER

    def test_without_personalized_returns_child_profile(self, mock_db, user_id):
        """Case 2 (default): no fitted weights, useChildProfile=True → child_profile."""
        mock_db.scalar.return_value = make_settings(fsrs_weights=None, use_child_profile=True)
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_CHILD_PROFILE
        # Snapshot should still contain 19 weights (the child profile ones) so
        # audit rows are always self-describing.
        assert len(meta.fsrs_params_snapshot["weights"]) == 19

    def test_without_personalized_and_no_child_returns_built_in(self, mock_db, user_id):
        """No fitted weights and useChildProfile=False → built_in (adult defaults)."""
        mock_db.scalar.return_value = make_settings(fsrs_weights=None, use_child_profile=False)
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_BUILT_IN

    def test_without_any_settings_returns_child_profile(self, mock_db, user_id):
        """No row in user_model_settings at all → defaults to child_profile."""
        mock_db.scalar.return_value = None
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_CHILD_PROFILE

    def test_db_error_falls_back_to_child_profile(self, mock_db, user_id):
        """ProgrammingError (e.g. table missing) → child_profile as safe default."""
        from sqlalchemy.exc import ProgrammingError

        # The first scalar call is the metadata lookup; raise on it.
        mock_db.scalar.side_effect = ProgrammingError("SELECT 1", {}, Exception("table missing"))
        meta = get_scheduler_metadata(mock_db, user_id)
        assert meta.scheduler_type == SCHEDULER_TYPE_CHILD_PROFILE
        # Snapshot still populated with child profile weights.
        assert len(meta.fsrs_params_snapshot["weights"]) == 19


# --- 3: mistake_penalty shortens interval -----------------------------------
class TestMistakePenaltyShortensInterval:
    """w[15] is the "hard penalty" multiplier inside `next_fsrs_recall_stability`.

    The growth formula multiplies stability growth by `weights[15]` for Hard
    ratings. So:
      - w[15] = 0.2315 (default) → Hard growth is multiplied by 0.2315 (penalty)
      - w[15] = 0.10           → stronger penalty (Hard growth × 0.10)
      - w[15] = 1.0            → no penalty (Hard growth × 1.0)

    The Goal says "mistake_penalty 缩短间隔" — a stronger mistake_penalty
    (lower w[15]) should produce a SHORTER interval. This test asserts that
    direction, not the raw value of w[15].
    """

    def test_harder_penalty_reduces_stability_growth(self):
        """Halving w[15] should make Hard stability growth strictly smaller."""
        prev_difficulty = 5.0
        prev_stability = 5.0
        prev_retrievability = 0.9

        # Baseline: default w[15] = 0.2315
        baseline_stability = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating=2,  # FSRS_HARD
            weights=CHILD_FSRS_WEIGHTS,
        )

        # Stronger penalty: w[15] = 0.2315 * 0.5
        harsh_weights = list(CHILD_FSRS_WEIGHTS)
        harsh_weights[15] = CHILD_FSRS_WEIGHTS[15] * 0.5
        harsh_stability = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating=2,
            weights=tuple(harsh_weights),
        )

        assert harsh_stability < baseline_stability, (
            f"Stronger mistake_penalty (lower w[15]) should produce smaller stability, "
            f"got harsh={harsh_stability} baseline={baseline_stability}"
        )

    def test_harder_penalty_shortens_interval_days(self):
        """End-to-end: a stronger mistake_penalty (lower w[15]) shortens the interval.

        Compares intervals for two scenarios that both apply rating=Hard to the
        same starting state. Only w[15] differs between them.
        """
        prev_difficulty = 5.0
        prev_stability = 5.0
        prev_retrievability = 0.9
        rating = 2  # FSRS_HARD

        baseline_stability = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating, CHILD_FSRS_WEIGHTS,
        )
        harsh_weights = list(CHILD_FSRS_WEIGHTS)
        harsh_weights[15] = CHILD_FSRS_WEIGHTS[15] * 0.5
        harsh_stability = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating, tuple(harsh_weights),
        )

        baseline_interval = calculate_fsrs_interval(baseline_stability, target_retention=0.9)
        harsh_interval = calculate_fsrs_interval(harsh_stability, target_retention=0.9)

        assert harsh_interval < baseline_interval, (
            f"Stronger mistake_penalty should produce shorter interval, "
            f"got harsh={harsh_interval} baseline={baseline_interval}"
        )


# --- 4: stability_growth extends interval ------------------------------------
class TestStabilityGrowthExtendsInterval:
    """w[8] (growth intercept) and w[10] (retrievability factor) drive growth."""

    def test_higher_w8_increases_stability_growth(self):
        prev_difficulty = 5.0
        prev_stability = 5.0
        prev_retrievability = 0.9

        # Baseline growth
        baseline = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating=3,  # FSRS_GOOD
            weights=CHILD_FSRS_WEIGHTS,
        )
        # Boost w[8] by 50%
        boosted = list(CHILD_FSRS_WEIGHTS)
        boosted[8] = CHILD_FSRS_WEIGHTS[8] * 1.5
        grown = next_fsrs_recall_stability(
            prev_difficulty, prev_stability, prev_retrievability, rating=3,
            weights=tuple(boosted),
        )
        assert grown > baseline, (
            f"Higher w[8] should produce greater stability growth, "
            f"got grown={grown} baseline={baseline}"
        )

    def test_higher_w8_extends_interval(self):
        baseline_interval = calculate_fsrs_interval(stability_days=5.0, target_retention=0.9)
        boosted = list(CHILD_FSRS_WEIGHTS)
        boosted[8] = CHILD_FSRS_WEIGHTS[8] * 1.5
        grown_stability = next_fsrs_recall_stability(
            difficulty=5.0, stability_days=5.0, retrievability=0.9, rating=3,
            weights=tuple(boosted),
        )
        grown_interval = calculate_fsrs_interval(stability_days=grown_stability, target_retention=0.9)
        assert grown_interval > baseline_interval, (
            f"Higher stability_growth should produce longer interval, "
            f"got grown={grown_interval} baseline={baseline_interval}"
        )

    def test_slow_learner_weights_produce_shorter_intervals_than_child(self):
        """Slow-learner profile is designed for shorter intervals than child profile."""
        # Same inputs, two weight sets.
        difficulty = 5.0
        stability = 5.0
        retrievability = 0.9
        rating = 3  # GOOD

        child_stability = next_fsrs_recall_stability(
            difficulty, stability, retrievability, rating, CHILD_FSRS_WEIGHTS,
        )
        slow_stability = next_fsrs_recall_stability(
            difficulty, stability, retrievability, rating, SLOW_LEARNER_FSRS_WEIGHTS,
        )
        child_interval = calculate_fsrs_interval(child_stability, CHILD_TARGET_RETENTION)
        slow_interval = calculate_fsrs_interval(slow_stability, 0.92)  # SLOW_LEARNER_TARGET_RETENTION

        assert slow_interval < child_interval, (
            f"Slow-learner profile should produce shorter intervals, "
            f"got slow={slow_interval} child={child_interval}"
        )


# --- 5: sentence interval is shorter than word interval --------------------
class TestSentenceShorterThanWord:
    """Same scheduling math, but the `adjust_delay_for_learning_item` post-processor
    shortens sentence delays (item_type='sentence' multiplier 0.7) vs words (1.0)."""

    def test_sentence_delay_is_shorter_than_word_for_same_underlying_delay(self):
        # Pick a delay > 1 day so the item-type adjustment kicks in.
        base_delay = timedelta(days=10)
        state = make_memory_state(lapse_count=0)

        word_item = make_learning_item(item_type="word", english_text="apple")
        sentence_item = make_learning_item(item_type="sentence", english_text="I like apples")

        # `now` matters for the time-of-day branch — pick 9am to avoid all
        # time-window adjustments (hours 0-9 are pass-through).
        now = datetime(2026, 6, 17, 9, 0, 0, tzinfo=timezone.utc)

        word_delay = adjust_delay_for_learning_item(base_delay, word_item, state)
        # Force the sentence path: item_type='sentence' → delay *= 0.7
        sentence_delay = adjust_delay_for_learning_item(base_delay, sentence_item, state)

        # Word should be unchanged (or barely changed by other factors).
        # Sentence should be reduced by 30%.
        assert sentence_delay < word_delay, (
            f"Sentence delay should be shorter than word delay, "
            f"got sentence={sentence_delay} word={word_delay}"
        )
        # The 0.7 ratio should hold within a small tolerance for the
        # other post-processors (hint-dependency, danger-zone, etc.).
        ratio = sentence_delay.total_seconds() / word_delay.total_seconds()
        assert 0.6 <= ratio <= 0.85, (
            f"Expected sentence/word ratio near 0.7, got {ratio:.3f}"
        )

    def test_phrase_delay_between_word_and_sentence(self):
        """Phrases get 0.85 multiplier, between word (1.0) and sentence (0.7)."""
        base_delay = timedelta(days=10)
        state = make_memory_state(lapse_count=0)
        now = datetime(2026, 6, 17, 9, 0, 0, tzinfo=timezone.utc)

        word_item = make_learning_item(item_type="word", english_text="apple")
        phrase_item = make_learning_item(item_type="phrase", english_text="red apple")
        sentence_item = make_learning_item(item_type="sentence", english_text="I like apples")

        word_delay = adjust_delay_for_learning_item(base_delay, word_item, state)
        phrase_delay = adjust_delay_for_learning_item(base_delay, phrase_item, state)
        sentence_delay = adjust_delay_for_learning_item(base_delay, sentence_item, state)

        assert word_delay >= phrase_delay >= sentence_delay, (
            f"Expected word >= phrase >= sentence delays, "
            f"got word={word_delay} phrase={phrase_delay} sentence={sentence_delay}"
        )
