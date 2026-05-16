"""
TUS 兼容的大文件分片上传路由。

端点：
- POST   /api/v1/uploads                 — 初始化上传会话
- HEAD   /api/v1/uploads/{upload_id}      — 查询上传进度（TUS 兼容）
- PATCH  /api/v1/uploads/{upload_id}      — 上传分片（TUS 兼容）
- DELETE /api/v1/uploads/{upload_id}      — 取消上传

TUS 协议核心 header：
- Tus-Resumable: 1.0.0
- Upload-Length: <total_size>
- Upload-Offset: <current_offset>
- Content-Type: application/offset+octet-stream（PATCH 时）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response

from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.schemas.upload import (
    ChunkStatusResponse,
    InitUploadRequest,
    InitUploadResponse,
)

router = APIRouter(prefix="/api/v1/uploads", tags=["file_upload"])

_TUS_VERSION = "1.0.0"


def _get_upload_service():
    import redis as redis_lib

    from backend.repositories.upload_repository import UploadRepository
    from backend.services.upload_service import UploadService

    redis_client = redis_lib.Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    return UploadService(UploadRepository(redis_client))


@router.post("", status_code=status.HTTP_201_CREATED)
async def initiate_upload(
    payload: InitUploadRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
) -> InitUploadResponse:
    """初始化 TUS 上传会话。"""
    service = _get_upload_service()
    return service.initiate_upload(owner_id=current_user.user_id, payload=payload)


@router.head("/{upload_id}")
async def get_upload_status(
    upload_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
) -> Response:
    """TUS 兼容：查询上传进度。

    返回标准 TUS headers：
    - Tus-Resumable: 1.0.0
    - Upload-Offset: <已上传字节数>
    - Upload-Length: <总字节数>
    """
    service = _get_upload_service()
    status_info = service.get_upload_status(upload_id=upload_id, owner_id=current_user.user_id)

    if status_info is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    headers = {
        "Tus-Resumable": _TUS_VERSION,
        "Upload-Offset": str(status_info.uploaded_size),
        "Upload-Length": str(status_info.total_size),
        "Cache-Control": "no-store",
    }
    return Response(status_code=204, headers=headers)


@router.patch("/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    upload_offset: int = Header(..., alias="Upload-Offset"),
    content_type: str = Header(default="application/offset+octet-stream"),
) -> Response:
    """TUS 兼容：上传单个分片。

    客户端必须提供：
    - Upload-Offset header（当前分片的字节偏移量）
    - Content-Type: application/offset+octet-stream
    - Body：分片二进制数据
    """
    service = _get_upload_service()

    # 验证 TUS 协议版本
    tus_version = request.headers.get("Tus-Resumable", "")
    if tus_version and tus_version != _TUS_VERSION:
        raise HTTPException(
            status_code=412,
            detail=f"Unsupported Tus-Resumable version: {tus_version}",
        )

    # 读取分片数据
    chunk_data = await request.body()
    if not chunk_data:
        raise HTTPException(status_code=400, detail="Empty chunk body")

    # 计算分片索引
    chunk_size = 10 * 1024 * 1024  # SERVER_CHUNK_SIZE
    chunk_index = upload_offset // chunk_size

    try:
        state = service.upload_chunk(
            upload_id=upload_id,
            owner_id=current_user.user_id,
            chunk_index=chunk_index,
            data=chunk_data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 返回 TUS 兼容响应
    headers = {
        "Tus-Resumable": _TUS_VERSION,
        "Upload-Offset": str(state.uploaded_size),
    }
    new_offset = state.uploaded_size

    if state.is_complete:
        # 全部上传完成，返回 200 OK（TUS 规范：全部完成用 200 而非 204）
        return Response(
            status_code=200,
            headers={**headers, "Upload-Offset": str(new_offset)},
        )

    return Response(
        status_code=204,
        headers={**headers, "Upload-Offset": str(new_offset)},
    )


@router.delete("/{upload_id}")
async def cancel_upload(
    upload_id: str,
    current_user: UserView = Depends(get_current_user),
) -> dict:
    """取消上传会话并清理已上传的分片文件。"""
    service = _get_upload_service()
    result = service.cancel_upload(upload_id=upload_id, owner_id=current_user.user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return result.model_dump()


@router.get("/{upload_id}")
async def get_upload_info(
    upload_id: str,
    current_user: UserView = Depends(get_current_user),
) -> ChunkStatusResponse:
    """查询上传进度（JSON 格式，非 TUS 标准，方便前端轮询）。"""
    service = _get_upload_service()
    status_info = service.get_upload_status(upload_id=upload_id, owner_id=current_user.user_id)
    if status_info is None:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return status_info
