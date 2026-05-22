from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel


class GeminiModel(BaseModel):
    """Google Gemini provider（OpenAI-compatible 端点）。

    Gemini 通过 Google AI Studio 的 OpenAI 兼容层暴露标准接口：
      https://generativelanguage.googleapis.com/v1beta/openai/

    chat 与 vision 使用同一模型（如 gemini-2.0-flash），均支持多模态。
    语音转录：Gemini OpenAI 兼容端点不暴露 /v1/audio/transcriptions；
    如需转录，请为 transcribe 单独配置其他 provider。
    """

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                "Missing API key for Gemini provider. "
                "请设置 GEMINI_API_KEY（Google AI Studio API Key）或 CHAT_API_KEY 环境变量。"
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
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta

    def transcribe_audio(
        self,
        *,
        model: str,
        audio_path: Path,
        response_format: str = "verbose_json",
    ) -> str:
        raise NotImplementedError(
            "Gemini OpenAI 兼容端点不暴露 /v1/audio/transcriptions。"
            "Google 语音转文本请使用 Speech-to-Text API，"
            "或设置 TRANSCRIBE_PROVIDER=openai 使用 Whisper。"
        )
