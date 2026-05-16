"""
进度发布服务。

封装进度事件的发布逻辑：
- 按 scope + scope_id 维护严格递增的 sequence
- 组装统一事件信封并发布到 Redis Pub/Sub
- 同时通过 WebSocket ConnectionManager 向本地已连接用户直推

边界约束：
- 事件序列号 sequence 由本服务统一生成并按 scope+scope_id 递增。
- 禁止由各 worker 本地自增。
- Redis Pub/Sub 仅用于实时分发，不作为权威存储。
"""

from __future__ import annotations

import logging
import threading

from backend.websocket.schemas import (
    WSEventEnvelope,
    WSEventType,
    WSScope,
    WSStage,
    WSSource,
)
from backend.services.progress_event_bus import ProgressEventBus

logger = logging.getLogger(__name__)


class ProgressPublishService:
    """统一的进度事件发布门面。

    提供按 scope+scope_id 的严格递增 sequence 管理，
    并通过 ProgressEventBus（Redis Pub/Sub）广播事件。
    """

    def __init__(self, event_bus: ProgressEventBus, instance_id: str = "") -> None:
        self._event_bus = event_bus
        self._instance_id = instance_id
        # scope+scope_id → sequence 计数器（线程安全）
        self._sequences: dict[str, int] = {}
        self._seq_lock = threading.Lock()

    def _next_sequence(self, scope: str, scope_id: str) -> int:
        key = f"{scope}:{scope_id}"
        with self._seq_lock:
            seq = self._sequences.get(key, -1) + 1
            self._sequences[key] = seq
            return seq

    def _build_envelope(
        self,
        event_type: WSEventType,
        user_id: str,
        scope: WSScope,
        scope_id: str,
        *,
        stage: WSStage | None = None,
        substage: str | None = None,
        status: str = "UNKNOWN",
        progress: int | None = None,
        message: str | None = None,
        payload: dict | None = None,
        tenant_id: str = "default",
        trace_id: str = "",
    ) -> WSEventEnvelope:
        return WSEventEnvelope(
            event_type=event_type,
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
            scope_id=scope_id,
            sequence=self._next_sequence(scope.value, scope_id),
            stage=stage,
            substage=substage,
            status=status,
            progress=progress,
            message=message,
            payload=payload or {},
            trace_id=trace_id,
            source=WSSource(service="progress_publish_service", instance_id=self._instance_id),
        )

    # ------------------------------------------------------------------
    # 便捷发布方法（对应 plan 定义的 5 种事件类型）
    # ------------------------------------------------------------------

    def publish_progress(
        self,
        user_id: str,
        scope: WSScope,
        scope_id: str,
        *,
        stage: WSStage | None = None,
        substage: str | None = None,
        status: str = "RUNNING",
        progress: int | None = None,
        message: str | None = None,
        payload: dict | None = None,
        tenant_id: str = "default",
        trace_id: str = "",
    ) -> int:
        """发布 progress 事件。"""
        event = self._build_envelope(
            WSEventType.PROGRESS,
            user_id,
            scope,
            scope_id,
            stage=stage,
            substage=substage,
            status=status,
            progress=progress,
            message=message,
            payload=payload,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        return self._event_bus.publish(event)

    def publish_completed(
        self,
        user_id: str,
        scope: WSScope,
        scope_id: str,
        *,
        result: dict | None = None,
        message: str | None = None,
        tenant_id: str = "default",
        trace_id: str = "",
    ) -> int:
        """发布 completed 事件。"""
        event = self._build_envelope(
            WSEventType.COMPLETED,
            user_id,
            scope,
            scope_id,
            status="COMPLETED",
            progress=100,
            message=message,
            payload={"result": result} if result else {},
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        return self._event_bus.publish(event)

    def publish_error(
        self,
        user_id: str,
        scope: WSScope,
        scope_id: str,
        *,
        code: str = "UNKNOWN_ERROR",
        message: str = "",
        is_retryable: bool = False,
        tenant_id: str = "default",
        trace_id: str = "",
    ) -> int:
        """发布 error 事件。"""
        event = self._build_envelope(
            WSEventType.ERROR,
            user_id,
            scope,
            scope_id,
            status="FAILED",
            message=message,
            payload={"code": code, "message": message, "is_retryable": is_retryable},
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        return self._event_bus.publish(event)

    def publish_status_update(
        self,
        user_id: str,
        scope: WSScope,
        scope_id: str,
        *,
        status: str,
        previous_status: str | None = None,
        message: str | None = None,
        extra: dict | None = None,
        tenant_id: str = "default",
        trace_id: str = "",
    ) -> int:
        """发布 status_update 事件。"""
        event = self._build_envelope(
            WSEventType.STATUS_UPDATE,
            user_id,
            scope,
            scope_id,
            status=status,
            message=message,
            payload={"status": status, "previous_status": previous_status, "extra": extra or {}},
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        return self._event_bus.publish(event)
