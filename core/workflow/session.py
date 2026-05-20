import uuid
from typing import TypedDict


VIDEO_SUMMARY_TASK_SCOPE = "video_summary_task"


class SessionRestorePayload(TypedDict):
    scope: str
    scope_id: str
    checkpoint_status: str
    workflow_state: str


def ensure_thread_id(thread_id: str = "") -> str:
    """
    标准化 thread_id。
    - 若传入有效字符串，返回去首尾空格后的值
    - 若为空，自动生成 UUID
    """
    if thread_id and thread_id.strip():
        return thread_id.strip()
    return str(uuid.uuid4())


def build_restore_payload(*, scope_id: str, workflow_state: str, checkpoint_status: str = "restored") -> SessionRestorePayload:
    """构建会话恢复响应载荷，保持固定字段契约。"""
    return {
        "scope": VIDEO_SUMMARY_TASK_SCOPE,
        "scope_id": scope_id,
        "checkpoint_status": checkpoint_status,
        "workflow_state": workflow_state,
    }
