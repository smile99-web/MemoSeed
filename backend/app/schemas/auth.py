from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    # Optional invite code — enforced only when the server sets INVITE_CODE.
    invite_code: str | None = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    # 登录不做注册强度校验（min_length=8 会把早期短密码账号挡在 422，
    # 且前端把 422 数组 detail 显示成 [object Object]）。对错由
    # verify_password 判定，统一返回 401（2026-08-09 修复）。
    password: str = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthUserResponse(BaseModel):
    id: UUID
    email: EmailStr
    username: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    user: AuthUserResponse
    tokens: TokenResponse
