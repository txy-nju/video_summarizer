import logging
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.routes.auth_routes import router as auth_router
from backend.api.routes.kb_routes import router as kb_router
from backend.api.routes.video_resource_routes import router as video_resource_router
from backend.config import get_settings
from backend.logging import setup_logging
from backend.middleware.mobile_optimize import register_mobile_optimization

logger = logging.getLogger(__name__)


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


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)
    register_mobile_optimization(app)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        trace_id = request.headers.get("x-trace-id") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        request.state.user_id = request.headers.get("x-user-id", "anonymous")

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["x-request-id"] = request_id
        response.headers["x-trace-id"] = trace_id

        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "trace_id": trace_id,
                "user_id": request.state.user_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": getattr(request.state, "request_id", "-"),
                "trace_id": getattr(request.state, "trace_id", "-"),
                "user_id": getattr(request.state, "user_id", "-"),
            },
        )
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Server Error"})

    _register_routers(app)
    return app
