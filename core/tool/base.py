"""Tool system base types — self-describing tool definitions and results.

Each tool declares its name, parameter schema, required permissions, and
execution constraints. The ToolRegistry and ToolExecutor consume these
definitions to provide safe, validated tool execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ToolParam:
    """A single parameter accepted by a tool.

    Attributes
    ----------
    name:
        Parameter name (e.g. ``"query"``).
    type:
        JSON-like type name: ``"string"``, ``"integer"``, ``"boolean"``.
    description:
        Human-readable description for the LLM prompt.
    required:
        Whether the parameter is mandatory.
    default:
        Default value when the parameter is omitted (only meaningful
        when ``required=False``).
    """

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Complete self-description of a tool.

    Attributes
    ----------
    name:
        Unique identifier (e.g. ``"rag_search"``).
    description:
        Natural-language description injected into the LLM prompt.
    params:
        Ordered list of accepted parameters.
    required_permissions:
        Permission strings the caller must possess (e.g. ``["kb:read"]``).
    max_timeout_seconds:
        Execution deadline; the executor cancels the tool if exceeded.
    max_retries:
        Number of automatic retries for transient failures.
    handler:
        Callable that performs the actual work. Signature:
        ``handler(params: dict, context: ExecutionContext) -> ToolResult``
    """

    name: str
    description: str
    params: list[ToolParam] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    max_timeout_seconds: float = 30.0
    max_retries: int = 1
    handler: Callable[..., Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Unified result from a tool execution.

    Attributes
    ----------
    success:
        ``True`` if the tool completed without error.
    data:
        Arbitrary payload returned by the tool (may be ``None``).
    error:
        Human-readable error description when ``success=False``.
    cited_sources:
        Optional citation list (populated by RAG tools).
    """

    success: bool
    data: Any = None
    error: str | None = None
    cited_sources: list[dict] = field(default_factory=list)
