
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil
import os
import json
import types

from core.extraction.infrastructure.transcriber import AudioTranscriber, _merge_verbose_json, _split_audio, _load_audio_file_clip_class, _slice_audio_clip

class TestAudioTranscriber(unittest.TestCase):

    def setUp(self):
        """创建测试所需的临时目录和文件"""
        self.test_audio_dir = Path(__file__).parent / "test_temp_transcriber_audio"
        
        if self.test_audio_dir.exists():
            shutil.rmtree(self.test_audio_dir)
        self.test_audio_dir.mkdir()

        self.dummy_audio_path = self.test_audio_dir / "dummy_audio.mp3"
        self.dummy_audio_path.touch()

        self.api_key = "test_api_key_placeholder"

    def tearDown(self):
        """清理测试产生的临时目录"""
        if self.test_audio_dir.exists():
            shutil.rmtree(self.test_audio_dir)

    @patch.dict('sys.modules', {'moviepy.editor': None}, clear=False)
    def test_load_audio_file_clip_class_supports_new_moviepy_import(self):
        """当前环境若只有 moviepy 新入口，仍应拿到 AudioFileClip 类。"""
        clip_cls = _load_audio_file_clip_class()
        self.assertIsNotNone(clip_cls)

    def test_slice_audio_clip_prefers_subclipped(self):
        """当前 moviepy 版本提供 subclipped 时，应优先使用它。"""
        expected = object()

        class _Clip:
            def subclipped(self, start, end):
                self.called = ("subclipped", start, end)
                return expected

        clip = _Clip()
        result = _slice_audio_clip(clip, 1.0, 2.5)

        self.assertIs(result, expected)
        self.assertEqual(getattr(clip, "called", None), ("subclipped", 1.0, 2.5))

    @patch('core.llm.openai_model.OpenAI')
    def test_transcribe(self, mock_openai_client):
        """测试 transcribe 方法"""
        # --- 准备模拟 ---
        mock_instance = MagicMock()
        mock_openai_client.return_value = mock_instance
        
        # 准备一个假的 TranscriptionVerbose 对象返回结果
        mock_transcript_obj = MagicMock()
        expected_json_string = '{\n  "text": "Hello, world.",\n  "language": "en"\n}'
        mock_transcript_obj.model_dump_json.return_value = expected_json_string
        
        mock_instance.audio.transcriptions.create.return_value = mock_transcript_obj

        # --- 执行测试 ---
        transcriber = AudioTranscriber(api_key=self.api_key)
        result = transcriber.transcribe(self.dummy_audio_path)

        # --- 断言 ---
        mock_openai_client.assert_called_once_with(api_key=self.api_key, base_url=None)
        mock_instance.audio.transcriptions.create.assert_called_once()
        
        _, kwargs = mock_instance.audio.transcriptions.create.call_args
        
        self.assertEqual(kwargs['model'], 'whisper-1')
        self.assertEqual(kwargs['response_format'], 'verbose_json')
        
        self.assertTrue(hasattr(kwargs['file'], 'read'))
        self.assertEqual(kwargs['file'].name, str(self.dummy_audio_path))

        # 验证返回的是否是预期的 JSON 字符串
        self.assertEqual(result, expected_json_string)

    @patch('core.llm.openai_model.OpenAI')
    @patch('core.extraction.infrastructure.transcriber._split_audio')
    def test_transcribe_large_audio_split_and_merge_keeps_order(self, mock_split_audio, mock_openai_client):
        """大文件分片后应按分片顺序合并，且时间戳偏移正确。"""
        mock_openai_client.return_value = MagicMock()

        part0 = self.test_audio_dir / "dummy_audio_part000.mp3"
        part1 = self.test_audio_dir / "dummy_audio_part001.mp3"
        part2 = self.test_audio_dir / "dummy_audio_part002.mp3"
        for p in (part0, part1, part2):
            p.touch()

        mock_split_audio.return_value = [
            (part0, 0.0),
            (part1, 10.0),
            (part2, 20.0),
        ]

        transcriber = AudioTranscriber(api_key=self.api_key)
        fake_outputs = {
            str(part0): json.dumps({"text": "A", "language": "en", "duration": 8, "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "A1"}]}),
            str(part1): json.dumps({"text": "B", "language": "en", "duration": 8, "segments": [{"id": 0, "start": 0.2, "end": 1.2, "text": "B1"}]}),
            str(part2): json.dumps({"text": "C", "language": "en", "duration": 8, "segments": [{"id": 0, "start": 0.4, "end": 1.4, "text": "C1"}]}),
        }

        with patch.object(
            transcriber,
            '_transcribe_single',
            side_effect=lambda p: fake_outputs[str(p)]
        ) as mock_transcribe_single:
            merged_json = transcriber.transcribe(self.dummy_audio_path)

        self.assertEqual(mock_transcribe_single.call_count, 3)
        merged = json.loads(merged_json)

        # 文本拼接顺序必须与分片顺序一致
        self.assertEqual(merged["text"], "A B C")

        # 合并后 segment 顺序与偏移校正（0.x -> 10.x -> 20.x）
        starts = [seg["start"] for seg in merged["segments"]]
        self.assertEqual(starts, [0.0, 10.2, 20.4])

        # 合并后 id 连续递增，保持稳定有序
        ids = [seg["id"] for seg in merged["segments"]]
        self.assertEqual(ids, [0, 1, 2])

    def test_merge_verbose_json_preserves_relative_order(self):
        """直接验证 merge 函数在多段多条 segment 下保持原始相对顺序。"""
        parts = [
            (
                {
                    "text": "P0",
                    "language": "en",
                    "duration": 5,
                    "segments": [
                        {"id": 7, "start": 0.0, "end": 1.0, "text": "p0-s0"},
                        {"id": 8, "start": 1.0, "end": 2.0, "text": "p0-s1"},
                    ],
                },
                0.0,
            ),
            (
                {
                    "text": "P1",
                    "language": "en",
                    "duration": 5,
                    "segments": [
                        {"id": 3, "start": 0.2, "end": 1.2, "text": "p1-s0"},
                        {"id": 4, "start": 1.2, "end": 2.2, "text": "p1-s1"},
                    ],
                },
                10.0,
            ),
        ]

        merged = json.loads(_merge_verbose_json(parts))
        ordered_texts = [seg["text"] for seg in merged["segments"]]
        self.assertEqual(ordered_texts, ["p0-s0", "p0-s1", "p1-s0", "p1-s1"])

        # 后半段应统一加上 offset，且不改变相对先后
        starts = [seg["start"] for seg in merged["segments"]]
        self.assertEqual(starts, [0.0, 1.0, 10.2, 11.2])

    def test_merge_verbose_json_keeps_single_path_metadata_shape(self):
        """大文件合并路径应尽量保持与单路径 verbose_json 一致的元字段结构。"""
        parts = [
            (
                {
                    "task": "transcribe",
                    "language": "en",
                    "duration": 5,
                    "text": "first",
                    "segments": [{"id": 0, "start": 0, "end": 1, "text": "a"}],
                    "extra_meta": {"source": "whisper"},
                },
                0.0,
            ),
            (
                {
                    "task": "transcribe",
                    "language": "en",
                    "duration": 6,
                    "text": "second",
                    "segments": [{"id": 0, "start": 0.5, "end": 1.5, "text": "b"}],
                },
                10.0,
            ),
        ]

        merged = json.loads(_merge_verbose_json(parts))

        # 保留首段元字段（同构性）
        self.assertEqual(merged.get("task"), "transcribe")
        self.assertEqual(merged.get("extra_meta"), {"source": "whisper"})

        # 核心字段按合并逻辑重建
        self.assertEqual(merged.get("text"), "first second")
        self.assertEqual(merged.get("duration"), 16.0)
        self.assertEqual([s.get("id") for s in merged.get("segments", [])], [0, 1])
        self.assertEqual([s.get("start") for s in merged.get("segments", [])], [0.0, 10.5])

    @patch('core.llm.openai_model.OpenAI')
    @patch('core.extraction.infrastructure.transcriber._split_audio')
    def test_transcribe_large_and_single_equivalent_core_fields(self, mock_split_audio, mock_openai_client):
        """同一语义内容下，单文件路径与大文件合并路径应产出下游等价的核心 transcript 结构。"""
        mock_openai_client.return_value = MagicMock()

        part0 = self.test_audio_dir / "dummy_audio_part000.mp3"
        part1 = self.test_audio_dir / "dummy_audio_part001.mp3"
        part0.touch()
        part1.touch()

        single_payload = {
            "task": "transcribe",
            "language": "en",
            "duration": 16.0,
            "text": "first second",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "a"},
                {"id": 1, "start": 10.5, "end": 11.5, "text": "b"},
            ],
        }

        # Case 1: 单文件路径
        mock_split_audio.return_value = [(self.dummy_audio_path, 0.0)]
        single_transcriber = AudioTranscriber(api_key=self.api_key)
        with patch.object(single_transcriber, '_transcribe_single', return_value=json.dumps(single_payload)):
            single_result = json.loads(single_transcriber.transcribe(self.dummy_audio_path))

        # Case 2: 大文件分片合并路径
        mock_split_audio.return_value = [(part0, 0.0), (part1, 10.0)]
        large_transcriber = AudioTranscriber(api_key=self.api_key)
        large_outputs = {
            str(part0): json.dumps(
                {
                    "task": "transcribe",
                    "language": "en",
                    "duration": 6,
                    "text": "first",
                    "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "a"}],
                }
            ),
            str(part1): json.dumps(
                {
                    "task": "transcribe",
                    "language": "en",
                    "duration": 6,
                    "text": "second",
                    "segments": [{"id": 0, "start": 0.5, "end": 1.5, "text": "b"}],
                }
            ),
        }
        with patch.object(
            large_transcriber,
            '_transcribe_single',
            side_effect=lambda p: large_outputs[str(p)]
        ):
            large_result = json.loads(large_transcriber.transcribe(self.dummy_audio_path))

        # 对齐下游核心依赖字段（chunk_planner/chunk_audio/time_travel）
        for key in ["text", "language", "duration"]:
            self.assertEqual(large_result.get(key), single_result.get(key))

        self.assertEqual(len(large_result.get("segments", [])), len(single_result.get("segments", [])))
        self.assertEqual(
            [(seg.get("start"), seg.get("end"), seg.get("text")) for seg in large_result.get("segments", [])],
            [(seg.get("start"), seg.get("end"), seg.get("text")) for seg in single_result.get("segments", [])],
        )

    @patch('core.llm.openai_model.OpenAI')
    @patch('core.extraction.infrastructure.transcriber._split_audio')
    def test_transcribe_single_segment_path_no_merge(self, mock_split_audio, mock_openai_client):
        """单段音频应直接走 _transcribe_single，不触发多段合并。"""
        mock_openai_client.return_value = MagicMock()
        mock_split_audio.return_value = [(self.dummy_audio_path, 0.0)]

        transcriber = AudioTranscriber(api_key=self.api_key)
        expected_json = json.dumps({"text": "single", "segments": []})

        with patch.object(transcriber, '_transcribe_single', return_value=expected_json) as mock_single:
            result = transcriber.transcribe(self.dummy_audio_path)

        mock_single.assert_called_once_with(self.dummy_audio_path)
        self.assertEqual(result, expected_json)

    @patch('core.extraction.infrastructure.transcriber._get_audio_duration', return_value=40.0)
    @patch('core.extraction.infrastructure.transcriber.Path.stat')
    @patch('core.extraction.infrastructure.transcriber.Path.exists', return_value=True)
    def test_split_audio_resplits_oversized_segment_and_keeps_order(
        self,
        mock_exists,
        mock_stat,
        mock_duration,
    ):
        """若初次分段仍超限，应自动二分并保持时间顺序。"""

        # 第一次 stat() 调用是原文件大小，后续是每个分段文件大小
        # 设计为：前两个初始分段都超限 -> 各自二分后四段都合规
        size_sequence = [
            36 * 1024 * 1024,  # 原始文件大小（对应初始 2 段）
            28 * 1024 * 1024,  # seg 0 (0-20) 超限
            10 * 1024 * 1024,  # seg 0a (0-10) 合规
            11 * 1024 * 1024,  # seg 0b (10-20) 合规
            29 * 1024 * 1024,  # seg 1 (20-40) 超限
            10 * 1024 * 1024,  # seg 1a (20-30) 合规
            11 * 1024 * 1024,  # seg 1b (30-40) 合规
        ]

        def fake_stat(*_args, **_kwargs):
            value = size_sequence.pop(0) if size_sequence else 10 * 1024 * 1024
            return types.SimpleNamespace(st_size=value)

        mock_stat.side_effect = fake_stat

        # 构造假的 moviepy AudioFileClip（仅验证切段顺序和输出）
        created_ranges = []

        class _FakeSubClip:
            def __init__(self, start, end):
                self.start = start
                self.end = end

            def write_audiofile(self, *_args, **_kwargs):
                return None

            def close(self):
                return None

        class _FakeClip:
            def __init__(self, *_args, **_kwargs):
                pass

            def subclipped(self, start, end):
                created_ranges.append((round(start, 2), round(end, 2)))
                return _FakeSubClip(start, end)

            def close(self):
                return None

        fake_moviepy_module = types.SimpleNamespace(AudioFileClip=_FakeClip)

        with patch.dict('sys.modules', {'moviepy': fake_moviepy_module, 'moviepy.editor': fake_moviepy_module}):
            segments = _split_audio(self.dummy_audio_path)

        # 最终应得到 4 段，且 offset 时间有序
        self.assertEqual(len(segments), 4)
        offsets = [round(offset, 2) for _, offset in segments]
        self.assertEqual(offsets, [0.0, 10.0, 20.0, 30.0])
        self.assertEqual(offsets, sorted(offsets))

if __name__ == '__main__':
    unittest.main()
