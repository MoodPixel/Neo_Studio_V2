from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .civitai_import import fetch_civitai_payload, import_civitai_into_record, normalize_civitai_payload, parse_civitai_url
from .library_scan import scan_comfy_lora_catalog, scan_lora_folder
from .library_schema import normalize_record, utc_now_iso
from .library_store import delete_record, find_record, load_records, merge_catalog_records, save_records, upsert_record
from .metadata_reader import infer_defaults_from_metadata, read_safetensors_metadata
from .comfy_metadata import fetch_comfy_lora_metadata
from .local_lora_paths import lora_path_resolution_payload, resolve_lora_file_path
from .catalog_bridge import catalog_bridge_payload, record_to_stack_row, resolve_catalog_record
from .preview_cache import cache_preview_urls, normalize_preview_paths, preview_file_response


def route_specs() -> list[dict[str, Any]]:
    return [
        {"method": "GET", "path": "/api/extensions/lora_stack/library/routes"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/status"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/catalog"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/resolve"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/browser"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/record"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/preview-file"},
        {"method": "GET", "path": "/api/extensions/lora_stack/library/insert-block"},
        {"method": "POST", "path": "/api/extensions/lora_stack/library/scan"},
        {"method": "POST", "path": "/api/extensions/lora_stack/library/save"},
        {"method": "POST", "path": "/api/extensions/lora_stack/library/civitai-import"},
        {"method": "POST", "path": "/api/extensions/lora_stack/library/set-primary-preview"},
        {"method": "POST", "path": "/api/extensions/lora_stack/library/delete"},
    ]


