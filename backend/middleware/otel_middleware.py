from __future__ import annotations

from fastapi import FastAPI, Request

from backend.observability.propagation import (
    extract_trace_headers,
    extract_trace_id_from_traceparent,
)
from backend.observability.tracing import (
    build_span_name,
    make_http_trace_headers,
    normalize_trace_id,
    start_span,
)


def register_otel_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def otel_middleware(request: Request, call_next):
        incoming = extract_trace_headers(request.headers)
        trace_id = ""

        traceparent = incoming.get("traceparent", "")
        from_traceparent = extract_trace_id_from_traceparent(traceparent)
        if from_traceparent:
            trace_id = from_traceparent
        else:
            trace_id = normalize_trace_id(str(getattr(request.state, "trace_id", "")))

        request.state.trace_id = trace_id

        with start_span(
            build_span_name("http", "request", "handle"),
            attributes={
                "scope": "http_request",
                "scope_id": request.url.path,
                "http_method": request.method,
                "trace_id": trace_id,
            },
        ):
            response = await call_next(request)

        propagated = incoming if incoming else make_http_trace_headers(trace_id)
        for header_name, header_value in propagated.items():
            response.headers[header_name] = header_value
        return response
