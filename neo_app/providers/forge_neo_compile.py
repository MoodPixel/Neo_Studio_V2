from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from neo_app.providers.forge_neo_extensions import apply_forge_extensions
from neo_app.providers.forge_neo_loader_translation import translate_forge_loader_bundle
from neo_app.providers.forge_neo_workflow_compilers import compile_forge_workflow_plan
from neo_app.providers.schema import NeoJob

FORGE_COMPILE_SCHEMA_ID = "neo.provider.forge_compile.v5"
_PROVIDER_DEFAULT_VALUES = {"", "provider_default", "automatic", "auto", "none", "null"}


def _first(params: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in params and params.get(key) is not None:
            return params.get(key)
    return default


def _int_value(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    if minimum is not None:
        resolved = max(minimum, resolved)
    if maximum is not None:
        resolved = min(maximum, resolved)
    return resolved


def _float_value(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = default
    if minimum is not None:
        resolved = max(minimum, resolved)
    if maximum is not None:
        resolved = min(maximum, resolved)
    return resolved


def _resolution_step(params: dict[str, Any]) -> int:
    value = _first(params, "forge_resolution_step", "resolution_step", default=64)
    try:
        step = int(value)
    except (TypeError, ValueError):
        step = 64
    return step if 8 <= step <= 512 else 64


def _snap_dimension(value: Any, default: int, *, step: int) -> tuple[int, int]:
    # Parameter Truth: retain the historical helper signature for callers, but
    # do not silently align explicit dimensions. Backend validation owns errors.
    requested = _int_value(value, default)
    return requested, requested


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _selected(value: Any) -> bool:
    return str(value or "").strip().casefold() not in _PROVIDER_DEFAULT_VALUES


def _image_data_uri(value: Any, *, label: str) -> str:
    candidate = value
    if isinstance(candidate, dict):
        for key in ("data_uri", "preview_data_url", "preview_url", "url", "path", "stored_path", "source_path", "file", "value", "image", "ref"):
            if candidate.get(key) not in {None, ""}:
                candidate = candidate.get(key)
                break
        else:
            candidate = ""
    if isinstance(candidate, (list, tuple)):
        candidate = next((item for item in candidate if item not in {None, ""}), "")
    text = str(candidate or "").strip()
    if not text:
        return ""
    if text.startswith("data:image/"):
        return text
    path = Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{label} file does not exist: {path.name or text}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _derived_finish_contract(params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get("_neo_derived_action")
    if not isinstance(contract, dict):
        contract = params.get("_neo_preview_action")
    if not isinstance(contract, dict):
        return {}
    if str(contract.get("dispatch_type") or "") != "run_provider_img2img_derived":
        return {}
    if str(contract.get("action_id") or "") not in {"extension.adetailer", "extension.identity_rescue"}:
        return {}
    return contract


def _derived_source_dimensions(contract: dict[str, Any]) -> tuple[int, int]:
    source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    return (
        _int_value(source.get("width"), 0, minimum=0, maximum=16384),
        _int_value(source.get("height"), 0, minimum=0, maximum=16384),
    )


def _apply_derived_finish_constraints(
    payload: dict[str, Any],
    contract: dict[str, Any],
    *,
    mode: str,
    resolution_step: int,
) -> dict[str, Any]:
    if not contract:
        return {}
    if mode != "img2img":
        raise ValueError("Forge ADetailer and Identity Rescue Finish actions must compile through img2img.")
    action_id = str(contract.get("action_id") or "")
    source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    if not any(str(source.get(key) or "").strip() for key in ("path", "saved_path", "source_saved_path", "data_uri", "url")):
        raise ValueError("Forge derived Finish action requires a materialized selected-output source image.")

    source_width, source_height = _derived_source_dimensions(contract)
    # Parameter Truth: derived Finish actions may stage a source image, but they
    # must not rewrite explicit generation width/height, denoise, or batch values.
    # The backend owns validation if a requested combination is unsupported.
    payload["denoising_strength"] = _float_value(
        payload.get("denoising_strength"),
        0.25 if action_id == "extension.adetailer" else 0.28,
    )
    payload["include_init_images"] = False
    return {
        "action_id": action_id,
        "dispatch_type": "run_provider_img2img_derived",
        "execution_mode": str(contract.get("execution_mode") or ""),
        "source_output_id": str(contract.get("source_output_id") or ""),
        "source_job_id": str(contract.get("source_job_id") or ""),
        "parent_output_id": str(contract.get("parent_output_id") or ""),
        "parent_job_id": str(contract.get("parent_job_id") or ""),
        "source_dimensions": [source_width, source_height] if source_width and source_height else [],
        "denoising_strength": payload["denoising_strength"],
        "batch_size": payload.get("batch_size"),
        "n_iter": payload.get("n_iter"),
        "cross_provider": False,
    }


def _native_hires_contract(params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get("_neo_derived_action")
    if not isinstance(contract, dict):
        contract = params.get("_neo_preview_action")
    if not isinstance(contract, dict):
        return {}
    if str(contract.get("action_id") or "") != "extension.high_res_lab":
        return {}
    if str(contract.get("dispatch_type") or "") != "run_forge_native_hires":
        return {}
    return contract


def _native_hires_source(contract: dict[str, Any]) -> Any:
    source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    for key in ("path", "saved_path", "source_saved_path", "data_uri", "url"):
        value = source.get(key)
        if value not in {None, ""}:
            return value
    return ""



def _snap_native_hires_dimension(value: float) -> int:
    """Match Forge's eight-pixel Hires target granularity."""

    return max(8, int(round(float(value) / 8.0)) * 8)


def _native_hires_size_contract(
    payload: dict[str, Any],
    contract: dict[str, Any],
    highres_meta: dict[str, Any],
) -> dict[str, Any]:
    source_width, source_height = _derived_source_dimensions(contract)
    requested = contract.get("native_hires") if isinstance(contract.get("native_hires"), dict) else {}
    params = highres_meta.get("params") if isinstance(highres_meta.get("params"), dict) else {}
    scale = _float_value(
        requested.get("scale", payload.get("hr_scale", params.get("scale", 1.5))),
        1.5,
        minimum=1.1,
        maximum=4.0,
    )
    explicit_width = _int_value(payload.get("hr_resize_x"), 0, minimum=0, maximum=16384)
    explicit_height = _int_value(payload.get("hr_resize_y"), 0, minimum=0, maximum=16384)
    size_mode = str(requested.get("size_mode") or ("exact" if explicit_width or explicit_height else "scale")).strip().casefold()
    if size_mode not in {"scale", "exact"}:
        size_mode = "scale"

    expected_width = 0
    expected_height = 0
    if size_mode == "scale":
        # Source-only Preview Hires must not inherit stale explicit dimensions.
        # The Forge Bridge resolves the exact target from the decoded source.
        payload["hr_resize_x"] = 0
        payload["hr_resize_y"] = 0
        if source_width and source_height:
            expected_width = _snap_native_hires_dimension(source_width * scale)
            expected_height = _snap_native_hires_dimension(source_height * scale)
    else:
        expected_width = explicit_width
        expected_height = explicit_height
        if source_width and source_height:
            if expected_width <= 0 and expected_height > 0:
                expected_width = _snap_native_hires_dimension(expected_height * (source_width / source_height))
            if expected_height <= 0 and expected_width > 0:
                expected_height = _snap_native_hires_dimension(expected_width * (source_height / source_width))
        payload["hr_resize_x"] = expected_width
        payload["hr_resize_y"] = expected_height

    if source_width and source_height and expected_width and expected_height:
        if expected_width <= source_width or expected_height <= source_height:
            raise ValueError(
                "Forge native post-Hires target must be larger than the selected source image "
                f"({source_width}x{source_height} -> {expected_width}x{expected_height})."
            )

    payload["hr_scale"] = scale
    payload["native_hires_size_mode"] = size_mode
    payload["native_hires_source_width"] = source_width
    payload["native_hires_source_height"] = source_height
    payload["native_hires_expected_width"] = expected_width
    payload["native_hires_expected_height"] = expected_height
    payload["native_hires_size_schema"] = "neo.forge_bridge.native_hires_size.v2"
    return {
        "schema_id": "neo.image.native_hires_size.v1",
        "size_mode": size_mode,
        "source_width": source_width,
        "source_height": source_height,
        "scale": scale,
        "expected_width": expected_width,
        "expected_height": expected_height,
    }

def _override_settings(params: dict[str, Any], *, loader_translation: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    explicit = params.get("override_settings")
    if isinstance(explicit, dict):
        overrides.update({str(key): value for key, value in explicit.items() if str(key).strip()})

    translated = loader_translation.get("override_settings") if isinstance(loader_translation.get("override_settings"), dict) else {}
    model = translated.get("sd_model_checkpoint")
    if _selected(model):
        overrides["sd_model_checkpoint"] = str(model).strip()

    modules = translated.get("forge_additional_modules")
    if isinstance(modules, str):
        modules = [item.strip() for item in modules.split(",") if item.strip()]
    selected_modules = [str(item).strip() for item in modules or [] if str(item).strip()] if isinstance(modules, list) else []
    if selected_modules:
        overrides["forge_additional_modules"] = selected_modules
    else:
        overrides.pop("forge_additional_modules", None)

    clip_skip = translated.get("CLIP_stop_at_last_layers")
    if clip_skip not in {None, ""}:
        overrides["CLIP_stop_at_last_layers"] = _int_value(clip_skip, 1)
    return overrides


def _base_payload(job: NeoJob, params: dict[str, Any], *, loader_translation: dict[str, Any]) -> dict[str, Any]:
    sampler = _first(params, "sampler_name", "sampler", default="Euler")
    scheduler = _first(params, "scheduler", "scheduler_name", default="Automatic")
    resolution_step = _resolution_step(params)
    _requested_width, resolved_width = _snap_dimension(_first(params, "width", default=1024), 1024, step=resolution_step)
    _requested_height, resolved_height = _snap_dimension(_first(params, "height", default=1024), 1024, step=resolution_step)
    payload: dict[str, Any] = {
        "prompt": str(job.prompt or ""),
        "negative_prompt": str(job.negative_prompt or ""),
        "seed": _int_value(_first(params, "seed", default=-1), -1),
        "subseed": _int_value(_first(params, "subseed", default=-1), -1),
        "subseed_strength": _float_value(_first(params, "subseed_strength", default=0.0), 0.0),
        "steps": _int_value(_first(params, "steps", default=20), 20),
        "cfg_scale": _float_value(_first(params, "cfg_scale", "cfg", default=7.0), 7.0),
        "width": resolved_width,
        "height": resolved_height,
        "sampler_name": str(sampler or "Euler"),
        "scheduler": str(scheduler or "Automatic"),
        "batch_size": _int_value(_first(params, "batch_size", default=1), 1),
        "n_iter": _int_value(_first(params, "n_iter", "batch_count", default=1), 1),
        "restore_faces": _bool_value(_first(params, "restore_faces", default=False)),
        "tiling": _bool_value(_first(params, "tiling", default=False)),
        "send_images": True,
        "save_images": False,
        "force_task_id": str(job.job_id or "") or None,
        "override_settings_restore_afterwards": True,
    }
    overrides = _override_settings(params, loader_translation=loader_translation)
    if overrides:
        payload["override_settings"] = overrides
    styles = _first(params, "styles", default=[])
    if isinstance(styles, list) and styles:
        payload["styles"] = [str(item) for item in styles if str(item).strip()]
    return payload


def _safe_outpaint_metadata(outpaint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(outpaint, dict):
        return None
    return {key: value for key, value in outpaint.items() if key not in {"init_image", "mask"}}


def compile_forge_neo_job(job: NeoJob, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    original_params = dict(job.params or {})
    mode = str(job.mode or "txt2img").strip().casefold()
    mode = {"generate": "txt2img", "image_to_image": "img2img"}.get(mode, mode)

    loader_translation = translate_forge_loader_bundle(job, snapshot=snapshot)
    translation_blockers = list(loader_translation.get("blockers") or [])
    if translation_blockers:
        raise ValueError("Forge loader translation blocked: " + "; ".join(str(item) for item in translation_blockers))

    workflow_plan = compile_forge_workflow_plan(
        job,
        loader_translation=loader_translation,
        snapshot=snapshot,
        image_encoder=_image_data_uri,
    )
    if workflow_plan.get("blockers"):
        raise ValueError("Forge workflow compilation blocked: " + "; ".join(str(item) for item in workflow_plan.get("blockers") or []))

    effective_params = dict(workflow_plan.get("effective_params") or original_params)
    resolution_step = _resolution_step(effective_params)
    requested_width = _int_value(_first(original_params, "width", default=1024), 1024, minimum=1)
    requested_height = _int_value(_first(original_params, "height", default=1024), 1024, minimum=1)
    payload = _base_payload(job, effective_params, loader_translation=loader_translation)
    payload.update(dict(workflow_plan.get("payload_updates") or {}))
    derived_finish = _derived_finish_contract(effective_params)
    derived_finish_meta = _apply_derived_finish_constraints(
        payload,
        derived_finish,
        mode=mode,
        resolution_step=resolution_step,
    )

    image_cfg_scale = _first(effective_params, "image_cfg_scale")
    if image_cfg_scale not in {None, ""} and mode in {"img2img", "inpaint", "outpaint", "edit"}:
        payload["image_cfg_scale"] = _float_value(image_cfg_scale, 1.5)

    extension_result = apply_forge_extensions(
        payload,
        extensions=job.extensions,
        snapshot=snapshot or {},
        mode=mode,
        image_encoder=_image_data_uri,
        family=str(job.family or ""),
    )
    if extension_result.get("errors"):
        raise ValueError(" ".join(str(item) for item in extension_result.get("errors") or []))
    payload = extension_result["payload"]
    if derived_finish:
        extension_meta = extension_result.get("metadata") if isinstance(extension_result.get("metadata"), dict) else {}
        extension_rows = extension_meta.get("extensions") if isinstance(extension_meta.get("extensions"), dict) else {}
        action_id = str(derived_finish.get("action_id") or "")
        if action_id == "extension.adetailer" and not bool((extension_rows.get("adetailer") or {}).get("enabled")):
            raise ValueError("Forge ADetailer Finish requires an enabled, live-verified ADetailer pass.")
        if action_id == "extension.identity_rescue":
            ip_meta = extension_rows.get("ip_adapter") if isinstance(extension_rows.get("ip_adapter"), dict) else {}
            if int(ip_meta.get("faceid_unit_count") or 0) < 1:
                raise ValueError("Forge Identity Rescue requires an enabled FaceID unit with a compatible live model, preprocessor, and reference image.")

    native_hires = _native_hires_contract(effective_params)
    native_hires_size: dict[str, Any] = {}
    operation = ""
    if native_hires:
        if mode != "txt2img":
            raise ValueError("Forge native post-Hires must compile through the txt2img runtime boundary.")
        source_value = _native_hires_source(native_hires)
        if not source_value:
            raise ValueError("Forge native post-Hires requires a materialized source image.")
        extension_meta = extension_result.get("metadata") if isinstance(extension_result.get("metadata"), dict) else {}
        extension_rows = extension_meta.get("extensions") if isinstance(extension_meta.get("extensions"), dict) else {}
        highres_meta = extension_rows.get("high_res_lab") if isinstance(extension_rows.get("high_res_lab"), dict) else {}
        if not bool(highres_meta.get("enabled")):
            raise ValueError("Forge native post-Hires requires the selected High-Res Lab settings payload.")
        native_hires_size = _native_hires_size_contract(payload, native_hires, highres_meta)
        payload["image"] = _image_data_uri(source_value, label="Forge native post-Hires source image")
        payload["native_operation_schema"] = "neo.forge_bridge.native_txt2img_upscale.v2"
        payload["reuse_original_seed"] = bool(payload.get("seed") not in {None, -1})
        payload["batch_size"] = 1
        payload["n_iter"] = 1
        payload["enable_hr"] = True
        operation = "native_txt2img_upscale"

    outpaint_metadata = _safe_outpaint_metadata(workflow_plan.get("outpaint"))
    return {
        "schema_id": FORGE_COMPILE_SCHEMA_ID,
        "provider_id": "forge",
        "backend": "forge_neo",
        "endpoint": "" if operation else str(workflow_plan.get("endpoint") or "/sdapi/v1/txt2img"),
        "operation": operation,
        "method": "POST",
        "payload": payload,
        "actual_params": {
            "mode": mode,
            "family": str(job.family or ""),
            "loader": str(job.loader or ""),
            "model": str(job.model or ""),
            "compiler_id": workflow_plan.get("compiler_id"),
            "workflow_type": workflow_plan.get("workflow_type"),
            "parameter_profile": workflow_plan.get("parameter_profile"),
            "defaults_applied": list(workflow_plan.get("defaults_applied") or []),
            "width": payload.get("width"),
            "height": payload.get("height"),
            "requested_width": requested_width,
            "requested_height": requested_height,
            "resolution_step": resolution_step,
            "resolution_adjusted": requested_width != payload.get("width") or requested_height != payload.get("height"),
            "steps": payload.get("steps"),
            "cfg_scale": payload.get("cfg_scale"),
            "distilled_cfg_scale": payload.get("distilled_cfg_scale"),
            "sampler_name": payload.get("sampler_name"),
            "scheduler": payload.get("scheduler"),
            "batch_size": payload.get("batch_size"),
            "n_iter": payload.get("n_iter"),
            "denoising_strength": payload.get("denoising_strength"),
            "model_override": (payload.get("override_settings") or {}).get("sd_model_checkpoint"),
            "module_overrides": (payload.get("override_settings") or {}).get("forge_additional_modules", []),
            "loader_translation": loader_translation,
            "source_image_count": len(payload.get("init_images") or []),
            "mask_included": bool(payload.get("mask")),
            "outpaint": outpaint_metadata,
            "workflow_warnings": list(workflow_plan.get("warnings") or []),
            "forge_extensions": extension_result.get("metadata") or {},
            "native_operation": operation or None,
            "native_post_hires": bool(operation),
            "native_hires_size": native_hires_size or None,
            "derived_finish": derived_finish_meta or None,
        },
        "lifecycle": {
            "state": "execution_lifecycle_ready",
            "execution_phase": "forge_image_job_lifecycle",
            "provider_queue_submission": True,
            "queue_policy": "single_worker_per_profile",
            "recovery_policy": "queued_resume_and_explicit_orphan_requeue",
        },
    }


def redact_forge_compile_payload(compiled: dict[str, Any]) -> dict[str, Any]:
    """Return a diagnostic copy without large base64 image content."""

    payload = dict(compiled or {})
    request_payload = dict(payload.get("payload") or {})
    if request_payload.get("init_images"):
        request_payload["init_images"] = ["<base64-image>" for _item in request_payload.get("init_images") or []]
    if request_payload.get("mask"):
        request_payload["mask"] = "<base64-image>"
    if request_payload.get("image"):
        request_payload["image"] = "<base64-image>"
    alwayson = request_payload.get("alwayson_scripts")
    if isinstance(alwayson, dict):
        safe_alwayson = {}
        for script_name, script_payload in alwayson.items():
            safe_script = dict(script_payload) if isinstance(script_payload, dict) else script_payload
            if isinstance(safe_script, dict) and isinstance(safe_script.get("args"), list):
                safe_args = []
                for arg in safe_script["args"]:
                    if isinstance(arg, dict):
                        clean_arg = dict(arg)
                        for field in ("image", "mask_image", "generated_image"):
                            if clean_arg.get(field):
                                clean_arg[field] = "<base64-image>"
                        safe_args.append(clean_arg)
                    else:
                        safe_args.append(arg)
                safe_script["args"] = safe_args
            safe_alwayson[str(script_name)] = safe_script
        request_payload["alwayson_scripts"] = safe_alwayson
    payload["payload"] = request_payload
    return payload
