from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.packages import export_roleplay_package_payload, import_roleplay_package_payload, PACKAGE_EXPORTS_ROOT
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROOT_DIR, _relative_to_root, ensure_roleplay_foundation

REGISTRY_ROOT = ROLEPLAY_DATA_ROOT / "package_registry"
REGISTRY_INDEX_PATH = REGISTRY_ROOT / "registry_index.json"
INSTALLED_PATH = REGISTRY_ROOT / "installed_packages.json"
ENABLED_PATH = REGISTRY_ROOT / "enabled_plugins.json"
REGISTRY_PACKAGES_ROOT = REGISTRY_ROOT / "packages"
PLUGIN_MANIFESTS_ROOT = REGISTRY_ROOT / "plugin_manifests"
REGISTRY_LOG_PATH = REGISTRY_ROOT / "registry_log.json"

SUPPORTED_PACKAGE_TYPES = ["roleplay_package", "plugin", "template_pack", "runtime_pack", "memory_pack"]
SUPPORTED_STATUSES = ["available", "installed", "enabled", "disabled", "invalid"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_registry() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    for path in (REGISTRY_ROOT, REGISTRY_PACKAGES_ROOT, PLUGIN_MANIFESTS_ROOT):
        path.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_INDEX_PATH.exists():
        _write_json(REGISTRY_INDEX_PATH, {"schema_id": "neo.roleplay.package_registry.index.v1", "updated_at": _now(), "items": []})
    if not INSTALLED_PATH.exists():
        _write_json(INSTALLED_PATH, {"schema_id": "neo.roleplay.package_registry.installed.v1", "updated_at": _now(), "items": []})
    if not ENABLED_PATH.exists():
        _write_json(ENABLED_PATH, {"schema_id": "neo.roleplay.package_registry.enabled.v1", "updated_at": _now(), "items": []})
    if not REGISTRY_LOG_PATH.exists():
        _write_json(REGISTRY_LOG_PATH, [])


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    clean = "-".join(part for part in clean.split("-") if part)
    return clean[:72] or "roleplay-package"


def _log(action: str, payload: dict[str, Any]) -> None:
    _ensure_registry()
    rows = _read_json(REGISTRY_LOG_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {"at": _now(), "action": action, **payload})
    _write_json(REGISTRY_LOG_PATH, rows[:80])


def _resolve_archive_path(value: str) -> Path:
    raw = Path(str(value or "").strip())
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    candidates.extend([
        REGISTRY_PACKAGES_ROOT / raw.name,
        PACKAGE_EXPORTS_ROOT / raw.name,
        ROOT_DIR / raw,
    ])
    for path in candidates:
        try:
            if path.exists() and path.is_file() and path.suffix.lower() == ".zip":
                return path.resolve()
        except Exception:
            continue
    raise FileNotFoundError("Registry package ZIP could not be found.")


def _extract_manifest(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as zipf:
            if "manifest.json" in zipf.namelist():
                return json.loads(zipf.read("manifest.json").decode("utf-8"))
    except Exception:
        pass
    return {}


def _normalize_item(raw: dict[str, Any], *, archive_path: Path | None = None) -> dict[str, Any]:
    manifest = raw or {}
    title = str(manifest.get("title") or manifest.get("name") or (archive_path.stem if archive_path else "Untitled package"))
    package_id = str(manifest.get("package_id") or manifest.get("plugin_id") or f"registry_{_slug(title)}")
    package_type = str(manifest.get("package_type") or manifest.get("type") or "roleplay_package")
    if package_type not in SUPPORTED_PACKAGE_TYPES:
        package_type = "roleplay_package"
    archive = archive_path.name if archive_path else str(manifest.get("archive") or "")
    return {
        "package_id": package_id,
        "title": title,
        "package_type": package_type,
        "version": str(manifest.get("version") or manifest.get("package_version") or "1.0.0"),
        "author": str(manifest.get("author") or manifest.get("created_by") or ""),
        "description": str(manifest.get("description") or manifest.get("notes") or ""),
        "tags": manifest.get("tags") if isinstance(manifest.get("tags"), list) else [],
        "archive": archive,
        "archive_path": _relative_to_root(archive_path) if archive_path else str(manifest.get("archive_path") or ""),
        "manifest_schema": str(manifest.get("schema_id") or ""),
        "status": "available",
        "created_at": str(manifest.get("created_at") or _now()),
        "updated_at": _now(),
    }


def _index_items() -> list[dict[str, Any]]:
    _ensure_registry()
    index = _read_json(REGISTRY_INDEX_PATH, {"items": []})
    items = index.get("items") if isinstance(index, dict) else []
    return items if isinstance(items, list) else []


def _installed_items() -> list[dict[str, Any]]:
    _ensure_registry()
    data = _read_json(INSTALLED_PATH, {"items": []})
    items = data.get("items") if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def _enabled_ids() -> set[str]:
    _ensure_registry()
    data = _read_json(ENABLED_PATH, {"items": []})
    items = data.get("items") if isinstance(data, dict) else []
    return {str(item.get("package_id")) for item in items if isinstance(item, dict) and item.get("enabled") is True}


def _save_index(items: list[dict[str, Any]]) -> None:
    _write_json(REGISTRY_INDEX_PATH, {"schema_id": "neo.roleplay.package_registry.index.v1", "updated_at": _now(), "items": items})


def _save_installed(items: list[dict[str, Any]]) -> None:
    _write_json(INSTALLED_PATH, {"schema_id": "neo.roleplay.package_registry.installed.v1", "updated_at": _now(), "items": items})


def _save_enabled(ids: set[str]) -> None:
    rows = [{"package_id": package_id, "enabled": True, "updated_at": _now()} for package_id in sorted(ids)]
    _write_json(ENABLED_PATH, {"schema_id": "neo.roleplay.package_registry.enabled.v1", "updated_at": _now(), "items": rows})


def _merge_item(items: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    package_id = str(item.get("package_id") or "")
    merged = [row for row in items if str(row.get("package_id")) != package_id]
    merged.insert(0, item)
    return merged


def registry_state_payload() -> dict[str, Any]:
    _ensure_registry()
    available = _index_items()
    installed = _installed_items()
    enabled = _enabled_ids()
    installed_ids = {str(item.get("package_id")) for item in installed}
    enriched_available: list[dict[str, Any]] = []
    for item in available:
        row = dict(item)
        package_id = str(row.get("package_id") or "")
        if package_id in installed_ids:
            row["status"] = "enabled" if package_id in enabled else "installed"
        else:
            row["status"] = row.get("status") or "available"
        enriched_available.append(row)
    logs = _read_json(REGISTRY_LOG_PATH, [])
    return {
        "schema_id": "neo.roleplay.package_registry.state.v1",
        "surface_id": "roleplay",
        "status": "active",
        "registry_root": _relative_to_root(REGISTRY_ROOT),
        "registry_packages_root": _relative_to_root(REGISTRY_PACKAGES_ROOT),
        "index_path": _relative_to_root(REGISTRY_INDEX_PATH),
        "available": enriched_available,
        "installed": installed,
        "enabled": sorted(enabled),
        "counts": {
            "available": len(enriched_available),
            "installed": len(installed),
            "enabled": len(enabled),
            "disabled": max(0, len(installed) - len(enabled)),
        },
        "supported_package_types": SUPPORTED_PACKAGE_TYPES,
        "supported_statuses": SUPPORTED_STATUSES,
        "recent_activity": logs[:20] if isinstance(logs, list) else [],
        "features": [
            "Local package registry index",
            "Add package ZIP to registry",
            "Install available package",
            "Enable/disable installed package",
            "Export current project to registry",
            "Package manifest validation",
            "Activity log",
        ],
        "deferred_features": [
            "Remote marketplace discovery",
            "Signed package verification",
            "Dependency resolver",
            "Plugin sandbox execution",
            "Online ratings/download counts",
        ],
    }


def add_package_to_registry_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_registry()
    payload = payload or {}
    archive_path = _resolve_archive_path(str(payload.get("archive_path") or payload.get("archive") or ""))
    manifest = _extract_manifest(archive_path)
    item = _normalize_item({**manifest, **{k: v for k, v in payload.items() if v not in (None, "")}}, archive_path=archive_path)
    registry_archive = REGISTRY_PACKAGES_ROOT / archive_path.name
    if archive_path.resolve() != registry_archive.resolve():
        shutil.copy2(archive_path, registry_archive)
    item["archive"] = registry_archive.name
    item["archive_path"] = _relative_to_root(registry_archive)
    _save_index(_merge_item(_index_items(), item))
    _write_json(PLUGIN_MANIFESTS_ROOT / f"{item['package_id']}.json", item)
    _log("registry.added", {"package_id": item["package_id"], "archive": item["archive"]})
    return {"ok": True, "schema_id": "neo.roleplay.package_registry.add.v1", "item": item, "state": registry_state_payload()}


def install_registry_package_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_registry()
    payload = payload or {}
    package_id = str(payload.get("package_id") or "").strip()
    mode = str(payload.get("mode") or "merge_safe")
    item = next((row for row in _index_items() if str(row.get("package_id")) == package_id), None)
    if not item:
        raise FileNotFoundError("Registry package was not found.")
    archive_path = _resolve_archive_path(str(item.get("archive") or item.get("archive_path") or ""))
    result = import_roleplay_package_payload({
        "archive_path": str(archive_path),
        "mode": mode,
        "include_sqlite": bool(payload.get("include_sqlite", False)),
        "include_vector_store": bool(payload.get("include_vector_store", False)),
    })
    installed_item = dict(item)
    installed_item.update({"status": "installed", "installed_at": _now(), "import_result": result.get("import_id") or result.get("status")})
    _save_installed(_merge_item(_installed_items(), installed_item))
    if bool(payload.get("enable", True)):
        enabled = _enabled_ids(); enabled.add(package_id); _save_enabled(enabled)
        installed_item["status"] = "enabled"
    _log("registry.installed", {"package_id": package_id, "mode": mode, "enabled": package_id in _enabled_ids()})
    return {"ok": True, "schema_id": "neo.roleplay.package_registry.install.v1", "item": installed_item, "import_result": result, "state": registry_state_payload()}


def toggle_registry_package_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_registry()
    payload = payload or {}
    package_id = str(payload.get("package_id") or "").strip()
    enabled_flag = bool(payload.get("enabled", True))
    if not any(str(item.get("package_id")) == package_id for item in _installed_items()):
        raise FileNotFoundError("Installed package was not found.")
    ids = _enabled_ids()
    if enabled_flag:
        ids.add(package_id)
    else:
        ids.discard(package_id)
    _save_enabled(ids)
    _log("registry.toggled", {"package_id": package_id, "enabled": enabled_flag})
    return {"ok": True, "schema_id": "neo.roleplay.package_registry.toggle.v1", "package_id": package_id, "enabled": enabled_flag, "state": registry_state_payload()}


def export_package_to_registry_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_registry()
    payload = payload or {}
    export_result = export_roleplay_package_payload(payload)
    archive = str(export_result.get("package", {}).get("archive") or "")
    item_payload = {
        "archive": archive,
        "package_type": str(payload.get("package_type") or "roleplay_package"),
        "author": str(payload.get("author") or ""),
        "description": str(payload.get("description") or payload.get("notes") or ""),
        "version": str(payload.get("version") or "1.0.0"),
        "tags": [tag.strip() for tag in str(payload.get("tags") or "").split(",") if tag.strip()],
    }
    registry_result = add_package_to_registry_payload(item_payload)
    _log("registry.exported_current", {"package_id": registry_result.get("item", {}).get("package_id"), "archive": archive})
    return {"ok": True, "schema_id": "neo.roleplay.package_registry.export_current.v1", "export": export_result, "registry": registry_result, "state": registry_state_payload()}
