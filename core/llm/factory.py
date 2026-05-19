from __future__ import annotations

from core.llm.base import BaseModel
from core.llm.config import resolve_api_key, resolve_base_url, resolve_model_name, resolve_provider
from core.llm.openai_model import OpenAIModel
from core.llm.deepseek_model import DeepSeekModel

# DeepSeek 不支持原生音频转录；若用户将 transcribe 指向 deepseek，直接提示切换。
_DEEPSEEK_UNSUPPORTED_CAPS = {"transcribe"}


def _build_model(capability: str, provider: str) -> BaseModel:
    if provider == "deepseek":
        if capability in _DEEPSEEK_UNSUPPORTED_CAPS:
            raise ValueError(
                f"DeepSeek 不支持 {capability} 能力。"
                "请将 TRANSCRIBE_PROVIDER 设为 openai 以使用 Whisper 转录。"
            )
        api_key = resolve_api_key(capability)
        if not api_key:
            raise ValueError(
                "未能找到 DEEPSEEK_API_KEY、CHAT_API_KEY 或 OPENAI_API_KEY 环境变量。"
            )
        return DeepSeekModel(
            api_key=api_key,
            base_url=resolve_base_url(capability),
        )

    if provider == "openai":
        api_key = resolve_api_key(capability)
        if not api_key:
            if capability == "chat":
                raise ValueError("未能找到 CHAT_API_KEY 或 OPENAI_API_KEY 环境变量。")
            if capability == "vision":
                raise ValueError("未能找到 VISION_API_KEY、CHAT_API_KEY 或 OPENAI_API_KEY 环境变量。")
            raise ValueError("未能找到 TRANSCRIBE_API_KEY 或 OPENAI_API_KEY 环境变量。")
        return OpenAIModel(
            api_key=api_key,
            base_url=resolve_base_url(capability),
        )
    raise ValueError(f"Unsupported provider: {provider}")


def get_model_for_capability(capability: str) -> BaseModel:
    capability = capability.strip().lower()
    provider = resolve_provider(capability)
    return _build_model(capability, provider)


def get_model_name_for_capability(capability: str) -> str:
    return resolve_model_name(capability)


def reset_model_cache() -> None:
    # Kept for backward compatibility in tests/imports.
    return None
