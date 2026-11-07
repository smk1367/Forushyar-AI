from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "Smart Sales AI API"
    app_version: str = "0.2.0"
    api_key: str = Field(min_length=24)
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

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


@lru_cache

def get_settings() -> Settings:
    return Settings()


settings = get_settings()
