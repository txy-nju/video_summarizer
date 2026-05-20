from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.exceptions import (
    DEFAULT_INTERNAL_ERROR_CODE,
    AppError,
    build_error_response,
)

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        payload = build_error_response(
            request=request,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            is_retryable=exc.is_retryable,
            retry_after=exc.retry_after,
        )
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        payload = build_error_response(
            request=request,
            code="REQUEST_VALIDATE_INVALID_PAYLOAD",
            message="Request validation failed",
            details={"errors": exc.errors()},
            is_retryable=False,
            retry_after=None,
        )
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        code = "HTTP_REQUEST_FAILED"
        message = str(exc.detail) if exc.detail else "Request failed"
        payload = build_error_response(
            request=request,
            code=code,
            message=message,
            details={},
            is_retryable=False,
            retry_after=None,
        )
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": getattr(request.state, "request_id", "-"),
                "trace_id": getattr(request.state, "trace_id", "-"),
                "user_id": getattr(request.state, "user_id", "-"),
            },
        )
        payload = build_error_response(
            request=request,
            code=DEFAULT_INTERNAL_ERROR_CODE,
            message="Internal Server Error",
            details={},
            is_retryable=True,
            retry_after=5,
        )
        return JSONResponse(status_code=500, content=payload)
