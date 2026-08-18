import json
import logging
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.course import Course
from app.models.course_package import CoursePackage
from app.models.learning_event import LearningEvent
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.review_log import ReviewLog
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
    HandwritingCheckRequest,
    HandwritingCheckResponse,
    LearningEncouragementRequest,
    LearningEncouragementResponse,
    LearningImportResponse,
    LearningItemCreate,
    LearningItemRead,
    LearningTranslationRequest,
    LearningTranslationResponse,
    PronunciationCheckResponse,
    ReadAloudEventRequest,
    ReadAloudEventResponse,
    WordMistakeLogRequest,
    WordMistakeLogResponse,
    WordReviewRequest,
    WordReviewResponse,
    WordTranslationsRequest,
    WordTranslationsResponse,
)
from app.services.dynamic_sentence import generate_dynamic_review_sentence
from app.services.learning_import import SUPPORTED_IMPORT_EXTENSIONS, import_learning_items, parse_txt_import, parse_xlsx_import
from app.services.llm_translation import AGENT_PLAN_DEFAULT_BASE_URL, DEFAULT_LLM_TRANSLATION_SETTINGS, LlmTranslationSettings, generate_learning_text, needs_translation, resolve_llm_credentials, translate_english_to_chinese, with_agent_plan_primary
from app.services.memory_dashboard import calculate_word_priority
from app.services.memory_scheduler import (
    ASSISTED_REVIEW_MODES,
    DAILY_REVIEW_ITEM_BUDGET,
    LOCAL_TIMEZONE,
    MAX_DAILY_REVIEWS_PER_WORD,
    SIGHT_WORDS,
    calculate_current_forget_risk,
    calculate_review_priority,
    exceeded_daily_review_filter_clause,
    is_leech_word,
    park_leech_words,
    run_park_suite,
    schedule_memory_review,
    smooth_overdue_backlog,
    stuck_word_daily_cap_filter_clause,
)
from app.services.pronunciation import recognize_speech_flash, score_pronunciation
from app.services.secure_model_settings import get_private_model_settings
from app.services.handwriting import (
    DAILY_TEST_WORD_LIMIT,
    DEFAULT_VISION_MODEL,
    HANDWRITING_BOTH_REVIEW_MODE,
    HANDWRITING_BOTH_TASK_TYPE,
    HANDWRITING_COURSE_PACKAGE_NAMES,
    HANDWRITING_DAILY_CAP,
    HANDWRITING_DICTATION_REVIEW_MODE,
    HANDWRITING_DICTATION_TASK_TYPE,
    HANDWRITING_REVIEW_MODES,
    HANDWRITING_TRANSLATION_REVIEW_MODE,
    HANDWRITING_TRANSLATION_TASK_TYPE,
    MAX_IMAGE_DATA_URL_CHARS,
    MAX_SENTENCE_WORDS,
    SENTENCE_HANDWRITING_REVIEW_MODE,
    compose_daily_handwriting_queue,
    handwriting_task_for,
    judge_handwriting,
    parse_lesson_number,
    pick_daily_test_words,
)
from app.services.speak_practice import (
    READ_ALOUD_REVIEW_MODE,
    ECHO_READ_REVIEW_MODE,
    READ_ALOUD_TASK_TYPE,
    SPEAK_DAILY_CAP,
    select_speak_candidates,
)
from app.services.speech_asset_cache import build_learning_speech_targets, ensure_volcengine_speech_asset, precache_learning_speech_assets
from app.services.tts_cache import build_cache_key, is_audio_cached
from app.services.word_memory import (
    DIM_GRADUATION_DAYS,
    TASK_TYPE_LABELS,
    _update_dimension_progress,
    build_task_choices,
    build_task_prompt,
    choose_task_sequence,
    complete_word_review_task,
    derive_word_status,
    get_or_create_word_memory_state,
    get_recent_word_test_stats,
    schedule_micro_review_tasks_for_mistake,
    supersede_stale_pending_tasks_for_reviewed_words,
    sync_word_memory_from_review,
)
from app.services.word_translation_cache import ensure_word_translations, get_cached_word_translations, sanitize_word_translation
from app.utils import extract_mistake_words, normalize_word, string_setting, tokenize_words

router = APIRouter()
logger = logging.getLogger(__name__)

# 每日一测（2026-08-11 三关重构）：每词连续三关——听音选中文（发音→意思）、
# 看英文选中文（形→意思）、手写英文（拼写）。提交统一打 context="daily-test"。
DAILY_TEST_CONTEXT = "daily-test"
DAILY_TEST_GATES: list[tuple[str, str | None]] = [
    ("listen_choose_chinese", "听英文发音，选择正确的中文意思"),
    ("english_to_chinese", "选择 {word} 的中文意思"),
    ("handwriting_dictation", None),
]

WORD_MEMORY_SOURCE = "word-memory"
MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024  # 10 MB upload cap for imports
MAX_PRONUNCIATION_AUDIO_BYTES = 2 * 1024 * 1024  # ~10 s of 16 kHz mono WAV
MAX_PRONUNCIATION_TEXT_CHARS = 200
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
def generic_word_distractors() -> list[str]:
    """Shuffled copy of the generic fallback pool.

    2026-08-11 修复：兜底池此前按固定顺序取前 5 个，DB 干扰项查询失败/不足时
    每道题的干扰项都是同一组（老师/学生/朋友/书/学校），只有正确答案在变。
    现在每次洗牌，兜底场景下干扰项也逐题随机。
    """
    pool = list(GENERIC_WORD_DISTRACTORS)
    random.shuffle(pool)
    return pool
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
CHINESE_SENTENCE_MARKERS = set("。！？：,.!?;: \n\r\t")  # meaning separators ，；、 are allowed in word choices
MAX_WORD_CHOICE_LENGTH = 24  # multi-meaning translations (up to 3 meanings) need more than a single-word cap


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
    # Use pre-cached Chinese translation if available. NEVER fall back
    # to task.prompt_text — that's the English question prompt (e.g.
    # "What does 'apple' mean?"), NOT the Chinese answer. Previously
    # the `if cached_translation` check was falsy for the empty
    # string, so an empty cache entry would silently copy the
    # English prompt into chinese_text, creating an English/Chinese
    # mismatch. If no cached translation exists, leave chinese_text
    # empty so the LLM call in _enrich_review_choices fills it later.
    cached_translation = (word_translations or {}).get(task.word.strip().lower(), "")
    chinese_text = cached_translation  # may be empty
    # 手写化（2026-08-02）：键盘拼写微任务统一改写为手写听写（听发音+
    # 看中文→画板手写）。手写卡片不消费 review_prompt，键盘题遗留的
    # 提示文案（如"补全缺失字母：a _ p _ e"）对孩子是噪音，置空。
    served_task_type = handwriting_task_for(task.task_type)
    is_handwriting_served = served_task_type == HANDWRITING_DICTATION_TASK_TYPE
    review_prompt = None if is_handwriting_served else task.prompt_text
    source = f"微型任务：{TASK_TYPE_LABELS.get(served_task_type or '', served_task_type)}：{task.word}"
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
        review_task_type=served_task_type,
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

    for choice in generic_word_distractors():
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
    if len(text) > MAX_WORD_CHOICE_LENGTH:
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
    # voice_practice giveups report "pronunciation" — without whitelist entry
    # they were silently relabeled "spelling", corrupting the error profile.
    "pronunciation",
})


# Plan A: an attempt this similar to the target is a near-miss, not a lapse.
NEAR_MISS_SIMILARITY = 0.8

# 一期改造(2026-08-18): 失误(slip)与不会(gap)分离。
# 手滑型错误 = 字面高度相似 + 错因属于"笔误类"(相邻字母颠倒/多字母/漏字母/
# 词尾小错)。这类错误说明孩子其实会这个词,只是手上出错:记 lapse、清连对、
# 推长间隔都是错误反应——正确反应是原地再答一次。first-letter/middle/unknown
# 不在此列(那是音形映射没建立,是真不会)。
SLIP_ERROR_TYPES = frozenset({"sequence", "missing-letter", "extra-letter", "ending"})
SLIP_MIN_SIMILARITY = 0.75
SLIP_HANDWRITING_MIN_SIMILARITY = 0.8


