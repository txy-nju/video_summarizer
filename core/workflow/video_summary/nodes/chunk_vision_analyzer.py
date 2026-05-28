import json
import re
import time
from typing import Any, Dict, List, Tuple

from core.llm.base import BaseModel
from core.llm.config import resolve_api_key
from core.llm.factory import get_model_for_capability, get_model_name_for_capability

from config.settings import (
    CHUNK_DEGRADED_MARKER,
    CHUNK_WORKER_MAX_RETRIES,
    CHUNK_WORKER_TIMEOUT_SECONDS,
)
from core.workflow.video_summary.nodes.chunk_state import ChunkState
from core.workflow.video_summary.utils.frame_utils import resolve_frame_image_base64
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span

FramePayload = Dict[str, Any]

_VISION_SYSTEM_PROMPT = (
    "你是严谨的视频分片视觉分析助手。\n\n"
    "【任务】根据当前分片的关键帧画面，对音频层传入的 transcript_claims 进行交叉核验，\n"
    "生成 frame_references（逐帧视觉观察记录）和 chunk_summary（融合叙事摘要）。\n\n"
    "【严格约束】：\n"
    "1. frame_references 每条记录对应一个关键帧：\n"
    "   - frame_time: 关键帧时间戳\n"
    "   - observation: 对该帧画面的客观描述（只能描述直接可见内容）\n"
    "   - audio_claim_match: confirmed（画面支持断言）| absent（无关）| contradicted（矛盾）\n"
    "2. chunk_summary: 基于 transcript_claims + frame_references 的综合叙事摘要。\n"
    "3. 禁止臆造：observation 只能描述实际可见内容，不得推断画面外信息。\n"
    '4. JSON 输出：{"frame_references": [...], "chunk_summary": "..."}'
)


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _build_fallback_output(chunk_id: str, frames: List[FramePayload], reason: str) -> Dict[str, Any]:
    frame_refs = [
        {
            "frame_time": str(f.get("time", "未知")),
            "observation": f"{CHUNK_DEGRADED_MARKER}:vision:degraded",
            "audio_claim_match": "absent",
        }
        for f in frames[:6]
    ]
    return {
        "frame_references": frame_refs,
        "chunk_summary": reason if reason.strip() else f"{CHUNK_DEGRADED_MARKER}:vision:degraded:no_visual_evidence",
    }


def _parse_vision_output(raw_text: str) -> Dict[str, Any]:
    try:
        raw_text = raw_text.strip()
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return {}
        frame_references: List[Dict[str, Any]] = []
        for ref in parsed.get("frame_references", []):
            if not isinstance(ref, dict):
                continue
            frame_references.append({
                "frame_time": str(ref.get("frame_time", "")).strip(),
                "observation": str(ref.get("observation", "")).strip(),
                "audio_claim_match": str(ref.get("audio_claim_match", "absent")).strip(),
            })
        return {
            "frame_references": frame_references,
            "chunk_summary": str(parsed.get("chunk_summary", "")).strip(),
        }
    except Exception:
        return {}


def _llm_vision_analyze(
    chunk_id: str,
    frames: List[FramePayload],
    transcript_claims: List[Dict[str, Any]],
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    if not resolve_api_key("vision"):
        return _build_fallback_output(chunk_id, frames, f"{CHUNK_DEGRADED_MARKER}:vision:degraded:no_api_key")

    narrative_arc_json = json.dumps(narrative_arc or [], ensure_ascii=False)
    claims_json = json.dumps(transcript_claims or [], ensure_ascii=False)
    previous_summaries_json = json.dumps(previous_chunk_summaries or [], ensure_ascii=False)

    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"[chunk_id] {chunk_id}\n"
                f"[user_prompt] {user_prompt}\n"
                f"[narrative_arc（全局章节大纲，仅用于语义定位）] {narrative_arc_json}\n"
                f"[previous_chunk_summaries（相邻分片摘要，仅用于消歧）] {previous_summaries_json}\n"
                f"[transcript_claims（音频层已提取的断言，待画面交叉验证）] {claims_json}\n\n"
                "请分析以下关键帧并输出 frame_references + chunk_summary："
            ),
        }
    ]

    for frame in frames[:8]:
        time_str = str(frame.get("time", "未知"))
        image_b64 = str(frame.get("image", ""))
        content.append({"type": "text", "text": f"关键帧时间戳: {time_str}"})
        if image_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"},
            })

    model_client = llm_model or get_model_for_capability("vision")
    model_name = get_model_name_for_capability("vision")

    with trace_llm_call(
        provider="openai_compatible",
        model=model_name,
        scope="chunk_vision_worker",
        scope_id=chunk_id,
        workflow_state="ANALYSIS",
    ):
        raw_content = model_client.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
            max_tokens=1500,
            timeout=timeout_seconds,
        )

    result = _parse_vision_output(raw_content)
    if not result:
        return _build_fallback_output(chunk_id, frames, raw_content[:500] if raw_content else "")
    return result


