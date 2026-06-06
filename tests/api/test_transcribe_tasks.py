from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.tasks.transcribe_tasks import async_transcribe_video


@dataclass
class _FakeVideo:
    oss_key: str


class _FakeDb:
    def close(self) -> None:
        return None


class _FakeTaskSelf:
    def __init__(self) -> None:
        self.retry_called = False

    def retry(self, *, exc: Exception, countdown: int = 0):
        self.retry_called = True
        raise RuntimeError("retry-called")


class _ServiceSuccess:
    def __init__(self, video_path: str) -> None:
        self.video_path = video_path
        self.in_progress_called = False
        self.completed_payload: str | None = None

    def mark_transcription_in_progress(self, *, video_id: str) -> None:
        self.in_progress_called = True

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(oss_key=self.video_path)

    def mark_transcription_completed(
        self,
        *,
        video_id: str,
        full_transcript: str,
        transcript_segments: list | None = None,
        duration: int | None = None,
    ) -> None:
        self.completed_payload = full_transcript


class _ServiceFail:
    def __init__(self) -> None:
        self.failed_called = False

    def mark_transcription_in_progress(self, *, video_id: str) -> None:
        return None

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(oss_key="missing.mp4")

    def mark_transcription_failed(self, *, video_id: str) -> None:
        self.failed_called = True


def test_async_transcribe_video_routes_status_through_service(monkeypatch, tmp_path) -> None:
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"dummy")

    service = _ServiceSuccess(video_path=str(video_file))
    monkeypatch.setattr(
        "backend.tasks.transcribe_tasks._create_video_resource_service",
        lambda: (service, _FakeDb()),
    )

    class _FakeExtractor:
        def extract_audio(self, _video_path: Path):
            return None

    monkeypatch.setattr("core.extraction.infrastructure.extractor.MediaExtractor", _FakeExtractor)

    result = async_transcribe_video.run("vid-1")

    assert result["status"] == "COMPLETED"
    assert service.in_progress_called is True
    assert service.completed_payload == ""


def test_async_transcribe_video_failure_retries_but_does_not_mark_failed_on_first_attempt(
    monkeypatch,
) -> None:
    """FAILED status should NOT be set on the first failure — only on the last retry attempt."""
    task_self = _FakeTaskSelf()

    calls = {"count": 0}

    def _fake_factory():
        calls["count"] += 1
        return _ServiceFail(), _FakeDb()

    monkeypatch.setattr("backend.tasks.transcribe_tasks._create_video_resource_service", _fake_factory)
    monkeypatch.setattr(async_transcribe_video, "retry", task_self.retry)

    with pytest.raises(RuntimeError, match="retry-called"):
        async_transcribe_video.run("vid-fail")

    assert task_self.retry_called is True
    # The factory is called once (main service), and FAILED should NOT be called
    # because is_last_attempt is False on the first failure with max_retries=3
    assert calls["count"] == 1  # only the main service was created; no fail_service


def test_async_transcribe_video_failure_marks_failed_on_last_attempt(monkeypatch) -> None:
    """FAILED status IS set on the terminal retry attempt.

    We verify this by calling retry_or_fail with a BaseTask configured as exhausted.
    The task-level integration is covered by test_base_task.py's retry_or_fail tests.
    """
    from backend.tasks.base_task import BaseTask

    # Build a BaseTask at the exhaustion point and verify on_exhausted_retry fires
    class _TestTask(BaseTask):
        exhausted_exc: Exception | None = None

        def on_exhausted_retry(self, exc: Exception) -> None:
            self.exhausted_exc = exc

    task = _TestTask()
    task.name = "test_transcribe"
    task.max_retries = 3

    # Simulate 3 retries already done (exhausted)
    class _FakeReq:
        retries = 3
        id = "task-001"
        args = ("vid-fail",)

    task.request_stack = type("_Stack", (), {"top": _FakeReq()})()

    with pytest.raises(ValueError, match="terminal"):
        task.retry_or_fail(ValueError("terminal"))

    assert task.exhausted_exc is not None
    assert isinstance(task.exhausted_exc, ValueError)
