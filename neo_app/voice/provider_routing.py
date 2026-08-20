from __future__ import annotations

from copy import deepcopy
from typing import Any

from neo_app.providers.profiles import get_backend_profile, list_backend_profiles

VOICE_PROVIDER_ROUTING_SCHEMA = "neo.voice.provider_routing.v1"
VOICE_PROVIDER_ROUTING_PHASE = "VO-R3"
VOICE_PROVIDER_DEFAULT = "provider_default"


class VoiceProfileRoutingError(ValueError):
    """Raised when an explicit Voice profile cannot be resolved safely."""


def _voice_profiles() -> list[dict[str, Any]]:
    return [
        profile
        for profile in list_backend_profiles("voice")
        if isinstance(profile, dict) and profile.get("enabled", True) is not False
    ]


def resolve_voice_profile(profile_id: str | None = None) -> dict[str, Any]:
    """Resolve exactly one enabled Voice profile without cross-profile fallback.

    An omitted profile id may use the configured/default Voice profile. An explicit
    profile id is strict: invalid, disabled, or non-Voice ids never fall back to a
    different provider because that would leak models/capabilities across profiles.
    """
    requested = str(profile_id or "").strip()
    if requested:
        profile = get_backend_profile(requested)
        if not isinstance(profile, dict):
            raise VoiceProfileRoutingError(f"Voice backend profile '{requested}' was not found.")
        if str(profile.get("surface") or "").strip() != "voice":
            raise VoiceProfileRoutingError(f"Backend profile '{requested}' is not a Voice profile.")
        if profile.get("enabled", True) is False:
            raise VoiceProfileRoutingError(f"Voice backend profile '{requested}' is disabled.")
        return profile

    profiles = _voice_profiles()
    profile = next((item for item in profiles if item.get("is_default")), None) or (profiles[0] if profiles else None)
    if not profile:
        raise VoiceProfileRoutingError("No enabled Voice backend profile exists.")
    return profile


def _profile_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    defaults = profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}
    if not defaults and isinstance(profile.get("defaults"), dict):
        defaults = profile.get("defaults") or {}
    return defaults


def _catalog_items(raw: Any, *, default_source: str = "profile") -> list[dict[str, Any]]:
    records = raw if isinstance(raw, list) else []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        if isinstance(item, dict):
            item_id = str(item.get("id") or item.get("name") or item.get("model") or item.get("voice") or "").strip()
            label = str(item.get("label") or item.get("name") or item_id).strip()
            record = {**item, "id": item_id, "label": label or item_id, "source": str(item.get("source") or default_source)}
        else:
            item_id = str(item or "").strip()
            record = {"id": item_id, "label": item_id.replace("_", " ").title(), "source": default_source}
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        output.append(record)
    return output


def _default_catalog_entry(kind: str, resolved_id: str) -> dict[str, Any]:
    resolved = str(resolved_id or "").strip()
    suffix = f" ({resolved.replace('_', ' ').title()})" if resolved and resolved != VOICE_PROVIDER_DEFAULT else ""
    return {
        "id": VOICE_PROVIDER_DEFAULT,
        "label": f"Provider Default{suffix}",
        "source": "profile_default",
        "resolved_id": resolved or VOICE_PROVIDER_DEFAULT,
        "kind": kind,
    }


def voice_profile_model_catalog(profile: dict[str, Any]) -> dict[str, Any]:
    model_block = profile.get("model") if isinstance(profile.get("model"), dict) else {}
    defaults = _profile_defaults(profile)
    models = _catalog_items(model_block.get("available_models"), default_source="profile")
    default_model = str(model_block.get("default_model") or defaults.get("model") or defaults.get("model_family") or "").strip()
    if default_model and default_model != VOICE_PROVIDER_DEFAULT and not any(item["id"] == default_model for item in models):
        models.insert(0, {"id": default_model, "label": default_model.replace("_", " ").title(), "source": "profile_default"})
    return {
        "default_id": VOICE_PROVIDER_DEFAULT,
        "resolved_default_id": default_model or VOICE_PROVIDER_DEFAULT,
        "items": [_default_catalog_entry("model", default_model), *[item for item in models if item["id"] != VOICE_PROVIDER_DEFAULT]],
    }


def voice_profile_voice_catalog(profile: dict[str, Any]) -> dict[str, Any]:
    defaults = _profile_defaults(profile)
    voices = _catalog_items(defaults.get("available_voices"), default_source="profile")
    default_voice = str(defaults.get("default_voice") or "").strip()
    if default_voice and default_voice != VOICE_PROVIDER_DEFAULT and not any(item["id"] == default_voice for item in voices):
        voices.insert(0, {"id": default_voice, "label": default_voice.replace("_", " ").title(), "source": "profile_default"})
    return {
        "default_id": VOICE_PROVIDER_DEFAULT,
        "resolved_default_id": default_voice or VOICE_PROVIDER_DEFAULT,
        "items": [_default_catalog_entry("voice", default_voice), *[item for item in voices if item["id"] != VOICE_PROVIDER_DEFAULT]],
    }


