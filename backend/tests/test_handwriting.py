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
    HANDWRITING_BOTH_TASK_TYPE,
    HANDWRITING_DICTATION_TASK_TYPE,
    HANDWRITING_TRANSLATION_TASK_TYPE,
    KEYBOARD_SPELLING_TASK_TYPES,
    compose_daily_handwriting_queue,
    handwriting_task_for,
    is_dictation_candidate,
    judge_handwriting,
    parse_lesson_number,
    pick_course_dictation_tasks,
    pick_daily_test_words,
    pick_review_word_tasks,
)


class TestHandwritingTaskFor:
    """手写化（2026-08-02）：键盘拼写任务类型出队时统一改写为手写听写。"""

    def test_all_keyboard_spelling_types_map_to_dictation(self):
        for task_type in ("listen_spell", "chinese_to_english", "missing_letter", "hidden_recall"):
            assert task_type in KEYBOARD_SPELLING_TASK_TYPES
            assert handwriting_task_for(task_type) == HANDWRITING_DICTATION_TASK_TYPE

    def test_recognition_types_pass_through(self):
        for task_type in ("listen_choose_chinese", "english_to_chinese", "match_translation", "voice_practice"):
            assert handwriting_task_for(task_type) == task_type

    def test_handwriting_types_pass_through(self):
        assert handwriting_task_for("handwriting_dictation") == "handwriting_dictation"
        assert handwriting_task_for("handwriting_translation") == "handwriting_translation"

    def test_none_passes_through(self):
        assert handwriting_task_for(None) is None


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

    def test_sight_words_excluded(self):
        """视觉词（the, I, is, are...）不出现在手写队列中——
        这些功能词在每句话里都会出现，手写考毫无价值。"""
        for word in ("the", "i", "is", "are", "a", "an", "he", "she", "it", "we", "you"):
            assert is_dictation_candidate(_item(english=word)) is False, f"'{word}' should be excluded"

    def test_content_words_still_qualify(self):
        """视觉词过滤不应影响正常内容词的入选。"""
        for word in ("apple", "beautiful", "elephant", "important", "remember", "school", "teacher"):
            assert is_dictation_candidate(_item(english=word)) is True, f"'{word}' should qualify"


