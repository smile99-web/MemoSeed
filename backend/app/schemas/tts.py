from pydantic import BaseModel, Field


class SpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    language: str | None = None
    x_api_key: str | None = None
    resource_id: str | None = None
    endpoint: str | None = None
    model: str | None = None


class KokoroSpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str
    api_url: str
    model: str = "kokoro"

