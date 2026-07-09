from __future__ import annotations

from copy import deepcopy
from typing import Any

from .payload_schema import EXTENSION_ID, extract_scene_director_block, normalize_scene_director_payload
from .validation import validate_scene_director_payload

REPLAY_SCHEMA_VERSION = "neo.extension.replay.scene_director.v1"
RESTORE_POLICY = "revalidate_route_node_assets_regions_before_enable"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()




def extract_scene_director_source_stack(reuse: Any) -> dict[str, Any]:
    """Extract the V054 Output Inspector source stack from common reuse shapes."""
    if not isinstance(reuse, dict):
        return {}
    direct = reuse.get("source_stack")
    if isinstance(direct, dict) and direct.get("extension_id") == EXTENSION_ID:
        return deepcopy(direct)
    extensions = reuse.get("extensions") if isinstance(reuse.get("extensions"), dict) else {}
    stacks = extensions.get("source_stacks") if isinstance(extensions.get("source_stacks"), dict) else {}
    stack = stacks.get(EXTENSION_ID)
    if isinstance(stack, dict):
        return deepcopy(stack)
    memory_events = extensions.get("memory_events") if isinstance(extensions.get("memory_events"), dict) else {}
    event = memory_events.get(EXTENSION_ID)
    if isinstance(event, dict):
        for key in ("source_stack", "outputs"):
            value = event.get(key)
            if isinstance(value, dict):
                nested = value.get("source_stack") if key == "outputs" else value
                if isinstance(nested, dict):
                    return deepcopy(nested)
    replay_payload = reuse.get("replay_payload") if isinstance(reuse.get("replay_payload"), dict) else {}
    if replay_payload:
        stack = replay_payload.get("source_stack")
        if isinstance(stack, dict):
            return deepcopy(stack)
        return extract_scene_director_source_stack(replay_payload)
    return {}


