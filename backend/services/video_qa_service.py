from __future__ import annotations

import json

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.video_qa_repository import VideoQARepository
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
from backend.schemas.video_qa import (
    AttachmentInfo,
    VideoQARecordCreateRequest,
    VideoQARecordUpdateRequest,
    VideoQARecordView,
)


class VideoQAService:
    """单视频局部追问服务"""

    def __init__(
        self,
        repository: VideoQARepository,
        task_repository: VideoSummaryTaskRepository,
    ) -> None:
        self._repository = repository
        self._task_repository = task_repository

    def create_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        payload: VideoQARecordCreateRequest,
    ) -> VideoQARecordView | None:
        """创建新的问答记录"""
        # 验证任务是否属于该用户
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return None

        # 创建问答记录
        attachments_data = [asdict(a) for a in payload.attachments]
        record = self._repository.create(
            owner_id=owner_id,
            task_id=task_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            question_content=payload.question_content,
            attachments=attachments_data,
        )
        return self._to_view(record)

    def list_qa_records(
        self,
        *,
        owner_id: str,
        task_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[VideoQARecordView], dict]:
        """查询某个任务下的所有问答"""
        # 验证任务是否属于该用户
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return [], build_pagination(page=page, page_size=page_size, total=0)

        records = self._repository.list_by_owner_and_task(owner_id, task_id)
        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = records[start_index:end_index]

        views = [self._to_view(record) for record in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(records),
            next_cursor=None,
        )
        return views, pagination

    def get_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        qa_id: str,
    ) -> VideoQARecordView | None:
        """获取单条问答记录"""
        record = self._repository.get_by_owner_task_and_qa_id(owner_id, task_id, qa_id)
        if record is None:
            return None
        return self._to_view(record)

    def update_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        qa_id: str,
        payload: VideoQARecordUpdateRequest,
    ) -> VideoQARecordView | None:
        """更新问答记录（重新生成回答）"""
        # 注意：当前仅支持重生成意图，具体回答由外部流程（LLM Agent）生成
        # 本服务仅负责更新标记；实际回答生成由 Celery 任务或其他异步服务处理
        if not payload.regenerate:
            return None

        record = self._repository.get_by_owner_task_and_qa_id(owner_id, task_id, qa_id)
        if record is None:
            return None

        # 这里仅作占位符；实际的回答更新应由外部任务触发
        # 例如：触发 Celery task `async_regenerate_qa_answer(task_id, qa_id)`
        return self._to_view(record)

    def delete_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        qa_id: str,
    ) -> bool:
        """删除单条问答记录"""
        # 验证任务是否属于该用户
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return False
        return self._repository.delete_by_owner_task_and_qa_id(owner_id, task_id, qa_id)

    def create_time_travel_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        timestamp: str,
        question_content: str,
        answer_content: str,
        attachments: list[AttachmentInfo],
        window_seconds: int,
    ) -> VideoQARecordView | None:
        """Persist time-travel Q&A record with computed evidence window."""
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return None

        start_time, end_time = self._compute_time_window(timestamp, window_seconds)
        record = self._repository.create(
            owner_id=owner_id,
            task_id=task_id,
            start_time=start_time,
            end_time=end_time,
            question_content=question_content,
            attachments=[asdict(a) for a in attachments],
        )
        updated = self._repository.update_answer_by_owner_task_and_qa_id(
            owner_id,
            task_id,
            record.qa_id,
            answer_content,
        )
        if updated is None:
            return None
        return self._to_view(updated)

    @staticmethod
    def _to_view(record) -> VideoQARecordView:
        """将 Repository 记录转换为视图"""
        attachments = []
        try:
            if record.attachments:
                attachments_data = json.loads(record.attachments)
                attachments = [AttachmentInfo(**a) for a in attachments_data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        return VideoQARecordView(
            qa_id=record.qa_id,
            task_id=record.task_id,
            start_time=record.start_time,
            end_time=record.end_time,
            question_content=record.question_content,
            answer_content=record.answer_content,
            attachments=attachments,
            question_time=record.question_time,
        )

    @staticmethod
    def _compute_time_window(timestamp: str, window_seconds: int) -> tuple[str, str]:
        hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
        center_seconds = hours * 3600 + minutes * 60 + seconds
        half_window = max(window_seconds // 2, 0)
        start_seconds = max(center_seconds - half_window, 0)
        end_seconds = center_seconds + half_window
        return VideoQAService._seconds_to_hms(start_seconds), VideoQAService._seconds_to_hms(end_seconds)

    @staticmethod
    def _seconds_to_hms(total_seconds: int) -> str:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# ============================================
# 辅助函数
# ============================================


def asdict(obj) -> dict:
    """简单对象转字典（支持 Pydantic Model）"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    elif hasattr(obj, "__dict__"):
        return obj.__dict__
    else:
        return {}
