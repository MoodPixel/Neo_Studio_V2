from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from .node_discovery import discover_extra_model_path_inputs, inspect_nodes, merge_model_inputs
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
    node_status = inspect_nodes(object_info)
    filesystem_inputs = discover_extra_model_path_inputs(backend_details)
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
            "extra_model_paths": filesystem_inputs,
        },
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
        object_info = object_info_resolver(profile_id)
        return build_ip_adapter_node_status(
            object_info=object_info,
            backend_details=backend_details,
            backend=backend,
            family=family,
            loader=loader,
            workflow_mode="generate" if workflow_mode == "txt2img" else workflow_mode,
        )
