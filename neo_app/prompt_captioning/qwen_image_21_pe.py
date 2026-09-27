from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Mapping
from urllib import error, parse, request
from uuid import uuid4

from neo_app.providers.profiles import get_backend_profile_for_live_task, get_backend_profile_payload
from neo_app.prompt_captioning.metadata import build_result_metadata, normalize_route
from neo_app.prompt_captioning.storage import (
    append_qpe_history_record,
    append_result_metadata,
    get_qpe_history_record,
    list_qpe_history_records,
    update_qpe_history_record,
    update_result_metadata_record,
)
from neo_app.providers.qwen_image_21_pe_discovery import (
    EDIT_NODE,
    NEO_IMAGE_INPUT_NODE,
    NEO_RESULT_OUTPUT_NODE,
    T2I_NODE,
    classify_qwen_image_21_pe_model,
    discover_qwen_image_21_pe,
)
from neo_app.services.comfy_gpu_lifecycle import ComfyGpuBusyError, get_comfy_gpu_lifecycle_manager
from neo_app.services.comfy_runtime_recovery import classify_comfy_runtime_error, comfy_error_text, safe_http_body

ENGINE_ID = "qwen_image_21_pe"
T2I_RUNTIME_SCHEMA = "neo.prompt_captioning.qwen_image_21_pe.t2i_runtime.v1"
EDIT_RUNTIME_SCHEMA = "neo.prompt_captioning.qwen_image_21_pe.edit_runtime.v1"
QPE_RESULT_SCHEMA = "neo.prompt_captioning.qwen_image_21_pe.result.v1"
QPE_REPLAY_SCHEMA = "neo.prompt_captioning.qwen_image_21_pe.replay.v1"
MAX_EDIT_REFERENCES = 10
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5
ROOT_DIR = Path(__file__).resolve().parents[2]
IMAGE_SOURCE_INPUT_DIR = ROOT_DIR / "neo_data" / "inputs" / "image"

