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


def _is_chunk_synthesized(item: Dict[str, Any]) -> bool:
    if bool(str(item.get("chunk_summary", "")).strip()):
        return True

    modality_status = item.get("modality_status", {})
    if isinstance(modality_status, dict):
        status = str(modality_status.get("synthesizer", "")).strip().lower()
        if status in {"timeout", "failed", "degraded"}:
            return True
    return False


def _modality_ready(item: Dict[str, Any], modality: str) -> bool:
    insights_key = f"{modality}_insights"
    if bool(str(item.get(insights_key, "")).strip()):
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


def synthesis_barrier_node(state: VideoSummaryState) -> Dict[str, Any]:
    """
    Send API 路径下的中间汇聚节点。

    地位:
    - 位于 audio/vision worker 之后，synthesis worker 之前。
    - 负责把并行分析阶段与并行融合阶段分隔开，形成显式 barrier。

    任务:
    - 检查每个 chunk 是否同时具备 audio_insights 和 vision_insights。
    - 记录 synthesis_ready 等门控状态。
    - 不做业务推理，只做条件汇聚与状态透传。

    主要输入:
    - state["chunk_plan"]
    - state["chunk_results"]

    主要输出:
    - reduce_debug_info: 汇聚完成度与是否可进入 synthesis fan-out。
    - chunk_results: 原样透传。
    """
    reduce_debug_info = state.get("reduce_debug_info", {})
    if not isinstance(reduce_debug_info, dict):
        reduce_debug_info = {}

    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        chunk_plan = []
    wave_chunk_ids = state.get("active_wave_chunk_ids", [])
    if not isinstance(wave_chunk_ids, list):
        wave_chunk_ids = []
    if not wave_chunk_ids:
        wave_chunk_ids = _chunk_ids_from_plan(chunk_plan)
    total_chunks = len(wave_chunk_ids)

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        chunk_results = []

    result_map = _build_result_map(chunk_results)
    ready_chunk_ids = {
        chunk_id
        for chunk_id in wave_chunk_ids
        if _modality_ready(result_map.get(chunk_id, {}), "audio") and _modality_ready(result_map.get(chunk_id, {}), "vision")
    }

    reduce_debug_info.update(
        {
            "synthesis_barrier_reached": True,
            "synthesis_ready_chunks": len(ready_chunk_ids),
            "synthesis_total_chunks": total_chunks,
            "synthesis_ready": total_chunks > 0 and len(ready_chunk_ids) == total_chunks,
            "synthesis_wave_chunk_ids": wave_chunk_ids,
        }
    )

    return {
        "chunk_results": chunk_results,
        "reduce_debug_info": reduce_debug_info,
    }


def route_audio_send_tasks(state: VideoSummaryState) -> List[Send]:
    """
    为音频分析阶段生成 Send API 派发任务。
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
        # 避免重复派发：该分片音频模态已 ready（含终结降级）则跳过。
        if _modality_ready(result_map.get(chunk_id, {}), "audio"):
            continue

        sends.append(
            Send(
                "chunk_audio_worker_node",
                {
                    "transcript": transcript,
                    "user_prompt": user_prompt,
                    "structured_global_context": structured_global_context,
                    "previous_chunk_summaries": previous_chunk_summaries_by_chunk.get(chunk_id, []),
                    "current_chunk": chunk,
                },
            )
        )

    return sends


def route_vision_send_tasks(state: VideoSummaryState) -> List[Send]:
    """
    为视觉分析阶段生成 Send API 派发任务。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        return []

    active_wave_chunk_ids = state.get("active_wave_chunk_ids", [])
    if not isinstance(active_wave_chunk_ids, list):
        active_wave_chunk_ids = []
    active_wave_set = {str(item).strip() for item in active_wave_chunk_ids if str(item).strip()}

    sends: List[Send] = []
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
        # 避免重复派发：该分片视觉模态已 ready（含终结降级）则跳过。
        if _modality_ready(result_map.get(chunk_id, {}), "vision"):
            continue

        sends.append(
            Send(
                "chunk_vision_worker_node",
                {
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


def route_synthesis_send_tasks(state: VideoSummaryState) -> List[Send]:
    """
    为分片融合阶段生成 Send API 派发任务。

    仅在所有 chunk 已同时具备音频和视觉洞察时触发。
    """
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list) or not chunk_plan:
        return []

    active_wave_chunk_ids = state.get("active_wave_chunk_ids", [])
    if not isinstance(active_wave_chunk_ids, list):
        active_wave_chunk_ids = []
    active_wave_set = {str(item).strip() for item in active_wave_chunk_ids if str(item).strip()}

    planned_chunk_ids: List[str] = []
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if chunk_id:
            if active_wave_set and chunk_id not in active_wave_set:
                continue
            planned_chunk_ids.append(chunk_id)

    if not planned_chunk_ids:
        return []

    chunk_results = state.get("chunk_results", [])
    if not isinstance(chunk_results, list):
        return []

    result_map: Dict[str, Dict[str, Any]] = _build_result_map(chunk_results)

    # 触发时机保底：必须等待 audio_worker 和 vision_worker 整理完全部 chunk。
    all_ready = True
    for chunk_id in planned_chunk_ids:
        item = result_map.get(chunk_id, {})
        has_audio = _modality_ready(item, "audio")
        has_vision = _modality_ready(item, "vision")
        if not (has_audio and has_vision):
            all_ready = False
            break

    if not all_ready:
        return []

    sends: List[Send] = []
    user_prompt = str(state.get("user_prompt", ""))
    structured_global_context = str(state.get("structured_global_context", ""))
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        if active_wave_set and chunk_id not in active_wave_set:
            continue

        base_item = result_map.get(chunk_id, {"chunk_id": chunk_id})
        # 幂等：已生成 chunk_summary 的分片不重复派发
        if str(base_item.get("chunk_summary", "")).strip():
            continue
        if not _modality_ready(base_item, "audio"):
            continue
        if not _modality_ready(base_item, "vision"):
            continue

        sends.append(
            Send(
                "chunk_synthesizer_worker_node",
                {
                    "user_prompt": user_prompt,
                    "structured_global_context":structured_global_context,
                    "current_synthesis_chunk": chunk,
                    "current_synthesis_base_item": base_item,
                },
            )
        )

    return sends


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
