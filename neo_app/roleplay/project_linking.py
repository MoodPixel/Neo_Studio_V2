from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.forge import list_forge_records
from neo_app.roleplay.scene import scene_state_payload
from neo_app.roleplay.stories import list_storylines, list_story_sessions, list_story_checkpoints, list_story_branches, read_story_checkpoint, read_story_session
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.project_workspace import (
    active_project_payload,
    add_project_timeline_event,
    create_project_surface_action,
    save_project_brief,
)

ROLEPLAY_PROJECT_LINK_SCHEMA: Final[str] = "neo.roleplay.project_link.v1"
ROLEPLAY_PROJECT_LINKS_SCHEMA: Final[str] = "neo.roleplay.project_links.v1"
ROLEPLAY_PROJECT_LINKS_DIR: Final[Path] = ROLEPLAY_DATA_ROOT / "projects" / "links"

LINKABLE_SOURCE_TYPES: Final[tuple[str, ...]] = (
    "forge_record",
    "scene_setup",
    "storyline",
    "story_session",
    "story_checkpoint",
    "story_branch",
)

SUPPORTED_PROJECT_ACTIONS: Final[tuple[str, ...]] = (
    "add_context",
    "link_resource",
    "send_to_project",
    "create_milestone",
    "create_deliverable",
    "create_review_item",
    "create_brief",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, fallback: str = "roleplay_link") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "_", text).strip("._-")
    return text or fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _link_dir(project_id: str) -> Path:
    ensure_roleplay_foundation(write_manifest=False)
    path = ROLEPLAY_PROJECT_LINKS_DIR / _slug(project_id, "general")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _link_path(project_id: str, link_id: str) -> Path:
    path = (_link_dir(project_id) / f"{_slug(link_id, 'roleplay_link')}.json").resolve()
    root = _link_dir(project_id).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid Roleplay project link path")
    return path


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return model_to_dict(value)
    except Exception:
        return {}


def _compact_forge_record(record: Any) -> dict[str, Any]:
    item = _as_dict(record)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    title = str(item.get("title") or payload.get("label") or payload.get("display_label") or item.get("record_id") or "Roleplay record")
    summary = str(item.get("body") or payload.get("summary") or "")
    return {
        "source_type": "forge_record",
        "source_id": str(item.get("record_id") or payload.get("id") or ""),
        "kind": str(item.get("kind") or payload.get("kind") or "record"),
        "title": title,
        "summary": summary,
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else payload.get("tags", []),
        "storage_path": str(item.get("storage_path") or ""),
        "payload": item,
    }


def _current_scene_snapshot() -> dict[str, Any]:
    scene = scene_state_payload()
    setup = scene.get("setup") if isinstance(scene.get("setup"), dict) else {}
    transcript = scene.get("transcript") if isinstance(scene.get("transcript"), dict) else {}
    title = str(setup.get("title") or setup.get("scene_title") or setup.get("scene_id") or scene.get("scene_id") or "Current Roleplay Scene")
    summary = str(setup.get("premise") or setup.get("summary") or transcript.get("summary") or scene.get("last_response") or "")
    return {
        "source_type": "scene_setup",
        "source_id": str(setup.get("scene_id") or scene.get("scene_id") or "default"),
        "kind": "scene",
        "title": title,
        "summary": summary,
        "tags": ["roleplay", "scene", "canon"],
        "storage_path": str(setup.get("storage_path") or ""),
        "payload": scene,
    }


def _storyline_snapshot(source_id: str = "") -> dict[str, Any]:
    rows = [_as_dict(row) for row in list_storylines()]
    match = next((row for row in rows if str(row.get("storyline_id") or "") == str(source_id)), rows[0] if rows else {})
    return {
        "source_type": "storyline",
        "source_id": str(match.get("storyline_id") or source_id or ""),
        "kind": "storyline",
        "title": str(match.get("title") or match.get("storyline_id") or "Roleplay storyline"),
        "summary": str(match.get("summary") or match.get("premise") or ""),
        "tags": match.get("tags") if isinstance(match.get("tags"), list) else ["roleplay", "storyline"],
        "storage_path": str(match.get("storage_path") or ""),
        "payload": match,
    }


