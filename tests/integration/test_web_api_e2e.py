"""Web API Front-end Call Complete E2E Integration Test.

This test simulates the full client journey through the FastAPI Web API layer,
mimicking real frontend requests and verifying:
- JWT Authentication & registration
- FCM Device token registration
- WebSocket progress connection & heartbeats (ping/pong)
- TUS protocol chunk upload and finalization (mocked/local storage client)
- Task Creation & Workflow execution (DRAFT_GENERATING -> WAITING_USER_APPROVAL)
- Real-time WebSocket event dispatching
- FCM Push notifications sent on state changes
- Human Review and task completion (FINAL_GENERATING -> COMPLETED)
- Time Travel Q&A & Stream RAG Q&A (SSE stream output)
- Global KB Chat Session creation, QA, history query, and deletion
"""

from __future__ import annotations

import os
import time
import uuid
import json
from datetime import datetime, UTC
from threading import Timer
from typing import Any, Iterator, Generator

import pytest
from fastapi.testclient import TestClient

from backend.app_factory import create_app
from backend.db.session import SessionLocal
from backend.models.database import VideoResource, DeviceToken
from backend.models.enums import FrameExtractionStatus, TranscribeStatus


# Ensure celery uses synchronous eager mode for tests
@pytest.fixture(scope="module", autouse=True)
def configure_celery():
    try:
        from celery import current_app
        current_app.conf.update(
            task_always_eager=True,
            task_eager_propagates=True,
        )
    except ImportError:
        pass


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class MockMessage:
    def __init__(self, token=None, notification=None, data=None):
        self.token = token
        if isinstance(notification, dict):
            class DummyNotification:
                def __init__(self, title, body):
                    self.title = title
                    self.body = body
            self.notification = DummyNotification(notification.get("title"), notification.get("body"))
        else:
            self.notification = notification
        self.data = data


