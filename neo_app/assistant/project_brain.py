from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import UploadFile

from neo_app.assistant.contracts import compact_json_payload, normalize_surface_id, trim_text
from neo_app.assistant.attachments import MAX_DOCUMENT_UPLOAD_BYTES, extract_document_text
from neo_app.knowledge.project_documents import extract_project_document
from neo_app.context_identity import resolve_canonical_identity, is_builtin_scope
from neo_app.memory.project_brain_ingestion import get_project_brain_ingestion_service
from neo_app.memory.job_service import get_memory_job_service
from neo_app.assistant.guides import load_guides, project_surface, search_guides
from neo_app.assistant.store import ASSISTANT_DATA_DIR, get_project, list_context_items, list_memory_captures, now_iso, read_json, save_context_item_payload, slugify, write_json

PROJECT_BRAIN_SCHEMA_ID = "neo.assistant.project_brain.v1"
PROJECT_BRAIN_DIR = ASSISTANT_DATA_DIR / "project_brain"
SNAPSHOT_SCHEMA_ID = "neo.assistant.project_snapshot.v1"
METADATA_INDEX_SCHEMA_ID = "neo.assistant.project_metadata_index.v1"
PROJECT_UPLOAD_SCHEMA_ID = "neo.assistant.project_upload.v1"

SURFACE_METADATA_ROOTS: dict[str, tuple[str, ...]] = {
    "image": ("outputs/image_metadata",),
    "video": ("outputs/video", "runtime/jobs/video"),
    "voice": ("outputs/voice", "runtime/jobs/voice"),
    "prompt_captioning": ("outputs/prompt_captioning", "prompt_captioning"),
    "roleplay": ("roleplay", "runtime/jobs/roleplay"),
}

ROOT_DIR = Path(__file__).resolve().parents[2]
NEO_DATA_DIR = ROOT_DIR / "neo_data"


def _project_id(value: Any) -> str:
    return slugify(str(value or "general"), "general")


def _brain_root(project_id: str) -> Path:
    return PROJECT_BRAIN_DIR / _project_id(project_id)


def ensure_project_brain_dirs(project_id: str) -> Path:
    root = _brain_root(project_id)
    for name in ("snapshots", "memory_index", "uploads", "reports"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _safe_path(root: Path, filename: str) -> Path:
    root = root.resolve()
    safe = slugify(filename, "record")
    path = (root / f"{safe}.json").resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid project brain path")
    return path


def _safe_upload_path(root: Path, filename: str) -> Path:
    root = root.resolve()
    name = str(filename or "upload").strip() or "upload"
    suffix = Path(name).suffix[:16]
    stem = slugify(Path(name).stem, "upload")[:80]
    path = (root / f"{stem}_{uuid4().hex[:10]}{suffix}").resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid project upload path")
    return path


def _surface_for_project(project_id: str, surface: str = "") -> str:
    return project_surface(project_id, surface or "")


def _project_identity(project_id: str, surface: str = "") -> dict[str, Any]:
    scope = get_project(project_id) or {}
    nested = scope.get("identity") if isinstance(scope.get("identity"), dict) else {}
    delivery_project_id = str(
        scope.get("delivery_project_id")
        or scope.get("linked_project_id")
        or nested.get("project_id")
        or ""
    ).strip()
    identity = resolve_canonical_identity(
        {
            "project_id": project_id or "general",
            "scope_id": scope.get("scope_id") or project_id or "general",
            "surface": surface or scope.get("surface_id") or scope.get("surface") or _surface_for_project(project_id),
            "delivery_project_id": delivery_project_id,
            "workspace_id": scope.get("workspace_id") or "",
        },
        legacy_project_is_scope=True,
        source="assistant_project_brain_phase7",
    )
    return identity.as_dict()


def _hash_payload(value: Any) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        text = str(value or "")
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _document_id_for_upload(project_id: str, filename: str) -> str:
    logical = f"{_project_id(project_id)}:{str(filename or 'project_document').strip().lower()}"
    return f"pdoc_{hashlib.sha256(logical.encode('utf-8', errors='ignore')).hexdigest()[:20]}"


def _knowledge_manifest_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".knowledge.json")


