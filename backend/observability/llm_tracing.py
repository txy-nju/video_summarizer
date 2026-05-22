from __future__ import annotations

from typing import Any

from backend.observability.tracing import build_span_name, start_span


def build_llm_span_attributes(
    *,
    provider: str,
    model: str,
    scope: str,
    scope_id: str,
    task_id: str | None = None,
    workflow_state: str | None = None,
    retry_count: int | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "llm_provider": provider,
        "llm_model": model,
        "scope": scope,
        "scope_id": scope_id,
        "task_id": task_id or "",
        "workflow_state": workflow_state or "",
        "retry_count": retry_count or 0,
        "error_code": error_code or "",
    }


def trace_llm_call(
    *,
    provider: str,
    model: str,
    scope: str,
    scope_id: str,
    task_id: str | None = None,
    workflow_state: str | None = None,
    retry_count: int | None = None,
    error_code: str | None = None,
):
    """Context manager for wrapping LLM API calls with span attributes.

    Must not include prompt content or transcript payloads.
    """
    return start_span(
        build_span_name("llm", "inference", "generate"),
        attributes=build_llm_span_attributes(
            provider=provider,
            model=model,
            scope=scope,
            scope_id=scope_id,
            task_id=task_id,
            workflow_state=workflow_state,
            retry_count=retry_count,
            error_code=error_code,
        ),
    )
