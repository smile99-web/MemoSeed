import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.listening_story import ListeningStory
from app.models.user import User
from app.services.listening_stories import (
    resolve_dialogue_b_voices,
    story_player_payload,
    story_summary,
    warm_story_audio,
)
from app.services.secure_model_settings import get_private_model_settings
from app.services.speech_asset_cache import build_volcengine_tts_settings
from app.services.volcengine_tts import (
    DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE,
    DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE,
    VolcengineTtsSettings,
)
from app.utils import string_setting

router = APIRouter()
logger = logging.getLogger("listening_router")


def _resolve_story_voices(db: Session, user_id: UUID) -> tuple[str, str, int]:
    """故事音频用和孩子日常学习一致的音色/语速（stored settings → env → 默认）。"""
    stored = get_private_model_settings(db, user_id)
    en_voice = (
        string_setting(stored, "ttsEnglishVoice")
        or app_settings.volcengine_tts_english_voice
        or DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE
    )
    zh_voice = (
        string_setting(stored, "ttsChineseVoice")
        or app_settings.volcengine_tts_chinese_voice
        or DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE
    )
    try:
        speech_rate = int(stored.get("ttsSpeedPreference", 0) or 0)
    except (TypeError, ValueError):
        speech_rate = 0
    return en_voice, zh_voice, speech_rate


def _tts_settings_factory(db: Session, user_id: UUID, speech_rate: int):
    stored = get_private_model_settings(db, user_id)

    def factory(voice: str, language: str) -> VolcengineTtsSettings:
        # build_volcengine_tts_settings enforces the custom-endpoint-requires-
        # own-key rule (SSRF/key exfiltration guard, same as /tts/speech).
        return build_volcengine_tts_settings(
            stored,
            voice=voice,
            language=language,
            speech_rate=speech_rate,
        )

    return factory


@router.get("/stories")
def list_stories(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """故事列表（不含句子正文，轻量）。"""
    stories = db.scalars(
        select(ListeningStory)
        .where(ListeningStory.user_id == current_user.id)
        .order_by(ListeningStory.created_at.asc())
    ).all()
    return {"stories": [story_summary(s) for s in stories]}


@router.get("/stories/{story_id}")
def get_story(
    story_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """整篇故事 + 每句英/中音频 URL（确定性 cache key）。"""
    story = db.scalar(
        select(ListeningStory).where(
            ListeningStory.id == story_id,
            ListeningStory.user_id == current_user.id,
        )
    )
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="故事不存在")
    en_voice, zh_voice, speech_rate = _resolve_story_voices(db, current_user.id)
    en_voice_b, zh_voice_b = resolve_dialogue_b_voices(en_voice, zh_voice)
    return story_player_payload(story, en_voice, zh_voice, speech_rate, en_voice_b, zh_voice_b)


@router.post("/stories/{story_id}/warm")
def warm_story(
    story_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """预生成整篇故事的 TTS 缓存，让逐句播放零等待。"""
    story = db.scalar(
        select(ListeningStory).where(
            ListeningStory.id == story_id,
            ListeningStory.user_id == current_user.id,
        )
    )
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="故事不存在")
    en_voice, zh_voice, speech_rate = _resolve_story_voices(db, current_user.id)
    en_voice_b, zh_voice_b = resolve_dialogue_b_voices(en_voice, zh_voice)
    stats = warm_story_audio(
        story,
        en_voice,
        zh_voice,
        speech_rate,
        _tts_settings_factory(db, current_user.id, speech_rate),
        en_voice_b,
        zh_voice_b,
    )
    return stats
