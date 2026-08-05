from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable

from neo_extensions.built_in.ip_adapter.backend.payload_schema import normalize_block as normalize_ip_adapter_block

FORGE_IP_ADAPTER_SCHEMA_ID = "neo.provider.forge_ip_adapter_remap.v2"
FORGE_IP_ADAPTER_VERSION = "2.0.0"
SUPPORTED_FAMILIES = {"sd15", "sdxl"}
SUPPORTED_MODES = {"txt2img", "img2img", "inpaint"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _display_name(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]


def _model_parse_name(value: Any) -> str:
    name = _display_name(value)
    return re.sub(r"\s*\[[0-9a-fA-F]{6,}\]\s*$", "", name).strip()


def _match_catalog(requested: Any, available: Iterable[str]) -> str:
    wanted = _norm(_model_parse_name(requested))
    if not wanted:
        return ""
    rows = [str(item) for item in available if str(item or "").strip()]
    for item in rows:
        if _norm(_model_parse_name(item)) == wanted:
            return item
    for item in rows:
        current = _norm(_model_parse_name(item))
        if current and (wanted in current or current in wanted):
            return item
    return ""


def _faceid_module(variant: str, modules: Iterable[str]) -> str:
    rows = [str(item) for item in modules if str(item or "").strip()]
    scored: list[tuple[int, str]] = []
    for module in rows:
        compact = _norm(module)
        score = 0
        if variant == "instantid":
            if "instantid" in compact:
                score = 100
            elif "insightface" in compact and "instant" in compact:
                score = 90
        else:
            if "faceid" in compact:
                score = 100
            elif "insightface" in compact and "ipadapter" in compact:
                score = 95
            elif "insightface" in compact and any(token in compact for token in ("clip", "identity")):
                score = 90
        if score:
            scored.append((score, module))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], item[1].casefold()))
    return scored[0][1]


def classify_forge_ip_adapter_model(
    name: Any,
    modules: Iterable[str] = (),
    encoders: Iterable[str] = (),
    *,
    source: str = "live_controlnet",
) -> dict[str, Any]:
    catalog_name = str(name or "").strip()
    parse_name = _model_parse_name(catalog_name)
    compact = _norm(parse_name)
    is_faceid = "faceid" in compact or "instantid" in compact
    is_ip_adapter = "ipadapter" in compact or is_faceid
    family = ""
    if "sdxl" in compact or re.search(r"(?:^|[^a-z0-9])xl(?:[^a-z0-9]|$)", parse_name.casefold()):
        family = "sdxl"
    elif any(token in compact for token in ("sd15", "sd1p5")) or re.search(r"sd[ _.-]?1[._-]?5", parse_name.casefold()):
        family = "sd15"

    variant = ""
    if is_faceid:
        variant = "instantid" if "instantid" in compact else "faceid"
    elif is_ip_adapter:
        variant = "plus" if "plus" in compact else "standard"

    required_module = ""
    matched_module = ""
    if is_faceid:
        matched_module = _faceid_module(variant, modules)
        required_module = matched_module or ("InstantID" if variant == "instantid" else "InsightFace + IPAdapter")
    elif is_ip_adapter and family == "sdxl":
        required_module = "CLIP-ViT-H (IPAdapter)" if any(token in compact for token in ("vith", "plus")) else "CLIP-ViT-bigG (IPAdapter)"
    elif is_ip_adapter and family == "sd15":
        required_module = "CLIP-ViT-bigG (IPAdapter)" if any(token in compact for token in ("vitg", "bigg")) else "CLIP-ViT-H (IPAdapter)"

    if required_module and not is_faceid:
        matched_module = _match_catalog(required_module, modules)
        if not matched_module:
            required_kind = "bigg" if "bigg" in _norm(required_module) else "vith"
            for module in modules:
                compact_module = _norm(module)
                if "ipadapter" not in compact_module:
                    continue
                if required_kind == "bigg" and any(token in compact_module for token in ("bigg", "vitg", "clipg")):
                    matched_module = str(module)
                    break
                if required_kind == "vith" and any(token in compact_module for token in ("vith", "cliph", "visionh")):
                    matched_module = str(module)
                    break
            if not matched_module:
                automatic = [str(module) for module in modules if _norm(module) in {"ipadapter", "ipadapterauto", "ipadapterautomatic"}]
                if automatic:
                    matched_module = automatic[0]
            if not matched_module:
                generic = [str(module) for module in modules if "ipadapter" in _norm(module) and "insightface" not in _norm(module)]
                if len(generic) == 1:
                    matched_module = generic[0]

    required_encoder_kind = ""
    if required_module and not is_faceid:
        required_encoder_kind = "bigg" if "bigg" in _norm(required_module) else "vith"
    matched_encoder = ""
    for encoder in encoders:
        compact_encoder = _norm(encoder)
        if required_encoder_kind == "bigg" and any(token in compact_encoder for token in ("bigg", "vitg", "clipvisiong", "clipg")):
            matched_encoder = str(encoder)
            break
        if required_encoder_kind == "vith" and any(token in compact_encoder for token in ("vith", "clipvisionh", "cliph")):
            matched_encoder = str(encoder)
            break

    supported = bool(is_ip_adapter and family in SUPPORTED_FAMILIES and matched_module)
    reason = ""
    if not is_ip_adapter:
        reason = "not_ip_adapter_model"
    elif not family:
        reason = "family_ambiguous"
    elif not matched_module:
        reason = "required_faceid_preprocessor_missing" if is_faceid else "required_preprocessor_missing"
    elif is_faceid and variant == "instantid":
        reason = "supported_instantid"
    elif is_faceid:
        reason = "supported_faceid"
    elif supported:
        reason = "supported_standard_ip_adapter"
    return {
        "catalog_name": catalog_name,
        "name": parse_name,
        "family": family,
        "variant": variant,
        "required_module": matched_module or required_module,
        "required_encoder_kind": required_encoder_kind,
        "matched_encoder": matched_encoder,
        "source": str(source or "live_controlnet"),
        "supported": supported,
        "reason": reason,
    }


