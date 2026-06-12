"""测试 GroqModel：初始化、transcribe_audio mock。"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.llm.groq_model import GroqModel


class TestGroqModelInit(unittest.TestCase):
    """6.1: GroqModel 初始化测试"""

    def test_init_with_api_key(self):
        model = GroqModel(api_key="gsk_test_key")
        self.assertIsNotNone(model._client)
        self.assertEqual(model._client.api_key, "gsk_test_key")

    def test_init_with_custom_base_url(self):
        model = GroqModel(api_key="gsk_test", base_url="https://custom.groq.example/v1")
        self.assertEqual(str(model._client.base_url).rstrip("/"), "https://custom.groq.example/v1")

    def test_init_default_base_url(self):
        model = GroqModel(api_key="gsk_test")
        self.assertEqual(str(model._client.base_url).rstrip("/"), "https://api.groq.com/openai/v1")

    def test_init_raises_for_empty_key(self):
        with self.assertRaises(ValueError):
            GroqModel(api_key="")

    def test_capability_declarations(self):
        model = GroqModel(api_key="gsk_test")
        self.assertTrue(model.supports_transcribe)
        self.assertEqual(model.max_audio_upload_bytes, 100 * 1024 * 1024)
        self.assertIsNone(model.audio_chunk_size_bytes)


class TestGroqModelTranscribe(unittest.TestCase):
    """6.2: GroqModel.transcribe_audio() 测试"""

    def setUp(self):
        self.test_dir = Path(__file__).parent / "test_temp_groq"
        self.test_dir.mkdir(exist_ok=True)
        self.test_audio = self.test_dir / "test_audio.mp3"
        self.test_audio.write_bytes(b"fake audio data")

    def tearDown(self):
        if self.test_dir.exists():
            import shutil
            shutil.rmtree(self.test_dir)

    def test_transcribe_audio_basic(self):
        """基本转录路径：mock OpenAI client 返回 Whisper 兼容响应。"""
        model = GroqModel(api_key="gsk_test")

        # mock OpenAI client 的 audio.transcriptions.create
        mock_transcript = MagicMock()
        mock_transcript.model_dump_json.return_value = json.dumps({
            "text": "Hello world.",
            "language": "en",
            "duration": 3.0,
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.5, "text": "Hello"},
                {"id": 1, "start": 1.6, "end": 3.0, "text": "world."},
            ],
        })

        with patch.object(model._client.audio.transcriptions, "create", return_value=mock_transcript) as mock_create:
            result = model.transcribe_audio(
                model="whisper-large-v3-turbo",
                audio_path=self.test_audio,
            )

        # 验证结果
        self.assertEqual(result.text, "Hello world.")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.duration, 3.0)
        self.assertEqual(len(result.segments), 2)

        # 验证 API 调用参数
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "whisper-large-v3-turbo")
        self.assertEqual(call_kwargs["response_format"], "verbose_json")
        self.assertEqual(call_kwargs["timestamp_granularities"], ["segment"])

    def test_transcribe_audio_custom_timestamp_granularities(self):
        """测试自定义 timestamp_granularities 参数。"""
        model = GroqModel(api_key="gsk_test")

        mock_transcript = MagicMock()
        mock_transcript.model_dump_json.return_value = json.dumps({
            "text": "Test.",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "Test."}],
        })

        with patch.object(model._client.audio.transcriptions, "create", return_value=mock_transcript) as mock_create:
            model.transcribe_audio(
                model="whisper-large-v3",
                audio_path=self.test_audio,
                timestamp_granularities=["word", "segment"],
            )

        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["timestamp_granularities"], ["word", "segment"])

    def test_transcribe_audio_empty_none_granularities(self):
        """timestamp_granularities=None 时使用默认值。"""
        model = GroqModel(api_key="gsk_test")

        mock_transcript = MagicMock()
        mock_transcript.model_dump_json.return_value = json.dumps({
            "text": "x", "segments": [],
        })

        with patch.object(model._client.audio.transcriptions, "create", return_value=mock_transcript) as mock_create:
            model.transcribe_audio(
                model="whisper-large-v3",
                audio_path=self.test_audio,
                timestamp_granularities=None,
            )

        # 应使用默认值
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["timestamp_granularities"], ["segment"])

    def test_transcribe_audio_response_format_not_verbose_json_skips_granularities(self):
        """response_format 不是 verbose_json 时不应传 timestamp_granularities。"""
        model = GroqModel(api_key="gsk_test")

        mock_transcript = MagicMock()
        mock_transcript.model_dump_json.return_value = json.dumps({"text": "x"})
        # text format 直接返回 model_dump_json 的结果 — 但这不重要因为
        # 我们只关心 create_kwargs

        with patch.object(model._client.audio.transcriptions, "create", return_value=mock_transcript) as mock_create:
            model.transcribe_audio(
                model="whisper-large-v3",
                audio_path=self.test_audio,
                response_format="text",
            )

        call_kwargs = mock_create.call_args.kwargs
        self.assertNotIn("timestamp_granularities", call_kwargs)


if __name__ == "__main__":
    unittest.main()
