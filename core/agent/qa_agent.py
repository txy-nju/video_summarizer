"""QAAgent — Two-phase agent for video knowledge base Q&A.

Uses a two-phase decision + streaming answer approach:

1. User asks question
2. Phase 1 (non-streaming, fast): LLM decides whether to search or answer
3. If search: executes rag_search tool, retrieves context
4. Phase 2 (streaming): LLM generates answer token-by-token
   (no ReAct text wrapping — tokens are yielded directly to the SSE stream)

Max iterations prevent infinite loops.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from core.agent.base import BaseAgent
from core.agent.events import AgentProgressEvent
from core.context.execution_context import ExecutionContext
from core.memory.base import BaseChatMemory
from core.tool.executor import ToolExecutor
from core.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_DECISION_PROMPT = """\
你是视频知识库问答助手。根据对话历史和用户问题，判断是否需要从知识库检索信息。

输出格式（二选一，不要输出其他任何内容）：
- DECISION: answer  （如果对话历史已包含足够信息可直接回答）
- DECISION: search  （如果需要检索知识库获取新信息）

注意：
- 追问类问题（如"刚才说的那个"、"你提到的"）通常不需要检索
- 需要查找新知识点的问题才需要检索
- 不确定时请选择 search
"""

_DEFAULT_ANSWER_PROMPT = """\
你是视频知识库问答助手。请根据以下信息，结合对话历史，准确回答用户的问题。

{retrieval_context}

重要规则：
- 请直接输出纯文本答案，不要使用任何标签格式（包括但不限于 DECISION、THOUGHT、ACTION、FINAL_ANSWER 等）
- 如果提供了检索结果，请基于检索结果回答
- 如果检索结果不足以回答问题，请明确说明
- 回答请使用中文
- 引用来源时请注明视频名称和时间段
"""

# ── Legacy prompt templates (kept for backward compatibility) ─────

_DEFAULT_SYSTEM_PROMPT = """\
你是视频知识库问答助手，请基于提供的视频转录内容，结合对话历史，准确回答用户的问题。

{react_instructions}

重要规则：
- 如果问题可以通过对话历史直接回答，无需检索
- 检索时使用与问题最相关的关键词
- 回答请使用中文
- 引用来源时请注明视频名称和时间段
"""

_REACT_INSTRUCTIONS = """\
回答格式:

如果需要从知识库检索信息来回答问题，请严格按以下格式回复:
THOUGHT: <简要说明为什么需要检索，以及检索什么内容>
ACTION: rag_search
QUERY: <具体的检索查询语句>

检索结果会以 Observation 的形式提供给你，之后你可以继续思考并回答。

