from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_UNIT_ROUTING_SCHEMA = "neo.image.scene_director.extension_unit_routing.v054.v1"
EXTENSION_UNIT_ROUTING_PHASE = "SD-V054-26.9.2b"
# Phase 26.7 compatibility anchor: EXTENSION_UNIT_ROUTING_PHASE = "SD-V054-26.7"
# Phase 26.5 compatibility anchor: EXTENSION_UNIT_ROUTING_PHASE = "SD-V054-26.5"
# Phase 26.4 compatibility anchor: EXTENSION_UNIT_ROUTING_PHASE = "SD-V054-26.4"
# Phase 26.3 compatibility anchor: EXTENSION_UNIT_ROUTING_PHASE = "SD-V054-26.3"

EXTENSION_OWNER_IDS = {
    "controlnet": "image.controlnet",
    "adetailer": "image.adetailer",
    "ipadapter": "image.ip_adapter",
    "lora": "lora_stack",
}

_NULLISH = {"", "none", "null", "undefined", "off", "false"}


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _NULLISH else text


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value.strip():
        items = [part.strip() for part in value.split(",")]
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _clean_id(item)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def normalize_extension_routes_v054(value: Any) -> dict[str, Any]:
    """Normalize the owner-extension unit/profile link contract.

    Phase 25 moves identity profile ownership to image.ip_adapter. Scene Director
    stores only assignment references, never profile creation state or direct
    FaceID execution data.
    """
    source = value if isinstance(value, dict) else {}
    lora_ids = _clean_list(source.get("lora_row_ids") or source.get("lora_ids") or source.get("lora_row_id"))
    raw_ip = _clean_id(
        source.get("ipadapter_unit_id")
        or source.get("ip_adapter_unit_id")
        or source.get("ipadapter_profile_id")
        or source.get("identity_profile_id")
        or source.get("ipadapter")
    )
    ipadapter_profile_id = _clean_id(source.get("ipadapter_profile_id") or source.get("identity_profile_id"))
    ipadapter_unit_id = _clean_id(source.get("ipadapter_unit_id") or source.get("ip_adapter_unit_id"))
    if raw_ip.startswith("profile:"):
        ipadapter_profile_id = _clean_id(raw_ip.split(":", 1)[1])
        ipadapter_unit_id = ""
    elif raw_ip.startswith("unit:"):
        ipadapter_unit_id = _clean_id(raw_ip.split(":", 1)[1])
    elif raw_ip and not ipadapter_unit_id and not ipadapter_profile_id:
        # Legacy values were ambiguous. Keep them as unit ids for compatibility.
        ipadapter_unit_id = raw_ip
    return {
        "schema": EXTENSION_UNIT_ROUTING_SCHEMA,
        "phase": EXTENSION_UNIT_ROUTING_PHASE,
        "controlnet_unit_id": _clean_id(source.get("controlnet_unit_id") or source.get("controlnet") or source.get("controlnet_unit")),
        "adetailer_pass_id": _clean_id(source.get("adetailer_pass_id") or source.get("adetailer") or source.get("detailer_pass_id")),
        "ipadapter_unit_id": ipadapter_unit_id,
        "ipadapter_profile_id": ipadapter_profile_id,
        "lora_row_ids": lora_ids,
        "mask_mode": _clean_id(source.get("mask_mode") or "region") or "region",
        "execution": "region_assignment_ready",
        "ipadapter_execution": "disabled_metadata_only",
    }


