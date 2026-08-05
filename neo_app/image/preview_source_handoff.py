"""Provider-neutral preview source handoff contracts.

Preview Source actions (Img2Img, Inpaint, Outpaint) stage a selected Neo output
without selecting or invoking a different provider.  The browser stores this
contract on the draft and the API revalidates the provider/profile binding
before handing the source to a provider compiler.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA_ID = "neo.image.preview_source_handoff.v1"
REPORT_SCHEMA_ID = "neo.image.preview_source_handoff_normalization.v1"
SOURCE_MODES = {"img2img", "inpaint", "outpaint"}
SOURCE_ACTION_IDS = {
    "img2img": "core.img2img",
    "inpaint": "core.inpaint",
    "outpaint": "core.outpaint",
}

# These names are backend upload/cache outputs, not canonical source identity.
# A newly staged preview source must force the selected provider to resolve the
# canonical Neo source again instead of reusing a previous backend upload name.
PROVIDER_TRANSIENT_KEYS = {
    "comfy_source_image_name",
    "source_image_uploaded_to_comfy",
    "comfy_mask_image_name",
    "comfy_outpaint_canvas_image_name",
    "comfy_outpaint_mask_image_name",
    "forge_source_image_b64",
    "forge_mask_image_b64",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def normalize_source_mode(value: Any) -> str:
    mode = str(value or "img2img").strip().lower().replace("-", "_")
    if mode == "image_to_image":
        return "img2img"
    return mode if mode in SOURCE_MODES else "img2img"


def source_record_from_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    raw = _as_dict(contract)
    nested = _as_dict(raw.get("source"))
    source = nested or raw
    path = _text(source.get("path"), source.get("saved_path"), source.get("source_image_path"))
    url = _text(source.get("url"), source.get("view_url"), source.get("source_image_url"))
    filename = _text(
        source.get("filename"),
        source.get("saved_filename"),
        source.get("source_image_name"),
        Path(path).name if path else "",
        Path(url.split("?", 1)[0]).name if url else "",
    )
    return {
        "source_type": _text(source.get("source_type"), "generated_output"),
        "source_scope": _text(source.get("source_scope")),
        "result_id": _text(source.get("result_id")),
        "job_id": _text(source.get("job_id")),
        "output_id": _text(source.get("output_id")),
        "file_id": _text(source.get("file_id")),
        "filename": filename,
        "saved_filename": _text(source.get("saved_filename"), filename),
        "path": path,
        "url": url,
        "width": _int(source.get("width") or source.get("image_width") or source.get("source_width")),
        "height": _int(source.get("height") or source.get("image_height") or source.get("source_height")),
        "parent_output_id": _text(source.get("parent_output_id")),
        "parent_job_id": _text(source.get("parent_job_id")),
    }


def build_preview_source_handoff(
    source: dict[str, Any] | None,
    *,
    action_id: str,
    target_mode: str,
    profile_id: str,
    provider_id: str,
    dispatch_type: str = "stage_source_mode",
    execution_mode: str = "",
    replay_source: str = "none",
    created_at: str = "",
) -> dict[str, Any]:
    mode = normalize_source_mode(target_mode)
    record = source_record_from_contract(source)
    return {
        "schema": SCHEMA_ID,
        "action_id": str(action_id or SOURCE_ACTION_IDS[mode]),
        "action_class": "source_stage",
        "target_mode": mode,
        "profile_id": str(profile_id or "").strip(),
        "provider_id": str(provider_id or "").strip().lower(),
        "dispatch_type": str(dispatch_type or "stage_source_mode"),
        "execution_mode": str(execution_mode or ""),
        "provider_policy": "selected_profile_only",
        "automatic_provider_fallback": False,
        "auto_run": False,
        "mask_policy": "clear_on_stage",
        "prompt_context_policy": "preserve_or_explicit_replay",
        "reference_context_policy": "preserve_or_explicit_replay",
        "replay_source": str(replay_source or "none"),
        "created_at": str(created_at or ""),
        "source": record,
    }


def normalize_preview_source_handoff_params(
    params: dict[str, Any] | None,
    *,
    runtime_mode: str,
    provider_id: str = "",
    profile_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote and validate a staged source contract before provider handoff.

    The selected request profile/provider is authoritative.  A source contract
    bound to another provider/profile is blocked rather than silently rerouted.
    """

    normalized = deepcopy(params if isinstance(params, dict) else {})
    raw_mode = str(runtime_mode or "").strip().lower().replace("-", "_")
    if raw_mode == "image_to_image":
        raw_mode = "img2img"
    mode = raw_mode if raw_mode in SOURCE_MODES else ""
    contract = _as_dict(normalized.get("_neo_preview_action_source"))
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA_ID,
        "contract_schema": str(contract.get("schema") or ""),
        "runtime_mode": mode or raw_mode,
        "provider_id": str(provider_id or "").strip().lower(),
        "profile_id": str(profile_id or "").strip(),
        "contract_present": bool(contract),
        "provider_locked": False,
        "cleared_provider_fields": [],
        "warning_codes": [],
        "status": "not_applicable",
    }
    if not contract or mode not in SOURCE_MODES:
        return normalized, report

    report["status"] = "normalized"
    contract_provider = str(contract.get("provider_id") or "").strip().lower()
    contract_profile = str(contract.get("profile_id") or "").strip()
    requested_provider = report["provider_id"]
    requested_profile = report["profile_id"]

    if contract_provider and requested_provider and contract_provider != requested_provider:
        report["status"] = "blocked"
        report["warning_codes"].append("preview_source_provider_mismatch")
    if contract_profile and requested_profile and contract_profile != requested_profile:
        report["status"] = "blocked"
        report["warning_codes"].append("preview_source_profile_mismatch")

    contract_mode = normalize_source_mode(contract.get("target_mode") or mode)
    if contract_mode != mode:
        report["status"] = "blocked"
        report["warning_codes"].append("preview_source_target_mode_mismatch")

    expected_action = SOURCE_ACTION_IDS[mode]
    contract_action = str(contract.get("action_id") or expected_action)
    if contract_action != expected_action:
        report["warning_codes"].append("preview_source_action_mode_mismatch")

    source = source_record_from_contract(contract)
    source_ref = _text(source.get("path"), source.get("url"))
    if not source_ref:
        report["status"] = "blocked"
        report["warning_codes"].append("preview_source_contract_missing_ref")
    else:
        # The selected preview contract is the source authority for this handoff.
        # Never retain an older source field or backend upload alias alongside it.
        normalized["source_image"] = source_ref
        normalized["source_image_path"] = source.get("path") or ""
        normalized["source_image_url"] = source.get("url") or ""
        normalized["source_image_name"] = source.get("filename") or ""
        normalized["source_image_width"] = source.get("width") or 0
        normalized["source_image_height"] = source.get("height") or 0
        report["source_promoted"] = True

    for key in sorted(PROVIDER_TRANSIENT_KEYS):
        if key in normalized:
            normalized.pop(key, None)
            report["cleared_provider_fields"].append(key)

    effective_contract = deepcopy(contract)
    effective_contract["schema"] = SCHEMA_ID
    effective_contract["target_mode"] = mode
    effective_contract["action_id"] = expected_action
    effective_contract["provider_id"] = requested_provider or contract_provider
    effective_contract["profile_id"] = requested_profile or contract_profile
    effective_contract["provider_policy"] = "selected_profile_only"
    effective_contract["automatic_provider_fallback"] = False
    effective_contract["auto_run"] = False
    effective_contract["source"] = source
    normalized["_neo_preview_action_source"] = effective_contract
    report["provider_locked"] = bool(effective_contract.get("provider_id") or effective_contract.get("profile_id"))
    normalized["_neo_preview_source_handoff"] = report
    if report["warning_codes"]:
        existing = normalized.get("_neo_route_validation_warnings") if isinstance(normalized.get("_neo_route_validation_warnings"), list) else []
        normalized["_neo_route_validation_warnings"] = sorted({*(str(item) for item in existing), *report["warning_codes"]})
    return normalized, report
