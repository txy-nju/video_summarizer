"""Agent stream events — structured progress phases emitted during agent execution.

These events are yielded alongside text tokens from ``answer_stream()`` methods.
SSE routes convert them to ``progress`` SSE events; non-streaming consumers filter them out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProgressEvent:
    """Emitted by agents during streaming to report progress phases.

    Attributes
    ----------
    phase:
        Fixed label for the current phase. Standard values:
        ``"thinking"``, ``"searching"``, ``"retrieved"``, ``"loading"``, ``"generating"``.
    message:
        Human-readable status text for frontend display.
    """

    phase: str
    message: str
