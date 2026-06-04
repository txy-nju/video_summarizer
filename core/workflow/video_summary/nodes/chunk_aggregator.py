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


import re

EVENT_PATTERN = re.compile(r"^\s*[-*]\s*\[?\s*(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?))?\s*\]?:?\s*")

def _parse_time(t_str: str) -> int:
    parts = t_str.strip().split(':')
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        pass
    return 0

def _build_primary_path(
    ordered_ids: List[str],
    result_map: Dict[str, Dict],
    plan_map: Dict[str, Dict],
    narrative_arc: List[Dict],
    user_prompt: str,
) -> Tuple[str, int]:
    """主路径：按 narrative_arc 章节归组，输出分片多模态分析 markdown 结构。
    
    解析 markdown 中的时间戳，将不同时间的观察块精确分配到对应的章节中。
    """
    lines: List[str] = []

    chapter_chunk_map: Dict[str, List[Tuple[str, str]]] = {}
    for chapter in narrative_arc:
        if not isinstance(chapter, dict):
            continue
        cid = _safe_str(chapter.get("chapter_id", ""))
        if cid:
            chapter_chunk_map[cid] = []

    unassigned: List[Tuple[str, str]] = []
    dropped_count = 0

    for chunk_id in ordered_ids:
        plan_item = plan_map.get(chunk_id, {})
        item = result_map.get(chunk_id, {})
        p_start = _safe_sec(plan_item.get("start_sec", 0))
        p_end = _safe_sec(plan_item.get("end_sec", 0))
        chunk_mid = (p_start + p_end) / 2.0
        
        insights_md = _safe_str(item.get("chunk_insights_md", ""))
        if not insights_md:
            dropped_count += 1
            continue
            
        current_block_lines: List[str] = []
        current_mid = chunk_mid
        
        def flush_block():
            if not current_block_lines:
                return
            block_text = "\n".join(current_block_lines)
            
            assigned = False
            for chapter in narrative_arc:
                if not isinstance(chapter, dict):
                    continue
                cid = _safe_str(chapter.get("chapter_id", ""))
                c_start = _safe_sec(chapter.get("start_sec", 0))
                c_end = _safe_sec(chapter.get("end_sec", 0))
                if cid and c_start <= current_mid <= c_end:
                    chapter_chunk_map[cid].append((chunk_id, block_text))
                    assigned = True
                    break
            
            if not assigned:
                unassigned.append((chunk_id, block_text))
            
            current_block_lines.clear()

        for line in insights_md.split('\n'):
            match = EVENT_PATTERN.match(line)
            if match:
                flush_block()
                start_str = match.group(1)
                end_str = match.group(2)
                start_sec = _parse_time(start_str)
                end_sec = _parse_time(end_str) if end_str else start_sec
                current_mid = (start_sec + end_sec) / 2.0
                
            current_block_lines.append(line)
            
        flush_block()

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
        blocks = chapter_chunk_map.get(cid, [])

        lines.append(f"# {title} {time_span}")
        lines.append("")

        if not blocks:
            lines.append("_（本章节无已处理分片）_")
            lines.append("")
            continue

        grouped_blocks: Dict[str, List[str]] = {}
        for chunk_id, block_text in blocks:
            grouped_blocks.setdefault(chunk_id, []).append(block_text)

        for chunk_id, block_texts in grouped_blocks.items():
            plan_item = plan_map.get(chunk_id, {})
            p_start = _safe_sec(plan_item.get("start_sec", 0))
            p_end = _safe_sec(plan_item.get("end_sec", 0))
            
            for b in block_texts:
                lines.append(b)
            lines.append("")

    if unassigned:
        lines.append("# 未归章节分片")
        lines.append("")
        
        grouped_blocks: Dict[str, List[str]] = {}
        for chunk_id, block_text in unassigned:
            grouped_blocks.setdefault(chunk_id, []).append(block_text)
            
        for chunk_id, block_texts in grouped_blocks.items():
            plan_item = plan_map.get(chunk_id, {})
            p_start = _safe_sec(plan_item.get("start_sec", 0))
            p_end = _safe_sec(plan_item.get("end_sec", 0))
            
            for b in block_texts:
                lines.append(b)
            lines.append("")

    return "\n".join(lines).strip(), dropped_count


def _build_fallback_path(
    ordered_ids: List[str],
    result_map: Dict[str, Dict],
    plan_map: Dict[str, Dict],
    user_prompt: str,
) -> Tuple[str, int]:
    """降级路径：narrative_arc 为空时，平铺各分片的 chunk_insights_md。"""
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

        insights_md = _safe_str(item.get("chunk_insights_md", ""))
        if not insights_md:
            dropped_count += 1
            continue

        lines.append(f"## {chunk_id} {time_span}")
        lines.append(insights_md)
        lines.append("")

    return "\n".join(lines).strip(), dropped_count


def chunk_aggregator_node(state: VideoSummaryState) -> dict:
    """
    分片聚合节点。

    主路径（narrative_arc 非空）：
      按章节归组，输出 multimodal 分析证据（chunk_insights_md）。
      chunk_summary 仅作滑动窗口上下文，不在最终 Markdown 报告中展示。

    降级路径（narrative_arc 为空）：
      平铺 chunk_insights_md，保持鲁棒性。

    主要输入:
    - state["chunk_plan"]
    - state["chunk_results"]   每条含 chunk_insights_md、chunk_summary
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
