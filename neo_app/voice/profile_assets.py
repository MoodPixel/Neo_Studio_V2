from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json

from .adapter_client import voice_provider_routing_payload
from .base_contract import VOICE_COMMON_FIELD_IDS, normalize_voice_common_settings
from .output_paths import ROOT_DIR, get_voice_output_paths, sanitize_path_part
from .profile_store import voice_profiles_payload as legacy_voice_profiles_payload
from .reference_clone_runtime import current_reference_payload

VOICE_PROFILE_ASSET_PHASE = "VO-R7"
VOICE_PROFILE_ASSET_SCHEMA = "neo.voice.profile_asset.v1"
VOICE_PROFILE_ASSET_LIST_SCHEMA = "neo.voice.profile_assets.v1"
VOICE_PROFILE_ASSET_DETAIL_SCHEMA = "neo.voice.profile_asset_detail.v1"
VOICE_PROFILE_ASSET_APPLY_SCHEMA = "neo.voice.profile_asset_apply.v1"

PROFILE_ASSET_INDEX = ROOT_DIR / "neo_data" / "outputs" / "voice" / "profiles" / "voice_profile_assets.r7.json"
_PORTABLE_FIELDS = [field for field in VOICE_COMMON_FIELD_IDS if field not in {"script", "model_id", "voice_id"}]
_STORED_COMMON_FIELDS = [field for field in VOICE_COMMON_FIELD_IDS if field != "script"]


