from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_model_settings import UserModelSettings
from app.schemas.settings import ModelSettingsPayload

router = APIRouter()


@router.get("/model", response_model=ModelSettingsPayload)
def get_model_settings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelSettingsPayload:
    stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == current_user.id))
    if stored_settings is None:
        return ModelSettingsPayload(settings={})
    return ModelSettingsPayload(settings=stored_settings.settings)


@router.put("/model", response_model=ModelSettingsPayload, status_code=status.HTTP_200_OK)
def save_model_settings(
    payload: ModelSettingsPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelSettingsPayload:
    settings = sanitize_model_settings(payload.settings)
    stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == current_user.id))
    if stored_settings is None:
        stored_settings = UserModelSettings(user_id=current_user.id, settings=settings)
        db.add(stored_settings)
    else:
        stored_settings.settings = settings

    db.commit()
    db.refresh(stored_settings)
    return ModelSettingsPayload(settings=stored_settings.settings)


def sanitize_model_settings(settings: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "modelMode",
        "llmProvider",
        "llmBaseUrl",
        "llmModel",
        "llmApiKey",
        "ttsProvider",
        "ttsApiUrl",
        "ttsDocsUrl",
        "ttsWebUrl",
        "ttsEnglishVoice",
        "ttsChineseVoice",
        "volcengineTtsEndpoint",
        "volcengineTtsAppId",
        "volcengineTtsAccessToken",
        "volcengineTtsSecretKey",
        "volcengineTtsResourceId",
        "volcengineTtsModel",
    }
    return {key: value for key, value in settings.items() if key in allowed_keys and isinstance(value, str)}

