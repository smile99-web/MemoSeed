import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
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
from app.services.memory_scheduler import calculate_current_forget_risk, calculate_review_priority, schedule_memory_review
from app.services.secure_model_settings import get_private_model_settings
from app.services.speech_asset_cache import build_learning_speech_targets, ensure_volcengine_speech_asset
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

WORD_MEMORY_SOURCE = "word-memory"
AI_CLOZE_CACHE_TYPE = "ai_cloze_sentence"
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
FALLBACK_CLOZE_SENTENCE_TEMPLATES = (
    ("I see {word}.", "我看见 {word} 这个单词。"),
    ("We like {word}.", "我们喜欢 {word} 这个单词。"),
    ("Please read {word}.", "请读出 {word} 这个单词。"),
    ("This word is {word}.", "这个单词是 {word}。"),
    ("Say {word} with me.", "和我一起说 {word}。"),
    ("I remember {word}.", "我记得 {word} 这个单词。"),
    ("Let us spell {word}.", "让我们拼写 {word}。"),
    ("Can you find {word}?", "你能找到 {word} 吗？"),
)


def choose_cloze_sentence_template(word: str, seed: str) -> tuple[str, str]:
    template_index = sum(ord(char) for char in seed) % len(FALLBACK_CLOZE_SENTENCE_TEMPLATES)
    english_template, chinese_template = FALLBACK_CLOZE_SENTENCE_TEMPLATES[template_index]
    return english_template.format(word=word), chinese_template.format(word=word)


def build_cloze_sentence_text(word: str, seed: str) -> str:
    english_text, _ = choose_cloze_sentence_template(word, seed)
    return english_text


def build_cloze_sentence_chinese_text(word: str, seed: str) -> str:
    _, chinese_text = choose_cloze_sentence_template(word, seed)
    return chinese_text


