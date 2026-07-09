from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import shutil
import zipfile

from neo_app.admin.engine import ROOT_DIR, admin_engine_state_payload, ensure_admin_engine_foundation, VECTOR_STORE_DIR, VECTOR_STORE_PATH

CHROMA_SCHEMA_ID = "neo.admin.engine.chroma_collections.v1"
CHROMA_VERSION = "0.1.0-chroma-export-import"
EXPORT_DIR = ROOT_DIR / "neo_data" / "admin" / "engine" / "chroma_exports"
IMPORT_DIR = ROOT_DIR / "neo_data" / "admin" / "engine" / "chroma_imports"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return cleaned.strip("._") or "collection"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except Exception:
        return str(path)


def _resolve_under_root(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    raw = Path(str(value))
    if not raw.is_absolute():
        raw = ROOT_DIR / raw
    return raw.resolve()


def _vector_root() -> Path:
    engine = admin_engine_state_payload()
    vector = engine.get("vector_store") or {}
    return _resolve_under_root(vector.get("persist_path") or vector.get("root"), VECTOR_STORE_DIR)


def _collection_candidates(root: Path) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    collections: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            file_count = sum(1 for p in child.rglob("*") if p.is_file())
            size_bytes = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
            collections.append({
                "collection_id": child.name,
                "name": child.name,
                "kind": "directory_collection",
                "path": _rel(child),
                "file_count": file_count,
                "size_bytes": size_bytes,
                "exportable": True,
            })
    chroma_files = ["chroma.sqlite3", "index", "index_metadata.pickle"]
    root_files = [name for name in chroma_files if (root / name).exists()]
    if root_files:
        size_bytes = sum((root / name).stat().st_size for name in root_files if (root / name).is_file())
        collections.insert(0, {
            "collection_id": "root_chroma_store",
            "name": "Root Chroma Store",
            "kind": "root_chroma_store",
            "path": _rel(root),
            "file_count": len(root_files),
            "size_bytes": size_bytes,
            "exportable": True,
        })
    return collections


def chroma_collection_state_payload() -> dict[str, Any]:
    ensure_admin_engine_foundation()
    engine = admin_engine_state_payload()
    root = _vector_root()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    collections = _collection_candidates(root)
    exports = []
    for archive in sorted(EXPORT_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
        exports.append({
            "archive_name": archive.name,
            "archive_path": _rel(archive),
            "size_bytes": archive.stat().st_size,
            "created_at": datetime.fromtimestamp(archive.stat().st_mtime, tz=timezone.utc).isoformat(),
            "download_url": f"/api/admin/engine/chroma/export/download?archive={archive.name}",
        })
    imports = []
    for manifest in sorted(IMPORT_DIR.glob("*/import_manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
        try:
            imports.append(json.loads(manifest.read_text(encoding="utf-8")))
        except Exception:
            imports.append({"manifest_path": _rel(manifest), "status": "unreadable"})
    return {
        "schema_id": CHROMA_SCHEMA_ID,
        "version": CHROMA_VERSION,
        "surface_id": "admin",
        "tab_id": "engine",
        "status": "active",
        "ready": True,
        "owner": "admin",
        "paths": {
            "vector_root": _rel(root),
            "export_root": _rel(EXPORT_DIR),
            "import_root": _rel(IMPORT_DIR),
        },
        "vector_store": engine.get("vector_store") or {},
        "collections": collections,
        "collection_count": len(collections),
        "exports": exports,
        "imports": imports,
        "supported_operations": ["export_collection_zip", "import_collection_zip", "manifest_validation", "safe_merge_into_vector_root"],
        "notes": [
            "Admin owns collection export/import. Roleplay consumes vector collections through the Admin Engine bridge.",
            "This exports filesystem-backed Chroma/vector collection folders and root Chroma files when present.",
        ],
    }


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def export_chroma_collection_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    state = chroma_collection_state_payload()
    root = _vector_root()
    collection_ids = payload.get("collection_ids") or payload.get("collections") or []
    if isinstance(collection_ids, str):
        collection_ids = [item.strip() for item in collection_ids.split(",") if item.strip()]
    if not collection_ids:
        collection_ids = [item["collection_id"] for item in state.get("collections") or []]
    selected = [item for item in state.get("collections") or [] if item.get("collection_id") in set(collection_ids)]
    if not selected:
        selected = []
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    title = _safe_name(payload.get("title") or "neo_chroma_export")
    archive = EXPORT_DIR / f"{title}_{stamp}.zip"
    manifest = {
        "schema_id": "neo.admin.engine.chroma.export_manifest.v1",
        "version": CHROMA_VERSION,
        "created_at": _now(),
        "source_vector_root": _rel(root),
        "collection_ids": collection_ids,
        "selected_collections": selected,
        "engine_vector_store": state.get("vector_store") or {},
        "restore_policy": payload.get("restore_policy") or "merge_safe",
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        for item in selected:
            cid = str(item.get("collection_id") or "")
            if cid == "root_chroma_store":
                for child in root.iterdir():
                    if child.is_file() and child.name in {"chroma.sqlite3", "index_metadata.pickle"}:
                        zf.write(child, f"root_chroma_store/{child.name}")
                    elif child.is_dir() and child.name == "index":
                        for sub in child.rglob("*"):
                            if sub.is_file():
                                zf.write(sub, f"root_chroma_store/index/{sub.relative_to(child)}")
                continue
            src = root / cid
            if src.exists() and src.is_dir():
                for file_path in src.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, f"collections/{cid}/{file_path.relative_to(src)}")
    result = {
        "schema_id": "neo.admin.engine.chroma.export.v1",
        "status": "exported",
        "archive_name": archive.name,
        "archive_path": _rel(archive),
        "download_url": f"/api/admin/engine/chroma/export/download?archive={archive.name}",
        "collection_count": len(selected),
        "manifest": manifest,
        "state": chroma_collection_state_payload(),
    }
    return result


def export_archive_path(archive_name: str) -> Path:
    safe = _safe_name(archive_name)
    archive = (EXPORT_DIR / safe).resolve()
    if not str(archive).startswith(str(EXPORT_DIR.resolve())) or not archive.exists() or archive.suffix.lower() != ".zip":
        raise FileNotFoundError(archive_name)
    return archive


def import_chroma_archive_payload(archive_path: str, *, mode: str = "merge_safe") -> dict[str, Any]:
    root = _vector_root()
    root.mkdir(parents=True, exist_ok=True)
    source = _resolve_under_root(archive_path, ROOT_DIR)
    if not source.exists() or source.suffix.lower() != ".zip":
        raise FileNotFoundError(str(source))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    import_root = IMPORT_DIR / f"import_{stamp}_{_safe_name(source.stem)}"
    import_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as zf:
        names = zf.namelist()
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe archive member: {name}")
        zf.extractall(import_root)
    manifest_path = import_root / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    imported: list[dict[str, Any]] = []
    collections_dir = import_root / "collections"
    if collections_dir.exists():
        for collection in sorted(collections_dir.iterdir(), key=lambda p: p.name.lower()):
            if not collection.is_dir():
                continue
            target = root / _safe_name(collection.name)
            if target.exists() and mode == "skip_existing":
                imported.append({"collection_id": collection.name, "status": "skipped_existing", "target": _rel(target)})
                continue
            if target.exists() and mode == "replace":
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            for item in collection.rglob("*"):
                if item.is_file():
                    dest = target / item.relative_to(collection)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
            imported.append({"collection_id": collection.name, "status": "imported", "target": _rel(target)})
    root_chroma = import_root / "root_chroma_store"
    if root_chroma.exists() and root_chroma.is_dir():
        for item in root_chroma.rglob("*"):
            if item.is_file():
                dest = root / item.relative_to(root_chroma)
                if dest.exists() and mode == "skip_existing":
                    continue
                if dest.exists() and mode == "merge_safe":
                    dest = dest.with_name(f"{dest.stem}_imported_{stamp}{dest.suffix}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
        imported.append({"collection_id": "root_chroma_store", "status": "imported", "target": _rel(root)})
    import_manifest = {
        "schema_id": "neo.admin.engine.chroma.import_manifest.v1",
        "version": CHROMA_VERSION,
        "created_at": _now(),
        "archive_path": _rel(source),
        "mode": mode,
        "import_root": _rel(import_root),
        "target_vector_root": _rel(root),
        "source_manifest": manifest,
        "imported": imported,
    }
    _write_manifest(import_root / "import_manifest.json", import_manifest)
    return {
        "schema_id": "neo.admin.engine.chroma.import.v1",
        "status": "imported",
        "imported_count": len(imported),
        "import_manifest": import_manifest,
        "state": chroma_collection_state_payload(),
    }
