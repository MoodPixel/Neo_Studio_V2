from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.forge import list_forge_records
from neo_app.roleplay.sandbox_contract import derive_sandbox_context, context_json, context_scope_id
from neo_app.roleplay.sqlite_store import _connect, _json, ensure_roleplay_memory_schema, roleplay_sqlite_state_payload
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root

PHASE185_SCHEMA_ID = "neo.roleplay.scoped_compile_plan.v1"
PHASE185_VERSION = "1.0.0-phase18.5-scoped-compile-plan-record-state"
PHASE185_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "scoped_compile_plan_contract.json"
PHASE185_STATE_PATH = ROLEPLAY_DATA_ROOT / "scoped_compile_plan_state.json"

_SCOPE_KEYS = ("project_id", "sandbox_id", "universe_id", "world_id", "region_id", "city_id", "location_id", "storyline_id", "session_id", "branch_id")
_ACTIVE_FRAGMENT_STATUSES = ("compiled_builder_record", "active", "candidate_runtime", "approved_canon")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, default: str = "item") -> str:
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean(value).lower()).strip("-")
    return cleaned[:80] or default


def _stable_id(*parts: Any, prefix: str = "compile") -> str:
    base = "|".join(_clean(part) for part in parts)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=8).hexdigest()
    return f"{prefix}:{_slug(parts[-1] if parts else '', 'row')}:{digest}"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value or "")


