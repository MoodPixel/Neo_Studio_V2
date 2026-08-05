from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable

from neo_extensions.built_in.adetailer.backend.payload_schema import normalize_block as normalize_adetailer_block
from neo_extensions.built_in.controlnet.backend.payload_schema import normalize_block as normalize_controlnet_block
from neo_extensions.built_in.embeddings_ti.backend.payload_schema import normalize_items
from neo_extensions.built_in.embeddings_ti.backend.provider_serialization import (
    clean_embedding_catalog_name,
    embedding_asset_name,
    prompt_contains_embedding,
    render_provider_embedding_token,
)
from neo_extensions.built_in.high_res_lab.backend.payload_schema import normalize_params as normalize_highres_params
from neo_extensions.built_in.forge_couple.backend.advanced import mapping_covers_canvas as forge_couple_mapping_covers_canvas, mapping_errors as forge_couple_mapping_errors, normalize_mapping as normalize_forge_couple_mapping
from neo_extensions.built_in.forge_couple.backend.mask import mask_mapping_errors as forge_couple_mask_mapping_errors, mask_union_coverage as forge_couple_mask_union_coverage, normalize_mask_mapping as normalize_forge_couple_mask_mapping, redact_mask_mapping as redact_forge_couple_mask_mapping
from neo_extensions.built_in.forge_couple.backend.payload_schema import compile_args as compile_forge_couple_args, normalize_block as normalize_forge_couple_block, required_prompt_lines as forge_couple_required_prompt_lines, split_prompt as split_forge_couple_prompt
from neo_extensions.built_in.forge_couple.backend.tile import tile_errors as forge_couple_tile_errors
from neo_extensions.built_in.lora_stack.backend.payload_schema import normalize_lora_rows
from neo_extensions.built_in.lora_stack.backend.provider_serialization import (
    clean_lora_catalog_name,
    forge_lora_name,
    lora_identity_keys,
    prompt_lora_identity_keys,
    render_forge_lora_tag,
)
from neo_app.providers.forge_neo_extension_bridge import compile_forge_generic_extension_bridge, validate_forge_generic_extension_bridge
from neo_app.providers.forge_neo_ip_adapter import compile_forge_ip_adapter_units

FORGE_EXTENSION_SCHEMA_ID = "neo.provider.forge_extensions.v1"

