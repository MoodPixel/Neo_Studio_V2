from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .asset_contract import validate_asset_contract
from .map_preprocessors import batch_preview_payload, list_preprocessor_options, preview_map_payload
from .validation import validate_controlnet_payload


def route_specs() -> list[dict[str, str]]:
    return [
        {"method": "GET", "path": "/api/extensions/controlnet/maps/routes"},
        {"method": "GET", "path": "/api/extensions/controlnet/maps/status"},
        {"method": "GET", "path": "/api/extensions/controlnet/maps/preprocessors"},
        {"method": "POST", "path": "/api/extensions/controlnet/maps/validate-assets"},
        {"method": "POST", "path": "/api/extensions/controlnet/maps/validate"},
        {"method": "POST", "path": "/api/extensions/controlnet/maps/preview"},
        {"method": "POST", "path": "/api/extensions/controlnet/maps/batch-preview"},
        {"method": "GET", "path": "/api/extensions/controlnet/maps/file/{map_id}"},
    ]


def create_controlnet_map_router(
    root: str | Path,
    *,
    object_info_resolver: Callable[[str | None], dict[str, Any]] | None = None,
    backend_resolver: Callable[[str | None], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/extensions/controlnet/maps", tags=["controlnet_maps"])
    root = Path(root)

    def _backend(profile_id: str | None = None) -> dict[str, Any]:
        if backend_resolver is not None:
            try:
                payload = backend_resolver(profile_id)
                return payload if isinstance(payload, dict) else {}
            except Exception:  # noqa: BLE001 - map UI should degrade safely.
                return {}
        if object_info_resolver is None:
            return {}
        try:
            info = object_info_resolver(profile_id)
            return {"object_info": info if isinstance(info, dict) else {}}
        except Exception:  # noqa: BLE001 - map UI should degrade to provider-gated diagnostics.
            return {}

    def _object_info(profile_id: str | None = None) -> dict[str, Any]:
        payload = _backend(profile_id)
        return payload.get("object_info") if isinstance(payload.get("object_info"), dict) else payload if isinstance(payload, dict) and "ControlNetLoader" in payload else {}

    def _object_info_from_backend(payload: dict[str, Any]) -> dict[str, Any]:
        return payload.get("object_info") if isinstance(payload.get("object_info"), dict) else payload if "ControlNetLoader" in payload else {}

    @router.get("/routes")
    def controlnet_map_routes() -> dict[str, Any]:
        return {"ok": True, "schema_version": "neo.image.controlnet.map_routes.v1", "routes": route_specs()}

    @router.get("/status")
    def controlnet_map_status(profile_id: str | None = None) -> dict[str, Any]:
        backend = _backend(profile_id)
        options = list_preprocessor_options(_object_info_from_backend(backend), backend_details=backend)
        return {
            "ok": True,
            "schema_version": "neo.image.controlnet.map_status.v1",
            "profile_id": str(backend.get("profile_id") or profile_id or ""),
            "route_specs": route_specs(),
            **options,
        }

    @router.get("/preprocessors")
    def controlnet_preprocessors(profile_id: str | None = None) -> dict[str, Any]:
        backend = _backend(profile_id)
        return {
            "profile_id": str(backend.get("profile_id") or profile_id or ""),
            **list_preprocessor_options(_object_info_from_backend(backend), backend_details=backend),
        }

    @router.post("/validate-assets")
    def controlnet_validate_assets(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        units = payload.get("units") if isinstance(payload.get("units"), list) else payload.get("inputs", {}).get("units", []) if isinstance(payload.get("inputs"), dict) else []
        return validate_asset_contract(units, payload.get("assets") if isinstance(payload.get("assets"), dict) else {})

    @router.post("/validate")
    def controlnet_validate(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
        return validate_controlnet_payload(
            payload,
            backend=route.get("backend") or payload.get("backend") or "comfyui",
            family=route.get("family") or payload.get("family") or "sdxl",
            loader=route.get("loader") or payload.get("loader") or "checkpoint",
            workflow_mode=route.get("workflow_mode") or payload.get("workflow_mode") or "generate",
            object_info=_object_info(payload.get("profile_id")) or payload.get("object_info") or {},
            require_assets=bool(payload.get("require_assets", True)),
        )

    @router.post("/preview")
    def controlnet_preview(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profile_id = (payload or {}).get("profile_id")
        backend = _backend(profile_id)
        result = preview_map_payload(root, payload or {}, object_info=backend.get("object_info") if isinstance(backend.get("object_info"), dict) else _object_info(profile_id), runtime=backend)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("reason") or result)
        return result

    @router.post("/batch-preview")
    def controlnet_batch_preview(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profile_id = (payload or {}).get("profile_id")
        backend = _backend(profile_id)
        result = batch_preview_payload(root, payload or {}, object_info=backend.get("object_info") if isinstance(backend.get("object_info"), dict) else _object_info(profile_id), runtime=backend)
        if not result.get("ok"):
            # Keep the manifest visible to the UI, but use HTTP 400 for failed batch.
            raise HTTPException(status_code=400, detail=result)
        return result

    @router.get("/file/{map_id}")
    def controlnet_map_file(map_id: str) -> FileResponse:
        safe_name = Path(map_id).name
        path = root / "neo_data" / "controlnet_maps" / safe_name
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="ControlNet map not found.")
        return FileResponse(path)

    return router


def register_controlnet_map_routes(app: Any, root: str | Path, *, object_info_resolver: Callable[[str | None], dict[str, Any]] | None = None, backend_resolver: Callable[[str | None], dict[str, Any]] | None = None) -> APIRouter:
    router = create_controlnet_map_router(root, object_info_resolver=object_info_resolver, backend_resolver=backend_resolver)
    app.include_router(router)
    return router
