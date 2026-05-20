from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Request


DEFAULT_INTERNAL_ERROR_CODE = "SYSTEM_RUNTIME_INTERNAL_ERROR"


@dataclass(slots=True)
class AppError(Exception):
    """业务异常基类，统一映射为稳定错误码响应。"""

    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)
    is_retryable: bool = False
    retry_after: int | None = None


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
