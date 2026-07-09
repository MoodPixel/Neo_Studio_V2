from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.storage import (
    ROLEPLAY_DATA_ROOT,
    ROLEPLAY_FOUNDATION_DIRECTORIES,
    ROLEPLAY_SQLITE_PATH,
    _relative_to_root,
    ensure_roleplay_foundation,
)
from neo_app.roleplay.sqlite_store import ROLEPLAY_MEMORY_TABLES, ensure_roleplay_memory_schema
from neo_app.roleplay.sandbox_contract import SANDBOX_COLUMNS, TABLES_REQUIRING_SANDBOX_COLUMNS, sandbox_contract_payload

SCHEMA_ID = "neo.roleplay.storage.audit.v1"
CONTRACT_SCHEMA_ID = "neo.roleplay.storage.contract.lock.v1"
CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "storage_contract.lock.json"
AUDIT_REPORT_PATH = ROLEPLAY_DATA_ROOT / "storage_audit_report.json"

EXPECTED_TABLES: dict[str, dict[str, Any]] = {
    "rp_entities": {
        "purpose": "Authoritative Forge/Library entity mirror for world, region, city, character, scenario, etc.",
        "required_columns": ["entity_id", "kind", "title", "status", "scope_id", "tags_json", "payload_json", "source_path", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_entity_versions": {
        "purpose": "Version snapshots of entity payloads.",
        "required_columns": ["version_id", "entity_id", "kind", "payload_json", "created_at", "note"],
        "status_if_present": "implemented",
    },
    "rp_edges": {
        "purpose": "Graph links between roleplay records.",
        "required_columns": ["edge_id", "source_id", "target_id", "relation_type", "source_kind", "target_kind", "weight", "payload_json", "created_at", "updated_at"],
        "status_if_present": "partial",
        "notes": "Table exists, but full reverse-link/materialized graph behavior is not V1-parity yet.",
    },
    "rp_memory_fragments": {
        "purpose": "Compiled memory fragments used by retrieval and scene packets.",
        "required_columns": ["fragment_id", "namespace", "source_type", "source_id", "memory_type", "status", "content", "tags_json", "payload_json", "vector_status", "created_at", "updated_at"],
        "status_if_present": "partial",
        "notes": "Storage exists, but current Builder compile is shallow and does not yet split full nested JSON into V1-grade fragments.",
    },
    "rp_shared_memories": {
        "purpose": "Reusable cross-scene shared memory rows.",
        "required_columns": ["memory_id", "namespace", "scope_id", "title", "content", "status", "payload_json", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_relationship_state": {
        "purpose": "Runtime relationship state between characters.",
        "required_columns": ["state_id", "character_a_id", "character_b_id", "relationship_type", "state_label", "payload_json", "updated_at"],
        "status_if_present": "partial",
        "notes": "Storage exists, but relationship Builder schema still needs its own V2 relationship shape.",
    },
    "rp_character_states": {
        "purpose": "Runtime character emotion/goals/boundaries state.",
        "required_columns": ["state_id", "character_id", "scope_id", "display_name", "current_emotion", "emotional_vector_json", "goals_json", "boundaries_json", "payload_json", "trust_level", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_character_knowledge": {
        "purpose": "Character-specific knowledge visibility and belief rows.",
        "required_columns": ["knowledge_id", "character_id", "scope_id", "subject_id", "knowledge_type", "content", "visibility", "canon_status", "payload_json", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_unresolved_threads": {
        "purpose": "Open hooks, unresolved plot threads, and active pressures.",
        "required_columns": ["thread_id", "scope_id", "scene_id", "title", "thread_type", "status", "priority", "content", "payload_json", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_scene_memory_packets": {
        "purpose": "Prepared scene context packets consumed by Scene Chat.",
        "required_columns": ["packet_id", "scene_id", "scope_id", "title", "emotional_tone", "relationship_state_json", "character_knowledge_json", "canon_locks_json", "unresolved_threads_json", "continuity_warnings_json", "payload_json", "created_at", "updated_at"],
        "status_if_present": "partial",
        "notes": "Packet table exists. Full provenance-rich packet builder still needs Phase 11.",
    },
    "rp_continuity_rows": {
        "purpose": "Pinned/suppressed/resolved continuity control rows.",
        "required_columns": ["row_id", "scope_id", "continuity_type", "title", "content", "source_id", "status", "created_at", "updated_at"],
        "status_if_present": "partial",
        "notes": "Basic controls exist. Recurrence/cooldown/source-trace controls need upgrade.",
    },
    "rp_retrieval_traces": {
        "purpose": "Retrieval query, engine snapshot, and result trace storage.",
        "required_columns": ["trace_id", "query", "scope_id", "result_count", "engine_snapshot_json", "results_json", "status", "created_at"],
        "status_if_present": "implemented",
    },
    "rp_turn_summaries": {
        "purpose": "Scene turn summaries and writeback seed rows.",
        "required_columns": ["summary_id", "scene_id", "turn_id", "summary", "payload_json", "created_at", "updated_at"],
        "status_if_present": "partial",
        "notes": "Writes summaries, but promotion/canon approval path is not complete.",
    },
    "rp_story_checkpoints": {
        "purpose": "Story/session checkpoint snapshots.",
        "required_columns": ["checkpoint_id", "storyline_id", "session_id", "title", "summary", "payload_json", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_vector_index": {
        "purpose": "Local SQLite vector index fallback for Roleplay retrieval.",
        "required_columns": ["index_id", "source_table", "source_id", "scope_id", "content", "embedding_json", "embedding_dimension", "model_id", "vector_status", "payload_json", "indexed_at"],
        "status_if_present": "partial",
        "notes": "Local hash embedding fallback exists. External embedding/Chroma bridge is not V1-parity yet.",
    },
    "rp_contradiction_reports": {
        "purpose": "Contradiction scan/report rows.",
        "required_columns": ["report_id", "rule_id", "severity", "status", "scope_id", "title", "summary", "source_a_table", "source_a_id", "source_b_table", "source_b_id", "evidence_json", "resolution_note", "confidence", "semantic_score", "lexical_score", "scoring_json", "resolution_suggestion", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
    "rp_checkpoint_branches": {
        "purpose": "Story checkpoint branching metadata.",
        "required_columns": ["branch_id", "source_checkpoint_id", "storyline_id", "session_id", "title", "status", "payload_json", "created_at", "updated_at"],
        "status_if_present": "implemented",
    },
}

EXPECTED_DIRECTORIES: tuple[str, ...] = ROLEPLAY_FOUNDATION_DIRECTORIES


def _required_columns_for(table_name: str) -> list[str]:
    base = list(EXPECTED_TABLES.get(table_name, {}).get("required_columns", []))
    if table_name in TABLES_REQUIRING_SANDBOX_COLUMNS:
        for column in SANDBOX_COLUMNS:
            if column not in base:
                base.append(column)
    return base

MISSING_V1_PARITY_ITEMS: list[dict[str, str]] = [
    {"item": "deep_builder_memory_compiler", "status": "missing", "notes": "V2 saves one shallow fragment per record; it does not yet walk every nested field into scoped fragments."},
    {"item": "full_memory_sandbox_contract", "status": "missing", "notes": "project_id/sandbox_id/canon_snapshot_id/storyline_id/session_id/branch_id/promotion_scope are not consistently present on every fragment."},
    {"item": "relationship_builder_schema", "status": "missing", "notes": "Relationship kind still aliases to character template in Forge."},
    {"item": "chroma_roleplay_write_path", "status": "deferred", "notes": "Chroma is mentioned as deferred in runtime/memory adapter; SQLite vector fallback is current working path."},
    {"item": "external_embedding_execution", "status": "partial", "notes": "Admin Engine bridge exists; local hash vector fallback is the reliable current path."},
    {"item": "external_reranker_execution", "status": "partial", "notes": "Admin Engine bridge exists; lexical rerank fallback is the reliable current path."},
    {"item": "promotion_workflow", "status": "missing", "notes": "draft/runtime/candidate_canon/canon/rejected promotion is not yet enforced across writebacks."},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _sqlite_schema() -> tuple[dict[str, Any], dict[str, int], list[str]]:
    ensure_roleplay_memory_schema()
    tables: dict[str, Any] = {}
    counts: dict[str, int] = {}
    errors: list[str] = []
    with sqlite3.connect(ROLEPLAY_SQLITE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        present = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table_name in sorted(set(EXPECTED_TABLES) | set(ROLEPLAY_MEMORY_TABLES)):
            if table_name not in present:
                tables[table_name] = {"exists": False, "columns": [], "missing_columns": EXPECTED_TABLES.get(table_name, {}).get("required_columns", [])}
                counts[table_name] = 0
                continue
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
            required = _required_columns_for(table_name)
            missing = [column for column in required if column not in columns]
            try:
                count = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
            except Exception as exc:
                count = 0
                errors.append(f"{table_name}: count failed: {exc}")
            spec = EXPECTED_TABLES.get(table_name, {})
            status = "missing" if not columns else ("partial" if missing else spec.get("status_if_present", "implemented"))
            tables[table_name] = {
                "exists": True,
                "status": status,
                "purpose": spec.get("purpose", "Roleplay table"),
                "columns": columns,
                "required_columns": required,
                "missing_columns": missing,
                "notes": spec.get("notes", ""),
            }
            counts[table_name] = count
    return tables, counts, errors


def _directory_report() -> list[dict[str, Any]]:
    ensure_roleplay_foundation(write_manifest=True)
    rows: list[dict[str, Any]] = []
    for name in EXPECTED_DIRECTORIES:
        path = ROLEPLAY_DATA_ROOT / name
        rows.append({
            "directory_id": name,
            "path": _relative_to_root(path),
            "exists": path.exists() and path.is_dir(),
            "file_count": len([p for p in path.rglob("*") if p.is_file()]) if path.exists() else 0,
        })
    return rows


def roleplay_storage_audit_payload(*, write_report: bool = False) -> dict[str, Any]:
    foundation = ensure_roleplay_foundation(write_manifest=True)
    sqlite_state = ensure_roleplay_memory_schema()
    tables, counts, errors = _sqlite_schema()
    directory_rows = _directory_report()
    missing_tables = [name for name, row in tables.items() if not row.get("exists")]
    partial_tables = [name for name, row in tables.items() if row.get("status") == "partial"]
    missing_dirs = [row["directory_id"] for row in directory_rows if not row.get("exists")]
    existing_lock = _read_json(CONTRACT_PATH, {})
    payload = {
        "schema_id": SCHEMA_ID,
        "status": "ready_with_gaps" if (partial_tables or MISSING_V1_PARITY_ITEMS) else "ready",
        "generated_at": _now(),
        "phase": "Phase 1 — Audit + Lock Current V2 Roleplay Storage",
        "foundation": foundation if isinstance(foundation, dict) else getattr(foundation, "model_dump", lambda: {})(),
        "paths": {
            "data_root": _relative_to_root(ROLEPLAY_DATA_ROOT),
            "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
            "contract_lock_path": _relative_to_root(CONTRACT_PATH),
            "audit_report_path": _relative_to_root(AUDIT_REPORT_PATH),
        },
        "directories": directory_rows,
        "sqlite": {
            "ready": bool(sqlite_state.get("ready", True)) if isinstance(sqlite_state, dict) else True,
            "schema_id": sqlite_state.get("schema_id") if isinstance(sqlite_state, dict) else "",
            "schema_version": sqlite_state.get("schema_version") if isinstance(sqlite_state, dict) else "",
            "tables": tables,
            "table_counts": counts,
            "missing_tables": missing_tables,
            "partial_tables": partial_tables,
            "errors": errors,
        },
        "capability_matrix": {
            "sqlite_authoritative_store": "implemented",
            "forge_record_json_storage": "implemented",
            "entity_mirror": "implemented",
            "shallow_memory_fragment_sync": "implemented",
            "deep_nested_builder_compile": "missing",
            "runtime_retrieval_trace_storage": "implemented",
            "sqlite_vector_index_fallback": "partial",
            "admin_engine_embedding_bridge": "partial",
            "admin_engine_reranker_bridge": "partial",
            "chroma_roleplay_mirror": "deferred",
            "scene_packet_table": "partial",
            "turn_writeback": "partial",
            "checkpoint_storage": "implemented",
            "memory_sandboxing": "implemented_phase2_contract",
        },
        "missing_v1_parity": MISSING_V1_PARITY_ITEMS,
        "lock": {
            "exists": bool(existing_lock),
            "locked_at": existing_lock.get("locked_at", "") if isinstance(existing_lock, dict) else "",
            "contract_version": existing_lock.get("contract_version", "") if isinstance(existing_lock, dict) else "",
        },
        "sandbox_contract": sandbox_contract_payload(write_report=False),
        "next_required_phase": "Phase 3 — Builder Record Memory Compiler",
    }
    if write_report:
        AUDIT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def lock_roleplay_storage_contract_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    audit = roleplay_storage_audit_payload(write_report=True)
    contract = {
        "schema_id": CONTRACT_SCHEMA_ID,
        "contract_version": "1.1.0-phase1-plus-phase2-sandbox-columns",
        "locked_at": _now(),
        "phase": "Phase 1 — Audit + Lock Current V2 Roleplay Storage",
        "mode": str(payload.get("mode") or "non_destructive_contract_lock"),
        "notes": "This lock documents the current V2 Roleplay storage contract. It does not freeze data writes; it freezes the expected paths/tables before Phase 2+ migrations.",
        "authoritative_store": "SQLite: neo_data/roleplay/roleplay.sqlite",
        "json_record_root": "neo_data/roleplay/entities",
        "directories": [row["directory_id"] for row in audit.get("directories", []) if row.get("exists")],
        "tables": {
            table_name: {
                "status": row.get("status"),
                "columns": row.get("columns", []),
                "missing_columns": row.get("missing_columns", []),
            }
            for table_name, row in (audit.get("sqlite", {}).get("tables", {}) or {}).items()
        },
        "capability_matrix": audit.get("capability_matrix", {}),
        "missing_v1_parity": audit.get("missing_v1_parity", []),
        "allowed_next_migrations": [
            "sandbox/snapshot/promotion metadata columns added in Phase 2",
            "add deep Builder compiler fragments",
            "add relationship schema",
            "add callback/recurrence/memory controls",
            "add Chroma mirror after SQLite remains source of truth",
        ],
    }
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    refreshed = roleplay_storage_audit_payload(write_report=True)
    return {
        "schema_id": "neo.roleplay.storage.lock.result.v1",
        "status": "locked",
        "contract": contract,
        "audit": refreshed,
    }
