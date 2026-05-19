from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.user import User
from app.schemas.memory import MemoryScheduleResponse, MemoryStateRead, ReviewScoreRequest
from app.schemas.review import MistakeLogRead, ReviewLogRead
from app.services.memory_scheduler import schedule_memory_review

router = APIRouter()


@router.get("/states/{learning_item_id}", response_model=MemoryStateRead)
def get_memory_state(
    learning_item_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MemoryStateRead:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    memory_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == learning_item_id))
    if memory_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory state not found")

    return MemoryStateRead.model_validate(memory_state)


@router.post("/schedule", response_model=MemoryScheduleResponse)
def schedule_next_review(
    payload: ReviewScoreRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MemoryScheduleResponse:
    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=payload.learning_item_id,
        score=payload.score,
        review_mode=payload.review_mode,
        response_text=payload.response_text,
        duration_seconds=payload.duration_seconds,
    )

    return MemoryScheduleResponse(
        memory_state=MemoryStateRead.model_validate(result.memory_state),
        review_log=ReviewLogRead.model_validate(result.review_log),
        mistake_log=MistakeLogRead.model_validate(result.mistake_log) if result.mistake_log is not None else None,
    )
