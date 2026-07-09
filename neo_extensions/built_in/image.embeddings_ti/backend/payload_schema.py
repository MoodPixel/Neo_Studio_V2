from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

EXTENSION_ID = "embeddings_ti"
VERSION = 1
WORKSPACE_APP = "assets"
SOURCE = "image.assets.embeddings_ti"
ACTIVE_ROUTE_STATES = {"available", "experimental_available"}
VALID_TARGETS = {"positive_prompt", "negative_prompt", "finish_positive", "finish_negative"}
TARGET_ALIASES = {
    "positive": "positive_prompt",
    "base_positive": "positive_prompt",
    "pos": "positive_prompt",
    "negative": "negative_prompt",
    "base_negative": "negative_prompt",
    "neg": "negative_prompt",
    "finish_positive_prompt": "finish_positive",
    "finish_negative_prompt": "finish_negative",
}
TOKEN_RE = re.compile(r"^(embedding:)?[A-Za-z0-9_. -]+$")
BLOCK_ALLOWED_KEYS = {"enabled", "version", "inputs", "params", "assets", "metadata"}
PARAM_ALLOWED_KEYS = {"items", "selected_tokens", "helper_target", "base_positive", "base_negative", "finish_positive", "finish_negative"}
LEGACY_PARAM_KEYS = {"selected_tokens", "helper_target", "base_positive", "base_negative", "finish_positive", "finish_negative"}
ASSET_ALLOWED_KEYS = {"selected_embedding", "selected_embeddings", "selected_embedding_id", "selected_embedding_name", "selected_embedding_path", "selected_embedding_token"}
ITEM_ALLOWED_KEYS = {"uid", "id", "source_record_id", "token", "name", "strength", "target"}
INPUT_ALLOWED_KEYS: set[str] = set()


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


def normalize_target(value: Any) -> str:
    raw = str(value or "negative_prompt").strip().lower()
    raw = TARGET_ALIASES.get(raw, raw)
    return raw if raw in VALID_TARGETS else "negative_prompt"


def normalize_helper_target(value: Any) -> str:
    # Legacy payload compatibility. New chip UI uses per-item targets.
    target = str(value or "both").strip().lower()
    return target if target in {"base", "finish", "both"} else "both"


def normalize_strength(value: Any) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        strength = 1.0
    return max(0.0, min(2.0, round(strength, 3)))


