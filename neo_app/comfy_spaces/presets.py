"""NCS-14: local value-only Comfy Space presets; no execution/topology authority."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import Field, field_validator

from neo_app.comfy_spaces.contract import load_space_manifest
from neo_app.comfy_spaces.paths import comfy_space_manifest_path, comfy_space_preset_path, comfy_spaces_presets_root
from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.comfy_spaces.variants import (
    _SPACE_ID_RE, _VERSION_RE, _SHA256_RE, _publication_receipt, _sanitize_saved_values,
    SpaceVariantError,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict

SCHEMA_VERSION = "neo.comfy_space.preset.v1"


class SpacePresetError(ValueError):
    def __init__(self, message: str, *, code: str = "space_preset_invalid") -> None:
        super().__init__(message)
        self.code = code


class ComfySpacePreset(StrictContractModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    kind: Literal["comfy_space_preset"] = "comfy_space_preset"
    preset_id: str
    name: str
    description: str = ""
    scope: Literal["user", "space"] = "user"
    base_space_id: str
    base_version: str
    base_publication_fingerprint: str
    created_at: str
    updated_at: str
    saved_values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("preset_id", "base_space_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        value = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(value):
            raise ValueError("Invalid preset/Space identifier")
        return value

    @field_validator("base_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if not _VERSION_RE.fullmatch(value):
            raise ValueError("Invalid Space version")
        return value

    @field_validator("base_publication_fingerprint")
    @classmethod
    def valid_fingerprint(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Invalid publication fingerprint")
        return value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value or len(value) > 120:
            raise ValueError("Preset name must be 1–120 characters")
        return value

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str) -> str:
        if len(value) > 1000:
            raise ValueError("Preset description must not exceed 1000 characters")
        return value


def _base(space_id: str, version: str, root_dir=None) -> str:
    if not _SPACE_ID_RE.fullmatch(space_id) or not _VERSION_RE.fullmatch(version):
        raise SpacePresetError("Invalid Space identity")
    try:
        manifest = load_space_manifest(comfy_space_manifest_path(space_id, version, root_dir))
        if manifest.status != "published":
            raise SpacePresetError("Presets require a published Space")
        _, fingerprint = _publication_receipt(space_id, version, root_dir=root_dir)
        return fingerprint
    except SpacePresetError:
        raise
    except Exception as exc:
        raise SpacePresetError("Published Space is missing or invalid", code="space_preset_base_unavailable") from exc


def _values(space_id: str, version: str, values: Mapping[str, Any], root_dir=None) -> dict[str, Any]:
    try:
        result = _sanitize_saved_values(space_id, version, values, root_dir=root_dir)
    except (SpaceVariantError, TypeError, ValueError) as exc:
        raise SpacePresetError(str(exc), code="space_preset_values_invalid") from exc
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 262144:
        raise SpacePresetError("Preset values exceed 256 KiB")
    return result


def preset_payload(preset: ComfySpacePreset, *, root_dir=None) -> dict[str, Any]:
    data = model_to_dict(preset)
    try:
        fingerprint = _base(preset.base_space_id, preset.base_version, root_dir)
        if fingerprint != preset.base_publication_fingerprint:
            raise SpacePresetError("Published Space fingerprint changed")
        _values(preset.base_space_id, preset.base_version, preset.saved_values, root_dir)
        data.update(status="ready", ready=True, reason="")
    except SpacePresetError as exc:
        data.update(status="stale", ready=False, reason=str(exc))
    data["saved_value_count"] = len(preset.saved_values)
    return data


def create_space_preset(*, name: str, base_space_id: str, base_version: str,
                        saved_values: Mapping[str, Any], description: str = "",
                        scope: str = "user", root_dir=None) -> dict[str, Any]:
    space_id = str(base_space_id or "").strip().lower()
    version = str(base_version or "").strip()
    fingerprint = _base(space_id, version, root_dir)
    if scope not in {"user", "space"}:
        raise SpacePresetError("Invalid preset scope")
    values = _values(space_id, version, saved_values, root_dir)
    now = datetime.now(timezone.utc).isoformat()
    try:
        preset = ComfySpacePreset(preset_id=f"preset_{uuid4().hex}", name=name,
            description=description, scope=scope, base_space_id=space_id, base_version=version,
            base_publication_fingerprint=fingerprint, created_at=now, updated_at=now,
            saved_values=values)
    except ValueError as exc:
        raise SpacePresetError(str(exc)) from exc
    root = comfy_spaces_presets_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = comfy_space_preset_path(preset.preset_id, root_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model_to_dict(preset), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return preset_payload(preset, root_dir=root_dir)


def load_space_preset(preset_id: str, *, root_dir=None, require_ready: bool = False) -> ComfySpacePreset:
    path = comfy_space_preset_path(preset_id, root_dir)
    try:
        preset = model_from_dict(ComfySpacePreset, json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise SpacePresetError("Preset not found", code="space_preset_not_found") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise SpacePresetError("Preset data is invalid") from exc
    if require_ready and not preset_payload(preset, root_dir=root_dir)["ready"]:
        raise SpacePresetError("Preset is stale; choose an exact compatible Space version", code="space_preset_stale")
    return preset


def list_space_presets(*, space_id: str = "", version: str = "", root_dir=None) -> dict[str, Any]:
    items = []
    root = comfy_spaces_presets_root(root_dir)
    for path in root.glob("preset_*.json") if root.is_dir() else []:
        try:
            preset = model_from_dict(ComfySpacePreset, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            continue
        if space_id and preset.base_space_id != space_id:
            continue
        if version and preset.base_version != version:
            continue
        items.append(preset_payload(preset, root_dir=root_dir))
    items.sort(key=lambda item: (item["name"].casefold(), item["preset_id"]))
    return {"ok": True, "schema_version": "neo.comfy_space.preset_collection.v1", "presets": items, "count": len(items)}


def delete_space_preset(preset_id: str, *, root_dir=None) -> dict[str, Any]:
    preset = load_space_preset(preset_id, root_dir=root_dir)
    comfy_space_preset_path(preset.preset_id, root_dir).unlink()
    return {"ok": True, "deleted": True, "preset_id": preset.preset_id}


def resolve_space_preset(preset_id: str, *, space_id: str, version: str, root_dir=None) -> dict[str, Any]:
    preset = load_space_preset(preset_id, root_dir=root_dir, require_ready=True)
    if preset.base_space_id != space_id or preset.base_version != version:
        raise SpacePresetError("Preset is for a different Space version", code="space_preset_base_mismatch")
    return preset_payload(preset, root_dir=root_dir)
