"""Unit tests for the lenient read-aloud pronunciation scorer.

Product rules under test (parent's requirements):
- roughly-correct child pronunciation PASSES (protect confidence);
- ASR noise (case, punctuation, a/an swaps, accent-y near-homophones) passes;
- reading something completely different ("乱读") FAILS and triggers re-read;
- silence/noise is not an attempt at all (heard_speech=False).
"""

from app.services.pronunciation import PASS_THRESHOLD, score_pronunciation


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


def test_word_order_hiccup_still_passes():
    result = score_pronunciation("what is your name", "what your is name")
    assert result.passed is True


def test_score_is_bounded():
    result = score_pronunciation("hello world", "completely unrelated sentence here")
    assert 0.0 <= result.score <= 1.0
