"""
Regression tests for the "mastered count drops as child learns more" bug.

Background
----------
The dashboard's `mastered` count went DOWN as the child did more reviews.
Root cause: the mastery threshold used `consecutive_correct_count >= N`,
which is reset to 0 on every error (memory_scheduler.update_memory_counters).
A single natural slip demoted a mastered word back to near_mastered /
teaching, and the more reviews the child did, the more opportunities
existed for a slip to occur.

Fix
---
The mastery threshold now uses *cumulative* `recall_correct_count` (which
does not reset on error) and tolerates a single recent error. The tests
below pin both behaviors: a word that legitimately achieved mastery must
stay mastered across a single natural slip.

See memory_dashboard.py:summarize_word and word_memory.py:derive_word_status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.memory_dashboard import (
    MASTERY_STATUS_LABELS,
    WordStats,
    summarize_word,
)
from app.services.word_memory import derive_word_status


# --- Helpers -----------------------------------------------------------------
def _now():
    return datetime(2026, 6, 17, 9, 0, 0, tzinfo=timezone.utc)


def make_mastered_word_stats(*, consecutive_correct_count=5, consecutive_error_count=0, recall_correct_count=5, no_hint_correct_date_count=3) -> WordStats:
    """Build a WordStats that meets all 4 original dashboard mastery conditions:
    strength >= 0.80, recall_correct_count >= 3, no_hint_correct_date_count >= 3,
    consecutive_correct_count >= 5.

    Strength and risk values are set so priority_score stays well below 0.78
    (the "difficult" threshold).
    """
    return WordStats(
        word="apple",
        strengths=[0.85, 0.85, 0.85],
        risks=[0.10, 0.10, 0.10],
        intervals=[10, 14, 20],
        next_reviews=[],
        direct_strengths=[0.85],
        direct_risks=[0.10],
        direct_intervals=[10],
        direct_next_reviews=[],
        review_count=12,
        mistake_count=0,
        recall_correct_count=recall_correct_count,
        hinted_correct_count=2,
        preview_correct_count=2,
        context_correct_count=1,
        hidden_recall_correct_count=1,
        no_hint_correct_date_count=no_hint_correct_date_count,
        consecutive_correct_count=consecutive_correct_count,
        consecutive_error_count=consecutive_error_count,
        last_reviewed_at=_now(),
        error_type_counts={},
    )


def make_mastered_word_memory_state(*, consecutive_correct_count=3, consecutive_error_count=0, recall_correct_count=5, no_hint_correct_date_count=3) -> SimpleNamespace:
    """Build a WordMemoryState-shaped mock that meets the original word_memory
    mastery conditions: memory_strength >= 0.82, recall_correct_count >= 3,
    no_hint_correct_date_count >= 3, consecutive_correct_count >= 3,
    consecutive_error_count == 0.
    """
    return SimpleNamespace(
        word="apple",
        memory_strength=0.85,
        forget_risk=0.10,
        priority_score=0.20,  # well below 0.78 difficult threshold
        consecutive_correct_count=consecutive_correct_count,
        consecutive_error_count=consecutive_error_count,
        recall_correct_count=recall_correct_count,
        hinted_correct_count=2,
        preview_correct_count=2,
        context_correct_count=1,
        hidden_recall_correct_count=1,
        no_hint_correct_date_count=no_hint_correct_date_count,
        last_answer_seen_at=None,
    )


# --- Dashboard: memory_dashboard.summarize_word ------------------------------
class TestDashboardMasteryIsSticky:
    """summarize_word must keep status='mastered' across a single natural slip."""

    def test_baseline_mastered_status(self):
        """Sanity: a word with all 4 original conditions is mastered."""
        stats = make_mastered_word_stats()
        summary = summarize_word(stats, _now())
        assert summary.status == "mastered", (
            f"Word meeting all 4 original conditions should be mastered, got {summary.status}"
        )

    def test_single_slip_does_not_demote_dashboard_mastered(self):
        """REGRESSION: a single error after mastering must NOT demote.

        Simulates the child getting 5+ correct reviews in a row
        (consecutive_correct_count was 5, recall_correct_count >= 5, etc.)
        and then making ONE mistake. The bug reset consecutive_correct_count
        to 0, which failed the `>= 5` threshold and dropped the word to
        near_mastered or worse. With the fix, the cumulative
        `recall_correct_count >= 5` check holds, so the word stays mastered.
        """
        stats = make_mastered_word_stats(
            # The "before" state: 5 consecutive correct, 5 cumulative recall.
            consecutive_correct_count=5,
            consecutive_error_count=0,
        )
        # Sanity: confirmed mastered before the slip.
        assert summarize_word(stats, _now()).status == "mastered"

        # Now simulate one error: counter resets to 0, errors go to 1.
        stats.consecutive_correct_count = 0
        stats.consecutive_error_count = 1

        summary = summarize_word(stats, _now())
        assert summary.status == "mastered", (
            f"Mastered word demoted after a single slip — bug regression. "
            f"Expected 'mastered', got {summary.status!r} "
            f"(consecutive_correct_count={stats.consecutive_correct_count}, "
            f"consecutive_error_count={stats.consecutive_error_count}, "
            f"recall_correct_count={stats.recall_correct_count})"
        )

    def test_persistent_error_streak_eventually_demotes(self):
        """Guard against over-correction: a real error streak must still demote.

        consecutive_error_count >= 3 (the 'difficult' threshold) should pull
        the word out of mastered regardless of cumulative recall.
        """
        stats = make_mastered_word_stats(
            recall_correct_count=10,  # lots of cumulative evidence
            consecutive_correct_count=0,
            consecutive_error_count=3,  # 3 in a row → "difficult"
        )
        summary = summarize_word(stats, _now())
        assert summary.status == "difficult", (
            f"Word with 3-error streak should be 'difficult', got {summary.status!r}"
        )


# --- word_memory.derive_word_status ------------------------------------------
class TestWordMemoryMasteryIsSticky:
    """derive_word_status must keep status='mastered' across a single natural slip."""

    def test_baseline_mastered_status(self):
        state = make_mastered_word_memory_state()
        assert derive_word_status(state) == "mastered"

    def test_single_slip_does_not_demote_word_memory_mastered(self):
        """REGRESSION: same bug as dashboard, different code path.

        The original condition was:
          consecutive_correct_count >= 3 AND consecutive_error_count == 0
        Both are reset-on-error: any mistake kills the mastered status.
        With the fix, we require cumulative `recall_correct_count >= 5` and
        tolerate a single recent error (`consecutive_error_count <= 1`).
        """
        state = make_mastered_word_memory_state(
            consecutive_correct_count=3,
            consecutive_error_count=0,
        )
        assert derive_word_status(state) == "mastered"

        # Simulate one error
        state.consecutive_correct_count = 0
        state.consecutive_error_count = 1

        assert derive_word_status(state) == "mastered", (
            f"Word-memory 'mastered' demoted after one slip — bug regression. "
            f"Got {derive_word_status(state)!r}"
        )

    def test_two_errors_in_a_row_demotes_word_memory(self):
        """Guard: persistent error streak must still demote, not be sticky forever."""
        state = make_mastered_word_memory_state(
            recall_correct_count=10,  # lots of cumulative evidence
            consecutive_correct_count=0,
            consecutive_error_count=2,  # 2 in a row — beyond the tolerated 1
        )
        assert derive_word_status(state) != "mastered", (
            "Word with 2-error streak should not be 'mastered'"
        )
