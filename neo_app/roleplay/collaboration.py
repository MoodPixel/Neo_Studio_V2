from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
import json
from typing import Any

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ensure_roleplay_foundation

COLLAB_ROOT = ROLEPLAY_DATA_ROOT / "collaboration"
SESSIONS_PATH = COLLAB_ROOT / "sessions.json"
LOCKS_PATH = COLLAB_ROOT / "locks.json"
ACTIVITY_PATH = COLLAB_ROOT / "activity.json"
CONFLICTS_PATH = COLLAB_ROOT / "conflicts.json"
SETTINGS_PATH = COLLAB_ROOT / "settings.json"

LOCK_TTL_SECONDS = 15 * 60
ACTIVITY_LIMIT = 250


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    ensure_roleplay_foundation()
    COLLAB_ROOT.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        _write_json(SETTINGS_PATH, {
            "schema_id": "neo.roleplay.collaboration.settings.v1",
            "mode": "local_foundation",
            "lock_ttl_seconds": LOCK_TTL_SECONDS,
            "realtime_transport": "deferred_websocket",
            "cloud_sync_bridge": "deferred_phase18D",
        })


def _read_json(path: Path, default: Any) -> Any:
    _ensure()
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or fallback).strip()
    return text


def _user_from_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    payload = payload or {}
    user_id = _clean_text(payload.get("user_id"), "local_user") or "local_user"
    display_name = _clean_text(payload.get("display_name"), user_id) or user_id
    color = _clean_text(payload.get("color"), "neo") or "neo"
    return {"user_id": user_id, "display_name": display_name, "color": color}


def _sessions() -> list[dict[str, Any]]:
    data = _read_json(SESSIONS_PATH, {"sessions": []})
    return list(data.get("sessions") or [])


def _locks() -> list[dict[str, Any]]:
    data = _read_json(LOCKS_PATH, {"locks": []})
    now = datetime.now(timezone.utc)
    cleaned: list[dict[str, Any]] = []
    for lock in data.get("locks") or []:
        expires = str(lock.get("expires_at") or "")
        try:
            expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except Exception:
            expires_at = now - timedelta(seconds=1)
        if expires_at > now and lock.get("status", "active") == "active":
            cleaned.append(lock)
    if len(cleaned) != len(data.get("locks") or []):
        _write_json(LOCKS_PATH, {"locks": cleaned})
    return cleaned


def _activity() -> list[dict[str, Any]]:
    data = _read_json(ACTIVITY_PATH, {"activity": []})
    return list(data.get("activity") or [])[-ACTIVITY_LIMIT:]


def _conflicts() -> list[dict[str, Any]]:
    data = _read_json(CONFLICTS_PATH, {"conflicts": []})
    return list(data.get("conflicts") or [])


def _save_sessions(sessions: list[dict[str, Any]]) -> None:
    _write_json(SESSIONS_PATH, {"sessions": sessions[-100:]})


def _save_locks(locks: list[dict[str, Any]]) -> None:
    _write_json(LOCKS_PATH, {"locks": locks})


def _save_activity(activity: list[dict[str, Any]]) -> None:
    _write_json(ACTIVITY_PATH, {"activity": activity[-ACTIVITY_LIMIT:]})


def _save_conflicts(conflicts: list[dict[str, Any]]) -> None:
    _write_json(CONFLICTS_PATH, {"conflicts": conflicts[-250:]})