def spelling_similarity(expected: str, actual: str) -> float:
    """Letter-level similarity between the expected word and the child's attempt.

    P13: drives partial credit — a 9-letter word with 8 correct letters is a
    different signal than a blank guess. Uses difflib ratio on lowercased,
    stripped inputs; 0.0 when either side is empty.
    """
    from difflib import SequenceMatcher

    expected_norm = expected.strip().lower()
    actual_norm = actual.strip().lower()
    if not expected_norm or not actual_norm:
        return 0.0
    return SequenceMatcher(None, expected_norm, actual_norm).ratio()


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

    def _is_valid_chinese(eng: str, ch: str | None) -> bool:
        if not ch or not ch.strip(): return False
        if not any("一" <= c <= "鿿" for c in ch): return False
        if ch.strip().lower() == eng.strip().lower(): return False
        # Word-level Chinese may hold several common meanings separated by ，；、
        if len(ch) > 24 or any(p in ch for p in ("。","！","？","……")): return False
        return True

    existing_item = db.scalar(
        select(LearningItem).where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
            LearningItem.source == WORD_MEMORY_SOURCE,
            LearningItem.english_text == normalized_word,
        )
    )
    if existing_item is not None:
        if not existing_item.chinese_text or not any("\u4e00" <= c <= "\u9fff" for c in existing_item.chinese_text):
            if source_item is not None:
                from app.services.word_translation_cache import get_cached_word_translations
                cached = get_cached_word_translations(db, user_id, [normalized_word])
                word_tr = cached.get(normalized_word, "")
                if _is_valid_chinese(normalized_word, word_tr):
                    existing_item.chinese_text = word_tr
                elif source_item is not None and _is_valid_chinese(normalized_word, source_item.chinese_text):
                    existing_item.chinese_text = source_item.chinese_text
        return existing_item

    initial_chinese = ""
    from app.services.word_translation_cache import get_cached_word_translations
    cached = get_cached_word_translations(db, user_id, [normalized_word])
    initial_chinese = cached.get(normalized_word, "") if _is_valid_chinese(normalized_word, cached.get(normalized_word, "")) else ""
    if not initial_chinese and source_item is not None:
        src_ch = source_item.chinese_text if _is_valid_chinese(normalized_word, source_item.chinese_text) else ""
        if src_ch:
            initial_chinese = src_ch
    # If still empty, leave blank — the translation service fills it later
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
    # resolve_llm_credentials enforces the custom-base_url-requires-own-key
    # rule (SSRF/key exfiltration guard, same as /tts/speech).
    base = resolve_llm_credentials(
        stored_settings,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
    # Agent Plan as primary (plan quota), legacy config as its fallback.
    # Explicit per-request overrides (cache tooling) skip the wrap.
    return with_agent_plan_primary(
        base,
        stored_settings,
        overrides_given=bool(llm_provider or llm_base_url or llm_model or llm_api_key),
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
        select(LearningItem, MemoryState)
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
    item_rows = list(db.execute(statement).all())

    cloze_by_item_id: dict[UUID, list[str]] = {}
    if course_id is not None:
        # N2/N3/N4: course sentences are the new-word teaching path. For each
        # sentence find its WEAK words (no WordMemoryState at all, or status
        # teaching/difficult): mark them as focus_words so the frontend warms
        # them up before the sentence (N4) and blanks only them for typing
        # (N2 cloze, ~15s instead of ~80s whole-sentence typing); and serve
        # never-studied sentences easiest-first so each new sentence
        # introduces as few unknown words as possible (N3, i+1 input).
        sentence_words: set[str] = set()
        for item, _ms in item_rows:
            if item.item_type == "sentence":
                sentence_words.update(w.strip().lower() for w in tokenize_words(item.english_text or "") if w.strip())
        weak_status_by_word: dict[str, tuple[float, str]] = {}
        if sentence_words:
            course_word_states = db.scalars(
                select(WordMemoryState).where(
                    WordMemoryState.user_id == current_user.id,
                    WordMemoryState.word.in_(list(sentence_words)),
                )
            ).all()
            status_by_word = {ws.word: (ws.memory_strength or 0.0, ws.status or "") for ws in course_word_states}
            weak_status_by_word = {
                w: v for w, v in ((w, status_by_word.get(w, (0.0, ""))) for w in sentence_words)
                # Sight words never become cloze/warmup targets: they are
                # given text in sentence spelling (2026-08-10). Their
                # perpetual "difficult" status (from forced re-typing
                # failures) used to make EVERY sentence blank the/a/is
                # instead of its real vocabulary.
                if v[1] in ("teaching", "difficult", "") and w not in SIGHT_WORDS
            }
        for item, _ms in item_rows:
            if item.item_type != "sentence":
                continue
            weak = [w for w in (w.strip().lower() for w in tokenize_words(item.english_text or "")) if w in weak_status_by_word]
            if weak:
                weak.sort(key=lambda w: weak_status_by_word[w][0])
                cloze_by_item_id[item.id] = weak[:2]

        # N3: i+1 ordering — never-studied sentences sorted by weak-word
        # count (fewest unknown words first), then import order. Studied
        # items keep their FSRS order at the front.
        new_sentence_ids = {
            item.id for item, ms in item_rows
            if ms is None and item.item_type == "sentence"
        }
        if new_sentence_ids:
            studied_rows = [(item, ms) for item, ms in item_rows if item.id not in new_sentence_ids]
            new_rows = [(item, ms) for item, ms in item_rows if item.id in new_sentence_ids]
            new_rows.sort(key=lambda row: (len(cloze_by_item_id.get(row[0].id, [])), row[0].sort_order or 0))
            item_rows = studied_rows + new_rows

    items = [row[0] for row in item_rows]

    def _apply_course_cloze(item_read: LearningItemRead) -> LearningItemRead:
        weak = cloze_by_item_id.get(item_read.id)
        if weak:
            return item_read.model_copy(update={"focus_words": weak, "review_task_type": "cloze_sentence"})
        return item_read

    result = [_apply_course_cloze(LearningItemRead.model_validate(item)) for item in items]

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
                enriched.append(_apply_course_cloze(LearningItemRead.model_validate(item)))
                continue
            normalized = normalize_word(item.english_text or "")
            if not normalized or normalized in seen_words:
                enriched.append(_apply_course_cloze(LearningItemRead.model_validate(item)))
                continue
            seen_words.add(normalized)

            # Build distractors from the user's own word database
            choices, correct_answer = _enrich_choices_for_word(
                db, current_user.id, normalized, item, cloze_settings
            )
            # Skip if no Chinese translation could be found
            if not choices or not correct_answer:
                enriched.append(_apply_course_cloze(LearningItemRead.model_validate(item)))
                continue
            choice_item = LearningItemRead(
                id=uuid4(),
                source_item_id=item.id,
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
            enriched.append(_apply_course_cloze(LearningItemRead.model_validate(item)))

        # Active voice practice in learn mode: sprinkle read-aloud tasks onto
        # familiar content so the child speaks aloud during new-word learning
        # too (parent asked for "单词复习 + 新句子学习" coverage).
        learn_voice = _build_voice_practice_items(db, current_user.id, enriched)
        if learn_voice:
            enriched = _interleave_voice(enriched, learn_voice, step=7)

        # Prune to limit if needed
        if limit is not None and limit > 0:
            return enriched[:limit]
        return enriched

    # Non-include_choices path: still inject voice practice.
    voice = _build_voice_practice_items(db, current_user.id, result)
    if voice:
        result = _interleave_voice(result, voice, step=7)
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

    # Step 3: fallback to the (shuffled) generic distractor pool
    for choice in generic_word_distractors():
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


def _build_voice_practice_items(
    db: Session,
    user_id: UUID,
    existing_items: list[LearningItemRead],
) -> list[LearningItemRead]:
    """Pick familiar word/sentence items for active voice practice.

    Returns synthetic LearningItemRead objects with review_task_type=
    "voice_practice". Items are sourced from LearningItem rows the child has
    already studied (MemoryState.repetition_count >= 3) and that are NOT
    already in the existing queue (avoids testing the same word twice in one
    session). Capped at 4 per queue so voice work stays a light complement.
    """
    VOICE_PRACTICE_MAX_PER_QUEUE = 4
    # Collect english_text already in the queue so we don't duplicate.
    existing_texts: set[str] = set()
    for item in existing_items:
        eng = (item.english_text or "").strip().lower()
        if eng:
            existing_texts.add(eng)
    candidates = db.execute(
        select(LearningItem, MemoryState.repetition_count)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.item_type.in_(["word", "phrase", "sentence"]),
            MemoryState.repetition_count >= 3,
        )
        .order_by(MemoryState.repetition_count.desc(), LearningItem.id)
        .limit(40)
    ).all()
    voice_items: list[LearningItemRead] = []
    for cand_item, _reps in candidates:
        if len(voice_items) >= VOICE_PRACTICE_MAX_PER_QUEUE:
            break
        eng = (cand_item.english_text or "").strip()
        if not eng or eng.lower() in existing_texts:
            continue
        # 视觉词（the, I, is, are...）不需要朗读练习——孩子在每句话里都会
        # 读到它们，单独练"the"的发音没有教学价值。
        if cand_item.item_type == "word" and eng.lower() in SIGHT_WORDS:
            continue
        voice_items.append(
            LearningItemRead(
                id=uuid4(),
                source_item_id=cand_item.id,
                user_id=user_id,
                course_id=cand_item.course_id,
                item_type=cand_item.item_type,
                english_text=eng,
                chinese_text=(cand_item.chinese_text or "").strip(),
                review_task_type="voice_practice",
                review_prompt="🎤 跟我读一遍",
                review_answer=eng,
                source="主动发音练习",
                focus_words=[eng.lower()] if cand_item.item_type == "word" else [],
                created_at=cand_item.created_at,
                updated_at=cand_item.updated_at,
            )
        )
        existing_texts.add(eng.lower())
    return voice_items


def _interleave_voice(
    base_items: list[LearningItemRead],
    voice_items: list[LearningItemRead],
    *,
    step: int = 6,
) -> list[LearningItemRead]:
    """Splice voice_practice items evenly into the base queue.

    After every `step` regular items, insert one voice item. No regular
    items are removed - the voice items are pure additions.
    """
    if not voice_items:
        return base_items
    merged: list[LearningItemRead] = []
    queue_idx = 0
    voice_idx = 0
    s = max(1, step)
    while queue_idx < len(base_items) or voice_idx < len(voice_items):
        for _ in range(s):
            if queue_idx < len(base_items):
                merged.append(base_items[queue_idx])
                queue_idx += 1
        if voice_idx < len(voice_items):
            merged.append(voice_items[voice_idx])
            voice_idx += 1
    return merged


@router.get("/review-items", response_model=list[LearningItemRead])
def list_due_review_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    exclude_course_id: UUID | None = None,
    limit: int = 12,
    review_cap: int | None = None,
    interleave: bool = False,
    focus: bool = False,
    phonics: bool = False,
) -> list[LearningItemRead]:
    """List due review items.

    The interleave parameter is kept for API compatibility but no longer
    changes the queue shape (2026-08-09: the interleave branch had been
    unreachable dead code — the sentence_review_items block above always
    returned first — so it was removed rather than left as a trap).

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
    # building the due queue. run_park_suite keeps the required order
    # (leech breaker first) and throttles the whole suite to once per user
    # per few minutes — it used to run 5 bulk UPDATEs + commits on EVERY
    # queue fetch.
    run_park_suite(db, current_user.id, now)
    # P2: spread any oversized overdue backlog across the next few days, then
    # apply the daily review budget (distinct items already served today).
    # Without this, 280+ due items made "priority order" meaningless and the
    # child never reached the end of the queue.
    smooth_overdue_backlog(db, current_user.id, now)
    reviewed_today_count = int(
        db.scalar(
            select(func.count(func.distinct(ReviewLog.learning_item_id))).where(
                ReviewLog.user_id == current_user.id,
                ReviewLog.reviewed_at >= today_start,
            )
        )
        or 0
    )
    daily_budget_remaining = max(DAILY_REVIEW_ITEM_BUDGET - reviewed_today_count, 0)
    cloze_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    has_task_updates = supersede_stale_pending_tasks_for_reviewed_words(db, current_user.id, now)

    # Word-centric: prioritize word-type items first, then sentences as context
    # Per-day review caps prevent a handful of unlearnable words from
    # dominating the queue (see memory_scheduler MAX_DAILY_REVIEWS_PER_WORD).
    # 2026-08-16: the old lapse>=10&strength<0.3 queue filter was removed —
    # the Q1 park system replaced that definition, and the filter made
    # "recovering" words (consecutive_correct>0 but lapse>=10, strength<0.3)
    # invisible: not served, not parked, not counted anywhere.
    due_statement = (
        select(LearningItem, MemoryState)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == current_user.id,
            MemoryState.next_review_at <= now,
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
    # P2: cap the due queue by the remaining daily budget. Micro-review tasks
    # (below) are still served when the budget is exhausted — they are the
    # in-flight correction machinery, not new due work.
    due_rows = due_rows[:daily_budget_remaining] if daily_budget_remaining > 0 else []
    # P19: new-word gate — ALWAYS on. The old version only engaged when the
    # backlog was >= 30 items, so a drained queue let 81 new words flood in
    # over 14 days (each then needing 8-10 min of teaching). New words enter
    # only when the child is succeeding on REAL tests (7-day accuracy >= 70%,
    # assisted 100%-correct phases excluded) AND within a hard budget: at
    # most 3 new items/day and 10/week. Feeding new words into a struggling
    # child just manufactures tomorrow's failures.
    NEW_WORD_GATE_MIN_ACCURACY = 0.70
    NEW_WORD_DAILY_CAP = 3
    NEW_WORD_WEEKLY_CAP = 10
    total_7d, correct_7d = db.execute(
        select(
            func.count(ReviewLog.id),
            func.coalesce(func.sum(case((ReviewLog.is_correct, 1), else_=0)), 0),
        ).where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.reviewed_at >= now - timedelta(days=7),
            ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES)),
        )
    ).one()
    week_accuracy = (correct_7d / total_7d) if total_7d else 1.0
    # "New today" = items whose FIRST-EVER review happened today (same for
    # the week, Monday-start local). They consume the new-word budget.
    local_today = now.astimezone(LOCAL_TIMEZONE).date()
    week_start = datetime.combine(
        local_today - timedelta(days=local_today.weekday()), datetime.min.time(), tzinfo=LOCAL_TIMEZONE
    ).astimezone(UTC)
    new_today = db.scalar(
        select(func.count()).select_from(
            select(ReviewLog.learning_item_id)
            .where(ReviewLog.user_id == current_user.id)
            .group_by(ReviewLog.learning_item_id)
            .having(func.min(ReviewLog.reviewed_at) >= today_start)
            .subquery()
        )
    ) or 0
    new_this_week = db.scalar(
        select(func.count()).select_from(
            select(ReviewLog.learning_item_id)
            .where(ReviewLog.user_id == current_user.id)
            .group_by(ReviewLog.learning_item_id)
            .having(func.min(ReviewLog.reviewed_at) >= week_start)
            .subquery()
        )
    ) or 0
    new_word_budget = min(NEW_WORD_DAILY_CAP - new_today, NEW_WORD_WEEKLY_CAP - new_this_week)
    if week_accuracy < NEW_WORD_GATE_MIN_ACCURACY:
        new_word_budget = 0
    if new_word_budget <= 0:
        due_rows = [(item, ms) for item, ms in due_rows if (ms.repetition_count or 0) > 0]
    else:
        admitted_new = 0
        gated_rows: list[tuple[LearningItem, MemoryState]] = []
        for item, ms in due_rows:
            if (ms.repetition_count or 0) > 0 or admitted_new < new_word_budget:
                if (ms.repetition_count or 0) == 0:
                    admitted_new += 1
                gated_rows.append((item, ms))
        due_rows = gated_rows
    for item, _memory_state in due_rows:
        item_by_id.setdefault(item.id, item)

    # P18: words that already hit today's attempt cap (>= MAX_DAILY_REVIEWS_PER_WORD
    # scored attempts on their word-level item) are excluded EVERYWHERE — the
    # due queue filter above only covers due_rows; micro-review tasks and
    # focus items must not sneak the same word back in.
    over_cap_words: set[str] = {
        w
        for (w,) in db.execute(
            select(LearningItem.english_text).where(
                LearningItem.user_id == current_user.id,
                LearningItem.item_type == "word",
                LearningItem.id.in_(
                    select(ReviewLog.learning_item_id)
                    .where(
                        ReviewLog.user_id == current_user.id,
                        ReviewLog.reviewed_at >= today_start,
                    )
                    .group_by(ReviewLog.learning_item_id)
                    .having(func.count(ReviewLog.id) >= MAX_DAILY_REVIEWS_PER_WORD)
                ),
            )
        ).all()
        if w
    }
    over_cap_words = {normalize_word(w) for w in over_cap_words}

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
        .outerjoin(WordMemoryState, WordMemoryState.id == WordReviewTask.word_memory_state_id)
        .where(
            WordReviewTask.user_id == current_user.id,
            WordReviewTask.status == "pending",
            WordReviewTask.due_at <= now,
            # Respect the rotation / micro-spacing clock: when the word's
            # memory state has been pushed into the future (focus rotation),
            # its tasks must not be served early.
            or_(
                WordReviewTask.word_memory_state_id.is_(None),
                WordMemoryState.next_micro_review_at.is_(None),
                WordMemoryState.next_micro_review_at <= now,
            ),
        )
        .order_by(WordReviewTask.priority_score.desc(), WordReviewTask.due_at.asc())
        .limit(min(effective_review_cap + 15, 35))  # keep session manageable
    ).all()
    # Compute AFTER type filtering below (covered words must match served tasks)
    # P1-5: Filter out removed task types (recall_word, cloze_sentence).
    REMOVED_TASK_TYPES = {"recall_word", "cloze_sentence"}
    task_rows = [(t, s) for t, s in task_rows if t.task_type not in REMOVED_TASK_TYPES]
    # P18: skip tasks for words that already hit today's attempt cap.
    task_rows = [(t, s) for t, s in task_rows if t.word.strip().lower() not in over_cap_words]
    covered_task_words = {task.word for task, _source_item in task_rows}
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
        # Persist LLM/dictionary cache writes deterministically. Previously
        # they only survived when the unrelated has_task_updates flag happened
        # to trigger a commit later; otherwise the same words were re-sent to
        # the LLM on every session.
        db.commit()
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
        .outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            MistakeLog.user_id == current_user.id,
            MistakeLog.is_resolved.is_(False),
            LearningItem.user_id == current_user.id,
            # Respect the review clock: mistakes on items that are not due
            # yet (e.g. just-rotated focus words) must not force the item
            # back into today's queue.
            or_(MemoryState.next_review_at.is_(None), MemoryState.next_review_at <= now),
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
        # Guard: skip items with invalid Chinese (empty, English-as-Chinese, sentence-level).
        # Word items may hold several common meanings separated by \uff0c\uff1b\u3001 (multi-meaning
        # learning), so they only reject sentence-ending punctuation and get a longer cap.
        ch = item.chinese_text or ""
        eng = item.english_text or ""
        if item.item_type == "word":
            invalid_chinese = len(ch) > 24 or any(p in ch for p in ("\u3002","\uff01","\uff1f","\u2026\u2026"))
        else:
            invalid_chinese = len(ch) > 15 or any(p in ch for p in ("\u3002","\uff01","\uff1f","\u2026\u2026","\uff0c","\uff1b"))
        if not any("\u4e00" <= c <= "\u9fff" for c in ch) or ch.strip().lower() == eng.strip().lower() or invalid_chinese:
            continue
        item_read = LearningItemRead.model_validate(item)
        focus_words = focus_words_by_item_id.get(item.id, [])
        if focus_words:
            item_read = item_read.model_copy(update={"source": f"AI 动态复习：{', '.join(focus_words)}"})
        sentence_review_items.append(item_read)
    # Sort by priority: highest-risk cross-course items first
    sentence_review_items.sort(key=lambda it: -item_priority.get(it.id, 0.0))

    # P20: whole-sentence typing is the most expensive mode (~80s per attempt
    # at 35% accuracy — the single biggest time sink in the 72h analysis).
    # Cap it at 3 sentences/day; beyond that the session is word work only.
    SENTENCE_DAILY_CAP = 3
    sentence_attempts_today = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.review_mode.like("sentence-%"),
        )
    ) or 0
    if sentence_attempts_today >= SENTENCE_DAILY_CAP:
        sentence_review_items = [it for it in sentence_review_items if it.item_type != "sentence"]

    if sentence_review_items:
        # Multi-mode review: for each due word, generate 3 question types
        # (listen_choose_chinese, english_to_chinese, chinese_to_english)
        # so the child gets a mix of recognition and spelling practice.
        # In focus mode (small batch), cap at 3 words; in normal mode cap
        # at 10 words — enough to give variety without overwhelming the
        # session. Previously this was gated by `if focus and ...` so the
        # word review mode had NO choice tasks when focus was off, leaving
        # only pure-spelling items from the WordReviewTask table.
        REVIEW_WORD_COUNT = 3 if focus else len(sentence_review_items)
        import random
        # Shuffle ONLY the leading pool used for word-review modes. `max`
        # here made the slice cover the whole list — destroying the FSRS
        # priority sort above and serving lowest-priority items first.
        pool = sentence_review_items[:min(REVIEW_WORD_COUNT * 3, len(sentence_review_items))]
        random.shuffle(pool)
        sentence_review_items = pool + sentence_review_items[len(pool):]
        # Mixed mode set: recognition tasks first (build confidence),
        # then handwriting dictation (apply what was just reviewed).
        # The old set [chinese_to_english, listen_spell, missing_letter]
        # was 100% spelling — children with many lapsed words would get
        # stuck in an infinite loop of failing at spelling, creating more
        # spelling micro-review tasks, and never seeing a recognition task.
        # english_to_chinese = 57% acc, listen_choose_chinese = 55% acc
        # — these give the child a chance to succeed before attempting
        # the harder production mode.
        # 手写化（2026-08-02）：产出环节统一为手写听写，键盘拼写下线。
        # 题型集合见 modes_for_word 各链路（识别：listen_choose_chinese /
        # english_to_chinese；产出：handwriting_dictation）。
        # N1: the new-word bootstrap chain — recognition first, handwritten
        # production last. Stage index = number of REAL tests so far.
        # 2026-08-11: 每阶段只出一种题 → 新词要到第 3 次课才第一次手写，
        # 家长反馈"手写和英选中频率太低"。改为每阶段一条短链：首次接触
        # 双识别（听选+英选中），第二次起意思确认 + 手写产出。
        N1_BOOTSTRAP_CHAINS = [
            ["listen_choose_chinese", "english_to_chinese"],
            ["english_to_chinese", "handwriting_dictation"],
        ]

        # Build per-word intelligence from WordMemoryState to drive
        # dynamic question selection (Phase 1 optimization).
        word_intel: dict[str, dict[str, int]] = {}

        def modes_for_word(word: str) -> list[str]:
            """P3 task ladder: recognition -> handwriting, rung chosen by mastery.

            Rung map (all are existing, frontend-supported task types):
              T1/T2 recognition : listen_choose_chinese, english_to_chinese
              T3-T5 production  : handwriting_dictation（手写听写，2026-08-02
                                  起取代全部键盘拼写类型）
            P1: intervention words (chronic failures) get recognition-first
            chains with a single handwritten attempt. Re-failing the same
            production test for the 100th time teaches nothing — the
            breakthrough path rebuilds the sound<->letter mapping with
            recognition and one handwritten attempt.

            视觉词快速通道（2026-08-03）：日常高频功能词不需要手写考。
            强度 ≥0.75 的视觉词直接从队列排除——它们在每句话里都会出现，
            孩子的掌握度已在语境中体现；较低强度的保持识别模式（只考意思
            不考写），写对就给长间隔。数据：the 近7天141条 review_log、
            i 80条（18次拼写全错），1-2字母词吃掉复习预算 13.7%。
            """
            word_lower = word.strip().lower()
            # 视觉词已掌握 → 从复习队列中排除，不占每日预算。
            if word_lower in SIGHT_WORDS:
                intel = word_intel.get(word, {})
                strength = intel.get("strength", 0)
                if strength >= 0.75:
                    return []  # 自动退休，不占预算
                # 强度不足的视觉词：只识别，不手写（写这些词毫无意义）
                status_value = intel.get("status", "")
                if status_value in ("mastered", "near_mastered") or strength >= 0.65:
                    return ["listen_choose_chinese"]
                return ["listen_choose_chinese", "english_to_chinese"]

            intel = word_intel.get(word, {})
            lapse = intel.get("lapse_count", 0)
            real_tests = intel.get("real_tests", 0)
            unknown_errs = intel.get("unknown_errors", 0)

            # 二期改造(2026-08-18): 分维回炉优先。某维度最近一次失败 →
            # 该维度的练习排到最前(只回炉这一维,其余维度进度保留)。
            # listen/meaning 用识别题低成本重建;spell 先确认意思再手写;
            # use 回到句中语境;speak 由跟读卡独立处理,不在此插队。
            dim_failed = intel.get("dim_last_failed", "")
            if dim_failed == "listen":
                return ["listen_choose_chinese", "english_to_chinese"]
            if dim_failed == "meaning":
                return ["english_to_chinese", "listen_choose_chinese", "handwriting_dictation"]
            if dim_failed == "spell":
                return ["english_to_chinese", "handwriting_dictation"]
            if dim_failed == "use":
                return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]

            # 改进2(2026-08-07 修订): lapse > 20 悠悠球词 -> 重教链。
            # 旧版纯识别(不考产出)让高 lapse 词永远毕不了业:识别太简单
            # 强度虚高(0.75-0.85),间隔拉长后产出能力其实没重建,到期再忘,
            # 陷入"学会→遗忘→重学→再忘"循环(feel lapse=88)。改为完整
            # 多模态重教: 两次识别热身 + 一次手写产出。配合 park_leech_words
            # (lapse>=30 到期即推 30 天),高频回笼与无效空考都被遏止。
            if lapse > 20:
                return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]

            if intel.get("intervention"):
                # 2026-08-11: 补 english_to_chinese——慢性失败词的意思重建
                # 不能只靠听音一种识别，且家长要求提高英选中频率。
                return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]
            # N1: new-word bootstrap. Words with few REAL tests get a fixed
            # recognition-first chain — one stage per queue fetch — instead of
            # being thrown straight into spelling production. Data behind
            # this: 165 of 217 recent new words had their FIRST real test be
            # a spelling failure (0% pass), and none ever saw a recognition
            # test first. Failing a new word on first contact is the most
            # demotivating possible introduction.
            if real_tests < len(N1_BOOTSTRAP_CHAINS):
                return N1_BOOTSTRAP_CHAINS[real_tests]

            # 改进3+4: 拼写失败率高或 unknown 多 -> 回识别 + 一次手写。
            # 改进3: 真测试正确率 <50%（lapse/real_tests>0.5 且 real_tests>=5）
            # 说明词没学会，反复考产出只是反复失败（真测试整体正确率仅36%）。
            # 改进4: unknown_errors>=3 完全不会拼（unknown 错误2062次最多）。
            fail_rate = lapse / max(real_tests, 1)
            if (fail_rate > 0.5 and real_tests >= 5) or unknown_errs >= 3:
                return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]

            status_value = intel.get("status", "")
            strength = intel.get("strength", 0)
            # T4-T5: near/mastery — quick meaning check, then production.
            # 2026-08-11: 补 english_to_chinese 作为产出前的意思确认（~20s，
            # 选对再写才有意义；选错说明"假掌握"，应尽早暴露）。家长反馈
            # 英选中频率太低。
            if status_value in ("mastered", "near_mastered") or strength >= 0.90:
                return ["english_to_chinese", "handwriting_dictation"]
            # T3-T4: consolidating — recognition warm-up, then production.
            # 2026-08-11: 识别热身从单一听选改为听选+英选中双识别。
            if status_value == "consolidating" or strength >= 0.6:
                return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]
            # T1-T3: teaching / difficult / unknown — recognition first.
            return ["listen_choose_chinese", "english_to_chinese", "handwriting_dictation"]

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
                            # N1: approximate REAL-test count from FSRS counters
                            # so established words without a WordMemoryState row
                            # don't fall into the new-word bootstrap chain.
                            "real_tests": (mem_state.repetition_count or 0) + (mem_state.lapse_count or 0),
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
                # P13: error-type-driven hints (ending / missing-letter / middle)
                intel["ending_errors"] = sum(error_count_value(v) for k, v in error_counts.items() if k == "ending")
                intel["missing_letter_errors"] = sum(error_count_value(v) for k, v in error_counts.items() if k == "missing-letter")
                intel["strength"] = max(intel.get("strength", 0), ws.memory_strength or 0)
                intel["status"] = ws.status or ""
                # 二期改造: 最近失败维度驱动回炉排序(弱维优先)
                intel["dim_last_failed"] = ws.dim_last_failed or ""
                # P1: chronic-failure detection — lapse-heavy words that are
                # still weak, or words the status engine already flags as
                # difficult, enter breakthrough mode (assisted forms only).
                intel["intervention"] = bool(
                    (intel.get("lapse_count", 0) >= 8 and intel["strength"] < 0.5)
                    or ws.status == "difficult"
                )

            # N1: count REAL tests per top word (drives the bootstrap chain).
            # Exact count from review_logs when the word-state links to a
            # word-level item; recall_correct_count as floor for rows whose
            # learning_item_id link is missing (they clearly passed bootstrap).
            intel_item_ids = [ws.learning_item_id for ws in word_state_rows if ws.learning_item_id is not None]
            real_counts_by_item: dict[UUID, int] = {}
            if intel_item_ids:
                real_counts_by_item = dict(
                    db.execute(
                        select(ReviewLog.learning_item_id, func.count(ReviewLog.id))
                        .where(
                            ReviewLog.user_id == current_user.id,
                            ReviewLog.learning_item_id.in_(intel_item_ids),
                            ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES)),
                        )
                        .group_by(ReviewLog.learning_item_id)
                    ).all()
                )
            for ws in word_state_rows:
                intel = word_intel[ws.word]
                intel["real_tests"] = max(
                    real_counts_by_item.get(ws.learning_item_id, 0),
                    ws.recall_correct_count or 0,
                )

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
            # Persist translation cache writes (same determinism fix as the
            # task-word path above)
            db.commit()
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
            # P18: don't re-serve a word that already hit today's attempt cap.
            if main_word in over_cap_words:
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
                if mode == "handwriting_dictation":
                    # P13: hint follows the child's dominant error type for
                    # this word. First-letter hint wins (hardest blocker),
                    # then ending anchor, then a plain letter count for
                    # missing/extra-letter strugglers. 手写卡片会把提示
                    # 显示在画板上方（2026-08-02 起手写取代键盘拼写）。
                    if first_letter_hint:
                        review_prompt = f"首字母:{first_letter_hint}"
                    elif intel.get("ending_errors", 0) >= 2 and wlen >= 3:
                        review_prompt = f"词尾:…{main_word[-2:]}"
                    elif intel.get("missing_letter_errors", 0) >= 2:
                        review_prompt = f"字母数:{wlen}"
                focus_item = LearningItemRead(
                    id=uuid4(),
                    source_item_id=word_item.id,
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
        # Note: served focus items are NOT pushed to tomorrow here. A
        # blanket push-on-serve wiped the queue on every refetch (page
        # refresh = everything due tomorrow = nothing left to review).
        # Rotation is handled per-word by /memory/focus-rotate when the
        # word's correct streak completes.
        # Sentences first (discover new weak words), then word-only
        # review (practice already-discovered words). Each word that
        # appears in the word review section is excluded from the
        # sentence section so the child doesn't see 'your' in 20
        # different sentences during word practice.
        # P4: sentence typing is the child's weakest mode (31% accuracy) —
        # small doses only, and never sentences containing an intervention
        # word: re-typing a chronic failure embedded in a sentence compounds
        # the frustration. Those words get recognition work in the word-only
        # section instead.
        intervention_words = {w for w, intel in word_intel.items() if intel.get("intervention")}
        sentence_candidates: list[LearningItemRead] = []
        for s in sentence_review_items:
            s_words = [w.strip().lower() for w in tokenize_words(s.english_text)]
            if not s_words or s_words[0] in seen_main_words:
                continue
            if intervention_words and any(w in intervention_words for w in s_words):
                continue
            # 手写整句限量且限长：超过 MAX_SENTENCE_WORDS 词的句子手写太
            # 慢（手写模式队列同款限制），复习环节只练短句。
            if s.item_type == "sentence" and len(s_words) > MAX_SENTENCE_WORDS:
                continue
            sentence_candidates.append(s)
            if len(sentence_candidates) >= 4:
                break
        sentences_for_session = sentence_candidates[:2]

        # 手写化（2026-08-02 家长决定）：复习队列的句子不再键盘打字/挖空
        # （整句敲键盘 ~80s/次、正确率 35%，是 72h 分析里最大的时间黑洞），
        # 统一改为手写听写：听发音 + 看中文 → 手写整句，AI 视觉判分。
        # 限量不变：每次会话 ≤2 句 + SENTENCE_DAILY_CAP=3（review_mode
        # 记为 sentence-handwriting，保持在 sentence-% 前缀下）。
        sentences_for_session = [
            s.model_copy(update={"review_task_type": HANDWRITING_DICTATION_TASK_TYPE})
            for s in sentences_for_session
        ]

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
        prefix_ids = {s.id for s in prefix_items}
        tail_items = [s for s in sentence_review_items
                      if s.id not in prefix_ids
                      and tokenize_words(s.english_text) and tokenize_words(s.english_text)[0].strip().lower() not in prefix_word_set]
        # Active voice practice (voice_practice task type): sprinkle read-aloud
        # tasks into the review queue so the child is actively pushed to speak
        # on familiar content during normal review. One voice task every ~6
        # regular tasks keeps the queue mostly spelling/choice but introduces
        # voice work organically. Items are picked from already-studied
        # (repetition_count >= 3) word/sentence items - voice work supplements
        # the existing ladder rather than replacing it.
        voice_items = _build_voice_practice_items(db, current_user.id, prefix_items)
        if voice_items:
            prefix_items = _interleave_voice(prefix_items, voice_items, step=6)
        review_items = task_review_items + prefix_items + tail_items
        # Clamp to capped_limit
        return review_items[:capped_limit] if len(review_items) > capped_limit else review_items

    # NOTE: the former `if interleave and task_review_items and
    # sentence_review_items:` branch was unreachable dead code (the
    # `if sentence_review_items:` block above always returns) and has been
    # removed (2026-08-09). All frontend callers pass interleave=false.

    review_items: list[LearningItemRead] = task_review_items[:]
    for item_read in sentence_review_items:
        if len(review_items) >= capped_limit:
            break
        review_items.append(item_read)

    # Active voice practice for non-focus-mode paths too.
    voice_items = _build_voice_practice_items(db, current_user.id, review_items)
    if voice_items:
        review_items = _interleave_voice(review_items, voice_items, step=6)

    # Phonics mode: regroup items by sound family so the child
    # practices related words together (e.g., light/night/right
    # from the -ight family). This teaches the PATTERN rather than
    # isolated word memorization, directly addressing the 21.5%
    # first-letter and 19.1% missing-letter error rates.
    if phonics and review_items:
        # Step 1: assign each item to its phonics family
        family_items: dict[str, list[LearningItemRead]] = {}
        ungrouped: list[LearningItemRead] = []
        for item in review_items:
            eng = (item.english_text or "").strip().lower()
            if not eng:
                ungrouped.append(item)
                continue
            main_word = tokenize_words(eng)[0] if tokenize_words(eng) else ""
            if not main_word:
                ungrouped.append(item)
                continue
            family = _get_phonics_group(main_word)
            if family:
                family_items.setdefault(family, []).append(item)
            else:
                ungrouped.append(item)
        # Step 2: interleave families (2 words per family, then switch)
        # so the child sees the pattern clearly without getting bored
        rebuilt: list[LearningItemRead] = []
        family_keys = sorted(family_items.keys())
        max_fam = max((len(v) for v in family_items.values()), default=0)
        for i in range(max_fam):
            for key in family_keys:
                items = family_items[key]
                if i < len(items):
                    item = items[i]
                    # Tag the first item in each family group
                    if i == 0:
                        item = item.model_copy(update={"source": f"phonics:{key}" + (f" {item.source}" if item.source else "")})
                    rebuilt.append(item)
        # Tail: ungrouped words after all families
        review_items = rebuilt + ungrouped

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

    # Exact case-insensitive match (ilike would treat % and _ in the
    # payload as wildcards and produce false duplicates)
    existing_item = db.scalar(
        select(LearningItem).where(
            LearningItem.user_id == current_user.id,
            LearningItem.course_id == payload.course_id,
            LearningItem.item_type == payload.item_type,
            func.lower(LearningItem.english_text) == payload.english_text.strip().lower(),
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

    # 2026-08-18 perf: the old target_ready re-scanned speech_targets AND
    # re-read the audio file for EVERY term of EVERY item — O(items × terms
    # × targets) Python loops plus whole-file reads per probe (a 500-item
    # course pulled hundreds of MB off disk per status fetch). Precompute
    # lookup maps once; each probe is then O(1) with at most one stat()
    # per unique target.
    target_by_text: dict[tuple[str, str], SpeechTarget] = {}
    for target in speech_targets:
        text_key = target.text.strip()
        if text_key:
            target_by_text.setdefault((target.language, text_key), target)

    file_ready_by_key: dict[tuple[str, str, int, str], bool] = {}

    def target_ready(text: str, language: str) -> bool:
        normalized_text = text.strip()
        if not normalized_text:
            return False
        target = target_by_text.get((language, normalized_text))
        if target is None:
            return False
        key = (target.language, target.voice, target.speech_rate, build_cache_key(target.text.strip(), target.voice, target.speech_rate))
        if key not in cached_asset_keys:
            return False
        ready = file_ready_by_key.get(key)
        if ready is None:
            ready = is_audio_cached(target.text.strip(), target.voice, target.speech_rate)
            file_ready_by_key[key] = ready
        return ready

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
                    if item.item_type == "word":
                        # Word items get 1-3 common meanings from the LLM (multi-meaning learning).
                        item.chinese_text = sanitize_word_translation(
                            translate_english_to_chinese(item.english_text, translation_settings, multiple_meanings=True),
                            source_word=item.english_text,
                        )
                        if not item.chinese_text:
                            raise ValueError("empty after sanitize")
                    else:
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
            if item.item_type == "word":
                # Word items get 1-3 common meanings from the LLM (multi-meaning learning).
                item.chinese_text = sanitize_word_translation(
                    translate_english_to_chinese(item.english_text, translation_settings, multiple_meanings=True),
                    source_word=item.english_text,
                ) or item.chinese_text
            else:
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


@router.post("/pronunciation-check", response_model=PronunciationCheckResponse)
async def check_pronunciation(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    expected_text: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> PronunciationCheckResponse:
    """Transcribe the child's read-aloud clip and leniently score it.

    The frontend records right after the echo prompt's model TTS finishes,
    converts to 16 kHz mono WAV, and uploads here. `heard_speech=false`
    means silence/noise — the child gets re-prompted without it counting
    as a failed attempt.
    """
    expected = expected_text.strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expected_text is required")
    if len(expected) > MAX_PRONUNCIATION_TEXT_CHARS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expected_text is too long")

    audio = await file.read(MAX_PRONUNCIATION_AUDIO_BYTES + 1)
    if not audio:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio file is empty")
    if len(audio) > MAX_PRONUNCIATION_AUDIO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="audio file is too large")

    stored_settings = get_private_model_settings(db, current_user.id)
    api_key = string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key
    if not api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="speech recognition is not configured")

    try:
        transcript = await run_in_threadpool(recognize_speech_flash, audio, api_key=api_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    result = score_pronunciation(expected, transcript)
    if not result.heard_speech:
        # 观测"总是识别不出来"：记录 no-speech 频次（不说话不计失败，但
        # 高频出现说明采集链有问题——增益/VAD/设备）。
        logger.info("pronunciation-check no-speech: expected=%.40s", expected)
    return PronunciationCheckResponse(
        transcript=transcript,
        score=round(result.score, 3),
        passed=result.passed,
        heard_speech=result.heard_speech,
    )


@router.get("/speak-items", response_model=list[LearningItemRead])
def list_speak_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 20,
) -> list[LearningItemRead]:
    """Familiar sentences/phrases for the dedicated read-aloud speaking mode.

    Studied content only (MemoryState.repetition_count > 0): speaking practice
    rehearses what the child already knows — it must never ambush them with
    brand-new words. Items already spoken today are excluded and a small
    daily cap keeps the mode a light complement to review, not a second job.
    Each item is served with review_task_type="read_aloud" so the frontend
    runs the whole exercise on the pronunciation-gated echo card.
    """
    capped_limit = max(1, min(limit, 50))
    now = datetime.now(UTC)
    today_start = now.astimezone(LOCAL_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)

    spoken_today_ids = {
        row_id
        for row_id in db.scalars(
            select(LearningEvent.learning_item_id).where(
                LearningEvent.user_id == current_user.id,
                LearningEvent.review_mode == READ_ALOUD_REVIEW_MODE,
                LearningEvent.occurred_at >= today_start,
                LearningEvent.learning_item_id.is_not(None),
            )
        ).all()
        if row_id is not None
    }
    daily_remaining = max(SPEAK_DAILY_CAP - len(spoken_today_ids), 0)
    if daily_remaining == 0:
        return []

    last_spoken = (
        select(
            LearningEvent.learning_item_id.label("item_id"),
            func.max(LearningEvent.occurred_at).label("last_spoken_at"),
        )
        .where(
            LearningEvent.user_id == current_user.id,
            LearningEvent.review_mode == READ_ALOUD_REVIEW_MODE,
        )
        .group_by(LearningEvent.learning_item_id)
        .subquery()
    )
    statement = (
        select(LearningItem, MemoryState.repetition_count, last_spoken.c.last_spoken_at)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .outerjoin(last_spoken, last_spoken.c.item_id == LearningItem.id)
        .where(
            LearningItem.user_id == current_user.id,
            LearningItem.item_type.in_(["sentence", "phrase"]),
            MemoryState.repetition_count > 0,
        )
    )
    rows = [(item, reps, spoken_at) for item, reps, spoken_at in db.execute(statement).all()]
    selected = select_speak_candidates(
        rows,
        spoken_today_ids=spoken_today_ids,
        limit=min(capped_limit, daily_remaining),
    )
    return [
        LearningItemRead.model_validate(item).model_copy(update={"review_task_type": READ_ALOUD_TASK_TYPE})
        for item in selected
    ]


@router.post("/read-aloud-events", response_model=ReadAloudEventResponse, status_code=status.HTTP_201_CREATED)
def create_read_aloud_event(
    payload: ReadAloudEventRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReadAloudEventResponse:
    """Record a finished read-aloud exercise (pass or 5-attempt giveup).

    Telemetry only — same rule as the assisted review modes: a LearningEvent
    for the replay timeline, NO review_log, NO FSRS mutation, NO accuracy
    contribution. A pass scores 3; a giveup scores 1 and stays
    is_correct=False so the timeline is honest about which sentences are
    still hard to say.

    `source` splits the timeline mode: "speak-mode" events keep
    review_mode="read-aloud" (the dedicated 语音练习 queue — the speak-item
    candidate selection excludes items already spoken today based on it),
    while "exercise-echo" events (the read-aloud gate after any ordinary
    exercise) use "echo-read" so they never shrink the speak queue. The
    dashboard's 今日/每周朗读次数 count both.
    """
    learning_item: LearningItem | None = None
    if payload.learning_item_id is not None:
        learning_item = db.scalar(
            select(LearningItem).where(
                LearningItem.id == payload.learning_item_id,
                LearningItem.user_id == current_user.id,
            )
        )
    if learning_item is None:
        # Defensive fallback (mirrors /word-reviews): resolve by text so the
        # event is never silently lost.
        english_text = payload.english_text.strip()
        if english_text:
            learning_item = db.scalar(
                select(LearningItem).where(
                    LearningItem.user_id == current_user.id,
                    LearningItem.english_text == english_text,
                ).limit(1)
            )
    try:
        from app.services.learning_replay import record_assisted_learning_event
        # Savepoint containment (2026-08-16): a failed flush inside the
        # recorder would otherwise abort the outer transaction and the
        # db.commit() below would raise PendingRollbackError → 500 (same
        # 2026-07-30 incident class the other call sites guard against).
        with db.begin_nested():
            record_assisted_learning_event(
                db,
                current_user.id,
                learning_item,
                READ_ALOUD_REVIEW_MODE if payload.source == "speak-mode" else ECHO_READ_REVIEW_MODE,
                3 if payload.passed else 1,
                response_text=(payload.transcript or "").strip() or None,
                duration_ms=min(int(payload.duration_seconds * 1000), 5 * 60 * 1000) or 10_000,
                is_correct=payload.passed,
                fallback_english_text=payload.english_text,
            )
    except Exception as exc:
        logger.warning("Failed to record read-aloud learning event: %s", exc)
    db.commit()
    return ReadAloudEventResponse(learning_item_id=(learning_item.id if learning_item else None))


@router.get("/handwriting-items", response_model=list[LearningItemRead])
def list_handwriting_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 16,
) -> list[LearningItemRead]:
    """Today's handwriting-dictation queue (手写听写 mode).

    家长指定的取材顺序：先是单词复习内容——与"单词复习"模式同源的到期
    复习词（next_review_at 到期，最弱优先，听写/翻译交替），不足 12 个
    时用学过的最弱词补齐；再按课次出中考英语 第1课→第10课 的句子
    （学习内容，不要求学过，全部听写）。每个条目每天最多一次，
    日上限 = 12 词 + 4 句。
    """
    capped_limit = max(1, min(limit, 30))
    now = datetime.now(UTC)
    today_start = now.astimezone(LOCAL_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    # 2026-08-07 复核:手写入口此前不做任何 park,到期漏词(lapse>=30)在此
    # 以最高优先级听写、提交后次日又到期——月频熔断在此入口变日频死循环。
    park_leech_words(db, current_user.id, now)
    # 2026-08-04 fix: the "tested today" exclusion was doubly broken —
    # (a) it filtered only dictation/translation modes, missing
    #     sentence-handwriting and handwriting-both events;
    # (b) handwriting reviews are booked on the word-memory TWIN item, so
    #     the served course item id never matched tested_today_ids and the
    #     same sentences/words were re-served all day (cap also undercounted).
    # Fix: exclude by normalized TEXT as well as id, count distinct items.
    _handwriting_modes_all = (
        *HANDWRITING_REVIEW_MODES,
        SENTENCE_HANDWRITING_REVIEW_MODE,
        HANDWRITING_BOTH_REVIEW_MODE,
    )
    tested_rows = db.execute(
        select(LearningEvent.learning_item_id, LearningItem.english_text)
        .outerjoin(LearningItem, LearningItem.id == LearningEvent.learning_item_id)
        .where(
            LearningEvent.user_id == current_user.id,
            LearningEvent.review_mode.in_(_handwriting_modes_all),
            LearningEvent.occurred_at >= today_start,
        )
    ).all()
    tested_today_ids = {row_id for row_id, _text in tested_rows if row_id is not None}
    tested_today_words = {text.strip().lower() for _id, text in tested_rows if text}
    tested_today_keys = {
        text.strip().lower() if text else str(row_id)
        for row_id, text in tested_rows
        if row_id is not None or text
    }
    daily_remaining = max(HANDWRITING_DAILY_CAP - len(tested_today_keys), 0)
    if daily_remaining == 0:
        return []

    last_tested = (
        select(
            LearningEvent.learning_item_id.label("item_id"),
            func.max(LearningEvent.occurred_at).label("last_tested_at"),
        )
        .where(
            LearningEvent.user_id == current_user.id,
            LearningEvent.review_mode.in_(_handwriting_modes_all),
        )
        .group_by(LearningEvent.learning_item_id)
        .subquery()
    )
    # 第一部分：单词复习（到期复习词优先 + 学过的最弱词补齐）
    word_statement = (
        select(LearningItem, MemoryState.memory_strength, last_tested.c.last_tested_at, MemoryState.next_review_at, MemoryState.lapse_count)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .outerjoin(last_tested, last_tested.c.item_id == LearningItem.id)
        .where(
            LearningItem.user_id == current_user.id,
            LearningItem.item_type == "word",
            MemoryState.repetition_count > 0,
        )
    )
    # 漏词(lapse>=30)不进手写队列——到期档会被熔断推走,"最弱补齐"档
    # 也不许把它们捞回来,与复习队列的软退役口径保持一致。
    word_rows = [
        (item, strength, tested_at, next_review_at is not None and next_review_at <= now)
        for item, strength, tested_at, next_review_at, lapse_count in db.execute(word_statement).all()
        if not is_leech_word(lapse_count)
    ]

    # 第二部分：中考英语 第1课→第10课 的句子（学习内容，不要求学过）。
    # 按 (课次数字, sort_order) 排序后交给 compose 顺序挑选。
    course_items: list[LearningItem] = []
    package_id = db.scalar(
        select(CoursePackage.id).where(
            CoursePackage.user_id == current_user.id,
            CoursePackage.name.in_(HANDWRITING_COURSE_PACKAGE_NAMES),
        )
    )
    if package_id is not None:
        course_rows = db.execute(
            select(LearningItem, Course.name)
            .join(Course, Course.id == LearningItem.course_id)
            .where(
                LearningItem.user_id == current_user.id,
                Course.package_id == package_id,
                LearningItem.item_type.in_(["sentence", "phrase"]),
            )
        ).all()
        course_items = [
            item
            for item, _course_name in sorted(
                course_rows,
                key=lambda row: (
                    parse_lesson_number(row[1]),
                    row[0].sort_order,
                    str(row[0].id),
                ),
            )
        ]

    selected = compose_daily_handwriting_queue(
        word_rows,
        course_items,
        tested_today_ids=tested_today_ids,
        tested_today_words=tested_today_words,
        limit=min(capped_limit, daily_remaining),
    )
    return [
        LearningItemRead.model_validate(item).model_copy(update={"review_task_type": task_type})
        for item, task_type in selected
    ]


@router.get("/daily-test-items", response_model=list[LearningItemRead])
def list_daily_test_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DAILY_TEST_WORD_LIMIT,
) -> list[LearningItemRead]:
    """每日一测（2026-08-11 三关重构）：每天测 20 个词，每词连续三关——
    ① 听音选中文（发音→意思）② 看英文选中文（形→意思）③ 手写英文（拼写）。
    三关全过 = 今日真正掌握。旧版要求手写中文意思（handwriting_both），
    中文写错会拖垮整个词的判定（生产通过率仅 6%）；中文意思改为选择判定，
    检查意图不变、判定可靠。

    选词优先级：今日所学（当天任何模式复习过的词，最弱在前）→ 到期复习词
    → 学过的最弱词补齐。今日已测过的词不重出（重测只出剩余）。
    """
    capped_limit = max(1, min(limit, 30))
    now = datetime.now(UTC)
    today_start = now.astimezone(LOCAL_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    # 2026-08-07 复核:每日一测此前不做任何 park,漏词会经到期档/最弱补齐
    # 档每天进测验,与复习队列的熔断口径矛盾。
    park_leech_words(db, current_user.id, now)

    # 今日已测：三关提交统一打 context="daily-test" 标记（选择题/手写与
    # 日常复习共用 review_mode，只能靠 context 区分）；旧版 handwriting-both
    # 事件一并收集，保证改版当天不重复出词。id 与归一化词文本双重排除。
    tested_rows = db.execute(
        select(LearningEvent.learning_item_id, LearningItem.english_text)
        .outerjoin(LearningItem, LearningItem.id == LearningEvent.learning_item_id)
        .where(
            LearningEvent.user_id == current_user.id,
            LearningEvent.review_mode == HANDWRITING_BOTH_REVIEW_MODE,
            LearningEvent.occurred_at >= today_start,
        )
    ).all()
    tested_context_rows = db.execute(
        select(ReviewLog.learning_item_id, LearningItem.english_text)
        .outerjoin(LearningItem, LearningItem.id == ReviewLog.learning_item_id)
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.context == DAILY_TEST_CONTEXT,
            ReviewLog.reviewed_at >= today_start,
        )
    ).all()
    tested_today_ids = {row_id for row_id, _text in tested_rows + tested_context_rows if row_id is not None}
    tested_today_words = {
        (text or "").strip().lower() for _row_id, text in tested_rows + tested_context_rows if (text or "").strip()
    }

    def _testable(item: LearningItem) -> bool:
        # 必须有有效中文释义——每日一测要考"写出中文意思"。
        ch = item.chinese_text or ""
        return (
            item.item_type == "word"
            and bool(ch.strip())
            and any("一" <= c <= "鿿" for c in ch)
            and ch.strip().lower() != (item.english_text or "").strip().lower()
        )

    # 今日所学：当天任何模式有真复习记录的单词条目。最弱的排最前——
    # 测的就是不牢的；并列时最近学过的在前（刚学完就测，反馈最及时）。
    today_logs = (
        select(
            ReviewLog.learning_item_id.label("item_id"),
            func.max(ReviewLog.reviewed_at).label("last_at"),
        )
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.learning_item_id.is_not(None),
        )
        .group_by(ReviewLog.learning_item_id)
        .subquery()
    )
    today_rows = [
        item
        for item, _last_at, _strength in sorted(
            db.execute(
                select(LearningItem, today_logs.c.last_at, MemoryState.memory_strength)
                .join(today_logs, today_logs.c.item_id == LearningItem.id)
                .outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id)
                .where(
                    LearningItem.user_id == current_user.id,
                    LearningItem.item_type == "word",
                )
            ).all(),
            key=lambda row: (
                row[2] if row[2] is not None else 1.0,
                -(row[1].timestamp() if row[1] else 0.0),
            ),
        )
        if _testable(item)
    ]

    studied_rows = db.execute(
        select(LearningItem, MemoryState.memory_strength, MemoryState.next_review_at, MemoryState.lapse_count)
        .join(MemoryState, MemoryState.learning_item_id == LearningItem.id)
        .where(
            LearningItem.user_id == current_user.id,
            LearningItem.item_type == "word",
            MemoryState.repetition_count > 0,
        )
    ).all()
    strength_by_id = {item.id: (strength or 0.0) for item, strength, _nra, _lapse in studied_rows}
    # 视觉词（the, I, is, are...）不出每日一测——这些功能词太基础，
    # 写错只是笔迹问题不反映词汇掌握，不值得占 20 个测验位。
    _SIGHT_TEST_THRESHOLD = 0.70
    def _not_retired_sight(item: LearningItem) -> bool:
        word = (item.english_text or "").strip().lower()
        if word not in SIGHT_WORDS:
            return True
        return strength_by_id.get(item.id, 0.0) < _SIGHT_TEST_THRESHOLD

    # 漏词(lapse>=30)不进每日一测的到期档和最弱补齐档——与复习队列的
    # 软退役口径一致(今日所学 today_rows 不受影响:当天学当天测是正常反馈)。
    due_rows = sorted(
        (item for item, _s, nra, lapse in studied_rows if nra is not None and nra <= now and not is_leech_word(lapse) and _testable(item) and _not_retired_sight(item)),
        key=lambda item: strength_by_id.get(item.id, 0.0),
    )
    weak_rows = sorted(
        (item for item, _s, _nra, lapse in studied_rows if not is_leech_word(lapse) and _testable(item) and _not_retired_sight(item)),
        key=lambda item: strength_by_id.get(item.id, 0.0),
    )
    # today_rows 也过滤——今天刚学过的 sight word 强度已够就不测。
    today_rows = [item for item in today_rows if _not_retired_sight(item)]

    picked = pick_daily_test_words(
        today_rows,
        due_rows,
        weak_rows,
        tested_today_ids=tested_today_ids,
        tested_today_words=tested_today_words,
        limit=capped_limit,
    )

    # 每词展开为连续三关。选择关的 6 个选项来自用户自己的词库
    # （与复习队列同一套 _enrich_choices_for_word）；手写关复用复习队列的
    # handwriting_dictation 卡。三关共用同一 source_item_id（真实课程词条），
    # 前端按它把三关聚合成成绩单的一行。
    stored_settings = get_private_model_settings(db, current_user.id)
    choice_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    test_items: list[LearningItemRead] = []
    for item in picked:
        normalized = normalize_word(item.english_text or "")
        choices, correct_answer = _enrich_choices_for_word(db, current_user.id, normalized, item, choice_settings)
        if not choices or not correct_answer:
            continue
        for gate_index, (gate_type, gate_prompt) in enumerate(DAILY_TEST_GATES):
            test_items.append(
                LearningItemRead(
                    id=uuid4(),
                    source_item_id=item.id,
                    user_id=current_user.id,
                    course_id=item.course_id,
                    item_type="word",
                    english_text=item.english_text,
                    chinese_text=item.chinese_text,
                    phonetic=item.phonetic,
                    syllables=item.syllables,
                    grapheme_phoneme_map=item.grapheme_phoneme_map,
                    difficulty_level=item.difficulty_level,
                    source=f"每日一测·第{gate_index + 1}关",
                    review_task_type=gate_type,
                    review_prompt=gate_prompt.format(word=normalized) if gate_prompt else None,
                    review_choices=choices if gate_type != "handwriting_dictation" else [],
                    review_answer=correct_answer if gate_type != "handwriting_dictation" else None,
                    focus_words=[normalized],
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
    return test_items


# —— 今日学习流程（daily flow）内容端点 ——
# 2026-08-11 流程重构：复习30词 → 新词20个 → 句子30句 → 每日一测，全部计数制。
# 新词与句子都来自「中考英语」课程包：新词按包内句子首次出现顺序收割（无记忆
# 状态的真新词优先，不足时用最弱的在学词补齐）；句子按课序取未练过的。完成的
# 内容当天即带记忆状态，第二天重进自然得到下一批任务——"按算法重新安排"。
ZHONGKAO_PACKAGE_NAME = "中考英语"
# 一期改造(2026-08-18): 每日新词 20 → 8。7-10 岁儿童每天能稳定留存的
# 新词约 5-8 个;20 个/天制造 140+ 个"半生不熟"的在途词,复习队列永远
# 还不完债——"花大量时间却掌握很少"的结构性根因。
DAILY_FLOW_NEW_WORD_LIMIT = 8
DAILY_FLOW_SENTENCE_LIMIT = 30
# 在途词(学过但未毕业)硬上限:到顶即停喂新词,直到旧词毕业腾出位置。
# 把"还不完的无形债务"变成"看得见的有界队列"。
IN_FLIGHT_WORD_CAP = 60


def _resolve_daily_flow_package(db: Session, user_id: UUID) -> CoursePackage | None:
    """Locate the user's 中考英语 course package (exact name, then any 中考 package)."""
    packages = db.scalars(
        select(CoursePackage).where(CoursePackage.user_id == user_id)
    ).all()
    for package in packages:
        if (package.name or "") == ZHONGKAO_PACKAGE_NAME:
            return package
    for package in packages:
        if "中考" in (package.name or ""):
            return package
    return None


def _daily_flow_package_sentences(db: Session, user_id: UUID, package_id: UUID) -> list[LearningItem]:
    """All sentence items of the package, flattened in course order (第N课 numeric),
    then in-item sort order."""
    courses = db.scalars(
        select(Course).where(Course.user_id == user_id, Course.package_id == package_id)
    ).all()

    def course_order_key(course: Course) -> tuple[int, datetime]:
        digits = "".join(character for character in (course.name or "") if character.isdigit())
        return (int(digits) if digits else 1_000_000, course.created_at)

    ordered_courses = sorted(courses, key=course_order_key)
    course_ids = [course.id for course in ordered_courses]
    if not course_ids:
        return []
    rows = db.scalars(
        select(LearningItem)
        .where(
            LearningItem.user_id == user_id,
            LearningItem.course_id.in_(course_ids),
            LearningItem.item_type == "sentence",
        )
        .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
    ).all()
    by_course: dict[UUID, list[LearningItem]] = {course_id: [] for course_id in course_ids}
    for row in rows:
        if row.course_id in by_course:
            by_course[row.course_id].append(row)
    return [row for course_id in course_ids for row in by_course[course_id]]


@router.get("/daily-flow/new-words", response_model=list[LearningItemRead])
def list_daily_flow_new_words(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DAILY_FLOW_NEW_WORD_LIMIT,
) -> list[LearningItemRead]:
    """今日流程·新单词阶段：从「中考英语」课程包的句子里收割新词。
    每词连续四关，按听说读写顺序：听音选中文 → 跟读 → 看词选中文 → 手写。
    真新词（无记忆状态）按包内首次出现顺序优先；不足时用包内最弱的在学词
    补齐；全部掌握后返回更少甚至空队列（前端对空队列自动跳过该阶段）。
    """
    capped_limit = max(1, min(limit, 40))
    # 一期改造(2026-08-18): 在途词硬上限。学过但未毕业(teaching /
    # consolidating / difficult)的词达到 60 个时不再喂新词——先把存量
    # 推到毕业,否则新词只会制造明天的失败。返回空队列,前端自动跳过
    # 该阶段。
    in_flight_count = int(db.scalar(
        select(func.count(WordMemoryState.id)).where(
            WordMemoryState.user_id == current_user.id,
            WordMemoryState.status.in_(("teaching", "consolidating", "difficult")),
        )
    ) or 0)
    if in_flight_count >= IN_FLIGHT_WORD_CAP:
        return []
    package = _resolve_daily_flow_package(db, current_user.id)
    if package is None:
        return []
    sentences = _daily_flow_package_sentences(db, current_user.id, package.id)
    candidate_words: list[str] = []
    seen_words: set[str] = set()
    for sentence_item in sentences:
        for raw_word in tokenize_words(sentence_item.english_text or ""):
            word = normalize_word(raw_word)
            if not word or len(word) < 2 or word in seen_words or word in SIGHT_WORDS:
                continue
            seen_words.add(word)
            candidate_words.append(word)
    if not candidate_words:
        return []
    word_states = db.scalars(
        select(WordMemoryState).where(
            WordMemoryState.user_id == current_user.id,
            WordMemoryState.word.in_(candidate_words),
        )
    ).all()
    state_by_word = {state.word: state for state in word_states}
    fresh_words = [word for word in candidate_words if word not in state_by_word]
    picked = fresh_words[:capped_limit]
    if len(picked) < capped_limit:
        # 新词不足 → 包内最弱的在学词补齐（已掌握/近掌握的不碰，它们留给复习）。
        weak_states = sorted(
            (
                state
                for state in word_states
                if state.word not in picked and (state.status or "") in {"teaching", "difficult", "consolidating"}
            ),
            key=lambda state: state.memory_strength or 0.0,
        )
        for state in weak_states:
            picked.append(state.word)
            if len(picked) >= capped_limit:
                break
    if not picked:
        return []
    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    translations = ensure_word_translations(db, current_user.id, picked, translation_settings)
    # 2026-08-16: persist the translation cache writes. This GET endpoint has
    # no other commit, so the WordTranslation rows ensure_word_translations
    # flushed rolled back at request end — every page fetch re-billed the LLM
    # for the same words (same fix as the task-word ~1287 and focus-word
    # ~1618 paths, which commit explicitly).
    db.commit()
    now = datetime.now(UTC)
    items: list[LearningItemRead] = []
    for word in picked:
        # Transient probe item: _enrich_choices_for_word only reads
        # english/chinese text off it — nothing is persisted here.
        probe = LearningItem(
            user_id=current_user.id,
            course_id=None,
            item_type="word",
            english_text=word,
            chinese_text=translations.get(word, "") or "",
        )
        choices, correct_answer = _enrich_choices_for_word(db, current_user.id, word, probe, translation_settings)
        if not choices or not correct_answer:
            continue
        # 听说读写四关：听音选中文（听）→ 跟读（说）→ 看词选中文（读）→ 手写（写）。
        gates: list[tuple[str, str | None, list[str], str | None]] = [
            ("listen_choose_chinese", "听英文发音，选择正确的中文意思", choices, correct_answer),
            ("voice_practice", "🎤 跟我读一遍", [], word),
            ("english_to_chinese", f"选择 {word} 的中文意思", choices, correct_answer),
            ("handwriting_dictation", None, [], None),
        ]
        for gate_index, (gate_type, gate_prompt, gate_choices, gate_answer) in enumerate(gates):
            items.append(
                LearningItemRead(
                    id=uuid4(),
                    source_item_id=None,
                    user_id=current_user.id,
                    course_id=None,
                    item_type="word",
                    english_text=word,
                    chinese_text=correct_answer,
                    difficulty_level=3,
                    source=f"每日流程·新词·第{gate_index + 1}关",
                    review_task_type=gate_type,
                    review_prompt=gate_prompt,
                    review_choices=gate_choices,
                    review_answer=gate_answer,
                    focus_words=[word],
                    created_at=now,
                    updated_at=now,
                )
            )
    return items


@router.get("/daily-flow/sentences", response_model=list[LearningItemRead])
def list_daily_flow_sentences(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DAILY_FLOW_SENTENCE_LIMIT,
) -> list[LearningItemRead]:
    """今日流程·句子阶段：「中考英语」课程包内未练过的 30 个句子。
    按课序/句序排列；练过（repetition_count > 0）的句子不再出现。每个句子带
    focus_words（弱词预热 + 完形填空），与课程学习同一套听说读写体验：
    听句子发音 → 弱词跟读 → 看句理解 → 完形拼写。
    """
    capped_limit = max(1, min(limit, 60))
    package = _resolve_daily_flow_package(db, current_user.id)
    if package is None:
        return []
    sentences = _daily_flow_package_sentences(db, current_user.id, package.id)
    if not sentences:
        return []
    sentence_ids = [item.id for item in sentences]
    memory_states = db.scalars(
        select(MemoryState).where(MemoryState.learning_item_id.in_(sentence_ids))
    ).all()
    repetitions_by_id = {state.learning_item_id: state.repetition_count or 0 for state in memory_states}
    fresh_sentences = [item for item in sentences if repetitions_by_id.get(item.id, 0) <= 0][:capped_limit]
    if not fresh_sentences:
        return []
    # 弱词标记（与 /items 的 N2/N4 同一规则）：无记忆状态或在学/困难的非视觉词
    # 进 focus_words —— 前端据此先预热跟读、再只对弱词做完形填空。
    sentence_words: set[str] = set()
    for item in fresh_sentences:
        sentence_words.update(
            word for word in (normalize_word(raw) for raw in tokenize_words(item.english_text or "")) if word
        )
    weak_status_by_word: dict[str, tuple[float, str]] = {}
    if sentence_words:
        word_states = db.scalars(
            select(WordMemoryState).where(
                WordMemoryState.user_id == current_user.id,
                WordMemoryState.word.in_(list(sentence_words)),
            )
        ).all()
        status_by_word = {state.word: (state.memory_strength or 0.0, state.status or "") for state in word_states}
        weak_status_by_word = {
            word: value
            for word, value in ((word, status_by_word.get(word, (0.0, ""))) for word in sentence_words)
            if value[1] in ("teaching", "difficult", "") and word not in SIGHT_WORDS
        }
    result: list[LearningItemRead] = []
    for item in fresh_sentences:
        weak = [
            word
            for word in (normalize_word(raw) for raw in tokenize_words(item.english_text or ""))
            if word and word in weak_status_by_word
        ]
        if weak:
            weak.sort(key=lambda word: weak_status_by_word[word][0])
            result.append(
                LearningItemRead.model_validate(item).model_copy(
                    update={"focus_words": weak[:2], "review_task_type": "cloze_sentence"}
                )
            )
        else:
            result.append(LearningItemRead.model_validate(item))
    return result




# ── 三期改造(2026-08-18): 每日流程新阶段 ─────────────────────────────
# 毕业冲刺: 五维中四维已毕业、只差一维今天验证的词——一张对准缺维的卡,
# 通过即五维毕业(判 mastered)。每天最先做,孩子一开场就能"收割"。
DAILY_FLOW_SPRINT_LIMIT = 10
# 昨日回炉: 昨天真测试失败的词,先识别重建(低成本热身)再考弱维。
DAILY_FLOW_RETEACH_LIMIT = 8

_DIM_CARD_TYPE = {
    "listen": "listen_choose_chinese",
    "meaning": "english_to_chinese",
    "speak": "voice_practice",
    "spell": "handwriting_dictation",
}
_DIM_LABEL = {"listen": "听音", "meaning": "释义", "speak": "跟读", "spell": "拼写", "use": "用词"}


def _missing_dimensions(word_state: WordMemoryState, today_local) -> list[str]:
    """今天仍缺验证的维度(已毕业或今天已过的维度不在其列)。"""
    missing: list[str] = []
    if (word_state.dim_listen_days or 0) < DIM_GRADUATION_DAYS and word_state.dim_listen_last_date != today_local:
        missing.append("listen")
    if (word_state.dim_meaning_days or 0) < DIM_GRADUATION_DAYS and word_state.dim_meaning_last_date != today_local:
        missing.append("meaning")
    if not word_state.dim_speak_passed:
        missing.append("speak")
    if (word_state.dim_spell_days or 0) < DIM_GRADUATION_DAYS and word_state.dim_spell_last_date != today_local:
        missing.append("spell")
    if (word_state.dim_use_days or 0) < DIM_GRADUATION_DAYS and word_state.dim_use_last_date != today_local:
        missing.append("use")
    return missing


@router.get("/daily-flow/graduation-sprint", response_model=list[LearningItemRead])
def list_daily_flow_graduation_sprint(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DAILY_FLOW_SPRINT_LIMIT,
) -> list[LearningItemRead]:
    """毕业冲刺: 只差最后一维的词,每词一张对准缺维的卡(通过即毕业)。"""
    capped_limit = max(1, min(limit, 20))
    now = datetime.now(UTC)
    today_local = now.astimezone(LOCAL_TIMEZONE).date()
    word_states = db.scalars(
        select(WordMemoryState).where(WordMemoryState.user_id == current_user.id)
    ).all()
    sprint_words: list[tuple[WordMemoryState, str]] = []
    for ws in word_states:
        if (ws.status or "") in ("mastered", "near_mastered"):
            continue
        if not ws.word or ws.word in SIGHT_WORDS:
            continue
        if (ws.memory_strength or 0) < 0.3:
            continue  # 太弱的词先走常规教学链,冲刺是给"临门一脚"的词
        missing = _missing_dimensions(ws, today_local)
        if len(missing) == 1:
            sprint_words.append((ws, missing[0]))
    if not sprint_words:
        return []
    # 最弱的排最前(冲刺收益最大)
    sprint_words.sort(key=lambda row: row[0].memory_strength or 0.0)
    sprint_words = sprint_words[:capped_limit]

    stored_settings = get_private_model_settings(db, current_user.id)
    choice_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    words = [ws.word for ws, _dim in sprint_words]
    translations = ensure_word_translations(db, current_user.id, words, choice_settings)
    db.commit()  # 持久化翻译缓存(与其它 GET 队列同款修复)

    items: list[LearningItemRead] = []
    for ws, dim in sprint_words:
        probe = LearningItem(
            user_id=current_user.id,
            course_id=None,
            item_type="word",
            english_text=ws.word,
            chinese_text=translations.get(ws.word, "") or "",
        )
        card_type = _DIM_CARD_TYPE.get(dim)
        if dim == "use":
            # 用词维:从课程包里找一句含该词的句子做完形;找不到退化为手写。
            # 2026-08-18 回归修复: SQL contains 是子串匹配——"art" 会命中
            # "start"。拉候选句后在 Python 侧按分词精确匹配。
            sentence_rows = db.scalars(
                select(LearningItem).where(
                    LearningItem.user_id == current_user.id,
                    LearningItem.item_type == "sentence",
                    func.lower(LearningItem.english_text).contains(ws.word[:4] if len(ws.word) > 4 else ws.word),
                ).limit(20)
            ).all()
            sentence_item = next(
                (row for row in sentence_rows if ws.word in tokenize_words(row.english_text or "")),
                None,
            )
            if sentence_item is not None:
                items.append(
                    LearningItemRead(
                        id=uuid4(),
                        source_item_id=sentence_item.id,
                        user_id=current_user.id,
                        course_id=sentence_item.course_id,
                        item_type="sentence",
                        english_text=sentence_item.english_text,
                        chinese_text=sentence_item.chinese_text or "",
                        difficulty_level=sentence_item.difficulty_level,
                        source="毕业冲刺·用词",
                        review_task_type="cloze_sentence",
                        focus_words=[ws.word],
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue
            card_type = "handwriting_dictation"
        choices: list[str] = []
        correct_answer: str | None = None
        if card_type in ("listen_choose_chinese", "english_to_chinese"):
            choices, correct_answer = _enrich_choices_for_word(db, current_user.id, ws.word, probe, choice_settings)
            if not choices or not correct_answer:
                continue
        items.append(
            LearningItemRead(
                id=uuid4(),
                source_item_id=ws.learning_item_id,
                user_id=current_user.id,
                course_id=None,
                item_type="word",
                english_text=ws.word,
                chinese_text=translations.get(ws.word, "") or "",
                difficulty_level=3,
                source=f"毕业冲刺·{_DIM_LABEL.get(dim, '')}",
                review_task_type=card_type,
                review_choices=choices,
                review_answer=correct_answer or (ws.word if card_type == "voice_practice" else None),
                review_prompt="🎤 跟我读一遍" if card_type == "voice_practice" else None,
                focus_words=[ws.word],
                created_at=now,
                updated_at=now,
            )
        )
    return items


@router.get("/daily-flow/reteach", response_model=list[LearningItemRead])
def list_daily_flow_reteach(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = DAILY_FLOW_RETEACH_LIMIT,
) -> list[LearningItemRead]:
    """昨日回炉: 昨天真测试失败的词,先低成本识别重建,再考最近失败的维度。"""
    capped_limit = max(1, min(limit, 16))
    now = datetime.now(UTC)
    today_start = now.astimezone(LOCAL_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_utc = (today_start - timedelta(days=1)).astimezone(UTC)
    today_start_utc = today_start.astimezone(UTC)
    # 昨天真测试(非辅助模式)失败的单词
    failed_rows = db.execute(
        select(LearningItem.english_text)
        .join(ReviewLog, ReviewLog.learning_item_id == LearningItem.id)
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.is_correct.is_(False),
            ReviewLog.reviewed_at >= yesterday_start_utc,
            ReviewLog.reviewed_at < today_start_utc,
            ReviewLog.review_mode.notin_(sorted(ASSISTED_REVIEW_MODES)),
            LearningItem.item_type == "word",
        )
        .group_by(LearningItem.english_text)
    ).all()
    failed_words = {normalize_word(text or "") for _text in failed_rows for text in [_text] if normalize_word(text or "")}
    failed_words.discard("")
    if not failed_words:
        return []
    word_states = db.scalars(
        select(WordMemoryState).where(
            WordMemoryState.user_id == current_user.id,
            WordMemoryState.word.in_(list(failed_words)),
        )
    ).all()
    # 最弱的排最前;视觉词不进回炉(退休口径全局一致)
    candidates = [ws for ws in word_states if ws.word and ws.word not in SIGHT_WORDS]
    candidates.sort(key=lambda ws: ws.memory_strength or 0.0)
    candidates = candidates[:capped_limit]
    if not candidates:
        return []
    stored_settings = get_private_model_settings(db, current_user.id)
    choice_settings = build_llm_translation_settings(None, None, None, None, stored_settings)
    translations = ensure_word_translations(db, current_user.id, [ws.word for ws in candidates], choice_settings)
    db.commit()

    items: list[LearningItemRead] = []
    for ws in candidates:
        probe = LearningItem(
            user_id=current_user.id,
            course_id=None,
            item_type="word",
            english_text=ws.word,
            chinese_text=translations.get(ws.word, "") or "",
        )
        choices, correct_answer = _enrich_choices_for_word(db, current_user.id, ws.word, probe, choice_settings)
        if not choices or not correct_answer:
            continue
        weak_dim = ws.dim_last_failed if ws.dim_last_failed in _DIM_CARD_TYPE else "meaning"
        # 第一张:识别重建(低成本热身,先赢一次);第二张:弱维验证。
        items.append(
            LearningItemRead(
                id=uuid4(),
                source_item_id=ws.learning_item_id,
                user_id=current_user.id,
                course_id=None,
                item_type="word",
                english_text=ws.word,
                chinese_text=correct_answer,
                difficulty_level=3,
                source="昨日回炉·热身",
                review_task_type="listen_choose_chinese",
                review_prompt="听英文发音，选择正确的中文意思",
                review_choices=choices,
                review_answer=correct_answer,
                focus_words=[ws.word],
                created_at=now,
                updated_at=now,
            )
        )
        verify_type = _DIM_CARD_TYPE[weak_dim]
        items.append(
            LearningItemRead(
                id=uuid4(),
                source_item_id=ws.learning_item_id,
                user_id=current_user.id,
                course_id=None,
                item_type="word",
                english_text=ws.word,
                chinese_text=correct_answer,
                difficulty_level=3,
                source="昨日回炉·验证",
                review_task_type=verify_type,
                review_prompt="🎤 跟我读一遍" if verify_type == "voice_practice" else None,
                review_choices=choices if verify_type in ("listen_choose_chinese", "english_to_chinese") else [],
                review_answer=correct_answer if verify_type in ("listen_choose_chinese", "english_to_chinese") else (ws.word if verify_type == "voice_practice" else None),
                focus_words=[ws.word],
                created_at=now,
                updated_at=now,
            )
        )
    return items


@router.post("/handwriting-check", response_model=HandwritingCheckResponse)
async def check_handwriting(
    payload: HandwritingCheckRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HandwritingCheckResponse:
    """Judge a handwritten answer with the vision LLM AND record the review.

    One atomic call (the client cannot talk to the vision model directly, so
    letting it submit the verdict separately would let a buggy/playing client
    mark itself correct): judge → real review_log (score 4/1, feeds FSRS) →
    word-memory sync → points → replay event (which anchors the study-time
    session windows, so handwriting minutes show up on the dashboard).
    """
    if not payload.image.startswith("data:image/") or len(payload.image) > MAX_IMAGE_DATA_URL_CHARS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image")
    expected_english = payload.expected_english.strip()
    if not expected_english:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expected_english is required")

    stored_settings = get_private_model_settings(db, current_user.id)
    # The vision judge runs on the Agent Plan channel (the plan includes the
    # multimodal doubao models; the legacy DeepSeek-direct LLM is text-only).
    api_key = string_setting(stored_settings, "agentPlanApiKey")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="手写识别需要先配置 Agent Plan API Key（请家长在模型设置中填写）",
        )
    base_url = string_setting(stored_settings, "agentPlanBaseUrl") or AGENT_PLAN_DEFAULT_BASE_URL
    vision_model = string_setting(stored_settings, "handwritingVisionModel") or DEFAULT_VISION_MODEL

    try:
        verdict = await run_in_threadpool(
            judge_handwriting,
            payload.image,
            payload.task_type,
            expected_english=expected_english,
            expected_chinese=payload.expected_chinese.strip(),
            base_url=base_url,
            api_key=api_key,
            model=vision_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    learning_item: LearningItem | None = None
    if payload.learning_item_id is not None:
        learning_item = db.scalar(
            select(LearningItem).where(
                LearningItem.id == payload.learning_item_id,
                LearningItem.user_id == current_user.id,
            )
        )
    if learning_item is None:
        # Same never-lost rule as /word-reviews: resolve by text.
        learning_item = db.scalar(
            select(LearningItem).where(
                LearningItem.user_id == current_user.id,
                LearningItem.english_text == expected_english,
            ).limit(1)
        )

    # 句子手写与单词手写分开记账：review_mode 保持 "sentence-" 前缀，
    # 复习接口的 SENTENCE_DAILY_CAP（LIKE 'sentence-%'）才能把复习队列里
    # 的句子手写纳入每日 3 句限量（手写化后句子不再产生 sentence-typing
    # 日志，没有这个分支限量就失效了）。每日一测（handwriting_both）是
    # 独立模式独立记账（只出单词）。
    is_sentence_answer = (
        (learning_item is not None and learning_item.item_type == "sentence")
        or len(tokenize_words(expected_english)) > 1
    )
    if payload.task_type == HANDWRITING_BOTH_TASK_TYPE:
        review_mode = HANDWRITING_BOTH_REVIEW_MODE
    elif payload.task_type == HANDWRITING_TRANSLATION_TASK_TYPE:
        review_mode = HANDWRITING_TRANSLATION_REVIEW_MODE
    elif is_sentence_answer:
        review_mode = SENTENCE_HANDWRITING_REVIEW_MODE
    else:
        review_mode = HANDWRITING_DICTATION_REVIEW_MODE
    if verdict.correct:
        error_type = None
    elif payload.task_type == HANDWRITING_TRANSLATION_TASK_TYPE:
        error_type = "meaning"
    elif payload.task_type == HANDWRITING_BOTH_TASK_TYPE:
        # 双关判定：英文错记 spelling，英文对但中文错记 meaning——
        # 错因分流决定回炉微任务的类型。
        error_type = "spelling" if verdict.english_ok is False else "meaning"
    else:
        error_type = "spelling"
    # 一期改造(2026-08-18): 手滑判定(单词手写)。视觉识别把潦草但高度
    # 相似的字判错时,不记 lapse、不动 FSRS——孩子重写一遍即可。只对单词
    # 听写生效(句子手写和双关翻译不适用),门槛比键盘输入更高(0.8):
    # 手写识别本身有误差,放宽会把真不会也放进来。
    if not verdict.correct and error_type == "spelling" and len(tokenize_words(expected_english)) == 1:
        hw_similarity = spelling_similarity(expected_english, verdict.recognized or "")
        if hw_similarity >= SLIP_HANDWRITING_MIN_SIMILARITY:
            complete_word_review_task(db, current_user.id, payload.review_task_id, True)
            db.commit()
            return HandwritingCheckResponse(
                recognized=verdict.recognized,
                correct=verdict.correct,
                comment=verdict.comment,
                expected=expected_english,
                learning_item_id=learning_item.id if learning_item else None,
                english_ok=verdict.english_ok,
                chinese_ok=verdict.chinese_ok,
                is_slip=True,
            )
    # 2026-08-04 fix: sentence answers must NOT mint word-type word-memory
    # items. Previously get_or_create_word_memory_item received the whole
    # sentence text, creating a permanent item_type="word" item per sentence
    # (normalize_word keeps interior spaces) that then re-entered the 12-word
    # handwriting quota as duplicate sentence dictation and polluted
    # WordMemoryState word metrics. Sentence answers book the review on the
    # original course item; only genuine single words get a word-memory twin.
    if is_sentence_answer and learning_item is not None:
        review_target_item = learning_item
    else:
        review_target_item = get_or_create_word_memory_item(db, current_user.id, expected_english, learning_item)
    word_item = review_target_item  # response/points keep the historical name
    # 2026-08-16: P18 daily attempt cap — create_word_review has enforced
    # MAX_DAILY_REVIEWS_PER_WORD for keyboard reviews, but this handwriting
    # path scheduled unconditionally: 每日一测 gate 3 (handwriting_dictation)
    # put a 3rd FSRS mutation on a word already attempted twice today (复习
    # 阶段 2 次 + 三关各 1 次). Beyond the cap: log for telemetry, settle the
    # task, no FSRS mutation, no points (same 2026-08-09 anti-farming rule).
    today_start = datetime.now(LOCAL_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    attempts_today = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.learning_item_id == review_target_item.id,
            ReviewLog.reviewed_at >= today_start,
        )
    ) or 0
    if attempts_today >= MAX_DAILY_REVIEWS_PER_WORD:
        log_only_review_log = ReviewLog(
            user_id=current_user.id,
            learning_item_id=review_target_item.id,
            review_mode=review_mode,
            error_type=error_type,
            score=4 if verdict.correct else 1,
            context=payload.context if payload.context == DAILY_TEST_CONTEXT else None,
            is_correct=verdict.correct,
            response_text=verdict.recognized[:200],
            duration_seconds=max(int(payload.duration_seconds or 0), 0),
        )
        db.add(log_only_review_log)
        db.flush()
        db.refresh(log_only_review_log)
        complete_word_review_task(db, current_user.id, payload.review_task_id, log_only_review_log.is_correct)
        try:
            from app.services.learning_replay import record_learning_event
            capped_ms = min(max(int(payload.duration_seconds or 0), 0) * 1000, 5 * 60 * 1000)
            with db.begin_nested():
                record_learning_event(db, current_user.id, log_only_review_log, review_target_item, duration_ms=capped_ms or 30_000)
        except Exception as exc:
            logger.warning("Failed to record learning replay event for capped handwriting review: %s", exc)
        db.commit()
        return HandwritingCheckResponse(
            recognized=verdict.recognized,
            correct=verdict.correct,
            comment=verdict.comment,
            expected=expected_english,
            learning_item_id=word_item.id,
            english_ok=verdict.english_ok,
            chinese_ok=verdict.chinese_ok,
        )
    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=review_target_item.id,
        score=4 if verdict.correct else 1,
        review_mode=review_mode,
        response_text=verdict.recognized[:200],
        duration_seconds=max(int(payload.duration_seconds or 0), 0),
        error_type=error_type,
    )
    if payload.context == DAILY_TEST_CONTEXT:
        result.review_log.context = DAILY_TEST_CONTEXT
    if not is_sentence_answer:
        sync_word_memory_from_review(
            db, current_user.id, review_target_item.english_text, result.memory_state, review_mode,
            result.review_log.is_correct, error_type,
        )
    try:
        from app.services.points_service import POINTS_CORRECT_NO_HINT, POINTS_WRONG, award_points
        with db.begin_nested():
            if result.review_log.is_correct:
                award_points(db, current_user.id, POINTS_CORRECT_NO_HINT, "handwriting_correct", f"手写正确 +{POINTS_CORRECT_NO_HINT}", word_item.id)
            else:
                award_points(db, current_user.id, POINTS_WRONG, "handwriting_wrong", f"手写错误 {POINTS_WRONG}", word_item.id)
    except Exception:
        pass  # points failure should never block learning
    try:
        from app.services.learning_replay import record_learning_event
        duration_ms = min(max(int(payload.duration_seconds or 0), 0) * 1000, 5 * 60 * 1000) or 30_000
        # Savepoint containment: a replay-recording failure must never poison
        # the session — the review itself still has to commit (2026-07-30:
        # zombie pending insert → PendingRollbackError → 500, answer lost).
        with db.begin_nested():
            record_learning_event(db, current_user.id, result.review_log, word_item, duration_ms=duration_ms)
    except Exception as exc:
        logger.warning("Failed to record learning replay event for handwriting review: %s", exc)
    # 微任务闭环：复习队列把键盘拼写微任务映射成手写后，手写提交必须
    # 同样结算 WordReviewTask，否则任务永远 pending、每天重复出队。
    complete_word_review_task(db, current_user.id, payload.review_task_id, result.review_log.is_correct)
    db.commit()
    return HandwritingCheckResponse(
        recognized=verdict.recognized,
        correct=verdict.correct,
        comment=verdict.comment,
        expected=expected_english,
        learning_item_id=word_item.id,
        english_ok=verdict.english_ok,
        chinese_ok=verdict.chinese_ok,
    )


@router.post("/word-mistakes", response_model=WordMistakeLogResponse, status_code=status.HTTP_201_CREATED)
def create_word_mistake_log(
    payload: WordMistakeLogRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WordMistakeLogResponse:
    learning_item = db.scalar(select(LearningItem).where(LearningItem.id == payload.learning_item_id, LearningItem.user_id == current_user.id))
    if learning_item is None:
        # Focus-mode/AI-generated items carry synthetic ids (uuid4) that do not
        # exist in the DB. Resolve by word text so the review is NEVER lost —
        # previously this raised 404 and the review silently vanished while
        # study time kept recording (heatmap showed minutes, day detail showed 0).
        _w = normalize_word(getattr(payload, "word", "") or getattr(payload, "expected_word", ""))
        if _w:
            learning_item = db.scalar(
                select(LearningItem).where(
                    LearningItem.user_id == current_user.id,
                    LearningItem.english_text == _w,
                ).limit(1)
            )
        logger.warning(
            "Synthetic learning_item_id %s resolved by word %r (found=%s)",
            payload.learning_item_id, _w, learning_item is not None,
        )

    word_item = get_or_create_word_memory_item(db, current_user.id, payload.expected_word, learning_item)
    error_type = normalize_word_error_type(payload.error_type)
    # 一期改造(2026-08-18): 手滑(slip)与真不会(gap)分离。字面高度相似的
    # 笔误类小错(颠倒/多字母/漏字母/词尾)不记 lapse、不动 FSRS、不进错词
    # 本——只留回放遥测,响应 is_slip=True 让前端原地温和重答。这本是
    # word-reviews 端点的同款判定;键盘拼写走本端点,漏掉它等于主入口没改。
    if error_type in SLIP_ERROR_TYPES:
        slip_similarity = spelling_similarity(payload.expected_word or "", payload.actual_word or "")
        if slip_similarity >= SLIP_MIN_SIMILARITY:
            try:
                from app.services.learning_replay import record_assisted_learning_event
                with db.begin_nested():
                    record_assisted_learning_event(
                        db,
                        current_user.id,
                        word_item,
                        "word-spelling",
                        2,
                        response_text=(payload.actual_word or "").strip() or None,
                        duration_ms=min(max(int(payload.duration_seconds or 0), 0) * 1000, 5 * 60 * 1000) or 20_000,
                        error_type=error_type,
                        is_correct=None,
                    )
            except Exception as exc:
                logger.warning("Failed to record slip telemetry for word mistake: %s", exc)
            db.commit()
            return WordMistakeLogResponse(logged_count=0, is_slip=True)
    # P13: partial credit — near-miss spellings (>= 80% letter similarity)
    # record score=2 instead of the flat score=1. Scheduling treats both as
    # failures (rating Again), but the score preserves the difference for
    # analytics and the effectiveness dashboard.
    mistake_score = 2 if spelling_similarity(payload.expected_word, payload.actual_word) >= 0.8 else 1
    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=word_item.id,
        score=mistake_score,
        review_mode="word-spelling",
        response_text=payload.actual_word.strip(),
        duration_seconds=max(int(payload.duration_seconds or 0), 0),
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
        with db.begin_nested():
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
        # Focus-mode/AI-generated items carry synthetic ids (uuid4) that do not
        # exist in the DB. Resolve by word text so the review is NEVER lost —
        # previously this raised 404 and the review silently vanished while
        # study time kept recording (heatmap showed minutes, day detail showed 0).
        _w = normalize_word(getattr(payload, "word", "") or getattr(payload, "expected_word", ""))
        if _w:
            learning_item = db.scalar(
                select(LearningItem).where(
                    LearningItem.user_id == current_user.id,
                    LearningItem.english_text == _w,
                ).limit(1)
            )
        logger.warning(
            "Synthetic learning_item_id %s resolved by word %r (found=%s)",
            payload.learning_item_id, _w, learning_item is not None,
        )

    # 2026-08-09 fix (same class of bug the handwriting path fixed on
    # 2026-08-04): voice_practice read-alouds submit the WHOLE sentence as
    # `word`. Minting a word-type word-memory item for a sentence pollutes
    # word metrics and eats daily queue budget with an unservable ghost item
    # (its >24-char Chinese fails sentence validation, yet it still takes a
    # due_rows slot). Book the review on the resolved real item instead; only
    # genuine single words get a word-memory twin.
    is_sentence_word_payload = " " in (payload.word or "").strip()
    if is_sentence_word_payload and learning_item is not None:
        word_item = learning_item
    else:
        word_item = get_or_create_word_memory_item(db, current_user.id, payload.word, learning_item)
    review_mode = payload.review_mode.strip()[:32]
    error_type = normalize_word_error_type(payload.error_type) if payload.error_type else None

    # Plan A: near-miss upgrade. A score==2 attempt that is highly similar to
    # the target is upgraded to a passing 3 so it counts as correct (does not
    # lapse / reset intervals). Falls back to server-side similarity when the
    # client did not supply one.
    review_score = payload.score
    if review_score == 2:
        sim = payload.spelling_similarity
        if sim is None:
            sim = spelling_similarity(payload.word or "", payload.response_text or "")
        if sim >= NEAR_MISS_SIMILARITY:
            review_score = 3

    # P15: assisted phases (answer shown / heavy hints BEFORE responding) can
    # never fail, so they are telemetry-only — no review_log, no FSRS
    # mutation, no mistake_log, no accuracy contribution. They used to be 63%
    # of all review_logs with a fake 100% correct rate, which both inflated
    # FSRS stability and poisoned every accuracy metric. The teaching value
    # is preserved: the task completes, points are awarded, and the event is
    # recorded for the replay timeline.
    # 2026-08-16: word-context (句中拼写) 并入 assisted 门控。该模式
    # 提交只可能是"成功"（失败走 word-spelling 错词端点），此前漏进
    # 门控导致它走真实测试路径：FSRS 照常推进、连错被清零（熔断信号
    # 丢失）、记 review_log、发 +10——与下方 2026-08-11 死锁修复注释
    # 的设计意图完全相反，那条 elif 手臂实际是死代码。常量
    # ASSISTED_REVIEW_MODES 保持不变（统计口径与测试钉死了它），
    # 仅在此门控放行。
    if review_mode in ASSISTED_REVIEW_MODES or review_mode == "word-context":
        now_utc = datetime.now(UTC)
        if not is_sentence_word_payload:
            word_state = get_or_create_word_memory_state(db, current_user.id, word_item.english_text, word_item.id)
            word_state.last_reviewed_at = now_utc
            if review_mode == "word-preview":
                word_state.hidden_recall_correct_count += 1
                word_state.last_answer_seen_at = now_utc
            elif review_mode == "word-context":
                # 2026-08-11 状态死锁修复：句中拼写成功是真实产出（词本身
                # 并未预先展示，只是借助句子语境），但 assisted 分支不动
                # FSRS、不清连错——失败（word-spelling 错词端点）却照常
                # +1。单向棘轮让"with"这类词连错冻结在 104。这里给成功一
                # 条解冻通道：连错 -1（不清零，真困难词的熔断信号要保留），
                # 并按现有门槛重算状态。
                word_state.context_correct_count += 1
                # 二期配套修复(2026-08-18 回归发现): word-context 是"用"
                # 维度(句中用词)的唯一供给源,assisted 门控让它绕过了
                # sync_word_memory_from_review,五维的 dim_use_days 永远不
                # 增长、五维毕业永远不可达。在此直接更新维度进度。
                _update_dimension_progress(word_state, review_mode, True, now_utc)
                word_state.consecutive_error_count = max(0, (word_state.consecutive_error_count or 0) - 1)
                if (word_state.memory_strength or 0) >= 0.5:
                    stats = get_recent_word_test_stats(db, current_user.id, word_state.learning_item_id)
                    if stats is not None:
                        word_state.status = derive_word_status(word_state, stats[0], stats[1], stats[2])
                    else:
                        word_state.status = derive_word_status(word_state)
            db.add(word_state)
        complete_word_review_task(db, current_user.id, payload.review_task_id, True)
        try:
            from app.services.points_service import POINTS_CORRECT_HINTED, POINTS_CORRECT_PREVIEW, award_points
            # Savepoint containment: a points failure must not poison the
            # session (PendingRollbackError → 500 → client retry double-counts).
            with db.begin_nested():
                if review_mode == "word-hinted":
                    award_points(db, current_user.id, POINTS_CORRECT_HINTED, "word_hinted", f"提示后正确拼写 +{POINTS_CORRECT_HINTED}", word_item.id)
                elif review_mode == "word-preview":
                    award_points(db, current_user.id, POINTS_CORRECT_PREVIEW, "word_preview", f"预览后正确拼写 +{POINTS_CORRECT_PREVIEW}", word_item.id)
                else:
                    award_points(db, current_user.id, POINTS_CORRECT_HINTED, "word_assisted", f"辅助练习 +{POINTS_CORRECT_HINTED}", word_item.id)
        except Exception:
            pass  # points failure should never block learning
        try:
            from app.services.learning_replay import record_assisted_learning_event
            encoding_ms = int(payload.encoding_duration_ms or 0)
            total_ms = int(payload.duration_seconds or 0) * 1000
            # Savepoint containment (2026-08-16): without it a recorder
            # failure aborts the outer transaction and the commit below
            # raises PendingRollbackError — also rolling back the word_state
            # unfreeze, task completion and points staged above.
            with db.begin_nested():
                record_assisted_learning_event(
                    db,
                    current_user.id,
                    word_item,
                    review_mode,
                    review_score,
                    response_text=(payload.response_text or "").strip() or None,
                    duration_ms=min(encoding_ms or total_ms or 20_000, 5 * 60 * 1000),
                    error_type=error_type,
                )
        except Exception as exc:
            logger.warning("Failed to record assisted learning event: %s", exc)
        db.commit()
        return WordReviewResponse(learning_item_id=word_item.id, word=word_item.english_text)

    # 一期改造(2026-08-18): 失误(slip)与不会(gap)分离。手滑型错误
    # (字面高度相似的笔误类小错)不是"不会":不记 lapse、不动 FSRS、不扣
    # 积分、不产生错词本记录——只留一条回放遥测,响应带 is_slip=True 让
    # 前端原地重答。此前手滑与"完全不认识"同罚,简单词因一次笔误被连错
    # 计数拖进反复重考的循环。
    if review_score < 3 and error_type in SLIP_ERROR_TYPES:
        slip_similarity = payload.spelling_similarity
        if slip_similarity is None:
            slip_similarity = spelling_similarity(payload.word or "", payload.response_text or "")
        if slip_similarity >= SLIP_MIN_SIMILARITY:
            complete_word_review_task(db, current_user.id, payload.review_task_id, True)
            try:
                from app.services.learning_replay import record_assisted_learning_event
                with db.begin_nested():
                    record_assisted_learning_event(
                        db,
                        current_user.id,
                        word_item,
                        review_mode,
                        review_score,
                        response_text=(payload.response_text or "").strip() or None,
                        duration_ms=min(int(payload.duration_seconds or 0) * 1000, 5 * 60 * 1000) or 20_000,
                        error_type=error_type,
                        is_correct=None,
                    )
            except Exception as exc:
                logger.warning("Failed to record slip telemetry: %s", exc)
            db.commit()
            return WordReviewResponse(learning_item_id=word_item.id, word=word_item.english_text, is_slip=True)

    # P18: in-flight daily attempt cap. The due-queue already hides items
    # with >= MAX_DAILY_REVIEWS_PER_WORD reviews today, but task/focus items
    # can still submit further attempts. Beyond the cap, log the attempt for
    # telemetry WITHOUT mutating FSRS state or spawning correction tasks —
    # extra same-day repetitions produce no learning signal (production: one
    # word was tested up to 136x in a single day).
    today_start = datetime.now(LOCAL_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    attempts_today = db.scalar(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.learning_item_id == word_item.id,
            ReviewLog.reviewed_at >= today_start,
        )
    ) or 0
    if attempts_today >= MAX_DAILY_REVIEWS_PER_WORD:
        log_only_review_log = ReviewLog(
            user_id=current_user.id,
            learning_item_id=word_item.id,
            review_mode=review_mode,
            error_type=error_type,
            score=review_score,
            context=payload.context if payload.context == DAILY_TEST_CONTEXT else None,
            is_correct=review_score >= 3,
            response_text=(payload.response_text or "").strip(),
            duration_seconds=payload.duration_seconds,
            encoding_stage=payload.encoding_stage,
            encoding_duration_ms=payload.encoding_duration_ms,
        )
        db.add(log_only_review_log)
        db.flush()
        # reviewed_at is a server_default — refresh so the replay event below
        # can read it (record_learning_event skips logs with reviewed_at=None).
        db.refresh(log_only_review_log)
        complete_word_review_task(db, current_user.id, payload.review_task_id, log_only_review_log.is_correct)
        # 2026-08-09: NO points in the capped branch. The cap exists to stop
        # same-word farming (production: 136 attempts/day on one word) —
        # awarding +10 per capped attempt let exactly that loop print points.
        try:
            from app.services.learning_replay import record_learning_event
            capped_ms = min(int(payload.duration_seconds or 0) * 1000, 5 * 60 * 1000)
            with db.begin_nested():
                record_learning_event(db, current_user.id, log_only_review_log, word_item, duration_ms=capped_ms or 20_000)
        except Exception as exc:
            logger.warning("Failed to record learning replay event for capped word review: %s", exc)
        db.commit()
        return WordReviewResponse(learning_item_id=word_item.id, word=word_item.english_text)

    result = schedule_memory_review(
        db=db,
        user_id=current_user.id,
        learning_item_id=word_item.id,
        score=review_score,
        review_mode=review_mode,
        response_text=(payload.response_text or "").strip(),
        duration_seconds=payload.duration_seconds,
        error_type=error_type,
        encoding_stage=payload.encoding_stage,
        encoding_duration_ms=payload.encoding_duration_ms,
    )
    # 每日一测标记：测试队列"今日已测不重出"的排除依赖 context（见
    # list_daily_test_items）。只接受白名单值，客户端不能写任意场景。
    if payload.context == DAILY_TEST_CONTEXT:
        result.review_log.context = DAILY_TEST_CONTEXT
    word_state = None if is_sentence_word_payload else sync_word_memory_from_review(db, current_user.id, word_item.english_text, result.memory_state, review_mode, result.review_log.is_correct, error_type)

    # Plan E (2026-08-07): confusable-word bookkeeping. When the child fails a
    # word by typing a DIFFERENT word they are also studying (e.g. writes
    # "here" for "hear"), tag the target word's error_type_counts with
    # `confusable:<typed>` so the parent's word-detail error breakdown shows
    # the confusion, and log the pair for later contrast re-teaching.
    if word_state is not None and not result.review_log.is_correct:
        typed = normalize_word((payload.response_text or "").strip())
        target = normalize_word(word_item.english_text or "")
        if typed and target and typed != target and " " not in typed:
            confused_item = db.scalar(
                select(LearningItem).where(
                    LearningItem.user_id == current_user.id,
                    func.lower(LearningItem.english_text) == typed,
                ).limit(1)
            )
            if confused_item is not None:
                counts = dict(word_state.error_type_counts or {})
                key = f"confusable:{typed}"
                existing = counts.get(key)
                # 2026-08-16: legacy rows stored non-numeric strings here;
                # int(existing) raised ValueError AFTER the review had
                # already committed (old mid-request commit), so the client
                # retried and double-graded the answer. Parse defensively.
                raw_count = existing.get("count", 0) if isinstance(existing, dict) else (existing or 0)
                try:
                    prev_count = int(raw_count)
                except (TypeError, ValueError):
                    prev_count = 0
                counts[key] = {"count": prev_count + 1, "last": datetime.now(UTC).isoformat()}
                word_state.error_type_counts = counts
                db.add(word_state)
                logger.info("confusable-pair target=%r typed=%r user=%s", target, typed, current_user.id)

    complete_word_review_task(db, current_user.id, payload.review_task_id, result.review_log.is_correct)
    if result.review_log.is_correct:
        supersede_stale_pending_tasks_for_reviewed_words(db, current_user.id)
        # Award points for correct word review
        try:
            from app.services.points_service import POINTS_CORRECT_HINTED, POINTS_CORRECT_NO_HINT, POINTS_CORRECT_PREVIEW, POINTS_PERFECT_SENTENCE, award_points
            # Savepoint containment: a points failure must not poison the
            # session (PendingRollbackError → 500 → client retry double-counts).
            with db.begin_nested():
                if review_mode.startswith("word-recall"):
                    award_points(db, current_user.id, POINTS_CORRECT_NO_HINT, "word_correct", f"无提示正确拼写 +{POINTS_CORRECT_NO_HINT}", word_item.id)
                elif review_mode.startswith("word-hinted"):
                    award_points(db, current_user.id, POINTS_CORRECT_HINTED, "word_hinted", f"提示后正确拼写 +{POINTS_CORRECT_HINTED}", word_item.id)
                elif review_mode.startswith("word-preview"):
                    award_points(db, current_user.id, POINTS_CORRECT_PREVIEW, "word_preview", f"预览后正确拼写 +{POINTS_CORRECT_PREVIEW}", word_item.id)
                elif review_mode.startswith("sentence-spelling") and review_score >= 5:
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
        if review_mode == "sentence-spelling" and word_state is not None:
            schedule_micro_review_tasks_for_mistake(db, current_user.id, word_state, learning_item.chinese_text if learning_item else word_item.english_text, learning_item.id if learning_item else None, error_type or "spelling")
        # Deduct points for wrong answer
        try:
            from app.services.points_service import POINTS_WRONG, award_points
            with db.begin_nested():
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
        with db.begin_nested():
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

    # 2026-08-16: bounded read — the previous `await file.read()` buffered
    # the ENTIRE upload before the size check below, so one oversized POST
    # could balloon RSS on the single uvicorn worker (the pronunciation
    # endpoint at ~2342 already uses this pattern).
    content = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(content) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 10 MB)")

    try:
        if extension == ".txt":
            parse_result = parse_txt_import(content, filename)
        else:
            parse_result = parse_xlsx_import(content, filename)
    except ValueError as exc:
        # Bad encoding / unreadable content — a client error, not a 500
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法解析文件: {exc}") from exc

    stored_settings = get_private_model_settings(db, current_user.id)
    translation_settings = build_llm_translation_settings(llm_provider, llm_base_url, llm_model, llm_api_key, stored_settings)

    # Blocking DB + LLM translation work — run in the threadpool so the
    # event loop stays responsive for other requests during long imports.
    imported_items, duplicate_skipped_items = await run_in_threadpool(
        import_learning_items,
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
