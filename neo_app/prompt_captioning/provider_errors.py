from __future__ import annotations

from typing import Any

PROMPT_CAPTIONING_PROVIDER_ERROR_SCHEMA = "neo.prompt_captioning.provider_error.v1"


def _compact(text: Any, limit: int = 700) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _profile_label(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "selected backend profile"
    return str(profile.get("profile_id") or profile.get("name") or "selected backend profile")


def _operation_label(operation: str) -> str:
    return "Caption Studio" if operation == "caption" else "Prompt Studio"


def normalize_prompt_captioning_provider_error(
    result: dict[str, Any] | BaseException | str | None,
    *,
    operation: str = "prompt",
    profile: dict[str, Any] | None = None,
    fallback: str = "Prompt/Captioning provider failed.",
) -> dict[str, Any]:
    """Return a UI-safe, source-preserving provider error payload.

    This helper intentionally does not retry, reroute, or mutate provider behavior.
    It only converts raw provider/gating failures into clearer local diagnostics.
    """
    if isinstance(result, dict):
        raw_error = result.get("error") or result.get("provider_error") or result.get("message") or result.get("detail") or fallback
        raw_type = str(result.get("error_type") or result.get("code") or "").strip().lower()
        recoverable = bool(result.get("recoverable"))
        partial_text = str(result.get("partial_text") or result.get("text") or "").strip()
        finish_reason = str(result.get("finish_reason") or "").strip()
    else:
        raw_error = str(result or fallback)
        raw_type = ""
        recoverable = False
        partial_text = ""
        finish_reason = ""
    text = str(raw_error or fallback)
    low = f"{raw_type} {text}".lower()
    op_label = _operation_label(operation)
    profile_name = _profile_label(profile)

    if partial_text and recoverable:
        code = "prompt_captioning_provider_recovered"
        title = f"{op_label} recovered partial output."
        message = "The backend connection ended early, but Neo recovered usable text."
        actions = ["Review the recovered output before saving.", "If this repeats, lower max tokens or check the backend connection."]
    elif any(marker in low for marker in ("timeout", "timed out", "time out")):
        code = "prompt_captioning_provider_timeout"
        title = f"{op_label} backend timed out."
        message = "The selected backend did not respond before the timeout."
        actions = ["Check whether the text/caption backend is busy.", "Increase the backend timeout or lower max tokens.", "Try again after the backend finishes the current job."]
    elif any(marker in low for marker in ("connection refused", "failed to establish", "urlopen error", "not reachable", "actively refused", "winerror 10061", "connection aborted", "remotedisconnected", "broken pipe")):
        code = "prompt_captioning_provider_unreachable"
        title = f"{op_label} backend is not reachable."
        message = "Neo could not reach the selected Prompt/Captioning backend."
        actions = ["Start KoboldCpp or the selected local backend.", "Check the backend URL/profile in Admin.", "Refresh backend status, then try again."]
    elif any(marker in low for marker in ("model", "not found", "unknown model", "missing model", "does not exist")):
        code = "prompt_captioning_model_missing"
        title = "Selected backend model is missing."
        message = "The selected profile points to a model that the backend could not load or find."
        actions = ["Check the model name in the backend profile.", "Load the model in the backend.", "Use another Prompt/Captioning profile."]
    elif "vision" in low or "caption" in low or "text-only" in low or "unsupported" in low:
        code = "prompt_captioning_capability_unsupported"
        title = f"{op_label} capability is not supported by this profile."
        message = "The selected backend profile does not support the requested Prompt/Captioning operation."
        actions = ["For captions, choose a vision-capable caption backend.", "For prompts, choose a text-capable backend profile.", "Check Prompt & Captioning support matrix in Admin."]
    elif "invalid json" in low or "invalid response" in low or "empty" in low:
        code = "prompt_captioning_invalid_provider_response"
        title = f"{op_label} backend returned an invalid response."
        message = "Neo reached the backend, but the response did not contain usable output."
        actions = ["Try again with a simpler prompt/instruction.", "Check backend logs for response-format errors.", "Lower max tokens if the response is being cut off."]
    elif raw_type in {"provider_http_error", "http_error"} or "http" in low:
        code = "prompt_captioning_provider_http_error"
        title = f"{op_label} backend returned an HTTP error."
        message = "The backend rejected the Prompt/Captioning request."
        actions = ["Check backend logs for the exact HTTP error.", "Confirm URL, model, and API-key settings in the backend profile."]
    else:
        code = "prompt_captioning_provider_error"
        title = f"{op_label} backend failed."
        message = "The selected backend could not complete the request."
        actions = ["Check the selected backend profile.", "Review backend logs.", "Try again with a smaller request."]

    return {
        "schema": PROMPT_CAPTIONING_PROVIDER_ERROR_SCHEMA,
        "error_code": code,
        "title": title,
        "message": message,
        "detail": _compact(text),
        "raw_detail": _compact(text, 1400),
        "operation": operation,
        "profile_id": str((profile or {}).get("profile_id") or ""),
        "provider_id": str((profile or {}).get("provider_id") or ""),
        "profile_label": profile_name,
        "recoverable": recoverable,
        "partial_text": partial_text,
        "finish_reason": finish_reason,
        "recovery_actions": actions,
    }


def provider_error_response(
    result: dict[str, Any] | BaseException | str | None,
    *,
    operation: str,
    profile: dict[str, Any] | None,
    fallback: str,
) -> dict[str, Any]:
    normalized = normalize_prompt_captioning_provider_error(result, operation=operation, profile=profile, fallback=fallback)
    partial_text = normalized.get("partial_text") or ""
    return {
        "ok": bool(partial_text),
        "recoverable": bool(normalized.get("recoverable")),
        "partial_text": partial_text,
        "text": partial_text,
        "warning": normalized.get("message") if normalized.get("recoverable") else "",
        "provider_error": normalized,
        "error_type": normalized.get("error_code") or "",
        "finish_reason": normalized.get("finish_reason") or "",
        "errors": [] if partial_text else [normalized.get("message") or fallback],
    }
