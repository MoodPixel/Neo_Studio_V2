from __future__ import annotations

import copy
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
IMAGE_UI_PRESET_LATENT_CHECKPOINT_SCHEMA = "neo.image.ui_preset_latent_checkpoint.v1"


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


def _is_late_pass_replay_context(value: Any) -> bool:
    replay = value if isinstance(value, dict) else {}
    branch = replay.get("branch_restore") if isinstance(replay.get("branch_restore"), dict) else {}
    restore_point = str(branch.get("restore_point") or "").strip()
    return bool(
        replay.get("replay_kind") == "late_pass_continuation"
        or isinstance(replay.get("late_pass_continuation"), dict)
        or branch.get("activation_scope") == "explicit_late_pass_continuation"
        or (replay.get("replay_kind") == "latent_branch" and restore_point and restore_point != "base_generation_only")
    )


def _normalize_image_preset_latent_checkpoint(value: Any) -> dict[str, Any] | None:
    checkpoint = value if isinstance(value, dict) else {}
    raw_branch = checkpoint.get("branch_restore") if isinstance(checkpoint.get("branch_restore"), dict) else checkpoint
    restore_point = str(raw_branch.get("restore_point") or checkpoint.get("restore_point") or "").strip()
    if not restore_point or restore_point == "base_generation_only":
        return None
    provider_reference = raw_branch.get("provider_reference") if isinstance(raw_branch.get("provider_reference"), dict) else {}
    allowed_passes = raw_branch.get("allowed_passes") if isinstance(raw_branch.get("allowed_passes"), list) else checkpoint.get("allowed_passes")
    allowed_passes = [str(item) for item in (allowed_passes or []) if str(item).strip()]
    branch_restore = {
        **raw_branch,
        "source_result_id": str(raw_branch.get("source_result_id") or checkpoint.get("source_result_id") or "").strip(),
        "restore_point": restore_point,
        "restore_point_label": str(raw_branch.get("restore_point_label") or checkpoint.get("restore_point_label") or restore_point),
        "provider_reference": dict(provider_reference),
        "activation_scope": "ui_preset_reference_only",
        "late_pass_only": True,
        "allowed_passes": allowed_passes,
        "enabled_passes": [],
        "state": "ui_preset_latent_checkpoint_inactive",
    }
    return {
        "schema_version": IMAGE_UI_PRESET_LATENT_CHECKPOINT_SCHEMA,
        "source_result_id": branch_restore["source_result_id"],
        "restore_point": restore_point,
        "restore_point_label": branch_restore["restore_point_label"],
        "provider_id": str(branch_restore.get("provider_id") or ""),
        "backend": str(branch_restore.get("backend") or ""),
        "artifact_id": str(branch_restore.get("artifact_id") or ""),
        "provider_reference": dict(provider_reference),
        "allowed_passes": allowed_passes,
        "branch_restore": branch_restore,
        "activation_scope": "ui_preset_reference_only",
        "state": "saved_in_ui_preset_inactive",
        "requires_explicit_load": True,
        "no_png_reencode": branch_restore.get("no_png_reencode") is not False,
    }


def _sanitize_image_draft_for_ui_preset(value: Any) -> dict[str, Any]:
    draft = copy.deepcopy(value) if isinstance(value, dict) else {}
    replay = draft.get("_replay_context") if isinstance(draft.get("_replay_context"), dict) else {}
    checkpoint = None
    if _is_late_pass_replay_context(replay):
        checkpoint = _normalize_image_preset_latent_checkpoint(replay.get("branch_restore") or replay.get("late_pass_continuation"))
        draft.pop("_replay_context", None)
        draft.pop("_late_pass_continuation", None)
    if checkpoint is None:
        checkpoint = _normalize_image_preset_latent_checkpoint(draft.get("_preset_latent_checkpoint"))
    if checkpoint is not None:
        draft["_preset_latent_checkpoint"] = checkpoint
    else:
        draft.pop("_preset_latent_checkpoint", None)
    return draft


def normalize_snapshot(value: Any, *, surface: str = "") -> dict[str, Any]:
    snapshot = copy.deepcopy(value) if isinstance(value, dict) else {}
    # Preserve extension data when the frontend includes it, but keep the shell stable.
    snapshot.setdefault("extensions", {})
    snapshot.setdefault("extension_settings", {})
    snapshot.setdefault("surface_state", {})
    if str(surface or "").strip().lower() == "image":
        if isinstance(snapshot.get("imageDraft"), dict):
            snapshot["imageDraft"] = _sanitize_image_draft_for_ui_preset(snapshot["imageDraft"])
        surface_state = snapshot.get("surface_state") if isinstance(snapshot.get("surface_state"), dict) else {}
        if isinstance(surface_state.get("imageDraft"), dict):
            surface_state["imageDraft"] = _sanitize_image_draft_for_ui_preset(surface_state["imageDraft"])
        snapshot["surface_state"] = surface_state
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
    record["snapshot"] = normalize_snapshot(record.get("snapshot"), surface=surface)
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
        "snapshot": normalize_snapshot(payload.get("snapshot"), surface=surface),
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
        record["snapshot"] = normalize_snapshot(payload.get("snapshot"), surface=surface)
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
