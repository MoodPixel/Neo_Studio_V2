from __future__ import annotations

import json
import re
import socket
from typing import Any, Mapping
from urllib import error

COMFY_RUNTIME_ERROR_SCHEMA = "neo.runtime.comfy_error.v1"


def _compact(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _http_status(exc: BaseException | None) -> int | None:
    if isinstance(exc, error.HTTPError):
        try:
            return int(exc.code)
        except Exception:
            return None
    return None


def _exception_text(exc: BaseException | str | None) -> str:
    if exc is None:
        return ""
    if isinstance(exc, str):
        return exc.strip()
    parts = [str(exc).strip()]
    if isinstance(exc, error.URLError):
        reason = getattr(exc, "reason", None)
        if reason is not None:
            parts.append(str(reason).strip())
    return " · ".join(part for part in parts if part)


def _history_failure_payload(history_item: Mapping[str, Any] | None) -> dict[str, Any]:
    item = dict(history_item or {})
    status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
    status_str = str(status.get("status_str") or item.get("status_str") or "").strip().lower()
    messages = status.get("messages") if isinstance(status.get("messages"), list) else item.get("messages")
    messages = messages if isinstance(messages, list) else []

    event_name = ""
    event_payload: dict[str, Any] = {}
    for raw in reversed(messages):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        name = str(raw[0] or "").strip().lower()
        payload = raw[1] if isinstance(raw[1], Mapping) else {}
        if name in {"execution_error", "execution_failed", "execution_interrupted", "execution_cancelled"} or "error" in name or "fail" in name:
            event_name = name
            event_payload = dict(payload)
            break

    node_errors = item.get("node_errors") if isinstance(item.get("node_errors"), Mapping) else {}
    node_id = str(event_payload.get("node_id") or "").strip()
    node_type = str(event_payload.get("node_type") or event_payload.get("class_type") or "").strip()
    exception_type = str(event_payload.get("exception_type") or "").strip()
    message = str(
        event_payload.get("exception_message")
        or event_payload.get("message")
        or item.get("error")
        or ""
    ).strip()

    if not message and node_errors:
        first_node_id, first_error = next(iter(node_errors.items()))
        node_id = node_id or str(first_node_id or "").strip()
        if isinstance(first_error, Mapping):
            node_type = node_type or str(first_error.get("class_type") or first_error.get("node_type") or "").strip()
            errors = first_error.get("errors") if isinstance(first_error.get("errors"), list) else []
            if errors:
                candidate = errors[0]
                if isinstance(candidate, Mapping):
                    message = str(candidate.get("message") or candidate.get("details") or candidate.get("type") or "").strip()
                else:
                    message = str(candidate or "").strip()
            message = message or str(first_error.get("message") or first_error.get("error") or "").strip()
        else:
            message = str(first_error or "").strip()

    failed = bool(
        status_str in {"error", "failed", "execution_error", "execution_failed"}
        or event_name
        or node_errors
        or item.get("error")
    )
    cancelled = event_name in {"execution_interrupted", "execution_cancelled"}
    return {
        "failed": failed,
        "cancelled": cancelled,
        "status_str": status_str,
        "event": event_name,
        "node_id": node_id,
        "node_type": node_type,
        "exception_type": exception_type,
        "message": message,
    }


def classify_comfy_runtime_error(
    source: BaseException | str | None = None,
    *,
    phase: str = "runtime",
    history_item: Mapping[str, Any] | None = None,
    status_code: int | None = None,
    model: str = "",
    mmproj: str = "",
    prompt_id: str = "",
) -> dict[str, Any]:
    """Normalize Comfy/llama.cpp failures into one UI-safe recovery contract.

    This is deliberately classification-only. It never retries or mutates the
    backend; callers decide whether a GPU lease can be released safely.
    """
    history = _history_failure_payload(history_item)
    raw = history.get("message") or _exception_text(source)
    exception_type = str(history.get("exception_type") or "")
    node_type = str(history.get("node_type") or "")
    event = str(history.get("event") or "")
    effective_status = status_code if status_code is not None else (_http_status(source) if isinstance(source, BaseException) else None)
    low = " ".join([str(raw), exception_type, node_type, event, str(effective_status or "")]).casefold()

    code = "comfy_runtime_failed"
    category = "execution"
    message = "ComfyUI could not complete the request."
    action = "Review the ComfyUI console, then retry the request."
    recoverable = False
    retryable = True
    fatal_batch = False
    gpu_state_uncertain = False
    cleanup_recommended = False

    cancelled = bool(history.get("cancelled")) or any(marker in low for marker in ("execution_interrupted", "execution_cancelled", "cancelled", "canceled"))
    oom = any(marker in low for marker in (
        "cuda out of memory", "out of memory", "cublas_status_alloc_failed", "cudnn_status_alloc_failed",
        "failed to allocate", "cannot allocate memory", "insufficient memory", "ggml_cuda", "allocation on device",
    ))
    backend_offline = (
        isinstance(source, (ConnectionError, ConnectionRefusedError, ConnectionResetError, socket.timeout, TimeoutError, error.URLError))
        or any(marker in low for marker in (
            "connection refused", "actively refused", "connection reset", "connection aborted", "remote end closed",
            "remotedisconnected", "broken pipe", "winerror 10061", "winerror 10054", "name or service not known",
            "temporary failure in name resolution", "no route to host", "network is unreachable",
        ))
    )
    timeout = any(marker in low for marker in ("timed out", "timeout", "time out"))
    validation = effective_status in {400, 422} or any(marker in low for marker in (
        "prompt outputs failed validation", "failed validation", "invalid prompt", "validation error", "value not in list",
    ))
    missing_node = any(marker in low for marker in (
        "node does not exist", "node not found", "class_type", "unknown node", "missing node", "cannot execute because a node",
        "neoPromptCaption".casefold(), "llama_cpp_model_loader", "llama_cpp_instruct_adv",
    )) and any(marker in low for marker in ("missing", "not found", "does not exist", "unknown", "invalid", "cannot execute"))
    missing_mmproj = any(marker in low for marker in ("mmproj", "projector", "clip_model_path")) and any(marker in low for marker in (
        "not found", "missing", "no such file", "does not exist", "value not in list", "unavailable",
    ))
    missing_model = not missing_mmproj and any(marker in low for marker in ("model", ".gguf", "llama")) and any(marker in low for marker in (
        "not found", "missing", "no such file", "does not exist", "value not in list", "unavailable", "failed to load model",
    ))
    history_lost = any(marker in low for marker in ("history lost", "history missing", "prompt disappeared", "prompt no longer exists", "history_lost"))
    cleanup_failed = str(phase).casefold() in {"cleanup", "free"} or "/free" in low

    if cancelled:
        code = "comfy_execution_cancelled"
        category = "cancelled"
        message = "ComfyUI stopped the request before it completed."
        action = "Retry when you are ready. If you did not cancel it, check the ComfyUI console for an interrupt or restart."
        recoverable = True
        retryable = True
    elif oom:
        code = "comfy_cuda_oom"
        category = "resource"
        message = "ComfyUI ran out of GPU memory while loading or running this workflow."
        action = "Use a smaller GGUF/model, lower the Comfy VRAM Budget or context/image analysis size, keep Unload After Run enabled, then retry after cleanup."
        recoverable = True
        retryable = True
        fatal_batch = True
        cleanup_recommended = True
    elif cleanup_failed:
        code = "comfy_cleanup_failed"
        category = "cleanup"
        message = "The Comfy job finished, but Neo could not confirm the explicit model/memory cleanup."
        action = "Neo will retry cleanup before later Comfy work. If VRAM remains high, restart ComfyUI before retrying a heavy Image/Video job."
        recoverable = True
        retryable = True
        cleanup_recommended = True
    elif backend_offline and timeout:
        code = "comfy_backend_unreachable_timeout"
        category = "connection"
        message = "ComfyUI stopped responding before the request reached a confirmed terminal state."
        action = "Check or restart ComfyUI. Neo keeps the shared GPU guarded until the backend returns and the old queue can be proven clear."
        recoverable = True
        retryable = True
        fatal_batch = True
        gpu_state_uncertain = True
    elif backend_offline:
        code = "comfy_backend_unreachable"
        category = "connection"
        message = "Neo lost the connection to ComfyUI during this request."
        action = "Start or restart ComfyUI, then run Connect/Test. Neo will reconcile the old Comfy queue before allowing another shared-GPU job."
        recoverable = True
        retryable = True
        fatal_batch = True
        gpu_state_uncertain = True
    elif timeout:
        code = "comfy_request_timeout"
        category = "timeout"
        message = "ComfyUI did not reach a confirmed terminal state before Neo's request timeout."
        action = "Wait for ComfyUI to finish or recover. Neo keeps the shared GPU guarded while the prompt may still be running."
        recoverable = True
        retryable = True
        gpu_state_uncertain = bool(prompt_id) or str(phase).casefold() == "queue"
    elif missing_mmproj:
        code = "comfy_mmproj_missing"
        category = "configuration"
        message = "The selected VLM vision projector (mmproj) is missing or no longer valid in ComfyUI."
        action = "Run Connect/Test, choose a currently detected mmproj, or restore the matching projector file, then retry."
        recoverable = True
        retryable = True
        fatal_batch = True
    elif missing_model:
        code = "comfy_model_missing"
        category = "configuration"
        message = "The selected LLM/VLM model is missing or could not be loaded by ComfyUI."
        action = "Run Connect/Test, choose a currently detected GGUF model, or restore the model file, then retry."
        recoverable = True
        retryable = True
        fatal_batch = True
    elif missing_node:
        code = "comfy_node_missing"
        category = "dependency"
        message = "A required Comfy llama.cpp or Neo bridge node is missing or changed."
        action = "Restart ComfyUI after installing/updating the required custom nodes, then run Connect/Test before retrying."
        recoverable = True
        retryable = True
        fatal_batch = True
    elif validation:
        code = "comfy_workflow_validation_failed"
        category = "validation"
        message = "ComfyUI rejected the compiled LLM/VLM workflow before execution."
        action = "Run Connect/Test to refresh the live node/model catalog, then verify the selected model, mmproj, and chat handler."
        recoverable = True
        retryable = True
        fatal_batch = True
    elif history_lost:
        code = "comfy_history_lost"
        category = "recovery"
        message = "The old Comfy prompt is no longer present in either history or the active queue."
        action = "Neo released the stale GPU guard after confirming the backend is clear. Rerun the request if you still need the result."
        recoverable = True
        retryable = True
        fatal_batch = True
    elif history.get("failed"):
        code = "comfy_execution_failed"
        category = "execution"
        location = ""
        if history.get("node_id") and history.get("node_type"):
            location = f" at node {history['node_id']} ({history['node_type']})"
        elif history.get("node_type"):
            location = f" in {history['node_type']}"
        message = f"ComfyUI execution failed{location}."
        action = "Review the ComfyUI console and the selected model/backend settings, then retry."
        retryable = True

    detail = _compact(raw or exception_type or event or code)
    if detail and detail.casefold() not in message.casefold():
        friendly_detail = detail
    else:
        friendly_detail = ""

    return {
        "schema_id": COMFY_RUNTIME_ERROR_SCHEMA,
        "code": code,
        "category": category,
        "phase": str(phase or "runtime"),
        "message": message,
        "detail": friendly_detail,
        "next_action": action,
        "recoverable": recoverable,
        "retryable": retryable,
        "fatal_batch": fatal_batch,
        "gpu_state_uncertain": gpu_state_uncertain,
        "cleanup_recommended": cleanup_recommended,
        "status_code": effective_status,
        "prompt_id": str(prompt_id or ""),
        "model": str(model or ""),
        "mmproj": str(mmproj or ""),
        "node_id": str(history.get("node_id") or ""),
        "node_type": node_type,
        "exception_type": exception_type,
        "raw_detail": _compact(raw, 1800),
    }


def comfy_error_text(record: Mapping[str, Any] | None) -> str:
    data = dict(record or {})
    message = str(data.get("message") or "ComfyUI request failed.").strip()
    detail = str(data.get("detail") or "").strip()
    action = str(data.get("next_action") or "").strip()
    pieces = [message]
    if detail:
        pieces.append(detail)
    if action:
        pieces.append(f"Next: {action}")
    return " ".join(piece for piece in pieces if piece)


def safe_http_body(exc: BaseException) -> str:
    if not isinstance(exc, error.HTTPError):
        return ""
    try:
        raw = exc.read()
    except Exception:
        return ""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw or "")
    text = text.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return _compact(text, 1800)
    return _compact(json.dumps(parsed, ensure_ascii=False), 1800)


__all__ = [
    "COMFY_RUNTIME_ERROR_SCHEMA",
    "classify_comfy_runtime_error",
    "comfy_error_text",
    "safe_http_body",
]
