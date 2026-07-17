"""
Tests for the grammar history service (DB-touching).

These tests need a real DB. We use the in-memory SQLite via the
project's `conftest.py` if it's compatible; otherwise we mock the
DB session. Since the project ships Alembic + PostgreSQL-only
features, we test the pure-Python aggregator functions
(_session_to_summary) directly and the high-level history
orchestration via mocks.

Run with:
    cd /Users/ai/MemoSeed/backend
    pytest tests/test_grammar_history.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.grammar import (
    GrammarAnswerSubmission,
    GrammarSessionStart,
)
from app.services.grammar_history import (
    _session_to_summary,
    complete_session,
    create_session,
    get_user_history,
    record_answer,
)


# --- Pure-Python unit tests (no DB) ----------------------------------------

class TestSessionToSummary:
    """Verify the summary conversion handles edge cases (0 questions, all wrong, etc)."""

    def test_zero_questions_yields_zero_accuracy(self):
        session = SimpleNamespace(
            id=uuid4(),
            level=5,
            total_questions=0,
            correct_count=0,
            choice_questions=0,
            fill_in_questions=0,
            started_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
            completed_at=None,
        )
        summary = _session_to_summary(session)
        assert summary.accuracy == 0.0
        assert summary.total_questions == 0

    def test_all_correct(self):
        session = SimpleNamespace(
            id=uuid4(),
            level=5,
            total_questions=10,
            correct_count=10,
            choice_questions=6,
            fill_in_questions=4,
            started_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 6, 23, 10, 5, tzinfo=UTC),
        )
        summary = _session_to_summary(session)
        assert summary.accuracy == 1.0
        assert summary.completed_at is not None

    def test_partial_correct(self):
        session = SimpleNamespace(
            id=uuid4(),
            level=8,
            total_questions=10,
            correct_count=7,
            choice_questions=6,
            fill_in_questions=4,
            started_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 6, 23, 10, 4, tzinfo=UTC),
        )
        summary = _session_to_summary(session)
        assert summary.accuracy == pytest.approx(0.7)


class TestSchemaValidation:
    """Pin the schema validation so client/server agree on required fields."""

    def test_session_start_requires_question_ids(self):
        # Pydantic should reject empty question_ids list
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GrammarSessionStart(
                level=5,
                total_questions=10,
                choice_questions=10,
                fill_in_questions=0,
                question_ids=[],
            )

    def test_session_start_out_of_range_level_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GrammarSessionStart(
                level=11,  # out of range
                total_questions=10,
                choice_questions=10,
                fill_in_questions=0,
                question_ids=["q1"] * 10,
            )

    def test_answer_submission_requires_level(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GrammarAnswerSubmission(
                question_id="q1",
                question_type="choice",
                # level missing
                prompt="?",
                user_answer="A",
                correct_answer="A",
                is_correct=True,
            )

    def test_answer_submission_negative_time_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GrammarAnswerSubmission(
                question_id="q1",
                question_type="choice",
                level=5,
                prompt="?",
                user_answer="A",
                correct_answer="A",
                is_correct=True,
                time_spent_ms=-1,  # out of range
            )


# --- Mocked DB-level tests ---------------------------------------------------

class TestRecordAnswer:
    """Verify the record_answer orchestration: validate session, increment
    correct_count atomically, write the answer row.

    Uses SimpleNamespace mocks instead of a real session to keep these
    unit tests fast and DB-agnostic.
    """

    def test_raises_when_session_not_found(self):
        db = _MockDB(scalar_result=None)
        with pytest.raises(ValueError, match="not found"):
            record_answer(
                db,
                user_id=uuid4(),
                session_id=uuid4(),
                question_id="q1",
                question_type="choice",
                level=5,
                prompt="?",
                user_answer="A",
                correct_answer="A",
                is_correct=True,
                time_spent_ms=1000,
            )

    def test_raises_when_session_already_completed(self):
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=datetime.now(UTC),
            correct_count=5,
        )
        db = _MockDB(scalar_result=session)
        with pytest.raises(ValueError, match="already completed"):
            record_answer(
                db,
                user_id=session.user_id,
                session_id=session.id,
                question_id="q1",
                question_type="choice",
                level=5,
                prompt="?",
                user_answer="A",
                correct_answer="A",
                is_correct=True,
                time_spent_ms=1000,
            )

    def test_correct_answer_increments_count(self):
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=None,
            correct_count=3,
        )
        db = _MockDB(scalar_result=[session, None])  # session lookup, then no existing answer
        record_answer(
            db,
            user_id=session.user_id,
            session_id=session.id,
            question_id="q4",
            question_type="choice",
            level=5,
            prompt="?",
            user_answer="A",
            correct_answer="A",
            is_correct=True,
            time_spent_ms=2000,
        )
        assert session.correct_count == 4, (
            f"Expected correct_count to be incremented from 3 to 4, got {session.correct_count}"
        )
        # Verify the answer was added to the session
        assert hasattr(db, "added_objects")
        assert len(db.added_objects) == 1

    def test_wrong_answer_does_not_increment_count(self):
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=None,
            correct_count=3,
        )
        db = _MockDB(scalar_result=[session, None])  # session lookup, then no existing answer
        record_answer(
            db,
            user_id=session.user_id,
            session_id=session.id,
            question_id="q4",
            question_type="choice",
            level=5,
            prompt="?",
            user_answer="B",
            correct_answer="A",
            is_correct=False,
            time_spent_ms=2000,
        )
        assert session.correct_count == 3, (
            f"Expected correct_count to stay at 3 for a wrong answer, got {session.correct_count}"
        )

    def test_rerecord_same_question_updates_in_place(self):
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=None,
            correct_count=1,
        )
        existing_answer = SimpleNamespace(
            is_correct=False,
            user_answer="B",
            correct_answer="A",
            time_spent_ms=1000,
        )
        db = _MockDB(scalar_result=[session, existing_answer])
        record_answer(
            db,
            user_id=session.user_id,
            session_id=session.id,
            question_id="q4",
            question_type="choice",
            level=5,
            prompt="?",
            user_answer="A",
            correct_answer="A",
            is_correct=True,
            time_spent_ms=2000,
        )
        # Changed answer adjusts correct_count by the delta, no duplicate row
        assert session.correct_count == 2
        assert existing_answer.user_answer == "A"
        assert existing_answer.is_correct is True
        assert len(db.added_objects) == 0


class TestCompleteSession:
    def test_idempotent_on_already_completed(self):
        original_completed_at = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=original_completed_at,
        )
        db = _MockDB(scalar_result=session)
        result = complete_session(db, user_id=session.user_id, session_id=session.id)
        # completed_at should be preserved
        assert result.completed_at == original_completed_at

    def test_stamps_completed_at_when_in_progress(self):
        session = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            completed_at=None,
        )
        db = _MockDB(scalar_result=session)
        result = complete_session(db, user_id=session.user_id, session_id=session.id)
        assert result.completed_at is not None


class TestCreateSession:
    def test_creates_with_correct_aggregates(self):
        db = _MockDB(scalar_result=None)
        payload = GrammarSessionStart(
            level=8,
            total_questions=10,
            choice_questions=6,
            fill_in_questions=4,
            question_ids=[f"q{i+1}" for i in range(10)],
        )
        user_id = uuid4()
        session = create_session(db, user_id=user_id, payload=payload)
        # Aggregates must be passed through unchanged
        assert session.level == 8
        assert session.total_questions == 10
        assert session.choice_questions == 6
        assert session.fill_in_questions == 4
        # New session starts with 0 correct
        assert session.correct_count == 0


# --- Mock DB helper ---------------------------------------------------------

class _MockDB:
    """Tiny in-memory mock for SQLAlchemy Session, enough for these tests.

    Supports:
      - db.scalar(stmt) → returns the configured scalar_result; when a list
        is given, each call pops the next result (functions doing multiple
        scalar lookups configure one entry per query, in order)
      - db.add(obj) → records into added_objects
      - db.commit() → no-op
      - db.refresh(obj) → no-op (objects are real SimpleNamespaces)
    """

    def __init__(self, scalar_result):
        if isinstance(scalar_result, list):
            self._scalar_results = list(scalar_result)
        else:
            self._scalar_results = [scalar_result]
        self.added_objects: list[object] = []

    def scalar(self, _stmt):
        if len(self._scalar_results) > 1:
            return self._scalar_results.pop(0)
        return self._scalar_results[0]

    def add(self, obj):
        self.added_objects.append(obj)

    def commit(self):
        pass

    def refresh(self, _obj):
        pass
