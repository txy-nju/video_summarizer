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


def _login(username: str, password: str = "Secret123!") -> str:
    register_response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert register_response.status_code in (200, 201)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_id": f"device-{username}"},
    )
    assert login_response.status_code == 200
    return login_response.json()["data"]["access_token"]


def _prepare_chat(token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    chat_response = client.post(
        f"/api/v1/kbs/{kbid}/chats",
        json={"kbid": kbid},
        headers=headers,
    )
    assert chat_response.status_code == 201
    chat_id = chat_response.json()["data"]["chat_id"]
    return kbid, chat_id


def test_global_qa_record_crud_flow() -> None:
    token = _login("alice-qa-global")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, chat_id = _prepare_chat(token)

    create_qa_response = client.post(
        f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa",
        json={
            "question_content": "这个知识库里有什么内容?",
            "attachments": [],
        },
        headers=headers,
    )
    assert create_qa_response.status_code == 201
    qa_id = create_qa_response.json()["data"]["qa_id"]
    assert create_qa_response.json()["data"]["question_content"] == "这个知识库里有什么内容?"
    assert create_qa_response.json()["data"]["answer_content"] is None

    list_qa_response = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa?page=1&page_size=20", headers=headers)
    assert list_qa_response.status_code == 200
    assert list_qa_response.json()["pagination"]["total"] == 1

    get_qa_response = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}", headers=headers)
    assert get_qa_response.status_code == 200
    assert get_qa_response.json()["data"]["qa_id"] == qa_id

    delete_qa_response = client.delete(f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}", headers=headers)
    assert delete_qa_response.status_code == 200

    get_after_delete = client.get(f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}", headers=headers)
    assert get_after_delete.status_code == 404


def test_global_qa_with_attachments() -> None:
    token = _login("bob-qa-global")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, chat_id = _prepare_chat(token)

    create_qa_response = client.post(
        f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa",
        json={
            "question_content": "请解释这个图表",
            "attachments": [
                {
                    "name": "chart.png",
                    "oss_key": "attachments/usr_001/qa_001/chart.png",
                    "mime_type": "image/png",
                    "size_bytes": 204800,
                }
            ],
        },
        headers=headers,
    )
    assert create_qa_response.status_code == 201
    assert len(create_qa_response.json()["data"]["attachments"]) == 1
