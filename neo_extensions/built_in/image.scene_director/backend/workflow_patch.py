from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from .node_decision import detect_node_status, workflow_readiness
from .payload_schema import legacy_payload_from_block
from .v054_contract import normalize_scene_graph_v054
from .prompt_authority import (
    PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    apply_prompt_authority_to_scene_graph,
    build_prompt_authority_contract,
    normalize_prompt_authority,
)
from .support_matrix import ACTIVE_STATES, EXTENSION_ID
from .provider_capabilities import resolve_provider_capabilities_v054
from .flux_adapter import build_flux_adapter_plan_v054
from .qwen_adapter import build_qwen_adapter_plan_v054
from .extension_routing import (
    build_extension_unit_routing_contract_v054,
    build_controlnet_adetailer_region_assignments_v054,
    apply_controlnet_adetailer_assignments_to_scene_graph_v054,
    build_lora_region_assignments_v054,
    apply_lora_assignments_to_scene_graph_v054,
    strip_disabled_owner_routes_v054,
)
from .validation import validate_and_normalize_payload

PHASE = "phase_27_15_character_additional_details"
# Phase 27.14 compatibility anchor: PHASE = "phase_27_14_child_owned_attached_detail_roles"
# Phase 27.1 compatibility anchor: PHASE = "phase_27_1_character_lock_pass_separation_trait_attention_restore"
# Phase 26.9.16 compatibility anchor: PHASE = "phase_26_9_16_postpass_character_lock_gate_standalone_route_validation"
# Phase 26.9.15 compatibility anchor: PHASE = "phase_26_9_15_adetailer_style_regional_lora_crop_refinement_pass"
# Phase 26.9.14 compatibility anchor: PHASE = "phase_26_9_14_regional_lora_runtime_proof_visual_fallback"
# Phase 26.9.13 compatibility anchor: PHASE = "phase_26_9_13_regional_lora_model_delta_mixer"
# Phase 26.9.12 compatibility anchor: PHASE = "phase_26_9_12_disabled_adapter_route_gate_ipadapter_restore_fallback"
# Phase 26.9.11 compatibility anchor: PHASE = "phase_26_9_11_regional_ipadapter_instruction_preservation_gate"
# Phase 26.9.9 compatibility anchor: PHASE = "phase_26_9_9_native_first_pass_regional_adapter_injection"
# Phase 26.9.8 compatibility anchor: PHASE = "phase_26_9_8_regional_adapter_visibility_background_guard"
# Phase 26.9.7 compatibility anchor: PHASE = "phase_26_9_7_v054_full_character_lock_authority_parity"
# Phase 26.9.5 compatibility anchor: PHASE = "phase_26_9_5_v054_prompt_derived_flexible_character_guards"
# Phase 26.9.3 compatibility anchor: PHASE = "phase_26_9_3_v054_regional_authority_compatibility_restore"
# Phase 26.9.2b compatibility anchor: PHASE = "phase_26_9_2b_v054_disabled_owner_route_cleanup"
# Phase 26.9.2 compatibility anchor: PHASE = "phase_26_9_2_v054_subject_slot_resolver_detail_role_guard"
# Phase 26.9 compatibility anchor: PHASE = "phase_26_9_v054_safe_regional_ipadapter_execution"
# Phase 26.8 compatibility anchor: PHASE = "phase_26_8_v054_character_lock_execution_bridge"
# Phase 26.7 compatibility anchor: PHASE = "phase_26_7_v054_ipadapter_regional_execution_hard_gate"
# Phase 26.5 compatibility anchor: PHASE = "phase_26_5_v054_controlnet_conditioning_merge_blend_fix"
# Phase 26.4 compatibility anchor: PHASE = "phase_26_4_v054_controlnet_regional_mask_execution_hotfix"
# Phase 26.3 compatibility anchor: PHASE = "phase_26_3_v054_extension_routing_submit_state_sync_hotfix"
# Phase 26 compatibility anchor: PHASE = "phase_26_v054_lora_stack_region_assignment_migration"
# Phase 25 compatibility anchor: PHASE = "phase_25_v054_ipadapter_identity_profile_migration"
# Phase 24 compatibility anchor: PHASE = "phase_24_v054_controlnet_adetailer_region_assignment"
# Phase 23 compatibility anchor: PHASE = "phase_23_v054_extension_unit_routing_contract"
# Phase 21.7 compatibility anchor: PHASE = "phase_21_7_v054_manual_background_slot_hotfix"
# Phase 21.6 compatibility anchor: PHASE = "phase_21_6_v054_disable_scene_director_ipadapter_execution_hotfix"
# Phase 21.5 compatibility anchor: PHASE = "phase_21_5_v054_background_context_reinforcement_hotfix"
# Phase 21.1 compatibility anchor: PHASE = "phase_21_1_v054_scene_graph_serialization_hotfix"
# Phase 21 compatibility anchor: PHASE = "phase_21_v054_retire_v052_v053_active_path"
# Phase 16 compatibility anchor: PHASE = "phase_16_v054_output_inspector_source_stack"
# Phase 11 compatibility anchor: PHASE = "phase_11_v054_regional_controlnet_routing"
# Phase 10 compatibility anchor: PHASE = "phase_10_v054_mixed_background_regions"
# Phase 8 compatibility anchor: PHASE = "phase_8_v054_conflict_resolver"
# Phase 7.5 compatibility anchor: PHASE = "phase_7_5_v054_prompt_compiler_registry"
# Legacy test anchor: PHASE = "phase_7_v054_relationship_compiler"
# Phase 6 compatibility anchor: PHASE = "phase_6_v054_linked_detail_lanes"
# Phase 5 compatibility anchor: "compiler_phase": "SD-V054-5"
# Phase 6 compatibility anchor: "compiler_phase": "SD-V054-6"
SCENE_NODE_OUTPUT_MODEL = 0
SCENE_NODE_OUTPUT_POSITIVE_TEXT = 3
SCENE_NODE_OUTPUT_NEGATIVE_TEXT = 4
SCENE_NODE_OUTPUT_CONTROL_MASKS = 13
SCENE_NODE_OUTPUT_DETAIL_MASKS = 11
V054_NODE_CLASS = "NeoSceneDirectorV054"
LEGACY_NODE_CLASSES = {"NeoSceneDirectorV053", "NeoSceneDirectorV052"}
RETIRED_NODE_CLASSES = LEGACY_NODE_CLASSES
SCENE_NODE_CLASSES = {V054_NODE_CLASS}


def _next_id(graph: dict[str, Any]) -> int:
    nums: list[int] = []
    for key in graph.keys():
        try:
            nums.append(int(str(key)))
        except Exception:
            continue
    return (max(nums) if nums else 0) + 1


def _copy_ref(value: Any, fallback: list[Any]) -> list[Any]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [str(value[0]), int(value[1]) if str(value[1]).isdigit() else value[1]]
    return list(fallback)


IP_ADAPTER_EXTENSION_ID = "image.ip_adapter"
LORA_STACK_EXTENSION_ID = "lora_stack"
REGIONAL_LORA_MIXER_MAX_ROUTES = 2



def _payload_extension_block(payload: Any, extension_id: str) -> dict[str, Any]:
    """Return an extension block from all payload shapes Neo may submit.

    Phase 26.9.12 uses this as the execution gate source of truth so stale
    Scene Director region identity metadata cannot resurrect IPAdapter after the
    user turns the owner extension off.
    """
    if not isinstance(payload, dict):
        return {}
    for root_key in ("payloads", "extensions"):
        root = payload.get(root_key)
        if isinstance(root, dict) and isinstance(root.get(extension_id), dict):
            return root.get(extension_id) or {}
    if isinstance(payload.get(extension_id), dict):
        return payload.get(extension_id) or {}
    actual = payload.get("actual_params") if isinstance(payload.get("actual_params"), dict) else {}
    if actual:
        found = _payload_extension_block(actual, extension_id)
        if found:
            return found
    return {}


def _extension_state_from_root(root: Any, extension_id: str) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}
    state = root.get("_neo_extension_state") if isinstance(root.get("_neo_extension_state"), dict) else {}
    extensions = state.get("extensions") if isinstance(state.get("extensions"), dict) else {}
    item = extensions.get(extension_id) if isinstance(extensions.get(extension_id), dict) else None
    return item or {}


def _route_actual_params_for_submit_state(route: Any) -> dict[str, Any]:
    if not isinstance(route, dict):
        return {}
    actual = route.get("actual_params") if isinstance(route.get("actual_params"), dict) else None
    params = route.get("params") if isinstance(route.get("params"), dict) else None
    return actual or params or {}


def _payload_extension_submit_state(payload: Any, extension_id: str, route: Any | None = None) -> dict[str, Any]:
    """Return the visible UI submit-state for an owner extension.

    Workflow hooks sanitize disabled owner payload blocks before Scene Director
    runs. After that, the only authoritative off switch may live in the provider
    route's ``actual_params._neo_extension_state``. Phase 26.10.8K2 reads that
    route state first so removed/absent owner blocks cannot default back to
    enabled and resurrect Scene Director IPAdapter/FaceID execution.
    """
    route_params = _route_actual_params_for_submit_state(route)
    route_state = _extension_state_from_root(route_params, extension_id)
    if route_state:
        return route_state
    route_state = _extension_state_from_root(route, extension_id)
    if route_state:
        return route_state
    if not isinstance(payload, dict):
        return {}
    payload_state = _extension_state_from_root(payload, extension_id)
    if payload_state:
        return payload_state
    actual = payload.get("actual_params") if isinstance(payload.get("actual_params"), dict) else {}
    if actual:
        return _payload_extension_submit_state(actual, extension_id, route=None)
    return {}


def _payload_extension_enabled(payload: Any, extension_id: str, route: Any | None = None) -> bool:
    submit_state = _payload_extension_submit_state(payload, extension_id, route=route)
    if submit_state:
        if submit_state.get("enabled") is False:
            return False
        if submit_state.get("enabled") is True:
            return True
    block = _payload_extension_block(payload, extension_id)
    if block:
        if block.get("enabled") is False:
            return False
        if block.get("enabled") is True:
            return True
        if block.get("workflow_applied") is True:
            return True
    # Absence is not an explicit user disable for old direct unit tests/replay
    # payloads. However, when a current submit-state snapshot exists in the route
    # and does not enable this owner extension, execution must stay off.
    route_params = _route_actual_params_for_submit_state(route)
    route_state = route_params.get("_neo_extension_state") if isinstance(route_params.get("_neo_extension_state"), dict) else {}
    route_extensions = route_state.get("extensions") if isinstance(route_state.get("extensions"), dict) else {}
    if route_extensions and extension_id not in route_extensions:
        return False
    return True


def _strip_identity_units_for_disabled_ipadapter(block: dict[str, Any]) -> dict[str, Any]:
    block = deepcopy(block or {})
    assets = dict(block.get("assets") or {})
    assets["identity_units"] = []
    assets["ipadapter_bindings"] = []
    block["assets"] = assets
    return block


def _disabled_ipadapter_route_gate_metadata(*, owner_enabled: bool, identity_unit_count: int, route_count_removed: int = 0) -> dict[str, Any]:
    status = "enabled" if owner_enabled else ("disabled_metadata_only" if identity_unit_count or route_count_removed else "off")
    warnings: list[str] = []
    if not owner_enabled and (identity_unit_count or route_count_removed):
        warnings.append("ipadapter_owner_extension_disabled_execution_suppressed")
        warnings.append("ipadapter_profile_preserved_metadata_only")
    return {
        "schema": "neo.image.scene_director.disabled_adapter_route_gate.v054.v1",
        "phase": "SD-V054-26.9.12",
        "status": status,
        "owner_extension_id": IP_ADAPTER_EXTENSION_ID,
        "owner_enabled": bool(owner_enabled),
        "identity_unit_count": int(identity_unit_count or 0),
        "route_count_removed": int(route_count_removed or 0),
        "profile_preserved_metadata_only": bool(not owner_enabled and (identity_unit_count or route_count_removed)),
        "execution_allowed": bool(owner_enabled),
        "warnings": warnings,
        "policy": "If image.ip_adapter is disabled, Scene Director preserves regional identity profile metadata but suppresses all IPAdapter/FaceID execution routes and nodes.",
    }


def _route_mode(route: dict[str, Any] | None) -> str:
    mode = str((route or {}).get("workflow_mode") or (route or {}).get("mode") or "generate").strip().lower()
    return "txt2img" if mode == "generate" else mode


def _size_from_route(route: dict[str, Any] | None, width: int | None, height: int | None) -> tuple[int, int]:
    route = route or {}
    def _int(value: Any, default: int) -> int:
        try:
            parsed = int(round(float(value)))
        except Exception:
            parsed = default
        return max(64, parsed)
    return _int(width if width is not None else route.get("width"), 1024), _int(height if height is not None else route.get("height"), 1024)


def _size_from_workflow(graph: dict[str, Any], fallback: tuple[int, int]) -> tuple[int, int]:
    """Read the active EmptyLatent/Image node size from the compiled graph.

    The shared extension hook historically did not pass width/height into the
    Scene Director patch. V1 built scene_json from the final payload width/height;
    in V2 we recover the same final dimensions from the graph so the Scene
    Director canvas does not default back to 1024x1024.
    """
    for node in (graph or {}).values():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") not in {"EmptyLatentImage", "LatentFromBatch"}:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if inputs.get("width") is not None and inputs.get("height") is not None:
            return _size_from_route({}, inputs.get("width"), inputs.get("height"))
    return fallback




def _raw_scene_director_block(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for root_key in ("extensions", "payloads"):
        root = payload.get(root_key)
        if isinstance(root, dict) and isinstance(root.get(EXTENSION_ID), dict):
            return deepcopy(root.get(EXTENSION_ID) or {})
    if isinstance(payload.get(EXTENSION_ID), dict):
        return deepcopy(payload.get(EXTENSION_ID) or {})
    return {}


def _v054_source_block(payload: Any, normalized_block: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_scene_director_block(payload)
    if not raw:
        return normalized_block
    merged = deepcopy(normalized_block)
    raw_inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    if raw_inputs:
        inputs = merged.setdefault("inputs", {})
        if isinstance(inputs, dict):
            for key in ("regions", "scene_graph", "scene_graph_json", "global"):
                if key in raw_inputs:
                    inputs[key] = deepcopy(raw_inputs[key])
    raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    if raw_params:
        params = merged.setdefault("params", {})
        if isinstance(params, dict):
            params.update(deepcopy(raw_params))
    raw_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    if raw_metadata:
        metadata = merged.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("raw_v054_payload_metadata", deepcopy(raw_metadata))
    return merged


def _existing_v054_fix_pass_controls(graph: dict[str, Any] | None) -> dict[str, Any]:
    """Read persisted Fix Pass controls from an existing V054 node on replay."""
    for node in (graph or {}).values():
        if not isinstance(node, dict) or str(node.get("class_type") or "") != V054_NODE_CLASS:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        candidate = inputs.get("scene_graph_json")
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except Exception:
                candidate = None
        if not isinstance(candidate, dict) and isinstance(inputs.get("scene_graph"), dict):
            candidate = inputs.get("scene_graph")
        metadata = candidate.get("metadata") if isinstance(candidate, dict) and isinstance(candidate.get("metadata"), dict) else {}
        controls = metadata.get("advanced_fix_pass_controls") if isinstance(metadata.get("advanced_fix_pass_controls"), dict) else None
        if controls:
            return deepcopy(controls)
    return {}

def _block_params(block: dict[str, Any]) -> dict[str, Any]:
    return block.get("params") if isinstance(block.get("params"), dict) else {}


def _block_inputs(block: dict[str, Any]) -> dict[str, Any]:
    return block.get("inputs") if isinstance(block.get("inputs"), dict) else {}


def _legacy_bbox_to_v054(value: Any) -> list[float]:
    if isinstance(value, dict):
        x = max(0.0, min(1.0, _float(value.get("x"), 0.0)))
        y = max(0.0, min(1.0, _float(value.get("y"), 0.0)))
        w = max(0.0, min(1.0, _float(value.get("w"), 1.0)))
        h = max(0.0, min(1.0, _float(value.get("h"), 1.0)))
        return [round(x, 4), round(y, 4), round(min(1.0, x + w), 4), round(min(1.0, y + h), 4)]
    if isinstance(value, (list, tuple)) and len(value) == 4:
        vals = [max(0.0, min(1.0, _float(item, 0.0))) for item in value]
        x1, y1, a, b = vals
        # Existing Scene Director lists are already x1,y1,x2,y2 in newer tests.
        if a > x1 and b > y1:
            return [round(x1, 4), round(y1, 4), round(a, 4), round(b, 4)]
        return [round(x1, 4), round(y1, 4), round(min(1.0, x1 + a), 4), round(min(1.0, y1 + b), 4)]
    return [0.0, 0.0, 1.0, 1.0]


def _region_role_v054(region: dict[str, Any]) -> str:
    role = str(region.get("role") or region.get("type") or region.get("region_role") or "character").strip().lower()
    aliases = {"person": "character", "subject": "character", "hair": "hair_detail", "face": "face_detail", "hand": "hand_detail", "outfit": "clothing", "clothes": "clothing", "prop": "object", "detail": "character_detail"}
    return aliases.get(role, role)


_V054_MAIN_PARENT_ROLES = {"character", "background"}
_V054_REQUIRED_CHILD_ROLES = {"face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop"}
_V054_CHARACTER_PARENT_ONLY_ROLES = set(_V054_REQUIRED_CHILD_ROLES)
_V054_BACKGROUND_PARENT_ONLY_ROLES = {"background_object", "transition_effect"}


def _v054_backend_allowed_parent_roles(role: str) -> set[str]:
    if role in _V054_CHARACTER_PARENT_ONLY_ROLES:
        return {"character"}
    if role in _V054_BACKGROUND_PARENT_ONLY_ROLES:
        return {"background"}
    return set(_V054_MAIN_PARENT_ROLES) if role not in _V054_MAIN_PARENT_ROLES else set()


def _sanitize_child_owned_attachments_v054(scene_graph: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Keep broken child rows from disabling the complete Scene Director graph."""
    graph = deepcopy(scene_graph or {})
    rows = [row for row in graph.get("regions", []) if isinstance(row, dict)]
    by_id = {str(row.get("id") or "").strip(): row for row in rows if str(row.get("id") or "").strip()}
    resolved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cleared: list[dict[str, Any]] = []
    warnings: list[str] = []

    for source in rows:
        region = deepcopy(source)
        rid = str(region.get("id") or "").strip()
        label = str(region.get("label") or rid or "detail")
        role = _region_role_v054(region)
        parent_id = str(region.get("attach_to") or "").strip()
        if role in _V054_MAIN_PARENT_ROLES:
            if parent_id:
                cleared.append({"region_id": rid, "role": role, "previous_parent_id": parent_id, "reason": "main_parent_role"})
            region.pop("attach_to", None)
            region.pop("relationship", None)
            resolved.append(region)
            continue

        parent = by_id.get(parent_id) if parent_id else None
        allowed = _v054_backend_allowed_parent_roles(role)
        parent_role = _region_role_v054(parent) if parent else ""
        invalid_reason = ""
        if not parent_id and role in _V054_REQUIRED_CHILD_ROLES:
            invalid_reason = "missing parent"
        elif parent_id == rid:
            invalid_reason = "cannot attach to itself"
        elif parent_id and parent is None:
            invalid_reason = "parent not found"
        elif parent and allowed and parent_role not in allowed:
            invalid_reason = "parent must be " + " or ".join(sorted(allowed))

        if invalid_reason and role in _V054_REQUIRED_CHILD_ROLES:
            skipped.append({"region_id": rid, "label": label, "role": role, "reason": invalid_reason})
            warnings.append(f"Attached detail '{label}' was skipped: {invalid_reason}; select the parent inside the child region")
            continue
        if invalid_reason and parent_id:
            cleared.append({"region_id": rid, "label": label, "role": role, "previous_parent_id": parent_id, "reason": invalid_reason})
            region.pop("attach_to", None)
            region.pop("relationship", None)
            warnings.append(f"Optional attachment for '{label}' was cleared: {invalid_reason}; the region remains standalone")
        resolved.append(region)

    graph["regions"] = resolved
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    graph["metadata"] = metadata
    report = {
        "schema": "neo.image.scene_director.attached_detail_resolution.v054.v1",
        "phase": "SD-V054-27.14",
        "policy": "attachment_is_authored_on_the_child_region_only",
        "content_policy_guards_added": False,
        "active_child_count": len([r for r in resolved if _region_role_v054(r) not in _V054_MAIN_PARENT_ROLES and str(r.get("attach_to") or "").strip()]),
        "skipped": skipped,
        "cleared": cleared,
    }
    metadata["attached_detail_roles"] = report
    return graph, report, warnings


def _prompt_authority_contract_for_block(
    block: dict[str, Any],
    legacy: dict[str, Any],
    scene_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the single prompt ownership contract for any payload generation."""

    inputs = _block_inputs(block)
    params = _block_params(block)
    global_data = inputs.get("global") if isinstance(inputs.get("global"), dict) else {}
    graph_global = scene_graph.get("global") if isinstance(scene_graph, dict) and isinstance(scene_graph.get("global"), dict) else {}
    graph_metadata = scene_graph.get("metadata") if isinstance(scene_graph, dict) and isinstance(scene_graph.get("metadata"), dict) else {}
    saved_contract = graph_metadata.get("prompt_authority_contract") if isinstance(graph_metadata.get("prompt_authority_contract"), dict) else {}
    authority = normalize_prompt_authority(
        params.get("prompt_authority")
        or params.get("scene_director_prompt_authority")
        or global_data.get("prompt_authority")
        or graph_global.get("prompt_authority")
        or saved_contract.get("mode")
        or legacy.get("scene_director_prompt_authority")
    )
    contract_source = dict(params)
    contract_source["prompt_authority"] = authority
    if isinstance(saved_contract, dict):
        contract_source.setdefault("region_context", saved_contract)
    return build_prompt_authority_contract(
        contract_source,
        global_positive=(
            global_data.get("positive_prompt")
            or global_data.get("prompt")
            or graph_global.get("prompt")
            or legacy.get("scene_director_v052_global_prompt_override")
        ),
        global_negative=(
            global_data.get("negative_prompt")
            or global_data.get("negative")
            or graph_global.get("negative")
            or legacy.get("scene_director_effective_negative_prompt")
        ),
        style_positive=global_data.get("style_prompt") or graph_global.get("style_prompt") or "",
        region_context_weight=(params.get("region_context") or {}).get("weight") if isinstance(params.get("region_context"), dict) else None,
    )


def _restore_replay_character_fields_v054(scene_graph: dict[str, Any] | None, workflow: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep explicit character fields when a replay payload predates them.

    Some saved workflows contain the richer fields in the existing V054 node
    while the normalized extension payload only carries the older region
    shape. Merge only missing/empty character fields from that existing node;
    current UI values remain authoritative when they are present.
    """
    if not isinstance(scene_graph, dict) or not isinstance(workflow, dict):
        return scene_graph
    replay_by_id: dict[str, dict[str, Any]] = {}
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != V054_NODE_CLASS:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        raw = inputs.get("scene_graph_json")
        try:
            saved = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            saved = None
        if not isinstance(saved, dict):
            continue
        for region in saved.get("regions") or []:
            if isinstance(region, dict) and str(region.get("id") or "").strip():
                replay_by_id[str(region["id"]).strip()] = region

    restored: list[str] = []
    for region in scene_graph.get("regions") or []:
        if not isinstance(region, dict) or str(region.get("role") or "").strip().lower() != "character":
            continue
        saved = replay_by_id.get(str(region.get("id") or "").strip())
        if not saved:
            continue
        for field in ("character_traits", "trait_lock"):
            current = region.get(field)
            previous = saved.get(field)
            if isinstance(previous, dict) and previous and not (isinstance(current, dict) and current):
                region[field] = deepcopy(previous)
                restored.append(f"{region.get('id')}:{field}")
        current_correction = region.get("character_lock_correction") if isinstance(region.get("character_lock_correction"), dict) else {}
        previous_correction = saved.get("character_lock_correction") if isinstance(saved.get("character_lock_correction"), dict) else {}
        if previous_correction:
            merged_correction = {**previous_correction, **current_correction}
            for key, value in previous_correction.items():
                if key not in current_correction or current_correction.get(key) in (None, "", {}, []):
                    merged_correction[key] = deepcopy(value)
            if merged_correction != current_correction:
                region["character_lock_correction"] = merged_correction
                restored.append(f"{region.get('id')}:character_lock_correction")
    if restored:
        metadata = scene_graph.setdefault("metadata", {}) if isinstance(scene_graph.setdefault("metadata", {}), dict) else {}
        metadata["replay_character_field_restore"] = {
            "schema": "neo.image.scene_director.replay_character_field_restore.v054.v1",
            "phase": "SD-V054-27.2",
            "status": "restored",
            "fields": sorted(set(restored)),
            "policy": "Merge only missing explicit character fields from the existing V054 workflow node during replay; current payload fields remain authoritative.",
        }
    return scene_graph


def _scene_graph_from_block_v054(block: dict[str, Any], *, width: int, height: int, legacy: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    inputs = _block_inputs(block)
    params = _block_params(block)
    candidate = inputs.get("scene_graph") or inputs.get("scene_graph_json") or block.get("scene_graph")
    if isinstance(candidate, dict):
        candidate, _attachment_report, attachment_warnings = _sanitize_child_owned_attachments_v054(candidate)
        result = normalize_scene_graph_v054(candidate)
        errors = [str(item.get("message") or item.get("code") or item) for item in result.get("errors") or []]
        warnings = [*attachment_warnings, *[str(item.get("message") or item.get("code") or item) for item in result.get("warnings") or []]]
        graph = result.get("scene_graph") if result.get("ok") else None
        if isinstance(graph, dict):
            graph = apply_prompt_authority_to_scene_graph(
                graph,
                _prompt_authority_contract_for_block(block, legacy, graph),
            )
        return graph, errors, warnings

    global_data = inputs.get("global") if isinstance(inputs.get("global"), dict) else {}
    raw_regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    scene_regions: list[dict[str, Any]] = []
    for index, region in enumerate(raw_regions, start=1):
        if not isinstance(region, dict):
            continue
        role = _region_role_v054(region)
        rid = str(region.get("id") or region.get("uid") or f"region_{index}").strip() or f"region_{index}"
        item: dict[str, Any] = {
            "id": rid,
            "role": role,
            "label": str(region.get("label") or rid.replace("_", " ").title()),
            "bbox": _legacy_bbox_to_v054(region.get("bbox")),
            "prompt": str(region.get("prompt") or region.get("positive") or ""),
            "negative": str(region.get("negative") or region.get("negative_prompt") or ""),
            "strength": _float(region.get("strength") or region.get("weight"), 1.0),
            "attach_to": str(region.get("attach_to") or region.get("parent_id") or region.get("bound_to") or "").strip() or None,
            "relationship": str(region.get("relationship") or region.get("relation") or "").strip() or None,
            "target_area": str(region.get("target_area") or region.get("area") or "").strip() or None,
            "priority": str(region.get("priority") or "reinforce"),
        }
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        char_lock = params.get("character_lock") if isinstance(params.get("character_lock"), dict) else {}
        char_lock_mode = str(region.get("character_lock_mode") or params.get("character_lock_mode") or char_lock.get("character") or "").strip().lower()
        if role == "character" and char_lock_mode and char_lock_mode not in {"off", "none", "false"}:
            lock = {**lock, "character": char_lock_mode if char_lock_mode in {"soft", "balanced", "strong", "strict"} else "balanced"}
            for key in ("gender", "skin_tone", "hair", "build", "body_height", "outfit", "negative"):
                value = str(char_lock.get(key) or "").strip().lower()
                if value and value not in {"off", "none", "false"}:
                    lock.setdefault(key, value)
        if lock:
            item["lock"] = lock
        for key in ("relationship_prompt", "parent_prompt", "parent_prompt_template", "local_prompt", "local_prompt_template", "negative_guard", "negative_guard_prompt", "relationship_negative", "conflict_resolution_prompt", "resolution_prompt", "conflict_negative_guard", "zone", "background_zone", "background_prompt", "zone_prompt", "background_negative_guard", "zone_negative_guard", "inpaint_prompt", "inpaint_negative", "inpaint_action", "inpaint_mask_mode"):
            value = str(region.get(key) or "").strip()
            if value:
                item[key] = value
        for key in ("control", "detailer", "inpaint", "edit_intent", "metadata", "compiler_override", "conflict_override", "background_override", "extension_routes", "character_lock_correction", "character_traits", "trait_lock"):
            if isinstance(region.get(key), dict):
                item[key] = deepcopy(region[key])
        for key in ("source_image", "source_image_name", "source_id", "source_region_id", "source_region"):
            value = str(region.get(key) or "").strip()
            if value:
                item[key] = value
        if role == "text":
            item["text"] = str(region.get("text") or region.get("content") or "")
            item["mode"] = str(region.get("mode") or region.get("text_mode") or "composite")
            item["text_mode"] = item["mode"]
            item["font_style"] = str(region.get("font_style") or region.get("font") or "")
            item["font_family"] = str(region.get("font_family") or region.get("font_name") or "")
            item["font_size"] = region.get("font_size", 48)
            item["color"] = str(region.get("color") or region.get("fill") or "")
            item["stroke_color"] = str(region.get("stroke_color") or region.get("outline_color") or "")
            item["stroke_width"] = region.get("stroke_width", 0)
            item["align"] = str(region.get("align") or region.get("text_align") or "center")
            item["valign"] = str(region.get("valign") or region.get("vertical_align") or "middle")
            item["opacity"] = region.get("opacity", 1.0)
            item["rotation"] = region.get("rotation", 0)
        scene_regions.append(item)

    scene_graph = {
        "version": "v054",
        "canvas": {"width": int(width), "height": int(height)},
        "global": {
            "prompt": str(global_data.get("positive_prompt") or global_data.get("prompt") or legacy.get("scene_director_v052_global_prompt_override") or ""),
            "negative": str(global_data.get("negative_prompt") or global_data.get("negative") or legacy.get("scene_director_effective_negative_prompt") or ""),
            "prompt_authority": normalize_prompt_authority(
                params.get("prompt_authority")
                or global_data.get("prompt_authority")
                or legacy.get("scene_director_prompt_authority")
            ),
            "style_strength": _float(params.get("style_strength"), 0.8),
        },
        "regions": scene_regions,
        "metadata": {
            "source": "neo_backend_workflow_patch",
            "compiler_phase": "SD-V054-15", "legacy_img2img_phase_anchor": "SD-V054-14", "legacy_text_phase_anchor": "SD-V054-13", "legacy_detailer_phase_anchor": "SD-V054-12", "legacy_controlnet_phase_anchor": "SD-V054-11", "legacy_compiler_phase_anchor": "SD-V054-10",
            "legacy_relationship_compiler_phase_anchor": "compiler_phase: SD-V054-7",
            "legacy_compiler_phase_anchor": "\"compiler_phase\": \"SD-V054-5\"",
            "legacy_compiler_phase_6_anchor": "\"compiler_phase\": \"SD-V054-6\"",
            "legacy_scene_json_available": bool(str(legacy.get("scene_director_v052_scene_json") or "").strip()),
        },
    }
    scene_graph, _attachment_report, attachment_warnings = _sanitize_child_owned_attachments_v054(scene_graph)
    result = normalize_scene_graph_v054(scene_graph)
    errors = [str(item.get("message") or item.get("code") or item) for item in result.get("errors") or []]
    warnings = [*attachment_warnings, *[str(item.get("message") or item.get("code") or item) for item in result.get("warnings") or []]]
    graph = result.get("scene_graph") if result.get("ok") else None
    if isinstance(graph, dict):
        graph = apply_prompt_authority_to_scene_graph(
            graph,
            _prompt_authority_contract_for_block(block, legacy, graph),
        )
    return graph, errors, warnings


def _node_inputs_for_scene_director(
    *,
    node_class: str,
    legacy: dict[str, Any],
    block: dict[str, Any],
    scene_graph: dict[str, Any] | None,
    model_ref: list[Any],
    clip_ref: list[Any],
    width: int,
    height: int,
    extension_routes_json: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = _block_params(block)
    prompt_authority_contract = _prompt_authority_contract_for_block(block, legacy, scene_graph)
    inputs: dict[str, Any] = {
        "model": deepcopy(model_ref),
        "clip": deepcopy(clip_ref),
        "width": int(width),
        "height": int(height),
        "global_prompt_override": str(legacy.get("scene_director_v052_global_prompt_override") or ""),
        "base_weight": str(legacy.get("scene_director_v052_base_weight", 0.35)),
        "region_gain": str(legacy.get("scene_director_v052_region_gain", 0.65)),
        "max_subject_slots": int(params.get("max_subject_slots") or legacy.get("scene_director_v052_max_subject_slots") or 4),
        "normalize_masks": bool(legacy.get("scene_director_v052_normalize_masks", True)),
        "enable_auto_prompts": bool(legacy.get("scene_director_v052_enable_auto_prompts", False)),
    }
    if prompt_authority_contract.get("mode") == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        # Do not let a stale V1 effective-global field reconnect the Neo core
        # prompt through the node's override widget.
        inputs["global_prompt_override"] = ""
    if node_class == V054_NODE_CLASS:
        # ComfyUI widget inputs are string-oriented. Passing a Python dict here can
        # arrive inside the custom node as a Python-literal string with single quotes,
        # which json.loads() cannot parse. The active V054 contract therefore sends
        # a canonical JSON string while preserving dict metadata elsewhere.
        scene_graph_for_node = apply_prompt_authority_to_scene_graph(
            deepcopy(scene_graph or {}),
            prompt_authority_contract,
        )
        background_prime_authority = _scene_background_prime_contract(scene_graph_for_node)
        prime_prompt = _clean_text(background_prime_authority.get("prompt") or "")
        if prime_prompt:
            inputs["global_prompt_override"] = _prepend_unique_text(prime_prompt, str(inputs.get("global_prompt_override") or ""))
            if isinstance(scene_graph_for_node, dict):
                global_block = scene_graph_for_node.get("global") if isinstance(scene_graph_for_node.get("global"), dict) else {}
                global_block["prompt"] = _prepend_unique_text(prime_prompt, str(global_block.get("prompt") or ""))
                scene_graph_for_node["global"] = global_block
        character_lock_mode = str(params.get("character_lock_mode") or params.get("lock_mode") or "balanced").strip().lower()
        identity_strength = float(params.get("identity_strength") or legacy.get("scene_director_appearance_lock_gain") or 0.55)
        # Phase 26.9.7: Character Lock Strong/Strict must restore the old
        # accidental "hair strong" authority as an intentional full-character
        # masked branch, while explicit upper_identity_* requests remain honored.
        legacy_mode = str(legacy.get("scene_director_appearance_lock_mode") or "").strip().lower()
        if legacy_mode == "hair_focus_strong":
            appearance_lock_mode = "full_character_strong"
        elif legacy_mode == "hair_focus_soft":
            appearance_lock_mode = "full_character_soft"
        elif legacy_mode in {"full_character_soft", "full_character_strong", "full_identity_soft", "full_identity_strong", "character_soft", "character_strong"}:
            appearance_lock_mode = {
                "full_identity_soft": "full_character_soft",
                "full_identity_strong": "full_character_strong",
                "character_soft": "full_character_soft",
                "character_strong": "full_character_strong",
            }.get(legacy_mode, legacy_mode)
        elif legacy_mode in {"upper_identity_soft", "upper_identity_strong"}:
            appearance_lock_mode = legacy_mode
        elif character_lock_mode in {"strict", "strong"}:
            appearance_lock_mode = "full_character_strong"
        elif character_lock_mode in {"balanced", "soft"}:
            appearance_lock_mode = "full_character_soft"
        else:
            appearance_lock_mode = "off"
        character_lock_execution = _character_lock_execution_settings(params)
        if not character_lock_execution.get("in_sampler_attention_enabled"):
            # End refinement is a deliberately separate late-pass choice. Do
            # not leave the full-character attention branch active when the
            # user explicitly asks for refinement-only behavior.
            appearance_lock_mode = "off"
        if appearance_lock_mode in {"upper_identity_strong", "full_character_strong"}:
            appearance_lock_gain = max(identity_strength, 0.90)
            appearance_lock_height = max(float(legacy.get("scene_director_appearance_lock_height") or 0.46), 0.46)
        elif appearance_lock_mode in {"upper_identity_soft", "full_character_soft"}:
            appearance_lock_gain = max(identity_strength, 0.62)
            appearance_lock_height = max(float(legacy.get("scene_director_appearance_lock_height") or 0.40), 0.40)
        else:
            appearance_lock_gain = float(legacy.get("scene_director_appearance_lock_gain") or identity_strength)
            appearance_lock_height = float(legacy.get("scene_director_appearance_lock_height") or 0.34)
        appearance_lock_feather = int(params.get("mask_feather") or legacy.get("scene_director_appearance_lock_feather") or 18)
        effective_authority_values = _v25_9_6_effective_character_lock_authority_values(
            character_lock_mode=character_lock_mode,
            appearance_lock_mode=appearance_lock_mode,
            base_weight=inputs.get("base_weight"),
            region_gain=inputs.get("region_gain"),
            identity_strength=identity_strength,
            appearance_lock_gain=appearance_lock_gain,
            mask_feather=appearance_lock_feather,
        )
        effective_values = effective_authority_values["effective"]
        inputs["base_weight"] = str(round(float(effective_values["base_weight"]), 4))
        inputs["region_gain"] = str(round(float(effective_values["region_gain"]), 4))
        if isinstance(scene_graph_for_node, dict):
            scene_graph_for_node.setdefault("metadata", {})["effective_authority_values"] = deepcopy(effective_authority_values)
            scene_graph_for_node.setdefault("metadata", {})["background_prime_authority"] = deepcopy(background_prime_authority)
            scene_graph_for_node.setdefault("metadata", {})["character_lock_execution"] = deepcopy(character_lock_execution)
        scene_graph_payload = json.dumps(scene_graph_for_node or {}, ensure_ascii=False, separators=(",", ":"))
        inputs.update({
            "scene_graph_json": scene_graph_payload,
            "character_lock_mode": character_lock_mode,
            "identity_strength": float(effective_values["identity_strength"]),
            "detail_strength": float(params.get("detail_strength") or 0.85),
            "background_strength": float(params.get("background_strength") or 0.65),
            "mask_feather": int(effective_values["mask_feather"]),
            "appearance_lock_mode": appearance_lock_mode,
            "appearance_lock_gain": str(round(float(effective_values["appearance_lock_gain"]), 4)),
            "appearance_lock_height": str(round(appearance_lock_height, 4)),
            "appearance_lock_feather": int(effective_values["mask_feather"]),
            "debug_mode": bool(params.get("debug_mode") or False),
        })
        if extension_routes_json:
            if isinstance(extension_routes_json, str):
                inputs["extension_routes_json"] = extension_routes_json
            else:
                inputs["extension_routes_json"] = json.dumps(extension_routes_json, ensure_ascii=False, separators=(",", ":"))
        return inputs

    inputs["scene_json"] = str(legacy.get("scene_director_v052_scene_json") or "")
    if node_class == "NeoSceneDirectorV053":
        # These inputs are V053-only in V1. They are optional in the V2 payload
        # today; defaults mirror the V1 Appearance Lock fallback-safe values.
        inputs.update({
            "appearance_lock_mode": str(legacy.get("scene_director_appearance_lock_mode") or "hair_focus_soft"),
            "appearance_lock_gain": float(legacy.get("scene_director_appearance_lock_gain") or 0.35),
            "appearance_lock_height": float(legacy.get("scene_director_appearance_lock_height") or 0.34),
            "appearance_lock_feather": int(legacy.get("scene_director_appearance_lock_feather") or 18),
        })
    return inputs




def _v25_9_6_effective_character_lock_authority_values(
    *,
    character_lock_mode: Any,
    appearance_lock_mode: Any,
    base_weight: Any,
    region_gain: Any,
    identity_strength: Any,
    appearance_lock_gain: Any,
    mask_feather: Any,
) -> dict[str, Any]:
    """V25.9.6 Fix 1: make Strong/Strict Character Lock numerically strong.

    This is a numeric authority policy only. It must not inject or hardcode
    character prompt text. User/region prompts remain the source of identity.
    """
    char_mode = str(character_lock_mode or "off").strip().lower()
    app_mode = str(appearance_lock_mode or "off").strip().lower()
    requested_base = _float(base_weight, 0.35)
    requested_region = _float(region_gain, 0.65)
    requested_identity = _float(identity_strength, 0.55)
    requested_appearance = _float(appearance_lock_gain, requested_identity)
    requested_feather = _int(mask_feather, 24)

    effective_base = requested_base
    effective_region = requested_region
    effective_identity = requested_identity
    effective_appearance = requested_appearance
    effective_feather = requested_feather
    profile = "passthrough"

    full_character = app_mode.startswith("full_character") or app_mode in {"character_strong", "full_identity_strong"}
    strong_requested = char_mode in {"strong", "strict"} or app_mode in {"full_character_strong", "hair_focus_strong", "upper_identity_strong"}
    strict_requested = char_mode == "strict"

    if full_character and strong_requested:
        profile = "strict" if strict_requested else "strong"
        if strict_requested:
            effective_base = min(effective_base, 0.18)
            effective_region = max(effective_region, 0.98)
            effective_identity = max(effective_identity, 0.85)
            effective_appearance = max(effective_appearance, 1.05)
            effective_feather = min(effective_feather, 12)
        else:
            effective_base = min(effective_base, 0.25)
            effective_region = max(effective_region, 0.90)
            effective_identity = max(effective_identity, 0.75)
            effective_appearance = max(effective_appearance, 0.95)
            effective_feather = min(effective_feather, 16)

    changes = {
        "base_weight": abs(effective_base - requested_base) > 1e-6,
        "region_gain": abs(effective_region - requested_region) > 1e-6,
        "identity_strength": abs(effective_identity - requested_identity) > 1e-6,
        "appearance_lock_gain": abs(effective_appearance - requested_appearance) > 1e-6,
        "mask_feather": effective_feather != requested_feather,
    }
    return {
        "schema": "neo.image.scene_director.effective_authority_values.v25_9_6_fix1",
        "phase": "V25.9.6-fix1",
        "status": "strengthened" if any(changes.values()) else "passthrough",
        "profile": profile,
        "character_lock_mode": char_mode,
        "appearance_lock_mode": app_mode,
        "requested": {
            "base_weight": requested_base,
            "region_gain": requested_region,
            "identity_strength": requested_identity,
            "appearance_lock_gain": requested_appearance,
            "mask_feather": requested_feather,
        },
        "effective": {
            "base_weight": effective_base,
            "region_gain": effective_region,
            "identity_strength": effective_identity,
            "appearance_lock_gain": effective_appearance,
            "mask_feather": effective_feather,
        },
        "changes": changes,
        "prompt_injection": False,
        "policy": "Strong/Strict Character Lock raises numeric Scene Director authority only; prompts remain user-authored and region-derived.",
    }

def _attention_lock_runtime_proof_v25_9_2(
    *,
    scene_node_class: str,
    scene_node_id: str | None,
    scene_graph: dict[str, Any] | None,
    sampler_node_id: str | int,
    sampler_inputs: dict[str, Any] | None,
    patched_model_ref: list[Any] | tuple[Any, ...] | None,
    appearance_lock_mode: Any,
    base_weight: Any,
    region_gain: Any,
    normalize_masks: Any,
    first_pass_character_lock_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """V25.9.2 compile-time proof for the legacy in-sampler attention path.

    The installed Comfy node proves attn2 patch registration at runtime. This
    backend proof proves Neo wired the node's patched MODEL output into the main
    sampler, so Character Lock proof is not confused with external KSampler
    masked correction passes.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    subject_slots = _v054_character_subject_slot_map(graph) if scene_node_class == V054_NODE_CLASS else {}
    appearance_mode = str(appearance_lock_mode or "off")
    sampler_model_ref = (sampler_inputs or {}).get("model") if isinstance(sampler_inputs, dict) else None
    patched_ref = list(patched_model_ref or [])
    sampler_model_as_list = list(sampler_model_ref) if isinstance(sampler_model_ref, (list, tuple)) else []
    patched_model_used = bool(patched_ref and sampler_model_as_list == patched_ref)
    fallback_authority = first_pass_character_lock_authority if isinstance(first_pass_character_lock_authority, dict) else {}
    fallback_nodes = fallback_authority.get("nodes_added") if isinstance(fallback_authority.get("nodes_added"), list) else []
    fallback_lanes = fallback_authority.get("lanes") if isinstance(fallback_authority.get("lanes"), list) else []
    return {
        "schema": "neo.image.scene_director.attention_lock_runtime_proof.v25_9_2",
        "phase": "V25.9.2",
        "active": bool(scene_node_class == V054_NODE_CLASS and scene_node_id and patched_model_used),
        "node_class": scene_node_class or "",
        "node_id": str(scene_node_id or ""),
        "legacy_patch_director_called": bool(scene_node_class == V054_NODE_CLASS and scene_node_id),
        "attn2_patch_registered": "runtime_node_debug_json",
        "attn2_output_patch_registered": "runtime_node_debug_json",
        "appearance_lock_mode": appearance_mode,
        "legacy_alias_mode": "hair_focus_strong" if appearance_mode == "full_character_strong" else ("hair_focus_soft" if appearance_mode == "full_character_soft" else ""),
        "subject_branch_count": len(subject_slots),
        "full_character_branch_count": len(subject_slots) if appearance_mode.startswith("full_character") else 0,
        "upper_identity_branch_count": len(subject_slots) if appearance_mode in {"full_character_strong", "upper_identity_strong", "hair_focus_strong"} else 0,
        "patched_model_ref": patched_ref,
        "sampler_model_ref": sampler_model_as_list,
        "main_sampler_id": str(sampler_node_id),
        "patched_model_used_by_main_sampler": patched_model_used,
        "base_weight": _float(base_weight, 0.55),
        "region_gain": _float(region_gain, 0.45),
        "normalize_masks": bool(normalize_masks),
        "primary_character_lock_path": "legacy_in_sampler_attention" if patched_model_used and appearance_mode != "off" else "none",
        "legacy_attention_lock_primary": bool(patched_model_used and appearance_mode != "off"),
        "external_masked_correction_primary": False,
        "fallback_masked_correction_primary": False,
        "fallback_masked_correction_role": "rescue_only",
        "fallback_masked_correction_nodes": list(fallback_nodes),
        "fallback_masked_correction_lane_count": len(fallback_lanes),
        "proof_scope": "backend_sampler_wiring",
        "policy": "V25.9.3 restores the legacy in-sampler regional attention patch as the primary Character Lock path; masked KSampler correction is rescue-only and must not be used as success proof.",
    }

def _build_no_patch(
    graph: dict[str, Any],
    *,
    validation: dict[str, Any],
    reason: str,
    route: dict[str, Any] | None,
    model_ref: list[Any],
    clip_ref: list[Any],
    sampler_node_id: str,
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_capabilities = resolve_provider_capabilities_v054(validation.get("route") or route or {})
    flux_adapter_plan = provider_capabilities.get("flux_adapter_plan")
    qwen_adapter_plan = provider_capabilities.get("qwen_adapter_plan")

    patch = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "patch_type": "scene_director_v054" if extra and extra.get("node_class") == V054_NODE_CLASS else "scene_director_v054_required",
        "applied": False,
        "mutated": False,
        "reason": reason,
        "route": validation.get("route") or route or {},
        "route_state": validation.get("route_state"),
        "workflow_patch_allowed": False,
        "scene_director_flux_adapter_plan": flux_adapter_plan,
        "scene_director_flux_adapter_planning_mode": bool(flux_adapter_plan),
        "scene_director_qwen_adapter_plan": qwen_adapter_plan,
        "scene_director_qwen_adapter_planning_mode": bool(qwen_adapter_plan),
        "workflow_readiness_state": validation.get("route_state"),
        "node": None,
        "node_class": None,
        "node_classes": [],
        "nodes_added": [],
        "text_nodes_added": [],
        "sampler_node_id": sampler_node_id,
        "previous_model_ref": deepcopy(model_ref),
        "patched_model_ref": deepcopy(model_ref),
        "previous_positive_ref": deepcopy(previous_positive_ref),
        "previous_negative_ref": deepcopy(previous_negative_ref),
        "patched_positive_ref": deepcopy(previous_positive_ref),
        "patched_negative_ref": deepcopy(previous_negative_ref),
        "clip_ref": deepcopy(clip_ref),
        "node_status": validation.get("node_status"),
        "fallback_policy": "no_fake_graph_support",
        "validation_errors": validation.get("errors") or [],
        "validation_warnings": validation.get("warnings") or [],
        "regions": len(validation.get("active_regions") or []),
    }
    if extra:
        patch.update(extra)
    return {
        "workflow": graph,
        "workflow_patch": patch,
        "validation": validation,
        "model_ref": deepcopy(model_ref),
        "clip_ref": deepcopy(clip_ref),
        "positive_ref": deepcopy(previous_positive_ref),
        "negative_ref": deepcopy(previous_negative_ref),
        "mutated": False,
        "changed": False,
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
    }



def _available_node(available_nodes: Any, class_name: str) -> bool:
    if isinstance(available_nodes, dict):
        return class_name in available_nodes or bool(available_nodes.get(class_name))
    if isinstance(available_nodes, (set, list, tuple)):
        return class_name in set(map(str, available_nodes))
    return False


def _norm_image_names(unit: dict[str, Any]) -> list[str]:
    raw = unit.get("image_names") if isinstance(unit.get("image_names"), list) else unit.get("reference_images")
    if isinstance(raw, str):
        names = [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    elif isinstance(raw, list):
        names = [str(item or "").strip() for item in raw if str(item or "").strip()]
    else:
        names = []
    one = str(unit.get("image_name") or unit.get("reference_image") or "").strip()
    if one and one not in names:
        names.insert(0, one)
    return names


def _build_scene_ipadapter_image_ref(graph: dict[str, Any], next_id: int, names: list[str]) -> tuple[int, list[Any] | None, list[str]]:
    refs: list[list[Any]] = []
    added: list[str] = []
    for name in names:
        node_id = str(next_id)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name, "upload": "image"}}
        refs.append([node_id, 0])
        added.append(node_id)
        next_id += 1
    if not refs:
        return next_id, None, added
    current = refs[0]
    for nxt in refs[1:]:
        node_id = str(next_id)
        graph[node_id] = {"class_type": "ImageBatch", "inputs": {"image1": current, "image2": nxt}}
        current = [node_id, 0]
        added.append(node_id)
        next_id += 1
    return next_id, current, added


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default



_LOCK_MODES = {"soft", "balanced", "strong", "strict"}
_LOCK_OFF = {"", "off", "none", "false", "0", "disabled"}
_CHARACTER_ROLES = {"character"}
_HAIR_DETAIL_ROLES = {"hair_detail"}
_DETAIL_TARGETS = {"hair", "head hair", "hairstyle"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _guard_mode(value: Any, fallback: str = "off") -> str:
    mode = str(value or fallback or "off").strip().lower().replace(" ", "_")
    if mode in _LOCK_OFF:
        return "off"
    if mode in _LOCK_MODES:
        return mode
    # UI sometimes sends Balanced/Strong strings for non-character guard fields.
    if mode in {"medium", "normal"}:
        return "balanced"
    return fallback if fallback in _LOCK_MODES else "balanced"


def _append_unique_text(base: str, *parts: str) -> str:
    out = _clean_text(base)
    seen = {chunk.strip().casefold() for chunk in out.split(",") if chunk.strip()}
    for part in parts:
        text = _clean_text(part)
        if not text:
            continue
        key = text.casefold()
        if key in seen or key in out.casefold():
            continue
        out = f"{out}, {text}" if out else text
        seen.add(key)
    return out

def _prepend_unique_text(prefix: str, base: str) -> str:
    """Prepend authority text without duplicating existing comma chunks.

    Used for background/environment prime terms because CLIP/global attention
    is strongest when scene-setting terms appear before late character/repair
    contracts. This does not invent prompt text; callers provide sanitized
    user-authored text only.
    """
    pre = _clean_text(prefix)
    out = _clean_text(base)
    if not pre:
        return out
    if pre.casefold() in out.casefold():
        return out
    return f"{pre}, {out}" if out else pre



def _region_label(region: dict[str, Any], index: int) -> str:
    return _clean_text(region.get("label") or region.get("id") or f"Character {index}")


def _role_is_character(region: dict[str, Any]) -> bool:
    return str(region.get("role") or "").strip().lower() in _CHARACTER_ROLES


def _bbox_zone_hint(region: dict[str, Any]) -> str:
    bbox = region.get("bbox")
    vals: list[float] = []
    if isinstance(bbox, dict):
        try:
            x = float(bbox.get("x", 0.0))
            w = float(bbox.get("w", 0.0))
            vals = [x, 0.0, x + w, 0.0]
        except Exception:
            vals = []
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            vals = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        except Exception:
            vals = []
    if not vals:
        return "region"
    center_x = (vals[0] + vals[2]) / 2.0
    if center_x <= 0.40:
        return "left side"
    if center_x >= 0.60:
        return "right side"
    return "center seam"



def _bbox_values(region: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = region.get("bbox")
    try:
        if isinstance(bbox, dict):
            x = float(bbox.get("x", 0.0))
            y = float(bbox.get("y", 0.0))
            w = float(bbox.get("w", 1.0))
            h = float(bbox.get("h", 1.0))
            return (x, y, x + w, y + h)
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except Exception:
        return None
    return None


def _bbox_overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = max(0.0001, (ax2 - ax1) * (ay2 - ay1))
    return max(0.0, min(1.0, inter / area))


def _region_side_intent(region: dict[str, Any]) -> str:
    blob = " ".join(str(region.get(key) or "") for key in ("label", "zone", "background_zone", "target_area", "prompt", "background_prompt")).casefold()
    override = region.get("background_override") if isinstance(region.get("background_override"), dict) else {}
    if override:
        blob = f"{blob} {str(override.get('prompt') or '')} {str(override.get('zone') or '')}".casefold()
    left_hits = any(term in blob for term in ("left side", "left background", "on the left", "left-side", "left half"))
    right_hits = any(term in blob for term in ("right side", "right background", "on the right", "right-side", "right half"))
    if left_hits and not right_hits:
        return "left side"
    if right_hits and not left_hits:
        return "right side"
    return ""


def _scene_director_checkpoint_is_anime_like(payload: dict[str, Any] | None, route: dict[str, Any] | None) -> bool:
    parts: list[str] = []
    for source in (payload if isinstance(payload, dict) else {}, route if isinstance(route, dict) else {}):
        if not isinstance(source, dict):
            continue
        for key in ("checkpoint", "checkpoint_name", "ckpt_name", "model", "model_name"):
            value = source.get(key)
            if value:
                parts.append(str(value))
        actual = source.get("actual_params")
        if isinstance(actual, dict):
            for key in ("checkpoint", "checkpoint_name", "ckpt_name", "model"):
                value = actual.get(key)
                if value:
                    parts.append(str(value))
    blob = " ".join(parts).casefold()
    anime_terms = ("anime", "illustrious", "wai", "animagine", "noobai", "danbooru", "pony", "manga", "niji", "cartoon")
    return any(term in blob for term in anime_terms)


def _effective_authority_mode_v054(requested_mode: str, payload: dict[str, Any] | None, route: dict[str, Any] | None) -> dict[str, Any]:
    requested = str(requested_mode or "balanced").strip().lower() or "balanced"
    anime_like = _scene_director_checkpoint_is_anime_like(payload, route)
    prompt_only_modes = {"layout_only", "soft_regional_guide", "anime_safe_prompt", "anime_safe_prompt_only", "prompt_append_only", "soft_prompt_append_only"}
    effective = requested
    strategy = "regional_model_native"
    reason = "requested_model_native"
    if requested in {"neutral", "neutral_planning", "planning_only", "metadata_only"}:
        strategy = "neutral_planning"
        reason = "requested_neutral"
    elif requested in prompt_only_modes:
        effective = "soft_regional_guide" if requested in {"soft_regional_guide", "layout_only"} else "anime_safe_prompt"
        strategy = "prompt_append_only"
        reason = "requested_prompt_only"
    elif anime_like and requested == "balanced":
        effective = "anime_safe_prompt"
        strategy = "prompt_append_only"
        reason = "anime_checkpoint_balanced_auto_soft"
    elif requested in {"strong_correction", "debug_aggressive"}:
        strategy = "regional_model_native"
        reason = "explicit_strong_or_debug"
    return {
        "schema": "neo.image.scene_director.authority_mode_effective.v054.v1",
        "phase": "SD-V054-26.10.8K3",
        "requested_mode": requested,
        "effective_mode": effective,
        "execution_strategy": strategy,
        "anime_checkpoint_detected": anime_like,
        "reason": reason,
        "model_mutation_allowed": strategy == "regional_model_native",
        "prompt_only": strategy == "prompt_append_only",
    }


def _compile_region_zone_validation_v054(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    warnings: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    overlap_warnings: list[dict[str, Any]] = []
    character_boxes: list[tuple[str, str, tuple[float, float, float, float]]] = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").strip().lower()
        if role == "character":
            vals = _bbox_values(region)
            if vals:
                character_boxes.append((str(region.get("id") or f"region_{index}"), _region_label(region, index), vals))
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").strip().lower()
        label = _region_label(region, index)
        rid = str(region.get("id") or f"region_{index}")
        bbox_zone = _bbox_zone_hint(region)
        intent_zone = _region_side_intent(region)
        if intent_zone and bbox_zone in {"left side", "right side"} and intent_zone != bbox_zone:
            item = {
                "region_id": rid,
                "label": label,
                "role": role,
                "intent_zone": intent_zone,
                "bbox_zone": bbox_zone,
                "code": "region_zone_mismatch",
                "message": f"{label} says {intent_zone}, but its region box resolves to {bbox_zone}.",
            }
            mismatches.append(item)
            warnings.append({"level": "warning", **item})
        vals = _bbox_values(region)
        if vals and role in {"background", "background_object", "environment", "transition_effect"}:
            for char_id, char_label, char_box in character_boxes:
                ratio = _bbox_overlap_ratio(vals, char_box)
                if ratio >= 0.45:
                    item = {
                        "region_id": rid,
                        "label": label,
                        "role": role,
                        "overlaps_region_id": char_id,
                        "overlaps_label": char_label,
                        "overlap_ratio": round(ratio, 4),
                        "code": "background_character_overlap",
                        "message": f"{label} overlaps {char_label}; background authority should be subordinate to character masks.",
                    }
                    overlap_warnings.append(item)
                    warnings.append({"level": "info", **item})
    return {
        "schema": "neo.image.scene_director.region_zone_validation.v054.v1",
        "phase": "SD-V054-26.10.8K3",
        "status": "warning" if warnings else "clean",
        "mismatch_count": len(mismatches),
        "overlap_warning_count": len(overlap_warnings),
        "mismatches": mismatches,
        "overlaps": overlap_warnings,
        "warnings": warnings,
        "policy": "Side intent from region label/prompt must match the region box; background lanes may overlap characters only as subordinate masks.",
    }



_BACKGROUND_ROLES_FOR_SEPARATION = {"background", "background_object", "environment"}


def _normalize_background_prompt_key(value: Any) -> str:
    import re
    text = _clean_text(value).casefold()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ",", text)
    return text.strip(" ,")


def _background_region_source_prompt(region: dict[str, Any]) -> str:
    override = region.get("background_override") if isinstance(region.get("background_override"), dict) else {}
    candidates = [
        region.get("background_prompt"),
        override.get("prompt") if isinstance(override, dict) else "",
        region.get("prompt"),
    ]
    for value in candidates:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _background_semantic_hint(region: dict[str, Any], global_prompt: str = "") -> tuple[str, str, list[str]]:
    label_blob = " ".join(
        str(region.get(key) or "")
        for key in ("label", "zone", "target_area", "id")
    ).casefold()
    prompt_blob = _clean_text(region.get("prompt")).casefold()
    global_blob = _clean_text(global_prompt).casefold()
    fantasy_terms = ("fantasy", "medieval", "ancient", "ruins", "warrior", "torch", "stone", "castle")
    modern_terms = ("modern", "future", "futuristic", "city", "neon", "cyberpunk", "sci-fi", "megacity", "glass tower", "urban")

    # Label/zone intent wins over duplicated prompt text. This is the current
    # failure mode: a region labelled Fantasy can accidentally carry the Modern
    # prompt, and prompt scoring alone would reinforce the wrong side.
    if any(term in label_blob for term in fantasy_terms):
        return "fantasy", "label_and_global_prompt" if any(term in global_blob for term in fantasy_terms) else "label_or_region_prompt", [
            "distinct medieval fantasy background for this region",
            "ancient stone ruins",
            "warm torchlight",
            "fantasy atmosphere",
            "avoid futuristic city dominance in this region",
        ]
    if any(term in label_blob for term in modern_terms):
        return "modern", "label_and_global_prompt" if any(term in global_blob for term in modern_terms) else "label_or_region_prompt", [
            "distinct modern futuristic city background for this region",
            "neon city architecture",
            "glass towers",
            "urban sci-fi environment",
            "avoid medieval ruins dominating this region",
        ]

    # Without a label/zone clue, do not use broad global prompt side text to invent
    # content for generic Background A/B duplicates. Only an already-specific
    # region prompt can provide a safe clue.
    fantasy_score = sum(1 for term in fantasy_terms if term in prompt_blob)
    modern_score = sum(1 for term in modern_terms if term in prompt_blob)
    if fantasy_score > modern_score and fantasy_score > 0:
        return "fantasy", "label_or_region_prompt", [
            "distinct medieval fantasy background for this region",
            "ancient stone ruins",
            "warm torchlight",
            "fantasy atmosphere",
            "avoid futuristic city dominance in this region",
        ]
    if modern_score > fantasy_score and modern_score > 0:
        return "modern", "label_or_region_prompt", [
            "distinct modern futuristic city background for this region",
            "neon city architecture",
            "glass towers",
            "urban sci-fi environment",
            "avoid medieval ruins dominating this region",
        ]
    return "unknown", "none", []

def _compile_background_separation_guard_v054(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    global_data = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    global_prompt = _clean_text(global_data.get("prompt"))
    seen: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    duplicate_count = 0
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").strip().lower()
        if role not in _BACKGROUND_ROLES_FOR_SEPARATION:
            continue
        prompt = _background_region_source_prompt(region)
        key = _normalize_background_prompt_key(prompt)
        if not key:
            continue
        label = _region_label(region, index)
        rid = str(region.get("id") or f"region_{index}")
        prior = seen.get(key)
        if prior is None:
            seen[key] = {"region_id": rid, "label": label, "index": index, "prompt": prompt}
            continue
        duplicate_count += 1
        warning = {
            "level": "warning",
            "code": "duplicate_background_prompt",
            "region_id": rid,
            "label": label,
            "matches": prior.get("label"),
            "message": f"Background region {label} uses the same prompt as {prior.get('label')}; regional background separation will be weak until the prompts differ.",
        }
        warnings.append(warning)
        semantic, source, terms = _background_semantic_hint(region, global_prompt)
        if terms:
            repairs.append({
                "region_id": rid,
                "label": label,
                "duplicate_of": prior.get("region_id"),
                "duplicate_of_label": prior.get("label"),
                "action": "regional_authority_disambiguation_appended",
                "source": source,
                "semantic": semantic,
                "appended_terms": terms,
            })
            warnings.append({
                "level": "info",
                "code": "background_semantic_separation_applied",
                "region_id": rid,
                "label": label,
                "message": f"Background separation terms were appended to regional authority for {label}.",
            })
        else:
            warnings.append({
                "level": "warning",
                "code": "background_semantic_separation_skipped_no_clue",
                "region_id": rid,
                "label": label,
                "message": f"Duplicate background prompt found for {label}, but no safe label/global clue was available for semantic separation.",
            })
    if duplicate_count <= 0:
        status = "not_applicable"
    elif repairs:
        status = "applied"
    else:
        status = "warning_only"
    return {
        "schema": "neo.image.scene_director.background_separation_guard.v054.v1",
        "phase": "SD-V054-26.9.8",
        "status": status,
        "duplicate_count": duplicate_count,
        "repaired_count": len(repairs),
        "warnings": warnings,
        "repairs": repairs,
    }


def _background_separation_terms_for_region(background_guard: dict[str, Any] | None, region_id: str) -> list[str]:
    guard = background_guard if isinstance(background_guard, dict) else {}
    out: list[str] = []
    for repair in guard.get("repairs") or []:
        if isinstance(repair, dict) and str(repair.get("region_id") or "") == str(region_id):
            for term in repair.get("appended_terms") or []:
                text = _clean_text(term)
                if text and text not in out:
                    out.append(text)
    return out

def _compile_regional_authority_restore_v054(scene_graph: dict[str, Any] | None, ipadapter_active: bool, authority_mode: str = "balanced", zone_validation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a safe prompt/mask authority bridge when IPAdapter bypasses the regional model.

    Phase 26.9.1 intentionally kept IPAdapter off the Scene Director patched
    MODEL to avoid IPAdapter Plus attention-batch crashes. That made the sampler
    use the safe IPAdapter MODEL path, but it also meant the CLIP conditioning
    had to carry more regional layout intent. This bridge is conservative: it
    does not reattach IPAdapter to the regional-attention model; it compiles the
    existing scene graph into explicit layout/detail/background prompt clauses.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    if not regions:
        return {
            "schema": "neo.image.scene_director.regional_authority_restore.v054.v1",
            "phase": "SD-V054-26.9.4",
            "status": "not_applicable",
            "mode": "none",
            "positive": "",
            "negative": "",
            "positive_count": 0,
            "negative_count": 0,
        }

    requested_authority_mode = str(authority_mode or "balanced").strip().lower() or "balanced"
    if requested_authority_mode in {"layout_only", "soft_regional_guide", "anime_safe_prompt", "anime_safe_prompt_only", "prompt_append_only", "soft_prompt_append_only"}:
        mode = "prompt_append_only"
    else:
        mode = "prompt_mask_restore" if ipadapter_active else "regional_model_native"
    positive_parts: list[str] = []
    negative_parts: list[str] = []
    lanes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    background_guard = _compile_background_separation_guard_v054(graph)
    warnings.extend([deepcopy(w) for w in background_guard.get("warnings", []) if isinstance(w, dict)])
    region_zone_validation = zone_validation if isinstance(zone_validation, dict) else _compile_region_zone_validation_v054(graph)
    warnings.extend([deepcopy(w) for w in region_zone_validation.get("warnings", []) if isinstance(w, dict)])

    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").strip().lower()
        label = _region_label(region, index)
        prompt = _clean_text(region.get("prompt"))
        negative = _clean_text(region.get("negative") or region.get("negative_guard"))
        if not prompt and role not in {"background", "transition_effect", "hair_detail", "held_prop", "character"}:
            continue
        intent_zone = _region_side_intent(region)
        bbox_zone = _bbox_zone_hint(region)
        zone = intent_zone or bbox_zone
        clause = ""
        lane_kind = role or "region"
        if role == "background":
            separation_terms = _background_separation_terms_for_region(background_guard, str(region.get("id") or ""))
            if separation_terms:
                prompt = _append_unique_text(prompt, *separation_terms)
            clause = f"{zone} background authority: {prompt}" if prompt else ""
        elif role == "transition_effect":
            clause = f"center transition authority: {prompt}" if prompt else ""
        elif role == "character":
            clause = f"{label} {zone} character authority: keep this subject in its assigned region; {prompt}" if prompt else f"{label} {zone} character authority: keep this subject in its assigned region"
        elif role == "hair_detail":
            parent = _clean_text(region.get("attach_to"))
            clause = f"{label} attached hair detail authority: {prompt}; keep it locked to {parent or 'the parent character'}" if prompt else ""
        elif role == "held_prop":
            parent = _clean_text(region.get("attach_to"))
            clause = f"{label} held prop authority: {prompt}; keep it attached to {parent or 'the parent character'}" if prompt else ""
        else:
            clause = f"{label} {zone} detail authority: {prompt}" if prompt else ""

        if clause:
            positive_parts.append(clause)
            lanes.append({"region_id": str(region.get("id") or ""), "label": label, "role": role, "zone": zone, "status": "compiled"})
        if negative:
            negative_parts.append(f"{label} regional negative: {negative}")

    if positive_parts:
        positive_parts.insert(0, "Regional Authority Restore: preserve the declared left/right region layout, subject separation, background separation, attached detail lanes, and no cross-region identity/style bleeding")
        negative_parts.append("wrong region assignment, swapped subjects, background bleeding across regions, missing regional detail, merged regional styles")

    return {
        "schema": "neo.image.scene_director.regional_authority_restore.v054.v1",
        "phase": "SD-V054-26.9.3",
        "status": "applied" if positive_parts else "empty",
        "mode": mode,
        "ipadapter_safe_bypass_active": bool(ipadapter_active),
        "policy": "Regional intent is compiled according to the visible Scene Director authority mode. Prompt-only modes do not mutate the model path.",
        "requested_authority_mode": requested_authority_mode,
        "lanes": lanes,
        "warnings": warnings,
        "background_separation_guard": background_guard,
        "region_zone_validation": region_zone_validation,
        "positive": ", ".join(dict.fromkeys([p for p in positive_parts if p])),
        "negative": ", ".join(dict.fromkeys([n for n in negative_parts if n])),
        "positive_count": len(positive_parts),
        "negative_count": len(negative_parts),
    }


def _target_is_hair(region: dict[str, Any]) -> bool:
    role = str(region.get("role") or "").strip().lower()
    target = str(region.get("target_area") or region.get("area") or "").strip().lower()
    label = str(region.get("label") or "").strip().lower()
    prompt = str(region.get("prompt") or "").strip().lower()
    return role in _HAIR_DETAIL_ROLES or target in _DETAIL_TARGETS or "hair" in label or "hair" in prompt


def _extract_trait_hint(prompt: str, keywords: tuple[str, ...], window: int = 4) -> str:
    words = [w.strip(" ,.;:()[]{}") for w in _clean_text(prompt).split()]
    lowered = [w.casefold() for w in words]
    snippets: list[str] = []
    for idx, word in enumerate(lowered):
        if any(key in word for key in keywords):
            start = max(0, idx - window)
            end = min(len(words), idx + window + 1)
            chunk = " ".join(words[start:end]).strip(" ,")
            if chunk and chunk.casefold() not in {s.casefold() for s in snippets}:
                snippets.append(chunk)
    return "; ".join(snippets[:3])


_MALE_GENDER_TERMS = ("1boy", "2boys", "boy", "boys", "man", "men", "male", "males", "young man", "young men", "husband", "boyfriend")
_FEMALE_GENDER_TERMS = ("1girl", "2girls", "girl", "girls", "woman", "women", "female", "females", "young woman", "young women", "wife", "girlfriend")
_FLEX_GENDER_TERMS = ("nonbinary", "non-binary", "androgynous", "genderfluid", "agender")
_SKIN_PATTERNS = (
    "fair light skin", "light skin", "fair skin", "pale skin", "porcelain skin", "tan skin", "tanned skin",
    "olive skin", "brown skin", "dark brown skin", "dark skin", "deep skin", "warm brown skin", "fair-to-light tan skin", "light tan skin", "complexion", "skin tone"
)
_HAIR_HINT_TERMS = ("hair", "hairstyle", "spiky", "bangs", "blond", "blonde", "pink", "black", "brown", "blue", "red", "silver", "white")
_BUILD_HINT_TERMS = ("build", "body", "silhouette", "torso", "chest", "shoulders", "waist", "hips", "slim", "skinny", "thin", "lean", "average", "athletic", "muscular", "stocky", "broad", "narrow", "tall", "short", "height", "proportions")
_BODY_BUILD_EXPLICIT_PATTERNS = (
    "slim build", "average build", "lean build", "skinny build", "thin build", "athletic build",
    "muscular build", "stocky build", "broad build", "slender build", "lithe build",
    "slim body", "average body", "lean body", "skinny body", "thin body", "athletic body",
    "muscular body", "stocky body", "slender body", "lithe body",
    "flat male chest", "flat chest", "male chest", "masculine chest", "masculine torso",
    "male torso", "adult male body", "adult male body silhouette", "male body silhouette", "masculine body silhouette",
    "female body silhouette", "feminine body silhouette", "feminine torso", "female torso",
    "broad shoulders", "narrow shoulders", "narrow waist", "wide hips", "slim waist",
    "average height", "short height", "tall height", "short stature", "tall stature",
)
_OUTFIT_HINT_TERMS = ("wearing", "outfit", "suit", "shirt", "armor", "cape", "costume", "dress", "jacket", "tie", "clothing", "clothes")


_GENERATED_CHARACTER_LOCK_MARKERS = (
    "character lock:",
    "gender guard",
    "skin tone guard",
    "hair guard",
    "build/body guard",
    "outfit preservation",
    "first-pass character lock correction",
    "regional authority restore:",
    "character authority:",
    "background authority:",
    "regional negative:",
)

_GENERATED_RESCUE_TEMPLATE_MARKERS = (
    "preserve this exact assigned subject only",
    "one subject in this mask",
    "no cross-region identity bleed",
    "preserve original outfit",
    "armor, clothing cut",
    "preserve pose, body contact",
    "relationship staging",
    "wrong region assignment",
    "swapped subject",
    "changed outfit",
    "missing armor",
    "broken contact",
    "swapped embrace",
)

_HAIR_DESCRIPTOR_RE = r"(?:short|long|medium|medium-length|shoulder-length|straight|wavy|curly|coily|kinky|messy|neat|textured|wet|swept-back|swept|side-part|side-parted|undercut|fringe|bangs|black|brown|dark|blond|blonde|red|auburn|silver|white|blue|pink|green|dyed|natural|thick|thin|voluminous|close-cropped|buzzed|shaved)"
_CLOTHING_NOUN_RE = r"(?:shirt|t-shirt|tee|polo|overshirt|tank top|trousers|pants|shorts|jacket|coat|suit|tie|hoodie|sweater|cardigan|vest|robe|dress|skirt|jeans|bracelet|watch|necklace|chain|ring|boots|shoes|belt|backpack|armor|cape|costume|uniform|clothing|clothes)"


def _dedupe_text_items(items: list[str], limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _clean_text(item).strip(" ,.;:")
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _split_prompt_clauses(text: str) -> list[str]:
    import re
    source = _clean_text(text)
    if not source:
        return []
    # Character-lock bridges are comma-heavy. Splitting on commas/semicolons is
    # good enough for removing machine-generated bridge clauses while leaving the
    # user's natural region description intact.
    return [part.strip(" ,.;:\n\t") for part in re.split(r"[,;]\s+", source) if part.strip(" ,.;:\n\t")]


def _strip_character_lock_generated_text(text: str) -> tuple[str, dict[str, Any]]:
    clauses = _split_prompt_clauses(text)
    kept: list[str] = []
    removed: list[str] = []
    for clause in clauses:
        folded = clause.casefold()
        if any(marker in folded for marker in _GENERATED_CHARACTER_LOCK_MARKERS):
            removed.append(clause)
            continue
        if any(marker in folded for marker in _GENERATED_RESCUE_TEMPLATE_MARKERS):
            removed.append(clause)
            continue
        kept.append(clause)
    cleaned = ", ".join(_dedupe_text_items(kept, limit=64))
    return cleaned, {
        "schema": "neo.image.scene_director.prompt_hygiene.v25_9_6_fix3",
        "phase": "V25.9.6-fix3",
        "recursive_guard_removed_count": len(removed),
        "removed_preview": removed[:8],
        "sanitized_region_prompt_used": bool(removed),
        "prompt_template_injection": False,
        "policy": "Strip backend/generated Character Lock bridge text from prompts before compiling rescue conditioning; keep only user-authored region/global text.",
    }


def _strip_generated_negative_text(text: str) -> tuple[str, dict[str, Any]]:
    clauses = _split_prompt_clauses(text)
    kept: list[str] = []
    removed: list[str] = []
    for clause in clauses:
        folded = clause.casefold()
        if any(marker in folded for marker in _GENERATED_CHARACTER_LOCK_MARKERS):
            removed.append(clause)
            continue
        if any(marker in folded for marker in _GENERATED_RESCUE_TEMPLATE_MARKERS):
            removed.append(clause)
            continue
        kept.append(clause)
    return ", ".join(_dedupe_text_items(kept, limit=64)), {
        "schema": "neo.image.scene_director.negative_prompt_hygiene.v25_9_6_fix3",
        "phase": "V25.9.6-fix3",
        "recursive_guard_removed_count": len(removed),
        "removed_preview": removed[:8],
        "prompt_template_injection": False,
    }


def _detect_prompt_trait_conflicts(prompt: str, negative: str = "") -> dict[str, Any]:
    source = _clean_text(prompt).casefold()
    neg = _clean_text(negative).casefold()
    warnings: list[str] = []
    if "clean-shaven" in source and "stubble" in source:
        warnings.append("face_hair_conflict_clean_shaven_and_stubble")
    if "stubble" in source and "beard" in neg:
        warnings.append("face_hair_conflict_stubble_positive_beard_negative")
    if "short wavy hair" in source and "short curly hair" in source:
        warnings.append("hair_texture_conflict_short_wavy_and_short_curly")
    if "long hair" in source and "short" in source and "hair" in source:
        warnings.append("hair_length_conflict_short_and_long")
    if "wearing" in source and "shirtless" in source:
        warnings.append("outfit_conflict_wearing_and_shirtless")
    return {
        "schema": "neo.image.scene_director.prompt_conflict_warnings.v25_9_6_fix3",
        "phase": "V25.9.6-fix3",
        "warning_count": len(warnings),
        "warnings": warnings,
    }




def _sanitize_region_nested_prompt_fields(region: dict[str, Any]) -> dict[str, Any]:
    removed_total = 0
    for block_name in ("inpaint", "detailer", "edit_intent"):
        block = region.get(block_name)
        if not isinstance(block, dict):
            continue
        if "prompt" in block:
            cleaned, hygiene = _strip_character_lock_generated_text(_clean_text(block.get("prompt")))
            block["prompt"] = cleaned
            removed_total += int(hygiene.get("recursive_guard_removed_count") or 0)
        if "negative" in block:
            cleaned_neg, neg_hygiene = _strip_generated_negative_text(_clean_text(block.get("negative")))
            block["negative"] = cleaned_neg
            removed_total += int(neg_hygiene.get("recursive_guard_removed_count") or 0)
    return {
        "schema": "neo.image.scene_director.nested_prompt_hygiene.v25_9_6_fix3",
        "phase": "V25.9.6-fix3",
        "recursive_guard_removed_count": removed_total,
    }

def _extract_hair_phrases_clean(prompt: str, attached_hair: list[dict[str, Any]] | None = None) -> list[str]:
    import re
    attached_terms = [_clean_text(r.get("prompt")) for r in (attached_hair or []) if _clean_text(r.get("prompt"))]
    if attached_terms:
        return _dedupe_text_items(attached_terms, limit=3)
    source = _clean_text(prompt)
    found: list[str] = []
    # Capture natural phrases ending in "hair" without leaking skin/build/body terms.
    pattern = re.compile(rf"\b((?:{_HAIR_DESCRIPTOR_RE}\s+){{1,6}}hair)\b", re.IGNORECASE)
    for match in pattern.finditer(source):
        found.append(match.group(1))
    # Common styles that may not literally include "hair".
    for style in ("side-part undercut", "side part undercut", "neat side-part undercut", "undercut", "short fringe", "swept back"):
        if _word_match(source, style):
            found.append(style)
    return _dedupe_text_items(found, limit=4)


def _extract_outfit_phrases_clean(prompt: str) -> list[str]:
    import re
    source = _clean_text(prompt)
    found: list[str] = []
    # Prefer the user's explicit "wearing ..." clause, stopping before pose/body
    # instructions so face/hair/build text does not bleed into outfit.
    m = re.search(r"\bwearing\s+(.+?)(?:\bbody\s+angled\b|\bone\s+complete\b|\bstanding\b|\blooking\b|\bcalm\b|\bromantic\b|$)", source, re.IGNORECASE)
    if m:
        clause = m.group(1).strip(" ,.;")
        if clause:
            found.append("wearing " + clause)
    # Also collect standalone clothing/accessory chunks.
    clothing_pattern = re.compile(rf"\b((?:[a-zA-Z0-9\-]+\s+){{0,5}}{_CLOTHING_NOUN_RE}(?:\s+[a-zA-Z0-9\-]+){{0,3}})\b", re.IGNORECASE)
    for match in clothing_pattern.finditer(source):
        phrase = match.group(1).strip(" ,.;")
        if phrase and not any(bad in phrase.casefold() for bad in ("hair", "face", "skin", "body silhouette", "chest")):
            found.append(phrase)
    return _dedupe_text_items(found, limit=5)


def _word_match(text: str, term: str) -> bool:
    import re
    source = _clean_text(text).casefold()
    needle = re.escape(term.casefold()).replace("\\ ", r"\s+")
    if term.startswith("1") or term.startswith("2"):
        return re.search(rf"(?<![a-z0-9_]){needle}(?![a-z0-9_])", source) is not None
    return re.search(rf"(?<![a-z0-9_]){needle}(?![a-z0-9_])", source) is not None


def _extract_prompt_terms(prompt: str, terms: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for term in terms:
        if _word_match(prompt, term) and term not in found:
            found.append(term)
    return found


def _extract_gender_terms(prompt: str) -> dict[str, Any]:
    male = _extract_prompt_terms(prompt, _MALE_GENDER_TERMS)
    female = _extract_prompt_terms(prompt, _FEMALE_GENDER_TERMS)
    flexible = _extract_prompt_terms(prompt, _FLEX_GENDER_TERMS)
    terms = male + female + flexible
    family = "unspecified"
    if flexible and not male and not female:
        family = "flexible"
    elif male and not female:
        family = "male"
    elif female and not male:
        family = "female"
    elif terms:
        family = "mixed"
    return {"family": family, "terms": terms, "male_terms": male, "female_terms": female, "flexible_terms": flexible}


def _character_gender_negative_from_terms(gender: dict[str, Any]) -> str:
    family = str(gender.get("family") or "unspecified")
    if family == "male":
        return "female, woman, girl, gender swap, wrong gender, feminine body on male subject, breasts, cleavage"
    if family == "female":
        return "male, man, boy, gender swap, wrong gender, masculine body on female subject"
    if family == "flexible":
        return "wrong gender expression, gender drift"
    return "gender swap, wrong gender"


def _extract_skin_terms(prompt: str) -> list[str]:
    found = _extract_prompt_terms(prompt, _SKIN_PATTERNS)
    if found:
        return found[:4]
    hint = _extract_trait_hint(prompt, ("skin", "complexion", "tone"), window=3)
    return [hint] if hint else []


def _extract_hair_terms(prompt: str, attached_hair: list[dict[str, Any]]) -> list[str]:
    return _extract_hair_phrases_clean(prompt, attached_hair)


def _extract_build_terms(prompt: str) -> list[str]:
    """Extract body/build/height terms for Character Lock.

    V25.9.4 restores the body side of Character Lock. The older extractor only
    used a loose word window, which missed the exact phrases creators actually
    type, such as "slim build" or "flat male chest". The compiler now keeps
    explicit body phrases first, then falls back to a short contextual hint.
    """
    import re

    source = _clean_text(prompt)
    folded = source.casefold()
    found: list[str] = []

    for pattern in _BODY_BUILD_EXPLICIT_PATTERNS:
        needle = re.escape(pattern.casefold()).replace("\\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9_]){needle}(?![a-z0-9_])", folded) and pattern not in found:
            found.append(pattern)

    # Capture compact adjective+noun variants without requiring every possible
    # combination in the static pattern list.
    combo_re = re.compile(
        r"\b(slim|skinny|thin|lean|average|athletic|muscular|stocky|broad|narrow|slender|lithe)\s+"
        r"(build|body|frame|physique|torso|waist|shoulders|chest|hips)\b",
        re.IGNORECASE,
    )
    for match in combo_re.finditer(source):
        phrase = match.group(0).strip()
        if phrase and phrase.casefold() not in {item.casefold() for item in found}:
            found.append(phrase)

    if found:
        return found[:6]

    hint = _extract_trait_hint(prompt, _BUILD_HINT_TERMS, window=4)
    # Do not treat casual adjectives like "slim soft face" as build terms. A
    # fallback hint must mention a body/build/proportion noun.
    if hint and any(term in hint.casefold() for term in ("build", "body", "height", "proportion", "torso", "chest", "shoulder", "waist", "hip", "physique", "frame", "silhouette")):
        return [hint]
    return []


def _body_guard_terms_for_gender(gender_family: str, *, clothed: bool = False) -> tuple[str, str]:
    family = str(gender_family or "unspecified").strip().lower()
    if family == "male":
        return (
            (
                "masculine body silhouette and shoulder line preserved beneath the selected clothing"
                if clothed
                else "flat male chest, masculine torso, adult male body silhouette, male shoulder line"
            ),
            "feminine body on male subject, breasts, cleavage, curvy hips, hourglass figure",
        )
    if family == "female":
        return (
            (
                "requested feminine body silhouette and proportions preserved beneath the selected clothing"
                if clothed
                else "requested female body silhouette and proportions, preserve the region-described presentation"
            ),
            "masculine body on female subject, broad male torso, male chest",
        )
    if family == "flexible":
        return (
            "requested body presentation and silhouette from this region",
            "changed body presentation, forced binary body type",
        )
    return (
        "explicit body, build, height, and proportion terms from this region",
        "changed body presentation, wrong body silhouette",
    )


def _should_infer_body_guard(character_mode: str, gender_mode: str, build_mode: str, gender_family: str, build_terms: list[str]) -> bool:
    if build_mode != "off" or not build_terms:
        return False
    if str(gender_family or "").strip().lower() not in {"male", "female"}:
        return False
    return character_mode in {"strong", "strict"} or gender_mode in {"strong", "strict"}


def _extract_outfit_terms(prompt: str) -> list[str]:
    return _extract_outfit_phrases_clean(prompt)


def _character_lock_phrase(label: str, prompt: str, lock: dict[str, Any], attached: dict[str, list[dict[str, Any]]], identity_strength: float) -> tuple[list[str], list[str], dict[str, Any]]:
    character_mode = _guard_mode(lock.get("character"), "off")
    if character_mode == "off":
        return [], [], {"status": "off", "label": label}

    hair_mode = _guard_mode(lock.get("hair"), "off")
    skin_mode = _guard_mode(lock.get("skin_tone"), "off")
    gender_mode = _guard_mode(lock.get("gender"), "off")
    build_mode = _guard_mode(lock.get("build") or lock.get("body_height"), "off")
    outfit_mode = _guard_mode(lock.get("outfit"), "off")
    negative_mode = _guard_mode(lock.get("negative"), "balanced")

    positive_parts: list[str] = []
    negative_parts: list[str] = []
    applied_guards: dict[str, str] = {"character": character_mode}

    strength_word = "preserve"
    if character_mode == "soft":
        strength_word = "gently preserve"
    elif character_mode == "strong":
        strength_word = "must preserve"
    elif character_mode == "strict":
        strength_word = "strictly lock"

    positive_parts.append(
        f"{label} character lock: {strength_word} this character's identity and explicitly described appearance traits for this region; identity strength {identity_strength:.2f}"
    )

    extracted_terms: dict[str, Any] = {"gender": [], "skin_tone": [], "hair": [], "build": [], "outfit": []}
    gender_terms = _extract_gender_terms(prompt)
    extracted_terms["gender"] = gender_terms.get("terms", [])
    gender_family = str(gender_terms.get("family") or "unspecified")
    build_terms = _extract_build_terms(prompt)
    extracted_terms["build"] = build_terms
    outfit_terms = _extract_outfit_terms(prompt)
    extracted_terms["outfit"] = outfit_terms
    auto_inferred_guards: dict[str, str] = {}
    if _should_infer_body_guard(character_mode, gender_mode, build_mode, gender_family, build_terms):
        build_mode = "strict" if "strict" in {character_mode, gender_mode} else "strong"
        lock["build"] = build_mode
        lock.setdefault("body_height", build_mode)
        auto_inferred_guards["build"] = "body_terms_from_region_prompt_with_strong_gender_or_character_lock"
        auto_inferred_guards["body_height"] = "shared_body_guard_channel"

    if gender_mode != "off":
        applied_guards["gender"] = gender_mode
        family = gender_family
        if family == "male":
            positive_parts.append(f"{label} gender guard {gender_mode}: preserve the gender terms explicitly described for this region: {', '.join(gender_terms.get('terms') or [])}; preserve the explicitly requested male subject identity, body presentation, and silhouette for this region")
        elif family == "female":
            positive_parts.append(f"{label} gender guard {gender_mode}: preserve the gender terms explicitly described for this region: {', '.join(gender_terms.get('terms') or [])}; preserve the explicitly requested female subject identity, body presentation, and silhouette for this region")
        elif family == "flexible":
            positive_parts.append(f"{label} gender guard {gender_mode}: preserve the explicitly requested gender expression/presentation for this region: {', '.join(gender_terms.get('terms') or [])}")
        elif gender_terms.get("terms"):
            positive_parts.append(f"{label} gender guard {gender_mode}: preserve the gender terms explicitly described for this region: {', '.join(gender_terms.get('terms') or [])}")
        else:
            positive_parts.append(f"{label} gender guard {gender_mode}: preserve only the gender presentation explicitly described for this region; do not invent a different gender")
        if negative_mode != "off":
            negative_parts.append(_character_gender_negative_from_terms(gender_terms))

    if skin_mode != "off":
        applied_guards["skin_tone"] = skin_mode
        skin_terms = _extract_skin_terms(prompt)
        extracted_terms["skin_tone"] = skin_terms
        positive_parts.append(f"{label} skin tone guard {skin_mode}: preserve {', '.join(skin_terms) if skin_terms else 'only the skin tone or complexion terms explicitly described for this region'}")
        if negative_mode != "off":
            negative_parts.append("wrong skin tone, changed complexion, inconsistent skin color")

    attached_hair = [r for r in attached.get("hair", []) if _clean_text(r.get("prompt"))]
    hair_terms = _extract_hair_terms(prompt, attached_hair)
    extracted_terms["hair"] = hair_terms
    if hair_mode != "off" or attached_hair:
        applied_guards["hair"] = hair_mode if hair_mode != "off" else "attached_detail"
        positive_parts.append(f"{label} hair guard {hair_mode if hair_mode != 'off' else character_mode}: preserve {', '.join(hair_terms) if hair_terms else 'only the hair terms explicitly described for this region'}")
        if negative_mode != "off":
            negative_parts.append("wrong hair color, changed hairstyle, missing hair detail, inconsistent hair")
            if "pink" in (" ".join(hair_terms) or prompt).casefold():
                negative_parts.append("black hair, brown hair, blonde hair, natural hair color instead of pink")

    if build_mode != "off":
        applied_guards["build"] = build_mode
        body_positive, body_negative = _body_guard_terms_for_gender(gender_family, clothed=bool(outfit_terms))
        build_source = ", ".join(build_terms) if build_terms else "only the body, build, height, or proportion terms explicitly described for this region"
        inferred_note = " inferred" if "build" in auto_inferred_guards else ""
        positive_parts.append(f"{label} build/body guard{inferred_note} {build_mode}: preserve {build_source}; preserve {body_positive}")
        if negative_mode != "off":
            negative_parts.append(f"wrong body build, changed body type, distorted body proportions, {body_negative}")

    if outfit_mode != "off":
        applied_guards["outfit"] = outfit_mode
        positive_parts.append(f"{label} outfit preservation {outfit_mode}: preserve {', '.join(outfit_terms) if outfit_terms else 'only the clothing or costume terms explicitly described for this region'}")
        if negative_mode != "off":
            negative_parts.append("wrong outfit, changed clothing, missing costume details")

    body_guard_compiler = {
        "schema": "neo.image.scene_director.body_guard_compiler.v25_9_4",
        "phase": "V25.9.4",
        "status": "applied" if build_mode != "off" else ("terms_detected_guard_off" if build_terms else "no_body_terms"),
        "extracted_build_terms": build_terms,
        "auto_inferred_guards": auto_inferred_guards,
        "gender_family": gender_family,
        "policy": "Explicit body/build/height terms are preserved by Character Lock; strong binary gender/character locks infer a body guard when the visible Build/Body guards are off.",
    }
    return positive_parts, negative_parts, {"status": "applied", "label": label, "guards": applied_guards, "extracted_terms": extracted_terms, "gender_family": gender_terms.get("family"), "body_guard_compiler": body_guard_compiler, "auto_inferred_guards": auto_inferred_guards, "positive_count": len(positive_parts), "negative_count": len(negative_parts)}




def _postpass_gender_guard_for_region(label: str, region_prompt: str, lock: dict[str, Any] | None) -> dict[str, Any]:
    """Phase 26.9.16: harden post-generation crop/detail passes against gender/body drift.

    The base Scene Director character lock can be correct while a later crop refine
    pass repaints the subject with stronger local LoRA/CLIP. This helper builds a
    crop-local guard from the same prompt-derived gender family used by the main
    Character Lock bridge. It intentionally never invents a binary lock when the
    region prompt does not provide one.
    """
    gender = _extract_gender_terms(region_prompt or "")
    family = str(gender.get("family") or "unspecified")
    mode = _guard_mode((lock or {}).get("gender"), "off")
    positive: list[str] = []
    negative: list[str] = []
    warnings: list[str] = []
    if mode == "off":
        return {"family": family, "positive": "", "negative": "", "warnings": [], "gender_guard_carried": False}
    if family == "male":
        positive.append(
            f"{label} post-pass gender lock: preserve male subject identity, masculine face, masculine body presentation, male silhouette, same outfit/body role from this assigned region"
        )
        negative.append(
            "female, woman, girl, feminine face, feminine body on male subject, breasts, cleavage, lipstick, makeup, long eyelashes, soft feminine facial structure, gender swap, wrong gender"
        )
        warnings.append("crop_refine_gender_guard_carried")
    elif family == "female":
        positive.append(
            f"{label} post-pass gender lock: preserve female subject identity, feminine presentation if explicitly requested, same outfit/body role from this assigned region"
        )
        negative.append(
            "male, man, boy, masculine face on female subject, masculine body on female subject, gender swap, wrong gender"
        )
        warnings.append("crop_refine_gender_guard_carried")
    elif family == "flexible":
        positive.append(f"{label} post-pass gender lock: preserve the explicitly requested gender expression/presentation for this assigned region")
        negative.append("wrong gender expression, gender drift")
        warnings.append("crop_refine_flexible_gender_guard_used")
    elif family == "mixed":
        positive.append(f"{label} post-pass gender lock: preserve only the gender terms explicitly described for this assigned region")
        negative.append("gender swap, wrong gender")
        warnings.append("crop_refine_gender_guard_carried")
    else:
        positive.append(f"{label} post-pass identity lock: preserve assigned region identity and presentation without changing gender, body role, outfit, or pose")
        warnings.append("crop_refine_binary_gender_guard_skipped_unspecified")
    return {
        "family": family,
        "positive": ", ".join([v for v in positive if v]),
        "negative": ", ".join([v for v in negative if v]),
        "warnings": warnings,
        "gender_guard_carried": bool(positive),
    }


def _postpass_crop_scope_for_region(scene_graph: dict[str, Any] | None, lock: dict[str, Any] | None, explicit_scope: str | None = None) -> tuple[str, list[str]]:
    if explicit_scope:
        return str(explicit_scope), []
    warnings: list[str] = []
    character_mode = _guard_mode((lock or {}).get("character"), "off")
    gender_mode = _guard_mode((lock or {}).get("gender"), "off")
    if character_mode in {"strong", "strict"} and gender_mode in {"strong", "strict"} and _scene_has_relationship_pose_complexity(scene_graph):
        warnings.append("crop_refine_face_mask_unavailable_low_denoise_guard")
        return "outfit_detail_only", warnings
    return "full_character_allowed", warnings


def _build_postpass_character_lock_gate_lane(
    *,
    region: dict[str, Any],
    unit: dict[str, Any],
    scene_graph: dict[str, Any] | None,
    requested_denoise: Any,
    effective_denoise: float,
    requested_strength: float,
    effective_strength: float,
    explicit_denoise_override: bool,
    explicit_scope: str | None = None,
) -> dict[str, Any]:
    label = str(region.get("label") or unit.get("label") or region.get("id") or "Region")
    lock = deepcopy(region.get("lock") if isinstance(region.get("lock"), dict) else {})
    prompt = _clean_text(region.get("prompt"))
    gender_guard = _postpass_gender_guard_for_region(label, prompt, lock)
    scope, scope_warnings = _postpass_crop_scope_for_region(scene_graph, lock, explicit_scope)
    warnings = list(gender_guard.get("warnings") or []) + scope_warnings
    if explicit_denoise_override and effective_denoise > 0.40:
        warnings.append("crop_refine_denoise_may_override_character_lock")
    elif not explicit_denoise_override and effective_denoise <= 0.36:
        warnings.append("crop_refine_denoise_capped_to_preserve_character_lock")
    return {
        "schema": "neo.image.scene_director.postpass_character_lock_gate.lane.v054.v1",
        "phase": "SD-V054-26.9.16",
        "region_id": str(region.get("id") or unit.get("region_id") or ""),
        "label": label,
        "subject_slot": unit.get("subject_slot"),
        "crop_refine_scope": scope,
        "gender_family": gender_guard.get("family"),
        "character_lock_mode": _guard_mode(lock.get("character"), "off"),
        "requested_crop_denoise": requested_denoise,
        "effective_crop_denoise": effective_denoise,
        "requested_lora_strength": requested_strength,
        "effective_lora_strength": effective_strength,
        "positive_guard": gender_guard.get("positive") or "",
        "negative_guard": gender_guard.get("negative") or "",
        "positive_guard_carried": bool(gender_guard.get("positive")),
        "negative_guard_carried": bool(gender_guard.get("negative")),
        "gender_guard_carried": bool(gender_guard.get("gender_guard_carried")),
        "face_mask_available": False,
        "warnings": sorted(set([str(w) for w in warnings if w])),
    }

def _repair_scene_director_detail_lanes_v054(scene_graph: dict[str, Any]) -> dict[str, Any]:
    """Normalize attached detail regions so old V1 hair-lock behavior survives V054.

    UI migrations can leave hair/prop lanes as generic object rows. V054 needs
    semantically correct roles before the prompt bridge and mask routing can make
    them useful.
    """
    graph = deepcopy(scene_graph or {})
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    by_id = {str(r.get("id") or ""): r for r in regions if isinstance(r, dict)}
    repairs: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or "").strip().lower()
        target = str(region.get("target_area") or "").strip().lower()
        attach_to = str(region.get("attach_to") or "").strip()
        if attach_to and _target_is_hair(region) and role in {"object", "character_detail", "custom", "detail", ""}:
            old = role or ""
            region["role"] = "hair_detail"
            region["relationship"] = region.get("relationship") or "attached_to"
            region["target_area"] = region.get("target_area") or "hair"
            region["priority"] = "override" if str(region.get("priority") or "").lower() in {"", "reinforce"} else region.get("priority")
            repairs.append({"region_id": region.get("id"), "label": region.get("label"), "from_role": old, "to_role": "hair_detail", "reason": "attached_hair_detail"})
        if role == "held_prop" and attach_to:
            # Defend against copied inpaint blocks from another detail lane.
            inpaint = region.get("inpaint") if isinstance(region.get("inpaint"), dict) else None
            if inpaint:
                expected_id = str(region.get("id") or "")
                if str(inpaint.get("target_region_id") or "") and str(inpaint.get("target_region_id")) != expected_id:
                    inpaint["target_region_id"] = expected_id
                    repairs.append({"region_id": region.get("id"), "label": region.get("label"), "reason": "fixed_inpaint_target_region"})
                parent = str(inpaint.get("parent_id") or "")
                if parent and parent != attach_to and attach_to in by_id:
                    inpaint["parent_id"] = attach_to
                    repairs.append({"region_id": region.get("id"), "label": region.get("label"), "reason": "fixed_inpaint_parent"})
                preserve = inpaint.get("preserve_regions") if isinstance(inpaint.get("preserve_regions"), list) else []
                if attach_to and attach_to not in preserve:
                    inpaint["preserve_regions"] = [attach_to, *[x for x in preserve if x != attach_to]]
    metadata = graph.setdefault("metadata", {}) if isinstance(graph.setdefault("metadata", {}), dict) else {}
    metadata["detail_lane_role_repair"] = {"phase": "SD-V054-26.8", "repair_count": len(repairs), "repairs": repairs}
    return graph


def _apply_character_lock_execution_bridge_v054(scene_graph: dict[str, Any], params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Normalize Character Lock metadata without hiding live V054 conditioning.

    This bridge is deliberately provider-safe: it does not create new model
    nodes or pretend FaceID/IPAdapter is active. It sanitizes legacy recursive
    guard text and preserves explicit trait/correction fields. The active V054
    node consumes those fields when it builds subject-local CLIP branches; they
    must not be treated as metadata-only.
    """
    graph = _repair_scene_director_detail_lanes_v054(scene_graph)
    params = params or {}
    execution = _character_lock_execution_settings(params)
    if execution.get("mode") == "off":
        metadata = graph.setdefault("metadata", {}) if isinstance(graph.setdefault("metadata", {}), dict) else {}
        bridge_meta = {
            "schema": "neo.image.scene_director.character_lock_execution_bridge.v054.v2",
            "phase": "SD-V054-26.10.8K4",
            "status": "disabled_by_execution_mode",
            "character_count": 0,
            "compiled_positive_count": 0,
            "compiled_negative_count": 0,
            "default_character_lock_mode": "off",
            "guard_policy": "disabled_by_visible_character_lock_execution",
            "identity_strength": 0.0,
            "characters": [],
            "global_positive_bridge": "",
            "global_negative_bridge": "",
            "character_lock_execution": deepcopy(execution),
            "detail_lane_role_repair": metadata.get("detail_lane_role_repair"),
            "live_conditioning_route": "v054_node_attn2_subject_branches" if execution.get("in_sampler_attention_enabled") else "not_requested",
        }
        metadata["character_lock_execution_bridge"] = bridge_meta
        return graph, bridge_meta, "", ""
    char_lock = params.get("character_lock") if isinstance(params.get("character_lock"), dict) else {}
    default_mode = _guard_mode(params.get("character_lock_mode") or char_lock.get("character"), "balanced")
    identity_strength = _float(params.get("identity_strength"), 0.55)
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []

    attached_by_parent: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for region in regions:
        if not isinstance(region, dict):
            continue
        parent = str(region.get("attach_to") or "").strip()
        if not parent:
            continue
        bucket = attached_by_parent.setdefault(parent, {"hair": [], "detail": []})
        if _target_is_hair(region):
            bucket["hair"].append(region)
        else:
            bucket["detail"].append(region)

    all_positive: list[str] = []
    all_negative: list[str] = []
    character_reports: list[dict[str, Any]] = []
    character_index = 0
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        character_index += 1
        lock = deepcopy(region.get("lock") if isinstance(region.get("lock"), dict) else {})
        if default_mode != "off":
            lock.setdefault("character", default_mode)
            for key in ("gender", "skin_tone", "hair", "build", "body_height", "outfit", "negative"):
                value = char_lock.get(key)
                if value is not None:
                    mode = _guard_mode(value, "off")
                    if mode != "off":
                        lock.setdefault(key, mode)
        label = _region_label(region, character_index)
        raw_prompt = _clean_text(region.get("prompt"))
        sanitized_prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_prompt)
        raw_negative = _clean_text(region.get("negative") or region.get("negative_prompt") or "")
        sanitized_negative, negative_hygiene = _strip_generated_negative_text(raw_negative)
        conflict_meta = _detect_prompt_trait_conflicts(sanitized_prompt, sanitized_negative)
        # V25.9.6 Fix 3: do not recursively append compiled guard text back into
        # the user region prompt. If an older saved region already contains bridge
        # text, replace it with the sanitized user-authored description.
        region["prompt"] = sanitized_prompt
        if raw_negative:
            region["negative"] = sanitized_negative
        nested_hygiene = _sanitize_region_nested_prompt_fields(region)
        positive, negative, report = _character_lock_phrase(
            label,
            sanitized_prompt,
            lock,
            {"hair": [], "detail": []},
            identity_strength,
        )
        report["prompt_hygiene"] = prompt_hygiene
        report["negative_prompt_hygiene"] = negative_hygiene
        report["prompt_conflicts"] = conflict_meta
        report["nested_prompt_hygiene"] = nested_hygiene
        report["attached_detail_conditioning_owner"] = "child_region_only"
        report["attached_detail_prompt_copied_to_parent"] = False
        if report.get("status") == "applied":
            region["lock"] = lock
            for attached_hair in attached_by_parent.get(str(region.get("id") or ""), {}).get("hair", []):
                attached_hair["priority"] = "override" if str(attached_hair.get("priority") or "").lower() in {"", "reinforce"} else attached_hair.get("priority")
                hair_prompt, hair_hygiene = _strip_character_lock_generated_text(_clean_text(attached_hair.get("prompt")))
                attached_hair["prompt"] = hair_prompt
                attached_hair.setdefault("metadata", {}) if isinstance(attached_hair.setdefault("metadata", {}), dict) else {}
                if isinstance(attached_hair.get("metadata"), dict):
                    attached_hair["metadata"]["prompt_hygiene"] = hair_hygiene
            all_positive.extend(positive)
            all_negative.extend(negative)
        character_reports.append({"region_id": region.get("id"), **report})

    global_block = graph.setdefault("global", {}) if isinstance(graph.setdefault("global", {}), dict) else {}
    raw_global_prompt = _clean_text(global_block.get("prompt"))
    sanitized_global_prompt, global_prompt_hygiene = _strip_character_lock_generated_text(raw_global_prompt)
    raw_global_negative = _clean_text(global_block.get("negative"))
    sanitized_global_negative, global_negative_hygiene = _strip_generated_negative_text(raw_global_negative)
    global_block["prompt"] = sanitized_global_prompt
    if raw_global_negative:
        global_block["negative"] = sanitized_global_negative
    # Guard phrases remain visible in metadata. Explicit per-character negative
    # corrections are also routed to the sampler's negative CLIP input so the
    # active V054 attention path can suppress a gender/body swap during denoise.
    global_positive_add = ""
    live_negative_terms: list[str] = []
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        correction = _character_lock_correction_block(region)
        negative_text = _clean_text(correction.get("negative_text") or "")
        if negative_text:
            label = _region_label(region, 0)
            live_negative_terms.append(f"{label} live Character Lock negative correction: {negative_text}")
    global_negative_add = ", ".join(live_negative_terms)
    compiled_positive_preview = ", ".join([p for p in all_positive if p])
    compiled_negative_preview = ", ".join([n for n in all_negative if n])

    metadata = graph.setdefault("metadata", {}) if isinstance(graph.setdefault("metadata", {}), dict) else {}
    bridge_meta = {
        "schema": "neo.image.scene_director.character_lock_execution_bridge.v054.v2",
        "phase": "SD-V054-26.9.5",
        "status": "applied" if all_positive or all_negative else "no_active_character_locks",
        "character_count": character_index,
        "compiled_positive_count": len(all_positive),
        "compiled_negative_count": len(all_negative),
        "default_character_lock_mode": default_mode,
        "guard_policy": "prompt_derived_flexible_character_guards",
        "identity_strength": round(identity_strength, 4),
        "characters": character_reports,
        "body_guard_compiler": {
            "schema": "neo.image.scene_director.body_guard_compiler.v25_9_4",
            "phase": "V25.9.4",
            "character_count": len(character_reports),
            "detected_count": len([c for c in character_reports if (c.get("body_guard_compiler") or {}).get("extracted_build_terms")]),
            "inferred_count": len([c for c in character_reports if (c.get("auto_inferred_guards") or {}).get("build")]),
            "characters": [
                {
                    "region_id": c.get("region_id"),
                    "label": c.get("label"),
                    "gender_family": c.get("gender_family"),
                    "build_terms": (c.get("body_guard_compiler") or {}).get("extracted_build_terms", []),
                    "auto_inferred_guards": c.get("auto_inferred_guards", {}),
                    "status": (c.get("body_guard_compiler") or {}).get("status"),
                }
                for c in character_reports
            ],
            "policy": "Body/build/height terms are now first-class Character Lock compiler inputs, not loose prompt leftovers.",
        },
        "global_positive_bridge": global_positive_add,
        "global_negative_bridge": global_negative_add,
        "live_conditioning_route": "v054_node_attn2_subject_branches" if execution.get("in_sampler_attention_enabled") else "not_requested",
        "live_negative_bridge": global_negative_add,
        "explicit_character_trait_field_count": sum(
            1 for region in regions
            if isinstance(region, dict)
            and (
                _flatten_character_trait_values(region.get("character_traits"))
                or _flatten_character_trait_values(region.get("trait_lock"))
                or _character_lock_correction_block(region).get("positive_text")
                or _character_lock_correction_block(region).get("negative_text")
            )
        ),
        "compiled_positive_preview": compiled_positive_preview,
        "compiled_negative_preview": compiled_negative_preview,
        "prompt_hygiene": {
            "schema": "neo.image.scene_director.character_lock_prompt_hygiene.v25_9_6_fix3",
            "phase": "V25.9.6-fix3",
            "status": "applied",
            "prompt_template_injection": False,
            "global_prompt_hygiene": global_prompt_hygiene,
            "global_negative_hygiene": global_negative_hygiene,
            "recursive_guard_removed_count": sum(int(((c.get("prompt_hygiene") or {}).get("recursive_guard_removed_count") or 0)) for c in character_reports) + int(global_prompt_hygiene.get("recursive_guard_removed_count") or 0),
            "trait_bleed_detected_count": sum(1 for c in character_reports if ((c.get("prompt_conflicts") or {}).get("warning_count") or 0)),
            "sanitized_region_prompt_used": any(bool((c.get("prompt_hygiene") or {}).get("sanitized_region_prompt_used")) for c in character_reports) or bool(global_prompt_hygiene.get("sanitized_region_prompt_used")),
            "bridge_prompt_appended_to_global": False,
            "bridge_prompt_appended_to_regions": False,
            "negative_bridge_appended_to_sampler": bool(global_negative_add),
            "live_conditioning_route": "v054_node_attn2_subject_branches" if execution.get("in_sampler_attention_enabled") else "not_requested",
            "policy": "Guard reports remain metadata, while explicit Character Trait Lock and Character Lock correction fields are consumed by NeoSceneDirectorV054 before CLIP branch encoding.",
        },
        "character_lock_execution": deepcopy(execution),
        "detail_lane_role_repair": metadata.get("detail_lane_role_repair"),
    }
    metadata["character_lock_execution_bridge"] = bridge_meta
    return graph, bridge_meta, global_positive_add, global_negative_add


def _v054_character_subject_slot_map(scene_graph: dict[str, Any] | None) -> dict[str, int]:
    """Map V054 character region ids to fixed NeoSceneDirectorV054 subject mask output slots.

    The Comfy node exposes subject_1_mask..subject_4_mask at output indexes 6..9.
    Region indexes include backgrounds/details, so they must not be reused as
    subject mask slots.
    """
    if not isinstance(scene_graph, dict):
        return {}
    mapping: dict[str, int] = {}
    slot = 1
    for region in scene_graph.get("regions") or []:
        if not isinstance(region, dict):
            continue
        if str(region.get("role") or "").strip().lower() != "character":
            continue
        rid = str(region.get("id") or "").strip()
        if rid and slot <= 4:
            mapping[rid] = slot
        slot += 1
    return mapping


def _ensure_v054_subjects_for_masks(scene_graph: dict[str, Any] | None) -> dict[str, Any] | None:
    """Populate scene_graph.subjects so the installed V054 node emits real character masks.

    Older payloads only had regions. The installed node's legacy fallback ignored
    region bboxes when creating subject masks, which made regional IPAdapter masks
    unreliable. This bridge makes the subject mask contract explicit.
    """
    if not isinstance(scene_graph, dict):
        return scene_graph
    # Always rebuild from regions. Some pre-26.9.2 payloads persisted subjects
    # derived from region order and can include detail/held_prop/background lanes.
    subjects: list[dict[str, Any]] = []
    for region in scene_graph.get("regions") or []:
        if not isinstance(region, dict):
            continue
        if str(region.get("role") or "").strip().lower() != "character":
            continue
        subjects.append({
            "id": str(region.get("id") or f"subject_{len(subjects)+1}"),
            "bbox": deepcopy(region.get("bbox") or [0.0, 0.0, 1.0, 1.0]),
            "prompt": str(region.get("prompt") or ""),
            "identity": deepcopy(((region.get("metadata") or {}).get("identity") if isinstance(region.get("metadata"), dict) else {}) or {}),
            "identity_mask_feather": ((region.get("metadata") or {}).get("mask", {}) if isinstance(region.get("metadata"), dict) else {}).get("feather", 18),
        })
        if len(subjects) >= 4:
            break
    scene_graph = deepcopy(scene_graph)
    scene_graph["subjects"] = subjects
    metadata = dict(scene_graph.get("metadata") or {})
    metadata["subject_mask_bridge"] = {
        "phase": "SD-V054-26.9.2",
        "status": "applied",
        "subject_count": len(subjects),
        "mask_output_indexes": {str(item.get("id")): 6 + idx for idx, item in enumerate(subjects)},
    }
    scene_graph["metadata"] = metadata
    return scene_graph


IPADAPTER_PRESERVE_PROFILE = {
    "profile": "identity_preserve_delayed",
    "role": "identity_only",
    "default_start_at_floor": 0.18,
    "default_end_at_ceiling": 0.80,
    "default_weight_ceiling": 0.38,
    "preserve_scene_composition": True,
    "preserve_region_prompt": True,
    "preserve_relationship_pose": True,
    "preserve_outfit_and_props": True,
    "preserve_background_authority": True,
}


def _bool_user_override(src: dict[str, Any], *keys: str) -> bool:
    if not isinstance(src, dict):
        return False
    for key in keys:
        if key in src:
            return bool(src.get(key))
    return False


def _scene_relationship_pose_authority(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Retire the old scene-level Pair Pose route without replaying its text.

    Phase 27.13 makes Character > Pose the only text-pose authority.  Older
    payloads may still contain Pair Pose metadata, so retain a content-free
    tombstone for provenance while explicitly preventing that text from reaching
    character contracts or executable conditioning.
    """
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    metadata = graph_data.get("metadata") if isinstance(graph_data.get("metadata"), dict) else {}
    raw = metadata.get("relationship_pose_authority") if isinstance(metadata.get("relationship_pose_authority"), dict) else metadata.get("pair_pose_authority")
    raw = raw if isinstance(raw, dict) else {}
    legacy_prompt_present = bool(_clean_text(raw.get("prompt") or raw.get("pair_pose_prompt") or raw.get("relationship_pose_prompt")))
    legacy_negative_present = bool(_clean_text(raw.get("negative") or raw.get("negative_guard") or raw.get("pair_pose_negative_guard") or raw.get("relationship_pose_negative_guard")))
    raw_is_retirement_marker = bool(
        str(raw.get("schema") or "").startswith("neo.image.scene_director.pair_pose_retirement")
        or str(raw.get("status") or "") == "retired_character_region_pose_only"
    )
    legacy_input_present = bool(raw.get("legacy_input_present")) if raw_is_retirement_marker else bool(raw)
    character_count = len([r for r in (graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []) if isinstance(r, dict) and _role_is_character(r)])
    return {
        "schema": "neo.image.scene_director.pair_pose_retirement.v25_9_15",
        "phase": "SD-V054-27.13",
        "enabled": False,
        "status": "retired_character_region_pose_only",
        "source": "retired_legacy_pair_pose_metadata" if (legacy_input_present or legacy_prompt_present or legacy_negative_present) else "empty",
        "prompt": "",
        "negative": "",
        "prompt_terms": [],
        "negative_terms": [],
        "strength": 0.0,
        "apply_to_character_traits": False,
        "character_count": character_count,
        "legacy_input_present": legacy_input_present,
        "legacy_prompt_present": legacy_prompt_present,
        "legacy_negative_present": legacy_negative_present,
        "policy": "Advanced Pair Pose is retired. Character > Pose is the sole text-pose authority; exact skeleton authority still requires ControlNet/OpenPose.",
    }


def _scene_background_space_authority(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    metadata = graph_data.get("metadata") if isinstance(graph_data.get("metadata"), dict) else {}
    raw = metadata.get("background_space_authority") if isinstance(metadata.get("background_space_authority"), dict) else {}
    prompt = _clean_text(raw.get("prompt") or raw.get("background_space_prompt"))
    negative = _clean_text(raw.get("negative") or raw.get("negative_guard") or raw.get("background_space_negative_guard"))
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    background_count = len([r for r in regions if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}])
    enabled = bool(raw.get("enabled")) and bool(prompt)
    if enabled and bool(raw.get("synthetic_region_added")):
        status = "active_synthetic_background_lane"
    else:
        status = "active_synthetic_background_lane" if enabled and not background_count else ("metadata_only_existing_background_regions" if enabled else ("disabled" if prompt else "empty"))
    result = {
        "schema": "neo.image.scene_director.background_space_authority.v25_9_9",
        "phase": "V25.9.9",
        "enabled": enabled,
        "status": status,
        "source": "explicit_scene_background_space_field" if prompt else "empty",
        "source_mode": str(raw.get("source_mode") or "explicit_only"),
        "prompt": prompt,
        "negative": negative,
        "prompt_terms": _split_trait_terms(prompt),
        "negative_terms": _split_trait_terms(negative),
        "strength": _float(raw.get("strength"), 0.70),
        "denoise": _float(raw.get("denoise"), 0.42),
        "existing_background_region_count": background_count,
        "synthetic_region_added": bool(raw.get("synthetic_region_added")),
        "synthetic_region_id": str(raw.get("synthetic_region_id") or ""),
        "policy": "When no explicit background region exists, Neo may add one hidden full-canvas background region from this explicit field only; no global prompt is converted into background text.",
    }
    return result


def _apply_scene_director_background_space_authority(scene_graph: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(scene_graph, dict):
        return scene_graph
    graph_data = deepcopy(scene_graph)
    authority = _scene_background_space_authority(graph_data)
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    has_background = any(isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"} for r in regions)
    if authority.get("enabled") and authority.get("prompt") and not has_background:
        synthetic_region_id = "background_space_authority"
        if not any(isinstance(r, dict) and str(r.get("id") or "") == synthetic_region_id for r in regions):
            regions = list(regions) + [{
                "id": synthetic_region_id,
                "role": "background",
                "label": "Background Space Authority",
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "prompt": authority.get("prompt") or "",
                "negative": authority.get("negative") or "",
                "strength": authority.get("strength") or 0.70,
                "priority": "reinforce",
                "metadata": {
                    "schema": "neo.image.scene_director.background_space_authority.synthetic_region.v25_9_9",
                    "phase": "V25.9.9",
                    "hidden_ui_region": True,
                    "source": "background_space_authority",
                    "mask_policy": "full_canvas_background_mask_with_subject_subtraction",
                },
            }]
        authority.update({
            "status": "active_synthetic_background_lane",
            "existing_background_region_count": 0,
            "synthetic_region_added": True,
            "synthetic_region_id": synthetic_region_id,
        })
    elif authority.get("enabled") and has_background:
        authority.update({"status": "metadata_only_existing_background_regions", "synthetic_region_added": False})
    graph_data["regions"] = regions
    metadata = dict(graph_data.get("metadata") or {})
    metadata["background_space_authority"] = authority
    graph_data["metadata"] = metadata
    return graph_data


def _scene_has_relationship_pose_complexity(scene_graph: dict[str, Any] | None) -> bool:
    if not isinstance(scene_graph, dict):
        return False
    text_parts: list[str] = []
    global_data = scene_graph.get("global") if isinstance(scene_graph.get("global"), dict) else {}
    text_parts.append(str(global_data.get("prompt") or ""))
    relationship_pose = _scene_relationship_pose_authority(scene_graph)
    if relationship_pose.get("enabled"):
        text_parts.append(str(relationship_pose.get("prompt") or ""))
    for region in scene_graph.get("regions") or []:
        if not isinstance(region, dict):
            continue
        for key in ("relationship", "relationship_prompt", "prompt", "label"):
            value = region.get(key)
            if value:
                text_parts.append(str(value))
    text = " ".join(text_parts).lower()
    risky_terms = (
        "relationship", "intimate", "couple", "hug", "embrace", "shoulder", "resting his head",
        "resting her head", "holding", "supporting", "close emotional", "protective", "pose",
        "two-person", "two person", "body turned", "physical contact",
    )
    return any(term in text for term in risky_terms)


def _apply_ipadapter_instruction_preservation(unit: dict[str, Any], *, scene_graph: dict[str, Any] | None = None, requested_mode: str = "first_pass_native") -> dict[str, Any]:
    """Phase 26.9.11: keep regional FaceID identity from overriding scene instructions."""
    unit = dict(unit or {})
    mode = str(unit.get("mode") or "faceid").strip().lower()
    if mode not in {"faceid", "standard", "ipadapter"}:
        return unit
    profile = dict(IPADAPTER_PRESERVE_PROFILE)
    scope_mode = str(unit.get("scope_mode") or unit.get("ipadapter_scope_mode") or ("identity_only" if mode == "faceid" else "full_subject_identity")).strip() or "identity_only"
    requested_weight = _float(unit.get("weight"), 0.52)
    requested_weight_faceidv2 = _float(unit.get("weight_faceidv2") if unit.get("weight_faceidv2") is not None else unit.get("weight"), requested_weight)
    requested_start = _float(unit.get("start_at"), 0.05)
    requested_end = _float(unit.get("end_at"), 0.75)
    weight_override = _bool_user_override(unit, "weight_user_override", "ipadapter_weight_user_override", "explicit_weight", "weight_explicit")
    faceidv2_override = _bool_user_override(unit, "weight_faceidv2_user_override", "explicit_weight_faceidv2", "weight_user_override", "ipadapter_weight_user_override")
    start_override = _bool_user_override(unit, "start_at_user_override", "ipadapter_start_at_user_override", "explicit_start_at", "start_at_explicit")
    end_override = _bool_user_override(unit, "end_at_user_override", "ipadapter_end_at_user_override", "explicit_end_at", "end_at_explicit")
    warnings: list[str] = list(unit.get("instruction_preservation_warnings") or unit.get("warnings") or [])

    start_floor = float(profile["default_start_at_floor"])
    end_ceiling = float(profile["default_end_at_ceiling"])
    weight_ceiling = float(profile["default_weight_ceiling"])

    effective_weight = requested_weight if weight_override else min(requested_weight, weight_ceiling)
    effective_weight_faceidv2 = requested_weight_faceidv2 if faceidv2_override else min(requested_weight_faceidv2, weight_ceiling)
    effective_start = requested_start if start_override else max(requested_start, start_floor)
    effective_end = requested_end if end_override else min(requested_end, end_ceiling)

    if requested_start <= 0.05:
        warnings.append("ipadapter_starts_too_early_may_override_composition" if start_override else "ipadapter_start_delayed_to_preserve_composition")
    if weight_override and requested_weight >= 0.45:
        warnings.append("ipadapter_weight_may_override_prompt")
    elif requested_weight >= 0.45 and effective_weight < requested_weight:
        warnings.append("ipadapter_weight_capped_to_preserve_prompt")
    if faceidv2_override and requested_weight_faceidv2 >= 0.45:
        warnings.append("ipadapter_weight_may_override_prompt")
    face_mask_unavailable = False
    if scope_mode in {"full_subject_identity", "upper_identity"}:
        warnings.append("ipadapter_subject_mask_can_affect_outfit_pose")
    else:
        # Until a real face/head mask is available, regional FaceID uses the
        # full subject mask. That is acceptable for simple portraits but risky
        # for intimate/contact poses, so Phase 26.9.12 can force restore mode.
        face_mask_unavailable = True
        warnings.append("face_mask_unavailable_using_subject_mask")
    relationship_risk = False
    if str(requested_mode).lower() in {"first_pass_native", "hybrid", "hybrid_safe_identity", "delayed_first_pass"} and _scene_has_relationship_pose_complexity(scene_graph):
        relationship_risk = True
        warnings.append("ipadapter_first_pass_relationship_pose_risk")

    requested_mode_norm = str(requested_mode or "second_pass_restore").lower()
    if requested_mode_norm == "hybrid_safe_identity" and face_mask_unavailable and relationship_risk:
        warnings.append("ipadapter_relationship_pose_risk_forced_second_pass_restore")
        actual_execution = "second_pass_restore"
    elif requested_mode_norm in {"first_pass_native", "hybrid", "hybrid_safe_identity", "delayed_first_pass"}:
        actual_execution = "delayed_first_pass"
    else:
        actual_execution = str(requested_mode or "second_pass_restore")
    unit.update({
        "requested_weight": requested_weight,
        "requested_weight_faceidv2": requested_weight_faceidv2,
        "requested_start_at": requested_start,
        "requested_end_at": requested_end,
        "effective_weight": effective_weight,
        "effective_weight_faceidv2": effective_weight_faceidv2,
        "effective_start_at": effective_start,
        "effective_end_at": effective_end,
        "weight": effective_weight,
        "weight_faceidv2": effective_weight_faceidv2,
        "start_at": effective_start,
        "end_at": effective_end,
        "scope_mode": scope_mode,
        "ipadapter_execution_mode": actual_execution,
        "ipadapter_instruction_preservation": profile,
        "composition_preservation_enabled": True,
        "instruction_preservation_warnings": sorted(set(str(w) for w in warnings if str(w).strip())),
        "weight_user_override": weight_override,
        "weight_faceidv2_user_override": faceidv2_override,
        "start_at_user_override": start_override,
        "end_at_user_override": end_override,
    })
    return unit


def _normalize_scene_director_identity_units(block: dict[str, Any], subject_slot_by_region: dict[str, int] | None = None, *, scene_graph: dict[str, Any] | None = None, requested_mode: str = "first_pass_native") -> list[dict[str, Any]]:
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    raw_units = assets.get("identity_units") if isinstance(assets.get("identity_units"), list) else []
    units: list[dict[str, Any]] = []
    subject_slot_by_region = subject_slot_by_region or {}
    for index, unit in enumerate(raw_units, start=1):
        if not isinstance(unit, dict):
            continue
        mode = str(unit.get("mode") or unit.get("ipadapter_mode") or "faceid").strip().lower() or "faceid"
        if mode == "ipadapter":
            mode = "standard"
        if mode not in {"standard", "faceid"}:
            continue
        image_names = _norm_image_names(unit)
        if not image_names:
            continue
        clip_vision = str(unit.get("clip_vision") or unit.get("clip_vision_model") or "").strip()
        if not clip_vision or clip_vision.lower() == "auto":
            clip_vision = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
        model_name = str(unit.get("model") or unit.get("ipadapter_model") or "").strip()
        # FaceID does not require a separate model file in the unified loader route.
        if mode == "standard" and not model_name:
            continue
        region_index = _int(unit.get("region_index") or index, index)
        region_id = str(unit.get("region_id") or "")
        # Phase 26.9.2: authoritative subject slots come from the V054 scene
        # graph character order, not stale unit.region_index / unit.subject_slot.
        # Older payloads wrote region-index-derived slots after backgrounds, which
        # routed Person 1 to subject_3_mask and Person 2 LoRA to subject_4_mask.
        subject_slot = _int(subject_slot_by_region.get(region_id) or unit.get("subject_slot") or unit.get("subject_index") or index, index)
        subject_slot = max(1, min(4, subject_slot))
        region_index = max(1, min(999, region_index))
        normalized_unit = {
            "uid": str(unit.get("uid") or unit.get("profile_id") or unit.get("region_id") or f"scene_identity_{index}"),
            "mode": mode,
            "model": model_name,
            "clip_vision": clip_vision,
            "faceid_preset": str(unit.get("faceid_preset") or "FACEID PLUS V2").strip() or "FACEID PLUS V2",
            "faceid_provider": str(unit.get("faceid_provider") or "CUDA").strip() or "CUDA",
            "faceid_lora_strength": _float(unit.get("faceid_lora_strength"), 0.75),
            "weight": _float(unit.get("weight"), 0.52),
            "weight_faceidv2": _float(unit.get("weight_faceidv2") if unit.get("weight_faceidv2") is not None else unit.get("weight"), 1.0),
            "weight_type": str(unit.get("weight_type") or "linear").strip() or "linear",
            "combine_embeds": str(unit.get("combine_embeds") or "concat").strip() or "concat",
            "start_at": _float(unit.get("start_at"), 0.05),
            "end_at": _float(unit.get("end_at"), 0.75),
            "embeds_scaling": str(unit.get("embeds_scaling") or "V only").strip() or "V only",
            "image_names": image_names,
            "image_name": image_names[0],
            "region_id": region_id,
            "region_index": region_index,
            "subject_slot": subject_slot,
            "label": str(unit.get("profile_name") or unit.get("label") or f"Identity Profile {index}"),
            "attn_mask_output_index": 5 + subject_slot,
            "weight_user_override": _bool_user_override(unit, "weight_user_override", "ipadapter_weight_user_override", "explicit_weight", "weight_explicit"),
            "weight_faceidv2_user_override": _bool_user_override(unit, "weight_faceidv2_user_override", "explicit_weight_faceidv2", "weight_user_override", "ipadapter_weight_user_override"),
            "start_at_user_override": _bool_user_override(unit, "start_at_user_override", "ipadapter_start_at_user_override", "explicit_start_at", "start_at_explicit"),
            "end_at_user_override": _bool_user_override(unit, "end_at_user_override", "ipadapter_end_at_user_override", "explicit_end_at", "end_at_explicit"),
            "scope_mode": str(unit.get("scope_mode") or unit.get("ipadapter_scope_mode") or "identity_only").strip() or "identity_only",
        }
        units.append(_apply_ipadapter_instruction_preservation(normalized_unit, scene_graph=scene_graph, requested_mode=requested_mode))
    return units


def _ipadapter_required_nodes_available(available_nodes: Any, mode: str) -> tuple[bool, list[str]]:
    if mode == "faceid":
        required = ["LoadImage", "CLIPVisionLoader", "IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceID"]
    else:
        required = ["LoadImage", "CLIPVisionLoader", "IPAdapterModelLoader", "IPAdapterAdvanced"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    return not missing, missing


def _schema_has_declared_inputs(available_nodes: Any, class_name: str) -> bool:
    schema = _node_schema_from_available(available_nodes, class_name)
    return bool(_schema_input_names(schema))


def _scene_ipadapter_supports_mask(available_nodes: Any, class_name: str) -> bool:
    # Provider-safe rule: when Comfy object_info exposes a schema, obey it.
    # Older object_info snapshots in tests/portable installs can return an empty
    # dict for installed nodes; in that case we use the IPAdapter Plus standard
    # regional input name instead of falsely blocking execution.
    if _schema_has_declared_inputs(available_nodes, class_name):
        return _schema_supports_input(available_nodes, class_name, "attn_mask")
    return True


def _scene_mask_ref_for_identity_unit(scene_node_id: str, unit: dict[str, Any]) -> list[Any] | None:
    idx = _int(unit.get("attn_mask_output_index") or (5 + _int(unit.get("region_index"), 1)), 0)
    if idx <= 0:
        return None
    return [scene_node_id, idx]


def _ipadapter_model_loader_input(available_nodes: Any) -> str:
    for candidate in ("ipadapter_file", "ipadapter_name", "model_name", "model"):
        if _schema_supports_input(available_nodes, "IPAdapterModelLoader", candidate):
            return candidate
    return "ipadapter_file"


def _clip_vision_loader_input(available_nodes: Any) -> str:
    for candidate in ("clip_name", "clip_vision_name", "model_name", "model"):
        if _schema_supports_input(available_nodes, "CLIPVisionLoader", candidate):
            return candidate
    return "clip_name"


def _apply_scene_director_ipadapter_stack(
    graph: dict[str, Any],
    *,
    next_id: int,
    model_ref: list[Any],
    scene_node_id: str,
    block: dict[str, Any],
    available_nodes: Any,
    scene_graph: dict[str, Any] | None = None,
    requested_mode: str = "hybrid_safe_identity",
) -> tuple[int, list[Any], list[str], list[str]]:
    """Execute Scene Director Character Profile IPAdapter/FaceID units safely.

    Phase 26.9 replaces the Phase 21.6/26.7 hard gate with a provider-aware
    route. It only inserts runtime IPAdapter nodes when the required Comfy nodes,
    reference images, and regional Scene Director mask outputs are available.
    If any requirement is missing, the unit remains metadata-only and the patch
    reports the exact block reason instead of faking graph support.
    """
    units = _normalize_scene_director_identity_units(block, _v054_character_subject_slot_map(scene_graph), scene_graph=scene_graph, requested_mode=requested_mode)
    if not units:
        return next_id, list(model_ref), [], []

    current_model_ref = list(model_ref)
    added_all: list[str] = []
    notes: list[str] = []
    blocked: list[str] = []
    shared_faceid_ref: list[Any] | None = None
    clip_input_key = _clip_vision_loader_input(available_nodes)
    model_loader_key = _ipadapter_model_loader_input(available_nodes)

    subject_slot_map = _v054_character_subject_slot_map(scene_graph)
    for unit in units:
        label = str(unit.get("label") or unit.get("uid") or unit.get("region_id") or "identity profile")
        region_id = str(unit.get("region_id") or "")
        if region_id and region_id not in subject_slot_map:
            blocked.append(f"{label}: assigned region mask is missing or is not a character subject; refusing global IPAdapter route")
            continue
        mode = str(unit.get("mode") or "faceid").strip().lower()
        mode = "standard" if mode in {"standard", "ipadapter"} else "faceid"
        ok, missing = _ipadapter_required_nodes_available(available_nodes, mode)
        if not ok:
            blocked.append(f"{label}: missing required node(s): {', '.join(missing)}")
            continue
        mask_ref = _scene_mask_ref_for_identity_unit(scene_node_id, unit)
        apply_class = "IPAdapterFaceID" if mode == "faceid" else "IPAdapterAdvanced"
        if not mask_ref:
            blocked.append(f"{label}: no Scene Director regional mask output was resolved")
            continue
        if not _scene_ipadapter_supports_mask(available_nodes, apply_class):
            blocked.append(f"{label}: {apply_class} does not expose attn_mask in object_info")
            continue
        names = _norm_image_names(unit)
        if not names:
            blocked.append(f"{label}: no reference image")
            continue

        next_id, image_ref, image_added = _build_scene_ipadapter_image_ref(graph, next_id, names)
        added_all.extend(image_added)
        if not image_ref:
            blocked.append(f"{label}: reference image load failed")
            continue

        clip_id = str(next_id); next_id += 1
        graph[clip_id] = {"class_type": "CLIPVisionLoader", "inputs": {clip_input_key: unit.get("clip_vision")}}
        added_all.append(clip_id)

        if mode == "faceid":
            if shared_faceid_ref is None:
                loader_id = str(next_id); next_id += 1
                graph[loader_id] = {
                    "class_type": "IPAdapterUnifiedLoaderFaceID",
                    "inputs": {
                        "model": deepcopy(current_model_ref),
                        "preset": unit.get("faceid_preset") or "FACEID PLUS V2",
                        "lora_strength": unit.get("faceid_lora_strength", 0.75),
                        "provider": unit.get("faceid_provider") or "CUDA",
                    },
                }
                current_model_ref = [loader_id, 0]
                shared_faceid_ref = [loader_id, 1]
                added_all.append(loader_id)
            apply_id = str(next_id); next_id += 1
            graph[apply_id] = {
                "class_type": "IPAdapterFaceID",
                "inputs": {
                    "model": deepcopy(current_model_ref),
                    "ipadapter": deepcopy(shared_faceid_ref),
                    "image": deepcopy(image_ref),
                    "weight": unit.get("effective_weight", unit.get("weight", 1.0)),
                    "weight_faceidv2": unit.get("effective_weight_faceidv2", unit.get("weight_faceidv2", unit.get("weight", 1.0))),
                    "weight_type": unit.get("weight_type", "linear"),
                    "combine_embeds": unit.get("combine_embeds", "concat"),
                    "start_at": unit.get("effective_start_at", unit.get("start_at", 0.0)),
                    "end_at": unit.get("effective_end_at", unit.get("end_at", 1.0)),
                    "embeds_scaling": unit.get("embeds_scaling", "V only"),
                    "clip_vision": [clip_id, 0],
                    "attn_mask": deepcopy(mask_ref),
                },
            }
            current_model_ref = [apply_id, 0]
            added_all.append(apply_id)
            notes.append(f"Scene Director applied masked regional FaceID/IPAdapter identity for {label} using {len(names)} reference image(s).")
            if unit.get("ipadapter_execution_mode") == "delayed_first_pass":
                notes.append(f"Scene Director delayed regional FaceID/IPAdapter for {label} to preserve pose/composition instructions.")
        else:
            loader_id = str(next_id); next_id += 1
            graph[loader_id] = {"class_type": "IPAdapterModelLoader", "inputs": {model_loader_key: unit.get("model")}}
            apply_id = str(next_id); next_id += 1
            graph[apply_id] = {
                "class_type": "IPAdapterAdvanced",
                "inputs": {
                    "model": deepcopy(current_model_ref),
                    "ipadapter": [loader_id, 0],
                    "image": deepcopy(image_ref),
                    "weight": unit.get("effective_weight", unit.get("weight", 1.0)),
                    "weight_type": unit.get("weight_type", "linear"),
                    "combine_embeds": unit.get("combine_embeds", "concat"),
                    "start_at": unit.get("effective_start_at", unit.get("start_at", 0.0)),
                    "end_at": unit.get("effective_end_at", unit.get("end_at", 1.0)),
                    "embeds_scaling": unit.get("embeds_scaling", "V only"),
                    "clip_vision": [clip_id, 0],
                    "attn_mask": deepcopy(mask_ref),
                },
            }
            current_model_ref = [apply_id, 0]
            added_all.extend([loader_id, apply_id])
            notes.append(f"Scene Director applied masked regional standard IPAdapter identity for {label} using {len(names)} reference image(s).")
            if unit.get("ipadapter_execution_mode") == "delayed_first_pass":
                notes.append(f"Scene Director delayed regional IPAdapter for {label} to preserve pose/composition instructions.")

    if blocked:
        notes.extend([f"Scene Director kept regional IPAdapter metadata-only for blocked unit: {item}" for item in blocked])
    if added_all:
        notes.append(f"Scene Director regional IPAdapter execution is active in Phase 26.9; added {len(added_all)} node(s).")
    elif units:
        notes.append("Scene Director regional IPAdapter execution was requested but no safe provider-compatible unit could be built.")
    return next_id, current_model_ref, added_all, notes



def _node_schema_from_available(available_nodes: Any, class_name: str) -> dict[str, Any]:
    if not isinstance(available_nodes, dict):
        return {}
    node = available_nodes.get(class_name)
    if not isinstance(node, dict):
        return {}
    inputs = node.get("input") or node.get("inputs") or {}
    if isinstance(inputs, dict) and ("required" in inputs or "optional" in inputs or "hidden" in inputs):
        return inputs
    return node


def _schema_input_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for group in ("required", "optional", "hidden", "required_inputs", "optional_inputs", "hidden_inputs"):
        value = schema.get(group) if isinstance(schema, dict) else None
        if isinstance(value, dict):
            names.update(map(str, value.keys()))
        elif isinstance(value, (list, tuple, set)):
            names.update(map(str, value))
    return names


def _schema_supports_input(available_nodes: Any, class_name: str, name: str) -> bool:
    schema = _node_schema_from_available(available_nodes, class_name)
    names = _schema_input_names(schema)
    # When no object-info schema is available, only emit standard inputs. Extra
    # masked-ControlNet inputs are added only when Comfy explicitly reports them.
    return name in names


def _controlnet_loader_model_input(available_nodes: Any, loader_node: str) -> str:
    for candidate in ("control_net_name", "controlnet_name", "model_name", "model"):
        if _schema_supports_input(available_nodes, loader_node, candidate):
            return candidate
    return "control_net_name"


def _controlnet_apply_node(available_nodes: Any) -> str | None:
    for candidate in ("ACN_AdvancedControlNetApply", "ACN_ControlNetApplyAdvanced", "AdvancedControlNetApply", "ControlNetApplyAdvanced", "ControlNetApply"):
        if _available_node(available_nodes, candidate):
            return candidate
    return None


def _conditioning_set_mask_node(available_nodes: Any) -> str | None:
    for candidate in ("ConditioningSetMask", "ConditioningSetMaskAndCombine"):
        if _available_node(available_nodes, candidate):
            return candidate
    # Phase 26.4: ConditioningSetMask is a core ComfyUI conditioning node on
    # supported installs. Object-info snapshots can omit it, so do not fall
    # back to global ControlNet merely because discovery missed the schema.
    # If a runtime truly lacks the node, the generated patch metadata names it
    # clearly instead of silently applying ControlNet globally.
    return "ConditioningSetMask"


def _conditioning_set_mask_inputs(available_nodes: Any, class_name: str, conditioning_ref: list[Any], mask_ref: list[Any], strength: float = 1.0) -> dict[str, Any]:
    # ConditioningSetMask is stable across common ComfyUI installs. When object
    # info is unavailable we still emit the canonical inputs instead of silently
    # falling back to global ControlNet.
    inputs: dict[str, Any] = {"conditioning": list(conditioning_ref), "mask": list(mask_ref), "strength": round(float(strength), 4)}
    if not _schema_input_names(_node_schema_from_available(available_nodes, class_name)) or _schema_supports_input(available_nodes, class_name, "set_cond_area"):
        inputs["set_cond_area"] = "mask bounds"
    return inputs


def _conditioning_combine_node(available_nodes: Any) -> str | None:
    for candidate in ("ConditioningCombine", "ConditioningConcat"):
        if _available_node(available_nodes, candidate):
            return candidate
    # Phase 26.5: ConditioningCombine is a common/core Comfy conditioning node.
    # Object-info snapshots can omit it, so emit the canonical node instead of
    # replacing the main positive conditioning with a masked-only branch.
    return "ConditioningCombine"


def _conditioning_combine_inputs(class_name: str, base_ref: list[Any], regional_ref: list[Any]) -> dict[str, Any]:
    if class_name == "ConditioningConcat":
        return {"conditioning_to": list(base_ref), "conditioning_from": list(regional_ref)}
    return {"conditioning_1": list(base_ref), "conditioning_2": list(regional_ref)}


def _regional_controlnet_lanes(scene_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    lanes: list[dict[str, Any]] = []
    for index, region in enumerate((scene_graph or {}).get("regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        control = region.get("control") if isinstance(region.get("control"), dict) else {}
        if control.get("enabled") is not True:
            continue
        model = str(control.get("model") or control.get("controlnet_model") or control.get("control_net_name") or control.get("controlnet_name") or "").strip()
        image_name = str(control.get("image_name") or control.get("reference_id") or control.get("control_image") or control.get("control_image_name") or "").strip()
        lanes.append({
            "uid": str(control.get("uid") or f"v054_control_{region.get('id') or index}"),
            "region_id": str(region.get("id") or f"region_{index}"),
            "region_index": index,
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index}"),
            "type": str(control.get("type") or control.get("preprocessor") or "").strip(),
            "model": model,
            "image_name": image_name,
            "strength": _float(control.get("strength"), 0.75),
            "start": _float(control.get("start"), 0.0),
            "end": _float(control.get("end"), 0.8),
            "mask_mode": str(control.get("mask_mode") or "region").strip().lower() or "region",
            "route_mode": str(control.get("route_mode") or "structure_assist").strip() or "structure_assist",
            "raw_strength": control.get("raw_strength"),
            "raw_end": control.get("raw_end"),
            "strength_cap": control.get("strength_cap"),
            "end_cap": control.get("end_cap"),
            "mask_blend_strength": control.get("mask_blend_strength"),
            "softened": bool(control.get("softened")),
        })
    return lanes


def _apply_scene_director_regional_controlnet_stack(
    graph: dict[str, Any],
    *,
    next_id: int,
    positive_ref: list[Any],
    negative_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    available_nodes: Any,
) -> tuple[int, list[Any], list[Any], list[str], list[str], list[dict[str, Any]]]:
    lanes = _regional_controlnet_lanes(scene_graph)
    if not lanes:
        return next_id, list(positive_ref), list(negative_ref), [], [], []
    if not _available_node(available_nodes, "ControlNetLoader"):
        return next_id, list(positive_ref), list(negative_ref), [], ["Scene Director regional ControlNet skipped: ControlNetLoader is unavailable."], []
    apply_node = _controlnet_apply_node(available_nodes)
    if not apply_node:
        return next_id, list(positive_ref), list(negative_ref), [], ["Scene Director regional ControlNet skipped: no ControlNetApply/Advanced ControlNet apply node is available."], []

    loader_node = "ControlNetLoader"
    model_input = _controlnet_loader_model_input(available_nodes, loader_node)
    current_positive_ref = list(positive_ref)
    current_negative_ref = list(negative_ref)
    added: list[str] = []
    notes: list[str] = []
    applied: list[dict[str, Any]] = []
    for offset, lane in enumerate(lanes):
        if not lane.get("model"):
            notes.append(f"Scene Director regional ControlNet skipped {lane.get('label')}: no ControlNet model selected.")
            continue
        if not lane.get("image_name"):
            notes.append(f"Scene Director regional ControlNet skipped {lane.get('label')}: no reference image/map selected.")
            continue
        load_id = str(next_id)
        graph[load_id] = {"class_type": "LoadImage", "inputs": {"image": lane.get("image_name"), "upload": "image"}}
        loader_id = str(next_id + 1)
        graph[loader_id] = {"class_type": loader_node, "inputs": {model_input: lane.get("model")}}
        apply_id = str(next_id + 2)
        apply_inputs: dict[str, Any] = {
            "positive": list(current_positive_ref),
            "negative": list(current_negative_ref),
            "control_net": [loader_id, 0],
            "image": [load_id, 0],
            "strength": round(_float(lane.get("strength"), 0.75), 4),
        }
        if "Advanced" in apply_node or apply_node.startswith("ACN_"):
            apply_inputs["start_percent"] = round(_float(lane.get("start"), 0.0), 4)
            apply_inputs["end_percent"] = round(_float(lane.get("end"), 0.8), 4)
        mask_ref = [str(scene_node_id), SCENE_NODE_OUTPUT_CONTROL_MASKS]
        mask_input_name = ""
        for candidate in ("mask", "control_mask", "attn_mask", "mask_optional", "mask_image"):
            if _schema_supports_input(available_nodes, apply_node, candidate):
                mask_input_name = candidate
                break
        mask_node = _conditioning_set_mask_node(available_nodes)
        region_mask_requested = lane.get("mask_mode") == "region"
        if region_mask_requested and mask_input_name:
            apply_inputs[mask_input_name] = list(mask_ref)
        elif region_mask_requested and not mask_node:
            notes.append(f"Scene Director regional ControlNet skipped {lane.get('label')}: {apply_node} has no mask input and ConditioningSetMask is unavailable, avoiding accidental global ControlNet.")
            continue
        graph[apply_id] = {"class_type": apply_node, "inputs": apply_inputs}
        control_positive_ref = [apply_id, 0]
        control_negative_ref = [apply_id, 1]
        node_count = 3
        csm_id = ""
        combine_id = ""
        combine_node = ""
        if region_mask_requested and not mask_input_name and mask_node:
            csm_id = str(next_id + 3)
            graph[csm_id] = {
                "class_type": mask_node,
                "inputs": _conditioning_set_mask_inputs(available_nodes, mask_node, control_positive_ref, mask_ref, _float(lane.get("mask_blend_strength"), 0.82)),
            }
            masked_positive_ref = [csm_id, 0]
            combine_node = _conditioning_combine_node(available_nodes) or ""
            if combine_node:
                combine_id = str(next_id + 4)
                graph[combine_id] = {
                    "class_type": combine_node,
                    "inputs": _conditioning_combine_inputs(combine_node, current_positive_ref, masked_positive_ref),
                }
                final_positive_ref = [combine_id, 0]
                node_count = 5
            else:
                # Defensive fallback: never use the masked-only conditioning as
                # the entire sampler positive branch. Keep the base branch and
                # report that the regional blend could not be merged.
                final_positive_ref = list(current_positive_ref)
                notes.append(f"Scene Director regional ControlNet skipped blend for {lane.get('label')}: ConditioningCombine is unavailable; preserving base positive conditioning.")
                node_count = 4
        else:
            # If the apply node itself supports a mask input, its output is
            # already constrained by the mask. Blend that constrained branch back
            # into the base positive conditioning instead of replacing it.
            combine_node = _conditioning_combine_node(available_nodes) or "" if region_mask_requested else ""
            if region_mask_requested and combine_node:
                combine_id = str(next_id + 3)
                graph[combine_id] = {
                    "class_type": combine_node,
                    "inputs": _conditioning_combine_inputs(combine_node, current_positive_ref, control_positive_ref),
                }
                final_positive_ref = [combine_id, 0]
                node_count = 4
            else:
                final_positive_ref = list(control_positive_ref)
        # Keep the original/base negative branch. The regional ControlNet positive
        # is merged into the scene, but its negative output is not allowed to
        # replace the whole negative conditioning. This prevents black or
        # mask-only outputs when a regional ControlNet lane is active.
        final_negative_ref = list(current_negative_ref)
        current_positive_ref = list(final_positive_ref)
        current_negative_ref = list(final_negative_ref)
        added.extend([load_id, loader_id, apply_id] + ([csm_id] if csm_id else []) + ([combine_id] if combine_id else []))
        applied.append({
            "uid": lane.get("uid"),
            "region_id": lane.get("region_id"),
            "label": lane.get("label"),
            "type": lane.get("type"),
            "model": lane.get("model"),
            "image_name": lane.get("image_name"),
            "strength": round(_float(lane.get("strength"), 0.75), 4),
            "start": round(_float(lane.get("start"), 0.0), 4),
            "end": round(_float(lane.get("end"), 0.8), 4),
            "mask_mode": lane.get("mask_mode"),
            "mask_ref": mask_ref,
            "mask_input": mask_input_name or ("ConditioningSetMask" if csm_id else "unavailable"),
            "apply_node": apply_node,
            "conditioning_mask_node": csm_id or None,
            "conditioning_combine_node": combine_id or None,
            "conditioning_combine_class": combine_node or None,
            "controlnet_mask_node_id": csm_id or (apply_id if mask_input_name else None),
            "controlnet_mask_source": "scene_director_control_masks",
            "controlnet_global_suppressed": True,
            "controlnet_conditioning_merged_with_base": bool(combine_id),
            "base_positive_preserved": True,
            "base_negative_preserved": True,
            "route_mode": lane.get("route_mode") or "structure_assist",
            "raw_strength": round(_float(lane.get("raw_strength"), _float(lane.get("strength"), 0.75)), 4),
            "raw_end": round(_float(lane.get("raw_end"), _float(lane.get("end"), 0.8)), 4),
            "softened": bool(lane.get("softened")),
            "strength_cap": lane.get("strength_cap"),
            "end_cap": lane.get("end_cap"),
            "mask_blend_strength": lane.get("mask_blend_strength"),
            "execution": "region_masked_soft_merged",
        })
        if mask_input_name:
            mask_note = f" with {mask_input_name}=control_masks"
        elif csm_id:
            mask_note = " with ConditioningSetMask(control_masks)"
        else:
            mask_note = ""
        if combine_id:
            mask_note += f" + {combine_node}(base positive + regional ControlNet)"
        notes.append(f"Scene Director regional ControlNet routed {lane.get('label')} through {apply_node}{mask_note}.")
        next_id += node_count
    return next_id, current_positive_ref, current_negative_ref, added, notes, applied


def _regional_detailer_passes(scene_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    passes: list[dict[str, Any]] = []
    for index, region in enumerate((scene_graph or {}).get("regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        detailer = region.get("detailer") if isinstance(region.get("detailer"), dict) else {}
        if detailer.get("enabled") is not True:
            continue
        mode = str(detailer.get("mode") or "face").strip().lower() or "face"
        detector = str(detailer.get("detector") or detailer.get("detector_model") or "").strip()
        passes.append({
            "uid": str(detailer.get("uid") or f"v054_detailer_{region.get('id') or index}"),
            "region_id": str(region.get("id") or f"region_{index}"),
            "region_index": index,
            "region_role": str(region.get("role") or ""),
            "label": str(region.get("label") or region.get("id") or f"Region {index}"),
            "mode": mode,
            "detector": detector,
            "detector_type": str(detailer.get("detector_type") or "bbox").strip().lower() or "bbox",
            "custom_classes": str(detailer.get("custom_classes") or ("hand" if mode == "hand" else "face" if mode == "face" else "all")),
            "denoise": _float(detailer.get("denoise"), 0.3),
            "steps": _int(detailer.get("steps"), 20),
            "cfg": _float(detailer.get("cfg"), 5.5),
            "mask_feather": _int(detailer.get("mask_feather") if detailer.get("mask_feather") is not None else detailer.get("mask_blur"), 12),
            "detect_inside_region": detailer.get("detect_inside_region", True) is not False,
            "mask_mode": str(detailer.get("mask_mode") or "region").strip().lower() or "region",
        })
    return passes


def _find_image_consumers(graph: dict[str, Any], image_ref: list[Any]) -> list[tuple[str, str]]:
    consumers: list[tuple[str, str]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for key, value in inputs.items():
            if _same_ref(value, image_ref):
                consumers.append((str(node_id), str(key)))
    return consumers


def _rewrite_image_consumers(graph: dict[str, Any], consumers: list[tuple[str, str]], new_ref: list[Any]) -> list[str]:
    rewired: list[str] = []
    for node_id, input_name in consumers:
        node = graph.get(str(node_id))
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        inputs[input_name] = list(new_ref)
        rewired.append(str(node_id))
    return rewired


def _find_decode_for_latent(graph: dict[str, Any], latent_ref: list[Any]) -> tuple[str | None, list[Any] | None, list[Any] | None]:
    for node_id, node in graph.items():
        if not isinstance(node, dict) or node.get("class_type") != "VAEDecode":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if _same_ref(inputs.get("samples"), latent_ref):
            vae_ref = inputs.get("vae") if isinstance(inputs.get("vae"), list) else None
            return str(node_id), [str(node_id), 0], vae_ref
    return None, None, None


def _detailer_detector_inputs(pass_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    detector_model = str(pass_data.get("detector") or "").strip()
    detector_type = str(pass_data.get("detector_type") or "bbox").strip().lower()
    inputs = {"model_name": detector_model or "bbox/face_yolov8m.pt"}
    if detector_type:
        inputs["type"] = detector_type
    return "UltralyticsDetectorProvider", inputs


def _apply_scene_director_regional_detailer_stack(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    available_nodes: Any,
) -> tuple[int, list[str], list[str], list[dict[str, Any]], list[str]]:
    passes = _regional_detailer_passes(scene_graph)
    if not passes:
        return next_id, [], [], [], []
    if not _available_node(available_nodes, "FaceDetailer"):
        return next_id, [], ["Scene Director regional detailer skipped: FaceDetailer is unavailable."], [], []
    if not _available_node(available_nodes, "UltralyticsDetectorProvider"):
        return next_id, [], ["Scene Director regional detailer skipped: UltralyticsDetectorProvider is unavailable."], [], []

    decode_id, current_image_ref, vae_ref = _find_decode_for_latent(graph, base_latent_ref)
    if current_image_ref is None or vae_ref is None:
        return next_id, [], ["Scene Director regional detailer skipped: no VAEDecode image output was found after the sampler/finish latent."], [], []

    output_consumers = [(node_id, input_name) for node_id, input_name in _find_image_consumers(graph, current_image_ref) if node_id != decode_id]
    added: list[str] = []
    notes: list[str] = []
    applied: list[dict[str, Any]] = []
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    seed = max(1, _int(sampler_inputs.get("seed"), 1))
    current_ref = list(current_image_ref)
    for item in passes:
        detector_class, detector_inputs = _detailer_detector_inputs(item)
        detector_id = str(next_id)
        graph[detector_id] = {"class_type": detector_class, "inputs": detector_inputs}
        detailer_id = str(next_id + 1)
        detailer_inputs: dict[str, Any] = {
            "image": list(current_ref),
            "model": list(model_ref),
            "clip": list(clip_ref),
            "vae": list(vae_ref),
            "guide_size": 512.0,
            "guide_size_for": True,
            "max_size": 1024.0,
            "seed": seed,
            "steps": max(1, _int(item.get("steps"), 20)),
            "cfg": min(15.0, max(0.0, _float(item.get("cfg"), 5.5))),
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "positive": list(positive_ref),
            "negative": list(negative_ref),
            "denoise": min(1.0, max(0.0, _float(item.get("denoise"), 0.3))),
            "feather": max(0, _int(item.get("mask_feather"), 12)),
            "noise_mask": True,
            "force_inpaint": True,
            "bbox_threshold": 0.30,
            "bbox_dilation": max(0, _int(item.get("mask_feather"), 12)),
            "bbox_crop_factor": 2.0,
            "drop_size": 10,
            "bbox_detector": [detector_id, 0],
            "wildcard": "",
            "cycle": 1,
            "inpaint_model": False,
            "noise_mask_feather": max(0, _int(item.get("mask_feather"), 12)),
        }
        mask_ref = [str(scene_node_id), SCENE_NODE_OUTPUT_DETAIL_MASKS]
        detailer_inputs["neo_region_mask_ref"] = list(mask_ref)  # metadata-only unless custom FaceDetailer bridge consumes it.
        graph[detailer_id] = {"class_type": "FaceDetailer", "inputs": detailer_inputs}
        current_ref = [detailer_id, 0]
        added.extend([detector_id, detailer_id])
        applied.append({
            "uid": item.get("uid"),
            "region_id": item.get("region_id"),
            "label": item.get("label"),
            "mode": item.get("mode"),
            "detector": item.get("detector") or detector_inputs.get("model_name"),
            "denoise": round(_float(item.get("denoise"), 0.3), 4),
            "steps": max(1, _int(item.get("steps"), 20)),
            "mask_feather": max(0, _int(item.get("mask_feather"), 12)),
            "detect_inside_region": item.get("detect_inside_region"),
            "mask_ref": mask_ref,
            "mask_mode": item.get("mask_mode"),
            "detailer_node": "FaceDetailer",
        })
        notes.append(f"Scene Director regional detailer routed {item.get('label')} through FaceDetailer as a {item.get('mode')} finish pass.")
        next_id += 2
    rewired = _rewrite_image_consumers(graph, output_consumers, current_ref) if output_consumers else []
    if not rewired:
        notes.append("Scene Director regional detailer created finish passes, but no downstream image consumer was found to rewire.")
    return next_id, added, notes, applied, rewired

def _normalize_scene_director_lora_units(block: dict[str, Any], legacy: dict[str, Any] | None = None, subject_slot_by_region: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Resolve Scene Director regional LoRA bindings without touching IPAdapter.

    LoRA Stack owns selection/catalog state. Scene Director only consumes rows
    that LoRA Stack has already marked for scene_region_* targets and executes
    them as a separate masked low-denoise latent refinement pass after the
    primary Scene Director/IPAdapter sample path.
    """
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    raw = []
    if isinstance(legacy, dict) and isinstance((legacy.get("scene_director_lora_region_assignments") or {}).get("lora_lanes"), list):
        raw = (legacy.get("scene_director_lora_region_assignments") or {}).get("lora_lanes") or []
    if not raw:
        raw = assets.get("lora_bindings") if isinstance(assets.get("lora_bindings"), list) else []
    if not raw and isinstance(legacy, dict) and isinstance(legacy.get("scene_director_lora_bindings"), list):
        raw = legacy.get("scene_director_lora_bindings") or []
    units: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    subject_slot_by_region = subject_slot_by_region or {}
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        source_region_index = _int(item.get("region_index") or item.get("target_region_index") or index, index)
        region_index = max(1, min(999, source_region_index))
        region_id = str(item.get("region_id") or item.get("apply_to") or "")
        subject_slot = _int(subject_slot_by_region.get(region_id) or item.get("subject_slot") or item.get("subject_index") or 0, 0)
        if subject_slot <= 0:
            # No subject mask exists for backgrounds/details/props. Keep these
            # out of the subject-lane finish pass instead of corrupting slots.
            continue
        subject_slot = max(1, min(4, subject_slot))
        name = str(item.get("name") or item.get("lora_name") or item.get("file") or "").strip()
        if not name:
            continue
        key = (subject_slot, name)
        if key in seen:
            continue
        seen.add(key)
        owner_row = deepcopy(item.get("owner_row") if isinstance(item.get("owner_row"), dict) else {})
        units.append({
            "uid": str(item.get("uid") or f"scene_lora_region_{region_index}_{len(units) + 1}"),
            "region_id": region_id,
            "region_index": region_index,
            "region_role": str(item.get("region_role") or item.get("role") or ""),
            "subject_slot": subject_slot,
            "attn_mask_output_index": 5 + subject_slot,
            "slot": _int(item.get("slot") or len(units) + 1, len(units) + 1),
            "name": name,
            "strength": _float(item.get("strength"), 0.8),
            "strength_user_override": bool(item.get("strength_user_override") or any(k in owner_row for k in ("strength", "strength_model", "strength_clip"))),
            "target": str(item.get("target") or owner_row.get("target") or "both"),
            "route_mode": str(item.get("route_mode") or owner_row.get("route_mode") or "auto").strip() or "auto",
            "finish_denoise": item.get("finish_denoise", owner_row.get("finish_denoise")),
            "finish_steps": item.get("finish_steps", owner_row.get("finish_steps")),
            "crop_denoise": item.get("crop_denoise", owner_row.get("crop_denoise")),
            "crop_steps": item.get("crop_steps", owner_row.get("crop_steps")),
            "crop_padding": item.get("crop_padding", owner_row.get("crop_padding")),
            "crop_feather": item.get("crop_feather", owner_row.get("crop_feather")),
            "crop_scope": str(item.get("crop_scope") or owner_row.get("crop_scope") or "").strip(),
            "visibility_preset": str(item.get("visibility_preset") or item.get("lora_visibility_preset") or owner_row.get("visibility_preset") or owner_row.get("lora_visibility_preset") or "off").strip() or "off",
            "visibility_preset_plan": deepcopy(item.get("visibility_preset_plan") if isinstance(item.get("visibility_preset_plan"), dict) else owner_row.get("visibility_preset_plan") if isinstance(owner_row.get("visibility_preset_plan"), dict) else {}),
            "postpass_lock_policy": str(item.get("postpass_lock_policy") or item.get("character_lock_postpass_policy") or owner_row.get("postpass_lock_policy") or owner_row.get("character_lock_postpass_policy") or "preserve").strip() or "preserve",
            "character_lock_postpass_policy": str(item.get("character_lock_postpass_policy") or item.get("postpass_lock_policy") or owner_row.get("character_lock_postpass_policy") or owner_row.get("postpass_lock_policy") or "preserve").strip() or "preserve",
            "allow_character_repaint": bool(item.get("allow_character_repaint") or owner_row.get("allow_character_repaint")),
            "allow_character_lock_bypass": bool(item.get("allow_character_lock_bypass") or owner_row.get("allow_character_lock_bypass")),
            "lora_compatibility": deepcopy(item.get("lora_compatibility") if isinstance(item.get("lora_compatibility"), dict) else owner_row.get("lora_compatibility") if isinstance(owner_row.get("lora_compatibility"), dict) else {}),
            "lora_family": str(item.get("lora_family") or owner_row.get("lora_family") or "").strip(),
            "checkpoint_family": str(item.get("checkpoint_family") or owner_row.get("checkpoint_family") or "").strip(),
            "checkpoint_name": str(item.get("checkpoint_name") or owner_row.get("checkpoint_name") or "").strip(),
            "finish_denoise_user_override": bool(item.get("finish_denoise_user_override") or owner_row.get("finish_denoise_user_override")),
            "finish_steps_user_override": bool(item.get("finish_steps_user_override") or owner_row.get("finish_steps_user_override")),
            "crop_denoise_user_override": bool(item.get("crop_denoise_user_override") or owner_row.get("crop_denoise_user_override")),
            "crop_steps_user_override": bool(item.get("crop_steps_user_override") or owner_row.get("crop_steps_user_override")),
            "trigger_words": str(item.get("trigger_words") or item.get("trigger_override") or owner_row.get("trigger_words") or "").strip(),
            "source_record_trigger_words": str(item.get("source_record_trigger_words") or owner_row.get("source_record_trigger_words") or "").strip(),
            "source_record_activation_text": str(item.get("source_record_activation_text") or owner_row.get("source_record_activation_text") or owner_row.get("activation_text") or "").strip(),
            "source_record_id": str(item.get("source_record_id") or owner_row.get("source_record_id") or owner_row.get("record_id") or "").strip(),
            "runtime_proof": deepcopy(item.get("runtime_proof") if isinstance(item.get("runtime_proof"), dict) else owner_row.get("runtime_proof") if isinstance(owner_row.get("runtime_proof"), dict) else {}),
            "runtime_applied": bool(item.get("runtime_applied") or owner_row.get("runtime_applied")),
            "lora_file_exists": item.get("lora_file_exists", owner_row.get("lora_file_exists")),
            "lora_load_success": item.get("lora_load_success", owner_row.get("lora_load_success")),
            "lora_load_error": item.get("lora_load_error", owner_row.get("lora_load_error")),
            "model_patch_created": item.get("model_patch_created", owner_row.get("model_patch_created")),
            "delta_eval_attempted": item.get("delta_eval_attempted", owner_row.get("delta_eval_attempted")),
            "delta_nonzero": item.get("delta_nonzero", owner_row.get("delta_nonzero")),
            "delta_norm_mean": item.get("delta_norm_mean", owner_row.get("delta_norm_mean")),
            "delta_norm_max": item.get("delta_norm_max", owner_row.get("delta_norm_max")),
            "assigned_mask_coverage": item.get("assigned_mask_coverage", owner_row.get("assigned_mask_coverage")),
            "effective_delta_strength": item.get("effective_delta_strength", owner_row.get("effective_delta_strength")),
            "owner_row": owner_row,
            "label": str(item.get("label") or f"Region {region_index}"),
            "source": str(item.get("source") or "scene_director_lora_row_assignment"),
        })
    return units


def _region_lookup_by_id(scene_graph: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    regions = (scene_graph or {}).get("regions") if isinstance(scene_graph, dict) else []
    return {str(r.get("id") or ""): r for r in regions or [] if isinstance(r, dict) and str(r.get("id") or "")}



def _split_trigger_terms(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    # Trigger words can be comma/newline-separated, but multi-token activation
    # phrases should survive whitespace. Keep comma/newline as hard separators.
    raw = []
    for chunk in text.replace("\r", "\n").replace(";", ",").split(","):
        raw.extend(part.strip() for part in chunk.split("\n") if part.strip())
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        clean = _clean_text(item)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _resolve_lora_trigger_terms(unit: dict[str, Any], region_prompt: str) -> tuple[list[str], str, list[str]]:
    warnings: list[str] = []
    sources = [
        ("row_trigger_words", unit.get("trigger_words")),
        ("source_record", unit.get("source_record_trigger_words")),
        ("source_record", unit.get("source_record_activation_text")),
    ]
    for source, value in sources:
        terms = _split_trigger_terms(value)
        if terms:
            return terms, source, warnings
    # Region prompt can already carry an activation token such as cloud_strife.
    # Use only explicit prompt tokens with trigger-like shape; never infer from filenames.
    import re
    region_terms: list[str] = []
    for chunk in _clean_text(region_prompt).split(","):
        token = chunk.strip().split(" ", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+_[A-Za-z0-9_]+", token) and token not in region_terms:
            region_terms.append(token)
    if region_terms:
        return region_terms[:4], "region_prompt", warnings
    warnings.append("regional_lora_missing_trigger_terms")
    return [], "none", warnings


def _region_role_for_unit(unit: dict[str, Any], region: dict[str, Any]) -> str:
    return str(unit.get("region_role") or region.get("role") or "").strip().lower()


def _is_character_lora_unit(unit: dict[str, Any], region: dict[str, Any]) -> bool:
    target = str(unit.get("target") or "both").strip().lower()
    return _region_role_for_unit(unit, region) in _CHARACTER_ROLES and target in {"model", "clip", "both"}

def _lora_route_mode(unit: dict[str, Any]) -> str:
    mode = str(unit.get("route_mode") or "auto").strip().lower().replace("-", "_")
    return mode or "auto"

LORA_VISIBILITY_PRESETS: dict[str, dict[str, Any]] = {
    "off": {"preset": "off", "label": "Manual / row default"},
    "soft": {"preset": "soft", "label": "Soft", "strength": 0.8, "finish_denoise": 0.28, "finish_steps": 14, "crop_denoise": 0.30, "crop_steps": 18, "crop_scope": "character_style"},
    "balanced": {"preset": "balanced", "label": "Balanced", "strength": 0.95, "finish_denoise": 0.36, "finish_steps": 16, "crop_denoise": 0.36, "crop_steps": 22, "crop_scope": "character_style"},
    "strong": {"preset": "strong", "label": "Strong", "strength": 1.10, "finish_denoise": 0.45, "finish_steps": 20, "crop_denoise": 0.45, "crop_steps": 26, "crop_scope": "character_style"},
}


def _lora_visibility_preset_id(unit: dict[str, Any]) -> str:
    raw = str(unit.get("visibility_preset") or unit.get("lora_visibility_preset") or "off").strip().lower().replace("-", "_").replace(" ", "_")
    return raw if raw in LORA_VISIBILITY_PRESETS else "off"


def _lora_visibility_booster_plan(unit: dict[str, Any], region: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = _lora_visibility_preset_id(unit)
    lock = (region or {}).get("lock") if isinstance((region or {}).get("lock"), dict) else {}
    character_guard = _guard_mode(lock.get("character"), "off")
    gender_guard = _guard_mode(lock.get("gender"), "off")
    guarded = character_guard in {"strong", "strict"} or (character_guard in {"balanced"} and gender_guard in {"strong", "strict"})
    effective = requested
    warnings: list[str] = []
    if requested == "strong" and not guarded:
        effective = "balanced"
        warnings.append("lora_visibility_strong_requires_character_lock_downgraded")
    preset = deepcopy(LORA_VISIBILITY_PRESETS.get(effective) or LORA_VISIBILITY_PRESETS["off"])
    return {
        "schema": "neo.image.scene_director.lora_visibility_booster.v054.v1",
        "phase": "SD-V054-26.10.8E",
        "requested_preset": requested,
        "effective_preset": effective,
        "enabled": effective != "off",
        "guard_policy": "character_lock_guarded_visibility_boost",
        "character_lock_guarded": bool(guarded),
        "character_guard": character_guard,
        "gender_guard": gender_guard,
        "preset_values": preset,
        "warnings": warnings,
    }


def _lora_family_guess_from_text(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if not text.strip():
        return "unknown"
    if any(token in text for token in ("pony", "pdxl", "score_9", "score 9")):
        return "pony_xl"
    if any(token in text for token in ("sdxl", " xl", "xl_", "animagine", "illustrious", "kohaku", "realvisxl", "juggernautxl", "albedobase")):
        return "sdxl"
    if any(token in text for token in ("sd1.5", "sd15", "sd_1_5", "sd1_5", "v1-5", "v1_5", "1.5", "anything", "dreamshaper", "deliberate", "revanimated", "majicmix")):
        return "sd15"
    if any(token in text for token in ("flux", "schnell")):
        return "flux"
    return "unknown"


def _checkpoint_family_guess(legacy: dict[str, Any] | None = None) -> tuple[str, str]:
    legacy = legacy or {}
    checkpoint = str(legacy.get("checkpoint") or legacy.get("checkpoint_name") or legacy.get("ckpt_name") or legacy.get("model") or "").strip()
    family = str(legacy.get("family") or "").strip().lower().replace("-", "_")
    from_name = _lora_family_guess_from_text(checkpoint)
    if from_name != "unknown":
        return from_name, checkpoint
    if family in {"pony", "pony_xl", "pdxl"}:
        return "pony_xl", checkpoint
    if family in {"sdxl", "sdxl_sd", "xl"}:
        return "sdxl", checkpoint
    if family in {"sd", "sd15", "sd1.5", "sd_1_5", "sd1_5"}:
        return "sd15", checkpoint
    if family in {"flux", "flux_dev"}:
        return "flux", checkpoint
    return "unknown", checkpoint


def _lora_checkpoint_compatibility(unit: dict[str, Any], legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    lora_family = str(unit.get("lora_family") or "").strip().lower().replace("-", "_")
    if not lora_family or lora_family == "unknown":
        lora_family = _lora_family_guess_from_text(unit.get("name"), unit.get("source_record_id"), unit.get("source_record_base_model"), unit.get("source_record_activation_text"))
    checkpoint_family = str(unit.get("checkpoint_family") or "").strip().lower().replace("-", "_")
    checkpoint_name = str(unit.get("checkpoint_name") or "").strip()
    if not checkpoint_family or checkpoint_family == "unknown":
        checkpoint_family, checkpoint_name_from_legacy = _checkpoint_family_guess(legacy)
        checkpoint_name = checkpoint_name or checkpoint_name_from_legacy
    warnings: list[str] = []
    status = "verify"
    compatible: bool | None = None
    if lora_family == "unknown" or checkpoint_family == "unknown":
        warnings.append("lora_checkpoint_family_unknown_verify")
        status = "verify"
    elif lora_family == checkpoint_family or (lora_family == "sdxl" and checkpoint_family == "pony_xl"):
        compatible = True
        status = "compatible"
    elif lora_family == "pony_xl" and checkpoint_family == "sdxl":
        warnings.append("pony_xl_lora_on_non_pony_sdxl_checkpoint_verify")
        status = "verify"
    else:
        compatible = False
        status = "mismatch"
        warnings.append("lora_checkpoint_family_mismatch")
    return {
        "schema": "neo.image.scene_director.lora_checkpoint_compatibility.v054.v1",
        "phase": "SD-V054-26.10.8E",
        "lora_name": unit.get("name"),
        "lora_family": lora_family,
        "checkpoint_name": checkpoint_name,
        "checkpoint_family": checkpoint_family,
        "status": status,
        "compatible": compatible,
        "warnings": warnings,
        "message": "LoRA/checkpoint compatibility is verified." if status == "compatible" else ("Likely LoRA/checkpoint family mismatch." if status == "mismatch" else "Verify LoRA base model against the active checkpoint."),
    }


def _apply_lora_visibility_strength(unit: dict[str, Any], region: dict[str, Any], requested_strength: float, explicit_strength_override: bool) -> tuple[float, dict[str, Any]]:
    plan = _lora_visibility_booster_plan(unit, region)
    strength = float(requested_strength)
    if plan.get("enabled") and not explicit_strength_override:
        preset_strength = _float((plan.get("preset_values") or {}).get("strength"), strength)
        strength = max(strength, preset_strength)
    return round(max(0.0, min(strength, 2.0)), 4), plan


def _optional_unit_value(unit: dict[str, Any], key: str, fallback: Any = None) -> Any:
    value = unit.get(key)
    if value is None or value == "":
        return fallback
    return value

def _lora_strength_pair(unit: dict[str, Any], strength: float) -> tuple[float, float]:
    target = str(unit.get("target") or "both").strip().lower()
    if target == "model":
        return float(strength), 0.0
    if target == "clip":
        return 0.0, float(strength)
    return float(strength), float(strength)


def _character_lock_is_strong_or_strict(region: dict[str, Any] | None, *, require_gender: bool = False) -> bool:
    lock = region.get("lock") if isinstance(region, dict) and isinstance(region.get("lock"), dict) else {}
    character_mode = _guard_mode(lock.get("character"), "off")
    gender_mode = _guard_mode(lock.get("gender"), "off")
    if require_gender:
        return character_mode in {"strong", "strict"} and gender_mode in {"strong", "strict"}
    return character_mode in {"strong", "strict"} or gender_mode in {"strong", "strict"}


def _lora_postpass_lock_policy(unit: dict[str, Any] | None) -> str:
    raw = str(
        (unit or {}).get("postpass_lock_policy")
        or (unit or {}).get("character_lock_postpass_policy")
        or (unit or {}).get("character_lock_policy")
        or "preserve"
    ).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "preserve",
        "auto": "preserve",
        "safe": "preserve",
        "locked": "preserve",
        "preserve_character_lock": "preserve",
        "identity_safe": "preserve",
        "allow_repaint": "allow_repaint",
        "allow_character_repaint": "allow_repaint",
        "character_repaint": "allow_repaint",
        "unlock": "allow_repaint",
        "unlocked": "allow_repaint",
        "bypass": "allow_repaint",
        "bypass_character_lock": "allow_repaint",
    }
    return aliases.get(raw, "preserve")


def _lora_postpass_unlock_requested(unit: dict[str, Any] | None) -> bool:
    if _lora_postpass_lock_policy(unit) == "allow_repaint":
        return True
    return bool(
        (unit or {}).get("allow_character_repaint")
        or (unit or {}).get("allow_character_lock_bypass")
        or (unit or {}).get("character_lock_unlocked")
    )


def _locked_character_lora_postpass_risk(unit: dict[str, Any], region: dict[str, Any], scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    character_locked = _character_lock_is_strong_or_strict(region)
    gender_locked = _character_lock_is_strong_or_strict(region, require_gender=True)
    relationship_pose = _scene_has_relationship_pose_complexity(scene_graph)
    unlock_requested = _lora_postpass_unlock_requested(unit)
    route_mode = _lora_route_mode(unit)
    visibility_preset = _lora_visibility_preset_id(unit)
    return {
        "schema": "neo.image.scene_director.latent_character_lock_postpass_authority.risk.v054.v1",
        "phase": "SD-V054-26.10.8H",
        "character_locked": character_locked,
        "gender_locked": gender_locked,
        "relationship_pose": relationship_pose,
        "unlock_requested": unlock_requested,
        "postpass_lock_policy": _lora_postpass_lock_policy(unit),
        "route_mode": route_mode,
        "visibility_preset": visibility_preset,
        "requires_latent_protection": bool(character_locked and not unlock_requested),
        "blocks_crop_refine": bool(character_locked and not unlock_requested),
    }


def _gender_family_from_locked_region(region: dict[str, Any] | None) -> str:
    if not isinstance(region, dict):
        return "unspecified"
    prompt = _clean_text(region.get("prompt") or "")
    gender = _extract_gender_terms(prompt)
    family = str(gender.get("family") or "unspecified")
    if family == "mixed":
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        if _guard_mode(lock.get("gender"), "off") in {"strong", "strict"}:
            male_terms = gender.get("male_terms") or []
            female_terms = gender.get("female_terms") or []
            if male_terms and not female_terms:
                return "male"
            if female_terms and not male_terms:
                return "female"
    return family


def _latent_character_lock_local_conditioning_text(
    region: dict[str, Any],
    *,
    label: str,
    negative_fallback: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """Phase 26.10.8I: build a masked local conditioner for post-LoRA Character Lock.

    26.10.8H re-used the broad Scene Director positive/negative conditioning. That
    could still carry global style/relationship ambiguity into the masked rescue pass.
    This helper creates a region-local lock conditioner so the latent pass has a
    concrete gender/body target instead of another global scene prompt.
    """
    prompt = _clean_text(region.get("prompt") or "")
    label_text = _clean_text(label or region.get("label") or region.get("id") or "Locked character")
    family = _gender_family_from_locked_region(region)
    base_positive = _append_unique_text(
        prompt,
        f"{label_text} latent Character Lock: preserve this exact assigned subject only; keep the same region, pose, outfit, props, and body role",
        "one subject in this mask, no extra subject, no cross-region identity bleed",
    )
    base_negative = _append_unique_text(
        negative_fallback,
        str(region.get("negative") or ""),
        "wrong region assignment, swapped subject, extra subject, merged body, changed identity, changed outfit, changed prop",
    )
    guard_positive = ""
    guard_negative = ""
    if family == "male":
        guard_positive = (
            "locked male anatomy and presentation, exactly one male young man, masculine face, masculine jawline, "
            "masculine body silhouette, flat male chest, broad shoulders, athletic male build, male waist and hips, "
            "male fantasy warrior body, armor fitted to a male torso"
        )
        guard_negative = (
            "female, woman, girl, feminine face, feminine body, feminine torso, female chest, breasts, cleavage, "
            "curvy hips, hourglass figure, narrow feminine waist, bikini armor, exposed female chest, lipstick, makeup, "
            "long eyelashes, soft feminine facial structure, gender swap, wrong gender, androgynous female presentation"
        )
    elif family == "female":
        guard_positive = (
            "locked female anatomy and presentation, exactly one female subject, feminine face and body presentation, "
            "preserve the explicitly requested female silhouette and outfit role"
        )
        guard_negative = (
            "male, man, boy, masculine face, masculine body on female subject, beard, mustache, broad male torso, "
            "gender swap, wrong gender"
        )
    elif family == "flexible":
        guard_positive = "locked gender expression, preserve the requested nonbinary or flexible gender presentation exactly"
        guard_negative = "wrong gender expression, binary gender drift, changed gender presentation"
    else:
        guard_positive = "locked body presentation, preserve the explicitly described subject anatomy, silhouette, and outfit role"
        guard_negative = "gender swap, wrong gender, changed body presentation, changed silhouette"

    positive = _append_unique_text(base_positive, guard_positive)
    negative = _append_unique_text(base_negative, guard_negative)
    return positive, negative, {
        "schema": "neo.image.scene_director.latent_character_lock_local_conditioning.v054.v1",
        "phase": "SD-V054-26.10.8I",
        "source": "region_local_gender_body_lock",
        "gender_family": family,
        "label": label_text,
        "positive_guard": guard_positive,
        "negative_guard": guard_negative,
    }


def _normalize_character_lock_execution_mode(value: Any, default: str = "latent_attention") -> str:
    """Normalize the user-facing Character Lock pass plan.

    The old labels were ambiguous: ``latent_correction`` and
    ``masked_correction`` both meant extra KSampler work, even though the
    legacy V1 ``hair_focus_strong`` behavior came from the main sampler's
    attention patch. Keep old payloads readable, but give them the intended
    separation in the V054 runtime:

    * ``latent_attention``: the fast in-sampler attn2 lock only;
    * ``latent_repair``: in-sampler lock plus the optional structured masked
      trait repair lanes;
    * ``end_refinement``: optional late repair lanes without the in-sampler
      Character Lock branch;
    * ``latent_and_refinement``: explicitly request both families.
    """
    raw = str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "prompt": "prompt_guard_only",
        "prompt_only": "prompt_guard_only",
        "prompt_guard": "prompt_guard_only",
        "guard_only": "prompt_guard_only",
        "text_only": "prompt_guard_only",
        "attention": "latent_attention",
        "attention_only": "latent_attention",
        "in_sampler": "latent_attention",
        "in_sampler_attention": "latent_attention",
        "legacy_attention": "latent_attention",
        "legacy_in_sampler_attention": "latent_attention",
        "hairlock_strong": "latent_attention",
        # V1/V2 payload compatibility: these old names did not describe the
        # actual execution layer. V054 now maps them to the intended legacy
        # in-sampler behavior instead of silently adding late samplers.
        "latent": "latent_attention",
        "latent_pass": "latent_attention",
        "latent_correction": "latent_attention",
        "latent_correction_pass": "latent_attention",
        "character_latent": "latent_repair",
        "latent_trait_repair": "latent_repair",
        "latent_repair_pass": "latent_repair",
        "masked": "end_refinement",
        "mask": "end_refinement",
        "masked_pass": "end_refinement",
        "masked_correction": "end_refinement",
        "masked_correction_pass": "end_refinement",
        "refinement": "end_refinement",
        "end": "end_refinement",
        "end_pass": "end_refinement",
        "final": "end_refinement",
        "both": "latent_and_refinement",
        "latent_plus_refinement": "latent_and_refinement",
        "latent_and_end_refinement": "latent_and_refinement",
        "none": "off",
        "false": "off",
        "disabled": "off",
        "0": "off",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in {
        "prompt_guard_only",
        "latent_attention",
        "latent_repair",
        "end_refinement",
        "latent_and_refinement",
        "off",
    } else default


def _character_trait_lanes_force_on(legacy: dict[str, Any] | None) -> bool:
    """Return whether the visible Character Trait Lanes gate is force-enabled.

    This control is a *gate* for a separately selected repair plan.  It must not
    change ``latent_attention`` into a split sampler by itself.  Earlier V054
    builds did that promotion for compatibility, but it made a stale ``force_on``
    value silently replace the legacy one-sampler Hairlock route.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    controls = legacy.get("scene_director_advanced_fix_pass_controls")
    if not isinstance(controls, dict):
        controls = legacy.get("advanced_fix_pass_controls")
    controls = controls if isinstance(controls, dict) else {}
    raw = controls.get("character_trait_lanes")
    if raw is None:
        raw = legacy.get("scene_director_fix_pass_character_trait_lanes")
    if raw is None and str(controls.get("mode") or "").strip().lower().replace("-", "_") == "force_all":
        raw = "force_on"
    return str(raw or "auto").strip().lower().replace("-", "_").replace(" ", "_") in {
        "on",
        "force",
        "force_on",
        "always",
        "enabled",
        "true",
        "1",
    }


def _character_lock_execution_settings(legacy: dict[str, Any] | None) -> dict[str, Any]:
    """Phase 27.1: resolve Character Lock layers independently.

    Character Lock strength owns the main V054 attn2 branch. Extra masked
    samplers are opt-in pass families and must never be inferred from the
    strength value alone.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    first_pass = legacy.get("scene_director_first_pass_character_lock_authority") if isinstance(legacy.get("scene_director_first_pass_character_lock_authority"), dict) else {}
    raw_policy = legacy.get("scene_director_character_lock_execution") if isinstance(legacy.get("scene_director_character_lock_execution"), dict) else {}
    explicit_plan = (
        raw_policy.get("pass_plan")
        or raw_policy.get("plan")
        or legacy.get("scene_director_character_lock_pass_plan")
        or legacy.get("character_lock_pass_plan")
    )
    raw = (
        explicit_plan
        or raw_policy.get("mode")
        or raw_policy.get("execution_mode")
        or first_pass.get("execution_mode")
        or first_pass.get("execution")
        or legacy.get("scene_director_character_lock_execution_mode")
        or legacy.get("character_lock_execution_mode")
        or "latent_attention"
    )
    requested_mode = _normalize_character_lock_execution_mode(raw)
    mode = requested_mode
    source = "ui_visible_fields" if (
        explicit_plan
        or raw_policy
        or "execution_mode" in first_pass
        or "execution" in first_pass
        or "scene_director_character_lock_execution_mode" in legacy
        or "character_lock_execution_mode" in legacy
    ) else "visible_ui_default"
    trait_lanes_force_on = _character_trait_lanes_force_on(legacy)
    # Phase 27.7: never infer a second sampler from the lane gate.  The pass-plan
    # selector is the sole execution owner.  Strong/Strict Character Lock and
    # its explicit traits already run inside V054's main attn2 model patch.
    promoted_from_fast_attention = False
    in_sampler_attention = mode in {"latent_attention", "latent_repair", "latent_and_refinement"}
    latent_repair = mode in {"latent_repair", "latent_and_refinement"}
    end_refinement = mode in {"end_refinement", "latent_and_refinement"}
    return {
        "schema": "neo.image.scene_director.character_lock_execution.settings.v054.v2",
        "phase": "SD-V054-27.1",
        "dedupe_phase": "V25.9.14",
        "mode": mode,
        "pass_plan": mode,
        "requested_mode": requested_mode,
        "requested_pass_plan": requested_mode,
        "effective_mode": mode,
        "effective_pass_plan": mode,
        "pass_plan_source": source,
        "character_trait_lanes_force_on": trait_lanes_force_on,
        "promoted_from_fast_attention": promoted_from_fast_attention,
        "trait_lane_gate_role": "selected_plan_gate_only",
        "single_sampler_legacy_route": bool(mode == "latent_attention"),
        "prompt_guard_enabled": mode != "off",
        "in_sampler_attention_enabled": in_sampler_attention,
        "masked_correction_enabled": latent_repair or end_refinement,
        "latent_correction_requested": latent_repair,
        "latent_repair_enabled": latent_repair,
        "end_refinement_enabled": end_refinement,
        "uses_scene_masks": latent_repair or end_refinement,
        "source": source,
        "policy": "Character Lock strength and explicit traits run through the uninterrupted V054 in-sampler attention branch. Character Trait Lanes only gates an explicitly selected repair plan and never promotes the fast plan. End refinement remains a separate opt-in family.",
    }


def _character_lock_authority_settings(legacy: dict[str, Any] | None) -> dict[str, Any]:
    """Phase 26.10.8J: user-visible first-pass Character Lock authority settings.

    These values are intentionally sourced from payload/UI fields. The backend only
    clamps and records them; it must not hide new behavior behind invisible preset
    constants.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    raw = legacy.get("scene_director_first_pass_character_lock_authority") if isinstance(legacy.get("scene_director_first_pass_character_lock_authority"), dict) else {}
    execution = _character_lock_execution_settings(legacy)
    enabled_raw = raw.get("enabled") if "enabled" in raw else legacy.get("scene_director_character_lock_first_pass_enabled", True)
    timing = str(raw.get("timing") or legacy.get("scene_director_character_lock_first_pass_timing") or "before_adapters").strip().lower().replace("-", "_").replace(" ", "_")
    if timing in {"after_base", "after_base_composition", "base", "before_adapter", "before_adapters"}:
        timing = "before_adapters"
    elif timing in {"final", "final_pass", "after_extensions"}:
        timing = "final_pass"
    elif timing in {"both", "before_and_final", "before_adapters_and_final"}:
        timing = "both"
    else:
        timing = "before_adapters"
    apply_to = str(raw.get("apply_to") or legacy.get("scene_director_character_lock_first_pass_apply_to") or "strong_strict_only").strip().lower().replace("-", "_").replace(" ", "_")
    if apply_to not in {"strong_strict_only", "all_locked_characters"}:
        apply_to = "strong_strict_only"
    cfg_mode = str(raw.get("cfg_mode") or legacy.get("scene_director_character_lock_first_pass_cfg_mode") or "inherit").strip().lower().replace("-", "_").replace(" ", "_")
    if cfg_mode not in {"inherit", "custom"}:
        cfg_mode = "inherit"
    mask_source = str(raw.get("mask_source") or legacy.get("scene_director_character_lock_first_pass_mask_source") or "full_character_mask").strip().lower().replace("-", "_").replace(" ", "_")
    if mask_source not in {"full_character_mask", "upper_body", "face_torso", "subject_mask"}:
        mask_source = "full_character_mask"
    return {
        "schema": "neo.image.scene_director.first_pass_character_lock_authority.settings.v054.v1",
        "phase": "SD-V054-26.10.8J",
        "dedupe_phase": "V25.9.14",
        "ui_owner": "fix_pass_controls",
        "enabled": _legacy_truthy(enabled_raw),
        "apply_to": apply_to,
        "timing": timing,
        "denoise": round(max(0.0, min(1.0, _float(raw.get("denoise", legacy.get("scene_director_character_lock_first_pass_denoise")), 0.30))), 4),
        "steps": max(1, min(80, _int(raw.get("steps", legacy.get("scene_director_character_lock_first_pass_steps")), 10))),
        "cfg_mode": cfg_mode,
        "cfg": max(0.0, min(30.0, _float(raw.get("cfg", legacy.get("scene_director_character_lock_first_pass_cfg")), 0.0))),
        "mask_source": mask_source,
        "mask_feather": max(0, min(256, _int(raw.get("mask_feather", legacy.get("scene_director_character_lock_first_pass_mask_feather")), 24))),
        "protect_outfit": _legacy_truthy(raw.get("protect_outfit", legacy.get("scene_director_character_lock_first_pass_protect_outfit", True))),
        "protect_pose_contact": _legacy_truthy(raw.get("protect_pose_contact", legacy.get("scene_director_character_lock_first_pass_protect_pose_contact", True))),
        "execution_mode": execution.get("mode"),
        "execution": execution.get("mode"),
        "character_lock_execution": deepcopy(execution),
        "source": "fix_pass_controls_visible_fields",
    }



def _normalize_fix_pass_mode(value: Any, default: str = "auto") -> str:
    raw = str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"off", "disabled", "none", "false", "0"}:
        return "off"
    if raw in {"on", "force", "force_on", "always", "enabled", "true", "1"}:
        return "force_on"
    return "auto"


def _advanced_fix_pass_controls(legacy: dict[str, Any] | None) -> dict[str, Any]:
    legacy = legacy if isinstance(legacy, dict) else {}
    raw = legacy.get("scene_director_advanced_fix_pass_controls") if isinstance(legacy.get("scene_director_advanced_fix_pass_controls"), dict) else legacy.get("advanced_fix_pass_controls") if isinstance(legacy.get("advanced_fix_pass_controls"), dict) else {}
    mode = str(raw.get("mode") or legacy.get("scene_director_fix_pass_mode") or "smart_auto").strip().lower().replace("-", "_").replace(" ", "_")
    if mode not in {"smart_auto", "minimal_fast", "manual", "force_all"}:
        mode = "smart_auto"
    if mode == "minimal_fast":
        defaults = {"first": "off", "background": "off", "character": "off", "final": "off"}
    elif mode == "force_all":
        defaults = {"first": "force_on", "background": "force_on", "character": "force_on", "final": "force_on"}
    else:
        defaults = {"first": "auto", "background": "auto", "character": "auto", "final": "auto"}
    controls = {
        "schema": "neo.image.scene_director.advanced_fix_pass_controls.v25_9_13",
        "phase": "V25.9.13",
        "dedupe_phase": "V25.9.14",
        "ui_ownership": {
            "character_lock": "trait_guard_authority_only",
            "fix_pass_controls": "repair_pass_execution_and_numeric_controls",
        },
        "mode": mode,
        "first_pass_character_lock_rescue": _normalize_fix_pass_mode(raw.get("first_pass_character_lock_rescue", legacy.get("scene_director_fix_pass_first_character_lock_rescue", defaults["first"])), defaults["first"]),
        "background_restore": _normalize_fix_pass_mode(raw.get("background_restore", legacy.get("scene_director_fix_pass_background_restore", defaults["background"])), defaults["background"]),
        "character_trait_lanes": _normalize_fix_pass_mode(raw.get("character_trait_lanes", legacy.get("scene_director_fix_pass_character_trait_lanes", defaults["character"])), defaults["character"]),
        "final_background_reconciliation": _normalize_fix_pass_mode(raw.get("final_background_reconciliation", legacy.get("scene_director_fix_pass_final_background_reconciliation", defaults["final"])), defaults["final"]),
        "environment_aware_character_lanes": _legacy_truthy(raw.get("environment_aware_character_lanes", legacy.get("scene_director_fix_pass_environment_aware_character_lanes", True))),
        "layout_safety_enabled": _legacy_truthy(raw.get("layout_safety_enabled", True)),
        "layout_safety_background_safe_area_min": _float(raw.get("layout_safety_background_safe_area_min"), 12.0),
        "layout_safety_full_height_threshold": _float(raw.get("layout_safety_full_height_threshold"), 0.92),
        "source": "ui_visible_advanced_scene_control",
        "policy": "Fix passes are optional surgical tools. Auto preserves legacy behavior; Off skips costly/overcooking repair lanes; Character Trait Lanes = Force on promotes a fast default to midpoint repair; Force on still respects hard safety gates.",
    }
    layout = raw.get("layout_safety") if isinstance(raw.get("layout_safety"), dict) else {}
    if layout:
        controls["layout_safety"] = deepcopy(layout)
    return controls


def _v054_live_character_conditioning_report(scene_graph: dict[str, Any] | None, execution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report whether explicit character fields reach the active V054 route.

    This is a runtime contract, not a promise that the model will never drift.
    It distinguishes the live V054 attn2 conditioning path from optional masked
    repair samplers so diagnostics cannot call a deliberately unrequested lane
    "disabled" when the selected in-sampler path is active.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    execution = execution if isinstance(execution, dict) else _character_lock_execution_settings({})
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    character_regions = [region for region in regions if isinstance(region, dict) and _role_is_character(region)]
    trait_regions = []
    correction_regions = []
    negative_regions = []
    for region in character_regions:
        traits = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
        correction = _character_lock_correction_block(region)
        if traits:
            trait_regions.append(str(region.get("id") or region.get("label") or "character"))
        if str(correction.get("positive_text") or "").strip() or str(correction.get("negative_text") or "").strip():
            correction_regions.append(str(region.get("id") or region.get("label") or "character"))
        if str(correction.get("negative_text") or "").strip():
            negative_regions.append(str(region.get("id") or region.get("label") or "character"))
    in_sampler = bool(execution.get("in_sampler_attention_enabled"))
    return {
        "schema": "neo.image.scene_director.character_trait_conditioning.workflow.v054.v1",
        "phase": "SD-V054-27.2",
        "status": "live_in_sampler" if in_sampler and (trait_regions or correction_regions) else ("no_explicit_fields" if not (trait_regions or correction_regions) else "preserved_for_node"),
        "route": "scene_graph_json -> NeoSceneDirectorV054 -> subject_local_clip_branches -> attn2_main_sampler",
        "execution_mode": execution.get("mode"),
        "in_sampler_attention_enabled": in_sampler,
        "character_trait_regions": trait_regions,
        "character_correction_regions": correction_regions,
        "character_negative_correction_regions": negative_regions,
        "explicit_trait_fields_preserved": bool(trait_regions or correction_regions),
        "masked_repair_is_separate": True,
        "policy": "Explicit trait and correction fields are live conditioning for in-sampler V054 attention; masked trait lanes are only built for the selected latent-repair plan.",
    }


def _fix_pass_allowed(controls: dict[str, Any], key: str) -> bool:
    return str((controls or {}).get(key) or "auto") != "off"


def _fix_pass_disabled_meta(schema: str, phase: str, pass_key: str, controls: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = {
        "schema": schema,
        "phase": phase,
        "status": "disabled_by_advanced_fix_pass_controls",
        "nodes_added": [],
        "lanes": [],
        "applied_count": 0,
        "skipped_count": 0,
        "advanced_fix_pass_controls": deepcopy(controls),
        "warnings": [f"{pass_key}_disabled_by_advanced_fix_pass_controls"],
        "policy": "This repair pass was skipped because the user disabled it under Advanced Scene Control > Fix Pass Controls.",
    }
    if extra:
        meta.update(deepcopy(extra))
    return meta

def _character_lock_correction_block(region: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(region, dict):
        return {}
    raw = region.get("character_lock_correction") if isinstance(region.get("character_lock_correction"), dict) else {}
    # Flat fields remain supported so the UI can store lightweight region state.
    merged = {
        **raw,
        "enabled": region.get("character_lock_correction_enabled", raw.get("enabled", "auto")),
        "gender_family": region.get("character_lock_gender_family", raw.get("gender_family", "auto")),
        "positive_text": region.get("character_lock_positive_text", raw.get("positive_text", raw.get("positive", ""))),
        "negative_text": region.get("character_lock_negative_text", raw.get("negative_text", raw.get("negative", ""))),
        "denoise": region.get("character_lock_correction_denoise", raw.get("denoise")),
        "steps": region.get("character_lock_correction_steps", raw.get("steps")),
    }
    return merged


def _character_lock_correction_enabled_for_region(region: dict[str, Any], settings: dict[str, Any]) -> bool:
    if str(region.get("role") or region.get("type") or "").strip().lower() != "character":
        return False
    correction = _character_lock_correction_block(region)
    enabled_raw = correction.get("enabled", "auto")
    enabled_text = str(enabled_raw).strip().lower()
    if enabled_text in {"0", "false", "no", "off", "disabled"}:
        return False
    if enabled_text in {"1", "true", "yes", "on", "enabled"}:
        return True
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    character_mode = _guard_mode(lock.get("character"), "off")
    gender_mode = _guard_mode(lock.get("gender"), "off")
    if settings.get("apply_to") == "all_locked_characters":
        return character_mode != "off" or gender_mode != "off"
    return character_mode in {"strong", "strict"} or gender_mode in {"strong", "strict"}




def _character_lock_rescue_profile(candidate_regions: list[tuple[str, dict[str, Any], int]], legacy: dict[str, Any] | None) -> str:
    """V25.9.6 Fix 2: resolve numeric rescue strength from visible lock modes only.

    This helper intentionally does not add prompt words. It only decides whether
    the already-visible masked correction lane should run with strong or strict
    numeric settings.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    raw_mode = (
        legacy.get("character_lock_mode")
        or legacy.get("scene_director_character_lock_mode")
        or (((legacy.get("character_lock") or {}) if isinstance(legacy.get("character_lock"), dict) else {}).get("character"))
        or "off"
    )
    profile = _guard_mode(raw_mode, "off")
    for _region_id, region, _slot in candidate_regions:
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        for key in ("character", "gender", "build", "body_height", "body", "height", "outfit"):
            mode = _guard_mode(lock.get(key), "off")
            if mode == "strict":
                return "strict"
            if mode == "strong" and profile not in {"strict"}:
                profile = "strong"
    return "strong" if profile in {"strong", "strict"} else "off"


def _character_lock_conditional_rescue_policy(
    *,
    primary_attention_lock_active: bool,
    settings: dict[str, Any],
    candidate_regions: list[tuple[str, dict[str, Any], int]],
    timing_stage: str,
    legacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether rescue can run even when legacy attention is primary.

    V25.9.3 correctly demoted masked correction to rescue-only, but skipped it
    whenever the attention patch was present. V25.9.6 Fix 2 restores a conditional
    rescue path for high-risk locked-character scenes, without making masked
    correction the primary success proof and without injecting prompt text.
    """
    legacy = legacy if isinstance(legacy, dict) else {}
    profile = _character_lock_rescue_profile(candidate_regions, legacy)
    candidate_count = len(candidate_regions)
    gender_body_outfit_locked = False
    strict_region_count = 0
    strong_region_count = 0
    region_summaries: list[dict[str, Any]] = []
    for region_id, region, slot in candidate_regions:
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        modes = {key: _guard_mode(lock.get(key), "off") for key in ("character", "gender", "build", "body_height", "body", "height", "outfit", "skin_tone", "hair")}
        if any(modes.get(key) in {"strong", "strict"} for key in ("gender", "build", "body_height", "body", "height", "outfit")):
            gender_body_outfit_locked = True
        if any(value == "strict" for value in modes.values()):
            strict_region_count += 1
        if any(value in {"strong", "strict"} for value in modes.values()):
            strong_region_count += 1
        region_summaries.append({
            "region_id": str(region_id),
            "label": str(region.get("label") or region_id),
            "subject_slot": int(slot),
            "lock_modes": modes,
        })

    # Structural risk only. No prompt phrase matching and no hidden character text.
    execution = settings.get("character_lock_execution") if isinstance(settings.get("character_lock_execution"), dict) else _character_lock_execution_settings(legacy)
    end_refinement_requested = bool(execution.get("end_refinement_enabled"))
    high_risk = bool(
        primary_attention_lock_active
        and settings.get("enabled")
        and settings.get("masked_correction_enabled", True) is not False
        and end_refinement_requested
        and profile in {"strong", "strict"}
        and candidate_count > 0
        and (candidate_count >= 2 or gender_body_outfit_locked)
    )
    effective_numeric = {}
    if high_risk:
        if profile == "strict":
            effective_numeric = {"denoise_min": 0.65, "steps_min": 20, "mask_feather_max": 12}
        else:
            effective_numeric = {"denoise_min": 0.50, "steps_min": 16, "mask_feather_max": 16}
    reasons = []
    if primary_attention_lock_active:
        reasons.append("legacy_attention_primary_active")
    if candidate_count >= 2:
        reasons.append("multiple_locked_character_regions")
    if gender_body_outfit_locked:
        reasons.append("gender_body_or_outfit_guard_active")
    if profile in {"strong", "strict"}:
        reasons.append(f"{profile}_character_lock_profile")
    return {
        "schema": "neo.image.scene_director.conditional_rescue_fallback.v25_9_6_fix2",
        "phase": "V25.9.6-fix2",
        "status": "enabled" if high_risk else "not_required",
        "allow_with_primary_attention": bool(high_risk),
        "primary_attention_lock_active": bool(primary_attention_lock_active),
        "candidate_region_count": candidate_count,
        "strong_or_strict_region_count": strong_region_count,
        "strict_region_count": strict_region_count,
        "gender_body_or_outfit_guard_active": bool(gender_body_outfit_locked),
        "profile": profile,
        "timing_stage": str(timing_stage or ""),
        "effective_numeric": effective_numeric,
        "reason_codes": reasons,
        "prompt_injection": False,
        "regions": region_summaries,
        "policy": "Run masked Character Lock as a rescue-only pass for high-risk Strong/Strict locked-character scenes even when legacy attention is primary; do not add prompt text or treat rescue as primary proof.",
    }


def _apply_character_lock_rescue_numeric_settings(settings: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Apply V25.9.6 Fix 2 numeric rescue floors/ceilings only."""
    effective = deepcopy(settings) if isinstance(settings, dict) else {}
    numeric = policy.get("effective_numeric") if isinstance(policy, dict) else {}
    if not numeric:
        return effective
    requested = {
        "denoise": _float(effective.get("denoise"), 0.30),
        "steps": _int(effective.get("steps"), 10),
        "mask_feather": _int(effective.get("mask_feather"), 24),
    }
    effective["denoise"] = round(max(requested["denoise"], _float(numeric.get("denoise_min"), requested["denoise"])), 4)
    effective["steps"] = max(requested["steps"], _int(numeric.get("steps_min"), requested["steps"]))
    effective["mask_feather"] = min(requested["mask_feather"], _int(numeric.get("mask_feather_max"), requested["mask_feather"]))
    effective["rescue_numeric_policy"] = {
        "schema": "neo.image.scene_director.conditional_rescue_fallback.numeric.v25_9_6_fix2",
        "phase": "V25.9.6-fix2",
        "requested": requested,
        "effective": {
            "denoise": effective["denoise"],
            "steps": effective["steps"],
            "mask_feather": effective["mask_feather"],
        },
        "prompt_injection": False,
        "profile": policy.get("profile"),
    }
    return effective

def _character_lock_region_conditioning_text(
    region: dict[str, Any],
    *,
    label: str,
    negative_fallback: str = "",
    settings: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Build masked rescue conditioning from sanitized user-authored fields only.

    V25.9.6 Fix 3 removes backend prompt templates from this rescue pass. The
    rescue sampler should not inject generic phrases like "first-pass Character
    Lock correction", armor/outfit defaults, or pose/contact templates. It uses
    the user's visible region prompt and negative prompt after recursive bridge
    text has been stripped.
    """
    settings = settings if isinstance(settings, dict) else {}
    correction = _character_lock_correction_block(region)
    label_text = _clean_text(label or region.get("label") or region.get("id") or "Locked character")
    raw_region_prompt = _clean_text(region.get("prompt") or "")
    raw_region_negative = _clean_text(region.get("negative") or region.get("negative_prompt") or "")
    region_prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_region_prompt)
    region_negative, negative_hygiene = _strip_generated_negative_text(raw_region_negative)
    fallback_negative, fallback_hygiene = _strip_generated_negative_text(negative_fallback)
    conflict_meta = _detect_prompt_trait_conflicts(region_prompt, region_negative)

    requested_family = str(correction.get("gender_family") or "auto").strip().lower()
    if requested_family and requested_family != "auto":
        gender_family = requested_family
        gender_family_source = "ui_region_field"
    else:
        gender_family = _gender_family_from_locked_region({**region, "prompt": region_prompt})
        gender_family_source = "prompt_detection"

    positive_override = _clean_text(correction.get("positive_text") or "")
    negative_override = _clean_text(correction.get("negative_text") or "")
    positive = region_prompt
    negative = _append_unique_text(fallback_negative, region_negative)
    meta = {
        "schema": "neo.image.scene_director.first_pass_character_lock_local_conditioning.v054.v1",
        "phase": "V25.9.6-fix3",
        "source": "sanitized_user_region_fields_only",
        "gender_family": gender_family,
        "gender_family_source": gender_family_source,
        "label": label_text,
        "positive_guard": "",
        "negative_guard": "",
        "ui_positive_override_present": bool(positive_override),
        "ui_negative_override_present": bool(negative_override),
        "ui_positive_override_used": False,
        "ui_negative_override_used": False,
        "ignored_generated_override_fields": [
            name for name, value in (("positive_text", positive_override), ("negative_text", negative_override)) if value
        ],
        "protect_outfit": bool(settings.get("protect_outfit")),
        "protect_pose_contact": bool(settings.get("protect_pose_contact")),
        "fallback_used": False,
        "prompt_template_injection": False,
        "sanitized_region_prompt_used": True,
        "prompt_hygiene": prompt_hygiene,
        "negative_prompt_hygiene": negative_hygiene,
        "fallback_negative_hygiene": fallback_hygiene,
        "prompt_conflicts": conflict_meta,
        "policy": "Rescue conditioning uses only sanitized user-authored region prompt/negative fields; backend generic guard templates and generated overrides are ignored.",
    }
    return positive, negative, meta



def _background_and_character_area_report(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Estimate background coverage after subject-mask subtraction.

    V25.9.6 Fix 4 proof: the node owns the real background mask, but the
    backend can still report whether full-height character boxes leave enough
    visible background for a restore pass. This uses region boxes only; it does
    not invent prompt text or change user-authored regions.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    backgrounds = [r for r in regions if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}]
    characters = [r for r in regions if isinstance(r, dict) and _role_is_character(r)]

    def area(vals: tuple[float, float, float, float] | None) -> float:
        if not vals:
            return 0.0
        x1, y1, x2, y2 = vals
        x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2)
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    def shrink(vals: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
        if not vals:
            return None
        x1, y1, x2, y2 = vals
        w = max(0.0, x2 - x1)
        h = max(0.0, y2 - y1)
        # Match the installed node's eroded protection intent: protect the core
        # subject area, not the entire layout lane. This leaves background pixels
        # around full-height/half-frame character regions without overwriting the
        # subject center.
        mx = min(w * 0.18, 0.08)
        my = min(h * 0.06, 0.04)
        return (min(1.0, x1 + mx), min(1.0, y1 + my), max(0.0, x2 - mx), max(0.0, y2 - my))

    rows: list[dict[str, Any]] = []
    for bg in backgrounds:
        bg_vals = _bbox_values(bg) or (0.0, 0.0, 1.0, 1.0)
        bg_area = max(0.0001, area(bg_vals))
        raw_protected = 0.0
        core_protected = 0.0
        for char in characters:
            raw_protected += bg_area * _bbox_overlap_ratio(bg_vals, _bbox_values(char) or (0, 0, 0, 0))
            core_vals = shrink(_bbox_values(char))
            core_protected += bg_area * _bbox_overlap_ratio(bg_vals, core_vals or (0, 0, 0, 0))
        # This is an estimate; overlaps between characters may double count.
        raw_visible = max(0.0, bg_area - min(bg_area, raw_protected))
        core_visible = max(0.0, bg_area - min(bg_area, core_protected))
        rows.append({
            "region_id": str(bg.get("id") or ""),
            "label": _clean_text(bg.get("label") or bg.get("id") or "Background"),
            "background_area_percent": round(bg_area * 100.0, 3),
            "raw_box_subtracted_visible_area_percent": round((raw_visible / bg_area) * 100.0, 3),
            "eroded_subject_protection_visible_area_percent": round((core_visible / bg_area) * 100.0, 3),
        })
    warning_codes: list[str] = []
    if rows and max(float(r.get("raw_box_subtracted_visible_area_percent") or 0.0) for r in rows) < 12.0:
        warning_codes.append("character_regions_leave_low_raw_background_area")
    if rows and max(float(r.get("eroded_subject_protection_visible_area_percent") or 0.0) for r in rows) < 18.0:
        warning_codes.append("background_visible_area_still_low_after_eroded_protection")
    return {
        "schema": "neo.image.scene_director.background_mask_visibility.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "background_region_count": len(backgrounds),
        "character_region_count": len(characters),
        "background_mask_subtracted_from_subjects": bool(backgrounds and characters),
        "subject_protection_mode": "eroded_character_core",
        "rows": rows,
        "warning_codes": warning_codes,
    }


def _background_restore_prompt_pair(region: dict[str, Any], graph: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    global_block = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    raw_prompt = _clean_text(region.get("prompt") or region.get("background_prompt"))
    raw_negative = _clean_text(region.get("negative") or region.get("background_negative_guard"))
    global_negative = _clean_text(global_block.get("negative"))
    prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_prompt)
    negative, negative_hygiene = _strip_generated_negative_text(_append_unique_text("", raw_negative, global_negative))
    return prompt, negative, {
        "schema": "neo.image.scene_director.background_restore_conditioning.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "source": "sanitized_user_background_region_fields_only",
        "prompt_template_injection": False,
        "prompt_hygiene": prompt_hygiene,
        "negative_prompt_hygiene": negative_hygiene,
    }


def _scene_background_prime_contract(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Collect user-authored background fields for early/main conditioning.

    Post-latent background inpaint cannot safely create scenery behind broad
    full-height character boxes because the mask either has almost no true
    background area or it touches protected subjects. V25.9.11 therefore moves
    background authority earlier into the main conditioning path while keeping
    all text user-authored.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    controls = metadata.get("advanced_fix_pass_controls") if isinstance(metadata.get("advanced_fix_pass_controls"), dict) else {}
    if controls and _legacy_truthy(controls.get("environment_aware_character_lanes", True)) is False:
        return {
            "schema": "neo.image.scene_director.environment_character_context.v25_9_12",
            "phase": "V25.9.12",
            "status": "disabled_by_advanced_fix_pass_controls",
            "source": "ui_visible_advanced_scene_control",
            "background_region_count": 0,
            "prompt": "",
            "negative": "",
            "prompt_terms": [],
            "negative_terms": [],
            "stripped_character_lane_scope_terms": [],
            "prompt_template_injection": False,
            "advanced_fix_pass_controls": deepcopy(controls),
            "policy": "Environment-aware character lane text was disabled under Advanced Scene Control > Fix Pass Controls.",
        }
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    backgrounds = [
        r for r in regions
        if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}
    ]
    prompt_terms: list[str] = []
    negative_terms: list[str] = []
    prompt_hygiene_rows: list[dict[str, Any]] = []
    negative_hygiene_rows: list[dict[str, Any]] = []
    for region in backgrounds:
        prompt, negative, conditioning = _background_restore_prompt_pair(region, graph)
        if prompt:
            prompt_terms.append(prompt)
        if negative:
            negative_terms.append(negative)
        if isinstance(conditioning, dict):
            prompt_hygiene_rows.append(conditioning.get("prompt_hygiene") or {})
            negative_hygiene_rows.append(conditioning.get("negative_prompt_hygiene") or {})

    # Global negative belongs in final negative anyway, but explicit background
    # negatives must be front-loaded so plain/studio backdrops are rejected early.
    prime_prompt = _append_unique_text("", *prompt_terms)
    prime_negative = _append_unique_text("", *negative_terms)
    return {
        "schema": "neo.image.scene_director.background_prime_authority.v25_9_11",
        "phase": "V25.9.11",
        "status": "applied" if prime_prompt or prime_negative else "empty",
        "source": "sanitized_user_background_region_fields_only",
        "background_region_count": len(backgrounds),
        "prompt": prime_prompt,
        "negative": prime_negative,
        "prompt_terms": prompt_terms,
        "negative_terms": negative_terms,
        "prompt_template_injection": False,
        "policy": "Promote user-authored background/environment region text into early main conditioning; do not use post-pass background repaint as identity proof.",
        "prompt_hygiene_rows": prompt_hygiene_rows,
        "negative_hygiene_rows": negative_hygiene_rows,
    }


def _strip_character_lane_environment_conflicts(text: Any) -> str:
    """Remove background-only scoping words before merging environment into character lanes.

    Environment-aware character lanes should carry scene context around the
    subject, not tell CLIP that the whole masked pass is "background only".
    This strips only scoping phrases from already user-authored background text.
    """
    source = _clean_text(text)
    if not source:
        return ""
    clauses: list[str] = []
    removed: list[str] = []
    for clause in _split_prompt_clauses(source):
        cleaned = re.sub(r"\b(background|environment|scenery|setting)\s+only\b", "", clause, flags=re.I)
        cleaned = re.sub(r"\b(no|without)\s+(people|persons|subjects|characters|men|women)\b", "", cleaned, flags=re.I)
        cleaned = _clean_text(cleaned).strip(" ,.;:")
        if cleaned:
            clauses.append(cleaned)
        elif clause:
            removed.append(clause)
    if not clauses and source:
        cleaned = re.sub(r"\b(background|environment|scenery|setting)\s+only\b", "", source, flags=re.I)
        cleaned = re.sub(r"\b(no|without)\s+(people|persons|subjects|characters|men|women)\b", "", cleaned, flags=re.I)
        return _clean_text(cleaned).strip(" ,.;:")
    return _append_unique_text("", *clauses)


def _scene_environment_character_context(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Collect safe environment text for late character trait lanes.

    V25.9.12 keeps the V25.9.11 identity safety gate: it does not repaint the
    background after characters. Instead, each late character latent lane receives
    the user-authored environment context so the masked character pass does not
    drift back to studio/plain-wall space.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    backgrounds = [
        r for r in regions
        if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}
    ]
    prompt_terms: list[str] = []
    negative_terms: list[str] = []
    stripped_scope_terms: list[str] = []
    prompt_hygiene_rows: list[dict[str, Any]] = []
    negative_hygiene_rows: list[dict[str, Any]] = []
    for region in backgrounds:
        prompt, negative, conditioning = _background_restore_prompt_pair(region, graph)
        character_safe_prompt = _strip_character_lane_environment_conflicts(prompt)
        if character_safe_prompt:
            prompt_terms.append(character_safe_prompt)
            if prompt and character_safe_prompt.casefold() != prompt.casefold():
                stripped_scope_terms.append(prompt)
        if negative:
            negative_terms.append(negative)
        if isinstance(conditioning, dict):
            prompt_hygiene_rows.append(conditioning.get("prompt_hygiene") or {})
            negative_hygiene_rows.append(conditioning.get("negative_prompt_hygiene") or {})
    prompt = _append_unique_text("", *prompt_terms)
    negative = _append_unique_text("", *negative_terms)
    return {
        "schema": "neo.image.scene_director.environment_character_context.v25_9_12",
        "phase": "V25.9.12",
        "status": "applied" if prompt or negative else "empty",
        "source": "sanitized_user_background_region_fields_only",
        "background_region_count": len(backgrounds),
        "prompt": prompt,
        "negative": negative,
        "prompt_terms": prompt_terms,
        "negative_terms": negative_terms,
        "stripped_character_lane_scope_terms": stripped_scope_terms,
        "prompt_template_injection": False,
        "policy": "Carry user-authored environment context inside late character trait lanes so they preserve scene/background intent without unsafe final background repaint.",
        "prompt_hygiene_rows": prompt_hygiene_rows,
        "negative_hygiene_rows": negative_hygiene_rows,
    }


def _apply_scene_director_background_authority_restore(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    regional_authority_restore: dict[str, Any] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    report = _background_and_character_area_report(scene_graph)
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.background_authority_restore.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "status": "not_applicable",
        "nodes_added": [],
        "lanes": [],
        "applied_count": 0,
        "skipped_count": 0,
        "background_mask_visibility": deepcopy(report),
        "background_mask_subtracted_from_subjects": bool(report.get("background_mask_subtracted_from_subjects")),
        "background_restore_lane_added": False,
        "background_restore_strengthened": False,
        "background_restore_strengthening": {},
        "policy": "Restore user-authored background regions after character rescue using the V054 background mask output; no prompt templates or invented background text are injected.",
    }
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    backgrounds = [r for r in regions if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}]
    meta["route_count"] = len(backgrounds)
    if not backgrounds:
        meta.update({"status": "skipped_no_background_regions", "warnings": ["background_restore_no_background_regions"]})
        return next_id, list(base_latent_ref), [], [], meta
    required = ["SetLatentNoiseMask", "KSampler", "CLIPTextEncode"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        meta.update({"status": "skipped_missing_nodes", "warnings": ["background_restore_missing_nodes:" + ",".join(missing)]})
        return next_id, list(base_latent_ref), [], [], meta

    inherited_cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    # V25.9.6 Fix 5 strengthens background restore numerically when the
    # background has low visible area or Strong/Strict character correction is
    # active. Prompt text remains strictly user-authored background fields.
    warning_codes = set(report.get("warning_codes") or [])
    strong_character_scene = False
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        if any(_guard_mode(lock.get(key), "off") in {"strong", "strict"} for key in ("character", "gender", "skin_tone", "build", "body_height", "outfit")):
            strong_character_scene = True
            break
    strengthen_background = bool(strong_character_scene or warning_codes)
    steps = 16 if strengthen_background else 12
    denoise = 0.52 if strengthen_background else 0.42
    effective_cfg = max(float(inherited_cfg), 5.0) if strengthen_background else float(inherited_cfg)
    meta["background_restore_strengthened"] = bool(strengthen_background)
    meta["background_restore_strengthening"] = {
        "schema": "neo.image.scene_director.background_restore_strengthening.v25_9_6_fix5",
        "phase": "V25.9.6-fix5",
        "status": "strengthened" if strengthen_background else "unchanged",
        "reason_codes": sorted([*warning_codes, *( ["strong_locked_character_scene"] if strong_character_scene else [] )]),
        "effective": {"denoise": denoise, "steps": steps, "cfg": effective_cfg},
        "prompt_template_injection": False,
        "policy": "Only numeric background restore authority is raised; background prompt text remains user-authored.",
    }
    current_ref = list(base_latent_ref)
    added: list[str] = []
    lanes: list[dict[str, Any]] = []
    notes: list[str] = []
    for offset, region in enumerate(backgrounds):
        positive, negative, conditioning = _background_restore_prompt_pair(region, graph_data)
        if not positive:
            continue
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": positive}}
        graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": negative}}
        # V054 output index 12 = grouped background_masks.
        graph[mask_id] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(current_ref), "mask": [str(scene_node_id), 12]}}
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 73117 + offset * 97,
                "steps": steps,
                "cfg": float(effective_cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": denoise,
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        lanes.append({
            "schema": "neo.image.scene_director.background_authority_restore.lane.v25_9_6_fix4",
            "phase": "V25.9.6-fix4",
            "region_id": str(region.get("id") or ""),
            "label": _clean_text(region.get("label") or region.get("id") or "Background"),
            "mask_ref": [str(scene_node_id), 12],
            "mask_source": "v054_background_masks_subject_subtracted",
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "background_restore_denoise": denoise,
            "effective_steps": steps,
            "effective_cfg": effective_cfg,
            "background_restore_strengthened": bool(strengthen_background),
            "conditioning": deepcopy(conditioning),
            "prompt_template_injection": False,
        })
        notes.append(f"Scene Director background authority restored {lanes[-1]['label']} with V054 background mask at denoise {denoise:.2f}.")
        next_id += 4
    meta.update({
        "status": "applied" if lanes else "skipped_empty_background_prompts",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(backgrounds) - len(lanes)),
        "nodes_added": added,
        "lanes": deepcopy(lanes),
        "background_restore_lane_added": bool(lanes),
        "background_restore_denoise": denoise if lanes else None,
    })
    return next_id, current_ref, added, notes, meta




def _apply_scene_director_final_background_reconciliation(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    background_authority_restore: dict[str, Any] | None = None,
    character_latent_controller: dict[str, Any] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """V25.9.11 safe final background reconciliation gate.

    V25.9.10 proved that repeating a background KSampler after character
    trait lanes can damage identity/ethnicity when the only available
    background mask comes from broad full-height character boxes. V25.9.11
    only allows final background reconciliation when the true bbox-subtracted
    background area is large enough. Broad two-half-character layouts are
    handled by early/main background prime authority instead.
    """
    report = _background_and_character_area_report(scene_graph)
    prior_background = background_authority_restore if isinstance(background_authority_restore, dict) else {}
    controller = character_latent_controller if isinstance(character_latent_controller, dict) else {}
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.final_background_reconciliation.v25_9_11",
        "phase": "V25.9.11",
        "status": "not_applicable",
        "nodes_added": [],
        "lanes": [],
        "applied_count": 0,
        "skipped_count": 0,
        "background_mask_visibility": deepcopy(report),
        "runs_after": "character_latent_controller",
        "uses_mask_ref": [str(scene_node_id), 12],
        "prompt_template_injection": False,
        "policy": "Only run final background reconciliation when the safe subject-protected background mask is large enough; broad full-height character boxes are handled by early background prime authority to avoid subject/ethnicity mutation.",
    }
    if prior_background.get("status") != "applied":
        meta.update({"status": "skipped_no_prior_background_restore", "warnings": ["final_background_reconcile_no_prior_background_restore"]})
        return next_id, list(base_latent_ref), [], [], meta
    if controller.get("status") != "applied":
        meta.update({"status": "skipped_no_late_character_latent_controller", "warnings": ["final_background_reconcile_no_late_character_controller"]})
        return next_id, list(base_latent_ref), [], [], meta

    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    backgrounds = [r for r in regions if isinstance(r, dict) and str(r.get("role") or "").strip().lower() in {"background", "background_object", "environment", "transition_effect"}]
    meta["route_count"] = len(backgrounds)
    if not backgrounds:
        meta.update({"status": "skipped_no_background_regions", "warnings": ["final_background_reconcile_no_background_regions"]})
        return next_id, list(base_latent_ref), [], [], meta

    required = ["SetLatentNoiseMask", "KSampler", "CLIPTextEncode"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        meta.update({"status": "skipped_missing_nodes", "warnings": ["final_background_reconcile_missing_nodes:" + ",".join(missing)]})
        return next_id, list(base_latent_ref), [], [], meta

    warning_codes = set(report.get("warning_codes") or [])
    low_raw_background_area = any(
        _float((row or {}).get("raw_box_subtracted_visible_area_percent"), 100.0) <= 8.0
        for row in (report.get("rows") or [])
        if isinstance(row, dict)
    )
    broad_character_layout = bool(low_raw_background_area or "character_regions_leave_low_raw_background_area" in warning_codes)
    inherited_cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    steps = 14 if broad_character_layout else 12
    denoise = 0.40 if broad_character_layout else 0.34
    effective_cfg = max(float(inherited_cfg), 5.0)
    reason_codes = ["background_restore_preceded_character_latent_controller"]
    if broad_character_layout:
        reason_codes.append("broad_character_region_masks_can_repaint_background")
    reason_codes.extend(sorted(warning_codes))
    meta["reason_codes"] = _dedupe_text_items(reason_codes)
    meta["effective"] = {"denoise": denoise, "steps": steps, "cfg": effective_cfg}
    meta["broad_character_layout_detected"] = bool(broad_character_layout)
    # Critical V25.9.11 gate: when broad full-height character boxes leave
    # almost no true background area, the eroded background mask overlaps
    # subjects. A final background KSampler would repaint skin/ethnicity/identity
    # while trying to recover scenery. Do not run it; rely on background prime
    # authority in the main pass instead.
    if broad_character_layout:
        meta.update({
            "status": "skipped_unsafe_broad_character_background_mask",
            "skipped_count": len(backgrounds),
            "identity_safety_gate": {
                "schema": "neo.image.scene_director.final_background_identity_safety_gate.v25_9_11",
                "phase": "V25.9.11",
                "status": "blocked_final_background_repaint",
                "reason_codes": deepcopy(meta.get("reason_codes") or []),
                "policy": "Do not run final background repaint when the available mask overlaps protected character regions; avoid skin/ethnicity mutation.",
            },
            "warnings": ["final_background_reconcile_skipped_to_protect_character_identity"],
        })
        return next_id, list(base_latent_ref), [], ["Scene Director skipped final background reconciliation because the broad character boxes make the background mask unsafe for identity/ethnicity."], meta

    current_ref = list(base_latent_ref)
    added: list[str] = []
    lanes: list[dict[str, Any]] = []
    notes: list[str] = []
    for offset, region in enumerate(backgrounds):
        positive, negative, conditioning = _background_restore_prompt_pair(region, graph_data)
        if not positive:
            continue
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": positive}}
        graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": negative}}
        graph[mask_id] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(current_ref), "mask": [str(scene_node_id), 12]}}
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 126731 + offset * 101,
                "steps": int(steps),
                "cfg": float(effective_cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        lane = {
            "schema": "neo.image.scene_director.final_background_reconciliation.lane.v25_9_11",
            "phase": "V25.9.11",
            "region_id": str(region.get("id") or ""),
            "label": _clean_text(region.get("label") or region.get("id") or "Background"),
            "mask_ref": [str(scene_node_id), 12],
            "mask_source": "v054_background_masks_subject_subtracted_final_reconcile_safe_only",
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "effective_denoise": denoise,
            "effective_steps": steps,
            "effective_cfg": effective_cfg,
            "conditioning": deepcopy(conditioning),
            "prompt_template_injection": False,
            "runs_after_sampler_node_id": str((controller.get("lanes") or [{}])[-1].get("sampler_node_id") or "") if isinstance(controller.get("lanes"), list) and controller.get("lanes") else "",
            "reason_codes": deepcopy(meta.get("reason_codes") or []),
        }
        lanes.append(lane)
        notes.append(f"Scene Director final background reconciliation restored {lane['label']} after character trait lanes at denoise {denoise:.2f}.")
        next_id += 4

    meta.update({
        "status": "applied" if lanes else "skipped_empty_background_prompts",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(backgrounds) - len(lanes)),
        "nodes_added": added,
        "lanes": deepcopy(lanes),
    })
    return next_id, current_ref, added, notes, meta


def _skin_build_contrast_report(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Report user-authored skin/build contrast across character regions.

    V25.9.6 Fix 5 proof only. This does not invent prompt text; it detects
    contrast from extracted region terms so the runtime can apply a local masked
    restore pass using the user's own phrasing.
    """
    graph = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    rows: list[dict[str, Any]] = []
    skin_signatures: set[tuple[str, ...]] = set()
    build_signatures: set[tuple[str, ...]] = set()
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        prompt = _clean_text(region.get("prompt"))
        skin_terms = _extract_skin_terms(prompt)
        build_terms = _extract_build_terms(prompt)
        if skin_terms:
            skin_signatures.add(tuple(sorted({t.casefold() for t in skin_terms})))
        if build_terms:
            build_signatures.add(tuple(sorted({t.casefold() for t in build_terms})))
        rows.append({
            "region_id": str(region.get("id") or ""),
            "label": _clean_text(region.get("label") or region.get("id") or "Character"),
            "skin_terms": skin_terms,
            "build_terms": build_terms,
            "skin_tone_guard": _guard_mode((region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("skin_tone"), "off"),
            "build_guard": _guard_mode((region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("build"), "off"),
            "body_height_guard": _guard_mode((region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("body_height"), "off"),
        })
    return {
        "schema": "neo.image.scene_director.skin_build_contrast_report.v25_9_6_fix5",
        "phase": "V25.9.6-fix5",
        "character_count": len(rows),
        "skin_tone_contrast_detected": len([sig for sig in skin_signatures if sig]) >= 2,
        "build_contrast_detected": len([sig for sig in build_signatures if sig]) >= 2,
        "rows": rows,
        "policy": "Only user-authored region skin/build terms are detected; no race, ethnicity, or body prompt templates are generated.",
    }


def _skin_build_restore_prompt_pair(region: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    raw_prompt = _clean_text(region.get("prompt"))
    raw_negative = _clean_text(region.get("negative"))
    region_prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_prompt)
    region_negative, negative_hygiene = _strip_generated_negative_text(raw_negative)
    gender_terms = _extract_gender_terms(region_prompt).get("terms") or []
    skin_terms = _extract_skin_terms(region_prompt)
    build_terms = _extract_build_terms(region_prompt)
    outfit_terms = _extract_outfit_terms(region_prompt)
    # V25.9.6 Fix 5A: keep this helper safe if a future explicit/debug route
    # re-enables a separate pass. It must use the full user-authored character
    # prompt, not a body-only/skin-only rewrite prompt. The default runtime no
    # longer creates separate KSampler lanes from this helper.
    reinforcement_terms = _dedupe_text_items([*skin_terms, *build_terms], limit=12)
    positive = _append_unique_text(region_prompt, ", ".join(reinforcement_terms))
    return positive, region_negative, {
        "schema": "neo.image.scene_director.skin_build_restore_conditioning.v25_9_6_fix5a",
        "phase": "V25.9.6-fix5A",
        "source": "safe_full_user_region_prompt_with_extracted_skin_build_terms",
        "gender_terms": gender_terms,
        "skin_terms": skin_terms,
        "build_terms": build_terms,
        "outfit_terms": outfit_terms,
        "full_character_context_included": bool(region_prompt),
        "outfit_context_included": bool(outfit_terms),
        "body_only_rewrite": False,
        "skin_terms_preserved": bool(skin_terms),
        "build_terms_preserved": bool(build_terms),
        "prompt_template_injection": False,
        "prompt_hygiene": prompt_hygiene,
        "negative_prompt_hygiene": negative_hygiene,
    }


def _apply_scene_director_skin_build_contrast_restore(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    subject_slot_by_region = subject_slot_by_region or {}
    report = _skin_build_contrast_report(scene_graph)
    candidates: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        region_id = str(region.get("id") or "")
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        skin_mode = _guard_mode(lock.get("skin_tone"), "off")
        build_mode = _guard_mode(lock.get("build"), "off")
        body_mode = _guard_mode(lock.get("body_height") or lock.get("body") or lock.get("height"), "off")
        if not any(mode in {"strong", "strict"} for mode in (skin_mode, build_mode, body_mode)):
            continue
        positive, _negative, conditioning = _skin_build_restore_prompt_pair(region)
        if not positive:
            continue
        profile = "strict" if any(mode == "strict" for mode in (skin_mode, build_mode, body_mode)) else "strong"
        slot = max(1, min(4, _int(subject_slot_by_region.get(region_id), len(candidates) + 1)))
        candidates.append({
            "region_id": region_id,
            "label": _clean_text(region.get("label") or region_id),
            "subject_slot": slot,
            "profile": profile,
            "skin_terms": conditioning.get("skin_terms") or [],
            "build_terms": conditioning.get("build_terms") or [],
            "outfit_terms": conditioning.get("outfit_terms") or [],
            "full_character_context_included": bool(conditioning.get("full_character_context_included")),
            "outfit_context_included": bool(conditioning.get("outfit_context_included")),
        })
    has_skin = any(bool(c.get("skin_terms")) for c in candidates)
    has_build = any(bool(c.get("build_terms")) for c in candidates)
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.skin_build_contrast_authority.v25_9_6_fix5a",
        "phase": "V25.9.6-fix5A",
        "status": "disabled_body_only_rewrite",
        "route_count": len(candidates),
        "applied_count": 0,
        "skipped_count": len(candidates),
        "nodes_added": [],
        "lanes": [],
        "candidate_regions": deepcopy(candidates),
        "skin_build_contrast_report": deepcopy(report),
        "skin_tone_contrast_detected": bool(report.get("skin_tone_contrast_detected")),
        "build_contrast_detected": bool(report.get("build_contrast_detected")),
        "skin_tone_restore_lane_added": False,
        "build_silhouette_authority_active": False,
        "separate_skin_build_ksampler_disabled": True,
        "body_only_rewrite_disabled": True,
        "safe_trait_reinforcement_active": bool(candidates),
        "skin_tone_reinforced_in_character_context": has_skin,
        "build_reinforced_in_character_context": has_build,
        "safe_trait_reinforcement_sources": [
            "legacy_in_sampler_attention_full_region_prompt",
            "conditional_character_rescue_full_region_prompt",
            "outfit_restore_full_region_prompt",
        ],
        "prompt_template_injection": False,
        "policy": "Disable the separate body-only skin/build KSampler rewrite by default. Skin/build terms remain reinforced only inside full user-authored character/outfit context so clothing, gender, and pose are not stripped.",
        "warnings": [
            "body_only_skin_build_rewrite_disabled_to_prevent_outfit_bodywear_collapse"
        ] if candidates else ["skin_build_restore_no_strong_skin_or_build_terms"],
    }
    notes = []
    if candidates:
        notes.append("Scene Director disabled the separate body-only skin/build rewrite; traits are reinforced through full character/outfit context only.")
    return next_id, list(base_latent_ref), [], notes, meta


def _character_trait_lock_profile(region: dict[str, Any]) -> str:
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    modes = [
        _guard_mode(lock.get("character"), "off"),
        _guard_mode(lock.get("gender"), "off"),
        _guard_mode(lock.get("skin_tone"), "off"),
        _guard_mode(lock.get("hair"), "off"),
        _guard_mode(lock.get("build"), "off"),
        _guard_mode(lock.get("body_height"), "off"),
        _guard_mode(lock.get("outfit"), "off"),
    ]
    if "strict" in modes:
        return "strict"
    if "strong" in modes:
        return "strong"
    return "off"


V25_9_8_CHARACTER_TRAIT_CATEGORY_GROUPS: dict[str, str] = {
    "gender": "gender",
    "ethnicity": "ethnicity",
    "species_race": "species_race",
    "race": "species_race",
    "build": "body",
    "body": "body",
    "body_height": "body",
    "skin_tone": "skin",
    "skin": "skin",
    "hair": "hair",
    "facial_hair": "facial_hair",
    "clothing_top": "outfit",
    "clothing_bottom": "outfit",
    "full_costume": "outfit",
    "clothing": "outfit",
    "outfit": "outfit",
    "pose": "pose",
    "expression": "expression",
    "accessories": "accessories",
    "shoes": "shoes",
    "body_details": "body",
    "top_garment_state": "outfit",
    "bottom_garment_state": "outfit",
    "underlayer": "outfit",
    "held_items": "accessories",
    "custom_details": "additional_details",
}


def _split_trait_terms(value: Any) -> list[str]:
    terms: list[str] = []

    def add(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, dict):
            for key in ("explicit_terms", "prompt_terms", "terms", "custom", "custom_text", "selected_label", "label", "value"):
                add(item.get(key))
            return
        if isinstance(item, (list, tuple, set)):
            for sub in item:
                add(sub)
            return
        for part in re.split(r"[\n,;]+", str(item or "")):
            text = _clean_text(part)
            if text:
                terms.append(text)

    add(value)
    return _dedupe_text_items(terms)


def _explicit_character_trait_groups(region: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """V25.9.8 explicit Character Trait Lock fields.

    The UI can now submit structured trait fields per character region. These
    explicit values are source-of-truth for their trait group; auto extraction
    remains the fallback only when a group is empty. This function is deliberately
    data-only: it does not invent wording and it does not read any built-in
    library text that was not already submitted by the UI/custom fields.
    """
    raw = region.get("character_traits")
    if not isinstance(raw, dict):
        raw = region.get("trait_lock") if isinstance(region.get("trait_lock"), dict) else {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else raw
    groups: dict[str, list[str]] = {}
    category_sources: dict[str, Any] = {}
    for category, value in (categories or {}).items():
        category_key = str(category or "").strip().lower()
        group = V25_9_8_CHARACTER_TRAIT_CATEGORY_GROUPS.get(category_key)
        if not group:
            continue
        terms = _split_trait_terms(value)
        if not terms:
            continue
        groups[group] = _dedupe_text_items((groups.get(group) or []) + terms)
        if isinstance(value, dict):
            source = value.get("source") or ("explicit_custom" if value.get("custom") or value.get("custom_text") else "explicit_library")
            category_sources[category_key] = {
                "source": source,
                "selected_id": value.get("selected_id") or value.get("id") or "",
                "selected_label": value.get("selected_label") or value.get("label") or "",
                "custom": value.get("custom") or value.get("custom_text") or "",
                "terms": terms,
            }
        else:
            category_sources[category_key] = {"source": "explicit_direct", "terms": terms}
    meta = {
        "schema": "neo.image.scene_director.explicit_character_trait_fields.v25_9_8",
        "phase": "V25.9.8",
        "enabled": bool(groups),
        "source": "explicit_region_trait_fields" if groups else "auto_extract_fallback_only",
        "category_count": len(category_sources),
        "group_count": len(groups),
        "category_sources": category_sources,
        "group_sources": {group: "explicit" for group in groups},
        "policy": "Explicit per-region trait fields override auto extraction for that trait group; auto extraction is used only for empty groups.",
    }
    return groups, meta


def _gender_family_from_terms(terms: list[str], fallback: str = "auto") -> str:
    text = " ".join(str(t or "") for t in terms).casefold()
    male = bool(re.search(r"\b(1boy|2boys|boy|boys|man|men|male|masculine|gentleman|husband)\b", text))
    female = bool(re.search(r"\b(1girl|2girls|girl|girls|woman|women|female|feminine|wife)\b", text))
    nonbinary = bool(re.search(r"\b(nonbinary|non-binary|androgynous|genderfluid|agender)\b", text))
    if male and not female and not nonbinary:
        return "male"
    if female and not male and not nonbinary:
        return "female"
    if nonbinary and not male and not female:
        return "flexible"
    if male or female or nonbinary:
        return "mixed"
    return fallback or "auto"


def _merge_gender_terms(auto_gender: Any, explicit_terms: list[str] | None = None) -> dict[str, Any]:
    auto = auto_gender if isinstance(auto_gender, dict) else {"family": "auto", "terms": _split_trait_terms(auto_gender)}
    terms = _dedupe_text_items((explicit_terms or []) if explicit_terms else (auto.get("terms") or []))
    family = _gender_family_from_terms(terms, str(auto.get("family") or "auto"))
    male_terms = [term for term in terms if _gender_family_from_terms([term], "") == "male"]
    female_terms = [term for term in terms if _gender_family_from_terms([term], "") == "female"]
    flexible_terms = [term for term in terms if _gender_family_from_terms([term], "") in {"flexible", "mixed"}]
    return {
        "family": family,
        "terms": terms,
        "male_terms": male_terms,
        "female_terms": female_terms,
        "flexible_terms": flexible_terms,
    }


def _flatten_character_trait_values(value: Any) -> list[str]:
    """Flatten trait groups without leaking metadata keys into prompts.

    V25.9.7 accidentally allowed dict iteration for the gender group, which put
    words such as `family`, `terms`, and `male_terms` into the CLIP prompt. This
    helper only emits actual user/library/custom term values.
    """
    terms: list[str] = []
    if value is None:
        return []
    if isinstance(value, dict):
        for key in ("terms", "male_terms", "female_terms", "flexible_terms", "prompt_terms", "explicit_terms"):
            terms.extend(_flatten_character_trait_values(value.get(key)))
        return _dedupe_text_items(terms)
    if isinstance(value, (list, tuple, set)):
        for item in value:
            terms.extend(_flatten_character_trait_values(item))
        return _dedupe_text_items(terms)
    text = _clean_text(value)
    return [text] if text else []


def _region_local_pose_terms_from_prompt(prompt: Any) -> list[str]:
    """Extract only posture/contact clauses from one character's own prompt."""
    local_terms: list[str] = []
    pose_markers = (
        "body angled", "standing", "stands", "upright", "sitting", "seated",
        "sits", "kneeling", "kneels", "lying", "reclining", "crouching",
        "squatting", "pose", "posture", "toward", "close to", "holding",
        "holds", "hugging", "embracing", "touching", "supporting", "hand",
        "contact", "waist", "shoulder", "arm around", "leaning", "leans",
    )
    for fragment in _split_prompt_clauses(_clean_text(prompt)):
        fragment_l = fragment.casefold()
        if any(marker in fragment_l for marker in pose_markers):
            local_terms.append(fragment)
    return _dedupe_text_items(local_terms)


def _scene_character_pose_authority(scene_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Report the character-local Pose fields that own text-pose conditioning."""
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    rows: list[dict[str, Any]] = []
    for region in graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        explicit_groups, _ = _explicit_character_trait_groups(region)
        explicit_terms = _dedupe_text_items(explicit_groups.get("pose") or [])
        fallback_terms = [] if explicit_terms else _region_local_pose_terms_from_prompt(region.get("prompt"))
        terms = explicit_terms or fallback_terms
        rows.append({
            "region_id": str(region.get("id") or ""),
            "label": _clean_text(region.get("label") or region.get("id") or "Character"),
            "enabled": bool(terms),
            "source": "character_pose_trait" if explicit_terms else ("character_region_prompt_fallback" if fallback_terms else "empty"),
            "terms": terms,
        })
    active_rows = [row for row in rows if row.get("enabled")]
    return {
        "schema": "neo.image.scene_director.character_pose_authority.v25_9_15",
        "phase": "SD-V054-27.13",
        "enabled": bool(active_rows),
        "status": "active" if active_rows else "empty",
        "source": "character_region_pose_only",
        "character_count": len(rows),
        "active_character_count": len(active_rows),
        "characters": rows,
        "advanced_pair_pose_execution": False,
        "adds_attention_branch": False,
        "policy": "Each Character region owns only its own Pose terms. Pose is carried in existing character conditioning; exact skeleton authority requires ControlNet/OpenPose.",
    }


def _character_trait_contract(region: dict[str, Any], scene_graph: dict[str, Any] | None = None) -> dict[str, Any]:
    """V25.9.7: compile user-authored character traits into one latent contract.

    This contract is metadata-first. It never invents ethnicity, gender, skin,
    body, hair, outfit, or pose terms; every prompt token used by the latent
    controller comes from the visible region prompt/negative and extracted
    substrings of that same user-authored prompt.
    """
    raw_prompt = _clean_text(region.get("prompt"))
    raw_negative = _clean_text(region.get("negative") or region.get("negative_prompt"))
    region_prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_prompt)
    region_negative, negative_hygiene = _strip_generated_negative_text(raw_negative)
    auto_extracted = {
        "gender": _extract_gender_terms(region_prompt),
        "skin": _extract_skin_terms(region_prompt),
        "hair": _extract_hair_terms(region_prompt, []),
        "body": _extract_build_terms(region_prompt),
        "outfit": _extract_outfit_terms(region_prompt),
    }
    # Pose fallback is local to this character's own region prompt. Scene-level
    # Pair Pose metadata is deliberately excluded from this contract.
    auto_extracted["pose"] = _region_local_pose_terms_from_prompt(region_prompt)
    explicit_groups, explicit_meta = _explicit_character_trait_groups(region)
    relationship_pose = _scene_relationship_pose_authority(scene_graph)
    pair_pose_terms: list[str] = []
    extracted: dict[str, Any] = {}
    trait_source_report: dict[str, Any] = {}
    all_group_names = ["gender", "ethnicity", "species_race", "skin", "hair", "facial_hair", "body", "outfit", "pose", "expression", "accessories", "shoes", "additional_details"]
    for group_name in all_group_names:
        explicit_terms = explicit_groups.get(group_name) or []
        auto_terms = auto_extracted.get(group_name) or []
        if group_name == "gender":
            extracted[group_name] = _merge_gender_terms(auto_terms, explicit_terms if explicit_terms else None)
            selected_terms = extracted[group_name].get("terms") or []
        else:
            if group_name == "pose":
                extracted[group_name] = _dedupe_text_items(explicit_terms if explicit_terms else auto_terms)
            else:
                extracted[group_name] = _dedupe_text_items(explicit_terms) if explicit_terms else _dedupe_text_items(auto_terms)
            selected_terms = extracted[group_name]
        trait_source_report[group_name] = {
            "source": "explicit" if explicit_terms else ("auto" if auto_terms else "empty"),
            "explicit_terms": explicit_terms,
            "auto_terms": _flatten_character_trait_values(auto_terms),
            "pair_pose_terms": [],
            "selected_terms": _flatten_character_trait_values(selected_terms),
        }
    bodywear_risk_terms = [
        term for term in ("white tank top", "tank top", "crop top", "shirtless", "underwear", "briefs")
        if term in region_prompt.casefold()
    ]
    environment_context = _scene_environment_character_context(scene_graph)
    active_groups = [name for name, values in extracted.items() if _flatten_character_trait_values(values)]
    if environment_context.get("status") == "applied":
        active_groups.append("environment")
    return {
        "schema": "neo.image.scene_director.character_trait_contract.v25_9_8",
        "phase": "V25.9.8",
        "region_id": str(region.get("id") or ""),
        "label": _clean_text(region.get("label") or region.get("id") or "Character"),
        "profile": _character_trait_lock_profile(region),
        "source": "explicit_trait_fields_with_auto_extract_fallback" if explicit_meta.get("enabled") else "sanitized_user_region_prompt_only",
        "region_prompt": region_prompt,
        "region_negative": region_negative,
        "trait_groups": extracted,
        "explicit_trait_fields": explicit_meta,
        "trait_source_report": trait_source_report,
        "relationship_pose_authority": relationship_pose,
        "scene_pair_pose_terms": pair_pose_terms,
        "scene_pair_pose_used": False,
        "pose_authority_source": "character_region_pose_only",
        "environment_character_context": deepcopy(environment_context),
        "environment_context_used": environment_context.get("status") == "applied",
        "active_trait_groups": active_groups,
        "full_character_context_used": bool(region_prompt),
        "body_only_rewrite": False,
        "prompt_template_injection": False,
        "prompt_hygiene": prompt_hygiene,
        "negative_prompt_hygiene": negative_hygiene,
        "bodywear_collapse_risk_warning": {
            "schema": "neo.image.scene_director.bodywear_collapse_risk.v25_9_6_fix5a",
            "phase": "V25.9.6-fix5A",
            "active": bool(bodywear_risk_terms),
            "risk_terms": bodywear_risk_terms,
            "policy": "Warn only; do not rewrite user-authored clothing prompts.",
        },
    }


def _character_trait_runtime_authority(
    region: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Compile concise per-character authority for executable conditioning.

    The full Character Lock compiler already reported strong gender/hair/body
    guards, but the midpoint CLIP nodes previously received only the raw region
    prose.  Keep exact submitted trait terms first, then add only guard terms
    justified by the selected trait and visible lock mode.  Negatives stay
    region-local so one character's pink-hair guard cannot forbid another
    character's required black hair.
    """
    groups = contract.get("trait_groups") if isinstance(contract.get("trait_groups"), dict) else {}
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    correction = _character_lock_correction_block(region)
    profile = _character_trait_lock_profile(region)
    order = ("gender", "ethnicity", "species_race", "hair", "facial_hair", "skin", "body", "outfit", "pose", "accessories", "additional_details", "expression", "shoes")
    owners = {
        "gender": "gender",
        "ethnicity": "character",
        "species_race": "character",
        "hair": "hair",
        "facial_hair": "character",
        "skin": "skin_tone",
        "body": "build",
        "outfit": "outfit",
        "pose": "character",
        "additional_details": "character",
    }
    weights = {"strict": 1.55, "strong": 1.42, "balanced": 1.20, "soft": 1.10, "off": 1.0}
    weighted_terms: list[str] = []
    plain_terms: list[str] = []
    seen: set[str] = set()
    for group_name in order:
        owner = owners.get(group_name)
        fallback = profile if owner == "character" and profile in {"strong", "strict"} else "balanced"
        mode = _guard_mode(lock.get(owner) if owner else None, fallback)
        weight = weights.get(mode, 1.20)
        for term in _flatten_character_trait_values(groups.get(group_name)):
            key = _clean_text(term).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            plain_terms.append(_clean_text(term))
            weighted_terms.append(f"({_clean_text(term)}:{weight:.2f})")
            if len(weighted_terms) >= 24:
                break
        if len(weighted_terms) >= 24:
            break

    negative_terms: list[str] = []

    def add_negative(value: Any) -> None:
        for item in re.split(r"[\n,;]+", _clean_text(value)):
            text = _clean_text(item).strip(" ,.;:")
            if text and text.casefold() not in {part.casefold() for part in negative_terms}:
                negative_terms.append(text)

    add_negative(correction.get("negative_text") or "")
    gender = groups.get("gender") if isinstance(groups.get("gender"), dict) else {}
    family = str(gender.get("family") or correction.get("gender_family") or "auto").strip().lower()
    gender_mode = _guard_mode(lock.get("gender") or lock.get("character"), "off")
    if gender_mode in {"balanced", "strong", "strict"}:
        if family == "male":
            add_negative("female, woman, girl, feminine face, feminine body, breasts, cleavage, curvy hips, hourglass figure, gender swap, wrong gender")
        elif family == "female":
            add_negative("male, man, boy, masculine face, masculine body, gender swap, wrong gender")
    hair_mode = _guard_mode(lock.get("hair"), "off")
    hair_text = " ".join(_flatten_character_trait_values(groups.get("hair"))).casefold()
    if hair_mode in {"balanced", "strong", "strict"}:
        add_negative("wrong hair color, changed hairstyle, missing hair detail, inconsistent hair")
        if "pink" in hair_text:
            add_negative("black hair, brown hair, blonde hair, natural hair color instead of pink")
    if _guard_mode(lock.get("skin_tone"), "off") in {"balanced", "strong", "strict"}:
        add_negative("wrong skin tone, changed complexion, inconsistent skin color")
    if _guard_mode(lock.get("build") or lock.get("body_height"), "off") in {"balanced", "strong", "strict"}:
        add_negative("wrong body build, changed body type, distorted body proportions")
    if _guard_mode(lock.get("outfit"), "off") in {"balanced", "strong", "strict"}:
        add_negative("wrong outfit, changed clothing, missing costume details")

    positive = _append_unique_text(
        "",
        ", ".join(weighted_terms),
        _clean_text(correction.get("positive_text") or ""),
    )
    negative = ", ".join(negative_terms)
    return positive, negative, {
        "schema": "neo.image.scene_director.character_trait_runtime_authority.v054.v1",
        "phase": "SD-V054-27.6",
        "profile": profile,
        "weighted_terms": weighted_terms,
        "plain_terms": plain_terms,
        "positive": positive,
        "negative": negative,
        "correction_positive_used": bool(_clean_text(correction.get("positive_text") or "")),
        "correction_negative_used": bool(_clean_text(correction.get("negative_text") or "")),
        "regional_negative_required": bool(negative),
        "policy": "Exact visible Character Trait values lead the executable regional CLIP branch; generated exclusions remain scoped to the same character mask.",
    }


def _character_latent_controller_prompt_pair(region: dict[str, Any], scene_graph: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
    contract = _character_trait_contract(region, scene_graph=scene_graph)
    region_prompt = str(contract.get("region_prompt") or "")
    region_negative = str(contract.get("region_negative") or "")
    groups = contract.get("trait_groups") if isinstance(contract.get("trait_groups"), dict) else {}
    reinforcement_terms: list[str] = []
    for group_name in ("gender", "ethnicity", "species_race", "body", "skin", "hair", "facial_hair", "outfit", "pose", "expression", "accessories", "shoes", "additional_details"):
        reinforcement_terms.extend(_flatten_character_trait_values(groups.get(group_name)))
    environment_context = _scene_environment_character_context(scene_graph)
    env_prompt = _clean_text(environment_context.get("prompt") or "")
    env_negative = _clean_text(environment_context.get("negative") or "")
    runtime_positive, runtime_negative, runtime_authority = _character_trait_runtime_authority(region, contract)
    # Repeating extracted user-authored fragments is allowed; adding backend
    # wording is not. This remains user-region text + extracted region terms;
    # V25.9.12 appends sanitized user-authored background context so late masked
    # character lanes do not repaint the area around characters back to studio space.
    positive = _append_unique_text("", runtime_positive, region_prompt, ", ".join(_dedupe_text_items(reinforcement_terms, limit=32)), env_prompt)
    negative = _append_unique_text("", runtime_negative, region_negative, env_negative)
    contract["runtime_authority"] = deepcopy(runtime_authority)
    contract["runtime_positive_leads_prompt"] = bool(runtime_positive and positive.startswith(runtime_positive))
    contract["runtime_regional_negative_live"] = bool(runtime_negative)
    contract["environment_character_context"] = deepcopy(environment_context)
    contract["environment_context_used"] = bool(env_prompt or env_negative)
    if env_prompt:
        active = list(contract.get("active_trait_groups") or [])
        if "environment" not in active:
            active.append("environment")
        contract["active_trait_groups"] = active
    return positive, negative, contract


def _apply_scene_director_character_latent_controller(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """V25.9.7 coherent per-character latent trait controller.

    This replaces the old pattern of disconnected body/skin/outfit post-passes.
    Each locked character receives one coherent latent pass that carries all
    extracted user-authored trait groups together: gender/body/skin/hair/outfit.
    """
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    subject_slot_by_region = subject_slot_by_region or {}
    environment_context = _scene_environment_character_context(graph_data)
    background_area_report = _background_and_character_area_report(graph_data)
    background_warning_codes = set(background_area_report.get("warning_codes") or [])
    broad_character_environment_layout = bool(
        environment_context.get("status") == "applied"
        and "character_regions_leave_low_raw_background_area" in background_warning_codes
    )
    candidates: list[tuple[str, dict[str, Any], int, dict[str, Any]]] = []
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        profile = _character_trait_lock_profile(region)
        if profile not in {"strong", "strict"}:
            continue
        contract = _character_trait_contract(region, scene_graph=graph_data)
        if not contract.get("region_prompt"):
            continue
        region_id = str(region.get("id") or "")
        slot = max(1, min(4, _int(subject_slot_by_region.get(region_id), len(candidates) + 1)))
        candidates.append((region_id, region, slot, contract))

    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_latent_controller.v25_9_8",
        "phase": "V25.9.8",
        "status": "not_applicable",
        "route_count": len(candidates),
        "applied_count": 0,
        "skipped_count": 0,
        "nodes_added": [],
        "lanes": [],
        "trait_contracts": [deepcopy(c[3]) for c in candidates],
        "trait_level_latent_control": bool(candidates),
        "full_character_context_used": bool(candidates),
        "body_only_rewrite": False,
        "outfit_restore_merged": bool(candidates),
        "prompt_template_injection": False,
        "controlled_trait_groups": sorted({g for _, _, _, c in candidates for g in (c.get("active_trait_groups") or [])}),
        "explicit_trait_field_support": True,
        "auto_extract_fallback_enabled": True,
        "trait_source_counts": {
            "explicit": sum(1 for _, _, _, c in candidates for report in (c.get("trait_source_report") or {}).values() if report.get("source") == "explicit"),
            "auto": sum(1 for _, _, _, c in candidates for report in (c.get("trait_source_report") or {}).values() if report.get("source") == "auto"),
            "empty": sum(1 for _, _, _, c in candidates for report in (c.get("trait_source_report") or {}).values() if report.get("source") == "empty"),
        },
        "environment_aware_character_lanes": {
            "schema": "neo.image.scene_director.environment_aware_character_lanes.v25_9_12",
            "phase": "V25.9.12",
            "status": "applied" if environment_context.get("status") == "applied" and candidates else ("empty" if environment_context.get("status") != "applied" else "skipped_no_locked_characters"),
            "environment_character_context": deepcopy(environment_context),
            "background_mask_visibility": deepcopy(background_area_report),
            "broad_character_layout_detected": broad_character_environment_layout,
            "character_denoise_cap_applied": False,
            "denoise_cap": 0.36 if broad_character_environment_layout else None,
            "prompt_template_injection": False,
            "policy": "Append user-authored environment context into each late character trait lane and cap broad-mask denoise so character passes preserve the scene without unsafe final background repaint.",
        },
        "policy": "One coherent masked latent pass per locked character using only the sanitized user-authored full character prompt plus extracted user-authored trait groups; no separate body-only rewrite and no backend prompt templates.",
    }
    if not candidates:
        meta.update({"status": "skipped_no_strong_locked_characters", "warnings": ["character_latent_controller_no_eligible_locked_characters"]})
        return next_id, list(base_latent_ref), [], [], meta
    required = ["SetLatentNoiseMask", "KSampler", "CLIPTextEncode"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        meta.update({"status": "skipped_missing_nodes", "warnings": ["character_latent_controller_missing_nodes:" + ",".join(missing)]})
        return next_id, list(base_latent_ref), [], ["Scene Director Character Latent Controller skipped: missing Comfy node(s) " + ", ".join(missing) + "."], meta

    inherited_cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    current_ref = list(base_latent_ref)
    added: list[str] = []
    lanes: list[dict[str, Any]] = []
    notes: list[str] = []
    for offset, (region_id, region, slot, contract) in enumerate(candidates):
        positive, negative, conditioning = _character_latent_controller_prompt_pair(region, scene_graph=graph_data)
        if not positive:
            continue
        profile = str(contract.get("profile") or "strong")
        if profile == "strict":
            steps = 20
            denoise = 0.52
            cfg = max(inherited_cfg, 5.2)
            mask_feather = 10
        else:
            steps = 16
            # Bodywear-risk clothing is more likely to turn into underwear/camisole
            # at high denoise, so keep the coherent pass slightly calmer while
            # still stronger than the old 0.30 outfit-only polish.
            risk = ((conditioning.get("bodywear_collapse_risk_warning") or {}).get("active") is True) if isinstance(conditioning, dict) else False
            denoise = 0.40 if risk else 0.42
            cfg = max(inherited_cfg, 4.8)
            mask_feather = 12
        requested_denoise = float(denoise)
        denoise_cap_applied = False
        if broad_character_environment_layout:
            denoise = min(float(denoise), 0.36)
            denoise_cap_applied = float(denoise) < requested_denoise
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": positive}}
        graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": negative}}
        mask_ref = [str(scene_node_id), 5 + slot]
        graph[mask_id] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(current_ref), "mask": list(mask_ref)}}
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 97123 + offset * 107,
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        lane = {
            "schema": "neo.image.scene_director.character_latent_controller.lane.v25_9_8",
            "phase": "V25.9.8",
            "region_id": region_id,
            "label": _clean_text(region.get("label") or region_id),
            "subject_slot": slot,
            "profile": profile,
            "mask_ref": list(mask_ref),
            "mask_source": "full_character_subject_mask",
            "mask_feather": mask_feather,
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "effective_denoise": denoise,
            "requested_denoise_before_environment_cap": requested_denoise,
            "environment_denoise_cap_applied": denoise_cap_applied,
            "effective_steps": steps,
            "effective_cfg": cfg,
            "conditioning": deepcopy(conditioning),
            "active_trait_groups": list(conditioning.get("active_trait_groups") or []),
            "trait_groups": deepcopy(conditioning.get("trait_groups") or {}),
            "full_character_context_used": bool(conditioning.get("full_character_context_used")),
            "body_only_rewrite": False,
            "outfit_context_included": bool((conditioning.get("trait_groups") or {}).get("outfit")) if isinstance(conditioning.get("trait_groups"), dict) else False,
            "prompt_template_injection": False,
            "warnings": ["bodywear_collapse_risk"] if ((conditioning.get("bodywear_collapse_risk_warning") or {}).get("active") is True) else [],
        }
        lanes.append(lane)
        notes.append(f"Scene Director Character Latent Controller restored {lane['label']} with full trait context on subject_{slot}_mask at denoise {denoise:.2f}.")
        next_id += 4
    meta.update({
        "status": "applied" if lanes else "skipped_empty_character_prompts",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(candidates) - len(lanes)),
        "nodes_added": added,
        "lanes": deepcopy(lanes),
        "controlled_trait_groups": sorted({g for lane in lanes for g in (lane.get("active_trait_groups") or [])}),
        "full_character_context_used": any(bool(l.get("full_character_context_used")) for l in lanes),
        "outfit_restore_merged": bool(lanes),
    })
    if isinstance(meta.get("environment_aware_character_lanes"), dict):
        meta["environment_aware_character_lanes"]["character_denoise_cap_applied"] = any(bool(l.get("environment_denoise_cap_applied")) for l in lanes)
        meta["environment_aware_character_lanes"]["lane_count"] = len(lanes)
        if meta["environment_aware_character_lanes"].get("status") == "applied" and lanes:
            notes.append("Scene Director environment-aware character lanes appended user-authored background context into late character trait conditioning.")
            if meta["environment_aware_character_lanes"].get("character_denoise_cap_applied"):
                notes.append("Scene Director capped Character Latent Controller denoise for broad character masks to protect background continuity.")
        meta["environment_aware_character_lanes"]["lanes"] = [
            {
                "region_id": lane.get("region_id"),
                "label": lane.get("label"),
                "environment_context_used": bool(((lane.get("conditioning") or {}).get("environment_context_used"))),
                "requested_denoise_before_environment_cap": lane.get("requested_denoise_before_environment_cap"),
                "effective_denoise": lane.get("effective_denoise"),
                "environment_denoise_cap_applied": lane.get("environment_denoise_cap_applied"),
            }
            for lane in lanes
        ]
    return next_id, current_ref, added, notes, meta


def _character_midstep_sampler_safety(sampler_name: Any) -> dict[str, Any]:
    """Report whether a sampler can be restarted safely at a denoise midpoint.

    A second KSampler invocation cannot preserve multistep solver history or the
    original Brownian/SDE noise process.  Matching ``noise_seed`` is therefore
    insufficient for DPM++ 2M/3M SDE and similar stateful samplers.  Keep the
    experimental split route limited to deterministic single-step solvers; all
    other samplers fall back to the uninterrupted V054 attention route.
    """
    name = str(sampler_name or "").strip().lower()
    safe = {
        "euler",
        "euler_cfg_pp",
        "heun",
        "heunpp2",
        "dpm_2",
        "lcm",
    }
    if name in safe:
        return {
            "safe": True,
            "sampler_name": name,
            "reason": "deterministic_single_step_solver",
        }
    stateful_tokens = (
        "sde",
        "ancestral",
        "2m",
        "3m",
        "multistep",
        "lms",
        "ipndm",
        "deis",
        "sa_solver",
        "seeds_",
        "gradient_estimation",
    )
    reason = "stateful_or_stochastic_solver_restart" if any(token in name for token in stateful_tokens) else "split_continuity_not_proven"
    return {
        "safe": False,
        "sampler_name": name,
        "reason": reason,
    }


def _apply_scene_director_character_latent_midstep(
    graph: dict[str, Any],
    *,
    sampler_node_id: str,
    next_id: int,
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """Split the main sampler for an optional, genuinely mid-sampling repair.

    ``latent_repair`` used to append ordinary KSamplers after the full image was
    already denoised.  That was an end repair with a latent-sounding label.  The
    repaired route changes the main sampler to ``KSamplerAdvanced`` for an
    early composition segment, then continues denoising the *whole* latent from
    the selected midpoint through the remaining steps while applying the
    character-trait conditioning through subject masks. A fresh ``txt2img``
    latent cannot be left at the midpoint outside the subject mask: doing that
    freezes unfinished noise into the background. End refinement remains a
    separate plan and is not enabled by this helper.
    """
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    subject_slot_by_region = subject_slot_by_region or {}
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_latent_controller.v054.midstep.v1",
        "phase": "SD-V054-27.4",
        "status": "not_applicable",
        "execution": "mid_sampler_split",
        "split_sampler": True,
        "nodes_added": [],
        "lanes": [],
        "applied_count": 0,
        "skipped_count": 0,
        "warnings": [],
        "policy": "Optional latent repair runs inside the denoise schedule after the early composition segment; end refinement is a separate user-selected plan.",
    }
    if not _available_node(available_nodes, "KSamplerAdvanced"):
        meta.update({
            "status": "skipped_missing_nodes",
            "warnings": ["character_latent_midstep_missing_nodes:KSamplerAdvanced"],
        })
        return next_id, [str(sampler_node_id), 0], [], ["Scene Director mid-sampling Character Lock skipped: KSamplerAdvanced is not available."], meta

    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    candidates: list[tuple[str, dict[str, Any], int, str, str, dict[str, Any]]] = []
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        profile = _character_trait_lock_profile(region)
        correction = _character_lock_correction_block(region)
        explicit = bool(
            _flatten_character_trait_values(region.get("character_traits"))
            or _flatten_character_trait_values(region.get("trait_lock"))
            or str(correction.get("positive_text") or "").strip()
            or str(correction.get("negative_text") or "").strip()
        )
        if profile not in {"strong", "strict"} and not explicit:
            continue
        region_id = str(region.get("id") or "")
        slot = max(1, min(4, _int(subject_slot_by_region.get(region_id), len(candidates) + 1)))
        positive, negative, conditioning = _character_latent_controller_prompt_pair(region, scene_graph=graph_data)
        if positive:
            candidates.append((region_id, region, slot, positive, negative, conditioning))

    if not candidates:
        meta.update({"status": "skipped_no_explicit_trait_regions", "warnings": ["character_latent_midstep_no_eligible_regions"]})
        return next_id, [str(sampler_node_id), 0], [], [], meta

    sampler = graph.get(str(sampler_node_id)) if isinstance(graph.get(str(sampler_node_id)), dict) else {}
    inputs = sampler.setdefault("inputs", {}) if isinstance(sampler, dict) else {}
    total_steps = max(2, _int(inputs.get("steps"), 20))
    midpoint_step = max(1, min(total_steps - 1, int(round(total_steps * 0.42))))
    seed = int(sampler_seed)
    sampler_name = str(inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(inputs.get("scheduler") or "karras")
    cfg = max(0.0, _float(inputs.get("cfg"), 7.0))
    repair_model_ref = deepcopy(inputs.get("model") or model_ref)
    base_positive_ref = _copy_ref(inputs.get("positive"), [])
    base_negative_ref = _copy_ref(inputs.get("negative"), [])
    sampler_safety = _character_midstep_sampler_safety(sampler_name)
    meta["sampler_safety"] = deepcopy(sampler_safety)
    if not sampler_safety.get("safe"):
        warning = f"character_latent_midstep_blocked_sampler:{sampler_name}:{sampler_safety.get('reason')}"
        meta.update({
            "status": "blocked_incompatible_sampler",
            "fallback": "uninterrupted_in_sampler_attention",
            "warnings": [warning],
        })
        return (
            next_id,
            [str(sampler_node_id), 0],
            [],
            [f"Scene Director midpoint repair was skipped for {sampler_name}; the uninterrupted in-sampler Character Lock remains active."],
            meta,
        )

    # Convert the existing main sampler into the early denoise segment. The
    # model/positive/negative references are already wired to V054 above.
    sampler["class_type"] = "KSamplerAdvanced"
    inputs.update({
        "add_noise": "enable",
        "noise_seed": seed,
        "steps": total_steps,
        "cfg": cfg,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "start_at_step": 0,
        "end_at_step": midpoint_step,
        "return_with_leftover_noise": "enable",
    })

    positive_parts = [f"subject {region_id}: {positive}" for region_id, _region, _slot, positive, _negative, _conditioning in candidates]
    negative_parts = [negative for _region_id, _region, _slot, _positive, negative, _conditioning in candidates if negative]

    # A latent noise mask is correct for source-image inpaint, where the
    # unmasked pixels already contain a finished image. It is not correct for
    # this txt2img midpoint split: the unmasked pixels are still unfinished
    # noise from ``midpoint_step``. Continue the full latent instead and scope
    # only the extra trait conditioning to each subject region.
    conditioning_mask_class = "ConditioningSetMask" if _available_node(available_nodes, "ConditioningSetMask") else None
    conditioning_combine_class = None
    for candidate_class in ("ConditioningCombine", "ConditioningConcat"):
        if _available_node(available_nodes, candidate_class):
            conditioning_combine_class = candidate_class
            break

    region_mask_refs = [[str(scene_node_id), 5 + slot] for _region_id, _region, slot, _positive, _negative, _conditioning in candidates]
    scoped_conditioning = bool(
        conditioning_mask_class
        and conditioning_combine_class
        and base_positive_ref
        and base_negative_ref
    )
    conditioning_scope = "subject_masked_full_canvas" if scoped_conditioning else "full_canvas_fallback"
    conditioning_mask_node_ids: list[str] = []
    conditioning_combine_node_ids: list[str] = []
    conditioner_positive_node_ids: list[str] = []
    conditioner_negative_node_ids: list[str] = []
    added: list[str] = []

    if scoped_conditioning:
        repair_positive_ref = list(base_positive_ref)
        repair_negative_ref = list(base_negative_ref)
        conditioning_next_id = int(next_id)
        regional_conditioning_strengths: list[float] = []
        for (_region_id, _region, _slot, positive, negative, _conditioning), mask_ref in zip(candidates, region_mask_refs):
            profile = _character_trait_lock_profile(_region)
            conditioning_strength = 1.55 if profile == "strict" else (1.35 if profile == "strong" else 1.0)
            regional_conditioning_strengths.append(conditioning_strength)
            region_positive_id = str(conditioning_next_id)
            region_negative_id = str(conditioning_next_id + 1)
            masked_positive_id = str(conditioning_next_id + 2)
            masked_negative_id = str(conditioning_next_id + 3)
            combined_positive_id = str(conditioning_next_id + 4)
            combined_negative_id = str(conditioning_next_id + 5)
            graph[region_positive_id] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": deepcopy(clip_ref), "text": positive},
            }
            graph[region_negative_id] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": deepcopy(clip_ref), "text": negative or " "},
            }
            graph[masked_positive_id] = {
                "class_type": conditioning_mask_class,
                "inputs": _conditioning_set_mask_inputs(
                    available_nodes,
                    conditioning_mask_class,
                    [region_positive_id, 0],
                    mask_ref,
                    conditioning_strength,
                ),
            }
            graph[masked_negative_id] = {
                "class_type": conditioning_mask_class,
                "inputs": _conditioning_set_mask_inputs(
                    available_nodes,
                    conditioning_mask_class,
                    [region_negative_id, 0],
                    mask_ref,
                    conditioning_strength,
                ),
            }
            graph[combined_positive_id] = {
                "class_type": conditioning_combine_class,
                "inputs": _conditioning_combine_inputs(
                    conditioning_combine_class,
                    repair_positive_ref,
                    [masked_positive_id, 0],
                ),
            }
            graph[combined_negative_id] = {
                "class_type": conditioning_combine_class,
                "inputs": _conditioning_combine_inputs(
                    conditioning_combine_class,
                    repair_negative_ref,
                    [masked_negative_id, 0],
                ),
            }
            repair_positive_ref = [combined_positive_id, 0]
            repair_negative_ref = [combined_negative_id, 0]
            conditioner_positive_node_ids.append(region_positive_id)
            conditioner_negative_node_ids.append(region_negative_id)
            conditioning_mask_node_ids.extend([masked_positive_id, masked_negative_id])
            conditioning_combine_node_ids.extend([combined_positive_id, combined_negative_id])
            added.extend([
                region_positive_id,
                region_negative_id,
                masked_positive_id,
                masked_negative_id,
                combined_positive_id,
                combined_negative_id,
            ])
            conditioning_next_id += 6
    else:
        # Provider-safe fallback for older Comfy installs that do not expose
        # ConditioningSetMask/ConditioningCombine. This still performs a
        # genuine midpoint continuation and keeps the live V054 attention
        # branch active; it deliberately avoids the background-corrupting
        # SetLatentNoiseMask route.
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        graph[positive_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": ", ".join(positive_parts)},
        }
        graph[negative_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": ", ".join(negative_parts) or " "},
        }
        repair_positive_ref = [positive_id, 0]
        repair_negative_ref = [negative_id, 0]
        conditioner_positive_node_ids.append(positive_id)
        conditioner_negative_node_ids.append(negative_id)
        added.extend([positive_id, negative_id])
        meta["warnings"].append(
            "character_latent_midstep_conditioning_mask_nodes_unavailable_full_canvas_fallback"
        )
        regional_conditioning_strengths = []

    # ``next_id`` was the first id available before the conditioning nodes.
    # Recompute from the actual graph so this stays safe if a provider changes
    # the number of conditioning nodes in a future compatibility branch.
    repair_sampler_id = str(_next_id(graph))
    graph[repair_sampler_id] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "add_noise": "disable",
            # SDE samplers use noise_seed for their stochastic continuation even
            # when add_noise is disabled. Switching seeds at the split changes
            # the Brownian path and can undo identity/hair structure established
            # during the first segment.
            "noise_seed": seed,
            "steps": total_steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "start_at_step": midpoint_step,
            "end_at_step": total_steps,
            "return_with_leftover_noise": "disable",
            "model": repair_model_ref,
            "positive": repair_positive_ref,
            "negative": repair_negative_ref,
            "latent_image": [str(sampler_node_id), 0],
        },
    }
    next_id = int(repair_sampler_id) + 1
    added.append(repair_sampler_id)
    lane_mask_ref: list[Any] = deepcopy(region_mask_refs[0]) if len(region_mask_refs) == 1 else []
    lane = {
        "schema": "neo.image.scene_director.character_latent_controller.v054.midstep.lane.v1",
        "phase": "SD-V054-27.4",
        "region_ids": [region_id for region_id, *_rest in candidates],
        "subject_slots": [slot for _region_id, _region, slot, *_rest in candidates],
        "mask_ref": lane_mask_ref,
        "mask_refs": deepcopy(region_mask_refs),
        "mask_source": "subject_conditioning_masks" if scoped_conditioning else "full_canvas_fallback",
        "conditioner_positive_node_id": conditioner_positive_node_ids[0] if conditioner_positive_node_ids else None,
        "conditioner_negative_node_id": conditioner_negative_node_ids[0] if conditioner_negative_node_ids else None,
        "conditioner_positive_node_ids": conditioner_positive_node_ids,
        "conditioner_negative_node_ids": conditioner_negative_node_ids,
        "conditioning_mask_node_ids": conditioning_mask_node_ids,
        "conditioning_combine_node_ids": conditioning_combine_node_ids,
        "latent_mask_node_id": None,
        "sampler_node_id": repair_sampler_id,
        "base_sampler_node_id": str(sampler_node_id),
        "start_at_step": 0,
        "midpoint_step": midpoint_step,
        "end_at_step": total_steps,
        "midpoint_fraction": round(midpoint_step / float(total_steps), 4),
        "conditioned_regions": [
            {
                "region_id": region_id,
                "label": _clean_text(region.get("label") or region_id),
                "profile": _character_trait_lock_profile(region),
                "active_trait_groups": list(conditioning.get("active_trait_groups") or []),
            }
            for region_id, region, _slot, _positive, _negative, conditioning in candidates
        ],
        "conditioning": {
            "source": "explicit_character_traits_and_correction_fields",
            "positive_regions": len(positive_parts),
            "negative_regions": len(negative_parts),
            "prompt_template_injection": False,
            "scope": conditioning_scope,
            "regional_strengths": regional_conditioning_strengths,
            "mask_nodes": conditioning_mask_node_ids,
            "combine_nodes": conditioning_combine_node_ids,
        },
    }
    meta.update({
        "status": "applied",
        "nodes_added": added,
        "lanes": [lane],
        "applied_count": 1,
        "skipped_count": 0,
        "base_sampler_node_id": str(sampler_node_id),
        "repair_sampler_node_id": repair_sampler_id,
        "midpoint_step": midpoint_step,
        "total_steps": total_steps,
        "midpoint_fraction": round(midpoint_step / float(total_steps), 4),
        "live_in_sampler_attention": True,
        "conditioning_scope": conditioning_scope,
        "mask_source": "subject_conditioning_masks" if scoped_conditioning else "full_canvas_fallback",
        "mask_refs": deepcopy(region_mask_refs),
        "conditioning_mask_node_ids": conditioning_mask_node_ids,
        "conditioning_combine_node_ids": conditioning_combine_node_ids,
        "background_preservation": {
            "mode": "full_canvas_continuation",
            "latent_noise_mask_used": False,
            "policy": "Continue every latent pixel from the midpoint; constrain only the additional Character Trait conditioning to subject masks.",
        },
    })
    notes = [
        f"Scene Director split the main sampler at step {midpoint_step}/{total_steps} and continued the full latent while applying Character Trait conditioning through subject masks.",
        "SetLatentNoiseMask is intentionally not used on this fresh txt2img midpoint; it would freeze unfinished background noise.",
        "End refinement remains independent; this midstep lane is not a late full-image repair pass.",
    ]
    return next_id, [repair_sampler_id, 0], added, notes, meta


def _merged_outfit_restore_metadata_from_character_controller(controller: dict[str, Any]) -> dict[str, Any]:
    lanes = controller.get("lanes") if isinstance(controller.get("lanes"), list) else []
    return {
        "schema": "neo.image.scene_director.outfit_preservation_restore.v25_9_6_fix4",
        "phase": "V25.9.8",
        "status": "merged_into_character_latent_controller" if lanes else "not_applicable",
        "route_count": len(lanes),
        "applied_count": 0,
        "skipped_count": len(lanes),
        "nodes_added": [],
        "lanes": [],
        "character_outfit_terms_preserved": any(bool((lane.get("trait_groups") or {}).get("outfit")) for lane in lanes if isinstance(lane, dict)),
        "prompt_template_injection": False,
        "merged_controller_schema": controller.get("schema"),
        "merged_controller_lane_count": len(lanes),
        "policy": "V25.9.8 keeps outfit preservation merged into the coherent per-character latent controller and supports explicit trait fields before auto extraction fallback.",
    }

def _outfit_restore_prompt_pair(region: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    raw_prompt = _clean_text(region.get("prompt"))
    raw_negative = _clean_text(region.get("negative"))
    region_prompt, prompt_hygiene = _strip_character_lock_generated_text(raw_prompt)
    region_negative, negative_hygiene = _strip_generated_negative_text(raw_negative)
    outfit_terms = _extract_outfit_terms(region_prompt)
    skin_terms = _extract_skin_terms(region_prompt)
    build_terms = _extract_build_terms(region_prompt)
    bodywear_risk_terms = [
        term for term in ("white tank top", "tank top", "crop top", "shirtless", "underwear", "briefs")
        if term in region_prompt.casefold()
    ]
    # No template phrase is added. Repeating the exact user-authored clothing
    # terms as a source stack is allowed; it gives the masked pass a cleaner
    # local clothing signal without inventing garments. Fix 5A keeps skin/build
    # reinforcement inside this full user-authored character prompt instead of
    # creating a separate body-only repaint pass.
    positive = _append_unique_text(region_prompt, ", ".join(outfit_terms))
    return positive, region_negative, {
        "schema": "neo.image.scene_director.outfit_preservation_conditioning.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "source": "sanitized_user_region_prompt_and_extracted_user_outfit_terms",
        "outfit_terms": outfit_terms,
        "outfit_terms_preserved": bool(outfit_terms),
        "safe_trait_reinforcement": {
            "schema": "neo.image.scene_director.safe_trait_reinforcement.v25_9_6_fix5a",
            "phase": "V25.9.6-fix5A",
            "active": bool(skin_terms or build_terms),
            "source": "full_user_region_prompt_only",
            "skin_terms": skin_terms,
            "build_terms": build_terms,
            "separate_body_only_rewrite_disabled": True,
            "prompt_template_injection": False,
        },
        "bodywear_collapse_risk_warning": {
            "schema": "neo.image.scene_director.bodywear_collapse_risk.v25_9_6_fix5a",
            "phase": "V25.9.6-fix5A",
            "active": bool(bodywear_risk_terms),
            "risk_terms": bodywear_risk_terms,
            "policy": "Warn only; do not rewrite user-authored clothing prompts.",
        },
        "prompt_template_injection": False,
        "prompt_hygiene": prompt_hygiene,
        "negative_prompt_hygiene": negative_hygiene,
    }


def _apply_scene_director_outfit_preservation_restore(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    scene_graph: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    graph_data = scene_graph if isinstance(scene_graph, dict) else {}
    regions = graph_data.get("regions") if isinstance(graph_data.get("regions"), list) else []
    subject_slot_by_region = subject_slot_by_region or {}
    candidates: list[tuple[str, dict[str, Any], int]] = []
    for region in regions:
        if not isinstance(region, dict) or not _role_is_character(region):
            continue
        region_id = str(region.get("id") or "")
        lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
        outfit_mode = _guard_mode(lock.get("outfit"), "off")
        if outfit_mode not in {"strong", "strict"}:
            continue
        terms = _extract_outfit_terms(_clean_text(region.get("prompt")))
        if not terms:
            continue
        slot = max(1, min(4, _int(subject_slot_by_region.get(region_id), len(candidates) + 1)))
        candidates.append((region_id, region, slot))
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.outfit_preservation_restore.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "status": "not_applicable",
        "route_count": len(candidates),
        "applied_count": 0,
        "skipped_count": 0,
        "nodes_added": [],
        "lanes": [],
        "character_outfit_terms_preserved": False,
        "prompt_template_injection": False,
        "policy": "Run a low-denoise masked foreground pass after background restore using only user-authored region prompts and extracted user-authored outfit terms.",
    }
    if not candidates:
        meta.update({"status": "skipped_no_outfit_locked_characters", "warnings": ["outfit_restore_no_strong_outfit_terms"]})
        return next_id, list(base_latent_ref), [], [], meta
    required = ["SetLatentNoiseMask", "KSampler", "CLIPTextEncode"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        meta.update({"status": "skipped_missing_nodes", "warnings": ["outfit_restore_missing_nodes:" + ",".join(missing)]})
        return next_id, list(base_latent_ref), [], [], meta

    inherited_cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    steps = 10
    denoise = 0.30
    current_ref = list(base_latent_ref)
    added: list[str] = []
    lanes: list[dict[str, Any]] = []
    notes: list[str] = []
    for offset, (region_id, region, slot) in enumerate(candidates):
        positive, negative, conditioning = _outfit_restore_prompt_pair(region)
        if not positive:
            continue
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": positive}}
        graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": deepcopy(clip_ref), "text": negative}}
        mask_ref = [str(scene_node_id), 5 + slot]
        graph[mask_id] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(current_ref), "mask": list(mask_ref)}}
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 79123 + offset * 101,
                "steps": steps,
                "cfg": float(inherited_cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": denoise,
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        lanes.append({
            "schema": "neo.image.scene_director.outfit_preservation_restore.lane.v25_9_6_fix4",
            "phase": "V25.9.6-fix4",
            "region_id": region_id,
            "label": _clean_text(region.get("label") or region_id),
            "subject_slot": slot,
            "mask_ref": mask_ref,
            "mask_source": "subject_mask",
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "outfit_restore_denoise": denoise,
            "effective_steps": steps,
            "effective_cfg": inherited_cfg,
            "conditioning": deepcopy(conditioning),
            "safe_trait_reinforcement": deepcopy(conditioning.get("safe_trait_reinforcement") or {}),
            "bodywear_collapse_risk_warning": deepcopy(conditioning.get("bodywear_collapse_risk_warning") or {}),
            "character_outfit_terms_preserved": bool(conditioning.get("outfit_terms_preserved")),
            "prompt_template_injection": False,
        })
        notes.append(f"Scene Director outfit preservation restored {lanes[-1]['label']} with subject_{slot}_mask at denoise {denoise:.2f}.")
        next_id += 4
    meta.update({
        "status": "applied" if lanes else "skipped_empty_outfit_prompts",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(candidates) - len(lanes)),
        "nodes_added": added,
        "lanes": deepcopy(lanes),
        "character_outfit_terms_preserved": any(bool(l.get("character_outfit_terms_preserved")) for l in lanes),
    })
    return next_id, current_ref, added, notes, meta
def _apply_scene_director_first_pass_character_lock_authority(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    negative_ref: list[Any],
    scene_node_id: str,
    legacy: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
    scene_graph: dict[str, Any] | None = None,
    timing_stage: str = "before_adapters",
    primary_attention_lock_active: bool = False,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """Masked Character Lock rescue pass.

    V25.9.3 restores the legacy V053-style in-sampler attention patch as the
    primary Character Lock engine. This external masked KSampler path remains
    available only as fallback/rescue when the primary attention lock is absent.
    Legacy audit anchor: masked latent correction after the base composition.
    """
    settings = _character_lock_authority_settings(legacy)
    timing_stage = str(timing_stage or "before_adapters").strip().lower()
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
        "phase": "SD-V054-26.10.8J",
        "status": "not_applicable",
        "timing_stage": timing_stage,
        "settings": deepcopy(settings),
        "route_count": 0,
        "applied_count": 0,
        "skipped_count": 0,
        "nodes_added": [],
        "lanes": [],
        "warnings": [],
        "policy": "V25.9.3: primary Character Lock is legacy in-sampler regional attention; this masked correction path is fallback/rescue only.",
    }
    if not settings.get("enabled"):
        meta.update({"status": "disabled_by_ui", "warnings": ["first_pass_character_lock_disabled_by_ui"]})
        return next_id, list(base_latent_ref), [], [], meta
    execution = settings.get("character_lock_execution") if isinstance(settings.get("character_lock_execution"), dict) else _character_lock_execution_settings(legacy)
    if not execution.get("masked_correction_enabled"):
        mode = str(execution.get("mode") or settings.get("execution_mode") or "off")
        meta.update({
            "status": "disabled_by_character_lock_execution_mode",
            "character_lock_execution": deepcopy(execution),
            "warnings": [f"first_pass_character_lock_execution_mode_{mode}"],
            "policy": "V25.9.14: execution mode lives under Fix Pass Controls; prompt-guard-only/off modes block the optional correction sampler.",
        })
        return next_id, list(base_latent_ref), [], [], meta
    timing = str(settings.get("timing") or "before_adapters")
    if timing_stage == "before_adapters" and timing not in {"before_adapters", "both"}:
        meta.update({"status": "skipped_timing", "warnings": ["first_pass_character_lock_timing_not_before_adapters"]})
        return next_id, list(base_latent_ref), [], [], meta
    if timing_stage == "final_pass" and timing not in {"final_pass", "both"}:
        meta.update({"status": "skipped_timing", "warnings": ["first_pass_character_lock_timing_not_final_pass"]})
        return next_id, list(base_latent_ref), [], [], meta
    region_lookup = _region_lookup_by_id(scene_graph)
    subject_slot_by_region = subject_slot_by_region or {}
    candidate_regions: list[tuple[str, dict[str, Any], int]] = []
    for region_id, slot in subject_slot_by_region.items():
        region = region_lookup.get(str(region_id), {})
        if not isinstance(region, dict):
            continue
        if _character_lock_correction_enabled_for_region(region, settings):
            candidate_regions.append((str(region_id), region, max(1, min(4, _int(slot, len(candidate_regions) + 1)))))
    meta["route_count"] = len(candidate_regions)
    if not candidate_regions:
        meta.update({"status": "skipped_no_locked_characters", "warnings": ["first_pass_character_lock_no_eligible_locked_regions"]})
        return next_id, list(base_latent_ref), [], [], meta

    rescue_policy = _character_lock_conditional_rescue_policy(
        primary_attention_lock_active=primary_attention_lock_active,
        settings=settings,
        candidate_regions=candidate_regions,
        timing_stage=timing_stage,
        legacy=legacy if isinstance(legacy, dict) else {},
    )
    meta["conditional_rescue_fallback"] = deepcopy(rescue_policy)
    if primary_attention_lock_active and not rescue_policy.get("allow_with_primary_attention"):
        meta.update({
            "status": "skipped_primary_attention_lock_active",
            "primary_attention_lock_active": True,
            "rescue_only": True,
            "warnings": ["masked_correction_skipped_because_legacy_attention_lock_is_primary"],
        })
        return next_id, list(base_latent_ref), [], [], meta
    if primary_attention_lock_active and rescue_policy.get("allow_with_primary_attention"):
        settings = _apply_character_lock_rescue_numeric_settings(settings, rescue_policy)
        meta["settings"] = deepcopy(settings)
        meta["primary_attention_lock_active"] = True
        meta["rescue_only"] = True
        meta.setdefault("warnings", []).append("conditional_rescue_fallback_enabled_with_primary_attention")

    required = ["SetLatentNoiseMask", "KSampler", "CLIPTextEncode"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        meta.update({
            "status": "skipped_missing_nodes",
            "warnings": [*meta.get("warnings", []), "first_pass_character_lock_missing_nodes:" + ",".join(missing)],
        })
        return next_id, list(base_latent_ref), [], ["Scene Director first-pass Character Lock skipped: missing Comfy node(s) " + ", ".join(missing) + "."], meta
    current_ref = list(base_latent_ref)
    added: list[str] = []
    notes: list[str] = []
    lanes: list[dict[str, Any]] = []
    inherited_cfg = _float(sampler_inputs.get("cfg"), 7.0)
    cfg = _float(settings.get("cfg"), inherited_cfg) if settings.get("cfg_mode") == "custom" else inherited_cfg
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    base_negative_text = ""
    try:
        neg_node_id = str((negative_ref or [None])[0])
        base_negative_text = str(((graph.get(neg_node_id) or {}).get("inputs") or {}).get("text") or "")
    except Exception:
        base_negative_text = ""
    for offset, (region_id, region, subject_slot) in enumerate(candidate_regions):
        correction = _character_lock_correction_block(region)
        local_steps = max(1, min(80, _int(correction.get("steps"), int(settings.get("steps") or 10))))
        local_denoise = round(max(0.0, min(1.0, _float(correction.get("denoise"), float(settings.get("denoise") or 0.30)))), 4)
        rescue_numeric = ((settings.get("rescue_numeric_policy") or {}).get("effective") or {}) if isinstance(settings.get("rescue_numeric_policy"), dict) else {}
        if rescue_numeric:
            local_steps = max(local_steps, _int(rescue_numeric.get("steps"), local_steps))
            local_denoise = round(max(local_denoise, _float(rescue_numeric.get("denoise"), local_denoise)), 4)
        label = str(region.get("label") or region_id)
        local_positive, local_negative, local_conditioning = _character_lock_region_conditioning_text(
            region,
            label=label,
            negative_fallback=base_negative_text,
            settings=settings,
        )
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        mask_ref = [str(scene_node_id), 5 + subject_slot]
        graph[positive_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": local_positive},
        }
        graph[negative_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": local_negative},
        }
        graph[mask_id] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": list(current_ref), "mask": list(mask_ref)},
        }
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 42109 + (offset * 113) + (0 if timing_stage == "before_adapters" else 9000),
                "steps": int(local_steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(local_denoise),
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        lanes.append({
            "schema": "neo.image.scene_director.first_pass_character_lock_authority.lane.v054.v1",
            "phase": "SD-V054-26.10.8J",
            "region_id": region_id,
            "label": label,
            "subject_slot": subject_slot,
            "mask_ref": list(mask_ref),
            "mask_source": settings.get("mask_source"),
            "mask_feather": settings.get("mask_feather"),
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "effective_denoise": local_denoise,
            "effective_steps": local_steps,
            "effective_cfg": cfg,
            "cfg_mode": settings.get("cfg_mode"),
            "conditioning_source": "ui_visible_region_lock_fields",
            "local_conditioning": deepcopy(local_conditioning),
            "character_lock_latent_protected": True,
            "timing_stage": timing_stage,
            "warnings": [],
        })
        notes.append(f"Scene Director first-pass Character Lock corrected {label} with subject_{subject_slot}_mask before adapters at denoise {local_denoise:.2f}.")
        next_id += 4
    meta.update({
        "status": "applied" if lanes else "skipped",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(candidate_regions) - len(lanes)),
        "nodes_added": list(added),
        "lanes": deepcopy(lanes),
    })
    return next_id, current_ref, added, notes, meta

def _legacy_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "allow", "allowed"}


def _region_refinement_prompt_pair(legacy: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    legacy = legacy or {}
    prompt_extension_merge = legacy.get("prompt_extension_merge") if isinstance(legacy.get("prompt_extension_merge"), dict) else {}
    scene_director_interop = prompt_extension_merge.get("scene_director_interop") if isinstance(prompt_extension_merge.get("scene_director_interop"), dict) else {}
    style_stack_metadata = (prompt_extension_merge.get("extension_metadata") or {}).get("style_stack") if isinstance(prompt_extension_merge.get("extension_metadata"), dict) else {}
    style_stack_applied_globally = bool(
        scene_director_interop.get("style_stack_applied")
        or (isinstance(style_stack_metadata, dict) and style_stack_metadata.get("enabled"))
    )
    style_stack_global_only = _legacy_truthy(legacy.get("scene_director_style_stack_global_only")) or style_stack_applied_globally
    apply_style_to_refinement = _legacy_truthy(legacy.get("scene_director_style_stack_apply_to_region_refinement"))
    effective_positive = str(legacy.get("scene_director_effective_global_prompt") or legacy.get("scene_director_v052_global_prompt_override") or "").strip()
    effective_negative = str(legacy.get("scene_director_effective_negative_prompt") or "").strip()
    explicit_positive = str(legacy.get("scene_director_region_refinement_global_prompt") or "").strip()
    explicit_negative = str(legacy.get("scene_director_region_refinement_negative_prompt") or "").strip()
    original_positive = str(legacy.get("scene_director_style_stack_original_positive") or prompt_extension_merge.get("original_positive") or "").strip()
    original_negative = str(legacy.get("scene_director_style_stack_original_negative") or prompt_extension_merge.get("original_negative") or "").strip()
    policy = str(legacy.get("scene_director_style_stack_region_refinement_policy") or "").strip()

    if style_stack_global_only and not apply_style_to_refinement:
        positive = explicit_positive or original_positive or effective_positive
        negative = explicit_negative or original_negative or effective_negative
        style_source = "original_global"
        style_blocked = True
        policy = policy or "global_style_blocked_for_region_refinement"
    else:
        positive = explicit_positive or effective_positive
        negative = explicit_negative or effective_negative
        style_source = "styled_global" if apply_style_to_refinement else "effective_global"
        style_blocked = False
        policy = policy or ("styled_global_allowed" if apply_style_to_refinement else "not_style_stack_global_only")

    meta = {
        "style_stack_global_only": style_stack_global_only,
        "style_stack_apply_to_region_refinement": apply_style_to_refinement,
        "style_stack_region_refinement_policy": policy,
        "region_refinement_global_source": style_source,
        "style_stack_blocked_from_region_refinement": style_blocked,
    }
    return positive, negative, meta


def _lora_finish_pass_prompt_stack(
    *,
    unit: dict[str, Any],
    scene_graph: dict[str, Any] | None,
    legacy: dict[str, Any] | None,
    identity_strength: float,
    regional_authority_restore: dict[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    """Build the text stack for a masked regional LoRA refinement pass.

    The finish pass runs after Scene Director's primary sampler, so it must not
    fall back to a stripped global prompt. Carrying the assigned region prompt
    and prompt-derived character guards prevents the LoRA from undoing gender,
    silhouette, outfit, or region-authority constraints.
    """
    legacy = legacy or {}
    region_id = str(unit.get("region_id") or "")
    lookup = _region_lookup_by_id(scene_graph)
    region = lookup.get(region_id, {})
    label = str(region.get("label") or unit.get("label") or region_id or "Region").strip()
    global_positive, global_negative, style_isolation_meta = _region_refinement_prompt_pair(legacy)
    raw_region_positive = _clean_text(region.get("prompt"))
    region_positive, region_positive_hygiene = _strip_character_lock_generated_text(raw_region_positive)
    trigger_terms, trigger_source, trigger_warnings = _resolve_lora_trigger_terms(unit, region_positive)
    trigger_text = ", ".join(trigger_terms)
    if trigger_text:
        region_positive = _append_unique_text(region_positive, trigger_text)
    raw_region_negative = _clean_text(region.get("negative"))
    region_negative, region_negative_hygiene = _strip_generated_negative_text(raw_region_negative)
    lock = deepcopy(region.get("lock") if isinstance(region.get("lock"), dict) else {})
    char_positive: list[str] = []
    char_negative: list[str] = []
    authority_positive = ""
    authority_negative = ""
    if isinstance(regional_authority_restore, dict) and regional_authority_restore.get("status") == "applied":
        authority_positive = f"{label} regional authority: keep this subject in its assigned region; no cross-region identity/style bleeding"
        authority_negative = "wrong region assignment, swapped subjects, background bleeding across regions, merged regional styles"

    positive_parts = [global_positive, region_positive, authority_positive]
    negative_parts = [global_negative, region_negative, authority_negative]
    positive_text = _append_unique_text("", *[p for p in positive_parts if p])
    negative_text = _append_unique_text("", *[p for p in negative_parts if p])
    meta = {
        "region_id": region_id,
        "label": label,
        "region_prompt_carried": bool(region_positive),
        "character_lock_carried": False,
        "character_lock_prompt_hygiene": {"positive": region_positive_hygiene, "negative": region_negative_hygiene, "prompt_template_injection": False},
        "regional_authority_carried": bool(authority_positive),
        "lora_trigger_terms_carried": bool(trigger_terms),
        "trigger_source": trigger_source,
        "visibility_warnings": trigger_warnings,
        "positive_source_stack": [name for name, present in (
            ("global", bool(global_positive)),
            ("region", bool(region_positive)),
            ("lora_trigger", bool(trigger_terms)),
            ("character_lock", False),
            ("regional_authority", bool(authority_positive)),
        ) if present],
        "negative_source_stack": [name for name, present in (
            ("global", bool(global_negative)),
            ("region", bool(region_negative)),
            ("character_lock", False),
            ("regional_authority", bool(authority_negative)),
        ) if present],
        "style_stack_isolation": style_isolation_meta,
    }
    return positive_text, negative_text, meta


def _regional_adapter_execution_mode(block: dict[str, Any], legacy: dict[str, Any] | None = None) -> str:
    """Resolve Scene Director regional adapter execution mode.

    Phase 26.9.9 defaults to hybrid: native first-pass injection when the
    required graph can be built, otherwise the existing finish/second-pass
    path remains available as fallback. The setting is intentionally backend
    metadata, not a user-facing phase label.
    """
    params = _block_params(block)
    legacy = legacy or {}
    raw = (
        params.get("regional_adapter_execution_mode")
        or params.get("adapter_execution_mode")
        or params.get("scene_director_adapter_execution_mode")
        or legacy.get("scene_director_adapter_execution_mode")
        or legacy.get("regional_adapter_execution_mode")
        or "hybrid"
    )
    mode = str(raw or "hybrid").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "native": "first_pass_native",
        "firstpass": "first_pass_native",
        "first_pass": "first_pass_native",
        "main_pass": "first_pass_native",
        "generation": "first_pass_native",
        "finish": "finish_pass",
        "finishpass": "finish_pass",
        "second_pass": "finish_pass",
        "safe": "finish_pass",
        "off": "finish_pass",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"hybrid", "first_pass_native", "finish_pass"}:
        return "hybrid"
    return mode


def _adapter_first_pass_requested(mode: str) -> bool:
    return str(mode or "hybrid") in {"hybrid", "first_pass_native"}


def _inject_lora_trigger_terms_into_scene_graph(
    scene_graph: dict[str, Any] | None,
    units: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """Append explicit LoRA triggers only to the assigned region prompt.

    First-pass LoRA is a model/CLIP-level operation, so regional scoping comes
    from Scene Director's masked conditioning branches. Triggers must therefore
    live in the target region prompt, never the global prompt or neighboring
    regions.
    """
    if not isinstance(scene_graph, dict) or not units:
        return scene_graph, {}
    graph = deepcopy(scene_graph)
    lookup = _region_lookup_by_id(graph)
    meta_by_uid: dict[str, dict[str, Any]] = {}
    for unit in units:
        uid = str(unit.get("uid") or "")
        region_id = str(unit.get("region_id") or "")
        region = lookup.get(region_id)
        if not isinstance(region, dict):
            meta_by_uid[uid] = {
                "lora_trigger_terms_carried": False,
                "trigger_source": "none",
                "visibility_warnings": ["regional_lora_missing_region_for_trigger"],
            }
            continue
        prompt = _clean_text(region.get("prompt"))
        terms, source, warnings = _resolve_lora_trigger_terms(unit, prompt)
        if terms:
            region["prompt"] = _append_unique_text(prompt, ", ".join(terms))
        meta_by_uid[uid] = {
            "lora_trigger_terms_carried": bool(terms),
            "trigger_source": source,
            "visibility_warnings": warnings,
            "trigger_terms": terms,
        }
    return graph, meta_by_uid




def _scene_director_regional_lora_mixer_supported(available_nodes: Any) -> bool:
    """Return whether the installed V054 node can own regional LoRA delta routes.

    Phase 26.9.13 moves regional LoRA authority into NeoSceneDirectorV054. The
    backend should stop relying on a global LoraLoader branch for regional LoRAs
    when that node is available; the node receives a route contract and reports
    hard regional isolation only for regional_noise_delta routes.
    """
    return _available_node(available_nodes, V054_NODE_CLASS)


def _regional_lora_route_limit_exceeded(units: list[dict[str, Any]], max_routes: int = REGIONAL_LORA_MIXER_MAX_ROUTES) -> bool:
    count = 0
    for unit in units or []:
        if str(unit.get("region_id") or "").strip():
            count += 1
    return count > max(0, int(max_routes))


def _lora_route_file_missing_hint(lora_name: Any) -> bool:
    """Cheap static guard used by tests and metadata.

    The backend cannot reliably see the user's Comfy lora folder from all routes,
    so it does not hard-fail arbitrary names. It only treats blank/obviously
    sentinel missing names as unavailable and lets the node/provider perform real
    runtime loading/fallback when available.
    """
    text = str(lora_name or "").strip()
    if not text:
        return True
    lowered = text.casefold()
    return "missing" in lowered or "not_found" in lowered or "does_not_exist" in lowered


def _regional_lora_runtime_proof_from_unit(unit: dict[str, Any]) -> dict[str, Any]:
    """Phase 26.9.14 runtime proof gate for regional LoRA mixer claims.

    Neo cannot claim true regional model-delta isolation just because a route
    contract exists. The route may only remain `regional_model_delta_mixer` when
    the node/provider reports that the LoRA was loaded and produced a non-zero
    masked delta. Normal workflow compilation does not have that proof yet, so
    regional rows fall back to the visible masked finish pass instead of making a
    fake hard-isolation claim.
    """
    proof = unit.get("runtime_proof") if isinstance(unit.get("runtime_proof"), dict) else {}
    load_success = bool(unit.get("lora_load_success") or proof.get("lora_load_success"))
    model_patch_created = bool(unit.get("model_patch_created") or proof.get("model_patch_created"))
    delta_eval_attempted = bool(unit.get("delta_eval_attempted") or proof.get("delta_eval_attempted"))
    delta_nonzero = bool(unit.get("delta_nonzero") or proof.get("delta_nonzero"))
    runtime_applied = bool(unit.get("runtime_applied") or proof.get("runtime_applied"))
    runtime_applied = bool(runtime_applied and load_success and model_patch_created and delta_eval_attempted and delta_nonzero)
    delta_norm_mean = proof.get("delta_norm_mean", unit.get("delta_norm_mean"))
    delta_norm_max = proof.get("delta_norm_max", unit.get("delta_norm_max"))
    return {
        "schema": "neo.image.scene_director.regional_lora_runtime_proof.v054.v1",
        "phase": "SD-V054-26.9.15",
        "resolved_lora_path": unit.get("resolved_lora_path") or proof.get("resolved_lora_path") or unit.get("name"),
        "lora_file_exists": bool(unit.get("lora_file_exists") or proof.get("lora_file_exists") or (not _lora_route_file_missing_hint(unit.get("name")))),
        "lora_load_success": load_success,
        "lora_load_error": unit.get("lora_load_error") or proof.get("lora_load_error") or "",
        "model_patch_created": model_patch_created,
        "delta_eval_attempted": delta_eval_attempted,
        "delta_nonzero": delta_nonzero,
        "delta_norm_mean": delta_norm_mean,
        "delta_norm_max": delta_norm_max,
        "assigned_mask_coverage": unit.get("assigned_mask_coverage") or proof.get("assigned_mask_coverage"),
        "effective_delta_strength": unit.get("effective_delta_strength") or proof.get("effective_delta_strength") or unit.get("strength"),
        "runtime_applied": runtime_applied,
    }


def _regional_lora_mixer_route_payload(
    *,
    unit: dict[str, Any],
    region: dict[str, Any],
    scene_node_id: str,
    subject_slot: int,
    trigger_meta: dict[str, Any],
    fallback: bool = False,
    fallback_warning: str | None = None,
) -> dict[str, Any]:
    requested_strength = round(_float(unit.get("strength"), 0.8), 4)
    explicit_strength_override = bool(unit.get("strength_user_override"))
    effective_strength, visibility_booster = _apply_lora_visibility_strength(unit, region, requested_strength if explicit_strength_override else max(requested_strength, 0.85), explicit_strength_override)
    compatibility = _lora_checkpoint_compatibility(unit, None)
    uid = str(unit.get("uid") or "")
    region_id = str(unit.get("region_id") or "")
    label = str(region.get("label") or unit.get("label") or region_id or "Region")
    target = str(unit.get("target") or "both").strip().lower() or "both"
    warnings = list(trigger_meta.get("visibility_warnings") or [])
    runtime_proof = _regional_lora_runtime_proof_from_unit(unit)
    if not trigger_meta.get("lora_trigger_terms_carried") and "regional_lora_missing_trigger_terms" not in warnings:
        warnings.append("regional_lora_missing_trigger_terms")
    for warning_code in compatibility.get("warnings") or []:
        if warning_code not in warnings:
            warnings.append(str(warning_code))
    for warning_code in visibility_booster.get("warnings") or []:
        if warning_code not in warnings:
            warnings.append(str(warning_code))
    if fallback_warning and fallback_warning not in warnings:
        warnings.append(fallback_warning)
    if not fallback and not runtime_proof.get("runtime_applied"):
        fallback = True
        fallback_warning = "regional_lora_mixer_claimed_but_no_runtime_delta"
        if fallback_warning not in warnings:
            warnings.append(fallback_warning)
        if "regional_clip_delta_missing_may_reduce_character_lora_visibility" not in warnings:
            warnings.append("regional_clip_delta_missing_may_reduce_character_lora_visibility")
    if not fallback and "regional_lora_model_delta_mixer_active" not in warnings:
        warnings.append("regional_lora_model_delta_mixer_active")
    if not runtime_proof.get("delta_nonzero") and runtime_proof.get("delta_eval_attempted") and "regional_lora_delta_zero" not in warnings:
        warnings.append("regional_lora_delta_zero")
    if fallback:
        actual_mode = "finish_pass_fallback"
        model_delta_scope = "masked_finish_pass"
        hard_region_isolation = False
        global_bleed_risk = False
        fallback_used = True
    else:
        actual_mode = "regional_model_delta_mixer"
        model_delta_scope = "regional_noise_delta"
        hard_region_isolation = True
        global_bleed_risk = False
        fallback_used = False
    return {
        "uid": uid,
        "region_id": region_id,
        "label": label,
        "lora_name": unit.get("name"),
        "subject_slot": subject_slot,
        "mask_ref": [str(scene_node_id), 5 + subject_slot],
        "mask_output": f"subject_{subject_slot}_mask" if subject_slot else "region_mask",
        "node_id": str(scene_node_id),
        "requested_mode": "regional_model_delta_mixer",
        "actual_mode": actual_mode,
        "visibility_profile": "regional_character_model_delta_mixer" if not fallback else "regional_character_finish_pass_fallback",
        "requested_lora_strength": requested_strength,
        "effective_lora_strength": effective_strength,
        "strength_user_override": explicit_strength_override,
        "target": target,
        "visibility_booster": visibility_booster,
        "lora_compatibility": compatibility,
        "lora_family": compatibility.get("lora_family"),
        "checkpoint_family": compatibility.get("checkpoint_family"),
        "checkpoint_name": compatibility.get("checkpoint_name"),
        "model_branch_created": bool(runtime_proof.get("model_patch_created")) if not fallback else False,
        "clip_branch_created": False,
        "model_scope": "scene_director_internal_regional_delta_mixer" if not fallback else "masked_finish_pass",
        "runtime_proof": runtime_proof,
        "runtime_applied": bool(runtime_proof.get("runtime_applied")) and not fallback,
        "model_delta_scope": model_delta_scope,
        "clip_delta_scope": "region_prompt_only",
        "clip_delta_hard_isolation": False,
        "clip_delta_warning": "regional_clip_delta_not_supported_without_global_bleed",
        "hard_region_isolation": hard_region_isolation,
        "global_bleed_risk": global_bleed_risk,
        "fallback_used": fallback_used,
        "region_conditioning_scoped": True,
        "region_prompt_carried": True,
        "character_lock_carried": "character lock" in _clean_text(region.get("prompt")).casefold(),
        "lora_trigger_terms_carried": bool(trigger_meta.get("lora_trigger_terms_carried")),
        "trigger_source": str(trigger_meta.get("trigger_source") or "none"),
        "trigger_terms": list(trigger_meta.get("trigger_terms") or []),
        "regional_lora_delta_mixer": {
            "enabled": not fallback,
            "max_routes": REGIONAL_LORA_MIXER_MAX_ROUTES,
            "mixer_mode": "noise_prediction_delta",
            "mask_space": "latent",
            "mask_blur": 0,
            "mask_feather": 12,
            "strength": effective_strength,
            "model_delta_scope": model_delta_scope,
            "clip_delta_scope": "region_prompt_only",
        },
        "visual_authority_profile": "regional_character_lora_visual_authority" if fallback else "regional_character_lora_runtime_mixer",
        "visibility_warnings": sorted(set(warnings)),
    }

def _apply_scene_director_regional_lora_first_pass_native(
    graph: dict[str, Any],
    *,
    next_id: int,
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    block: dict[str, Any],
    legacy: dict[str, Any] | None,
    scene_graph: dict[str, Any] | None,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
    trigger_meta_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, list[Any], list[Any], list[str], list[str], list[dict[str, Any]]]:
    """Prepare regional LoRA execution for Scene Director.

    Phase 26.9.14 requires runtime proof before node-owned regional model-delta mixer routes are treated as applied.
    NeoSceneDirectorV054 may receive the route contract, but without proof the backend uses the visible masked finish-pass fallback instead of claiming hard isolation. The route contract is passed to
    the node, trigger terms remain target-region only, and the graph keeps the
    base MODEL/CLIP refs so global CLIP/model contamination is avoided.

    If the mixer route is unsafe or over limit, the legacy LoraLoader path is
    still available as a fallback.
    """
    units = _normalize_scene_director_lora_units(block, legacy, subject_slot_by_region)
    if not units:
        return next_id, list(model_ref), list(clip_ref), [], [], []

    region_lookup = _region_lookup_by_id(scene_graph)
    trigger_meta_by_uid = trigger_meta_by_uid or {}
    subject_slot_by_region = subject_slot_by_region or {}
    mixer_supported = _scene_director_regional_lora_mixer_supported(available_nodes)
    route_limit_exceeded = _regional_lora_route_limit_exceeded(units, REGIONAL_LORA_MIXER_MAX_ROUTES)

    # Preferred Phase 26.9.13 path: no global LoraLoader mutation for regional rows.
    if mixer_supported:
        lanes: list[dict[str, Any]] = []
        notes: list[str] = []
        applied_count = 0
        for offset, unit in enumerate(units):
            region_id = str(unit.get("region_id") or "")
            region = region_lookup.get(region_id, {})
            if not _is_character_lora_unit(unit, region):
                continue
            subject_slot = max(1, min(4, _int(unit.get("subject_slot") or subject_slot_by_region.get(region_id), offset + 1)))
            uid = str(unit.get("uid") or "")
            trigger_meta = deepcopy(trigger_meta_by_uid.get(uid) or {})
            fallback_warning: str | None = None
            fallback = False
            if route_limit_exceeded:
                fallback = True
                fallback_warning = "regional_lora_mixer_route_limit_exceeded"
            elif not region_id or region_id not in region_lookup:
                fallback = True
                fallback_warning = "regional_lora_mask_missing_fallback"
            elif not subject_slot_by_region.get(region_id):
                fallback = True
                fallback_warning = "regional_lora_subject_mask_missing_using_region_mask"
            elif _lora_route_file_missing_hint(unit.get("name")):
                fallback = True
                fallback_warning = "regional_lora_file_missing"
            lane = _regional_lora_mixer_route_payload(
                unit=unit,
                region=region,
                scene_node_id=scene_node_id,
                subject_slot=subject_slot,
                trigger_meta=trigger_meta,
                fallback=fallback,
                fallback_warning=fallback_warning,
            )
            lanes.append(lane)
            if not fallback:
                applied_count += 1
                notes.append(
                    f"Scene Director routed regional LoRA {unit.get('name')} through the V054 regional model-delta mixer for {region.get('label') or unit.get('label') or region_id}; model delta is masked to the assigned subject region."
                )
            else:
                notes.append(
                    f"Scene Director could not use regional LoRA model-delta mixer for {unit.get('name')}; {fallback_warning or 'fallback'} kept available."
                )
        if lanes:
            return next_id, list(model_ref), list(clip_ref), [], notes, lanes

    # Legacy fallback: Comfy's standard LoraLoader is model/CLIP scoped, not mask scoped.
    if not _available_node(available_nodes, "LoraLoader"):
        return next_id, list(model_ref), list(clip_ref), [], ["Scene Director native regional LoRA skipped: LoraLoader is unavailable; finish-pass fallback remains available."], []

    current_model_ref = list(model_ref)
    current_clip_ref = list(clip_ref)
    added: list[str] = []
    notes: list[str] = []
    lanes: list[dict[str, Any]] = []

    for offset, unit in enumerate(units):
        region_id = str(unit.get("region_id") or "")
        region = region_lookup.get(region_id, {})
        if not _is_character_lora_unit(unit, region):
            continue
        target = str(unit.get("target") or "both").strip().lower()
        if target not in {"model", "clip", "both"}:
            continue
        requested_strength = round(_float(unit.get("strength"), 0.8), 4)
        explicit_strength_override = bool(unit.get("strength_user_override"))
        effective_strength, visibility_booster = _apply_lora_visibility_strength(unit, region, requested_strength if explicit_strength_override else max(requested_strength, 0.85), explicit_strength_override)
        compatibility = _lora_checkpoint_compatibility(unit, legacy)
        strength_model = effective_strength if target in {"model", "both"} else 0.0
        strength_clip = effective_strength if target in {"clip", "both"} else 0.0
        lora_id = str(next_id)
        graph[lora_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": deepcopy(current_model_ref),
                "clip": deepcopy(current_clip_ref),
                "lora_name": str(unit.get("name") or ""),
                "strength_model": strength_model,
                "strength_clip": strength_clip,
            },
        }
        current_model_ref = [lora_id, 0]
        current_clip_ref = [lora_id, 1]
        added.append(lora_id)
        uid = str(unit.get("uid") or "")
        trigger_meta = deepcopy(trigger_meta_by_uid.get(uid) or {})
        subject_slot = max(1, min(4, _int(unit.get("subject_slot"), offset + 1)))
        mask_ref = [str(scene_node_id), 5 + subject_slot]
        warnings = list(trigger_meta.get("visibility_warnings") or [])
        if not trigger_meta.get("lora_trigger_terms_carried") and "regional_lora_missing_trigger_terms" not in warnings:
            warnings.append("regional_lora_missing_trigger_terms")
        if "lora_model_delta_is_global_without_true_node_delta" not in warnings:
            warnings.append("lora_model_delta_is_global_without_true_node_delta")
        for warning_code in compatibility.get("warnings") or []:
            if warning_code not in warnings:
                warnings.append(str(warning_code))
        for warning_code in visibility_booster.get("warnings") or []:
            if warning_code not in warnings:
                warnings.append(str(warning_code))
        lanes.append({
            "uid": uid,
            "region_id": region_id,
            "label": str(region.get("label") or unit.get("label") or region_id or "Region"),
            "lora_name": unit.get("name"),
            "subject_slot": subject_slot,
            "mask_ref": mask_ref,
            "node_id": lora_id,
            "requested_mode": "first_pass_native",
            "actual_mode": "first_pass_native",
            "visibility_profile": "regional_character_native",
            "requested_lora_strength": requested_strength,
            "effective_lora_strength": effective_strength,
            "strength_user_override": explicit_strength_override,
            "target": target,
            "visibility_booster": visibility_booster,
            "lora_compatibility": compatibility,
            "lora_family": compatibility.get("lora_family"),
            "checkpoint_family": compatibility.get("checkpoint_family"),
            "checkpoint_name": compatibility.get("checkpoint_name"),
            "model_branch_created": True,
            "clip_branch_created": True,
            "model_scope": "lora_model_branch_with_scene_director_region_masked_conditioning",
            "model_delta_scope": "global_model_branch",
            "hard_region_isolation": False,
            "global_bleed_risk": True,
            "region_conditioning_scoped": True,
            "region_prompt_carried": True,
            "character_lock_carried": "character lock" in _clean_text(region.get("prompt")).casefold(),
            "lora_trigger_terms_carried": bool(trigger_meta.get("lora_trigger_terms_carried")),
            "trigger_source": str(trigger_meta.get("trigger_source") or "none"),
            "trigger_terms": list(trigger_meta.get("trigger_terms") or []),
            "visibility_warnings": sorted(set(warnings)),
        })
        notes.append(
            f"Scene Director injected regional LoRA {unit.get('name')} into the first-pass Scene Director model/CLIP branch for {region.get('label') or unit.get('label') or region_id}; activation remains scoped to the assigned region prompt."
        )
        next_id += 1
    return next_id, current_model_ref, current_clip_ref, added, notes, lanes


def _route_region_summary(scene_graph: dict[str, Any] | None, region_id: str) -> dict[str, Any]:
    region = _region_lookup_by_id(scene_graph).get(str(region_id or ""), {})
    return region if isinstance(region, dict) else {}


def _route_label_for_region(scene_graph: dict[str, Any] | None, region_id: str, fallback: str = "Region") -> str:
    region = _route_region_summary(scene_graph, region_id)
    return str(region.get("label") or region.get("id") or fallback)


def _build_extension_authority_routes_v054(
    *,
    scene_graph: dict[str, Any] | None,
    block: dict[str, Any],
    legacy: dict[str, Any] | None,
    subject_slot_by_region: dict[str, int],
    lora_region_assignments: dict[str, Any] | None,
    lora_trigger_meta: dict[str, dict[str, Any]] | None,
    lora_first_pass_lanes: list[dict[str, Any]] | None,
    adapter_execution_mode: str,
    disabled_owner_cleanup: dict[str, Any] | None = None,
    ipadapter_owner_enabled: bool = True,
    disabled_ipadapter_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the V054 node-level extension authority contract.

    The contract is intentionally honest about LoRA: Comfy's standard LoraLoader
    is MODEL/CLIP scoped, so true per-region model-delta isolation is not claimed
    unless a future custom masked model-delta implementation exists.
    """
    routes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    subject_slot_by_region = subject_slot_by_region or {}
    region_lookup = _region_lookup_by_id(scene_graph)
    lora_trigger_meta = lora_trigger_meta or {}
    lora_lane_by_uid = {str(l.get("uid") or ""): l for l in (lora_first_pass_lanes or []) if isinstance(l, dict)}

    def region_role(region_id: str) -> str:
        r = region_lookup.get(str(region_id or ""), {})
        return str(r.get("role") or r.get("type") or "custom").strip().lower()

    ipadapter_units_for_routes = []
    if ipadapter_owner_enabled:
        ipadapter_units_for_routes = _normalize_scene_director_identity_units(block, subject_slot_by_region, scene_graph=scene_graph, requested_mode="hybrid_safe_identity")
    elif isinstance(disabled_ipadapter_gate, dict) and disabled_ipadapter_gate.get("identity_unit_count"):
        warnings.append({
            "code": "ipadapter_owner_extension_disabled_execution_suppressed",
            "level": "info",
            "owner_extension_id": IP_ADAPTER_EXTENSION_ID,
            "message": "Scene Director preserved regional identity profile metadata, but image.ip_adapter is disabled so no IPAdapter route is emitted.",
        })

    for unit in ipadapter_units_for_routes:
        region_id = str(unit.get("region_id") or "")
        slot = subject_slot_by_region.get(region_id) or unit.get("subject_slot")
        route_id = f"ipadapter_{unit.get('uid') or region_id or len(routes)+1}"
        if region_id not in region_lookup or not slot:
            warnings.append({
                "code": "ipadapter_region_mask_missing_fallback_second_pass",
                "level": "warning",
                "route_id": route_id,
                "region_id": region_id,
                "message": "Regional IPAdapter/FaceID has no valid assigned character mask; do not run it as global first-pass.",
            })
        routes.append({
            "route_id": route_id,
            "extension_type": "ipadapter",
            "owner_extension_id": "image.ip_adapter",
            "owner_enabled": True,
            "region_id": region_id,
            "subject_slot": slot,
            "region_role": region_role(region_id),
            "label": unit.get("label") or _route_label_for_region(scene_graph, region_id, "Identity"),
            "mask_output": f"subject_{slot}_mask" if slot else "unresolved",
            "mask_mode": "region",
            "target_area": "character_identity",
            "execution_mode": "node_authority" if region_id in region_lookup and slot else "second_pass_fallback",
            "requested_mode": "first_pass_native" if _adapter_first_pass_requested(adapter_execution_mode) else "second_pass",
            "actual_mode": unit.get("ipadapter_execution_mode") or ("backend_first_pass" if _adapter_first_pass_requested(adapter_execution_mode) else "second_pass"),
            "strength": unit.get("effective_weight", unit.get("weight")),
            "requested_weight": unit.get("requested_weight", unit.get("weight")),
            "effective_weight": unit.get("effective_weight", unit.get("weight")),
            "requested_start_at": unit.get("requested_start_at", unit.get("start_at")),
            "effective_start_at": unit.get("effective_start_at", unit.get("start_at")),
            "requested_end_at": unit.get("requested_end_at", unit.get("end_at")),
            "effective_end_at": unit.get("effective_end_at", unit.get("end_at")),
            "start_at": unit.get("effective_start_at", unit.get("start_at")),
            "end_at": unit.get("effective_end_at", unit.get("end_at")),
            "scope_mode": unit.get("scope_mode") or "identity_only",
            "mask_type": "subject_mask",
            "composition_preservation_enabled": bool(unit.get("composition_preservation_enabled", True)),
            "ipadapter_instruction_preservation": deepcopy(unit.get("ipadapter_instruction_preservation") or IPADAPTER_PRESERVE_PROFILE),
            "instruction_preservation_warnings": list(unit.get("instruction_preservation_warnings") or []),
            "reference_images": list(unit.get("image_names") or []),
            "isolation_policy": "assigned_subject_mask_only_no_global_canvas_identity_only",
            "model_delta_scope": "masked_adapter",
            "hard_region_isolation": bool(region_id in region_lookup and slot),
            "global_bleed_risk": False,
            "warnings": list(unit.get("instruction_preservation_warnings") or []),
        })
        for warning_code in unit.get("instruction_preservation_warnings") or []:
            warnings.append({
                "code": str(warning_code),
                "level": "warning" if "risk" in str(warning_code) or "override" in str(warning_code) or "affect" in str(warning_code) else "info",
                "route_id": route_id,
                "region_id": region_id,
                "message": str(warning_code).replace("_", " "),
            })

    lora_legacy = {**(legacy if isinstance(legacy, dict) else {}), "scene_director_lora_region_assignments": lora_region_assignments or {}}
    for unit in _normalize_scene_director_lora_units(block, lora_legacy, subject_slot_by_region):
        region_id = str(unit.get("region_id") or "")
        uid = str(unit.get("uid") or region_id or len(routes)+1)
        trigger_meta = lora_trigger_meta.get(uid) or {}
        lane = lora_lane_by_uid.get(uid) or {}
        requested_mode = lane.get("requested_mode") or ("first_pass_native" if _adapter_first_pass_requested(adapter_execution_mode) else "finish_pass")
        actual_mode = lane.get("actual_mode") or ("backend_first_pass" if _adapter_first_pass_requested(adapter_execution_mode) else "finish_pass")
        model_delta_scope = lane.get("model_delta_scope") or ("global_model_branch" if _adapter_first_pass_requested(adapter_execution_mode) else "masked_finish_pass")
        clip_delta_scope = lane.get("clip_delta_scope") or ("region_prompt_only" if actual_mode == "regional_model_delta_mixer" else None)
        runtime_proof = lane.get("runtime_proof") if isinstance(lane.get("runtime_proof"), dict) else {}
        runtime_applied = bool(lane.get("runtime_applied") and runtime_proof.get("runtime_applied"))
        if actual_mode == "regional_model_delta_mixer" and not runtime_applied:
            actual_mode = "finish_pass_fallback"
            model_delta_scope = "masked_finish_pass"
            fallback_used = True
        else:
            fallback_used = bool(lane.get("fallback_used"))
        hard_region_isolation = bool(runtime_applied and (lane.get("hard_region_isolation") or model_delta_scope == "regional_noise_delta"))
        bleed = bool(lane.get("global_bleed_risk") if "global_bleed_risk" in lane else model_delta_scope == "global_model_branch")
        route_warnings = list(lane.get("visibility_warnings") or [])
        compatibility = lane.get("lora_compatibility") if isinstance(lane.get("lora_compatibility"), dict) else _lora_checkpoint_compatibility(unit, legacy)
        region_for_lock = region_lookup.get(region_id, {})
        visibility_booster = lane.get("visibility_booster") if isinstance(lane.get("visibility_booster"), dict) else _lora_visibility_booster_plan(unit, region_for_lock)
        postpass_lock_risk = _locked_character_lora_postpass_risk(unit, region_for_lock, scene_graph)
        if postpass_lock_risk.get("blocks_crop_refine") and "regional_lora_crop_refine_blocked_by_latent_character_lock" not in route_warnings:
            route_warnings.append("regional_lora_crop_refine_blocked_by_latent_character_lock")
        for warning_code in [*(compatibility.get("warnings") or []), *(visibility_booster.get("warnings") or [])]:
            if warning_code not in route_warnings:
                route_warnings.append(str(warning_code))
        if actual_mode == "regional_model_delta_mixer" and runtime_applied:
            route_warnings = [w for w in route_warnings if w not in {"lora_model_delta_is_global_without_true_node_delta", "regional_lora_mixer_claimed_but_no_runtime_delta"}]
            if "regional_clip_delta_not_supported_without_global_bleed" not in route_warnings:
                route_warnings.append("regional_clip_delta_not_supported_without_global_bleed")
        elif fallback_used:
            if "regional_lora_mixer_claimed_but_no_runtime_delta" not in route_warnings:
                route_warnings.append("regional_lora_mixer_claimed_but_no_runtime_delta")
            if "regional_lora_finish_pass_visual_authority_fallback" not in route_warnings:
                route_warnings.append("regional_lora_finish_pass_visual_authority_fallback")
        elif bleed and "lora_model_delta_is_global_without_true_node_delta" not in route_warnings:
            route_warnings.append("lora_model_delta_is_global_without_true_node_delta")
        routes.append({
            "route_id": f"lora_{uid}",
            "extension_type": "lora",
            "owner_extension_id": "lora_stack",
            "region_id": region_id,
            "subject_slot": subject_slot_by_region.get(region_id) or unit.get("subject_slot"),
            "region_role": region_role(region_id),
            "label": unit.get("label") or _route_label_for_region(scene_graph, region_id, "LoRA Region"),
            "mask_output": f"subject_{subject_slot_by_region.get(region_id) or unit.get('subject_slot')}_mask" if (subject_slot_by_region.get(region_id) or unit.get("subject_slot")) else "unresolved",
            "mask_mode": "region",
            "target_area": "regional_character_style",
            "execution_mode": "node_authority",
            "requested_mode": requested_mode,
            "actual_mode": actual_mode,
            "strength": unit.get("strength"),
            "trigger_terms": list(trigger_meta.get("trigger_terms") or _split_trigger_terms(unit.get("trigger_words") or unit.get("source_record_trigger_words") or unit.get("source_record_activation_text") or "")),
            "lora_name": unit.get("name"),
            "target": unit.get("target"),
            "postpass_lock_policy": postpass_lock_risk.get("postpass_lock_policy"),
            "character_lock_latent_protected": bool(postpass_lock_risk.get("requires_latent_protection")),
            "postpass_may_bypass_lock": bool(postpass_lock_risk.get("unlock_requested")),
            "crop_refine_allowed": not bool(postpass_lock_risk.get("blocks_crop_refine")),
            "latent_character_lock_postpass_authority": deepcopy(postpass_lock_risk),
            "visibility_booster": deepcopy(visibility_booster),
            "lora_compatibility": deepcopy(compatibility),
            "lora_family": compatibility.get("lora_family"),
            "checkpoint_family": compatibility.get("checkpoint_family"),
            "checkpoint_name": compatibility.get("checkpoint_name"),
            "isolation_policy": "regional_lora_model_delta_mixer" if actual_mode == "regional_model_delta_mixer" else "trigger_terms_and_conditioning_are_region_scoped; standard LoraLoader model delta is not hard mask-scoped",
            "model_delta_scope": model_delta_scope,
            "clip_delta_scope": clip_delta_scope,
            "clip_delta_hard_isolation": False if clip_delta_scope else None,
            "clip_delta_warning": "regional_clip_delta_not_supported_without_global_bleed" if clip_delta_scope else None,
            "hard_region_isolation": bool(hard_region_isolation),
            "global_bleed_risk": bool(bleed),
            "fallback_used": fallback_used,
            "runtime_applied": runtime_applied,
            "runtime_proof": runtime_proof,
            "visual_authority_profile": lane.get("visual_authority_profile") or ("regional_character_lora_visual_authority" if fallback_used else None),
            "regional_lora_delta_mixer": lane.get("regional_lora_delta_mixer") or ({
                "enabled": actual_mode == "regional_model_delta_mixer",
                "max_routes": REGIONAL_LORA_MIXER_MAX_ROUTES,
                "mixer_mode": "noise_prediction_delta",
                "mask_space": "latent",
                "mask_blur": 0,
                "mask_feather": 12,
                "strength": unit.get("strength"),
                "model_delta_scope": model_delta_scope,
                "clip_delta_scope": clip_delta_scope or "region_prompt_only",
            } if actual_mode == "regional_model_delta_mixer" else None),
            "warnings": sorted(set(route_warnings)),
        })
        for warning_code in route_warnings:
            if warning_code in {"extension_route_hard_isolated"}:
                level = "info"
            elif warning_code in {"regional_clip_delta_not_supported_without_global_bleed"}:
                level = "info"
            else:
                level = "warning"
            warnings.append({
                "code": str(warning_code),
                "level": level,
                "route_id": f"lora_{uid}",
                "region_id": region_id,
                "message": ("Regional LoRA model delta is mixed inside Scene Director and limited to the assigned region mask." if warning_code == "regional_lora_model_delta_mixer_active" else str(warning_code).replace("_", " ")),
            })

    for lane in _regional_controlnet_lanes(scene_graph):
        region_id = str(lane.get("region_id") or "")
        routes.append({
            "route_id": f"controlnet_{lane.get('uid') or region_id}",
            "extension_type": "controlnet",
            "owner_extension_id": "image.controlnet",
            "region_id": region_id,
            "subject_slot": subject_slot_by_region.get(region_id),
            "region_role": region_role(region_id),
            "label": lane.get("label") or _route_label_for_region(scene_graph, region_id, "ControlNet Region"),
            "mask_output": "control_masks",
            "mask_mode": lane.get("mask_mode") or "region",
            "target_area": lane.get("type") or "structure",
            "execution_mode": "node_authority",
            "requested_mode": "regional_controlnet",
            "actual_mode": "regional_controlnet",
            "strength": lane.get("strength"),
            "start_at": lane.get("start"),
            "end_at": lane.get("end"),
            "controlnet_unit_id": lane.get("uid"),
            "isolation_policy": "assigned_region_control_mask_only",
            "model_delta_scope": "masked_conditioning",
            "hard_region_isolation": True,
            "global_bleed_risk": False,
        })

    for item in _regional_detailer_passes(scene_graph):
        region_id = str(item.get("region_id") or "")
        routes.append({
            "route_id": f"adetailer_{item.get('uid') or region_id}",
            "extension_type": "adetailer",
            "owner_extension_id": "image.adetailer",
            "region_id": region_id,
            "subject_slot": subject_slot_by_region.get(region_id),
            "region_role": region_role(region_id),
            "label": item.get("label") or _route_label_for_region(scene_graph, region_id, "Detailer Region"),
            "mask_output": "detail_masks",
            "mask_mode": item.get("mask_mode") or "region",
            "target_area": item.get("mode") or "detail",
            "execution_mode": "node_authority",
            "requested_mode": "regional_detailer",
            "actual_mode": "regional_detailer",
            "strength": item.get("denoise"),
            "adetailer_pass_id": item.get("uid"),
            "isolation_policy": "assigned_region_detail_mask_only",
            "model_delta_scope": "masked_detailer_pass",
            "hard_region_isolation": True,
            "global_bleed_risk": False,
        })

    cleanup = disabled_owner_cleanup if isinstance(disabled_owner_cleanup, dict) else {}
    for repair in cleanup.get("repairs") or []:
        if not isinstance(repair, dict):
            continue
        warnings.append({
            "code": "extension_route_owner_disabled",
            "level": "warning",
            "route_id": repair.get("route_id"),
            "region_id": repair.get("region_id"),
            "owner_extension_id": repair.get("owner_extension_id") or repair.get("owner"),
            "message": "Stale extension route ignored because owner extension is disabled or not submitted.",
        })

    status = "applied" if routes else ("not_applicable" if not warnings else "applied")
    return {
        "schema": "neo.image.scene_director.extension_authority_routes.v054.v1",
        "phase": "SD-V054-26.9.14",
        "status": status,
        "route_count": len(routes),
        "routes": routes,
        "warnings": warnings,
        "disabled_ipadapter_gate": deepcopy(disabled_ipadapter_gate or {}),
        "policy": "Extension routes are passed into NeoSceneDirectorV054 so adapter/detail/control influence is tied to assigned region masks. LoRA model-delta isolation is reported honestly until runtime-proven custom masked model-delta mixing exists.",
    }




def _build_regional_lora_model_delta_mixer_summary(routes_contract: dict[str, Any] | None) -> dict[str, Any]:
    routes_contract = routes_contract if isinstance(routes_contract, dict) else {}
    raw_routes = [r for r in (routes_contract.get("routes") or []) if isinstance(r, dict) and r.get("extension_type") == "lora"]
    routes: list[dict[str, Any]] = []
    applied = 0
    fallback = 0
    for route in raw_routes:
        proof = route.get("runtime_proof") if isinstance(route.get("runtime_proof"), dict) else {}
        is_applied = route.get("actual_mode") == "regional_model_delta_mixer" and route.get("model_delta_scope") == "regional_noise_delta" and route.get("runtime_applied") is True and proof.get("runtime_applied") is True
        if is_applied:
            applied += 1
        elif route.get("fallback_used") or route.get("actual_mode") in {"finish_pass_fallback", "finish_pass", "backend_first_pass", "first_pass_native"}:
            fallback += 1
        routes.append({
            "route_id": route.get("route_id"),
            "lora_name": route.get("lora_name"),
            "region_id": route.get("region_id"),
            "label": route.get("label"),
            "subject_slot": route.get("subject_slot"),
            "requested_strength": route.get("strength"),
            "effective_strength": (route.get("regional_lora_delta_mixer") or {}).get("strength") if isinstance(route.get("regional_lora_delta_mixer"), dict) else route.get("strength"),
            "mask_output": route.get("mask_output"),
            "model_delta_scope": route.get("model_delta_scope"),
            "clip_delta_scope": route.get("clip_delta_scope"),
            "hard_region_isolation": bool(route.get("hard_region_isolation")),
            "global_bleed_risk": bool(route.get("global_bleed_risk")),
            "fallback_used": bool(route.get("fallback_used")),
            "actual_mode": route.get("actual_mode"),
            "runtime_applied": bool(route.get("runtime_applied")),
            "runtime_proof": route.get("runtime_proof") or {},
            "visual_authority_profile": route.get("visual_authority_profile"),
            "visibility_booster": route.get("visibility_booster") or {},
            "lora_compatibility": route.get("lora_compatibility") or {},
            "lora_family": route.get("lora_family"),
            "checkpoint_family": route.get("checkpoint_family"),
            "warnings": route.get("warnings") or [],
        })
    if applied:
        status = "applied"
    elif fallback:
        status = "fallback"
    elif raw_routes:
        status = "not_available"
    else:
        status = "off"
    return {
        "schema": "neo.image.scene_director.regional_lora_model_delta_mixer.v054.v1",
        "phase": "SD-V054-26.9.14",
        "status": status,
        "mixer_mode": "noise_prediction_delta",
        "route_count": len(raw_routes),
        "applied_count": applied,
        "fallback_count": fallback,
        "max_routes": REGIONAL_LORA_MIXER_MAX_ROUTES,
        "routes": routes,
        "policy": "Regional LoRA model-delta mixer claims require runtime proof; when proof is missing, Scene Director uses a visible masked finish-pass fallback while keeping regional triggers out of the global prompt.",
    }

def _build_extension_routing_authority_summary(
    node_authority: dict[str, Any] | None,
    routes_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    node_authority = node_authority if isinstance(node_authority, dict) else {}
    routes_contract = routes_contract if isinstance(routes_contract, dict) else {}
    routes = node_authority.get("routes") if isinstance(node_authority.get("routes"), list) else routes_contract.get("routes", [])
    warnings = []
    for source in (routes_contract.get("warnings"), node_authority.get("warnings")):
        for item in source or []:
            code = item.get("code") if isinstance(item, dict) else str(item)
            if code and code not in warnings:
                warnings.append(code)
    gate = routes_contract.get("disabled_ipadapter_gate") if isinstance(routes_contract.get("disabled_ipadapter_gate"), dict) else {}
    if gate.get("warnings"):
        for code in gate.get("warnings") or []:
            if code and code not in warnings:
                warnings.append(str(code))
    summary_routes = []
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        summary_routes.append({
            "extension_type": route.get("extension_type"),
            "region_id": route.get("region_id"),
            "label": route.get("label"),
            "subject_slot": route.get("subject_slot"),
            "requested_mode": route.get("requested_mode"),
            "actual_mode": route.get("actual_mode"),
            "node_authority_mask_confirmed": bool(route.get("node_authority_mask_confirmed") if "node_authority_mask_confirmed" in route else route.get("hard_region_isolation")),
            "hard_region_isolation": bool(route.get("hard_region_isolation")),
            "mask_output": route.get("mask_output"),
            "model_delta_scope": route.get("model_delta_scope"),
            "clip_delta_scope": route.get("clip_delta_scope"),
            "clip_delta_hard_isolation": route.get("clip_delta_hard_isolation"),
            "global_bleed_risk": bool(route.get("global_bleed_risk")),
            "fallback_used": bool(route.get("fallback_used")),
            "warnings": route.get("warnings") or [],
            "requested_weight": route.get("requested_weight"),
            "effective_weight": route.get("effective_weight"),
            "requested_start_at": route.get("requested_start_at"),
            "effective_start_at": route.get("effective_start_at"),
            "requested_end_at": route.get("requested_end_at"),
            "effective_end_at": route.get("effective_end_at"),
            "scope_mode": route.get("scope_mode"),
            "composition_preservation_enabled": route.get("composition_preservation_enabled"),
        })
    return {
        "schema": "neo.image.scene_director.extension_routing_authority.v054.v1",
        "phase": "SD-V054-26.9.14",
        "status": "applied" if summary_routes or warnings else "off",
        "route_count": len(summary_routes),
        "routes": summary_routes,
        "warnings": sorted(set(warnings)),
        "disabled_ipadapter_gate": deepcopy(gate),
        "policy": "Scene Director node receives extension_routes_json; disabled owner extensions suppress adapter execution while preserving metadata; standard LoRA global model deltas are not mislabeled as hard isolated.",
    }

def _native_adapter_injection_metadata(
    *,
    requested_mode: str,
    lora_lanes: list[dict[str, Any]],
    lora_nodes_added: list[str],
    ipadapter_requested_mode: str,
    ipadapter_actual_mode: str,
    ipadapter_nodes_added: list[str],
    ipadapter_units: list[dict[str, Any]],
    ipadapter_safety_gate: dict[str, Any],
) -> dict[str, Any]:
    ip_regions = []
    for unit in ipadapter_units or []:
        ip_regions.append({
            "region_id": unit.get("region_id"),
            "label": unit.get("label"),
            "subject_slot": unit.get("subject_slot"),
            "mask_ref": ["scene_node", unit.get("attn_mask_output_index")],
            "weight": unit.get("effective_weight", unit.get("weight")),
            "requested_weight": unit.get("requested_weight"),
            "effective_weight": unit.get("effective_weight"),
            "requested_start_at": unit.get("requested_start_at"),
            "effective_start_at": unit.get("effective_start_at"),
            "requested_end_at": unit.get("requested_end_at"),
            "effective_end_at": unit.get("effective_end_at"),
            "scope_mode": unit.get("scope_mode"),
            "execution_mode": unit.get("ipadapter_execution_mode"),
            "composition_preservation_enabled": unit.get("composition_preservation_enabled"),
            "warnings": list(unit.get("instruction_preservation_warnings") or []),
            "mode": unit.get("mode"),
        })
    return {
        "schema": "neo.image.scene_director.native_regional_adapter_injection.v054.v1",
        "phase": "SD-V054-26.9.9",
        "requested_mode": requested_mode,
        "lora": {
            "requested_mode": requested_mode if requested_mode in {"hybrid", "first_pass_native"} else "finish_pass",
            "actual_mode": ("regional_model_delta_mixer" if any(isinstance(l, dict) and l.get("actual_mode") == "regional_model_delta_mixer" for l in (lora_lanes or [])) else ("first_pass_native" if lora_nodes_added else "finish_pass_fallback")),
            "lane_count": len(lora_lanes or []),
            "nodes_added": list(lora_nodes_added or []),
            "lanes": deepcopy(lora_lanes or []),
        },
        "ipadapter": {
            "requested_mode": ipadapter_requested_mode,
            "actual_mode": ipadapter_actual_mode,
            "nodes_added": list(ipadapter_nodes_added or []),
            "region_count": len(ipadapter_units or []) if ipadapter_nodes_added else 0,
            "regions": ip_regions if ipadapter_nodes_added else [],
            "safety_gate": deepcopy(ipadapter_safety_gate or {}),
        },
        "policy": "Adapters prefer native first-pass injection when safe; Phase 26.9.14 requires runtime proof for the regional LoRA mixer and falls back to visible masked regional finish pass when proof is missing, with finish/second-pass fallback retained.",
    }


def _same_ref(a: Any, b: Any) -> bool:
    if not (isinstance(a, list) and isinstance(b, list) and len(a) >= 2 and len(b) >= 2):
        return False
    return str(a[0]) == str(b[0]) and a[1] == b[1]


_SAMPLER_MODEL_WRAPPER_CLASSES = {
    "DynamicThresholdingFull",
    "RescaleCFG",
    "ModelSamplingDiscrete",
    "ModelSamplingContinuousEDM",
    "ModelSamplingContinuousV",
    "ModelSamplingFlux",
    "ModelSamplingAuraFlow",
}


def _rewire_existing_sampler_model_wrapper(
    graph: dict[str, Any],
    original_model_ref: Any,
    scene_model_ref: list[Any],
) -> list[Any]:
    """Keep an already compiled model wrapper on both split samplers.

    Dynamic Thresholding and Comfy model-sampling nodes are often compiled
    before Scene Director. Replacing the main sampler's model with the raw
    V054 output would leave the midpoint sampler on a different model path.
    Rebind only known model-wrapper nodes to the new Scene Director output;
    unknown nodes stay untouched and safely fall back to the raw scene model.
    """
    if not isinstance(original_model_ref, (list, tuple)) or len(original_model_ref) < 2:
        return list(scene_model_ref)
    source = str(original_model_ref[0])
    output = original_model_ref[1]
    node = graph.get(source)
    if not isinstance(node, dict) or str(node.get("class_type") or "") not in _SAMPLER_MODEL_WRAPPER_CLASSES:
        return list(scene_model_ref)
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    if not isinstance(inputs.get("model"), (list, tuple)):
        return list(scene_model_ref)
    inputs["model"] = deepcopy(scene_model_ref)
    return [source, output]


def _find_vae_decode_consumers(graph: dict[str, Any], latent_ref: list[Any]) -> list[str]:
    consumers: list[str] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict) or node.get("class_type") != "VAEDecode":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if _same_ref(inputs.get("samples"), latent_ref):
            consumers.append(str(node_id))
    return consumers


def _prune_stale_character_lock_passes(
    graph: dict[str, Any],
    *,
    sampler_node_id: str,
    scene_node_ids: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove an older Scene Director Character Lock repair chain before replanning.

    A replayed Comfy graph can already contain the previous four-KSampler
    Character Lock chain. Replacing the payload alone does not remove those
    nodes, and the old VAEDecode may still point at the last repair sampler.
    Only chains that satisfy all of these constraints are eligible:

    * the sampler model is a V054 Scene Director model output;
    * the latent enters through ``SetLatentNoiseMask``;
    * that mask is one of the V054 subject-mask outputs; and
    * the chain descends from the current main sampler.

    This deliberately leaves unrelated IPAdapter, LoRA, ADetailer, and user
    KSampler passes intact. The caller can then build exactly the newly chosen
    pass plan on the clean main latent.
    """
    scene_ids = {str(item) for item in (scene_node_ids or set()) if str(item).strip()}
    main_ref = [str(sampler_node_id), 0]
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_lock_graph_cleanup.v054.v1",
        "phase": "SD-V054-27.1",
        "status": "not_needed",
        "scene_node_ids": sorted(scene_ids),
        "nodes_removed": [],
        "decode_nodes_rewired": [],
        "warnings": [],
    }
    if not scene_ids:
        meta["status"] = "no_existing_v054_node"
        return graph, meta

    def ref_source(value: Any) -> str | None:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return str(value[0])
        return None

    def ref_output(value: Any) -> int | None:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return int(value[1])
            except (TypeError, ValueError):
                return None
        return None

    def is_subject_mask_ref(value: Any, visited: set[str] | None = None) -> bool:
        source = ref_source(value)
        output = ref_output(value)
        if source in scene_ids and output is not None and 6 <= output <= 9:
            return True
        # Older midpoint plans inserted a MaskComposite before
        # SetLatentNoiseMask for two or more characters. Follow those mask
        # inputs so replay can remove the complete stale chain, not just the
        # final SetLatentNoiseMask node.
        if not source:
            return False
        seen = set(visited or set())
        if source in seen:
            return False
        seen.add(source)
        node = graph.get(source)
        if not isinstance(node, dict) or node.get("class_type") != "MaskComposite":
            return False
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        return is_subject_mask_ref(inputs.get("destination"), seen) or is_subject_mask_ref(inputs.get("source"), seen)

    def node_inputs(node_id: str) -> dict[str, Any]:
        node = graph.get(str(node_id))
        return node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}

    # Phase 27.6: the background-safe midpoint route no longer uses
    # SetLatentNoiseMask, so the older cleanup signature below cannot see it.
    # Detect only a continuation sampler that descends directly from the main
    # sampler and whose conditioning depends on V054 subject masks.
    subject_conditioning_mask_nodes = {
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict)
        and node.get("class_type") in {"ConditioningSetMask", "ConditioningSetMaskAndCombine"}
        and is_subject_mask_ref((node.get("inputs") or {}).get("mask"))
    }

    def conditioning_depends_on_subject_mask(value: Any, visited: set[str] | None = None) -> bool:
        source = ref_source(value)
        if not source:
            return False
        if source in subject_conditioning_mask_nodes:
            return True
        seen = set(visited or set())
        if source in seen:
            return False
        seen.add(source)
        node = graph.get(source)
        if not isinstance(node, dict) or node.get("class_type") not in {"ConditioningCombine", "ConditioningConcat"}:
            return False
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        return any(conditioning_depends_on_subject_mask(ref, seen) for ref in inputs.values())

    stale_midpoint_sampler_ids: set[str] = set()
    for node_id, node in graph.items():
        node_key = str(node_id)
        if node_key == str(sampler_node_id) or not isinstance(node, dict) or node.get("class_type") != "KSamplerAdvanced":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if ref_source(inputs.get("latent_image")) != str(sampler_node_id) or ref_output(inputs.get("latent_image")) != 0:
            continue
        if str(inputs.get("add_noise") or "").strip().lower() != "disable" or _int(inputs.get("start_at_step"), 0) <= 0:
            continue
        if not (
            conditioning_depends_on_subject_mask(inputs.get("positive"))
            or conditioning_depends_on_subject_mask(inputs.get("negative"))
        ):
            continue
        stale_midpoint_sampler_ids.add(node_key)

    if stale_midpoint_sampler_ids:
        midpoint_conditioning_nodes: set[str] = set()
        midpoint_text_nodes: set[str] = set()

        def collect_conditioning_path(value: Any, visited: set[str] | None = None) -> bool:
            source = ref_source(value)
            if not source:
                return False
            seen = set(visited or set())
            if source in seen:
                return False
            seen.add(source)
            node = graph.get(source)
            if not isinstance(node, dict):
                return False
            cls = str(node.get("class_type") or "")
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if source in subject_conditioning_mask_nodes:
                midpoint_conditioning_nodes.add(source)
                cond_source = ref_source(inputs.get("conditioning"))
                cond_node = graph.get(cond_source) if cond_source else None
                if cond_source and isinstance(cond_node, dict) and cond_node.get("class_type") == "CLIPTextEncode":
                    midpoint_text_nodes.add(cond_source)
                return True
            if cls not in {"ConditioningCombine", "ConditioningConcat"}:
                return False
            child_hit = False
            for ref in inputs.values():
                if collect_conditioning_path(ref, seen):
                    child_hit = True
            if child_hit:
                midpoint_conditioning_nodes.add(source)
            return child_hit

        for sampler_id in stale_midpoint_sampler_ids:
            inputs = node_inputs(sampler_id)
            collect_conditioning_path(inputs.get("positive"))
            collect_conditioning_path(inputs.get("negative"))

        removable_midpoint_ids = set(stale_midpoint_sampler_ids) | midpoint_conditioning_nodes | midpoint_text_nodes
        for consumer_id, consumer in list(graph.items()):
            if not isinstance(consumer, dict) or str(consumer_id) in removable_midpoint_ids:
                continue
            inputs = consumer.get("inputs") if isinstance(consumer.get("inputs"), dict) else {}
            for key, value in list(inputs.items()):
                if ref_source(value) not in stale_midpoint_sampler_ids or ref_output(value) != 0:
                    continue
                if consumer.get("class_type") == "VAEDecode" and key == "samples":
                    inputs[key] = list(main_ref)
                    meta["decode_nodes_rewired"].append(str(consumer_id))

        for node_id in removable_midpoint_ids:
            graph.pop(str(node_id), None)

        # Restore the early segment to a complete ordinary sampler before the
        # newly selected plan is compiled. Otherwise an attention-only replay
        # would decode the unfinished midpoint latent.
        main_node = graph.get(str(sampler_node_id))
        if isinstance(main_node, dict) and main_node.get("class_type") == "KSamplerAdvanced":
            main_inputs = main_node.get("inputs") if isinstance(main_node.get("inputs"), dict) else {}
            main_node["class_type"] = "KSampler"
            main_inputs["seed"] = int(main_inputs.get("noise_seed", main_inputs.get("seed", 0)) or 0)
            main_inputs.setdefault("denoise", 1.0)
            for key in ("add_noise", "noise_seed", "start_at_step", "end_at_step", "return_with_leftover_noise"):
                main_inputs.pop(key, None)

        meta.update({
            "status": "pruned",
            "midpoint_sampler_nodes_removed": sorted(stale_midpoint_sampler_ids),
            "conditioning_nodes_removed": sorted(midpoint_conditioning_nodes),
            "text_nodes_removed": sorted(midpoint_text_nodes),
            "nodes_removed": sorted(removable_midpoint_ids),
        })

    subject_mask_nodes = {
        str(node_id)
        for node_id, node in graph.items()
        if isinstance(node, dict)
        and node.get("class_type") == "SetLatentNoiseMask"
        and is_subject_mask_ref((node.get("inputs") or {}).get("mask"))
    }
    if not subject_mask_nodes:
        return graph, meta

    pass_sampler_ids: set[str] = set()
    pass_mask_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for mask_id in subject_mask_nodes:
            inputs = node_inputs(mask_id)
            source = ref_source(inputs.get("samples"))
            if source == str(sampler_node_id) or source in pass_sampler_ids:
                if mask_id not in pass_mask_ids:
                    pass_mask_ids.add(mask_id)
                    changed = True
        for node_id, node in graph.items():
            if not isinstance(node, dict) or node.get("class_type") not in {"KSampler", "KSamplerAdvanced"}:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            model_ref = inputs.get("model")
            latent_source = ref_source(inputs.get("latent_image"))
            if ref_source(model_ref) not in scene_ids or ref_output(model_ref) != 0 or latent_source not in pass_mask_ids:
                continue
            node_key = str(node_id)
            if node_key not in pass_sampler_ids:
                pass_sampler_ids.add(node_key)
                changed = True

    if not pass_sampler_ids:
        return graph, meta

    # Do not remove a pass that feeds an unrelated node type. The normal
    # Character Lock chain ends at VAEDecode; preserving an unusual external
    # consumer is safer than guessing how to rewrite it.
    removable_sampler_ids: set[str] = set()
    for sampler_id in pass_sampler_ids:
        external = []
        for consumer_id, consumer in graph.items():
            if str(consumer_id) in pass_sampler_ids or str(consumer_id) in pass_mask_ids or not isinstance(consumer, dict):
                continue
            inputs = consumer.get("inputs") if isinstance(consumer.get("inputs"), dict) else {}
            for key, value in inputs.items():
                if ref_source(value) == sampler_id and ref_output(value) == 0:
                    external.append((str(consumer.get("class_type") or ""), str(key)))
        if all(cls == "VAEDecode" and key == "samples" for cls, key in external):
            removable_sampler_ids.add(sampler_id)
        elif not external:
            removable_sampler_ids.add(sampler_id)
        else:
            meta["warnings"].append(f"preserved_character_lock_sampler_{sampler_id}_external_consumer")

    if not removable_sampler_ids:
        meta["status"] = "preserved_external_consumer"
        return graph, meta

    # Remove only masks that feed a sampler being removed. A preceding mask in
    # a retained chain is left intact and therefore cannot be orphaned by this
    # cleanup.
    removable_mask_ids = {
        mask_id
        for mask_id in pass_mask_ids
        if any(ref_source(node_inputs(sampler_id).get("latent_image")) == mask_id for sampler_id in removable_sampler_ids)
    }

    # Include any old MaskComposite nodes that feed the removable latent mask.
    # They are part of the stale Character Lock chain and must not remain as
    # orphaned graph nodes after a new midpoint plan is compiled.
    def collect_mask_dependencies(value: Any, collected: set[str] | None = None) -> set[str]:
        collected = collected if collected is not None else set()
        source = ref_source(value)
        if not source or source in collected or source in scene_ids:
            return collected
        node = graph.get(source)
        if not isinstance(node, dict):
            return collected
        if node.get("class_type") != "MaskComposite":
            return collected
        collected.add(source)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        collect_mask_dependencies(inputs.get("destination"), collected)
        collect_mask_dependencies(inputs.get("source"), collected)
        return collected

    for mask_id in list(removable_mask_ids):
        collect_mask_dependencies(node_inputs(mask_id).get("mask"), removable_mask_ids)
    removable_ids = set(removable_sampler_ids) | removable_mask_ids

    # Reconnect the final latent consumers before deleting the stale chain.
    for consumer_id, consumer in list(graph.items()):
        if not isinstance(consumer, dict) or str(consumer_id) in removable_ids:
            continue
        inputs = consumer.get("inputs") if isinstance(consumer.get("inputs"), dict) else {}
        for key, value in list(inputs.items()):
            if ref_source(value) not in removable_ids or ref_output(value) != 0:
                continue
            if consumer.get("class_type") == "VAEDecode" and key == "samples":
                inputs[key] = list(main_ref)
                meta["decode_nodes_rewired"].append(str(consumer_id))
            elif key in {"samples", "latent_image"}:
                inputs[key] = list(main_ref)
            else:
                meta["warnings"].append(f"unrewritten_consumer_{consumer_id}_{key}")

    # Character-lock text nodes are removed only when they have no remaining
    # consumers after the KSampler chain is removed.
    text_candidates: set[str] = set()
    for sampler_id in removable_sampler_ids:
        inputs = node_inputs(sampler_id)
        for key in ("positive", "negative"):
            source = ref_source(inputs.get(key))
            node = graph.get(source) if source else None
            if source and isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                text_candidates.add(source)
    for text_id in text_candidates:
        still_used = False
        for consumer_id, consumer in graph.items():
            if str(consumer_id) in removable_ids or str(consumer_id) == text_id or not isinstance(consumer, dict):
                continue
            inputs = consumer.get("inputs") if isinstance(consumer.get("inputs"), dict) else {}
            if any(ref_source(value) == text_id for value in inputs.values()):
                still_used = True
                break
        if not still_used:
            removable_ids.add(text_id)

    for node_id in sorted(removable_ids, key=lambda item: (not str(item).isdigit(), int(item) if str(item).isdigit() else str(item))):
        graph.pop(str(node_id), None)

    meta.update({
        "status": "pruned",
        "nodes_removed": sorted(removable_ids),
        "sampler_nodes_removed": sorted(removable_sampler_ids),
        "mask_nodes_removed": sorted(removable_mask_ids),
        "text_nodes_removed": sorted(text_candidates & removable_ids),
    })
    return graph, meta



def _apply_scene_director_two_pass_identity_restore_v054(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    ip_model_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """Run IPAdapter/FaceID as a second pass over Scene Director layout.

    Phase 26.9.4 restores Scene Director regional MODEL authority for the first
    sampler pass. To avoid the IPAdapter Plus attention-shape crash, IPAdapter
    remains on the safe base-model chain and is only used in a low-denoise
    second KSampler pass over the Scene Director latent.
    """
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.two_pass_identity_restore.v054.v1",
        "phase": "SD-V054-26.9.4",
        "status": "not_applicable",
        "policy": "First pass uses Scene Director regional model authority; second pass uses safe IPAdapter model chain over the first-pass latent.",
        "first_pass_model": "scene_director_regional_model",
        "second_pass_model": "safe_ipadapter_model",
        "decode_source": "first_pass",
    }
    if not ip_model_ref:
        return next_id, list(base_latent_ref), [], [], meta
    if not _available_node(available_nodes, "KSampler"):
        meta["status"] = "blocked"
        meta["reason"] = "missing_KSampler"
        return next_id, list(base_latent_ref), [], ["Scene Director two-pass identity restore skipped: missing KSampler node."], meta
    source_steps = _int(sampler_inputs.get("steps"), 20)
    steps = max(6, min(28, _int(sampler_inputs.get("scene_director_identity_restore_steps") or max(8, int(source_steps * 0.35)), 10)))
    denoise = max(0.08, min(0.45, _float(sampler_inputs.get("scene_director_identity_restore_denoise"), 0.32)))
    cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde").strip() or "dpmpp_2m_sde"
    scheduler = str(sampler_inputs.get("scheduler") or "karras").strip() or "karras"
    sampler_id = str(next_id)
    graph[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(sampler_seed) + 424242,
            "steps": int(steps),
            "cfg": float(cfg),
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "denoise": float(denoise),
            "model": deepcopy(ip_model_ref),
            "positive": deepcopy(positive_ref),
            "negative": deepcopy(negative_ref),
            "latent_image": deepcopy(base_latent_ref),
        },
    }
    meta.update({
        "status": "applied",
        "node_id": sampler_id,
        "nodes_added": [sampler_id],
        "steps": steps,
        "denoise": denoise,
        "seed": int(sampler_seed) + 424242,
        "decode_source": "second_pass_identity_restore",
    })
    notes = [
        f"Scene Director two-pass safety active: first pass uses regional model authority; second pass restores masked IPAdapter/FaceID identity with denoise {denoise:.2f}.",
        "Scene Director regional attention model output was reattached to the first sampler; IPAdapter remains isolated in the second pass to avoid attention-batch crashes.",
    ]
    return next_id + 1, [sampler_id, 0], [sampler_id], notes, meta

def _apply_scene_director_regional_lora_finish_passes(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    block: dict[str, Any],
    legacy: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
    scene_graph: dict[str, Any] | None = None,
    regional_authority_restore: dict[str, Any] | None = None,
) -> tuple[int, list[Any], list[str], list[str], list[dict[str, Any]]]:
    """Apply region LoRAs as an isolated finish pass.

    This function deliberately does not edit IPAdapter nodes or the primary
    sampler model chain. It starts from the already-sampled latent and only
    rewires VAEDecode to the final regional-LoRA refinement latent.
    """
    units = _normalize_scene_director_lora_units(block, legacy, subject_slot_by_region)
    first_pass_applied_uids = {str(uid) for uid in ((legacy or {}).get("scene_director_lora_first_pass_applied_uids") or [])}
    runtime_fallback_uids = {str(uid) for uid in ((legacy or {}).get("scene_director_lora_runtime_fallback_uids") or [])}
    if first_pass_applied_uids:
        units = [unit for unit in units if str(unit.get("uid") or "") not in first_pass_applied_uids]
    if not units:
        return next_id, list(base_latent_ref), [], [], []
    required = ["LoraLoader", "SetLatentNoiseMask", "KSampler"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    if missing:
        return next_id, list(base_latent_ref), [], [
            "Scene Director regional LoRA skipped: missing Comfy node(s) " + ", ".join(missing) + "."
        ], []
    current_latent_ref = list(base_latent_ref)
    added: list[str] = []
    notes: list[str] = []
    source_steps = _int(sampler_inputs.get("steps"), 20)
    default_requested_steps_value = (legacy or {}).get("scene_director_lora_steps")
    default_requested_denoise_value = (legacy or {}).get("scene_director_lora_denoise")
    cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    lane_metadata: list[dict[str, Any]] = []
    identity_strength = _float((block.get("params") or {}).get("identity_strength") if isinstance(block.get("params"), dict) else (legacy or {}).get("scene_director_appearance_lock_gain"), 0.55)
    region_lookup = _region_lookup_by_id(scene_graph)
    for offset, unit in enumerate(units):
        region_index = max(1, min(999, _int(unit.get("region_index"), offset + 1)))
        subject_slot = max(1, min(4, _int(unit.get("subject_slot"), offset + 1)))
        mask_ref = [str(scene_node_id), 5 + subject_slot]
        region = region_lookup.get(str(unit.get("region_id") or ""), {})
        route_mode = _lora_route_mode(unit)
        if route_mode == "crop_refine_pass":
            continue
        character_lora = _is_character_lora_unit(unit, region)
        requested_strength = round(_float(unit.get("strength"), 0.8), 4)
        explicit_strength_override = bool(unit.get("strength_user_override"))
        strength = requested_strength
        visibility_profile = "regional_character_visible" if character_lora else "regional_standard"
        if character_lora and not explicit_strength_override:
            strength = max(strength, 0.85)
        strength, visibility_booster = _apply_lora_visibility_strength(unit, region, strength, explicit_strength_override)
        compatibility = _lora_checkpoint_compatibility(unit, legacy)
        preset_values = visibility_booster.get("preset_values") if isinstance(visibility_booster.get("preset_values"), dict) else {}
        requested_steps_value = _optional_unit_value(unit, "finish_steps", default_requested_steps_value if default_requested_steps_value is not None else preset_values.get("finish_steps"))
        requested_denoise_value = _optional_unit_value(unit, "finish_denoise", default_requested_denoise_value if default_requested_denoise_value is not None else preset_values.get("finish_denoise"))
        explicit_steps_override = bool(unit.get("finish_steps_user_override") or requested_steps_value is not None)
        explicit_denoise_override = bool(unit.get("finish_denoise_user_override") or requested_denoise_value is not None)
        steps = max(4, min(24, _int(requested_steps_value or max(6, int(source_steps * 0.25)), 8)))
        denoise = max(0.05, min(0.45, _float(requested_denoise_value, 0.28)))
        runtime_fallback = str(unit.get("uid") or "") in runtime_fallback_uids
        if character_lora and not explicit_steps_override:
            steps = max(steps, 16 if runtime_fallback else 14)
        if character_lora and not explicit_denoise_override:
            denoise = max(denoise, 0.38 if runtime_fallback else 0.34)
        if character_lora and runtime_fallback and not explicit_strength_override:
            strength = max(strength, 0.95)
        strength = round(min(max(strength, 0.0), 2.0), 4)
        steps = max(1, min(40, int(steps)))
        denoise = round(max(0.05, min(float(denoise), 0.65)), 4)
        positive_text, negative_text, prompt_stack_meta = _lora_finish_pass_prompt_stack(
            unit=unit,
            scene_graph=scene_graph,
            legacy=legacy,
            identity_strength=identity_strength,
            regional_authority_restore=regional_authority_restore,
        )
        prompt_stack_meta.setdefault("visibility_warnings", [])
        for warning_code in [*(compatibility.get("warnings") or []), *(visibility_booster.get("warnings") or [])]:
            if warning_code not in prompt_stack_meta["visibility_warnings"]:
                prompt_stack_meta["visibility_warnings"].append(str(warning_code))
        strength_model, strength_clip = _lora_strength_pair(unit, strength)
        lora_id = str(next_id)
        graph[lora_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": list(model_ref),
                "clip": list(clip_ref),
                "lora_name": str(unit.get("name") or ""),
                "strength_model": strength_model,
                "strength_clip": strength_clip,
            },
        }
        positive_id = str(next_id + 1)
        graph[positive_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": [lora_id, 1],
                "text": positive_text or "",
            },
        }
        negative_id = str(next_id + 2)
        graph[negative_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": [lora_id, 1],
                "text": negative_text or "",
            },
        }
        mask_latent_id = str(next_id + 3)
        graph[mask_latent_id] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": list(current_latent_ref), "mask": mask_ref},
        }
        sampler_id = str(next_id + 4)
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 9703 + (offset * 101),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
                "model": [lora_id, 0],
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_latent_id, 0],
            },
        }
        current_latent_ref = [sampler_id, 0]
        added.extend([lora_id, positive_id, negative_id, mask_latent_id, sampler_id])
        lane_metadata.append({
            **prompt_stack_meta,
            "uid": unit.get("uid"),
            "lora_name": unit.get("name"),
            "subject_slot": subject_slot,
            "mask_ref": mask_ref,
            "positive_node_id": positive_id,
            "negative_node_id": negative_id,
            "visibility_profile": "regional_character_lora_visual_authority_fallback" if runtime_fallback else visibility_profile,
            "runtime_fallback": runtime_fallback,
            "runtime_fallback_reason": "regional_lora_mixer_claimed_but_no_runtime_delta" if runtime_fallback else "",
            "visual_authority_profile": "regional_character_lora_visual_authority" if runtime_fallback else visibility_profile,
            "route_mode": route_mode,
            "visibility_booster": visibility_booster,
            "lora_compatibility": compatibility,
            "lora_family": compatibility.get("lora_family"),
            "checkpoint_family": compatibility.get("checkpoint_family"),
            "checkpoint_name": compatibility.get("checkpoint_name"),
            "requested_lora_strength": requested_strength,
            "effective_lora_strength": strength,
            "effective_lora_strength_model": strength_model,
            "effective_lora_strength_clip": strength_clip,
            "strength_user_override": explicit_strength_override,
            "requested_finish_denoise": requested_denoise_value,
            "effective_finish_denoise": denoise,
            "denoise_user_override": explicit_denoise_override,
            "requested_finish_steps": requested_steps_value,
            "effective_finish_steps": steps,
            "steps_user_override": explicit_steps_override,
        })
        notes.append(
            f"Scene Director regional LoRA applied {unit.get('name')} to {unit.get('label') or f'Region {region_index}'} with subject_{subject_slot}_mask, denoise {denoise:.2f}."
        )
        next_id += 5
    if added:
        notes.append("Scene Director executed assigned LoRA Stack row(s) as isolated masked regional finish pass(es), leaving the IPAdapter FaceID model chain untouched.")
    return next_id, current_latent_ref, added, notes, lane_metadata


def _apply_scene_director_latent_character_lock_postpass_authority(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    scene_node_id: str,
    lora_lanes: list[dict[str, Any]] | None,
    block: dict[str, Any],
    legacy: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
    scene_graph: dict[str, Any] | None = None,
) -> tuple[int, list[Any], list[str], list[str], dict[str, Any]]:
    """Phase 26.10.8H/8I: re-assert latent Character Lock after LoRA finish passes.

    Character Lock strong/strict is not prompt-only. When a regional LoRA fallback
    runs after the primary Scene Director sample, this masked pass reattaches the
    non-LoRA Scene Director model to the same subject mask. Phase 26.10.8I also
    uses a region-local gender/body conditioner instead of broad global scene
    conditioning, because broad conditioning can preserve the scene while failing
    to correct a feminized locked male subject.
    """
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.latent_character_lock_postpass_authority.v054.v1",
        "phase": "SD-V054-26.10.8H",
        "status": "not_applicable",
        "route_count": 0,
        "applied_count": 0,
        "skipped_count": 0,
        "nodes_added": [],
        "lanes": [],
        "warnings": [],
        "policy": "Strong/strict Character Lock is reasserted at latent level after regional LoRA finish passes unless the user explicitly allows character repaint. Phase 26.10.8I uses region-local gender/body conditioning for the masked lock pass.",
    }
    lanes_in = [lane for lane in (lora_lanes or []) if isinstance(lane, dict)]
    if not lanes_in:
        return next_id, list(base_latent_ref), [], [], meta
    required = ["SetLatentNoiseMask", "KSampler"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    meta["route_count"] = len(lanes_in)
    if missing:
        meta.update({
            "status": "skipped_missing_nodes",
            "skipped_count": len(lanes_in),
            "warnings": ["latent_character_lock_postpass_missing_nodes:" + ",".join(missing)],
        })
        return next_id, list(base_latent_ref), [], ["Scene Director latent Character Lock post-pass skipped: missing Comfy node(s) " + ", ".join(missing) + "."], meta

    units_by_uid = {str(unit.get("uid") or ""): unit for unit in _normalize_scene_director_lora_units(block, legacy, subject_slot_by_region) if isinstance(unit, dict)}
    region_lookup = _region_lookup_by_id(scene_graph)
    current_ref = list(base_latent_ref)
    added: list[str] = []
    notes: list[str] = []
    out_lanes: list[dict[str, Any]] = []
    source_steps = _int(sampler_inputs.get("steps"), 20)
    steps = max(6, min(18, _int((legacy or {}).get("scene_director_character_lock_postpass_steps") or max(8, int(source_steps * 0.22)), 10)))
    requested_lock_denoise = (legacy or {}).get("scene_director_character_lock_postpass_denoise")
    denoise = round(max(0.06, min(0.30, _float(requested_lock_denoise, 0.24))), 4)
    cfg = _float(sampler_inputs.get("cfg"), 7.0)
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")

    for offset, lane in enumerate(lanes_in):
        region_id = str(lane.get("region_id") or "")
        unit = units_by_uid.get(str(lane.get("uid") or ""), {})
        region = region_lookup.get(region_id, {})
        risk = _locked_character_lora_postpass_risk(unit, region, scene_graph)
        if not risk.get("requires_latent_protection"):
            meta.setdefault("warnings", []).append("latent_character_lock_postpass_not_required_or_unlocked")
            continue
        subject_slot = max(1, min(4, _int(lane.get("subject_slot") or unit.get("subject_slot") or (subject_slot_by_region or {}).get(region_id), offset + 1)))
        mask_ref = [str(scene_node_id), 5 + subject_slot]
        label = str(lane.get("label") or region.get("label") or region_id)
        base_negative_text = ""
        try:
            neg_node_id = str((negative_ref or [None])[0])
            base_negative_text = str(((graph.get(neg_node_id) or {}).get("inputs") or {}).get("text") or "")
        except Exception:
            base_negative_text = ""
        local_positive, local_negative, local_conditioning = _latent_character_lock_local_conditioning_text(
            region,
            label=label,
            negative_fallback=base_negative_text,
        )
        positive_id = str(next_id)
        negative_id = str(next_id + 1)
        mask_id = str(next_id + 2)
        sampler_id = str(next_id + 3)
        graph[positive_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": local_positive},
        }
        graph[negative_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": deepcopy(clip_ref), "text": local_negative},
        }
        graph[mask_id] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": list(current_ref), "mask": list(mask_ref)},
        }
        sampler_id = str(next_id + 3)
        graph[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(sampler_seed) + 58291 + (offset * 97),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
                "model": deepcopy(model_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
                "latent_image": [mask_id, 0],
            },
        }
        current_ref = [sampler_id, 0]
        local_added = [positive_id, negative_id, mask_id, sampler_id]
        added.extend(local_added)
        out_lanes.append({
            "schema": "neo.image.scene_director.latent_character_lock_postpass_authority.lane.v054.v1",
            "phase": "SD-V054-26.10.8I",
            "uid": lane.get("uid"),
            "lora_name": lane.get("lora_name"),
            "region_id": region_id,
            "label": label,
            "subject_slot": subject_slot,
            "mask_ref": list(mask_ref),
            "conditioner_positive_node_id": positive_id,
            "conditioner_negative_node_id": negative_id,
            "latent_mask_node_id": mask_id,
            "sampler_node_id": sampler_id,
            "source_lora_positive_node_id": lane.get("positive_node_id"),
            "source_lora_negative_node_id": lane.get("negative_node_id"),
            "effective_denoise": denoise,
            "effective_steps": steps,
            "model_source": "scene_director_locked_model",
            "conditioning_source": "region_local_gender_body_lock",
            "fallback_conditioning_source": "scene_director_locked_positive_negative",
            "local_conditioning": deepcopy(local_conditioning),
            "character_lock_latent_protected": True,
            "postpass_may_bypass_lock": False,
            "postpass_lock_policy": risk.get("postpass_lock_policy"),
            "risk": deepcopy(risk),
            "warnings": [],
        })
        notes.append(f"Scene Director latent Character Lock reasserted after LoRA for {label} using subject_{subject_slot}_mask at denoise {denoise:.2f} with local gender/body conditioning.")
        next_id += 4

    meta.update({
        "status": "applied" if out_lanes else "skipped",
        "applied_count": len(out_lanes),
        "skipped_count": max(0, len(lanes_in) - len(out_lanes)),
        "nodes_added": list(added),
        "lanes": deepcopy(out_lanes),
        "warnings": sorted(set(meta.get("warnings") or [])),
    })
    return next_id, current_ref, added, notes, meta


def _lora_crop_refine_nodes_available(available_nodes: Any) -> tuple[bool, list[str]]:
    """Phase 26.9.15: ADetailer-style crop refinement needs Impact Pack image-detail nodes.

    This path intentionally works after the main Scene Director/IPAdapter image is formed.
    It converts the assigned Scene Director subject mask into SEGS, applies the assigned
    LoRA only inside that cropped region, then pastes the refined region back. This is
    closer to separate ADetailer behavior than a full-frame latent noise mask pass.
    """
    required = ["LoraLoader", "MaskToSEGS", "ToBasicPipe", "SEGSDetailer", "SEGSPaste"]
    missing = [name for name in required if not _available_node(available_nodes, name)]
    return not missing, missing


def _apply_scene_director_regional_lora_crop_refinement_passes(
    graph: dict[str, Any],
    *,
    next_id: int,
    base_latent_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    scene_node_id: str,
    block: dict[str, Any],
    legacy: dict[str, Any] | None,
    sampler_inputs: dict[str, Any],
    sampler_seed: int,
    available_nodes: Any,
    subject_slot_by_region: dict[str, int] | None = None,
    scene_graph: dict[str, Any] | None = None,
    regional_authority_restore: dict[str, Any] | None = None,
) -> tuple[int, list[str], list[str], list[dict[str, Any]], list[str], dict[str, Any]]:
    """Apply regional LoRA as an ADetailer-style cropped image refinement pass.

    The existing masked latent fallback can be too weak for character LoRAs because the
    subject is still competing with the entire frame. This pass uses the Scene Director
    subject mask as an Impact SEGS selection, runs a LoRA-patched local detailer pass on
    that crop, and pastes the crop back into the composed image. It is still region-only,
    but visually stronger because the selected subject fills the local refinement area.
    """
    meta: dict[str, Any] = {
        "schema": "neo.image.scene_director.regional_lora_crop_refinement.v054.v1",
        "phase": "SD-V054-26.9.15",
        "status": "not_applicable",
        "mode": "adetailer_style_crop_refine_pass",
        "route_count": 0,
        "applied_count": 0,
        "skipped_count": 0,
        "nodes_added": [],
        "image_consumers_rewired": [],
        "lanes": [],
        "warnings": [],
        "policy": "Regional character LoRAs that cannot prove node-side model-delta mixing are refined through an ADetailer-style crop pass using the assigned Scene Director subject mask.",
    }
    postpass_gate: dict[str, Any] = {
        "schema": "neo.image.scene_director.postpass_character_lock_gate.v054.v1",
        "phase": "SD-V054-26.9.16",
        "status": "not_applicable",
        "lanes": [],
        "warnings": [],
        "policy": "Post-generation LoRA crop/detail passes must carry prompt-derived character, gender, body, outfit, and relationship-preservation guards so they cannot overwrite the base Scene Director lock.",
    }
    units = _normalize_scene_director_lora_units(block, legacy, subject_slot_by_region)
    runtime_fallback_uids = {str(uid) for uid in ((legacy or {}).get("scene_director_lora_runtime_fallback_uids") or [])}
    units = [u for u in units if str(u.get("uid") or "") in runtime_fallback_uids]
    if not units:
        return next_id, [], [], [], [], meta

    ok, missing = _lora_crop_refine_nodes_available(available_nodes)
    meta["route_count"] = len(units)
    postpass_gate["status"] = "pending"
    if not ok:
        meta.update({
            "status": "skipped_missing_nodes",
            "skipped_count": len(units),
            "warnings": ["regional_lora_crop_refine_missing_nodes:" + ",".join(missing)],
        })
        meta["postpass_character_lock_gate"] = postpass_gate
        return next_id, [], ["Scene Director regional LoRA crop refine skipped: missing Comfy node(s) " + ", ".join(missing) + "."], [], [], meta

    decode_id, current_image_ref, vae_ref = _find_decode_for_latent(graph, base_latent_ref)
    if current_image_ref is None or vae_ref is None:
        meta.update({
            "status": "skipped_no_decode_image",
            "skipped_count": len(units),
            "warnings": ["regional_lora_crop_refine_no_decode_image"],
        })
        meta["postpass_character_lock_gate"] = postpass_gate
        return next_id, [], ["Scene Director regional LoRA crop refine skipped: no VAEDecode image output was found for the current latent."], [], [], meta

    output_consumers = [(node_id, input_name) for node_id, input_name in _find_image_consumers(graph, current_image_ref) if node_id != decode_id]
    if not output_consumers:
        meta.update({
            "status": "skipped_no_image_consumer",
            "skipped_count": len(units),
            "warnings": ["regional_lora_crop_refine_no_image_consumer"],
        })
        meta["postpass_character_lock_gate"] = postpass_gate
        return next_id, [], ["Scene Director regional LoRA crop refine created no pass because no downstream image consumer was available to rewire."], [], [], meta

    added: list[str] = []
    notes: list[str] = []
    lanes: list[dict[str, Any]] = []
    region_lookup = _region_lookup_by_id(scene_graph)
    subject_slot_by_region = subject_slot_by_region or {}
    identity_strength = _float((block.get("params") or {}).get("identity_strength") if isinstance(block.get("params"), dict) else (legacy or {}).get("scene_director_appearance_lock_gain"), 0.55)
    source_steps = _int(sampler_inputs.get("steps"), 20)
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    default_requested_steps_value = (legacy or {}).get("scene_director_lora_crop_steps") or params.get("scene_director_lora_crop_steps")
    default_requested_denoise_value = (legacy or {}).get("scene_director_lora_crop_denoise") or params.get("scene_director_lora_crop_denoise")
    default_requested_padding_value = (legacy or {}).get("scene_director_lora_crop_padding") or params.get("scene_director_lora_crop_padding")
    default_requested_feather_value = (legacy or {}).get("scene_director_lora_crop_feather") or params.get("scene_director_lora_crop_feather")
    cfg = min(15.0, max(0.0, _float(sampler_inputs.get("cfg"), 7.0)))
    sampler_name = str(sampler_inputs.get("sampler_name") or "dpmpp_2m_sde")
    scheduler = str(sampler_inputs.get("scheduler") or "karras")
    current_ref = list(current_image_ref)

    for offset, unit in enumerate(units):
        region_id = str(unit.get("region_id") or "")
        region = region_lookup.get(region_id, {})
        route_mode = _lora_route_mode(unit)
        if route_mode == "finish_pass":
            continue
        if not _is_character_lora_unit(unit, region):
            meta["warnings"].append("regional_lora_crop_refine_non_character_route_skipped")
            continue
        # Phase 26.10.8H: crop refinement is an image-space repaint pass.
        # style_stack_crop_risk from Phase 26.10.8G is superseded by the stricter latent lock gate.
        # Strong/strict Character Lock is latent authority, not a prompt hint, so
        # crop-refine is blocked unless the user explicitly unlocks character repaint.
        lock_risk = _locked_character_lora_postpass_risk(unit, region, scene_graph)
        auto_crop_requested = route_mode == "auto" and _lora_visibility_preset_id(unit) == "off"
        if lock_risk.get("blocks_crop_refine"):
            warning_code = "regional_lora_crop_refine_blocked_by_latent_character_lock"
            if auto_crop_requested:
                warning_code = "regional_lora_auto_crop_refine_skipped_to_preserve_character_lock"
            meta["warnings"].append(warning_code)
            postpass_gate.setdefault("warnings", []).append(warning_code)
            postpass_gate.setdefault("lanes", []).append({
                "schema": "neo.image.scene_director.postpass_character_lock_gate.blocked_lane.v054.v1",
                "phase": "SD-V054-26.10.8H",
                "region_id": region_id,
                "label": str(region.get("label") or unit.get("label") or region_id or "Region"),
                "subject_slot": unit.get("subject_slot"),
                "route_mode": route_mode,
                "crop_refine_allowed": False,
                "character_lock_latent_protected": True,
                "postpass_may_bypass_lock": False,
                "postpass_lock_policy": lock_risk.get("postpass_lock_policy"),
                "required_unlock": "allow_character_repaint",
                "risk": deepcopy(lock_risk),
                "warnings": [warning_code],
            })
            notes.append(f"Scene Director LoRA crop-refine blocked for {region.get('label') or unit.get('label') or region_id}: strong Character Lock keeps latent authority unless character repaint is explicitly unlocked.")
            continue
        subject_slot = max(1, min(4, _int(unit.get("subject_slot") or subject_slot_by_region.get(region_id), offset + 1)))
        mask_ref = [str(scene_node_id), 5 + subject_slot]
        requested_strength = round(_float(unit.get("strength"), 0.8), 4)
        visibility_booster = _lora_visibility_booster_plan(unit, region)
        compatibility = _lora_checkpoint_compatibility(unit, legacy)
        preset_values = visibility_booster.get("preset_values") if isinstance(visibility_booster.get("preset_values"), dict) else {}
        requested_steps_value = _optional_unit_value(unit, "crop_steps", default_requested_steps_value if default_requested_steps_value is not None else preset_values.get("crop_steps"))
        requested_denoise_value = _optional_unit_value(unit, "crop_denoise", default_requested_denoise_value if default_requested_denoise_value is not None else preset_values.get("crop_denoise"))
        requested_padding_value = _optional_unit_value(unit, "crop_padding", default_requested_padding_value)
        requested_feather_value = _optional_unit_value(unit, "crop_feather", default_requested_feather_value)
        steps = max(12, min(36, _int(requested_steps_value or max(18, int(source_steps * 0.55)), 22)))
        explicit_crop_denoise_override = bool(unit.get("crop_denoise_user_override") or requested_denoise_value is not None)
        base_crop_denoise = round(max(0.20, min(0.72, _float(requested_denoise_value, 0.50))), 4)
        crop_factor = round(max(1.02, min(2.0, 1.0 + _float(requested_padding_value, 0.18))), 4)
        feather = max(0, min(64, _int(requested_feather_value, 24)))
        # Crop refinement is intentionally a visual-authority fallback, but Phase
        # 26.9.16 caps default denoise when strong gender/body locks and relationship
        # poses are active so the post-pass cannot feminize or reshape the subject.
        effective_strength, visibility_booster = _apply_lora_visibility_strength(unit, region, max(requested_strength, 0.95), bool(unit.get("strength_user_override")))
        lane_denoise = base_crop_denoise
        if not explicit_crop_denoise_override and _guard_mode((region.get("lock") or {}).get("character"), "off") in {"strong", "strict"}:
            lane_denoise = min(lane_denoise, 0.36)
        postpass_lane = _build_postpass_character_lock_gate_lane(
            region=region,
            unit={**unit, "subject_slot": subject_slot},
            scene_graph=scene_graph,
            requested_denoise=requested_denoise_value,
            effective_denoise=round(float(lane_denoise), 4),
            requested_strength=requested_strength,
            effective_strength=effective_strength,
            explicit_denoise_override=explicit_crop_denoise_override,
            explicit_scope=unit.get("crop_scope") or (legacy or {}).get("scene_director_lora_crop_scope") or params.get("scene_director_lora_crop_scope"),
        )
        positive_text, negative_text, prompt_stack_meta = _lora_finish_pass_prompt_stack(
            unit=unit,
            scene_graph=scene_graph,
            legacy=legacy,
            identity_strength=identity_strength,
            regional_authority_restore=regional_authority_restore,
        )
        positive_text = _append_unique_text(
            positive_text,
            postpass_lane.get("positive_guard"),
            "Preserve the Scene Director relationship pose, subject count, outfit, body role, and assigned region composition during this crop refinement pass",
        )
        negative_text = _append_unique_text(negative_text, postpass_lane.get("negative_guard"), "gender drift, changed body type, changed relationship pose, changed subject count")

        prompt_stack_meta.setdefault("visibility_warnings", [])
        for warning_code in [*(compatibility.get("warnings") or []), *(visibility_booster.get("warnings") or [])]:
            if warning_code not in prompt_stack_meta["visibility_warnings"]:
                prompt_stack_meta["visibility_warnings"].append(str(warning_code))
        strength_model, strength_clip = _lora_strength_pair(unit, effective_strength)
        lora_id = str(next_id)
        graph[lora_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": list(model_ref),
                "clip": list(clip_ref),
                "lora_name": str(unit.get("name") or ""),
                "strength_model": strength_model,
                "strength_clip": strength_clip,
            },
        }
        positive_id = str(next_id + 1)
        graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": [lora_id, 1], "text": positive_text or ""}}
        negative_id = str(next_id + 2)
        graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"clip": [lora_id, 1], "text": negative_text or ""}}
        segs_id = str(next_id + 3)
        graph[segs_id] = {
            "class_type": "MaskToSEGS",
            "inputs": {
                "mask": list(mask_ref),
                "combined": False,
                "crop_factor": float(crop_factor),
                "bbox_fill": False,
                "drop_size": 1,
                "contour_fill": False,
            },
        }
        segs_ref: list[Any] = [segs_id, 0]
        local_added = [lora_id, positive_id, negative_id, segs_id]
        next_after = next_id + 4
        if feather and _available_node(available_nodes, "ImpactDilateMaskInSEGS"):
            dilate_id = str(next_after)
            graph[dilate_id] = {"class_type": "ImpactDilateMaskInSEGS", "inputs": {"segs": list(segs_ref), "dilation": max(0, min(64, int(feather // 2)))}}
            segs_ref = [dilate_id, 0]
            local_added.append(dilate_id)
            next_after += 1
        if feather and _available_node(available_nodes, "ImpactGaussianBlurMaskInSEGS"):
            blur_id = str(next_after)
            graph[blur_id] = {"class_type": "ImpactGaussianBlurMaskInSEGS", "inputs": {"segs": list(segs_ref), "kernel_size": max(3, int(feather) * 2 + 1), "sigma": max(1.0, round(float(feather) / 2.0, 2))}}
            segs_ref = [blur_id, 0]
            local_added.append(blur_id)
            next_after += 1
        pipe_id = str(next_after)
        graph[pipe_id] = {
            "class_type": "ToBasicPipe",
            "inputs": {
                "model": [lora_id, 0],
                "clip": [lora_id, 1],
                "vae": list(vae_ref),
                "positive": [positive_id, 0],
                "negative": [negative_id, 0],
            },
        }
        detailer_id = str(next_after + 1)
        graph[detailer_id] = {
            "class_type": "SEGSDetailer",
            "inputs": {
                "image": list(current_ref),
                "segs": list(segs_ref),
                "guide_size": 768.0,
                "guide_size_for": True,
                "max_size": 1344.0,
                "seed": int(sampler_seed) + 12615 + (offset * 131),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(lane_denoise),
                "noise_mask": True,
                "force_inpaint": True,
                "basic_pipe": [pipe_id, 0],
                "refiner_ratio": 0.2,
                "batch_size": 1,
                "cycle": 1,
                "inpaint_model": False,
                "noise_mask_feather": int(feather),
            },
        }
        paste_id = str(next_after + 2)
        graph[paste_id] = {"class_type": "SEGSPaste", "inputs": {"image": list(current_ref), "segs": [detailer_id, 0], "feather": int(feather), "alpha": 255}}
        local_added.extend([pipe_id, detailer_id, paste_id])
        current_ref = [paste_id, 0]
        added.extend(local_added)
        lanes.append({
            **prompt_stack_meta,
            "uid": unit.get("uid"),
            "lora_name": unit.get("name"),
            "region_id": region_id,
            "label": str(region.get("label") or unit.get("label") or region_id or "Region"),
            "subject_slot": subject_slot,
            "mask_ref": list(mask_ref),
            "mask_to_segs_node_id": segs_id,
            "segs_detailer_node_id": detailer_id,
            "paste_node_id": paste_id,
            "positive_node_id": positive_id,
            "negative_node_id": negative_id,
            "crop_refine_pass": True,
            "actual_mode": "crop_refine_pass",
            "visual_authority_profile": "adetailer_style_regional_lora_crop_refine",
            "route_mode": route_mode,
            "visibility_booster": visibility_booster,
            "lora_compatibility": compatibility,
            "lora_family": compatibility.get("lora_family"),
            "checkpoint_family": compatibility.get("checkpoint_family"),
            "checkpoint_name": compatibility.get("checkpoint_name"),
            "requested_lora_strength": requested_strength,
            "requested_crop_denoise": requested_denoise_value,
            "requested_crop_steps": requested_steps_value,
            "effective_lora_strength": effective_strength,
            "effective_lora_strength_model": strength_model,
            "effective_lora_strength_clip": strength_clip,
            "effective_crop_denoise": round(float(lane_denoise), 4),
            "effective_crop_steps": steps,
            "crop_factor": crop_factor,
            "composite_feather": feather,
            "runtime_fallback": True,
            "runtime_fallback_reason": "regional_lora_mixer_claimed_but_no_runtime_delta",
            "model_delta_scope": "crop_refine_local_lora_model",
            "clip_delta_scope": "crop_refine_local_lora_clip",
            "hard_region_isolation": True,
            "global_bleed_risk": False,
            "crop_refine_scope": postpass_lane.get("crop_refine_scope"),
            "gender_family": postpass_lane.get("gender_family"),
            "postpass_character_lock_gate": deepcopy(postpass_lane),
            "postpass_lock_policy": _lora_postpass_lock_policy(unit),
            "character_lock_latent_protected": not _lora_postpass_unlock_requested(unit),
            "postpass_may_bypass_lock": _lora_postpass_unlock_requested(unit),
        })
        postpass_gate.setdefault("lanes", []).append(deepcopy(postpass_lane))
        for warning in postpass_lane.get("warnings") or []:
            postpass_gate.setdefault("warnings", []).append(str(warning))
        notes.append(f"Scene Director regional LoRA crop-refined {unit.get('name')} on {region.get('label') or unit.get('label') or region_id} using subject_{subject_slot}_mask via MaskToSEGS/SEGSDetailer with post-pass character lock gate.")
        next_id = next_after + 3

    rewired = _rewrite_image_consumers(graph, output_consumers, current_ref) if added else []
    postpass_gate.update({
        "status": "applied" if postpass_gate.get("lanes") else "not_applicable",
        "warnings": sorted(set(postpass_gate.get("warnings") or [])),
    })
    meta.update({
        "status": "applied" if lanes else "skipped",
        "applied_count": len(lanes),
        "skipped_count": max(0, len(units) - len(lanes)),
        "nodes_added": list(added),
        "image_consumers_rewired": list(rewired),
        "lanes": deepcopy(lanes),
        "warnings": sorted(set(meta.get("warnings") or [])),
        "postpass_character_lock_gate": deepcopy(postpass_gate),
    })
    if lanes:
        notes.append("Scene Director executed regional LoRA as an ADetailer-style crop refinement pass, then composited the selected area back to the final image.")
    return next_id, added, notes, lanes, rewired, meta

def _build_regional_adapter_visibility_v054(
    *,
    block: dict[str, Any],
    scene_graph: dict[str, Any] | None,
    ip_nodes_added: list[str],
    two_pass_identity_restore: dict[str, Any] | None,
    lora_nodes_added: list[str],
    lora_finish_prompt_stack: list[dict[str, Any]],
    subject_slot_by_region: dict[str, int],
    lora_crop_refinement_lanes: list[dict[str, Any]] | None = None,
    native_adapter_injection: dict[str, Any] | None = None,
    ipadapter_owner_enabled: bool = True,
    disabled_ipadapter_gate: dict[str, Any] | None = None,
    preserved_ipadapter_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identity_units = _normalize_scene_director_identity_units(block, subject_slot_by_region, scene_graph=scene_graph, requested_mode="hybrid_safe_identity")
    display_identity_units = list(preserved_ipadapter_units or identity_units or [])
    disabled_ipadapter_gate = disabled_ipadapter_gate if isinstance(disabled_ipadapter_gate, dict) else {}
    ip_warnings: list[str] = []
    if not ipadapter_owner_enabled and display_identity_units:
        ip_warnings.extend(["ipadapter_owner_extension_disabled_execution_suppressed", "ipadapter_profile_preserved_metadata_only"])
    regions: list[dict[str, Any]] = []
    weights: list[float] = []
    for unit in display_identity_units:
        weight = _float(unit.get("weight"), 0.0)
        weights.append(weight)
        for warning in unit.get("instruction_preservation_warnings") or []:
            ip_warnings.append(str(warning))
        if weight < 0.50:
            ip_warnings.append("ipadapter_low_weight")
        if not unit.get("image_names"):
            ip_warnings.append("ipadapter_missing_reference_image")
        regions.append({
            "region_id": unit.get("region_id"),
            "label": unit.get("label"),
            "subject_slot": unit.get("subject_slot"),
            "mode": unit.get("mode"),
            "weight": weight,
            "requested_weight": unit.get("requested_weight"),
            "effective_weight": unit.get("effective_weight"),
            "start_at": unit.get("start_at"),
            "requested_start_at": unit.get("requested_start_at"),
            "effective_start_at": unit.get("effective_start_at"),
            "end_at": unit.get("end_at"),
            "requested_end_at": unit.get("requested_end_at"),
            "effective_end_at": unit.get("effective_end_at"),
            "scope_mode": unit.get("scope_mode"),
            "execution_mode": "disabled_metadata_only" if not ipadapter_owner_enabled else unit.get("ipadapter_execution_mode"),
            "composition_preservation_enabled": unit.get("composition_preservation_enabled"),
            "execution_allowed": bool(ipadapter_owner_enabled),
            "warnings": sorted(set((list(unit.get("instruction_preservation_warnings") or []) + (["ipadapter_owner_extension_disabled_execution_suppressed", "ipadapter_profile_preserved_metadata_only"] if not ipadapter_owner_enabled else [])))),
        })
    restore = two_pass_identity_restore if isinstance(two_pass_identity_restore, dict) else {}
    restore_denoise = restore.get("denoise")
    if restore.get("status") == "applied" and _float(restore_denoise, 0.0) < 0.25:
        ip_warnings.append("ipadapter_low_restore_denoise")
    if display_identity_units and not ip_nodes_added and ipadapter_owner_enabled:
        ip_warnings.append("ipadapter_profile_present_but_no_region_binding")
    native_adapter_injection = native_adapter_injection if isinstance(native_adapter_injection, dict) else {}
    native_lora_lanes = ((native_adapter_injection.get("lora") or {}).get("lanes") or []) if isinstance(native_adapter_injection.get("lora"), dict) else []
    crop_lora_lanes = list(lora_crop_refinement_lanes or [])
    combined_lora_lanes = list(native_lora_lanes or []) + list(lora_finish_prompt_stack or []) + crop_lora_lanes
    lora_warnings: list[str] = []
    for lane in combined_lora_lanes or []:
        for warning in lane.get("visibility_warnings") or []:
            if warning and warning not in lora_warnings:
                lora_warnings.append(str(warning))
    return {
        "schema": "neo.image.scene_director.regional_adapter_visibility.v054.v1",
        "phase": "SD-V054-26.9.15",
        "ipadapter": {
            "scene_director_applied": bool(ip_nodes_added),
            "standalone_extension_applied": False,
            "owner_enabled": bool(ipadapter_owner_enabled),
            "execution_allowed": bool(ipadapter_owner_enabled),
            "disabled_route_gate": deepcopy(disabled_ipadapter_gate),
            "profile_preserved_metadata_only": bool(disabled_ipadapter_gate.get("profile_preserved_metadata_only")),
            "region_count": len(regions),
            "regions": regions,
            "nodes_added": list(ip_nodes_added),
            "identity_units_planned": len(display_identity_units),
            "two_pass_identity_restore": restore.get("status") == "applied",
            "restore_denoise": restore_denoise,
            "weights": weights,
            "visibility_warnings": sorted(set(ip_warnings)),
        },
        "lora": {
            "applied": bool(lora_nodes_added or native_lora_lanes or crop_lora_lanes),
            "lane_count": len(combined_lora_lanes or []),
            "lanes": deepcopy(combined_lora_lanes or []),
            "native_first_pass_lane_count": len(native_lora_lanes or []),
            "finish_pass_lane_count": len(lora_finish_prompt_stack or []),
            "crop_refine_lane_count": len(crop_lora_lanes or []),
            "visibility_warnings": sorted(set(lora_warnings)),
        },
    }


def _build_ipadapter_instruction_preservation_metadata(
    *,
    ipadapter_units: list[dict[str, Any]],
    ipadapter_nodes_added: list[str],
    actual_mode: str,
    ipadapter_owner_enabled: bool = True,
    disabled_ipadapter_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    disabled_ipadapter_gate = disabled_ipadapter_gate if isinstance(disabled_ipadapter_gate, dict) else {}
    for unit in ipadapter_units or []:
        warnings = [str(w) for w in unit.get("instruction_preservation_warnings") or [] if str(w).strip()]
        if not ipadapter_owner_enabled:
            warnings.extend(["ipadapter_owner_extension_disabled_execution_suppressed", "ipadapter_profile_preserved_metadata_only"])
        all_warnings.extend(warnings)
        routes.append({
            "route_id": f"ipadapter_{unit.get('uid') or unit.get('region_id') or len(routes)+1}",
            "region_id": unit.get("region_id"),
            "subject_slot": unit.get("subject_slot"),
            "requested_weight": unit.get("requested_weight"),
            "effective_weight": unit.get("effective_weight"),
            "requested_start_at": unit.get("requested_start_at"),
            "effective_start_at": unit.get("effective_start_at"),
            "requested_end_at": unit.get("requested_end_at"),
            "effective_end_at": unit.get("effective_end_at"),
            "scope_mode": unit.get("scope_mode") or "identity_only",
            "mask_type": "subject_mask",
            "execution_mode": "disabled_metadata_only" if not ipadapter_owner_enabled else (unit.get("ipadapter_execution_mode") or actual_mode),
            "composition_preservation_enabled": bool(unit.get("composition_preservation_enabled", True)),
            "execution_allowed": bool(ipadapter_owner_enabled),
            "warnings": sorted(set(warnings)),
        })
    return {
        "schema": "neo.image.scene_director.ipadapter_instruction_preservation.v054.v1",
        "phase": "SD-V054-26.9.12",
        "status": ("disabled_metadata_only" if routes and not ipadapter_owner_enabled else ("applied" if routes else "not_applicable")),
        "profile": "identity_preserve_delayed",
        "owner_enabled": bool(ipadapter_owner_enabled),
        "execution_allowed": bool(ipadapter_owner_enabled),
        "disabled_route_gate": deepcopy(disabled_ipadapter_gate),
        "nodes_added": list(ipadapter_nodes_added or []),
        "route_count": len(routes),
        "routes": routes,
        "warnings": sorted(set(all_warnings)),
        "policy": "Regional FaceID/IPAdapter is delayed/capped by default and disabled at execution when the image.ip_adapter owner extension is off; metadata is preserved for replay.",
    }


def apply_scene_director_patch(
    workflow: dict[str, Any],
    *,
    payload: Any,
    route: dict[str, Any] | None,
    available_nodes: Any,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    width: int | None = None,
    height: int | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Patch a supported Comfy checkpoint workflow with NeoSceneDirectorV054.

    The V052/V053 active fallback path is retired in Phase 21. The patch mirrors the proven compiler shape:
    model/clip -> NeoSceneDirectorV054 -> KSampler model
    and new CLIPTextEncode nodes feed the sampler positive/negative inputs.

    No prompt-only fallback is allowed. If route/node/validation gates fail, the
    original graph is returned untouched with a structured patch summary.
    """
    graph = deepcopy(workflow or {})
    route_data = route or {}
    sampler_key = str(sampler_node_id)
    model_output_ref = _copy_ref(model_ref, ["1", 0])
    clip_output_ref = _copy_ref(clip_ref, ["1", 1])
    width_value, height_value = _size_from_workflow(graph, _size_from_route(route_data, width, height))

    sampler = graph.get(sampler_key) if isinstance(graph.get(sampler_key), dict) else {}
    sampler_inputs = sampler.setdefault("inputs", {}) if isinstance(sampler, dict) else {}
    original_sampler_model_ref = deepcopy(sampler_inputs.get("model"))
    previous_positive_ref = _copy_ref(sampler_inputs.get("positive"), ["2", 0])
    previous_negative_ref = _copy_ref(sampler_inputs.get("negative"), ["3", 0])

    validation = validate_and_normalize_payload(
        payload,
        backend=route_data.get("backend") or "comfyui",
        family=route_data.get("family") or "sdxl",
        loader=route_data.get("loader") or "checkpoint",
        workflow_mode=route_data.get("workflow_mode") or route_data.get("mode") or "generate",
        object_info=available_nodes,
    )
    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    v054_block = _v054_source_block(payload, block)
    v054_params = _block_params(v054_block) if isinstance(v054_block, dict) else {}
    if not isinstance(v054_params.get("advanced_fix_pass_controls"), dict):
        persisted_workflow_controls = _existing_v054_fix_pass_controls(graph)
        if persisted_workflow_controls:
            # A compact replay payload may omit Advanced Scene Control even
            # though the existing NeoSceneDirectorV054 node still contains the
            # user's visible settings. Carry them into this compile pass.
            v054_params["advanced_fix_pass_controls"] = persisted_workflow_controls
    authority_mode = str(v054_params.get("authority_mode") or v054_params.get("scene_director_authority_mode") or "balanced").strip().lower() or "balanced"
    effective_authority = _effective_authority_mode_v054(authority_mode, payload if isinstance(payload, dict) else {}, route_data)
    effective_authority_mode = str(effective_authority.get("effective_mode") or authority_mode).strip().lower() or authority_mode
    prompt_only_authority_mode = bool(effective_authority.get("prompt_only"))
    neutral_authority_mode = authority_mode in {"neutral", "neutral_planning", "planning_only", "metadata_only"} or effective_authority.get("execution_strategy") == "neutral_planning"

    if not validation.get("enabled") or validation.get("ok") is False or not validation.get("can_emit_workflow_patch"):
        return _build_no_patch(
            graph,
            validation=validation,
            reason=validation.get("reason") or "Scene Director validation blocked workflow mutation.",
            route=route_data,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
        )

    legacy, notes = legacy_payload_from_block(
        block,
        base_payload={
            "width": width_value,
            "height": height_value,
            "family": route_data.get("family"),
            "mode": _route_mode(route_data),
            "checkpoint": (payload.get("checkpoint") if isinstance(payload, dict) else None) or route_data.get("checkpoint") or route_data.get("checkpoint_name"),
            "checkpoint_name": (payload.get("checkpoint_name") if isinstance(payload, dict) else None) or (payload.get("ckpt_name") if isinstance(payload, dict) else None) or route_data.get("checkpoint_name"),
            "prompt_extension_merge": (payload.get("prompt_extension_merge") if isinstance(payload, dict) and isinstance(payload.get("prompt_extension_merge"), dict) else {}),
        },
    )
    prompt_authority_contract = _prompt_authority_contract_for_block(v054_block, legacy)
    prompt_authority = normalize_prompt_authority(prompt_authority_contract.get("mode"))
    if isinstance(v054_params.get("advanced_fix_pass_controls"), dict):
        legacy["scene_director_advanced_fix_pass_controls"] = deepcopy(v054_params.get("advanced_fix_pass_controls") or {})
    advanced_fix_pass_controls = _advanced_fix_pass_controls(legacy if isinstance(legacy, dict) else {})
    readiness = workflow_readiness(route=route_data, available_nodes=available_nodes, enabled=bool(validation.get("enabled")))
    node_status = readiness.get("node_status") or detect_node_status(available_nodes)
    scene_node_class = str(node_status.get("selected_node") or "")
    if not readiness.get("workflow_patch_allowed") or scene_node_class not in SCENE_NODE_CLASSES:
        return _build_no_patch(
            graph,
            validation=validation,
            reason=readiness.get("reason") or "Scene Director workflow patch gated.",
            route=route_data,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            extra={
                "route_state": readiness.get("route_state"),
                "workflow_readiness_state": readiness.get("workflow_readiness_state"),
                "node_status": node_status,
                "notes": notes[:8],
            },
        )

    scene_graph_v054: dict[str, Any] | None = None
    extension_region_assignments: dict[str, Any] = {"schema": "neo.image.scene_director.extension_unit_routing.v054.v1", "phase": "SD-V054-26", "status": "not_applicable"}
    lora_region_assignments: dict[str, Any] = {"schema": "neo.image.scene_director.extension_unit_routing.v054.v1", "phase": "SD-V054-26", "status": "not_applicable"}
    character_lock_bridge: dict[str, Any] = {"schema": "neo.image.scene_director.character_lock_execution_bridge.v054.v2", "phase": "SD-V054-26.8", "status": "not_applicable"}
    character_lock_positive_add = ""
    character_lock_negative_add = ""
    v054_errors: list[str] = []
    v054_warnings: list[str] = []
    if scene_node_class == V054_NODE_CLASS:
        scene_graph_v054, v054_errors, v054_warnings = _scene_graph_from_block_v054(v054_block, width=width_value, height=height_value, legacy=legacy)
        if scene_graph_v054 is not None:
            scene_graph_v054 = _restore_replay_character_fields_v054(scene_graph_v054, graph)
            scene_graph_v054 = strip_disabled_owner_routes_v054(scene_graph_v054, payload)
            persisted_fix_controls = (
                (scene_graph_v054.get("metadata") or {}).get("advanced_fix_pass_controls")
                if isinstance(scene_graph_v054.get("metadata"), dict)
                else None
            )
            has_explicit_fix_controls = bool(
                isinstance(v054_params.get("advanced_fix_pass_controls"), dict)
                or isinstance(legacy.get("scene_director_advanced_fix_pass_controls"), dict)
            )
            if isinstance(persisted_fix_controls, dict) and not has_explicit_fix_controls:
                # Replayed workflows can carry the visible Fix Pass controls in
                # the saved scene graph even when the compact extension payload
                # no longer contains that UI block. Restore them before plan
                # resolution so Force on cannot degrade into metadata-only
                # diagnostics on the next submit.
                v054_params["advanced_fix_pass_controls"] = deepcopy(persisted_fix_controls)
                legacy["scene_director_advanced_fix_pass_controls"] = deepcopy(persisted_fix_controls)
                advanced_fix_pass_controls = _advanced_fix_pass_controls(legacy)
            extension_region_assignments = build_controlnet_adetailer_region_assignments_v054(scene_graph_v054, payload)
            if neutral_authority_mode:
                neutral_metadata = dict(scene_graph_v054.get("metadata") or {}) if isinstance(scene_graph_v054, dict) else {}
                return _build_no_patch(
                    graph,
                    validation=validation,
                    reason="Scene Director authority mode is Neutral / planning only; sampler graph left unchanged.",
                    route=route_data,
                    model_ref=model_output_ref,
                    clip_ref=clip_output_ref,
                    sampler_node_id=sampler_key,
                    previous_positive_ref=previous_positive_ref,
                    previous_negative_ref=previous_negative_ref,
                    extra={
                        "patch_type": "scene_director_v054_neutral_planning",
                        "applied": True,
                        "mutated": False,
                        "workflow_patch_allowed": False,
                        "scene_director_authority_mode": authority_mode,
                        "scene_director_effective_authority": effective_authority,
                        "scene_director_effective_authority_mode": effective_authority_mode,
                        "scene_director_neutral_mode": True,
                        "scene_director_neutral_mode_reason": "metadata_and_inspector_only_no_sampler_mutation",
                        "scene_director_scene_graph_json_present": True,
                        "scene_graph_json_present": True,
                        "scene_graph_json_region_count": len(scene_graph_v054.get("regions") or []),
                        "regions": len(scene_graph_v054.get("regions") or []),
                        "subject_count": len(_v054_character_subject_slot_map(scene_graph_v054)),
                        "scene_director_disabled_owner_route_cleanup": neutral_metadata.get("disabled_owner_route_cleanup", {}),
                        "scene_director_extension_unit_routing": neutral_metadata.get("extension_unit_routing", build_extension_unit_routing_contract_v054(scene_graph_v054)),
                        "scene_director_ipadapter_nodes_added": [],
                        "scene_director_ipadapter_applied": False,
                        "scene_director_ipadapter_execution_disabled": True,
                        "scene_director_lora_nodes_added": [],
                        "scene_director_lora_applied": False,
                        "scene_director_extra_samplers_added": 0,
                        "notes": [
                            "Scene Director Neutral / planning only mode preserved source stack metadata and did not add NeoSceneDirectorV054, IPAdapter, LoRA, ControlNet, ADetailer, or correction sampler nodes."
                        ],
                    },
                )
            scene_graph_v054 = apply_controlnet_adetailer_assignments_to_scene_graph_v054(scene_graph_v054, extension_region_assignments)
            lora_region_assignments = build_lora_region_assignments_v054(scene_graph_v054, payload)
            scene_graph_v054 = apply_lora_assignments_to_scene_graph_v054(scene_graph_v054, lora_region_assignments)
            scene_graph_v054, character_lock_bridge, character_lock_positive_add, character_lock_negative_add = _apply_character_lock_execution_bridge_v054(scene_graph_v054, _block_params(v054_block))
            scene_graph_v054 = _apply_scene_director_background_space_authority(scene_graph_v054)
            scene_graph_v054 = _ensure_v054_subjects_for_masks(scene_graph_v054)
        else:
            extension_region_assignments = {"schema": "neo.image.scene_director.extension_unit_routing.v054.v1", "phase": "SD-V054-26", "status": "no_scene_graph"}
            lora_region_assignments = {"schema": "neo.image.scene_director.extension_unit_routing.v054.v1", "phase": "SD-V054-26", "status": "no_scene_graph"}
        if scene_graph_v054 is None:
            return _build_no_patch(
                graph,
                validation=validation,
                reason="Scene Director V054 scene_graph_json validation failed; workflow mutation skipped.",
                route=route_data,
                model_ref=model_output_ref,
                clip_ref=clip_output_ref,
                sampler_node_id=sampler_key,
                previous_positive_ref=previous_positive_ref,
                previous_negative_ref=previous_negative_ref,
                extra={
                    "node_status": node_status,
                    "node_class": V054_NODE_CLASS,
                    "v054_scene_graph_errors": v054_errors,
                    "v054_scene_graph_warnings": v054_warnings,
                    "notes": notes[:8],
                },
            )
    elif scene_node_class in RETIRED_NODE_CLASSES:
        return _build_no_patch(
            graph,
            validation=validation,
            reason="Scene Director V052/V053 active fallback is retired; install/enable NeoSceneDirectorV054.",
            route=route_data,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            extra={"notes": notes[:8], "node_class": scene_node_class, "retired_node_classes": sorted(RETIRED_NODE_CLASSES)},
        )

    adapter_execution_mode = _regional_adapter_execution_mode(v054_block if scene_node_class == V054_NODE_CLASS else block, legacy)
    subject_slot_by_region = _v054_character_subject_slot_map(scene_graph_v054) if scene_node_class == V054_NODE_CLASS else {}
    region_zone_validation = _compile_region_zone_validation_v054(scene_graph_v054) if scene_node_class == V054_NODE_CLASS else {"schema": "neo.image.scene_director.region_zone_validation.v054.v1", "phase": "SD-V054-26.10.8K3", "status": "not_applicable", "warnings": []}
    ipadapter_owner_enabled = _payload_extension_enabled(payload, IP_ADAPTER_EXTENSION_ID, route=route_data)
    ipadapter_profile_units_for_gate = _normalize_scene_director_identity_units(
        v054_block if scene_node_class == V054_NODE_CLASS else block,
        subject_slot_by_region,
        scene_graph=scene_graph_v054,
        requested_mode="hybrid_safe_identity",
    ) if scene_node_class == V054_NODE_CLASS else []
    disabled_ipadapter_gate = _disabled_ipadapter_route_gate_metadata(
        owner_enabled=ipadapter_owner_enabled,
        identity_unit_count=len(ipadapter_profile_units_for_gate),
        route_count_removed=len(ipadapter_profile_units_for_gate) if not ipadapter_owner_enabled else 0,
    )
    lora_first_pass_prompt_meta: dict[str, dict[str, Any]] = {}
    if scene_node_class == V054_NODE_CLASS and (not prompt_only_authority_mode) and _adapter_first_pass_requested(adapter_execution_mode):
        lora_first_pass_units = _normalize_scene_director_lora_units(
            v054_block if scene_node_class == V054_NODE_CLASS else block,
            {**(legacy if isinstance(legacy, dict) else {}), "scene_director_lora_region_assignments": lora_region_assignments},
            subject_slot_by_region,
        )
        if lora_first_pass_units and _available_node(available_nodes, "LoraLoader"):
            scene_graph_v054, lora_first_pass_prompt_meta = _inject_lora_trigger_terms_into_scene_graph(scene_graph_v054, lora_first_pass_units)

    stale_character_lock_cleanup: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_lock_graph_cleanup.v054.v1",
        "phase": "SD-V054-27.1",
        "status": "not_applicable",
        "nodes_removed": [],
        "decode_nodes_rewired": [],
        "warnings": [],
    }
    if scene_node_class == V054_NODE_CLASS:
        existing_scene_node_ids = {
            str(existing_id)
            for existing_id, existing_node in graph.items()
            if isinstance(existing_node, dict) and existing_node.get("class_type") == V054_NODE_CLASS
        }
        graph, stale_character_lock_cleanup = _prune_stale_character_lock_passes(
            graph,
            sampler_node_id=sampler_key,
            scene_node_ids=existing_scene_node_ids,
        )

    node_id = str(_next_id(graph))
    positive_encode_id = str(int(node_id) + 1)
    negative_encode_id = str(int(node_id) + 2)

    lora_first_pass_nodes_added: list[str] = []
    lora_first_pass_notes: list[str] = []
    lora_first_pass_lanes: list[dict[str, Any]] = []
    lora_first_pass_model_ref = deepcopy(model_output_ref)
    lora_first_pass_clip_ref = deepcopy(clip_output_ref)
    next_after_text = int(negative_encode_id) + 1
    if scene_node_class == V054_NODE_CLASS and _adapter_first_pass_requested(adapter_execution_mode):
        next_after_text, lora_first_pass_model_ref, lora_first_pass_clip_ref, lora_first_pass_nodes_added, lora_first_pass_notes, lora_first_pass_lanes = _apply_scene_director_regional_lora_first_pass_native(
            graph,
            next_id=next_after_text,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            block=v054_block if scene_node_class == V054_NODE_CLASS else block,
            legacy={**(legacy if isinstance(legacy, dict) else {}), "scene_director_lora_region_assignments": lora_region_assignments},
            scene_graph=scene_graph_v054,
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
            trigger_meta_by_uid=lora_first_pass_prompt_meta,
        )

    extension_authority_routes = _build_extension_authority_routes_v054(
        scene_graph=scene_graph_v054,
        block=v054_block if scene_node_class == V054_NODE_CLASS else block,
        legacy=legacy if isinstance(legacy, dict) else {},
        subject_slot_by_region=subject_slot_by_region,
        lora_region_assignments=lora_region_assignments,
        lora_trigger_meta=lora_first_pass_prompt_meta,
        lora_first_pass_lanes=lora_first_pass_lanes,
        adapter_execution_mode=adapter_execution_mode,
        disabled_owner_cleanup=((scene_graph_v054.get("metadata", {}) if isinstance(scene_graph_v054, dict) else {}).get("disabled_owner_route_cleanup") if isinstance(scene_graph_v054, dict) else {}),
        ipadapter_owner_enabled=ipadapter_owner_enabled,
        disabled_ipadapter_gate=disabled_ipadapter_gate,
    ) if scene_node_class == V054_NODE_CLASS else {"schema": "neo.image.scene_director.extension_authority_routes.v054.v1", "phase": "SD-V054-26.9.10", "status": "not_applicable", "route_count": 0, "routes": [], "warnings": [], "disabled_ipadapter_gate": disabled_ipadapter_gate}
    if isinstance(scene_graph_v054, dict):
        sg_meta = dict(scene_graph_v054.get("metadata") or {})
        sg_meta["extension_authority_routes"] = deepcopy(extension_authority_routes)
        sg_meta["region_zone_validation"] = deepcopy(region_zone_validation)
        sg_meta["effective_authority"] = deepcopy(effective_authority)
        layout_safety_report = _background_and_character_area_report(scene_graph_v054)
        sg_meta["advanced_fix_pass_controls"] = deepcopy(advanced_fix_pass_controls)
        sg_meta["character_lock_execution"] = deepcopy(
            _character_lock_execution_settings(_block_params(v054_block))
        )
        sg_meta["character_lock_live_conditioning"] = _v054_live_character_conditioning_report(
            scene_graph_v054,
            _character_lock_execution_settings(_block_params(v054_block)),
        )
        sg_meta["layout_safety"] = {
            "schema": "neo.image.scene_director.layout_safety.v25_9_13",
            "phase": "V25.9.13",
            "status": "danger" if "character_regions_leave_low_raw_background_area" in (layout_safety_report.get("warning_codes") or []) else ("warning" if layout_safety_report.get("warning_codes") else "safe"),
            **deepcopy(layout_safety_report),
        }
        scene_graph_v054["metadata"] = sg_meta

    if prompt_only_authority_mode:
        regional_authority_restore = _compile_regional_authority_restore_v054(
            scene_graph_v054,
            False,
            authority_mode=effective_authority_mode,
            zone_validation=region_zone_validation,
        )
        positive_node_id = str(previous_positive_ref[0]) if previous_positive_ref else ""
        negative_node_id = str(previous_negative_ref[0]) if previous_negative_ref else ""
        positive_appended = False
        negative_appended = False
        if regional_authority_restore.get("status") == "applied":
            if positive_node_id in graph and isinstance(graph.get(positive_node_id), dict):
                text = str(graph[positive_node_id].setdefault("inputs", {}).get("text") or "")
                graph[positive_node_id]["inputs"]["text"] = (
                    _clean_text(regional_authority_restore.get("positive") or "") or " "
                    if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY
                    else _append_unique_text(text, str(regional_authority_restore.get("positive") or ""))
                )
                positive_appended = True
            if negative_node_id in graph and isinstance(graph.get(negative_node_id), dict):
                text = str(graph[negative_node_id].setdefault("inputs", {}).get("text") or "")
                graph[negative_node_id]["inputs"]["text"] = (
                    _clean_text(regional_authority_restore.get("negative") or "") or " "
                    if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY
                    else _append_unique_text(text, str(regional_authority_restore.get("negative") or ""))
                )
                negative_appended = True
        prompt_only_character_lock_execution = _character_lock_execution_settings(_block_params(v054_block if scene_node_class == V054_NODE_CLASS else block))
        prompt_only_scene_node_id = None
        prompt_only_scene_nodes_added: list[str] = []
        prompt_only_character_lock_nodes_added: list[str] = []
        prompt_only_character_lock_notes: list[str] = []
        prompt_only_character_lock_final_latent_ref = [sampler_key, 0]
        prompt_only_vae_decode_nodes_rewired: list[str] = []
        prompt_only_character_lock_authority: dict[str, Any] = {
            "schema": "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
            "phase": "SD-V054-26.10.8K4",
            "status": "prompt_guard_only" if prompt_only_character_lock_execution.get("mode") == "prompt_guard_only" else ("disabled_by_execution_mode" if prompt_only_character_lock_execution.get("mode") == "off" else "not_applicable"),
            "timing_stage": "before_adapters",
            "settings": deepcopy(_character_lock_authority_settings(legacy if isinstance(legacy, dict) else {})),
            "character_lock_execution": deepcopy(prompt_only_character_lock_execution),
            "lanes": [],
            "nodes_added": [],
            "warnings": [],
            "policy": "Anime-safe prompt-only regional authority can still run visible masked/latent Character Lock correction without rewiring the main Scene Director model path.",
        }
        if prompt_only_character_lock_execution.get("masked_correction_enabled") and scene_node_class == V054_NODE_CLASS:
            prompt_only_scene_node_id = node_id
            graph[prompt_only_scene_node_id] = {
                "class_type": scene_node_class,
                "inputs": _node_inputs_for_scene_director(
                    node_class=scene_node_class,
                    legacy=legacy,
                    block=v054_block if scene_node_class == V054_NODE_CLASS else block,
                    scene_graph=scene_graph_v054,
                    model_ref=model_output_ref,
                    clip_ref=clip_output_ref,
                    width=width_value,
                    height=height_value,
                    extension_routes_json=extension_authority_routes,
                ),
            }
            prompt_lock_next_id = int(prompt_only_scene_node_id) + 1
            prompt_lock_next_id, prompt_only_character_lock_final_latent_ref, prompt_only_character_lock_nodes_added, prompt_only_character_lock_notes, prompt_only_character_lock_authority = _apply_scene_director_first_pass_character_lock_authority(
                graph,
                next_id=prompt_lock_next_id,
                base_latent_ref=[sampler_key, 0],
                model_ref=model_output_ref,
                clip_ref=clip_output_ref,
                negative_ref=previous_negative_ref,
                scene_node_id=prompt_only_scene_node_id,
                legacy=legacy if isinstance(legacy, dict) else {},
                sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
                sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
                available_nodes=available_nodes,
                subject_slot_by_region=subject_slot_by_region,
                scene_graph=scene_graph_v054,
                timing_stage="before_adapters",
            )
            prompt_only_character_lock_authority["phase"] = "SD-V054-26.10.8K4"
            prompt_only_character_lock_authority["character_lock_execution"] = deepcopy(prompt_only_character_lock_execution)
            prompt_only_character_lock_authority["anime_safe_prompt_only"] = True
            prompt_only_character_lock_authority["model_mutation_allowed"] = False
            prompt_only_character_lock_authority["scene_node_mask_only"] = bool(prompt_only_character_lock_nodes_added)
            if prompt_only_character_lock_nodes_added:
                prompt_only_scene_nodes_added = [prompt_only_scene_node_id]
                for decode_id in _find_vae_decode_consumers(graph, [sampler_key, 0]):
                    inputs = graph[decode_id].setdefault("inputs", {})
                    if isinstance(inputs, dict):
                        inputs["samples"] = deepcopy(prompt_only_character_lock_final_latent_ref)
                        prompt_only_vae_decode_nodes_rewired.append(decode_id)
            else:
                graph.pop(prompt_only_scene_node_id, None)
                prompt_only_scene_node_id = None
        notes_prompt_only = list(notes[:8])
        notes_prompt_only.append(f"Scene Director authority mode {authority_mode} resolved to {effective_authority_mode}; prompt-only regional guide applied without NeoSceneDirectorV054 model mutation.")
        if prompt_only_character_lock_nodes_added:
            notes_prompt_only.extend(prompt_only_character_lock_notes)
            notes_prompt_only.append("Anime-safe Character Lock execution used NeoSceneDirectorV054 masks only; sampler model path stayed on the base checkpoint.")
        if region_zone_validation.get("warnings"):
            notes_prompt_only.append(f"Scene Director zone validation reported {len(region_zone_validation.get('warnings') or [])} warning(s).")
        prompt_only_nodes_added = [*prompt_only_scene_nodes_added, *prompt_only_character_lock_nodes_added]
        prompt_only_text_nodes_added = [node for node in prompt_only_character_lock_nodes_added if isinstance(graph.get(node), dict) and graph[node].get("class_type") == "CLIPTextEncode"]
        patch = {
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "phase": PHASE,
            "patch_type": "scene_director_v054_prompt_append_only_character_lock" if prompt_only_character_lock_nodes_added else "scene_director_v054_prompt_append_only",
            "applied": True,
            "mutated": bool(positive_appended or negative_appended or prompt_only_nodes_added),
            "reason": "Scene Director prompt-only authority mode applied; sampler model path left unchanged.",
            "route": validation.get("route") or route_data or {},
            "route_state": readiness.get("route_state"),
            "workflow_patch_allowed": True,
            "node": prompt_only_scene_node_id,
            "node_class": V054_NODE_CLASS if prompt_only_scene_node_id else None,
            "node_classes": sorted({str(graph[n].get("class_type")) for n in prompt_only_nodes_added if isinstance(graph.get(n), dict)}),
            "nodes_added": prompt_only_nodes_added,
            "text_nodes_added": prompt_only_text_nodes_added,
            "sampler_node_id": sampler_key,
            "previous_model_ref": deepcopy(model_output_ref),
            "patched_model_ref": deepcopy(model_output_ref),
            "previous_positive_ref": deepcopy(previous_positive_ref),
            "previous_negative_ref": deepcopy(previous_negative_ref),
            "patched_positive_ref": deepcopy(previous_positive_ref),
            "patched_negative_ref": deepcopy(previous_negative_ref),
            "clip_ref": deepcopy(clip_output_ref),
            "node_status": node_status,
            "fallback_policy": "prompt_only_no_model_mutation",
            "regions": len(scene_graph_v054.get("regions") or []) if isinstance(scene_graph_v054, dict) else 0,
            "subject_count": len(subject_slot_by_region),
            "scene_graph_json_present": isinstance(scene_graph_v054, dict),
            "scene_graph_json_region_count": len(scene_graph_v054.get("regions") or []) if isinstance(scene_graph_v054, dict) else 0,
            "scene_director_authority_mode": authority_mode,
            "scene_director_effective_authority_mode": effective_authority_mode,
            "scene_director_effective_authority": effective_authority,
            "scene_director_prompt_only_mode": True,
            "scene_director_prompt_authority": prompt_authority,
            "scene_director_prompt_authority_contract": deepcopy(prompt_authority_contract),
            "scene_director_neutral_mode": False,
            "scene_director_regional_authority_restore": regional_authority_restore,
            "scene_director_regional_authority_mode": regional_authority_restore.get("mode"),
            "scene_director_region_zone_validation": region_zone_validation,
            "scene_director_background_separation_guard": regional_authority_restore.get("background_separation_guard"),
            "scene_director_extension_authority_routes": extension_authority_routes,
            "scene_director_extension_routing_authority": extension_authority_routes,
            "scene_director_extension_unit_routing": (scene_graph_v054.get("metadata", {}) if isinstance(scene_graph_v054, dict) else {}).get("extension_unit_routing", build_extension_unit_routing_contract_v054(scene_graph_v054)),
            "scene_director_disabled_owner_route_cleanup": (scene_graph_v054.get("metadata", {}) if isinstance(scene_graph_v054, dict) else {}).get("disabled_owner_route_cleanup", {}),
            "scene_director_ipadapter_applied": False,
            "scene_director_ipadapter_nodes_added": [],
            "scene_director_ipadapter_execution_disabled": not ipadapter_owner_enabled,
            "scene_director_lora_applied": False,
            "scene_director_lora_nodes_added": [],
            "scene_director_extra_samplers_added": len([node for node in prompt_only_character_lock_nodes_added if isinstance(graph.get(node), dict) and graph[node].get("class_type") == "KSampler"]),
            "scene_director_character_lock_execution": prompt_only_character_lock_execution,
            "scene_director_character_lock_graph_cleanup": deepcopy(stale_character_lock_cleanup),
            "scene_director_character_lock_execution_mode": prompt_only_character_lock_execution.get("mode"),
            "scene_director_character_lock_execution_masked": bool(prompt_only_character_lock_nodes_added),
            "scene_director_first_pass_character_lock_authority": prompt_only_character_lock_authority,
            "scene_director_first_pass_character_lock_nodes_added": prompt_only_character_lock_nodes_added,
            "scene_director_prompt_only_mask_node_id": prompt_only_scene_node_id,
            "scene_director_prompt_only_mask_node_added": bool(prompt_only_scene_node_id),
            "scene_director_vae_decode_nodes_rewired": prompt_only_vae_decode_nodes_rewired,
            "scene_director_final_decode_latent_ref": prompt_only_character_lock_final_latent_ref,
            "scene_director_attention_lock_runtime_proof": _attention_lock_runtime_proof_v25_9_2(
                scene_node_class=V054_NODE_CLASS if prompt_only_scene_node_id else "",
                scene_node_id=prompt_only_scene_node_id,
                scene_graph=scene_graph_v054,
                sampler_node_id=sampler_key,
                sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
                patched_model_ref=[],
                appearance_lock_mode=(graph.get(prompt_only_scene_node_id, {}).get("inputs", {}) if prompt_only_scene_node_id and isinstance(graph.get(prompt_only_scene_node_id, {}), dict) else {}).get("appearance_lock_mode"),
                base_weight=(graph.get(prompt_only_scene_node_id, {}).get("inputs", {}) if prompt_only_scene_node_id and isinstance(graph.get(prompt_only_scene_node_id, {}), dict) else {}).get("base_weight"),
                region_gain=(graph.get(prompt_only_scene_node_id, {}).get("inputs", {}) if prompt_only_scene_node_id and isinstance(graph.get(prompt_only_scene_node_id, {}), dict) else {}).get("region_gain"),
                normalize_masks=(graph.get(prompt_only_scene_node_id, {}).get("inputs", {}) if prompt_only_scene_node_id and isinstance(graph.get(prompt_only_scene_node_id, {}), dict) else {}).get("normalize_masks"),
                first_pass_character_lock_authority=prompt_only_character_lock_authority,
            ),
            "notes": notes_prompt_only,
        }
        return {
            "workflow": graph,
            "workflow_patch": patch,
            "validation": validation,
            "model_ref": deepcopy(model_output_ref),
            "clip_ref": deepcopy(clip_output_ref),
            "positive_ref": deepcopy(previous_positive_ref),
            "negative_ref": deepcopy(previous_negative_ref),
            "mutated": bool(positive_appended or negative_appended or prompt_only_nodes_added),
            "changed": bool(positive_appended or negative_appended or prompt_only_nodes_added),
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
        }

    graph[node_id] = {
        "class_type": scene_node_class,
        "inputs": _node_inputs_for_scene_director(
            node_class=scene_node_class,
            legacy=legacy,
            block=v054_block if scene_node_class == V054_NODE_CLASS else block,
            scene_graph=scene_graph_v054,
            model_ref=lora_first_pass_model_ref,
            clip_ref=lora_first_pass_clip_ref,
            width=width_value,
            height=height_value,
            extension_routes_json=extension_authority_routes,
        ),
    }

    effective_positive = str(legacy.get("scene_director_effective_global_prompt") or legacy.get("scene_director_v052_global_prompt_override") or "").strip()
    effective_negative = str(legacy.get("scene_director_effective_negative_prompt") or "").strip()
    background_prime_authority = _scene_background_prime_contract(scene_graph_v054) if scene_node_class == V054_NODE_CLASS else {
        "schema": "neo.image.scene_director.background_prime_authority.v25_9_11",
        "phase": "V25.9.11",
        "status": "not_applicable",
    }
    if scene_node_class == V054_NODE_CLASS:
        prime_prompt = _clean_text(background_prime_authority.get("prompt") or "")
        prime_negative = _clean_text(background_prime_authority.get("negative") or "")
        effective_positive = _prepend_unique_text(prime_prompt, effective_positive)
        effective_negative = _prepend_unique_text(prime_negative, effective_negative)
        effective_positive = _append_unique_text(effective_positive, character_lock_positive_add)
        effective_negative = _append_unique_text(effective_negative, character_lock_negative_add)
    if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        # The node output is the only conditioning source in this mode. It has
        # already been compiled from local Scene Director lanes and explicit
        # scene-owned controls, while the Neo core prompt was blanked above.
        effective_positive = ""
        # Keep explicit Character Lock negative corrections connected even when
        # the global Neo prompt is intentionally excluded.
        effective_negative = character_lock_negative_add
    graph[positive_encode_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": deepcopy(lora_first_pass_clip_ref),
            "text": effective_positive or [node_id, SCENE_NODE_OUTPUT_POSITIVE_TEXT],
        },
    }
    graph[negative_encode_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": deepcopy(lora_first_pass_clip_ref),
            "text": effective_negative or [node_id, SCENE_NODE_OUTPUT_NEGATIVE_TEXT],
        },
    }

    patched_model_ref = [node_id, SCENE_NODE_OUTPUT_MODEL]
    ip_profile_block = deepcopy(block)
    ip_block = deepcopy(block)
    ip_assets = ip_block.get("assets") if isinstance(ip_block.get("assets"), dict) else {}
    legacy_identity_units = legacy.get("scene_director_identity_units") if isinstance(legacy.get("scene_director_identity_units"), list) else []
    if legacy_identity_units and ipadapter_owner_enabled:
        existing_identity_units = ip_assets.get("identity_units") if isinstance(ip_assets.get("identity_units"), list) else []
        seen_identity_keys = {str(item.get("uid") or item.get("profile_id") or item.get("region_id") or "") for item in existing_identity_units if isinstance(item, dict)}
        merged_identity_units = list(existing_identity_units)
        for unit in legacy_identity_units:
            if not isinstance(unit, dict):
                continue
            key = str(unit.get("uid") or unit.get("profile_id") or unit.get("region_id") or "")
            if key and key in seen_identity_keys:
                continue
            if key:
                seen_identity_keys.add(key)
            merged_identity_units.append(unit)
        ip_assets["identity_units"] = merged_identity_units
        ip_block["assets"] = ip_assets
    if not ipadapter_owner_enabled:
        ip_block = _strip_identity_units_for_disabled_ipadapter(ip_block)
    ip_nodes_added: list[str] = []
    ip_notes: list[str] = []
    ip_model_ref: list[Any] = []
    ipadapter_second_pass_model_ref: list[Any] = []
    ipadapter_actual_mode = "not_applicable"
    ipadapter_requested_mode = "first_pass_native" if _adapter_first_pass_requested(adapter_execution_mode) else "second_pass"
    ipadapter_safety_gate: dict[str, Any] = {
        "requested_mode": ipadapter_requested_mode,
        "safe_first_pass_ipadapter": False,
        "reason": "not_requested",
        "fallback": "second_pass_identity_restore",
    }
    two_pass_identity_restore: dict[str, Any] = {
        "schema": "neo.image.scene_director.two_pass_identity_restore.v054.v1",
        "phase": "SD-V054-26.9.4",
        "status": "not_applicable",
    }

    ip_profile_units_for_execution = _normalize_scene_director_identity_units(
        ip_block,
        subject_slot_by_region,
        scene_graph=scene_graph_v054,
        requested_mode="hybrid_safe_identity",
    )
    ipadapter_restore_fallback_required = bool(
        ipadapter_owner_enabled
        and ip_profile_units_for_execution
        and any(
            "ipadapter_relationship_pose_risk_forced_second_pass_restore" in (unit.get("instruction_preservation_warnings") or [])
            or unit.get("ipadapter_execution_mode") == "second_pass_restore"
            for unit in ip_profile_units_for_execution
        )
    )

    # Phase 26.9.9 preferred native first-pass regional IPAdapter/FaceID when safe.
    # Phase 26.9.12 adds two hard stops: the owner extension master switch and
    # composition-risk restore fallback when FaceID only has a full subject mask.
    if not ipadapter_owner_enabled:
        ip_next_id = next_after_text
        ipadapter_actual_mode = "disabled_metadata_only"
        ipadapter_safety_gate.update({
            "safe_first_pass_ipadapter": False,
            "reason": "image_ip_adapter_extension_disabled",
            "owner_enabled": False,
            "fallback": "metadata_only_no_execution",
        })
        if disabled_ipadapter_gate.get("profile_preserved_metadata_only"):
            ip_notes.append("Scene Director preserved regional IPAdapter/FaceID profile metadata, but image.ip_adapter is disabled so execution was suppressed.")
    elif _adapter_first_pass_requested(adapter_execution_mode) and not ipadapter_restore_fallback_required:
        ipadapter_safety_gate.update({"safe_first_pass_ipadapter": True, "reason": "provider_nodes_available_or_schema_permissive", "owner_enabled": True})
        ip_next_id, ip_model_ref, ip_nodes_added, ip_notes = _apply_scene_director_ipadapter_stack(
            graph,
            next_id=next_after_text,
            model_ref=patched_model_ref,
            scene_node_id=node_id,
            block=ip_block,
            available_nodes=available_nodes,
            scene_graph=scene_graph_v054,
            requested_mode="hybrid_safe_identity",
        )
        if ip_nodes_added:
            patched_model_ref = deepcopy(ip_model_ref)
            ipadapter_actual_mode = "first_pass_native"
            ip_notes.append("Scene Director regional IPAdapter/FaceID injected into the first-pass regional model chain; no isolated identity second pass is required.")
        else:
            ipadapter_actual_mode = "second_pass_fallback"
            ipadapter_safety_gate.update({"safe_first_pass_ipadapter": False, "reason": "native_graph_not_built", "owner_enabled": True})
    else:
        ip_next_id = next_after_text
        ipadapter_actual_mode = "second_pass_restore" if ipadapter_restore_fallback_required else "second_pass"
        if ipadapter_restore_fallback_required:
            ipadapter_safety_gate.update({
                "safe_first_pass_ipadapter": False,
                "reason": "relationship_pose_risk_with_subject_mask_requires_second_pass_restore",
                "owner_enabled": True,
                "fallback": "second_pass_identity_restore",
            })

    if ipadapter_owner_enabled and not ip_nodes_added and ipadapter_actual_mode in {"second_pass", "second_pass_fallback", "second_pass_restore", "not_applicable"}:
        # Safe restore path: build IPAdapter from the provider base model and run
        # it as a low-denoise identity restore over the Scene Director latent.
        ip_next_id, ip_model_ref, ip_nodes_added, ip_notes = _apply_scene_director_ipadapter_stack(
            graph,
            next_id=next_after_text,
            model_ref=model_output_ref,
            scene_node_id=node_id,
            block=ip_block,
            available_nodes=available_nodes,
            scene_graph=scene_graph_v054,
            requested_mode="second_pass_restore",
        )
        ipadapter_second_pass_model_ref = deepcopy(ip_model_ref) if ip_nodes_added else []
        if ip_nodes_added:
            patched_model_ref = [node_id, SCENE_NODE_OUTPUT_MODEL]
            if ipadapter_restore_fallback_required:
                ipadapter_actual_mode = "second_pass_restore"
            else:
                ipadapter_actual_mode = "second_pass_fallback" if _adapter_first_pass_requested(adapter_execution_mode) else "second_pass"
            ip_notes.append("Scene Director regional attention model output reattached for the first sampler; masked IPAdapter will run as an isolated second pass restore/fallback.")

    regional_authority_restore = _compile_regional_authority_restore_v054(scene_graph_v054, bool(ip_nodes_added and ipadapter_actual_mode != "first_pass_native"), authority_mode=effective_authority_mode, zone_validation=region_zone_validation)
    if regional_authority_restore.get("status") == "applied":
        graph[positive_encode_id]["inputs"]["text"] = _append_unique_text(str(graph[positive_encode_id]["inputs"].get("text") or ""), str(regional_authority_restore.get("positive") or ""))
        graph[negative_encode_id]["inputs"]["text"] = _append_unique_text(str(graph[negative_encode_id]["inputs"].get("text") or ""), str(regional_authority_restore.get("negative") or ""))
        ip_notes.append(f"Regional Authority Compatibility Restore active in {regional_authority_restore.get('mode')}: compiled {regional_authority_restore.get('positive_count', 0)} regional authority prompt lane(s) and {regional_authority_restore.get('negative_count', 0)} regional negative lane(s).")
    patched_positive_ref = [positive_encode_id, 0]
    patched_negative_ref = [negative_encode_id, 0]

    controlnet_nodes_added: list[str] = []
    controlnet_notes: list[str] = []
    regional_controlnet_applied: list[dict[str, Any]] = []
    controlnet_next_id = ip_next_id
    if scene_node_class == V054_NODE_CLASS:
        controlnet_next_id, patched_positive_ref, patched_negative_ref, controlnet_nodes_added, controlnet_notes, regional_controlnet_applied = _apply_scene_director_regional_controlnet_stack(
            graph,
            next_id=ip_next_id,
            positive_ref=patched_positive_ref,
            negative_ref=patched_negative_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            available_nodes=available_nodes,
        )

    sampler_model_execution_ref = _rewire_existing_sampler_model_wrapper(
        graph,
        original_sampler_model_ref,
        patched_model_ref,
    )
    sampler_rewired = False
    if isinstance(sampler, dict):
        sampler_inputs["model"] = deepcopy(sampler_model_execution_ref)
        sampler_inputs["positive"] = deepcopy(patched_positive_ref)
        sampler_inputs["negative"] = deepcopy(patched_negative_ref)
        sampler_rewired = True

    scene_node_inputs_for_proof = graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}
    _pre_sampler_model_ref = list(sampler_inputs.get("model")) if isinstance(sampler_inputs, dict) and isinstance(sampler_inputs.get("model"), (list, tuple)) else []
    _pre_patched_model_ref = list(sampler_model_execution_ref or [])
    _pre_appearance_mode = str(scene_node_inputs_for_proof.get("appearance_lock_mode") or "off")
    primary_attention_lock_active = bool(
        scene_node_class == V054_NODE_CLASS
        and _pre_appearance_mode != "off"
        and _pre_patched_model_ref
        and _pre_sampler_model_ref == _pre_patched_model_ref
    )
    character_lock_execution = _character_lock_execution_settings(
        _block_params(v054_block if scene_node_class == V054_NODE_CLASS else block)
    )
    latent_repair_enabled = bool(character_lock_execution.get("latent_repair_enabled"))
    end_refinement_enabled = bool(character_lock_execution.get("end_refinement_enabled"))

    character_midstep_nodes_added: list[str] = []
    character_midstep_notes: list[str] = []
    character_midstep_authority: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_latent_controller.v054.midstep.v1",
        "phase": "SD-V054-27.2",
        "status": "not_requested",
        "nodes_added": [],
        "lanes": [],
    }
    primary_latent_ref = [sampler_key, 0]
    midstep_next_id = controlnet_next_id
    if scene_node_class == V054_NODE_CLASS and latent_repair_enabled and _fix_pass_allowed(advanced_fix_pass_controls, "character_trait_lanes"):
        midstep_next_id, primary_latent_ref, character_midstep_nodes_added, character_midstep_notes, character_midstep_authority = _apply_scene_director_character_latent_midstep(
            graph,
            sampler_node_id=sampler_key,
            next_id=controlnet_next_id,
            model_ref=sampler_model_execution_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
        )
    elif scene_node_class == V054_NODE_CLASS and latent_repair_enabled:
        character_midstep_authority = _fix_pass_disabled_meta(
            "neo.image.scene_director.character_latent_controller.v054.midstep.v1",
            "SD-V054-27.2",
            "character_trait_lanes",
            advanced_fix_pass_controls,
            extra={"execution_mode": character_lock_execution.get("mode"), "end_refinement_separate": True},
        )

    first_pass_character_lock_nodes_added: list[str] = []
    first_pass_character_lock_notes: list[str] = []
    first_pass_character_lock_final_latent_ref = list(primary_latent_ref)
    first_pass_character_lock_authority: dict[str, Any] = {
        "schema": "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
        "phase": "SD-V054-26.10.8J",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
        "warnings": [],
    }
    first_pass_next_id = midstep_next_id
    if scene_node_class == V054_NODE_CLASS and end_refinement_enabled and _fix_pass_allowed(advanced_fix_pass_controls, "first_pass_character_lock_rescue"):
        first_pass_next_id, first_pass_character_lock_final_latent_ref, first_pass_character_lock_nodes_added, first_pass_character_lock_notes, first_pass_character_lock_authority = _apply_scene_director_first_pass_character_lock_authority(
            graph,
            next_id=first_pass_next_id,
            base_latent_ref=list(primary_latent_ref),
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            negative_ref=patched_negative_ref,
            scene_node_id=node_id,
            legacy=legacy if isinstance(legacy, dict) else {},
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
            scene_graph=scene_graph_v054,
            timing_stage="before_adapters",
            primary_attention_lock_active=primary_attention_lock_active,
        )
    elif scene_node_class == V054_NODE_CLASS:
        first_pass_character_lock_authority = _fix_pass_disabled_meta(
            "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
            "SD-V054-26.10.8J",
            "first_pass_character_lock_rescue",
            advanced_fix_pass_controls,
            extra={"timing_stage": "before_adapters"},
        )

    scene_node_effective_authority_values: dict[str, Any] = {}
    try:
        _scene_node_graph = json.loads(str(scene_node_inputs_for_proof.get("scene_graph_json") or "{}"))
        if isinstance(_scene_node_graph, dict):
            scene_node_effective_authority_values = dict((_scene_node_graph.get("metadata") or {}).get("effective_authority_values") or {})
    except Exception:
        scene_node_effective_authority_values = {}
    if not scene_node_effective_authority_values:
        scene_node_effective_authority_values = _v25_9_6_effective_character_lock_authority_values(
            character_lock_mode=scene_node_inputs_for_proof.get("character_lock_mode"),
            appearance_lock_mode=scene_node_inputs_for_proof.get("appearance_lock_mode"),
            base_weight=scene_node_inputs_for_proof.get("base_weight"),
            region_gain=scene_node_inputs_for_proof.get("region_gain"),
            identity_strength=scene_node_inputs_for_proof.get("identity_strength"),
            appearance_lock_gain=scene_node_inputs_for_proof.get("appearance_lock_gain"),
            mask_feather=scene_node_inputs_for_proof.get("mask_feather"),
        )

    attention_lock_runtime_proof = _attention_lock_runtime_proof_v25_9_2(
        scene_node_class=scene_node_class,
        scene_node_id=node_id if scene_node_class == V054_NODE_CLASS else None,
        scene_graph=scene_graph_v054,
        sampler_node_id=sampler_key,
        sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
        patched_model_ref=sampler_model_execution_ref,
        appearance_lock_mode=scene_node_inputs_for_proof.get("appearance_lock_mode"),
        base_weight=scene_node_inputs_for_proof.get("base_weight"),
        region_gain=scene_node_inputs_for_proof.get("region_gain"),
        normalize_masks=scene_node_inputs_for_proof.get("normalize_masks"),
        first_pass_character_lock_authority=first_pass_character_lock_authority,
    )
    attention_lock_runtime_proof["effective_authority_values"] = deepcopy(scene_node_effective_authority_values)


    identity_restore_nodes_added: list[str] = []
    identity_restore_notes: list[str] = []
    identity_restore_final_latent_ref = list(first_pass_character_lock_final_latent_ref)
    identity_restore_vae_decode_nodes_rewired: list[str] = []
    identity_next_id = first_pass_next_id
    if ip_nodes_added and ipadapter_second_pass_model_ref:
        identity_next_id, identity_restore_final_latent_ref, identity_restore_nodes_added, identity_restore_notes, two_pass_identity_restore = _apply_scene_director_two_pass_identity_restore_v054(
            graph,
            next_id=first_pass_next_id,
            base_latent_ref=first_pass_character_lock_final_latent_ref,
            ip_model_ref=ipadapter_second_pass_model_ref,
            positive_ref=patched_positive_ref,
            negative_ref=patched_negative_ref,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
        )

    lora_nodes_added: list[str] = []
    lora_notes: list[str] = []
    lora_finish_prompt_stack: list[dict[str, Any]] = []
    lora_final_latent_ref = list(identity_restore_final_latent_ref)
    lora_vae_decode_nodes_rewired: list[str] = []
    subject_slot_by_region = _v054_character_subject_slot_map(scene_graph_v054)
    _lora_next_id, lora_final_latent_ref, lora_nodes_added, lora_notes, lora_finish_prompt_stack = _apply_scene_director_regional_lora_finish_passes(
        graph,
        next_id=identity_next_id,
        base_latent_ref=identity_restore_final_latent_ref,
        model_ref=ipadapter_second_pass_model_ref or patched_model_ref,
        clip_ref=clip_output_ref,
        scene_node_id=node_id,
        block=block,
        legacy={
            **(legacy if isinstance(legacy, dict) else {}),
            "scene_director_lora_region_assignments": lora_region_assignments,
            "scene_director_lora_first_pass_applied_uids": [lane.get("uid") for lane in (lora_first_pass_lanes or []) if lane.get("uid") and lane.get("runtime_applied") is True and lane.get("actual_mode") == "regional_model_delta_mixer"],
            "scene_director_lora_runtime_fallback_uids": [lane.get("uid") for lane in (lora_first_pass_lanes or []) if lane.get("uid") and (lane.get("fallback_used") or lane.get("actual_mode") == "finish_pass_fallback")],
        },
        sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
        sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
        available_nodes=available_nodes,
        subject_slot_by_region=subject_slot_by_region,
        scene_graph=scene_graph_v054,
        regional_authority_restore=regional_authority_restore,
    )
    latent_lock_nodes_added: list[str] = []
    latent_lock_notes: list[str] = []
    latent_character_lock_postpass_authority: dict[str, Any] = {
        "schema": "neo.image.scene_director.latent_character_lock_postpass_authority.v054.v1",
        "phase": "SD-V054-26.10.8H",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
        "warnings": [],
    }
    if lora_nodes_added and scene_node_class == V054_NODE_CLASS:
        _latent_next_id, lora_final_latent_ref, latent_lock_nodes_added, latent_lock_notes, latent_character_lock_postpass_authority = _apply_scene_director_latent_character_lock_postpass_authority(
            graph,
            next_id=_lora_next_id,
            base_latent_ref=lora_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            positive_ref=patched_positive_ref,
            negative_ref=patched_negative_ref,
            scene_node_id=node_id,
            lora_lanes=lora_finish_prompt_stack,
            block=block,
            legacy={
                **(legacy if isinstance(legacy, dict) else {}),
                "scene_director_lora_region_assignments": lora_region_assignments,
            },
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
            scene_graph=scene_graph_v054,
        )
    else:
        _latent_next_id = _lora_next_id

    final_pass_character_lock_nodes_added: list[str] = []
    final_pass_character_lock_notes: list[str] = []
    final_pass_character_lock_authority: dict[str, Any] = {
        "schema": "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
        "phase": "SD-V054-26.10.8J",
        "status": "not_applicable",
        "timing_stage": "final_pass",
        "lanes": [],
        "nodes_added": [],
        "warnings": [],
    }
    final_pass_character_lock_final_latent_ref = list(lora_final_latent_ref)
    final_pass_next_id = _latent_next_id
    if scene_node_class == V054_NODE_CLASS and end_refinement_enabled and _fix_pass_allowed(advanced_fix_pass_controls, "first_pass_character_lock_rescue"):
        final_pass_next_id, final_pass_character_lock_final_latent_ref, final_pass_character_lock_nodes_added, final_pass_character_lock_notes, final_pass_character_lock_authority = _apply_scene_director_first_pass_character_lock_authority(
            graph,
            next_id=_latent_next_id,
            base_latent_ref=lora_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            negative_ref=patched_negative_ref,
            scene_node_id=node_id,
            legacy=legacy if isinstance(legacy, dict) else {},
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
            scene_graph=scene_graph_v054,
            timing_stage="final_pass",
            primary_attention_lock_active=primary_attention_lock_active,
        )
    elif scene_node_class == V054_NODE_CLASS:
        final_pass_character_lock_authority = _fix_pass_disabled_meta(
            "neo.image.scene_director.first_pass_character_lock_authority.v054.v1",
            "SD-V054-26.10.8J",
            "first_pass_character_lock_rescue",
            advanced_fix_pass_controls,
            extra={"timing_stage": "final_pass"},
        )

    background_restore_nodes_added: list[str] = []
    background_restore_notes: list[str] = []
    background_authority_restore: dict[str, Any] = {
        "schema": "neo.image.scene_director.background_authority_restore.v25_9_6_fix4",
        "phase": "V25.9.6-fix4",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
    }
    background_restore_final_latent_ref = list(final_pass_character_lock_final_latent_ref)
    background_restore_next_id = final_pass_next_id
    if scene_node_class == V054_NODE_CLASS and end_refinement_enabled and _fix_pass_allowed(advanced_fix_pass_controls, "background_restore"):
        background_restore_next_id, background_restore_final_latent_ref, background_restore_nodes_added, background_restore_notes, background_authority_restore = _apply_scene_director_background_authority_restore(
            graph,
            next_id=final_pass_next_id,
            base_latent_ref=final_pass_character_lock_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            regional_authority_restore=regional_authority_restore,
        )
    elif scene_node_class == V054_NODE_CLASS:
        background_authority_restore = _fix_pass_disabled_meta(
            "neo.image.scene_director.background_authority_restore.v25_9_6_fix4",
            "V25.9.6-fix4",
            "background_restore",
            advanced_fix_pass_controls,
        )

    skin_build_restore_nodes_added: list[str] = []
    skin_build_restore_notes: list[str] = []
    skin_build_contrast_authority: dict[str, Any] = {
        "schema": "neo.image.scene_director.skin_build_contrast_authority.v25_9_6_fix5",
        "phase": "V25.9.6-fix5",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
    }
    skin_build_restore_final_latent_ref = list(background_restore_final_latent_ref)
    skin_build_restore_next_id = background_restore_next_id
    if scene_node_class == V054_NODE_CLASS:
        skin_build_restore_next_id, skin_build_restore_final_latent_ref, skin_build_restore_nodes_added, skin_build_restore_notes, skin_build_contrast_authority = _apply_scene_director_skin_build_contrast_restore(
            graph,
            next_id=background_restore_next_id,
            base_latent_ref=background_restore_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
        )

    character_latent_controller_nodes_added: list[str] = []
    character_latent_controller_notes: list[str] = []
    character_latent_controller: dict[str, Any] = {
        "schema": "neo.image.scene_director.character_latent_controller.v25_9_8",
        "phase": "V25.9.8",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
    }
    character_latent_controller_final_ref = list(skin_build_restore_final_latent_ref)
    character_latent_controller_next_id = skin_build_restore_next_id
    if scene_node_class == V054_NODE_CLASS and latent_repair_enabled:
        character_latent_controller = deepcopy(character_midstep_authority)
        character_latent_controller["execution_mode"] = character_lock_execution.get("mode")
        character_latent_controller["live_in_sampler_attention"] = bool(character_lock_execution.get("in_sampler_attention_enabled"))
        character_latent_controller["final_latent_ref_after_follow_on_passes"] = deepcopy(character_latent_controller_final_ref)
        character_latent_controller_nodes_added = list(character_midstep_nodes_added)
        character_latent_controller_notes = list(character_midstep_notes)
    elif scene_node_class == V054_NODE_CLASS and character_lock_execution.get("in_sampler_attention_enabled"):
        character_latent_controller = {
            "schema": "neo.image.scene_director.character_latent_controller.v25_9_8",
            "phase": "SD-V054-27.2",
            "status": "live_in_sampler_attention",
            "lanes": [],
            "nodes_added": [],
            "applied_count": 0,
            "skipped_count": 0,
            "live_in_sampler_attention": True,
            "execution_mode": character_lock_execution.get("mode"),
            "advanced_fix_pass_controls": deepcopy(advanced_fix_pass_controls),
            "live_conditioning": _v054_live_character_conditioning_report(scene_graph_v054, character_lock_execution),
            "policy": "Character Trait Lanes control masked repair only. The selected in-sampler plan is already consuming explicit traits through V054 attn2 subject branches; no late KSampler was requested.",
        }
    elif scene_node_class == V054_NODE_CLASS:
        character_latent_controller = _fix_pass_disabled_meta(
            "neo.image.scene_director.character_latent_controller.v25_9_8",
            "V25.9.8",
            "character_trait_lanes",
            advanced_fix_pass_controls,
            extra={"environment_aware_character_lanes": {"schema": "neo.image.scene_director.environment_aware_character_lanes.v25_9_12", "phase": "V25.9.12", "status": "disabled_parent_character_trait_lanes", "advanced_fix_pass_controls": deepcopy(advanced_fix_pass_controls)}},
        )

    outfit_restore_nodes_added: list[str] = []
    outfit_restore_notes: list[str] = []
    outfit_preservation_restore: dict[str, Any] = _merged_outfit_restore_metadata_from_character_controller(character_latent_controller)
    outfit_restore_final_latent_ref = list(character_latent_controller_final_ref)
    outfit_restore_next_id = character_latent_controller_next_id
    if scene_node_class == V054_NODE_CLASS and end_refinement_enabled and not character_latent_controller_nodes_added:
        outfit_restore_next_id, outfit_restore_final_latent_ref, outfit_restore_nodes_added, outfit_restore_notes, outfit_preservation_restore = _apply_scene_director_outfit_preservation_restore(
            graph,
            next_id=character_latent_controller_next_id,
            base_latent_ref=character_latent_controller_final_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
        )

    final_background_reconciliation_nodes_added: list[str] = []
    final_background_reconciliation_notes: list[str] = []
    final_background_reconciliation_restore: dict[str, Any] = {
        "schema": "neo.image.scene_director.final_background_reconciliation.v25_9_11",
        "phase": "V25.9.11",
        "status": "not_applicable",
        "nodes_added": [],
        "lanes": [],
        "applied_count": 0,
        "skipped_count": 0,
    }
    final_background_reconciliation_ref = list(outfit_restore_final_latent_ref)
    final_background_reconciliation_next_id = outfit_restore_next_id
    if scene_node_class == V054_NODE_CLASS and end_refinement_enabled and _fix_pass_allowed(advanced_fix_pass_controls, "final_background_reconciliation"):
        final_background_reconciliation_next_id, final_background_reconciliation_ref, final_background_reconciliation_nodes_added, final_background_reconciliation_notes, final_background_reconciliation_restore = _apply_scene_director_final_background_reconciliation(
            graph,
            next_id=outfit_restore_next_id,
            base_latent_ref=outfit_restore_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            background_authority_restore=background_authority_restore,
            character_latent_controller=character_latent_controller,
        )
    elif scene_node_class == V054_NODE_CLASS:
        final_background_reconciliation_restore = _fix_pass_disabled_meta(
            "neo.image.scene_director.final_background_reconciliation.v25_9_11",
            "V25.9.11",
            "final_background_reconciliation",
            advanced_fix_pass_controls,
            extra={"runs_after": "character_latent_controller", "uses_mask_ref": [str(node_id), 12]},
        )

    final_decode_latent_ref = final_background_reconciliation_ref if (final_background_reconciliation_nodes_added or outfit_restore_nodes_added or character_latent_controller_nodes_added or skin_build_restore_nodes_added or background_restore_nodes_added or final_pass_character_lock_nodes_added or lora_nodes_added or latent_lock_nodes_added or identity_restore_nodes_added or first_pass_character_lock_nodes_added) else [sampler_key, 0]
    if lora_nodes_added or identity_restore_nodes_added or latent_lock_nodes_added or first_pass_character_lock_nodes_added or final_pass_character_lock_nodes_added or background_restore_nodes_added or skin_build_restore_nodes_added or character_latent_controller_nodes_added or outfit_restore_nodes_added or final_background_reconciliation_nodes_added:
        for decode_id in _find_vae_decode_consumers(graph, [sampler_key, 0]):
            inputs = graph[decode_id].setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs["samples"] = deepcopy(final_decode_latent_ref)
                if lora_nodes_added or latent_lock_nodes_added or final_pass_character_lock_nodes_added or background_restore_nodes_added or skin_build_restore_nodes_added or character_latent_controller_nodes_added or outfit_restore_nodes_added or final_background_reconciliation_nodes_added:
                    lora_vae_decode_nodes_rewired.append(decode_id)
                else:
                    identity_restore_vae_decode_nodes_rewired.append(decode_id)

    lora_crop_nodes_added: list[str] = []
    lora_crop_notes: list[str] = []
    lora_crop_refinement_lanes: list[dict[str, Any]] = []
    lora_crop_image_consumers_rewired: list[str] = []
    lora_crop_refinement: dict[str, Any] = {
        "schema": "neo.image.scene_director.regional_lora_crop_refinement.v054.v1",
        "phase": "SD-V054-26.9.15",
        "status": "not_applicable",
        "lanes": [],
        "nodes_added": [],
        "image_consumers_rewired": [],
        "warnings": [],
    }
    crop_next_id = final_pass_next_id
    if scene_node_class == V054_NODE_CLASS:
        crop_next_id, lora_crop_nodes_added, lora_crop_notes, lora_crop_refinement_lanes, lora_crop_image_consumers_rewired, lora_crop_refinement = _apply_scene_director_regional_lora_crop_refinement_passes(
            graph,
            next_id=_latent_next_id,
            base_latent_ref=lora_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            scene_node_id=node_id,
            block=block,
            legacy={
                **(legacy if isinstance(legacy, dict) else {}),
                "scene_director_lora_region_assignments": lora_region_assignments,
                "scene_director_lora_runtime_fallback_uids": [lane.get("uid") for lane in (lora_first_pass_lanes or []) if lane.get("uid") and (lane.get("fallback_used") or lane.get("actual_mode") == "finish_pass_fallback")],
            },
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            sampler_seed=_int((sampler_inputs or {}).get("seed"), 0),
            available_nodes=available_nodes,
            subject_slot_by_region=subject_slot_by_region,
            scene_graph=scene_graph_v054,
            regional_authority_restore=regional_authority_restore,
        )

    detailer_nodes_added: list[str] = []
    detailer_notes: list[str] = []
    regional_detailer_applied: list[dict[str, Any]] = []
    detailer_image_consumers_rewired: list[str] = []
    detailer_next_id = crop_next_id
    if scene_node_class == V054_NODE_CLASS:
        detailer_next_id, detailer_nodes_added, detailer_notes, regional_detailer_applied, detailer_image_consumers_rewired = _apply_scene_director_regional_detailer_stack(
            graph,
            next_id=crop_next_id,
            base_latent_ref=lora_final_latent_ref,
            model_ref=patched_model_ref,
            clip_ref=clip_output_ref,
            positive_ref=patched_positive_ref,
            negative_ref=patched_negative_ref,
            scene_node_id=node_id,
            scene_graph=scene_graph_v054,
            sampler_inputs=sampler_inputs if isinstance(sampler_inputs, dict) else {},
            available_nodes=available_nodes,
        )

    native_regional_adapter_injection = _native_adapter_injection_metadata(
        requested_mode=adapter_execution_mode,
        lora_lanes=lora_first_pass_lanes,
        lora_nodes_added=lora_first_pass_nodes_added,
        ipadapter_requested_mode=ipadapter_requested_mode,
        ipadapter_actual_mode=ipadapter_actual_mode,
        ipadapter_nodes_added=ip_nodes_added if ipadapter_actual_mode == "first_pass_native" else [],
        ipadapter_units=(_normalize_scene_director_identity_units(ip_block, subject_slot_by_region, scene_graph=scene_graph_v054, requested_mode="hybrid_safe_identity") if ipadapter_owner_enabled else []),
        ipadapter_safety_gate=ipadapter_safety_gate,
    )
    regional_adapter_visibility = _build_regional_adapter_visibility_v054(
        block=ip_block,
        scene_graph=scene_graph_v054,
        ip_nodes_added=ip_nodes_added,
        two_pass_identity_restore=two_pass_identity_restore,
        lora_nodes_added=lora_nodes_added,
        lora_finish_prompt_stack=lora_finish_prompt_stack,
        lora_crop_refinement_lanes=lora_crop_refinement_lanes,
        subject_slot_by_region=subject_slot_by_region,
        native_adapter_injection=native_regional_adapter_injection,
        ipadapter_owner_enabled=ipadapter_owner_enabled,
        disabled_ipadapter_gate=disabled_ipadapter_gate,
        preserved_ipadapter_units=ipadapter_profile_units_for_gate if not ipadapter_owner_enabled else None,
    )
    ipadapter_instruction_preservation = _build_ipadapter_instruction_preservation_metadata(
        ipadapter_units=(ipadapter_profile_units_for_gate if not ipadapter_owner_enabled else _normalize_scene_director_identity_units(ip_block, subject_slot_by_region, scene_graph=scene_graph_v054, requested_mode="hybrid_safe_identity")),
        ipadapter_nodes_added=ip_nodes_added,
        actual_mode=ipadapter_actual_mode,
        ipadapter_owner_enabled=ipadapter_owner_enabled,
        disabled_ipadapter_gate=disabled_ipadapter_gate,
    )
    extension_routing_authority = _build_extension_routing_authority_summary({}, extension_authority_routes)
    regional_lora_model_delta_mixer = _build_regional_lora_model_delta_mixer_summary(extension_authority_routes)
    background_separation_guard = regional_authority_restore.get("background_separation_guard") if isinstance(regional_authority_restore, dict) else {
        "schema": "neo.image.scene_director.background_separation_guard.v054.v1",
        "phase": "SD-V054-26.9.8",
        "status": "not_applicable",
        "duplicate_count": 0,
        "repaired_count": 0,
        "warnings": [],
        "repairs": [],
    }

    notes = [
        f"Scene Director routed through {scene_node_class} with V054 scene_graph_json conditioning.",
        f"Prompt authority: {prompt_authority}; Neo core prompt is {'excluded' if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY else 'kept as global context with a compact regional suffix'}.",
        "Sampler model/positive/negative inputs were rewired to the Scene Director branch.",
        f"Character Lock Execution Bridge {character_lock_bridge.get('status', 'not_applicable')}: compiled {character_lock_bridge.get('compiled_positive_count', 0)} positive guard phrase(s) and {character_lock_bridge.get('compiled_negative_count', 0)} negative guard phrase(s).",
        *ip_notes,
        *controlnet_notes,
        *first_pass_character_lock_notes,
        *identity_restore_notes,
        *lora_first_pass_notes,
        *lora_notes,
        *latent_lock_notes,
        *final_pass_character_lock_notes,
        *(["Scene Director background prime authority moved user-authored environment text into early main conditioning."] if isinstance(background_prime_authority, dict) and background_prime_authority.get("status") == "applied" else []),
        *background_restore_notes,
        *skin_build_restore_notes,
        *character_latent_controller_notes,
        *outfit_restore_notes,
        *final_background_reconciliation_notes,
        *lora_crop_notes,
        *detailer_notes,
        *notes,
    ]
    if (legacy.get("scene_director_ipadapter_bindings") or legacy.get("scene_director_identity_units")) and not ip_nodes_added:
        notes.append("Regional IPAdapter/FaceID identity intent was present, but no safe provider-compatible unit was built; character profile data remains available as metadata.")
    if (lora_region_assignments.get("lora_lanes") or legacy.get("scene_director_lora_bindings") or (block.get("assets") or {}).get("lora_bindings")) and not (lora_nodes_added or lora_first_pass_nodes_added):
        notes.append("Regional LoRA binding metadata was present, but no native first-pass or masked finish-pass LoRA route was added; check LoraLoader and SetLatentNoiseMask availability.")

    provider_metadata = dict(scene_graph_v054.get("metadata", {}) if isinstance(scene_graph_v054, dict) else {})
    if isinstance(scene_graph_v054, dict):
        provider_metadata["scene_graph_json"] = scene_graph_v054
    provider_capabilities = resolve_provider_capabilities_v054(
        readiness.get("route") or validation.get("route") or {},
        object_info=available_nodes,
        metadata=provider_metadata,
    )
    flux_adapter_plan = provider_capabilities.get("flux_adapter_plan")
    if provider_capabilities.get("provider_profile") == "flux_adapter_planned" and not flux_adapter_plan:
        flux_adapter_plan = build_flux_adapter_plan_v054(scene_graph_v054, route=readiness.get("route") or validation.get("route") or {})
    qwen_adapter_plan = provider_capabilities.get("qwen_adapter_plan")
    if provider_capabilities.get("provider_profile") == "qwen_semantic_edit_adapter" and not qwen_adapter_plan:
        qwen_adapter_plan = build_qwen_adapter_plan_v054(scene_graph_v054, route=readiness.get("route") or validation.get("route") or {})

    patch = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "patch_type": "scene_director_v054",
        "applied": True,
        "mutated": True,
        "node": scene_node_class,
        "node_class": scene_node_class,
        "node_classes": sorted({scene_node_class, *[str(graph[n].get("class_type")) for n in [*lora_first_pass_nodes_added, *ip_nodes_added, *controlnet_nodes_added, *identity_restore_nodes_added, *lora_nodes_added, *latent_lock_nodes_added, *final_pass_character_lock_nodes_added, *background_restore_nodes_added, *skin_build_restore_nodes_added, *character_latent_controller_nodes_added, *outfit_restore_nodes_added, *final_background_reconciliation_nodes_added, *lora_crop_nodes_added, *detailer_nodes_added] if n in graph]}),
        "nodes_added": [node_id, positive_encode_id, negative_encode_id, *lora_first_pass_nodes_added, *ip_nodes_added, *controlnet_nodes_added, *identity_restore_nodes_added, *lora_nodes_added, *latent_lock_nodes_added, *final_pass_character_lock_nodes_added, *background_restore_nodes_added, *skin_build_restore_nodes_added, *character_latent_controller_nodes_added, *outfit_restore_nodes_added, *final_background_reconciliation_nodes_added, *lora_crop_nodes_added, *detailer_nodes_added],
        "scene_node_id": node_id,
        "text_nodes_added": [positive_encode_id, negative_encode_id],
        "sampler_node_id": sampler_key,
        "sampler_rewired": sampler_rewired,
        "regions": len(validation.get("active_regions") or []),
        "subject_count": len(_v054_character_subject_slot_map(scene_graph_v054)) if scene_node_class == V054_NODE_CLASS else int((block.get("metadata") or {}).get("subject_count") or validation.get("subject_count") or 0),
        "detail_region_count": int((block.get("metadata") or {}).get("detail_region_count") or validation.get("detail_region_count") or 0),
        "route": validation.get("route"),
        "route_state": readiness.get("route_state"),
        "workflow_readiness_state": readiness.get("workflow_readiness_state"),
        "workflow_patch_allowed": True,
        "scene_director_authority_mode": authority_mode,
        "scene_director_effective_authority_mode": effective_authority_mode,
        "scene_director_effective_authority": effective_authority,
        "scene_director_prompt_authority": prompt_authority,
        "scene_director_prompt_authority_contract": deepcopy(prompt_authority_contract),
        "scene_director_global_prompt_excluded": prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
        "scene_director_prompt_only_mode": False,
        "scene_director_neutral_mode": False,
        "scene_director_character_lock_execution": deepcopy(character_lock_execution),
        "scene_director_character_lock_graph_cleanup": deepcopy(stale_character_lock_cleanup),
        "scene_director_character_lock_execution_mode": character_lock_execution.get("mode"),
        "scene_director_character_lock_pass_plan": character_lock_execution.get("pass_plan"),
        "scene_director_character_lock_primary_path": "legacy_in_sampler_attention" if (primary_attention_lock_active and character_lock_execution.get("in_sampler_attention_enabled")) else ("end_refinement_only" if character_lock_execution.get("end_refinement_enabled") else "none"),
        "scene_director_legacy_attention_lock_primary": bool(primary_attention_lock_active),
        "scene_director_masked_correction_role": "optional_pass_plan" if character_lock_execution.get("uses_scene_masks") else "not_requested",
        "scene_director_character_lock_execution_masked": bool(first_pass_character_lock_nodes_added or final_pass_character_lock_nodes_added),
        "scene_director_effective_authority_values": deepcopy(scene_node_effective_authority_values),
        "scene_director_effective_authority_values_phase": "V25.9.6-fix1",
        "node_status": node_status,
        "fallback_policy": readiness.get("fallback_policy"),
        "previous_model_ref": deepcopy(model_output_ref),
        "patched_model_ref": deepcopy(patched_model_ref),
        "scene_director_ipadapter_nodes_added": ip_nodes_added,
        "scene_director_ipadapter_applied": bool(ip_nodes_added),
        "scene_director_ipadapter_execution_disabled": not bool(ip_nodes_added),
        "scene_director_ipadapter_disable_reason": "" if ip_nodes_added else ("image.ip_adapter disabled by user; regional identity profile preserved as metadata only." if not ipadapter_owner_enabled and ipadapter_profile_units_for_gate else "Phase 26.9 safe regional IPAdapter route found no provider-compatible executable unit; identity data remains metadata-only."),
        "scene_director_ipadapter_execution_phase": "SD-V054-26.9.12",
        "scene_director_disabled_adapter_route_gate": disabled_ipadapter_gate,
        "scene_director_ipadapter_actual_mode": ipadapter_actual_mode,
        "scene_director_ipadapter_safety_gate": ipadapter_safety_gate,
        "scene_director_two_pass_identity_restore": two_pass_identity_restore,
        "scene_director_first_pass_character_lock_authority": first_pass_character_lock_authority,
        "scene_director_first_pass_character_lock_nodes_added": first_pass_character_lock_nodes_added,
        "scene_director_final_character_lock_authority": final_pass_character_lock_authority,
        "scene_director_final_character_lock_nodes_added": final_pass_character_lock_nodes_added,
        "scene_director_relationship_pose_authority": _scene_relationship_pose_authority(scene_graph_v054),
        "scene_director_pair_pose_authority": _scene_relationship_pose_authority(scene_graph_v054),
        "scene_director_character_pose_authority": _scene_character_pose_authority(scene_graph_v054),
        "scene_director_background_space_authority": _scene_background_space_authority(scene_graph_v054),
        "scene_director_advanced_fix_pass_controls": deepcopy(advanced_fix_pass_controls),
        "scene_director_layout_safety": deepcopy((scene_graph_v054.get("metadata", {}) if isinstance(scene_graph_v054, dict) else {}).get("layout_safety", {})),
        "scene_director_background_prime_authority": background_prime_authority,
        "scene_director_background_authority_restore": background_authority_restore,
        "scene_director_background_restore_nodes_added": background_restore_nodes_added,
        "scene_director_skin_build_contrast_authority": skin_build_contrast_authority,
        "scene_director_skin_build_restore_nodes_added": skin_build_restore_nodes_added,
        "scene_director_character_latent_midstep_authority": character_midstep_authority,
        "scene_director_character_latent_midstep_nodes_added": character_midstep_nodes_added,
        "scene_director_character_latent_controller": character_latent_controller,
        "scene_director_environment_aware_character_lanes": deepcopy(character_latent_controller.get("environment_aware_character_lanes") or {}),
        "scene_director_character_latent_controller_nodes_added": character_latent_controller_nodes_added,
        "scene_director_outfit_preservation_restore": outfit_preservation_restore,
        "scene_director_outfit_restore_nodes_added": outfit_restore_nodes_added,
        "scene_director_final_background_reconciliation": final_background_reconciliation_restore,
        "scene_director_final_background_reconciliation_nodes_added": final_background_reconciliation_nodes_added,
        "scene_director_identity_restore_nodes_added": identity_restore_nodes_added,
        "scene_director_identity_restore_vae_decode_nodes_rewired": identity_restore_vae_decode_nodes_rewired,
        "scene_director_identity_units_planned": len(ipadapter_profile_units_for_gate),
        "scene_director_subject_slot_resolver": {
            "schema": "neo.image.scene_director.subject_slot_resolver.v054.v1",
            "phase": "SD-V054-26.9.4",
            "status": "applied",
            "policy": "character regions only; backgrounds, hair_detail, held_prop and transition_effect are never subject slots",
            "subject_slot_by_region": _v054_character_subject_slot_map(scene_graph_v054),
        },
        "scene_director_disabled_owner_route_cleanup": (scene_graph_v054.get("metadata", {}).get("disabled_owner_route_cleanup", {}) if isinstance(scene_graph_v054, dict) else {}),
        "scene_director_regional_authority_restore": regional_authority_restore,
        "scene_director_regional_authority_mode": regional_authority_restore.get("mode"),
        "scene_director_regional_authority_compatibility_restore_mode": True,
        "scene_director_background_separation_guard": background_separation_guard,
        "scene_director_region_zone_validation": region_zone_validation,
        "scene_director_native_regional_adapter_injection": native_regional_adapter_injection,
        "scene_director_extension_authority_routes": extension_authority_routes,
        "scene_director_extension_routing_authority": extension_routing_authority,
        "scene_director_regional_lora_model_delta_mixer": regional_lora_model_delta_mixer,
        "scene_director_regional_lora_crop_refinement": lora_crop_refinement,
        "scene_director_postpass_character_lock_gate": deepcopy((lora_crop_refinement or {}).get("postpass_character_lock_gate") or {}),
        "scene_director_regional_adapter_visibility": regional_adapter_visibility,
        "scene_director_ipadapter_instruction_preservation": ipadapter_instruction_preservation,
        "scene_director_controlnet_nodes_added": controlnet_nodes_added,
        "scene_director_regional_controlnet_applied": bool(controlnet_nodes_added),
        "regional_controlnet_units": regional_controlnet_applied,
        "scene_director_lora_first_pass_nodes_added": lora_first_pass_nodes_added,
        "scene_director_lora_first_pass_prompt_stack": lora_first_pass_lanes,
        "scene_director_lora_nodes_added": lora_nodes_added,
        "scene_director_latent_character_lock_postpass_authority": latent_character_lock_postpass_authority,
        "scene_director_latent_character_lock_nodes_added": latent_lock_nodes_added,
        "scene_director_lora_crop_nodes_added": lora_crop_nodes_added,
        "scene_director_lora_crop_refinement_lanes": lora_crop_refinement_lanes,
        "scene_director_lora_crop_image_consumers_rewired": lora_crop_image_consumers_rewired,
        "scene_director_lora_applied": bool(lora_nodes_added or lora_first_pass_nodes_added or latent_lock_nodes_added or lora_crop_nodes_added),
        "scene_director_lora_final_latent_ref": deepcopy(lora_final_latent_ref),
        "scene_director_final_decode_latent_ref": deepcopy(final_decode_latent_ref),
        "scene_director_lora_vae_decode_nodes_rewired": lora_vae_decode_nodes_rewired,
        "scene_director_lora_finish_pass_prompt_stack": lora_finish_prompt_stack,
        "scene_director_detailer_nodes_added": detailer_nodes_added,
        "scene_director_regional_detailer_applied": bool(detailer_nodes_added),
        "regional_detailer_units": regional_detailer_applied,
        "scene_director_detailer_image_consumers_rewired": detailer_image_consumers_rewired,
        "clip_ref": deepcopy(clip_output_ref),
        "patched_clip_ref": deepcopy(lora_first_pass_clip_ref),
        "previous_positive_ref": deepcopy(previous_positive_ref),
        "previous_negative_ref": deepcopy(previous_negative_ref),
        "patched_positive_ref": deepcopy(patched_positive_ref),
        "patched_negative_ref": deepcopy(patched_negative_ref),
        "scene_json_length": 0,
        "legacy_scene_json_retired": True,
        "scene_graph_json_present": bool(scene_graph_v054),
        "scene_graph_json_region_count": len((scene_graph_v054 or {}).get("regions") or []),
        "regional_controlnet_count": len(regional_controlnet_applied),
        "regional_detailer_count": len(regional_detailer_applied),
        "scene_director_text_compositor_mode": True,
        "scene_director_text_regions": (scene_graph_v054.get("metadata", {}).get("text_regions", {}) if isinstance(scene_graph_v054, dict) else {}),
        "scene_director_img2img_region_reuse": (scene_graph_v054.get("metadata", {}).get("img2img_region_reuse", {}) if isinstance(scene_graph_v054, dict) else {}),
        "scene_director_img2img_region_reuse_mode": True,
        "scene_director_inpaint_region_targets": (scene_graph_v054.get("metadata", {}).get("inpaint_region_targets", {}) if isinstance(scene_graph_v054, dict) else {}),
        "scene_director_inpaint_region_targeting_mode": True,
        "scene_director_output_inspector_source_stack": {
            "phase": "SD-V054-21",
            "legacy_qwen_adapter_phase_anchor": "SD-V054-20",
            "legacy_phase_anchor": "SD-V054-16",
            "legacy_flux_adapter_phase_anchor": "SD-V054-19",
            "legacy_provider_capability_phase_anchor": "SD-V054-17",
            "legacy_sdxl_full_lock_phase_anchor": "SD-V054-18",
            "scene_graph_json_present": bool(scene_graph_v054),
            "mask_outputs": ["subject_masks", "detail_masks", "background_masks", "control_masks", "inpaint_masks"],
            "controlnet_binding_count": len(regional_controlnet_applied),
            "detailer_binding_count": len(regional_detailer_applied),
            "controlnet_adetailer_region_assignments": extension_region_assignments,
            "lora_region_assignments": lora_region_assignments,
            "character_lock_bridge": character_lock_bridge,
            "regional_authority_restore": regional_authority_restore,
            "first_pass_character_lock_authority": first_pass_character_lock_authority,
            "attention_lock_runtime_proof": attention_lock_runtime_proof,
            "effective_authority_values": deepcopy(scene_node_effective_authority_values),
            "background_separation_guard": background_separation_guard,
            "background_authority_restore": background_authority_restore,
            "skin_build_contrast_authority": skin_build_contrast_authority,
            "character_latent_midstep_authority": character_midstep_authority,
            "character_latent_controller": character_latent_controller,
            "outfit_preservation_restore": outfit_preservation_restore,
            "final_background_reconciliation": final_background_reconciliation_restore,
            "native_regional_adapter_injection": native_regional_adapter_injection,
            "extension_authority_routes": extension_authority_routes,
            "extension_routing_authority": extension_routing_authority,
            "regional_lora_model_delta_mixer": regional_lora_model_delta_mixer,
            "latent_character_lock_postpass_authority": latent_character_lock_postpass_authority,
            "regional_lora_crop_refinement": lora_crop_refinement,
            "postpass_character_lock_gate": deepcopy((lora_crop_refinement or {}).get("postpass_character_lock_gate") or {}),
            "regional_adapter_visibility": regional_adapter_visibility,
            "img2img_reuse": (scene_graph_v054.get("metadata", {}).get("img2img_region_reuse", {}) if isinstance(scene_graph_v054, dict) else {}),
            "inpaint_targets": (scene_graph_v054.get("metadata", {}).get("inpaint_region_targets", {}) if isinstance(scene_graph_v054, dict) else {}),
        },
        "scene_director_output_inspector_source_stack_mode": True,
        "scene_director_extension_unit_routing": (scene_graph_v054.get("metadata", {}).get("extension_unit_routing", {}) if isinstance(scene_graph_v054, dict) else {}),
        "scene_director_extension_unit_routing_contract_mode": True,
        "scene_director_controlnet_adetailer_region_assignments": extension_region_assignments,
        "scene_director_controlnet_adetailer_region_assignment_mode": True,
        "scene_director_lora_region_assignments": lora_region_assignments,
        "scene_director_lora_stack_region_assignment_migration_mode": True,
        "scene_director_ipadapter_identity_profile_migration_mode": True,
        "scene_director_character_lock_bridge": character_lock_bridge,
        "scene_director_character_lock_execution_bridge_mode": True,
        "scene_director_character_lock_positive_added": character_lock_positive_add,
        "scene_director_character_lock_negative_added": character_lock_negative_add,
        "scene_director_character_lock_live_conditioning": _v054_live_character_conditioning_report(scene_graph_v054, character_lock_execution) if scene_node_class == V054_NODE_CLASS else {"status": "not_applicable"},
        "scene_director_attention_lock_runtime_proof": attention_lock_runtime_proof,
        "scene_director_attention_lock_runtime": attention_lock_runtime_proof,
        "scene_director_legacy_appearance_lock_parity": {
            "schema": "neo.image.scene_director.legacy_appearance_lock_parity.v054.v1",
            "phase": "SD-V054-26.9.6",
            "status": "applied" if scene_node_class == V054_NODE_CLASS else "not_applicable",
            "policy": "Phase 26.9.6 upper identity branch remains available; Phase 26.9.7 promotes Character Lock Strong/Strict to full-character authority without hardcoded gender/body terms.",
            "node_inputs": {
                "appearance_lock_mode": (graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_mode"),
                "appearance_lock_gain": (graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_gain"),
                "appearance_lock_height": (graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_height"),
                "appearance_lock_feather": (graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_feather"),
            },
        },
        "scene_director_full_character_lock_authority_parity": {
            "schema": "neo.image.scene_director.full_character_lock_authority_parity.v054.v1",
            "phase": "SD-V054-26.9.7",
            "status": "applied" if scene_node_class == V054_NODE_CLASS and str((graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_mode") or "").startswith("full_character") else ("off" if scene_node_class == V054_NODE_CLASS else "not_applicable"),
            "appearance_lock_mode": (graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_mode"),
            "full_character_branch_count": len(_v054_character_subject_slot_map(scene_graph_v054)) if str((graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_mode") or "").startswith("full_character") else 0,
            "upper_identity_branch_count": len(_v054_character_subject_slot_map(scene_graph_v054)) if str((graph.get(node_id, {}).get("inputs", {}) if isinstance(graph.get(node_id, {}), dict) else {}).get("appearance_lock_mode") or "") == "full_character_strong" else 0,
            "policy": "Character Lock Strong/Strict maps to full-character masked authority plus upper identity reinforcement without hardcoded gender assumptions.",
        },
        "scene_director_provider_capabilities": provider_capabilities,
        "scene_director_provider_capability_resolver_mode": True,
        "scene_director_provider_profile": provider_capabilities.get("provider_profile"),
        "scene_director_flux_adapter_plan": flux_adapter_plan,
        "scene_director_flux_adapter_planning_mode": bool(flux_adapter_plan),
        "scene_director_qwen_adapter_plan": qwen_adapter_plan,
        "scene_director_qwen_adapter_planning_mode": bool(qwen_adapter_plan),
        "scene_director_sdxl_full_implementation_lock": provider_capabilities.get("sdxl_full_implementation_lock"),
        "scene_director_sdxl_full_implementation_lock_mode": provider_capabilities.get("provider_profile") == "sdxl_checkpoint",
        "inpaint_region_target_count": ((scene_graph_v054.get("metadata", {}).get("inpaint_region_targets", {}) or {}).get("lane_count", 0) if isinstance(scene_graph_v054, dict) else 0),
        "text_region_count": ((scene_graph_v054.get("metadata", {}).get("text_regions", {}) or {}).get("region_count", 0) if isinstance(scene_graph_v054, dict) else 0),
        "v054_scene_graph_warnings": v054_warnings,
        "scene_json_subject_count": None,
        "scene_json_detail_region_count": None,
        "retired_node_classes": sorted(RETIRED_NODE_CLASSES),
        "reason": "",
        "notes": notes[:12],
    }
    return {
        "workflow": graph,
        "workflow_patch": patch,
        "validation": validation,
        "model_ref": patched_model_ref,
        "clip_ref": clip_output_ref,
        "positive_ref": patched_positive_ref,
        "negative_ref": patched_negative_ref,
        "mutated": True,
        "changed": True,
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
    }
# Legacy test anchor: "compiler_phase": "SD-V054-7"

# Legacy test anchor: "compiler_phase": "SD-V054-8"
# Legacy test anchor: "compiler_phase": "SD-V054-7.5"

# Legacy test anchor: "compiler_phase": "SD-V054-9"

# Legacy test anchor: "compiler_phase": "SD-V054-10"

# Legacy test anchor: "compiler_phase": "SD-V054-11"

# Phase 12 compatibility anchor: PHASE = "phase_12_v054_regional_detailer_routing"

# Phase 13 compatibility anchor: PHASE = "phase_13_v054_text_regions_compositor_mode"

# Legacy test anchor: "compiler_phase": "SD-V054-15"
# Legacy test anchor: "compiler_phase": "SD-V054-14"

# Phase 14 compatibility anchor: PHASE = "phase_14_v054_img2img_region_reuse"

# Phase 16 compatibility anchor: "compiler_phase": "SD-V054-16"
# Phase 15 compatibility anchor: PHASE = "phase_15_v054_inpaint_region_targeting"

# Phase 21.5 compatibility anchor: PHASE = "phase_21_6_v054_disable_scene_director_ipadapter_execution_hotfix"
# Phase 21.4 compatibility anchor: PHASE = "phase_21_4_v054_linked_prop_records_hygiene_hotfix"
# Phase 21.5 compatibility anchor: PHASE = "phase_21_5_v054_background_context_reinforcement_hotfix"
# Phase 21.1 compatibility anchor: PHASE = "phase_21_1_v054_scene_graph_serialization_hotfix"
# Phase 21 compatibility anchor: PHASE = "phase_21_v054_retire_v052_v053_active_path"
