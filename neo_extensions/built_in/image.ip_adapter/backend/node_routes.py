from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from .node_discovery import discover_model_path_catalog, inspect_nodes, merge_model_inputs
from .support_matrix import route_reason, route_state

EXTENSION_ID = "image.ip_adapter"


def _provider_id_from_backend(backend: str) -> str:
    value = (backend or "").strip().lower()
    if value == "comfy":
        return "comfyui"
    if value in {"comfyui", "comfyui_portable"}:
        return value
    return value or "unknown"


def build_ip_adapter_node_status(
    *,
    object_info: Any = None,
    backend_details: dict[str, Any] | None = None,
    backend: str = "comfy",
    family: str = "sdxl",
    loader: str = "checkpoint",
    workflow_mode: str = "generate",
) -> dict[str, Any]:
    """Return UI-safe Comfy node readiness for the built-in IP Adapter extension."""
    backend_details = backend_details or {}
    provider_id = _provider_id_from_backend(str(backend_details.get("provider_id") or backend))
    matrix_state = route_state(provider_id, family, loader, workflow_mode)
    if provider_id == "forge":
        snapshot = backend_details.get("forge_admin") if isinstance(backend_details.get("forge_admin"), dict) else {}
        extension_caps = snapshot.get("extension_capabilities") if isinstance(snapshot.get("extension_capabilities"), dict) else {}
        capability = extension_caps.get("ip_adapter") if isinstance(extension_caps.get("ip_adapter"), dict) else {}
        family_key = str(family or "").lower()
        family_rows = capability.get("models_by_family") if isinstance(capability.get("models_by_family"), dict) else {}
        faceid_family_rows = capability.get("faceid_models_by_family") if isinstance(capability.get("faceid_models_by_family"), dict) else {}
        model_rows = family_rows.get(family_key) if isinstance(family_rows.get(family_key), list) else []
        faceid_rows = faceid_family_rows.get(family_key) if isinstance(faceid_family_rows.get(family_key), list) else []
        model_names = [str(item.get("catalog_name") or "") for item in model_rows if isinstance(item, dict) and str(item.get("catalog_name") or "").strip()]
        faceid_names = [str(item.get("catalog_name") or "") for item in faceid_rows if isinstance(item, dict) and str(item.get("catalog_name") or "").strip()]
        preprocessors = [str(item) for item in capability.get("preprocessors") or [] if str(item or "").strip()]
        faceid_preprocessors = [str(item) for item in capability.get("faceid_preprocessors") or [] if str(item or "").strip()]
        route_active = matrix_state in {"available", "experimental_available"}
        standard_available = bool(route_active and capability.get("standard_available", capability.get("available")) and model_names)
        faceid_available = bool(route_active and capability.get("faceid_available") and faceid_names)
        any_available = standard_available or faceid_available
        if not route_active:
            readiness_state = matrix_state
            summary = route_reason(matrix_state)
        elif not capability.get("available"):
            readiness_state = "provider_gated"
            summary = str(capability.get("reason") or "Forge IP-Adapter capability is unavailable.")
        elif not any_available:
            readiness_state = "provider_gated"
            summary = f"Forge exposes no compatible IP-Adapter or FaceID models for {family}."
        elif standard_available and faceid_available:
            readiness_state = "ready"
            summary = f"Forge Standard IP-Adapter and FaceID are ready for {family.upper()}."
        elif standard_available and not capability.get("faceid_detected"):
            readiness_state = "ready"
            summary = f"Forge Standard IP-Adapter is ready for {family.upper()}."
        else:
            readiness_state = "partial"
            summary = (
                f"Forge FaceID is ready for {family.upper()}, but Standard IP-Adapter is unavailable."
                if faceid_available
                else f"Forge Standard IP-Adapter is ready for {family.upper()}, but discovered FaceID models do not have a compatible live preprocessor."
            )
        missing_standard = [] if standard_available else ["compatible Forge IP-Adapter model/preprocessor pair"]
        missing_faceid = [] if faceid_available else ["compatible Forge FaceID/InstantID model and InsightFace preprocessor pair"]
        return {
            "ok": readiness_state in {"ready", "partial"},
            "extension_id": EXTENSION_ID,
            "schema": "neo.image.ip_adapter.node_status.v1",
            "profile_id": backend_details.get("profile_id") or "",
            "provider_id": "forge",
            "route": {"backend": "forge", "family": family, "loader": loader, "workflow_mode": workflow_mode, "route_state": matrix_state},
            "readiness_state": readiness_state,
            "summary": summary,
            "standard_available": standard_available,
            "faceid_available": faceid_available,
            "instantid_available": bool(route_active and capability.get("instantid_available")),
            "image_batch_available": False,
            "missing": {"standard": missing_standard, "faceid": missing_faceid, "optional": ["multi-reference per unit"]},
            "required": {
                "standard": ["Forge Integrated ControlNet", "IP-Adapter model", "matching IP-Adapter preprocessor"],
                "faceid": ["Forge Integrated ControlNet", "FaceID/InstantID model", "matching InsightFace preprocessor"],
            },
            "optional": [],
            "available_nodes": [],
            "model_inputs": {"ip_adapter": model_names, "ip_adapter_faceid": faceid_names, "clip_vision": sorted(set(preprocessors + faceid_preprocessors))},
            "model_input_sources": {"forge_controlnet_catalog": True},
            "model_catalog_diagnostics": {
                "contract": str(capability.get("contract") or ""),
                "faceid_detected": bool(capability.get("faceid_detected")),
                "faceid_available": faceid_available,
                "instantid_available": bool(capability.get("instantid_available")),
                "shared_controlnet_unit_pool": True,
                "faceid_authority": "live_model_and_preprocessor_pair_required",
            },
            "contract": str(capability.get("contract") or ""),
            "available_modes": [str(item) for item in capability.get("available_modes") or []],
            "unit_slots_by_mode": dict(capability.get("unit_slots_by_mode") or {}),
            "max_units": int(capability.get("max_units") or max([int(value or 0) for value in (capability.get("unit_slots_by_mode") or {}).values()] or [0])),
            "shared_controlnet_unit_pool": True,
            "unknown_object_info": False,
        }
    node_status = inspect_nodes(object_info)
    path_catalog = discover_model_path_catalog(backend_details)
    filesystem_inputs = path_catalog.get("model_inputs") if isinstance(path_catalog.get("model_inputs"), dict) else {}
    model_inputs = merge_model_inputs(node_status.get("model_inputs") or {}, filesystem_inputs)
    missing: dict[str, list[str]] = {
        "standard": list(node_status.get("standard_missing") or []),
        "faceid": list(node_status.get("faceid_missing") or []),
        "optional": [] if node_status.get("image_batch_available") else ["ImageBatch"],
    }
    standard_available = bool(node_status.get("standard_available"))
    faceid_available = bool(node_status.get("faceid_available"))
    any_required_ready = standard_available or faceid_available
    if matrix_state not in {"available", "experimental_available"}:
        readiness_state = matrix_state
        summary = route_reason(matrix_state)
    elif not any_required_ready:
        readiness_state = "provider_gated"
        summary = "IP Adapter custom nodes are missing in ComfyUI."
    elif not standard_available:
        readiness_state = "partial"
        summary = "FaceID nodes are available, but Standard IP Adapter nodes are missing."
    elif not faceid_available:
        readiness_state = "partial"
        summary = "Standard IP Adapter nodes are available, but FaceID nodes are missing."
    else:
        readiness_state = "ready"
        summary = "IP Adapter nodes are ready."
    return {
        "ok": readiness_state in {"ready", "partial"},
        "extension_id": EXTENSION_ID,
        "schema": "neo.image.ip_adapter.node_status.v1",
        "profile_id": backend_details.get("profile_id") or "",
        "provider_id": provider_id,
        "route": {"backend": provider_id, "family": family, "loader": loader, "workflow_mode": workflow_mode, "route_state": matrix_state},
        "readiness_state": readiness_state,
        "summary": summary,
        "standard_available": standard_available,
        "faceid_available": faceid_available,
        "image_batch_available": bool(node_status.get("image_batch_available")),
        "missing": missing,
        "required": {"standard": node_status.get("standard_required") or [], "faceid": node_status.get("faceid_required") or []},
        "optional": node_status.get("optional") or [],
        "available_nodes": node_status.get("available") or [],
        "model_inputs": model_inputs,
        "model_input_sources": {
            "object_info": node_status.get("model_inputs") or {},
            **dict(path_catalog.get("sources") or {}),
        },
        "model_catalog_diagnostics": dict(path_catalog.get("diagnostics") or {}),
        "unknown_object_info": bool(node_status.get("unknown_object_info")),
    }


def register_ip_adapter_node_routes(
    app: FastAPI,
    *,
    object_info_resolver: Callable[[str | None], Any],
    backend_resolver: Callable[[str | None], dict[str, Any]] | None = None,
) -> None:
    @app.get("/api/image/ip-adapter/node-status")
    def ip_adapter_node_status(
        profile_id: str | None = None,
        backend: str = "comfy",
        family: str = "sdxl",
        loader: str = "checkpoint",
        workflow_mode: str = "generate",
    ) -> dict[str, Any]:
        backend_details = backend_resolver(profile_id) if backend_resolver else {}
        backend_supplied_object_info = "object_info" in backend_details
        object_info = backend_details.get("object_info") if isinstance(backend_details.get("object_info"), dict) else {}
        if not object_info and not backend_supplied_object_info:
            object_info = object_info_resolver(profile_id)
        return build_ip_adapter_node_status(
            object_info=object_info,
            backend_details=backend_details,
            backend=backend,
            family=family,
            loader=loader,
            workflow_mode="generate" if workflow_mode == "txt2img" else workflow_mode,
        )
