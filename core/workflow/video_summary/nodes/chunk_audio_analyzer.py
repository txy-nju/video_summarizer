import json
import re
import time
import threading
from typing import Any, Dict, List, Tuple

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
from core.workflow.video_summary.chunk_state import ChunkState
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span


def _load_transcript_data(transcript: str) -> Dict[str, Any]:
    if not transcript or not transcript.strip():
        return {}
    try:
        data = json.loads(transcript)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _build_transcript_items(transcript_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    segments = transcript_data.get("segments", [])
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict):
                items.append(seg)

    chunks = transcript_data.get("chunks", [])
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                items.append(chunk)

    return items


def _extract_chunk_text(transcript_items: List[Dict[str, Any]], indexes: List[int]) -> str:
    lines: List[str] = []
    for idx in indexes:
        if not isinstance(idx, int) or idx < 0 or idx >= len(transcript_items):
            continue
        text = str(transcript_items[idx].get("text", "")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)





def _build_audio_structured_fallback(chunk_id: str, chunk_text: str, reason: str) -> Dict[str, Any]:
    return {
        "verified_insights": []
    }


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


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
            "exact_quote": str(insight.get("exact_quote", ""))
        })

    return {
        "verified_insights": normalized
    }


def _llm_audio_chunk_structured(
    chunk_id: str,
    chunk_text: str,
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: BaseModel | None = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    if not resolve_api_key("chat"):
        fallback = f"[chunk={chunk_id}] 音频摘要（降级）:\n" + (chunk_text[:500] if chunk_text else "无可用语音证据")
        return _build_audio_structured_fallback(chunk_id, chunk_text, fallback)

    model_client = llm_model or get_model_for_capability("chat")
    model_name = get_model_name_for_capability("chat")
    global_context_json = json.dumps(structured_global_context or {}, ensure_ascii=False)
    previous_summaries_json = json.dumps(previous_chunk_summaries or [], ensure_ascii=False)
    system_prompt = (
        "你是严谨的视频分片音频转录文本分析助手。请严格遵守以下证据规则：\n"
        "1. 提取讲师提到的核心事实（例如术语、步骤、结论）。\n"
        "2. 对于每一条提取的事实，必须在 verified_insights 列表中输出一个对象，包含：\n"
        "   - claim: 事实陈述\n"
        "   - exact_quote: 必须是能从 transcript 中找到的原话，找不到原话的禁止输出。\n"
        "   - timestamp: 原话发生的大致时间（如果有提供秒数，如没有则填空或提供大概位置）。\n"
        "3. 必须参考 structured_global_context 确定当前分片在全篇的宏观章节定位，严禁越界脑补前后文。\n"
        "输出必须是 JSON 对象，包含 verified_insights 数组。"
    )
    with trace_llm_call(
        provider="openai_compatible",
        model=model_name,
        scope="chunk_audio_worker",
        scope_id=chunk_id,
        workflow_state="ANALYSIS",
    ):
        raw_content = model_client.chat_completion(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        f"[chunk_id]\n{chunk_id}\n\n"
                        f"[user_prompt]\n{user_prompt}\n\n"
                        f"[structured_global_context]\n{global_context_json}\n\n"
                        f"[previous_chunk_summaries]\n{previous_summaries_json}\n\n"
                        f"[chunk_transcript]\n{chunk_text}"
                    ),
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=timeout_seconds,
        )
    try:
        parsed = json.loads(raw_content)
    except Exception:
        return _build_audio_structured_fallback(chunk_id, chunk_text, raw_content)
    if not isinstance(parsed, dict):
        return _build_audio_structured_fallback(chunk_id, chunk_text, raw_content)

    fallback_summary = f"[chunk={chunk_id}] 音频分析结果为空，已降级。"
    return _normalize_structured_payload(parsed, fallback_summary)


