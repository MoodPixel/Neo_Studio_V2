from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

SANDBOX_CONTRACT_SCHEMA_ID: Final[str] = "neo.roleplay.memory.sandbox_contract.v1"
SANDBOX_CONTRACT_VERSION: Final[str] = "1.0.0-phase2-sandbox-contract"
SANDBOX_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "memory_sandbox_contract.json"
SANDBOX_AUDIT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "memory_sandbox_audit.json"

SANDBOX_COLUMNS: Final[dict[str, str]] = {
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
    "memory_scope": "TEXT NOT NULL DEFAULT 'builder_record'",
    "promotion_scope": "TEXT NOT NULL DEFAULT 'draft'",
    "sandbox_json": "TEXT NOT NULL DEFAULT '{}'",
}

TABLES_REQUIRING_SANDBOX_COLUMNS: Final[tuple[str, ...]] = (
    "rp_entities",
    "rp_memory_fragments",
    "rp_shared_memories",
    "rp_source_documents",
    "rp_source_chunks",
    "rp_canon_records",
    "rp_relationship_state",
    "rp_character_states",
    "rp_character_knowledge",
    "rp_unresolved_threads",
    "rp_scene_memory_packets",
    "rp_continuity_rows",
    "rp_retrieval_traces",
    "rp_turn_summaries",
    "rp_story_checkpoints",
    "rp_vector_index",
)

PROMOTION_SCOPES: Final[tuple[str, ...]] = ("draft", "runtime", "candidate_canon", "canon", "rejected", "archived")
MEMORY_SCOPES: Final[tuple[str, ...]] = ("builder_record", "project", "world", "story", "scene", "character", "relationship", "novel_source")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    node: Any = payload
    for key in keys:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return node


