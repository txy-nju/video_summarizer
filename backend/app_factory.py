from fastapi import APIRouter, FastAPI

from backend.api.routes.auth_routes import router as auth_router
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

def _build_system_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return router


def _register_routers(app: FastAPI) -> None:
    app.include_router(_build_system_router())
    app.include_router(auth_router)
    app.include_router(kb_router)
    app.include_router(video_resource_router)
    app.include_router(video_summary_task_router)
    app.include_router(video_qa_router)
    app.include_router(global_chat_router)
    app.include_router(global_qa_router)
    app.include_router(file_upload_router)
    app.include_router(devices_router)
    app.include_router(websocket_router)


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

    app = FastAPI(title=settings.app_name)
    register_mobile_optimization(app)
    register_access_log_middleware(app)
    register_request_context_middleware(app)
    register_otel_middleware(app)
    register_error_handlers(app)

    _register_routers(app)
    return app