_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "wildcards": ("wildcards", "image.wildcards"),
    "style_stack": ("style_stack", "image.style_stack"),
    "lora_stack": ("lora_stack", "image.lora_stack"),
    "embeddings_ti": ("embeddings_ti", "image.embeddings_ti"),
    "high_res_lab": ("high_res_lab", "image.high_res_lab"),
    "controlnet": ("controlnet", "image.controlnet"),
    "ip_adapter": ("ip_adapter", "image.ip_adapter"),
    "adetailer": ("adetailer", "image.adetailer"),
    "image_upscale": ("image_upscale", "image.image_upscale"),
    "pid_integrated": ("pid_integrated", "image.pid_integrated"),
    "spectrum": ("spectrum", "image.spectrum"),
    "multidiffusion": ("multidiffusion", "image.multidiffusion"),
    "forge_couple": ("forge_couple", "image.forge_couple"),
    "forge_script_bridge": ("forge_script_bridge", "image.forge_script_bridge"),
}
_ALLOWED_ALWAYS = {"wildcards", "style_stack"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _payload_container(extensions: Any) -> dict[str, Any]:
    source = _as_dict(extensions)
    for key in ("payloads", "extensions"):
        nested = source.get(key)
        if isinstance(nested, dict):
            return nested
    return source


def extension_block(extensions: Any, canonical_id: str) -> dict[str, Any]:
    container = _payload_container(extensions)
    for alias in _ALIAS_GROUPS.get(canonical_id, (canonical_id,)):
        block = container.get(alias)
        if isinstance(block, dict):
            return deepcopy(block)
    return {}


def active_extension_ids(extensions: Any) -> set[str]:
    active: set[str] = set()
    for canonical_id in _ALIAS_GROUPS:
        block = extension_block(extensions, canonical_id)
        if block and block.get("enabled") is not False:
            active.add(canonical_id)
    container = _payload_container(extensions)
    known_aliases = {alias for aliases in _ALIAS_GROUPS.values() for alias in aliases}
    for key, value in container.items():
        if key in known_aliases or not isinstance(value, dict):
            continue
        if value.get("enabled") is not False and any(field in value for field in ("enabled", "params", "inputs", "assets")):
            active.add(str(key))
    return active


def _append_prompt(prompt: str, tokens: Iterable[str]) -> str:
    additions = [str(token).strip() for token in tokens if str(token or "").strip()]
    if not additions:
        return prompt
    base = str(prompt or "").strip()
    return ", ".join([part for part in (base, *additions) if part])


def _clean_lora_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    for suffix in (".safetensors", ".ckpt", ".pt", ".bin"):
        if text.casefold().endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip()


def _format_strength(value: Any, default: float = 1.0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return f"{number:.4f}".rstrip("0").rstrip(".") or "0"


def _compile_lora(block: dict[str, Any], prompt: str) -> tuple[str, dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return prompt, {"enabled": False}, []
    params = _as_dict(block.get("params"))
    rows = normalize_lora_rows(params.get("loras") or block.get("loras") or [])
    errors: list[str] = []
    existing = prompt_lora_identity_keys(prompt)
    tags: list[str] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        catalog_name = clean_lora_catalog_name(row.get("name"))
        name = forge_lora_name(catalog_name)
        if not name:
            continue
        if row.get("apply_to") != "global":
            errors.append(f"Forge LoRA Stack does not support regional target {row.get('apply_to')} for {name}.")
            continue
        if row.get("target") == "finish":
            errors.append(f"Forge LoRA Stack cannot apply finish-only LoRA {name} during base generation.")
            continue
        identity = lora_identity_keys(catalog_name)
        rendered = render_forge_lora_tag(catalog_name, row.get("strength"))
        duplicate = bool(existing.intersection(identity))
        if rendered and not duplicate:
            tags.append(rendered)
            existing.update(identity)
        applied.append({
            "name": name,
            "catalog_name": catalog_name,
            "strength": row.get("strength"),
            "target": row.get("target"),
            "apply_to": "global",
            "rendered_tag": rendered,
            "deduplicated_against_prompt": duplicate,
        })
    return _append_prompt(prompt, tags), {
        "enabled": bool(applied),
        "provider_id": "forge",
        "mode": "positive_prompt_extra_network",
        "prompt_mutation": "compile_time_only",
        "items": applied,
        "prompt_tags": tags,
    }, errors


def _compile_embeddings(block: dict[str, Any], prompt: str, negative_prompt: str) -> tuple[str, str, dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return prompt, negative_prompt, {"enabled": False}, []
    params = _as_dict(block.get("params"))
    items = normalize_items(params.get("items") or block.get("items") or [])
    positive: list[str] = []
    negative: list[str] = []
    applied: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        catalog_name = clean_embedding_catalog_name(item.get("catalog_name") or item.get("asset_name") or item.get("token") or item.get("name"))
        asset_name = embedding_asset_name(item.get("asset_name") or item.get("token") or catalog_name or item.get("name"))
        if not asset_name:
            continue
        target = str(item.get("target") or "negative_prompt").casefold()
        if target in {"finish_positive", "finish_negative"}:
            errors.append(f"Forge Embeddings/TI does not map finish-pass target {target} for {asset_name}.")
            continue
        targets = ["positive_prompt", "negative_prompt"] if target == "both" else [target]
        targets = [
            "positive_prompt" if value in {"positive", "positive_prompt", "base_positive"} else
            "negative_prompt" if value in {"negative", "negative_prompt", "base_negative"} else value
            for value in targets
        ]
        if any(value not in {"positive_prompt", "negative_prompt"} for value in targets):
            errors.append(f"Forge Embeddings/TI target {target} is unsupported for {asset_name}.")
            continue
        rendered = render_provider_embedding_token("forge", asset_name, item.get("strength", 1.0))
        positive_duplicate = "positive_prompt" in targets and prompt_contains_embedding(prompt, asset_name)
        negative_duplicate = "negative_prompt" in targets and prompt_contains_embedding(negative_prompt, asset_name)
        if "positive_prompt" in targets and not positive_duplicate:
            positive.append(rendered)
            prompt = _append_prompt(prompt, [rendered])
        if "negative_prompt" in targets and not negative_duplicate:
            negative.append(rendered)
            negative_prompt = _append_prompt(negative_prompt, [rendered])
        applied.append({
            "asset_name": asset_name,
            "catalog_name": catalog_name or asset_name,
            "token": asset_name,
            "strength": item.get("strength"),
            "target": target,
            "rendered_token": rendered,
            "deduplicated_positive": positive_duplicate,
            "deduplicated_negative": negative_duplicate,
        })
    return prompt, negative_prompt, {
        "enabled": bool(applied),
        "provider_id": "forge",
        "contract": "forge.embedding.token.v2",
        "serialization": "plain_trigger_compile_time",
        "prompt_mutation": "compile_time_only",
        "visible_prompt_mutation": False,
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
        "items": applied,
        "positive_tokens": positive,
        "negative_tokens": negative,
    }, errors

def _compile_highres(block: dict[str, Any], *, mode: str, available_upscalers: set[str]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    if mode != "txt2img":
        return {}, {"enabled": False}, ["Forge High-Res Lab is available only for txt2img routes."]
    params = normalize_highres_params(_as_dict(block.get("params")))
    strategy = str(params.get("strategy") or "standard")
    if strategy not in {"standard", "forge_pixel_refine"}:
        return {}, {"enabled": False, "strategy": strategy}, [f"Forge High-Res Lab does not map strategy {strategy}."]
    requested_upscaler = str(params.get("upscaler") or "Latent").strip() or "Latent"
    upscaler = "Latent" if requested_upscaler.casefold() == "latent" else _catalog_match(requested_upscaler, available_upscalers)
    if available_upscalers and not upscaler:
        return {}, {"enabled": False, "strategy": strategy}, [f"Forge High-Res upscaler is not available: {requested_upscaler}."]
    upscaler = upscaler or requested_upscaler
    update: dict[str, Any] = {
        "enable_hr": True,
        "hr_scale": float(params.get("scale") or 1.5),
        "hr_upscaler": upscaler,
        "hr_second_pass_steps": int(params.get("steps") or 0),
        "denoising_strength": float(params.get("denoise") or 0.28),
        # Forge Neo's Hires path expects explicit reuse markers when Neo does not
        # override the second-pass checkpoint/modules. Without these values a plain
        # txt2img request can hit the hr_additional_modules=None crash path.
        "hr_checkpoint_name": "Use same checkpoint",
        "hr_additional_modules": ["Use same choices"],
    }
    if params.get("target_width"):
        update["hr_resize_x"] = int(params["target_width"])
    if params.get("target_height"):
        update["hr_resize_y"] = int(params["target_height"])
    if str(params.get("sampler") or "").strip():
        update["hr_sampler_name"] = str(params["sampler"]).strip()
    if str(params.get("scheduler") or "").strip():
        update["hr_scheduler"] = str(params["scheduler"]).strip()
    return update, {"enabled": True, "strategy": strategy, "params": deepcopy(params)}, []


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _catalog_match(requested: Any, available: Iterable[str], *, fallback: str = "") -> str:
    text = str(requested or "").strip()
    choices = [str(item).strip() for item in available if str(item or "").strip()]
    if not text:
        text = fallback
    target = _normalize_name(text)
    for choice in choices:
        if _normalize_name(choice) == target:
            return choice
    for choice in choices:
        normalized = _normalize_name(choice)
        if target and (target in normalized or normalized in target):
            return choice
    return text if text and not choices else ""


def _asset_reference(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("data_uri", "preview_data_url", "preview_url", "path", "stored_path", "source_path", "file", "value", "url", "image", "ref", "filename", "image_name", "comfy_image_name"):
            resolved = _asset_reference(value.get(key))
            if resolved:
                return resolved
    if isinstance(value, list):
        for item in value:
            resolved = _asset_reference(item)
            if resolved:
                return resolved
    return ""


def _unit_asset(assets: dict[str, Any], buckets: tuple[str, ...], uid: str) -> str:
    for bucket in buckets:
        records = assets.get(bucket)
        if isinstance(records, dict):
            for key in (uid, "primary", "default"):
                if key in records:
                    found = _asset_reference(records.get(key))
                    if found:
                        return found
        else:
            found = _asset_reference(records)
            if found:
                return found
    return ""


def _sanitize_forge_controlnet_unit(unit: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(unit, dict):
        return {}, []
    normalized = dict(unit)
    notes: list[str] = []

    def _force(key: str, value: Any, note: str, *, condition: bool) -> None:
        if not condition:
            return
        normalized[key] = value
        notes.append(note)

    _force("advanced_enabled", False, "advanced control", condition=bool(normalized.get("advanced_enabled")))
    _force("strength_schedule", "flat", "non-flat strength schedule", condition=str(normalized.get("strength_schedule") or "flat") != "flat")
    _force("batch_mode", "auto", "batch mode", condition=str(normalized.get("batch_mode") or "auto") != "auto")
    _force("sliding_context", False, "sliding context", condition=bool(normalized.get("sliding_context")))
    _force("invert_map", False, "invert map", condition=bool(normalized.get("invert_map")))
    if str(normalized.get("weight_preset") or "balanced") not in {"balanced", "prompt_strong", "control_strong"}:
        normalized["weight_preset"] = "balanced"
        notes.append("soft/strict weight preset")
    if str(normalized.get("mask_mode") or "none") == "inpaint_mask":
        normalized["mask_mode"] = "none"
        notes.append("inpaint mask mode")
    preprocessor = str(normalized.get("preprocessor") or normalized.get("unit") or "")
    if preprocessor in {"openpose", "dwpose"}:
        if bool(normalized.get("openpose_hand")) or bool(normalized.get("openpose_face")):
            notes.append("OpenPose hand/face toggles")
        normalized["openpose_hand"] = False
        normalized["openpose_face"] = False
        if normalized.get("openpose_body") is False:
            notes.append("OpenPose body disabled")
        normalized["openpose_body"] = True
    return normalized, notes


def _compile_controlnet(block: dict[str, Any], *, snapshot: dict[str, Any], mode: str, image_encoder) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("controlnet"))
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "Forge ControlNet API/schema was not verified for this profile.")]
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    available_modes = {str(item) for item in capability.get("available_modes") or []}
    if available_modes and script_mode not in available_modes:
        return {}, {"enabled": False}, [f"Forge ControlNet script schema is unavailable for {script_mode}."]
    normalized, notes = normalize_controlnet_block(block, enforce_route_state=False)
    if not normalized.get("enabled"):
        return {}, {"enabled": False}, ["Forge ControlNet has no active units."]
    if _as_dict(normalized.get("params")).get("controlnet_task") != "map_control":
        return {}, {"enabled": False}, ["Forge ControlNet supports Neo map_control only; inpaint_control and outpaint_control remain gated."]
    available_models = [str(item) for item in capability.get("models") or []]
    available_modules = [str(item) for item in capability.get("modules") or []]
    units = _as_list(_as_dict(normalized.get("inputs")).get("units"))
    slot_map = _as_dict(capability.get("unit_slots_by_mode"))
    max_units = int(slot_map.get(script_mode) or capability.get("max_units") or len(units))
    active_units = [item for item in units if isinstance(item, dict) and item.get("enabled") is not False]
    if len(active_units) > max_units:
        return {}, {"enabled": False}, [f"Forge ControlNet exposes {max_units} {script_mode} unit slots, but Neo requested {len(active_units)}."]
    assets = _as_dict(normalized.get("assets"))
    compiled_units: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw_unit in units:
        if not isinstance(raw_unit, dict) or raw_unit.get("enabled") is False:
            continue
        unit, sanitized = _sanitize_forge_controlnet_unit(raw_unit)
        uid = str(unit.get("uid") or f"unit_{len(compiled_units) + 1}")
        if sanitized:
            notes.append(f"Forge ControlNet unit {uid} auto-normalized unsupported UI-only settings: {', '.join(sanitized)}.")
        source = _unit_asset(assets, ("generated_maps", "control_images", "source_images"), uid)
        if not source:
            errors.append(f"Forge ControlNet unit {uid} requires a control image or generated map.")
            continue
        model = _catalog_match(unit.get("model"), available_models)
        module = _catalog_match(unit.get("preprocessor"), available_modules, fallback="None")
        if available_models and not model:
            errors.append(f"Forge ControlNet model is unavailable for unit {uid}: {unit.get('model') or '(not selected)' }.")
            continue
        if available_modules and not module:
            errors.append(f"Forge ControlNet preprocessor is unavailable for unit {uid}: {unit.get('preprocessor')}.")
            continue
        fit_mode = str(unit.get("fit_mode") or "contain")
        resize_mode = {"stretch": "Just Resize", "cover": "Crop and Resize", "contain": "Resize and Fill", "native": "Just Resize"}.get(fit_mode, "Resize and Fill")
        control_mode = {"prompt_strong": "My prompt is more important", "control_strong": "ControlNet is more important"}.get(str(unit.get("weight_preset") or "balanced"), "Balanced")
        unit_payload: dict[str, Any] = {
            "enabled": True,
            "module": module or "None",
            "model": model or "None",
            "weight": float(unit.get("strength") or 0.45),
            "image": image_encoder(source, label=f"ControlNet {uid} image"),
            "resize_mode": resize_mode,
            "processor_res": int(unit.get("detect_resolution") or 512),
            "threshold_a": float(unit.get("canny_low") if unit.get("canny_low") is not None else -1),
            "threshold_b": float(unit.get("canny_high") if unit.get("canny_high") is not None else -1),
            "guidance_start": float(unit.get("start_percent") or 0.0),
            "guidance_end": float(unit.get("end_percent") if unit.get("end_percent") is not None else 1.0),
            "pixel_perfect": bool(unit.get("safe_mode", True)),
            "control_mode": control_mode,
            "save_detected_map": bool(unit.get("save_intermediate", False)),
        }
        mask_mode = str(unit.get("mask_mode") or "none")
        mask_ref = _unit_asset(assets, ("control_masks",), uid)
        if mask_mode == "control_mask" and not mask_ref:
            errors.append(f"Forge ControlNet unit {uid} requests control_mask but no control mask asset was supplied.")
            continue
        if mask_mode == "control_mask" and mask_ref:
            unit_payload["mask_image"] = image_encoder(mask_ref, label=f"ControlNet {uid} mask")
        compiled_units.append(unit_payload)
    script_name = str(capability.get("script_name") or "ControlNet")
    update = {"alwayson_scripts": {script_name: {"args": compiled_units}}} if compiled_units else {}
    meta = {"enabled": bool(compiled_units), "script_name": script_name, "unit_count": len(compiled_units), "notes": notes}
    return update, meta, errors


def _adetailer_args_from_pass(pass_data: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    model = str(pass_data.get("detector_model") or params.get("detector_model") or "None").strip() or "None"
    cfg = params.get("cfg")
    result: dict[str, Any] = {
        "ad_model": model,
        "ad_model_classes": str(params.get("custom_classes") or ""),
        "ad_tab_enable": bool(pass_data.get("enabled", True)),
        "ad_prompt": str(pass_data.get("positive_prompt") or params.get("positive_prompt") or ""),
        "ad_negative_prompt": str(pass_data.get("negative_prompt") or params.get("negative_prompt") or ""),
        "ad_confidence": float(params.get("confidence") or 0.35),
        "ad_mask_k": int(pass_data.get("count") if pass_data.get("count") is not None else params.get("top_k") or 0),
        "ad_dilate_erode": int(params.get("bbox_grow") or 0),
        "ad_mask_blur": int(params.get("mask_blur") or 4),
        "ad_denoising_strength": float(params.get("denoise") or 0.12),
        "ad_inpaint_only_masked": True,
        "ad_inpaint_only_masked_padding": 32,
        "ad_use_steps": True,
        "ad_steps": int(params.get("steps") or 12),
        "ad_use_cfg_scale": cfg not in (None, ""),
        "ad_cfg_scale": float(cfg or 7.0),
    }
    return result


def _compile_adetailer(block: dict[str, Any], *, snapshot: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("adetailer"))
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "Forge ADetailer script schema was not verified for this profile.")]
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    available_modes = {str(item) for item in capability.get("available_modes") or []}
    if available_modes and script_mode not in available_modes:
        return {}, {"enabled": False}, [f"Forge ADetailer script schema is unavailable for {script_mode}."]
    normalized = normalize_adetailer_block({"extensions": {"image.adetailer": block}})
    params = _as_dict(normalized.get("params"))
    passes = [item for item in _as_list(params.get("detailer_passes")) if isinstance(item, dict) and item.get("enabled", True)]
    if not passes:
        return {}, {"enabled": False}, ["Forge ADetailer has no enabled detailer passes."]
    pass_slots = _as_dict(capability.get("pass_slots_by_mode"))
    max_passes = int(pass_slots.get(script_mode) or capability.get("max_passes") or len(passes))
    if len(passes) > max_passes:
        return {}, {"enabled": False}, [f"Forge ADetailer exposes {max_passes} pass slots, but Neo requested {len(passes)}."]
    if any(str(item.get("target_mode") or "auto_detect") != "auto_detect" for item in passes):
        return {}, {"enabled": False}, ["Forge ADetailer manual-box passes are not mapped in this release."]
    unsupported: list[str] = []
    shared_names = {
        str(name or "").strip().casefold(): str(name or "").strip()
        for name in capability.get("shared_model_names") or []
        if str(name or "").strip()
    }
    shared_covered_names = {
        str(name or "").strip().casefold(): str(name or "").strip()
        for name in capability.get("shared_covered_model_names") or []
        if str(name or "").strip()
    }
    shared_coverage_known = bool(capability.get("shared_model_coverage_known"))
    shared_ready = bool(capability.get("shared_extra_model_dirs_ready"))
    shared_blocked: list[str] = []
    for index, item in enumerate(passes, 1):
        detector_type = str(item.get("detector_type") or params.get("detector_type") or "bbox").strip().casefold()
        selected_model = str(item.get("detector_model") or params.get("detector_model") or "").strip()
        selected_key = selected_model.casefold()
        selected_basename = selected_model.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if detector_type.startswith("onnx") or selected_model.casefold().endswith(".onnx"):
            unsupported.append(f"pass {index} ONNX detector")
        if selected_model and selected_model.casefold().endswith((".pth", ".safetensors")):
            unsupported.append(f"pass {index} non-.pt Forge detector")
        selected_is_shared = selected_key in shared_names or selected_basename in shared_names
        selected_is_covered = selected_key in shared_covered_names or selected_basename in shared_covered_names
        if selected_is_shared:
            if shared_coverage_known and not selected_is_covered:
                shared_blocked.append(selected_model)
            elif not shared_coverage_known and not shared_ready:
                shared_blocked.append(selected_model)
        if str(item.get("reference_lock") or "none") != "none":
            unsupported.append(f"pass {index} reference lock")
        if str(item.get("target_order") or "auto") != "auto":
            unsupported.append(f"pass {index} target order")
        if int(item.get("start_index") or 1) != 1:
            unsupported.append(f"pass {index} start index")
        if int(item.get("min_area") or 0) or int(item.get("max_area") or 0):
            unsupported.append(f"pass {index} area filters")
    if unsupported:
        return {}, {"enabled": False}, ["Forge ADetailer does not map: " + ", ".join(unsupported) + "."]
    if shared_blocked:
        return {}, {"enabled": False}, [
            "Forge ADetailer can see the selected detector in Neo's shared library, but that model is outside Forge ADetailer's active native or ad_extra_models_dir directories. Configure the folder that contains this detector, restart Forge, then refresh Forge Admin: "
            + ", ".join(shared_blocked)
            + "."
        ]
    args = [True, False] + [_adetailer_args_from_pass(item, params) for item in passes]
    script_name = str(capability.get("script_name") or "ADetailer")
    return {"alwayson_scripts": {script_name: {"args": args}}}, {
        "enabled": True, "script_name": script_name, "pass_count": len(passes), "contract": capability.get("contract")
    }, []


def _number(value: Any, default: float, minimum: float, maximum: float, *, integer: bool = False) -> float | int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    parsed = max(minimum, min(maximum, parsed))
    return int(round(parsed)) if integer else float(parsed)


def _compile_forge_couple(
    block: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    mode: str,
    family: str,
    prompt: str,
    image_encoder,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("forge_couple"))
    errors: list[str] = []
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "ForgeCouple native script schema was not verified for this profile.")]
    if family not in {"sd15", "sdxl"}:
        errors.append(f"ForgeCouple Phase 3 supports SD1.5/SDXL only; {family or '(unknown)'} is gated.")
    if mode not in {"txt2img", "img2img", "inpaint"}:
        errors.append(f"ForgeCouple Phase 3 does not support the {mode} route.")
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    available_modes = {str(item) for item in capability.get("available_modes") or []}
    if available_modes and script_mode not in available_modes:
        errors.append(f"ForgeCouple script schema is unavailable for {script_mode}.")

    normalized = normalize_forge_couple_block(block)
    params = _as_dict(normalized.get("params"))
    region_mode = str(params.get("mode") or "Basic")
    if region_mode not in {"Basic", "Advanced", "Mask"}:
        errors.append("Neo ForgeCouple Phase 3 supports Basic, Advanced, and Mask modes only.")
    regions = split_forge_couple_prompt(prompt, params.get("separator"))
    if any(not item for item in regions):
        errors.append("ForgeCouple prompt regions cannot be empty. Remove trailing or repeated separators.")

    mapping: list[list[float]] = []
    masks: list[dict[str, Any]] = []
    mask_coverage: float | None = None
    required = forge_couple_required_prompt_lines(params)
    if region_mode == "Advanced":
        mapping = normalize_forge_couple_mapping(params.get("advanced_mapping"))
        errors.extend(forge_couple_mapping_errors(mapping))
        if len(regions) != len(mapping):
            errors.append(f"ForgeCouple Advanced mode requires one positive-prompt region per mapping; found {len(regions)} prompts and {len(mapping)} mappings.")
        if not forge_couple_mapping_errors(mapping) and not forge_couple_mapping_covers_canvas(mapping):
            errors.append("ForgeCouple Advanced mapping must cover the entire canvas without gaps.")
    elif region_mode == "Mask":
        masks = normalize_forge_couple_mask_mapping(params.get("mask_mapping"))
        errors.extend(forge_couple_mask_mapping_errors(params.get("mask_mapping")))
        if len(regions) != required:
            errors.append(
                f"ForgeCouple Mask mode requires exactly {required} prompt regions for {len(masks)} masks"
                f" and Global Effect {params.get('background')}; found {len(regions)}."
            )
        mask_coverage = forge_couple_mask_union_coverage(params.get("mask_mapping"))
        if str(params.get("background") or "None") == "None":
            if mask_coverage is None:
                errors.append("ForgeCouple Mask mode could not verify full-canvas coverage from the submitted session masks.")
            elif mask_coverage < 0.999:
                errors.append(f"ForgeCouple Mask layers cover only {mask_coverage * 100:.1f}% of the canvas. Cover the full canvas or enable Global Effect.")
    elif len(regions) < required:
        errors.append(f"ForgeCouple Basic mode requires at least {required} positive-prompt regions; found {len(regions)}.")

    errors.extend(forge_couple_tile_errors(params, mode=mode, region_mode=region_mode))
    if params.get("tile_enabled"):
        if not capability.get("tile_runtime_available"):
            errors.append(str(capability.get("tile_reason") or "Forge selectable SD Upscale is unavailable for ForgeCouple Tile Mode."))
        tile_upscalers = [str(item) for item in capability.get("tile_upscalers") or [] if str(item or "").strip()]
        tile_upscaler = str(params.get("tile_upscaler") or "None")
        if tile_upscalers and tile_upscaler not in tile_upscalers:
            errors.append(f"ForgeCouple Tile upscaler {tile_upscaler} is not present in the live SD Upscale catalog.")
    if errors:
        return {}, {
            "enabled": False,
            "mode": region_mode,
            "region_count": len(regions),
            "required_region_count": required,
            "mapping_count": len(mapping),
            "mask_count": len(masks),
            "tile_enabled": bool(params.get("tile_enabled")),
        }, errors

    args = compile_forge_couple_args(normalized, image_encoder=image_encoder)
    script_name = str(capability.get("script_name") or "Forge Couple")
    metadata = {
        "enabled": True,
        "script_name": script_name,
        "contract": capability.get("contract"),
        "mode": region_mode,
        "direction": params.get("direction") if region_mode == "Basic" else None,
        "background": params.get("background") if region_mode in {"Basic", "Mask"} else "None",
        "background_weight": params.get("background_weight") if region_mode in {"Basic", "Mask"} and params.get("background") != "None" else None,
        "advanced_mapping": deepcopy(mapping) if region_mode == "Advanced" else None,
        "mapping_count": len(mapping) if region_mode == "Advanced" else 0,
        "mapping_covers_canvas": forge_couple_mapping_covers_canvas(mapping) if region_mode == "Advanced" else None,
        "mask_layers": redact_forge_couple_mask_mapping(masks) if region_mode == "Mask" else [],
        "mask_count": len(masks) if region_mode == "Mask" else 0,
        "mask_coverage_ratio": mask_coverage if region_mode == "Mask" else None,
        "tile_enabled": bool(params.get("tile_enabled")),
        "tile_columns": int(params.get("tile_columns") or 0) if params.get("tile_enabled") else 0,
        "tile_rows": int(params.get("tile_rows") or 0) if params.get("tile_enabled") else 0,
        "tile_threshold": float(params.get("tile_threshold") or 0.0) if params.get("tile_enabled") else None,
        "tile_upscaler": str(params.get("tile_upscaler") or "None") if params.get("tile_enabled") else None,
        "tile_scale_factor": float(params.get("tile_scale_factor") or 2.0) if params.get("tile_enabled") else None,
        "tile_overlap": int(params.get("tile_overlap") or 0) if params.get("tile_enabled") else None,
        "tile_save_to_extras": bool(params.get("tile_save_to_extras", False)) if params.get("tile_enabled") else False,
        "tile_runtime_available": bool(capability.get("tile_runtime_available")),
        "tile_script_name": str(capability.get("tile_script_name") or "SD Upscale") if params.get("tile_enabled") else None,
        "disable_hr": bool(params.get("disable_hr", True)),
        "common_parser": params.get("common_parser"),
        "def_in_prompt": bool(params.get("def_in_prompt", True)),
        "region_count": len(regions),
        "prompt_authority": "neo_core_positive_prompt",
    }
    return {"alwayson_scripts": {script_name: {"args": args}}}, metadata, []


def _compile_pid(block: dict[str, Any], *, snapshot: dict[str, Any], mode: str, family: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("pid_integrated"))
    errors: list[str] = []
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "Forge PiD Integrated schema was not verified for this profile.")]
    supported_families = {"sdxl", "flux", "flux2_klein", "qwen_image", "qwen_image_edit_2509"}
    if family not in supported_families:
        errors.append(f"Forge PiD Integrated is not enabled for Neo family {family or '(unknown)' } in E2.")
    script_mode = "img2img" if mode in {"img2img", "inpaint", "outpaint", "edit"} else "txt2img"
    available_modes = {str(item) for item in capability.get("available_modes") or []}
    if available_modes and script_mode not in available_modes:
        errors.append(f"Forge PiD Integrated script schema is unavailable for {script_mode}.")
    params = _as_dict(block.get("params"))
    inputs = _as_dict(block.get("inputs"))
    pid_model = str(inputs.get("pid_model") or params.get("pid_model") or "").strip()
    vae = str(inputs.get("vae") or params.get("vae") or "").strip()
    text_encoder = str(inputs.get("text_encoder") or params.get("text_encoder") or "").strip()
    for label, selected, key in (("PiD model", pid_model, "pid_models"), ("VAE", vae, "vaes"), ("text encoder", text_encoder, "text_encoders")):
        catalog = [str(item) for item in capability.get(key) or []]
        if not selected:
            errors.append(f"Forge PiD Integrated requires a {label} selection.")
        elif catalog and selected not in catalog:
            errors.append(f"Forge PiD Integrated {label} is unavailable in the live script catalog: {selected}.")
    if errors:
        return {}, {"enabled": False}, errors
    prompt = str(params.get("prompt") or "").strip() or None
    args = [True, prompt, pid_model, vae, text_encoder, _number(params.get("degrade_sigma"), 0.0, 0.0, 1.0), bool(params.get("color_correction", True))]
    script_name = str(capability.get("script_name") or "PiD Integrated")
    return {"alwayson_scripts": {script_name: {"args": args}}}, {"enabled": True, "script_name": script_name, "contract": capability.get("contract")}, []


