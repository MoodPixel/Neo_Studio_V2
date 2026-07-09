from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

SCHEMA_ID: Final[str] = "neo.roleplay.phase18.regression_tests.v1"
PHASE: Final[str] = "Phase 18 — Regression Tests"
RUN_TABLE: Final[str] = "rp_regression_test_runs"
REPORT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "regression_tests_state.json"
CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "regression_tests_contract.json"
SYSTEM_MEMORY_DOC: Final[Path] = Path("neo_system_records/05_MEMORY_SYSTEM/ROLEPLAY_PHASE_18_REGRESSION_TESTS.md")
SYSTEM_SURFACE_DOC: Final[Path] = Path("neo_system_records/06_SURFACES/roleplay/PHASE_18_REGRESSION_TESTS.md")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rel(path: Path) -> str:
    try:
        return _relative_to_root(path)
    except Exception:
        return str(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_doc(path: Path, text: str) -> None:
    target = Path(path)
    if not target.is_absolute():
        target = ROLEPLAY_DATA_ROOT.parent.parent / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.strip() + "\n", encoding="utf-8")


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"] or 0)
    except Exception:
        return 0


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _test(name: str, status: str, details: str = "", **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "details": details, **extra}


def ensure_roleplay_regression_tests_schema() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                total_tests INTEGER DEFAULT 0,
                passed_tests INTEGER DEFAULT 0,
                warning_tests INTEGER DEFAULT 0,
                failed_tests INTEGER DEFAULT 0,
                summary_json TEXT DEFAULT '{{}}'
            )
            """
        )
        conn.commit()
    return {"schema_id": f"{SCHEMA_ID}.ensure_schema", "status": "ready", "table": RUN_TABLE, "sqlite_path": _safe_rel(ROLEPLAY_SQLITE_PATH)}


def _log_run(summary: dict[str, Any]) -> None:
    ensure_roleplay_regression_tests_schema()
    run_id = str(summary.get("run_id") or f"regression-{uuid.uuid4().hex[:12]}")
    now = _now()
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {RUN_TABLE} (
                run_id, status, created_at, updated_at, total_tests, passed_tests, warning_tests, failed_tests, summary_json
            ) VALUES (?, ?, COALESCE((SELECT created_at FROM {RUN_TABLE} WHERE run_id = ?), ?), ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(summary.get("status") or "unknown"),
                run_id,
                now,
                now,
                int(summary.get("total_tests") or 0),
                int(summary.get("passed_tests") or 0),
                int(summary.get("warning_tests") or 0),
                int(summary.get("failed_tests") or 0),
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        conn.commit()


def _latest_runs(limit: int = 8) -> list[dict[str, Any]]:
    ensure_roleplay_regression_tests_schema()
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT run_id, status, created_at, updated_at, total_tests, passed_tests, warning_tests, failed_tests FROM {RUN_TABLE} ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def _write_contract_docs(contract: dict[str, Any]) -> None:
    _write_json(CONTRACT_PATH, contract)
    doc = f"""
# {PHASE}

Schema: `{SCHEMA_ID}`

Purpose: provide a repeatable regression suite for the V2 Roleplay / Novel memory pipeline.

The suite checks templates, compiler profiles, sandboxing, SQLite memory tables, large JSON import readiness, search documents, vector index readiness, runtime retrieval traces, scene packets, injection/writeback/checkpoint proof tables, Chroma mirror safety, and provenance/debug proof.

