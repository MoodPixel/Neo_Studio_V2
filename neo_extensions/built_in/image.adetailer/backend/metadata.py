from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_NAME, EXTENSION_TYPE, WORKSPACE_APP, MOUNT_SLOT, VERSION
from .readiness import build_replay_readiness, summarize_replay_readiness
from .execution_recipe import build_execution_recipe, build_locked_replay_block


def _route_state(route: dict[str, Any] | None, validation_result: dict[str, Any] | None = None) -> str:
    support = (validation_result or {}).get("support") if isinstance((validation_result or {}).get("support"), dict) else {}
    return str(
        support.get("state")
        or (route or {}).get("route_state")
        or (route or {}).get("state")
        or "unknown"
    )


def _patch_applied(patch: dict[str, Any] | None) -> bool:
    return bool(isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")))


def _basename(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    return text.split("/")[-1] if text else ""


def _compact_params(params: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "model_source",
        "identity_protection",
        "identity_lora_revision",
        "family_preset_mode",
        "detailer_model_family",
        "detailer_checkpoint",
        "detailer_vae",
        "lora_inheritance",
        "inherit_lora_uids",
        "detailer_lora_enabled",
        "detailer_lora",
        "detailer_lora_strength_model",
        "detailer_lora_strength_clip",
        "detailer_lora_trigger",
        "detailer_loras",
        "detector_model",
        "detector_type",
        "confidence",
        "top_k",
        "bbox_grow",
        "mask_blur",
        "denoise",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "guide_size",
        "max_size",
        "noise_mask",
        "force_inpaint",
        "noise_mask_feather",
        "sam_model",
        "custom_classes",
        "target_order",
        "target_split_mode",
        "detailer_passes",
    )
    return {key: deepcopy(params.get(key)) for key in keys if params.get(key) not in (None, "", [], {})}


def _pass_summary_counts(pass_summaries: list[dict[str, Any]]) -> dict[str, int]:
    manual_units = sum(1 for item in pass_summaries if item.get("manual_box_index"))
    sep_units = sum(1 for item in pass_summaries if item.get("sep_target_total"))
    face_units = sum(1 for item in pass_summaries if item.get("patch_path") == "face_detailer")
    segs_units = sum(1 for item in pass_summaries if item.get("patch_path") in {"segs_detailer", "manual_mask_to_segs"})
    return {
        "manual_unit_count": manual_units,
        "sep_unit_count": sep_units,
        "face_detailer_unit_count": face_units,
        "segs_detailer_unit_count": segs_units,
    }


def _pass_label(item: dict[str, Any], index: int) -> str:
    label = str(item.get("label") or item.get("pass_id") or f"Pass {index + 1}").strip()
    suffixes: list[str] = []
    if item.get("manual_box_index"):
        suffixes.append(f"box {item.get('manual_box_index')}")
    if item.get("sep_target_index"):
        total = item.get("sep_target_total") or "?"
        suffixes.append(f"target {item.get('sep_target_index')}/{total}")
    return f"{label} ({', '.join(suffixes)})" if suffixes else label


def _assistant_summary(*, block: dict[str, Any], patch: dict[str, Any] | None, validation_result: dict[str, Any], route: dict[str, Any] | None, reason: str = "") -> str:
    requested_params = block.get("params") if isinstance(block.get("params"), dict) else {}
    params = patch.get("params_used") if isinstance((patch or {}).get("params_used"), dict) else requested_params
    enabled = bool(block.get("enabled"))
    applied = _patch_applied(patch)
    state = _route_state(route, validation_result)
    patch_data = patch or {}
    pass_summaries = patch_data.get("pass_summaries") if isinstance(patch_data.get("pass_summaries"), list) else []
    skipped_passes = patch_data.get("skipped_passes") if isinstance(patch_data.get("skipped_passes"), list) else []
    detector = _basename(params.get("detector_model") or patch_data.get("detector_model")) or "detector not selected"
    path = str(patch_data.get("patch_path") or "detailer").replace("_", " ")
    if not enabled:
        return "ADetailer was disabled; no Finish repair pass was applied."
    if applied:
        steps = params.get("steps")
        denoise = params.get("denoise")
        cfg = params.get("cfg")
        runtime_units = int(patch_data.get("runtime_unit_count") or len(pass_summaries) or 1)
        enabled_cards = int(patch_data.get("enabled_detailer_pass_count") or 0)
        paths = patch_data.get("patch_paths") if isinstance(patch_data.get("patch_paths"), list) else []
        counts = _pass_summary_counts(pass_summaries)
        bits = [f"{runtime_units} runtime unit(s)"]
        if enabled_cards:
            bits.append(f"{enabled_cards} enabled card(s)")
        if counts["manual_unit_count"]:
            bits.append(f"{counts['manual_unit_count']} manual box unit(s)")
        if counts["sep_unit_count"]:
            bits.append(f"{counts['sep_unit_count']} [SEP] target unit(s)")
        if skipped_passes:
            bits.append(f"{len(skipped_passes)} skipped")
        cfg_note = f", CFG {cfg}" if cfg is not None else ""
        path_note = ", ".join(str(item).replace("_", " ") for item in paths) if paths else path
        model_source = patch_data.get("detailer_model_source") if isinstance(patch_data.get("detailer_model_source"), dict) else {}
        if str(model_source.get("source") or "") == "dedicated_checkpoint":
            source_note = f"dedicated {str(model_source.get('family') or '').upper()} checkpoint {_basename(model_source.get('checkpoint'))}"
        else:
            source_note = "generation model"
        lora_branch = patch_data.get("detailer_lora_branch") if isinstance(patch_data.get("detailer_lora_branch"), dict) else {}
        lora_count = int(lora_branch.get("applied_lora_count") or 0)
        lora_note = f", {lora_count} isolated detailer LoRA(s)" if lora_count else ""
        identity = patch_data.get("identity_policy") if isinstance(patch_data.get("identity_policy"), dict) else {}
        identity_mode = str(identity.get("effective") or "none")
        identity_note = "" if identity_mode == "none" else f", identity mode {identity_mode.replace('_', ' ')}"
        return f"ADetailer applied: {path_note}, {', '.join(bits)}, {source_note}{lora_note}{identity_note}, detector {detector}, {steps} steps, denoise {denoise}{cfg_note}."
    return f"ADetailer requested but not applied ({reason or state or 'gated'})."


def build_output_extension_metadata(validation_result: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build Output Inspector, replay, and assistant-readable metadata for ADetailer.

    Phase 9 adds a fingerprinted effective execution recipe and a disabled,
    route-locked replay payload so the Output Inspector can explain exactly what
    ran while replay remains fail-closed against the current backend.
    """
    result = validation_result if isinstance(validation_result, dict) else {}
    block = result.get("block") if isinstance(result.get("block"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    normalization = metadata.get("normalization") if isinstance(metadata.get("normalization"), dict) else {}
    patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    effective_params = patch.get("params_used") if isinstance(patch.get("params_used"), dict) else params
    route_data = deepcopy(route or patch.get("route") or {})
    support = result.get("support") if isinstance(result.get("support"), dict) else {}
    node_status = result.get("node_status") if isinstance(result.get("node_status"), dict) else patch.get("node_status", {})
    derived = result.get("derived") if isinstance(result.get("derived"), dict) else {}
    enabled = bool(block.get("enabled"))
    applied = _patch_applied(patch)
    state = _route_state(route_data, result)
    reason = str(patch.get("reason") or support.get("reason") or metadata.get("reason") or "").strip()
    pass_summaries = deepcopy(patch.get("pass_summaries") if isinstance(patch.get("pass_summaries"), list) else [])
    skipped_passes = deepcopy(patch.get("skipped_passes") if isinstance(patch.get("skipped_passes"), list) else [])
    patch_paths = deepcopy(patch.get("patch_paths") if isinstance(patch.get("patch_paths"), list) else ([] if not patch.get("patch_path") else [patch.get("patch_path")]))
    pass_counts = _pass_summary_counts(pass_summaries)
    prequeue_diagnostics = deepcopy(patch.get("prequeue_diagnostics") if isinstance(patch.get("prequeue_diagnostics"), dict) else result.get("prequeue_diagnostics") if isinstance(result.get("prequeue_diagnostics"), dict) else {})
    graph_invariants = deepcopy(patch.get("graph_invariants") if isinstance(patch.get("graph_invariants"), dict) else {})
    multi_pass = {
        "detailer_pass_count": int(patch.get("detailer_pass_count") or derived.get("detailer_pass_count") or normalization.get("detailer_pass_count") or 0),
        "enabled_detailer_pass_count": int(patch.get("enabled_detailer_pass_count") or derived.get("enabled_detailer_pass_count") or normalization.get("enabled_detailer_pass_count") or 0),
        "runtime_unit_count": int(patch.get("runtime_unit_count") or len(pass_summaries)),
        "patch_paths": patch_paths,
        "pass_summaries": pass_summaries,
        "skipped_passes": skipped_passes,
        "manual_unit_count": pass_counts["manual_unit_count"],
        "sep_unit_count": pass_counts["sep_unit_count"],
        "face_detailer_unit_count": pass_counts["face_detailer_unit_count"],
        "segs_detailer_unit_count": pass_counts["segs_detailer_unit_count"],
    }
    execution_recipe = build_execution_recipe(result, patch, route_data)
    safe_replay = build_locked_replay_block(block, execution_recipe) if block else {}
    replay_readiness = build_replay_readiness(safe_replay or block, route=route_data, node_status=node_status, workflow_patch=patch)
    memory_readiness_summary = summarize_replay_readiness(replay_readiness)
    outputs = {
        "workflow_patch_applied": applied,
        "patch_path": patch.get("patch_path") or "none",
        "patch_paths": patch_paths,
        "node": patch.get("node") or patch.get("node_class") or "",
        "node_ids": deepcopy(patch.get("node_ids") if isinstance(patch.get("node_ids"), list) else []),
        "previous_image_ref": deepcopy(patch.get("previous_image_ref") if isinstance(patch.get("previous_image_ref"), list) else []),
        "patched_image_ref": deepcopy(patch.get("patched_image_ref") if isinstance(patch.get("patched_image_ref"), list) else []),
        "output_consumers": deepcopy(patch.get("output_consumers") if isinstance(patch.get("output_consumers"), list) else []),
        "detailer_model_source": deepcopy(patch.get("detailer_model_source") if isinstance(patch.get("detailer_model_source"), dict) else {}),
        "detailer_lora_branch": deepcopy(patch.get("detailer_lora_branch") if isinstance(patch.get("detailer_lora_branch"), dict) else {}),
        "identity_policy": deepcopy(patch.get("identity_policy") if isinstance(patch.get("identity_policy"), dict) else {}),
        "family_preset": deepcopy(patch.get("family_preset") if isinstance(patch.get("family_preset"), dict) else {}),
        "multi_pass": deepcopy(multi_pass),
        "prequeue_diagnostics": deepcopy(prequeue_diagnostics),
        "graph_invariants": deepcopy(graph_invariants),
        "execution_recipe": deepcopy(execution_recipe),
    }
    assistant_summary = _assistant_summary(block=block, patch=patch, validation_result=result, route=route_data, reason=reason)
    memory_event = {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": deepcopy(route_data),
        "assets": deepcopy(assets),
        "params": deepcopy(_compact_params(effective_params)),
        "requested_params": deepcopy(_compact_params(params)),
        "outputs": deepcopy(outputs),
        "workflow_summary": assistant_summary,
        "assistant_summary": assistant_summary,
        "replay_payload": {"extensions": {EXTENSION_ID: safe_replay}} if safe_replay else {},
        "replay_readiness": deepcopy(replay_readiness),
        "memory_readiness": {
            "ready_for_memory": True,
            "ready_for_replay_restore": True,
            "ready_to_auto_enable": False,
            "summary": memory_readiness_summary,
            "checklist": deepcopy(replay_readiness.get("checklist") or {}),
            "blockers": deepcopy(replay_readiness.get("blockers") or []),
        },
        "node_availability": deepcopy(node_status or {}),
        "gated_reason": reason if not applied else "",
        "multi_pass": deepcopy(multi_pass),
        "prequeue_diagnostics": deepcopy(prequeue_diagnostics),
        "graph_invariants": deepcopy(graph_invariants),
        "execution_recipe": deepcopy(execution_recipe),
    }
    status = "used" if applied else ("gated" if enabled else "disabled")
    used_entry = {
        "extension_id": EXTENSION_ID,
        "label": EXTENSION_NAME,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "mount_slot": MOUNT_SLOT,
        "version": VERSION,
        "enabled": enabled,
        "status": status,
        "state": state,
        "route_state": state,
        "workflow_patch_applied": applied,
        "workflow_patch_allowed": bool(result.get("workflow_patch_allowed")),
        "node": patch.get("node") or patch.get("node_class") or "",
        "node_status": deepcopy(node_status or {}),
        "optional_capabilities": deepcopy((node_status or {}).get("capabilities") or {}),
        "reason": reason,
        "detector_model": params.get("detector_model") or patch.get("detector_model") or "",
        "model_source": params.get("model_source") or "generation_model",
        "detailer_model_family": params.get("detailer_model_family") or "",
        "detailer_checkpoint": params.get("detailer_checkpoint") or "",
        "detailer_vae": params.get("detailer_vae") or "",
        "detailer_model_source": deepcopy(patch.get("detailer_model_source") if isinstance(patch.get("detailer_model_source"), dict) else {}),
        "detailer_lora_branch": deepcopy(patch.get("detailer_lora_branch") if isinstance(patch.get("detailer_lora_branch"), dict) else {}),
        "identity_policy": deepcopy(patch.get("identity_policy") if isinstance(patch.get("identity_policy"), dict) else {}),
        "identity_protection": params.get("identity_protection") or "none",
        "identity_lora_revision": params.get("identity_lora_revision") or "route_family",
        "family_preset": deepcopy(patch.get("family_preset") if isinstance(patch.get("family_preset"), dict) else {}),
        "family_preset_mode": params.get("family_preset_mode") or "auto_family",
        "lora_inheritance": params.get("lora_inheritance") or "inherit_all",
        "detailer_lora": params.get("detailer_lora") or "",
        "patch_path": patch.get("patch_path") or "none",
        "patch_paths": patch_paths,
        "steps": effective_params.get("steps"),
        "denoise": effective_params.get("denoise"),
        "cfg": effective_params.get("cfg"),
        "sampler_name": effective_params.get("sampler_name"),
        "scheduler": effective_params.get("scheduler"),
        "guide_size": effective_params.get("guide_size"),
        "max_size": effective_params.get("max_size"),
        "detailer_pass_count": multi_pass["detailer_pass_count"],
        "enabled_detailer_pass_count": multi_pass["enabled_detailer_pass_count"],
        "runtime_unit_count": multi_pass["runtime_unit_count"],
        "manual_unit_count": multi_pass["manual_unit_count"],
        "sep_unit_count": multi_pass["sep_unit_count"],
        "skipped_pass_count": len(skipped_passes),
        "assistant_summary": assistant_summary,
        "diagnostic_state": prequeue_diagnostics.get("state") or ("ready" if applied else "not_ready"),
        "diagnostic_blocker_codes": deepcopy(prequeue_diagnostics.get("blocker_codes") or []),
        "graph_invariants_ready": bool(graph_invariants.get("ready")),
        "execution_recipe_fingerprint": execution_recipe.get("fingerprint") or "",
        "replay_recipe_locked": bool((execution_recipe.get("replay") or {}).get("locked")),
    }
    metadata_block = deepcopy(block)
    if metadata_block:
        metadata_block.setdefault("metadata", {})
        if isinstance(metadata_block["metadata"], dict):
            metadata_block["metadata"].update({
                "assistant_summary": assistant_summary,
                "multi_pass": deepcopy(multi_pass),
                "pass_summaries": deepcopy(pass_summaries),
                "skipped_passes": deepcopy(skipped_passes),
                "patch_paths": deepcopy(patch_paths),
                "detailer_model_source": deepcopy(patch.get("detailer_model_source") if isinstance(patch.get("detailer_model_source"), dict) else {}),
                "detailer_lora_branch": deepcopy(patch.get("detailer_lora_branch") if isinstance(patch.get("detailer_lora_branch"), dict) else {}),
                "identity_policy": deepcopy(patch.get("identity_policy") if isinstance(patch.get("identity_policy"), dict) else {}),
                "family_preset": deepcopy(patch.get("family_preset") if isinstance(patch.get("family_preset"), dict) else {}),
                "effective_params": deepcopy(_compact_params(effective_params)),
                "source_phase": "Phase 9",
                "prequeue_diagnostics": deepcopy(prequeue_diagnostics),
                "graph_invariants": deepcopy(graph_invariants),
                "execution_recipe": deepcopy(execution_recipe),
                "execution_recipe_fingerprint": execution_recipe.get("fingerprint") or "",
                "replay_readiness": deepcopy(replay_readiness),
                "memory_readiness": {
                    "ready_for_memory": True,
                    "ready_for_replay_restore": True,
                    "ready_to_auto_enable": False,
                    "summary": memory_readiness_summary,
                    "checklist": deepcopy(replay_readiness.get("checklist") or {}),
                    "blockers": deepcopy(replay_readiness.get("blockers") or []),
                },
            })
    return {
        "used": [used_entry],
        "payloads": {EXTENSION_ID: metadata_block} if metadata_block else {},
        "workflow_patches": [deepcopy(patch)] if patch else [],
        "validation": deepcopy(result.get("validation") or []),
        "replay_payloads": {EXTENSION_ID: safe_replay} if safe_replay else {},
        "assistant_summary": assistant_summary,
        "assistant_summaries": {EXTENSION_ID: assistant_summary},
        "memory_events": {EXTENSION_ID: memory_event},
        "replay_readiness": {EXTENSION_ID: deepcopy(replay_readiness)},
        "prequeue_diagnostics": {EXTENSION_ID: deepcopy(prequeue_diagnostics)},
        "graph_invariants": {EXTENSION_ID: deepcopy(graph_invariants)},
    }
