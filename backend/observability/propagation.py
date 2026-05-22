from __future__ import annotations

import re
from typing import Mapping

TRACE_HEADER_NAMES = ("traceparent", "tracestate", "baggage")
_TRACEPARENT_RE = re.compile(r"^[\da-f]{2}-([\da-f]{32})-([\da-f]{16})-[\da-f]{2}$", re.IGNORECASE)


def extract_trace_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Extract W3C trace propagation headers from an arbitrary header mapping."""
    extracted: dict[str, str] = {}
    for header_name in TRACE_HEADER_NAMES:
        value = headers.get(header_name) or headers.get(header_name.title())
        if value:
            extracted[header_name] = value
    return extracted


def extract_trace_id_from_traceparent(traceparent: str) -> str | None:
    match = _TRACEPARENT_RE.match((traceparent or "").strip())
    if not match:
        return None
    return match.group(1).lower()


def build_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    flags = "01" if sampled else "00"
    return f"00-{trace_id.lower()}-{span_id.lower()}-{flags}"


def normalize_trace_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep only allowed W3C headers for transport propagation."""
    extracted = extract_trace_headers(headers)
    normalized: dict[str, str] = {}
    for key, value in extracted.items():
        normalized[key] = value.strip()
    return normalized
