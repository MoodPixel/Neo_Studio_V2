from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

EXTENSION_ID = "lora_stack"
VERSION = 1
WORKSPACE_APP = "assets"
SOURCE = "image.assets.lora_stack"

VALID_TARGETS = {"both", "base", "finish"}
VALID_APPLY_TO_PREFIX = "scene_region_"
SCENE_REGION_RE = re.compile(r"^scene_region_[A-Za-z0-9_-]+$")
ACTIVE_ROUTE_STATES = {"available", "experimental_available"}

ROW_ALLOWED_KEYS = {"uid", "enabled", "name", "strength", "target", "apply_to", "source_record_id"}
ASSET_ALLOWED_KEYS = {"name", "record_id", "file", "hash", "preview_image"}
BLOCK_ALLOWED_KEYS = {"enabled", "version", "inputs", "params", "assets", "metadata"}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def clamp_strength(value: Any, default: float = 0.8) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    number = max(-4.0, min(4.0, number))
    return round(number, 4)


def normalize_target(value: Any) -> str:
    target = str(value or "both").strip().lower()
    return target if target in VALID_TARGETS else "both"


def normalize_apply_to(value: Any) -> str:
    apply_to = str(value or "global").strip()
    if apply_to == "global":
        return apply_to
    if SCENE_REGION_RE.match(apply_to):
        return apply_to
    return "global"


