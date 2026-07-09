from __future__ import annotations

"""Legacy Project Workspace compatibility layer.

Assistant Scope (`neo_data/assistant`) is Neo's primary internal project/context
model. This module remains as a compatibility layer for older Admin delivery
tools: briefs, milestones, deliverables, packages, review queues, and historical
cross-surface handoffs. Normal creative surfaces should not mount this as a
global project system.
"""

import difflib
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = ROOT_DIR / "neo_data" / "projects"
WORKSPACES_DIR = WORKSPACE_DIR / "workspaces"
CONTEXT_DIR = WORKSPACE_DIR / "context"
LINKS_DIR = WORKSPACE_DIR / "links"
HANDOFFS_DIR = WORKSPACE_DIR / "handoffs"
TIMELINE_DIR = WORKSPACE_DIR / "timeline"
BRIEFS_DIR = WORKSPACE_DIR / "briefs"
BRIEF_EXPORTS_DIR = WORKSPACE_DIR / "brief_exports"
MILESTONES_DIR = WORKSPACE_DIR / "milestones"
DELIVERABLES_DIR = WORKSPACE_DIR / "deliverables"
STATUS_EXPORTS_DIR = WORKSPACE_DIR / "status_exports"
REVIEW_QUEUE_DIR = WORKSPACE_DIR / "review_queue"
PACKAGES_DIR = WORKSPACE_DIR / "packages"
SURFACE_ACTIONS_DIR = WORKSPACE_DIR / "surface_actions"
INDEX_PATH = WORKSPACE_DIR / "project_workspace_index.json"
ACTIVE_PATH = WORKSPACE_DIR / "active_project.json"

PROJECT_WORKSPACE_DIRS = (
    WORKSPACE_DIR,
    WORKSPACES_DIR,
    CONTEXT_DIR,
    LINKS_DIR,
    HANDOFFS_DIR,
    TIMELINE_DIR,
    BRIEFS_DIR,
    BRIEF_EXPORTS_DIR,
    MILESTONES_DIR,
    DELIVERABLES_DIR,
    STATUS_EXPORTS_DIR,
    REVIEW_QUEUE_DIR,
    PACKAGES_DIR,
    SURFACE_ACTIONS_DIR,
)

SCHEMA_ID = "neo.project_workspace.v1"
COMPATIBILITY_SCHEMA_ID = "neo.project_workspace.compatibility.v1"
PROJECT_WORKSPACE_LAYER = "legacy_creator_project_workspace"
PROJECT_WORKSPACE_STATUS = "compatibility_ready"
PROJECT_WORKSPACE_VISIBILITY = "admin_compatibility_only"
PROJECT_WORKSPACE_COMPATIBILITY_POLICY = (
    "Legacy Project Workspace is kept for Admin delivery tools and historical routes. "
    "Assistant Scope is the primary internal project/context model; normal creative "
    "surfaces must not mount this layer as global project UI."
)

# Backward-compatible marker for older audit tests and records. The active policy
# above supersedes this text, but the statement remains true.
DEPRECATED_PROJECT_WORKSPACE_POLICY_NOTE = (
    "Project Workspace does not replace Assistant projects; it is now a legacy "
    "Admin compatibility layer behind Assistant Scope."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, fallback: str = "project") -> str:
    clean = re.sub(r"[^a-zA-Z0-9._ -]+", "", str(value or "").strip())
    clean = re.sub(r"\s+", "_", clean).strip("._- ").lower()
    return clean[:80] or fallback


def read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def project_workspace_compatibility_payload() -> dict[str, Any]:
    return {
        "schema_id": COMPATIBILITY_SCHEMA_ID,
        "compatibility_layer": True,
        "legacy_layer": True,
        "layer": PROJECT_WORKSPACE_LAYER,
        "status": PROJECT_WORKSPACE_STATUS,
        "visibility": PROJECT_WORKSPACE_VISIBILITY,
        "assistant_scope_is_primary": True,
        "global_project_ui_deprecated": True,
        "normal_surface_mount_allowed": False,
        "admin_routes_preserved": True,
        "source_id": "project_workspace",
        "storage_root": "neo_data/projects",
        "primary_replacement": "Assistant Scope / neo_data/assistant",
        "policy": PROJECT_WORKSPACE_COMPATIBILITY_POLICY,
    }


def _compatibility_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = extra if isinstance(extra, dict) else {}
    return {
        **metadata,
        "compatibility_layer": True,
        "legacy_layer": True,
        "project_workspace_layer": PROJECT_WORKSPACE_LAYER,
        "assistant_scope_is_primary": True,
        "normal_surface_mount_allowed": False,
    }


def _with_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    payload["compatibility"] = project_workspace_compatibility_payload()
    payload.setdefault("compatibility_layer", True)
    payload.setdefault("legacy_layer", True)
    payload.setdefault("assistant_scope_is_primary", True)
    return payload


def _safe_path(root: Path, record_id: str, fallback: str) -> Path:
    root = root.resolve()
    safe = slugify(record_id, fallback)
    path = (root / f"{safe}.json").resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid project workspace path")
    return path


def _default_workspace() -> dict[str, Any]:
    stamp = now_iso()
    return {
        "project_id": "general",
        "name": "General",
        "type": "general",
        "status": "active",
        "description": "Default legacy compatibility workspace for Admin delivery tools and historical Project Workspace routes.",
        "notes": "",
        "surfaces": ["admin"],
        "tags": ["general", "legacy_project_workspace", "compatibility"],
        "memory_namespace": "project:general",
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(),
    }


def ensure_project_workspace_storage() -> None:
    # Runtime bootstrap owns first-run data creation. This local guard keeps the
    # compatibility layer safe when imported directly by tests or old routes.
    for path in PROJECT_WORKSPACE_DIRS:
        path.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        project = _default_workspace()
        write_json(INDEX_PATH, {
            "schema_id": "neo.project_workspace.compat_index.v1",
            "compatibility_layer": True,
            "assistant_scope_is_primary": True,
            "projects": [project],
            "updated_at": now_iso(),
        })
        write_json(_safe_path(WORKSPACES_DIR, "general", "project"), project)
    if not ACTIVE_PATH.exists():
        write_json(ACTIVE_PATH, {"project_id": "general", "updated_at": now_iso(), "compatibility_layer": True})


def _summary(project: dict[str, Any]) -> dict[str, Any]:
    summary = {k: project.get(k) for k in ("project_id", "name", "type", "status", "description", "notes", "surfaces", "tags", "memory_namespace", "created_at", "updated_at")}
    summary["metadata"] = _compatibility_metadata(project.get("metadata") if isinstance(project.get("metadata"), dict) else {})
    return summary


def _read_index() -> list[dict[str, Any]]:
    ensure_project_workspace_storage()
    data = read_json(INDEX_PATH, {"projects": []})
    projects = data.get("projects") if isinstance(data, dict) else []
    if not isinstance(projects, list):
        projects = []
    if not any((p or {}).get("project_id") == "general" for p in projects):
        general = _default_workspace()
        projects.insert(0, _summary(general))
        write_json(_safe_path(WORKSPACES_DIR, "general", "project"), general)
        _write_index(projects)
    return projects


def _write_index(projects: list[dict[str, Any]]) -> None:
    write_json(INDEX_PATH, {
        "schema_id": "neo.project_workspace.compat_index.v1",
        "compatibility_layer": True,
        "assistant_scope_is_primary": True,
        "projects": sorted(projects, key=lambda p: (p.get("name") or "").lower()),
        "updated_at": now_iso(),
    })


def list_project_workspaces() -> list[dict[str, Any]]:
    return _read_index()


def get_project_workspace(project_id: str) -> dict[str, Any] | None:
    project_id = str(project_id or "general").strip() or "general"
    for summary in list_project_workspaces():
        if summary.get("project_id") == project_id:
            record = read_json(_safe_path(WORKSPACES_DIR, project_id, "project"), {})
            if isinstance(record, dict) and record:
                return {**summary, **record}
            return summary
    return None


def save_project_workspace(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    name = str(data.get("name") or data.get("title") or data.get("project_id") or "New Project").strip() or "New Project"
    project_id = slugify(data.get("project_id") or name, "project")
    existing = {p.get("project_id") for p in list_project_workspaces()}
    if not data.get("project_id") and project_id in existing:
        project_id = f"{project_id}_{uuid4().hex[:8]}"
    stamp = now_iso()
    current = get_project_workspace(project_id) or {"project_id": project_id, "created_at": stamp}
    for key in ("name", "type", "status", "description", "notes"):
        if key in data or key not in current:
            current[key] = str(data.get(key) or current.get(key) or ("active" if key == "status" else "general" if key == "type" else ""))
    for key in ("surfaces", "tags"):
        if key in data:
            value = data.get(key)
            if isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            current[key] = value if isinstance(value, list) else current.get(key, [])
    current["metadata"] = _compatibility_metadata({**(current.get("metadata") if isinstance(current.get("metadata"), dict) else {}), **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})})
    current.setdefault("surfaces", ["admin"])
    current.setdefault("tags", [])
    current["name"] = str(current.get("name") or name)
    current["type"] = str(current.get("type") or "general")
    current["status"] = str(current.get("status") or "active")
    current["memory_namespace"] = f"project:{project_id}"
    current["updated_at"] = stamp
    write_json(_safe_path(WORKSPACES_DIR, project_id, "project"), current)
    projects = [p for p in list_project_workspaces() if p.get("project_id") != project_id]
    projects.append(_summary(current))
    _write_index(projects)
    return {"ok": True, "schema_id": SCHEMA_ID, "project": current, "projects": list_project_workspaces()}


def active_project_payload() -> dict[str, Any]:
    ensure_project_workspace_storage()
    active = read_json(ACTIVE_PATH, {"project_id": "general"})
    project_id = str((active or {}).get("project_id") or "general")
    project = get_project_workspace(project_id) or get_project_workspace("general") or _default_workspace()
    return _with_compatibility({"ok": True, "schema_id": "neo.project_workspace.active.v1", "status": "ready", "active_project_id": project.get("project_id"), "project": project})


