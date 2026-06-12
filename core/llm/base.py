from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterator, List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm.transcription_result import TranscriptionResult


class BaseModel(ABC):
    """Provider-agnostic LLM base interface.

    Subclasses declare capabilities and limits via class attributes:
    - supports_transcribe: 该 provider 是否支持 audio transcription
    - max_audio_upload_bytes: 单次上传最大字节数，None 表示无限制
    - audio_chunk_size_bytes: 固定分片上传每片大小，None 表示单次上传（不需要分片）
    """

    # ── 能力声明（子类覆写）──────────────────────────────────────────
    supports_transcribe: bool = False
    max_audio_upload_bytes: int | None = 25 * 1024 * 1024  # 默认 25MB（Whisper 限制）
    audio_chunk_size_bytes: int | None = None  # 默认单次上传，不分片

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
    def stream_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Run a streaming chat completion and yield text tokens one by one."""

    @abstractmethod
    def transcribe_audio(
        self,
        *,
        model: str,
        audio_path: Path,
        response_format: str = "verbose_json",
    ) -> "TranscriptionResult":
        """Run an audio transcription request and return a normalized TranscriptionResult."""
