"""ToolRegistry — central registration and discovery of tools.

Agents query the registry to learn what tools are available and to
generate the tool-description block in the system prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool.base import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Thread-safe registry of named tools.

    Usage::

        registry = ToolRegistry()
        registry.register(rag_search_tool)
        tool = registry.get("rag_search")
        prompt_block = registry.list_for_llm()
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ── registration ─────────────────────────────────────────────────

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition.

        Overwrites any existing tool with the same name after logging a
        warning.
        """
        if tool.name in self._tools:
            logger.warning("ToolRegistry: overwriting tool '%s'", tool.name)
        self._tools[tool.name] = tool
        logger.info("ToolRegistry: registered tool '%s'", tool.name)

    # ── lookup ───────────────────────────────────────────────────────

    def get(self, name: str) -> ToolDefinition | None:
        """Look up a tool by name.

        Returns ``None`` when the tool is not registered.
        """
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        """Return all registered tools."""
        return list(self._tools.values())

    # ── prompt generation ────────────────────────────────────────────

    def list_for_llm(self) -> str:
        """Generate a ReAct-format tool list for injection into the system prompt.

        Example output::

            你可以使用以下工具:
            - rag_search(query: string): 从知识库中检索视频转录内容
        """
        if not self._tools:
            return "（当前没有可用的工具）"

        lines = ["你可以使用以下工具:"]
        for tool in self._tools.values():
            params_str = ", ".join(
                f"{p.name}: {p.type}" + (" (可选)" if not p.required else "")
                for p in tool.params
            )
            lines.append(f"- {tool.name}({params_str}): {tool.description}")
        return "\n".join(lines)

    # ── validation ───────────────────────────────────────────────────

    def validate_params(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Validate parameters against a tool's declared schema.

        Returns
        -------
        (is_valid, error_message)
            ``error_message`` is ``None`` when valid.
        """
        tool = self.get(tool_name)
        if tool is None:
            return False, f"未知工具: {tool_name}"

        for param_def in tool.params:
            if param_def.required and param_def.name not in params:
                return False, f"工具 '{tool_name}' 缺少必填参数: {param_def.name}"

        return True, None
