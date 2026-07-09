from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .library_schema import SCHEMA_VERSION, normalize_record, record_from_catalog_name, stable_record_id, utc_now_iso

DEFAULT_STORE_NAME = "library_index.json"


def library_data_dir(root: str | Path) -> Path:
    return Path(root) / "neo_data" / "extensions" / "embeddings_ti"


def library_index_path(root: str | Path) -> Path:
    path = library_data_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path / DEFAULT_STORE_NAME


def store_path(root: str | Path) -> Path:
    return library_index_path(root)


def load_records(root: str | Path) -> list[dict[str, Any]]:
    path = library_index_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = data.get("records") if isinstance(data, dict) else data
    return [normalize_record(item) for item in records or [] if isinstance(item, dict)]


def save_records(root: str | Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = library_index_path(root)
    normalized = [normalize_record(item) for item in records if isinstance(item, dict)]
    normalized.sort(key=lambda item: str(item.get("name") or item.get("token") or "").casefold())
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "records": normalized}, indent=2), encoding="utf-8")
    return normalized


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("token") or record.get("catalog_name") or record.get("name") or record.get("file") or record.get("id") or "").casefold()


def merge_catalog_records(saved: list[dict[str, Any]], catalog_embeddings: list[str]) -> list[dict[str, Any]]:
    records = [normalize_record(item) for item in saved]
    by_key = {_record_key(item): item for item in records if _record_key(item)}
    for name in catalog_embeddings or []:
        incoming = record_from_catalog_name(name)
        key = _record_key(incoming)
        existing = by_key.get(key)
        if existing:
            existing["catalog_available"] = True
            existing["catalog_source"] = "provider:embeddings"
            existing.setdefault("field_sources", {})["catalog_available"] = "provider:embeddings"
        else:
            records.append(incoming)
            by_key[key] = incoming
    return [normalize_record(item) for item in records]


def upsert_record(root: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    incoming = normalize_record({**record, "updated": utc_now_iso()})
    if not incoming.get("id"):
        incoming["id"] = stable_record_id(incoming.get("file") or incoming.get("token") or incoming.get("name"))
    records = load_records(root)
    replaced = False
    incoming_key = _record_key(incoming)
    for index, existing in enumerate(records):
        same_id = existing.get("id") == incoming.get("id")
        same_key = incoming_key and _record_key(existing) == incoming_key
        if same_id or same_key:
            incoming.setdefault("created", existing.get("created") or utc_now_iso())
            records[index] = normalize_record({**existing, **incoming})
            incoming = records[index]
            replaced = True
            break
    if not replaced:
        records.append(incoming)
    save_records(root, records)
    return incoming


def find_record(root: str | Path, record_id: str, *, catalog_embeddings: list[str] | None = None) -> dict[str, Any] | None:
    wanted_raw = str(record_id or "").strip()
    wanted = wanted_raw.casefold()
    if not wanted:
        return None
    aliases = {wanted}
    if wanted.startswith("embedding:"):
        aliases.add(wanted.replace("embedding:", "", 1))
    records = merge_catalog_records(load_records(root), catalog_embeddings or [])
    for record in records:
        values = [record.get("id"), record.get("name"), record.get("catalog_name"), record.get("token"), record.get("file")]
        normalized_values = {str(value or "").strip().casefold() for value in values if str(value or "").strip()}
        normalized_values |= {str(value or "").replace("embedding:", "", 1).strip().casefold() for value in values if str(value or "").startswith("embedding:")}
        if aliases & normalized_values:
            return normalize_record(record)
    return None


def delete_record(root: str | Path, record_id: str) -> dict[str, Any]:
    wanted = str(record_id or "").strip().casefold()
    records = load_records(root)
    kept = [record for record in records if str(record.get("id") or "").casefold() != wanted]
    save_records(root, kept)
    return {"ok": len(kept) != len(records), "deleted": len(kept) != len(records), "record_id": record_id}
