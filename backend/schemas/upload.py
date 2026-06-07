"""
大文件分片上传 DTO（TUS 协议兼容）。

契约：
- POST /api/v1/uploads：初始化上传会话 → InitUploadResponse
- HEAD /api/v1/uploads/{upload_id}：查询上传进度 → ChunkStatusResponse
- PATCH /api/v1/uploads/{upload_id}：上传分片（二进制 body + Upload-Offset header）
- DELETE /api/v1/uploads/{upload_id}：取消上传
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from backend.schemas.video_format import ALLOWED_VIDEO_EXTENSIONS, validate_video_extension


# 服务端强制分片大小：10 MiB
SERVER_CHUNK_SIZE: int = 10 * 1024 * 1024  # 10,485,760 bytes


class InitUploadRequest(BaseModel):
    """客户端发起上传时提供的元数据。

    约束：
    - file_name：原始文件名，用于展示与默认 OSS 键生成。
    - total_size：文件总字节数（客户端按实际文件大小上报）。
    """

    file_name: str = Field(..., min_length=1, max_length=512)
    total_size: int = Field(..., gt=0, le=10 * 1024 * 1024 * 1024)  # 最大 10GB
    video_id: str | None = Field(default=None, min_length=1, max_length=36)  # 可选：关联的预注册 VideoResource ID

    @field_validator("file_name")
    @classmethod
    def validate_video_format(cls, v: str) -> str:
        """拒绝非视频格式的文件扩展名（第一道防线）。"""
        if not validate_video_extension(v):
            allowed = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_VIDEO_EXTENSIONS))
            raise ValueError(
                f"不支持的文件格式：{Path(v).suffix or '(无扩展名)'}。"
                f"支持的格式：{allowed}"
            )
        return v


class InitUploadResponse(BaseModel):
    """初始化上传会话的响应。

    字段对齐计划约定：
    - upload_id：会话唯一标识（UUIDv7）。
    - chunk_size：服务端强制的分片大小（字节）。
    - expires_at：会话过期时间（UTC ISO 8601）。
    """

    upload_id: str
    chunk_size: int = Field(default=SERVER_CHUNK_SIZE)
    expires_at: str


class ChunkStatusResponse(BaseModel):
    """分片上传进度查询响应。

    字段对齐计划约定：
    - upload_id：会话标识。
    - uploaded_size：已上传总字节数。
    - total_size：文件总字节数。
    - uploaded_chunks：已完成的分片索引列表。
    """

    upload_id: str
    uploaded_size: int
    total_size: int
    uploaded_chunks: list[int]


class UploadCancelResponse(BaseModel):
    """取消上传的确认响应。"""

    upload_id: str
    status: str = "cancelled"


# ---------------------------------------------------------------------------
# 内部数据类（不直接暴露给 HTTP 响应）
# ---------------------------------------------------------------------------

class UploadSessionState(BaseModel):
    """Redis 中存储的上传会话状态。

    字段：
    - upload_id：会话标识。
    - owner_id：属主用户 ID。
    - file_name：原始文件名。
    - total_size：文件总字节数。
    - chunk_size：服务端分片大小。
    - uploaded_chunks：已完成分片索引集合。
    - state：会话状态（created / uploading / uploading_complete / finalizing / done / failed / cancelled）。
    - expires_at：过期时间（UTC ISO 8601）。
    - created_at：创建时间（UTC ISO 8601）。
    """

    upload_id: str
    owner_id: str
    file_name: str
    total_size: int
    chunk_size: int = Field(default=SERVER_CHUNK_SIZE)
    uploaded_chunks: set[int] = Field(default_factory=set)
    state: str = "created"
    expires_at: str = ""
    created_at: str = ""
    video_id: str | None = None

    @property
    def uploaded_size(self) -> int:
        """计算已上传总字节数（最后一个分片可能不满 chunk_size）。"""
        if not self.uploaded_chunks:
            return 0
        max_chunk = max(self.uploaded_chunks)
        total = max_chunk * self.chunk_size
        # 最后一个分片的大小 = 剩余字节数
        remaining = self.total_size - total
        if remaining > 0:
            return total + min(remaining, self.chunk_size)
        return min(total, self.total_size)

    @property
    def total_chunks(self) -> int:
        """计算总分片数。"""
        return (self.total_size + self.chunk_size - 1) // self.chunk_size

    @property
    def is_complete(self) -> bool:
        """判断所有分片是否已上传完毕。"""
        return len(self.uploaded_chunks) >= self.total_chunks

    def chunk_offset(self, chunk_index: int) -> int:
        """计算指定分片的字节偏移量。"""
        return chunk_index * self.chunk_size

    def chunk_length(self, chunk_index: int) -> int:
        """计算指定分片的字节长度（最后一个分片可能不满）。"""
        offset = self.chunk_offset(chunk_index)
        remaining = self.total_size - offset
        return min(remaining, self.chunk_size)
