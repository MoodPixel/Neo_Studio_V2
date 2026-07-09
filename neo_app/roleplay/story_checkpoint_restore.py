from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.scene import load_scene_setup, load_scene_transcript, save_scene_setup_payload, _scene_transcript_path, _write_json
from neo_app.roleplay.scene_packet_builder import PHASE11_PACKET_DIR
from neo_app.roleplay.stories import (
    create_story_checkpoint,
    ensure_stories_storage,
    restore_story_checkpoint_to_scene_payload,
    stories_state_payload,
)
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema
from neo_app.roleplay.turn_writeback import turn_writeback_state_payload

PHASE14_SCHEMA_ID: Final[str] = "neo.roleplay.stories_checkpoint_restore.v1"
PHASE14_VERSION: Final[str] = "1.0.0-phase14-stories-checkpoint-restore"
PHASE14_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "story_checkpoint_restore_contract.json"
PHASE14_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "story_checkpoint_restore_state.json"
PHASE14_RESTORE_DIR: Final[Path] = ROLEPLAY_DATA_ROOT / "story_restores"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_json(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def ensure_story_checkpoint_restore_schema() -> dict[str, Any]:
    """Additive Phase 14 tables for storyline/session/checkpoint restore history."""
    ensure_stories_storage()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_storyline_index (
                storyline_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                active_session_id TEXT NOT NULL DEFAULT '',
                active_checkpoint_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_story_session_index (
                session_id TEXT PRIMARY KEY,
                storyline_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                latest_checkpoint_id TEXT NOT NULL DEFAULT '',
                branch_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_story_checkpoint_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                checkpoint_id TEXT NOT NULL DEFAULT '',
                storyline_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                scene_id TEXT NOT NULL DEFAULT '',
                scene_packet_id TEXT NOT NULL DEFAULT '',
                runtime_bundle_id TEXT NOT NULL DEFAULT '',
                turn_count INTEGER NOT NULL DEFAULT 0,
                active_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_story_restore_events (
                restore_id TEXT PRIMARY KEY,
                checkpoint_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                storyline_id TEXT NOT NULL DEFAULT '',
                scene_id TEXT NOT NULL DEFAULT '',
                restore_mode TEXT NOT NULL DEFAULT 'replace_scene',
                scene_packet_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'restored',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_story_snapshot_checkpoint ON rp_story_checkpoint_snapshots(checkpoint_id);
            CREATE INDEX IF NOT EXISTS idx_rp_story_snapshot_session ON rp_story_checkpoint_snapshots(session_id);
            CREATE INDEX IF NOT EXISTS idx_rp_story_restore_checkpoint ON rp_story_restore_events(checkpoint_id);
            CREATE INDEX IF NOT EXISTS idx_rp_story_restore_scene ON rp_story_restore_events(scene_id);
            """
        )
        conn.commit()
        counts = {
            "storyline_index": _table_count(conn, "rp_storyline_index"),
            "session_index": _table_count(conn, "rp_story_session_index"),
            "checkpoint_snapshots": _table_count(conn, "rp_story_checkpoint_snapshots"),
            "restore_events": _table_count(conn, "rp_story_restore_events"),
        }
    return {"status": "schema_ready", "counts": counts}


def _load_scene_packet(scene_packet_id: str) -> dict[str, Any]:
    clean = str(scene_packet_id or "").strip()
    if not clean:
        return {}
    try:
        with _connect() as conn:
            row = conn.execute("SELECT payload_json FROM rp_scene_memory_packets WHERE packet_id = ?", (clean,)).fetchone()
            if row:
                packet = _read_json(row["payload_json"], {}) or {}
                if isinstance(packet, dict):
                    return packet
    except Exception:
        pass
    path = PHASE11_PACKET_DIR / f"{clean}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _active_memory_ids_from_packet(packet: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for section in [
        "world_context",
        "location_context",
        "character_context",
        "relationship_context",
        "scenario_context",
        "canon_guards",
        "callback_anchors",
        "continuity_rows",
        "retrieved_memory",
    ]:
        for row in packet.get(section) or []:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or row.get("result_id") or "").strip()
            source_table = str(row.get("source_table") or row.get("table") or section).strip()
            if source_id:
                ids.append(f"{source_table}:{source_id}")
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _persist_checkpoint_snapshot(checkpoint: dict[str, Any], snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    ensure_story_checkpoint_restore_schema()
    now = _now()
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
    snapshot_id = str(snapshot_payload.get("snapshot_id") or f"checkpoint_snapshot_{uuid.uuid4().hex[:10]}")
    scene_setup = snapshot_payload.get("scene_setup") if isinstance(snapshot_payload.get("scene_setup"), dict) else {}
    transcript = snapshot_payload.get("transcript") if isinstance(snapshot_payload.get("transcript"), dict) else {}
    turns = transcript.get("turns") if isinstance(transcript.get("turns"), list) else []
    scene_packet = snapshot_payload.get("scene_packet") if isinstance(snapshot_payload.get("scene_packet"), dict) else {}
    active_memory_ids = _active_memory_ids_from_packet(scene_packet)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_story_checkpoint_snapshots(snapshot_id, checkpoint_id, storyline_id, session_id, scene_id, scene_packet_id, runtime_bundle_id, turn_count, active_memory_ids_json, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                checkpoint_id=excluded.checkpoint_id,
                storyline_id=excluded.storyline_id,
                session_id=excluded.session_id,
                scene_id=excluded.scene_id,
                scene_packet_id=excluded.scene_packet_id,
                runtime_bundle_id=excluded.runtime_bundle_id,
                turn_count=excluded.turn_count,
                active_memory_ids_json=excluded.active_memory_ids_json,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                snapshot_id,
                checkpoint_id,
                str(checkpoint.get("storyline_id") or ""),
                str(checkpoint.get("session_id") or ""),
                str(scene_setup.get("scene_id") or checkpoint.get("scene_id") or "default"),
                str(scene_setup.get("scene_packet_id") or scene_packet.get("scene_packet_id") or scene_packet.get("packet_id") or ""),
                str(scene_setup.get("runtime_bundle_id") or checkpoint.get("runtime_bundle_id") or ""),
                int(checkpoint.get("turn_count") or len(turns) or 0),
                _json(active_memory_ids),
                _json(snapshot_payload),
                now,
                now,
            ),
        )
        conn.commit()
    return {"snapshot_id": snapshot_id, "active_memory_ids": active_memory_ids, "status": "snapshotted"}


def capture_active_scene_checkpoint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a restorable checkpoint that includes Scene setup, transcript, active packet, memory ids and writeback proof."""
    ensure_story_checkpoint_restore_schema()
    clean_payload = dict(payload or {})
    scene_id = str(clean_payload.get("scene_id") or "default")
    scene_setup = clean_payload.get("scene_setup") if isinstance(clean_payload.get("scene_setup"), dict) else load_scene_setup(scene_id)
    transcript = clean_payload.get("transcript") if isinstance(clean_payload.get("transcript"), dict) else load_scene_transcript(scene_id)
    turns = transcript.get("turns") if isinstance(transcript.get("turns"), list) else []
    scene_packet_id = str(clean_payload.get("scene_packet_id") or scene_setup.get("scene_packet_id") or "")
    scene_packet = clean_payload.get("scene_packet") if isinstance(clean_payload.get("scene_packet"), dict) else _load_scene_packet(scene_packet_id)
    turn_writeback = turn_writeback_state_payload(scene_id=scene_id, limit=20)
    checkpoint_payload = dict(clean_payload)
    checkpoint_payload.update({
        "scene_id": scene_id,
        "scene_setup": scene_setup,
        "transcript": transcript,
        "scene_packet_id": scene_packet_id or str(scene_packet.get("scene_packet_id") or scene_packet.get("packet_id") or ""),
        "runtime_bundle_id": str(clean_payload.get("runtime_bundle_id") or scene_setup.get("runtime_bundle_id") or ""),
        "turn_count": int(clean_payload.get("turn_count") or len(turns) or 0),
        "source": str(clean_payload.get("source") or "scene_live_phase14"),
    })
    checkpoint = create_story_checkpoint(checkpoint_payload)
    snapshot_payload = {
        "schema_id": "neo.roleplay.stories.checkpoint_snapshot.v1",
        "snapshot_id": f"snapshot_{checkpoint.get('checkpoint_id')}",
        "checkpoint_id": checkpoint.get("checkpoint_id") or "",
        "scene_id": scene_id,
        "scene_setup": scene_setup,
        "transcript": transcript,
        "scene_packet": scene_packet,
        "turn_writeback": turn_writeback,
        "captured_at": _now(),
    }
    checkpoint["restore_snapshot"] = snapshot_payload
    checkpoint["scene_packet_id"] = checkpoint_payload.get("scene_packet_id") or ""
    snapshot_link = _persist_checkpoint_snapshot(checkpoint, snapshot_payload)
    checkpoint["snapshot_link"] = snapshot_link
    try:
        # Re-save checkpoint JSON + SQLite memory with enriched snapshot payload.
        create_story_checkpoint(checkpoint)
    except Exception:
        pass
    return {
        "schema_id": "neo.roleplay.stories.checkpoint.capture_active.v1",
        "status": "checkpoint_saved",
        "checkpoint": checkpoint,
        "snapshot": snapshot_link,
        "stories": stories_state_payload("workspace"),
    }



def _latest_snapshot_for_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    clean = str(checkpoint_id or "").strip()
    if not clean:
        return {}
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM rp_story_checkpoint_snapshots WHERE checkpoint_id = ? ORDER BY updated_at DESC LIMIT 1",
                (clean,),
            ).fetchone()
            if row:
                payload = _read_json(row["payload_json"], {}) or {}
                return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}

def _restore_scene_packet_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    packet = snapshot.get("scene_packet") if isinstance(snapshot.get("scene_packet"), dict) else {}
    packet_id = str(packet.get("scene_packet_id") or packet.get("packet_id") or "").strip()
    if not packet_id:
        return {"status": "no_packet"}
    PHASE11_PACKET_DIR.mkdir(parents=True, exist_ok=True)
    _write_report(PHASE11_PACKET_DIR / f"{packet_id}.json", packet)
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO rp_scene_memory_packets(packet_id, scene_id, scope_id, title, emotional_tone, relationship_state_json, character_knowledge_json, canon_locks_json, unresolved_threads_json, continuity_warnings_json, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(packet_id) DO UPDATE SET
                    scene_id=excluded.scene_id,
                    scope_id=excluded.scope_id,
                    title=excluded.title,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    packet_id,
                    str(packet.get("scene_id") or "default"),
                    str(packet.get("scope_id") or packet.get("sandbox_id") or ""),
                    str(packet.get("title") or packet_id),
                    str((packet.get("model_instructions") or {}).get("tone") or "restored"),
                    _json(packet.get("relationship_context") or []),
                    _json(packet.get("character_context") or []),
                    _json(packet.get("canon_guards") or []),
                    _json(packet.get("callback_anchors") or []),
                    _json(packet.get("continuity_rows") or []),
                    _json(packet),
                    str(packet.get("created_at") or _now()),
                    _now(),
                ),
            )
            conn.commit()
    except Exception as exc:
        return {"status": "packet_file_restored_sql_warning", "scene_packet_id": packet_id, "warning": str(exc)}
    return {"status": "packet_restored", "scene_packet_id": packet_id, "storage_path": _relative_to_root(PHASE11_PACKET_DIR / f"{packet_id}.json")}


def restore_checkpoint_with_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_story_checkpoint_restore_schema()
    result = restore_story_checkpoint_to_scene_payload(payload or {})
    checkpoint = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
    snapshot = checkpoint.get("restore_snapshot") if isinstance(checkpoint.get("restore_snapshot"), dict) else {}
    checkpoint_id_for_snapshot = str(result.get("checkpoint_id") or checkpoint.get("checkpoint_id") or "")
    if not snapshot.get("scene_packet"):
        snapshot = _latest_snapshot_for_checkpoint(checkpoint_id_for_snapshot) or snapshot
    packet_restore = _restore_scene_packet_from_snapshot(snapshot)
    if packet_restore.get("scene_packet_id"):
        setup = result.get("scene_setup") if isinstance(result.get("scene_setup"), dict) else {}
        setup["scene_packet_id"] = packet_restore.get("scene_packet_id") or ""
        setup = save_scene_setup_payload(setup)
        result["scene_setup"] = setup
    restore_id = f"story_restore_{uuid.uuid4().hex[:10]}"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_story_restore_events(restore_id, checkpoint_id, session_id, storyline_id, scene_id, restore_mode, scene_packet_id, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                restore_id,
                str(result.get("checkpoint_id") or checkpoint.get("checkpoint_id") or ""),
                str(result.get("session_id") or checkpoint.get("session_id") or ""),
                str(checkpoint.get("storyline_id") or ""),
                str(result.get("scene_id") or "default"),
                str(result.get("restore_mode") or "replace_scene"),
                str(packet_restore.get("scene_packet_id") or ""),
                str(result.get("status") or "restored"),
                _json({"restore": result, "packet_restore": packet_restore}),
                _now(),
            ),
        )
        conn.commit()
    result["restore_id"] = restore_id
    result["packet_restore"] = packet_restore
    result["phase14"] = {"status": "restored_with_snapshot", "restore_id": restore_id}
    return result


