"""Unit tests for the read-aloud ("语音练习") speak-item selection.

Product rules under test:
- only sentence/phrase items are speakable (never single words);
- the text must be readable aloud in one breath (2-12 words, <=200 chars);
- a Chinese gloss is required (the echo card shows it);
- items already spoken today are excluded (one practice per item per day);
- ordering: never-spoken first, then oldest-spoken, familiarity breaks ties.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.speak_practice import (
    MAX_SPEAK_WORDS,
    is_speakable,
    select_speak_candidates,
)


def _item(english: str, *, item_type: str = "sentence", chinese: str = "中文", item_id=None):
    return SimpleNamespace(
        id=item_id or uuid4(),
        item_type=item_type,
        english_text=english,
        chinese_text=chinese,
    )


class TestIsSpeakable:
    def test_plain_sentence_is_speakable(self):
        assert is_speakable(_item("Give me a pen")) is True

    def test_phrase_is_speakable(self):
        assert is_speakable(_item("a cup of tea", item_type="phrase")) is True

    def test_single_word_is_not_speakable(self):
        assert is_speakable(_item("apple", item_type="word")) is False
        # Even a word-typed item with spaces is rejected by type.
        assert is_speakable(_item("ice cream", item_type="word")) is False

    def test_too_few_words(self):
        assert is_speakable(_item("Hello")) is False

    def test_too_many_words(self):
        long_sentence = " ".join(f"word{i}" for i in range(MAX_SPEAK_WORDS + 1))
        assert is_speakable(_item(long_sentence)) is False

    def test_missing_chinese_is_not_speakable(self):
        assert is_speakable(_item("Give me a pen", chinese="")) is False
        assert is_speakable(_item("Give me a pen", chinese=None)) is False

    def test_strips_whitespace_before_counting(self):
        assert is_speakable(_item("  Give me a pen  ")) is True


class TestSelectSpeakCandidates:
    def test_excludes_items_spoken_today(self):
        spoken = _item("I like apples")
        fresh = _item("She has a cat")
        rows = [(spoken, 3, None), (fresh, 1, None)]
        selected = select_speak_candidates(rows, spoken_today_ids={spoken.id}, limit=10)
        assert selected == [fresh]

    def test_excludes_unspeakable_items(self):
        good = _item("I like apples")
        bad = _item("apple", item_type="word")
        rows = [(good, 1, None), (bad, 5, None)]
        selected = select_speak_candidates(rows, spoken_today_ids=set(), limit=10)
        assert selected == [good]

    def test_never_spoken_comes_first(self):
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        old_spoken = _item("I like apples")
        never_spoken = _item("She has a cat")
        rows = [
            (old_spoken, 9, now - timedelta(days=30)),
            (never_spoken, 1, None),
        ]
        selected = select_speak_candidates(rows, spoken_today_ids=set(), limit=10)
        assert selected == [never_spoken, old_spoken]

    def test_oldest_spoken_next(self):
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        recent = _item("I like apples")
        stale = _item("She has a cat")
        rows = [
            (recent, 5, now - timedelta(days=2)),
            (stale, 5, now - timedelta(days=20)),
        ]
        selected = select_speak_candidates(rows, spoken_today_ids=set(), limit=10)
        assert selected == [stale, recent]

    def test_familiarity_breaks_never_spoken_ties(self):
        less_familiar = _item("I like apples")
        more_familiar = _item("She has a cat")
        rows = [(less_familiar, 1, None), (more_familiar, 8, None)]
        selected = select_speak_candidates(rows, spoken_today_ids=set(), limit=10)
        assert selected == [more_familiar, less_familiar]

    def test_limit_is_respected(self):
        rows = [(_item(f"sentence number {i} here", item_id=uuid4()), 1, None) for i in range(10)]
        selected = select_speak_candidates(rows, spoken_today_ids=set(), limit=3)
        assert len(selected) == 3
