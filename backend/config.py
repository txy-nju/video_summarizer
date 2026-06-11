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

    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1",
        alias="CELERY_RESULT_BACKEND",
    )

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")

    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    oss_local_root: str = Field(default="temp/object_storage", alias="OSS_LOCAL_ROOT")
    oss_presign_ttl_seconds: int = Field(default=3600, alias="OSS_PRESIGN_TTL_SECONDS")
    public_api_base_url: str = Field(
        default="http://localhost:8000",
        alias="PUBLIC_API_BASE_URL",
        description="对外可访问的 API 基地址，用于生成附件的 HTTP 访问链接。",
    )

    # --- Observability/OTEL ---
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    otel_exporter: str = Field(default="jaeger", alias="OTEL_EXPORTER")  # jaeger/otlp
    otel_jaeger_endpoint: str = Field(default="http://localhost:14250", alias="OTEL_JAEGER_ENDPOINT")
    otel_otlp_endpoint: str = Field(default="http://localhost:4317", alias="OTEL_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="video-summarizer-backend", alias="OTEL_SERVICE_NAME")
    otel_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0, alias="OTEL_SAMPLE_RATIO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
