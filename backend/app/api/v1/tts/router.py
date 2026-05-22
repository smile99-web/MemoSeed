import json
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.tts import KokoroSpeechSynthesisRequest, SpeechSynthesisRequest
from app.services.secure_model_settings import get_private_model_settings
from app.services.volcengine_tts import (
    DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE,
    DEFAULT_VOLCENGINE_TTS_ENDPOINT,
    DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE,
    DEFAULT_VOLCENGINE_TTS_MODEL,
    DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
    VolcengineTtsSettings,
    synthesize_volcengine_speech,
)

router = APIRouter()
LOCAL_KOKORO_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
    tts_settings = VolcengineTtsSettings(
        endpoint=payload.endpoint or _string_setting(stored_settings, "volcengineTtsEndpoint") or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
        api_key=payload.x_api_key or _string_setting(stored_settings, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
        resource_id=payload.resource_id
        or _string_setting(stored_settings, "volcengineTtsResourceId")
        or app_settings.volcengine_tts_resource_id
        or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
        model=payload.model or _string_setting(stored_settings, "volcengineTtsModel") or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
        voice=voice,
        language=payload.language,
        speech_rate=payload.speech_rate or 0,
    )

    try:
        audio = synthesize_volcengine_speech(payload.text, tts_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return Response(content=audio, media_type="audio/mpeg")


def select_default_voice(language: str | None, stored_settings: dict[str, object] | None = None) -> str:
    stored_settings = stored_settings or {}
    if language and language.lower().startswith("en"):
        return _string_setting(stored_settings, "ttsEnglishVoice") or app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    return _string_setting(stored_settings, "ttsChineseVoice") or app_settings.volcengine_tts_chinese_voice or DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE


def _string_setting(settings: dict[str, object], key: str) -> str | None:
    value = settings.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