def _run_audio_with_retry(
    chunk_id: str,
    chunk_text: str,
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
    trace_id: str = "",
) -> Tuple[Dict[str, Any], str, int]:
    last_error: Exception | None = None
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        try:
            structured = _llm_audio_chunk_structured(
                chunk_id=chunk_id,
                chunk_text=chunk_text,
                user_prompt=user_prompt,
                structured_global_context=structured_global_context,
                previous_chunk_summaries=previous_chunk_summaries,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            return structured, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("audio worker failed"))
    reason = f"{CHUNK_DEGRADED_MARKER}:audio:{status}:retries_exhausted"
    structured = _build_audio_structured_fallback(chunk_id, chunk_text, reason)
    return structured, status, CHUNK_WORKER_MAX_RETRIES


def _process_single_chunk_audio(
    chunk_id: str,
    indexes: List[int],
    transcript_items: List[Dict[str, Any]],
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: BaseModel | None = None,
    trace_id: str = "",
) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    chunk_text = _extract_chunk_text(transcript_items, indexes)

    if not chunk_text:
        structured_insights = _build_audio_structured_fallback(
            chunk_id,
            chunk_text,
            f"{CHUNK_DEGRADED_MARKER}:audio:degraded:no_transcript_evidence",
        )
        insights = structured_insights.get("verified_insights", [])
        audio_status = "degraded"
        retry_count = 0
        searches: List[Dict[str, str]] = []
    else:
        structured_insights, audio_status, retry_count = _run_audio_with_retry(
            chunk_id,
            chunk_text,
            user_prompt,
            structured_global_context,
            previous_chunk_summaries,
            llm_model,
            trace_id,
        )
        insights = structured_insights.get("verified_insights", [])

    latency_ms = int((time.perf_counter() - started) * 1000)

    delta = {
        "chunk_id": chunk_id,
        "audio_structured_analysis": structured_insights,
        "verified_insights": insights,
        "evidence_refs": {
            "transcript_segment_indexes": indexes,
        },
        "token_usage": {
            "audio": 0,
        },
        "modality_status": {
            "audio": audio_status,
        },
        "chunk_retry_count": {
            "audio": retry_count,
        },
        "degraded_context": {
            "audio": audio_status != "ok",
        },
        "latency_ms": {
            "audio": latency_ms,
        },
    }
    return chunk_id, delta


def chunk_audio_worker_node(state: ChunkState) -> dict:
    """
    Send API 路径下的单分片音频分析 worker。

    地位:
    - Send API 图级 fan-out 下的单分片执行单元。

    任务:
    - 仅读取 current_chunk 对应的 transcript 片段。
    - 生成单个 chunk 的 audio_insights。

    主要输入:
    - state["current_chunk"]
    - state["transcript"]
    - state["user_prompt"]

    主要输出:
    - chunk_results: 长度为 1 的列表，包含当前 chunk 的音频分析结果。
    """
    current_chunk = state.get("current_chunk", {})
    if not isinstance(current_chunk, dict):
        return {"chunk_results": []}

    chunk_id = str(current_chunk.get("chunk_id", "")).strip()
    if not chunk_id:
        return {"chunk_results": []}

    indexes = current_chunk.get("transcript_segment_indexes", [])
    if not isinstance(indexes, list):
        indexes = []

    transcript = str(state.get("transcript", ""))
    user_prompt = str(state.get("user_prompt", ""))
    structured_global_context = state.get("structured_global_context", {})
    if not isinstance(structured_global_context, dict):
        structured_global_context = {}
    previous_chunk_summaries = state.get("previous_chunk_summaries", [])
    if not isinstance(previous_chunk_summaries, list):
        previous_chunk_summaries = []
    trace_id = str(state.get("trace_id", ""))
    transcript_items = _build_transcript_items(_load_transcript_data(transcript))

    with start_span(
        build_span_name("workflow", "chunk_audio", "analyze"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_chunk",
            "scope_id": chunk_id,
            "workflow_state": "ANALYSIS",
        },
    ):
        _, merged = _process_single_chunk_audio(
            chunk_id,
            indexes,
            transcript_items,
            user_prompt,
            structured_global_context,
            previous_chunk_summaries,
            trace_id=trace_id,
        )

    if str(merged.get("modality_status", {}).get("audio", "ok")).lower() != "ok":
        error_code = f"AUDIO_{str(merged.get('modality_status', {}).get('audio', 'FAILED')).upper()}"
        merged["error_code"] = error_code
        merged["status"] = "ERROR"

    return {"chunk_results": [merged]}
