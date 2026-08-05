from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from neo_app.image.sampling_guidance_registry import (
    normalize_family,
    normalize_loader,
    normalize_mode,
    resolve_sampling_guidance_capability,
)

SCHEMA = "neo.image.sampling_preset.user.v1"
PHASE = "IP-7"
SURFACE_ID = "image_sampling"
ROOT_DIR = Path(__file__).resolve().parents[2]
USER_PRESET_ROOT = ROOT_DIR / "neo_data" / "image" / "sampling_presets"
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,96}$")

_INTEGER_FIELDS = {"width", "height", "steps", "seed", "requested_seed", "actual_seed"}
_FLOAT_FIELDS = {"cfg", "true_cfg", "flux_guidance", "guidance", "guidance_scale", "model_guidance", "denoise"}
_STRING_FIELDS = {"sampler", "scheduler"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _slug(value: Any) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return clean[:48] or "sampling-preset"


def _safe_path(preset_id: Any) -> Path:
    value = str(preset_id or "").strip()
    if not VALID_ID.fullmatch(value):
        raise ValueError("Invalid image sampling preset id")
    root = USER_PRESET_ROOT.resolve()
    path = (root / f"{value}.json").resolve()
    if root not in path.parents:
        raise ValueError("Invalid image sampling preset path")
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - malformed user data must fail closed.
        raise ValueError(f"Could not read image sampling preset: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Image sampling preset must be a JSON object: {path.name}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def normalize_user_sampling_context(value: Mapping[str, Any] | None) -> dict[str, str]:
    raw = dict(value or {})
    family = normalize_family(raw.get("family"))
    loader = normalize_loader(raw.get("loader"))
    mode = normalize_mode(raw.get("mode") or raw.get("workflow") or "txt2img")
    raw_variant = raw.get("variant") or raw.get("flux_variant") or raw.get("krea2_variant") or ""
    model_name = raw.get("model_name") or raw.get("model") or ""
    capability = resolve_sampling_guidance_capability(
        family,
        loader=loader,
        mode=mode,
        variant=raw_variant,
        model_name=model_name,
    )
    variant = _token(capability.get("variant") or raw_variant) or "*"
    return {
        "family": str(capability.get("family") or family),
        "variant": variant,
        "loader": str(capability.get("loader") or loader),
        "mode": str(capability.get("mode") or mode),
        # Output Intent is deliberately separate in IP-6/IP-7. User sampling
        # presets are portable across None / Realistic / Anime-Illustration.
        "intent": "*",
    }


def _coerce_sampling_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if field in _INTEGER_FIELDS:
        try:
            return int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sampling preset field {field!r} must be an integer.") from exc
    if field in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sampling preset field {field!r} must be numeric.") from exc
    if field in _STRING_FIELDS:
        return str(value).strip()
    return deepcopy(value)


def sanitize_user_sampling_values(value: Mapping[str, Any] | None) -> dict[str, Any]:
    # Lazy import avoids making user-preset storage part of the built-in registry
    # import cycle. The built-in registry remains the authority for managed fields.
    from neo_app.image.sampling_presets import managed_sampling_fields

    raw = dict(value or {})
    allowed = managed_sampling_fields()
    foreign = sorted(str(key) for key in raw.keys() if str(key) not in allowed)
    if foreign:
        raise ValueError(f"User sampling presets can only store sampling fields; unsupported fields: {foreign}")
    clean: dict[str, Any] = {}
    for field, raw_value in raw.items():
        normalized = _coerce_sampling_value(str(field), raw_value)
        if normalized not in (None, ""):
            clean[str(field)] = normalized
    return clean


def _validate_record(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != SCHEMA:
        raise ValueError(f"Unexpected user sampling preset schema: {record.get('schema_version')!r}")
    preset_id = str(record.get("preset_id") or "")
    if not VALID_ID.fullmatch(preset_id):
        raise ValueError("User sampling preset has an invalid preset_id")
    if record.get("surface") != SURFACE_ID:
        raise ValueError("User sampling preset has the wrong surface id")
    if record.get("source") != "user" or record.get("immutable") is not False:
        raise ValueError("User sampling preset must be source=user and immutable=false")
    context = normalize_user_sampling_context(record.get("context") if isinstance(record.get("context"), dict) else {})
    if not context["family"] or not context["loader"] or not context["mode"]:
        raise ValueError("User sampling preset requires family, loader, and workflow context")
    sanitize_user_sampling_values(record.get("values") if isinstance(record.get("values"), dict) else {})
    if record.get("output_intent") not in (None, "", "*"):
        raise ValueError("Output Intent is separate from user sampling presets in IP-7")


def _summary(record: Mapping[str, Any]) -> dict[str, Any]:
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    return {
        "preset_id": record.get("preset_id", ""),
        "name": record.get("name", "Untitled preset"),
        "surface": SURFACE_ID,
        "description": record.get("description", ""),
        "source": "user",
        "immutable": False,
        "is_default": False,
        "context": deepcopy(record.get("context") or {}),
        "base_preset_id": record.get("base_preset_id", ""),
        "value_fields": sorted(str(key) for key in values),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
    }


def _record_with_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(record))
    payload["snapshot"] = {
        "context": deepcopy(payload.get("context") or {}),
        "values": deepcopy(payload.get("values") or {}),
        "base_preset_id": payload.get("base_preset_id", ""),
    }
    payload["is_default"] = False
    return payload


def list_user_sampling_presets() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if USER_PRESET_ROOT.exists():
        for path in sorted(USER_PRESET_ROOT.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            record = _read_json(path)
            if not record:
                continue
            try:
                _validate_record(record)
            except ValueError:
                # Do not let one malformed portable user file hide every valid
                # preset. Surface it as skipped diagnostics instead.
                continue
            items.append(_summary(record))
    return {
        "schema_version": "neo.image.sampling_preset.user_index.v1",
        "phase": PHASE,
        "surface": SURFACE_ID,
        "namespace": "neo_data/image/sampling_presets",
        "default_preset_id": "",
        "presets": items,
    }


def get_user_sampling_preset(preset_id: Any) -> dict[str, Any]:
    path = _safe_path(preset_id)
    record = _read_json(path)
    if not record:
        raise FileNotFoundError("Image sampling preset not found")
    _validate_record(record)
    return _record_with_snapshot(record)


def _payload_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    return deepcopy(snapshot)


def create_user_sampling_preset(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(payload or {})
    name = str(raw.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Image sampling preset name is required")
    snapshot = _payload_snapshot(raw)
    context_raw = raw.get("context") if isinstance(raw.get("context"), dict) else snapshot.get("context")
    values_raw = raw.get("values") if isinstance(raw.get("values"), dict) else snapshot.get("values")
    context = normalize_user_sampling_context(context_raw if isinstance(context_raw, dict) else {})
    values = sanitize_user_sampling_values(values_raw if isinstance(values_raw, dict) else {})
    if not context["family"] or not context["loader"] or not context["mode"]:
        raise ValueError("Image sampling preset requires family, loader, and workflow context")
    preset_id = f"user_{_slug(name)}_{uuid4().hex[:8]}".replace("-", "_")
    now = _now()
    record = {
        "schema_version": SCHEMA,
        "phase": PHASE,
        "preset_id": preset_id,
        "surface": SURFACE_ID,
        "name": name,
        "description": str(raw.get("description") or "").strip()[:500],
        "source": "user",
        "immutable": False,
        "application_mode": "replace_sampling_fields",
        "authoring_template": False,
        "context": context,
        "values": values,
        "base_preset_id": str(raw.get("base_preset_id") or snapshot.get("base_preset_id") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    _validate_record(record)
    _write_json(_safe_path(preset_id), record)
    return get_user_sampling_preset(preset_id)


def update_user_sampling_preset(preset_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    current = get_user_sampling_preset(preset_id)
    current.pop("snapshot", None)
    current.pop("is_default", None)
    raw = dict(payload or {})
    if "name" in raw:
        name = str(raw.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("Image sampling preset name cannot be empty")
        current["name"] = name
    if "description" in raw:
        current["description"] = str(raw.get("description") or "").strip()[:500]
    if "snapshot" in raw or "context" in raw or "values" in raw:
        snapshot = _payload_snapshot(raw)
        if "context" in raw or "context" in snapshot:
            source = raw.get("context") if isinstance(raw.get("context"), dict) else snapshot.get("context")
            current["context"] = normalize_user_sampling_context(source if isinstance(source, dict) else {})
        if "values" in raw or "values" in snapshot:
            source = raw.get("values") if isinstance(raw.get("values"), dict) else snapshot.get("values")
            current["values"] = sanitize_user_sampling_values(source if isinstance(source, dict) else {})
        if "base_preset_id" in raw or "base_preset_id" in snapshot:
            current["base_preset_id"] = str(raw.get("base_preset_id") or snapshot.get("base_preset_id") or "").strip()
    current["updated_at"] = _now()
    _validate_record(current)
    _write_json(_safe_path(preset_id), current)
    return get_user_sampling_preset(preset_id)


def delete_user_sampling_preset(preset_id: Any) -> dict[str, Any]:
    path = _safe_path(preset_id)
    if path.exists():
        path.unlink()
    return {"ok": True, "deleted": str(preset_id), **list_user_sampling_presets()}


def user_sampling_preset_matches_context(record_context: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    saved = normalize_user_sampling_context(record_context)
    active = normalize_user_sampling_context(context)
    if saved["family"] != active["family"] or saved["loader"] != active["loader"] or saved["mode"] != active["mode"]:
        return False
    return saved["variant"] in {"*", active["variant"]}


def resolve_user_sampling_preset(
    preset_id: Any,
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> dict[str, Any] | None:
    try:
        record = get_user_sampling_preset(preset_id)
    except FileNotFoundError:
        return None
    context = normalize_user_sampling_context({
        "family": family,
        "loader": loader,
        "mode": mode,
        "variant": variant,
        "model_name": model_name,
    })
    if not user_sampling_preset_matches_context(record.get("context") or {}, context):
        return None
    values = sanitize_user_sampling_values(record.get("values") if isinstance(record.get("values"), dict) else {})
    return {
        "preset_id": record.get("preset_id"),
        "entry_id": f"user:{record.get('preset_id')}",
        "label": record.get("name"),
        "description": record.get("description", ""),
        "category": "my_presets",
        "source": "user",
        "immutable": False,
        "application_mode": "replace_sampling_fields",
        "authoring_template": False,
        "values": values,
        "local_values": deepcopy(values),
        "match": deepcopy(record.get("context") or {}),
        "inheritance": {
            "inherited": False,
            "chain": [f"user:{record.get('preset_id')}"],
            "drop_fields": [],
        },
        "user_record": {
            "preset_id": record.get("preset_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "base_preset_id": record.get("base_preset_id", ""),
        },
    }


def user_sampling_preset_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "surface": SURFACE_ID,
        "namespace": "neo_data/image/sampling_presets",
        "api_endpoint": "/api/ui-presets/image_sampling",
        "built_in_storage": "repository_immutable",
        "user_storage": "portable_json_files",
        "output_intent_separate": True,
        "supported_actions": ["save_as", "duplicate", "rename", "delete", "reset_apply"],
        "final_release_lock_phase": "IP-8",
        "inspector_phase": "IP-8",
    }
