"""Schemas for the English grammar practice module.

Grammar questions are LLM-generated on demand. Each question has:
  - type="choice"      → multiple choice (4 options)
  - type="fill_in_blank" → user types the missing word(s)

Distribution by difficulty (level 1-10):
  - 1-5 : 100% multiple choice (single-select)
  - 6-10: 60% multiple choice + 40% fill-in-the-blank

The split is defined in grammar_generator.question_type_distribution so the
API and frontend can agree on what to expect for any given level.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


GrammarQuestionType = Literal["choice", "fill_in_blank"]


class GrammarQuestion(BaseModel):
    """A single English grammar question."""

    id: str = Field(..., description="Stable client-side id (use the LLM-provided value or hash of prompt+index).")
    type: GrammarQuestionType = Field(..., description="Question type. 'choice' has 4 options; 'fill_in_blank' has none.")
    level: int = Field(..., ge=1, le=10, description="Difficulty level (1-10).")
    prompt: str = Field(..., description="The question text shown to the child. For fill_in_blank, include '____' for the missing span.")
    translation: str | None = Field(default=None, description="Optional Chinese translation / context for the prompt.")
    options: list[str] | None = Field(default=None, description="4 options for 'choice' questions. None for 'fill_in_blank'.")
    answer: str = Field(..., description="Correct answer. For choice, the exact option text. For fill_in_blank, the missing word(s).")
    explanation: str = Field(..., description="Short grammar-rule explanation, in Chinese (parent-friendly).")


class GrammarQuestionSet(BaseModel):
    """A batch of 10 grammar questions at a given difficulty."""

    level: int = Field(..., ge=1, le=10)
    questions: list[GrammarQuestion] = Field(..., min_length=1, max_length=20)


class GrammarQuestionSetRequest(BaseModel):
    """Request body for generating a question set."""

    level: int = Field(..., ge=1, le=10, description="Difficulty level 1-10. 1 = easiest, 10 = hardest.")


class GrammarAnswerSubmission(BaseModel):
    """User's answer to a single question — submitted as they go.

    Stored on the server in `grammar_answers` for analytics
    (per-question-type accuracy, time-per-question trends, etc).
    """

    question_id: str
    question_type: GrammarQuestionType
    level: int = Field(..., ge=1, le=10)
    prompt: str
    user_answer: str
    correct_answer: str
    is_correct: bool
    time_spent_ms: int = Field(default=0, ge=0)


class GrammarSessionStart(BaseModel):
    """Client → server: declare a new session right after the LLM returns questions."""

    level: int = Field(..., ge=1, le=10)
    total_questions: int = Field(..., ge=1, le=20)
    choice_questions: int = Field(..., ge=0)
    fill_in_questions: int = Field(..., ge=0)
    question_ids: list[str] = Field(..., min_length=1, max_length=20)


class GrammarSessionSummary(BaseModel):
    """A completed (or in-progress) grammar practice session."""

    id: UUID
    level: int
    total_questions: int
    correct_count: int
    choice_questions: int
    fill_in_questions: int
    started_at: datetime
    completed_at: datetime | None
    accuracy: float = Field(..., description="correct_count / total_questions, 0.0-1.0")


class GrammarLevelStat(BaseModel):
    """Aggregated stats for a single difficulty level."""

    level: int
    total_sessions: int
    total_questions: int
    correct_count: int
    accuracy: float
    avg_time_per_question_ms: float


class GrammarHistoryResponse(BaseModel):
    """Recent sessions + per-level aggregate stats."""

    recent_sessions: list[GrammarSessionSummary] = Field(default_factory=list)
    per_level: list[GrammarLevelStat] = Field(default_factory=list)
    total_sessions: int
    total_questions: int
    overall_accuracy: float
