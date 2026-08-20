from __future__ import annotations

from copy import deepcopy
from typing import Any

from .provider_routing import VoiceProfileRoutingError, resolve_voice_profile

VOICE_PROVIDER_CONTROLS_SCHEMA = "neo.voice.provider_controls.v1"
VOICE_PROVIDER_CONTROLS_PHASE = "VO-R8"
_ALLOWED_TYPES = {"number", "integer", "boolean", "text", "tags", "json"}
_ALLOWED_MODES = {"tts", "voice_clone"}
_RESERVED_NATIVE_KEYS = {
    "script", "text", "model", "model_id", "voice", "voice_id", "language", "profile_id", "provider_id",
    "reference_audio", "reference_id", "output_format", "job_id", "surface", "mode", "params",
}


class VoiceProviderControlsError(ValueError):
    pass


def _as_capabilities(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}


def _as_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}


def _definitions(profile: dict[str, Any]) -> list[dict[str, Any]]:
    raw = profile.get("provider_controls") if isinstance(profile.get("provider_controls"), list) else []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        control_id = str(item.get("id") or "").strip()
        control_type = str(item.get("type") or "").strip().lower()
        if not control_id or control_id in seen or control_type not in _ALLOWED_TYPES:
            continue
        seen.add(control_id)
        modes = [str(value).strip() for value in (item.get("modes") if isinstance(item.get("modes"), list) else ["tts", "voice_clone"]) if str(value).strip() in _ALLOWED_MODES]
        output.append({**deepcopy(item), "id": control_id, "type": control_type, "modes": modes or ["tts", "voice_clone"]})
    return output


def _is_supported(definition: dict[str, Any], capabilities: dict[str, Any], mode: str) -> bool:
    if mode not in definition.get("modes", []):
        return False
    requires = str(definition.get("requires_capability") or "tts").strip()
    return capabilities.get(requires) is True


def _normalize_json(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise VoiceProviderControlsError("Backend-native extras must be a JSON object.")
    if len(value) > 32:
        raise VoiceProviderControlsError("Backend-native extras may contain at most 32 keys.")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key or "").strip()
        if not name or name in _RESERVED_NATIVE_KEYS or len(name) > 64:
            raise VoiceProviderControlsError(f"Backend-native key '{name or '<empty>'}' is reserved or invalid.")
        if isinstance(item, (str, int, float, bool)) or item is None:
            clean[name] = item
        elif isinstance(item, list) and len(item) <= 32 and all(isinstance(v, (str, int, float, bool)) or v is None for v in item):
            clean[name] = item
        else:
            raise VoiceProviderControlsError(f"Backend-native value '{name}' must be a scalar or a short scalar list.")
    return clean


def _normalize_value(definition: dict[str, Any], value: Any) -> Any:
    kind = definition["type"]
    label = str(definition.get("label") or definition["id"])
    if kind == "number":
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise VoiceProviderControlsError(f"{label} must be numeric.") from exc
        if definition.get("min") is not None and result < float(definition["min"]):
            raise VoiceProviderControlsError(f"{label} must be at least {definition['min']}.")
        if definition.get("max") is not None and result > float(definition["max"]):
            raise VoiceProviderControlsError(f"{label} must be at most {definition['max']}.")
        return result
    if kind == "integer":
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise VoiceProviderControlsError(f"{label} must be an integer.") from exc
        if definition.get("min") is not None and result < int(definition["min"]):
            raise VoiceProviderControlsError(f"{label} must be at least {definition['min']}.")
        if definition.get("max") is not None and result > int(definition["max"]):
            raise VoiceProviderControlsError(f"{label} must be at most {definition['max']}.")
        return result
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off", ""}:
            return False
        raise VoiceProviderControlsError(f"{label} must be true or false.")
    if kind == "tags":
        values = value if isinstance(value, list) else [part.strip() for part in str(value or "").split(",")]
        result = [str(item).strip()[:80] for item in values if str(item).strip()][:24]
        return result
    if kind == "json":
        return _normalize_json(value)
    text = str(value or "")
    max_length = int(definition.get("max_length") or 500)
    if len(text) > max_length:
        raise VoiceProviderControlsError(f"{label} must be {max_length} characters or fewer.")
    return text


def build_voice_provider_controls(profile: dict[str, Any], *, mode: str = "tts") -> dict[str, Any]:
    mode = mode if mode in _ALLOWED_MODES else "tts"
    caps = _as_capabilities(profile)
    defaults = _as_defaults(profile)
    controls = []
    for definition in _definitions(profile):
        supported = _is_supported(definition, caps, mode)
        if not supported:
            continue
        control_id = definition["id"]
        default = deepcopy(defaults.get(control_id, definition.get("default")))
        controls.append({
            **definition,
            "default": default,
            "visible": True,
            "enabled": True,
            "authority": "selected_backend_profile",
        })
    return {
        "schema_id": VOICE_PROVIDER_CONTROLS_SCHEMA,
        "phase": VOICE_PROVIDER_CONTROLS_PHASE,
        "surface": "voice",
        "mode": mode,
        "profile_id": str(profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "status": "ready",
        "authority": "selected_backend_profile_capability_manifest",
        "controls": controls,
        "control_ids": [item["id"] for item in controls],
        "defaults": {item["id"]: deepcopy(item.get("default")) for item in controls if item.get("default") is not None},
        "common_contract_isolated": True,
        "provider_native_passthrough": "nested_provider_controls_only",
    }


def voice_provider_controls_payload(profile_id: str | None = None, *, mode: str = "tts") -> dict[str, Any]:
    try:
        profile = resolve_voice_profile(profile_id)
    except VoiceProfileRoutingError as exc:
        return {
            "schema_id": VOICE_PROVIDER_CONTROLS_SCHEMA,
            "phase": VOICE_PROVIDER_CONTROLS_PHASE,
            "surface": "voice",
            "mode": mode if mode in _ALLOWED_MODES else "tts",
            "profile_id": str(profile_id or ""),
            "status": "invalid_profile",
            "authority": "selected_backend_profile_capability_manifest",
            "controls": [],
            "control_ids": [],
            "defaults": {},
            "errors": [str(exc)],
        }
    return build_voice_provider_controls(profile, mode=mode)


def normalize_voice_provider_controls(profile: dict[str, Any], raw: Any, *, mode: str = "tts") -> dict[str, Any]:
    contract = build_voice_provider_controls(profile, mode=mode)
    data = raw if isinstance(raw, dict) else {}
    definitions = {item["id"]: item for item in contract["controls"]}
    errors: list[dict[str, str]] = []
    normalized: dict[str, Any] = {}
    unknown = sorted(set(str(key) for key in data.keys()) - set(definitions.keys()))
    for key in unknown:
        errors.append({"code": "unsupported_provider_control", "field": key, "message": f"Provider control '{key}' is not supported by the selected Voice backend for {mode}."})
    for control_id, definition in definitions.items():
        if control_id not in data:
            continue
        try:
            normalized[control_id] = _normalize_value(definition, data[control_id])
        except VoiceProviderControlsError as exc:
            errors.append({"code": "invalid_provider_control", "field": control_id, "message": str(exc)})
    return {
        **contract,
        "status": "valid" if not errors else "invalid",
        "provider_controls": normalized,
        "errors": errors,
    }
