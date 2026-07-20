from __future__ import annotations

from typing import Any

from .constants import EXTENSION_ID, EXTENSION_VERSION


def build_background_removal_extension_usage(*, params: dict[str, Any], route: dict[str, Any], node_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "version": EXTENSION_VERSION,
        "enabled": True,
        "route": route,
        "params": params,
        "node_status": node_status,
    }


def build_background_removal_metadata(
    *,
    route: dict[str, Any],
    params: dict[str, Any],
    assets: dict[str, Any],
    payload_block: dict[str, Any],
    node_status: dict[str, Any],
    compile_notes: list[str],
) -> dict[str, Any]:
    workflow_mode = str(params.get("workflow_mode") or "segment")
    engine = str(params.get("resolved_engine") or params.get("engine") or "comfy_birefnet")
    model = str(params.get("resolved_model") or params.get("rmbg_model") or params.get("model") or params.get("native_model") or "BiRefNet")
    preset = str(params.get("preset") or "smart_auto").replace("_", " ")
    if workflow_mode == "refine_mask":
        assistant_summary = "Background Removal mask refinement reused the reviewed mask without rerunning BiRefNet segmentation"
        if params.get("mask_expand"):
            assistant_summary += f", offset {int(params.get('mask_expand') or 0):+d}px"
        if params.get("mask_feather"):
            assistant_summary += f", feather {int(params.get('mask_feather') or 0)}px"
        assistant_summary += "; transparent PNG"
    elif workflow_mode == "interactive_sam":
        subjects = [item for item in (params.get("sam_subjects") or []) if item.get("selected", True)]
        prompts = list(params.get("sam_prompts") or [])
        if subjects:
            keep_count = sum(len(item.get("keep_points") or []) for item in subjects)
            remove_count = sum(len(item.get("remove_points") or []) for item in subjects)
            box_count = sum(1 for item in subjects if item.get("bbox"))
        else:
            keep_count = sum(1 for item in prompts if item.get("type") == "point" and int(item.get("label") or 0) == 1)
            remove_count = sum(1 for item in prompts if item.get("type") == "point" and int(item.get("label") or 0) == 0)
            box_count = sum(1 for item in prompts if item.get("type") == "rectangle")
        execution = str(params.get("sam_execution_resolved") or params.get("resolved_engine") or params.get("sam_execution") or "auto")
        assistant_summary = (
            f"Interactive SAM kept {len(subjects) or 1} selected subject(s) with {box_count} box(es), "
            f"{keep_count} keep point(s), and {remove_count} remove point(s) via {execution}"
        )
        if params.get("sam_refine_mode") == "birefnet_gate":
            edge_model = params.get("model") if params.get("resolved_engine") == "comfy_sam" else params.get("sam_refine_model")
            assistant_summary += f"; BiRefNet edge refinement requested with {edge_model or 'BiRefNet'}"
        assistant_summary += "; transparent PNG"
    elif workflow_mode == "segmentation_lab":
        prompt_rows = [item for item in (params.get("segmentation_lab_prompts") or []) if item.get("enabled", True)]
        adapter = str(params.get("segmentation_adapter") or "auto")
        operation = str(params.get("segmentation_mask_operation") or "union")
        assistant_summary = f"Segmentation Lab selected {len(prompt_rows)} prompted object mask(s) with {adapter} and {operation} operation; transparent PNG"
    elif workflow_mode == "region_segmentation":
        targets = [item for item in (params.get("region_segmentation_targets") or []) if item.get("enabled", True)]
        adapter = str(params.get("region_segmentation_adapter") or "auto")
        operation = str(params.get("region_segmentation_mask_operation") or "union")
        assistant_summary = f"Face, clothes, and fashion segmentation selected {len(targets)} region target(s) with {adapter} and {operation} operation; transparent PNG"
    elif workflow_mode == "mask_utility":
        operation = str(params.get("mask_utility_operation") or "enhance")
        mask_count = len(params.get("mask_utility_mask_names") or [])
        assistant_summary = f"RMBG mask utility {operation} processed {mask_count} uploaded mask image(s); derived PNG"
    elif workflow_mode == "matting":
        profile = str(params.get("matting_profile") or "birefnet_hr").replace("_", " ")
        edge_mode = str(params.get("matting_edge_mode") or "high_resolution_edges").replace("_", " ")
        assistant_summary = f"Advanced RMBG matting used {profile} with {edge_mode} at {int(params.get('matting_process_res') or 0)}px; transparent PNG"
    else:
        if engine == "commercial_api":
            provider_label = str(route.get("provider_id") or params.get("resolved_model") or "commercial provider")
            assistant_summary = f"Background Removal applied with optional commercial provider {provider_label} after explicit upload consent; transparent PNG"
        else:
            engine_label = "Neo native rembg" if engine == "native_rembg" else ("ComfyUI-RMBG generic RMBG" if engine == "comfy_rmbg" else "Comfy BiRefNet")
            assistant_summary = f"Background Removal applied with {engine_label} · {model} ({preset}); transparent PNG"
            if params.get("fallback_used"):
                assistant_summary += f"; smart fallback used ({params.get('fallback_reason') or 'preferred engine unavailable'})"
    if params.get("save_mask"):
        assistant_summary += " and alpha-mask PNG were saved."
    else:
        assistant_summary += " was saved."
    return {
        "extension_id": EXTENSION_ID,
        "route": route,
        "params": params,
        "assets": assets,
        "payload_block": payload_block,
        "node_status": node_status,
        "compile_notes": compile_notes,
        "assistant_summary": assistant_summary,
        "replay_payload": {
            "enabled": True,
            "params": {**params, "commercial_upload_consent": False},
            "restore_policy": "revalidate_requested_engine_models_runtime_source_review_mask_sam_subjects_comfy_sam_asset_and_require_fresh_commercial_upload_consent",
        },
        "output_contract": {
            "foreground": "rgba_png",
            "mask": "grayscale_png" if params.get("save_mask") else "disabled",
            "verification": "verify_alpha_after_persistence",
            "workflow_mode": workflow_mode,
            "non_destructive": True,
        },
        "engine_resolution": {
            "requested_engine": params.get("engine") or "smart",
            "resolved_engine": engine,
            "resolved_model": model,
            "fallback_policy": params.get("fallback_policy") or "on_unavailable",
            "fallback_used": bool(params.get("fallback_used")),
            "fallback_reason": params.get("fallback_reason") or "",
            "native_provider": params.get("native_provider") or "AUTO",
            "commercial_profile_id": params.get("commercial_profile_id") or "",
            "commercial_provider_id": route.get("provider_id") if engine == "commercial_api" else "",
        },
        "sam_selection": {
            "enabled": workflow_mode == "interactive_sam",
            "requested_execution": params.get("sam_execution") or "auto",
            "resolved_execution": "comfy_impact" if engine == "comfy_sam" else ("native_onnx" if engine == "native_sam" else ""),
            "comfy_sam_model": params.get("sam_comfy_model") or "",
            "native_sam_variant": params.get("sam_model_variant") or "",
            "selected_subject_count": sum(1 for item in (params.get("sam_subjects") or []) if item.get("selected", True)),
            "subjects": params.get("sam_subjects") or [],
            "detector_model": params.get("sam_detector_model") or "",
            "detector_type": params.get("sam_detector_type") or "bbox",
            "comfy_sam_backend": engine == "comfy_sam",
            "comfy_birefnet_model": params.get("model") if engine == "comfy_sam" else "",
            "refinement_fallback_used": bool(params.get("sam_refine_fallback_used")),
            "refinement_fallback_reason": params.get("sam_refine_fallback_reason") or "",
            "mask_operation": params.get("sam_mask_operation") or "union",
        },
        "commercial_provider": {
            "enabled": engine == "commercial_api",
            "profile_id": params.get("commercial_profile_id") or "",
            "provider_id": route.get("provider_id") if engine == "commercial_api" else "",
            "external_upload": engine == "commercial_api",
            "per_run_consent_recorded": bool(params.get("commercial_upload_consent")) if engine == "commercial_api" else False,
            "fallback_allowed": False if engine == "commercial_api" else None,
            "provider_terms_apply": engine == "commercial_api",
            "credits": ((assets.get("commercial_runtime") or {}).get("credits") or {}) if isinstance(assets, dict) else {},
            "replay_requires_fresh_consent": engine == "commercial_api",
        },
        "mask_review": {
            "workflow_mode": workflow_mode,
            "manual_mask": bool(params.get("manual_mask")),
            "mask_source": params.get("mask_source") or "birefnet",
            "mask_expand": int(params.get("mask_expand") or 0),
            "mask_feather": int(params.get("mask_feather") or 0),
            "foreground_estimation": bool(params.get("foreground_estimation")),
            "preview_background": params.get("preview_background") or "checkerboard",
            "segmentation_reused": workflow_mode == "refine_mask",
        },
        "interactive_sam": {
            "enabled": workflow_mode == "interactive_sam",
            "model_variant": params.get("sam_model_variant") or "",
            "quantized": bool(params.get("sam_quantized")),
            "prompts": list(params.get("sam_prompts") or []),
            "prompt_count": len(params.get("sam_prompts") or []),
            "refine_mode": params.get("sam_refine_mode") or "sam_only",
            "refine_model": params.get("sam_refine_model") or "",
            "refine_fallback": bool(params.get("sam_refine_fallback")),
            "gate_expand": int(params.get("sam_gate_expand") or 0),
            "gate_feather": int(params.get("sam_gate_feather") or 0),
        },
        "segmentation_lab": {
            "enabled": workflow_mode == "segmentation_lab",
            "schema_id": "neo.image.background_removal.segmentation_lab.v1",
            "adapter": params.get("segmentation_adapter") or "",
            "node_class": params.get("segmentation_node_class") or "",
            "prompts": list(params.get("segmentation_lab_prompts") or []),
            "prompt_count": len(params.get("segmentation_lab_prompts") or []),
            "mask_operation": params.get("segmentation_mask_operation") or "union",
            "threshold": params.get("segmentation_threshold", 0.35),
        },
        "region_segmentation": {
            "enabled": workflow_mode == "region_segmentation",
            "schema_id": "neo.image.background_removal.region_segmentation.v1",
            "adapter": params.get("region_segmentation_adapter") or "",
            "node_class": params.get("region_segmentation_node_class") or "",
            "targets": list(params.get("region_segmentation_targets") or []),
            "target_count": len(params.get("region_segmentation_targets") or []),
            "mask_operation": params.get("region_segmentation_mask_operation") or "union",
        },
        "mask_utilities": {
            "enabled": workflow_mode == "mask_utility",
            "schema_id": "neo.image.background_removal.mask_utilities.v2",
            "operation": params.get("mask_utility_operation") or "",
            "node_class": params.get("mask_utility_node_class") or "",
            "mask_operation": params.get("mask_utility_mask_operation") or "union",
            "mask_count": len(params.get("mask_utility_mask_names") or []),
            "derived_image_operation": params.get("mask_utility_operation") in {"mask_overlay", "object_remove_lama", "image_mask_resize", "image_crop"},
            "handoff_contract": "neo.image.image_mask_preparation.v1" if params.get("mask_utility_operation") in {"image_mask_resize", "image_crop"} else "neo.image.preview_action.v1",
            "non_destructive": True,
        },
        "matting": {
            "enabled": workflow_mode == "matting",
            "schema_id": "neo.image.background_removal.matting.v1",
            "profile": params.get("matting_profile") or "",
            "node_class": params.get("matting_node_class") or "",
            "model": params.get("matting_model") or "",
            "edge_mode": params.get("matting_edge_mode") or "high_resolution_edges",
            "process_res": int(params.get("matting_process_res") or 0),
            "mask_refine": bool(params.get("matting_mask_refine")),
            "mask_supplied": bool(params.get("matting_mask_names")),
        },
    }
