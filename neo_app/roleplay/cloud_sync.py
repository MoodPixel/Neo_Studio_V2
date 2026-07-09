from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROOT_DIR, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.packages import ROLEPLAY_PACKAGE_DIRS, VECTOR_STORE_ROOT
from neo_app.roleplay.sqlite_store import ROLEPLAY_SQLITE_PATH

CLOUD_SYNC_ROOT = ROLEPLAY_DATA_ROOT / "cloud_sync"
SNAPSHOTS_ROOT = CLOUD_SYNC_ROOT / "snapshots"
BACKUPS_ROOT = CLOUD_SYNC_ROOT / "backups"
INBOUND_ROOT = CLOUD_SYNC_ROOT / "inbound"
LOG_PATH = CLOUD_SYNC_ROOT / "sync_log.json"
SETTINGS_PATH = CLOUD_SYNC_ROOT / "settings.json"
SYNC_MANIFEST_PATH = CLOUD_SYNC_ROOT / "sync_manifest.json"

DEFAULT_SYNC_DIRS: tuple[str, ...] = tuple(ROLEPLAY_PACKAGE_DIRS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    clean = "-".join(part for part in clean.split("-") if part)
    return clean[:80] or "roleplay-sync"


def _ensure_cloud_sync_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    CLOUD_SYNC_ROOT.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUPS_ROOT.mkdir(parents=True, exist_ok=True)
    INBOUND_ROOT.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        _write_json(SETTINGS_PATH, {
            "schema_id": "neo.roleplay.cloud_sync.settings.v1",
            "mode": "local_first",
            "provider": "manual_path",
            "default_remote_path": "",
            "conflict_policy": "backup_then_merge_safe",
            "include_sqlite": True,
            "include_vector_store": False,
            "created_at": _now(),
            "updated_at": _now(),
        })


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_log(event_type: str, payload: dict[str, Any]) -> None:
    _ensure_cloud_sync_storage()
    rows = _read_json(LOG_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {"event_type": event_type, "created_at": _now(), **payload})
    _write_json(LOG_PATH, rows[:200])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return None


def _iter_sync_files(include_dirs: list[str], *, include_sqlite: bool, include_vector_store: bool) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    for directory in include_dirs:
        if not directory or ".." in directory or directory.startswith("/"):
            continue
        root = ROLEPLAY_DATA_ROOT / directory
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*")):
            if file_path.is_file():
                rel = _safe_relative(file_path, ROLEPLAY_DATA_ROOT)
                if rel:
                    rows.append((file_path, f"roleplay/{rel}"))
    if include_sqlite and ROLEPLAY_SQLITE_PATH.exists():
        rows.append((ROLEPLAY_SQLITE_PATH, "roleplay/roleplay.sqlite"))
    if include_vector_store and VECTOR_STORE_ROOT.exists():
        for file_path in sorted(VECTOR_STORE_ROOT.rglob("*")):
            if file_path.is_file():
                rel = _safe_relative(file_path, VECTOR_STORE_ROOT)
                if rel:
                    rows.append((file_path, f"vector_store/{rel}"))
    return rows


def _local_manifest(include_dirs: list[str] | None = None, *, include_sqlite: bool = True, include_vector_store: bool = False) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    dirs = include_dirs or list(DEFAULT_SYNC_DIRS)
    files = _iter_sync_files(dirs, include_sqlite=include_sqlite, include_vector_store=include_vector_store)
    file_rows: list[dict[str, Any]] = []
    for file_path, archive_name in files:
        try:
            stat = file_path.stat()
            file_rows.append({
                "archive_name": archive_name,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "sha256": _sha256(file_path),
            })
        except Exception:
            continue
    manifest_digest = hashlib.sha256(json.dumps(file_rows, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schema_id": "neo.roleplay.cloud_sync.manifest.v1",
        "snapshot_kind": "local_manifest",
        "created_at": _now(),
        "data_root": _relative_to_root(ROLEPLAY_DATA_ROOT),
        "include_dirs": dirs,
        "include_sqlite": include_sqlite,
        "include_vector_store": include_vector_store,
        "file_count": len(file_rows),
        "total_bytes": sum(int(row.get("size_bytes") or 0) for row in file_rows),
        "digest": manifest_digest,
        "files": file_rows,
    }


def _read_snapshot_manifest(archive_path: Path) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(archive_path, "r") as zipf:
            with zipf.open("manifest.json") as handle:
                return json.loads(handle.read().decode("utf-8"))
    except Exception:
        return None


def _latest_zip_in_dir(path: Path) -> Path | None:
    if path.is_file() and path.suffix.lower() == ".zip":
        return path
    if path.is_dir():
        archives = sorted(path.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if archives:
            return archives[0]
    return None


def _safe_extract_member(zipf: zipfile.ZipFile, member: zipfile.ZipInfo, target_root: Path) -> bool:
    name = member.filename
    if not name or name.endswith("/") or name == "manifest.json":
        return False
    if name.startswith("/") or ".." in Path(name).parts:
        return False
    if name.startswith("roleplay/"):
        relative = Path(name).relative_to("roleplay")
        target = ROLEPLAY_DATA_ROOT / relative
    elif name.startswith("vector_store/"):
        relative = Path(name).relative_to("vector_store")
        target = VECTOR_STORE_ROOT / relative
    else:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipf.open(member) as source, target.open("wb") as dest:
        shutil.copyfileobj(source, dest)
    return True


def cloud_sync_state_payload() -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    settings = _read_json(SETTINGS_PATH, {})
    sync_manifest = _read_json(SYNC_MANIFEST_PATH, {})
    recent_snapshots = []
    for path in sorted(SNAPSHOTS_ROOT.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
        manifest = _read_snapshot_manifest(path) or {}
        recent_snapshots.append({
            "archive": path.name,
            "path": _relative_to_root(path),
            "size_bytes": path.stat().st_size,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "file_count": manifest.get("file_count", 0),
            "digest": manifest.get("digest", ""),
        })
    recent_backups = []
    for path in sorted(BACKUPS_ROOT.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
        recent_backups.append({"archive": path.name, "path": _relative_to_root(path), "size_bytes": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()})
    logs = _read_json(LOG_PATH, [])
    if not isinstance(logs, list):
        logs = []
    local = _local_manifest(include_sqlite=bool(settings.get("include_sqlite", True)), include_vector_store=bool(settings.get("include_vector_store", False)))
    return {
        "schema_id": "neo.roleplay.cloud_sync.state.v1",
        "surface_id": "roleplay",
        "status": "foundation",
        "mode": settings.get("mode", "local_first"),
        "provider": settings.get("provider", "manual_path"),
        "conflict_policy": settings.get("conflict_policy", "backup_then_merge_safe"),
        "ready": True,
        "settings": settings,
        "local_manifest": {k: v for k, v in local.items() if k != "files"},
        "last_sync_manifest": sync_manifest,
        "snapshots": recent_snapshots,
        "backups": recent_backups,
        "logs": logs[:20],
        "storage": {
            "root": _relative_to_root(CLOUD_SYNC_ROOT),
            "snapshots": _relative_to_root(SNAPSHOTS_ROOT),
            "backups": _relative_to_root(BACKUPS_ROOT),
            "inbound": _relative_to_root(INBOUND_ROOT),
        },
        "deferred_features": [
            "remote provider auth",
            "automatic scheduled sync",
            "real-time collaborative cloud transport",
            "field-level merge resolver",
        ],
    }


def create_cloud_snapshot_payload(payload: dict[str, Any] | None = None, *, backup: bool = False) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    payload = payload or {}
    title = str(payload.get("title") or ("Roleplay Backup" if backup else "Roleplay Cloud Snapshot"))
    include_dirs_raw = str(payload.get("include_dirs") or "").strip()
    include_dirs = [item.strip() for item in include_dirs_raw.split(",") if item.strip()] or list(DEFAULT_SYNC_DIRS)
    include_sqlite = bool(payload.get("include_sqlite", True))
    include_vector_store = bool(payload.get("include_vector_store", False))
    manifest = _local_manifest(include_dirs, include_sqlite=include_sqlite, include_vector_store=include_vector_store)
    snapshot_id = f"{'backup' if backup else 'sync'}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    archive_name = f"{_slug(title)}_{snapshot_id}.zip"
    root = BACKUPS_ROOT if backup else SNAPSHOTS_ROOT
    archive_path = root / archive_name
    manifest.update({
        "snapshot_id": snapshot_id,
        "title": title,
        "snapshot_kind": "backup" if backup else "cloud_sync_snapshot",
        "archive": archive_name,
        "created_at": _now(),
    })
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        for file_path, archive_member in _iter_sync_files(include_dirs, include_sqlite=include_sqlite, include_vector_store=include_vector_store):
            if file_path.exists() and file_path.is_file():
                zipf.write(file_path, archive_member)
    result = {
        "ok": True,
        "snapshot": {
            "snapshot_id": snapshot_id,
            "archive": archive_name,
            "path": _relative_to_root(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "file_count": manifest.get("file_count", 0),
            "digest": manifest.get("digest", ""),
            "created_at": manifest.get("created_at"),
            "kind": manifest.get("snapshot_kind"),
        },
    }
    _write_json(SYNC_MANIFEST_PATH, {"last_snapshot": result["snapshot"], "updated_at": _now()})
    _append_log("cloud_sync.backup_created" if backup else "cloud_sync.snapshot_created", result["snapshot"])
    result["state"] = cloud_sync_state_payload()
    return result


def compare_cloud_sync_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    payload = payload or {}
    remote_path_raw = str(payload.get("remote_path") or payload.get("archive_path") or "").strip()
    settings = _read_json(SETTINGS_PATH, {})
    if not remote_path_raw:
        remote_path_raw = str(settings.get("default_remote_path") or "").strip()
    remote_path = Path(remote_path_raw).expanduser() if remote_path_raw else Path()
    archive = _latest_zip_in_dir(remote_path) if remote_path_raw else None
    local = _local_manifest(include_sqlite=bool(payload.get("include_sqlite", settings.get("include_sqlite", True))), include_vector_store=bool(payload.get("include_vector_store", settings.get("include_vector_store", False))))
    remote_manifest = _read_snapshot_manifest(archive) if archive else None
    local_map = {row["archive_name"]: row for row in local.get("files", [])}
    remote_map = {row["archive_name"]: row for row in (remote_manifest or {}).get("files", [])}
    only_local = sorted(set(local_map) - set(remote_map))
    only_remote = sorted(set(remote_map) - set(local_map))
    changed = sorted(key for key in set(local_map).intersection(remote_map) if local_map[key].get("sha256") != remote_map[key].get("sha256"))
    status = "no_remote" if not remote_manifest else ("in_sync" if not only_local and not only_remote and not changed else "different")
    result = {
        "ok": True,
        "schema_id": "neo.roleplay.cloud_sync.compare.v1",
        "status": status,
        "remote_path": remote_path_raw,
        "remote_archive": archive.name if archive else "",
        "local_digest": local.get("digest", ""),
        "remote_digest": (remote_manifest or {}).get("digest", ""),
        "local_file_count": local.get("file_count", 0),
        "remote_file_count": (remote_manifest or {}).get("file_count", 0),
        "only_local": only_local[:100],
        "only_remote": only_remote[:100],
        "changed": changed[:100],
        "summary": {
            "only_local": len(only_local),
            "only_remote": len(only_remote),
            "changed": len(changed),
        },
    }
    _append_log("cloud_sync.compared", {"status": status, "remote_path": remote_path_raw, "remote_archive": result["remote_archive"], "summary": result["summary"]})
    result["state"] = cloud_sync_state_payload()
    return result


def push_cloud_sync_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    payload = payload or {}
    remote_path_raw = str(payload.get("remote_path") or "").strip()
    if not remote_path_raw:
        settings = _read_json(SETTINGS_PATH, {})
        remote_path_raw = str(settings.get("default_remote_path") or "").strip()
    if not remote_path_raw:
        return {"ok": False, "error": "remote_path is required for push"}
    remote_dir = Path(remote_path_raw).expanduser()
    if remote_dir.suffix.lower() == ".zip":
        remote_dir = remote_dir.parent
    remote_dir.mkdir(parents=True, exist_ok=True)
    snapshot = create_cloud_snapshot_payload(payload, backup=False)["snapshot"]
    source = SNAPSHOTS_ROOT / snapshot["archive"]
    target = remote_dir / snapshot["archive"]
    shutil.copy2(source, target)
    result = {
        "ok": True,
        "action": "push",
        "remote_path": str(remote_dir),
        "archive": target.name,
        "remote_archive_path": str(target),
        "size_bytes": target.stat().st_size,
        "pushed_at": _now(),
    }
    _write_json(SYNC_MANIFEST_PATH, {"last_push": result, "updated_at": _now()})
    _append_log("cloud_sync.pushed", result)
    result["state"] = cloud_sync_state_payload()
    return result


def pull_cloud_sync_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    payload = payload or {}
    remote_path_raw = str(payload.get("remote_path") or payload.get("archive_path") or "").strip()
    if not remote_path_raw:
        settings = _read_json(SETTINGS_PATH, {})
        remote_path_raw = str(settings.get("default_remote_path") or "").strip()
    if not remote_path_raw:
        return {"ok": False, "error": "remote_path is required for pull"}
    remote_archive = _latest_zip_in_dir(Path(remote_path_raw).expanduser())
    if not remote_archive or not remote_archive.exists():
        return {"ok": False, "error": "No remote snapshot ZIP found"}
    mode = str(payload.get("mode") or "merge_safe")
    backup_result = create_cloud_snapshot_payload({"title": "Pre-pull backup", "include_sqlite": True, "include_vector_store": bool(payload.get("include_vector_store", False))}, backup=True)
    inbound = INBOUND_ROOT / remote_archive.name
    shutil.copy2(remote_archive, inbound)
    written = 0
    skipped = 0
    with zipfile.ZipFile(inbound, "r") as zipf:
        for member in zipf.infolist():
            if member.filename.endswith("/") or member.filename == "manifest.json":
                continue
            target_exists = False
            if member.filename.startswith("roleplay/"):
                target = ROLEPLAY_DATA_ROOT / Path(member.filename).relative_to("roleplay")
                target_exists = target.exists()
            elif member.filename.startswith("vector_store/"):
                target = VECTOR_STORE_ROOT / Path(member.filename).relative_to("vector_store")
                target_exists = target.exists()
            else:
                skipped += 1
                continue
            if mode == "skip_existing" and target_exists:
                skipped += 1
                continue
            if mode == "merge_safe" and target_exists:
                # Keep local copy when hashes differ; this is foundation-safe.
                skipped += 1
                continue
            if _safe_extract_member(zipf, member, ROLEPLAY_DATA_ROOT):
                written += 1
            else:
                skipped += 1
    result = {
        "ok": True,
        "action": "pull",
        "mode": mode,
        "remote_path": remote_path_raw,
        "archive": remote_archive.name,
        "inbound_path": _relative_to_root(inbound),
        "written": written,
        "skipped": skipped,
        "backup": backup_result.get("snapshot", {}),
        "pulled_at": _now(),
    }
    _write_json(SYNC_MANIFEST_PATH, {"last_pull": result, "updated_at": _now()})
    _append_log("cloud_sync.pulled", {k: v for k, v in result.items() if k != "backup"})
    result["state"] = cloud_sync_state_payload()
    return result


def update_cloud_sync_settings_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_cloud_sync_storage()
    payload = payload or {}
    settings = _read_json(SETTINGS_PATH, {})
    for key in ("default_remote_path", "conflict_policy", "provider", "mode"):
        if key in payload:
            settings[key] = payload.get(key)
    for key in ("include_sqlite", "include_vector_store"):
        if key in payload:
            settings[key] = bool(payload.get(key))
    settings["updated_at"] = _now()
    _write_json(SETTINGS_PATH, settings)
    _append_log("cloud_sync.settings.updated", {"default_remote_path": settings.get("default_remote_path", ""), "conflict_policy": settings.get("conflict_policy", "")})
    return {"ok": True, "settings": settings, "state": cloud_sync_state_payload()}
