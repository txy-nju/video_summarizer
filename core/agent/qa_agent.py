"""QAAgent — Prompt-driven ReAct agent for video knowledge base Q&A.

Implements the ReAct pattern using a text-based prompt format:

1. User asks question
2. Agent thinks → decides whether to search or answer
3. If search: executes rag_search tool, injects Observation, loops
4. If answer: yields FINAL_ANSWER tokens to caller

Max iterations prevent infinite loops.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from core.agent.base import BaseAgent
from core.agent.events import AgentProgressEvent
from core.agent.parser import parse_react_output
from core.context.execution_context import ExecutionContext
from core.memory.base import BaseChatMemory
from core.tool.executor import ToolExecutor
from core.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

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


class QAAgent(BaseAgent):
    """ReAct agent with prompt-driven tool use.

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
    system_prompt_template:
        Custom system prompt (uses default if not provided).
    max_iterations:
        Maximum ReAct loop iterations (default 3).
    """

    def __init__(
        self,
        *,
        memory: BaseChatMemory,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        rag_stream_llm: Any,
        system_prompt_template: str | None = None,
        max_iterations: int = _MAX_ITERATIONS,
    ) -> None:
        self._memory = memory
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._llm = rag_stream_llm
        self._system_prompt_template = system_prompt_template or _DEFAULT_SYSTEM_PROMPT
        self._max_iterations = max_iterations
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
    ) -> Iterator[str]:
        self.last_cited_sources = []  # Reset for this call
        context = ExecutionContext(owner_id=owner_id, kbid=kbid)
        system_prompt = self._build_system_prompt()
        rag_context = ""

        # Build initial messages with history
        messages = self._memory.build_messages(
            chat_id=chat_id,
            owner_id=owner_id,
            system_prompt=system_prompt,
            current_question=question,
            rag_context=rag_context,
        )

        for iteration in range(1, self._max_iterations + 1):
            logger.debug(
                "QAAgent: iteration %d/%d, messages_count=%d",
                iteration,
                self._max_iterations,
                len(messages),
            )

            # Progress: thinking
            yield AgentProgressEvent("thinking", "正在分析你的问题...")

            # Call LLM (streaming)
            full_response = ""
            try:
                for token in self._llm._model.stream_chat_completion(
                    model=self._llm._model_name,
                    messages=messages,
                ):
                    full_response += token
            except Exception as exc:
                logger.exception("QAAgent: LLM call failed at iteration %d", iteration)
                yield f"\n（回答生成失败: {exc}）"
                return

            # Parse the response
            parsed = parse_react_output(full_response)

            if parsed.final_answer is not None:
                # Yield the final answer (token by token for streaming feel)
                yield parsed.final_answer
                return

            if parsed.is_action:
                # Progress: searching
                query = parsed.action_params.get("query", "")
                search_msg = f"正在检索: {query}" if query else "正在从知识库检索相关内容..."
                yield AgentProgressEvent("searching", search_msg)

                # Execute the tool
                logger.info(
                    "QAAgent: executing tool '%s' with params %s",
                    parsed.action,
                    parsed.action_params,
                )
                tool_result = self._tool_executor.execute(
                    tool_name=parsed.action,
                    params=parsed.action_params,
                    context=context,
                )

                if tool_result.success:
                    # Progress: retrieved
                    n_cited = len(tool_result.cited_sources) if tool_result.cited_sources else 0
                    yield AgentProgressEvent("retrieved", f"已找到 {n_cited} 条相关内容" if n_cited else "未找到相关内容")
                    # Inject observation into messages and continue
                    observation = _OBSERVATION_TEMPLATE.format(
                        data=tool_result.data or "（无结果）"
                    )
                    if tool_result.cited_sources:
                        self.last_cited_sources.extend(tool_result.cited_sources)
                        rag_context = observation  # Use as RAG context for next round
                else:
                    observation = f"工具执行失败: {tool_result.error}"

                # Append assistant thought + observation to messages
                messages.append({"role": "assistant", "content": full_response})
                messages.append({"role": "user", "content": observation})
                continue

            # Should not reach here (parser always returns action or final_answer)
            logger.warning("QAAgent: unexpected parse result at iteration %d", iteration)
            yield full_response
            return

        # Max iterations exhausted → force answer
        logger.warning("QAAgent: max iterations (%d) reached, forcing final answer", self._max_iterations)
        # Make one more LLM call asking for final answer
        messages.append({
            "role": "user",
            "content": "请基于已有信息直接给出最终回答（FINAL_ANSWER格式）。",
        })
        try:
            for token in self._llm._model.stream_chat_completion(
                model=self._llm._model_name,
                messages=messages,
            ):
                full_response = ""
                full_response += token
                # Check for FINAL_ANSWER in streaming — best effort
        except Exception:
            pass
        # Yield whatever we got from the last iteration
        if full_response:
            parsed = parse_react_output(full_response)
            yield parsed.final_answer or full_response

    # ── internal ─────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Build the full system prompt with dynamically injected tool list."""
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
