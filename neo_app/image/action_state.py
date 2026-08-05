"""Image action-state lifecycle and replay sanitization.

The Image surface keeps provider-neutral user settings in the draft, but Source,
Reference, and Finish actions also create short-lived handoff contracts and
provider upload aliases.  This module defines the backend cleanup boundary so
those temporary values cannot leak into replay payloads or a different provider.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

ACTION_STATE_SCHEMA = "neo.image.action_state_cleanup.v1"
REPLAY_SANITIZE_SCHEMA = "neo.image.replay_state_sanitization.v1"

# Contracts and execution markers exist only for one action lifecycle.  They are
# useful in persisted output metadata/lineage, but must not be restored as live
# draft state by replay.
ACTION_TRANSIENT_KEYS = {
    "_neo_derived_action",
    "_neo_derived_action_validation",
    "_neo_derived_action_type",
    "_neo_preview_action",
    "_neo_preview_action_source",
    "_neo_preview_source_handoff",
    "_neo_preview_reference_handoff",
    "_neo_source_output_id",
    "_neo_source_job_id",
    "_neo_parent_output_id",
    "_neo_save_lane",
    "_preview_action",
    "_preview_action_source",
    "_preview_action_finish_pass",
    "_preview_action_force_workflow_mode",
    "_preview_action_run_label",
    "_post_output_bridge",
    "save_mode_override",
}

# Provider-side upload/cache names are never portable replay state.  The selected
# provider must rebuild them from canonical Neo-owned assets.
COMFY_UPLOAD_CACHE_KEYS = {
    "comfy_source_image_name",
    "source_image_uploaded_to_comfy",
    "comfy_mask_image_name",
    "comfy_outpaint_canvas_image_name",
    "comfy_outpaint_mask_image_name",
    "comfy_reference_image_name",
    "comfy_control_image_name",
}
FORGE_UPLOAD_CACHE_KEYS = {
    "forge_source_image_b64",
    "forge_mask_image_b64",
    "forge_reference_image_b64",
    "forge_control_image_b64",
}
PROVIDER_UPLOAD_CACHE_KEYS = COMFY_UPLOAD_CACHE_KEYS | FORGE_UPLOAD_CACHE_KEYS

SOURCE_FIELDS = {
    "source_image",
    "source_image_path",
    "source_image_url",
    "source_image_name",
    "source_image_width",
    "source_image_height",
}
MASK_FIELDS = {
    "mask_image",
    "mask_image_path",
    "mask_image_url",
    "mask_image_name",
    "mask_image_preview_url",
    "inpaint_mask",
}
OUTPAINT_FIELDS = {
    "outpaint_canvas_image",
    "outpaint_canvas_image_path",
    "outpaint_canvas_image_url",
    "outpaint_canvas_image_name",
    "outpaint_mask_image",
    "outpaint_mask_image_path",
    "outpaint_mask_image_url",
    "outpaint_mask_image_name",
    "outpaint_left",
    "outpaint_top",
    "outpaint_right",
    "outpaint_bottom",
}

EXTENSION_HANDOFF_KEYS = {
    "preview_reference_handoff",
    "preview_action_source",
    "staged_preview_source",
    "preview_derived_action",
    "_replay_source_handoff",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_mode(value: Any) -> str:
    mode = str(value or "generate").strip().casefold().replace("-", "_")
    if mode in {"generate", "text2img", "text_to_image"}:
        return "txt2img"
    if mode in {"image_to_image", "image2image"}:
        return "img2img"
    return mode or "txt2img"


def clear_cross_provider_upload_caches(
    params: dict[str, Any] | None,
    *,
    provider_id: str = "",
    profile_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove upload aliases that cannot belong to the selected provider/profile."""

    clean = deepcopy(params if isinstance(params, dict) else {})
    provider = str(provider_id or "").strip().casefold()
    profile = str(profile_id or "").strip()
    owner = _as_dict(clean.get("_neo_provider_state_owner"))
    owner_provider = str(owner.get("provider_id") or "").strip().casefold()
    owner_profile = str(owner.get("profile_id") or "").strip()
    owner_changed = bool(
        (owner_provider and provider and owner_provider != provider)
        or (owner_profile and profile and owner_profile != profile)
    )

    if owner_changed or provider not in {"comfyui", "comfyui_portable", "forge"}:
        candidates = PROVIDER_UPLOAD_CACHE_KEYS
    elif provider == "forge":
        candidates = COMFY_UPLOAD_CACHE_KEYS
    else:
        candidates = FORGE_UPLOAD_CACHE_KEYS

    cleared: list[str] = []
    for key in sorted(candidates):
        if key in clean:
            clean.pop(key, None)
            cleared.append(key)

    if provider or profile:
        clean["_neo_provider_state_owner"] = {
            "schema": "neo.image.provider_state_owner.v1",
            "provider_id": provider,
            "profile_id": profile,
        }

    return clean, {
        "schema": ACTION_STATE_SCHEMA,
        "provider_id": provider,
        "profile_id": profile,
        "previous_provider_id": owner_provider,
        "previous_profile_id": owner_profile,
        "provider_changed": owner_changed,
        "cleared_fields": cleared,
        "status": "cleaned" if cleared else "clean",
    }


