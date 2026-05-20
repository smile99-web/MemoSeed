from typing import Any

from pydantic import BaseModel, Field


class ModelSettingsPayload(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)

