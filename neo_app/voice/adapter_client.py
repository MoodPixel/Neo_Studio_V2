from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib import request, parse
import json

from neo_app.providers.profiles import get_backend_profile, list_backend_profiles
from .capabilities import capability_payload, normalize_family

VOICE_ADAPTER_CONTRACT_VERSION = "neo.voice.adapter_contract.v12"
DEFAULT_TIMEOUT_SECONDS = 8

KOKORO_FAMILY = "kokoro_preview"
KOKORO_PROFILE_ID = "voice.kokoro"
FISH_FAMILY = "fish_hq"
FISH_PROFILE_ID = "voice.fish_speech"
FISH_CONTRACT_MODELS = [
    {"id": "fish_hq", "label": "Fish Speech HQ", "source": "neo_contract", "tier": "high_vram_hq", "status": "advanced_adapter_ready"},
    {"id": "fish_hq_clone", "label": "Fish Speech HQ Clone", "source": "neo_contract", "tier": "high_vram_hq", "status": "advanced_adapter_ready"},
]
FISH_CONTRACT_VOICES = [
    {"id": "provider_default", "label": "Provider Default", "source": "neo_contract", "supports_preview": True, "tier": "high_vram_hq"},
    {"id": "fish_narrator_hq", "label": "Fish Narrator HQ", "source": "neo_contract", "supports_preview": True, "supports_clone": True, "tier": "high_vram_hq"},
    {"id": "fish_clone_reference", "label": "Fish Reference Clone", "source": "neo_contract", "supports_preview": True, "supports_clone": True, "tier": "high_vram_hq"},
]
KOKORO_CONTRACT_MODELS = [
    {"id": "kokoro_preview", "label": "Kokoro Preview", "source": "neo_contract", "tier": "low_vram", "status": "adapter_ready"},
]
KOKORO_CONTRACT_VOICES = [
    {"id": "provider_default", "label": "Provider Default", "source": "neo_contract", "supports_preview": True},
    {"id": "kokoro_default", "label": "Kokoro Default", "source": "neo_contract", "supports_preview": True, "tier": "low_vram"},
    {"id": "kokoro_narrator", "label": "Kokoro Narrator", "source": "neo_contract", "supports_preview": True, "tier": "low_vram"},
]


def _profile_model_family(profile: dict[str, Any] | None, fallback: str = "chatterbox_turbo") -> str:
    defaults = profile.get("generation_defaults") if isinstance(profile, dict) and isinstance(profile.get("generation_defaults"), dict) else {}
    raw = str(defaults.get("model_family") or fallback).strip()
    aliases = {"chatterbox": "chatterbox_turbo", "kokoro": KOKORO_FAMILY, "fish_speech": FISH_FAMILY, "fish": FISH_FAMILY}
    return normalize_family(aliases.get(raw, raw))


def is_kokoro_selection(profile: dict[str, Any] | None = None, family: str | None = None, runtime: str | None = None) -> bool:
    provider_id = str((profile or {}).get("provider_id") or runtime or "").strip()
    family_id = normalize_family(family or _profile_model_family(profile, "chatterbox_turbo"))
    return provider_id == "kokoro" or family_id == KOKORO_FAMILY


def is_fish_selection(profile: dict[str, Any] | None = None, family: str | None = None, runtime: str | None = None) -> bool:
    provider_id = str((profile or {}).get("provider_id") or runtime or "").strip()
    family_id = normalize_family(family or _profile_model_family(profile, "chatterbox_turbo"))
    return provider_id in {"fish_speech", "fish"} or family_id == FISH_FAMILY


def voice_backend_tier(profile: dict[str, Any] | None = None, family: str | None = None, runtime: str | None = None) -> str:
    if is_kokoro_selection(profile, family, runtime):
        return "low_vram"
    if is_fish_selection(profile, family, runtime):
        return "high_vram_hq"
    return "standard"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_get(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    with request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"raw": raw}


def _json_post(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"raw": raw}


def voice_profiles() -> list[dict[str, Any]]:
    return [profile for profile in list_backend_profiles("voice") if profile.get("enabled", True) is not False]


def default_voice_profile(profile_id: str | None = None) -> dict[str, Any] | None:
    if profile_id:
        profile = get_backend_profile(profile_id)
        if profile and profile.get("surface") == "voice":
            return profile
    profiles = voice_profiles()
    return next((item for item in profiles if item.get("is_default")), None) or (profiles[0] if profiles else None)


def _profile_connection(profile: dict[str, Any] | None) -> dict[str, Any]:
    return profile.get("connection") if isinstance(profile, dict) and isinstance(profile.get("connection"), dict) else {}


