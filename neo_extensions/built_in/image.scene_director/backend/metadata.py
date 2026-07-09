from __future__ import annotations

from copy import deepcopy
from typing import Any

from .support_matrix import EXTENSION_ID
from .provider_capabilities import resolve_provider_capabilities_v054
from .flux_adapter import build_flux_adapter_plan_v054
from .qwen_adapter import build_qwen_adapter_plan_v054

METADATA_SCHEMA_VERSION = "neo.extension.metadata.scene_director.v1"

SOURCE_STACK_SCHEMA_VERSION = "neo.extension.source_stack.scene_director.v054.v1"


def _scene_graph(inputs: dict[str, Any], *, active_regions: list[Any] | None = None) -> dict[str, Any]:
    graph = inputs.get("scene_graph_json") or inputs.get("scene_graph")
    if isinstance(graph, dict) and graph:
        return deepcopy(graph)
    regions = active_regions if isinstance(active_regions, list) else _list(inputs.get("regions"))
    if regions:
        converted = []
        for index, region in enumerate(regions):
            if not isinstance(region, dict):
                continue
            bbox = region.get("bbox")
            if isinstance(bbox, dict):
                x = float(bbox.get("x") or 0); y = float(bbox.get("y") or 0); w = float(bbox.get("w") or 0); h = float(bbox.get("h") or 0)
                bbox = [x, y, x + w, y + h]
            converted.append({
                "id": region.get("id") or f"region_{index + 1}",
                "role": region.get("role") or region.get("type") or "custom",
                "label": region.get("label") or region.get("id") or f"Region {index + 1}",
                "bbox": deepcopy(bbox if isinstance(bbox, list) else [0, 0, 1, 1]),
                "prompt": region.get("prompt") or region.get("positive_prompt") or "",
                "negative": region.get("negative") or region.get("negative_prompt") or "",
            })
        return {"version": "v054", "regions": converted, "global": deepcopy(_dict(inputs.get("global"))), "metadata": {"source_stack_fallback": "normalized_regions"}}
    return {}


def _source_stack_latent_compatibility(route: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    family = str(route.get("family") or route.get("model_family") or route.get("loader") or "").lower()
    node = str(_dict(patch.get("node_status")).get("selected_node") or patch.get("node") or "")
    latent_ref = patch.get("scene_director_lora_final_latent_ref") or patch.get("latent_ref") or patch.get("saved_latent_ref")
    sdxl_like = any(token in family for token in ("sdxl", "xl", "checkpoint")) or "V054" in node
    compatible = bool(latent_ref and sdxl_like)
    return {
        "compatible": compatible,
        "reason": "sdxl_route_with_saved_latent" if compatible else "latent_replay_requires_matching_sdxl_route_and_saved_latent",
        "latent_ref": deepcopy(latent_ref),
        "route_family": family,
        "node": node,
    }


def _region_branch_actions(scene_graph: dict[str, Any], patch: dict[str, Any]) -> list[dict[str, Any]]:
    regions = _list(scene_graph.get("regions"))
    actions: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        rid = region.get("id")
        role = region.get("role") or region.get("type")
        label = region.get("label") or rid
        base = {"region_id": rid, "label": label, "role": role}
        actions.append({**base, "action": "replay_region", "label_action": "Replay this region setup"})
        edit = region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}
        if edit:
            actions.append({**base, "action": f"img2img_{edit.get('mode') or 'modify'}", "edit_intent": deepcopy(edit)})
        inpaint = region.get("inpaint") if isinstance(region.get("inpaint"), dict) else {}
        if inpaint and inpaint.get("enabled"):
            actions.append({**base, "action": "open_region_in_inpaint", "inpaint": deepcopy(inpaint)})
        if role in ("text",):
            actions.append({**base, "action": "edit_text_plate", "text": deepcopy(region.get("text") or region.get("text_content"))})
    for unit in _list(patch.get("regional_detailer_units")):
        if isinstance(unit, dict):
            actions.append({"region_id": unit.get("region_id"), "action": "rerun_detailer_pass", "detailer": deepcopy(unit)})
    return actions