def set_active_project(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project_id = str((payload or {}).get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    project = get_project_workspace(project_id)
    if not project:
        raise FileNotFoundError("Project workspace not found")
    write_json(ACTIVE_PATH, {"project_id": project_id, "updated_at": now_iso()})
    return active_project_payload()


def add_project_context(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    text = str(data.get("text") or data.get("content") or "").strip()
    title = str(data.get("title") or data.get("label") or "Project context").strip() or "Project context"
    if not text:
        raise ValueError("Project context text is required")
    stamp = now_iso()
    context_id = slugify(data.get("context_id") or f"ctx_{uuid4().hex[:12]}", "context")
    record = {
        "context_id": context_id,
        "project_id": project_id,
        "title": title,
        "kind": str(data.get("kind") or "note"),
        "surface": str(data.get("surface") or "project_workspace"),
        "text": text,
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "source_ref": str(data.get("source_ref") or ""),
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_safe_path(CONTEXT_DIR / project_id, context_id, "context"), record)
    return {"ok": True, "schema_id": "neo.project_workspace.context.v1", "context": record}


def list_project_context(project_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = CONTEXT_DIR / project_id
    if not root.exists():
        return []
    rows = []
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("context_id"):
            rows.append(record)
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows[: max(1, int(limit or 50))]


def link_project_resource(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    link_id = slugify(data.get("link_id") or f"link_{uuid4().hex[:12]}", "link")
    stamp = now_iso()
    record = {
        "link_id": link_id,
        "project_id": project_id,
        "surface": str(data.get("surface") or "unknown"),
        "resource_type": str(data.get("resource_type") or data.get("kind") or "resource"),
        "title": str(data.get("title") or "Linked resource"),
        "path": str(data.get("path") or data.get("source_path") or ""),
        "ref_id": str(data.get("ref_id") or ""),
        "notes": str(data.get("notes") or ""),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_safe_path(LINKS_DIR / project_id, link_id, "link"), record)
    return {"ok": True, "schema_id": "neo.project_workspace.link.v1", "link": record}


def list_project_links(project_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = LINKS_DIR / project_id
    if not root.exists():
        return []
    rows = []
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("link_id"):
            rows.append(record)
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows[: max(1, int(limit or 50))]


def project_workspace_context_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    active = active_project_payload()
    requested_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(requested_project_id)
    fallback_used = False
    if not project:
        project = get_project_workspace("general") or _default_workspace()
        fallback_used = True
    project_record_id = str(project.get("project_id") or requested_project_id or "general")
    return _with_compatibility({
        "ok": True,
        "schema_id": "neo.project_workspace.context_pack.v1",
        "status": "ready",
        "project_id": project_record_id,
        "requested_project_id": requested_project_id,
        "fallback_used": fallback_used,
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "context_items": list_project_context(project_record_id, limit=limit),
        "links": list_project_links(project_record_id, limit=limit),
        "timeline": list_project_timeline(project_record_id, limit=limit),
        "milestones": list_project_milestones(project_record_id, limit=limit),
        "deliverables": list_project_deliverables(project_record_id, limit=limit),
        "deliverables_summary": project_deliverable_summary(project_record_id),
        "handoffs": list_project_handoffs(project_record_id, limit=limit),
        "asset_tray_endpoint": "/api/projects/workspace/asset-tray",
        "handoff_endpoint": "/api/projects/workspace/handoff",
        "policy": PROJECT_WORKSPACE_COMPATIBILITY_POLICY,
    })


def project_workspace_status_payload() -> dict[str, Any]:
    projects = list_project_workspaces()
    active = active_project_payload()
    return _with_compatibility({
        "schema_id": "neo.project_workspace.status.v1",
        "status": "ready",
        "mode": PROJECT_WORKSPACE_STATUS,
        "label": "Legacy Project Workspace Compatibility",
        "root": str(WORKSPACE_DIR),
        "active_project_id": active.get("active_project_id"),
        "project_count": len(projects),
        "context_count": sum(len(list_project_context(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "link_count": sum(len(list_project_links(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "handoff_count": sum(len(list_project_handoffs(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "surface_action_count": sum(len(list_project_surface_actions(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "timeline_count": sum(len(list_project_timeline(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "milestone_count": sum(len(list_project_milestones(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "deliverable_count": sum(len(list_project_deliverables(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "review_item_count": sum(len(list_project_review_items(str(p.get("project_id") or "general"), limit=1000)) for p in projects),
        "endpoints": [
            "/api/projects/workspace/status",
            "/api/projects/workspace/list",
            "/api/projects/workspace/active",
            "/api/projects/workspace/save",
            "/api/projects/workspace/context",
            "/api/projects/workspace/link",
            "/api/projects/workspace/index",
            "/api/projects/workspace/handoff",
            "/api/projects/workspace/timeline",
            "/api/projects/workspace/asset-tray",
            "/api/projects/workspace/milestones",
            "/api/projects/workspace/deliverables",
            "/api/projects/workspace/deliverable-tracker",
            "/api/projects/workspace/review-queue",
            "/api/projects/workspace/review-items",
            "/api/projects/workspace/review-decision",
            "/api/projects/workspace/approval-workflow",
            "/api/projects/workspace/package-builder",
            "/api/projects/workspace/package/build",
            "/api/projects/workspace/surface-actions",
            "/api/projects/workspace/surface-action",
        ],
        "policy": PROJECT_WORKSPACE_COMPATIBILITY_POLICY,
    })


def project_workspace_records_for_index(limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project in list_project_workspaces():
        project_id = str(project.get("project_id") or "general")
        full = get_project_workspace(project_id) or project
        rows.append({"kind": "workspace", "project_id": project_id, "title": full.get("name") or project_id, "text": "\n".join([str(full.get("description") or ""), str(full.get("notes") or ""), "Tags: " + ", ".join(full.get("tags") or []), "Surfaces: " + ", ".join(full.get("surfaces") or [])]), "payload": full})
        for item in list_project_context(project_id, limit=1000):
            rows.append({"kind": "context", "project_id": project_id, "title": item.get("title") or item.get("context_id"), "text": item.get("text") or "", "payload": item})
        for link in list_project_links(project_id, limit=1000):
            text = "\n".join([str(link.get("title") or ""), str(link.get("notes") or ""), str(link.get("path") or ""), str(link.get("ref_id") or "")])
            rows.append({"kind": "link", "project_id": project_id, "title": link.get("title") or link.get("link_id"), "text": text, "payload": link})
        for handoff in list_project_handoffs(project_id, limit=1000):
            text = "\n".join([str(handoff.get("title") or ""), str(handoff.get("content_preview") or ""), str(handoff.get("source_surface") or ""), str(handoff.get("target_surface") or "")])
            rows.append({"kind": "handoff", "project_id": project_id, "title": handoff.get("title") or handoff.get("handoff_id"), "text": text, "payload": handoff})
        for event in list_project_timeline(project_id, limit=1000):
            text = "\n".join([str(event.get("title") or ""), str(event.get("summary") or ""), str(event.get("event_type") or ""), str(event.get("surface") or "")])
            rows.append({"kind": "timeline", "project_id": project_id, "title": event.get("title") or event.get("event_id"), "text": text, "payload": event})
        for milestone in list_project_milestones(project_id, limit=1000):
            text = "\n".join([str(milestone.get("title") or ""), str(milestone.get("summary") or ""), str(milestone.get("status") or ""), str(milestone.get("due_date") or "")])
            rows.append({"kind": "milestone", "project_id": project_id, "title": milestone.get("title") or milestone.get("milestone_id"), "text": text, "payload": milestone})
        for deliverable in list_project_deliverables(project_id, limit=1000):
            text = "\n".join([str(deliverable.get("title") or ""), str(deliverable.get("summary") or ""), str(deliverable.get("status") or ""), str(deliverable.get("review_state") or ""), str(deliverable.get("due_date") or "")])
            rows.append({"kind": "deliverable", "project_id": project_id, "title": deliverable.get("title") or deliverable.get("deliverable_id"), "text": text, "payload": deliverable})
        for review in list_project_review_items(project_id, limit=1000):
            text = "\n".join([str(review.get("title") or ""), str(review.get("summary") or ""), str(review.get("status") or ""), str(review.get("review_scope") or ""), str(review.get("decision_notes") or "")])
            rows.append({"kind": "review_item", "project_id": project_id, "title": review.get("title") or review.get("review_id"), "text": text, "payload": review})
        for package in _list_project_packages(project_id, limit=1000):
            text = "\n".join([str(package.get("title") or ""), str(package.get("status") or ""), str(package.get("package_dir") or ""), str(package.get("zip_path") or "")])
            rows.append({"kind": "project_package", "project_id": project_id, "title": package.get("title") or package.get("package_id"), "text": text, "payload": package})
        for action in list_project_surface_actions(project_id, limit=1000):
            text = "\n".join([str(action.get("title") or ""), str(action.get("action_type") or ""), str(action.get("source_surface") or ""), str(action.get("resource_type") or ""), str(action.get("content_preview") or "")])
            rows.append({"kind": "surface_action", "project_id": project_id, "title": action.get("title") or action.get("action_id"), "text": text, "payload": action})
        try:
            for brief in list_project_briefs(project_id, limit=1000):
                full = get_project_brief(project_id, str(brief.get("brief_id") or "")).get("brief", {})
                rows.append({"kind": "brief", "project_id": project_id, "title": full.get("title") or brief.get("brief_id"), "text": full.get("markdown") or "", "payload": full})
        except Exception:
            pass
    if limit:
        return rows[: max(0, int(limit))]
    return rows



def add_project_timeline_event(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    event_id = slugify(data.get("event_id") or f"event_{uuid4().hex[:12]}", "event")
    stamp = now_iso()
    record = {
        "event_id": event_id,
        "project_id": project_id,
        "event_type": str(data.get("event_type") or data.get("type") or "project.activity"),
        "surface": str(data.get("surface") or "project_workspace"),
        "title": str(data.get("title") or "Project activity"),
        "summary": str(data.get("summary") or data.get("notes") or ""),
        "resource_type": str(data.get("resource_type") or "activity"),
        "ref_id": str(data.get("ref_id") or ""),
        "link_id": str(data.get("link_id") or ""),
        "context_id": str(data.get("context_id") or ""),
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_safe_path(TIMELINE_DIR / project_id, event_id, "event"), record)
    return {"ok": True, "schema_id": "neo.project_workspace.timeline_event.v1", "event": record}


def list_project_timeline(project_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = TIMELINE_DIR / project_id
    if not root.exists():
        return []
    rows = []
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("event_id"):
            rows.append(record)
    rows.sort(key=lambda r: r.get("created_at") or r.get("updated_at") or "", reverse=True)
    return rows[: max(1, int(limit or 50))]


def create_cross_surface_handoff(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    handoff_id = slugify(data.get("handoff_id") or f"handoff_{uuid4().hex[:12]}", "handoff")
    source_surface = str(data.get("source_surface") or data.get("surface") or "unknown")
    target_surface = str(data.get("target_surface") or "project_workspace")
    title = str(data.get("title") or "Project handoff")
    content = str(data.get("content") or data.get("text") or data.get("notes") or "").strip()
    resource_type = str(data.get("resource_type") or data.get("kind") or "handoff")
    path = str(data.get("path") or data.get("source_path") or "")
    ref_id = str(data.get("ref_id") or "")
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    stamp = now_iso()
    context = None
    link = None
    if content:
        context = add_project_context({
            "project_id": project_id,
            "title": title,
            "kind": resource_type,
            "surface": source_surface,
            "text": content,
            "tags": tags,
            "source_ref": ref_id or path,
            "metadata": {"handoff_id": handoff_id, "target_surface": target_surface, **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})},
        }).get("context")
    if path or ref_id or resource_type:
        link = link_project_resource({
            "project_id": project_id,
            "surface": source_surface,
            "resource_type": resource_type,
            "title": title,
            "path": path,
            "ref_id": ref_id,
            "notes": content[:500],
            "tags": tags,
            "metadata": {"handoff_id": handoff_id, "target_surface": target_surface, **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})},
        }).get("link")
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.handoff.created",
        "surface": source_surface,
        "title": title,
        "summary": content[:500] or f"{source_surface} sent {resource_type} to {target_surface}",
        "resource_type": resource_type,
        "ref_id": ref_id,
        "link_id": (link or {}).get("link_id", ""),
        "context_id": (context or {}).get("context_id", ""),
        "metadata": {"handoff_id": handoff_id, "target_surface": target_surface},
    }).get("event")
    record = {
        "handoff_id": handoff_id,
        "project_id": project_id,
        "source_surface": source_surface,
        "target_surface": target_surface,
        "resource_type": resource_type,
        "title": title,
        "content_preview": content[:500],
        "path": path,
        "ref_id": ref_id,
        "context_id": (context or {}).get("context_id"),
        "link_id": (link or {}).get("link_id"),
        "event_id": (timeline or {}).get("event_id"),
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_safe_path(HANDOFFS_DIR / project_id, handoff_id, "handoff"), record)
    return {"ok": True, "schema_id": "neo.project_workspace.handoff.v1", "handoff": record, "context": context, "link": link, "timeline_event": timeline}



SURFACE_ACTION_SCHEMA = "neo.project_workspace.surface_action.v1"
SURFACE_ACTIONS_SCHEMA = "neo.project_workspace.surface_actions.v1"


def _surface_action_path(project_id: str, action_id: str) -> Path:
    return _safe_path(SURFACE_ACTIONS_DIR / project_id, action_id, "surface_action")


def create_project_surface_action(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute and ledger a native project action triggered from any Neo surface."""
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    action_type = slugify(data.get("action_type") or data.get("type") or "send_to_project", "send_to_project")
    if action_type in {"send", "handoff", "save_to_project"}:
        action_type = "send_to_project"
    source_surface = str(data.get("source_surface") or data.get("surface") or "unknown")
    resource_type = str(data.get("resource_type") or data.get("kind") or "surface_snapshot")
    title = str(data.get("title") or f"{source_surface} project action")
    content = str(data.get("content") or data.get("text") or data.get("notes") or data.get("summary") or "").strip()
    stamp = now_iso()
    action_id = slugify(data.get("action_id") or f"action_{uuid4().hex[:12]}", "surface_action")
    result: dict[str, Any] = {}
    created_ids: dict[str, str] = {}
    if action_type in {"send_to_project", "handoff_to_project"}:
        result = create_cross_surface_handoff({**data, "project_id": project_id, "source_surface": source_surface, "target_surface": data.get("target_surface") or "project_workspace", "resource_type": resource_type, "title": title, "content": content})
        created_ids = {
            "handoff_id": ((result.get("handoff") or {}).get("handoff_id") or ""),
            "context_id": ((result.get("context") or {}).get("context_id") or ""),
            "link_id": ((result.get("link") or {}).get("link_id") or ""),
            "event_id": ((result.get("timeline_event") or {}).get("event_id") or ""),
        }
    elif action_type in {"add_context", "save_context", "add_as_context"}:
        context = add_project_context({"project_id": project_id, "title": title, "kind": resource_type, "surface": source_surface, "text": content or title, "tags": data.get("tags") if isinstance(data.get("tags"), list) else [source_surface, resource_type], "source_ref": str(data.get("ref_id") or data.get("path") or ""), "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {}}).get("context")
        result = {"context": context}
        created_ids = {"context_id": (context or {}).get("context_id", "")}
    elif action_type in {"link_resource", "link_to_project"}:
        link = link_project_resource({"project_id": project_id, "surface": source_surface, "resource_type": resource_type, "title": title, "path": data.get("path") or data.get("source_path") or "", "ref_id": data.get("ref_id") or "", "notes": content[:500], "tags": data.get("tags") if isinstance(data.get("tags"), list) else [source_surface, resource_type], "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {}}).get("link")
        result = {"link": link}
        created_ids = {"link_id": (link or {}).get("link_id", "")}
    elif action_type in {"create_deliverable", "add_deliverable"}:
        deliverable = save_project_deliverable({"project_id": project_id, "title": title, "summary": content or title, "status": data.get("status") or "planned", "review_state": data.get("review_state") or "not_started", "source_surface": source_surface, "resource_type": resource_type, "source_ref": data.get("ref_id") or data.get("path") or "", "metadata": {"surface_action_id": action_id, **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})}}).get("deliverable")
        result = {"deliverable": deliverable}
        created_ids = {"deliverable_id": (deliverable or {}).get("deliverable_id", "")}
    elif action_type in {"create_milestone", "add_milestone"}:
        milestone = save_project_milestone({"project_id": project_id, "title": title, "summary": content or title, "status": data.get("status") or "planned", "source_surface": source_surface, "metadata": {"surface_action_id": action_id, "resource_type": resource_type, "source_ref": data.get("ref_id") or data.get("path") or "", **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})}}).get("milestone")
        result = {"milestone": milestone}
        created_ids = {"milestone_id": (milestone or {}).get("milestone_id", "")}
    elif action_type in {"create_review_item", "add_review_item"}:
        review = save_project_review_item({"project_id": project_id, "title": title, "summary": content or title, "status": data.get("status") or "queued", "review_scope": data.get("review_scope") or resource_type, "source_surface": source_surface, "resource_type": resource_type, "source_ref": data.get("ref_id") or data.get("path") or "", "metadata": {"surface_action_id": action_id, **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})}}).get("review_item")
        result = {"review_item": review}
        created_ids = {"review_id": (review or {}).get("review_id", "")}
    else:
        raise ValueError(f"Unsupported project surface action: {action_type}")
    timeline_event = add_project_timeline_event({
        "project_id": project_id,
        "event_type": f"project.surface_action.{action_type}",
        "surface": source_surface,
        "title": f"Surface action: {title}",
        "summary": content[:500] or f"{source_surface} ran {action_type} for {resource_type}.",
        "resource_type": resource_type,
        "ref_id": str(data.get("ref_id") or data.get("path") or ""),
        "metadata": {"action_id": action_id, "action_type": action_type, "created_ids": created_ids},
    }).get("event")
    if timeline_event and not created_ids.get("surface_action_event_id"):
        created_ids["surface_action_event_id"] = timeline_event.get("event_id", "")
    record = {
        "action_id": action_id,
        "project_id": project_id,
        "action_type": action_type,
        "source_surface": source_surface,
        "target_surface": str(data.get("target_surface") or "project_workspace"),
        "resource_type": resource_type,
        "title": title,
        "content_preview": content[:500],
        "path": str(data.get("path") or data.get("source_path") or ""),
        "ref_id": str(data.get("ref_id") or ""),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [source_surface, resource_type],
        "created_ids": created_ids,
        "created_at": stamp,
        "updated_at": now_iso(),
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_surface_action_path(project_id, action_id), record)
    return {"ok": True, "schema_id": SURFACE_ACTION_SCHEMA, "project_id": project_id, "action": record, "result": result, "timeline_event": timeline_event}


def list_project_surface_actions(project_id: str = "", *, limit: int = 50, surface: str = "") -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = SURFACE_ACTIONS_DIR / project_id
    if not root.exists():
        return []
    rows = []
    wanted = str(surface or "").lower().strip()
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("action_id"):
            if wanted and str(record.get("source_surface") or "").lower() != wanted:
                continue
            rows.append(record)
    rows.sort(key=lambda r: r.get("created_at") or r.get("updated_at") or "", reverse=True)
    return rows[: max(1, int(limit or 50))]


def project_surface_actions_payload(project_id: str = "", *, limit: int = 50, surface: str = "") -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    actions = list_project_surface_actions(resolved_project_id, limit=limit, surface=surface)
    by_surface: dict[str, int] = {}
    by_action_type: dict[str, int] = {}
    for action in actions:
        by_surface[str(action.get("source_surface") or "unknown")] = by_surface.get(str(action.get("source_surface") or "unknown"), 0) + 1
        by_action_type[str(action.get("action_type") or "unknown")] = by_action_type.get(str(action.get("action_type") or "unknown"), 0) + 1
    return _with_compatibility({
        "ok": True,
        "schema_id": SURFACE_ACTIONS_SCHEMA,
        "status": "ready",
        "project_id": resolved_project_id,
        "active_project_id": active.get("active_project_id"),
        "surface": surface,
        "supported_actions": ["send_to_project", "add_context", "link_resource", "create_deliverable", "create_milestone", "create_review_item"],
        "summary": {"action_count": len(actions), "by_surface": by_surface, "by_action_type": by_action_type},
        "actions": actions,
        "policy": "Legacy cross-surface project actions are compatibility writes for Admin delivery tools. Normal surfaces should not expose global Project UI.",
    })


def list_project_handoffs(project_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = HANDOFFS_DIR / project_id
    if not root.exists():
        return []
    rows = []
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("handoff_id"):
            rows.append(record)
    rows.sort(key=lambda r: r.get("created_at") or r.get("updated_at") or "", reverse=True)
    return rows[: max(1, int(limit or 50))]


def project_workspace_asset_tray_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    links = list_project_links(resolved_project_id, limit=limit)
    context_items = list_project_context(resolved_project_id, limit=limit)
    timeline = list_project_timeline(resolved_project_id, limit=limit)
    handoffs = list_project_handoffs(resolved_project_id, limit=limit)
    milestones = list_project_milestones(resolved_project_id, limit=limit)
    deliverables = list_project_deliverables(resolved_project_id, limit=limit)
    deliverable_summary = project_deliverable_summary(resolved_project_id)
    by_surface: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for link in links:
        by_surface[str(link.get("surface") or "unknown")] = by_surface.get(str(link.get("surface") or "unknown"), 0) + 1
        by_type[str(link.get("resource_type") or "resource")] = by_type.get(str(link.get("resource_type") or "resource"), 0) + 1
    return _with_compatibility({
        "ok": True,
        "schema_id": "neo.project_workspace.asset_tray.v1",
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "links": links,
        "context_items": context_items,
        "timeline": timeline,
        "handoffs": handoffs,
        "summary": {
            "link_count": len(links),
            "context_count": len(context_items),
            "timeline_count": len(timeline),
            "milestone_count": len(milestones),
            "deliverable_count": len(deliverables),
            "open_deliverable_count": deliverable_summary.get("open_count", 0),
            "blocked_deliverable_count": deliverable_summary.get("blocked_count", 0),
            "handoff_count": len(handoffs),
            "by_surface": by_surface,
            "by_type": by_type,
        },
        "policy": "Legacy Asset Tray is an Admin compatibility view. It stores references and context; it does not copy binary assets or re-enable global surface Project UI.",
    })


def _event_importance(event: dict[str, Any]) -> str:
    text = " ".join([str(event.get("event_type") or ""), str(event.get("resource_type") or ""), str(event.get("title") or ""), str(event.get("summary") or "")]).lower()
    if any(token in text for token in ("canon", "milestone", "approved", "final", "delivery", "decision", "blocked", "error", "conflict")):
        return "high"
    if any(token in text for token in ("handoff", "assistant", "roleplay", "image", "prompt", "caption", "diagnostic")):
        return "normal"
    return "low"


def _workflow_key(event: dict[str, Any]) -> str:
    surface = str(event.get("surface") or "unknown").strip() or "unknown"
    rtype = str(event.get("resource_type") or event.get("event_type") or "activity").strip() or "activity"
    return f"{surface}:{rtype}"


def _add_count(bucket: dict[str, int], key: str) -> None:
    bucket[key or "unknown"] = bucket.get(key or "unknown", 0) + 1


def project_activity_intelligence_payload(project_id: str = "", *, limit: int = 100) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    timeline = list_project_timeline(resolved_project_id, limit=limit)
    handoffs = list_project_handoffs(resolved_project_id, limit=limit)
    links = list_project_links(resolved_project_id, limit=limit)
    context_items = list_project_context(resolved_project_id, limit=limit)
    milestones = list_project_milestones(resolved_project_id, limit=limit)
    deliverables = list_project_deliverables(resolved_project_id, limit=limit)
    deliverable_summary = project_deliverable_summary(resolved_project_id)
    by_surface: dict[str, int] = {}
    by_event_type: dict[str, int] = {}
    by_resource_type: dict[str, int] = {}
    by_importance: dict[str, int] = {}
    workflow_groups: dict[str, dict[str, Any]] = {}
    important_events: list[dict[str, Any]] = []
    assistant_decisions: list[dict[str, Any]] = []
    roleplay_milestones: list[dict[str, Any]] = []
    project_milestones: list[dict[str, Any]] = []

    for event in timeline:
        surface = str(event.get("surface") or "unknown")
        event_type = str(event.get("event_type") or "activity")
        resource_type = str(event.get("resource_type") or "activity")
        importance = _event_importance(event)
        event["importance"] = importance
        event["workflow_key"] = _workflow_key(event)
        _add_count(by_surface, surface)
        _add_count(by_event_type, event_type)
        _add_count(by_resource_type, resource_type)
        _add_count(by_importance, importance)
        group = workflow_groups.setdefault(event["workflow_key"], {
            "workflow_key": event["workflow_key"],
            "surface": surface,
            "resource_type": resource_type,
            "count": 0,
            "latest_at": "",
            "important_count": 0,
            "events": [],
        })
        group["count"] += 1
        group["latest_at"] = max(str(group.get("latest_at") or ""), str(event.get("created_at") or event.get("updated_at") or ""))
        if importance == "high":
            group["important_count"] += 1
            important_events.append(event)
        if surface == "assistant" or "assistant" in event_type.lower() or "decision" in (event.get("title") or "").lower():
            assistant_decisions.append(event)
        if surface == "roleplay" or "canon" in resource_type.lower() or "roleplay" in event_type.lower():
            roleplay_milestones.append(event)
        if importance == "high" or "milestone" in event_type.lower():
            project_milestones.append(event)

    for milestone in milestones:
        if str(milestone.get("status") or "") in {"completed", "approved", "blocked"}:
            project_milestones.append({**milestone, "event_type": "project.milestone", "surface": "project_workspace", "resource_type": "project_milestone", "importance": "high"})
    for deliverable in deliverables:
        if str(deliverable.get("status") or "") in {"ready_for_review", "revision_needed", "approved", "delivered"} or str(deliverable.get("review_state") or "") in {"client_review", "revision_requested", "approved"}:
            important_events.append({**deliverable, "event_type": "project.deliverable", "surface": "project_workspace", "resource_type": "project_deliverable", "importance": "high"})

    workflow_list = sorted(workflow_groups.values(), key=lambda g: (g.get("important_count", 0), g.get("count", 0), g.get("latest_at", "")), reverse=True)
    active_surfaces = sorted(by_surface, key=lambda key: by_surface[key], reverse=True)
    latest_event = timeline[0] if timeline else None
    summary_lines = []
    if latest_event:
        summary_lines.append(f"Latest activity: {latest_event.get('title') or latest_event.get('event_type')} from {latest_event.get('surface') or 'unknown'}.")
    if active_surfaces:
        summary_lines.append("Most active surfaces: " + ", ".join(active_surfaces[:3]) + ".")
    if important_events:
        summary_lines.append(f"{len(important_events)} important events need visibility or review.")
    if not timeline and not handoffs and not context_items:
        summary_lines.append("No project activity yet. Start by sending a surface result, prompt, scene, or Assistant source evidence to the active project.")

    recommended_focus = []
    if not context_items:
        recommended_focus.append("Add project context notes so Assistant has a clean brief.")
    if handoffs and not important_events:
        recommended_focus.append("Review recent handoffs and mark important decisions or milestones.")
    if roleplay_milestones:
        recommended_focus.append("Review Roleplay milestones for canon promotion if they are final.")
    if deliverable_summary.get("blocked_count"):
        recommended_focus.append("Resolve blocked deliverables or revision requests before final delivery.")
    if deliverable_summary.get("due_soon_count"):
        recommended_focus.append("Review due-soon deliverables and confirm next delivery steps.")
    if assistant_decisions:
        recommended_focus.append("Attach key Assistant decisions to project context or summary memory.")
    if not recommended_focus:
        recommended_focus.append("Continue building timeline events from active surfaces.")

    return _with_compatibility({
        "ok": True,
        "schema_id": "neo.project_workspace.activity_intelligence.v1",
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "summary": {
            "timeline_count": len(timeline),
            "milestone_count": len(milestones),
            "deliverable_count": len(deliverables),
            "open_deliverable_count": deliverable_summary.get("open_count", 0),
            "blocked_deliverable_count": deliverable_summary.get("blocked_count", 0),
            "handoff_count": len(handoffs),
            "link_count": len(links),
            "context_count": len(context_items),
            "workflow_group_count": len(workflow_list),
            "important_event_count": len(important_events),
            "assistant_decision_count": len(assistant_decisions),
            "roleplay_milestone_count": len(roleplay_milestones),
            "active_surfaces": active_surfaces,
            "latest_event": latest_event,
            "progress_summary": " ".join(summary_lines),
            "recommended_focus": recommended_focus,
        },
        "groups": {
            "by_surface": by_surface,
            "by_event_type": by_event_type,
            "by_resource_type": by_resource_type,
            "by_importance": by_importance,
            "workflow_groups": workflow_list[:20],
        },
        "important_events": important_events[:20],
        "assistant_decisions": assistant_decisions[:20],
        "roleplay_milestones": roleplay_milestones[:20],
        "project_milestones": project_milestones[:20],
        "milestones": milestones[:20],
        "deliverables": deliverables[:20],
        "deliverable_summary": deliverable_summary,
        "timeline": timeline,
        "policy": "Legacy Project Activity Intelligence is read-only Admin compatibility diagnostics; it does not rewrite Assistant Scope memory or assets.",
    })


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id") or event.get("handoff_id") or event.get("link_id") or event.get("context_id") or "",
        "title": event.get("title") or event.get("event_type") or event.get("resource_type") or "Project item",
        "surface": event.get("surface") or event.get("source_surface") or "project_workspace",
        "resource_type": event.get("resource_type") or event.get("kind") or event.get("event_type") or "activity",
        "summary": event.get("summary") or event.get("content") or event.get("text") or event.get("description") or "",
        "created_at": event.get("created_at") or event.get("updated_at") or "",
        "ref": event.get("ref") or event.get("path") or event.get("source_ref") or "",
    }


def _dedupe_lines(lines: list[str], *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        clean = str(line or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(clean)
        if len(output) >= limit:
            break
    return output


def project_smart_brief_payload(project_id: str = "", *, audience: str = "internal", detail: str = "standard", limit: int = 120) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    activity = project_activity_intelligence_payload(resolved_project_id, limit=limit)
    context_items = list_project_context(resolved_project_id, limit=limit)
    links = list_project_links(resolved_project_id, limit=limit)
    handoffs = list_project_handoffs(resolved_project_id, limit=limit)
    timeline = list_project_timeline(resolved_project_id, limit=limit)
    milestones = list_project_milestones(resolved_project_id, limit=limit)
    deliverables = list_project_deliverables(resolved_project_id, limit=limit)
    deliverable_summary = project_deliverable_summary(resolved_project_id)

    summary = activity.get("summary") if isinstance(activity.get("summary"), dict) else {}
    important_events = activity.get("important_events") if isinstance(activity.get("important_events"), list) else []
    assistant_decisions = activity.get("assistant_decisions") if isinstance(activity.get("assistant_decisions"), list) else []
    roleplay_milestones = activity.get("roleplay_milestones") if isinstance(activity.get("roleplay_milestones"), list) else []
    project_milestones = activity.get("project_milestones") if isinstance(activity.get("project_milestones"), list) else []
    workflow_groups = ((activity.get("groups") or {}).get("workflow_groups") if isinstance(activity.get("groups"), dict) else []) or []

    project_name = project.get("name") or project.get("project_id") or "Untitled project"
    status = project.get("status") or "active"
    progress_summary = summary.get("progress_summary") or "No project activity has been summarized yet."

    completed_work = _dedupe_lines([
        f"{item.get('surface') or item.get('source_surface') or 'surface'}: {item.get('title') or item.get('resource_type') or item.get('event_type') or 'project item'}"
        for item in (important_events + handoffs + timeline)
        if item.get('title') or item.get('resource_type') or item.get('event_type')
    ], limit=10)

    open_decisions = _dedupe_lines([
        item.get("summary") or item.get("content") or item.get("title") or ""
        for item in assistant_decisions
    ] + [
        focus for focus in (summary.get("recommended_focus") or [])
    ], limit=8)

    creative_direction = _dedupe_lines([
        project.get("description") or "",
        project.get("notes") or "",
    ] + [
        item.get("text") or item.get("content") or "" for item in context_items[:6]
    ], limit=8)

    linked_assets = [_compact_event(item) for item in (links[:10] + handoffs[:10])]
    deliverable_notes = [_compact_event(item) for item in deliverables[:12]]
    milestone_notes = [_compact_event(item) for item in (milestones[:8] + project_milestones[:8])]
    assistant_notes = [_compact_event(item) for item in assistant_decisions[:10]]
    roleplay_notes = [_compact_event(item) for item in roleplay_milestones[:10]]
    workflow_notes = [
        {
            "workflow_key": group.get("workflow_key"),
            "surface": group.get("surface"),
            "resource_type": group.get("resource_type"),
            "count": group.get("count", 0),
            "important_count": group.get("important_count", 0),
            "latest_at": group.get("latest_at"),
        }
        for group in workflow_groups[:10]
    ]

    next_actions = _dedupe_lines(list(summary.get("recommended_focus") or []), limit=6)
    if not next_actions:
        next_actions = ["Add project context, link important assets, and send surface outputs into the project timeline."]

    brief_lines = [
        f"Project: {project_name}",
        f"Status: {status}",
        f"Summary: {progress_summary}",
    ]
    if completed_work:
        brief_lines.append("Completed / captured work: " + "; ".join(completed_work[:5]))
    if open_decisions:
        brief_lines.append("Open decisions / focus: " + "; ".join(open_decisions[:5]))
    if linked_assets:
        brief_lines.append(f"Linked assets: {len(linked_assets)} project references available.")
    if deliverables:
        brief_lines.append(f"Deliverables: {deliverable_summary.get('open_count', 0)} open, {deliverable_summary.get('approved_count', 0)} approved, {deliverable_summary.get('blocked_count', 0)} blocked.")

    return {
        "ok": True,
        "schema_id": "neo.project_workspace.smart_brief.v1",
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "audience": audience or "internal",
        "detail": detail or "standard",
        "project": project,
        "brief": {
            "title": f"{project_name} — Smart Project Brief",
            "current_status": status,
            "progress_summary": progress_summary,
            "completed_work": completed_work,
            "open_decisions": open_decisions,
            "creative_direction": creative_direction,
            "linked_assets": linked_assets[:12],
            "deliverables": deliverable_notes[:12],
            "milestones": milestone_notes[:12],
            "deliverable_summary": deliverable_summary,
            "assistant_notes": assistant_notes[:8],
            "roleplay_milestones": roleplay_notes[:8],
            "workflow_groups": workflow_notes[:10],
            "next_actions": next_actions,
            "client_ready_summary": " ".join(brief_lines),
        },
        "source_counts": {
            "context": len(context_items),
            "links": len(links),
            "handoffs": len(handoffs),
            "timeline": len(timeline),
            "milestones": len(milestones),
            "deliverables": len(deliverables),
            "important_events": len(important_events),
            "assistant_decisions": len(assistant_decisions),
            "roleplay_milestones": len(roleplay_milestones),
            "workflow_groups": len(workflow_groups),
        },
        "source_payloads": {
            "activity_intelligence_schema": activity.get("schema_id"),
            "latest_timeline": [_compact_event(item) for item in timeline[:8]],
        },
        "policy": "Smart Brief Builder is read-only. It composes a project brief from Project Workspace context, timeline, handoffs, and activity intelligence; it does not mutate memory or assets.",
    }


def _brief_slug(project_id: str, brief_id: str) -> Path:
    return _safe_path(BRIEFS_DIR / slugify(project_id or "general", "project"), brief_id, "brief")


def _brief_export_path(project_id: str, export_id: str, ext: str = "md") -> Path:
    safe_ext = re.sub(r"[^a-zA-Z0-9]+", "", ext or "md")[:12] or "md"
    root = (BRIEF_EXPORTS_DIR / slugify(project_id or "general", "project")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(export_id, 'brief_export')}.{safe_ext}"
    path = (root / filename).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid project brief export path")
    return path


def _format_brief_markdown(brief_payload: dict[str, Any]) -> str:
    brief = brief_payload.get("brief") if isinstance(brief_payload.get("brief"), dict) else {}
    project = brief_payload.get("project") if isinstance(brief_payload.get("project"), dict) else {}
    title = brief.get("title") or f"{project.get('name') or project.get('project_id') or 'Project'} — Smart Project Brief"
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"**Project ID:** {brief_payload.get('project_id') or project.get('project_id') or 'general'}")
    lines.append(f"**Audience:** {brief_payload.get('audience') or 'internal'}")
    lines.append(f"**Detail:** {brief_payload.get('detail') or 'standard'}")
    lines.append("")
    if brief.get("client_ready_summary"):
        lines += ["## Summary", str(brief.get("client_ready_summary") or ""), ""]
    sections = [
        ("Completed / captured work", brief.get("completed_work") or []),
        ("Open decisions", brief.get("open_decisions") or []),
        ("Creative direction", brief.get("creative_direction") or []),
        ("Next suggested actions", brief.get("next_actions") or []),
    ]
    for heading, values in sections:
        lines.append(f"## {heading}")
        if isinstance(values, list) and values:
            for item in values:
                lines.append(f"- {item}")
        else:
            lines.append("- No items captured yet.")
        lines.append("")
    linked = brief.get("linked_assets") if isinstance(brief.get("linked_assets"), list) else []
    if linked:
        lines.append("## Linked assets")
        for item in linked[:20]:
            if isinstance(item, dict):
                ref = item.get("ref") or item.get("path") or ""
                lines.append(f"- {item.get('title') or item.get('resource_type') or 'Asset'} · {item.get('surface') or 'surface'}{f' · {ref}' if ref else ''}")
            else:
                lines.append(f"- {item}")
        lines.append("")
    assistant_notes = brief.get("assistant_notes") if isinstance(brief.get("assistant_notes"), list) else []
    roleplay_notes = brief.get("roleplay_milestones") if isinstance(brief.get("roleplay_milestones"), list) else []
    if assistant_notes or roleplay_notes:
        lines.append("## Assistant / Roleplay notes")
        for item in (assistant_notes + roleplay_notes)[:20]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('title') or item.get('resource_type') or 'Note'}: {item.get('summary') or ''}")
        lines.append("")
    lines.append("---")
    lines.append("Generated by Neo Project Workspace Smart Brief Builder.")
    return "\n".join(lines).strip() + "\n"


def _project_brief_records(project_id: str = "") -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = BRIEFS_DIR / project_id
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        record = read_json(path, {})
        if isinstance(record, dict) and record.get("brief_id"):
            rows.append(record)
    rows.sort(key=lambda r: r.get("created_at") or r.get("updated_at") or "", reverse=True)
    return rows


def _brief_version_group_for(data: dict[str, Any], project_id: str, brief_id: str) -> tuple[str, int, str]:
    base_id = str(data.get("base_brief_id") or data.get("parent_brief_id") or "")
    explicit_group = str(data.get("version_group_id") or "")
    if explicit_group:
        group_id = slugify(explicit_group, brief_id)
        base_id = base_id or group_id
    elif base_id:
        try:
            base = get_project_brief(project_id, base_id).get("brief") or {}
        except Exception:
            base = {}
        group_id = str(base.get("version_group_id") or base.get("brief_id") or base_id)
        base_id = str(base.get("brief_id") or base_id)
    else:
        group_id = brief_id
    records = _project_brief_records(project_id)
    version_number = 1 + max([int(r.get("version_number") or 0) for r in records if str(r.get("version_group_id") or r.get("brief_id")) == group_id] or [0])
    return group_id, version_number, base_id


def save_project_brief(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    audience = str(data.get("audience") or "internal")
    detail = str(data.get("detail") or "standard")
    brief_payload = data.get("brief_payload") if isinstance(data.get("brief_payload"), dict) else project_smart_brief_payload(project_id, audience=audience, detail=detail, limit=int(data.get("limit") or 160))
    stamp = now_iso()
    brief_id = slugify(data.get("brief_id") or f"brief_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}", "brief")
    version_group_id, version_number, base_brief_id = _brief_version_group_for(data, project_id, brief_id)
    markdown = str(data.get("markdown") or _format_brief_markdown(brief_payload))
    record = {
        "brief_id": brief_id,
        "project_id": project_id,
        "title": str(data.get("title") or ((brief_payload.get("brief") or {}).get("title")) or "Project smart brief"),
        "audience": audience,
        "detail": detail,
        "format": "markdown",
        "markdown": markdown,
        "brief_payload": brief_payload,
        "version_group_id": version_group_id,
        "version_number": version_number,
        "base_brief_id": base_brief_id,
        "is_final": bool(data.get("is_final") or False),
        "final_at": stamp if data.get("is_final") else "",
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": _compatibility_metadata(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
    }
    write_json(_brief_slug(project_id, brief_id), record)
    context = add_project_context({
        "project_id": project_id,
        "title": record["title"],
        "kind": "project_brief",
        "surface": "project_workspace",
        "text": markdown,
        "tags": ["project_brief", audience, detail],
        "source_ref": brief_id,
        "metadata": {"brief_id": brief_id, "audience": audience, "detail": detail},
    }).get("context")
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.brief.saved",
        "surface": "project_workspace",
        "title": record["title"],
        "summary": f"Saved {audience} {detail} project brief.",
        "resource_type": "project_brief",
        "ref_id": brief_id,
        "context_id": (context or {}).get("context_id", ""),
        "metadata": {"brief_id": brief_id},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.brief_saved.v1", "brief": record, "context": context, "timeline_event": timeline}


def list_project_briefs(project_id: str = "", *, limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _project_brief_records(project_id):
        rows.append({k: record.get(k) for k in ("brief_id", "project_id", "title", "audience", "detail", "format", "version_group_id", "version_number", "base_brief_id", "is_final", "final_at", "created_at", "updated_at")})
    return rows[: max(1, int(limit or 30))]


def get_project_brief(project_id: str = "", brief_id: str = "") -> dict[str, Any]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    if not brief_id:
        rows = list_project_briefs(project_id, limit=1)
        brief_id = rows[0]["brief_id"] if rows else ""
    if not brief_id:
        raise FileNotFoundError("No project brief found")
    record = read_json(_brief_slug(project_id, brief_id), {})
    if not isinstance(record, dict) or not record.get("brief_id"):
        raise FileNotFoundError("Project brief not found")
    return {"ok": True, "schema_id": "neo.project_workspace.brief.v1", "brief": record}


def project_briefs_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    return {"ok": True, "schema_id": "neo.project_workspace.briefs.v1", "project_id": project_id, "briefs": list_project_briefs(project_id, limit=limit), "save_endpoint": "/api/projects/workspace/brief/save", "export_endpoint": "/api/projects/workspace/brief/export"}



def project_brief_versions_payload(project_id: str = "", brief_id: str = "", *, limit: int = 50) -> dict[str, Any]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    if not brief_id:
        rows = list_project_briefs(project_id, limit=1)
        brief_id = str(rows[0].get("brief_id") or "") if rows else ""
    if not brief_id:
        return {"ok": True, "schema_id": "neo.project_workspace.brief_versions.v1", "project_id": project_id, "brief_id": "", "version_group_id": "", "versions": []}
    current = get_project_brief(project_id, brief_id).get("brief")
    group_id = str((current or {}).get("version_group_id") or brief_id)
    versions: list[dict[str, Any]] = []
    for record in _project_brief_records(project_id):
        if str(record.get("version_group_id") or record.get("brief_id")) != group_id:
            continue
        versions.append({k: record.get(k) for k in ("brief_id", "project_id", "title", "audience", "detail", "format", "version_group_id", "version_number", "base_brief_id", "is_final", "final_at", "created_at", "updated_at")})
    versions.sort(key=lambda r: int(r.get("version_number") or 0), reverse=True)
    return {"ok": True, "schema_id": "neo.project_workspace.brief_versions.v1", "project_id": project_id, "brief_id": brief_id, "version_group_id": group_id, "versions": versions[: max(1, int(limit or 50))]}


def compare_project_briefs(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    left_id = str(data.get("left_brief_id") or data.get("base_brief_id") or "")
    right_id = str(data.get("right_brief_id") or data.get("brief_id") or "")
    if not left_id or not right_id:
        versions = project_brief_versions_payload(project_id, right_id or left_id).get("versions", [])
        if len(versions) >= 2:
            right_id = right_id or str(versions[0].get("brief_id") or "")
            left_id = left_id or str(versions[1].get("brief_id") or "")
    if not left_id or not right_id:
        raise ValueError("Two project brief IDs are required for comparison")
    left = get_project_brief(project_id, left_id).get("brief") or {}
    right = get_project_brief(project_id, right_id).get("brief") or {}
    left_lines = str(left.get("markdown") or "").splitlines()
    right_lines = str(right.get("markdown") or "").splitlines()
    diff_lines = list(difflib.unified_diff(left_lines, right_lines, fromfile=left_id, tofile=right_id, lineterm=""))
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    changed_sections: list[str] = []
    for line in right_lines:
        if line.startswith("## "):
            changed_sections.append(line.replace("## ", "", 1).strip())
    return {
        "ok": True,
        "schema_id": "neo.project_workspace.brief_compare.v1",
        "project_id": project_id,
        "left_brief": {k: left.get(k) for k in ("brief_id", "title", "version_number", "version_group_id", "created_at", "is_final")},
        "right_brief": {k: right.get(k) for k in ("brief_id", "title", "version_number", "version_group_id", "created_at", "is_final")},
        "summary": {"added_lines": added, "removed_lines": removed, "diff_lines": len(diff_lines), "right_sections": changed_sections[:12]},
        "diff": "\n".join(diff_lines[:1200]),
        "policy": "Project brief compare is read-only and uses stored Markdown snapshots.",
    }


def restore_project_brief_version(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    brief_id = str(data.get("brief_id") or "")
    if not brief_id:
        raise ValueError("brief_id is required")
    source = get_project_brief(project_id, brief_id).get("brief") or {}
    restored = save_project_brief({
        "project_id": project_id,
        "base_brief_id": str(source.get("version_group_id") or source.get("brief_id") or brief_id),
        "version_group_id": str(source.get("version_group_id") or source.get("brief_id") or brief_id),
        "title": str(data.get("title") or f"Restored: {source.get('title') or brief_id}"),
        "audience": source.get("audience") or "internal",
        "detail": source.get("detail") or "standard",
        "markdown": source.get("markdown") or "",
        "brief_payload": source.get("brief_payload") if isinstance(source.get("brief_payload"), dict) else {},
        "metadata": {"restored_from_brief_id": brief_id, "restore_note": data.get("note") or ""},
    })
    add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.brief.version_restored",
        "surface": "project_workspace",
        "title": "Project brief version restored",
        "summary": f"Restored brief version {brief_id} as a new version.",
        "resource_type": "project_brief",
        "ref_id": str((restored.get("brief") or {}).get("brief_id") or ""),
        "metadata": {"source_brief_id": brief_id},
    })
    return {"ok": True, "schema_id": "neo.project_workspace.brief_restored.v1", "restored_from": brief_id, "brief": restored.get("brief"), "timeline_event": restored.get("timeline_event")}


def mark_project_brief_final(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    brief_id = str(data.get("brief_id") or "")
    if not brief_id:
        raise ValueError("brief_id is required")
    current = get_project_brief(project_id, brief_id).get("brief") or {}
    group_id = str(current.get("version_group_id") or brief_id)
    stamp = now_iso()
    updated: list[str] = []
    for record in _project_brief_records(project_id):
        if str(record.get("version_group_id") or record.get("brief_id")) != group_id:
            continue
        record["is_final"] = str(record.get("brief_id")) == brief_id
        record["final_at"] = stamp if record["is_final"] else ""
        record["updated_at"] = stamp
        write_json(_brief_slug(project_id, str(record.get("brief_id") or "")), record)
        updated.append(str(record.get("brief_id") or ""))
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.brief.finalized",
        "surface": "project_workspace",
        "title": "Project brief marked final",
        "summary": f"Marked {brief_id} as final for version group {group_id}.",
        "resource_type": "project_brief",
        "ref_id": brief_id,
        "metadata": {"brief_id": brief_id, "version_group_id": group_id, "updated_versions": updated},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.brief_finalized.v1", "project_id": project_id, "brief_id": brief_id, "version_group_id": group_id, "updated_versions": updated, "timeline_event": timeline}


# ---------------------------------------------------------------------------
# Phase 34: Project Milestones + Deliverable Tracker
# ---------------------------------------------------------------------------

MILESTONE_STATUSES = {"planned", "active", "blocked", "completed", "approved", "archived"}
DELIVERABLE_STATUSES = {"todo", "in_progress", "ready_for_review", "revision_needed", "approved", "delivered", "archived"}
REVIEW_STATES = {"not_started", "internal_review", "client_review", "approved", "revision_requested"}

def _project_record_dir(root: Path, project_id: str) -> Path:
    project_id = slugify(project_id or "general", "project")
    path = (root / project_id).resolve()
    root_resolved = root.resolve()
    if root_resolved not in path.parents and path != root_resolved:
        raise ValueError("Invalid project record path")
    path.mkdir(parents=True, exist_ok=True)
    return path

def _milestone_path(project_id: str, milestone_id: str) -> Path:
    return _safe_path(_project_record_dir(MILESTONES_DIR, project_id), milestone_id, "milestone")

def _deliverable_path(project_id: str, deliverable_id: str) -> Path:
    return _safe_path(_project_record_dir(DELIVERABLES_DIR, project_id), deliverable_id, "deliverable")

def save_project_milestone(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    milestone_id = slugify(data.get("milestone_id") or f"milestone_{uuid4().hex[:12]}", "milestone")
    existing = read_json(_milestone_path(project_id, milestone_id), {})
    stamp = now_iso()
    status = str(data.get("status") or existing.get("status") or "planned").strip().lower()
    if status not in MILESTONE_STATUSES:
        status = "planned"
    linked_briefs = data.get("linked_brief_ids") if isinstance(data.get("linked_brief_ids"), list) else existing.get("linked_brief_ids", [])
    linked_assets = data.get("linked_asset_refs") if isinstance(data.get("linked_asset_refs"), list) else existing.get("linked_asset_refs", [])
    record = {
        "milestone_id": milestone_id,
        "project_id": project_id,
        "title": str(data.get("title") or existing.get("title") or "Project milestone"),
        "summary": str(data.get("summary") or data.get("notes") or existing.get("summary") or ""),
        "status": status,
        "due_date": str(data.get("due_date") or existing.get("due_date") or ""),
        "completed_at": str(data.get("completed_at") or existing.get("completed_at") or (stamp if status in {"completed", "approved"} and not existing.get("completed_at") else "")),
        "approved_at": str(data.get("approved_at") or existing.get("approved_at") or (stamp if status == "approved" and not existing.get("approved_at") else "")),
        "linked_brief_ids": linked_briefs,
        "linked_asset_refs": linked_assets,
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else existing.get("tags", []),
        "created_at": existing.get("created_at") or stamp,
        "updated_at": stamp,
        "metadata": {**(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}), **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})},
    }
    write_json(_milestone_path(project_id, milestone_id), record)
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.milestone.updated",
        "surface": "project_workspace",
        "title": record["title"],
        "summary": f"Milestone {status}: {record['summary'] or record['title']}",
        "resource_type": "project_milestone",
        "ref_id": milestone_id,
        "metadata": {"milestone_id": milestone_id, "status": status, "due_date": record.get("due_date", "")},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.milestone_saved.v1", "milestone": record, "timeline_event": timeline}

def list_project_milestones(project_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = MILESTONES_DIR / project_id
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            record = read_json(path, {})
            if isinstance(record, dict) and record.get("milestone_id"):
                rows.append(record)
    rows.sort(key=lambda r: (str(r.get("due_date") or "9999-99-99"), str(r.get("updated_at") or "")), reverse=False)
    return rows[: max(1, int(limit or 50))]

def save_project_deliverable(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    deliverable_id = slugify(data.get("deliverable_id") or f"deliverable_{uuid4().hex[:12]}", "deliverable")
    existing = read_json(_deliverable_path(project_id, deliverable_id), {})
    stamp = now_iso()
    status = str(data.get("status") or existing.get("status") or "todo").strip().lower()
    if status not in DELIVERABLE_STATUSES:
        status = "todo"
    review_state = str(data.get("review_state") or existing.get("review_state") or "not_started").strip().lower()
    if review_state not in REVIEW_STATES:
        review_state = "not_started"
    linked_briefs = data.get("linked_brief_ids") if isinstance(data.get("linked_brief_ids"), list) else existing.get("linked_brief_ids", [])
    linked_assets = data.get("linked_asset_refs") if isinstance(data.get("linked_asset_refs"), list) else existing.get("linked_asset_refs", [])
    milestone_id = str(data.get("milestone_id") or existing.get("milestone_id") or "")
    record = {
        "deliverable_id": deliverable_id,
        "project_id": project_id,
        "milestone_id": milestone_id,
        "title": str(data.get("title") or existing.get("title") or "Project deliverable"),
        "summary": str(data.get("summary") or data.get("notes") or existing.get("summary") or ""),
        "status": status,
        "review_state": review_state,
        "due_date": str(data.get("due_date") or existing.get("due_date") or ""),
        "delivery_format": str(data.get("delivery_format") or existing.get("delivery_format") or ""),
        "priority": str(data.get("priority") or existing.get("priority") or "normal"),
        "approved_at": str(data.get("approved_at") or existing.get("approved_at") or (stamp if review_state == "approved" and not existing.get("approved_at") else "")),
        "delivered_at": str(data.get("delivered_at") or existing.get("delivered_at") or (stamp if status == "delivered" and not existing.get("delivered_at") else "")),
        "linked_brief_ids": linked_briefs,
        "linked_asset_refs": linked_assets,
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else existing.get("tags", []),
        "created_at": existing.get("created_at") or stamp,
        "updated_at": stamp,
        "metadata": {**(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}), **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})},
    }
    write_json(_deliverable_path(project_id, deliverable_id), record)
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.deliverable.updated",
        "surface": "project_workspace",
        "title": record["title"],
        "summary": f"Deliverable {status} / {review_state}: {record['summary'] or record['title']}",
        "resource_type": "project_deliverable",
        "ref_id": deliverable_id,
        "metadata": {"deliverable_id": deliverable_id, "milestone_id": milestone_id, "status": status, "review_state": review_state, "due_date": record.get("due_date", "")},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.deliverable_saved.v1", "deliverable": record, "timeline_event": timeline}

def list_project_deliverables(project_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = DELIVERABLES_DIR / project_id
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            record = read_json(path, {})
            if isinstance(record, dict) and record.get("deliverable_id"):
                rows.append(record)
    rows.sort(key=lambda r: (str(r.get("due_date") or "9999-99-99"), str(r.get("priority") or "normal"), str(r.get("updated_at") or "")), reverse=False)
    return rows[: max(1, int(limit or 100))]

def project_deliverable_summary(project_id: str = "") -> dict[str, Any]:
    milestones = list_project_milestones(project_id, limit=1000)
    deliverables = list_project_deliverables(project_id, limit=1000)
    by_status: dict[str, int] = {}
    by_review: dict[str, int] = {}
    by_milestone: dict[str, int] = {}
    blocked: list[dict[str, Any]] = []
    due_soon: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    for item in deliverables:
        status = str(item.get("status") or "todo")
        review = str(item.get("review_state") or "not_started")
        milestone_id = str(item.get("milestone_id") or "unassigned")
        _add_count(by_status, status)
        _add_count(by_review, review)
        _add_count(by_milestone, milestone_id)
        if status == "blocked" or review == "revision_requested":
            blocked.append(item)
        if review == "approved" or status in {"approved", "delivered"}:
            approved.append(item)
        if status not in {"approved", "delivered", "archived"}:
            open_items.append(item)
        due = str(item.get("due_date") or "")[:10]
        try:
            due_date = datetime.fromisoformat(due).date()
            if (due_date - today).days <= 7 and status not in {"approved", "delivered", "archived"}:
                due_soon.append(item)
        except Exception:
            pass
    return {
        "milestone_count": len(milestones),
        "deliverable_count": len(deliverables),
        "open_count": len(open_items),
        "approved_count": len(approved),
        "blocked_count": len(blocked),
        "due_soon_count": len(due_soon),
        "by_status": by_status,
        "by_review_state": by_review,
        "by_milestone": by_milestone,
        "blocked": blocked[:10],
        "due_soon": due_soon[:10],
        "open_items": open_items[:12],
    }

def project_deliverable_tracker_payload(project_id: str = "", *, limit: int = 100) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    milestones = list_project_milestones(resolved_project_id, limit=limit)
    deliverables = list_project_deliverables(resolved_project_id, limit=limit)
    summary = project_deliverable_summary(resolved_project_id)
    milestone_map = {str(m.get("milestone_id") or ""): m for m in milestones}
    rows = []
    for item in deliverables:
        milestone = milestone_map.get(str(item.get("milestone_id") or ""), {})
        rows.append({**item, "milestone_title": milestone.get("title") or "Unassigned milestone", "is_blocked": item.get("status") == "blocked" or item.get("review_state") == "revision_requested"})
    return {
        "ok": True,
        "schema_id": "neo.project_workspace.deliverable_tracker.v1",
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "milestones": milestones,
        "deliverables": rows,
        "summary": summary,
        "allowed_statuses": {"milestone": sorted(MILESTONE_STATUSES), "deliverable": sorted(DELIVERABLE_STATUSES), "review": sorted(REVIEW_STATES)},
        "policy": "Deliverable Tracker stores project management metadata and timeline events. It does not mutate asset files.",
    }



# ---------------------------------------------------------------------------
# Phase 36: Project Review Queue + Approval Workflow
# ---------------------------------------------------------------------------

REVIEW_STATUSES = {"pending", "in_review", "approved", "revision_requested", "rejected", "resolved", "archived"}
REVIEW_SCOPES = {"internal", "client", "creative", "technical"}
REVIEW_DECISIONS = {"start_review", "approve", "request_revision", "reject", "resolve", "archive", "reopen"}
REVIEW_PRIORITIES = {"low", "normal", "high", "urgent"}


def _review_path(project_id: str, review_id: str) -> Path:
    return _safe_path(_project_record_dir(REVIEW_QUEUE_DIR, project_id), review_id, "review")


def _normalize_review_refs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def save_project_review_item(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    review_id = slugify(data.get("review_id") or f"review_{uuid4().hex[:12]}", "review")
    existing = read_json(_review_path(project_id, review_id), {})
    stamp = now_iso()
    status = str(data.get("status") or existing.get("status") or "pending").strip().lower()
    if status not in REVIEW_STATUSES:
        status = "pending"
    scope = str(data.get("review_scope") or data.get("scope") or existing.get("review_scope") or "internal").strip().lower()
    if scope not in REVIEW_SCOPES:
        scope = "internal"
    priority = str(data.get("priority") or existing.get("priority") or "normal").strip().lower()
    if priority not in REVIEW_PRIORITIES:
        priority = "normal"
    target_type = str(data.get("target_type") or existing.get("target_type") or ("deliverable" if data.get("deliverable_id") else "custom")).strip().lower()
    target_id = str(data.get("target_id") or data.get("deliverable_id") or data.get("milestone_id") or data.get("brief_id") or existing.get("target_id") or "")
    linked_deliverables = _normalize_review_refs(data.get("linked_deliverable_ids") or data.get("deliverable_ids") or existing.get("linked_deliverable_ids") or ([] if not data.get("deliverable_id") else [data.get("deliverable_id")]))
    linked_milestones = _normalize_review_refs(data.get("linked_milestone_ids") or data.get("milestone_ids") or existing.get("linked_milestone_ids") or ([] if not data.get("milestone_id") else [data.get("milestone_id")]))
    linked_briefs = _normalize_review_refs(data.get("linked_brief_ids") or data.get("brief_ids") or existing.get("linked_brief_ids") or ([] if not data.get("brief_id") else [data.get("brief_id")]))
    linked_assets = _normalize_review_refs(data.get("linked_asset_refs") or existing.get("linked_asset_refs") or [])
    record = {
        "review_id": review_id,
        "project_id": project_id,
        "target_type": target_type,
        "target_id": target_id,
        "title": str(data.get("title") or existing.get("title") or "Project review item"),
        "summary": str(data.get("summary") or data.get("notes") or existing.get("summary") or ""),
        "status": status,
        "review_scope": scope,
        "priority": priority,
        "requested_by": str(data.get("requested_by") or existing.get("requested_by") or ""),
        "reviewer": str(data.get("reviewer") or existing.get("reviewer") or ""),
        "due_date": str(data.get("due_date") or existing.get("due_date") or ""),
        "decision": str(data.get("decision") or existing.get("decision") or ""),
        "decision_notes": str(data.get("decision_notes") or existing.get("decision_notes") or ""),
        "approved_at": str(data.get("approved_at") or existing.get("approved_at") or (stamp if status == "approved" and not existing.get("approved_at") else "")),
        "resolved_at": str(data.get("resolved_at") or existing.get("resolved_at") or (stamp if status in {"approved", "resolved", "rejected"} and not existing.get("resolved_at") else "")),
        "linked_deliverable_ids": linked_deliverables,
        "linked_milestone_ids": linked_milestones,
        "linked_brief_ids": linked_briefs,
        "linked_asset_refs": linked_assets,
        "created_at": existing.get("created_at") or stamp,
        "updated_at": stamp,
        "metadata": {**(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}), **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {})},
    }
    write_json(_review_path(project_id, review_id), record)
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.review.item.updated",
        "surface": "project_workspace",
        "title": record["title"],
        "summary": f"Review {status}: {record['summary'] or record['title']}",
        "resource_type": "project_review_item",
        "ref_id": review_id,
        "metadata": {"review_id": review_id, "status": status, "review_scope": scope, "target_type": target_type, "target_id": target_id},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.review_item_saved.v1", "review_item": record, "timeline_event": timeline}


def list_project_review_items(project_id: str = "", *, limit: int = 100, status: str = "", review_scope: str = "") -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = REVIEW_QUEUE_DIR / project_id
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            record = read_json(path, {})
            if isinstance(record, dict) and record.get("review_id"):
                if status and str(record.get("status") or "") != status:
                    continue
                if review_scope and str(record.get("review_scope") or "") != review_scope:
                    continue
                rows.append(record)
    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    rows.sort(key=lambda r: (priority_order.get(str(r.get("priority") or "normal"), 2), str(r.get("due_date") or "9999-99-99"), str(r.get("updated_at") or "")))
    return rows[: max(1, int(limit or 100))]


def _auto_review_candidates(project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    existing_targets = {str(item.get("target_type") or "") + ":" + str(item.get("target_id") or "") for item in list_project_review_items(project_id, limit=1000)}
    candidates: list[dict[str, Any]] = []
    for deliverable in list_project_deliverables(project_id, limit=1000):
        status = str(deliverable.get("status") or "")
        review_state = str(deliverable.get("review_state") or "")
        if status in {"ready_for_review", "revision_needed"} or review_state in {"internal_review", "client_review", "revision_requested"}:
            key = "deliverable:" + str(deliverable.get("deliverable_id") or "")
            if key in existing_targets:
                continue
            scope = "client" if review_state == "client_review" else "internal"
            candidates.append({
                "candidate_id": f"candidate_{deliverable.get('deliverable_id')}",
                "project_id": project_id,
                "target_type": "deliverable",
                "target_id": deliverable.get("deliverable_id"),
                "title": deliverable.get("title") or deliverable.get("deliverable_id"),
                "summary": deliverable.get("summary") or "Deliverable is ready for review or needs revision tracking.",
                "status": "pending" if review_state != "revision_requested" else "revision_requested",
                "review_scope": scope,
                "priority": "high" if review_state == "revision_requested" else "normal",
                "due_date": deliverable.get("due_date") or "",
                "source": "deliverable_tracker",
            })
    return candidates[: max(1, int(limit or 50))]


def project_review_queue_payload(project_id: str = "", *, limit: int = 100, include_auto: bool = True) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    items = list_project_review_items(resolved_project_id, limit=limit)
    auto_candidates = _auto_review_candidates(resolved_project_id, limit=limit) if include_auto else []
    by_status: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for item in items:
        by_status[str(item.get("status") or "pending")] = by_status.get(str(item.get("status") or "pending"), 0) + 1
        by_scope[str(item.get("review_scope") or "internal")] = by_scope.get(str(item.get("review_scope") or "internal"), 0) + 1
        by_priority[str(item.get("priority") or "normal")] = by_priority.get(str(item.get("priority") or "normal"), 0) + 1
    pending = [item for item in items if item.get("status") in {"pending", "in_review"}]
    revisions = [item for item in items if item.get("status") == "revision_requested"]
    approvals = [item for item in items if item.get("status") == "approved"]
    client_review = [item for item in items if item.get("review_scope") == "client" and item.get("status") in {"pending", "in_review", "revision_requested"}]
    internal_review = [item for item in items if item.get("review_scope") == "internal" and item.get("status") in {"pending", "in_review", "revision_requested"}]
    focus = []
    if revisions:
        focus.append("Resolve revision-requested items before delivery approval.")
    if client_review:
        focus.append("Check client review items and confirm approval/revision state.")
    if auto_candidates:
        focus.append("Convert ready-for-review deliverables into explicit review queue items.")
    if not focus:
        focus.append("Review queue is clean. Keep approvals linked to deliverables and milestones.")
    return {
        "ok": True,
        "schema_id": "neo.project_workspace.review_queue.v1",
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "review_items": items,
        "auto_candidates": auto_candidates,
        "summary": {
            "review_count": len(items),
            "pending_count": len(pending),
            "revision_requested_count": len(revisions),
            "approved_count": len(approvals),
            "client_review_count": len(client_review),
            "internal_review_count": len(internal_review),
            "auto_candidate_count": len(auto_candidates),
            "by_status": by_status,
            "by_scope": by_scope,
            "by_priority": by_priority,
        },
        "recommended_focus": focus,
        "allowed_statuses": sorted(REVIEW_STATUSES),
        "allowed_scopes": sorted(REVIEW_SCOPES),
        "allowed_decisions": sorted(REVIEW_DECISIONS),
        "policy": "Review Queue is explicit and audit-friendly. Decisions update linked deliverable/milestone states only through reviewed actions.",
    }


def apply_project_review_decision(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    review_id = str(data.get("review_id") or "").strip()
    if not review_id:
        raise ValueError("review_id is required")
    decision = str(data.get("decision") or "").strip().lower()
    if decision not in REVIEW_DECISIONS:
        raise ValueError("Unsupported review decision")
    record = read_json(_review_path(project_id, review_id), {})
    if not isinstance(record, dict) or not record.get("review_id"):
        raise ValueError("Review item not found")
    stamp = now_iso()
    notes = str(data.get("decision_notes") or data.get("notes") or "")
    status_map = {
        "start_review": "in_review",
        "approve": "approved",
        "request_revision": "revision_requested",
        "reject": "rejected",
        "resolve": "resolved",
        "archive": "archived",
        "reopen": "pending",
    }
    record["status"] = status_map[decision]
    record["decision"] = decision
    if notes:
        record["decision_notes"] = notes
    if decision == "approve":
        record["approved_at"] = stamp
        record["resolved_at"] = stamp
    elif decision in {"reject", "resolve", "archive"}:
        record["resolved_at"] = stamp
    record["updated_at"] = stamp
    write_json(_review_path(project_id, review_id), record)
    linked_updates: list[dict[str, Any]] = []
    for deliverable_id in record.get("linked_deliverable_ids") or ([record.get("target_id")] if record.get("target_type") == "deliverable" and record.get("target_id") else []):
        existing = read_json(_deliverable_path(project_id, str(deliverable_id)), {})
        if not existing:
            continue
        update_payload = {"project_id": project_id, "deliverable_id": deliverable_id, **existing}
        if decision == "approve":
            update_payload.update({"status": "approved", "review_state": "approved"})
        elif decision == "request_revision":
            update_payload.update({"status": "revision_needed", "review_state": "revision_requested"})
        elif decision == "start_review":
            update_payload.update({"status": "ready_for_review", "review_state": record.get("review_scope") == "client" and "client_review" or "internal_review"})
        elif decision == "archive":
            update_payload.update({"status": "archived"})
        saved = save_project_deliverable(update_payload).get("deliverable")
        linked_updates.append({"type": "deliverable", "id": deliverable_id, "record": saved})
    for milestone_id in record.get("linked_milestone_ids") or ([record.get("target_id")] if record.get("target_type") == "milestone" and record.get("target_id") else []):
        existing = read_json(_milestone_path(project_id, str(milestone_id)), {})
        if not existing:
            continue
        update_payload = {"project_id": project_id, "milestone_id": milestone_id, **existing}
        if decision == "approve":
            update_payload.update({"status": "approved"})
        elif decision == "request_revision":
            update_payload.update({"status": "blocked"})
        elif decision == "archive":
            update_payload.update({"status": "archived"})
        saved = save_project_milestone(update_payload).get("milestone")
        linked_updates.append({"type": "milestone", "id": milestone_id, "record": saved})
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.review.decision_applied",
        "surface": "project_workspace",
        "title": f"Review decision: {decision.replace('_', ' ')}",
        "summary": notes or f"Applied {decision} to review item {review_id}.",
        "resource_type": "project_review_decision",
        "ref_id": review_id,
        "metadata": {"review_id": review_id, "decision": decision, "status": record.get("status"), "linked_updates": linked_updates},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.review_decision.v1", "project_id": project_id, "review_item": record, "linked_updates": linked_updates, "timeline_event": timeline}


def project_approval_workflow_payload(project_id: str = "", *, limit: int = 100) -> dict[str, Any]:
    queue = project_review_queue_payload(project_id, limit=limit)
    project_id = str(queue.get("project_id") or active_project_payload().get("active_project_id") or "general")
    tracker = project_deliverable_tracker_payload(project_id, limit=limit)
    dashboard = project_delivery_dashboard_payload(project_id, audience="client", limit=limit)
    summary = queue.get("summary") or {}
    deliverable_summary = tracker.get("summary") or {}
    approval_blockers = []
    if summary.get("revision_requested_count"):
        approval_blockers.append({"level": "high", "label": "Revision requests open", "count": summary.get("revision_requested_count")})
    if deliverable_summary.get("blocked_count"):
        approval_blockers.append({"level": "high", "label": "Blocked deliverables", "count": deliverable_summary.get("blocked_count")})
    if (dashboard.get("delivery_status") or {}).get("overdue_count"):
        approval_blockers.append({"level": "medium", "label": "Overdue deliverables", "count": (dashboard.get("delivery_status") or {}).get("overdue_count")})
    if not approval_blockers:
        approval_blockers.append({"level": "ok", "label": "No approval blockers detected", "count": 0})
    return {
        "ok": True,
        "schema_id": "neo.project_workspace.approval_workflow.v1",
        "status": "ready",
        "project_id": project_id,
        "review_queue": queue,
        "deliverable_tracker": tracker,
        "delivery_dashboard": dashboard,
        "approval_blockers": approval_blockers,
        "approval_summary": {
            "pending_reviews": summary.get("pending_count", 0),
            "client_reviews": summary.get("client_review_count", 0),
            "revision_requests": summary.get("revision_requested_count", 0),
            "approved_reviews": summary.get("approved_count", 0),
            "approved_or_delivered_deliverables": deliverable_summary.get("approved_count", 0) + deliverable_summary.get("delivered_count", 0),
        },
        "policy": "Approval Workflow is review-gated. It records decisions, updates linked delivery states, and writes timeline events for auditability.",
    }



# ---------------------------------------------------------------------------
# Phase 37: Project Package Builder
# ---------------------------------------------------------------------------

PACKAGE_SCHEMA = "neo.project_workspace.package_builder.v1"
PACKAGE_MANIFEST_SCHEMA = "neo.project_workspace.package_manifest.v1"
PACKAGE_INCLUDE_KEYS = {"briefs", "status_report", "milestones", "deliverables", "reviews", "asset_references", "timeline", "context", "handoffs", "local_files", "roleplay_links"}


def _package_root(project_id: str) -> Path:
    return _project_record_dir(PACKAGES_DIR, project_id)


def _package_path(project_id: str, package_id: str) -> Path:
    return _safe_path(_package_root(project_id), package_id, "package")


def _safe_package_file_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    clean = str(relative or "").replace("\\", "/").strip("/")
    if not clean:
        raise ValueError("Invalid package file path")
    parts = []
    for part in clean.split("/"):
        if not part or part in {".", ".."}:
            continue
        safe_part = re.sub(r"[^a-zA-Z0-9._ -]+", "", part).strip("._- ")[:100] or "item"
        parts.append(safe_part)
    path = (root / Path(*parts)).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid package file path")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_package_text(root: Path, relative: str, content: str) -> str:
    path = _safe_package_file_path(root, relative)
    path.write_text(str(content or ""), encoding="utf-8")
    return str(path.relative_to(root))


def _write_package_json(root: Path, relative: str, payload: Any) -> str:
    return _write_package_text(root, relative, json.dumps(payload, indent=2, ensure_ascii=False))


def _safe_local_source_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        else:
            path = path.resolve()
        allowed_roots = [ROOT_DIR.resolve(), WORKSPACE_DIR.resolve()]
        if not any(path == root or root in path.parents for root in allowed_roots):
            return None
        if not path.exists() or not path.is_file():
            return None
        return path
    except Exception:
        return None


def _package_readme(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    summary = payload.get("summary") or {}
    included = payload.get("included") or {}
    lines = [
        f"# Delivery Package — {project.get('name') or payload.get('project_id') or 'Project'}",
        "",
        f"**Project ID:** {payload.get('project_id') or 'general'}",
        f"**Package ID:** {payload.get('package_id') or ''}",
        f"**Created:** {payload.get('created_at') or now_iso()}",
        f"**Status:** {payload.get('status') or 'built'}",
        "",
        "## Package summary",
        f"- Briefs: {summary.get('brief_count', 0)}",
        f"- Deliverables: {summary.get('deliverable_count', 0)}",
        f"- Milestones: {summary.get('milestone_count', 0)}",
        f"- Review items: {summary.get('review_count', 0)}",
        f"- Asset references: {summary.get('asset_reference_count', 0)}",
        f"- Timeline events: {summary.get('timeline_count', 0)}",
        "",
        "## Included sections",
    ]
    for key, value in included.items():
        lines.append(f"- {key}: {'yes' if value else 'no'}")
    if payload.get("notes"):
        lines += ["", "## Notes", str(payload.get("notes") or "")]
    lines += ["", "---", "Generated by Neo Studio Project Package Builder."]
    return "\n".join(lines).strip() + "\n"


def _list_project_packages(project_id: str = "", *, limit: int = 30) -> list[dict[str, Any]]:
    project_id = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = PACKAGES_DIR / slugify(project_id, "project")
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            record = read_json(path, {})
            if isinstance(record, dict) and record.get("package_id"):
                rows.append({k: record.get(k) for k in ("package_id", "project_id", "title", "status", "created_at", "updated_at", "package_dir", "zip_path", "manifest_path")})
    rows.sort(key=lambda r: str(r.get("created_at") or r.get("updated_at") or ""), reverse=True)
    return rows[: max(1, int(limit or 30))]




def _roleplay_project_link_records(project_id: str = "", *, limit: int = 1000) -> list[dict[str, Any]]:
    resolved = str(project_id or active_project_payload().get("active_project_id") or "general")
    root = ROOT_DIR / "neo_data" / "roleplay" / "projects" / "links" / slugify(resolved, "general")
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in root.glob("*.json"):
            record = read_json(path, {})
            if isinstance(record, dict) and record.get("link_id"):
                rows.append(record)
    rows.sort(key=lambda row: str(row.get("created_at") or row.get("updated_at") or ""), reverse=True)
    return rows[: max(1, int(limit or 1000))]

def project_package_builder_payload(project_id: str = "", *, limit: int = 50) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    briefs = list_project_briefs(resolved_project_id, limit=limit)
    tracker = project_deliverable_tracker_payload(resolved_project_id, limit=limit)
    queue = project_review_queue_payload(resolved_project_id, limit=limit, include_auto=True)
    tray = project_workspace_asset_tray_payload(resolved_project_id, limit=limit)
    packages = _list_project_packages(resolved_project_id, limit=limit)
    deliverables = tracker.get("deliverables") or []
    milestones = tracker.get("milestones") or []
    review_items = queue.get("review_items") or []
    asset_refs = (tray.get("links") or []) + (tray.get("handoffs") or [])
    roleplay_links = _roleplay_project_link_records(resolved_project_id, limit=limit)
    blockers: list[dict[str, Any]] = []
    revision_count = len([item for item in review_items if str(item.get("status") or "") == "revision_requested"])
    blocked_deliverables = len([item for item in deliverables if str(item.get("status") or "") in {"blocked", "revision_needed"} or str(item.get("review_state") or "") == "revision_requested"])
    if revision_count:
        blockers.append({"level": "high", "label": "Open revision requests", "count": revision_count})
    if blocked_deliverables:
        blockers.append({"level": "high", "label": "Blocked/revision deliverables", "count": blocked_deliverables})
    if not blockers:
        blockers.append({"level": "ok", "label": "No package blockers detected", "count": 0})
    return {
        "ok": True,
        "schema_id": PACKAGE_SCHEMA,
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "summary": {
            "brief_count": len(briefs),
            "milestone_count": len(milestones),
            "deliverable_count": len(deliverables),
            "review_count": len(review_items),
            "asset_reference_count": len(asset_refs),
            "roleplay_link_count": len(roleplay_links),
            "package_count": len(packages),
        },
        "briefs": briefs,
        "milestones": milestones[:limit],
        "deliverables": deliverables[:limit],
        "review_items": review_items[:limit],
        "asset_references": asset_refs[:limit],
        "roleplay_links": roleplay_links[:limit],
        "recent_packages": packages,
        "package_blockers": blockers,
        "allowed_include_sections": sorted(PACKAGE_INCLUDE_KEYS),
        "build_endpoint": "/api/projects/workspace/package/build",
        "policy": "Project Package Builder collects generated project records and safe local references into a delivery package. It does not delete originals or publish externally.",
    }


def build_project_package(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    if not get_project_workspace(project_id):
        save_project_workspace({"project_id": project_id, "name": project_id})
    project = get_project_workspace(project_id) or _default_workspace()
    stamp = now_iso()
    requested_sections = data.get("include_sections") if isinstance(data.get("include_sections"), list) else []
    include = {key: (not requested_sections or key in requested_sections) for key in PACKAGE_INCLUDE_KEYS}
    if "include_local_files" in data:
        include["local_files"] = bool(data.get("include_local_files"))
    package_id = slugify(data.get("package_id") or f"package_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}", "package")
    title = str(data.get("title") or f"{project.get('name') or project_id} delivery package")
    package_dir = (_package_root(project_id) / package_id).resolve()
    root = _package_root(project_id).resolve()
    if root not in package_dir.parents and package_dir != root:
        raise ValueError("Invalid package directory")
    if package_dir.exists() and not bool(data.get("overwrite", False)):
        package_id = slugify(f"{package_id}_{uuid4().hex[:4]}", "package")
        package_dir = (_package_root(project_id) / package_id).resolve()
    package_dir.mkdir(parents=True, exist_ok=True)

    briefs = list_project_briefs(project_id, limit=1000)
    milestones = list_project_milestones(project_id, limit=1000)
    deliverables = list_project_deliverables(project_id, limit=1000)
    reviews = list_project_review_items(project_id, limit=1000)
    timeline = list_project_timeline(project_id, limit=1000)
    context_payload = project_workspace_context_payload(project_id, limit=1000)
    tray_payload = project_workspace_asset_tray_payload(project_id, limit=1000)
    dashboard = project_delivery_dashboard_payload(project_id, audience="client", limit=1000)
    roleplay_links = _roleplay_project_link_records(project_id, limit=1000)
    files: list[dict[str, Any]] = []
    copied_files: list[dict[str, Any]] = []

    def add_file(kind: str, rel: str, content: Any, is_json: bool = False) -> None:
        written = _write_package_json(package_dir, rel, content) if is_json else _write_package_text(package_dir, rel, str(content or ""))
        files.append({"kind": kind, "relative_path": written})

    # Always include the machine manifest snapshot and human README.
    snapshot = {
        "schema_id": PACKAGE_MANIFEST_SCHEMA,
        "package_id": package_id,
        "project_id": project_id,
        "title": title,
        "created_at": stamp,
        "project": project,
        "included": include,
        "notes": str(data.get("notes") or ""),
    }
    add_file("snapshot", "manifest/project_snapshot.json", {
        **snapshot,
        "briefs": briefs,
        "milestones": milestones,
        "deliverables": deliverables,
        "reviews": reviews,
        "timeline": timeline if include.get("timeline") else [],
        "context": context_payload if include.get("context") else {},
        "asset_tray": tray_payload if include.get("asset_references") or include.get("handoffs") else {},
        "delivery_dashboard": dashboard,
        "roleplay_links": roleplay_links if include.get("roleplay_links") else [],
    }, True)

    if include.get("status_report"):
        add_file("status_report", "reports/client_status.md", _format_delivery_status_markdown(dashboard))
        add_file("status_report_json", "reports/client_status.json", dashboard, True)
    if include.get("briefs"):
        add_file("brief_index", "briefs/brief_index.json", briefs, True)
        for brief in briefs:
            brief_id = str(brief.get("brief_id") or "")
            try:
                full = get_project_brief(project_id, brief_id).get("brief") or {}
            except Exception:
                full = brief
            safe_id = slugify(brief_id, "brief")
            add_file("brief", f"briefs/{safe_id}.md", full.get("markdown") or _format_brief_markdown((full.get("brief_payload") if isinstance(full, dict) else {}) or {}))
            add_file("brief_json", f"briefs/{safe_id}.json", full, True)
    if include.get("milestones"):
        add_file("milestones", "records/milestones.json", milestones, True)
    if include.get("deliverables"):
        add_file("deliverables", "records/deliverables.json", deliverables, True)
    if include.get("reviews"):
        add_file("reviews", "records/review_items.json", reviews, True)
    if include.get("timeline"):
        add_file("timeline", "records/timeline.json", timeline, True)
    if include.get("context"):
        add_file("context", "records/context.json", context_payload, True)
    if include.get("asset_references") or include.get("handoffs"):
        add_file("asset_references", "records/asset_references.json", tray_payload, True)
    if include.get("roleplay_links"):
        add_file("roleplay_links", "records/roleplay_project_links.json", roleplay_links, True)

    source_refs = []
    if isinstance(tray_payload.get("links"), list):
        source_refs.extend(tray_payload.get("links") or [])
    if isinstance(tray_payload.get("handoffs"), list):
        source_refs.extend(tray_payload.get("handoffs") or [])
    if include.get("local_files"):
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            raw = str(ref.get("path") or ref.get("ref_id") or "")
            src = _safe_local_source_path(raw)
            if not src:
                continue
            dest_rel = f"assets/{slugify(ref.get('surface') or 'surface', 'surface')}/{slugify(src.stem, 'asset')}{src.suffix}"
            dest = _safe_package_file_path(package_dir, dest_rel)
            shutil.copy2(src, dest)
            copied_files.append({"source": str(src), "relative_path": str(dest.relative_to(package_dir)), "title": ref.get("title") or src.name})
            files.append({"kind": "local_file", "relative_path": str(dest.relative_to(package_dir)), "source": str(src)})

    summary = {
        "brief_count": len(briefs),
        "milestone_count": len(milestones),
        "deliverable_count": len(deliverables),
        "review_count": len(reviews),
        "asset_reference_count": len(source_refs),
        "roleplay_link_count": len(roleplay_links),
        "timeline_count": len(timeline),
        "copied_file_count": len(copied_files),
        "package_file_count": len(files),
    }
    manifest = {
        **snapshot,
        "status": "built",
        "summary": summary,
        "files": files,
        "copied_files": copied_files,
        "package_dir": str(package_dir),
        "zip_path": "",
        "manifest_path": "",
        "updated_at": stamp,
    }
    readme = _package_readme(manifest)
    add_file("readme", "README.md", readme)
    manifest["files"] = files
    manifest_rel = _write_package_json(package_dir, "manifest/package_manifest.json", manifest)
    manifest["manifest_path"] = str(package_dir / manifest_rel)

    zip_path = ""
    if bool(data.get("zip", True)):
        zip_file = _package_root(project_id) / f"{package_id}.zip"
        with zipfile.ZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in package_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, arcname=str(Path(package_id) / path.relative_to(package_dir)))
        zip_path = str(zip_file)
        manifest["zip_path"] = zip_path
        _write_package_json(package_dir, "manifest/package_manifest.json", manifest)

    write_json(_package_path(project_id, package_id), manifest)
    timeline_event = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.package.built",
        "surface": "project_workspace",
        "title": title,
        "summary": f"Built project package with {summary['package_file_count']} files and {summary['asset_reference_count']} asset references.",
        "resource_type": "project_package",
        "ref_id": package_id,
        "metadata": {"package_id": package_id, "package_dir": str(package_dir), "zip_path": zip_path, "summary": summary},
    }).get("event")
    manifest["timeline_event"] = timeline_event
    write_json(_package_path(project_id, package_id), manifest)
    return {"ok": True, "schema_id": "neo.project_workspace.package_built.v1", "project_id": project_id, "package_id": package_id, "package": manifest, "package_dir": str(package_dir), "zip_path": zip_path, "manifest_path": manifest["manifest_path"], "timeline_event": timeline_event}

# ---------------------------------------------------------------------------
# Phase 35: Project Delivery Dashboard + Client Status View
# ---------------------------------------------------------------------------

DELIVERY_REPORT_SCHEMA = "neo.project_workspace.delivery_dashboard.v1"
CLIENT_STATUS_SCHEMA = "neo.project_workspace.client_status.v1"


def _date_bucket(date_text: str) -> dict[str, Any]:
    raw = str(date_text or "")[:10]
    if not raw:
        return {"date": "", "days_remaining": None, "bucket": "unscheduled", "is_overdue": False}
    try:
        due_date = datetime.fromisoformat(raw).date()
        today = datetime.now(timezone.utc).date()
        delta = (due_date - today).days
        if delta < 0:
            bucket = "overdue"
        elif delta <= 3:
            bucket = "due_very_soon"
        elif delta <= 7:
            bucket = "due_soon"
        else:
            bucket = "scheduled"
        return {"date": raw, "days_remaining": delta, "bucket": bucket, "is_overdue": delta < 0}
    except Exception:
        return {"date": raw, "days_remaining": None, "bucket": "unknown", "is_overdue": False}


def _latest_final_or_saved_brief(project_id: str) -> dict[str, Any] | None:
    payload = project_briefs_payload(project_id, limit=100)
    briefs = payload.get("briefs") if isinstance(payload, dict) else []
    if not isinstance(briefs, list) or not briefs:
        return None
    final = [b for b in briefs if b.get("is_final")]
    rows = final or briefs
    rows.sort(key=lambda b: str(b.get("created_at") or b.get("updated_at") or ""), reverse=True)
    return rows[0]


def project_delivery_dashboard_payload(project_id: str = "", *, audience: str = "client", limit: int = 120) -> dict[str, Any]:
    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or _default_workspace()
    tracker = project_deliverable_tracker_payload(resolved_project_id, limit=limit)
    smart = project_smart_brief_payload(resolved_project_id, audience=audience or "client", detail="standard", limit=limit)
    activity = project_activity_intelligence_payload(resolved_project_id, limit=limit)
    asset_tray = project_workspace_asset_tray_payload(resolved_project_id, limit=limit)
    milestones = tracker.get("milestones") or []
    deliverables = tracker.get("deliverables") or []
    summary = tracker.get("summary") or {}
    total = len(deliverables)
    approved_or_done = [d for d in deliverables if str(d.get("status") or "") in {"approved", "delivered"} or str(d.get("review_state") or "") == "approved"]
    in_review = [d for d in deliverables if str(d.get("review_state") or "") in {"internal_review", "client_review"} or str(d.get("status") or "") == "ready_for_review"]
    open_items = [d for d in deliverables if str(d.get("status") or "") not in {"approved", "delivered", "archived"}]
    blocked = [d for d in deliverables if str(d.get("status") or "") == "blocked" or str(d.get("review_state") or "") == "revision_requested"]
    revision_needed = [d for d in deliverables if str(d.get("status") or "") == "revision_needed" or str(d.get("review_state") or "") == "revision_requested"]
    due_items = []
    overdue_items = []
    for item in deliverables:
        bucket = _date_bucket(str(item.get("due_date") or ""))
        enriched = {**item, "due_bucket": bucket}
        if bucket.get("bucket") in {"due_very_soon", "due_soon"} and str(item.get("status") or "") not in {"approved", "delivered", "archived"}:
            due_items.append(enriched)
        if bucket.get("is_overdue") and str(item.get("status") or "") not in {"approved", "delivered", "archived"}:
            overdue_items.append(enriched)
    milestone_rows = []
    for milestone in milestones:
        linked = [d for d in deliverables if str(d.get("milestone_id") or "") == str(milestone.get("milestone_id") or "")]
        done = [d for d in linked if str(d.get("status") or "") in {"approved", "delivered"} or str(d.get("review_state") or "") == "approved"]
        progress = round((len(done) / len(linked)) * 100) if linked else (100 if milestone.get("status") in {"completed", "approved"} else 0)
        milestone_rows.append({**milestone, "deliverable_count": len(linked), "completed_deliverable_count": len(done), "progress_percent": progress, "due_bucket": _date_bucket(str(milestone.get("due_date") or ""))})
    progress_percent = round((len(approved_or_done) / total) * 100) if total else 0
    status_label = "No deliverables yet"
    if total:
        if blocked:
            status_label = "Needs attention"
        elif len(approved_or_done) == total:
            status_label = "Ready / delivered"
        elif in_review:
            status_label = "In review"
        else:
            status_label = "In progress"
    warnings = []
    if blocked:
        warnings.append({"level": "high", "label": "Blocked or revision-needed deliverables", "count": len(blocked), "items": blocked[:8]})
    if overdue_items:
        warnings.append({"level": "high", "label": "Overdue deliverables", "count": len(overdue_items), "items": overdue_items[:8]})
    if due_items:
        warnings.append({"level": "medium", "label": "Due soon", "count": len(due_items), "items": due_items[:8]})
    final_brief = _latest_final_or_saved_brief(resolved_project_id)
    linked_assets = (asset_tray.get("links") or [])[:12]
    handoffs = (asset_tray.get("handoffs") or [])[:12]
    client_summary = smart.get("client_ready_summary") or smart.get("progress_summary") or f"{project.get('name') or 'Project'} is currently {status_label.lower()} with {len(approved_or_done)} of {total} deliverables approved or delivered."
    return {
        "ok": True,
        "schema_id": DELIVERY_REPORT_SCHEMA,
        "status": "ready",
        "project_id": project.get("project_id"),
        "active_project_id": active.get("active_project_id"),
        "project": project,
        "audience": audience or "client",
        "delivery_status": {
            "label": status_label,
            "progress_percent": progress_percent,
            "deliverable_count": total,
            "approved_or_delivered_count": len(approved_or_done),
            "open_count": len(open_items),
            "in_review_count": len(in_review),
            "blocked_count": len(blocked),
            "revision_needed_count": len(revision_needed),
            "due_soon_count": len(due_items),
            "overdue_count": len(overdue_items),
        },
        "milestones": milestone_rows,
        "deliverables": deliverables,
        "warnings": warnings,
        "blocked_or_revision_items": blocked[:12],
        "due_soon_items": due_items[:12],
        "overdue_items": overdue_items[:12],
        "final_brief": final_brief,
        "linked_assets": linked_assets,
        "recent_handoffs": handoffs,
        "activity_summary": activity.get("summary") or {},
        "smart_brief_summary": {"title": smart.get("title"), "client_ready_summary": client_summary, "next_suggested_actions": smart.get("next_suggested_actions") or []},
        "tracker_summary": summary,
        "generated_at": now_iso(),
        "policy": "Client Status View is generated from project workspace metadata, milestones, deliverables, briefs, and linked project references. It does not modify source assets.",
    }


def _format_delivery_status_markdown(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    status = payload.get("delivery_status") or {}
    lines = [
        f"# Project Status — {project.get('name') or payload.get('project_id') or 'Project'}",
        "",
        f"**Status:** {status.get('label') or 'Unknown'}",
        f"**Progress:** {status.get('progress_percent', 0)}%",
        f"**Deliverables:** {status.get('approved_or_delivered_count', 0)} approved/delivered of {status.get('deliverable_count', 0)} total",
        f"**Open:** {status.get('open_count', 0)} · **In review:** {status.get('in_review_count', 0)} · **Blocked:** {status.get('blocked_count', 0)}",
        "",
        "## Client-ready summary",
        str((payload.get("smart_brief_summary") or {}).get("client_ready_summary") or "No summary available."),
        "",
    ]
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("## Needs Attention")
        for warning in warnings:
            lines.append(f"- **{warning.get('label')}**: {warning.get('count', 0)}")
        lines.append("")
    milestones = payload.get("milestones") or []
    if milestones:
        lines.append("## Milestones")
        for item in milestones[:12]:
            lines.append(f"- {item.get('title') or item.get('milestone_id')} — {item.get('status')} · {item.get('progress_percent', 0)}% · due {item.get('due_date') or 'not set'}")
        lines.append("")
    deliverables = payload.get("deliverables") or []
    if deliverables:
        lines.append("## Deliverables")
        for item in deliverables[:20]:
            lines.append(f"- {item.get('title') or item.get('deliverable_id')} — {item.get('status')} / {item.get('review_state')} · due {item.get('due_date') or 'not set'}")
        lines.append("")
    final_brief = payload.get("final_brief") or {}
    if final_brief:
        lines.extend(["## Final / latest brief", f"- {final_brief.get('title') or final_brief.get('brief_id')} · v{final_brief.get('version_number', 1)}", ""])
    assets = payload.get("linked_assets") or []
    if assets:
        lines.append("## Linked assets")
        for asset in assets[:12]:
            lines.append(f"- {asset.get('title') or asset.get('link_id')} — {asset.get('resource_type') or 'resource'} · {asset.get('path') or asset.get('ref_id') or ''}")
        lines.append("")
    actions = (payload.get("smart_brief_summary") or {}).get("next_suggested_actions") or []
    if actions:
        lines.append("## Suggested next actions")
        for action in actions[:8]:
            lines.append(f"- {action}")
        lines.append("")
    lines.append(f"_Generated: {payload.get('generated_at') or now_iso()}_")
    return "\n".join(lines).strip() + "\n"


def project_client_status_view_payload(project_id: str = "", *, audience: str = "client", detail: str = "standard", limit: int = 120) -> dict[str, Any]:
    dashboard = project_delivery_dashboard_payload(project_id, audience=audience, limit=limit)
    markdown = _format_delivery_status_markdown(dashboard)
    status = dashboard.get("delivery_status") or {}
    return {
        "ok": True,
        "schema_id": CLIENT_STATUS_SCHEMA,
        "status": "ready",
        "project_id": dashboard.get("project_id"),
        "audience": audience,
        "detail": detail,
        "dashboard": dashboard,
        "client_status": {
            "title": f"Project Status — {(dashboard.get('project') or {}).get('name') or dashboard.get('project_id')}",
            "status_label": status.get("label"),
            "progress_percent": status.get("progress_percent"),
            "summary": (dashboard.get("smart_brief_summary") or {}).get("client_ready_summary"),
            "warnings": dashboard.get("warnings") or [],
            "final_brief": dashboard.get("final_brief"),
            "linked_assets": dashboard.get("linked_assets") or [],
        },
        "markdown": markdown,
        "generated_at": dashboard.get("generated_at"),
    }


def _status_export_dir(project_id: str) -> Path:
    return _project_record_dir(STATUS_EXPORTS_DIR, project_id)


def export_project_status_report(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    export_format = str(data.get("format") or "markdown").lower()
    if export_format in {"md", "markdown"}:
        ext = "md"
    elif export_format in {"txt", "text"}:
        ext = "txt"
    elif export_format == "json":
        ext = "json"
    else:
        raise ValueError("Unsupported project status export format")
    payload_view = project_client_status_view_payload(project_id, audience=str(data.get("audience") or "client"), detail=str(data.get("detail") or "standard"), limit=int(data.get("limit") or 120))
    content = json.dumps(payload_view, indent=2, ensure_ascii=False) if ext == "json" else str(payload_view.get("markdown") or "")
    export_id = slugify(data.get("export_id") or f"status_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}", "status_report")
    path = (_status_export_dir(project_id) / f"{export_id}.{ext}").resolve()
    path.write_text(content, encoding="utf-8")
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.status_report.exported",
        "surface": "project_workspace",
        "title": "Project status report exported",
        "summary": f"Exported client status view as {ext.upper()}.",
        "resource_type": "project_status_report",
        "ref_id": export_id,
        "metadata": {"export_id": export_id, "path": str(path), "format": ext},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.status_report_export.v1", "project_id": project_id, "export_id": export_id, "format": ext, "path": str(path), "content_preview": content[:1200], "timeline_event": timeline}

def export_project_brief(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    export_format = str(data.get("format") or "markdown").lower()
    if export_format in {"md", "markdown"}:
        ext = "md"
    elif export_format in {"txt", "text"}:
        ext = "txt"
    elif export_format == "json":
        ext = "json"
    else:
        raise ValueError("Unsupported brief export format")
    brief_id = str(data.get("brief_id") or "")
    if brief_id:
        brief_record = get_project_brief(project_id, brief_id).get("brief")
    else:
        saved = save_project_brief(data)
        brief_record = saved.get("brief")
        brief_id = str((brief_record or {}).get("brief_id") or "brief")
    export_id = slugify(data.get("export_id") or f"{brief_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}", "brief_export")
    if ext == "json":
        content = json.dumps(brief_record, indent=2, ensure_ascii=False)
    else:
        content = str((brief_record or {}).get("markdown") or _format_brief_markdown((brief_record or {}).get("brief_payload") or {}))
    path = _brief_export_path(project_id, export_id, ext)
    path.write_text(content, encoding="utf-8")
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "project.brief.exported",
        "surface": "project_workspace",
        "title": f"Brief exported: {(brief_record or {}).get('title') or brief_id}",
        "summary": f"Exported project brief as {ext.upper()}.",
        "resource_type": "project_brief_export",
        "ref_id": brief_id,
        "metadata": {"brief_id": brief_id, "export_id": export_id, "path": str(path)},
    }).get("event")
    return {"ok": True, "schema_id": "neo.project_workspace.brief_export.v1", "project_id": project_id, "brief_id": brief_id, "export_id": export_id, "format": ext, "path": str(path), "content_preview": content[:1200], "timeline_event": timeline}