def build_forge_ip_adapter_capability(
    *,
    controlnet_available: bool,
    controlnet_script_name: str,
    controlnet_modes: Iterable[str],
    controlnet_slots_by_mode: dict[str, Any] | None,
    control_models: Iterable[str],
    control_modules: Iterable[str],
    shared_models: Iterable[str] = (),
    shared_encoders: Iterable[str] = (),
    shared_path_reference_ready: bool = False,
) -> dict[str, Any]:
    modules = [str(item) for item in control_modules if str(item or "").strip()]
    encoders = [str(item) for item in shared_encoders if str(item or "").strip()]
    live_models = [str(item) for item in control_models if str(item or "").strip()]
    verified_shared_models = [str(item) for item in shared_models if str(item or "").strip()] if shared_path_reference_ready else []
    catalog_rows: list[tuple[str, str]] = [(item, "live_controlnet") for item in live_models]
    seen = {_norm(_model_parse_name(item)) for item in live_models}
    for item in verified_shared_models:
        key = _norm(_model_parse_name(item))
        if key and key not in seen:
            seen.add(key)
            catalog_rows.append((item, "shared_comfy_paths"))

    records = [classify_forge_ip_adapter_model(item, modules, encoders, source=source) for item, source in catalog_rows]
    supported = [item for item in records if item.get("supported")]
    standard = [item for item in supported if item.get("variant") not in {"faceid", "instantid"}]
    faceid = [item for item in supported if item.get("variant") in {"faceid", "instantid"}]
    standard_by_family = {family: [item for item in standard if item.get("family") == family] for family in sorted(SUPPORTED_FAMILIES)}
    faceid_by_family = {family: [item for item in faceid if item.get("family") == family] for family in sorted(SUPPORTED_FAMILIES)}
    available_modes = sorted({str(item) for item in controlnet_modes if str(item) in {"txt2img", "img2img"}})
    standard_available = bool(controlnet_available and standard and available_modes)
    faceid_available = bool(controlnet_available and faceid and available_modes)
    available = bool(standard_available or faceid_available)
    matched_preprocessors = sorted({str(item.get("required_module") or "") for item in supported if item.get("required_module")})
    faceid_preprocessors = sorted({str(item.get("required_module") or "") for item in faceid if item.get("required_module")})

    if not controlnet_available:
        blocker = "controlnet_contract_unavailable"
        reason = "Forge Integrated ControlNet API contract is unavailable."
    elif not available_modes:
        blocker = "controlnet_modes_unavailable"
        reason = "Forge Integrated ControlNet did not expose txt2img/img2img unit slots."
    elif not records:
        blocker = "ip_adapter_models_not_discovered"
        reason = "No IP-Adapter or FaceID model was found in Forge's live ControlNet catalog or verified shared paths."
    elif not supported:
        blocker = "matching_preprocessor_missing"
        reason = "IP-Adapter model files were discovered, but Forge did not expose a matching IP-Adapter preprocessor or compatible FaceID preprocessor."
    else:
        blocker = ""
        modes = []
        if standard_available:
            modes.append("standard IP-Adapter")
        if faceid_available:
            modes.append("FaceID/InstantID")
        reason = "Forge Integrated ControlNet verified " + " and ".join(modes) + "."

    return {
        "available": available,
        "standard_available": standard_available,
        "detected": bool(records),
        "blocker": blocker,
        "mode": "controlnet_ip_adapter",
        "contract": "forge.controlnet.ip_adapter.v2" if available else "unverified",
        "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID,
        "version": FORGE_IP_ADAPTER_VERSION,
        "script_name": str(controlnet_script_name or "ControlNet"),
        "available_modes": available_modes,
        "unit_slots_by_mode": deepcopy(controlnet_slots_by_mode or {}),
        "max_units": max([int(v or 0) for v in (controlnet_slots_by_mode or {}).values()] or [0]),
        "models": standard,
        "models_by_family": standard_by_family,
        "model_names": [str(item.get("catalog_name") or "") for item in standard],
        "faceid_records": faceid,
        "faceid_models_by_family": faceid_by_family,
        "faceid_models": [str(item.get("catalog_name") or "") for item in faceid],
        "faceid_available": faceid_available,
        "instantid_available": any(item.get("variant") == "instantid" for item in faceid),
        "faceid_preprocessors": faceid_preprocessors,
        "live_model_names": live_models,
        "shared_model_names": verified_shared_models,
        "shared_encoder_names": encoders,
        "shared_path_reference_ready": bool(shared_path_reference_ready),
        "diagnostics": {
            "controlnet_contract_ready": bool(controlnet_available),
            "live_catalog_model_count": len(live_models),
            "shared_model_count": len(verified_shared_models),
            "shared_encoder_count": len(encoders),
            "controlnet_module_count": len(modules),
            "compatible_standard_model_count": len(standard),
            "compatible_faceid_model_count": len(faceid),
            "compatible_preprocessor_count": len(matched_preprocessors),
            "model_authority": "live_controlnet_plus_verified_shared_paths",
            "faceid_authority": "live_model_and_preprocessor_pair_required",
        },
        "faceid_detected": any(item.get("variant") in {"faceid", "instantid"} for item in records),
        "preprocessors": matched_preprocessors,
        "reason": reason,
    }


