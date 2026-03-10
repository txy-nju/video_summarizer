
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil
import sys
import os

# 将项目根目录添加到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from core.extraction.transcriber import AudioTranscriber

class TestAudioTranscriber(unittest.TestCase):

    def setUp(self):
        """创建测试所需的临时目录和文件"""
        # 【修复】将临时目录创建在测试文件所在的目录下，确保路径正确
        self.test_audio_dir = Path(__file__).parent / "test_temp_transcriber_audio"
        
        if self.test_audio_dir.exists():
            shutil.rmtree(self.test_audio_dir)
        self.test_audio_dir.mkdir()

        self.dummy_audio_path = self.test_audio_dir / "dummy_audio.mp3"
        # 创建一个真实的空文件，避免 FileNotFoundError
        self.dummy_audio_path.touch()

        # 使用假的 API Key，因为我们 mock 了 OpenAI 客户端，不会真的发起请求
        self.api_key = "test_api_key_placeholder"

    def tearDown(self):
        """清理测试产生的临时目录"""
        if self.test_audio_dir.exists():
            shutil.rmtree(self.test_audio_dir)

    @patch('core.extraction.transcriber.openai.OpenAI')
    def test_transcribe(self, mock_openai_client):
        """测试 transcribe 方法"""
        # --- 准备模拟 ---
        # 1. 模拟 OpenAI 客户端实例
        mock_instance = MagicMock()
        mock_openai_client.return_value = mock_instance
        
        # 2. 准备一个假的 VTT 格式返回结果
        expected_vtt_result = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nHello, world."
        
        # 模拟 client.audio.transcriptions.create 的返回值
        mock_instance.audio.transcriptions.create.return_value = expected_vtt_result

        # --- 执行测试 ---
        transcriber = AudioTranscriber(api_key=self.api_key)
        
        # 直接调用，不 mock open，让它读取真实的临时文件
        result = transcriber.transcribe(self.dummy_audio_path)

        # --- 断言 ---
        # 1. 验证 OpenAI 客户端是否用提供的 API Key 和默认的 base_url 初始化
        mock_openai_client.assert_called_once_with(api_key=self.api_key, base_url=None)

        # 2. 验证 transcriptions.create 方法是否被正确调用
        mock_instance.audio.transcriptions.create.assert_called_once()
        
        # 3. 检查调用参数
        _, kwargs = mock_instance.audio.transcriptions.create.call_args
        
        self.assertEqual(kwargs['model'], 'whisper-1')
        self.assertEqual(kwargs['response_format'], 'vtt')
        
        # 验证传递给API的文件参数
        # 检查它是否是一个打开的文件对象，并且文件名正确
        self.assertTrue(hasattr(kwargs['file'], 'read'), "传递给 API 的 file 参数应该是一个文件对象")
        self.assertEqual(kwargs['file'].name, str(self.dummy_audio_path))

        # 4. 验证方法的返回值是否是模拟的 VTT 结果
        self.assertEqual(result, expected_vtt_result)

if __name__ == '__main__':
    unittest.main()
