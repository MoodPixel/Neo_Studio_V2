from __future__ import annotations

from typing import Any


def mark_source(record: dict[str, Any], field: str, source: str) -> dict[str, Any]:
    record.setdefault("field_sources", {})[field] = source
    return record


def mark_sources(record: dict[str, Any], fields: list[str], source: str) -> dict[str, Any]:
    for field in fields:
        if record.get(field) not in (None, "", []):
            mark_source(record, field, source)
    return record


def merge_field_sources(existing: dict[str, Any], incoming: dict[str, Any], source: str, fields: list[str] | None = None) -> dict[str, str]:
    merged = dict(existing.get("field_sources") or {})
    source_map = incoming.get("field_sources") if isinstance(incoming.get("field_sources"), dict) else {}
    for field in fields or list(incoming.keys()):
        if field == "field_sources":
            continue
        if incoming.get(field) not in (None, "", []):
            merged[field] = str(source_map.get(field) or source)
    return merged
