import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

_CHECKPOINTER_CACHE: dict[str, Any] = {}
_CHECKPOINTER_CONTEXTS: dict[str, Any] = {}  # 持有 context manager 引用，防止连接被回收


def create_checkpointer(backend: str, postgres_url: str = "") -> Any:
    """
    创建 LangGraph checkpointer。

    - memory: 使用 InMemorySaver（开发环境默认）
    - postgres: 使用 PostgresSaver，连接字符串通过 CHECKPOINT_DB_URL 配置
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

        # from_conn_string 是 @contextmanager，需要进入上下文后持有引用，
        # 使数据库连接在进程生命周期内保持有效
        cm = PostgresSaver.from_conn_string(postgres_url)
        checkpointer = cm.__enter__()
        checkpointer.setup()  # 自动创建 checkpoints / checkpoint_writes / checkpoint_blobs 表
        _CHECKPOINTER_CONTEXTS[cache_key] = cm
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
