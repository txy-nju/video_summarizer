import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_CHECKPOINTER_CACHE: dict[str, Any] = {}
_CHECKPOINTER_POOLS: dict[str, ConnectionPool] = {}  # 持有连接池引用，用于优雅关闭


def create_checkpointer(backend: str, postgres_url: str = "") -> Any:
    """
    创建 LangGraph checkpointer。

    - memory: 使用 InMemorySaver（开发环境默认，缓存单例以保持内存状态）
    - postgres: 使用 PostgresSaver + ConnectionPool，支持多线程并发访问。
                checkpoint 数据持久化在 PostgreSQL 中，每个线程从连接池
                获取独立连接，避免跨线程共享导致的连接关闭问题。
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

        # 已缓存则直接返回（连接池 + PostgresSaver 单例）
        if cache_key in _CHECKPOINTER_CACHE:
            return _CHECKPOINTER_CACHE[cache_key]

        try:
            # 使用 ConnectionPool 为每个线程提供独立连接，避免
            # `--pool=threads` 模式下多线程共享单一 psycopg Connection
            # 导致 "the connection is closed" 错误。
            pool = ConnectionPool(
                postgres_url,
                min_size=2,
                max_size=8,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            checkpointer = PostgresSaver(pool)
            checkpointer.setup()  # 自动创建 checkpoints / checkpoint_writes / checkpoint_blobs 表
            _CHECKPOINTER_POOLS[cache_key] = pool
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


def close_checkpointer(backend: str = "postgres", postgres_url: str = "") -> None:
    """关闭 checkpointer 对应的连接池，释放数据库连接资源。

    应在 worker 关闭或应用退出时调用。
    """
    if not postgres_url:
        postgres_url = os.getenv("CHECKPOINT_DB_URL", "")
    normalized = (backend or "").strip().lower()
    cache_key = f"{normalized}:{postgres_url}"

    pool = _CHECKPOINTER_POOLS.pop(cache_key, None)
    if pool is not None:
        pool.close()
    _CHECKPOINTER_CACHE.pop(cache_key, None)


def get_checkpoint_snapshot(*, backend: str, thread_id: str, postgres_url: str = "") -> dict[str, Any] | None:
    """按 thread_id 读取 checkpoint 快照，不存在时返回 None。"""
    checkpointer = create_checkpointer(backend, postgres_url)
    checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})
    if isinstance(checkpoint, dict):
        return checkpoint
    return None