SQLite remains the source of truth. Semantic/vector stores are mirrors or accelerators only.
"""
    _write_doc(SYSTEM_MEMORY_DOC, doc)
    _write_doc(SYSTEM_SURFACE_DOC, doc)


def roleplay_regression_tests_contract_payload(write_report: bool = True) -> dict[str, Any]:
    contract = {
        "schema_id": SCHEMA_ID,
        "phase": PHASE,
        "status": "ready",
        "endpoints": [
            "GET /api/roleplay/regression-tests/contract",
            "GET /api/roleplay/regression-tests/state",
            "POST /api/roleplay/regression-tests/ensure-schema",
            "POST /api/roleplay/regression-tests/run",
        ],
        "test_groups": [
            "builder_templates",
            "compiler_profiles",
            "sandbox_contract",
            "sqlite_memory_store",
            "large_json_pipeline",
            "embedding_vector_index",
            "runtime_retrieval",
            "retrieval_labels_debug_proof",
            "scene_packet_categories",
            "scene_memory_injection",
            "turn_writeback_continuity",
            "checkpoint_restore",
            "provenance_debug",
            "chroma_mirror_safety",
        ],
        "statuses": ["passed", "passed_with_warnings", "failed"],
        "run_table": RUN_TABLE,
        "sqlite_path": _safe_rel(ROLEPLAY_SQLITE_PATH),
    }
    if write_report:
        ensure_roleplay_regression_tests_schema()
        _write_contract_docs(contract)
    return contract


def roleplay_regression_tests_state_payload() -> dict[str, Any]:
    ensure_roleplay_regression_tests_schema()
    with _connect() as conn:
        counts = {
            "forge_records": _count(conn, "rp_entities"),
            "memory_fragments": _count(conn, "rp_memory_fragments"),
            "search_documents": _count(conn, "rp_memory_search_documents"),
            "vector_index": _count(conn, "rp_vector_index"),
            "retrieval_traces": _count(conn, "rp_retrieval_traces"),
            "scene_packets": _count(conn, "rp_scene_memory_packets"),
            "turn_writebacks": _count(conn, "rp_turn_writebacks"),
            "checkpoint_snapshots": _count(conn, "rp_story_checkpoint_snapshots"),
            "debug_events": _count(conn, "rp_debug_events"),
        }
    state = {"schema_id": f"{SCHEMA_ID}.state", "status": "ready", "counts": counts, "recent_runs": _latest_runs()}
    _write_json(REPORT_PATH, state)
    return state


def _run_tests(payload: dict[str, Any]) -> list[dict[str, Any]]:
    require_vectors = bool(payload.get("require_vectors", False))
    require_scene_packet = bool(payload.get("require_scene_packet", False))
    require_writeback = bool(payload.get("require_writeback", False))
    include_chroma = bool(payload.get("include_chroma", False))
    tests: list[dict[str, Any]] = []

    root = ROLEPLAY_DATA_ROOT.parent.parent
    roleplay_dir = root / "neo_app" / "roleplay"
    template_dir = roleplay_dir / "builder_templates"

    canonical_kinds = ["universe", "world", "region", "city", "location", "character", "organization", "artifact", "ritual", "cycle", "creature", "legend", "scenario", "relationship"]
    # File/module checks are intentionally warnings if missing; runtime DB checks below are authoritative after installation.
    missing_modules = [m for m in [
        "builder_kind_templates.py", "builder_compiler_profiles.py", "sandbox_contract.py", "builder_memory_compiler.py",
        "sqlite_upgrade.py", "embedding_reranker_adapter.py", "runtime_retrieval_lane.py", "scene_packet_builder.py",
        "scene_memory_injection.py", "turn_writeback.py", "story_checkpoint_restore.py", "provenance_debug_ui.py",
        "large_json_io.py", "large_json_test_validation.py",
    ] if not (roleplay_dir / m).exists()]
    tests.append(_test("phase_modules_present", "passed" if not missing_modules else "failed", f"Missing modules: {missing_modules}" if missing_modules else "All phase modules present.", missing_modules=missing_modules))

    with _connect() as conn:
        # Template state.
        missing_templates = []
        for kind in canonical_kinds:
            json_template = template_dir / "json" / f"{kind}.template.json"
            md_template = template_dir / "md" / f"{kind}.template.md"
            if not json_template.exists():
                missing_templates.append(f"json:{kind}")
            if not md_template.exists():
                missing_templates.append(f"md:{kind}")
        tests.append(_test("first_class_builder_templates", "passed" if not missing_templates else "warning", f"Missing template files: {missing_templates}" if missing_templates else "All first-class templates detected.", missing_templates=missing_templates))

        # Critical tables.
        required_tables = [
            "rp_entities", "rp_memory_fragments", "rp_entity_versions", "rp_edges", "rp_memory_search_documents",
            "rp_vector_index", "rp_retrieval_traces", "rp_scene_memory_packets", "rp_turn_summaries",
            "rp_turn_writebacks", "rp_continuity_rows", "rp_story_checkpoint_snapshots", "rp_debug_events",
            "rp_large_json_validation_runs", "rp_retrieval_label_proofs",
        ]
        missing_tables = [t for t in required_tables if not _table_exists(conn, t)]
        tests.append(_test("sqlite_memory_tables", "passed" if not missing_tables else "failed", f"Missing tables: {missing_tables}" if missing_tables else "Required SQLite tables exist.", missing_tables=missing_tables))

        # Sandbox columns on main tables.
        sandbox_cols = {"sandbox_id", "source_record_id", "source_record_kind", "memory_scope", "promotion_scope", "sandbox_json"}
        sandbox_missing: dict[str, list[str]] = {}
        for table in ["rp_entities", "rp_memory_fragments", "rp_vector_index", "rp_retrieval_traces", "rp_scene_memory_packets"]:
            cols = _columns(conn, table)
            missing = sorted(sandbox_cols - cols)
            if missing:
                sandbox_missing[table] = missing
        tests.append(_test("sandbox_columns", "passed" if not sandbox_missing else "failed", "Sandbox columns are present." if not sandbox_missing else "Some tables lack sandbox columns.", missing=sandbox_missing))

        counts = {
            "entities": _count(conn, "rp_entities"),
            "memory_fragments": _count(conn, "rp_memory_fragments"),
            "search_documents": _count(conn, "rp_memory_search_documents"),
            "vectors": _count(conn, "rp_vector_index"),
            "retrieval_traces": _count(conn, "rp_retrieval_traces"),
            "retrieval_label_proofs": _count(conn, "rp_retrieval_label_proofs"),
            "scene_packets": _count(conn, "rp_scene_memory_packets"),
            "turn_writebacks": _count(conn, "rp_turn_writebacks"),
            "continuity_rows": _count(conn, "rp_continuity_rows"),
            "checkpoint_snapshots": _count(conn, "rp_story_checkpoint_snapshots"),
            "debug_events": _count(conn, "rp_debug_events"),
            "chroma_log": _count(conn, "rp_chroma_mirror_log"),
        }
        tests.append(_test("builder_records_exist", "passed" if counts["entities"] > 0 else "warning", "Forge entities found." if counts["entities"] > 0 else "No Forge entities yet. Import/save records before final scene testing.", count=counts["entities"]))
        tests.append(_test("compiled_memory_fragments_exist", "passed" if counts["memory_fragments"] > 0 else "warning", "Compiled memory fragments found." if counts["memory_fragments"] > 0 else "No compiled fragments yet. Run Compile Builder memory.", count=counts["memory_fragments"]))
        tests.append(_test("search_documents_exist", "passed" if counts["search_documents"] > 0 else "warning", "Search documents found." if counts["search_documents"] > 0 else "No search documents yet. Run rebuild search docs.", count=counts["search_documents"]))

        vector_status = "passed" if counts["vectors"] > 0 else ("failed" if require_vectors else "warning")
        tests.append(_test("vector_index_readiness", vector_status, "Vector rows found." if counts["vectors"] > 0 else "No vector rows yet. Run embedding-reranker index-search-documents.", count=counts["vectors"], required=require_vectors))

        retrieval_status = "passed" if counts["retrieval_traces"] > 0 else "warning"
        tests.append(_test("runtime_retrieval_trace_readiness", retrieval_status, "Retrieval traces found." if counts["retrieval_traces"] > 0 else "No retrieval traces yet. Run Runtime Retrieval once.", count=counts["retrieval_traces"]))

        label_status = "passed" if counts["retrieval_label_proofs"] > 0 else "warning"
        tests.append(_test("retrieval_label_debug_proof", label_status, "Retrieval label proof rows found." if counts["retrieval_label_proofs"] > 0 else "No retrieval label proof rows yet. Run Runtime Retrieval after labels are enabled.", count=counts["retrieval_label_proofs"]))

        packet_status = "passed" if counts["scene_packets"] > 0 else ("failed" if require_scene_packet else "warning")
        tests.append(_test("scene_packet_builder_readiness", packet_status, "Scene packets found." if counts["scene_packets"] > 0 else "No scene packets yet. Build a Scene Packet before Scene Chat testing.", count=counts["scene_packets"], required=require_scene_packet))

        writeback_status = "passed" if counts["turn_writebacks"] > 0 else ("failed" if require_writeback else "warning")
        tests.append(_test("turn_writeback_readiness", writeback_status, "Turn writebacks found." if counts["turn_writebacks"] > 0 else "No turn writebacks yet. Run at least one Scene Chat turn if required.", count=counts["turn_writebacks"], required=require_writeback))

        tests.append(_test("checkpoint_restore_readiness", "passed" if counts["checkpoint_snapshots"] > 0 else "warning", "Checkpoint snapshots found." if counts["checkpoint_snapshots"] > 0 else "No checkpoint snapshots yet. Save a checkpoint after Scene Chat setup.", count=counts["checkpoint_snapshots"]))
        tests.append(_test("provenance_debug_readiness", "passed" if counts["debug_events"] > 0 else "warning", "Debug events found." if counts["debug_events"] > 0 else "No debug events yet. Open Inspector / run debug dashboard.", count=counts["debug_events"]))

        if include_chroma:
            tests.append(_test("chroma_mirror_safety", "passed" if _table_exists(conn, "rp_chroma_mirror_log") else "warning", "Chroma mirror table exists." if _table_exists(conn, "rp_chroma_mirror_log") else "Chroma mirror table missing. Run chroma-mirror ensure-schema if Chroma mirror testing is needed.", count=counts["chroma_log"]))
        else:
            tests.append(_test("chroma_mirror_safety", "passed", "Chroma mirror not required for this run; SQLite/vector fallback is acceptable.", required=False))

    return tests


def run_roleplay_regression_tests_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    ensure_roleplay_regression_tests_schema()
    tests = _run_tests(payload)
    passed = sum(1 for t in tests if t.get("status") == "passed")
    warnings = sum(1 for t in tests if t.get("status") == "warning")
    failed = sum(1 for t in tests if t.get("status") == "failed")
    status = "failed" if failed else ("passed_with_warnings" if warnings else "passed")
    summary = {
        "schema_id": f"{SCHEMA_ID}.run",
        "phase": PHASE,
        "run_id": str(payload.get("run_id") or f"regression-{uuid.uuid4().hex[:12]}"),
        "status": status,
        "created_at": _now(),
        "total_tests": len(tests),
        "passed_tests": passed,
        "warning_tests": warnings,
        "failed_tests": failed,
        "tests": tests,
        "next_actions": _next_actions(tests),
        "payload": payload,
    }
    _log_run(summary)
    _write_json(REPORT_PATH, summary)
    return summary


def _next_actions(tests: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    by_name = {str(t.get("name")): t for t in tests}
    if by_name.get("sqlite_memory_tables", {}).get("status") == "failed":
        actions.append("Run the Phase 7 SQLite ensure-schema endpoint and restart Neo.")
    if by_name.get("sandbox_columns", {}).get("status") == "failed":
        actions.append("Run the Phase 2 sandbox ensure-schema endpoint before compiling memory.")
    if by_name.get("builder_records_exist", {}).get("status") != "passed":
        actions.append("Import or save Forge Builder records through Large JSON IO or Builder.")
    if by_name.get("compiled_memory_fragments_exist", {}).get("status") != "passed":
        actions.append("Run Compile Builder memory / Compile Source memory.")
    if by_name.get("search_documents_exist", {}).get("status") != "passed":
        actions.append("Run SQLite rebuild-search.")
    if by_name.get("vector_index_readiness", {}).get("status") != "passed":
        actions.append("Run Embedding + Reranker index-search-documents.")
    if by_name.get("runtime_retrieval_trace_readiness", {}).get("status") != "passed":
        actions.append("Run Runtime Retrieval once with a scene-relevant query.")
    if by_name.get("scene_packet_builder_readiness", {}).get("status") != "passed":
        actions.append("Build a Scene Packet from the Runtime tab.")
    if by_name.get("turn_writeback_readiness", {}).get("status") == "failed":
        actions.append("Run at least one Scene Chat turn before requiring writeback.")
    return actions


# Backward-friendly aliases if the caller expects shorter names.
def roleplay_regression_tests_ensure_schema_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return ensure_roleplay_regression_tests_schema()
