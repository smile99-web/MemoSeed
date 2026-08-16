from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_refresh_token_expires_at,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_refresh_token,
)
from app.db.session import get_db
from app.models.refresh_token import RefreshToken
from app.services.rate_limit import SlidingWindowRateLimiter
from app.models.user import User
from app.schemas.auth import AuthResponse, AuthUserResponse, LoginRequest, RefreshTokenRequest, RegisterRequest, TokenResponse
from app.schemas.common import MessageResponse

router = APIRouter()

# P0 (2026-08-06): throttle password-guessing against /login. Keyed by
# client-IP + email so one device mistyping a password is not locked out by
# another, while a targeted spray at one account is capped. 5 attempts /
# minute / key; single-process in-memory is right for this single-family app.
_login_rate_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=60.0)


def _client_ip(request: Request) -> str:
    """Client IP, trusting X-Forwarded-For only because nginx sets it.

    2026-08-16: use the LAST XFF element, not the first. nginx appends the
    real peer IP with $proxy_add_x_forwarded_for, so a client-supplied
    X-Forwarded-For header survives as the FIRST element(s) — an attacker
    rotating it bypassed the 5/min login limiter. The last element is the
    one nginx itself appended and cannot be spoofed through the proxy.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _extract_device_hint(user_agent: str | None) -> str | None:
    """Best-effort, truncated User-Agent for the audit trail. Never trusted."""
    if not user_agent:
        return None
    return user_agent[:160]


def issue_tokens(db: Session, user: User, device_hint: str | None = None) -> TokenResponse:
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=get_refresh_token_expires_at(),
            device_hint=device_hint,
        )
    )
    db.commit()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> AuthResponse:
    # Optional invite-code gate (2026-08-09): active only when INVITE_CODE is
    # configured. Open registration on the public domain let any stranger
    # create an account and burn the server's paid TTS/LLM keys.
    from app.core.config import settings as app_settings
    required_code = (app_settings.invite_code or "").strip()
    if required_code and (payload.invite_code or "").strip() != required_code:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="邀请码不正确")

    existing_user = db.scalar(select(User).where(or_(User.email == payload.email, User.username == payload.username)))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email or username already exists")

    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    tokens = issue_tokens(db, user, device_hint=_extract_device_hint(user_agent))
    return AuthResponse(user=AuthUserResponse.model_validate(user), tokens=tokens)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> AuthResponse:
    rate_key = f"{_client_ip(request)}|{payload.email.strip().lower()}"
    if not _login_rate_limiter.check(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait a minute and try again.",
        )
    user = db.scalar(select(User).where(User.email == payload.email, User.is_active.is_(True)))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    tokens = issue_tokens(db, user, device_hint=_extract_device_hint(user_agent))
    return AuthResponse(user=AuthUserResponse.model_validate(user), tokens=tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshTokenRequest,
    db: Annotated[Session, Depends(get_db)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> TokenResponse:
    token_hash = hash_refresh_token(payload.refresh_token)
    matched_token = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked.is_(False),
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    if matched_token is None or not verify_refresh_token(payload.refresh_token, matched_token.token_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = db.scalar(select(User).where(User.id == matched_token.user_id, User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    matched_token.is_revoked = True
    matched_token.revoked_at = datetime.now(UTC)
    db.add(matched_token)
    # Carry the original device hint onto the rotated token so a long-running
    # session keeps the same "iPad Safari" label even after multiple refreshes.
    return issue_tokens(db, user, device_hint=matched_token.device_hint or _extract_device_hint(user_agent))


@router.post("/logout", response_model=MessageResponse)
def logout(payload: RefreshTokenRequest, db: Annotated[Session, Depends(get_db)]) -> MessageResponse:
    token_hash = hash_refresh_token(payload.refresh_token)
    matched_token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash, RefreshToken.is_revoked.is_(False)))
    if matched_token is not None and verify_refresh_token(payload.refresh_token, matched_token.token_hash):
        matched_token.is_revoked = True
        matched_token.revoked_at = datetime.now(UTC)
        db.add(matched_token)
        db.commit()
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=AuthUserResponse)
def me(current_user: Annotated[User, Depends(get_current_user)]) -> AuthUserResponse:
    return AuthUserResponse.model_validate(current_user)
