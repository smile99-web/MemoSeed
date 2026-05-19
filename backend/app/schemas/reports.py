from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DailyPlanRead(BaseModel):
    id: UUID
    user_id: UUID
    plan_date: date
    warmup_review_minutes: int
    new_learning_minutes: int
    sentence_training_minutes: int
    mistake_reinforcement_minutes: int
    new_word_limit: int
    new_phrase_limit: int
    strategy: dict[str, object]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AiDailyReportRead(BaseModel):
    id: UUID
    user_id: UUID
    report_date: date
    accuracy_rate: float = Field(ge=0.0, le=1.0)
    spelling_error_rate: float = Field(ge=0.0, le=1.0)
    sentence_error_rate: float = Field(ge=0.0, le=1.0)
    study_duration_minutes: int
    review_backlog_count: int
    high_forget_risk_count: int
    summary: str
    next_day_strategy: dict[str, object]
    created_at: datetime

    model_config = {"from_attributes": True}
