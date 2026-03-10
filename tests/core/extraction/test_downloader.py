
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil
import sys
import os

# 将项目根目录添加到 sys.path
# 这对于在命令行中直接运行测试脚本是必要的
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from core.extraction.downloader import VideoDownloader

class TestVideoDownloader(unittest.TestCase):

    def setUp(self):
        """在每个测试开始前，创建一个临时的测试目录"""
        self.test_dir = Path("./test_temp_videos")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(exist_ok=True)
        self.downloader = VideoDownloader(output_dir=self.test_dir)

    def tearDown(self):
        """在每个测试结束后，清理临时目录"""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch('core.extraction.downloader.yt_dlp.YoutubeDL')
    def test_download_success(self, mock_youtube_dl):
        """测试视频下载成功的情况"""
        # --- 准备模拟 ---
        video_url = "https://www.youtube.com/watch?v=test"
        video_id = "test"
        video_ext = "mp4"
        expected_filename = f"{video_id}.{video_ext}"
        expected_filepath = self.test_dir / expected_filename

        # 模拟 yt_dlp.YoutubeDL 的实例
        mock_instance = MagicMock()
        # 配置上下文管理器 (__enter__) 返回我们的 mock 实例
        mock_youtube_dl.return_value.__enter__.return_value = mock_instance

        # 模拟 extract_info 返回的字典
        mock_instance.extract_info.return_value = {
            'id': video_id,
            'ext': video_ext,
        }
        # 模拟 prepare_filename 返回最终的文件路径
        mock_instance.prepare_filename.return_value = str(expected_filepath)

        # --- 执行测试 ---
        result_path = self.downloader.download(video_url)

        # --- 断言 ---
        # 验证返回的路径是否正确
        self.assertEqual(result_path, expected_filepath)

        # 验证 YoutubeDL 是否以正确的参数被实例化
        mock_youtube_dl.assert_called_once()
        
        # 获取调用参数
        # call_args 返回 (args, kwargs)
        # YoutubeDL(ydl_opts) -> ydl_opts 是第一个位置参数
        args, _ = mock_youtube_dl.call_args
        ydl_opts = args[0]

        # 验证 ydl_opts 中的配置
        self.assertIn('format', ydl_opts)
        self.assertEqual(ydl_opts['format'], 'best[ext=mp4]')
        self.assertIn('outtmpl', ydl_opts)
        # 验证 outtmpl 是否包含我们的临时目录路径
        # 注意：outtmpl 是一个字符串，我们检查它是否包含目录路径
        self.assertIn(str(self.test_dir), ydl_opts['outtmpl'])

        # 验证核心方法是否被调用
        mock_instance.extract_info.assert_called_once_with(video_url, download=True)
        mock_instance.prepare_filename.assert_called_once()

if __name__ == '__main__':
    unittest.main()
