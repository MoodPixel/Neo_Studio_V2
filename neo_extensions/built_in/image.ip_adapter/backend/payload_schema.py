from __future__ import annotations
from copy import deepcopy
from typing import Any

from .support_matrix import ACTIVE_STATES, route_reason, route_state

EXTENSION_ID = "image.ip_adapter"
VERSION = 1
VALID_MODES = {"standard", "faceid"}
VALID_WEIGHT_TYPES = {"linear", "ease in", "ease out", "ease in-out", "reverse in-out", "weak input", "weak output", "weak middle", "strong middle", "style transfer", "composition", "strong style transfer", "style and composition", "strong style and composition"}
VALID_COMBINE = {"concat", "add", "subtract", "average", "norm average"}
VALID_EMBEDS_SCALING = {"V only", "K+V", "K+V w/ C penalty", "K+mean(V) w/ C penalty"}
VISIBLE_UNIT_KEYS = {
    "uid", "enabled", "mode", "model", "clip_vision", "image_name", "image_names", "weight", "weight_faceidv2",
    "weight_type", "combine_embeds", "embeds_scaling", "start_at", "end_at", "faceid_model", "faceid_preset", "faceid_provider",
    "faceid_lora_strength", "image_field",
}
VALID_INPUT_KEYS = {"units", "mode", "suppress_global_when_scene_director_active"}
VALID_ASSET_KEYS = {"reference_images"}
VALID_METADATA_KEYS = {"schema", "route", "route_state", "reason", "legacy_migrated", "scene_director_bound_slots", "scene_director_suppressed", "scene_director_identity_units_consumed", "scene_director_identity_units_blocked", "scene_director_regional_ipadapter_hard_gated", "scene_director_regional_ipadapter_units_suppressed", "scene_director_regional_ipadapter_profiles_metadata_only", "scene_director_ipadapter_hard_gate_phase"}



def _clean_select_placeholder(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"select_ip_adapter_model_later", "select_clip_vision_later", "select_faceid_model_later", "no_ip_adapter_models_found", "no_faceid_models_found", "no_clip_vision_models_found"} else text

def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}: return True
        if low in {"false", "0", "no", "off"}: return False
    return default if value is None else bool(value)


def _float(value: Any, default: float, min_v: float, max_v: float) -> float:
    try: number = float(value)
    except (TypeError, ValueError): number = default
    return round(max(min_v, min(max_v, number)), 4)


def _enum(value: Any, valid: set[str], default: str) -> str:
    text = str(value or default).strip()
    return text if text in valid else default


def _asset_ref(value: Any) -> dict[str, Any] | list[dict[str, Any]]:
    if isinstance(value, list):
        refs: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            normalized = _asset_ref(item)
            if isinstance(normalized, dict) and str(normalized.get("ref") or "").strip():
                normalized.setdefault("index", index)
                refs.append(normalized)
        return refs
    if isinstance(value, dict):
        ref = deepcopy(value)
    else:
        ref = {"ref": str(value or "").strip()}
    text = str(ref.get("ref") or ref.get("path") or ref.get("url") or ref.get("id") or "").strip()
    if text:
        ref.setdefault("ref", text)
    return ref


SCENE_DIRECTOR_EXTENSION_ID = "image.scene_director"
SCENE_DIRECTOR_CLIP_VISION_DEFAULT = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"


def _scene_director_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get(SCENE_DIRECTOR_EXTENSION_ID), dict):
        return deepcopy(payload[SCENE_DIRECTOR_EXTENSION_ID])
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(SCENE_DIRECTOR_EXTENSION_ID), dict):
        return deepcopy(payloads[SCENE_DIRECTOR_EXTENSION_ID])
    nested = payload.get("extensions")
    if isinstance(nested, dict):
        if isinstance(nested.get(SCENE_DIRECTOR_EXTENSION_ID), dict):
            return deepcopy(nested[SCENE_DIRECTOR_EXTENSION_ID])
        nested_payloads = nested.get("payloads")
        if isinstance(nested_payloads, dict) and isinstance(nested_payloads.get(SCENE_DIRECTOR_EXTENSION_ID), dict):
            return deepcopy(nested_payloads[SCENE_DIRECTOR_EXTENSION_ID])
    backend_payload = payload.get("backend_payload")
    if isinstance(backend_payload, dict):
        return _scene_director_block(backend_payload)
    return {}




