import io
import logging
import uuid
import wave
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("cosyvoice_tts")

DEFAULT_COSYVOICE_BASE_URL = "http://localhost:50000"
COSYVOICE_SAMPLE_RATE = 22050
COSYVOICE_SAMPLE_WIDTH = 2  # int16 PCM


@dataclass(frozen=True)
class CosyVoiceTtsSettings:
    base_url: str
    speaker: str


def synthesize_cosyvoice_speech(text: str, settings: CosyVoiceTtsSettings) -> bytes:
    if not text.strip():
        raise ValueError("TTS text is required")
    if not settings.speaker.strip():
        raise ValueError("TTS speaker (spk_id) is required")

    base_url = settings.base_url.strip().rstrip("/")
    url = f"{base_url}/inference_sft"

    logger.info(
        "CosyVoice TTS request: url=%s speaker=%s text_len=%d",
        url,
        settings.speaker,
        len(text),
    )

    body, content_type = encode_multipart_form_data(
        {"tts_text": text, "spk_id": settings.speaker}
    )

    request = Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            pcm_data = response.read()
    except HTTPError as exc:
        exc.read()
        logger.error("CosyVoice TTS HTTP error: code=%d", exc.code)
        raise ValueError(f"CosyVoice TTS failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        logger.error("CosyVoice TTS network error: %s", exc)
        raise ValueError(f"CosyVoice TTS failed: {exc}") from exc

    if not pcm_data:
        logger.error("CosyVoice TTS returned empty audio")
        raise ValueError("CosyVoice TTS returned empty audio")

    wav_data = pcm_to_wav(pcm_data)
    logger.info("CosyVoice TTS success: pcm_size=%d wav_size=%d", len(pcm_data), len(wav_data))
    return wav_data


def encode_multipart_form_data(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----MemoSeedCosyVoice{uuid.uuid4().hex}"
    body_parts: list[bytes] = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}\r\n".encode("ascii"))
        body_parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        body_parts.append(value.encode("utf-8"))
        body_parts.append(b"\r\n")
    body_parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(body_parts), f"multipart/form-data; boundary={boundary}"


def pcm_to_wav(pcm_data: bytes, sample_rate: int = COSYVOICE_SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(COSYVOICE_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return buffer.getvalue()
