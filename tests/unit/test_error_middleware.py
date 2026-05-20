from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.exceptions import AppError
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
