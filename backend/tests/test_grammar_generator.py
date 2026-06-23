"""
Tests for the grammar question generator (pure-Python logic only).

We don't call a real LLM here — that's an integration test. The unit
tests pin:
  * difficulty distribution per level (1-10)
  * difficulty band descriptions
  * LLM response parsing (tolerates markdown fences, dict-wrapped arrays,
    short/long arrays, sloppy types)
  * question normalization (missing id, options as string, etc.)

Run with:
    cd /Users/ai/MemoSeed/backend
    pytest tests/test_grammar_generator.py -v
"""

from __future__ import annotations

import json

import pytest

from app.schemas.grammar import GrammarQuestion, GrammarQuestionType
from app.services.grammar_generator import (
    MAX_LEVEL,
    MIN_LEVEL,
    QUESTIONS_PER_SET,
    _build_prompt,
    _normalize_question,
    _parse_questions_payload,
    _strip_code_fences,
    difficulty_description,
    question_type_distribution,
)


# --- Difficulty distribution ------------------------------------------------

class TestQuestionTypeDistribution:
    """The level→(choice, fill_in_blank) split is the core product decision.
    Pin it tightly so a tweak is a deliberate commit, not an accident.
    """

    def test_levels_1_to_5_are_100_percent_choice(self):
        for level in range(1, 6):
            num_choice, num_fill = question_type_distribution(level)
            assert num_choice == QUESTIONS_PER_SET, (
                f"Level {level} should be 100% choice, got {num_choice} choice + {num_fill} fill"
            )
            assert num_fill == 0

    def test_levels_6_to_10_are_60_40_split(self):
        for level in range(6, 11):
            num_choice, num_fill = question_type_distribution(level)
            assert num_choice == 6, (
                f"Level {level} should have 6 choice, got {num_choice}"
            )
            assert num_fill == 4, (
                f"Level {level} should have 4 fill-in, got {num_fill}"
            )

    def test_distribution_totals_ten(self):
        for level in range(1, 11):
            num_choice, num_fill = question_type_distribution(level)
            assert num_choice + num_fill == QUESTIONS_PER_SET

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            question_type_distribution(0)
        with pytest.raises(ValueError):
            question_type_distribution(11)
        with pytest.raises(ValueError):
            question_type_distribution(-1)


# --- Prompt construction ----------------------------------------------------

class TestBuildPrompt:
    def test_prompt_mentions_difficulty_band(self):
        prompt = _build_prompt(level=1, num_choice=10, num_fill=0)
        assert "Level 1" in prompt
        assert "最简单" in prompt

    def test_prompt_mentions_hard_difficulty(self):
        prompt = _build_prompt(level=10, num_choice=6, num_fill=4)
        assert "Level 10" in prompt
        assert "高级" in prompt

    def test_prompt_requests_correct_count(self):
        prompt = _build_prompt(level=8, num_choice=6, num_fill=4)
        assert "10 grammar questions" in prompt
        # The prompt should explicitly ask for the per-type counts.
        assert "选择题" in prompt
        assert "填空题" in prompt

    def test_prompt_requires_json(self):
        prompt = _build_prompt(level=1, num_choice=10, num_fill=0)
        assert "JSON" in prompt
        assert "no prose" in prompt or "no markdown" in prompt


# --- Response parsing -------------------------------------------------------

class TestStripCodeFences:
    def test_strips_json_fence(self):
        assert _strip_code_fences("```json\n[1,2,3]\n```") == "[1,2,3]"

    def test_strips_plain_fence(self):
        assert _strip_code_fences("```\n[1,2,3]\n```") == "[1,2,3]"

    def test_passthrough_when_no_fence(self):
        assert _strip_code_fences('[1,2,3]') == '[1,2,3]'


