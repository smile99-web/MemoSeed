from functools import cached_property

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MemoSeed"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"

    backend_cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000", alias="BACKEND_CORS_ORIGINS")
    database_url: str = Field(
        default="postgresql+psycopg://memoseed:memoseed_password@postgres:5432/memoseed",
        alias="DATABASE_URL",
    )

    jwt_secret_key: str = Field(default="change_me_to_a_long_random_secret", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=15, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=30, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    ai_provider: str | None = Field(default=None, alias="AI_PROVIDER")
    ai_base_url: str | None = Field(default=None, alias="AI_BASE_URL")
    ai_api_key: str | None = Field(default=None, alias="AI_API_KEY")
    ai_model: str | None = Field(default=None, alias="AI_MODEL")

    tts_provider: str | None = Field(default=None, alias="TTS_PROVIDER")
    volcengine_tts_endpoint: str | None = Field(default=None, alias="VOLCENGINE_TTS_ENDPOINT")
    volcengine_tts_api_key: str | None = Field(default=None, alias="VOLCENGINE_TTS_API_KEY")
    volcengine_tts_resource_id: str | None = Field(default=None, alias="VOLCENGINE_TTS_RESOURCE_ID")
    volcengine_tts_model: str | None = Field(default=None, alias="VOLCENGINE_TTS_MODEL")
    volcengine_tts_english_voice: str | None = Field(default=None, alias="VOLCENGINE_TTS_ENGLISH_VOICE")
    volcengine_tts_chinese_voice: str | None = Field(default=None, alias="VOLCENGINE_TTS_CHINESE_VOICE")

    cosyvoice_base_url: str | None = Field(default=None, alias="COSYVOICE_BASE_URL")
    cosyvoice_english_speaker: str | None = Field(default=None, alias="COSYVOICE_ENGLISH_SPEAKER")
    cosyvoice_chinese_speaker: str | None = Field(default=None, alias="COSYVOICE_CHINESE_SPEAKER")

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        if self.app_env.lower() in {"production", "prod"} and self.jwt_secret_key == "change_me_to_a_long_random_secret":
            raise ValueError("JWT_SECRET_KEY must be changed before running in production")
        return self

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


settings = Settings()
