from __future__ import annotations

from copy import deepcopy
from typing import Any

from neo_app.image.action_state import (
    ACTION_TRANSIENT_KEYS,
    MASK_FIELDS,
    OUTPAINT_FIELDS,
    PROVIDER_UPLOAD_CACHE_KEYS,
    SOURCE_FIELDS,
    clear_cross_provider_upload_caches,
)

SCHEMA_ID = "neo.image.clean_state_boundary.v25_9_5"
PHASE = "V25.9.5"
SOURCE_WORKFLOW_MODES = {"img2img", "image_to_image", "edit", "inpaint", "outpaint"}
TXT2IMG_MODES = {"generate", "txt2img", "text_to_image"}

PREVIEW_ACTION_KEYS = {
    "_neo_preview_action",
    "_neo_derived_action",
    "_neo_derived_action_validation",
    "_neo_derived_action_type",
    "_neo_source_output_id",
    "_neo_source_job_id",
    "_neo_parent_output_id",
    "_neo_preview_action_source",
    "_neo_preview_source_handoff",
}
SOURCE_IMAGE_KEYS = {
    "source_image",
    "source_image_path",
    "source_image_url",
    "source_image_name",
    "comfy_source_image_name",
    "source_image_uploaded_to_comfy",
    "forge_source_image_b64",
    "mask_image",
    "mask_image_path",
    "mask_image_url",
    "mask_image_name",
    "mask_image_preview_url",
    "comfy_mask_image_name",
    "forge_mask_image_b64",
    "comfy_outpaint_canvas_image_name",
    "comfy_outpaint_mask_image_name",
} | SOURCE_FIELDS | MASK_FIELDS | OUTPAINT_FIELDS | PROVIDER_UPLOAD_CACHE_KEYS
PREVIEW_ACTION_KEYS |= ACTION_TRANSIENT_KEYS | {
    "_neo_preview_reference_handoff",
    "_neo_action_state_cleanup",
}


def normalize_runtime_mode(mode: Any) -> str:
    value = str(mode or "txt2img").strip().lower().replace("-", "_")
    if value in {"generate", "text2img", "text_to_image"}:
        return "txt2img"
    if value == "image_to_image":
        return "img2img"
    return value or "txt2img"


def is_source_workflow_mode(mode: Any) -> bool:
    return normalize_runtime_mode(mode) in SOURCE_WORKFLOW_MODES


def has_source_image(params: dict[str, Any] | None) -> bool:
    raw = params if isinstance(params, dict) else {}
    for key in ("source_image", "source_image_path", "source_image_url", "source_image_name", "comfy_source_image_name"):
        if str(raw.get(key) or "").strip():
            return True
    return False


def sanitize_image_params_for_state_boundary(params: dict[str, Any] | None, mode: Any = "txt2img") -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove stale img2img/preview state from clean txt2img submissions.

    Scene Director can keep region/edit metadata in the saved draft, but a clean
    Generate/txt2img run must not submit preview-action or source-image fields.
    Those fields change routing semantics and can make txt2img behave like an old
    img2img/replay action.
    """

    clean = deepcopy(params if isinstance(params, dict) else {})
    runtime_mode = normalize_runtime_mode(mode)
    source_workflow_active = runtime_mode in SOURCE_WORKFLOW_MODES
    cleared: list[str] = []
    warnings: list[str] = []
    preview_action_present = any(key in clean and clean.get(key) not in (None, "", {}) for key in PREVIEW_ACTION_KEYS)

    def clear(key: str) -> None:
        if key in clean:
            clean.pop(key, None)
            cleared.append(key)

    if not source_workflow_active:
        for key in sorted(PREVIEW_ACTION_KEYS | SOURCE_IMAGE_KEYS):
            clear(key)
        if preview_action_present or cleared:
            warnings.append("clean_txt2img_preview_img2img_state_cleared")
        if clean.get("save_mode_override") == "append_derived":
            clear("save_mode_override")
    elif str(clean.get("_neo_derived_action_type") or "").lower() in {"img2img", "image_to_image"} and not has_source_image(clean):
        warnings.append("source_workflow_preview_action_missing_source_image")

    existing = clean.get("_neo_route_validation_warnings") if isinstance(clean.get("_neo_route_validation_warnings"), list) else []
    if warnings:
        clean["_neo_route_validation_warnings"] = sorted({*(str(item) for item in existing), *warnings})

    report = {
        "schema": SCHEMA_ID,
        "phase": PHASE,
        "runtime_mode": runtime_mode,
        "source_workflow_active": source_workflow_active,
        "preview_action_present": preview_action_present,
        "cleared_fields": cleared,
        "warning_codes": warnings,
        "status": "cleaned" if cleared else "clean",
        "policy": "Clean txt2img/generate submissions cannot carry preview-action, source-image, or derived img2img state.",
    }
    if cleared or warnings:
        clean["_neo_clean_state_boundary"] = report
    return clean, report


def sanitize_image_action_state_for_provider(
    params: dict[str, Any] | None,
    *,
    mode: Any = "txt2img",
    provider_id: str = "",
    profile_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply provider ownership cleanup without deleting the active action contract.

    Source/Reference/Finish validators still own their active contracts.  This
    boundary removes only stale provider upload aliases and then applies the
    existing clean-txt2img mode hygiene.
    """

    provider_clean, provider_report = clear_cross_provider_upload_caches(
        params, provider_id=provider_id, profile_id=profile_id
    )
    active_contract = any(
        isinstance(provider_clean.get(key), dict) and provider_clean.get(key)
        for key in ("_neo_derived_action", "_neo_preview_action", "_preview_action_source", "_neo_preview_source_handoff")
    )
    if active_contract:
        mode_clean = provider_clean
        mode_report = {
            "schema": SCHEMA_ID,
            "phase": PHASE,
            "runtime_mode": normalize_runtime_mode(mode),
            "status": "preserved_active_action_contract",
            "cleared_fields": [],
            "warning_codes": [],
        }
    else:
        mode_clean, mode_report = sanitize_image_params_for_state_boundary(provider_clean, mode)
    report = {
        "schema": "neo.image.action_state_provider_boundary.v1",
        "provider_id": str(provider_id or "").strip().casefold(),
        "profile_id": str(profile_id or "").strip(),
        "runtime_mode": normalize_runtime_mode(mode),
        "provider_cleanup": provider_report,
        "mode_cleanup": mode_report,
        "status": "cleaned" if provider_report.get("cleared_fields") or mode_report.get("cleared_fields") else "clean",
    }
    mode_clean["_neo_action_state_cleanup"] = report
    return mode_clean, report
