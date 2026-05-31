"""Pre-synthesized 44 English phonemes as isolated sounds.

Uses the professional TTS voice to generate phoneme approximations,
cached permanently so they are never re-synthesized.

Each phoneme is synthesized with text the TTS engine can produce
(e.g., isolated letter or short word fragment).
"""

import logging

logger = logging.getLogger("phonics_deck")

PHONEME_SYNTH_MAP: dict[str, str] = {
    # Consonants — use isolated letter sounds
    "p": "p",
    "b": "b",
    "t": "t",
    "d": "d",
    "k": "k",
    "g": "g",
    "f": "f",
    "v": "v",
    "th_voiceless": "th",
    "th_voiced": "the",
    "s": "s",
    "z": "z",
    "sh": "sh",
    "zh": "si",
    "h": "h",
    "ch": "ch",
    "j": "j",
    "m": "m",
    "n": "n",
    "ng": "ng",
    "l": "l",
    "r": "r",
    "w": "w",
    "y": "y",
    # Short vowels — use CVC words and hope for the vowel
    "a_short": "a",
    "e_short": "e",
    "i_short": "i",
    "o_short": "o",
    "u_short": "u",
    "oo_short": "oo",
    # Long vowels
    "a_long": "ay",
    "e_long": "ee",
    "i_long": "igh",
    "o_long": "oa",
    "u_long": "ue",
    "oo_long": "oo",
    # R-controlled
    "ar": "ar",
    "er": "er",
    "ir": "ir",
    "or": "or",
    "ur": "ur",
    # Diphthongs
    "ow": "ow",
    "ou": "ou",
    "oi": "oi",
    "oy": "oy",
}


def get_phonics_phonemes() -> dict[str, str]:
    """Return map of phoneme_key -> display_label."""
    display_map: dict[str, str] = {
        "p": "P /p/",
        "b": "B /b/",
        "t": "T /t/",
        "d": "D /d/",
        "k": "K /k/",
        "g": "G /g/",
        "f": "F /f/",
        "v": "V /v/",
        "th_voiceless": "TH (thin) /θ/",
        "th_voiced": "TH (this) /ð/",
        "s": "S /s/",
        "z": "Z /z/",
        "sh": "SH /ʃ/",
        "zh": "ZH /ʒ/",
        "h": "H /h/",
        "ch": "CH /tʃ/",
        "j": "J /dʒ/",
        "m": "M /m/",
        "n": "N /n/",
        "ng": "NG /ŋ/",
        "l": "L /l/",
        "r": "R /r/",
        "w": "W /w/",
        "y": "Y /j/",
        "a_short": "A short /æ/",
        "e_short": "E short /e/",
        "i_short": "I short /ɪ/",
        "o_short": "O short /ɒ/",
        "u_short": "U short /ʌ/",
        "oo_short": "OO short /ʊ/",
        "a_long": "A long /eɪ/",
        "e_long": "E long /iː/",
        "i_long": "I long /aɪ/",
        "o_long": "O long /əʊ/",
        "u_long": "U long /juː/",
        "oo_long": "OO long /uː/",
        "ar": "AR /ɑː/",
        "er": "ER /ɜː/",
        "ir": "IR /ɜː/",
        "or": "OR /ɔː/",
        "ur": "UR /ɜː/",
        "ow": "OW /aʊ/",
        "ou": "OU /aʊ/",
        "oi": "OI /ɔɪ/",
        "oy": "OY /ɔɪ/",
    }
    return display_map


def get_phonics_synth_map() -> dict[str, str]:
    """Return map of phoneme_key -> synthesis_text."""
    return dict(PHONEME_SYNTH_MAP)
