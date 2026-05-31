from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.word_translation import WordTranslation
from app.services.llm_translation import LlmTranslationSettings, translate_english_to_chinese
from app.utils import normalize_word, tokenize_words


def extract_unique_words(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    words: list[str] = []
    for text in texts:
        for word in tokenize_words(text):
            normalized = normalize_word(word)
            if normalized and normalized not in seen:
                seen.add(normalized)
                words.append(normalized)
    return words


def get_cached_word_translations(db: Session, user_id: UUID, words: list[str]) -> dict[str, str]:
    normalized_words = [normalize_word(word) for word in words]
    normalized_words = [word for word in normalized_words if word]
    if not normalized_words:
        return {}

    rows = db.scalars(
        select(WordTranslation).where(
            WordTranslation.user_id == user_id,
            WordTranslation.word.in_(normalized_words),
        )
    ).all()
    return {row.word: row.chinese_text for row in rows}


def ensure_word_translations(
    db: Session,
    user_id: UUID,
    words: list[str],
    settings: LlmTranslationSettings | None,
    course_id: UUID | None = None,
) -> dict[str, str]:
    normalized_words = []
    seen: set[str] = set()
    for word in words:
        normalized = normalize_word(word)
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_words.append(normalized)

    if not normalized_words:
        return {}

    cached_translations = get_cached_word_translations(db, user_id, normalized_words)
    translations = {
        word: translation
        for word, translation in cached_translations.items()
        if is_valid_chinese_translation(word, translation)
    }
    missing_words = [word for word in normalized_words if word not in translations]
    if settings is None:
        return translations

    for word in missing_words:
        try:
            chinese_text = sanitize_word_translation(translate_english_to_chinese(word, settings), source_word=word)
        except ValueError:
            chinese_text = ""
        if not chinese_text:
            continue

        translation = db.scalar(
            select(WordTranslation).where(
                WordTranslation.user_id == user_id,
                WordTranslation.word == word,
            )
        )
        if translation is None:
            translation = WordTranslation(
                user_id=user_id,
                course_id=course_id,
                word=word,
                chinese_text=chinese_text,
                source="llm",
            )
            db.add(translation)
        else:
            translation.course_id = translation.course_id or course_id
            translation.chinese_text = chinese_text
            translation.source = "llm"
        translations[word] = chinese_text

    if missing_words:
        db.flush()
    return translations


def sanitize_word_translation(value: str, source_word: str = "") -> str:
    text = value.strip()
    if not text:
        return ""
    for separator in ("\n", "，", "。", "！", "？", ";", "；"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    text = text[:40]
    return text if is_valid_chinese_translation(source_word, text) else ""


def is_valid_chinese_translation(source_word: str, translation: str) -> bool:
    text = translation.strip()
    if not text:
        return False
    if source_word and text.lower() == source_word.strip().lower():
        return False
    return any("\u4e00" <= character <= "\u9fff" for character in text)