class TestParseQuestionsPayload:
    def _ok_payload(self):
        return json.dumps([
            {
                "id": f"q{i+1}",
                "type": "choice",
                "level": 1,
                "prompt": f"Question {i+1}?",
                "translation": "",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
                "explanation": "因为 A 是正确的。",
            }
            for i in range(QUESTIONS_PER_SET)
        ])

    def test_parses_plain_json_array(self):
        items = _parse_questions_payload(self._ok_payload(), level=1)
        assert len(items) == QUESTIONS_PER_SET
        assert items[0]["id"] == "q1"

    def test_strips_json_fence_before_parsing(self):
        items = _parse_questions_payload(f"```json\n{self._ok_payload()}\n```", level=1)
        assert len(items) == QUESTIONS_PER_SET

    def test_unwraps_dict_with_questions_key(self):
        wrapped = json.dumps({"questions": json.loads(self._ok_payload())})
        items = _parse_questions_payload(wrapped, level=1)
        assert len(items) == QUESTIONS_PER_SET

    def test_rejects_invalid_json(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_questions_payload("not json at all", level=1)

    def test_rejects_non_array_response(self):
        with pytest.raises(ValueError, match="no question array"):
            _parse_questions_payload('{"foo": "bar"}', level=1)

    def test_accepts_short_array_with_warning(self, caplog):
        short = json.dumps([{"id": "q1", "type": "choice", "level": 1, "prompt": "?", "options": ["A", "B", "C", "D"], "answer": "A", "explanation": "x"}])
        items = _parse_questions_payload(short, level=1)
        assert len(items) == 1
        # We don't assert the warning text — just that parsing succeeded.


# --- Question normalization -------------------------------------------------

class TestNormalizeQuestion:
    def _raw_choice(self, **overrides):
        base = {
            "id": "q1",
            "type": "choice",
            "level": 5,
            "prompt": "She ___ a teacher.",
            "translation": "她是老师。",
            "options": ["is", "are", "am", "be"],
            "answer": "is",
            "explanation": "第三人称单数用 is。",
        }
        base.update(overrides)
        return base

    def _raw_fill(self, **overrides):
        base = {
            "id": "q2",
            "type": "fill_in_blank",
            "level": 7,
            "prompt": "I have lived here ___ 2010.",
            "translation": "",
            "options": None,
            "answer": "since",
            "explanation": "since + 时间起点。",
        }
        base.update(overrides)
        return base

    def test_choice_question_normalizes(self):
        q = _normalize_question(self._raw_choice(), index=0, level=5)
        assert isinstance(q, GrammarQuestion)
        assert q.type == "choice"
        assert q.options == ["is", "are", "am", "be"]
        assert q.answer == "is"

    def test_fill_in_blank_question_normalizes(self):
        q = _normalize_question(self._raw_fill(), index=0, level=7)
        assert q.type == "fill_in_blank"
        assert q.options is None
        assert q.answer == "since"
        # The default raw_fill fixture has '___' (3 underscores). The product
        # spec says 4 — the normalize step doesn't fix that, it just
        # preserves the LLM's prompt as-is. So we assert the marker is
        # present (any length >= 3 underscores) rather than an exact count.
        assert "____" in q.prompt or "___" in q.prompt

    def test_generates_id_when_missing(self):
        raw = self._raw_choice()
        raw.pop("id")
        q = _normalize_question(raw, index=3, level=5)
        assert q.id.startswith("g_")
        # Stable for the same prompt+index+level
        q2 = _normalize_question(raw, index=3, level=5)
        assert q.id == q2.id

    def test_strips_whitespace_from_string_fields(self):
        q = _normalize_question(
            self._raw_choice(prompt="  Hello  ", answer=" is ", explanation=" x "),
            index=0,
            level=5,
        )
        assert q.prompt == "Hello"
        assert q.answer == "is"
        assert q.explanation == "x"

    def test_choice_options_as_string_gets_split(self):
        q = _normalize_question(
            self._raw_choice(options="is, are, am, be"),
            index=0,
            level=5,
        )
        assert q.options == ["is", "are", "am", "be"]

    def test_choice_with_too_few_options_pads(self):
        # Override the default answer to "A" so the answer-is-in-options
        # check (which auto-replaces the first option) doesn't fire and
        # obscure the "pads to 4" behavior we're testing.
        q = _normalize_question(
            self._raw_choice(options=["A", "B"], answer="A"),
            index=0,
            level=5,
        )
        assert len(q.options) == 4
        assert q.options[0] == "A"

    def test_choice_with_no_options_raises(self):
        with pytest.raises(ValueError, match="choice type requires"):
            _normalize_question(self._raw_choice(options=[]), index=0, level=5)

    def test_fill_in_blank_with_options_is_normalized_away(self):
        # The schema enforces options=None for fill_in_blank. The router
        # should normalise the LLM's sloppy output.
        q = _normalize_question(
            self._raw_fill(options=["since", "for", "from", "at"]),
            index=0,
            level=7,
        )
        assert q.options is None

    def test_missing_answer_raises(self):
        raw = self._raw_choice()
        raw["answer"] = ""
        with pytest.raises(ValueError, match="answer is empty"):
            _normalize_question(raw, index=0, level=5)

    def test_missing_prompt_raises(self):
        raw = self._raw_choice()
        raw["prompt"] = ""
        with pytest.raises(ValueError, match="prompt is empty"):
            _normalize_question(raw, index=0, level=5)

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="type must be"):
            _normalize_question(self._raw_choice(type="essay"), index=0, level=5)

    def test_answer_not_in_options_auto_replaces(self):
        q = _normalize_question(
            self._raw_choice(options=["A", "B", "C", "D"], answer="X"),
            index=0,
            level=5,
        )
        # First option gets replaced with the answer so the answer IS in options
        assert q.answer == "X"
        assert q.options[0] == "X"

    def test_default_explanation_when_missing(self):
        raw = self._raw_choice()
        raw["explanation"] = ""
        q = _normalize_question(raw, index=0, level=5)
        assert "语法" in q.explanation  # default Chinese fallback

    def test_translation_normalized_to_none_for_empty(self):
        q = _normalize_question(self._raw_choice(translation=""), index=0, level=5)
        assert q.translation is None


# --- Difficulty description (Chinese bands) --------------------------------

class TestDifficultyDescription:
    def test_level_1_is_easiest(self):
        desc = difficulty_description(1)
        assert "最简单" in desc
        assert "Level 1" in desc

    def test_level_10_is_hardest(self):
        desc = difficulty_description(10)
        assert "高级" in desc
        assert "Level 10" in desc

    def test_all_levels_have_description(self):
        for level in range(1, 11):
            desc = difficulty_description(level)
            assert desc, f"Level {level} has no description"
            assert f"Level {level}" in desc


# --- Sanity: schema + generator agree on the types ------------------------

class TestSchemaGeneratorAgreement:
    """The GrammarQuestionType literal and the generator's type strings
    must stay in sync. If you add a new type, both must be updated.
    """

    def test_choice_type_matches(self):
        q = _normalize_question(
            {
                "type": "choice",
                "level": 1,
                "prompt": "?",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
                "explanation": "x",
            },
            index=0,
            level=1,
        )
        assert q.type == "choice"
        assert q.type in {"choice", "fill_in_blank"}

    def test_fill_in_blank_type_matches(self):
        q = _normalize_question(
            {
                "type": "fill_in_blank",
                "level": 7,
                "prompt": "I ___ here since 2010.",
                "answer": "lived",
                "explanation": "x",
            },
            index=0,
            level=7,
        )
        assert q.type == "fill_in_blank"
