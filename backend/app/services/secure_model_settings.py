import base64
import hashlib
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models.user_model_settings import UserModelSettings

ENCRYPTED_VALUE_PREFIX = "enc:v1:"
SECRET_SETTING_KEYS = {"llmApiKey", "volcengineTtsApiKey"}
SECRET_CONFIGURED_KEYS = {
    "llmApiKey": "llmApiKeyConfigured",
    "volcengineTtsApiKey": "volcengineTtsApiKeyConfigured",
}


def encrypt_model_settings(raw_settings: dict[str, Any], existing_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = dict(raw_settings)
    existing_settings = existing_settings or {}
    for key in SECRET_SETTING_KEYS:
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            settings[key] = encrypt_secret(value.strip())
            continue
        existing_value = existing_settings.get(key)
        if isinstance(existing_value, str) and existing_value.strip():
            settings[key] = encrypt_secret(existing_value.strip())
        else:
            settings.pop(key, None)
    return settings


def public_model_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    settings = dict(raw_settings)
    for secret_key, configured_key in SECRET_CONFIGURED_KEYS.items():
        configured = bool(decrypt_secret(settings.get(secret_key)))
        settings.pop(secret_key, None)
        settings[configured_key] = configured
    return settings


def private_model_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    settings = dict(raw_settings)
    for key in SECRET_SETTING_KEYS:
        settings[key] = decrypt_secret(settings.get(key))
    return settings


def get_private_model_settings(db: Session, user_id: UUID) -> dict[str, Any]:
    stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == user_id))
    if stored_settings is None:
        return {}
    return private_model_settings(stored_settings.settings)


def encrypt_secret(value: str) -> str:
    if value.startswith(ENCRYPTED_VALUE_PREFIX):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_VALUE_PREFIX}{token}"


def decrypt_secret(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if not value.startswith(ENCRYPTED_VALUE_PREFIX):
        return value
    token = value.removeprefix(ENCRYPTED_VALUE_PREFIX)
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def is_settings_table_missing_error(exc: ProgrammingError) -> bool:
    return "user_model_settings" in str(exc).lower()


def _fernet() -> Fernet:
    key_material = hashlib.sha256(app_settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))
