from functools import lru_cache

from backend.config import Settings, get_settings
from backend.services.auth_service import AuthService


def get_app_settings() -> Settings:
    """Global dependency hook for configuration access."""

    return get_settings()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(settings=get_settings())
