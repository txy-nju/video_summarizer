"""
任务状态查询服务。

封装 Celery AsyncResult 查询接口，将 Celery 内部状态映射为计划约定格式：
{
    "task_id": "<celery_task_id>",
    "task_type": "<task_type_label>",
    "status": "PENDING | RUNNING | COMPLETED | FAILED | CANCELLED",
    "progress": null,
    "message": null,
    "result": <result_payload_or_null>,
    "error": "<error_message_or_null>",
    "updated_at": "<ISO-8601-UTC>"
}

注意：
- 此服务仅查询 Celery result backend（Redis），不写入数据库。
- 权威状态以数据库实体字段（transcribe_status / frame_extraction_status 等）为准。
- 本服务提供任务级细粒度进度快照，适合 WebSocket 轮询与前端进度栏渲染（步骤 6 对接）。
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging


logger = logging.getLogger(__name__)


_CELERY_STATE_MAP: dict[str, str] = {
    "PENDING": "PENDING",
    "RECEIVED": "PENDING",
    "STARTED": "RUNNING",
    "RETRY": "RUNNING",
    "SUCCESS": "COMPLETED",
    "FAILURE": "FAILED",
    "REVOKED": "CANCELLED",
}


class TaskStatusService:
    """查询 Celery 任务状态并转换为统一格式。"""

    @staticmethod
    def record_observable_event(event: dict) -> None:
        """Record lightweight observable events for task/status diagnostics."""
        logger.info("task_status_observable_event", extra={"event": event})

    def get_task_status(self, celery_task_id: str, task_type: str = "") -> dict:
        """
        查询单个 Celery 任务状态。

        :param celery_task_id: Celery 任务 ID（由 .delay() 或 .apply_async() 返回的字符串）
        :param task_type: 可选标签，用于前端渲染区分任务类型
        :return: 统一任务状态 dict（见模块文档）
        """
        from celery.result import AsyncResult
        from backend.tasks.celery_app import celery_app

        result = AsyncResult(celery_task_id, app=celery_app)
        status = _CELERY_STATE_MAP.get(result.state, result.state)

        task_result = None
        error = None

        if result.successful():
            task_result = result.result
        elif result.failed():
            error = str(result.info) if result.info else "Unknown error"

        return {
            "task_id": celery_task_id,
            "task_type": task_type,
            "status": status,
            "progress": None,
            "message": None,
            "result": task_result,
            "error": error,
            "updated_at": datetime.now(UTC).isoformat(),
        }
