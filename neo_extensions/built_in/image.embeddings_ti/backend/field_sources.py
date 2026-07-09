from __future__ import annotations

from typing import Any


def mark_source(record: dict[str, Any], field: str, source: str) -> dict[str, Any]:
    record.setdefault("field_sources", {})[field] = source
    return record


def merge_field_sources(existing: dict[str, Any], incoming: dict[str, Any], source: str, fields: list[str] | None = None) -> dict[str, str]:
    merged = dict(existing.get("field_sources") or {})
    for field in fields or list(incoming.keys()):
        if field != "field_sources" and incoming.get(field) not in (None, "", []):
            merged[field] = source
    return merged
