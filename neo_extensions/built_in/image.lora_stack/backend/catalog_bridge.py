from __future__ import annotations

from pathlib import Path
from typing import Any

from .library_schema import normalize_record, record_from_provider_lora_name, stable_record_id


def normalize_lora_catalog_name(value: Any) -> str:
    """Return the provider-facing portable LoRA catalog choice."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("catalog_name") or value.get("file") or value.get("id")
    text = str(value or "").replace("\\", "/").strip()
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        text = Path(text).name
    while text.startswith("./"):
        text = text[2:]
    return text


def portable_catalog_key(value: Any) -> str:
    """Return the full relative catalog identity used for safe matching only.

    The key intentionally normalizes path separators and case, but does not
    collapse to basename/stem. Two different provider subfolders must never
    become interchangeable merely because their filenames match.
    """

    return normalize_lora_catalog_name(value).casefold()


def resolve_exact_provider_catalog_name(
    requested_name: Any,
    provider_choices: list[str] | tuple[str, ...] | set[str] | None,
) -> dict[str, Any]:
    """Bind a portable LoRA identity to one exact live provider enum value.

    ComfyUI validates ``lora_name`` against the exact strings advertised by the
    active loader node. Neo may persist portable slash-separated identities, but
    graph compilation must submit the original provider spelling/separators.
    Ambiguous normalized matches fail closed instead of selecting arbitrarily.
    """

    requested_raw = str(requested_name or "").strip()
    portable_name = normalize_lora_catalog_name(requested_raw)
    choices = [str(item) for item in (provider_choices or []) if str(item).strip()]
    result = {
        "schema_version": "neo.image.lora_stack.catalog_binding.v1",
        "portable_catalog_name": portable_name,
        "provider_catalog_name": "",
        "status": "blocked_missing_catalog_entry",
        "match_mode": "",
        "catalog_count": len(choices),
        "candidate_provider_names": [],
        "verified": False,
    }
    if not portable_name:
        result["reason"] = "LoRA row has no portable catalog name."
        return result

    exact = [item for item in choices if item == requested_raw]
    if len(exact) == 1:
        result.update({
            "provider_catalog_name": exact[0],
            "status": "resolved",
            "match_mode": "exact_provider_value",
            "candidate_provider_names": exact,
            "verified": True,
            "reason": "Exact live provider catalog value matched.",
        })
        return result

    key = portable_catalog_key(portable_name)
    normalized = [item for item in choices if portable_catalog_key(item) == key]
    if len(normalized) == 1:
        result.update({
            "provider_catalog_name": normalized[0],
            "status": "resolved",
            "match_mode": "portable_separator_case_match",
            "candidate_provider_names": normalized,
            "verified": True,
            "reason": "Portable catalog identity rebound to the exact live provider value.",
        })
        return result
    if len(normalized) > 1:
        result.update({
            "status": "blocked_ambiguous_catalog_entry",
            "candidate_provider_names": normalized,
            "reason": "Multiple live provider catalog values normalize to the same portable LoRA identity.",
        })
        return result

    result["reason"] = "Portable LoRA identity was not found in the active loader catalog."
    return result


def lora_catalog_match_keys(value: Any) -> set[str]:
    """Match saved records without changing the provider-facing catalog value."""
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


def catalog_records_from_names(
    catalog_loras: list[str] | tuple[str, ...] | None,
    *,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in catalog_loras or []:
        name = normalize_lora_catalog_name(raw)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        record = record_from_provider_lora_name(
            name,
            provider_id=provider_id,
            catalog_source=catalog_source,
            provider_label=provider_label,
        )
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


def attach_catalog_bridge(
    records: list[dict[str, Any]],
    catalog_loras: list[str] | tuple[str, ...] | None,
    *,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> list[dict[str, Any]]:
    """Mark saved records as available/missing against the selected provider catalog.

    Saved metadata stays authoritative. The provider catalog name remains the value
    rendered by the provider compiler. Missing saved records remain visible but are
    marked unavailable so the UI never pretends another backend owns them.
    """
    provider = str(provider_id or "comfyui").strip().casefold()
    source = str(catalog_source or "").strip() or ("forge:extra_network_lora" if provider == "forge" else "comfy:LoraLoader.lora_name")
    label = str(provider_label or "").strip() or ("Forge Neo" if provider == "forge" else "ComfyUI")
    catalog = catalog_records_from_names(
        catalog_loras,
        provider_id=provider,
        catalog_source=source,
        provider_label=label,
    )
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
            record["catalog_source"] = source
            record["provider_id"] = provider
            record["provider_label"] = label
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


def resolve_catalog_record(
    records: list[dict[str, Any]],
    query: str,
    catalog_loras: list[str] | tuple[str, ...] | None = None,
    *,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> dict[str, Any] | None:
    wanted_keys = lora_catalog_match_keys(query)
    if not wanted_keys:
        return None
    bridged = attach_catalog_bridge(
        records,
        catalog_loras or [],
        provider_id=provider_id,
        catalog_source=catalog_source,
        provider_label=provider_label,
    )
    for record in bridged:
        if wanted_keys.intersection(_record_catalog_keys(record)):
            return normalize_record(record)
    return None


def catalog_bridge_payload(
    records: list[dict[str, Any]],
    catalog_loras: list[str] | tuple[str, ...] | None,
    *,
    provider_id: str = "comfyui",
    profile_id: str = "",
    catalog_source: str = "",
    provider_label: str = "",
) -> dict[str, Any]:
    provider = str(provider_id or "comfyui").strip().casefold()
    source = str(catalog_source or "").strip() or ("forge:extra_network_lora" if provider == "forge" else "comfy:LoraLoader.lora_name")
    label = str(provider_label or "").strip() or ("Forge Neo" if provider == "forge" else "ComfyUI")
    bridged = attach_catalog_bridge(
        records,
        catalog_loras or [],
        provider_id=provider,
        catalog_source=source,
        provider_label=label,
    )
    available = [item for item in bridged if item.get("catalog_available")]
    saved = [item for item in bridged if item.get("source") not in {"comfy_lora_loader", "forge_lora_catalog"}]
    return {
        "schema_version": "neo.lora_stack.catalog_bridge.v2",
        "source": source,
        "catalog_source": source,
        "provider_id": provider,
        "provider_label": label,
        "profile_id": str(profile_id or ""),
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
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
    new_name_keys = lora_catalog_match_keys(row["name"])
    new_record_id = str(row["source_record_id"] or "").casefold()
    for existing in existing_rows or []:
        existing_name_keys = lora_catalog_match_keys(existing.get("name"))
        existing_record_id = str(existing.get("source_record_id") or "").casefold()
        if new_name_keys.intersection(existing_name_keys) or (new_record_id and existing_record_id == new_record_id):
            return None
    return row
