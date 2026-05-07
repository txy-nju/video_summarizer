import json
import heapq
from typing import Any, Dict, List, Tuple

from config.settings import (
    MAP_CHUNK_SECONDS,
    ENABLE_SCENE_ANCHORED_CHUNK_SPLIT,
    CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS,
    CHUNK_SPLIT_DURATION_SHORT_SECONDS,
    CHUNK_SPLIT_DURATION_MEDIUM_SECONDS,
    CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS,
    CHUNK_SPLIT_MIN_DURATION_MEDIUM_SECONDS,
    CHUNK_SPLIT_MIN_DURATION_LONG_SECONDS,
    SCENE_CHANGE_SEVERE_SHORT_THRESHOLD,
    SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD,
    SCENE_CHANGE_SEVERE_LONG_THRESHOLD,
)
from core.workflow.time_travel import parse_timestamp_to_seconds
from core.workflow.video_summary.state import VideoSummaryState


def _load_transcript_json(transcript: str) -> Dict[str, Any]:
    if not transcript or not transcript.strip():
        return {}

    try:
        data = json.loads(transcript)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _load_segments(transcript_data: Dict[str, Any]) -> List[Dict]:
    # OpenAI Whisper verbose_json 常见结构
    segments = transcript_data.get("segments", [])
    if isinstance(segments, list):
        return [item for item in segments if isinstance(item, dict)]

    return []


def _load_chunks(transcript_data: Dict[str, Any]) -> List[Dict]:
    # 兼容部分 ASR 提供商输出：chunks/timestamp
    chunks = transcript_data.get("chunks", [])
    if isinstance(chunks, list):
        return [item for item in chunks if isinstance(item, dict)]

    return []


def _extract_duration_seconds(
    transcript_data: Dict[str, Any], segments: List[Dict], chunks: List[Dict], keyframes: List[Dict]
) -> int:
    max_seconds = 0.0

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            end = float(seg.get("end", 0))
        except Exception:
            continue
        max_seconds = max(max_seconds, end)

    for chunk in chunks:
        ts = chunk.get("timestamp")
        if isinstance(ts, (list, tuple)) and len(ts) == 2:
            try:
                chunk_end = float(ts[1])
            except Exception:
                continue
            max_seconds = max(max_seconds, chunk_end)

    for key in ("duration", "audio_duration", "total_duration"):
        raw = transcript_data.get(key)
        if raw is None:
            continue
        try:
            max_seconds = max(max_seconds, float(raw))
        except Exception:
            continue

    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        try:
            frame_seconds = float(parse_timestamp_to_seconds(str(frame.get("time", ""))))
        except Exception:
            continue
        max_seconds = max(max_seconds, frame_seconds)

    return max(0, int(round(max_seconds)))


def _build_windows(duration_seconds: int, chunk_seconds: int) -> List[Tuple[int, int]]:
    safe_chunk = max(30, chunk_seconds)

    if duration_seconds <= 0:
        return [(0, 0)]

    windows: List[Tuple[int, int]] = []
    start = 0
    while start < duration_seconds:
        end = min(start + safe_chunk, duration_seconds)
        windows.append((start, end))
        start = end

    if not windows:
        windows.append((0, duration_seconds))

    return windows


def _resolve_scene_severe_threshold(duration_seconds: int) -> float:
    if duration_seconds < CHUNK_SPLIT_DURATION_SHORT_SECONDS:
        return SCENE_CHANGE_SEVERE_SHORT_THRESHOLD
    if duration_seconds < CHUNK_SPLIT_DURATION_MEDIUM_SECONDS:
        return SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD
    return SCENE_CHANGE_SEVERE_LONG_THRESHOLD


def _resolve_min_chunk_duration(duration_seconds: int) -> int:
    if duration_seconds < CHUNK_SPLIT_DURATION_SHORT_SECONDS:
        return CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS
    if duration_seconds < CHUNK_SPLIT_DURATION_MEDIUM_SECONDS:
        return CHUNK_SPLIT_MIN_DURATION_MEDIUM_SECONDS
    return CHUNK_SPLIT_MIN_DURATION_LONG_SECONDS