def _asset_reference(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("ref", "path", "stored_path", "source_path", "file", "value", "url", "data_uri", "image"):
            resolved = _asset_reference(value.get(key))
            if resolved:
                return resolved
    if isinstance(value, list):
        for item in value:
            resolved = _asset_reference(item)
            if resolved:
                return resolved
    return ""


def _unit_images(assets: dict[str, Any], unit: dict[str, Any]) -> list[str]:
    refs = _as_dict(assets.get("reference_images"))
    uid = str(unit.get("uid") or "")
    value = refs.get(uid)
    if value is None and uid == "primary":
        value = refs.get("primary")
    if value is None:
        value = refs.get("default")
    rows = value if isinstance(value, list) else [value]
    result: list[str] = []
    for row in rows:
        ref = _asset_reference(row)
        if ref and ref not in result:
            result.append(ref)
    if not result:
        for name in _as_list(unit.get("image_names")):
            text = str(name or "").strip()
            if text and text not in result:
                result.append(text)
        text = str(unit.get("image_name") or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def compile_forge_ip_adapter_units(
    block: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    mode: str,
    family: str,
    image_encoder,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return [], {"enabled": False, "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("ip_adapter"))
    if not capability.get("available"):
        return [], {"enabled": False, "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID}, [str(capability.get("reason") or "Forge IP-Adapter capability is unavailable.")]
    family = str(family or "").strip().lower()
    if family not in SUPPORTED_FAMILIES:
        return [], {"enabled": False, "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID}, [f"Forge IP-Adapter supports SD1.5/SDXL only; {family or 'unknown'} remains gated."]
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    if mode not in SUPPORTED_MODES or (capability.get("available_modes") and script_mode not in set(capability.get("available_modes") or [])):
        return [], {"enabled": False, "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID}, [f"Forge IP-Adapter is unavailable for {mode}."]

    route = {"backend": "forge", "family": family, "loader": "checkpoint", "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "experimental_available"}
    normalized, notes = normalize_ip_adapter_block({"extensions": {"image.ip_adapter": block}}, route=route)
    units = _as_list(_as_dict(normalized.get("inputs")).get("units"))
    assets = _as_dict(normalized.get("assets"))
    standard_records = [item for item in capability.get("models") or [] if isinstance(item, dict) and item.get("family") == family and item.get("supported")]
    faceid_records = [item for item in capability.get("faceid_records") or [] if isinstance(item, dict) and item.get("family") == family and item.get("supported")]
    compiled: list[dict[str, Any]] = []
    errors: list[str] = []
    faceid_count = 0
    standard_count = 0
    normalization_notes = list(notes)

    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or unit.get("enabled") is False:
            continue
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        unit_mode = str(unit.get("mode") or "standard").strip().casefold()
        records = faceid_records if unit_mode == "faceid" else standard_records
        requested_model = str(unit.get("faceid_model") if unit_mode == "faceid" else unit.get("model") or "").strip()
        model_names = [str(item.get("catalog_name") or "") for item in records]
        catalog_model = _match_catalog(requested_model, model_names)
        record = next((item for item in records if str(item.get("catalog_name") or "") == catalog_model), None)
        if not record:
            label = "FaceID" if unit_mode == "faceid" else "IP-Adapter"
            errors.append(f"Forge {label} model is unavailable or incompatible with {family}: {requested_model or '(not selected)' }.")
            continue
        images = _unit_images(assets, unit)
        if len(images) != 1:
            errors.append(f"Forge IP-Adapter unit {uid} requires exactly one reference image; use multiple Neo units for multiple references.")
            continue
        weight = unit.get("weight_faceidv2") if unit_mode == "faceid" else unit.get("weight")
        compiled.append({
            "enabled": True,
            "module": str(record.get("required_module") or "None"),
            "model": catalog_model,
            "weight": float(weight if weight is not None else 1.0),
            "image": image_encoder(images[0], label=f"IP Adapter {uid} reference"),
            "resize_mode": "Crop and Resize",
            "processor_res": 512,
            "threshold_a": -1.0,
            "threshold_b": -1.0,
            "guidance_start": float(unit.get("start_at") if unit.get("start_at") is not None else 0.0),
            "guidance_end": float(unit.get("end_at") if unit.get("end_at") is not None else 1.0),
            "pixel_perfect": False,
            "control_mode": "Balanced",
            "save_detected_map": False,
        })
        if unit_mode == "faceid":
            faceid_count += 1
            normalization_notes.append({
                "level": "info",
                "field": f"inputs.units[{index}]",
                "message": "Forge executes FaceID through its verified Integrated ControlNet model/preprocessor pair. Comfy-only FaceID preset, provider, and LoRA-strength fields are not sent to Forge.",
            })
        else:
            standard_count += 1

    meta = {
        "enabled": bool(compiled),
        "schema_id": FORGE_IP_ADAPTER_SCHEMA_ID,
        "version": FORGE_IP_ADAPTER_VERSION,
        "script_name": str(capability.get("script_name") or "ControlNet"),
        "unit_count": len(compiled),
        "standard_unit_count": standard_count,
        "faceid_unit_count": faceid_count,
        "family": family,
        "mode": mode,
        "notes": normalization_notes,
        "faceid_available": bool(capability.get("faceid_available")),
        "instantid_available": bool(capability.get("instantid_available")),
        "multi_reference_per_unit_gated": True,
    }
    return compiled, meta, errors
