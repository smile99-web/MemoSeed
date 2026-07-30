"""Unit tests for the listening-stories service (parse + validate only; no DB/LLM)."""

import json

import pytest

from app.services.listening_stories import (
    MAX_WORDS_PER_SENTENCE,
    SENTENCES_PER_STORY_MAX,
    SENTENCES_PER_STORY_MIN,
    parse_story_json,
    validate_story,
)


def _story(sentence_count: int = SENTENCES_PER_STORY_MIN, words: int = 4) -> dict:
    sentence = " ".join(["word"] * (words - 2)) + " dog runs" if words >= 2 else "runs"
    return {
        "title_en": "My Dog",
        "title_zh": "我的狗",
        "sentences": [
            {"en": f"This is my happy dog number {i}." if words == 6 else f"Little dog number {i} runs.",
             "zh": f"这是第 {i} 句。"}
            for i in range(sentence_count)
        ],
    }


class TestParseStoryJson:
    def test_plain_json(self):
        data = parse_story_json(json.dumps(_story()))
        assert data["title_en"] == "My Dog"

    def test_json_fence(self):
        raw = "好的，这是故事：\n```json\n" + json.dumps(_story()) + "\n```"
        data = parse_story_json(raw)
        assert data["title_zh"] == "我的狗"

    def test_surrounding_prose(self):
        raw = "Here you go! " + json.dumps(_story()) + " Hope that helps."
        data = parse_story_json(raw)
        assert len(data["sentences"]) == SENTENCES_PER_STORY_MIN

    def test_garbage_raises(self):
        with pytest.raises(Exception):
            parse_story_json("no json here at all")


class TestValidateStory:
    def test_valid_story_normalized(self):
        result = validate_story(_story())
        assert result is not None
        assert result["title"] == "My Dog · 我的狗"
        assert len(result["sentences"]) == SENTENCES_PER_STORY_MIN
        for s in result["sentences"]:
            assert s["en"] and s["zh"]

    def test_title_without_zh(self):
        data = _story()
        data["title_zh"] = ""
        result = validate_story(data)
        assert result is not None
        assert result["title"] == "My Dog"

    def test_missing_title_rejected(self):
        data = _story()
        data["title_en"] = ""
        assert validate_story(data) is None

    def test_too_few_sentences_rejected(self):
        assert validate_story(_story(sentence_count=SENTENCES_PER_STORY_MIN - 1)) is None

    def test_too_many_sentences_rejected(self):
        assert validate_story(_story(sentence_count=SENTENCES_PER_STORY_MAX + 1)) is None

    def test_sentence_too_long_rejected(self):
        data = _story()
        data["sentences"][0]["en"] = " ".join(["very"] * (MAX_WORDS_PER_SENTENCE + 1))
        assert validate_story(data) is None

    def test_one_word_sentence_rejected(self):
        data = _story()
        data["sentences"][0]["en"] = "Run."
        assert validate_story(data) is None

    def test_chinese_in_english_rejected(self):
        data = _story()
        data["sentences"][0]["en"] = "The 狗 runs fast."
        assert validate_story(data) is None

    def test_missing_zh_rejected(self):
        data = _story()
        data["sentences"][0]["zh"] = ""
        assert validate_story(data) is None

    def test_non_dict_sentence_rejected(self):
        data = _story()
        data["sentences"][0] = "just a string"
        assert validate_story(data) is None

    def test_whitespace_normalized(self):
        data = _story()
        data["sentences"][0]["en"] = "  The   dog\n  runs   fast.  "
        result = validate_story(data)
        assert result is not None
        assert result["sentences"][0]["en"] == "The dog runs fast."
