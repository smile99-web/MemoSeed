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
    # Access token default: 8h. Long enough that children on iPads rarely hit a
    # mid-session refresh (which can race across devices and log them out),
    # short enough to bound compromise. Override via JWT_ACCESS_TOKEN_EXPIRE_MINUTES.
    jwt_access_token_expire_minutes: int = Field(default=480, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    # Refresh token default: 90d. A child may not log in for weeks; we don't
    # want a hard 30-day logout. Multiple refresh tokens per user are allowed
    # (one per device) so login on a new device does NOT kick older devices.
    jwt_refresh_token_expire_days: int = Field(default=90, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    # Optional registration invite code. Empty (default) = open registration
    # (dev/tests). Set INVITE_CODE in production to stop strangers from
    # registering and burning the server's paid TTS/LLM keys.
    invite_code: str = Field(default="", alias="INVITE_CODE")

    ai_provider: str | None = Field(default=None, alias="AI_PROVIDER")
    ai_base_url: str | None = Field(default=None, alias="AI_BASE_URL")
    ai_api_key: str | None = Field(default=None, alias="AI_API_KEY")
    ai_model: str | None = Field(default=None, alias="AI_MODEL")

    # System-level LLM fallback, tried when the per-user primary LLM call
    # fails (quota exhausted, provider outage, bad key...). Lives in env so
    # it survives settings-page saves (which replace the whole per-user
    # settings blob). 2026-07-30: primary = Agent Plan (ark runtime), this
    # fallback = the previous DeepSeek direct config.
    ai_fallback_provider: str | None = Field(default=None, alias="AI_FALLBACK_PROVIDER")
    ai_fallback_base_url: str | None = Field(default=None, alias="AI_FALLBACK_BASE_URL")
    ai_fallback_api_key: str | None = Field(default=None, alias="AI_FALLBACK_API_KEY")
    ai_fallback_model: str | None = Field(default=None, alias="AI_FALLBACK_MODEL")

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

    tts_cache_dir: str | None = Field(default=None, alias="TTS_CACHE_DIR")

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        if self.jwt_secret_key == "change_me_to_a_long_random_secret":
            # 2026-08-16: previously the default secret was only rejected when
            # APP_ENV was literally "production"/"prod" — any other value
            # ("staging", a typo, unset with a prod .env) ran with a publicly
            # known secret, i.e. forgeable tokens for every account. Now the
            # default is only tolerated in explicit dev/test, and even there
            # it logs a warning.
            if self.app_env.lower() not in {"development", "dev", "test", "testing"}:
                raise ValueError("JWT_SECRET_KEY must be changed before running outside development/test")
            import logging
            logging.getLogger(__name__).warning(
                "JWT_SECRET_KEY is the built-in default — acceptable only for local development/tests"
            )
        return self

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


settings = Settings()
