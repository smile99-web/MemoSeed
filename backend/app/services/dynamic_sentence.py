import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.generated_sentence import GeneratedSentence
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.word_memory_state import WordMemoryState
from app.services.llm_translation import LlmTranslationSettings, generate_learning_text, translate_english_to_chinese
from app.utils import extract_mistake_words, normalize_word, tokenize_words


@dataclass(frozen=True)
class DynamicSentenceResult:
    english_text: str
    chinese_text: str
    focus_words: list[str]
    known_words: list[str]
    weak_words: list[str]
    candidates: list[dict[str, str]]


COMMON_FILLER_WORDS = ["I", "can", "see", "the", "and", "like", "my", "this", "is", "a"]
WORD_MEMORY_SOURCE = "word-memory"

SENTENCE_TEMPLATE_POOL = [
    "Subject + can + verb + object",
    "There is + subject + in the + place",
    "Subject and subject + are + description",
    "I like to + verb + with my + object",
    "The + subject + is very + adjective",
    "Subject + likes to + verb + object",
    "I can see the + subject + and + object",
    "My + subject + is + adjective + and + adjective",
    "We + verb + the + object + together",
    "Subject + has a + object",
    "Subject + goes to + place",
    "It is + adjective + to + verb",
    "The + subject + with + object + is + description",
    "I + verb + because it is + adjective",
    "Do you + verb + the + object?",
]

FALLBACK_SENTENCE_TEMPLATES = [
    ("I can {} a {}.", ["use", "make", "find", "draw", "read"], None),
    ("The {0} is {1}.", ["big", "new", "good", "nice", "red"], None),
    ("We like to {} the {}.", ["see", "use", "draw", "read", "find"], None),
    ("This {0} can {1}.", ["run", "fly", "swim", "jump", "grow"], None),
    ("My {0} is very {1}.", ["good", "nice", "big", "new", "fun"], None),
    ("I have a {1} {0}.", ["new", "big", "red", "good", "small"], None),
    ("Let us {} with the {}.", ["play", "work", "learn", "read", "write"], None),
]
# For templates where the option words serve as noun-like fillers:
# templates at indices 1, 4, 5 use adjectives for the {1} slot; indices 3 uses verbs for {1}
_FALLBACK_VERB_SETS = {
    "I can {} a {}.": ["use", "make", "find", "draw", "read"],
    "We like to {} the {}.": ["see", "use", "draw", "read", "find"],
    "Let us {} with the {}.": ["play", "work", "learn", "read", "write"],
    "This {0} can {1}.": ["run", "fly", "swim", "jump", "grow"],
}
_FALLBACK_ADJ_SETS = {
    "The {0} is {1}.": ["big", "new", "good", "nice", "red"],
    "My {0} is very {1}.": ["good", "nice", "big", "new", "fun"],
    "I have a {1} {0}.": ["new", "big", "red", "good", "small"],
}


