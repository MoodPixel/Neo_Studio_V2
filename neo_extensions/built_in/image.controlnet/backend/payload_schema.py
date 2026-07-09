from __future__ import annotations

from copy import deepcopy
from typing import Any

from .support_matrix import (
    ACTIVE_STATES,
    CONTROLNET_TASKS,
    TASK_MAP_CONTROL,
    TASK_INPAINT_CONTROL,
    TASK_OUTPAINT_CONTROL,
    normalize_controlnet_task,
    route_reason,
)

EXTENSION_ID = "image.controlnet"
VERSION = 1
SCHEMA = "neo.image.controlnet.v1"

VALID_UNITS = {"canny", "depth", "openpose", "lineart", "lineart_anime", "softedge", "tile", "normalbae", "scribble"}
VALID_PREPROCESSORS = VALID_UNITS | {"dwpose", "none"}
VALID_FIT_MODES = {"contain", "cover", "stretch", "native"}
VALID_BATCH_MODES = {"auto", "repeat", "clamp", "strict"}
VALID_MASK_MODES = {"none", "control_mask", "inpaint_mask"}
VALID_STRENGTH_SCHEDULES = {"flat", "linear", "ease_in", "ease_out", "ease_in_out"}
VALID_WEIGHT_PRESETS = {"balanced", "prompt_strong", "control_strong", "soft", "strict"}
VALID_ADVANCED_ENGINES = {"auto", "standard", "advanced_controlnet"}
VALID_PARAM_KEYS = {"advanced_controlnet_requested", "batch_policy", "controlnet_task", "qwen_controlnet_adapter", "controlnet_qwen_adapter", "qwen_cn_adapter", "flux_controlnet_adapter", "controlnet_flux_adapter", "flux_cn_adapter", "flux_klein_controlnet_adapter"}
VALID_ASSET_KEYS = {"control_images", "control_masks", "generated_maps", "source_images", "source_masks", "inpaint_source_images", "inpaint_masks", "outpaint_source_images", "outpaint_canvas_images", "outpaint_masks", "padded_images", "padded_masks"}
VALID_METADATA_KEYS = {"schema", "route", "route_state", "ui_source", "reason", "requested", "payload_hardening", "legacy_migrated", "controlnet_task", "asset_resolver_phase"}

COMMON_UNIT_KEYS = {
    "uid",
    "enabled",
    "unit",
    "model",
    "preprocessor",
    "strength",
    "start_percent",
    "end_percent",
    "fit_mode",
    "detect_resolution",
    "safe_mode",
    "invert_map",
    "save_intermediate",
    "advanced_enabled",
    "advanced_engine",
    "strength_schedule",
    "weight_preset",
    "mask_mode",
    "batch_mode",
    "sliding_context",
}
CANNY_KEYS = {"canny_low", "canny_high"}
OPENPOSE_KEYS = {"openpose_body", "openpose_hand", "openpose_face"}

DEFAULT_UNIT = {
    "uid": "unit_1",
    "enabled": True,
    "unit": "canny",
    "model": "",
    "preprocessor": "canny",
    "strength": 0.45,
    "start_percent": 0.0,
    "end_percent": 1.0,
    "fit_mode": "contain",
    "detect_resolution": 512,
    "safe_mode": True,
    "invert_map": False,
    "save_intermediate": False,
    "canny_low": 100,
    "canny_high": 200,
    "openpose_body": True,
    "openpose_hand": False,
    "openpose_face": False,
    "advanced_enabled": False,
    "advanced_engine": "auto",
    "strength_schedule": "flat",
    "weight_preset": "balanced",
    "mask_mode": "none",
    "batch_mode": "auto",
    "sliding_context": False,
}

