from __future__ import annotations

from backend.tasks.video_summary_tasks import async_mark_video_resource_ready


def test_async_mark_video_resource_ready_uses_service_entry(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_mark(video_id: str) -> bool:
        captured["video_id"] = video_id
        return True

    monkeypatch.setattr(
        "backend.tasks.video_summary_tasks._mark_video_resource_ready",
        _fake_mark,
    )

    result = async_mark_video_resource_ready([], "vid-001")

    assert captured.get("video_id") == "vid-001"
    assert result["status"] == "READY"


def test_async_mark_video_resource_ready_returns_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.tasks.video_summary_tasks._mark_video_resource_ready",
        lambda _video_id: False,
    )

    result = async_mark_video_resource_ready([], "missing")

    assert result["status"] == "NOT_FOUND"
