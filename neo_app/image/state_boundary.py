from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_ID = "neo.image.clean_state_boundary.v25_9_5"
PHASE = "V25.9.5"
SOURCE_WORKFLOW_MODES = {"img2img", "image_to_image", "edit", "inpaint", "outpaint"}
TXT2IMG_MODES = {"generate", "txt2img", "text_to_image"}

PREVIEW_ACTION_KEYS = {
    "_neo_preview_action",
    "_neo_derived_action_type",
    "_neo_source_output_id",
    "_neo_source_job_id",
    "_neo_parent_output_id",
    "_neo_preview_action_source",
}
SOURCE_IMAGE_KEYS = {
    "source_image",
    "source_image_path",
    "source_image_url",
    "source_image_name",
    "comfy_source_image_name",
    "source_image_uploaded_to_comfy",
    "mask_image",
    "mask_image_path",
    "mask_image_url",
    "mask_image_name",
    "mask_image_preview_url",
    "comfy_mask_image_name",
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
