"""Grammar practice session and answer models.

A `GrammarSession` is one practice set (10 questions at a given level).
A `GrammarAnswer` is one question's response within a session. The
session row carries the aggregate correct_count so the dashboard can
read it without joining the per-answer table; the per-answer rows
are kept for future analytics (which question types the child gets
wrong, time-per-question trends, etc.).

See `database/init/010_grammar_sessions.sql` for the schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GrammarSession(Base):
    __tablename__ = "grammar_sessions"
    __table_args__ = (
        CheckConstraint("level >= 1 AND level <= 10", name="ck_grammar_sessions_level"),
        CheckConstraint("total_questions > 0", name="ck_grammar_sessions_total_questions"),
        CheckConstraint("correct_count >= 0", name="ck_grammar_sessions_correct_count"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    choice_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fill_in_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GrammarAnswer(Base):
    __tablename__ = "grammar_answers"
    __table_args__ = (
        CheckConstraint("question_type IN ('choice', 'fill_in_blank')", name="ck_grammar_answers_type"),
        CheckConstraint("level >= 1 AND level <= 10", name="ck_grammar_answers_level"),
        CheckConstraint("time_spent_ms >= 0", name="ck_grammar_answers_time"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("grammar_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_type: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_answer: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    time_spent_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
