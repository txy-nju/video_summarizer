import os
from unittest.mock import MagicMock, patch

import pytest

from core.llm.factory import get_model_for_capability, get_model_name_for_capability
from core.llm.openai_model import OpenAIModel


def test_factory_routes_chat_to_openai_by_default():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "fake-key",
            "CHAT_PROVIDER": "openai",
        },
        clear=False,
    ):
        model = get_model_for_capability("chat")
    assert isinstance(model, OpenAIModel)


def test_factory_routes_vision_and_transcribe_independently():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "fake-key",
            "VISION_PROVIDER": "openai",
            "TRANSCRIBE_PROVIDER": "openai",
        },
        clear=False,
    ):
        vision_model = get_model_for_capability("vision")
        transcribe_model = get_model_for_capability("transcribe")

    assert isinstance(vision_model, OpenAIModel)
    assert isinstance(transcribe_model, OpenAIModel)


def test_factory_raises_when_api_key_missing():
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            get_model_for_capability("chat")


def test_factory_raises_for_unsupported_provider():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "fake-key",
            "CHAT_PROVIDER": "unknown_provider",
        },
        clear=False,
    ):
        with pytest.raises(ValueError, match="Unsupported provider"):
            get_model_for_capability("chat")


def test_factory_raises_for_unsupported_capability():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}, clear=False):
        with pytest.raises(ValueError, match="Unsupported capability"):
            get_model_for_capability("embeddings")


def test_model_name_can_be_split_by_capability():
    with patch.dict(
        os.environ,
        {
            "CHAT_MODEL_NAME": "chat-model-x",
            "VISION_MODEL_NAME": "vision-model-y",
            "TRANSCRIBE_MODEL_NAME": "transcribe-model-z",
        },
        clear=False,
    ):
        assert get_model_name_for_capability("chat") == "chat-model-x"
        assert get_model_name_for_capability("vision") == "vision-model-y"
        assert get_model_name_for_capability("transcribe") == "transcribe-model-z"


def test_factory_uses_split_credentials_and_base_url():
    with patch("core.llm.factory.OpenAIModel") as mock_openai_model:
        mock_openai_model.return_value = object()
        with patch.dict(
            os.environ,
            {
                "CHAT_PROVIDER": "openai",
                "CHAT_API_KEY": "chat-key",
                "CHAT_BASE_URL": "https://chat.example/v1",
                "OPENAI_API_KEY": "fallback-key",
                "OPENAI_BASE_URL": "https://fallback.example/v1",
            },
            clear=False,
        ):
            _ = get_model_for_capability("chat")

        mock_openai_model.assert_called_once_with(
            api_key="chat-key",
            base_url="https://chat.example/v1",
        )


# ── 12.3: transcribe capability provider 路由测试 ────────────────────────

from core.llm.aigc_model import AigcModel
from core.llm.deepseek_model import DeepSeekModel
from core.llm.gemini_model import GeminiModel
from core.llm.groq_model import GroqModel
from core.llm.local_model import LocalModel
from core.llm.qwen_model import QwenModel


def test_transcribe_routes_to_openai_by_default():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-test",
            "TRANSCRIBE_PROVIDER": "openai",
        },
        clear=False,
    ):
        model = get_model_for_capability("transcribe")
    assert isinstance(model, OpenAIModel)
    assert model.supports_transcribe is True


def test_transcribe_routes_to_aigc():
    with patch.dict(
        os.environ,
        {
            "AIGC_API_KEY": "aigc-app-key",
            "TRANSCRIBE_PROVIDER": "aigc",
        },
        clear=False,
    ):
        model = get_model_for_capability("transcribe")
    assert isinstance(model, AigcModel)
    assert model.supports_transcribe is True
    assert model.audio_chunk_size_bytes == 5 * 1024 * 1024


