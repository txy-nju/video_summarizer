"""Unit tests for ToolRegistry."""

from __future__ import annotations

import pytest

from core.tool.base import ToolDefinition, ToolParam, ToolResult
from core.tool.registry import ToolRegistry


def _dummy_handler(*, params, context):
    return ToolResult(success=True, data=params.get("query", ""))


def _make_search_tool():
    return ToolDefinition(
        name="rag_search",
        description="搜索知识库",
        params=[
            ToolParam(name="query", type="string", description="查询语句", required=True),
        ],
        required_permissions=["kb:read"],
        max_timeout_seconds=30.0,
        max_retries=1,
        handler=_dummy_handler,
    )


class TestToolRegistry:
    """ToolRegistry — registration, lookup, validation, prompt generation."""

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    def test_register_and_get(self, registry):
        tool = _make_search_tool()
        registry.register(tool)
        assert registry.get("rag_search") is tool

    def test_get_missing_returns_none(self, registry):
        assert registry.get("nonexistent") is None

    def test_register_overwrites_with_warning(self, registry):
        t1 = _make_search_tool()
        t2 = ToolDefinition(
            name="rag_search",
            description="新版搜索",
            params=[],
            handler=_dummy_handler,
        )
        registry.register(t1)
        registry.register(t2)
        assert registry.get("rag_search") is t2

    def test_list_all(self, registry):
        assert len(registry.list_all()) == 0
        registry.register(_make_search_tool())
        assert len(registry.list_all()) == 1

    def test_list_for_llm_single_tool(self, registry):
        registry.register(_make_search_tool())
        prompt = registry.list_for_llm()
        assert "rag_search" in prompt
        assert "query" in prompt
        assert "搜索知识库" in prompt

    def test_list_for_llm_empty_registry(self, registry):
        prompt = registry.list_for_llm()
        assert "没有可用的工具" in prompt

    def test_list_for_llm_optional_param(self, registry):
        tool = ToolDefinition(
            name="search",
            description="搜索",
            params=[
                ToolParam(name="query", type="string", required=True),
                ToolParam(name="limit", type="integer", required=False, default=5),
            ],
            handler=_dummy_handler,
        )
        registry.register(tool)
        prompt = registry.list_for_llm()
        assert "可选" in prompt
        assert "limit" in prompt

    def test_validate_params_valid(self, registry):
        registry.register(_make_search_tool())
        valid, error = registry.validate_params("rag_search", {"query": "test"})
        assert valid
        assert error is None

    def test_validate_params_missing_required(self, registry):
        registry.register(_make_search_tool())
        valid, error = registry.validate_params("rag_search", {})
        assert not valid
        assert "query" in error

    def test_validate_params_unknown_tool(self, registry):
        valid, error = registry.validate_params("unknown", {"x": 1})
        assert not valid
        assert "unknown" in error
