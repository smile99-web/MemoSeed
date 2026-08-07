"""
Tests for the yo-yo leech-word circuit breaker (park_leech_words).

Background
----------
park_stuck_words gates on CURRENT weakness (strength < 0.3) and
park_chronic_failure_words gates on CURRENT consecutive errors (>= 3).
Yo-yo leech words (learn -> forget -> relearn -> forget) recover just
enough between failures to evade both gates, so they cycle forever:
feel had lapse=88 with strength 0.81 and an active correct streak.

The leech breaker ignores current state entirely: lifetime lapse >= 30
means the word gets pushed 30 days out every time it comes due, capping
its appearance frequency at once a month.

These tests pin the predicate boundaries. The SQL-level park is verified
against the production DB at deploy time (same as park_stuck_words).
"""

from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.services.memory_scheduler import (
    LEECH_LAPSE_THRESHOLD,
    LEECH_RESCHEDULE_DAYS,
    is_leech_word,
)


class TestIsLeechWord:
    """is_leech_word: lifetime-lapse gate, blind to current strength/streak."""

    def test_threshold_is_30(self):
        assert LEECH_LAPSE_THRESHOLD == 30

    def test_reschedule_is_30_days(self):
        """The park duration must be long enough to actually relieve the
        child — 7 days (stuck-word park) proved useless for yo-yo words."""
        assert LEECH_RESCHEDULE_DAYS == 30

    def test_at_threshold_is_leech(self):
        assert is_leech_word(LEECH_LAPSE_THRESHOLD) is True

    def test_below_threshold_is_not_leech(self):
        assert is_leech_word(LEECH_LAPSE_THRESHOLD - 1) is False

    def test_extreme_lapse_is_leech(self):
        """feel: lapse=88 — the motivating production case."""
        assert is_leech_word(88) is True

    def test_zero_and_none_are_not_leech(self):
        assert is_leech_word(0) is False
        assert is_leech_word(None) is False

    def test_current_state_is_irrelevant(self):
        """The whole point: a word on a correct streak with high strength
        is STILL a leech if its lifetime lapse count is over the line.
        The predicate takes only lapse_count, so there is no way for
        strength/consecutive-correct to influence it — this test pins
        that signature contract."""
        assert is_leech_word(31) is True  # books: streaking AND leech