def story_checkpoint_restore_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE14_SCHEMA_ID,
        "version": PHASE14_VERSION,
        "status": "active",
        "purpose": "Stories/checkpoints become a reliable restore layer for Scene setup, transcript, active Scene Packet, runtime memory ids, branches, and restore events.",
        "endpoints": {
            "contract": "/api/roleplay/story-checkpoint-restore/contract",
            "state": "/api/roleplay/story-checkpoint-restore/state",
            "ensure_schema": "/api/roleplay/story-checkpoint-restore/ensure-schema",
            "capture_active": "/api/roleplay/story-checkpoint/capture-active",
            "restore_snapshot": "/api/roleplay/story-checkpoint/restore-snapshot",
        },
        "restore_snapshot_sections": [
            "scene_setup",
            "transcript",
            "scene_packet",
            "turn_writeback",
            "active_memory_ids",
        ],
        "locked_rules": [
            "Checkpoint restore must restore the active scene_packet_id when a packet was captured.",
            "Checkpoint capture writes JSON files and SQLite audit rows; SQLite stays source of truth for restore events.",
            "Branching creates a new session from a checkpoint without promoting alternate branch memory to canon.",
        ],
    }
    if write_report:
        _write_report(PHASE14_CONTRACT_PATH, payload)
    return payload


