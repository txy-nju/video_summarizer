import json
import time
import threading
from typing import Any, Dict, List

from core.llm.base import BaseModel
from core.llm.config import resolve_api_key
from core.llm.factory import get_model_for_capability, get_model_name_for_capability

from config.settings import (
    CHUNK_DEGRADED_MARKER,
    CHUNK_MAX_TOOL_CALLS,
    CHUNK_WORKER_MAX_RETRIES,
    CHUNK_WORKER_TIMEOUT_SECONDS,
    ENABLE_CHUNK_CACHE,
)
from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.tools.search_tools import execute_tavily_search
from core.workflow.video_summary.utils.frame_utils import resolve_frame_image_base64

_VISION_SEARCH_CACHE: Dict[str, str] = {}
_VISION_SEARCH_CACHE_LOCK = threading.Lock()


FramePayload = Dict[str, Any]
ChunkResult = Dict[str, Any]


def _search_with_cache(query: str) -> str:
    if ENABLE_CHUNK_CACHE:
        with _VISION_SEARCH_CACHE_LOCK:
            cached = _VISION_SEARCH_CACHE.get(query)
        if cached is not None:
            return cached

    result = execute_tavily_search(query)
    if ENABLE_CHUNK_CACHE:
        with _VISION_SEARCH_CACHE_LOCK:
            _VISION_SEARCH_CACHE[query] = result
    return result


def _build_vision_structured_fallback(chunk_id: str, frames: List[FramePayload], reason: str) -> Dict[str, Any]:
    frame_times = [str(frame.get("time", "未知")) for frame in frames[:6]]
    direct_observation = f"命中 {len(frames)} 帧，时间点: {frame_times}" if frames else "无直接视觉证据"
    final_summary = reason if reason.strip() else direct_observation
    return {
        "observation": {
            "source": "direct_vision",
            "content": direct_observation,
        },
        "context_calibration": {
            "source": "structured_global_context",
            "content": "未使用上下文消歧（无前序分片摘要）",
        },
        "final_summary": final_summary,
    }


def _normalize_structured_payload(payload: Dict[str, Any], fallback_summary: str) -> Dict[str, Any]:
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        observation = {"source": "direct_vision", "content": ""}

    context_calibration = payload.get("context_calibration")
    if not isinstance(context_calibration, dict):
        context_calibration = {"source": "structured_global_context", "content": ""}

    final_summary = str(payload.get("final_summary", "")).strip()
    if not final_summary:
        final_summary = fallback_summary.strip() or "证据不足"

    return {
        "observation": {
            "source": str(observation.get("source", "direct_vision") or "direct_vision"),
            "content": str(observation.get("content", "") or ""),
        },
        "context_calibration": {
            "source": str(context_calibration.get("source", "structured_global_context") or "structured_global_context"),
            "content": str(context_calibration.get("content", "") or ""),
        },
        "final_summary": final_summary,
    }


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _llm_vision_chunk_structured(
    chunk_id: str,
    frames: List[FramePayload],
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: BaseModel | None = None,
) -> Dict[str, Any]:
    if not resolve_api_key("vision"):
        times = [str(frame.get("time", "未知")) for frame in frames[:8]]
        fallback = f"[chunk={chunk_id}] 视觉摘要（降级）：命中 {len(frames)} 帧，时间点 {times}"
        return _build_vision_structured_fallback(chunk_id, frames, fallback)

    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请分析该视频分片关键帧，并输出 JSON 对象且只包含 observation、context_calibration、final_summary。"
                f"\\n[user_prompt] {user_prompt}\\n[chunk_id] {chunk_id}"
                f"\\n[structured_global_context] {json.dumps(structured_global_context or {}, ensure_ascii=False)}"
                f"\\n[previous_chunk_summaries] {json.dumps(previous_chunk_summaries or [], ensure_ascii=False)}"
            ),
        }
    ]

    for frame in frames[:8]:
        time_str = str(frame.get("time", "未知"))
        image_b64 = str(frame.get("image", ""))
        content.append({"type": "text", "text": f"时间戳: {time_str}"})
        if image_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"},
                }
            )

    model_client = llm_model or get_model_for_capability("vision")
    model_name = get_model_name_for_capability("vision")
    system_prompt = (
        "你是严谨的视频分片视觉分析助手。请严格遵守以下证据规则：\n"
        "1. 一级证据 (observation)：只能描述你在关键帧图片中直接看到的客观画面、动作或文字。\n"
        "2. 二级证据 (context_calibration)：参考 structured_global_context 和 previous_chunk_summaries（仅前 1-2 个相邻分片摘要），"
        "对一级证据中模糊的词汇进行纠正。"
        "绝对禁止用大纲来捏造画面中不存在的动作。\n"
        "3. 如果画面无法提供有效信息，直接在 final_summary 中声明证据不足。\n"
        "输出必须是 JSON 对象，且只包含 observation、context_calibration、final_summary。"
    )
    messages_payload: Any = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
    raw_content = model_client.chat_completion(
        model=model_name,
        messages=messages_payload,
        temperature=0.2,
        max_tokens=1024,
        response_format={"type": "json_object"},
        timeout=timeout_seconds,
    )
    try:
        parsed = json.loads(raw_content)
    except Exception:
        return _build_vision_structured_fallback(chunk_id, frames, raw_content)
    if not isinstance(parsed, dict):
        return _build_vision_structured_fallback(chunk_id, frames, raw_content)

    fallback_summary = f"[chunk={chunk_id}] 视觉分析结果为空，已降级。"
    return _normalize_structured_payload(parsed, fallback_summary)