def _record_activity(event_type: str, user: dict[str, str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "activity_id": f"act_{uuid4().hex[:12]}",
        "event_type": event_type,
        "user_id": user.get("user_id") or "local_user",
        "display_name": user.get("display_name") or user.get("user_id") or "local_user",
        "payload": payload or {},
        "created_at": utc_now(),
    }
    activity = _activity()
    activity.append(event)
    _save_activity(activity)
    return event


def collaboration_state_payload() -> dict[str, Any]:
    _ensure()
    sessions = _sessions()
    locks = _locks()
    activity = _activity()
    conflicts = _conflicts()
    active_conflicts = [item for item in conflicts if item.get("status", "open") == "open"]
    return {
        "schema_id": "neo.roleplay.collaboration.state.v1",
        "status": "foundation",
        "mode": "local_multi_user_foundation",
        "ready": True,
        "storage_root": str(COLLAB_ROOT),
        "counts": {
            "sessions": len(sessions),
            "active_sessions": len([s for s in sessions if s.get("status") == "active"]),
            "locks": len(locks),
            "activity": len(activity),
            "conflicts": len(conflicts),
            "open_conflicts": len(active_conflicts),
        },
        "sessions": sessions[-25:],
        "locks": locks,
        "activity": activity[-25:][::-1],
        "conflicts": active_conflicts[-25:][::-1],
        "capabilities": {
            "local_identity": True,
            "heartbeat": True,
            "record_locks": True,
            "activity_log": True,
            "edit_conflict_warnings": True,
            "websocket_realtime": False,
            "cloud_sync": False,
        },
        "deferred_features": [
            "realtime websocket fanout",
            "remote user authentication",
            "cloud sync merge transport",
            "field-level operational transforms",
        ],
    }


def collaboration_heartbeat_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    user = _user_from_payload(data)
    session_id = _clean_text(data.get("session_id"), f"session_{uuid4().hex[:10]}")
    surface_id = _clean_text(data.get("surface_id"), "roleplay")
    tab_id = _clean_text(data.get("tab_id"), "unknown")
    sessions = _sessions()
    next_sessions = [s for s in sessions if s.get("session_id") != session_id]
    record = {
        "session_id": session_id,
        "user_id": user["user_id"],
        "display_name": user["display_name"],
        "color": user["color"],
        "surface_id": surface_id,
        "tab_id": tab_id,
        "status": "active",
        "last_seen_at": utc_now(),
    }
    next_sessions.append(record)
    _save_sessions(next_sessions)
    _record_activity("collaboration.heartbeat", user, {"session_id": session_id, "surface_id": surface_id, "tab_id": tab_id})
    return {"schema_id": "neo.roleplay.collaboration.heartbeat.v1", "session": record, "collaboration": collaboration_state_payload()}


def acquire_lock_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    user = _user_from_payload(data)
    target_type = _clean_text(data.get("target_type"), "record")
    target_id = _clean_text(data.get("target_id"))
    scope_id = _clean_text(data.get("scope_id"))
    if not target_id:
        raise ValueError("target_id is required")
    locks = _locks()
    existing = next((item for item in locks if item.get("target_type") == target_type and item.get("target_id") == target_id), None)
    if existing and existing.get("user_id") != user["user_id"]:
        conflict = {
            "conflict_id": f"collab_conflict_{uuid4().hex[:12]}",
            "status": "open",
            "conflict_type": "lock_conflict",
            "target_type": target_type,
            "target_id": target_id,
            "scope_id": scope_id,
            "requested_by": user,
            "locked_by": {
                "user_id": existing.get("user_id"),
                "display_name": existing.get("display_name"),
            },
            "summary": f"{user['display_name']} tried to edit {target_type}:{target_id}, but it is locked by {existing.get('display_name') or existing.get('user_id')}",
            "created_at": utc_now(),
        }
        conflicts = _conflicts()
        conflicts.append(conflict)
        _save_conflicts(conflicts)
        _record_activity("collaboration.lock.conflict", user, conflict)
        return {"schema_id": "neo.roleplay.collaboration.lock.v1", "acquired": False, "conflict": conflict, "collaboration": collaboration_state_payload()}
    lock = {
        "lock_id": existing.get("lock_id") if existing else f"lock_{uuid4().hex[:12]}",
        "target_type": target_type,
        "target_id": target_id,
        "scope_id": scope_id,
        "user_id": user["user_id"],
        "display_name": user["display_name"],
        "color": user["color"],
        "status": "active",
        "acquired_at": existing.get("acquired_at") if existing else utc_now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=LOCK_TTL_SECONDS)).isoformat(),
    }
    locks = [item for item in locks if item.get("lock_id") != lock["lock_id"] and not (item.get("target_type") == target_type and item.get("target_id") == target_id)]
    locks.append(lock)
    _save_locks(locks)
    _record_activity("collaboration.lock.acquired", user, {"target_type": target_type, "target_id": target_id, "lock_id": lock["lock_id"]})
    return {"schema_id": "neo.roleplay.collaboration.lock.v1", "acquired": True, "lock": lock, "collaboration": collaboration_state_payload()}


def release_lock_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    user = _user_from_payload(data)
    target_type = _clean_text(data.get("target_type"), "")
    target_id = _clean_text(data.get("target_id"), "")
    lock_id = _clean_text(data.get("lock_id"), "")
    locks = _locks()
    released = []
    remaining = []
    for lock in locks:
        match = (lock_id and lock.get("lock_id") == lock_id) or (target_type and target_id and lock.get("target_type") == target_type and lock.get("target_id") == target_id)
        if match and lock.get("user_id") in {user["user_id"], "local_user"}:
            released.append(lock)
        else:
            remaining.append(lock)
    _save_locks(remaining)
    _record_activity("collaboration.lock.released", user, {"released_count": len(released), "target_type": target_type, "target_id": target_id})
    return {"schema_id": "neo.roleplay.collaboration.release.v1", "released_count": len(released), "released": released, "collaboration": collaboration_state_payload()}


def log_activity_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    user = _user_from_payload(data)
    event_type = _clean_text(data.get("event_type"), "collaboration.activity")
    event = _record_activity(event_type, user, data.get("payload") if isinstance(data.get("payload"), dict) else {})
    return {"schema_id": "neo.roleplay.collaboration.activity.v1", "activity": event, "collaboration": collaboration_state_payload()}


def resolve_conflict_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    user = _user_from_payload(data)
    conflict_id = _clean_text(data.get("conflict_id"))
    status = _clean_text(data.get("status"), "resolved")
    note = _clean_text(data.get("resolution_note"))
    if status not in {"resolved", "ignored", "intentional", "open"}:
        raise ValueError("status must be resolved, ignored, intentional, or open")
    conflicts = _conflicts()
    updated = None
    for conflict in conflicts:
        if conflict.get("conflict_id") == conflict_id:
            conflict["status"] = status
            conflict["resolution_note"] = note
            conflict["resolved_by"] = user
            conflict["updated_at"] = utc_now()
            updated = conflict
            break
    if updated is None:
        raise KeyError(conflict_id)
    _save_conflicts(conflicts)
    _record_activity("collaboration.conflict.updated", user, {"conflict_id": conflict_id, "status": status})
    return {"schema_id": "neo.roleplay.collaboration.conflict.v1", "conflict": updated, "collaboration": collaboration_state_payload()}
