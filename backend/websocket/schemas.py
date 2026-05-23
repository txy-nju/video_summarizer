"""
WebSocket 消息 Schema 定义。

统一事件信封字段（对齐计划约定）：
- event_id, schema_version, event_type, trace_id, produced_at
- tenant_id, user_id, scope, scope_id, sequence
- stage, substage, status, progress, message, payload, source
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class WSEventType(str, Enum):
    """WebSocket 事件类型（计划约定固定值）。"""

    PROGRESS = "progress"
    COMPLETED = "completed"
    ERROR = "error"
    STATUS_UPDATE = "status_update"
    RECONNECT_ACK = "reconnect_ack"


class WSScope(str, Enum):
    """进度事件 scope 枚举。"""

    VIDEO_RESOURCE = "video_resource"
    VIDEO_SUMMARY_TASK = "video_summary_task"
    VIDEO_QA = "video_qa"
    GLOBAL_CHAT = "global_chat"


class WSStage(str, Enum):
    """进度阶段枚举。"""

    EXTRACTION = "extraction"
    TRANSCRIBING = "transcribing"
    EXTRACTING_KEYFRAMES = "extracting_keyframes"
    RAG_RETRIEVAL = "rag_retrieval"
    LLM_REASONING = "llm_reasoning"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    CLEANUP = "cleanup"


class WSSource(BaseModel):
    """事件来源标识。"""

    service: str = Field(..., description="发出事件的服务名称")
    instance_id: str = Field(..., description="服务实例标识")


class WSProgressPayload(BaseModel):
    """progress 事件的 payload 结构。"""

    task_id: str | None = Field(default=None, description="关联的 Celery 任务 ID")
    node: str | None = Field(default=None, description="当前执行节点名称")
    extra: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


class WSCompletedPayload(BaseModel):
    """completed 事件的 payload 结构。"""

    task_id: str | None = Field(default=None)
    result: dict[str, Any] | None = Field(default=None)


class WSErrorPayload(BaseModel):
    """error 事件的 payload 结构。"""

    task_id: str | None = Field(default=None)
    code: str = Field(default="UNKNOWN_ERROR")
    message: str = Field(default="")
    is_retryable: bool = Field(default=False)


class WSStatusUpdatePayload(BaseModel):
    """status_update 事件的 payload 结构。"""

    status: str = Field(...)
    previous_status: str | None = Field(default=None)
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------- 顶层统一事件信封 ----------

class WSEventEnvelope(BaseModel):
    """
    统一 WebSocket 事件信封（对齐计划约定）。

    示例（分布式广播）：
    {
      "event_id": "evt_018f9d9b8d6a7db0",
      "schema_version": "1.0",
      "event_type": "progress",
      "trace_id": "trc_018f9d9b8d6a7db0",
      "produced_at": "2026-05-13T08:30:00Z",
      "tenant_id": "tenant_001",
      "user_id": "usr_001",
      "scope": "video_resource",
      "scope_id": "vid_001",
      "sequence": 42,
      "stage": "extraction",
      "substage": "extracting_keyframes",
      "status": "RUNNING",
      "progress": 45,
      "message": "正在抽取关键帧",
      "payload": {"task_id": "task_001"},
      "source": {"service": "task_status_service", "instance_id": "worker-02"}
    }
    """

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:24]}")
    schema_version: str = Field(default="1.0")
    event_type: WSEventType = Field(...)
    trace_id: str = Field(default="")
    produced_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tenant_id: str = Field(default="default")
    user_id: str = Field(...)
    scope: WSScope = Field(...)
    scope_id: str = Field(...)
    sequence: int = Field(..., ge=0, description="严格递增序号，按 scope+scope_id 独立维护")
    stage: WSStage | None = Field(default=None)
    substage: str | None = Field(default=None)
    status: str = Field(default="UNKNOWN")
    progress: int | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: WSSource | None = Field(default=None)
