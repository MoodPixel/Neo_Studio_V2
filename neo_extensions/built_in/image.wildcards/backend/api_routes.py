"""API routes for the built-in Wildcards extension.

Phase G keeps the API/storage routes V2-native and activates seeded preview
resolution through the same backend resolver used by generation jobs. Preview
resolution is still non-persistent: it returns effective prompt variants without
writing generation state.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .resolver import resolve_wildcard_text
from .wildcard_store import (
    SUPPORTED_WILDCARD_EXTENSIONS,
    export_wildcard_pack,
    import_wildcard_pack,
    list_wildcard_files,
    load_wildcard_values,
    resolve_wildcard_root,
    save_wildcard_values_file,
)


def _root_from_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("root") or payload.get("wildcard_root") or "")


def _token_from_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("token") or payload.get("selected_token") or payload.get("name") or "")


def _values_from_payload(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("values")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return []


def _entries_payload(root: str | Path | None = None, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    entries = [entry.to_dict() for entry in list_wildcard_files(root, repo_root=repo_root)]
    library_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    return {
        "ok": True,
        "root": str(library_root),
        "entries": entries,
        "count": len(entries),
        "supported_extensions": list(SUPPORTED_WILDCARD_EXTENSIONS),
        "runtime_resolution": False,
        "phase": "D-api-routes",
    }


def _delete_wildcard_file(token: str, root: str | Path | None = None, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    from .wildcard_store import find_wildcard_file

    clean = str(token or "").strip().strip("/").replace("\\", "/")
    if not clean:
        raise ValueError("Wildcard token is required.")
    fp = find_wildcard_file(clean, root, repo_root=repo_root)
    if fp is None:
        raise FileNotFoundError(f"Wildcard token not found: {clean}")
    fp.unlink()
    return _entries_payload(root, repo_root=repo_root) | {"deleted_token": clean}


def create_wildcards_api_router(repo_root: str | Path | None = None) -> APIRouter:
    """Create V2-native Wildcards API routes.

    Route namespace: ``/api/extensions/wildcards``.
    """

    router = APIRouter(prefix="/api/extensions/wildcards", tags=["wildcards"])

    @router.get("/files")
    def files(root: str = "") -> dict[str, Any]:
        return _entries_payload(root, repo_root=repo_root)

    @router.get("/values")
    def values(token: str, root: str = "") -> dict[str, Any]:
        values_payload = load_wildcard_values(token, root, repo_root=repo_root).to_dict()
        return {
            "ok": bool(values_payload.get("token")) and bool(values_payload.get("relative_path")),
            "root": str(resolve_wildcard_root(root, repo_root=repo_root, create=True)),
            "wildcard": values_payload,
            "values": values_payload.get("values", []),
            "count": values_payload.get("count", 0),
            "phase": "D-api-routes",
            "runtime_resolution": False,
        }

    @router.post("/save")
    def save(payload: dict[str, Any]) -> dict[str, Any]:
        token = _token_from_payload(payload)
        values_payload = _values_from_payload(payload)
        root = _root_from_payload(payload)
        extension = str(payload.get("extension") or ".txt") if isinstance(payload, dict) else ".txt"
        try:
            path = save_wildcard_values_file(token, values_payload, root, repo_root=repo_root, extension=extension)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        values_after = load_wildcard_values(token, root, repo_root=repo_root).to_dict()
        return {
            "ok": True,
            "saved_token": token,
            "path": str(path),
            "wildcard": values_after,
            "entries": _entries_payload(root, repo_root=repo_root)["entries"],
            "phase": "D-api-routes",
            "runtime_resolution": False,
        }

    @router.post("/delete")
    def delete(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _delete_wildcard_file(_token_from_payload(payload), _root_from_payload(payload), repo_root=repo_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/import")
    async def import_pack(file: UploadFile = File(...), mode: str = "merge", root: str = "") -> dict[str, Any]:
        safe_name = Path(file.filename or "wildcards_import.zip").name
        suffix = Path(safe_name).suffix.lower()
        if suffix != ".zip":
            raise HTTPException(status_code=400, detail="Wildcards import requires a .zip pack in Phase D.")
        with TemporaryDirectory(prefix="neo_wildcards_import_") as temp_dir:
            temp_path = Path(temp_dir) / safe_name
            temp_path.write_bytes(await file.read())
            try:
                return import_wildcard_pack(temp_path, root, repo_root=repo_root, mode=mode)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/export")
    def export(root: str = "") -> FileResponse:
        path = export_wildcard_pack(root, repo_root=repo_root)
        return FileResponse(str(path), media_type="application/zip", filename="wildcards_pack.zip")

    @router.post("/preview-resolve")
    def preview_resolve(payload: dict[str, Any]) -> dict[str, Any]:
        positive = str(payload.get("positive") or payload.get("positive_prompt") or "") if isinstance(payload, dict) else ""
        negative = str(payload.get("negative") or payload.get("negative_prompt") or "") if isinstance(payload, dict) else ""
        root = _root_from_payload(payload)
        preview_count = int((payload.get("preview_count") if isinstance(payload, dict) else 1) or 1)
        preview_count = max(1, min(preview_count, 10))
        seed = payload.get("seed", "") if isinstance(payload, dict) else ""
        max_passes = int((payload.get("max_passes") if isinstance(payload, dict) else 24) or 24)
        results = []
        for index in range(preview_count):
            pos = resolve_wildcard_text(positive, seed=seed, variant_offset=index * 2, max_passes=max_passes, root=root, repo_root=repo_root, channel="positive")
            neg = resolve_wildcard_text(negative, seed=seed, variant_offset=index * 2 + 1, max_passes=max_passes, root=root, repo_root=repo_root, channel="negative")
            resolved_tokens = []
            missing_tokens = []
            for item in list(pos.get("resolved_tokens") or []) + list(neg.get("resolved_tokens") or []):
                if item not in resolved_tokens:
                    resolved_tokens.append(item)
            for item in list(pos.get("missing_tokens") or []) + list(neg.get("missing_tokens") or []):
                if item not in missing_tokens:
                    missing_tokens.append(item)
            results.append(
                {
                    "index": index,
                    "variant_offset": index,
                    "positive": pos.get("effective", positive),
                    "negative": neg.get("effective", negative),
                    "source_positive": positive,
                    "source_negative": negative,
                    "changed": bool(pos.get("changed") or neg.get("changed")),
                    "resolved_tokens": resolved_tokens,
                    "missing_tokens": missing_tokens,
                    "positive_resolution": pos,
                    "negative_resolution": neg,
                    "status": "resolved_phase_g_backend_resolver",
                }
            )
        return {
            "ok": True,
            "phase": "G-backend-resolver-hook",
            "runtime_resolution": True,
            "resolver_status": "implemented_phase_g",
            "results": results,
            "count": len(results),
        }

    return router


def register_wildcards_api_routes(app: Any, repo_root: str | Path | None = None) -> APIRouter:
    router = create_wildcards_api_router(repo_root)
    app.include_router(router)
    return router
