from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app_factory import create_app
from backend.dependencies import SessionLocal, get_workflow_orchestration_service
from backend.models.database import VideoResource, VideoSummaryTask
from backend.models.enums import FrameExtractionStatus, TranscribeStatus, WorkflowState


app = create_app()
client = TestClient(app)


KB_PAYLOAD = {
    "name": "QA知识库",
    "category": "research",
    "description": "用于测试单视频问答",
    "config": {
        "retrieval": {"top_k": 5, "rerank": True},
        "tool_preferences": {"allow_web_search": False},
        "llm_policy": {"temperature": 0.2},
    },
}

VIDEO_PAYLOAD = {
    "file_name": "qa-video.mp4",
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


def _mark_video_ready(video_id: str) -> None:
    """测试辅助：直接标记视频为已就绪状态（模拟 Celery 转录 + 抽帧任务完成）。"""
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row:
            row.transcribe_status = TranscribeStatus.COMPLETED
            row.frame_extraction_status = FrameExtractionStatus.COMPLETED
            row.extract_completed_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


def _prepare_task(token: str) -> str:
    """准备一个任务用于测试问答"""
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert video_response.status_code == 201
    video_id = video_response.json()["data"]["video_id"]

    # 模拟 Celery 提取任务完成，标记视频就绪
    _mark_video_ready(video_id)

    create_response = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "测试问答",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    return create_response.json()["data"]["task_id"]


def _set_task_workflow_state(task_id: str, state: WorkflowState) -> None:
    db = SessionLocal()
    try:
        row = db.query(VideoSummaryTask).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row:
            row.workflow_state = state
            db.commit()
    finally:
        db.close()


@contextmanager
def _override_workflow_service(stub_service: object):
    app.dependency_overrides[get_workflow_orchestration_service] = lambda: stub_service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_workflow_orchestration_service, None)