def _scene_director_regions(block: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    return [region for region in regions if isinstance(region, dict)]


def _scene_director_assigned_ipadapter_ids(payload: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    """Return Scene Director region-assigned IP Adapter unit/profile IDs.

    Phase 26.7 hard-gates regional IP Adapter execution. Scene Director may
    still store an ipadapter_unit_id/ipadapter_profile_id on a region as
    metadata, but the owner IP Adapter extension must not consume those IDs as
    runtime FaceID/IPAdapter units until a provider-safe regional route exists.
    """
    block = _scene_director_block(payload)
    if not block or block.get("enabled") is False:
        return set(), set()
    unit_ids: set[str] = set()
    profile_ids: set[str] = set()

    def add_routes(routes: Any) -> None:
        if not isinstance(routes, dict):
            return
        unit_id = str(routes.get("ipadapter_unit_id") or "").strip()
        profile_id = str(routes.get("ipadapter_profile_id") or "").strip()
        if unit_id:
            unit_ids.add(unit_id)
        if profile_id:
            profile_ids.add(profile_id)

    for region in _scene_director_regions(block):
        add_routes(region.get("extension_routes"))
        metadata = region.get("metadata") if isinstance(region.get("metadata"), dict) else {}
        add_routes(metadata.get("extension_routes"))

    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    routing = metadata.get("extension_unit_routing") if isinstance(metadata.get("extension_unit_routing"), dict) else {}
    routes = routing.get("region_routes") if isinstance(routing.get("region_routes"), list) else []
    for row in routes:
        if isinstance(row, dict):
            add_routes(row.get("routes"))

    return {item for item in unit_ids if item.lower() not in {"none", "off", "null"}}, {item for item in profile_ids if item.lower() not in {"none", "off", "null"}}


def _is_scene_director_unit(unit: dict[str, Any]) -> bool:
    source = str(unit.get("source") or "").strip()
    uid = str(unit.get("uid") or "").strip()
    return source == "scene_director_character_profile_region" or uid.startswith("scene_director_identity_") or bool(unit.get("scene_director_region_id"))

def _scene_director_identity_units(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    block = _scene_director_block(payload)
    if not block or block.get("enabled") is False:
        return []
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    units = assets.get("identity_units") if isinstance(assets.get("identity_units"), list) else []
    converted: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        mode = str(unit.get("mode") or "faceid").strip().lower() or "faceid"
        if mode in {"trigger_only", "none", "off"}:
            continue
        if mode == "ipadapter":
            mode = "standard"
        image_names = unit.get("image_names") if isinstance(unit.get("image_names"), list) else []
        clean_names = [str(item or "").strip() for item in image_names if str(item or "").strip()]
        image_name = str(unit.get("image_name") or unit.get("reference_image") or "").strip()
        if image_name and image_name not in clean_names:
            clean_names.insert(0, image_name)
        if not clean_names:
            continue
        clip_vision = str(unit.get("clip_vision") or "").strip()
        if not clip_vision or clip_vision.lower() == "auto":
            clip_vision = SCENE_DIRECTOR_CLIP_VISION_DEFAULT
        converted.append({
            "uid": f"scene_director_identity_{unit.get('region_id') or unit.get('profile_id') or index + 1}",
            "enabled": True,
            "mode": mode if mode in VALID_MODES else "faceid",
            "model": unit.get("model") or unit.get("ipadapter_model") or "",
            "clip_vision": clip_vision,
            "image_name": clean_names[0],
            "image_names": clean_names,
            "weight": unit.get("weight", 0.45),
            "weight_faceidv2": unit.get("weight_faceidv2", unit.get("weight", 0.45)),
            "start_at": unit.get("start_at", 0.0),
            "end_at": unit.get("end_at", 0.65),
            "faceid_preset": unit.get("faceid_preset") or "FACEID PLUS V2",
            "faceid_provider": unit.get("faceid_provider") or "CUDA",
            "faceid_lora_strength": unit.get("faceid_lora_strength", 0.8),
            "weight_type": unit.get("weight_type") or "linear",
            "combine_embeds": unit.get("combine_embeds") or "concat",
            "embeds_scaling": unit.get("embeds_scaling") or "V only",
            "scene_director_region_id": unit.get("region_id"),
            "scene_director_region_index": unit.get("region_index"),
            "source": "scene_director_character_profile_region",
        })
    return converted


def normalize_image_names(unit: dict[str, Any], assets: dict[str, Any] | None = None) -> list[str]:
    names = unit.get("image_names") if isinstance(unit.get("image_names"), list) else []
    clean = [str(item or "").strip() for item in names if str(item or "").strip()]
    single = str(unit.get("image_name") or unit.get("ipadapter_image_name") or "").strip()
    if single and single not in clean:
        clean.insert(0, single)
    assets = assets or {}
    refs = assets.get("reference_images") if isinstance(assets.get("reference_images"), dict) else {}
    uid = str(unit.get("uid") or "unit_1")
    fallback_candidates = [refs.get(uid), refs.get("default")]
    if uid == "primary":
        fallback_candidates.append(refs.get("primary"))
    for candidate in fallback_candidates:
        candidates = candidate if isinstance(candidate, list) else [candidate]
        for item in candidates:
            text = str((item or {}).get("ref") if isinstance(item, dict) else item or "").strip()
            if text and text not in clean:
                clean.append(text)
    return clean




def _asset_ref_values(value: Any) -> set[str]:
    items = value if isinstance(value, list) else [value]
    refs: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("ref") or item.get("path") or item.get("url") or item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            refs.add(text)
    return refs

def normalize_unit(raw: dict[str, Any], index: int, assets: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    notes: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        return None, [{"level": "warning", "field": f"inputs.units[{index}]", "message": "Ignored non-object IP Adapter unit."}]
    enabled = _as_bool(raw.get("enabled"), True)
    mode = str(raw.get("mode") or raw.get("ipadapter_mode") or "standard").strip().lower() or "standard"
    if mode == "ipadapter": mode = "standard"
    if mode not in VALID_MODES:
        notes.append({"level": "warning", "field": f"inputs.units[{index}].mode", "message": "Unsupported mode normalized to standard."})
        mode = "standard"
    unit = {
        "uid": str(raw.get("uid") or f"unit_{index + 1}").strip() or f"unit_{index + 1}",
        "enabled": enabled,
        "mode": mode,
        "clip_vision": _clean_select_placeholder(raw.get("clip_vision") or raw.get("clip_vision_name") or raw.get("ipadapter_clip_vision")),
        "weight": _float(raw.get("weight"), 1.0, -1.0, 5.0),
        "start_at": _float(raw.get("start_at"), 0.0, 0.0, 1.0),
        "end_at": _float(raw.get("end_at"), 1.0, 0.0, 1.0),
    }
    if mode == "standard":
        unit.update({
            "model": _clean_select_placeholder(raw.get("model") or raw.get("ipadapter_name") or raw.get("ipadapter_model")),
            "weight_type": _enum(raw.get("weight_type"), VALID_WEIGHT_TYPES, "linear"),
            "combine_embeds": _enum(raw.get("combine_embeds"), VALID_COMBINE, "concat"),
            "embeds_scaling": _enum(raw.get("embeds_scaling"), VALID_EMBEDS_SCALING, "V only"),
        })
    if mode == "faceid":
        unit.update({
            "weight_faceidv2": _float(raw.get("weight_faceidv2") if raw.get("weight_faceidv2") is not None else raw.get("weight"), 1.0, -1.0, 5.0),
            "faceid_model": _clean_select_placeholder(raw.get("faceid_model") or raw.get("ipadapter_faceid_model")),
            "faceid_preset": str(raw.get("faceid_preset") or "FACEID PLUS V2").strip() or "FACEID PLUS V2",
            "faceid_provider": str(raw.get("faceid_provider") or "CUDA").strip() or "CUDA",
            "faceid_lora_strength": _float(raw.get("faceid_lora_strength"), 0.75, 0.0, 2.0),
            "weight_type": _enum(raw.get("weight_type"), VALID_WEIGHT_TYPES, "linear"),
            "combine_embeds": _enum(raw.get("combine_embeds"), VALID_COMBINE, "concat"),
            "embeds_scaling": _enum(raw.get("embeds_scaling"), VALID_EMBEDS_SCALING, "V only"),
        })
    image_names = normalize_image_names({**raw, **unit}, assets)
    if raw.get("source"):
        unit["source"] = str(raw.get("source") or "")
    if raw.get("scene_director_region_id"):
        unit["scene_director_region_id"] = str(raw.get("scene_director_region_id") or "")
    if raw.get("scene_director_region_index") is not None:
        unit["scene_director_region_index"] = raw.get("scene_director_region_index")
    if image_names:
        unit["image_name"] = image_names[0]
        unit["image_names"] = image_names
    if mode != "faceid" and not unit["model"]:
        notes.append({"level": "warning", "field": f"inputs.units[{index}].model", "message": "Standard IP Adapter unit needs a model before queue."})
    if not unit["clip_vision"]:
        notes.append({"level": "warning", "field": f"inputs.units[{index}].clip_vision", "message": "IP Adapter unit needs a CLIP Vision model before queue."})
    if not image_names:
        notes.append({"level": "warning", "field": f"inputs.units[{index}].image", "message": "IP Adapter unit needs a reference image before queue."})
    return unit, notes


def _extract_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get(EXTENSION_ID), dict):
        return deepcopy(payload[EXTENSION_ID])
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return deepcopy(payloads[EXTENSION_ID])
    nested = payload.get("extensions")
    if isinstance(nested, dict):
        if isinstance(nested.get(EXTENSION_ID), dict):
            return deepcopy(nested[EXTENSION_ID])
        if isinstance(nested.get("ip_adapter"), dict):
            return deepcopy(nested["ip_adapter"])
    legacy_keys = {"ipadapter_units", "ipadapter_name", "ipadapter_model", "ipadapter_clip_vision", "ipadapter_image_name", "ipadapter_mode", "ipadapter_enabled"}
    if legacy_keys.intersection(payload.keys()):
        return deepcopy(payload)
    return deepcopy(payload)


def normalize_block(payload: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw = _extract_block(payload)
    route = route or {}
    notes: list[dict[str, str]] = []
    if raw.get("enabled") is False or raw.get("ipadapter_enabled") is False:
        return {"enabled": False, "version": VERSION, "inputs": {}, "params": {}, "assets": {}, "metadata": {"schema": "neo.image.ip_adapter.v1", "reason": "disabled"}}, notes

    inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    units_raw = inputs.get("units") if isinstance(inputs.get("units"), list) else raw.get("ipadapter_units") if isinstance(raw.get("ipadapter_units"), list) else []
    assigned_unit_ids, assigned_profile_ids = _scene_director_assigned_ipadapter_ids(payload)
    scene_director_units = _scene_director_identity_units(payload)
    if scene_director_units:
        # Phase 26.7 hard gate: Scene Director identity/profile data stays
        # metadata-only. The owner IP Adapter extension must not convert it into
        # runtime IPAdapterFaceID/IPAdapterAdvanced nodes until a safe regional
        # route exists. This prevents SDXL attention-shape crashes when regional
        # Scene Director masks and model-wide FaceID patches are stacked.
        metadata["scene_director_identity_units_blocked"] = len(scene_director_units)
        metadata["scene_director_regional_ipadapter_hard_gated"] = True
        metadata["scene_director_ipadapter_hard_gate_phase"] = "SD-V054-26.7"
        notes.append({
            "level": "warning",
            "field": "scene_director.assets.identity_units",
            "message": "Scene Director identity/profile units are metadata-only; IP Adapter runtime consumption is hard-gated in SD-V054-26.7.",
            "code": "scene_director_ipadapter_identity_units_blocked",
        })
    if assigned_unit_ids or assigned_profile_ids:
        metadata["scene_director_regional_ipadapter_hard_gated"] = True
        metadata["scene_director_ipadapter_hard_gate_phase"] = "SD-V054-26.7"
        if assigned_unit_ids:
            metadata["scene_director_regional_ipadapter_units_suppressed"] = sorted(assigned_unit_ids)
        if assigned_profile_ids:
            metadata["scene_director_regional_ipadapter_profiles_metadata_only"] = sorted(assigned_profile_ids)
        notes.append({
            "level": "warning",
            "field": "scene_director.extension_routes.ipadapter",
            "message": "Scene Director IP Adapter region routes are metadata-only; matching owner units are suppressed from runtime execution.",
            "code": "scene_director_ipadapter_region_route_hard_gated",
        })
    if units_raw and assigned_unit_ids:
        filtered_units = []
        for unit in list(units_raw or []):
            uid = str(unit.get("uid") if isinstance(unit, dict) else "").strip()
            if uid and uid in assigned_unit_ids:
                continue
            filtered_units.append(unit)
        units_raw = filtered_units
    if not units_raw:
        scalar = {
            "enabled": raw.get("enabled", raw.get("ipadapter_enabled", True)),
            "mode": raw.get("ipadapter_mode") or inputs.get("mode") or "standard",
            "model": raw.get("ipadapter_name") or raw.get("ipadapter_model") or params.get("model"),
            "clip_vision": raw.get("ipadapter_clip_vision") or params.get("clip_vision"),
            "image_name": raw.get("ipadapter_image_name"),
            "image_names": raw.get("ipadapter_image_names"),
            "weight": raw.get("ipadapter_weight"),
            "weight_faceidv2": raw.get("ipadapter_weight_faceidv2"),
            "weight_type": raw.get("ipadapter_weight_type"),
            "combine_embeds": raw.get("ipadapter_combine_embeds"),
            "embeds_scaling": raw.get("ipadapter_embeds_scaling"),
            "start_at": raw.get("ipadapter_start_at"),
            "end_at": raw.get("ipadapter_end_at"),
            "faceid_model": raw.get("ipadapter_faceid_model"),
            "faceid_preset": raw.get("ipadapter_faceid_preset"),
            "faceid_provider": raw.get("ipadapter_faceid_provider"),
            "faceid_lora_strength": raw.get("ipadapter_faceid_lora_strength"),
        }
        if scalar.get("clip_vision") or scalar.get("model") or scalar.get("image_name"):
            units_raw = [scalar]
            metadata["legacy_migrated"] = True
    clean_units = []
    for idx, item in enumerate(units_raw):
        unit, unit_notes = normalize_unit(item, idx, assets)
        notes.extend(unit_notes)
        if unit and unit.get("enabled"):
            clean_units.append(unit)
    enabled = _as_bool(raw.get("enabled", raw.get("ipadapter_enabled", bool(clean_units))), bool(clean_units)) and bool(clean_units)
    if not enabled:
        clean_units = []
    clean_assets = {}
    refs = assets.get("reference_images") if isinstance(assets.get("reference_images"), dict) else {}
    active_uids = {str(unit.get("uid") or "") for unit in clean_units}
    active_image_refs = {str(name or "").strip() for unit in clean_units for name in (unit.get("image_names") or []) if str(name or "").strip()}
    if refs and enabled:
        cleaned_refs = {}
        for key, value in refs.items():
            key_text = str(key)
            value_refs = _asset_ref_values(value)
            # Keep only active unit assets. default/primary fallbacks are allowed only when their refs were actually consumed by an active unit.
            if key_text not in active_uids and not (key_text in {"default", "primary"} and value_refs.intersection(active_image_refs)):
                continue
            normalized_ref = _asset_ref(value)
            if isinstance(normalized_ref, list):
                normalized_ref = [item for item in normalized_ref if str(item.get("ref") or "").strip() in active_image_refs]
                if normalized_ref:
                    cleaned_refs[key_text] = normalized_ref
            elif str(normalized_ref.get("ref") or "").strip() in active_image_refs:
                cleaned_refs[key_text] = normalized_ref
        if cleaned_refs:
            clean_assets["reference_images"] = cleaned_refs
    clean_metadata = {k: deepcopy(v) for k, v in metadata.items() if k in VALID_METADATA_KEYS}
    clean_metadata.update({"schema": "neo.image.ip_adapter.v1", "route": deepcopy(route), "route_state": route.get("route_state") or route.get("state")})
    return {"enabled": enabled, "version": VERSION, "inputs": {"units": clean_units} if enabled else {}, "params": {}, "assets": clean_assets if enabled else {}, "metadata": clean_metadata}, notes
