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

    def retry(self, *, exc: Exception):
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

    def mark_transcription_completed(self, *, video_id: str, full_transcript: str) -> None:
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


def test_async_transcribe_video_failure_marks_failed_and_retries(monkeypatch) -> None:
    task_self = _FakeTaskSelf()
    main_service = _ServiceFail()
    fail_service = _ServiceFail()

    calls = {"count": 0}

    def _fake_factory():
        calls["count"] += 1
        if calls["count"] == 1:
            return main_service, _FakeDb()
        return fail_service, _FakeDb()

    monkeypatch.setattr("backend.tasks.transcribe_tasks._create_video_resource_service", _fake_factory)
    monkeypatch.setattr(async_transcribe_video, "retry", task_self.retry)

    with pytest.raises(RuntimeError, match="retry-called"):
        async_transcribe_video.run("vid-fail")

    assert task_self.retry_called is True
    assert fail_service.failed_called is True
