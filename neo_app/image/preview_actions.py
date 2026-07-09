"""Preview action parity contract for the Image surface.

Phase C is intentionally a mapping/contract layer. It does not render buttons,
mutate UI state, queue Comfy jobs, or stage files. Later phases consume this
registry to build the V2 preview/result action toolbar.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

ACTION_GROUPS: List[Dict[str, str]] = [
    {"id": "source", "label": "Source", "description": "Send an output into a core image source mode."},
    {"id": "reference", "label": "Reference", "description": "Use an output as a conditioning/reference image."},
    {"id": "finish", "label": "Finish", "description": "Stage an output for a post-process/finish pass."},
]

SOURCE_PRIORITY: List[str] = [
    "active_inspector_media_file",
    "active_saved_output_file",
    "active_saved_result_active_file",
    "active_live_preview_result",
    "visible_live_preview_url_fallback",
]

SOURCE_CONTEXT_SCHEMA: Dict[str, str] = {
    "source_type": "generated_output",
    "source_scope": "live_preview | saved_result | inspector_file",
    "result_id": "",
    "job_id": "",
    "output_id": "",
    "file_id": "",
    "filename": "",
    "saved_filename": "",
    "path": "",
    "url": "",
    "subfolder": "",
    "file_type": "output",
    "metadata": "{}",
    "parent_output_id": "",
    "parent_job_id": "",
    "created_at": "",
}

# V1 toolbar parity mapped into V2 destinations. Normal clicks stage/open; they
# do not auto-run expensive finish passes in Phase C.
PREVIEW_ACTIONS: List[Dict[str, Any]] = [
    {
        "id": "core.img2img",
        "v1_button_id": "btn-generation-preview-img2img",
        "v1_icon": "🖼️",
        "icon": "🖼️",
        "group": "source",
        "label": "Img2Img",
        "tooltip": "Send this image to Img2Img",
        "type": "core",
        "requires_extension": None,
        "target_workspace": "generations",
        "target_mode": "img2img",
        "handler": "previewActionSendToSourceMode",
        "handler_args": ["img2img"],
        "auto_run_default": False,
        "preserve_prompt_context": "optional",
        "preserve_reference_context": "optional",
        "v1_parity": True,
    },
    {
        "id": "core.inpaint",
        "v1_button_id": "btn-generation-preview-inpaint",
        "v1_icon": "🩹",
        "icon": "🩹",
        "group": "source",
        "label": "Inpaint",
        "tooltip": "Send this image to Inpaint",
        "type": "core",
        "requires_extension": None,
        "target_workspace": "generations",
        "target_mode": "inpaint",
        "handler": "previewActionSendToSourceMode",
        "handler_args": ["inpaint"],
        "clears": ["mask"],
        "auto_run_default": False,
        "preserve_prompt_context": "optional",
        "preserve_reference_context": "optional",
        "v1_parity": True,
    },
    {
        "id": "core.outpaint",
        "v1_button_id": "btn-generation-preview-outpaint",
        "v1_icon": "↔️",
        "icon": "↔️",
        "group": "source",
        "label": "Outpaint",
        "tooltip": "Send this image to Outpaint",
        "type": "core",
        "requires_extension": None,
        "target_workspace": "generations",
        "target_mode": "outpaint",
        "handler": "previewActionSendToSourceMode",
        "handler_args": ["outpaint"],
        "clears": ["mask"],
        "auto_run_default": False,
        "preserve_prompt_context": "optional",
        "preserve_reference_context": "optional",
        "v1_parity": True,
    },
    {
        "id": "extension.controlnet",
        "v1_button_id": "btn-generation-preview-controlnet",
        "v1_icon": "🎯",
        "icon": "🎯",
        "group": "reference",
        "label": "ControlNet",
        "tooltip": "Send this image to ControlNet reference",
        "type": "extension",
        "requires_extension": "image.controlnet",
        "target_workspace": "generations",
        "target_panel": "image.controlnet",
        "handler": "previewActionSendToControlNet",
        "stage_policy": "first_empty_unit_no_overwrite",
        "auto_run_default": False,
        "preserve_prompt_context": False,
        "preserve_reference_context": "source_becomes_reference",
        "v1_parity": True,
    },
    {
        "id": "extension.ip_adapter",
        "v1_button_id": "btn-generation-preview-ipadapter",
        "v1_icon": "👤",
        "icon": "👤",
        "group": "reference",
        "label": "IPAdapter",
        "tooltip": "Send this image to IPAdapter reference",
        "type": "extension",
        "requires_extension": "image.ip_adapter",
        "target_workspace": "generations",
        "target_panel": "image.ip_adapter",
        "handler": "previewActionSendToIpAdapter",
        "stage_policy": "first_empty_reference_slot_no_overwrite",
        "auto_run_default": False,
        "preserve_prompt_context": False,
        "preserve_reference_context": "source_becomes_reference",
        "v1_parity": True,
    },
    {
        "id": "extension.image_upscale",
        "v1_button_id": None,
        "v1_icon": None,
        "icon": "⬆️",
        "group": "finish",
        "label": "Image Upscale",
        "tooltip": "Upscale this image",
        "type": "extension",
        "requires_extension": "image.image_upscale",
        "target_workspace": "finish",
        "target_panel": "image.image_upscale",
        "handler": "previewActionStageImageUpscale",
        "auto_run_default": False,
        "preserve_prompt_context": False,
        "preserve_reference_context": False,
        "v1_parity": False,
        "v2_improvement": True,
    },
    {
        "id": "extension.high_res_lab",
        "v1_button_id": "btn-generation-preview-hires",
        "v1_icon": "✨",
        "icon": "✨",
        "group": "finish",
        "label": "High-Res Lab",
        "tooltip": "Refine with High-Res Lab",
        "type": "extension",
        "requires_extension": "image.high_res_lab",
        "target_workspace": "finish",
        "target_panel": "image.high_res_lab",
        "handler": "previewActionStageHighResLab",
        "auto_run_default": False,
        "v1_auto_queued": True,
        "preserve_prompt_context": True,
        "preserve_reference_context": "optional",
        "v1_parity": True,
    },
    {
        "id": "extension.adetailer",
        "v1_button_id": "btn-generation-preview-detailer",
        "v1_icon": "🩹+",
        "icon": "🩹+",
        "group": "finish",
        "label": "ADetailer",
        "tooltip": "Repair faces/details with ADetailer",
        "type": "extension",
        "requires_extension": "image.adetailer",
        "target_workspace": "finish",
        "target_panel": "image.adetailer",
        "handler": "previewActionStageADetailer",
        "auto_run_default": False,
        "v1_auto_queued": True,
        "preserve_prompt_context": True,
        "preserve_reference_context": "optional",
        "v1_parity": True,
    },
    {
        "id": "extension.identity_rescue",
        "v1_button_id": "btn-generation-preview-identity",
        "v1_icon": "🧬",
        "icon": "🧬",
        "group": "finish",
        "label": "Identity Rescue",
        "tooltip": "Identity Rescue / FaceID",
        "type": "extension",
        "requires_extension": "image.ip_adapter",
        "requires_capability": "face_id",
        "target_workspace": "finish",
        "target_panel": "image.ip_adapter",
        "handler": "previewActionStageIdentityRescue",
        "auto_run_default": False,
        "v1_auto_queued": True,
        "preserve_prompt_context": True,
        "preserve_reference_context": True,
        "v1_parity": True,
    },
]

ALLOWED_ROUTE_STATES = {"available", "experimental_available"}


def get_preview_action_groups() -> List[Dict[str, str]]:
    """Return action group metadata in toolbar display order."""
    return deepcopy(ACTION_GROUPS)


def get_preview_action_registry() -> List[Dict[str, Any]]:
    """Return the Phase C V1→V2 preview action registry contract."""
    return deepcopy(PREVIEW_ACTIONS)


def get_preview_action(action_id: str) -> Optional[Dict[str, Any]]:
    """Return one action contract by ID."""
    for action in PREVIEW_ACTIONS:
        if action["id"] == action_id:
            return deepcopy(action)
    return None


def preview_action_ids_by_group() -> Dict[str, List[str]]:
    """Return action IDs grouped in toolbar display order."""
    grouped: Dict[str, List[str]] = {group["id"]: [] for group in ACTION_GROUPS}
    for action in PREVIEW_ACTIONS:
        grouped.setdefault(action["group"], []).append(action["id"])
    return grouped


def preview_action_metadata_contract(action_id: str, source_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build the normalized metadata shell for a future preview action handoff."""
    action = get_preview_action(action_id)
    if not action:
        raise KeyError(f"Unknown preview action: {action_id}")
    source_context = dict(source_context or {})
    return {
        "preview_action": {
            "schema_version": 1,
            "action_id": action["id"],
            "action_type": action["id"].split(".", 1)[-1],
            "source_output_id": source_context.get("output_id", ""),
            "source_file_id": source_context.get("file_id", ""),
            "source_job_id": source_context.get("job_id", ""),
            "source_filename": source_context.get("filename") or source_context.get("saved_filename", ""),
            "source_url": source_context.get("url", ""),
            "parent_output_id": source_context.get("parent_output_id", ""),
            "parent_job_id": source_context.get("parent_job_id", ""),
            "preserve_prompt_context": action.get("preserve_prompt_context", False),
            "preserve_reference_context": action.get("preserve_reference_context", False),
            "created_at": source_context.get("created_at", ""),
        }
    }