def voice_health_payload(profile_id: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id)
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    provider_id = str(profile.get("provider_id") if profile else "chatterbox")
    payload: dict[str, Any] = {
        "schema_id": "neo.voice.health.v2",
        "adapter_contract": VOICE_ADAPTER_CONTRACT_VERSION,
        "surface": "voice",
        "profile_id": profile.get("profile_id") if profile else "",
        "provider_id": provider_id,
        "backend": provider_id,
        "backend_family": _profile_model_family(profile, "chatterbox_turbo"),
        "adapter_phase": "VO-V10" if provider_id == "kokoro" else ("VO-V12" if provider_id == "fish_speech" else "VO-V12"),
        "backend_badge": "Low-VRAM / Lightweight" if provider_id == "kokoro" else ("HQ / Advanced" if provider_id == "fish_speech" else ""),
        "clone_supported": False if provider_id == "kokoro" else (True if provider_id == "fish_speech" else None),
        "base_url_configured": bool(base_url),
        "reachable": False,
        "status": "not_configured" if not base_url else "offline",
        "last_checked": _now(),
        "message": "Configure and connect a Voice backend profile in the Backend card.",
    }
    if not profile:
        payload["status"] = "missing_profile"
        payload["message"] = "No enabled Voice backend profile exists."
        return payload
    if not base_url:
        if provider_id == "kokoro":
            payload["status"] = "kokoro_adapter_not_configured"
            payload["message"] = "Kokoro low-end adapter is registered. Configure a local Kokoro HTTP base URL to use live synthesis; Neo preview/render handoff remains available."
        elif provider_id == "fish_speech":
            payload["status"] = "fish_adapter_not_configured"
            payload["message"] = "Fish Speech HQ adapter is registered as an advanced backend lane. Configure a local Fish HTTP base URL before live HQ synthesis; Neo guarded preview/render/clone handoff remains available."
            payload["setup_warnings"] = ["advanced_backend", "higher_vram_expected", "slower_startup", "more_install_complexity"]
        return payload

    health_paths = [
        connection.get("healthcheck_path") or "/health",
        "/health",
        "/api/health",
        "/api/voice/health",
    ]
    errors: list[str] = []
    for path in dict.fromkeys(str(item or "").strip() for item in health_paths if item):
        try:
            remote = _json_get(base_url, path if path.startswith("/") else f"/{path}", timeout)
            payload.update({
                "reachable": True,
                "status": str(remote.get("status") or remote.get("state") or "connected"),
                "message": str(remote.get("message") or "Voice backend responded."),
                "remote": remote,
                "checked_path": path,
            })
            return payload
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    payload["errors"] = errors[-4:]
    payload["message"] = "Voice backend did not respond to health probes."
    return payload


def voice_capabilities_payload(profile_id: str | None = None, family: str | None = None, runtime: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id)
    health = voice_health_payload(profile_id)
    family_id = normalize_family(family or _profile_model_family(profile, "chatterbox_turbo"))
    base = capability_payload(family=family_id, runtime=runtime or (profile or {}).get("provider_id"), profile=profile or {}, backend_health=health)
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    if base_url and health.get("reachable"):
        for path in ("/api/voice/capabilities", "/capabilities", "/api/capabilities"):
            try:
                remote = _json_get(base_url, f"{path}?{parse.urlencode({'family': family_id})}", timeout)
                if isinstance(remote, dict):
                    base["remote_capabilities"] = remote
                    base["status"] = "ready"
                    break
            except Exception:
                continue
    return base


