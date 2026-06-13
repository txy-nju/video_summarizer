"""VideoQAAgent — encapsulates LLM calls for video question answering.

Two modes:
- ``"rag"``: HybridSearch + Reranker retrieval, then multimodal or text-only LLM.
  Supports optional two-phase decision (answer vs search) before retrieval.
- ``"timestamp"``: Checkpoint-based time-travel, extracts evidence window
  around a target timestamp and calls LLM with the evidence.

Supports multi-turn conversation memory via :class:`BaseChatMemory`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Iterator

from core.agent.base import BaseAgent
from core.agent.events import AgentProgressEvent
from core.context.execution_context import ExecutionContext
from core.memory.base import BaseChatMemory

logger = logging.getLogger(__name__)

# ── Default system prompt templates ────────────────────────────────────────

_DEFAULT_RAG_SYSTEM_PROMPT = (
    "你是一名视频内容问答助手。"
    "请基于提供的视频转录片段和相关视频帧，准确、客观地回答用户的问题。"
    "如果提供的材料不足以回答问题，请明确说明。"
    "请用中文回答。"
)

_DEFAULT_TIMESTAMP_SYSTEM_PROMPT = (
    "你是一名严谨的视频证据问答助手。"
    "你只能基于提供的时间窗证据回答，禁止超出证据臆测。"
    "若证据不足，必须明确说明不足点。"
    "请用中文回答。"
)

_DEFAULT_DECISION_PROMPT = """\
你是一名视频内容问答助手。根据对话历史和用户问题，判断是否需要从视频中检索信息。

输出格式（二选一，不要输出其他任何内容）：
- DECISION: answer  （如果对话历史已包含足够信息可直接回答）
- DECISION: search  （如果需要检索视频转录内容获取新信息）

注意：
- 追问类问题（如"刚才说的那个"、"你提到的"）通常不需要检索
- 需要查找视频中的具体内容时才需要检索
- 不确定时请选择 search
"""

_DEFAULT_ANSWER_PROMPT = """\
你是一名视频内容问答助手。请根据以下信息，结合对话历史，准确回答用户的问题。

{retrieval_context}

