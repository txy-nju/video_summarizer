from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Request


# =============================================================================
# Error Code Catalog — 集中管理的语义错误码
# =============================================================================
# Naming convention: {DOMAIN}_{SPECIFIC_ERROR}
# Once published, codes MUST NOT change — frontends depend on them.
# =============================================================================


class ErrorCode:
    """Semantic error codes for every business error the API can produce.

    Organised by domain. Add new codes here; avoid adding new
    bare ``HTTPException`` raises in route or service code.
    """

    # ── AUTH: Authentication & Authorization ──────────────────────────
    AUTH_MISSING_TOKEN = "AUTH_MISSING_TOKEN"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    AUTH_INVALID_TOKEN_TYPE = "AUTH_INVALID_TOKEN_TYPE"
    AUTH_DEVICE_MISMATCH = "AUTH_DEVICE_MISMATCH"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_USERNAME_ALREADY_EXISTS = "AUTH_USERNAME_ALREADY_EXISTS"
    AUTH_INSUFFICIENT_PERMISSIONS = "AUTH_INSUFFICIENT_PERMISSIONS"
    AUTH_USER_NOT_FOUND = "AUTH_USER_NOT_FOUND"

    # ── KB: Knowledge Base ────────────────────────────────────────────
    KB_NOT_FOUND = "KB_NOT_FOUND"
    KB_ACCESS_DENIED = "KB_ACCESS_DENIED"
    KB_DUPLICATE_VIDEO = "KB_DUPLICATE_VIDEO"
    KB_VIDEO_NOT_IN_KB = "KB_VIDEO_NOT_IN_KB"
    KB_VIDEO_BIND_FAILED = "KB_VIDEO_BIND_FAILED"
    KB_DELETE_FAILED = "KB_DELETE_FAILED"

    # ── VIDEO: Video Resource ─────────────────────────────────────────
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    VIDEO_NOT_READY = "VIDEO_NOT_READY"
    VIDEO_DELETE_FAILED = "VIDEO_DELETE_FAILED"
    VIDEO_ACCESS_DENIED = "VIDEO_ACCESS_DENIED"
    VIDEO_FILE_NOT_FOUND = "VIDEO_FILE_NOT_FOUND"

    # ── TASK: Video Summary Task ──────────────────────────────────────
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_DUPLICATE_VIDEO_IN_KB = "TASK_DUPLICATE_VIDEO_IN_KB"
    TASK_ACCESS_DENIED = "TASK_ACCESS_DENIED"
    TASK_INVALID_STATE_TRANSITION = "TASK_INVALID_STATE_TRANSITION"
    TASK_FINALIZATION_IN_PROGRESS = "TASK_FINALIZATION_IN_PROGRESS"
    TASK_WORKFLOW_START_FAILED = "TASK_WORKFLOW_START_FAILED"
    TASK_CLONE_SOURCE_NOT_FOUND = "TASK_CLONE_SOURCE_NOT_FOUND"
    TASK_CLONE_TARGET_KB_NOT_FOUND = "TASK_CLONE_TARGET_KB_NOT_FOUND"
    TASK_APPROVE_FAILED = "TASK_APPROVE_FAILED"
    TASK_DELETE_FAILED = "TASK_DELETE_FAILED"

    # ── QA: Video QA ──────────────────────────────────────────────────
    QA_RECORD_NOT_FOUND = "QA_RECORD_NOT_FOUND"
    QA_TASK_NOT_FOUND = "QA_TASK_NOT_FOUND"
    QA_AGENT_NOT_CONFIGURED = "QA_AGENT_NOT_CONFIGURED"
    QA_ACCESS_DENIED = "QA_ACCESS_DENIED"
    QA_DELETE_FAILED = "QA_DELETE_FAILED"
    QA_STREAM_ERROR = "QA_STREAM_ERROR"

    # ── GQA: Global QA ────────────────────────────────────────────────
    GQA_RECORD_NOT_FOUND = "GQA_RECORD_NOT_FOUND"
    GQA_ACCESS_DENIED = "GQA_ACCESS_DENIED"
    GQA_DELETE_FAILED = "GQA_DELETE_FAILED"
    GQA_STREAM_ERROR = "GQA_STREAM_ERROR"

    # ── CHAT: Global Chat ─────────────────────────────────────────────
    CHAT_SESSION_NOT_FOUND = "CHAT_SESSION_NOT_FOUND"
    CHAT_ACCESS_DENIED = "CHAT_ACCESS_DENIED"
    CHAT_DELETE_FAILED = "CHAT_DELETE_FAILED"

    # ── UPLOAD: File Chunk Upload ─────────────────────────────────────
    UPLOAD_SESSION_NOT_FOUND = "UPLOAD_SESSION_NOT_FOUND"
    UPLOAD_SESSION_NOT_OWNER = "UPLOAD_SESSION_NOT_OWNER"
    UPLOAD_SESSION_TERMINAL_STATE = "UPLOAD_SESSION_TERMINAL_STATE"
    UPLOAD_CHUNK_INDEX_OUT_OF_RANGE = "UPLOAD_CHUNK_INDEX_OUT_OF_RANGE"
    UPLOAD_CHUNK_SIZE_MISMATCH = "UPLOAD_CHUNK_SIZE_MISMATCH"
    UPLOAD_CHUNK_BODY_EMPTY = "UPLOAD_CHUNK_BODY_EMPTY"
    UPLOAD_FINALIZE_FAILED = "UPLOAD_FINALIZE_FAILED"

    # ── ATTACH: Attachment Upload ─────────────────────────────────────
    ATTACH_FILE_TOO_LARGE = "ATTACH_FILE_TOO_LARGE"
    ATTACH_UNSUPPORTED_TYPE = "ATTACH_UNSUPPORTED_TYPE"
    ATTACH_FILE_EMPTY = "ATTACH_FILE_EMPTY"
    ATTACH_UPLOAD_FAILED = "ATTACH_UPLOAD_FAILED"

    # ── DEVICE: Device Management ─────────────────────────────────────
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_NOT_OWNER = "DEVICE_NOT_OWNER"
    DEVICE_REGISTER_FAILED = "DEVICE_REGISTER_FAILED"
    DEVICE_UNREGISTER_FAILED = "DEVICE_UNREGISTER_FAILED"
    DEVICE_LIST_FAILED = "DEVICE_LIST_FAILED"

    # ── REQUEST: Request Validation ───────────────────────────────────
    REQUEST_VALIDATE_INVALID_PAYLOAD = "REQUEST_VALIDATE_INVALID_PAYLOAD"
    REQUEST_UNSUPPORTED_FIELDS = "REQUEST_UNSUPPORTED_FIELDS"
    REQUEST_INVALID_QUERY_PARAM = "REQUEST_INVALID_QUERY_PARAM"

    # ── SYSTEM: System / Infrastructure ───────────────────────────────
    SYSTEM_RUNTIME_INTERNAL_ERROR = "SYSTEM_RUNTIME_INTERNAL_ERROR"
    SYSTEM_STORAGE_BACKEND_ERROR = "SYSTEM_STORAGE_BACKEND_ERROR"
    SYSTEM_STORAGE_FILE_NOT_FOUND = "SYSTEM_STORAGE_FILE_NOT_FOUND"
    SYSTEM_DATABASE_ERROR = "SYSTEM_DATABASE_ERROR"
    SYSTEM_SERVICE_UNAVAILABLE = "SYSTEM_SERVICE_UNAVAILABLE"