如果已有足够信息回答问题（或不需要检索），请按以下格式回复:
THOUGHT: <简要说明你的回答思路>
FINAL_ANSWER: <给用户的完整回答>
"""

_OBSERVATION_TEMPLATE = """\
检索结果:
{data}
"""

_MAX_ITERATIONS = 3
_DEFAULT_DECISION_MAX_TOKENS = 50


class QAAgent(BaseAgent):
    """Two-phase agent: fast decision → streaming answer.

    Parameters
    ----------
    memory:
        ChatMemory for loading conversation history.
    tool_registry:
        ToolRegistry for discovering available tools.
    tool_executor:
        ToolExecutor for safe tool execution.
    rag_stream_llm:
        ``RagStreamLLM`` instance for streaming LLM calls.
    decision_prompt_template:
        Custom Phase 1 decision prompt (uses default if not provided).
    answer_prompt_template:
        Custom Phase 2 answer prompt (uses default if not provided).
    system_prompt_template:
        (deprecated) Custom system prompt for legacy ReAct mode.
    max_iterations:
        Maximum decision loop iterations (default 3).
    decision_max_tokens:
        Max tokens for the Phase 1 decision call (default 50).
    """

    def __init__(
        self,
        *,
        memory: BaseChatMemory,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        rag_stream_llm: Any,
        decision_prompt_template: str | None = None,
        answer_prompt_template: str | None = None,
        system_prompt_template: str | None = None,
        max_iterations: int = _MAX_ITERATIONS,
        decision_max_tokens: int = _DEFAULT_DECISION_MAX_TOKENS,
    ) -> None:
        self._memory = memory
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._llm = rag_stream_llm
        self._decision_prompt_template = decision_prompt_template or _DEFAULT_DECISION_PROMPT
        self._answer_prompt_template = answer_prompt_template or _DEFAULT_ANSWER_PROMPT
        # Legacy — kept for backward compatibility
        self._system_prompt_template = system_prompt_template or _DEFAULT_SYSTEM_PROMPT
        self._max_iterations = max_iterations
        self._decision_max_tokens = decision_max_tokens
        # Mutable: cited sources collected during the most recent answer_stream call
        self.last_cited_sources: list[dict] = []

    # ── BaseAgent interface ───────────────────────────────────────────

    def answer(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> str:
        return "".join(
            t for t in self.answer_stream(
                question=question, chat_id=chat_id, kbid=kbid, owner_id=owner_id
            )
            if isinstance(t, str)
        )

    def answer_stream(
        self, *, question: str, chat_id: str, kbid: str, owner_id: str
    ) -> Iterator[str | AgentProgressEvent]:
        """Two-phase streaming answer.

        Phase 1 (non-streaming, fast): LLM decides answer vs search.
        Phase 2 (streaming): LLM generates answer token-by-token — each
        ``str`` token is yielded immediately for real-time SSE delivery.
        """
        self.last_cited_sources = []  # Reset for this call
        context = ExecutionContext(owner_id=owner_id, kbid=kbid)
        rag_context = ""

        # Build decision messages with conversation history
        decision_messages = self._memory.build_messages(
            chat_id=chat_id,
            owner_id=owner_id,
            system_prompt=self._decision_prompt_template,
            current_question=question,
            rag_context="",
        )

        for iteration in range(1, self._max_iterations + 1):
            logger.debug(
                "QAAgent: iteration %d/%d (decision phase)",
                iteration,
                self._max_iterations,
            )

            # ── Phase 1: Decide ──────────────────────────────────────
            yield AgentProgressEvent("deciding", "正在分析是否需要检索知识库...")

            try:
                decision_raw = self._llm._model.chat_completion(
                    model=self._llm._model_name,
                    messages=decision_messages,
                    temperature=0,
                    max_tokens=self._decision_max_tokens,
                )
            except Exception as exc:
                logger.exception("QAAgent: decision LLM call failed at iteration %d", iteration)
                yield f"\n（回答生成失败: {exc}）"
                return

            decision = self._parse_decision(decision_raw.strip() if decision_raw else "")
            logger.info("QAAgent: decision=%s (raw=%r)", decision, decision_raw[:80])

            if decision == "answer":
                # Proceed to Phase 2 with empty RAG context
                break

            if decision == "search":
                # ── Execute RAG search ──────────────────────────────
                yield AgentProgressEvent("searching", "正在从知识库检索相关内容...")

                tool_result = self._tool_executor.execute(
                    tool_name="rag_search",
                    params={"query": question},
                    context=context,
                )

                if tool_result.success:
                    n_cited = len(tool_result.cited_sources) if tool_result.cited_sources else 0
                    yield AgentProgressEvent(
                        "retrieved",
                        f"已找到 {n_cited} 条相关内容" if n_cited else "未找到相关内容",
                    )
                    rag_context = tool_result.data or ""
                    if tool_result.cited_sources:
                        self.last_cited_sources.extend(tool_result.cited_sources)

                    # Append search result as observation for next decision round
                    observation = _OBSERVATION_TEMPLATE.format(
                        data=rag_context or "（无结果）"
                    )
                    decision_messages.append({"role": "assistant", "content": f"DECISION: search (query: {question})"})
                    decision_messages.append({"role": "user", "content": observation})
                else:
                    logger.warning("QAAgent: RAG search failed: %s", tool_result.error)
                    observation = f"工具执行失败: {tool_result.error}"
                    decision_messages.append({"role": "assistant", "content": f"DECISION: search (query: {question})"})
                    decision_messages.append({"role": "user", "content": observation})

                continue

            # Unknown decision — fail-open: treat as answer
            logger.warning("QAAgent: unparseable decision, falling back to answer. raw=%r", decision_raw[:80])
            break

        # ── Iteration limit exhausted ────────────────────────────────
        else:
            logger.warning(
                "QAAgent: max iterations (%d) reached, forcing answer phase",
                self._max_iterations,
            )
            # Note: the "else" branch of a for-loop runs when no break occurred.
            # This means every iteration returned "search" — we force answer.
            n_searches = self._max_iterations
            yield AgentProgressEvent(
                "generating",
                f"已达到最大搜索次数 ({n_searches})，正在基于已有信息生成回答...",
            )

        # ── Phase 2: Stream answer (token-by-token) ──────────────────
        yield AgentProgressEvent("generating", "正在生成回答...")

        retrieval_context_text = rag_context if rag_context else "（基于已有对话知识回答）"
        answer_prompt = self._answer_prompt_template.format(
            retrieval_context=retrieval_context_text,
        )

        answer_messages = self._memory.build_messages(
            chat_id=chat_id,
            owner_id=owner_id,
            system_prompt=answer_prompt,
            current_question=question,
            rag_context="",
        )

        # Guard: ensure the system prompt is correct even when
        # build_messages returns a Redis-cached list whose system
        # message was written by Phase 1's decision prompt.
        if answer_messages and answer_messages[0]["role"] == "system":
            answer_messages[0]["content"] = answer_prompt

        try:
            for token in self._llm._model.stream_chat_completion(
                model=self._llm._model_name,
                messages=answer_messages,
            ):
                yield token
        except Exception as exc:
            logger.exception("QAAgent: answer LLM call failed")
            yield f"\n（回答生成失败: {exc}）"

    # ── internal ─────────────────────────────────────────────────────

    def _parse_decision(self, raw: str) -> str | None:
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
        # Heuristic fallback for common variations
        if "answer" in lowered and "search" not in lowered:
            return "answer"
        if "search" in lowered:
            return "search"
        return None

    def _build_decision_prompt(self) -> str:
        """Build the Phase 1 decision prompt.

        .. deprecated::
            The decision prompt template is now used directly via
            ``self._decision_prompt_template``. This method exists for
            backward compatibility with subclasses that may override it.
        """
        return self._decision_prompt_template

    def _build_answer_prompt(self, retrieval_context: str) -> str:
        """Build the Phase 2 answer prompt with retrieval context injected.

        .. deprecated::
            The answer prompt template is now used directly via
            ``self._answer_prompt_template.format(...)``. This method
            exists for backward compatibility with subclasses that may
            override it.
        """
        return self._answer_prompt_template.format(
            retrieval_context=retrieval_context or "（基于已有对话知识回答）",
        )

    def _build_system_prompt(self) -> str:
        """Build the legacy ReAct system prompt.

        .. deprecated::
            This method is kept for backward compatibility with
            VideoQAAgent and other callers that may still use
            the legacy ReAct prompt format. New code should use
            ``_build_decision_prompt()`` and ``_build_answer_prompt()``.
        """
        tool_list = self._tool_registry.list_for_llm()
        react_instructions = _REACT_INSTRUCTIONS.replace(
            "{tool_list}", tool_list
        )
        # Ensure tool list is included in the instructions
        if "{tool_list}" not in _REACT_INSTRUCTIONS:
            react_instructions = f"{tool_list}\n\n{_REACT_INSTRUCTIONS}"

        return self._system_prompt_template.format(
            react_instructions=react_instructions,
        )
