from functools import lru_cache

from backend.config import Settings, get_settings
from backend.services.auth_service import AuthService
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.services.kb_service import KnowledgeBaseService
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.services.video_resource_service import VideoResourceService
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
from backend.services.video_summary_task_service import VideoSummaryTaskService


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


@lru_cache(maxsize=1)
def get_video_resource_repository() -> VideoResourceRepository:
    return VideoResourceRepository()


@lru_cache(maxsize=1)
def get_video_resource_service() -> VideoResourceService:
    return VideoResourceService(repository=get_video_resource_repository())


@lru_cache(maxsize=1)
def get_video_summary_task_repository() -> VideoSummaryTaskRepository:
    return VideoSummaryTaskRepository()


@lru_cache(maxsize=1)
def get_video_summary_task_service() -> VideoSummaryTaskService:
    return VideoSummaryTaskService(
        repository=get_video_summary_task_repository(),
        kb_repository=get_kb_repository(),
        video_repository=get_video_resource_repository(),
    )
