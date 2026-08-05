"""Provider-neutral preview reference handoff validation.

Preview Reference actions stage a selected Neo output into ControlNet or
IP-Adapter without changing the selected Image backend profile.  The browser
stores a provider-bound contract inside the extension metadata; this module
revalidates that contract before provider compilation.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA_ID = "neo.image.preview_reference_handoff.v1"
REPORT_SCHEMA_ID = "neo.image.preview_reference_handoff_normalization.v1"

REFERENCE_TARGETS: dict[str, dict[str, str]] = {
    "image.controlnet": {
        "action_id": "extension.controlnet",
        "asset_bucket": "control_images",
        "target_kind": "controlnet_unit",
    },
    "image.ip_adapter": {
        "action_id": "extension.ip_adapter",
        "asset_bucket": "reference_images",
        "target_kind": "ip_adapter_unit",
    },
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _asset_ref(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in (
            "ref",
            "path",
            "stored_path",
            "saved_path",
            "source_path",
            "url",
            "view_url",
            "preview_url",
            "file",
            "filename",
            "image",
            "value",
        ):
            found = _asset_ref(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _asset_ref(item)
            if found:
                return found
    return ""


def _normalized_ref(value: Any) -> str:
    text = _asset_ref(value).replace("\\", "/").strip()
    if not text:
        return ""
    return text.split("?", 1)[0].casefold()


def source_record_from_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    raw = _as_dict(contract)
    source = _as_dict(raw.get("source")) or raw
    path = _text(source.get("path"), source.get("saved_path"))
    url = _text(source.get("url"), source.get("view_url"))
    filename = _text(
        source.get("filename"),
        source.get("saved_filename"),
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
        "parent_output_id": _text(source.get("parent_output_id")),
        "parent_job_id": _text(source.get("parent_job_id")),
    }


def build_preview_reference_handoff(
    source: dict[str, Any] | None,
    *,
    action_id: str,
    target_extension: str,
    target_unit_id: str,
    target_unit_index: int,
    profile_id: str,
    provider_id: str,
    dispatch_type: str = "stage_reference",
    execution_mode: str = "",
    stage_policy: str = "first_empty_slot_no_overwrite",
    created_at: str = "",
) -> dict[str, Any]:
    expected = REFERENCE_TARGETS.get(str(target_extension or ""), {})
    return {
        "schema": SCHEMA_ID,
        "action_id": str(action_id or expected.get("action_id") or ""),
        "action_class": "reference_stage",
        "target_extension": str(target_extension or ""),
        "target_kind": str(expected.get("target_kind") or "reference_unit"),
        "target_unit_id": str(target_unit_id or ""),
        "target_unit_index": max(0, _int(target_unit_index, 0)),
        "profile_id": str(profile_id or "").strip(),
        "provider_id": str(provider_id or "").strip().lower(),
        "dispatch_type": str(dispatch_type or "stage_reference"),
        "execution_mode": str(execution_mode or ""),
        "provider_policy": "selected_profile_only",
        "catalog_policy": "selected_profile_live_catalog",
        "automatic_provider_fallback": False,
        "overwrite_existing": False,
        "auto_run": False,
        "stage_policy": str(stage_policy or "first_empty_slot_no_overwrite"),
        "created_at": str(created_at or ""),
        "source": source_record_from_contract(source),
    }


def _extension_container(extensions: Any) -> tuple[dict[str, Any], str]:
    payload = _as_dict(extensions)
    for key in ("payloads", "extensions"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested, key
    return payload, ""


def _unit_by_id(block: dict[str, Any], unit_id: str, unit_index: int) -> dict[str, Any]:
    inputs = _as_dict(block.get("inputs"))
    units = [row for row in _as_list(inputs.get("units")) if isinstance(row, dict)]
    if unit_id:
        match = next((row for row in units if str(row.get("uid") or "") == unit_id), None)
        if isinstance(match, dict):
            return match
    if 0 <= unit_index < len(units):
        return units[unit_index]
    return {}


def normalize_preview_reference_handoffs(
    extensions: Any,
    *,
    provider_id: str = "",
    profile_id: str = "",
) -> tuple[Any, dict[str, Any]]:
    """Validate preview reference contracts against the selected provider/profile.

    A mismatched contract is blocked rather than rebound to another backend.
    Existing extension state without a preview-action contract remains valid.
    """

    normalized = deepcopy(extensions if isinstance(extensions, dict) else {})
    container, nested_key = _extension_container(normalized)
    selected_provider = str(provider_id or "").strip().lower()
    selected_profile = str(profile_id or "").strip()
    items: list[dict[str, Any]] = []
    blocked_codes: list[str] = []

    for extension_id, policy in REFERENCE_TARGETS.items():
        block = _as_dict(container.get(extension_id))
        if not block:
            continue
        metadata = _as_dict(block.get("metadata"))
        contract = _as_dict(metadata.get("preview_reference_handoff"))
        if not contract:
            continue
        if block.get("enabled") is False:
            items.append({
                "extension_id": extension_id,
                "action_id": policy["action_id"],
                "target_unit_id": str(contract.get("target_unit_id") or ""),
                "target_unit_index": max(0, _int(contract.get("target_unit_index"), 0)),
                "provider_id": str(contract.get("provider_id") or selected_provider),
                "profile_id": str(contract.get("profile_id") or selected_profile),
                "source_ref": _text(source_record_from_contract(contract).get("path"), source_record_from_contract(contract).get("url")),
                "asset_ref": "",
                "status": "ignored_disabled",
                "warning_codes": [],
            })
            continue

        codes: list[str] = []
        contract_provider = str(contract.get("provider_id") or "").strip().lower()
        contract_profile = str(contract.get("profile_id") or "").strip()
        if contract_provider and selected_provider and contract_provider != selected_provider:
            codes.append("preview_reference_provider_mismatch")
        if contract_profile and selected_profile and contract_profile != selected_profile:
            codes.append("preview_reference_profile_mismatch")
        if str(contract.get("action_id") or policy["action_id"]) != policy["action_id"]:
            codes.append("preview_reference_action_mismatch")
        if str(contract.get("target_extension") or extension_id) != extension_id:
            codes.append("preview_reference_extension_mismatch")
        if str(contract.get("dispatch_type") or "stage_reference") != "stage_reference":
            codes.append("preview_reference_dispatch_mismatch")
        if bool(contract.get("automatic_provider_fallback")):
            codes.append("preview_reference_fallback_forbidden")
        if bool(contract.get("overwrite_existing")):
            codes.append("preview_reference_overwrite_forbidden")
        if bool(contract.get("auto_run")):
            codes.append("preview_reference_auto_run_forbidden")

        unit_id = str(contract.get("target_unit_id") or "").strip()
        unit_index = max(0, _int(contract.get("target_unit_index"), 0))
        unit = _unit_by_id(block, unit_id, unit_index)
        if not unit:
            codes.append("preview_reference_target_unit_missing")
        elif unit.get("enabled") is False:
            codes.append("preview_reference_target_unit_disabled")
        resolved_unit_id = str(unit.get("uid") or unit_id or f"unit_{unit_index + 1}")

        assets = _as_dict(block.get("assets"))
        bucket = _as_dict(assets.get(policy["asset_bucket"]))
        staged_asset = bucket.get(resolved_unit_id)
        if staged_asset is None and resolved_unit_id == "primary":
            staged_asset = bucket.get("primary")
        staged_ref = _asset_ref(staged_asset)
        source = source_record_from_contract(contract)
        source_ref = _text(source.get("path"), source.get("url"))
        if not source_ref:
            codes.append("preview_reference_contract_missing_ref")
        if not staged_ref:
            codes.append("preview_reference_asset_missing")
        elif source_ref and _normalized_ref(staged_ref) != _normalized_ref(source_ref):
            codes.append("preview_reference_asset_source_mismatch")

        effective = deepcopy(contract)
        effective.update({
            "schema": SCHEMA_ID,
            "action_id": policy["action_id"],
            "action_class": "reference_stage",
            "target_extension": extension_id,
            "target_kind": policy["target_kind"],
            "target_unit_id": resolved_unit_id,
            "target_unit_index": unit_index,
            "provider_id": selected_provider or contract_provider,
            "profile_id": selected_profile or contract_profile,
            "dispatch_type": "stage_reference",
            "provider_policy": "selected_profile_only",
            "catalog_policy": "selected_profile_live_catalog",
            "automatic_provider_fallback": False,
            "overwrite_existing": False,
            "auto_run": False,
            "source": source,
        })
        metadata["preview_reference_handoff"] = effective
        block["metadata"] = metadata
        container[extension_id] = block

        status = "blocked" if codes else "normalized"
        items.append({
            "extension_id": extension_id,
            "action_id": policy["action_id"],
            "target_unit_id": resolved_unit_id,
            "target_unit_index": unit_index,
            "provider_id": effective["provider_id"],
            "profile_id": effective["profile_id"],
            "source_ref": source_ref,
            "asset_ref": staged_ref,
            "status": status,
            "warning_codes": codes,
        })
        blocked_codes.extend(codes)

    if nested_key:
        normalized[nested_key] = container
    else:
        normalized = container
    report = {
        "schema": REPORT_SCHEMA_ID,
        "provider_id": selected_provider,
        "profile_id": selected_profile,
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
        "contract_count": len(items),
        "status": "blocked" if blocked_codes else ("normalized" if items else "not_applicable"),
        "warning_codes": sorted(set(blocked_codes)),
        "items": items,
    }
    return normalized, report
