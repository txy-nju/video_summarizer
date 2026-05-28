import time
import random
import json
from typing import Any, List, Dict, Callable, Optional

from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span

from core.llm.config import resolve_api_key
from core.llm.factory import get_model_for_capability, get_model_name_for_capability

from core.workflow.video_summary.graph import build_video_summary_graph, build_finalization_graph
from core.workflow.checkpoint_factory import create_checkpointer
from core.workflow.session import ensure_thread_id
from core.workflow.time_travel import (
    parse_timestamp_to_seconds,
    find_nearest_keyframe,
    extract_transcript_window,
)
from config.settings import (
    CHECKPOINT_BACKEND,
    CHECKPOINT_DB_URL,
    ENABLE_METRICS_LOGGING,
    METRICS_SAMPLE_RATE,
    TEMP_FRAMES_DIR,
)
from utils.logger import setup_logger, log_metric_event
from core.workflow.video_summary.utils.frame_utils import resolve_frame_image_base64


def _build_time_travel_fallback_response(
    timestamp: str,
    frame_time: str,
    transcript_window: str,
    reason: str,
) -> str:
    return (
        "[系统降级回答]\n"
        f"- 目标时间戳: {timestamp}\n"
        f"- 最近关键帧: {frame_time}\n"
        f"- 语音证据:\n{transcript_window}\n\n"
        f"降级原因: {reason}"
    )