def normalize_lora_row(row: dict[str, Any] | None, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    if _as_bool(row.get("enabled"), True) is False:
        return None
    name = str(row.get("name") or row.get("lora_name") or "").strip()
    if not name:
        return None
    item = {
        "uid": str(row.get("uid") or f"lora_{index + 1}"),
        "enabled": True,
        "name": name,
        "strength": clamp_strength(row.get("strength", row.get("lora_strength", 0.8))),
        "target": normalize_target(row.get("target")),
        "apply_to": normalize_apply_to(row.get("apply_to")),
    }
    source_record_id = str(row.get("source_record_id") or row.get("record_id") or "").strip()
    if source_record_id:
        item["source_record_id"] = source_record_id
    return item


def normalize_lora_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str, str]] = set()
    for index, row in enumerate(rows or []):
        item = normalize_lora_row(row, index)
        if not item:
            continue
        key = (item["name"], item["strength"], item["target"], item["apply_to"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized




def lora_target_summary(rows: list[dict[str, Any]] | None) -> dict[str, int]:
    clean_rows = normalize_lora_rows(rows or [])
    return {
        "total": len(clean_rows),
        "global": len([row for row in clean_rows if row.get("apply_to") == "global"]),
        "regional": len([row for row in clean_rows if str(row.get("apply_to") or "").startswith(VALID_APPLY_TO_PREFIX)]),
        "finish_only": len([row for row in clean_rows if row.get("target") == "finish"]),
        "base_or_both": len([row for row in clean_rows if row.get("target") in {"base", "both"}]),
    }

def sanitize_asset(asset: dict[str, Any] | None, row_name: str = "") -> dict[str, Any]:
    if not isinstance(asset, dict):
        asset = {}
    clean = {
        "name": str(asset.get("name") or row_name or "").strip(),
        "record_id": str(asset.get("record_id") or asset.get("source_record_id") or "").strip(),
        "file": str(asset.get("file") or "").strip(),
        "hash": str(asset.get("hash") or "").strip(),
        "preview_image": str(asset.get("preview_image") or "").strip(),
    }
    return {key: value for key, value in clean.items() if value}


def sanitize_assets(assets: dict[str, Any] | None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = rows or []
    raw_loras = []
    if isinstance(assets, dict) and isinstance(assets.get("loras"), list):
        raw_loras = assets.get("loras") or []
    by_name = {str(item.get("name") or "").strip(): item for item in raw_loras if isinstance(item, dict)}
    clean_assets: list[dict[str, Any]] = []
    for row in rows:
        row_name = str(row.get("name") or "").strip()
        clean_assets.append(sanitize_asset(by_name.get(row_name), row_name=row_name))
    return {"loras": clean_assets} if clean_assets else {}


def extension_block(
    *,
    enabled: bool,
    rows: list[dict[str, Any]] | None = None,
    assets: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_rows = normalize_lora_rows(rows or []) if enabled else []
    active = bool(enabled and clean_rows)
    meta = deepcopy(metadata) if isinstance(metadata, dict) else {}
    meta.setdefault("source", SOURCE)
    return {
        "enabled": active,
        "version": VERSION,
        "inputs": {},
        "params": {"loras": clean_rows} if active else {},
        "assets": sanitize_assets(assets, clean_rows) if active else {},
        "metadata": meta,
    }


def disabled_block(reason: str = "disabled", *, route: dict[str, Any] | None = None, requested_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": SOURCE, "reason": reason}
    if route:
        metadata["route"] = deepcopy(route)
        if route.get("route_state"):
            metadata["route_state"] = route.get("route_state")
    if requested_rows:
        metadata["requested"] = {
            "row_count": len(requested_rows),
            "lora_names": [str(row.get("name") or "") for row in requested_rows if isinstance(row, dict) and row.get("name")],
            "loras": deepcopy(requested_rows),
            "target_summary": lora_target_summary(requested_rows),
        }
    return extension_block(enabled=False, rows=[], assets={}, metadata=metadata)


def active_block(rows: list[dict[str, Any]], *, route: dict[str, Any] | None = None, assets: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": SOURCE}
    if route:
        metadata["route"] = deepcopy(route)
        if route.get("route_state"):
            metadata["route_state"] = route.get("route_state")
    return extension_block(enabled=True, rows=rows, assets=assets, metadata=metadata)


def payload_wrapper(block: dict[str, Any]) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: block}}


def build_payload(
    *,
    enabled: bool = True,
    loras: list[dict[str, Any]] | None = None,
    assets: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return payload_wrapper(extension_block(enabled=enabled, rows=loras or [], assets=assets, metadata=metadata or {"source": SOURCE, "phase": "D"}))


def _raw_extension_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if EXTENSION_ID in payload and isinstance(payload.get(EXTENSION_ID), dict):
        return payload.get(EXTENSION_ID) or {}
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads.get(EXTENSION_ID) or {}
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(EXTENSION_ID), dict):
        return extensions.get(EXTENSION_ID) or {}
    return {}


def sanitize_block(block: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    rows = normalize_lora_rows(((block.get("params") or {}).get("loras") or []) if isinstance(block.get("params"), dict) else [])
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    if route:
        metadata = {**metadata, "route": deepcopy(route), "route_state": route.get("route_state")}
    return extension_block(enabled=_as_bool(block.get("enabled"), False), rows=rows, assets=assets, metadata=metadata)


def migrate_legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    extensions = result.get("extensions") if isinstance(result.get("extensions"), dict) else {}
    if EXTENSION_ID in extensions:
        extensions[EXTENSION_ID] = sanitize_block(extensions[EXTENSION_ID])
        result["extensions"] = extensions
        return result

    rows = result.get("loras") if isinstance(result.get("loras"), list) else []
    if not rows and result.get("lora_name"):
        rows = [{
            "uid": "legacy_primary",
            "enabled": _as_bool(result.get("lora_enabled"), True),
            "name": result.get("lora_name"),
            "strength": result.get("lora_strength", 0.8),
            "target": result.get("lora_target", "both"),
            "apply_to": result.get("lora_apply_to", "global"),
        }]
    normalized = normalize_lora_rows(rows)
    result.setdefault("extensions", {})[EXTENSION_ID] = extension_block(enabled=bool(normalized), rows=normalized)
    return result


def validate_payload_block_shape(block: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["LoRA Stack payload block must be an object."]
    for key in BLOCK_ALLOWED_KEYS:
        if key not in block:
            errors.append(f"LoRA Stack payload block missing key: {key}")
    unknown = sorted(set(block.keys()) - BLOCK_ALLOWED_KEYS)
    if unknown:
        errors.append(f"LoRA Stack payload block has unsupported keys: {', '.join(unknown)}")
    if block.get("enabled") is False:
        for key in ("inputs", "params", "assets"):
            if block.get(key):
                errors.append(f"Disabled LoRA Stack payload must not carry active {key}.")
    params = block.get("params")
    if block.get("enabled") and (not isinstance(params, dict) or not isinstance(params.get("loras"), list)):
        errors.append("Enabled LoRA Stack payload must carry params.loras as a list.")
    return errors
