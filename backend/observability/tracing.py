from __future__ import annotations

import logging
import os
import secrets
from contextlib import contextmanager
from typing import Any, Iterator

from backend.observability.propagation import build_traceparent

try:
    from opentelemetry import trace as otel_trace  # type: ignore
    from opentelemetry.sdk.resources import Resource  # type: ignore
    from opentelemetry.sdk.trace import sampling  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    otel_trace = None
    Resource = None
    sampling = None
    TracerProvider = None
    BatchSpanProcessor = None
    _OTEL_AVAILABLE = False

try:
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    JaegerExporter = None

try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OTLPSpanExporter = None


_CONFIGURED = False
_LOGGER = logging.getLogger(__name__)


def build_span_name(domain: str, stage: str, action: str) -> str:
    return f"{domain}.{stage}.{action}"


def normalize_trace_id(raw_trace_id: str) -> str:
    compact = "".join(ch for ch in (raw_trace_id or "") if ch.lower() in "0123456789abcdef").lower()
    if len(compact) >= 32:
        return compact[:32]
    if len(compact) > 0:
        return compact.rjust(32, "0")
    return secrets.token_hex(16)


def generate_span_id() -> str:
    return secrets.token_hex(8)


def _normalize_sample_ratio(sample_ratio: float) -> float:
    return max(0.0, min(float(sample_ratio), 1.0))


def configure_tracing(
    *,
    enabled: bool = False,
    service_name: str = "video-summarizer-backend",
    exporter: str = "jaeger",
    sample_ratio: float = 1.0,
    jaeger_endpoint: str = "",
    otlp_endpoint: str = "",
) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    if not enabled:
        _LOGGER.info("otel_tracing_disabled")
        _CONFIGURED = True
        return

    if not _OTEL_AVAILABLE:
        _LOGGER.warning("otel_not_available_skip_configure")
        _CONFIGURED = True
        return

    if otel_trace is None or TracerProvider is None or Resource is None or sampling is None:
        _LOGGER.warning("otel_sdk_incomplete_skip_configure")
        _CONFIGURED = True
        return

    ratio = _normalize_sample_ratio(sample_ratio)
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=sampling.TraceIdRatioBased(ratio),
    )

    exporter_name = (exporter or "").strip().lower()
    span_exporter = None

    if exporter_name == "jaeger":
        if JaegerExporter is not None:
            span_exporter = JaegerExporter(collector_endpoint=jaeger_endpoint or None)
        elif OTLPSpanExporter is not None:
            # Fallback path: use OTLP to Jaeger collector when dedicated Jaeger exporter is unavailable.
            span_exporter = OTLPSpanExporter(endpoint=jaeger_endpoint or None)
        else:
            _LOGGER.warning("jaeger_exporter_unavailable")
    elif exporter_name == "otlp":
        if OTLPSpanExporter is not None:
            span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint or None)
        else:
            _LOGGER.warning("otlp_exporter_unavailable")
    else:
        _LOGGER.warning("unsupported_otel_exporter", extra={"otel_exporter": exporter_name})

    if span_exporter is not None and BatchSpanProcessor is not None:
        provider.add_span_processor(BatchSpanProcessor(span_exporter))

    otel_trace.set_tracer_provider(provider)
    _CONFIGURED = True


@contextmanager
def start_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Start span when OpenTelemetry is available, otherwise behave as a no-op context manager."""
    attrs = attributes or {}
    if _OTEL_AVAILABLE and otel_trace is not None:
        tracer = otel_trace.get_tracer("video_summarizer.observability")
        with tracer.start_as_current_span(name) as span:
            for key, value in attrs.items():
                if value is not None:
                    span.set_attribute(key, value)
            yield {"span_enabled": True}
        return

    yield {"span_enabled": False}


def make_http_trace_headers(trace_id: str) -> dict[str, str]:
    span_id = generate_span_id()
    return {
        "traceparent": build_traceparent(normalize_trace_id(trace_id), span_id, sampled=True),
    }


def get_trace_sampling_ratio() -> float:
    raw = os.getenv("OTEL_SAMPLE_RATIO") or os.getenv("OTEL_TRACE_SAMPLING_RATIO", "0.1")
    try:
        ratio = float(raw)
    except ValueError:
        return 0.1
    return max(0.0, min(ratio, 1.0))
