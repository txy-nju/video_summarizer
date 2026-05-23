"""
进度事件总线（Redis Pub/Sub 封装）。

提供：
- publish(event): 发布进度事件到 Redis Pub/Sub
- subscribe(channels, callback): 订阅指定频道并回调处理

边界约束：
- Redis Pub/Sub 仅用于实时分发，不作为权威存储。
- 权威状态以数据库实体字段与任务状态表为准。
- 多实例部署下通过 Redis Pub/Sub 跨实例广播。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import redis

from backend.websocket.schemas import WSEventEnvelope

logger = logging.getLogger(__name__)

# Redis 频道命名约定（对齐计划）
CHANNEL_PREFIX_V1 = "progress.v1"


def _channel_name(scope: str, identifier: str) -> str:
    """生成 Redis Pub/Sub 频道名称。"""
    return f"{CHANNEL_PREFIX_V1}.{scope}.{identifier}"


class ProgressEventBus:
    """基于 Redis Pub/Sub 的进度事件广播总线。"""

    def __init__(self, redis_client: redis.Redis, instance_id: str = "") -> None:
        self._redis = redis_client
        self._instance_id = instance_id

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    def publish(self, event: WSEventEnvelope) -> int:
        """发布进度事件到 Redis Pub/Sub。

        同时发布到三个频道层级：
        1. 主业务进度频道：progress.v1.tenant.{tenant_id}
        2. 用户定向频道：progress.v1.user.{user_id}
        3. 系统控制频道：progress.v1.control

        Returns:
            收到消息的订阅者总数。
        """
        # 注入事件源
        if event.source is None:
            from backend.websocket.schemas import WSSource
            event.source = WSSource(service="progress_event_bus", instance_id=self._instance_id)

        msg = event.model_dump_json()
        channels = [
            _channel_name("tenant", event.tenant_id),
            _channel_name("user", event.user_id),
            _channel_name("control", ""),
        ]

        total = 0
        for ch in set(channels):
            try:
                count = self._redis.publish(ch, msg)
                total += count
            except Exception:
                logger.exception("Failed to publish to channel: %s", ch)

        logger.debug(
            "Progress event published: event_type=%s, event_id=%s, sequence=%s, channels=%d, subscribers=%d",
            event.event_type,
            event.event_id,
            event.sequence,
            len(channels),
            total,
        )
        return total

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def subscribe(
        self,
        channels: list[str],
        callback: Callable[[WSEventEnvelope], Any],
        stop_check: Callable[[], bool] | None = None,
        poll_timeout: float = 1.0,
    ) -> None:
        """订阅指定频道列表，收到消息时调用 callback。

        此方法运行在独立线程中，使用 get_message() 轮询替代 listen() 无限阻塞，
        支持通过 stop_check 回调优雅退出，避免进程 shutdown 时 TCP socket 卡死。

        Args:
            channels: Redis Pub/Sub 频道名列表
            callback: 收到消息时的处理函数，接收 WSEventEnvelope 实例
            stop_check: 可选的停止检查函数，返回 True 时退出循环
            poll_timeout: 每次 get_message 的超时秒数，默认 1.0s
        """
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(*channels)
            logger.info("ProgressEventBus subscribed to channels: %s", channels)

            while True:
                if stop_check is not None and stop_check():
                    break
                raw = pubsub.get_message(timeout=poll_timeout)
                if raw is None:
                    continue
                if raw["type"] != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                    event = WSEventEnvelope.model_validate(data)
                    callback(event)
                except Exception:
                    logger.exception("Failed to process progress event")
        finally:
            try:
                pubsub.unsubscribe(*channels)
                pubsub.close()
            except Exception:
                pass