class TestPickReviewWordTasks:
    def test_weakest_first_and_never_tested_first(self):
        weak = _item(english="weak")
        strong = _item(english="strong")
        never = _item(english="never")
        rows = [
            (strong, 0.9, None, False),
            (weak, 0.2, datetime(2026, 7, 1), False),
            (never, 0.5, None, False),
        ]
        selected = pick_review_word_tasks(rows, tested_today_ids=set(), limit=10)
        assert [item.english_text for item, _ in selected] == ["weak", "never", "strong"]

    def test_due_words_come_before_not_yet_due(self):
        """到期复习词（is_due=True）永远排在未到期词前面，即使未到期词更弱——
        手写队列的词部分要与"单词复习"模式当天服务的内容一致。"""
        due_strong = _item(english="due_strong")
        due_weak = _item(english="due_weak")
        not_due_weakest = _item(english="not_due_weakest")
        rows = [
            (not_due_weakest, 0.1, None, False),
            (due_strong, 0.9, datetime(2026, 7, 1), True),
            (due_weak, 0.3, None, True),
        ]
        selected = pick_review_word_tasks(rows, tested_today_ids=set(), limit=10)
        assert [item.english_text for item, _ in selected] == ["due_weak", "due_strong", "not_due_weakest"]

    def test_today_tested_items_excluded(self):
        done = _item(english="done")
        fresh = _item(english="fresh")
        rows = [(done, 0.1, None, True), (fresh, 0.9, None, True)]
        selected = pick_review_word_tasks(rows, tested_today_ids={done.id}, limit=10)
        assert [item.english_text for item, _ in selected] == ["fresh"]

    def test_word_tasks_alternate_dictation_translation(self):
        words = [_item(english=f"w{i}") for i in range(4)]
        rows = [(w, 0.5, None, True) for w in words]
        selected = pick_review_word_tasks(rows, tested_today_ids=set(), limit=10)
        assert [task for _, task in selected] == [
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
            HANDWRITING_DICTATION_TASK_TYPE,
            HANDWRITING_TRANSLATION_TASK_TYPE,
        ]

    def test_word_without_chinese_is_dictation_only(self):
        word = _item(english="mystery", chinese="")
        selected = pick_review_word_tasks([(word, 0.5, None, True)], tested_today_ids=set(), limit=10)
        assert selected == [(word, HANDWRITING_DICTATION_TASK_TYPE)]

    def test_non_word_rows_ignored(self):
        sentence = _item("sentence", "I like it.", "我喜欢它。")
        selected = pick_review_word_tasks([(sentence, 0.5, None, True)], tested_today_ids=set(), limit=10)
        assert selected == []

    def test_limit_respected(self):
        rows = [(_item(english=f"w{i}"), 0.5, None, True) for i in range(10)]
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
        """16 题 = 12 个复习词（与单词复习一次会话等量）+ 4 句课程，词全部在前。"""
        words = [(_item(english=f"w{i}"), 0.5, None, True) for i in range(14)]
        course = [_item("sentence", f"Lesson sentence {i}.", "课文句") for i in range(10)]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=16)
        types = [item.item_type for item, _ in queue]
        assert types.count("word") == 12
        assert types.count("sentence") == 4
        assert types[:12] == ["word"] * 12  # 单词复习全部在前
        assert [item.english_text for item, _ in queue[12:]] == [
            f"Lesson sentence {i}." for i in range(4)
        ]

    def test_course_tops_up_when_words_dry(self):
        words = [(_item(english="only"), 0.5, None, True)]
        course = [_item("sentence", f"S {i}.", "句") for i in range(20)]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=16)
        assert len(queue) == 16
        assert queue[0][0].english_text == "only"
        assert sum(1 for item, _ in queue if item.item_type == "sentence") == 15

    def test_words_top_up_when_course_dry(self):
        words = [(_item(english=f"w{i}"), 0.5, None, True) for i in range(20)]
        course = [_item("sentence", "Only one.", "唯一")]
        queue = compose_daily_handwriting_queue(words, course, tested_today_ids=set(), limit=16)
        assert len(queue) == 16
        assert sum(1 for item, _ in queue if item.item_type == "sentence") == 1
        # 课程只有 1 句 → 复习词补到 15 个，且词仍然全部排在课程句前面
        types = [item.item_type for item, _ in queue]
        assert types[:15] == ["word"] * 15

    def test_empty_both_pools_gives_empty_queue(self):
        assert compose_daily_handwriting_queue([], [], tested_today_ids=set(), limit=16) == []


class TestPickDailyTestWords:
    """每日一测选词（家长 2026-08-02）：今日所学优先 → 到期复习 → 最弱学过，
    今日已测的词排除（重测只出剩余）。"""

    def test_today_words_come_first_then_due_then_weak(self):
        today = [_item(english="today1"), _item(english="today2")]
        due = [_item(english="due1")]
        weak = [_item(english="weak1")]
        picked = pick_daily_test_words(today, due, weak, tested_today_ids=set(), limit=20)
        assert [item.english_text for item in picked] == ["today1", "today2", "due1", "weak1"]

    def test_tested_today_excluded_by_id(self):
        done = _item(english="done")
        fresh = _item(english="fresh")
        picked = pick_daily_test_words(
            [done, fresh], [], [], tested_today_ids={done.id}, limit=20,
        )
        assert [item.english_text for item in picked] == ["fresh"]

    def test_tested_today_excluded_by_word_text(self):
        """手写提交记账在 word-memory 专属 item 上（id 与课程 item 不同）——
        只靠 id 排除会让刚测过的词当天重出，必须按归一化词文本也排除。"""
        same_word_other_id = _item(english="Done")  # 大小写不同，同一个词
        fresh = _item(english="fresh")
        picked = pick_daily_test_words(
            [same_word_other_id, fresh], [], [],
            tested_today_ids=set(), tested_today_words={"done"}, limit=20,
        )
        assert [item.english_text for item in picked] == ["fresh"]

    def test_duplicate_word_forms_deduped(self):
        first = _item(english="apple")
        same_word = _item(english="Apple ")  # 同一个词的另一条 item 行
        picked = pick_daily_test_words([first, same_word], [], [], tested_today_ids=set(), limit=20)
        assert len(picked) == 1

    def test_non_word_items_skipped(self):
        sentence = _item("sentence", "I like it.", "我喜欢它。")
        word = _item(english="word1")
        picked = pick_daily_test_words([sentence, word], [], [], tested_today_ids=set(), limit=20)
        assert [item.english_text for item in picked] == ["word1"]

    def test_limit_respected(self):
        rows = [_item(english=f"w{i}") for i in range(30)]
        assert len(pick_daily_test_words(rows, [], [], tested_today_ids=set(), limit=20)) == 20

    def test_empty_pools_give_empty_queue(self):
        assert pick_daily_test_words([], [], [], tested_today_ids=set(), limit=20) == []


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