重要规则：
- 请直接输出纯文本答案，不要使用任何标签格式（包括但不限于 DECISION、THOUGHT、ACTION、FINAL_ANSWER 等）
- 如果提供了检索结果，请基于检索结果回答
- 如果检索结果不足以回答问题，请明确说明
- 请用中文回答
"""

_DECISION_MAX_TOKENS = 50


class VideoQAAgent(BaseAgent):
    """Agent that answers questions about a specific video.

    Parameters
    ----------
    memory:
        Conversation memory for multi-turn history.
    rag_agent_service:
        ``RagAgentService`` instance for RAG retrieval and frame lookup.
    rag_stream_llm:
        ``RagStreamLLM`` instance for streaming LLM calls.
    workflow_service:
        Optional ``WorkflowOrchestrationService`` for time-travel QA.
        When omitted, timestamp mode falls back to direct checkpoint loading.
    system_prompt_template:
        Optional custom system prompt template. Supports ``{mode}`` placeholder.
        When omitted, defaults are used per mode.
    decision_prompt_template:
        Optional custom Phase 1 decision prompt for RAG mode.
    answer_prompt_template:
        Optional custom Phase 2 answer prompt for RAG mode.
    enable_decision_phase:
        Whether to run the decision phase in RAG mode before retrieval.
        Default ``True`` — set to ``False`` to always search (legacy behavior).
    """

    def __init__(
        self,
        *,
        memory: BaseChatMemory,
        rag_agent_service: Any,
        rag_stream_llm: Any,
        workflow_service: Any | None = None,
        system_prompt_template: str | None = None,
        decision_prompt_template: str | None = None,
        answer_prompt_template: str | None = None,
        enable_decision_phase: bool = True,
    ) -> None:
        self._memory = memory
        self._rag_agent_service = rag_agent_service
        self._llm = rag_stream_llm
        self._workflow_service = workflow_service
        self._system_prompt_template = system_prompt_template
        self._decision_prompt_template = decision_prompt_template or _DEFAULT_DECISION_PROMPT
        self._answer_prompt_template = answer_prompt_template or _DEFAULT_ANSWER_PROMPT
        self._enable_decision_phase = enable_decision_phase
        # Mutable: cited sources collected during the most recent answer_stream call
        self.last_cited_sources: list[dict] = []

    # ── BaseAgent interface ───────────────────────────────────────────────

    def answer(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> str:
        """Non-streaming answer (default RAG mode)."""
        return "".join(
            t for t in self.answer_stream(
                question=question, chat_id=chat_id, kbid=kbid, owner_id=owner_id
            )
            if isinstance(t, str)
        )

    def answer_stream(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> Iterator[str]:
        """Streaming answer — default RAG mode.

        Uses ``chat_id`` as the task identifier for video collection lookup
        and conversation memory key.  The ``kbid`` parameter is ignored
        (video QA is scoped to a task, not a knowledge base).
        """
        return self.answer_stream_with_context(
            question=question,
            chat_id=chat_id,
            owner_id=owner_id,
            mode="rag",
        )

    # ── Extended interface ────────────────────────────────────────────────

    def answer_stream_with_context(
        self,
        *,
        question: str,
        chat_id: str,
        owner_id: str,
        mode: str = "rag",
        attachments: list[dict] | None = None,
        timestamp: str = "",
        window_seconds: int | None = None,
    ) -> Iterator[str]:
        """Streaming answer with video-specific parameters.

        Parameters
        ----------
        question:
            User's question text.
        chat_id:
            Task ID — used as memory key and for video collection resolution.
        owner_id:
            Authenticated user ID for permission-scoped history loading.
        mode:
            ``"rag"`` for RAG-based QA, ``"timestamp"`` for time-travel QA.
        attachments:
            Optional user-uploaded image attachments (list of dicts with
            ``mime_type``, ``oss_key`` / ``frame_path``).
        timestamp:
            Target timestamp in HH:MM:SS format (only for ``mode="timestamp"``).
        window_seconds:
            Evidence window size in seconds (only for ``mode="timestamp"``).
            When None, defaults to 20s.
        """
        self.last_cited_sources = []

        if mode == "timestamp":
            yield from self._answer_timestamp(
                question=question,
                chat_id=chat_id,
                owner_id=owner_id,
                timestamp=timestamp,
                window_seconds=window_seconds or 20,
            )
        else:
            yield from self._answer_rag(
                question=question,
                chat_id=chat_id,
                owner_id=owner_id,
                attachments=attachments,
            )

    # ── RAG mode ──────────────────────────────────────────────────────────

    def _answer_rag(
        self,
        *,
        question: str,
        chat_id: str,
        owner_id: str,
        attachments: list[dict] | None,
    ) -> Iterator[str]:
        """RAG-based QA with optional two-phase decision.

        Phase 1 (optional): Fast decision — answer from history or search video.
        Phase 2: RAG retrieval (if needed) → streaming LLM answer.
        """
        self.last_cited_sources = []

        # ── Phase 1: Decision (optional) ─────────────────────────────
        if self._enable_decision_phase:
            yield AgentProgressEvent("deciding", "正在分析是否需要检索视频内容...")

            decision_messages = self._memory.build_messages(
                chat_id=chat_id,
                owner_id=owner_id,
                system_prompt=self._decision_prompt_template,
                current_question=question,
                rag_context="",
            )

            try:
                decision_raw = self._llm._model.chat_completion(
                    model=self._llm._model_name,
                    messages=decision_messages,
                    temperature=0,
                    max_tokens=_DECISION_MAX_TOKENS,
                )
            except Exception as exc:
                logger.exception("VideoQAAgent: decision LLM call failed")
                yield f"\n（回答生成失败: {exc}）"
                return

            decision = _parse_decision(decision_raw.strip() if decision_raw else "")
            logger.info("VideoQAAgent: decision=%s (raw=%r)", decision, decision_raw[:80])

            if decision == "answer":
                # Skip RAG retrieval — answer from conversation history
                yield AgentProgressEvent("generating", "正在基于对话历史生成回答...")
                answer_prompt = self._build_answer_prompt(retrieval_context="（基于已有对话知识回答）")
                answer_messages = self._memory.build_messages(
                    chat_id=chat_id,
                    owner_id=owner_id,
                    system_prompt=answer_prompt,
                    current_question=question,
                    rag_context="",
                )
                # Guard against Redis cache returning Phase 1's decision prompt
                if answer_messages and answer_messages[0]["role"] == "system":
                    answer_messages[0]["content"] = answer_prompt
                try:
                    yield from self._llm._model.stream_chat_completion(
                        model=self._llm._model_name,
                        messages=answer_messages,
                    )
                except Exception as exc:
                    logger.exception("VideoQAAgent: answer LLM call failed")
                    yield f"\n（回答生成失败: {exc}）"
                return

            # "search" or unknown → fall through to retrieval below
            # (unknown is treated as "search" for video QA — safer to retrieve)

        # ── RAG Retrieval ───────────────────────────────────────────
        yield AgentProgressEvent("searching", "正在检索视频相关内容...")

        # Build messages with conversation history
        system_prompt = self._build_system_prompt(mode="rag")
        messages = self._memory.build_messages(
            chat_id=chat_id,
            owner_id=owner_id,
            system_prompt=system_prompt,
            current_question="",
            rag_context="",
        )
        # Guard against Redis cache returning Phase 1's decision prompt
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = system_prompt

        collection = self._rag_agent_service._resolve_video_collection(chat_id)
        rag_context = self._rag_agent_service._build_retrieval_context(
            question=question,
            collection=collection,
            top_k=5,
            rerank=True,
        )

        self.last_cited_sources = rag_context.cited_sources

        # Merge system-retrieved frames with user-uploaded attachments
        extra_frames = self._rag_agent_service._download_attachment_frames(
            attachments or []
        )
        all_frames = list(rag_context.frames) + extra_frames

        # Build current-turn user message
        text_context = (
            "\n\n".join(r.text for r in rag_context.results)
            if rag_context.results
            else ""
        )

        if not text_context and not all_frames:
            yield "未找到相关的视频内容。"
            return

        if all_frames:
            # Multimodal message
            content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": (
                        "请基于以下视频转录内容和对应视频帧回答问题。\n\n"
                        f"转录内容：\n{text_context}"
                    ),
                }
            ]
            for f in all_frames:
                try:
                    with open(f["frame_path"], "rb") as img_file:
                        b64 = base64.b64encode(img_file.read()).decode()
                    label = f.get("time_range", "")
                    mime = f.get("mime_type", "image/jpeg")
                    if label:
                        content.append(
                            {"type": "text", "text": f"视频帧（{label}）："}
                        )
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        }
                    )
                except OSError:
                    logger.debug(
                        "_answer_rag: skipping unreadable frame %s",
                        f.get("frame_path"),
                    )

            content.append({"type": "text", "text": f"\n问题：{question}"})
            messages.append({"role": "user", "content": content})
        else:
            # Text-only message
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "请基于以下视频转录内容回答问题。\n\n"
                        f"转录内容：\n{text_context}\n\n"
                        f"问题：{question}"
                    ),
                }
            )

        # ── Phase 2: Stream answer (token-by-token) ─────────────────
        yield AgentProgressEvent("generating", "正在生成回答...")
        try:
            yield from self._llm._model.stream_chat_completion(
                model=self._llm._model_name,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("VideoQAAgent: RAG LLM call failed")
            yield f"\n（回答生成失败: {exc}）"

    # ── Timestamp mode ─────────────────────────────────────────────────────

    def _answer_timestamp(
        self,
        *,
        question: str,
        chat_id: str,
        owner_id: str,
        timestamp: str,
        window_seconds: int,
    ) -> Iterator[str]:
        """Time-travel QA: checkpoint evidence extraction → LLM."""
        from config.settings import CHECKPOINT_BACKEND, CHECKPOINT_DB_URL
        from core.llm.config import resolve_api_key
        from core.workflow.checkpoint_factory import create_checkpointer
        from core.workflow.session import ensure_thread_id
        from core.workflow.time_travel import (
            extract_transcript_window,
            find_nearest_keyframe,
            parse_timestamp_to_seconds,
        )
        from core.workflow.video_summary.utils.frame_utils import (
            resolve_frame_image_base64,
        )

        system_prompt = self._build_system_prompt(mode="timestamp")

        # 1. Build messages with history
        messages = self._memory.build_messages(
            chat_id=chat_id,
            owner_id=owner_id,
            system_prompt=system_prompt,
            current_question="",
            rag_context="",
        )

        # 2. Load checkpoint
        yield AgentProgressEvent("loading", "正在加载视频分析状态...")
        resolved_thread_id = ensure_thread_id(chat_id)
        target_seconds = parse_timestamp_to_seconds(timestamp)

        checkpointer = create_checkpointer(CHECKPOINT_BACKEND, CHECKPOINT_DB_URL)
        checkpoint = checkpointer.get(
            {"configurable": {"thread_id": resolved_thread_id}}
        )

        if not checkpoint:
            yield (
                f"[系统提示] 未找到 thread_id={resolved_thread_id} 的历史会话状态。"
                "请先用同一个 thread_id 跑一次完整视频总结流程。"
            )
            return

        channel_values = (
            checkpoint.get("channel_values", {})
            if isinstance(checkpoint, dict)
            else {}
        )
        if not isinstance(channel_values, dict):
            yield "[系统提示] 检索到的会话状态格式异常，无法执行时间旅行追问。"
            return

        transcript = str(channel_values.get("transcript", ""))
        keyframes = channel_values.get("keyframes", [])
        keyframes_base_path = str(channel_values.get("keyframes_base_path", ""))
        draft_summary = str(channel_values.get("draft_summary", ""))
        user_prompt = str(channel_values.get("user_prompt", ""))

        if not isinstance(keyframes, list):
            keyframes = []

        # 3. Extract evidence window
        representative_frames = find_nearest_keyframe(
            keyframes, target_seconds, window_seconds=window_seconds
        )
        if not isinstance(representative_frames, list):
            representative_frames = (
                [representative_frames] if representative_frames else []
            )
        transcript_window = extract_transcript_window(
            transcript, target_seconds, window_seconds=window_seconds
        )

        frame_times = (
            [f.get("time", "未知") for f in representative_frames]
            if representative_frames
            else []
        )
        frame_times_str = ", ".join(frame_times) if frame_times else "未命中"

        # 4. Build evidence user message
        if not resolve_api_key("chat"):
            # Fallback: return evidence text without LLM
            yield (
                f"[系统提示] 未配置 CHAT_API_KEY。\n\n"
                f"目标时间戳: {timestamp}\n"
                f"时间窗: ±{window_seconds}s\n"
                f"已选取关键帧: {frame_times_str}\n\n"
                f"语音证据:\n{transcript_window}"
            )
            return

        evidence_text = (
            f"[会话ID] {resolved_thread_id}\n"
            f"[目标时间戳] {timestamp}\n"
            f"[时间窗] ±{window_seconds}s\n"
            f"[已选取的关键帧时间戳] {frame_times_str}\n"
            f"[用户原始总结侧重点] {user_prompt}\n\n"
            f"[语音证据]\n{transcript_window}\n\n"
            f"[历史总结草稿摘要]\n{draft_summary[:1500]}"
        )

        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": evidence_text + f"\n\n[追问问题]\n{question}"}
        ]

        # Encode keyframe images
        has_images = False
        for idx, frame in enumerate(representative_frames, 1):
            if isinstance(frame, dict):
                frame_image_b64 = resolve_frame_image_base64(
                    frame, keyframes_base_path
                )
                if frame_image_b64:
                    has_images = True
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
                    user_content[0]["text"] += (
                        f"\n[视觉证据帧 {idx}] 时间戳: {frame_time}"
                    )

        messages.append({"role": "user", "content": user_content})

        # 5. Stream LLM response
        yield AgentProgressEvent("generating", "正在基于证据生成回答...")
        try:
            yield from self._llm._model.stream_chat_completion(
                model=self._llm._model_name,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("VideoQAAgent: timestamp LLM call failed")
            yield (
                f"\n[系统提示] LLM 调用异常，已降级返回证据片段"
                f"（包含 {len(representative_frames)} 帧）:\n\n"
                f"时间戳: {timestamp}\n"
                f"语音证据:\n{transcript_window}\n"
                f"关键帧时间戳: {frame_times_str}"
            )

    # ── internal ───────────────────────────────────────────────────────────

    def _build_system_prompt(self, *, mode: str) -> str:
        """Build the system prompt for the given mode."""
        if self._system_prompt_template:
            return self._system_prompt_template.format(mode=mode)

        if mode == "timestamp":
            return _DEFAULT_TIMESTAMP_SYSTEM_PROMPT
        return _DEFAULT_RAG_SYSTEM_PROMPT

    def _build_decision_prompt(self) -> str:
        """Build the Phase 1 decision prompt for RAG mode."""
        return self._decision_prompt_template

    def _build_answer_prompt(self, *, retrieval_context: str) -> str:
        """Build the Phase 2 answer prompt with retrieval context."""
        return self._answer_prompt_template.format(
            retrieval_context=retrieval_context,
        )


def _parse_decision(raw: str) -> str | None:
    """Parse a Phase 1 decision response.

    Returns ``"answer"``, ``"search"``, or ``None`` if unparseable.
    """
    if not raw:
        return None
    lowered = raw.lower()
    if "decision:" in lowered:
        after = lowered.split("decision:", 1)[1].strip()
        if after.startswith("answer"):
            return "answer"
        if after.startswith("search"):
            return "search"
    # Heuristic fallback
    if "answer" in lowered and "search" not in lowered:
        return "answer"
    if "search" in lowered:
        return "search"
    return None
