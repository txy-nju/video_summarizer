from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="video-summarizer-backend", alias="APP_NAME")
    app_env: str = Field(default="dev", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], alias="CORS_ORIGINS")

    jwt_secret_key: str = Field(default="change-me-access", alias="JWT_SECRET_KEY")
    jwt_refresh_secret_key: str = Field(default="change-me-refresh", alias="JWT_REFRESH_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expires_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRES_MINUTES")
    jwt_refresh_token_expires_minutes: int = Field(default=10080, alias="JWT_REFRESH_TOKEN_EXPIRES_MINUTES")

    database_url: str = Field(
        default="postgresql+psycopg2://postgres:123456@localhost:5432/video_summarizer_test",
        alias="DATABASE_URL",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
