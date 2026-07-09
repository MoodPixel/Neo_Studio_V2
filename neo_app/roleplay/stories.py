from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.schema import RoleplayStoriesState, RoleplayStorylineRecord
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_store import _connect, roleplay_sqlite_state_payload, upsert_story_checkpoint_memory, upsert_story_session_memory, upsert_storyline_memory
from neo_app.roleplay.scene import load_scene_setup, load_scene_transcript, save_scene_setup_payload, _scene_transcript_path, _write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:72] or "storyline"


def _storylines_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "storylines"


def _story_drafts_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_drafts"


def _story_sessions_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_sessions"


def _story_checkpoints_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_checkpoints"


def _story_snapshots_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_snapshots"


def _story_branches_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_branches"


def _canon_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "canon_records"


def ensure_stories_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    for directory in [
        _storylines_dir(),
        _story_drafts_dir(),
        _story_sessions_dir(),
        _story_checkpoints_dir(),
        _story_snapshots_dir(),
        _story_branches_dir(),
        _canon_dir(),
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def _read_storyline(path: Path) -> RoleplayStorylineRecord | None:
    try:
        return RoleplayStorylineRecord(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_storylines() -> list[RoleplayStorylineRecord]:
    ensure_stories_storage()
    records = [_read_storyline(path) for path in sorted(_storylines_dir().glob("*.json"))]
    return sorted([record for record in records if record], key=lambda item: item.updated_at or item.created_at, reverse=True)


def create_storyline(payload: dict[str, Any]) -> RoleplayStorylineRecord:
    ensure_stories_storage()
    title = str(payload.get("title") or payload.get("storyline_title") or "Untitled Storyline").strip() or "Untitled Storyline"
    now = _now()
    storyline_id = str(payload.get("storyline_id") or f"{_slug(title)}-{uuid.uuid4().hex[:8]}")
    path = _storylines_dir() / f"{storyline_id}.json"
    existing = _read_storyline(path)
    record = RoleplayStorylineRecord(
        storyline_id=storyline_id,
        title=title,
        premise=str(payload.get("premise") or payload.get("description") or ""),
        arc=str(payload.get("arc") or ""),
        beats=str(payload.get("beats") or ""),
        status=str(payload.get("status") or "foundation"),
        created_at=existing.created_at if existing else now,
        updated_at=now,
        storage_path=_relative_to_root(path),
    )
    path.write_text(json.dumps(model_to_dict(record), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        data = model_to_dict(record)
        data["memory_link"] = upsert_storyline_memory(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        allowed = getattr(RoleplayStorylineRecord, "model_fields", None)
        if allowed is None:
            allowed = getattr(RoleplayStorylineRecord, "__fields__", {})
        return RoleplayStorylineRecord(**{k: v for k, v in data.items() if k in allowed})
    except Exception:
        return record




def _read_json_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("record_id", path.stem)
            data.setdefault("storage_path", _relative_to_root(path))
            return data
    except Exception:
        return None
    return None


def list_story_sessions() -> list[dict[str, Any]]:
    ensure_stories_storage()
    records = [_read_json_record(path) for path in sorted(_story_sessions_dir().glob("*.json"))]
    return sorted([record for record in records if record], key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def list_story_checkpoints() -> list[dict[str, Any]]:
    ensure_stories_storage()
    records = [_read_json_record(path) for path in sorted(_story_checkpoints_dir().glob("*.json"))]
    return sorted([record for record in records if record], key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def create_story_session(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_stories_storage()
    now = _now()
    storyline_id = str(payload.get("storyline_id") or payload.get("active_storyline_id") or "unassigned").strip() or "unassigned"
    summary = str(payload.get("summary") or payload.get("session_summary") or "").strip()
    session_id = str(payload.get("session_id") or f"story_session_{uuid.uuid4().hex[:10]}")
    path = _story_sessions_dir() / f"{session_id}.json"
    existing = _read_json_record(path) or {}
    record = {
        "schema_id": "neo.roleplay.stories.session.v1",
        "session_id": session_id,
        "storyline_id": storyline_id,
        "title": str(payload.get("title") or summary[:80] or "Untitled session"),
        "summary": summary,
        "status": str(payload.get("status") or "draft"),
        "mode_lock": str(payload.get("mode_lock") or "cinematic_authoring"),
        "interaction_mode": str(payload.get("interaction_mode") or "roleplay"),
        "seed_from_checkpoint": bool(payload.get("seed_from_checkpoint", True)),
        "checkpoint_count": int(existing.get("checkpoint_count") or 0),
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "storage_path": _relative_to_root(path),
    }
    try:
        record["memory_link"] = upsert_story_session_memory(record)
    except Exception as exc:
        record["memory_link"] = {"status": "error", "error": str(exc)}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def create_story_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_stories_storage()
    now = _now()
    scene_id = str(payload.get("scene_id") or "default")
    storyline_id = str(payload.get("storyline_id") or payload.get("active_storyline_id") or "unassigned").strip() or "unassigned"
    session_id = str(payload.get("session_id") or payload.get("active_session_id") or "unassigned").strip() or "unassigned"
    checkpoint_id = str(payload.get("checkpoint_id") or f"story_checkpoint_{uuid.uuid4().hex[:10]}")
    title = str(payload.get("title") or payload.get("checkpoint_title") or "Scene checkpoint")
    summary = str(payload.get("summary") or payload.get("checkpoint_summary") or payload.get("scene_summary") or "")
    scene_setup = payload.get("scene_setup") if isinstance(payload.get("scene_setup"), dict) else None
    transcript = payload.get("transcript") if isinstance(payload.get("transcript"), dict) else None
    if scene_setup is None:
        try:
            scene_setup = load_scene_setup(scene_id)
        except Exception:
            scene_setup = {}
    if transcript is None:
        try:
            transcript = load_scene_transcript(scene_id)
        except Exception:
            transcript = {}
    turns = transcript.get("turns") if isinstance(transcript, dict) and isinstance(transcript.get("turns"), list) else []
    if not summary and turns:
        summary = "\n".join(f"{turn.get('role', 'turn')}: {turn.get('text', '')}" for turn in turns[-10:] if isinstance(turn, dict))[:2400]
    path = _story_checkpoints_dir() / f"{checkpoint_id}.json"
    existing = _read_json_record(path) or {}
    restore_snapshot = {
        "schema_id": "neo.roleplay.stories.restore_snapshot.v1",
        "scene_id": scene_id,
        "scene_setup": scene_setup or {},
        "transcript": transcript or {"scene_id": scene_id, "turns": []},
        "runtime_bundle_id": str(payload.get("runtime_bundle_id") or (scene_setup or {}).get("runtime_bundle_id") or ""),
        "captured_at": now,
    }
    record = {
        "schema_id": "neo.roleplay.stories.checkpoint.v1",
        "checkpoint_id": checkpoint_id,
        "storyline_id": storyline_id,
        "session_id": session_id,
        "title": title,
        "summary": summary,
        "status": str(payload.get("status") or "restorable"),
        "source": str(payload.get("source") or "manual"),
        "scene_id": scene_id,
        "runtime_bundle_id": restore_snapshot.get("runtime_bundle_id") or "",
        "turn_count": int(payload.get("turn_count") or len(turns) or 0),
        "restore_ready": True,
        "restore_snapshot": restore_snapshot,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "storage_path": _relative_to_root(path),
    }
    try:
        record["memory_link"] = upsert_story_checkpoint_memory(record)
    except Exception as exc:
        record["memory_link"] = {"status": "error", "error": str(exc)}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def create_story_checkpoint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    checkpoint = create_story_checkpoint(payload or {})
    return {
        "schema_id": "neo.roleplay.stories.checkpoint.write.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "saved",
        "checkpoint": checkpoint,
        "stories": stories_state_payload("workspace"),
    }


def create_story_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session = create_story_session(payload or {})
    return {
        "schema_id": "neo.roleplay.stories.session.write.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "saved",
        "session": session,
        "stories": stories_state_payload("workspace"),
    }



def list_story_branches() -> list[dict[str, Any]]:
    ensure_stories_storage()
    records = [_read_json_record(path) for path in sorted(_story_branches_dir().glob("*.json"))]
    return sorted([record for record in records if record], key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def _flatten_checkpoint_for_diff(checkpoint: dict[str, Any]) -> dict[str, Any]:
    snapshot = checkpoint.get("restore_snapshot") if isinstance(checkpoint.get("restore_snapshot"), dict) else {}
    setup = snapshot.get("scene_setup") if isinstance(snapshot.get("scene_setup"), dict) else {}
    transcript = snapshot.get("transcript") if isinstance(snapshot.get("transcript"), dict) else {}
    turns = transcript.get("turns") if isinstance(transcript.get("turns"), list) else []
    return {
        "checkpoint_id": checkpoint.get("checkpoint_id") or checkpoint.get("record_id") or "",
        "title": checkpoint.get("title") or "",
        "summary": checkpoint.get("summary") or "",
        "status": checkpoint.get("status") or "",
        "storyline_id": checkpoint.get("storyline_id") or "",
        "session_id": checkpoint.get("session_id") or "",
        "runtime_bundle_id": checkpoint.get("runtime_bundle_id") or snapshot.get("runtime_bundle_id") or "",
        "turn_count": int(checkpoint.get("turn_count") or len(turns) or 0),
        "setup": setup,
        "turns": turns,
        "memory_link": checkpoint.get("memory_link") or {},
    }


def _diff_dict(left: dict[str, Any], right: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keys = sorted(set(left.keys()) | set(right.keys()))
    for key in keys:
        a = left.get(key)
        b = right.get(key)
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(a, dict) and isinstance(b, dict):
            rows.extend(_diff_dict(a, b, path))
        elif a != b:
            rows.append({"path": path, "before": a, "after": b, "change_type": "changed" if key in left and key in right else ("added" if key in right else "removed")})
    return rows


def compare_story_checkpoints(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_stories_storage()
    left_id = str(payload.get("left_checkpoint_id") or payload.get("checkpoint_a_id") or "").strip()
    right_id = str(payload.get("right_checkpoint_id") or payload.get("checkpoint_b_id") or "").strip()
    if not left_id or not right_id:
        raise ValueError("Choose two checkpoints to compare.")
    left = read_story_checkpoint(left_id)
    right = read_story_checkpoint(right_id)
    if not left or not right:
        raise ValueError("One or both checkpoints could not be found.")
    flat_left = _flatten_checkpoint_for_diff(left)
    flat_right = _flatten_checkpoint_for_diff(right)
    setup_diff = _diff_dict(flat_left.get("setup") or {}, flat_right.get("setup") or {})
    meta_diff = _diff_dict({k: v for k, v in flat_left.items() if k not in {"setup", "turns", "memory_link"}}, {k: v for k, v in flat_right.items() if k not in {"setup", "turns", "memory_link"}})
    left_turns = flat_left.get("turns") or []
    right_turns = flat_right.get("turns") or []
    turn_rows: list[dict[str, Any]] = []
    max_turns = max(len(left_turns), len(right_turns))
    for idx in range(max_turns):
        a = left_turns[idx] if idx < len(left_turns) and isinstance(left_turns[idx], dict) else None
        b = right_turns[idx] if idx < len(right_turns) and isinstance(right_turns[idx], dict) else None
        if a is None or b is None:
            turn_rows.append({"index": idx, "change_type": "added" if b else "removed", "before": a, "after": b})
            continue
        if (a.get("role"), a.get("text"), a.get("status")) != (b.get("role"), b.get("text"), b.get("status")):
            turn_rows.append({"index": idx, "change_type": "changed", "before": {"role": a.get("role"), "text": a.get("text"), "status": a.get("status")}, "after": {"role": b.get("role"), "text": b.get("text"), "status": b.get("status")}})
    return {
        "schema_id": "neo.roleplay.stories.checkpoint_diff.v1",
        "surface_id": "roleplay",
        "status": "diffed",
        "left_checkpoint_id": left_id,
        "right_checkpoint_id": right_id,
        "summary": {
            "meta_changes": len(meta_diff),
            "setup_changes": len(setup_diff),
            "turn_changes": len(turn_rows),
            "left_turns": len(left_turns),
            "right_turns": len(right_turns),
        },
        "meta_diff": meta_diff,
        "setup_diff": setup_diff,
        "turn_diff": turn_rows[:120],
        "left": {"title": flat_left.get("title"), "status": flat_left.get("status"), "turn_count": flat_left.get("turn_count")},
        "right": {"title": flat_right.get("title"), "status": flat_right.get("status"), "turn_count": flat_right.get("turn_count")},
    }


def branch_story_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_stories_storage()
    checkpoint_id = str(payload.get("checkpoint_id") or payload.get("source_checkpoint_id") or "").strip()
    if not checkpoint_id:
        raise ValueError("Choose a checkpoint to branch from.")
    checkpoint = read_story_checkpoint(checkpoint_id)
    if not checkpoint:
        raise ValueError("Source checkpoint could not be found.")
    now = _now()
    branch_id = str(payload.get("branch_id") or f"story_branch_{uuid.uuid4().hex[:10]}")
    branch_type = str(payload.get("branch_type") or "alternate").strip() or "alternate"
    title = str(payload.get("title") or f"Branch from {checkpoint.get('title') or checkpoint_id}").strip()
    status = str(payload.get("status") or "branch").strip() or "branch"
    source_session_id = str(checkpoint.get("session_id") or payload.get("session_id") or "unassigned")
    storyline_id = str(checkpoint.get("storyline_id") or payload.get("storyline_id") or "unassigned")
    session = create_story_session({
        "storyline_id": storyline_id,
        "title": title,
        "summary": str(payload.get("summary") or checkpoint.get("summary") or f"Branched from checkpoint {checkpoint_id}"),
        "status": status,
        "mode_lock": str(payload.get("mode_lock") or "cinematic_authoring"),
        "interaction_mode": str(payload.get("interaction_mode") or "roleplay"),
        "seed_from_checkpoint": True,
    })
    branch_record = {
        "schema_id": "neo.roleplay.stories.branch.v1",
        "branch_id": branch_id,
        "source_checkpoint_id": checkpoint_id,
        "source_session_id": source_session_id,
        "storyline_id": storyline_id,
        "session_id": session.get("session_id"),
        "title": title,
        "summary": str(payload.get("summary") or checkpoint.get("summary") or ""),
        "status": status,
        "branch_type": branch_type,
        "canon_policy": str(payload.get("canon_policy") or "alternate"),
        "restore_ready": True,
        "created_at": now,
        "updated_at": now,
    }
    path = _story_branches_dir() / f"{branch_id}.json"
    branch_record["storage_path"] = _relative_to_root(path)
    path.write_text(json.dumps(branch_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rp_checkpoint_branches
                (branch_id, source_checkpoint_id, source_session_id, storyline_id, session_id, title, status, branch_type, diff_from_checkpoint_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (branch_id, checkpoint_id, source_session_id, storyline_id, str(session.get("session_id") or ""), title, status, branch_type, checkpoint_id, json.dumps(branch_record, ensure_ascii=False), now, now),
            )
            conn.commit()
    except Exception as exc:
        branch_record["sqlite_warning"] = str(exc)
    return {
        "schema_id": "neo.roleplay.stories.branch.write.v1",
        "surface_id": "roleplay",
        "status": "branched",
        "branch": branch_record,
        "session": session,
        "source_checkpoint": checkpoint,
        "stories": stories_state_payload("workspace"),
    }


def read_story_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    ensure_stories_storage()
    clean = str(checkpoint_id or "").strip()
    if not clean:
        return None
    return _read_json_record(_story_checkpoints_dir() / f"{clean}.json")


def read_story_session(session_id: str) -> dict[str, Any] | None:
    ensure_stories_storage()
    clean = str(session_id or "").strip()
    if not clean:
        return None
    return _read_json_record(_story_sessions_dir() / f"{clean}.json")


def _latest_checkpoint_for_session(session_id: str) -> dict[str, Any] | None:
    checkpoints = [item for item in list_story_checkpoints() if str(item.get("session_id") or "") == str(session_id or "")]
    return checkpoints[0] if checkpoints else None


def restore_story_checkpoint_to_scene_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Restore a saved Stories checkpoint/session back into the active Scene lane."""
    ensure_stories_storage()
    scene_id = str(payload.get("scene_id") or "default")
    checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    restore_mode = str(payload.get("restore_mode") or "replace_scene").strip() or "replace_scene"
    checkpoint = read_story_checkpoint(checkpoint_id) if checkpoint_id else None
    session = read_story_session(session_id) if session_id else None
    if checkpoint is None and session_id:
        checkpoint = _latest_checkpoint_for_session(session_id)
        if checkpoint:
            checkpoint_id = str(checkpoint.get("checkpoint_id") or checkpoint.get("record_id") or "")
    if checkpoint is None and session is None:
        raise ValueError("Choose a checkpoint or session to restore.")
    snapshot = checkpoint.get("restore_snapshot") if isinstance(checkpoint, dict) and isinstance(checkpoint.get("restore_snapshot"), dict) else {}
    source_setup = snapshot.get("scene_setup") if isinstance(snapshot.get("scene_setup"), dict) else {}
    source_transcript = snapshot.get("transcript") if isinstance(snapshot.get("transcript"), dict) else {}
    if not source_setup:
        source_setup = {
            "scene_id": scene_id,
            "title": (checkpoint or session or {}).get("title") or "Restored Scene",
            "premise": (checkpoint or session or {}).get("summary") or "Restored from Stories.",
            "scene_notes": f"Restored from Stories checkpoint/session at {_now()}.",
            "runtime_bundle_id": (checkpoint or {}).get("runtime_bundle_id") or "",
            "continuity_mode": "session_persistent",
        }
    source_setup = dict(source_setup)
    source_setup["scene_id"] = scene_id
    source_setup["restored_from_checkpoint_id"] = checkpoint_id or ""
    source_setup["restored_from_session_id"] = session_id or str((checkpoint or {}).get("session_id") or "")
    source_setup["restore_mode"] = restore_mode
    source_setup["updated_at"] = _now()
    saved_setup = save_scene_setup_payload(source_setup)
    turns = source_transcript.get("turns") if isinstance(source_transcript.get("turns"), list) else []
    if not turns and checkpoint:
        turns = [{
            "turn_id": f"restored-summary-{uuid.uuid4().hex[:8]}",
            "role": "system",
            "text": str(checkpoint.get("summary") or "Restored checkpoint."),
            "status": "restored_summary",
            "created_at": _now(),
        }]
    restored_transcript = {
        "schema_id": "neo.roleplay.scene.transcript.v1",
        "scene_id": scene_id,
        "status": "restored",
        "restored_from_checkpoint_id": checkpoint_id or "",
        "restored_from_session_id": session_id or str((checkpoint or {}).get("session_id") or ""),
        "restore_mode": restore_mode,
        "generation_enabled": True,
        "turns": turns,
        "created_at": source_transcript.get("created_at") or _now(),
        "updated_at": _now(),
    }
    _write_json(_scene_transcript_path(scene_id), restored_transcript)
    return {
        "schema_id": "neo.roleplay.stories.restore.v1",
        "surface_id": "roleplay",
        "status": "restored",
        "scene_id": scene_id,
        "checkpoint_id": checkpoint_id or "",
        "session_id": session_id or str((checkpoint or {}).get("session_id") or ""),
        "restore_mode": restore_mode,
        "checkpoint": checkpoint or {},
        "session": session or {},
        "scene_setup": saved_setup,
        "transcript": restored_transcript,
    }


def restore_story_session_to_scene_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str((payload or {}).get("session_id") or "").strip()
    checkpoint_id = str((payload or {}).get("checkpoint_id") or "").strip()
    if not checkpoint_id and session_id:
        checkpoint = _latest_checkpoint_for_session(session_id)
        if checkpoint:
            payload = dict(payload or {})
            payload["checkpoint_id"] = checkpoint.get("checkpoint_id") or checkpoint.get("record_id") or ""
    return restore_story_checkpoint_to_scene_payload(payload or {})

def _count_json(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    return len(list(directory.glob("*.json")))


def _recent_records(directory: Path, limit: int = 8) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            title = str(data.get("title") or data.get("name") or data.get("scene_id") or data.get("storyline_id") or path.stem)
            status = str(data.get("status") or "foundation")
        except Exception:
            title = path.stem
            status = "unreadable"
        items.append({
            "record_id": path.stem,
            "title": title,
            "status": status,
            "storage_path": _relative_to_root(path),
        })
    return items


def stories_state_payload(active_view: str | None = None) -> dict[str, Any]:
    ensure_stories_storage()
    storylines = list_storylines()
    archive = {
        "status": "placeholder",
        "active_child_view": "stories",
        "stories": {
            "status": "placeholder",
            "root": _relative_to_root(_story_drafts_dir()),
            "record_count": _count_json(_story_drafts_dir()),
            "records": _recent_records(_story_drafts_dir()),
        },
        "roleplay": {
            "status": "placeholder",
            "root": _relative_to_root(_story_sessions_dir()),
            "record_count": _count_json(_story_sessions_dir()),
            "records": _recent_records(_story_sessions_dir()),
        },
        "canon": {
            "status": "placeholder",
            "root": _relative_to_root(_canon_dir()),
            "record_count": _count_json(_canon_dir()),
            "records": _recent_records(_canon_dir()),
        },
    }
    sessions = list_story_sessions()
    checkpoints = list_story_checkpoints()
    branches = list_story_branches()
    active_storyline_id = storylines[0].storyline_id if storylines else ""
    active_session = sessions[0] if sessions else {}
    active_checkpoint = checkpoints[0] if checkpoints else {}
    sqlite_state = roleplay_sqlite_state_payload()
    table_counts = sqlite_state.get("table_counts") or {}
    inspector = {
        "status": "foundation",
        "summary": {
            "status": "placeholder",
            "storyline_count": len(storylines),
            "session_count": len(sessions),
            "checkpoint_count": len(checkpoints),
            "branch_count": len(branches),
            "active_storyline_id": active_storyline_id,
            "memory_rows": {"fragments": int(table_counts.get("rp_memory_fragments") or 0), "shared": int(table_counts.get("rp_shared_memories") or 0), "turn_summaries": int(table_counts.get("rp_turn_summaries") or 0)},
            "message": "Summary generation is deferred; this panel now reports Stories records plus memory foundation rows.",
        },
        "continuity": {
            "status": "placeholder",
            "row_count": int(table_counts.get("rp_continuity_rows") or 0),
            "checkpoint_count": len(checkpoints),
            "checks_enabled": False,
            "message": "Continuity rows are now written by Scene setup and Story sessions; contradiction checks are still deferred.",
        },
        "provenance": {
            "status": "active",
            "trace_count": int(table_counts.get("rp_retrieval_traces") or 0),
            "checkpoint_memory_count": int(table_counts.get("rp_story_checkpoints") or 0),
            "trace_enabled": True,
            "message": "Full provenance graph UI is active. Use the Provenance inspector to trace Forge, memory, retrieval, runtime, checkpoint, and contradiction links.",
        },
    }
    state = RoleplayStoriesState(
        active_view=active_view or "workspace",
        storylines=storylines,
        workspace={
            "status": "foundation",
            "active_storyline_id": active_storyline_id,
            "active_session_id": str(active_session.get("session_id") or ""),
            "active_checkpoint_id": str(active_checkpoint.get("checkpoint_id") or active_checkpoint.get("record_id") or ""),
            "draft_enabled": False,
            "notes_enabled": False,
            "message": "Workspace is staged for save/resume hierarchy, sessions, checkpoints, and Scene handoff.",
            "sessions": sessions,
            "checkpoints": checkpoints,
            "branches": branches,
            "restore_target": {
                "storyline_id": active_storyline_id,
                "session_id": str(active_session.get("session_id") or ""),
                "checkpoint_id": str(active_checkpoint.get("checkpoint_id") or active_checkpoint.get("record_id") or ""),
                "mode_lock": str(active_session.get("mode_lock") or "cinematic_authoring"),
                "interaction_mode": str(active_session.get("interaction_mode") or "roleplay"),
                "restore_ready": bool(active_checkpoint or active_session),
            },
            "memory_link": {
                "sqlite_ready": bool(sqlite_state.get("ready")),
                "continuity_rows": int(table_counts.get("rp_continuity_rows") or 0),
                "checkpoint_rows": int(table_counts.get("rp_story_checkpoints") or 0),
                "story_fragments": int(table_counts.get("rp_memory_fragments") or 0),
            },
            "storage_roots": [
                _relative_to_root(_story_drafts_dir()),
                _relative_to_root(_story_sessions_dir()),
                _relative_to_root(_story_checkpoints_dir()),
                _relative_to_root(_story_snapshots_dir()),
                _relative_to_root(_story_branches_dir()),
            ],
        },
        storyline={
            "status": "foundation",
            "record_count": len(storylines),
            "root": _relative_to_root(_storylines_dir()),
            "fields": ["title", "premise", "arc", "beats", "status", "project_id", "continuity_policy"],
        },
        archive=archive,
        inspector=inspector,
    )
    state_dict = model_to_dict(state)
    state_dict["sessions"] = sessions
    state_dict["checkpoints"] = checkpoints
    state_dict["branches"] = branches
    state_dict["active_storyline_id"] = active_storyline_id
    state_dict["active_session_id"] = str(active_session.get("session_id") or "")
    state_dict["active_checkpoint_id"] = str(active_checkpoint.get("checkpoint_id") or active_checkpoint.get("record_id") or "")
    return state_dict


def create_storyline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    record = create_storyline(payload)
    return {
        "schema_id": "neo.roleplay.stories.storyline.write.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "saved",
        "storyline": model_to_dict(record),
        "stories": stories_state_payload("storyline"),
    }
