import json
import logging
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.course import Course
from app.models.learning_item import LearningItem
from app.models.tts_usage_log import TtsUsageLog
from app.models.user import User
from app.schemas.tts import (
    CachedSpeechRequest,
    CosyVoiceSpeechSynthesisRequest,
    KokoroSpeechSynthesisRequest,
    PhonicsDeckItem,
    PhonicsDeckResponse,
    PrefetchCourseAudioRequest,
    PrefetchCourseAudioResponse,
    SpeechSynthesisRequest,
)
from app.services.cosyvoice_tts import (
    COSYVOICE_AUDIO_SUFFIX,
    DEFAULT_COSYVOICE_BASE_URL,
    CosyVoiceTtsSettings,
    synthesize_cosyvoice_speech,
)
from app.services.phonics_deck import (
    get_phonics_phonemes,
    get_phonics_synth_map,
)
from app.services.secure_model_settings import get_private_model_settings
from app.services.speech_asset_cache import SpeechTarget, upsert_speech_asset
from app.services.tts_cache import (
    build_cache_key,
    get_cache_url,
    get_cached_audio,
    resolve_cached_file,
)
from app.utils import string_setting, tokenize_words
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

router = APIRouter()
logger = logging.getLogger("tts_router")
LOCAL_KOKORO_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
COSYVOICE_HOSTS = LOCAL_KOKORO_HOSTS | {"cosyvoice", "memoseed-cosyvoice"}


