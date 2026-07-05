from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import get_global_chat_service
from backend.schemas.common import MetaInfo
from backend.schemas.global_chat import (
    GlobalChatSessionCreateRequest,
    GlobalChatSessionDeleteData,
    GlobalChatSessionDeleteResponse,
    GlobalChatSessionListResponse,
    GlobalChatSessionResponse,
    GlobalChatSessionUpdateRequest,
)
from backend.exceptions import ErrorCode, NotFoundError, ValidationError
from backend.services.global_chat_service import GlobalChatService


router = APIRouter(prefix="/api/v1/kbs", tags=["global-chat"])

_CHAT_ALLOWED_FIELDS = {
    "chat_id",
    "kbid",
    "chat_title",
    "created_at",
}

def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


# ============================================
# Global Chat Session Endpoints
# ============================================


@router.post(
    "/{kbid}/chats",
    response_model=GlobalChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_global_chat_session(
    kbid: str,
    payload: GlobalChatSessionCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    chat_service: GlobalChatService = Depends(get_global_chat_service),
):
    """创建新的全局知识库会话"""
    # 检查路径 kbid 与请求体一致
    if kbid != payload.kbid:
        raise ValidationError(code=ErrorCode.REQUEST_INVALID_QUERY_PARAM, message="kbid in path and payload must match")

    session = chat_service.create_chat_session(
        owner_id=current_user.user_id,
        payload=payload,
    )
    if session is None:
        raise NotFoundError(code=ErrorCode.KB_NOT_FOUND, message="Knowledge base not found")
    return GlobalChatSessionResponse(data=session, meta=_build_meta(request))


@router.get(
    "/{kbid}/chats",
    response_model=GlobalChatSessionListResponse,
)
async def list_global_chat_sessions(
    kbid: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    chat_service: GlobalChatService = Depends(get_global_chat_service),
):
    """查询知识库下的所有会话"""
    _ = sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _CHAT_ALLOWED_FIELDS)
        except ValueError as exc:
            raise ValidationError(code=ErrorCode.REQUEST_UNSUPPORTED_FIELDS, message=str(exc)) from exc

    items, pagination = chat_service.list_chat_sessions(
        owner_id=current_user.user_id,
        kbid=kbid,
        page=page,
        page_size=page_size,
    )
    return GlobalChatSessionListResponse(
        data=items,
        pagination=pagination,
        meta=_build_meta(request),
    )


@router.get(
    "/{kbid}/chats/{chat_id}",
    response_model=GlobalChatSessionResponse,
)
async def get_global_chat_session(
    kbid: str,
    chat_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    chat_service: GlobalChatService = Depends(get_global_chat_service),
):
    """获取单个会话"""
    session = chat_service.get_chat_session(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
    )
    if session is None:
        raise NotFoundError(code=ErrorCode.CHAT_SESSION_NOT_FOUND, message="Chat session not found")
    return GlobalChatSessionResponse(data=session, meta=_build_meta(request))


@router.patch(
    "/{kbid}/chats/{chat_id}",
    response_model=GlobalChatSessionResponse,
)
async def update_global_chat_session(
    kbid: str,
    chat_id: str,
    payload: GlobalChatSessionUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    chat_service: GlobalChatService = Depends(get_global_chat_service),
):
    """更新会话标题"""
    session = chat_service.update_chat_session(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
        payload=payload,
    )
    if session is None:
        raise NotFoundError(code=ErrorCode.CHAT_SESSION_NOT_FOUND, message="Chat session not found")
    return GlobalChatSessionResponse(data=session, meta=_build_meta(request))


@router.delete(
    "/{kbid}/chats/{chat_id}",
    response_model=GlobalChatSessionDeleteResponse,
)
async def delete_global_chat_session(
    kbid: str,
    chat_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    chat_service: GlobalChatService = Depends(get_global_chat_service),
):
    """删除会话及其所有问答"""
    success = chat_service.delete_chat_session(
        owner_id=current_user.user_id,
        kbid=kbid,
        chat_id=chat_id,
    )
    if not success:
        raise NotFoundError(code=ErrorCode.CHAT_SESSION_NOT_FOUND, message="Chat session not found")
    return GlobalChatSessionDeleteResponse(
        data=GlobalChatSessionDeleteData(chat_id=chat_id),
        meta=_build_meta(request),
    )
