import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

_CHECKPOINTER_CACHE: dict[str, Any] = {}


def create_checkpointer(backend: str, postgres_url: str = "") -> Any:
    """
    创建 LangGraph checkpointer。

    - memory: 使用 InMemorySaver（开发环境默认）
    - postgres: 预留生产路径；若依赖缺失会给出明确异常
    """
    normalized = (backend or "memory").strip().lower()
    cache_key = f"{normalized}:{postgres_url}"

    if cache_key in _CHECKPOINTER_CACHE:
        return _CHECKPOINTER_CACHE[cache_key]

    if normalized == "memory":
        checkpointer = InMemorySaver()
        _CHECKPOINTER_CACHE[cache_key] = checkpointer
        return checkpointer

    if normalized == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "CHECKPOINT_BACKEND=postgres but postgres checkpointer dependency is not installed."
            ) from exc

        if not postgres_url:
            postgres_url = os.getenv("CHECKPOINT_DB_URL", "")

        if not postgres_url:
            raise ValueError("CHECKPOINT_BACKEND=postgres requires CHECKPOINT_DB_URL.")

        checkpointer = PostgresSaver.from_conn_string(postgres_url)
        _CHECKPOINTER_CACHE[cache_key] = checkpointer
        return checkpointer

    raise ValueError(f"Unsupported CHECKPOINT_BACKEND: {backend}")


def get_checkpoint_snapshot(*, backend: str, thread_id: str, postgres_url: str = "") -> dict[str, Any] | None:
    """按 thread_id 读取 checkpoint 快照，不存在时返回 None。"""
    checkpointer = create_checkpointer(backend, postgres_url)
    checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})
    if isinstance(checkpoint, dict):
        return checkpoint
    return None
