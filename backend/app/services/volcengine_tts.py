import base64
import json
import logging
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.tts_cache import get_cached_audio, store_cached_audio

logger = logging.getLogger("volcengine_tts")

DEFAULT_VOLCENGINE_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_VOLCENGINE_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_VOLCENGINE_TTS_MODEL = "seed-tts-2.0-standard"
DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE = "zh_female_xiaohe_uranus_bigtts"
DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE = "en_female_dacey_uranus_bigtts"

AUDIO_SUFFIX = "mp3"


@dataclass(frozen=True)
class VolcengineTtsSettings:
    endpoint: str
    api_key: str | None
    resource_id: str
    model: str
    voice: str
    language: str | None = None
    speech_rate: int = 0


def synthesize_volcengine_speech(text: str, settings: VolcengineTtsSettings, use_cache: bool = True) -> bytes:
    if not text.strip():
        raise ValueError("TTS text is required")
    if not settings.voice.strip():
        raise ValueError("TTS voice is required")
    if not settings.api_key or not settings.api_key.strip():
        raise ValueError("Volcengine TTS X-Api-Key is required")

    if use_cache:
        cached = get_cached_audio(text, settings.voice, settings.speech_rate, suffix=AUDIO_SUFFIX)
        if cached is not None:
            return cached

    payload = build_payload(text, settings)
    logger.info(
        "Volcengine TTS request: endpoint=%s resource=%s model=%s voice=%s language=%s text_len=%d",
        settings.endpoint,
        settings.resource_id,
        settings.model,
        settings.voice,
        settings.language,
        len(text),
    )

    request = Request(
        settings.endpoint.strip(),
        data=json.dumps(payload).encode("utf-8"),
        headers=build_headers(settings),
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            audio_chunks = collect_audio_chunks(response)
    except HTTPError as exc:
        exc.read()
        logger.error("Volcengine TTS HTTP error: code=%d", exc.code)
        raise ValueError(f"Volcengine TTS failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        logger.error("Volcengine TTS network error: %s", exc)
        raise ValueError(f"Volcengine TTS failed: {exc}") from exc

    if not audio_chunks:
        logger.error("Volcengine TTS returned empty audio (no audio chunks collected)")
        raise ValueError("Volcengine TTS returned empty audio")
    audio = b"".join(audio_chunks)
    logger.info("Volcengine TTS success: audio_size=%d bytes", len(audio))
    if use_cache:
        store_cached_audio(text, settings.voice, settings.speech_rate, audio, suffix=AUDIO_SUFFIX)
    return audio


def build_headers(settings: VolcengineTtsSettings) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": settings.resource_id.strip(),
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Key": settings.api_key.strip(),
    }
    return headers


def build_payload(text: str, settings: VolcengineTtsSettings) -> dict[str, object]:
    additions: dict[str, object] = {}
    if settings.language:
        additions["explicit_language"] = settings.language.lower().replace("_", "-")

    req_params: dict[str, object] = {
        "text": text.strip(),
        "model": settings.model.strip(),
        "speaker": settings.voice.strip(),
        "audio_params": {
            "format": "mp3",
            "sample_rate": 24000,
            "speech_rate": settings.speech_rate,
            "loudness_rate": 0,
        },
    }
    if additions:
        req_params["additions"] = json.dumps(additions, ensure_ascii=False)

    return {
        "user": {"uid": "memoseed"},
        "req_params": req_params,
    }


def collect_audio_chunks(response: object) -> list[bytes]:
    chunks: list[bytes] = []
    raw_lines = response.readlines()  # type: ignore[attr-defined]
    if len(raw_lines) == 1:
        chunks.extend(parse_response_fragment(raw_lines[0]))
        return chunks

    for raw_line in raw_lines:
        chunks.extend(parse_response_fragment(raw_line))
    return chunks


def parse_response_fragment(raw_fragment: bytes) -> list[bytes]:
    fragment = raw_fragment.decode("utf-8", errors="ignore").strip()
    if not fragment:
        return []
    if fragment.startswith("data:"):
        fragment = fragment.removeprefix("data:").strip()
    if fragment == "[DONE]":
        return []

    parsed_fragments = [fragment]
    if "\n" in fragment:
        parsed_fragments = [line.strip().removeprefix("data:").strip() for line in fragment.splitlines() if line.strip()]

    chunks: list[bytes] = []
    for parsed_fragment in parsed_fragments:
        try:
            body = json.loads(parsed_fragment)
        except json.JSONDecodeError:
            logger.warning("Volcengine TTS returned an unparseable response fragment")
            continue

        code = body.get("code")
        # Volcengine TTS API returns 0, 200, 20000000 as success codes
        if code is not None and code not in (0, 200, "0", "200") and str(code) not in ("0", "200", "20000000"):
            msg = body.get("message") or "Volcengine TTS returned an error"
            logger.error("Volcengine TTS API error: code=%s message=%s", code, msg)
            raise ValueError(msg)
        data = body.get("data")
        if isinstance(data, str) and data:
            decoded = base64.b64decode(data)
            chunks.append(decoded)
        elif data is not None:
            logger.info("Volcengine TTS non-string data field (skipped): type=%s", type(data).__name__)
    return chunks
