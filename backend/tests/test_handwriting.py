"""Tests for the handwriting dictation mode (手写听写).

Covers the pure service layer: candidate selection (weakest-first, one per
day, dictation/translation alternation) and the vision judge's response
handling — especially the server-side verdict guard ported from tingxie
(never trust the model's own `correct` flag for dictation).
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import handwriting
from app.services.handwriting import (
    HANDWRITING_DICTATION_TASK_TYPE,
    HANDWRITING_TRANSLATION_TASK_TYPE,
    is_dictation_candidate,
    judge_handwriting,
    select_handwriting_items,
)


def _item(item_type="word", english="apple", chinese="苹果", item_id=None):
    return SimpleNamespace(
        id=item_id or uuid4(),
        item_type=item_type,
        english_text=english,
        chinese_text=chinese,
    )


class TestIsDictationCandidate:
    def test_word_always_qualifies(self):
        assert is_dictation_candidate(_item()) is True

    def test_short_sentence_qualifies(self):
        assert is_dictation_candidate(_item("sentence", "I have a pen.", "我有一支钢笔。")) is True

    def test_long_sentence_rejected(self):
        long_sentence = " ".join(["word"] * 12)
        assert is_dictation_candidate(_item("sentence", long_sentence, "长句")) is False

    def test_empty_english_rejected(self):
        assert is_dictation_candidate(_item("word", "", "空")) is False


class TestSelectHandwritingItems:
    def test_weakest_first_and_never_tested_first(self):
        weak = _item(english="weak")
        strong = _item(english="strong")
        never = _item(english="never")
        rows = [
            (strong, 0.9, None),
            (weak, 0.2, datetime(2026, 7, 1)),
            (never, 0.5, None),
        ]
        selected = select_handwriting_items(rows, tested_today_ids=set(), limit=10)
        assert [item.english_text for item, _ in selected] == ["weak", "never", "strong"]

    def test_today_tested_items_excluded(self):
        done = _item(english="done")
        fresh = _item(english="fresh")
        rows = [(done, 0.1, None), (fresh, 0.9, None)]
        selected = select_handwriting_items(rows, tested_today_ids={done.id}, limit=10)
        assert [item.english_text for item, _ in selected] == ["fresh"]

    def test_word_tasks_alternate_and_sentences_are_dictation(self):
        words = [_item(english=f"w{i}") for i in range(4)]
        sentence = _item("sentence", "I like it.", "我喜欢它。")
        rows = [(w, 0.5, None) for w in words] + [(sentence, 0.5, None)]
        selected = select_handwriting_items(rows, tested_today_ids=set(), limit=10)
        task_by_english = {item.english_text: task for item, task in selected}
        assert task_by_english["I like it."] == HANDWRITING_DICTATION_TASK_TYPE
        word_tasks = [task_by_english[w.english_text] for w in words]
        assert word_tasks == [
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
        ]

    def test_words_and_sentences_are_mixed_not_globally_sorted(self):
        # Production bug (first deploy): sentence memory strengths are
        # systematically lower, so a global weakness sort served 12
        # sentences and ZERO words/translation tasks.
        sentences = [_item("sentence", f"S number {i}.", "句子") for i in range(12)]
        words = [_item(english=f"w{i}") for i in range(12)]
        rows = [(s, 0.1, None) for s in sentences] + [(w, 0.9, None) for w in words]
        selected = select_handwriting_items(rows, tested_today_ids=set(), limit=12)
        types = [item.item_type for item, _ in selected]
        assert types.count("word") == 8
        assert types.count("sentence") == 4
        assert types[0] == "word"  # words lead

    def test_sentence_pool_dry_tops_up_from_words(self):
        words = [_item(english=f"w{i}") for i in range(12)]
        rows = [(w, 0.5, None) for w in words]
        selected = select_handwriting_items(rows, tested_today_ids=set(), limit=12)
        assert len(selected) == 12
        assert all(item.item_type == "word" for item, _ in selected)

    def test_word_without_chinese_is_dictation_only(self):
        word = _item(english="mystery", chinese="")
        selected = select_handwriting_items([(word, 0.5, None)], tested_today_ids=set(), limit=10)
        assert selected == [(word, HANDWRITING_DICTATION_TASK_TYPE)]

    def test_limit_respected(self):
        rows = [(_item(english=f"w{i}"), 0.5, None) for i in range(10)]
        assert len(select_handwriting_items(rows, tested_today_ids=set(), limit=3)) == 3


class _FakeHttpResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_response(content: str) -> _FakeHttpResponse:
    body = {"choices": [{"message": {"content": content}}]}
    return _FakeHttpResponse(json.dumps(body).encode("utf-8"))


@pytest.fixture()
def mock_urlopen(monkeypatch):
    captured: dict = {"next_response": None}

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return captured["next_response"]

    monkeypatch.setattr(handwriting, "urlopen", fake_urlopen)
    return captured


def _set_next(mock_urlopen, recognized: str, correct: bool):
    mock_urlopen["next_response"] = _http_response(
        json.dumps({"recognized": recognized, "correct": correct, "comment": "加油"})
    )


class TestJudgeHandwriting:
    def test_thinking_disabled_in_payload(self, mock_urlopen):
        _set_next(mock_urlopen, "apple", True)
        judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="apple",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert mock_urlopen["payload"]["thinking"] == {"type": "disabled"}

    def test_exact_recognition_passes_even_if_model_says_wrong(self, mock_urlopen):
        _set_next(mock_urlopen, "Apple!", False)  # normalized == expected
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="apple",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is True

    def test_divergent_recognition_fails_even_if_model_says_correct(self, mock_urlopen):
        _set_next(mock_urlopen, "banana", True)  # hallucinated pass
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="strawberry",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is False

    def test_near_miss_with_model_pass_survives(self, mock_urlopen):
        # 8/9 normalized chars equal → similarity >= 0.85 → trust the model's
        # lenient pass (e.g. it judged a wobbly-but-right letter as correct).
        _set_next(mock_urlopen, "strawbery", True)
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="strawberry",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is True

    def test_empty_recognition_fails(self, mock_urlopen):
        _set_next(mock_urlopen, "", True)
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="apple",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is False
        assert "没有认出" in verdict.comment

    def test_translation_trusts_model_semantics(self, mock_urlopen):
        _set_next(mock_urlopen, "喜欢", True)
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_TRANSLATION_TASK_TYPE,
            expected_english="like",
            expected_chinese="喜欢；像",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is True

    def test_markdown_fenced_json_parsed(self, mock_urlopen):
        mock_urlopen["next_response"] = _http_response(
            '```json\n{"recognized": "apple", "correct": true, "comment": "好"}\n```'
        )
        verdict = judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_DICTATION_TASK_TYPE,
            expected_english="apple",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )
        assert verdict.correct is True

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="not configured"):
            judge_handwriting(
                "data:image/png;base64,AAAA",
                HANDWRITING_DICTATION_TASK_TYPE,
                expected_english="apple",
                base_url="https://ark.example.com/api/v3",
                api_key="",
            )

    def test_non_data_url_rejected(self):
        with pytest.raises(ValueError, match="data URL"):
            judge_handwriting(
                "https://example.com/x.png",
                HANDWRITING_DICTATION_TASK_TYPE,
                expected_english="apple",
                base_url="https://ark.example.com/api/v3",
                api_key="sk-test",
            )
