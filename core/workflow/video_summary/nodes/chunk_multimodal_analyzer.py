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
from core.workflow.video_summary.utils.frame_utils import resolve_frame_image_base64
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span

FramePayload = Dict[str, Any]

_MULTIMODAL_SYSTEM_PROMPT = (
    "你是严谨的视频分片多模态分析助手。\n\n"
    "【任务】综合当前分片的「台词文本（带时间戳）」和「关键帧画面」，"
    "输出该分片的结构化分析结果。\n\n"
    "【严格约束】：\n"
    "1. 请输出有效的 JSON 对象，包含两个字段：`chunk_summary` 和 `chunk_insights_md`。\n"
    "2. `chunk_summary`：纯文本摘要（约 100 字），用于上下文流转。\n"
    "3. `chunk_insights_md`：结构化 Markdown 文本，直接作为当前分片的分析结果（将被拼接到底稿中）。\n"
    "   - 在 Markdown 中列出关键事件或断言，并在列表后附带精确的 [时间戳范围] 和 🖼 画面证据说明。\n"
    "   - 无需复述 exact_quote，只需准确总结发生的事件和对应画面即可。\n"
    "   - 若画面与台词无关，标明 [画面不支持] 或 [仅音频]。\n"
    "4. 完整性：必须完整覆盖传入的整段文本，绝对不能提前截断或遗漏后半部分的内容！即便后半部分画面缺失，也必须根据音频文本提取事件。\n"
    "5. 🖼 画面证据聚焦原则（非常重要）：\n"
    "   - 画面分析必须聚焦于【知识强相关信息】：具体文字内容（标题、字幕、标注）、数据（数字、图表、表格数值）、文档/PPT/板书中的关键信息、代码片段、公式等。\n"
    "   - 禁止输出人物动作、表情、手势、穿着、场景布局、背景装饰、镜头运镜等与知识内容无关的视觉描述。\n"
    "   - 若画面中不存在可提取的文字/数据/图表等知识信息，直接标注 [仅音频] 或 [画面无知识信息]，不要描述画面中的人物或场景。\n"
    "   - 示例（正确）：🖼 05:10: PPT 标题「Q3 营收增长 23%」；图表显示华东区销售额 1.2 亿。\n"
    "   - 示例（错误）：🖼 05:10: 一位穿西装的男士站在讲台前，背景是蓝色投影屏幕。\n"
    "6. 输出格式（仅 JSON，无其它文字）：\n"
    '{\n  "chunk_summary": "...",\n  "chunk_insights_md": "- [05:10-05:40] 发言人展示 Q3 财报：营收增长 23%\\n  - 🖼 05:10: PPT 标题\\"Q3 营收同比增长 23%\\"，图表显示华东区销售额 1.2 亿"\n}'
)


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _build_fallback_output(chunk_id: str, reason: str) -> Dict[str, Any]:
    reason_str = reason if reason.strip() else f"{CHUNK_DEGRADED_MARKER}:multimodal:degraded:no_evidence"
    return {
        "chunk_summary": reason_str,
        "chunk_insights_md": f"- {reason_str}",
    }


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


def _parse_multimodal_output(raw_text: str) -> Dict[str, Any]:
    try:
        raw_text = raw_text.strip()
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return {}
        
        return {
            "chunk_summary": str(parsed.get("chunk_summary", "")).strip(),
            "chunk_insights_md": str(parsed.get("chunk_insights_md", "")).strip(),
        }
    except Exception:
        return {}


def _llm_multimodal_analyze(
    chunk_id: str,
    transcript_text: str,
    frames: List[FramePayload],
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    timeout_seconds: float,
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    if not resolve_api_key("vision"):
        return _build_fallback_output(chunk_id, f"{CHUNK_DEGRADED_MARKER}:multimodal:degraded:no_api_key")

    narrative_arc_json = json.dumps(narrative_arc or [], ensure_ascii=False)
    previous_summaries_json = json.dumps(previous_chunk_summaries or [], ensure_ascii=False)

    text_part = (
        f"[chunk_id] {chunk_id}\n"
        f"[user_prompt] {user_prompt}\n"
        f"[narrative_arc（全局章节大纲，仅用于语义定位）] {narrative_arc_json}\n"
        f"[previous_chunk_summaries（相邻分片摘要，仅用于消歧）] {previous_summaries_json}\n\n"
        f"[chunk_transcript]\n{transcript_text}\n\n"
        "请综合以上台词文本及以下关键帧，输出 JSON 结果："
    )

    content: List[Dict[str, Any]] = [{"type": "text", "text": text_part}]

    # 均匀采样关键帧，避免只截取了分片开头的画面导致大模型提前截断总结
    max_frames = 15
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        sampled_frames = [frames[int(i * step)] for i in range(max_frames)]
    else:
        sampled_frames = frames

    for frame in sampled_frames:
        time_str = str(frame.get("time", "未知"))
        image_b64 = str(frame.get("image", ""))
        content.append({"type": "text", "text": f"关键帧时间戳: {time_str}"})
        if image_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"},
            })

    model_client = llm_model or get_model_for_capability("vision")
    model_name = get_model_name_for_capability("vision")

    with trace_llm_call(
        provider="openai_compatible",
        model=model_name,
        scope="chunk_multimodal_worker",
        scope_id=chunk_id,
        workflow_state="ANALYSIS",
    ):
        raw_content = model_client.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": _MULTIMODAL_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
            max_tokens=2000,
            timeout=timeout_seconds,
        )

    result = _parse_multimodal_output(raw_content)
    if not result:
        return _build_fallback_output(chunk_id, raw_content[:500] if raw_content else "")
    return result


