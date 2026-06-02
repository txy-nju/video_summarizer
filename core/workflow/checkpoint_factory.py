import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore

_CHECKPOINTER_CACHE: dict[str, Any] = {}
_CHECKPOINTER_CONTEXTS: dict[str, Any] = {}  # 持有 context manager 引用，防止连接被回收


def create_checkpointer(backend: str, postgres_url: str = "") -> Any:
    """
    创建 LangGraph checkpointer。

    - memory: 使用 InMemorySaver（开发环境默认，缓存单例以保持内存状态）
    - postgres: 使用 PostgresSaver，连接字符串通过 CHECKPOINT_DB_URL 配置。
                每次创建新连接以避免连接过期问题（状态持久化在数据库中）。
                连接失败时自动回退为 InMemorySaver。
    """
    normalized = (backend or "memory").strip().lower()

    if normalized == "memory":
        cache_key = f"{normalized}:{postgres_url}"
        if cache_key in _CHECKPOINTER_CACHE:
            return _CHECKPOINTER_CACHE[cache_key]
        checkpointer = InMemorySaver()
        _CHECKPOINTER_CACHE[cache_key] = checkpointer
        return checkpointer

    if normalized == "postgres":
        if not postgres_url:
            postgres_url = os.getenv("CHECKPOINT_DB_URL", "")
        if not postgres_url:
            raise ValueError("CHECKPOINT_BACKEND=postgres requires CHECKPOINT_DB_URL.")

        cache_key = f"{normalized}:{postgres_url}"

        # from_conn_string 是 @contextmanager，需要进入上下文后持有引用，
        # 使数据库连接在进程生命周期内保持有效
        try:
            cm = PostgresSaver.from_conn_string(postgres_url)
            checkpointer = cm.__enter__()
            checkpointer.setup()  # 自动创建 checkpoints / checkpoint_writes / checkpoint_blobs 表
            _CHECKPOINTER_CONTEXTS[cache_key] = cm
            _CHECKPOINTER_CACHE[cache_key] = checkpointer
            return checkpointer
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "PostgresSaver 连接失败（%s），回退为 InMemorySaver。"
                "检查点不会持久化，重启后历史状态丢失。",
                exc,
            )
            cache_key = f"memory-fallback:{postgres_url}"
            if cache_key in _CHECKPOINTER_CACHE:
                return _CHECKPOINTER_CACHE[cache_key]
            checkpointer = InMemorySaver()
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

