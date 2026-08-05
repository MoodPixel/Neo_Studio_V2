"""Provider-owned preview Finish dispatch contracts.

Preview Finish actions create derived outputs from an existing Neo image.  The
selected Image backend profile owns the operation.  A contract bound to another
provider/profile is rejected instead of being silently rerouted through Comfy.

This module does not implement Forge native Hires, Forge ADetailer/FaceID, or
Forge Extras.  It establishes the durable dispatch and lineage boundary those
provider executors consume in later phases.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from neo_app.image.preview_actions import get_preview_action

SCHEMA_ID = "neo.image.derived_action.v2"
REPORT_SCHEMA_ID = "neo.image.derived_action_validation.v1"
SAVE_LANE = "append_derived"

FINISH_ACTION_IDS = {
    "extension.high_res_lab",
    "extension.adetailer",
    "extension.identity_rescue",
    "extension.image_upscale",
}

GENERATION_DISPATCH_TYPES = {
    "run_comfy_derived",
    "run_provider_img2img_derived",
    "run_forge_native_hires",
}
UPSCALE_DISPATCH_TYPES = {"run_provider_upscale", "run_provider_extras"}
ALL_FINISH_DISPATCH_TYPES = GENERATION_DISPATCH_TYPES | UPSCALE_DISPATCH_TYPES | {
    "explicit_cross_provider_bridge",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _normalized_runtime_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold().replace("-", "_")
    if mode in {"image_to_image", "image2image"}:
        return "img2img"
    if mode in {"generate", "text_to_image", "text2img"}:
        return "txt2img"
    return mode


def source_record_from_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    raw = _as_dict(contract)
    nested = _as_dict(raw.get("source"))
    source = nested or raw
    path = _text(source.get("path"), source.get("saved_path"), source.get("source_saved_path"))
    url = _text(source.get("url"), source.get("view_url"), source.get("source_view_url"))
    filename = _text(
        source.get("filename"),
        source.get("saved_filename"),
        source.get("source_filename"),
        Path(path).name if path else "",
        Path(url.split("?", 1)[0]).name if url else "",
    )
    lineage = _as_dict(source.get("lineage"))
    raw_ancestors = source.get("ancestor_output_ids") if isinstance(source.get("ancestor_output_ids"), list) else lineage.get("ancestor_output_ids")
    ancestors: list[str] = []
    for value in raw_ancestors if isinstance(raw_ancestors, list) else []:
        item = str(value or "").strip()
        if item and item not in ancestors:
            ancestors.append(item)
    try:
        lineage_depth = int(source.get("lineage_depth") or lineage.get("depth") or 0)
    except (TypeError, ValueError):
        lineage_depth = 0
    return {
        "source_type": _text(source.get("source_type"), source.get("source_kind"), "generated_output"),
        "source_scope": _text(source.get("source_scope")),
        "result_id": _text(source.get("result_id"), lineage.get("current_result_id")),
        "job_id": _text(source.get("job_id"), source.get("source_job_id"), lineage.get("current_job_id")),
        "output_id": _text(source.get("output_id"), source.get("source_output_id"), lineage.get("current_output_id")),
        "file_id": _text(source.get("file_id"), source.get("source_file_id")),
        "filename": filename,
        "saved_filename": _text(source.get("saved_filename"), source.get("source_saved_filename"), filename),
        "path": path,
        "url": url,
        "parent_output_id": _text(source.get("parent_output_id"), lineage.get("parent_output_id")),
        "parent_job_id": _text(source.get("parent_job_id"), lineage.get("parent_job_id")),
        "root_output_id": _text(source.get("root_output_id"), lineage.get("root_output_id")),
        "root_job_id": _text(source.get("root_job_id"), lineage.get("root_job_id")),
        "lineage_depth": max(0, lineage_depth),
        "ancestor_output_ids": ancestors,
        "lineage": deepcopy(lineage),
        "width": int(source.get("width") or 0) if str(source.get("width") or "").isdigit() else 0,
        "height": int(source.get("height") or 0) if str(source.get("height") or "").isdigit() else 0,
    }


def canonical_dispatch_type(action_id: str, provider_id: str) -> str:
    action = get_preview_action(str(action_id or "")) or {}
    dispatch_map = _as_dict(action.get("provider_dispatch"))
    provider = str(provider_id or "").strip().casefold()
    return str(dispatch_map.get(provider) or dispatch_map.get("*") or "unavailable")


def build_derived_action_contract(
    source: dict[str, Any] | None,
    *,
    action_id: str,
    profile_id: str,
    provider_id: str,
    dispatch_type: str,
    execution_mode: str,
    label: str = "",
    created_at: str = "",
    source_provider_id: str = "",
    source_profile_id: str = "",
    cross_provider: bool = False,
) -> dict[str, Any]:
    action = get_preview_action(str(action_id or "")) or {}
    record = source_record_from_contract(source)
    # The selected output is the immediate parent.  Its own parent is provenance,
    # not the parent of the new derived output.
    parent_output_id = _text(record.get("output_id"), record.get("file_id"), record.get("parent_output_id"))
    parent_job_id = _text(record.get("job_id"), record.get("parent_job_id"))
    root_output_id = _text(record.get("root_output_id"), record.get("lineage", {}).get("root_output_id") if isinstance(record.get("lineage"), dict) else "", parent_output_id)
    root_job_id = _text(record.get("root_job_id"), record.get("lineage", {}).get("root_job_id") if isinstance(record.get("lineage"), dict) else "", parent_job_id)
    ancestors: list[str] = []
    for value in [*(record.get("ancestor_output_ids") or []), parent_output_id]:
        item = str(value or "").strip()
        if item and item not in ancestors:
            ancestors.append(item)
    try:
        source_depth = int(record.get("lineage_depth") or 0)
    except (TypeError, ValueError):
        source_depth = 0
    return {
        "schema": SCHEMA_ID,
        "schema_version": 2,
        "action_id": str(action_id or ""),
        "action_class": "post_process",
        "action_type": str(action.get("label") or label or action_id or "derived").strip().casefold().replace(" ", "_"),
        "label": str(label or action.get("label") or action_id or "Derived action"),
        "provider_id": str(provider_id or "").strip().casefold(),
        "profile_id": str(profile_id or "").strip(),
        "dispatch_type": str(dispatch_type or ""),
        "execution_mode": str(execution_mode or ""),
        "provider_policy": "explicit_cross_provider_only" if cross_provider else "selected_profile_only",
        "automatic_provider_fallback": False,
        "cross_provider": bool(cross_provider),
        "source_provider_id": str(source_provider_id or "").strip().casefold(),
        "source_profile_id": str(source_profile_id or "").strip(),
        "finish_provider_id": str(provider_id or "").strip().casefold(),
        "finish_profile_id": str(profile_id or "").strip(),
        "save_lane": SAVE_LANE,
        "output_policy": SAVE_LANE,
        "parent_output_id": parent_output_id,
        "parent_job_id": parent_job_id,
        "source_parent_output_id": _text(record.get("parent_output_id")),
        "source_parent_job_id": _text(record.get("parent_job_id")),
        "root_output_id": root_output_id,
        "root_job_id": root_job_id,
        "lineage_depth": max(source_depth + 1, len(ancestors)),
        "ancestor_output_ids": ancestors,
        "source_output_id": _text(record.get("output_id"), record.get("file_id")),
        "source_job_id": _text(record.get("job_id")),
        "source": record,
        "created_at": str(created_at or ""),
    }


def normalize_preview_finish_params(
    params: dict[str, Any] | None,
    *,
    runtime_mode: str,
    provider_id: str,
    profile_id: str,
    allow_upscale_dispatch: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a generation-backed Finish action before provider compilation.

    Forge ADetailer and Identity Rescue use the selected profile's native
    img2img boundary and are rejected unless their live capabilities and
    extension payloads remain valid.
    """

    normalized = deepcopy(params if isinstance(params, dict) else {})
    raw_contract = _as_dict(normalized.get("_neo_derived_action"))
    legacy_contract = _as_dict(normalized.get("_neo_preview_action"))
    contract = raw_contract or legacy_contract
    selected_provider = str(provider_id or "").strip().casefold()
    selected_profile = str(profile_id or "").strip()
    mode = _normalized_runtime_mode(runtime_mode)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA_ID,
        "contract_schema": str(contract.get("schema") or contract.get("schema_version") or ""),
        "contract_present": bool(contract),
        "provider_id": selected_provider,
        "profile_id": selected_profile,
        "runtime_mode": mode,
        "action_id": str(contract.get("action_id") or ""),
        "dispatch_type": str(contract.get("dispatch_type") or ""),
        "provider_locked": False,
        "warning_codes": [],
        "status": "not_applicable",
    }
    if not contract:
        return normalized, report

    action_id = str(contract.get("action_id") or "").strip()
    action = get_preview_action(action_id) or {}
    if not action or action_id not in FINISH_ACTION_IDS or action.get("action_class") != "post_process":
        report["status"] = "blocked"
        report["warning_codes"].append("derived_action_not_canonical_finish_action")
        return normalized, report

    report["status"] = "validated"
    contract_provider = str(contract.get("provider_id") or contract.get("finish_provider_id") or "").strip().casefold()
    contract_profile = str(contract.get("profile_id") or contract.get("finish_profile_id") or "").strip()
    dispatch_type = str(contract.get("dispatch_type") or "").strip()
    expected_dispatch = canonical_dispatch_type(action_id, selected_provider or contract_provider)

    if contract_provider and selected_provider and contract_provider != selected_provider:
        report["warning_codes"].append("derived_action_provider_mismatch")
    if contract_profile and selected_profile and contract_profile != selected_profile:
        report["warning_codes"].append("derived_action_profile_mismatch")
    if dispatch_type not in ALL_FINISH_DISPATCH_TYPES:
        report["warning_codes"].append("derived_action_dispatch_unknown")
    elif expected_dispatch and expected_dispatch != "unavailable" and dispatch_type != expected_dispatch:
        report["warning_codes"].append("derived_action_dispatch_provider_mismatch")
    if _bool(contract.get("automatic_provider_fallback")):
        report["warning_codes"].append("derived_action_automatic_fallback_forbidden")

    cross_provider = _bool(contract.get("cross_provider"))
    if cross_provider:
        if dispatch_type != "explicit_cross_provider_bridge":
            report["warning_codes"].append("derived_action_cross_provider_dispatch_mismatch")
        if not _text(contract.get("source_provider_id")) or not _text(contract.get("finish_provider_id"), contract_provider):
            report["warning_codes"].append("derived_action_cross_provider_binding_missing")
    elif dispatch_type == "explicit_cross_provider_bridge":
        report["warning_codes"].append("derived_action_explicit_bridge_confirmation_missing")

    source = source_record_from_contract(contract)
    if not _text(source.get("path"), source.get("url")):
        report["warning_codes"].append("derived_action_source_missing")

    if dispatch_type in {"run_comfy_derived", "run_provider_img2img_derived"} and mode != "img2img":
        report["warning_codes"].append("derived_action_runtime_mode_mismatch")
    if dispatch_type == "run_forge_native_hires" and mode != "txt2img":
        report["warning_codes"].append("derived_action_runtime_mode_mismatch")
    if dispatch_type in UPSCALE_DISPATCH_TYPES and not allow_upscale_dispatch:
        report["warning_codes"].append("derived_action_upscale_wrong_endpoint")
    if dispatch_type in UPSCALE_DISPATCH_TYPES and allow_upscale_dispatch and mode not in {"image_upscale", "image_upscale_finish", "finish"}:
        report["warning_codes"].append("derived_action_runtime_mode_mismatch")

    if report["warning_codes"]:
        report["status"] = "blocked"
        existing = normalized.get("_neo_route_validation_warnings") if isinstance(normalized.get("_neo_route_validation_warnings"), list) else []
        normalized["_neo_route_validation_warnings"] = sorted({*(str(item) for item in existing), *report["warning_codes"]})
        normalized["_neo_derived_action_validation"] = report
        return normalized, report

    effective = deepcopy(contract)
    effective.update({
        "schema": SCHEMA_ID,
        "schema_version": 2,
        "action_id": action_id,
        "action_class": "post_process",
        "provider_id": selected_provider or contract_provider,
        "profile_id": selected_profile or contract_profile,
        "finish_provider_id": selected_provider or contract_provider,
        "finish_profile_id": selected_profile or contract_profile,
        "dispatch_type": dispatch_type,
        "provider_policy": "explicit_cross_provider_only" if cross_provider else "selected_profile_only",
        "automatic_provider_fallback": False,
        "cross_provider": cross_provider,
        "save_lane": SAVE_LANE,
        "output_policy": SAVE_LANE,
        "source": source,
        "source_output_id": _text(contract.get("source_output_id"), source.get("output_id"), source.get("file_id")),
        "source_job_id": _text(contract.get("source_job_id"), source.get("job_id")),
        "parent_output_id": _text(contract.get("parent_output_id"), source.get("output_id"), source.get("file_id"), source.get("parent_output_id")),
        "parent_job_id": _text(contract.get("parent_job_id"), source.get("job_id"), source.get("parent_job_id")),
        "source_parent_output_id": _text(contract.get("source_parent_output_id"), source.get("parent_output_id")),
        "source_parent_job_id": _text(contract.get("source_parent_job_id"), source.get("parent_job_id")),
        "root_output_id": _text(contract.get("root_output_id"), source.get("root_output_id"), source.get("lineage", {}).get("root_output_id") if isinstance(source.get("lineage"), dict) else "", source.get("output_id"), source.get("file_id")),
        "root_job_id": _text(contract.get("root_job_id"), source.get("root_job_id"), source.get("lineage", {}).get("root_job_id") if isinstance(source.get("lineage"), dict) else "", source.get("job_id")),
        "lineage_depth": int(contract.get("lineage_depth") or source.get("lineage_depth") or 1),
        "ancestor_output_ids": deepcopy(contract.get("ancestor_output_ids") if isinstance(contract.get("ancestor_output_ids"), list) else source.get("ancestor_output_ids") if isinstance(source.get("ancestor_output_ids"), list) else []),
    })
    normalized["_neo_derived_action"] = effective
    # Compatibility for existing output/replay readers.  New code must prefer
    # _neo_derived_action and must not write the old Comfy bridge schema.
    normalized["_neo_preview_action"] = effective
    normalized["_neo_derived_action_type"] = effective.get("action_type") or action_id
    normalized["_neo_source_output_id"] = effective.get("source_output_id") or ""
    normalized["_neo_source_job_id"] = effective.get("source_job_id") or ""
    normalized["_neo_parent_output_id"] = effective.get("parent_output_id") or ""
    normalized["_neo_save_lane"] = SAVE_LANE
    normalized["save_mode_override"] = SAVE_LANE
    normalized.pop("_post_output_bridge", None)
    report["provider_locked"] = True
    report["expected_dispatch_type"] = expected_dispatch
    normalized["_neo_derived_action_validation"] = report
    return normalized, report
