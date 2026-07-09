from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.runtime_data import ASSISTANT_BUILTIN_SCOPES, ensure_assistant_scope_seed
from neo_app.assistant.contracts import normalize_surface_id, trim_text
from neo_app.assistant.store import (
    assistant_profile,
    create_project_payload,
    get_project,
    list_projects,
    save_assistant_profile,
    save_project_payload,
)
from neo_app.control_center.assistant_controller import get_assistant_control_center

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"

ASSISTANT_BRAIN_PHASE = "M14"
ASSISTANT_BRAIN_SCHEMA_ID = "neo.assistant.brain_workspace.v1"

BUILTIN_WORKSPACES: list[dict[str, Any]] = [
    {
        "workspace_id": str(scope.get("workspace_id") or scope.get("project_id") or ""),
        "project_id": str(scope.get("project_id") or ""),
        "surface": str(scope.get("surface") or "assistant"),
        "name": str(scope.get("name") or scope.get("project_id") or "Assistant Scope"),
        "type": str(scope.get("type") or "assistant_scope"),
        "description": str(scope.get("description") or ""),
        "memory_lanes": list(scope.get("memory_lanes") or []),
    }
    for scope in ASSISTANT_BUILTIN_SCOPES
]



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _clean(value: Any, limit: int = 500) -> str:
    return trim_text(str(value or "").replace("\r", " ").replace("\n", " ").strip(), limit)


@dataclass(slots=True)
class AssistantWorkspaceRequest:
    workspace_id: str = ""
    project_id: str = ""
    surface: str = ""
    query: str = ""
    retrieval_profile: str = "smart"
    limit: int = 8
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "AssistantWorkspaceRequest":
        payload = payload or {}
        return cls(
            workspace_id=str(payload.get("workspace_id") or payload.get("workspace") or "").strip(),
            project_id=str(payload.get("project_id") or "").strip(),
            surface=normalize_surface_id(payload.get("surface") or payload.get("active_surface") or "", default=""),
            query=str(payload.get("query") or payload.get("message") or payload.get("text") or "").strip(),
            retrieval_profile=str(payload.get("retrieval_profile") or assistant_profile().get("retrieval_profile") or "smart"),
            limit=max(1, min(int(payload.get("limit") or payload.get("memory_limit") or 8), 40)),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )


