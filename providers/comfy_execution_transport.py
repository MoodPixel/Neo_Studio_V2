from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, AsyncIterator, Mapping
from urllib import error, parse, request

import websockets

from neo_app.providers.profiles import get_backend_profile_for_live_task, get_backend_profile_for_runtime


COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION = "neo.comfy_execution_transport.v1"
_COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}


class ComfyExecutionTransportError(RuntimeError):
    """Safe selected-runtime Comfy execution transport failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_payload: Any = None,
        queue_state_uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = response_payload
        self.queue_state_uncertain = bool(queue_state_uncertain)


def _profile_base_url(profile: Mapping[str, Any]) -> str:
    connection = profile.get("connection") if isinstance(profile.get("connection"), Mapping) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), Mapping) else {}
    return str(connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188").strip().rstrip("/")


def resolve_comfy_execution_profile(profile_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise ComfyExecutionTransportError("A Comfy backend profile id is required for Space execution.")
    profile = get_backend_profile_for_live_task(clean_profile_id) or get_backend_profile_for_runtime(clean_profile_id)
    if not profile:
        raise ComfyExecutionTransportError(f"Unknown backend profile: {clean_profile_id}")
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id not in _COMFY_PROVIDER_IDS:
        raise ComfyExecutionTransportError(f"Backend profile '{clean_profile_id}' is not a ComfyUI profile.")
    if profile.get("enabled") is False:
        raise ComfyExecutionTransportError(f"Backend profile '{clean_profile_id}' is disabled.")
    base_url = _profile_base_url(profile)
    parsed = parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ComfyExecutionTransportError("ComfyUI profile base URL must use http or https.")
    connection = profile.get("connection") if isinstance(profile.get("connection"), Mapping) else {}
    request_timeout = float(timeout if timeout is not None else connection.get("timeout_seconds") or 10.0)
    request_timeout = max(0.5, min(request_timeout, 120.0))
    return {
        "profile_id": clean_profile_id,
        "provider_id": provider_id,
        "base_url": base_url,
        "timeout": request_timeout,
    }


def _decode_payload(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _request_json(
    resolved: Mapping[str, Any],
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    allow_empty: bool = False,
    timeout: float | None = None,
    queue_state_uncertain_on_transport_error: bool = False,
) -> Any:
    clean_path = path if path.startswith("/") else f"/{path}"
    url = f"{str(resolved['base_url']).rstrip('/')}{clean_path}"
    data = None if payload is None else json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=float(timeout if timeout is not None else resolved["timeout"])) as response:  # noqa: S310 - selected local/admin profile.
            raw = response.read()
            if not raw and allow_empty:
                return {"ok": True, "status_code": int(getattr(response, "status", 200) or 200)}
            return _decode_payload(raw)
    except error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        decoded = _decode_payload(raw)
        detail = decoded if isinstance(decoded, dict) else {"detail": str(decoded)}
        raise ComfyExecutionTransportError(
            f"ComfyUI {method} {clean_path} failed with HTTP {exc.code}: {detail}",
            status_code=int(exc.code),
            response_payload=decoded,
            queue_state_uncertain=False,
        ) from exc
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ComfyExecutionTransportError(
            f"Unable to reach ComfyUI for {method} {clean_path}: {exc}",
            queue_state_uncertain=queue_state_uncertain_on_transport_error,
        ) from exc



def _execution_ws_url(resolved: Mapping[str, Any], client_id: str) -> str:
    base_url = str(resolved.get("base_url") or "").rstrip("/")
    parsed = parse.urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc
    prefix = parsed.path.rstrip("/")
    if not netloc:
        raise ComfyExecutionTransportError("ComfyUI profile base URL cannot be converted to a websocket endpoint.")
    return f"{scheme}://{netloc}{prefix}/ws?clientId={parse.quote(str(client_id or ''), safe='')}"


async def stream_comfy_prompt_events(
    profile_id: str,
    *,
    client_id: str,
    prompt_id: str,
    receive_timeout_seconds: float = 25.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield selected-prompt Comfy websocket events without exposing backend URLs.

    Binary preview frames are intentionally ignored by the NCS-8 persistent
    progress monitor. The Image/Video workspaces have separate transient preview
    lanes; Comfy Spaces persists only small graph/progress facts so long-running
    jobs remain resumable without filling the runtime registry with media blobs.
    """

    clean_client_id = str(client_id or "").strip()
    clean_prompt_id = str(prompt_id or "").strip()
    if not clean_client_id or not clean_prompt_id:
        raise ComfyExecutionTransportError("client_id and prompt_id are required for Comfy live progress.")
    resolved = resolve_comfy_execution_profile(profile_id)
    ws_url = _execution_ws_url(resolved, clean_client_id)
    timeout = max(2.0, min(float(receive_timeout_seconds or 25.0), 120.0))
    try:
        async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=20) as upstream:
            while True:
                try:
                    raw = await asyncio.wait_for(upstream.recv(), timeout=timeout)
                except asyncio.TimeoutError as exc:
                    raise ComfyExecutionTransportError(
                        "ComfyUI live progress was silent long enough to require history/queue reconciliation."
                    ) from exc
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(str(raw or "{}"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, Mapping):
                    continue
                event_type = str(message.get("type") or "").strip().lower()
                data = message.get("data") if isinstance(message.get("data"), Mapping) else {}
                event_prompt_id = str(data.get("prompt_id") or "").strip()
                if event_prompt_id and event_prompt_id != clean_prompt_id:
                    continue
                # Queue/status messages are session-scoped rather than prompt-scoped.
                # The client id is unique per Space run, so keeping them is safe and
                # useful for navigation-safe queue feedback. Older/reconnected Comfy
                # builds can also emit `executing` without prompt_id.
                if not event_prompt_id and event_type not in {
                    "status",
                    "executing",
                    "progress",
                    "progress_state",
                    "execution_cached",
                    "executed",
                    "execution_start",
                    "execution_success",
                    "execution_error",
                    "execution_interrupted",
                    "execution_cancelled",
                    "execution_canceled",
                }:
                    continue
                yield {
                    "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
                    "type": event_type,
                    "data": dict(data),
                    "profile_id": str(resolved.get("profile_id") or profile_id),
                    "provider_id": str(resolved.get("provider_id") or "comfyui"),
                    "prompt_id": clean_prompt_id,
                    "base_url_exposed": False,
                }
    except ComfyExecutionTransportError:
        raise
    except Exception as exc:  # noqa: BLE001 - websocket disconnect is recoverable telemetry failure.
        raise ComfyExecutionTransportError(f"Unable to read ComfyUI live progress: {exc}") from exc

def submit_comfy_prompt(
    profile_id: str,
    *,
    workflow: Mapping[str, Any],
    client_id: str,
    extra_data: Mapping[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    if not isinstance(workflow, Mapping) or not workflow:
        raise ComfyExecutionTransportError("Comfy Space execution requires a non-empty canonical workflow graph.")
    clean_client_id = str(client_id or "").strip()
    if not clean_client_id:
        raise ComfyExecutionTransportError("A Comfy client_id is required for Space execution.")
    resolved = resolve_comfy_execution_profile(profile_id, timeout=timeout)
    payload: dict[str, Any] = {
        "prompt": dict(workflow),
        "client_id": clean_client_id,
        "extra_data": dict(extra_data or {}),
    }
    response = _request_json(
        resolved,
        "/prompt",
        method="POST",
        payload=payload,
        timeout=timeout,
        queue_state_uncertain_on_transport_error=True,
    )
    if not isinstance(response, Mapping):
        raise ComfyExecutionTransportError("ComfyUI /prompt returned an invalid response.", queue_state_uncertain=True)
    prompt_id = str(response.get("prompt_id") or "").strip()
    if not prompt_id:
        error_payload = response.get("error") or response.get("node_errors") or response
        raise ComfyExecutionTransportError(
            f"ComfyUI rejected the Space workflow: {error_payload}",
            response_payload=dict(response),
            queue_state_uncertain=False,
        )
    return {
        "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "prompt_id": prompt_id,
        "number": response.get("number"),
        "node_errors": dict(response.get("node_errors") or {}) if isinstance(response.get("node_errors"), Mapping) else {},
        "client_id": clean_client_id,
        "base_url_exposed": False,
    }


def get_comfy_prompt_history(profile_id: str, *, prompt_id: str, timeout: float | None = None) -> dict[str, Any]:
    clean_prompt_id = str(prompt_id or "").strip()
    if not clean_prompt_id:
        raise ComfyExecutionTransportError("prompt_id is required to read Comfy history.")
    resolved = resolve_comfy_execution_profile(profile_id, timeout=timeout)
    payload = _request_json(resolved, f"/history/{parse.quote(clean_prompt_id, safe='')}", timeout=timeout)
    history = dict(payload) if isinstance(payload, Mapping) else {}
    item = history.get(clean_prompt_id) if isinstance(history.get(clean_prompt_id), Mapping) else None
    return {
        "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "prompt_id": clean_prompt_id,
        "found": isinstance(item, Mapping),
        "history_item": dict(item) if isinstance(item, Mapping) else None,
        "base_url_exposed": False,
    }


def _queue_prompt_ids(rows: Any) -> list[str]:
    found: list[str] = []
    if not isinstance(rows, list):
        return found
    for row in rows:
        prompt_id = ""
        if isinstance(row, Mapping):
            prompt_id = str(row.get("prompt_id") or row.get("id") or "").strip()
        elif isinstance(row, (list, tuple)) and len(row) > 1:
            prompt_id = str(row[1] or "").strip()
        if prompt_id and prompt_id not in found:
            found.append(prompt_id)
    return found


def get_comfy_queue_state(profile_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    resolved = resolve_comfy_execution_profile(profile_id, timeout=timeout)
    payload = _request_json(resolved, "/queue", timeout=timeout)
    data = dict(payload) if isinstance(payload, Mapping) else {}
    running_ids = _queue_prompt_ids(data.get("queue_running") or data.get("running"))
    pending_ids = _queue_prompt_ids(data.get("queue_pending") or data.get("pending"))
    return {
        "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "running_prompt_ids": running_ids,
        "pending_prompt_ids": pending_ids,
        "queue_remaining": len(running_ids) + len(pending_ids),
        "base_url_exposed": False,
    }


def cancel_comfy_prompt(profile_id: str, *, prompt_id: str, timeout: float | None = None) -> dict[str, Any]:
    clean_prompt_id = str(prompt_id or "").strip()
    if not clean_prompt_id:
        raise ComfyExecutionTransportError("prompt_id is required to cancel a Comfy Space run.")
    resolved = resolve_comfy_execution_profile(profile_id, timeout=timeout)
    queue = get_comfy_queue_state(profile_id, timeout=timeout)
    state = "not_queued"
    action = "none"
    if clean_prompt_id in queue["pending_prompt_ids"]:
        _request_json(resolved, "/queue", method="POST", payload={"delete": [clean_prompt_id]}, allow_empty=True, timeout=timeout)
        state = "cancelled_pending"
        action = "queue_delete"
    elif clean_prompt_id in queue["running_prompt_ids"]:
        # Current ComfyUI supports prompt-scoped interruption. Older builds may
        # ignore the JSON body and perform a global interrupt; the pre-check above
        # ensures Neo only sends it while this selected prompt is the running item.
        _request_json(resolved, "/interrupt", method="POST", payload={"prompt_id": clean_prompt_id}, allow_empty=True, timeout=timeout)
        state = "interrupt_sent"
        action = "interrupt"
    else:
        history = get_comfy_prompt_history(profile_id, prompt_id=clean_prompt_id, timeout=timeout)
        if history.get("found"):
            state = "already_finished"
            action = "none"
    return {
        "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
        "ok": True,
        "profile_id": resolved["profile_id"],
        "provider_id": resolved["provider_id"],
        "prompt_id": clean_prompt_id,
        "state": state,
        "action": action,
        "base_url_exposed": False,
    }


def execution_transport_capabilities() -> dict[str, Any]:
    return {
        "schema_version": COMFY_EXECUTION_TRANSPORT_SCHEMA_VERSION,
        "selected_profile_only": True,
        "profile_fallback": False,
        "prompt_submission": True,
        "history_poll": True,
        "queue_inspection": True,
        "pending_queue_delete": True,
        "running_interrupt": True,
        "websocket_progress": True,
        "websocket_binary_preview_persisted": False,
        "backend_base_url_exposed": False,
    }
