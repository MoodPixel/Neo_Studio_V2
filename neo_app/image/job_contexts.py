from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "neo.image.job_context.v1"
INDEX_SCHEMA = "neo.image.job_context_index.v1"
DEFAULT_RETENTION_DAYS = 7
MAX_LOADED_CONTEXTS = 100

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_job_id(job_id: str) -> str:
    cleaned = _SAFE_ID_RE.sub("_", str(job_id or "").strip())
    return cleaned[:160] or "unknown_job"


def runtime_image_job_dir(root_dir: Path) -> Path:
    return Path(root_dir) / "neo_data" / "runtime" / "image_jobs"


def context_path(root_dir: Path, job_id: str) -> Path:
    return runtime_image_job_dir(root_dir) / f"{safe_job_id(job_id)}.json"


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_job_context(context: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    job_id = str(context.get("job_id") or context.get("id") or "").strip()
    now = utc_now()
    existing = context.get("persistence") if isinstance(context.get("persistence"), dict) else {}
    created_at = existing.get("created_at") or context.get("created_at") or now
    normalized = dict(context)
    normalized["job_id"] = job_id
    normalized.setdefault("profile_id", context.get("backend_profile_id") or "")
    normalized.setdefault("backend_profile_id", normalized.get("profile_id") or "")
    normalized.setdefault("provider_id", "")
    normalized.setdefault("subtab", "generate")
    normalized.setdefault("mode", "txt2img")
    normalized.setdefault("params", {})
    normalized.setdefault("extensions", {})
    normalized["persistence"] = {
        "schema": SCHEMA,
        "created_at": created_at,
        "updated_at": now,
        "status": status or existing.get("status") or context.get("status") or "active",
        "storage": "neo_data/runtime/image_jobs",
    }
    return normalized


def save_image_job_context(root_dir: Path, context: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    normalized = normalize_job_context(context, status=status)
    job_id = normalized.get("job_id") or ""
    if not job_id:
        return {"ok": False, "error": "Missing image job_id", "path": ""}
    folder = runtime_image_job_dir(root_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = context_path(root_dir, job_id)
    payload = {"schema": SCHEMA, "context": normalized}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "job_id": job_id, "path": str(path), "context": normalized}


def load_image_job_context(root_dir: Path, job_id: str) -> dict[str, Any] | None:
    path = context_path(root_dir, job_id)
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("context"), dict):
        context = payload["context"]
    elif isinstance(payload, dict):
        context = payload
    else:
        return None
    if not context.get("job_id"):
        context["job_id"] = safe_job_id(job_id)
    return context


def load_recent_image_job_contexts(root_dir: Path, *, max_items: int = MAX_LOADED_CONTEXTS) -> dict[str, dict[str, Any]]:
    folder = runtime_image_job_dir(root_dir)
    if not folder.exists():
        return {}
    paths = sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    loaded: dict[str, dict[str, Any]] = {}
    for path in paths[: max(1, int(max_items))]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        context = payload.get("context") if isinstance(payload, dict) else None
        if not isinstance(context, dict):
            continue
        job_id = str(context.get("job_id") or path.stem).strip()
        if job_id:
            loaded[job_id] = context
    return loaded


def mark_image_job_context(root_dir: Path, job_id: str, *, status: str, updates: dict[str, Any] | None = None) -> dict[str, Any]:
    context = load_image_job_context(root_dir, job_id) or {"job_id": job_id}
    if updates:
        context.update(updates)
    result = save_image_job_context(root_dir, context, status=status)
    return result


def prune_image_job_contexts(root_dir: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> dict[str, Any]:
    folder = runtime_image_job_dir(root_dir)
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    removed: list[str] = []
    kept = 0
    for path in folder.glob("*.json"):
        should_remove = False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            context = payload.get("context") if isinstance(payload, dict) else {}
            persistence = context.get("persistence") if isinstance(context, dict) and isinstance(context.get("persistence"), dict) else {}
            updated = _parse_dt(persistence.get("updated_at")) or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            status = str(persistence.get("status") or context.get("status") or "").lower() if isinstance(context, dict) else ""
            should_remove = updated < cutoff and status in {"completed", "failed", "cancelled", "canceled", "active", ""}
        except Exception:
            should_remove = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff
        if should_remove:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                kept += 1
        else:
            kept += 1
    return {"ok": True, "schema": INDEX_SCHEMA, "retention_days": retention_days, "removed_count": len(removed), "removed": removed, "kept_count": kept, "storage": str(folder)}


def image_job_context_index(root_dir: Path, *, max_items: int = 50) -> dict[str, Any]:
    folder = runtime_image_job_dir(root_dir)
    folder.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)[:max_items]:
        context = load_image_job_context(root_dir, path.stem) or {}
        persistence = context.get("persistence") if isinstance(context.get("persistence"), dict) else {}
        items.append({
            "job_id": context.get("job_id") or path.stem,
            "profile_id": context.get("profile_id") or context.get("backend_profile_id") or "",
            "provider_id": context.get("provider_id") or "",
            "mode": context.get("mode") or "",
            "subtab": context.get("subtab") or "",
            "status": persistence.get("status") or context.get("status") or "active",
            "updated_at": persistence.get("updated_at") or "",
            "path": str(path),
        })
    return {"ok": True, "schema": INDEX_SCHEMA, "storage": str(folder), "count": len(items), "items": items}
