from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import ssl
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from secrets import compare_digest
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request

from modules import launch_utils, paths_internal, script_callbacks, shared

BRIDGE_VERSION = "1.2.1"
PROTOCOL_VERSION = "1.0"
SCHEMA_ID = "neo.forge_bridge.job.v1"
_ALLOWED_ENDPOINTS = {"/sdapi/v1/txt2img", "/sdapi/v1/img2img", "/sdapi/v1/extra-single-image"}
_ALLOWED_NATIVE_OPERATIONS = {"native_txt2img_upscale"}
_NATIVE_OPERATION_SCHEMA = "neo.forge_bridge.native_operation.v1"
_TERMINAL = {"completed", "failed", "cancelled"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_SENSITIVE_TOKENS = ("password", "passwd", "secret", "token", "credential", "api_key", "apikey", "auth", "cookie")
_PATH_TOKENS = ("path", "folder", "directory", "_dir", "home", "root", "filename", "file_name", "tls_key", "tls_cert")
_LOCK = threading.RLock()
_CONDITION = threading.Condition(_LOCK)
_PREVIEWS: dict[str, str] = {}
_WORKER: threading.Thread | None = None
_STOPPING = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: Any) -> str:
    return _SAFE_ID.sub("_", str(value or "").strip())[:160] or f"neo-{int(time.time())}"


def _data_root() -> Path:
    override = str(os.getenv("NEO_FORGE_BRIDGE_DATA_DIR") or "").strip()
    base = Path(override).expanduser() if override else Path(paths_internal.data_path) / "neo_forge_bridge"
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _job_dir(job_id: str) -> Path:
    return _data_root() / "jobs" / _safe_id(job_id)


def _state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _request_path(job_id: str) -> Path:
    return _job_dir(job_id) / "request.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_state(job_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        current = _read_json(_state_path(job_id))
        merged = {
            **current,
            **updates,
            "schema_id": SCHEMA_ID,
            "job_id": _safe_id(job_id),
            "updated_at": _utc_now(),
        }
        _atomic_json(_state_path(job_id), merged)
        return merged


def _all_states() -> list[dict[str, Any]]:
    root = _data_root() / "jobs"
    if not root.exists():
        return []
    states = [_read_json(path) for path in root.glob("*/state.json")]
    states = [item for item in states if item.get("job_id")]
    return sorted(states, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)


def _authorize(req: Request) -> None:
    expected = str(os.getenv("NEO_FORGE_BRIDGE_TOKEN") or "").strip()
    provided = str(req.headers.get("X-Neo-Bridge-Token") or "").strip()
    if expected:
        if not provided or not compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Invalid Neo Forge Bridge token.")
        return
    host = str(req.client.host if req.client else "")
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise HTTPException(status_code=403, detail="Bridge access is loopback-only until NEO_FORGE_BRIDGE_TOKEN is configured.")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Bridge access could not verify a loopback client.") from exc


def _internal_base_url() -> str:
    override = str(os.getenv("NEO_FORGE_BRIDGE_INTERNAL_BASE_URL") or "").strip().rstrip("/")
    if override:
        parsed = urlparse(override)
        host = str(parsed.hostname or "").strip().casefold()
        loopback = host == "localhost"
        if host and not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not loopback:
            raise RuntimeError("NEO_FORGE_BRIDGE_INTERNAL_BASE_URL must target a loopback HTTP(S) address.")
        return override
    port = int(getattr(shared.cmd_opts, "port", None) or 7860)
    scheme = "https" if getattr(shared.cmd_opts, "tls_certfile", None) else "http"
    return f"{scheme}://127.0.0.1:{port}"


def _api_authorization() -> str:
    auth = str(getattr(shared.cmd_opts, "api_auth", None) or "").strip()
    first = auth.split(",", 1)[0].strip() if auth else ""
    if not first or ":" not in first:
        return ""
    return "Basic " + base64.b64encode(first.encode("utf-8")).decode("ascii")


