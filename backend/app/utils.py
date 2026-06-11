"""Shared utility functions used across services and routers."""

import re
from datetime import date, datetime
from typing import Any


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def normalize_word(value: str) -> str:
    return re.sub(r"^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$", "", value).lower()


def tokenize_words(value: str) -> list[str]:
    return [word for word in (normalize_word(part) for part in value.split()) if word]


def extract_mistake_words(mistake_type: str, expected_answer: str, actual_answer: str) -> list[str]:
    if mistake_type.startswith("word-spelling"):
        return tokenize_words(expected_answer)
    if "错词：" in actual_answer:
        _, words_text = actual_answer.split("错词：", 1)
        return tokenize_words(words_text.replace(",", " "))
    return []


def average(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def parse_datetime_setting(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_date_param(value: str) -> date | None:
    """Parse YYYY-MM-DD date string."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def string_setting(settings: dict[str, Any], key: str) -> str | None:
    value = settings.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
