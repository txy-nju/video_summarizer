from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.middleware.otel_middleware import register_otel_middleware
from backend.observability.llm_tracing import build_llm_span_attributes
from backend.observability.propagation import (
    build_traceparent,
    extract_trace_id_from_traceparent,
)
from backend.observability.tracing import build_span_name, normalize_trace_id


def test_extract_trace_id_from_traceparent() -> None:
    traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert extract_trace_id_from_traceparent(traceparent) == "0123456789abcdef0123456789abcdef"


def test_build_span_name_contract() -> None:
    assert build_span_name("video_resource", "extraction", "transcribe") == "video_resource.extraction.transcribe"


def test_otel_middleware_propagates_traceparent() -> None:
    app = FastAPI()
    register_otel_middleware(app)

    @app.get("/ping")
    async def ping(request: Request) -> dict[str, str]:
        return {"trace_id": str(getattr(request.state, "trace_id", ""))}

    client = TestClient(app)
    incoming = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    response = client.get("/ping", headers={"traceparent": incoming})
    assert response.status_code == 200
    assert response.headers["traceparent"] == incoming
    assert response.json()["trace_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_otel_middleware_generates_traceparent_when_absent() -> None:
    app = FastAPI()
    register_otel_middleware(app)

    @app.get("/ping")
    async def ping(request: Request) -> dict[str, str]:
        return {"trace_id": str(getattr(request.state, "trace_id", ""))}

    client = TestClient(app)
    response = client.get("/ping")
    assert response.status_code == 200
    generated = response.headers["traceparent"]
    extracted = extract_trace_id_from_traceparent(generated)
    assert extracted is not None
    assert response.json()["trace_id"] == extracted


def test_build_llm_span_attributes_keeps_allowed_fields_only() -> None:
    attrs = build_llm_span_attributes(
        provider="openai",
        model="gpt-4o",
        scope="video_summary_task",
        scope_id="task_001",
        task_id="task_001",
        workflow_state="FINAL_GENERATING",
        retry_count=1,
        error_code="",
    )
    assert attrs["llm_provider"] == "openai"
    assert attrs["scope"] == "video_summary_task"
    assert "prompt" not in attrs
    assert "transcript" not in attrs


def test_trace_helpers_build_traceparent() -> None:
    trace_id = normalize_trace_id("req-123")
    traceparent = build_traceparent(trace_id, "0123456789abcdef", sampled=True)
    assert extract_trace_id_from_traceparent(traceparent) == trace_id
