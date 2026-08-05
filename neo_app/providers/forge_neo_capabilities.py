from __future__ import annotations

from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.models.forge_neo_route_catalog import forge_route_authority_payload, forge_selectable_route_summary
from neo_app.providers.forge_admin import forge_models_for_backend_profile, load_forge_admin_cache
from neo_app.providers.forge_neo_loader_translation import forge_loader_translation_contract_payload
from neo_app.providers.forge_neo_extension_bridge import forge_generic_extension_bridge_contract_payload
from neo_app.providers.forge_neo_workflow_compilers import forge_workflow_compiler_contract_payload
from neo_app.providers.forge_neo_validation import forge_validation_contract_payload
from neo_app.providers.forge_neo_model_classification import (
    build_forge_live_route_intersection,
    ensure_forge_live_discovery,
)
from neo_app.providers.schema import (
    BackendCapabilityDiscoveryResult,
    BackendLoaderCapability,
    BackendRoleCapability,
)

FORGE_CAPABILITY_SCHEMA_ID = "neo.provider.forge_capabilities.v8"
_IMAGE_MODES = ("txt2img", "img2img", "inpaint", "outpaint", "edit")


def forge_snapshot_for_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    snapshot = runtime.get("forge_admin") if isinstance(runtime.get("forge_admin"), dict) else None
    if snapshot:
        return snapshot
    profile_id = str(profile.get("profile_id") or "forge_local")
    return load_forge_admin_cache(profile_id) or {}


def forge_discovered_models(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    snapshot = forge_snapshot_for_profile(profile)
    buckets = forge_models_for_backend_profile(snapshot) if snapshot else {}
    records: list[dict[str, Any]] = []
    for bucket, items in buckets.items():
        for item in items or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            records.append(
                {
                    **item,
                    "id": name,
                    "name": name,
                    "provider_id": "forge",
                    "bucket": bucket,
                }
            )
    return records


def _profile_enabled_modes(profile: dict[str, Any]) -> set[str]:
    flags = {
        **(profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}),
        **(profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}),
    }
    explicit = any(mode in flags for mode in _IMAGE_MODES)
    if not explicit:
        return set(_IMAGE_MODES)
    return {mode for mode in _IMAGE_MODES if bool(flags.get(mode))}


