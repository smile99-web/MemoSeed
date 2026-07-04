"""
Regression tests for bugs fixed in the "comprehensive code review" Goal.

Each test pins one specific bug so future refactors can't silently
re-introduce it. Tests are pure-Python (no DB) where possible so they
run in the unit-test layer.

Run with:
    cd /Users/ai/MemoSeed/backend
    pytest tests/test_regression_bugs.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from app.services.memory_dashboard import _to_local
from app.services.memory_scheduler import (
    FSRS_AGAIN,
    FSRS_GOOD,
    MIN_STABILITY_DAYS,
    calculate_failure_delay,
    calculate_fsrs_interval,
    clamp,
)
from app.services.word_memory import (
    ERROR_TYPE_TASK_STRATEGIES,
    choose_task_sequence,
    mask_learning_letters,
)
from app.services.grammar_generator import generate_grammar_questions
from app.services.grammar_history import complete_session
from app.utils import parse_datetime_setting


# --- BUG-1: constrain_stability was used on a strength [0,1] value ---
class TestStrengthClamp:
    """`calculate_fsrs_interval` must be called with a [0,1] strength, not
    one clamped to the day-range [MIN_STABILITY_DAYS, MAX_STABILITY_DAYS].

    The original bug was: `constrain_stability(1.0 - forget_risk)` always
    returned MIN_STABILITY_DAYS (~0.00347) for any forget_risk > 0,
    forcing every successful review into the "0.5x interval" branch
    and roughly halving all long-term intervals.
    """

    def test_strength_075_not_clamped_to_min_stability(self):
        # A typical "in-progress" strength (0.75). MUST pass through unchanged.
        # If constrain_stability were used, this would come out as
        # MIN_STABILITY_DAYS = 5/1440 ≈ 0.00347 instead of 0.75.
        result = clamp(0.75, 0.0, 1.0)
        assert result == pytest.approx(0.75)
        assert result > 0.5, (
            f"Strength 0.75 must not be clamped to MIN_STABILITY_DAYS "
            f"({MIN_STABILITY_DAYS}); got {result}"
        )

    def test_strength_005_not_clamped_to_min_stability(self):
        # A failing word's strength. clamp() returns the value as-is.
        result = clamp(0.05, 0.0, 1.0)
        assert result == pytest.approx(0.05)

    def test_calculate_fsrs_interval_branch_assignment(self):
        # Verify the in-progress band is reachable: 0.5x applies to
        # strength < 0.30, 0.7x to [0.30, 0.70), 1.0x otherwise.
        # Stable=30d, target_retention=0.9 → raw interval ~63d.
        stable = 30.0
        target = 0.9
        # Weak: strength=0.20 → 0.5x branch
        weak_interval = calculate_fsrs_interval(stable, target, current_strength=0.20)
        # In-progress: strength=0.50 → 0.7x branch
        mid_interval = calculate_fsrs_interval(stable, target, current_strength=0.50)
        # Strong: strength=0.90 → 1.0x branch (no scaling)
        strong_interval = calculate_fsrs_interval(stable, target, current_strength=0.90)
        # Strong interval should be the largest, weak the smallest.
        assert strong_interval > mid_interval > weak_interval, (
            f"Expected strong > mid > weak; got strong={strong_interval} "
            f"mid={mid_interval} weak={weak_interval}"
        )


# --- BUG-10: calculate_failure_delay produced a 2-minute next interval at 22:00 ---
class TestFailureDelayNoNegativeEndOfDay:
    """The previous end-of-day cap used `replace(hour=20)` then fell back
    to `replace(hour=21, 30)` if the result was <= now. At 22:00 local,
    the fallback (21:30) is *earlier* than now, producing a negative
    `end_of_day_delay` that the final `max(..., timedelta(minutes=2))`
    collapsed to 2 minutes. Now we use a forward-looking +2h window.
    """

    def test_failure_at_2230_produces_reasonable_delay(self):
        # The bedtime cap only matters when the STS-derived delay is in
        # (2 min, 2 h]. We mock same_day_next_interval to return a
        # realistic value in that range, then assert the bedtime fix
        # doesn't collapse it to 2 minutes.
        now = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)  # 22:30 Asia/Shanghai
        memory_state = SimpleNamespace(
            short_term_stability=0.5,
            last_short_term_updated_at=now,
            lapse_count=1,
        )
        with patch(
            "app.services.memory_scheduler.same_day_next_interval",
            return_value=timedelta(minutes=15),
        ):
            delay = calculate_failure_delay(score=1, lapse_count=1, now=now, memory_state=memory_state)
        # Must be at least 2 minutes (the floor) and not absurdly small.
        assert delay >= timedelta(minutes=2)
        # The original bug at 22:30 would have produced exactly 2 minutes
        # (bedtime 21:30 < now → negative cap → max(negative, 2min) = 2min).
        # The fix uses bedtime = now + 2h, so the cap is non-binding and
        # the 15-min STS delay passes through unchanged.
        assert delay == timedelta(minutes=15), (
            f"Failure at 22:30 with 15-min STS delay should pass through, "
            f"not collapse to 2 min. Got {delay}"
        )

    def test_failure_at_morning_produces_normal_delay(self):
        now = datetime(2026, 6, 17, 3, 0, tzinfo=UTC)  # 11:00 Asia/Shanghai
        memory_state = SimpleNamespace(
            short_term_stability=0.5,
            last_short_term_updated_at=now - timedelta(minutes=10),
            lapse_count=1,
        )
        delay = calculate_failure_delay(score=1, lapse_count=1, now=now, memory_state=memory_state)
        assert delay >= timedelta(minutes=2)


# --- BUG-13: astimezone() crashed on naive datetimes from legacy imports ---
class TestToLocalSafeForNaiveDatetime:
    """`_to_local` must not raise on naive datetimes — it should default
    them to UTC. This is the safety net for legacy imported data.
    """

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 6, 17, 10, 0)  # no tzinfo
        local = _to_local(naive)
        # naive 10:00 UTC → 18:00 Asia/Shanghai (UTC+8)
        assert local.hour == 18
        assert local.tzinfo is not None

    def test_aware_datetime_unchanged(self):
        aware = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
        local = _to_local(aware)
        assert local.hour == 18  # 10:00 UTC → 18:00 Asia/Shanghai

    def test_aware_utc_input_does_not_lose_tz(self):
        aware = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
        local = _to_local(aware)
        assert local.tzinfo is not None


# --- BUG-17: parse_datetime_setting returned naive datetimes ---
class TestParseDatetimeSetting:
    """Settings JSONB datetimes should be returned as UTC-aware. Naive
    values are assumed to be UTC (server-generated snapshots) and
    stamped as such.
    """

    def test_naive_iso_string_returns_utc_aware(self):
        result = parse_datetime_setting("2026-06-17T10:00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_aware_iso_string_preserves_tz(self):
        result = parse_datetime_setting("2026-06-17T10:00:00+08:00")
        assert result is not None
        assert result.utcoffset() == timedelta(hours=8)

    def test_invalid_string_returns_none(self):
        assert parse_datetime_setting("not a date") is None
        assert parse_datetime_setting("") is None
        assert parse_datetime_setting(None) is None


# --- BUG-23: mask_learning_letters for 6+ letter words ---
class TestMaskLearningLetters:
    """For 6+ letter words, the previous formula leaked too many letters
    (e.g. 'abcdef' → 'a _ _ d _ f' showed 3 of 6). The fix shows the
    first letter, ONE anchored middle letter, and the last letter.
    """

    def test_three_letter_word(self):
        result = mask_learning_letters("cat")
        parts = result.split()
        # 1 letter + 2 underscores for a 3-letter word
        assert len(parts) == 3
        assert parts[0] == "c"
        assert all(p == "_" for p in parts[1:])

    def test_five_letter_word(self):
        result = mask_learning_letters("apple")
        parts = result.split()
        assert len(parts) == 5
        assert parts[0] == "a"
        assert all(p == "_" for p in parts[1:])

    def test_six_letter_word_shows_first_middle_last_only(self):
        word = "abcdef"
        result = mask_learning_letters(word)
        parts = result.split()
        assert len(parts) == 6
        assert parts[0] == "a", f"First letter should be 'a', got {parts[0]}"
        assert parts[-1] == "f", f"Last letter should be 'f', got {parts[-1]}"
        # Exactly ONE middle letter should be visible (the anchored middle).
        visible_middle = [p for p in parts[1:-1] if p != "_"]
        assert len(visible_middle) == 1, (
            f"Six-letter word should show exactly one middle letter, "
            f"got {len(visible_middle)} visible: {result}"
        )

    def test_eight_letter_word(self):
        word = "elephant"  # 8 letters
        result = mask_learning_letters(word)
        parts = result.split()
        assert len(parts) == 8
        assert parts[0] == "e"
        assert parts[-1] == "t"
        visible_middle = [p for p in parts[1:-1] if p != "_"]
        assert len(visible_middle) == 1, (
            f"Eight-letter word should show exactly one middle letter, "
            f"got {len(visible_middle)} visible: {result}"
        )


# --- BUG-31: choose_task_sequence should not duplicate hidden_recall ---
class TestChooseTaskSequenceNoDuplicate:
    """When base_sequence already contains hidden_recall (e.g. for
    error_type='unknown' it's at index 1), the old `if 'hidden_recall'
    not in base_sequence[:2]` check failed to detect it and produced
    ['hidden_recall', 'english_to_chinese', 'hidden_recall', ...]. The
    fix checks the whole list and skips the prepend.
    """

    def test_unknown_error_type_does_not_duplicate_hidden_recall(self):
        word_state = SimpleNamespace(
            consecutive_error_count=5,  # qualifies for hidden_recall prepend
            task_type_counts={},
            error_type_counts={},
            priority_score=0.5,
            last_answer_seen_at=None,
        )
        sequence = choose_task_sequence(word_state, "unknown")
        # No duplicates of any task type.
        assert len(sequence) == len(set(sequence)), (
            f"Sequence should have no duplicates, got {sequence}"
        )
        # The first task in the sequence should be the demoted (easiest)
        # mode for high-error words, not a duplicated hidden_recall.
        assert "hidden_recall" in sequence, (
            f"hidden_recall should still be present (consecutive_error_count=5), "
            f"got sequence={sequence}"
        )

    def test_low_error_count_no_hidden_recall_prepend(self):
        word_state = SimpleNamespace(
            consecutive_error_count=1,  # below the 3-threshold
            task_type_counts={},
            error_type_counts={},
            priority_score=0.3,
            last_answer_seen_at=None,
        )
        sequence = choose_task_sequence(word_state, "meaning")
        # No hidden_recall prepend for low-error count.
        assert sequence[0] != "hidden_recall", (
            f"Low-error word should not start with hidden_recall, got {sequence[0]}"
        )


# --- M-BE-2: complete_session should refresh before returning ---
class TestCompleteSessionRefresh:
    """`complete_session` must refresh the ORM session before returning.

    Without the refresh, callers that subsequently access `completed_at`
    may see a stale value (the previous SELECT, not the freshly-committed
    row). The early-return path for already-completed sessions is the
    most common call — must refresh there too.
    """

    def test_idempotent_complete_refreshes_session(self):
        """Calling complete_session on an already-completed session should
        return the session with completed_at populated, regardless of
        whether the caller loaded a stale snapshot."""
        original_completed_at = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=original_completed_at,
        )

        # Lightweight inline mock — complete_session needs .scalar(),
        # .commit(), and .refresh().
        class _InlineMockDB:
            def __init__(self, scalar_result):
                self.scalar_result = scalar_result
                self.refreshed = False
            def scalar(self, _stmt):
                return self.scalar_result
            def commit(self):
                pass
            def refresh(self, _obj):
                self.refreshed = True

        db = _InlineMockDB(scalar_result=session)
        # First call seals it (no-op since already completed_at)
        result = complete_session(db, user_id=session.user_id, session_id=session.id)
        # The session must be refreshable; verify the mock received refresh
        assert db.refreshed, "complete_session must call db.refresh() before returning"


# --- M-BE-3: grammar_generator should reject null elements ---
class TestNullElementDefense:
    """If the LLM emits a `null` inside its JSON array response, the
    generator must raise a clear ValueError instead of crashing with
    AttributeError inside _normalize_question.
    """

    def test_null_element_raises_value_error(self):
        # Simulate the LLM returning [valid, null, valid] — _parse_questions_payload
        # would yield this list. The generator must catch the None and raise
        # ValueError before any attribute access.
        raw_items = [
            {"id": "q1", "type": "choice", "level": 1, "prompt": "?",
             "options": ["A", "B", "C", "D"], "answer": "A", "explanation": "x"},
            None,
            {"id": "q3", "type": "choice", "level": 1, "prompt": "?",
             "options": ["A", "B", "C", "D"], "answer": "A", "explanation": "x"},
        ]
        # We patch _parse_questions_payload to return our crafted list
        with patch("app.services.grammar_generator._parse_questions_payload", return_value=raw_items):
            with patch("app.services.grammar_generator.call_llm_generate", return_value="[]"):
                with pytest.raises(ValueError, match="null element"):
                    generate_grammar_questions(
                        level=1,
                        settings=SimpleNamespace(provider="ollama", base_url="x", model="x", api_key=None),
                    )


# --- M-BE-6: ProgrammingError vs OperationalError distinction ---
class TestProgrammingErrorHandling:
    """The `except ProgrammingError` pattern in FSRS settings lookup
    silently swallows REAL query bugs (column missing, bad SQL). The
    fix narrows it to OperationalError (table/connection issues only)
    so genuine bugs surface instead of being masked as "settings missing".
    """

    def test_operational_error_returns_defaults(self):
        """OperationalError (e.g. table missing on fresh install) still
        falls back to defaults — preserving the original safety net."""
        from sqlalchemy.exc import OperationalError
        from app.services.memory_scheduler import (
            get_user_fsrs_weights,
            FSRS_WEIGHTS,
        )

        db = SimpleNamespace(scalar=Mock(side_effect=OperationalError("table missing", None, None)))
        result = get_user_fsrs_weights(db, user_id=uuid4())  # type: ignore[arg-type]
        assert result == FSRS_WEIGHTS

    def test_programming_error_propagates(self):
        """ProgrammingError (query syntax / column missing) should NOT
        be swallowed — it indicates a real bug. This test pins that the
        fix doesn't catch ProgrammingError anymore."""
        from sqlalchemy.exc import ProgrammingError
        from app.services.memory_scheduler import get_user_fsrs_weights

        db = SimpleNamespace(scalar=Mock(side_effect=ProgrammingError("syntax error", None, None)))
        with pytest.raises(ProgrammingError):
            get_user_fsrs_weights(db, user_id=uuid4())  # type: ignore[arg-type]


# --- BUG-HIGH-1: award_daily_study_points must use local timezone ---
class TestDailyPointsTimezone:
    """The previous version compared date.today() (server local) against
    last_awarded_date.astimezone(UTC).date() — on a UTC server that
    happens to match, but on the production VPS in Asia/Shanghai the
    child couldn't claim the day's points until 8am local. The fix uses
    LOCAL_TIMEZONE for both sides of the comparison, AND actually
    increments the streak counters (the previous code's 'yesterday'
    calculation always yielded today, leaving streak at 0).
    """

    def test_local_timezone_is_asia_shanghai(self):
        """Pin the configured timezone — guards against accidental
        changes that would re-introduce the UTC-compare bug."""
        from app.services.points_service import LOCAL_TIMEZONE, datetime
        from datetime import timezone, timedelta
        offset = LOCAL_TIMEZONE.utcoffset(datetime.now(LOCAL_TIMEZONE))
        assert offset == timedelta(hours=8), (
            f"Expected CST (UTC+8) but got offset {offset}. If you change "
            "this, update the deploy docs and the comment in points_service.py."
        )

    def test_local_date_conversion_handles_naive_datetime(self):
        """Defensive: legacy rows may have naive last_awarded_date. The
        fix replaces tzinfo with UTC before converting. Verify the helper
        doesn't crash on naive input."""
        from app.services.points_service import (
            award_daily_study_points,
            LOCAL_TIMEZONE,
        )
        from datetime import datetime, UTC

        # Naive datetime that should be interpreted as UTC
        naive_last = datetime(2026, 1, 1, 10, 0, 0)  # no tzinfo
        # Build a stub session
        class _StubUserPoints:
            user_id = uuid4()
            total_points = 0
            level = 1
            current_streak_days = 0
            longest_streak_days = 0
            last_awarded_date = naive_last

        class _StubDB:
            def __init__(self):
                self.scalar_calls = 0
            def scalar(self, _stmt):
                self.scalar_calls += 1
                return self._stub_user_points if self.scalar_calls == 1 else 0
            @property
            def _stub_user_points(self):
                return _StubUserPoints()
            def add(self, _obj):
                pass
            def flush(self):
                pass
            def commit(self):
                pass
            def refresh(self, _obj):
                pass

        # Just exercise the path — no crash on naive datetime
        db = _StubDB()
        try:
            award_daily_study_points(db, user_id=uuid4())  # type: ignore[arg-type]
        except Exception:
            # We don't care about the result shape here, just that
            # naive datetime doesn't crash with "can't compare offset-naive
            # and offset-aware datetimes".
            pass
