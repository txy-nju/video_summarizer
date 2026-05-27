from typing import Any, Dict, List
from langgraph.types import Send

from config.settings import CONTEXT_MEMORY_SUMMARY_MAX_CHARS, CONTEXT_MEMORY_WINDOW_SIZE, WAVE_DISPATCH_SIZE
from core.workflow.video_summary.state import VideoSummaryState


ROUTE_CONTINUE_WAVE = "continue_wave"
ROUTE_WAVE_DONE = "wave_done"


def _chunk_ids_from_plan(chunk_plan: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if chunk_id:
            ids.append(chunk_id)
    return ids


def _build_result_map(chunk_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("chunk_id", "")).strip(): dict(item)
        for item in chunk_results
        if isinstance(item, dict) and str(item.get("chunk_id", "")).strip()
    }


def _is_chunk_completed(item: Dict[str, Any]) -> bool:
    if "verified_insights" in item:
        return True

    modality_status = item.get("modality_status", {})
    if isinstance(modality_status, dict):
        status = str(modality_status.get("vision", "")).strip().lower()
        if status in {"timeout", "failed", "degraded"}:
            return True
    return False


def _compress_summary(summary: str) -> str:
    normalized = " ".join(str(summary or "").split())
    if len(normalized) <= CONTEXT_MEMORY_SUMMARY_MAX_CHARS:
        return normalized
    return normalized[: CONTEXT_MEMORY_SUMMARY_MAX_CHARS].rstrip()