def _compile_spectrum(block: dict[str, Any], *, snapshot: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("spectrum"))
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "Forge Spectrum schema was not verified for this profile.")]
    script_mode = "img2img" if mode in {"img2img", "inpaint", "outpaint", "edit"} else "txt2img"
    available_modes = {str(item) for item in capability.get("available_modes") or []}
    if available_modes and script_mode not in available_modes:
        return {}, {"enabled": False}, [f"Forge Spectrum script schema is unavailable for {script_mode}."]
    params = _as_dict(block.get("params"))
    args = [
        True,
        _number(params.get("prediction_weighting"), 0.25, 0.0, 1.0),
        _number(params.get("polynomial_degree"), 6, 1, 8, integer=True),
        _number(params.get("regularization"), 0.5, 0.0, 2.0),
        _number(params.get("cache_window"), 2, 1, 10, integer=True),
        _number(params.get("window_growth"), 0.0, 0.0, 2.0),
        _number(params.get("warmup_steps"), 6, 0, 20, integer=True),
        _number(params.get("stop_caching_step"), 0.9, 0.0, 1.0),
    ]
    script_name = str(capability.get("script_name") or "Spectrum Integrated")
    return {"alwayson_scripts": {script_name: {"args": args}}}, {"enabled": True, "script_name": script_name, "contract": capability.get("contract")}, []