def voice_models_payload(profile_id: str | None = None, family: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id)
    family_id = normalize_family(family or _profile_model_family(profile, "chatterbox_turbo"))
    health = voice_health_payload(profile_id)
    models: list[dict[str, Any]] = []
    defaults = (profile or {}).get("generation_defaults") if isinstance((profile or {}).get("generation_defaults"), dict) else {}
    if defaults.get("model_family"):
        default_raw = str(defaults.get("model_family"))
        default_family = normalize_family({"chatterbox": "chatterbox_turbo", "kokoro": KOKORO_FAMILY, "fish_speech": FISH_FAMILY, "fish": FISH_FAMILY}.get(default_raw, default_raw))
        tier = "low_vram" if default_family == KOKORO_FAMILY else ("high_vram_hq" if default_family == FISH_FAMILY else "")
        models.append({"id": default_family, "label": default_family.replace("_", " ").title(), "source": "profile_default", "tier": tier})
    if family_id.startswith("chatterbox") and not any(item["id"] == family_id for item in models):
        models.append({"id": family_id, "label": family_id.replace("_", " ").title(), "source": "neo_contract"})
    if family_id == KOKORO_FAMILY:
        for model in KOKORO_CONTRACT_MODELS:
            if not any(item["id"] == model["id"] for item in models):
                models.append(dict(model))
    if family_id == FISH_FAMILY:
        for model in FISH_CONTRACT_MODELS:
            if not any(item["id"] == model["id"] for item in models):
                models.append(dict(model))
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    remote: Any = None
    if base_url and health.get("reachable"):
        for path in ("/api/voice/models", "/models", "/api/models"):
            try:
                remote = _json_get(base_url, f"{path}?{parse.urlencode({'family': family_id})}", timeout)
                break
            except Exception:
                continue
    if isinstance(remote, dict):
        raw_models = remote.get("models") or remote.get("items") or []
    elif isinstance(remote, list):
        raw_models = remote
    else:
        raw_models = []
    for item in raw_models:
        if isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
            label = str(item.get("label") or item.get("name") or model_id).strip()
        else:
            model_id = str(item or "").strip(); label = model_id
        if model_id and not any(model["id"] == model_id for model in models):
            models.append({"id": model_id, "label": label, "source": "backend"})
    return {
        "schema_id": "neo.voice.models.v12",
        "surface": "voice",
        "profile_id": profile.get("profile_id") if profile else "",
        "family": family_id,
        "status": "ready" if health.get("reachable") else "contract_fallback",
        "models": models,
        "backend": health,
    }


def voice_voices_payload(profile_id: str | None = None, family: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id)
    family_id = normalize_family(family or _profile_model_family(profile, "chatterbox_turbo"))
    health = voice_health_payload(profile_id)
    voices = [
        {"id": "provider_default", "label": "Provider Default", "source": "neo_contract", "supports_preview": True},
    ]
    if family_id == KOKORO_FAMILY:
        voices = [dict(item) for item in KOKORO_CONTRACT_VOICES]
    if family_id == FISH_FAMILY:
        voices = [dict(item) for item in FISH_CONTRACT_VOICES]
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    remote: Any = None
    if base_url and health.get("reachable"):
        for path in ("/api/voice/voices", "/voices", "/api/voices"):
            try:
                remote = _json_get(base_url, f"{path}?{parse.urlencode({'family': family_id})}", timeout)
                break
            except Exception:
                continue
    raw_voices = remote.get("voices") if isinstance(remote, dict) else (remote if isinstance(remote, list) else [])
    for item in raw_voices or []:
        if isinstance(item, dict):
            voice_id = str(item.get("id") or item.get("name") or "").strip()
            label = str(item.get("label") or item.get("name") or voice_id).strip()
        else:
            voice_id = str(item or "").strip(); label = voice_id
        if voice_id and not any(voice["id"] == voice_id for voice in voices):
            voices.append({"id": voice_id, "label": label, "source": "backend", "supports_preview": True})
    return {
        "schema_id": "neo.voice.voices.v12",
        "surface": "voice",
        "profile_id": profile.get("profile_id") if profile else "",
        "family": family_id,
        "status": "ready" if health.get("reachable") else "contract_fallback",
        "voices": voices,
        "backend": health,
    }


def voice_remote_post_payload(path: str, payload: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id)
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    if not base_url:
        return {"ok": False, "status": "not_configured", "message": "Voice backend base URL is not configured.", "profile_id": profile.get("profile_id") if profile else ""}
    try:
        remote = _json_post(base_url, path, payload, timeout)
        return {"ok": True, "status": "submitted", "remote": remote, "profile_id": profile.get("profile_id") if profile else ""}
    except Exception as exc:
        return {"ok": False, "status": "backend_post_failed", "error": str(exc), "profile_id": profile.get("profile_id") if profile else ""}


def voice_capability_controls_payload(profile_id: str | None = None, family: str | None = None, runtime: str | None = None) -> dict[str, Any]:
    """Return only the VO-V8 UI control manifest for capability-aware rendering."""
    capabilities = voice_capabilities_payload(profile_id=profile_id, family=family, runtime=runtime)
    manifest = capabilities.get("control_manifest") or capabilities.get("ui_manifest") or {}
    return {
        "schema_id": "neo.voice.capability_controls_response.v12",
        "surface": "voice",
        "family": capabilities.get("family"),
        "runtime": capabilities.get("runtime"),
        "profile_id": capabilities.get("profile_id") or "",
        "status": capabilities.get("status"),
        "compatible": capabilities.get("compatible"),
        "control_manifest": manifest,
        "support_flags": capabilities.get("support_flags") or manifest.get("support_flags") or {},
        "backend_badge": capabilities.get("backend_badge") or "",
        "adapter_phase": capabilities.get("adapter_phase") or "",
        "backend": capabilities.get("backend") or {},
    }