class TestJudgeHandwritingBoth:
    """每日一测（handwriting_both）双关判定：英文服务端硬校验
    （不信模型自判，同 tingxie 教训）+ 中文模型语义判定，两者都对才算对。"""

    def _set_both(self, mock_urlopen, en: str, zh: str, en_ok: bool, zh_ok: bool, comment: str = ""):
        mock_urlopen["next_response"] = _http_response(
            json.dumps({
                "recognized_english": en,
                "recognized_chinese": zh,
                "english_ok": en_ok,
                "chinese_ok": zh_ok,
                "comment": comment,
            })
        )

    def _judge_both(self):
        return judge_handwriting(
            "data:image/png;base64,AAAA",
            HANDWRITING_BOTH_TASK_TYPE,
            expected_english="apple",
            expected_chinese="苹果",
            base_url="https://ark.example.com/api/v3",
            api_key="sk-test",
        )

    def test_both_correct(self, mock_urlopen):
        self._set_both(mock_urlopen, "apple", "苹果", True, True)
        verdict = self._judge_both()
        assert verdict.correct is True
        assert verdict.english_ok is True
        assert verdict.chinese_ok is True
        assert verdict.recognized == "apple / 苹果"

    def test_divergent_english_fails_despite_model_pass(self, mock_urlopen):
        # 模型幻觉放行英文——服务端硬校验必须拦住。
        self._set_both(mock_urlopen, "banana", "苹果", True, True)
        verdict = self._judge_both()
        assert verdict.english_ok is False
        assert verdict.chinese_ok is True
        assert verdict.correct is False

    def test_exact_english_forces_pass_despite_model_strictness(self, mock_urlopen):
        self._set_both(mock_urlopen, "Apple!", "苹果", False, True)
        verdict = self._judge_both()
        assert verdict.english_ok is True
        assert verdict.correct is True

    def test_right_english_wrong_chinese_is_wrong(self, mock_urlopen):
        self._set_both(mock_urlopen, "apple", "香蕉", True, False)
        verdict = self._judge_both()
        assert verdict.english_ok is True
        assert verdict.chinese_ok is False
        assert verdict.correct is False

    def test_empty_chinese_fails(self, mock_urlopen):
        self._set_both(mock_urlopen, "apple", "", True, True)
        verdict = self._judge_both()
        assert verdict.chinese_ok is False
        assert verdict.correct is False

    def test_fallback_comment_points_at_english(self, mock_urlopen):
        self._set_both(mock_urlopen, "aple", "苹果", False, True)
        verdict = self._judge_both()
        assert verdict.english_ok is False
        assert "apple" in verdict.comment

    def test_fallback_comment_points_at_chinese(self, mock_urlopen):
        self._set_both(mock_urlopen, "apple", "香蕉", True, False)
        verdict = self._judge_both()
        assert "中文" in verdict.comment

    def test_prompt_mentions_both_grids_and_word(self, mock_urlopen):
        self._set_both(mock_urlopen, "apple", "苹果", True, True)
        self._judge_both()
        prompt_text = mock_urlopen["payload"]["messages"][0]["content"][1]["text"]
        assert "四线" in prompt_text
        assert "米字格" in prompt_text
        assert "apple" in prompt_text
