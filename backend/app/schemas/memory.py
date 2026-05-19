from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.review import MistakeLogRead, ReviewLogRead


class MemoryStateRead(BaseModel):
    id: UUID
    learning_item_id: UUID
    interval_days: int
    ease_factor: float
    memory_strength: float
    forget_risk: float
    repetition_count: int
    lapse_count: int
    last_reviewed_at: datetime | None
    next_review_at: datetime

    model_config = {"from_attributes": True}


class ReviewScoreRequest(BaseModel):
    learning_item_id: UUID
    score: int = Field(ge=0, le=5)
    review_mode: str = Field(min_length=1, max_length=32)
    response_text: str | None = None
    duration_seconds: int = Field(default=0, ge=0)


class MemoryScheduleResponse(BaseModel):
    memory_state: MemoryStateRead
    review_log: ReviewLogRead
    mistake_log: MistakeLogRead | None
