from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.lora_stack.legacy_retirement.v1"
CANONICAL_KEY: Final[str] = "video_lora_stack"
LEGACY_FIELDS: Final[tuple[str, ...]] = (
    "h3_turbo_enabled", "h3_turbo_lora", "h3_turbo_strength",
    "enable_video_lora", "video_lora_mode", "video_lora_model", "video_lora_strength",
    "video_lora_target", "enable_lightx2v", "high_noise_lora", "low_noise_lora",
    "high_noise_lora_strength", "low_noise_lora_strength",
)


def _enabled(value: Any) -> bool:
    return value is True or str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _strength(value: Any, fallback: float) -> float:
    try:
        return float(value) if value not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback


def _target(value: Any) -> str:
    return {"both": "all", "all": "all", "high": "high", "high_noise": "high", "low": "low", "low_noise": "low"}.get(str(value or "both").casefold(), "all")


def legacy_rows(payload: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    source = payload or {}
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    if _enabled(source.get("h3_turbo_enabled")):
        name = str(source.get("h3_turbo_lora") or "").strip()
        if name:
            rows.append({"uid": "retired_h3_turbo", "enabled": True, "name": name, "strength_model": _strength(source.get("h3_turbo_strength"), 1.0), "role": "speed", "target": "all"})
        else:
            rows.append({"uid": "retired_h3_turbo_unresolved", "enabled": True, "name": "", "strength_model": _strength(source.get("h3_turbo_strength"), 1.0), "role": "speed", "target": "all"})
            unresolved.append({"code": "legacy_h3_file_missing", "message": "Legacy H3 Turbo was enabled without a saved LoRA filename."})
    if _enabled(source.get("enable_video_lora")):
        name = str(source.get("video_lora_model") or "").strip()
        if name:
            rows.append({"uid": "retired_wan_standard", "enabled": True, "name": name, "strength_model": _strength(source.get("video_lora_strength"), .8), "role": "standard", "target": _target(source.get("video_lora_target"))})
        else:
            rows.append({"uid": "retired_wan_standard_unresolved", "enabled": True, "name": "", "strength_model": _strength(source.get("video_lora_strength"), .8), "role": "standard", "target": _target(source.get("video_lora_target"))})
            unresolved.append({"code": "legacy_wan_file_missing", "message": "Legacy WAN Video LoRA was enabled without a saved filename."})
    if _enabled(source.get("enable_lightx2v")):
        for branch in ("high", "low"):
            name = str(source.get(f"{branch}_noise_lora") or "").strip()
            if name:
                rows.append({"uid": f"retired_wan_speed_{branch}", "enabled": True, "name": name, "strength_model": _strength(source.get(f"{branch}_noise_lora_strength"), 1.0), "role": "speed", "target": branch})
        if not any(row["uid"].startswith("retired_wan_speed_") for row in rows):
            rows.append({"uid": "retired_wan_speed_unresolved", "enabled": True, "name": "", "strength_model": 1.0, "role": "speed", "target": "all"})
            unresolved.append({"code": "legacy_wan_speed_files_missing", "message": "Legacy WAN LightX2V was enabled without saved branch filenames."})
    return rows, unresolved


def retire_legacy_payload(payload: dict[str, Any] | None, *, max_rows: int = 8) -> tuple[dict[str, Any], dict[str, Any]]:
    """One-way read migration. Output contains canonical state and no legacy write fields."""
    result = deepcopy(payload or {})
    canonical = result.get(CANONICAL_KEY) if isinstance(result.get(CANONICAL_KEY), dict) else {}
    canonical_rows = deepcopy(canonical.get("rows")) if isinstance(canonical.get("rows"), list) else []
    had_canonical_rows = bool(canonical_rows)
    migrated, unresolved = legacy_rows(result)
    seen = {(str(row.get("name") or "").casefold(), str(row.get("target") or "all")) for row in canonical_rows if isinstance(row, dict)}
    added = []
    duplicates = 0
    overflow = 0
    if not canonical_rows:
        for row in migrated:
            key = (row["name"].casefold(), row["target"])
            if row["name"] and key in seen:
                duplicates += 1
                continue
            if len(canonical_rows) >= max_rows:
                overflow += 1
                unresolved.append({"code": "legacy_stack_overflow", "message": f"Legacy row {row['name']} exceeded the canonical {max_rows}-row limit."})
                continue
            canonical_rows.append(row); added.append(row["uid"])
            if row["name"]: seen.add(key)
    elif migrated:
        duplicates = len(migrated)
    legacy_present = [key for key in LEGACY_FIELDS if key in result]
    for key in LEGACY_FIELDS:
        result.pop(key, None)
    if canonical_rows or canonical:
        result[CANONICAL_KEY] = {
            **canonical,
            "enabled": bool(canonical.get("enabled")) if canonical else bool(canonical_rows),
            "rows": canonical_rows,
            "unresolved": [*deepcopy(canonical.get("unresolved") or []), *([] if had_canonical_rows else unresolved)],
            "migration": {"schema_version": SCHEMA_VERSION, "status": "retired", "legacy_fields_removed": legacy_present, "added_uids": added},
        }
    report = {
        "schema_version": SCHEMA_VERSION,
        "legacy_detected": bool(legacy_present),
        "legacy_fields_removed": legacy_present,
        "canonical_precedence": bool(canonical_rows and migrated and not added),
        "migrated_count": len(added),
        "duplicate_or_shadowed_count": duplicates,
        "overflow_count": overflow,
        "unresolved": unresolved,
        "safe_to_persist": not any(key in result for key in LEGACY_FIELDS),
        "graph_authority": "canonical_video_lora_stack_only",
    }
    return result, report


def assert_no_legacy_writeback(payload: dict[str, Any] | None) -> None:
    present = [key for key in LEGACY_FIELDS if key in (payload or {})]
    if present:
        raise ValueError("Retired Video LoRA fields cannot be persisted: " + ", ".join(present))
