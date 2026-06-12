from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel
from core.llm.transcription_result import TranscriptionResult


class GroqModel(BaseModel):
    """Groq provider implementation (OpenAI-compatible API).

    Groq 提供 OpenAI 兼容端点：https://api.groq.com/openai/v1
    语音转录使用 whisper-large-v3 / whisper-large-v3-turbo 模型，
    支持 segment 和 word 级别的 timestamp_granularities。
    """

    supports_transcribe = True
    max_audio_upload_bytes = 100 * 1024 * 1024  # Dev tier 100MB；Free tier 25MB
    audio_chunk_size_bytes = None  # 单次上传

    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                "Missing API key for Groq provider. "
                "请设置 GROQ_API_KEY 或 OPENAI_API_KEY 环境变量。"
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )

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
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout

        response = self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    def stream_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        kwargs: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        with self._client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta

    def transcribe_audio(
        self,
        *,
        model: str,
        audio_path: Path,
        response_format: str = "verbose_json",
        timestamp_granularities: List[str] | None = None,
    ) -> TranscriptionResult:
        """调用 Groq Whisper API 并返回标准化 TranscriptionResult。

        kwargs:
            timestamp_granularities: 可选 ["segment"] 或 ["word", "segment"]，
                控制返回的时间戳粒度。默认仅 segment 级别。
        """
        if timestamp_granularities is None:
            timestamp_granularities = ["segment"]

        create_kwargs: Dict[str, Any] = {
            "model": model,
            "file": open(audio_path, "rb"),
            "response_format": response_format,
        }

        # Groq 特有的 timestamp_granularities 参数
        # 注意：仅 response_format="verbose_json" 时有效
        if response_format == "verbose_json" and timestamp_granularities:
            create_kwargs["timestamp_granularities"] = timestamp_granularities

        try:
            transcript = self._client.audio.transcriptions.create(**create_kwargs)
            raw_json = transcript.model_dump_json(indent=2)
            return TranscriptionResult.from_whisper_verbose_json(json.loads(raw_json))
        finally:
            # 清理打开的文件句柄
            if "file" in create_kwargs and hasattr(create_kwargs["file"], "close"):
                create_kwargs["file"].close()
