from __future__ import annotations

import json
from typing import Any

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


def create_adetailer_api_router() -> APIRouter:
    router = APIRouter(prefix='/api/extensions/adetailer', tags=['adetailer'])

    @router.get('/models')
    def models(detector_root: str = '', sam_root: str = '') -> dict:
        return list_detailer_models(detector_root=detector_root, sam_root=sam_root)

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
            return download_sam_model(str(payload.get('model_key') or payload.get('key') or ''), str(payload.get('target_root') or ''))
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


def register_adetailer_api_routes(app: Any) -> APIRouter:
    router = create_adetailer_api_router()
    app.include_router(router)

    # V1-compatible aliases so migrated UI snippets and bookmarks continue to work.
    alias = APIRouter(prefix='/api/generation', tags=['adetailer_v1_alias'])

    @alias.get('/detailer-models')
    def v1_models(detector_root: str = '', sam_root: str = '') -> dict:
        return list_detailer_models(detector_root=detector_root, sam_root=sam_root)

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
            return download_sam_model(str(payload.get('model_key') or payload.get('key') or ''), str(payload.get('target_root') or ''))
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
