"""Read-aloud pronunciation checking via Volcengine bigmodel ASR (flash).

The child reads the target English text aloud; we transcribe the recording
with the synchronous "flash" recognition endpoint and compare the transcript
against the expected text with a deliberately LENIENT matcher. The product
goal (parent's explicit requirement): roughly-correct pronunciation passes —
we never demand perfection from a child — but reading something completely
different ("乱读") must fail and trigger a re-read.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VOLCENGINE_ASR_FLASH_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
VOLCENGINE_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
ASR_STATUS_OK = "20000000"

# Volcengine returns status 20000003 ("Normal silence audio / no valid speech")
# when the clip contains no usable speech — the child read too quietly, or only
# background noise was captured. That is a NORMAL outcome, not an outage:
# mapping it to an empty transcript lets the scorer answer heard_speech=False
# (HTTP 200) instead of the endpoint returning 502. The frontend counts 502s
# as ASR failures and used to DISABLE the pronunciation gate after just two of
# them — two quiet reads were enough to switch the session into the ungated
# manual mode, which is exactly the bug the parent reported.
ASR_NO_SPEECH_STATUSES = {"20000003"}

# Lenient pass bar: max() of the similarity metrics must reach this. Kept at
# 0.6 so completely-unrelated speech ("乱读") and reading only one word still
# FAIL — this is the anti-garbage guard, not the leniency lever.
PASS_THRESHOLD = 0.6

# "Word coverage" gate — the primary lenient path for child voices. The child
# is judged to have read the sentence basically-correctly when at least
# PASS_COVERAGE_FRACTION of the expected words each fuzzy-match (char
# similarity >= WORD_COVERAGE_BAR) some word the ASR actually heard. This
# tolerates accent-y near-misses ("giv me a pin" for "give me a pen"), dropped
# function words ("this is big tree" for "this is a big tree") and ASR
# mangling, while completely unrelated speech shares none of the expected
# words and a child who only babbles one repeated word covers too few.
WORD_COVERAGE_BAR = 0.6
PASS_COVERAGE_FRACTION = 0.5

_WORD_RE = re.compile(r"[a-z']+")


@dataclass(frozen=True)
class PronunciationScore:
    score: float
    passed: bool
    heard_speech: bool


def recognize_speech_flash(
    audio: bytes,
    *,
    api_key: str,
    endpoint: str = VOLCENGINE_ASR_FLASH_ENDPOINT,
    resource_id: str = VOLCENGINE_ASR_RESOURCE_ID,
    timeout: int = 30,
) -> str:
    """Transcribe a short clip (a few seconds) synchronously. Returns text."""
    if not api_key:
        raise ValueError("Volcengine ASR X-Api-Key is required")
    if not audio:
        raise ValueError("Audio data is empty")

    payload = {
        "user": {"uid": "memoseed-pronunciation"},
        "audio": {"data": base64.b64encode(audio).decode("ascii")},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True},
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.headers.get("X-Api-Status-Code") or ""
            status_message = response.headers.get("X-Api-Message") or ""
            raw = response.read()
    except HTTPError as exc:
        exc.read()
        raise ValueError(f"Volcengine ASR request failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"Volcengine ASR request failed: {exc}") from exc

    if status_code != ASR_STATUS_OK:
        if status_code in ASR_NO_SPEECH_STATUSES or "silence" in status_message.lower() or "no valid speech" in status_message.lower():
            return ""
        raise ValueError(f"Volcengine ASR failed: {status_code or 'no status'} {status_message}".strip())

    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Volcengine ASR returned an invalid response") from exc

    result = body.get("result")
    text = result.get("text") if isinstance(result, dict) else None
    return (text or "").strip()


def score_pronunciation(expected: str, transcript: str) -> PronunciationScore:
    """Leniently compare what the child should have said vs what ASR heard."""
    transcript_norm = _normalize(transcript)
    if not transcript_norm:
        # Silence / noise: does not count as an attempt (no re-read penalty).
        return PronunciationScore(score=0.0, passed=False, heard_speech=False)

    expected_words = _words(expected)
    # Ultra-short targets ("a", "I", "go") are beyond ASR's reliable
    # resolution on child voices — any clear speech counts as a pass.
    if 0 < len("".join(expected_words)) <= 2:
        return PronunciationScore(score=1.0, passed=True, heard_speech=True)

    expected_norm = _normalize(expected)
    actual_words = _words(transcript)
    coverage = _word_coverage(expected_words, actual_words)
    score = max(
        _char_ratio(expected_norm, transcript_norm),
        _word_f1(expected_words, actual_words),
        _mean_best_word_score(expected_words, actual_words),
    )
    passed = score >= PASS_THRESHOLD or coverage >= PASS_COVERAGE_FRACTION
    return PronunciationScore(score=score, passed=passed, heard_speech=True)


def _normalize(text: str) -> str:
    return " ".join(_words(text))


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _char_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _word_f1(expected: list[str], actual: list[str]) -> float:
    """Multiset F1 over words — robust to word-order ASR hiccups."""
    if not expected or not actual:
        return 0.0
    remaining = list(actual)
    hits = 0
    for word in expected:
        if word in remaining:
            remaining.remove(word)
            hits += 1
    if hits == 0:
        return 0.0
    precision = hits / len(actual)
    recall = hits / len(expected)
    return 2 * precision * recall / (precision + recall)


def _mean_best_word_score(expected: list[str], actual: list[str]) -> float:
    """Per expected word, best char-similarity against any heard word, averaged.

    Catches heavy-accent renderings ("pen" heard as "pin") that exact word
    matching would miss, while unrelated speech still scores near zero.
    """
    if not expected or not actual:
        return 0.0
    total = 0.0
    for word in expected:
        total += max((_char_ratio(word, candidate) for candidate in actual), default=0.0)
    return total / len(expected)


def _word_coverage(expected: list[str], actual: list[str]) -> float:
    """Fraction of expected words each fuzzy-matched to some heard word.

    Each expected word counts as "read" when it is char-similar enough
    (>= WORD_COVERAGE_BAR) to at least one word the ASR heard. A child voice
    ASR transcribes with accent-y near-misses ("giv me a pin") or with small
    function words dropped still reaches a high fraction, while a completely
    unrelated sentence shares almost none of the expected words.
    """
    if not expected or not actual:
        return 0.0
    covered = 0
    for word in expected:
        if any(_char_ratio(word, candidate) >= WORD_COVERAGE_BAR for candidate in actual):
            covered += 1
    return covered / len(expected)
