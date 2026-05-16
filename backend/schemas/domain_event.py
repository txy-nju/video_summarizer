"""
领域事件统一信封 DTO。

对齐步骤 5.6 数据格式约定：
- event_id: UUIDv7，事件唯一标识
- schema_version: 语义化版本，消费端按版本路由
- event_type: 事件类型（如 video_uploaded）
- trace_id: 链路追踪 ID，贯穿 HTTP → Celery → Event Bus
- produced_at: 事件生产时间（UTC ISO 8601）
- tenant_id: 租户标识（当前为单租户，预留）
- scope: 领域范围（video_resource / video_summary_task / global_chat 等）
- scope_id: 领域内实体主键
- payload: 事件携带的业务数据（自由 JSON）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    """领域事件统一信封。

    Redis Stream 中以 JSON 序列化存储。
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = Field(default="1.0")
    event_type: str = Field(..., min_length=1)
    trace_id: str = Field(default="")
    produced_at: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    tenant_id: str = Field(default="tenant_001")
    scope: str = Field(default="")
    scope_id: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """序列化为 JSON 字符串，供 Redis XADD 使用。"""
        return self.model_dump_json()
