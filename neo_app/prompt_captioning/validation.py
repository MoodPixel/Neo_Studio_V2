from __future__ import annotations

from typing import Any

from .support_matrix import get_support_matrix
from .payload_contract import normalize_prompt_captioning_payload
from .batch_safety import batch_dataset_safety

TEXT_TOOLS = {"prompt_generate", "prompt_enhance", "prompt_rewrite", "prompt_cleanup", "negative_prompt", "text_transform", "prompt_studio"}
CAPTION_TOOLS = {"image_captioning", "result_image_captioning", "batch_captioning", "caption_studio"}
RUNNABLE_STATES = {"available", "experimental_available"}
VALIDATION_CHIPS = {"Available", "Provider gated", "Unsupported", "Offline", "Running", "Error", "Saved", "Sent"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _profile_flags(profile: dict[str, Any] | None) -> dict[str, bool]:
    flags = (profile or {}).get("capability_flags") or {}
    runtime_caps = (profile or {}).get("runtime", {}).get("capabilities") or {}
    runtime_supports_vision = _as_bool(runtime_caps.get("runtime_supports_vision", runtime_caps.get("supports_vision")), False)
    runtime_supports_captioning = _as_bool(runtime_caps.get("runtime_supports_captioning", runtime_caps.get("supports_captioning")), False)
    effective_supports_vision = _as_bool(flags.get("supports_vision"), False) or runtime_supports_vision
    # One KoboldCpp profile is enough: if the backend reports a loaded vision/mmproj route,
    # Caption Studio can unlock without forcing a duplicate "vision" profile.
    effective_supports_captioning = _as_bool(flags.get("supports_captioning"), False) or runtime_supports_captioning or runtime_supports_vision
    return {
        "supports_text": _as_bool(flags.get("supports_text", runtime_caps.get("supports_text")), True),
        "supports_vision": effective_supports_vision,
        "supports_captioning": effective_supports_captioning,
        "streaming_enabled": _as_bool(runtime_caps.get("streaming_enabled", flags.get("streaming_enabled")), False),
    }

def _profile_status(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "missing_config"
    if profile.get("enabled") is False:
        return "disabled"
    return str(profile.get("runtime_status") or (profile.get("runtime") or {}).get("status") or profile.get("profile_status") or "disconnected")


def _profiles_for_prompt_captioning(backend_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = backend_payload or {}
    profiles = payload.get("profiles") or []
    return [
        profile for profile in profiles
        if profile.get("surface") in {"prompt_captioning", "text"}
    ]


def _requested_profile_id(clean_payload: dict[str, Any]) -> str:
    metadata = clean_payload.get("metadata") if isinstance(clean_payload.get("metadata"), dict) else {}
    params = clean_payload.get("params") if isinstance(clean_payload.get("params"), dict) else {}
    inputs = clean_payload.get("inputs") if isinstance(clean_payload.get("inputs"), dict) else {}
    for source in (metadata, params, inputs):
        for key in ("backend_profile_id", "backend_profile", "caption_backend_profile_id", "caption_backend_profile"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def resolve_caption_profile_capabilities(profile: dict[str, Any] | None) -> dict[str, Any]:
    flags = _profile_flags(profile)
    raw_flags = (profile or {}).get("capability_flags") or {}
    runtime_caps = (profile or {}).get("runtime", {}).get("capabilities") or {}
    status = _profile_status(profile)
    profile_id = (profile or {}).get("profile_id", "")
    provider_id = (profile or {}).get("provider_id", "")
    enabled = bool(profile) and profile.get("enabled") is not False
    profile_supports_vision = _as_bool(raw_flags.get("supports_vision"), False)
    profile_supports_captioning = _as_bool(raw_flags.get("supports_captioning"), False)
    runtime_supports_vision = _as_bool(runtime_caps.get("runtime_supports_vision", runtime_caps.get("supports_vision")), False)
    runtime_supports_captioning = _as_bool(runtime_caps.get("runtime_supports_captioning", runtime_caps.get("supports_captioning")), False)
    if profile_supports_captioning:
        capability_source = "manual_caption_flag"
    elif profile_supports_vision:
        capability_source = "profile_vision_flag"
    elif runtime_supports_vision or runtime_supports_captioning:
        capability_source = "runtime_vision_detected"
    else:
        capability_source = "profile_flag"
    gate_reason = ""
    warnings: list[str] = []
    if not profile:
        gate_reason = "missing_backend_profile"
    elif not enabled:
        gate_reason = "backend_profile_disabled"
    elif status in {"offline", "missing_config", "error"}:
        gate_reason = "backend_profile_offline"
    elif not flags["supports_vision"]:
        gate_reason = "vision_support_disabled"
    elif not flags["supports_captioning"]:
        gate_reason = "caption_support_disabled"
    if (runtime_supports_vision or runtime_supports_captioning) and not (profile_supports_vision and profile_supports_captioning):
        warnings.append("Runtime vision/caption support detected from the backend; Caption Studio is enabled for this session.")
    return {
        "profile_id": profile_id,
        "provider_id": provider_id,
        "supports_text": flags["supports_text"],
        "supports_vision": flags["supports_vision"],
        "supports_captioning": flags["supports_captioning"],
        "profile_supports_vision": profile_supports_vision,
        "profile_supports_captioning": profile_supports_captioning,
        "runtime_supports_vision": runtime_supports_vision,
        "runtime_supports_captioning": runtime_supports_captioning,
        "capability_source": capability_source,
        "enabled": enabled,
        "status": status,
        "caption_runnable": not gate_reason,
        "gate_reason": gate_reason,
        "warnings": warnings,
    }


def _resolve_profile(clean_payload: dict[str, Any], backend_payload: dict[str, Any] | None, *, require: str) -> dict[str, Any] | None:
    requested_id = _requested_profile_id(clean_payload)
    profiles = _profiles_for_prompt_captioning(backend_payload)
    if requested_id:
        return next((profile for profile in profiles if profile.get("profile_id") == requested_id), None)
    if require == "caption":
        for profile in profiles:
            caps = resolve_caption_profile_capabilities(profile)
            if caps["caption_runnable"] or (caps["supports_vision"] and caps["supports_captioning"] and caps["enabled"]):
                return profile
    defaults = (backend_payload or {}).get("defaults") or {}
    for profile_id in (defaults.get("prompt_captioning"), defaults.get("text")):
        if profile_id:
            match = next((profile for profile in profiles if profile.get("profile_id") == profile_id), None)
            if match:
                return match
    return next((profile for profile in profiles if profile.get("enabled") is not False), None) or (profiles[0] if profiles else None)


def _profile_route_state(profile: dict[str, Any] | None, *, require: str) -> tuple[str, str, str]:
    if not profile:
        return "provider_gated", "missing_backend_profile", "No backend profile is configured for this Prompt & Captioning route."
    profile_id = profile.get("profile_id") or "unknown"
    if profile.get("enabled") is False:
        return "provider_gated", "backend_profile_disabled", f"Backend profile '{profile_id}' is disabled."
    flags = _profile_flags(profile)
    status = _profile_status(profile)
    if status in {"offline", "missing_config", "error"}:
        return "provider_gated", "backend_profile_offline", f"Backend profile '{profile_id}' is {status}."
    if require == "text" and not flags["supports_text"]:
        return "provider_gated", "text_support_disabled", f"Backend profile '{profile_id}' does not declare text support."
    if require == "caption":
        caps = resolve_caption_profile_capabilities(profile)
        if caps.get("warnings"):
            pass
        if caps["gate_reason"]:
            readable = {
                "missing_backend_profile": "No caption backend profile is configured.",
                "backend_profile_disabled": f"Backend profile '{profile_id}' is disabled.",
                "backend_profile_offline": f"Backend profile '{profile_id}' is {status}.",
                "vision_support_disabled": "No backend vision support detected. Load a KoboldCpp vision model/mmproj or enable Vision support on the profile.",
                "caption_support_disabled": "Selected backend profile does not support image captioning. Choose a vision-capable profile.",
            }.get(caps["gate_reason"], f"Backend profile '{profile_id}' cannot run captioning.")
            return "provider_gated", caps["gate_reason"], readable
    if status in {"disconnected", "enabled"}:
        return "experimental_available", "", f"Backend profile '{profile_id}' is configured but not connected/tested yet."
    return "available", "", f"Backend profile '{profile_id}' supports this route."


def _status_chip(state: str, errors: list[str] | None = None) -> str:
    if errors:
        reason = " ".join(errors).lower()
        if "unsupported" in reason:
            return "Unsupported"
        if "offline" in reason:
            return "Offline"
        if "provider" in reason or "backend" in reason or "disabled" in reason or "configured" in reason:
            return "Provider gated"
        return "Error"
    if state in RUNNABLE_STATES:
        return "Available"
    if state == "unsupported":
        return "Unsupported"
    if state == "provider_gated":
        return "Provider gated"
    return "Error"


def _tool_id(payload: dict[str, Any]) -> str:
    return str(payload.get("tool") or payload.get("tool_id") or "").strip()


def _assets(payload: dict[str, Any]) -> dict[str, Any]:
    assets = payload.get("assets")
    return assets if isinstance(assets, dict) else {}


def _input_text(clean_payload: dict[str, Any]) -> str:
    inputs = clean_payload.get("inputs") if isinstance(clean_payload.get("inputs"), dict) else {}
    for key in ("source_text", "idea", "prompt", "text", "custom_instructions"):
        value = str(inputs.get(key) or "").strip()
        if value:
            return value
    return ""


def _asset_refs(clean_payload: dict[str, Any]) -> list[str]:
    assets = _assets(clean_payload)
    refs: list[str] = []
    for key in ("image", "source_image", "result_image"):
        if assets.get(key):
            refs.append(str(assets.get(key)))
    images = assets.get("images") or assets.get("image_batch") or []
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                ref = item.get("asset_ref") or item.get("path") or item.get("image") or item.get("source_image") or item.get("url")
            else:
                ref = item
            if ref:
                refs.append(str(ref))
    return refs


def _validate_image_refs(refs: list[str], *, require_local: bool = False) -> list[str]:
    errors: list[str] = []
    for ref in refs:
        lowered = ref.lower().split("?")[0]
        if lowered.startswith(("http://", "https://", "data:image/")):
            continue
        if not any(lowered.endswith(suffix) for suffix in IMAGE_SUFFIXES):
            errors.append(f"Captioning asset is not a supported image type: {ref}")
            continue
        if require_local:
            from pathlib import Path
            path = Path(ref)
            if not path.exists() or not path.is_file():
                errors.append(f"Captioning image asset is missing or not a file: {ref}")
    return errors



def _normalize_batch_workflow_mode(value: object) -> str:
    text = str(value or "dataset").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"library", "save_to_library", "save_library"}:
        return "library"
    return "dataset"

def validate_route_payload(payload: dict[str, Any], backend_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a Prompt & Captioning route without executing it.

    Phase P makes this the single route-aware preflight used by run routes,
    UI status chips, replay validation, and future handoff execution.
    """
    normalized = normalize_prompt_captioning_payload(payload)
    clean_payload = normalized["payload"]
    tool_id = _tool_id(clean_payload)
    matrix = get_support_matrix(backend_payload)
    aliases = {
        "prompt_studio": "prompt_generate",
        "caption_studio": "image_captioning",
    }
    matrix_tool_id = aliases.get(tool_id, tool_id)
    match = next((item for item in matrix["tools"] if item["tool_id"] == matrix_tool_id), None)
    errors: list[str] = []
    warnings: list[str] = []

    if not tool_id:
        errors.append("Missing tool id.")
    if match is None:
        errors.append(f"Unsupported Prompt & Captioning tool: {tool_id}")

    metadata = clean_payload.get("metadata") if isinstance(clean_payload.get("metadata"), dict) else {}
    params = clean_payload.get("params") if isinstance(clean_payload.get("params"), dict) else {}
    inputs = clean_payload.get("inputs") if isinstance(clean_payload.get("inputs"), dict) else {}
    if metadata.get("disabled") is True or metadata.get("tool_enabled") is False or params.get("enabled") is False or inputs.get("enabled") is False:
        errors.append("Tool is disabled and cannot run.")

    require = "caption" if matrix_tool_id in CAPTION_TOOLS else "text"
    profile = _resolve_profile(clean_payload, backend_payload, require=require) if matrix_tool_id in TEXT_TOOLS | CAPTION_TOOLS else None
    state = match["state"] if match else "unsupported"
    reason = match["reason"] if match else "Tool is not declared in the Prompt & Captioning support matrix."
    gate_reason_code = ""
    if matrix_tool_id in TEXT_TOOLS | CAPTION_TOOLS:
        state, gate_reason_code, reason = _profile_route_state(profile, require=require)
        if require == "caption":
            caps_for_warnings = resolve_caption_profile_capabilities(profile)
            warnings.extend(caps_for_warnings.get("warnings") or [])
        if state not in RUNNABLE_STATES:
            errors.append(reason)
    elif match and match["state"] not in RUNNABLE_STATES:
        errors.append(match["reason"])

    if matrix_tool_id in {"prompt_enhance", "prompt_rewrite", "prompt_cleanup", "text_transform"} and not _input_text(clean_payload):
        errors.append(f"{matrix_tool_id} requires source text or custom instructions.")

    assets = _assets(clean_payload)
    if matrix_tool_id in {"image_captioning", "result_image_captioning"}:
        refs = _asset_refs(clean_payload)
        if not refs:
            gate_reason_code = gate_reason_code or "missing_image_asset"
            errors.append("Captioning requires a valid image asset.")
        else:
            image_errors = _validate_image_refs(refs[:1], require_local=False)
            if image_errors:
                gate_reason_code = gate_reason_code or "unsupported_image_type"
            errors.extend(image_errors)
    if matrix_tool_id == "batch_captioning":
        inputs = clean_payload.get("inputs") if isinstance(clean_payload.get("inputs"), dict) else {}
        params = clean_payload.get("params") if isinstance(clean_payload.get("params"), dict) else {}
        folder_path = str(inputs.get("folder_path") or "").strip()
        workflow_mode = _normalize_batch_workflow_mode(inputs.get("workflow_mode") or "dataset")
        caption_mode = str((params.get("caption_settings") or {}).get("caption_mode") or params.get("caption_mode") or "full_image").strip()
        dataset = params.get("dataset") if isinstance(params.get("dataset"), dict) else {}
        caption_images = dataset.get("caption_images", True) is not False
        if not folder_path:
            gate_reason_code = gate_reason_code or "missing_input_folder"
            errors.append("Batch captioning requires an input folder path.")
        if workflow_mode == "dataset" and not str(dataset.get("output_folder") or "").strip():
            gate_reason_code = gate_reason_code or "missing_output_folder"
            errors.append("Dataset Preparation requires an output folder path.")
        if workflow_mode == "dataset" and str(dataset.get("output_folder") or "").strip():
            safety = batch_dataset_safety(dataset)
            if not safety.get("ok"):
                gate_reason_code = "batch_safety_confirmation_required"
                errors.extend(safety.get("errors") or [])
            warnings.extend(safety.get("warnings") or [])
        if caption_mode == "custom_crop":
            gate_reason_code = gate_reason_code or "custom_crop_unsupported"
            errors.append("Batch captioning does not support Custom crop mode.")
        if workflow_mode == "dataset" and not caption_images:
            # Folder-only dataset copy/rename jobs do not require an image-caption provider.
            errors = [err for err in errors if "backend" not in err.lower() and "vision" not in err.lower() and "caption support" not in err.lower()]

    if matrix_tool_id in TEXT_TOOLS and any(key in assets for key in {"image", "images", "source_image", "result_image"}):
        warnings.append("Image assets are ignored for text-only prompt tools and must not be emitted in the run payload.")

    chip = _status_chip(state, errors)
    return {
        "ok": not errors,
        "surface_id": "prompt_captioning",
        "tool_id": tool_id,
        "resolved_tool_id": matrix_tool_id,
        "state": state if not errors else ("unsupported" if any("Unsupported" in err for err in errors) else state),
        "status_chip": chip,
        "runnable": not errors and state in RUNNABLE_STATES,
        "reason": reason,
        "gate_reason": gate_reason_code,
        "gate_reason_code": gate_reason_code,
        "errors": errors,
        "warnings": warnings,
        "route": {**(match or {}), "state": state, "reason": reason, "status_chip": chip, "runnable": not errors and state in RUNNABLE_STATES},
        "backend_profile": {
            "profile_id": (profile or {}).get("profile_id", ""),
            "provider_id": (profile or {}).get("provider_id", ""),
            "status": _profile_status(profile),
            "capabilities": _profile_flags(profile),
            "caption_capabilities": resolve_caption_profile_capabilities(profile) if require == "caption" else {},
        },
        "payload": clean_payload,
        "stripped_fields": normalized.get("stripped_fields", []),
    }


def validation_status(payload: dict[str, Any], backend_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a UI-friendly validation snapshot with status chips."""
    result = validate_route_payload(payload, backend_payload)
    return {
        "ok": True,
        "surface_id": "prompt_captioning",
        "tool_id": result.get("tool_id"),
        "resolved_tool_id": result.get("resolved_tool_id"),
        "runnable": result.get("runnable", False),
        "state": result.get("state"),
        "status_chip": result.get("status_chip"),
        "reason": result.get("reason"),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
        "backend_profile": result.get("backend_profile") or {},
        "stripped_fields": result.get("stripped_fields") or [],
        "allowed_status_chips": sorted(VALIDATION_CHIPS),
    }
