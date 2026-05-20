from __future__ import annotations

import os


def _read_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_non_empty(*values: str, default: str = "") -> str:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return default


def resolve_provider(capability: str) -> str:
    cap = capability.strip().lower()
    if cap == "chat":
        return _first_non_empty(_read_env("CHAT_PROVIDER"), default="openai").lower()
    if cap == "vision":
        return _first_non_empty(_read_env("VISION_PROVIDER"), default="openai").lower()
    if cap == "transcribe":
        return _first_non_empty(_read_env("TRANSCRIBE_PROVIDER"), default="openai").lower()
    raise ValueError(f"Unsupported capability: {capability}")


def resolve_model_name(capability: str) -> str:
    cap = capability.strip().lower()
    if cap == "chat":
        return _first_non_empty(
            _read_env("CHAT_MODEL_NAME"),
            _read_env("OPENAI_MODEL_NAME"),
            default="gpt-4o",
        )
    if cap == "vision":
        return _first_non_empty(
            _read_env("VISION_MODEL_NAME"),
            _read_env("OPENAI_VISION_MODEL_NAME"),
            _read_env("CHAT_MODEL_NAME"),
            _read_env("OPENAI_MODEL_NAME"),
            default="gpt-4o",
        )
    if cap == "transcribe":
        return _first_non_empty(
            _read_env("TRANSCRIBE_MODEL_NAME"),
            _read_env("TRANSCRIBER_MODEL"),
            default="whisper-1",
        )
    raise ValueError(f"Unsupported capability: {capability}")


def resolve_api_key(capability: str) -> str:
    cap = capability.strip().lower()
    if cap == "chat":
        return _first_non_empty(
            _read_env("CHAT_API_KEY"),
            _read_env("QWEN_API_KEY"),
            _read_env("DEEPSEEK_API_KEY"),
            _read_env("LOCAL_API_KEY"),
            _read_env("OPENAI_API_KEY"),
        )
    if cap == "vision":
        return _first_non_empty(
            _read_env("VISION_API_KEY"),
            _read_env("QWEN_API_KEY"),
            _read_env("DEEPSEEK_API_KEY"),
            _read_env("LOCAL_API_KEY"),
            _read_env("OPENAI_API_KEY"),
            _read_env("CHAT_API_KEY"),
        )
    if cap == "transcribe":
        return _first_non_empty(
            _read_env("TRANSCRIBE_API_KEY"),
            _read_env("QWEN_API_KEY"),
            _read_env("LOCAL_API_KEY"),
            _read_env("OPENAI_API_KEY"),
        )
    raise ValueError(f"Unsupported capability: {capability}")


def resolve_base_url(capability: str) -> str | None:
    cap = capability.strip().lower()
    if cap == "chat":
        base_url = _first_non_empty(
            _read_env("CHAT_BASE_URL"),
            _read_env("QWEN_BASE_URL"),
            _read_env("DEEPSEEK_BASE_URL"),
            _read_env("LOCAL_BASE_URL"),
            _read_env("OPENAI_BASE_URL"),
        )
        return base_url or None
    if cap == "vision":
        base_url = _first_non_empty(
            _read_env("VISION_BASE_URL"),
            _read_env("QWEN_BASE_URL"),
            _read_env("DEEPSEEK_BASE_URL"),
            _read_env("LOCAL_BASE_URL"),
            _read_env("OPENAI_BASE_URL"),
            _read_env("CHAT_BASE_URL"),
        )
        return base_url or None
    if cap == "transcribe":
        base_url = _first_non_empty(
            _read_env("TRANSCRIBE_BASE_URL"),
            _read_env("QWEN_BASE_URL"),
            _read_env("LOCAL_BASE_URL"),
            _read_env("OPENAI_BASE_URL"),
        )
        return base_url or None
    raise ValueError(f"Unsupported capability: {capability}")
