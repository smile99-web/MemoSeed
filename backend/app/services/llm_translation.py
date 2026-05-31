import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LlmTranslationSettings:
    provider: str
    base_url: str
    model: str
    api_key: str | None = None


DEFAULT_LLM_TRANSLATION_SETTINGS = LlmTranslationSettings(provider="ollama", base_url="http://localhost:11434", model="ali6parmak/hy-mt1.5:latest")


def needs_translation(chinese_text: str) -> bool:
    return not chinese_text.strip() or chinese_text.strip() == "待补充"


def translate_english_to_chinese(english_text: str, settings: LlmTranslationSettings) -> str:
    prompt = (
        "Translate the following English word, phrase, or sentence into concise Simplified Chinese. "
        "Return only the Chinese translation, with no explanation, quotes, markdown, or extra text.\n\n"
        f"English: {english_text.strip()}"
    )
    response = call_llm_generate(settings, prompt)
    translated_text = response.strip().strip('"“”')
    if not translated_text:
        raise ValueError("LLM translation returned empty text")
    return translated_text


def generate_learning_text(prompt: str, settings: LlmTranslationSettings) -> str:
    response = call_llm_generate(settings, prompt)
    generated_text = response.strip().strip('"“”')
    if not generated_text:
        raise ValueError("LLM generation returned empty text")
    return generated_text


def call_llm_generate(settings: LlmTranslationSettings, prompt: str) -> str:
    provider = settings.provider.strip().lower()
    if provider in {"ollama", "local"}:
        return call_ollama_generate(settings.base_url, settings.model, prompt)
    if provider in {"deepseek", "openai", "qwen"}:
        return call_openai_chat_completion(settings.base_url, settings.model, settings.api_key, prompt)
    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def call_ollama_generate(base_url: str, model: str, prompt: str) -> str:
    if not base_url.strip():
        raise ValueError("LLM base URL is required")
    if not model.strip():
        raise ValueError("LLM model is required")

    errors: list[str] = []
    for candidate_base_url in get_candidate_base_urls(base_url.strip()):
        try:
            return request_ollama_generate(candidate_base_url, model.strip(), prompt)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{candidate_base_url}: {exc}")

    raise ValueError("LLM translation failed: " + "; ".join(errors))


def request_ollama_generate(base_url: str, model: str, prompt: str) -> str:
    url = base_url.rstrip("/") + "/api/generate"
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))

    generated_text = body.get("response")
    if not isinstance(generated_text, str):
        raise ValueError("Invalid Ollama response")
    return generated_text


