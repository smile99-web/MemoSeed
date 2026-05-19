from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.models.user import User
from app.schemas.auth import AuthResponse, AuthUserResponse, LoginRequest, RefreshTokenRequest, RegisterRequest, TokenResponse
from app.schemas.common import MessageResponse

router = APIRouter()


def issue_tokens(db: Session, user: User) -> TokenResponse:
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=get_refresh_token_expires_at(),
        )
    )
    db.commit()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
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

    tokens = issue_tokens(db, user)
    return AuthResponse(user=AuthUserResponse.model_validate(user), tokens=tokens)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == payload.email, User.is_active.is_(True)))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    tokens = issue_tokens(db, user)
    return AuthResponse(user=AuthUserResponse.model_validate(user), tokens=tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshTokenRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
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
    return issue_tokens(db, user)


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
