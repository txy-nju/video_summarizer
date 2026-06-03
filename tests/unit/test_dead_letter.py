"""Unit tests for dead-letter recording (Redis-backed terminal failure storage)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tasks.dead_letter import (
    DEAD_LETTER_KEY_PREFIX,
    DEAD_LETTER_TTL_SECONDS,
    count_dead_letters,
    get_dead_letters,
    purge_dead_letters,
    save_dead_letter,
)


class _FakeRedis:
    """Minimal fake Redis client for dead-letter tests."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, float]] = {}  # key -> {member_json: score}

    def zadd(self, key: str, mapping: dict) -> int:
        self._data.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in self._data[key]:
                added += 1
            self._data[key][member] = score
        return added

    def zcard(self, key: str) -> int:
        return len(self._data.get(key, {}))

    def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        entries = self._data.get(key, {})
        sorted_entries = sorted(entries.items(), key=lambda x: x[1], reverse=True)
        return [m for m, _ in sorted_entries[start : end + 1]]

    def expire(self, key: str, ttl: int) -> bool:
        return True

    def delete(self, key: str) -> int:
        if key in self._data:
            count = len(self._data.pop(key))
            return count
        return 0


@pytest.fixture(autouse=True)
def _fake_redis_client(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(
        "backend.tasks.dead_letter._get_redis_client",
        lambda: fake,
    )
    return fake


# ── save_dead_letter ─────────────────────────────────────────────────────────


def test_save_dead_letter_adds_entry(_fake_redis_client) -> None:
    save_dead_letter(
        task_name="backend.tasks.test.task",
        task_id="task-001",
        args=("video-1",),
        exc=ValueError("simulated"),
    )

    key = f"{DEAD_LETTER_KEY_PREFIX}backend.tasks.test.task"
    assert key in _fake_redis_client._data
    assert len(_fake_redis_client._data[key]) == 1

    entry = json.loads(list(_fake_redis_client._data[key].keys())[0])
    assert entry["task_id"] == "task-001"
    assert entry["error_type"] == "ValueError"
    assert entry["error_message"] == "simulated"
    assert "timestamp" in entry
    assert "timestamp_iso" in entry


def test_save_dead_letter_multiple_entries_same_task(_fake_redis_client) -> None:
    save_dead_letter(
        task_name="backend.tasks.test.task",
        task_id="task-001",
        args=(),
        exc=RuntimeError("first"),
    )
    save_dead_letter(
        task_name="backend.tasks.test.task",
        task_id="task-002",
        args=(),
        exc=RuntimeError("second"),
    )

    key = f"{DEAD_LETTER_KEY_PREFIX}backend.tasks.test.task"
    assert len(_fake_redis_client._data[key]) == 2


def test_save_dead_letter_different_task_names(_fake_redis_client) -> None:
    save_dead_letter(
        task_name="backend.tasks.test.task_a",
        task_id="a-1",
        args=(),
        exc=Exception("a"),
    )
    save_dead_letter(
        task_name="backend.tasks.test.task_b",
        task_id="b-1",
        args=(),
        exc=Exception("b"),
    )

    assert len(_fake_redis_client._data[f"{DEAD_LETTER_KEY_PREFIX}backend.tasks.test.task_a"]) == 1
    assert len(_fake_redis_client._data[f"{DEAD_LETTER_KEY_PREFIX}backend.tasks.test.task_b"]) == 1


# ── get_dead_letters ─────────────────────────────────────────────────────────


def test_get_dead_letters_returns_newest_first(_fake_redis_client) -> None:
    save_dead_letter(task_name="test.get.task", task_id="old", args=(), exc=Exception("old"))
    import time

    time.sleep(0.01)
    save_dead_letter(task_name="test.get.task", task_id="new", args=(), exc=Exception("new"))

    entries = get_dead_letters("test.get.task", limit=10)
    assert len(entries) == 2
    # newest first (higher score)
    assert entries[0]["task_id"] == "new"
    assert entries[1]["task_id"] == "old"


def test_get_dead_letters_respects_limit(_fake_redis_client) -> None:
    for i in range(5):
        import time

        time.sleep(0.01)
        save_dead_letter(task_name="test.limit.task", task_id=f"task-{i}", args=(), exc=Exception(str(i)))

    entries = get_dead_letters("test.limit.task", limit=3)
    assert len(entries) == 3


def test_get_dead_letters_empty() -> None:
    entries = get_dead_letters("nonexistent.task", limit=10)
    assert entries == []


# ── count_dead_letters ───────────────────────────────────────────────────────


def test_count_dead_letters(_fake_redis_client) -> None:
    assert count_dead_letters("test.count.task") == 0
    save_dead_letter(task_name="test.count.task", task_id="1", args=(), exc=Exception("e"))
    assert count_dead_letters("test.count.task") == 1
    save_dead_letter(task_name="test.count.task", task_id="2", args=(), exc=Exception("e"))
    assert count_dead_letters("test.count.task") == 2


# ── purge_dead_letters ───────────────────────────────────────────────────────


def test_purge_dead_letters(_fake_redis_client) -> None:
    save_dead_letter(task_name="test.purge.task", task_id="1", args=(), exc=Exception("e"))
    save_dead_letter(task_name="test.purge.task", task_id="2", args=(), exc=Exception("e"))

    assert count_dead_letters("test.purge.task") == 2
    removed = purge_dead_letters("test.purge.task")
    assert removed == 2
    assert count_dead_letters("test.purge.task") == 0


# ── TTL ──────────────────────────────────────────────────────────────────────


def test_save_dead_letter_sets_ttl(monkeypatch) -> None:
    """Verify that expire() is called with the correct TTL."""
    fake_redis = _FakeRedis()
    expire_calls: list[tuple] = []

    def _track_expire(key: str, ttl: int) -> bool:
        expire_calls.append((key, ttl))
        return True

    fake_redis.expire = _track_expire

    monkeypatch.setattr("backend.tasks.dead_letter._get_redis_client", lambda: fake_redis)
    save_dead_letter(task_name="test.ttl.task", task_id="1", args=(), exc=Exception("e"))

    assert len(expire_calls) == 1
    assert expire_calls[0][1] == DEAD_LETTER_TTL_SECONDS
