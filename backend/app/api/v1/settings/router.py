from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_model_settings import UserModelSettings
from app.schemas.settings import ModelSettingsPayload
from app.services.secure_model_settings import encrypt_model_settings, public_model_settings

router = APIRouter()


@router.get("/model", response_model=ModelSettingsPayload)
def get_model_settings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelSettingsPayload:
    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == current_user.id))
    except ProgrammingError as exc:
        raise_settings_table_missing_error(exc)
    if stored_settings is None:
        return ModelSettingsPayload(settings={})
    protected_settings = encrypt_model_settings(stored_settings.settings)
    if protected_settings != stored_settings.settings:
        stored_settings.settings = protected_settings
        db.commit()
        db.refresh(stored_settings)
    return ModelSettingsPayload(settings=public_model_settings(stored_settings.settings))


@router.put("/model", response_model=ModelSettingsPayload, status_code=status.HTTP_200_OK)
def save_model_settings(
    payload: ModelSettingsPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelSettingsPayload:
    settings = sanitize_model_settings(payload.settings)
    try:
        stored_settings = db.scalar(select(UserModelSettings).where(UserModelSettings.user_id == current_user.id))
    except ProgrammingError as exc:
        raise_settings_table_missing_error(exc)
    if stored_settings is None:
        stored_settings = UserModelSettings(user_id=current_user.id, settings=encrypt_model_settings(settings))
        db.add(stored_settings)
    else:
        stored_settings.settings = encrypt_model_settings(settings, stored_settings.settings)

    db.commit()
    db.refresh(stored_settings)
    return ModelSettingsPayload(settings=public_model_settings(stored_settings.settings))


def raise_settings_table_missing_error(exc: ProgrammingError) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Model settings table is missing. Apply database/init/003_user_model_settings.sql to enable server-side settings sync.",
    ) from exc


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
        "volcengineTtsApiKey",
        "volcengineTtsResourceId",
        "volcengineTtsModel",
        "llmApiKeyConfigured",
        "volcengineTtsApiKeyConfigured",
    }
    return {key: value for key, value in settings.items() if key in allowed_keys and isinstance(value, str)}

