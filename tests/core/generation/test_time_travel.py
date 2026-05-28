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


if __name__ == "__main__":
    unittest.main()
