from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import get_global_qa_service
from backend.schemas.common import MetaInfo
from backend.schemas.global_qa import (
    GlobalQARecordCreateRequest,
    GlobalQARecordDeleteData,
    GlobalQARecordDeleteResponse,
    GlobalQARecordListResponse,
    GlobalQARecordResponse,
    GlobalQARecordUpdateRequest,
)
from backend.services.global_qa_service import GlobalQAService

router = APIRouter(prefix="/api/v1/kbs", tags=["global-qa"])

_QA_ALLOWED_FIELDS = {
    "qa_id",
    "chat_id",
    "question_content",
    "answer_content",
    "attachments",
    "cited_sources",
    "question_time",
}


def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


@router.post(
    "/{kbid}/chats/{chat_id}/qa",
    response_model=GlobalQARecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_global_qa_record(
    kbid: str,
    chat_id: str,
    payload: GlobalQARecordCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    qa_service: GlobalQAService = Depends(get_global_qa_service),
):
    _ = kbid
    record = qa_service.create_qa_record(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        payload=payload,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )
    return GlobalQARecordResponse(data=record, meta=_build_meta(request))


@router.get(
    "/{kbid}/chats/{chat_id}/qa",
    response_model=GlobalQARecordListResponse,
)
async def list_global_qa_records(
    kbid: str,
    chat_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    qa_service: GlobalQAService = Depends(get_global_qa_service),
):
    _ = kbid, sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _QA_ALLOWED_FIELDS)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    items, pagination = qa_service.list_qa_records(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        page=page,
        page_size=page_size,
    )
    return GlobalQARecordListResponse(
        data=items,
        pagination=pagination,
        meta=_build_meta(request),
    )


@router.get(
    "/{kbid}/chats/{chat_id}/qa/{qa_id}",
    response_model=GlobalQARecordResponse,
)
async def get_global_qa_record(
    kbid: str,
    chat_id: str,
    qa_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    qa_service: GlobalQAService = Depends(get_global_qa_service),
):
    _ = kbid
    record = qa_service.get_qa_record(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        qa_id=qa_id,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return GlobalQARecordResponse(data=record, meta=_build_meta(request))


@router.patch(
    "/{kbid}/chats/{chat_id}/qa/{qa_id}",
    response_model=GlobalQARecordResponse,
)
async def update_global_qa_record(
    kbid: str,
    chat_id: str,
    qa_id: str,
    payload: GlobalQARecordUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    qa_service: GlobalQAService = Depends(get_global_qa_service),
):
    _ = kbid
    record = qa_service.update_qa_record(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        qa_id=qa_id,
        payload=payload,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return GlobalQARecordResponse(data=record, meta=_build_meta(request))


@router.delete(
    "/{kbid}/chats/{chat_id}/qa/{qa_id}",
    response_model=GlobalQARecordDeleteResponse,
)
async def delete_global_qa_record(
    kbid: str,
    chat_id: str,
    qa_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    qa_service: GlobalQAService = Depends(get_global_qa_service),
):
    _ = kbid
    success = qa_service.delete_qa_record(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        qa_id=qa_id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA record not found",
        )
    return GlobalQARecordDeleteResponse(
        data=GlobalQARecordDeleteData(qa_id=qa_id),
        meta=_build_meta(request),
    )
