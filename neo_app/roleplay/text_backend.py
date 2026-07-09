from __future__ import annotations

import json
from urllib import error, request
from typing import Any, Iterator

from neo_app.providers.profiles import get_backend_profile_payload
from neo_app.roleplay.base_contract import BACKEND_PROFILE_PROVIDER_IDS, BACKEND_PROFILE_SURFACES

FALLBACK_ORDER = ["roleplay", "text", "prompt_captioning", "assistant"]
TEXT_PROVIDER_IDS = set(BACKEND_PROFILE_PROVIDER_IDS)


def _supports_text(profile: dict[str, Any]) -> bool:
    flags = profile.get("capability_flags") or {}
    if flags.get("supports_text") is False:
        return False
    provider_id = str(profile.get("provider_id") or "")
    return provider_id in TEXT_PROVIDER_IDS or bool(flags.get("supports_text", False))


def _runtime_status(profile: dict[str, Any]) -> str:
    return str(profile.get("runtime_status") or (profile.get("runtime") or {}).get("status") or profile.get("profile_status") or "unknown")


CONNECTED_RUNTIME_STATUSES = {"connected", "available", "online", "ready"}
BLOCKED_RUNTIME_STATUSES = {"disconnected", "offline", "missing_config", "disabled", "error", "unknown"}


