
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil
import sys
import os
import cv2
import numpy as np
import base64

# 将项目根目录添加到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')))

from core.extraction.infrastructure.extractor import MediaExtractor

class TestMediaExtractor(unittest.TestCase):

    def setUp(self):
        """创建测试所需的临时目录和文件"""
        self.test_output_audio_dir = Path("./test_temp_audios")
        self.test_input_video_dir = Path("./test_temp_videos_input")
        
        # 清理并创建目录
        for d in [self.test_output_audio_dir, self.test_input_video_dir]:
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        # 实例化 MediaExtractor，指定输出目录
        self.extractor = MediaExtractor(audio_dir=self.test_output_audio_dir)
        
        # 创建一个假的视频文件作为输入
        self.dummy_video_path = self.test_input_video_dir / "dummy_video.mp4"
        self.dummy_video_path.touch() # 创建一个空文件

    def tearDown(self):
        """清理测试产生的临时目录"""
        for d in [self.test_output_audio_dir, self.test_input_video_dir]:
            if d.exists():
                shutil.rmtree(d)

    @patch('core.extraction.infrastructure.extractor.VideoFileClip')
    def test_extract_audio(self, mock_video_file_clip):
        """测试 extract_audio 方法"""
        # --- 准备模拟 ---
        # 模拟 VideoFileClip 的上下文管理器 (__enter__)
        mock_clip_instance = MagicMock()
        mock_video_file_clip.return_value.__enter__.return_value = mock_clip_instance
        
        # 模拟 audio 属性和其 write_audiofile 方法
        mock_audio = MagicMock()
        mock_clip_instance.audio = mock_audio

        # --- 执行测试 ---
        result_path = self.extractor.extract_audio(self.dummy_video_path)

        # --- 断言 ---
        # 1. 验证 VideoFileClip 是否用正确的视频路径实例化
        mock_video_file_clip.assert_called_once_with(str(self.dummy_video_path))

        # 2. 构造预期的输出路径
        expected_audio_path = self.test_output_audio_dir / "dummy_video.mp3"

        # 3. 验证 write_audiofile 是否用正确的输出路径和参数调用
        mock_audio.write_audiofile.assert_called_once_with(
            str(expected_audio_path), 
            codec='mp3', 
            logger=None
        )

        # 4. 验证方法的返回值是否是预期的输出路径
        self.assertEqual(result_path, expected_audio_path)

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames(self, mock_video_capture, mock_imencode):
        """测试 extract_frames 方法"""
        # --- 准备模拟 ---
        # 1. 模拟 VideoCapture
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        
        # 2. 【修改】模拟真实的 FPS = 30
        fps = 30
        mock_cap_instance.get.return_value = fps

        # 3. 【修改】模拟 read() 返回值
        # 构造一个包含 61 帧的视频流 (0 到 60 帧)，足以覆盖 0秒, 1秒, 2秒 三个时间点
        total_frames = 61
        fake_image = np.zeros((100, 100, 3), dtype=np.uint8)
        # 前 61 次调用返回 (True, image)，第 62 次调用返回 (False, None) 表示结束
        side_effect = [(True, fake_image) for _ in range(total_frames)]
        side_effect.append((False, None))
        
        mock_cap_instance.read.side_effect = side_effect

        # 4. 模拟 imencode
        fake_encoded_buffer = (True, np.array([1, 2, 3]))
        mock_imencode.return_value = fake_encoded_buffer
        
        # --- 执行测试 ---
        # 设置抽帧间隔为 1 秒
        # 预期抽帧点：第 0 帧 (0s), 第 30 帧 (1s), 第 60 帧 (2s)
        interval_seconds = 1
        frames = self.extractor.extract_frames(self.dummy_video_path, interval=interval_seconds)

        # --- 断言 ---
        # 1. 验证 VideoCapture 被正确调用
        mock_video_capture.assert_called_once_with(str(self.dummy_video_path))
        
        # 2. 验证 get(FPS) 被调用
        mock_cap_instance.get.assert_called_once_with(cv2.CAP_PROP_FPS)

        # 3. 验证 read() 被调用次数 (61帧 + 1次结束信号 = 62次)
        self.assertEqual(mock_cap_instance.read.call_count, total_frames + 1)

        # 4. 【关键验证】验证 imencode 被调用的次数
        # 应该只在第 0, 30, 60 帧调用，共 3 次
        expected_extracted_count = 3
        self.assertEqual(mock_imencode.call_count, expected_extracted_count)
        
        # 5. 验证返回的帧列表长度
        self.assertEqual(len(frames), expected_extracted_count)
        
        # 6. 验证每个返回的元素都是正确的 base64 字符串
        expected_b64_string = base64.b64encode(fake_encoded_buffer[1]).decode('utf-8')
        for frame in frames:
            self.assertEqual(frame, expected_b64_string)

        # 7. 验证 release() 被调用
        mock_cap_instance.release.assert_called_once()

if __name__ == '__main__':
    unittest.main()
