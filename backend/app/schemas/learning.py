from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LearningItemBase(BaseModel):
    course_id: UUID | None = None
    item_type: str = Field(pattern="^(word|phrase|sentence)$")
    english_text: str = Field(min_length=1)
    chinese_text: str = Field(min_length=1)
    phonetic: str | None = None
    syllables: list[str] | None = None
    grapheme_phoneme_map: dict[str, str] | None = None
    difficulty_level: int = Field(default=1, ge=1, le=5)
    sort_order: int = 0
    unit_label: str | None = None
    source: str | None = None


class LearningItemCreate(LearningItemBase):
    pass


class LearningItemRead(LearningItemBase):
    id: UUID
    user_id: UUID
    review_task_id: UUID | None = None
    review_task_type: str | None = None
    review_prompt: str | None = None
    review_choices: list[str] = Field(default_factory=list)
    review_answer: str | None = None
    focus_words: list[str] = Field(default_factory=list)
    source_item_id: UUID | None = None
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


class WordTranslationsRequest(BaseModel):
    words: list[str] = Field(min_length=1, max_length=80)
    course_id: UUID | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class WordTranslationsResponse(BaseModel):
    translations: dict[str, str]


class CourseCacheRebuildRequest(BaseModel):
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class CourseCacheStatusSummary(BaseModel):
    total_items: int
    sentence_translations_ready: int
    sentence_english_audio_ready: int
    sentence_chinese_audio_ready: int
    total_terms: int
    term_translations_ready: int
    word_english_audio_ready: int
    word_chinese_audio_ready: int
    speech_assets_ready: int
    total_speech_assets: int


class CourseCacheItemStatus(BaseModel):
    learning_item_id: UUID
    sentence_chinese_translation_ready: bool
    sentence_english_audio_ready: bool
    sentence_chinese_audio_ready: bool
    word_translations_ready: bool
    word_english_audio_ready: bool
    word_chinese_audio_ready: bool


class CourseCacheStatusResponse(BaseModel):
    course_id: UUID
    summary: CourseCacheStatusSummary
    items: list[CourseCacheItemStatus]


class WordMistakeLogRequest(BaseModel):
    learning_item_id: UUID
    expected_word: str = Field(min_length=1)
    actual_word: str = ""
    error_type: str = Field(default="spelling", max_length=32)


class WordMistakeLogResponse(BaseModel):
    logged_count: int


class WordReviewRequest(BaseModel):
    learning_item_id: UUID
    review_task_id: UUID | None = None
    word: str = Field(min_length=1)
    score: int = Field(ge=0, le=5)
    review_mode: str = Field(min_length=1, max_length=32)
    response_text: str | None = None
    duration_seconds: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=32)
    encoding_stage: str | None = Field(default=None, max_length=32)
    encoding_duration_ms: int = Field(default=0, ge=0)


class WordReviewResponse(BaseModel):
    learning_item_id: UUID
    word: str


class DynamicSentenceCandidate(BaseModel):
    english_text: str
    chinese_text: str


class DynamicSentenceRequest(BaseModel):
    course_id: UUID | None = None
    current_sentence: str = ""
    mistaken_words: list[str] = Field(default_factory=list)
    difficulty_level: int = Field(default=3, ge=1, le=5)
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
    candidates: list[DynamicSentenceCandidate] = Field(default_factory=list)


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