def _compile_multidiffusion(block: dict[str, Any], *, snapshot: dict[str, Any], mode: str, family: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not block or block.get("enabled") is False:
        return {}, {"enabled": False}, []
    capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("multidiffusion"))
    errors: list[str] = []
    if not capability.get("available"):
        return {}, {"enabled": False}, [str(capability.get("reason") or "Forge MultiDiffusion schema was not verified for this profile.")]
    if mode not in {"img2img", "inpaint", "outpaint"}:
        errors.append("Forge MultiDiffusion Integrated is img2img-only; use Img2Img, Inpaint, or Outpaint.")
    if family not in {"sd15", "sdxl"}:
        errors.append(f"Forge MultiDiffusion is conservatively enabled only for SD 1.5/SDXL in E2; {family or '(unknown)'} remains gated pending physical validation.")
    if errors:
        return {}, {"enabled": False}, errors
    params = _as_dict(block.get("params"))
    method = str(params.get("method") or "Mixture of Diffusers")
    methods = [str(item) for item in capability.get("methods") or []]
    if methods and method not in methods:
        return {}, {"enabled": False}, [f"Forge MultiDiffusion method is unavailable: {method}."]
    args = [
        True, method,
        _number(params.get("tile_width"), 768, 256, 2048, integer=True),
        _number(params.get("tile_height"), 768, 256, 2048, integer=True),
        _number(params.get("tile_overlap"), 64, 0, 1024, integer=True),
        _number(params.get("tile_batch_size"), 1, 1, 8, integer=True),
    ]
    script_name = str(capability.get("script_name") or "MultiDiffusion Integrated")
    return {"alwayson_scripts": {script_name: {"args": args}}}, {"enabled": True, "script_name": script_name, "contract": capability.get("contract")}, []


