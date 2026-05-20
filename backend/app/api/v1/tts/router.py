from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.models.user import User
from app.schemas.tts import SpeechSynthesisRequest
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


@router.post("/speech")
def synthesize_speech(
    payload: SpeechSynthesisRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    voice = payload.voice or select_default_voice(payload.language)
    tts_settings = VolcengineTtsSettings(
        endpoint=payload.endpoint or app_settings.volcengine_tts_endpoint or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
        app_id=payload.app_id or app_settings.volcengine_tts_app_id,
        access_token=payload.access_token or app_settings.volcengine_tts_access_token,
        secret_key=payload.secret_key or app_settings.volcengine_tts_secret_key,
        resource_id=payload.resource_id or app_settings.volcengine_tts_resource_id or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
        model=payload.model or app_settings.volcengine_tts_model or DEFAULT_VOLCENGINE_TTS_MODEL,
        voice=voice,
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

