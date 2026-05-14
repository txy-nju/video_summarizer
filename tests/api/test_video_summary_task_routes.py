from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app_factory import create_app


app = create_app()
client = TestClient(app)


KB_PAYLOAD = {
    "name": "任务知识库",
    "category": "research",
    "description": "任务测试用",
    "config": {
        "retrieval": {"top_k": 5, "rerank": True},
        "tool_preferences": {"allow_web_search": False},
        "llm_policy": {"temperature": 0.2},
    },
}

VIDEO_PAYLOAD = {
    "file_name": "task-video.mp4",
    "oss_key": "videos/usr_001/vid_001/original.mp4",
    "duration": 120,
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


def _prepare_assets(token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert video_response.status_code == 201
    video_id = video_response.json()["data"]["video_id"]
    return kbid, video_id


def test_video_summary_task_crud_flow() -> None:
    token = _login("alice-task")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "请生成结构化摘要",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    list_response = client.get("/api/v1/tasks?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1

    update_response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"workflow_state": "WAITING_USER_APPROVAL", "user_guidance": "突出风险分析", "title": "第一版"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["workflow_state"] == "WAITING_USER_APPROVAL"
    assert update_response.json()["data"]["title"] == "第一版"

    delete_response = client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
    assert delete_response.status_code == 200

    get_after_delete = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert get_after_delete.status_code == 404


def test_video_summary_task_owner_isolation() -> None:
    alice_token = _login("alice-task-isolation")
    bob_token = _login("bob-task-isolation")

    kbid, video_id = _prepare_assets(alice_token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    forbidden_lookup = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert forbidden_lookup.status_code == 404

    forbidden_delete = client.delete(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert forbidden_delete.status_code == 404


def test_video_summary_task_create_requires_owned_assets() -> None:
    alice_token = _login("alice-task-assets")
    bob_token = _login("bob-task-assets")

    kbid, video_id = _prepare_assets(alice_token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert create_response.status_code == 404
