import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil
import sys
import os
import math
import numpy as np

# 将项目根目录添加到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')))

from core.extraction.infrastructure.extractor import MediaExtractor

class TestMediaExtractor(unittest.TestCase):

    def setUp(self):
        """创建测试所需的临时目录和文件"""
        self.test_output_audio_dir = Path("./test_temp_audios")
        self.test_input_video_dir = Path("./test_temp_videos_input")
        
        for d in [self.test_output_audio_dir, self.test_input_video_dir]:
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        self.extractor = MediaExtractor(audio_dir=self.test_output_audio_dir)
        
        self.dummy_video_path = self.test_input_video_dir / "dummy_video.mp4"
        self.dummy_video_path.touch()

    def tearDown(self):
        """清理测试产生的临时目录"""
        for d in [self.test_output_audio_dir, self.test_input_video_dir]:
            if d.exists():
                shutil.rmtree(d)

    # ==========================
    # extract_audio 健壮性测试
    # ==========================

    @patch('core.extraction.infrastructure.extractor.VideoFileClip')
    def test_extract_audio_normal(self, mock_video_file_clip):
        """正常情况：提取音频成功"""
        mock_clip_instance = MagicMock()
        mock_video_file_clip.return_value.__enter__.return_value = mock_clip_instance
        mock_audio = MagicMock()
        mock_clip_instance.audio = mock_audio

        result_path = self.extractor.extract_audio(self.dummy_video_path)

        mock_video_file_clip.assert_called_once_with(str(self.dummy_video_path))
        expected_audio_path = self.test_output_audio_dir / "dummy_video.mp3"
        mock_audio.write_audiofile.assert_called_once_with(
            str(expected_audio_path), 
            codec='mp3', 
            logger=None
        )
        self.assertEqual(result_path, expected_audio_path)

    @patch('core.extraction.infrastructure.extractor.VideoFileClip')
    def test_extract_audio_no_audio_track(self, mock_video_file_clip):
        """边界情况：视频无音轨 (clip.audio 为 None)，应优雅返回 None 而不是崩溃"""
        mock_clip_instance = MagicMock()
        mock_video_file_clip.return_value.__enter__.return_value = mock_clip_instance
        mock_clip_instance.audio = None  # 强行设为无音轨

        result_path = self.extractor.extract_audio(self.dummy_video_path)

        self.assertIsNone(result_path, "当没有音轨时，系统应当拦截属性调用并优雅返回 None")

    def test_extract_audio_file_not_found(self):
        """边界情况：文件不存在，应当提前抛出明确的 FileNotFoundError"""
        non_existent_path = self.test_input_video_dir / "ghost.mp4"
        with self.assertRaises(FileNotFoundError):
            self.extractor.extract_audio(non_existent_path)


    # ==========================
    # extract_frames 健壮性测试
    # ==========================

    def test_extract_frames_file_not_found(self):
        """边界情况：文件不存在，应当抛出 FileNotFoundError"""
        non_existent_path = self.test_input_video_dir / "ghost.mp4"
        with self.assertRaises(FileNotFoundError):
            self.extractor.extract_frames(non_existent_path)

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_scene_detection(self, mock_video_capture, mock_imencode):
        """一般情况：测试 extract_frames 的基于灰度直方图的场景剧变检测逻辑"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.return_value = 10.0

        total_frames = 50
        scene_A = np.zeros((100, 100, 3), dtype=np.uint8) # 全黑
        scene_B = np.ones((100, 100, 3), dtype=np.uint8) * 255 # 全白
        
        retrieve_side_effect = []
        for i in range(total_frames):
            if i < 20:
                retrieve_side_effect.append((True, scene_A))
            else:
                retrieve_side_effect.append((True, scene_B))

        mock_cap_instance.grab.side_effect = [True] * total_frames + [False]
        mock_cap_instance.retrieve.side_effect = retrieve_side_effect
        mock_imencode.return_value = (True, np.array([1, 2, 3]))
        
        frames = self.extractor.extract_frames(
            self.dummy_video_path,
            interval=1,
            max_interval=3,
            threshold=0.90,
            probe_fps=10,
        )

        expected_extracted_count = 2 # 0s 和 2s
        self.assertEqual(mock_imencode.call_count, expected_extracted_count)
        self.assertEqual(len(frames), expected_extracted_count)
        self.assertEqual(frames[0]["time"], "00:00")
        self.assertEqual(frames[1]["time"], "00:02")
        self.assertIn("scene_change_score", frames[0])
        self.assertIn("scene_change_level", frames[0])
        self.assertEqual(frames[0]["scene_change_level"], "none")
        self.assertEqual(frames[1]["scene_change_level"], "severe")

    def test_scene_change_threshold_adapts_by_duration_bucket(self):
        short = self.extractor._resolve_severe_threshold(300)
        medium = self.extractor._resolve_severe_threshold(1200)
        long = self.extractor._resolve_severe_threshold(3600)

        self.assertGreater(short, medium)
        self.assertGreater(medium, long)

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_short_video(self, mock_video_capture, mock_imencode):
        """边界情况：极短视频（只有 1 帧，0.1秒），确保至少提取首帧"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.return_value = 10.0

        scene = np.zeros((100, 100, 3), dtype=np.uint8) 
        mock_cap_instance.grab.side_effect = [True, False]
        mock_cap_instance.retrieve.side_effect = [(True, scene)]
        mock_imencode.return_value = (True, np.array([1]))

        frames = self.extractor.extract_frames(self.dummy_video_path, interval=2, max_interval=60)

        self.assertEqual(len(frames), 1, "极短视频也必须保证抽取首帧作为兜底")
        self.assertEqual(frames[0]["time"], "00:00")

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_abnormal_fps(self, mock_video_capture, mock_imencode):
        """边界情况：异常的 FPS (NaN 或 0)，应退推至 30.0 默认值，防止除零错误崩溃"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        
        # 模拟各种破损视频头引发的恶劣参数
        for bad_fps in [0.0, -5.0, math.nan, None]:
            mock_cap_instance.get.return_value = bad_fps 

            scene = np.zeros((10, 10, 3), dtype=np.uint8) 
            mock_cap_instance.grab.side_effect = [True, False]
            mock_cap_instance.retrieve.side_effect = [(True, scene)]
            mock_imencode.return_value = (True, np.array([1]))

            frames = self.extractor.extract_frames(self.dummy_video_path)

            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0]["time"], "00:00")

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_stream_interrupt(self, mock_video_capture, mock_imencode):
        """边界情况：视频流中断或尾部破损，read() 突然返回 False, 程序不应崩溃，必须保存已抽取的成果"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.return_value = 10.0 

        scene = np.zeros((100, 100, 3), dtype=np.uint8) 
        # 前 15 帧正常，第 16 帧 grab 失败中断
        mock_cap_instance.grab.side_effect = [True for _ in range(15)] + [False]
        mock_cap_instance.retrieve.side_effect = [(True, scene) for _ in range(15)]
        mock_imencode.return_value = (True, np.array([1]))

        # 使用 max_interval=1 迫使系统在 1s 处抽第二帧
        frames = self.extractor.extract_frames(self.dummy_video_path, max_interval=1)
        
        self.assertEqual(len(frames), 2, "即使发生断流，也必须安全退出并返回断流前成功抽取的帧集合")
        self.assertEqual(frames[0]["time"], "00:00")
        self.assertEqual(frames[1]["time"], "00:01")

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_long_video_timestamp(self, mock_video_capture, mock_imencode):
        """边界情况：超长视频 (> 1小时)，时间戳必须自适应升维为 HH:MM:SS 格式"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        
        # 黑客级技巧：使用一个极低的极小 FPS，使得在第二帧的 current_time 瞬间跨越 1 小时
        fps = 1.0 / 3665.0  # 1 帧代表 3665 秒
        mock_cap_instance.get.return_value = fps

        scene = np.zeros((10, 10, 3), dtype=np.uint8) 
        mock_cap_instance.grab.side_effect = [True, True, False]
        mock_cap_instance.retrieve.side_effect = [(True, scene), (True, scene)]
        mock_imencode.return_value = (True, np.array([1]))

        # 迫使兜底机制起效
        frames = self.extractor.extract_frames(self.dummy_video_path, max_interval=1)
        
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["time"], "00:00")
        self.assertEqual(frames[1]["time"], "01:01:05", "超过1小时的时间戳必须格式化为 HH:MM:SS")

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_first_frame_fail(self, mock_video_capture, mock_imencode):
        """边界情况：首帧读取失败（视频完全空或严重损坏），应退推并返回空列表 []"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.return_value = 30.0

        mock_cap_instance.grab.return_value = False

        frames = self.extractor.extract_frames(self.dummy_video_path)

        self.assertEqual(frames, [], "首帧都读不到时，不应崩溃，必须兜底返回空列表")

    @patch('core.extraction.infrastructure.extractor.cv2.imencode')
    @patch('core.extraction.infrastructure.extractor.cv2.VideoCapture')
    def test_extract_frames_auto_probe_fps_for_long_video(self, mock_video_capture, mock_imencode):
        """长视频自动档应降到 1fps 探测，减少 retrieve 次数。"""
        mock_cap_instance = MagicMock()
        mock_video_capture.return_value = mock_cap_instance
        mock_cap_instance.isOpened.return_value = True

        # 30min, 30fps -> 54000 frames，自动档应取 1fps => stride=30
        fps = 30.0
        total_frames = 54000.0

        def fake_get(prop_id):
            if prop_id == 5:  # cv2.CAP_PROP_FPS
                return fps
            if prop_id == 7:  # cv2.CAP_PROP_FRAME_COUNT
                return total_frames
            return 0.0

        mock_cap_instance.get.side_effect = fake_get
        mock_cap_instance.grab.side_effect = [True] * 60 + [False]
        scene = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_cap_instance.retrieve.side_effect = [(True, scene), (True, scene)]
        mock_imencode.return_value = (True, np.array([1]))

        _ = self.extractor.extract_frames(self.dummy_video_path, interval=1, max_interval=100)

        # 0 和 30 帧命中探测点，retrieve 调用应为 2 次（其余帧仅 grab）
        self.assertEqual(mock_cap_instance.retrieve.call_count, 2)

if __name__ == '__main__':
    unittest.main()