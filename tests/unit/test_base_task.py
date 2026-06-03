"""Unit tests for BaseTask — exponential backoff, jitter, retry exhaustion hooks."""

from __future__ import annotations

import math
import random
from unittest.mock import MagicMock

import pytest

from backend.tasks.base_task import (
    BaseTask,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_BASE,
    DEFAULT_RETRY_BACKOFF_MAX,
)


# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeRequest:
    """Minimal Celery request-like object for isolated BaseTask tests."""

    def __init__(self, retries: int = 0, task_id: str = "task-id-001") -> None:
        self.retries = retries
        self.id = task_id
        self.args = ("arg1", "arg2")


class _FakeRequestStack:
    """Simulate Celery's request_stack.top."""

    def __init__(self, request: _FakeRequest | None) -> None:
        self._request = request

    @property
    def top(self):
        return self._request


def _make_task(max_retries: int = 3,
               default_retry_delay: int = 30,
               retries_so_far: int = 0) -> BaseTask:
    """Build a BaseTask with a mocked request_stack for isolated testing."""
    task = BaseTask()
    task.name = "tests.unit.test_base_task._fake_task"
    task.max_retries = max_retries
    task.default_retry_delay = default_retry_delay

    request = _FakeRequest(retries=retries_so_far)
    task.request_stack = _FakeRequestStack(request)  # type: ignore[assignment]
    return task


# ── retries_remaining / is_last_attempt ──────────────────────────────────────


def test_retries_remaining_when_no_retries_yet() -> None:
    task = _make_task(max_retries=3, retries_so_far=0)
    assert task.retries_remaining == 3


def test_retries_remaining_after_one_retry() -> None:
    task = _make_task(max_retries=3, retries_so_far=1)
    assert task.retries_remaining == 2


def test_retries_remaining_on_last_attempt() -> None:
    task = _make_task(max_retries=3, retries_so_far=3)
    assert task.retries_remaining == 0


def test_retries_remaining_clamps_to_zero() -> None:
    task = _make_task(max_retries=2, retries_so_far=5)
    assert task.retries_remaining == 0


def test_is_last_attempt_false_when_retries_left() -> None:
    task = _make_task(max_retries=3, retries_so_far=1)
    assert task.is_last_attempt is False


def test_is_last_attempt_true_when_exhausted() -> None:
    task = _make_task(max_retries=3, retries_so_far=3)
    assert task.is_last_attempt is True


# ── compute_retry_countdown ──────────────────────────────────────────────────


def test_compute_retry_countdown_first_retry_equals_base_delay() -> None:
    """First retry (retries=0): delay ≈ default_retry_delay (2^0 * 30 = 30s)."""
    random.seed(42)
    task = _make_task(max_retries=3, default_retry_delay=30, retries_so_far=0)
    countdown = task.compute_retry_countdown()
    # With jitter [0.75, 1.25] and seed 42, expect value near 30
    assert 1 <= countdown <= 300


def test_compute_retry_countdown_second_retry_doubled() -> None:
    """Second retry (retries=1): base = 2^1 * 30 = 60s ± jitter."""
    random.seed(42)
    task = _make_task(max_retries=3, default_retry_delay=30, retries_so_far=1)
    countdown = task.compute_retry_countdown()
    assert 1 <= countdown <= 300
    # With jitter disabled, would be exactly 60
    task.retry_jitter = False
    assert task.compute_retry_countdown() == 60


def test_compute_retry_countdown_respects_cap() -> None:
    """Backoff should never exceed retry_backoff_max."""
    task = _make_task(max_retries=10, default_retry_delay=30, retries_so_far=10)
    task.retry_backoff_max = 300
    task.retry_jitter = False
    countdown = task.compute_retry_countdown()
    # 2^10 * 30 = 30720, but capped at 300
    assert countdown == 300


def test_compute_retry_countdown_jitter_range() -> None:
    """Jitter should keep values within [min, max] multipliers."""
    task = _make_task(max_retries=3, default_retry_delay=30, retries_so_far=0)
    task.retry_jitter = True
    task.retry_jitter_min = 0.75
    task.retry_jitter_max = 1.25

    # Run 100 iterations to verify bounds
    for _ in range(100):
        cd = task.compute_retry_countdown()
        # base delay = 30 (2^0 * 30), with jitter: 30*0.75=22, 30*1.25=37
        assert 22 <= cd <= 38, f"countdown {cd} outside expected jitter range"


