"""Agent base interface — framework-agnostic abstraction for intelligent agents.

Agents consume conversation memory, tool registries, and LLM backends
to produce answers (streaming or non-streaming) for user questions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.agent.events import AgentProgressEvent


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

        Returns the complete answer text (progress events are filtered out).
        """

    @abstractmethod
    def answer_stream(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> Iterator[str | AgentProgressEvent]:
        """Streaming answer: yields tokens and progress events as they become available.

        Yields ``str`` for text tokens and ``AgentProgressEvent`` for progress
        phase notifications.  Callers should filter with ``isinstance(item, str)``
        when only the answer text is needed (e.g. for DB persistence).
        """