def is_preview_action_visible(
    action: Dict[str, Any],
    *,
    extension_states: Dict[str, Dict[str, Any]] | None = None,
    route_states: Dict[str, str] | None = None,
    expert_mode: bool = False,
) -> bool:
    """Return whether an action should be visible under the Phase C policy.

    Core actions are visible for valid image sources. Extension actions are
    visible in normal mode only when their extension is enabled and route state
    is available/experimental. In expert mode, extension actions may remain
    visible for diagnostics even when disabled/gated.
    """
    if action.get("type") == "core":
        return True
    extension_id = action.get("requires_extension")
    if not extension_id:
        return True
    extension_states = extension_states or {}
    route_states = route_states or {}
    record = extension_states.get(extension_id, {})
    route_state = route_states.get(extension_id, record.get("route_state", "available"))
    enabled = bool(record.get("enabled", False))
    available = route_state in ALLOWED_ROUTE_STATES
    if expert_mode:
        return True
    return enabled and available


def validate_preview_action_mapping() -> List[str]:
    """Return mapping contract errors. Empty list means Phase C is valid."""
    errors: List[str] = []
    seen_ids = set()
    group_ids = {group["id"] for group in ACTION_GROUPS}
    for action in PREVIEW_ACTIONS:
        action_id = action.get("id")
        if not action_id:
            errors.append("Action missing id")
            continue
        if action_id in seen_ids:
            errors.append(f"Duplicate action id: {action_id}")
        seen_ids.add(action_id)
        if action.get("group") not in group_ids:
            errors.append(f"{action_id} uses unknown group: {action.get('group')}")
        if not action.get("icon"):
            errors.append(f"{action_id} missing icon")
        if not action.get("tooltip"):
            errors.append(f"{action_id} missing tooltip")
        if action.get("auto_run_default") is not False:
            errors.append(f"{action_id} must not auto-run by default")
        if action.get("type") == "extension" and not action.get("requires_extension"):
            errors.append(f"{action_id} missing requires_extension")
    return errors

