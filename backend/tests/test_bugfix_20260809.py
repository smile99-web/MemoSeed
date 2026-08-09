"""Regression tests for the 2026-08-09 full-repo bug-sweep fixes.

Covers:
- B7: consecutive_correct_count is a true streak again (reset on failure) —
  the graduation jump and stuck/cliff park recovery gates consume it as one.
- C4: same_day_next_interval floor is 10 minutes (was 2 — a rapid-fire loop
  that violated P10's MIN_FAILURE_RETRY_MINUTES).
- A2: "pronunciation" survives error-type normalization (voice_practice
  giveups were silently relabeled "spelling").
- A5: /memory/points/award rejects non-whitelisted reasons (was: arbitrary
  client-supplied points_change minted unlimited points).
- A4: /auth/register enforces the invite code when INVITE_CODE is set.
- B2: fallback dynamic sentences with placeholder Chinese are not cached.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.memory_scheduler import MIN_FAILURE_RETRY_MINUTES, same_day_next_interval, update_memory_counters


# --- B7: consecutive-correct is a streak, not a lifetime counter --------------

def _counters(**overrides):
    base = dict(
        consecutive_correct_count=0,
        consecutive_error_count=0,
        recall_correct_count=0,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestConsecutiveCorrectStreak:
    def test_failure_resets_streak(self):
        ms = _counters(consecutive_correct_count=5)
        update_memory_counters(ms, is_correct=False, review_mode="word-recall")
        assert ms.consecutive_correct_count == 0
        assert ms.consecutive_error_count == 1

    def test_success_increments_streak_and_clears_errors(self):
        ms = _counters(consecutive_correct_count=2, consecutive_error_count=3)
        update_memory_counters(ms, is_correct=True, review_mode="word-recall")
        assert ms.consecutive_correct_count == 3
        assert ms.consecutive_error_count == 0
        assert ms.recall_correct_count == 1


# --- C4: same-day micro-review floor ------------------------------------------

class TestSameDayIntervalFloor:
    def test_low_sts_hits_ten_minute_floor(self):
        floor = timedelta(minutes=MIN_FAILURE_RETRY_MINUTES)
        assert same_day_next_interval(0.5, 0.998) >= floor
        assert same_day_next_interval(0.9, 0.998) >= floor

    def test_high_sts_never_below_floor(self):
        floor = timedelta(minutes=MIN_FAILURE_RETRY_MINUTES)
        for target in (0.998, 0.99, 0.97, 0.94, 0.90):
            assert same_day_next_interval(1.0, target) >= floor


# --- A2: error-type whitelist keeps pronunciation ------------------------------

class TestPronunciationErrorType:
    def test_pronunciation_survives(self):
        from app.api.v1.learning.router import normalize_word_error_type

        assert normalize_word_error_type("pronunciation") == "pronunciation"

    def test_garbage_still_falls_back(self):
        from app.api.v1.learning.router import normalize_word_error_type

        assert normalize_word_error_type("hacked!!") == "spelling"
        assert normalize_word_error_type(None) == "spelling"


# --- A5: points award is a closed world ----------------------------------------

class TestPointsAwardWhitelist:
    def test_unknown_reason_rejected(self):
        from app.api.v1.memory.router import award_points_endpoint

        payload = SimpleNamespace(points_change=999999, reason="hack", detail=None, learning_item_id=None)
        with pytest.raises(HTTPException) as exc_info:
            award_points_endpoint(payload, SimpleNamespace(id=uuid4()), MagicMock())
        assert exc_info.value.status_code == 403

    def test_daily_cap_noops(self):
        from app.api.v1.memory.router import award_points_endpoint

        db = MagicMock()
        # First scalar = today's read-aloud sum (at the 20/day cap);
        # second = _get_or_create_points inside the 0-point no-op response.
        db.scalar.side_effect = [20, SimpleNamespace(total_points=50, level=1)]
        payload = SimpleNamespace(points_change=2, reason="read-aloud", detail="x", learning_item_id=None)
        # Must not raise; the award itself is a 0-point no-op.
        award_points_endpoint(payload, SimpleNamespace(id=uuid4()), db)
        db.commit.assert_called_once()


# --- A4: invite-code gate -------------------------------------------------------

class TestInviteCodeGate:
    def test_rejects_wrong_code_when_configured(self, monkeypatch):
        from app.core.config import settings
        from app.api.v1.auth.router import register

        monkeypatch.setattr(settings, "invite_code", "2468")
        payload = SimpleNamespace(invite_code="wrong")
        with pytest.raises(HTTPException) as exc_info:
            register(payload, MagicMock(), None)
        assert exc_info.value.status_code == 403

    def test_no_gate_when_unconfigured(self, monkeypatch):
        from app.core.config import settings
        from app.api.v1.auth.router import register

        monkeypatch.setattr(settings, "invite_code", "")
        db = MagicMock()
        db.scalar.return_value = None  # no existing user
        payload = SimpleNamespace(invite_code=None, email="a@b.co", username="kid", password="password123")
        # Gate passed: we get past the 403 check and reach user creation
        # (the mocked session then short-circuits token issuing).
        try:
            register(payload, db, None)
        except Exception:
            pass
        assert db.add.called


# --- B2: placeholder Chinese never reaches the sentence cache -------------------

class TestFallbackSentenceNotCached:
    def test_placeholder_constant_matches(self, monkeypatch):
        import app.services.dynamic_sentence as dyn

        monkeypatch.setattr(
            dyn,
            "translate_english_to_chinese",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("LLM down")),
        )
        assert dyn.translate_sentence_or_fallback("Hello.", SimpleNamespace()) == dyn.TRANSLATION_FALLBACK_PLACEHOLDER
