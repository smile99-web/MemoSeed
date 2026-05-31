import logging
from dataclasses import dataclass
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models.learning_item import LearningItem
from app.models.speech_asset import SpeechAsset
from app.services.tts_cache import build_cache_key, get_cache_url, get_cached_audio
from app.services.volcengine_tts import (
    AUDIO_SUFFIX,
    DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE,
    DEFAULT_VOLCENGINE_TTS_ENDPOINT,
    DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE,
    DEFAULT_VOLCENGINE_TTS_MODEL,
    DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
    VolcengineTtsSettings,
    synthesize_volcengine_speech,
)
from app.services.word_translation_cache import get_cached_word_translations
from app.utils import string_setting, tokenize_words

logger = logging.getLogger("speech_asset_cache")


@dataclass(frozen=True)
class SpeechTarget:
    text: str
    language: str
    voice: str
    speech_rate: int = 0


def select_cached_voice(language: str, stored_settings: dict[str, object] | None = None) -> str:
    stored_settings = stored_settings or {}
    if language.lower().startswith("en"):
        return string_setting(stored_settings, "ttsEnglishVoice") or app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    return string_setting(stored_settings, "ttsChineseVoice") or app_settings.volcengine_tts_chinese_voice or DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE


def build_volcengine_tts_settings(
    stored_settings: dict[str, object] | None,
    *,
    voice: str,
    language: str,
    speech_rate: int,
) -> VolcengineTtsSettings:
    stored_settings = stored_settings or {}
    return VolcengineTtsSettings(
        endpoint=string_setting(stored_settings, "volcengineTtsEndpoint") or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
        api_key=string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
        resource_id=string_setting(stored_settings, "volcengineTtsResourceId") or app_settings.volcengine_tts_resource_id or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
        model=string_setting(stored_settings, "volcengineTtsModel") or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
        voice=voice,
        language=language,
        speech_rate=speech_rate,
    )


def upsert_speech_asset(
    db: Session,
    *,
    user_id: UUID,
    course_id: UUID | None,
    target: SpeechTarget,
    provider: str = "volcengine",
    suffix: str = AUDIO_SUFFIX,
    cached: bool,
) -> SpeechAsset:
    normalized_text = target.text.strip()
    text_hash = build_cache_key(normalized_text, target.voice, target.speech_rate)
    audio_url = get_cache_url(normalized_text, target.voice, target.speech_rate, suffix=suffix)
    existing = db.scalar(
        select(SpeechAsset).where(
            SpeechAsset.user_id == user_id,
            SpeechAsset.provider == provider,
            SpeechAsset.language == target.language,
            SpeechAsset.voice == target.voice,
            SpeechAsset.speech_rate == target.speech_rate,
            SpeechAsset.text_hash == text_hash,
        )
    )
    if existing is not None:
        existing.course_id = existing.course_id or course_id
        existing.text = normalized_text
        existing.audio_url = audio_url
        existing.suffix = suffix
        existing.cached = cached
        return existing

    speech_asset = SpeechAsset(
        user_id=user_id,
        course_id=course_id,
        provider=provider,
        language=target.language,
        voice=target.voice,
        speech_rate=target.speech_rate,
        text_hash=text_hash,
        text=normalized_text,
        audio_url=audio_url,
        suffix=suffix,
        cached=cached,
    )
    db.add(speech_asset)
    return speech_asset


def ensure_volcengine_speech_asset(
    db: Session,
    *,
    user_id: UUID,
    course_id: UUID | None,
    target: SpeechTarget,
    stored_settings: dict[str, object] | None,
    allow_synthesis: bool = True,
) -> tuple[SpeechAsset | None, bool]:
    normalized_text = target.text.strip()
    if not normalized_text:
        return None, False

    cached = get_cached_audio(normalized_text, target.voice, target.speech_rate, suffix=AUDIO_SUFFIX) is not None
    synthesis_failed = False
    if not cached:
        tts_settings = build_volcengine_tts_settings(
            stored_settings,
            voice=target.voice,
            language=target.language,
            speech_rate=target.speech_rate,
        )
        if not tts_settings.api_key:
            logger.info("Skip speech pre-cache without Volcengine X-Api-Key: language=%s text=%s", target.language, normalized_text[:40])
        elif allow_synthesis:
            try:
                synthesize_volcengine_speech(normalized_text, tts_settings)
                cached = True
            except ValueError:
                synthesis_failed = True
                logger.warning("Failed to pre-cache speech asset: language=%s text=%s", target.language, normalized_text[:80], exc_info=True)

    return upsert_speech_asset(
        db,
        user_id=user_id,
        course_id=course_id,
        target=target,
        provider="volcengine",
        suffix=AUDIO_SUFFIX,
        cached=cached,
    ), synthesis_failed


def precache_learning_speech_assets(
    db: Session,
    *,
    user_id: UUID,
    course_id: UUID,
    learning_items: list[LearningItem],
    stored_settings: dict[str, object] | None,
    on_progress: Callable[[int, int, SpeechTarget, bool], None] | None = None,
) -> dict[str, int]:
    targets = build_learning_speech_targets(db, user_id=user_id, learning_items=learning_items, stored_settings=stored_settings)

    generated_or_cached = 0
    missing = 0
    synthesis_failures = 0
    for index, target in enumerate(targets, start=1):
        speech_asset, synthesis_failed = ensure_volcengine_speech_asset(
            db,
            user_id=user_id,
            course_id=course_id,
            target=target,
            stored_settings=stored_settings,
            allow_synthesis=synthesis_failures < 3,
        )
        if speech_asset is None:
            continue
        if synthesis_failed:
            synthesis_failures += 1
        if speech_asset.cached:
            generated_or_cached += 1
        else:
            missing += 1
        if on_progress is not None:
            on_progress(index, len(targets), target, speech_asset.cached)

    db.flush()
    return {"total": len(targets), "cached": generated_or_cached, "missing": missing, "synthesis_failures": synthesis_failures}


def build_learning_speech_targets(
    db: Session,
    *,
    user_id: UUID,
    learning_items: list[LearningItem],
    stored_settings: dict[str, object] | None,
) -> list[SpeechTarget]:
    english_voice = select_cached_voice("en-US", stored_settings)
    chinese_voice = select_cached_voice("zh-CN", stored_settings)
    english_words = sorted({word for item in learning_items for word in tokenize_words(item.english_text)})
    word_translations = get_cached_word_translations(db, user_id, english_words) if english_words else {}

    targets: dict[tuple[str, str, str, int], SpeechTarget] = {}

    def add_target(text: str, language: str, voice: str, speech_rate: int = 0) -> None:
        normalized_text = text.strip()
        if not normalized_text:
            return
        key = (normalized_text, language, voice, speech_rate)
        targets[key] = SpeechTarget(
            text=normalized_text,
            language=language,
            voice=voice,
            speech_rate=speech_rate,
        )

    for item in learning_items:
        add_target(item.english_text, "en-US", english_voice)
        add_target(item.chinese_text, "zh-CN", chinese_voice)
        if item.item_type in {"word", "phrase"}:
            add_target(item.english_text, "en-US", english_voice)
            add_target(item.chinese_text, "zh-CN", chinese_voice)

    for word in english_words:
        add_target(word, "en-US", english_voice)
        chinese_text = word_translations.get(word)
        if chinese_text:
            add_target(chinese_text, "zh-CN", chinese_voice)

    return list(targets.values())
