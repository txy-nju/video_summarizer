"""测试 TranscriptionResult 数据结构：各 provider 格式转换、序列化 round-trip、边界情况。"""

import json
import unittest

from core.llm.transcription_result import TranscriptionResult, TranscriptionSegment


class TestTranscriptionResultFromWhisper(unittest.TestCase):
    """1.3: from_whisper_verbose_json 解析测试"""

    def test_parse_normal_whisper_response(self):
        data = {
            "text": "Hello world.",
            "language": "en",
            "duration": 5.5,
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.0, "text": "Hello"},
                {"id": 1, "start": 2.1, "end": 5.0, "text": "world."},
            ],
        }
        result = TranscriptionResult.from_whisper_verbose_json(data)
        self.assertEqual(result.text, "Hello world.")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.duration, 5.5)
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].text, "Hello")
        self.assertEqual(result.segments[1].start, 2.1)

    def test_parse_with_missing_segments(self):
        data = {"text": "No segments", "language": "zh"}
        result = TranscriptionResult.from_whisper_verbose_json(data)
        self.assertEqual(result.text, "No segments")
        self.assertEqual(result.segments, [])

    def test_parse_segments_with_none(self):
        data = {"text": "x", "segments": None}
        result = TranscriptionResult.from_whisper_verbose_json(data)
        self.assertEqual(result.segments, [])

    def test_parse_empty_dict(self):
        result = TranscriptionResult.from_whisper_verbose_json({})
        self.assertEqual(result.text, "")
        self.assertEqual(result.language, "")
        self.assertEqual(result.duration, 0.0)
        self.assertEqual(result.segments, [])


class TestTranscriptionResultFromAigc(unittest.TestCase):
    """1.4: from_aigc_lasr_response 解析测试"""

    def test_parse_normal_aigc_response(self):
        data = {
            "result": [
                {"onebest": "播放歌曲摇篮曲。", "bg": 0, "ed": 2190, "speaker": 1},
                {"onebest": "接下来是新闻时间。", "bg": 2200, "ed": 5000, "speaker": 2},
            ]
        }
        result = TranscriptionResult.from_aigc_lasr_response(data)

        self.assertEqual(result.text, "播放歌曲摇篮曲。 接下来是新闻时间。")
        self.assertEqual(result.language, "")  # AIGC 不返回 language
        self.assertEqual(result.duration, 5.0)  # 最后一段 ed/1000

        self.assertEqual(len(result.segments), 2)
        # bg/ed 毫秒 -> start/end 秒
        self.assertEqual(result.segments[0].start, 0.0)
        self.assertEqual(result.segments[0].end, 2.19)
        self.assertEqual(result.segments[0].text, "播放歌曲摇篮曲。")
        self.assertEqual(result.segments[1].start, 2.2)
        self.assertEqual(result.segments[1].end, 5.0)
        self.assertEqual(result.segments[1].text, "接下来是新闻时间。")

    def test_parse_aigc_bg_ed_precision(self):
        """bg/ed 毫秒转换精度：确保 float 秒精度在 3 位小数以内。"""
        data = {
            "result": [
                {"onebest": "测试", "bg": 1234, "ed": 5678},
            ]
        }
        result = TranscriptionResult.from_aigc_lasr_response(data)
        self.assertEqual(result.segments[0].start, 1.234)
        self.assertEqual(result.segments[0].end, 5.678)

    def test_parse_aigc_empty_result(self):
        result = TranscriptionResult.from_aigc_lasr_response({})
        self.assertEqual(result.text, "")
        self.assertEqual(result.language, "")
        self.assertEqual(result.duration, 0.0)
        self.assertEqual(result.segments, [])

    def test_parse_aigc_result_none(self):
        result = TranscriptionResult.from_aigc_lasr_response({"result": None})
        self.assertEqual(result.segments, [])

    def test_parse_aigc_empty_text_skipped(self):
        """空 onebest 的 segment 应保留在 segments 中但不参与 text 拼接。"""
        data = {
            "result": [
                {"onebest": "好的", "bg": 0, "ed": 1000},
                {"onebest": "", "bg": 1200, "ed": 1800},
                {"onebest": "   ", "bg": 2000, "ed": 3000},
            ]
        }
        result = TranscriptionResult.from_aigc_lasr_response(data)
        # 空文本和纯空格不应出现在合并 text 中
        self.assertEqual(result.text, "好的")
        # 但 segments 仍保留
        self.assertEqual(len(result.segments), 3)

    def test_parse_aigc_non_dict_items_skipped(self):
        data = {"result": [{"onebest": "A", "bg": 0, "ed": 1000}, "not_a_dict", None]}
        result = TranscriptionResult.from_aigc_lasr_response(data)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.text, "A")


