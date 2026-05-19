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
from core.workflow.video_summary.state import VideoSummaryState


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "empty summary" in text:
        return "degraded"
    return "failed"


def _llm_chunk_fusion(
    chunk_id: str,
    audio_insights: str,
    vision_insights: str,
    structured_global_context:str,
    user_prompt: str,
    timeout_seconds: float,
    llm_model: BaseModel | None = None,
) -> str:
    if not resolve_api_key("chat"):
        return (
            f"[chunk={chunk_id}] 分片融合（降级）\\n"
            f"- Audio: {audio_insights[:200]}\\n"
            f"- Vision: {vision_insights[:200]}"
        )

    model_client = llm_model or get_model_for_capability("chat")
    model_name = get_model_name_for_capability("chat")
    return model_client.chat_completion(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "你是分片融合助手，请将音频洞察与视觉洞察融合为该时间片的简洁总结。",
            },
            {
                "role": "user",
                "content": (
                    f"[chunk_id]\\n{chunk_id}\\n\\n"
                    f"[user_prompt]\\n{user_prompt}\\n\\n"
                    f"[audio_insights]\\n{audio_insights}\\n\\n"
                    f"[vision_insights]\\n{vision_insights}\\n\\n"
                    f"[structured_global_context]\\n{structured_global_context}"
                ),
            },
        ],
        temperature=0.3,
        timeout=timeout_seconds,
    )


def _process_single_chunk_synthesis(
    chunk_id: str,
    user_prompt: str,
    structured_global_context:str,
    base_item: Dict[str, Any],
    llm_model: BaseModel | None = None,
) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    audio_insights = str(base_item.get("audio_insights", ""))
    vision_insights = str(base_item.get("vision_insights", ""))

    status = "ok"
    try:
        chunk_summary = _llm_chunk_fusion(
            chunk_id,
            audio_insights,
            vision_insights,
            structured_global_context,
            user_prompt,
            CHUNK_WORKER_TIMEOUT_SECONDS,
            llm_model,
        )
        if not str(chunk_summary).strip():
            raise ValueError("empty summary")
    except Exception as exc:
        status = _classify_error(exc)
        chunk_summary = f"{CHUNK_DEGRADED_MARKER}:synthesizer:{status}:{str(exc)}"

    latency_ms = int((time.perf_counter() - started) * 1000)
    delta = {
        "chunk_id": chunk_id,
        "chunk_summary": chunk_summary,
        "modality_status": {
            "synthesizer": status,
        },
        "latency_ms": {
            "synthesizer": latency_ms,
        },
    }
    return chunk_id, delta


def _run_synthesis_with_retry(
    chunk_id: str,
    user_prompt: str,
    structured_global_context:str,
    base_item: Dict[str, Any],
    llm_model: BaseModel | None = None,
) -> Tuple[str, Dict[str, Any]]:
    last_delta: Dict[str, Any] = {
        "chunk_id": chunk_id,
        "chunk_summary": f"{CHUNK_DEGRADED_MARKER}:synthesizer:failed:no_attempt",
        "modality_status": {"synthesizer": "failed"},
        "latency_ms": {"synthesizer": 0},
    }

    retries_used = 0
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        _, delta = _process_single_chunk_synthesis(chunk_id, user_prompt, structured_global_context ,base_item, llm_model)
        last_delta = dict(delta)
        status = str(last_delta.get("modality_status", {}).get("synthesizer", "ok")).strip().lower()
        retries_used = attempt
        if status == "ok":
            break

    status = str(last_delta.get("modality_status", {}).get("synthesizer", "failed")).strip().lower()
    last_delta["chunk_retry_count"] = {"synthesizer": retries_used}
    last_delta["degraded_context"] = {"synthesizer": status != "ok"}
    return chunk_id, last_delta


def chunk_synthesizer_node(state: VideoSummaryState) -> dict:
    """
    分片融合节点。

    地位:
    - 位于音频分支和视觉分支之后，是分片级多模态汇合点。
    - 输出的 chunk_summary 会被后续 aggregator 作为更高层的证据摘要使用。

    任务:
    - 仅负责按 chunk_plan 顺序重建和结果透传，避免重复计算。

    主要输入:
    - state["chunk_plan"]
    - state["chunk_results"]
    - state["user_prompt"]

    主要输出:
    - chunk_results: 为每个 chunk 补充 chunk_summary 和 synthesizer 延迟信息。
    """
    # 分片融合由 chunk_synthesizer_worker_node 完成。
    # 这里仅做顺序重建与透传，避免重复计算。
    chunk_plan = state.get("chunk_plan", [])
    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_plan, list) or not isinstance(chunk_results, list):
        return {"chunk_results": chunk_results if isinstance(chunk_results, list) else []}

    result_map: Dict[str, Dict[str, Any]] = {
        str(item.get("chunk_id", "")).strip(): dict(item)
        for item in chunk_results
        if isinstance(item, dict) and str(item.get("chunk_id", "")).strip()
    }

    ordered_results: List[Dict[str, Any]] = []
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if chunk_id and chunk_id in result_map:
            ordered_results.append(result_map[chunk_id])

    return {"chunk_results": ordered_results}


def chunk_synthesizer_worker_node(state: VideoSummaryState) -> dict:
    """
    Send API 路径下的单分片融合 worker。

    地位:
    - 是 chunk_synthesizer_node 的单分片版本，用于图级 fan-out。

    任务:
    - 读取单个 chunk 已完成的音频与视觉洞察。
    - 生成当前 chunk 的 chunk_summary。

    主要输入:
    - state["current_synthesis_chunk"]
    - state["current_synthesis_base_item"]
    - state["user_prompt"]

    主要输出:
    - chunk_results: 长度为 1 的列表，包含当前 chunk 的融合结果。
    """
    current_chunk = state.get("current_synthesis_chunk", {})
    if not isinstance(current_chunk, dict):
        return {"chunk_results": []}

    chunk_id = str(current_chunk.get("chunk_id", "")).strip()
    if not chunk_id:
        return {"chunk_results": []}

    user_prompt = str(state.get("user_prompt", ""))
    base_item = state.get("current_synthesis_base_item", {"chunk_id": chunk_id})
    structured_global_context = str(state.get("structured_global_context",""))
    if not isinstance(base_item, dict):
        base_item = {"chunk_id": chunk_id}

    _, merged = _run_synthesis_with_retry(
        chunk_id,
        user_prompt,
        structured_global_context,
        base_item,
    )
    return {"chunk_results": [merged]}