def _build_scene_change_points(keyframes: List[Dict], duration_seconds: int) -> List[Tuple[int, float]]:
    severe_threshold = _resolve_scene_severe_threshold(duration_seconds)
    points: List[Tuple[int, float]] = []
    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        try:
            seconds = parse_timestamp_to_seconds(str(frame.get("time", "")))
        except Exception:
            continue

        score_raw = frame.get("scene_change_score", 0.0)
        try:
            score = max(0.0, min(1.0, float(score_raw)))
        except Exception:
            score = 0.0

        level = str(frame.get("scene_change_level", "")).strip().lower()
        if level == "severe" or score >= severe_threshold:
            points.append((seconds, score))

    points.sort(key=lambda item: item[0])
    return points


def _align_windows_to_scene_changes(
    windows: List[Tuple[int, int]], keyframes: List[Dict], duration_seconds: int, search_seconds: int
) -> Tuple[List[Tuple[int, int]], List[Dict[str, Any]]]:
    if len(windows) <= 1 or duration_seconds <= 0:
        return windows, []

    scene_points = _build_scene_change_points(keyframes, duration_seconds)
    if not scene_points:
        return windows, []

    min_chunk_duration = _resolve_min_chunk_duration(duration_seconds)
    feasible_max_min = max(1, duration_seconds // len(windows))
    min_chunk_duration = min(min_chunk_duration, feasible_max_min)
    boundary_count = max(0, len(windows) - 1)
    fixed_boundaries = [end for _, end in windows[:-1]]
    adjusted_boundaries: List[int] = []
    boundary_meta: List[Dict[str, Any]] = []
    prev_boundary = 0

    for idx, boundary in enumerate(fixed_boundaries):
        remaining_chunks = boundary_count - idx
        min_candidate = max(prev_boundary + min_chunk_duration, boundary - max(1, search_seconds))
        max_candidate = min(
            duration_seconds - remaining_chunks * min_chunk_duration,
            boundary + max(1, search_seconds),
        )

        best_candidate = boundary
        best_source = "fixed"
        best_score = 0.0

        if min_candidate <= max_candidate:
            candidates = [
                (sec, score)
                for sec, score in scene_points
                if min_candidate <= sec <= max_candidate
            ]
            if candidates:
                candidates.sort(key=lambda item: (abs(item[0] - boundary), -item[1], item[0]))
                best_candidate, best_score = candidates[0]
                best_source = "scene"

        # 兜底保护：如果固定边界本身已不满足约束，强制裁剪为可行边界。
        if not (prev_boundary + min_chunk_duration <= best_candidate <= duration_seconds - remaining_chunks * min_chunk_duration):
            lower = prev_boundary + min_chunk_duration
            upper = duration_seconds - remaining_chunks * min_chunk_duration
            best_candidate = min(max(boundary, lower), upper)
            best_source = "fixed"
            best_score = 0.0

        adjusted_boundaries.append(best_candidate)
        boundary_meta.append(
            {
                "boundary_index": idx,
                "base_boundary_sec": boundary,
                "applied_boundary_sec": best_candidate,
                "split_anchor_source": best_source,
                "split_anchor_time": best_candidate if best_source == "scene" else None,
                "scene_change_score": round(best_score, 4) if best_source == "scene" else None,
            }
        )
        prev_boundary = best_candidate

    adjusted_windows: List[Tuple[int, int]] = []
    start = 0
    for boundary in adjusted_boundaries:
        adjusted_windows.append((start, boundary))
        start = boundary
    adjusted_windows.append((start, duration_seconds))
    return adjusted_windows, boundary_meta


def _build_keyframe_times(keyframes: List[Dict]) -> List[Tuple[int, int]]:
    """
    返回按时间排序的关键帧索引：(seconds, original_index)。
    """
    timed_keyframes: List[Tuple[int, int]] = []
    for frame_idx, frame in enumerate(keyframes):
        if not isinstance(frame, dict):
            continue
        try:
            seconds = parse_timestamp_to_seconds(str(frame.get("time", "")))
        except Exception:
            continue
        timed_keyframes.append((seconds, frame_idx))

    timed_keyframes.sort(key=lambda item: item[0])
    return timed_keyframes


def _build_transcript_intervals(segments: List[Dict], chunks: List[Dict]) -> List[Tuple[float, float, int]]:
    """
    统一构建 transcript 区间：[(start, end, index)]，并按 start 排序。
    index 约定：segments 使用原始索引；chunks 使用 base_index + chunk_idx。
    """
    intervals: List[Tuple[float, float, int]] = []

    for seg_idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        try:
            seg_start = float(seg.get("start", 0))
            seg_end = float(seg.get("end", seg_start))
        except Exception:
            continue
        intervals.append((seg_start, seg_end, seg_idx))

    base_index = len(segments)
    for chunk_idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        ts = chunk.get("timestamp")
        if not (isinstance(ts, (list, tuple)) and len(ts) == 2):
            continue
        try:
            chunk_start = float(ts[0])
            chunk_end = float(ts[1])
        except Exception:
            continue
        intervals.append((chunk_start, chunk_end, base_index + chunk_idx))

    intervals.sort(key=lambda item: item[0])
    return intervals


def chunk_planner_node(state: VideoSummaryState) -> dict:
    """
    工作流入口阶段的规划节点。

    地位:
    - 位于工作流最前端，是后续所有分片并行节点的上游依赖。
    - 负责把整段视频证据重写为统一的时间片计划，建立后续 fan-out/fan-in 的公共坐标系。

    任务:
    - 解析 transcript 中的 segments/chunks 结构。
    - 结合关键帧时间戳估算视频总时长。
    - 按 MAP_CHUNK_SECONDS 生成稳定的 chunk_plan。
    - 为每个 chunk 关联命中的 transcript 片段索引和关键帧索引。

    主要输入:
    - state["transcript"]: Whisper verbose_json 或兼容的 transcript JSON 字符串。
    - state["keyframes"]: 已提取的关键帧列表。

    主要输出:
    - video_duration_seconds: 推断的视频总时长。
    - chunk_plan: 后续音频、视觉、融合节点共同消费的分片计划。
    """
    transcript = state.get("transcript", "")
    keyframes = state.get("keyframes", [])

    if not isinstance(keyframes, list):
        keyframes = []

    transcript_data = _load_transcript_json(transcript)
    segments = _load_segments(transcript_data)
    chunks = _load_chunks(transcript_data)
    duration_seconds = _extract_duration_seconds(transcript_data, segments, chunks, keyframes)
    windows = _build_windows(duration_seconds, MAP_CHUNK_SECONDS)
    boundary_meta: List[Dict[str, Any]] = []
    if ENABLE_SCENE_ANCHORED_CHUNK_SPLIT:
        windows, boundary_meta = _align_windows_to_scene_changes(
            windows,
            keyframes,
            duration_seconds,
            CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS,
        )

    # 预处理：按时间排序，避免每个窗口反复全量扫描
    timed_keyframes = _build_keyframe_times(keyframes)
    transcript_intervals = _build_transcript_intervals(segments, chunks)

    keyframe_start_ptr = 0
    interval_start_ptr = 0
    active_intervals_heap: List[Tuple[float, int]] = []

    chunk_plan: List[Dict] = []
    for index, (start_sec, end_sec) in enumerate(windows):
        chunk_id = f"chunk-{index:03d}"

        # 关键帧双指针扫描（保持边界包含语义：time == start_sec 仍可命中）
        while keyframe_start_ptr < len(timed_keyframes) and timed_keyframes[keyframe_start_ptr][0] < start_sec:
            keyframe_start_ptr += 1

        keyframe_indexes: List[int] = []
        probe_ptr = keyframe_start_ptr
        while probe_ptr < len(timed_keyframes) and timed_keyframes[probe_ptr][0] <= end_sec:
            keyframe_indexes.append(timed_keyframes[probe_ptr][1])
            probe_ptr += 1

        # transcript 区间扫线：按 start 入堆，按 end 出堆
        while interval_start_ptr < len(transcript_intervals) and transcript_intervals[interval_start_ptr][0] <= end_sec:
            _, seg_end, seg_idx = transcript_intervals[interval_start_ptr]
            heapq.heappush(active_intervals_heap, (seg_end, seg_idx))
            interval_start_ptr += 1

        while active_intervals_heap and active_intervals_heap[0][0] < start_sec:
            heapq.heappop(active_intervals_heap)

        transcript_segment_indexes = sorted(seg_idx for _, seg_idx in active_intervals_heap)

        chunk_plan.append(
            {
                "chunk_id": chunk_id,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "keyframe_indexes": keyframe_indexes,
                "transcript_segment_indexes": transcript_segment_indexes,
                "priority": "normal",
                "split_anchor_source": "fixed",
                "split_anchor_time": None,
            }
        )

        if index < len(boundary_meta):
            chunk_plan[-1]["split_anchor_source"] = boundary_meta[index].get("split_anchor_source", "fixed")
            chunk_plan[-1]["split_anchor_time"] = boundary_meta[index].get("split_anchor_time")
        
    return {
        "video_duration_seconds": duration_seconds,
        "chunk_plan": chunk_plan,
    }
