from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request


def register_request_context_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        trace_id = request.headers.get("x-trace-id") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        request.state.user_id = request.headers.get("x-user-id", "anonymous")
        request.state._request_started_at = time.perf_counter()

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-trace-id"] = trace_id
        return response
