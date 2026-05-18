"""
FCM (Firebase Cloud Messaging) 推送服务。

职责：
- 封装 Firebase Admin SDK，提供 send() 接口
- 从 device_tokens 表中查询用户已注册的设备令牌
- 推送不负责决定"何时通知"（由上层业务事件驱动）

边界约束（对齐计划）：
- 推送服务不负责决定"何时通知"，只接受明确业务事件。
- 推送载荷格式对齐 unified response format。
- 单次推送失败不阻塞主流程（best effort）。

使用前提：
- 设置环境变量 FCM_CREDENTIALS_PATH（指向 Firebase 服务账号 JSON 文件路径）
- 或通过 Firebase 应用默认凭据自动发现
"""

from __future__ import annotations

import logging
from typing import Any

from backend.schemas.device import FCMPushData, FCMPushPayload, FCMPushRequest

logger = logging.getLogger(__name__)

# 延迟导入 Firebase SDK，避免缺失依赖时阻塞应用启动
_firebase_messaging: Any = None


def _get_firebase_messaging():
    """延迟获取 Firebase Messaging 实例（lazy import）。"""
    global _firebase_messaging
    if _firebase_messaging is not None:
        return _firebase_messaging

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        # 尝试通过环境变量加载凭据
        import os

        creds_path = os.getenv("FCM_CREDENTIALS_PATH")
        if creds_path:
            cred = credentials.Certificate(creds_path)
        else:
            cred = None  # 依赖应用默认凭据

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)

        _firebase_messaging = messaging
        logger.info("FCM service initialized successfully")
        return _firebase_messaging

    except ImportError:
        logger.warning(
            "firebase-admin SDK not installed. FCM push will be no-op. "
            "Install with: pip install firebase-admin"
        )
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK")
    return None


class FCMService:
    """FCM 推送服务。

    接口约定（对齐计划）：
    - send(): 接收 FCMPushRequest DTO，查询用户设备令牌并发送推送
    - 推送失败仅记录日志，不向上传播异常（best effort）
    - 单用户多设备：依次推送每个已注册令牌
    """

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def send(self, request: FCMPushRequest) -> dict:
        """向指定用户的所有已注册设备发送 FCM 推送。

        Args:
            request: 推送请求 DTO（user_id + 推送内容）

        Returns:
            {"status": "sent", "device_count": N} 或 {"status": "skipped", "reason": "..."}
        """
        tokens = self._get_user_device_tokens(request.user_id)
        if not tokens:
            logger.info("FCM: no device tokens for user_id=%s", request.user_id)
            return {"status": "skipped", "reason": "no_device_tokens"}

        messaging = _get_firebase_messaging()
        if messaging is None:
            logger.warning("FCM: messaging unavailable, push skipped for user_id=%s", request.user_id)
            return {"status": "skipped", "reason": "firebase_unavailable"}

        # 批量发送：每个 token 一条消息
        sent_count = 0
        failed_count = 0
        for token in tokens:
            try:
                message = self._build_message(token, request)
                messaging.send(message)
                sent_count += 1
            except Exception:
                logger.exception("FCM: send failed for token=%s...", token[:20])
                failed_count += 1
                # 不中断主流程

        logger.info(
            "FCM: push sent=%d, failed=%d, user_id=%s",
            sent_count,
            failed_count,
            request.user_id,
        )
        return {"status": "sent", "device_count": sent_count, "failed_count": failed_count}

    def send_broadcast(self, title: str, body: str, data: FCMPushData | None = None) -> dict:
        """向所有已知设备广播推送（仅用于系统通知，慎用）。"""
        tokens = self._get_all_device_tokens()
        if not tokens:
            return {"status": "skipped", "reason": "no_devices"}

        messaging = _get_firebase_messaging()
        if messaging is None:
            return {"status": "skipped", "reason": "firebase_unavailable"}

        sent_count = 0
        for token in tokens:
            try:
                msg_kwargs = self._build_message_kwargs(token, title, body, data)
                message = messaging.Message(**msg_kwargs)
                messaging.send(message)
                sent_count += 1
            except Exception:
                logger.exception("FCM broadcast: send failed for token=%s...", token[:20])

        return {"status": "sent", "device_count": sent_count}

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_message(self, token: str, request: FCMPushRequest):
        """构造 Firebase Message 对象。"""
        messaging = _get_firebase_messaging()
        msg_kwargs = self._build_message_kwargs(token, request.title, request.body, request.data)
        return messaging.Message(**msg_kwargs)

    @staticmethod
    def _build_message_kwargs(token: str, title: str, body: str, data: FCMPushData | None) -> dict:
        """构造 Firebase Message 关键字参数。"""
        kwargs: dict = {
            "token": token,
            "notification": {"title": title, "body": body},
        }
        if data:
            kwargs["data"] = {
                "scope": data.scope,
                "scope_id": data.scope_id,
                "deep_link": data.deep_link,
            }
        return kwargs

    # ------------------------------------------------------------------
    # 数据库查询（延迟导入，避免循环依赖）
    # ------------------------------------------------------------------

    @staticmethod
    def _get_user_device_tokens(user_id: str) -> list[str]:
        """查询用户已注册的 FCM 设备令牌列表。"""
        try:
            from backend.db.session import SessionLocal
            from backend.models.database import DeviceToken

            db = SessionLocal()
            try:
                rows = (
                    db.query(DeviceToken.device_token)
                    .filter(DeviceToken.user_id == user_id)
                    .all()
                )
                return [row[0] for row in rows]
            finally:
                db.close()
        except Exception:
            logger.exception("FCM: failed to query device tokens for user_id=%s", user_id)
            return []

    @staticmethod
    def _get_all_device_tokens() -> list[str]:
        """查询所有已注册设备令牌列表。"""
        try:
            from backend.db.session import SessionLocal
            from backend.models.database import DeviceToken

            db = SessionLocal()
            try:
                rows = db.query(DeviceToken.device_token).all()
                return [row[0] for row in rows]
            finally:
                db.close()
        except Exception:
            logger.exception("FCM: failed to query all device tokens")
            return []