def _run_multimodal_with_retry(
    chunk_id: str,
    transcript_text: str,
    frames: List[FramePayload],
    user_prompt: str,
    narrative_arc: List[Dict[str, Any]],
    previous_chunk_summaries: List[Dict[str, Any]],
    llm_model: "BaseModel | None" = None,
    trace_id: str = "",
) -> "Tuple[Dict[str, Any], str, int]":
    last_error: "Exception | None" = None
    for attempt in range(CHUNK_WORKER_MAX_RETRIES + 1):
        try:
            result = _llm_multimodal_analyze(
                chunk_id=chunk_id,
                transcript_text=transcript_text,
                frames=frames,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                timeout_seconds=CHUNK_WORKER_TIMEOUT_SECONDS,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            return result, "ok", attempt
        except Exception as exc:
            last_error = exc

    status = _classify_error(last_error or Exception("multimodal worker failed"))
    fallback = _build_fallback_output(
        chunk_id,
        f"{CHUNK_DEGRADED_MARKER}:multimodal:{status}:retries_exhausted",
    )
    return fallback, status, CHUNK_WORKER_MAX_RETRIES


def chunk_multimodal_worker_node(state: ChunkState, llm_model: "BaseModel | None" = None) -> dict:
    """
    原生多模态分析 worker。
    替代原先的 audio -> vision 串行流水线。直接读取 transcript 和关键帧，并融合输出。
    """
    chunk_id = str(state.get("chunk_id", "")).strip()
    if not chunk_id:
        return {
            "chunk_summary": f"{CHUNK_DEGRADED_MARKER}:multimodal:degraded:no_chunk_id",
            "chunk_insights_md": f"- {CHUNK_DEGRADED_MARKER}:multimodal:degraded:no_chunk_id",
            "modality_status": {"multimodal": "degraded"},
            "latency_ms": {"multimodal": 0},
        }

    transcript_segments = state.get("transcript_segments", [])
    if not isinstance(transcript_segments, list):
        transcript_segments = []
    
    transcript_text = _build_transcript_text_with_timestamps(transcript_segments)

    keyframe_indexes = state.get("keyframe_indexes", [])
    if not isinstance(keyframe_indexes, list):
        keyframe_indexes = []
    keyframes = state.get("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
    keyframes_base_path = str(state.get("keyframes_base_path", ""))
    
    user_prompt = str(state.get("user_prompt", ""))
    narrative_arc = state.get("narrative_arc") or []
    if not isinstance(narrative_arc, list):
        narrative_arc = []
    previous_chunk_summaries = state.get("previous_chunk_summaries", [])
    if not isinstance(previous_chunk_summaries, list):
        previous_chunk_summaries = []
    trace_id = str(state.get("trace_id", ""))

    started = time.perf_counter()

    selected_frames: List[FramePayload] = []
    for idx in keyframe_indexes:
        if not isinstance(idx, int) or idx < 0 or idx >= len(keyframes):
            continue
        frame = keyframes[idx]
        if isinstance(frame, dict):
            normalized_frame = dict(frame)
            normalized_frame["image"] = resolve_frame_image_base64(frame, keyframes_base_path)
            selected_frames.append(normalized_frame)

    with start_span(
        build_span_name("workflow", "chunk_multimodal", "analyze"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_chunk",
            "scope_id": chunk_id,
            "workflow_state": "ANALYSIS",
        },
    ):
        if not selected_frames and not transcript_text:
            result = _build_fallback_output(
                chunk_id,
                f"{CHUNK_DEGRADED_MARKER}:multimodal:degraded:no_evidence",
            )
            modality_status = "degraded"
            print(f"  -> [Multimodal Worker] {chunk_id}: no evidence, degraded.")
        else:
            result, modality_status, _ = _run_multimodal_with_retry(
                chunk_id=chunk_id,
                transcript_text=transcript_text,
                frames=selected_frames,
                user_prompt=user_prompt,
                narrative_arc=narrative_arc,
                previous_chunk_summaries=previous_chunk_summaries,
                llm_model=llm_model,
                trace_id=trace_id,
            )
            if modality_status != "ok":
                print(f"  -> [Multimodal Worker] {chunk_id}: status={modality_status}")
            else:
                print(f"  -> [Multimodal Worker] {chunk_id}: multimodal insights generated.")

    latency_ms = int((time.perf_counter() - started) * 1000)

    existing_modality = state.get("modality_status") or {}
    new_modality = {**existing_modality, "multimodal": modality_status}
    existing_latency = state.get("latency_ms") or {}
    new_latency = {**existing_latency, "multimodal": latency_ms}

    return {
        "chunk_summary": result.get("chunk_summary", ""),
        "chunk_insights_md": result.get("chunk_insights_md", ""),
        "modality_status": new_modality,
        "latency_ms": new_latency,
    }