class AssistantBrainWorkspace:
    """M14 Assistant Brain workspace router.

    This layer turns Assistant from a generic chat into a workspace-aware brain:
    built-in projects are mapped to Neo surfaces, scoped memory is queried by
    workspace, and the Assistant Control Center receives a clear workspace brief.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.control = get_assistant_control_center()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def status(self) -> dict[str, Any]:
        ensured = self.ensure_builtin_workspaces()
        return {
            "ok": True,
            "schema_id": ASSISTANT_BRAIN_SCHEMA_ID,
            "phase": ASSISTANT_BRAIN_PHASE,
            "status": "ready",
            "label": "Assistant Brain Workspace Integration",
            "workspace_count": len(BUILTIN_WORKSPACES),
            "ensured_projects": ensured.get("created_or_updated", 0),
            "policy": {
                "assistant_is_central_brain": True,
                "workspace_memory_is_sandboxed": True,
                "surface_projects_are_builtin": True,
                "cross_workspace_memory_requires_explicit_scope": True,
                "control_center_required": True,
            },
            "endpoints": {
                "status": "/api/assistant/brain/status",
                "workspaces": "/api/assistant/brain/workspaces",
                "dashboard": "/api/assistant/brain/dashboard",
                "context": "/api/assistant/brain/context",
                "activate": "/api/assistant/brain/activate",
            },
        }

    def ensure_builtin_workspaces(self) -> dict[str, Any]:
        scope_seed = ensure_assistant_scope_seed(ROOT_DIR)
        existing = {p.get("project_id"): p for p in list_projects()}
        created_or_updated = 0
        workspaces = []
        for workspace in BUILTIN_WORKSPACES:
            project_id = workspace["project_id"]
            payload = {
                "project_id": project_id,
                "name": workspace["name"],
                "type": workspace["type"],
                "description": workspace["description"],
                "notes": self._workspace_notes(workspace),
                "status": "active",
            }
            if project_id in existing:
                current = get_project(project_id) or existing[project_id]
                merged = {**current, **payload, "created_at": current.get("created_at") or _now()}
                save_project_payload(merged)
            else:
                if project_id == "general":
                    save_project_payload(payload)
                else:
                    create_project_payload(payload)
            created_or_updated += 1
            workspaces.append({**workspace, "project": get_project(project_id) or payload})
        return {
            "ok": True,
            "status": "ensured",
            "created_or_updated": created_or_updated,
            "scope_seed": scope_seed,
            "workspaces": workspaces,
        }

    def workspaces(self) -> dict[str, Any]:
        ensured = self.ensure_builtin_workspaces()
        enriched = []
        for workspace in ensured.get("workspaces", []):
            enriched.append({**workspace, "memory_stats": self._memory_stats(workspace.get("surface"), workspace.get("project_id")), "recent_traces": self._recent_traces(workspace.get("surface"), workspace.get("project_id"), limit=3)})
        return {"ok": True, "status": "ready", "phase": ASSISTANT_BRAIN_PHASE, "workspaces": enriched, "count": len(enriched)}

    def dashboard(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = AssistantWorkspaceRequest.from_payload(payload)
        workspace = self._resolve_workspace(request)
        dashboard = {
            "ok": True,
            "status": "ready",
            "phase": ASSISTANT_BRAIN_PHASE,
            "active_workspace": workspace,
            "workspaces": self.workspaces().get("workspaces", []),
            "memory_preview": self._memory_preview(workspace["surface"], workspace["project_id"], limit=request.limit),
            "recent_traces": self._recent_traces(workspace["surface"], workspace["project_id"], limit=8),
            "workspace_brief": self._workspace_brief(workspace),
        }
        return dashboard

    def context(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        request = AssistantWorkspaceRequest.from_payload(payload)
        workspace = self._resolve_workspace(request)
        query = request.query or f"Workspace status for {workspace['name']}"
        cc_payload = {
            "message": query,
            "project_id": workspace["project_id"],
            "surface": workspace["surface"],
            "active_surface": workspace["surface"],
            "retrieval_profile": request.retrieval_profile,
            "memory_limit": request.limit,
            "metadata": {
                **(request.metadata or {}),
                "assistant_brain_phase": ASSISTANT_BRAIN_PHASE,
                "workspace_id": workspace["workspace_id"],
                "workspace_name": workspace["name"],
                "workspace_memory_lanes": workspace.get("memory_lanes", []),
            },
        }
        control_context = self.control.context(cc_payload, persist=persist)
        prompt_block = str(control_context.get("prompt_block") or "")
        workspace_block = self._workspace_prompt_block(workspace)
        merged_prompt = f"{workspace_block}\n\n{prompt_block}".strip() if prompt_block else workspace_block
        return {
            "ok": True,
            "status": "ready",
            "phase": ASSISTANT_BRAIN_PHASE,
            "workspace": workspace,
            "trace_id": control_context.get("trace_id"),
            "prompt_block": merged_prompt,
            "messages": [{"role": "system", "content": merged_prompt}] if merged_prompt else [],
            "control_center": control_context,
            "diagnostics": {
                "workspace_id": workspace["workspace_id"],
                "project_id": workspace["project_id"],
                "surface": workspace["surface"],
                "memory_lanes": workspace.get("memory_lanes", []),
                "control_trace_id": control_context.get("trace_id") or "",
                "policy": "Assistant Brain routes requests through the active workspace sandbox before backend generation.",
            },
        }

    def activate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = AssistantWorkspaceRequest.from_payload(payload)
        workspace = self._resolve_workspace(request)
        save_assistant_profile({"default_project_id": workspace["project_id"]})
        return {"ok": True, "status": "activated", "phase": ASSISTANT_BRAIN_PHASE, "workspace": workspace, "profile": assistant_profile()}

    def resolve_chat_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        request = AssistantWorkspaceRequest.from_payload(payload)
        workspace = self._resolve_workspace(request)
        # Do not override explicit project_id unless it is blank/general and a workspace/surface was supplied.
        if not str(payload.get("project_id") or "").strip() or str(payload.get("project_id") or "").strip() == "general" and request.surface and request.surface != "assistant":
            payload["project_id"] = workspace["project_id"]
        payload.setdefault("surface", workspace["surface"])
        payload.setdefault("active_surface", workspace["surface"])
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {**metadata, "assistant_brain_workspace": workspace}
        return payload

    def _resolve_workspace(self, request: AssistantWorkspaceRequest) -> dict[str, Any]:
        self.ensure_builtin_workspaces()
        by_workspace = {w["workspace_id"]: w for w in BUILTIN_WORKSPACES}
        by_project = {w["project_id"]: w for w in BUILTIN_WORKSPACES}
        by_surface = {w["surface"]: w for w in BUILTIN_WORKSPACES if w["surface"] not in {"assistant", "admin"}}
        if request.workspace_id and request.workspace_id in by_workspace:
            workspace = by_workspace[request.workspace_id]
        elif request.project_id and request.project_id in by_project:
            workspace = by_project[request.project_id]
        elif request.surface and request.surface in by_surface:
            workspace = by_surface[request.surface]
        elif request.surface == "admin":
            workspace = by_project["neo_development_workspace"]
        elif request.query and any(token in request.query.lower() for token in ("client", "fiverr", "brief", "price", "proposal")):
            workspace = by_project["client_work_workspace"]
        elif request.query and any(token in request.query.lower() for token in ("neo", "phase", "repo", "implementation", "bug", "fix")):
            workspace = by_project["neo_development_workspace"]
        else:
            default_project_id = str(assistant_profile().get("default_project_id") or "general")
            workspace = by_project.get(default_project_id, by_project["general"])
        return {**workspace, "project": get_project(workspace["project_id"]) or {"project_id": workspace["project_id"], "name": workspace["name"]}}

    def _workspace_notes(self, workspace: dict[str, Any]) -> str:
        return "\n".join([
            f"Neo Assistant Brain built-in workspace: {workspace['name']}",
            f"Surface sandbox: {workspace['surface']}",
            f"Memory lanes: {', '.join(workspace.get('memory_lanes', []))}",
            "Assistant should retrieve only scoped memory for this workspace unless the user explicitly asks for cross-workspace context.",
        ])

    def _memory_stats(self, surface: str, project_id: str) -> dict[str, Any]:
        stats = {"events": 0, "fragments": 0, "summaries": 0, "embeddings": 0, "facts": 0}
        try:
            with self._connect() as conn:
                for table, key in (("neo_memory_events", "events"), ("neo_memory_fragments", "fragments"), ("neo_memory_summaries", "summaries"), ("neo_memory_embeddings", "embeddings"), ("neo_memory_facts", "facts")):
                    try:
                        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE (surface = ? OR ? = '') AND (project_id = ? OR project_id IS NULL OR project_id = '')", (surface or "", surface or "", project_id or "")).fetchone()
                        stats[key] = int(row[0]) if row else 0
                    except Exception:
                        stats[key] = 0
        except Exception:
            pass
        return stats

    def _memory_preview(self, surface: str, project_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with self._connect() as conn:
                data = conn.execute(
                    """
                    SELECT fragment_id, surface, project_id, scope_type, scope_id, memory_type, title, content, importance, created_at
                    FROM neo_memory_fragments
                    WHERE (surface = ? OR ? = '') AND (project_id = ? OR project_id IS NULL OR project_id = '')
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (surface or "", surface or "", project_id or "", max(1, min(limit, 40))),
                ).fetchall()
                for row in data:
                    item = dict(row)
                    item["content_preview"] = _clean(item.get("content"), 420)
                    item.pop("content", None)
                    rows.append(item)
        except Exception:
            rows = []
        return rows

    def _recent_traces(self, surface: str, project_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT trace_id, controller, surface, project_id, intent, status, created_at, selected_context_json, metadata_json
                    FROM neo_control_center_traces
                    WHERE controller = 'assistant' AND (surface = ? OR ? = '') AND (project_id = ? OR project_id = '' OR project_id IS NULL)
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (surface or "", surface or "", project_id or "", max(1, min(limit, 50))),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                selected = _safe_json(item.pop("selected_context_json", "{}"), {})
                item["context_count"] = selected.get("item_count") or len(selected.get("items") or []) if isinstance(selected, dict) else 0
                item["metadata"] = _safe_json(item.pop("metadata_json", "{}"), {})
                result.append(item)
            return result
        except Exception:
            return []

    def _workspace_brief(self, workspace: dict[str, Any]) -> dict[str, Any]:
        stats = self._memory_stats(workspace.get("surface", ""), workspace.get("project_id", ""))
        return {
            "title": workspace.get("name"),
            "surface": workspace.get("surface"),
            "project_id": workspace.get("project_id"),
            "memory_lanes": workspace.get("memory_lanes", []),
            "memory_stats": stats,
            "instructions": [
                "Use this workspace as the Assistant's scoped memory sandbox.",
                "Prefer memories from the active surface/project before broader advice.",
                "Do not mix unrelated projects or Roleplay universes unless explicitly asked.",
                "Use Control Center traces and observability when the answer depends on system behavior.",
            ],
        }

    def _workspace_prompt_block(self, workspace: dict[str, Any]) -> str:
        stats = self._memory_stats(workspace.get("surface", ""), workspace.get("project_id", ""))
        return "\n".join([
            "# Neo Assistant Brain Workspace",
            f"Phase: {ASSISTANT_BRAIN_PHASE}",
            f"Workspace: {workspace.get('name')} ({workspace.get('workspace_id')})",
            f"Surface sandbox: {workspace.get('surface')}",
            f"Project ID: {workspace.get('project_id')}",
            f"Memory lanes: {', '.join(workspace.get('memory_lanes', []))}",
            f"Memory stats: events {stats.get('events', 0)}, fragments {stats.get('fragments', 0)}, summaries {stats.get('summaries', 0)}, embeddings {stats.get('embeddings', 0)}",
            "Rule: use this workspace as the main Assistant memory sandbox; do not blend unrelated workspace memory unless the user asks.",
        ]).strip()


_ASSISTANT_BRAIN: AssistantBrainWorkspace | None = None


def get_assistant_brain_workspace() -> AssistantBrainWorkspace:
    global _ASSISTANT_BRAIN
    if _ASSISTANT_BRAIN is None:
        _ASSISTANT_BRAIN = AssistantBrainWorkspace(DEFAULT_DB_PATH)
    return _ASSISTANT_BRAIN


def assistant_brain_status_payload() -> dict[str, Any]:
    return get_assistant_brain_workspace().status()


def assistant_brain_workspaces_payload() -> dict[str, Any]:
    return get_assistant_brain_workspace().workspaces()


def assistant_brain_dashboard_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_assistant_brain_workspace().dashboard(payload or {})


def assistant_brain_context_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_assistant_brain_workspace().context(payload or {}, persist=True)


def assistant_brain_activate_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_assistant_brain_workspace().activate(payload or {})


def resolve_assistant_brain_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return get_assistant_brain_workspace().resolve_chat_payload(payload or {})