def _story_session_snapshot(source_id: str = "") -> dict[str, Any]:
    match = read_story_session(source_id) if source_id else None
    if not match:
        rows = list_story_sessions()
        match = rows[0] if rows else {}
    return {
        "source_type": "story_session",
        "source_id": str(match.get("session_id") or source_id or ""),
        "kind": "story_session",
        "title": str(match.get("title") or match.get("session_id") or "Roleplay story session"),
        "summary": str(match.get("summary") or match.get("scene_premise") or ""),
        "tags": ["roleplay", "story", "session"],
        "storage_path": str(match.get("storage_path") or ""),
        "payload": match,
    }


def _story_checkpoint_snapshot(source_id: str = "") -> dict[str, Any]:
    match = read_story_checkpoint(source_id) if source_id else None
    if not match:
        rows = list_story_checkpoints()
        match = rows[0] if rows else {}
    return {
        "source_type": "story_checkpoint",
        "source_id": str(match.get("checkpoint_id") or source_id or ""),
        "kind": "story_checkpoint",
        "title": str(match.get("title") or match.get("checkpoint_id") or "Roleplay checkpoint"),
        "summary": str(match.get("summary") or match.get("snapshot_summary") or ""),
        "tags": ["roleplay", "story", "checkpoint"],
        "storage_path": str(match.get("storage_path") or ""),
        "payload": match,
    }


def _story_branch_snapshot(source_id: str = "") -> dict[str, Any]:
    rows = list_story_branches()
    match = next((row for row in rows if str(row.get("branch_id") or "") == str(source_id)), rows[0] if rows else {})
    return {
        "source_type": "story_branch",
        "source_id": str(match.get("branch_id") or source_id or ""),
        "kind": "story_branch",
        "title": str(match.get("title") or match.get("branch_id") or "Roleplay story branch"),
        "summary": str(match.get("summary") or match.get("branch_notes") or ""),
        "tags": ["roleplay", "story", "branch"],
        "storage_path": str(match.get("storage_path") or ""),
        "payload": match,
    }


def resolve_roleplay_project_source(source_type: str = "", source_id: str = "", kind: str = "") -> dict[str, Any]:
    clean_type = _slug(source_type or "forge_record", "forge_record")
    clean_id = str(source_id or "").strip()
    if clean_type == "forge_record":
        records = list_forge_records(kind or None)
        snapshot = None
        for record in records:
            item = _compact_forge_record(record)
            if clean_id and item.get("source_id") == clean_id:
                snapshot = item
                break
        if snapshot is None and records:
            snapshot = _compact_forge_record(records[0])
        if snapshot is not None:
            return snapshot
    if clean_type == "scene_setup":
        return _current_scene_snapshot()
    if clean_type == "storyline":
        return _storyline_snapshot(clean_id)
    if clean_type == "story_session":
        return _story_session_snapshot(clean_id)
    if clean_type == "story_checkpoint":
        return _story_checkpoint_snapshot(clean_id)
    if clean_type == "story_branch":
        return _story_branch_snapshot(clean_id)
    return {
        "source_type": clean_type,
        "source_id": clean_id,
        "kind": kind or "roleplay_record",
        "title": clean_id or "Roleplay record",
        "summary": "",
        "tags": ["roleplay"],
        "storage_path": "",
        "payload": {},
    }


