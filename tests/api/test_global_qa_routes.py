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
    assert create_qa_response.json()["data"]["answer_content"] is not None
    assert isinstance(create_qa_response.json()["data"]["cited_sources"], list)

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


def test_global_qa_kb_isolation_same_user() -> None:
    """同一用户下的两个 KB 问答应完全隔离，互不可见。"""
    token = _login("alice-kb-isolation")
    headers = {"Authorization": f"Bearer {token}"}

    # 创建两个 KB
    kb1_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb1_response.status_code == 201
    kbid1 = kb1_response.json()["data"]["kbid"]

    kb2_response = client.post("/api/v1/kbs", json={
        **KB_PAYLOAD, "name": "KB2 知识库",
    }, headers=headers)
    assert kb2_response.status_code == 201
    kbid2 = kb2_response.json()["data"]["kbid"]

    # 在各自 KB 下创建 chat session
    chat1_response = client.post(
        f"/api/v1/kbs/{kbid1}/chats", json={"kbid": kbid1}, headers=headers,
    )
    assert chat1_response.status_code == 201
    chat_id1 = chat1_response.json()["data"]["chat_id"]

    chat2_response = client.post(
        f"/api/v1/kbs/{kbid2}/chats", json={"kbid": kbid2}, headers=headers,
    )
    assert chat2_response.status_code == 201
    chat_id2 = chat2_response.json()["data"]["chat_id"]

    # 在 KB1 下创建 QA
    qa1_response = client.post(
        f"/api/v1/kbs/{kbid1}/chats/{chat_id1}/qa",
        json={"question_content": "KB1 的问题", "attachments": []},
        headers=headers,
    )
    assert qa1_response.status_code == 201
    qa1_id = qa1_response.json()["data"]["qa_id"]

    # 在 KB2 下创建 QA
    qa2_response = client.post(
        f"/api/v1/kbs/{kbid2}/chats/{chat_id2}/qa",
        json={"question_content": "KB2 的问题", "attachments": []},
        headers=headers,
    )
    assert qa2_response.status_code == 201
    qa2_id = qa2_response.json()["data"]["qa_id"]

    # KB1 的 QA 列表不包含 KB2 的 QA
    list1 = client.get(
        f"/api/v1/kbs/{kbid1}/chats/{chat_id1}/qa?page=1&page_size=20",
        headers=headers,
    )
    assert list1.status_code == 200
    qa_ids_1 = {r["qa_id"] for r in list1.json()["data"]}
    assert qa1_id in qa_ids_1
    assert qa2_id not in qa_ids_1

    # KB2 的 QA 列表不包含 KB1 的 QA
    list2 = client.get(
        f"/api/v1/kbs/{kbid2}/chats/{chat_id2}/qa?page=1&page_size=20",
        headers=headers,
    )
    assert list2.status_code == 200
    qa_ids_2 = {r["qa_id"] for r in list2.json()["data"]}
    assert qa2_id in qa_ids_2
    assert qa1_id not in qa_ids_2

    # 交叉访问应返回 404（用 KB1 的 path 访问 KB2 的 QA）
    cross_get = client.get(
        f"/api/v1/kbs/{kbid1}/chats/{chat_id1}/qa/{qa2_id}",
        headers=headers,
    )
    assert cross_get.status_code == 404

    cross_get2 = client.get(
        f"/api/v1/kbs/{kbid2}/chats/{chat_id2}/qa/{qa1_id}",
        headers=headers,
    )
    assert cross_get2.status_code == 404