def _merge_alwayson(payload: dict[str, Any], update: dict[str, Any]) -> None:
    incoming = _as_dict(update.get("alwayson_scripts"))
    if not incoming:
        return
    target = payload.setdefault("alwayson_scripts", {})
    if not isinstance(target, dict):
        target = {}
        payload["alwayson_scripts"] = target
    target.update(deepcopy(incoming))




def _controlnet_args(update: dict[str, Any], script_name: str) -> list[dict[str, Any]]:
    scripts = _as_dict(update.get("alwayson_scripts"))
    block = _as_dict(scripts.get(script_name))
    return [deepcopy(item) for item in _as_list(block.get("args")) if isinstance(item, dict)]


def _combined_controlnet_update(
    *,
    control_update: dict[str, Any],
    control_meta: dict[str, Any],
    ip_units: list[dict[str, Any]],
    ip_meta: dict[str, Any],
    snapshot: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    control_cap = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("controlnet"))
    script_name = str(control_meta.get("script_name") or ip_meta.get("script_name") or control_cap.get("script_name") or "ControlNet")
    standard_units = _controlnet_args(control_update, script_name)
    combined = [*standard_units, *[deepcopy(item) for item in ip_units]]
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    slot_map = _as_dict(control_cap.get("unit_slots_by_mode"))
    max_units = int(slot_map.get(script_mode) or control_cap.get("max_units") or len(combined) or 0)
    errors: list[str] = []
    if max_units and len(combined) > max_units:
        errors.append(
            f"Forge ControlNet exposes {max_units} {script_mode} unit slots, but Neo requested "
            f"{len(standard_units)} ControlNet + {len(ip_units)} IP-Adapter units."
        )
    update = {"alwayson_scripts": {script_name: {"args": combined}}} if combined and not errors else (control_update if errors else {})
    meta = {
        "script_name": script_name,
        "controlnet_units": len(standard_units),
        "ip_adapter_units": len(ip_units),
        "combined_units": len(combined),
        "max_units": max_units,
    }
    return update, errors, meta


