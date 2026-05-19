from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List


class BaseModel(ABC):
    """Provider-agnostic LLM base interface."""

    @abstractmethod
    def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        response_format: Dict[str, str] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """Run a chat completion request and return text output."""

    @abstractmethod
    def transcribe_audio(
        self,
        *,
        model: str,
        audio_path: Path,
        response_format: str = "verbose_json",
    ) -> str:
        """Run an audio transcription request and return serialized response."""
