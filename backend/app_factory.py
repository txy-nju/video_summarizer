from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import FileResponse

from backend.api.routes.auth_routes import router as auth_router
from backend.api.routes.attachment_upload import router as attachment_upload_router
from backend.api.routes.kb_routes import router as kb_router
from backend.api.routes.video_summary_task_routes import router as video_summary_task_router
from backend.api.routes.video_qa_routes import router as video_qa_router
from backend.api.routes.video_resource_routes import router as video_resource_router
from backend.api.routes.global_chat_routes import router as global_chat_router
from backend.api.routes.global_qa_routes import router as global_qa_router
from backend.api.routes.file_upload import router as file_upload_router
from backend.api.routes.devices import router as devices_router
from backend.websocket.handlers import router as websocket_router
from backend.config import get_settings
from backend.logging import setup_logging
from backend.middleware.access_log import register_access_log_middleware
from backend.middleware.error_handler import register_error_handlers
from backend.middleware.mobile_optimize import register_mobile_optimization
from backend.middleware.otel_middleware import register_otel_middleware
from backend.middleware.request_context import register_request_context_middleware
from backend.observability.tracing import configure_tracing
from backend.dependencies import get_connection_manager
from backend.exceptions import ErrorCode, NotFoundError

def _build_system_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # ── 本地开发文件流 ──
    # Android 模拟器 / 非本地客户端无法访问 file:// 协议的 presigned_url，
    # 因此提供一个 HTTP 流式传输端点。
    @router.get("/api/v1/files/stream", tags=["system"])
    async def stream_file(
        object_key: str = Query(..., description="OSS object key of the file to stream"),
    ) -> FileResponse:
        from backend.infrastructure.storage.oss_client import get_object_storage_client

        storage = get_object_storage_client()
        file_path: Path = storage._local_root / storage._normalize_key(object_key)
        if not file_path.exists():
            raise NotFoundError(code=ErrorCode.SYSTEM_STORAGE_FILE_NOT_FOUND, message="File not found")
        # 不指定 media_type，由 Starlette 根据文件扩展名自动检测正确 MIME 类型
        # （如 .mp4 → video/mp4），ExoPlayer 需要正确的 Content-Type 才能初始化解码器。
        # FileResponse 默认支持 HTTP Range 请求（206 Partial Content）。
        return FileResponse(
            file_path,
            filename=file_path.name,
        )

    return router


def _register_routers(app: FastAPI) -> None:
    app.include_router(_build_system_router())
    app.include_router(auth_router)
    app.include_router(attachment_upload_router)
    app.include_router(kb_router)
    app.include_router(video_resource_router)
    app.include_router(video_summary_task_router)
    app.include_router(video_qa_router)
    app.include_router(global_chat_router)
    app.include_router(global_qa_router)
    app.include_router(file_upload_router)
    app.include_router(devices_router)
    app.include_router(websocket_router)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    try:
        yield
    finally:
        # 在应用关闭时停止后台 Redis Pub/Sub 监听线程，避免测试进程退出时悬挂。
        get_connection_manager().stop_listening()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    configure_tracing(
        enabled=settings.otel_enabled,
        service_name=settings.otel_service_name,
        exporter=settings.otel_exporter,
        sample_ratio=settings.otel_sample_ratio,
        jaeger_endpoint=settings.otel_jaeger_endpoint,
        otlp_endpoint=settings.otel_otlp_endpoint,
    )

    app = FastAPI(title=settings.app_name, lifespan=_app_lifespan)
    register_mobile_optimization(app)
    register_access_log_middleware(app)
    register_request_context_middleware(app)
    register_otel_middleware(app)
    register_error_handlers(app)

    _register_routers(app)
    return app
