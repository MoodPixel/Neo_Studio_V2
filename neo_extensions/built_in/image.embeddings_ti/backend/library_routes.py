from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .civitai_import import fetch_civitai_payload, import_civitai_into_record, is_textual_inversion_payload, normalize_textual_inversion_civitai_payload, parse_civitai_url
from .library_scan import scan_embeddings_folder
from .library_schema import normalize_record, token_for_name, utc_now_iso
from .library_store import delete_record, find_record, load_records, merge_catalog_records, save_records, upsert_record
from .preview_cache import cache_preview_urls, normalize_preview_paths, preview_file_response


def route_specs() -> list[dict[str, Any]]:
    return [
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/routes", "purpose": "List extension-owned Embeddings/TI library routes."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/status", "purpose": "Return saved/scanned/provider Embeddings/TI library status."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/catalog", "purpose": "Return provider catalog bridge records when available."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/browser", "purpose": "Return saved Embeddings/TI records filtered by query."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/record", "purpose": "Return one Embeddings/TI record by id/token/name."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/resolve", "purpose": "Resolve a dropdown/provider/local query into a record."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/insert-token", "purpose": "Return a prompt token/chip payload for a record."},
        {"method": "GET", "path": "/api/extensions/embeddings_ti/library/preview-file", "purpose": "Serve cached/local preview image files."},
        {"method": "POST", "path": "/api/extensions/embeddings_ti/library/scan", "purpose": "Scan a local embeddings folder and persist records."},
        {"method": "POST", "path": "/api/extensions/embeddings_ti/library/save", "purpose": "Save or update one Embeddings/TI metadata record."},
        {"method": "POST", "path": "/api/extensions/embeddings_ti/library/civitai-import", "purpose": "Pull CivitAI Textual Inversion metadata/previews into a record."},
        {"method": "POST", "path": "/api/extensions/embeddings_ti/library/set-primary-preview", "purpose": "Set one preview as the primary image."},
        {"method": "POST", "path": "/api/extensions/embeddings_ti/library/delete", "purpose": "Delete one saved Embeddings/TI metadata record."},
    ]


