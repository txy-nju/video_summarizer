from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app_factory import create_app


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
