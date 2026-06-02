import json
from typing import Dict, List, Optional, Tuple


def parse_timestamp_to_seconds(timestamp: str) -> int:
    """
    支持 MM:SS 或 HH:MM:SS，返回总秒数。
    """
    if not timestamp or not timestamp.strip():
        raise ValueError("timestamp is required")

    parts = timestamp.strip().split(":")
    if len(parts) == 2:
        mm, ss = parts
        return int(mm) * 60 + int(ss)
    if len(parts) == 3:
        hh, mm, ss = parts
        return int(hh) * 3600 + int(mm) * 60 + int(ss)

    raise ValueError(f"Unsupported timestamp format: {timestamp}")


def format_seconds(total_seconds: float) -> str:
    """
    将秒数格式化为 HH:MM:SS。
    """
    seconds = max(0, int(total_seconds))
    hh = seconds // 3600
    mm = (seconds % 3600) // 60
    ss = seconds % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def find_nearest_keyframe(
    keyframes: List[Dict],
    target_seconds: int,
    window_seconds: Optional[int] = None,
) -> Optional[Dict] | List[Dict]:
    """
    根据目标时间戳找关键帧。
    
    当 window_seconds 为 None 时（默认），返回最近邻的单个关键帧（向后兼容）。
    当提供 window_seconds 时，返回时间窗口内有代表性的多个关键帧（3-5帧），
    这些帧在时间上均匀分布。
    
    Args:
        keyframes: 关键帧列表，每个帧包含 "time" 和其他属性
        target_seconds: 目标时间（秒数）
        window_seconds: 时间窗口大小（秒数），若为 None 则使用传统单帧模式
        
    Returns:
        单帧模式（window_seconds=None）：返回 Dict 或 None
        多帧模式（window_seconds 已提供）：返回 List[Dict]（可能为空列表）
    """
    if not keyframes:
        return None if window_seconds is None else []

    # 单帧模式：返回最近邻关键帧（原有行为，用于向后兼容）
    if window_seconds is None:
        nearest: Optional[Dict] = None
        min_delta = 10**9

        for frame in keyframes:
            frame_time = frame.get("time", "")
            try:
                frame_seconds = parse_timestamp_to_seconds(str(frame_time))
            except Exception:
                continue

            delta = abs(frame_seconds - target_seconds)
            if delta < min_delta:
                min_delta = delta
                nearest = frame

        return nearest

    # 多帧模式：在时间窗口内选取有代表性的帧（起止模式：从 target_seconds 到 target_seconds + window_seconds）
    window_size = max(0, window_seconds)
    left = max(0, target_seconds)
    right = target_seconds + window_size

    # 收集窗口范围内的所有关键帧
    frames_in_window: List[Tuple[int, Dict]] = []
    for frame in keyframes:
        frame_time = frame.get("time", "")
        try:
            frame_seconds = parse_timestamp_to_seconds(str(frame_time))
        except Exception:
            continue

        if left <= frame_seconds <= right:
            frames_in_window.append((frame_seconds, frame))

    if not frames_in_window:
        return []

    # 按时间排序
    frames_in_window.sort(key=lambda x: x[0])

    # 计算应返回的帧数：根据窗口大小自动配置
    # 基础策略：window_seconds / 10 秒配一帧，限制在 3-5 帧
    target_frame_count = max(3, min(5, (window_size // 10) + 3))
    target_frame_count = min(target_frame_count, len(frames_in_window))

    # 均匀分布选取代表性帧
    if target_frame_count == len(frames_in_window):
        # 如果窗口内的帧数正好等于目标数，全部返回
        selected_frames = [frame for _, frame in frames_in_window]
    else:
        # 均匀间隔选取
        selected_indices = [
            int(i * (len(frames_in_window) - 1) / (target_frame_count - 1))
            for i in range(target_frame_count)
        ]
        selected_frames = [frames_in_window[idx][1] for idx in selected_indices]

    return selected_frames


def extract_transcript_window(transcript: str, target_seconds: int, window_seconds: int = 20) -> str:
    """
    从 Whisper verbose_json 字符串中抽取目标时间窗的文本证据。
    """
    if not transcript or not transcript.strip():
        return "[无音频转录可用]"

    try:
        data = json.loads(transcript)
    except Exception:
        # 非 JSON 场景：按普通文本降级
        return transcript[:1200]

    segments = data.get("segments", []) if isinstance(data, dict) else []
    if not isinstance(segments, list) or not segments:
        # Whisper 某些返回可能只有 text 字段
        text = data.get("text", "") if isinstance(data, dict) else ""
        return text[:1200] if text else "[无可用转录分段]"

    left = max(0, target_seconds)
    right = target_seconds + max(0, window_seconds)
    lines: List[str] = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start))
        except Exception:
            continue

        # 时间窗重叠判定
        if end < left or start > right:
            continue

        text = str(seg.get("text", "")).strip()
        if not text:
            continue

        lines.append(f"[{format_seconds(start)}-{format_seconds(end)}] {text}")

    if not lines:
        return "[该时间窗未命中可用语音分段]"

    return "\n".join(lines)