def test_global_qa_kb_cascade_delete_isolation() -> None:
    """删除一个 KB 的 chat session 不应影响其他 KB 的 QA 数据。"""
    token = _login("alice-kb-cascade")
    headers = {"Authorization": f"Bearer {token}"}

    # KB1: 创建 chat + QA
    kb1_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb1_response.status_code == 201
    kbid1 = kb1_response.json()["data"]["kbid"]
    chat1 = client.post(f"/api/v1/kbs/{kbid1}/chats", json={"kbid": kbid1}, headers=headers)
    assert chat1.status_code == 201
    chat_id1 = chat1.json()["data"]["chat_id"]
    qa1 = client.post(
        f"/api/v1/kbs/{kbid1}/chats/{chat_id1}/qa",
        json={"question_content": "KB1 问题", "attachments": []}, headers=headers,
    )
    assert qa1.status_code == 201
    qa1_id = qa1.json()["data"]["qa_id"]

    # KB2: 创建 chat + QA
    kb2_response = client.post("/api/v1/kbs", json={
        **KB_PAYLOAD, "name": "KB2",
    }, headers=headers)
    assert kb2_response.status_code == 201
    kbid2 = kb2_response.json()["data"]["kbid"]
    chat2 = client.post(f"/api/v1/kbs/{kbid2}/chats", json={"kbid": kbid2}, headers=headers)
    assert chat2.status_code == 201
    chat_id2 = chat2.json()["data"]["chat_id"]
    qa2 = client.post(
        f"/api/v1/kbs/{kbid2}/chats/{chat_id2}/qa",
        json={"question_content": "KB2 问题", "attachments": []}, headers=headers,
    )
    assert qa2.status_code == 201
    qa2_id = qa2.json()["data"]["qa_id"]

    # 删除 KB1 的 chat session
    delete_response = client.delete(f"/api/v1/kbs/{kbid1}/chats/{chat_id1}", headers=headers)
    assert delete_response.status_code == 200

    # KB1 的 QA 应被级联删除
    get_deleted = client.get(
        f"/api/v1/kbs/{kbid1}/chats/{chat_id1}/qa/{qa1_id}", headers=headers,
    )
    assert get_deleted.status_code == 404

    # KB2 的 QA 应完好无损
    get_kb2 = client.get(
        f"/api/v1/kbs/{kbid2}/chats/{chat_id2}/qa/{qa2_id}", headers=headers,
    )
    assert get_kb2.status_code == 200
    assert get_kb2.json()["data"]["qa_id"] == qa2_id


def test_global_qa_user_isolation() -> None:
    """用户 A 的 KB QA 对用户 B 不可见。"""
    alice_token = _login("alice-qa-user-isolation")
    bob_token = _login("bob-qa-user-isolation")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    # Alice 创建 KB + chat + QA
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=alice_headers)
    assert kb_response.status_code == 201
    alice_kbid = kb_response.json()["data"]["kbid"]
    chat_response = client.post(
        f"/api/v1/kbs/{alice_kbid}/chats", json={"kbid": alice_kbid}, headers=alice_headers,
    )
    assert chat_response.status_code == 201
    chat_id = chat_response.json()["data"]["chat_id"]
    qa_response = client.post(
        f"/api/v1/kbs/{alice_kbid}/chats/{chat_id}/qa",
        json={"question_content": "Alice 的问题", "attachments": []}, headers=alice_headers,
    )
    assert qa_response.status_code == 201
    qa_id = qa_response.json()["data"]["qa_id"]

    # Bob 无法访问 Alice 的 KB 的 QA
    bob_qa_list = client.get(
        f"/api/v1/kbs/{alice_kbid}/chats/{chat_id}/qa", headers=bob_headers,
    )
    # Bob 看到的是空列表（因为 KB 不属于他，list 找不到归属）
    assert bob_qa_list.status_code == 200
    assert bob_qa_list.json()["pagination"]["total"] == 0

    bob_qa_get = client.get(
        f"/api/v1/kbs/{alice_kbid}/chats/{chat_id}/qa/{qa_id}", headers=bob_headers,
    )
    assert bob_qa_get.status_code == 404


def test_global_qa_different_configs_are_independent() -> None:
    """不同 KB 可以有不同的检索配置，互不影响。"""
    token = _login("alice-kb-configs")
    headers = {"Authorization": f"Bearer {token}"}

    kb1_response = client.post("/api/v1/kbs", json={
        **KB_PAYLOAD,
        "config": {
            "retrieval": {"top_k": 3, "rerank": False},
            "tool_preferences": {"allow_web_search": False},
            "llm_policy": {"temperature": 0.1},
        },
    }, headers=headers)
    assert kb1_response.status_code == 201
    kbid1 = kb1_response.json()["data"]["kbid"]

    kb2_response = client.post("/api/v1/kbs", json={
        **KB_PAYLOAD,
        "name": "KB 不同配置",
        "config": {
            "retrieval": {"top_k": 10, "rerank": True},
            "tool_preferences": {"allow_web_search": True},
            "llm_policy": {"temperature": 0.9},
        },
    }, headers=headers)
    assert kb2_response.status_code == 201
    kbid2 = kb2_response.json()["data"]["kbid"]

    # 验证配置各自保存正确
    get1 = client.get(f"/api/v1/kbs/{kbid1}", headers=headers)
    assert get1.json()["data"]["config"]["retrieval"]["top_k"] == 3
    assert get1.json()["data"]["config"]["retrieval"]["rerank"] is False

    get2 = client.get(f"/api/v1/kbs/{kbid2}", headers=headers)
    assert get2.json()["data"]["config"]["retrieval"]["top_k"] == 10
    assert get2.json()["data"]["config"]["retrieval"]["rerank"] is True
