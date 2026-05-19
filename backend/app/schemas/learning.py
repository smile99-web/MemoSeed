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
    llm_base_url: str | None = None
    llm_model: str | None = None


class LearningTranslationResponse(BaseModel):
    english_text: str
    chinese_text: str