class VoiceProfileAssetError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_assets() -> list[dict[str, Any]]:
    if not PROFILE_ASSET_INDEX.exists():
        return []
    try:
        payload = json.loads(PROFILE_ASSET_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("assets") or []
        return items if isinstance(items, list) else []
    return payload if isinstance(payload, list) else []


def _write_assets(items: list[dict[str, Any]]) -> None:
    get_voice_output_paths("profiles", create=True)
    PROFILE_ASSET_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_ASSET_INDEX.write_text(
        json.dumps(
            {
                "schema_id": VOICE_PROFILE_ASSET_LIST_SCHEMA,
                "phase": VOICE_PROFILE_ASSET_PHASE,
                "surface": "voice",
                "authority": "current_voice_profile_asset_store",
                "updated_at": _now(),
                "count": len(items),
                "items": items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _sidecar_path(asset_id: str) -> Path:
    return get_voice_output_paths("profiles", create=True).output_file(f"{sanitize_path_part(asset_id, 'voice_profile_asset')}.profile_asset.r7.json")


def _write_sidecar(asset: dict[str, Any]) -> dict[str, Any]:
    path = _sidecar_path(str(asset.get("asset_id") or "voice_profile_asset"))
    stored = dict(asset)
    stored["metadata_file"] = _relative(path)
    path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    return stored


def _delete_sidecar(asset: dict[str, Any]) -> None:
    raw = str(asset.get("metadata_file") or "").strip()
    path = (ROOT_DIR / raw).resolve() if raw else _sidecar_path(str(asset.get("asset_id") or ""))
    root = get_voice_output_paths("profiles", create=True).output_dir.resolve()
    if path == root or root not in path.parents:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _catalog_ids(block: Any) -> set[str]:
    if not isinstance(block, dict):
        return set()
    return {str(item.get("id") or "").strip() for item in block.get("items") or [] if isinstance(item, dict) and str(item.get("id") or "").strip()}


def _resolved_catalog_value(block: Any, requested: str, *, label: str) -> tuple[str, str]:
    requested = str(requested or "provider_default").strip() or "provider_default"
    ids = _catalog_ids(block)
    if requested not in ids:
        raise VoiceProfileAssetError(f"Selected Voice {label} '{requested}' does not belong to the selected backend profile.")
    resolved = requested
    if requested == "provider_default" and isinstance(block, dict):
        resolved = str(block.get("resolved_default_id") or block.get("default_id") or requested).strip() or requested
    return requested, resolved


def _strict_routing(backend_profile_id: str) -> dict[str, Any]:
    requested = str(backend_profile_id or "").strip()
    if not requested:
        raise VoiceProfileAssetError("backend_profile_id is required for a Voice Profile Asset.")
    routing = voice_provider_routing_payload(requested)
    if routing.get("routing_ready") is not True:
        errors = routing.get("errors") if isinstance(routing.get("errors"), list) else []
        raise VoiceProfileAssetError(str(errors[0] if errors else f"Voice backend profile '{requested}' is unavailable."))
    profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
    if str(profile.get("profile_id") or "") != requested:
        raise VoiceProfileAssetError("Voice Profile Asset routing did not resolve the explicitly selected backend profile.")
    return routing


def _normalized_asset_common(payload: dict[str, Any], routing: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = payload.get("common_settings") if isinstance(payload.get("common_settings"), dict) else payload
    validation = normalize_voice_common_settings(raw, require_script=False)
    common = validation.get("common_settings") if isinstance(validation.get("common_settings"), dict) else {}
    language_state = (routing.get("common_controls") or {}).get("language") if isinstance(routing.get("common_controls"), dict) else {}
    if isinstance(language_state, dict) and language_state.get("mode") == "fixed" and language_state.get("fixed_value"):
        common["language"] = str(language_state.get("fixed_value"))
    model_id, resolved_model = _resolved_catalog_value(routing.get("models"), common.get("model_id") or "provider_default", label="model")
    voice_id, resolved_voice = _resolved_catalog_value(routing.get("voices"), common.get("voice_id") or "provider_default", label="voice")
    common["model_id"] = model_id
    common["voice_id"] = voice_id
    stored = {field: common.get(field) for field in _STORED_COMMON_FIELDS}
    return stored, {
        "resolved_model_id": resolved_model,
        "resolved_voice_id": resolved_voice,
        "warnings": validation.get("warnings") if isinstance(validation.get("warnings"), list) else [],
        "ignored_provider_native_fields": validation.get("provider_native_fields_ignored") if isinstance(validation.get("provider_native_fields_ignored"), list) else [],
    }


def _reference_snapshot(reference_id: str, routing: dict[str, Any]) -> dict[str, Any] | None:
    requested = str(reference_id or "").strip()
    if not requested:
        return None
    caps = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    if caps.get("voice_clone") is not True or caps.get("reference_audio") is not True:
        raise VoiceProfileAssetError("The selected backend profile cannot own a Voice Profile Asset with reference cloning.")
    payload = current_reference_payload(requested)
    reference = payload.get("reference") if isinstance(payload.get("reference"), dict) else None
    if not reference or reference.get("clone_ready") is not True:
        raise VoiceProfileAssetError("Selected reference must be authorized, available, and clone-ready before it can be saved in a Voice Profile Asset.")
    return {
        "reference_id": str(reference.get("reference_id") or requested),
        "label": str(reference.get("label") or "Reference audio"),
        "path": str(reference.get("path") or ""),
        "qc_status": str((reference.get("qc") or {}).get("status") or "") if isinstance(reference.get("qc"), dict) else "",
        "rights_confirmed": bool((reference.get("rights_attestation") or {}).get("confirmed")) if isinstance(reference.get("rights_attestation"), dict) else False,
        "clone_ready": True,
    }


def _compatibility(asset: dict[str, Any], active_backend_profile_id: str | None) -> dict[str, Any]:
    requested = str(active_backend_profile_id or "").strip()
    if not requested:
        return {
            "status": "unknown",
            "application_mode": "requires_active_backend",
            "exact_backend": False,
            "reference_usable": False,
            "message": "Select a Voice backend profile to evaluate this asset.",
        }
    try:
        routing = _strict_routing(requested)
    except VoiceProfileAssetError as exc:
        return {"status": "blocked", "application_mode": "invalid_active_backend", "exact_backend": False, "reference_usable": False, "message": str(exc)}
    exact = requested == str(asset.get("backend_profile_id") or "")
    caps = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    reference_id = str(asset.get("reference_id") or "")
    reference_usable = False
    if reference_id and caps.get("voice_clone") is True and caps.get("reference_audio") is True:
        ref = current_reference_payload(reference_id)
        reference_usable = bool(isinstance(ref.get("reference"), dict) and ref["reference"].get("clone_ready") is True)
    return {
        "status": "compatible",
        "application_mode": "exact_profile" if exact else "portable_common_settings",
        "exact_backend": exact,
        "reference_usable": reference_usable,
        "active_backend_profile_id": requested,
        "message": "Exact profile settings can be restored." if exact else "Portable common settings can be restored; model/voice remain owned by the active backend.",
    }


def _build_asset(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    current = existing if isinstance(existing, dict) else {}
    backend_profile_id = str(data.get("backend_profile_id") or data.get("profile_id") or current.get("backend_profile_id") or "").strip()
    routing = _strict_routing(backend_profile_id)
    profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
    common_input = data.get("common_settings") if isinstance(data.get("common_settings"), dict) else current.get("common_settings") if isinstance(current.get("common_settings"), dict) else data
    common, normalization = _normalized_asset_common({"common_settings": common_input}, routing)
    reference_id = str(data.get("reference_id") if "reference_id" in data else current.get("reference_id") or "").strip()
    reference = _reference_snapshot(reference_id, routing) if reference_id else None
    asset_id = str(current.get("asset_id") or data.get("asset_id") or f"voice_profile_asset_{uuid4().hex[:12]}").strip()
    name = str(data.get("name") or current.get("name") or "Voice Profile").strip()[:120] or "Voice Profile"
    description = str(data.get("description") if "description" in data else current.get("description") or "").strip()[:1000]
    return {
        "schema_id": VOICE_PROFILE_ASSET_SCHEMA,
        "phase": VOICE_PROFILE_ASSET_PHASE,
        "surface": "voice",
        "asset_id": asset_id,
        "asset_kind": "voice_profile",
        "name": name,
        "description": description,
        "backend_profile_id": backend_profile_id,
        "provider_id": str(profile.get("provider_id") or ""),
        "family": str(profile.get("family") or ""),
        "common_settings": common,
        "resolved_selection_snapshot": {
            "model_id": normalization["resolved_model_id"],
            "voice_id": normalization["resolved_voice_id"],
        },
        "reference_id": reference_id,
        "reference": reference,
        "source_kind": "reference_clone" if reference_id else "tts",
        "created_at": str(current.get("created_at") or _now()),
        "updated_at": _now(),
        "status": "ready",
        "policy": {
            "backend_switch": "never_auto_switch",
            "provider_native_fields": "not_stored",
            "script": "not_stored",
            "reference": "optional_current_r6_clone_ready_asset_only",
        },
        "normalization": normalization,
    }


def create_voice_profile_asset_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    asset = _write_sidecar(_build_asset(payload or {}))
    items = [item for item in _read_assets() if str(item.get("asset_id") or "") != asset["asset_id"]]
    items.append(asset)
    _write_assets(items)
    return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": True, "status": "created", "asset": asset}


def voice_profile_assets_payload(limit: int = 100, *, active_backend_profile_id: str | None = None) -> dict[str, Any]:
    requested_limit = max(1, min(int(limit or 100), 300))
    items = list(reversed(_read_assets()))[:requested_limit]
    current_items = [{**item, "compatibility": _compatibility(item, active_backend_profile_id)} for item in items]
    legacy = legacy_voice_profiles_payload(limit=500)
    legacy_items = legacy.get("profiles") if isinstance(legacy.get("profiles"), list) else []
    return {
        "schema_id": VOICE_PROFILE_ASSET_LIST_SCHEMA,
        "phase": VOICE_PROFILE_ASSET_PHASE,
        "surface": "voice",
        "authority": "current_voice_profile_asset_store",
        "count": len(current_items),
        "items": current_items,
        "active_backend_profile_id": str(active_backend_profile_id or ""),
        "legacy_compatibility": {
            "schema_id": str(legacy.get("schema_id") or "neo.voice.profile_index.v7"),
            "count": len(legacy_items),
            "policy": "legacy_v7_profiles_are_not_auto_promoted_or_auto_applied",
        },
    }


def voice_profile_asset_payload(asset_id: str, *, active_backend_profile_id: str | None = None) -> dict[str, Any]:
    asset = next((item for item in _read_assets() if str(item.get("asset_id") or "") == str(asset_id or "")), None)
    if not asset:
        return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": False, "status": "missing_asset", "asset_id": asset_id}
    return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": True, "status": "ready", "asset": {**asset, "compatibility": _compatibility(asset, active_backend_profile_id)}}


def update_voice_profile_asset_payload(asset_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    items = _read_assets()
    existing = next((item for item in items if str(item.get("asset_id") or "") == str(asset_id or "")), None)
    if not existing:
        return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": False, "status": "missing_asset", "asset_id": asset_id}
    data = dict(payload or {})
    data["asset_id"] = asset_id
    asset = _write_sidecar(_build_asset(data, existing=existing))
    items = [asset if str(item.get("asset_id") or "") == asset_id else item for item in items]
    _write_assets(items)
    return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": True, "status": "updated", "asset": asset}


def delete_voice_profile_asset_payload(asset_id: str) -> dict[str, Any]:
    items = _read_assets()
    existing = next((item for item in items if str(item.get("asset_id") or "") == str(asset_id or "")), None)
    if not existing:
        return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": False, "status": "missing_asset", "asset_id": asset_id}
    kept = [item for item in items if str(item.get("asset_id") or "") != asset_id]
    _write_assets(kept)
    _delete_sidecar(existing)
    return {"schema_id": VOICE_PROFILE_ASSET_DETAIL_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": True, "status": "deleted", "asset_id": asset_id}


def apply_voice_profile_asset_payload(asset_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    active_backend_profile_id = str(data.get("backend_profile_id") or data.get("active_backend_profile_id") or data.get("profile_id") or "").strip()
    detail = voice_profile_asset_payload(asset_id, active_backend_profile_id=active_backend_profile_id)
    asset = detail.get("asset") if isinstance(detail.get("asset"), dict) else None
    if not asset:
        return {"schema_id": VOICE_PROFILE_ASSET_APPLY_SCHEMA, "phase": VOICE_PROFILE_ASSET_PHASE, "ok": False, "status": "missing_asset", "asset_id": asset_id}
    routing = _strict_routing(active_backend_profile_id)
    exact = active_backend_profile_id == str(asset.get("backend_profile_id") or "")
    stored = asset.get("common_settings") if isinstance(asset.get("common_settings"), dict) else {}
    warnings: list[str] = []

    if exact:
        draft = dict(stored)
        try:
            _resolved_catalog_value(routing.get("models"), draft.get("model_id") or "provider_default", label="model")
        except VoiceProfileAssetError:
            draft["model_id"] = "provider_default"
            warnings.append("The saved model is no longer in the active backend catalog; Provider Default was used.")
        try:
            _resolved_catalog_value(routing.get("voices"), draft.get("voice_id") or "provider_default", label="voice")
        except VoiceProfileAssetError:
            draft["voice_id"] = "provider_default"
            warnings.append("The saved voice is no longer in the active backend catalog; Provider Default was used.")
        application_mode = "exact_profile"
    else:
        draft = {field: stored.get(field) for field in _PORTABLE_FIELDS}
        draft["model_id"] = "provider_default"
        draft["voice_id"] = "provider_default"
        application_mode = "portable_common_settings"
        warnings.append("The Voice Profile Asset belongs to another backend. Neo kept the active backend and reset Model/Voice to Provider Default.")

    language_state = (routing.get("common_controls") or {}).get("language") if isinstance(routing.get("common_controls"), dict) else {}
    if isinstance(language_state, dict) and language_state.get("mode") == "fixed" and language_state.get("fixed_value"):
        fixed = str(language_state.get("fixed_value"))
        if draft.get("language") != fixed:
            warnings.append(f"Language was adjusted to the active backend's fixed locale ({fixed}).")
        draft["language"] = fixed

    reference_id = str(asset.get("reference_id") or "")
    applied_reference_id = ""
    target_workspace = "generation"
    if reference_id:
        caps = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
        ref = current_reference_payload(reference_id)
        current_ref = ref.get("reference") if isinstance(ref.get("reference"), dict) else None
        if caps.get("voice_clone") is True and caps.get("reference_audio") is True and current_ref and current_ref.get("clone_ready") is True:
            applied_reference_id = reference_id
            target_workspace = "reference"
        else:
            warnings.append("The saved reference was not applied because the active backend cannot use it or the reference is no longer clone-ready.")

    return {
        "schema_id": VOICE_PROFILE_ASSET_APPLY_SCHEMA,
        "phase": VOICE_PROFILE_ASSET_PHASE,
        "ok": True,
        "status": "apply_ready",
        "asset_id": str(asset.get("asset_id") or asset_id),
        "asset_name": str(asset.get("name") or "Voice Profile"),
        "source_backend_profile_id": str(asset.get("backend_profile_id") or ""),
        "active_backend_profile_id": active_backend_profile_id,
        "application_mode": application_mode,
        "backend_switch_policy": "never_auto_switch",
        "common_settings": draft,
        "reference_id": applied_reference_id,
        "target_workspace": target_workspace,
        "warnings": warnings,
    }


def voice_profile_asset_lineage(asset_id: str | None, *, applied_backend_profile_id: str) -> dict[str, Any] | None:
    requested = str(asset_id or "").strip()
    if not requested:
        return None
    detail = voice_profile_asset_payload(requested, active_backend_profile_id=applied_backend_profile_id)
    asset = detail.get("asset") if isinstance(detail.get("asset"), dict) else None
    if not asset:
        raise VoiceProfileAssetError(f"Voice Profile Asset '{requested}' no longer exists.")
    compatibility = asset.get("compatibility") if isinstance(asset.get("compatibility"), dict) else {}
    return {
        "asset_id": requested,
        "name": str(asset.get("name") or "Voice Profile"),
        "source_backend_profile_id": str(asset.get("backend_profile_id") or ""),
        "applied_backend_profile_id": str(applied_backend_profile_id or ""),
        "application_mode": str(compatibility.get("application_mode") or ("exact_profile" if asset.get("backend_profile_id") == applied_backend_profile_id else "portable_common_settings")),
        "source_kind": str(asset.get("source_kind") or "tts"),
        "reference_id": str(asset.get("reference_id") or ""),
    }
