"""ExecutionContext — runtime identity and scope for tool execution.

Passed to ToolExecutor so that every tool invocation carries the
caller's identity, the target knowledge base, and a request trace ID.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable runtime context for a single tool execution.

    Attributes
    ----------
    owner_id:
        The authenticated user who initiated the request.
    kbid:
        The knowledge base against which the tool operates.
    trace_id:
        Request-scoped identifier for log correlation (optional).
    """

    owner_id: str
    kbid: str
    trace_id: str = ""
