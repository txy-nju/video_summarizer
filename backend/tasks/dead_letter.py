"""
Dead-letter recording for terminal Celery task failures.

Stores terminal failures in a Redis sorted set (per task name) so that
operators can inspect and manually recover from irrecoverable failures.

Key format:   dead_letter:{task_name}
Value format:  JSON blob with task_id, args, error_type, error_message, timestamp
Score:         Unix timestamp of failure
TTL:           7 days (auto-cleanup)
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis as redis_lib

logger = logging.getLogger(__name__)

DEAD_LETTER_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEAD_LETTER_KEY_PREFIX = "dead_letter:"


def _get_redis_client() -> "redis_lib.Redis":
    """Create a Redis client from the Celery broker URL (reuse existing config)."""
    import redis as redis_lib

    import os
    redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    # Use a dedicated DB index for dead letters to avoid cluttering the broker
    # (the broker URL typically ends with /0; we replace with /3)
    if "/" in redis_url:
        base, db = redis_url.rsplit("/", 1)
        # If there are multiple path segments just replace the last one
        dead_letter_url = f"{base}/3"
    else:
        dead_letter_url = f"{redis_url}/3"

    return redis_lib.Redis.from_url(dead_letter_url, decode_responses=True)


def save_dead_letter(
    *,
    task_name: str,
    task_id: str,
    args: tuple,
    exc: Exception,
) -> None:
    """Persist a terminal task failure to Redis for later inspection.

    Args:
        task_name: The registered task name (e.g. ``backend.tasks.transcribe_tasks.async_transcribe_video``).
        task_id: Celery task UUID.
        args: Positional arguments the task was called with.
        exc: The exception that caused the terminal failure.
    """
    try:
        client = _get_redis_client()
        key = f"{DEAD_LETTER_KEY_PREFIX}{task_name}"

        entry = json.dumps(
            {
                "task_id": task_id,
                "task_name": task_name,
                "args": [_serialize_arg(a) for a in args],
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "timestamp": time.time(),
                "timestamp_iso": _iso_now(),
            },
            ensure_ascii=False,
        )

        score = time.time()
        client.zadd(key, {entry: score})
        client.expire(key, DEAD_LETTER_TTL_SECONDS)

        logger.debug("Dead-letter saved: task=%s, task_id=%s", task_name, task_id)
    except Exception:
        logger.exception("Failed to persist dead-letter for task %s[%s]", task_name, task_id)


def get_dead_letters(
    task_name: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Retrieve the most recent dead-letter entries for a task.

    Args:
        task_name: The registered Celery task name.
        limit: Max entries to return (default 50).
        offset: Skip the first N entries (for pagination).

    Returns:
        List of dead-letter entry dicts, newest first.
    """
    try:
        client = _get_redis_client()
        key = f"{DEAD_LETTER_KEY_PREFIX}{task_name}"

        # ZREVRANGE: highest score (= newest) first
        raw_entries = client.zrevrange(key, offset, offset + limit - 1)
        return [json.loads(e) for e in raw_entries]
    except Exception:
        logger.exception("Failed to read dead-letters for task %s", task_name)
        return []


def count_dead_letters(task_name: str) -> int:
    """Return the number of dead-letter entries for a task."""
    try:
        client = _get_redis_client()
        key = f"{DEAD_LETTER_KEY_PREFIX}{task_name}"
        return client.zcard(key) or 0
    except Exception:
        return 0


def purge_dead_letters(task_name: str) -> int:
    """Delete all dead-letter entries for a task (e.g. after manual recovery)."""
    try:
        client = _get_redis_client()
        key = f"{DEAD_LETTER_KEY_PREFIX}{task_name}"
        count = client.zcard(key) or 0
        client.delete(key)
        return int(count)
    except Exception:
        return 0


# ── helpers ──────────────────────────────────────────────────────────────────

def _serialize_arg(arg: object) -> str:
    """Safe string representation of a task argument."""
    try:
        return str(arg)
    except Exception:
        return repr(arg)


def _iso_now() -> str:
    """ISO-8601 UTC timestamp string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
