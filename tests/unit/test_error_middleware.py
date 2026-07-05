from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.exceptions import (
    AppError,
    AuthError,
    ConflictError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ServiceError,
    ValidationError,
)
from backend.middleware.access_log import register_access_log_middleware
from backend.middleware.error_handler import register_error_handlers
from backend.middleware.request_context import register_request_context_middleware


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_request_context_middleware(app)
    register_access_log_middleware(app)
    register_error_handlers(app)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/domain-error")
    async def domain_error() -> dict[str, str]:
        raise AppError(
            code="VIDEO_TASK_QUERY_NOT_FOUND",
            message="Task not found",
            status_code=404,
            details={"task_id": "task_001"},
            is_retryable=False,
            retry_after=None,
        )

    @app.get("/unexpected")
    async def unexpected() -> dict[str, str]:
        raise RuntimeError("boom")

    @app.get("/http-error")
    async def http_error() -> dict[str, str]:
        raise HTTPException(status_code=403, detail="forbidden")

    @app.get("/validate")
    async def validate(page: int) -> dict[str, int]:
        return {"page": page}

    return app


def test_request_context_headers_are_set() -> None:
    client = TestClient(_build_test_app())

    response = client.get("/ok")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")
    assert response.headers.get("x-trace-id")


def test_request_context_reuses_incoming_request_headers() -> None:
    client = TestClient(_build_test_app())

    response = client.get(
        "/ok",
        headers={
            "x-request-id": "req-123",
            "x-trace-id": "trc-123",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-123"
    assert response.headers["x-trace-id"] == "trc-123"


def test_domain_error_uses_unified_error_payload() -> None:
    client = TestClient(_build_test_app())

    response = client.get("/domain-error")
    body = response.json()

    assert response.status_code == 404
    assert body["status"] == "error"
    assert body["data"] is None
    assert body["error"]["code"] == "VIDEO_TASK_QUERY_NOT_FOUND"
    assert body["error"]["details"]["task_id"] == "task_001"
    assert body["meta"]["request_id"]
    assert body["meta"]["timestamp"]


def test_unexpected_error_hides_stack_from_client() -> None:
    app = _build_test_app()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/unexpected")
    body = response.json()

    assert response.status_code == 500
    assert body["status"] == "error"
    assert body["error"]["code"] == "SYSTEM_RUNTIME_INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal Server Error"
    assert "exception" not in body


def test_http_exception_maps_to_unified_payload() -> None:
    app = _build_test_app()
    client = TestClient(app)

    response = client.get("/http-error")
    body = response.json()

    assert response.status_code == 403
    assert body["status"] == "error"
    assert body["error"]["code"] == "HTTP_REQUEST_FAILED"
    assert body["error"]["message"] == "forbidden"


def test_validation_error_maps_to_unified_payload() -> None:
    app = _build_test_app()
    client = TestClient(app)

    response = client.get("/validate", params={"page": "oops"})
    body = response.json()

    assert response.status_code == 422
    assert body["status"] == "error"
    assert body["error"]["code"] == "REQUEST_VALIDATE_INVALID_PAYLOAD"
    assert body["error"]["details"]["errors"]


# ── Domain exception subclass smoke tests ────────────────────────────────────


def test_auth_error_uses_default_401() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/auth-error")
    async def auth_error():
        raise AuthError(code=ErrorCode.AUTH_INVALID_TOKEN, message="Token expired")

    client = TestClient(app)
    response = client.get("/auth-error")
    body = response.json()

    assert response.status_code == 401
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"
    assert body["error"]["message"] == "Token expired"
    assert body["status"] == "error"


def test_forbidden_error_uses_default_403() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/forbidden")
    async def forbidden():
        raise ForbiddenError(code=ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS, message="Access denied")

    client = TestClient(app)
    response = client.get("/forbidden")
    body = response.json()

    assert response.status_code == 403
    assert body["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSIONS"


def test_not_found_error_uses_default_404() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/not-found")
    async def not_found():
        raise NotFoundError(code=ErrorCode.KB_NOT_FOUND, message="KB missing")

    client = TestClient(app)
    response = client.get("/not-found")
    body = response.json()

    assert response.status_code == 404
    assert body["error"]["code"] == "KB_NOT_FOUND"


def test_conflict_error_uses_default_409() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/conflict")
    async def conflict():
        raise ConflictError(code=ErrorCode.TASK_DUPLICATE_VIDEO_IN_KB, message="Duplicate")

    client = TestClient(app)
    response = client.get("/conflict")
    body = response.json()

    assert response.status_code == 409
    assert body["error"]["code"] == "TASK_DUPLICATE_VIDEO_IN_KB"


def test_validation_error_subclass_uses_default_422() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/validation-error")
    async def validation_error():
        raise ValidationError(code=ErrorCode.VIDEO_NOT_READY, message="Video not ready")

    client = TestClient(app)
    response = client.get("/validation-error")
    body = response.json()

    assert response.status_code == 422
    assert body["error"]["code"] == "VIDEO_NOT_READY"


def test_service_error_uses_default_500_and_is_retryable() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/service-error")
    async def service_error():
        raise ServiceError(code=ErrorCode.QA_AGENT_NOT_CONFIGURED, message="Agent missing")

    client = TestClient(app)
    response = client.get("/service-error")
    body = response.json()

    assert response.status_code == 500
    assert body["error"]["code"] == "QA_AGENT_NOT_CONFIGURED"
    assert body["error"]["is_retryable"] is True


def test_domain_error_can_override_status_code() -> None:
    app = FastAPI()
    register_request_context_middleware(app)
    register_error_handlers(app)

    @app.get("/override")
    async def override():
        raise NotFoundError(code=ErrorCode.KB_NOT_FOUND, message="Gone", status_code=410)

    client = TestClient(app)
    response = client.get("/override")
    body = response.json()

    assert response.status_code == 410
    assert body["error"]["code"] == "KB_NOT_FOUND"