def _filter_records(records: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    q = str(query or "").strip().casefold()
    if not q:
        return records
    return [
        item for item in records
        if any(q in str(item.get(key) or "").casefold() for key in ("name", "catalog_name", "file", "category", "base_model", "notes"))
        or any(q in str(token or "").casefold() for token in (item.get("triggers") or []) + (item.get("keywords") or []))
    ]


def browser_payload(root: str | Path, *, catalog_loras: list[str] | None = None, query: str = "") -> dict[str, Any]:
    saved = load_records(root)
    records = merge_catalog_records(saved, catalog_loras or [])
    filtered = _filter_records(records, query)
    bridge = catalog_bridge_payload(records, catalog_loras or [])
    return {
        "ok": True,
        "schema_version": "neo.lora_stack.library.browser.v1",
        "source": "saved_plus_comfy_lora_loader",
        "catalog_bridge": {key: value for key, value in bridge.items() if key != "records"},
        "catalog_count": bridge["catalog_count"],
        "available_count": bridge["available_count"],
        "count": len(filtered),
        "total_count": len(records),
        "records": filtered,
        "route_specs": route_specs(),
    }


def catalog_payload(root: str | Path, *, catalog_loras: list[str] | None = None, query: str = "") -> dict[str, Any]:
    saved = load_records(root)
    bridge = catalog_bridge_payload(saved, catalog_loras or [])
    records = _filter_records(bridge["records"], query)
    bridge = {**bridge, "records": records, "count": len(records)}
    return {"ok": True, **bridge}


def resolve_payload(root: str | Path, query: str, *, catalog_loras: list[str] | None = None, metadata_resolver: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    saved = load_records(root)
    record = resolve_catalog_record(saved, query, catalog_loras or [])
    if record is None:
        return {"ok": False, "error": "LoRA catalog record not found.", "query": query}
    row = record_to_stack_row(record)
    enriched = enrich_record_from_local_metadata(root, record, metadata_resolver=metadata_resolver)
    if enriched != record:
        upsert_record(root, enriched)
    return {"ok": True, "record": enriched, "stack_row": row}


def record_payload(root: str | Path, record_id: str, *, catalog_loras: list[str] | None = None, metadata_resolver: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    record = find_record(root, record_id, catalog_loras=catalog_loras or [])
    if record is None:
        return {"ok": False, "error": "LoRA record not found.", "record": None}
    enriched = enrich_record_from_local_metadata(root, record, metadata_resolver=metadata_resolver)
    if enriched != record:
        upsert_record(root, enriched)
    return {"ok": True, "record": enriched}


def enrich_record_from_local_metadata(root: str | Path, record: dict[str, Any], *, metadata_resolver: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    record = normalize_record(record)
    file_path = str(record.get("file") or "")
    resolved = None
    if not file_path or not Path(file_path).exists() or Path(file_path).suffix.lower() != ".safetensors":
        resolved = resolve_lora_file_path(root, str(record.get("catalog_name") or record.get("name") or file_path or ""))
        if resolved:
            file_path = str(resolved)
            record["file"] = file_path
            record.setdefault("field_sources", {})["file"] = "local:path_resolver"
    query_name = str(record.get("catalog_name") or record.get("name") or file_path or "")
    if not file_path or not Path(file_path).exists() or Path(file_path).suffix.lower() != ".safetensors":
        if metadata_resolver:
            remote_result = metadata_resolver(query_name)
            if remote_result.get("ok") and remote_result.get("metadata"):
                defaults = infer_defaults_from_metadata(remote_result.get("metadata", {}))
                merged = {**record}
                for key, value in defaults.items():
                    if key == "field_sources":
                        merged.setdefault("field_sources", {}).update({field: "comfy:view_metadata" for field in value})
                    elif value and (not merged.get(key) or str(merged.get(key)).strip().casefold() in {"base unknown", "unknown"}):
                        merged[key] = value
                merged["metadata_status"] = defaults.get("metadata_status") or "readable"
                merged["metadata_resolution"] = {"source": "comfy:view_metadata", "ok": True}
                return normalize_record(merged)
            record["remote_metadata_resolution"] = remote_result
        record["metadata_status"] = "path_unresolved" if record.get("metadata_status") == "catalog_only" else record.get("metadata_status", "path_unresolved")
        record["metadata_resolution"] = lora_path_resolution_payload(root, query_name)
        return record
    metadata_result = read_safetensors_metadata(file_path)
    if not metadata_result.get("ok"):
        record["metadata_status"] = "unreadable"
        return record
    defaults = infer_defaults_from_metadata(metadata_result.get("metadata", {}))
    merged = {**record}
    for key, value in defaults.items():
        if key == "field_sources":
            merged.setdefault("field_sources", {}).update(value)
        elif value and (not merged.get(key) or str(merged.get(key)).strip().casefold() in {"base unknown", "unknown"}):
            merged[key] = value
    merged["metadata_status"] = defaults.get("metadata_status") or "readable"
    merged["metadata_resolution"] = lora_path_resolution_payload(root, str(record.get("catalog_name") or record.get("name") or file_path))
    return normalize_record(merged)


def scan_payload(root: str | Path, payload: dict[str, Any] | None = None, *, catalog_loras: list[str] | None = None) -> dict[str, Any]:
    payload = payload or {}
    source = str(payload.get("source") or "comfy_lora_loader")
    if source == "local_folder" or payload.get("folder"):
        result = scan_lora_folder(payload.get("folder") or "")
        if result.get("ok"):
            existing = load_records(root)
            merged = merge_catalog_records(existing, [])
            by_id = {item["id"]: item for item in merged}
            for item in result.get("records", []):
                by_id[item["id"]] = normalize_record(item)
            save_records(root, list(by_id.values()))
            result["records"] = list(by_id.values())
            result["count"] = len(result["records"])
        return result
    return scan_comfy_lora_catalog(root, catalog_loras or [])


def save_record_payload(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    record = payload.get("record") if isinstance(payload.get("record"), dict) else payload
    normalized = normalize_record({**record, "updated": utc_now_iso()})
    field_sources = normalized.setdefault("field_sources", {})
    for field in ("triggers", "keywords", "negative_keywords", "example_prompt", "civitai_url"):
        if field in record:
            field_sources[field] = "manual"
    if isinstance(record.get("remote_source"), dict) and record.get("remote_source", {}).get("url"):
        field_sources["remote_source.url"] = "manual"
    saved = upsert_record(root, normalized)
    return {"ok": True, "record": saved, "message": "LoRA metadata saved."}



def _has_meaningful_civitai_data(incoming: dict[str, Any]) -> bool:
    for key in ("triggers", "keywords", "negative_keywords", "preview_images", "preview_urls", "prompt_options"):
        if incoming.get(key):
            return True
    for key in ("base_model", "example_prompt", "notes"):
        if str(incoming.get(key) or "").strip():
            return True
    return False


def _changed_lora_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields = [
        "triggers", "keywords", "negative_keywords", "preview_image", "preview_images",
        "base_model", "example_prompt", "prompt_options", "notes", "remote_source", "civitai_url",
    ]
    return [field for field in fields if before.get(field) != after.get(field)]

def civitai_import_payload(root: str | Path, payload: dict[str, Any], *, catalog_loras: list[str] | None = None, fetcher=None, preview_fetcher=None) -> dict[str, Any]:
    record_id = str(payload.get("record_id") or payload.get("id") or "").strip()
    url = str(payload.get("url") or payload.get("civitai_url") or "").strip()
    mode = str(payload.get("mode") or "fill_missing")
    selected_fields = payload.get("selected_fields") if isinstance(payload.get("selected_fields"), list) else None
    record = find_record(root, record_id, catalog_loras=catalog_loras or []) if record_id else None
    if record is None and payload.get("record"):
        record = normalize_record(payload.get("record") or {})
    if record is None:
        return {"ok": False, "error": "LoRA record is required before CivitAI import.", "parsed": parse_civitai_url(url)}
    fetched = fetch_civitai_payload(url, fetcher=fetcher)
    if not fetched.get("ok"):
        errors = fetched.get("errors") or []
        if errors:
            fetched["message"] = f"{fetched.get('error', 'Could not fetch CivitAI metadata.')} Details: {' | '.join(str(item) for item in errors[:2])}"
        return fetched
    incoming = normalize_civitai_payload(fetched.get("data") or {})
    incoming.setdefault("remote_source", {})["url"] = url
    if not _has_meaningful_civitai_data(incoming):
        return {
            "ok": False,
            "error": "CivitAI returned a response, but Neo could not find usable triggers, keywords, prompts, previews, or base model metadata in it.",
            "fetched_url": fetched.get("url"),
            "parsed": fetched.get("parsed"),
        }
    preview_urls = incoming.get("preview_urls") or incoming.get("preview_images") or []
    cache_result = cache_preview_urls(root, record.get("id") or record.get("name") or "unknown", preview_urls, fetcher=preview_fetcher)
    if cache_result.get("paths"):
        incoming["preview_images"] = normalize_preview_paths(cache_result.get("paths") or [])
        incoming["preview_image"] = incoming["preview_images"][0]
    elif preview_urls:
        # Keep remote URLs as fallback previews, but report that local preview caching failed.
        incoming["preview_images"] = normalize_preview_paths(preview_urls)
        incoming["preview_image"] = incoming["preview_images"][0] if incoming["preview_images"] else ""
    before = normalize_record(record)
    merged = import_civitai_into_record(record, incoming, mode=mode, selected_fields=selected_fields)
    incoming_previews = normalize_preview_paths((incoming.get("preview_images") or []) + (incoming.get("preview_urls") or []))
    if incoming_previews:
        merged_previews = normalize_preview_paths((merged.get("preview_images") or []) + incoming_previews)
        merged["preview_images"] = merged_previews
        current_primary = str(merged.get("preview_image") or "")
        if not current_primary or "/original=true/" in current_primary:
            merged["preview_image"] = merged_previews[0] if merged_previews else current_primary
        merged.setdefault("field_sources", {})["preview_images"] = "remote:civitai"
        merged.setdefault("field_sources", {})["preview_image"] = "remote:civitai"
    merged["updated"] = utc_now_iso()
    saved = upsert_record(root, merged)
    changed_fields = _changed_lora_fields(before, saved)
    summary = {
        "incoming_triggers": len(incoming.get("triggers") or []),
        "incoming_keywords": len(incoming.get("keywords") or []),
        "incoming_negative_keywords": len(incoming.get("negative_keywords") or []),
        "incoming_prompts": len(incoming.get("prompt_options") or []) + (1 if incoming.get("example_prompt") else 0),
        "incoming_preview_urls": len(preview_urls or []),
        "cached_previews": len(cache_result.get("paths") or []),
        "changed_fields": changed_fields,
        "changed_count": len(changed_fields),
    }
    message = "CivitAI pull complete"
    if summary["changed_count"] == 0:
        message = "CivitAI returned metadata, but fill/merge rules did not change this saved LoRA record."
    if preview_urls and not cache_result.get("paths"):
        message += " Preview images were found, but local preview caching failed; remote preview URLs were saved as fallback."
    return {"ok": True, "record": saved, "fetched_url": fetched.get("url"), "preview_cache": cache_result, "mode": mode, "import_summary": summary, "message": message}


def set_primary_preview_payload(root: str | Path, payload: dict[str, Any], *, catalog_loras: list[str] | None = None) -> dict[str, Any]:
    record_id = str(payload.get("record_id") or payload.get("id") or "").strip()
    preview = str(payload.get("preview") or payload.get("preview_image") or "").strip()
    record = find_record(root, record_id, catalog_loras=catalog_loras or [])
    if record is None:
        return {"ok": False, "error": "LoRA record not found."}
    previews = normalize_preview_paths([preview] + (record.get("preview_images") or []))
    record["preview_image"] = preview
    record["preview_images"] = previews
    record.setdefault("field_sources", {})["preview_image"] = "manual"
    saved = upsert_record(root, record)
    return {"ok": True, "record": saved}


def delete_record_payload(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    return delete_record(root, str(payload.get("record_id") or payload.get("id") or ""))


def insert_block_payload(root: str | Path, record_id: str, *, catalog_loras: list[str] | None = None) -> dict[str, Any]:
    record = find_record(root, record_id, catalog_loras=catalog_loras or [])
    if record is None:
        return {"ok": False, "error": "LoRA record not found.", "text": ""}
    triggers = ", ".join(record.get("triggers") or [])
    prompt = record.get("example_prompt") or triggers
    return {"ok": True, "record_id": record.get("id"), "text": prompt, "triggers": record.get("triggers") or [], "keywords": record.get("keywords") or []}


def preview_payload(root: str | Path, path: str) -> dict[str, Any]:
    return preview_file_response(path, root=root)



def create_lora_stack_library_router(
    root: str | Path,
    *,
    catalog_resolver: Callable[[str | None], list[str]] | None = None,
    metadata_resolver: Callable[[str | None, str], dict[str, Any]] | None = None,
) -> APIRouter:
    """Build the extension-owned LoRA Library API router.

    The main app supplies a tiny catalog resolver so this module can stay inside
    the extension folder while still reading the active Comfy profile catalog.
    """
    router = APIRouter(prefix="/api/extensions/lora_stack/library", tags=["lora_stack_library"])

    def _catalog(profile_id: str | None = None) -> list[str]:
        if catalog_resolver is None:
            return []
        try:
            return catalog_resolver(profile_id)
        except Exception:  # noqa: BLE001 - API should remain usable for saved records offline.
            return []

    @router.get("/routes")
    def lora_stack_library_routes() -> dict[str, Any]:
        return {
            "ok": True,
            "schema_version": "neo.lora_stack.library.routes.v1",
            "routes": route_specs(),
        }

    @router.get("/status")
    def lora_stack_library_status(profile_id: str | None = None) -> dict[str, Any]:
        names = _catalog(profile_id)
        return {
            "ok": True,
            "schema_version": "neo.lora_stack.library.status.v1",
            "source": "comfy_lora_loader",
            "catalog_count": len(names),
            "store_ready": True,
            "route_specs": route_specs(),
        }

    @router.get("/catalog")
    def lora_stack_library_catalog(profile_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        return catalog_payload(root, catalog_loras=_catalog(profile_id), query=q or "")

    @router.get("/resolve")
    def lora_stack_library_resolve(query: str, profile_id: str | None = None) -> dict[str, Any]:
        result = resolve_payload(root, query, catalog_loras=_catalog(profile_id), metadata_resolver=(lambda name: metadata_resolver(profile_id, name)) if metadata_resolver else None)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "LoRA catalog record not found."))
        return result

    @router.get("/browser")
    def lora_stack_library_browser(profile_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        return browser_payload(root, catalog_loras=_catalog(profile_id), query=q or "")

    @router.get("/record")
    def lora_stack_library_record(record_id: str, profile_id: str | None = None) -> dict[str, Any]:
        result = record_payload(root, record_id, catalog_loras=_catalog(profile_id), metadata_resolver=(lambda name: metadata_resolver(profile_id, name)) if metadata_resolver else None)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "LoRA record not found."))
        return result

    @router.get("/preview-file")
    def lora_stack_library_preview_file(path: str) -> FileResponse:
        result = preview_payload(root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "LoRA preview not found."))
        return FileResponse(result["path"], media_type=result.get("media_type") or None)

    @router.get("/insert-block")
    def lora_stack_library_insert_block(record_id: str, profile_id: str | None = None) -> dict[str, Any]:
        result = insert_block_payload(root, record_id, catalog_loras=_catalog(profile_id))
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "LoRA record not found."))
        return result

    @router.post("/scan")
    def lora_stack_library_scan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        return scan_payload(root, payload, catalog_loras=_catalog(payload.get("profile_id")))

    @router.post("/save")
    def lora_stack_library_save(payload: dict[str, Any]) -> dict[str, Any]:
        result = save_record_payload(root, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Could not save LoRA metadata."))
        return result

    @router.post("/civitai-import")
    def lora_stack_library_civitai_import(payload: dict[str, Any]) -> dict[str, Any]:
        result = civitai_import_payload(root, payload, catalog_loras=_catalog(payload.get("profile_id")))
        if not result.get("ok"):
            detail = result.get("message") or result.get("error") or result.get("errors") or "CivitAI import failed."
            raise HTTPException(status_code=400, detail=detail)
        return result

    @router.post("/set-primary-preview")
    def lora_stack_library_set_primary_preview(payload: dict[str, Any]) -> dict[str, Any]:
        result = set_primary_preview_payload(root, payload, catalog_loras=_catalog(payload.get("profile_id")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Could not set primary preview."))
        return result

    @router.post("/delete")
    def lora_stack_library_delete(payload: dict[str, Any]) -> dict[str, Any]:
        result = delete_record_payload(root, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="LoRA record not found.")
        return result

    return router


def register_lora_stack_library_routes(
    app: Any,
    root: str | Path,
    *,
    catalog_resolver: Callable[[str | None], list[str]] | None = None,
    metadata_resolver: Callable[[str | None, str], dict[str, Any]] | None = None,
) -> APIRouter:
    """Register LoRA Library API routes on the provided FastAPI app."""
    router = create_lora_stack_library_router(root, catalog_resolver=catalog_resolver, metadata_resolver=metadata_resolver)
    app.include_router(router)
    return router
