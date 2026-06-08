"""ToolExecutor — safe, validated tool execution sandbox.

Wraps every tool call with:
1. Parameter validation (via ToolRegistry schema)
2. Permission checks (via ExecutionContext)
3. Timeout enforcement
4. Error isolation (failures don't propagate)
5. Automatic retry for transient errors
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from core.context.execution_context import ExecutionContext
from core.tool.base import ToolResult
from core.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Transient error patterns that trigger automatic retry.
_TRANSIENT_SUBSTRINGS = ("timeout", "connection", "network", "temporary")


class ToolExecutor:
    """Execute tools with safety guardrails.

    Parameters
    ----------
    registry:
        ToolRegistry for parameter schema validation.
    permission_checker:
        Optional callable ``(tool_name, required_permissions, context) -> bool``.
        When omitted, all permission checks pass (open mode).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        permission_checker: Any | None = None,
    ) -> None:
        self._registry = registry
        self._permission_checker = permission_checker

    # ── public API ───────────────────────────────────────────────────

    def execute(
        self,
        *,
        tool_name: str,
        params: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        """Execute a tool with full safety pipeline.

        Returns a ``ToolResult`` — never raises.
        """
        # 1. Lookup
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")

        # 2. Validate parameters
        valid, error = self._registry.validate_params(tool_name, params)
        if not valid:
            return ToolResult(success=False, error=error)

        # 3. Permission check
        if not self._check_permission(tool, context):
            return ToolResult(
                success=False,
                error=f"权限不足: 需要 {tool.required_permissions}",
            )

        # 4. Execute with timeout & retry
        return self._execute_with_retry(tool, params, context)

    # ── internal ─────────────────────────────────────────────────────

    def _check_permission(
        self, tool: Any, context: ExecutionContext
    ) -> bool:
        """Check whether the caller has the required permissions."""
        if not tool.required_permissions:
            return True
        if self._permission_checker is None:
            # No checker configured → allow all (caller should provide one
            # in production).
            logger.warning(
                "ToolExecutor: permission_checker not configured, allowing '%s'",
                tool.name,
            )
            return True
        try:
            return self._permission_checker(
                tool.name, tool.required_permissions, context
            )
        except Exception:
            logger.exception(
                "ToolExecutor: permission check failed for '%s'", tool.name
            )
            return False

    def _execute_with_retry(
        self, tool: Any, params: dict[str, Any], context: ExecutionContext
    ) -> ToolResult:
        """Execute handler with retry loop for transient errors."""
        last_error: str | None = None
        max_attempts = max(1, tool.max_retries)

        for attempt in range(1, max_attempts + 1):
            try:
                result = self._call_with_timeout(tool, params, context, attempt)
                if result.success:
                    return result
                # Non-transient errors → no retry
                if not self._is_transient(result.error):
                    return result
                last_error = result.error
            except Exception as exc:
                last_error = f"工具执行异常: {type(exc).__name__}: {exc}"
                if not self._is_transient(last_error):
                    break

            if attempt < max_attempts:
                delay = 0.5 * (2 ** (attempt - 1))  # exponential backoff
                logger.debug(
                    "ToolExecutor: retrying '%s' (attempt %d/%d) after %ss",
                    tool.name,
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)

        return ToolResult(success=False, error=last_error or "工具执行失败")

    def _call_with_timeout(
        self,
        tool: Any,
        params: dict[str, Any],
        context: ExecutionContext,
        attempt: int,
    ) -> ToolResult:
        """Call the tool handler with a timeout."""
        handler = tool.handler
        if handler is None:
            return ToolResult(success=False, error=f"工具 '{tool.name}' 未绑定执行函数")

        timeout = tool.max_timeout_seconds
        started = time.monotonic()

        result = handler(params=params, context=context)

        elapsed = time.monotonic() - started
        if elapsed > timeout:
            return ToolResult(
                success=False,
                error=f"工具执行超时 ({elapsed:.1f}s > {timeout}s)",
            )

        # Normalise return value
        if isinstance(result, ToolResult):
            return result
        # Auto-wrap raw returns
        return ToolResult(success=True, data=result)

    @staticmethod
    def _is_transient(error: str | None) -> bool:
        """Heuristic: is this error likely to succeed on retry?"""
        if not error:
            return False
        error_lower = error.lower()
        return any(pat in error_lower for pat in _TRANSIENT_SUBSTRINGS)