def derive_sandbox_context(payload: dict[str, Any] | None = None, *, record_id: str = "", kind: str = "", defaults: dict[str, Any] | None = None) -> dict[str, str]:
    """Normalize V2 Roleplay/Novel scope metadata into one stable sandbox contract.

    This is intentionally conservative: it does not invent canon/story ids, but it
    does derive a practical sandbox_id from the strongest available scope so big
    Builder JSON records can be isolated before Phase 3 deep compilation.
    """
    payload = payload if isinstance(payload, dict) else {}
    defaults = defaults if isinstance(defaults, dict) else {}
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    scope = links.get("scope") if isinstance(links.get("scope"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}

    source_record_id = _clean(record_id or payload.get("id") or defaults.get("source_record_id"))
    source_record_kind = _clean(kind or payload.get("kind") or defaults.get("source_record_kind"))
    project_id = _clean(defaults.get("project_id") or scope.get("project_id") or payload.get("project_id") or project.get("project_id"))
    universe_id = _clean(defaults.get("universe_id") or scope.get("universe_id") or payload.get("universe_id"))
    world_id = _clean(defaults.get("world_id") or scope.get("world_id") or scope.get("current_world_id") or scope.get("origin_world_id") or payload.get("world_id"))
    region_id = _clean(defaults.get("region_id") or scope.get("region_id") or scope.get("current_region_id") or scope.get("origin_region_id") or payload.get("region_id"))
    city_id = _clean(defaults.get("city_id") or scope.get("city_id") or scope.get("current_city_id") or scope.get("origin_city_id") or payload.get("city_id"))
    location_id = _clean(defaults.get("location_id") or scope.get("location_id") or scope.get("current_location_id") or scope.get("origin_location_id") or payload.get("location_id"))
    canon_snapshot_id = _clean(defaults.get("canon_snapshot_id") or payload.get("canon_snapshot_id") or meta.get("canon_snapshot_id"))
    storyline_id = _clean(defaults.get("storyline_id") or payload.get("storyline_id") or meta.get("storyline_id") or runtime.get("storyline_id"))
    session_id = _clean(defaults.get("session_id") or payload.get("session_id") or meta.get("session_id") or runtime.get("session_id"))
    branch_id = _clean(defaults.get("branch_id") or payload.get("branch_id") or meta.get("branch_id") or runtime.get("branch_id"))
    memory_scope = _clean(defaults.get("memory_scope") or payload.get("memory_scope") or meta.get("memory_scope")) or "builder_record"
    promotion_scope = _clean(defaults.get("promotion_scope") or payload.get("promotion_scope") or meta.get("promotion_scope")) or "draft"

    if memory_scope not in MEMORY_SCOPES:
        memory_scope = "builder_record"
    if promotion_scope not in PROMOTION_SCOPES:
        promotion_scope = "draft"

    explicit_sandbox = _clean(defaults.get("sandbox_id") or payload.get("sandbox_id") or meta.get("sandbox_id") or runtime.get("sandbox_id"))
    sandbox_id = explicit_sandbox or project_id or storyline_id or world_id or universe_id or source_record_id or "global-roleplay-sandbox"

    return {
        "project_id": project_id,
        "sandbox_id": sandbox_id,
        "universe_id": universe_id,
        "world_id": world_id,
        "region_id": region_id,
        "city_id": city_id,
        "location_id": location_id,
        "source_record_id": source_record_id,
        "source_record_kind": source_record_kind,
        "canon_snapshot_id": canon_snapshot_id,
        "storyline_id": storyline_id,
        "session_id": session_id,
        "branch_id": branch_id,
        "memory_scope": memory_scope,
        "promotion_scope": promotion_scope,
    }


def context_scope_id(context: dict[str, str], fallback: str = "") -> str:
    for key in ("sandbox_id", "world_id", "universe_id", "project_id", "storyline_id", "source_record_id"):
        value = _clean(context.get(key))
        if value:
            return value
    return _clean(fallback)


def context_json(context: dict[str, str]) -> str:
    payload = {key: _clean(context.get(key)) for key in SANDBOX_COLUMNS if key != "sandbox_json"}
    return _json(payload)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return bool(row)


def _existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def ensure_roleplay_sandbox_schema(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Add Phase 2 sandbox/snapshot/promotion columns without destroying old data."""
    ensure_roleplay_foundation(write_manifest=True)
    owns_connection = conn is None
    conn = conn or sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    added: dict[str, list[str]] = {}
    tables: dict[str, Any] = {}
    try:
        for table in TABLES_REQUIRING_SANDBOX_COLUMNS:
            if not _table_exists(conn, table):
                tables[table] = {"exists": False, "missing_columns": list(SANDBOX_COLUMNS)}
                continue
            existing = _existing_columns(conn, table)
            added[table] = []
            for column, ddl in SANDBOX_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    added[table].append(column)
            columns_after = _existing_columns(conn, table)
            missing = [column for column in SANDBOX_COLUMNS if column not in columns_after]
            tables[table] = {"exists": True, "added_columns": added[table], "missing_columns": missing}
        for table in TABLES_REQUIRING_SANDBOX_COLUMNS:
            if _table_exists(conn, table):
                for column in ("sandbox_id", "world_id", "project_id", "storyline_id", "session_id", "branch_id", "promotion_scope", "memory_scope"):
                    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()
    return {
        "schema_id": SANDBOX_CONTRACT_SCHEMA_ID,
        "contract_version": SANDBOX_CONTRACT_VERSION,
        "status": "ready" if all(not row.get("missing_columns") for row in tables.values() if row.get("exists")) else "partial",
        "checked_at": _now(),
        "tables": tables,
        "added_columns": {table: columns for table, columns in added.items() if columns},
    }


def sandbox_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    state = ensure_roleplay_sandbox_schema()
    contract = {
        "schema_id": SANDBOX_CONTRACT_SCHEMA_ID,
        "contract_version": SANDBOX_CONTRACT_VERSION,
        "phase": "Phase 2 — Define Proper Memory Sandboxing Contract",
        "generated_at": _now(),
        "authoritative_store": "SQLite remains source of truth; Chroma/vector stores are mirrors only.",
        "required_context_fields": [column for column in SANDBOX_COLUMNS if column != "sandbox_json"],
        "memory_scopes": list(MEMORY_SCOPES),
        "promotion_scopes": list(PROMOTION_SCOPES),
        "table_policy": {
            "tables_with_context_columns": list(TABLES_REQUIRING_SANDBOX_COLUMNS),
            "migration_mode": "additive_only",
            "legacy_scope_id": "kept for compatibility; sandbox_id becomes the preferred isolation key",
        },
        "routing_rules": {
            "roleplay_builder_records": "Use payload.links.scope first, then project/story/world ids, then source_record_id.",
            "novel_source_documents": "Use project_id/storyline_id/canon_snapshot_id when present; do not mix with unrelated roleplay sandboxes.",
            "scene_runtime": "Scene packets must query by sandbox_id/world_id/storyline_id before falling back to broad search.",
        },
        "schema_state": state,
        "paths": {
            "contract_path": _relative_to_root(SANDBOX_CONTRACT_PATH),
            "audit_path": _relative_to_root(SANDBOX_AUDIT_PATH),
            "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        },
        "next_required_phase": "Phase 3 — Builder Record Memory Compiler",
    }
    if write_report:
        SANDBOX_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SANDBOX_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        SANDBOX_AUDIT_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract


def apply_context_to_payload(payload: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    merged = dict(payload or {})
    existing = merged.get("sandbox_context") if isinstance(merged.get("sandbox_context"), dict) else {}
    merged["sandbox_context"] = {**existing, **context}
    return merged
