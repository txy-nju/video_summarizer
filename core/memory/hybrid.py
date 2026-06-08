"""HybridChatMemory — DB-backed conversation memory with Redis hot-cache.

DB (GlobalQARecord) is the source of truth.
Redis caches the built messages list for low-latency reads.
Cache-aside pattern: writes invalidate the cache, reads repopulate on miss.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.context.message_builder import MessageBuilder
from core.memory.base import BaseChatMemory

logger = logging.getLogger(__name__)

_DEFAULT_REDIS_TTL_SECONDS = 300
_MEMORY_KEY_PREFIX = "chat_memory:"


class HybridChatMemory(BaseChatMemory):
    """Hybrid conversation memory (DB + Redis).

    Parameters
    ----------
    qa_repository:
        ``GlobalQARepository`` instance for loading persistent Q&A history.
    redis_client:
        A ``redis.Redis`` (or compatible) client for caching.
    message_builder:
        ``MessageBuilder`` for assembling the final message list.
    redis_ttl_seconds:
        TTL for Redis cache keys (default 300s).
    """

    def __init__(
        self,
        *,
        qa_repository: Any,
        redis_client: Any,
        message_builder: MessageBuilder | None = None,
        redis_ttl_seconds: int = _DEFAULT_REDIS_TTL_SECONDS,
    ) -> None:
        self._qa_repo = qa_repository
        self._redis = redis_client
        self._builder = message_builder or MessageBuilder()
        self._ttl = redis_ttl_seconds

    # ── BaseChatMemory interface ──────────────────────────────────────

    def build_messages(
        self,
        *,
        chat_id: str,
        owner_id: str,
        system_prompt: str,
        current_question: str,
        rag_context: str = "",
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        # 1. Try Redis cache
        cache_key = _cache_key(chat_id)
        try:
            cached = self._redis.get(cache_key)
            if cached:
                logger.debug("HybridChatMemory: cache hit for chat_id=%s", chat_id)
                messages = json.loads(cached)
                # Append current user message with RAG context
                current_user = self._builder._build_current_user_message(
                    rag_context=rag_context, question=current_question
                )
                result = list(messages)
                result.append(current_user)
                # Re-apply truncation if needed
                if max_tokens is not None:
                    system_msg = messages[0] if messages else {"role": "system", "content": system_prompt}
                    history = messages[1:] if len(messages) > 1 else []
                    result = [system_msg] + self._builder._truncate_history(
                        system_prompt=system_prompt,
                        history=history,
                        current_user_content=current_user["content"],
                        max_tokens=max_tokens,
                    ) + [current_user]
                return result
        except Exception:
            logger.warning("HybridChatMemory: Redis read failed for chat_id=%s", chat_id)

        # 2. Cache miss → load from DB
        history = self._load_history_with_owner(owner_id, chat_id)

        # 3. Build messages
        messages = self._builder.build(
            system_prompt=system_prompt,
            history=history,
            rag_context=rag_context,
            current_question=current_question,
            max_tokens=max_tokens,
        )

        # 4. Write to Redis (cache system + history only — current question changes per turn)
        try:
            cacheable = messages[:-1]  # exclude current user message
            self._redis.setex(
                cache_key, self._ttl, json.dumps(cacheable, ensure_ascii=False)
            )
            logger.debug("HybridChatMemory: cache written for chat_id=%s", chat_id)
        except Exception:
            logger.warning("HybridChatMemory: Redis write failed for chat_id=%s", chat_id)

        return messages

    def add_turn(self, *, chat_id: str, question: str, answer: str) -> None:
        """Invalidate the Redis cache for this chat.

        The Q&A record is already persisted to DB by GlobalQAService.
        We just need to evict the cache so the next ``build_messages``
        picks up the new turn from DB.
        """
        cache_key = _cache_key(chat_id)
        try:
            self._redis.delete(cache_key)
        except Exception:
            logger.warning("HybridChatMemory: Redis delete failed for chat_id=%s", chat_id)

    def clear(self, *, chat_id: str) -> None:
        """Clear the Redis cache for this chat.

        Does NOT delete DB records — those are managed by the chat session
        lifecycle (delete_chat_session removes all Q&A records).
        """
        cache_key = _cache_key(chat_id)
        try:
            self._redis.delete(cache_key)
            logger.info("HybridChatMemory: cache cleared for chat_id=%s", chat_id)
        except Exception:
            logger.warning("HybridChatMemory: Redis clear failed for chat_id=%s", chat_id)

    # ── internal ─────────────────────────────────────────────────────

    def _load_history_with_owner(
        self, owner_id: str, chat_id: str
    ) -> list[dict[str, Any]]:
        """Load history from DB with owner context for permission filtering."""
        try:
            records = self._qa_repo.list_by_owner_and_chat(
                owner_id=owner_id, chat_id=chat_id
            )
        except Exception:
            logger.exception(
                "HybridChatMemory: DB load failed for chat_id=%s owner=%s",
                chat_id,
                owner_id,
            )
            return []

        messages: list[dict[str, Any]] = []
        for record in records:
            messages.append({"role": "user", "content": record.question_content})
            if record.answer_content:
                messages.append({"role": "assistant", "content": record.answer_content})
        return messages


def _cache_key(chat_id: str) -> str:
    return f"{_MEMORY_KEY_PREFIX}{chat_id}"
