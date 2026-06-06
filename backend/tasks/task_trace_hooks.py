from __future__ import annotations

import logging
import time
from typing import Any

from celery.signals import task_failure, task_postrun, task_prerun

from backend.observability.propagation import extract_trace_headers, extract_trace_id_from_traceparent
from backend.observability.tracing import build_span_name, normalize_trace_id, start_span

logger = logging.getLogger(__name__)
_TASK_START_TIME: dict[str, float] = {}
_REGISTERED = False


def _resolve_trace_id(task: Any, kwargs: dict[str, Any] | None) -> str:
    request_obj = getattr(task, "request", None)
    headers = getattr(request_obj, "headers", {}) if request_obj is not None else {}
    if headers is None:
        headers = {}
    normalized_headers = extract_trace_headers(headers)
    traceparent = normalized_headers.get("traceparent", "")
    from_header = extract_trace_id_from_traceparent(traceparent)
    if from_header:
        return from_header

    if kwargs and isinstance(kwargs.get("trace_id"), str) and kwargs.get("trace_id"):
        return normalize_trace_id(kwargs["trace_id"])

    request_id = getattr(request_obj, "id", "") if request_obj is not None else ""
    return normalize_trace_id(str(request_id))


def register_task_trace_hooks() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    @task_prerun.connect(weak=False)
    def _on_task_prerun(task_id: str | None = None, task: Any = None, args: tuple[Any, ...] | None = None, kwargs: dict[str, Any] | None = None, **_: Any) -> None:
        _ = args
        if not task_id or task is None:
            return
        _TASK_START_TIME[task_id] = time.perf_counter()
        trace_id = _resolve_trace_id(task, kwargs)
        with start_span(
            build_span_name("celery", "task", "start"),
            attributes={
                "task_id": task_id,
                "scope": "celery_task",
                "scope_id": getattr(task, "name", "unknown"),
                "trace_id": trace_id,
            },
        ):
            logger.info(
                "task_trace_start",
                extra={
                    "trace_id": trace_id,
                    "task_id": task_id,
                    "task_name": getattr(task, "name", "unknown"),
                },
            )

    @task_postrun.connect(weak=False)
    def _on_task_postrun(task_id: str | None = None, task: Any = None, kwargs: dict[str, Any] | None = None, state: str | None = None, **_: Any) -> None:
        if not task_id or task is None:
            return
        started = _TASK_START_TIME.pop(task_id, None)
        duration_ms = (time.perf_counter() - started) * 1000 if started is not None else 0.0
        trace_id = _resolve_trace_id(task, kwargs)
        with start_span(
            build_span_name("celery", "task", "finish"),
            attributes={
                "task_id": task_id,
                "scope": "celery_task",
                "scope_id": getattr(task, "name", "unknown"),
                "trace_id": trace_id,
                "duration_ms": round(duration_ms, 2),
                "workflow_state": state or "",
            },
        ):
            logger.info(
                "task_trace_finish",
                extra={
                    "trace_id": trace_id,
                    "task_id": task_id,
                    "task_name": getattr(task, "name", "unknown"),
                    "duration_ms": round(duration_ms, 2),
                    "task_state": state or "",
                },
            )

    @task_failure.connect(weak=False)
    def _on_task_failure(task_id: str | None = None, task: Any = None, exception: Exception | None = None, kwargs: dict[str, Any] | None = None, **_: Any) -> None:
        if not task_id or task is None:
            return
        trace_id = _resolve_trace_id(task, kwargs)

        # 检测重试是否耗尽
        request_retries = getattr(getattr(task, "request", None), "retries", 0) or 0
        max_retries = getattr(task, "max_retries", 0) or 0
        retries_exhausted = max_retries > 0 and request_retries >= max_retries

        with start_span(
            build_span_name("celery", "task", "failure"),
            attributes={
                "task_id": task_id,
                "scope": "celery_task",
                "scope_id": getattr(task, "name", "unknown"),
                "trace_id": trace_id,
                "error_code": exception.__class__.__name__ if exception else "UNKNOWN",
                "retries_attempted": request_retries,
                "max_retries": max_retries,
                "retries_exhausted": retries_exhausted,
            },
        ):
            logger.error(
                "task_trace_failure",
                extra={
                    "trace_id": trace_id,
                    "task_id": task_id,
                    "task_name": getattr(task, "name", "unknown"),
                    "error_code": exception.__class__.__name__ if exception else "UNKNOWN",
                    "retries_attempted": request_retries,
                    "max_retries": max_retries,
                    "retries_exhausted": retries_exhausted,
                },
            )

    _REGISTERED = True
