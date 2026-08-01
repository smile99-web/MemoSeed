"""Handwriting dictation mode ("手写听写") — AI-judged handwritten answers.

Mirrors the tingxie (语文听写) pattern: the child handwrites on an on-screen
canvas (Apple Pencil on iPad), the canvas PNG is sent to a vision LLM, and
the model judges correctness like a patient teacher. Two task types:

- dictation  (听写):   TTS plays an English word/sentence → child writes the
                       English → judge spelling (lenient on case/punctuation/
                       spacing, strict on letters — a misspelled word is a
                       real spelling error worth feeding FSRS);
- translation (写翻译): child sees/hears an English word → writes the Chinese
                       meaning → judge SEMANTICALLY (any valid meaning from
                       the item's chinese_text list passes; minor character
                       wobble is forgiven, wrong meaning fails).

Hard-won lessons ported from tingxie:
- the vision call MUST pass ``thinking: {"type": "disabled"}`` — thinking
  mode takes 60-120s, disabled it answers in 2-4s;
- never trust the model's own verdict blindly: for dictation we re-compare
  the recognized text against the target server-side (normalized equality
  forces correct, heavy mismatch forces incorrect), exactly like tingxie
  recomputing wrong_positions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from app.utils import tokenize_words

logger = logging.getLogger(__name__)

# Vision model served by the Volcengine Agent Plan (same model tingxie uses
# for 语文听写 handwriting recognition — multimodal and fast with thinking
# disabled). Overridable per-user via the handwritingVisionModel setting.
DEFAULT_VISION_MODEL = "doubao-seed-2-1-turbo"

HANDWRITING_DICTATION_TASK_TYPE = "handwriting_dictation"
HANDWRITING_TRANSLATION_TASK_TYPE = "handwriting_translation"
HANDWRITING_DICTATION_REVIEW_MODE = "handwriting-dictation"
HANDWRITING_TRANSLATION_REVIEW_MODE = "handwriting-translation"
HANDWRITING_REVIEW_MODES = (HANDWRITING_DICTATION_REVIEW_MODE, HANDWRITING_TRANSLATION_REVIEW_MODE)

HANDWRITING_DAILY_CAP = 12
MAX_SENTENCE_WORDS = 8  # handwriting a long sentence is slow — keep it short
MAX_SENTENCE_CHARS = 120
MAX_IMAGE_DATA_URL_CHARS = 9 * 1024 * 1024  # same bound as tingxie

# 手写听写队列的课程来源（家长指定）：先出单词复习，再按课次出中考英语
# 第1课→第10课的句子。包名容忍 "中考英语"/"中考英文" 两种写法。
HANDWRITING_COURSE_PACKAGE_NAMES = ("中考英语", "中考英文")

_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class HandwritingVerdict:
    recognized: str
    correct: bool
    comment: str


# --------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------

def is_dictation_candidate(item: Any) -> bool:
    """Words always qualify; sentences only when short enough to handwrite."""
    item_type = getattr(item, "item_type", None)
    english = (getattr(item, "english_text", "") or "").strip()
    if not english:
        return False
    if item_type == "word":
        return True
    if item_type in ("sentence", "phrase"):
        return len(english) <= MAX_SENTENCE_CHARS and len(tokenize_words(english)) <= MAX_SENTENCE_WORDS
    return False


def parse_lesson_number(course_name: str) -> int:
    """"第N课" → N；解析不了的排到最后。"""
    match = re.search(r"第\s*(\d+)\s*课", course_name or "")
    return int(match.group(1)) if match else 999


def pick_review_word_tasks(
    rows: list[tuple[Any, float | None, datetime | None]],
    *,
    tested_today_ids: set[UUID],
    limit: int,
) -> list[tuple[Any, str]]:
    """单词复习部分：学过的词（调用方保证 repetition_count > 0）最弱优先。

    今日已测的排除；有中文释义的词交替 听写/翻译（两个方向都练），
    无释义只听写。
    """
    pool: list[tuple[Any, float, datetime | None]] = []
    for item, strength, last_tested_at in rows:
        if getattr(item, "id", None) in tested_today_ids:
            continue
        if getattr(item, "item_type", None) != "word":
            continue
        if not is_dictation_candidate(item):
            continue
        pool.append((item, strength if strength is not None else -1.0, last_tested_at))
    pool.sort(key=lambda row: (
        row[1],  # weakest first
        row[2] is not None,  # never-handwritten before practiced
        (row[2] or _EPOCH).replace(tzinfo=None),  # then oldest-practiced
    ))

    tasks: list[tuple[Any, str]] = []
    word_toggle = False
    for item, _strength, _tested in pool[:limit]:
        if (item.chinese_text or "").strip():
            task_type = HANDWRITING_TRANSLATION_TASK_TYPE if word_toggle else HANDWRITING_DICTATION_TASK_TYPE
            word_toggle = not word_toggle
        else:
            task_type = HANDWRITING_DICTATION_TASK_TYPE
        tasks.append((item, task_type))
    return tasks


def pick_course_dictation_tasks(
    rows: list[Any],
    *,
    tested_today_ids: set[UUID],
    limit: int,
) -> list[tuple[Any, str]]:
    """课程句子部分（中考英语 第1课→第10课）：全部听写。

    rows 由调用方按 (课次, sort_order) 排好序传入——这里不重复排序，
    只过滤 今日已测 / 不适合手写（过长），顺序取前 limit 个。
    课程句子是"学习内容"，不要求学过（repetition_count 可为 0）。
    """
    tasks: list[tuple[Any, str]] = []
    for item in rows:
        if len(tasks) >= limit:
            break
        if getattr(item, "id", None) in tested_today_ids:
            continue
        if not is_dictation_candidate(item):
            continue
        tasks.append((item, HANDWRITING_DICTATION_TASK_TYPE))
    return tasks


def compose_daily_handwriting_queue(
    word_rows: list[tuple[Any, float | None, datetime | None]],
    course_rows: list[Any],
    *,
    tested_today_ids: set[UUID],
    limit: int,
) -> list[tuple[Any, str]]:
    """今日手写队列 = 单词复习在前（约 2/3）+ 中考课程句子按课次在后。

    一侧不足时另一侧补齐，保证队列不空（课程句子写完 10 课后，
    队列自然回到纯单词复习）。
    """
    word_quota = max(1, limit - limit // 3)
    course_quota = limit - word_quota
    words = pick_review_word_tasks(word_rows, tested_today_ids=tested_today_ids, limit=word_quota)
    course = pick_course_dictation_tasks(course_rows, tested_today_ids=tested_today_ids, limit=course_quota)
    if len(words) < word_quota:
        # 复习词不足 → 课程句子多补
        course = pick_course_dictation_tasks(
            course_rows, tested_today_ids=tested_today_ids, limit=course_quota + (word_quota - len(words)),
        )
    if len(course) < course_quota:
        # 课程句子不足 → 复习词多补（取 limit 个最弱的即可，含前面已选的）
        words = pick_review_word_tasks(word_rows, tested_today_ids=tested_today_ids, limit=limit - len(course))
    return (words + course)[:limit]


# --------------------------------------------------------------------------
# Vision judging
# --------------------------------------------------------------------------

_DICTATION_PROMPT = """你是小学英语听写批改老师。图片是孩子在四线三格中手写的英文。
孩子听到的录音内容是「{expected}」，请完成：
1. 仔细辨认孩子实际写出的英文（孩子字体可能稚嫩、连笔、大小不一）；
2. 与目标内容对比并判定对错。
宽松规则（都算对）：大小写不同、标点符号不同或缺失、单词之间空格不均匀。
严格规则（都算错）：单词拼写错误、漏写单词、多写单词、写成了别的词。
只输出 JSON，不要输出任何其他文字：
{{"recognized": "辨认出的英文", "correct": true或false, "comment": "给孩子的简短评语，15字以内，语气温和鼓励"}}"""

_TRANSLATION_PROMPT = """你是小学英语批改老师。图片是孩子手写的中文。
孩子看到的英文是「{expected}」，参考中文意思是「{chinese}」。
请完成：
1. 仔细辨认孩子实际写出的中文（孩子字体可能稚嫩，允许写拼音代替生字）；
2. 判定孩子写的中文是否表达了正确的意思。只要意思是英文词的任何一个正确释义就算对
（同义表达、意思相近、用词不同都可以）；写的是别的词的意思、意思错误、答非所问算错。
只输出 JSON，不要输出任何其他文字：
{{"recognized": "辨认出的中文", "correct": true或false, "comment": "给孩子的简短评语，15字以内，语气温和鼓励"}}"""


def judge_handwriting(
    image_data_url: str,
    task_type: str,
    *,
    expected_english: str,
    expected_chinese: str = "",
    base_url: str,
    api_key: str,
    model: str = DEFAULT_VISION_MODEL,
    timeout: int = 30,
) -> HandwritingVerdict:
    """Send the canvas snapshot to the vision LLM and parse its verdict."""
    if not api_key:
        raise ValueError("Handwriting recognition is not configured")
    if not image_data_url.startswith("data:image/"):
        raise ValueError("image must be a data URL")

    if task_type == HANDWRITING_TRANSLATION_TASK_TYPE:
        prompt = _TRANSLATION_PROMPT.format(expected=expected_english, chinese=expected_chinese or "（无参考释义）")
    else:
        prompt = _DICTATION_PROMPT.format(expected=expected_english)

    payload = {
        "model": model,
        # tingxie lesson: thinking mode takes 60-120s; disabled it is 2-4s.
        "thinking": {"type": "disabled"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 400,
    }
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        exc.read()
        raise ValueError(f"Handwriting recognition request failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"Handwriting recognition request failed: {exc}") from exc

    try:
        body = json.loads(raw.decode("utf-8"))
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, UnicodeDecodeError) as exc:
        raise ValueError("Handwriting recognition returned an invalid response") from exc
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Handwriting recognition result parse failed: {text[:200]}") from exc

    recognized = str(result.get("recognized") or "").strip()
    model_correct = bool(result.get("correct"))
    comment = str(result.get("comment") or "").strip()

    if task_type == HANDWRITING_TRANSLATION_TASK_TYPE:
        # Semantic judgment is the model's job — no string compare possible.
        return HandwritingVerdict(recognized=recognized, correct=model_correct, comment=comment)

    # Dictation: re-verify server-side (tingxie lesson — never trust the
    # model's own verdict). Normalized equality forces a pass even when the
    # model is over-strict; a heavily divergent recognition forces a fail
    # even when the model hallucinates success.
    expected_norm = _normalize_english(expected_english)
    recognized_norm = _normalize_english(recognized)
    if not recognized_norm:
        # The model saw nothing readable — its own comment is guesswork, so
        # always give the actionable "write clearer" hint instead.
        return HandwritingVerdict(recognized=recognized, correct=False, comment="没有认出写的字，再写清楚一点试试")
    if recognized_norm == expected_norm:
        return HandwritingVerdict(recognized=recognized, correct=True, comment=comment)
    similarity = SequenceMatcher(None, expected_norm, recognized_norm).ratio()
    correct = model_correct and similarity >= 0.85
    return HandwritingVerdict(recognized=recognized, correct=correct, comment=comment)


_WORD_CHAR_RE = re.compile(r"[a-z']+")


def _normalize_english(text: str) -> str:
    """Case/punctuation/spacing-insensitive form for dictation comparison."""
    return " ".join(_WORD_CHAR_RE.findall((text or "").lower()))
