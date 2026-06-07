from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app_factory import create_app
from backend.dependencies import SessionLocal, get_workflow_orchestration_service
from backend.models.database import VideoResource, VideoSummaryTask
from backend.models.database import kb_video_relation_table
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


def _mark_video_ready_with_object_key(video_id: str) -> None:
    """测试辅助：视频以对象键形式就绪（非本地绝对路径）。"""
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row:
            row.oss_key = f"videos/{row.owner_id}/{video_id}/original.mp4"
            row.transcribe_status = TranscribeStatus.COMPLETED
            row.frame_extraction_status = FrameExtractionStatus.COMPLETED
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


def _populate_task_analysis(task_id: str) -> None:
    """测试辅助：填充 Task 的分析结果字段，模拟已完成的分析任务。"""
    db = SessionLocal()
    try:
        row = db.query(VideoSummaryTask).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row:
            row.workflow_state = WorkflowState.COMPLETED
            row.draft_summary = "块聚合分析草稿"
            row.final_summary = "最终摘要：这是一份关于项目进展的报告..."
            row.title = "项目进展分析"
            row.summary_vector_ids = ["vec_001", "vec_002"]
            db.commit()
    finally:
        db.close()


def _create_kb(token: str, name: str) -> str:
    """测试辅助：仅创建 KB 并返回 kbid（不涉及视频）。"""
    headers = {"Authorization": f"Bearer {token}"}
    kb_response = client.post(
        "/api/v1/kbs",
        json={
            "name": name,
            "category": "research",
            "description": "辅助知识库",
            "config": {
                "retrieval": {"top_k": 5, "rerank": True},
                "tool_preferences": {"allow_web_search": False},
                "llm_policy": {"temperature": 0.2},
            },
        },
        headers=headers,
    )
    assert kb_response.status_code == 201
    return kb_response.json()["data"]["kbid"]


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


def test_video_summary_task_create_accepts_ready_video_with_object_key() -> None:
    token = _login("alice-task-object-key-ready")
    headers = {"Authorization": f"Bearer {token}"}

    kb_response = client.post("/api/v1/kbs", json=KB_PAYLOAD, headers=headers)
    assert kb_response.status_code == 201
    kbid = kb_response.json()["data"]["kbid"]

    video_response = client.post("/api/v1/videos", json=VIDEO_PAYLOAD, headers=headers)
    assert video_response.status_code == 201
    video_id = video_response.json()["data"]["video_id"]
    _mark_video_ready_with_object_key(video_id)

    create_response = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "请输出面向业务的结构化摘要",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    payload = create_response.json()["data"]
    assert payload["video_id"] == video_id
    assert payload["kbid"] == kbid


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


def test_start_analysis_workflow_uses_trace_id_from_traceparent(monkeypatch) -> None:
    token = _login("alice-task-trace-propagation")
    headers = {"Authorization": f"Bearer {token}", "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"}
    kbid, video_id = _prepare_assets(token)

    create_response = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "trace"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["data"]["task_id"]

    captured: dict[str, tuple] = {}

    def _fake_apply_async(*, args, queue):
        captured["args"] = args
        captured["queue"] = queue
        return SimpleNamespace(id="celery-analysis-trace")

    monkeypatch.setattr(
        "backend.tasks.workflow_runtime_tasks.async_execute_analysis_workflow.apply_async",
        _fake_apply_async,
    )

    with _override_workflow_service(SimpleNamespace()):
        response = client.post(f"/api/v1/tasks/{task_id}/start-analysis", json={}, headers=headers)

    assert response.status_code == 202
    args = captured["args"]
    assert args[5] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


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


# ============================================================================
# Duplicate detection & clone-to-KB tests
# ============================================================================


def test_create_task_duplicate_detection() -> None:
    """同 KB + 同视频创建第二个 Task 时返回 409；提供 replace 参数后替换成功。"""
    token = _login("alice-task-dup")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    # 第一次创建成功
    create1 = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "第一版"},
        headers=headers,
    )
    assert create1.status_code == 201
    task_id_1 = create1.json()["data"]["task_id"]

    # 同 (KB, video) 再次创建 → 409
    create2 = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id, "user_initial_preference": "第二版"},
        headers=headers,
    )
    assert create2.status_code == 409
    error = create2.json()["error"]
    assert error["code"] == "TASK_DUPLICATE_VIDEO_IN_KB"
    assert error["details"]["existing_task_id"] == task_id_1
    assert error["details"]["kbid"] == kbid

    # 带 replace_existing_task_id 创建 → 旧 Task 被替换
    create3 = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "替换版",
            "replace_existing_task_id": task_id_1,
        },
        headers=headers,
    )
    assert create3.status_code == 201
    task_id_2 = create3.json()["data"]["task_id"]
    assert task_id_2 != task_id_1

    # 旧 Task 已被删除
    get_old = client.get(f"/api/v1/tasks/{task_id_1}", headers=headers)
    assert get_old.status_code == 404

    # 新 Task 存在且内容正确
    get_new = client.get(f"/api/v1/tasks/{task_id_2}", headers=headers)
    assert get_new.status_code == 200
    assert get_new.json()["data"]["user_initial_preference"] == "替换版"

    # Verify ref_count is unchanged after replace (was 1, should stay 1)
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None
        assert row.task_ref_count == 1, f"Expected ref_count=1 after create-replace, got {row.task_ref_count}"
    finally:
        db.close()


