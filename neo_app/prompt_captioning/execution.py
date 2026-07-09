from __future__ import annotations

from pathlib import Path
from typing import Any

TEXT_TOOLS = {"prompt_generate", "prompt_enhance", "prompt_rewrite", "prompt_cleanup", "negative_prompt", "text_transform", "prompt_studio"}
CAPTION_TOOLS = {"image_captioning", "result_image_captioning", "batch_captioning", "caption_studio"}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
}


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def profile_flags(profile: dict[str, Any] | None) -> dict[str, bool]:
    flags = (profile or {}).get("capability_flags") or {}
    runtime_caps = (profile or {}).get("runtime", {}).get("capabilities") or {}
    runtime_supports_vision = as_bool(runtime_caps.get("runtime_supports_vision", runtime_caps.get("supports_vision")), False)
    runtime_supports_captioning = as_bool(runtime_caps.get("runtime_supports_captioning", runtime_caps.get("supports_captioning")), False)
    effective_supports_vision = as_bool(flags.get("supports_vision"), False) or runtime_supports_vision
    # KoboldCpp reports the loaded multimodal projector as runtime vision support.
    # In Neo, that is enough to unlock Caption Studio without maintaining a second profile.
    effective_supports_captioning = as_bool(flags.get("supports_captioning"), False) or runtime_supports_captioning or runtime_supports_vision
    return {
        "supports_text": as_bool(flags.get("supports_text", runtime_caps.get("supports_text")), True),
        "supports_vision": effective_supports_vision,
        "supports_captioning": effective_supports_captioning,
        "streaming_enabled": as_bool(runtime_caps.get("streaming_enabled", flags.get("streaming_enabled")), False),
    }


def profile_status(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "missing_config"
    if profile.get("enabled") is False:
        return "disabled"
    return str(profile.get("runtime_status") or (profile.get("runtime") or {}).get("status") or profile.get("profile_status") or "disconnected")


def prompt_captioning_profiles(backend_payload: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = backend_payload.get("profiles") or []
    return [
        profile for profile in profiles
        if profile.get("surface") in {"prompt_captioning", "text"}
    ]


def resolve_backend_profile(payload: dict[str, Any], backend_payload: dict[str, Any], *, require: str = "text") -> dict[str, Any] | None:
    requested_id = str((payload.get("metadata") or {}).get("backend_profile_id") or "").strip()
    profiles = prompt_captioning_profiles(backend_payload)
    if requested_id:
        return next((profile for profile in profiles if profile.get("profile_id") == requested_id), None)
    defaults = backend_payload.get("defaults") or {}
    wanted_ids = [defaults.get("prompt_captioning"), defaults.get("text")]
    if require == "caption":
        for profile in profiles:
            flags = profile_flags(profile)
            if profile.get("enabled") is not False and flags["supports_vision"] and flags["supports_captioning"]:
                return profile
    for profile_id in wanted_ids:
        if profile_id:
            match = next((profile for profile in profiles if profile.get("profile_id") == profile_id), None)
            if match:
                return match
    return next((profile for profile in profiles if profile.get("enabled") is not False), None) or (profiles[0] if profiles else None)


def profile_gate(profile: dict[str, Any] | None, *, require: str) -> tuple[bool, str]:
    if not profile:
        return False, "No Text Backend Profile is configured for Prompt & Captioning."
    profile_id = profile.get("profile_id") or "unknown"
    if profile.get("enabled") is False:
        return False, f"Backend profile '{profile_id}' is disabled."
    flags = profile_flags(profile)
    if require == "text" and not flags["supports_text"]:
        return False, f"Backend profile '{profile_id}' does not declare text support."
    if require == "caption" and not (flags["supports_vision"] and flags["supports_captioning"]):
        return False, f"Backend profile '{profile_id}' has no detected vision/caption support. Load a vision model/mmproj in KoboldCpp or enable Vision + Caption flags."
    status = profile_status(profile).strip().lower()
    if status in {"disconnected", "offline", "missing_config", "error", "disabled", "unknown"}:
        return False, f"Backend profile '{profile_id}' is {status}. Click Connect/Test before running this task."
    return True, ""


def clamp_generation_params(params: dict[str, Any] | None, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    defaults = defaults or {}

    def number(key: str, fallback: float, low: float, high: float) -> float:
        try:
            value = float(params.get(key, defaults.get(key, fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(low, min(high, value))

    def integer(key: str, fallback: int, low: int, high: int) -> int:
        try:
            value = int(float(params.get(key, defaults.get(key, fallback))))
        except (TypeError, ValueError):
            value = fallback
        return max(low, min(high, value))

    clean: dict[str, Any] = {
        "temperature": number("temperature", 0.7, 0.0, 2.0),
        "top_p": number("top_p", 0.9, 0.0, 1.0),
        "max_tokens": integer("max_tokens", 512, 1, 8192),
        "stream": False,
    }
    if params.get("top_k") is not None or defaults.get("top_k") is not None:
        clean["top_k"] = integer("top_k", 40, 0, 1000)
    stop_sequences = params.get("stop_sequences", defaults.get("stop_sequences"))
    if stop_sequences:
        clean["stop_sequences"] = stop_sequences if isinstance(stop_sequences, list) else [str(stop_sequences)]
    return clean


def strip_reasoning_text(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    lowered = raw.lower()
    stripped = False
    while "<think>" in lowered and "</think>" in lowered:
        start = lowered.find("<think>")
        end = lowered.find("</think>", start) + len("</think>")
        raw = raw[:start] + raw[end:]
        lowered = raw.lower()
        stripped = True
    for prefix in ("assistant:", "caption:", "prompt:", "output:"):
        if raw.strip().lower().startswith(prefix):
            raw = raw.strip()[len(prefix):]
            stripped = True
            break
    return raw.strip().strip('`').strip(), stripped


def validate_image_asset(asset_path: str) -> dict[str, Any]:
    path = Path(str(asset_path or ""))
    if not asset_path:
        return {"ok": False, "error": "Captioning requires an image asset path."}
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "Captioning image asset is missing or not a file.", "path": str(path)}
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return {"ok": False, "error": "Captioning asset is not a supported image type.", "path": str(path)}
    head = path.read_bytes()[:16]
    detected = ""
    for magic, mime in IMAGE_MAGIC.items():
        if head.startswith(magic):
            detected = mime
            break
    if path.suffix.lower() == ".webp" and head.startswith(b"RIFF") and b"WEBP" in head:
        detected = "image/webp"
    if not detected:
        return {"ok": False, "error": "Captioning asset failed image signature validation.", "path": str(path)}
    return {"ok": True, "path": str(path), "mime_type": detected, "size_bytes": path.stat().st_size}


def execution_metadata(profile: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "surface_id": "prompt_captioning",
        "workspace_app": "neo_studio",
        "tool_id": payload.get("tool_id") or payload.get("tool") or "",
        "mode": payload.get("mode") or "",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": (payload.get("params") or {}).get("model") or (profile.get("connection") or {}).get("model") or (profile.get("generation_defaults") or {}).get("model") or "default",
        "profile_status": profile_status(profile),
        "capabilities": profile_flags(profile),
    }
