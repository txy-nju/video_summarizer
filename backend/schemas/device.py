"""
设备注册与 FCM token 管理的请求/响应 Schema。

对齐计划约定：
- 设备注册请求：device_token, platform, app_version, device_id
- 推送载荷：title, body, data (scope, scope_id, deep_link)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """设备平台枚举。"""

    ANDROID = "android"
    IOS = "ios"
    WEB = "web"


class DeviceRegisterRequest(BaseModel):
    """设备注册请求 DTO。

    对齐计划格式约定：
    {
        "device_token": "fcm_token_xxx",
        "platform": "android",
        "app_version": "1.0.0",
        "device_id": "android_001"
    }
    """

    device_token: str = Field(min_length=1, max_length=512, description="FCM 设备令牌")
    platform: Platform
    app_version: str = Field(default="", max_length=32, description="客户端版本号")
    device_id: str = Field(min_length=1, max_length=128, description="设备唯一标识")


class DeviceRegisterResponse(BaseModel):
    """设备注册响应 DTO。"""

    device_token_id: str = Field(description="内部设备记录主键")
    platform: Platform
    device_id: str
    registered_at: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        description="注册时间（ISO 8601 UTC）",
    )


class DeviceUnregisterRequest(BaseModel):
    """设备取消注册请求 DTO。"""

    device_token: str | None = None
    device_id: str | None = None


# ---------- FCM 推送载荷 ----------

class FCMPushData(BaseModel):
    """FCM data 载荷结构（对齐计划约定）。

    客户端根据 data.scope + data.scope_id 决定导航目标。
    """

    scope: str = Field(description="业务域：video_summary_task / video_qa / global_chat")
    scope_id: str = Field(description="业务实体主键")
    deep_link: str = Field(default="", description="客户端 deep link URI，例如 app://tasks/task_001")


class FCMPushPayload(BaseModel):
    """FCM 推送完整载荷（对齐计划约定）。

    示例：
    {
        "title": "分析完成",
        "body": "您的视频总结已生成",
        "data": {
            "scope": "video_summary_task",
            "scope_id": "task_001",
            "deep_link": "app://tasks/task_001"
        }
    }
    """

    title: str = Field(min_length=1, description="通知标题")
    body: str = Field(min_length=1, description="通知正文")
    data: FCMPushData


class FCMPushRequest(BaseModel):
    """后端服务层触发推送的请求 DTO。

    调用方（如进度事件监听器）构造此 DTO 传入 FCMService.send()。
    """

    user_id: str = Field(description="目标用户")
    title: str = Field(description="通知标题")
    body: str = Field(description="通知正文")
    data: FCMPushData