def record_checksum(record: dict[str, Any]) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    if _table_exists(conn, table) and not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_scoped_compile_schema() -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_compile_runs (
                compile_run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'changed_only',
                scope_type TEXT NOT NULL DEFAULT 'global',
                scope_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                record_count INTEGER NOT NULL DEFAULT 0,
                compiled_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                fragment_count INTEGER NOT NULL DEFAULT 0,
                plan_json TEXT NOT NULL DEFAULT '{}',
                summary_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS rp_record_compile_state (
                record_id TEXT PRIMARY KEY,
                record_kind TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                sandbox_id TEXT NOT NULL DEFAULT '',
                universe_id TEXT NOT NULL DEFAULT '',
                world_id TEXT NOT NULL DEFAULT '',
                region_id TEXT NOT NULL DEFAULT '',
                city_id TEXT NOT NULL DEFAULT '',
                location_id TEXT NOT NULL DEFAULT '',
                source_record_id TEXT NOT NULL DEFAULT '',
                source_record_kind TEXT NOT NULL DEFAULT '',
                current_checksum TEXT NOT NULL DEFAULT '',
                last_compiled_checksum TEXT NOT NULL DEFAULT '',
                compile_status TEXT NOT NULL DEFAULT 'new',
                last_compiled_at TEXT NOT NULL DEFAULT '',
                last_compile_run_id TEXT NOT NULL DEFAULT '',
                compiled_fragment_count INTEGER NOT NULL DEFAULT 0,
                last_compile_error TEXT NOT NULL DEFAULT '',
                sandbox_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_record_compile_scope ON rp_record_compile_state(scope_id, compile_status, record_kind);
            CREATE INDEX IF NOT EXISTS idx_rp_record_compile_world ON rp_record_compile_state(universe_id, world_id, compile_status);
            CREATE INDEX IF NOT EXISTS idx_rp_compile_runs_scope ON rp_compile_runs(scope_type, scope_id, started_at);
            """
        )
        # Phase 18.5 uses non-destructive superseding instead of silent fragment growth.
        _add_column(conn, "rp_memory_fragments", "superseded_by_run_id TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "rp_memory_fragments", "superseded_at TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "rp_vector_index", "compile_run_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    return roleplay_sqlite_state_payload()


def _normalise_record_kind(value: Any) -> str:
    clean = _clean(value).lower().replace(" ", "_").replace("/", "_")
    aliases = {
        "region_kingdom": "region",
        "city_settlement": "city",
        "legend_canon": "legend",
        "lore": "legend",
        "canon": "legend",
        "locations": "location",
        "characters": "character",
        "organizations": "organization",
        "artifacts": "artifact",
        "relationships": "relationship",
    }
    return aliases.get(clean, clean) or "record"


def _coerce_disk_record(raw: dict[str, Any], path: Path) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("payload"), dict):
        payload = raw.get("payload") or {}
        rid = _clean(raw.get("record_id") or payload.get("id") or path.stem)
        kind = _normalise_record_kind(raw.get("kind") or payload.get("kind") or path.parent.name)
        return {
            "record_id": rid,
            "kind": kind,
            "title": _clean(raw.get("title") or payload.get("display_label") or payload.get("label") or rid),
            "body": _clean(raw.get("body") or raw.get("summary") or payload.get("summary")),
            "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else payload.get("tags", []),
            "payload": payload,
            "markdown": _clean(raw.get("markdown")),
            "created_at": _clean(raw.get("created_at") or (payload.get("meta") or {}).get("created_at") if isinstance(payload.get("meta"), dict) else ""),
            "updated_at": _clean(raw.get("updated_at") or (payload.get("meta") or {}).get("updated_at") if isinstance(payload.get("meta"), dict) else ""),
            "storage_path": _clean(raw.get("storage_path") or _relative_to_root(path)),
        }
    if raw.get("kind") or raw.get("fields") or raw.get("links"):
        kind = _normalise_record_kind(raw.get("kind") or path.parent.name)
        rid = _clean(raw.get("id") or path.stem)
        return {
            "record_id": rid,
            "kind": kind,
            "title": _clean(raw.get("display_label") or raw.get("label") or rid),
            "body": _clean(raw.get("summary")),
            "tags": raw.get("tags", []) if isinstance(raw.get("tags"), list) else [],
            "payload": raw,
            "markdown": "",
            "created_at": _clean((raw.get("meta") or {}).get("created_at") if isinstance(raw.get("meta"), dict) else ""),
            "updated_at": _clean((raw.get("meta") or {}).get("updated_at") if isinstance(raw.get("meta"), dict) else ""),
            "storage_path": _relative_to_root(path),
        }
    return None


def _records_from_disk_fallback() -> list[dict[str, Any]]:
    root = ROLEPLAY_DATA_ROOT / "entities"
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in sorted(root.glob("*/*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        record = _coerce_disk_record(raw, path)
        if record:
            records.append(record)
    return records


def _records() -> list[dict[str, Any]]:
    # Compile must read the same file-backed Forge records that Scope Discovery reads.
    # Newly imported bundles can exist on disk before the live Forge API surfaces them.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for record in _records_from_disk_fallback():
        rid = _clean(record.get("record_id") or (record.get("payload") or {}).get("id"))
        kind = _clean(record.get("kind") or (record.get("payload") or {}).get("kind") or "record").lower()
        if rid:
            merged[(kind, rid)] = record
    try:
        live_records = [model_to_dict(record) for record in list_forge_records(None)]
    except Exception:
        live_records = []
    for record in live_records:
        rid = _clean(record.get("record_id") or (record.get("payload") or {}).get("id"))
        kind = _clean(record.get("kind") or (record.get("payload") or {}).get("kind") or "record").lower()
        if rid:
            merged[(kind, rid)] = record
    return list(merged.values())


def _state_row(conn: sqlite3.Connection, record_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM rp_record_compile_state WHERE record_id=?", (record_id,)).fetchone()
    return dict(row) if row else {}


def _derive_record_status(state_row: dict[str, Any], checksum: str) -> str:
    existing = _clean(state_row.get("compile_status"))
    if existing == "compile_failed":
        return "compile_failed"
    last = _clean(state_row.get("last_compiled_checksum"))
    if not last:
        return "new"
    if last != checksum:
        return "changed_since_compile"
    # A record can be marked compiled after an older/import test run while having no
    # usable fragments. Treat that as stale so Scope Build repairs the memory layer.
    if _safe_int(state_row.get("compiled_fragment_count"), 0) <= 0:
        return "missing_fragments"
    return "compiled"


def _record_info(record: dict[str, Any], state_row: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    record_id = _clean(record.get("record_id") or payload.get("id"))
    kind = _clean(record.get("kind") or payload.get("kind") or "record")
    title = _clean(record.get("title") or payload.get("display_label") or payload.get("label") or record_id)
    context = derive_sandbox_context(payload, record_id=record_id, kind=kind)
    checksum = record_checksum(record)
    status = _derive_record_status(state_row or {}, checksum)
    scope_id = context_scope_id(context)
    return {
        "record_id": record_id,
        "record_kind": kind,
        "kind": kind,
        "title": title,
        "scope_id": scope_id,
        "current_checksum": checksum,
        "compile_status": status,
        "last_compiled_checksum": _clean((state_row or {}).get("last_compiled_checksum")),
        "last_compiled_at": _clean((state_row or {}).get("last_compiled_at")),
        "last_compile_run_id": _clean((state_row or {}).get("last_compile_run_id")),
        "compiled_fragment_count": _safe_int((state_row or {}).get("compiled_fragment_count"), 0),
        "last_compile_error": _clean((state_row or {}).get("last_compile_error")),
        "sandbox_context": context,
        **{key: _clean(context.get(key)) for key in _SCOPE_KEYS},
    }


def _upsert_record_state(conn: sqlite3.Connection, info: dict[str, Any], *, status: str | None = None, run_id: str = "", fragment_count: int | None = None, error: str = "", mark_compiled: bool = False) -> None:
    now = _now()
    compile_status = status or info.get("compile_status") or "new"
    last_compiled_checksum = info.get("last_compiled_checksum") or ""
    last_compiled_at = info.get("last_compiled_at") or ""
    if mark_compiled:
        compile_status = "compiled"
        last_compiled_checksum = info.get("current_checksum") or ""
        last_compiled_at = now
    conn.execute(
        """
        INSERT INTO rp_record_compile_state(record_id, record_kind, title, scope_id, project_id, sandbox_id, universe_id, world_id, region_id, city_id, location_id, source_record_id, source_record_kind, current_checksum, last_compiled_checksum, compile_status, last_compiled_at, last_compile_run_id, compiled_fragment_count, last_compile_error, sandbox_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_id) DO UPDATE SET
            record_kind=excluded.record_kind,
            title=excluded.title,
            scope_id=excluded.scope_id,
            project_id=excluded.project_id,
            sandbox_id=excluded.sandbox_id,
            universe_id=excluded.universe_id,
            world_id=excluded.world_id,
            region_id=excluded.region_id,
            city_id=excluded.city_id,
            location_id=excluded.location_id,
            source_record_id=excluded.source_record_id,
            source_record_kind=excluded.source_record_kind,
            current_checksum=excluded.current_checksum,
            last_compiled_checksum=excluded.last_compiled_checksum,
            compile_status=excluded.compile_status,
            last_compiled_at=excluded.last_compiled_at,
            last_compile_run_id=excluded.last_compile_run_id,
            compiled_fragment_count=excluded.compiled_fragment_count,
            last_compile_error=excluded.last_compile_error,
            sandbox_json=excluded.sandbox_json,
            updated_at=excluded.updated_at
        """,
        (
            info.get("record_id", ""), info.get("record_kind", ""), info.get("title", ""), info.get("scope_id", ""),
            info.get("project_id", ""), info.get("sandbox_id", ""), info.get("universe_id", ""), info.get("world_id", ""),
            info.get("region_id", ""), info.get("city_id", ""), info.get("location_id", ""), info.get("record_id", ""),
            info.get("record_kind", ""), info.get("current_checksum", ""), last_compiled_checksum, compile_status, last_compiled_at,
            run_id or info.get("last_compile_run_id", ""), int(fragment_count if fragment_count is not None else info.get("compiled_fragment_count") or 0),
            error, context_json(info.get("sandbox_context") or {}), now,
        ),
    )


def sync_record_compile_state() -> dict[str, Any]:
    ensure_scoped_compile_schema()
    records = _records()
    changed = 0
    with _connect() as conn:
        for record in records:
            rid = _clean(record.get("record_id") or (record.get("payload") or {}).get("id"))
            info = _record_info(record, _state_row(conn, rid))
            previous = _clean(_state_row(conn, rid).get("compile_status"))
            _upsert_record_state(conn, info)
            if previous != info.get("compile_status"):
                changed += 1
        conn.commit()
    return {"status": "synced", "record_count": len(records), "changed_count": changed}


def _matches_scope(info: dict[str, Any], scope_type: str, scope_id: str) -> bool:
    clean_type = _clean(scope_type).lower() or "global"
    clean_id = _clean(scope_id)
    if clean_type in {"global", "all"} or not clean_id:
        return True
    if clean_type in {"scope", "sandbox"}:
        return clean_id in {info.get("scope_id"), info.get("sandbox_id")} or clean_id in _canonical_json(info.get("sandbox_context") or {})
    key = clean_type if clean_type.endswith("_id") else f"{clean_type}_id"
    return _clean(info.get(key)) == clean_id


def _wanted_action(info: dict[str, Any], mode: str, include_compiled: bool) -> tuple[str, str]:
    status = info.get("compile_status") or "new"
    stale_statuses = {"new", "changed_since_compile", "compile_failed", "missing_fragments"}
    # Explicit All means force rebuild every matching/linked record. Include Compiled is
    # still honored by selected/current-scope modes, but All should be literal.
    if mode == "all":
        return ("compile", "mode_all")
    if mode in {"changed_only", "changed", "stale"}:
        return ("compile", status) if status in stale_statuses else ("skip", "already_compiled")
    if mode in {"selected", "selected_records", "scope", "current_scope"}:
        return ("compile", status) if include_compiled or status in stale_statuses else ("skip", "already_compiled")
    return ("compile", status) if include_compiled or status in stale_statuses else ("skip", "already_compiled")


def build_compile_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_scoped_compile_schema()
    sync_record_compile_state()
    payload = payload or {}
    mode = _clean(payload.get("mode") or "changed_only").lower()
    scope_type = _clean(payload.get("scope_type") or "global").lower()
    scope_id = _clean(payload.get("scope_id") or "")
    kind_filter = _clean(payload.get("kind") or payload.get("record_kind") or "")
    record_ids = payload.get("record_ids") or payload.get("selected_record_ids") or []
    if isinstance(record_ids, str):
        record_ids = [part.strip() for part in record_ids.split(",") if part.strip()]
    selected_set = {str(item) for item in record_ids if str(item).strip()}
    include_compiled = bool(payload.get("include_compiled", False))
    limit = max(1, min(_safe_int(payload.get("limit"), 1000), 5000))

    plan_records: list[dict[str, Any]] = []
    skipped_out_of_scope = 0
    with _connect() as conn:
        for record in _records():
            rid = _clean(record.get("record_id") or (record.get("payload") or {}).get("id"))
            info = _record_info(record, _state_row(conn, rid))
            if kind_filter and info.get("record_kind") != kind_filter:
                skipped_out_of_scope += 1
                continue
            if selected_set and rid not in selected_set:
                skipped_out_of_scope += 1
                continue
            if not selected_set and not _matches_scope(info, scope_type, scope_id):
                skipped_out_of_scope += 1
                continue
            action, reason = _wanted_action(info, mode, include_compiled)
            plan_records.append({**info, "action": action, "reason": reason})
            if len(plan_records) >= limit:
                break
    compile_count = len([row for row in plan_records if row["action"] == "compile"])
    skipped_count = len(plan_records) - compile_count
    status = "ready" if compile_count else "nothing_to_compile"
    return {
        "schema_id": "neo.roleplay.scoped_compile_plan.preview.v1",
        "status": status,
        "mode": mode,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "kind": kind_filter,
        "include_compiled": include_compiled,
        "record_count": len(plan_records),
        "compile_count": compile_count,
        "skipped_count": skipped_count,
        "out_of_scope_count": skipped_out_of_scope,
        "records": plan_records,
        "summary": {
            "new": len([r for r in plan_records if r.get("compile_status") == "new"]),
            "changed_since_compile": len([r for r in plan_records if r.get("compile_status") == "changed_since_compile"]),
            "compiled": len([r for r in plan_records if r.get("compile_status") == "compiled"]),
            "compile_failed": len([r for r in plan_records if r.get("compile_status") == "compile_failed"]),
            "missing_fragments": len([r for r in plan_records if r.get("compile_status") == "missing_fragments"]),
        },
        "warning": "All/global can touch multiple universes. Prefer current sandbox/world/scenario or changed_only mode for normal work." if scope_type in {"global", "all"} else "",
    }


def _supersede_existing_fragments(conn: sqlite3.Connection, record_id: str, run_id: str, now: str) -> int:
    rows = [str(row[0]) for row in conn.execute("SELECT fragment_id FROM rp_memory_fragments WHERE source_type='forge_record' AND source_id=? AND status NOT IN ('superseded','archived')", (record_id,)).fetchall()]
    if not rows:
        return 0
    conn.execute("UPDATE rp_memory_fragments SET status='superseded', vector_status='superseded', superseded_by_run_id=?, superseded_at=?, updated_at=? WHERE source_type='forge_record' AND source_id=? AND status NOT IN ('superseded','archived')", (run_id, now, now, record_id))
    for fragment_id in rows:
        conn.execute("UPDATE rp_vector_index SET vector_status='superseded' WHERE source_table='rp_memory_fragments' AND source_id=?", (fragment_id,))
    return len(rows)


def execute_compile_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from neo_app.roleplay.builder_memory_compiler import upsert_compiled_builder_record_memory
    from neo_app.roleplay.sqlite_upgrade import rebuild_roleplay_memory_search_documents
    from neo_app.roleplay.embedding_reranker_adapter import index_roleplay_search_documents_payload
    try:
        from neo_app.roleplay.chroma_vector_mirror import mirror_roleplay_vectors_to_chroma_payload
    except Exception:  # pragma: no cover
        mirror_roleplay_vectors_to_chroma_payload = None

    ensure_scoped_compile_schema()
    payload = payload or {}
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else build_compile_plan_payload(payload)
    run_id = _stable_id(plan.get("mode"), plan.get("scope_type"), plan.get("scope_id"), _now(), prefix="compile-run")
    now = _now()
    compiled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    fragment_count = 0
    record_map = {_clean(record.get("record_id") or (record.get("payload") or {}).get("id")): record for record in _records()}

    with _connect() as conn:
        conn.execute(
            "INSERT INTO rp_compile_runs(compile_run_id, mode, scope_type, scope_id, status, started_at, record_count, plan_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, plan.get("mode") or "changed_only", plan.get("scope_type") or "global", plan.get("scope_id") or "", "running", now, int(plan.get("record_count") or 0), _json(plan)),
        )
        conn.commit()

    for item in plan.get("records") or []:
        rid = _clean(item.get("record_id"))
        if item.get("action") != "compile":
            skipped.append({"record_id": rid, "reason": item.get("reason") or "skipped"})
            continue
        record = record_map.get(rid)
        if not record:
            errors.append({"record_id": rid, "error": "Forge record missing at execution time"})
            continue
        try:
            with _connect() as conn:
                _supersede_existing_fragments(conn, rid, run_id, now)
                conn.commit()
            result = upsert_compiled_builder_record_memory(record, delete_existing=False)
            fragment_count += int(result.get("fragment_count") or 0)
            info = _record_info(record, {})
            with _connect() as conn:
                _upsert_record_state(conn, info, run_id=run_id, fragment_count=int(result.get("fragment_count") or 0), mark_compiled=True)
                conn.execute("UPDATE rp_memory_fragments SET status='compiled_builder_record', superseded_by_run_id='', superseded_at='' WHERE source_type='forge_record' AND source_id=? AND status!='superseded'", (rid,))
                conn.commit()
            compiled.append(result)
        except Exception as exc:
            err = str(exc)
            errors.append({"record_id": rid, "kind": item.get("record_kind") or item.get("kind") or "", "error": err})
            with _connect() as conn:
                info = _record_info(record, _state_row(conn, rid) if record else {}) if record else {"record_id": rid, "record_kind": item.get("record_kind", ""), "title": item.get("title", ""), "current_checksum": item.get("current_checksum", "")}
                _upsert_record_state(conn, info, status="compile_failed", run_id=run_id, error=err)
                conn.commit()
    status = "compiled" if not errors else "partial"
    if not compiled and errors:
        status = "failed"
    finished = _now()
    summary = {"compiled": len(compiled), "skipped": len(skipped), "errors": len(errors), "fragment_count": fragment_count}
    with _connect() as conn:
        conn.execute(
            "UPDATE rp_compile_runs SET status=?, finished_at=?, compiled_count=?, skipped_count=?, error_count=?, fragment_count=?, summary_json=? WHERE compile_run_id=?",
            (status, finished, len(compiled), len(skipped), len(errors), fragment_count, _json(summary), run_id),
        )
        conn.commit()

    followups: dict[str, Any] = {}
    if bool(payload.get("rebuild_search", False)):
        followups["rebuild_search"] = rebuild_roleplay_memory_search_documents(limit=_safe_int(payload.get("search_limit"), 20000))
    if bool(payload.get("index_after", False)):
        followups["index"] = index_roleplay_search_documents_payload({"limit": _safe_int(payload.get("index_limit"), 2000), "force": bool(payload.get("force_index", True)), "scope_id": plan.get("scope_id") or ""})
    if bool(payload.get("mirror_after", False)) and mirror_roleplay_vectors_to_chroma_payload:
        followups["chroma_mirror"] = mirror_roleplay_vectors_to_chroma_payload({"limit": _safe_int(payload.get("mirror_limit"), 2000), "scope_id": plan.get("scope_id") or ""})

    return {
        "schema_id": "neo.roleplay.scoped_compile_plan.execute.v1",
        "status": status,
        "compile_run_id": run_id,
        "plan": plan,
        "compiled_count": len(compiled),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "fragment_count": fragment_count,
        "compiled": compiled[:100],
        "skipped": skipped[:100],
        "errors": errors,
        "followups": followups,
        "state": scoped_compile_state_payload(),
        "next_step": "Rebuild search / index vectors for this scope, then run Runtime retrieval and build a Scene Packet.",
    }


def scoped_compile_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_scoped_compile_schema()
    sync_record_compile_state()
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM rp_record_compile_state ORDER BY updated_at DESC LIMIT 500").fetchall()]
        status_counts: dict[str, int] = {}
        for row in rows:
            status_counts[row.get("compile_status") or "unknown"] = status_counts.get(row.get("compile_status") or "unknown", 0) + 1
        latest_runs = [dict(row) for row in conn.execute("SELECT compile_run_id, mode, scope_type, scope_id, status, started_at, finished_at, record_count, compiled_count, skipped_count, error_count, fragment_count FROM rp_compile_runs ORDER BY started_at DESC LIMIT 12").fetchall()]
        active_fragments = conn.execute("SELECT COUNT(*) FROM rp_memory_fragments WHERE status NOT IN ('superseded','archived')").fetchone()[0]
        superseded_fragments = conn.execute("SELECT COUNT(*) FROM rp_memory_fragments WHERE status='superseded'").fetchone()[0]
    payload = {
        "schema_id": "neo.roleplay.scoped_compile_plan.state.v1",
        "version": PHASE185_VERSION,
        "status": "active",
        "record_count": len(rows),
        "status_counts": status_counts,
        "active_fragment_count": int(active_fragments or 0),
        "superseded_fragment_count": int(superseded_fragments or 0),
        "records": rows[:200],
        "latest_runs": latest_runs,
        "safe_defaults": {"mode": "changed_only", "scope_type": "sandbox", "include_compiled": False, "all_global_is_advanced": True},
    }
    if write_report:
        PHASE185_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE185_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def scoped_compile_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE185_SCHEMA_ID,
        "version": PHASE185_VERSION,
        "phase": "Phase 18.5 — Scoped Compile Plan + Record Compile State",
        "status": "implemented",
        "purpose": "Prevent global memory soup by requiring previewable scope-aware compile plans and per-record compile state.",
        "tables": ["rp_compile_runs", "rp_record_compile_state"],
        "compile_modes": ["changed_only", "selected_records", "current_scope", "all"],
        "scope_types": ["global", "project", "sandbox", "universe", "world", "region", "city", "location", "scenario"],
        "record_statuses": ["new", "compiled", "changed_since_compile", "compile_failed", "missing_fragments"],
        "fragment_policy": "Recompile supersedes old fragments instead of endlessly appending; search documents and vector retrieval should ignore superseded rows.",
        "endpoints": {
            "contract": "GET /api/roleplay/scoped-compile/contract",
            "state": "GET /api/roleplay/scoped-compile/state",
            "ensure_schema": "POST /api/roleplay/scoped-compile/ensure-schema",
            "preview_plan": "POST /api/roleplay/scoped-compile/preview-plan",
            "execute_plan": "POST /api/roleplay/scoped-compile/execute-plan",
        },
    }
    if write_report:
        PHASE185_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE185_CONTRACT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
