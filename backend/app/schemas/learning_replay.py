from datetime import date as date_type, datetime
from typing import Optional

from pydantic import BaseModel, Field


class HeatmapDay(BaseModel):
    date: str
    minutes: float
    events: int
    color: str


class HeatmapResponse(BaseModel):
    year: int
    days: list[HeatmapDay]
    total_minutes: float
    active_days: int


class MinuteBreakdown(BaseModel):
    minute: int
    spelling: int
    english_to_chinese: int
    chinese_to_english: int
    phrase: int
    sentence: int
    total: int
    correct: int
    incorrect: int
    accuracy: float
    study_seconds: float = 0.0


class HourBlock(BaseModel):
    hour: int
    label: str
    minutes: list[MinuteBreakdown]
    modes: list[dict] = []


class ModeCount(BaseModel):
    mode: str
    label: str
    count: int


class MinuteModeRow(BaseModel):
    minute: int
    modes: dict[str, int]


class DayDetailResponse(BaseModel):
    date: str
    study_minutes: float
    total_events: int
    accuracy: float
    mistake_count: int
    hours: list[HourBlock]
    day_modes: list[ModeCount] = []


class HourDetailResponse(BaseModel):
    date: str
    hour: int
    minutes: list[MinuteBreakdown]
    minute_modes: list[MinuteModeRow] = []


class LearningEventLog(BaseModel):
    id: str
    occurred_at: Optional[str] = None
    english_text: str
    chinese_text: Optional[str] = None
    response_text: Optional[str] = None
    is_correct: Optional[bool] = None
    score: Optional[int] = None
    review_mode: Optional[str] = None
    duration_ms: int
    error_type: Optional[str] = None


class MinuteEventsResponse(BaseModel):
    date: str
    hour: int
    minute: int
    events: list[LearningEventLog]
