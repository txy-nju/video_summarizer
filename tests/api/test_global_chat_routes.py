from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app_factory import create_app


app = create_app()
client = TestClient(app)


KB_PAYLOAD = {
    "name": "全局问答知识库",
    "category": "research",
    "description": "用于测试全局会话和问答",
    "config": {
        "retrieval": {"top_k": 5, "rerank": True},
        "tool_preferences": {"allow_web_search": False},
        "llm_policy": {"temperature": 0.2},
    },
}

VIDEO_PAYLOAD = {
    "file_name": "global-chat-video.mp4",
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


def _prepare_kb(token: str) -> str:
    """准备一个知识库用于测试会话"""
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    return kb_response.json()["data"]["kbid"]


def test_global_chat_session_crud_flow() -> None:
    token = _login("alice-chat")
    headers = {"Authorization": f"Bearer {token}"}
    kbid = _prepare_kb(token)

    # 创建会话
    create_response = client.post(
        f"/api/v1/kbs/{kbid}/chats",
        json={"kbid": kbid, "chat_title": "我的第一个对话"},
        headers=headers,
    )
    assert create_response.status_code == 201
    chat_id = create_response.json()["data"]["chat_id"]
    assert create_response.json()["data"]["chat_title"] == "我的第一个对话"

    # 查询会话列表
    list_response = client.get(f"/api/v1/kbs/{kbid}/chats?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1

    # 获取单个会话
    get_response = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["data"]["chat_title"] == "我的第一个对话"

    # 更新会话标题
    update_response = client.patch(
        f"/api/v1/kbs/{kbid}/chats/{chat_id}",
        json={"chat_title": "更新后的对话标题"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["chat_title"] == "更新后的对话标题"

    # 删除会话
    delete_response = client.delete(f"/api/v1/kbs/{kbid}/chats/{chat_id}", headers=headers)
    assert delete_response.status_code == 200

    get_after_delete = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}", headers=headers)
    assert get_after_delete.status_code == 404


def test_global_chat_owner_isolation() -> None:
    alice_token = _login("alice-chat-isolation")
    bob_token = _login("bob-chat-isolation")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    alice_kbid = _prepare_kb(alice_token)

    # Alice 创建会话
    chat_response = client.post(
        f"/api/v1/kbs/{alice_kbid}/chats",
        json={"kbid": alice_kbid},
        headers=alice_headers,
    )
    assert chat_response.status_code == 201

    # Bob 不能访问 Alice 的知识库的会话
    list_response = client.get(f"/api/v1/kbs/{alice_kbid}/chats", headers=bob_headers)
    # 由于 Alice 的知识库不属于 Bob，列表应该为空
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 0


def test_global_chat_cascade_delete() -> None:
    token = _login("cascade-delete-test")
    headers = {"Authorization": f"Bearer {token}"}
    kbid = _prepare_kb(token)

    # 创建会话
    chat_response = client.post(
        f"/api/v1/kbs/{kbid}/chats",
        json={"kbid": kbid},
        headers=headers,
    )
    assert chat_response.status_code == 201
    chat_id = chat_response.json()["data"]["chat_id"]

    # 创建多个问答
    qa_ids = []
    for i in range(3):
        qa_response = client.post(
            f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa",
            json={"question_content": f"问题 {i+1}", "attachments": []},
            headers=headers,
        )
        assert qa_response.status_code == 201
        qa_ids.append(qa_response.json()["data"]["qa_id"])

    # 删除会话（应该级联删除问答）
    delete_response = client.delete(f"/api/v1/kbs/{kbid}/chats/{chat_id}", headers=headers)
    assert delete_response.status_code == 200

    # 验证问答已被删除
    for qa_id in qa_ids:
        get_response = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}", headers=headers)
        assert get_response.status_code == 404
