from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "cfg_fix_dynamic_thresholding"
VERSION = 1
ALLOWED_PRESETS = ("off", "safe", "detail_push", "aggressive", "smart_auto", "advanced")
ALLOWED_MODES = ("simple", "full")
ACTIVE_ROUTE_STATES = {"available", "experimental_available"}
GATED_ROUTE_STATES = {"planned_gated", "provider_gated", "unsupported"}
LOW_CFG_SKIP_THRESHOLD = 7.0

DEFAULT_PARAMS = {
    "preset": "off",
    "mode": "simple",
    "mimic_scale": 7.0,
    "threshold_percentile": 1.0,
    "custom_values": False,
    "auto_disable_low_cfg": True,
    "auto_disable_family": True,
}

PRESET_DEFAULTS = {
    "off": {"enabled": False, "mimic_scale": 7.0, "threshold_percentile": 1.0},
    "safe": {"enabled": True, "mimic_scale": 7.0, "threshold_percentile": 1.0},
    "detail_push": {"enabled": True, "mimic_scale": 7.0, "threshold_percentile": 0.99},
    "aggressive": {"enabled": True, "mimic_scale": 6.0, "threshold_percentile": 0.98},
    "advanced": {"enabled": True, "mimic_scale": 7.0, "threshold_percentile": 0.99},
}

FULL_MODE_DEFAULTS = {
    "mimic_mode": "Half Cosine Up",
    "mimic_scale_min": 3.5,
    "cfg_mode": "Half Cosine Up",
    "cfg_scale_min": 3.5,
    "sched_val": 1.0,
    "separate_feature_channels": "disable",
    "scaling_startpoint": "MEAN",
    "variability_measure": "AD",
    "interpolate_phi": 1.0,
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


def clamp_float(value: Any, *, minimum: float, maximum: float, default: float, precision: int = 2) -> float:
    number = _as_float(value, default)
    number = max(minimum, min(maximum, number))
    return round(number, precision)


def resolve_preset_values(preset: str, cfg: Any = 7.0) -> dict[str, Any]:
    cfg_value = _as_float(cfg, 7.0)
    if preset == "smart_auto":
        if cfg_value >= 16:
            return {"enabled": True, "mimic_scale": 6.0, "threshold_percentile": 0.98}
        if cfg_value >= 12:
            return {"enabled": True, "mimic_scale": 7.0, "threshold_percentile": 0.99}
        if cfg_value >= 8:
            return {"enabled": True, "mimic_scale": 7.5, "threshold_percentile": 1.0}
        return {"enabled": True, "mimic_scale": 7.0, "threshold_percentile": 1.0}
    return deepcopy(PRESET_DEFAULTS.get(preset, PRESET_DEFAULTS["off"]))


def normalize_params(raw_params: dict[str, Any] | None, *, cfg: Any = 7.0) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Normalize user/browser params without deciding route/node availability.

    Returns clean active params plus validation notes. Invalid enum values are normalized
    to safe defaults and reported as errors so callers can choose whether to disable.
    """
    raw = {**DEFAULT_PARAMS, **(raw_params or {})}
    validation: list[dict[str, str]] = []

    preset = str(raw.get("preset") or DEFAULT_PARAMS["preset"])
    if preset not in ALLOWED_PRESETS:
        validation.append({"level": "error", "field": "preset", "message": f"Unknown CFG Fix preset: {preset}"})
        preset = DEFAULT_PARAMS["preset"]

    mode = str(raw.get("mode") or DEFAULT_PARAMS["mode"])
    if mode not in ALLOWED_MODES:
        validation.append({"level": "error", "field": "mode", "message": f"Unknown CFG Fix mode: {mode}"})
        mode = DEFAULT_PARAMS["mode"]

    custom_values = _as_bool(raw.get("custom_values"), DEFAULT_PARAMS["custom_values"])
    preset_values = resolve_preset_values(preset, cfg)
    mimic_source = raw.get("mimic_scale") if custom_values else preset_values["mimic_scale"]
    percentile_source = raw.get("threshold_percentile") if custom_values else preset_values["threshold_percentile"]

    mimic_scale = clamp_float(mimic_source, minimum=1.0, maximum=30.0, default=7.0, precision=1)
    threshold_percentile = clamp_float(percentile_source, minimum=0.8, maximum=1.0, default=1.0, precision=2)

    if custom_values:
        if _as_float(raw.get("mimic_scale"), mimic_scale) != mimic_scale:
            validation.append({"level": "warning", "field": "mimic_scale", "message": "Mimic CFG was clamped to the supported 1.0–30.0 range."})
        if _as_float(raw.get("threshold_percentile"), threshold_percentile) != threshold_percentile:
            validation.append({"level": "warning", "field": "threshold_percentile", "message": "Threshold percentile was clamped to the supported 0.80–1.00 range."})

    # V1 parity note: do not warn/block when mimic CFG equals generation CFG and
    # threshold percentile is 1.00. In Full mode, the node can still affect sampling
    # through its schedule/minimum CFG parameters, and V1 allowed this exact setup.

    return {
        "preset": preset,
        "mode": mode,
        "mimic_scale": mimic_scale,
        "threshold_percentile": threshold_percentile,
        "custom_values": custom_values,
        "auto_disable_low_cfg": _as_bool(raw.get("auto_disable_low_cfg"), True),
        "auto_disable_family": _as_bool(raw.get("auto_disable_family"), True),
    }, validation


def extension_block(*, enabled: bool, params: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "version": VERSION,
        "inputs": {},
        "params": params or {},
        "assets": {},
        "metadata": metadata or {},
    }


def payload_wrapper(block: dict[str, Any]) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: block}}


def disabled_block(reason: str = "disabled", *, route: dict[str, Any] | None = None, node_status: dict[str, Any] | None = None, requested: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"reason": reason}
    if route:
        metadata["route"] = deepcopy(route)
        if route.get("route_state"):
            metadata["route_state"] = route.get("route_state")
    if node_status:
        metadata["node_status"] = deepcopy(node_status)
    if requested:
        metadata["requested"] = {
            key: requested.get(key)
            for key in ("preset", "mode", "custom_values")
            if key in requested
        }
    return extension_block(enabled=False, params={}, metadata=metadata)


def disabled_payload(reason: str = "disabled", *, route: dict[str, Any] | None = None, node_status: dict[str, Any] | None = None, requested: dict[str, Any] | None = None) -> dict[str, Any]:
    return payload_wrapper(disabled_block(reason, route=route, node_status=node_status, requested=requested))


def active_block(params: dict[str, Any], *, route: dict[str, Any], node_status: dict[str, Any] | None = None) -> dict[str, Any]:
    node = "DynamicThresholdingFull" if params.get("mode") == "full" else "DynamicThresholdingSimple"
    metadata: dict[str, Any] = {
        "node": node,
        "route": deepcopy(route),
        "route_state": route.get("route_state"),
    }
    if node_status is not None:
        metadata["node_status"] = deepcopy(node_status)
    if params.get("mode") == "full":
        metadata["full_mode_defaults"] = deepcopy(FULL_MODE_DEFAULTS)
    return extension_block(enabled=True, params=params, metadata=metadata)
