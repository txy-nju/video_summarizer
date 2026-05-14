from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.api.filters import parse_fields
from backend.dependencies import get_kb_service
from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.kb import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDeleteData,
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from backend.services.kb_service import KnowledgeBaseService

router = APIRouter(prefix="/api/v1/kbs", tags=["knowledge-bases"])
_ALLOWED_FIELDS = {
    "kbid",
    "owner_id",
    "name",
    "category",
    "description",
    "vector_collection_name",
    "config",
    "created_at",
}


def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    kb = kb_service.create_knowledge_base(owner_id=current_user.user_id, payload=payload)
    return KnowledgeBaseResponse(data=kb, meta=_build_meta(request))


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    _ = sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _ALLOWED_FIELDS)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    items, pagination = kb_service.list_knowledge_bases(owner_id=current_user.user_id, page=page, page_size=page_size)
    return KnowledgeBaseListResponse(data=items, pagination=PaginationInfo.model_validate(pagination), meta=_build_meta(request))


@router.get("/{kbid}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kbid: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    kb = kb_service.get_knowledge_base(owner_id=current_user.user_id, kbid=kbid)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return KnowledgeBaseResponse(data=kb, meta=_build_meta(request))


@router.patch("/{kbid}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kbid: str,
    payload: KnowledgeBaseUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    kb = kb_service.update_knowledge_base(owner_id=current_user.user_id, kbid=kbid, payload=payload)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return KnowledgeBaseResponse(data=kb, meta=_build_meta(request))


@router.delete("/{kbid}", response_model=KnowledgeBaseDeleteResponse)
async def delete_knowledge_base(
    kbid: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
):
    deleted = kb_service.delete_knowledge_base(owner_id=current_user.user_id, kbid=kbid)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return KnowledgeBaseDeleteResponse(data=KnowledgeBaseDeleteData(kbid=kbid), meta=_build_meta(request))
