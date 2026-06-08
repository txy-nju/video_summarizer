"""MessageBuilder — assembles OpenAI-format message lists.

Combines a system prompt, conversation history, RAG retrieval context,
and the current user question into a single ``messages`` list suitable
for any OpenAI-compatible chat completion API.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Conservative estimate: 1 token ≈ 2 characters for Chinese/English mixed text.
_CHARS_PER_TOKEN_ESTIMATE = 2


class MessageBuilder:
    """Stateless message assembler with token-aware truncation.

    Usage::

        builder = MessageBuilder()
        messages = builder.build(
            system_prompt="You are a helpful assistant.",
            history=[{"role": "user", "content": "Hi"}, ...],
            rag_context="Transcript: ...",
            current_question="What did the speaker say about ML?",
            max_tokens=4096,
        )
    """

    # ── public API ────────────────────────────────────────────────────

    def build(
        self,
        *,
        system_prompt: str,
        history: list[dict[str, Any]] | None = None,
        rag_context: str = "",
        current_question: str,
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build the full messages list.

        Parameters
        ----------
        system_prompt:
            System-level instruction placed as the first message.
        history:
            Prior conversation turns in ``[user, assistant, user, ...]`` format.
        rag_context:
            Pre-formatted text from RAG retrieval (injected into the
            current user message).
        current_question:
            The user's latest question.
        max_tokens:
            If set, truncate history to keep total estimated tokens
            under this budget.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Build the current user message with RAG context
        current_user = self._build_current_user_message(
            rag_context=rag_context, question=current_question
        )

        history = list(history) if history else []

        if max_tokens is not None:
            history = self._truncate_history(
                system_prompt=system_prompt,
                history=history,
                current_user_content=current_user["content"],
                max_tokens=max_tokens,
            )

        messages.extend(history)
        messages.append(current_user)
        return messages

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_current_user_message(
        *, rag_context: str, question: str
    ) -> dict[str, Any]:
        if rag_context:
            content = (
                f"请基于以下视频转录内容回答问题。\n\n"
                f"转录内容：\n{rag_context}\n\n问题：{question}"
            )
        else:
            content = f"问题：{question}"
        return {"role": "user", "content": content}

    def _truncate_history(
        self,
        *,
        system_prompt: str,
        history: list[dict[str, Any]],
        current_user_content: str,
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        """Remove earliest (user, assistant) pairs until within budget."""
        fixed_tokens = self._estimate_tokens(system_prompt) + self._estimate_tokens(
            current_user_content
        )

        while history:
            total = fixed_tokens + self._estimate_history_tokens(history)
            if total <= max_tokens:
                break
            # Drop the earliest turn (user + assistant pair if possible)
            if len(history) >= 2 and history[0]["role"] == "user" and history[1]["role"] == "assistant":
                dropped = history.pop(0)  # user
                dropped = history.pop(0)  # assistant
            else:
                dropped = history.pop(0)

            logger.debug(
                "MessageBuilder: truncated history turn (estimated total %d > %d)",
                total,
                max_tokens,
            )

        return history

    def _estimate_history_tokens(self, history: list[dict[str, Any]]) -> int:
        return sum(
            self._estimate_tokens(msg.get("content", "")) for msg in history
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count for mixed Chinese/English text."""
        if not text:
            return 0
        return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)
