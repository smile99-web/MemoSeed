import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.course import Course
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.user import User
from app.schemas.learning import (
    DynamicSentenceRequest,
    DynamicSentenceResponse,
    LearningEncouragementRequest,
    LearningEncouragementResponse,
    LearningImportResponse,
    LearningItemCreate,
    LearningItemRead,
    LearningTranslationRequest,
    LearningTranslationResponse,
    WordMistakeLogRequest,
    WordMistakeLogResponse,
)
from app.services.dynamic_sentence import generate_dynamic_review_sentence
from app.services.learning_import import SUPPORTED_IMPORT_EXTENSIONS, import_learning_items, parse_txt_import, parse_xlsx_import
from app.services.llm_translation import DEFAULT_LLM_TRANSLATION_SETTINGS, LlmTranslationSettings, generate_learning_text, translate_english_to_chinese
from app.services.memory_scheduler import calculate_review_priority
from app.services.secure_model_settings import get_private_model_settings
from app.utils import extract_mistake_words, string_setting

router = APIRouter()


def build_llm_translation_settings(
    llm_provider: str | None,
    llm_base_url: str | None,
    llm_model: str | None,
    llm_api_key: str | None,
    stored_settings: dict[str, object] | None = None,
) -> LlmTranslationSettings:
    base_settings = DEFAULT_LLM_TRANSLATION_SETTINGS
    stored_settings = stored_settings or {}
    provider = llm_provider or string_setting(stored_settings, "llmProvider") or app_settings.ai_provider or base_settings.provider
    return LlmTranslationSettings(
        provider=str(provider),
        base_url=llm_base_url or string_setting(stored_settings, "llmBaseUrl") or app_settings.ai_base_url or base_settings.base_url,
        model=llm_model or string_setting(stored_settings, "llmModel") or app_settings.ai_model or base_settings.model,
        api_key=llm_api_key or string_setting(stored_settings, "llmApiKey") or app_settings.ai_api_key,
    )


@router.get("/items", response_model=list[LearningItemRead])
def list_learning_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    course_id: UUID | None = None,
) -> list[LearningItemRead]:
    statement = select(LearningItem).where(LearningItem.user_id == current_user.id)
    if course_id is not None:
        statement = statement.where(LearningItem.course_id == course_id)
    items = db.scalars(statement.order_by(LearningItem.created_at.desc())).all()
    return [LearningItemRead.model_validate(item) for item in items]


@router.get("/review-items", response_model=list[LearningItemRead])
def list_due_review_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    exclude_course_id: UUID | None = None,
    limit: int = 12,
) -> list[LearningItemRead]:
    capped_limit = max(1, min(limit, 30))
    now = datetime.now(UTC)
    item_by_id: dict[UUID, LearningItem] = {}
    focus_words_by_item_id: dict[UUID, list[str]] = {}

    def can_include(item: LearningItem) -> bool:
        return exclude_course_id is None or item.course_id != exclude_course_id

    def add_focus_words(item_id: UUID, words: list[str]) -> None:
        if not words:
            return
        current_words = focus_words_by_item_id.setdefault(item_id, [])
        for word in words:
            normalized_word = word.strip().lower()
            if normalized_word and normalized_word not in current_words:
                current_words.append(normalized_word)

    due_statement = (
        select(LearningItem, MemoryState)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(LearningItem.user_id == current_user.id, MemoryState.next_review_at <= now)
        .order_by(MemoryState.next_review_at.asc())
    )
    if exclude_course_id is not None:
        due_statement = due_statement.where(or_(LearningItem.course_id.is_(None), LearningItem.course_id != exclude_course_id))

    due_rows = list(db.execute(due_statement).all())
    due_rows.sort(key=lambda row: (-calculate_review_priority(row[1], now), row[1].next_review_at))
    for item, _memory_state in due_rows:
        item_by_id.setdefault(item.id, item)

    mistake_statement = (
        select(MistakeLog, LearningItem)
        .join(LearningItem, LearningItem.id == MistakeLog.learning_item_id)
        .where(
            MistakeLog.user_id == current_user.id,
            MistakeLog.is_resolved.is_(False),
            LearningItem.user_id == current_user.id,
        )
        .order_by(MistakeLog.occurred_at.desc())
    )
    if exclude_course_id is not None:
        mistake_statement = mistake_statement.where(or_(LearningItem.course_id.is_(None), LearningItem.course_id != exclude_course_id))

    for mistake, item in db.execute(mistake_statement).all():
        if can_include(item):
            item_by_id.setdefault(item.id, item)
            add_focus_words(item.id, extract_mistake_words(mistake.mistake_type, mistake.expected_answer, mistake.actual_answer))

    review_items: list[LearningItemRead] = []
    for item in item_by_id.values():
        item_read = LearningItemRead.model_validate(item)
        focus_words = focus_words_by_item_id.get(item.id, [])
        if focus_words:
            item_read = item_read.model_copy(update={"source": f"AI 动态复习：{', '.join(focus_words)}"})
        review_items.append(item_read)
        if len(review_items) >= capped_limit:
            break

    return review_items


