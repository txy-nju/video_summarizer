"""
BaseTask: shared Celery task base class with consistent retry semantics.

Provides:
- Exponential backoff with jitter (via compute_retry_countdown)
- Retry-exhaustion hooks (on_exhausted_retry / retry_or_fail)
- Helper properties (retries_remaining, is_last_attempt)
- Sensible default timeouts (task_soft_time_limit / task_time_limit)
- Lifecycle hooks for structured logging + dead-letter integration

Usage:
    @celery_app.task(
        base=BaseTask,
        bind=True,
        max_retries=3,
        default_retry_delay=30,
        ...
    )
    def my_task(self, ...):
        ...
        except Exception as exc:
            raise self.retry(exc=exc, countdown=self.compute_retry_countdown())

Design notes:
- We intentionally do NOT use Celery 5.4+ autoretry_for / retry_backoff kwargs
  because the project may not be pinned to a specific Celery version. The manual
  compute_retry_countdown() approach works on all Celery 5.x versions.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from celery import Task

logger = logging.getLogger(__name__)

# ── Global defaults ──────────────────────────────────────────────────────────
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_BASE = 2          # exponential base (delay * 2^retries)
DEFAULT_RETRY_BACKOFF_MAX = 300         # cap backoff at 5 minutes
DEFAULT_RETRY_JITTER_MIN = 0.75         # jitter multiplier floor
DEFAULT_RETRY_JITTER_MAX = 1.25         # jitter multiplier ceiling
DEFAULT_SOFT_TIME_LIMIT = 600           # 10 minutes
DEFAULT_TIME_LIMIT = 900                # 15 minutes


class BaseTask(Task):
    """Abstract base for all Celery tasks in video_summarizer.

    Class-level attributes (can be overridden per-task via decorator kwargs or
    subclass attributes):

        max_retries: int
        retry_backoff: bool           (default True)
        retry_backoff_base: int       (default 2)
        retry_backoff_max: int        (default 300)
        retry_jitter: bool            (default True)
        retry_jitter_min: float       (default 0.75)
        retry_jitter_max: float       (default 1.25)
        task_soft_time_limit: int     (default 600)
        task_time_limit: int          (default 900)
    """

    # ── Class-level defaults (Celery reads these when building the task) ──
    max_retries = DEFAULT_MAX_RETRIES
    retry_backoff = True
    retry_backoff_base = DEFAULT_RETRY_BACKOFF_BASE
    retry_backoff_max = DEFAULT_RETRY_BACKOFF_MAX
    retry_jitter = True
    retry_jitter_min = DEFAULT_RETRY_JITTER_MIN
    retry_jitter_max = DEFAULT_RETRY_JITTER_MAX
    task_soft_time_limit = DEFAULT_SOFT_TIME_LIMIT
    task_time_limit = DEFAULT_TIME_LIMIT

    # ── Public helpers for concrete tasks ────────────────────────────────────

    @property
    def retries_remaining(self) -> int:
        """How many retries are left (0 means this is the terminal attempt)."""
        current = self.request.retries or 0
        return max(self.max_retries - current, 0)

    @property
    def is_last_attempt(self) -> bool:
        """True when this is the final attempt before retries are exhausted."""
        return self.retries_remaining == 0

    def compute_retry_countdown(self) -> int:
        """Compute an exponential-backoff countdown with jitter.

        Formula:
            raw = min(retry_backoff_base ** retries * default_retry_delay,
                      retry_backoff_max)
            jittered = raw * random.uniform(retry_jitter_min, retry_jitter_max)

        Returns an integer number of seconds ≥ 1.
        """
        retries = self.request.retries or 0
        default_delay = getattr(self, "default_retry_delay", 60)

        raw = (self.retry_backoff_base ** retries) * default_delay
        capped = min(raw, self.retry_backoff_max)

        if self.retry_jitter:
            jittered = capped * random.uniform(self.retry_jitter_min, self.retry_jitter_max)
            return max(int(jittered), 1)

        return max(int(capped), 1)

    def retry_or_fail(self, exc: Exception) -> None:
        """Retry the task if attempts remain; otherwise invoke on_exhausted_retry
        and re-raise so Celery records a terminal FAILURE.

        Concrete tasks should call this in their ``except Exception`` block
        instead of raw ``self.retry()``.
        """
        if self.is_last_attempt:
            self.on_exhausted_retry(exc)
            raise exc
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())

    # ── Hooks (override in subclasses as needed) ─────────────────────────────

    def on_exhausted_retry(self, exc: Exception) -> None:
        """Hook: called when all retries are exhausted on this task invocation.

        The default implementation logs a CRITICAL-level message and records
        a dead-letter entry. Concrete tasks can override to perform additional
        cleanup (e.g. mark a DB status as irrecoverable, push a notification).
        """
        logger.critical(
            "Task %s exhausted all %d retries for args=%s. Final exception: %s: %s",
            self.name,
            self.max_retries,
            self.request.args,
            type(exc).__name__,
            exc,
            extra={
                "task_name": self.name,
                "task_id": self.request.id,
                "retries_exhausted": True,
                "retries_attempted": self.request.retries or 0,
                "max_retries": self.max_retries,
                "final_error": type(exc).__name__,
            },
        )

    # ── Celery lifecycle hooks ───────────────────────────────────────────────

    def on_failure(self, exc: Exception, task_id: str, args: tuple,
                   kwargs: dict, einfo: Any) -> None:
        """Celery invokes this when a task reaches terminal FAILURE.

        We emit a structured error log with retry-exhaustion metadata so that
        monitoring dashboards can distinguish transient retries from terminal
        failures.
        """
        retries_attempted = self.request.retries or 0
        exhausted = retries_attempted >= self.max_retries

        logger.error(
            "Task %s[%s] terminal failure after %d/%d attempts: %s",
            self.name,
            task_id,
            retries_attempted,
            self.max_retries,
            exc,
            extra={
                "task_name": self.name,
                "task_id": task_id,
                "retries_attempted": retries_attempted,
                "max_retries": self.max_retries,
                "retries_exhausted": exhausted,
                "terminal": True,
                "error_type": type(exc).__name__,
            },
        )

        # Save dead-letter entry for all terminal failures
        try:
            from backend.tasks.dead_letter import save_dead_letter
            save_dead_letter(
                task_name=self.name,
                task_id=task_id,
                args=args,
                exc=exc,
            )
        except Exception:
            logger.exception("Failed to save dead-letter for task %s[%s]", self.name, task_id)

        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval: Any, task_id: str, args: tuple,
                   kwargs: dict) -> None:
        """Log if the task succeeded after one or more retries."""
        retries = self.request.retries or 0
        if retries > 0:
            logger.info(
                "Task %s[%s] succeeded after %d retries",
                self.name, task_id, retries,
            )
        super().on_success(retval, task_id, args, kwargs)
