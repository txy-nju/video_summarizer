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

# ── Core module imports ──────────────────────────────────────────────
from core.context.message_builder import MessageBuilder
from core.memory.hybrid import HybridChatMemory
from core.tool.base import ToolDefinition
from core.tool.registry import ToolRegistry
from core.tool.executor import ToolExecutor
from core.tool.builtin.rag_search import _build_rag_search_tool
from core.agent.qa_agent import QAAgent


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
    """Create AuthService with UserRepository and KnowledgeBaseRepository dependencies."""
    return AuthService(
        user_repository=get_user_repository(),
        settings=get_settings(),
        kb_repository=get_kb_repository(),
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


# ── Chat Memory / Agent 依赖 ──────────────────────────────────────

@lru_cache(maxsize=1)
def _get_redis_client_for_memory() -> redis_lib.Redis:
    """Create a Redis client for chat memory caching (reuses broker URL, DB 4)."""
    settings = _get_settings()
    url = settings.celery_broker_url
    if "/" in url:
        base, db = url.rsplit("/", 1)
        memory_url = f"{base}/4"
    else:
        memory_url = f"{url}/4"
    return redis_lib.Redis.from_url(memory_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_chat_memory() -> HybridChatMemory:
    """Create HybridChatMemory (DB + Redis cache)."""
    return HybridChatMemory(
        qa_repository=get_global_qa_repository(),
        redis_client=_get_redis_client_for_memory(),
        message_builder=MessageBuilder(),
    )


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Create ToolRegistry with all built-in tools registered."""
    registry = ToolRegistry()
    rag_tool = _build_rag_search_tool(get_rag_agent_service())
    registry.register(rag_tool)
    return registry


def _default_permission_checker(
    tool_name: str,
    required_permissions: list[str],
    context,
) -> bool:
    """Permission checker that validates kb:read against the knowledge base."""
    if "kb:read" in required_permissions:
        kb_repo = get_kb_repository()
        kb = kb_repo.get_by_owner_and_id(context.owner_id, context.kbid)
        return kb is not None
    return True


@lru_cache(maxsize=1)
def get_tool_executor() -> ToolExecutor:
    """Create ToolExecutor with registry and permission checker."""
    return ToolExecutor(
        registry=get_tool_registry(),
        permission_checker=_default_permission_checker,
    )


@lru_cache(maxsize=1)
def get_qa_agent() -> QAAgent:
    """Create QAAgent with memory, tools, and LLM."""
    from core.llm.rag_llm import RagStreamLLM
    return QAAgent(
        memory=get_chat_memory(),
        tool_registry=get_tool_registry(),
        tool_executor=get_tool_executor(),
        rag_stream_llm=RagStreamLLM.from_env(),
    )


@lru_cache(maxsize=1)
def get_global_qa_service() -> GlobalQAService:
    return GlobalQAService(
        repository=get_global_qa_repository(),
        chat_repository=get_global_chat_repository(),
        qa_agent=get_qa_agent(),
        chat_memory=get_chat_memory(),
    )


@lru_cache(maxsize=1)
def get_rag_agent_service() -> RagAgentService:
    return RagAgentService()


@lru_cache(maxsize=1)
def get_upload_service() -> UploadService:
    """Create UploadService for TUS upload session orchestration."""
    import os
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
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
