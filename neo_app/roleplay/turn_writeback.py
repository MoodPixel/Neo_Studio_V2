from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_store import ensure_roleplay_memory_schema
from neo_app.roleplay.sandbox_contract import derive_sandbox_context, context_json, context_scope_id, ensure_roleplay_sandbox_schema

TURN_WRITEBACK_SCHEMA_ID: Final[str] = "neo.roleplay.turn_writeback.v1"
TURN_WRITEBACK_VERSION: Final[str] = "1.0.0-phase13-turn-writeback-continuity"
TURN_WRITEBACK_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "turn_writeback_contract.json"
TURN_WRITEBACK_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "turn_writeback_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, limit: int = 72) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean(value).lower()).strip("-")
    return (text[:limit] or "item")


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _insert_dynamic(conn: sqlite3.Connection, table: str, payload: dict[str, Any], key_columns: tuple[str, ...] = ()) -> None:
    cols = _columns(conn, table)
    if not cols:
        return
    data = {k: v for k, v in payload.items() if k in cols}
    if not data:
        return
    col_names = list(data.keys())
    placeholders = ", ".join("?" for _ in col_names)
    quoted = ", ".join(col_names)
    if key_columns and all(k in data for k in key_columns):
        update_cols = [c for c in col_names if c not in key_columns]
        if update_cols:
            updates = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = f"INSERT INTO {table}({quoted}) VALUES ({placeholders}) ON CONFLICT({', '.join(key_columns)}) DO UPDATE SET {updates}"
        else:
            sql = f"INSERT OR IGNORE INTO {table}({quoted}) VALUES ({placeholders})"
    else:
        sql = f"INSERT OR REPLACE INTO {table}({quoted}) VALUES ({placeholders})"
    conn.execute(sql, tuple(data[c] for c in col_names))


