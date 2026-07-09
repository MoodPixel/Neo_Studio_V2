from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.forge import CANONICAL_TEMPLATE_KINDS, list_forge_records
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.large_json_io import validate_large_json_records_payload, import_large_json_records_payload

SCHEMA_ID: Final[str] = "neo.roleplay.phase17_5e.large_json_test_validation.v1"
PHASE: Final[str] = "Phase 17.5E — Large JSON Test Pack Validation"
RUN_TABLE: Final[str] = "rp_large_json_validation_runs"
REPORT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "large_json_test_validation_state.json"
CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "large_json_test_validation_contract.json"
SYSTEM_MEMORY_DOC: Final[Path] = Path("neo_system_records/05_MEMORY_SYSTEM/ROLEPLAY_PHASE_17_5E_LARGE_JSON_TEST_VALIDATION.md")
SYSTEM_SURFACE_DOC: Final[Path] = Path("neo_system_records/06_SURFACES/roleplay/PHASE_17_5E_LARGE_JSON_TEST_VALIDATION.md")


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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _count_by(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    if not _table_exists(conn, table) or column not in _columns(conn, table):
        return {}
    rows = conn.execute(
        f"SELECT COALESCE({column}, '') AS key, COUNT(*) AS count FROM {table} GROUP BY COALESCE({column}, '')"
    ).fetchall()
    return {str(row["key"] or "unknown"): int(row["count"] or 0) for row in rows}


def ensure_large_json_test_validation_schema() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_path TEXT DEFAULT '',
                expected_kind_count INTEGER DEFAULT 0,
                detected_kind_count INTEGER DEFAULT 0,
                record_count INTEGER DEFAULT 0,
                imported_count INTEGER DEFAULT 0,
                compiled_fragment_count INTEGER DEFAULT 0,
                search_document_count INTEGER DEFAULT 0,
                vector_count INTEGER DEFAULT 0,
                missing_kinds_json TEXT DEFAULT '[]',
                failed_checks_json TEXT DEFAULT '[]',
                summary_json TEXT DEFAULT '{{}}'
            )
            """
        )
        conn.commit()
    return {
        "schema_id": f"{SCHEMA_ID}.ensure_schema",
        "status": "ready",
        "sqlite_path": _safe_rel(ROLEPLAY_SQLITE_PATH),
        "table": RUN_TABLE,
    }


def _log_run(summary: dict[str, Any]) -> None:
    ensure_large_json_test_validation_schema()
    now = _now()
    run_id = str(summary.get("run_id") or f"large-json-validation-{uuid.uuid4().hex[:12]}")
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {RUN_TABLE} (
                run_id, status, created_at, updated_at, input_path, expected_kind_count,
                detected_kind_count, record_count, imported_count, compiled_fragment_count,
                search_document_count, vector_count, missing_kinds_json, failed_checks_json, summary_json
            ) VALUES (?, ?, COALESCE((SELECT created_at FROM {RUN_TABLE} WHERE run_id = ?), ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(summary.get("status") or "unknown"),
                run_id,
                now,
                now,
                str(summary.get("input_path") or ""),
                int(summary.get("expected_kind_count") or 0),
                int(summary.get("detected_kind_count") or 0),
                int(summary.get("record_count") or 0),
                int(summary.get("imported_count") or 0),
                int(summary.get("compiled_fragment_count") or 0),
                int(summary.get("search_document_count") or 0),
                int(summary.get("vector_count") or 0),
                json.dumps(summary.get("missing_kinds") or [], ensure_ascii=False),
                json.dumps(summary.get("failed_checks") or [], ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        conn.commit()


def _current_store_counts() -> dict[str, Any]:
    records = list_forge_records(None)
    record_counts: dict[str, int] = {}
    for rec in records:
        record_counts[rec.kind] = record_counts.get(rec.kind, 0) + 1
    with _connect() as conn:
        fragment_counts = _count_by(conn, "rp_memory_fragments", "source_record_kind")
        search_counts = _count_by(conn, "rp_memory_search_documents", "source_record_kind")
        vector_counts = _count_by(conn, "rp_vector_index", "source_record_kind")
        if not vector_counts:
            vector_counts = _count_by(conn, "rp_vector_index", "source_table")
        proof_counts = _count_by(conn, "rp_retrieval_label_proofs", "source_record_kind")
    return {
        "records_by_kind": record_counts,
        "fragments_by_kind": fragment_counts,
        "search_documents_by_kind": search_counts,
        "vectors_by_kind": vector_counts,
        "retrieval_label_proofs_by_kind": proof_counts,
        "record_count": sum(record_counts.values()),
        "compiled_fragment_count": sum(fragment_counts.values()),
        "search_document_count": sum(search_counts.values()),
        "vector_count": sum(vector_counts.values()),
    }


def _coverage(expected_kinds: list[str], counts: dict[str, int]) -> dict[str, Any]:
    detected = sorted(k for k in expected_kinds if int(counts.get(k, 0)) > 0)
    missing = sorted(k for k in expected_kinds if int(counts.get(k, 0)) <= 0)
    return {
        "expected": expected_kinds,
        "detected": detected,
        "missing": missing,
        "detected_count": len(detected),
        "missing_count": len(missing),
        "ready": not missing,
    }


def large_json_test_validation_contract_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_large_json_test_validation_schema()
    contract = {
        "schema_id": f"{SCHEMA_ID}.contract",
        "status": "ready",
        "phase": PHASE,
        "goal": "Validate that large Forge JSON packs can be imported, compiled, searched, indexed, and proven across all first-class Builder kinds.",
        "canonical_kinds": list(CANONICAL_TEMPLATE_KINDS),
        "checks": [
            "Input pack validates as Forge-compatible JSON.",
            "All canonical kinds are present, unless an explicit expected_kinds list is supplied.",
            "Records exist in Forge storage after import/current-store validation.",
            "Compiled memory fragments exist per kind after compile.",
            "Search documents exist after rebuild-search.",
            "Vector index rows exist after indexing when requested.",
            "Retrieval label proof table is available for Phase 17.5D debug proof.",
        ],
        "endpoints": [
            "GET /api/roleplay/large-json-test-validation/contract",
            "GET /api/roleplay/large-json-test-validation/state",
            "POST /api/roleplay/large-json-test-validation/ensure-schema",
            "POST /api/roleplay/large-json-test-validation/run",
        ],
        "source_of_truth": "SQLite and Forge storage are the test targets; JSON files are portable fixtures only.",
    }
    if write_report:
        _write_json(CONTRACT_PATH, contract)
        _write_doc(
            SYSTEM_MEMORY_DOC,
            """
            # Roleplay Phase 17.5E — Large JSON Test Pack Validation

            Validates the first-class Builder memory pipeline for all canonical record kinds.

            The validation lane checks JSON input, Forge save/import, Builder memory compilation, search document rebuild, vector indexing, and retrieval label proof readiness.

            SQLite remains authoritative. JSON packs are fixtures for repeatable regression testing.
            """,
        )
        _write_doc(
            SYSTEM_SURFACE_DOC,
            """
            # Roleplay Surface — Phase 17.5E Large JSON Test Pack Validation

            Adds API-level validation for large JSON test packs across Universe, World, Region, City, Location, Character, Organization, Artifact, Ritual, Cycle, Creature, Legend, Scenario, and Relationship records.

            Use this before Phase 18 regression tests to prove that imported data reaches memory, search, vector, and debug-proof layers.
            """,
        )
    return contract


def large_json_test_validation_state_payload() -> dict[str, Any]:
    ensure_large_json_test_validation_schema()
    counts = _current_store_counts()
    rows: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {RUN_TABLE} ORDER BY updated_at DESC LIMIT 20").fetchall()]
    payload = {
        "schema_id": f"{SCHEMA_ID}.state",
        "status": "ready",
        "canonical_kinds": list(CANONICAL_TEMPLATE_KINDS),
        "coverage": {
            "forge_records": _coverage(list(CANONICAL_TEMPLATE_KINDS), counts["records_by_kind"]),
            "compiled_fragments": _coverage(list(CANONICAL_TEMPLATE_KINDS), counts["fragments_by_kind"]),
            "search_documents": _coverage(list(CANONICAL_TEMPLATE_KINDS), counts["search_documents_by_kind"]),
        },
        "counts": counts,
        "recent_runs": rows,
    }
    _write_json(REPORT_PATH, payload)
    return payload


def _run_optional_tasks(options: dict[str, Any]) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    if options.get("compile_after", True):
        try:
            from neo_app.roleplay.builder_memory_compiler import compile_all_builder_records_memory_payload
            tasks["compile_builder_memory"] = compile_all_builder_records_memory_payload({"force": True})
        except Exception as exc:
            tasks["compile_builder_memory"] = {"status": "error", "error": str(exc)}
    if options.get("rebuild_search", True):
        try:
            from neo_app.roleplay.sqlite_upgrade import rebuild_roleplay_memory_search_payload
            tasks["rebuild_search"] = rebuild_roleplay_memory_search_payload({})
        except Exception as exc:
            tasks["rebuild_search"] = {"status": "error", "error": str(exc)}
    if options.get("index_after", False):
        try:
            from neo_app.roleplay.embedding_reranker_adapter import index_roleplay_search_documents_payload
            tasks["index_search_documents"] = index_roleplay_search_documents_payload({"force": True})
        except Exception as exc:
            tasks["index_search_documents"] = {"status": "error", "error": str(exc)}
    if options.get("mirror_after", False):
        try:
            from neo_app.roleplay.chroma_vector_mirror import mirror_roleplay_vectors_to_chroma_payload
            tasks["chroma_mirror"] = mirror_roleplay_vectors_to_chroma_payload({})
        except Exception as exc:
            tasks["chroma_mirror"] = {"status": "error", "error": str(exc)}
    return tasks


def run_large_json_test_validation_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_large_json_test_validation_schema()
    payload = payload or {}
    run_id = str(payload.get("run_id") or f"phase17-5e-{uuid.uuid4().hex[:12]}")
    expected_kinds = [str(k).strip().lower() for k in (payload.get("expected_kinds") or list(CANONICAL_TEMPLATE_KINDS)) if str(k).strip()]
    expected_kinds = [k for k in expected_kinds if k in set(CANONICAL_TEMPLATE_KINDS)] or list(CANONICAL_TEMPLATE_KINDS)
    input_path = str(payload.get("path") or "")
    import_result: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    tasks: dict[str, Any] = {}

    if input_path or payload.get("records") or payload.get("payload"):
        validation = validate_large_json_records_payload({
            "path": input_path,
            "records": payload.get("records"),
            "payload": payload.get("payload"),
            "recursive": bool(payload.get("recursive", True)),
            "repair_ids": bool(payload.get("repair_ids", True)),
        })
        if payload.get("import_pack", True) and validation.get("valid_count", 0) > 0:
            import_result = import_large_json_records_payload({
                "run_id": f"{run_id}-import",
                "path": input_path,
                "records": payload.get("records"),
                "payload": payload.get("payload"),
                "recursive": bool(payload.get("recursive", True)),
                "dry_run": bool(payload.get("dry_run", False)),
                "overwrite": bool(payload.get("overwrite", True)),
                "repair_ids": bool(payload.get("repair_ids", True)),
                "compile_after": False,
                "rebuild_search": False,
                "index_after": False,
                "mirror_after": False,
            })
    if not payload.get("dry_run", False):
        tasks = _run_optional_tasks(payload)

    counts = _current_store_counts()
    forge_cov = _coverage(expected_kinds, counts["records_by_kind"])
    fragment_cov = _coverage(expected_kinds, counts["fragments_by_kind"])
    search_cov = _coverage(expected_kinds, counts["search_documents_by_kind"])
    vector_cov = _coverage(expected_kinds, counts["vectors_by_kind"])

    failed_checks: list[str] = []
    if not forge_cov["ready"]:
        failed_checks.append("missing_forge_records")
    if payload.get("require_compiled_fragments", True) and not fragment_cov["ready"]:
        failed_checks.append("missing_compiled_fragments")
    if payload.get("require_search_documents", True) and not search_cov["ready"]:
        failed_checks.append("missing_search_documents")
    if payload.get("require_vectors", bool(payload.get("index_after", False))) and not vector_cov["ready"]:
        failed_checks.append("missing_vectors")
    if validation and validation.get("status") == "invalid":
        failed_checks.append("invalid_input_pack")

    status = "passed" if not failed_checks else "passed_with_gaps" if counts["record_count"] else "failed"
    summary = {
        "schema_id": f"{SCHEMA_ID}.run",
        "status": status,
        "run_id": run_id,
        "input_path": input_path,
        "expected_kinds": expected_kinds,
        "expected_kind_count": len(expected_kinds),
        "detected_kind_count": forge_cov["detected_count"],
        "record_count": counts["record_count"],
        "imported_count": (import_result or {}).get("written_count", 0),
        "compiled_fragment_count": counts["compiled_fragment_count"],
        "search_document_count": counts["search_document_count"],
        "vector_count": counts["vector_count"],
        "missing_kinds": {
            "forge_records": forge_cov["missing"],
            "compiled_fragments": fragment_cov["missing"],
            "search_documents": search_cov["missing"],
            "vectors": vector_cov["missing"],
        },
        "failed_checks": failed_checks,
        "coverage": {
            "forge_records": forge_cov,
            "compiled_fragments": fragment_cov,
            "search_documents": search_cov,
            "vectors": vector_cov,
        },
        "validation": validation,
        "import_result": import_result,
        "tasks": tasks,
        "counts": counts,
        "next_step": "Phase 18 regression tests" if status == "passed" else "Fix missing kinds or rerun with compile/rebuild/index enabled.",
    }
    _write_json(ROLEPLAY_DATA_ROOT / f"large_json_test_validation_{run_id}.json", summary)
    _log_run(summary)
    return summary