# =============================================================================
# Legacy aliases — kept for backward compatibility with existing code
# =============================================================================

DEFAULT_INTERNAL_ERROR_CODE = ErrorCode.SYSTEM_RUNTIME_INTERNAL_ERROR

# Deprecated — still produced by handle_http_exception for third-party
# HTTPException raises (Starlette built-ins). New business code should
# use a domain-specific ErrorCode instead.
DEPRECATED_HTTP_REQUEST_FAILED = "HTTP_REQUEST_FAILED"


# =============================================================================
# AppError — unified business exception base class
# =============================================================================


@dataclass(slots=True)
class AppError(Exception):
    """业务异常基类，统一映射为稳定错误码响应。"""

    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)
    is_retryable: bool = False
    retry_after: int | None = None


# =============================================================================
# Domain exception subclasses — each pre-binds a default HTTP status code.
# Service-layer code should raise these instead of bare ValueError / HTTPException.
# =============================================================================


class AuthError(AppError):
    """Authentication / authorization error (default 401)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 401,
        details: dict[str, Any] | None = None,
        is_retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


class ForbiddenError(AppError):
    """Permission denied error (default 403)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 403,
        details: dict[str, Any] | None = None,
        is_retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


class NotFoundError(AppError):
    """Resource not found error (default 404)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 404,
        details: dict[str, Any] | None = None,
        is_retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


class ConflictError(AppError):
    """Resource conflict error (default 409)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
        is_retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


class ValidationError(AppError):
    """Validation / unprocessable entity error (default 422)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
        is_retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


class ServiceError(AppError):
    """Internal service / infrastructure error (default 500)."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
        is_retryable: bool = True,
        retry_after: int | None = 5,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details or {},
            is_retryable=is_retryable,
            retry_after=retry_after,
        )


# =============================================================================
# Response builders
# =============================================================================


def build_error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    is_retryable: bool = False,
    retry_after: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details or {},
        "is_retryable": is_retryable,
        "retry_after": retry_after,
    }
    return payload


def build_error_response(
    *,
    request: Request,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    is_retryable: bool = False,
    retry_after: int | None = None,
) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", "-")
    return {
        "status": "error",
        "data": None,
        "error": build_error_payload(
            code=code,
            message=message,
            details=details,
            is_retryable=is_retryable,
            retry_after=retry_after,
        ),
        "meta": {
            "request_id": request_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    }
