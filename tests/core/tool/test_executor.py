"""Unit tests for ToolExecutor — safe tool execution sandbox."""

from __future__ import annotations

import time

import pytest

from core.context.execution_context import ExecutionContext
from core.tool.base import ToolDefinition, ToolParam, ToolResult
from core.tool.executor import ToolExecutor
from core.tool.registry import ToolRegistry


def _make_tool(*, name="echo", required_permissions=None, handler=None, max_timeout=5.0, max_retries=0):
    return ToolDefinition(
        name=name,
        description="Echo tool",
        params=[ToolParam(name="text", type="string", required=True)],
        required_permissions=required_permissions or [],
        max_timeout_seconds=max_timeout,
        max_retries=max_retries,
        handler=handler or (lambda *, params, context: ToolResult(success=True, data=params["text"])),
    )


class TestToolExecutor:
    """ToolExecutor — validation, permissions, error isolation."""

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    @pytest.fixture
    def ctx(self):
        return ExecutionContext(owner_id="user1", kbid="kb1")

    def test_execute_success(self, registry, ctx):
        registry.register(_make_tool())
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "hello"}, context=ctx)
        assert result.success
        assert result.data == "hello"

    def test_execute_unknown_tool(self, registry, ctx):
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="unknown", params={}, context=ctx)
        assert not result.success
        assert "unknown" in result.error

    def test_execute_missing_required_param(self, registry, ctx):
        registry.register(_make_tool())
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={}, context=ctx)
        assert not result.success
        assert "text" in result.error

    def test_execute_permission_denied(self, registry, ctx):
        tool = _make_tool(required_permissions=["kb:write"])
        registry.register(tool)
        # Permission checker that always denies
        executor = ToolExecutor(registry, permission_checker=lambda *a, **kw: False)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert not result.success
        assert "权限不足" in result.error

    def test_execute_permission_allowed(self, registry, ctx):
        tool = _make_tool(required_permissions=["kb:read"])
        registry.register(tool)
        executor = ToolExecutor(registry, permission_checker=lambda *a, **kw: True)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert result.success

    def test_execute_no_permission_checker_allows(self, registry, ctx):
        """When no permission_checker is configured, all tools are allowed."""
        tool = _make_tool(required_permissions=["kb:read"])
        registry.register(tool)
        executor = ToolExecutor(registry)  # no checker
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert result.success

    def test_execute_handler_exception_is_caught(self, registry, ctx):
        def _failing(*, params, context):
            raise RuntimeError("BOOM")

        tool = _make_tool(handler=_failing)
        registry.register(tool)
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert not result.success
        assert "BOOM" in result.error

    def test_execute_auto_wraps_raw_return(self, registry, ctx):
        def _raw(*, params, context):
            return "raw_string"

        tool = _make_tool(handler=_raw)
        registry.register(tool)
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert result.success
        assert result.data == "raw_string"

    def test_execute_retries_transient_errors(self, registry, ctx):
        call_count = [0]

        def _sometimes_fail(*, params, context):
            call_count[0] += 1
            if call_count[0] < 2:
                return ToolResult(success=False, error="connection timeout")
            return ToolResult(success=True, data="ok")

        tool = _make_tool(handler=_sometimes_fail, max_retries=2)
        registry.register(tool)
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert result.success
        assert call_count[0] == 2  # First fails, retry succeeds

    def test_execute_no_retry_for_non_transient(self, registry, ctx):
        call_count = [0]

        def _validation_fail(*, params, context):
            call_count[0] += 1
            return ToolResult(success=False, error="invalid input format")

        tool = _make_tool(handler=_validation_fail, max_retries=2)
        registry.register(tool)
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert not result.success
        assert call_count[0] == 1  # Non-transient → no retry

    def test_execute_cited_sources_propagation(self, registry, ctx):
        def _rag(*, params, context):
            return ToolResult(
                success=True,
                data="results",
                cited_sources=[{"video_id": "v1", "quote": "text"}],
            )

        tool = _make_tool(handler=_rag)
        registry.register(tool)
        executor = ToolExecutor(registry)
        result = executor.execute(tool_name="echo", params={"text": "x"}, context=ctx)
        assert result.success
        assert len(result.cited_sources) == 1
        assert result.cited_sources[0]["video_id"] == "v1"
