from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel


class OpenAIModel(BaseModel):
    """OpenAI-compatible provider implementation."""

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError("Missing API key for OpenAI provider.")
        self._client = OpenAI(api_key=api_key, base_url=base_url)

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
    ) -> str:
        with open(audio_path, "rb") as audio_file:
            transcript = self._client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format=response_format,
                timeout=120.0,
            )
        return transcript.model_dump_json(indent=2)
