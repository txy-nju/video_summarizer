import unittest

from core.workflow.time_travel import (
    parse_timestamp_to_seconds,
    find_nearest_keyframe,
    extract_transcript_window,
)


class TestTimeTravelUtils(unittest.TestCase):
    def test_parse_timestamp_to_seconds(self):
        self.assertEqual(parse_timestamp_to_seconds("00:15"), 15)
        self.assertEqual(parse_timestamp_to_seconds("1:30"), 90)
        self.assertEqual(parse_timestamp_to_seconds("01:02:03"), 3723)
        self.assertEqual(parse_timestamp_to_seconds("99:99"), 6039)

    def test_parse_timestamp_to_seconds_invalid(self):
        with self.assertRaises(ValueError):
            parse_timestamp_to_seconds("")
        with self.assertRaises(ValueError):
            parse_timestamp_to_seconds("1-2-3")

    def test_find_nearest_keyframe(self):
        keyframes = [
            {"time": "00:05", "image": "a"},
            {"time": "00:20", "image": "b"},
            {"time": "00:45", "image": "c"},
        ]
        nearest = find_nearest_keyframe(keyframes, 18)
        self.assertIsNotNone(nearest)
        self.assertEqual(nearest["time"], "00:20")

    def test_find_nearest_keyframe_multi_frame_mode(self):
        """测试多帧模式：返回时间窗口内的多个代表性关键帧"""
        keyframes = [
            {"time": "00:00", "image": "a"},
            {"time": "00:10", "image": "b"},
            {"time": "00:20", "image": "c"},
            {"time": "00:30", "image": "d"},
            {"time": "00:40", "image": "e"},
            {"time": "00:50", "image": "f"},
        ]
        # 时间窗口：[30, 30+20] = [30, 50]，应该返回 3 帧
        result = find_nearest_keyframe(keyframes, 30, window_seconds=20)
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 3)
        self.assertLessEqual(len(result), 5)
        # 验证返回的帧都在窗口范围内
        for frame in result:
            frame_time = int(frame["time"].split(":")[1])
            self.assertGreaterEqual(frame_time, 30)
            self.assertLessEqual(frame_time, 50)

    def test_find_nearest_keyframe_multi_frame_empty_window(self):
        """测试多帧模式：窗口内无帧时返回空列表"""
        keyframes = [
            {"time": "00:05", "image": "a"},
            {"time": "00:10", "image": "b"},
        ]
        # 目标时间在很远的地方，窗口内无帧
        result = find_nearest_keyframe(keyframes, 100, window_seconds=10)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_extract_transcript_window_verbose_json(self):
        transcript = """
        {
          "segments": [
            {"start": 0.0, "end": 3.0, "text": "hello"},
            {"start": 10.0, "end": 12.0, "text": "target segment"},
            {"start": 40.0, "end": 45.0, "text": "far away"}
          ]
        }
        """
        window = extract_transcript_window(transcript, target_seconds=11, window_seconds=5)
        self.assertIn("target segment", window)
        self.assertNotIn("far away", window)

    def test_extract_transcript_window_plain_text_fallback(self):
        transcript = "this is plain transcript"
        window = extract_transcript_window(transcript, target_seconds=10, window_seconds=5)
        self.assertIn("plain transcript", window)

    # ── 12.5: AIGC 来源 segments 兼容性测试 ─────────────────────────────

    def test_extract_transcript_window_aigc_format_compatible(self):
        """AIGC 转录结果（language=""、segments 由 bg/ed 毫秒转 start/end 秒）
        应被 extract_transcript_window 正确解析。"""
        # 模拟 TranscriptionResult.to_json() 输出的 AIGC 格式结果
        transcript = """
        {
          "text": "第一段内容。 第二段内容。 第三段内容。",
          "language": "",
          "duration": 30.0,
          "segments": [
            {"id": 0, "start": 0.0, "end": 5.0, "text": "第一段内容。"},
            {"id": 1, "start": 12.0, "end": 18.0, "text": "第二段内容。"},
            {"id": 2, "start": 25.0, "end": 30.0, "text": "第三段内容。"}
          ]
        }
        """
        # 时间窗 [3, 8] 只命中第一段的尾部，不应包含第二段
        window = extract_transcript_window(transcript, target_seconds=3, window_seconds=5)
        self.assertIn("第一段内容", window)
        self.assertNotIn("第二段内容", window)
        self.assertNotIn("第三段内容", window)

    def test_extract_transcript_window_aigc_empty_language(self):
        """language 为空字符串不应导致解析失败。"""
        transcript = """
        {
          "text": "content",
          "language": "",
          "duration": 10.0,
          "segments": [
            {"id": 0, "start": 0.0, "end": 10.0, "text": "content"}
          ]
        }
        """
        window = extract_transcript_window(transcript, target_seconds=5, window_seconds=10)
        self.assertIn("content", window)

    def test_extract_transcript_window_empty_segments_with_text_fallback(self):
        """segments 为空但有 text 字段时应回退到 text。"""
        transcript = """
        {
          "text": "fallback text here",
          "language": "en",
          "duration": 5.0,
          "segments": []
        }
        """
        window = extract_transcript_window(transcript, target_seconds=0, window_seconds=10)
        self.assertIn("fallback text here", window)

    def test_extract_transcript_window_overlap_boundary(self):
        """segment 与时间窗有重叠时（非完全包含），也应被纳入。"""
        transcript = """
        {
          "text": "overlap test",
          "language": "en",
          "duration": 20.0,
          "segments": [
            {"id": 0, "start": 8.0, "end": 12.0, "text": "overlapping segment"}
          ]
        }
        """
        # 时间窗 [10, 15]，segment [8, 12] 有重叠
        window = extract_transcript_window(transcript, target_seconds=10, window_seconds=5)
        self.assertIn("overlapping segment", window)


if __name__ == "__main__":
    unittest.main()
