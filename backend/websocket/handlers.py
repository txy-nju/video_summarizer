"""
WebSocket 路由处理器。

提供：
- GET /ws/progress: WebSocket 端点，建立实时进度推送连接

边界约束：
- 连接认证失败必须立刻关闭，不允许匿名旁路监听。
- 仅转发状态事件，不直接查询复杂业务数据。
- 重连补偿规则：客户端以上次 last_sequence 发起重连，服务端先返回 reconnect_ack。
"""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from backend.auth.dependencies import get_current_user_ws
from backend.websocket.schemas import (
    WSEventEnvelope,
    WSEventType,
    WSScope,
)
from backend.websocket.manager import ConnectionManager
from backend.dependencies import get_connection_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/progress")
async def websocket_progress(
    websocket: WebSocket,
    manager: ConnectionManager = Depends(get_connection_manager),
):
    """
    WebSocket 实时进度推送端点。

    认证：通过 query parameter `token` 传递 JWT access_token。
    连接成功后，服务端通过 Redis Pub/Sub 接收跨实例进度事件并推送给客户端。

    Query Parameters:
        token: JWT access_token（必填）
        last_sequence: 客户端上次收到的 sequence，用于重连补偿（可选）

    消息格式：WSEventEnvelope JSON 字符串
    """
    # JWT 认证 — 认证失败立刻关闭连接
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="missing_token")
        return

    current_user = await get_current_user_ws(websocket, token)
    if current_user is None:
        # get_current_user_ws 已负责关闭连接
        return

    user_id = current_user.user_id

    # 重连补偿：客户端可传入上次 sequence，服务端先发 reconnect_ack
    last_seq = websocket.query_params.get("last_sequence")
    try:
        await manager.connect(websocket, user_id)
    except Exception:
        logger.exception("WebSocket connect failed: user_id=%s", user_id)
        return

    # 发送重连确认
    if last_seq is not None:
        try:
            last_seq_int = int(last_seq)
            ack = WSEventEnvelope(
                event_type=WSEventType.RECONNECT_ACK,
                user_id=user_id,
                scope=WSScope.VIDEO_RESOURCE,
                scope_id="",
                sequence=last_seq_int,
                status="RECONNECTED",
                message=f"Reconnected, last_sequence={last_seq_int}",
                payload={"last_sequence": last_seq_int},
            )
            await websocket.send_text(ack.model_dump_json())
        except ValueError:
            pass

    # 保持连接，接收客户端消息（当前仅用于心跳检测）
    try:
        while True:
            data = await websocket.receive_text()
            # 目前仅处理心跳 ping/pong，不做业务消息处理
            if data == "ping":
                await websocket.send_text("pong")
            else:
                logger.debug("WebSocket received unknown message from user_id=%s: %s", user_id, data)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: user_id=%s", user_id)
    except Exception:
        logger.exception("WebSocket error: user_id=%s", user_id)
    finally:
        await manager.disconnect(user_id)
