from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .library_schema import normalize_record, stable_record_id, utc_now_iso
from .catalog_bridge import attach_catalog_bridge

SCHEMA_VERSION = "neo.lora_stack.library.v1"


def library_data_dir(root: str | Path) -> Path:
    return Path(root) / "neo_data" / "extensions" / "lora_stack"


def library_index_path(root: str | Path) -> Path:
    return library_data_dir(root) / "library_index.json"


def load_records(root: str | Path) -> list[dict[str, Any]]:
    path = library_index_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [normalize_record(item) for item in data.get("records", []) if isinstance(item, dict)]


def save_records(root: str | Path, records: list[dict[str, Any]]) -> None:
    path = library_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [normalize_record(item) for item in records]
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "records": normalized}, indent=2), encoding="utf-8")


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("catalog_name") or record.get("name") or record.get("file") or record.get("id") or "").casefold()


def merge_catalog_records(saved: list[dict[str, Any]], catalog_loras: list[str]) -> list[dict[str, Any]]:
    return attach_catalog_bridge([normalize_record(item) for item in saved], catalog_loras or [])


def upsert_record(root: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    incoming = normalize_record({**record, "updated": utc_now_iso()})
    records = load_records(root)
    if not incoming.get("id"):
        incoming["id"] = stable_record_id(incoming.get("file") or incoming.get("name"))
    replaced = False
    for index, existing in enumerate(records):
        same_id = existing.get("id") == incoming.get("id")
        same_name = _record_key(existing) and _record_key(existing) == _record_key(incoming)
        if same_id or same_name:
            incoming.setdefault("created", existing.get("created") or utc_now_iso())
            records[index] = normalize_record({**existing, **incoming})
            incoming = records[index]
            replaced = True
            break
    if not replaced:
        records.append(incoming)
    save_records(root, records)
    return incoming


def find_record(root: str | Path, record_id: str, *, catalog_loras: list[str] | None = None) -> dict[str, Any] | None:
    wanted_raw = str(record_id or "").strip()
    wanted = wanted_raw.casefold()
    if not wanted:
        return None
    wanted_aliases = {wanted}
    if wanted.startswith("comfy:"):
        wanted_aliases.add(wanted_raw[6:].strip().casefold())
    records = merge_catalog_records(load_records(root), catalog_loras or [])
    for record in records:
        values = [record.get("id"), record.get("name"), record.get("catalog_name"), record.get("file")]
        if any(str(value or "").strip().casefold() in wanted_aliases for value in values):
            return normalize_record(record)
    return None


def delete_record(root: str | Path, record_id: str) -> dict[str, Any]:
    wanted = str(record_id or "").strip().casefold()
    records = load_records(root)
    kept = [record for record in records if str(record.get("id") or "").casefold() != wanted]
    save_records(root, kept)
    return {"ok": len(kept) != len(records), "deleted": len(kept) != len(records), "record_id": record_id}
