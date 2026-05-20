
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential
import json
import math
from typing import List, Tuple
from collections import deque
from tenacity import RetryError

from core.llm.base import BaseModel
from core.llm.config import resolve_api_key
from core.llm.factory import get_model_for_capability, get_model_name_for_capability

# Whisper API 单文件硬限制为 25MB，留 1MB 安全余量
_WHISPER_MAX_BYTES = 24 * 1024 * 1024
# 实际切段目标使用更保守阈值，降低 VBR 音频导致的分段超限概率
_WHISPER_TARGET_BYTES = 18 * 1024 * 1024
_MAX_SPLIT_DEPTH = 6


def _load_audio_file_clip_class():
    """兼容 moviepy 新旧版本的 AudioFileClip 导入路径。"""
    try:
        from moviepy import AudioFileClip
        return AudioFileClip
    except ImportError:
        try:
            from moviepy.editor import AudioFileClip
            return AudioFileClip
        except ImportError:
            return None


def _slice_audio_clip(clip, start: float, end: float):
    """兼容 moviepy 不同版本的音频裁剪 API。"""
    for method_name in ("subclipped", "subclip", "with_subclip"):
        method = getattr(clip, method_name, None)
        if callable(method):
            return method(start, end)

    raise AttributeError("AudioFileClip does not support subclipped/subclip/with_subclip")


def _get_audio_duration(audio_path: Path) -> float:
    """使用 moviepy 获取音频时长（秒），失败返回 0.0。"""
    try:
        AudioFileClip = _load_audio_file_clip_class()
        if AudioFileClip is None:
            return 0.0
        clip = AudioFileClip(str(audio_path))
        duration = float(clip.duration)
        clip.close()
        return duration
    except Exception:
        return 0.0


def _split_audio(audio_path: Path) -> List[Tuple[Path, float]]:
    """
    若音频文件超过 Whisper 25MB 限制，将其切分为多个片段。
    返回值：[(片段路径, 起始偏移秒数), ...]
    未超限时直接返回 [(audio_path, 0.0)]。
    """
    file_size = audio_path.stat().st_size
    if file_size <= _WHISPER_MAX_BYTES:
        return [(audio_path, 0.0)]

    AudioFileClip = _load_audio_file_clip_class()
    if AudioFileClip is None:
        print("[AudioTranscriber] moviepy 不可用，无法切分音频，将直接发送（可能超限）。")
        return [(audio_path, 0.0)]

    duration = _get_audio_duration(audio_path)
    if duration <= 0:
        return [(audio_path, 0.0)]

    n_segments = math.ceil(file_size / _WHISPER_TARGET_BYTES)
    segment_duration = duration / n_segments
    print(
        f"[AudioTranscriber] 音频 {audio_path.name} 大小 {file_size / 1024 / 1024:.1f}MB，"
        f"初始切分为 {n_segments} 段（每段约 {segment_duration:.0f}s，目标<={_WHISPER_TARGET_BYTES / 1024 / 1024:.0f}MB）。"
    )

    queue = deque()
    for i in range(n_segments):
        start = i * segment_duration
        end = min((i + 1) * segment_duration, duration)
        queue.append((start, end, 0))

    segments: List[Tuple[Path, float]] = []
    output_index = 0

    while queue:
        start, end, depth = queue.popleft()
        seg_path = audio_path.parent / f"{audio_path.stem}_part{output_index:03d}.mp3"

        try:
            clip = AudioFileClip(str(audio_path))
            sub = _slice_audio_clip(clip, start, end)
            sub.write_audiofile(str(seg_path), logger=None)
            sub.close()
            clip.close()
        except Exception as exc:
            print(f"[AudioTranscriber] 切段失败 [{start:.2f}, {end:.2f}]：{exc}，跳过。")
            continue

        seg_size = seg_path.stat().st_size if seg_path.exists() else 0
        seg_duration = end - start

        # 兜底：若某段仍超限，按时间二分递归切细，保证最终上传段不超阈值
        if seg_size > _WHISPER_MAX_BYTES and seg_duration > 2 and depth < _MAX_SPLIT_DEPTH:
            try:
                seg_path.unlink(missing_ok=True)
            except Exception:
                pass

            mid = (start + end) / 2.0
            # appendleft 顺序：先压后半段再压前半段，确保前半段优先处理，整体顺序不变
            queue.appendleft((mid, end, depth + 1))
            queue.appendleft((start, mid, depth + 1))
            continue

        segments.append((seg_path, start))
        output_index += 1

    return segments if segments else [(audio_path, 0.0)]


