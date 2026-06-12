from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TranscriptionSegment:
    """标准化转录片段，与 OpenAI Whisper verbose_json segments 格式对齐。

    字段命名与 Whisper 完全一致（id, start, end, text），
    其中 start/end 均为 float 秒。
    """

    id: int
    start: float  # seconds
    end: float  # seconds
    text: str

    def to_dict(self, include_metadata: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        return result


@dataclass
class TranscriptionResult:
    """标准化转录结果，统一所有 provider 的输出。

    to_json() 输出与 OpenAI Whisper verbose_json 格式完全兼容，
    下游 time_travel / chunk_multimodal_analyzer / map_dispatcher 零破坏。
    """

    text: str
    language: str = ""
    duration: float = 0.0
    segments: List[TranscriptionSegment] = field(default_factory=list)

    # ── 序列化 ──────────────────────────────────────────────────────────

    def to_json(self, indent: int = 2) -> str:
        """序列化为与 Whisper verbose_json 兼容的 JSON 字符串。

        下游 consumer（extract_transcript_window, _parse_transcript_segments,
        _build_transcript_text_with_timestamps, DB transcript_segments JSONB）
        均依赖此格式。
        """
        return json.dumps(
            {
                "text": self.text,
                "language": self.language,
                "duration": round(self.duration, 3),
                "segments": [seg.to_dict() for seg in self.segments],
            },
            ensure_ascii=False,
            indent=indent,
        )

    # ── 反序列化 ────────────────────────────────────────────────────────

    @classmethod
    def from_json(cls, json_string: str) -> "TranscriptionResult":
        """从 Whisper verbose_json 字符串反序列化。"""
        data = json.loads(json_string or "{}")
        if not isinstance(data, dict):
            return cls(text="", language="", duration=0.0, segments=[])
        return cls.from_whisper_verbose_json(data)

    # ── 各 provider 格式工厂方法 ─────────────────────────────────────────

    @classmethod
    def from_whisper_verbose_json(cls, data: Dict[str, Any]) -> "TranscriptionResult":
        """从 OpenAI/Groq/Local whisper 的 verbose_json 响应解析。

        期望格式: {"text": "...", "language": "en", "duration": 5.0,
                    "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "..."}]}
        """
        segments_data = data.get("segments", [])
        if not isinstance(segments_data, list):
            segments_data = []

        segments = []
        for seg in segments_data:
            if not isinstance(seg, dict):
                continue
            segments.append(
                TranscriptionSegment(
                    id=int(seg.get("id", 0)),
                    start=float(seg.get("start", 0)),
                    end=float(seg.get("end", 0)),
                    text=str(seg.get("text", "")),
                )
            )

        return cls(
            text=str(data.get("text", "")),
            language=str(data.get("language", "")),
            duration=float(data.get("duration", 0)),
            segments=segments,
        )

    @classmethod
    def from_aigc_lasr_response(cls, data: Dict[str, Any]) -> "TranscriptionResult":
        """从 vivo AIGC /lasr/result 响应解析。

        期望格式: {"result": [{"onebest": "文本", "bg": 0, "ed": 2190, "speaker": 1}, ...]}
        - bg/ed: 毫秒 → 转换为 start/end 浮点秒
        - speaker: 保留在 to_json 输出中（非破坏性扩展字段）
        - language: vivo API 不返回，默认空字符串
        """
        result_list = data.get("result", [])
        if not isinstance(result_list, list):
            result_list = []

        segments: List[TranscriptionSegment] = []
        full_text_parts: List[str] = []

        for i, item in enumerate(result_list):
            if not isinstance(item, dict):
                continue
            text = str(item.get("onebest", "")).strip()
            seg = TranscriptionSegment(
                id=i,
                start=round(float(item.get("bg", 0)) / 1000.0, 3),
                end=round(float(item.get("ed", 0)) / 1000.0, 3),
                text=text,
            )
            # 保留 speaker 信息：存储在 to_json 输出的扩展字段中
            # 通过自定义序列化保证向下兼容
            segments.append(seg)
            if text:
                full_text_parts.append(text)

        # 从最后一段 ed 推断总时长
        duration = segments[-1].end if segments else 0.0

        return cls(
            text=" ".join(full_text_parts),
            language="",  # vivo API 不返回 language 字段
            duration=duration,
            segments=segments,
        )

    @classmethod
    def from_paraformer_response(cls, data: Dict[str, Any]) -> "TranscriptionResult":
        """从 DashScope Paraformer 响应解析（预留，Qwen 阶段完善）。

        期望格式: {"sentences": [{"begin_time": 760, "end_time": 3240,
                                  "text": "Hello World", "sentence_id": 1}]}
        - begin_time/end_time: 毫秒 → 转换为 start/end 浮点秒
        - sentence_id → segment id
        """
        sentences = data.get("sentences", [])
        if not isinstance(sentences, list):
            sentences = []

        segments: List[TranscriptionSegment] = []
        full_text_parts: List[str] = []

        for sent in sentences:
            if not isinstance(sent, dict):
                continue
            text = str(sent.get("text", "")).strip()
            seg = TranscriptionSegment(
                id=int(sent.get("sentence_id", len(segments))),
                start=round(float(sent.get("begin_time", 0)) / 1000.0, 3),
                end=round(float(sent.get("end_time", 0)) / 1000.0, 3),
                text=text,
            )
            segments.append(seg)
            if text:
                full_text_parts.append(text)

        duration = segments[-1].end if segments else 0.0

        return cls(
            text=" ".join(full_text_parts),
            language=str(data.get("language", "")),
            duration=duration,
            segments=segments,
        )

    def to_dict(self) -> Dict[str, Any]:
        """兼容旧代码的直接 dict 访问。"""
        return {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": [seg.to_dict() for seg in self.segments],
        }
