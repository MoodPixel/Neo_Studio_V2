from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Mapping

SCHEMA_ID = "neo.image.lanpaint_route_family_contract.v1"
SCHEMA_VERSION = 1
ROUTE_FAMILY_ID = "image.inpaint.lanpaint"
ENGINE_ID = "lanpaint"
MODE_ID = "inpaint"
EXECUTION_STATE = "contract_only"

BASE_STAGE_ORDER = (
    "source_image",
    "mask_image",
    "crop_context",
    "processing_resize",
    "sampling_mask_refine",
    "latent_encode",
    "latent_noise_mask",
    "family_model_transform",
    "lanpaint_sample",
    "latent_decode",
    "restore_crop_size",
    "stitch_mask_refine",
    "stitch_composite",
    "output_handoff",
)

BASE_REQUIRED_STAGE_ROLES = (
    "source_image",
    "mask_image",
    "crop_context",
    "processing_resize",
    "sampling_mask_refine",
    "latent_encode",
    "latent_noise_mask",
    "lanpaint_sample",
    "latent_decode",
    "restore_crop_size",
    "stitch_mask_refine",
    "stitch_composite",
    "output_handoff",
)

BASE_OPTIONAL_STAGE_ROLES = (
    "family_model_transform",
    "lora_model_transform",
    "differential_diffusion",
    "prompt_enhancement",
)

FAMILY_POLICY_OWNED_FIELDS = (
    "model_loader_role",
    "text_encoder_role",
    "vae_role",
    "positive_conditioning_policy",
    "negative_conditioning_policy",
    "sampler_defaults",
    "lora_support_state",
    "lora_injection_strategy",
    "required_node_classes",
    "required_model_roles",
    "optional_model_transforms",
)

_PROVIDER_ALIASES = {
    "comfy": "comfyui",
    "comfy_ui": "comfyui",
    "comfyui_local": "comfyui",
    "comfy_local": "comfyui",
    "comfyuiportable": "comfyui_portable",
    "comfy_portable": "comfyui_portable",
    "comfy_ui_portable": "comfyui_portable",
}

_LOADER_ALIASES = {
    "native": "diffusion_model",
    "components": "diffusion_model",
    "component": "diffusion_model",
    "safetensor": "diffusion_model",
    "safetensors": "diffusion_model",
    "unet": "diffusion_model",
    "unet_loader": "diffusion_model",
    "diffusion": "diffusion_model",
    "diffusionmodel": "diffusion_model",
    "gguf_unet": "gguf",
    "unet_gguf": "gguf",
    "checkpoint_aio": "checkpoint",
    "aio": "checkpoint",
}

_FAMILY_ALIASES = {
    "krea_2": "krea2",
    "krea_2_raw": "krea2",
    "krea2_raw": "krea2",
    "krea_2_base": "krea2",
    "krea2_base": "krea2",
    "krea_2_turbo": "krea2_turbo",
    "krea2turbo": "krea2_turbo",
    "qwen": "qwen_image",
    "qwen_image_edit": "qwen_image_edit",
    "qwen_image_edit_2509": "qwen_image_edit_2509",
    "qwen_2509": "qwen_image_edit_2509",
    "qwen_image_edit_2511": "qwen_image_edit_2511",
    "qwen_2511": "qwen_image_edit_2511",
    "zimage": "z_image",
    "z_image_base": "z_image_base",
    "zimage_base": "z_image_base",
    "zimage_turbo": "z_image_turbo",
    "flux_2_dev": "flux2_dev",
    "flux2dev": "flux2_dev",
    "flux_2_klein": "flux2_klein",
    "flux2klein": "flux2_klein",
}

_PROMPT_MODE_ALIASES = {
    "image": "image_first",
    "imagefirst": "image_first",
    "image_first": "image_first",
    "prompt": "prompt_first",
    "promptfirst": "prompt_first",
    "prompt_first": "prompt_first",
}

