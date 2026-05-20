from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def register_access_log_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        started_at = getattr(request.state, "_request_started_at", time.perf_counter())

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request_failed",
                extra={
                    "request_id": getattr(request.state, "request_id", "-"),
                    "trace_id": getattr(request.state, "trace_id", "-"),
                    "user_id": getattr(request.state, "user_id", "anonymous"),
                    "path": request.url.path,
                    "method": request.method,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "request_completed",
            extra={
                "request_id": getattr(request.state, "request_id", "-"),
                "trace_id": getattr(request.state, "trace_id", "-"),
                "user_id": getattr(request.state, "user_id", "anonymous"),
                "path": request.url.path,
                "method": request.method,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