def _merge_verbose_json(parts: List[Tuple[dict, float]]) -> str:
    """
    将多段 verbose_json 合并为一个完整的 JSON 字符串。
    按偏移量修正每段 segments 的 start/end 时间戳。
    """
    if not parts:
        return json.dumps(
            {
                "text": "",
                "language": "",
                "duration": 0.0,
                "segments": [],
            },
            ensure_ascii=False,
            indent=2,
        )

    merged_text: List[str] = []
    merged_segments: List[dict] = []
    total_duration = 0.0
    language = ""
    seg_id_offset = 0

    # 以首段 transcript 作为结构基座，保留单路径 verbose_json 的其他元字段。
    first_payload = parts[0][0] if isinstance(parts[0][0], dict) else {}
    merged_payload = dict(first_payload)

    for transcript_dict, offset in parts:
        if not language:
            language = transcript_dict.get("language", "")

        merged_text.append(str(transcript_dict.get("text", "")).strip())

        for seg in transcript_dict.get("segments", []):
            new_seg = dict(seg)
            new_seg["id"] = seg_id_offset + int(seg.get("id", 0))
            new_seg["start"] = round(float(seg.get("start", 0)) + offset, 3)
            new_seg["end"] = round(float(seg.get("end", 0)) + offset, 3)
            merged_segments.append(new_seg)

        seg_id_offset += len(transcript_dict.get("segments", []))
        part_dur = float(transcript_dict.get("duration") or 0)
        total_duration = max(total_duration, offset + part_dur)

    merged_payload["text"] = " ".join(merged_text)
    merged_payload["language"] = language
    merged_payload["duration"] = round(total_duration, 3)
    merged_payload["segments"] = merged_segments

    return json.dumps(merged_payload, ensure_ascii=False, indent=2)


class AudioTranscriber:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transcribe_model: BaseModel | None = None,
    ):
        """
        初始化 AudioTranscriber。

        Args:
            api_key (str): OpenAI API Key。
            base_url (str, optional): OpenAI API 的中转地址。默认为 None。
            model (str, optional): 转文本模型名称。默认从 TRANSCRIBE_MODEL_NAME/TRANSCRIBER_MODEL 读取。
        """
        # api_key / base_url 参数保留以兼容旧调用方，但已不再用于直接构造 OpenAIModel。
        # 转录模型统一走 get_model_for_capability("transcribe")，由工厂按 TRANSCRIBE_PROVIDER 路由。
        self.api_key = api_key or resolve_api_key("transcribe")
        self.base_url = base_url
        self.model = model or get_model_name_for_capability("transcribe")
        if transcribe_model is not None:
            self.transcribe_model = transcribe_model
        else:
            self.transcribe_model = get_model_for_capability("transcribe")

    def transcribe(self, audio_path: Path) -> str:
        """
        将音频转录为 JSON 格式的文本。
        若音频超过 25MB，自动切段分批转录后合并时间戳。

        Args:
            audio_path (Path): 音频文件的路径。

        Returns:
            str: JSON 格式的转录结果（包含详细时间戳段落）。
        """
        segments = _split_audio(audio_path)

        if len(segments) == 1:
            try:
                return self._transcribe_single(segments[0][0])
            except RetryError as exc:
                seg_path = segments[0][0]
                seg_size_mb = 0.0
                try:
                    seg_size_mb = seg_path.stat().st_size / 1024 / 1024
                except Exception:
                    pass
                raise RuntimeError(
                    f"音频转录失败（重试耗尽）。段文件: {seg_path.name}, 大小: {seg_size_mb:.1f}MB。"
                    "可能原因：API 配额/限流、凭证异常，或分段仍超过服务端限制。"
                ) from exc

        # 多段转录：逐段调用 API，最后合并时间戳
        parts: List[Tuple[dict, float]] = []
        for seg_path, offset in segments:
            try:
                result_json = self._transcribe_single(seg_path)
            except RetryError as exc:
                seg_size_mb = 0.0
                try:
                    seg_size_mb = seg_path.stat().st_size / 1024 / 1024
                except Exception:
                    pass
                raise RuntimeError(
                    f"音频分段转录失败（重试耗尽）。段文件: {seg_path.name}, 大小: {seg_size_mb:.1f}MB, 偏移: {offset:.2f}s。"
                    "可能原因：API 配额/限流、凭证异常，或分段仍超过服务端限制。"
                ) from exc
            try:
                result_dict = json.loads(result_json)
            except Exception:
                result_dict = {"text": result_json, "segments": [], "duration": 0}
            parts.append((result_dict, offset))
            # 临时切段文件用完即删
            if seg_path != audio_path:
                try:
                    seg_path.unlink(missing_ok=True)
                except Exception:
                    pass

        return _merge_verbose_json(parts)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _transcribe_single(self, audio_path: Path) -> str:
        """对单个音频文件调用 Whisper API（含 tenacity 重试）。"""
        print(f"Transcribing audio segment: {audio_path}...")
        transcript_json = self.transcribe_model.transcribe_audio(
            model=self.model,
            audio_path=audio_path,
            response_format="verbose_json",
        )
        print(f"Transcription successful: {audio_path.name}")
        return transcript_json
