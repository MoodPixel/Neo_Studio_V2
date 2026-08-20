from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Mapping
from urllib import error, parse, request
from uuid import uuid4

from .execution import clamp_generation_params, strip_reasoning_text
from neo_app.services.comfy_gpu_lifecycle import ComfyGpuBusyError, get_comfy_gpu_lifecycle_manager
from neo_app.services.comfy_runtime_recovery import classify_comfy_runtime_error, comfy_error_text, safe_http_body
from neo_app.providers.comfy_llamacpp_compat import (
    GENERIC_MTMD_HANDLER_LABEL,
    pick_auto_vision_model,
    resolve_caption_route,
)


PROVIDER_ID = "comfy_llamacpp"
DEFAULT_RUN_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5
VISION_MODEL_MARKERS = (
    "qwen3-vl", "qwen2.5-vl", "qwen2_5-vl", "qwen25-vl", "vision", "llava", "moondream",
    "nanollava", "minicpm", "glm-4.6v", "glm4.6v", "glm-4.1v", "lfm2-vl", "lfm2.5-vl",
    "step3-vl", "gemma3", "gemma-3", "gemma4", "gemma-4",
)

_BATCH_SESSION_LOCAL = threading.local()


class ComfyLlamaCppBatchSession:
    """Hold one shared-GPU lease while sequential batch captions reuse one VLM."""

    def __init__(self, profile: dict[str, Any], *, batch_id: str, total_items: int) -> None:
        self.profile = profile
        self.batch_id = str(batch_id or "")
        self.total_items = max(0, int(total_items or 0))
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.current_index = 0
        self.current_file = ""
        self.current_prompt_id = ""
        self.model = ""
        self.model_retained = False
        self.fatal_error = ""
        self.deferred_release = False
        self.closed = False
        self.started_at = time.time()
        settings = _mapping(_mapping(profile.get("provider_settings")).get("comfy_llamacpp"))
        self.cleanup_after = bool(settings.get("force_offload", True))
        self.manager = get_comfy_gpu_lifecycle_manager()
        wait_timeout = float(_connection(profile).get("gpu_wait_timeout_seconds") or 900.0)
        self.lease = self.manager.acquire(
            base_url=_base_url(profile),
            owner_kind="prompt_captioning_batch",
            owner_label="ComfyUI LLM / VLM Batch Caption Session",
            profile_id=str(profile.get("profile_id") or ""),
            wait_timeout_seconds=wait_timeout,
            metadata={
                "batch_id": self.batch_id,
                "total_items": self.total_items,
                "processed": 0,
                "model_retained": False,
            },
        )
        self.token = str(self.lease.get("token") or "")
        self.manager.update_lease(
            self.token,
            phase="batch_session_ready",
            cleanup_after=self.cleanup_after,
            metadata={"batch_id": self.batch_id, "total_items": self.total_items},
        )

    def matches(self, profile: Mapping[str, Any]) -> bool:
        return (
            _base_url(dict(profile)) == _base_url(self.profile)
            and str(profile.get("profile_id") or "") == str(self.profile.get("profile_id") or "")
        )

    def item_start(self, index: int, image_name: str = "") -> None:
        self.current_index = max(0, int(index or 0))
        self.current_file = str(image_name or "")
        self.manager.update_lease(
            self.token,
            phase="batch_captioning",
            metadata={
                "batch_id": self.batch_id,
                "total_items": self.total_items,
                "processed": self.processed,
                "current_index": self.current_index,
                "current_file": self.current_file,
                "model_retained": self.model_retained,
            },
        )

    def prompt_started(self, prompt_id: str, model: str) -> None:
        self.current_prompt_id = str(prompt_id or "")
        self.model = str(model or self.model)
        self.model_retained = True
        self.manager.update_lease(
            self.token,
            phase="batch_caption_inference",
            prompt_id=self.current_prompt_id,
            metadata={
                "batch_id": self.batch_id,
                "model": self.model,
                "model_retained": True,
                "current_index": self.current_index,
                "current_file": self.current_file,
            },
        )

    def item_complete(self, *, ok: bool, error: str = "") -> None:
        self.processed += 1
        if ok:
            self.succeeded += 1
        else:
            self.failed += 1
        self.current_prompt_id = ""
        self.manager.update_lease(
            self.token,
            phase="batch_session_retained",
            prompt_id="",
            metadata={
                "batch_id": self.batch_id,
                "processed": self.processed,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "last_error": str(error or ""),
                "model": self.model,
                "model_retained": self.model_retained,
            },
        )

    def mark_fatal(self, error_message: str) -> None:
        self.fatal_error = str(error_message or "Batch caption session failed.")
        self.manager.update_lease(
            self.token,
            phase="batch_session_error",
            metadata={"batch_id": self.batch_id, "fatal_error": self.fatal_error},
        )

    def defer_release_to_prompt(self, prompt_id: str) -> dict[str, Any]:
        clean_prompt_id = str(prompt_id or "").strip()
        if not clean_prompt_id:
            return {"ok": False, "state": "missing_prompt_id"}
        self.deferred_release = True
        return self.manager.bind_prompt(
            self.token,
            prompt_id=clean_prompt_id,
            cleanup_after=self.cleanup_after,
            watch=True,
        )

    def defer_release_to_unbound_queue(self, reason: str) -> dict[str, Any]:
        self.deferred_release = True
        return self.manager.guard_unbound_after_queue_uncertainty(
            self.token,
            cleanup_after=self.cleanup_after,
            reason=reason,
        )

    def snapshot(self) -> dict[str, Any]:
        group = self.lease.get("resource_group")
        return {
            "schema_id": "neo.prompt_captioning.comfy_batch_session.v1",
            "enabled": True,
            "batch_id": self.batch_id,
            "active": not self.closed,
            "resource_group": group,
            "total_items": self.total_items,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "current_index": self.current_index,
            "current_file": self.current_file,
            "current_prompt_id": self.current_prompt_id,
            "model": self.model,
            "model_retained": self.model_retained,
            "cleanup_after": self.cleanup_after,
            "deferred_release": self.deferred_release,
            "fatal_error": self.fatal_error,
            "held_seconds": round(max(0.0, time.time() - self.started_at), 3),
            "gpu_lifecycle": self.manager.status(group=group),
        }

    def finish(self, *, state: str = "completed") -> dict[str, Any]:
        if self.closed:
            return {"ok": True, "state": "session_already_closed", "session": self.snapshot()}
        self.closed = True
        if self.deferred_release:
            return {"ok": True, "state": "release_deferred_to_prompt_watcher", "session": self.snapshot()}
        return self.manager.complete(self.token, state=state, cleanup_after=self.cleanup_after)


