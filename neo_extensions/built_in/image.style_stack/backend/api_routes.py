"""API routes for the built-in Style Stack extension.

Phase D exposes the V1-parity CSV store through V2-native extension routes.
It intentionally does not merge prompts, patch provider workflows, or mount UI.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from .style_store import (
    CSV_FIELDS,
    delete_generation_style,
    duplicate_generation_style,
    export_generation_styles_path,
    import_generation_styles_csv,
    load_generation_styles,
    save_generation_styles,
    upsert_generation_style,
)


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": bool(result.ok),
        "styles": result.styles,
        "count": len(result.styles),
        "path": result.path,
        "message": result.message,
        "encoding": result.encoding,
        "csv_fields": list(CSV_FIELDS),
        "sync": dict(getattr(result, "sync", {}) or {}),
    }


def _style_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else payload
    return {
        "name": str(style.get("name") or ""),
        "prompt": str(style.get("prompt") or ""),
        "negative_prompt": str(style.get("negative_prompt") or ""),
    }


def _name_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("style_name") or "")


def create_style_stack_api_router(root: str | Path | None = None) -> APIRouter:
    """Create V2-native Style Stack API routes.

    Routes are intentionally narrow and map one-to-one to the Phase C CSV store.
    The route namespace is `/api/extensions/style_stack/styles`.
    """

    router = APIRouter(prefix="/api/extensions/style_stack", tags=["style_stack"])

    @router.get("/styles")
    def styles(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return _result_payload(load_generation_styles(root))

    @router.post("/styles/save")
    def save_style(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _result_payload(upsert_generation_style(_style_from_payload(payload), root))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/styles/save-all")
    def save_all_styles(payload: dict[str, Any]) -> dict[str, Any]:
        styles_payload = payload.get("styles") if isinstance(payload, dict) else None
        if not isinstance(styles_payload, list):
            raise HTTPException(status_code=400, detail="styles must be a list")
        try:
            return _result_payload(save_generation_styles(styles_payload, root, track_bundled_removals=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/styles/delete")
    def delete_style(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _result_payload(delete_generation_style(_name_from_payload(payload), root))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/styles/duplicate")
    def duplicate_style(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _result_payload(duplicate_generation_style(_name_from_payload(payload), root))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/styles/import")
    async def import_styles(file: UploadFile = File(...), mode: str = "merge") -> dict[str, Any]:
        safe_name = Path(file.filename or "generation_styles_import.csv").name
        if not safe_name.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="Style Stack import requires a .csv file")
        with TemporaryDirectory(prefix="neo_style_stack_import_") as temp_dir:
            temp_path = Path(temp_dir) / safe_name
            temp_path.write_bytes(await file.read())
            try:
                return _result_payload(import_generation_styles_csv(temp_path, root, mode=mode))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/styles/export")
    def export_styles() -> FileResponse:
        path = export_generation_styles_path(root)
        return FileResponse(
            str(path),
            media_type="text/csv",
            filename="generation_styles_export.csv",
        )

    return router


def register_style_stack_api_routes(app: Any, root: str | Path | None = None) -> APIRouter:
    router = create_style_stack_api_router(root)
    app.include_router(router)
    return router