def test_clone_task_to_another_kb() -> None:
    """克隆已有 Task 到另一个 KB：验证 clone 的字段一致性 + KB↔Video 关联建立。"""
    token = _login("alice-task-clone")
    headers = {"Authorization": f"Bearer {token}"}
    kbid_a, video_id = _prepare_assets(token)

    # 创建源 Task 并填充分析结果
    create_src = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_a, "video_id": video_id, "user_initial_preference": "源Task"},
        headers=headers,
    )
    assert create_src.status_code == 201
    src_task_id = create_src.json()["data"]["task_id"]
    _populate_task_analysis(src_task_id)

    # 创建 KB B
    kbid_b = _create_kb(token, "克隆目标知识库")

    # 克隆
    clone_resp = client.post(
        f"/api/v1/tasks/{src_task_id}/clone-to-kb",
        json={"kbid": kbid_b},
        headers=headers,
    )
    assert clone_resp.status_code == 201
    clone = clone_resp.json()["data"]

    # 基础字段验证
    assert clone["task_id"] != src_task_id
    assert clone["kbid"] == kbid_b
    assert clone["video_id"] == video_id

    # 分析字段照抄
    assert clone["final_summary"] == "最终摘要：这是一份关于项目进展的报告..."
    assert clone["draft_summary"] == "块聚合分析草稿"
    assert clone["title"] == "项目进展分析"
    assert clone["workflow_state"] == "COMPLETED"
    assert clone["user_initial_preference"] == "源Task"
    # summary_vector_ids must NOT be copied from source (they belong to source KB's vector collection)
    assert clone.get("summary_vector_ids") is None, (
        f"Expected summary_vector_ids=None in clone, got {clone.get('summary_vector_ids')}"
    )

    # KB↔Video 关联已建立（幂等，clone 流程自动添加）
    db = SessionLocal()
    try:
        relations = db.execute(
            select(kb_video_relation_table).where(
                kb_video_relation_table.c.kbid == kbid_b,
                kb_video_relation_table.c.video_id == video_id,
            )
        ).all()
        assert len(relations) == 1
    finally:
        db.close()

    # clone 在目标 KB 的 Task 列表中可见
    list_resp = client.get(
        f"/api/v1/videos/{video_id}/tasks?page=1&page_size=20",
        headers=headers,
    )
    assert list_resp.status_code == 200
    task_ids = [t["task_id"] for t in list_resp.json()["data"]]
    assert src_task_id in task_ids
    assert clone["task_id"] in task_ids


def test_clone_task_dispatches_vector_indexing(monkeypatch) -> None:
    """克隆 Task 时触发 async_add_video_to_vector_collection 向量索引。"""
    token = _login("alice-task-clone-vector")
    headers = {"Authorization": f"Bearer {token}"}
    kbid_a, video_id = _prepare_assets(token)

    # 创建源 Task
    create_src = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_a, "video_id": video_id, "user_initial_preference": "源Task"},
        headers=headers,
    )
    assert create_src.status_code == 201
    src_task_id = create_src.json()["data"]["task_id"]
    _populate_task_analysis(src_task_id)

    kbid_b = _create_kb(token, "向量索引目标库")

    # Monkeypatch: 捕获 Celery 任务调度
    dispatched: list[tuple] = []

    def _fake_delay(kbid: str, video_id: str):
        dispatched.append((kbid, video_id))

    monkeypatch.setattr(
        "backend.tasks.global_retrieval_tasks.async_add_video_to_vector_collection.delay",
        _fake_delay,
    )

    clone_resp = client.post(
        f"/api/v1/tasks/{src_task_id}/clone-to-kb",
        json={"kbid": kbid_b},
        headers=headers,
    )
    assert clone_resp.status_code == 201

    # 验证 async_add_video_to_vector_collection.delay 被调用
    assert len(dispatched) == 1
    assert dispatched[0] == (kbid_b, video_id)