def start_comfy_llamacpp_batch_session(profile: dict[str, Any], *, batch_id: str, total_items: int) -> ComfyLlamaCppBatchSession:
    existing = getattr(_BATCH_SESSION_LOCAL, "session", None)
    if existing is not None:
        raise RuntimeError("A Comfy llama.cpp batch session is already active on this worker thread.")
    session = ComfyLlamaCppBatchSession(profile, batch_id=batch_id, total_items=total_items)
    _BATCH_SESSION_LOCAL.session = session
    return session


def finish_comfy_llamacpp_batch_session(session: ComfyLlamaCppBatchSession | None, *, state: str = "completed") -> dict[str, Any]:
    if session is None:
        return {"ok": True, "state": "no_session"}
    try:
        return session.finish(state=state)
    finally:
        if getattr(_BATCH_SESSION_LOCAL, "session", None) is session:
            _BATCH_SESSION_LOCAL.session = None


def _active_batch_session(profile: Mapping[str, Any]) -> ComfyLlamaCppBatchSession | None:
    session = getattr(_BATCH_SESSION_LOCAL, "session", None)
    if isinstance(session, ComfyLlamaCppBatchSession) and session.matches(profile):
        return session
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _connection(profile: dict[str, Any]) -> dict[str, Any]:
    return _mapping(profile.get("connection"))


def _runtime_discovery(profile: dict[str, Any]) -> dict[str, Any]:
    runtime = _mapping(profile.get("runtime"))
    return _mapping(runtime.get("backend_capabilities"))


def _runtime_readiness(profile: dict[str, Any]) -> dict[str, Any]:
    runtime = _mapping(profile.get("runtime"))
    return _mapping(runtime.get("readiness"))


def _readiness_blocker_message(readiness: Mapping[str, Any], route: str) -> str:
    blocker = _mapping(readiness.get(f"{route}_blocker"))
    detail = str(blocker.get("detail") or f"ComfyUI LLM/VLM {route} readiness is incomplete.")
    action = str(blocker.get("next_action") or "Run Connect/Test and review the Comfy LLM/VLM readiness panel.")
    return f"{detail} Next: {action}"


def _base_url(profile: dict[str, Any]) -> str:
    return str(_connection(profile).get("base_url") or "http://127.0.0.1:8188").rstrip("/")


def _http_json(profile: dict[str, Any], path: str, *, method: str = "GET", payload: Any = None, timeout: float | None = None) -> Any:
    url = f"{_base_url(profile)}{path if path.startswith('/') else '/' + path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    effective_timeout = float(timeout or _connection(profile).get("generation_timeout_seconds") or DEFAULT_RUN_TIMEOUT_SECONDS)
    try:
        with request.urlopen(req, timeout=effective_timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = safe_http_body(exc) or str(exc)
        raise RuntimeError(f"ComfyUI {method} {path} returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ComfyUI {method} {path} request failed: {exc}") from exc


def _node_class(discovery: Mapping[str, Any], role: str) -> str:
    node = _mapping(_mapping(discovery.get("nodes")).get(role))
    return str(node.get("class_type") or "").strip()


def _node_input_contract(discovery: Mapping[str, Any], class_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _mapping(_mapping(discovery.get("object_info_node_inputs")).get(class_type))
    return _mapping(record.get("required")), _mapping(record.get("optional"))


def _spec_choices(spec: Any) -> list[Any]:
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    values = spec[0]
    return list(values) if isinstance(values, (list, tuple)) else []


def _spec_metadata(spec: Any) -> dict[str, Any]:
    if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], Mapping):
        return dict(spec[1])
    return {}


def _spec_default(spec: Any) -> Any:
    metadata = _spec_metadata(spec)
    if "default" in metadata:
        return metadata["default"]
    choices = _spec_choices(spec)
    if choices:
        return choices[0]
    kind = spec[0] if isinstance(spec, (list, tuple)) and spec else ""
    if kind == "INT":
        return 0
    if kind == "FLOAT":
        return 0.0
    if kind == "BOOLEAN":
        return False
    if kind == "STRING":
        return ""
    return None


