from __future__ import annotations

from typing import Any

from config.settings import CHECKPOINT_BACKEND, CHECKPOINT_DB_URL
from core.workflow.checkpoint_factory import get_checkpoint_snapshot
from core.workflow.session import build_restore_payload

from backend.repositories.session_repository import SessionRepository


class SessionService:
    """Session restore orchestration for video summary workflow scope."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    def restore_video_task_session(
        self,
        *,
        owner_id: str,
        kbid: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        record = self._repository.get_video_task_session(
            owner_id=owner_id,
            kbid=kbid,
            task_id=task_id,
        )
        if record is None:
            return None

        checkpoint = get_checkpoint_snapshot(
            backend=CHECKPOINT_BACKEND,
            postgres_url=CHECKPOINT_DB_URL,
            thread_id=record.thread_id,
        )
        if checkpoint is None:
            return None

        return {
            "status": "success",
            "data": build_restore_payload(
                scope_id=record.task_id,
                checkpoint_status="restored",
                workflow_state=record.workflow_state,
            ),
        }