def _runtime_connected(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    status = _runtime_status(profile).strip().lower()
    if bool(runtime.get("reachable", False)) and status in CONNECTED_RUNTIME_STATUSES:
        return True
    return status in CONNECTED_RUNTIME_STATUSES and status not in BLOCKED_RUNTIME_STATUSES


def _profile_summary(profile: dict[str, Any], rank: int = 0, selected: bool = False) -> dict[str, Any]:
    flags = profile.get("capability_flags") or {}
    runtime = profile.get("runtime") or {}
    capabilities_raw = profile.get("capabilities") or {}
    capabilities = capabilities_raw if isinstance(capabilities_raw, dict) else {}
    capability_ids = {str(item.get("capability_id") or item.get("id") or "") for item in capabilities_raw} if isinstance(capabilities_raw, list) else set()
    return {
        "profile_id": str(profile.get("profile_id") or ""),
        "display_name": str(profile.get("display_name") or profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "surface": str(profile.get("surface") or ""),
        "profile_role": str(profile.get("profile_role") or ""),
        "enabled": profile.get("enabled") is not False,
        "is_default": bool(profile.get("is_default")),
        "runtime_status": _runtime_status(profile),
        "runtime_connected": _runtime_connected(profile),
        "supports_text": _supports_text(profile),
        "supports_vision": bool(flags.get("supports_vision") or capabilities.get("supports_vision") or "vision" in capability_ids or "captioning" in capability_ids),
        "supports_captioning": bool(flags.get("supports_captioning") or capabilities.get("supports_captioning") or "captioning" in capability_ids),
        "model": str((profile.get("connection") or {}).get("model") or ""),
        "base_url": str((profile.get("connection") or {}).get("base_url") or runtime.get("base_url") or ""),
        "context_window_tokens": int((profile.get("generation_defaults") or {}).get("context_window_tokens") or (profile.get("generation_defaults") or {}).get("context_size") or (profile.get("generation_defaults") or {}).get("n_ctx") or runtime.get("context_window_tokens") or runtime.get("context_size") or runtime.get("n_ctx") or 8192),
        "selection_rank": rank,
        "selected": selected,
    }


def _compatible_profiles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = payload.get("profiles") or []
    compatible: list[dict[str, Any]] = []
    for profile in profiles:
        if not isinstance(profile, dict) or profile.get("enabled") is False:
            continue
        surface = str(profile.get("surface") or "")
        provider_id = str(profile.get("provider_id") or "")
        if surface not in BACKEND_PROFILE_SURFACES and provider_id not in TEXT_PROVIDER_IDS:
            continue
        if not _supports_text(profile):
            continue
        compatible.append(profile)
    return compatible


def _find_profile(profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any] | None:
    if not profile_id:
        return None
    return next((profile for profile in profiles if profile.get("profile_id") == profile_id), None)


def resolve_roleplay_text_backend(profile_id: str | None = None) -> dict[str, Any]:
    payload = get_backend_profile_payload()
    defaults = payload.get("defaults") or {}
    profiles = _compatible_profiles(payload)

    selected = _find_profile(profiles, str(profile_id or ""))
    selection_source = "explicit" if selected else ""

    if not selected:
        for surface in FALLBACK_ORDER:
            default_id = str(defaults.get(surface) or "")
            selected = _find_profile(profiles, default_id)
            if selected:
                selection_source = f"default:{surface}"
                break

    if not selected:
        for surface in FALLBACK_ORDER:
            selected = next((profile for profile in profiles if profile.get("surface") == surface and profile.get("is_default")), None)
            if selected:
                selection_source = f"surface_default:{surface}"
                break

    if not selected and profiles:
        selected = profiles[0]
        selection_source = "first_compatible"

    selected_id = str(selected.get("profile_id") or "") if selected else ""
    summaries = [_profile_summary(profile, index, selected=str(profile.get("profile_id") or "") == selected_id) for index, profile in enumerate(profiles)]
    active_connected = _runtime_connected(selected) if selected else False
    bridge_status = "active" if selected_id and active_connected else ("backend_disconnected" if selected_id else "missing_profile")
    return {
        "schema_id": "neo.roleplay.text_backend_bridge.v1",
        "version": "1.0.0-text-backend-bridge",
        "surface_id": "roleplay",
        "status": bridge_status,
        "ready": bool(selected_id and active_connected),
        "active_profile_id": selected_id,
        "active_profile": _profile_summary(selected, 0, True) if selected else None,
        "selection_source": selection_source or "none",
        "fallback_order": FALLBACK_ORDER,
        "profile_surfaces": BACKEND_PROFILE_SURFACES,
        "provider_ids": BACKEND_PROFILE_PROVIDER_IDS,
        "defaults": {surface: defaults.get(surface, "") for surface in FALLBACK_ORDER},
        "profiles": summaries,
        "profile_count": len(summaries),
        "active_features": [
            "non_streaming_prompt_execution",
            "streaming_prompt_execution",
            "scene_generation",
            "runtime_bundle_context_consumption",
            "shared_admin_profile_fallback",
        ],
        "deferred_features": [
            "tool_call_execution",
        ],
    }



def _profile_connection(profile: dict[str, Any] | None) -> dict[str, Any]:
    return (profile or {}).get("connection") if isinstance((profile or {}).get("connection"), dict) else {}


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    suffix = str(path or "").strip() or "/v1/chat/completions"
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}"


def _extract_text_response(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        text = first.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    response = payload.get("response")
    if isinstance(response, str) and response.strip():
        return response.strip()
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


def execute_roleplay_text_backend(
    *,
    profile_id: str | None = None,
    messages: list[dict[str, str]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout_seconds: int | None = None,
    stop: list[str] | None = None,
) -> dict[str, Any]:
    """Execute one non-streaming Roleplay text turn through the shared Admin/backend profile."""
    bridge = resolve_roleplay_text_backend(profile_id)
    profile = bridge.get("active_profile") or None
    if not profile:
        return {"ok": False, "status": "no_text_backend_profile", "error": "No compatible shared text backend profile is available for Roleplay.", "bridge": bridge, "text": ""}

    raw_payload = get_backend_profile_payload()
    raw_profile = _find_profile(_compatible_profiles(raw_payload), str(profile.get("profile_id") or "")) or {}
    if not _runtime_connected(raw_profile):
        runtime_status = _runtime_status(raw_profile).strip().lower() or "disconnected"
        return {"ok": False, "status": "backend_disconnected", "error": f"Roleplay text backend is not connected. Connect/Test the selected backend first. Runtime status: {runtime_status}.", "bridge": bridge, "text": ""}
    connection = _profile_connection(raw_profile)
    provider_id = str(raw_profile.get("provider_id") or profile.get("provider_id") or "")
    base_url = str(connection.get("base_url") or profile.get("base_url") or "").strip()
    if not base_url:
        return {"ok": False, "status": "missing_base_url", "error": "Active text backend profile has no base_url.", "bridge": bridge, "text": ""}

    defaults = raw_profile.get("generation_defaults") if isinstance(raw_profile.get("generation_defaults"), dict) else {}
    clean_messages = [m for m in (messages or []) if isinstance(m, dict) and str(m.get("content") or "").strip()]
    if not clean_messages:
        return {"ok": False, "status": "missing_messages", "error": "No messages were provided for the Scene turn.", "bridge": bridge, "text": ""}

    model = str(connection.get("model") or profile.get("model") or "")
    timeout = int(timeout_seconds or connection.get("timeout_seconds") or 300)
    generation = {
        "max_tokens": int(max_tokens or defaults.get("max_tokens") or 512),
        "temperature": float(temperature if temperature is not None else defaults.get("temperature", 0.7)),
        "top_p": float(top_p if top_p is not None else defaults.get("top_p", 0.9)),
        "repetition_penalty": float(defaults.get("repetition_penalty", defaults.get("repeat_penalty", 1.12))),
    }

    if provider_id == "ollama":
        url = _join_url(base_url, connection.get("chat_path") or "/api/chat")
        options = {"temperature": generation["temperature"], "top_p": generation["top_p"], "num_predict": generation["max_tokens"], "repeat_penalty": generation["repetition_penalty"]}
        clean_stop = [str(x) for x in (stop or []) if str(x)]
        if clean_stop:
            options["stop"] = clean_stop
        req_payload = {
            "model": model,
            "messages": clean_messages,
            "stream": False,
            "options": options,
        }
    else:
        url = _join_url(base_url, connection.get("chat_path") or "/v1/chat/completions")
        req_payload = {"messages": clean_messages, "max_tokens": generation["max_tokens"], "temperature": generation["temperature"], "top_p": generation["top_p"], "repetition_penalty": generation["repetition_penalty"], "stream": False}
        clean_stop = [str(x) for x in (stop or []) if str(x)]
        if clean_stop:
            req_payload["stop"] = clean_stop
        if model:
            req_payload["model"] = model

    headers = {"Content-Type": "application/json"}
    api_key = str(connection.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(url, data=json.dumps(req_payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return {"ok": False, "status": "http_error", "error": f"HTTP {exc.code}: {body[:500]}", "bridge": bridge, "request_url": url, "text": ""}
    except Exception as exc:
        return {"ok": False, "status": "request_error", "error": str(exc), "bridge": bridge, "request_url": url, "text": ""}

    text = _extract_text_response(data)
    if not text:
        return {"ok": False, "status": "empty_response", "error": "Backend returned no assistant text.", "bridge": bridge, "request_url": url, "response": data, "text": ""}
    return {
        "ok": True,
        "status": "generated",
        "text": text,
        "bridge": bridge,
        "request_url": url,
        "provider_id": provider_id,
        "active_profile_id": bridge.get("active_profile_id") or "",
        "generation": generation,
        "response_meta": {"choice_count": len(data.get("choices") or []) if isinstance(data, dict) else 0},
    }



def _json_event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": event_type, **payload}


def _extract_openai_stream_token(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        text = first.get("text")
        if isinstance(text, str):
            return text
    return ""


def _extract_ollama_stream_token(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    response = payload.get("response")
    if isinstance(response, str):
        return response
    return ""


def execute_roleplay_text_backend_stream(
    *,
    profile_id: str | None = None,
    messages: list[dict[str, str]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout_seconds: int | None = None,
    stop: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream one Roleplay text turn through the shared Admin/backend profile.

    Emits small dict events: backend_start, token, backend_done, or error. If a
    configured provider does not support streaming cleanly, the caller can still
    recover because errors are structured and non-streaming execution remains
    available through execute_roleplay_text_backend.
    """
    bridge = resolve_roleplay_text_backend(profile_id)
    profile = bridge.get("active_profile") or None
    if not profile:
        yield _json_event("error", status="no_text_backend_profile", error="No compatible shared text backend profile is available for Roleplay.", bridge=bridge)
        return

    raw_payload = get_backend_profile_payload()
    raw_profile = _find_profile(_compatible_profiles(raw_payload), str(profile.get("profile_id") or "")) or {}
    if not _runtime_connected(raw_profile):
        runtime_status = _runtime_status(raw_profile).strip().lower() or "disconnected"
        yield _json_event("error", status="backend_disconnected", error=f"Roleplay text backend is not connected. Connect/Test the selected backend first. Runtime status: {runtime_status}.", bridge=bridge, active_profile_id=bridge.get("active_profile_id") or "")
        return
    connection = _profile_connection(raw_profile)
    provider_id = str(raw_profile.get("provider_id") or profile.get("provider_id") or "")
    base_url = str(connection.get("base_url") or profile.get("base_url") or "").strip()
    if not base_url:
        yield _json_event("error", status="missing_base_url", error="Active text backend profile has no base_url.", bridge=bridge)
        return

    defaults = raw_profile.get("generation_defaults") if isinstance(raw_profile.get("generation_defaults"), dict) else {}
    clean_messages = [m for m in (messages or []) if isinstance(m, dict) and str(m.get("content") or "").strip()]
    if not clean_messages:
        yield _json_event("error", status="missing_messages", error="No messages were provided for the Scene turn.", bridge=bridge)
        return

    model = str(connection.get("model") or profile.get("model") or "")
    timeout = int(timeout_seconds or connection.get("timeout_seconds") or 300)
    generation = {
        "max_tokens": int(max_tokens or defaults.get("max_tokens") or 512),
        "temperature": float(temperature if temperature is not None else defaults.get("temperature", 0.7)),
        "top_p": float(top_p if top_p is not None else defaults.get("top_p", 0.9)),
        "repetition_penalty": float(defaults.get("repetition_penalty", defaults.get("repeat_penalty", 1.12))),
    }

    if provider_id == "ollama":
        url = _join_url(base_url, connection.get("chat_path") or "/api/chat")
        options = {"temperature": generation["temperature"], "top_p": generation["top_p"], "num_predict": generation["max_tokens"], "repeat_penalty": generation["repetition_penalty"]}
        clean_stop = [str(x) for x in (stop or []) if str(x)]
        if clean_stop:
            options["stop"] = clean_stop
        req_payload = {
            "model": model,
            "messages": clean_messages,
            "stream": True,
            "options": options,
        }
        token_extractor = _extract_ollama_stream_token
    else:
        url = _join_url(base_url, connection.get("chat_path") or "/v1/chat/completions")
        req_payload = {"messages": clean_messages, "max_tokens": generation["max_tokens"], "temperature": generation["temperature"], "top_p": generation["top_p"], "repetition_penalty": generation["repetition_penalty"], "stream": True}
        clean_stop = [str(x) for x in (stop or []) if str(x)]
        if clean_stop:
            req_payload["stop"] = clean_stop
        if model:
            req_payload["model"] = model
        token_extractor = _extract_openai_stream_token

    headers = {"Content-Type": "application/json"}
    api_key = str(connection.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(url, data=json.dumps(req_payload).encode("utf-8"), headers=headers, method="POST")
    yield _json_event("backend_start", status="streaming", provider_id=provider_id, active_profile_id=bridge.get("active_profile_id") or "", request_url=url, generation=generation)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                token = token_extractor(data)
                if token:
                    yield _json_event("token", text=token)
                if provider_id == "ollama" and data.get("done") is True:
                    break
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        yield _json_event("error", status="http_error", error=f"HTTP {exc.code}: {body[:500]}", provider_id=provider_id, active_profile_id=bridge.get("active_profile_id") or "", request_url=url)
        return
    except Exception as exc:
        yield _json_event("error", status="request_error", error=str(exc), provider_id=provider_id, active_profile_id=bridge.get("active_profile_id") or "", request_url=url)
        return
    yield _json_event("backend_done", status="stream_complete", provider_id=provider_id, active_profile_id=bridge.get("active_profile_id") or "", request_url=url, generation=generation)