def source_stack_replay_compatibility(source_stack: dict[str, Any], *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or {}
    latent = _dict(source_stack.get("saved_latent"))
    model = _dict(source_stack.get("model"))
    old_route = _dict(model.get("route"))
    old_family = _text(old_route.get("family") or old_route.get("loader"))
    new_family = _text(route.get("family") or route.get("loader"))
    latent_compatible = bool(latent.get("compatible")) and (not new_family or old_family == new_family or ("sdxl" in old_family.lower() and "sdxl" in new_family.lower()))
    return {
        "scene_graph_reusable": bool(source_stack.get("scene_graph_json")),
        "source_image_reusable": bool(source_stack.get("source_image")),
        "masks_reusable_with_size_check": True,
        "latent_replay_compatible": latent_compatible,
        "latent_reason": "matching_route" if latent_compatible else "latent_requires_matching_source_route",
        "source_route_family": old_family,
        "target_route_family": new_family,
    }

def extract_scene_director_replay_block(reuse: Any) -> dict[str, Any]:
    """Extract a Scene Director replay block from output reuse shapes.

    Supported shapes include direct extension blocks, output replay payloads,
    ``extensions.payloads``, ``extensions.replay_payloads``, and Phase K memory
    events.  The function intentionally returns a block only; route/node checks
    are performed by ``prepare_scene_director_reuse``.
    """
    if not isinstance(reuse, dict):
        return {}
    direct = extract_scene_director_block(reuse)
    if direct:
        return direct
    extensions = reuse.get("extensions") if isinstance(reuse.get("extensions"), dict) else {}
    for bucket_name in ("payloads", "replay_payloads"):
        bucket = extensions.get(bucket_name) if isinstance(extensions.get(bucket_name), dict) else {}
        block = bucket.get(EXTENSION_ID)
        if isinstance(block, dict):
            return deepcopy(block)
        nested = bucket.get("extensions") if isinstance(bucket.get("extensions"), dict) else {}
        nested_block = nested.get(EXTENSION_ID)
        if isinstance(nested_block, dict):
            return deepcopy(nested_block)
    memory_events = extensions.get("memory_events") if isinstance(extensions.get("memory_events"), dict) else {}
    event = memory_events.get(EXTENSION_ID)
    if isinstance(event, dict):
        replay_payload = event.get("replay_payload")
        if isinstance(replay_payload, dict):
            block = extract_scene_director_block(replay_payload)
            if block:
                return block
            if replay_payload.get("enabled") is not None:
                return deepcopy(replay_payload)
    replay_payload = reuse.get("replay_payload") if isinstance(reuse.get("replay_payload"), dict) else {}
    if replay_payload:
        return extract_scene_director_replay_block(replay_payload)
    return {}


def _asset_catalog(available_assets: Any) -> dict[str, set[str]]:
    catalog: dict[str, set[str]] = {"identity_profiles": set(), "reference_images": set(), "ipadapter_images": set(), "loras": set()}
    if not isinstance(available_assets, dict):
        return catalog
    for key in catalog:
        value = available_assets.get(key)
        if isinstance(value, dict):
            items = value.keys()
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = []
        catalog[key] = {str(item).strip() for item in items if str(item).strip()}
    return catalog


def _asset_warnings(block: dict[str, Any], available_assets: Any) -> list[dict[str, str]]:
    catalog = _asset_catalog(available_assets)
    if not any(catalog.values()):
        return []
    notes: list[dict[str, str]] = []
    inputs = _dict(block.get("inputs"))
    regions = _list(inputs.get("regions"))
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        identity = _dict(region.get("identity"))
        profile_id = _text(identity.get("profile_id") or region.get("identity_profile_id") or region.get("character_profile_id"))
        profile_name = _text(identity.get("profile_name") or region.get("identity_profile_name") or region.get("character_profile_name"))
        reference_image = _text(identity.get("reference_image") or region.get("reference_image") or region.get("image_name"))
        if profile_id and catalog["identity_profiles"] and profile_id not in catalog["identity_profiles"]:
            notes.append({"level": "warning", "code": "missing_identity_profile", "field": f"inputs.regions[{index}].identity.profile_id", "message": f"Identity profile {profile_id} is not available for replay."})
        if profile_name and catalog["identity_profiles"] and profile_name not in catalog["identity_profiles"]:
            notes.append({"level": "warning", "code": "missing_identity_profile", "field": f"inputs.regions[{index}].identity.profile_name", "message": f"Identity profile {profile_name} is not available for replay."})
        if reference_image and catalog["reference_images"] and reference_image not in catalog["reference_images"] and reference_image not in catalog["ipadapter_images"]:
            notes.append({"level": "warning", "code": "missing_reference_image", "field": f"inputs.regions[{index}].identity.reference_image", "message": f"Reference image {reference_image} is not available for replay."})
    return notes


def prepare_scene_director_reuse(
    reuse: Any,
    *,
    route: dict[str, Any] | None = None,
    object_info: Any = None,
    available_assets: Any = None,
    allow_gated_restore: bool = True,
) -> dict[str, Any]:
    """Prepare Scene Director settings from an output replay/reuse payload.

    The returned block is re-normalized against the *current* route and node
    catalog.  ``should_enable`` is true only when validation says the extension
    can safely emit a workflow patch now.  If route/node/assets are stale, the
    user state is preserved for review but active workflow mutation stays off.
    """
    original_block = extract_scene_director_replay_block(reuse)
    source_stack = extract_scene_director_source_stack(reuse)
    stack_compatibility = source_stack_replay_compatibility(source_stack, route=route or {}) if source_stack else {}
    if not original_block:
        return {
            "schema": REPLAY_SCHEMA_VERSION,
            "extension_id": EXTENSION_ID,
            "restore_policy": RESTORE_POLICY,
            "ok": False,
            "should_restore": False,
            "should_enable": False,
            "activation_state": "no_replay_payload",
            "reason": "No Scene Director replay payload was found.",
            "restored_block": {},
            "validation": {},
            "source_stack": source_stack,
            "source_stack_compatibility": stack_compatibility,
            "notes": [{"level": "warning", "code": "missing_replay_payload", "message": "No Scene Director replay payload was found."}],
        }

    normalized_payload = normalize_scene_director_payload({"extensions": {EXTENSION_ID: original_block}}, route=route or {}, object_info=object_info)
    block = normalized_payload["extensions"][EXTENSION_ID]
    validation = validate_scene_director_payload({"extensions": {EXTENSION_ID: original_block}}, route=route or {}, object_info=object_info)
    asset_notes = _asset_warnings(block, available_assets)
    validation_notes = []
    for error in _list(validation.get("errors")):
        validation_notes.append({"level": "error", "code": _text(error.get("code") if isinstance(error, dict) else error), "message": _text(error.get("message") if isinstance(error, dict) else error)})
    for warning in _list(validation.get("warnings")):
        validation_notes.append({"level": "warning", "code": _text(warning.get("code") if isinstance(warning, dict) else warning), "message": _text(warning.get("message") if isinstance(warning, dict) else warning)})
    notes = validation_notes + asset_notes
    can_patch = bool(validation.get("can_emit_workflow_patch") and not asset_notes)
    should_restore = bool(block)
    should_enable = bool(can_patch)
    if not should_enable and not allow_gated_restore:
        block = deepcopy(block)
        block["enabled"] = False
        block.setdefault("metadata", {})["replay_restore_disabled"] = True
    block.setdefault("metadata", {})
    if source_stack:
        block["source_stack"] = deepcopy(source_stack)
    block["metadata"].update({
        "replay_schema": REPLAY_SCHEMA_VERSION,
        "replay_source": "output_reuse",
        "restore_policy": RESTORE_POLICY,
        "replay_revalidated": True,
        "replay_enable_after_revalidation": should_enable,
        "replay_restore_state": "ready" if should_enable else "gated_for_review",
        "source_stack_schema": source_stack.get("schema") if source_stack else "",
        "source_stack_phase": source_stack.get("phase") if source_stack else "",
        "source_stack_compatibility": stack_compatibility,
    })
    activation_state = "restored_enabled" if should_enable else ("restored_gated" if should_restore else "not_restored")
    reason = "ready" if should_enable else str(block.get("metadata", {}).get("gated_reason") or validation.get("gated_reason") or "Replay restored for review; revalidation blocked auto-enable.")
    return {
        "schema": REPLAY_SCHEMA_VERSION,
        "extension_id": EXTENSION_ID,
        "restore_policy": RESTORE_POLICY,
        "ok": bool(should_restore),
        "should_restore": should_restore,
        "should_enable": should_enable,
        "activation_state": activation_state,
        "reason": reason,
        "restored_block": block,
        "normalized_payload": {"extensions": {EXTENSION_ID: block}},
        "validation": validation,
        "source_stack": source_stack,
        "source_stack_compatibility": stack_compatibility,
        "region_branch_actions": _list(source_stack.get("region_branch_actions")) if source_stack else [],
        "notes": notes,
    }


def build_scene_director_reuse_event(
    block: dict[str, Any],
    *,
    route: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _dict(block.get("metadata"))
    source_stack = _dict(block.get("source_stack"))
    return {
        "schema": REPLAY_SCHEMA_VERSION,
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "workspace_app": "generations",
        "surface": "image",
        "restore_policy": RESTORE_POLICY,
        "route": deepcopy(route or metadata.get("route") or {}),
        "revalidation_required": True,
        "asset_revalidation_required": True,
        "node_revalidation_required": True,
        "region_revalidation_required": True,
        "source_stack": deepcopy(source_stack),
        "source_stack_compatibility": source_stack_replay_compatibility(source_stack, route=route or metadata.get("route") or {}) if source_stack else {},
        "region_branch_actions": _list(source_stack.get("region_branch_actions")) if source_stack else [],
        "replay_payload": deepcopy(block),
        "validation": deepcopy(validation or {}),
        "assistant_summary": "Scene Director replay is prepared; route, node, assets, and regions must be revalidated before enabling workflow patching.",
    }
