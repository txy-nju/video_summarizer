from __future__ import annotations

from math import ceil


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def normalize_page_size(page_size: int) -> int:
    if page_size < 1:
        return DEFAULT_PAGE_SIZE
    return min(page_size, MAX_PAGE_SIZE)


def build_pagination(*, page: int, page_size: int, total: int, next_cursor: str | None = None) -> dict:
    normalized_page = max(page, 1)
    normalized_size = normalize_page_size(page_size)
    total_pages = ceil(total / normalized_size) if total > 0 else 0

    return {
        "page": normalized_page,
        "page_size": normalized_size,
        "total": total,
        "has_next": normalized_page < total_pages,
        "next_cursor": next_cursor,
    }
