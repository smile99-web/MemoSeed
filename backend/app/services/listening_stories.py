"""听力故事模块：用词池、LLM 故事生成、内容校验、播放载荷与音频预热。

设计要点：
- 故事用词 = 孩子平时练习的单词（word 类 learning_items，按记忆强度排序，
  最熟悉的优先），保证"i+0"可理解输入——听力训练不是生词突袭。
- 每篇 16-22 个短句（3-8 词/句），英文慢速朗读约 2-3 分钟。
- LLM 输出严格 JSON，经 validate_story 校验（句数/句长/中文非空）才入库；
  不合格整篇丢弃重试，绝不给孩子放半成品。
- 音频 URL 用 tts_cache 的确定性 cache key（text|voice|rate 的 sha256），
  预热（warm）后播放端 100% 命中缓存，逐句零等待。
"""

import json
import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models.learning_item import LearningItem
from app.models.listening_story import ListeningStory
from app.models.word_memory_state import WordMemoryState
from app.services.llm_translation import LlmTranslationSettings, generate_learning_text
from app.services.tts_cache import get_cache_url, get_cached_audio
from app.services.volcengine_tts import AUDIO_SUFFIX, VolcengineTtsSettings, synthesize_volcengine_speech

logger = logging.getLogger("listening_stories")

SENTENCES_PER_STORY_MIN = 16
SENTENCES_PER_STORY_MAX = 22
MAX_WORDS_PER_SENTENCE = 12
# 日常对话：一问一答，A/B 轮流说话（speaker 字段），轮数比故事句数少
# （每轮播放 英文×2 + 中文×1，与故事一致）。
DIALOGUE_THEME = "日常对话"
DIALOGUE_TURNS_MIN = 10
DIALOGUE_TURNS_MAX = 16
MAX_WORDS_PER_DIALOGUE_TURN = 10
# B 角色音色（A 沿用孩子账号配置）。都从设置页音色白名单中选，保证可用。
DIALOGUE_B_ENGLISH_VOICE = "en_male_tim_uranus_bigtts"
DIALOGUE_B_CHINESE_VOICE = "zh_female_xiaohe_uranus_bigtts"
# A 音色恰好等于 B 默认值时的备用（避免两人同声）。
DIALOGUE_B_ENGLISH_FALLBACKS = [
    "en_female_dacey_uranus_bigtts",
    "en_female_stokie_uranus_bigtts",
]
DIALOGUE_B_CHINESE_FALLBACKS = [
    "zh_female_vv_uranus_bigtts",
    "zh_male_liufei_uranus_bigtts",
]
STORY_THEMES = [
    "在学校的一天",
    "我的家庭",
    "去公园玩",
    "可爱的小动物",
    "好吃的食物",
    "我的好朋友",
    "快乐的周末",
    "画画和颜色",
    "帮爸爸妈妈做事",
    "天气和四季",
]


def get_practiced_words(db: Session, user_id: UUID, limit: int = 120) -> list[dict[str, str]]:
    """孩子平时练习的单词（word 类条目 + 中文意思），熟悉的排前面。

    排序：有 WordMemoryState 的词按 memory_strength 降序（最熟最先给 LLM），
    没有 state 的词（新导入还没练过）排最后——故事应以"练过"的词为主。
    """
    rows = db.execute(
        select(
            LearningItem.english_text,
            LearningItem.chinese_text,
            WordMemoryState.memory_strength,
        )
        .outerjoin(
            WordMemoryState,
            (WordMemoryState.learning_item_id == LearningItem.id)
            & (WordMemoryState.user_id == LearningItem.user_id),
        )
        .where(
            LearningItem.user_id == user_id,
            LearningItem.item_type == "word",
        )
        .order_by(WordMemoryState.memory_strength.desc().nullslast(), LearningItem.sort_order.asc())
        .limit(limit)
    ).all()
    words: list[dict[str, str]] = []
    seen: set[str] = set()
    for english, chinese, _strength in rows:
        key = english.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        words.append({"word": english.strip(), "zh": (chinese or "").strip()})
    return words


def build_story_prompt(words: list[dict[str, str]], theme: str) -> str:
    """构造故事生成 prompt。只给词表（不带中文，避免 LLM 把中文抄进英文句）。"""
    word_list = ", ".join(w["word"] for w in words[:100])
    return (
        "Write a very simple English story for a 7-year-old Chinese child learning English.\n"
        f"Theme: {theme}\n"
        "Rules:\n"
        f"- {SENTENCES_PER_STORY_MIN} to {SENTENCES_PER_STORY_MAX} sentences.\n"
        "- Each sentence must be 3 to 8 words, simple present tense, easy grammar.\n"
        "- Use ONLY very common, high-frequency words. Prefer these words the child has practiced: "
        f"{word_list}\n"
        "- The story must have a simple beginning, middle and happy ending.\n"
        "- Provide a simple Chinese translation for EVERY sentence (natural, short).\n"
        "- Also provide a short English title (2-5 words) and its Chinese translation.\n"
        'Output STRICT JSON only, no markdown, no extra text:\n'
        '{"title_en": "...", "title_zh": "...", "sentences": [{"en": "...", "zh": "..."}, ...]}'
    )