LEGACY_UNIT_KEY_MAP = {
    "controlnet_name": "model",
    "controlnet_preprocessor": "preprocessor",
    "controlnet_strength": "strength",
    "control_image_name": "control_image_name",
}


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


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _enum(value: Any, *, valid: set[str], default: str, notes: list[dict[str, str]], field: str) -> str:
    text = str(value or default).strip()
    if text not in valid:
        notes.append({"level": "warning", "field": field, "message": f"Unsupported value normalized to {default}."})
        return default
    return text


def clamp(value: Any, *, minimum: float, maximum: float, default: float, precision: int = 3) -> float:
    number = max(minimum, min(maximum, _as_float(value, default)))
    return round(number, precision)


def _active_specific_keys(unit: str, preprocessor: str) -> set[str]:
    keys = set(COMMON_UNIT_KEYS)
    if unit == "canny" or preprocessor == "canny":
        keys |= CANNY_KEYS
    if unit == "openpose" or preprocessor in {"openpose", "dwpose"}:
        keys |= OPENPOSE_KEYS
    return keys


def _strip_unit_visibility(clean: dict[str, Any]) -> dict[str, Any]:
    """Drop controls hidden for the selected ControlNet unit/preprocessor.

    This is the Phase D stale-field guard: canny thresholds must not travel with
    OpenPose units, and OpenPose toggles must not travel with Canny/depth units.
    """
    allowed = _active_specific_keys(str(clean.get("unit")), str(clean.get("preprocessor")))
    return {key: value for key, value in clean.items() if key in allowed}


def normalize_unit(raw: dict[str, Any] | None, index: int = 0) -> tuple[dict[str, Any], list[dict[str, str]]]:
    data = {**DEFAULT_UNIT, **(raw or {})}
    notes: list[dict[str, str]] = []

    unit = _enum(data.get("unit"), valid=VALID_UNITS, default=DEFAULT_UNIT["unit"], notes=notes, field="unit")
    preprocessor_default = unit if unit in VALID_PREPROCESSORS else DEFAULT_UNIT["preprocessor"]
    preprocessor = _enum(data.get("preprocessor"), valid=VALID_PREPROCESSORS, default=preprocessor_default, notes=notes, field="preprocessor")
    fit_mode = _enum(data.get("fit_mode"), valid=VALID_FIT_MODES, default=DEFAULT_UNIT["fit_mode"], notes=notes, field="fit_mode")
    batch_mode = _enum(data.get("batch_mode"), valid=VALID_BATCH_MODES, default=DEFAULT_UNIT["batch_mode"], notes=notes, field="batch_mode")
    mask_mode = _enum(data.get("mask_mode"), valid=VALID_MASK_MODES, default=DEFAULT_UNIT["mask_mode"], notes=notes, field="mask_mode")
    strength_schedule = _enum(data.get("strength_schedule"), valid=VALID_STRENGTH_SCHEDULES, default=DEFAULT_UNIT["strength_schedule"], notes=notes, field="strength_schedule")
    weight_preset = _enum(data.get("weight_preset"), valid=VALID_WEIGHT_PRESETS, default=DEFAULT_UNIT["weight_preset"], notes=notes, field="weight_preset")
    advanced_engine = _enum(data.get("advanced_engine"), valid=VALID_ADVANCED_ENGINES, default=DEFAULT_UNIT["advanced_engine"], notes=notes, field="advanced_engine")

    start = clamp(data.get("start_percent"), minimum=0.0, maximum=1.0, default=0.0)
    end = clamp(data.get("end_percent"), minimum=0.0, maximum=1.0, default=1.0)
    if end < start:
        notes.append({"level": "warning", "field": "end_percent", "message": "End percent was below start percent and has been reset to 1.0."})
        end = 1.0

    canny_low = max(0, min(255, _as_int(data.get("canny_low"), 100)))
    canny_high = max(0, min(255, _as_int(data.get("canny_high"), 200)))
    if canny_high < canny_low:
        notes.append({"level": "warning", "field": "canny_high", "message": "Canny high threshold was below low threshold and has been raised."})
        canny_high = canny_low

    advanced_enabled = _as_bool(data.get("advanced_enabled"), False)
    clean = {
        **DEFAULT_UNIT,
        "uid": str(data.get("uid") or f"unit_{index + 1}"),
        "enabled": _as_bool(data.get("enabled"), True),
        "unit": unit,
        "model": str(data.get("model") or ""),
        "preprocessor": preprocessor,
        "strength": clamp(data.get("strength"), minimum=0.0, maximum=2.0, default=0.45),
        "start_percent": start,
        "end_percent": end,
        "fit_mode": fit_mode,
        "detect_resolution": max(64, min(4096, _as_int(data.get("detect_resolution"), 512))),
        "safe_mode": _as_bool(data.get("safe_mode"), True),
        "invert_map": _as_bool(data.get("invert_map"), False),
        "save_intermediate": _as_bool(data.get("save_intermediate"), False),
        "canny_low": canny_low,
        "canny_high": canny_high,
        "openpose_body": _as_bool(data.get("openpose_body"), True),
        "openpose_hand": _as_bool(data.get("openpose_hand"), False),
        "openpose_face": _as_bool(data.get("openpose_face"), False),
        "advanced_enabled": advanced_enabled,
        "advanced_engine": advanced_engine if advanced_enabled else "auto",
        "strength_schedule": strength_schedule,
        "weight_preset": weight_preset,
        "mask_mode": mask_mode,
        "batch_mode": batch_mode,
        "sliding_context": _as_bool(data.get("sliding_context"), False),
    }
    return _strip_unit_visibility(clean), notes


