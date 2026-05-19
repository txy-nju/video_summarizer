import os
from unittest.mock import patch

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
