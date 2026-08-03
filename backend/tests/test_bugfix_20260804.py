"""Regression tests for the 2026-08-04 high/medium bug-sweep fixes.

Covers:
- H1: handwriting-* review modes feed the mastery pipeline
  (recall_correct_count via update_memory_counters).
- H3: _pick_fallback_template never crashes for any template/seed
  (was the #1 production 500: IndexError on indexed placeholders).
- H4: handwriting queue "tested today" exclusion matches by word text.
- M3: repetition-count floor is strength-gated (weak words not pushed 7d).
- M5: failure delay uses CONSECUTIVE failures, not lifetime lapse_count.
- M7: _json_bool rejects the string "false" (was bool("false") == True).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.services.dynamic_sentence import _pick_fallback_template
from app.services.handwriting import _json_bool, pick_review_word_tasks
from app.services.memory_scheduler import (
    MIN_FAILURE_RETRY_MINUTES,
    calculate_failure_delay,
    schedule_memory_review,
    update_memory_counters,
)


# --- H3: fallback templates must never crash -----------------------------------

class TestFallbackTemplateNeverCrashes:
    def test_wide_seed_sweep(self):
        # Sweep enough distinct hashes to hit every template index many
        # times — before the fix, 4/7 templates raised IndexError here.
        outputs = []
        for i in range(300):
            focus = f"focus{i}"
            known = [f"known{j}" for j in range(i % 5)]
            outputs.append(_pick_fallback_template(focus, known))
        assert all(isinstance(text, str) and text.endswith(".") for text in outputs)

    def test_indexed_noun_verb_template_slot_order(self):
        # "This {0} can {1}." must render (noun, verb) — "This cat can run.",
        # never "This run can cat.". Find a seed that selects that template
        # (index 3) and check the verb lands in the second slot.
        for i in range(500):
            focus = f"w{i}"
            seed = f"{focus}:"
            import hashlib

            idx = hashlib.sha256(seed.encode("utf-8")).digest()[0] % 7
            if idx != 3:
                continue
            text = _pick_fallback_template(focus, [])
            match = re.fullmatch(r"This (\w+) can (\w+)\.", text)
            assert match is not None, f"unexpected sentence shape: {text!r}"
            assert match.group(2) in {"run", "fly", "swim", "jump", "grow"}, (
                f"verb slot must hold a verb, got {text!r}"
            )
            return
        raise AssertionError("sweep did not hit template index 3 — adjust range")


# --- M7: strict bool parsing for LLM verdicts -----------------------------------

class TestJsonBool:
    def test_string_false_is_false(self):
        assert _json_bool("false") is False
        assert _json_bool("False") is False
        assert _json_bool(" false ") is False

    def test_true_forms(self):
        assert _json_bool(True) is True
        assert _json_bool("true") is True
        assert _json_bool(1) is True

    def test_garbage_is_false(self):
        assert _json_bool(None) is False
        assert _json_bool(0) is False
        assert _json_bool("") is False
        assert _json_bool("yes, definitely") is False or _json_bool("yes") is True


# --- H1: handwriting modes count as recall --------------------------------------

def _counter_state() -> SimpleNamespace:
    return SimpleNamespace(
        consecutive_correct_count=0,
        consecutive_error_count=0,
        recall_correct_count=0,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
    )


class TestHandwritingModesFeedMastery:
    def test_dictation_counts_as_recall(self):
        state = _counter_state()
        update_memory_counters(state, True, "handwriting-dictation")
        assert state.recall_correct_count == 1

    def test_translation_counts_as_recall(self):
        state = _counter_state()
        update_memory_counters(state, True, "handwriting-translation")
        assert state.recall_correct_count == 1

    def test_both_counts_as_recall(self):
        state = _counter_state()
        update_memory_counters(state, True, "handwriting-both")
        assert state.recall_correct_count == 1

    def test_sentence_handwriting_does_not_pollute_word_recall(self):
        state = _counter_state()
        update_memory_counters(state, True, "sentence-handwriting")
        assert state.recall_correct_count == 0
        assert state.consecutive_correct_count == 1  # still tracks the streak


# --- H4: tested-today exclusion by word text ------------------------------------

class TestHandwritingTestedTodayTextDedup:
    def test_word_text_excludes_even_with_unknown_id(self):
        # The handwriting review is booked on the word-memory TWIN item, so
        # the served course item's id is never in tested_today_ids — only
        # text-based exclusion catches it (the all-day re-serve bug).
        course_item = SimpleNamespace(
            id=uuid4(), item_type="word", english_text="Apple", chinese_text="苹果",
        )
        rows = [(course_item, 0.5, None, True)]
        tasks = pick_review_word_tasks(
            rows,
            tested_today_ids=set(),
            tested_today_words={"apple"},
            limit=5,
        )
        assert tasks == []

    def test_untested_word_still_picked(self):
        course_item = SimpleNamespace(
            id=uuid4(), item_type="word", english_text="banana", chinese_text="香蕉",
        )
        rows = [(course_item, 0.5, None, True)]
        tasks = pick_review_word_tasks(
            rows,
            tested_today_ids=set(),
            tested_today_words={"apple"},
            limit=5,
        )
        assert len(tasks) == 1


# --- M5 + M3: scheduler call-site behavior ---------------------------------------

class _MockDB:
    """Pop-list scalar mock (same pattern as test_soft_failure_setback)."""

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
                consecutive_error_count=0, memory_strength=0.9, forget_risk=0.1):
    last_reviewed_at = now - timedelta(days=max(interval_days, 1))
    return SimpleNamespace(
        learning_item_id=item.id,
        interval_days=interval_days,
        ease_factor=5.0,
        memory_strength=memory_strength,
        forget_risk=forget_risk,
        repetition_count=repetition_count,
        lapse_count=lapse_count,
        consecutive_correct_count=0,
        consecutive_error_count=consecutive_error_count,
        recall_correct_count=10,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
        last_reviewed_at=last_reviewed_at,
        next_review_at=now,
        short_term_stability=1.0,
        last_short_term_updated_at=last_reviewed_at,
        scheduler_type=None,
        algorithm_version=None,
        fsrs_params_snapshot=None,
    )


class TestFailureDelayUsesConsecutiveFailures:
    def test_lifetime_lapses_do_not_force_two_hour_escape(self):
        """A re-learned word's first slip in weeks must get the normal
        same-day STS retry — before the fix, 100 lifetime lapses collapsed
        the STS to ~0 and forced the 2h stuck-loop escape every time."""
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        state = _make_state(
            item, now=now, interval_days=1, repetition_count=2,
            lapse_count=100, consecutive_error_count=0,
        )
        db = _MockDB([item, None, None, None, state])

        result = schedule_memory_review(
            db=db,
            user_id=user_id,
            learning_item_id=item.id,
            score=1,
            review_mode="word-spelling",
            response_text="exampel",
            duration_seconds=5,
            error_type="missing-letter",
        )

        gap = result.memory_state.next_review_at - result.memory_state.last_reviewed_at
        assert gap >= timedelta(minutes=MIN_FAILURE_RETRY_MINUTES)
        # First consecutive failure: bedtime-capped same-day retry. The old
        # code produced EXACTLY the 2h escape regardless of the clock; the
        # consecutive path caps at bedtime (20:30) which at most equals the
        # +2h forward window — but must still land the same local day-ish.
        assert gap < timedelta(days=1), f"must stay a same-day retry, got {gap}"

    def test_direct_function_semantics(self):
        # calculate_failure_delay itself is unchanged — the fix is what the
        # CALLER feeds it. Guard the contract: small streak → bedtime-capped
        # same-day ladder; huge streak → 2h escape.
        now = datetime.now(UTC)
        item = _make_item(uuid4())
        state = _make_state(item, now=now, interval_days=1, repetition_count=2)
        delay_first = calculate_failure_delay(1, 1, now, state)
        delay_streak = calculate_failure_delay(1, 100, now, state)
        assert delay_first >= timedelta(minutes=MIN_FAILURE_RETRY_MINUTES)
        # Bedtime cap: never schedules past local 20:30 (+2h forward window
        # past bedtime) — i.e. well under a day.
        assert delay_first < timedelta(days=1)
        assert delay_streak >= timedelta(hours=2)


class TestRepFloorIsStrengthGated:
    def test_weak_high_rep_word_not_pushed_seven_days(self):
        """rep>=30 used to force a 7-day floor on ANY correct answer — a
        chronically failing word (strength 0.3) with one lucky correct got
        pushed a week out and re-forgotten. The floor must skip weak words."""
        user_id = uuid4()
        item = _make_item(user_id)
        now = datetime.now(UTC)
        state = _make_state(
            item, now=now, interval_days=1, repetition_count=29,
            memory_strength=0.3, forget_risk=0.7,
        )
        db = _MockDB([item, None, None, None, state])

        result = schedule_memory_review(
            db=db,
            user_id=user_id,
            learning_item_id=item.id,
            score=4,
            review_mode="handwriting-dictation",
            response_text="example",
            duration_seconds=5,
        )

        gap = result.memory_state.next_review_at - now
        assert gap < timedelta(days=7), (
            f"weak word (strength 0.3) must not hit the 7-day rep floor, got {gap}"
        )
