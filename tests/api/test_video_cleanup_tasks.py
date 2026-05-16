from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.tasks.video_cleanup_tasks import async_cascade_delete_video


@dataclass
class _FakeVideo:
    owner_id: str
    oss_key: str
    transcript_vector_ids: list | None = None


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
    def __init__(self) -> None:
        self.deletion_in_progress_called = False
        self.purge_called = False

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(owner_id="owner-1", oss_key="videos/test.mp4")

    def mark_deletion_in_progress(self, *, video_id: str) -> None:
        self.deletion_in_progress_called = True

    def purge_video(self, *, video_id: str) -> None:
        self.purge_called = True


class _ServiceFail:
    def __init__(self) -> None:
        self.deletion_failed_called = False

    def get_video_resource_for_system(self, *, video_id: str):
        return _FakeVideo(owner_id="owner-1", oss_key="videos/test.mp4")

    def mark_deletion_in_progress(self, *, video_id: str) -> None:
        raise ValueError("simulated OSS failure")

    def mark_deletion_failed(self, *, video_id: str) -> None:
        self.deletion_failed_called = True


def test_async_cascade_delete_routes_status_through_service(monkeypatch) -> None:
    service = _ServiceSuccess()
    call_count = 0

    def _factory():
        nonlocal call_count
        call_count += 1
        return service, _FakeDb()

    monkeypatch.setattr(
        "backend.tasks.video_cleanup_tasks._create_video_resource_service",
        _factory,
    )

    result = async_cascade_delete_video.run("video-abc")

    assert result == {"video_id": "video-abc", "deletion_status": "PURGED"}
    assert service.deletion_in_progress_called is True
    assert service.purge_called is True


def test_async_cascade_delete_calls_mark_deletion_failed_on_error(monkeypatch) -> None:
    fail_service = _ServiceFail()

    call_count = 0

    def _factory():
        nonlocal call_count
        call_count += 1
        return fail_service, _FakeDb()

    monkeypatch.setattr(
        "backend.tasks.video_cleanup_tasks._create_video_resource_service",
        _factory,
    )

    task_self = _FakeTaskSelf()
    monkeypatch.setattr(
        async_cascade_delete_video,
        "retry",
        lambda *, exc: (_ for _ in ()).throw(RuntimeError("retry-called")),
    )

    with pytest.raises(RuntimeError, match="retry-called"):
        async_cascade_delete_video.run("video-xyz")

    assert fail_service.deletion_failed_called is True
