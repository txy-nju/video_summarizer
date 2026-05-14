from __future__ import annotations

from collections.abc import Iterable


def parse_fields(fields: str | None, allowed_fields: Iterable[str]) -> list[str] | None:
    if not fields:
        return None

    requested = [item.strip() for item in fields.split(",") if item.strip()]
    if not requested:
        return None

    allowed = set(allowed_fields)
    invalid = [field for field in requested if field not in allowed]
    if invalid:
        raise ValueError(f"Unsupported fields requested: {', '.join(invalid)}")

    return requested