def _request_json(method: str, path: str, *, payload: Any = None, timeout: float = 3600.0) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    authorization = _api_authorization()
    if authorization:
        headers["Authorization"] = authorization
    req = request.Request(f"{_internal_base_url()}/{path.lstrip('/')}", data=body, headers=headers, method=method.upper())
    context = ssl._create_unverified_context() if _internal_base_url().startswith("https://") else None  # noqa: S323 - loopback self-signed Forge TLS.
    try:
        with request.urlopen(req, timeout=timeout, context=context) as response:  # noqa: S310 - fixed loopback Forge API.
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        raise RuntimeError(f"Forge SDAPI returned HTTP {exc.code}: {detail[:1000]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Forge SDAPI loopback request failed: {exc.reason}") from exc


def _progress_payload(skip_current_image: bool) -> dict[str, Any]:
    value = _request_json(
        "GET",
        f"/sdapi/v1/progress?skip_current_image={'true' if skip_current_image else 'false'}",
        timeout=15.0,
    )
    return value if isinstance(value, dict) else {}


def _decode_image(value: Any) -> bytes:
    text = str(value or "").strip()
    if text.startswith("data:image/") and "," in text:
        text = text.split(",", 1)[1]
    if not text:
        raise ValueError("Forge returned an empty image payload.")
    text += "=" * ((4 - len(text) % 4) % 4)
    data = base64.b64decode(text, validate=False)
    if not data:
        raise ValueError("Forge returned an empty decoded image.")
    return data


def _number(value: Any, default: float, *, integer: bool = False) -> Any:
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return int(default) if integer else float(default)


def _snap_hires_dimension(value: float) -> int:
    return max(8, int(round(float(value) / 8.0)) * 8)


def _native_hires_target(source: Any, payload: dict[str, Any], *, snapper: Any = None) -> dict[str, Any]:
    source_width, source_height = source.size
    snap = snapper if callable(snapper) else _snap_hires_dimension
    size_mode = str(payload.get("native_hires_size_mode") or "scale").strip().casefold()
    if size_mode not in {"scale", "exact"}:
        size_mode = "scale"
    scale = max(1.1, min(4.0, _number(payload.get("hr_scale"), 1.5)))
    requested_width = max(0, _number(payload.get("hr_resize_x"), 0, integer=True))
    requested_height = max(0, _number(payload.get("hr_resize_y"), 0, integer=True))

    if size_mode == "scale":
        target_width = int(snap(source_width * scale))
        target_height = int(snap(source_height * scale))
    else:
        target_width = requested_width
        target_height = requested_height
        if target_width <= 0 and target_height > 0:
            target_width = int(snap(target_height * (source_width / source_height)))
        if target_height <= 0 and target_width > 0:
            target_height = int(snap(target_width * (source_height / source_width)))

    if target_width <= source_width or target_height <= source_height:
        raise ValueError(
            "Native Forge post-Hires target must be larger than the decoded source image "
            f"({source_width}x{source_height} -> {target_width}x{target_height})."
        )
    return {
        "schema_id": "neo.forge_bridge.native_hires_size.v2",
        "size_mode": size_mode,
        "scale": scale,
        "source_width": source_width,
        "source_height": source_height,
        "target_width": target_width,
        "target_height": target_height,
    }


def _encode_pil_image(image: Any) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _script_args_for_payload(runner: Any, alwayson_scripts: dict[str, Any]) -> tuple[Any, ...]:
    scripts = list(getattr(runner, "scripts", None) or [])
    if not scripts:
        runner.initialize_scripts(False)
        scripts = list(getattr(runner, "scripts", None) or [])
    size = max([1, *[int(getattr(script, "args_to", 1) or 1) for script in scripts]])
    args: list[Any] = [None] * size
    args[0] = 0
    for script in scripts:
        start = int(getattr(script, "args_from", 0) or 0)
        stop = int(getattr(script, "args_to", start) or start)
        controls = list(getattr(script, "controls", None) or [])
        for offset, control in enumerate(controls[: max(0, stop - start)]):
            args[start + offset] = getattr(control, "value", None)

    requested = alwayson_scripts if isinstance(alwayson_scripts, dict) else {}
    for requested_name, config in requested.items():
        target = str(requested_name or "").strip().casefold()
        script = next(
            (
                item
                for item in scripts
                if target
                in {
                    str(getattr(item, "name", "") or "").strip().casefold(),
                    str(item.title() if callable(getattr(item, "title", None)) else "").strip().casefold(),
                }
            ),
            None,
        )
        if script is None or not isinstance(config, dict) or not isinstance(config.get("args"), list):
            continue
        start = int(getattr(script, "args_from", 0) or 0)
        stop = int(getattr(script, "args_to", start) or start)
        for offset, value in enumerate(config.get("args")[: max(0, stop - start)]):
            args[start + offset] = value
    return tuple(args)


