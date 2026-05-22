from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel


class LocalModel(BaseModel):
    """本地 / 自托管 LLM provider（兼容 Ollama / vLLM / LM Studio / llama.cpp 等）。

    所有主流本地推理引擎均暴露 OpenAI-compatible API，可直接使用 openai SDK。
    默认 base_url 指向 Ollama 标准端口；可通过 LOCAL_BASE_URL 覆盖。

    语音转录：取决于本地服务端是否加载了 Whisper 模型。
    Ollama 默认不支持；vLLM + whisper 插件可以。
    """

    DEFAULT_BASE_URL = "http://localhost:11434/v1"

    def __init__(self, api_key: str, base_url: str | None = None):
        # 本地服务通常不需要真实 API Key，传任意非空字符串即可通过 SDK 校验。
        self._client = OpenAI(
            api_key=api_key or "local",
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
        """调用本地 /v1/audio/transcriptions 端点。
        若本地服务未加载 Whisper 模型，将返回 HTTP 错误。"""
        with open(audio_path, "rb") as audio_file:
            transcript = self._client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format=response_format,
            )
        return transcript.model_dump_json(indent=2)