def ensure_turn_writeback_schema(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    owns = conn is None
    conn = conn or _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_turn_writebacks (
                writeback_id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL DEFAULT '',
                user_turn_id TEXT NOT NULL DEFAULT '',
                assistant_turn_id TEXT NOT NULL DEFAULT '',
                scene_packet_id TEXT NOT NULL DEFAULT '',
                retrieval_trace_id TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'candidate_runtime',
                summary TEXT NOT NULL DEFAULT '',
                writeback_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_turn_writebacks_scene ON rp_turn_writebacks(scene_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_rp_turn_writebacks_scope ON rp_turn_writebacks(scope_id, status);
            CREATE TABLE IF NOT EXISTS rp_continuity_events (
                event_id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT 'scene_turn',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'candidate_runtime',
                source_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_continuity_events_scene ON rp_continuity_events(scene_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_rp_continuity_events_scope ON rp_continuity_events(scope_id, status);
            """
        )
        ensure_roleplay_sandbox_schema(conn)
        # Phase 13 context columns for newly introduced tables.
        for table in ("rp_turn_writebacks", "rp_continuity_events"):
            existing = _columns(conn, table)
            for column, ddl in {
                "project_id": "TEXT NOT NULL DEFAULT ''",
                "sandbox_id": "TEXT NOT NULL DEFAULT ''",
                "universe_id": "TEXT NOT NULL DEFAULT ''",
                "world_id": "TEXT NOT NULL DEFAULT ''",
                "region_id": "TEXT NOT NULL DEFAULT ''",
                "city_id": "TEXT NOT NULL DEFAULT ''",
                "location_id": "TEXT NOT NULL DEFAULT ''",
                "source_record_id": "TEXT NOT NULL DEFAULT ''",
                "source_record_kind": "TEXT NOT NULL DEFAULT ''",
                "canon_snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "storyline_id": "TEXT NOT NULL DEFAULT ''",
                "session_id": "TEXT NOT NULL DEFAULT ''",
                "branch_id": "TEXT NOT NULL DEFAULT ''",
                "memory_scope": "TEXT NOT NULL DEFAULT 'scene'",
                "promotion_scope": "TEXT NOT NULL DEFAULT 'runtime'",
                "sandbox_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_sandbox ON {table}(sandbox_id, memory_scope, promotion_scope)")
        if owns:
            conn.commit()
        counts = {}
        for table in ("rp_turn_writebacks", "rp_turn_summaries", "rp_continuity_rows", "rp_continuity_events", "rp_unresolved_threads", "rp_memory_fragments", "rp_relationship_state", "rp_character_states"):
            if _table_exists(conn, table):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {"schema_id": TURN_WRITEBACK_SCHEMA_ID, "contract_version": TURN_WRITEBACK_VERSION, "status": "ready", "table_counts": counts, "checked_at": _now()}
    finally:
        if owns:
            conn.close()


def turn_writeback_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    schema = ensure_turn_writeback_schema()
    payload = {
        "schema_id": TURN_WRITEBACK_SCHEMA_ID,
        "contract_version": TURN_WRITEBACK_VERSION,
        "phase": "Phase 13 — Turn Writeback + Continuity",
        "generated_at": _now(),
        "source_of_truth": "SQLite remains authoritative. Scene turn writebacks are candidate/runtime memory until explicitly promoted.",
        "writeback_outputs": [
            "rp_turn_writebacks audit row",
            "rp_turn_summaries concise scene summary",
            "rp_continuity_rows candidate continuity note",
            "rp_continuity_events richer event log",
            "rp_memory_fragments candidate_runtime scene memory",
            "rp_unresolved_threads open question/hook rows",
            "optional relationship/character state rows when explicit payload provides them",
        ],
        "promotion_policy": {
            "default_promotion_scope": "runtime",
            "canon_auto_promotion": False,
            "allowed_statuses": ["candidate_runtime", "candidate_canon", "approved_runtime", "rejected", "archived"],
        },
        "schema_state": schema,
        "paths": {
            "contract_path": _relative_to_root(TURN_WRITEBACK_CONTRACT_PATH),
            "state_path": _relative_to_root(TURN_WRITEBACK_STATE_PATH),
            "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        },
        "next_required_phase": "Phase 14 — Stories + Checkpoint Restore",
    }
    if write_report:
        TURN_WRITEBACK_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        TURN_WRITEBACK_CONTRACT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload




def _is_polluted_roleplay_writeback_text(value: Any) -> bool:
    text = str(value or "")
    if not text.strip():
        return False
    patterns = (
        r"\[\s*End\s+Scene\s*\]",
        r"\bthe\s+scene\s+ended\b",
        r"\banother\s+session\b",
        r"(?:^|\n|\s)(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}(?:['’]s)?\s+Response\s*(?:\*\*)?:",
        r"(?:^|\n|\s)(?:#{1,6}\s*)?(?:Next\s+Beat|Summary|Assistant\s+turn|Scene\s+input)\s*:",
        r"\[\s*content\s+redacted",
        r"\bthe\s+scene\s+referenc(?:es|ed)\s+\w+\b",
        r"\bfrom\s+his\s+computer\s+screen\b",
        r"\bdata\s+upload\b",
        r"\bbe\s+a\s+bit\s+more\s+patient\s+please\b",
        r"\bresponse\s+is\s+blocked\s+by\s+(?:a\s+)?guardrail\b",
        r"\bRoughly,\s*my\s+turn\s*:",
        r"\bI\s+await\s+(?:the\s+)?(?:reply|response)\s+from\b",
        r"\bNeo\s+Studio\s+Roleplay\s+Scene\s+Engine\b",
        r"(?:^|\n)\s*[—-]\s*Neo\s+Studio\b",
        r"\b(?:the\s+)?conversation\s+ended\s+abruptly\b",
        r"\bNext\s+scene\s*:",
        r"\b(?:the\s+)?next\s+scene\s+will\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def _extract_unresolved_threads(user_text: str, assistant_text: str) -> list[dict[str, Any]]:
    text = f"{user_text}\n{assistant_text}".strip()
    threads: list[dict[str, Any]] = []
    questions = re.findall(r"([^.!?]{8,180}\?)", text)
    for question in questions[:4]:
        threads.append({
            "title": question.strip()[:90],
            "content": question.strip(),
            "thread_type": "question_or_mystery",
            "status": "open",
            "priority": "normal",
        })
    for marker in ("secret", "promise", "vow", "betray", "truth", "hidden", "remember", "forgot", "missing", "danger"):
        if re.search(rf"\b{marker}\b", text, re.I):
            threads.append({
                "title": f"Open thread: {marker}",
                "content": f"Open hook marker detected: {marker}.",
                "thread_type": "open_hook",
                "status": "open",
                "priority": "high" if marker in {"secret", "betray", "truth", "promise", "vow"} else "normal",
            })
    # Deduplicate by title.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in threads:
        key = _slug(item.get("title"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:8]


def _extract_callback_anchors(user_text: str, assistant_text: str) -> list[str]:
    text = f"{user_text}\n{assistant_text}"
    anchors: list[str] = []
    quoted = re.findall(r"[\"“”']([^\"“”']{8,120})[\"“”']", text)
    anchors.extend(q.strip() for q in quoted[:5])
    for pattern in (r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b",):
        for match in re.findall(pattern, text):
            if len(match) > 3 and match.lower() not in {"Scene", "Roleplay", "Neo"}:
                anchors.append(match.strip())
    # Keep concise unique anchors.
    out: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        key = anchor.lower()
        if key not in seen:
            seen.add(key)
            out.append(anchor)
    return out[:12]


def build_turn_writeback_candidate(*, scene_id: str, setup: dict[str, Any], user_turn: dict[str, Any], assistant_turn: dict[str, Any], prompt: dict[str, Any] | None = None, transcript: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt = prompt if isinstance(prompt, dict) else {}
    transcript = transcript if isinstance(transcript, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    user_text = _clean(user_turn.get("text"))
    assistant_text = _clean(assistant_turn.get("text"))
    scene_packet = prompt.get("scene_packet") if isinstance(prompt.get("scene_packet"), dict) else {}
    assistant_status = _clean(assistant_turn.get("status"))
    if assistant_status not in {"streamed", "generated"} or _is_polluted_roleplay_writeback_text(assistant_text):
        return {
            "schema_id": TURN_WRITEBACK_SCHEMA_ID,
            "contract_version": TURN_WRITEBACK_VERSION,
            "writeback_id": f"writeback:{scene_id}:skipped:{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id,
            "status": "skipped_polluted_or_nonfinal",
            "summary": "",
            "skip_reason": "assistant turn was not clean generated story content",
            "created_at": _now(),
            "updated_at": _now(),
        }
    scene_packet_id = _clean(assistant_turn.get("scene_packet_id") or scene_packet.get("scene_packet_id") or scene_packet.get("packet_id") or setup.get("scene_packet_id"))
    retrieval_trace_id = _clean(assistant_turn.get("retrieval_trace_id") or ((prompt.get("retrieval") or {}).get("search") or {}).get("trace_id"))
    context = derive_sandbox_context({
        "id": scene_id,
        "kind": "scene_turn",
        "links": {"scope": {
            "world_id": scene_packet.get("world_id") or setup.get("world_id") or "",
            "region_id": scene_packet.get("region_id") or setup.get("region_id") or "",
            "city_id": scene_packet.get("city_id") or setup.get("city_id") or "",
        }},
        "meta": {
            "sandbox_id": scene_packet.get("sandbox_id") or scene_packet.get("scope_id") or setup.get("memory_scope") or scene_packet_id or scene_id,
            "storyline_id": setup.get("storyline_id") or scene_packet.get("storyline_id") or "",
            "session_id": setup.get("session_id") or scene_id,
            "branch_id": setup.get("branch_id") or "",
            "memory_scope": "scene",
            "promotion_scope": "runtime",
        },
    }, record_id=scene_id, kind="scene_turn", defaults={"memory_scope": "scene", "promotion_scope": "runtime"})
    scope_id = context_scope_id(context, scene_packet_id or scene_id)
    summary_bits = []
    if setup.get("title"):
        summary_bits.append(f"Scene: {setup.get('title')}")
    if user_text:
        summary_bits.append(f"User turn: {user_text[:500]}")
    if assistant_text:
        summary_bits.append(f"Assistant turn: {assistant_text[:700]}")
    summary = "\n".join(summary_bits).strip()
    callback_anchors = _extract_callback_anchors(user_text, assistant_text)
    unresolved_threads = _extract_unresolved_threads(user_text, assistant_text)
    continuity_rows = [{
        "title": f"Scene turn continuity · {setup.get('title') or scene_id}",
        "content": summary,
        "continuity_type": "scene_turn_summary",
        "status": "candidate_runtime",
        "source_id": _clean(assistant_turn.get("turn_id")),
    }]
    writeback_id = f"writeback:{scene_id}:{assistant_turn.get('turn_id') or uuid.uuid4().hex[:10]}"
    return {
        "schema_id": TURN_WRITEBACK_SCHEMA_ID,
        "contract_version": TURN_WRITEBACK_VERSION,
        "writeback_id": writeback_id,
        "scene_id": scene_id,
        "scope_id": scope_id,
        "scene_packet_id": scene_packet_id,
        "retrieval_trace_id": retrieval_trace_id,
        "user_turn_id": _clean(user_turn.get("turn_id")),
        "assistant_turn_id": _clean(assistant_turn.get("turn_id")),
        "status": "candidate_runtime",
        "summary": summary,
        "context": context,
        "promotion_scope": "runtime",
        "memory_scope": "scene",
        "callback_anchors": callback_anchors,
        "unresolved_threads": unresolved_threads,
        "continuity_rows": continuity_rows,
        "character_state_updates": extra.get("character_state_updates") if isinstance(extra.get("character_state_updates"), list) else [],
        "relationship_state_updates": extra.get("relationship_state_updates") if isinstance(extra.get("relationship_state_updates"), list) else [],
        "source": {
            "setup_title": setup.get("title") or "",
            "runtime_bundle_id": prompt.get("runtime_bundle_id") or setup.get("runtime_bundle_id") or "",
            "memory_injection_status": assistant_turn.get("memory_injection_status") or "",
        },
        "created_at": _now(),
        "updated_at": _now(),
    }


def persist_turn_writeback_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    ensure_turn_writeback_schema()
    candidate = candidate if isinstance(candidate, dict) else {}
    if str(candidate.get("status") or "") == "skipped_polluted_or_nonfinal":
        return {"schema_id": TURN_WRITEBACK_SCHEMA_ID, "status": "skipped", "reason": candidate.get("skip_reason") or "not clean generated story content", "writeback_id": candidate.get("writeback_id") or ""}
    context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
    sandbox = context_json(context)
    context_cols = {**context, "sandbox_json": sandbox, "memory_scope": candidate.get("memory_scope") or "scene", "promotion_scope": candidate.get("promotion_scope") or "runtime"}
    now = _now()
    writeback_id = _clean(candidate.get("writeback_id") or f"writeback:{uuid.uuid4().hex}")
    scene_id = _clean(candidate.get("scene_id") or "default")
    scope_id = _clean(candidate.get("scope_id") or context_scope_id(context, scene_id))
    summary = _clean(candidate.get("summary"))
    assistant_turn_id = _clean(candidate.get("assistant_turn_id"))
    with _connect() as conn:
        ensure_turn_writeback_schema(conn)
        base = {
            "writeback_id": writeback_id,
            "scene_id": scene_id,
            "user_turn_id": _clean(candidate.get("user_turn_id")),
            "assistant_turn_id": assistant_turn_id,
            "scene_packet_id": _clean(candidate.get("scene_packet_id")),
            "retrieval_trace_id": _clean(candidate.get("retrieval_trace_id")),
            "scope_id": scope_id,
            "status": _clean(candidate.get("status") or "candidate_runtime"),
            "summary": summary,
            "writeback_json": _json(candidate),
            "created_at": _clean(candidate.get("created_at") or now),
            "updated_at": now,
            **context_cols,
        }
        _insert_dynamic(conn, "rp_turn_writebacks", base, ("writeback_id",))

        summary_id = f"turn_summary:{assistant_turn_id or _slug(writeback_id)}"
        _insert_dynamic(conn, "rp_turn_summaries", {
            "summary_id": summary_id,
            "scene_id": scene_id,
            "turn_id": assistant_turn_id,
            "summary": summary,
            "payload_json": _json(candidate),
            "created_at": _clean(candidate.get("created_at") or now),
            "updated_at": now,
            "scope_id": scope_id,
            **context_cols,
        }, ("summary_id",))

        continuity_ids: list[str] = []
        for idx, item in enumerate(candidate.get("continuity_rows") or []):
            if not isinstance(item, dict):
                continue
            content = _clean(item.get("content"))
            if not content:
                continue
            row_id = f"continuity:{scene_id}:{assistant_turn_id or _slug(writeback_id)}:{idx}"
            continuity_ids.append(row_id)
            row = {
                "row_id": row_id,
                "scope_id": scope_id,
                "continuity_type": _clean(item.get("continuity_type") or "scene_turn_summary"),
                "title": _clean(item.get("title") or "Scene turn continuity"),
                "content": content,
                "source_id": _clean(item.get("source_id") or assistant_turn_id or writeback_id),
                "status": _clean(item.get("status") or "candidate_runtime"),
                "created_at": now,
                "updated_at": now,
                **context_cols,
            }
            _insert_dynamic(conn, "rp_continuity_rows", row, ("row_id",))
            _insert_dynamic(conn, "rp_continuity_events", {
                "event_id": f"event:{row_id}",
                "scene_id": scene_id,
                "scope_id": scope_id,
                "event_type": row.get("continuity_type"),
                "title": row.get("title"),
                "content": content,
                "status": row.get("status"),
                "source_id": row.get("source_id"),
                "payload_json": _json({"writeback_id": writeback_id, "continuity_row_id": row_id, **item}),
                "created_at": now,
                "updated_at": now,
                **context_cols,
            }, ("event_id",))

        unresolved_ids: list[str] = []
        for idx, item in enumerate(candidate.get("unresolved_threads") or []):
            if not isinstance(item, dict):
                continue
            content = _clean(item.get("content"))
            if not content:
                continue
            title = _clean(item.get("title") or content[:90])
            thread_id = f"thread:{scene_id}:{_slug(title, 42)}"
            unresolved_ids.append(thread_id)
            _insert_dynamic(conn, "rp_unresolved_threads", {
                "thread_id": thread_id,
                "scope_id": scope_id,
                "scene_id": scene_id,
                "title": title,
                "thread_type": _clean(item.get("thread_type") or "open_hook"),
                "status": _clean(item.get("status") or "open"),
                "priority": _clean(item.get("priority") or "normal"),
                "content": content,
                "payload_json": _json({"writeback_id": writeback_id, **item}),
                "created_at": now,
                "updated_at": now,
                **context_cols,
            }, ("thread_id",))

        anchor_ids: list[str] = []
        for idx, anchor in enumerate(candidate.get("callback_anchors") or []):
            anchor_text = _clean(anchor)
            if not anchor_text:
                continue
            anchor_id = f"callback:{scene_id}:{_slug(anchor_text, 40)}"
            anchor_ids.append(anchor_id)
            _insert_dynamic(conn, "rp_callback_anchors", {
                "anchor_id": anchor_id,
                "scope_id": scope_id,
                "title": anchor_text[:90],
                "content": anchor_text,
                "source_id": assistant_turn_id or writeback_id,
                "status": "candidate_runtime",
                "priority": "normal",
                "payload_json": _json({"writeback_id": writeback_id, "anchor": anchor_text}),
                "created_at": now,
                "updated_at": now,
                **context_cols,
            }, ("anchor_id",))

        fragment_id = f"scene_turn:{assistant_turn_id or _slug(writeback_id)}:writeback"
        _insert_dynamic(conn, "rp_memory_fragments", {
            "fragment_id": fragment_id,
            "namespace": "roleplay.scene",
            "source_type": "scene_turn_writeback",
            "source_id": writeback_id,
            "memory_type": "episodic_memory",
            "status": "candidate_runtime",
            "content": summary,
            "tags_json": _json(["scene_turn", "writeback", "continuity"]),
            "payload_json": _json(candidate),
            "vector_status": "not_indexed",
            "created_at": now,
            "updated_at": now,
            **context_cols,
        }, ("fragment_id",))

        # Optional explicit state updates from future UI/model extraction; Phase 13 does not invent state rows.
        relationship_state_ids: list[str] = []
        for idx, item in enumerate(candidate.get("relationship_state_updates") or []):
            if not isinstance(item, dict):
                continue
            state_id = _clean(item.get("state_id") or f"relationship_state:{scene_id}:{idx}:{_slug(item.get('state_label') or item.get('relationship_type') or 'state')}")
            relationship_state_ids.append(state_id)
            _insert_dynamic(conn, "rp_relationship_state", {
                "state_id": state_id,
                "character_a_id": _clean(item.get("character_a_id")),
                "character_b_id": _clean(item.get("character_b_id")),
                "relationship_type": _clean(item.get("relationship_type") or "inferred_scene_state"),
                "state_label": _clean(item.get("state_label") or "candidate update"),
                "payload_json": _json({"writeback_id": writeback_id, **item}),
                "updated_at": now,
                "scope_id": scope_id,
                **context_cols,
            }, ("state_id",))

        character_state_ids: list[str] = []
        for idx, item in enumerate(candidate.get("character_state_updates") or []):
            if not isinstance(item, dict):
                continue
            character_id = _clean(item.get("character_id") or item.get("id"))
            state_id = _clean(item.get("state_id") or f"character_state:{scene_id}:{character_id or idx}")
            character_state_ids.append(state_id)
            _insert_dynamic(conn, "rp_character_states", {
                "state_id": state_id,
                "character_id": character_id,
                "scope_id": scope_id,
                "display_name": _clean(item.get("display_name") or character_id),
                "current_emotion": _clean(item.get("current_emotion") or item.get("emotion") or ""),
                "emotional_vector_json": _json(item.get("emotional_vector") or {}),
                "goals_json": _json(item.get("goals") or []),
                "boundaries_json": _json(item.get("boundaries") or []),
                "payload_json": _json({"writeback_id": writeback_id, **item}),
                "trust_level": _clean(item.get("trust_level") or "candidate"),
                "updated_at": now,
                **context_cols,
            }, ("state_id",))

        conn.commit()
    return {
        "schema_id": TURN_WRITEBACK_SCHEMA_ID,
        "status": "persisted",
        "writeback_id": writeback_id,
        "scene_id": scene_id,
        "scope_id": scope_id,
        "summary_id": summary_id,
        "fragment_id": fragment_id,
        "continuity_row_ids": continuity_ids,
        "unresolved_thread_ids": unresolved_ids,
        "callback_anchor_ids": anchor_ids,
        "relationship_state_ids": relationship_state_ids,
        "character_state_ids": character_state_ids,
        "promotion_scope": candidate.get("promotion_scope") or "runtime",
        "memory_scope": candidate.get("memory_scope") or "scene",
    }


def apply_turn_writeback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else payload
    if not candidate.get("writeback_id") and (payload.get("scene_id") or payload.get("user_turn") or payload.get("assistant_turn")):
        candidate = build_turn_writeback_candidate(
            scene_id=_clean(payload.get("scene_id") or "default"),
            setup=payload.get("setup") if isinstance(payload.get("setup"), dict) else {},
            user_turn=payload.get("user_turn") if isinstance(payload.get("user_turn"), dict) else {},
            assistant_turn=payload.get("assistant_turn") if isinstance(payload.get("assistant_turn"), dict) else {},
            prompt=payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {},
            transcript=payload.get("transcript") if isinstance(payload.get("transcript"), dict) else {},
            extra=payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
        )
    result = persist_turn_writeback_candidate(candidate)
    return {"ok": result.get("status") == "persisted", "writeback": result, "candidate": candidate, "state": turn_writeback_state_payload(scene_id=candidate.get("scene_id") or "default")}


def writeback_scene_turn(*, scene_id: str, setup: dict[str, Any], user_turn: dict[str, Any], assistant_turn: dict[str, Any], prompt: dict[str, Any] | None = None, transcript: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = build_turn_writeback_candidate(scene_id=scene_id, setup=setup, user_turn=user_turn, assistant_turn=assistant_turn, prompt=prompt, transcript=transcript, extra=extra)
    persisted = persist_turn_writeback_candidate(candidate)
    status = "skipped" if persisted.get("status") == "skipped" else "written"
    return {"status": status, "candidate": candidate, "persisted": persisted}



def archive_scene_runtime_writebacks(scene_id: str = "default") -> dict[str, Any]:
    """Archive generated/runtime continuity for a scene without touching Forge canon."""
    ensure_turn_writeback_schema()
    scene = _clean(scene_id or "default")
    now = _now()
    counts: dict[str, int] = {}
    with _connect() as conn:
        for table in ("rp_turn_writebacks", "rp_turn_summaries", "rp_continuity_rows", "rp_continuity_events", "rp_unresolved_threads"):
            if not _table_exists(conn, table):
                continue
            cols = _columns(conn, table)
            if "scene_id" not in cols or "status" not in cols:
                continue
            counts[table] = conn.execute(f"UPDATE {table} SET status = 'archived', updated_at = ? WHERE scene_id = ?", (now, scene)).rowcount
        if _table_exists(conn, "rp_memory_fragments"):
            counts["rp_memory_fragments"] = conn.execute(
                "UPDATE rp_memory_fragments SET status = 'archived', updated_at = ? WHERE source_type IN ('scene_turn_writeback', 'scene_turn') AND (source_id LIKE ? OR payload_json LIKE ?)",
                (now, f"%:{scene}:%", f"%\"scene_id\": \"{scene}\"%"),
            ).rowcount
        conn.commit()
    return {"status": "archived", "scene_id": scene, "counts": counts}

def turn_writeback_state_payload(scene_id: str = "default", *, limit: int = 20, write_report: bool = False) -> dict[str, Any]:
    schema = ensure_turn_writeback_schema()
    lim = max(1, min(int(limit or 20), 100))
    scene = _clean(scene_id or "default")
    rows: dict[str, list[dict[str, Any]]] = {"writebacks": [], "turn_summaries": [], "continuity_rows": [], "continuity_events": [], "unresolved_threads": []}
    with _connect() as conn:
        if _table_exists(conn, "rp_turn_writebacks"):
            rows["writebacks"] = [dict(row) for row in conn.execute("SELECT writeback_id, scene_id, scope_id, status, summary, scene_packet_id, retrieval_trace_id, updated_at FROM rp_turn_writebacks WHERE scene_id = ? AND status NOT IN ('rejected','archived','skipped_polluted_or_nonfinal') ORDER BY updated_at DESC LIMIT ?", (scene, lim)).fetchall() if not _is_polluted_roleplay_writeback_text(row["summary"])]
        if _table_exists(conn, "rp_turn_summaries"):
            rows["turn_summaries"] = [dict(row) for row in conn.execute("SELECT summary_id, scene_id, turn_id, summary, updated_at FROM rp_turn_summaries WHERE scene_id = ? ORDER BY updated_at DESC LIMIT ?", (scene, lim)).fetchall()]
        if _table_exists(conn, "rp_continuity_rows"):
            cols = _columns(conn, "rp_continuity_rows")
            if "scene_id" in cols:
                query = "SELECT row_id, scope_id, continuity_type, title, content, status, updated_at FROM rp_continuity_rows WHERE scene_id = ? ORDER BY updated_at DESC LIMIT ?"
                args = (scene, lim)
            else:
                query = "SELECT row_id, scope_id, continuity_type, title, content, status, updated_at FROM rp_continuity_rows ORDER BY updated_at DESC LIMIT ?"
                args = (lim,)
            rows["continuity_rows"] = [dict(row) for row in conn.execute(query, args).fetchall()]
        if _table_exists(conn, "rp_continuity_events"):
            rows["continuity_events"] = [dict(row) for row in conn.execute("SELECT event_id, scene_id, scope_id, event_type, title, content, status, updated_at FROM rp_continuity_events WHERE scene_id = ? ORDER BY updated_at DESC LIMIT ?", (scene, lim)).fetchall()]
        if _table_exists(conn, "rp_unresolved_threads"):
            rows["unresolved_threads"] = [dict(row) for row in conn.execute("SELECT thread_id, scene_id, scope_id, title, thread_type, status, priority, content, updated_at FROM rp_unresolved_threads WHERE scene_id = ? ORDER BY updated_at DESC LIMIT ?", (scene, lim)).fetchall()]
    payload = {
        "schema_id": TURN_WRITEBACK_SCHEMA_ID,
        "contract_version": TURN_WRITEBACK_VERSION,
        "status": "ready",
        "scene_id": scene,
        "schema_state": schema,
        "counts": {key: len(value) for key, value in rows.items()},
        **rows,
        "checked_at": _now(),
    }
    if write_report:
        TURN_WRITEBACK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TURN_WRITEBACK_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