def apply_forge_extensions(
    payload: dict[str, Any],
    *,
    extensions: Any,
    snapshot: dict[str, Any] | None,
    mode: str,
    image_encoder,
    family: str = "",
) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {"schema_id": FORGE_EXTENSION_SCHEMA_ID, "extensions": {}}

    active = active_extension_ids(extensions)
    unknown = sorted(active - set(_ALIAS_GROUPS) - _ALLOWED_ALWAYS)
    if unknown:
        errors.append("Forge extension mappings are unavailable for: " + ", ".join(unknown) + ".")

    prompt, lora_meta, lora_errors = _compile_lora(extension_block(extensions, "lora_stack"), str(payload.get("prompt") or ""))
    payload["prompt"] = prompt
    metadata["extensions"]["lora_stack"] = lora_meta
    errors.extend(lora_errors)

    embedding_block = extension_block(extensions, "embeddings_ti")
    embedding_capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("embeddings_ti"))
    if embedding_block and embedding_block.get("enabled") is not False and not embedding_capability.get("available"):
        errors.append(str(embedding_capability.get("reason") or "Forge embeddings capability was not verified for this profile."))
    prompt, negative, embedding_meta, embedding_errors = _compile_embeddings(
        embedding_block, str(payload.get("prompt") or ""), str(payload.get("negative_prompt") or "")
    )
    payload["prompt"] = prompt
    payload["negative_prompt"] = negative
    metadata["extensions"]["embeddings_ti"] = embedding_meta
    errors.extend(embedding_errors)

    upscalers = {str(item.get("name") if isinstance(item, dict) else item).strip() for item in _as_list(snapshot.get("upscalers"))}
    highres_block = extension_block(extensions, "high_res_lab")
    highres_capability = _as_dict(_as_dict(snapshot.get("extension_capabilities")).get("high_res_lab"))
    if highres_block and highres_block.get("enabled") is not False and not highres_capability.get("available"):
        errors.append(str(highres_capability.get("reason") or "Forge High-Res capability was not verified for this profile."))
    highres_update, highres_meta, highres_errors = _compile_highres(highres_block, mode=mode, available_upscalers=upscalers)
    payload.update(highres_update)
    metadata["extensions"]["high_res_lab"] = highres_meta
    errors.extend(highres_errors)

    control_update, control_meta, control_errors = _compile_controlnet(
        extension_block(extensions, "controlnet"), snapshot=snapshot, mode=mode, image_encoder=image_encoder
    )
    ip_units, ip_meta, ip_errors = compile_forge_ip_adapter_units(
        extension_block(extensions, "ip_adapter"), snapshot=snapshot, mode=mode, family=family, image_encoder=image_encoder
    )
    combined_control_update, combined_control_errors, combined_control_meta = _combined_controlnet_update(
        control_update=control_update, control_meta=control_meta, ip_units=ip_units, ip_meta=ip_meta, snapshot=snapshot, mode=mode
    )
    _merge_alwayson(payload, combined_control_update)
    metadata["extensions"]["controlnet"] = control_meta
    metadata["extensions"]["ip_adapter"] = ip_meta
    metadata["extensions"]["controlnet_ip_adapter_aggregate"] = combined_control_meta
    errors.extend(control_errors)
    errors.extend(ip_errors)
    errors.extend(combined_control_errors)

    adetailer_update, adetailer_meta, adetailer_errors = _compile_adetailer(extension_block(extensions, "adetailer"), snapshot=snapshot, mode=mode)
    _merge_alwayson(payload, adetailer_update)
    metadata["extensions"]["adetailer"] = adetailer_meta
    errors.extend(adetailer_errors)

    forge_couple_update, forge_couple_meta, forge_couple_errors = _compile_forge_couple(
        extension_block(extensions, "forge_couple"),
        snapshot=snapshot,
        mode=mode,
        family=family,
        prompt=str(payload.get("prompt") or ""),
        image_encoder=image_encoder,
    )
    _merge_alwayson(payload, forge_couple_update)
    metadata["extensions"]["forge_couple"] = forge_couple_meta
    errors.extend(forge_couple_errors)

    pid_update, pid_meta, pid_errors = _compile_pid(extension_block(extensions, "pid_integrated"), snapshot=snapshot, mode=mode, family=family)
    if pid_meta.get("enabled") and highres_meta.get("enabled"):
        pid_errors.append("Forge PiD Integrated cannot run with Hires Fix / High-Res Lab in the same generation.")
        pid_update = {}
        pid_meta = {**pid_meta, "enabled": False, "conflict": "high_res_lab"}
    _merge_alwayson(payload, pid_update)
    metadata["extensions"]["pid_integrated"] = pid_meta
    errors.extend(pid_errors)

    spectrum_update, spectrum_meta, spectrum_errors = _compile_spectrum(extension_block(extensions, "spectrum"), snapshot=snapshot, mode=mode)
    _merge_alwayson(payload, spectrum_update)
    metadata["extensions"]["spectrum"] = spectrum_meta
    errors.extend(spectrum_errors)

    multidiff_update, multidiff_meta, multidiff_errors = _compile_multidiffusion(extension_block(extensions, "multidiffusion"), snapshot=snapshot, mode=mode, family=family)
    _merge_alwayson(payload, multidiff_update)
    metadata["extensions"]["multidiffusion"] = multidiff_meta
    errors.extend(multidiff_errors)

    generic_update, generic_meta, generic_errors = compile_forge_generic_extension_bridge(
        extension_block(extensions, "forge_script_bridge"),
        snapshot=snapshot,
        mode=mode,
        family=family,
        payload=payload,
    )
    _merge_alwayson(payload, generic_update)
    if "script_name" in generic_update:
        payload["script_name"] = generic_update["script_name"]
        payload["script_args"] = deepcopy(generic_update.get("script_args") or [])
    metadata["extensions"]["forge_script_bridge"] = generic_meta
    errors.extend(generic_errors)

    forge_couple_runtime = _as_dict(metadata["extensions"].get("forge_couple"))
    if forge_couple_runtime.get("enabled") and forge_couple_runtime.get("tile_enabled"):
        selected_script = str(payload.get("script_name") or "").strip()
        if selected_script:
            errors.append(
                f"ForgeCouple Tile Mode owns the one selectable-script slot with SD Upscale; "
                f"disable the separately selected {selected_script} script."
            )
        elif forge_couple_runtime.get("tile_runtime_available"):
            tile_script_name = str(forge_couple_runtime.get("tile_script_name") or "SD Upscale")
            payload["script_name"] = tile_script_name
            payload["script_args"] = [
                int(forge_couple_runtime.get("tile_overlap") or 0),
                str(forge_couple_runtime.get("tile_upscaler") or "None"),
                float(forge_couple_runtime.get("tile_scale_factor") or 2.0),
                bool(forge_couple_runtime.get("tile_save_to_extras", False)),
            ]
        else:
            errors.append("ForgeCouple Tile Mode requires the verified selectable SD Upscale runtime in Forge.")

    if extension_block(extensions, "image_upscale").get("enabled"):
        errors.append("Forge Image Upscale is a standalone Extras route and is not valid inside a base generation job.")

    metadata["active_extension_ids"] = sorted(active)
    metadata["warnings"] = warnings
    metadata["errors"] = errors
    return {"payload": payload, "metadata": metadata, "errors": errors, "warnings": warnings}