def _ordered_project_upload_records(upload_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return real Project upload sidecars in deterministic revision order.

    Upload filenames contain random UUIDs, so path ordering cannot represent revision
    order. Rebuild replays oldest -> newest using the recorded creation timestamp,
    with mtime/name only as deterministic fallbacks. Knowledge manifests are excluded.
    """
    rows: list[tuple[str, float, str, Path, dict[str, Any]]] = []
    if not upload_dir.exists():
        return []
    for sidecar in upload_dir.glob("*.json"):
        record = read_json(sidecar, {})
        if not isinstance(record, dict) or not record.get("upload_id"):
            continue
        try:
            mtime = float(sidecar.stat().st_mtime)
        except OSError:
            mtime = 0.0
        rows.append((str(record.get("created_at") or ""), mtime, sidecar.name, sidecar, record))
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(item[3], item[4]) for item in rows]


def _short_json(value: Any, limit: int = 7000) -> str:
    try:
        return trim_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), limit)
    except Exception:
        return trim_text(str(value or ""), limit)


def _snapshot_summary(record: dict[str, Any]) -> str:
    surface = str(record.get("surface") or "assistant")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    params = payload.get("imageDraft") or payload.get("videoDraft") or payload.get("voiceDraft") or payload.get("promptCaptioning") or {}
    if not isinstance(params, dict):
        params = {}
    interesting = []
    for key in ("family", "loader", "model", "checkpoint", "positive_prompt", "negative_prompt", "width", "height", "steps", "cfg", "seed", "latent_capture_mode"):
        if key in params and params.get(key) not in (None, "", [], {}):
            interesting.append(f"{key}: {trim_text(params.get(key), 180)}")
    return "\n".join([
        f"Surface: {surface}",
        f"Captured at: {record.get('created_at') or ''}",
        f"Project: {record.get('project_id') or 'general'}",
        "Current values:",
        *(f"- {item}" for item in interesting[:20]),
        "Snapshot payload preview:",
        _short_json(payload, 2600),
    ]).strip()


def capture_project_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _project_id(payload.get("project_id") or "general")
    surface = normalize_surface_id(payload.get("surface") or _surface_for_project(project_id), default="assistant")
    identity = _project_identity(project_id, surface)
    root = ensure_project_brain_dirs(project_id)
    stamp = now_iso()
    snapshot_payload = payload.get("surface_context_snapshot") or payload.get("payload") or payload.get("snapshot") or {}
    if not isinstance(snapshot_payload, dict):
        snapshot_payload = {"value": str(snapshot_payload)}
    content_hash = _hash_payload({"surface": surface, "payload": snapshot_payload})

    # Capture is a manual pin, but repeated clicks on an unchanged state should not
    # multiply legacy snapshots or canonical memory fragments.
    for existing in list_project_brain_snapshots(project_id, limit=20):
        existing_hash = str(existing.get("content_hash") or "")
        if not existing_hash:
            existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
            existing_hash = _hash_payload({"surface": existing.get("surface") or "assistant", "payload": existing_payload})
        if str(existing.get("surface") or "") == surface and existing_hash == content_hash:
            canonical = get_project_brain_ingestion_service().ingest_snapshot(existing, content=_snapshot_summary(existing), identity=identity)
            existing_context = next((
                item for item in list_context_items(project_id=project_id, limit=250)
                if str((item.get("metadata") or {}).get("snapshot_id") or "") == str(existing.get("snapshot_id") or "")
            ), None)
            if existing_context is None:
                projection = save_context_item_payload({
                    "title": existing.get("title") or f"{surface.title()} state capture",
                    "text": _snapshot_summary(existing),
                    "project_id": project_id,
                    "surface": surface,
                    "source": "assistant_project_brain_snapshot",
                    "kind": "live_surface_snapshot",
                    "tags": [surface, "snapshot", "live_state"],
                    "metadata": {"snapshot_id": existing.get("snapshot_id") or "", "schema_id": SNAPSHOT_SCHEMA_ID, "canonical_source": True},
                    "canonical_projection_only": True,
                })
                existing_context = projection.get("context_item")
            existing["content_hash"] = content_hash
            existing["context_id"] = (existing_context or {}).get("context_id", "")
            existing["canonical_memory"] = {
                "fragment_ids": canonical.get("fragment_ids") or [],
                "event_id": canonical.get("event_id") or "",
                "status": canonical.get("status") or "",
            }
            write_json(_safe_path(root / "snapshots", str(existing.get("snapshot_id") or "snapshot")), existing)
            return {
                "ok": True,
                "schema_id": SNAPSHOT_SCHEMA_ID,
                "status": "deduplicated",
                "deduplicated": True,
                "snapshot": existing,
                "context_item": existing_context,
                "canonical_memory": canonical,
                "project_brain": project_brain_status_payload(project_id=project_id, surface=surface),
            }

    snapshot_id = slugify(payload.get("snapshot_id") or f"snapshot_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}", "snapshot")
    record = {
        "schema_id": SNAPSHOT_SCHEMA_ID,
        "snapshot_id": snapshot_id,
        "project_id": project_id,  # legacy Scope alias
        "scope_id": identity.get("scope_id") or project_id,
        "surface_id": identity.get("surface_id") or surface,
        "delivery_project_id": identity.get("project_id") or "",
        "surface": surface,
        "title": trim_text(payload.get("title") or f"{surface.title()} state capture", 180),
        "summary": trim_text(payload.get("summary") or "Live surface state captured from Assistant > Scope.", 1200),
        "payload": compact_json_payload(snapshot_payload, limit=64000),
        "content_hash": content_hash,
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_path(root / "snapshots", snapshot_id), record)
    canonical = get_project_brain_ingestion_service().ingest_snapshot(record, content=_snapshot_summary(record), identity=identity)
    context = save_context_item_payload({
        "title": record["title"],
        "text": _snapshot_summary(record),
        "project_id": project_id,
        "surface": surface,
        "source": "assistant_project_brain_snapshot",
        "kind": "live_surface_snapshot",
        "tags": [surface, "snapshot", "live_state"],
        "metadata": {"snapshot_id": snapshot_id, "schema_id": SNAPSHOT_SCHEMA_ID, "canonical_source": True},
        "canonical_projection_only": True,
    })
    record["context_id"] = (context.get("context_item") or {}).get("context_id", "")
    record["canonical_memory"] = {
        "fragment_ids": canonical.get("fragment_ids") or [],
        "event_id": canonical.get("event_id") or "",
        "status": canonical.get("status") or "",
    }
    write_json(_safe_path(root / "snapshots", snapshot_id), record)
    return {"ok": True, "schema_id": SNAPSHOT_SCHEMA_ID, "snapshot": record, "context_item": context.get("context_item"), "canonical_memory": canonical, "project_brain": project_brain_status_payload(project_id=project_id, surface=surface)}


def _metadata_roots_for_surface(surface: str, *, project_id: str = "") -> list[Path]:
    # General is the deliberate cross-surface Project Brain. A custom Assistant
    # Scope (client work, ad-hoc scope, etc.) must not silently ingest every Neo
    # surface merely because its canonical surface is `assistant`.
    if surface in {"global", "all"} or (surface == "assistant" and project_id == "general"):
        roots: list[Path] = []
        for names in SURFACE_METADATA_ROOTS.values():
            roots.extend(NEO_DATA_DIR / name for name in names)
        return roots
    if surface == "assistant":
        return []
    return [NEO_DATA_DIR / name for name in SURFACE_METADATA_ROOTS.get(surface, ())]


def _metadata_summary(path: Path, data: Any, surface: str) -> dict[str, Any]:
    if isinstance(data, dict):
        prompt = data.get("prompt") or data.get("positive_prompt") or data.get("positive") or data.get("text") or ""
        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else data
        model = params.get("model") or params.get("checkpoint") or params.get("model_name") or params.get("family") or ""
        created = data.get("created_at") or data.get("updated_at") or data.get("timestamp") or ""
        file = data.get("output_file") or data.get("file") or data.get("path") or path.name
        keys = sorted(str(k) for k in list(data.keys())[:40])
        summary = trim_text(prompt or data.get("summary") or _short_json(data, 1200), 1200)
    else:
        model = ""
        created = ""
        file = path.name
        keys = []
        summary = trim_text(str(data), 1200)
    return {
        "surface": surface,
        "path": str(path.relative_to(ROOT_DIR)) if path.is_relative_to(ROOT_DIR) else str(path),
        "file": str(file),
        "model": trim_text(model, 180),
        "created_at": str(created),
        "keys": keys,
        "summary": summary,
    }


def index_project_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _project_id(payload.get("project_id") or "general")
    surface = normalize_surface_id(payload.get("surface") or _surface_for_project(project_id), default="assistant")
    identity = _project_identity(project_id, surface)
    limit = max(1, min(int(payload.get("limit") or 80), 250))
    root = ensure_project_brain_dirs(project_id)
    stamp = now_iso()
    records: list[dict[str, Any]] = []
    for scan_root in _metadata_roots_for_surface(surface, project_id=project_id):
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(records) >= limit:
                break
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rel = str(path.relative_to(NEO_DATA_DIR)).replace("\\", "/") if path.is_relative_to(NEO_DATA_DIR) else str(path)
            inferred_surface = surface
            for key in SURFACE_METADATA_ROOTS:
                if rel.startswith(tuple(SURFACE_METADATA_ROOTS[key])):
                    inferred_surface = key
                    break
            records.append(_metadata_summary(path, data, inferred_surface))
        if len(records) >= limit:
            break
    content_hash = _hash_payload(records)
    existing = next((row for row in list_project_brain_indexes(project_id, limit=20) if row.get("surface") == surface and str(row.get("content_hash") or "") == content_hash), None)
    if existing is not None:
        canonical = get_project_brain_ingestion_service().ingest_metadata_records(existing, identity=identity)
        return {
            "ok": True,
            "schema_id": METADATA_INDEX_SCHEMA_ID,
            "status": "deduplicated",
            "deduplicated": True,
            "index": existing,
            "context_item": None,
            "canonical_memory": canonical,
            "project_brain": project_brain_status_payload(project_id=project_id, surface=surface),
        }

    index_id = slugify(payload.get("index_id") or f"index_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}", "index")
    index = {
        "schema_id": METADATA_INDEX_SCHEMA_ID,
        "index_id": index_id,
        "project_id": project_id,
        "scope_id": identity.get("scope_id") or project_id,
        "surface_id": identity.get("surface_id") or surface,
        "delivery_project_id": identity.get("project_id") or "",
        "surface": surface,
        "created_at": stamp,
        "record_count": len(records),
        "content_hash": content_hash,
        "records": records,
    }
    write_json(_safe_path(root / "memory_index", index_id), index)
    canonical = get_project_brain_ingestion_service().ingest_metadata_records(index, identity=identity)
    summary_lines = [f"Indexed {len(records)} Neo metadata record(s) for {surface}."]
    for row in records[:24]:
        summary_lines.append(f"- [{row.get('surface')}] {row.get('file') or row.get('path')}: {trim_text(row.get('summary'), 300)}")
    context = save_context_item_payload({
        "title": f"{surface.title()} indexed project data",
        "text": "\n".join(summary_lines),
        "project_id": project_id,
        "surface": surface if surface not in {"all", "global"} else "assistant",
        "source": "assistant_project_brain_index",
        "kind": "metadata_index",
        "tags": [surface, "metadata", "index"],
        "metadata": {"index_id": index_id, "schema_id": METADATA_INDEX_SCHEMA_ID, "record_count": len(records), "canonical_source": True},
        "canonical_projection_only": True,
    })
    index["context_id"] = (context.get("context_item") or {}).get("context_id", "")
    index["canonical_ingestion"] = {
        "fragment_ids": canonical.get("fragment_ids") or [],
        "ingested_count": canonical.get("ingested_count") or 0,
        "deduplicated_count": canonical.get("deduplicated_count") or 0,
    }
    write_json(_safe_path(root / "memory_index", index_id), index)
    return {"ok": True, "schema_id": METADATA_INDEX_SCHEMA_ID, "index": index, "context_item": context.get("context_item"), "canonical_memory": canonical, "project_brain": project_brain_status_payload(project_id=project_id, surface=surface)}


def list_project_brain_snapshots(project_id: str, limit: int = 8) -> list[dict[str, Any]]:
    root = ensure_project_brain_dirs(project_id) / "snapshots"
    rows = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("snapshot_id"):
            rows.append(record)
    return rows


def list_project_brain_indexes(project_id: str, limit: int = 5) -> list[dict[str, Any]]:
    root = ensure_project_brain_dirs(project_id) / "memory_index"
    rows = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("index_id"):
            rows.append(record)
    return rows


def project_brain_status_payload(project_id: str = "general", surface: str = "") -> dict[str, Any]:
    project_id = _project_id(project_id)
    resolved_surface = _surface_for_project(project_id, surface)
    identity = _project_identity(project_id, resolved_surface)
    root = ensure_project_brain_dirs(project_id)
    snapshots = list_project_brain_snapshots(project_id, limit=20)
    indexes = list_project_brain_indexes(project_id, limit=20)
    uploads = [path for path in (root / "uploads").glob("*") if not path.name.endswith(".json")] if (root / "uploads").exists() else []
    structured_manifests = list((root / "uploads").glob("*.knowledge.json")) if (root / "uploads").exists() else []
    upload_records: list[dict[str, Any]] = []
    if (root / "uploads").exists():
        for sidecar in (root / "uploads").glob("*.json"):
            if sidecar.name.endswith(".knowledge.json"):
                continue
            record = read_json(sidecar, {})
            if not isinstance(record, dict) or not record.get("upload_id"):
                continue
            stored_path = str(record.get("stored_path") or "")
            record = {**record, "stored_exists": bool(stored_path and (ROOT_DIR / stored_path).exists())}
            upload_records.append(record)
        upload_records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    newest_by_document: dict[str, str] = {}
    first_by_hash: dict[str, str] = {}
    for row in upload_records:
        document_id = str(row.get("document_id") or "")
        if document_id and document_id not in newest_by_document:
            newest_by_document[document_id] = str(row.get("revision_id") or "")
    for row in reversed(upload_records):
        content_hash = str(row.get("content_hash") or "")
        if content_hash and content_hash not in first_by_hash:
            first_by_hash[content_hash] = str(row.get("filename") or "")
    latest_uploads = []
    for row in upload_records[:100]:
        content_hash = str(row.get("content_hash") or "")
        filename = str(row.get("filename") or "")
        duplicate_of = first_by_hash.get(content_hash, "") if content_hash else ""
        canonical = row.get("canonical_memory") if isinstance(row.get("canonical_memory"), dict) else {}
        latest_uploads.append({
            "upload_id": row.get("upload_id") or "",
            "document_id": row.get("document_id") or "",
            "revision_id": row.get("revision_id") or "",
            "filename": filename,
            "mime_type": row.get("mime_type") or "",
            "size_bytes": int(row.get("size_bytes") or 0),
            "content_hash": content_hash,
            "created_at": row.get("created_at") or "",
            "stored_exists": bool(row.get("stored_exists")),
            "current_revision": newest_by_document.get(str(row.get("document_id") or ""), "") == str(row.get("revision_id") or ""),
            "duplicate_content_of": duplicate_of if duplicate_of and duplicate_of != filename else "",
            "status": canonical.get("status") or "stored",
            "fragment_count": int(canonical.get("chunk_count") or 0),
            "fact_count": len(canonical.get("fact_ids") or []),
            "entity_count": int(canonical.get("entity_count") or 0),
        })
    guides = search_guides("", project_id=project_id, surface=resolved_surface, limit=12) if is_builtin_scope(identity.get("scope_id") or project_id) else {"count": 0, "total_available": 0}
    canonical = get_project_brain_ingestion_service().status(project_id=project_id, surface=resolved_surface, identity=identity)
    job_rows = (get_memory_job_service().list(job_type="project_brain_rebuild", limit=50).get("jobs") or [])
    project_jobs = [job for job in job_rows if str(job.get("scope_id") or "") == project_id and str(job.get("surface") or "") in {resolved_surface, "global", "assistant"}]
    latest_job = project_jobs[0] if project_jobs else None
    active_jobs = [job for job in project_jobs if job.get("status") in {"queued", "running"}]
    latest_report = None
    reports = sorted((root / "reports").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if (root / "reports").exists() else []
    if reports:
        latest_report = read_json(reports[0], {})
    return {
        "ok": True,
        "schema_id": PROJECT_BRAIN_SCHEMA_ID,
        "project_id": project_id,
        "scope_id": identity.get("scope_id") or project_id,
        "surface_id": identity.get("surface_id") or resolved_surface,
        "delivery_project_id": identity.get("project_id") or "",
        "surface": resolved_surface,
        "root": str(root),
        "counts": {
            "snapshots": len(snapshots),
            "indexes": len(indexes),
            "uploads": len(uploads),
            "structured_project_documents": len(structured_manifests),
            "built_in_guides_visible": guides.get("total_available") or guides.get("count") or 0,
            "canonical_fragments": int((canonical.get("counts") or {}).get("active_fragments") or 0),
            "canonical_facts": int((canonical.get("counts") or {}).get("active_facts") or 0),
            "queued_embeddings": int((canonical.get("counts") or {}).get("queued_embeddings") or 0),
            "active_jobs": len(active_jobs),
        },
        "latest_uploads": latest_uploads,
        "canonical_memory": canonical,
        "jobs": {
            "active": active_jobs[:4],
            "latest": latest_job,
            "authority": "neo_memory_jobs",
            "schema_id": "neo.memory.jobs.phase10.v1",
        },
        "latest_rebuild": {
            "rebuilt_at": (latest_report or {}).get("rebuilt_at") or "",
            "status": (latest_report or {}).get("status") or "",
            "report_id": (latest_report or {}).get("report_id") or "",
        },
        "latest_snapshots": [{k: row.get(k) for k in ("snapshot_id", "surface", "title", "created_at", "content_hash")} for row in snapshots[:6]],
        "latest_indexes": [{"index_id": row.get("index_id"), "surface": row.get("surface"), "record_count": row.get("record_count"), "created_at": row.get("created_at"), "content_hash": row.get("content_hash")} for row in indexes[:6]],
        "policy": "Unified Memory is retrieval-authoritative; persistent project documents use NKB-7 source-direct structured fragments, while Project Brain JSON remains compatibility/audit projection.",
    }


def rebuild_project_brain_payload(
    payload: dict[str, Any],
    *,
    progress_callback: Callable[..., Any] | None = None,
    cancel_callback: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    project_id = _project_id(payload.get("project_id") or "general")
    surface = normalize_surface_id(payload.get("surface") or _surface_for_project(project_id), default="assistant")
    identity = _project_identity(project_id, surface)
    service = get_project_brain_ingestion_service()
    root = ensure_project_brain_dirs(project_id)
    pipeline: list[dict[str, Any]] = []

    def progress(phase: str, percent: int, message: str, *, current: int | None = None, total: int | None = None, warning: str = "", extra: dict[str, Any] | None = None) -> None:
        if progress_callback:
            progress_callback(phase=phase, percent=percent, message=message, current=current, total=total, warning=warning, extra=extra)

    def checkpoint(message: str = "") -> None:
        if cancel_callback:
            cancel_callback(message)

    progress("scan_metadata", 3, "Scanning Neo-owned project metadata.")
    checkpoint("Cancelled before metadata scanning.")
    index_result = index_project_data_payload({"project_id": project_id, "surface": surface, "limit": payload.get("limit") or 80})
    pipeline.append({
        "step": "scan_and_index_metadata",
        "ok": bool(index_result.get("ok")),
        "record_count": int((index_result.get("index") or {}).get("record_count") or 0),
        "deduplicated": bool(index_result.get("deduplicated")),
    })
    progress("scan_metadata", 15, f"Indexed {int((index_result.get('index') or {}).get('record_count') or 0)} metadata record(s).")

    snapshots = [record for record in list_project_brain_snapshots(project_id, limit=250) if surface in {"assistant", "global", "all"} or record.get("surface") in {surface, "assistant", "global"}]
    snapshot_results = []
    total = len(snapshots)
    for idx, record in enumerate(snapshots, start=1):
        checkpoint("Cancelled while ingesting snapshots.")
        snapshot_results.append(service.ingest_snapshot(record, content=_snapshot_summary(record), identity=identity))
        progress("ingest_snapshots", 15 + round((idx / max(1, total)) * 10), f"Ingesting snapshots {idx}/{total}.", current=idx, total=total)
    pipeline.append({
        "step": "ingest_snapshots",
        "ok": all(item.get("ok") for item in snapshot_results) if snapshot_results else True,
        "items": len(snapshot_results),
        "deduplicated": sum(1 for item in snapshot_results if item.get("deduplicated")),
    })

    indexes = [record for record in list_project_brain_indexes(project_id, limit=250) if surface in {"assistant", "global", "all"} or record.get("surface") in {surface, "assistant", "global", "all"}]
    index_results = []
    total = len(indexes)
    for idx, record in enumerate(indexes, start=1):
        checkpoint("Cancelled while ingesting metadata indexes.")
        index_results.append(service.ingest_metadata_records(record, identity=identity))
        progress("ingest_metadata", 25 + round((idx / max(1, total)) * 12), f"Ingesting metadata indexes {idx}/{total}.", current=idx, total=total)
    pipeline.append({
        "step": "ingest_metadata_indexes",
        "ok": all(item.get("ok") for item in index_results) if index_results else True,
        "indexes": len(index_results),
        "records": sum(int(item.get("record_count") or 0) for item in index_results),
        "deduplicated": sum(int(item.get("deduplicated_count") or 0) for item in index_results),
    })

    knowledge_records = []
    for record in list_context_items(project_id=project_id, limit=250):
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if metadata.get("canonical_projection_only") or str(record.get("source") or "").startswith("assistant_project_brain_"):
            continue
        knowledge_records.append(record)
    scope_knowledge_results = []
    total = len(knowledge_records)
    for idx, record in enumerate(knowledge_records, start=1):
        checkpoint("Cancelled while ingesting Scope Knowledge.")
        scope_knowledge_results.append(service.ingest_scope_knowledge(record, identity=identity))
        progress("ingest_scope_knowledge", 37 + round((idx / max(1, total)) * 10), f"Ingesting Scope Knowledge {idx}/{total}.", current=idx, total=total)
    pipeline.append({
        "step": "ingest_scope_knowledge",
        "ok": all(item.get("ok") for item in scope_knowledge_results) if scope_knowledge_results else True,
        "items": len(scope_knowledge_results),
        "deduplicated": sum(1 for item in scope_knowledge_results if item.get("deduplicated")),
    })

    captures = [record for record in list_memory_captures(limit=500) if str(record.get("project_id") or "") == project_id]
    capture_results = []
    total = len(captures)
    for idx, record in enumerate(captures, start=1):
        checkpoint("Cancelled while ingesting manual captures.")
        capture_results.append(service.ingest_manual_capture(record, identity=identity))
        progress("ingest_captures", 47 + round((idx / max(1, total)) * 8), f"Ingesting manual captures {idx}/{total}.", current=idx, total=total)
    pipeline.append({
        "step": "ingest_manual_captures",
        "ok": all(item.get("ok") for item in capture_results) if capture_results else True,
        "items": len(capture_results),
        "deduplicated": sum(1 for item in capture_results if item.get("deduplicated")),
    })

    upload_results = []
    upload_dir = root / "uploads"
    upload_records = _ordered_project_upload_records(upload_dir)
    total = len(upload_records)
    for idx, (sidecar, record) in enumerate(upload_records, start=1):
        checkpoint("Cancelled while extracting project files.")
        stored_path = str(record.get("stored_path") or "")
        path = ROOT_DIR / stored_path if stored_path and not Path(stored_path).is_absolute() else Path(stored_path)
        if not stored_path or not path.exists() or not path.is_file():
            progress("extract_documents", 55 + round((idx / max(1, total)) * 20), f"Skipping missing project file {idx}/{total}.", current=idx, total=total, warning=f"Missing project file: {stored_path}")
            continue
        progress("extract_documents", 55 + round(((idx - 1) / max(1, total)) * 20), f"Extracting {path.name} ({idx}/{total}).", current=idx - 1, total=total)
        suffix = path.suffix.lower()
        structured = extract_project_document(path, suffix)
        extracted_text = ""
        extraction = {k: v for k, v in structured.items() if k != "blocks"}
        if not structured.get("blocks"):
            extracted_text, legacy = extract_document_text(path, suffix)
            if extracted_text:
                extraction = {**legacy, "fallback": "assistant_attachment_extractor", "legacy_preview_chars": len(extracted_text)}
        checkpoint("Cancelled after document extraction.")
        file_hash = str(record.get("content_hash") or "") or _file_hash(path)
        if not record.get("document_id"):
            record["document_id"] = _document_id_for_upload(project_id, str(record.get("filename") or path.name))
        record["revision_id"] = f"rev_{file_hash[:24]}"
        result = service.ingest_document(record, extracted_text=extracted_text, extraction=extraction, structured_extraction=structured, identity=identity, file_content_hash=file_hash)
        manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else None
        if manifest:
            manifest_path = _knowledge_manifest_path(path)
            write_json(manifest_path, manifest)
            record["knowledge_manifest_path"] = str(manifest_path.relative_to(ROOT_DIR)) if manifest_path.is_relative_to(ROOT_DIR) else str(manifest_path)
        record["extraction"] = extraction
        record["extracted_text_chars"] = int((structured or {}).get("chars") or len(extracted_text))
        record["canonical_memory"] = {
            "status": result.get("status") or "", "phase": result.get("phase") or "",
            "fragment_ids": result.get("fragment_ids") or [], "chunk_count": result.get("chunk_count") or 0,
            "fact_ids": result.get("fact_ids") or [], "edge_ids": result.get("edge_ids") or [],
            "entity_count": result.get("entity_count") or 0, "deduplicated_count": result.get("deduplicated_count") or 0,
            "superseded_count": result.get("superseded_count") or 0,
        }
        write_json(path.with_suffix(path.suffix + ".json"), record)
        upload_results.append(result)
        progress("extract_documents", 55 + round((idx / max(1, total)) * 20), f"Extracted and ingested {idx}/{total} project file(s).", current=idx, total=total)
    pipeline.append({
        "step": "extract_and_ingest_project_files",
        "ok": all(item.get("ok") for item in upload_results) if upload_results else True,
        "files": len(upload_results),
        "fragments": sum(len(item.get("fragment_ids") or []) for item in upload_results),
        "deduplicated": sum(int(item.get("deduplicated_count") or 0) for item in upload_results),
    })

    checkpoint("Cancelled before consolidation.")
    progress("consolidate", 78, "Consolidating Project Brain memory.")
    try:
        consolidation = service.consolidate(project_id=project_id, surface=surface, identity=identity, max_groups=12)
    except Exception as exc:
        consolidation = {"ok": False, "status": "failed", "error": str(exc)[:800], "created_count": 0}
        progress("consolidate", 86, "Consolidation completed with a warning.", warning=str(exc)[:800])
    pipeline.append({
        "step": "consolidate_and_index",
        "ok": bool(consolidation.get("ok")),
        "summary_count": int(consolidation.get("created_count") or 0),
        "fts_index": "updated_inline",
        "semantic_embeddings": "queued",
    })

    checkpoint("Cancelled before validation.")
    progress("validate", 90, "Validating canonical Project Brain memory.")
    canonical_status = service.status(project_id=project_id, surface=surface, identity=identity)
    pipeline.append({
        "step": "validate",
        "ok": bool(canonical_status.get("ok")),
        "active_fragments": int((canonical_status.get("counts") or {}).get("active_fragments") or 0),
        "active_facts": int((canonical_status.get("counts") or {}).get("active_facts") or 0),
        "queued_embeddings": int((canonical_status.get("counts") or {}).get("queued_embeddings") or 0),
    })

    checkpoint("Cancelled before rebuild report write.")
    progress("report", 96, "Writing Project Brain rebuild report.")
    report_id = f"rebuild_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    report = {
        "schema_id": PROJECT_BRAIN_SCHEMA_ID,
        "phase": "10",
        "legacy_phase": "7",
        "report_id": report_id,
        "status": "completed" if all(bool(step.get("ok")) for step in pipeline) else "completed_with_warnings",
        "project_id": project_id,
        "scope_id": identity.get("scope_id") or project_id,
        "surface_id": identity.get("surface_id") or surface,
        "delivery_project_id": identity.get("project_id") or "",
        "surface": surface,
        "rebuilt_at": now_iso(),
        "guide_count": len(load_guides()),
        "pipeline": pipeline,
        "metadata_index_id": (index_result.get("index") or {}).get("index_id"),
        "metadata_record_count": (index_result.get("index") or {}).get("record_count"),
        "canonical_memory": canonical_status,
        "consolidation": consolidation,
        "policy": "Rebuild scans compatibility artifacts and replays them idempotently into Unified Memory. Long-running execution may be owned by the Phase 10 Memory Job Service; legacy data remains preserved.",
    }
    write_json(_safe_path(root / "reports", report_id), report)
    status = project_brain_status_payload(project_id=project_id, surface=surface)
    progress("completed", 100, "Project Brain rebuild completed.")
    return {"ok": True, "message": "Project Brain rebuild completed.", "report": report, "project_brain": status}


def _query_wants_metadata_history(query: str = "") -> bool:
    text = str(query or "").lower()
    terms = (
        "metadata", "sidecar", "output", "outputs", "generated", "generation", "history", "previous",
        "before", "last", "past", "used", "settings worked", "prompt used", "seed", "cleanup",
        "replay", "inspect", "inspector", "saved", "record", "records",
    )
    return any(term in text for term in terms)


def project_brain_context_text(project_id: str = "general", surface: str = "", limit: int = 6, query: str = "") -> tuple[str, dict[str, Any]]:
    project_id = _project_id(project_id)
    surface = _surface_for_project(project_id, surface)
    snapshots = [row for row in list_project_brain_snapshots(project_id, limit=limit) if surface in {"global", "all", "assistant"} or row.get("surface") in {surface, "assistant", "global"}]
    indexes = [row for row in list_project_brain_indexes(project_id, limit=limit) if surface in {"global", "all", "assistant"} or row.get("surface") in {surface, "all", "global", "assistant"}]
    include_metadata = _query_wants_metadata_history(query)
    parts: list[str] = []
    for row in snapshots[:3]:
        parts.append(f"Snapshot summary: {row.get('title') or row.get('snapshot_id')} ({row.get('surface')} · {row.get('created_at')})\n{_snapshot_summary(row)}")
    if include_metadata:
        for index in indexes[:3]:
            records = index.get("records") if isinstance(index.get("records"), list) else []
            sample_rows = []
            for r in records[:8]:
                model = f" · model: {trim_text(r.get('model'), 120)}" if r.get("model") else ""
                created = f" · {r.get('created_at')}" if r.get("created_at") else ""
                sample_rows.append(f"- [{r.get('surface')}] {r.get('file') or 'saved output'}{model}{created}: {trim_text(r.get('summary'), 220)}")
            sample = "\n".join(sample_rows) or "No summarized metadata rows were available."
            parts.append(f"Metadata summary: {index.get('index_id')} ({index.get('record_count') or len(records)} records · {index.get('surface')})\n{sample}")
    elif indexes:
        parts.append(f"Metadata indexes available for this scope: {len(indexes)}. They are withheld from this answer because the current question sounds like a guide/settings question, not a request for previous outputs or raw metadata.")
    text = "\n\n".join(parts).strip() or "No captured snapshots or indexed project data available for this scope yet."
    diagnostics = {"snapshot_count": len(snapshots), "index_count": len(indexes), "metadata_included": include_metadata, "surface": surface, "project_id": project_id}
    return text, diagnostics


async def save_project_file_upload(file: UploadFile, *, project_id: str = "general", surface: str = "assistant", session_id: str = "") -> dict[str, Any]:
    project_id = _project_id(project_id)
    surface = normalize_surface_id(surface or _surface_for_project(project_id), default="assistant")
    identity = _project_identity(project_id, surface)
    root = ensure_project_brain_dirs(project_id) / "uploads"
    destination = _safe_upload_path(root, file.filename or "upload")
    written = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DOCUMENT_UPLOAD_BYTES:
                    raise ValueError(f"Project file exceeds {MAX_DOCUMENT_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
                handle.write(chunk)
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    suffix = destination.suffix.lower()
    mime = file.content_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
    file_content_hash = _file_hash(destination)
    document_id = _document_id_for_upload(project_id, file.filename or destination.name)
    revision_id = f"rev_{file_content_hash[:24]}"

    # Exact-content duplicate guard. The upload has to reach the backend before
    # its hash is authoritative, but an identical source must not create another
    # physical copy or another canonical ingestion pass.
    for sidecar in root.glob("*.json"):
        if sidecar.name.endswith(".knowledge.json"):
            continue
        existing = read_json(sidecar, {})
        if not isinstance(existing, dict) or str(existing.get("content_hash") or "") != file_content_hash:
            continue
        existing_path = str(existing.get("stored_path") or "")
        if existing_path and (ROOT_DIR / existing_path).exists():
            destination.unlink(missing_ok=True)
            return {
                "ok": True,
                "schema_id": PROJECT_UPLOAD_SCHEMA_ID,
                "phase": "NKB-7",
                "status": "duplicate",
                "duplicate": True,
                "duplicate_of": {
                    "upload_id": existing.get("upload_id") or "",
                    "filename": existing.get("filename") or "",
                    "document_id": existing.get("document_id") or "",
                    "revision_id": existing.get("revision_id") or "",
                    "content_hash": file_content_hash,
                },
                "upload": existing,
                "canonical_memory": existing.get("canonical_memory") or {},
                "project_brain": project_brain_status_payload(project_id=project_id, surface=surface),
            }

    structured: dict[str, Any] = {}
    extracted_text = ""
    extraction: dict[str, Any] = {"status": "not_applicable" if mime.startswith("image/") else "stored_only"}
    if not mime.startswith("image/"):
        try:
            structured = extract_project_document(destination, suffix)
            extraction = {k: v for k, v in structured.items() if k != "blocks"}
            # Compatibility fallback for unsupported/corrupt formats and for legacy
            # extractor extensions. This path is not the persistent-project default.
            if not structured.get("blocks"):
                extracted_text, legacy = extract_document_text(destination, suffix)
                if extracted_text:
                    extraction = {**legacy, "fallback": "assistant_attachment_extractor", "legacy_preview_chars": len(extracted_text)}
        except Exception as exc:
            extracted_text, extraction = "", {"status": "stored_only", "reason": f"project_document_extract_failed: {exc}"}

    record = {
        "schema_id": PROJECT_UPLOAD_SCHEMA_ID,
        "upload_id": slugify(f"upload_{uuid4().hex[:12]}", "upload"),
        "document_id": document_id,
        "revision_id": revision_id,
        "project_id": project_id,  # legacy Scope alias
        "scope_id": identity.get("scope_id") or project_id,
        "surface_id": identity.get("surface_id") or surface,
        "delivery_project_id": identity.get("project_id") or "",
        "surface": surface,
        "session_id": session_id or "",
        "filename": file.filename or destination.name,
        "stored_path": str(destination.relative_to(ROOT_DIR)) if destination.is_relative_to(ROOT_DIR) else str(destination),
        "mime_type": mime,
        "kind": "image" if mime.startswith("image/") else "document",
        "size_bytes": destination.stat().st_size,
        "content_hash": file_content_hash,
        "extraction": extraction,
        "extracted_text_chars": int((structured or {}).get("chars") or len(extracted_text)),
        "created_at": now_iso(),
    }
    canonical = get_project_brain_ingestion_service().ingest_document(
        record,
        extracted_text=extracted_text,
        extraction=extraction,
        structured_extraction=structured,
        identity=identity,
        file_content_hash=file_content_hash,
    )
    manifest = canonical.get("manifest") if isinstance(canonical.get("manifest"), dict) else None
    if manifest:
        manifest_path = _knowledge_manifest_path(destination)
        write_json(manifest_path, manifest)
        record["knowledge_manifest_path"] = str(manifest_path.relative_to(ROOT_DIR)) if manifest_path.is_relative_to(ROOT_DIR) else str(manifest_path)

    context = None
    if canonical.get("fragment_ids"):
        # Compatibility Scope card is deliberately pointer-only. The source body is
        # retrieved from canonical fragments through the Retrieval Gateway.
        headings = []
        for item in (manifest or {}).get("fragments") or []:
            path = " > ".join(item.get("heading_path") or [])
            if path and path not in headings:
                headings.append(path)
            if len(headings) >= 8:
                break
        pointer_lines = [
            f"Project source indexed: {record['filename']}",
            f"Document ID: {document_id}",
            f"Revision: {revision_id}",
            f"Retrieval fragments: {canonical.get('chunk_count') or len(canonical.get('fragment_ids') or [])}",
            "Source body is not duplicated in Scope Knowledge; retrieve relevant source-direct fragments when needed.",
        ]
        if headings:
            pointer_lines.append("Sections: " + "; ".join(headings))
        context = save_context_item_payload({
            "title": f"Indexed project source: {record['filename']}",
            "text": "\n".join(pointer_lines),
            "project_id": project_id,
            "session_id": session_id,
            "surface": surface,
            "source": "assistant_project_brain_upload_pointer",
            "kind": "uploaded_project_doc_pointer",
            "tags": [surface, "upload", suffix.lstrip("."), "source_pointer"],
            "metadata": {
                "upload_id": record["upload_id"], "document_id": document_id, "revision_id": revision_id,
                "stored_path": record["stored_path"], "mime_type": mime, "canonical_source": True,
                "canonical_projection_only": True, "source_pointer_only": True,
            },
            "canonical_projection_only": True,
        })
        record["context_id"] = (context.get("context_item") or {}).get("context_id", "")

    record["canonical_memory"] = {
        "status": canonical.get("status") or "",
        "phase": canonical.get("phase") or "",
        "fragment_ids": canonical.get("fragment_ids") or [],
        "chunk_count": canonical.get("chunk_count") or 0,
        "fact_ids": canonical.get("fact_ids") or [],
        "edge_ids": canonical.get("edge_ids") or [],
        "entity_count": canonical.get("entity_count") or 0,
        "deduplicated_count": canonical.get("deduplicated_count") or 0,
        "superseded_count": canonical.get("superseded_count") or 0,
    }
    write_json(destination.with_suffix(destination.suffix + ".json"), record)
    return {
        "ok": True,
        "schema_id": PROJECT_UPLOAD_SCHEMA_ID,
        "phase": "NKB-7",
        "upload": record,
        "context_item": (context or {}).get("context_item"),
        "canonical_memory": canonical,
        "project_brain": project_brain_status_payload(project_id=project_id, surface=surface),
    }

