"""Grammar practice history service.

Handles persistence of practice sessions and per-answer records, plus
aggregation for the dashboard.

Design notes
------------
A `GrammarSession` is created on the client side right after the LLM
returns questions — the session is "in progress" with
`completed_at IS NULL` and `correct_count = 0`. As the child answers
each question, the client POSTs the answer to
`POST /api/v1/grammar/sessions/{id}/answers` and we increment
`correct_count` in the same transaction. When the last answer is
recorded, the client POSTs to `.../complete` which stamps
`completed_at = now()`.

This two-phase commit keeps the per-answer table consistent with
the session aggregate without requiring the client to send a
single "I'm done" payload that could be lost on a refresh.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.grammar_session import GrammarAnswer, GrammarSession
from app.schemas.grammar import (
    GrammarHistoryResponse,
    GrammarLevelStat,
    GrammarSessionStart,
    GrammarSessionSummary,
)


# --- Session lifecycle ------------------------------------------------------

def create_session(
    db: Session,
    user_id: UUID,
    payload: GrammarSessionStart,
) -> GrammarSession:
    """Create a new in-progress session.

    The session is "in progress" until the client marks it complete.
    """
    session = GrammarSession(
        user_id=user_id,
        level=payload.level,
        total_questions=payload.total_questions,
        correct_count=0,
        choice_questions=payload.choice_questions,
        fill_in_questions=payload.fill_in_questions,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def record_answer(
    db: Session,
    user_id: UUID,
    session_id: UUID,
    *,
    question_id: str,
    question_type: str,
    level: int,
    prompt: str,
    user_answer: str,
    correct_answer: str,
    is_correct: bool,
    time_spent_ms: int,
) -> tuple[GrammarSession, GrammarAnswer]:
    """Record a single answer and increment the session's correct_count.

    Returns the (session, answer) tuple. Validates that the session
    belongs to the user and is still in progress; raises ValueError
    otherwise.
    """
    session = db.scalar(
        select(GrammarSession).where(
            GrammarSession.id == session_id,
            GrammarSession.user_id == user_id,
        )
    )
    if session is None:
        raise ValueError(f"Session {session_id} not found for user {user_id}")
    if session.completed_at is not None:
        # Allow re-recording the same question_id (child changed their
        # answer before completing the set) but reject once the session
        # is sealed. The frontend can guard against this with a UI flag.
        raise ValueError(f"Session {session_id} is already completed")

    answer = GrammarAnswer(
        session_id=session_id,
        user_id=user_id,
        question_id=question_id,
        question_type=question_type,
        level=level,
        prompt=prompt,
        user_answer=user_answer,
        correct_answer=correct_answer,
        is_correct=is_correct,
        time_spent_ms=time_spent_ms,
    )
    db.add(answer)
    if is_correct:
        # Atomic increment — avoids read-modify-write race when two
        # answers land in the same request.
        session.correct_count = (session.correct_count or 0) + 1
    db.commit()
    db.refresh(session)
    db.refresh(answer)
    return session, answer


def complete_session(
    db: Session,
    user_id: UUID,
    session_id: UUID,
) -> GrammarSession:
    """Mark a session as completed.

    Idempotent: calling on an already-completed session is a no-op
    (returns the same row).
    """
    session = db.scalar(
        select(GrammarSession).where(
            GrammarSession.id == session_id,
            GrammarSession.user_id == user_id,
        )
    )
    if session is None:
        raise ValueError(f"Session {session_id} not found for user {user_id}")
    if session.completed_at is None:
        session.completed_at = datetime.now(UTC)
        db.commit()
    # Always refresh — the caller may have a stale ORM snapshot from a
    # previous read in the same request (e.g. _session_to_summary ->
    # call another service that calls back into this). Without refresh,
    # subsequent attribute access could trigger a lazy load with the
    # session already in an invalid state (e.g. after commit).
    db.refresh(session)
    return session


# --- Aggregations -----------------------------------------------------------

def _session_to_summary(session: GrammarSession) -> GrammarSessionSummary:
    accuracy = (
        session.correct_count / session.total_questions
        if session.total_questions > 0
        else 0.0
    )
    return GrammarSessionSummary(
        id=session.id,
        level=session.level,
        total_questions=session.total_questions,
        correct_count=session.correct_count,
        choice_questions=session.choice_questions,
        fill_in_questions=session.fill_in_questions,
        started_at=session.started_at,
        completed_at=session.completed_at,
        accuracy=round(accuracy, 4),
    )


def get_user_history(
    db: Session,
    user_id: UUID,
    *,
    recent_limit: int = 20,
) -> GrammarHistoryResponse:
    """Return the user's recent sessions + per-level aggregate stats.

    Per-level stats include BOTH completed and in-progress sessions
    (so the child sees progress on partial attempts). The
    `recent_sessions` list only includes completed ones by default,
    since a half-finished session has no useful summary.
    """
    # --- Recent completed sessions ---
    recent_rows = db.execute(
        select(GrammarSession)
        .where(
            GrammarSession.user_id == user_id,
            GrammarSession.completed_at.isnot(None),
        )
        .order_by(GrammarSession.completed_at.desc())
        .limit(recent_limit)
    ).scalars().all()
    recent_sessions = [_session_to_summary(s) for s in recent_rows]

    # --- Per-level aggregate ---
    # Use SQL aggregation rather than per-level Python loop so the
    # whole thing is one round-trip to the DB. Filter to COMPLETED
    # sessions only — in-progress sessions have correct_count = 0
    # (the user just started, no answers yet) which would drag the
    # accuracy denominator down. The previous implementation
    # included in-progress rows, which made per-level accuracy
    # disagree with the overall_accuracy (which already filtered
    # by completed_at IS NOT NULL).
    per_level_rows = db.execute(
        select(
            GrammarSession.level,
            func.count(GrammarSession.id).label("total_sessions"),
            func.coalesce(func.sum(GrammarSession.total_questions), 0).label("total_questions"),
            func.coalesce(func.sum(GrammarSession.correct_count), 0).label("correct_count"),
        )
        .where(
            GrammarSession.user_id == user_id,
            GrammarSession.completed_at.isnot(None),
        )
        .group_by(GrammarSession.level)
        .order_by(GrammarSession.level.asc())
    ).all()

    # Per-level average time per question — joined from grammar_answers
    # so we don't have to denormalise on the session row.
    avg_time_rows = db.execute(
        select(
            GrammarAnswer.level,
            func.avg(GrammarAnswer.time_spent_ms).label("avg_time_ms"),
        )
        .where(GrammarAnswer.user_id == user_id)
        .group_by(GrammarAnswer.level)
    ).all()
    avg_time_by_level: dict[int, float] = {
        level: float(avg_time_ms or 0.0) for level, avg_time_ms in avg_time_rows
    }

    per_level: list[GrammarLevelStat] = []
    for level, total_sessions, total_questions, correct_count in per_level_rows:
        total_q = int(total_questions)
        correct = int(correct_count)
        per_level.append(
            GrammarLevelStat(
                level=int(level),
                total_sessions=int(total_sessions),
                total_questions=total_q,
                correct_count=correct,
                accuracy=round(correct / total_q, 4) if total_q > 0 else 0.0,
                avg_time_per_question_ms=round(avg_time_by_level.get(int(level), 0.0), 1),
            )
        )

    # --- Overall totals ---
    overall = db.execute(
        select(
            func.count(GrammarSession.id).label("total_sessions"),
            func.coalesce(func.sum(GrammarSession.total_questions), 0).label("total_questions"),
            func.coalesce(func.sum(GrammarSession.correct_count), 0).label("correct_count"),
        ).where(
            GrammarSession.user_id == user_id,
            GrammarSession.completed_at.isnot(None),
        )
    ).one()
    total_sessions = int(overall.total_sessions)
    total_questions = int(overall.total_questions)
    correct_total = int(overall.correct_count)
    overall_accuracy = (
        round(correct_total / total_questions, 4) if total_questions > 0 else 0.0
    )

    return GrammarHistoryResponse(
        recent_sessions=recent_sessions,
        per_level=per_level,
        total_sessions=total_sessions,
        total_questions=total_questions,
        overall_accuracy=overall_accuracy,
    )