def story_checkpoint_restore_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    schema = ensure_story_checkpoint_restore_schema()
    with _connect() as conn:
        counts = {
            "storylines": _table_count(conn, "rp_storyline_index"),
            "sessions": _table_count(conn, "rp_story_session_index"),
            "checkpoints": _table_count(conn, "rp_story_checkpoints"),
            "checkpoint_snapshots": _table_count(conn, "rp_story_checkpoint_snapshots"),
            "branches": _table_count(conn, "rp_checkpoint_branches"),
            "restore_events": _table_count(conn, "rp_story_restore_events"),
        }
        latest_restore = None
        try:
            row = conn.execute("SELECT * FROM rp_story_restore_events ORDER BY created_at DESC LIMIT 1").fetchone()
            if row:
                latest_restore = dict(row)
        except Exception:
            latest_restore = None
    payload = {
        "schema_id": "neo.roleplay.stories_checkpoint_restore.state.v1",
        "version": PHASE14_VERSION,
        "status": "active",
        "ready": True,
        "schema": schema,
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "restore_dir": _relative_to_root(PHASE14_RESTORE_DIR),
        "counts": counts,
        "latest_restore": latest_restore or {},
    }
    if write_report:
        _write_report(PHASE14_STATE_PATH, payload)
    return payload
