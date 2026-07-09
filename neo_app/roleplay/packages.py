from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROOT_DIR, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_store import ROLEPLAY_SQLITE_PATH, roleplay_sqlite_state_payload

PACKAGES_ROOT = ROLEPLAY_DATA_ROOT / "packages"
PACKAGE_EXPORTS_ROOT = ROLEPLAY_DATA_ROOT / "exports" / "packages"
PACKAGE_IMPORTS_ROOT = ROLEPLAY_DATA_ROOT / "imports" / "packages"
VECTOR_STORE_ROOT = ROOT_DIR / "neo_data" / "vector_store"

ROLEPLAY_PACKAGE_DIRS: tuple[str, ...] = (
    "entities",
    "source_documents",
    "drafts",
    "helper_outputs",
    "canon_records",
    "memory_fragments",
    "relationships",
    "shared_memories",
    "runtime_bundles",
    "projects",
    "retrieval",
    "storylines",
    "story_sessions",
    "story_checkpoints",
    "story_drafts",
    "story_snapshots",
    "story_branches",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    clean = "-".join(part for part in clean.split("-") if part)
    return clean[:72] or "roleplay-package"


def _ensure_package_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    PACKAGES_ROOT.mkdir(parents=True, exist_ok=True)
    PACKAGE_EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    PACKAGE_IMPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    for directory in ROLEPLAY_PACKAGE_DIRS:
        (ROLEPLAY_DATA_ROOT / directory).mkdir(parents=True, exist_ok=True)


def _safe_relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return None


def _count_files(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for item in root.rglob("*") if item.is_file())


def _json_file_count(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for item in root.rglob("*.json") if item.is_file())


def _recent_archives(limit: int = 12) -> list[dict[str, Any]]:
    _ensure_package_storage()
    archives = sorted(PACKAGE_EXPORTS_ROOT.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    rows: list[dict[str, Any]] = []
    for path in archives:
        try:
            size = path.stat().st_size
            created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            size = 0
            created_at = ""
        rows.append({
            "archive": path.name,
            "path": _relative_to_root(path),
            "size_bytes": size,
            "created_at": created_at,
            "download_url": f"/api/roleplay/package/export/download?archive={path.name}",
        })
    return rows


def _recent_imports(limit: int = 12) -> list[dict[str, Any]]:
    _ensure_package_storage()
    manifests = sorted(PACKAGE_IMPORTS_ROOT.glob("*/import_result.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    rows: list[dict[str, Any]] = []
    for path in manifests:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"import_id": path.parent.name, "status": "unreadable"}
        data.setdefault("storage_path", _relative_to_root(path))
        rows.append(data)
    return rows


def package_state_payload() -> dict[str, Any]:
    _ensure_package_storage()
    directory_counts = {name: _json_file_count(ROLEPLAY_DATA_ROOT / name) for name in ROLEPLAY_PACKAGE_DIRS}
    sqlite_state = roleplay_sqlite_state_payload()
    return {
        "schema_id": "neo.roleplay.package.state.v1",
        "surface_id": "roleplay",
        "status": "active",
        "package_root": _relative_to_root(PACKAGES_ROOT),
        "exports_root": _relative_to_root(PACKAGE_EXPORTS_ROOT),
        "imports_root": _relative_to_root(PACKAGE_IMPORTS_ROOT),
        "roleplay_root": _relative_to_root(ROLEPLAY_DATA_ROOT),
        "vector_root": _relative_to_root(VECTOR_STORE_ROOT),
        "directory_counts": directory_counts,
        "sqlite": {
            "ready": bool(sqlite_state.get("ready")),
            "path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
            "table_counts": sqlite_state.get("table_counts") or {},
        },
        "exports": _recent_archives(),
        "imports": _recent_imports(),
        "supported_import_modes": ["merge_safe", "skip_existing", "replace"],
        "package_features": [
            "Forge records",
            "Studio projects/sources",
            "Stories sessions/checkpoints/branches",
            "Runtime bundles",
            "Roleplay SQLite memory export",
            "Optional vector store export",
            "Manifest validation",
            "Merge/skip/replace import modes",
        ],
    }


def _write_manifest(zipf: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    zipf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def _add_directory_to_zip(zipf: zipfile.ZipFile, source_root: Path, archive_root: str) -> int:
    count = 0
    if not source_root.exists():
        return 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = _safe_relative(path, source_root)
        if not rel:
            continue
        zipf.write(path, f"{archive_root}/{rel}")
        count += 1
    return count


def export_roleplay_package_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_package_storage()
    payload = payload or {}
    now = _now()
    title = str(payload.get("title") or "Neo Roleplay Package").strip() or "Neo Roleplay Package"
    package_id = str(payload.get("package_id") or f"rp_pkg_{uuid.uuid4().hex[:10]}")
    include_sqlite = bool(payload.get("include_sqlite", True))
    include_vector_store = bool(payload.get("include_vector_store", False))
    include_data_dirs = payload.get("include_dirs")
    if isinstance(include_data_dirs, str):
        include_dirs = [item.strip() for item in include_data_dirs.split(",") if item.strip()]
    elif isinstance(include_data_dirs, list):
        include_dirs = [str(item).strip() for item in include_data_dirs if str(item).strip()]
    else:
        include_dirs = list(ROLEPLAY_PACKAGE_DIRS)
    include_dirs = [item for item in include_dirs if item in ROLEPLAY_PACKAGE_DIRS]
    filename = f"{_slug(title)}_{package_id}.zip"
    archive_path = PACKAGE_EXPORTS_ROOT / filename
    directory_counts: dict[str, int] = {}
    manifest = {
        "schema_id": "neo.roleplay.package.manifest.v1",
        "package_id": package_id,
        "title": title,
        "created_at": now,
        "created_by": "Neo Studio V2 Roleplay",
        "source_root": _relative_to_root(ROLEPLAY_DATA_ROOT),
        "include_sqlite": include_sqlite,
        "include_vector_store": include_vector_store,
        "include_dirs": include_dirs,
        "directory_counts": directory_counts,
        "sqlite_path": "roleplay/roleplay.sqlite" if include_sqlite and ROLEPLAY_SQLITE_PATH.exists() else "",
        "vector_store_root": "vector_store" if include_vector_store and VECTOR_STORE_ROOT.exists() else "",
        "import_modes": ["merge_safe", "skip_existing", "replace"],
        "notes": str(payload.get("notes") or ""),
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for directory in include_dirs:
            count = _add_directory_to_zip(zipf, ROLEPLAY_DATA_ROOT / directory, f"roleplay/{directory}")
            directory_counts[directory] = count
        if include_sqlite and ROLEPLAY_SQLITE_PATH.exists():
            zipf.write(ROLEPLAY_SQLITE_PATH, "roleplay/roleplay.sqlite")
        if include_vector_store and VECTOR_STORE_ROOT.exists():
            directory_counts["vector_store"] = _add_directory_to_zip(zipf, VECTOR_STORE_ROOT, "vector_store")
        _write_manifest(zipf, manifest)
    manifest["archive"] = archive_path.name
    manifest["archive_path"] = _relative_to_root(archive_path)
    manifest["size_bytes"] = archive_path.stat().st_size if archive_path.exists() else 0
    return {
        "schema_id": "neo.roleplay.package.export.v1",
        "surface_id": "roleplay",
        "status": "exported",
        "ok": True,
        "package": manifest,
        "download_url": f"/api/roleplay/package/export/download?archive={archive_path.name}",
        "state": package_state_payload(),
    }


def _resolve_archive_path(value: str) -> Path:
    raw = Path(str(value or "").strip())
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    candidates.append(PACKAGE_EXPORTS_ROOT / raw.name)
    candidates.append(ROOT_DIR / raw)
    for path in candidates:
        try:
            if path.exists() and path.is_file() and path.suffix.lower() == ".zip":
                return path.resolve()
        except Exception:
            continue
    raise FileNotFoundError("Package ZIP could not be found.")


def _safe_zip_member(member: str) -> Path | None:
    path = Path(member)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _copy_zip_member(zipf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, mode: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if mode == "skip_existing":
            return "skipped_existing"
        if mode == "merge_safe":
            return "skipped_existing"
        if mode == "replace":
            if dest.is_file():
                dest.unlink()
    with zipf.open(info) as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out)
    return "written"


def import_roleplay_package_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_package_storage()
    payload = payload or {}
    archive_value = str(payload.get("archive_path") or payload.get("archive") or "").strip()
    if not archive_value:
        raise ValueError("Choose a package ZIP to import.")
    mode = str(payload.get("mode") or "merge_safe").strip() or "merge_safe"
    if mode not in {"merge_safe", "skip_existing", "replace"}:
        raise ValueError("Unsupported import mode.")
    include_sqlite = bool(payload.get("include_sqlite", False))
    include_vector_store = bool(payload.get("include_vector_store", False))
    archive_path = _resolve_archive_path(archive_value)
    import_id = f"rp_import_{uuid.uuid4().hex[:10]}"
    import_dir = PACKAGE_IMPORTS_ROOT / import_id
    import_dir.mkdir(parents=True, exist_ok=True)
    now = _now()
    result = {
        "schema_id": "neo.roleplay.package.import.v1",
        "import_id": import_id,
        "archive": archive_path.name,
        "archive_path": _relative_to_root(archive_path),
        "status": "imported",
        "mode": mode,
        "include_sqlite": include_sqlite,
        "include_vector_store": include_vector_store,
        "created_at": now,
        "written": 0,
        "skipped": 0,
        "ignored": 0,
        "errors": [],
        "manifest": {},
    }
    with zipfile.ZipFile(archive_path, "r") as zipf:
        try:
            manifest = json.loads(zipf.read("manifest.json").decode("utf-8"))
            if isinstance(manifest, dict):
                result["manifest"] = manifest
        except Exception as exc:
            result["errors"].append(f"Manifest warning: {exc}")
        for info in zipf.infolist():
            if info.is_dir() or info.filename == "manifest.json":
                continue
            member = _safe_zip_member(info.filename)
            if member is None:
                result["ignored"] += 1
                continue
            parts = member.parts
            try:
                action = "ignored"
                if len(parts) >= 2 and parts[0] == "roleplay" and parts[1] in ROLEPLAY_PACKAGE_DIRS:
                    rel = Path(*parts[2:]) if len(parts) > 2 else None
                    if rel and rel.name:
                        action = _copy_zip_member(zipf, info, ROLEPLAY_DATA_ROOT / parts[1] / rel, mode)
                elif include_sqlite and info.filename == "roleplay/roleplay.sqlite":
                    action = _copy_zip_member(zipf, info, ROLEPLAY_SQLITE_PATH, mode)
                elif include_vector_store and parts and parts[0] == "vector_store":
                    rel = Path(*parts[1:]) if len(parts) > 1 else None
                    if rel and rel.name:
                        action = _copy_zip_member(zipf, info, VECTOR_STORE_ROOT / rel, mode)
                if action == "written":
                    result["written"] += 1
                elif action.startswith("skipped"):
                    result["skipped"] += 1
                else:
                    result["ignored"] += 1
            except Exception as exc:
                result["errors"].append(f"{info.filename}: {exc}")
    result["ok"] = not result["errors"]
    result["storage_path"] = _relative_to_root(import_dir / "import_result.json")
    (import_dir / "import_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["state"] = package_state_payload()
    return result


def package_archive_download_path(archive: str) -> Path:
    _ensure_package_storage()
    clean = Path(str(archive or "").strip()).name
    if not clean or not clean.endswith(".zip"):
        raise FileNotFoundError("Archive is invalid.")
    path = PACKAGE_EXPORTS_ROOT / clean
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Archive could not be found.")
    return path
