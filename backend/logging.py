import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict


_SENSITIVE_FIELDS = {"password", "token", "authorization", "access_token", "refresh_token"}


def _safe_extra(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in _SENSITIVE_FIELDS else _safe_extra(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_safe_extra(item) for item in value]
    return value


class JsonLogFormatter(logging.Formatter):
    """Emit one-line JSON logs with stable trace fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
            "path": getattr(record, "path", "-"),
            "method": getattr(record, "method", "-"),
            "status_code": getattr(record, "status_code", 0),
            "duration_ms": getattr(record, "duration_ms", 0),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        payload = _safe_extra(payload)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level.upper())

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root_logger.addHandler(handler)
