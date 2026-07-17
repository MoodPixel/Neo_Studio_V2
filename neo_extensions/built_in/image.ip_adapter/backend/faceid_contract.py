from __future__ import annotations

from pathlib import PurePath
import re
from typing import Any


SCHEMA_VERSION = "neo.image.ip_adapter.faceid_execution.v1"
UNIFIED_LOADER = "IPAdapterUnifiedLoaderFaceID"


def _basename(value: Any) -> str:
    return PurePath(str(value or "").strip().replace("\\", "/")).name


def _tokens(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _basename(value).casefold()).strip("_")


def classify_faceid_model(value: Any) -> dict[str, str]:
    """Classify a FaceID filename without depending on a private model path."""

    name = _basename(value)
    tokenized = _tokens(name)
    compact = tokenized.replace("_", "")
    family = ""
    if "sdxl" in compact:
        family = "sdxl"
    elif any(marker in compact for marker in ("sd15", "sd1_5", "sd1.5")):
        family = "sd15"

    variant = ""
    if "portrait" in compact and "unnorm" in compact:
        variant = "faceid_portrait_unnorm"
    elif "portrait" in compact:
        variant = "faceid_portrait"
    elif "plusv2" in compact or "plus2" in compact:
        variant = "faceid_plus_v2"
    elif "plus" in compact:
        variant = "faceid_plus"
    elif "faceid" in compact:
        variant = "faceid"
    return {"basename": name, "family": family, "variant": variant}


def classify_faceid_preset(value: Any) -> str:
    preset = " ".join(str(value or "FACEID PLUS V2").strip().upper().split())
    return {
        "FACEID": "faceid",
        "FACEID PLUS": "faceid_plus",
        "FACEID PLUS V2": "faceid_plus_v2",
        "FACEID PORTRAIT": "faceid_portrait",
        "FACEID PORTRAIT UNNORM": "faceid_portrait_unnorm",
    }.get(preset, "")


def _node_input_names(object_info: Any, node_name: str) -> set[str]:
    if not isinstance(object_info, dict):
        return set()
    node = object_info.get(node_name) or {}
    inputs = node.get("input") or {}
    return {
        str(name)
        for bucket in (inputs.get("required") or {}, inputs.get("optional") or {})
        for name in bucket.keys()
    }


def inspect_faceid_loader(object_info: Any) -> dict[str, Any]:
    names = {str(name) for name in object_info.keys()} if isinstance(object_info, dict) else {
        str(name) for name in (object_info or [])
    }
    if object_info is None:
        names = {UNIFIED_LOADER}
    inputs = _node_input_names(object_info, UNIFIED_LOADER)
    available = UNIFIED_LOADER in names
    preset_owned = available and (not inputs or "preset" in inputs)
    return {
        "loader_node": UNIFIED_LOADER if available else "",
        "loader_strategy": "unified_preset_resolution" if preset_owned else "unsupported",
        "model_selection_mode": "validated_resolution_assertion" if preset_owned else "unsupported",
        "input_names": sorted(inputs),
    }


def resolve_faceid_execution_contract(unit: dict[str, Any], family: str, object_info: Any) -> dict[str, Any]:
    loader = inspect_faceid_loader(object_info)
    requested = classify_faceid_model(unit.get("faceid_model"))
    preset = " ".join(str(unit.get("faceid_preset") or "FACEID PLUS V2").strip().upper().split())
    preset_variant = classify_faceid_preset(preset)
    route_family = str(family or "").strip().casefold()
    errors: list[dict[str, str]] = []

    def fail(code: str, field: str, message: str) -> None:
        errors.append({"code": code, "field": field, "message": message})

    if loader["loader_strategy"] != "unified_preset_resolution":
        fail("faceid_loader_contract_unavailable", "nodes.faceid", "The installed FaceID loader does not expose the supported unified preset contract.")
    if not requested["basename"]:
        fail("faceid_model_required", "faceid_model", "FaceID mode requires a selected FaceID model file.")
    elif not requested["family"] or not requested["variant"]:
        fail("faceid_model_name_not_resolvable", "faceid_model", "The selected FaceID filename does not declare a supported checkpoint family and FaceID variant, so the unified loader cannot be verified.")
    if not preset_variant:
        fail("faceid_preset_unsupported", "faceid_preset", f"Unsupported FaceID preset: {preset or '(empty)' }.")
    if requested["family"] and route_family in {"sd15", "sdxl"} and requested["family"] != route_family:
        fail("faceid_model_family_mismatch", "faceid_model", f"Selected FaceID model is {requested['family'].upper()}, but the active checkpoint family is {route_family.upper()}.")
    if requested["variant"] and preset_variant and requested["variant"] != preset_variant:
        fail("faceid_model_preset_mismatch", "faceid_preset", f"Selected model is {requested['variant'].replace('_', ' ')}, but the active preset is {preset}.")
    if preset_variant == "faceid_plus" and route_family != "sd15":
        fail("faceid_preset_family_mismatch", "faceid_preset", "FACEID PLUS is supported only on the SD1.5 checkpoint route.")
    if preset_variant == "faceid_portrait_unnorm" and route_family != "sdxl":
        fail("faceid_preset_family_mismatch", "faceid_preset", "FACEID PORTRAIT UNNORM is supported only on the SDXL checkpoint route.")

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "loader_node": loader["loader_node"],
        "loader_strategy": loader["loader_strategy"],
        "model_selection_mode": loader["model_selection_mode"],
        "requested_model": requested["basename"],
        "route_family": route_family,
        "model_family": requested["family"],
        "model_variant": requested["variant"],
        "preset": preset,
        "preset_variant": preset_variant,
        "provider": str(unit.get("faceid_provider") or "CUDA").strip(),
        "lora_strength": float(unit.get("faceid_lora_strength", 0.75)),
        "selection_consumption": "validated_for_unified_auto_resolution" if not errors else "blocked_before_graph_mutation",
        "errors": errors,
    }
