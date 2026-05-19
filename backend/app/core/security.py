import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid access token") from exc

    token_type = payload.get("type")
    subject = payload.get("sub")
    if token_type != "access" or not isinstance(subject, str):
        raise ValueError("Invalid access token")
    return subject


def create_refresh_token() -> str:
    return token_urlsafe(48)


def get_refresh_token_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bool(password_context.verify(plain_password, hashed_password))


def hash_password(password: str) -> str:
    return str(password_context.hash(password))


def hash_refresh_token(token: str) -> str:
    return hmac.new(settings.jwt_secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def verify_refresh_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_refresh_token(token), token_hash)
