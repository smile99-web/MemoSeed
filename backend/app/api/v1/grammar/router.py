"""Grammar practice endpoints.

POST /api/v1/grammar/generate
  Body: {"level": 1-10}
  Returns: {level, questions: [10 GrammarQuestion]}

GET /api/v1/grammar/levels
  Returns: {min, max, distribution: [{level, choice, fill_in_blank}, ...]}
  Useful for the frontend to render the difficulty selector without
  hard-coding the 1-10 range or the choice/fill mix.

POST /api/v1/grammar/sessions
  Body: {level, total_questions, choice_questions, fill_in_questions, question_ids}
  Returns: GrammarSessionSummary (in-progress)

POST /api/v1/grammar/sessions/{session_id}/answers
  Body: GrammarAnswerSubmission
  Returns: {session: GrammarSessionSummary, answer_id: UUID}

POST /api/v1/grammar/sessions/{session_id}/complete
  Marks the session as completed (idempotent).
  Returns: GrammarSessionSummary

GET /api/v1/grammar/history
  Returns: GrammarHistoryResponse (recent sessions + per-level stats)
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.grammar import (
    GrammarAnswerSubmission,
    GrammarHistoryResponse,
    GrammarQuestionSet,
    GrammarQuestionSetRequest,
    GrammarSessionStart,
    GrammarSessionSummary,
)
from app.services import grammar_history
from app.services.grammar_generator import (
    MAX_LEVEL,
    MIN_LEVEL,
    QUESTIONS_PER_SET,
    generate_grammar_questions,
    question_type_distribution,
)
from app.services.llm_translation import LlmTranslationSettings
from app.services.secure_model_settings import get_private_model_settings


logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/levels")
def list_grammar_levels(
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Return the supported difficulty range and the question-type mix per level.

    The frontend calls this on page load to render the difficulty selector
    and to know how many choice vs fill-in questions to expect at each
    level. Keeping this server-side ensures the frontend and backend
    agree on the distribution even if the rules change later.
    """
    distribution = [
        {
            "level": level,
            "choice": question_type_distribution(level)[0],
            "fill_in_blank": question_type_distribution(level)[1],
        }
        for level in range(MIN_LEVEL, MAX_LEVEL + 1)
    ]
    return {
        "min": MIN_LEVEL,
        "max": MAX_LEVEL,
        "questions_per_set": QUESTIONS_PER_SET,
        "distribution": distribution,
    }


@router.post("/generate", response_model=GrammarQuestionSet)
def generate_grammar_question_set(
    payload: GrammarQuestionSetRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GrammarQuestionSet:
    """Generate a fresh set of 10 grammar questions at the requested level.

    The LLM is invoked with the user's configured provider/model
    (settings → model). On LLM error or invalid response, returns
    502 Bad Gateway with a clear message — the client should
    surface this as "出题失败，请重试" rather than crashing the page.
    """
    settings = _resolve_user_llm_settings(db, current_user.id)
    try:
        question_set = generate_grammar_questions(
            level=payload.level,
            settings=settings,
        )
    except ValueError as exc:
        # Bad LLM response shape — 502 is more accurate than 400
        # because the *input* is valid; the *upstream* is the problem.
        logger.warning("Grammar generation failed for user=%s level=%d: %s",
                       current_user.id, payload.level, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 返回的题目格式无效: {exc}",
        ) from exc

    return question_set


@router.post("/sessions", response_model=GrammarSessionSummary, status_code=status.HTTP_201_CREATED)
def start_grammar_session(
    payload: GrammarSessionStart,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GrammarSessionSummary:
    """Create a new in-progress grammar practice session.

    Call this immediately after `POST /grammar/generate` returns the
    question set. The session id is used by the client to record
    each answer and to mark the session complete.
    """
    session = grammar_history.create_session(db, current_user.id, payload)
    return grammar_history._session_to_summary(session)


@router.post("/sessions/{session_id}/answers", response_model=GrammarSessionSummary)
def submit_grammar_answer(
    session_id: UUID,
    payload: GrammarAnswerSubmission,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GrammarSessionSummary:
    """Record one answer and increment the session's correct_count if applicable.

    Returns the updated session summary so the client can show a
    running total without an extra round-trip.
    """
    try:
        session, _answer = grammar_history.record_answer(
            db,
            current_user.id,
            session_id,
            question_id=payload.question_id,
            question_type=payload.question_type,
            level=payload.level,
            prompt=payload.prompt,
            user_answer=payload.user_answer,
            correct_answer=payload.correct_answer,
            is_correct=payload.is_correct,
            time_spent_ms=payload.time_spent_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return grammar_history._session_to_summary(session)


@router.post("/sessions/{session_id}/complete", response_model=GrammarSessionSummary)
def complete_grammar_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GrammarSessionSummary:
    """Mark a session as completed. Idempotent."""
    try:
        session = grammar_history.complete_session(db, current_user.id, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return grammar_history._session_to_summary(session)


@router.get("/history", response_model=GrammarHistoryResponse)
def get_grammar_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GrammarHistoryResponse:
    """Return the user's recent completed sessions + per-level aggregate stats."""
    return grammar_history.get_user_history(db, current_user.id)


def _resolve_user_llm_settings(db: Session, user_id) -> LlmTranslationSettings:
    """Build an LlmTranslationSettings from the user's stored private model config.

    Falls back to the global app_settings (env) when the user has not
    configured their own model. Mirrors the pattern used in the
    learning router (see build_llm_translation_settings).
    """
    from app.core.config import settings as app_settings
    from app.services.llm_translation import DEFAULT_LLM_TRANSLATION_SETTINGS
    from app.utils import string_setting

    stored = get_private_model_settings(db, user_id)
    provider = (
        string_setting(stored, "llmProvider")
        or app_settings.ai_provider
        or DEFAULT_LLM_TRANSLATION_SETTINGS.provider
    )
    base_url = (
        string_setting(stored, "llmBaseUrl")
        or app_settings.ai_base_url
        or DEFAULT_LLM_TRANSLATION_SETTINGS.base_url
    )
    model = (
        string_setting(stored, "llmModel")
        or app_settings.ai_model
        or DEFAULT_LLM_TRANSLATION_SETTINGS.model
    )
    api_key = string_setting(stored, "llmApiKey") or app_settings.ai_api_key

    return LlmTranslationSettings(
        provider=str(provider),
        base_url=str(base_url),
        model=str(model),
        api_key=api_key,
    )
