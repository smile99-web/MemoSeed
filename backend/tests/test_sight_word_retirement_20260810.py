"""Regression tests for the 2026-08-10 sight-word retirement completion.

The 2026-08-03 retirement removed sight words from the review queue,
handwriting queue, daily test and voice practice — but the course-learn
sentence spelling flow still forced the child to keyboard-type every
the/a/is in every sentence (~1000 review events, ~19h/week), and mistake
loops kept minting micro-review tasks for them. These tests pin:

- frontend/backend sight-word lists cannot drift apart;
- schedule_micro_review_tasks_for_mistake never creates tasks for sight
  words (and still does for content words).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.word_review_task import WordReviewTask
from app.services.memory_scheduler import SIGHT_WORDS
from app.services.word_memory import schedule_micro_review_tasks_for_mistake


# --- frontend/backend list sync guard ----------------------------------------

def test_frontend_sight_words_match_backend():
    ts_path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "sight-words.ts"
    source = ts_path.read_text(encoding="utf-8")
    frontend_words = set(re.findall(r'"([a-z]+)"', source.split("new Set([", 1)[1].split("])", 1)[0]))
    assert frontend_words == set(SIGHT_WORDS), (
        f"frontend/backend SIGHT_WORDS drift: "
        f"only-frontend={sorted(frontend_words - set(SIGHT_WORDS))}, "
        f"only-backend={sorted(set(SIGHT_WORDS) - frontend_words)}"
    )


# --- micro-review task guard --------------------------------------------------

def _word_state(word: str):
    return SimpleNamespace(
        id=uuid4(),
        word=word,
        memory_state_id=None,
        learning_item_id=None,
        micro_review_stage=0,
        next_micro_review_at=None,
        task_type_counts={},
        error_type_counts={},
        consecutive_correct_count=0,
        consecutive_error_count=1,
        recall_correct_count=0,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
        priority_score=0.5,
        memory_strength=0.5,
        status="difficult",
        last_answer_seen_at=None,
    )


def _fake_db():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    return db


class TestSightWordMicroTaskGuard:
    def test_sight_word_creates_no_tasks(self):
        db = _fake_db()
        state = _word_state("the")
        schedule_micro_review_tasks_for_mistake(db, uuid4(), state, "the", None, "spelling")
        added = [call.args[0] for call in db.add.call_args_list]
        assert not any(isinstance(obj, WordReviewTask) for obj in added)
        # Micro-review clock must not be re-armed for sight words either.
        assert state.micro_review_stage == 0
        assert state.next_micro_review_at is None

    def test_all_sight_words_blocked(self):
        for word in sorted(SIGHT_WORDS):
            db = _fake_db()
            state = _word_state(word)
            schedule_micro_review_tasks_for_mistake(db, uuid4(), state, word, None, "spelling")
            added = [call.args[0] for call in db.add.call_args_list]
            assert not any(isinstance(obj, WordReviewTask) for obj in added), word

    def test_content_word_still_creates_tasks(self):
        db = _fake_db()
        state = _word_state("banana")
        schedule_micro_review_tasks_for_mistake(db, uuid4(), state, "香蕉", None, "spelling")
        added = [call.args[0] for call in db.add.call_args_list]
        assert any(isinstance(obj, WordReviewTask) for obj in added)
        assert state.micro_review_stage == 1