_ALLOWED_LORA_STATES = {
    "family_policy",
    "supported",
    "experimental",
    "unsupported",
}
_ALLOWED_LORA_STRATEGIES = {
    "family_policy",
    "model_only",
    "model_and_clip",
    "unsupported",
}
_ALLOWED_CONTEXT_MODES = {"masked_bounds", "full_frame"}
_ALLOWED_ASSET_REF_KINDS = {"neo_asset_id", "portable_name", "output_id", "job_id"}

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_provider_id(value: Any) -> str:
    normalized = _slug(value)
    return _PROVIDER_ALIASES.get(normalized, normalized)


def normalize_family_id(value: Any) -> str:
    normalized = _slug(value)
    return _FAMILY_ALIASES.get(normalized, normalized)


def normalize_loader_id(value: Any) -> str:
    normalized = _slug(value)
    return _LOADER_ALIASES.get(normalized, normalized)


def normalize_variant_id(value: Any) -> str:
    return _slug(value) or "default"


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return default


def _optional_int(
    value: Any,
    *,
    field: str,
    issues: list[dict[str, Any]],
    minimum: int,
    maximum: int,
    multiple_of: int | None = None,
) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        issues.append({"level": "error", "field": field, "message": "Expected an integer value."})
        return None
    clamped = max(minimum, min(maximum, parsed))
    if clamped != parsed:
        issues.append({
            "level": "warning",
            "field": field,
            "message": f"Value was clamped to the supported range {minimum}-{maximum}.",
            "requested": parsed,
            "normalized": clamped,
        })
    if multiple_of:
        rounded = max(multiple_of, int(round(clamped / multiple_of) * multiple_of))
        rounded = max(minimum, min(maximum, rounded))
        if rounded != clamped:
            issues.append({
                "level": "warning",
                "field": field,
                "message": f"Value was rounded to a multiple of {multiple_of}.",
                "requested": clamped,
                "normalized": rounded,
            })
        clamped = rounded
    return clamped


def _optional_float(
    value: Any,
    *,
    field: str,
    issues: list[dict[str, Any]],
    minimum: float,
    maximum: float,
    precision: int = 4,
) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        issues.append({"level": "error", "field": field, "message": "Expected a numeric value."})
        return None
    clamped = max(minimum, min(maximum, parsed))
    if clamped != parsed:
        issues.append({
            "level": "warning",
            "field": field,
            "message": f"Value was clamped to the supported range {minimum}-{maximum}.",
            "requested": parsed,
            "normalized": clamped,
        })
    return round(clamped, precision)


def _portable_asset_ref(value: Any, *, field: str, issues: list[dict[str, Any]]) -> dict[str, str] | None:
    if value in (None, "", {}):
        return None
    raw = _mapping(value)
    if raw:
        kind = _slug(raw.get("kind") or "neo_asset_id")
        ref = str(raw.get("ref") or raw.get("id") or raw.get("asset_id") or raw.get("name") or "").strip()
    else:
        kind = "neo_asset_id"
        ref = str(value).strip()

    if kind not in _ALLOWED_ASSET_REF_KINDS:
        issues.append({
            "level": "error",
            "field": f"{field}.kind",
            "message": "Asset references must use a Neo-owned portable identity kind.",
        })
        return None
    if not ref:
        issues.append({"level": "error", "field": f"{field}.ref", "message": "Asset reference is empty."})
        return None
    if _WINDOWS_ABSOLUTE_RE.match(ref) or ref.startswith("/") or _URI_RE.match(ref):
        issues.append({
            "level": "error",
            "field": f"{field}.ref",
            "message": "LanPaint route contracts do not carry absolute paths or external URLs; use a Neo asset/output identity.",
        })
        return None
    return {"kind": kind, "ref": ref}


def _policy_value_source(*values: Any) -> str:
    return "explicit_request" if any(value not in (None, "") for value in values) else "family_policy"


