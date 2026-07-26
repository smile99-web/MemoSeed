"""Dedicated read-aloud ("语音练习") speaking practice.

The child sees a familiar sentence/phrase, hears the model TTS, then reads it
aloud; the pronunciation gate (app.services.pronunciation) decides when to
advance. This module decides WHICH items to serve:

- studied content only (MemoryState.repetition_count > 0, enforced by the
  caller's query) — speaking practice rehearses known material, it must never
  ambush the child with brand-new words;
- short enough to read aloud in one breath;
- least-recently-spoken first, each item at most once per day, with a small
  daily cap so the mode stays a light complement to review.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.utils import tokenize_words

MIN_SPEAK_WORDS = 2
MAX_SPEAK_WORDS = 12
MAX_SPEAK_CHARS = 200
SPEAK_DAILY_CAP = 10

# review_task_type on served items (frontend switches the study screen into
# the echo-gate flow) / review_mode on the timeline LearningEvent.
READ_ALOUD_TASK_TYPE = "read_aloud"
READ_ALOUD_REVIEW_MODE = "read-aloud"

_EPOCH = datetime(1970, 1, 1)


def is_speakable(item: Any) -> bool:
    """A sentence/phrase is speakable when it is short enough to read aloud
    in one breath and has a Chinese gloss for the echo card."""
    if getattr(item, "item_type", None) not in ("sentence", "phrase"):
        return False
    english = (getattr(item, "english_text", "") or "").strip()
    if not english or len(english) > MAX_SPEAK_CHARS:
        return False
    if not (MIN_SPEAK_WORDS <= len(tokenize_words(english)) <= MAX_SPEAK_WORDS):
        return False
    chinese = (getattr(item, "chinese_text", "") or "").strip()
    return bool(chinese)


def select_speak_candidates(
    rows: list[tuple[Any, int, datetime | None]],
    *,
    spoken_today_ids: set[UUID],
    limit: int,
) -> list[Any]:
    """Filter + order read-aloud candidates.

    rows: (learning_item, repetition_count, last_read_aloud_at) triples.
    Excludes unspeakable items and anything already spoken today; orders
    never-spoken first, then oldest-spoken, with familiarity (repetition
    count) breaking ties.
    """
    candidates: list[tuple[Any, int, datetime | None]] = []
    for item, repetition_count, last_spoken_at in rows:
        if getattr(item, "id", None) in spoken_today_ids:
            continue
        if not is_speakable(item):
            continue
        candidates.append((item, repetition_count, last_spoken_at))
    candidates.sort(
        key=lambda row: (
            row[2] is not None,  # never-spoken first
            (row[2] or _EPOCH).replace(tzinfo=None),  # then oldest-spoken
            -row[1],  # then most familiar
        )
    )
    return [item for item, _reps, _spoken in candidates[:limit]]
