from functools import lru_cache

from backend.config import Settings, get_settings
from backend.db.session import SessionLocal
from backend.repositories.user_repository import UserRepository
from backend.services.auth_service import AuthService
from backend.repositories.kb_repository import KnowledgeBaseRepository
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
from backend.services.rag_agent_service import RagAgentService
from backend.services.device_service import DeviceService
from backend.repositories.upload_repository import UploadRepository
from backend.services.upload_service import UploadService
import redis as redis_lib
import socket
from backend.config import get_settings as _get_settings
from backend.services.progress_event_bus import ProgressEventBus
from backend.websocket.manager import ConnectionManager


# ------------------------------------------------------------------
# WebSocket / 进度事件依赖
# ------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_progress_event_bus() -> ProgressEventBus:
    settings = _get_settings()
    r = redis_lib.Redis.from_url(settings.celery_broker_url)
    import socket
    return ProgressEventBus(redis_client=r, instance_id=socket.gethostname())

@lru_cache(maxsize=1)
def get_connection_manager() -> ConnectionManager:
    return ConnectionManager(event_bus=get_progress_event_bus())


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
def get_kb_service() -> KnowledgeBaseService:
    return KnowledgeBaseService(
        repository=get_kb_repository(),
        video_repository=get_video_resource_repository(),
    )


@lru_cache(maxsize=1)
def get_video_resource_repository() -> VideoResourceRepository:
    return VideoResourceRepository(db_session=SessionLocal())


@lru_cache(maxsize=1)
def get_video_resource_service() -> VideoResourceService:
    return VideoResourceService(repository=get_video_resource_repository())


@lru_cache(maxsize=1)
def get_video_summary_task_repository() -> VideoSummaryTaskRepository:
    return VideoSummaryTaskRepository(db_session=SessionLocal())


@lru_cache(maxsize=1)
def get_video_summary_task_service() -> VideoSummaryTaskService:
    return VideoSummaryTaskService(
        repository=get_video_summary_task_repository(),
        kb_repository=get_kb_repository(),
        video_repository=get_video_resource_repository(),
    )


@lru_cache(maxsize=1)
def get_video_qa_repository() -> VideoQARepository:
    return VideoQARepository(db_session=SessionLocal())


@lru_cache(maxsize=1)
def get_video_qa_service() -> VideoQAService:
    return VideoQAService(
        repository=get_video_qa_repository(),
        task_repository=get_video_summary_task_repository(),
        rag_agent_service=get_rag_agent_service(),
    )


@lru_cache(maxsize=1)
def get_global_chat_repository() -> GlobalChatRepository:
    return GlobalChatRepository(db_session=SessionLocal())


@lru_cache(maxsize=1)
def get_global_qa_repository() -> GlobalQARepository:
    return GlobalQARepository(db_session=SessionLocal())


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
        rag_agent_service=get_rag_agent_service(),
    )


@lru_cache(maxsize=1)
def get_rag_agent_service() -> RagAgentService:
    return RagAgentService()


@lru_cache(maxsize=1)
def get_upload_service() -> UploadService:
    """Create UploadService for TUS upload session orchestration."""
    redis_client = redis_lib.Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    return UploadService(repository=UploadRepository(redis_client=redis_client))


# ------------------------------------------------------------------
# FCM / Device 依赖
# ------------------------------------------------------------------
from backend.notifications.fcm_service import FCMService


@lru_cache(maxsize=1)
def get_fcm_service() -> FCMService:
    return FCMService()


# ------------------------------------------------------------------
# 工作流编排依赖
# ------------------------------------------------------------------
from backend.services.progress_publish_service import ProgressPublishService
from backend.services.task_status_service import TaskStatusService
from backend.services.workflow_orchestration_service import WorkflowOrchestrationService
from backend.services.workflow_notification_service import WorkflowNotificationService
from backend.repositories.device_repository import DeviceRepository


@lru_cache(maxsize=1)
def get_progress_publish_service() -> ProgressPublishService:
    """Create ProgressPublishService for unified progress event publishing."""
    return ProgressPublishService(
        event_bus=get_progress_event_bus(),
        instance_id=socket.gethostname(),
    )


@lru_cache(maxsize=1)
def get_task_status_service() -> TaskStatusService:
    """Create TaskStatusService for observable event tracking."""
    return TaskStatusService()


@lru_cache(maxsize=1)
def get_workflow_orchestration_service() -> WorkflowOrchestrationService:
    """Create WorkflowOrchestrationService for workflow execution."""
    return WorkflowOrchestrationService(
        task_repository=get_video_summary_task_repository(),
        video_repository=get_video_resource_repository(),
        progress_publisher=get_progress_publish_service(),
        task_status_service=get_task_status_service(),
        notification_service=get_workflow_notification_service(),
    )


@lru_cache(maxsize=1)
def get_device_repository() -> DeviceRepository:
    """Create DeviceRepository for FCM device token management."""
    return DeviceRepository(db_session=SessionLocal())


def get_device_service() -> DeviceService:
    """Create request-scoped DeviceService for device token operations."""
    db_session = SessionLocal()
    try:
        repository = DeviceRepository(db_session=db_session)
        yield DeviceService(repository=repository)
    finally:
        db_session.close()


@lru_cache(maxsize=1)
def get_workflow_notification_service() -> WorkflowNotificationService:
    """Create WorkflowNotificationService for FCM push orchestration."""
    return WorkflowNotificationService(
        fcm_service=get_fcm_service(),
        device_repository=get_device_repository(),
    )