def build_lanpaint_route_key(
    *,
    provider_id: Any,
    family: Any,
    loader: Any,
    mode: Any = MODE_ID,
    engine: Any = ENGINE_ID,
    variant: Any = "default",
) -> str:
    return ":".join(
        [
            normalize_provider_id(provider_id) or "unresolved_provider",
            normalize_family_id(family) or "unresolved_family",
            normalize_loader_id(loader) or "unresolved_loader",
            _slug(mode) or MODE_ID,
            _slug(engine) or ENGINE_ID,
            normalize_variant_id(variant),
        ]
    )


def lanpaint_contract_fingerprint(contract: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(contract))
    payload.pop("validation", None)
    payload.pop("contract_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_identity(raw: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    identity = _mapping(raw.get("identity"))
    provider_id = normalize_provider_id(identity.get("provider_id", raw.get("provider_id")))
    family = normalize_family_id(identity.get("family", raw.get("family")))
    loader = normalize_loader_id(identity.get("loader", raw.get("loader")))
    requested_mode = _slug(identity.get("mode", raw.get("mode", MODE_ID))) or MODE_ID
    requested_engine = _slug(identity.get("engine", raw.get("engine", ENGINE_ID))) or ENGINE_ID
    variant = normalize_variant_id(identity.get("variant", raw.get("variant", "default")))

    if not provider_id:
        issues.append({"level": "error", "field": "identity.provider_id", "message": "Provider id is required."})
    if not family:
        issues.append({"level": "error", "field": "identity.family", "message": "Model family is required."})
    if not loader:
        issues.append({"level": "error", "field": "identity.loader", "message": "Loader id is required."})
    if requested_mode not in {"inpaint", "inpainting", "mask_inpaint"}:
        issues.append({
            "level": "error",
            "field": "identity.mode",
            "message": f"{ROUTE_FAMILY_ID} supports inpaint only; requested mode was {requested_mode!r}.",
        })
    if requested_engine not in {"lanpaint", "lan_paint"}:
        issues.append({
            "level": "error",
            "field": "identity.engine",
            "message": f"Route engine must be {ENGINE_ID!r}; requested engine was {requested_engine!r}.",
        })

    return {
        "route_family_id": ROUTE_FAMILY_ID,
        "provider_id": provider_id,
        "family": family,
        "loader": loader,
        "mode": MODE_ID,
        "engine": ENGINE_ID,
        "variant": variant,
        "route_key": build_lanpaint_route_key(
            provider_id=provider_id,
            family=family,
            loader=loader,
            mode=MODE_ID,
            engine=ENGINE_ID,
            variant=variant,
        ),
    }


def _normalize_crop_policy(raw: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    policy = _mapping(raw.get("crop_policy") or raw.get("crop"))
    size = _mapping(policy.get("processing_size") or raw.get("processing_size"))
    padding = policy.get("padding_px", policy.get("padding", raw.get("crop_padding")))
    width = size.get("width", raw.get("processing_width"))
    height = size.get("height", raw.get("processing_height"))
    resize_method = _slug(policy.get("resize_method", raw.get("resize_method"))) or None
    context_mode = _slug(policy.get("context_mode", "masked_bounds")) or "masked_bounds"
    if context_mode not in _ALLOWED_CONTEXT_MODES:
        issues.append({
            "level": "warning",
            "field": "crop_policy.context_mode",
            "message": "Unsupported context mode normalized to masked_bounds.",
            "requested": context_mode,
        })
        context_mode = "masked_bounds"
    return {
        "enabled": _as_bool(policy.get("enabled"), True),
        "context_mode": context_mode,
        "padding_px": _optional_int(padding, field="crop_policy.padding_px", issues=issues, minimum=0, maximum=4096),
        "processing_size": {
            "width": _optional_int(width, field="crop_policy.processing_size.width", issues=issues, minimum=64, maximum=8192, multiple_of=8),
            "height": _optional_int(height, field="crop_policy.processing_size.height", issues=issues, minimum=64, maximum=8192, multiple_of=8),
            "multiple_of": 8,
        },
        "resize_method": resize_method,
        "value_authority": _policy_value_source(padding, width, height, resize_method),
    }


def _normalize_mask_policy(raw: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    policy = _mapping(raw.get("mask_policy") or raw.get("mask"))
    sampling = _mapping(policy.get("sampling") or raw.get("sampling_mask"))
    stitch = _mapping(policy.get("stitch") or raw.get("stitch_mask"))

    sample_expand = sampling.get("expand_px", sampling.get("expand", raw.get("sampling_mask_expand")))
    sample_blur = sampling.get("blur_radius", sampling.get("blur", raw.get("sampling_mask_blur")))
    stitch_expand = stitch.get("expand_px", stitch.get("expand", raw.get("stitch_mask_expand")))
    stitch_blur = stitch.get("blur_radius", stitch.get("blur", raw.get("stitch_mask_blur")))

    return {
        "sampling": {
            "expand_px": _optional_int(sample_expand, field="mask_policy.sampling.expand_px", issues=issues, minimum=0, maximum=1024),
            "blur_radius": _optional_float(sample_blur, field="mask_policy.sampling.blur_radius", issues=issues, minimum=0.0, maximum=1024.0),
            "fill_holes": _as_bool(sampling.get("fill_holes"), False),
            "invert": _as_bool(sampling.get("invert"), False),
            "value_authority": _policy_value_source(sample_expand, sample_blur),
        },
        "stitch": {
            "expand_px": _optional_int(stitch_expand, field="mask_policy.stitch.expand_px", issues=issues, minimum=0, maximum=1024),
            "blur_radius": _optional_float(stitch_blur, field="mask_policy.stitch.blur_radius", issues=issues, minimum=0.0, maximum=1024.0),
            "fill_holes": _as_bool(stitch.get("fill_holes"), False),
            "invert": _as_bool(stitch.get("invert"), False),
            "value_authority": _policy_value_source(stitch_expand, stitch_blur),
        },
    }


def _normalize_latent_policy(raw: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(raw.get("latent_policy") or raw.get("latent"))
    differential = _slug(policy.get("differential_diffusion", "family_policy")) or "family_policy"
    return {
        "encode_role": "family_vae_encode",
        "decode_role": "family_vae_decode",
        "latent_format": _slug(policy.get("latent_format")) or "family_policy",
        "noise_mask_required": _as_bool(policy.get("noise_mask_required"), True),
        "differential_diffusion": differential,
        "value_authority": "family_policy" if differential == "family_policy" else "explicit_request",
    }


def _normalize_sampler_policy(raw: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    policy = _mapping(raw.get("sampler_policy") or raw.get("sampler"))
    steps = policy.get("steps", raw.get("steps"))
    cfg = policy.get("cfg", raw.get("cfg"))
    sampler_name = _slug(policy.get("sampler_name", policy.get("sampler", raw.get("sampler_name")))) or None
    scheduler = _slug(policy.get("scheduler", raw.get("scheduler"))) or None
    denoise = policy.get("denoise", raw.get("denoise"))
    thinking = policy.get("lanpaint_thinking_steps", policy.get("thinking_steps", raw.get("lanpaint_thinking_steps")))
    prompt_mode_raw = policy.get("prompt_mode", raw.get("lanpaint_prompt_mode"))
    prompt_mode_key = _slug(prompt_mode_raw)
    prompt_mode = _PROMPT_MODE_ALIASES.get(prompt_mode_key, prompt_mode_key or None)
    if prompt_mode and prompt_mode not in {"image_first", "prompt_first"}:
        issues.append({
            "level": "error",
            "field": "sampler_policy.prompt_mode",
            "message": "LanPaint prompt mode must be Image First or Prompt First.",
        })
        prompt_mode = None
    return {
        "steps": _optional_int(steps, field="sampler_policy.steps", issues=issues, minimum=1, maximum=1000),
        "cfg": _optional_float(cfg, field="sampler_policy.cfg", issues=issues, minimum=0.0, maximum=100.0),
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "denoise": _optional_float(denoise, field="sampler_policy.denoise", issues=issues, minimum=0.0, maximum=1.0),
        "lanpaint_thinking_steps": _optional_int(thinking, field="sampler_policy.lanpaint_thinking_steps", issues=issues, minimum=0, maximum=100),
        "prompt_mode": prompt_mode,
        "inpainting_mode": "image",
        "value_authority": _policy_value_source(steps, cfg, sampler_name, scheduler, denoise, thinking, prompt_mode_raw),
    }


def _normalize_lora_policy(raw: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    policy = _mapping(raw.get("lora_policy") or raw.get("lora"))
    support_state = _slug(policy.get("support_state", "family_policy")) or "family_policy"
    strategy = _slug(policy.get("injection_strategy", "family_policy")) or "family_policy"
    if support_state not in _ALLOWED_LORA_STATES:
        issues.append({
            "level": "error",
            "field": "lora_policy.support_state",
            "message": f"Unsupported LoRA support state: {support_state}.",
        })
        support_state = "family_policy"
    if strategy not in _ALLOWED_LORA_STRATEGIES:
        issues.append({
            "level": "error",
            "field": "lora_policy.injection_strategy",
            "message": f"Unsupported LoRA injection strategy: {strategy}.",
        })
        strategy = "family_policy"
    return {
        "stack_source": "neo.image.lora_stack",
        "support_state": support_state,
        "injection_strategy": strategy,
        "injection_point": "pre_sampler_model_transform",
        "allow_multiple": _as_bool(policy.get("allow_multiple"), True),
        "visible_prompt_mutation": False,
        "family_policy_required": support_state == "family_policy" or strategy == "family_policy",
    }


def _normalize_stitch_policy(raw: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(raw.get("stitch_policy") or raw.get("stitch"))
    resize_method = _slug(policy.get("resize_method")) or None
    return {
        "enabled": _as_bool(policy.get("enabled"), True),
        "restore_crop_size": _as_bool(policy.get("restore_crop_size"), True),
        "composite_into_source": _as_bool(policy.get("composite_into_source"), True),
        "preserve_source_dimensions": _as_bool(policy.get("preserve_source_dimensions"), True),
        "resize_method": resize_method,
        "value_authority": _policy_value_source(resize_method),
    }


def _unresolved_family_fields(contract: Mapping[str, Any]) -> list[str]:
    unresolved: list[str] = []
    crop = _mapping(contract.get("crop_policy"))
    size = _mapping(crop.get("processing_size"))
    mask = _mapping(contract.get("mask_policy"))
    sampling = _mapping(mask.get("sampling"))
    stitch_mask = _mapping(mask.get("stitch"))
    sampler = _mapping(contract.get("sampler_policy"))
    latent = _mapping(contract.get("latent_policy"))
    lora = _mapping(contract.get("lora_policy"))

    candidates = {
        "crop_policy.padding_px": crop.get("padding_px"),
        "crop_policy.processing_size.width": size.get("width"),
        "crop_policy.processing_size.height": size.get("height"),
        "crop_policy.resize_method": crop.get("resize_method"),
        "mask_policy.sampling.expand_px": sampling.get("expand_px"),
        "mask_policy.sampling.blur_radius": sampling.get("blur_radius"),
        "mask_policy.stitch.expand_px": stitch_mask.get("expand_px"),
        "mask_policy.stitch.blur_radius": stitch_mask.get("blur_radius"),
        "sampler_policy.steps": sampler.get("steps"),
        "sampler_policy.cfg": sampler.get("cfg"),
        "sampler_policy.sampler_name": sampler.get("sampler_name"),
        "sampler_policy.scheduler": sampler.get("scheduler"),
        "sampler_policy.denoise": sampler.get("denoise"),
        "sampler_policy.lanpaint_thinking_steps": sampler.get("lanpaint_thinking_steps"),
        "sampler_policy.prompt_mode": sampler.get("prompt_mode"),
    }
    unresolved.extend(field for field, value in candidates.items() if value is None)
    if latent.get("latent_format") == "family_policy":
        unresolved.append("latent_policy.latent_format")
    if latent.get("differential_diffusion") == "family_policy":
        unresolved.append("latent_policy.differential_diffusion")
    if lora.get("family_policy_required"):
        unresolved.extend(["lora_policy.support_state", "lora_policy.injection_strategy"])
    unresolved.extend(f"family_policy.{field}" for field in FAMILY_POLICY_OWNED_FIELDS)
    return sorted(set(unresolved))


def normalize_lanpaint_route_contract(raw: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize a provider-neutral LanPaint route-family request.

    Phase 1 deliberately stops at a contract-only representation. It does not
    resolve a family overlay, discover Comfy nodes, compile a workflow, or make
    a route selectable. Missing family-owned values remain explicit in
    ``validation.unresolved_family_policy_fields`` instead of borrowing defaults
    from Krea 2, Qwen, Z-Image, or another model family.
    """

    values = _mapping(raw)
    issues: list[dict[str, Any]] = []
    identity = _normalized_identity(values, issues)
    assets = _mapping(values.get("assets"))

    contract: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": "neo_app.image.lanpaint_route_contract",
        "identity": identity,
        "assets": {
            "source_image": _portable_asset_ref(assets.get("source_image", values.get("source_asset_id")), field="assets.source_image", issues=issues),
            "mask_image": _portable_asset_ref(assets.get("mask_image", values.get("mask_asset_id")), field="assets.mask_image", issues=issues),
            "reference_policy": "neo_owned_portable_identity_only",
        },
        "stage_order": list(BASE_STAGE_ORDER),
        "crop_policy": _normalize_crop_policy(values, issues),
        "mask_policy": _normalize_mask_policy(values, issues),
        "latent_policy": _normalize_latent_policy(values),
        "sampler_policy": _normalize_sampler_policy(values, issues),
        "lora_policy": _normalize_lora_policy(values, issues),
        "stitch_policy": _normalize_stitch_policy(values),
        "capability_requirements": {
            "required_stage_roles": list(BASE_REQUIRED_STAGE_ROLES),
            "optional_stage_roles": list(BASE_OPTIONAL_STAGE_ROLES),
            "required_node_classes": [],
            "required_model_roles": [],
            "family_policy_resolution_required": True,
        },
        "family_policy": {
            "policy_id": None,
            "owned_fields": list(FAMILY_POLICY_OWNED_FIELDS),
            "resolution_state": "unresolved",
        },
        "execution": {
            "enabled": False,
            "state": EXECUTION_STATE,
            "compiler_id": None,
            "workflow_type": None,
            "selectable": False,
            "reason": "Phase 1 defines and normalizes the route-family contract only; family policy and compiler phases are not enabled.",
        },
    }

    errors = [item for item in issues if item.get("level") == "error"]
    warnings = [item for item in issues if item.get("level") == "warning"]
    unresolved = _unresolved_family_fields(contract)
    contract["validation"] = {
        "ok": not errors,
        "errors": deepcopy(errors),
        "warnings": deepcopy(warnings),
        "unresolved_family_policy_fields": unresolved,
        "execution_ready": False,
    }
    contract["contract_fingerprint"] = lanpaint_contract_fingerprint(contract)
    return contract, deepcopy(issues)


def lanpaint_route_contract_template(
    *,
    provider_id: Any,
    family: Any,
    loader: Any,
    variant: Any = "default",
) -> dict[str, Any]:
    contract, _ = normalize_lanpaint_route_contract(
        {
            "identity": {
                "provider_id": provider_id,
                "family": family,
                "loader": loader,
                "mode": MODE_ID,
                "engine": ENGINE_ID,
                "variant": variant,
            }
        }
    )
    return contract


def validate_lanpaint_route_contract(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    contract, issues = normalize_lanpaint_route_contract(raw)
    return {
        "schema_id": "neo.image.lanpaint_route_family_contract_validation.v1",
        "ok": contract["validation"]["ok"],
        "issues": issues,
        "route_key": contract["identity"]["route_key"],
        "execution_ready": False,
        "contract_fingerprint": contract["contract_fingerprint"],
    }
