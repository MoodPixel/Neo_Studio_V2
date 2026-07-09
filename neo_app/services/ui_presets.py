from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
PRESET_ROOT = ROOT_DIR / "neo_data" / "ui_presets"
VALID_SURFACE = re.compile(r"^[a-z0-9_-]{1,48}$")
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,80}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _surface_dir(surface: str) -> Path:
    surface_id = str(surface or "").strip().lower()
    if not VALID_SURFACE.match(surface_id):
        raise ValueError("Invalid surface id")
    path = PRESET_ROOT / surface_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(surface: str) -> Path:
    return _surface_dir(surface) / "index.json"


def _preset_path(surface: str, preset_id: str) -> Path:
    if not VALID_ID.match(str(preset_id or "")):
        raise ValueError("Invalid preset id")
    return _surface_dir(surface) / f"{preset_id}.json"


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_index(surface: str) -> dict[str, Any]:
    data = _load_json(_index_path(surface), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("surface", surface)
    data.setdefault("default_preset_id", "")
    data.setdefault("presets", [])
    if not isinstance(data["presets"], list):
        data["presets"] = []
    return data


def _save_index(surface: str, data: dict[str, Any]) -> None:
    data["surface"] = surface
    _write_json(_index_path(surface), data)


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return clean[:42] or "ui-preset"


def _summary_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "preset_id": record.get("preset_id", ""),
        "name": record.get("name", "Untitled preset"),
        "surface": record.get("surface", ""),
        "description": record.get("description", ""),
        "is_default": bool(record.get("is_default", False)),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
    }


def normalize_snapshot(value: Any) -> dict[str, Any]:
    snapshot = value if isinstance(value, dict) else {}
    # Preserve extension data when the frontend includes it, but keep the shell stable.
    snapshot.setdefault("extensions", {})
    snapshot.setdefault("extension_settings", {})
    snapshot.setdefault("surface_state", {})
    return snapshot


def list_ui_presets(surface: str) -> dict[str, Any]:
    index = _load_index(surface)
    presets = []
    for item in index.get("presets", []):
        preset_id = item.get("preset_id") if isinstance(item, dict) else ""
        if not preset_id:
            continue
        record = _load_json(_preset_path(surface, preset_id), None)
        if isinstance(record, dict):
            record["is_default"] = preset_id == index.get("default_preset_id")
            presets.append(_summary_from_record(record))
    index["presets"] = presets
    _save_index(surface, index)
    return {"surface": surface, "default_preset_id": index.get("default_preset_id", ""), "presets": presets}


def get_ui_preset(surface: str, preset_id: str) -> dict[str, Any]:
    record = _load_json(_preset_path(surface, preset_id), None)
    if not isinstance(record, dict):
        raise FileNotFoundError("UI preset not found")
    index = _load_index(surface)
    record["is_default"] = preset_id == index.get("default_preset_id")
    return record


def create_ui_preset(surface: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "New UI Preset").strip()[:80] or "New UI Preset"
    preset_id = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
    now = _now()
    record = {
        "schema_version": "neo.ui_preset.v1",
        "preset_id": preset_id,
        "surface": surface,
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "snapshot": normalize_snapshot(payload.get("snapshot")),
        "created_at": now,
        "updated_at": now,
    }
    _write_json(_preset_path(surface, preset_id), record)
    index = _load_index(surface)
    index["presets"] = [item for item in index.get("presets", []) if item.get("preset_id") != preset_id]
    index["presets"].append(_summary_from_record(record))
    if payload.get("make_default") or not index.get("default_preset_id"):
        index["default_preset_id"] = preset_id
    _save_index(surface, index)
    return get_ui_preset(surface, preset_id)


def update_ui_preset(surface: str, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = get_ui_preset(surface, preset_id)
    if "name" in payload:
        record["name"] = str(payload.get("name") or record.get("name") or "UI Preset").strip()[:80]
    if "description" in payload:
        record["description"] = str(payload.get("description") or "").strip()
    if "snapshot" in payload:
        record["snapshot"] = normalize_snapshot(payload.get("snapshot"))
    record["updated_at"] = _now()
    record.pop("is_default", None)
    _write_json(_preset_path(surface, preset_id), record)
    index = _load_index(surface)
    index["presets"] = [item for item in index.get("presets", []) if item.get("preset_id") != preset_id]
    index["presets"].append(_summary_from_record(record))
    _save_index(surface, index)
    return get_ui_preset(surface, preset_id)


def delete_ui_preset(surface: str, preset_id: str) -> dict[str, Any]:
    path = _preset_path(surface, preset_id)
    if path.exists():
        path.unlink()
    index = _load_index(surface)
    index["presets"] = [item for item in index.get("presets", []) if item.get("preset_id") != preset_id]
    if index.get("default_preset_id") == preset_id:
        index["default_preset_id"] = ""
    _save_index(surface, index)
    return {"ok": True, "deleted": preset_id, **list_ui_presets(surface)}


def set_default_ui_preset(surface: str, preset_id: str) -> dict[str, Any]:
    get_ui_preset(surface, preset_id)
    index = _load_index(surface)
    index["default_preset_id"] = preset_id
    _save_index(surface, index)
    return {"ok": True, **list_ui_presets(surface)}


def get_default_ui_preset(surface: str) -> dict[str, Any]:
    index = _load_index(surface)
    preset_id = index.get("default_preset_id")
    if not preset_id:
        return {"surface": surface, "preset": None}
    return {"surface": surface, "preset": get_ui_preset(surface, preset_id)}
