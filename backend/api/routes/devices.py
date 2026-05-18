"""
设备注册路由（FCM Token 管理）。

端点：
- POST /api/v1/devices：注册/更新设备 FCM token
- DELETE /api/v1/devices/{device_token_id}：取消注册设备
- GET /api/v1/devices：列出当前用户已注册设备

边界约束（对齐计划）：
- 设备注册接口必须与用户身份绑定。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.dependencies import get_current_user
from backend.dependencies import get_device_service
from backend.auth.models import UserView
from backend.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
)
from backend.services.device_service import DeviceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    current_user: UserView = Depends(get_current_user),
    device_service: DeviceService = Depends(get_device_service),
):
    """注册或更新设备的 FCM 推送令牌。

    幂等：同一 device_token 重复注册时更新 platform/app_version/device_id。
    """
    try:
        return device_service.register_or_update_device(
            owner_id=current_user.user_id,
            payload=payload,
        )
    except Exception as exc:
        logger.exception("Failed to register device for user=%s", current_user.user_id)
        raise HTTPException(status_code=500, detail="Failed to register device") from exc


@router.delete("/{device_token_id}")
async def unregister_device(
    device_token_id: str,
    current_user: UserView = Depends(get_current_user),
    device_service: DeviceService = Depends(get_device_service),
):
    """取消注册设备（删除 FCM token）。

    仅允许删除自己名下的设备。
    """
    try:
        device_service.unregister_device(
            owner_id=current_user.user_id,
            device_token_id=device_token_id,
        )
        return {"status": "success", "message": "Device unregistered"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to unregister device id=%s", device_token_id)
        raise HTTPException(status_code=500, detail="Failed to unregister device") from exc


@router.get("")
async def list_devices(
    current_user: UserView = Depends(get_current_user),
    device_service: DeviceService = Depends(get_device_service),
):
    """列出当前用户所有已注册设备。"""
    try:
        return {
            "status": "success",
            "data": device_service.list_devices_by_owner(owner_id=current_user.user_id),
        }
    except Exception as exc:
        logger.exception("Failed to list devices for user=%s", current_user.user_id)
        raise HTTPException(status_code=500, detail="Failed to list devices") from exc
