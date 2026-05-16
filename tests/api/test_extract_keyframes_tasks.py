from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.tasks.extract_keyframes_tasks import async_extract_keyframes


@dataclass
class _FakeVideo:
    owner_id: str
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
    def __init__(self, *, video_path: str) -> None:
        self.video_path = video_path
        self.in_progress_called = False
        self.completed_keyframes: list[dict] | None = None
        self.completed_prefix: str | None = None

    def mark_frame_extraction_in_progress(self, *, video_id: str) -> None:
        self.in_progress_called = True

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(owner_id="owner-1", oss_key=self.video_path)

    def mark_frame_extraction_completed(
        self,
        *,
        video_id: str,
        keyframes: list[dict],
        keyframes_oss_prefix: str,
    ) -> None:
        self.completed_keyframes = keyframes
        self.completed_prefix = keyframes_oss_prefix


class _ServiceFail:
    def __init__(self) -> None:
        self.failed_called = False

    def mark_frame_extraction_in_progress(self, *, video_id: str) -> None:
        return None

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(owner_id="owner-1", oss_key="missing.mp4")

    def mark_frame_extraction_failed(self, *, video_id: str) -> None:
        self.failed_called = True


def test_async_extract_keyframes_routes_status_through_service(monkeypatch, tmp_path) -> None:
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"dummy")

    service = _ServiceSuccess(video_path=str(video_file))
    monkeypatch.setattr(
        "backend.tasks.extract_keyframes_tasks._create_video_resource_service",
        lambda: (service, _FakeDb()),
    )

    class _FakeExtractor:
        def extract_frames(self, _video_path: Path, interval: int):
            return [
                {
                    "time": "00:00:01",
                    "scene_change_score": 0.9,
                    "scene_change_level": "high",
                    "frame_file": "frame_0001.jpg",
                }
            ]

    monkeypatch.setattr("core.extraction.infrastructure.extractor.MediaExtractor", _FakeExtractor)

    result = async_extract_keyframes.run("vid-1")

    assert result["status"] == "COMPLETED"
    assert result["keyframes_count"] == 1
    assert service.in_progress_called is True
    assert service.completed_prefix == "frames/owner-1/vid-1/"
    assert service.completed_keyframes is not None
    assert service.completed_keyframes[0]["oss_key"].endswith("/frame_0001.jpg")


def test_async_extract_keyframes_failure_marks_failed_and_retries(monkeypatch) -> None:
    task_self = _FakeTaskSelf()
    main_service = _ServiceFail()
    fail_service = _ServiceFail()

    calls = {"count": 0}

    def _fake_factory():
        calls["count"] += 1
        if calls["count"] == 1:
            return main_service, _FakeDb()
        return fail_service, _FakeDb()

    monkeypatch.setattr("backend.tasks.extract_keyframes_tasks._create_video_resource_service", _fake_factory)
    monkeypatch.setattr(async_extract_keyframes, "retry", task_self.retry)

    with pytest.raises(RuntimeError, match="retry-called"):
        async_extract_keyframes.run("vid-fail")

    assert task_self.retry_called is True
    assert fail_service.failed_called is True