def _filter_records(records: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    q = str(query or "").strip().casefold()
    if not q:
        return records
    fields = ("name", "catalog_name", "token", "file", "rel", "base_model", "notes", "category")
    return [
        item for item in records
        if any(q in str(item.get(key) or "").casefold() for key in fields)
        or any(q in str(token or "").casefold() for token in (item.get("trigger_words") or []) + (item.get("keywords") or []) + (item.get("negative_keywords") or []))
    ]


def catalog_payload(root: str | Path, *, catalog_embeddings: list[str] | None = None, query: str = "") -> dict[str, Any]:
    saved = load_records(root)
    records = merge_catalog_records(saved, catalog_embeddings or [])
    filtered = _filter_records(records, query)
    return {
        "ok": True,
        "schema_version": "neo.embeddings_ti.library.catalog.v2",
        "source": "saved_plus_provider_embedding_catalog",
        "catalog_count": len(catalog_embeddings or []),
        "available_count": len([item for item in records if item.get("catalog_available")]),
        "count": len(filtered),
        "total_count": len(records),
        "records": filtered,
        "route_specs": route_specs(),
    }


def browser_payload(root: str | Path, *, catalog_embeddings: list[str] | None = None, query: str = "") -> dict[str, Any]:
    payload = catalog_payload(root, catalog_embeddings=catalog_embeddings, query=query)
    payload["schema_version"] = "neo.embeddings_ti.library.browser.v2"
    return payload


def resolve_payload(root: str | Path, query: str, *, catalog_embeddings: list[str] | None = None) -> dict[str, Any]:
    record = find_record(root, query, catalog_embeddings=catalog_embeddings or [])
    if record is None:
        return {"ok": False, "error": "Embeddings/TI record not found.", "query": query}
    return {"ok": True, "record": record, "token": record.get("token") or token_for_name(record.get("name") or query)}


def record_payload(root: str | Path, record_id: str, *, catalog_embeddings: list[str] | None = None) -> dict[str, Any]:
    record = find_record(root, record_id, catalog_embeddings=catalog_embeddings or [])
    if record is None:
        return {"ok": False, "error": "Embeddings/TI record not found.", "record": None}
    return {"ok": True, "record": record}


def scan_payload(root: str | Path, payload: dict[str, Any] | None = None, *, catalog_embeddings: list[str] | None = None) -> dict[str, Any]:
    payload = payload or {}
    folder = payload.get("folder") or payload.get("folder_path") or ""
    result = scan_embeddings_folder(folder)
    if result.get("ok"):
        existing = merge_catalog_records(load_records(root), catalog_embeddings or [])
        by_id = {item["id"]: item for item in existing if item.get("id")}
        for item in result.get("records", []):
            normalized = normalize_record(item)
            # Preserve manual/CivitAI enrichment where possible, while refreshing path/token info.
            prior = by_id.get(normalized["id"])
            if prior:
                merged = {**normalized, **{key: value for key, value in prior.items() if value not in (None, "", [])}}
                # Local scan owns file/path/token freshness; manual metadata owns notes/triggers/previews.
                for key in ("file", "rel", "token", "source", "metadata_status"):
                    if normalized.get(key) not in (None, "", []):
                        merged[key] = normalized[key]
                by_id[normalized["id"]] = normalize_record(merged)
            else:
                by_id[normalized["id"]] = normalized
        records = save_records(root, list(by_id.values()))
        return {
            **result,
            "schema_version": "neo.embeddings_ti.library.scan.v2",
            "records": records,
            "count": len(records),
            "catalog_count": len(records),
            "message": f"Scanned {result.get('count', 0)} embedding file(s); library now has {len(records)} record(s).",
        }
    return {**result, "schema_version": "neo.embeddings_ti.library.scan.v2"}


def save_record_payload(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    record = payload.get("record") if isinstance(payload.get("record"), dict) else payload
    if not isinstance(record, dict):
        return {"ok": False, "error": "Record payload must be an object."}
    normalized = normalize_record({**record, "updated": utc_now_iso()})
    field_sources = normalized.setdefault("field_sources", {})
    for field in ("trigger_words", "keywords", "negative_keywords", "example_prompt", "notes", "base_model", "civitai_url", "default_target"):
        if field in record:
            field_sources[field] = "manual"
    if isinstance(record.get("remote_source"), dict) and record.get("remote_source", {}).get("url"):
        field_sources["remote_source.url"] = "manual"
    saved = upsert_record(root, normalized)
    return {"ok": True, "schema_version": "neo.embeddings_ti.library.save.v2", "record": saved, "message": "Embeddings/TI metadata saved."}


def _has_meaningful_civitai_data(incoming: dict[str, Any]) -> bool:
    for key in ("trigger_words", "keywords", "negative_keywords", "preview_images", "preview_urls", "prompt_options"):
        if incoming.get(key):
            return True
    for key in ("base_model", "example_prompt", "notes"):
        if str(incoming.get(key) or "").strip():
            return True
    return False


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields = ["trigger_words", "keywords", "negative_keywords", "preview_image", "preview_images", "base_model", "example_prompt", "prompt_options", "notes", "remote_source", "civitai_url", "default_target"]
    return [field for field in fields if before.get(field) != after.get(field)]


def civitai_import_payload(root: str | Path, payload: dict[str, Any], *, catalog_embeddings: list[str] | None = None, fetcher=None, preview_fetcher=None) -> dict[str, Any]:
    record_id = str(payload.get("record_id") or payload.get("id") or "").strip()
    url = str(payload.get("url") or payload.get("civitai_url") or "").strip()
    mode = str(payload.get("mode") or "fill_missing")
    selected_fields = payload.get("selected_fields") if isinstance(payload.get("selected_fields"), list) else None
    record = find_record(root, record_id, catalog_embeddings=catalog_embeddings or []) if record_id else None
    if record is None and isinstance(payload.get("record"), dict):
        record = normalize_record(payload.get("record") or {})
    if record is None:
        return {"ok": False, "error": "Embeddings/TI record is required before CivitAI import.", "parsed": parse_civitai_url(url)}
    fetched = fetch_civitai_payload(url, fetcher=fetcher)
    if not fetched.get("ok"):
        return fetched
    data = fetched.get("data") or {}
    if isinstance(data, dict) and not is_textual_inversion_payload(data):
        model = data.get("model") if isinstance(data.get("model"), dict) else {}
        model_type = model.get("type") or data.get("type") or data.get("modelType")
        return {"ok": False, "error": f"CivitAI model type is {model_type!r}, not Textual Inversion.", "fetched_url": fetched.get("url"), "parsed": fetched.get("parsed")}
    incoming = normalize_textual_inversion_civitai_payload(data)
    incoming.setdefault("remote_source", {})["url"] = url
    incoming["civitai_url"] = url
    if not _has_meaningful_civitai_data(incoming):
        return {"ok": False, "error": "CivitAI returned a response, but Neo could not find usable Textual Inversion metadata.", "fetched_url": fetched.get("url"), "parsed": fetched.get("parsed")}
    preview_urls = incoming.get("preview_urls") or incoming.get("preview_images") or []
    cache_result = cache_preview_urls(root, record.get("id") or record.get("token") or "unknown", preview_urls, fetcher=preview_fetcher)
    if cache_result.get("paths"):
        incoming["preview_images"] = normalize_preview_paths(cache_result.get("paths") or [])
        incoming["preview_image"] = incoming["preview_images"][0]
    elif preview_urls:
        incoming["preview_images"] = normalize_preview_paths(preview_urls)
        incoming["preview_image"] = incoming["preview_images"][0] if incoming["preview_images"] else ""
    before = normalize_record(record)
    merged = import_civitai_into_record(record, incoming, mode=mode, selected_fields=selected_fields)
    # Treat empty/unknown scaffold values as fillable for Embeddings/TI metadata.
    for key in ("base_model", "notes", "example_prompt", "default_target"):
        if incoming.get(key) not in (None, "", []) and str(merged.get(key) or "").strip().casefold() in {"", "unknown", "base unknown"}:
            merged[key] = incoming[key]
    for key in ("trigger_words", "keywords", "negative_keywords", "prompt_options"):
        if incoming.get(key) and not merged.get(key):
            merged[key] = incoming[key]
    incoming_previews = normalize_preview_paths((incoming.get("preview_images") or []) + (incoming.get("preview_urls") or []))
    if incoming_previews:
        merged_previews = normalize_preview_paths((merged.get("preview_images") or []) + incoming_previews)
        merged["preview_images"] = merged_previews
        merged["preview_image"] = merged.get("preview_image") or (merged_previews[0] if merged_previews else "")
        merged.setdefault("field_sources", {})["preview_images"] = "remote:civitai"
        merged.setdefault("field_sources", {})["preview_image"] = "remote:civitai"
    merged["updated"] = utc_now_iso()
    saved = upsert_record(root, merged)
    changed = _changed_fields(before, saved)
    return {
        "ok": True,
        "schema_version": "neo.embeddings_ti.library.civitai_import.v2",
        "record": saved,
        "fetched_url": fetched.get("url"),
        "preview_cache": cache_result,
        "mode": mode,
        "import_summary": {
            "incoming_trigger_words": len(incoming.get("trigger_words") or []),
            "incoming_keywords": len(incoming.get("keywords") or []),
            "incoming_negative_keywords": len(incoming.get("negative_keywords") or []),
            "incoming_prompts": len(incoming.get("prompt_options") or []) + (1 if incoming.get("example_prompt") else 0),
            "incoming_preview_urls": len(preview_urls or []),
            "cached_previews": len(cache_result.get("paths") or []),
            "changed_fields": changed,
            "changed_count": len(changed),
        },
        "message": "CivitAI Textual Inversion pull complete" if changed else "CivitAI returned metadata, but merge rules did not change this record.",
    }


def set_primary_preview_payload(root: str | Path, payload: dict[str, Any], *, catalog_embeddings: list[str] | None = None) -> dict[str, Any]:
    record_id = str(payload.get("record_id") or payload.get("id") or "").strip()
    preview = str(payload.get("preview") or payload.get("preview_image") or "").strip()
    record = find_record(root, record_id, catalog_embeddings=catalog_embeddings or [])
    if record is None:
        return {"ok": False, "error": "Embeddings/TI record not found."}
    previews = normalize_preview_paths([preview] + (record.get("preview_images") or []))
    record["preview_image"] = preview
    record["preview_images"] = previews
    record.setdefault("field_sources", {})["preview_image"] = "manual"
    return {"ok": True, "record": upsert_record(root, record)}


def delete_record_payload(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    return delete_record(root, str(payload.get("record_id") or payload.get("id") or ""))


def insert_token_payload(root: str | Path, record_id: str, *, catalog_embeddings: list[str] | None = None) -> dict[str, Any]:
    record = find_record(root, record_id, catalog_embeddings=catalog_embeddings or [])
    if record is None:
        return {"ok": False, "error": "Embeddings/TI record not found.", "text": "", "item": {}}
    token = record.get("token") or token_for_name(record.get("name") or record_id)
    item = {"token": token, "name": record.get("name") or token.replace("embedding:", ""), "strength": record.get("default_strength", 1), "target": record.get("default_target") or "negative_prompt", "source_record_id": record.get("id")}
    return {"ok": True, "record_id": record.get("id"), "text": token, "token": token, "item": item, "record": record}


def preview_payload(root: str | Path, path: str) -> dict[str, Any]:
    return preview_file_response(path, root=root)


def create_embeddings_ti_library_router(
    root: str | Path,
    *,
    catalog_resolver: Callable[[str | None], list[str]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/extensions/embeddings_ti/library", tags=["embeddings_ti_library"])

    def _catalog(profile_id: str | None = None) -> list[str]:
        if catalog_resolver is None:
            return []
        try:
            return catalog_resolver(profile_id)
        except Exception:  # noqa: BLE001 - saved library remains usable offline.
            return []

    @router.get("/routes")
    def embeddings_ti_library_routes() -> dict[str, Any]:
        return {"ok": True, "schema_version": "neo.embeddings_ti.library.routes.v2", "routes": route_specs()}

    @router.get("/status")
    def embeddings_ti_library_status(profile_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        result = browser_payload(root, catalog_embeddings=_catalog(profile_id), query=q or "")
        return {**result, "schema_version": "neo.embeddings_ti.library.status.v2", "store_ready": True}

    @router.get("/catalog")
    def embeddings_ti_library_catalog(profile_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        return catalog_payload(root, catalog_embeddings=_catalog(profile_id), query=q or "")

    @router.get("/browser")
    def embeddings_ti_library_browser(profile_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        return browser_payload(root, catalog_embeddings=_catalog(profile_id), query=q or "")

    @router.get("/record")
    def embeddings_ti_library_record(record_id: str, profile_id: str | None = None) -> dict[str, Any]:
        result = record_payload(root, record_id, catalog_embeddings=_catalog(profile_id))
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Embeddings/TI record not found."))
        return result

    @router.get("/resolve")
    def embeddings_ti_library_resolve(query: str, profile_id: str | None = None) -> dict[str, Any]:
        result = resolve_payload(root, query, catalog_embeddings=_catalog(profile_id))
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Embeddings/TI record not found."))
        return result

    @router.get("/insert-token")
    def embeddings_ti_library_insert_token(record_id: str, profile_id: str | None = None) -> dict[str, Any]:
        result = insert_token_payload(root, record_id, catalog_embeddings=_catalog(profile_id))
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Embeddings/TI record not found."))
        return result

    @router.get("/preview-file")
    def embeddings_ti_library_preview_file(path: str) -> FileResponse:
        result = preview_payload(root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Embeddings/TI preview not found."))
        return FileResponse(result["path"], media_type=result.get("media_type") or None)

    @router.post("/scan")
    def embeddings_ti_library_scan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        result = scan_payload(root, payload, catalog_embeddings=_catalog(payload.get("profile_id")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Embeddings folder scan failed."))
        return result

    @router.post("/save")
    def embeddings_ti_library_save(payload: dict[str, Any]) -> dict[str, Any]:
        result = save_record_payload(root, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Could not save Embeddings/TI metadata."))
        return result

    @router.post("/civitai-import")
    def embeddings_ti_library_civitai_import(payload: dict[str, Any]) -> dict[str, Any]:
        result = civitai_import_payload(root, payload, catalog_embeddings=_catalog(payload.get("profile_id")))
        if not result.get("ok"):
            detail = result.get("message") or result.get("error") or result.get("errors") or "CivitAI import failed."
            raise HTTPException(status_code=400, detail=detail)
        return result

    @router.post("/set-primary-preview")
    def embeddings_ti_library_set_primary_preview(payload: dict[str, Any]) -> dict[str, Any]:
        result = set_primary_preview_payload(root, payload, catalog_embeddings=_catalog(payload.get("profile_id")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Could not set primary preview."))
        return result

    @router.post("/delete")
    def embeddings_ti_library_delete(payload: dict[str, Any]) -> dict[str, Any]:
        result = delete_record_payload(root, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="Embeddings/TI record not found.")
        return result

    return router


def register_embeddings_ti_library_routes(
    app: Any,
    root: str | Path,
    *,
    catalog_resolver: Callable[[str | None], list[str]] | None = None,
) -> APIRouter:
    router = create_embeddings_ti_library_router(root, catalog_resolver=catalog_resolver)
    app.include_router(router)
    return router
