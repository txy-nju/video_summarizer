
import unittest
import json
import os
import base64
from pathlib import Path
from unittest.mock import patch
import sys  # 【修复】导入 sys 模块

# 将项目根目录添加到 sys.path
# 注意：路径深度已根据文件位置调整
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# 导入 settings 会自动加载 .env 文件
from config import settings
from services.workflow_service import VideoSummaryService

class TestWorkflowIntegration(unittest.TestCase):

    def setUp(self):
        """准备测试环境"""
        # 1. 读取测试数据源
        self.data_file = Path(__file__).parent.parent / "data" / "video_sources.json"
        if not self.data_file.exists():
            self.skipTest("测试数据文件 video_sources.json 不存在")
        
        with open(self.data_file, "r", encoding="utf-8") as f:
            self.video_sources = json.load(f)

        # 2. 准备输出目录
        self.output_base_dir = settings.BASE_DIR / "test_output"
        self.output_base_dir.mkdir(exist_ok=True)

        # 3. 从环境变量获取 API Key 和 Base URL
        # settings.py 已经帮我们加载了 .env 文件
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL")

        if not self.api_key:
            self.skipTest("环境变量 OPENAI_API_KEY 未设置，跳过集成测试")

    # 为了进行真实的集成测试，我们不再 mock transcribe
    # @patch('core.extraction.transcriber.AudioTranscriber.transcribe')
    def test_extract_content_from_real_videos(self):
        """
        集成测试：从真实 YouTube 视频提取内容。
        该测试会真实调用 OpenAI API，请确保已设置 OPENAI_API_KEY 和 OPENAI_BASE_URL。
        """
        service = VideoSummaryService(api_key=self.api_key)
        # 确认 service 也获取到了 base_url
        self.assertEqual(service.base_url, self.base_url)

        for video_data in self.video_sources:
            url = video_data["url"]
            description = video_data.get("description", "No description")
            print(f"\nTesting video: {description} ({url})")

            try:
                # 调用核心提取函数
                transcript, frames = service.extract_content_from_url(url)

                # --- 验证与保存结果 ---
                
                # 1. 创建该视频的输出子目录
                video_id = url.split("v=")[-1]
                video_output_dir = self.output_base_dir / video_id
                video_output_dir.mkdir(exist_ok=True)

                # 2. 保存转录文本
                transcript_path = video_output_dir / "transcript.txt"
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(transcript)
                print(f"Saved transcript to: {transcript_path}")

                # 3. 保存关键帧图片
                frames_dir = video_output_dir / "frames"
                frames_dir.mkdir(exist_ok=True)
                
                for i, frame_b64 in enumerate(frames):
                    frame_data = base64.b64decode(frame_b64)
                    frame_path = frames_dir / f"frame_{i:03d}.jpg"
                    with open(frame_path, "wb") as f:
                        f.write(frame_data)
                print(f"Saved {len(frames)} frames to: {frames_dir}")

                # --- 断言 ---
                self.assertIsNotNone(transcript)
                self.assertTrue(len(transcript) > 0)
                self.assertIsNotNone(frames)
                self.assertTrue(len(frames) > 0)

            except Exception as e:
                self.fail(f"Failed to process video {url}: {e}")

if __name__ == '__main__':
    unittest.main()
