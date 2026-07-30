"""生成听力故事（10 篇）并预热全部 TTS 音频。

用法（VPS 上）：
    cd /opt/MemoSeed/backend
    .venv/bin/python -m scripts.generate_listening_stories --username 轩轩

可选：
    --count 10            生成篇数（默认 10）
    --warm-only           跳过生成，只对已有故事补预热音频
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import settings as app_settings
from app.db.session import SessionLocal
from app.models.listening_story import ListeningStory
from app.models.user import User
from app.services.listening_stories import generate_stories, warm_story_audio
from app.services.llm_translation import DEFAULT_LLM_TRANSLATION_SETTINGS, LlmTranslationSettings
from app.services.secure_model_settings import get_private_model_settings
from app.services.volcengine_tts import (
    DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE,
    DEFAULT_VOLCENGINE_TTS_ENDPOINT,
    DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE,
    DEFAULT_VOLCENGINE_TTS_MODEL,
    DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
    VolcengineTtsSettings,
)
from app.utils import string_setting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("generate_listening_stories")


def resolve_user(db, username: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        names = [u.username for u in db.scalars(select(User)).all()]
        raise SystemExit(f"用户 {username!r} 不存在。现有用户: {names}")
    return user


def resolve_llm_settings(db, user_id, model_override: str | None = None) -> LlmTranslationSettings:
    stored = get_private_model_settings(db, user_id)
    base_url = str(
        string_setting(stored, "llmBaseUrl")
        or app_settings.ai_base_url
        or DEFAULT_LLM_TRANSLATION_SETTINGS.base_url
    )
    model = model_override or str(
        string_setting(stored, "llmModel")
        or app_settings.ai_model
        or DEFAULT_LLM_TRANSLATION_SETTINGS.model
    )
    # DeepSeek 推理模型（如 deepseek-v4-flash）会先烧几千个 reasoning token 才输出，
    # 稳定超过 generate_learning_text 的 30s 读超时 → 批量生成必然全部超时。
    # 与 MindReview preferNonReasoning 同一处置：非交互批量任务强制走 deepseek-chat。
    if not model_override and "deepseek.com" in base_url.lower() and model != "deepseek-chat":
        logger.warning("配置的模型 %s 可能是推理模型，批量生成改用 deepseek-chat（可用 --model 覆盖）", model)
        model = "deepseek-chat"
    return LlmTranslationSettings(
        provider=str(
            string_setting(stored, "llmProvider")
            or app_settings.ai_provider
            or DEFAULT_LLM_TRANSLATION_SETTINGS.provider
        ),
        base_url=base_url,
        model=model,
        api_key=string_setting(stored, "llmApiKey") or app_settings.ai_api_key,
    )


def resolve_voices(db, user_id) -> tuple[str, str, int]:
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


def tts_settings_factory(db, user_id, speech_rate: int):
    stored = get_private_model_settings(db, user_id)

    def factory(voice: str, language: str) -> VolcengineTtsSettings:
        return VolcengineTtsSettings(
            endpoint=string_setting(stored, "volcengineTtsEndpoint")
            or app_settings.volcengine_tts_endpoint
            or DEFAULT_VOLCENGINE_TTS_ENDPOINT,
            api_key=string_setting(stored, "volcengineTtsApiKey") or app_settings.volcengine_tts_api_key,
            resource_id=string_setting(stored, "volcengineTtsResourceId")
            or app_settings.volcengine_tts_resource_id
            or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID,
            model=string_setting(stored, "volcengineTtsModel")
            or app_settings.volcengine_tts_model
            or DEFAULT_VOLCENGINE_TTS_MODEL,
            voice=voice,
            language=language,
            speech_rate=speech_rate,
        )

    return factory


def main() -> None:
    parser = argparse.ArgumentParser(description="生成听力故事并预热 TTS 音频")
    parser.add_argument("--username", required=True, help="孩子账号用户名")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--model", default=None, help="覆盖 LLM 模型（默认用账号配置；DeepSeek 推理模型自动换成 deepseek-chat）")
    parser.add_argument("--warm-only", action="store_true", help="只对已有故事补预热音频")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = resolve_user(db, args.username)
        logger.info("目标用户: %s (%s)", user.username, user.id)

        if not args.warm_only:
            llm_settings = resolve_llm_settings(db, user.id, args.model)
            logger.info("LLM: provider=%s model=%s base_url=%s",
                        llm_settings.provider, llm_settings.model, llm_settings.base_url)
            stories = generate_stories(db, user.id, llm_settings, count=args.count)
            logger.info("生成完成 %d 篇:", len(stories))
            for s in stories:
                logger.info("  - [%s] %s（%d 句）", s.theme, s.title, len(s.sentences))

        en_voice, zh_voice, speech_rate = resolve_voices(db, user.id)
        logger.info("TTS: en=%s zh=%s rate=%d", en_voice, zh_voice, speech_rate)
        factory = tts_settings_factory(db, user.id, speech_rate)

        all_stories = db.scalars(
            select(ListeningStory)
            .where(ListeningStory.user_id == user.id)
            .order_by(ListeningStory.created_at.asc())
        ).all()
        totals = {"cached": 0, "generated": 0, "failed": 0, "total": 0}
        for story in all_stories:
            stats = warm_story_audio(story, en_voice, zh_voice, speech_rate, factory)
            for key in totals:
                totals[key] += stats[key]
            logger.info(
                "  预热 [%s]: cached=%d generated=%d failed=%d",
                story.title, stats["cached"], stats["generated"], stats["failed"],
            )
        logger.info(
            "全部完成: 故事 %d 篇, 音频 total=%d cached=%d generated=%d failed=%d",
            len(all_stories), totals["total"], totals["cached"], totals["generated"], totals["failed"],
        )
        if totals["failed"]:
            logger.warning("有 %d 条音频生成失败，重跑本脚本可补齐（已缓存的不会重复生成）", totals["failed"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