def _common_control_contract(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    caps = profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}
    defaults = _profile_defaults(profile)
    multilingual = bool(caps.get("multilingual"))
    fixed_language = str(defaults.get("language") or "auto").strip() or "auto"
    tts = bool(caps.get("tts", True))
    return {
        "script": {"visible": True, "enabled": True, "authority": "common_contract"},
        "language": {
            "visible": True,
            "enabled": bool(tts and (multilingual or fixed_language in {"", "auto"})),
            "mode": "selectable" if multilingual or fixed_language in {"", "auto"} else "fixed",
            "fixed_value": "" if multilingual or fixed_language in {"", "auto"} else fixed_language,
            "authority": "selected_profile",
        },
        "model_id": {"visible": True, "enabled": tts, "authority": "selected_profile_catalog"},
        "voice_id": {"visible": True, "enabled": tts, "authority": "selected_profile_catalog"},
        "speaking_rate": {"visible": True, "enabled": tts, "authority": "common_contract"},
        "output_format": {"visible": True, "enabled": tts, "authority": "common_contract"},
        "split_long_text": {"visible": True, "enabled": True, "authority": "neo_processing"},
        "max_chunk_chars": {"visible": True, "enabled": True, "authority": "neo_processing"},
        "punctuation_cleanup": {"visible": True, "enabled": True, "authority": "neo_processing"},
    }


def build_voice_provider_routing(profile_id: str | None = None) -> dict[str, Any]:
    """Return the profile-authoritative VO-R3 routing contract.

    This contract is intentionally non-executing. It selects one profile, exposes
    only that profile's catalogs/capabilities, and tells the UI what common fields
    remain editable. Live provider discovery may later augment these same catalogs,
    but it must stay bound to this exact profile id.
    """
    try:
        profile = resolve_voice_profile(profile_id)
    except VoiceProfileRoutingError as exc:
        return {
            "schema_id": VOICE_PROVIDER_ROUTING_SCHEMA,
            "phase": VOICE_PROVIDER_ROUTING_PHASE,
            "surface": "voice",
            "status": "invalid_profile",
            "routing_ready": False,
            "generation_execution": False,
            "requested_profile_id": str(profile_id or ""),
            "profile": None,
            "models": {"default_id": VOICE_PROVIDER_DEFAULT, "resolved_default_id": VOICE_PROVIDER_DEFAULT, "items": []},
            "voices": {"default_id": VOICE_PROVIDER_DEFAULT, "resolved_default_id": VOICE_PROVIDER_DEFAULT, "items": []},
            "capabilities": {},
            "common_controls": {},
            "errors": [str(exc)],
        }

    defaults = _profile_defaults(profile)
    capabilities = deepcopy(profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {})
    model_catalog = voice_profile_model_catalog(profile)
    voice_catalog = voice_profile_voice_catalog(profile)
    profile_id_value = str(profile.get("profile_id") or "")
    provider_id = str(profile.get("provider_id") or "")
    family = str(defaults.get("model_family") or model_catalog.get("resolved_default_id") or VOICE_PROVIDER_DEFAULT)
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    from .provider_controls import build_voice_provider_controls
    provider_controls = build_voice_provider_controls(profile, mode="tts")
    return {
        "schema_id": VOICE_PROVIDER_ROUTING_SCHEMA,
        "phase": VOICE_PROVIDER_ROUTING_PHASE,
        "surface": "voice",
        "status": "profile_routed",
        "routing_ready": True,
        "generation_execution": False,
        "authority": "selected_backend_profile",
        "profile": {
            "profile_id": profile_id_value,
            "display_name": str(profile.get("display_name") or profile_id_value),
            "provider_id": provider_id,
            "provider_label": str(profile.get("provider_label") or provider_id),
            "family": family,
            "connection_type": str(profile.get("connection_type") or connection.get("connection_type") or connection.get("kind") or ""),
            "enabled": profile.get("enabled", True) is not False,
            "is_default": bool(profile.get("is_default")),
            "runtime_status": str(profile.get("runtime_status") or runtime.get("status") or "not_checked"),
            "reachable": bool(runtime.get("reachable")),
        },
        "models": model_catalog,
        "voices": voice_catalog,
        "capabilities": capabilities,
        "common_controls": _common_control_contract(profile),
        "provider_controls": provider_controls,
        "defaults": {
            "language": str(defaults.get("language") or "auto"),
            "speaking_rate": float(defaults.get("speaking_rate") or 1.0),
            "output_format": str(defaults.get("output_format") or "wav"),
        },
        "release_boundary": {
            "provider_profile_authority": True,
            "model_catalog_authority": True,
            "voice_catalog_authority": True,
            "capability_routing": True,
            "remote_catalog_must_match_profile": True,
            "provider_native_controls_mounted": True,
            "provider_controls_authority": "selected_backend_profile_capability_manifest",
            "generation_execution": False,
            "next_phase": "VO-R4",
            "current_surface_phase": "VO-R8",
        },
        "errors": [],
    }