def parse_compact_json_object(raw_text: str) -> dict[str, object]:
    normalized_text = raw_text.strip()
    if normalized_text.startswith("```"):
        normalized_text = normalized_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    json_start = normalized_text.find("{")
    json_end = normalized_text.rfind("}")
    if json_start >= 0 and json_end >= json_start:
        normalized_text = normalized_text[json_start : json_end + 1]
    parsed = json.loads(normalized_text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM cloze sentence response is not a JSON object")
    return parsed


def get_cached_ai_cloze_sentence(task: WordReviewTask) -> tuple[str, str] | None:
    for entry in task.choices or []:
        if not isinstance(entry, dict) or entry.get("type") != AI_CLOZE_CACHE_TYPE:
            continue
        english_text = str(entry.get("english_text") or "").strip()
        chinese_text = str(entry.get("chinese_text") or "").strip()
        if english_text and chinese_text:
            return english_text, chinese_text
    return None


def validate_ai_cloze_sentence(word: str, english_text: str, chinese_text: str) -> tuple[str, str]:
    normalized_english = " ".join(english_text.strip().strip('"“”').split())
    normalized_chinese = chinese_text.strip().strip('"“”')
    if not normalized_english or not normalized_chinese:
        raise ValueError("LLM cloze sentence response is incomplete")
    if normalize_word(word) not in set(tokenize_words(normalized_english)):
        raise ValueError("LLM cloze sentence does not contain the review word")
    if len(tokenize_words(normalized_english)) > 7:
        raise ValueError("LLM cloze sentence is longer than 7 words")
    if normalized_english[-1] not in ".?!":
        normalized_english = f"{normalized_english}."
    return normalized_english, normalized_chinese


def generate_ai_cloze_sentence(word: str, settings: LlmTranslationSettings) -> tuple[str, str]:
    prompt = (
        "Generate one simple English sentence for a child to practice a review word. "
        "Return only compact JSON with keys english_text and chinese_text. "
        "Rules: english_text must contain the exact review word once, use 7 English words or fewer, "
        "be natural and different from fixed phrases like 'I can spell ...'. "
        "chinese_text must be a natural Simplified Chinese translation of english_text. "
        f"Review word: {word}"
    )
    body = parse_compact_json_object(generate_learning_text(prompt, settings))
    return validate_ai_cloze_sentence(word, str(body.get("english_text") or ""), str(body.get("chinese_text") or ""))


def build_cloze_sentence_pair(task: WordReviewTask, settings: LlmTranslationSettings | None) -> tuple[str, str, bool]:
    cached_pair = get_cached_ai_cloze_sentence(task)
    if cached_pair is not None:
        return cached_pair[0], cached_pair[1], False

    if settings is not None:
        try:
            english_text, chinese_text = generate_ai_cloze_sentence(task.word, settings)
            task.choices = [{"type": AI_CLOZE_CACHE_TYPE, "english_text": english_text, "chinese_text": chinese_text}]
            return english_text, chinese_text, True
        except ValueError:
            pass

    cloze_seed = f"{task.id}:{task.word}:{task.created_at.isoformat()}"
    return build_cloze_sentence_text(task.word, cloze_seed), build_cloze_sentence_chinese_text(task.word, cloze_seed), False


def build_micro_task_learning_item(
    task: WordReviewTask,
    source_item: LearningItem | None,
    current_user: User,
    cloze_settings: LlmTranslationSettings | None = None,
    db: Session | None = None,
) -> tuple[LearningItemRead, bool]:
    task_updated = False
    if task.task_type == "cloze_sentence":
        english_text, chinese_text, task_updated = build_cloze_sentence_pair(task, cloze_settings)
        review_prompt = chinese_text
        source = f"AI 动态复习：{task.word}"
        item_type = "sentence"
        raw_choices = []
        review_answer = task.expected_answer
    else:
        english_text = task.word
        chinese_text = task.prompt_text
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


def normalize_word_error_type(value: str | None) -> str:
    normalized = "".join(char for char in (value or "spelling").strip().lower() if char.isalnum() or char == "-")
    return normalized[:24] or "spelling"


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
        if source_item is not None and existing_item.chinese_text == source_item.chinese_text:
            existing_item.chinese_text = normalized_word
        return existing_item

    learning_item = LearningItem(
        user_id=user_id,
        course_id=None,
        item_type="word",
        english_text=normalized_word,
        chinese_text=normalized_word,
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
) -> list[LearningItemRead]:
    statement = select(LearningItem).where(LearningItem.user_id == current_user.id)
    if course_id is not None:
        statement = statement.where(LearningItem.course_id == course_id)
    statement = statement.order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
    if limit is not None and limit > 0:
        statement = statement.limit(limit)
    items = db.scalars(statement).all()
    return [LearningItemRead.model_validate(item) for item in items]


@router.get("/review-items", response_model=list[LearningItemRead])
def list_due_review_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    exclude_course_id: UUID | None = None,
    limit: int = 12,
    review_cap: int | None = None,
    interleave: bool = False,
) -> list[LearningItemRead]:
    """List due review items.

    When interleave=True, review tasks and new items are interleaved (1:2 ratio)
    and review tasks are capped to avoid front-loading fatigue.
    """
    capped_limit = max(1, min(limit, 30))
    effective_review_cap = review_cap if review_cap is not None else capped_limit
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

    stored_settings = get_private_model_settings(db, current_user.id)
    cloze_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    has_task_updates = supersede_stale_pending_tasks_for_reviewed_words(db, current_user.id, now)

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

    has_task_updates = ensure_due_word_review_tasks(db, current_user.id, now, max(capped_limit, effective_review_cap) * 3) or has_task_updates
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
        .limit(effective_review_cap * 3)
    ).all()
    covered_task_words = {task.word for task, _source_item in task_rows}
    task_review_items: list[LearningItemRead] = []
    queued_task_words: set[str] = set()
    deferred_task_rows: list[tuple[WordReviewTask, LearningItem | None]] = []
    for task, source_item in task_rows:
        if task.word in queued_task_words:
            deferred_task_rows.append((task, source_item))
            continue
        task_item, task_updated = build_micro_task_learning_item(task, source_item, current_user, cloze_settings, db)
        task_review_items.append(task_item)
        has_task_updates = has_task_updates or task_updated
        queued_task_words.add(task.word)
        if len(task_review_items) >= effective_review_cap:
            break
    if len(task_review_items) < effective_review_cap:
        for task, source_item in deferred_task_rows:
            task_item, task_updated = build_micro_task_learning_item(task, source_item, current_user, cloze_settings, db)
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

    # Build sentence-level review items
    sentence_review_items: list[LearningItemRead] = []
    for item in item_by_id.values():
        if item.item_type == "word" and item.source == WORD_MEMORY_SOURCE and normalize_word(item.english_text) in covered_task_words:
            continue
        item_read = LearningItemRead.model_validate(item)
        focus_words = focus_words_by_item_id.get(item.id, [])
        if focus_words:
            item_read = item_read.model_copy(update={"source": f"AI 动态复习：{', '.join(focus_words)}"})
        sentence_review_items.append(item_read)

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
    schedule_micro_review_tasks_for_mistake(db, current_user.id, word_state, learning_item.chinese_text, learning_item.id, error_type)
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
        schedule_micro_review_tasks_for_mistake(db, current_user.id, word_state, learning_item.chinese_text, learning_item.id, error_type or "spelling")
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