def validate_forge_extensions(extensions: Any, *, snapshot: dict[str, Any] | None, mode: str, family: str = "") -> tuple[list[str], list[str]]:
    """Cheap validation used before compilation.

    Asset decoding and exact ControlNet unit compilation happen in
    :func:`apply_forge_extensions`; this function only rejects known unsupported
    contracts early without mutating prompts.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    active = active_extension_ids(extensions)
    supported = set(_ALIAS_GROUPS)
    for extension_id in sorted(active - supported):
        errors.append(f"Forge extension compiler mapping is unavailable: {extension_id}.")
    extension_caps = _as_dict(snapshot.get("extension_capabilities"))
    if "high_res_lab" in active:
        if mode != "txt2img":
            errors.append("Forge High-Res Lab is supported only for txt2img.")
        highres_cap = _as_dict(extension_caps.get("high_res_lab"))
        if not highres_cap.get("available"):
            errors.append(str(highres_cap.get("reason") or "Forge High-Res capability was not verified for this profile."))
    if "embeddings_ti" in active:
        embedding_cap = _as_dict(extension_caps.get("embeddings_ti"))
        if not embedding_cap.get("available"):
            errors.append(str(embedding_cap.get("reason") or "Forge embeddings capability was not verified for this profile."))
    script_mode = "img2img" if mode in {"img2img", "inpaint"} else "txt2img"
    control_cap = _as_dict(extension_caps.get("controlnet"))
    if "controlnet" in active:
        if not control_cap.get("available"):
            errors.append(str(control_cap.get("reason") or "Forge ControlNet schema is unavailable."))
        elif control_cap.get("available_modes") and script_mode not in set(control_cap.get("available_modes") or []):
            errors.append(f"Forge ControlNet script schema is unavailable for {script_mode}.")
    if "ip_adapter" in active:
        ip_cap = _as_dict(extension_caps.get("ip_adapter"))
        if not ip_cap.get("available"):
            errors.append(str(ip_cap.get("reason") or "Forge IP-Adapter capability is unavailable."))
        elif family and family not in {"sd15", "sdxl"}:
            errors.append(f"Forge IP-Adapter E1.1 supports SD1.5/SDXL only; {family} remains gated.")
        elif mode not in {"txt2img", "img2img", "inpaint"}:
            errors.append(f"Forge IP-Adapter E1.1 does not support {mode}.")
        elif ip_cap.get("available_modes") and script_mode not in set(ip_cap.get("available_modes") or []):
            errors.append(f"Forge IP-Adapter ControlNet contract is unavailable for {script_mode}.")
    adetailer_cap = _as_dict(extension_caps.get("adetailer"))
    if "adetailer" in active:
        if not adetailer_cap.get("available"):
            errors.append(str(adetailer_cap.get("reason") or "Forge ADetailer schema is unavailable."))
        elif adetailer_cap.get("available_modes") and script_mode not in set(adetailer_cap.get("available_modes") or []):
            errors.append(f"Forge ADetailer script schema is unavailable for {script_mode}.")
    if "forge_couple" in active:
        forge_couple_cap = _as_dict(extension_caps.get("forge_couple"))
        if not forge_couple_cap.get("available"):
            errors.append(str(forge_couple_cap.get("reason") or "ForgeCouple native script schema is unavailable."))
        if family and family not in {"sd15", "sdxl"}:
            errors.append(f"ForgeCouple Phase 3 supports SD1.5/SDXL only; {family} is gated.")
        if mode not in {"txt2img", "img2img", "inpaint"}:
            errors.append(f"ForgeCouple Phase 3 does not support the {mode} route.")
        if forge_couple_cap.get("available_modes") and script_mode not in set(forge_couple_cap.get("available_modes") or []):
            errors.append(f"ForgeCouple script schema is unavailable for {script_mode}.")
        if "multidiffusion" in active:
            errors.append("ForgeCouple and MultiDiffusion are both regional conditioning engines; disable one before generation.")
        scene_block = _payload_container(extensions).get("image.scene_director")
        if isinstance(scene_block, dict) and scene_block.get("enabled") is not False:
            errors.append("ForgeCouple and Scene Director cannot run together on the same generation.")

    if "pid_integrated" in active:
        pid_cap = _as_dict(extension_caps.get("pid_integrated"))
        if not pid_cap.get("available"):
            errors.append(str(pid_cap.get("reason") or "Forge PiD Integrated schema is unavailable."))
        if family and family not in {"sdxl", "flux", "flux2_klein", "qwen_image", "qwen_image_edit_2509"}:
            errors.append(f"Forge PiD Integrated is not enabled for Neo family {family} in E2.")
        if "high_res_lab" in active:
            errors.append("Forge PiD Integrated cannot run with Hires Fix / High-Res Lab in the same generation.")
    if "spectrum" in active:
        spectrum_cap = _as_dict(extension_caps.get("spectrum"))
        if not spectrum_cap.get("available"):
            errors.append(str(spectrum_cap.get("reason") or "Forge Spectrum schema is unavailable."))
        elif spectrum_cap.get("available_modes") and script_mode not in set(spectrum_cap.get("available_modes") or []):
            errors.append(f"Forge Spectrum script schema is unavailable for {script_mode}.")
    if "multidiffusion" in active:
        multidiff_cap = _as_dict(extension_caps.get("multidiffusion"))
        if not multidiff_cap.get("available"):
            errors.append(str(multidiff_cap.get("reason") or "Forge MultiDiffusion schema is unavailable."))
        if mode not in {"img2img", "inpaint", "outpaint"}:
            errors.append("Forge MultiDiffusion Integrated is img2img-only.")
        if family and family not in {"sd15", "sdxl"}:
            errors.append(f"Forge MultiDiffusion is conservatively enabled only for SD 1.5/SDXL in E2; {family} remains gated.")
    if "forge_script_bridge" in active:
        errors.extend(validate_forge_generic_extension_bridge(
            extension_block(extensions, "forge_script_bridge"),
            snapshot=snapshot,
            mode=mode,
            family=family,
        ))
    if "image_upscale" in active:
        errors.append("Forge Image Upscale must run through its standalone Extras route, not a generation extension block.")
    return errors, warnings
