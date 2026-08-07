"""Unit tests for the lenient read-aloud pronunciation scorer.

Product rules under test (parent's requirements):
- roughly-correct child pronunciation PASSES (protect confidence);
- ASR noise (case, punctuation, a/an swaps, accent-y near-homophones) passes;
- reading something completely different ("乱读") FAILS and triggers re-read;
- silence/noise is not an attempt at all (heard_speech=False).
"""

import json

import pytest

from app.services import pronunciation
from app.services.pronunciation import PASS_THRESHOLD, recognize_speech_flash, score_pronunciation


def test_exact_match_passes():
    result = score_pronunciation("Give me a pen", "Give me a pen")
    assert result.passed is True
    assert result.heard_speech is True
    assert result.score >= PASS_THRESHOLD


def test_asr_noise_still_passes():
    # Real transcript observed from the Volc flash endpoint for "Give me a pen".
    result = score_pronunciation("Give me a pen", "Give Me Your pen. ")
    assert result.passed is True
    assert result.heard_speech is True


def test_case_and_punctuation_are_ignored():
    result = score_pronunciation("I like apples.", "i like APPLES!")
    assert result.passed is True


def test_accented_near_homophone_passes():
    # Child says "pen" with an accent, ASR hears "pin".
    result = score_pronunciation("pen", "pin")
    assert result.passed is True


def test_partial_reading_fails():
    # Only one word of a four-word sentence is not good enough.
    result = score_pronunciation("Give me a pen", "pen")
    assert result.passed is False
    assert result.heard_speech is True


def test_completely_different_speech_fails():
    result = score_pronunciation("Give me a pen", "I like eating apples and bananas")
    assert result.passed is False
    assert result.heard_speech is True
    assert result.score < PASS_THRESHOLD


def test_gibberish_words_fail():
    result = score_pronunciation("The cat is on the mat", "banana orange purple monkey dishwasher")
    assert result.passed is False


def test_empty_transcript_is_not_an_attempt():
    for transcript in ("", "   ", "...", "!!!"):
        result = score_pronunciation("Give me a pen", transcript)
        assert result.passed is False
        assert result.heard_speech is False
        assert result.score == 0.0


def test_ultra_short_target_passes_on_any_speech():
    # "a"/"I"/"go" are beyond ASR's reliable resolution on child voices.
    for expected in ("a", "I", "go"):
        result = score_pronunciation(expected, "uh")
        assert result.passed is True
        assert result.heard_speech is True


def test_ultra_short_target_rejects_long_ambient_transcript():
    # 2026-08-07: the frontend peak-normalizes recordings before ASR, so
    # background speech (TV dialogue) returns as a real multi-word
    # transcript. That must NOT earn a pass on a one-letter prompt — the
    # child said nothing. It still counts as heard speech (a failed
    # attempt), not as silence.
    result = score_pronunciation("a", "and then she said hello to him")
    assert result.passed is False
    assert result.heard_speech is True
    # Two-word noise bursts stay lenient — could be the child's own attempt.
    result = score_pronunciation("go", "go go")
    assert result.passed is True


def test_word_order_hiccup_still_passes():
    result = score_pronunciation("what is your name", "what your is name")
    assert result.passed is True


def test_score_is_bounded():
    result = score_pronunciation("hello world", "completely unrelated sentence here")
    assert 0.0 <= result.score <= 1.0


class _FakeHttpResponse:
    """Minimal stand-in for urllib's urlopen response (context manager)."""

    def __init__(self, headers: dict[str, str], body: bytes):
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, status_code: str, message: str, text: str = "") -> None:
    body = json.dumps({"result": {"text": text}}).encode("utf-8")
    response = _FakeHttpResponse({"X-Api-Status-Code": status_code, "X-Api-Message": message}, body)
    monkeypatch.setattr(pronunciation, "urlopen", lambda *args, **kwargs: response)


def test_silence_status_returns_empty_transcript(monkeypatch: pytest.MonkeyPatch):
    # 20000003 "Normal silence audio" is a quiet-read outcome, not an outage —
    # it must surface as "" (→ heard_speech=False, HTTP 200) instead of the
    # 502 that tripped the frontend's ASR-error circuit breaker and disabled
    # the pronunciation gate.
    _patch_urlopen(monkeypatch, "20000003", "Normal silence audio no valid speech in audio")
    assert recognize_speech_flash(b"fake-audio", api_key="test-key") == ""


def test_real_asr_failure_still_raises(monkeypatch: pytest.MonkeyPatch):
    _patch_urlopen(monkeypatch, "45000000", "invalid argument")
    with pytest.raises(ValueError, match="Volcengine ASR failed"):
        recognize_speech_flash(b"fake-audio", api_key="test-key")


def test_ok_status_returns_transcript(monkeypatch: pytest.MonkeyPatch):
    _patch_urlopen(monkeypatch, "20000000", "Success", text="Give me a pen")
    assert recognize_speech_flash(b"fake-audio", api_key="test-key") == "Give me a pen"


def test_word_coverage_pass_accented_transcription():
    # ASR hears accent-y near-misses of every word — still a basically-correct read.
    result = score_pronunciation("Give me a pen", "giv me a pin")
    assert result.passed is True
    assert result.heard_speech is True


def test_word_coverage_pass_dropped_function_words():
    result = score_pronunciation("This is a big tree", "this is big tree")
    assert result.passed is True


def test_word_coverage_pass_homophone_transcription():
    result = score_pronunciation("I can swim", "eye can swim")
    assert result.passed is True


def test_word_coverage_pass_most_words_correct():
    # Three of four words right — basically correct even if one word differs.
    result = score_pronunciation("Give me a pen", "give me a book")
    assert result.passed is True


def test_word_coverage_rejects_babbling_one_word():
    # Repeating a single word of a longer sentence must NOT pass.
    result = score_pronunciation("The cat is on the mat", "cat cat cat")
    assert result.passed is False
    assert result.heard_speech is True


def test_word_coverage_rejects_first_word_only():
    # "只读第一个词" regression guard — one word of a four-word sentence fails.
    result = score_pronunciation("Give me a pen", "give")
    assert result.passed is False
    assert result.heard_speech is True


def test_word_coverage_rejects_unrelated_speech():
    result = score_pronunciation("Give me a pen", "I like eating apples and bananas")
    assert result.passed is False
    assert result.heard_speech is True
