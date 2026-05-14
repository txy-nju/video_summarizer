from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


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
    question_time: datetime


class VideoQARepository:
    """内存 Repository 实现（步骤 4 临时方案）"""

    def __init__(self) -> None:
        # 按 owner_id -> task_id -> qa_id 三层嵌套存储
        self._records_by_owner: dict[str, dict[str, dict[str, VideoQARecordData]]] = {}
        self._lock = Lock()

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
        now = datetime.now(UTC)
        record = VideoQARecordData(
            qa_id=str(uuid7()),
            task_id=task_id,
            owner_id=owner_id,
            start_time=start_time,
            end_time=end_time,
            question_content=question_content,
            answer_content=None,
            attachments=json.dumps(attachments),
            question_time=now,
        )
        with self._lock:
            owner_bucket = self._records_by_owner.setdefault(owner_id, {})
            task_bucket = owner_bucket.setdefault(task_id, {})
            task_bucket[record.qa_id] = record
        return record

    def list_by_owner_and_task(self, owner_id: str, task_id: str) -> list[VideoQARecordData]:
        """查询某个任务下的所有问答"""
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            task_bucket = owner_bucket.get(task_id, {})
            return sorted(task_bucket.values(), key=lambda item: item.question_time)

    def get_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str
    ) -> VideoQARecordData | None:
        """获取单条问答记录"""
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            task_bucket = owner_bucket.get(task_id, {})
            return task_bucket.get(qa_id)

    def update_answer_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str, answer_content: str
    ) -> VideoQARecordData | None:
        """更新问答的回答内容（重新生成场景）"""
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            task_bucket = owner_bucket.get(task_id, {})
            current = task_bucket.get(qa_id)
            if current is None:
                return None
            updated = replace(current, answer_content=answer_content)
            task_bucket[qa_id] = updated
            return updated

    def delete_by_owner_task_and_qa_id(
        self, owner_id: str, task_id: str, qa_id: str
    ) -> bool:
        """删除单条问答记录"""
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            task_bucket = owner_bucket.get(task_id, {})
            if qa_id in task_bucket:
                del task_bucket[qa_id]
                return True
        return False

    def delete_all_by_owner_and_task(self, owner_id: str, task_id: str) -> int:
        """删除某个任务下的所有问答（级联删除支持）"""
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            task_bucket = owner_bucket.get(task_id, {})
            count = len(task_bucket)
            task_bucket.clear()
        return count
