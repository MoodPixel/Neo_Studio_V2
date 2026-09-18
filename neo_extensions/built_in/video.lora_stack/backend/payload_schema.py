from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "video.lora_stack"
VERSION = 1
PAYLOAD_SCHEMA_VERSION = "neo.video.lora_stack.payload.v1"
WORKSPACE_APP = "assets"
SOURCE = "video.assets.lora_stack"
MAX_LORAS = 12

VALID_ROLES = {"standard", "speed"}
VALID_TARGETS = {"all", "high", "low"}

ROLE_ALIASES = {
    "normal": "standard",
    "default": "standard",
    "style": "standard",
    "motion": "standard",
    "turbo": "speed",
    "lightning": "speed",
    "lightx2v": "speed",
    "distilled": "speed",
    "accelerator": "speed",
    "fast": "speed",
}

TARGET_ALIASES = {
    "both": "all",
    "model": "all",
    "base": "all",
    "global": "all",
    "high_noise": "high",
    "high-noise": "high",
    "low_noise": "low",
    "low-noise": "low",
}

ROW_ALLOWED_KEYS = {
    "uid",
    "enabled",
    "name",
    "portable_catalog_name",
    "strength_model",
    "strength_clip",
    "role",
    "target",
    "source_record_id",
}
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


def normalize_strength(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    number = max(-10.0, min(10.0, number))
    return round(number, 4)


def normalize_role(value: Any) -> str:
    role = str(value or "standard").strip().lower()
    role = ROLE_ALIASES.get(role, role)
    return role if role in VALID_ROLES else "standard"


def normalize_target(value: Any) -> str:
    target = str(value or "all").strip().lower()
    target = TARGET_ALIASES.get(target, target)
    return target if target in VALID_TARGETS else "all"


def normalize_lora_row(
    row: dict[str, Any] | None,
    index: int = 0,
    *,
    include_disabled: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None

    enabled = _as_bool(row.get("enabled"), True)
    if not enabled and not include_disabled:
        return None

    # Persist only a portable catalog identity. Exact provider enum values are
    # rebound from the live backend catalog later by route/compiler integration.
    name = str(
        row.get("portable_catalog_name")
        or row.get("name")
        or row.get("lora_name")
        or ""
    ).replace("\\", "/").strip()
    if not name:
        return None

    item: dict[str, Any] = {
        "uid": str(row.get("uid") or f"video_lora_{index + 1}"),
        "enabled": enabled,
        "name": name,
        "strength_model": normalize_strength(
            row.get("strength_model", row.get("strength", row.get("lora_strength", 1.0)))
        ),
        "role": normalize_role(row.get("role")),
        "target": normalize_target(row.get("target", row.get("lora_target"))),
    }

    # Video routes are model-only by default. CLIP strength is retained only
    # when the caller explicitly provides it so a future compiler patch profile
    # can distinguish model-only from model+CLIP application cleanly.
    if row.get("strength_clip") is not None:
        item["strength_clip"] = normalize_strength(row.get("strength_clip"), 1.0)

    source_record_id = str(row.get("source_record_id") or row.get("record_id") or "").strip()
    if source_record_id:
        item["source_record_id"] = source_record_id

    return item


def normalize_lora_rows(
    rows: list[dict[str, Any]] | None,
    *,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for index, row in enumerate(rows or []):
        item = normalize_lora_row(row, index, include_disabled=include_disabled)
        if not item:
            continue

        key = (
            item["name"],
            item["strength_model"],
            item.get("strength_clip"),
            item["role"],
            item["target"],
            item["enabled"],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)

        if len(normalized) >= MAX_LORAS:
            break

    return normalized


def lora_stack_summary(rows: list[dict[str, Any]] | None) -> dict[str, int]:
    clean_rows = normalize_lora_rows(rows or [], include_disabled=True)
    return {
        "total": len(clean_rows),
        "enabled": len([row for row in clean_rows if row.get("enabled")]),
        "standard": len([row for row in clean_rows if row.get("role") == "standard"]),
        "speed": len([row for row in clean_rows if row.get("role") == "speed"]),
        "all": len([row for row in clean_rows if row.get("target") == "all"]),
        "high": len([row for row in clean_rows if row.get("target") == "high"]),
        "low": len([row for row in clean_rows if row.get("target") == "low"]),
    }


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
    meta.setdefault("payload_schema", PAYLOAD_SCHEMA_VERSION)

    return {
        "enabled": active,
        "version": VERSION,
        "inputs": {},
        "params": {"loras": clean_rows} if active else {},
        "assets": deepcopy(assets) if active and isinstance(assets, dict) else {},
        "metadata": meta,
    }


def active_block(
    rows: list[dict[str, Any]],
    *,
    assets: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return extension_block(enabled=True, rows=rows, assets=assets, metadata=metadata)


def disabled_block(
    reason: str = "disabled",
    *,
    requested_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": SOURCE,
        "payload_schema": PAYLOAD_SCHEMA_VERSION,
        "reason": reason,
    }
    if requested_rows:
        requested = normalize_lora_rows(requested_rows, include_disabled=True)
        metadata["requested"] = {
            "loras": requested,
            "summary": lora_stack_summary(requested),
        }
    return extension_block(enabled=False, rows=[], assets={}, metadata=metadata)


def payload_wrapper(block: dict[str, Any]) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: block}}


def build_payload(
    *,
    enabled: bool = True,
    loras: list[dict[str, Any]] | None = None,
    assets: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return payload_wrapper(
        extension_block(
            enabled=enabled,
            rows=loras or [],
            assets=assets,
            metadata=metadata,
        )
    )


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


def sanitize_block(block: dict[str, Any] | None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}

    return extension_block(
        enabled=_as_bool(block.get("enabled"), False),
        rows=rows,
        assets=assets,
        metadata=metadata,
    )


def extract_lora_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    block = sanitize_block(_raw_extension_block(payload))
    if not block.get("enabled"):
        return []
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    return normalize_lora_rows(rows)


def validate_payload_block_shape(block: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["Video LoRA Stack payload block must be an object."]

    for key in BLOCK_ALLOWED_KEYS:
        if key not in block:
            errors.append(f"Video LoRA Stack payload block missing key: {key}")

    unknown = sorted(set(block.keys()) - BLOCK_ALLOWED_KEYS)
    if unknown:
        errors.append(f"Video LoRA Stack payload block has unsupported keys: {', '.join(unknown)}")

    if block.get("version") != VERSION:
        errors.append(f"Video LoRA Stack payload version must be {VERSION}.")

    if block.get("enabled") is False:
        for key in ("inputs", "params", "assets"):
            if block.get(key):
                errors.append(f"Disabled Video LoRA Stack payload must not carry active {key}.")
        return errors

    params = block.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("loras"), list):
        errors.append("Enabled Video LoRA Stack payload must carry params.loras as a list.")
        return errors

    rows = params.get("loras") or []
    if len(rows) > MAX_LORAS:
        errors.append(f"Video LoRA Stack supports at most {MAX_LORAS} LoRAs per payload.")

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"Video LoRA row {index + 1} must be an object.")
            continue

        unknown_row_keys = sorted(set(row.keys()) - ROW_ALLOWED_KEYS)
        if unknown_row_keys:
            errors.append(
                f"Video LoRA row {index + 1} has unsupported keys: {', '.join(unknown_row_keys)}"
            )

        if not str(row.get("name") or row.get("portable_catalog_name") or "").strip():
            errors.append(f"Video LoRA row {index + 1} is missing a portable LoRA name.")

        role = str(row.get("role") or "standard").strip().lower()
        role = ROLE_ALIASES.get(role, role)
        if role not in VALID_ROLES:
            errors.append(f"Video LoRA row {index + 1} has unsupported role: {role}")

        target = str(row.get("target") or "all").strip().lower()
        target = TARGET_ALIASES.get(target, target)
        if target not in VALID_TARGETS:
            errors.append(f"Video LoRA row {index + 1} has unsupported target: {target}")

    return errors
