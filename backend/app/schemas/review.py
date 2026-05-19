from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReviewLogCreate(BaseModel):
    learning_item_id: UUID
    review_mode: str = Field(min_length=1, max_length=32)
    score: int = Field(ge=0, le=5)
    is_correct: bool | None = None
    response_text: str | None = None
    duration_seconds: int = Field(default=0, ge=0)


class ReviewLogRead(BaseModel):
    id: UUID
    user_id: UUID
    learning_item_id: UUID
    review_mode: str
    score: int
    is_correct: bool
    response_text: str | None
    duration_seconds: int
    reviewed_at: datetime

    model_config = {"from_attributes": True}


class MistakeLogRead(BaseModel):
    id: UUID
    user_id: UUID
    learning_item_id: UUID
    mistake_type: str
    expected_answer: str
    actual_answer: str
    is_resolved: bool
    occurred_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