def sanitize_replay_params(
    params: dict[str, Any] | None,
    *,
    mode: str = "generate",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return provider-neutral params suitable for a replay draft.

    The saved output may retain the original action contracts for provenance,
    but replay restores only canonical user settings and source assets relevant
    to the recorded workflow mode.
    """

    clean = deepcopy(params if isinstance(params, dict) else {})
    runtime_mode = normalize_mode(mode)
    cleared: list[str] = []

    def drop(keys: set[str]) -> None:
        for key in sorted(keys):
            if key in clean:
                clean.pop(key, None)
                cleared.append(key)

    drop(ACTION_TRANSIENT_KEYS | PROVIDER_UPLOAD_CACHE_KEYS)
    clean.pop("_neo_provider_state_owner", None)
    if runtime_mode not in {"img2img", "inpaint", "outpaint", "edit"}:
        drop(SOURCE_FIELDS)
    if runtime_mode != "inpaint":
        drop(MASK_FIELDS)
    if runtime_mode != "outpaint":
        drop(OUTPAINT_FIELDS)
    if clean.get("output_policy") == "append_derived":
        clean.pop("output_policy", None)
        cleared.append("output_policy")

    # LanPaint replay restores source/mask from Neo-owned input_assets. Names in
    # compiled params may be disposable Comfy upload aliases and must not become
    # portable replay authority.
    lanpaint_replay = clean.get("lanpaint_replay") if isinstance(clean.get("lanpaint_replay"), dict) else {}
    if runtime_mode == "inpaint" and lanpaint_replay.get("schema_id") == "neo.image.lanpaint_replay.v1":
        for key in ("source_image_name", "mask_image_name"):
            if key in clean:
                clean.pop(key, None)
                cleared.append(key)

    # Reports and browser-only runtime snapshots are not replay inputs.
    for key in list(clean):
        if key.startswith("_neo_") and key not in {
            "_neo_replay_context",
            "_neo_latent_capture",
            "_neo_latent_artifacts",
            "_neo_run_timing",
        }:
            clean.pop(key, None)
            cleared.append(key)

    report = {
        "schema": REPLAY_SANITIZE_SCHEMA,
        "runtime_mode": runtime_mode,
        "cleared_fields": sorted(set(cleared)),
        "provider_neutral": True,
        "temporary_action_state_restored": False,
        "status": "cleaned" if cleared else "clean",
    }
    return clean, report


def _sanitize_extension_block(value: Any, *, extension_id: str = "") -> tuple[Any, list[str], bool]:
    if isinstance(value, list):
        cleaned: list[Any] = []
        removed: list[str] = []
        had_handoff = False
        for item in value:
            clean_item, item_removed, item_handoff = _sanitize_extension_block(item, extension_id=extension_id)
            cleaned.append(clean_item)
            removed.extend(item_removed)
            had_handoff = had_handoff or item_handoff
        return cleaned, removed, had_handoff
    if not isinstance(value, dict):
        return deepcopy(value), [], False

    clean: dict[str, Any] = {}
    removed: list[str] = []
    had_handoff = False
    for key, item in value.items():
        key_text = str(key)
        if key_text in EXTENSION_HANDOFF_KEYS:
            removed.append(key_text)
            had_handoff = True
            continue
        if key_text in PROVIDER_UPLOAD_CACHE_KEYS or key_text.startswith("comfy_uploaded_") or key_text.startswith("forge_uploaded_"):
            removed.append(key_text)
            continue
        nested, nested_removed, nested_handoff = _sanitize_extension_block(item, extension_id=extension_id)
        clean[key_text] = nested
        removed.extend(nested_removed)
        had_handoff = had_handoff or nested_handoff

    params = clean.get("params") if isinstance(clean.get("params"), dict) else None
    metadata = clean.get("metadata") if isinstance(clean.get("metadata"), dict) else None
    if params is not None:
        if params.get("source_mode") == "preview_action_selected_output":
            params["source_mode"] = "selected_result_or_upload"
            removed.append("params.source_mode")
            had_handoff = True
        for key in ("detailer_output_pass", "upscale_lab_source_only", "preserve_prompt_context", "preserve_reference_context"):
            if key in params:
                params.pop(key, None)
                removed.append(f"params.{key}")
        clean["params"] = params
    if metadata is not None and had_handoff:
        metadata.update({
            "revalidation_required": True,
            "restore_state": "restored_disabled_pending_selected_provider_revalidation",
            "temporary_handoff_restored": False,
        })
        clean["metadata"] = metadata
    if had_handoff and "enabled" in clean:
        clean["enabled"] = False
    return clean, removed, had_handoff


def sanitize_replay_extensions(extensions: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strip provider/action handoffs while retaining canonical extension settings."""

    source = deepcopy(extensions if isinstance(extensions, dict) else {})
    cleaned: dict[str, Any] = {}
    removed: list[str] = []
    handoff_extensions: list[str] = []
    for key, value in source.items():
        if key in {"payloads", "replay_payloads", "extensions"} and isinstance(value, dict):
            bucket: dict[str, Any] = {}
            for extension_id, block in value.items():
                clean_block, block_removed, had_handoff = _sanitize_extension_block(block, extension_id=str(extension_id))
                bucket[str(extension_id)] = clean_block
                removed.extend(f"{key}.{extension_id}.{item}" for item in block_removed)
                if had_handoff:
                    handoff_extensions.append(str(extension_id))
            cleaned[key] = bucket
        else:
            clean_value, value_removed, had_handoff = _sanitize_extension_block(value, extension_id=str(key))
            cleaned[str(key)] = clean_value
            removed.extend(f"{key}.{item}" for item in value_removed)
            if had_handoff:
                handoff_extensions.append(str(key))

    report = {
        "schema": REPLAY_SANITIZE_SCHEMA,
        "provider_neutral": True,
        "temporary_handoff_restored": False,
        "disabled_pending_revalidation": sorted(set(handoff_extensions)),
        "cleared_fields": sorted(set(removed)),
        "status": "cleaned" if removed else "clean",
    }
    return cleaned, report