QPE_COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable", "comfy_llamacpp"}
QPE_CONNECTED_STATUSES = {"connected", "connected_with_warnings", "available", "online", "ready"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _connection(profile: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(profile.get("connection"))


def _base_url(profile: Mapping[str, Any]) -> str:
    return str(_connection(profile).get("base_url") or "http://127.0.0.1:8188").rstrip("/")


def _runtime_caps(profile: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _mapping(profile.get("runtime"))
    direct = _mapping(profile.get("backend_capabilities"))
    return direct or _mapping(runtime.get("backend_capabilities"))


def _qpe_caps(profile: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(_runtime_caps(profile).get("qwen_image_21_prompt_enhancer"))


def _profile_status(profile: Mapping[str, Any]) -> str:
    runtime = _mapping(profile.get("runtime"))
    return str(runtime.get("status") or profile.get("runtime_status") or profile.get("profile_status") or "disconnected").strip().lower()


def _http_json(profile: Mapping[str, Any], path: str, *, method: str = "GET", payload: Any = None, timeout: float | None = None) -> Any:
    url = f"{_base_url(profile)}{path if path.startswith('/') else '/' + path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    effective = float(timeout or _connection(profile).get("generation_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    try:
        with request.urlopen(req, timeout=max(1.0, effective)) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = safe_http_body(exc) or str(exc)
        raise RuntimeError(f"ComfyUI {method} {path} returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ComfyUI {method} {path} request failed: {exc}") from exc


def _input_sections(node: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _mapping(node)
    inputs = _mapping(payload.get("input"))
    return _mapping(inputs.get("required")), _mapping(inputs.get("optional"))


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
    meta = _spec_metadata(spec)
    if "default" in meta:
        return meta["default"]
    choices = _spec_choices(spec)
    if choices:
        return choices[0]
    kind = spec[0] if isinstance(spec, (list, tuple)) and spec else ""
    return {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False, "STRING": ""}.get(kind)


def _coerce_to_spec(value: Any, spec: Any) -> Any:
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
        return list(value)
    choices = _spec_choices(spec)
    if choices:
        if value in choices:
            return value
        raise ValueError(f"Value {value!r} is not available in the connected Comfy node contract.")
    meta = _spec_metadata(spec)
    kind = spec[0] if isinstance(spec, (list, tuple)) and spec else ""
    if kind == "INT":
        number = int(value)
        if meta.get("min") is not None:
            number = max(int(meta["min"]), number)
        if meta.get("max") is not None:
            number = min(int(meta["max"]), number)
        return number
    if kind == "FLOAT":
        number = float(value)
        if meta.get("min") is not None:
            number = max(float(meta["min"]), number)
        if meta.get("max") is not None:
            number = min(float(meta["max"]), number)
        return number
    if kind == "BOOLEAN":
        return bool(value)
    if kind == "STRING":
        return str(value)
    return value


def _build_inputs(object_info: Mapping[str, Any], class_type: str, desired: Mapping[str, Any]) -> dict[str, Any]:
    required, optional = _input_sections(_mapping(object_info.get(class_type)))
    if not required and not optional:
        raise ValueError(f"Comfy node {class_type} is missing from live /object_info.")
    result: dict[str, Any] = {}
    for name, spec in required.items():
        if name in desired and desired[name] is not None:
            result[name] = _coerce_to_spec(desired[name], spec)
            continue
        default = _spec_default(spec)
        if default is None:
            raise ValueError(f"Comfy node {class_type} requires input '{name}' and Neo could not resolve a safe value.")
        result[name] = default
    for name, value in desired.items():
        if name in result or name not in optional or value is None:
            continue
        result[name] = _coerce_to_spec(value, optional[name])
    return result


def _clip_model_names(object_info: Mapping[str, Any]) -> list[str]:
    required, optional = _input_sections(_mapping(object_info.get("CLIPLoader")))
    inputs = {**optional, **required}
    for key in ("clip_name", "clip_name1", "text_encoder_name"):
        values = _spec_choices(inputs.get(key))
        if values:
            return [str(item) for item in values]
    return []


def _is_qpe_runtime_profile(profile: Mapping[str, Any]) -> bool:
    if not isinstance(profile, Mapping) or profile.get("enabled") is False:
        return False
    provider_id = str(profile.get("provider_id") or "").strip()
    if provider_id not in QPE_COMFY_PROVIDER_IDS:
        return False
    # image_background_removal_backend is an Image utility profile, not a general
    # Comfy execution runtime and should never appear in QPE selectors.
    if str(profile.get("profile_role") or "") == "image_background_removal_backend":
        return False
    return True


def _qpe_profile_sort_key(profile: Mapping[str, Any]) -> tuple[int, int, str]:
    connected = _profile_status(profile) in QPE_CONNECTED_STATUSES
    provider_id = str(profile.get("provider_id") or "").strip()
    surface = str(profile.get("surface") or "").strip()
    if surface == "image" and provider_id in {"comfyui", "comfyui_portable"}:
        surface_rank = 0
    elif surface in {"prompt_captioning", "text"} and provider_id == "comfy_llamacpp":
        surface_rank = 1
    elif surface == "image":
        surface_rank = 2
    elif surface in {"prompt_captioning", "text"}:
        surface_rank = 3
    else:
        surface_rank = 4
    return (0 if connected else 1, surface_rank, str(profile.get("display_name") or profile.get("profile_id") or "").casefold())


def _qpe_profiles() -> list[dict[str, Any]]:
    payload = get_backend_profile_payload()
    profiles = [profile for profile in payload.get("profiles") or [] if _is_qpe_runtime_profile(profile)]
    profiles.sort(key=_qpe_profile_sort_key)
    records: list[dict[str, Any]] = []
    for profile in profiles:
        caps = _qpe_caps(profile)
        records.append({
            "profile_id": str(profile.get("profile_id") or ""),
            "display_name": str(profile.get("display_name") or profile.get("profile_id") or "ComfyUI"),
            "provider_id": str(profile.get("provider_id") or ""),
            "surface": str(profile.get("surface") or ""),
            "status": _profile_status(profile),
            "shared_runtime": str(profile.get("provider_id") or "") == "comfy_llamacpp",
            "t2i_execution_ready": bool(caps.get("t2i_execution_ready")),
            "t2i_dependency_ready": bool(caps.get("t2i_dependency_ready")),
            "t2i_models": list(caps.get("t2i_model_candidates") or []),
            "t2i_blockers": list(_mapping(caps.get("readiness")).get("t2i_blockers") or []),
            "edit_execution_ready": bool(caps.get("edit_execution_ready")),
            "edit_dependency_ready": bool(caps.get("edit_dependency_ready")),
            "edit_models": list(caps.get("edit_model_candidates") or []),
            "edit_blockers": list(_mapping(caps.get("readiness")).get("edit_blockers") or []),
            # Backward-compatible aliases for the QPE-2 T2I UI.
            "models": list(caps.get("t2i_model_candidates") or []),
            "blockers": list(_mapping(caps.get("readiness")).get("t2i_blockers") or []),
        })
    return records

def t2i_status() -> dict[str, Any]:
    profiles = _qpe_profiles()
    return {
        "ok": True,
        "engine_id": ENGINE_ID,
        "task": "t2i",
        "profiles": profiles,
        "ready_profile_ids": [row["profile_id"] for row in profiles if row["t2i_execution_ready"]],
        "license_notice": {
            "wrapper_code": "MIT",
            "model_weights": "Qwen Research License",
        },
    }


def edit_status() -> dict[str, Any]:
    profiles = _qpe_profiles()
    return {
        "ok": True,
        "engine_id": ENGINE_ID,
        "task": "edit",
        "max_references": MAX_EDIT_REFERENCES,
        "profiles": profiles,
        "ready_profile_ids": [row["profile_id"] for row in profiles if row["edit_execution_ready"]],
        "license_notice": {
            "wrapper_code": "MIT",
            "model_weights": "Qwen Research License",
        },
    }


def _resolve_qpe_profile(profile_id: str, *, role: str) -> dict[str, Any]:
    clean_id = str(profile_id or "").strip()
    readiness_key = "edit_execution_ready" if role == "edit" else "t2i_execution_ready"
    blocker_key = "edit_blockers" if role == "edit" else "t2i_blockers"
    role_label = "Edit" if role == "edit" else "T2I"

    if clean_id:
        profile = get_backend_profile_for_live_task(clean_id)
        if profile is None:
            raise ValueError(f"QPE Comfy runtime profile '{clean_id}' was not found.")
        candidates = [profile]
    else:
        payload = get_backend_profile_payload()
        passive = [row for row in payload.get("profiles") or [] if _is_qpe_runtime_profile(row)]
        passive.sort(key=_qpe_profile_sort_key)
        candidates = []
        for row in passive:
            pid = str(row.get("profile_id") or "").strip()
            if not pid:
                continue
            live = get_backend_profile_for_live_task(pid)
            if live:
                candidates.append(live)
            if live and _profile_status(live) in QPE_CONNECTED_STATUSES and _qpe_caps(live).get(readiness_key):
                return live

    for profile in candidates:
        if not _is_qpe_runtime_profile(profile):
            continue
        if _profile_status(profile) not in QPE_CONNECTED_STATUSES:
            continue
        if _qpe_caps(profile).get(readiness_key):
            return profile

    if clean_id and candidates:
        profile = candidates[0]
        if not _is_qpe_runtime_profile(profile):
            raise ValueError("QPE requires a compatible ComfyUI runtime profile. Image ComfyUI/Portable and Prompt & Captioning ComfyUI LLM/VLM profiles are supported.")
        if _profile_status(profile) not in QPE_CONNECTED_STATUSES:
            raise ValueError("The selected QPE Comfy runtime is not connected. Run Connect/Test for that profile in Admin -> Backends.")
        blockers = list(_mapping(_qpe_caps(profile).get("readiness")).get(blocker_key) or [])
        raise ValueError(f"Qwen Image 2.1 PE {role_label} is not ready on the selected Comfy runtime. " + " ".join(str(x) for x in blockers))

    raise ValueError(
        f"No connected QPE {role_label}-ready ComfyUI runtime was found. "
        "Run Connect/Test on a compatible Image or Prompt & Captioning Comfy profile after installing the PE node/model and updated Neo bridge."
    )


def _resolve_profile(profile_id: str) -> dict[str, Any]:
    return _resolve_qpe_profile(profile_id, role="t2i")


def _resolve_edit_profile(profile_id: str) -> dict[str, Any]:
    return _resolve_qpe_profile(profile_id, role="edit")

def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_t2i_params(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = _mapping(params)
    return {
        "temperature": _clamp_float(source.get("temperature"), 1.0, 0.0, 2.0),
        "top_p": _clamp_float(source.get("top_p"), 0.95, 0.0, 1.0),
        "top_k": _clamp_int(source.get("top_k"), 20, 1, 200),
        "presence_penalty": _clamp_float(source.get("presence_penalty"), 1.5, 0.0, 5.0),
        "max_new_tokens": _clamp_int(source.get("max_new_tokens"), 16256, 256, 32768),
        "seed": _clamp_int(source.get("seed"), 42, 0, 0xFFFFFFFF),
    }


def compile_t2i_workflow(*, object_info: Mapping[str, Any], model: str, prompt: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    info = _mapping(object_info)
    live = discover_qwen_image_21_pe(info, text_encoders=_clip_model_names(info), reachable=True)
    if not live.get("core_cliploader_ready"):
        raise ValueError("Live ComfyUI no longer exposes CLIPLoader(type=qwen_image). Reconnect/test after updating ComfyUI.")
    if not live.get("t2i_node_ready"):
        raise ValueError(f"Live ComfyUI no longer exposes a compatible {T2I_NODE} node.")
    if not live.get("neo_result_bridge_ready"):
        raise ValueError(f"{NEO_RESULT_OUTPUT_NODE} is missing. Recopy Neo's bundled neo_prompt_captioning node into ComfyUI/custom_nodes and restart ComfyUI.")
    model_names = _clip_model_names(info)
    if model not in model_names:
        raise ValueError(f"Selected QPE text encoder '{model}' is not available in the live CLIPLoader catalog.")
    if classify_qwen_image_21_pe_model(model) != "t2i":
        raise ValueError("Selected text encoder is not a Qwen Image 2.1 PE T2I checkpoint.")
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Qwen Image 2.1 Prompt Enhancer requires source prompt text.")
    clean = normalize_t2i_params(params)
    workflow = {
        "1": {
            "class_type": "CLIPLoader",
            "inputs": _build_inputs(info, "CLIPLoader", {"clip_name": model, "type": "qwen_image"}),
        },
        "2": {
            "class_type": T2I_NODE,
            "inputs": _build_inputs(info, T2I_NODE, {"clip": ["1", 0], "prompt": clean_prompt, **clean}),
        },
        "3": {
            "class_type": NEO_RESULT_OUTPUT_NODE,
            "inputs": _build_inputs(info, NEO_RESULT_OUTPUT_NODE, {
                "positive_prompt": ["2", 0],
                "negative_prompt": ["2", 1],
                "wh_ratio": ["2", 2],
                "ratio_follow": "",
                "parse_ok": ["2", 4],
            }),
        },
    }
    return {
        "schema_id": T2I_RUNTIME_SCHEMA,
        "workflow": workflow,
        "output_node_id": "3",
        "model": model,
        "prompt": clean_prompt,
        "params": clean,
    }


def normalize_edit_params(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = _mapping(params)
    return {
        "temperature": _clamp_float(source.get("temperature"), 1.0, 0.0, 2.0),
        "top_p": _clamp_float(source.get("top_p"), 0.95, 0.0, 1.0),
        "presence_penalty": _clamp_float(source.get("presence_penalty"), 0.0, 0.0, 5.0),
        "max_length": _clamp_int(source.get("max_length"), 24000, 256, 32768),
        "seed": _clamp_int(source.get("seed"), 42, 0, 0xFFFFFFFF),
    }


def _normalize_reference_data_uri(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("QPE Edit reference image is empty.")
    if raw.startswith("data:image/"):
        if ";base64," not in raw:
            raise ValueError("QPE Edit reference data URI must be base64 encoded.")
        return raw
    if raw.startswith(("http://", "https://")):
        raise ValueError("QPE Edit accepts Neo-managed local image assets, not remote image URLs.")

    # QPE-5 lets the Image workspace reuse its own Neo-managed source/reference
    # uploads without copying them into Prompt & Captioning storage first. The
    # public source-file URL is resolved back to the same bounded input folder.
    if raw.startswith("/api/image/source-file/"):
        raw = str(IMAGE_SOURCE_INPUT_DIR / Path(parse.unquote(raw.rsplit("/", 1)[-1])).name)

    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except Exception as exc:
        raise ValueError(f"QPE Edit reference image was not found: {raw}") from exc
    from neo_app.prompt_captioning.storage import CAPTION_ASSETS_DIR, CAPTION_BATCH_IMAGES_DIR, CAPTION_SINGLE_IMAGES_DIR
    allowed_roots = [
        CAPTION_ASSETS_DIR.resolve(),
        CAPTION_SINGLE_IMAGES_DIR.resolve(),
        CAPTION_BATCH_IMAGES_DIR.resolve(),
        IMAGE_SOURCE_INPUT_DIR.resolve(),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError("QPE Edit reference must be a Neo-managed Prompt & Captioning or Image source asset.")
    from neo_app.prompt_captioning.execution import validate_image_asset
    check = validate_image_asset(str(resolved))
    if not check.get("ok"):
        raise ValueError(check.get("error") or f"QPE Edit reference is not a valid image: {resolved.name}")
    mime = mimetypes.guess_type(resolved.name)[0] or "image/png"
    payload = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def compile_edit_workflow(
    *,
    object_info: Mapping[str, Any],
    model: str,
    prompt: str,
    references: list[Any] | tuple[Any, ...],
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    info = _mapping(object_info)
    live = discover_qwen_image_21_pe(info, text_encoders=_clip_model_names(info), reachable=True)
    if not live.get("core_cliploader_ready"):
        raise ValueError("Live ComfyUI no longer exposes CLIPLoader(type=qwen_image). Reconnect/test after updating ComfyUI.")
    if not live.get("edit_node_ready"):
        raise ValueError(f"Live ComfyUI no longer exposes a compatible {EDIT_NODE} node with image_1 through image_10.")
    if not live.get("neo_image_bridge_ready"):
        raise ValueError(f"{NEO_IMAGE_INPUT_NODE} is missing. Recopy Neo's bundled neo_prompt_captioning node into ComfyUI/custom_nodes and restart ComfyUI.")
    if not live.get("neo_result_bridge_ready"):
        raise ValueError(f"{NEO_RESULT_OUTPUT_NODE} is missing. Recopy Neo's bundled neo_prompt_captioning node into ComfyUI/custom_nodes and restart ComfyUI.")
    model_names = _clip_model_names(info)
    if model not in model_names:
        raise ValueError(f"Selected QPE text encoder '{model}' is not available in the live CLIPLoader catalog.")
    if classify_qwen_image_21_pe_model(model) != "edit":
        raise ValueError("Selected text encoder is not a Qwen Image 2.1 PE I2I/Edit checkpoint.")
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Qwen Image 2.1 PE Edit requires an edit instruction.")
    refs = list(references or [])
    if not refs:
        raise ValueError("Qwen Image 2.1 PE Edit requires at least one reference image.")
    if len(refs) > MAX_EDIT_REFERENCES:
        raise ValueError(f"Qwen Image 2.1 PE Edit supports at most {MAX_EDIT_REFERENCES} ordered reference images.")
    clean = normalize_edit_params(params)
    workflow: dict[str, Any] = {
        "1": {
            "class_type": "CLIPLoader",
            "inputs": _build_inputs(info, "CLIPLoader", {"clip_name": model, "type": "qwen_image"}),
        }
    }
    edit_inputs: dict[str, Any] = {"clip": ["1", 0], "prompt": clean_prompt, **clean}
    reference_nodes: list[dict[str, Any]] = []
    next_id = 2
    for index, reference in enumerate(refs, start=1):
        node_id = str(next_id)
        data_uri = _normalize_reference_data_uri(reference)
        workflow[node_id] = {
            "class_type": NEO_IMAGE_INPUT_NODE,
            "inputs": _build_inputs(info, NEO_IMAGE_INPUT_NODE, {"image_data_uri": data_uri}),
        }
        edit_inputs[f"image_{index}"] = [node_id, 0]
        reference_nodes.append({"slot": index, "node_id": node_id})
        next_id += 1
    edit_node_id = str(next_id)
    workflow[edit_node_id] = {
        "class_type": EDIT_NODE,
        "inputs": _build_inputs(info, EDIT_NODE, edit_inputs),
    }
    output_node_id = str(next_id + 1)
    workflow[output_node_id] = {
        "class_type": NEO_RESULT_OUTPUT_NODE,
        "inputs": _build_inputs(info, NEO_RESULT_OUTPUT_NODE, {
            "positive_prompt": [edit_node_id, 0],
            "negative_prompt": [edit_node_id, 1],
            "wh_ratio": [edit_node_id, 2],
            "ratio_follow": [edit_node_id, 3],
            "parse_ok": [edit_node_id, 5],
        }),
    }
    return {
        "schema_id": EDIT_RUNTIME_SCHEMA,
        "workflow": workflow,
        "output_node_id": output_node_id,
        "edit_node_id": edit_node_id,
        "reference_nodes": reference_nodes,
        "reference_count": len(refs),
        "model": model,
        "prompt": clean_prompt,
        "params": clean,
    }


def _history_failure(history_item: Mapping[str, Any], prompt_id: str) -> dict[str, Any] | None:
    status = _mapping(history_item.get("status"))
    messages = status.get("messages") or []
    for row in messages if isinstance(messages, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        if str(row[0] or "") != "execution_error":
            continue
        detail = row[1] if isinstance(row[1], Mapping) else {"exception_message": str(row[1])}
        return classify_comfy_runtime_error(
            detail.get("exception_message") or detail.get("exception_type") or "ComfyUI QPE execution failed.",
            phase="execute",
            prompt_id=prompt_id,
        )
    return None


def _ui_scalar(value: Any, default: Any = "") -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return default if value is None else value


def _parse_terminal(history_item: Mapping[str, Any], output_node_id: str) -> dict[str, Any]:
    outputs = _mapping(history_item.get("outputs"))
    terminal = _mapping(outputs.get(output_node_id))
    positive = str(_ui_scalar(terminal.get("positive_prompt"), "") or "").strip()
    negative = str(_ui_scalar(terminal.get("negative_prompt"), "") or "").strip()
    wh_ratio = str(_ui_scalar(terminal.get("wh_ratio"), "") or "").strip()
    ratio_follow = str(_ui_scalar(terminal.get("ratio_follow"), "") or "").strip()
    parse_raw = _ui_scalar(terminal.get("parse_ok"), False)
    if isinstance(parse_raw, str):
        parse_ok = parse_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        parse_ok = bool(parse_raw)
    if not positive:
        raise RuntimeError(f"ComfyUI completed QPE, but {NEO_RESULT_OUTPUT_NODE} returned no positive prompt.")
    if not parse_ok:
        # Raw fallback text is still useful, but ratio metadata is not trustworthy.
        wh_ratio = ""
        ratio_follow = ""
    return {
        "positive_prompt": positive,
        "negative_prompt": negative,
        "wh_ratio": wh_ratio,
        "ratio_follow": ratio_follow,
        "parse_ok": parse_ok,
    }



def _reference_asset_record(value: Any, slot: int) -> dict[str, Any]:
    if isinstance(value, Mapping):
        row = dict(value)
        asset_ref = str(row.get("asset_ref") or row.get("assetRef") or row.get("path") or "").strip()
        path = str(row.get("path") or asset_ref or "").strip()
        filename = str(row.get("filename") or row.get("stored_filename") or (Path(path).name if path else "")).strip()
        original_name = str(row.get("original_name") or row.get("name") or filename or "").strip()
        url = str(row.get("url") or row.get("preview_url") or row.get("previewUrl") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            url = ""
        return {
            "slot": int(slot),
            "asset_ref": asset_ref,
            "path": path,
            "filename": filename,
            "original_name": original_name,
            "url": url,
            "replayable": bool(asset_ref or path),
            "inline_only": False,
        }
    raw = str(value or "").strip()
    if raw.startswith("data:image/"):
        return {"slot": int(slot), "asset_ref": "", "path": "", "filename": "", "original_name": f"Inline Image {slot}", "url": "", "replayable": False, "inline_only": True}
    filename = Path(raw).name if raw else ""
    return {"slot": int(slot), "asset_ref": raw, "path": raw, "filename": filename, "original_name": filename or f"Image {slot}", "url": "", "replayable": bool(raw), "inline_only": False}


def _persisted_reference_assets(request_payload: Mapping[str, Any], references: list[Any]) -> list[dict[str, Any]]:
    supplied = request_payload.get("reference_assets")
    source = list(supplied) if isinstance(supplied, (list, tuple)) else list(references or [])
    records: list[dict[str, Any]] = []
    for index, value in enumerate(source[:MAX_EDIT_REFERENCES], start=1):
        fallback = references[index - 1] if index - 1 < len(references) else ""
        record = _reference_asset_record(value if value is not None and value != "" else fallback, index)
        if not record.get("asset_ref") and not record.get("path") and fallback and not str(fallback).startswith("data:image/"):
            fallback_record = _reference_asset_record(fallback, index)
            record.update({key: fallback_record.get(key) for key in ("asset_ref", "path", "filename", "original_name", "replayable")})
        records.append(record)
    return records


def _qpe_profile(task: str) -> dict[str, Any]:
    if task == "edit":
        return {
            "surface": "caption_studio",
            "purpose": "general",
            "visual_treatment": "source_accurate",
            "grounding": "balanced",
            "analysis_scope": "full_image",
            "output_format": "edit_instruction",
            "target_media": "image",
            "prompt_task": "image_edit",
            "edit_intent": "general_edit",
            "preservation_policy": "preserve_unrequested",
            "motion_profile": "natural_balanced",
            "camera_behavior": "preserve_auto",
        }
    return {
        "surface": "prompt_studio",
        "purpose": "general",
        "visual_treatment": "source_accurate",
        "grounding": "transformative",
        "analysis_scope": "full_image",
        "output_format": "natural_prompt",
        "target_media": "image",
        "prompt_task": "text_to_image",
        "edit_intent": "general_edit",
        "preservation_policy": "preserve_unrequested",
        "motion_profile": "natural_balanced",
        "camera_behavior": "preserve_auto",
    }


def _persist_qpe_result(
    *,
    task: str,
    request_payload: Mapping[str, Any],
    result: Mapping[str, Any],
    profile: Mapping[str, Any],
    compiled: Mapping[str, Any],
    prompt_id: str,
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    source_prompt = str(request_payload.get("prompt") or "").strip()
    source_surface = str(request_payload.get("source_surface") or "prompt_captioning").strip().lower()
    if source_surface not in {"prompt_captioning", "image"}:
        source_surface = "prompt_captioning"
    references = list(request_payload.get("references") or []) if isinstance(request_payload.get("references"), (list, tuple)) else []
    reference_assets = _persisted_reference_assets(request_payload, references) if task == "edit" else []
    outputs = {
        "positive_prompt": str(result.get("positive_prompt") or "").strip(),
        "negative_prompt": str(result.get("negative_prompt") or "").strip(),
        "wh_ratio": str(result.get("wh_ratio") or "").strip(),
        "ratio_follow": str(result.get("ratio_follow") or "").strip(),
        "parse_ok": bool(result.get("parse_ok")),
        "prompt": str(result.get("positive_prompt") or "").strip(),
        "output_text": str(result.get("positive_prompt") or "").strip(),
    }
    payload = {
        "workspace": "prompt_captioning",
        "mode": "captioning" if task == "edit" else "prompt_builder",
        "tool": f"qwen_image_21_pe_{task}",
        "tool_id": f"qwen_image_21_pe_{task}",
        "inputs": ({"caption_instruction": source_prompt, "prompt": source_prompt} if task == "edit" else {"source_text": source_prompt, "prompt": source_prompt}),
        "params": dict(compiled.get("params") or request_payload.get("params") or {}),
        "assets": ({"image": str((reference_assets[0] if reference_assets else {}).get("asset_ref") or (reference_assets[0] if reference_assets else {}).get("path") or ""), "images": reference_assets} if task == "edit" else {}),
        "metadata": {
            "qpe_schema": QPE_REPLAY_SCHEMA,
            "qpe_engine_id": ENGINE_ID,
            "qpe_task": task,
            "qpe_source_surface": source_surface,
            "qpe_runtime_profile_id": str(profile.get("profile_id") or ""),
            "qpe_provider_id": str(profile.get("provider_id") or ""),
            "qpe_model": str(compiled.get("model") or ""),
            "qpe_prompt_id": prompt_id,
            "qpe_parse_ok": bool(result.get("parse_ok")),
            "qpe_wh_ratio": outputs["wh_ratio"],
            "qpe_ratio_follow": outputs["ratio_follow"],
            "qpe_ratio_selection": {"kind": "none", "value": ""},
        },
        "profile": _qpe_profile(task),
    }
    route = normalize_route(
        provider_id=str(profile.get("provider_id") or ""),
        backend_profile_id=str(profile.get("profile_id") or ""),
        model=str(compiled.get("model") or ""),
        route_state="available",
    )
    metadata = build_result_metadata(
        tool_id=f"qwen_image_21_pe_{task}",
        mode="captioning" if task == "edit" else "prompt_builder",
        payload=payload,
        outputs=outputs,
        route=route,
        assets=payload["assets"],
        params=payload["params"],
        workflow_summary=(
            f"Rewrote an image edit instruction with Qwen Image 2.1 PE using {len(reference_assets)} ordered reference image(s)."
            if task == "edit"
            else "Enhanced a text-to-image prompt with Qwen Image 2.1 PE."
        ),
        event_type=f"prompt_captioning.qpe.{task}.completed",
    )
    stored_metadata = append_result_metadata(metadata)
    replay_payload = dict(stored_metadata.get("replay_payload") or {})
    record = append_qpe_history_record({
        "schema_id": QPE_RESULT_SCHEMA,
        "task": task,
        "source_surface": source_surface,
        "input_prompt": source_prompt,
        "positive_prompt": outputs["positive_prompt"],
        "negative_prompt": outputs["negative_prompt"],
        "parse_ok": outputs["parse_ok"],
        "wh_ratio": outputs["wh_ratio"],
        "ratio_follow": outputs["ratio_follow"],
        "ratio_selection": {"kind": "none", "value": ""},
        "runtime_profile_id": str(profile.get("profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "model": str(compiled.get("model") or ""),
        "prompt_id": prompt_id,
        "params": dict(compiled.get("params") or {}),
        "references": reference_assets,
        "reference_count": len(reference_assets),
        "metadata_id": str(stored_metadata.get("metadata_id") or ""),
        "replay_payload": replay_payload,
        "execution": dict(execution or {}),
    })
    return {"metadata": stored_metadata, "history": record, "replay_payload": replay_payload}


def qpe_history(limit: int = 50, task: str = "") -> dict[str, Any]:
    return list_qpe_history_records(limit=limit, task=task)


def build_qpe_replay_draft(record: Mapping[str, Any]) -> dict[str, Any]:
    item = _mapping(record)
    task = str(item.get("task") or "").strip().lower()
    if task not in {"t2i", "edit"}:
        raise ValueError("QPE replay record has an unsupported task.")
    references = [dict(row) for row in item.get("references") or [] if isinstance(row, Mapping)]
    missing_reference_slots = [int(row.get("slot") or index + 1) for index, row in enumerate(references) if not bool(row.get("replayable"))]
    ratio_selection = _mapping(item.get("ratio_selection"))
    return {
        "schema_id": QPE_REPLAY_SCHEMA,
        "result_id": str(item.get("result_id") or ""),
        "task": task,
        "source_surface": str(item.get("source_surface") or "prompt_captioning"),
        "workspace_mode": "captioning" if task == "edit" else "prompt_builder",
        "prompt_task": "image_edit" if task == "edit" else "text_to_image",
        "input_prompt": str(item.get("input_prompt") or ""),
        "positive_prompt": str(item.get("positive_prompt") or ""),
        "negative_prompt": str(item.get("negative_prompt") or ""),
        "parse_ok": bool(item.get("parse_ok")),
        "wh_ratio": str(item.get("wh_ratio") or ""),
        "ratio_follow": str(item.get("ratio_follow") or ""),
        "ratio_selection": {"kind": str(ratio_selection.get("kind") or "none"), "value": str(ratio_selection.get("value") or "")},
        "runtime_profile_id": str(item.get("runtime_profile_id") or ""),
        "provider_id": str(item.get("provider_id") or ""),
        "model": str(item.get("model") or ""),
        "prompt_id": str(item.get("prompt_id") or ""),
        "params": _mapping(item.get("params")),
        "references": references,
        "reference_count": int(item.get("reference_count") or len(references)),
        "missing_reference_slots": missing_reference_slots,
        "metadata_id": str(item.get("metadata_id") or ""),
    }


def replay_qpe_result(result_id: str) -> dict[str, Any]:
    found = get_qpe_history_record(result_id)
    if not found.get("ok"):
        return found
    record = _mapping(found.get("record"))
    try:
        draft = build_qpe_replay_draft(record)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [str(exc)], "record": record}
    return {"ok": True, "record": record, "draft": draft, "replay_payload": record.get("replay_payload") if isinstance(record.get("replay_payload"), dict) else {}}


def set_qpe_ratio_selection(result_id: str, kind: str = "none") -> dict[str, Any]:
    found = get_qpe_history_record(result_id)
    if not found.get("ok"):
        return found
    record = _mapping(found.get("record"))
    clean_kind = str(kind or "none").strip().lower()
    if clean_kind not in {"none", "wh_ratio", "ratio_follow"}:
        return {"ok": False, "errors": ["Ratio selection must be none, wh_ratio, or ratio_follow."]}
    if clean_kind != "none" and not bool(record.get("parse_ok")):
        return {"ok": False, "errors": ["Structured parsing did not succeed, so ratio suggestions cannot be accepted."]}
    value = ""
    if clean_kind == "wh_ratio":
        value = str(record.get("wh_ratio") or "").strip()
        if not value:
            return {"ok": False, "errors": ["This QPE result does not contain a wh_ratio suggestion."]}
    elif clean_kind == "ratio_follow":
        value = str(record.get("ratio_follow") or "").strip()
        if not value:
            return {"ok": False, "errors": ["This QPE result does not contain a ratio_follow suggestion."]}
    selection = {"kind": clean_kind, "value": value}
    replay_payload = dict(record.get("replay_payload") or {})
    replay_meta = _mapping(replay_payload.get("metadata"))
    replay_meta["qpe_ratio_selection"] = selection
    replay_payload["metadata"] = replay_meta
    updated = update_qpe_history_record(result_id, {"ratio_selection": selection, "replay_payload": replay_payload})
    if not updated.get("ok"):
        return updated
    metadata_id = str(record.get("metadata_id") or "").strip()
    if metadata_id:
        metadata_found = update_result_metadata_record(metadata_id, {"replay_payload": replay_payload})
        # The dedicated QPE result remains authoritative even if an older metadata
        # record cannot be patched for any reason.
        if metadata_found.get("ok"):
            updated["metadata"] = metadata_found.get("record")
    updated["draft"] = build_qpe_replay_draft(_mapping(updated.get("record")))
    updated["replay_payload"] = replay_payload
    return updated

def run_t2i(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request_payload = _mapping(payload)
    prompt = str(request_payload.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "route_state": "invalid_request", "error": "Qwen Image 2.1 PE T2I requires prompt text."}
    prompt_id = ""
    token = ""
    queued = False
    manager = get_comfy_gpu_lifecycle_manager()
    profile: dict[str, Any] | None = None
    compiled: dict[str, Any] | None = None
    try:
        profile = _resolve_profile(str(request_payload.get("runtime_profile_id") or ""))
        snapshot_caps = _qpe_caps(profile)
        candidates = [str(item) for item in snapshot_caps.get("t2i_model_candidates") or [] if str(item).strip()]
        requested_model = str(request_payload.get("model") or "").strip()
        model = requested_model or (candidates[0] if candidates else "")
        if not model or classify_qwen_image_21_pe_model(model) != "t2i":
            raise ValueError("Select a discovered Qwen Image 2.1 PE T2I text encoder before enhancing.")
        if candidates and model not in candidates:
            raise ValueError("The selected QPE T2I model is not in the connected profile's discovered PE model catalog.")

        object_info = _http_json(profile, "/object_info", timeout=30.0)
        compiled = compile_t2i_workflow(object_info=_mapping(object_info), model=model, prompt=prompt, params=_mapping(request_payload.get("params")))

        wait_timeout = float(_connection(profile).get("gpu_wait_timeout_seconds") or 900.0)
        lease = manager.acquire(
            base_url=_base_url(profile),
            owner_kind="prompt_captioning_qpe_t2i",
            owner_label="Qwen Image 2.1 Prompt Enhancer · T2I",
            profile_id=str(profile.get("profile_id") or ""),
            wait_timeout_seconds=wait_timeout,
            metadata={"engine_id": ENGINE_ID, "task": "t2i", "model": model},
        )
        token = str(lease.get("token") or "")
        manager.update_lease(token, phase="qpe_t2i_queue", cleanup_after=True, metadata={"model": model})

        client_id = f"neo-qpe-t2i-{uuid4().hex}"
        queued_response = _http_json(profile, "/prompt", method="POST", payload={"prompt": compiled["workflow"], "client_id": client_id})
        prompt_id = str(_mapping(queued_response).get("prompt_id") or "").strip()
        if not prompt_id:
            manager.guard_unbound_after_queue_uncertainty(token, cleanup_after=True, reason="QPE POST /prompt returned no prompt_id")
            token = ""
            raise RuntimeError(f"ComfyUI did not return a prompt_id for QPE: {queued_response}")
        queued = True
        manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=False)
        manager.update_lease(token, phase="qpe_t2i_inference", prompt_id=prompt_id, metadata={"model": model})

        timeout = float(_connection(profile).get("generation_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
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
            manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=True)
            token = ""
            raise TimeoutError(f"Qwen Image 2.1 Prompt Enhancer timed out after {timeout:.0f} seconds; Neo will keep watching the Comfy prompt and clean up after it finishes.")

        execution_failure = _history_failure(history_item, prompt_id)
        if execution_failure:
            raise RuntimeError(comfy_error_text(execution_failure))
        result = _parse_terminal(history_item, compiled["output_node_id"])
        cleanup = manager.complete(token, state="qpe_t2i_complete", cleanup_after=True)
        token = ""
        execution = {
            "schema_id": T2I_RUNTIME_SCHEMA,
            "output_node_id": compiled["output_node_id"],
            "cleanup_after": True,
            "cleanup": _mapping(cleanup.get("cleanup")),
            "gpu_lifecycle": cleanup,
        }
        persistence: dict[str, Any] = {}
        persistence_warning = ""
        try:
            persistence = _persist_qpe_result(task="t2i", request_payload=request_payload, result=result, profile=profile, compiled=compiled, prompt_id=prompt_id, execution=execution)
        except Exception as exc:  # noqa: BLE001
            persistence_warning = f"QPE result completed, but Neo could not save its durable history/replay record: {exc}"
        warning = "" if result["parse_ok"] else "The enhancer returned fallback text that did not parse as structured JSON. Review the prompt manually; ratio metadata was discarded."
        if persistence_warning:
            warning = " ".join(part for part in (warning, persistence_warning) if part).strip()
        history_record = _mapping(persistence.get("history"))
        return {
            "ok": True,
            "engine_id": ENGINE_ID,
            "task": "t2i",
            **result,
            "warning": warning,
            "runtime_profile_id": str(profile.get("profile_id") or ""),
            "provider_id": str(profile.get("provider_id") or ""),
            "model": compiled["model"],
            "prompt_id": prompt_id,
            "result_id": str(history_record.get("result_id") or ""),
            "ratio_selection": _mapping(history_record.get("ratio_selection")),
            "metadata": _mapping(persistence.get("metadata")),
            "history": history_record,
            "replay_payload": _mapping(persistence.get("replay_payload")),
            "execution": execution,
        }
    except ComfyGpuBusyError as exc:
        return {
            "ok": False,
            "route_state": "gpu_busy",
            "error": str(exc),
            "gpu_lifecycle": exc.status,
            "runtime_profile_id": str((profile or {}).get("profile_id") or request_payload.get("runtime_profile_id") or ""),
        }
    except Exception as exc:  # noqa: BLE001
        if token:
            if queued and prompt_id:
                # The history item is terminal when we reach most execution errors;
                # on an uncertain exception, a watcher is safer than freeing a running model.
                try:
                    manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=True)
                    token = ""
                except Exception:
                    pass
            if token:
                try:
                    manager.complete(token, state="qpe_t2i_error", cleanup_after=not queued)
                except Exception:
                    pass
        failure = classify_comfy_runtime_error(exc, phase="execute" if queued else "queue", prompt_id=prompt_id)
        return {
            "ok": False,
            "route_state": "provider_error",
            "error": comfy_error_text(failure),
            "error_type": str(failure.get("kind") or failure.get("error_type") or "provider_error"),
            "runtime_profile_id": str((profile or {}).get("profile_id") or request_payload.get("runtime_profile_id") or ""),
            "model": str((compiled or {}).get("model") or request_payload.get("model") or ""),
            "prompt_id": prompt_id,
        }


def run_edit(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request_payload = _mapping(payload)
    prompt = str(request_payload.get("prompt") or "").strip()
    references = list(request_payload.get("references") or []) if isinstance(request_payload.get("references"), (list, tuple)) else []
    if not prompt:
        return {"ok": False, "route_state": "invalid_request", "error": "Qwen Image 2.1 PE Edit requires an edit instruction."}
    if not references:
        return {"ok": False, "route_state": "invalid_request", "error": "Qwen Image 2.1 PE Edit requires at least one reference image."}
    if len(references) > MAX_EDIT_REFERENCES:
        return {"ok": False, "route_state": "invalid_request", "error": f"Qwen Image 2.1 PE Edit supports at most {MAX_EDIT_REFERENCES} ordered reference images."}
    prompt_id = ""
    token = ""
    queued = False
    manager = get_comfy_gpu_lifecycle_manager()
    profile: dict[str, Any] | None = None
    compiled: dict[str, Any] | None = None
    try:
        profile = _resolve_edit_profile(str(request_payload.get("runtime_profile_id") or ""))
        snapshot_caps = _qpe_caps(profile)
        candidates = [str(item) for item in snapshot_caps.get("edit_model_candidates") or [] if str(item).strip()]
        requested_model = str(request_payload.get("model") or "").strip()
        model = requested_model or (candidates[0] if candidates else "")
        if not model or classify_qwen_image_21_pe_model(model) != "edit":
            raise ValueError("Select a discovered Qwen Image 2.1 PE I2I/Edit text encoder before rewriting.")
        if candidates and model not in candidates:
            raise ValueError("The selected QPE Edit model is not in the connected profile's discovered PE model catalog.")

        object_info = _http_json(profile, "/object_info", timeout=30.0)
        compiled = compile_edit_workflow(
            object_info=_mapping(object_info),
            model=model,
            prompt=prompt,
            references=references,
            params=_mapping(request_payload.get("params")),
        )

        wait_timeout = float(_connection(profile).get("gpu_wait_timeout_seconds") or 900.0)
        lease = manager.acquire(
            base_url=_base_url(profile),
            owner_kind="prompt_captioning_qpe_edit",
            owner_label="Qwen Image 2.1 Prompt Enhancer · Edit",
            profile_id=str(profile.get("profile_id") or ""),
            wait_timeout_seconds=wait_timeout,
            metadata={"engine_id": ENGINE_ID, "task": "edit", "model": model, "reference_count": compiled["reference_count"]},
        )
        token = str(lease.get("token") or "")
        manager.update_lease(token, phase="qpe_edit_queue", cleanup_after=True, metadata={"model": model, "reference_count": compiled["reference_count"]})

        client_id = f"neo-qpe-edit-{uuid4().hex}"
        queued_response = _http_json(profile, "/prompt", method="POST", payload={"prompt": compiled["workflow"], "client_id": client_id})
        prompt_id = str(_mapping(queued_response).get("prompt_id") or "").strip()
        if not prompt_id:
            manager.guard_unbound_after_queue_uncertainty(token, cleanup_after=True, reason="QPE Edit POST /prompt returned no prompt_id")
            token = ""
            raise RuntimeError(f"ComfyUI did not return a prompt_id for QPE Edit: {queued_response}")
        queued = True
        manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=False)
        manager.update_lease(token, phase="qpe_edit_inference", prompt_id=prompt_id, metadata={"model": model, "reference_count": compiled["reference_count"]})

        timeout = float(_connection(profile).get("generation_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
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
            manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=True)
            token = ""
            raise TimeoutError(f"Qwen Image 2.1 PE Edit timed out after {timeout:.0f} seconds; Neo will keep watching the Comfy prompt and clean up after it finishes.")

        execution_failure = _history_failure(history_item, prompt_id)
        if execution_failure:
            raise RuntimeError(comfy_error_text(execution_failure))
        result = _parse_terminal(history_item, compiled["output_node_id"])
        cleanup = manager.complete(token, state="qpe_edit_complete", cleanup_after=True)
        token = ""
        execution = {
            "schema_id": EDIT_RUNTIME_SCHEMA,
            "output_node_id": compiled["output_node_id"],
            "reference_nodes": compiled["reference_nodes"],
            "cleanup_after": True,
            "cleanup": _mapping(cleanup.get("cleanup")),
            "gpu_lifecycle": cleanup,
        }
        persistence: dict[str, Any] = {}
        persistence_warning = ""
        try:
            persistence = _persist_qpe_result(task="edit", request_payload=request_payload, result=result, profile=profile, compiled=compiled, prompt_id=prompt_id, execution=execution)
        except Exception as exc:  # noqa: BLE001
            persistence_warning = f"QPE result completed, but Neo could not save its durable history/replay record: {exc}"
        warning = "" if result["parse_ok"] else "The enhancer returned fallback text that did not parse as structured JSON. Review the prompt manually; ratio metadata was discarded."
        if persistence_warning:
            warning = " ".join(part for part in (warning, persistence_warning) if part).strip()
        history_record = _mapping(persistence.get("history"))
        return {
            "ok": True,
            "engine_id": ENGINE_ID,
            "task": "edit",
            **result,
            "warning": warning,
            "runtime_profile_id": str(profile.get("profile_id") or ""),
            "provider_id": str(profile.get("provider_id") or ""),
            "model": compiled["model"],
            "reference_count": compiled["reference_count"],
            "prompt_id": prompt_id,
            "result_id": str(history_record.get("result_id") or ""),
            "ratio_selection": _mapping(history_record.get("ratio_selection")),
            "metadata": _mapping(persistence.get("metadata")),
            "history": history_record,
            "replay_payload": _mapping(persistence.get("replay_payload")),
            "execution": execution,
        }
    except ComfyGpuBusyError as exc:
        return {
            "ok": False,
            "route_state": "gpu_busy",
            "error": str(exc),
            "gpu_lifecycle": exc.status,
            "runtime_profile_id": str((profile or {}).get("profile_id") or request_payload.get("runtime_profile_id") or ""),
        }
    except Exception as exc:  # noqa: BLE001
        if token:
            if queued and prompt_id:
                try:
                    manager.bind_prompt(token, prompt_id=prompt_id, cleanup_after=True, watch=True)
                    token = ""
                except Exception:
                    pass
            if token:
                try:
                    manager.complete(token, state="qpe_edit_error", cleanup_after=not queued)
                except Exception:
                    pass
        failure = classify_comfy_runtime_error(exc, phase="execute" if queued else "queue", prompt_id=prompt_id)
        return {
            "ok": False,
            "route_state": "provider_error",
            "error": comfy_error_text(failure),
            "error_type": str(failure.get("kind") or failure.get("error_type") or "provider_error"),
            "runtime_profile_id": str((profile or {}).get("profile_id") or request_payload.get("runtime_profile_id") or ""),
            "model": str((compiled or {}).get("model") or request_payload.get("model") or ""),
            "prompt_id": prompt_id,
        }


__all__ = [
    "ENGINE_ID",
    "T2I_RUNTIME_SCHEMA",
    "EDIT_RUNTIME_SCHEMA",
    "QPE_RESULT_SCHEMA",
    "QPE_REPLAY_SCHEMA",
    "MAX_EDIT_REFERENCES",
    "compile_t2i_workflow",
    "compile_edit_workflow",
    "normalize_t2i_params",
    "normalize_edit_params",
    "run_t2i",
    "run_edit",
    "t2i_status",
    "edit_status",
    "qpe_history",
    "build_qpe_replay_draft",
    "replay_qpe_result",
    "set_qpe_ratio_selection",
]