def test_transcribe_routes_to_groq():
    with patch.dict(
        os.environ,
        {
            "GROQ_API_KEY": "groq-key",
            "TRANSCRIBE_PROVIDER": "groq",
        },
        clear=False,
    ):
        model = get_model_for_capability("transcribe")
    assert isinstance(model, GroqModel)
    assert model.supports_transcribe is True
    assert model.max_audio_upload_bytes == 100 * 1024 * 1024


def test_transcribe_routes_to_qwen():
    with patch.dict(
        os.environ,
        {
            "QWEN_API_KEY": "qwen-key",
            "TRANSCRIBE_PROVIDER": "qwen",
        },
        clear=False,
    ):
        model = get_model_for_capability("transcribe")
    assert isinstance(model, QwenModel)
    assert model.supports_transcribe is True


def test_transcribe_routes_to_local():
    with patch.dict(
        os.environ,
        {
            "TRANSCRIBE_PROVIDER": "local",
        },
        clear=False,
    ):
        model = get_model_for_capability("transcribe")
    assert isinstance(model, LocalModel)
    assert model.supports_transcribe is True


def test_transcribe_raises_for_deepseek():
    """DeepSeek 不支持 transcribe，应抛出 ValueError。"""
    with patch.dict(
        os.environ,
        {
            "DEEPSEEK_API_KEY": "ds-key",
            "TRANSCRIBE_PROVIDER": "deepseek",
        },
        clear=False,
    ):
        with pytest.raises(ValueError, match="不支持 transcription"):
            get_model_for_capability("transcribe")


def test_transcribe_raises_for_gemini():
    """Gemini 不支持 transcribe，应抛出 ValueError。"""
    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "gemini-key",
            "TRANSCRIBE_PROVIDER": "gemini",
        },
        clear=False,
    ):
        with pytest.raises(ValueError, match="不支持 transcription"):
            get_model_for_capability("transcribe")


def test_transcribe_uses_transcribe_api_key_first():
    """TRANSCRIBE_API_KEY 优先级高于 AIGC_API_KEY。"""
    from core.llm import factory as factory_module

    with patch.object(factory_module, "AigcModel") as mock_aigc:
        mock_instance = MagicMock()
        mock_instance.supports_transcribe = True
        mock_aigc.return_value = mock_instance
        with patch.dict(
            os.environ,
            {
                "TRANSCRIBE_PROVIDER": "aigc",
                "TRANSCRIBE_API_KEY": "transcribe-specific-key",
                "AIGC_API_KEY": "generic-aigc-key",
            },
            clear=False,
        ):
            get_model_for_capability("transcribe")

        mock_aigc.assert_called_once()
        call_kwargs = mock_aigc.call_args.kwargs
        assert call_kwargs["api_key"] == "transcribe-specific-key"


def test_transcribe_model_name_per_provider():
    """各 provider 的默认 transcribe 模型名应正确（需清除 TRANSCRIBE_MODEL_NAME 以防环境覆盖）。"""
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-test",
            "TRANSCRIBE_PROVIDER": "openai",
            "TRANSCRIBE_MODEL_NAME": "",
            "TRANSCRIBER_MODEL": "",
        },
        clear=False,
    ):
        assert get_model_name_for_capability("transcribe") == "whisper-1"

    with patch.dict(
        os.environ,
        {
            "AIGC_API_KEY": "aigc-key",
            "TRANSCRIBE_PROVIDER": "aigc",
            "TRANSCRIBE_MODEL_NAME": "",
            "TRANSCRIBER_MODEL": "",
        },
        clear=False,
    ):
        assert get_model_name_for_capability("transcribe") == "fileasrrecorder"

    with patch.dict(
        os.environ,
        {
            "GROQ_API_KEY": "groq-key",
            "TRANSCRIBE_PROVIDER": "groq",
            "TRANSCRIBE_MODEL_NAME": "",
            "TRANSCRIBER_MODEL": "",
        },
        clear=False,
    ):
        assert get_model_name_for_capability("transcribe") == "whisper-large-v3-turbo"
