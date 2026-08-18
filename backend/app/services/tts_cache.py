import hashlib
import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger("tts_cache")

DEFAULT_TTS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tts_cache")


def _get_cache_dir() -> Path:
    from app.core.config import settings as app_settings

    cache_dir = getattr(app_settings, "tts_cache_dir", None) or DEFAULT_TTS_CACHE_DIR
    return Path(cache_dir)


def build_cache_key(text: str, voice: str, speech_rate: int) -> str:
    raw = f"{text}|{voice}|{speech_rate}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_audio(text: str, voice: str, speech_rate: int, suffix: str = "mp3") -> bytes | None:
    cache_key = build_cache_key(text, voice, speech_rate)
    file_path = _get_cache_dir() / f"{cache_key}.{suffix}"
    if file_path.exists():
        logger.info("TTS cache HIT: key=%s size=%d", cache_key, file_path.stat().st_size)
        return file_path.read_bytes()
    logger.info("TTS cache MISS: key=%s", cache_key)
    return None


def is_audio_cached(text: str, voice: str, speech_rate: int, suffix: str = "mp3") -> bool:
    """Existence-only cache probe.

    get_cached_audio(...) is not None reads the whole file into memory just
    to answer "is it there" — cache-status / precache loops do that once per
    word per request (a 200-target course re-read every mp3 on every status
    fetch). One stat() syscall instead.
    """
    cache_key = build_cache_key(text, voice, speech_rate)
    return (_get_cache_dir() / f"{cache_key}.{suffix}").exists()


def store_cached_audio(text: str, voice: str, speech_rate: int, audio: bytes, suffix: str = "mp3") -> None:
    cache_key = build_cache_key(text, voice, speech_rate)
    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / f"{cache_key}.{suffix}"
    # 原子写入：先写同目录的临时文件再 os.replace。直接 write_bytes 时进程
    # 崩溃会留下半截文件且永久 cache HIT，并发读者也可能读到部分前缀；
    # 同目录 rename 在 Linux 上是原子操作，读者无需改动。
    tmp_path = cache_dir / f".{cache_key}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp_path.write_bytes(audio)
    os.replace(tmp_path, file_path)
    logger.info("TTS cache STORE: key=%s size=%d", cache_key, len(audio))


def get_cache_url(text: str, voice: str, speech_rate: int, suffix: str = "mp3") -> str:
    from app.core.config import settings as app_settings

    cache_key = build_cache_key(text, voice, speech_rate)
    api_prefix = app_settings.api_v1_prefix.rstrip("/")
    return f"{api_prefix}/tts/cached/{cache_key}.{suffix}"


_CACHED_FILENAME_RE = re.compile(r"[a-f0-9]{64}\.(mp3|wav|ogg)")


def resolve_cached_file(hash_with_ext: str) -> bytes | None:
    if not _CACHED_FILENAME_RE.fullmatch(hash_with_ext):
        logger.warning("TTs cache rejected unsafe filename: %s", hash_with_ext)
        return None
    cache_dir = _get_cache_dir()
    file_path = (cache_dir / hash_with_ext).resolve()
    if not str(file_path).startswith(str(cache_dir.resolve())):
        logger.warning("TTs cache path traversal blocked: %s", hash_with_ext)
        return None
    if file_path.exists():
        return file_path.read_bytes()
    return None
