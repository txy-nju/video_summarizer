"""ChatMemory base interface — conversation history management.

Provides a storage-agnostic abstraction for loading, caching, and
building multi-turn conversation messages for LLM consumption.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseChatMemory(ABC):
    """Abstract conversation memory for a single chat session.

    Implementations may use:
    - DB-only (query GlobalQARecord each time)
    - Redis-only (ephemeral, low-latency)
    - Hybrid (DB as source-of-truth + Redis hot-cache)

    All methods operate within the scope of a single ``chat_id``.
    """

    @abstractmethod
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
        """Build the full messages list for an LLM call.

        Returns a list of message dicts in OpenAI Chat Completions format:
        ``[{"role": "system", "content": ...}, {"role": "user", "content": ...}, ...]``

        Parameters
        ----------
        chat_id:
            Session identifier for loading history.
        owner_id:
            Authenticated user ID for permission-scoped history loading.
        system_prompt:
            System-level instruction (role + behaviour).
        current_question:
            The user's latest question text.
        rag_context:
            Pre-formatted RAG retrieval results to inject into the current turn.
        max_tokens:
            Optional token budget for truncation. When exceeded, the earliest
            history turns are dropped.
        """

    @abstractmethod
    def add_turn(self, *, chat_id: str, question: str, answer: str) -> None:
        """Record a completed Q&A turn.

        Implementations should invalidate any cached messages for this
        ``chat_id`` so that the next ``build_messages`` call includes the
        new turn.
        """

    @abstractmethod
    def clear(self, *, chat_id: str) -> None:
        """Clear cached messages for a chat session.

        Does NOT delete persistent Q&A records — only evicts the cache.
        """