def build_scene_director_source_stack(
    validation_result: dict[str, Any],
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block = _dict(validation_result.get("block"))
    inputs = _dict(block.get("inputs"))
    params = _dict(block.get("params"))
    assets = _dict(block.get("assets"))
    patch = _dict(workflow_patch)
    route_data = deepcopy(_dict(route) or _dict(validation_result.get("route")) or _dict(_dict(block.get("metadata")).get("route")))
    graph = _scene_graph(inputs, active_regions=_list(validation_result.get("active_regions")))
    graph_meta = _dict(graph.get("metadata"))
    node_status = _dict(patch.get("node_status"))
    mask_outputs = {
        "region_masks": "scene_node_region_outputs",
        "subject_masks": ["subject_1_mask", "subject_2_mask", "subject_3_mask", "subject_4_mask"],
        "detail_masks": "NeoSceneDirectorV054.detail_masks",
        "background_masks": "NeoSceneDirectorV054.background_masks",
        "control_masks": "NeoSceneDirectorV054.control_masks",
        "inpaint_masks": "NeoSceneDirectorV054.inpaint_masks",
        "mask_index": deepcopy(graph_meta.get("mask_index") or {}),
    }
    prompt_blocks = {
        "global": deepcopy(_dict(graph.get("global"))),
        "compiled": {
            "linked_detail_lanes": deepcopy(graph_meta.get("linked_detail_lanes") or patch.get("linked_detail_lanes") or []),
            "relationship_plan": deepcopy(graph_meta.get("relationship_plan") or []),
            "background_plan": deepcopy(graph_meta.get("background_plan") or []),
            "conflict_plan": deepcopy(graph_meta.get("conflict_plan") or []),
            "text_region_plan": deepcopy(graph_meta.get("text_regions") or {}),
        },
    }
    provider_capabilities = deepcopy(graph_meta.get("provider_capabilities") or patch.get("scene_director_provider_capabilities") or resolve_provider_capabilities_v054(route_data))
    flux_adapter_plan = deepcopy(graph_meta.get("flux_adapter_plan") or patch.get("scene_director_flux_adapter_plan") or provider_capabilities.get("flux_adapter_plan"))
    if provider_capabilities.get("provider_profile") == "flux_adapter_planned" and not flux_adapter_plan:
        flux_adapter_plan = build_flux_adapter_plan_v054(graph, route=route_data)
    qwen_adapter_plan = deepcopy(graph_meta.get("qwen_adapter_plan") or patch.get("scene_director_qwen_adapter_plan") or provider_capabilities.get("qwen_adapter_plan"))
    if provider_capabilities.get("provider_profile") == "qwen_semantic_edit_adapter" and not qwen_adapter_plan:
        qwen_adapter_plan = build_qwen_adapter_plan_v054(graph, route=route_data)
    stack = {
        "schema": SOURCE_STACK_SCHEMA_VERSION,
        "phase": "SD-V054-21",
        "legacy_qwen_adapter_phase_anchor": "SD-V054-20",
        "legacy_phase_anchor": "SD-V054-16",
        "legacy_provider_capability_phase_anchor": "SD-V054-17",
        "legacy_flux_adapter_phase_anchor": "SD-V054-19",
        "legacy_sdxl_full_lock_phase_anchor": "SD-V054-18",
        "extension_id": EXTENSION_ID,
        "source_kind": "scene_director_output",
        "scene_graph_json": deepcopy(graph),
        "prompt_blocks": prompt_blocks,
        "mask_outputs": mask_outputs,
        "provider_capabilities": provider_capabilities,
        "flux_adapter_plan": flux_adapter_plan,
        "qwen_adapter_plan": qwen_adapter_plan,
        "sdxl_full_implementation_lock": provider_capabilities.get("sdxl_full_implementation_lock"),
        "model": {
            "route": route_data,
            "node": node_status.get("selected_node") or patch.get("node"),
            "workflow_route": patch.get("workflow_route") or patch.get("route_state"),
        },
        "sampler": {
            "sampler_node_id": patch.get("sampler_node_id"),
            "positive_ref": deepcopy(patch.get("patched_positive_ref")),
            "negative_ref": deepcopy(patch.get("patched_negative_ref")),
            "model_ref": deepcopy(patch.get("patched_model_ref")),
        },
        "source_image": deepcopy(inputs.get("source_image") or params.get("source_image") or graph_meta.get("source_image")),
        "saved_latent": _source_stack_latent_compatibility(route_data, patch) if provider_capabilities.get("features", {}).get("latent_replay") else {"compatible": False, "reason": "latent_replay_disabled_for_provider", "route_family": provider_capabilities.get("family")},
        "controlnet_bindings": deepcopy(_list(patch.get("regional_controlnet_units"))),
        "detailer_bindings": deepcopy(_list(patch.get("regional_detailer_units"))),
        "text_compositor": deepcopy(graph_meta.get("text_regions") or patch.get("scene_director_text_regions") or {}),
        "img2img_region_reuse": deepcopy(graph_meta.get("img2img_region_reuse") or patch.get("scene_director_img2img_region_reuse") or {}),
        "inpaint_region_targets": deepcopy(graph_meta.get("inpaint_region_targets") or patch.get("scene_director_inpaint_region_targets") or {}),
        "region_branch_actions": _region_branch_actions(graph, patch),
        "regional_adapter_visibility": deepcopy(patch.get("scene_director_regional_adapter_visibility") or {}),
        "extension_routing_authority": deepcopy(patch.get("scene_director_extension_routing_authority") or {}),
        "extension_authority_routes": deepcopy(patch.get("scene_director_extension_authority_routes") or {}),
        "regional_lora_crop_refinement": deepcopy(patch.get("scene_director_regional_lora_crop_refinement") or {}),
        "postpass_character_lock_gate": deepcopy(patch.get("scene_director_postpass_character_lock_gate") or {}),
        "background_separation_guard": deepcopy(patch.get("scene_director_background_separation_guard") or {}),
        "replay_policy": {
            "scene_graph_reusable_across_providers": True,
            "source_image_reusable_across_providers": True,
            "masks_require_size_match": True,
            "latent_requires_matching_route": True,
        },
    }
    return stack


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _warning_code(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("code") or value.get("message") or value.get("warning") or "").strip()
    return str(value or "").strip()


def _warning_set(value: Any) -> set[str]:
    return {code for code in (_warning_code(item) for item in _list(value)) if code}


def _label_route(route: dict[str, Any]) -> str:
    parts = [
        route.get("backend") or route.get("provider") or "unknown",
        route.get("family") or "unknown_family",
        route.get("loader") or "unknown_loader",
        route.get("workflow_mode") or route.get("mode") or "generate",
    ]
    return ":".join(str(part) for part in parts if part not in (None, ""))


def _region_label(region: dict[str, Any], index: int) -> str:
    return str(region.get("label") or region.get("id") or f"Region {index + 1}")


def _region_summary(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        identity = region.get("identity") if isinstance(region.get("identity"), dict) else {}
        mask = region.get("mask") if isinstance(region.get("mask"), dict) else {}
        summary.append({
            "id": region.get("id") or f"scene_region_{index + 1}",
            "label": _region_label(region, index),
            "type": region.get("type") or "object",
            "has_prompt": bool(str(region.get("prompt") or "").strip()),
            "has_negative_prompt": bool(str(region.get("negative_prompt") or "").strip()),
            "has_identity_reference": bool(identity.get("profile_id") or identity.get("profile_name") or identity.get("reference_image")),
            "strength": region.get("strength"),
            "bbox": deepcopy(bbox),
            "mask": {
                "source": mask.get("source") or "region_box",
                "feather": mask.get("feather"),
                "refine_requested": bool(mask.get("refine_requested")),
            },
        })
    return summary


def _binding_summary(bindings: list[Any], *, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        row = {
            "kind": kind,
            "region_id": item.get("region_id"),
            "region_index": item.get("region_index"),
            "slot": item.get("slot"),
            "source": item.get("source"),
        }
        if kind == "ipadapter":
            row.update({
                "use_region_mask": bool(item.get("use_region_mask")),
                "weight": item.get("weight"),
                "start_at": item.get("start_at"),
                "end_at": item.get("end_at"),
            })
        if kind == "lora":
            row.update({"strength": item.get("strength")})
        rows.append(row)
    return rows


def build_output_extension_metadata(
    validation_result: dict[str, Any],
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Scene Director output metadata for Neo's extension slots.

    Phase K owns the compact Output Inspector data, assistant summary, replay
    payload, and memory-readiness event shape.  The function intentionally uses
    the normalized extension block from validation instead of legacy V1 flat
    keys, while preserving V1-relevant counts/bindings in structured metadata.
    """
    block = _dict(validation_result.get("block"))
    inputs = _dict(block.get("inputs"))
    params = _dict(block.get("params"))
    assets = _dict(block.get("assets"))
    block_metadata = _dict(block.get("metadata"))
    patch = _dict(workflow_patch)
    route_data = deepcopy(_dict(route) or _dict(validation_result.get("route")) or _dict(block_metadata.get("route")))
    regions = _list(validation_result.get("active_regions")) or _list(inputs.get("regions"))
    region_count = int(block_metadata.get("regional_count") or validation_result.get("regional_count") or len(regions) or 0)
    subject_slot_resolver = _dict(patch.get("scene_director_subject_slot_resolver"))
    subject_slot_by_region = _dict(subject_slot_resolver.get("subject_slot_by_region"))
    subject_count = int(
        patch.get("subject_count")
        or len(subject_slot_by_region)
        or block_metadata.get("subject_count")
        or validation_result.get("subject_count")
        or 0
    )
    detail_region_count = int(block_metadata.get("detail_region_count") or validation_result.get("detail_region_count") or 0)
    ip_bindings = _list(assets.get("ipadapter_bindings"))
    lora_bindings = _list(assets.get("lora_bindings"))
    identity_units = _list(assets.get("identity_units"))
    node_status = _dict(patch.get("node_status")) or _dict(validation_result.get("node_status")) or _dict(block_metadata.get("node_status"))
    applied = bool(patch.get("applied") or patch.get("mutated"))
    enabled = bool(block.get("enabled"))
    route_state = str(block_metadata.get("route_state") or validation_result.get("route_state") or patch.get("route_state") or "")
    reason = str(block_metadata.get("gated_reason") or block_metadata.get("reason") or patch.get("reason") or validation_result.get("gated_reason") or "")
    selected_node = str(node_status.get("selected_node") or patch.get("node") or patch.get("node_class") or "")
    status = "applied" if enabled and applied else ("validated_gated" if enabled else "disabled")
    workflow_summary = (
        f"Scene Director {status.replace('_', ' ')} on {_label_route(route_data)}: "
        f"{region_count} region(s), {subject_count} character subject(s), {detail_region_count} detail/background/style region(s)."
    )
    if selected_node:
        workflow_summary += f" Node: {selected_node}."
    if reason:
        workflow_summary += f" Reason: {reason}."
    assistant_summary = workflow_summary
    adapter_visibility = _dict(patch.get("scene_director_regional_adapter_visibility"))
    ip_vis = _dict(adapter_visibility.get("ipadapter"))
    if ip_vis.get("profile_preserved_metadata_only") or (not ip_vis.get("owner_enabled", True) and int(ip_vis.get("identity_units_planned") or 0) > 0):
        assistant_summary += " IPAdapter profile was preserved, but image.ip_adapter is disabled so Scene Director did not execute regional IPAdapter/FaceID."
    elif ip_vis.get("scene_director_applied"):
        assistant_summary += (
            f" Standalone IP Adapter extension was not applied; "
            f"Scene Director regional IPAdapter/FaceID was applied to {int(ip_vis.get('region_count') or 0)} region(s)."
        )
    preservation = _dict(patch.get("scene_director_ipadapter_instruction_preservation"))
    if preservation.get("status") in {"applied", "disabled_metadata_only"}:
        warnings = _warning_set(preservation.get("warnings"))
        if preservation.get("status") == "disabled_metadata_only":
            assistant_summary += " Regional IPAdapter execution is metadata-only for this run."
        if any(r.get("execution_mode") == "delayed_first_pass" for r in _list(preservation.get("routes")) if isinstance(r, dict)):
            assistant_summary += " Scene Director delayed regional IPAdapter/FaceID so identity does not override pose/composition."
        if any(r.get("execution_mode") == "second_pass_restore" for r in _list(preservation.get("routes")) if isinstance(r, dict)):
            assistant_summary += " Scene Director moved regional IPAdapter/FaceID to second-pass restore to preserve the main composition."
        if "ipadapter_weight_may_override_prompt" in warnings or "ipadapter_starts_too_early_may_override_composition" in warnings:
            assistant_summary += " IPAdapter may override Scene Director instructions because it starts early or uses high weight."
    mixer = _dict(patch.get("scene_director_regional_lora_model_delta_mixer"))
    route_auth = _dict(patch.get("scene_director_extension_routing_authority"))
    if route_auth.get("status") == "applied":
        warnings = _warning_set(route_auth.get("warnings"))
        crop_refine = _dict(patch.get("scene_director_regional_lora_crop_refinement"))
        if crop_refine.get("status") == "applied":
            assistant_summary += " Regional LoRA used an ADetailer-style crop refinement pass on the assigned mask for stronger local character influence."
            postpass_gate = _dict(patch.get("scene_director_postpass_character_lock_gate"))
            if postpass_gate.get("status") == "applied":
                assistant_summary += " Post-pass character lock guards were carried into the crop refinement pass to prevent gender/body drift."
        if mixer.get("status") == "applied":
            assistant_summary += " Regional LoRA model delta is runtime-proven and limited to the assigned region mask."
        elif mixer.get("status") == "fallback" or "regional_lora_mixer_claimed_but_no_runtime_delta" in warnings:
            assistant_summary += " Regional LoRA mixer did not provide runtime delta proof, so Scene Director used the masked visual-authority finish-pass fallback."
        elif "lora_model_delta_is_global_without_true_node_delta" in warnings:
            assistant_summary += " Regional LoRA is routed through Scene Director region conditioning; standard LoRA model delta is not physically mask-scoped yet."
        if any(str(w).startswith("ipadapter_region_mask_missing") for w in warnings):
            assistant_summary += " Regional IPAdapter could not be safely mask-confirmed in first pass; global IPAdapter routing was suppressed."
        elif any(r.get("extension_type") == "ipadapter" and r.get("hard_region_isolation") for r in _list(route_auth.get("routes")) if isinstance(r, dict)):
            assistant_summary += " Scene Director regional IPAdapter/FaceID is mask-confirmed for assigned character region(s)."

    source_stack = build_scene_director_source_stack(validation_result, workflow_patch=workflow_patch, route=route_data)

    compact_usage = {
        "extension_id": EXTENSION_ID,
        "label": "Scene Director",
        "version": block.get("version", 1),
        "extension_type": "built_in",
        "enabled": enabled,
        "status": status,
        "workspace_app": "generations",
        "surface": "image",
        "route": route_data,
        "route_state": route_state,
        "reason": reason,
        "regional_count": region_count,
        "subject_count": subject_count,
        "detail_region_count": detail_region_count,
        "ip_adapter_binding_count": len(ip_bindings),
        "lora_binding_count": len(lora_bindings),
        "identity_unit_count": len(identity_units),
        "node": selected_node,
        "node_readiness": node_status,
        "workflow_patch_applied": applied,
        "workflow_patch_allowed": bool(block_metadata.get("workflow_patch_allowed") or patch.get("workflow_patch_allowed")),
        "source_stack_schema": source_stack.get("schema"),
        "source_stack_phase": source_stack.get("phase"),
        "scene_graph_replay_ready": bool(source_stack.get("scene_graph_json")),
        "region_branch_action_count": len(_list(source_stack.get("region_branch_actions"))),
        "assistant_summary": assistant_summary,
    }

    replay_block = deepcopy(block) if block else {}
    if replay_block:
        replay_block.setdefault("metadata", {})
        replay_block["metadata"].update({
            "replay_source": "output_metadata",
            "restore_policy": "revalidate_route_node_assets_regions_before_enable",
            "workflow_patch_applied": applied,
            "workflow_summary": workflow_summary,
            "assistant_summary": assistant_summary,
            "source_stack_schema": source_stack.get("schema"),
            "source_stack_phase": source_stack.get("phase"),
        })
        replay_block["source_stack"] = deepcopy(source_stack)

    memory_event = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "workspace_app": "generations",
        "surface": "image",
        "schema": METADATA_SCHEMA_VERSION,
        "enabled": enabled,
        "status": status,
        "route": route_data,
        "assets": deepcopy(assets),
        "params": deepcopy(params),
        "outputs": {
            "workflow_patch_applied": applied,
            "node": selected_node,
            "nodes_added": deepcopy(_list(patch.get("nodes_added"))),
            "scene_node_id": patch.get("scene_node_id"),
            "source_stack": deepcopy(source_stack),
        },
        "workflow_summary": workflow_summary,
        "assistant_summary": assistant_summary,
        "replay_payload": deepcopy(replay_block),
        "source_stack": deepcopy(source_stack),
        "region_summary": _region_summary([r for r in regions if isinstance(r, dict)]),
        "binding_summary": {
            "ipadapter": _binding_summary(ip_bindings, kind="ipadapter"),
            "lora": _binding_summary(lora_bindings, kind="lora"),
        },
    }

    return {
        "used": [compact_usage],
        "replay_payloads": {EXTENSION_ID: replay_block} if replay_block else {},
        "source_stacks": {EXTENSION_ID: source_stack} if source_stack else {},
        "assistant_summary": assistant_summary,
        "memory_events": {EXTENSION_ID: memory_event},
    }

# Phase 21 compatibility anchor: SD-V054-21 Retire V052/V053 Active Path