def _models_for_loader(classification: dict[str, Any], loader_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in classification.get("models") or []:
        if not isinstance(item, dict) or loader_id not in {str(value) for value in item.get("loader_candidates") or []}:
            continue
        output.append(item)
    return output


def _asset_names(records: list[dict[str, Any]]) -> list[str]:
    names = {
        str(item.get("name") or item.get("title") or item.get("model_name") or item.get("id") or "").strip()
        for item in records
        if isinstance(item, dict)
    }
    return sorted((name for name in names if name), key=str.casefold)


def _role_assets(classification: dict[str, Any], role_id: str) -> list[str]:
    inventory = classification.get("module_inventory") if isinstance(classification.get("module_inventory"), dict) else {}
    return [str(item) for item in inventory.get(role_id) or [] if str(item or "").strip()]


def _loader_live_available(intersection: dict[str, Any], loader_id: str) -> bool:
    return any(
        item.get("selectable") and str(item.get("loader") or "") == loader_id
        for item in intersection.get("routes") or []
        if isinstance(item, dict)
    )


def _loader_target_routes(intersection: dict[str, Any], loader_id: str) -> list[dict[str, Any]]:
    return [
        {
            "family": str(item.get("family") or ""),
            "mode": str(item.get("mode") or ""),
            "live_state": str(item.get("live_state") or ""),
            "assets_ready": bool(item.get("assets_ready")),
            "blockers": list(item.get("blockers") or []),
        }
        for item in intersection.get("routes") or []
        if isinstance(item, dict)
        and str(item.get("loader") or "") == loader_id
        and (item.get("exact_models") or item.get("ambiguous_models"))
    ]


def _module_role_capability(
    classification: dict[str, Any],
    role_id: str,
    *,
    backend_key: str = "forge_additional_modules",
) -> BackendRoleCapability:
    names = _role_assets(classification, role_id)
    return BackendRoleCapability(
        role_id=role_id,
        available=bool(names),
        backend_key=backend_key,
        backend_node="/sdapi/v1/sd-modules",
        assets={role_id: names},
        notes=["Forge additional modules are selected by portable model names; absolute backend paths are never persisted."],
    )


def _build_loader_capability(
    loader_id: str,
    *,
    classification: dict[str, Any],
    intersection: dict[str, Any],
    reachable: bool,
) -> BackendLoaderCapability | None:
    models = _models_for_loader(classification, loader_id)
    if not models and loader_id != "checkpoint":
        return None
    names = _asset_names(models)
    live_available = bool(reachable and names and _loader_live_available(intersection, loader_id))
    roles: dict[str, BackendRoleCapability] = {}

    if loader_id == "checkpoint":
        roles["checkpoint"] = BackendRoleCapability(
            role_id="checkpoint",
            available=bool(names),
            backend_key="sd_model_checkpoint",
            backend_node="/sdapi/v1/sd-models",
            assets={"checkpoints": names},
            notes=["Forge checkpoint selection is translated through override_settings.sd_model_checkpoint."],
        )
    else:
        roles["primary_model"] = BackendRoleCapability(
            role_id="primary_model",
            available=bool(names),
            backend_key="sd_model_checkpoint",
            backend_node="/sdapi/v1/sd-models",
            aliases=[loader_id],
            assets={"primary_models": names},
            notes=["The selected primary-model format is executable only when route authority, live assets, and a Phase 4 workflow compiler agree."],
        )
        discovered_roles = sorted(
            {
                str(role)
                for item in classification.get("modules") or []
                if isinstance(item, dict)
                for role in item.get("roles") or []
                if str(role or "").strip()
            }
        )
        for role_id in discovered_roles:
            roles[role_id] = _module_role_capability(classification, role_id)

    roles["source_image"] = BackendRoleCapability(
        role_id="source_image",
        available=reachable,
        backend_key="init_images",
        backend_node="/sdapi/v1/img2img",
        notes=["Neo-owned source images are base64 encoded at the provider boundary."],
    )
    roles["mask_image"] = BackendRoleCapability(
        role_id="mask_image",
        available=reachable,
        backend_key="mask",
        backend_node="/sdapi/v1/img2img",
        notes=["Inpaint masks are base64 encoded at the provider boundary."],
    )

    target_routes = _loader_target_routes(intersection, loader_id)
    warnings: list[str] = []
    if not reachable:
        warnings.append("Forge Admin API is not currently connected.")
    if names and not live_available:
        warnings.append("Models were classified for this loader, but no executable Neo Forge route currently intersects the selected profile.")
    return BackendLoaderCapability(
        loader_id=loader_id,
        available=live_available,
        roles=roles,
        assets={"primary_models": names, "checkpoints": names if loader_id == "checkpoint" else []},
        notes=[
            "Availability is the intersection of route authority, the selected Forge profile, live models/modules, settings, scripts, and API endpoints.",
            f"Discovered {len(target_routes)} live route candidate(s) for this loader; detailed blockers are published in live_route_intersection.",
        ],
        warnings=warnings,
    )


def forge_backend_capabilities(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    snapshot = forge_snapshot_for_profile(profile)
    status = str(snapshot.get("status") or (profile.get("runtime") or {}).get("status") or "offline")
    reachable = bool(snapshot.get("reachable")) and bool(snapshot.get("api_enabled", True))
    classification, _cached_intersection = ensure_forge_live_discovery(snapshot)
    live_intersection = build_forge_live_route_intersection(
        classification,
        enabled_modes=_profile_enabled_modes(profile),
    )

    loaders: dict[str, BackendLoaderCapability] = {}
    for loader_id in ("checkpoint", "diffusion_model", "gguf", "checkpoint_aio", "nunchaku"):
        loader = _build_loader_capability(
            loader_id,
            classification=classification,
            intersection=live_intersection,
            reachable=reachable,
        )
        if loader is not None:
            loaders[loader_id] = loader

    result = BackendCapabilityDiscoveryResult(
        provider_id="forge",
        backend="forge",
        discovery_version="4.0.0",
        discovery_status="available" if reachable else "offline",
        reachable=reachable,
        object_info_available=False,
        loaders=loaders,
        warnings=[] if reachable else ["Forge Admin API is not currently connected."],
        errors=[] if reachable else [f"Forge capability snapshot is {status}."],
    )
    payload = model_to_dict(result)
    payload["schema_id"] = FORGE_CAPABILITY_SCHEMA_ID
    payload["admin_snapshot_status"] = status
    payload["route_authority"] = forge_route_authority_payload()
    payload["loader_translation_contract"] = forge_loader_translation_contract_payload()
    payload["workflow_compiler_contract"] = forge_workflow_compiler_contract_payload()
    payload["validation_contract"] = forge_validation_contract_payload()
    payload["generic_extension_bridge_contract"] = forge_generic_extension_bridge_contract_payload()
    payload["generic_extension_bridge"] = snapshot.get("generic_extension_bridge") if isinstance(snapshot.get("generic_extension_bridge"), dict) else {}
    payload["authority_selectable_route_summary"] = forge_selectable_route_summary(
        enabled_modes=_profile_enabled_modes(profile)
    )
    payload["selectable_route_summary"] = live_intersection.get("selectable_summary") or {}
    payload["model_classification"] = classification
    payload["live_route_intersection"] = live_intersection
    payload["classification_policy"] = {
        "selected_profile_only": True,
        "generic_classic_checkpoints_remain_ambiguous": True,
        "nunchaku_is_not_gguf": True,
        "only_authority_backed_compilers_enable_modern_assets": True,
    }
    bridge = snapshot.get("bridge") if isinstance(snapshot.get("bridge"), dict) else {}
    bridge_selected = bool(bridge.get("selected"))
    payload["job_lifecycle"] = {
        "submission": "forge_bridge_durable_job" if bridge_selected else "durable_single_worker_per_profile",
        "progress": "/neo-api/v1/jobs/{job_id}" if bridge_selected else "/sdapi/v1/progress",
        "preview": "bridge_job_preview" if bridge_selected else "current_image",
        "cancel": "/neo-api/v1/jobs/{job_id}/cancel" if bridge_selected else "/sdapi/v1/interrupt",
        "history": "/neo-api/v1/history" if bridge_selected else "neo_local_registry",
        "output_handoff": "forge_bridge_result_spool_then_neo_data" if bridge_selected else "forge_response_spool_then_neo_data",
        "restart_recovery": "bridge_job_reattach" if bridge_selected else "queued_resume_and_explicit_orphan_requeue",
        "bridge": bridge,
    }
    extension_capabilities = snapshot.get("extension_capabilities") if isinstance(snapshot.get("extension_capabilities"), dict) else {}
    payload["extension_compatibility"] = {
        key: {
            "available": bool(value.get("available")),
            "contract": str(value.get("contract") or ""),
            "mode": str(value.get("mode") or ""),
            "available_modes": list(value.get("available_modes") or []),
        }
        for key, value in extension_capabilities.items()
        if isinstance(value, dict)
    }
    payload["execution_state"] = "bridge_lifecycle_ready" if bridge_selected else "execution_lifecycle_ready"
    return payload
