from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib import request, parse
import json

from neo_app.providers.profiles import get_backend_profile, list_backend_profiles
from .capabilities import capability_payload, normalize_family
from .provider_routing import build_voice_provider_routing, voice_profile_model_catalog, voice_profile_voice_catalog

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


def default_voice_profile(profile_id: str | None = None, *, strict: bool = False) -> dict[str, Any] | None:
    requested = str(profile_id or "").strip()
    if requested:
        profile = get_backend_profile(requested)
        if profile and profile.get("surface") == "voice" and profile.get("enabled", True) is not False:
            return profile
        if strict:
            return None
    profiles = voice_profiles()
    return next((item for item in profiles if item.get("is_default")), None) or (profiles[0] if profiles else None)


def _profile_connection(profile: dict[str, Any] | None) -> dict[str, Any]:
    return profile.get("connection") if isinstance(profile, dict) and isinstance(profile.get("connection"), dict) else {}


def voice_health_payload(profile_id: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id, strict=bool(str(profile_id or "").strip()))
    connection = _profile_connection(profile)
    base_url = str(connection.get("base_url") or "").strip()
    timeout = float(connection.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    provider_id = str(profile.get("provider_id") if profile else "")
    payload: dict[str, Any] = {
        "schema_id": "neo.voice.health.v2",
        "adapter_contract": VOICE_ADAPTER_CONTRACT_VERSION,
        "surface": "voice",
        "profile_id": profile.get("profile_id") if profile else "",
        "provider_id": provider_id,
        "backend": provider_id,
        "backend_family": _profile_model_family(profile, "chatterbox_turbo") if profile else "",
        "adapter_phase": "VO-V10" if provider_id == "kokoro" else ("VO-V12" if provider_id == "fish_speech" else ("VO-E5A" if provider_id == "neo_voice_engine" else ("VO-R13" if provider_id == "chatterbox" else "VO-V12"))),
        "backend_badge": "Low-VRAM / Lightweight" if provider_id == "kokoro" else ("HQ / Advanced" if provider_id == "fish_speech" else ("Unified / Gateway" if provider_id == "neo_voice_engine" else ("Legacy Direct / Physical" if provider_id == "chatterbox" else ""))),
        "clone_supported": False if provider_id == "kokoro" else (True if provider_id in {"fish_speech", "neo_voice_engine", "chatterbox"} else None),
        "base_url_configured": bool(base_url),
        "reachable": False,
        "status": "not_configured" if not base_url else "offline",
        "last_checked": _now(),
        "message": "Configure and connect a Voice backend profile in the Backend card.",
    }
    if not profile:
        payload["status"] = "invalid_profile" if str(profile_id or "").strip() else "missing_profile"
        payload["message"] = (
            f"Voice backend profile '{str(profile_id or '').strip()}' was not found or is not an enabled Voice profile."
            if str(profile_id or "").strip()
            else "No enabled Voice backend profile exists."
        )
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
    profile = default_voice_profile(profile_id, strict=bool(str(profile_id or "").strip()))
    health = voice_health_payload(profile_id)
    if not profile:
        return {
            "schema_id": "neo.voice.capabilities.v13",
            "phase": "VO-R3",
            "surface": "voice",
            "profile_id": str(profile_id or ""),
            "status": "invalid_profile",
            "compatible": False,
            "authority": "selected_backend_profile",
            "features": {},
            "support_flags": {},
            "control_manifest": {"controls": [], "zones": {}, "source_options": []},
            "backend": health,
        }
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


def _append_remote_catalog_items(base_items: list[dict[str, Any]], raw_items: Any, *, kind: str) -> list[dict[str, Any]]:
    items = [dict(item) for item in base_items if isinstance(item, dict)]
    seen = {str(item.get("id") or "").strip() for item in items}
    records = raw_items if isinstance(raw_items, list) else []
    for item in records:
        if isinstance(item, dict):
            item_id = str(item.get("id") or item.get("name") or "").strip()
            label = str(item.get("label") or item.get("name") or item_id).strip()
            record = {**item, "id": item_id, "label": label or item_id, "source": "backend"}
        else:
            item_id = str(item or "").strip()
            record = {"id": item_id, "label": item_id, "source": "backend"}
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        record["kind"] = kind
        items.append(record)
    return items


def voice_models_payload(profile_id: str | None = None, family: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id, strict=bool(str(profile_id or "").strip()))
    health = voice_health_payload(profile_id)
    if not profile:
        return {
            "schema_id": "neo.voice.models.v13",
            "phase": "VO-R3",
            "surface": "voice",
            "profile_id": str(profile_id or ""),
            "provider_id": "",
            "family": "",
            "status": "invalid_profile",
            "authority": "selected_backend_profile",
            "models": [],
            "backend": health,
        }
    defaults = profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}
    family_id = str(defaults.get("model_family") or "provider_default").strip() or "provider_default"
    catalog = voice_profile_model_catalog(profile)
    models = list(catalog.get("items") or [])
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
    raw_models = (remote.get("models") or remote.get("items") or []) if isinstance(remote, dict) else (remote if isinstance(remote, list) else [])
    models = _append_remote_catalog_items(models, raw_models, kind="model")
    return {
        "schema_id": "neo.voice.models.v13",
        "phase": "VO-R3",
        "surface": "voice",
        "profile_id": str(profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "family": family_id,
        "status": "ready" if health.get("reachable") else "profile_catalog",
        "authority": "selected_backend_profile",
        "default_id": catalog.get("default_id") or "provider_default",
        "resolved_default_id": catalog.get("resolved_default_id") or "provider_default",
        "models": models,
        "backend": health,
    }


def voice_voices_payload(profile_id: str | None = None, family: str | None = None) -> dict[str, Any]:
    profile = default_voice_profile(profile_id, strict=bool(str(profile_id or "").strip()))
    health = voice_health_payload(profile_id)
    if not profile:
        return {
            "schema_id": "neo.voice.voices.v13",
            "phase": "VO-R3",
            "surface": "voice",
            "profile_id": str(profile_id or ""),
            "provider_id": "",
            "family": "",
            "status": "invalid_profile",
            "authority": "selected_backend_profile",
            "voices": [],
            "backend": health,
        }
    defaults = profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}
    family_id = str(defaults.get("model_family") or "provider_default").strip() or "provider_default"
    catalog = voice_profile_voice_catalog(profile)
    voices = list(catalog.get("items") or [])
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
    raw_voices = (remote.get("voices") or remote.get("items") or []) if isinstance(remote, dict) else (remote if isinstance(remote, list) else [])
    voices = _append_remote_catalog_items(voices, raw_voices, kind="voice")
    return {
        "schema_id": "neo.voice.voices.v13",
        "phase": "VO-R3",
        "surface": "voice",
        "profile_id": str(profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "family": family_id,
        "status": "ready" if health.get("reachable") else "profile_catalog",
        "authority": "selected_backend_profile",
        "default_id": catalog.get("default_id") or "provider_default",
        "resolved_default_id": catalog.get("resolved_default_id") or "provider_default",
        "voices": voices,
        "backend": health,
    }


def voice_provider_routing_payload(profile_id: str | None = None) -> dict[str, Any]:
    routing = build_voice_provider_routing(profile_id)
    if not routing.get("routing_ready"):
        return routing
    selected_id = str((routing.get("profile") or {}).get("profile_id") or "")
    health = voice_health_payload(selected_id)
    models = voice_models_payload(selected_id)
    voices = voice_voices_payload(selected_id)
    capabilities = voice_capabilities_payload(selected_id)
    return {
        **routing,
        "status": "profile_routed_live" if health.get("reachable") else "profile_routed",
        "health": health,
        "models": {
            **(routing.get("models") or {}),
            "items": models.get("models") or [],
            "status": models.get("status") or "profile_catalog",
            "resolved_default_id": models.get("resolved_default_id") or (routing.get("models") or {}).get("resolved_default_id") or "provider_default",
        },
        "voices": {
            **(routing.get("voices") or {}),
            "items": voices.get("voices") or [],
            "status": voices.get("status") or "profile_catalog",
            "resolved_default_id": voices.get("resolved_default_id") or (routing.get("voices") or {}).get("resolved_default_id") or "provider_default",
        },
        "legacy_capability_manifest": capabilities,
        "remote_capabilities": capabilities.get("remote_capabilities") if isinstance(capabilities, dict) else None,
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
