from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.auth import AuthUserResponse
from app.schemas.common import MessageResponse

router = APIRouter()


@router.get("/me", response_model=AuthUserResponse)
def get_current_user_profile(current_user: Annotated[User, Depends(get_current_user)]) -> AuthUserResponse:
    return AuthUserResponse.model_validate(current_user)


@router.get("/{user_id}", response_model=MessageResponse)
def get_user(user_id: UUID) -> MessageResponse:
    return MessageResponse(message=f"User endpoint ready for {user_id}")
