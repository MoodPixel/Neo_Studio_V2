from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.runtime_data import ASSISTANT_BUILTIN_SCOPES, ensure_assistant_scope_seed
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot
from neo_app.assistant.contracts import (
    MAX_CONTEXT_TEXT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    ASSISTANT_CONTRACT_VERSION,
    clamp_retrieval_profile,
    compact_json_payload,
    contract_lock_payload,
    normalize_suggested_action,
    normalize_surface_id,
    trim_text,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
ASSISTANT_DATA_DIR = ROOT_DIR / "neo_data" / "assistant"
PROJECTS_DIR = ASSISTANT_DATA_DIR / "projects"
SESSIONS_DIR = ASSISTANT_DATA_DIR / "sessions"
CAPTURES_DIR = ASSISTANT_DATA_DIR / "memory_captures"
CONTEXT_DIR = ASSISTANT_DATA_DIR / "context_items"
SURFACE_CONTEXT_DIR = ASSISTANT_DATA_DIR / "surface_context"
ATTACHMENTS_DIR = ASSISTANT_DATA_DIR / "attachments"
PROJECT_BRAIN_DIR = ASSISTANT_DATA_DIR / "project_brain"
PROFILE_PATH = ASSISTANT_DATA_DIR / "assistant_profile.json"
PROJECTS_INDEX_PATH = ASSISTANT_DATA_DIR / "assistant_projects_index.json"
SESSIONS_INDEX_PATH = ASSISTANT_DATA_DIR / "assistant_sessions_index.json"

def ensure_assistant_runtime_dirs() -> None:
    """Create Assistant runtime folders lazily under neo_data.

    Importing this module should not create user/runtime state. The folders are
    created by the first Assistant store operation or the app bootstrap.
    """

    for _path in (ASSISTANT_DATA_DIR, PROJECTS_DIR, SESSIONS_DIR, CAPTURES_DIR, CONTEXT_DIR, SURFACE_CONTEXT_DIR, ATTACHMENTS_DIR, PROJECT_BRAIN_DIR):
        _path.mkdir(parents=True, exist_ok=True)

DEFAULT_PROFILE: dict[str, Any] = {
    "profile_id": "neo_assistant_v2",
    "display_name": "Neo Assistant",
    "default_project_id": "general",
    "default_mode": "general",
    "memory_source": "memory_engine",
    "legacy_memory_source": "admin_engine",
    "retrieval_profile": "smart",
    "tone": "clear, practical, project-aware",
    "updated_at": "",
}

DEFAULT_PROJECT: dict[str, Any] = {
    "project_id": "general",
    "scope_id": "general",
    "name": "General Assistant",
    "type": "assistant_workspace",
    "description": "Default Assistant workspace for uncategorized questions, planning, and cross-surface coordination.",
    "notes": "",
    "status": "active",
    "created_at": "",
    "updated_at": "",
}


def _assistant_store_log_summary(record: dict[str, Any] | None = None, *, action: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    return {
        "action": str(action or ""),
        "project_id": str(record.get("project_id") or extra.get("project_id") or "general"),
        "scope_id": str(record.get("scope_id") or record.get("project_id") or extra.get("scope_id") or extra.get("project_id") or "general"),
        "session_id": str(record.get("session_id") or extra.get("session_id") or ""),
        "surface": str(record.get("surface") or extra.get("surface") or "assistant"),
        "kind": str(record.get("kind") or extra.get("kind") or ""),
        "title": str(record.get("title") or extra.get("title") or "")[:160],
        "text_chars": len(str(record.get("text") or extra.get("text") or "")),
        "payload_keys": sorted((record.get("payload") or {}).keys()) if isinstance(record.get("payload"), dict) else [],
    }


def _safe_log_assistant_store_event(event: str, *, run_id: str = "", payload: dict[str, Any] | None = None, level: str = "INFO") -> None:
    try:
        log_surface_event("assistant", event, run_id=run_id or None, level=level, payload=payload or {})
    except Exception:
        pass



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, fallback: str = "item") -> str:
    clean = re.sub(r"[^a-zA-Z0-9._ -]+", "", str(value or "").strip())
    clean = re.sub(r"\s+", "_", clean).strip("._- ").lower()
    return clean[:80] or fallback


def read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _safe_record_path(root: Path, record_id: str, fallback_prefix: str) -> Path:
    root = root.resolve()
    safe_id = slugify(record_id, f"{fallback_prefix}_{uuid4().hex[:10]}")
    path = (root / f"{safe_id}.json").resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid Assistant record path")
    return path


def ensure_assistant_storage() -> None:
    ensure_assistant_runtime_dirs()
    created = now_iso()
    if not PROFILE_PATH.exists():
        profile = dict(DEFAULT_PROFILE)
        profile["updated_at"] = created
        write_json(PROFILE_PATH, profile)
    if not PROJECTS_INDEX_PATH.exists():
        project = dict(DEFAULT_PROJECT)
        project["created_at"] = created
        project["updated_at"] = created
        write_json(PROJECTS_INDEX_PATH, {"projects": [project], "updated_at": created})
        write_json(_safe_record_path(PROJECTS_DIR, project["project_id"], "project"), project)
    if not SESSIONS_INDEX_PATH.exists():
        write_json(SESSIONS_INDEX_PATH, {"sessions": [], "updated_at": created})
    ensure_assistant_scope_seed(ROOT_DIR)


def assistant_profile() -> dict[str, Any]:
    ensure_assistant_storage()
    profile = read_json(PROFILE_PATH, {})
    merged = {**DEFAULT_PROFILE, **(profile if isinstance(profile, dict) else {})}
    if str(merged.get("memory_source") or "") == "admin_engine":
        merged["memory_source"] = "memory_engine"
        merged.setdefault("legacy_memory_source", "admin_engine")
    return merged


def save_assistant_profile(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_assistant_storage()
    current = assistant_profile()
    allowed = {"display_name", "default_project_id", "default_mode", "memory_source", "retrieval_profile", "tone"}
    for key in allowed:
        if key in payload:
            current[key] = payload[key]
    current["updated_at"] = now_iso()
    write_json(PROFILE_PATH, current)
    if "default_project_id" in payload:
        _safe_log_assistant_store_event("assistant.scope.changed", run_id=str(current.get("default_project_id") or "general"), payload={"default_scope_id": current.get("default_project_id") or "general", "memory_source": current.get("memory_source") or ""})
    return {"ok": True, "profile": current}


def list_projects() -> list[dict[str, Any]]:
    ensure_assistant_storage()
    index = read_json(PROJECTS_INDEX_PATH, {"projects": []})
    projects = index.get("projects") if isinstance(index, dict) else []
    if not isinstance(projects, list):
        projects = []
    if not any((p or {}).get("project_id") == "general" for p in projects):
        project = dict(DEFAULT_PROJECT)
        stamp = now_iso()
        project["created_at"] = stamp
        project["updated_at"] = stamp
        projects.insert(0, project)
        write_json(PROJECTS_INDEX_PATH, {"projects": projects, "updated_at": stamp})
    return sorted(projects, key=lambda item: (item.get("name") or "").lower())


def _write_projects(projects: list[dict[str, Any]]) -> None:
    write_json(PROJECTS_INDEX_PATH, {"projects": projects, "updated_at": now_iso()})


def get_project(project_id: str) -> dict[str, Any] | None:
    for project in list_projects():
        if project.get("project_id") == project_id:
            record = read_json(_safe_record_path(PROJECTS_DIR, project_id, "project"), {})
            if isinstance(record, dict) and record:
                return {**project, **record}
            return project
    return None


def create_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_assistant_storage()
    name = str(payload.get("name") or payload.get("title") or "New project").strip() or "New project"
    base_id = slugify(payload.get("project_id") or name, "project")
    existing = {p.get("project_id") for p in list_projects()}
    project_id = base_id
    if project_id in existing:
        project_id = f"{base_id}_{uuid4().hex[:8]}"
    stamp = now_iso()
    project = {
        "project_id": project_id,
        "name": name,
        "type": str(payload.get("type") or "general"),
        "description": str(payload.get("description") or ""),
        "notes": str(payload.get("notes") or ""),
        "status": "active",
        "created_at": stamp,
        "updated_at": stamp,
    }
    projects = [p for p in list_projects() if p.get("project_id") != project_id]
    projects.append(project)
    _write_projects(projects)
    write_json(_safe_record_path(PROJECTS_DIR, project_id, "project"), project)
    _safe_log_assistant_store_event("assistant.scope.created", run_id=project_id, payload=_assistant_store_log_summary(project, action="scope_created"))
    return {"ok": True, "project": project, "projects": list_projects()}


def save_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        return create_project_payload(payload)
    current = get_project(project_id) or {"project_id": project_id, "created_at": now_iso(), "status": "active"}
    for key in ("name", "type", "description", "notes", "status"):
        if key in payload:
            current[key] = payload[key]
    current.setdefault("name", project_id)
    current["updated_at"] = now_iso()
    projects = [p for p in list_projects() if p.get("project_id") != project_id]
    projects.append({k: current.get(k) for k in ("project_id", "name", "type", "description", "notes", "status", "created_at", "updated_at")})
    _write_projects(projects)
    write_json(_safe_record_path(PROJECTS_DIR, project_id, "project"), current)
    _safe_log_assistant_store_event("assistant.scope.saved", run_id=project_id, payload=_assistant_store_log_summary(current, action="scope_saved"))
    return {"ok": True, "project": current, "projects": list_projects()}


def rename_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not project_id or not name:
        raise ValueError("project_id and name are required")
    return save_project_payload({"project_id": project_id, "name": name})


def delete_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    if project_id == "general":
        raise ValueError("The General project cannot be deleted")
    projects = [p for p in list_projects() if p.get("project_id") != project_id]
    _write_projects(projects)
    path = _safe_record_path(PROJECTS_DIR, project_id, "project")
    if path.exists():
        path.unlink()
    return {"ok": True, "project_id": project_id, "projects": list_projects()}


def list_sessions() -> list[dict[str, Any]]:
    ensure_assistant_storage()
    index = read_json(SESSIONS_INDEX_PATH, {"sessions": []})
    sessions = index.get("sessions") if isinstance(index, dict) else []
    if not isinstance(sessions, list):
        sessions = []
    return sorted(sessions, key=lambda item: item.get("updated_at") or "", reverse=True)


def _write_sessions(sessions: list[dict[str, Any]]) -> None:
    write_json(SESSIONS_INDEX_PATH, {"sessions": sessions, "updated_at": now_iso()})


def session_summary(record: dict[str, Any]) -> dict[str, Any]:
    messages = record.get("messages") if isinstance(record.get("messages"), list) else []
    preview = ""
    for message in reversed(messages):
        text = str((message or {}).get("text") or "").strip()
        if text:
            preview = text[:180]
            break
    return {
        "session_id": record.get("session_id"),
        "title": record.get("title") or "New assistant chat",
        "project_id": record.get("project_id") or "general",
        "mode": record.get("mode") or "general",
        "message_count": len(messages),
        "preview": preview,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def get_session(session_id: str) -> dict[str, Any] | None:
    path = _safe_record_path(SESSIONS_DIR, session_id, "session")
    record = read_json(path, {})
    return record if isinstance(record, dict) and record.get("session_id") else None


def create_session_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_assistant_storage()
    payload = payload or {}
    stamp = now_iso()
    session_id = slugify(payload.get("session_id") or f"chat_{uuid4().hex[:12]}", "chat")
    record = {
        "session_id": session_id,
        "title": str(payload.get("title") or "New assistant chat"),
        "project_id": str(payload.get("project_id") or assistant_profile().get("default_project_id") or "general"),
        "mode": str(payload.get("mode") or assistant_profile().get("default_mode") or "general"),
        "messages": payload.get("messages") if isinstance(payload.get("messages"), list) else [],
        "context_items": payload.get("context_items") if isinstance(payload.get("context_items"), list) else [],
        "memory_summary": str(payload.get("memory_summary") or ""),
        "draft": str(payload.get("draft") or ""),
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_record_path(SESSIONS_DIR, session_id, "session"), record)
    sessions = [s for s in list_sessions() if s.get("session_id") != session_id]
    sessions.insert(0, session_summary(record))
    _write_sessions(sessions)
    return {"ok": True, "session": record, "sessions": list_sessions()}


def save_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return create_session_payload(payload)
    current = get_session(session_id) or {
        "session_id": session_id,
        "title": "New assistant chat",
        "project_id": "general",
        "mode": "general",
        "messages": [],
        "context_items": [],
        "created_at": now_iso(),
    }
    for key in ("title", "project_id", "mode", "messages", "context_items", "memory_summary", "draft", "last_diagnostics"):
        if key in payload:
            value = payload[key]
            if key in {"messages", "context_items"} and not isinstance(value, list):
                value = []
            current[key] = value
    current["updated_at"] = now_iso()
    write_json(_safe_record_path(SESSIONS_DIR, session_id, "session"), current)
    sessions = [s for s in list_sessions() if s.get("session_id") != session_id]
    sessions.insert(0, session_summary(current))
    _write_sessions(sessions)
    return {"ok": True, "session": current, "sessions": list_sessions()}


def load_session_payload(session_id: str) -> dict[str, Any]:
    record = get_session(session_id)
    if not record:
        raise FileNotFoundError("Assistant session not found")
    return {"ok": True, "session": record}


def rename_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not session_id or not title:
        raise ValueError("session_id and title are required")
    return save_session_payload({"session_id": session_id, "title": title})



def clear_session_messages_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    current = get_session(session_id)
    if not current:
        raise FileNotFoundError("Assistant session not found")
    current["messages"] = []
    current["draft"] = ""
    current["memory_summary"] = ""
    current["last_diagnostics"] = {
        "schema_id": "neo.assistant.session_clear.v1",
        "status": "messages_cleared",
        "cleared_at": now_iso(),
    }
    return save_session_payload(current)


def delete_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    path = _safe_record_path(SESSIONS_DIR, session_id, "session")
    if path.exists():
        path.unlink()
    sessions = [s for s in list_sessions() if s.get("session_id") != session_id]
    _write_sessions(sessions)
    return {"ok": True, "session_id": session_id, "sessions": list_sessions()}



def _refresh_assistant_memory_engine_index(limit: int | None = None) -> dict[str, Any]:
    try:
        from neo_app.memory.service import get_memory_service
        return get_memory_service().index_source("assistant_memory", force=True, limit=limit)
    except Exception as exc:
        return {"ok": False, "status": "index_failed", "message": str(exc)[:500]}

def manual_memory_capture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_assistant_storage()
    text = str(payload.get("text") or payload.get("content") or "").strip()
    if not text:
        raise ValueError("Memory capture text is required")
    stamp = now_iso()
    capture_id = slugify(payload.get("capture_id") or f"capture_{uuid4().hex[:12]}", "capture")
    record = {
        "capture_id": capture_id,
        "title": str(payload.get("title") or text[:80] or "Assistant memory capture"),
        "text": text,
        "project_id": str(payload.get("project_id") or "general"),
        "session_id": str(payload.get("session_id") or ""),
        "namespace": str(payload.get("namespace") or "assistant"),
        "source": "assistant_manual_capture",
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_record_path(CAPTURES_DIR, capture_id, "capture"), record)
    admin_memory = {"ok": False, "message": "Centralized memory bridge unavailable."}
    try:
        from neo_app.assistant.memory_adapter import record_assistant_capture
        admin_memory = record_assistant_capture(record)
    except Exception as exc:
        admin_memory = {"ok": False, "message": f"Centralized memory write failed: {exc}"}
    record["admin_memory_event_id"] = ((admin_memory.get("event") or {}).get("event_id") if isinstance(admin_memory, dict) else "") or ""
    write_json(_safe_record_path(CAPTURES_DIR, capture_id, "capture"), record)
    memory_engine_index = _refresh_assistant_memory_engine_index(limit=5)
    return {
        "ok": True,
        "capture": record,
        "admin_memory": admin_memory,
        "memory_engine_index": memory_engine_index,
        "message": "Saved to Assistant memory and refreshed the Memory Engine index." if memory_engine_index.get("ok") else "Saved locally. Memory Engine index needs attention.",
    }


def list_memory_captures(limit: int = 50) -> list[dict[str, Any]]:
    ensure_assistant_storage()
    captures: list[dict[str, Any]] = []
    for path in sorted(CAPTURES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("capture_id"):
            captures.append(record)
        if len(captures) >= limit:
            break
    return captures


def assistant_search_payload(query: str = "", project_id: str = "") -> dict[str, Any]:
    ensure_assistant_storage()
    needle = str(query or "").strip().lower()
    sessions = list_sessions()
    projects = list_projects()
    captures = list_memory_captures()
    if project_id:
        sessions = [s for s in sessions if s.get("project_id") == project_id]
        captures = [c for c in captures if c.get("project_id") == project_id]
    if needle:
        def match(obj: dict[str, Any]) -> bool:
            haystack = json.dumps(obj, ensure_ascii=False).lower()
            return needle in haystack
        sessions = [s for s in sessions if match(s)]
        projects = [p for p in projects if match(p)]
        captures = [c for c in captures if match(c)]
    return {"ok": True, "query": query, "project_id": project_id, "sessions": sessions, "projects": projects, "memory_captures": captures}


def context_pack_preview_payload(session_id: str = "", project_id: str = "", message: str = "", retrieval_profile: str = "smart", surface: str = "") -> dict[str, Any]:
    ensure_assistant_storage()
    from neo_app.assistant.context_pack import build_context_pack
    return build_context_pack(session_id=session_id, project_id=project_id, message=message, retrieval_profile=clamp_retrieval_profile(retrieval_profile), active_surface=surface)


def assistant_bootstrap_payload() -> dict[str, Any]:
    ensure_assistant_storage()
    profile = assistant_profile()
    sessions = list_sessions()
    projects = list_projects()
    active_session = get_session(sessions[0]["session_id"]) if sessions else None
    return {
        "ok": True,
        "profile": profile,
        "projects": projects,
        "sessions": sessions,
        "active_session": active_session,
        "memory_captures": list_memory_captures(30),
        "context_items": list_context_items(limit=40),
        "surface_context": list_surface_context_payload(limit=30).get("surface_context", []),
        "storage": {
            "data_root": str(ASSISTANT_DATA_DIR),
            "projects_dir": str(PROJECTS_DIR),
            "sessions_dir": str(SESSIONS_DIR),
            "profile_path": str(PROFILE_PATH),
            "attachments_dir": str(ATTACHMENTS_DIR),
            "project_brain_dir": str(PROJECT_BRAIN_DIR),
            "guides_dir": str(ROOT_DIR / "guides"),
        },
        "capabilities": {
            "surface": "active",
            "chat_storage": True,
            "project_storage": True,
            "assistant_scope_seed": True,
            "builtin_scope_ids": [scope.get("project_id") for scope in ASSISTANT_BUILTIN_SCOPES],
            "context_pack_preview": True,
            "model_runtime": True,
            "centralized_memory_writeback": True,
            "context_pack_memory_engine": True,
            "context_pack_admin_memory": True,
            "cross_surface_context": True,
            "project_knowledge": True,
            "safe_tool_catalog": True,
            "assistant_attachments": True,
            "assistant_image_attachments": True,
            "assistant_document_attachments": True,
            "surface_project_context_provider": True,
            "live_surface_context_in_context_pack": True,
            "guide_aware_project_brain": True,
            "built_in_guides": True,
            "project_state_capture": True,
            "project_metadata_indexing": True,
            "project_file_uploads": True,
        },
        "thinking_layer": {
            "model_runtime_bridge": True,
            "memory_engine_bridge": True,
            "admin_engine_memory_bridge": True,
            "context_pack_builder": True,
            "guide_registry": True,
            "project_brain_builder": True,
            "retrieval_profiles": ["fast", "smart", "deep"],
        },
        "heart_layer": {
            "cross_surface_context": True,
            "project_knowledge_import": True,
            "safe_tool_catalog": True,
            "surface_guide_actions": True,
            "attachment_uploads": True,
            "surface_context_providers": ["image", "video", "prompt_captioning", "roleplay", "voice"],
            "project_brain_layers": ["built_in_guides", "live_snapshots", "metadata_indexes", "uploaded_project_files", "scope_knowledge"],
        },
        "lock_layer": {
            **contract_lock_payload(),
            "assistant_no_generic_workshell": True,
            "route_contract_tests": True,
            "safe_tool_execution_only": True,
            "context_diagnostics_required": True,
        },
        "deferred_to_wave4": [
            "dangerous local command execution",
            "automatic patch apply",
            "external connector actions",
        ],
    }



def _coerce_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def save_context_item_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Save a project/thread knowledge card for Assistant context packs."""
    ensure_assistant_storage()
    text = trim_text(payload.get("text") or payload.get("content") or payload.get("body") or "", MAX_CONTEXT_TEXT_CHARS)
    if not text:
        raise ValueError("Context text is required")
    stamp = now_iso()
    context_id = slugify(payload.get("context_id") or f"context_{uuid4().hex[:12]}", "context")
    project_id = str(payload.get("project_id") or "general").strip() or "general"
    record = {
        "context_id": context_id,
        "title": trim_text(payload.get("title") or text[:80] or "Assistant context item", MAX_TITLE_CHARS),
        "text": text,
        "project_id": project_id,
        "session_id": str(payload.get("session_id") or ""),
        "surface": normalize_surface_id(payload.get("surface") or "assistant"),
        "source": str(payload.get("source") or "assistant_context_import"),
        "kind": normalize_surface_id(payload.get("kind") or "project_knowledge", default="project_knowledge"),
        "tags": _coerce_tags(payload.get("tags") or []),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_record_path(CONTEXT_DIR, context_id, "context"), record)
    if project_id:
        project = get_project(project_id) or {"project_id": project_id, "name": project_id, "created_at": stamp, "status": "active"}
        linked = project.get("context_item_ids") if isinstance(project.get("context_item_ids"), list) else []
        if context_id not in linked:
            linked.append(context_id)
        project["context_item_ids"] = linked[-100:]
        project.setdefault("name", project_id)
        save_project_payload(project)
    memory_engine_index = _refresh_assistant_memory_engine_index(limit=5)
    summary = _assistant_store_log_summary(record, action="scope_knowledge_saved")
    _safe_log_assistant_store_event("assistant.scope_knowledge.saved", run_id=project_id or record.get("context_id") or "assistant", payload=summary)
    try:
        record_surface_snapshot("assistant", "neo_last_scope_knowledge.json", summary, run_id=project_id or record.get("context_id") or "assistant")
    except Exception:
        pass
    return {"ok": True, "context_item": record, "context_items": list_context_items(project_id=project_id), "memory_engine_index": memory_engine_index}


def list_context_items(project_id: str = "", session_id: str = "", surface: str = "", limit: int = 80) -> list[dict[str, Any]]:
    ensure_assistant_storage()
    records: list[dict[str, Any]] = []
    for path in sorted(CONTEXT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        record = read_json(path, {})
        if not isinstance(record, dict) or not record.get("context_id"):
            continue
        if project_id and record.get("project_id") != project_id:
            continue
        if session_id and record.get("session_id") != session_id:
            continue
        if surface and record.get("surface") != surface:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records


def context_items_payload(project_id: str = "", session_id: str = "", surface: str = "") -> dict[str, Any]:
    return {"ok": True, "context_items": list_context_items(project_id=project_id, session_id=session_id, surface=surface)}


def save_surface_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Record an Ask Assistant handoff from any Neo surface and attach it to project/session context."""
    ensure_assistant_storage()
    surface = normalize_surface_id(payload.get("surface") or payload.get("surface_id") or "unknown", default="unknown")
    stamp = now_iso()
    handoff_id = slugify(payload.get("handoff_id") or f"handoff_{surface}_{uuid4().hex[:10]}", "handoff")
    project_id = str(payload.get("project_id") or assistant_profile().get("default_project_id") or "general").strip() or "general"
    summary = trim_text(payload.get("summary") or payload.get("title") or f"Context from {surface}", MAX_SUMMARY_CHARS)
    record = {
        "handoff_id": handoff_id,
        "surface": surface,
        "subtab": str(payload.get("subtab") or payload.get("subtab_id") or ""),
        "record_id": str(payload.get("record_id") or ""),
        "title": trim_text(payload.get("title") or summary or f"{surface} context", MAX_TITLE_CHARS),
        "summary": summary,
        "payload": compact_json_payload(payload.get("payload") if isinstance(payload.get("payload"), dict) else {}),
        "suggested_action": normalize_suggested_action(payload.get("suggested_action") or "explain"),
        "project_id": project_id,
        "session_id": str(payload.get("session_id") or ""),
        "created_at": stamp,
        "updated_at": stamp,
    }
    write_json(_safe_record_path(SURFACE_CONTEXT_DIR, handoff_id, "handoff"), record)
    context_text = "\n".join([part for part in [
        f"Surface: {record['surface']}",
        f"Subtab: {record['subtab']}" if record.get("subtab") else "",
        f"Suggested action: {record['suggested_action']}",
        f"Summary: {record['summary']}",
        json.dumps(record.get("payload") or {}, ensure_ascii=False) if record.get("payload") else "",
    ] if part]).strip()
    context_result = save_context_item_payload({
        "title": record["title"],
        "text": context_text,
        "project_id": project_id,
        "session_id": record.get("session_id") or "",
        "surface": surface,
        "source": "assistant_surface_handoff",
        "kind": "surface_context",
        "metadata": {"handoff_id": handoff_id, "suggested_action": record["suggested_action"], "contract_version": ASSISTANT_CONTRACT_VERSION},
    })
    record["context_id"] = (context_result.get("context_item") or {}).get("context_id", "")
    write_json(_safe_record_path(SURFACE_CONTEXT_DIR, handoff_id, "handoff"), record)
    summary = _assistant_store_log_summary(record, action="surface_context_attached")
    _safe_log_assistant_store_event("assistant.surface_context.attached", run_id=project_id or handoff_id, payload=summary)
    try:
        record_surface_snapshot("assistant", "neo_last_surface_context.json", summary, run_id=project_id or handoff_id)
    except Exception:
        pass
    return {"ok": True, "handoff": record, "context_item": context_result.get("context_item"), "message": "Surface context attached to Assistant."}


def list_surface_context_payload(project_id: str = "", surface: str = "", limit: int = 50) -> dict[str, Any]:
    ensure_assistant_storage()
    records: list[dict[str, Any]] = []
    for path in sorted(SURFACE_CONTEXT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        record = read_json(path, {})
        if not isinstance(record, dict) or not record.get("handoff_id"):
            continue
        if project_id and record.get("project_id") != project_id:
            continue
        if surface and record.get("surface") != surface:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return {"ok": True, "surface_context": records}