class MockFirebaseMessaging:
    Message = MockMessage

    def __init__(self):
        self.sent_messages = []

    def send(self, message):
        # Capture sent notification details
        # message is an instance of firebase_admin.messaging.Message
        # We can extract details from its attributes
        self.sent_messages.append({
            "token": getattr(message, "token", None),
            "title": message.notification.title if message.notification else None,
            "body": message.notification.body if message.notification else None,
            "data": message.data,
        })
        return f"mock-fcm-msg-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def mock_external_services(monkeypatch):
    # 1. Mock FCM firebase messaging SDK
    mock_fcm = MockFirebaseMessaging()
    monkeypatch.setattr("backend.notifications.fcm_service._get_firebase_messaging", lambda: mock_fcm)

    # 2. Mock core LLM/LangGraph API workflows
    def mock_analyze_video(
        transcript: str,
        keyframes: list[dict],
        user_prompt: str,
        status_callback=None,
        thread_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        if status_callback:
            status_callback("⚙️ [LangGraph 初始化] 正在编排多智能体认知状态机网络...")
            # Simulate real chunk progress JSON messages
            status_callback('[[PROGRESS]]{"type": "chunk_progress", "stage": "running", "overall_percent": 50, "overall_done": 1, "overall_total": 2}')
            status_callback('[[PROGRESS]]{"type": "chunk_progress", "stage": "finished", "overall_percent": 100, "overall_done": 2, "overall_total": 2}')
            status_callback("🧑‍⚖️ [Human Gate] 分析完成，等待人类审批。")
        return {
            "thread_id": thread_id or f"thread-{uuid.uuid4().hex[:8]}",
            "aggregated_chunk_insights": "Mocked draft insights from video analysis",
            "editable_aggregated_chunk_insights": "Mocked draft insights from video analysis (editable)",
            "chunk_count": 2,
            "human_gate_status": "pending",
        }

    def mock_finalize_summary(
        thread_id: str,
        edited_aggregated_chunk_insights: str,
        human_guidance: str = "",
        status_callback=None,
        trace_id: str = "",
    ) -> str:
        if status_callback:
            status_callback("⚡ [Finalizing] 生成最终总结中...")
        return "Mocked final summary of the video based on insights."

    def mock_answer_question_at_timestamp(
        thread_id: str,
        timestamp: str,
        question: str,
        window_seconds: int = 20,
        status_callback=None,
        trace_id: str = "",
    ) -> str:
        if status_callback:
            status_callback("🕒 [Time Travel] 回溯时间戳中...")
        return f"Mocked answer for question: '{question}' at timestamp {timestamp}"

    monkeypatch.setattr("backend.services.workflow_orchestration_service.analyze_video", mock_analyze_video)
    monkeypatch.setattr("backend.services.workflow_orchestration_service.finalize_summary", mock_finalize_summary)
    monkeypatch.setattr("backend.services.workflow_orchestration_service.answer_question_at_timestamp", mock_answer_question_at_timestamp)

    # 3. Mock RAG Agent Service (avoid dependency on chroma / models)
    from backend.services.rag_agent_service import RagAgentService, RagAgentAnswer

    def mock_stream_video_question(self, owner_id, task_id, question_content, attachments):
        cited_sources = [{"video_id": "vid_1", "time_range": "00:00:10-00:00:20", "quote": "source text", "score": 0.95}]
        def token_gen():
            yield "Mocked "
            yield "RAG "
            yield "answer "
            yield "for "
            yield f"question: {question_content}"
        return cited_sources, token_gen()

    def mock_answer_global_question(self, owner_id, kbid, question_content, attachments, kb_config=None):
        return RagAgentAnswer(
            answer_content=f"Mocked global RAG answer for {question_content}",
            cited_sources=[{"video_id": "vid_1", "time_range": "00:00:10-00:00:20", "quote": "source text", "score": 0.95}],
        )

    def mock_stream_global_question(self, owner_id, kbid, question_content, attachments, kb_config=None):
        cited_sources = [{"video_id": "vid_1", "time_range": "00:00:10-00:00:20", "quote": "source text", "score": 0.95}]
        def token_gen():
            yield "Mocked "
            yield "global "
            yield "RAG "
            yield "stream"
        return cited_sources, token_gen()

    monkeypatch.setattr(RagAgentService, "stream_video_question", mock_stream_video_question)
    monkeypatch.setattr(RagAgentService, "answer_global_question", mock_answer_global_question)
    monkeypatch.setattr(RagAgentService, "stream_global_question", mock_stream_global_question)

    yield mock_fcm

    # Clean up Redis DB 2 (domain events)
    try:
        import redis as redis_lib
        r = redis_lib.Redis.from_url("redis://localhost:6379/2")
        r.flushdb()
    except Exception:
        pass


def _login(client, username, password="Password123!"):
    client.post("/api/v1/auth/register", json={"username": username, "password": password})
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_id": f"dev-{username}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    return data["access_token"], data["user"]["user_id"]


def _mark_video_ready(video_id: str) -> None:
    """Simulates Celery background processing completing transcription and frame extraction."""
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


def test_complete_frontend_web_api_e2e_flow(client, mock_external_services) -> None:
    # --- 1. User Registration & Login ---
    username = f"e2e_user_{uuid.uuid4().hex[:8]}"
    token, user_id = _login(client, username)
    headers = {"Authorization": f"Bearer {token}"}

    # --- 2. Register Device Token for FCM Push ---
    device_token = f"fcm_token_{uuid.uuid4().hex[:8]}"
    device_resp = client.post(
        "/api/v1/devices",
        json={
            "device_token": device_token,
            "platform": "android",
            "app_version": "1.0.0",
            "device_id": f"dev-{username}",
        },
        headers=headers,
    )
    assert device_resp.status_code == 200

    # --- 3. WebSocket Progress Connection & Ping Heartbeat ---
    websocket_events = []
    with client.websocket_connect(f"/ws/progress?token={token}") as ws:
        # Check basic ping/pong
        ws.send_text("ping")
        pong = ws.receive_text()
        assert pong == "pong"

        # --- 4. Create Knowledge Base ---
        kb_resp = client.post(
            "/api/v1/kbs",
            json={
                "name": "E2E Test KB",
                "category": "research",
                "description": "E2E testing",
                "config": {
                    "retrieval": {"top_k": 3, "rerank": True},
                    "tool_preferences": {"allow_web_search": False},
                    "llm_policy": {"temperature": 0.0},
                },
            },
            headers=headers,
        )
        assert kb_resp.status_code == 201
        kbid = kb_resp.json()["data"]["kbid"]

        # --- 5. File Upload via TUS Protocol ---
        file_size = 1 * 1024 * 1024  # 1MB
        init_resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "e2e_video.mp4", "total_size": file_size},
            headers=headers,
        )
        assert init_resp.status_code == 201
        upload_id = init_resp.json()["upload_id"]

        # HEAD progress check (TUS)
        head_resp = client.head(f"/api/v1/uploads/{upload_id}", headers=headers)
        assert head_resp.status_code == 204
        assert head_resp.headers["Upload-Offset"] == "0"
        assert head_resp.headers["Upload-Length"] == str(file_size)

        # PATCH chunk upload (which triggers eager upload finalization celery task)
        chunk_data = b"x" * file_size
        patch_resp = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=chunk_data,
            headers={
                **headers,
                "Upload-Offset": "0",
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert patch_resp.status_code == 200

        # Query GET upload info
        get_up_resp = client.get(f"/api/v1/uploads/{upload_id}", headers=headers)
        assert get_up_resp.status_code == 200
        assert get_up_resp.json()["uploaded_size"] == file_size

        # Find the created video resource in the list
        list_videos_resp = client.get("/api/v1/videos", headers=headers)
        assert list_videos_resp.status_code == 200
        videos = list_videos_resp.json()["data"]
        assert len(videos) > 0
        video_id = videos[0]["video_id"]

        # Simulate background extraction worker completing keyframe extraction and transcription
        _mark_video_ready(video_id)

        # Verify video status is now READY
        video_detail_resp = client.get(f"/api/v1/videos/{video_id}", headers=headers)
        assert video_detail_resp.status_code == 200
        assert video_detail_resp.json()["data"]["transcribe_status"] == "COMPLETED"

        # --- 6. Video Summary Task Creation & Phase-1 execution ---
        task_create_resp = client.post(
            "/api/v1/tasks",
            json={
                "kbid": kbid,
                "video_id": video_id,
                "user_initial_preference": "Give me a structured summary focusing on details.",
            },
            headers=headers,
        )
        assert task_create_resp.status_code == 201
        task_id = task_create_resp.json()["data"]["task_id"]

        # Trigger phase-1 analysis (DRAFT_GENERATING)
        # Runs synchronously because task_always_eager=True
        start_resp = client.post(f"/api/v1/tasks/{task_id}/start-analysis", json={}, headers=headers)
        assert start_resp.status_code == 202

        # Verify task is now WAITING_USER_APPROVAL
        task_detail_resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert task_detail_resp.status_code == 200
        task_data = task_detail_resp.json()["data"]
        assert task_data["workflow_state"] == "WAITING_USER_APPROVAL"
        assert task_data["draft_summary"] == "Mocked draft insights from video analysis"

        # --- 7. Phase-2 Approval and Finalization ---
        approve_resp = client.post(
            f"/api/v1/tasks/{task_id}/approve-and-finalize",
            json={
                "edited_aggregated_chunk_insights": "User-edited chunk insights",
                "human_guidance": "Please emphasize the call to actions.",
            },
            headers=headers,
        )
        assert approve_resp.status_code == 202

        # Verify task state is now COMPLETED and final summary is present
        task_completed_resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert task_completed_resp.status_code == 200
        completed_task_data = task_completed_resp.json()["data"]
        assert completed_task_data["workflow_state"] == "COMPLETED"
        assert completed_task_data["final_summary"] == "Mocked final summary of the video based on insights."

        # --- 8. Collect and Assert WebSocket Progress Events ---
        # Read from WebSocket until we receive the final completed event of the entire workflow
        try:
            for _ in range(100):
                msg_str = ws.receive_text()
                evt = json.loads(msg_str)
                websocket_events.append(evt)
                # Check if this is the final completed event (where workflow_state is COMPLETED)
                if evt.get("event_type") == "completed" or evt.get("status") == "COMPLETED":
                    payload = evt.get("payload", {})
                    result = payload.get("result", {})
                    if result.get("workflow_state") == "COMPLETED":
                        break
        except Exception:
            pass

        # Assert correct sequence of WebSocket events were received
        assert len(websocket_events) >= 6  # Should receive DRAFT_GENERATING, progress deltas, WAITING_USER_APPROVAL, FINAL_GENERATING, COMPLETED, etc.
        event_types = [e["event_type"] for e in websocket_events]
        assert "status_update" in event_types
        assert "progress" in event_types

        # Check status codes and workflow_states inside the event bodies
        statuses = []
        for e in websocket_events:
            if e.get("status") is not None:
                statuses.append(e.get("status"))
            # Extract nested workflow_state from result payload if present
            payload = e.get("payload", {})
            result = payload.get("result", {})
            if result.get("workflow_state") is not None:
                statuses.append(result.get("workflow_state"))

        print("DEBUG: RECEIVED EVENTS:", json.dumps(websocket_events, indent=2, ensure_ascii=True))
        print("DEBUG: EXTRACTED STATUSES:", statuses)
        assert "DRAFT_GENERATING" in statuses
        assert "WAITING_USER_APPROVAL" in statuses
        assert "FINAL_GENERATING" in statuses
        assert "COMPLETED" in statuses

        # --- 9. Assert FCM Push Notifications Sent ---
        fcm_messages = mock_external_services.sent_messages
        assert len(fcm_messages) >= 2  # At least notify_workflow_approval_required and notify_workflow_completed
        
        # Verify approval required notification content
        approval_notif = next((m for m in fcm_messages if "等待您的审核" in m["title"]), None)
        assert approval_notif is not None
        assert approval_notif["token"] == device_token
        assert approval_notif["data"]["scope_id"] == task_id

        # Verify completed notification content
        completed_notif = next((m for m in fcm_messages if "视频总结已完成" in m["title"]), None)
        assert completed_notif is not None
        assert completed_notif["token"] == device_token
        assert completed_notif["data"]["scope_id"] == task_id

        # --- 10. Video Q&A (Time Travel QA with SSE Stream) ---
        qa_stream_resp = client.post(
            f"/api/v1/tasks/{task_id}/time-travel-qa/stream",
            json={
                "timestamp": "00:00:10",
                "question_content": "What was discussed at 10 seconds?",
                "window_seconds": 20,
            },
            headers=headers,
        )
        assert qa_stream_resp.status_code == 200
        assert "text/event-stream" in qa_stream_resp.headers["content-type"]

        # Parse SSE stream response
        sse_events = []
        for line_str in qa_stream_resp.text.splitlines():
            line_str = line_str.strip()
            if line_str.startswith("data:"):
                data_payload = json.loads(line_str[5:])
                sse_events.append(data_payload)

        assert len(sse_events) > 0
        assert sse_events[0]["task_id"] == task_id
        # The 'done' event should contain the full mocked answer
        done_event = next((e for e in sse_events if "answer_content" in e), None)
        assert done_event is not None
        assert "Mocked answer for question" in done_event["answer_content"]

        # Query GET tasks QA list
        list_qa_resp = client.get(f"/api/v1/tasks/{task_id}/qa", headers=headers)
        assert list_qa_resp.status_code == 200
        assert len(list_qa_resp.json()["data"]) > 0
        qa_id = list_qa_resp.json()["data"][0]["qa_id"]

        # Get single QA details
        get_qa_resp = client.get(f"/api/v1/tasks/{task_id}/qa/{qa_id}", headers=headers)
        assert get_qa_resp.status_code == 200
        assert get_qa_resp.json()["data"]["question_content"] == "What was discussed at 10 seconds?"

        # --- 11. Global KB Chat Sessions & QA ---
        # Create global chat session
        chat_create_resp = client.post(
            "/api/v1/kbs/{}/chats".format(kbid),
            json={"kbid": kbid, "chat_title": "E2E Global Session"},
            headers=headers,
        )
        assert chat_create_resp.status_code == 201
        chat_id = chat_create_resp.json()["data"]["chat_id"]

        # Send query to global chat with SSE stream response
        global_qa_stream_resp = client.post(
            "/api/v1/kbs/{}/chats/{}/qa/stream".format(kbid, chat_id),
            json={"question_content": "Tell me about global search."},
            headers=headers,
        )
        assert global_qa_stream_resp.status_code == 200
        
        global_sse_events = []
        for line_str in global_qa_stream_resp.text.splitlines():
            line_str = line_str.strip()
            if line_str.startswith("data:"):
                global_sse_events.append(json.loads(line_str[5:]))

        print("DEBUG: GLOBAL SSE EVENTS:", json.dumps(global_sse_events, indent=2, ensure_ascii=True))
        assert len(global_sse_events) > 0
        global_done = next((e for e in global_sse_events if "answer_content" in e), None)
        assert global_done is not None
        assert "Mocked global RAG stream" in global_done["answer_content"]

        # Query Global Chat history
        chat_history_resp = client.get("/api/v1/kbs/{}/chats/{}/qa".format(kbid, chat_id), headers=headers)
        assert chat_history_resp.status_code == 200
        assert len(chat_history_resp.json()["data"]) > 0

        # Delete Global Chat Session
        delete_chat_resp = client.delete("/api/v1/kbs/{}/chats/{}".format(kbid, chat_id), headers=headers)
        assert delete_chat_resp.status_code == 200
