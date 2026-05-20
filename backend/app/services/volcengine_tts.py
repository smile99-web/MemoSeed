import base64
import json
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_VOLCENGINE_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_VOLCENGINE_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_VOLCENGINE_TTS_MODEL = "seed-tts-2.0-standard"
DEFAULT_VOLCENGINE_TTS_CHINESE_VOICE = "zh_female_xiaohe_uranus_bigtts"
DEFAULT_VOLCENGINE_TTS_ENGLISH_VOICE = "en_female_dacey_uranus_bigtts"


@dataclass(frozen=True)
class VolcengineTtsSettings:
    endpoint: str
    app_id: str | None
    access_token: str | None
    secret_key: str | None
    resource_id: str
    model: str
    voice: str


def synthesize_volcengine_speech(text: str, settings: VolcengineTtsSettings) -> bytes:
    if not text.strip():
        raise ValueError("TTS text is required")
    if not settings.voice.strip():
        raise ValueError("TTS voice is required")
    if not settings.access_token and not settings.secret_key:
        raise ValueError("Volcengine TTS API Key or Access Token is required")
    if settings.access_token and not settings.app_id and not settings.secret_key:
        raise ValueError("Volcengine TTS APP ID is required when using Access Token")

    request = Request(
        settings.endpoint.strip(),
        data=json.dumps(build_payload(text, settings)).encode("utf-8"),
        headers=build_headers(settings),
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            audio_chunks = collect_audio_chunks(response)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Volcengine TTS failed: HTTP {exc.code} {error_body}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"Volcengine TTS failed: {exc}") from exc

    if not audio_chunks:
        raise ValueError("Volcengine TTS returned empty audio")
    return b"".join(audio_chunks)


def build_headers(settings: VolcengineTtsSettings) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": settings.resource_id.strip(),
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    if settings.secret_key:
        headers["X-Api-Key"] = settings.secret_key.strip()
        return headers
    if settings.access_token and settings.app_id:
        headers["X-Api-App-Id"] = settings.app_id.strip()
        headers["X-Api-Access-Key"] = settings.access_token.strip()
    return headers


def build_payload(text: str, settings: VolcengineTtsSettings) -> dict[str, object]:
    return {
        "user": {"uid": "memoseed"},
        "req_params": {
            "text": text.strip(),
            "model": settings.model.strip(),
            "speaker": settings.voice.strip(),
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
        },
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
            continue

        code = body.get("code")
        if code not in (None, 0, 200, "0", "200"):
            raise ValueError(body.get("message") or "Volcengine TTS returned an error")
        data = body.get("data")
        if isinstance(data, str) and data:
            chunks.append(base64.b64decode(data))
    return chunks
