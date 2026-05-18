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
from backend.repositories.device_repository import DeviceRepository
from backend.auth.models import UserView
from backend.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceUnregisterRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


def _get_device_repository():
    """获取 DeviceRepository 实例（每次请求创建新 session）。"""
    from backend.db.session import SessionLocal

    return DeviceRepository(db_session=SessionLocal())


@router.post("", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    current_user: UserView = Depends(get_current_user),
):
    """注册或更新设备的 FCM 推送令牌。

    幂等：同一 device_token 重复注册时更新 platform/app_version/device_id。
    """
    repo = _get_device_repository()
    try:
        # upsert by device_token
        existing = repo.get_by_token(payload.device_token)
        if existing:
            record = repo.update(
                device_token_id=existing.device_token_id,
                user_id=current_user.user_id,
                platform=payload.platform.value,
                app_version=payload.app_version,
                device_id=payload.device_id,
            )
        else:
            record = repo.create(
                user_id=current_user.user_id,
                device_token=payload.device_token,
                platform=payload.platform.value,
                app_version=payload.app_version,
                device_id=payload.device_id,
            )
        return DeviceRegisterResponse(
            device_token_id=record.device_token_id,
            platform=payload.platform,
            device_id=record.device_id,
            registered_at=record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except Exception as exc:
        logger.exception("Failed to register device for user=%s", current_user.user_id)
        raise HTTPException(status_code=500, detail="Failed to register device") from exc
    finally:
        repo._session.close()


@router.delete("/{device_token_id}")
async def unregister_device(
    device_token_id: str,
    current_user: UserView = Depends(get_current_user),
):
    """取消注册设备（删除 FCM token）。

    仅允许删除自己名下的设备。
    """
    repo = _get_device_repository()
    try:
        existing = repo.get_by_id(device_token_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Device not found")
        if existing.user_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Cannot unregister another user's device")
        repo.delete(device_token_id)
        return {"status": "success", "message": "Device unregistered"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to unregister device id=%s", device_token_id)
        raise HTTPException(status_code=500, detail="Failed to unregister device") from exc
    finally:
        repo._session.close()


@router.get("")
async def list_devices(
    current_user: UserView = Depends(get_current_user),
):
    """列出当前用户所有已注册设备。"""
    repo = _get_device_repository()
    try:
        records = repo.list_by_user(current_user.user_id)
        return {
            "status": "success",
            "data": [
                {
                    "device_token_id": r.device_token_id,
                    "platform": r.platform,
                    "device_id": r.device_id,
                    "app_version": r.app_version,
                    "registered_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for r in records
            ],
        }
    except Exception as exc:
        logger.exception("Failed to list devices for user=%s", current_user.user_id)
        raise HTTPException(status_code=500, detail="Failed to list devices") from exc
    finally:
        repo._session.close()
