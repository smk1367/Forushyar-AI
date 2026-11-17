import ipaddress
import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "Forushyar AI API"
    app_version: str = "0.7.4"
    api_key: str = Field(min_length=24)
    session_secret: str = Field(min_length=40)
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Any OpenAI-compatible endpoint is supported, including self-hosted gateways.
    # Keep these empty until an LLM provider has been selected and configured.
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=45.0, ge=5.0, le=180.0)
    llm_max_tokens: int = Field(default=160, ge=32, le=512)
    assistant_history_limit: int = Field(default=12, ge=2, le=40)

    # Grandstream UCM6202 integration is opt-in; no credentials are committed.
    ucm_enabled: bool = False
    ucm_api_url: str | None = None
    ucm_api_username: str | None = None
    ucm_api_password: str | None = None
    ucm_cdr_url: str | None = None
    ucm_recording_url: str | None = None
    ucm_cdr_username: str | None = None
    ucm_cdr_password: str | None = None
    ucm_verify_tls: bool = True
    ucm_sync_interval_seconds: int = Field(default=60, ge=15, le=3600)
    ucm_live_poll_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    ucm_cdr_page_size: int = Field(default=500, ge=1, le=1000)
    ucm_cdr_max_pages: int = Field(default=20, ge=1, le=100)
    ucm_webhook_username: str | None = None
    ucm_webhook_password: str | None = None
    ucm_tcp_listener_enabled: bool = False
    ucm_tcp_host: str = "0.0.0.0"
    ucm_tcp_port: int = Field(default=10000, ge=1024, le=65535)
    ucm_tcp_allowed_ips: list[str] = Field(default_factory=list)

    @field_validator("ucm_tcp_allowed_ips", mode="before")
    @classmethod
    def parse_ucm_allowed_ips(cls, value):
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                value = parsed if isinstance(parsed, list) else [value]
            except json.JSONDecodeError:
                value = [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("ucm_tcp_allowed_ips")
    @classmethod
    def validate_ucm_allowed_ips(cls, values):
        return [str(ipaddress.ip_address(value)) for value in values]

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        return value.lower().strip()

    @field_validator("api_key")
    @classmethod
    def reject_placeholder_api_key_in_production(cls, value: str, info):
        app_env = info.data.get("app_env", "development")
        if app_env == "production" and (len(value) < 40 or "change_me" in value or "replace" in value):
            raise ValueError("Production API_KEY must be a unique random secret of at least 40 characters")
        return value

    @property
    def llm_is_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
