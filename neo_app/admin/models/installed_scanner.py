from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os

from .manifest_loader import load_folder_rules, load_model_catalog
from .model_paths import ROOT_DIR, load_model_paths
from .path_resolver import resolve_model_target

INSTALLED_SCAN_SCHEMA_ID = "neo.admin.models.installed_scan.v1"
INSTALLED_INDEX_PATH = ROOT_DIR / "neo_data" / "cache" / "model_installed_index.json"
INSTALLED_SCAN_VERSION = "0.3.0-phase3"
MAX_FILES_PER_TARGET = 2500
MAX_TOTAL_FILES = 10000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _normalize_path(path: str) -> str:
    return _clean(path).replace("\\", "/")


def _safe_relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return path.name


def _store_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _allowed_extensions(folder_rules: dict[str, Any], target_type: str) -> set[str]:
    raw = _as_dict(folder_rules.get("allowed_extensions")).get(target_type, [])
    return {str(item).lower().strip() for item in _as_list(raw) if str(item).strip()}


def _file_extension(path: Path) -> str:
    return path.suffix.lower()


def _scan_files(root: Path, allowed_extensions: set[str], *, max_files: int = MAX_FILES_PER_TARGET) -> tuple[list[dict[str, Any]], list[str], bool]:
    warnings: list[str] = []
    files: list[dict[str, Any]] = []
    truncated = False
    if not root.exists():
        return files, ["target_folder_missing"], truncated
    if not root.is_dir():
        return files, ["target_path_is_not_folder"], truncated

    try:
        walker = os.walk(root, followlinks=False)
        for directory, dirnames, filenames in walker:
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            current = Path(directory)
            for filename in filenames:
                if filename.startswith("."):
                    continue
                file_path = current / filename
                extension = _file_extension(file_path)
                if allowed_extensions and extension not in allowed_extensions:
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    warnings.append(f"could_not_stat:{filename}")
                    continue
                files.append({
                    "filename": filename,
                    "extension": extension,
                    "size_bytes": int(stat.st_size),
                    "relative_path": _safe_relative_to(file_path, root),
                    "path": str(file_path),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
                if len(files) >= max_files:
                    truncated = True
                    warnings.append("target_scan_truncated")
                    return files, warnings, truncated
    except OSError as exc:
        warnings.append(f"scan_error:{type(exc).__name__}")
    return files, warnings, truncated


def _catalog_records(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _as_list(catalog.get("records")) if isinstance(item, dict)]


def _record_target_type(record: dict[str, Any]) -> str:
    install = _as_dict(record.get("install"))
    return _clean(install.get("target_type") or record.get("model_type")).lower()


def _record_backends(record: dict[str, Any]) -> list[str]:
    install = _as_dict(record.get("install"))
    return [_clean(item).lower() for item in _as_list(install.get("backend_targets")) if _clean(item)]


def _record_expected_filenames(record: dict[str, Any]) -> list[str]:
    source = _as_dict(record.get("source"))
    filenames: list[str] = []
    filename = _clean(source.get("filename"))
    if filename:
        filenames.append(filename)
    for item in _as_list(source.get("filenames")):
        clean = _clean(item)
        if clean:
            filenames.append(clean)
    # Preserve order while deduping.
    seen: set[str] = set()
    result: list[str] = []
    for item in filenames:
        lower = item.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(item)
    return result


def _target_key(backend: str, target_type: str) -> str:
    return f"{backend}::{target_type}"


def _build_scan_targets(*, model_paths: dict[str, Any], folder_rules: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested: set[tuple[str, str]] = set()
    backend_rules = _as_dict(folder_rules.get("backends"))
    for backend_id, rules in backend_rules.items():
        for target_type in _as_dict(rules).keys():
            requested.add((_clean(backend_id).lower(), _clean(target_type).lower()))
    for record in records:
        target_type = _record_target_type(record)
        for backend_id in _record_backends(record):
            requested.add((backend_id, target_type))

    targets: list[dict[str, Any]] = []
    for backend_id, target_type in sorted(requested):
        if not backend_id or not target_type:
            continue
        resolved = resolve_model_target(
            backend_id=backend_id,
            target_type=target_type,
            model_paths=model_paths,
            folder_rules=folder_rules,
        )
        target_path = _clean(resolved.get("resolved_path"))
        targets.append({
            "key": _target_key(backend_id, target_type),
            "backend": backend_id,
            "target_type": target_type,
            "resolved_path": target_path,
            "ok": bool(resolved.get("ok")),
            "status": resolved.get("status"),
            "root_key": resolved.get("root_key"),
            "rule_subdir": resolved.get("rule_subdir"),
            "allowed_extensions": resolved.get("allowed_extensions") or sorted(_allowed_extensions(folder_rules, target_type)),
            "errors": list(resolved.get("errors") or []),
            "warnings": list(resolved.get("warnings") or []),
        })
    return targets


def _index_files_by_target(targets: list[dict[str, Any]], *, folder_rules: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str], bool]:
    files_by_target: dict[str, list[dict[str, Any]]] = {}
    detected_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    truncated = False
    total = 0
    for target in targets:
        key = str(target.get("key") or "")
        files_by_target[key] = []
        if not target.get("ok"):
            continue
        target_path = _clean(target.get("resolved_path"))
        if not target_path:
            continue
        allowed = {str(item).lower() for item in _as_list(target.get("allowed_extensions")) if str(item).strip()}
        if not allowed:
            allowed = _allowed_extensions(folder_rules, str(target.get("target_type") or ""))
        scanned, target_warnings, target_truncated = _scan_files(Path(target_path), allowed)
        for warning in target_warnings:
            target.setdefault("warnings", []).append(warning)
        if target_truncated:
            truncated = True
        for item in scanned:
            row = {
                **item,
                "backend": target.get("backend"),
                "target_type": target.get("target_type"),
                "target_key": key,
                "target_root": target_path,
            }
            files_by_target[key].append(row)
            detected_files.append(row)
            total += 1
            if total >= MAX_TOTAL_FILES:
                truncated = True
                warnings.append("total_scan_truncated")
                return files_by_target, detected_files, warnings, truncated
    return files_by_target, detected_files, warnings, truncated


def _catalog_install_status(record: dict[str, Any], files_by_target: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    record_id = _clean(record.get("id"))
    expected = _record_expected_filenames(record)
    target_type = _record_target_type(record)
    statuses: list[dict[str, Any]] = []
    for backend in _record_backends(record):
        key = _target_key(backend, target_type)
        files = files_by_target.get(key, [])
        by_name = {str(item.get("filename") or "").lower(): item for item in files}
        matched = [by_name[name.lower()] for name in expected if name.lower() in by_name]
        if expected:
            status = "installed" if len(matched) == len(expected) else "missing"
            reason = "expected_filename_match" if matched else "expected_filename_not_found"
        elif files:
            status = "local_candidates"
            reason = "manifest_has_no_exact_filename"
        else:
            status = "unknown"
            reason = "no_exact_filename_and_no_local_candidates"
        statuses.append({
            "backend": backend,
            "target_type": target_type,
            "target_key": key,
            "status": status,
            "reason": reason,
            "expected_filenames": expected,
            "matched_count": len(matched),
            "candidate_count": len(files),
            "matched_files": [
                {
                    "filename": item.get("filename"),
                    "relative_path": item.get("relative_path"),
                    "size_bytes": item.get("size_bytes"),
                    "path": item.get("path"),
                }
                for item in matched
            ],
            "candidate_preview": [
                {
                    "filename": item.get("filename"),
                    "relative_path": item.get("relative_path"),
                    "size_bytes": item.get("size_bytes"),
                    "path": item.get("path"),
                }
                for item in files[:10]
            ],
        })
    if any(item.get("status") == "installed" for item in statuses):
        overall = "installed"
    elif any(item.get("status") == "missing" for item in statuses):
        overall = "missing"
    elif any(item.get("status") == "local_candidates" for item in statuses):
        overall = "local_candidates"
    else:
        overall = "unknown"
    return {
        "catalog_id": record_id,
        "display_name": record.get("display_name"),
        "category": record.get("category"),
        "base_model": record.get("base_model"),
        "model_type": record.get("model_type"),
        "source_mode": record.get("source_mode"),
        "overall_status": overall,
        "backends": statuses,
    }


def scan_installed_models(
    *,
    model_paths: dict[str, Any] | None = None,
    folder_rules: dict[str, Any] | None = None,
    catalog: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Scan configured local model folders and compare them to the manifest.

    Phase 3 is local-only. It does not download, hash, upload, or call remote
    model sites. The optional persisted index is stored under neo_data/cache.
    """

    model_paths = model_paths or load_model_paths(create=False)
    folder_rules = folder_rules or load_folder_rules()
    catalog = catalog or load_model_catalog()
    records = _catalog_records(catalog)
    targets = _build_scan_targets(model_paths=model_paths, folder_rules=folder_rules, records=records)
    files_by_target, detected_files, warnings, truncated = _index_files_by_target(targets, folder_rules=folder_rules)
    catalog_status = [_catalog_install_status(record, files_by_target) for record in records]
    extension_counts = Counter(str(item.get("extension") or "unknown") for item in detected_files)
    target_counts = Counter(str(item.get("target_key") or "unknown") for item in detected_files)
    type_counts = Counter(str(item.get("target_type") or "unknown") for item in detected_files)
    payload = {
        "schema_id": INSTALLED_SCAN_SCHEMA_ID,
        "version": INSTALLED_SCAN_VERSION,
        "phase": "phase3_installed_scanner",
        "status": "ready",
        "ok": True,
        "scanned_at": _now(),
        "summary": {
            "target_count": len(targets),
            "target_ready_count": sum(1 for item in targets if item.get("ok")),
            "detected_file_count": len(detected_files),
            "catalog_record_count": len(records),
            "catalog_installed_count": sum(1 for item in catalog_status if item.get("overall_status") == "installed"),
            "catalog_missing_count": sum(1 for item in catalog_status if item.get("overall_status") == "missing"),
            "catalog_with_local_candidates_count": sum(1 for item in catalog_status if item.get("overall_status") == "local_candidates"),
            "extension_counts": dict(sorted(extension_counts.items())),
            "target_counts": dict(sorted(target_counts.items())),
            "model_type_counts": dict(sorted(type_counts.items())),
            "truncated": truncated,
        },
        "targets": targets,
        "detected_files": detected_files,
        "catalog_status": catalog_status,
        "warnings": warnings,
        "store": {
            "path": _store_path(INSTALLED_INDEX_PATH),
            "exists": INSTALLED_INDEX_PATH.exists(),
            "policy": "local_only_gitignored_neo_data_cache",
        },
        "capabilities": {
            "installed_scan": True,
            "remote_metadata": False,
            "downloads": False,
            "hashing": False,
            "folder_creation": False,
        },
        "privacy_policy": {
            "local_only": True,
            "remote_calls": False,
            "stores_scan_in_repo": False,
            "runtime_store": "neo_data/cache/model_installed_index.json",
        },
    }
    if persist:
        _write_json(INSTALLED_INDEX_PATH, payload)
        payload["store"]["exists"] = True
    return payload


def load_installed_index() -> dict[str, Any] | None:
    return _read_json(INSTALLED_INDEX_PATH)


def admin_installed_models_payload() -> dict[str, Any]:
    existing = load_installed_index()
    if existing:
        return {
            "schema_id": "neo.admin.models.installed_payload.v1",
            "status": "ready",
            "phase": "phase3_installed_scanner",
            "has_scan": True,
            "scan": existing,
            "store": {
                "path": _store_path(INSTALLED_INDEX_PATH),
                "exists": True,
                "policy": "local_only_gitignored_neo_data_cache",
            },
        }
    return {
        "schema_id": "neo.admin.models.installed_payload.v1",
        "status": "not_scanned",
        "phase": "phase3_installed_scanner",
        "has_scan": False,
        "scan": None,
        "store": {
            "path": _store_path(INSTALLED_INDEX_PATH),
            "exists": False,
            "policy": "local_only_gitignored_neo_data_cache",
        },
        "capabilities": {
            "installed_scan": True,
            "scan_endpoint": "/api/admin/models/scan-installed",
            "remote_metadata": False,
            "downloads": False,
        },
    }


def admin_scan_installed_models_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _as_dict(payload)
    persist = bool(payload.get("persist", True))
    return scan_installed_models(persist=persist)
