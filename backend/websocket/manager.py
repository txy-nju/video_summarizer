"""
WebSocket 连接管理器。

职责：
- 管理活跃 WebSocket 连接（按 user_id 索引）
- 接收 Redis Pub/Sub 消息并广播到对应 WebSocket 客户端
- 断开连接时自动清理

边界约束：
- WebSocket 层不直接查询复杂业务数据，只转发状态事件。
- 连接认证失败必须立刻关闭，不允许匿名旁路监听。
- 多实例部署下，通过 Redis Pub/Sub 跨实例广播进度。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from backend.websocket.schemas import (
    WSEventEnvelope,
    WSEventType,
    WSSource,
)
from backend.services.progress_event_bus import ProgressEventBus, _channel_name

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期与消息广播。"""

    def __init__(self, event_bus: ProgressEventBus) -> None:
        # user_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self._event_bus = event_bus
        # 线程标识：确保 Pub/Sub 只在一个线程中监听
        self._sub_thread: threading.Thread | None = None
        self._should_stop = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """接受 WebSocket 连接并注册到管理器。"""
        await websocket.accept()
        async with self._lock:
            # 同一用户的新连接会替换旧连接
            if user_id in self._connections:
                old = self._connections[user_id]
                try:
                    await old.close(code=4001, reason="new_connection")
                except Exception:
                    pass
            self._connections[user_id] = websocket
        logger.info("WebSocket connected: user_id=%s, active=%d", user_id, len(self._connections))

    async def disconnect(self, user_id: str) -> None:
        """取消注册 WebSocket 连接。"""
        async with self._lock:
            self._connections.pop(user_id, None)
        logger.info("WebSocket disconnected: user_id=%s, active=%d", user_id, len(self._connections))

    # ------------------------------------------------------------------
    # 消息发送
    # ------------------------------------------------------------------

    async def send_personal(self, user_id: str, event: WSEventEnvelope) -> bool:
        """向指定用户发送消息。"""
        async with self._lock:
            ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_text(event.model_dump_json())
            return True
        except Exception:
            await self.disconnect(user_id)
            return False

    async def broadcast_to_all(self, event: WSEventEnvelope) -> None:
        """广播消息到所有已连接用户。"""
        async with self._lock:
            user_ids = list(self._connections.keys())
        for uid in user_ids:
            await self.send_personal(uid, event)

    # ------------------------------------------------------------------
    # Redis Pub/Sub 订阅（后台线程）
    # ------------------------------------------------------------------

    def start_listening(self, tenant_id: str, instance_id: str) -> None:
        """启动后台线程，通过 Redis Pub/Sub 接收跨实例进度事件。

        Args:
            tenant_id: 租户 ID，用于订阅主业务频道
            instance_id: 本实例标识
        """
        if self._sub_thread is not None and self._sub_thread.is_alive():
            return

        channels = [
            _channel_name("tenant", tenant_id),
            _channel_name("control", ""),
        ]

        def _run():
            logger.info("ProgressEventBus subscriber started: instance=%s, channels=%s", instance_id, channels)

            def _on_event(event: WSEventEnvelope) -> None:
                # 将收到的进度事件转发到对应 WebSocket 客户端
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                except Exception:
                    pass

                async def _forward():
                    success = await self.send_personal(event.user_id, event)
                    if not success:
                        logger.debug(
                            "Progress event dropped (user offline): user_id=%s, event_type=%s, sequence=%s",
                            event.user_id,
                            event.event_type,
                            event.sequence,
                        )

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(_forward())
                    else:
                        loop.run_until_complete(_forward())
                except Exception:
                    pass

            try:
                self._event_bus.subscribe(channels, _on_event)
            except Exception:
                logger.exception("ProgressEventBus subscriber crashed")
            finally:
                self._sub_thread = None

        self._should_stop = False
        self._sub_thread = threading.Thread(target=_run, daemon=True, name="ws-pubsub-listener")
        self._sub_thread.start()

    def stop_listening(self) -> None:
        """停止后台 Pub/Sub 监听线程。"""
        self._should_stop = True
        if self._sub_thread is not None:
            self._sub_thread.join(timeout=3)
            self._sub_thread = None

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    async def get_active_count(self) -> int:
        async with self._lock:
            return len(self._connections)

    async def is_connected(self, user_id: str) -> bool:
        async with self._lock:
            return user_id in self._connections
