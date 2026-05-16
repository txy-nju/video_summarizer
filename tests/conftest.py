"""Pytest fixtures for isolated test database lifecycle.

Strategy:
- Create one temporary PostgreSQL database for the whole pytest session.
- Point the app to that database before test modules import backend code.
- Create the current schema once.
- Truncate all business tables before and after each test so application code can
  use real commits without outer transaction locks.
- Drop the temporary database when the session finishes.
"""

from __future__ import annotations

import gc
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


_DEFAULT_DATABASE_URL = "postgresql+psycopg2://postgres:123456@localhost:5432/video_summarizer_test"
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


def _admin_url(base_url: URL) -> URL:
    return base_url.set(database=os.environ.get("PYTEST_ADMIN_DATABASE", "postgres"))


def _create_temporary_database(base_url: URL) -> tuple[str, str]:
    db_name = f"{base_url.database}_pytest_{uuid4().hex[:8]}"
    admin_engine = create_engine(
        _admin_url(base_url).render_as_string(hide_password=False),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
    finally:
        admin_engine.dispose()

    return db_name, base_url.set(database=db_name).render_as_string(hide_password=False)


def _drop_temporary_database(base_url: URL, db_name: str) -> None:
    admin_engine = create_engine(
        _admin_url(base_url).render_as_string(hide_password=False),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{db_name}'
                  AND pid <> pg_backend_pid()
                """
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        admin_engine.dispose()


_BASE_DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
_BASE_DATABASE = make_url(_BASE_DATABASE_URL)
_TEMP_DATABASE_NAME, _TEMP_DATABASE_URL = _create_temporary_database(_BASE_DATABASE)
os.environ["DATABASE_URL"] = _TEMP_DATABASE_URL

from backend.config import get_settings  # noqa: E402

get_settings.cache_clear()

import backend.dependencies as dependencies  # noqa: E402
import backend.db.session as db_session_module  # noqa: E402
from backend.models.database import Base  # noqa: E402


def _clear_dependency_caches() -> None:
    for name in _DEPENDENCY_CACHE_FUNCS:
        func = getattr(dependencies, name, None)
        if func is not None and hasattr(func, "cache_clear"):
            func.cache_clear()
    gc.collect()


def _truncate_all_tables() -> None:
    with db_session_module.engine.begin() as connection:
        connection.exec_driver_sql(_TRUNCATE_SQL)


def _dispose_temporary_database() -> None:
    _clear_dependency_caches()
    db_session_module.engine.dispose()
    get_settings.cache_clear()
    _drop_temporary_database(_BASE_DATABASE, _TEMP_DATABASE_NAME)
    os.environ["DATABASE_URL"] = _BASE_DATABASE_URL


Base.metadata.create_all(db_session_module.engine)


@pytest.fixture(scope="session", autouse=True)
def configure_celery_for_testing() -> Iterator[None]:
    """在测试时配置 Celery 使用同步执行模式，避免需要 broker 和 worker。"""
    try:
        from celery import current_app
        current_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
        )
    except ImportError:
        # Celery 未安装，跳过配置
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def test_database_lifecycle() -> Iterator[None]:
    try:
        yield
    finally:
        _dispose_temporary_database()


@pytest.fixture(autouse=True)
def reset_database_state() -> Iterator[None]:
    _clear_dependency_caches()
    _truncate_all_tables()

    try:
        yield
    finally:
        _clear_dependency_caches()
        _truncate_all_tables()