SOURCE_CONTEXT_DEFAULTS: Dict[str, Any] = {
    "source_type": "generated_output",
    "source_scope": "",
    "result_id": "",
    "job_id": "",
    "output_id": "",
    "file_id": "",
    "filename": "",
    "saved_filename": "",
    "path": "",
    "url": "",
    "subfolder": "",
    "file_type": "output",
    "metadata": {},
    "parent_output_id": "",
    "parent_job_id": "",
    "created_at": "",
    "priority_key": "",
    "source_rank": -1,
    "media_kind": "output",
    "is_valid": False,
    "missing_reason": "No preview action source resolved.",
}

IMAGE_URL_KEYS = ("url", "view_url", "preview_url", "image_url", "download_url", "data_url")
FILENAME_KEYS = ("filename", "saved_filename", "name", "label")


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_from_keys(record: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _first_text(record.get(key))
        if text:
            return text
    return ""


def _metadata_files(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    outputs = _as_dict(metadata.get("outputs"))
    return [item for item in _as_list(outputs.get("files")) if isinstance(item, dict)]


def _metadata_active_file_id(metadata: Dict[str, Any]) -> str:
    outputs = _as_dict(metadata.get("outputs"))
    return _first_text(outputs.get("active_file"), outputs.get("active_file_id"), metadata.get("active_file"))


def _find_file_by_id(files: List[Dict[str, Any]], file_id: str) -> Optional[Dict[str, Any]]:
    if not file_id:
        return None
    for item in files:
        if _first_text(item.get("file_id"), item.get("id")) == file_id:
            return item
    return None


def _strip_output_prefix(media_id: str) -> str:
    media_id = _first_text(media_id)
    return media_id[len("output:"):] if media_id.startswith("output:") else ""


def build_preview_action_source_context(
    record: Dict[str, Any] | None = None,
    *,
    source_scope: str,
    priority_key: str,
    source_rank: int,
    metadata: Dict[str, Any] | None = None,
    result_summary: Dict[str, Any] | None = None,
    media_kind: str = "output",
) -> Dict[str, Any]:
    """Normalize one V2 output/media record into the shared preview action source contract.

    Phase D only creates a safe source identity. It does not fetch files, render
    buttons, stage extension inputs, or queue jobs. URL/path resolution remains
    metadata-first so later UI code does not scrape random preview DOM as the
    primary source.
    """
    record = _as_dict(record)
    metadata = _as_dict(metadata)
    result_summary = _as_dict(result_summary)
    context = deepcopy(SOURCE_CONTEXT_DEFAULTS)
    url = _first_from_keys(record, IMAGE_URL_KEYS)
    filename = _first_from_keys(record, FILENAME_KEYS)
    file_id = _first_text(record.get("file_id"), record.get("id"))
    result_id = _first_text(
        record.get("result_id"),
        metadata.get("result_id"),
        result_summary.get("result_id"),
    )
    job_id = _first_text(
        record.get("job_id"),
        metadata.get("job_id"),
        result_summary.get("job_id"),
        metadata.get("source_job_id"),
    )
    output_id = _first_text(record.get("output_id"), record.get("output_key"), file_id)
    created_at = _first_text(record.get("created_at"), metadata.get("created_at"), result_summary.get("created_at"))
    context.update(
        {
            "source_type": "generated_output" if media_kind == "output" else "inspector_media",
            "source_scope": source_scope,
            "result_id": result_id,
            "job_id": job_id,
            "output_id": output_id,
            "file_id": file_id,
            "filename": filename,
            "saved_filename": _first_text(record.get("saved_filename"), filename),
            "path": _first_text(record.get("path"), record.get("saved_path"), record.get("absolute_path")),
            "url": url,
            "subfolder": _first_text(record.get("subfolder")),
            "file_type": _first_text(record.get("file_type"), record.get("type"), "output"),
            "metadata": deepcopy(metadata),
            "parent_output_id": _first_text(record.get("parent_output_id"), metadata.get("parent_output_id")),
            "parent_job_id": _first_text(record.get("parent_job_id"), metadata.get("parent_job_id")),
            "created_at": created_at,
            "priority_key": priority_key,
            "source_rank": source_rank,
            "media_kind": media_kind,
        }
    )
    if url or context["path"]:
        context["is_valid"] = True
        context["missing_reason"] = ""
    else:
        context["missing_reason"] = "Source has no URL or saved path."
    return context


def invalid_preview_action_source(reason: str = "No preview action source resolved.") -> Dict[str, Any]:
    """Return a complete invalid source contract with a user-facing reason."""
    context = deepcopy(SOURCE_CONTEXT_DEFAULTS)
    context["missing_reason"] = reason
    return context


def is_valid_preview_action_source(source_context: Dict[str, Any] | None) -> bool:
    """Return true when a normalized source context can be used by actions."""
    context = _as_dict(source_context)
    return bool(context.get("is_valid") and (_first_text(context.get("url")) or _first_text(context.get("path"))))


def _resolve_active_inspector_media_file(state_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    metadata = _as_dict(state_snapshot.get("activeSavedResultMetadata")) or _as_dict(state_snapshot.get("active_saved_result_metadata"))
    files = _metadata_files(metadata)
    media_id = _first_text(state_snapshot.get("activeSavedInspectorMediaId"), state_snapshot.get("active_saved_inspector_media_id"))
    file_id = _strip_output_prefix(media_id)
    if file_id:
        return _find_file_by_id(files, file_id)
    return None


def _resolve_active_saved_output_file(state_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    metadata = _as_dict(state_snapshot.get("activeSavedResultMetadata")) or _as_dict(state_snapshot.get("active_saved_result_metadata"))
    files = _metadata_files(metadata)
    active_id = _first_text(
        state_snapshot.get("activeSavedOutputFileId"),
        state_snapshot.get("active_saved_output_file_id"),
        _metadata_active_file_id(metadata),
    )
    return _find_file_by_id(files, active_id) or (files[0] if files else None)


def _resolve_active_saved_result_active_file(state_snapshot: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    metadata = _as_dict(state_snapshot.get("activeSavedResultMetadata")) or _as_dict(state_snapshot.get("active_saved_result_metadata"))
    outputs = _as_dict(metadata.get("outputs"))
    active = _as_dict(outputs.get("active_file"))
    if active:
        return active, metadata
    summaries = _as_list(state_snapshot.get("imageSavedResults")) or _as_list(state_snapshot.get("image_saved_results"))
    index = state_snapshot.get("activeSavedResultIndex", state_snapshot.get("active_saved_result_index", 0))
    try:
        index = int(index)
    except Exception:
        index = 0
    if 0 <= index < len(summaries) and isinstance(summaries[index], dict):
        summary = summaries[index]
        return _as_dict(summary.get("active_file")), summary
    return None, metadata


def _resolve_active_live_preview_result(state_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    results = _as_list(state_snapshot.get("imageResults")) or _as_list(state_snapshot.get("image_results"))
    index = state_snapshot.get("activeResultIndex", state_snapshot.get("active_result_index", 0))
    try:
        index = int(index)
    except Exception:
        index = 0
    if 0 <= index < len(results) and isinstance(results[index], dict):
        return results[index]
    return None


def _resolve_visible_live_preview_url_fallback(state_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = _first_text(
        state_snapshot.get("visibleLivePreviewUrl"),
        state_snapshot.get("visible_live_preview_url"),
        state_snapshot.get("imageLivePreviewUrl"),
        state_snapshot.get("image_live_preview_url"),
    )
    if not url:
        return None
    return {
        "url": url,
        "filename": _first_text(
            state_snapshot.get("visibleLivePreviewLabel"),
            state_snapshot.get("imageLivePreviewLabel"),
            "live-preview.png",
        ),
    }


def resolve_preview_action_source(state_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Resolve the active image source using the Phase C priority order.

    The input is a plain dict snapshot of V2 UI/output state. This function is
    intentionally framework-free so both JS and backend tests can mirror the
    same contract without importing browser state.
    """
    state_snapshot = _as_dict(state_snapshot)
    metadata = _as_dict(state_snapshot.get("activeSavedResultMetadata")) or _as_dict(state_snapshot.get("active_saved_result_metadata"))
    summary = {}
    saved_results = _as_list(state_snapshot.get("imageSavedResults")) or _as_list(state_snapshot.get("image_saved_results"))
    try:
        saved_index = int(state_snapshot.get("activeSavedResultIndex", state_snapshot.get("active_saved_result_index", 0)))
    except Exception:
        saved_index = 0
    if 0 <= saved_index < len(saved_results) and isinstance(saved_results[saved_index], dict):
        summary = saved_results[saved_index]
    explicit_sources = _as_dict(state_snapshot.get("preview_action_sources"))

    for rank, priority_key in enumerate(SOURCE_PRIORITY):
        record: Optional[Dict[str, Any]] = None
        scope = ""
        media_kind = "output"
        local_metadata = metadata
        local_summary = summary
        if priority_key in explicit_sources:
            record = _as_dict(explicit_sources.get(priority_key))
            scope = record.get("source_scope") or priority_key
            media_kind = record.get("media_kind") or "output"
        elif priority_key == "active_inspector_media_file":
            record = _resolve_active_inspector_media_file(state_snapshot)
            scope = "inspector_file"
        elif priority_key == "active_saved_output_file":
            record = _resolve_active_saved_output_file(state_snapshot)
            scope = "saved_result"
        elif priority_key == "active_saved_result_active_file":
            record, result_or_metadata = _resolve_active_saved_result_active_file(state_snapshot)
            scope = "saved_result"
            if result_or_metadata is not metadata:
                local_summary = result_or_metadata
        elif priority_key == "active_live_preview_result":
            record = _resolve_active_live_preview_result(state_snapshot)
            scope = "live_preview"
            local_metadata = _as_dict(record.get("metadata")) if isinstance(record, dict) else {}
            local_summary = {}
        elif priority_key == "visible_live_preview_url_fallback":
            record = _resolve_visible_live_preview_url_fallback(state_snapshot)
            scope = "live_preview"
            local_metadata = {}
            local_summary = {}
        if not record:
            continue
        context = build_preview_action_source_context(
            record,
            source_scope=scope or priority_key,
            priority_key=priority_key,
            source_rank=rank,
            metadata=local_metadata,
            result_summary=local_summary,
            media_kind=media_kind,
        )
        if is_valid_preview_action_source(context):
            return context
    return invalid_preview_action_source("No preview output is selected yet.")


def preview_action_source_metadata(source_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build a compact serializable metadata block for source handoff/audits."""
    source = _as_dict(source_context)
    return {
        "source_type": source.get("source_type", ""),
        "source_scope": source.get("source_scope", ""),
        "priority_key": source.get("priority_key", ""),
        "source_rank": source.get("source_rank", -1),
        "result_id": source.get("result_id", ""),
        "job_id": source.get("job_id", ""),
        "output_id": source.get("output_id", ""),
        "file_id": source.get("file_id", ""),
        "filename": source.get("filename", ""),
        "url": source.get("url", ""),
        "path": source.get("path", ""),
        "is_valid": bool(source.get("is_valid")),
        "missing_reason": source.get("missing_reason", ""),
    }

PREVIEW_ACTION_REGISTRY_SCHEMA_VERSION = 1


def _action_public_contract(action: Dict[str, Any]) -> Dict[str, Any]:
    """Return the UI-safe registry fields for one preview action."""
    public_keys = (
        "id",
        "group",
        "icon",
        "label",
        "tooltip",
        "type",
        "requires_extension",
        "requires_capability",
        "target_workspace",
        "target_mode",
        "target_panel",
        "handler",
        "handler_args",
        "stage_policy",
        "auto_run_default",
        "v1_parity",
        "v2_improvement",
        "v1_button_id",
        "v1_auto_queued",
        "preserve_prompt_context",
        "preserve_reference_context",
    )
    return {key: deepcopy(action.get(key)) for key in public_keys if key in action}


def _extension_record(extension_states: Dict[str, Any] | None, extension_id: str) -> Dict[str, Any]:
    states = _as_dict(extension_states)
    record = states.get(extension_id, {})
    if isinstance(record, bool):
        return {"enabled": record}
    return _as_dict(record)


def _route_state_for_action(
    action: Dict[str, Any],
    extension_record: Dict[str, Any],
    route_states: Dict[str, str] | None,
) -> str:
    extension_id = action.get("requires_extension") or ""
    states = route_states or {}
    return _first_text(
        states.get(action.get("id", "")) if isinstance(states, dict) else "",
        states.get(extension_id) if isinstance(states, dict) else "",
        extension_record.get("route_state"),
        "available",
    )


def _capability_available(
    action: Dict[str, Any],
    extension_record: Dict[str, Any],
    capability_states: Dict[str, Any] | None,
) -> bool:
    capability = action.get("requires_capability")
    if not capability:
        return True
    extension_id = action.get("requires_extension") or ""
    states = _as_dict(capability_states)
    direct_key = f"{extension_id}:{capability}"
    for value in (
        states.get(direct_key),
        states.get(action.get("id", "")),
        _as_dict(states.get(extension_id)).get(capability),
        _as_dict(extension_record.get("capabilities")).get(capability),
    ):
        if value is not None:
            return bool(value)
    return False


def evaluate_preview_action(
    action: Dict[str, Any] | str,
    *,
    source_context: Dict[str, Any] | None = None,
    extension_states: Dict[str, Any] | None = None,
    route_states: Dict[str, str] | None = None,
    capability_states: Dict[str, Any] | None = None,
    expert_mode: bool = False,
) -> Dict[str, Any]:
    """Evaluate one preview action for toolbar rendering.

    This is Phase E's central registry decision point. It does not render UI and
    does not run handlers. It only combines the raw V1→V2 action map, the Phase D
    source contract, Admin extension state, route state, and optional capability
    state into a deterministic UI-safe action record.
    """
    if isinstance(action, str):
        raw_action = get_preview_action(action)
        if not raw_action:
            raise KeyError(f"Unknown preview action: {action}")
    else:
        raw_action = deepcopy(action)
    source = _as_dict(source_context)
    source_valid = is_valid_preview_action_source(source)
    action_type = raw_action.get("type")
    extension_id = raw_action.get("requires_extension") or ""
    extension_record = _extension_record(extension_states, extension_id)
    extension_enabled = True
    route_state = "available"
    capability_ok = True
    disabled_reason = ""

    if not source_valid:
        disabled_reason = source.get("missing_reason") or "No preview output is selected yet."

    if action_type == "extension":
        extension_enabled = bool(extension_record.get("enabled", False))
        route_state = _route_state_for_action(raw_action, extension_record, route_states)
        capability_ok = _capability_available(raw_action, extension_record, capability_states)
        if not disabled_reason and not extension_enabled:
            disabled_reason = f"{extension_id} is disabled in Admin."
        if not disabled_reason and route_state not in ALLOWED_ROUTE_STATES:
            disabled_reason = f"{extension_id} route is {route_state}."
        if not disabled_reason and not capability_ok:
            disabled_reason = f"{extension_id} is missing required capability: {raw_action.get('requires_capability')}."

    enabled = bool(
        source_valid
        and (
            action_type == "core"
            or (extension_enabled and route_state in ALLOWED_ROUTE_STATES and capability_ok)
        )
    )
    if action_type == "core":
        visible = source_valid or expert_mode
    else:
        visible = enabled or expert_mode

    record = _action_public_contract(raw_action)
    record.update(
        {
            "schema_version": PREVIEW_ACTION_REGISTRY_SCHEMA_VERSION,
            "visible": bool(visible),
            "enabled": bool(enabled),
            "disabled_reason": "" if enabled else disabled_reason,
            "source_valid": bool(source_valid),
            "source": preview_action_source_metadata(source),
            "route_state": route_state,
            "extension_enabled": bool(extension_enabled),
            "capability_available": bool(capability_ok),
            "diagnostic_visible": bool(expert_mode and not enabled),
        }
    )
    if source_valid:
        record["metadata"] = preview_action_metadata_contract(raw_action["id"], source)["preview_action"]
    else:
        record["metadata"] = {}
    return record


def build_preview_action_registry(
    *,
    source_context: Dict[str, Any] | None = None,
    extension_states: Dict[str, Any] | None = None,
    route_states: Dict[str, str] | None = None,
    capability_states: Dict[str, Any] | None = None,
    expert_mode: bool = False,
    include_hidden: bool = False,
) -> Dict[str, Any]:
    """Build the UI-safe preview action registry for toolbar rendering.

    The returned registry is grouped in V1 order. Normal mode omits hidden
    extension actions; expert/include_hidden callers may inspect gated actions
    and disabled reasons without changing runtime behavior.
    """
    source = _as_dict(source_context) if source_context is not None else invalid_preview_action_source()
    evaluated = [
        evaluate_preview_action(
            action,
            source_context=source,
            extension_states=extension_states,
            route_states=route_states,
            capability_states=capability_states,
            expert_mode=expert_mode,
        )
        for action in PREVIEW_ACTIONS
    ]
    if not include_hidden:
        evaluated = [action for action in evaluated if action.get("visible")]
    grouped: List[Dict[str, Any]] = []
    for group in ACTION_GROUPS:
        group_actions = [action for action in evaluated if action.get("group") == group["id"]]
        if group_actions or include_hidden or expert_mode:
            grouped.append({**deepcopy(group), "actions": group_actions})
    return {
        "schema_version": PREVIEW_ACTION_REGISTRY_SCHEMA_VERSION,
        "source": preview_action_source_metadata(source),
        "source_valid": is_valid_preview_action_source(source),
        "expert_mode": bool(expert_mode),
        "groups": grouped,
        "actions": evaluated,
    }


def build_preview_action_registry_for_state(
    state_snapshot: Dict[str, Any] | None = None,
    *,
    extension_states: Dict[str, Any] | None = None,
    route_states: Dict[str, str] | None = None,
    capability_states: Dict[str, Any] | None = None,
    expert_mode: bool = False,
    include_hidden: bool = False,
) -> Dict[str, Any]:
    """Resolve the Phase D source from V2 state and build the Phase E registry."""
    source = resolve_preview_action_source(state_snapshot or {})
    return build_preview_action_registry(
        source_context=source,
        extension_states=extension_states,
        route_states=route_states,
        capability_states=capability_states,
        expert_mode=expert_mode,
        include_hidden=include_hidden,
    )


def enabled_preview_action_ids(registry: Dict[str, Any] | None = None) -> List[str]:
    """Return enabled action IDs from a built Phase E registry."""
    data = _as_dict(registry)
    return [action.get("id", "") for action in _as_list(data.get("actions")) if action.get("enabled")]


def preview_action_registry_errors(registry: Dict[str, Any] | None = None) -> List[str]:
    """Return structural errors for a built Phase E registry."""
    data = _as_dict(registry)
    errors: List[str] = []
    if data.get("schema_version") != PREVIEW_ACTION_REGISTRY_SCHEMA_VERSION:
        errors.append("Registry schema version mismatch")
    grouped_ids: List[str] = []
    for group in _as_list(data.get("groups")):
        if not isinstance(group, dict):
            errors.append("Registry group is not an object")
            continue
        if group.get("id") not in {item["id"] for item in ACTION_GROUPS}:
            errors.append(f"Unknown registry group: {group.get('id')}")
        for action in _as_list(group.get("actions")):
            if not isinstance(action, dict):
                errors.append("Registry action is not an object")
                continue
            grouped_ids.append(action.get("id", ""))
            if action.get("auto_run_default") is not False:
                errors.append(f"{action.get('id')} may not auto-run by default")
            if action.get("enabled") and not action.get("source_valid"):
                errors.append(f"{action.get('id')} enabled without valid source")
    flat_ids = [action.get("id", "") for action in _as_list(data.get("actions"))]
    if grouped_ids != flat_ids:
        errors.append("Grouped action order does not match flat action order")
    return errors
