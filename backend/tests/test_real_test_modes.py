"""P15-P21 tests: assisted-phase telemetry split + real-test mastery gates.

Background
----------
The 72-hour efficiency analysis found that 63% of all review_logs came from
*assisted* phases (word-preview / word-hinted / word-missing_letter /
word-hidden_recall) — phases where the answer is shown before the child
responds, so they can never fail. Their fake 100% correct rate fed FSRS,
inflated every accuracy metric, and let words "graduate" without ever being
tested.

P15: assisted phases are telemetry-only (no review_log / FSRS mutation).
P16: accuracy metrics count REAL tests only.
P21: mastery requires proof — enough real tests, decent accuracy, and correct
     answers on at least 2 distinct days (spaced proof), not same-day cram.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.memory_scheduler import ASSISTED_REVIEW_MODES
from app.services.word_memory import derive_word_status, get_recent_word_test_stats


# --- Helpers -----------------------------------------------------------------
def _ws(**over):
    """WordMemoryState-shaped mock with consolidating-ish defaults."""
    base = dict(
        memory_strength=0.7,
        forget_risk=0.3,
        consecutive_correct_count=2,
        consecutive_error_count=0,
        recall_correct_count=1,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
        no_hint_correct_date_count=0,
        last_answer_seen_at=None,
        priority_score=0.3,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """db.execute(stmt).all() -> preset rows (statement content ignored)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return _FakeResult(self._rows)


# --- P15: mode classification --------------------------------------------------
def test_assisted_mode_set_is_exact():
    assert set(ASSISTED_REVIEW_MODES) == {
        "word-preview",
        "word-hinted",
        "word-missing_letter",
        "word-hidden_recall",
    }


def test_real_test_modes_are_not_assisted():
    for real in (
        "word-recall",
        "word-context",
        "word-spelling",
        "word-listen_choose_chinese",
        "word-english_to_chinese",
        "word-match_translation",
        "word-chinese_to_english",
        "word-listen_spell",
        "sentence-spelling",
    ):
        assert real not in ASSISTED_REVIEW_MODES


# --- P16: real-test stats ------------------------------------------------------
def test_recent_test_stats_counts_distinct_correct_days():
    now = datetime.now(timezone.utc)
    rows = [
        (True, now),                              # today
        (True, now - timedelta(hours=2)),         # same local day
        (True, now - timedelta(days=1)),          # second day
        (False, now - timedelta(days=2)),         # wrong — not a correct day
    ]
    stats = get_recent_word_test_stats(_FakeDb(rows), "user", "item")
    assert stats is not None
    accuracy, correct_days, test_count = stats
    assert test_count == 4
    assert accuracy == 0.75
    assert correct_days == 2


def test_recent_test_stats_none_without_data():
    assert get_recent_word_test_stats(_FakeDb([]), "user", "item") is None
    assert get_recent_word_test_stats(_FakeDb([]), "user", None) is None


# --- P21: graduation gates -----------------------------------------------------
def test_mastered_requires_spaced_proof_not_same_day_cram():
    ws = _ws(memory_strength=0.65)
    # 6/6 correct but ALL on one day — a cram session must not graduate.
    assert derive_word_status(ws, 1.0, 1, 6) != "mastered"
    # Same record spread over 2 distinct days -> mastered.
    assert derive_word_status(ws, 0.8, 2, 5) == "mastered"


def test_mastered_exact_minima_and_shortfalls():
    assert derive_word_status(_ws(memory_strength=0.6), 0.7, 2, 5) == "mastered"
    assert derive_word_status(_ws(memory_strength=0.65), 0.7, 2, 4) != "mastered"   # too few tests
    assert derive_word_status(_ws(memory_strength=0.65), 0.69, 3, 9) != "mastered"  # accuracy short
    assert derive_word_status(_ws(memory_strength=0.59), 1.0, 3, 9) != "mastered"   # strength short
    # A 2-error streak blocks graduation even with great aggregates.
    assert derive_word_status(_ws(memory_strength=0.65, consecutive_error_count=2), 0.9, 3, 6) != "mastered"


def test_near_mastered_gates():
    assert derive_word_status(_ws(memory_strength=0.56), 0.6, 1, 3) == "near_mastered"
    assert derive_word_status(_ws(memory_strength=0.56), 0.59, 2, 9) != "near_mastered"  # accuracy short
    assert derive_word_status(_ws(memory_strength=0.54), 0.9, 2, 9) != "near_mastered"   # strength short
    assert derive_word_status(_ws(memory_strength=0.56), 0.7, 1, 2) != "near_mastered"   # too few tests


def test_recency_demotion_blocks_both_tiers():
    ws = _ws(memory_strength=0.9, recall_correct_count=9, no_hint_correct_date_count=9)
    # accuracy below the demotion line -> neither mastered nor near_mastered.
    assert derive_word_status(ws, 0.59, 4, 10) not in ("mastered", "near_mastered")


def test_legacy_path_unchanged_when_stats_absent():
    # recent_test_count=None -> the pre-P21 cumulative path (also pinned by
    # tests/test_mastery_status.py which calls derive_word_status(state)).
    ws = _ws(memory_strength=0.8, recall_correct_count=5, no_hint_correct_date_count=3)
    assert derive_word_status(ws) == "mastered"
    ws_near = _ws(memory_strength=0.73, recall_correct_count=2, no_hint_correct_date_count=2)
    assert derive_word_status(ws_near) == "near_mastered"
    # Strength gates still hold when stats ARE present.
    assert derive_word_status(_ws(memory_strength=0.4), 1.0, 2, 9) != "mastered"
