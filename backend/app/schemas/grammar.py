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

from typing import Literal

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
    """User's answer to a single question — used for scoring on the client.

    The backend intentionally does NOT persist submissions; grammar is a
    lightweight LLM-driven drill, not a tracked learning unit. Stats live
    on the client (sessionStorage) if the parent wants to see accuracy.
    """

    question_id: str
    answer: str
