import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

_CHECKPOINTER_CACHE: dict[str, Any] = {}
_POSTGRES_SETUP_DONE: set[str] = set()


def _create_postgres_checkpointer(postgres_url: str) -> Any:
    """Create a PostgresSaver with a fresh database connection.

    PostgresSaver state lives in the database, so caching the instance is
    unnecessary and harmful — the underlying psycopg connection can go stale
    between Celery tasks.  Instead, we open a new connection each time and
    only call ``setup()`` once per URL to create the required tables.

    If the connection fails (e.g. PostgreSQL unreachable), falls back to
    an InMemorySaver to avoid blocking the workflow indefinitely.
    """
    from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
    import psycopg  # type: ignore

    conn = psycopg.connect(
        postgres_url,
        autocommit=True,
        connect_timeout=5,  # 5 秒连接超时，防止卡死
    )

    checkpointer = PostgresSaver(conn)

    if postgres_url not in _POSTGRES_SETUP_DONE:
        checkpointer.setup()
        _POSTGRES_SETUP_DONE.add(postgres_url)

    return checkpointer


def create_checkpointer(backend: str, postgres_url: str = "") -> Any:
    """
    创建 LangGraph checkpointer。

    - memory: 使用 InMemorySaver（开发环境默认，缓存单例以保持内存状态）
    - postgres: 每次创建新连接以避免连接过期问题（状态持久化在数据库中）
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
        try:
            return _create_postgres_checkpointer(postgres_url)
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

