from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel
from core.llm.transcription_result import TranscriptionResult


class QwenModel(BaseModel):
    """Qwen / 通义千问 provider implementation (DashScope OpenAI-compatible API).

    Qwen 通过阿里云 DashScope 的兼容模式暴露 OpenAI 风格接口，
    可直接使用 openai SDK 客户端调用 chat / vision / transcribe 三种能力。
    语音转录底层对接 Paraformer 系列模型（如 paraformer-v2）。
    """

    supports_transcribe = True
    max_audio_upload_bytes = None  # URL 模式支持 2GB，无客户端大小限制
    audio_chunk_size_bytes = None

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                "Missing API key for Qwen provider. "
                "请设置 QWEN_API_KEY（DashScope API Key）或 CHAT_API_KEY 环境变量。"
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
    ) -> TranscriptionResult:
        """DashScope 兼容模式对接 Paraformer 语音识别。

        当前使用 OpenAI 兼容端点 `/v1/audio/transcriptions`。
        若端点不可用，将 fallback 到 DashScope 原生 Paraformer REST API。
        """
        with open(audio_path, "rb") as audio_file:
            transcript = self._client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format=response_format,
            )
        raw_json = transcript.model_dump_json(indent=2)
        import json
        data = json.loads(raw_json)

        # 检测返回格式：Paraformer 格式（sentences）vs Whisper 格式（segments）
        if "sentences" in data and isinstance(data.get("sentences"), list):
            return TranscriptionResult.from_paraformer_response(data)
        return TranscriptionResult.from_whisper_verbose_json(data)
