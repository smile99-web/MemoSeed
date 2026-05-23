from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.user import User
from app.schemas.memory import MemoryScheduleResponse, MemoryStateRead
from app.schemas.review import MistakeLogRead, ReviewLogCreate, ReviewLogRead
from app.services.memory_scheduler import calculate_current_forget_risk, calculate_review_priority, schedule_memory_review

router = APIRouter()


@router.get("/queue", response_model=list[MemoryStateRead])
def get_review_queue(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[MemoryStateRead]:
    now = datetime.now(UTC)
    memory_states = list(
        db.scalars(
            select(MemoryState)
            .join(LearningItem, LearningItem.id == MemoryState.learning_item_id)
            .where(LearningItem.user_id == current_user.id, MemoryState.next_review_at <= now)
            .order_by(MemoryState.next_review_at.asc())
        ).all()
    )
    memory_states.sort(key=lambda memory_state: (-calculate_review_priority(memory_state, now), memory_state.next_review_at))
    response_items: list[MemoryStateRead] = []
    for memory_state in memory_states:
        current_forget_risk = calculate_current_forget_risk(memory_state, now)
        response_items.append(
            MemoryStateRead.model_validate(memory_state).model_copy(
                update={
                    "forget_risk": current_forget_risk,
                    "memory_strength": round(1 - current_forget_risk, 2),
                }
            )
        )
    return response_items


@router.post("/logs", response_model=MemoryScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_review_log(
    payload: ReviewLogCreate,
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
