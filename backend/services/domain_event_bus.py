"""
领域事件总线（Redis Streams 封装）。

提供：
- publish(event): 发布领域事件 → XADD
- consume(consumer_group, consumer_name, event_types): 消费事件
  → XREADGROUP + XACK，返回生成器

边界约束：
- Event Bus 仅负责消息路由，不承载业务规则。
- 发布方不感知消费方。
- Consumer Group 保证消息至少消费一次。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generator

import redis

from backend.schemas.domain_event import DomainEvent

logger = logging.getLogger(__name__)

# Redis Stream Key 前缀
_STREAM_PREFIX = "domain:events:"


class DomainEventBus:
    """基于 Redis Streams 的领域事件总线。"""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    def publish(self, event: DomainEvent) -> str | None:
        """发布领域事件到 Redis Stream。

        Stream Key: domain:events:{event_type}
        消息体: DomainEvent JSON 字符串

        Returns:
            Redis Stream 消息 ID，或 None（发送失败时）。
        """
        stream_key = self._stream_key(event.event_type)
        try:
            msg_id = self._redis.xadd(
                stream_key,
                {"data": event.to_json()},
                maxlen=10000,  # 保留最近 1 万条
            )
            logger.debug(
                "Domain event published: event_type=%s, event_id=%s, stream=%s, msg_id=%s",
                event.event_type,
                event.event_id,
                stream_key,
                msg_id,
            )
            return msg_id
        except Exception:
            logger.exception("Failed to publish domain event: event_type=%s", event.event_type)
            return None

    # ------------------------------------------------------------------
    # 消费
    # ------------------------------------------------------------------

    def consume(
        self,
        consumer_group: str,
        consumer_name: str,
        event_types: list[str],
        block_ms: int = 5000,
        batch_size: int = 10,
    ) -> Generator[DomainEvent, None, None]:
        """消费指定类型领域事件（阻塞式生成器）。

        使用 Redis Consumer Group 实现：
        - 首次调用自动创建 Stream + Consumer Group（MKSTREAM）
        - 读取未确认消息（>），处理完后 XACK
        - 支持多实例负载均衡（同一 consumer_group 下多个 consumer）

        Args:
            consumer_group: Consumer Group 名称
            consumer_name: 当前消费者名称
            event_types: 要监听的事件类型列表
            block_ms: 阻塞等待时间（毫秒）
            batch_size: 每批最多拉取消息数

        Yields:
            DomainEvent 实例（每批最多 batch_size 个）。
        """
        streams = {self._stream_key(et): ">" for et in event_types}

        # 确保 Consumer Group 存在
        self._ensure_consumer_groups(consumer_group, list(streams.keys()))

        while True:
            try:
                results = self._redis.xreadgroup(
                    groupname=consumer_group,
                    consumername=consumer_name,
                    streams=streams,
                    count=batch_size,
                    block=block_ms,
                )
            except Exception:
                logger.exception(
                    "xreadgroup error for group=%s consumer=%s",
                    consumer_group,
                    consumer_name,
                )
                continue

            if not results:
                continue

            for stream_name_bytes, messages in results:  # type: ignore[assignment]
                stream_name = stream_name_bytes.decode() if isinstance(stream_name_bytes, bytes) else stream_name_bytes
                for msg_id_bytes, fields in messages:  # type: ignore[assignment]
                    msg_id = msg_id_bytes.decode() if isinstance(msg_id_bytes, bytes) else msg_id_bytes
                    try:
                        raw = fields.get(b"data") or fields.get("data")
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        event = DomainEvent.model_validate(json.loads(raw))
                        yield event
                        # ACK
                        self._redis.xack(stream_name, consumer_group, msg_id)
                    except Exception:
                        logger.exception(
                            "Failed to parse/ack domain event: stream=%s, msg_id=%s",
                            stream_name,
                            msg_id,
                        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _stream_key(event_type: str) -> str:
        return f"{_STREAM_PREFIX}{event_type}"

    def _ensure_consumer_groups(self, consumer_group: str, stream_keys: list[str]) -> None:
        """为每个 Stream 创建 Consumer Group（如果不存在则 MKSTREAM）。"""
        for key in stream_keys:
            try:
                self._redis.xgroup_create(
                    key,
                    consumer_group,
                    id="0",
                    mkstream=True,
                )
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