def analyze_video(
    transcript: str,
    keyframes: List[Dict],
    user_prompt: str = "请结合画面与语音，给出一个全面、客观的高质量视频总结。",
    status_callback: Optional[Callable[[str], None]] = None,
    thread_id: str = "",
    trace_id: str = "",
) -> Dict[str, Any]:
    """
    第一阶段：执行分片规划、分析、聚合并进入人类审批关口。
    返回待审批包（而不是最终总结）。
    """
    if status_callback:
        status_callback("⚙️ [LangGraph 初始化] 正在编排多智能体认知状态机网络...")

    metrics_enabled = ENABLE_METRICS_LOGGING and random.random() <= METRICS_SAMPLE_RATE
    metrics_logger = setup_logger("video_summarizer.metrics")
    run_started_at = time.perf_counter()

    checkpointer = create_checkpointer(CHECKPOINT_BACKEND, CHECKPOINT_DB_URL)
    workflow_app = build_video_summary_graph(checkpointer=checkpointer)
    resolved_thread_id = ensure_thread_id(thread_id)

    if status_callback:
        status_callback(f"🧵 [Session] 当前会话 thread_id: {resolved_thread_id}")
        status_callback("🛠️ [Concurrency] 当前并发模式: send_api")

    if metrics_enabled:
        keyframes_estimate_bytes = 0
        try:
            keyframes_estimate_bytes = len(json.dumps(keyframes, ensure_ascii=False).encode("utf-8"))
        except Exception:
            keyframes_estimate_bytes = 0

        log_metric_event(
            metrics_logger,
            "workflow_started",
            thread_id=resolved_thread_id,
            concurrency_mode="send_api",
            transcript_chars=len(transcript),
            keyframes_count=len(keyframes),
            keyframes_estimate_bytes=keyframes_estimate_bytes,
            user_prompt_chars=len(user_prompt),
        )

    initial_state: Dict[str, Any] = {
        "trace_id": trace_id,
        "transcript": transcript,
        "keyframes": keyframes,
        "keyframes_base_path": str(TEMP_FRAMES_DIR),
        "user_prompt": user_prompt,
        "aggregated_chunk_insights": "",
        "human_edited_aggregated_insights": "",
        "human_guidance": "",
        "human_gate_status": "pending",
        "human_gate_reason": "",
    }

    if status_callback:
        status_callback("⚡ [引擎点火] 第一阶段启动：并行并发拆解多模态任务并生成待审批聚合稿...")

    node_msg_map = {
        "chunk_planner_node": "📋 [Plan Checker] 正在以时间戳为锚点，将视频逻辑分割成多个 120 秒粒度的分片任务...",
        "map_dispatch_node": "🗺️ [Dispatcher] 正在为微智能体群编排分片执行配方，准备发起并行实时处理...",
        "chunk_audio_worker_node": "🎧 [Chunk Audio Send Worker] 图级 fan-out：正在处理单分片音频洞察...",
        "chunk_vision_worker_node": "📸 [Chunk Vision Send Worker] 图级 fan-out：正在处理单分片视觉洞察...",
        "chunk_subgraph_node": "⚡ [Chunk Subgraph] 正在处理单分片音视频串行分析（音频→视觉）...",
        "wave_gate_node": "🧱 [Wave Gate] 正在校验当前波次分片完成状态，准备进入下一波次或聚合...",
        "chunk_aggregator_node": "🧾 [Chunk Aggregator] 正在按时间线整合 n 个分片洞察，生成统一证据底稿...",
        "human_gate_node": "🧑‍⚖️ [Human Gate] 已到达人类审批关口，请确认或编辑聚合稿后继续。",
    }

    def _emit_chunk_progress(
        total_chunks: int,
        audio_done_ids: set[str],
        vision_done_ids: set[str],
        stage: str = "running",
    ) -> None:
        if not status_callback:
            return

        safe_total = max(0, total_chunks)
        audio_done = len(audio_done_ids)
        vision_done = len(vision_done_ids)
        overall_total = safe_total * 2
        overall_done = audio_done + vision_done
        overall_percent = int((overall_done / overall_total) * 100) if overall_total > 0 else 0

        payload = {
            "type": "chunk_progress",
            "stage": stage,
            "total_chunks": safe_total,
            "audio_done": audio_done,
            "vision_done": vision_done,
            "overall_done": overall_done,
            "overall_total": overall_total,
            "overall_percent": overall_percent,
        }
        status_callback(f"[[PROGRESS]]{json.dumps(payload, ensure_ascii=False)}")

    current_state = initial_state.copy()
    previous_event_at = run_started_at
    node_event_counts: Dict[str, int] = {}
    audio_done_ids: set[str] = set()
    vision_done_ids: set[str] = set()
    total_chunks = 0
    last_progress_signature: tuple[int, int, int] = (-1, -1, -1)

    with start_span(
        build_span_name("workflow", "analysis", "run"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_analysis",
            "scope_id": resolved_thread_id,
        },
    ):
        for output in workflow_app.stream(
            initial_state,
            {"configurable": {"thread_id": resolved_thread_id}},
            stream_mode="updates",
        ):
            for node_name, state_update in output.items():
                event_now = time.perf_counter()
                since_previous_ms = int((event_now - previous_event_at) * 1000)
                node_event_counts[node_name] = node_event_counts.get(node_name, 0) + 1

                current_state.update(state_update)

                chunk_plan = current_state.get("chunk_plan", [])
                if isinstance(chunk_plan, list):
                    total_chunks = len(chunk_plan)

                if node_name in {
                    "chunk_audio_worker_node",
                    "chunk_vision_worker_node",
                    "chunk_subgraph_node",
                }:
                    updated_chunks = state_update.get("chunk_results", []) if isinstance(state_update, dict) else []
                    if isinstance(updated_chunks, list):
                        for chunk_item in updated_chunks:
                            if not isinstance(chunk_item, dict):
                                continue
                            chunk_id = str(chunk_item.get("chunk_id", "")).strip()
                            if not chunk_id:
                                continue
                            if isinstance(chunk_item.get("transcript_claims"), list) and chunk_item["transcript_claims"]:
                                audio_done_ids.add(chunk_id)
                            if isinstance(chunk_item.get("frame_references"), list) and chunk_item["frame_references"]:
                                vision_done_ids.add(chunk_id)

                    progress_signature = (
                        total_chunks,
                        len(audio_done_ids),
                        len(vision_done_ids),
                    )
                    if progress_signature != last_progress_signature:
                        _emit_chunk_progress(
                            total_chunks,
                            audio_done_ids,
                            vision_done_ids,
                            stage="running",
                        )
                        last_progress_signature = progress_signature

                if metrics_enabled:
                    chunk_results = current_state.get("chunk_results", [])
                    chunk_count = len(chunk_results) if isinstance(chunk_results, list) else 0
                    log_metric_event(
                        metrics_logger,
                        "workflow_node_update",
                        thread_id=resolved_thread_id,
                        concurrency_mode="send_api",
                        node_name=node_name,
                        node_event_count=node_event_counts[node_name],
                        since_previous_event_ms=since_previous_ms,
                        chunk_count=chunk_count,
                    )
                previous_event_at = event_now

                if status_callback and node_name in node_msg_map:
                    msg = node_msg_map[node_name]
                    if node_name == "chunk_aggregator_node":
                        chunk_results = current_state.get("chunk_results", [])
                        if isinstance(chunk_results, list) and chunk_results:
                            num_chunks = len(chunk_results)
                            msg = f"{msg}\n✅ [微智能体群汇聚] 已完成 {num_chunks} 个分片的并行深度分析，成果已交付全局融合层..."
                    status_callback(msg)

    _emit_chunk_progress(
        total_chunks,
        audio_done_ids,
        vision_done_ids,
        stage="finished",
    )

    aggregated_chunk_insights = str(current_state.get("aggregated_chunk_insights", ""))
    editable_aggregated_chunk_insights = str(
        current_state.get("human_edited_aggregated_insights", "") or aggregated_chunk_insights
    )
    review_package: Dict[str, Any] = {
        "thread_id": resolved_thread_id,
        "stage": "pending_human_review",
        "human_gate_status": str(current_state.get("human_gate_status", "pending") or "pending"),
        "human_gate_reason": str(current_state.get("human_gate_reason", "human_review_required") or "human_review_required"),
        "aggregated_chunk_insights": aggregated_chunk_insights,
        "editable_aggregated_chunk_insights": editable_aggregated_chunk_insights,
        "human_guidance": str(current_state.get("human_guidance", "")),
        "chunk_count": len(current_state.get("chunk_plan", []) if isinstance(current_state.get("chunk_plan", []), list) else []),
    }

    if metrics_enabled:
        final_state_estimate_bytes = 0
        try:
            final_state_estimate_bytes = len(json.dumps(current_state, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            final_state_estimate_bytes = 0

        total_duration_ms = int((time.perf_counter() - run_started_at) * 1000)
        log_metric_event(
            metrics_logger,
            "workflow_finished",
            thread_id=resolved_thread_id,
            concurrency_mode="send_api",
            total_duration_ms=total_duration_ms,
            node_count=len(node_event_counts),
            node_event_counts=node_event_counts,
            revision_count=current_state.get("revision_count", 0),
            summary_chars=0,
            final_state_estimate_bytes=final_state_estimate_bytes,
        )

    return review_package


def finalize_summary(
    thread_id: str,
    edited_aggregated_chunk_insights: str = "",
    human_guidance: str = "",
    status_callback: Optional[Callable[[str], None]] = None,
    trace_id: str = "",
) -> str:
    """
    第二阶段：基于人类审批结果完成全篇总结。
    """
    if not thread_id or not thread_id.strip():
        raise ValueError("thread_id is required")

    resolved_thread_id = ensure_thread_id(thread_id)
    checkpointer = create_checkpointer(CHECKPOINT_BACKEND, CHECKPOINT_DB_URL)
    checkpoint = checkpointer.get({"configurable": {"thread_id": resolved_thread_id}})
    if not checkpoint:
        raise ValueError(
            f"未找到 thread_id={resolved_thread_id} 的待审批状态，请先执行 analyze_video() 第一阶段。"
        )

    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    if not isinstance(channel_values, dict):
        raise ValueError("checkpoint channel_values format is invalid")

    aggregated_chunk_insights = str(channel_values.get("aggregated_chunk_insights", ""))
    final_edited = edited_aggregated_chunk_insights.strip() if edited_aggregated_chunk_insights else aggregated_chunk_insights
    final_guidance = human_guidance.strip() if human_guidance else ""

    initial_state: Dict[str, Any] = dict(channel_values)
    initial_state.update(
        {
            "trace_id": trace_id or str(channel_values.get("trace_id", "")),
            "human_gate_status": "approved",
            "human_gate_reason": "approved",
            "human_edited_aggregated_insights": final_edited,
            "human_guidance": final_guidance,
            "draft_summary": "",
            "hallucination_score": "",
            "usefulness_score": "",
            "feedback_instructions": "",
            "revision_count": 0,
        }
    )

    if status_callback:
        status_callback(f"🧵 [Session] 当前会话 thread_id: {resolved_thread_id}")
        status_callback("🚀 [Finalization] 审批通过，进入第二阶段全篇总结与质量审查...")

    workflow_app = build_finalization_graph(checkpointer=checkpointer)
    current_state: Dict[str, Any] = dict(initial_state)
    node_msg_map = {
        "fusion_drafter_node": "🧩 [Synthesizer Agent] 正在根据人类审批稿生成全篇总结...",
        "hallucination_grader_node": "⚖️ [Hallucination Guard] 正在进行事实一致性审查...",
        "usefulness_grader_node": "🎯 [Usefulness Guard] 正在进行需求命中审查...",
    }

    with start_span(
        build_span_name("workflow", "finalization", "run"),
        attributes={
            "trace_id": initial_state.get("trace_id", ""),
            "scope": "workflow_finalization",
            "scope_id": resolved_thread_id,
        },
    ):
        for output in workflow_app.stream(
            initial_state,
            {"configurable": {"thread_id": resolved_thread_id}},
            stream_mode="updates",
        ):
            for node_name, state_update in output.items():
                current_state.update(state_update)
                if status_callback and node_name in node_msg_map:
                    status_callback(node_msg_map[node_name])

    return str(current_state.get("draft_summary", ""))


def answer_question_at_timestamp(
    thread_id: str,
    timestamp: str,
    question: str,
    window_seconds: int = 20,
    status_callback: Optional[Callable[[str], None]] = None,
    trace_id: str = "",
) -> str:
    """
    基于 checkpoint 的时间旅行追问入口。
    输入 thread_id、时间戳和问题，返回带证据约束的回答。
    """
    if not question or not question.strip():
        raise ValueError("question is required")
    if not thread_id or not thread_id.strip():
        raise ValueError("thread_id is required")

    resolved_thread_id = ensure_thread_id(thread_id)
    target_seconds = parse_timestamp_to_seconds(timestamp)

    if status_callback:
        status_callback(f"🕒 [Time Travel] 正在回溯 thread_id={resolved_thread_id} 的历史状态...")

    checkpointer = create_checkpointer(CHECKPOINT_BACKEND, CHECKPOINT_DB_URL)
    checkpoint = checkpointer.get({"configurable": {"thread_id": resolved_thread_id}})

    if not checkpoint:
        return (
            f"[系统提示] 未找到 thread_id={resolved_thread_id} 的历史会话状态。"
            "请先用同一个 thread_id 跑一次完整视频总结流程。"
        )

    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    if not isinstance(channel_values, dict):
        return "[系统提示] 检索到的会话状态格式异常，无法执行时间旅行追问。"

    transcript = str(channel_values.get("transcript", ""))
    keyframes = channel_values.get("keyframes", [])
    keyframes_base_path = str(channel_values.get("keyframes_base_path", ""))
    draft_summary = str(channel_values.get("draft_summary", ""))
    user_prompt = str(channel_values.get("user_prompt", ""))

    if not isinstance(keyframes, list):
        keyframes = []

    representative_frames = find_nearest_keyframe(keyframes, target_seconds, window_seconds=window_seconds)
    transcript_window = extract_transcript_window(transcript, target_seconds, window_seconds=window_seconds)

    # 处理多帧返回值：representative_frames 现在是一个列表
    if not isinstance(representative_frames, list):
        # 向后兼容：如果返回的是单帧（Dict），转换为列表
        representative_frames = [representative_frames] if representative_frames else []

    frame_times = [f.get("time", "未知") for f in representative_frames] if representative_frames else []
    frame_times_str = ", ".join(frame_times) if frame_times else "未命中"

    if status_callback:
        frame_count = len(representative_frames)
        status_callback(
            f"🎯 [Time Travel] 已定位目标窗口 {timestamp} ±{window_seconds}s，"
            f"已选取 {frame_count} 帧代表性关键帧（时间戳: {frame_times_str}）"
        )

    if not resolve_api_key("chat"):
        return _build_time_travel_fallback_response(
            timestamp=timestamp,
            frame_time=frame_times_str,
            transcript_window=transcript_window,
            reason="未配置 CHAT_API_KEY 或 OPENAI_API_KEY，当前返回的是证据抽取结果（已选取 {} 帧视觉证据）。".format(len(representative_frames)),
        )

    model_client = get_model_for_capability("chat")
    model_name = get_model_name_for_capability("chat")

    system_prompt = (
        "你是一名严谨的视频证据问答助手。"
        "你只能基于提供的时间窗证据回答，禁止超出证据臆测。"
        "若证据不足，必须明确说明不足点。"
    )

    evidence_text = (
        f"[会话ID] {resolved_thread_id}\n"
        f"[目标时间戳] {timestamp}\n"
        f"[时间窗] ±{window_seconds}s\n"
        f"[已选取的关键帧时间戳] {frame_times_str}\n"
        f"[用户原始总结侧重点] {user_prompt}\n\n"
        f"[语音证据]\n{transcript_window}\n\n"
        f"[历史总结草稿摘要]\n{draft_summary[:1500]}"
    )

    user_content: List[Dict] = [{"type": "text", "text": evidence_text + f"\n\n[追问问题]\n{question}"}]
    
    # 为所有代表性帧添加图像证据
    for idx, frame in enumerate(representative_frames, 1):
        if isinstance(frame, dict):
            frame_image_b64 = resolve_frame_image_base64(frame, keyframes_base_path)
            if frame_image_b64:
                frame_time = frame.get("time", "未知")
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{frame_image_b64}",
                            "detail": "low",
                        },
                    }
                )
                # 在文本中添加帧的时间戳标注
                user_content[0]["text"] += f"\n[视觉证据帧 {idx}] 时间戳: {frame_time}"

    messages_payload: Any = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        with trace_llm_call(
            provider="openai_compatible",
            model=model_name,
            scope="time_travel_qa",
            scope_id=resolved_thread_id,
            task_id=resolved_thread_id,
            workflow_state="TIME_TRAVEL_QA",
        ):
            answer = model_client.chat_completion(
                model=model_name,
                messages=messages_payload,
                temperature=0.2,
            )
    except Exception as exc:
        if status_callback:
            status_callback(f"⚠️ [Time Travel] OpenAI 调用异常，已降级返回证据片段（包含 {len(representative_frames)} 帧）: {str(exc)}")
        return _build_time_travel_fallback_response(
            timestamp=timestamp,
            frame_time=frame_times_str,
            transcript_window=transcript_window,
            reason=f"OpenAI API 调用异常: {str(exc)}",
        )

    return answer or "[系统提示] 已完成追问，但模型未返回文本内容。"
