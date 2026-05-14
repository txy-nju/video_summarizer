from functools import lru_cache

from backend.config import Settings, get_settings
from backend.db.session import SessionLocal
from backend.repositories.user_repository import UserRepository
from backend.services.auth_service import AuthService
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.repositories.kb_video_relation_repository import KBVideoRelationRepository
from backend.services.kb_service import KnowledgeBaseService
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.services.video_resource_service import VideoResourceService
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
from backend.services.video_summary_task_service import VideoSummaryTaskService
from backend.repositories.video_qa_repository import VideoQARepository
from backend.services.video_qa_service import VideoQAService
from backend.repositories.global_chat_repository import GlobalChatRepository
from backend.repositories.global_qa_repository import GlobalQARepository
from backend.services.global_chat_service import GlobalChatService
from backend.services.global_qa_service import GlobalQAService


def get_app_settings() -> Settings:
    """Global dependency hook for configuration access."""
    return get_settings()


@lru_cache(maxsize=1)
def get_user_repository() -> UserRepository:
    """Create UserRepository with a new database session."""
    db_session = SessionLocal()
    return UserRepository(db_session=db_session)


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """Create AuthService with UserRepository dependency."""
    return AuthService(
        user_repository=get_user_repository(),
        settings=get_settings(),
    )


@lru_cache(maxsize=1)
def get_kb_repository() -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(db_session=SessionLocal())


@lru_cache(maxsize=1)
def get_kb_video_relation_repository() -> KBVideoRelationRepository:
    return KBVideoRelationRepository()


@lru_cache(maxsize=1)
def get_kb_service() -> KnowledgeBaseService:
    return KnowledgeBaseService(
        repository=get_kb_repository(),
        video_repository=get_video_resource_repository(),
        kb_video_relation_repository=get_kb_video_relation_repository(),
    )


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


@lru_cache(maxsize=1)
def get_video_qa_repository() -> VideoQARepository:
    return VideoQARepository()


@lru_cache(maxsize=1)
def get_video_qa_service() -> VideoQAService:
    return VideoQAService(
        repository=get_video_qa_repository(),
        task_repository=get_video_summary_task_repository(),
    )


@lru_cache(maxsize=1)
def get_global_chat_repository() -> GlobalChatRepository:
    return GlobalChatRepository()


@lru_cache(maxsize=1)
def get_global_qa_repository() -> GlobalQARepository:
    return GlobalQARepository()


@lru_cache(maxsize=1)
def get_global_chat_service() -> GlobalChatService:
    return GlobalChatService(
        repository=get_global_chat_repository(),
        kb_repository=get_kb_repository(),
        qa_repository=get_global_qa_repository(),
    )


@lru_cache(maxsize=1)
def get_global_qa_service() -> GlobalQAService:
    return GlobalQAService(
        repository=get_global_qa_repository(),
        chat_repository=get_global_chat_repository(),
    )
