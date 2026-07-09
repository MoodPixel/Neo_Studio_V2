from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.schema import RoleplayStudioProject, RoleplayStudioSource, RoleplayStudioState
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.text_backend import resolve_roleplay_text_backend
from neo_app.roleplay.runtime import runtime_state_payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:72] or "studio-record"


def _projects_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "projects"


def _source_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "source_documents"


def _runtime_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "runtime_bundles"


def ensure_studio_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    _projects_dir().mkdir(parents=True, exist_ok=True)
    _source_dir().mkdir(parents=True, exist_ok=True)
    _runtime_dir().mkdir(parents=True, exist_ok=True)


def _read_project(path: Path) -> RoleplayStudioProject | None:
    try:
        return RoleplayStudioProject(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _read_source(path: Path) -> RoleplayStudioSource | None:
    try:
        return RoleplayStudioSource(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_studio_projects() -> list[RoleplayStudioProject]:
    ensure_studio_storage()
    projects = [_read_project(path) for path in sorted(_projects_dir().glob("*.json"))]
    return sorted([project for project in projects if project], key=lambda item: item.updated_at or item.created_at, reverse=True)


def list_studio_sources(project_id: str | None = None) -> list[RoleplayStudioSource]:
    ensure_studio_storage()
    sources = [_read_source(path) for path in sorted(_source_dir().glob("*.json"))]
    valid = [source for source in sources if source]
    if project_id:
        valid = [source for source in valid if source.project_id == project_id]
    return sorted(valid, key=lambda item: item.updated_at or item.created_at, reverse=True)


def create_studio_project(payload: dict[str, Any]) -> RoleplayStudioProject:
    ensure_studio_storage()
    title = str(payload.get("title") or payload.get("project_title") or "Untitled Roleplay Project").strip() or "Untitled Roleplay Project"
    now = _now()
    project_id = str(payload.get("project_id") or f"{_slug(title)}-{uuid.uuid4().hex[:8]}")
    path = _projects_dir() / f"{project_id}.json"
    existing = _read_project(path)
    project = RoleplayStudioProject(
        project_id=project_id,
        title=title,
        description=str(payload.get("description") or payload.get("project_description") or ""),
        created_at=existing.created_at if existing else now,
        updated_at=now,
        storage_path=_relative_to_root(path),
    )
    path.write_text(json.dumps(model_to_dict(project), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return project


def save_studio_source(payload: dict[str, Any]) -> RoleplayStudioSource:
    ensure_studio_storage()
    title = str(payload.get("title") or payload.get("source_title") or "Untitled Source").strip() or "Untitled Source"
    body = str(payload.get("body") or payload.get("source_body") or "")
    now = _now()
    source_id = str(payload.get("source_id") or f"{_slug(title)}-{uuid.uuid4().hex[:8]}")
    path = _source_dir() / f"{source_id}.json"
    existing = _read_source(path)
    source = RoleplayStudioSource(
        source_id=source_id,
        project_id=str(payload.get("project_id") or ""),
        title=title,
        source_type=str(payload.get("source_type") or "text"),
        body_preview=body[:280],
        created_at=existing.created_at if existing else now,
        updated_at=now,
        storage_path=_relative_to_root(path),
    )
    stored = model_to_dict(source)
    stored["body"] = body
    stored["meta"] = payload.get("meta") or {}
    path.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return source


def studio_state_payload(active_view: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
    ensure_studio_storage()
    projects = list_studio_projects()
    sources = list_studio_sources()
    text_backend = resolve_roleplay_text_backend(profile_id)
    runtime_state = runtime_state_payload()
    try:
        from neo_app.roleplay.novel_memory import novel_memory_state_payload
        novel_state = novel_memory_state_payload()
    except Exception as exc:
        novel_state = {"status": "unavailable", "error": str(exc)}
    state = RoleplayStudioState(
        active_view=active_view or "guide",
        projects=projects,
        sources=sources,
        guide={
            "purpose": "Studio is the V2-native prep lane for projects, sources, shared Scene defaults, libraries, compile planning, runtime packets, and inspection.",
            "phase": "studio_v1_ui_parity",
            "safe_next_steps": ["project records", "source intake", "novel source memory compile", "library browse", "runtime compile design"],
        },
        advanced={
            "status": "foundation",
            "model_note": "Scene generation uses the shared Admin text backend profile bridge. Assist moved to the Assistant surface.",
            "controls": ["max_tokens", "temperature", "top_p", "top_k", "author_notes", "continuity_mode"],
        },
        libraries={
            "status": "placeholder",
            "roots": [
                _relative_to_root(ROLEPLAY_DATA_ROOT / "packages"),
                _relative_to_root(ROLEPLAY_DATA_ROOT / "imports"),
                _relative_to_root(ROLEPLAY_DATA_ROOT / "exports"),
            ],
        },
        compile={
            "status": "ready",
            "ready": True,
            "phase": "phase5_novel_memory_path",
            "bundle_count": runtime_state.get("bundle_count", 0),
            "latest_bundle_id": runtime_state.get("latest_bundle_id", ""),
            "novel_source_count": novel_state.get("source_count", 0),
            "novel_chunk_count": novel_state.get("chunk_count", 0),
            "novel_canon_record_count": novel_state.get("canon_record_count", 0),
            "planned_inputs": ["project", "sources", "source_chunks", "forge_records", "canon_records", "memory_fragments", "admin_engine_snapshot"],
        },
        inspector={
            "project_count": len(projects),
            "source_count": len(sources),
            "text_backend_ready": bool(text_backend.get("ready")),
            "active_text_backend_profile_id": text_backend.get("active_profile_id") or "",
            "assist_surface": "assistant",
            "engine_source": "admin",
            "embedding_reranker_source": "admin",
            "project_root": _relative_to_root(_projects_dir()),
            "source_root": _relative_to_root(_source_dir()),
            "runtime_root": _relative_to_root(_runtime_dir()),
            "runtime_bundle_count": runtime_state.get("bundle_count", 0),
            "latest_runtime_bundle_id": runtime_state.get("latest_bundle_id", ""),
            "phase": "phase5_novel_memory_path",
            "novel_source_count": novel_state.get("source_count", 0),
            "novel_chunk_count": novel_state.get("chunk_count", 0),
            "novel_canon_record_count": novel_state.get("canon_record_count", 0),
        },
    )
    state.runtime.bundle_root = _relative_to_root(_runtime_dir())
    state.runtime.status = "deferred"
    state.runtime.compile_ready = True
    result = model_to_dict(state)
    result["runtime"] = dict(runtime_state)
    result["runtime"]["status"] = "deferred"
    result["runtime"]["compile_ready"] = False
    result["runtime"]["phase9_compile_available"] = True
    result["text_backend"] = text_backend
    result["engine"]["text_backend"] = text_backend
    result["engine"]["ready"] = bool(text_backend.get("ready"))
    result["engine"]["active_profile_id"] = text_backend.get("active_profile_id") or ""
    result["engine"]["selection_source"] = text_backend.get("selection_source") or "none"
    result["novel"] = novel_state
    result["source_memory_path"] = novel_state
    return result


def create_studio_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project = create_studio_project(payload)
    return {
        "schema_id": "neo.roleplay.studio.project.write.v1",
        "surface_id": "roleplay",
        "tab_id": "studio",
        "status": "saved",
        "project": model_to_dict(project),
        "studio": studio_state_payload("project"),
    }


def save_studio_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = save_studio_source(payload)
    return {
        "schema_id": "neo.roleplay.studio.source.write.v1",
        "surface_id": "roleplay",
        "tab_id": "studio",
        "status": "saved",
        "source": model_to_dict(source),
        "studio": studio_state_payload("source"),
    }
