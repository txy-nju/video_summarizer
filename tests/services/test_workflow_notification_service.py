from __future__ import annotations

from dataclasses import dataclass

from backend.services.workflow_notification_service import WorkflowNotificationService


@dataclass
class _Device:
    device_token: str
    device_id: str


class _StubDeviceRepository:
    def __init__(self, devices):
        self._devices = devices

    def list_by_user(self, user_id: str):
        return self._devices


class _StubFCMService:
    def __init__(self):
        self.sent = []

    def send(self, request):
        self.sent.append(request)


def test_notify_workflow_completed_payload_shape():
    fcm = _StubFCMService()
    svc = WorkflowNotificationService(
        fcm_service=fcm,
        device_repository=_StubDeviceRepository([_Device("token-a", "dev-a")]),
    )

    result = svc.notify_workflow_completed(user_id="u1", task_id="task-001")

    assert result["device_count"] == 1
    assert result["success_count"] == 1
    request = fcm.sent[0]
    assert request.data.scope == "video_summary_task"
    assert request.data.scope_id == "task-001"
    assert request.data.deep_link == "app://tasks/task-001"


def test_notify_workflow_failed_payload_shape():
    fcm = _StubFCMService()
    svc = WorkflowNotificationService(
        fcm_service=fcm,
        device_repository=_StubDeviceRepository([_Device("token-a", "dev-a")]),
    )

    result = svc.notify_workflow_failed(user_id="u1", task_id="task-002", error_message="x")

    assert result["device_count"] == 1
    assert result["failed_count"] == 0
    request = fcm.sent[0]
    assert request.data.scope == "video_summary_task"
    assert request.data.scope_id == "task-002"
    assert request.data.deep_link == "app://tasks/task-002/error"


def test_notify_returns_empty_when_no_devices():
    fcm = _StubFCMService()
    svc = WorkflowNotificationService(
        fcm_service=fcm,
        device_repository=_StubDeviceRepository([]),
    )

    result = svc.notify_workflow_approval_required(user_id="u1", task_id="task-003", chunk_count=3)

    assert result["device_count"] == 0
    assert result["success_count"] == 0
    assert len(fcm.sent) == 0
