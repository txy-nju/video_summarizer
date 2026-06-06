from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.models.database import KnowledgeBase, VideoQARecord, VideoSummaryTask


@dataclass(frozen=True, slots=True)
class VideoQARecordData:
    """单视频局部追问记录"""
    qa_id: str
    task_id: str
    owner_id: str  # 从 task 继承获取，用于权限校验
    start_time: str
    end_time: str
    question_content: str
    answer_content: str | None
    attachments: str  # JSON 格式存储
    cited_sources: str  # JSON 格式存储
    question_time: datetime


class VideoQARepository:
    """数据库 Repository 实现"""

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(
        self,
        *,
        owner_id: str,
        task_id: str,
        start_time: str,
        end_time: str,
        question_content: str,
        attachments: list[dict],
    ) -> VideoQARecordData:
        """创建新的问答记录"""
        task = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if task is None:
            raise ValueError("Task not found")

        entity = VideoQARecord(
            task_id=task_id,
            start_time=start_time,
            end_time=end_time,
            question_content=question_content,
            answer_content=None,
            attachments=attachments,
            cited_sources=[],
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity, owner_id=owner_id)

    def list_by_owner_and_task(self, owner_id: str, task_id: str) -> list[VideoQARecordData]:
        """查询某个任务下的所有问答"""
        rows = (
            self._owned_qa_query(owner_id)
            .filter(VideoQARecord.task_id == task_id)
            .order_by(VideoQARecord.question_time.asc())
            .all()
        )
        return [self._to_record(row, owner_id=owner_id) for row in rows]

    def get_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str
    ) -> VideoQARecordData | None:
        """获取单条问答记录"""
        row = (
            self._owned_qa_query(owner_id)
            .filter(VideoQARecord.task_id == task_id, VideoQARecord.qa_id == qa_id)
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    def update_answer_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str, answer_content: str,
        cited_sources: list[dict] | None = None,
    ) -> VideoQARecordData | None:
        """更新问答的回答内容（重新生成场景）"""
        row = (
            self._owned_qa_query(owner_id)
            .filter(VideoQARecord.task_id == task_id, VideoQARecord.qa_id == qa_id)
            .one_or_none()
        )
        if row is None:
            return None

        row.answer_content = answer_content
        if cited_sources is not None:
            row.cited_sources = cited_sources
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def delete_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str
    ) -> bool:
        """删除单条问答记录"""
        row = (
            self._owned_qa_query(owner_id)
            .filter(VideoQARecord.task_id == task_id, VideoQARecord.qa_id == qa_id)
            .one_or_none()
        )
        if row is None:
            return False

        self._session.delete(row)
        self._session.commit()
        return True

    def delete_all_by_owner_and_task(self, owner_id: str, task_id: str) -> int:
        """删除某个任务下的所有问答（级联删除支持）"""
        rows = (
            self._owned_qa_query(owner_id)
            .filter(VideoQARecord.task_id == task_id)
            .all()
        )
        count = len(rows)
        for row in rows:
            self._session.delete(row)
        if count > 0:
            self._session.commit()
        return count

    def _owned_task_query(self, owner_id: str):
        return (
            self._session.query(VideoSummaryTask)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .filter(KnowledgeBase.owner_id == owner_id)
        )

    def _owned_qa_query(self, owner_id: str):
        return (
            self._session.query(VideoQARecord)
            .join(VideoSummaryTask, VideoQARecord.task_id == VideoSummaryTask.task_id)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .filter(KnowledgeBase.owner_id == owner_id)
        )

    @staticmethod
    def _to_record(entity: VideoQARecord, *, owner_id: str) -> VideoQARecordData:
        question_time = getattr(entity, "question_time", None) or datetime.now(UTC)
        attachments = entity.attachments or []
        cited_sources = getattr(entity, "cited_sources", None) or []
        return VideoQARecordData(
            qa_id=str(entity.qa_id),
            task_id=str(entity.task_id),
            owner_id=owner_id,
            start_time=entity.start_time or "",
            end_time=entity.end_time or "",
            question_content=entity.question_content,
            answer_content=entity.answer_content,
            attachments=json.dumps(attachments),
            cited_sources=json.dumps(cited_sources),
            question_time=question_time,
        )