def _linkable_records(limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in list_forge_records(None)[: max(1, int(limit or 40))]:
        rows.append(_compact_forge_record(record))
    try:
        rows.insert(0, _current_scene_snapshot())
    except Exception:
        pass
    for storyline in list_storylines()[:10]:
        item = _as_dict(storyline)
        rows.append({
            "source_type": "storyline",
            "source_id": str(item.get("storyline_id") or ""),
            "kind": "storyline",
            "title": str(item.get("title") or item.get("storyline_id") or "Storyline"),
            "summary": str(item.get("summary") or item.get("premise") or ""),
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else ["roleplay", "storyline"],
            "storage_path": str(item.get("storage_path") or ""),
            "payload": item,
        })
    for checkpoint in list_story_checkpoints()[:10]:
        rows.append({
            "source_type": "story_checkpoint",
            "source_id": str(checkpoint.get("checkpoint_id") or ""),
            "kind": "story_checkpoint",
            "title": str(checkpoint.get("title") or checkpoint.get("checkpoint_id") or "Checkpoint"),
            "summary": str(checkpoint.get("summary") or checkpoint.get("snapshot_summary") or ""),
            "tags": ["roleplay", "story", "checkpoint"],
            "storage_path": str(checkpoint.get("storage_path") or ""),
            "payload": checkpoint,
        })
    return rows[: max(1, int(limit or 40))]


def list_roleplay_project_links(project_id: str = "", *, limit: int = 80, source_type: str = "") -> list[dict[str, Any]]:
    active = active_project_payload()
    resolved = str(project_id or active.get("active_project_id") or "general")
    root = _link_dir(resolved)
    rows: list[dict[str, Any]] = []
    wanted = _slug(source_type, "") if source_type else ""
    for path in root.glob("*.json"):
        record = _read_json(path, {})
        if not isinstance(record, dict) or not record.get("link_id"):
            continue
        if wanted and _slug(record.get("source_type"), "") != wanted:
            continue
        rows.append(record)
    rows.sort(key=lambda row: str(row.get("created_at") or row.get("updated_at") or ""), reverse=True)
    return rows[: max(1, int(limit or 80))]


def roleplay_project_links_payload(project_id: str = "", *, limit: int = 80, source_type: str = "") -> dict[str, Any]:
    active = active_project_payload()
    resolved = str(project_id or active.get("active_project_id") or "general")
    links = list_roleplay_project_links(resolved, limit=limit, source_type=source_type)
    by_type: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for link in links:
        key = str(link.get("source_type") or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
        action = str(link.get("project_action_type") or "linked")
        by_action[action] = by_action.get(action, 0) + 1
    linkable = _linkable_records(limit=limit)
    return {
        "ok": True,
        "schema_id": ROLEPLAY_PROJECT_LINKS_SCHEMA,
        "status": "ready",
        "project_id": resolved,
        "active_project_id": active.get("active_project_id"),
        "summary": {
            "link_count": len(links),
            "linkable_count": len(linkable),
            "by_source_type": by_type,
            "by_project_action": by_action,
        },
        "links": links,
        "linkable_records": linkable,
        "supported_source_types": list(LINKABLE_SOURCE_TYPES),
        "supported_project_actions": list(SUPPORTED_PROJECT_ACTIONS),
        "policy": "Roleplay Project Linking stores local references and project records only. It does not rewrite canon, delete story data, or publish packages.",
    }


def _roleplay_markdown(source: dict[str, Any], notes: str = "") -> str:
    lines = [
        f"# {source.get('title') or 'Roleplay Project Link'}",
        "",
        f"**Source type:** {source.get('source_type') or 'roleplay'}",
        f"**Source ID:** {source.get('source_id') or ''}",
        f"**Kind:** {source.get('kind') or ''}",
        "",
    ]
    summary = str(source.get("summary") or "").strip()
    if summary:
        lines += ["## Summary", summary, ""]
    if notes:
        lines += ["## Project notes", notes, ""]
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    if payload:
        lines += ["## Source payload", "```json", json.dumps(payload, indent=2, ensure_ascii=False)[:12000], "```", ""]
    return "\n".join(lines).strip() + "\n"


def create_roleplay_project_link(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    active = active_project_payload()
    project_id = str(data.get("project_id") or active.get("active_project_id") or "general")
    source_type = _slug(data.get("source_type") or data.get("resource_type") or "forge_record", "forge_record")
    source_id = str(data.get("source_id") or data.get("record_id") or data.get("scene_id") or "").strip()
    kind = str(data.get("kind") or data.get("record_kind") or "").strip()
    source = resolve_roleplay_project_source(source_type, source_id, kind)
    title = str(data.get("title") or source.get("title") or "Roleplay project link")
    notes = str(data.get("notes") or data.get("summary") or source.get("summary") or "")
    action_type = _slug(data.get("project_action_type") or data.get("action_type") or "add_context", "add_context")
    if action_type not in SUPPORTED_PROJECT_ACTIONS:
        action_type = "add_context"
    stamp = _now()
    link_id = _slug(data.get("link_id") or f"rp_{source_type}_{source.get('source_id') or uuid4().hex[:8]}_{uuid4().hex[:6]}", "roleplay_link")
    project_action: dict[str, Any] = {}
    brief: dict[str, Any] = {}
    project_payload = {
        "project_id": project_id,
        "action_type": "add_context" if action_type == "create_brief" else action_type,
        "source_surface": "roleplay",
        "resource_type": f"roleplay_{source_type}",
        "title": title,
        "content": notes or str(source.get("summary") or title),
        "ref_id": str(source.get("source_id") or source_id or link_id),
        "path": str(source.get("storage_path") or ""),
        "tags": ["roleplay", source_type, str(source.get("kind") or kind or "record")],
        "status": str(data.get("status") or ("completed" if action_type == "create_milestone" else "planned")),
        "review_state": str(data.get("review_state") or "not_started"),
        "metadata": {
            "roleplay_project_link_id": link_id,
            "roleplay_source_type": source_type,
            "roleplay_source_id": source.get("source_id") or source_id,
            "roleplay_kind": source.get("kind") or kind,
        },
    }
    project_action = create_project_surface_action(project_payload)
    if action_type == "create_brief" or bool(data.get("create_brief")):
        brief_payload = {
            "schema_id": "neo.roleplay.project_link.brief_payload.v1",
            "project_id": project_id,
            "brief": {
                "title": title,
                "client_ready_summary": notes or str(source.get("summary") or title),
                "completed_work": [f"Linked {source_type.replace('_', ' ')} from Roleplay."],
                "open_decisions": [],
                "creative_direction": [str(source.get("kind") or kind or "roleplay")],
                "next_actions": ["Review linked Roleplay context before delivery/package build."],
                "linked_assets": [{"title": title, "surface": "roleplay", "resource_type": f"roleplay_{source_type}", "ref_id": source.get("source_id") or source_id}],
                "roleplay_milestones": [{"title": title, "summary": notes or source.get("summary") or "", "source_type": source_type, "source_id": source.get("source_id") or source_id}],
            },
            "project": {"project_id": project_id},
        }
        brief = save_project_brief({
            "project_id": project_id,
            "title": title,
            "audience": data.get("audience") or "internal",
            "detail": data.get("detail") or "roleplay_link",
            "markdown": _roleplay_markdown(source, notes),
            "brief_payload": brief_payload,
            "metadata": {"roleplay_project_link_id": link_id, "roleplay_source_type": source_type, "roleplay_source_id": source.get("source_id") or source_id},
        })
    timeline = add_project_timeline_event({
        "project_id": project_id,
        "event_type": "roleplay.project.linked",
        "surface": "roleplay",
        "title": title,
        "summary": f"Linked Roleplay {source_type.replace('_', ' ')} to project: {notes[:300] or title}",
        "resource_type": f"roleplay_{source_type}",
        "ref_id": str(source.get("source_id") or source_id or link_id),
        "metadata": {"roleplay_project_link_id": link_id, "project_action_type": action_type},
    }).get("event")
    record = {
        "schema_id": ROLEPLAY_PROJECT_LINK_SCHEMA,
        "link_id": link_id,
        "project_id": project_id,
        "source_surface": "roleplay",
        "source_type": source_type,
        "source_id": str(source.get("source_id") or source_id or ""),
        "kind": str(source.get("kind") or kind or "roleplay_record"),
        "title": title,
        "summary": notes,
        "tags": source.get("tags") if isinstance(source.get("tags"), list) else ["roleplay", source_type],
        "storage_path": str(source.get("storage_path") or ""),
        "project_action_type": action_type,
        "project_action_ids": (project_action.get("action") or {}).get("created_ids") or {},
        "project_action_id": (project_action.get("action") or {}).get("action_id") or "",
        "brief_id": ((brief.get("brief") or {}).get("brief_id") if isinstance(brief, dict) else "") or "",
        "timeline_event_id": (timeline or {}).get("event_id", ""),
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": {**(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}), "source_payload_preview": source.get("payload")},
    }
    _write_json(_link_path(project_id, link_id), record)
    return {
        "ok": True,
        "schema_id": ROLEPLAY_PROJECT_LINK_SCHEMA,
        "status": "linked",
        "project_id": project_id,
        "link": record,
        "source": source,
        "project_action": project_action,
        "brief": brief,
        "timeline_event": timeline,
        "links": roleplay_project_links_payload(project_id, limit=80),
    }