def _run_vision_with_retry(
    chunk_id: str,
    frames: List[FramePayload],
    transcript_claims: List[Dict[str, Any]],
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> "Tuple[Dict[str, Any], str, int]":
    last_error: "Exception | None" = None
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        try:
            result = _llm_vision_analyze(
                chunk_id=chunk_id,
                frames=frames,
                transcript_claims=transcript_claims,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            return result, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("vision worker failed"))
    fallback = _build_fallback_output(
        chunk_id, frames,
        f"{CHUNK_DEGRADED_MARKER}:vision:{status}:retries_exhausted",
    )
    return fallback, status, CHUNK_WORKER_MAX_RETRIES


def chunk_vision_worker_node(state: ChunkState, llm_model: "BaseModel | None" = None) -> dict:
    """
    子图内的视觉分析 worker（audio -> vision 顺序流水线中的第二步）。

    读取 ChunkState.transcript_claims（由 chunk_audio_worker_node 写入，顺序保证）。
    对关键帧进行视觉分析，逐条交叉核验 transcript_claims。

    输出写入 ChunkState:
    - frame_references: [{frame_time, observation, audio_claim_match}]
    - chunk_summary: 融合叙事摘要（聚合器降级兜底用）
    - modality_status: {"vision": "ok"|"degraded"|"timeout"|"failed"}
    - latency_ms: {"vision": <ms>}
    """
    chunk_id = str(state.get("chunk_id", "")).strip()
    if not chunk_id:
        return {
            "frame_references": [],
            "chunk_summary": f"{CHUNK_DEGRADED_MARKER}:vision:degraded:no_chunk_id",
            "modality_status": {"vision": "degraded"},
            "latency_ms": {"vision": 0},
        }

    keyframe_indexes = state.get("keyframe_indexes", [])
    if not isinstance(keyframe_indexes, list):
        keyframe_indexes = []
    keyframes = state.get("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
    keyframes_base_path = str(state.get("keyframes_base_path", ""))
    user_prompt = str(state.get("user_prompt", ""))
    narrative_arc = state.get("narrative_arc") or []
    if not isinstance(narrative_arc, list):
        narrative_arc = []
    transcript_claims = state.get("transcript_claims", [])
    if not isinstance(transcript_claims, list):
        transcript_claims = []
    previous_chunk_summaries = state.get("previous_chunk_summaries", [])
    if not isinstance(previous_chunk_summaries, list):
        previous_chunk_summaries = []
    trace_id = str(state.get("trace_id", ""))

    started = time.perf_counter()

    selected_frames: List[FramePayload] = []
    for idx in keyframe_indexes:
        if not isinstance(idx, int) or idx < 0 or idx >= len(keyframes):
            continue
        frame = keyframes[idx]
        if isinstance(frame, dict):
            normalized_frame = dict(frame)
            normalized_frame["image"] = resolve_frame_image_base64(frame, keyframes_base_path)
            selected_frames.append(normalized_frame)

    with start_span(
        build_span_name("workflow", "chunk_vision", "analyze"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_chunk",
            "scope_id": chunk_id,
            "workflow_state": "ANALYSIS",
        },
    ):
        if not selected_frames:
            result = _build_fallback_output(
                chunk_id, [],
                f"{CHUNK_DEGRADED_MARKER}:vision:degraded:no_keyframe_evidence",
            )
            vision_status = "degraded"
            print(f"  -> [Vision Worker] {chunk_id}: no keyframe evidence, degraded.")
        else:
            result, vision_status, _ = _run_vision_with_retry(
                chunk_id=chunk_id,
                frames=selected_frames,
                transcript_claims=transcript_claims,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            if vision_status != "ok":
                print(f"  -> [Vision Worker] {chunk_id}: status={vision_status}")
            else:
                print(f"  -> [Vision Worker] {chunk_id}: {len(result.get('frame_references', []))} frame_references extracted.")

    latency_ms = int((time.perf_counter() - started) * 1000)

    existing_modality = state.get("modality_status") or {}
    new_modality = {**existing_modality, "vision": vision_status}
    existing_latency = state.get("latency_ms") or {}
    new_latency = {**existing_latency, "vision": latency_ms}

    return {
        "frame_references": result.get("frame_references", []),
        "chunk_summary": result.get("chunk_summary", ""),
        "modality_status": new_modality,
        "latency_ms": new_latency,
    }
