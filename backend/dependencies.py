from functools import lru_cache

from backend.config import Settings, get_settings
from backend.services.auth_service import AuthService
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.services.kb_service import KnowledgeBaseService


def get_app_settings() -> Settings:
    """Global dependency hook for configuration access."""

    return get_settings()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(settings=get_settings())


@lru_cache(maxsize=1)
def get_kb_repository() -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository()


@lru_cache(maxsize=1)
def get_kb_service() -> KnowledgeBaseService:
    return KnowledgeBaseService(repository=get_kb_repository())