@router.post("/cosyvoice/speech")
def synthesize_cosyvoice_speech_endpoint(
    payload: CosyVoiceSpeechSynthesisRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    stored_settings = get_private_model_settings(db, current_user.id)
    base_url = _strip_url(payload.api_url) or string_setting(stored_settings, "cosyvoiceBaseUrl") or app_settings.cosyvoice_base_url or DEFAULT_COSYVOICE_BASE_URL
    base_url = _resolve_cosyvoice_base_url(base_url)
    speaker = payload.speaker.strip() or string_setting(stored_settings, "cosyvoiceSpeaker") or "中文女"

    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CosyVoice API URL is invalid")
    if parsed_url.hostname not in COSYVOICE_HOSTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CosyVoice hostname '{parsed_url.hostname}' not allowed; must be localhost or cosyvoice container")

    tts_settings = CosyVoiceTtsSettings(base_url=base_url, speaker=speaker)

    cache_hit = get_cached_audio(payload.text, speaker, 0, suffix=COSYVOICE_AUDIO_SUFFIX) is not None
    try:
        audio = synthesize_cosyvoice_speech(payload.text, tts_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    _log_tts_usage(db, current_user.id, payload.text, speaker, 0, "cosyvoice", cache_hit)
    return Response(content=audio, media_type="audio/wav")


@router.post("/kokoro/speech")
def synthesize_kokoro_speech(
    payload: KokoroSpeechSynthesisRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    parsed_url = urlparse(payload.api_url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kokoro API URL is invalid")
    if parsed_url.hostname not in LOCAL_KOKORO_HOSTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kokoro API URL must point to localhost")
    # SSRF hardening: reject any client-supplied URL that carries
    # credentials, a path, query, or fragment. The path is appended
    # server-side as /v1/audio/speech, so allowing user paths here
    # would let an authenticated user probe other services running on
    # the same container (e.g. http://kokoro:3000/admin). The hostname
    # allowlist above stops cross-container probing; this stops
    # same-container probing on non-standard paths.
    if parsed_url.username or parsed_url.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kokoro API URL must not contain credentials")
    if parsed_url.path and parsed_url.path not in ("", "/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kokoro API URL must not contain a path")
    if parsed_url.query or parsed_url.fragment:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kokoro API URL must not contain query/fragment")

    api_url = payload.api_url.strip().rstrip("/")
    request_body: dict[str, object] = {
        "model": payload.model,
        "input": payload.text,
        "voice": payload.voice,
        "response_format": "mp3",
    }
    if payload.speed is not None:
        request_body["speed"] = payload.speed

    request = Request(
        f"{api_url}/v1/audio/speech",
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            audio = response.read()
    except HTTPError as exc:
        exc.read()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Kokoro TTS failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Kokoro TTS failed: {exc}") from exc

    if not audio:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Kokoro TTS returned empty audio")

    return Response(content=audio, media_type="audio/mpeg")


@router.post("/speech")
def synthesize_speech(
    payload: SpeechSynthesisRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    stored_settings = get_private_model_settings(db, current_user.id)
    voice = payload.voice or select_default_voice(payload.language, stored_settings)
    speech_rate = payload.speech_rate if payload.speech_rate is not None else 0
    # Apply user speed preference if no explicit speech_rate given
    if payload.speech_rate is None:
        user_speed_pref = stored_settings.get("ttsSpeedPreference", 0)
        try:
            speech_rate = int(user_speed_pref)
        except (ValueError, TypeError):
            speech_rate = 0

    tts_settings = VolcengineTtsSettings(
        endpoint=payload.endpoint or string_setting(stored_settings, "volcengineTtsEndpoint") or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
        api_key=payload.x_api_key or string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
        resource_id=payload.resource_id
        or string_setting(stored_settings, "volcengineTtsResourceId")
        or app_settings.volcengine_tts_resource_id
        or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
        model=payload.model or string_setting(stored_settings, "volcengineTtsModel") or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
        voice=voice,
        language=payload.language,
        speech_rate=speech_rate,
    )

    cache_hit = get_cached_audio(payload.text, voice, speech_rate, suffix=AUDIO_SUFFIX) is not None

    try:
        audio = synthesize_volcengine_speech(payload.text, tts_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    _record_speech_asset(db, current_user.id, None, payload.text, payload.language or "", voice, speech_rate, True)
    _log_tts_usage(db, current_user.id, payload.text, voice, speech_rate, "volcengine", cache_hit)
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/cached/{hash_with_ext:path}")
def serve_cached_audio(hash_with_ext: str) -> Response:
    audio = resolve_cached_file(hash_with_ext)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cached audio not found")
    media_type = "audio/wav" if hash_with_ext.endswith(".wav") else "audio/mpeg"
    return Response(content=audio, media_type=media_type)


@router.post("/prefetch-course-audio", response_model=PrefetchCourseAudioResponse)
def prefetch_course_audio(
    payload: PrefetchCourseAudioRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PrefetchCourseAudioResponse:
    from uuid import UUID

    stored_settings = get_private_model_settings(db, current_user.id)
    speech_rate = payload.speech_rate
    # Try to parse course_id as UUID
    try:
        course_uuid = UUID(payload.course_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid course_id format")

    course = db.scalar(select(Course).where(Course.id == course_uuid, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    learning_items = db.scalars(
        select(LearningItem).where(LearningItem.course_id == course_uuid)
    ).all()

    unique_words: set[str] = set()
    for item in learning_items:
        for word in tokenize_words(item.english_text):
            unique_words.add(word.lower())

    words_map: dict[str, str] = {}
    cache_hits = 0
    cache_misses = 0

    voice = payload.voice or select_default_voice(payload.language, stored_settings)

    for word in sorted(unique_words):
        cached = get_cached_audio(word, voice, speech_rate, suffix=AUDIO_SUFFIX)
        if cached is not None:
            cache_hits += 1
        else:
            cache_misses += 1
            # Pre-generate audio for this word
            tts_settings = VolcengineTtsSettings(
                endpoint=string_setting(stored_settings, "volcengineTtsEndpoint") or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
                api_key=string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
                resource_id=string_setting(stored_settings, "volcengineTtsResourceId") or app_settings.volcengine_tts_resource_id or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
                model=string_setting(stored_settings, "volcengineTtsModel") or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
                voice=voice,
                language=payload.language,
                speech_rate=speech_rate,
            )
            try:
                synthesize_volcengine_speech(word, tts_settings)
            except Exception:
                logger.warning("Failed to prefetch audio for word: %s", word)
        _record_speech_asset(db, current_user.id, course_uuid, word, payload.language, voice, speech_rate, get_cached_audio(word, voice, speech_rate, suffix=AUDIO_SUFFIX) is not None)
        words_map[word] = get_cache_url(word, voice, speech_rate, suffix=AUDIO_SUFFIX)

    return PrefetchCourseAudioResponse(
        course_id=payload.course_id,
        words=words_map,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
    )


@router.post("/ensure-cached")
def ensure_cached_audio(
    payload: CachedSpeechRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    voice = payload.voice
    speech_rate = payload.speech_rate
    suffix = payload.suffix

    cached = get_cached_audio(payload.text, voice, speech_rate, suffix=suffix)
    if cached is not None:
        return {"cached": True, "url": get_cache_url(payload.text, voice, speech_rate, suffix=suffix)}

    return {"cached": False, "url": get_cache_url(payload.text, voice, speech_rate, suffix=suffix)}


@router.get("/phonics-deck", response_model=PhonicsDeckResponse)
def get_phonics_deck(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PhonicsDeckResponse:
    stored_settings = get_private_model_settings(db, current_user.id)
    voice = string_setting(stored_settings, "ttsEnglishVoice") or app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    speech_rate = 0
    display_map = get_phonics_phonemes()
    synth_map = get_phonics_synth_map()

    phonemes: list[PhonicsDeckItem] = []
    for phoneme_key, synth_text in synth_map.items():
        url = get_cache_url(synth_text, voice, speech_rate, suffix=AUDIO_SUFFIX)
        phonemes.append(PhonicsDeckItem(
            phoneme_key=phoneme_key,
            display_label=display_map.get(phoneme_key, phoneme_key),
            synth_text=synth_text,
            audio_url=url,
        ))

    return PhonicsDeckResponse(phonemes=phonemes)


@router.post("/phonics-deck/generate")
def generate_phonics_deck_audio(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    stored_settings = get_private_model_settings(db, current_user.id)
    voice = string_setting(stored_settings, "ttsEnglishVoice") or app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    speech_rate = 0
    synth_map = get_phonics_synth_map()

    generated = 0
    cached_count = 0
    errors = 0

    for phoneme_key, synth_text in synth_map.items():
        cached = get_cached_audio(synth_text, voice, speech_rate, suffix=AUDIO_SUFFIX)
        if cached is not None:
            cached_count += 1
            continue

        tts_settings = VolcengineTtsSettings(
            endpoint=string_setting(stored_settings, "volcengineTtsEndpoint") or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
            api_key=string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
            resource_id=string_setting(stored_settings, "volcengineTtsResourceId") or app_settings.volcengine_tts_resource_id or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
            model=string_setting(stored_settings, "volcengineTtsModel") or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
            voice=voice,
            language="en-US",
            speech_rate=speech_rate,
        )
        try:
            synthesize_volcengine_speech(synth_text, tts_settings)
            generated += 1
        except Exception:
            logger.warning("Failed to generate phonics audio for phoneme: %s", phoneme_key)
            errors += 1

    return {"generated": generated, "cached": cached_count, "errors": errors, "total": len(synth_map)}


def _log_tts_usage(db: Session, user_id: object, text: str, voice: str, speech_rate: int, provider: str, cache_hit: bool) -> None:
    try:
        log_entry = TtsUsageLog(
            user_id=user_id,
            text_hash=build_cache_key(text, voice, speech_rate),
            text_length=len(text),
            voice=voice,
            speech_rate=speech_rate,
            provider=provider,
            cached=cache_hit,
        )
        db.add(log_entry)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to log TTS usage", exc_info=True)


def _record_speech_asset(
    db: Session,
    user_id: object,
    course_id: object | None,
    text: str,
    language: str,
    voice: str,
    speech_rate: int,
    cached: bool,
) -> None:
    try:
        upsert_speech_asset(
            db,
            user_id=user_id,  # type: ignore[arg-type]
            course_id=course_id,  # type: ignore[arg-type]
            target=SpeechTarget(
                text=text,
                language=language or "unknown",
                voice=voice,
                speech_rate=speech_rate,
            ),
            provider="volcengine",
            suffix=AUDIO_SUFFIX,
            cached=cached,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to record speech asset", exc_info=True)


def select_default_voice(language: str | None, stored_settings: dict[str, object] | None = None) -> str:
    stored_settings = stored_settings or {}
    if language and language.lower().startswith("en"):
        return string_setting(stored_settings, "ttsEnglishVoice") or app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    return string_setting(stored_settings, "ttsChineseVoice") or app_settings.volcengine_tts_chinese_voice or DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE


def _strip_url(value: str) -> str | None:
    stripped = (value or "").strip().rstrip("/")
    return stripped if stripped else None


def _resolve_cosyvoice_base_url(base_url: str) -> str:
    configured_base_url = _strip_url(app_settings.cosyvoice_base_url or "")
    if not configured_base_url:
        return base_url

    parsed_url = urlparse(base_url)
    configured_url = urlparse(configured_base_url)
    if (
        parsed_url.hostname in LOCAL_KOKORO_HOSTS
        and configured_url.hostname in COSYVOICE_HOSTS
        and configured_url.hostname not in LOCAL_KOKORO_HOSTS
    ):
        return configured_base_url

    return base_url

