import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.course import Course
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.speech_asset import SpeechAsset
from app.models.user import User
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.models.word_translation import WordTranslation
from app.schemas.learning import (
    CourseCacheItemRetryRequest,
    CourseCacheRebuildRequest,
    CourseCacheItemStatus,
    CourseCacheStatusResponse,
    CourseCacheStatusSummary,
    DynamicSentenceCandidate,
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
    WordReviewRequest,
    WordReviewResponse,
    WordTranslationsRequest,
    WordTranslationsResponse,
)
from app.services.dynamic_sentence import generate_dynamic_review_sentence
from app.services.learning_import import SUPPORTED_IMPORT_EXTENSIONS, import_learning_items, parse_txt_import, parse_xlsx_import
from app.services.llm_translation import DEFAULT_LLM_TRANSLATION_SETTINGS, LlmTranslationSettings, generate_learning_text, needs_translation, translate_english_to_chinese
from app.services.memory_dashboard import calculate_word_priority
from app.services.memory_scheduler import (
    calculate_current_forget_risk,
    calculate_review_priority,
    exceeded_daily_review_filter_clause,
    park_cliff_words,
    park_mastered_words,
    park_stuck_words,
    schedule_memory_review,
    stuck_word_daily_cap_filter_clause,
    stuck_word_filter_clause,
)
from app.services.secure_model_settings import get_private_model_settings
from app.services.speech_asset_cache import build_learning_speech_targets, ensure_volcengine_speech_asset, precache_learning_speech_assets
from app.services.tts_cache import build_cache_key, get_cached_audio
from app.services.word_memory import (
    build_task_choices,
    build_task_prompt,
    choose_task_sequence,
    complete_word_review_task,
    schedule_micro_review_tasks_for_mistake,
    supersede_stale_pending_tasks_for_reviewed_words,
    sync_word_memory_from_review,
)
from app.services.word_translation_cache import ensure_word_translations, get_cached_word_translations
from app.utils import extract_mistake_words, normalize_word, string_setting, tokenize_words

router = APIRouter()
logger = logging.getLogger(__name__)

WORD_MEMORY_SOURCE = "word-memory"
GENERIC_WORD_DISTRACTORS = [
    "老师",
    "学生",
    "朋友",
    "书",
    "学校",
    "家庭",
    "苹果",
    "颜色",
    "动物",
    "天气",
    "喜欢",
    "知道",
]
BASIC_WORD_TRANSLATIONS = {
    "a": "一个",
    "an": "一个",
    "am": "是",
    "are": "是",
    "be": "是",
    "book": "书",
    "can": "能",
    "come": "来",
    "day": "天",
    "do": "做",
    "every": "每个",
    "go": "去",
    "have": "有",
    "i": "我",
    "is": "是",
    "new": "新的",
    "please": "请",
    "school": "学校",
    "student": "学生",
    "to": "去",
    "us": "我们",
    "we": "我们",
    "what": "什么",
    "you": "你",
}
CHINESE_SENTENCE_MARKERS = set("，。！？；：,.!?;:、 \n\r\t")


def build_micro_task_learning_item(
    task: WordReviewTask,
    source_item: LearningItem | None,
    current_user: User,
    cloze_settings: LlmTranslationSettings | None = None,
    db: Session | None = None,
    word_translations: dict[str, str] | None = None,
) -> tuple[LearningItemRead, bool]:
    task_updated = False
    english_text = task.word
    # Use pre-cached Chinese translation if available; fall back to prompt text
    cached_translation = (word_translations or {}).get(task.word.strip().lower(), "")
    chinese_text = cached_translation if cached_translation else task.prompt_text
    review_prompt = task.prompt_text
    source = f"微型任务：{task.task_type}：{task.word}"
    item_type = "word"
    raw_choices = [str(choice) for choice in task.choices]
    review_answer = raw_choices[0] if raw_choices else task.expected_answer
    # Enrich choice tasks: ensure 6 choices with the word's real Chinese translation
    if task.task_type in {"listen_choose_chinese", "english_to_chinese", "match_translation"} and db is not None:
        enriched, correct_answer = _enrich_review_choices(db, current_user.id, task, raw_choices, cloze_settings)
        raw_choices = enriched
        review_answer = correct_answer
        task.choices = raw_choices
        db.add(task)
        task_updated = True
    if len(raw_choices) > 1:
        shift = len(task.word) % len(raw_choices)
        raw_choices = raw_choices[shift:] + raw_choices[:shift]

    return LearningItemRead(
        id=task.id,
        user_id=current_user.id,
        course_id=source_item.course_id if source_item is not None else None,
        item_type=item_type,
        english_text=english_text,
        chinese_text=chinese_text,
        phonetic=source_item.phonetic if source_item is not None else None,
        syllables=source_item.syllables if source_item is not None else None,
        grapheme_phoneme_map=source_item.grapheme_phoneme_map if source_item is not None else None,
        difficulty_level=source_item.difficulty_level if source_item is not None else 3,
        source=source,
        review_task_id=task.id,
        review_task_type=task.task_type,
        review_prompt=review_prompt,
        review_choices=raw_choices,
        review_answer=review_answer,
        focus_words=[task.word],
        source_item_id=source_item.id if source_item is not None else task.learning_item_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    ), task_updated


def _enrich_review_choices(
    db: Session,
    user_id: UUID,
    task: WordReviewTask,
    existing_choices: list[str],
    settings: LlmTranslationSettings | None,
) -> tuple[list[str], str]:
    """Ensure 6 real Chinese choices without blocking the review queue on LLM calls."""
    from sqlalchemy import func as sa_func, select as sa_select

    normalized_word = normalize_word(task.word)
    try:
        cached_translations = get_cached_word_translations(db, user_id, [normalized_word])
    except ProgrammingError:
        db.rollback()
        cached_translations = {}

    correct_answer = cached_translations.get(normalized_word, "") or BASIC_WORD_TRANSLATIONS.get(normalized_word, "")
    correct_answer = sanitize_word_choice(correct_answer)
    if not correct_answer:
        for choice in existing_choices:
            correct_answer = sanitize_word_choice(choice)
            if correct_answer:
                break
    if not correct_answer:
        correct_answer = "这个词"

    rebuilt: list[str] = [correct_answer]

    try:
        cached_distractors = db.execute(
            sa_select(WordTranslation.chinese_text)
            .where(
                WordTranslation.user_id == user_id,
                WordTranslation.word != normalized_word,
                WordTranslation.chinese_text != correct_answer,
            )
            .order_by(sa_func.random())
            .limit(10)
        ).scalars().all()
    except ProgrammingError:
        db.rollback()
        cached_distractors = []

    for distractor in cached_distractors:
        distractor = sanitize_word_choice(distractor)
        if distractor and distractor not in rebuilt and distractor != correct_answer:
            rebuilt.append(distractor)
        if len(rebuilt) >= 6:
            return rebuilt, correct_answer

    for choice in GENERIC_WORD_DISTRACTORS:
        if choice and choice != correct_answer and choice not in rebuilt:
            rebuilt.append(choice)
        if len(rebuilt) >= 6:
            return rebuilt, correct_answer

    # Step 3: only use existing choices after filtering out sentence-level text.
    for choice in existing_choices:
        choice = sanitize_word_choice(choice)
        if choice and choice != correct_answer and choice not in rebuilt:
            rebuilt.append(choice)
        if len(rebuilt) >= 6:
            return rebuilt, correct_answer

    return rebuilt, correct_answer


