"""Built-in ``rag_search`` tool.

Wraps the existing RagAgentService retrieval pipeline as a ToolDefinition
suitable for registration in the ToolRegistry.
"""

from __future__ import annotations

from typing import Any

from core.context.execution_context import ExecutionContext
from core.tool.base import ToolDefinition, ToolParam, ToolResult


def _build_rag_search_tool(
    rag_agent_service: Any,
) -> ToolDefinition:
    """Create the ``rag_search`` tool definition.

    Parameters
    ----------
    rag_agent_service:
        An instance of ``RagAgentService`` whose ``_build_retrieval_context``
        method performs the actual hybrid search + rerank.
    """

    def _handler(*, params: dict[str, Any], context: ExecutionContext) -> ToolResult:
        query: str = params.get("query", "").strip()
        if not query:
            return ToolResult(success=False, error="检索查询不能为空")

        try:
            ctx = rag_agent_service._build_retrieval_context(
                question=query,
                collection=rag_agent_service._resolve_kb_collection(context.kbid),
                top_k=6,
                rerank=True,
                is_kb=True,
                kbid=context.kbid,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"RAG 检索异常: {type(exc).__name__}: {exc}",
            )

        if not ctx.results:
            return ToolResult(
                success=True,
                data="未找到相关的视频内容。",
                cited_sources=[],
            )

        # Build a text block from the top retrieval results
        text_parts: list[str] = []
        for i, r in enumerate(ctx.results, start=1):
            snippet = getattr(r, "text", "") or ""
            if snippet:
                text_parts.append(f"[{i}] {snippet[:500]}")

        return ToolResult(
            success=True,
            data="\n\n".join(text_parts),
            cited_sources=ctx.cited_sources,
        )

    return ToolDefinition(
        name="rag_search",
        description="从知识库中检索视频转录内容，获取与查询相关的文本片段和来源引用",
        params=[
            ToolParam(
                name="query",
                type="string",
                description="检索查询语句，应包含用户问题的关键信息",
                required=True,
            ),
        ],
        required_permissions=["kb:read"],
        max_timeout_seconds=30.0,
        max_retries=1,
        handler=_handler,
    )