def _coerce_input_to_spec(value: Any, spec: Any) -> Any:
    """Clamp Neo-authored scalar values to the live Comfy node contract.

    Provider-neutral Prompt/Caption controls can have wider ranges than a
    particular llama.cpp node build. /object_info is authoritative for the
    active backend, so Neo clamps numeric values and rejects unavailable enum
    values before the workflow reaches Comfy prompt validation.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
        # Comfy node reference, not a scalar UI value.
        return list(value)
    choices = _spec_choices(spec)
    if choices:
        return value if value in choices else _spec_default(spec)
    metadata = _spec_metadata(spec)
    kind = spec[0] if isinstance(spec, (list, tuple)) and spec else ""
    if kind == "INT":
        number = int(value)
        if metadata.get("min") is not None:
            number = max(int(metadata["min"]), number)
        if metadata.get("max") is not None:
            number = min(int(metadata["max"]), number)
        return number
    if kind == "FLOAT":
        number = float(value)
        if metadata.get("min") is not None:
            number = max(float(metadata["min"]), number)
        if metadata.get("max") is not None:
            number = min(float(metadata["max"]), number)
        return number
    if kind == "BOOLEAN":
        return bool(value)
    if kind == "STRING":
        return str(value)
    return value


def _build_inputs(discovery: Mapping[str, Any], class_type: str, desired: Mapping[str, Any]) -> dict[str, Any]:
    required, optional = _node_input_contract(discovery, class_type)
    result: dict[str, Any] = {}
    for name, spec in required.items():
        if name in desired and desired[name] is not None:
            result[name] = _coerce_input_to_spec(desired[name], spec)
            continue
        default = _spec_default(spec)
        if default is None:
            raise ValueError(f"Comfy node {class_type} requires input '{name}' and Neo could not resolve a safe value.")
        result[name] = default
    for name, spec in optional.items():
        if name in desired and desired[name] is not None:
            result[name] = _coerce_input_to_spec(desired[name], spec)
    return result


def _names(discovery: Mapping[str, Any], key: str) -> list[str]:
    values = _mapping(discovery.get("models")).get(key) or []
    return [str(value).strip() for value in values if str(value).strip()]


def _selected_setting(profile: Mapping[str, Any], params: Mapping[str, Any], key: str) -> str:
    provider_settings = _mapping(profile.get("provider_settings"))
    comfy_settings = _mapping(provider_settings.get("comfy_llamacpp"))
    for source in (params, comfy_settings, _mapping(profile.get("generation_defaults")), _mapping(profile.get("model"))):
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _vision_model_score(name: str) -> int:
    folded = str(name or "").casefold().replace("_", "-")
    score = 0
    for marker in VISION_MODEL_MARKERS:
        if marker in folded:
            score += 10
    if "vl" in folded:
        score += 4
    if "vision" in folded:
        score += 4
    return score


def _resolve_model(profile: Mapping[str, Any], params: Mapping[str, Any], discovery: Mapping[str, Any], *, vision: bool) -> str:
    models = _names(discovery, "all")
    explicit = _selected_setting(profile, params, "comfy_llamacpp_model") or _selected_setting(profile, params, "default_model")
    if explicit:
        if explicit not in models:
            raise ValueError(f"Selected Comfy llama.cpp model is not available: {explicit}")
        return explicit
    if not models:
        raise ValueError("No Comfy llama.cpp model is available.")
    if not vision:
        return models[0]
    auto_model = pick_auto_vision_model(models)
    if auto_model:
        return auto_model
    raise ValueError("Multiple llama.cpp models are available and Neo could not identify the VLM model automatically. Choose an explicit LLM / VLM Model in the ComfyUI LLM / VLM backend settings.")


def _tokenize_name(value: str) -> set[str]:
    folded = str(value or "").casefold().replace("mmproj", " ")
    return {token for token in re.split(r"[^a-z0-9]+", folded) if len(token) >= 3 and token not in {"gguf", "f16", "f32", "q4", "q5", "q6", "q8", "model"}}


def _resolve_mmproj(profile: Mapping[str, Any], params: Mapping[str, Any], discovery: Mapping[str, Any], model: str) -> str:
    projectors = _names(discovery, "mmproj")
    explicit = _selected_setting(profile, params, "comfy_llamacpp_mmproj")
    if explicit:
        if explicit not in projectors:
            raise ValueError(f"Selected Comfy VLM mmproj is not available: {explicit}")
        return explicit
    if not projectors:
        raise ValueError("Caption Studio requires a VLM mmproj projector, but none is available in ComfyUI/models/LLM.")
    if len(projectors) == 1:
        return projectors[0]
    model_tokens = _tokenize_name(model)
    scored: list[tuple[int, int, str]] = []
    for index, name in enumerate(projectors):
        score = len(model_tokens & _tokenize_name(name))
        scored.append((score, index, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][2]
    raise ValueError("Multiple VLM mmproj projectors are available and Neo cannot safely choose between them automatically. Choose an explicit Vision Projector (mmproj) in the ComfyUI LLM / VLM backend settings.")


def _handler_preferences(model: str) -> list[str]:
    folded = str(model or "").casefold().replace("_", "-")
    if "qwen3" in folded and "vl" in folded:
        return ["Qwen3-VL", "Qwen3-VL-Thinking"]
    if ("qwen2.5" in folded or "qwen25" in folded) and "vl" in folded:
        return ["Qwen2.5-VL"]
    if "minicpm" in folded and "4.6" in folded:
        return ["MiniCPM-v4.6", "MiniCPM-v4.6-Thinking"]
    if "minicpm" in folded and "4.5" in folded:
        return ["MiniCPM-v4.5", "MiniCPM-v4.5-Thinking"]
    if "minicpm" in folded:
        return ["MiniCPM-v2.6", "MiniCPM-v4.5", "MiniCPM-v4.6"]
    if "glm-4.6v" in folded or "glm4.6v" in folded:
        return ["GLM-4.6V", "GLM-4.6V-Thinking"]
    if "glm-4.1v" in folded:
        return ["GLM-4.1V-Thinking"]
    if "lfm2.5" in folded:
        return ["LFM2.5-VL"]
    if "lfm2" in folded:
        return ["LFM2-VL"]
    if "step3" in folded:
        return ["Step3-VL"]
    if "gemma4" in folded or "gemma-4" in folded:
        return ["Gemma4"]
    if "gemma3" in folded or "gemma-3" in folded:
        return ["Gemma3"]
    if "moondream" in folded:
        return ["Moondream2"]
    if "nano" in folded and "llava" in folded:
        return ["nanoLLaVA"]
    if "llava" in folded:
        return ["LLaVA-1.6", "LLaVA-1.5", "llama3-Vision-Alpha"]
    return []


def _resolve_chat_handler(profile: Mapping[str, Any], params: Mapping[str, Any], discovery: Mapping[str, Any], model: str, *, vision: bool) -> str:
    handlers = _names(discovery, "chat_handlers")
    explicit = _selected_setting(profile, params, "comfy_llamacpp_chat_handler")
    if explicit:
        if explicit not in handlers:
            raise ValueError(f"Selected Comfy llama.cpp chat handler is not available: {explicit}")
        return explicit
    if not vision:
        if "None" in handlers:
            return "None"
        return handlers[0] if handlers else "None"
    usable = [value for value in handlers if value.casefold() not in {"none", "null", "off"}]
    for preference in _handler_preferences(model):
        if preference in usable:
            return preference
    if len(usable) == 1:
        return usable[0]
    raise ValueError("Neo could not safely infer the VLM chat handler from the selected model. Choose an explicit Chat Handler in the ComfyUI LLM / VLM backend settings.")


def _resolve_caption_execution_route(
    profile: Mapping[str, Any],
    params: Mapping[str, Any],
    discovery: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    handlers = _names(discovery, "chat_handlers")
    explicit = _selected_setting(profile, params, "comfy_llamacpp_chat_handler")
    nodes = _mapping(discovery.get("nodes"))
    generic_available = bool(
        _mapping(nodes.get("neo_generic_mtmd_loader")).get("available")
        and _mapping(nodes.get("neo_generic_mtmd_instruct")).get("available")
    )
    route = resolve_caption_route(
        model=model,
        available_handlers=handlers,
        explicit_handler=explicit,
        generic_mtmd_available=generic_available,
    )
    if not route.get("ready"):
        raise ValueError(
            "Neo could not resolve a compatible VLM execution route for the selected model. "
            "Use a compatible dedicated Chat Handler or update the bundled Neo Prompt/Caption bridge to enable Generic MTMD."
        )
    return route


def _message_parts(messages: list[dict[str, Any]]) -> tuple[str, str, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    image_data_uri = ""
    for message in messages or []:
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content")
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") == "text" and item.get("text") is not None:
                    text_parts.append(str(item.get("text") or ""))
                elif item.get("type") == "image_url":
                    image = _mapping(item.get("image_url"))
                    url = str(image.get("url") or "").strip()
                    if url:
                        image_data_uri = url
        elif content is not None:
            text_parts.append(str(content))
        text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            user_parts.append(f"ASSISTANT CONTEXT:\n{text}")
        else:
            user_parts.append(text)
    return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip(), image_data_uri


def _clean_result_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = _clean_result_text(item)
            if text:
                return text
        return ""
    if isinstance(value, Mapping):
        for key in ("text", "output", "result", "content"):
            text = _clean_result_text(value.get(key))
            if text:
                return text
        return ""
    return str(value or "").strip()


def _history_failure(history_item: Mapping[str, Any], *, model: str = "", mmproj: str = "", prompt_id: str = "") -> dict[str, Any]:
    failure = classify_comfy_runtime_error(
        phase="execution",
        history_item=history_item,
        model=model,
        mmproj=mmproj,
        prompt_id=prompt_id,
    )
    status = _mapping(history_item.get("status"))
    status_str = str(status.get("status_str") or "").strip().lower()
    messages = status.get("messages") if isinstance(status.get("messages"), list) else []
    has_failure_event = any(
        isinstance(item, (list, tuple))
        and item
        and ("error" in str(item[0]).lower() or "fail" in str(item[0]).lower() or str(item[0]).lower() in {"execution_interrupted", "execution_cancelled"})
        for item in messages
    )
    if status_str not in {"error", "failed", "execution_error", "execution_failed"} and not has_failure_event and not history_item.get("node_errors") and not history_item.get("error"):
        return {}
    return failure


def _history_error(history_item: Mapping[str, Any]) -> str:
    failure = _history_failure(history_item)
    return comfy_error_text(failure) if failure else ""


def _runtime_failure_result(
    failure: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    prompt_id: str = "",
    model: str = "",
    runtime: Mapping[str, Any] | None = None,
    batch_session_fatal: bool | None = None,
) -> dict[str, Any]:
    data = dict(failure or {})
    payload = {
        "ok": False,
        "recoverable": bool(data.get("recoverable")),
        "error_type": str(data.get("code") or "comfy_runtime_failed"),
        "error": comfy_error_text(data),
        "next_action": str(data.get("next_action") or ""),
        "provider": PROVIDER_ID,
        "backend_profile_id": str(profile.get("profile_id") or ""),
        "model": str(model or data.get("model") or ""),
        "prompt_id": str(prompt_id or data.get("prompt_id") or ""),
        "runtime_failure": data,
        "runtime": dict(runtime or {}),
    }
    if batch_session_fatal is not None:
        payload["batch_session_fatal"] = bool(batch_session_fatal)
    return payload


def compile_workflow(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any] | None = None, *, force_offload_override: bool | None = None) -> dict[str, Any]:
    params = dict(params or {})
    discovery = _runtime_discovery(profile)
    readiness = _runtime_readiness(profile)

    system_prompt, custom_prompt, image_data_uri = _message_parts(messages)
    vision = bool(image_data_uri)
    if vision:
        if readiness and not readiness.get("caption_ready"):
            raise ValueError(_readiness_blocker_message(readiness, "caption"))
        if not readiness and not discovery.get("caption_execution_ready"):
            raise ValueError("ComfyUI LLM/VLM caption execution is not ready. A VLM model, mmproj, compatible vision route, and Neo Prompt/Caption bridge nodes are required.")
    else:
        if readiness and not readiness.get("prompt_ready"):
            raise ValueError(_readiness_blocker_message(readiness, "prompt"))
        if not readiness and not discovery.get("text_execution_ready"):
            raise ValueError("ComfyUI LLM/VLM text execution is not ready. Connect/Test the profile and install the bundled neo_prompt_captioning Comfy nodes.")
    if not custom_prompt:
        raise ValueError("Prompt & Captioning request contains no user instruction text.")

    output_class = _node_class(discovery, "neo_text_output")
    image_class = _node_class(discovery, "neo_image_input")
    if not output_class:
        raise ValueError("NeoPromptCaptionTextOutput is missing from the connected ComfyUI backend.")

    model = _resolve_model(profile, params, discovery, vision=vision)
    defaults = _mapping(profile.get("generation_defaults"))
    clean_params = clamp_generation_params(params, defaults)
    settings = _mapping(_mapping(profile.get("provider_settings")).get("comfy_llamacpp"))
    force_offload = bool(settings.get("force_offload", True)) if force_offload_override is None else bool(force_offload_override)
    warnings: list[str] = []

    # Caption routes are resolved conservatively. Dedicated handlers are used
    # when the selected model family has a matching handler; otherwise Auto may
    # choose Neo's template-driven Generic MTMD fallback.
    caption_route = _resolve_caption_execution_route(profile, params, discovery, model) if vision else {
        "mode": "text",
        "ready": True,
        "handler": "",
        "label": "Text llama.cpp",
        "family": "text",
        "reason": "text_route",
    }

    if vision and caption_route.get("mode") == "generic_mtmd":
        generic_loader_class = _node_class(discovery, "neo_generic_mtmd_loader")
        generic_instruct_class = _node_class(discovery, "neo_generic_mtmd_instruct")
        if not generic_loader_class or not generic_instruct_class:
            raise ValueError("Neo Generic MTMD nodes are missing from the connected ComfyUI backend. Update the bundled neo_prompt_captioning custom node and restart ComfyUI.")
        if not image_class:
            raise ValueError("NeoPromptCaptionImageInput is missing from the connected ComfyUI backend.")
        mmproj = _resolve_mmproj(profile, params, discovery, model)
        workflow: dict[str, Any] = {
            "1": {
                "class_type": generic_loader_class,
                "inputs": _build_inputs(discovery, generic_loader_class, {
                    "model": model,
                    "mmproj": mmproj,
                    "n_ctx": int(settings.get("n_ctx", 8192) or 8192),
                    "vram_limit": int(settings.get("vram_limit", -1) if settings.get("vram_limit") is not None else -1),
                }),
            },
            "2": {"class_type": image_class, "inputs": {"image_data_uri": image_data_uri}},
        }
        instruct_id = "3"
        generic_desired = {
            "mtmd_model": ["1", 0],
            "image": ["2", 0],
            "custom_prompt": custom_prompt,
            "system_prompt": system_prompt,
            "max_size": int(settings.get("max_size", 512) or 512),
            "max_tokens": int(clean_params.get("max_tokens", 512)),
            "top_k": int(clean_params.get("top_k", 40)),
            "top_p": float(clean_params.get("top_p", 0.9)),
            "temperature": float(clean_params.get("temperature", 0.7)),
            "seed": int(params.get("seed", settings.get("seed", 0)) or 0),
            "force_offload": force_offload,
        }
        workflow[instruct_id] = {
            "class_type": generic_instruct_class,
            "inputs": _build_inputs(discovery, generic_instruct_class, generic_desired),
        }
        output_id = "4"
        workflow[output_id] = {"class_type": output_class, "inputs": {"text": [instruct_id, 0]}}
        return {
            "workflow": workflow,
            "output_node_id": output_id,
            "model": model,
            "mmproj": mmproj,
            "chat_handler": GENERIC_MTMD_HANDLER_LABEL,
            "caption_route": caption_route,
            "vision": True,
            "warnings": warnings,
            "force_offload": force_offload,
        }

    loader_class = _node_class(discovery, "loader")
    instruct_class = _node_class(discovery, "instruct")
    params_class = _node_class(discovery, "parameters")
    if not loader_class or not instruct_class:
        raise ValueError("Required Comfy llama.cpp loader/instruct nodes are missing from discovery.")

    mmproj = _resolve_mmproj(profile, params, discovery, model) if vision else "None"
    if vision:
        chat_handler = str(caption_route.get("handler") or "")
        if not chat_handler:
            raise ValueError("Neo resolved a dedicated VLM route without a chat handler.")
    else:
        chat_handler = _resolve_chat_handler(profile, params, discovery, model, vision=False)

    loader_desired = {
        "model": model,
        "mmproj": mmproj,
        "chat_handler": chat_handler,
        "n_ctx": int(settings.get("n_ctx", 8192) or 8192),
        "vram_limit": int(settings.get("vram_limit", -1) if settings.get("vram_limit") is not None else -1),
        "image_min_tokens": int(settings.get("image_min_tokens", 0) or 0),
        "image_max_tokens": int(settings.get("image_max_tokens", 0) or 0),
        "load_mtp": bool(settings.get("load_mtp", False)),
    }
    workflow: dict[str, Any] = {
        "1": {"class_type": loader_class, "inputs": _build_inputs(discovery, loader_class, loader_desired)},
    }

    parameter_ref = None
    if params_class:
        parameter_desired = {
            "max_tokens": int(clean_params.get("max_tokens", 512)),
            "top_k": int(clean_params.get("top_k", 40)),
            "top_p": float(clean_params.get("top_p", 0.9)),
            "temperature": float(clean_params.get("temperature", 0.7)),
            "state_uid": -1,
        }
        workflow["2"] = {"class_type": params_class, "inputs": _build_inputs(discovery, params_class, parameter_desired)}
        parameter_ref = ["2", 0]
    else:
        warnings.append("llama_cpp_parameters is not installed/exposed; the llama.cpp node's internal generation defaults will be used for this run.")

    next_id = 3
    image_ref = None
    if vision:
        if not image_class:
            raise ValueError("NeoPromptCaptionImageInput is missing from the connected ComfyUI backend.")
        image_id = str(next_id)
        next_id += 1
        workflow[image_id] = {"class_type": image_class, "inputs": {"image_data_uri": image_data_uri}}
        image_ref = [image_id, 0]

    instruct_id = str(next_id)
    next_id += 1
    instruct_desired: dict[str, Any] = {
        "llama_model": ["1", 0],
        "preset_prompt": "Empty - Nothing",
        "custom_prompt": custom_prompt,
        "system_prompt": system_prompt,
        "inference_mode": "one by one",
        "max_frames": int(settings.get("max_frames", 24) or 24),
        "max_size": int(settings.get("max_size", 512) or 512),
        "seed": int(params.get("seed", settings.get("seed", 0)) or 0),
        "force_offload": force_offload,
        "save_states": False,
        "parameters": parameter_ref,
    }
    instruct_required, instruct_optional = _node_input_contract(discovery, instruct_class)
    image_inputs = [name for name in list(instruct_required) + list(instruct_optional) if name == "images" or name.startswith("image_")]
    if vision and image_ref:
        if "images" in image_inputs:
            instruct_desired["images"] = image_ref
        elif image_inputs:
            instruct_desired[image_inputs[0]] = image_ref
        else:
            raise ValueError("The detected llama_cpp_instruct_adv node no longer exposes a compatible image input.")
    workflow[instruct_id] = {"class_type": instruct_class, "inputs": _build_inputs(discovery, instruct_class, instruct_desired)}

    output_id = str(next_id)
    workflow[output_id] = {"class_type": output_class, "inputs": {"text": [instruct_id, 0]}}

    return {
        "workflow": workflow,
        "output_node_id": output_id,
        "model": model,
        "mmproj": mmproj,
        "chat_handler": chat_handler,
        "caption_route": caption_route,
        "vision": vision,
        "warnings": warnings,
        "force_offload": force_offload,
    }

def _run_chat_in_batch_session(
    profile: dict[str, Any],
    messages: list[dict[str, Any]],
    params: dict[str, Any] | None,
    session: ComfyLlamaCppBatchSession,
) -> dict[str, Any]:
    """Run one caption item while preserving the batch VLM and GPU lease."""
    prompt_id = ""
    queued_prompt = False
    try:
        compiled = compile_workflow(profile, messages, params, force_offload_override=False)
        client_id = f"neo-prompt-caption-batch-{uuid4().hex}"
        queued = _http_json(profile, "/prompt", method="POST", payload={"prompt": compiled["workflow"], "client_id": client_id})
        prompt_id = str(_mapping(queued).get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not return a prompt_id: {queued}")
        queued_prompt = True
        session.prompt_started(prompt_id, str(compiled.get("model") or ""))

        timeout = float(_connection(profile).get("generation_timeout_seconds") or DEFAULT_RUN_TIMEOUT_SECONDS)
        interval = max(0.1, float(_connection(profile).get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS))
        deadline = time.monotonic() + max(1.0, timeout)
        history_item: dict[str, Any] = {}
        while time.monotonic() < deadline:
            history = _http_json(profile, f"/history/{parse.quote(prompt_id)}", timeout=min(30.0, timeout))
            history_map = _mapping(history)
            if prompt_id in history_map and isinstance(history_map[prompt_id], Mapping):
                history_item = dict(history_map[prompt_id])
                break
            time.sleep(interval)
        if not history_item:
            failure = classify_comfy_runtime_error(
                f"ComfyUI batch caption item timed out after {timeout:.0f} seconds.",
                phase="poll",
                model=str(compiled.get("model") or ""),
                mmproj=str(compiled.get("mmproj") or ""),
                prompt_id=prompt_id,
            )
            session.mark_fatal(comfy_error_text(failure))
            session.defer_release_to_prompt(prompt_id)
            return _runtime_failure_result(
                failure,
                profile=profile,
                prompt_id=prompt_id,
                model=str(compiled.get("model") or ""),
                runtime={"batch_session": session.snapshot()},
                batch_session_fatal=True,
            )

        execution_failure = _history_failure(
            history_item,
            model=str(compiled.get("model") or ""),
            mmproj=str(compiled.get("mmproj") or ""),
            prompt_id=prompt_id,
        )
        if execution_failure:
            execution_error = comfy_error_text(execution_failure)
            session.item_complete(ok=False, error=execution_error)
            fatal = bool(execution_failure.get("fatal_batch"))
            if fatal:
                session.mark_fatal(execution_error)
            payload = _runtime_failure_result(
                execution_failure,
                profile=profile,
                prompt_id=prompt_id,
                model=str(compiled.get("model") or ""),
                runtime={"batch_session": session.snapshot()},
                batch_session_fatal=fatal,
            )
            payload["raw"] = history_item
            return payload

        outputs = _mapping(history_item.get("outputs"))
        terminal = _mapping(outputs.get(compiled["output_node_id"]))
        raw_text = _clean_result_text(terminal.get("text"))
        text, reasoning_stripped = strip_reasoning_text(raw_text)
        if not text:
            message = "ComfyUI completed the batch caption item, but NeoPromptCaptionTextOutput returned no text."
            session.item_complete(ok=False, error=message)
            return {
                "ok": False,
                "recoverable": False,
                "error_type": "provider_output_missing",
                "error": message,
                "provider": PROVIDER_ID,
                "backend_profile_id": profile.get("profile_id") or "",
                "model": compiled.get("model") or "",
                "prompt_id": prompt_id,
                "raw": history_item,
                "runtime": {"batch_session": session.snapshot()},
            }

        session.item_complete(ok=True)
        warning = " ".join(str(item).strip() for item in (compiled.get("warnings") or []) if str(item).strip()).strip()
        return {
            "ok": True,
            "text": text,
            "partial_text": text,
            "recoverable": False,
            "reasoning_stripped": reasoning_stripped,
            "finish_reason": "comfy_history_complete_batch_session",
            "warning": warning,
            "provider": PROVIDER_ID,
            "backend_profile_id": profile.get("profile_id") or "",
            "model": compiled.get("model") or "",
            "prompt_id": prompt_id,
            "runtime": {
                "backend": "comfyui",
                "output_node_id": compiled["output_node_id"],
                "vision": compiled["vision"],
                "mmproj": compiled["mmproj"],
                "chat_handler": compiled["chat_handler"],
                "force_offload": False,
                "batch_model_retained": True,
                "batch_session": session.snapshot(),
            },
        }
    except Exception as exc:  # noqa: BLE001
        failure = classify_comfy_runtime_error(
            exc,
            phase="poll" if queued_prompt else "queue",
            model=str(compiled.get("model") or "") if isinstance(compiled, Mapping) else "",
            mmproj=str(compiled.get("mmproj") or "") if isinstance(compiled, Mapping) else "",
            prompt_id=prompt_id,
        )
        message = comfy_error_text(failure)
        session.mark_fatal(message)
        if queued_prompt and prompt_id:
            session.defer_release_to_prompt(prompt_id)
        elif bool(failure.get("gpu_state_uncertain")):
            session.defer_release_to_unbound_queue(message)
        return _runtime_failure_result(
            failure,
            profile=profile,
            prompt_id=prompt_id,
            model=str(compiled.get("model") or "") if isinstance(compiled, Mapping) else "",
            runtime={"batch_session": session.snapshot()},
            batch_session_fatal=True,
        )


def run_chat(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run Prompt Studio or Caption Studio through Comfy under the shared GPU lease."""
    active_batch_session = _active_batch_session(profile)
    if active_batch_session is not None:
        return _run_chat_in_batch_session(profile, messages, params, active_batch_session)

    manager = get_comfy_gpu_lifecycle_manager()
    lease_token = ""
    lease_snapshot: dict[str, Any] = {}
    prompt_id = ""
    prompt_bound = False
    compiled: dict[str, Any] = {}
    try:
        compiled = compile_workflow(profile, messages, params)
        wait_timeout = float(_connection(profile).get("gpu_wait_timeout_seconds") or 900.0)
        lease_snapshot = manager.acquire(
            base_url=_base_url(profile),
            owner_kind="prompt_captioning",
            owner_label="ComfyUI LLM / VLM Prompt & Captioning",
            profile_id=str(profile.get("profile_id") or ""),
            wait_timeout_seconds=wait_timeout,
            metadata={"vision": bool(compiled.get("vision")), "model": compiled.get("model") or ""},
        )
        lease_token = str(lease_snapshot.get("token") or "")

        client_id = f"neo-prompt-captioning-{uuid4().hex}"
        queued = _http_json(profile, "/prompt", method="POST", payload={"prompt": compiled["workflow"], "client_id": client_id})
        prompt_id = str(_mapping(queued).get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not return a prompt_id: {queued}")
        manager.bind_prompt(
            lease_token,
            prompt_id=prompt_id,
            cleanup_after=bool(compiled.get("force_offload", True)),
            watch=True,
        )
        prompt_bound = True

        timeout = float(_connection(profile).get("generation_timeout_seconds") or DEFAULT_RUN_TIMEOUT_SECONDS)
        interval = max(0.1, float(_connection(profile).get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS))
        deadline = time.monotonic() + max(1.0, timeout)
        history_item: dict[str, Any] = {}
        while time.monotonic() < deadline:
            history = _http_json(profile, f"/history/{parse.quote(prompt_id)}", timeout=min(30.0, timeout))
            history_map = _mapping(history)
            if prompt_id in history_map and isinstance(history_map[prompt_id], Mapping):
                history_item = dict(history_map[prompt_id])
                break
            time.sleep(interval)
        if not history_item:
            # The watcher keeps ownership after the surface timeout. Releasing
            # early could let Image/Video collide with a llama.cpp job that is
            # still active inside Comfy.
            failure = classify_comfy_runtime_error(
                f"ComfyUI LLM/VLM run timed out after {timeout:.0f} seconds.",
                phase="poll",
                model=str(compiled.get("model") or ""),
                mmproj=str(compiled.get("mmproj") or ""),
                prompt_id=prompt_id,
            )
            return _runtime_failure_result(
                failure,
                profile=profile,
                prompt_id=prompt_id,
                model=str(compiled.get("model") or ""),
                runtime={"gpu_lifecycle": manager.status(group=lease_snapshot.get("resource_group"))},
            )

        execution_failure = _history_failure(
            history_item,
            model=str(compiled.get("model") or ""),
            mmproj=str(compiled.get("mmproj") or ""),
            prompt_id=prompt_id,
        )
        release_state = "failed" if execution_failure else "completed"
        lifecycle_release = manager.complete_prompt(
            base_url=_base_url(profile),
            prompt_id=prompt_id,
            state=release_state,
            cleanup_after=bool(compiled.get("force_offload", True)),
        )
        if execution_failure:
            payload = _runtime_failure_result(
                execution_failure,
                profile=profile,
                prompt_id=prompt_id,
                model=str(compiled.get("model") or ""),
                runtime={"gpu_lifecycle": lifecycle_release},
            )
            payload["raw"] = history_item
            return payload

        outputs = _mapping(history_item.get("outputs"))
        terminal = _mapping(outputs.get(compiled["output_node_id"]))
        raw_text = _clean_result_text(terminal.get("text"))
        text, reasoning_stripped = strip_reasoning_text(raw_text)
        if not text:
            return {
                "ok": False,
                "recoverable": False,
                "error_type": "provider_output_missing",
                "error": "ComfyUI completed the llama.cpp workflow, but NeoPromptCaptionTextOutput returned no text.",
                "provider": PROVIDER_ID,
                "backend_profile_id": profile.get("profile_id") or "",
                "model": compiled["model"],
                "prompt_id": prompt_id,
                "raw": history_item,
                "runtime": {"gpu_lifecycle": lifecycle_release},
            }

        warning_parts = list(compiled.get("warnings") or [])
        cleanup = lifecycle_release.get("cleanup") if isinstance(lifecycle_release, Mapping) else {}
        if isinstance(cleanup, Mapping) and cleanup.get("attempted") and cleanup.get("ok") is False:
            cleanup_failure = cleanup.get("failure") if isinstance(cleanup.get("failure"), Mapping) else classify_comfy_runtime_error(cleanup.get("error") or "cleanup failed", phase="cleanup", prompt_id=prompt_id)
            warning_parts.append(comfy_error_text(cleanup_failure))
        warning = " ".join(str(item).strip() for item in warning_parts if str(item).strip()).strip()
        return {
            "ok": True,
            "text": text,
            "partial_text": text,
            "recoverable": False,
            "reasoning_stripped": reasoning_stripped,
            "finish_reason": "comfy_history_complete",
            "warning": warning,
            "provider": PROVIDER_ID,
            "backend_profile_id": profile.get("profile_id") or "",
            "model": compiled["model"],
            "prompt_id": prompt_id,
            "runtime": {
                "backend": "comfyui",
                "output_node_id": compiled["output_node_id"],
                "vision": compiled["vision"],
                "mmproj": compiled["mmproj"],
                "chat_handler": compiled["chat_handler"],
                "force_offload": compiled["force_offload"],
                "gpu_lifecycle": lifecycle_release,
            },
        }
    except ComfyGpuBusyError as exc:
        return {
            "ok": False,
            "recoverable": True,
            "error_type": "comfy_gpu_busy",
            "error": str(exc),
            "provider": PROVIDER_ID,
            "backend_profile_id": profile.get("profile_id") or "",
            "runtime": {"gpu_lifecycle": exc.status},
        }
    except Exception as exc:  # noqa: BLE001 - provider failures must be returned safely to the surface.
        failure = classify_comfy_runtime_error(
            exc,
            phase="poll" if prompt_bound else "queue",
            model=str(compiled.get("model") or "") if isinstance(compiled, Mapping) else "",
            mmproj=str(compiled.get("mmproj") or "") if isinstance(compiled, Mapping) else "",
            prompt_id=prompt_id,
        )
        if lease_token and not prompt_bound:
            if bool(failure.get("gpu_state_uncertain")):
                manager.guard_unbound_after_queue_uncertainty(
                    lease_token,
                    cleanup_after=bool(compiled.get("force_offload", True)) if isinstance(compiled, Mapping) else False,
                    reason=comfy_error_text(failure),
                )
            else:
                manager.release(lease_token, state="queue_failed")
        # If a prompt was already bound, or the POST /prompt response is
        # uncertain, a recovery watcher keeps the lease until queue/history
        # reconciliation proves the backend is clear.
        return _runtime_failure_result(
            failure,
            profile=profile,
            prompt_id=prompt_id,
            model=str(compiled.get("model") or "") if isinstance(compiled, Mapping) else "",
            runtime={"gpu_lifecycle": manager.status(group=lease_snapshot.get("resource_group")) if lease_snapshot else {}},
        )


__all__ = ["ComfyLlamaCppBatchSession", "compile_workflow", "finish_comfy_llamacpp_batch_session", "run_chat", "start_comfy_llamacpp_batch_session"]