def sanitize_word_choice(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not contains_chinese_text(text):
        return ""
    if any(marker in text for marker in CHINESE_SENTENCE_MARKERS):
        return ""
    if len(text) > 6:
        return ""
    return text


def contains_chinese_text(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def build_word_level_distractors(
    target_word: str,
    candidate_texts: list[str],
    settings: LlmTranslationSettings | None,
) -> list[str]:
    if settings is None:
        return []

    target_key = normalize_word(target_word)
    candidate_words: list[str] = []
    for candidate_text in candidate_texts:
        for word in tokenize_words(candidate_text):
            word_key = normalize_word(word)
            if not word_key or word_key == target_key or word_key in candidate_words:
                continue
            candidate_words.append(word_key)
            if len(candidate_words) >= 12:
                break
        if len(candidate_words) >= 12:
            break

    distractors: list[str] = []
    for word in candidate_words:
        try:
            translated = sanitize_word_choice(translate_english_to_chinese(word, settings))
        except Exception:
            translated = ""
        if translated and translated not in distractors:
            distractors.append(translated)
        if len(distractors) >= 5:
            break
    return distractors


def error_count_value(value: object) -> int:
    if isinstance(value, dict):
        value = value.get("count", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_word_state_error_type(word_state: WordMemoryState) -> str:
    counts = word_state.error_type_counts or {}
    if not counts:
        return "spelling"
    return max(counts.items(), key=lambda item: error_count_value(item[1]))[0]


def calculate_due_word_task_priority(word_state: WordMemoryState, memory_state: MemoryState | None, now: datetime) -> float:
    if memory_state is not None:
        risk = calculate_current_forget_risk(memory_state, now)
        strength = round(1 - risk, 2)
        # IMPORTANT: use the long-term MemoryState.next_review_at for the
        # overdue calculation. Using next_micro_review_at here would let
        # the micro-review clock (typically set to +1d after a mistake)
        # dominate a mastered word's long-term 30-day schedule and pull it
        # back into the queue. The micro-review clock should only feed
        # ensure_due_word_review_tasks, not the priority score itself.
        next_review_at = memory_state.next_review_at
    else:
        risk = word_state.forget_risk
        strength = word_state.memory_strength
        next_review_at = word_state.next_micro_review_at
    mistake_count = sum(error_count_value(value) for value in (word_state.error_type_counts or {}).values())
    stats = type(
        "DueWordPriorityStats",
        (),
        {
            "mistake_count": mistake_count,
            "consecutive_error_count": word_state.consecutive_error_count,
            "preview_correct_count": word_state.preview_correct_count,
            "recall_correct_count": word_state.recall_correct_count,
            "last_reviewed_at": word_state.last_reviewed_at,
            "error_type_counts": word_state.error_type_counts or {},
        },
    )()
    return calculate_word_priority(stats, strength, risk, next_review_at, now)


def ensure_due_word_review_tasks(db: Session, user_id: UUID, now: datetime, limit: int) -> bool:
    pending_words = set(
        db.scalars(
            select(WordReviewTask.word).where(
                WordReviewTask.user_id == user_id,
                WordReviewTask.status == "pending",
            )
        ).all()
    )

    due_rows = db.execute(
        select(WordMemoryState, LearningItem, MemoryState)
        .outerjoin(LearningItem, LearningItem.id == WordMemoryState.learning_item_id)
        .outerjoin(MemoryState, MemoryState.id == WordMemoryState.memory_state_id)
        .where(
            WordMemoryState.user_id == user_id,
            WordMemoryState.next_micro_review_at.isnot(None),
            WordMemoryState.next_micro_review_at <= now,
        )
    ).all()

    due_candidates = [
        (word_state, source_item, memory_state, calculate_due_word_task_priority(word_state, memory_state, now))
        for word_state, source_item, memory_state in due_rows
        if word_state.word not in pending_words
    ]
    due_candidates.sort(key=lambda row: (-row[3], row[0].next_micro_review_at or now))

    created = False
    for word_state, source_item, _memory_state, priority in due_candidates[: max(limit, 0)]:
        error_type = get_word_state_error_type(word_state)
        task_type = choose_task_sequence(word_state, error_type)[0]
        prompt_source = source_item.chinese_text if source_item is not None and source_item.chinese_text else word_state.word
        db.add(
            WordReviewTask(
                user_id=user_id,
                word_memory_state_id=word_state.id,
                learning_item_id=word_state.learning_item_id,
                word=word_state.word,
                task_type=task_type,
                prompt_text=build_task_prompt(task_type, word_state.word, prompt_source),
                expected_answer=word_state.word,
                choices=build_task_choices(db, user_id, task_type, word_state.word, prompt_source),
                priority_score=priority,
                status="pending",
                source="word-memory:due",
                due_at=word_state.next_micro_review_at or now,
            )
        )
        word_state.priority_score = priority
        db.add(word_state)
        created = True

    if created:
        db.flush()
    return created


def refresh_pending_word_review_task_priorities(db: Session, user_id: UUID, now: datetime) -> bool:
    """Recompute due pending task priorities from the current memory state.

    Older tasks may have been created with a stale priority_score. Recomputing
    before queue selection keeps the review order aligned with the latest FSRS
    risk, mistakes, overdue time, and recent-practice penalty.
    """
    task_rows = db.execute(
        select(WordReviewTask, WordMemoryState, MemoryState)
        .outerjoin(WordMemoryState, WordMemoryState.id == WordReviewTask.word_memory_state_id)
        .outerjoin(MemoryState, MemoryState.id == WordMemoryState.memory_state_id)
        .where(
            WordReviewTask.user_id == user_id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at <= now,
        )
    ).all()

    updated = False
    for task, word_state, memory_state in task_rows:
        if word_state is None:
            continue
        priority = calculate_due_word_task_priority(word_state, memory_state, now)
        # P2-1: Stale task boost — tasks pending >24h get +0.15 priority
        task_age_hours = (now - task.created_at).total_seconds() / 3600
        stale_boost = min(task_age_hours / 24 * 0.15, 0.20) if task_age_hours > 24 else 0
        priority = min(priority + stale_boost, 1.0)
        if abs(float(task.priority_score) - priority) < 0.0001:
            continue
        task.priority_score = priority
        word_state.priority_score = priority
        db.add(task)
        db.add(word_state)
        updated = True

    if updated:
        db.flush()
    return updated


VALID_WORD_ERROR_TYPES: frozenset[str] = frozenset({
    "spelling",
    "first-letter",
    "meaning",
    "middle",
    "ending",
    "sequence",
    "missing-letter",
    "extra-letter",
    "unknown",
})


def normalize_word_error_type(value: str | None) -> str:
    """Normalize and validate a per-word error_type.

    The previous implementation only stripped non-alphanumeric chars and
    truncated to 24 chars, which let arbitrary 24-char garbage reach the
    database. That value then leaked into the WordReviewTask.source string
    and the dashboard's "build_review_reason" labels via
    ERROR_TYPE_LABELS.get(...) — falling through to the "拼写错误" default
    while still being recorded as a unique error_type in the DB. The
    whitelist below ensures only known error types are stored.
    """
    cleaned = "".join(char for char in (value or "").strip().lower() if char.isalnum() or char == "-")
    if cleaned in VALID_WORD_ERROR_TYPES:
        return cleaned
    return "spelling"


def get_or_create_word_memory_item(
    db: Session,
    user_id: UUID,
    word: str,
    source_item: LearningItem | None = None,
) -> LearningItem:
    normalized_word = normalize_word(word)
    if not normalized_word:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Word is required")

    existing_item = db.scalar(
        select(LearningItem).where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
            LearningItem.source == WORD_MEMORY_SOURCE,
            LearningItem.english_text == normalized_word,
        )
    )
    if existing_item is not None:
        # Do NOT overwrite chinese_text with the English word. The
        # previous code set chinese_text = normalized_word here, which
        # caused the child to see the English word ("the", "apple") as
        # the Chinese prompt in spelling tasks. If the existing item
        # doesn't have Chinese text, try to fill it from the source.
        if not existing_item.chinese_text or not any("一" <= c <= "鿿" for c in existing_item.chinese_text):
            if source_item is not None and source_item.chinese_text:
                existing_item.chinese_text = source_item.chinese_text
        return existing_item

    # Use source_item's Chinese text if available, otherwise leave empty
    # (the translation service fills it later)
    initial_chinese = source_item.chinese_text if (source_item is not None and source_item.chinese_text) else ""
    learning_item = LearningItem(
        user_id=user_id,
        course_id=None,
        item_type="word",
        english_text=normalized_word,
        chinese_text=initial_chinese,
        difficulty_level=source_item.difficulty_level if source_item is not None else 1,
        source=WORD_MEMORY_SOURCE,
    )
    db.add(learning_item)
    db.flush()
    return learning_item


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
    limit: int | None = None,
    include_choices: bool = False,
) -> list[LearningItemRead]:
    statement = (
        select(LearningItem)
        .outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(LearningItem.user_id == current_user.id)
    )
    if course_id is not None:
        statement = statement.where(LearningItem.course_id == course_id)
    # Sort overdue items first (nulls last = never-reviewed/new items go after due items)
    statement = statement.order_by(
        MemoryState.next_review_at.is_(None),
        MemoryState.next_review_at.asc(),
        LearningItem.sort_order.asc(),
        LearningItem.created_at.asc(),
    )
    if limit is not None and limit > 0:
        statement = statement.limit(limit)
    items = db.scalars(statement).all()
    result = [LearningItemRead.model_validate(item) for item in items]

    if include_choices:
        # For each word-type item, prepend an english_to_chinese
        # choice variant with database-random distractors. Uses the
        # same _enrich_review_choices logic as the micro-review task
        # system — distractors come from WordTranslation (user's own
        # word list), NOT from a fixed pool.
        stored_settings = get_private_model_settings(db, current_user.id)
        cloze_settings = build_llm_translation_settings(None, None, None, None, stored_settings)

        enriched: list[LearningItemRead] = []
        seen_words: set[str] = set()
        for item in items:
            if item.item_type != "word":
                enriched.append(LearningItemRead.model_validate(item))
                continue
            normalized = normalize_word(item.english_text or "")
            if not normalized or normalized in seen_words:
                enriched.append(LearningItemRead.model_validate(item))
                continue
            seen_words.add(normalized)

            # Build distractors from the user's own word database
            choices, correct_answer = _enrich_choices_for_word(
                db, current_user.id, normalized, item, cloze_settings
            )
            # Skip if no Chinese translation could be found
            if not choices or not correct_answer:
                enriched.append(LearningItemRead.model_validate(item))
                continue
            choice_item = LearningItemRead(
                id=uuid4(),
                user_id=current_user.id,
                course_id=item.course_id,
                item_type="word",
                english_text=normalized,
                chinese_text=correct_answer if correct_answer else (item.chinese_text or ""),
                phonetic=item.phonetic,
                syllables=item.syllables,
                grapheme_phoneme_map=item.grapheme_phoneme_map,
                difficulty_level=item.difficulty_level,
                source="AI 动态复习",
                review_task_type="english_to_chinese",
                review_prompt=f"选择 {normalized} 的中文意思",
                review_choices=choices,
                review_answer=correct_answer,
                focus_words=[normalized],
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            enriched.append(choice_item)
            enriched.append(LearningItemRead.model_validate(item))

        # Prune to limit if needed
        if limit is not None and limit > 0:
            return enriched[:limit]
        return enriched

    return result


def _enrich_choices_for_word(
    db: Session,
    user_id: UUID,
    normalized_word: str,
    learning_item: LearningItem,
    settings: LlmTranslationSettings | None,
) -> tuple[list[str], str]:
    """Generate 6 Chinese-meaning choices for a word, with DB-random distractors.

    Mirrors _enrich_review_choices but works with a plain learning_item
    instead of a WordReviewTask. Distractors come from WordTranslation
    (user's own database) with func.random() — NOT a fixed pool.
    """
    from sqlalchemy import func as sa_func, select as sa_select

    # Step 1: correct answer from WordTranslation → BASIC_WORD_TRANSLATIONS → item.chinese_text
    try:
        cached = get_cached_word_translations(db, user_id, [normalized_word])
    except ProgrammingError:
        db.rollback()
        cached = {}
    correct_answer = sanitize_word_choice(
        cached.get(normalized_word, "")
        or BASIC_WORD_TRANSLATIONS.get(normalized_word, "")
        or (learning_item.chinese_text or "")
    )
    # If no Chinese translation can be found anywhere, skip this choice
    # item entirely — showing the English word as the answer is
    # confusing and defeats the purpose of the choice exercise.
    if not correct_answer or correct_answer == normalized_word:
        return [], ""

    rebuilt: list[str] = [correct_answer]

    # Step 2: DB-random distractors from user's other words
    try:
        rows = db.execute(
            sa_select(WordTranslation.chinese_text)
            .where(
                WordTranslation.user_id == user_id,
                WordTranslation.word != normalized_word,
                WordTranslation.chinese_text != correct_answer,
            )
            .order_by(sa_func.random())
            .limit(10)
        ).scalars().all()
    except ProgrammingError:
        db.rollback()
        rows = []

    for distractor in rows:
        distractor = sanitize_word_choice(distractor)
        if distractor and distractor not in rebuilt:
            rebuilt.append(distractor)
        if len(rebuilt) >= 6:
            return rebuilt, correct_answer

    # Step 3: fallback to GENERIC_WORD_DISTRACTORS
    for choice in GENERIC_WORD_DISTRACTORS:
        if choice and choice != correct_answer and choice not in rebuilt:
            rebuilt.append(choice)
        if len(rebuilt) >= 6:
            return rebuilt, correct_answer

    return rebuilt[:6], correct_answer


# Phonics pattern groups for batch teaching
PHONICS_GROUPS = {
    "ight": ["ight", "light", "night", "right", "bright", "fight"],
    "ing": ["ing", "king", "ring", "sing", "bring", "thing", "morning", "evening"],
    "ake": ["ake", "make", "take", "cake", "lake", "wake", "shake"],
    "all": ["all", "call", "fall", "ball", "small", "wall", "tall"],
    "ook": ["ook", "look", "book", "cook", "took", "good"],
    "ere": ["ere", "here", "there", "where"],
    "ame": ["ame", "name", "game", "same", "came"],
    "eat": ["eat", "meat", "seat", "beat", "heat", "great"],
    "ear": ["ear", "hear", "near", "dear", "year", "clear"],
    "our": ["our", "hour", "four", "your", "colour"],
}


def _get_phonics_group(word: str) -> str | None:
    w = word.lower().strip()
    for group, members in PHONICS_GROUPS.items():
        if w in members or w.endswith(group):
            return group
    return None


@router.get("/review-items", response_model=list[LearningItemRead])
def list_due_review_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    exclude_course_id: UUID | None = None,
    limit: int = 12,
    review_cap: int | None = None,
    interleave: bool = False,
    focus: bool = False,
) -> list[LearningItemRead]:
    """List due review items.

    When interleave=True, review tasks and new items are interleaved (1:2 ratio)
    and review tasks are capped to avoid front-loading fatigue.

    When focus=True (recommended for struggling learners), only the top 7
    highest-priority words are returned, each with 3 different review modes
    for thorough multi-modal practice. Total items = focus_word_count × 3.
    """
    capped_limit = max(1, min(limit, 200))
    effective_review_cap = review_cap if review_cap is not None else capped_limit
    now = datetime.now(UTC)
    # Local-day boundary (Asia/Shanghai) for the per-day review cap.
    # Use the actual LOCAL_TIMEZONE (not a hardcoded +8h offset) so this
    # survives any future timezone change without code edits.
    from app.services.memory_scheduler import LOCAL_TIMEZONE
    today_start = now.astimezone(LOCAL_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
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

    stored_settings = get_private_model_settings(db, current_user.id)
    # Park plateaued (R1), mastered (A), and stuck (C) words before
    # building the due queue.
    park_mastered_words(db, current_user.id, now)
    park_stuck_words(db, current_user.id, now)
    park_cliff_words(db, current_user.id, now)
    cloze_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    has_task_updates = supersede_stale_pending_tasks_for_reviewed_words(db, current_user.id, now)

    # Word-centric: prioritize word-type items first, then sentences as context
    # The stuck-word filter and per-day review cap prevent a handful of
    # unlearnable words from dominating the queue (see memory_scheduler
    # STUCK_WORD_LAPSE_THRESHOLD / MAX_DAILY_REVIEWS_PER_WORD).
    due_statement = (
        select(LearningItem, MemoryState)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == current_user.id,
            MemoryState.next_review_at <= now,
            stuck_word_filter_clause(),
            exceeded_daily_review_filter_clause(current_user.id, today_start),
            stuck_word_daily_cap_filter_clause(current_user.id, today_start),
        )
        .order_by(
            LearningItem.item_type.desc(),  # "word" > "sentence" > "phrase"
            MemoryState.next_review_at.asc(),
        )
    )
    if exclude_course_id is not None:
        due_statement = due_statement.where(or_(LearningItem.course_id.is_(None), LearningItem.course_id != exclude_course_id))

    due_rows = list(db.execute(due_statement).all())
    due_rows.sort(key=lambda row: (-calculate_review_priority(row[1], now), row[1].next_review_at))
    for item, _memory_state in due_rows:
        item_by_id.setdefault(item.id, item)

    # Disabled: ensure_due_word_review_tasks was generating 300+ micro-review
    # tasks per session, causing the same handful of words to be repeated
    # dozens of times with no variety. The focus mode (7 words × 5 modes =
    # 35 items) already provides sufficient practice per session.
    # has_task_updates = ensure_due_word_review_tasks(db, ...) or has_task_updates
    # Garbage-collect stale pending tasks: only supersede a pending task when
    # the word's micro-review clock has moved PAST the task's due_at, meaning
    # the word has been reviewed through another path. The previous version
    # deleted any task whose due_at was > 1 day old — which meant a long
    # break (e.g. 3 days away) nuked all queued micro-reviews on the next
    # visit, including the highest-priority ones. Now we require the
    # associated WordMemoryState.next_micro_review_at to be after the
    # task's due_at (i.e. the word has progressed).
    from app.models.word_memory_state import WordMemoryState
    stale_task_subq = (
        select(WordReviewTask.id)
        .join(WordMemoryState, WordMemoryState.id == WordReviewTask.word_memory_state_id)
        .where(
            WordReviewTask.user_id == current_user.id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at < now - timedelta(days=1),
            WordMemoryState.next_micro_review_at.isnot(None),
            WordMemoryState.next_micro_review_at > WordReviewTask.due_at,
        )
    )
    _gc_count = db.execute(
        update(WordReviewTask)
        .where(WordReviewTask.id.in_(stale_task_subq))
        .values(status="superseded", updated_at=now)
    ).rowcount
    if _gc_count:
        has_task_updates = True
    has_task_updates = refresh_pending_word_review_task_priorities(db, current_user.id, now) or has_task_updates

    task_rows = db.execute(
        select(WordReviewTask, LearningItem)
        .outerjoin(LearningItem, LearningItem.id == WordReviewTask.learning_item_id)
        .where(
            WordReviewTask.user_id == current_user.id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at <= now,
        )
        .order_by(WordReviewTask.priority_score.desc(), WordReviewTask.due_at.asc())
        .limit(min(effective_review_cap + 15, 35))  # keep session manageable
    ).all()
    covered_task_words = {task.word for task, _source_item in task_rows}
    # P1-5: Filter out removed task types (recall_word, cloze_sentence).
    REMOVED_TASK_TYPES = {"recall_word", "cloze_sentence"}
    task_rows = [(t, s) for t, s in task_rows if t.task_type not in REMOVED_TASK_TYPES]
    # P0-2: Per-word-per-session cap. Without this, a single word with 5+ pending
    # micro-review tasks (one per mode) would monopolize the session — the child
    # sees "drink, can, go" 20 times each because every completed task makes the
    # next pending task for the same word immediately due. We deduplicate by word
    # AND by source learning_item so different learning items for the same word
    # (which have different Chinese translations) can still appear.
    seen_task_words: set[str] = set()
    deduped_task_rows: list[tuple[WordReviewTask, LearningItem | None]] = []
    for t, s in task_rows:
        w = t.word.strip().lower()
        if w in seen_task_words:
            continue
        seen_task_words.add(w)
        deduped_task_rows.append((t, s))
    task_rows = deduped_task_rows
    # P1-4: Pre-cache Chinese translations for ALL review task words.
    # Filter out words that don't have valid Chinese translations.
    unique_task_words = list({tw.strip().lower() for tw in covered_task_words if tw.strip()})
    task_word_translations: dict[str, str] = {}
    if unique_task_words:
        task_word_translations = ensure_word_translations(
            db, current_user.id, unique_task_words, cloze_settings, None
        )
    valid_task_words = {w for w, t in task_word_translations.items() if t}

    task_review_items: list[LearningItemRead] = []
    queued_task_words: set[str] = set()
    deferred_task_rows: list[tuple[WordReviewTask, LearningItem | None]] = []
    for task, source_item in task_rows:
        if task.word in queued_task_words:
            deferred_task_rows.append((task, source_item))
            continue
        if task.word.strip().lower() not in valid_task_words:
            continue  # skip words without Chinese translation
        task_item, task_updated = build_micro_task_learning_item(task, source_item, current_user, cloze_settings, db, task_word_translations)
        task_review_items.append(task_item)
        has_task_updates = has_task_updates or task_updated
        queued_task_words.add(task.word)
        if len(task_review_items) >= effective_review_cap:
            break
    if len(task_review_items) < effective_review_cap:
        for task, source_item in deferred_task_rows:
            task_item, task_updated = build_micro_task_learning_item(task, source_item, current_user, cloze_settings, db, task_word_translations)
            task_review_items.append(task_item)
            has_task_updates = has_task_updates or task_updated
            if len(task_review_items) >= effective_review_cap:
                break
    if has_task_updates:
        db.commit()

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

    # P1-2: Build item priority map from sorted due_rows for cross-course review prioritization
    item_priority: dict[UUID, float] = {}
    for item, mem_state in due_rows:
        item_priority[item.id] = calculate_review_priority(mem_state, now)

    # Build sentence-level review items, sorted by priority (highest first)
    sentence_review_items: list[LearningItemRead] = []
    for item in item_by_id.values():
        if item.item_type == "word" and item.source == WORD_MEMORY_SOURCE and normalize_word(item.english_text) in covered_task_words:
            continue
        # Skip items without Chinese text — no child should see a word without Chinese context
        if not item.chinese_text or not any("\u4e00" <= c <= "\u9fff" for c in item.chinese_text):
            continue
        item_read = LearningItemRead.model_validate(item)
        focus_words = focus_words_by_item_id.get(item.id, [])
        if focus_words:
            item_read = item_read.model_copy(update={"source": f"AI 动态复习：{', '.join(focus_words)}"})
        sentence_review_items.append(item_read)
    # Sort by priority: highest-risk cross-course items first
    sentence_review_items.sort(key=lambda it: -item_priority.get(it.id, 0.0))

    if sentence_review_items:
        # Multi-mode review: for each due word, generate 3 question types
        # (listen_choose_chinese, english_to_chinese, chinese_to_english)
        # so the child gets a mix of recognition and spelling practice.
        # In focus mode (small batch), cap at 3 words; in normal mode cap
        # at 10 words — enough to give variety without overwhelming the
        # session. Previously this was gated by `if focus and ...` so the
        # word review mode had NO choice tasks when focus was off, leaving
        # only pure-spelling items from the WordReviewTask table.
        REVIEW_WORD_COUNT = 3 if focus else 10
        import random
        pool = sentence_review_items[:max(REVIEW_WORD_COUNT * 3, len(sentence_review_items))]
        random.shuffle(pool)
        sentence_review_items = pool + sentence_review_items[len(pool):]
        # Mixed mode set: 2 recognition tasks first (build confidence),
        # then 3 spelling tasks (apply what was just reviewed).
        # The old set [chinese_to_english, listen_spell, missing_letter]
        # was 100% spelling — children with many lapsed words would get
        # stuck in an infinite loop of failing at spelling, creating more
        # spelling micro-review tasks, and never seeing a recognition task.
        # english_to_chinese = 57% acc, listen_choose_chinese = 55% acc
        # — these give the child a chance to succeed before attempting
        # the harder spelling modes.
        BASE_MODES = [
            "listen_choose_chinese",    # 听音选中文
            "english_to_chinese",       # 看英文选中文
            "chinese_to_english",       # 看中文拼英文
        ]

        # Build per-word intelligence from WordMemoryState to drive
        # dynamic question selection (Phase 1 optimization).
        word_intel: dict[str, dict[str, int]] = {}

        def modes_for_word(word: str) -> list[str]:
            """Return the optimal question mode sequence for a given word.

            Phase 1 optimizations:
              - Skip meaning choice if meaning_errors <= 1 (93% of errors are spelling)
              - Add hidden_recall before spelling if unknown_errors >= 2 (28% of errors)
              - Low-strength word: use only 1 verification question
            """
            intel = word_intel.get(word, {})
            strength = intel.get("strength", 0)
            meaning_errors = intel.get("meaning_errors", 0)
            unknown_errors = intel.get("unknown_errors", 0)

            modes: list[str] = []

            # Low-strength word (正确率>=90%) → only 1 quick verification
            if strength >= 0.90 and intel.get("consecutive_errors", 0) <= 0:
                return ["chinese_to_english"]

            # Build custom mode list
            # Step 1: Listen-based recognition (always start with confidence)
            modes.append("listen_choose_chinese")

            # Step 2: English→Chinese choice — skip if meaning is already solid
            if meaning_errors > 1 or strength < 0.5:
                modes.append("english_to_chinese")

            # Step 3: Preview for difficult words before spelling
            if unknown_errors >= 2 or intel.get("consecutive_errors", 0) >= 3:
                modes.append("hidden_recall")

            # Step 4: Spelling
            modes.append("chinese_to_english")

            return modes

        # P0-1: Warm-up — sort by strength (highest first = easiest words first)
        def _item_strength(item: LearningItemRead) -> float:
            ms_item = next((ms for li, ms in due_rows if li.id == item.id), None)
            return float(ms_item.memory_strength or 0.0) if ms_item and hasattr(ms_item, 'memory_strength') else 0.0
        sentence_review_items.sort(key=_item_strength, reverse=True)

        top_items = sentence_review_items[:REVIEW_WORD_COUNT]

        # Populate word_intel with per-word error data (moved here from
        # above because top_items must be defined first)
        if top_items:
            top_words = {tokenize_words(it.english_text)[0].strip().lower() for it in top_items if tokenize_words(it.english_text)}
            for item, mem_state in due_rows:
                for w in tokenize_words(item.english_text):
                    w = w.strip().lower()
                    if w in top_words and w not in word_intel:
                        word_intel[w] = {
                            "strength": round(mem_state.memory_strength or 0, 2),
                            "lapse_count": mem_state.lapse_count or 0,
                            "consecutive_errors": mem_state.consecutive_error_count or 0,
                        }
            word_state_rows = db.scalars(
                select(WordMemoryState).where(
                    WordMemoryState.user_id == current_user.id,
                    WordMemoryState.word.in_(list(top_words)),
                )
            ).all()
            for ws in word_state_rows:
                if ws.word not in word_intel:
                    word_intel[ws.word] = {"strength": 0, "lapse_count": 0, "consecutive_errors": 0}
                intel = word_intel[ws.word]
                error_counts = ws.error_type_counts or {}
                intel["meaning_errors"] = sum(error_count_value(v) for k, v in error_counts.items() if k == "meaning")
                intel["unknown_errors"] = sum(error_count_value(v) for k, v in error_counts.items() if k == "unknown")
                intel["first_letter_errors"] = sum(error_count_value(v) for k, v in error_counts.items() if k == "first-letter")
                intel["strength"] = max(intel.get("strength", 0), ws.memory_strength or 0)

        # P1-1: Phonics grouping — bring in pattern-siblings
        seen_patterns: set[str] = set()
        extra_items: list[LearningItemRead] = []
        for item in top_items:
            for w in tokenize_words(item.english_text):
                group = _get_phonics_group(w)
                if group and group not in seen_patterns:
                    seen_patterns.add(group)
                    for sibling in sentence_review_items[REVIEW_WORD_COUNT:]:
                        if sibling.id in {i.id for i in top_items}:
                            continue
                        for sw in tokenize_words(sibling.english_text):
                            if _get_phonics_group(sw) == group:
                                extra_items.append(sibling)
                                break
                        if len(extra_items) >= 3:
                            break
        top_items = (top_items + extra_items)[:REVIEW_WORD_COUNT + 2]

        # P1-3: Pre-cache Chinese translations for all focus words.
        # Children need Chinese context to understand what they're spelling.
        focus_words_set: set[str] = set()
        for item in top_items:
            for w in tokenize_words(item.english_text):
                focus_words_set.add(w.strip().lower())
        word_translations: dict[str, str] = {}
        if focus_words_set:
            word_translations = ensure_word_translations(
                db, current_user.id, list(focus_words_set), cloze_settings, None
            )
            # Filter out words that don't have valid Chinese translations
            valid_words = {w for w, t in word_translations.items() if t}
            top_items = [
                item for item in top_items
                if any(w.strip().lower() in valid_words for w in tokenize_words(item.english_text))
            ]

        focus_items: list[LearningItemRead] = []
        seen_main_words: set[str] = set()
        # Build a map of word → word-level LearningItem so each word
        # gets its own independent review items (5 modes per word).
        # Previously, focus tasks were cloned from sentence items —
        # if 20 sentences contained 'your', the child saw 'your'
        # 20 × 5 = 100 times. Now each word gets exactly 5 tasks
        # regardless of how many sentences reference it.
        word_items_by_word: dict[str, LearningItem] = {}
        for li in item_by_id.values():
            if li.item_type != "word":
                continue
            w = normalize_word(li.english_text)
            if w and w not in word_items_by_word:
                word_items_by_word[w] = li

        for item in top_items:
            words = tokenize_words(item.english_text)
            if not words:
                continue
            main_word = words[0].strip().lower()
            if main_word in seen_main_words:
                continue
            seen_main_words.add(main_word)
            # Use the word-level item if available; fall back to the
            # sentence item. The word item has the word's own Chinese
            # translation and properties, giving a clean word-only review.
            word_item = word_items_by_word.get(main_word, item)
            chinese_meaning = word_translations.get(main_word, "") or getattr(word_item, 'chinese_text', "") or main_word
            word_modes = modes_for_word(main_word)
            intel = word_intel.get(main_word, {})
            wlen = len(main_word)
            # Hint: first-letter prompt for words with persistent first-letter errors
            first_letter_hint = ""
            if intel.get("first_letter_errors", 0) >= 2 and wlen >= 3:
                first_letter_hint = main_word[0]
            # Syllable trigger: auto-encoding for 4-7 letter words (hardest range)
            need_syllable = 4 <= wlen <= 7
            for mode in word_modes:
                review_prompt = None
                if mode == "chinese_to_english":
                    if first_letter_hint:
                        review_prompt = f"首字母:{first_letter_hint}"
                focus_item = LearningItemRead(
                    id=uuid4(),
                    user_id=current_user.id,
                    course_id=item.course_id,
                    item_type="word",
                    english_text=main_word,
                    chinese_text=chinese_meaning,
                    review_task_type=mode,
                    review_prompt=review_prompt,
                    source=f"单词复习{'syllable' if need_syllable else ''}",
                    focus_words=[main_word],
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                focus_items.append(focus_item)
        # Focus-mode only: push served items to tomorrow so they don't
        # re-appear in the next session. In non-focus mode, items stay
        # on their natural FSRS schedule (no artificial push).
        if focus:
            tomorrow = now + timedelta(days=1)
            for item in top_items:
                db.execute(
                    update(MemoryState)
                    .where(
                        MemoryState.learning_item_id == item.id,
                        MemoryState.next_review_at < tomorrow,  # ← don't overwrite park
                    )
                    .values(next_review_at=tomorrow)
                )
            db.commit()
        # Sentences first (discover new weak words), then word-only
        # review (practice already-discovered words). Each word that
        # appears in the word review section is excluded from the
        # sentence section so the child doesn't see 'your' in 20
        # different sentences during word practice.
        sentences_for_session = [s for s in sentence_review_items[:3]
                                if tokenize_words(s.english_text) and tokenize_words(s.english_text)[0].strip().lower() not in seen_main_words]

        # Build the final queue: multi-mode focus items first, then
        # any remaining items from the due queue. Previously this
        # returned early at `return sentences_for_session + focus_items`
        # which capped the queue at ~33 items regardless of the
        # actual due count (123). Now focus items are just the prefix;
        # the remaining items follow in FSRS order.
        prefix_items = sentences_for_session + focus_items
        # Remove words already covered in the prefix from the
        # sentence_review_items so they don't appear twice.
        prefix_word_set = seen_main_words.copy()
        for item in prefix_items:
            for w in tokenize_words(item.english_text if hasattr(item, 'english_text') else item.english_text):
                prefix_word_set.add(w.strip().lower())
        tail_items = [s for s in sentence_review_items[3:]
                      if tokenize_words(s.english_text) and tokenize_words(s.english_text)[0].strip().lower() not in prefix_word_set]
        review_items = task_review_items + prefix_items + tail_items
        # Clamp to capped_limit
        return review_items[:capped_limit] if len(review_items) > capped_limit else review_items

    if interleave and task_review_items and sentence_review_items:
        # Interleave: pattern of 1 review → 2 sentence items → 1 review → ...
        review_items: list[LearningItemRead] = []
        review_idx = 0
        sentence_idx = 0
        while len(review_items) < capped_limit:
            if review_idx < len(task_review_items):
                review_items.append(task_review_items[review_idx])
                review_idx += 1
            if len(review_items) >= capped_limit:
                break
            for _ in range(2):
                if sentence_idx < len(sentence_review_items):
                    review_items.append(sentence_review_items[sentence_idx])
                    sentence_idx += 1
                if len(review_items) >= capped_limit:
                    break
            if review_idx >= len(task_review_items) and sentence_idx >= len(sentence_review_items):
                break
        return review_items

    review_items: list[LearningItemRead] = task_review_items[:]
    for item_read in sentence_review_items:
        if len(review_items) >= capped_limit:
            break
        review_items.append(item_read)

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

    normalized_words = tokenize_words(payload.english_text)
    if len(normalized_words) == 1 and normalize_word(payload.english_text) == normalized_words[0]:
        translations = ensure_word_translations(db, current_user.id, normalized_words, translation_settings)
        db.commit()
        chinese_text = translations.get(normalized_words[0], "")
        if chinese_text:
            return LearningTranslationResponse(english_text=payload.english_text, chinese_text=chinese_text)

    try:
        chinese_text = translate_english_to_chinese(payload.english_text, translation_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return LearningTranslationResponse(english_text=payload.english_text, chinese_text=chinese_text)


@router.post("/word-translations", response_model=WordTranslationsResponse)
def get_word_translations(
    payload: WordTranslationsRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WordTranslationsResponse:
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(
        payload.llm_provider,
        payload.llm_base_url,
        payload.llm_model,
        payload.llm_api_key,
        stored_settings,
    )
    translations = ensure_word_translations(db, current_user.id, payload.words, translation_settings, payload.course_id)
    db.commit()
    return WordTranslationsResponse(translations=translations)


@router.get("/courses/{course_id}/cache-status", response_model=CourseCacheStatusResponse)
def get_course_cache_status(
    course_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseCacheStatusResponse:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    learning_items = db.scalars(
        select(LearningItem)
        .where(LearningItem.user_id == current_user.id, LearningItem.course_id == course_id)
        .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
    ).all()
    stored_settings = get_private_model_settings(db, current_user.id)
    course_terms = collect_course_terms(learning_items)
    term_translations = get_cached_word_translations(db, current_user.id, course_terms)
    speech_targets = build_learning_speech_targets(db, user_id=current_user.id, learning_items=learning_items, stored_settings=stored_settings)
    target_keys = {
        (
            target.language,
            target.voice,
            target.speech_rate,
            build_cache_key(target.text.strip(), target.voice, target.speech_rate),
        )
        for target in speech_targets
        if target.text.strip()
    }
    cached_asset_keys: set[tuple[str, str, int, str]] = set()
    if target_keys:
        rows = db.scalars(
            select(SpeechAsset).where(
                SpeechAsset.user_id == current_user.id,
                SpeechAsset.cached.is_(True),
                SpeechAsset.text_hash.in_([key[3] for key in target_keys]),
            )
        ).all()
        cached_asset_keys = {
            (row.language, row.voice, row.speech_rate, row.text_hash)
            for row in rows
        }

    def target_ready(text: str, language: str) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        for target in speech_targets:
            if target.language == language and target.text.strip() == normalized_text:
                key = (target.language, target.voice, target.speech_rate, build_cache_key(target.text.strip(), target.voice, target.speech_rate))
                return key in cached_asset_keys and get_cached_audio(target.text.strip(), target.voice, target.speech_rate) is not None
        return False

    def terms_translation_ready(terms: list[str]) -> bool:
        return all(term in term_translations for term in terms)

    def terms_english_audio_ready(terms: list[str]) -> bool:
        return all(target_ready(term, "en-US") for term in terms)

    def terms_chinese_audio_ready(terms: list[str]) -> bool:
        return all(term in term_translations and target_ready(term_translations[term], "zh-CN") for term in terms)

    item_statuses: list[CourseCacheItemStatus] = []
    sentence_ready_count = 0
    sentence_english_audio_ready_count = 0
    sentence_chinese_audio_ready_count = 0
    for item in learning_items:
        item_terms = collect_course_terms([item])
        chinese_ready = not needs_translation(item.chinese_text)
        sentence_english_audio_ready = target_ready(item.english_text, "en-US")
        sentence_chinese_audio_ready = chinese_ready and target_ready(item.chinese_text, "zh-CN")
        if chinese_ready:
            sentence_ready_count += 1
        if sentence_english_audio_ready:
            sentence_english_audio_ready_count += 1
        if sentence_chinese_audio_ready:
            sentence_chinese_audio_ready_count += 1
        item_statuses.append(
            CourseCacheItemStatus(
                learning_item_id=item.id,
                sentence_chinese_translation_ready=chinese_ready,
                sentence_english_audio_ready=sentence_english_audio_ready,
                sentence_chinese_audio_ready=sentence_chinese_audio_ready,
                word_translations_ready=terms_translation_ready(item_terms),
                word_english_audio_ready=terms_english_audio_ready(item_terms),
                word_chinese_audio_ready=terms_chinese_audio_ready(item_terms),
            )
        )

    word_english_audio_ready_count = sum(1 for term in course_terms if target_ready(term, "en-US"))
    word_chinese_audio_ready_count = sum(
        1
        for term in course_terms
        if term in term_translations and target_ready(term_translations[term], "zh-CN")
    )

    return CourseCacheStatusResponse(
        course_id=course_id,
        summary=CourseCacheStatusSummary(
            total_items=len(learning_items),
            sentence_translations_ready=sentence_ready_count,
            sentence_english_audio_ready=sentence_english_audio_ready_count,
            sentence_chinese_audio_ready=sentence_chinese_audio_ready_count,
            total_terms=len(course_terms),
            term_translations_ready=sum(1 for term in course_terms if term in term_translations),
            word_english_audio_ready=word_english_audio_ready_count,
            word_chinese_audio_ready=word_chinese_audio_ready_count,
            speech_assets_ready=sum(1 for key in target_keys if key in cached_asset_keys),
            total_speech_assets=len(target_keys),
        ),
        items=item_statuses,
    )


@router.post("/courses/{course_id}/cache-rebuild")
def rebuild_course_cache(
    course_id: UUID,
    payload: CourseCacheRebuildRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    learning_items = db.scalars(
        select(LearningItem)
        .where(LearningItem.user_id == current_user.id, LearningItem.course_id == course_id)
        .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
    ).all()
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(
        payload.llm_provider,
        payload.llm_base_url,
        payload.llm_model,
        payload.llm_api_key,
        stored_settings,
    )

    def event_stream() -> Iterator[str]:
        stats: dict[str, int] = {
            "items": len(learning_items),
            "sentence_translations": 0,
            "term_translations": 0,
            "speech_cached": 0,
            "speech_missing": 0,
            "errors": 0,
        }

        def emit(percent: int, message: str, status_value: str = "running") -> str:
            return json.dumps(
                {
                    "status": status_value,
                    "percent": max(0, min(percent, 100)),
                    "message": message,
                    "stats": stats,
                },
                ensure_ascii=False,
            ) + "\n"

        def stage_percent(start: int, end: int, index: int, total: int) -> int:
            if total <= 0:
                return end
            return start + round(((index + 1) / total) * (end - start))

        yield emit(2, "正在读取课程内容...")
        if not learning_items:
            yield emit(100, "当前课程没有学习内容", "done")
            return

        sentence_items = [item for item in learning_items if needs_translation(item.chinese_text)]
        if sentence_items:
            for index, item in enumerate(sentence_items):
                try:
                    item.chinese_text = translate_english_to_chinese(item.english_text, translation_settings)
                    db.add(item)
                    stats["sentence_translations"] += 1
                except ValueError:
                    stats["errors"] += 1
                if index % 5 == 4:
                    db.commit()
                yield emit(stage_percent(5, 35, index, len(sentence_items)), f"正在补全句子中文释义 {index + 1}/{len(sentence_items)}")
            db.commit()
        else:
            yield emit(35, "句子中文释义已完整")

        terms = collect_course_terms(learning_items)
        cached_terms = get_cached_word_translations(db, current_user.id, terms)
        missing_terms = [term for term in terms if term not in cached_terms]
        if missing_terms:
            for index, term in enumerate(missing_terms):
                before_count = len(get_cached_word_translations(db, current_user.id, [term]))
                translations = ensure_word_translations(db, current_user.id, [term], translation_settings, course_id)
                db.commit()
                if term in translations and before_count == 0:
                    stats["term_translations"] += 1
                else:
                    stats["errors"] += 1
                yield emit(stage_percent(36, 65, index, len(missing_terms)), f"正在补全单词/词组中文释义 {index + 1}/{len(missing_terms)}")
        else:
            yield emit(65, "单词和词组中文释义已完整")

        speech_targets = build_learning_speech_targets(db, user_id=current_user.id, learning_items=learning_items, stored_settings=stored_settings)
        if speech_targets:
            synthesis_failures = 0
            for index, target in enumerate(speech_targets):
                speech_asset, synthesis_failed = ensure_volcengine_speech_asset(
                    db,
                    user_id=current_user.id,
                    course_id=course_id,
                    target=target,
                    stored_settings=stored_settings,
                    allow_synthesis=synthesis_failures < 3,
                )
                if synthesis_failed:
                    synthesis_failures += 1
                    stats["errors"] += 1
                if speech_asset is not None and speech_asset.cached:
                    stats["speech_cached"] += 1
                else:
                    stats["speech_missing"] += 1
                if index % 10 == 9:
                    db.commit()
                yield emit(stage_percent(66, 98, index, len(speech_targets)), f"正在生成发音缓存 {index + 1}/{len(speech_targets)}")
            db.commit()
        else:
            yield emit(98, "没有需要生成的发音缓存")

        yield emit(100, "课程缓存重新生成完成", "done")

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/courses/{course_id}/cache-retry/{item_id}", response_model=CourseCacheStatusResponse)
def retry_item_cache(
    course_id: UUID,
    item_id: UUID,
    payload: CourseCacheItemRetryRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseCacheStatusResponse:
    """Re-generate only the FAILED cache fields for a single item.

    Unlike /cache-rebuild (which re-processes the whole course), this
    endpoint takes a single item ID and only re-runs the layers
    specified in the request. Used by the "重试" button in the
    import page next to each yellow status cell.
    """
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    item = db.scalar(
        select(LearningItem).where(
            LearningItem.id == item_id,
            LearningItem.user_id == current_user.id,
            LearningItem.course_id == course_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(
        payload.llm_provider,
        payload.llm_base_url,
        payload.llm_model,
        payload.llm_api_key,
        stored_settings,
    )

    errors: list[str] = []

    # Re-generate the sentence Chinese translation.
    if payload.sentence_chinese_translation:
        try:
            item.chinese_text = translate_english_to_chinese(item.english_text, translation_settings)
            db.add(item)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Cache retry: sentence translation failed for item %s: %s", item_id, exc)
            errors.append(f"句子翻译失败: {exc}")

    # Re-generate speech assets. precache_learning_speech_assets has
    # a 3-failure limit that stops trying after 3 TTS errors. For
    # retry, we bypass it by calling ensure_volcengine_speech_asset
    # directly with allow_synthesis=True (no failure cap).
    if payload.sentence_english_audio or payload.sentence_chinese_audio or payload.word_english_audio or payload.word_chinese_audio:
        try:
            from app.services.speech_asset_cache import build_learning_speech_targets, ensure_volcengine_speech_asset
            targets = build_learning_speech_targets(
                db, user_id=current_user.id, learning_items=[item], stored_settings=stored_settings
            )
            for target in targets:
                ensure_volcengine_speech_asset(
                    db,
                    user_id=current_user.id,
                    course_id=course_id,
                    target=target,
                    stored_settings=stored_settings,
                    allow_synthesis=True,
                )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Cache retry: speech precache failed for item %s: %s", item_id, exc)
            errors.append(f"语音生成失败: {exc}")

    # Re-generate word/term translations if requested.
    if payload.word_translations:
        item_terms = collect_course_terms([item])
        try:
            ensure_word_translations(db, current_user.id, item_terms, translation_settings, course_id)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Cache retry: word translations failed for item %s: %s", item_id, exc)
            errors.append(f"单词翻译失败: {exc}")

    # If everything failed, raise an error so the frontend knows.
    if errors and len(errors) >= 3:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="；".join(errors),
        )

    # Return the full updated cache status so the UI can refresh in-place.
    return get_course_cache_status(course_id, current_user, db)


def collect_course_terms(learning_items: list[LearningItem]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for item in learning_items:
        for word in tokenize_words(item.english_text):
            if word and word not in seen:
                seen.add(word)
                terms.append(word)
        if item.item_type in {"word", "phrase"}:
            phrase = " ".join(item.english_text.strip().lower().split())
            if phrase and phrase not in seen:
                seen.add(phrase)
                terms.append(phrase)
    return terms


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

    word_item = get_or_create_word_memory_item(db, current_user.id, payload.expected_word, learning_item)
    error_type = normalize_word_error_type(payload.error_type)
    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=word_item.id,
        score=1,
        review_mode="word-spelling",
        response_text=payload.actual_word.strip(),
        duration_seconds=0,
        error_type=error_type,
    )
    word_state = sync_word_memory_from_review(db, current_user.id, word_item.english_text, result.memory_state, "word-spelling", False, error_type)
    # Disabled: same reason as create_word_review — the focus mode provides
    # sufficient correction practice without creating 5 extra micro-review
    # tasks per mistake.
    # schedule_micro_review_tasks_for_mistake(db, current_user.id, word_state, learning_item.chinese_text, learning_item.id, error_type)
    try:
        from app.services.learning_replay import record_learning_event
        # Same duration-source fix as create_word_review: prefer the
        # client-reported duration over the gap-to-last-review heuristic,
        # which collapses to ~0 ms for consecutive submissions.
        total_seconds = int(result.review_log.duration_seconds or 0)
        encoding_ms = int(result.review_log.encoding_duration_ms or 0)
        if encoding_ms > 0:
            duration_ms = min(encoding_ms, 5 * 60 * 1000)
        elif total_seconds > 0:
            duration_ms = min(total_seconds * 1000, 5 * 60 * 1000)
        else:
            duration_ms = 20_000
        record_learning_event(db, current_user.id, result.review_log, word_item, duration_ms=duration_ms)
    except Exception as exc:
        logger.warning("Failed to record learning replay event for word mistake: %s", exc)
    db.commit()
    return WordMistakeLogResponse(logged_count=1)


@router.post("/word-reviews", response_model=WordReviewResponse, status_code=status.HTTP_201_CREATED)
def create_word_review(
    payload: WordReviewRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WordReviewResponse:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == payload.learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")

    word_item = get_or_create_word_memory_item(db, current_user.id, payload.word, learning_item)
    review_mode = payload.review_mode.strip()[:32]
    error_type = normalize_word_error_type(payload.error_type) if payload.error_type else None
    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=word_item.id,
        score=payload.score,
        review_mode=review_mode,
        response_text=(payload.response_text or "").strip(),
        duration_seconds=payload.duration_seconds,
        error_type=error_type,
        encoding_stage=payload.encoding_stage,
        encoding_duration_ms=payload.encoding_duration_ms,
    )
    word_state = sync_word_memory_from_review(db, current_user.id, word_item.english_text, result.memory_state, review_mode, result.review_log.is_correct, error_type)
    complete_word_review_task(db, current_user.id, payload.review_task_id, result.review_log.is_correct)
    if result.review_log.is_correct:
        supersede_stale_pending_tasks_for_reviewed_words(db, current_user.id)
        # Award points for correct word review
        try:
            from app.services.points_service import POINTS_CORRECT_HINTED, POINTS_CORRECT_NO_HINT, POINTS_CORRECT_PREVIEW, POINTS_PERFECT_SENTENCE, award_points
            if review_mode.startswith("word-recall"):
                award_points(db, current_user.id, POINTS_CORRECT_NO_HINT, "word_correct", f"无提示正确拼写 +{POINTS_CORRECT_NO_HINT}", word_item.id)
            elif review_mode.startswith("word-hinted"):
                award_points(db, current_user.id, POINTS_CORRECT_HINTED, "word_hinted", f"提示后正确拼写 +{POINTS_CORRECT_HINTED}", word_item.id)
            elif review_mode.startswith("word-preview"):
                award_points(db, current_user.id, POINTS_CORRECT_PREVIEW, "word_preview", f"预览后正确拼写 +{POINTS_CORRECT_PREVIEW}", word_item.id)
            elif review_mode.startswith("sentence-spelling") and payload.score >= 5:
                award_points(db, current_user.id, POINTS_PERFECT_SENTENCE, "perfect_sentence", f"整句完全正确 +{POINTS_PERFECT_SENTENCE}", word_item.id)
            else:
                award_points(db, current_user.id, POINTS_CORRECT_NO_HINT, "word_correct", f"正确拼写 +{POINTS_CORRECT_NO_HINT}", word_item.id)
        except Exception:
            pass  # points failure should never block learning
    if not result.review_log.is_correct:
        # Only create micro-review correction tasks during sentence
        # learning (sentence-spelling) — the child encounters new
        # words in context and needs immediate practice. Word-only
        # review in the focus mode already provides 3 modes per word,
        # so no extra tasks are needed there.
        if review_mode == "sentence-spelling":
            schedule_micro_review_tasks_for_mistake(db, current_user.id, word_state, learning_item.chinese_text, learning_item.id, error_type or "spelling")
        # Deduct points for wrong answer
        try:
            from app.services.points_service import POINTS_WRONG, award_points
            award_points(db, current_user.id, POINTS_WRONG, "word_wrong", f"拼写错误 {POINTS_WRONG}", word_item.id)
        except Exception:
            pass  # points failure should never block learning
    # Learning Replay: record event with the actual review duration.
    try:
        from app.services.learning_replay import record_learning_event
        # Prefer the client-reported encoding duration (precise ms timing of
        # the encoding stage), then fall back to the total review duration
        # captured by the client, then to a sensible default. The previous
        # implementation derived duration_ms from the gap to the user's LAST
        # review of any item, which (a) is not "time spent on this question"
        # and (b) collapses to ~0 ms when reviews are submitted back-to-back
        # in a single session — the timeline then shows every event as
        # instantaneous.
        encoding_ms = int(result.review_log.encoding_duration_ms or 0)
        total_seconds = int(result.review_log.duration_seconds or 0)
        if encoding_ms > 0:
            duration_ms = min(encoding_ms, 5 * 60 * 1000)
        elif total_seconds > 0:
            duration_ms = min(total_seconds * 1000, 5 * 60 * 1000)
        else:
            duration_ms = 20_000  # default for legacy clients
        record_learning_event(db, current_user.id, result.review_log, word_item, duration_ms=duration_ms)
    except Exception as exc:
        logger.warning("Failed to record learning replay event for word review: %s", exc)
    db.commit()
    return WordReviewResponse(learning_item_id=word_item.id, word=word_item.english_text)


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
        difficulty_level=payload.difficulty_level,
    )
    return DynamicSentenceResponse(
        english_text=result.english_text,
        chinese_text=result.chinese_text,
        focus_words=result.focus_words,
        known_words=result.known_words,
        weak_words=result.weak_words,
        candidates=[DynamicSentenceCandidate(**c) for c in result.candidates],
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

    imported_items, duplicate_skipped_items = import_learning_items(
        db,
        current_user.id,
        course_id,
        parse_result.items,
        translation_settings,
        stored_settings,
    )
    skipped_items = [*parse_result.skipped_items, *duplicate_skipped_items]

    return LearningImportResponse(
        imported_count=len(imported_items),
        skipped_count=len(skipped_items),
        total_rows=parse_result.total_rows,
        items=[LearningItemRead.model_validate(item) for item in imported_items],
        skipped_items=skipped_items,
    )
