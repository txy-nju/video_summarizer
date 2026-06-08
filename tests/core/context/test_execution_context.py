"""Unit tests for ExecutionContext."""

from __future__ import annotations

import pytest

from core.context.execution_context import ExecutionContext


class TestExecutionContext:
    def test_basic_creation(self):
        ctx = ExecutionContext(owner_id="user1", kbid="kb1")
        assert ctx.owner_id == "user1"
        assert ctx.kbid == "kb1"
        assert ctx.trace_id == ""

    def test_with_trace_id(self):
        ctx = ExecutionContext(owner_id="u1", kbid="k1", trace_id="trace-123")
        assert ctx.trace_id == "trace-123"

    def test_frozen_immutable(self):
        ctx = ExecutionContext(owner_id="u1", kbid="k1")
        with pytest.raises(Exception):
            ctx.owner_id = "new"  # type: ignore
