from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LearningItemBase(BaseModel):
    course_id: UUID | None = None
    item_type: str = Field(pattern="^(word|phrase|sentence)$")
    english_text: str = Field(min_length=1)
    chinese_text: str = Field(min_length=1)
    phonetic: str | None = None
    difficulty_level: int = Field(default=1, ge=1, le=5)
    source: str | None = None


class LearningItemCreate(LearningItemBase):
    pass


class LearningItemRead(LearningItemBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ImportSkippedItem(BaseModel):
    english_text: str
    reason: str


class LearningImportResponse(BaseModel):
    imported_count: int
    skipped_count: int
    total_rows: int
    items: list[LearningItemRead]
    skipped_items: list[ImportSkippedItem]


class LearningTranslationRequest(BaseModel):
    english_text: str = Field(min_length=1)
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class LearningTranslationResponse(BaseModel):
    english_text: str
    chinese_text: str


class WordMistakeLogRequest(BaseModel):
    learning_item_id: UUID
    expected_word: str = Field(min_length=1)
    actual_word: str = ""
    error_type: str = Field(default="spelling", max_length=32)


class WordMistakeLogResponse(BaseModel):
    logged_count: int


class WordReviewRequest(BaseModel):
    learning_item_id: UUID
    word: str = Field(min_length=1)
    score: int = Field(ge=0, le=5)
    review_mode: str = Field(min_length=1, max_length=32)
    response_text: str | None = None
    duration_seconds: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=32)


class WordReviewResponse(BaseModel):
    learning_item_id: UUID
    word: str


class DynamicSentenceRequest(BaseModel):
    course_id: UUID | None = None
    current_sentence: str = ""
    mistaken_words: list[str] = Field(default_factory=list)
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class DynamicSentenceResponse(BaseModel):
    english_text: str
    chinese_text: str
    focus_words: list[str]
    known_words: list[str]
    weak_words: list[str]


class LearningEncouragementRequest(BaseModel):
    course_name: str = Field(default="本课", max_length=120)
    duration_seconds: int = Field(default=0, ge=0)
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class LearningEncouragementResponse(BaseModel):
    chinese_text: str
    english_text: str
