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


class StrugglingWordItem(BaseModel):
    word: str
    priority_score: float
    memory_strength: float
    mistake_count: int
    error_type: str | None = None
    recommendation: str


class PerWordBreakdownItem(BaseModel):
    word: str
    reviews: int
    correct: int


class PerTypeBreakdownItem(BaseModel):
    mode: str
    label: str
    reviews: int
    correct: int
    kind: str  # "spelling" or "choice"


class DailyReportResponse(BaseModel):
    report_date: date
    review_count: int
    correct_count: int
    accuracy_rate: float
    study_duration_minutes: int
    words_practiced: int
    new_words_practiced: int
    per_word_breakdown: list[PerWordBreakdownItem]
    per_type_breakdown: list[PerTypeBreakdownItem]
    spelling_total: int
    spelling_correct: int
    choice_total: int
    choice_correct: int
    mistake_count: int
    streak_days: int
    struggling_words: list[StrugglingWordItem]
    summary: str
    next_day_strategy: dict[str, object]


class TodayPlanItem(BaseModel):
    task_type: str
    task_description: str
    estimated_minutes: int
    item_count: int


class TodayPlanResponse(BaseModel):
    plan_date: date
    total_minutes: int
    due_review_count: int
    new_words_ready: int
    unresolved_mistake_count: int
    items: list[TodayPlanItem]
    time_budget: dict[str, int]


class WordHistoryEvent(BaseModel):
    timestamp: datetime
    event_type: str
    score: int | None = None
    is_correct: bool | None = None
    error_type: str | None = None
    memory_strength: float | None = None
    detail: str | None = None


class WordHistoryResponse(BaseModel):
    word: str
    events: list[WordHistoryEvent]
    current_strength: float
    current_risk: float
    review_count: int
    mistake_count: int


class RetentionBin(BaseModel):
    elapsed_days_label: str
    elapsed_days: float
    total_reviews: int
    correct_reviews: int
    recall_rate: float


class RetentionCurveResponse(BaseModel):
    bins: list[RetentionBin]
    course_id: UUID | None = None


class ErrorBreakdownItem(BaseModel):
    error_type: str
    error_label: str
    this_week_count: int
    last_week_count: int
    trend: str


class ErrorBreakdownResponse(BaseModel):
    items: list[ErrorBreakdownItem]
    total_this_week: int
    total_last_week: int


class StudyStreakResponse(BaseModel):
    current_streak_days: int
    longest_streak_days: int
    streak_start_date: date | None = None
    total_study_days: int
    today_studied: bool