def normalize_ipadapter_identity_profiles_v054(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            continue
        profile_id = _clean_id(item.get("profile_id") or item.get("uid") or item.get("id") or item.get("profile_name") or item.get("name") or f"profile_{index}")
        if not profile_id:
            continue
        base_id = profile_id
        bump = 2
        while profile_id in seen:
            profile_id = f"{base_id}_{bump}"
            bump += 1
        seen.add(profile_id)
        refs = item.get("reference_images") or item.get("image_names") or item.get("references") or item.get("reference_image") or []
        if isinstance(refs, str):
            refs = [part.strip() for part in refs.replace("\r", "\n").replace(",", "\n").split("\n") if part.strip()]
        if not isinstance(refs, list):
            refs = []
        out.append({
            "profile_id": profile_id,
            "profile_name": str(item.get("profile_name") or item.get("name") or profile_id).strip() or profile_id,
            "mode": _clean_id(item.get("mode") or item.get("ipadapter_mode") or "faceid") or "faceid",
            "reference_images": [_clean_id(ref) for ref in refs if _clean_id(ref)],
            "trigger_words": str(item.get("trigger_words") or "").strip(),
            "clip_vision": str(item.get("clip_vision") or item.get("clip_vision_model") or "auto").strip() or "auto",
            "weight": item.get("weight", item.get("weight_faceidv2", 0.45)),
            "start_at": item.get("start_at", 0),
            "end_at": item.get("end_at", 0.65),
            "faceid_lora_strength": item.get("faceid_lora_strength", item.get("lora_weight", 0.8)),
            "optional_lora": str(item.get("optional_lora") or item.get("lora") or "").strip(),
            "notes": str(item.get("notes") or "").strip(),
            "owner_extension_id": EXTENSION_OWNER_IDS["ipadapter"],
            "execution": "metadata_only_until_provider_safe_route",
        })
    return out


def _payload_roots(payload: Any) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        roots.append(payload)
        for key in ("backend_payload", "actual_params", "params", "route"):
            value = payload.get(key)
            if isinstance(value, dict):
                roots.append(value)
        # One level of nested route/actual_params appears in backend logs.
        for root in list(roots):
            route = root.get("route") if isinstance(root.get("route"), dict) else {}
            params = route.get("actual_params") if isinstance(route.get("actual_params"), dict) else {}
            if params:
                roots.append(params)
    return roots


def _explicit_enabled_state(item: Any) -> bool | None:
    """Return the explicit user-visible enabled state, if present.

    Runtime logs can contain ``workflow_applied: true`` for Scene Director-owned
    metadata even when the owner extension toggle is off.  Phase 26.10.8K makes
    the visible ``enabled`` value authoritative so stale/replay route metadata can
    never resurrect disabled IPAdapter/LoRA/ControlNet/ADetailer execution.
    """
    if not isinstance(item, dict):
        return None
    if item.get("enabled") is False:
        return False
    if item.get("enabled") is True:
        return True
    return None


def owner_extension_state_v054(payload: Any, extension_id: str) -> bool | None:
    """Return explicit owner-extension state from submit/runtime payloads.

    True means the owner extension is available for Scene Director to resolve
    assigned rows/units. False means it was explicitly disabled in the visible UI
    state or replay payload. None means no reliable state was found.
    """
    for root in _payload_roots(payload):
        state = root.get("_neo_extension_state") if isinstance(root.get("_neo_extension_state"), dict) else {}
        extensions = state.get("extensions") if isinstance(state.get("extensions"), dict) else {}
        item = extensions.get(extension_id) if isinstance(extensions.get(extension_id), dict) else None
        explicit = _explicit_enabled_state(item)
        if explicit is not None:
            return explicit

    block = _payload_extension_block(payload, extension_id)
    explicit = _explicit_enabled_state(block)
    if explicit is not None:
        return explicit
    if isinstance(block, dict) and block.get("workflow_applied") is True:
        return True

    for root in _payload_roots(payload):
        state = root.get("_neo_extension_state") if isinstance(root.get("_neo_extension_state"), dict) else {}
        extensions = state.get("extensions") if isinstance(state.get("extensions"), dict) else {}
        item = extensions.get(extension_id) if isinstance(extensions.get(extension_id), dict) else None
        if isinstance(item, dict) and item.get("workflow_applied") is True:
            return True
    return None


def disabled_owner_route_keys_v054(payload: Any) -> set[str]:
    """Owners with disabled/absent state should not leave stale region routes."""
    if payload is None:
        return set()
    disabled: set[str] = set()
    for owner_key, extension_id in EXTENSION_OWNER_IDS.items():
        state = owner_extension_state_v054(payload, extension_id)
        # Treat absent owner payload as disabled for route execution. This keeps
        # old region selections from producing scary unavailable warnings after
        # the user has turned the owner extension off or not submitted it.
        if state is not True:
            disabled.add(owner_key)
    return disabled


def strip_disabled_owner_routes_v054(scene_graph: Any, payload: Any) -> dict[str, Any]:
    graph = deepcopy(scene_graph if isinstance(scene_graph, dict) else {})
    disabled = disabled_owner_route_keys_v054(payload)
    if not disabled:
        return graph
    key_map = {
        "controlnet": (("controlnet_unit_id",), ("ext_controlnet_unit_id",)),
        "adetailer": (("adetailer_pass_id",), ("ext_adetailer_pass_id",)),
        "ipadapter": (("ipadapter_unit_id", "ipadapter_profile_id"), ("ext_ipadapter_unit_id", "ext_ipadapter_profile_id", "ipadapter_unit_id", "ipadapter_profile_id")),
        "lora": (("lora_row_ids",), ("ext_lora_row_ids", "lora_row_ids", "lora_row_id")),
    }
    repairs: list[dict[str, Any]] = []
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    next_regions = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            next_regions.append(region)
            continue
        item = deepcopy(region)
        routes = normalize_extension_routes_v054(item.get("extension_routes"))
        changed = False
        for owner in sorted(disabled):
            route_keys, legacy_keys = key_map[owner]
            route_values: list[str] = []
            for route_key in route_keys:
                value = routes.get(route_key)
                if isinstance(value, list):
                    route_values.extend(str(v) for v in value if str(v or "").strip())
                elif str(value or "").strip():
                    route_values.append(str(value))
            for legacy_key in legacy_keys:
                value = item.get(legacy_key)
                if isinstance(value, list):
                    route_values.extend(str(v) for v in value if str(v or "").strip())
                elif str(value or "").strip():
                    route_values.append(str(value))
            if route_values:
                repairs.append({
                    "owner": owner,
                    "owner_extension_id": EXTENSION_OWNER_IDS[owner],
                    "region_id": str(item.get("id") or f"region_{index}"),
                    "label": str(item.get("label") or item.get("id") or f"Region {index}"),
                    "route_key": ",".join(route_keys),
                    "route_id": ",".join(route_values),
                    "reason": "owner_extension_disabled_or_not_submitted",
                })
            for route_key in route_keys:
                routes[route_key] = [] if route_key == "lora_row_ids" else ""
            for legacy_key in legacy_keys:
                if legacy_key in item:
                    item[legacy_key] = [] if legacy_key.endswith("row_ids") else ""
                    changed = True
        if changed or repairs:
            item["extension_routes"] = routes
        next_regions.append(item)
    graph["regions"] = next_regions
    metadata = graph.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["disabled_owner_route_cleanup"] = {
            "schema": "neo.image.scene_director.disabled_owner_route_cleanup.v054.v1",
            "phase": "SD-V054-26.10.8K",
            "status": "applied" if repairs else "no_stale_routes",
            "disabled_owners": sorted(disabled),
            "repair_count": len(repairs),
            "repairs": repairs,
            "policy": "When an owner extension is disabled or not submitted, Scene Director strips stale region route ids and suppresses runtime execution for ControlNet, ADetailer, IPAdapter, and LoRA while preserving metadata for replay.",
        }
        metadata["extension_unit_routing"] = build_extension_unit_routing_contract_v054(graph)
    return graph

def extension_routes_have_selection(routes: Any) -> bool:
    normalized = normalize_extension_routes_v054(routes)
    return bool(
        normalized.get("controlnet_unit_id")
        or normalized.get("adetailer_pass_id")
        or normalized.get("ipadapter_unit_id")
        or normalized.get("ipadapter_profile_id")
        or normalized.get("lora_row_ids")
    )


def build_extension_unit_routing_contract_v054(scene_graph: Any) -> dict[str, Any]:
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    region_routes: list[dict[str, Any]] = []
    counts = {"controlnet": 0, "adetailer": 0, "ipadapter": 0, "lora": 0}
    owner_ids: set[str] = set()

    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        routes = normalize_extension_routes_v054(region.get("extension_routes"))
        selected: dict[str, Any] = {}
        if routes.get("controlnet_unit_id"):
            selected["controlnet_unit_id"] = routes["controlnet_unit_id"]
            counts["controlnet"] += 1
            owner_ids.add(EXTENSION_OWNER_IDS["controlnet"])
        if routes.get("adetailer_pass_id"):
            selected["adetailer_pass_id"] = routes["adetailer_pass_id"]
            counts["adetailer"] += 1
            owner_ids.add(EXTENSION_OWNER_IDS["adetailer"])
        if routes.get("ipadapter_unit_id"):
            selected["ipadapter_unit_id"] = routes["ipadapter_unit_id"]
            counts["ipadapter"] += 1
            owner_ids.add(EXTENSION_OWNER_IDS["ipadapter"])
        if routes.get("ipadapter_profile_id"):
            selected["ipadapter_profile_id"] = routes["ipadapter_profile_id"]
            counts["ipadapter"] += 1
            owner_ids.add(EXTENSION_OWNER_IDS["ipadapter"])
        if routes.get("lora_row_ids"):
            selected["lora_row_ids"] = list(routes["lora_row_ids"])
            counts["lora"] += len(routes["lora_row_ids"])
            owner_ids.add(EXTENSION_OWNER_IDS["lora"])
        if not selected:
            continue
        region_routes.append({
            "region_id": str(region.get("id") or f"region_{index + 1}"),
            "label": str(region.get("label") or region.get("id") or f"Region {index + 1}"),
            "role": str(region.get("role") or "custom"),
            "mask_mode": routes.get("mask_mode") or "region",
            "routes": selected,
            "execution": "region_assignment_ready",
        })

    return {
        "schema": EXTENSION_UNIT_ROUTING_SCHEMA,
        "phase": EXTENSION_UNIT_ROUTING_PHASE,
        "status": "contract_ready",
        "execution_state": "controlnet_adetailer_lora_assignment_ready",
        "owner_extension_ids": sorted(owner_ids),
        "region_routes": region_routes,
        "route_count": len(region_routes),
        "counts": counts,
        "policy": {
            "controlnet": "ControlNet extension owns units; Scene Director stores selected unit id per region.",
            "adetailer": "ADetailer extension owns passes; Scene Director stores selected pass id per region.",
            "ipadapter": "IP Adapter owns identity profile and unit creation; Scene Director stores selected profile/unit id per region only. Execution remains disabled until provider-safe routing is implemented.",
            "lora": "LoRA Stack owns LoRA rows; Scene Director stores selected row id(s) per region. LoRA Stack no longer owns region targeting; Scene Director owns row-to-region assignment.",
        },
    }


def _payload_extension_block(payload: Any, extension_id: str) -> dict[str, Any]:
    """Return an owner-extension payload block from all known Neo payload shapes.

    Recent Image submit payloads can store extension blocks in more than one
    location depending on whether they are read before or after workflow patch
    collection. Scene Director region routing must resolve owner rows/units from
    the real owner-extension payload, not only from the submit-state booleans.
    """
    if not isinstance(payload, dict):
        return {}

    candidates: list[Any] = [payload]
    for key in ("backend_payload", "actual_params", "params", "route"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    for root in list(candidates):
        if not isinstance(root, dict):
            continue
        for key in ("payloads", "extension_payloads"):
            block_root = root.get(key)
            if isinstance(block_root, dict) and isinstance(block_root.get(extension_id), dict):
                return deepcopy(block_root.get(extension_id) or {})
        ext_root = root.get("extensions")
        if isinstance(ext_root, dict):
            if isinstance(ext_root.get(extension_id), dict):
                return deepcopy(ext_root.get(extension_id) or {})
            nested_payloads = ext_root.get("payloads")
            if isinstance(nested_payloads, dict) and isinstance(nested_payloads.get(extension_id), dict):
                return deepcopy(nested_payloads.get(extension_id) or {})
        if isinstance(root.get(extension_id), dict):
            return deepcopy(root.get(extension_id) or {})
    return {}


def _block_inputs(block: dict[str, Any]) -> dict[str, Any]:
    if isinstance(block.get("inputs"), dict):
        return block.get("inputs") or {}
    return block


def _unit_enabled(owner_block: dict[str, Any], unit: dict[str, Any]) -> bool:
    owner_on = owner_block.get("enabled") is True or owner_block.get("workflow_applied") is True
    if not owner_on:
        return False
    return unit.get("enabled", True) is not False


def _unit_id(unit: dict[str, Any], fallback: str) -> str:
    return _clean_id(unit.get("uid") or unit.get("id") or unit.get("unit_id") or fallback)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_id(value)
        if text:
            return text
    return ""


def _controlnet_route_params(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    route = metadata.get("route") if isinstance(metadata.get("route"), dict) else {}
    params = route.get("actual_params") if isinstance(route.get("actual_params"), dict) else {}
    if not params and isinstance(route.get("params"), dict):
        params = route.get("params") or {}
    return params if isinstance(params, dict) else {}


def _controlnet_units_from_payload(payload: Any) -> dict[str, dict[str, Any]]:
    block = _payload_extension_block(payload, EXTENSION_OWNER_IDS["controlnet"])
    inputs = _block_inputs(block)
    route_params = _controlnet_route_params(block)
    route_units = route_params.get("controlnet_units") if isinstance(route_params.get("controlnet_units"), list) else []
    raw_units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
    if not raw_units and route_units:
        raw_units = route_units
    out: dict[str, dict[str, Any]] = {}
    for index, unit in enumerate(raw_units, start=1):
        if not isinstance(unit, dict) or not _unit_enabled(block, unit):
            continue
        uid = _unit_id(unit, f"unit_{index}")
        route_unit = route_units[index - 1] if index - 1 < len(route_units) and isinstance(route_units[index - 1], dict) else {}
        merged_unit = {**deepcopy(route_unit), **deepcopy(unit)}
        generated_map = _first_text(merged_unit.get("generated_map"), route_unit.get("generated_map"), route_params.get("control_image_name"))
        image_name = _first_text(generated_map, merged_unit.get("control_image"), merged_unit.get("control_image_name"), merged_unit.get("image_name"), merged_unit.get("reference_id"))
        soft = _regional_controlnet_soft_settings({**deepcopy(merged_unit), "strength": merged_unit.get("strength", route_params.get("controlnet_strength", 0.75)), "start": merged_unit.get("start_percent", merged_unit.get("start", 0.0)), "end": merged_unit.get("end_percent", merged_unit.get("end", 1.0))})
        out[uid] = {
            "uid": uid,
            "source_extension_id": EXTENSION_OWNER_IDS["controlnet"],
            "type": _first_text(merged_unit.get("unit"), merged_unit.get("preprocessor")),
            "preprocessor": _first_text(merged_unit.get("preprocessor"), merged_unit.get("unit")),
            "model": _first_text(merged_unit.get("model"), merged_unit.get("controlnet_model"), merged_unit.get("control_net_name"), merged_unit.get("controlnet_name"), route_params.get("controlnet_name")),
            "image_name": image_name,
            "generated_map": generated_map,
            "strength": soft["strength"],
            "start": soft["start"],
            "end": soft["end"],
            "raw_strength": soft["raw_strength"],
            "raw_start": soft["raw_start"],
            "raw_end": soft["raw_end"],
            "route_mode": soft["route_mode"],
            "strength_cap": soft["strength_cap"],
            "end_cap": soft["end_cap"],
            "mask_blend_strength": soft["mask_blend_strength"],
            "softened": soft["softened"],
            "mask_mode": _first_text(merged_unit.get("mask_mode"), "region") or "region",
            "owner_unit": deepcopy(merged_unit),
        }
    return out



def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _regional_controlnet_soft_settings(unit: dict[str, Any]) -> dict[str, Any]:
    """Return soft regional ControlNet settings for Scene Director assignments.

    Owner ControlNet units can be authored for full/global guidance. When the
    same unit is assigned to one V054 region, hard full-frame defaults tend to
    pull the whole pose into the reference and create pasted seams. Scene
    Director therefore defaults assigned lanes to Structure Assist unless the
    owner unit explicitly opts into stronger matching.
    """
    owner_mode = str(unit.get("scene_director_route_mode") or unit.get("region_route_mode") or unit.get("route_mode") or "structure_assist").strip().lower()
    if owner_mode in {"strong", "strong_match", "match"}:
        strength_cap = 0.45
        end_cap = 0.85
        blend_strength = 0.9
        label = "strong_match"
    elif owner_mode in {"experimental", "full", "full_match", "experimental_full_match"}:
        strength_cap = 1.0
        end_cap = 1.0
        blend_strength = 1.0
        label = "experimental_full_match"
    else:
        strength_cap = 0.35
        end_cap = 0.72
        blend_strength = 0.82
        label = "structure_assist"

    raw_strength = _to_float(unit.get("strength"), 0.75)
    raw_start = _to_float(unit.get("start"), _to_float(unit.get("start_percent"), 0.0))
    raw_end = _to_float(unit.get("end"), _to_float(unit.get("end_percent"), 1.0))
    return {
        "route_mode": label,
        "raw_strength": raw_strength,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "strength": round(min(raw_strength, strength_cap), 4),
        "start": round(max(0.0, min(raw_start, 1.0)), 4),
        "end": round(max(0.0, min(raw_end, end_cap)), 4),
        "strength_cap": strength_cap,
        "end_cap": end_cap,
        "mask_blend_strength": blend_strength,
        "softened": raw_strength > strength_cap or raw_end > end_cap,
    }

def _adetailer_passes_from_payload(payload: Any) -> dict[str, dict[str, Any]]:
    block = _payload_extension_block(payload, EXTENSION_OWNER_IDS["adetailer"])
    inputs = _block_inputs(block)
    raw_passes = inputs.get("detailer_passes") if isinstance(inputs.get("detailer_passes"), list) else []
    if not raw_passes and any(k in inputs for k in ("detector_model", "mode", "denoise", "steps")):
        raw_passes = [{**inputs, "id": "primary", "label": "Primary pass"}]
    out: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_passes, start=1):
        if not isinstance(item, dict) or not _unit_enabled(block, item):
            continue
        uid = _unit_id(item, "primary" if index == 1 else f"pass_{index}")
        mode = _first_text(item.get("mode"), item.get("target"), "face") or "face"
        detector = _first_text(item.get("detector_model"), item.get("detector"), item.get("model"))
        out[uid] = {
            "uid": uid,
            "source_extension_id": EXTENSION_OWNER_IDS["adetailer"],
            "label": _first_text(item.get("label"), f"Pass {index}"),
            "mode": mode,
            "detector": detector,
            "detector_model": detector,
            "detector_type": _first_text(item.get("detector_type"), "bbox") or "bbox",
            "custom_classes": _first_text(item.get("custom_classes"), "hand" if mode == "hands" else "face" if mode == "face" else "all"),
            "denoise": item.get("denoise", 0.3),
            "steps": item.get("steps", 20),
            "cfg": item.get("cfg", 5.5),
            "mask_feather": item.get("mask_feather", item.get("mask_blur", 12)),
            "mask_blur": item.get("mask_blur", item.get("mask_feather", 12)),
            "detect_inside_region": item.get("detect_inside_region", True),
            "mask_mode": _first_text(item.get("mask_mode"), "region") or "region",
            "owner_pass": deepcopy(item),
        }
    return out


def _lora_rows_from_payload(payload: Any) -> dict[str, dict[str, Any]]:
    block = _payload_extension_block(payload, EXTENSION_OWNER_IDS["lora"])
    inputs = _block_inputs(block)
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    raw_rows = inputs.get("loras") if isinstance(inputs.get("loras"), list) else []
    if not raw_rows and isinstance(params.get("loras"), list):
        raw_rows = params.get("loras") or []
    out: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict) or not _unit_enabled(block, row):
            continue
        uid = _unit_id(row, f"lora_{index}")
        name = _first_text(row.get("name"), row.get("lora_name"), row.get("file"))
        if not name:
            continue
        source_record = row.get("source_record") if isinstance(row.get("source_record"), dict) else {}
        explicit_strength = any(key in row for key in ("strength", "strength_model", "strength_clip"))
        out[uid] = {
            "uid": uid,
            "source_extension_id": EXTENSION_OWNER_IDS["lora"],
            "name": name,
            "lora_name": name,
            "strength": row.get("strength", row.get("strength_model", 0.8)),
            "strength_user_override": explicit_strength,
            "target": _first_text(row.get("target"), "both") or "both",
            "trigger_words": _first_text(row.get("trigger_words"), row.get("activation_text")),
            "source_record_trigger_words": _first_text(source_record.get("trigger_words") if source_record else "", row.get("source_record_trigger_words")),
            "source_record_activation_text": _first_text(source_record.get("activation_text") if source_record else "", row.get("source_record_activation_text")),
            "source_record_id": _first_text(row.get("source_record_id"), row.get("record_id")),
            "owner_row": deepcopy(row),
        }
    return out



def _scene_lora_route_setting_keys(item: dict[str, Any], *, fallback_region_id: str = "", fallback_row_id: str = "") -> set[str]:
    keys: set[str] = set()
    region_id = _clean_id(item.get("region_id") or item.get("apply_to") or fallback_region_id)
    row_values = [
        item.get("lora_row_id"),
        item.get("row_id"),
        item.get("assigned_row_id"),
        fallback_row_id,
    ]
    for raw_row_id in row_values:
        row_id = _clean_id(raw_row_id)
        if not row_id:
            continue
        if region_id:
            keys.add(f"row:{region_id}:{row_id}")
        keys.add(f"row:*:{row_id}")
    name = str(item.get("name") or item.get("lora_name") or "").strip().casefold()
    if name:
        if region_id:
            keys.add(f"name:{region_id}:{name}")
        keys.add(f"name:*:{name}")
    return keys


def _scene_lora_route_settings_lookup(payload: Any) -> dict[str, dict[str, Any]]:
    """Collect Scene Director-owned per-region LoRA route settings.

    LoRA Stack owns the selected file row, but Scene Director owns regional route
    controls.  This lookup lets the extension-route compiler preserve UI settings
    such as finish/crop denoise, target, visibility preset, and trigger override
    when building executable regional LoRA lanes from owner rows.
    """
    scene_block = _payload_extension_block(payload, "image.scene_director")
    out: dict[str, dict[str, Any]] = {}

    def add_setting(item: Any, *, fallback_region_id: str = "", fallback_row_id: str = "") -> None:
        if not isinstance(item, dict):
            return
        clean = {key: deepcopy(value) for key, value in item.items() if value not in (None, "")}
        if not clean:
            return
        for key in _scene_lora_route_setting_keys(clean, fallback_region_id=fallback_region_id, fallback_row_id=fallback_row_id):
            out.setdefault(key, clean)

    assets = scene_block.get("assets") if isinstance(scene_block.get("assets"), dict) else {}
    for item in assets.get("lora_bindings") or []:
        add_setting(item)

    metadata = scene_block.get("metadata") if isinstance(scene_block.get("metadata"), dict) else {}
    for container_key in ("extension_unit_routing", "submitted_region_routes"):
        routing = metadata.get(container_key) if isinstance(metadata.get(container_key), dict) else {}
        for route in routing.get("region_routes") or []:
            if not isinstance(route, dict):
                continue
            region_id = _clean_id(route.get("region_id"))
            advanced = route.get("advanced_settings") if isinstance(route.get("advanced_settings"), dict) else {}
            lora_advanced = advanced.get("lora") if isinstance(advanced.get("lora"), dict) else {}
            selected = route.get("routes") if isinstance(route.get("routes"), dict) else {}
            for row_id in _clean_list(selected.get("lora_row_ids") or selected.get("lora_row_id")):
                add_setting({"region_id": region_id, "lora_row_id": row_id, **deepcopy(lora_advanced)}, fallback_region_id=region_id, fallback_row_id=row_id)
    return out


def _merge_scene_lora_route_settings(row: dict[str, Any], *, region_id: str, row_id: str, lookup: dict[str, dict[str, Any]], region_routes: dict[str, Any] | None = None) -> dict[str, Any]:
    route_fields = {
        "route_mode", "target", "strength", "strength_user_override",
        "finish_denoise", "finish_denoise_user_override", "finish_steps", "finish_steps_user_override",
        "crop_denoise", "crop_denoise_user_override", "crop_steps", "crop_steps_user_override",
        "crop_padding", "crop_feather", "crop_scope",
        "visibility_preset", "lora_visibility_preset", "visibility_preset_plan",
        "postpass_lock_policy", "character_lock_postpass_policy", "allow_character_repaint", "allow_character_lock_bypass",
        "trigger_words", "trigger_override",
        "lora_compatibility", "lora_family", "checkpoint_family", "checkpoint_name",
    }
    keys = [
        f"row:{region_id}:{row_id}",
        f"row:*:{row_id}",
    ]
    name = str(row.get("name") or row.get("lora_name") or "").strip().casefold()
    if name:
        keys.extend([f"name:{region_id}:{name}", f"name:*:{name}"])
    match: dict[str, Any] | None = None
    for key in keys:
        if key in lookup:
            match = lookup[key]
            break

    inline_advanced: dict[str, Any] = {}
    if isinstance(region_routes, dict):
        advanced = region_routes.get("advanced_settings") if isinstance(region_routes.get("advanced_settings"), dict) else {}
        inline_advanced = advanced.get("lora") if isinstance(advanced.get("lora"), dict) else {}

    extra: dict[str, Any] = {}
    for source in (match or {}, inline_advanced or {}):
        if not isinstance(source, dict):
            continue
        for field in route_fields:
            value = source.get(field)
            if value not in (None, ""):
                extra[field] = deepcopy(value)
    if not extra:
        return row
    return {**row, **extra, "scene_director_route_settings_merged": True}

def build_lora_region_assignments_v054(scene_graph: Any, payload: Any = None) -> dict[str, Any]:
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    lora_rows = _lora_rows_from_payload(payload)
    route_settings_lookup = _scene_lora_route_settings_lookup(payload)
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    lanes: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        routes = normalize_extension_routes_v054(region.get("extension_routes"))
        raw_routes = region.get("extension_routes") if isinstance(region.get("extension_routes"), dict) else {}
        rid = str(region.get("id") or f"region_{index}")
        label = str(region.get("label") or rid)
        for row_id in routes.get("lora_row_ids") or []:
            row = lora_rows.get(row_id)
            if row:
                merged_row = _merge_scene_lora_route_settings(deepcopy(row), region_id=rid, row_id=row_id, lookup=route_settings_lookup, region_routes=raw_routes)
                lanes.append({
                    **merged_row,
                    "region_id": rid,
                    "region_index": index,
                    "region_role": str(region.get("role") or ""),
                    "label": label,
                    "assigned_row_id": row_id,
                    "mask_mode": routes.get("mask_mode") or "region",
                    "execution": "region_routed",
                    "source": "scene_director_lora_row_assignment",
                })
            else:
                messages.append({"level": "warning", "code": "lora_row_unavailable", "region_id": rid, "row_id": row_id, "message": f"LoRA row {row_id} is assigned to {label}, but the LoRA Stack row is disabled or unavailable."})
    return {
        "schema": EXTENSION_UNIT_ROUTING_SCHEMA,
        "phase": EXTENSION_UNIT_ROUTING_PHASE,
        "status": "assignment_ready",
        "execution_state": "lora_region_assignment",
        "lora_lanes": lanes,
        "counts": {"lora": len(lanes)},
        "messages": messages,
        "policy": {
            "lora": "LoRA Stack owns selected rows. Scene Director owns region targeting and applies assigned rows as masked regional finish passes.",
        },
    }


def apply_lora_assignments_to_scene_graph_v054(scene_graph: Any, assignments: Any) -> dict[str, Any]:
    graph = deepcopy(scene_graph if isinstance(scene_graph, dict) else {})
    metadata = graph.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["lora_region_assignments"] = deepcopy(assignments if isinstance(assignments, dict) else {})
    return graph


def build_controlnet_adetailer_region_assignments_v054(scene_graph: Any, payload: Any = None) -> dict[str, Any]:
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    disabled_owners = disabled_owner_route_keys_v054(payload)
    cn_units = {} if "controlnet" in disabled_owners else _controlnet_units_from_payload(payload)
    ad_passes = {} if "adetailer" in disabled_owners else _adetailer_passes_from_payload(payload)
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    controlnet_lanes: list[dict[str, Any]] = []
    adetailer_lanes: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []

    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        routes = normalize_extension_routes_v054(region.get("extension_routes"))
        rid = str(region.get("id") or f"region_{index}")
        label = str(region.get("label") or rid)
        cn_id = routes.get("controlnet_unit_id") or ""
        if cn_id and "controlnet" not in disabled_owners:
            unit = cn_units.get(cn_id)
            if unit:
                controlnet_lanes.append({**deepcopy(unit), "region_id": rid, "region_index": index, "region_role": str(region.get("role") or ""), "label": label, "assigned_unit_id": cn_id, "mask_mode": routes.get("mask_mode") or unit.get("mask_mode") or "region", "execution": "region_routed"})
            else:
                messages.append({"level": "warning", "code": "controlnet_unit_unavailable", "region_id": rid, "unit_id": cn_id, "message": f"ControlNet unit {cn_id} is assigned to {label}, but the ControlNet extension is enabled and the unit is unavailable."})
        ad_id = routes.get("adetailer_pass_id") or ""
        if ad_id and "adetailer" not in disabled_owners:
            item = ad_passes.get(ad_id)
            if item:
                adetailer_lanes.append({**deepcopy(item), "region_id": rid, "region_index": index, "region_role": str(region.get("role") or ""), "label": label, "assigned_pass_id": ad_id, "mask_mode": routes.get("mask_mode") or item.get("mask_mode") or "region", "execution": "region_routed"})
            else:
                messages.append({"level": "warning", "code": "adetailer_pass_unavailable", "region_id": rid, "pass_id": ad_id, "message": f"ADetailer pass {ad_id} is assigned to {label}, but the ADetailer extension is enabled and the pass is unavailable."})

    return {
        "schema": EXTENSION_UNIT_ROUTING_SCHEMA,
        "phase": EXTENSION_UNIT_ROUTING_PHASE,
        "status": "assignment_ready",
        "execution_state": "controlnet_adetailer_region_assignment",
        "controlnet_lanes": controlnet_lanes,
        "adetailer_lanes": adetailer_lanes,
        "counts": {"controlnet": len(controlnet_lanes), "adetailer": len(adetailer_lanes)},
        "messages": messages,
        "disabled_owner_route_cleanup": {
            "phase": "SD-V054-26.9.2b",
            "disabled_owners": sorted(disabled_owners),
            "policy": "Stale ControlNet/ADetailer region routes are ignored when their owner extension is disabled or not submitted.",
        },
        "policy": {
            "controlnet": "Only assigned owner-extension ControlNet units are routed through the selected Scene Director region mask.",
            "adetailer": "Only assigned owner-extension ADetailer passes are routed through the selected Scene Director region mask.",
            "ipadapter": "Still contract-only; execution remains disabled.",
            "lora": "LoRA Stack rows are resolved by Scene Director lora_row_ids and can execute as masked regional finish passes when required nodes are available.",
        },
    }


def apply_controlnet_adetailer_assignments_to_scene_graph_v054(scene_graph: Any, assignments: Any) -> dict[str, Any]:
    graph = deepcopy(scene_graph if isinstance(scene_graph, dict) else {})
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    if not regions or not isinstance(assignments, dict):
        return graph
    cn_by_region = {str(item.get("region_id")): item for item in assignments.get("controlnet_lanes") or [] if isinstance(item, dict)}
    ad_by_region = {str(item.get("region_id")): item for item in assignments.get("adetailer_lanes") or [] if isinstance(item, dict)}
    next_regions = []
    for region in regions:
        if not isinstance(region, dict):
            next_regions.append(region)
            continue
        rid = str(region.get("id") or "")
        item = deepcopy(region)
        if rid in cn_by_region:
            lane = cn_by_region[rid]
            item["control"] = {
                **deepcopy(item.get("control") if isinstance(item.get("control"), dict) else {}),
                "enabled": True,
                "uid": lane.get("uid"),
                "type": lane.get("type"),
                "preprocessor": lane.get("preprocessor"),
                "model": lane.get("model"),
                "image_name": lane.get("image_name"),
                "reference_id": lane.get("image_name"),
                "strength": lane.get("strength"),
                "start": lane.get("start"),
                "end": lane.get("end"),
                "mask_mode": lane.get("mask_mode") or "region",
                "source": "extension_unit_assignment",
                "owner_unit_id": lane.get("assigned_unit_id"),
                "route_mode": lane.get("route_mode"),
                "raw_strength": lane.get("raw_strength"),
                "raw_start": lane.get("raw_start"),
                "raw_end": lane.get("raw_end"),
                "strength_cap": lane.get("strength_cap"),
                "end_cap": lane.get("end_cap"),
                "mask_blend_strength": lane.get("mask_blend_strength"),
                "softened": lane.get("softened"),
            }
        if rid in ad_by_region:
            lane = ad_by_region[rid]
            item["detailer"] = {
                **deepcopy(item.get("detailer") if isinstance(item.get("detailer"), dict) else {}),
                "enabled": True,
                "uid": lane.get("uid"),
                "mode": lane.get("mode"),
                "detector": lane.get("detector"),
                "detector_model": lane.get("detector_model") or lane.get("detector"),
                "detector_type": lane.get("detector_type") or "bbox",
                "custom_classes": lane.get("custom_classes"),
                "denoise": lane.get("denoise"),
                "steps": lane.get("steps"),
                "cfg": lane.get("cfg"),
                "mask_feather": lane.get("mask_feather"),
                "mask_blur": lane.get("mask_blur"),
                "detect_inside_region": lane.get("detect_inside_region", True),
                "mask_mode": lane.get("mask_mode") or "region",
                "source": "extension_unit_assignment",
                "owner_pass_id": lane.get("assigned_pass_id"),
            }
        next_regions.append(item)
    graph["regions"] = next_regions
    metadata = graph.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["controlnet_adetailer_region_assignments"] = deepcopy(assignments)
    return graph


__all__ = [
    "EXTENSION_UNIT_ROUTING_SCHEMA",
    "EXTENSION_UNIT_ROUTING_PHASE",
    "normalize_extension_routes_v054",
    "normalize_ipadapter_identity_profiles_v054",
    "owner_extension_state_v054",
    "disabled_owner_route_keys_v054",
    "strip_disabled_owner_routes_v054",
    "extension_routes_have_selection",
    "build_extension_unit_routing_contract_v054",
    "build_controlnet_adetailer_region_assignments_v054",
    "apply_controlnet_adetailer_assignments_to_scene_graph_v054",
    "build_lora_region_assignments_v054",
    "apply_lora_assignments_to_scene_graph_v054",
]
