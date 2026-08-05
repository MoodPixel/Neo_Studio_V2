from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from neo_app.image.lanpaint_family_adapter import get_lanpaint_family_adapter

SCHEMA_ID = "neo.image.lanpaint_replay.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.image.lanpaint_replay"
PHASE11_STATE = "replay_lineage_and_audit_support"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [deepcopy(dict(item)) for item in value if isinstance(item, Mapping)] if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop("replay_fingerprint", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_lanpaint(params: Mapping[str, Any]) -> bool:
    route = _mapping(params.get("lanpaint_route"))
    engine = _text(params.get("inpaint_engine") or route.get("engine")).casefold().replace("-", "_")
    return engine == "lanpaint" and bool(route or params.get("lanpaint_controls") or params.get("lanpaint_ui_state"))


def _portable_asset(item: Mapping[str, Any]) -> dict[str, Any]:
    path = _text(item.get("path"))
    url = _text(item.get("url"))
    filename = _text(item.get("filename"))
    role = _text(item.get("role"))
    return {
        "asset_id": _text(item.get("asset_id")),
        "role": role,
        "label": _text(item.get("label")),
        "filename": filename,
        "path": path,
        "url": url,
        "storage": _text(item.get("storage")),
        "extension_id": _text(item.get("extension_id")),
        "unit": _text(item.get("unit")),
        "portable_reference_available": bool(path or url),
        "backend_handoff_retained": False,
        "reupload_required": not bool(path or url),
    }


def _asset_roles(input_assets: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    assets = [_portable_asset(item) for item in (input_assets or []) if isinstance(item, Mapping)]
    source = next((item for item in assets if item.get("role") == "source"), {})
    mask = next((item for item in assets if item.get("role") == "mask"), {})
    return {
        "source": source,
        "mask": mask,
        "all": assets,
        "required_roles": ["source", "mask"],
        "missing_roles": [role for role, item in (("source", source), ("mask", mask)) if not item or not item.get("portable_reference_available")],
        "image_bytes_embedded": False,
    }


def _workflow_lineage(params: Mapping[str, Any], workflow_prompt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    roles = _mapping(params.get("lanpaint_node_roles"))
    prompt = _mapping(workflow_prompt)
    nodes: list[dict[str, Any]] = []
    for node_id, role in sorted(roles.items(), key=lambda item: str(item[0])):
        node = _mapping(prompt.get(str(node_id)))
        nodes.append({
            "node_id": str(node_id),
            "role": _text(role),
            "class_type": _text(node.get("class_type")),
        })
    return {
        "schema_version": "neo.image.lanpaint_node_lineage.v1",
        "nodes": nodes,
        "node_roles": deepcopy(roles),
        "node_count": len(nodes) if nodes else len(roles),
        "sampler_node_id": _text(params.get("_neo_sampler_node_id")),
        "lora": deepcopy(_mapping(params.get("lanpaint_lora_lineage"))),
    }


def _route_block(params: Mapping[str, Any], *, provider_id: str = "", route_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    route = _mapping(params.get("lanpaint_route"))
    ui_route = _mapping(_mapping(params.get("lanpaint_ui_state")).get("route"))
    capability = _mapping(params.get("lanpaint_capability_report"))
    snapshot_route = _mapping(_mapping(route_snapshot).get("route"))
    family = _text(route.get("family") or ui_route.get("family") or snapshot_route.get("family") or params.get("family"))
    loader = _text(route.get("loader") or ui_route.get("loader") or snapshot_route.get("loader") or params.get("loader"))
    mode = _text(ui_route.get("mode") or snapshot_route.get("mode") or params.get("mode") or "inpaint")
    engine = _text(route.get("engine") or ui_route.get("engine") or params.get("inpaint_engine") or "lanpaint")
    return {
        "provider_id": _text(provider_id or ui_route.get("provider_id") or snapshot_route.get("provider_id") or params.get("provider_id")),
        "family": family,
        "loader": loader,
        "mode": mode,
        "engine": engine,
        "route_key": _text(route.get("route_key") or ui_route.get("route_key") or _mapping(params.get("lanpaint_lora_route")).get("route_key")),
        "variant": _text(route.get("variant")),
        "policy_id": _text(route.get("policy_id")),
        "compiler_id": _text(route.get("compiler_id") or snapshot_route.get("compiler")),
        "graph_state": _text(route.get("graph_state")),
        "family_variant": _text(route.get("family_variant") or _mapping(params.get("z_image_lanpaint_family_variant")).get("id") or params.get("hidream_variant")),
        "stability_profile": _text(_mapping(params.get("z_image_lanpaint_stability_policy")).get("profile_id")),
        "hidream_i1_profile": _text(route.get("profile") or params.get("hidream_i1_profile")) if family == "hidream" else "",
        "sampler_contract": _text(_mapping(params.get("lanpaint_controls")).get("sampler_contract") or _mapping(_mapping(params.get("lanpaint_family_adapter")).get("sampler")).get("contract")),
        "dual_model_required": bool(family == "ideogram4" or params.get("_neo_ideogram4_dual_model_required")),
        "route_state": _text(capability.get("status") or ui_route.get("route_state") or snapshot_route.get("state") or "experimental_available"),
        "exact_route_required": True,
        "automatic_family_fallback": False,
        "automatic_engine_fallback": False,
    }


def build_lanpaint_replay_contract(
    params: Mapping[str, Any] | None,
    *,
    provider_id: str = "",
    input_assets: Sequence[Mapping[str, Any]] | None = None,
    workflow_prompt: Mapping[str, Any] | None = None,
    route_snapshot: Mapping[str, Any] | None = None,
    output_lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the portable Phase 11 replay, lineage and audit envelope.

    Provider upload aliases and image bytes are deliberately excluded. Neo-owned
    source/mask asset references are attached later by output persistence.
    """

    values = _mapping(params)
    if not _is_lanpaint(values):
        return {}

    route = _route_block(values, provider_id=provider_id, route_snapshot=route_snapshot)
    assets = _asset_roles(input_assets)
    capability = _mapping(values.get("lanpaint_capability_report"))
    ui_state = _mapping(values.get("lanpaint_ui_state"))
    family_adapter = _mapping(values.get("lanpaint_family_adapter"))
    if not family_adapter:
        family_adapter = get_lanpaint_family_adapter(
            route.get("family"),
            loader=route.get("loader"),
            provider_id=route.get("provider_id") or provider_id or "comfyui",
            mode=route.get("mode") or "inpaint",
            engine=route.get("engine") or "lanpaint",
            variant=route.get("variant") or "default",
        )
    lora_lineage = _mapping(values.get("lanpaint_lora_lineage"))
    requested_rows = _list_of_mappings(values.get("lanpaint_lora_requested_rows"))
    base_rows = _list_of_mappings(values.get("lanpaint_lora_base_graph_rows"))
    deferred_rows = _list_of_mappings(values.get("lanpaint_lora_deferred_rows"))

    contract: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE11_STATE,
        "route": route,
        "family_adapter": {
            "schema_id": _text(family_adapter.get("schema_id")),
            "adapter_id": _text(_mapping(family_adapter.get("identity")).get("adapter_id") or values.get("lanpaint_family_adapter_id")),
            "adapter_fingerprint": _text(family_adapter.get("adapter_fingerprint") or values.get("lanpaint_family_adapter_fingerprint")),
            "binding_state": _text(_mapping(family_adapter.get("binding")).get("state")),
            "graph_profile": _text(_mapping(family_adapter.get("binding")).get("graph_profile")),
            "exact_adapter_required": bool(family_adapter or values.get("lanpaint_family_adapter_fingerprint")),
        },
        "controls": deepcopy(_mapping(values.get("lanpaint_controls"))),
        "ui_state": deepcopy(ui_state),
        "selected_assets": _list_of_mappings(values.get("lanpaint_selected_assets")),
        "input_assets": assets,
        "mask": {
            "target": _text(values.get("lanpaint_mask_target") or "masked_area"),
            "asset_id": _text(_mapping(assets.get("mask")).get("asset_id")),
            "portable_reference_available": bool(_mapping(assets.get("mask")).get("portable_reference_available")),
            "reupload_required": bool(_mapping(assets.get("mask")).get("reupload_required", True)),
            "mask_bytes_embedded": False,
        },
        "source": {
            "asset_id": _text(_mapping(assets.get("source")).get("asset_id")),
            "portable_reference_available": bool(_mapping(assets.get("source")).get("portable_reference_available")),
            "reupload_required": bool(_mapping(assets.get("source")).get("reupload_required", True)),
        },
        "lora": {
            "mode": _text(values.get("lanpaint_lora_mode")),
            "requested_rows": requested_rows,
            "base_graph_rows": base_rows,
            "deferred_rows": deferred_rows,
            "lineage": deepcopy(lora_lineage),
            "restore_enabled": False,
            "revalidation_required": bool(base_rows or requested_rows),
            "restore_policy": "restore_rows_disabled_then_revalidate_exact_route_loader_and_lora_catalog",
        },
        "workflow_lineage": _workflow_lineage(values, workflow_prompt=workflow_prompt),
        "output_lineage": deepcopy(_mapping(output_lineage)),
        "fingerprints": {
            "route_contract": _text(values.get("lanpaint_contract_fingerprint")),
            "compile_plan": _text(values.get("lanpaint_compile_plan_fingerprint")),
            "ui_state": _text(values.get("lanpaint_ui_state_fingerprint")),
            "capability": _text(values.get("lanpaint_capability_fingerprint")),
            "family_adapter": _text(family_adapter.get("adapter_fingerprint") or values.get("lanpaint_family_adapter_fingerprint")),
        },
        "capability_snapshot": {
            "status": _text(capability.get("status")),
            "selectable": bool(capability.get("selectable")),
            "executable": bool(capability.get("executable")),
            "fingerprint": _text(capability.get("capability_fingerprint")),
            "blockers": deepcopy(capability.get("blockers") if isinstance(capability.get("blockers"), list) else []),
            "warnings": deepcopy(capability.get("warnings") if isinstance(capability.get("warnings"), list) else []),
        },
        "reconstruction": {
            "state": "ready_for_live_revalidation" if not assets.get("missing_roles") else "blocked_missing_portable_assets",
            "restorable_fields": [
                "route", "family_adapter", "controls", "ui_state", "selected_assets", "mask.target", "lora.rows",
                "route.hidream_i1_profile", "route.sampler_contract", "route.dual_model_required",
            ],
            "missing_asset_roles": list(assets.get("missing_roles") or []),
            "live_capability_revalidation_required": True,
            "selected_asset_catalog_revalidation_required": True,
            "provider_profile_revalidation_required": True,
            "auto_run": False,
        },
        "audit": {
            "provider_upload_aliases_retained": False,
            "image_bytes_retained": False,
            "exact_route_rebuild": True,
            "exact_family_adapter_rebuild": True,
            "physical_validation": "required",
        },
    }
    contract["replay_fingerprint"] = _fingerprint(contract)
    return contract


def refresh_lanpaint_replay_contract(
    params: Mapping[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    values = deepcopy(_mapping(params))
    contract = build_lanpaint_replay_contract(values, **kwargs)
    if contract:
        values["lanpaint_replay"] = contract
        values["lanpaint_replay_fingerprint"] = contract.get("replay_fingerprint")
        values["_neo_lanpaint_phase11_state"] = PHASE11_STATE
    return values



def validate_lanpaint_replay_request(
    params: Mapping[str, Any] | None,
    *,
    provider_id: str,
    family: str,
    loader: str,
    mode: str = "inpaint",
    engine: str = "lanpaint",
) -> list[str]:
    values = _mapping(params)
    contract = _mapping(values.get("lanpaint_replay"))
    if not contract:
        return []
    if contract.get("schema_id") != SCHEMA_ID:
        return ["LanPaint replay contract schema is unsupported or missing."]
    route = _mapping(contract.get("route"))
    expected = {
        "provider_id": _text(provider_id),
        "family": _text(family),
        "loader": _text(loader),
        "mode": _text(mode),
        "engine": _text(engine),
    }
    errors: list[str] = []
    for key, expected_value in expected.items():
        recorded = _text(route.get(key))
        if key == "provider_id" and not recorded:
            continue
        if recorded and recorded != expected_value:
            errors.append(f"LanPaint replay exact-route mismatch for {key}: recorded {recorded}, selected {expected_value}.")
    recorded_adapter = _mapping(contract.get("family_adapter"))
    recorded_adapter_fingerprint = _text(recorded_adapter.get("adapter_fingerprint"))
    if recorded_adapter_fingerprint:
        current_adapter = get_lanpaint_family_adapter(
            family,
            loader=loader,
            provider_id=provider_id,
            mode=mode,
            engine=engine,
        )
        current_fingerprint = _text(current_adapter.get("adapter_fingerprint"))
        if current_fingerprint and current_fingerprint != recorded_adapter_fingerprint:
            errors.append(
                "LanPaint replay family-adapter fingerprint no longer matches the selected route; rebuild and revalidate the replay draft."
            )
    replay_fingerprint = _text(contract.get("replay_fingerprint"))
    if replay_fingerprint and replay_fingerprint != _fingerprint(contract):
        errors.append("LanPaint replay fingerprint does not match the supplied replay contract.")
    return errors

def lanpaint_replay_schema_payload() -> dict[str, Any]:
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE11_STATE,
        "rules": [
            "Replay requires the exact recorded provider, family, loader, mode and engine route.",
            "Neo-owned source and mask references are portable; Comfy upload aliases and image bytes are not.",
            "LoRA rows restore disabled and require live route, node and catalog revalidation.",
            "Capability and selected-model catalogs are revalidated before queue submission.",
            "Replay never auto-runs and never silently substitutes another LanPaint family or engine.",
        ],
    }


__all__ = [
    "AUTHORITY",
    "PHASE11_STATE",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "build_lanpaint_replay_contract",
    "lanpaint_replay_schema_payload",
    "refresh_lanpaint_replay_contract",
    "validate_lanpaint_replay_request",
]
