from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List

from openai import OpenAI

from core.llm.base import BaseModel


class AigcModel(BaseModel):
    """vivo AIGC provider implementation (OpenAI-compatible API).

    vivo 蓝心大模型通过 OpenAI 兼容接口暴露 chat / vision 能力，
    可直接使用 openai SDK 客户端调用。

    支持的模型：
    - 纯文本（chat）：Volc-DeepSeek-V3.2
    - 多模态（vision）：Doubao-Seed-2.0-mini / lite / pro、qwen3.5-plus

    注意：
    - 每个 client 实例初始化时生成 requestId UUID 作为 default_query，
      与官方文档示例完全一致。
    - vivo AIGC 不提供原生音频转录 API，transcribe_audio 将抛出 NotImplementedError。
      如需转录能力，请为 transcribe capability 单独配置其他 provider（如 openai）。
    """

    DEFAULT_BASE_URL = "https://api-ai.vivo.com.cn/v1"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                "Missing API key for AIGC provider. "
                "请设置 AIGC_API_KEY 或 CHAT_API_KEY 环境变量。"
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            default_headers={
                "Content-Type": "application/json; charset=utf-8",
            },
            default_query={"request_id": str(uuid.uuid4())},
        )

    # ── BaseModel 接口实现 ─────────────────────────────────────────────

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
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
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
        raise NotImplementedError(
            "vivo AIGC 不提供原生音频转录 API。"
            "如需转录，请设置环境变量 TRANSCRIBE_PROVIDER=openai 以使用 OpenAI Whisper。"
        )
