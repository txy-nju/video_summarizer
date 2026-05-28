from typing import Any, Dict, List, Tuple

from config.settings import AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS
from core.workflow.video_summary.state import VideoSummaryState


def _safe_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _to_hhmmss(total_seconds: int) -> str:
    safe = max(0, int(total_seconds))
    hours = safe // 3600
    minutes = (safe % 3600) // 60
    seconds = safe % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _truncate(text: str, limit: int) -> str:
    safe_limit = max(2000, int(limit))
    if len(text) <= safe_limit:
        return text
    suffix = "\n\n[系统提示] 聚合内容超过上限，已自动截断以保护后续节点上下文窗口。"
    return text[: safe_limit - len(suffix)] + suffix


def _safe_sec(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _format_claims_with_frames(
    transcript_claims: List[Dict],
    frame_references: List[Dict],
) -> List[str]:
    """将 transcript_claims + frame_references 格式化为带缩进的 Markdown 列表。"""
    lines: List[str] = []
    for claim_item in transcript_claims:
        if not isinstance(claim_item, dict):
            continue
        claim = _safe_str(claim_item.get("claim", ""))
        exact_quote = _safe_str(claim_item.get("exact_quote", ""))
        timestamp = _safe_str(claim_item.get("timestamp", ""))
        if not claim:
            continue
        prefix = f"[{timestamp}] " if timestamp else ""
        quote_part = f' ("{exact_quote}")' if exact_quote else ""
        lines.append(f"- {prefix}{claim}{quote_part}")
    for frame_ref in frame_references:
        if not isinstance(frame_ref, dict):
            continue
        frame_time = _safe_str(frame_ref.get("frame_time", ""))
        observation = _safe_str(frame_ref.get("observation", ""))
        audio_claim_match = _safe_str(frame_ref.get("audio_claim_match", ""))
        if not observation:
            continue
        match_tag = f" [{audio_claim_match}]" if audio_claim_match else ""
        lines.append(f"  - 🖼 {frame_time}: {observation}{match_tag}")
    return lines


def _build_primary_path(
    ordered_ids: List[str],
    result_map: Dict[str, Dict],
    plan_map: Dict[str, Dict],
    narrative_arc: List[Dict],
    user_prompt: str,
) -> Tuple[str, int]:
    """主路径：按 narrative_arc 章节归组，输出 transcript_claims + frame_references 交叉验证证据。"""
    lines: List[str] = []
    lines.append("# Chunk Aggregated Insights")
    lines.append("")
    if user_prompt:
        lines.append(f"- user_focus: {user_prompt}")
    lines.append(f"- total_chunks: {len(ordered_ids)}")
    lines.append("")

    chapter_chunk_map: Dict[str, List[str]] = {}
    for chapter in narrative_arc:
        if not isinstance(chapter, dict):
            continue
        cid = _safe_str(chapter.get("chapter_id", ""))
        if cid:
            chapter_chunk_map[cid] = []

    unassigned: List[str] = []
    for chunk_id in ordered_ids:
        plan_item = plan_map.get(chunk_id, {})
        p_start = _safe_sec(plan_item.get("start_sec", 0))
        p_end = _safe_sec(plan_item.get("end_sec", 0))
        mid = (p_start + p_end) / 2.0
        assigned = False
        for chapter in narrative_arc:
            if not isinstance(chapter, dict):
                continue
            cid = _safe_str(chapter.get("chapter_id", ""))
            c_start = _safe_sec(chapter.get("start_sec", 0))
            c_end = _safe_sec(chapter.get("end_sec", 0))
            if cid and c_start <= mid <= c_end:
                chapter_chunk_map[cid].append(chunk_id)
                assigned = True
                break
        if not assigned:
            unassigned.append(chunk_id)

    dropped_count = 0

    for chapter in narrative_arc:
        if not isinstance(chapter, dict):
            continue
        cid = _safe_str(chapter.get("chapter_id", ""))
        if not cid:
            continue
        title = _safe_str(chapter.get("title", cid))
        c_start = _safe_sec(chapter.get("start_sec", 0))
        c_end = _safe_sec(chapter.get("end_sec", 0))
        time_span = f"[{_to_hhmmss(c_start)} - {_to_hhmmss(c_end)}]"
        chunk_ids_in_chapter = chapter_chunk_map.get(cid, [])

        lines.append(f"# {title} {time_span}")
        lines.append("")

        if not chunk_ids_in_chapter:
            lines.append("_（本章节无已处理分片）_")
            lines.append("")
            continue

        for chunk_id in chunk_ids_in_chapter:
            item = result_map.get(chunk_id, {})
            plan_item = plan_map.get(chunk_id, {})
            p_start = _safe_sec(plan_item.get("start_sec", 0))
            p_end = _safe_sec(plan_item.get("end_sec", 0))
            time_span_chunk = f"[{_to_hhmmss(p_start)} - {_to_hhmmss(p_end)}]"

            transcript_claims = item.get("transcript_claims", [])
            frame_references = item.get("frame_references", [])
            if not isinstance(transcript_claims, list):
                transcript_claims = []
            if not isinstance(frame_references, list):
                frame_references = []

            if not transcript_claims and not frame_references:
                dropped_count += 1
                continue

            lines.append(f"## {chunk_id} {time_span_chunk}")
            lines.extend(_format_claims_with_frames(transcript_claims, frame_references))
            lines.append("")

    if unassigned:
        lines.append("# 未归章节分片")
        lines.append("")
        for chunk_id in unassigned:
            item = result_map.get(chunk_id, {})
            plan_item = plan_map.get(chunk_id, {})
            p_start = _safe_sec(plan_item.get("start_sec", 0))
            p_end = _safe_sec(plan_item.get("end_sec", 0))
            time_span_chunk = f"[{_to_hhmmss(p_start)} - {_to_hhmmss(p_end)}]"

            transcript_claims = item.get("transcript_claims", [])
            frame_references = item.get("frame_references", [])
            if not isinstance(transcript_claims, list):
                transcript_claims = []
            if not isinstance(frame_references, list):
                frame_references = []

            if not transcript_claims and not frame_references:
                dropped_count += 1
                continue

            lines.append(f"## {chunk_id} {time_span_chunk}")
            lines.extend(_format_claims_with_frames(transcript_claims, frame_references))
            lines.append("")

    return "\n".join(lines).strip(), dropped_count


def _build_fallback_path(
    ordered_ids: List[str],
    result_map: Dict[str, Dict],
    plan_map: Dict[str, Dict],
    user_prompt: str,
) -> Tuple[str, int]:
    """降级路径：narrative_arc 为空时，平铺各分片的 chunk_summary。"""
    lines: List[str] = []
    lines.append("# Chunk Aggregated Insights")
    lines.append("")
    lines.append(f"- total_chunks: {len(ordered_ids)}")
    if user_prompt:
        lines.append(f"- user_focus: {user_prompt}")
    lines.append("- mode: fallback (no narrative_arc)")
    lines.append("")

    dropped_count = 0
    for chunk_id in ordered_ids:
        item = result_map.get(chunk_id, {})
        plan_item = plan_map.get(chunk_id, {})
        p_start = _safe_sec(plan_item.get("start_sec", 0))
        p_end = _safe_sec(plan_item.get("end_sec", 0))
        time_span = f"[{_to_hhmmss(p_start)} - {_to_hhmmss(p_end)}]"

        chunk_summary = _safe_str(item.get("chunk_summary", ""))
        if not chunk_summary:
            dropped_count += 1
            continue

        lines.append(f"## {chunk_id} {time_span}")
        lines.append(chunk_summary)
        lines.append("")

    return "\n".join(lines).strip(), dropped_count


def chunk_aggregator_node(state: VideoSummaryState) -> dict:
    """
    分片聚合节点。

    主路径（narrative_arc 非空）：
      按章节归组，输出 transcript_claims + frame_references 交叉验证证据。
      chunk_summary 不参与主路径，仅作降级兜底，避免两份叙事并存。

    降级路径（narrative_arc 为空）：
      平铺 chunk_summary，保持鲁棒性。

    主要输入:
    - state["chunk_plan"]
    - state["chunk_results"]   每条含 transcript_claims、frame_references、chunk_summary
    - state["narrative_arc"]   全局叙事章节（主路径依赖）
    - state["user_prompt"]

    主要输出:
    - aggregated_chunk_insights: 供 fusion_drafter_node 消费的全局证据底稿。
    - reduce_debug_info: 聚合阶段统计信息（含 aggregator_mode）。
    """
    chunk_results = state.get("chunk_results", [])
    chunk_plan = state.get("chunk_plan", [])
    user_prompt = _safe_str(state.get("user_prompt", ""))
    narrative_arc = state.get("narrative_arc") or []
    if not isinstance(narrative_arc, list):
        narrative_arc = []

    if not isinstance(chunk_results, list):
        chunk_results = []
    if not isinstance(chunk_plan, list):
        chunk_plan = []

    result_map: Dict[str, Dict[str, Any]] = {}
    for item in chunk_results:
        if not isinstance(item, dict):
            continue
        chunk_id = _safe_str(item.get("chunk_id", ""))
        if not chunk_id:
            continue
        result_map[chunk_id] = item

    plan_map: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []
    for plan_item in chunk_plan:
        if not isinstance(plan_item, dict):
            continue
        chunk_id = _safe_str(plan_item.get("chunk_id", ""))
        if not chunk_id:
            continue
        plan_map[chunk_id] = plan_item
        ordered_ids.append(chunk_id)

    for chunk_id in sorted(result_map.keys()):
        if chunk_id not in plan_map:
            ordered_ids.append(chunk_id)

    if narrative_arc:
        aggregated, dropped_count = _build_primary_path(
            ordered_ids, result_map, plan_map, narrative_arc, user_prompt
        )
        mode = "primary"
    else:
        aggregated, dropped_count = _build_fallback_path(
            ordered_ids, result_map, plan_map, user_prompt
        )
        mode = "fallback"

    aggregated = _truncate(aggregated, AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS)
    return {
        "aggregated_chunk_insights": aggregated,
        "reduce_debug_info": {
            **(state.get("reduce_debug_info", {}) if isinstance(state.get("reduce_debug_info", {}), dict) else {}),
            "aggregator_total_chunks": len(ordered_ids),
            "aggregator_dropped_chunks": dropped_count,
            "aggregator_mode": mode,
        },
    }
