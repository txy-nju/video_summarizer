from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app_factory import create_app
from backend.dependencies import SessionLocal, get_workflow_orchestration_service
from backend.models.database import VideoResource, VideoSummaryTask
from backend.models.enums import FrameExtractionStatus, TranscribeStatus, WorkflowState


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


def _mark_video_inconsistent_ready(video_id: str) -> None:
    """测试辅助：制造 extract_completed_at 非空但双状态不一致的异常就绪态。"""
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row:
            row.transcribe_status = TranscribeStatus.COMPLETED
            row.frame_extraction_status = FrameExtractionStatus.EXTRACTING
            row.extract_completed_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


def _prepare_assets(token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert video_response.status_code == 201
    video_id = video_response.json()["data"]["video_id"]
    # 模拟 Celery 提取任务完成，标记视频就绪
    _mark_video_ready(video_id)
    return kbid, video_id


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
        json={"draft_summary": "这是用户修订后的摘要初稿", "user_guidance": "突出风险分析", "title": "第一版"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["workflow_state"] == "DRAFT_GENERATING"
    assert update_response.json()["data"]["draft_summary"] == "这是用户修订后的摘要初稿"
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


def test_video_summary_task_update_rejects_workflow_state_write() -> None:
    token = _login("alice-task-state")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    update_response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"workflow_state": "WAITING_USER_APPROVAL"},
        headers=headers,
    )

    assert update_response.status_code == 422


def test_video_summary_task_create_rejects_inconsistent_ready_state() -> None:
    token = _login("alice-task-inconsistent-ready")
    headers = {"Authorization": f"Bearer {token}"}

    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert video_response.status_code == 201
    video_id = video_response.json()["data"]["video_id"]
    _mark_video_inconsistent_ready(video_id)

    create_response = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "请生成结构化摘要",
        },
        headers=headers,
    )
    assert create_response.status_code == 422


def test_start_analysis_workflow_dispatches_celery_task(monkeypatch) -> None:
    token = _login("alice-task-start-analysis")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "给我一个结构化总结"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    dispatched: dict[str, object] = {}

    def _fake_apply_async(*, args, queue):
        dispatched["args"] = args
        dispatched["queue"] = queue
        return SimpleNamespace(id="celery-analysis-001")

    monkeypatch.setattr(
        "backend.tasks.workflow_runtime_tasks.async_execute_analysis_workflow.apply_async",
        _fake_apply_async,
    )

    with _override_workflow_service(SimpleNamespace()):
        response = client.post(f"/api/v1/tasks/{task_id}/start-analysis", json={}, headers=headers)
    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["task_id"] == task_id
    assert payload["workflow_state"] == "DRAFT_GENERATING"
    assert payload["thread_id"] == task_id
    assert payload["celery_task_id"] == "celery-analysis-001"
    assert payload["accepted_at"].endswith("Z")

    args = dispatched["args"]
    assert args[1] == task_id
    assert args[2] == ""
    assert args[3] == []
    assert dispatched["queue"] == "default"


def test_approve_and_finalize_requires_waiting_state() -> None:
    token = _login("alice-task-approve-state")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    with _override_workflow_service(SimpleNamespace()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve-and-finalize",
            json={
                "edited_aggregated_chunk_insights": "编辑后的分析",
                "human_guidance": "更强调可执行建议",
            },
            headers=headers,
        )
    assert response.status_code == 422


def test_approve_and_finalize_dispatches_celery_task_when_waiting(monkeypatch) -> None:
    token = _login("alice-task-approve-finalize")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]
    _set_task_workflow_state(task_id, WorkflowState.WAITING_USER_APPROVAL)

    dispatched: dict[str, object] = {}

    def _fake_apply_async(*, args, queue):
        dispatched["args"] = args
        dispatched["queue"] = queue
        return SimpleNamespace(id="celery-final-001")

    monkeypatch.setattr(
        "backend.tasks.workflow_runtime_tasks.async_execute_finalization_workflow.apply_async",
        _fake_apply_async,
    )

    with _override_workflow_service(SimpleNamespace()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve-and-finalize",
            json={
                "edited_aggregated_chunk_insights": "编辑后的分析",
                "human_guidance": "更强调可执行建议",
            },
            headers=headers,
        )
    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["task_id"] == task_id
    assert payload["workflow_state"] == "FINAL_GENERATING"
    assert payload["thread_id"] == task_id
    assert payload["celery_task_id"] == "celery-final-001"
    assert payload["accepted_at"].endswith("Z")

    args = dispatched["args"]
    assert args[1] == task_id
    assert args[2] == "编辑后的分析"
    assert args[3] == "更强调可执行建议"
    assert dispatched["queue"] == "default"


def test_time_travel_qa_returns_answer_when_task_ready() -> None:
    token = _login("alice-task-time-travel")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]
    _set_task_workflow_state(task_id, WorkflowState.WAITING_USER_APPROVAL)

    class _StubWorkflowService:
        async def start_time_travel_qa_async(self, **kwargs):
            assert kwargs["task_id"] == task_id
            assert kwargs["timestamp"] == "00:10:00"
            assert kwargs["question"] == "这里在讲什么?"
            assert kwargs["window_seconds"] == 20
            return "这是基于证据窗口的回答"

    with _override_workflow_service(_StubWorkflowService()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/time-travel-qa",
            json={"timestamp": "00:10:00", "question": "这里在讲什么?", "window_seconds": 20},
            headers=headers,
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["answer"] == "这是基于证据窗口的回答"
    assert payload["timestamp"] == "00:10:00"
    assert payload["window_seconds"] == 20


def test_time_travel_qa_rejects_when_analysis_not_ready() -> None:
    token = _login("alice-task-time-travel-invalid")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "默认"},
        headers=headers,
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    with _override_workflow_service(SimpleNamespace()):
        response = client.post(
            f"/api/v1/tasks/{task_id}/time-travel-qa",
            json={"timestamp": "00:10:00", "question": "这里在讲什么?", "window_seconds": 20},
            headers=headers,
        )

    assert response.status_code == 422