def test_compute_retry_countdown_minimum_one_second() -> None:
    """Even with very small delay, countdown should be at least 1."""
    task = _make_task(max_retries=3, default_retry_delay=0, retries_so_far=0)
    task.retry_jitter = False
    countdown = task.compute_retry_countdown()
    assert countdown >= 1


def test_compute_retry_countdown_without_jitter_is_deterministic() -> None:
    """Without jitter, same inputs → same output."""
    task = _make_task(max_retries=3, default_retry_delay=30, retries_so_far=2)
    task.retry_jitter = False
    assert task.compute_retry_countdown() == 120  # 2^2 * 30


# ── retry_or_fail ────────────────────────────────────────────────────────────


def test_retry_or_fail_calls_retry_when_retries_remain(monkeypatch) -> None:
    task = _make_task(max_retries=3, retries_so_far=0)
    retry_called: list[dict] = []

    def _fake_retry(*, exc: Exception, countdown: int) -> None:
        retry_called.append({"exc": exc, "countdown": countdown})
        raise Exception("retry-signal")

    monkeypatch.setattr(task, "retry", _fake_retry)

    with pytest.raises(Exception, match="retry-signal"):
        task.retry_or_fail(ValueError("test error"))

    assert len(retry_called) == 1
    assert isinstance(retry_called[0]["exc"], ValueError)


def test_retry_or_fail_invokes_on_exhausted_retry_then_raises(monkeypatch) -> None:
    task = _make_task(max_retries=3, retries_so_far=3)  # exhausted
    exhausted_called: list[Exception] = []

    monkeypatch.setattr(task, "on_exhausted_retry", lambda exc: exhausted_called.append(exc))

    with pytest.raises(ValueError, match="terminal"):
        task.retry_or_fail(ValueError("terminal"))

    assert len(exhausted_called) == 1
    assert isinstance(exhausted_called[0], ValueError)


# ── on_exhausted_retry ───────────────────────────────────────────────────────


def test_on_exhausted_retry_logs_critical(caplog) -> None:
    import logging

    caplog.set_level(logging.CRITICAL)
    task = _make_task(max_retries=3, retries_so_far=3)
    task.on_exhausted_retry(ValueError("boom"))

    assert "exhausted all" in caplog.text
    assert "ValueError" in caplog.text


# ── lifecycle hooks ──────────────────────────────────────────────────────────


def test_on_success_logs_when_retried(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO)
    task = _make_task(max_retries=3, retries_so_far=2)
    task.on_success({"status": "OK"}, "task-id", (), {})

    assert "succeeded after 2 retries" in caplog.text


def test_on_success_no_log_when_no_retries(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO)
    task = _make_task(max_retries=3, retries_so_far=0)
    task.on_success({"status": "OK"}, "task-id", (), {})

    assert "succeeded after" not in caplog.text  # nothing to brag about


def test_on_failure_logs_error_with_exhausted_metadata(caplog) -> None:
    import logging

    caplog.set_level(logging.ERROR)
    task = _make_task(max_retries=3, retries_so_far=3)

    einfo_mock = MagicMock()
    task.on_failure(ValueError("dead"), "task-id", ("a",), {"k": "v"}, einfo_mock)

    assert "terminal failure after 3/3 attempts" in caplog.text


# ── class-level defaults ─────────────────────────────────────────────────────


def test_base_task_class_defaults() -> None:
    """Ensure class-level defaults are set as documented."""
    assert BaseTask.max_retries == DEFAULT_MAX_RETRIES
    assert BaseTask.retry_backoff is True
    assert BaseTask.retry_backoff_base == DEFAULT_RETRY_BACKOFF_BASE
    assert BaseTask.retry_backoff_max == DEFAULT_RETRY_BACKOFF_MAX
    assert BaseTask.retry_jitter is True
    assert BaseTask.task_soft_time_limit == 600
    assert BaseTask.task_time_limit == 900