def call_openai_chat_completion(base_url: str, model: str, api_key: str | None, prompt: str) -> str:
    if not base_url.strip():
        raise ValueError("LLM base URL is required")
    if not model.strip():
        raise ValueError("LLM model is required")
    if not api_key or not api_key.strip():
        raise ValueError("LLM API key is required")

    url = build_openai_chat_completions_url(base_url)
    payload = json.dumps(
        {
            "model": model.strip(),
            "messages": [
                {"role": "system", "content": "You translate English into concise Simplified Chinese."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "stream": False,
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = read_http_error_detail(exc)
        raise ValueError(f"LLM translation failed: {url}: HTTP {exc.code}{detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"LLM translation failed: {url}: {exc}") from exc

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Invalid OpenAI-compatible response")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    generated_text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(generated_text, str):
        raise ValueError("Invalid OpenAI-compatible response")
    return generated_text


def build_openai_chat_completions_url(base_url: str) -> str:
    trimmed_base_url = base_url.strip().rstrip("/")
    if trimmed_base_url.endswith("/chat/completions"):
        return trimmed_base_url
    if trimmed_base_url.endswith("/compatible-mode/v1"):
        return f"{trimmed_base_url}/chat/completions"
    return f"{trimmed_base_url}/chat/completions"


def read_http_error_detail(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    return f" {body}" if body else ""


def get_candidate_base_urls(base_url: str) -> list[str]:
    parsed_url = urlparse(base_url)
    if parsed_url.hostname not in {"localhost", "127.0.0.1"}:
        return [base_url]

    host_docker_internal = urlunparse(parsed_url._replace(netloc=replace_hostname(parsed_url.netloc, "host.docker.internal")))
    return [base_url, host_docker_internal]


def replace_hostname(netloc: str, hostname: str) -> str:
    if "@" in netloc:
        auth, host = netloc.rsplit("@", 1)
        return f"{auth}@{replace_hostname(host, hostname)}"

    if ":" in netloc:
        _, port = netloc.rsplit(":", 1)
        return f"{hostname}:{port}"

    return hostname


def generate_choice_distractors(
    english_word: str,
    correct_chinese: str,
    count: int = 5,
    settings: LlmTranslationSettings | None = None,
) -> list[str]:
    """Generate wrong-but-plausible Chinese translations as distractors.

    Tries local Ollama first, falls back to the user's configured network LLM.
    Returns a list of Chinese phrases (up to `count` items).
    """
    prompt = (
        f"The English word \"{english_word.strip()}\" correctly means \"{correct_chinese.strip()}\" in Chinese.\n"
        f"Generate exactly {count} WRONG Chinese translations (each 1-5 characters) that a child English "
        f"learner might mistakenly think \"{english_word.strip()}\" means.\n"
        f"Rules:\n"
        f"1. Each must look like a plausible word meaning — short, common Chinese words/phrases.\n"
        f"2. ALL must be DIFFERENT from the correct answer \"{correct_chinese.strip()}\".\n"
        f"3. Make them CONFUSING — choose words with similar meaning range, similar-looking characters, "
        f"or words commonly mixed up by Chinese children learning English.\n"
        f"4. Examples of good confusing distractors for \"happy\" (高兴): 难过, 兴奋, 开心, 生气, 害怕\n"
        f"Return ONLY a JSON array of {count} strings, no explanation, no markdown.\n"
        f'Format: ["错误翻译1", "错误翻译2", "错误翻译3", "错误翻译4", "错误翻译5"]'
    )

    # Step 1: Try local Ollama
    local_settings = LlmTranslationSettings(
        provider="ollama",
        base_url="http://localhost:11434",
        model="ali6parmak/hy-mt1.5:latest",
    )
    try:
        result = _try_generate_distractors(local_settings, prompt, count)
        if len(result) >= count:
            return result[:count]
    except Exception:
        pass

    # Step 2: Try user-configured settings
    if settings is not None:
        try:
            result = _try_generate_distractors(settings, prompt, count)
            if len(result) >= count:
                return result[:count]
        except Exception:
            pass

    # Step 3: Try default network LLM as last resort
    network_settings = LlmTranslationSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key=settings.api_key if settings is not None else None,
    )
    if network_settings.api_key:
        try:
            result = _try_generate_distractors(network_settings, prompt, count)
            if len(result) >= count:
                return result[:count]
        except Exception:
            pass

    return []


def _try_generate_distractors(settings: LlmTranslationSettings, prompt: str, count: int) -> list[str]:
    response = call_llm_generate(settings, prompt)
    text = response.strip().strip("`").strip()
    # Try to parse as JSON array
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    # Fallback: split by common delimiters
    import re
    items = re.split(r'[,;，；\n]+', text)
    return [item.strip().strip('"“”\'') for item in items if item.strip().strip('"“”\'')]


def generate_phonetic_decomposition(english_word: str, settings: LlmTranslationSettings) -> dict:
    """Generate IPA, syllable breakdown, and phonics hints for an English word.

    Returns a dict with keys:
      - ipa: International Phonetic Alphabet transcription
      - syllables: list of syllable strings
      - phonics_hints: Chinese-language phonics hints for a young learner
    """
    clean_word = english_word.strip().lower()
    if not clean_word:
        return {"ipa": "", "syllables": [], "phonics_hints": ""}
    if len(clean_word) <= 2:
        return {
            "ipa": f"/{clean_word}/",
            "syllables": [clean_word],
            "phonics_hints": f"请听发音并模仿：{clean_word}",
        }

    prompt = (
        f"Analyze the English word \"{clean_word}\" and return a JSON object with three fields:\n"
        f"  \"ipa\": the IPA (International Phonetic Alphabet) pronunciation,\n"
        f"  \"syllables\": an array of the word broken into syllables (e.g. [\"beau\",\"ti\",\"ful\"]),\n"
        f"  \"phonics_hints\": a short Chinese-language phonics tip explaining how to pronounce "
        f"this word, suitable for a child learning English.\n"
        f"Return ONLY valid JSON, no markdown, no explanation."
    )
    try:
        response = call_llm_generate(settings, prompt)
        text = response.strip().strip("`").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {
                "ipa": str(parsed.get("ipa", "")),
                "syllables": [str(s) for s in parsed.get("syllables", [])]
                if isinstance(parsed.get("syllables"), list) else [],
                "phonics_hints": str(parsed.get("phonics_hints", "")),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback using existing functions
    syllable_list = split_into_phonetic_syllables(clean_word, settings)
    gp_map = generate_grapheme_phoneme_map(clean_word, settings)
    phonics_parts = [f"{k}={v}" for k, v in list(gp_map.items())[:5]]
    return {
        "ipa": f"/{clean_word}/",
        "syllables": syllable_list,
        "phonics_hints": f"请模仿发音：{clean_word}"
        + (f"（拼读：{'，'.join(phonics_parts)}）" if phonics_parts else ""),
    }


def split_into_phonetic_syllables(word: str, settings: LlmTranslationSettings) -> list[str]:
    """Split an English word into proper phonetic syllables using LLM.

    Uses vowel-consonant pattern rules and common English syllabification
    (e.g., 'beautiful' -> ['beau', 'ti', 'ful'] not character-count splits).
    The LLM is prompted to return a JSON array of syllable strings.
    Cached in the `syllables` JSON column on LearningItem.
    """
    clean_word = word.strip().lower()
    if len(clean_word) <= 3:
        return [clean_word]

    prompt = (
        "Split the following English word into proper phonetic syllables. "
        "Follow standard English syllabification rules (vowel-consonant patterns, open/closed syllables). "
        "Return ONLY a JSON array of syllable strings, no explanation, no markdown.\n\n"
        f"Word: {clean_word}\n\n"
        'Example: beautiful -> ["beau", "ti", "ful"]\n'
        'Example: elephant -> ["el", "e", "phant"]\n'
        'Example: computer -> ["com", "pu", "ter"]'
    )
    try:
        response = call_llm_generate(settings, prompt)
        text = response.strip().strip("`").strip()
        result = json.loads(text)
        if isinstance(result, list) and len(result) > 0:
            return [str(chunk).strip().lower() for chunk in result if str(chunk).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: basic vowel-consonant syllabification
    return _basic_syllabify(clean_word)


def _basic_syllabify(word: str) -> list[str]:
    """Fallback vowel-consonant pattern syllabification (no LLM)."""
    vowels = set("aeiouy")
    syllables: list[str] = []
    current = ""
    prev_vowel = False
    for i, ch in enumerate(word):
        is_vowel = ch in vowels
        current += ch
        if is_vowel and not prev_vowel and i > 0 and len(syllables) < 3:
            # Split before consonant clusters + vowel boundaries
            pass
        if i == len(word) - 1:
            syllables.append(current)
            current = ""
        elif is_vowel and i + 1 < len(word) and word[i + 1] not in vowels:
            # VC pattern: split after vowel if next is consonant
            lookahead = word[i + 1 : i + 4]
            if len(lookahead) >= 2 and lookahead[1] in vowels:
                # CVCV pattern: split before the consonant
                pass
            elif len(current) >= 2:
                syllables.append(current)
                current = ""
        prev_vowel = is_vowel
    if current:
        syllables.append(current)
    if not syllables:
        syllables = [word]
    return syllables


def generate_grapheme_phoneme_map(word: str, settings: LlmTranslationSettings) -> dict[str, str]:
    """Generate a grapheme-to-phoneme mapping for an English word using LLM.

    Returns a dict like {'igh': 'ī', 't': 't'}, mapping each grapheme
    (letter or letter group) to its phonics sound notation.
    Cached in the `grapheme_phoneme_map` JSON column on LearningItem.
    """
    clean_word = word.strip().lower()
    if not clean_word:
        return {}

    prompt = (
        "Break the following English word into its grapheme-phoneme mapping for phonics instruction. "
        "Each grapheme is a letter or letter group (like 'sh', 'igh', 'ea') that maps to one phoneme. "
        "Use simple phonics notation for sounds (e.g., short vowels: a/i/u/e/o, long vowels: ā/ē/ī/ō/ū/oo, consonants: b/k/d/f/g/h/j/l/m/n/p/r/s/t/v/w/y/z, digraphs: sh/ch/th/wh/ng). "
        "Return ONLY a JSON object mapping each grapheme (lowercase) to its phoneme, no explanation, no markdown.\n\n"
        f"Word: {clean_word}\n\n"
        'Example: light -> {"l": "l", "igh": "ī", "t": "t"}\n'
        'Example: ship -> {"sh": "sh", "i": "i", "p": "p"}\n'
        'Example: beautiful -> {"b": "b", "eau": "ū", "t": "t", "i": "i", "ful": "fəl"}\n'
        'Example: cat -> {"c": "k", "a": "a", "t": "t"}'
    )
    try:
        response = call_llm_generate(settings, prompt)
        text = response.strip().strip("`").strip()
        result = json.loads(text)
        if isinstance(result, dict) and len(result) > 0:
            return {str(k).strip().lower(): str(v).strip() for k, v in result.items() if str(k).strip() and str(v).strip()}
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: simple letter-by-letter mapping
    return _basic_grapheme_map(clean_word)


def _basic_grapheme_map(word: str) -> dict[str, str]:
    """Fallback grapheme mapping: each letter maps to itself."""
    return {ch: ch for ch in word if ch.isalpha()}


def generate_word_family(
    word: str,
    existing_words: list[str],
    settings: LlmTranslationSettings | None = None,
) -> list[str]:
    """Find rhyming/family words from the child's existing word bank.

    Returns a list of words from `existing_words` that share phonetic features
    (rhyme, same onset, same rime) with the given word. Falls back to
    suffix-based matching when no LLM is available.
    """
    clean_word = word.strip().lower()
    if not clean_word or not existing_words:
        return []

    if settings is not None and len(existing_words) >= 3:
        prompt = (
            "From the following list of English words, find any words that rhyme with or "
            "share the same word family (onset-rime pattern) as the target word. "
            "A word family shares the same rime (ending sound), e.g., bat/cat/hat share '-at'. "
            "Return ONLY a JSON array of matching words (empty array if none), no explanation.\n\n"
            f"Target word: {clean_word}\n"
            f"Word list: {json.dumps(existing_words[:50])}"
        )
        try:
            response = call_llm_generate(settings, prompt)
            text = response.strip().strip("`").strip()
            result = json.loads(text)
            if isinstance(result, list):
                return [str(w).strip().lower() for w in result if str(w).strip().lower() != clean_word]
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: suffix-based word family matching
    family: list[str] = []
    word_len = len(clean_word)
    min_match = min(2, max(1, word_len - 1))
    for suffix_len in range(word_len - 1, min_match - 1, -1):
        suffix = clean_word[-suffix_len:]
        for existing in existing_words:
            existing_clean = existing.strip().lower()
            if existing_clean != clean_word and existing_clean.endswith(suffix) and existing_clean not in family:
                family.append(existing_clean)
        if len(family) >= 3:
            break
    return family[:5]


def enrich_word_with_phonetics(
    word: str,
    settings: LlmTranslationSettings,
) -> tuple[list[str], dict[str, str]]:
    """One-shot LLM call to get both syllables and grapheme-phoneme mapping.

    Returns (syllables, grapheme_phoneme_map). Uses a single LLM call
    for efficiency, caching both results on the LearningItem.
    """
    clean_word = word.strip().lower()
    if not clean_word:
        return [clean_word], {}

    if len(clean_word) <= 3:
        return [clean_word], _basic_grapheme_map(clean_word)

    prompt = (
        "Analyze the following English word phonetically. Return ONLY a compact JSON object "
        "with two keys: 'syllables' (array of syllable strings) and 'graphemes' "
        "(object mapping each grapheme/letter-group to its phonics phoneme notation). "
        "Use standard English syllabification and phonics notation:\n"
        "- short vowels: a, e, i, o, u (as in cat, bed, sit, hot, cup)\n"
        "- long vowels: ā, ē, ī, ō, ū (as in cake, bee, bike, boat, cute)\n"
        "- other: oo (as in book), ow (as in cow), oy (as in boy), ar, er, ir, or, ur\n"
        "- consonants: b, k, d, f, g, h, j, l, m, n, p, r, s, t, v, w, y, z\n"
        "- digraphs: sh, ch, th (voiced), th (unvoiced), wh, ng, ck, ph, kn, wr\n"
        "No explanation, no markdown, no extra text.\n\n"
        f"Word: {clean_word}\n\n"
        'Example output: {"syllables": ["beau", "ti", "ful"], "graphemes": {"b": "b", "eau": "ū", "t": "t", "i": "i", "ful": "fəl"}}'
    )
    try:
        response = call_llm_generate(settings, prompt)
        text = response.strip().strip("`").strip()
        # Handle markdown code fences
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        syllables = result.get("syllables")
        graphemes = result.get("graphemes")
        valid_syllables: list[str] = []
        valid_graphemes: dict[str, str] = {}
        if isinstance(syllables, list) and len(syllables) > 0:
            valid_syllables = [str(s).strip().lower() for s in syllables if str(s).strip()]
        if isinstance(graphemes, dict) and len(graphemes) > 0:
            valid_graphemes = {str(k).strip().lower(): str(v).strip() for k, v in graphemes.items() if str(k).strip() and str(v).strip()}
        if valid_syllables and valid_graphemes:
            return valid_syllables, valid_graphemes
    except (json.JSONDecodeError, ValueError):
        pass

    # Individual fallback calls
    syllables = split_into_phonetic_syllables(clean_word, settings)
    gp_map = generate_grapheme_phoneme_map(clean_word, settings)
    return syllables, gp_map