def test_clone_task_duplicate_detection_and_replace() -> None:
    """克隆到已有同视频 Task 的 KB 时返回 409；提供 replace 后旧 Task 被替换。"""
    token = _login("alice-task-clone-dup")
    headers = {"Authorization": f"Bearer {token}"}
    kbid_a, video_id = _prepare_assets(token)

    # 在 KB A 创建源 Task
    create_src = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_a, "video_id": video_id, "user_initial_preference": "源Task"},
        headers=headers,
    )
    assert create_src.status_code == 201
    src_task_id = create_src.json()["data"]["task_id"]
    _populate_task_analysis(src_task_id)

    # 创建 KB B，并在 KB B 中为同视频建一个 Task（制造重复）
    kbid_b = _create_kb(token, "已有Task的知识库")
    create_existing = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_b, "video_id": video_id, "user_initial_preference": "KB B 中的旧Task"},
        headers=headers,
    )
    assert create_existing.status_code == 201
    existing_task_id = create_existing.json()["data"]["task_id"]

    # 克隆到 KB B → 409（KB B 已有同视频 Task）
    clone_dup = client.post(
        f"/api/v1/tasks/{src_task_id}/clone-to-kb",
        json={"kbid": kbid_b},
        headers=headers,
    )
    assert clone_dup.status_code == 409
    assert clone_dup.json()["error"]["details"]["existing_task_id"] == existing_task_id

    # 带 replace 克隆 → 旧 Task 被替换
    clone_replace = client.post(
        f"/api/v1/tasks/{src_task_id}/clone-to-kb",
        json={"kbid": kbid_b, "replace_existing_task_id": existing_task_id},
        headers=headers,
    )
    assert clone_replace.status_code == 201
    new_task_id = clone_replace.json()["data"]["task_id"]
    assert new_task_id != existing_task_id
    assert new_task_id != src_task_id

    # 旧 Task 已被删除
    get_old = client.get(f"/api/v1/tasks/{existing_task_id}", headers=headers)
    assert get_old.status_code == 404

    # clone 的 kbid 指向 KB B
    assert clone_replace.json()["data"]["kbid"] == kbid_b

    # Verify ref_count: source task in KB_A (1) + clone in KB_B (1) = 2.
    # The old KB_B task was replaced, so there is no net change.
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None
        assert row.task_ref_count == 2, (
            f"Expected ref_count=2 after clone-replace, got {row.task_ref_count}"
        )
    finally:
        db.close()


def test_clone_task_ref_count_accounting() -> None:
    """Clone preserves correct ref_count: each clone increments ref_count by 1."""
    token = _login("alice-clone-refcount")
    headers = {"Authorization": f"Bearer {token}"}
    kbid_a, video_id = _prepare_assets(token)

    # Create source task
    create_src = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_a, "video_id": video_id},
        headers=headers,
    )
    assert create_src.status_code == 201

    # Verify initial ref_count
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 1, (
            f"Expected ref_count=1 after create, got {row.task_ref_count}"
        )
    finally:
        db.close()

    # Clone to another KB
    kbid_b = _create_kb(token, "refcount验证库")
    src_task_id = create_src.json()["data"]["task_id"]
    clone_resp = client.post(
        f"/api/v1/tasks/{src_task_id}/clone-to-kb",
        json={"kbid": kbid_b},
        headers=headers,
    )
    assert clone_resp.status_code == 201

    # After clone, ref_count should be 2
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 2, (
            f"Expected ref_count=2 after clone, got {row.task_ref_count}"
        )
    finally:
        db.close()


def test_ref_count_matches_task_count_after_delete() -> None:
    """Delete one of two tasks and verify ref_count decrements from 2 to 1.

    Uses two tasks (in different KBs) so ref_count does not reach 0,
    avoiding async GC from removing the VideoResource row mid-test.
    """
    token = _login("alice-delete-refcount")
    headers = {"Authorization": f"Bearer {token}"}
    kbid_a, video_id = _prepare_assets(token)

    # Create task in KB_A
    create_a = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_a, "video_id": video_id},
        headers=headers,
    )
    assert create_a.status_code == 201
    task_id_a = create_a.json()["data"]["task_id"]

    # Create task in KB_B (same video)
    kbid_b = _create_kb(token, "删除测试KB_B")
    create_b = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid_b, "video_id": video_id},
        headers=headers,
    )
    assert create_b.status_code == 201
    task_id_b = create_b.json()["data"]["task_id"]

    # Verify ref_count == 2
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 2, (
            f"Expected ref_count=2 after creating 2 tasks, got {row.task_ref_count}"
        )
    finally:
        db.close()

    # Delete one task
    delete_resp = client.delete(f"/api/v1/tasks/{task_id_a}", headers=headers)
    assert delete_resp.status_code == 200

    # Verify ref_count == 1 (delete_by_owner_and_id atomically decremented)
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 1, (
            f"Expected ref_count=1 after deleting 1 of 2 tasks, got {row.task_ref_count}"
        )
    finally:
        db.close()


def test_ref_count_unchanged_after_replace_in_create() -> None:
    """Replacing a task in create should not change ref_count (net zero)."""
    token = _login("alice-create-replace-refcount")
    headers = {"Authorization": f"Bearer {token}"}
    kbid, video_id = _prepare_assets(token)

    # Create first task
    create1 = client.post(
        "/api/v1/tasks",
        json={"kbid": kbid, "video_id": video_id},
        headers=headers,
    )
    assert create1.status_code == 201
    task_id_1 = create1.json()["data"]["task_id"]

    # Verify ref_count == 1
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 1
    finally:
        db.close()

    # Replace with new task (create + replace_existing_task_id)
    create2 = client.post(
        "/api/v1/tasks",
        json={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "替换版",
            "replace_existing_task_id": task_id_1,
        },
        headers=headers,
    )
    assert create2.status_code == 201

    # Verify ref_count still == 1 (delete decremented, create incremented → net 0)
    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        assert row is not None and row.task_ref_count == 1, (
            f"Expected ref_count=1 after create-replace, got {row.task_ref_count}"
        )
    finally:
        db.close()
