import json
from typing import Any, Dict, List
from langgraph.types import Send

from config.settings import CONTEXT_MEMORY_SUMMARY_MAX_CHARS, CONTEXT_MEMORY_WINDOW_SIZE, WAVE_DISPATCH_SIZE
from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.nodes.chunk_state import ChunkState


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


def _is_chunk_synthesized(item: Dict[str, Any]) -> bool:
    if bool(str(item.get("chunk_summary", "")).strip()):
        return True

    modality_status = item.get("modality_status", {})
    if isinstance(modality_status, dict):
        # vision worker 只要运行过（任意 status），流水线即视为完成
        # 防止 LLM 返回合法 JSON 但 chunk_summary 为空时造成无限 wave 循环
        if str(modality_status.get("vision", "")).strip():
            return True
    return False


def _modality_ready(item: Dict[str, Any], modality: str) -> bool:
    """检查指定模态是否已完成（含降级终结态）。
    """
    if modality == "multimodal":
        insights = item.get("chunk_insights_md")
        if isinstance(insights, str) and insights.strip():
            return True

    modality_status = item.get("modality_status", {})
    if isinstance(modality_status, dict):
        status = str(modality_status.get(modality, "")).strip().lower()
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
        summary = _compress_summary(str(item.get("chunk_summary", "")))
        if summary:
            memory[chunk_id] = summary
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


def _parse_transcript_segments(transcript_str: str) -> List[Dict[str, Any]]:
    """从 transcript JSON 字符串中提取 segments 列表。"""
    try:
        data = json.loads(transcript_str or "{}")
        segs = data.get("segments", [])
        if isinstance(segs, list):
            return [s for s in segs if isinstance(s, dict)]
    except Exception:
        pass
    return []


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
    pending_chunk_ids = [chunk_id for chunk_id in chunk_ids if not _is_chunk_synthesized(result_map.get(chunk_id, {}))]

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


def route_chunk_multimodal_tasks(state: VideoSummaryState) -> List[Send]:
    """
    为当前波次的每个待处理分片生成 Send API 派发任务（多模态版）。

    每个分片以 ChunkState-shaped dict 发送给 chunk_multimodal_worker_node。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        return []

    active_wave_chunk_ids = state.get("active_wave_chunk_ids", [])
    if not isinstance(active_wave_chunk_ids, list):
        active_wave_chunk_ids = []
    active_wave_set = {str(item).strip() for item in active_wave_chunk_ids if str(item).strip()}

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []
    result_map = _build_result_map(chunk_results)

    # 预解析 transcript segments（一次性）
    all_segments = _parse_transcript_segments(str(state.get("transcript", "")))
    keyframes = state.get("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
    keyframes_base_path = str(state.get("keyframes_base_path", ""))
    user_prompt = str(state.get("user_prompt", ""))
    trace_id = str(state.get("trace_id", ""))
    narrative_arc = state.get("narrative_arc") or []
    if not isinstance(narrative_arc, list):
        narrative_arc = []
    previous_chunk_summaries_by_chunk = state.get("previous_chunk_summaries_by_chunk", {})
    if not isinstance(previous_chunk_summaries_by_chunk, dict):
        previous_chunk_summaries_by_chunk = {}

    sends: List[Send] = []
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        if active_wave_set and chunk_id not in active_wave_set:
            continue
        # 幂等：已完成（chunk_summary 非空或终结降级态）的分片跳过
        if _is_chunk_synthesized(result_map.get(chunk_id, {})):
            continue

        seg_indexes = chunk.get("transcript_segment_indexes", [])
        chunk_segments = [
            all_segments[i]
            for i in (seg_indexes if isinstance(seg_indexes, list) else [])
            if isinstance(i, int) and 0 <= i < len(all_segments)
        ]
        keyframe_indexes = chunk.get("keyframe_indexes", [])
        if not isinstance(keyframe_indexes, list):
            keyframe_indexes = []

        chunk_state_payload: ChunkState = {
            "chunk_id": chunk_id,
            "transcript_segments": chunk_segments,
            "keyframe_indexes": keyframe_indexes,
            "keyframes": keyframes,
            "keyframes_base_path": keyframes_base_path,
            "narrative_arc": narrative_arc,
            "previous_chunk_summaries": previous_chunk_summaries_by_chunk.get(chunk_id, []),
            "user_prompt": user_prompt,
            "trace_id": trace_id,
            "chunk_insights_md": "",
            "chunk_summary": "",
            "modality_status": {},
            "latency_ms": {},
        }
        sends.append(Send("chunk_multimodal_worker_node", chunk_state_payload))

    return sends


def wave_gate_node(state: VideoSummaryState) -> Dict[str, Any]:
    """
    波次收敛节点（fan-in）。

    地位:
    - 位于 chunk_subgraph_node (fan-out) 之后，是波次完成后的汇聚点。
    - 负责结果排序（原 chunk_synthesizer_node 职责）和波次等待（原 synthesis_barrier_node 职责）已统一在此处理。

    任务:
    - 按 chunk_plan 顺序重建 chunk_results（保证输出顺序稳定）。
    - 触发 route_after_wave_synthesis 路由判断（下一波 or 聚合）。

    主要输入:
    - state["chunk_plan"]
    - state["chunk_results"]

    主要输出:
    - chunk_results: 按 chunk_plan 顺序排列的结果列表。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        chunk_plan = []

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []

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

    reduce_debug_info = state.get("reduce_debug_info", {})
    if not isinstance(reduce_debug_info, dict):
        reduce_debug_info = {}
    reduce_debug_info["wave_gate_reached"] = True
    reduce_debug_info["wave_gate_ordered_count"] = len(ordered_results)

    return {
        "chunk_results": ordered_results,
        "reduce_debug_info": reduce_debug_info,
    }


def route_after_wave_synthesis(state: VideoSummaryState) -> str:
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
        if not _is_chunk_synthesized(result_map.get(chunk_id, {})):
            return ROUTE_CONTINUE_WAVE

    return ROUTE_WAVE_DONE
