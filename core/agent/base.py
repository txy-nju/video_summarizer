"""Agent base interface — framework-agnostic abstraction for intelligent agents.

Agents consume conversation memory, tool registries, and LLM backends
to produce answers (streaming or non-streaming) for user questions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class BaseAgent(ABC):
    """Abstract agent that answers user questions.

    Concrete implementations may use different strategies:
    - Prompt-driven ReAct (QAAgent)
    - Function-calling / tool-use
    - Direct RAG without reasoning loop

    Subclasses MUST implement both ``answer`` and ``answer_stream``.
    """

    @abstractmethod
    def answer(self, *, question: str, chat_id: str, kbid: str, owner_id: str) -> str:
        """Non-streaming answer: blocks until the full answer is ready.

        Returns the complete answer text.
        """

    @abstractmethod
    def answer_stream(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> Iterator[str]:
        """Streaming answer: yields tokens as they become available.

        The caller is responsible for collecting tokens and persisting
        the final result.
        """
