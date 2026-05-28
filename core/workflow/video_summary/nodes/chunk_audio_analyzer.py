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
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span

_AUDIO_SYSTEM_PROMPT = (
    "你是严谨的视频分片转录文本分析助手。\n\n"
    "【任务】从当前分片的 transcript 中提取原子事实断言（claims）。\n\n"
    "【严格约束】：\n"
    "1. 禁止补造：每条 claim 必须有 exact_quote（直接引自 transcript 原文）。\n"
    "2. 时间戳：timestamp 必须来自 transcript 中对应片段的时间信息（秒数或 HH:MM:SS）。\n"
    "3. 章节定位：参考 narrative_arc 中当前时间段所属章节，帮助理解上下文语义，"
    "但禁止用章节内容补充 transcript 中未出现的断言。\n"
    "4. JSON 输出：直接输出 JSON 数组，不要有其他说明文字。\n\n"
    "输出格式（JSON 数组）：\n"
    '[  {"claim": "断言内容", "exact_quote": "transcript 原话", "timestamp": "HH:MM:SS 或秒数"}, ... ]'
)


def _format_timestamp(seconds) -> str:
    try:
        s = float(seconds)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"
    except Exception:
        return str(seconds)


def _build_transcript_text_with_timestamps(transcript_segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in transcript_segments:
        start = seg.get("start", seg.get("timestamp", 0))
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{_format_timestamp(start)}] {text}")
    return "\n".join(lines)


def _parse_transcript_claims(raw_text: str) -> List[Dict[str, Any]]:
    try:
        raw_text = raw_text.strip()
        json_match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)
        claims = json.loads(raw_text)
        if not isinstance(claims, list):
            return []
        validated: List[Dict[str, Any]] = []
        for c in claims:
            if not isinstance(c, dict):
                continue
            validated.append({
                "claim": str(c.get("claim", "")).strip(),
                "exact_quote": str(c.get("exact_quote", "")).strip(),
                "timestamp": str(c.get("timestamp", "")).strip(),
            })
        return validated
    except Exception:
        return []


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _llm_extract_transcript_claims(
    chunk_id: str,
    transcript_text: str,
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> List[Dict[str, Any]]:
    if not resolve_api_key("chat"):
        return []

    model_client = llm_model or get_model_for_capability("chat")
    model_name = get_model_name_for_capability("chat")

    narrative_arc_json = json.dumps(narrative_arc or [], ensure_ascii=False)
    previous_summaries_json = json.dumps(previous_chunk_summaries or [], ensure_ascii=False)

    user_content = (
        f"[chunk_id]\n{chunk_id}\n\n"
        f"[user_prompt]\n{user_prompt}\n\n"
        f"[narrative_arc（全局章节大纲，仅用于语义定位）]\n{narrative_arc_json}\n\n"
        f"[previous_chunk_summaries（相邻分片摘要，仅用于消歧）]\n{previous_summaries_json}\n\n"
        f"[chunk_transcript]\n{transcript_text}"
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
                {"role": "system", "content": _AUDIO_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            timeout=timeout_seconds,
        )

    return _parse_transcript_claims(raw_content)


def _run_audio_with_retry(
    chunk_id: str,
    transcript_text: str,
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> "Tuple[List[Dict[str, Any]], str, int]":
    last_error: "Exception | None" = None
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        try:
            claims = _llm_extract_transcript_claims(
                chunk_id=chunk_id,
                transcript_text=transcript_text,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            return claims, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("audio worker failed"))
    return [], status, CHUNK_WORKER_MAX_RETRIES


def chunk_audio_worker_node(state: ChunkState, llm_model: "BaseModel | None" = None) -> dict:
    """
    子图内的音频分析 worker（audio -> vision 顺序流水线中的第一步）。

    输出写入 ChunkState:
    - transcript_claims: [{claim, exact_quote, timestamp}]
    - modality_status: {"audio": "ok"|"degraded"|"timeout"|"failed"}
    - latency_ms: {"audio": <ms>}
    """
    chunk_id = str(state.get("chunk_id", "")).strip()
    if not chunk_id:
        return {
            "transcript_claims": [],
            "modality_status": {"audio": "degraded"},
            "latency_ms": {"audio": 0},
        }

    transcript_segments = state.get("transcript_segments", [])
    if not isinstance(transcript_segments, list):
        transcript_segments = []

    user_prompt = str(state.get("user_prompt", ""))
    narrative_arc = state.get("narrative_arc") or []
    if not isinstance(narrative_arc, list):
        narrative_arc = []
    previous_chunk_summaries = state.get("previous_chunk_summaries", [])
    if not isinstance(previous_chunk_summaries, list):
        previous_chunk_summaries = []
    trace_id = str(state.get("trace_id", ""))

    started = time.perf_counter()
    transcript_text = _build_transcript_text_with_timestamps(transcript_segments)

    with start_span(
        build_span_name("workflow", "chunk_audio", "analyze"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_chunk",
            "scope_id": chunk_id,
            "workflow_state": "ANALYSIS",
        },
    ):
        if not transcript_text:
            transcript_claims: List[Dict[str, Any]] = []
            audio_status = "degraded"
            print(f"  -> [Audio Worker] {chunk_id}: no transcript evidence, degraded.")
        else:
            transcript_claims, audio_status, _ = _run_audio_with_retry(
                chunk_id=chunk_id,
                transcript_text=transcript_text,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            if audio_status != "ok":
                print(f"  -> [Audio Worker] {chunk_id}: status={audio_status}, claims={len(transcript_claims)}")
            else:
                print(f"  -> [Audio Worker] {chunk_id}: extracted {len(transcript_claims)} transcript claims.")

    latency_ms = int((time.perf_counter() - started) * 1000)

    existing_modality = state.get("modality_status") or {}
    new_modality = {**existing_modality, "audio": audio_status}
    existing_latency = state.get("latency_ms") or {}
    new_latency = {**existing_latency, "audio": latency_ms}

    return {
        "transcript_claims": transcript_claims,
        "modality_status": new_modality,
        "latency_ms": new_latency,
    }
