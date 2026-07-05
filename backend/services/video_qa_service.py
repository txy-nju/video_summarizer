from __future__ import annotations

import json
import logging
from typing import Iterator

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.video_qa_repository import VideoQARepository
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
from backend.schemas.video_qa import (
    AttachmentInfo,
    VideoQARecordCreateRequest,
    VideoQARecordUpdateRequest,
    VideoQARecordView,
)
from core.agent.events import AgentProgressEvent

logger = logging.getLogger(__name__)


class VideoQAService:
    """单视频局部追问服务 — 使用 VideoQAAgent 生成回答。"""

    def __init__(
        self,
        repository: VideoQARepository,
        task_repository: VideoSummaryTaskRepository,
        rag_agent_service=None,  # Deprecated: 保留用于向后兼容，VideoQAAgent 内部使用
        agent: Any | None = None,  # VideoQAAgent
        chat_memory: Any | None = None,  # BaseChatMemory for recording turns
    ) -> None:
        self._repository = repository
        self._task_repository = task_repository
        self._rag_agent_service = rag_agent_service
        self._agent = agent
        self._chat_memory = chat_memory

    # ── 公开属性 ──────────────────────────────────────────────────────────

    @property
    def agent(self) -> Any | None:
        """Expose the agent so callers can read ``last_cited_sources``."""
        return self._agent

    # ── CRUD ───────────────────────────────────────────────────────────────

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

        attachments_data = [asdict(a) for a in payload.attachments]
        record = self._repository.create(
            owner_id=owner_id,
            task_id=task_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            question_content=payload.question_content,
            attachments=attachments_data,
        )
        answer, cited_sources = self._generate_answer(
            owner_id=owner_id,
            task_id=task_id,
            question=payload.question_content,
            attachments=attachments_data,
        )
        if answer:
            updated = self._repository.update_answer_by_owner_task_and_qa_id(
                owner_id, task_id, record.qa_id, answer,
                cited_sources=cited_sources,
            )
            if updated:
                return self._to_view(updated)
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
        """重新生成回答：用原始问题重新走 RAG 流水线并更新记录。"""
        if not payload.regenerate:
            return None

        record = self._repository.get_by_owner_task_and_qa_id(owner_id, task_id, qa_id)
        if record is None:
            return None

        new_answer, cited_sources = self._generate_answer(
            owner_id=owner_id,
            task_id=task_id,
            question=record.question_content,
            attachments=[],
        )

        if new_answer:
            updated = self._repository.update_answer_by_owner_task_and_qa_id(
                owner_id, task_id, qa_id, new_answer,
                cited_sources=cited_sources,
            )
            if updated:
                return self._to_view(updated)
        return self._to_view(record)

    def delete_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        qa_id: str,
    ) -> bool:
        """删除单条问答记录"""
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return False
        return self._repository.delete_by_owner_task_and_qa_id(owner_id, task_id, qa_id)

    # ── 流式接口 ──────────────────────────────────────────────────────────

    def stream_rag_for_video(
        self,
        *,
        owner_id: str,
        task_id: str,
        question_content: str,
        attachments: list[AttachmentInfo],
    ) -> Iterator[str | AgentProgressEvent]:
        """返回真实 LLM token 流及进度事件。

        cited_sources 可通过 ``agent.last_cited_sources`` 在流结束后获取。
        """
        if self._agent is None:
            # Fallback: use old RagAgentService path
            _, token_gen = self._rag_agent_service.stream_video_question(
                owner_id=owner_id,
                task_id=task_id,
                question_content=question_content,
                attachments=[asdict(a) for a in attachments],
            )
            yield from token_gen
            return

        attachments_data = [asdict(a) for a in attachments]
        token_gen = self._agent.answer_stream_with_context(
            question=question_content,
            chat_id=task_id,
            owner_id=owner_id,
            mode="rag",
            attachments=attachments_data,
        )
        accumulated: list[str] = []
        for item in token_gen:
            if isinstance(item, str):
                accumulated.append(item)
            yield item

        # Record turn in conversation memory
        full_answer = "".join(accumulated)
        if self._chat_memory and full_answer:
            try:
                self._chat_memory.add_turn(
                    chat_id=task_id,
                    question=question_content,
                    answer=full_answer,
                )
            except Exception:
                logger.warning(
                    "stream_rag_for_video: memory.add_turn failed for task_id=%s",
                    task_id,
                )

    def stream_time_travel_for_video(
        self,
        *,
        owner_id: str,
        task_id: str,
        question_content: str,
        attachments: list[AttachmentInfo],
        timestamp: str,
        window_seconds: int,
    ) -> Iterator[str | AgentProgressEvent]:
        """时间旅行流式问答。

        cited_sources 可通过 ``agent.last_cited_sources`` 在流结束后获取。
        """
        if self._agent is None:
            raise ServiceError(code=ErrorCode.QA_AGENT_NOT_CONFIGURED, message="VideoQAAgent is not configured")

        attachments_data = [asdict(a) for a in attachments]
        token_gen = self._agent.answer_stream_with_context(
            question=question_content,
            chat_id=task_id,
            owner_id=owner_id,
            mode="timestamp",
            attachments=attachments_data,
            timestamp=timestamp,
            window_seconds=window_seconds,
        )
        accumulated: list[str] = []
        for item in token_gen:
            if isinstance(item, str):
                accumulated.append(item)
            yield item

        # Record turn in conversation memory
        full_answer = "".join(accumulated)
        if self._chat_memory and full_answer:
            try:
                self._chat_memory.add_turn(
                    chat_id=task_id,
                    question=question_content,
                    answer=full_answer,
                )
            except Exception:
                logger.warning(
                    "stream_time_travel_for_video: memory.add_turn failed for task_id=%s",
                    task_id,
                )

    # ── 时间旅行持久化 ─────────────────────────────────────────────────────

    def create_time_travel_qa_record(
        self,
        *,
        owner_id: str,
        task_id: str,
        timestamp: str,
        question_content: str,
        answer_content: str = "",
        attachments: list[AttachmentInfo],
        window_seconds: int | None,
    ) -> VideoQARecordView | None:
        """持久化时间旅行或无时间窗 RAG 问答记录。answer_content 为空时跳过写入。"""
        task = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return None

        if window_seconds is None:
            start_time, end_time = timestamp, timestamp
        else:
            start_time, end_time = self._compute_time_window(timestamp, window_seconds)
        record = self._repository.create(
            owner_id=owner_id,
            task_id=task_id,
            start_time=start_time,
            end_time=end_time,
            question_content=question_content,
            attachments=[asdict(a) for a in attachments],
        )
        if answer_content:
            updated = self._repository.update_answer_by_owner_task_and_qa_id(
                owner_id, task_id, record.qa_id, answer_content
            )
            if updated is None:
                return None
            return self._to_view(updated)
        return self._to_view(record)

    def finalize_time_travel_qa_answer(
        self,
        *,
        owner_id: str,
        task_id: str,
        qa_id: str,
        answer_content: str,
        cited_sources: list[dict] | None = None,
    ) -> None:
        """流式结束后将完整答案和引用溯源写回数据库。"""
        self._repository.update_answer_by_owner_task_and_qa_id(
            owner_id, task_id, qa_id, answer_content,
            cited_sources=cited_sources,
        )

    # ── 内部 ───────────────────────────────────────────────────────────────

    def _generate_answer(
        self,
        *,
        owner_id: str,
        task_id: str,
        question: str,
        attachments: list[dict],
    ) -> tuple[str, list[dict]]:
        """Generate answer and return (answer_text, cited_sources)."""
        if self._agent is not None:
            try:
                answer = self._agent.answer(
                    question=question,
                    chat_id=task_id,
                    kbid="",
                    owner_id=owner_id,
                )
                cited_sources = getattr(self._agent, "last_cited_sources", [])
                # Record turn in memory
                if self._chat_memory and answer:
                    try:
                        self._chat_memory.add_turn(
                            chat_id=task_id,
                            question=question,
                            answer=answer,
                        )
                    except Exception:
                        logger.warning(
                            "_generate_answer: memory.add_turn failed for task_id=%s",
                            task_id,
                        )
                return answer, cited_sources
            except Exception:
                logger.exception(
                    "_generate_answer: agent failed for task_id=%s", task_id
                )
                return "", []

        # Fallback: use old RagAgentService path
        try:
            cited_sources, token_gen = self._rag_agent_service.stream_video_question(
                owner_id=owner_id,
                task_id=task_id,
                question_content=question,
                attachments=attachments,
            )
            answer = "".join(token_gen)
            return answer, cited_sources
        except Exception:
            logger.exception(
                "_generate_answer: RAG fallback failed for task_id=%s", task_id
            )
            return "", []

    # ── 视图转换 ───────────────────────────────────────────────────────────

    @staticmethod
    def _to_view(record) -> VideoQARecordView:
        """将 Repository 记录转换为视图"""
        from backend.infrastructure.storage.oss_client import get_object_storage_client
        from backend.schemas.global_chat import CitedSource
        storage = get_object_storage_client()
        attachments = []
        try:
            if record.attachments:
                attachments_data = json.loads(record.attachments)
                for a in attachments_data:
                    att = AttachmentInfo.model_validate(a)
                    try:
                        att = att.model_copy(update={"presigned_url": storage.get_presigned_url(object_key=att.oss_key)})
                    except Exception:
                        pass
                    attachments.append(att)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        cited_sources = []
        try:
            if record.cited_sources:
                cited_sources_data = json.loads(record.cited_sources)
                cited_sources = [CitedSource(**s) for s in cited_sources_data]
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
            cited_sources=cited_sources,
            question_time=record.question_time,
        )

    @staticmethod
    def _compute_time_window(timestamp: str, window_seconds: int) -> tuple[str, str]:
        hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
        start_seconds = hours * 3600 + minutes * 60 + seconds
        end_seconds = start_seconds + window_seconds
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
