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
    consecutive_correct_count: int = 0
    consecutive_error_count: int = 0
    recall_correct_count: int = 0
    hinted_correct_count: int = 0
    preview_correct_count: int = 0
    context_correct_count: int = 0
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


class WordMasterySummary(BaseModel):
    word: str
    status: str
    status_label: str
    memory_strength: float
    forget_risk: float
    priority_score: float
    review_count: int
    mistake_count: int
    consecutive_correct_count: int = 0
    consecutive_error_count: int = 0
    recall_correct_count: int = 0
    hinted_correct_count: int = 0
    preview_correct_count: int = 0
    context_correct_count: int = 0
    hidden_recall_correct_count: int = 0
    no_hint_correct_date_count: int = 0
    dominant_error_type: str | None = None
    review_reason: str
    review_status_note: str
    recommended_task: str
    scheduled_task_count: int = 0
    interval_days: float
    next_review_at: datetime | None


class ReviewBucket(BaseModel):
    label: str
    count: int


class StudyTimeLogRequest(BaseModel):
    course_id: UUID | None = None
    duration_seconds: int = Field(ge=1, le=3600)


class CourseCompletionRequest(BaseModel):
    course_id: UUID
    duration_seconds: int = Field(default=0, ge=0, le=86400)
    correct_word_count: int = Field(default=0, ge=0)


class CourseProgressStats(BaseModel):
    course_id: UUID
    completed_count: int
    total_duration_seconds: int
    total_correct_word_count: int
    last_completed_at: datetime | None


class StudyTimeSummary(BaseModel):
    today_seconds: int
    week_seconds: int
    month_seconds: int
    year_seconds: int
    total_seconds: int


class MemoryDashboardResponse(BaseModel):
    total_items: int
    total_words: int
    mastered_words: int
    learning_words: int
    weak_words: int
    due_now_count: int
    overdue_count: int
    average_memory_strength: float
    average_forget_risk: float
    average_interval_days: float
    total_reviews: int
    correct_reviews: int
    accuracy_rate: float
    total_mistakes: int
    unresolved_mistakes: int
    fsrs_parameters_source: str
    fsrs_min_training_reviews: int
    fsrs_training_review_count: int
    fsrs_training_pair_count: int
    fsrs_fitted_at: datetime | None
    next_review_at: datetime | None
    study_time: StudyTimeSummary
    review_buckets: list[ReviewBucket]
    weakest_words: list[WordMasterySummary]
    strongest_words: list[WordMasterySummary]


class FsrsFitResponse(BaseModel):
    fitted_at: datetime
    training_review_count: int
    training_pair_count: int
    accuracy_rate: float
    weights: list[float]


class ReviewForecastToday(BaseModel):
    remaining_count: int
    remaining_minutes_low: int
    remaining_minutes_high: int


class ReviewForecastTomorrow(BaseModel):
    due_count: int
    estimated_minutes: list[int]
    high_risk_count: int


class ReviewForecastWeek(BaseModel):
    due_count: int
    daily_average: float
    peak_day: str
    peak_count: int


class ReviewForecastEfficiency(BaseModel):
    avg_seconds_per_item: int
    recent_accuracy: float
    avg_daily_minutes: int


class ReviewForecastResponse(BaseModel):
    today: ReviewForecastToday
    tomorrow: ReviewForecastTomorrow
    week: ReviewForecastWeek
    load_level: str
    suggested_actions: list[str]
    efficiency: ReviewForecastEfficiency


# Points system schemas

class PointsAwardRequest(BaseModel):
    points_change: int
    reason: str
    detail: str | None = None
    learning_item_id: UUID | None = None

class PointsLogEntry(BaseModel):
    points_changed: int
    reason: str
    detail: str | None = None
    created_at: str | None = None

class PointsSummaryResponse(BaseModel):
    total_points: int
    level: int
    level_label: str
    today_points: int
    next_level_points: int | None = None
    next_level_progress_pct: int
    recent_logs: list[PointsLogEntry]


class TodayProgressRow(BaseModel):
    planned: int
    completed: int
    remaining: int


class TodayProgressReviews(BaseModel):
    planned: int
    completed_items: int
    completed_reviews: int
    remaining: int


class TodayProgressResponse(BaseModel):
    review: TodayProgressReviews
    new_words: TodayProgressRow
    mistakes: TodayProgressRow