class TestTranscriptionResultFromParaformer(unittest.TestCase):
    """1.5: from_paraformer_response 解析测试"""

    def test_parse_normal_paraformer_response(self):
        data = {
            "sentences": [
                {"begin_time": 760, "end_time": 3240, "text": "Hello World", "sentence_id": 1},
                {"begin_time": 3500, "end_time": 6000, "text": "This is a test", "sentence_id": 2},
            ]
        }
        result = TranscriptionResult.from_paraformer_response(data)

        self.assertEqual(result.text, "Hello World This is a test")
        self.assertEqual(len(result.segments), 2)
        # begin_time/end_time 毫秒 -> start/end 秒
        self.assertEqual(result.segments[0].start, 0.76)
        self.assertEqual(result.segments[0].end, 3.24)
        self.assertEqual(result.segments[0].id, 1)  # sentence_id
        self.assertEqual(result.segments[1].start, 3.5)
        self.assertEqual(result.segments[1].end, 6.0)
        self.assertEqual(result.duration, 6.0)

    def test_parse_paraformer_language_field(self):
        data = {"sentences": [], "language": "zh"}
        result = TranscriptionResult.from_paraformer_response(data)
        self.assertEqual(result.language, "zh")

    def test_parse_paraformer_empty(self):
        result = TranscriptionResult.from_paraformer_response({})
        self.assertEqual(result.text, "")
        self.assertEqual(result.duration, 0.0)


class TestTranscriptionResultSerialization(unittest.TestCase):
    """1.2/1.6: to_json / from_json round-trip 测试"""

    def test_to_json_produces_whisper_compatible_format(self):
        result = TranscriptionResult(
            text="Hello",
            language="en",
            duration=5.0,
            segments=[
                TranscriptionSegment(id=0, start=0.0, end=2.0, text="Hello"),
            ],
        )
        json_str = result.to_json()
        parsed = json.loads(json_str)

        # Whisper verbose_json 兼容字段
        self.assertIn("text", parsed)
        self.assertIn("language", parsed)
        self.assertIn("duration", parsed)
        self.assertIn("segments", parsed)
        self.assertEqual(parsed["segments"][0]["id"], 0)
        self.assertEqual(parsed["segments"][0]["start"], 0.0)
        self.assertEqual(parsed["segments"][0]["end"], 2.0)
        self.assertEqual(parsed["segments"][0]["text"], "Hello")

    def test_to_json_round_trip_via_from_json(self):
        original = TranscriptionResult(
            text="Round trip test",
            language="zh",
            duration=12.345,
            segments=[
                TranscriptionSegment(id=0, start=0.0, end=3.0, text="Round"),
                TranscriptionSegment(id=1, start=3.5, end=12.0, text="trip test"),
            ],
        )
        json_str = original.to_json()
        restored = TranscriptionResult.from_json(json_str)

        self.assertEqual(restored.text, original.text)
        self.assertEqual(restored.language, original.language)
        self.assertAlmostEqual(restored.duration, original.duration, places=3)
        self.assertEqual(len(restored.segments), len(original.segments))
        for i in range(len(original.segments)):
            self.assertEqual(restored.segments[i].text, original.segments[i].text)
            self.assertAlmostEqual(restored.segments[i].start, original.segments[i].start, places=3)
            self.assertAlmostEqual(restored.segments[i].end, original.segments[i].end, places=3)

    def test_from_json_empty_string(self):
        result = TranscriptionResult.from_json("")
        self.assertEqual(result.text, "")
        self.assertEqual(result.segments, [])

    def test_from_json_invalid_json(self):
        with self.assertRaises(json.JSONDecodeError):
            TranscriptionResult.from_json("not json at all")

    def test_to_json_empty_segments(self):
        result = TranscriptionResult(text="", language="", duration=0.0, segments=[])
        json_str = result.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["segments"], [])

    def test_to_json_indent_parameter(self):
        result = TranscriptionResult(text="A", segments=[])
        json_compact = result.to_json(indent=None)
        json_readable = result.to_json(indent=2)
        self.assertIn("\n", json_readable)
        # compact 版本只有一行（除了最后的 newline）
        self.assertEqual(len(json_compact.splitlines()), 1)

    def test_to_dict_compatibility(self):
        result = TranscriptionResult(
            text="Test",
            language="en",
            duration=3.0,
            segments=[TranscriptionSegment(id=0, start=0.0, end=3.0, text="Test")],
        )
        d = result.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["text"], "Test")
        self.assertEqual(len(d["segments"]), 1)


class TestTranscriptionSegment(unittest.TestCase):
    """TranscriptionSegment 基础行为测试"""

    def test_segment_to_dict_default(self):
        seg = TranscriptionSegment(id=5, start=10.0, end=15.5, text="hello")
        d = seg.to_dict()
        self.assertEqual(d["id"], 5)
        self.assertEqual(d["start"], 10.0)
        self.assertEqual(d["end"], 15.5)
        self.assertEqual(d["text"], "hello")

    def test_segment_to_dict_no_extra_fields(self):
        """默认情况下 to_dict 不应泄漏 metadata。"""
        seg = TranscriptionSegment(id=0, start=0.0, end=1.0, text="x")
        d = seg.to_dict()
        self.assertEqual(set(d.keys()), {"id", "start", "end", "text"})


if __name__ == "__main__":
    unittest.main()