def normalize_units(raw_units: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(raw_units, list):
        raw_units = []
    units: list[dict[str, Any]] = []
    notes: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_units):
        if not isinstance(raw, dict):
            notes.append({"level": "warning", "field": f"units[{index}]", "message": "Skipped non-object ControlNet unit."})
            continue
        unit, unit_notes = normalize_unit(raw, index)
        uid = unit["uid"]
        if uid in seen:
            unit["uid"] = f"{uid}_{index + 1}"
            notes.append({"level": "warning", "field": f"units[{index}].uid", "message": "Duplicate ControlNet unit uid was made unique."})
        seen.add(unit["uid"])
        units.append(unit)
        notes.extend(unit_notes)
    return units, notes


def _extract_extension_block(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if isinstance(payload.get("extensions"), dict) and EXTENSION_ID in payload["extensions"]:
        block = payload["extensions"][EXTENSION_ID]
        return (deepcopy(block) if isinstance(block, dict) else {}, False)
    if isinstance(payload.get("extensions"), dict) and "controlnet" in payload["extensions"]:
        block = payload["extensions"]["controlnet"]
        return (deepcopy(block) if isinstance(block, dict) else {}, True)
    return deepcopy(payload), False


def migrate_legacy_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = deepcopy(raw or {})
    block, used_legacy_extension_key = _extract_extension_block(payload)
    legacy_keys = {
        "controlnet_name",
        "controlnet_preprocessor",
        "controlnet_strength",
        "controlnet_units",
        "controlnet_stack_enabled",
        "control_image_name",
    }
    if isinstance(block, dict) and not legacy_keys.intersection(block) and "enabled" in block:
        if used_legacy_extension_key:
            metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
            metadata["legacy_migrated"] = True
            block["metadata"] = metadata
        return block

    source = block if isinstance(block, dict) else payload
    units = source.get("controlnet_units") if isinstance(source.get("controlnet_units"), list) else []
    legacy_unit: dict[str, Any] = {}
    for old, new in LEGACY_UNIT_KEY_MAP.items():
        if old in source and new != "control_image_name":
            legacy_unit[new] = source.get(old)
    if not units and legacy_unit:
        legacy_unit["enabled"] = True
        units = [legacy_unit]
    assets: dict[str, Any] = {}
    if source.get("control_image_name"):
        assets["control_images"] = {"unit_1": source.get("control_image_name")}

    if legacy_keys.intersection(source) or used_legacy_extension_key:
        return extension_block(
            enabled=_as_bool(source.get("controlnet_stack_enabled"), bool(units) or _as_bool(source.get("enabled"), False)),
            inputs={"units": units},
            assets=assets,
            metadata={"schema": SCHEMA, "legacy_migrated": True},
        )
    return source


def _sanitize_params(raw_params: dict[str, Any] | None, active_units: list[dict[str, Any]]) -> dict[str, Any]:
    raw = raw_params if isinstance(raw_params, dict) else {}
    batch_policy = str(raw.get("batch_policy") or "auto")
    if batch_policy not in VALID_BATCH_MODES:
        batch_policy = "auto"
    task = normalize_controlnet_task(str(raw.get("controlnet_task") or TASK_MAP_CONTROL))
    params = {
        "advanced_controlnet_requested": any(unit.get("advanced_enabled") for unit in active_units),
        "batch_policy": batch_policy,
        "controlnet_task": task,
    }
    qwen_adapter = str(raw.get("qwen_controlnet_adapter") or raw.get("controlnet_qwen_adapter") or raw.get("qwen_cn_adapter") or "").strip().lower()
    if qwen_adapter:
        if qwen_adapter in {"diffsynth", "diff_synth", "model_patch", "model-patch", "patch"}:
            params["qwen_controlnet_adapter"] = "diffsynth"
        elif qwen_adapter in {"instantx", "instant_x", "native_controlnet", "controlnet"}:
            params["qwen_controlnet_adapter"] = "instantx"
        else:
            params["qwen_controlnet_adapter"] = "auto"
    flux_adapter = str(raw.get("flux_controlnet_adapter") or raw.get("controlnet_flux_adapter") or raw.get("flux_cn_adapter") or "").strip().lower()
    if flux_adapter:
        params["flux_controlnet_adapter"] = "alimama" if flux_adapter in {"alimama", "flux_inpaint", "flux-controlnet-inpaint", "inpaint", "controlnet"} else "auto"
    return params


def _sanitize_assets(raw_assets: dict[str, Any] | None, active_units: list[dict[str, Any]], *, controlnet_task: str = TASK_MAP_CONTROL) -> dict[str, Any]:
    raw = raw_assets if isinstance(raw_assets, dict) else {}
    active_uids = {str(unit.get("uid")) for unit in active_units}
    sanitized: dict[str, Any] = {}
    for key in VALID_ASSET_KEYS:
        value = raw.get(key)
        if isinstance(value, dict):
            filtered = {str(uid): deepcopy(asset) for uid, asset in value.items() if str(uid) in active_uids or str(uid) in {"default", "primary"}}
            if filtered:
                sanitized[key] = filtered
        elif isinstance(value, list):
            if value and active_uids:
                sanitized[key] = deepcopy(value)
        elif value and active_uids:
            sanitized[key] = deepcopy(value)
    # Map-control masks stay opt-in by unit. Inpaint/outpaint task assets keep
    # masks because the future adapters consume source/canvas masks directly.
    if controlnet_task == TASK_MAP_CONTROL and "control_masks" in sanitized and not any(unit.get("mask_mode") == "control_mask" for unit in active_units):
        sanitized.pop("control_masks", None)
    return sanitized


def _sanitize_metadata(raw_metadata: dict[str, Any] | None, route: dict[str, Any] | None, *, reason: str | None = None, requested: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw_metadata if isinstance(raw_metadata, dict) else {}
    metadata = {key: deepcopy(value) for key, value in raw.items() if key in VALID_METADATA_KEYS and not str(key).startswith("_neo_")}
    metadata["schema"] = SCHEMA
    if route:
        metadata["route"] = deepcopy(route)
        metadata["route_state"] = route.get("route_state")
    if raw.get("controlnet_task") in CONTROLNET_TASKS:
        metadata["controlnet_task"] = normalize_controlnet_task(str(raw.get("controlnet_task")))
    if reason:
        metadata["reason"] = reason
    if requested is not None:
        metadata["requested"] = _summarize_requested(requested)
    metadata["payload_hardening"] = "phase_l_task_contract"
    metadata["asset_resolver_phase"] = "N"
    return metadata


def _summarize_requested(requested: dict[str, Any]) -> dict[str, Any]:
    """Keep a safe diagnostic summary without replaying hidden user fields."""
    summary: dict[str, Any] = {}
    if "enabled" in requested:
        summary["enabled"] = _as_bool(requested.get("enabled"), False)
    units = []
    raw_units = requested.get("inputs", {}).get("units") if isinstance(requested.get("inputs"), dict) else requested.get("units")
    if isinstance(raw_units, list):
        for raw in raw_units:
            if isinstance(raw, dict):
                units.append({"uid": str(raw.get("uid") or ""), "unit": str(raw.get("unit") or ""), "preprocessor": str(raw.get("preprocessor") or ""), "enabled": _as_bool(raw.get("enabled"), True)})
    if units:
        summary["units"] = units
    return summary


def extension_block(*, enabled: bool, inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    block = {
        "enabled": bool(enabled),
        "version": VERSION,
        "inputs": deepcopy(inputs or {}),
        "params": deepcopy(params or {}),
        "assets": deepcopy(assets or {}),
        "metadata": deepcopy(metadata or {}),
    }
    if not block["enabled"]:
        block["inputs"] = {}
        block["params"] = {}
        block["assets"] = {}
    return block


def payload_wrapper(block: dict[str, Any]) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: block}}


def disabled_block(reason: str = "disabled", *, route: dict[str, Any] | None = None, requested: dict[str, Any] | None = None) -> dict[str, Any]:
    return extension_block(enabled=False, metadata=_sanitize_metadata({}, route, reason=reason, requested=requested))


def disabled_payload(reason: str = "disabled", *, route: dict[str, Any] | None = None, requested: dict[str, Any] | None = None) -> dict[str, Any]:
    return payload_wrapper(disabled_block(reason, route=route, requested=requested))


def normalize_block(
    raw: dict[str, Any] | None,
    *,
    route: dict[str, Any] | None = None,
    enforce_route_state: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    block = migrate_legacy_payload(raw)
    enabled = _as_bool(block.get("enabled"), False) if isinstance(block, dict) else False
    if not enabled:
        return disabled_block("disabled", route=route, requested=block if isinstance(block, dict) else None), []

    route_state_value = (route or {}).get("route_state")
    if enforce_route_state and route_state_value and route_state_value not in ACTIVE_STATES:
        return disabled_block(route_reason(str(route_state_value)), route=route, requested=block if isinstance(block, dict) else None), []

    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    units, notes = normalize_units(inputs.get("units") or block.get("units") or [])
    active_units = [unit for unit in units if unit.get("enabled")]
    raw_params = block.get("params") if isinstance(block.get("params"), dict) else {}
    controlnet_task = normalize_controlnet_task(str(raw_params.get("controlnet_task") or TASK_MAP_CONTROL))
    if not active_units:
        return disabled_block("no_active_units", route=route, requested=block if isinstance(block, dict) else None), notes

    params = _sanitize_params(raw_params, active_units)
    assets = _sanitize_assets(block.get("assets") if isinstance(block.get("assets"), dict) else {}, active_units, controlnet_task=params.get("controlnet_task") or controlnet_task)
    metadata_source = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    metadata = _sanitize_metadata({**metadata_source, "controlnet_task": controlnet_task}, route)
    return extension_block(
        enabled=True,
        inputs={"units": active_units},
        params=params,
        assets=assets,
        metadata=metadata,
    ), notes


def normalize_payload(
    raw: dict[str, Any] | None,
    *,
    route: dict[str, Any] | None = None,
    enforce_route_state: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    block, notes = normalize_block(raw, route=route, enforce_route_state=enforce_route_state)
    return payload_wrapper(block), notes
