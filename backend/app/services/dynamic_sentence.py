from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.services.llm_translation import LlmTranslationSettings, generate_learning_text, translate_english_to_chinese
from app.utils import extract_mistake_words, normalize_word, tokenize_words


@dataclass(frozen=True)
class DynamicSentenceResult:
    english_text: str
    chinese_text: str
    focus_words: list[str]
    known_words: list[str]
    weak_words: list[str]


COMMON_FILLER_WORDS = ["I", "can", "see", "the", "and", "like", "my", "this", "is", "a"]
MIN_DYNAMIC_SENTENCE_WORDS = 5
MAX_DYNAMIC_SENTENCE_WORDS = 7


def unique_preserve_order(words: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_words: list[str] = []
    for word in words:
        normalized = normalize_word(word)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_words.append(normalized)
    return unique_words


def build_mastery_word_pools(db: Session, user_id: UUID, course_id: UUID | None) -> tuple[list[str], list[str]]:
    statement = select(LearningItem, MemoryState).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(LearningItem.user_id == user_id)
    if course_id is not None:
        statement = statement.where(LearningItem.course_id == course_id)

    candidate_known_words: list[str] = []
    weak_words: list[str] = []
    for learning_item, memory_state in db.execute(statement).all():
        item_words = tokenize_words(learning_item.english_text)
        if memory_state is not None and memory_state.memory_strength >= 0.72 and memory_state.lapse_count == 0:
            candidate_known_words.extend(item_words)
        else:
            candidate_known_words.extend(item_words)

    mistake_statement = select(MistakeLog.mistake_type, MistakeLog.expected_answer, MistakeLog.actual_answer).where(
        MistakeLog.user_id == user_id,
        MistakeLog.is_resolved.is_(False),
    )
    if course_id is not None:
        mistake_statement = mistake_statement.join(LearningItem, LearningItem.id == MistakeLog.learning_item_id).where(LearningItem.course_id == course_id)
    for mistake_type, expected_answer, actual_answer in db.execute(mistake_statement).all():
        weak_words.extend(extract_mistake_words(mistake_type, expected_answer, actual_answer))

    weak_words = unique_preserve_order(weak_words)
    weak_set = set(weak_words)
    known_words = [word for word in unique_preserve_order(candidate_known_words) if word not in weak_set]
    return known_words, weak_words


def generate_dynamic_review_sentence(
    db: Session,
    user_id: UUID,
    course_id: UUID | None,
    current_sentence: str,
    mistaken_words: list[str],
    settings: LlmTranslationSettings,
) -> DynamicSentenceResult:
    known_words, historical_weak_words = build_mastery_word_pools(db, user_id, course_id)
    focus_words = unique_preserve_order([*mistaken_words, *historical_weak_words])[:3]
    if not focus_words:
        focus_words = unique_preserve_order(tokenize_words(current_sentence))[:1]

    selected_known_words = [word for word in known_words if word not in set(focus_words)][:10]
    required_words = unique_preserve_order(mistaken_words) or focus_words
    english_text = generate_sentence_with_llm(selected_known_words, focus_words, current_sentence, settings, required_words)
    english_text = normalize_sentence_length(english_text, selected_known_words, focus_words, required_words)
    chinese_text = translate_sentence_or_fallback(english_text, settings)

    return DynamicSentenceResult(
        english_text=english_text,
        chinese_text=chinese_text,
        focus_words=focus_words,
        known_words=selected_known_words,
        weak_words=historical_weak_words[:12],
    )


def generate_sentence_with_llm(
    known_words: list[str],
    focus_words: list[str],
    current_sentence: str,
    settings: LlmTranslationSettings,
    required_words: list[str],
) -> str:
    prompt = (
        "Create one simple English practice sentence for a young learner.\n"
        "Rules:\n"
        "- Return only the English sentence.\n"
        f"- Between {MIN_DYNAMIC_SENTENCE_WORDS} and {MAX_DYNAMIC_SENTENCE_WORDS} words.\n"
        "- Include at least one focus word.\n"
        f"- Must include at least one required mistake word: {', '.join(required_words)}.\n"
        "- About 85% of the words should come from known words when possible, and about 15% from focus words.\n"
        "- Use easy grammar and natural English.\n\n"
        f"Known words: {', '.join(known_words[:30]) or ', '.join(COMMON_FILLER_WORDS)}\n"
        f"Focus words: {', '.join(focus_words)}\n"
        f"Previous sentence: {current_sentence}"
    )
    try:
        return generate_learning_text(prompt, settings)
    except ValueError:
        return build_fallback_sentence(known_words, focus_words)


def normalize_sentence_length(sentence: str, known_words: list[str], focus_words: list[str], required_words: list[str]) -> str:
    cleaned = sentence.strip().replace("\n", " ")
    cleaned = re.sub(r"^[\"'“”]+|[\"'“”]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = cleaned.split()
    required_word_set = set(required_words)
    if MIN_DYNAMIC_SENTENCE_WORDS <= len(words) <= MAX_DYNAMIC_SENTENCE_WORDS and any(normalize_word(word) in required_word_set for word in words):
        return cleaned
    return build_fallback_sentence(known_words, focus_words)


def build_fallback_sentence(known_words: list[str], focus_words: list[str]) -> str:
    focus_word = focus_words[0] if focus_words else "word"
    support_words = [word for word in known_words if word != focus_word and word not in {"i", "a", "an", "the", "and"}][:2]
    if len(support_words) >= 2:
        sentence_words = ["I", "can", "use", focus_word, "with", support_words[0]]
    elif len(support_words) == 1:
        sentence_words = ["I", "can", "practice", focus_word, "with", support_words[0]]
    else:
        sentence_words = ["I", "can", "see", "the", focus_word]
    return " ".join(sentence_words[:MAX_DYNAMIC_SENTENCE_WORDS]) + "."


def translate_sentence_or_fallback(sentence: str, settings: LlmTranslationSettings) -> str:
    try:
        return translate_english_to_chinese(sentence, settings)
    except ValueError:
        return "AI 生成复习句"