def _run_vision_with_retry(
    chunk_id: str,
    frames: List[FramePayload],
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
) -> tuple[Dict[str, Any], str, int]:
    last_error: Exception | None = None
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        try:
            structured = _llm_vision_chunk_structured(
                chunk_id=chunk_id,
                frames=frames,
                user_prompt=user_prompt,
                structured_global_context=structured_global_context,
                previous_chunk_summaries=previous_chunk_summaries,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
            )
            return structured, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("vision worker failed"))
    reason = f"{CHUNK_DEGRADED_MARKER}:vision:{status}:retries_exhausted"
    structured = _build_vision_structured_fallback(chunk_id, frames, reason)
    return structured, status, CHUNK_WORKER_MAX_RETRIES


def _process_single_chunk_vision(
    chunk_id: str,
    frame_indexes: List[int],
    keyframes: List[FramePayload],
    keyframes_base_path: str,
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
) -> tuple[str, ChunkResult]:
    started = time.perf_counter()

    selected_frames: List[FramePayload] = []
    for idx in frame_indexes:
        if not isinstance(idx, int) or idx < 0 or idx >= len(keyframes):
            continue
        frame = keyframes[idx]
        if isinstance(frame, dict):
            normalized_frame = dict(frame)
            normalized_frame["image"] = resolve_frame_image_base64(frame, keyframes_base_path)
            selected_frames.append(normalized_frame)

    if not selected_frames:
        structured_insights = _build_vision_structured_fallback(
            chunk_id,
            selected_frames,
            f"{CHUNK_DEGRADED_MARKER}:vision:degraded:no_keyframe_evidence",
        )
        insights = structured_insights["final_summary"]
        vision_status = "degraded"
        retry_count = 0
        searches: List[Dict[str, str]] = []
    else:
        structured_insights, vision_status, retry_count = _run_vision_with_retry(
            chunk_id,
            selected_frames,
            user_prompt,
            structured_global_context,
            previous_chunk_summaries,
            llm_model,
        )
        insights = str(structured_insights.get("final_summary", "")).strip() or f"{CHUNK_DEGRADED_MARKER}:vision:{vision_status}:empty_summary"

        searches = []
        for frame in selected_frames[: max(0, CHUNK_MAX_TOOL_CALLS)]:
            t = str(frame.get("time", "未知"))
            query = f"video frame context at {t}"
            searches.append({"query": query, "result": _search_with_cache(query)})

    latency_ms = int((time.perf_counter() - started) * 1000)

    delta: ChunkResult = {
        "chunk_id": chunk_id,
        "vision_structured_analysis": structured_insights,
        "vision_insights": insights,
        "evidence_refs": {
            "keyframe_indexes": frame_indexes,
            "vision_searches": searches,
        },
        "token_usage": {
            "vision": 0,
        },
        "modality_status": {
            "vision": vision_status,
        },
        "chunk_retry_count": {
            "vision": retry_count,
        },
        "degraded_context": {
            "vision": vision_status != "ok",
        },
        "latency_ms": {
            "vision": latency_ms,
        },
    }
    return chunk_id, delta


def chunk_vision_worker_node(state: VideoSummaryState) -> dict:
    """
    Send API 路径下的单分片视觉分析 worker。

    地位:
    - Send API 图级 fan-out 下的单分片执行单元。

    任务:
    - 仅读取 current_chunk 命中的关键帧。
    - 生成单个 chunk 的 vision_insights。

    主要输入:
    - state["current_chunk"]
    - state["keyframes"] / state["keyframes_base_path"]
    - state["user_prompt"]

    主要输出:
    - chunk_results: 长度为 1 的列表，包含当前 chunk 的视觉分析结果。
    """
    current_chunk = state.get("current_chunk", {})
    if not isinstance(current_chunk, dict):
        return {"chunk_results": []}

    chunk_id = str(current_chunk.get("chunk_id", "")).strip()
    if not chunk_id:
        return {"chunk_results": []}

    frame_indexes = current_chunk.get("keyframe_indexes", [])
    if not isinstance(frame_indexes, list):
        frame_indexes = []

    keyframes = state.get("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
    keyframes_base_path = str(state.get("keyframes_base_path", ""))
    user_prompt = str(state.get("user_prompt", ""))
    structured_global_context = state.get("structured_global_context", {})
    if not isinstance(structured_global_context, dict):
        structured_global_context = {}
    previous_chunk_summaries = state.get("previous_chunk_summaries", [])
    if not isinstance(previous_chunk_summaries, list):
        previous_chunk_summaries = []

    _, merged = _process_single_chunk_vision(
        chunk_id,
        frame_indexes,
        keyframes,
        keyframes_base_path,
        user_prompt,
        structured_global_context,
        previous_chunk_summaries,
    )

    return {"chunk_results": [merged]}
