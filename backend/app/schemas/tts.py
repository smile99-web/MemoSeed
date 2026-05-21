from pydantic import BaseModel, Field


class SpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    language: str | None = None
    speech_rate: int | None = Field(default=None, ge=-50, le=100)
    x_api_key: str | None = None
    resource_id: str | None = None
    endpoint: str | None = None
    model: str | None = None


class KokoroSpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str
    api_url: str
    model: str = "kokoro"
    speed: float | None = Field(default=None, ge=0.25, le=4.0)

