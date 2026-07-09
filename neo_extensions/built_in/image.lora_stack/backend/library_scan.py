from __future__ import annotations

from pathlib import Path
from typing import Any

from .library_schema import empty_lora_record, normalize_record, record_from_comfy_lora_name, stable_record_id
from .library_store import load_records, merge_catalog_records, save_records
from .metadata_reader import infer_defaults_from_metadata, read_safetensors_metadata
from .local_lora_paths import lora_path_resolution_payload, resolve_lora_file_path


def scan_lora_folder(folder: str | Path) -> dict[str, Any]:
    root = Path(folder)
    if not root.exists():
        return {"ok": False, "errors": ["LoRA folder does not exist."], "records": []}
    records = []
    for file_path in sorted(root.rglob("*.safetensors")):
        metadata_result = read_safetensors_metadata(file_path)
        defaults = infer_defaults_from_metadata(metadata_result.get("metadata", {})) if metadata_result.get("ok") else {"metadata_status": "unreadable"}
        record = empty_lora_record(stable_record_id(str(file_path)))
        record.update({"file": str(file_path), "rel": str(file_path.relative_to(root)), "name": file_path.name, "catalog_name": file_path.name, "source": "local_scan"})
        record.update(defaults)
        records.append(normalize_record(record))
    return {"ok": True, "errors": [], "records": records, "count": len(records), "source": "local_folder"}


def _hydrate_catalog_record(root: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    record = normalize_record(record)
    resolved = resolve_lora_file_path(root, str(record.get("catalog_name") or record.get("name") or record.get("file") or ""))
    if not resolved:
        record["metadata_resolution"] = lora_path_resolution_payload(root, str(record.get("catalog_name") or record.get("name") or ""))
        if record.get("metadata_status") == "catalog_only":
            record["metadata_status"] = "path_unresolved"
        return record
    record["file"] = str(resolved)
    record.setdefault("field_sources", {})["file"] = "local:path_resolver"
    metadata_result = read_safetensors_metadata(resolved)
    if not metadata_result.get("ok"):
        record["metadata_status"] = "unreadable"
        return record
    defaults = infer_defaults_from_metadata(metadata_result.get("metadata", {}))
    for key, value in defaults.items():
        if key == "field_sources":
            record.setdefault("field_sources", {}).update(value)
        elif value and (not record.get(key) or str(record.get(key)).strip().casefold() in {"base unknown", "unknown"}):
            record[key] = value
    record["metadata_status"] = defaults.get("metadata_status") or "readable"
    record["metadata_resolution"] = lora_path_resolution_payload(root, str(record.get("catalog_name") or record.get("name") or ""))
    return normalize_record(record)


def scan_comfy_lora_catalog(root: str | Path, catalog_loras: list[str]) -> dict[str, Any]:
    saved = load_records(root)
    before = len(saved)
    merged = merge_catalog_records(saved, catalog_loras)
    merged = [_hydrate_catalog_record(root, item) for item in merged]
    # Only persist records that are no longer pure transient duplicates; catalog-only records are useful because CivitAI/manual enrichment can attach to them.
    save_records(root, merged)
    return {
        "ok": True,
        "source": "comfy_lora_loader",
        "count": len(merged),
        "added": max(0, len(merged) - before),
        "records": merged,
        "errors": [],
    }