def _run_native_txt2img_upscale(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_value = payload.get("image")
    if not source_value:
        raise ValueError("Native Forge post-Hires requires an image payload.")

    from contextlib import closing

    from PIL import Image

    from modules import call_queue, processing, progress, scripts
    from modules_forge import main_thread

    source = Image.open(BytesIO(_decode_image(source_value))).convert("RGB")
    size_contract = _native_hires_target(source, payload, snapper=getattr(processing, "sRound", None))
    hr_modules = payload.get("hr_additional_modules")
    if isinstance(hr_modules, str):
        hr_modules = [item.strip() for item in hr_modules.split(",") if item.strip()]
    if not isinstance(hr_modules, list) or not hr_modules:
        hr_modules = ["Use same choices"]

    def execute() -> dict[str, Any]:
        runner = scripts.scripts_txt2img
        script_args = _script_args_for_payload(runner, payload.get("alwayson_scripts") or {})
        cfg = _number(payload.get("cfg_scale"), 7.0)
        distilled_cfg = _number(payload.get("distilled_cfg_scale"), 3.5)
        p = processing.StableDiffusionProcessingTxt2Img(
            outpath_samples=shared.opts.outdir_samples or shared.opts.outdir_txt2img_samples,
            outpath_grids=shared.opts.outdir_grids or shared.opts.outdir_txt2img_grids,
            prompt=str(payload.get("prompt") or ""),
            negative_prompt=str(payload.get("negative_prompt") or ""),
            styles=list(payload.get("styles") or []),
            seed=_number(payload.get("seed"), -1, integer=True),
            subseed=_number(payload.get("subseed"), -1, integer=True),
            subseed_strength=_number(payload.get("subseed_strength"), 0.0),
            sampler_name=str(payload.get("sampler_name") or "Euler"),
            scheduler=str(payload.get("scheduler") or "Automatic"),
            batch_size=1,
            n_iter=1,
            steps=max(1, _number(payload.get("steps"), 20, integer=True)),
            cfg_scale=cfg,
            distilled_cfg_scale=distilled_cfg,
            width=source.width,
            height=source.height,
            restore_faces=bool(payload.get("restore_faces")),
            tiling=bool(payload.get("tiling")),
            do_not_save_samples=True,
            do_not_save_grid=True,
            enable_hr=True,
            denoising_strength=_number(payload.get("denoising_strength"), 0.28),
            hr_scale=size_contract["scale"],
            hr_upscaler=str(payload.get("hr_upscaler") or "Latent"),
            hr_second_pass_steps=max(0, _number(payload.get("hr_second_pass_steps"), 0, integer=True)),
            # Force the resolved target dimensions for selected-output Hires.
            # This prevents stale same-size hr_resize fields or Forge defaults
            # from turning a requested upscale into a 1x refinement pass.
            hr_resize_x=size_contract["target_width"],
            hr_resize_y=size_contract["target_height"],
            hr_checkpoint_name=str(payload.get("hr_checkpoint_name") or "Use same checkpoint"),
            hr_additional_modules=hr_modules,
            hr_sampler_name=(str(payload.get("hr_sampler_name") or "").strip() or None),
            hr_scheduler=(str(payload.get("hr_scheduler") or "").strip() or None),
            hr_prompt=str(payload.get("hr_prompt") or ""),
            hr_negative_prompt=str(payload.get("hr_negative_prompt") or ""),
            hr_cfg=_number(payload.get("hr_cfg"), cfg),
            hr_distilled_cfg=_number(payload.get("hr_distilled_cfg"), distilled_cfg),
            override_settings=dict(payload.get("override_settings") or {}),
        )
        p.firstpass_image = source
        p.txt2img_upscale = True
        p.is_api = True
        p.force_task_id = job_id
        p.override_settings["save_images_before_highres_fix"] = False
        p.scripts = runner
        p.script_args = script_args
        with closing(p):
            processed = runner.run(p, *p.script_args)
            if processed is None:
                processed = processing.process_images(p)
            processing.process_extra_images(processed)
            main_images = list(processed.images or [])
            if not main_images:
                raise ValueError("Native Forge post-Hires completed without a primary image.")
            primary = main_images[0]
            output_width, output_height = primary.size
            expected = (size_contract["target_width"], size_contract["target_height"])
            if (output_width, output_height) != expected:
                raise ValueError(
                    "Native Forge post-Hires returned an unexpected output size "
                    f"({output_width}x{output_height}); expected {expected[0]}x{expected[1]}."
                )
            images = [_encode_pil_image(image) for image in main_images + list(processed.extra_images or [])]
            safe_parameters = {key: value for key, value in payload.items() if key not in {"image", "authorization", "api_key"}}
            safe_parameters["native_hires_result"] = {
                **size_contract,
                "output_width": output_width,
                "output_height": output_height,
                "verified": True,
            }
            return {"images": images, "parameters": safe_parameters, "info": processed.js()}

    progress.add_task_to_queue(job_id)
    with call_queue.queue_lock:
        shared.state.begin(job=job_id)
        progress.start_task(job_id)
        try:
            return main_thread.run_and_wait_result(execute)
        finally:
            progress.finish_task(job_id)
            shared.state.end()
            shared.total_tqdm.clear()


def _run_native_operation(job_id: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "native_txt2img_upscale":
        return _run_native_txt2img_upscale(job_id, payload)
    raise ValueError(f"Unsupported Forge Bridge native operation: {operation}")


def _suffix(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return ".png", "image/png"


def _safe_metadata(response: dict[str, Any]) -> dict[str, Any]:
    parameters = response.get("parameters") if isinstance(response.get("parameters"), dict) else {}
    blocked = {"init_images", "mask", "images", "image", "authorization", "api_key"}
    safe = {str(key): value for key, value in parameters.items() if str(key).casefold() not in blocked}
    info = response.get("info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except Exception:
            info = info[:4000]
    return {"parameters": safe, "info": info}


def _spool_outputs(job_id: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    images = response.get("images") if isinstance(response.get("images"), list) else []
    if not images and isinstance(response.get("image"), str) and response.get("image"):
        images = [response.get("image")]
    output_dir = _job_dir(job_id) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    metadata = _safe_metadata(response)
    for index, image in enumerate(images, start=1):
        data = _decode_image(image)
        suffix, mime = _suffix(data)
        target = output_dir / f"output_{index}{suffix}"
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        outputs.append({"index": index, "filename": target.name, "mime_type": mime, "size_bytes": len(data), "metadata": metadata})
    return outputs


def _result_payload(job_id: str, *, include_images: bool) -> dict[str, Any]:
    state = _read_json(_state_path(job_id))
    metadata = state.get("result_metadata") if isinstance(state.get("result_metadata"), dict) else {}
    result = {"parameters": metadata.get("parameters") or {}, "info": metadata.get("info") or {}, "images": []}
    if include_images:
        for output in state.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            target = _job_dir(job_id) / "outputs" / str(output.get("filename") or "")
            if target.is_file():
                result["images"].append(base64.b64encode(target.read_bytes()).decode("ascii"))
    return result


def _monitor(job_id: str, stop: threading.Event) -> None:
    while not stop.wait(0.5):
        try:
            payload = _progress_payload(skip_current_image=False)
        except Exception as exc:
            state = _read_json(_state_path(job_id))
            progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
            progress["warning"] = str(exc)
            _write_state(job_id, {"progress": progress})
            continue
        current_task = str(payload.get("current_task") or "")
        if current_task and current_task != job_id:
            continue
        progress = {
            "progress": float(payload.get("progress") or 0.0),
            "eta_relative": max(0.0, float(payload.get("eta_relative") or 0.0)),
            "textinfo": str(payload.get("textinfo") or "Forge Bridge running"),
            "current_task": current_task or job_id,
            "state": payload.get("state") if isinstance(payload.get("state"), dict) else {},
        }
        current_image = str(payload.get("current_image") or "").strip()
        if current_image:
            if not current_image.startswith("data:image/"):
                current_image = f"data:image/png;base64,{current_image}"
            with _LOCK:
                _PREVIEWS[job_id] = current_image
        _write_state(job_id, {"progress": progress, "message": progress["textinfo"]})


def _recover_after_forge_restart() -> None:
    for state in _all_states():
        if state.get("status") == "running":
            _write_state(
                str(state.get("job_id") or ""),
                {
                    "status": "failed",
                    "message": "Forge restarted while this Bridge job was running.",
                    "error": "forge_restarted_during_bridge_job",
                    "recoverable": True,
                    "completed_at": _utc_now(),
                },
            )


def _queued_ids() -> list[str]:
    return [str(item.get("job_id") or "") for item in reversed(_all_states()) if item.get("status") == "queued"]


def _worker_loop() -> None:
    global _STOPPING
    while True:
        with _CONDITION:
            if _STOPPING:
                return
            queued = _queued_ids()
            if not queued:
                _CONDITION.wait(timeout=1.0)
                continue
            job_id = queued[0]
        _run_job(job_id)


def _ensure_worker() -> None:
    global _WORKER
    with _CONDITION:
        if _WORKER and _WORKER.is_alive():
            _CONDITION.notify_all()
            return
        _WORKER = threading.Thread(target=_worker_loop, name="neo-forge-bridge", daemon=True)
        _WORKER.start()
        _CONDITION.notify_all()


def _run_job(job_id: str) -> None:
    state = _read_json(_state_path(job_id))
    if state.get("status") != "queued":
        return
    record = _read_json(_request_path(job_id))
    endpoint = str(record.get("endpoint") or "")
    operation = str(record.get("operation") or "")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    payload = dict(payload)
    if endpoint in {"/sdapi/v1/txt2img", "/sdapi/v1/img2img"}:
        payload["force_task_id"] = job_id
    _write_state(
        job_id,
        {
            "status": "running",
            "message": "Forge Bridge submitted the generation request.",
            "started_at": _utc_now(),
            "progress": {"progress": 0.01, "eta_relative": 0.0, "textinfo": "Starting Forge", "current_task": job_id},
        },
    )
    stop = threading.Event()
    monitor = threading.Thread(target=_monitor, args=(job_id, stop), name=f"neo-forge-bridge-progress-{job_id}", daemon=True)
    monitor.start()
    try:
        response = (
            _run_native_operation(job_id, operation, payload)
            if operation
            else _request_json("POST", endpoint, payload=payload, timeout=float(os.getenv("NEO_FORGE_BRIDGE_GENERATION_TIMEOUT") or 86400))
        )
        if not isinstance(response, dict):
            raise RuntimeError("Forge returned an invalid generation response.")
        latest = _read_json(_state_path(job_id))
        if latest.get("cancel_requested"):
            _write_state(job_id, {"status": "cancelled", "message": "Forge Bridge job cancelled.", "completed_at": _utc_now(), "outputs": []})
            return
        outputs = _spool_outputs(job_id, response)
        if not outputs:
            raise RuntimeError("Forge completed without returning image outputs.")
        _write_state(
            job_id,
            {
                "status": "completed",
                "message": "Forge Bridge job completed.",
                "completed_at": _utc_now(),
                "outputs": outputs,
                "result_metadata": _safe_metadata(response),
                "progress": {"progress": 1.0, "eta_relative": 0.0, "textinfo": "Completed", "current_task": job_id},
                "recoverable": True,
            },
        )
    except Exception as exc:
        latest = _read_json(_state_path(job_id))
        status = "cancelled" if latest.get("cancel_requested") else "failed"
        _write_state(
            job_id,
            {
                "status": status,
                "message": "Forge Bridge job cancelled." if status == "cancelled" else str(exc),
                "error": "" if status == "cancelled" else str(exc),
                "completed_at": _utc_now(),
                "recoverable": True,
                "progress": {"progress": 1.0, "eta_relative": 0.0, "textinfo": status.title(), "current_task": job_id},
            },
        )
    finally:
        stop.set()
        monitor.join(timeout=2.0)
        with _LOCK:
            _PREVIEWS.pop(job_id, None)


def _looks_like_local_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith(("/", "~/", "\\")):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))


def _sanitize_setting_value(value: Any) -> Any:
    if isinstance(value, str):
        return "<local-path>" if _looks_like_local_path(value) else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_setting_value(item) for item in value[:100]]
    if isinstance(value, dict):
        return {str(key): _sanitize_setting_value(item) for key, item in list(value.items())[:100]}
    rendered = str(value)
    return "<local-path>" if _looks_like_local_path(rendered) else rendered


def _settings_schema() -> dict[str, Any]:
    settings: list[dict[str, Any]] = []
    for key, metadata in getattr(shared.opts, "data_labels", {}).items():
        key_text = str(key)
        folded = key_text.casefold()
        if any(token in folded for token in (*_SENSITIVE_TOKENS, *_PATH_TOKENS)):
            continue
        default = getattr(metadata, "default", None)
        value_type = type(default).__name__ if default is not None else "any"
        section = getattr(metadata, "section", None)
        if isinstance(section, (list, tuple)):
            section = " / ".join(str(item) for item in section if item)
        settings.append(
            {
                "key": key_text,
                "label": str(getattr(metadata, "label", None) or key_text),
                "type": value_type,
                "default": _sanitize_setting_value(default),
                "section": str(section or "General"),
                "requires_restart": any(token in folded for token in ("precision", "attention", "cuda", "compile", "memory", "offload", "dtype", "quant")),
            }
        )
    return {"schema_id": "neo.forge_bridge.settings_schema.v1", "settings": settings, "count": len(settings)}


def _handshake() -> dict[str, Any]:
    try:
        forge_version = str(launch_utils.git_tag() or "")
    except Exception:
        forge_version = ""
    return {
        "ok": True,
        "identity": "neo_forge_bridge",
        "bridge_version": BRIDGE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "forge_version": forge_version,
        "endpoints": [
            "/neo-api/v1/handshake",
            "/neo-api/v1/capabilities",
            "/neo-api/v1/settings-schema",
            "/neo-api/v1/jobs",
            "/neo-api/v1/jobs/{job_id}",
            "/neo-api/v1/jobs/{job_id}/cancel",
            "/neo-api/v1/history",
        ],
    }


def _capabilities() -> dict[str, Any]:
    return {
        "schema_id": "neo.forge_bridge.capabilities.v1",
        "durable_jobs": True,
        "job_specific_progress": True,
        "live_preview": True,
        "cancel": True,
        "history": True,
        "result_recovery": True,
        "settings_schema": True,
        "max_concurrent_jobs": 1,
        "generation_endpoints": sorted(_ALLOWED_ENDPOINTS),
        "native_operations": sorted(_ALLOWED_NATIVE_OPERATIONS),
        "native_post_hires": True,
        "native_post_hires_size_contract": True,
        "native_hires_size_schema": "neo.forge_bridge.native_hires_size.v2",
        "native_operation_schema": _NATIVE_OPERATION_SCHEMA,
        "security": "token" if str(os.getenv("NEO_FORGE_BRIDGE_TOKEN") or "").strip() else "loopback_only",
    }


def _public_state(job_id: str, *, include_images: bool = False, include_preview: bool = False) -> dict[str, Any]:
    state = _read_json(_state_path(job_id))
    if not state:
        raise HTTPException(status_code=404, detail="Unknown Forge Bridge job.")
    public = {key: value for key, value in state.items() if key not in {"request_path", "result_metadata"}}
    if include_images and state.get("status") == "completed":
        public["result"] = _result_payload(job_id, include_images=True)
    elif state.get("status") == "completed":
        public["result"] = _result_payload(job_id, include_images=False)
    if include_preview:
        with _LOCK:
            preview = _PREVIEWS.get(job_id)
        if preview:
            public["preview"] = {"data_url": preview, "source": "forge_bridge_progress"}
    return public


def _register_routes(_: Any, app: FastAPI) -> None:
    _recover_after_forge_restart()
    _ensure_worker()

    @app.get("/neo-api/v1/handshake")
    def neo_bridge_handshake(req: Request) -> dict[str, Any]:
        _authorize(req)
        return _handshake()

    @app.get("/neo-api/v1/capabilities")
    def neo_bridge_capabilities(req: Request) -> dict[str, Any]:
        _authorize(req)
        return _capabilities()

    @app.get("/neo-api/v1/settings-schema")
    def neo_bridge_settings_schema(req: Request) -> dict[str, Any]:
        _authorize(req)
        return _settings_schema()

    @app.post("/neo-api/v1/jobs")
    def neo_bridge_submit(req: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _authorize(req)
        job_id = _safe_id(payload.get("job_id"))
        endpoint = str(payload.get("endpoint") or "").strip()
        operation = str(payload.get("operation") or "").strip()
        request_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if bool(endpoint) == bool(operation):
            raise HTTPException(status_code=422, detail="Provide exactly one Forge Bridge endpoint or native operation.")
        if endpoint and endpoint not in _ALLOWED_ENDPOINTS:
            raise HTTPException(status_code=422, detail="Unsupported Forge Bridge generation endpoint.")
        if operation and operation not in _ALLOWED_NATIVE_OPERATIONS:
            raise HTTPException(status_code=422, detail="Unsupported Forge Bridge native operation.")
        existing = _read_json(_state_path(job_id))
        if existing:
            return _public_state(job_id)
        _job_dir(job_id).mkdir(parents=True, exist_ok=True)
        _atomic_json(_request_path(job_id), {"endpoint": endpoint, "operation": operation, "payload": request_payload})
        _write_state(
            job_id,
            {
                "status": "queued",
                "message": "Queued by Forge Bridge.",
                "created_at": _utc_now(),
                "endpoint": endpoint,
                "operation": operation,
                "progress": {"progress": 0.0, "eta_relative": 0.0, "textinfo": "Queued", "current_task": job_id},
                "outputs": [],
                "cancel_requested": False,
                "recoverable": True,
            },
        )
        _ensure_worker()
        return _public_state(job_id)

    @app.get("/neo-api/v1/jobs/{job_id}")
    def neo_bridge_job(req: Request, job_id: str, include_images: bool = False, include_preview: bool = False) -> dict[str, Any]:
        _authorize(req)
        return _public_state(_safe_id(job_id), include_images=include_images, include_preview=include_preview)

    @app.post("/neo-api/v1/jobs/{job_id}/cancel")
    def neo_bridge_cancel(req: Request, job_id: str) -> dict[str, Any]:
        _authorize(req)
        job_id = _safe_id(job_id)
        state = _read_json(_state_path(job_id))
        if not state:
            raise HTTPException(status_code=404, detail="Unknown Forge Bridge job.")
        if state.get("status") in _TERMINAL:
            return _public_state(job_id)
        _write_state(job_id, {"cancel_requested": True, "message": "Cancel requested."})
        if state.get("status") == "queued":
            _write_state(job_id, {"status": "cancelled", "message": "Cancelled before Forge submission.", "completed_at": _utc_now()})
            return _public_state(job_id)
        try:
            progress = _progress_payload(skip_current_image=True)
            active = str(progress.get("current_task") or "")
        except Exception as exc:
            return {
                **_public_state(job_id),
                "status": "cancel_refused",
                "message": f"The Bridge could not verify Forge's active task and did not interrupt it: {exc}",
            }
        if active != job_id:
            return {
                **_public_state(job_id),
                "status": "cancel_refused",
                "message": "Forge is not reporting this Bridge job as the active task; the Bridge did not interrupt it.",
            }
        shared.state.interrupt()
        return _public_state(job_id)

    @app.get("/neo-api/v1/history")
    def neo_bridge_history(req: Request, limit: int = 50) -> dict[str, Any]:
        _authorize(req)
        limit = max(1, min(int(limit), 500))
        jobs = [{key: value for key, value in state.items() if key not in {"result_metadata"}} for state in _all_states()[:limit]]
        return {"ok": True, "schema_id": "neo.forge_bridge.history.v1", "jobs": jobs, "count": len(jobs)}


script_callbacks.on_app_started(_register_routes, name="neo_forge_bridge_api")
