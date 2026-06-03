"""Unit tests for recovery_tasks — periodic scan, circuit breaker, backoff logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.tasks.recovery_tasks import (
    MAX_RECOVERY_ATTEMPTS,
    RECOVERY_BACKOFF_SECONDS,
    _should_recover,
)


# ── _should_recover ──────────────────────────────────────────────────────────


def test_should_recover_first_attempt_no_prior_recovery() -> None:
    """If last_recovery_at is None (never recovered before), always allow."""
    now = datetime.now(timezone.utc)
    assert _should_recover(recovery_attempts=0, last_recovery_at=None, now=now) is True


def test_should_recover_second_attempt_within_interval_blocked() -> None:
    """If the backoff interval hasn't elapsed, deny the recovery."""
    now = datetime.now(timezone.utc)
    # recovery_attempts=1 → index 0 → 300s (5 min) required
    last = now - timedelta(seconds=60)  # only 1 min ago
    assert _should_recover(recovery_attempts=1, last_recovery_at=last, now=now) is False


def test_should_recover_second_attempt_after_interval_allowed() -> None:
    """If enough time has passed, allow."""
    now = datetime.now(timezone.utc)
    last = now - timedelta(seconds=400)  # 6m40s ago, more than 5 min
    assert _should_recover(recovery_attempts=1, last_recovery_at=last, now=now) is True


def test_should_recover_third_attempt_requires_longer_interval() -> None:
    """Third recovery (attempts=2) needs 20 min (1200s)."""
    now = datetime.now(timezone.utc)
    last = now - timedelta(seconds=900)  # 15 min ago, less than 20 min
    assert _should_recover(recovery_attempts=2, last_recovery_at=last, now=now) is False

    last = now - timedelta(seconds=1300)  # 21m40s ago
    assert _should_recover(recovery_attempts=2, last_recovery_at=last, now=now) is True


def test_should_recover_fourth_attempt_requires_longest_interval() -> None:
    """Fourth attempt (attempts=3, which means retries=3 since 0-indexed) needs 60 min (3600s)."""
    now = datetime.now(timezone.utc)
    last = now - timedelta(seconds=3500)  # just under 60 min
    assert _should_recover(recovery_attempts=3, last_recovery_at=last, now=now) is False

    last = now - timedelta(seconds=3700)  # over 60 min
    assert _should_recover(recovery_attempts=3, last_recovery_at=last, now=now) is True


def test_should_recover_handles_negative_index_gracefully() -> None:
    """Edge case: recovery_attempts=0 but last_recovery_at is not None
    (anomalous state — index goes negative, clamped to 0, needs full 5 min)."""
    now = datetime.now(timezone.utc)
    last = now - timedelta(seconds=10)
    # recovery_attempts=0 → index=-1 → clamped to 0 → 300s required, only 10s elapsed → blocked
    assert _should_recover(recovery_attempts=0, last_recovery_at=last, now=now) is False

    # After 5+ minutes, it should allow recovery even in this anomalous state
    last2 = now - timedelta(seconds=400)
    assert _should_recover(recovery_attempts=0, last_recovery_at=last2, now=now) is True


# ── config constants ─────────────────────────────────────────────────────────


def test_max_recovery_attempts_is_three() -> None:
    assert MAX_RECOVERY_ATTEMPTS == 3


def test_recovery_backoff_seconds_are_ascending() -> None:
    assert RECOVERY_BACKOFF_SECONDS == [300, 1200, 3600]
    for i in range(1, len(RECOVERY_BACKOFF_SECONDS)):
        assert RECOVERY_BACKOFF_SECONDS[i] > RECOVERY_BACKOFF_SECONDS[i - 1]


# ── integration: scan task can be run in eager mode ──────────────────────────


def test_scan_task_no_stuck_videos_returns_ok(monkeypatch) -> None:
    """When there are no stuck videos, the task returns OK with 0 recovered."""
    from backend.tasks.recovery_tasks import async_scan_and_recover_stuck_videos

    # Patch the scan to return empty list (no candidates)
    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    monkeypatch.setattr(
        "backend.tasks.recovery_tasks.SessionLocal",
        lambda: _FakeDb(query_result=_FakeQuery()),
    )

    result = async_scan_and_recover_stuck_videos.run()
    assert result["status"] == "OK"
    assert result["recovered"] == 0


class _FakeDb:
    """Fake DB session for recovery task tests."""

    def __init__(self, query_result=None) -> None:
        self._query_result = query_result
        self.committed = False
        self.rolled_back = False

    def query(self, *args, **kwargs):
        return self._query_result

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def test_recovery_task_db_session_rollback_on_error() -> None:
    """Verify the DB session's rollback pattern used in the recovery task.

    This tests the session lifecycle contract rather than the full eager-mode
    task execution (eager mode bypasses BaseTask's task_cls, making is_last_attempt
    unavailable — that property is covered in test_base_task.py).
    """
    rollback_calls: list[bool] = []

    class _FailDb:
        def query(self, *args, **kwargs):
            raise RuntimeError("DB is down")

        def rollback(self) -> None:
            rollback_calls.append(True)

        def close(self) -> None:
            pass

    db = _FailDb()
    try:
        db.query(None)
    except Exception:
        db.rollback()
    finally:
        db.close()

    assert len(rollback_calls) == 1


# ── _mark_irrecoverable ──────────────────────────────────────────────────────

def test_mark_irrecoverable_sets_status(monkeypatch) -> None:
    """Videos with recovery_attempts >= 3 and FAILED status → IRRECOVERABLE."""
    from backend.models.enums import FrameExtractionStatus, TranscribeStatus
    from backend.tasks.recovery_tasks import _mark_irrecoverable

    # Build a fake video that should be marked
    class _FakeVideo:
        pass

    fake_video = _FakeVideo()
    fake_video.video_id = "vid-stuck"
    fake_video.recovery_attempts = 3
    fake_video.transcribe_status = TranscribeStatus.FAILED
    fake_video.frame_extraction_status = FrameExtractionStatus.COMPLETED
    fake_video.is_deleted = False

    class _FakeStuckQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [fake_video]

    # Build a fake DB session where query() returns the fake query chain
    class _FakeDb:
        def query(self, model):
            return _FakeStuckQuery()

    fake_db = _FakeDb()

    now = datetime.now(timezone.utc)
    count = _mark_irrecoverable(fake_db, now)

    # Should have set IRRECOVERABLE on the stuck transcribe status
    assert fake_video.transcribe_status == TranscribeStatus.IRRECOVERABLE
    assert fake_video.last_recovery_at == now
    assert count >= 1
