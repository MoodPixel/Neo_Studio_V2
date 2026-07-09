from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator
from urllib import request, error

from .execution import clamp_generation_params, strip_reasoning_text


def _connection(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("connection", {}) or {}


def _generation_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("generation_defaults", {}) or {}


def _chat_url(profile: dict[str, Any]) -> str:
    connection = _connection(profile)
    base_url = str(connection.get("base_url") or "http://127.0.0.1:5001").rstrip("/")
    chat_path = str(connection.get("chat_path") or "/v1/chat/completions")
    if not chat_path.startswith("/"):
        chat_path = f"/{chat_path}"
    return f"{base_url}{chat_path}"


def _headers(profile: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    connection = _connection(profile)
    api_key_mode = str(connection.get("api_key_mode") or "none").lower()
    api_key_env = str(connection.get("api_key_env") or "").strip()
    if api_key_mode != "none" and api_key_env:
        token = os.environ.get(api_key_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


RECOVERABLE_CONNECTION_ERROR_MARKERS = (
    "winerror 10053",
    "connection aborted",
    "connection was terminated",
    "remotedisconnected",
    "chunkedencodingerror",
    "connectionerror",
    "an established connection was aborted",
    "broken pipe",
    "incomplete read",
)

_WRAPPER_RE = re.compile(r"^\s*<(response|caption|answer)>\s*(.*?)\s*</\1>\s*$", re.IGNORECASE | re.DOTALL)
_ANY_WRAPPER_RE = re.compile(r"<(response|caption|answer)>\s*(.*?)\s*</\1>", re.IGNORECASE | re.DOTALL)
_PREFIX_RE = re.compile(r"^\s*(assistant|caption|prompt|response|answer)\s*:\s*", re.IGNORECASE)


def is_recoverable_connection_error(exc: BaseException | str) -> bool:
    """Return True when a KoboldCpp/OpenAI-compatible transport error is recoverable."""
    text = str(exc or "").lower()
    return any(marker in text for marker in RECOVERABLE_CONNECTION_ERROR_MARKERS)


def clean_model_text(text: str) -> tuple[str, bool]:
    """Strip common model wrapper tags/prefixes while preserving useful text."""
    original = str(text or "").strip()
    if not original:
        return "", False
    changed = False
    match = _WRAPPER_RE.match(original)
    if match:
        original = match.group(2).strip()
        changed = True
    else:
        inner = _ANY_WRAPPER_RE.search(original)
        if inner and inner.group(2).strip():
            original = inner.group(2).strip()
            changed = True
    for _ in range(3):
        new = _PREFIX_RE.sub("", original).strip()
        if new == original:
            break
        original = new
        changed = True
    return original.strip(), changed


def _recoverable_failure(exc: BaseException | str, partial_text: str = "") -> dict[str, Any]:
    text, wrapper_stripped = clean_model_text(partial_text)
    warning = "KoboldCpp connection ended early, but Neo recovered usable text." if text else "KoboldCpp generated output may have been interrupted before Neo received the final response."
    return {
        "ok": bool(text),
        "recoverable": True,
        "error_type": "provider_connection_aborted",
        "error": f"KoboldCpp request failed: {exc}",
        "warning": warning,
        "text": text,
        "partial_text": text,
        "finish_reason": "connection_aborted_after_text" if text else "connection_aborted_before_text",
        "wrapper_stripped": wrapper_stripped,
    }


def _decode_error_body(exc: error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
    except Exception:  # noqa: BLE001
        return str(exc)


def _extract_text(payload: Any) -> str:
    """Extract useful text from OpenAI-compatible and KoboldCpp-style responses."""
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            message = first.get("message") if isinstance(first, dict) else None
            if isinstance(message, dict) and message.get("content") is not None:
                return str(message.get("content") or "").strip()
            if isinstance(first, dict) and first.get("text") is not None:
                return str(first.get("text") or "").strip()
            if isinstance(first, dict) and first.get("content") is not None:
                return str(first.get("content") or "").strip()
        results = payload.get("results")
        if isinstance(results, list) and results:
            first_result = results[0] or {}
            if isinstance(first_result, dict):
                for key in ("text", "content", "response", "output"):
                    if first_result.get(key) is not None:
                        return str(first_result.get(key) or "").strip()
            elif first_result is not None:
                return str(first_result or "").strip()
        for key in ("response", "text", "content", "output", "result", "generated_text"):
            if payload.get(key) is not None:
                return str(payload.get(key) or "").strip()
    if isinstance(payload, str):
        return payload.strip()
    return ""


def _finish_reason(payload: Any) -> str:
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return str(choices[0].get("finish_reason") or "")
        return str(payload.get("finish_reason") or "")
    return ""




def _extract_openai_stream_token(payload: Any) -> str:
    if isinstance(payload, dict):
        choices = payload.get("choices")
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
        for key in ("token", "text", "content", "response"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return ""


def run_chat_stream(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Stream an OpenAI-compatible chat request against KoboldCpp/local text backends.

    Emits provider-neutral event dicts: backend_start, token, backend_done, or error.
    The caller owns final persistence because streamed output usually needs a
    post-cleaning pass before it becomes saved Assistant text.
    """
    connection = _connection(profile)
    defaults = _generation_defaults(profile)
    params = params or {}
    model = str(params.get("model") or connection.get("model") or defaults.get("model") or "default")
    clean_params = clamp_generation_params(params, defaults)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": clean_params["temperature"],
        "top_p": clean_params["top_p"],
        "max_tokens": clean_params["max_tokens"],
        "stream": True,
    }
    if "top_k" in clean_params:
        body["top_k"] = clean_params["top_k"]
    stop_sequences = clean_params.get("stop_sequences")
    if stop_sequences:
        body["stop"] = stop_sequences
    timeout = float(connection.get("timeout_seconds") or 300)
    req = request.Request(_chat_url(profile), data=json.dumps(body).encode("utf-8"), headers=_headers(profile), method="POST")
    yield {
        "type": "backend_start",
        "status": "streaming",
        "provider": profile.get("provider_id") or "koboldcpp",
        "backend_profile_id": profile.get("profile_id") or "",
        "model": model,
    }
    raw_text = ""
    try:
        with request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # Some local servers may stream raw text chunks. Treat them as tokens.
                    token = line
                else:
                    token = _extract_openai_stream_token(payload)
                if token:
                    raw_text += token
                    yield {"type": "token", "text": token}
    except error.HTTPError as exc:
        detail = _decode_error_body(exc)
        yield {
            "type": "error",
            "status": "provider_http_error",
            "recoverable": is_recoverable_connection_error(detail),
            "error": f"KoboldCpp returned HTTP {exc.code}: {detail}",
            "partial_text": raw_text,
            "provider": profile.get("provider_id") or "koboldcpp",
            "backend_profile_id": profile.get("profile_id") or "",
            "model": model,
        }
        return
    except Exception as exc:  # noqa: BLE001
        yield {
            "type": "error",
            "status": "provider_request_error",
            "recoverable": is_recoverable_connection_error(exc),
            "error": f"KoboldCpp stream failed: {exc}",
            "partial_text": raw_text,
            "provider": profile.get("provider_id") or "koboldcpp",
            "backend_profile_id": profile.get("profile_id") or "",
            "model": model,
        }
        return
    yield {
        "type": "backend_done",
        "status": "stream_complete",
        "text": raw_text,
        "provider": profile.get("provider_id") or "koboldcpp",
        "backend_profile_id": profile.get("profile_id") or "",
        "model": model,
    }

def run_chat(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an OpenAI-compatible chat request against KoboldCpp/local text backends."""
    connection = _connection(profile)
    defaults = _generation_defaults(profile)
    params = params or {}
    model = str(params.get("model") or connection.get("model") or defaults.get("model") or "default")
    clean_params = clamp_generation_params(params, defaults)
    body = {
        "model": model,
        "messages": messages,
        "temperature": clean_params["temperature"],
        "top_p": clean_params["top_p"],
        "max_tokens": clean_params["max_tokens"],
        "stream": False,
    }
    if "top_k" in clean_params:
        body["top_k"] = clean_params["top_k"]
    stop_sequences = clean_params.get("stop_sequences")
    if stop_sequences:
        body["stop"] = stop_sequences
    timeout = float(connection.get("timeout_seconds") or 300)
    req = request.Request(_chat_url(profile), data=json.dumps(body).encode("utf-8"), headers=_headers(profile), method="POST")
    raw = ""
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                salvaged, wrapper_stripped = clean_model_text(raw)
                if salvaged:
                    text, reasoning_stripped = strip_reasoning_text(salvaged)
                    return {
                        "ok": True,
                        "recoverable": True,
                        "text": text,
                        "partial_text": text,
                        "warning": "KoboldCpp returned invalid JSON, but Neo recovered usable text.",
                        "error_type": "provider_invalid_json_salvaged",
                        "error": f"KoboldCpp returned invalid JSON: {exc}",
                        "finish_reason": "invalid_json_after_text",
                        "reasoning_stripped": reasoning_stripped,
                        "wrapper_stripped": wrapper_stripped,
                        "raw_text": raw,
                        "provider": profile.get("provider_id") or "koboldcpp",
                        "backend_profile_id": profile.get("profile_id") or "",
                        "model": model,
                    }
                return {"ok": False, "recoverable": False, "error_type": "provider_invalid_json", "error": f"KoboldCpp returned invalid JSON: {exc}"}
    except error.HTTPError as exc:
        detail = _decode_error_body(exc)
        salvaged, wrapper_stripped = clean_model_text(detail)
        if salvaged and is_recoverable_connection_error(detail):
            text, reasoning_stripped = strip_reasoning_text(salvaged)
            return {
                "ok": True,
                "recoverable": True,
                "text": text,
                "partial_text": text,
                "warning": "KoboldCpp connection ended early, but Neo recovered usable text.",
                "error_type": "provider_connection_aborted",
                "error": f"KoboldCpp returned HTTP {exc.code}: {detail}",
                "finish_reason": "connection_aborted_after_text",
                "reasoning_stripped": reasoning_stripped,
                "wrapper_stripped": wrapper_stripped,
                "provider": profile.get("provider_id") or "koboldcpp",
                "backend_profile_id": profile.get("profile_id") or "",
                "model": model,
            }
        return {"ok": False, "recoverable": is_recoverable_connection_error(detail), "error_type": "provider_http_error", "error": f"KoboldCpp returned HTTP {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001 - provider failures should return UI-safe errors.
        if is_recoverable_connection_error(exc):
            recovered = _recoverable_failure(exc, raw)
            recovered.update({
                "provider": profile.get("provider_id") or "koboldcpp",
                "backend_profile_id": profile.get("profile_id") or "",
                "model": model,
            })
            return recovered
        return {"ok": False, "recoverable": False, "error_type": "provider_request_failed", "error": f"KoboldCpp request failed: {exc}"}
    extracted, wrapper_stripped = clean_model_text(_extract_text(payload))
    text, reasoning_stripped = strip_reasoning_text(extracted)
    return {
        "ok": bool(text),
        "text": text,
        "partial_text": text,
        "recoverable": False,
        "reasoning_stripped": reasoning_stripped,
        "wrapper_stripped": wrapper_stripped,
        "finish_reason": _finish_reason(payload),
        "raw": payload,
        "provider": profile.get("provider_id") or "koboldcpp",
        "backend_profile_id": profile.get("profile_id") or "",
        "model": model,
    }
