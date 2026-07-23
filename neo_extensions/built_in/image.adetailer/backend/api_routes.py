from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .model_catalog import download_sam_model, list_detailer_models
from .detection_preview import preview_detailer_detections
from .preset_store import (
    delete_user_preset,
    list_user_presets,
    load_draft,
    save_draft,
    save_user_preset,
    set_default_preset,
)


def _parse_settings(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Invalid settings JSON: {exc}') from exc


def _public_model_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    clean["comfy_root"] = "ComfyUI"
    clean["models_root"] = "ComfyUI/models"
    clean["ultralytics_dir"] = "ComfyUI/models/ultralytics"
    clean["bbox_dir"] = "ComfyUI/models/ultralytics/bbox"
    clean["segm_dir"] = "ComfyUI/models/ultralytics/segm"
    clean["adetailer_dir"] = "ComfyUI/models/adetailer"
    clean["onnx_dir"] = "ComfyUI/models/onnx"
    clean["sam_dir"] = "ComfyUI/models/sams"
    clean.pop("custom_detector_root", None)
    clean.pop("custom_sam_root", None)
    clean.pop("configured_models_root", None)
    clean.pop("configured_comfy_root", None)
    clean.pop("resolved_models_root", None)
    clean.pop("resolved_comfy_root", None)
    clean.pop("_comfy_path_authority", None)
    clean["path_policy"] = "absolute_paths_server_side_only"
    return clean


def _model_catalog_payload(
    object_info_resolver: Callable[[str | None], dict[str, Any]] | None,
    backend_resolver: Callable[[str | None], dict[str, Any]] | None,
    profile_id: str = "",
) -> dict[str, Any]:
    backend: dict[str, Any] = {}
    if backend_resolver is not None:
        try:
            resolved_backend = backend_resolver(profile_id or None)
            backend = resolved_backend if isinstance(resolved_backend, dict) else {}
        except Exception:
            backend = {}
    backend_supplied_object_info = "object_info" in backend
    info = backend.get("object_info") if isinstance(backend.get("object_info"), dict) else {}
    if not info and not backend_supplied_object_info and object_info_resolver is not None:
        try:
            resolved = object_info_resolver(profile_id or None)
            if isinstance(resolved, dict) and resolved:
                info = resolved
        except Exception:
            pass
    try:
        return list_detailer_models(object_info=info, backend_details=backend)
    except TypeError:
        # Compatibility for injected test doubles and older local overrides.
        try:
            return list_detailer_models(object_info=info)
        except TypeError:
            return list_detailer_models()


def _public_sam_download(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    clean.pop("path", None)
    clean.pop("target_root", None)
    clean["target_role"] = "ComfyUI/models/sams"
    return clean


def create_adetailer_api_router(*, object_info_resolver: Callable[[str | None], dict[str, Any]] | None = None, backend_resolver: Callable[[str | None], dict[str, Any]] | None = None) -> APIRouter:
    router = APIRouter(prefix='/api/extensions/adetailer', tags=['adetailer'])

    @router.get('/models')
    def models(profile_id: str = '') -> dict:
        return _public_model_catalog(_model_catalog_payload(object_info_resolver, backend_resolver, profile_id))

    @router.get('/presets')
    def presets() -> dict:
        return list_user_presets()

    @router.post('/presets')
    def save_preset(payload: dict) -> dict:
        try:
            return save_user_preset(str(payload.get('name') or ''), payload.get('payload') or payload.get('block') or {}, preset_id=payload.get('preset_id'), make_default=bool(payload.get('make_default')))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete('/presets/{preset_id}')
    def delete_preset(preset_id: str) -> dict:
        return delete_user_preset(preset_id)

    @router.post('/presets/{preset_id}/default')
    def default_preset(preset_id: str) -> dict:
        return set_default_preset(preset_id)

    @router.get('/draft')
    def get_draft() -> dict:
        return load_draft()

    @router.post('/draft')
    def put_draft(payload: dict) -> dict:
        return save_draft(payload.get('payload') or payload.get('block') or payload)

    @router.post('/download-sam')
    def download_sam(payload: dict) -> dict:
        try:
            return _public_sam_download(download_sam_model(str(payload.get('model_key') or payload.get('key') or ''), ''))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post('/preview-detections')
    async def preview_detections(file: UploadFile = File(...), settings: str = Form('{}')) -> dict:
        payload = _parse_settings(settings)
        raw = await file.read()
        try:
            return preview_detailer_detections(raw, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router


def register_adetailer_api_routes(
    app: Any,
    *,
    object_info_resolver: Callable[[str | None], dict[str, Any]] | None = None,
    backend_resolver: Callable[[str | None], dict[str, Any]] | None = None,
) -> APIRouter:
    router = create_adetailer_api_router(object_info_resolver=object_info_resolver, backend_resolver=backend_resolver)
    app.include_router(router)

    # V1-compatible aliases so migrated UI snippets and bookmarks continue to work.
    alias = APIRouter(prefix='/api/generation', tags=['adetailer_v1_alias'])

    @alias.get('/detailer-models')
    def v1_models(profile_id: str = '') -> dict:
        return _public_model_catalog(_model_catalog_payload(object_info_resolver, backend_resolver, profile_id))

    @alias.get('/detailer-presets')
    def v1_presets() -> dict:
        return list_user_presets()

    @alias.post('/detailer-presets')
    def v1_save_preset(payload: dict) -> dict:
        try:
            return save_user_preset(str(payload.get('name') or ''), payload.get('payload') or payload.get('block') or {}, preset_id=payload.get('preset_id'), make_default=bool(payload.get('make_default')))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @alias.delete('/detailer-presets/{preset_id}')
    def v1_delete_preset(preset_id: str) -> dict:
        return delete_user_preset(preset_id)

    @alias.get('/detailer-draft')
    def v1_get_draft() -> dict:
        return load_draft()

    @alias.post('/detailer-draft')
    def v1_put_draft(payload: dict) -> dict:
        return save_draft(payload.get('payload') or payload.get('block') or payload)

    @alias.post('/detailer-download-sam')
    def v1_download_sam(payload: dict) -> dict:
        try:
            return _public_sam_download(download_sam_model(str(payload.get('model_key') or payload.get('key') or ''), ''))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @alias.post('/detailer-preview-detections')
    async def v1_preview_detections(file: UploadFile = File(...), settings: str = Form('{}')) -> dict:
        payload = _parse_settings(settings)
        raw = await file.read()
        try:
            return preview_detailer_detections(raw, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    app.include_router(alias)
    return router