def _build_chunk_summary_memory(chunk_ids: List[str], result_map: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    memory: Dict[str, str] = {}
    for chunk_id in chunk_ids:
        item = result_map.get(chunk_id, {})
        insights = item.get("verified_insights", [])
        if insights:
            summary_parts = []
            for insight in insights:
                summary_parts.append(f"[{insight.get('timestamp', '')}] {insight.get('claim', '')}")
            memory[chunk_id] = _compress_summary("\n".join(summary_parts))
    return memory


def _build_previous_chunk_summaries_by_chunk(
    chunk_ids: List[str],
    active_wave_chunk_ids: List[str],
    chunk_summary_memory: Dict[str, str],
) -> Dict[str, List[Dict[str, Any]]]:
    previous_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
    id_to_index = {chunk_id: idx for idx, chunk_id in enumerate(chunk_ids)}
    window_size = max(1, CONTEXT_MEMORY_WINDOW_SIZE)

    for chunk_id in active_wave_chunk_ids:
        if chunk_id not in id_to_index:
            previous_by_chunk[chunk_id] = []
            continue

        current_index = id_to_index[chunk_id]
        start = max(0, current_index - window_size)
        previous_items: List[Dict[str, Any]] = []
        for prev_index in range(start, current_index):
            prev_chunk_id = chunk_ids[prev_index]
            summary = chunk_summary_memory.get(prev_chunk_id, "")
            if not summary:
                continue
            previous_items.append({"chunk_id": prev_chunk_id, "summary": summary})

        previous_by_chunk[chunk_id] = previous_items

    return previous_by_chunk


def map_dispatch_node(state: VideoSummaryState) -> Dict[str, Any]:
    """
    分发准备节点。

    地位:
    - 位于 chunk_planner_node 之后，是并行执行前的轻量级准备层。
    - 不直接产出业务洞察，而是补齐调度和观测所需的元信息。

    任务:
    - 初始化 chunk_retry_count。
    - 标记 dispatch_strategy、chunk_count 等调试字段。
    - 透传已有的 chunk_results，供后续节点继续累积结果。

    主要输入:
    - state["chunk_plan"]: 上游规划出的分片计划。
    - state["chunk_results"]: 已存在的分片结果（恢复会话或重入场景）。

    主要输出:
    - chunk_retry_count: 每个 chunk 的重试计数基座。
    - reduce_debug_info: 分发策略和规模信息。
    - chunk_results: 原样透传。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        chunk_plan = []

    retry_count = state.get("chunk_retry_count", {})
    if not isinstance(retry_count, dict):
        retry_count = {}

    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        retry_count.setdefault(chunk_id, 0)

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []

    chunk_ids = _chunk_ids_from_plan(chunk_plan)
    result_map = _build_result_map(chunk_results)
    pending_chunk_ids = [chunk_id for chunk_id in chunk_ids if not _is_chunk_completed(result_map.get(chunk_id, {}))]

    active_wave_chunk_ids = pending_chunk_ids[: max(1, WAVE_DISPATCH_SIZE)]
    chunk_summary_memory = _build_chunk_summary_memory(chunk_ids, result_map)
    previous_chunk_summaries_by_chunk = _build_previous_chunk_summaries_by_chunk(
        chunk_ids,
        active_wave_chunk_ids,
        chunk_summary_memory,
    )
    completed_count = len(chunk_ids) - len(pending_chunk_ids)
    wave_index = completed_count // max(1, WAVE_DISPATCH_SIZE)

    reduce_debug_info = state.get("reduce_debug_info", {})
    if not isinstance(reduce_debug_info, dict):
        reduce_debug_info = {}

    reduce_debug_info.update(
        {
            "dispatch_ready": True,
            "chunk_count": len(chunk_plan),
            "dispatch_strategy": "send-api-wave-pilot",
            "wave_size": max(1, WAVE_DISPATCH_SIZE),
            "wave_index": wave_index,
            "wave_active_chunk_ids": active_wave_chunk_ids,
            "wave_pending_chunks": len(pending_chunk_ids),
            "context_memory_size": len(chunk_summary_memory),
        }
    )

    return {
        "chunk_results": chunk_results,
        "chunk_retry_count": retry_count,
        "chunk_summary_memory": chunk_summary_memory,
        "previous_chunk_summaries_by_chunk": previous_chunk_summaries_by_chunk,
        "active_wave_chunk_ids": active_wave_chunk_ids,
        "wave_index": wave_index,
        "reduce_debug_info": reduce_debug_info,
    }


def route_subgraph_send_tasks(state: VideoSummaryState) -> List[Send]:
    """
    为子图并发阶段生成 Send API 派发任务。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        return []

    active_wave_chunk_ids = state.get("active_wave_chunk_ids", [])
    if not isinstance(active_wave_chunk_ids, list):
        active_wave_chunk_ids = []
    active_wave_set = {str(item).strip() for item in active_wave_chunk_ids if str(item).strip()}

    sends: List[Send] = []
    transcript = state.get("transcript", "")
    keyframes = state.get("keyframes", [])
    keyframes_base_path = str(state.get("keyframes_base_path", ""))
    user_prompt = state.get("user_prompt", "")
    structured_global_context = state.get("structured_global_context", {})
    previous_chunk_summaries_by_chunk = state.get("previous_chunk_summaries_by_chunk", {})
    if not isinstance(previous_chunk_summaries_by_chunk, dict):
        previous_chunk_summaries_by_chunk = {}
    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []
    result_map = _build_result_map(chunk_results)
    
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        if active_wave_set and chunk_id not in active_wave_set:
            continue
        # 避免重复派发：该分片已 completed（含终结降级）则跳过。
        if _is_chunk_completed(result_map.get(chunk_id, {})):
            continue

        sends.append(
            Send(
                "chunk_subgraph",
                {
                    "transcript": transcript,
                    "keyframes": keyframes,
                    "keyframes_base_path": keyframes_base_path,
                    "user_prompt": user_prompt,
                    "structured_global_context": structured_global_context,
                    "previous_chunk_summaries": previous_chunk_summaries_by_chunk.get(chunk_id, []),
                    "current_chunk": chunk,
                },
            )
        )

    return sends


def route_after_wave_subgraph(state: VideoSummaryState) -> str:
    """
    波次执行后的路由：若仍有未完成 chunk，继续下一波；否则进入聚合。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list) or not chunk_plan:
        return ROUTE_WAVE_DONE

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []

    result_map = _build_result_map(chunk_results)
    for chunk_id in _chunk_ids_from_plan(chunk_plan):
        if not _is_chunk_completed(result_map.get(chunk_id, {})):
            return ROUTE_CONTINUE_WAVE

    return ROUTE_WAVE_DONE
