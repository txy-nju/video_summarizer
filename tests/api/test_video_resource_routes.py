from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

import backend.dependencies as dependencies
from backend.app_factory import create_app
from backend.models.database import VideoResource
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.services.video_resource_service import VideoResourceService
from backend.models.database import kb_video_relation_table


app = create_app()
client = TestClient(app)


VIDEO_PAYLOAD = {
    "file_name": "intro.mp4",
}


def _login(username: str, password: str = "Secret123!") -> str:
    register_response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert register_response.status_code in (200, 201)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_id": f"device-{username}"},
    )
    assert login_response.status_code == 200
    return login_response.json()["data"]["access_token"]


def test_video_resource_crud_flow() -> None:
    token = _login("alice-video")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_response.status_code == 201
    video_id = create_response.json()["data"]["video_id"]

    list_response = client.get("/api/v1/videos?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1

    update_response = client.patch(
        f"/api/v1/videos/{video_id}",
        json={"file_name": "intro-v2.mp4"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["file_name"] == "intro-v2.mp4"
    assert update_response.json()["data"]["duration"] == 0

    delete_response = client.delete(f"/api/v1/videos/{video_id}", headers=headers)
    assert delete_response.status_code == 202

    list_after_delete = client.get("/api/v1/videos?page=1&page_size=20", headers=headers)
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["pagination"]["total"] == 0

    get_after_delete = client.get(f"/api/v1/videos/{video_id}", headers=headers)
    assert get_after_delete.status_code == 404

    second_delete = client.delete(f"/api/v1/videos/{video_id}", headers=headers)
    assert second_delete.status_code == 404


def test_video_resource_owner_isolation() -> None:
    alice_token = _login("alice-video-isolation")
    bob_token = _login("bob-video-isolation")

    create_response = client.post(
        "/api/v1/videos",
        json=VIDEO_PAYLOAD,
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert create_response.status_code == 201
    video_id = create_response.json()["data"]["video_id"]

    forbidden_lookup = client.get(
        f"/api/v1/videos/{video_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert forbidden_lookup.status_code == 404

    forbidden_delete = client.delete(
        f"/api/v1/videos/{video_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert forbidden_delete.status_code == 404


def test_video_resource_rejects_non_user_writable_fields() -> None:
    token = _login("alice-video-fields")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/api/v1/videos",
        json={"file_name": "intro.mp4", "oss_key": "videos/usr_001/vid_001/original.mp4"},
        headers=headers,
    )
    assert create_response.status_code == 422

    valid_create_response = client.post(
        "/api/v1/videos",
        json={"file_name": "intro.mp4"},
        headers=headers,
    )
    assert valid_create_response.status_code == 201
    video_id = valid_create_response.json()["data"]["video_id"]

    update_response = client.patch(
        f"/api/v1/videos/{video_id}",
        json={"duration": 180},
        headers=headers,
    )
    assert update_response.status_code == 422


def test_video_delete_strips_kb_video_relations() -> None:
    token = _login("alice-video-cascade")
    headers = {"Authorization": f"Bearer {token}"}

    create_kb_response = client.post(
        "/api/v1/kbs",
        json={
            "name": "删除关系验证库",
            "category": "test",
            "description": "验证软删除时关系剥离",
            "config": {
                "retrieval": {"top_k": 5, "rerank": True},
                "tool_preferences": {"allow_web_search": False},
                "llm_policy": {"temperature": 0.2},
            },
        },
        headers=headers,
    )
    assert create_kb_response.status_code == 201
    kbid = create_kb_response.json()["data"]["kbid"]

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_video_response.status_code == 201
    video_id = create_video_response.json()["data"]["video_id"]

    bind_response = client.post(f"/api/v1/kbs/{kbid}/videos", json={"video_id": video_id}, headers=headers)
    assert bind_response.status_code == 200

    with dependencies.SessionLocal() as db_session:
        relation_rows_before = db_session.execute(
            select(kb_video_relation_table.c.video_id).where(
                kb_video_relation_table.c.kbid == kbid,
                kb_video_relation_table.c.video_id == video_id,
            )
        ).all()
        assert len(relation_rows_before) == 1

    delete_response = client.delete(f"/api/v1/videos/{video_id}", headers=headers)
    assert delete_response.status_code == 202

    with dependencies.SessionLocal() as db_session:
        relation_rows_after = db_session.execute(
            select(kb_video_relation_table.c.video_id).where(
                kb_video_relation_table.c.kbid == kbid,
                kb_video_relation_table.c.video_id == video_id,
            )
        ).all()
        assert relation_rows_after == []

    list_response = client.get(f"/api/v1/kbs/{kbid}/videos?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 0


def test_video_delete_dispatches_async_cleanup(monkeypatch) -> None:
    token = _login("alice-video-cleanup-dispatch")
    headers = {"Authorization": f"Bearer {token}"}

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_video_response.status_code == 201
    video_id = create_video_response.json()["data"]["video_id"]

    captured: dict[str, str] = {}

    def _fake_delay(v_id: str):
        captured["video_id"] = v_id

    monkeypatch.setattr(
        "backend.services.video_resource_service._dispatch_async_cascade_delete",
        _fake_delay,
    )

    delete_response = client.delete(f"/api/v1/videos/{video_id}", headers=headers)
    assert delete_response.status_code == 202
    assert captured.get("video_id") == video_id


def test_trigger_processing_after_upload_dispatches_async_process(monkeypatch) -> None:
    token = _login("alice-video-process-dispatch")
    headers = {"Authorization": f"Bearer {token}"}

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_video_response.status_code == 201
    video_id = create_video_response.json()["data"]["video_id"]

    with dependencies.SessionLocal() as db_session:
        row = db_session.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None
        row.oss_key = f"videos/owner/{video_id}/original.mp4"
        db_session.commit()

    captured: dict[str, str] = {}

    def _fake_dispatch(v_id: str, trace_id: str = "") -> None:
        captured["video_id"] = v_id

    monkeypatch.setattr(
        "backend.services.video_resource_service._dispatch_async_process_video",
        _fake_dispatch,
    )

    with dependencies.SessionLocal() as db_session:
        service = VideoResourceService(repository=VideoResourceRepository(db_session=db_session))
        dispatched = service.trigger_processing_after_upload(video_id=video_id)

    assert dispatched is True
    assert captured.get("video_id") == video_id


def test_video_resource_returns_presigned_url_when_oss_key_exists() -> None:
    token = _login("alice-video-presigned")
    headers = {"Authorization": f"Bearer {token}"}

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_video_response.status_code == 201
    video_id = create_video_response.json()["data"]["video_id"]

    with dependencies.SessionLocal() as db_session:
        row = db_session.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None
        row.oss_key = f"videos/{row.owner_id}/{video_id}/original.mp4"
        db_session.commit()

    get_response = client.get(f"/api/v1/videos/{video_id}", headers=headers)
    assert get_response.status_code == 200
    data = get_response.json()["data"]
    assert data["oss_key"].startswith("videos/")
    assert data["presigned_url"].startswith("file://")
