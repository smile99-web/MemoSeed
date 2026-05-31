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


class CosyVoiceSpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1)
    speaker: str = Field(min_length=1)
    api_url: str


class CachedSpeechRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    speech_rate: int = Field(default=0, ge=-50, le=100)
    suffix: str = Field(default="mp3", pattern=r"^(mp3|wav)$")


class PrefetchCourseAudioRequest(BaseModel):
    course_id: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    language: str | None = None
    speech_rate: int = Field(default=0, ge=-50, le=100)


class PrefetchCourseAudioResponse(BaseModel):
    course_id: str
    words: dict[str, str]
    cache_hits: int
    cache_misses: int


class PhonicsDeckItem(BaseModel):
    phoneme_key: str
    display_label: str
    synth_text: str
    audio_url: str


class PhonicsDeckResponse(BaseModel):
    phonemes: list[PhonicsDeckItem]

