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
from core.workflow.video_summary.tools.search_tools import execute_tavily_search

_AUDIO_SEARCH_CACHE: Dict[str, str] = {}
_AUDIO_SEARCH_CACHE_LOCK = threading.Lock()


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


def _candidate_queries(text: str, max_calls: int) -> List[str]:
    # 以大写缩写和驼峰词作为轻量候选，限制每个 chunk 的搜索次数
    acronyms = re.findall(r"\b[A-Z]{2,}\b", text)
    camel_words = re.findall(r"\b[A-Za-z]+[A-Z][A-Za-z]+\b", text)

    candidates: List[str] = []
    for token in acronyms + camel_words:
        if token not in candidates:
            candidates.append(token)
        if len(candidates) >= max_calls:
            break
    return candidates


def _search_with_cache(query: str) -> str:
    if ENABLE_CHUNK_CACHE:
        with _AUDIO_SEARCH_CACHE_LOCK:
            cached = _AUDIO_SEARCH_CACHE.get(query)
        if cached is not None:
            return cached

    result = execute_tavily_search(query)
    if ENABLE_CHUNK_CACHE:
        with _AUDIO_SEARCH_CACHE_LOCK:
            _AUDIO_SEARCH_CACHE[query] = result
    return result


def _build_audio_structured_fallback(chunk_id: str, chunk_text: str, reason: str) -> Dict[str, Any]:
    direct_observation = chunk_text[:300] if chunk_text else "无直接音频证据"
    final_summary = reason if reason.strip() else (direct_observation[:180] if direct_observation else "证据不足")
    return {
        "observation": {
            "source": "direct_audio",
            "content": direct_observation,
        },
        "context_calibration": {
            "source": "structured_global_context",
            "content": "未使用上下文消歧（无前序分片摘要）",
        },
        "final_summary": final_summary,
    }


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _normalize_structured_payload(payload: Dict[str, Any], fallback_summary: str) -> Dict[str, Any]:
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        observation = {"source": "direct_audio", "content": ""}

    context_calibration = payload.get("context_calibration")
    if not isinstance(context_calibration, dict):
        context_calibration = {"source": "structured_global_context", "content": ""}

    final_summary = str(payload.get("final_summary", "")).strip()
    if not final_summary:
        final_summary = fallback_summary.strip() or "证据不足"

    return {
        "observation": {
            "source": str(observation.get("source", "direct_audio") or "direct_audio"),
            "content": str(observation.get("content", "") or ""),
        },
        "context_calibration": {
            "source": str(context_calibration.get("source", "structured_global_context") or "structured_global_context"),
            "content": str(context_calibration.get("content", "") or ""),
        },
        "final_summary": final_summary,
    }


def _llm_audio_chunk_structured(
    chunk_id: str,
    chunk_text: str,
    user_prompt: str,
    structured_global_context: Dict[str, Any],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: BaseModel | None = None,
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
        "1. 一级证据 (observation)：只能描述你在当前 transcript 分片中直接读到的客观内容，例如术语、陈述、数字、口播要点。\n"
        "2. 二级证据 (context_calibration)：参考 structured_global_context 和 previous_chunk_summaries（仅前 1-2 个相邻分片摘要），"
        "对一级证据中的模糊称呼、缩写或术语进行纠正。"
        "绝对禁止用大纲来捏造 transcript 中没有出现过的观点、结论或因果关系。\n"
        "3. 如果 transcript 无法提供有效信息，直接在 final_summary 中声明证据不足。\n"
        "输出必须是 JSON 对象，且只包含 observation、context_calibration、final_summary。"
    )
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
) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    chunk_text = _extract_chunk_text(transcript_items, indexes)

    if not chunk_text:
        structured_insights = _build_audio_structured_fallback(
            chunk_id,
            chunk_text,
            f"{CHUNK_DEGRADED_MARKER}:audio:degraded:no_transcript_evidence",
        )
        insights = structured_insights["final_summary"]
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
        )
        insights = str(structured_insights.get("final_summary", "")).strip() or f"{CHUNK_DEGRADED_MARKER}:audio:{audio_status}:empty_summary"

        searches = []
        for query in _candidate_queries(chunk_text, max(0, CHUNK_MAX_TOOL_CALLS)):
            searches.append({"query": query, "result": _search_with_cache(query)})

    latency_ms = int((time.perf_counter() - started) * 1000)

    delta = {
        "chunk_id": chunk_id,
        "audio_structured_analysis": structured_insights,
        "audio_insights": insights,
        "evidence_refs": {
            "transcript_segment_indexes": indexes,
            "audio_searches": searches,
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


def chunk_audio_worker_node(state: VideoSummaryState) -> dict:
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
    transcript_items = _build_transcript_items(_load_transcript_data(transcript))

    _, merged = _process_single_chunk_audio(
        chunk_id,
        indexes,
        transcript_items,
        user_prompt,
        structured_global_context,
        previous_chunk_summaries,
        None,
    )
    return {"chunk_results": [merged]}
