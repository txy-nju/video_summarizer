"""ExecutionContext — runtime identity and scope for tool execution.

Passed to ToolExecutor so that every tool invocation carries the
caller's identity, the target knowledge base, and a request trace ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    attachments:
        User-uploaded image attachments (mime_type, frame_path).
        Default empty list; backward-compatible with all existing callers.
    frames:
        System-retrieved keyframes (frame_path, time_range, video_id).
        Populated by RAG retrieval or checkpoint time-travel extraction.
        Default empty list; backward-compatible with all existing callers.
    """

    owner_id: str
    kbid: str
    trace_id: str = ""
    attachments: list[dict] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)