def _compute_words_hash(focus_words: list[str]) -> str:
    key = ",".join(sorted(set(normalize_word(w) for w in focus_words if w)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _pick_template(focus_words: list[str]) -> str:
    hash_bytes = hashlib.sha256(",".join(sorted(focus_words)).encode("utf-8")).digest()
    index = hash_bytes[0] % len(SENTENCE_TEMPLATE_POOL)
    return SENTENCE_TEMPLATE_POOL[index]


def _pick_fallback_template(focus_word: str, known_words: list[str]) -> str:
    seed = f"{focus_word}:{','.join(known_words[:5])}"
    hash_bytes = hashlib.sha256(seed.encode("utf-8")).digest()
    template_index = hash_bytes[0] % len(FALLBACK_SENTENCE_TEMPLATES)
    option_index = hash_bytes[1]

    pattern, nullable_words, _ = FALLBACK_SENTENCE_TEMPLATES[template_index]
    word_options = list(nullable_words) if nullable_words else ["word"]

    focus = focus_word if focus_word else "word"
    support_words = [w for w in known_words[:4] if w not in {"i", "a", "an", "the", "and"}]
    if not support_words:
        support_words = ["word"]

    if pattern.count("{}") == 2:
        if pattern in _FALLBACK_VERB_SETS:
            verb = word_options[option_index % len(word_options)]
            noun = support_words[option_index % len(support_words)]
            return pattern.format(verb, noun) + "."
        elif pattern in _FALLBACK_ADJ_SETS:
            noun = support_words[option_index % len(support_words)]
            adj = word_options[(option_index + 1) % len(word_options)]
            return pattern.format(noun, adj) + "."
        else:
            return pattern.format(focus, word_options[option_index % len(word_options)]) + "."
    else:
        return pattern.format(focus) + "."


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


def _count_mastered_words(db: Session, user_id: UUID) -> int:
    statement = select(WordMemoryState).where(
        WordMemoryState.user_id == user_id,
        WordMemoryState.memory_strength >= 0.72,
    )
    return len(db.execute(statement).scalars().all())


def _known_word_ratio(mastered_word_count: int) -> float:
    if mastered_word_count < 50:
        return 0.95
    elif mastered_word_count < 150:
        return 0.85
    else:
        return 0.75


def _difficulty_sentence_bounds(difficulty_level: int) -> tuple[int, int]:
    if difficulty_level <= 2:
        return (3, 5)
    elif difficulty_level <= 4:
        return (5, 7)
    else:
        return (7, 9)


def build_mastery_word_pools(db: Session, user_id: UUID, course_id: UUID | None) -> tuple[list[str], list[str], dict[str, float]]:
    statement = select(LearningItem, MemoryState).outerjoin(MemoryState, MemoryState.learning_item_id == LearningItem.id).where(LearningItem.user_id == user_id)
    if course_id is not None:
        statement = statement.where(LearningItem.course_id == course_id)

    candidate_known_words: list[str] = []
    word_strengths: dict[str, float] = {}
    weak_words: list[str] = []
    for learning_item, memory_state in db.execute(statement).all():
        item_words = tokenize_words(learning_item.english_text)
        is_direct_word = learning_item.item_type == "word" and learning_item.source == WORD_MEMORY_SOURCE
        if is_direct_word and memory_state is not None:
            if memory_state.memory_strength >= 0.72 and memory_state.lapse_count <= 1:
                candidate_known_words.extend(item_words)
                for w in item_words:
                    nw = normalize_word(w)
                    if nw not in word_strengths or memory_state.memory_strength > word_strengths[nw]:
                        word_strengths[nw] = memory_state.memory_strength
            else:
                weak_words.extend(item_words)
        elif memory_state is not None and memory_state.memory_strength >= 0.65 and memory_state.lapse_count <= 1:
            candidate_known_words.extend(item_words)
            for w in item_words:
                nw = normalize_word(w)
                if nw not in word_strengths or memory_state.memory_strength > word_strengths[nw]:
                    word_strengths[nw] = memory_state.memory_strength
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
    return known_words, weak_words, word_strengths


def _sort_known_by_strength(known_words: list[str], word_strengths: dict[str, float]) -> list[str]:
    return sorted(known_words, key=lambda w: word_strengths.get(w, 0.0), reverse=True)


def generate_dynamic_review_sentence(
    db: Session,
    user_id: UUID,
    course_id: UUID | None,
    current_sentence: str,
    mistaken_words: list[str],
    settings: LlmTranslationSettings,
    difficulty_level: int = 3,
) -> DynamicSentenceResult:
    known_words, historical_weak_words, word_strengths = build_mastery_word_pools(db, user_id, course_id)
    focus_words = unique_preserve_order([*mistaken_words, *historical_weak_words])[:3]
    if not focus_words:
        focus_words = unique_preserve_order(tokenize_words(current_sentence))[:1]

    words_hash = _compute_words_hash(focus_words)
    min_len, max_len = _difficulty_sentence_bounds(difficulty_level)
    mastered_count = _count_mastered_words(db, user_id)
    known_ratio = _known_word_ratio(mastered_count)

    # Check cache for existing sentences for this (focus_words_hash, difficulty_level)
    cached = _get_cached_sentences(db, words_hash, difficulty_level)
    if cached is not None and len(cached) >= 1:
        primary = cached[0]
        return DynamicSentenceResult(
            english_text=primary["english_text"],
            chinese_text=primary["chinese_text"],
            focus_words=focus_words,
            known_words=known_words[:12],
            weak_words=historical_weak_words[:12],
            candidates=cached,
        )

    known_by_strength = _sort_known_by_strength(known_words, word_strengths)
    selected_known_words = [word for word in known_by_strength if word not in set(focus_words)][:15]
    required_words = unique_preserve_order(mistaken_words) or focus_words

    # Generate 2-3 candidate sentences
    num_candidates = 3 if len(focus_words) >= 2 else 2
    candidates: list[dict[str, str]] = []
    for i in range(num_candidates):
        template = _pick_template(focus_words + [str(i)])
        english_text = _generate_sentence_with_llm(
            selected_known_words, focus_words, current_sentence, settings,
            required_words, template, known_ratio, min_len, max_len,
        )
        english_text = _normalize_sentence_length(english_text, selected_known_words, focus_words, required_words, min_len, max_len)
        chinese_text = translate_sentence_or_fallback(english_text, settings)
        if english_text and chinese_text:
            candidates.append({"english_text": english_text, "chinese_text": chinese_text})

    if not candidates:
        fb = _build_fallback_sentence(known_words, focus_words)
        candidates.append({"english_text": fb, "chinese_text": translate_sentence_or_fallback(fb, settings)})

    # Cache all generated candidates
    _cache_sentences(db, words_hash, difficulty_level, candidates)

    primary = candidates[0]
    return DynamicSentenceResult(
        english_text=primary["english_text"],
        chinese_text=primary["chinese_text"],
        focus_words=focus_words,
        known_words=selected_known_words[:12],
        weak_words=historical_weak_words[:12],
        candidates=candidates,
    )


def _get_cached_sentences(db: Session, words_hash: str, difficulty_level: int) -> list[dict[str, str]] | None:
    statement = select(GeneratedSentence).where(
        GeneratedSentence.focus_words_hash == words_hash,
        GeneratedSentence.difficulty_level == difficulty_level,
    ).order_by(GeneratedSentence.created_at.desc())
    rows = db.execute(statement).scalars().all()
    if not rows:
        return None
    return [{"english_text": row.english_text, "chinese_text": row.chinese_text} for row in rows]


def _cache_sentences(db: Session, words_hash: str, difficulty_level: int, candidates: list[dict[str, str]]) -> None:
    for candidate in candidates:
        sentence = GeneratedSentence(
            focus_words_hash=words_hash,
            difficulty_level=difficulty_level,
            english_text=candidate["english_text"],
            chinese_text=candidate["chinese_text"],
        )
        db.add(sentence)
    db.commit()


def _generate_sentence_with_llm(
    known_words: list[str],
    focus_words: list[str],
    current_sentence: str,
    settings: LlmTranslationSettings,
    required_words: list[str],
    template: str,
    known_ratio: float,
    min_len: int,
    max_len: int,
) -> str:
    known_pct = int(known_ratio * 100)
    focus_pct = 100 - known_pct
    prompt = (
        "Create one simple English practice sentence for a young learner.\n"
        "Rules:\n"
        "- Return only the English sentence.\n"
        f"- Between {min_len} and {max_len} words.\n"
        "- Include at least one focus word.\n"
        f"- Must include at least one required mistake word: {', '.join(required_words)}.\n"
        f"- About {known_pct}% of the words should come from known words when possible, and about {focus_pct}% from focus words.\n"
        "- Use easy grammar and natural English.\n"
        f"- Follow this sentence structure pattern: {template}\n\n"
        f"Known words: {', '.join(known_words[:30]) or ', '.join(COMMON_FILLER_WORDS)}\n"
        f"Focus words: {', '.join(focus_words)}\n"
        f"Previous sentence: {current_sentence}"
    )
    try:
        return generate_learning_text(prompt, settings)
    except ValueError:
        return _build_fallback_sentence(known_words, focus_words)


def _normalize_sentence_length(sentence: str, known_words: list[str], focus_words: list[str], required_words: list[str], min_len: int, max_len: int) -> str:
    cleaned = sentence.strip().replace("\n", " ")
    cleaned = re.sub(r"^[\"'""]+|[\"'""]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = cleaned.split()
    required_word_set = set(required_words)
    if min_len <= len(words) <= max_len and any(normalize_word(word) in required_word_set for word in words):
        return cleaned
    return _build_fallback_sentence(known_words, focus_words)


def _build_fallback_sentence(known_words: list[str], focus_words: list[str]) -> str:
    focus_word = focus_words[0] if focus_words else "word"
    return _pick_fallback_template(focus_word, known_words)


def translate_sentence_or_fallback(sentence: str, settings: LlmTranslationSettings) -> str:
    try:
        return translate_english_to_chinese(sentence, settings)
    except ValueError:
        return "AI 生成复习句"