@router.post("/items", response_model=LearningItemRead, status_code=status.HTTP_201_CREATED)
def create_learning_item(
    payload: LearningItemCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningItemRead:
    if payload.course_id is not None:
        course = db.scalar(select(Course).where(Course.id == payload.course_id, Course.user_id == current_user.id))
        if course is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    existing_item = db.scalar(
        select(LearningItem).where(
            LearningItem.user_id == current_user.id,
            LearningItem.course_id == payload.course_id,
            LearningItem.item_type == payload.item_type,
            LearningItem.english_text.ilike(payload.english_text.strip()),
        )
    )
    if existing_item is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Learning item already exists")

    learning_item = LearningItem(user_id=current_user.id, **payload.model_dump())
    db.add(learning_item)
    db.commit()
    db.refresh(learning_item)
    return LearningItemRead.model_validate(learning_item)


@router.post("/translations", response_model=LearningTranslationResponse)
def translate_learning_text(
    payload: LearningTranslationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningTranslationResponse:
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(
        payload.llm_provider,
        payload.llm_base_url,
        payload.llm_model,
        payload.llm_api_key,
        stored_settings,
    )

    try:
        chinese_text = translate_english_to_chinese(payload.english_text, translation_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return LearningTranslationResponse(english_text=payload.english_text, chinese_text=chinese_text)


@router.post("/encouragements", response_model=LearningEncouragementResponse)
def generate_learning_encouragement(
    payload: LearningEncouragementRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningEncouragementResponse:
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(
        payload.llm_provider,
        payload.llm_base_url,
        payload.llm_model,
        payload.llm_api_key,
        stored_settings,
    )
    prompt = (
        "Generate one short, warm encouragement for a primary or middle school English learner who just finished a lesson. "
        "Return only compact JSON with keys chinese_text and english_text. "
        "The Chinese sentence must be natural Simplified Chinese, and the English sentence must be a simple equivalent sentence. "
        "Keep each sentence under 22 words. "
        f"Lesson name: {payload.course_name.strip() or '本课'}. "
        f"Duration seconds: {payload.duration_seconds}."
    )

    try:
        generated_text = generate_learning_text(prompt, translation_settings)
        normalized_text = generated_text.strip()
        if normalized_text.startswith("```"):
            normalized_text = normalized_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        json_start = normalized_text.find("{")
        json_end = normalized_text.rfind("}")
        if json_start >= 0 and json_end >= json_start:
            normalized_text = normalized_text[json_start : json_end + 1]
        body = json.loads(normalized_text)
        chinese_text = str(body.get("chinese_text", "")).strip()
        english_text = str(body.get("english_text", "")).strip()
    except (ValueError, json.JSONDecodeError, AttributeError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if not chinese_text or not english_text:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM encouragement response is incomplete")

    return LearningEncouragementResponse(chinese_text=chinese_text, english_text=english_text)


@router.post("/word-mistakes", response_model=WordMistakeLogResponse, status_code=status.HTTP_201_CREATED)
def create_word_mistake_log(
    payload: WordMistakeLogRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WordMistakeLogResponse:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == payload.learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    mistake_log = MistakeLog(
        user_id=current_user.id,
        learning_item_id=learning_item.id,
        mistake_type="word-spelling",
        expected_answer=payload.expected_word.strip(),
        actual_answer=payload.actual_word.strip(),
        is_resolved=False,
    )
    db.add(mistake_log)
    db.commit()
    return WordMistakeLogResponse(logged_count=1)


@router.post("/dynamic-sentences", response_model=DynamicSentenceResponse)
def create_dynamic_sentence(
    payload: DynamicSentenceRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DynamicSentenceResponse:
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(payload.llm_provider, payload.llm_base_url, payload.llm_model, payload.llm_api_key, stored_settings)
    result = generate_dynamic_review_sentence(
        db=db,
        user_id=current_user.id,
        course_id=payload.course_id,
        current_sentence=payload.current_sentence,
        mistaken_words=payload.mistaken_words,
        settings=translation_settings,
    )
    return DynamicSentenceResponse(
        english_text=result.english_text,
        chinese_text=result.chinese_text,
        focus_words=result.focus_words,
        known_words=result.known_words,
        weak_words=result.weak_words,
    )


@router.get("/items/{item_id}", response_model=LearningItemRead)
def get_learning_item(
    item_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningItemRead:
    item = db.scalar(select(LearningItem).where(LearningItem.id == item_id, LearningItem.user_id == current_user.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")
    return LearningItemRead.model_validate(item)


@router.post("/imports", response_model=LearningImportResponse, status_code=status.HTTP_201_CREATED)
async def import_learning_items_file(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    course_id: Annotated[UUID, Form()],
    llm_provider: Annotated[str | None, Form()] = None,
    llm_base_url: Annotated[str | None, Form()] = None,
    llm_model: Annotated[str | None, Form()] = None,
    llm_api_key: Annotated[str | None, Form()] = None,
) -> LearningImportResponse:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    filename = file.filename or "uploaded-file"
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .txt and .xlsx files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    if extension == ".txt":
        parse_result = parse_txt_import(content, filename)
    else:
        parse_result = parse_xlsx_import(content, filename)

    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(llm_provider, llm_base_url, llm_model, llm_api_key, stored_settings)

    imported_items, duplicate_skipped_items = import_learning_items(db, current_user.id, course_id, parse_result.items, translation_settings)
    skipped_items = [*parse_result.skipped_items, *duplicate_skipped_items]

    return LearningImportResponse(
        imported_count=len(imported_items),
        skipped_count=len(skipped_items),
        total_rows=parse_result.total_rows,
        items=[LearningItemRead.model_validate(item) for item in imported_items],
        skipped_items=skipped_items,
    )
