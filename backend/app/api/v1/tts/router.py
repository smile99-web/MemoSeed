import json
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.models.user import User
from app.schemas.tts import KokoroSpeechSynthesisRequest, SpeechSynthesisRequest
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
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Kokoro TTS failed: HTTP {exc.code} {error_body}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Kokoro TTS failed: {exc}") from exc

    if not audio:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Kokoro TTS returned empty audio")

    return Response(content=audio, media_type="audio/mpeg")


@router.post("/speech")
def synthesize_speech(
    payload: SpeechSynthesisRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    voice = payload.voice or select_default_voice(payload.language)
    tts_settings = VolcengineTtsSettings(
        endpoint=payload.endpoint or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
        api_key=payload.x_api_key or app_settings.volcengine_tts_api_key,
        resource_id=payload.resource_id or app_settings.volcengine_tts_resource_id or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
        model=payload.model or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
        voice=voice,
        language=payload.language,
        speech_rate=payload.speech_rate or 0,
    )

    try:
        audio = synthesize_volcengine_speech(payload.text, tts_settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return Response(content=audio, media_type="audio/mpeg")


def select_default_voice(language: str | None) -> str:
    if language and language.lower().startswith("en"):
        return app_settings.volcengine_tts_english_voice or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    return app_settings.volcengine_tts_chinese_voice or DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE

