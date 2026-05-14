from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response


class MobileOptimizationMiddleware(BaseHTTPMiddleware):
    """Apply lightweight mobile-friendly headers and conditional caching."""

    async def dispatch(self, request: Request, call_next):
        """
        异步中间件调度方法，用于处理请求并增强响应头信息。

        主要功能包括：
        1. 设置默认的 Vary 头以支持内容协商缓存。
        2. 对成功的 GET 请求自动添加 Cache-Control 和 ETag 头。
        3. 处理客户端的 If-None-Match 头，若匹配则返回 304 Not Modified。
        4. 添加响应时间戳头。

        Args:
            request (Request): 传入的 HTTP 请求对象。
            call_next: 下一个中间件或路由处理函数的调用句柄。

        Returns:
            Response: 处理后的 HTTP 响应对象，或在缓存命中时返回 304 状态码的响应。
        """
        response: Response = await call_next(request)

        response.headers.setdefault("Vary", "Accept-Encoding")

        if request.method == "GET" and response.status_code == 200:
            response.headers.setdefault("Cache-Control", "private, max-age=60")
            body = getattr(response, "body", b"")
            if "ETag" not in response.headers and body:
                etag = hashlib.sha256(body).hexdigest()[:16]
                response.headers["ETag"] = f'W/"{etag}"'

            client_etag = request.headers.get("if-none-match")
            if client_etag and client_etag == response.headers.get("ETag"):
                return Response(status_code=304)

        response.headers.setdefault("X-Response-Timestamp", datetime.now(UTC).isoformat())
        return response


def register_mobile_optimization(app: FastAPI) -> None:
    # Built-in gzip compression for low-bandwidth mobile scenarios.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(MobileOptimizationMiddleware)
