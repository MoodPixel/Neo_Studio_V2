from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

from .library_schema import canonical_lora_identity, canonical_record_id, normalize_record, normalize_catalog_path, stable_record_id, utc_now_iso
from .catalog_bridge import attach_catalog_bridge

SCHEMA_VERSION = "neo.lora_stack.library.v2"


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
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "records": normalized}, indent=2), encoding="utf-8")
    temp.replace(path)


def _record_key(record: dict[str, Any]) -> str:
    normalized = normalize_record(record)
    return str(normalized.get("canonical_identity") or "").casefold()


def merge_catalog_records(
    saved: list[dict[str, Any]],
    catalog_loras: list[str],
    *,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> list[dict[str, Any]]:
    return attach_catalog_bridge(
        [normalize_record(item) for item in saved],
        catalog_loras or [],
        provider_id=provider_id,
        catalog_source=catalog_source,
        provider_label=provider_label,
    )


def upsert_record(root: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    incoming = normalize_record({**record, "updated": utc_now_iso()})
    records = load_records(root)
    if not incoming.get("id"):
        incoming["id"] = stable_record_id(incoming.get("file") or incoming.get("name"))
    incoming_identity = _record_key(incoming)
    id_conflict = next((item for item in records if item.get("id") == incoming.get("id") and _record_key(item) and _record_key(item) != incoming_identity), None)
    if id_conflict and incoming_identity:
        incoming["id"] = canonical_record_id(incoming.get("provider_id"), incoming.get("catalog_name"))
    replaced = False
    for index, existing in enumerate(records):
        same_id = existing.get("id") == incoming.get("id")
        same_identity = _record_key(existing) and _record_key(existing) == _record_key(incoming)
        if same_id or same_identity:
            incoming.setdefault("created", existing.get("created") or utc_now_iso())
            records[index] = normalize_record({**existing, **incoming})
            incoming = records[index]
            replaced = True
            break
    if not replaced:
        records.append(incoming)
    save_records(root, records)
    persisted = next((item for item in load_records(root) if item.get("id") == incoming.get("id")), None)
    if persisted is None:
        raise OSError("LoRA metadata save could not be verified on disk.")
    return persisted


def find_record(
    root: str | Path,
    record_id: str,
    *,
    catalog_loras: list[str] | None = None,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> dict[str, Any] | None:
    wanted_raw = str(record_id or "").strip()
    wanted = wanted_raw.casefold()
    if not wanted:
        return None
    wanted_aliases = {wanted}
    for prefix in ("comfy:", "forge:"):
        if wanted.startswith(prefix):
            wanted_aliases.add(wanted_raw[len(prefix):].strip().casefold())
    records = merge_catalog_records(
        load_records(root),
        catalog_loras or [],
        provider_id=provider_id,
        catalog_source=catalog_source,
        provider_label=provider_label,
    )
    matches = []
    for record in records:
        values = [record.get("id"), record.get("canonical_identity"), record.get("catalog_name")]
        if any(str(value or "").strip().casefold() in wanted_aliases for value in values):
            matches.append(normalize_record(record))
    return matches[0] if len(matches) == 1 else None


def audit_records(root: str | Path, *, provider_id: str, catalog_loras: list[str]) -> dict[str, Any]:
    """Preview conservative identity repairs without mutating the index."""
    provider = str(provider_id or "").strip().casefold()
    catalog = {normalize_catalog_path(name).casefold(): str(name) for name in catalog_loras if normalize_catalog_path(name)}
    basename_counts: dict[str, int] = {}
    for name in catalog:
        basename = Path(name).name.casefold()
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
    changes, ambiguous, unchanged = [], [], 0
    for record in load_records(root):
        path = normalize_catalog_path(record.get("catalog_name") or record.get("name"))
        key = path.casefold()
        current_provider = str(record.get("provider_id") or "").casefold()
        expected_identity = canonical_lora_identity(current_provider, path)
        if current_provider and expected_identity == record.get("canonical_identity") and record.get("id") == canonical_record_id(current_provider, path):
            unchanged += 1
            continue
        if key in catalog and (not current_provider or current_provider == provider):
            exact = normalize_catalog_path(catalog[key])
            changes.append({
                "old_id": record.get("id"), "new_id": canonical_record_id(provider, exact),
                "provider_id": provider, "catalog_name": exact,
                "canonical_identity": canonical_lora_identity(provider, exact),
            })
        else:
            reason = "catalog_path_not_found"
            if "/" not in path and basename_counts.get(Path(path).name.casefold(), 0) > 1:
                reason = "ambiguous_basename"
            ambiguous.append({"record_id": record.get("id"), "catalog_name": path, "reason": reason})
    material = json.dumps({"provider_id": provider, "changes": changes, "ambiguous": ambiguous}, sort_keys=True)
    return {
        "ok": True, "schema_version": "neo.lora_stack.identity_audit.v1", "mode": "preview",
        "preview_id": sha256(material.encode()).hexdigest()[:24], "change_count": len(changes),
        "ambiguous_count": len(ambiguous), "unchanged_count": unchanged,
        "changes": changes, "ambiguous": ambiguous, "destructive": False,
    }


def apply_identity_repairs(root: str | Path, *, provider_id: str, catalog_loras: list[str], preview_id: str) -> dict[str, Any]:
    preview = audit_records(root, provider_id=provider_id, catalog_loras=catalog_loras)
    if not preview_id or preview_id != preview["preview_id"]:
        return {"ok": False, "error": "Audit preview changed; request a fresh preview before applying."}
    path = library_index_path(root)
    backup = ""
    if path.exists():
        backup_path = path.with_name(f"library_index.backup-{utc_now_iso().replace(':', '')}.json")
        shutil.copy2(path, backup_path)
        backup = str(backup_path)
    changes = {item["old_id"]: item for item in preview["changes"]}
    repaired = []
    for record in load_records(root):
        change = changes.get(record.get("id"))
        if change:
            record.update({key: change[key] for key in ("provider_id", "catalog_name", "canonical_identity")})
            record["provider_catalog_name"] = change["catalog_name"]
            record["id"] = change["new_id"]
            record["updated"] = utc_now_iso()
        repaired.append(record)
    save_records(root, repaired)
    return {**preview, "mode": "applied", "backup_path": backup, "applied_count": len(changes)}


def delete_record(root: str | Path, record_id: str) -> dict[str, Any]:
    wanted = str(record_id or "").strip().casefold()
    records = load_records(root)
    kept = [record for record in records if str(record.get("id") or "").casefold() != wanted]
    save_records(root, kept)
    return {"ok": len(kept) != len(records), "deleted": len(kept) != len(records), "record_id": record_id}
