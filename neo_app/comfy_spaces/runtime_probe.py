from __future__ import annotations

import json
from typing import Any
from urllib import request

from neo_app.providers.profiles import get_backend_profile_for_runtime, get_backend_profile_for_live_task


SPACE_RUNTIME_PROBE_SCHEMA_VERSION = "neo.comfy_space.runtime_probe.v1"
_COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}


class SpaceRuntimeProbeError(RuntimeError):
    """Raised when a selected backend profile cannot provide Comfy object_info."""


def _profile_base_url(profile: dict[str, Any]) -> str:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    return str(connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188").strip().rstrip("/")


def probe_comfy_profile_object_info(profile_id: str, *, timeout: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read `/object_info` from one explicitly selected Comfy backend profile.

    The probe is read-only and never scans/falls back to another profile. API keys,
    local paths, and the full object_info catalog are not copied into the returned
    diagnostic metadata; callers receive the catalog separately for in-memory
    graph analysis.
    """

    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise SpaceRuntimeProbeError("A backend profile id is required for live Comfy analysis.")

    profile = get_backend_profile_for_live_task(clean_profile_id) or get_backend_profile_for_runtime(clean_profile_id)
    if not profile:
        raise SpaceRuntimeProbeError(f"Unknown backend profile: {clean_profile_id}")
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id not in _COMFY_PROVIDER_IDS:
        raise SpaceRuntimeProbeError(f"Backend profile '{clean_profile_id}' is not a ComfyUI profile.")
    if profile.get("enabled") is False:
        raise SpaceRuntimeProbeError(f"Backend profile '{clean_profile_id}' is disabled.")

    base_url = _profile_base_url(profile)
    if not base_url.lower().startswith(("http://", "https://")):
        raise SpaceRuntimeProbeError("ComfyUI profile base URL must use http or https.")
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    request_timeout = float(timeout if timeout is not None else connection.get("timeout_seconds") or 3.0)
    request_timeout = max(0.5, min(request_timeout, 30.0))

    try:
        with request.urlopen(f"{base_url}/object_info", timeout=request_timeout) as response:  # noqa: S310 - selected Admin backend profile.
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - caller needs one safe preflight error.
        raise SpaceRuntimeProbeError(f"Unable to read ComfyUI /object_info for profile '{clean_profile_id}': {exc}") from exc
    if not isinstance(payload, dict):
        raise SpaceRuntimeProbeError("ComfyUI /object_info returned an invalid payload.")

    diagnostic = {
        "schema_version": SPACE_RUNTIME_PROBE_SCHEMA_VERSION,
        "ok": True,
        "profile_id": clean_profile_id,
        "provider_id": provider_id,
        "reachable": True,
        "object_info_available": True,
        "object_info_node_count": len(payload),
        "selected_profile_only": True,
        "fallback_profile_used": False,
        "base_url_exposed": False,
    }
    return payload, diagnostic
