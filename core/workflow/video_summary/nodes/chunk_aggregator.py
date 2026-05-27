from typing import Any, Dict, List

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


def chunk_aggregator_node(state: VideoSummaryState) -> dict:
    """
    基于大纲的降维物理分拣聚合节点。
    任务:
    - 遍历 structured_global_context 中的 narrative_arc。
    - 将所有 chunk_results 中的 verified_insights 按时间戳归入对应的章节。
    - 形成结构化的 Markdown 草稿。
    """
    chunk_results = state.get("chunk_results", [])
    user_prompt = _safe_str(state.get("user_prompt", ""))
    structured_global_context = state.get("structured_global_context", {})
    
    if not isinstance(chunk_results, list):
        chunk_results = []
    if not isinstance(structured_global_context, dict):
        structured_global_context = {}

    narrative_arc = structured_global_context.get("narrative_arc", [])
    if not isinstance(narrative_arc, list) or not narrative_arc:
        narrative_arc = [{"chapter_title": "全局洞察(未提取大纲)", "start_sec": 0, "end_sec": 999999}]

    # 收集所有事实
    all_insights = []
    for item in chunk_results:
        if not isinstance(item, dict):
            continue
        insights = item.get("verified_insights", [])
        if isinstance(insights, list):
            all_insights.extend(insights)

    lines: List[str] = []
    lines.append("# Chunk Aggregated Insights")
    lines.append("")
    if user_prompt:
        lines.append(f"- user_focus: {user_prompt}")
    lines.append("")

    dropped_count = 0
    total_insights_processed = len(all_insights)

    # 物理分拣
    for chapter in narrative_arc:
        if not isinstance(chapter, dict):
            continue
        title = _safe_str(chapter.get("chapter_title", "未命名章节"))
        start_sec = int(chapter.get("start_sec", 0))
        end_sec = int(chapter.get("end_sec", 999999))
        
        lines.append(f"## {title} [{_to_hhmmss(start_sec)} - {_to_hhmmss(end_sec)}]")
        
        chapter_has_insight = False
        for insight in all_insights:
            if not isinstance(insight, dict):
                continue
            time_sec = int(insight.get("time_sec", 0))
            if start_sec <= time_sec < end_sec:
                chapter_has_insight = True
                timestamp_str = _safe_str(insight.get("timestamp", ""))
                claim = _safe_str(insight.get("claim", ""))
                exact_quote = _safe_str(insight.get("exact_quote", ""))
                frame_ref = _safe_str(insight.get("frame_ref", ""))
                
                bullet = f"- [{timestamp_str}] {claim}"
                if exact_quote:
                    bullet += f" (原话: \"{exact_quote}\")"
                if frame_ref:
                    bullet += f" [视觉证据: {frame_ref}]"
                lines.append(bullet)
        
        if not chapter_has_insight:
            lines.append("- (本章节无明显细节抽出)")
        lines.append("")

    aggregated = _truncate("\n".join(lines).strip(), AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS)
    return {
        "aggregated_chunk_insights": aggregated,
        "reduce_debug_info": {
            **(state.get("reduce_debug_info", {}) if isinstance(state.get("reduce_debug_info", {}), dict) else {}),
            "aggregator_chapters": len(narrative_arc),
            "aggregator_total_insights": total_insights_processed,
        },
    }
