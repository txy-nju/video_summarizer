



from __future__ import annotations

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings
from backend.db.session import engine
from backend.models.database import Base


def _parse_db_name(database_url: str) -> str:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError("DATABASE_URL must include database name")
    return db_name


def _build_admin_conn_info(database_url: str) -> dict[str, str | int]:
    parsed = urlparse(database_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": "postgres",
    }


def _ensure_database_exists(database_url: str) -> None:
    target_db = _parse_db_name(database_url)
    conn_info = _build_admin_conn_info(database_url)

    conn = psycopg2.connect(**conn_info)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            exists = cursor.fetchone() is not None
            if not exists:
                cursor.execute(f'CREATE DATABASE "{target_db}"')
                print(f"[OK] Created database: {target_db}")
            else:
                print(f"[SKIP] Database already exists: {target_db}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create database and schema for video_summarizer")
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="Create database from DATABASE_URL if it does not exist",
    )
    parser.add_argument(
        "--drop-all",
        action="store_true",
        help="Drop all existing tables before create_all()",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    settings = get_settings()

    print(f"[INFO] Project root: {root}")
    print(f"[INFO] DATABASE_URL: {settings.database_url}")

    if args.create_db:
        _ensure_database_exists(settings.database_url)

    if args.drop_all:
        Base.metadata.drop_all(bind=engine)
        print("[OK] Dropped all tables")

    Base.metadata.create_all(bind=engine)
    print("[OK] Created schema from ORM metadata")


if __name__ == "__main__":
    main()
