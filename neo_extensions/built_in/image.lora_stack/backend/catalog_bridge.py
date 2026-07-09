from __future__ import annotations

from pathlib import Path
from typing import Any

from .library_schema import normalize_record, record_from_comfy_lora_name, stable_record_id


def normalize_lora_catalog_name(value: Any) -> str:
    """Return the Comfy-facing LoRA choice exactly enough for LoraLoader."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("catalog_name") or value.get("file") or value.get("id")
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def lora_catalog_match_keys(value: Any) -> set[str]:
    """Keys used to match saved records to Comfy LoraLoader names without changing the actual loader value."""
    name = normalize_lora_catalog_name(value)
    if not name:
        return set()
    path = Path(name)
    keys = {name.casefold(), path.name.casefold(), path.stem.casefold()}
    # Also match extensionless relative path, because some saved records lose the suffix.
    if path.suffix:
        keys.add(str(path.with_suffix("")).replace("\\", "/").casefold())
    return {item for item in keys if item}


def catalog_names_from_models(models_payload: Any) -> list[str]:
    if not models_payload:
        return []
    records: list[Any] = []
    if isinstance(models_payload, dict):
        if isinstance(models_payload.get("loras"), list):
            records = models_payload.get("loras") or []
        elif isinstance(models_payload.get("models"), list):
            records = [item for item in models_payload.get("models") or [] if isinstance(item, dict) and item.get("kind") in {"lora", "loras"}]
    elif isinstance(models_payload, list):
        records = [item for item in models_payload if isinstance(item, dict) and item.get("kind") in {"lora", "loras"}]
    out: list[str] = []
    seen: set[str] = set()
    for item in records:
        name = normalize_lora_catalog_name(item)
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def catalog_records_from_names(catalog_loras: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in catalog_loras or []:
        name = normalize_lora_catalog_name(raw)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        record = record_from_comfy_lora_name(name)
        record["catalog_available"] = True
        record["catalog_match_keys"] = sorted(lora_catalog_match_keys(name))
        records.append(record)
    return records


def _record_catalog_keys(record: dict[str, Any]) -> set[str]:
    normalized = normalize_record(record)
    keys: set[str] = set()
    for value in [normalized.get("catalog_name"), normalized.get("name"), normalized.get("file"), normalized.get("id")]:
        keys.update(lora_catalog_match_keys(value))
    return keys


def attach_catalog_bridge(records: list[dict[str, Any]], catalog_loras: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    """Mark saved records as available/missing against Comfy's LoraLoader catalog.

    Saved metadata stays authoritative. The Comfy catalog name remains the value that will be sent
    to LoraLoader. Missing saved records remain visible but are marked unavailable so UI can avoid
    pretending they can be applied.
    """
    catalog = catalog_records_from_names(catalog_loras)
    catalog_by_key: dict[str, dict[str, Any]] = {}
    for item in catalog:
        for key in item.get("catalog_match_keys") or []:
            catalog_by_key[key] = item

    out: list[dict[str, Any]] = []
    seen_catalog_names: set[str] = set()
    for raw in records or []:
        record = normalize_record(raw)
        match = None
        for key in _record_catalog_keys(record):
            if key in catalog_by_key:
                match = catalog_by_key[key]
                break
        if match:
            record["catalog_available"] = True
            record["catalog_name"] = match.get("catalog_name") or match.get("name") or record.get("catalog_name")
            record["catalog_source"] = "comfy:LoraLoader.lora_name"
            seen_catalog_names.add(str(record.get("catalog_name") or "").casefold())
        else:
            record["catalog_available"] = False if record.get("source") != "manual" else bool(record.get("catalog_name"))
            record.setdefault("catalog_source", "saved_metadata")
        record["catalog_match_keys"] = sorted(_record_catalog_keys(record))
        out.append(normalize_record(record))

    for item in catalog:
        name_key = str(item.get("catalog_name") or item.get("name") or "").casefold()
        if name_key and name_key not in seen_catalog_names:
            out.append(normalize_record(item))
    return out


def resolve_catalog_record(records: list[dict[str, Any]], query: str, catalog_loras: list[str] | tuple[str, ...] | None = None) -> dict[str, Any] | None:
    wanted_keys = lora_catalog_match_keys(query)
    if not wanted_keys:
        return None
    bridged = attach_catalog_bridge(records, catalog_loras or [])
    for record in bridged:
        if wanted_keys.intersection(_record_catalog_keys(record)):
            return normalize_record(record)
    return None


def catalog_bridge_payload(records: list[dict[str, Any]], catalog_loras: list[str] | tuple[str, ...] | None) -> dict[str, Any]:
    bridged = attach_catalog_bridge(records, catalog_loras or [])
    available = [item for item in bridged if item.get("catalog_available")]
    saved = [item for item in bridged if item.get("source") != "comfy_lora_loader"]
    return {
        "schema_version": "neo.lora_stack.catalog_bridge.v1",
        "source": "comfy:LoraLoader.lora_name",
        "catalog_count": len(catalog_loras or []),
        "record_count": len(bridged),
        "available_count": len(available),
        "saved_count": len(saved),
        "records": bridged,
    }


def record_to_stack_row(record: dict[str, Any], existing_rows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    record = normalize_record(record)
    name = record.get("catalog_name") or record.get("name") or record.get("file") or ""
    name = normalize_lora_catalog_name(name)
    if not name:
        return None
    strength = record.get("default_strength", 0.8)
    row = {"uid": f"record_{record.get('id') or stable_record_id(name)}", "enabled": True, "name": name, "strength": strength, "target": "both", "apply_to": "global", "source_record_id": record.get("id", "")}
    new_key = (row["name"].casefold(), str(row["source_record_id"] or "").casefold())
    for existing in existing_rows or []:
        old_key = (str(existing.get("name") or "").casefold(), str(existing.get("source_record_id") or "").casefold())
        if old_key == new_key or (old_key[0] == new_key[0] and not old_key[1]):
            return None
    return row
