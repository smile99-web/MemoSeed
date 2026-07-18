"""Tests for multi-meaning (polysemy) word translations.

M1/M2: the built-in dictionary must not contain duplicate keys — Python
dict literals silently keep only the LAST value, which used to drop common
meanings (e.g. 'like' kept 像 and lost 喜欢).

M5: sanitize_word_translation must KEEP meaning separators (；、，) so a
word can carry several common Chinese meanings, while still truncating at
sentence-ending punctuation.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.word_dictionary import BUILTIN_WORD_DICTIONARY
from app.services.word_translation_cache import sanitize_word_translation

DICT_PATH = Path(__file__).resolve().parent.parent / "app" / "services" / "word_dictionary.py"
ENTRY_RE = re.compile(r'^\s*"([a-zA-Z\'\-]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$', re.M)


def test_builtin_dictionary_has_no_duplicate_keys():
    """Parse the source file (not the dict — Python silently drops dup keys)."""
    source = DICT_PATH.read_text(encoding="utf-8")
    keys = [match.group(1).lower() for match in ENTRY_RE.finditer(source)]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicate dictionary keys silently lose meanings: {duplicates}"


def test_builtin_dictionary_multi_meanings_present():
    """Spot-check the merged/expanded high-frequency polysemous words."""
    assert BUILTIN_WORD_DICTIONARY["like"] == "喜欢；像"
    assert BUILTIN_WORD_DICTIONARY["right"] == "正确的；右边；权利"
    assert BUILTIN_WORD_DICTIONARY["left"] == "左边；离开（过去式）"
    assert BUILTIN_WORD_DICTIONARY["can"] == "能；可以；罐头"
    assert BUILTIN_WORD_DICTIONARY["well"] == "好；嗯；井"


def test_sanitize_keeps_meaning_separators():
    assert sanitize_word_translation("喜欢；像", "like") == "喜欢；像"
    assert sanitize_word_translation("正确的；右边；权利", "right") == "正确的；右边；权利"


def test_sanitize_normalizes_separators_to_chinese_semicolon():
    assert sanitize_word_translation("喝，饮料", "drink") == "喝；饮料"
    assert sanitize_word_translation("灯、光", "light") == "灯；光"
    assert sanitize_word_translation("能;可以", "can") == "能；可以"
    # Spaces around separators are stripped per segment.
    assert sanitize_word_translation("喜欢； 像", "like") == "喜欢；像"


def test_sanitize_still_truncates_at_sentence_punctuation():
    assert sanitize_word_translation("苹果。一种水果", "apple") == "苹果"
    assert sanitize_word_translation("跑；经营！加油", "run") == "跑；经营"
    assert sanitize_word_translation("苹果\n一种水果", "apple") == "苹果"


def test_sanitize_caps_at_three_meanings():
    assert sanitize_word_translation("一；二；三；四；五", "x") == "一；二；三"


def test_sanitize_rejects_invalid():
    assert sanitize_word_translation("", "apple") == ""
    assert sanitize_word_translation("like", "like") == ""  # English-as-Chinese
    assert sanitize_word_translation("hello", "apple") == ""  # no Chinese at all
