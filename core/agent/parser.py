"""ReAct output parser — extracts actions and final answers from LLM text.

.. deprecated::
    This module is deprecated as of the two-phase QA refactor.
    QAAgent and VideoQAAgent no longer use ReAct text parsing;
    they use a two-phase decision + direct streaming answer approach instead.
    The module is retained for backward compatibility with existing tests.

Parses the prompt-driven ReAct format:

    THOUGHT: <reasoning>
    ACTION: rag_search
    QUERY: <search query>

    THOUGHT: <reasoning>
    FINAL_ANSWER: <answer to user>

Fail-open: if parsing fails, the entire text is treated as the final answer.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Recognised action names and their expected parameter keys.
_KNOWN_ACTIONS: dict[str, list[str]] = {
    "rag_search": ["query"],
}


class ParsedOutput:
    """Result of parsing a ReAct-formatted LLM response."""

    __slots__ = ("thought", "action", "action_params", "final_answer", "is_action")

    def __init__(
        self,
        *,
        thought: str = "",
        action: str | None = None,
        action_params: dict[str, str] | None = None,
        final_answer: str | None = None,
    ) -> None:
        self.thought = thought
        self.action = action
        self.action_params = action_params or {}
        self.final_answer = final_answer
        # Convenience flag
        self.is_action = action is not None and final_answer is None


def parse_react_output(text: str) -> ParsedOutput:
    """Parse a single ReAct LLM response.

    Returns a ``ParsedOutput`` indicating whether this is an action call
    (needs tool execution) or a final answer (stream to user).
    """
    if not text or not text.strip():
        return ParsedOutput(final_answer="（模型未返回内容）")

    # Extract THOUGHT block (optional; best-effort)
    thought = _extract_block(text, "THOUGHT")

    # Check for FINAL_ANSWER first
    final = _extract_block(text, "FINAL_ANSWER")
    if final:
        return ParsedOutput(thought=thought, final_answer=final)

    # Check for ACTION
    action_name = _extract_block(text, "ACTION")
    if action_name:
        action_name = action_name.strip().lower()
        if action_name in _KNOWN_ACTIONS:
            params: dict[str, str] = {}
            for param_key in _KNOWN_ACTIONS[action_name]:
                val = _extract_block(text, param_key.upper())
                if val:
                    params[param_key] = val
            return ParsedOutput(
                thought=thought,
                action=action_name,
                action_params=params,
            )
        else:
            logger.warning("ReAct parser: unknown action '%s', falling back to final answer", action_name)
            return ParsedOutput(final_answer=text.strip())

    # No recognised markers — fail-open: treat whole text as final answer
    logger.debug("ReAct parser: no markers found, treating as final answer")
    return ParsedOutput(final_answer=text.strip())


def _extract_block(text: str, marker: str) -> str | None:
    """Extract the content after a ReAct marker until the next marker or EOF.

    Example: ``"THOUGHT: hello\\nACTION: foo"`` with marker ``"THOUGHT"`` → ``"hello"``.
    """
    # Match "MARKER:" or "MARKER：" (Chinese colon)
    pattern = rf"{re.escape(marker)}\s*[:：]\s*(.*?)(?=\n\s*(?:THOUGHT|ACTION|QUERY|FINAL_ANSWER)\s*[:：]|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None