def test_video_qa_crud_flow() -> None:
    token = _login("alice-qa")
    headers = {"Authorization": f"Bearer {token}"}
    task_id = _prepare_task(token)

    # 创建问答
    create_response = client.post(
        f"/api/v1/tasks/{task_id}/qa",
        json={
            "task_id": task_id,
            "start_time": "00:10:00",
            "end_time": "00:12:00",
            "question_content": "这段视频讲的是什么?",
            "attachments": [],
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    qa_id = create_response.json()["data"]["qa_id"]

    # 查询问答列表
    list_response = client.get(f"/api/v1/tasks/{task_id}/qa?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1

    # 获取单条问答
    get_response = client.get(f"/api/v1/tasks/{task_id}/qa/{qa_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["data"]["question_content"] == "这段视频讲的是什么?"
    assert get_response.json()["data"]["answer_content"] is None

    # 删除问答
    delete_response = client.delete(f"/api/v1/tasks/{task_id}/qa/{qa_id}", headers=headers)
    assert delete_response.status_code == 200

    get_after_delete = client.get(f"/api/v1/tasks/{task_id}/qa/{qa_id}", headers=headers)
    assert get_after_delete.status_code == 404


def test_video_qa_with_attachments() -> None:
    token = _login("bob-qa")
    headers = {"Authorization": f"Bearer {token}"}
    task_id = _prepare_task(token)

    # 创建带附件的问答
    create_response = client.post(
        f"/api/v1/tasks/{task_id}/qa",
        json={
            "task_id": task_id,
            "start_time": "00:05:00",
            "end_time": "00:08:00",
            "question_content": "这个图表的含义?",
            "attachments": [
                {
                    "name": "screenshot.png",
                    "oss_key": "attachments/usr_001/qa_001/screenshot.png",
                    "mime_type": "image/png",
                    "size_bytes": 102400,
                }
            ],
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    assert len(create_response.json()["data"]["attachments"]) == 1


def test_video_qa_owner_isolation() -> None:
    alice_token = _login("alice-qa-isolation")
    bob_token = _login("bob-qa-isolation")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    alice_task_id = _prepare_task(alice_token)

    # Alice 创建问答
    create_response = client.post(
        f"/api/v1/tasks/{alice_task_id}/qa",
        json={
            "task_id": alice_task_id,
            "start_time": "00:00:00",
            "end_time": "00:01:00",
            "question_content": "Alice的问题",
            "attachments": [],
        },
        headers=alice_headers,
    )
    assert create_response.status_code == 201

    # Bob 不能访问 Alice 的任务的问答
    list_response = client.get(f"/api/v1/tasks/{alice_task_id}/qa", headers=bob_headers)
    assert list_response.status_code == 404 or list_response.json()["pagination"]["total"] == 0


def test_time_travel_qa_stream_returns_sse_events() -> None:
    token = _login("alice-qa-time-travel-stream")
    headers = {"Authorization": f"Bearer {token}"}
    task_id = _prepare_task(token)
    _set_task_workflow_state(task_id, WorkflowState.WAITING_USER_APPROVAL)

    class _StubWorkflowService:
        async def start_time_travel_qa_async(self, **kwargs):
            return "这是SSE时间旅行回答"

    with _override_workflow_service(_StubWorkflowService()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/time-travel-qa/stream",
            json={
                "timestamp": "00:10:00",
                "question_content": "这里在讲什么?",
                "attachments": [
                    {
                        "name": "frame-note.png",
                        "oss_key": "attachments/usr_001/qa_stream/frame-note.png",
                        "mime_type": "image/png",
                        "size_bytes": 2048,
                    }
                ],
                "window_seconds": 20,
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: start" in body
    assert "event: delta" in body
    assert "event: done" in body
    assert "这是SSE时间旅行回答" in body

    list_response = client.get(f"/api/v1/tasks/{task_id}/qa?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1
    attachments = list_response.json()["data"][0]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["name"] == "frame-note.png"


def test_time_travel_qa_stream_propagates_trace_id_from_traceparent() -> None:
    token = _login("alice-qa-trace-stream")
    headers = {
        "Authorization": f"Bearer {token}",
        "traceparent": "00-cccccccccccccccccccccccccccccccc-dddddddddddddddd-01",
    }
    task_id = _prepare_task(token)
    _set_task_workflow_state(task_id, WorkflowState.WAITING_USER_APPROVAL)

    captured: dict[str, str] = {}

    class _StubWorkflowService:
        async def start_time_travel_qa_async(self, **kwargs):
            captured["trace_id"] = str(kwargs.get("trace_id", ""))
            return "trace propagation ok"

    with _override_workflow_service(_StubWorkflowService()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/time-travel-qa/stream",
            json={
                "timestamp": "00:10:00",
                "question_content": "trace?",
                "attachments": [],
                "window_seconds": 20,
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert captured["trace_id"] == "cccccccccccccccccccccccccccccccc"


def test_time_travel_qa_stream_without_window_uses_rag_stream() -> None:
    token = _login("alice-qa-rag-stream")
    headers = {"Authorization": f"Bearer {token}"}
    task_id = _prepare_task(token)
    _set_task_workflow_state(task_id, WorkflowState.WAITING_USER_APPROVAL)

    response = client.post(
        f"/api/v1/tasks/{task_id}/time-travel-qa/stream",
        json={
            "timestamp": "00:20:00",
            "question_content": "无区间也要流式",
            "attachments": [],
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: start" in body
    assert "event: delta" in body
    assert "event: done" in body
    assert "[RAG] 已基于任务" in body

    list_response = client.get(f"/api/v1/tasks/{task_id}/qa?page=1&page_size=20", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1
    qa_record = list_response.json()["data"][0]
    assert qa_record["answer_content"].startswith("[RAG] 已基于任务")
    assert qa_record["start_time"] == "00:20:00"
    assert qa_record["end_time"] == "00:20:00"