from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.forge import FORGE_KIND_DEFINITIONS, save_forge_record_payload, list_forge_records
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, ROOT_DIR, _relative_to_root, ensure_roleplay_foundation

_VALID_KINDS = {kind for kind, _, _ in FORGE_KIND_DEFINITIONS}
IMPORT_ROOT = ROLEPLAY_DATA_ROOT / "large_json_imports"
EXPORT_ROOT = ROLEPLAY_DATA_ROOT / "large_json_exports"
TEST_ROOT = ROLEPLAY_DATA_ROOT / "large_json_tests"
LOG_TABLE = "rp_large_json_io_runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_path(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_rel(path: Path) -> str:
    try:
        return _relative_to_root(path)
    except Exception:
        return str(path)


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_large_json_io_schema() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    IMPORT_ROOT.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
                run_id TEXT PRIMARY KEY,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_path TEXT,
                output_path TEXT,
                record_count INTEGER DEFAULT 0,
                written_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                compile_requested INTEGER DEFAULT 0,
                index_requested INTEGER DEFAULT 0,
                mirror_requested INTEGER DEFAULT 0,
                summary_json TEXT DEFAULT '{{}}'
            )
            """
        )
        conn.commit()
    return {
        "schema_id": "neo.roleplay.large_json_io.ensure_schema.v1",
        "status": "ready",
        "sqlite_path": _safe_rel(ROLEPLAY_SQLITE_PATH),
        "import_root": _safe_rel(IMPORT_ROOT),
        "export_root": _safe_rel(EXPORT_ROOT),
        "test_root": _safe_rel(TEST_ROOT),
        "table": LOG_TABLE,
    }


def _log_run(payload: dict[str, Any]) -> None:
    ensure_large_json_io_schema()
    now = _now()
    run_id = str(payload.get("run_id") or f"large-json-{uuid.uuid4().hex[:12]}")
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {LOG_TABLE} (
                run_id, run_type, status, created_at, updated_at, input_path, output_path,
                record_count, written_count, skipped_count, error_count, compile_requested,
                index_requested, mirror_requested, summary_json
            ) VALUES (?, ?, ?, COALESCE((SELECT created_at FROM {LOG_TABLE} WHERE run_id = ?), ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(payload.get("run_type") or "unknown"),
                str(payload.get("status") or "unknown"),
                run_id,
                now,
                now,
                str(payload.get("input_path") or ""),
                str(payload.get("output_path") or ""),
                int(payload.get("record_count") or 0),
                int(payload.get("written_count") or 0),
                int(payload.get("skipped_count") or 0),
                int(payload.get("error_count") or 0),
                1 if payload.get("compile_requested") else 0,
                1 if payload.get("index_requested") else 0,
                1 if payload.get("mirror_requested") else 0,
                json.dumps(payload.get("summary") or {}, ensure_ascii=False),
            ),
        )
        conn.commit()


def _candidate_records_from_value(value: Any) -> list[dict[str, Any]]:
    """Accept a single record, a list, or wrapper objects like all_records."""
    if isinstance(value, dict):
        if isinstance(value.get("records"), list):
            return [item for item in value["records"] if isinstance(item, dict)]
        if isinstance(value.get("items"), list):
            return [item for item in value["items"] if isinstance(item, dict)]
        if isinstance(value.get("all_records"), list):
            return [item for item in value["all_records"] if isinstance(item, dict)]
        # common export bundle shape: keyed by kind.
        collected: list[dict[str, Any]] = []
        for key, child in value.items():
            if str(key).lower() in _VALID_KINDS and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        item = deepcopy(item)
                        item.setdefault("kind", str(key).lower())
                        collected.append(item)
        if collected:
            return collected
        if str(value.get("kind") or "").lower() in _VALID_KINDS:
            return [value]
        if isinstance(value.get("payload"), dict) and str(value["payload"].get("kind") or value.get("kind") or "").lower() in _VALID_KINDS:
            return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _records_from_path(path: Path, *, recursive: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    if not path.exists():
        return [], [{"path": str(path), "error": "path_not_found"}]
    paths: list[Path]
    if path.is_dir():
        globber = path.rglob("*.json") if recursive else path.glob("*.json")
        paths = sorted(globber)
    else:
        paths = [path]
    for item_path in paths:
        try:
            parsed = _read_json_path(item_path)
            for record in _candidate_records_from_value(parsed):
                record = deepcopy(record)
                record.setdefault("_import_source_path", str(item_path))
                records.append(record)
        except Exception as exc:
            errors.append({"path": str(item_path), "error": str(exc)})
    return records, errors


def _normalize_import_record(record: dict[str, Any], *, repair_ids: bool = True) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    payload = deepcopy(record.get("payload") if isinstance(record.get("payload"), dict) else record)
    kind = str(payload.get("kind") or record.get("kind") or "").strip().lower()
    if kind not in _VALID_KINDS:
        return None, {"record": record.get("id") or record.get("record_id") or record.get("label") or "unknown", "error": f"unsupported_kind:{kind or 'missing'}"}
    label = str(payload.get("label") or payload.get("display_label") or record.get("title") or "").strip()
    if not label:
        label = f"Imported {kind.title()}"
        payload["label"] = label
        payload.setdefault("display_label", label)
    record_id = str(payload.get("id") or record.get("record_id") or "").strip()
    if not record_id and repair_ids:
        record_id = f"{kind}-{uuid.uuid4().hex[:10]}"
        payload["id"] = record_id
    if not record_id:
        return None, {"record": label, "kind": kind, "error": "missing_id"}
    payload["kind"] = kind
    payload["id"] = record_id
    payload.setdefault("meta", {})
    if isinstance(payload["meta"], dict):
        payload["meta"].setdefault("imported_at", _now())
        payload["meta"].setdefault("import_source_path", record.get("_import_source_path", ""))
    return {
        "kind": kind,
        "record_id": record_id,
        "title": label,
        "payload": payload,
        "tags": payload.get("tags") or record.get("tags") or [],
    }, None


def validate_large_json_records_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_large_json_io_schema()
    payload = payload or {}
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if payload.get("path"):
        path_records, path_errors = _records_from_path(Path(str(payload.get("path"))).expanduser(), recursive=bool(payload.get("recursive", True)))
        records.extend(path_records)
        errors.extend(path_errors)
    records.extend(_candidate_records_from_value(payload.get("records") if "records" in payload else payload.get("payload")))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    by_kind: dict[str, int] = {}
    for raw in records:
        item, error = _normalize_import_record(raw, repair_ids=bool(payload.get("repair_ids", True)))
        if error:
            errors.append(error)
            continue
        assert item is not None
        key = f"{item['kind']}:{item['record_id']}"
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        normalized.append({"kind": item["kind"], "record_id": item["record_id"], "title": item["title"]})
    return {
        "schema_id": "neo.roleplay.large_json_io.validate.v1",
        "status": "valid" if not errors else "valid_with_errors" if normalized else "invalid",
        "record_count": len(records),
        "valid_count": len(normalized),
        "error_count": len(errors),
        "duplicate_count": len(duplicates),
        "by_kind": by_kind,
        "duplicates": duplicates[:50],
        "records": normalized[:200],
        "errors": errors[:200],
    }


def _post_import_tasks(*, compile_after: bool, rebuild_search: bool, index_after: bool, mirror_after: bool) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    if compile_after:
        try:
            from neo_app.roleplay.builder_memory_compiler import compile_all_builder_records_memory_payload
            tasks["compile_builder_memory"] = compile_all_builder_records_memory_payload({"force": True})
        except Exception as exc:
            tasks["compile_builder_memory"] = {"status": "error", "error": str(exc)}
    if rebuild_search:
        try:
            from neo_app.roleplay.sqlite_upgrade import rebuild_roleplay_memory_search_payload
            tasks["rebuild_search"] = rebuild_roleplay_memory_search_payload({})
        except Exception as exc:
            tasks["rebuild_search"] = {"status": "error", "error": str(exc)}
    if index_after:
        try:
            from neo_app.roleplay.embedding_reranker_adapter import index_roleplay_search_documents_payload
            tasks["index_search_documents"] = index_roleplay_search_documents_payload({"force": True})
        except Exception as exc:
            tasks["index_search_documents"] = {"status": "error", "error": str(exc)}
    if mirror_after:
        try:
            from neo_app.roleplay.chroma_vector_mirror import mirror_roleplay_vectors_to_chroma_payload
            tasks["chroma_mirror"] = mirror_roleplay_vectors_to_chroma_payload({})
        except Exception as exc:
            tasks["chroma_mirror"] = {"status": "error", "error": str(exc)}
    return tasks


def import_large_json_records_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_large_json_io_schema()
    payload = payload or {}
    run_id = str(payload.get("run_id") or f"import-{uuid.uuid4().hex[:12]}")
    records: list[dict[str, Any]] = []
    read_errors: list[dict[str, Any]] = []
    input_path = ""
    if payload.get("path"):
        input_path = str(payload.get("path"))
        path_records, path_errors = _records_from_path(Path(input_path).expanduser(), recursive=bool(payload.get("recursive", True)))
        records.extend(path_records)
        read_errors.extend(path_errors)
    records.extend(_candidate_records_from_value(payload.get("records") if "records" in payload else payload.get("payload")))
    dry_run = bool(payload.get("dry_run", False))
    overwrite = bool(payload.get("overwrite", True))
    repair_ids = bool(payload.get("repair_ids", True))
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = list(read_errors)
    snapshot_path = IMPORT_ROOT / f"{run_id}.input_snapshot.json"
    _write_json(snapshot_path, {"run_id": run_id, "created_at": _now(), "input_path": input_path, "records": records})
    existing_keys = {f"{record.kind}:{record.record_id}" for record in list_forge_records(None)}
    for raw in records:
        item, error = _normalize_import_record(raw, repair_ids=repair_ids)
        if error:
            errors.append(error)
            continue
        assert item is not None
        key = f"{item['kind']}:{item['record_id']}"
        if key in existing_keys and not overwrite:
            skipped.append({"kind": item["kind"], "record_id": item["record_id"], "reason": "exists"})
            continue
        if dry_run:
            written.append({"kind": item["kind"], "record_id": item["record_id"], "title": item["title"], "dry_run": True})
            continue
        try:
            result = save_forge_record_payload(item)
            rec = result.get("record", {})
            written.append({"kind": rec.get("kind"), "record_id": rec.get("record_id"), "title": rec.get("title"), "storage_path": rec.get("storage_path"), "memory_sync": result.get("memory_sync", {})})
        except Exception as exc:
            errors.append({"kind": item.get("kind"), "record_id": item.get("record_id"), "error": str(exc)})
    tasks = {}
    if not dry_run:
        tasks = _post_import_tasks(
            compile_after=bool(payload.get("compile_after", False)),
            rebuild_search=bool(payload.get("rebuild_search", False)),
            index_after=bool(payload.get("index_after", False)),
            mirror_after=bool(payload.get("mirror_after", False)),
        )
    status = "imported" if written and not errors else "imported_with_errors" if written else "dry_run" if dry_run else "error"
    result = {
        "schema_id": "neo.roleplay.large_json_io.import.v1",
        "status": status,
        "run_id": run_id,
        "dry_run": dry_run,
        "input_snapshot": _safe_rel(snapshot_path),
        "record_count": len(records),
        "written_count": len(written),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "written": written[:500],
        "skipped": skipped[:200],
        "errors": errors[:200],
        "post_import_tasks": tasks,
    }
    _log_run({
        "run_id": run_id,
        "run_type": "import",
        "status": status,
        "input_path": input_path or _safe_rel(snapshot_path),
        "record_count": len(records),
        "written_count": len(written),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "compile_requested": bool(payload.get("compile_after")),
        "index_requested": bool(payload.get("index_after")),
        "mirror_requested": bool(payload.get("mirror_after")),
        "summary": result,
    })
    return result


def export_large_json_sandbox_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_large_json_io_schema()
    payload = payload or {}
    export_id = str(payload.get("export_id") or f"export-{uuid.uuid4().hex[:12]}")
    work_dir = EXPORT_ROOT / export_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    kinds = payload.get("kinds") if isinstance(payload.get("kinds"), list) else []
    kinds_set = {str(k).lower() for k in kinds if str(k).lower() in _VALID_KINDS}
    records = [record for record in list_forge_records(None) if not kinds_set or record.kind in kinds_set]
    if payload.get("record_ids"):
        ids = {str(x) for x in payload.get("record_ids") or []}
        records = [record for record in records if record.record_id in ids]
    exported_records: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}
    for record in records:
        rec_dict = {
            "record_id": record.record_id,
            "kind": record.kind,
            "title": record.title,
            "body": record.body,
            "tags": record.tags,
            "payload": record.payload,
            "markdown": record.markdown,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        target = work_dir / "records" / record.kind / f"{record.record_id}.json"
        _write_json(target, rec_dict)
        exported_records.append(rec_dict)
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
    bundle = {
        "schema_id": "neo.roleplay.large_json_bundle.v1",
        "export_id": export_id,
        "created_at": _now(),
        "record_count": len(exported_records),
        "by_kind": by_kind,
        "records": exported_records,
        "notes": "SQLite is authoritative; this bundle exports JSON records for portability/testing.",
    }
    _write_json(work_dir / "all_records.json", bundle)
    if payload.get("include_sqlite_snapshot", False) and ROLEPLAY_SQLITE_PATH.exists():
        shutil.copy2(ROLEPLAY_SQLITE_PATH, work_dir / "roleplay.sqlite.snapshot")
    archive_path = EXPORT_ROOT / f"{export_id}.zip"
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(work_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(work_dir).as_posix())
    result = {
        "schema_id": "neo.roleplay.large_json_io.export.v1",
        "status": "exported",
        "export_id": export_id,
        "record_count": len(exported_records),
        "by_kind": by_kind,
        "directory": _safe_rel(work_dir),
        "archive": _safe_rel(archive_path),
        "size_bytes": archive_path.stat().st_size if archive_path.exists() else 0,
    }
    _log_run({
        "run_id": export_id,
        "run_type": "export",
        "status": "exported",
        "output_path": _safe_rel(archive_path),
        "record_count": len(exported_records),
        "written_count": len(exported_records),
        "summary": result,
    })
    return result


def large_json_io_state_payload() -> dict[str, Any]:
    ensure_large_json_io_schema()
    rows: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {LOG_TABLE} ORDER BY updated_at DESC LIMIT 20").fetchall()]
    return {
        "schema_id": "neo.roleplay.large_json_io.state.v1",
        "status": "ready",
        "import_root": _safe_rel(IMPORT_ROOT),
        "export_root": _safe_rel(EXPORT_ROOT),
        "test_root": _safe_rel(TEST_ROOT),
        "recent_runs": rows,
        "recent_run_count": len(rows),
        "record_count": len(list_forge_records(None)),
    }


def large_json_io_contract_payload(write_report: bool = True) -> dict[str, Any]:
    contract = {
        "schema_id": "neo.roleplay.large_json_io.contract.v1",
        "status": "ready",
        "phase": "Phase 16 — Import / Export / Large JSON Testing",
        "goals": [
            "Import single JSON records, all_records bundles, or folders of JSON records.",
            "Validate kind/id/link health before writing.",
            "Optionally compile, rebuild search, index vectors, and mirror to Chroma after import.",
            "Export Forge records as portable large JSON test bundles.",
        ],
        "endpoints": [
            "GET /api/roleplay/large-json-io/contract",
            "GET /api/roleplay/large-json-io/state",
            "POST /api/roleplay/large-json-io/ensure-schema",
            "POST /api/roleplay/large-json-io/validate",
            "POST /api/roleplay/large-json-io/import",
            "POST /api/roleplay/large-json-io/export",
        ],
        "source_of_truth": "SQLite remains source of truth; JSON bundles are import/export/testing artifacts.",
    }
    if write_report:
        _write_json(ROLEPLAY_DATA_ROOT / "large_json_io_contract.json", contract)
    return contract
