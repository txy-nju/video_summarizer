"""Pytest fixtures for isolated test database lifecycle.

Strategy:
- Create one temporary PostgreSQL database for the whole pytest session.
- Point the app to that database before test modules import backend code.
- Create the current schema once.
- Truncate all business tables before and after each test so application code can
  use real commits without outer transaction locks.
- Drop the temporary database when the session finishes.

Cleanup layers (defence in depth):
1. session fixture ``test_database_lifecycle`` teardown (normal exit)
2. ``atexit`` handler (covers Ctrl+C, early crash, collection failure)
3. ``DROP DATABASE IF EXISTS`` is idempotent — double cleanup is harmless
"""

from __future__ import annotations

import atexit
import gc
import logging
import os
import sys

os.environ["OTEL_ENABLED"] = "false"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "True"
os.environ["CELERY_TASK_EAGER_PROPAGATES"] = "True"


from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

_logger = logging.getLogger(__name__)

_DEFAULT_DATABASE_URL = "postgresql+psycopg2://postgres:123456@localhost:5432/video_summarizer_test"
_TRUNCATE_SQL = """
TRUNCATE TABLE
    device_tokens,
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

# ---------------------------------------------------------------------------
# 清理状态标记 — 防止重复清理
# ---------------------------------------------------------------------------
_DATABASE_CLEANED_UP = False


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

    try:
        print(f"[conftest] Created temporary test database: {db_name}", file=sys.stderr)
    except Exception:
        pass
    return db_name, base_url.set(database=db_name).render_as_string(hide_password=False)


def _drop_temporary_database(base_url: URL, db_name: str) -> None:
    """Drop the temporary database, terminating any lingering connections first.

    This function is intentionally standalone (no dependency on other project
    modules) so it can be called safely from an atexit handler where imported
    modules may already be partially torn down.
    """
    global _DATABASE_CLEANED_UP
    if _DATABASE_CLEANED_UP:
        return

    admin_engine: object = None
    try:
        admin_engine = create_engine(
            _admin_url(base_url).render_as_string(hide_password=False),
            future=True,
            isolation_level="AUTOCOMMIT",
        )
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
        _DATABASE_CLEANED_UP = True
        try:
            print(f"[conftest] Dropped temporary test database: {db_name}", file=sys.stderr)
        except Exception:
            pass  # stderr may already be closed during atexit
    except Exception:
        # Best-effort cleanup — don't let an error here crash teardown
        try:
            print(
                f"[conftest] WARNING: Failed to drop temporary test database {db_name}",
                file=sys.stderr,
            )
        except Exception:
            pass  # stderr may already be closed during atexit
    finally:
        if admin_engine is not None:
            try:
                admin_engine.dispose()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 创建临时数据库并注册多重清理保障
# ---------------------------------------------------------------------------
_BASE_DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
_BASE_DATABASE = make_url(_BASE_DATABASE_URL)
_TEMP_DATABASE_NAME, _TEMP_DATABASE_URL = _create_temporary_database(_BASE_DATABASE)
os.environ["DATABASE_URL"] = _TEMP_DATABASE_URL

# 第一重保障: atexit — 即使 pytest 崩溃或被 Ctrl+C 也能清理
atexit.register(_drop_temporary_database, _BASE_DATABASE, _TEMP_DATABASE_NAME)

# 第二重保障: unraisable hook — 覆盖更极端的退出方式
try:
    _prev_unraisable_hook = sys.unraisablehook

    def _unraisable_cleanup_hook(unraisable):  # pragma: no cover
        _drop_temporary_database(_BASE_DATABASE, _TEMP_DATABASE_NAME)
        if _prev_unraisable_hook is not None:
            _prev_unraisable_hook(unraisable)

    sys.unraisablehook = _unraisable_cleanup_hook
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# 导入应用代码（此时 DATABASE_URL 已指向临时库）
# ---------------------------------------------------------------------------
from backend.config import get_settings  # noqa: E402

get_settings.cache_clear()

import backend.dependencies as dependencies  # noqa: E402
import backend.db.session as db_session_module  # noqa: E402
from backend.models.database import Base  # noqa: E402


def _clear_dependency_caches() -> None:
    for name in dir(dependencies):
        func = getattr(dependencies, name, None)
        if callable(func) and hasattr(func, "cache_clear"):
            func.cache_clear()
    gc.collect()


def _truncate_all_tables() -> None:
    with db_session_module.engine.begin() as connection:
        connection.exec_driver_sql(_TRUNCATE_SQL)


def _dispose_temporary_database() -> None:
    """Wrapped cleanup that tolerates failures in app-level teardown steps.

    Application modules (engine, caches) may already be partially torn down;
    this function ensures the critical DB-drop step always runs regardless.
    """
    # Step 1: clear caches (best-effort)
    try:
        _clear_dependency_caches()
    except Exception:
        _logger.debug("Cache teardown failed (non-fatal)", exc_info=True)

    # Step 2: dispose the app engine (best-effort)
    try:
        db_session_module.engine.dispose()
    except Exception:
        _logger.debug("Engine dispose failed (non-fatal)", exc_info=True)

    # Step 3: clear settings cache (best-effort)
    try:
        get_settings.cache_clear()
    except Exception:
        _logger.debug("Settings cache clear failed (non-fatal)", exc_info=True)

    # Step 4: drop the database (CRITICAL — always attempt)
    _drop_temporary_database(_BASE_DATABASE, _TEMP_DATABASE_NAME)

    # Step 5: restore original URL (best-effort)
    try:
        os.environ["DATABASE_URL"] = _BASE_DATABASE_URL
    except Exception:
        pass


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
        pass

    try:
        from backend.tasks.celery_app import celery_app
        celery_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
        )
    except ImportError:
        pass
    yield


# ---------------------------------------------------------------------------
# 第三重保障: session 级 fixture（主清理路径 — 正常退出时走这里）
# ---------------------------------------------------------------------------
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
