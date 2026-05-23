"""附件上传路由。

端点：
- POST /api/v1/attachments/upload  — 上传单个图片附件，返回 oss_key 和预签名 URL

当前仅接受图片类型（JPEG / PNG / GIF / WEBP），因为 LLM 多模态路径只处理图片附件。
其他文件类型（PDF、文档等）将在后续版本支持。
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.infrastructure.storage.oss_client import get_object_storage_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/attachments", tags=["attachments"])

# 允许的 MIME 类型（目前仅支持图片多模态）
_ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
})
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

_MIME_TO_SUFFIX: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


# ── Response Schemas ──────────────────────────────────────────────────────────

class AttachmentUploadData(BaseModel):
    """上传成功后的附件元数据，含临时访问 URL。"""
    name: str
    oss_key: str
    mime_type: str
    size_bytes: int
    presigned_url: str | None = None


class AttachmentUploadResponse(BaseModel):
    status: str = "success"
    data: AttachmentUploadData


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentUploadResponse,
    summary="上传图片附件",
    description=(
        "将图片文件上传至对象存储，返回 `oss_key` 与预签名访问 URL。\n\n"
        "返回的 `oss_key` 应在后续的 QA 提问请求的 `attachments` 字段中使用。"
    ),
)
async def upload_attachment(
    file: UploadFile,
    current_user: UserView = Depends(get_current_user),
) -> AttachmentUploadResponse:
    """上传图片附件到对象存储，返回 oss_key 和预签名访问 URL。"""
    mime_type = (file.content_type or "").lower().split(";")[0].strip()
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"不支持的文件类型：{mime_type}。"
                "目前仅接受图片文件（JPEG / PNG / GIF / WEBP）。"
            ),
        )

    data = await file.read()
    size_bytes = len(data)

    if size_bytes == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件内容为空。")

    if size_bytes > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过大小限制（{size_bytes} bytes > {_MAX_SIZE_BYTES} bytes）。",
        )

    original_name = Path(file.filename or "attachment").name
    suffix = Path(original_name).suffix or _MIME_TO_SUFFIX.get(mime_type, ".bin")
    oss_key = f"attachments/{current_user.user_id}/{uuid.uuid4().hex}{suffix}"

    storage = get_object_storage_client()

    # 写临时文件 → 上传 OSS → 删除临时文件
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        stored_key = storage.upload_file(local_path=tmp_path, object_key=oss_key)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    presigned_url = storage.get_presigned_url(object_key=stored_key)
    logger.info(
        "attachment uploaded: owner=%s key=%s mime=%s size=%d",
        current_user.user_id, stored_key, mime_type, size_bytes,
    )

    return AttachmentUploadResponse(
        data=AttachmentUploadData(
            name=original_name,
            oss_key=stored_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            presigned_url=presigned_url,
        )
    )
