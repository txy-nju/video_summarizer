from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app_factory import create_app


app = create_app()
client = TestClient(app)


KB_PAYLOAD = {
    "name": "LLM 研究库",
    "category": "research",
    "description": "收录大模型公开视频",
    "config": {
        "retrieval": {"top_k": 5, "rerank": True},
        "tool_preferences": {"allow_web_search": False},
        "llm_policy": {"temperature": 0.2},
    },
}

VIDEO_PAYLOAD = {
    "file_name": "kb-related-video.mp4",
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


def test_create_and_list_knowledge_bases() -> None:
    token = _login("alice-kb")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert create_response.status_code == 201

    body = create_response.json()
    assert body["status"] == "success"
    assert body["data"]["name"] == KB_PAYLOAD["name"]
    assert body["data"]["config"]["retrieval"]["top_k"] == 5
    assert body["meta"]["request_id"]

    list_response = client.get("/api/v1/kbs?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200

    list_body = list_response.json()
    assert list_body["status"] == "success"
    assert list_body["pagination"]["page"] == 1
    assert list_body["pagination"]["page_size"] == 20
    assert list_body["pagination"]["total"] == 1
    assert list_body["data"][0]["name"] == KB_PAYLOAD["name"]


def test_knowledge_base_isolation_between_users() -> None:
    alice_token = _login("alice-isolation")
    bob_token = _login("bob-isolation")

    create_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers={"Authorization": f"Bearer {alice_token}"})
    assert create_response.status_code == 201
    alice_kbid = create_response.json()["data"]["kbid"]

    bob_list_response = client.get("/api/v1/kbs", headers={"Authorization": f"Bearer {bob_token}"})
    assert bob_list_response.status_code == 200
    assert bob_list_response.json()["data"] == []

    forbidden_lookup = client.get(f"/api/v1/kbs/{alice_kbid}", headers={"Authorization": f"Bearer {bob_token}"})
    assert forbidden_lookup.status_code == 404


def test_update_knowledge_base_success() -> None:
    token = _login("alice-update")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert create_response.status_code == 201
    kbid = create_response.json()["data"]["kbid"]

    update_payload = {
        "name": "LLM 工程库",
        "category": "engineering",
        "description": "更新后的说明",
        "config": {
            "retrieval": {"top_k": 8, "rerank": True},
            "tool_preferences": {"allow_web_search": True},
            "llm_policy": {"temperature": 0.5},
        },
    }
    update_response = client.patch(f"/api/v1/kbs/{kbid}", json=update_payload, headers=headers)
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["data"]["name"] == "LLM 工程库"
    assert body["data"]["category"] == "engineering"
    assert body["data"]["config"]["retrieval"]["top_k"] == 8


def test_update_knowledge_base_not_found_for_other_user() -> None:
    alice_token = _login("alice-update-isolation")
    bob_token = _login("bob-update-isolation")

    create_response = client.post(
        "/api/v1/kbs",
        json=KB_PAYLOAD,
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert create_response.status_code == 201
    alice_kbid = create_response.json()["data"]["kbid"]

    update_response = client.patch(
        f"/api/v1/kbs/{alice_kbid}",
        json={"name": "illegal-update"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert update_response.status_code == 404


def test_delete_knowledge_base_success_and_not_found_after_delete() -> None:
    token = _login("alice-delete")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert create_response.status_code == 201
    kbid = create_response.json()["data"]["kbid"]

    delete_response = client.delete(f"/api/v1/kbs/{kbid}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["kbid"] == kbid

    lookup_after_delete = client.get(f"/api/v1/kbs/{kbid}", headers=headers)
    assert lookup_after_delete.status_code == 404


def test_delete_knowledge_base_not_found_for_other_user() -> None:
    alice_token = _login("alice-delete-isolation")
    bob_token = _login("bob-delete-isolation")

    create_response = client.post(
        "/api/v1/kbs",
        json=KB_PAYLOAD,
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert create_response.status_code == 201
    alice_kbid = create_response.json()["data"]["kbid"]

    delete_response = client.delete(
        f"/api/v1/kbs/{alice_kbid}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert delete_response.status_code == 404


def test_kb_video_relation_intent_flow() -> None:
    token = _login("alice-kb-video")
    headers = {"Authorization": f"Bearer {token}"}

    create_kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert create_kb_response.status_code == 201
    kbid = create_kb_response.json()["data"]["kbid"]

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert create_video_response.status_code == 201
    video_id = create_video_response.json()["data"]["video_id"]

    bind_response = client.post(f"/api/v1/kbs/{kbid}/videos", json={"video_id": video_id}, headers=headers)
    assert bind_response.status_code == 200
    assert bind_response.json()["data"] == {"kbid": kbid, "video_id": video_id}

    list_response = client.get(f"/api/v1/kbs/{kbid}/videos?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1
    assert list_response.json()["data"][0]["video_id"] == video_id
    assert "relation_id" not in list_response.json()["data"][0]
    assert "added_at" not in list_response.json()["data"][0]

    remove_response = client.delete(f"/api/v1/kbs/{kbid}/videos/{video_id}", headers=headers)
    assert remove_response.status_code == 200

    list_after_remove = client.get(f"/api/v1/kbs/{kbid}/videos?page=1&page_size=20", headers=headers)
    assert list_after_remove.status_code == 200
    assert list_after_remove.json()["pagination"]["total"] == 0


def test_kb_video_relation_owner_isolation() -> None:
    alice_token = _login("alice-kb-video-isolation")
    bob_token = _login("bob-kb-video-isolation")

    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    create_kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=alice_headers)
    assert create_kb_response.status_code == 201
    alice_kbid = create_kb_response.json()["data"]["kbid"]

    create_video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=alice_headers)
    assert create_video_response.status_code == 201
    alice_video_id = create_video_response.json()["data"]["video_id"]

    bind_forbidden = client.post(
        f"/api/v1/kbs/{alice_kbid}/videos",
        json={"video_id": alice_video_id},
        headers=bob_headers,
    )
    assert bind_forbidden.status_code == 404

    list_forbidden = client.get(f"/api/v1/kbs/{alice_kbid}/videos", headers=bob_headers)
    assert list_forbidden.status_code == 404

    remove_forbidden = client.delete(f"/api/v1/kbs/{alice_kbid}/videos/{alice_video_id}", headers=bob_headers)
    assert remove_forbidden.status_code == 404
