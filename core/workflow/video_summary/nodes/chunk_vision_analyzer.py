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
from core.workflow.video_summary.state import VideoSummaryState, _merge_chunk_results
from core.workflow.video_summary.chunk_state import ChunkState
from core.workflow.video_summary.utils.frame_utils import resolve_frame_image_base64
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span


FramePayload = Dict[str, Any]
ChunkResult = Dict[str, Any]





def _build_vision_structured_fallback(chunk_id: str, frames: List[FramePayload], reason: str, audio_insights: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "verified_insights": audio_insights or []
    }


def _normalize_structured_payload(payload: Dict[str, Any], fallback_summary: str) -> Dict[str, Any]:
    insights = payload.get("verified_insights", [])
    if not isinstance(insights, list):
        insights = []

    normalized = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        normalized.append({
            "time_sec": insight.get("time_sec", 0),
            "timestamp": str(insight.get("timestamp", "")),
            "claim": str(insight.get("claim", "")),
            "exact_quote": str(insight.get("exact_quote", "")),
            "frame_ref": str(insight.get("frame_ref", ""))
        })

    return {
        "verified_insights": normalized
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
    audio_insights: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: BaseModel | None = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    if not resolve_api_key("vision"):
        times = [str(frame.get("time", "未知")) for frame in frames[:8]]
        fallback = f"[chunk={chunk_id}] 视觉摘要（降级）：命中 {len(frames)} 帧，时间点 {times}"
        return _build_vision_structured_fallback(chunk_id, frames, fallback, audio_insights)

    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请分析该视频分片关键帧。重点是：参考提供的 audio_insights，在画面中寻找证据并补充视觉细节。\n"
                f"\\n[user_prompt] {user_prompt}\\n[chunk_id] {chunk_id}"
                f"\\n[structured_global_context] {json.dumps(structured_global_context or {}, ensure_ascii=False)}"
                f"\\n[previous_chunk_summaries] {json.dumps(previous_chunk_summaries or [], ensure_ascii=False)}"
                f"\\n[audio_insights] {json.dumps(audio_insights or [], ensure_ascii=False)}"
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
        "你是严谨的视频分片视觉验证助手。请严格遵守以下规则：\n"
        "1. 你的主要任务是验证和补全 audio_insights 中的事实。\n"
        "2. 针对 audio_insights 中的每一条事实，如果在画面中看到了相关元素，请在 claim 中增加视觉细节描述（如补充关键帧中ppt的语音未涉及到的知识要点等）；"
        "如果没有在画面中看到，请显式声明'画面未展示'。\n"
        "3. 你也可以补充纯视觉发现的重要事实。\n"
        "4. 输出必须是 JSON 对象，包含 verified_insights 数组。每个对象需包含:\n"
        "   - time_sec: 秒数 (如果是验证音频，保留原时间戳)\n"
        "   - timestamp: 时间戳字符串 (如 02:15)\n"
        "   - claim: 缝合了视听细节的事实陈述\n"
        "   - exact_quote: 传承下来的音频原话 (如适用)\n"
        "   - frame_ref: 支撑该视觉结论的画面引用 (例如时间戳或画面序号)"
    )
    messages_payload: Any = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
    with trace_llm_call(
        provider="openai_compatible",
        model=model_name,
        scope="chunk_vision_worker",
        scope_id=chunk_id,
        workflow_state="ANALYSIS",
    ):
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
        return _build_vision_structured_fallback(chunk_id, frames, raw_content, audio_insights)
    if not isinstance(parsed, dict):
        return _build_vision_structured_fallback(chunk_id, frames, raw_content, audio_insights)

    fallback_summary = f"[chunk={chunk_id}] 视觉分析结果为空，已降级。"
    return _normalize_structured_payload(parsed, fallback_summary)


def _run_vision_with_retry(
    chunk_id: str,
    frames: List[FramePayload],
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    audio_insights: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
    trace_id: str = "",
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
                audio_insights=audio_insights,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            return structured, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("vision worker failed"))
    reason = f"{CHUNK_DEGRADED_MARKER}:vision:{status}:retries_exhausted"
    structured = _build_vision_structured_fallback(chunk_id, frames, reason, audio_insights)
    return structured, status, CHUNK_WORKER_MAX_RETRIES


def _process_single_chunk_vision(
    chunk_id: str,
    frame_indexes: List[int],
    keyframes: List[FramePayload],
    keyframes_base_path: str,
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    audio_insights: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
    trace_id: str = "",
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
            audio_insights
        )
        insights = structured_insights.get("verified_insights", [])
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
            audio_insights,
            llm_model,
            trace_id,
        )
        insights = structured_insights.get("verified_insights", [])

    latency_ms = int((time.perf_counter() - started) * 1000)

    delta: ChunkResult = {
        "chunk_id": chunk_id,
        "vision_structured_analysis": structured_insights,
        "verified_insights": insights,
        "evidence_refs": {
            "keyframe_indexes": frame_indexes,
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


def chunk_vision_worker_node(state: ChunkState) -> dict:
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
    chunk_results = state.get("chunk_results", [])
    audio_insights = []
    if chunk_results and isinstance(chunk_results, list) and len(chunk_results) > 0:
        audio_insights = chunk_results[0].get("verified_insights", [])
    trace_id = str(state.get("trace_id", ""))

    with start_span(
        build_span_name("workflow", "chunk_vision", "analyze"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_chunk",
            "scope_id": chunk_id,
            "workflow_state": "ANALYSIS",
        },
    ):
        _, merged = _process_single_chunk_vision(
            chunk_id,
            frame_indexes,
            keyframes,
            keyframes_base_path,
            user_prompt,
            structured_global_context,
            previous_chunk_summaries,
            audio_insights,
            trace_id=trace_id,
        )

    if str(merged.get("modality_status", {}).get("vision", "ok")).lower() != "ok":
        error_code = f"VISION_{str(merged.get('modality_status', {}).get('vision', 'FAILED')).upper()}"
        merged["error_code"] = error_code
        merged["status"] = "ERROR"

    existing_results = state.get("chunk_results", [])
    final_results = _merge_chunk_results(existing_results, [merged])
    
    return {"chunk_results": final_results}
