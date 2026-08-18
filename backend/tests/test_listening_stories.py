"""Unit tests for the listening-stories service (parse + validate only; no DB/LLM)."""

import json

import pytest

from app.services.listening_stories import (
    DIALOGUE_B_CHINESE_FALLBACKS,
    DIALOGUE_B_CHINESE_VOICE,
    DIALOGUE_B_ENGLISH_FALLBACKS,
    DIALOGUE_B_ENGLISH_VOICE,
    DIALOGUE_TURNS_MAX,
    DIALOGUE_TURNS_MIN,
    MAX_WORDS_PER_DIALOGUE_TURN,
    MAX_WORDS_PER_SENTENCE,
    SENTENCES_PER_STORY_MAX,
    SENTENCES_PER_STORY_MIN,
    is_dialogue_sentences,
    parse_story_json,
    resolve_dialogue_b_voices,
    story_player_payload,
    validate_dialogue,
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


def _dialogue(turns: int = 12) -> dict:
    sentences = []
    for i in range(turns):
        speaker = "A" if i % 2 == 0 else "B"
        sentences.append({
            "speaker": speaker,
            "en": f"Turn number {i} is fine.",
            "zh": f"第 {i} 轮。",
        })
    return {"title_en": "Say Hello", "title_zh": "打招呼", "sentences": sentences}


class TestValidateDialogue:
    def test_valid_dialogue_normalized(self):
        result = validate_dialogue(_dialogue())
        assert result is not None
        assert result["title"] == "Say Hello · 打招呼"
        assert len(result["sentences"]) == 12
        assert [s["speaker"] for s in result["sentences"]] == ["A", "B"] * 6

    def test_must_start_with_a(self):
        data = _dialogue()
        data["sentences"][0]["speaker"] = "B"
        assert validate_dialogue(data) is None

    def test_non_alternating_rejected(self):
        data = _dialogue()
        data["sentences"][3]["speaker"] = "A"  # 连续两个 A
        assert validate_dialogue(data) is None

    def test_missing_speaker_rejected(self):
        data = _dialogue()
        del data["sentences"][2]["speaker"]
        assert validate_dialogue(data) is None

    def test_one_word_turn_allowed(self):
        data = _dialogue()
        data["sentences"][1]["en"] = "Sure!"
        result = validate_dialogue(data)
        assert result is not None
        assert result["sentences"][1]["en"] == "Sure!"

    def test_too_few_turns_rejected(self):
        assert validate_dialogue(_dialogue(DIALOGUE_TURNS_MIN - 1)) is None

    def test_too_many_turns_rejected(self):
        assert validate_dialogue(_dialogue(DIALOGUE_TURNS_MAX + 1)) is None

    def test_turn_too_long_rejected(self):
        data = _dialogue()
        data["sentences"][0]["en"] = " ".join(["word"] * (MAX_WORDS_PER_DIALOGUE_TURN + 1))
        assert validate_dialogue(data) is None

    def test_chinese_in_english_rejected(self):
        data = _dialogue()
        data["sentences"][0]["en"] = "Hello 你好"
        assert validate_dialogue(data) is None


class TestDialogueHelpers:
    def test_is_dialogue_sentences(self):
        assert is_dialogue_sentences([{"speaker": "A", "en": "Hi.", "zh": "嗨。"}]) is True
        assert is_dialogue_sentences([{"en": "Hi.", "zh": "嗨。"}]) is False
        assert is_dialogue_sentences([]) is False

    def test_b_voices_default(self):
        en_b, zh_b = resolve_dialogue_b_voices("en_female_dacey_uranus_bigtts", "zh_male_taocheng_uranus_bigtts")
        assert en_b == DIALOGUE_B_ENGLISH_VOICE
        assert zh_b == DIALOGUE_B_CHINESE_VOICE

    def test_b_voices_avoid_clash_with_a(self):
        en_b, zh_b = resolve_dialogue_b_voices(DIALOGUE_B_ENGLISH_VOICE, DIALOGUE_B_CHINESE_VOICE)
        assert en_b != DIALOGUE_B_ENGLISH_VOICE
        assert zh_b != DIALOGUE_B_CHINESE_VOICE
        assert en_b in DIALOGUE_B_ENGLISH_FALLBACKS
        assert zh_b in DIALOGUE_B_CHINESE_FALLBACKS


class _FakeStory:
    """story_player_payload / warm_story_audio 只需要这些属性。"""

    def __init__(self, sentences):
        self.id = "00000000-0000-0000-0000-000000000000"
        self.title = "T"
        self.theme = "日常对话"
        self.sentences = sentences


class TestDialoguePayloadVoices:
    def test_b_sentences_use_b_voices(self):
        story = _FakeStory([
            {"speaker": "A", "en": "Hello there.", "zh": "你好。"},
            {"speaker": "B", "en": "Hi there.", "zh": "嗨。"},
        ])
        payload = story_player_payload(
            story, "VOICE_A_EN", "VOICE_A_ZH", 0, "VOICE_B_EN", "VOICE_B_ZH",
        )
        assert payload["kind"] == "dialogue"
        a_sentence, b_sentence = payload["sentences"]
        assert a_sentence["speaker"] == "A"
        assert b_sentence["speaker"] == "B"
        # 同一语言不同角色 → 音频 URL 必须不同（cache key 含 voice）
        assert a_sentence["en_audio_url"] != b_sentence["en_audio_url"]
        assert a_sentence["zh_audio_url"] != b_sentence["zh_audio_url"]

    def test_b_voice_fallback_to_a_when_missing(self):
        story = _FakeStory([{"speaker": "B", "en": "Hi.", "zh": "嗨。"}])
        payload = story_player_payload(story, "VOICE_A_EN", "VOICE_A_ZH", 0)
        solo = story_player_payload(
            _FakeStory([{"en": "Hi.", "zh": "嗨。"}]), "VOICE_A_EN", "VOICE_A_ZH", 0,
        )
        assert payload["sentences"][0]["en_audio_url"] == solo["sentences"][0]["en_audio_url"]

    def test_plain_story_kind_unchanged(self):
        story = _FakeStory([{"en": "A dog runs.", "zh": "小狗跑。"}])
        payload = story_player_payload(story, "V_EN", "V_ZH", 0)
        assert payload["kind"] == "story"
        assert payload["sentences"][0]["speaker"] is None

    def test_warm_routes_b_turns_to_b_voices(self, monkeypatch):
        from app.services import listening_stories

        synthesized: list[tuple[str, str]] = []
        monkeypatch.setattr(listening_stories, "is_audio_cached", lambda *a, **k: False)

        def fake_synthesize(text, settings):
            synthesized.append((text, settings.voice))

        monkeypatch.setattr(listening_stories, "synthesize_volcengine_speech", fake_synthesize)
        story = _FakeStory([
            {"speaker": "A", "en": "Hello.", "zh": "你好。"},
            {"speaker": "B", "en": "Hi.", "zh": "嗨。"},
        ])
        stats = listening_stories.warm_story_audio(
            story, "VA_EN", "VA_ZH", 0, lambda voice, language: type("S", (), {"voice": voice})(),
            "VB_EN", "VB_ZH",
        )
        assert stats == {"cached": 0, "generated": 4, "failed": 0, "total": 4}
        assert synthesized == [
            ("Hello.", "VA_EN"), ("你好。", "VA_ZH"),
            ("Hi.", "VB_EN"), ("嗨。", "VB_ZH"),
        ]
