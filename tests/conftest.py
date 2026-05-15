"""Pytest fixtures for DB transaction isolation.

Ensures every test runs in an isolated transaction and rolls back on teardown,
while also resetting cached dependency singletons between tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

import backend.dependencies as dependencies
from backend.db.session import engine


_DEPENDENCY_CACHE_FUNCS = (
    "get_user_repository",
    "get_auth_service",
    "get_kb_repository",
    "get_kb_service",
    "get_video_resource_repository",
    "get_video_resource_service",
    "get_video_summary_task_repository",
    "get_video_summary_task_service",
    "get_video_qa_repository",
    "get_video_qa_service",
    "get_global_chat_repository",
    "get_global_qa_repository",
    "get_global_chat_service",
    "get_global_qa_service",
)

_TRUNCATE_SQL = """
TRUNCATE TABLE
    global_qa_records,
    global_chat_sessions,
    video_qa_records,
    video_summary_tasks,
    kb_video_relations,
    video_resources,
    knowledge_bases,
    users
RESTART IDENTITY CASCADE
"""


def _clear_dependency_caches() -> None:
    for name in _DEPENDENCY_CACHE_FUNCS:
        func = getattr(dependencies, name, None)
        if func is not None and hasattr(func, "cache_clear"):
            func.cache_clear()


@pytest.fixture(autouse=True)
def per_test_transaction(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run each test in a transaction and rollback after the test finishes."""
    connection = engine.connect()
    outer_transaction = connection.begin()

    db_session = Session(bind=connection, future=True, autocommit=False, autoflush=False)
    db_session.begin_nested()

    @event.listens_for(db_session, "after_transaction_end")
    def _restart_savepoint(session: Session, transaction) -> None:  # type: ignore[no-untyped-def]
        # Re-open nested transaction after repository-level commit()/rollback().
        parent = getattr(transaction, "_parent", None)
        if transaction.nested and (parent is None or not parent.nested):
            session.begin_nested()

    monkeypatch.setattr(dependencies, "SessionLocal", lambda: db_session)

    _clear_dependency_caches()
    connection.exec_driver_sql(_TRUNCATE_SQL)

    try:
        yield
    finally:
        _clear_dependency_caches()
        db_session.close()
        outer_transaction.rollback()
        connection.close()
