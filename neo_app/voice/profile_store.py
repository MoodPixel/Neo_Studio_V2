from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json

from .output_paths import get_voice_output_paths, sanitize_path_part
from .reference_audio import reference_record

VOICE_PROFILE_SCHEMA = "neo.voice.profile.v7"
VOICE_PROFILE_INDEX_SCHEMA = "neo.voice.profile_index.v7"
ROOT = Path(__file__).resolve().parents[2]
PROFILE_INDEX = ROOT / "neo_data" / "outputs" / "voice" / "profiles" / "voice_profiles.v7.json"
LEGACY_PROFILE_INDEX = ROOT / "neo_data" / "outputs" / "voice" / "profiles" / "voice_profiles.v6.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_profiles() -> list[dict[str, Any]]:
    source = PROFILE_INDEX if PROFILE_INDEX.exists() else LEGACY_PROFILE_INDEX
    if not source.exists():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            records = data.get("profiles") or []
            return records if isinstance(records, list) else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_profiles(profiles: list[dict[str, Any]]) -> None:
    get_voice_output_paths("profiles", create=True)
    payload = {
        "schema_id": VOICE_PROFILE_INDEX_SCHEMA,
        "surface": "voice",
        "updated_at": _now(),
        "count": len(profiles),
        "profiles": profiles,
    }
    PROFILE_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_INDEX.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _profile_sidecar_path(profile_id: str) -> Path:
    profile_paths = get_voice_output_paths("profiles", create=True)
    return profile_paths.output_file(f"{sanitize_path_part(profile_id, 'voice_profile')}.profile.v7.json")


def _write_profile_sidecar(profile: dict[str, Any]) -> dict[str, Any]:
    sidecar = _profile_sidecar_path(str(profile.get("profile_id") or "voice_profile"))
    sidecar.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    profile["metadata_file"] = _relative_to_root(sidecar)
    return profile


def normalize_profile_payload(payload: dict[str, Any] | None = None, *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    current = dict(existing or {})
    voice_source = data.get("voice_source") if isinstance(data.get("voice_source"), dict) else dict(current.get("voice_source") or {})
    params = data.get("params") if isinstance(data.get("params"), dict) else dict(current.get("default_params") or {})
    profile_id = str(current.get("profile_id") or data.get("profile_id") or f"voice_profile_{uuid4().hex[:12]}").strip()
    reference_id = str(data.get("reference_id") or voice_source.get("reference_id") or current.get("reference_id") or "").strip()
    reference = reference_record(reference_id) if reference_id else None
    source_type = str(voice_source.get("type") or data.get("source_type") or current.get("source_type") or ("reference_clone" if reference_id else "built_in")).strip() or "built_in"
    name = str(data.get("name") or data.get("profile_name") or current.get("name") or voice_source.get("label") or data.get("label") or "Saved Voice Profile").strip()
    profile = {
        "schema_id": VOICE_PROFILE_SCHEMA,
        "surface": "voice",
        "profile_id": profile_id,
        "name": name,
        "description": str(data.get("description") or current.get("description") or "").strip(),
        "backend": str(data.get("backend") or current.get("backend") or data.get("runtime") or "chatterbox"),
        "family": str(data.get("family") or current.get("family") or "chatterbox_turbo"),
        "model_id": str(data.get("model_id") or current.get("model_id") or data.get("family") or "chatterbox_turbo"),
        "runtime": str(data.get("runtime") or current.get("runtime") or data.get("backend") or "chatterbox"),
        "language": str(data.get("language") or current.get("language") or "en"),
        "tone_tags": data.get("tone_tags") if isinstance(data.get("tone_tags"), list) else list(current.get("tone_tags") or []),
        "source_type": source_type,
        "voice_source": voice_source,
        "reference_id": reference_id,
        "reference_audio": voice_source.get("reference_audio") or (reference or {}).get("path") or current.get("reference_audio") or "",
        "reference_qc": voice_source.get("reference_qc") or (reference or {}).get("qc") or current.get("reference_qc") or None,
        "default_params": params,
        "preview_text": str(data.get("preview_text") or current.get("preview_text") or data.get("script") or "Hello from this saved Neo Voice profile.")[:800],
        "created_at": current.get("created_at") or _now(),
        "updated_at": _now(),
        "status": "ready",
    }
    if current.get("metadata_file"):
        profile["metadata_file"] = current.get("metadata_file")
    return profile


def create_voice_profile_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles = _read_profiles()
    profile = normalize_profile_payload(payload)
    profile = _write_profile_sidecar(profile)
    profiles = [item for item in profiles if item.get("profile_id") != profile.get("profile_id")]
    profiles.append(profile)
    _write_profiles(profiles)
    return {"ok": True, "status": "saved", "profile": profile, "profiles": profiles, "schema_id": VOICE_PROFILE_INDEX_SCHEMA}


def voice_profiles_payload(limit: int = 200) -> dict[str, Any]:
    profiles = list(reversed(_read_profiles()))[: max(1, min(int(limit or 200), 500))]
    return {"ok": True, "schema_id": VOICE_PROFILE_INDEX_SCHEMA, "surface": "voice", "count": len(profiles), "profiles": profiles}


def voice_profile_payload(profile_id: str) -> dict[str, Any]:
    profile = next((item for item in _read_profiles() if item.get("profile_id") == profile_id), None)
    if not profile:
        return {"ok": False, "status": "missing_profile", "profile_id": profile_id}
    return {"ok": True, "status": "ready", "profile": profile}


def update_voice_profile_payload(profile_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles = _read_profiles()
    existing = next((item for item in profiles if item.get("profile_id") == profile_id), None)
    if not existing:
        return {"ok": False, "status": "missing_profile", "profile_id": profile_id}
    data = dict(payload or {})
    data["profile_id"] = profile_id
    profile = normalize_profile_payload(data, existing=existing)
    profile = _write_profile_sidecar(profile)
    profiles = [profile if item.get("profile_id") == profile_id else item for item in profiles]
    _write_profiles(profiles)
    return {"ok": True, "status": "updated", "profile": profile, "profiles": profiles}


def delete_voice_profile_payload(profile_id: str) -> dict[str, Any]:
    profiles = _read_profiles()
    kept = [item for item in profiles if item.get("profile_id") != profile_id]
    if len(kept) == len(profiles):
        return {"ok": False, "status": "missing_profile", "profile_id": profile_id}
    _write_profiles(kept)
    return {"ok": True, "status": "deleted", "profile_id": profile_id, "profiles": kept}


def resolve_voice_profile(profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    return next((item for item in _read_profiles() if item.get("profile_id") == profile_id), None)
