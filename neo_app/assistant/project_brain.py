from __future__ import annotations

import json
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from neo_app.assistant.contracts import compact_json_payload, normalize_surface_id, trim_text
from neo_app.assistant.guides import load_guides, project_surface, search_guides
from neo_app.assistant.store import ASSISTANT_DATA_DIR, now_iso, read_json, save_context_item_payload, slugify, write_json

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

TEXT_UPLOAD_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".log", ".yaml", ".yml", ".xml", ".html", ".htm", ".srt", ".vtt", ".py", ".js", ".jsx", ".ts", ".tsx", ".css"}


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
    root = ensure_project_brain_dirs(project_id)
    stamp = now_iso()
    snapshot_id = slugify(payload.get("snapshot_id") or f"snapshot_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}", "snapshot")
    snapshot_payload = payload.get("surface_context_snapshot") or payload.get("payload") or payload.get("snapshot") or {}
    if not isinstance(snapshot_payload, dict):
        snapshot_payload = {"value": str(snapshot_payload)}
    record = {
        "schema_id": SNAPSHOT_SCHEMA_ID,
        "snapshot_id": snapshot_id,
        "project_id": project_id,
        "surface": surface,
        "title": trim_text(payload.get("title") or f"{surface.title()} state capture", 180),
        "summary": trim_text(payload.get("summary") or "Live surface state captured from Assistant > Project.", 1200),
        "payload": compact_json_payload(snapshot_payload, limit=64000),
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_path(root / "snapshots", snapshot_id), record)
    context = save_context_item_payload({
        "title": record["title"],
        "text": _snapshot_summary(record),
        "project_id": project_id,
        "surface": surface,
        "source": "assistant_project_brain_snapshot",
        "kind": "live_surface_snapshot",
        "tags": [surface, "snapshot", "live_state"],
        "metadata": {"snapshot_id": snapshot_id, "schema_id": SNAPSHOT_SCHEMA_ID},
    })
    return {"ok": True, "schema_id": SNAPSHOT_SCHEMA_ID, "snapshot": record, "context_item": context.get("context_item"), "project_brain": project_brain_status_payload(project_id=project_id)}


def _metadata_roots_for_surface(surface: str) -> list[Path]:
    if surface in {"global", "all", "assistant"}:
        roots: list[Path] = []
        for names in SURFACE_METADATA_ROOTS.values():
            roots.extend(NEO_DATA_DIR / name for name in names)
        return roots
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
    limit = max(1, min(int(payload.get("limit") or 80), 250))
    root = ensure_project_brain_dirs(project_id)
    stamp = now_iso()
    records: list[dict[str, Any]] = []
    for scan_root in _metadata_roots_for_surface(surface):
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
    index_id = slugify(payload.get("index_id") or f"index_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}", "index")
    index = {
        "schema_id": METADATA_INDEX_SCHEMA_ID,
        "index_id": index_id,
        "project_id": project_id,
        "surface": surface,
        "created_at": stamp,
        "record_count": len(records),
        "records": records,
    }
    write_json(_safe_path(root / "memory_index", index_id), index)
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
        "metadata": {"index_id": index_id, "schema_id": METADATA_INDEX_SCHEMA_ID, "record_count": len(records)},
    })
    return {"ok": True, "schema_id": METADATA_INDEX_SCHEMA_ID, "index": index, "context_item": context.get("context_item"), "project_brain": project_brain_status_payload(project_id=project_id)}


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
    root = ensure_project_brain_dirs(project_id)
    snapshots = list_project_brain_snapshots(project_id, limit=20)
    indexes = list_project_brain_indexes(project_id, limit=20)
    uploads = list((root / "uploads").glob("*")) if (root / "uploads").exists() else []
    guides = search_guides("", project_id=project_id, surface=surface or _surface_for_project(project_id), limit=12)
    return {
        "ok": True,
        "schema_id": PROJECT_BRAIN_SCHEMA_ID,
        "project_id": project_id,
        "surface": _surface_for_project(project_id, surface),
        "root": str(root),
        "counts": {
            "snapshots": len(snapshots),
            "indexes": len(indexes),
            "uploads": len(uploads),
            "built_in_guides_visible": guides.get("total_available") or guides.get("count") or 0,
        },
        "latest_snapshots": [{k: row.get(k) for k in ("snapshot_id", "surface", "title", "created_at")} for row in snapshots[:6]],
        "latest_indexes": [{"index_id": row.get("index_id"), "surface": row.get("surface"), "record_count": row.get("record_count"), "created_at": row.get("created_at")} for row in indexes[:6]],
    }


def rebuild_project_brain_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _project_id(payload.get("project_id") or "general")
    surface = normalize_surface_id(payload.get("surface") or _surface_for_project(project_id), default="assistant")
    index = index_project_data_payload({"project_id": project_id, "surface": surface, "limit": payload.get("limit") or 80})
    guide_count = len(load_guides())
    status = project_brain_status_payload(project_id=project_id, surface=surface)
    report = {
        "schema_id": PROJECT_BRAIN_SCHEMA_ID,
        "project_id": project_id,
        "surface": surface,
        "rebuilt_at": now_iso(),
        "guide_count": guide_count,
        "metadata_index_id": (index.get("index") or {}).get("index_id"),
        "metadata_record_count": (index.get("index") or {}).get("record_count"),
        "status": status,
    }
    root = ensure_project_brain_dirs(project_id)
    write_json(_safe_path(root / "reports", f"rebuild_{surface}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"), report)
    return {"ok": True, "report": report, "project_brain": status}


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
    root = ensure_project_brain_dirs(project_id) / "uploads"
    destination = _safe_upload_path(root, file.filename or "upload")
    with destination.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    suffix = destination.suffix.lower()
    mime = file.content_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
    text_preview = ""
    if suffix in TEXT_UPLOAD_EXTENSIONS:
        try:
            text_preview = trim_text(destination.read_text(encoding="utf-8", errors="replace"), 18000)
        except Exception:
            text_preview = ""
    record = {
        "schema_id": PROJECT_UPLOAD_SCHEMA_ID,
        "upload_id": slugify(f"upload_{uuid4().hex[:12]}", "upload"),
        "project_id": project_id,
        "surface": surface,
        "session_id": session_id or "",
        "filename": file.filename or destination.name,
        "stored_path": str(destination.relative_to(ROOT_DIR)) if destination.is_relative_to(ROOT_DIR) else str(destination),
        "mime_type": mime,
        "kind": "image" if mime.startswith("image/") else ("document" if text_preview else "file"),
        "size_bytes": destination.stat().st_size,
        "created_at": now_iso(),
    }
    write_json(destination.with_suffix(destination.suffix + ".json"), record)
    if text_preview:
        context = save_context_item_payload({
            "title": f"Uploaded project doc: {record['filename']}",
            "text": text_preview,
            "project_id": project_id,
            "session_id": session_id,
            "surface": surface,
            "source": "assistant_project_upload",
            "kind": "uploaded_project_doc",
            "tags": [surface, "upload", suffix.lstrip(".")],
            "metadata": {"upload_id": record["upload_id"], "stored_path": record["stored_path"], "mime_type": mime},
        })
        record["context_id"] = (context.get("context_item") or {}).get("context_id", "")
    else:
        context = None
    return {"ok": True, "schema_id": PROJECT_UPLOAD_SCHEMA_ID, "upload": record, "context_item": (context or {}).get("context_item"), "project_brain": project_brain_status_payload(project_id=project_id, surface=surface)}
