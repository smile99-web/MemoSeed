import base64
import json
import logging
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("volcengine_tts")

DEFAULT_VOLCENGINE_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_VOLCENGINE_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_VOLCENGINE_TTS_MODEL = "seed-tts-2.0-standard"
DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE = "zh_female_xiaohe_uranus_bigtts"
DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE = "en_female_dacey_uranus_bigtts"


@dataclass(frozen=True)
class VolcengineTtsSettings:
    endpoint: str
    api_key: str | None
    resource_id: str
    model: str
    voice: str
    language: str | None = None
    speech_rate: int = 0


def synthesize_volcengine_speech(text: str, settings: VolcengineTtsSettings) -> bytes:
    if not text.strip():
        raise ValueError("TTS text is required")
    if not settings.voice.strip():
        raise ValueError("TTS voice is required")
    if not settings.api_key or not settings.api_key.strip():
        raise ValueError("Volcengine TTS X-Api-Key is required")

    payload = build_payload(text, settings)
    print(f"[TTS DEBUG] Request: endpoint={settings.endpoint} resource={settings.resource_id} model={settings.model} voice={settings.voice} language={settings.language} text_len={len(text)}", flush=True)
    print(f"[TTS DEBUG] Payload: {json.dumps(payload, ensure_ascii=False)[:500]}", flush=True)

    request = Request(
        settings.endpoint.strip(),
        data=json.dumps(payload).encode("utf-8"),
        headers=build_headers(settings),
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            print(f"[TTS DEBUG] Response: status={response.status} content_type={response.headers.get('Content-Type', '')}", flush=True)
            audio_chunks = collect_audio_chunks(response)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("Volcengine TTS HTTP error: code=%d body=%s", exc.code, error_body)
        raise ValueError(f"Volcengine TTS failed: HTTP {exc.code} {error_body}") from exc
    except (URLError, TimeoutError) as exc:
        logger.error("Volcengine TTS network error: %s", exc)
        raise ValueError(f"Volcengine TTS failed: {exc}") from exc

    if not audio_chunks:
        logger.error("Volcengine TTS returned empty audio (no audio chunks collected)")
        raise ValueError("Volcengine TTS returned empty audio")
    logger.info("Volcengine TTS success: audio_size=%d bytes", len(b"".join(audio_chunks)))
    return b"".join(audio_chunks)


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
    print(f"[TTS DEBUG] Response lines: count={len(raw_lines)}", flush=True)
    if len(raw_lines) == 1:
        print(f"[TTS DEBUG] Response single-line (len={len(raw_lines[0])}): {raw_lines[0][:300]!r}", flush=True)
        chunks.extend(parse_response_fragment(raw_lines[0]))
        print(f"[TTS DEBUG] Chunks collected from single-line: {len(chunks)}", flush=True)
        return chunks

    for i, raw_line in enumerate(raw_lines):
        if i < 3 or i >= len(raw_lines) - 1:
            print(f"[TTS DEBUG] Response line[{i}] (len={len(raw_line)}): {raw_line[:200]!r}", flush=True)
        chunks.extend(parse_response_fragment(raw_line))
    print(f"[TTS DEBUG] Total chunks collected: {len(chunks)}", flush=True)
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
            logger.warning("Volcengine TTS unparseable fragment: %.200s", parsed_fragment)
            continue

        code = body.get("code")
        # Volcengine TTS API returns 0, 200, 20000000 as success codes
        if code is not None and code not in (0, 200, "0", "200") and str(code) not in ("0", "200", "20000000"):
            msg = body.get("message") or "Volcengine TTS returned an error"
            logger.error("Volcengine TTS API error: code=%s message=%s full_body=%.500s", code, msg, body)
            raise ValueError(msg)
        data = body.get("data")
        if isinstance(data, str) and data:
            decoded = base64.b64decode(data)
            chunks.append(decoded)
        elif data is not None:
            logger.info("Volcengine TTS non-string data field (skipped): type=%s", type(data).__name__)
    return chunks
