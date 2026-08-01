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
    compose_daily_handwriting_queue,
    is_dictation_candidate,
    judge_handwriting,
    parse_lesson_number,
    pick_course_dictation_tasks,
    pick_review_word_tasks,
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


class TestPickReviewWordTasks:
    def test_weakest_first_and_never_tested_first(self):
        weak = _item(english="weak")
        strong = _item(english="strong")
        never = _item(english="never")
        rows = [
            (strong, 0.9, None),
            (weak, 0.2, datetime(2026, 7, 1)),
            (never, 0.5, None),
        ]
        selected = pick_review_word_tasks(rows, tested_today_ids=set(), limit=10)
        assert [item.english_text for item, _ in selected] == ["weak", "never", "strong"]

    def test_today_tested_items_excluded(self):
        done = _item(english="done")
        fresh = _item(english="fresh")
        rows = [(done, 0.1, None), (fresh, 0.9, None)]
        selected = pick_review_word_tasks(rows, tested_today_ids={done.id}, limit=10)
        assert [item.english_text for item, _ in selected] == ["fresh"]

    def test_word_tasks_alternate_dictation_translation(self):
        words = [_item(english=f"w{i}") for i in range(4)]
        rows = [(w, 0.5, None) for w in words]
        selected = pick_review_word_tasks(rows, tested_today_ids=set(), limit=10)
        assert [task for _, task in selected] == [
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
        ]

    def test_word_without_chinese_is_dictation_only(self):
        word = _item(english="mystery", chinese="")
        selected = pick_review_word_tasks([(word, 0.5, None)], tested_today_ids=set(), limit=10)
        assert selected == [(word, HANDWRITING_DICTATION_TASK_TYPE)]

    def test_non_word_rows_ignored(self):
        sentence = _item("sentence", "I like it.", "我喜欢它。")
        selected = pick_review_word_tasks([(sentence, 0.5, None)], tested_today_ids=set(), limit=10)
        assert selected == []

    def test_limit_respected(self):
        rows = [(_item(english=f"w{i}"), 0.5, None) for i in range(10)]
        assert len(pick_review_word_tasks(rows, tested_today_ids=set(), limit=3)) == 3


class TestParseLessonNumber:
    def test_basic(self):
        assert parse_lesson_number("第1课") == 1
        assert parse_lesson_number("第10课") == 10

    def test_unparseable_sorts_last(self):
        assert parse_lesson_number("") == 999
        assert parse_lesson_number("入门单元") == 999


class TestPickCourseDictationTasks:
    def test_preserves_given_order_and_dictation_only(self):
        sentences = [_item("sentence", f"S number {i}.", "句子") for i in range(5)]
        selected = pick_course_dictation_tasks(sentences, tested_today_ids=set(), limit=3)
        assert [item.english_text for item, _ in selected] == ["S number 0.", "S number 1.", "S number 2."]
        assert all(task == HANDWRITING_DICTATION_TASK_TYPE for _, task in selected)

    def test_skips_tested_today_and_long_sentences(self):
        done = _item("sentence", "Done one.", "完了")
        long_sentence = _item("sentence", " ".join(["word"] * 12), "长句")
        fresh = _item("sentence", "Fresh one.", "新的")
        selected = pick_course_dictation_tasks(
            [done, long_sentence, fresh], tested_today_ids={done.id}, limit=5,
        )
        assert [item.english_text for item, _ in selected] == ["Fresh one."]


class TestComposeDailyHandwritingQueue:
    def test_words_first_then_course_in_order(self):
        words = [(_item(english=f"w{i}"), 0.5, None) for i in range(12)]
        course = [_item("sentence", f"Lesson sentence {i}.", "课文句") for i in range(10)]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=12)
        types = [item.item_type for item, _ in queue]
        assert types.count("word") == 8
        assert types.count("sentence") == 4
        assert types[:8] == ["word"] * 8  # 单词复习全部在前
        assert [item.english_text for item, _ in queue[8:]] == [
            f"Lesson sentence {i}." for i in range(4)
        ]

    def test_course_tops_up_when_words_dry(self):
        words = [(_item(english="only"), 0.5, None)]
        course = [_item("sentence", f"S {i}.", "句") for i in range(20)]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=12)
        assert len(queue) == 12
        assert queue[0][0].english_text == "only"
        assert sum(1 for item, _ in queue if item.item_type == "sentence") == 11

    def test_words_top_up_when_course_dry(self):
        words = [(_item(english=f"w{i}"), 0.5, None) for i in range(20)]
        course = [_item("sentence", "Only one.", "唯一")]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=12)
        assert len(queue) == 12
        assert sum(1 for item, _ in queue if item.item_type == "sentence") == 1

    def test_empty_both_pools_gives_empty_queue(self):
        assert compose_daily_handwriting_queue([], [], tested_today_ids=set(), limit=12) == []


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
