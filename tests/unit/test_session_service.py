from __future__ import annotations

from unittest.mock import Mock, patch

from backend.repositories.session_repository import VideoTaskSessionRecord
from backend.services.session_service import SessionService


def test_restore_video_task_session_success() -> None:
    repository = Mock()
    repository.get_video_task_session.return_value = VideoTaskSessionRecord(
        task_id="task_001",
        kbid="kb_001",
        workflow_state="WAITING_USER_APPROVAL",
        thread_id="task_001",
    )
    service = SessionService(repository=repository)

    with patch("backend.services.session_service.get_checkpoint_snapshot", return_value={"channel_values": {}}):
        response = service.restore_video_task_session(
            owner_id="user_001",
            kbid="kb_001",
            task_id="task_001",
        )

    repository.get_video_task_session.assert_called_once_with(
        owner_id="user_001",
        kbid="kb_001",
        task_id="task_001",
    )
    assert response == {
        "status": "success",
        "data": {
            "scope": "video_summary_task",
            "scope_id": "task_001",
            "checkpoint_status": "restored",
            "workflow_state": "WAITING_USER_APPROVAL",
        },
    }


def test_restore_video_task_session_returns_none_when_scope_not_found() -> None:
    repository = Mock()
    repository.get_video_task_session.return_value = None
    service = SessionService(repository=repository)

    response = service.restore_video_task_session(
        owner_id="user_001",
        kbid="kb_001",
        task_id="task_001",
    )

    assert response is None


def test_restore_video_task_session_returns_none_when_checkpoint_missing() -> None:
    repository = Mock()
    repository.get_video_task_session.return_value = VideoTaskSessionRecord(
        task_id="task_001",
        kbid="kb_001",
        workflow_state="WAITING_USER_APPROVAL",
        thread_id="task_001",
    )
    service = SessionService(repository=repository)

    with patch("backend.services.session_service.get_checkpoint_snapshot", return_value=None):
        response = service.restore_video_task_session(
            owner_id="user_001",
            kbid="kb_001",
            task_id="task_001",
        )

    assert response is None