def parse_story_json(raw: str) -> dict:
    """从 LLM 输出解析故事 JSON。容忍 ```json 代码围栏和首尾废话。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def validate_story(data: dict) -> dict | None:
    """校验并归一化 LLM 故事；不合格返回 None（整篇丢弃）。"""
    if not isinstance(data, dict):
        return None
    title_en = str(data.get("title_en") or "").strip()
    title_zh = str(data.get("title_zh") or "").strip()
    raw_sentences = data.get("sentences")
    if not title_en or not isinstance(raw_sentences, list):
        return None

    sentences: list[dict[str, str]] = []
    for item in raw_sentences:
        if not isinstance(item, dict):
            return None
        en = re.sub(r"\s+", " ", str(item.get("en") or "").strip())
        zh = re.sub(r"\s+", " ", str(item.get("zh") or "").strip())
        if not en or not zh:
            return None
        # 必须真的是英文句（含字母、不含中文字符）
        if not re.search(r"[A-Za-z]", en) or re.search(r"[一-鿿]", en):
            return None
        word_count = len(en.split())
        if word_count < 2 or word_count > MAX_WORDS_PER_SENTENCE:
            return None
        sentences.append({"en": en, "zh": zh})

    if not (SENTENCES_PER_STORY_MIN <= len(sentences) <= SENTENCES_PER_STORY_MAX):
        return None
    title = title_en if not title_zh else f"{title_en} · {title_zh}"
    return {"title": title[:200], "sentences": sentences}


def is_dialogue_sentences(sentences: list[dict]) -> bool:
    """带 speaker 字段的条目 = 对话（A/B 一问一答），否则是普通故事。"""
    return any(isinstance(s, dict) and s.get("speaker") in ("A", "B") for s in sentences)


def validate_dialogue(data: dict) -> dict | None:
    """校验并归一化日常对话；不合格返回 None（整篇丢弃）。

    与故事的差异：每轮带 speaker（"A"/"B"），A 先开口、严格轮流；
    单轮可以更短（1-10 词，对话短句如 "Sure!" 合法）；轮数 10-16。
    """
    if not isinstance(data, dict):
        return None
    title_en = str(data.get("title_en") or "").strip()
    title_zh = str(data.get("title_zh") or "").strip()
    raw_sentences = data.get("sentences")
    if not title_en or not isinstance(raw_sentences, list):
        return None

    sentences: list[dict[str, str]] = []
    expected_speaker = "A"
    for item in raw_sentences:
        if not isinstance(item, dict):
            return None
        speaker = str(item.get("speaker") or "").strip().upper()
        en = re.sub(r"\s+", " ", str(item.get("en") or "").strip())
        zh = re.sub(r"\s+", " ", str(item.get("zh") or "").strip())
        if speaker not in ("A", "B") or not en or not zh:
            return None
        if speaker != expected_speaker:
            return None  # 必须 A 先开口且严格轮流
        if not re.search(r"[A-Za-z]", en) or re.search(r"[一-鿿]", en):
            return None
        word_count = len(en.split())
        if word_count < 1 or word_count > MAX_WORDS_PER_DIALOGUE_TURN:
            return None
        sentences.append({"speaker": speaker, "en": en, "zh": zh})
        expected_speaker = "B" if expected_speaker == "A" else "A"

    if not (DIALOGUE_TURNS_MIN <= len(sentences) <= DIALOGUE_TURNS_MAX):
        return None
    title = title_en if not title_zh else f"{title_en} · {title_zh}"
    return {"title": title[:200], "sentences": sentences}


def resolve_dialogue_b_voices(en_voice_a: str, zh_voice_a: str) -> tuple[str, str]:
    """B 角色音色：固定默认值，与 A 撞音色时按白名单顺延（两人不能同声）。"""
    en_b = DIALOGUE_B_ENGLISH_VOICE
    if en_b == en_voice_a:
        en_b = next((v for v in DIALOGUE_B_ENGLISH_FALLBACKS if v != en_voice_a), en_b)
    zh_b = DIALOGUE_B_CHINESE_VOICE
    if zh_b == zh_voice_a:
        zh_b = next((v for v in DIALOGUE_B_CHINESE_FALLBACKS if v != zh_voice_a), zh_b)
    return en_b, zh_b


def generate_stories(
    db: Session,
    user_id: UUID,
    llm_settings: LlmTranslationSettings,
    count: int = 10,
    max_attempts_per_story: int = 3,
) -> list[ListeningStory]:
    """用 LLM 生成 count 篇故事并入库。每篇失败重试 max_attempts_per_story 次。"""
    words = get_practiced_words(db, user_id)
    if len(words) < 20:
        raise ValueError(f"练过的单词太少（{len(words)} 个），无法编故事——先学习更多单词")

    # 已存在的主题不重复生成 — match the exact theme column, not a substring
    # of the LLM-generated title (a differently-worded title like "小明在学校
    # 的一天" slipped past the title check and regenerated the same theme).
    existing_themes = {
        row[0] for row in db.execute(select(ListeningStory.theme).where(ListeningStory.user_id == user_id)).all()
    }
    themes = [t for t in STORY_THEMES if t not in existing_themes]
    if not themes:
        themes = list(STORY_THEMES)

    created: list[ListeningStory] = []
    theme_idx = 0
    while len(created) < count and theme_idx < len(themes) * 2:
        theme = themes[theme_idx % len(themes)]
        theme_idx += 1
        story_data = None
        for attempt in range(1, max_attempts_per_story + 1):
            try:
                raw = generate_learning_text(build_story_prompt(words, theme), llm_settings)
                story_data = validate_story(parse_story_json(raw))
            except Exception as exc:
                logger.warning("故事生成解析失败（主题=%s 第%d次）: %s", theme, attempt, exc)
                story_data = None
            if story_data:
                break
        if not story_data:
            logger.warning("主题 %s 生成失败，跳过", theme)
            continue
        story = ListeningStory(
            user_id=user_id,
            title=story_data["title"],
            theme=theme,
            sentences=story_data["sentences"],
        )
        db.add(story)
        created.append(story)
    db.commit()
    for story in created:
        db.refresh(story)
    return created


def story_summary(story: ListeningStory) -> dict:
    return {
        "id": str(story.id),
        "title": story.title,
        "theme": story.theme,
        "sentence_count": len(story.sentences),
        "kind": "dialogue" if is_dialogue_sentences(story.sentences) else "story",
    }


def story_player_payload(
    story: ListeningStory,
    en_voice: str,
    zh_voice: str,
    speech_rate: int,
    en_voice_b: str | None = None,
    zh_voice_b: str | None = None,
) -> dict:
    """播放端载荷：每句带上英/中音频 URL（确定性 cache key，预热后 100% 命中）。

    对话条目（speaker=="B"）的音频用 B 角色音色——A/B 各一个音色，
    孩子能听出是两个人在说话。B 音色缺省时退回 A（向后兼容）。
    """
    sentences: list[dict] = []
    for s in story.sentences:
        is_b = s.get("speaker") == "B"
        sentence_en_voice = en_voice_b if is_b and en_voice_b else en_voice
        sentence_zh_voice = zh_voice_b if is_b and zh_voice_b else zh_voice
        sentences.append(
            {
                "en": s["en"],
                "zh": s["zh"],
                "speaker": s.get("speaker"),
                "en_audio_url": get_cache_url(s["en"], sentence_en_voice, speech_rate, suffix=AUDIO_SUFFIX),
                "zh_audio_url": get_cache_url(s["zh"], sentence_zh_voice, speech_rate, suffix=AUDIO_SUFFIX),
            }
        )
    return {
        "id": str(story.id),
        "title": story.title,
        "theme": story.theme,
        "kind": "dialogue" if is_dialogue_sentences(story.sentences) else "story",
        "sentences": sentences,
    }


def warm_story_audio(
    story: ListeningStory,
    en_voice: str,
    zh_voice: str,
    speech_rate: int,
    tts_settings_factory,
    en_voice_b: str | None = None,
    zh_voice_b: str | None = None,
) -> dict[str, int]:
    """为整篇故事预生成 TTS 缓存（英文句 + 中文句）。

    tts_settings_factory(voice, language) -> VolcengineTtsSettings，由调用方
    注入（endpoint/api_key 涉及用户私密配置，service 层不直接碰）。
    对话条目的 B 角色用 B 音色预热（与 story_player_payload 同一套路由）。
    返回 {"cached": n, "generated": n, "failed": n, "total": n}。
    """
    stats = {"cached": 0, "generated": 0, "failed": 0, "total": 0}

    jobs: list[tuple[str, str, str]] = []  # (text, voice, language)
    for s in story.sentences:
        is_b = s.get("speaker") == "B"
        sentence_en_voice = en_voice_b if is_b and en_voice_b else en_voice
        sentence_zh_voice = zh_voice_b if is_b and zh_voice_b else zh_voice
        jobs.append((s["en"], sentence_en_voice, "en-US"))
        jobs.append((s["zh"], sentence_zh_voice, "zh-CN"))

    for text, voice, language in jobs:
        stats["total"] += 1
        if get_cached_audio(text, voice, speech_rate, suffix=AUDIO_SUFFIX) is not None:
            stats["cached"] += 1
            continue
        try:
            synthesize_volcengine_speech(text, tts_settings_factory(voice, language))
            stats["generated"] += 1
        except Exception as exc:
            logger.warning("故事音频预热失败（%s…）: %s", text[:20], exc)
            stats["failed"] += 1
    return stats
