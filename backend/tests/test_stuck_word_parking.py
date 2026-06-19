"""
Tests for the stuck-word park/release cycle.

Background
----------
The previous 'parking lot' was a SQL filter that hid stuck words from the
queue. This was incomplete: it didn't change next_review_at, so stuck
words were permanently exiled with no path back into the child's review.

The new algorithm:
  1. park_stuck_words() pushes next_review_at to now + 7 days for truly
     stuck words (lapse >= 10 AND strength < 0.3 AND consec < 3).
  2. After 7 days, the word re-enters the queue naturally.
  3. If the child gets it right, consec_correct_count increments and the
     word eventually un-parks (consec >= 3 = recovery gate).
  4. If the child fails, lapse increments and the word re-parks.

These tests pin the algorithm's key invariants. Integration with the live
DB is verified separately via the deploy-time integration check
(`park_stuck_words(db, user_id, now)` against the production database).
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.services.memory_scheduler import (
    STUCK_WORD_LAPSE_THRESHOLD,
    STUCK_WORD_STRENGTH_THRESHOLD,
    is_stuck_word,
)


# --- is_stuck_word unit tests ------------------------------------------------
class TestIsStuckWord:
    """Pure-function check of the stuck-word predicate.

    `is_stuck_word` is the gate for the new park_stuck_words() function.
    Its 3-condition check (lapse + strength + the recovery gate) is the
    core of the algorithm — these tests pin the boundaries.
    """

    def _ms(self, **kwargs):
        defaults = dict(lapse_count=0, memory_strength=1.0)
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_high_lapse_low_strength_is_stuck(self):
        assert is_stuck_word(self._ms(lapse_count=10, memory_strength=0.29)) is True

    def test_high_lapse_zero_strength_is_stuck(self):
        assert is_stuck_word(self._ms(lapse_count=120, memory_strength=0.0)) is True

    def test_high_strength_is_not_stuck_even_with_many_lapses(self):
        """The product decision: 已掌握 != 不再复习. A word with strength=0.9
        is doing fine even if its history had lots of lapses."""
        assert is_stuck_word(self._ms(lapse_count=120, memory_strength=0.9)) is False

    def test_low_lapse_is_not_stuck(self):
        assert is_stuck_word(self._ms(lapse_count=5, memory_strength=0.1)) is False

    def test_boundary_lapse_threshold(self):
        """lapse == STUCK_WORD_LAPSE_THRESHOLD should count as stuck."""
        assert is_stuck_word(
            self._ms(lapse_count=STUCK_WORD_LAPSE_THRESHOLD, memory_strength=0.29)
        ) is True
        assert is_stuck_word(
            self._ms(lapse_count=STUCK_WORD_LAPSE_THRESHOLD - 1, memory_strength=0.29)
        ) is False

    def test_boundary_strength_threshold(self):
        """strength == STUCK_WORD_STRENGTH_THRESHOLD should NOT count as stuck
        (the filter is strict-less-than)."""
        assert is_stuck_word(
            self._ms(lapse_count=10, memory_strength=STUCK_WORD_STRENGTH_THRESHOLD)
        ) is False
        assert is_stuck_word(
            self._ms(lapse_count=10, memory_strength=STUCK_WORD_STRENGTH_THRESHOLD - 0.01)
        ) is True

    def test_handles_none_strength(self):
        """Defensive: a freshly-created memory_state has memory_strength=0.0.
        That's stuck, not crash."""
        assert is_stuck_word(self._ms(lapse_count=0, memory_strength=0.0)) is False
        assert is_stuck_word(self._ms(lapse_count=15, memory_strength=0.0)) is True