def normalize_token(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = re.sub(r"\.(pt|safetensors|bin)$", "", raw, flags=re.IGNORECASE)
    if not TOKEN_RE.match(raw):
        return ""
    return raw if raw.startswith("embedding:") else f"embedding:{raw}"


def normalize_tokens(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [item.strip() for item in values.replace("\n", ",").split(",")]
    if not isinstance(values, list):
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = normalize_token(value)
        key = token.casefold()
        if token and key not in seen:
            result.append(token)
            seen.add(key)
    return result


def normalize_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = {"token": value}
    if not isinstance(value, dict):
        return None
    token = normalize_token(value.get("token") or value.get("name") or value.get("file"))
    if not token:
        return None
    name = str(value.get("name") or token.replace("embedding:", "")).strip()
    item = {
        "token": token,
        "name": name,
        "strength": normalize_strength(value.get("strength", 1.0)),
        "target": normalize_target(value.get("target")),
    }
    for key in ("uid", "source_record_id", "id"):
        if value.get(key):
            item[key] = str(value.get(key)).strip()
    return item


def normalize_items(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        values = []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        item = normalize_item(value)
        if not item:
            continue
        key = f"{item['token'].casefold()}|{item['strength']}|{item['target']}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def legacy_items_from_params(params: dict[str, Any]) -> list[dict[str, Any]]:
    # Phase E.1 replaces four text boxes with chips, but this keeps older saved drafts safe.
    legacy_fields = [
        ("base_positive", "positive_prompt"),
        ("base_negative", "negative_prompt"),
        ("finish_positive", "finish_positive"),
        ("finish_negative", "finish_negative"),
    ]
    items: list[dict[str, Any]] = []
    for field, target in legacy_fields:
        for token in normalize_tokens(params.get(field)):
            items.append({"token": token, "name": token.replace("embedding:", ""), "strength": 1.0, "target": target})
    for token in normalize_tokens(params.get("selected_tokens")):
        items.append({"token": token, "name": token.replace("embedding:", ""), "strength": 1.0, "target": "negative_prompt"})
    return normalize_items(items)


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    items = normalize_items(params.get("items"))
    if not items:
        items = legacy_items_from_params(params)
    clean = {"items": items} if items else {}
    return clean


def _clean_selected_embedding(value: dict[str, Any]) -> dict[str, Any]:
    token = normalize_token(value.get("token") or value.get("name") or value.get("file"))
    clean_selected = {
        "id": str(value.get("id") or value.get("source_record_id") or "").strip(),
        "name": str(value.get("name") or "").strip(),
        "path": str(value.get("path") or value.get("file") or "").strip(),
        "token": token,
        "preview_image": str(value.get("preview_image") or "").strip(),
        "base_model": str(value.get("base_model") or "").strip(),
    }
    return {key: val for key, val in clean_selected.items() if val}


def sanitize_assets(assets: dict[str, Any] | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    assets = assets if isinstance(assets, dict) else {}
    result: dict[str, Any] = {}
    selected = assets.get("selected_embedding") if isinstance(assets.get("selected_embedding"), dict) else {}
    if assets.get("selected_embedding_token") or assets.get("selected_embedding_name") or assets.get("selected_embedding_path"):
        selected = {
            **selected,
            "id": selected.get("id") or assets.get("selected_embedding_id"),
            "name": selected.get("name") or assets.get("selected_embedding_name"),
            "path": selected.get("path") or assets.get("selected_embedding_path"),
            "token": selected.get("token") or assets.get("selected_embedding_token"),
        }
    clean_selected = _clean_selected_embedding(selected) if selected else {}
    if clean_selected:
        result["selected_embedding"] = clean_selected
    raw_many = assets.get("selected_embeddings")
    if isinstance(raw_many, list):
        many = [_clean_selected_embedding(item) for item in raw_many if isinstance(item, dict)]
        many = [item for item in many if item]
        if many:
            result["selected_embeddings"] = many
    return result


def extension_block(*, enabled: bool, params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_params = sanitize_params(params)
    clean_assets = sanitize_assets(assets, clean_params)
    active = bool(enabled and clean_params.get("items"))
    meta = deepcopy(metadata) if isinstance(metadata, dict) else {}
    meta.setdefault("source", SOURCE)
    return {
        "enabled": active,
        "version": VERSION,
        "inputs": {},
        "params": clean_params if active else {},
        "assets": clean_assets if active else {},
        "metadata": meta,
    }


def disabled_block(reason: str = "disabled", *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": SOURCE, "reason": reason}
    if route:
        metadata["route"] = deepcopy(route)
        if route.get("route_state"):
            metadata["route_state"] = route.get("route_state")
    return extension_block(enabled=False, params={}, assets={}, metadata=metadata)


def active_block(params: dict[str, Any], *, assets: dict[str, Any] | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": SOURCE}
    if route:
        metadata["route"] = deepcopy(route)
        if route.get("route_state"):
            metadata["route_state"] = route.get("route_state")
    return extension_block(enabled=True, params=params, assets=assets, metadata=metadata)


def payload_wrapper(block: dict[str, Any]) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: block}}


def build_payload(*, enabled: bool = True, params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return payload_wrapper(extension_block(enabled=enabled, params=params or {}, assets=assets or {}, metadata=metadata or {"source": SOURCE, "phase": "F"}))


def raw_extension_block(payload: dict[str, Any] | None) -> dict[str, Any]:
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
    params = sanitize_params(block.get("params") if isinstance(block.get("params"), dict) else {})
    assets = sanitize_assets(block.get("assets") if isinstance(block.get("assets"), dict) else {}, params)
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    if route:
        metadata = {**metadata, "route": deepcopy(route), "route_state": route.get("route_state")}
    return extension_block(enabled=_as_bool(block.get("enabled"), False), params=params, assets=assets, metadata=metadata)


def validate_payload_block_shape(block: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["Embeddings/TI payload block must be an object."]
    missing = BLOCK_ALLOWED_KEYS - set(block.keys())
    for key in sorted(missing):
        errors.append(f"Embeddings/TI payload block missing required key: {key}")
    hidden = sorted(set(block.keys()) - BLOCK_ALLOWED_KEYS)
    if hidden:
        errors.append(f"Embeddings/TI payload contains unsupported top-level keys: {', '.join(hidden)}")
    inputs = block.get("inputs")
    if isinstance(inputs, dict):
        input_hidden = sorted(set(inputs.keys()) - INPUT_ALLOWED_KEYS)
        if input_hidden:
            errors.append(f"Embeddings/TI inputs must stay empty; unsupported input keys: {', '.join(input_hidden)}")
    params = block.get("params")
    if isinstance(params, dict):
        param_hidden = sorted(set(params.keys()) - PARAM_ALLOWED_KEYS)
        if param_hidden:
            errors.append(f"Embeddings/TI params contain unsupported keys: {', '.join(param_hidden)}")
        raw_items = params.get("items")
        if raw_items is not None and not isinstance(raw_items, list):
            errors.append("Embeddings/TI params.items must be a list.")
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    errors.append(f"Embeddings/TI params.items[{index}] must be an object.")
                    continue
                item_hidden = sorted(set(item.keys()) - ITEM_ALLOWED_KEYS)
                if item_hidden:
                    errors.append(f"Embeddings/TI params.items[{index}] contains unsupported keys: {', '.join(item_hidden)}")
                if not normalize_token(item.get("token") or item.get("name")):
                    errors.append(f"Embeddings/TI params.items[{index}] has an invalid token.")
    assets = block.get("assets")
    if isinstance(assets, dict):
        asset_hidden = sorted(set(assets.keys()) - ASSET_ALLOWED_KEYS)
        if asset_hidden:
            errors.append(f"Embeddings/TI assets contain unsupported keys: {', '.join(asset_hidden)}")
    return errors


def migrate_legacy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize older V1/E-phase payloads into the Phase F chip contract.

    This keeps saved drafts/replays safe while stripping hidden/stale fields from
    the active generation payload.
    """
    if not isinstance(payload, dict):
        return payload_wrapper(disabled_block("missing_payload"))
    result = deepcopy(payload)
    raw = raw_extension_block(result)
    if raw:
        block = sanitize_block(raw)
        result.setdefault("extensions", {})[EXTENSION_ID] = block
        return result
    legacy_params = {key: result.get(key) for key in LEGACY_PARAM_KEYS if key in result}
    if legacy_params:
        block = extension_block(enabled=True, params=legacy_params, assets={}, metadata={"source": SOURCE, "migrated_from": "legacy_embeddings_ti"})
        result.setdefault("extensions", {})[EXTENSION_ID] = block
    return result


def payload_contract() -> dict[str, Any]:
    return {
        "schema_version": "neo.extension.payload.v1",
        "extension_id": EXTENSION_ID,
        "version": VERSION,
        "block_key": "extensions",
        "required_keys": sorted(BLOCK_ALLOWED_KEYS),
        "inputs_policy": "always_empty",
        "params_policy": "items_only_after_legacy_migration",
        "asset_policy": "selected_embedding_metadata_only",
        "active_route_states": sorted(ACTIVE_ROUTE_STATES),
        "item_allowed_keys": sorted(ITEM_ALLOWED_KEYS),
        "allowed_targets": sorted(VALID_TARGETS),
        "legacy_accepted_params": sorted(LEGACY_PARAM_KEYS),
        "hidden_field_policy": "strip_from_clean_block_and_report_in_validation",
        "disabled_behavior": "no_active_params_no_assets_no_workflow_mutation",
    }
