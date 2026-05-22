from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import (
    get_video_qa_service,
    get_video_summary_task_service,
    get_workflow_orchestration_service,
)
from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.video_qa import (
    TimeTravelQAStreamRequest,
    VideoQARecordCreateRequest,
    VideoQARecordDeleteData,
    VideoQARecordDeleteResponse,
    VideoQARecordListResponse,
    VideoQARecordResponse,
    VideoQARecordUpdateRequest,
)
from backend.services.video_summary_task_service import VideoSummaryTaskService
from backend.services.video_qa_service import VideoQAService
from backend.services.workflow_orchestration_service import WorkflowOrchestrationService


router = APIRouter(prefix="/api/v1/tasks", tags=["video-qa"])
_ALLOWED_FIELDS = {
    "qa_id",
    "task_id",
    "start_time",
    "end_time",
    "question_content",
    "answer_content",
    "attachments",
    "question_time",
}


def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


def _chunk_text(text: str, chunk_size: int = 64) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/{task_id}/qa", response_model=VideoQARecordResponse, status_code=status.HTTP_201_CREATED)
async def create_video_qa(
    task_id: str,
    payload: VideoQARecordCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    service: VideoQAService = Depends(get_video_qa_service),
):
    """创建单视频局部追问"""
    # 检查 task_id 参数一致性
    if task_id != payload.task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="task_id in path and payload must match",
        )

    record = service.create_qa_record(
        owner_id=current_user.user_id,
        task_id=task_id,
        payload=payload,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    return VideoQARecordResponse(data=record, meta=_build_meta(request))


@router.get("/{task_id}/qa", response_model=VideoQARecordListResponse)
async def list_video_qa(
    task_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    service: VideoQAService = Depends(get_video_qa_service),
):
    """查询任务下的问答列表"""
    _ = sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _ALLOWED_FIELDS)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    items, pagination = service.list_qa_records(
        owner_id=current_user.user_id,
        task_id=task_id,
        page=page,
        page_size=page_size,
    )
    return VideoQARecordListResponse(data=items, pagination=pagination, meta=_build_meta(request))


@router.get("/{task_id}/qa/{qa_id}", response_model=VideoQARecordResponse)
async def get_video_qa(
    task_id: str,
    qa_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    service: VideoQAService = Depends(get_video_qa_service),
):
    """获取单条问答记录"""
    record = service.get_qa_record(
        owner_id=current_user.user_id,
        task_id=task_id,
        qa_id=qa_id,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return VideoQARecordResponse(data=record, meta=_build_meta(request))


@router.patch("/{task_id}/qa/{qa_id}", response_model=VideoQARecordResponse)
async def update_video_qa(
    task_id: str,
    qa_id: str,
    payload: VideoQARecordUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    service: VideoQAService = Depends(get_video_qa_service),
):
    """更新问答记录（重新生成回答）"""
    record = service.update_qa_record(
        owner_id=current_user.user_id,
        task_id=task_id,
        qa_id=qa_id,
        payload=payload,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return VideoQARecordResponse(data=record, meta=_build_meta(request))


@router.delete("/{task_id}/qa/{qa_id}", response_model=VideoQARecordDeleteResponse)
async def delete_video_qa(
    task_id: str,
    qa_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    service: VideoQAService = Depends(get_video_qa_service),
):
    """删除单条问答记录"""
    success = service.delete_qa_record(
        owner_id=current_user.user_id,
        task_id=task_id,
        qa_id=qa_id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return VideoQARecordDeleteResponse(
        data=VideoQARecordDeleteData(qa_id=qa_id),
        meta=_build_meta(request),
    )


@router.post("/{task_id}/time-travel-qa/stream", status_code=status.HTTP_200_OK)
async def time_travel_qa_stream(
    task_id: str,
    payload: TimeTravelQAStreamRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
    workflow_service: WorkflowOrchestrationService = Depends(get_workflow_orchestration_service),
    service: VideoQAService = Depends(get_video_qa_service),
):
    task = task_service.get_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")

    if task.workflow_state not in ("WAITING_USER_APPROVAL", "FINAL_GENERATING", "COMPLETED"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Task must have completed analysis phase to support time travel Q&A",
        )

    trace_id = str(getattr(request.state, "trace_id", ""))
    try:
        if payload.window_seconds is None:
            answer, output_chunks = service.answer_without_window_via_rag(
                owner_id=current_user.user_id,
                task_id=task_id,
                question_content=payload.question_content,
                attachments=payload.attachments,
            )
        else:
            answer = await workflow_service.start_time_travel_qa_async(
                owner_id=current_user.user_id,
                task_id=task_id,
                timestamp=payload.timestamp,
                question=payload.question_content,
                window_seconds=payload.window_seconds,
                trace_id=trace_id,
            )
            output_chunks = _chunk_text(answer)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Time travel Q&A failed: {str(exc)}",
        ) from exc

    qa_record = service.create_time_travel_qa_record(
        owner_id=current_user.user_id,
        task_id=task_id,
        timestamp=payload.timestamp,
        question_content=payload.question_content,
        answer_content=answer,
        attachments=payload.attachments,
        window_seconds=payload.window_seconds,
    )
    if qa_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")

    produced_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _event_iter() -> Iterator[str]:
        try:
            yield _sse_event(
                "start",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "timestamp": produced_at,
                },
            )
            for seq, chunk in enumerate(output_chunks, start=1):
                yield _sse_event(
                    "delta",
                    {
                        "task_id": task_id,
                        "qa_id": qa_record.qa_id,
                        "chunk": chunk,
                        "sequence": seq,
                        "timestamp": produced_at,
                    },
                )
            yield _sse_event(
                "done",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "answer_content": answer,
                    "timestamp": produced_at,
                },
            )
        except Exception as exc:
            yield _sse_event(
                "error",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "message": str(exc),
                    "timestamp": produced_at,
                },
            )

    return StreamingResponse(
        _event_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
