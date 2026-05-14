from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator


T = TypeVar("T")


class MetaInfo(BaseModel):
    request_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


class PaginationInfo(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    total: int = Field(default=0, ge=0)
    has_next: bool = False
    next_cursor: str | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    is_retryable: bool = False
    retry_after: int | None = Field(default=None, ge=0)


class SuccessResponse(BaseModel, Generic[T]):
    status: str = "success"
    data: T
    pagination: PaginationInfo | None = None
    meta: MetaInfo


class ErrorResponse(BaseModel):
    status: str = "error"
    data: None = None
    error: ErrorInfo
    meta: MetaInfo


class ListQueryParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    fields: str | None = None
    sort: str | None = None
    cursor: str | None = None
    include_content: bool = False

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: str | None) -> str | None:
        """验证并清理 fields 字段的值。

        该验证器用于处理以逗号分隔的字符串字段，去除每个部分的前后空白，
        并过滤掉空字符串。如果处理后没有有效内容，则返回 None。

        Args:
            cls: 类本身，由 classmethod 装饰器自动传入。
            value: 待验证的原始字符串值，可能为 None。

        Returns:
            清理后的逗号分隔字符串，如果输入为 None 或处理后无有效内容则返回 None。
        """
        if value is None:
            return value
        parts = [item.strip() for item in value.split(",") if item.strip()]
        return ",".join(parts) if parts else None
