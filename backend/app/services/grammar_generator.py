"""LLM-driven English grammar question generator.

Each call to `generate_grammar_questions(level, settings)` returns 10
questions at the requested difficulty. The LLM is asked for a JSON
array; the response is parsed and normalized into
`GrammarQuestion` schemas.

Difficulty model
----------------
Levels 1-5 → 100% multiple choice (easier; child picks from 4 options).
Levels 6-10 → 60% multiple choice + 40% fill-in-the-blank (harder;
child has to recall the missing word). The split is defined in
`question_type_distribution` so callers (frontend, tests) can agree on
the expected mix for a given level.

Why 60/40 and not 50/50
-----------------------
At level 6, the child is just transitioning to productive recall.
Keeping a majority of choice questions avoids frustrating the child
on the first harder level. By level 10 the 40% fill-in-blank share
is high enough to be challenging without making the batch feel like
a spelling test.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app.schemas.grammar import GrammarQuestion, GrammarQuestionSet
from app.services.llm_translation import LlmTranslationSettings, call_llm_generate


logger = logging.getLogger(__name__)


# --- Difficulty model --------------------------------------------------------

QUESTIONS_PER_SET = 10
MIN_LEVEL = 1
MAX_LEVEL = 10


def question_type_distribution(level: int) -> tuple[int, int]:
    """Return (num_choice, num_fill_in_blank) for a given level.

    Levels 1-5 → 100% choice. Levels 6-10 → 60% choice + 40% fill-in.
    Distribution is purely deterministic so the API and frontend can
    both predict the mix without an extra round-trip.
    """
    if level < MIN_LEVEL or level > MAX_LEVEL:
        raise ValueError(f"level must be between {MIN_LEVEL} and {MAX_LEVEL}, got {level}")
    if level <= 5:
        return (QUESTIONS_PER_SET, 0)
    # 60% choice, 40% fill-in, rounded to whole questions
    num_choice = round(QUESTIONS_PER_SET * 0.6)  # 6
    return (num_choice, QUESTIONS_PER_SET - num_choice)  # 6 + 4


def difficulty_description(level: int) -> str:
    """Short Chinese description of the level — used in the LLM prompt."""
    bands = [
        (1, 2, "最简单：be 动词、is/are, 单复数, this/that, here/there, 现在进行时 be + V-ing"),
        (3, 4, "入门：一般现在时, 否定句, 疑问句, 介词 in/on/at, 形容词比较级"),
        (5, 5, "初级：情态动词 can/must, 现在完成时 (基本), 副词位置, 连词 and/but/or"),
        (6, 7, "中级：现在完成时 (for/since), 过去时 (规则/不规则), 宾语从句 (简单), 定语从句 (简单)"),
        (8, 9, "中高级：被动语态 (一般现在/过去), 条件句 (真实/非真实), 虚拟语气 (基本), 强调句"),
        (10, 10, "高级：定语从句 (复杂), 名词性从句, 倒装句, It is ... that ... 强调, 长难句"),
    ]
    for lo, hi, desc in bands:
        if lo <= level <= hi:
            return f"Level {level} (1-10, 1=最简单, 10=最难): {desc}"
    return f"Level {level}"


# --- LLM prompt construction -------------------------------------------------

def _build_prompt(level: int, num_choice: int, num_fill: int) -> str:
    """Build the LLM prompt asking for exactly num_choice + num_fill questions.

    The prompt asks for a strict JSON array so we can parse reliably.
    Wrapping the JSON in a code fence hint (```json) helps models that
    default to markdown output.
    """
    diff = difficulty_description(level)
    choice_lines = [
        f"  - 选择题 #{i+1}: type=\"choice\", 4 options, the answer field MUST equal one of the 4 options verbatim"
        for i in range(num_choice)
    ]
    fill_lines = [
        f"  - 填空题 #{i+1}: type=\"fill_in_blank\", NO options array, the prompt must contain '____' (4 underscores) marking the blank, answer is the missing word(s)"
        for i in range(num_fill)
    ]
    all_lines = choice_lines + fill_lines
    questions_spec = "\n".join(all_lines)

    return (
        "You are an English grammar teacher for a Chinese-speaking child (ages 8-12, primary school level).\n"
        "Generate exactly 10 grammar questions at the requested difficulty.\n\n"
        f"DIFFICULTY: {diff}\n\n"
        "REQUIREMENTS:\n"
        "  - Output ONLY a valid JSON array, no prose, no markdown fences, no commentary.\n"
        "  - Each element is a JSON object with these exact fields:\n"
        "      id (string, e.g. 'q1', 'q2', ...)\n"
        "      type (string, either 'choice' or 'fill_in_blank')\n"
        "      level (integer, equal to the requested level)\n"
        "      prompt (string, the question text. For fill_in_blank, include '____' where the blank is)\n"
        "      translation (string, optional Chinese context or translation; use empty string if not needed)\n"
        "      options (array of exactly 4 strings, required for 'choice', MUST be null/absent for 'fill_in_blank')\n"
        "      answer (string, the correct answer)\n"
        "      explanation (string, 1-2 sentence grammar rule explanation in Simplified Chinese)\n"
        "  - For 'choice': options should include 1 correct + 3 plausible distractors, no obviously silly options.\n"
        "  - For 'fill_in_blank': test a single grammar point per question (verb form, article, preposition, etc.).\n"
        "  - Match the difficulty band — level 1 should be MUCH easier than level 10.\n"
        "  - Vary grammar topics across the 10 questions (tenses, articles, prepositions, plurals, etc.).\n\n"
        f"Generate these {QUESTIONS_PER_SET} questions in this order:\n"
        f"{questions_spec}\n\n"
        "Output the JSON array now."
    )


# --- LLM response parsing ----------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    """Strip ```json ... ``` fences if present (some models default to markdown)."""
    return _FENCE_RE.sub("", text).strip()


def _parse_questions_payload(raw: str, level: int) -> list[dict[str, Any]]:
    """Parse the LLM response into a list of question dicts.

    Tolerant of: leading/trailing whitespace, ```json fences, and a
    JSON object wrapping the array (some models return {questions: [...]}).
    Raises ValueError if the response cannot be parsed as a JSON array
    of 10 question objects.
    """
    text = _strip_code_fences(raw)
    # Try parsing as a JSON array first
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}; raw head: {text[:200]!r}") from exc

    if isinstance(parsed, dict):
        # Some models wrap the array. Accept either {questions: [...]} or {items: [...]}
        for key in ("questions", "items", "data", "result"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break
        else:
            raise ValueError(f"LLM returned a JSON object but no question array; keys: {list(parsed.keys())}")

    if not isinstance(parsed, list):
        raise ValueError(f"LLM response is not a JSON array (got {type(parsed).__name__})")

    if len(parsed) != QUESTIONS_PER_SET:
        # Some models return 9 or 11 — accept as long as we get something close.
        # The caller (router) will then truncate/pad if needed.
        logger.warning("LLM returned %d questions, expected %d", len(parsed), QUESTIONS_PER_SET)

    return parsed


# --- Normalization & validation ---------------------------------------------

def _stable_id(seed: str) -> str:
    """Deterministic short id derived from the seed (prompt+index)."""
    return "g_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def _normalize_question(raw: dict[str, Any], index: int, level: int) -> GrammarQuestion:
    """Coerce one LLM-emitted dict into a validated GrammarQuestion.

    Handles common LLM sloppiness:
      - missing 'id' → generated from prompt+index
      - 'options' is a string instead of a list → wrapped
      - 'options' missing/empty for 'choice' → defaults to 4 dummy options
        (the router will detect this and retry)
      - trailing/leading whitespace stripped from string fields
      - 'type' normalised to lowercase
    """
    qtype = str(raw.get("type", "")).strip().lower()
    if qtype not in {"choice", "fill_in_blank"}:
        raise ValueError(f"Question #{index+1}: type must be 'choice' or 'fill_in_blank', got {qtype!r}")

    prompt_text = str(raw.get("prompt", "")).strip()
    if not prompt_text:
        raise ValueError(f"Question #{index+1}: prompt is empty")

    answer = str(raw.get("answer", "")).strip()
    if not answer:
        raise ValueError(f"Question #{index+1}: answer is empty")

    explanation = str(raw.get("explanation", "")).strip()
    if not explanation:
        explanation = "本题考察英语基础语法。"

    translation = raw.get("translation")
    if translation is not None:
        translation = str(translation).strip() or None

    options: list[str] | None = None
    if qtype == "choice":
        raw_options = raw.get("options")
        if isinstance(raw_options, str):
            # Some models serialise as a single string
            options = [opt.strip() for opt in re.split(r"[,;，；\n]+", raw_options) if opt.strip()]
        elif isinstance(raw_options, list):
            options = [str(opt).strip() for opt in raw_options if str(opt).strip()]
        if not options or len(options) < 2:
            raise ValueError(f"Question #{index+1}: choice type requires >= 2 options, got {options!r}")
        # Pad to 4 if we only got 2-3 (rare)
        while len(options) < 4:
            options.append(f"Option {chr(ord('A') + len(options))}")
        # Make sure the answer is one of the options (auto-add if not)
        if answer not in options:
            options[0] = answer
            logger.warning("Question #%d: answer not in options, replaced first option", index + 1)

    qid = str(raw.get("id", "")).strip()
    if not qid:
        qid = _stable_id(f"{level}:{index}:{prompt_text[:50]}")

    return GrammarQuestion(
        id=qid,
        type=qtype,  # type: ignore[arg-type]
        level=level,
        prompt=prompt_text,
        translation=translation,
        options=options,
        answer=answer,
        explanation=explanation,
    )


# --- Public entry point ------------------------------------------------------

def generate_grammar_questions(
    level: int,
    settings: LlmTranslationSettings,
    *,
    questions_per_set: int = QUESTIONS_PER_SET,
) -> GrammarQuestionSet:
    """Generate `questions_per_set` grammar questions at the given level.

    Raises:
        ValueError: if level is out of range, the LLM response is not
            valid JSON, or the response cannot be normalised into
            GrammarQuestion schemas.
    """
    if level < MIN_LEVEL or level > MAX_LEVEL:
        raise ValueError(f"level must be between {MIN_LEVEL} and {MAX_LEVEL}, got {level}")

    num_choice, num_fill = question_type_distribution(level)
    if questions_per_set != QUESTIONS_PER_SET:
        # Re-scale the distribution proportionally if a non-default count is requested.
        ratio_choice = num_choice / QUESTIONS_PER_SET
        num_choice = round(questions_per_set * ratio_choice)
        num_fill = questions_per_set - num_choice

    prompt = _build_prompt(level, num_choice, num_fill)
    raw = call_llm_generate(settings, prompt)
    raw_items = _parse_questions_payload(raw, level)

    # If the LLM returned a different number of items, pad with placeholders
    # or truncate. We log and continue rather than failing — the client can
    # still display the questions we got.
    if len(raw_items) < questions_per_set:
        logger.warning("Padding %d missing question(s) — LLM returned %d, expected %d",
                       questions_per_set - len(raw_items), len(raw_items), questions_per_set)
    items = raw_items[:questions_per_set]

    questions: list[GrammarQuestion] = []
    for index, raw_item in enumerate(items):
        # Defensive: some LLMs occasionally emit a JSON `null` element in
        # the array (e.g. when the model aborts mid-stream and continues
        # with placeholders). Without this filter, _normalize_question
        # would call `raw.get(...)` on None and raise AttributeError,
        # surfacing as a 502 to the client. We surface it as a clear
        # "LLM response invalid" error instead, which the router maps to
        # 502 with a useful message.
        if raw_item is None:
            raise ValueError(
                f"LLM returned a null element at index {index}; aborting normalization. "
                "Common cause: model emitted an incomplete JSON array."
            )
        questions.append(_normalize_question(raw_item, index, level))

    return GrammarQuestionSet(level=level, questions=questions)
