"""Unit tests for Learning Replay System."""

import pytest
from datetime import datetime, timezone, date

from app.services.learning_replay import (
    COLOR_LEVELS,
    categorize_review_mode,
    color_for_minutes,
)


class TestColorForMinutes:
    def test_gray_for_zero(self):
        assert color_for_minutes(0) == "#ebedf0"

    def test_light_green_low(self):
        assert color_for_minutes(1) == "#9be9a8"
        assert color_for_minutes(15) == "#9be9a8"

    def test_mid_green(self):
        assert color_for_minutes(16) == "#40c463"
        assert color_for_minutes(30) == "#40c463"

    def test_dark_green(self):
        assert color_for_minutes(31) == "#30a14e"
        assert color_for_minutes(45) == "#30a14e"

    def test_deepest_green(self):
        assert color_for_minutes(46) == "#216e39"
        assert color_for_minutes(120) == "#216e39"
        assert color_for_minutes(9999) == "#216e39"

    def test_all_levels_covered(self):
        for lo, hi, color in COLOR_LEVELS:
            for m in [lo, (lo + hi) // 2, hi]:
                assert color_for_minutes(m) == color


class TestCategorizeReviewMode:
    def test_spelling_modes(self):
        for m in ["word-recall", "word-hinted", "word-preview", "word-context"]:
            assert categorize_review_mode(m) == "spelling", f"failed: {m}"

    def test_english_to_chinese_modes(self):
        for m in ["word-english_to_chinese", "word-listen_choose_chinese", "word-match_translation"]:
            assert categorize_review_mode(m) == "english_to_chinese", f"failed: {m}"

    def test_chinese_to_english_modes(self):
        for m in ["word-chinese_to_english", "word-listen_spell", "word-missing_letter", "word-hidden_recall"]:
            assert categorize_review_mode(m) == "chinese_to_english", f"failed: {m}"

    def test_phrase(self):
        assert categorize_review_mode("phrase-review") == "phrase"

    def test_sentence(self):
        assert categorize_review_mode("sentence-spelling") == "sentence"
        assert categorize_review_mode("sentence-cloze") == "sentence"

    def test_none_or_unknown(self):
        assert categorize_review_mode(None) == "other"
        assert categorize_review_mode("") == "other"
        assert categorize_review_mode("weird-mode") == "other"


class TestDateParsing:
    def test_valid_date(self):
        from app.utils import parse_date_param
        d = parse_date_param("2026-06-11")
        assert d == date(2026, 6, 11)

    def test_invalid_date(self):
        from app.utils import parse_date_param
        assert parse_date_param("not-a-date") is None
        assert parse_date_param("") is None
        assert parse_date_param("2026/06/11") is None
