from __future__ import annotations

import fnmatch
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[1]
SCHEMA_ID: Final[str] = "neo.release_hygiene.runtime_data_purge.v25_1"
ARCHIVE_SCHEMA_ID: Final[str] = "neo.release_hygiene.archive_audit.v25_1"

# These paths may exist on a developer machine, but they are never source assets.
# A clean release/export must exclude them every time.
RUNTIME_ONLY_ROOTS: Final[tuple[str, ...]] = (
    "neo_data",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
)

RUNTIME_ONLY_PREFIXES: Final[tuple[str, ...]] = (
    "neo_data/",
    "tests/.pytest_cache/",
    "neo_extensions/cache/",
)

RUNTIME_ONLY_DIR_NAMES: Final[tuple[str, ...]] = (
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "htmlcov",
)

LOCAL_ENV_FILES: Final[tuple[str, ...]] = (
    ".env",
)

RUNTIME_ONLY_FILE_PATTERNS: Final[tuple[str, ...]] = (
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.bak",
    "*.swp",
    "*.swo",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    ".coverage",
)

NEO_DATA_SENSITIVE_PREFIXES: Final[tuple[str, ...]] = (
    "neo_data/settings/secrets/",
    "neo_data/memory/",
    "neo_data/outputs/",
    "neo_data/logs/",
    "neo_data/vector_store/",
    "neo_data/user/",
)

SHIPPED_TEMPLATE_PATHS: Final[tuple[str, ...]] = (
    "neo_app/providers/backend_profiles.json",
    "neo_extensions/built_in/image.style_stack/resources/generation_styles.default.csv",
    "neo_extensions/built_in/image.wildcards/resources/default_wildcards.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_rel(path: Path | str, root_dir: Path | str | None = None) -> str:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    candidate = Path(path)
    try:
        rel = candidate.resolve().relative_to(root).as_posix()
    except Exception:
        rel = candidate.as_posix()
    # Preserve dotfile names such as `.gitignore`; only trim an explicit `./` prefix.
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/")


def _is_hidden_git_path(rel_path: str) -> bool:
    return rel_path == ".git" or rel_path.startswith(".git/")


def release_exclusion_reason(rel_path: str, *, is_dir: bool = False) -> str:
    """Return the release-exclusion reason for a repo-relative path, or ''."""

    rel = rel_path.replace("\\", "/").strip("/")
    if not rel:
        return ""
    name = rel.rsplit("/", 1)[-1]
    first = rel.split("/", 1)[0]

    if _is_hidden_git_path(rel):
        return "git_metadata"
    if first in RUNTIME_ONLY_ROOTS:
        return "runtime_only_root"
    if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in RUNTIME_ONLY_PREFIXES):
        return "runtime_only_prefix"
    if is_dir and name in RUNTIME_ONLY_DIR_NAMES:
        return "runtime_only_directory"
    if name in LOCAL_ENV_FILES:
        return "local_environment_file"
    if any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern) for pattern in RUNTIME_ONLY_FILE_PATTERNS):
        return "runtime_only_file_pattern"
    return ""


def is_release_excluded(rel_path: str, *, is_dir: bool = False) -> bool:
    return bool(release_exclusion_reason(rel_path, is_dir=is_dir))


def _iter_repo_paths(root_dir: Path) -> Iterable[tuple[Path, str, bool]]:
    for path in sorted(root_dir.rglob("*"), key=lambda item: item.as_posix()):
        rel = _norm_rel(path, root_dir)
        if _is_hidden_git_path(rel):
            continue
        yield path, rel, path.is_dir()


