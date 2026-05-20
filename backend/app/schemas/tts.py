from pydantic import BaseModel, Field


class SpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    language: str | None = None
    app_id: str | None = None
    access_token: str | None = None
    secret_key: str | None = None
    resource_id: str | None = None
    endpoint: str | None = None
    model: str | None = None