def runtime_data_hygiene_audit(root_dir: Path | str | None = None, *, max_findings: int = 100) -> dict[str, Any]:
    """Audit local-only runtime artifacts that must not be committed or shipped.

    Finding these paths on a developer machine is not fatal; it means the clean
    release builder must exclude them. A release archive audit is the final gate.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    findings: list[dict[str, Any]] = []
    sensitive_count = 0
    excluded_count = 0
    for path, rel, is_dir in _iter_repo_paths(root):
        reason = release_exclusion_reason(rel, is_dir=is_dir)
        if not reason:
            continue
        excluded_count += 1
        sensitive = any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in NEO_DATA_SENSITIVE_PREFIXES)
        if sensitive:
            sensitive_count += 1
        if len(findings) < max_findings:
            findings.append({
                "path": rel,
                "kind": "directory" if is_dir else "file",
                "reason": reason,
                "sensitive_runtime_area": sensitive,
                "size_bytes": path.stat().st_size if path.is_file() else 0,
            })

    status = "clean" if excluded_count == 0 else "runtime_artifacts_present"
    return {
        "ok": True,
        "schema_id": SCHEMA_ID,
        "phase": "V25.1",
        "status": status,
        "generated_at": _now(),
        "root": _norm_rel(root, root),
        "excluded_path_count": excluded_count,
        "sensitive_runtime_path_count": sensitive_count,
        "findings_truncated": excluded_count > len(findings),
        "findings": findings,
        "policy": {
            "neo_data_is_runtime_only": True,
            "neo_data_may_exist_locally": True,
            "release_exports_must_exclude_neo_data": True,
            "runtime_bootstrap_recreates_missing_neo_data": True,
            "shipped_defaults_must_live_outside_neo_data": True,
            "raw_api_keys_must_stay_inside_gitignored_neo_data": True,
        },
        "shipped_template_paths": list(SHIPPED_TEMPLATE_PATHS),
        "release_builder": "scripts/build_clean_release.py",
    }


def audit_release_archive(archive_path: Path | str) -> dict[str, Any]:
    """Verify that a produced release archive contains no runtime-only paths."""

    archive = Path(archive_path).resolve()
    blocked: list[dict[str, Any]] = []
    entries_checked = 0
    if not archive.exists():
        return {
            "ok": False,
            "schema_id": ARCHIVE_SCHEMA_ID,
            "phase": "V25.1",
            "status": "missing_archive",
            "archive_path": archive.as_posix(),
            "entries_checked": 0,
            "blocked_entry_count": 0,
            "blocked_entries": [],
        }

    with zipfile.ZipFile(archive, "r") as zf:
        for name in zf.namelist():
            rel = name.strip("/")
            if not rel:
                continue
            entries_checked += 1
            reason = release_exclusion_reason(rel, is_dir=name.endswith("/"))
            if reason:
                blocked.append({"path": rel, "reason": reason})

    status = "clean" if not blocked else "blocked"
    return {
        "ok": not blocked,
        "schema_id": ARCHIVE_SCHEMA_ID,
        "phase": "V25.1",
        "status": status,
        "archive_path": archive.as_posix(),
        "entries_checked": entries_checked,
        "blocked_entry_count": len(blocked),
        "blocked_entries": blocked[:100],
        "policy": {
            "neo_data_allowed_in_archive": False,
            "pycache_allowed_in_archive": False,
            "local_env_allowed_in_archive": False,
            "runtime_databases_allowed_in_archive": False,
        },
    }


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")


def build_clean_release_zip(
    output_path: Path | str,
    root_dir: Path | str | None = None,
    *,
    include_hygiene_manifest: bool = True,
) -> dict[str, Any]:
    """Build a source release zip while excluding all runtime-only artifacts."""

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    local_audit = runtime_data_hygiene_audit(root, max_findings=200)
    files_included = 0
    files_excluded = 0
    excluded_reasons: dict[str, int] = {}
    output_rel = ""
    try:
        output_rel = output.relative_to(root).as_posix()
    except Exception:
        output_rel = ""

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if include_hygiene_manifest:
            manifest = {
                "schema_id": "neo.release_hygiene.clean_release_manifest.v25_1",
                "phase": "V25.1",
                "created_at": _now(),
                "source_root": root.as_posix(),
                "policy": local_audit.get("policy", {}),
                "local_audit_summary": {
                    "status": local_audit.get("status"),
                    "excluded_path_count": local_audit.get("excluded_path_count"),
                    "sensitive_runtime_path_count": local_audit.get("sensitive_runtime_path_count"),
                },
            }
            zf.writestr("release_hygiene_manifest.json", _manifest_bytes(manifest))

        for path, rel, is_dir in _iter_repo_paths(root):
            if is_dir:
                continue
            if output_rel and rel == output_rel:
                files_excluded += 1
                excluded_reasons["release_output_archive"] = excluded_reasons.get("release_output_archive", 0) + 1
                continue
            if rel == "release_hygiene_manifest.json":
                files_excluded += 1
                excluded_reasons["local_generated_manifest"] = excluded_reasons.get("local_generated_manifest", 0) + 1
                continue
            reason = release_exclusion_reason(rel, is_dir=False)
            if reason:
                files_excluded += 1
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
                continue
            zf.write(path, rel)
            files_included += 1

    archive_audit = audit_release_archive(output)
    return {
        "ok": archive_audit.get("ok") is True,
        "schema_id": "neo.release_hygiene.clean_release_build.v25_1",
        "phase": "V25.1",
        "status": "ready" if archive_audit.get("ok") is True else "blocked",
        "archive_path": output.as_posix(),
        "files_included": files_included,
        "files_excluded": files_excluded,
        "excluded_reasons": excluded_reasons,
        "local_audit": local_audit,
        "archive_audit": archive_audit,
    }
