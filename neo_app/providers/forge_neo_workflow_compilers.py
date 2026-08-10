from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from neo_app.models.forge_neo_route_catalog import resolve_forge_route
from neo_app.providers.forge_neo_model_classification import ensure_forge_live_discovery
from neo_app.providers.forge_neo_outpaint import compile_forge_outpaint_canvas
from neo_app.providers.schema import NeoJob

FORGE_WORKFLOW_COMPILER_SCHEMA_ID = "neo.provider.forge_workflow_compilers.v1"
FORGE_WORKFLOW_COMPILER_VERSION = "1.1.0"

FORGE_WORKFLOW_COMPILER_IDS = frozenset(
    {
        "forge.sdapi_checkpoint",
        "forge.sdapi_modern_txt2img",
        "forge.sdapi_modern_img2img",
        "forge.sdapi_qwen_edit",
        "forge.sdapi_outpaint",
    }
)

_PROVIDER_DEFAULT_VALUES = {"", "provider_default", "automatic", "auto", "none", "null"}


@dataclass(frozen=True)
class ForgeCompilerSpec:
    compiler_id: str
    endpoint: str
    modes: tuple[str, ...]
    families: tuple[str, ...]
    purpose: str


_COMPILER_SPECS: tuple[ForgeCompilerSpec, ...] = (
    ForgeCompilerSpec(
        compiler_id="forge.sdapi_checkpoint",
        endpoint="dynamic",
        modes=("txt2img", "img2img", "inpaint"),
        families=("sd15", "sdxl"),
        purpose="Classic SD checkpoint generation through Forge SDAPI.",
    ),
    ForgeCompilerSpec(
        compiler_id="forge.sdapi_outpaint",
        endpoint="/sdapi/v1/img2img",
        modes=("outpaint",),
        families=("sd15", "sdxl"),
        purpose="Neo-owned canvas expansion and mask compilation followed by Forge img2img.",
    ),
    ForgeCompilerSpec(
        compiler_id="forge.sdapi_modern_txt2img",
        endpoint="/sdapi/v1/txt2img",
        modes=("txt2img",),
        families=("flux", "flux2_klein", "krea2", "krea2_turbo", "qwen_image", "z_image", "z_image_turbo"),
        purpose="Provider-native txt2img compiler for translated modern model bundles.",
    ),
    ForgeCompilerSpec(
        compiler_id="forge.sdapi_modern_img2img",
        endpoint="/sdapi/v1/img2img",
        modes=("img2img",),
        families=("flux", "flux2_klein"),
        purpose="Provider-native img2img compiler for verified modern model families.",
    ),
    ForgeCompilerSpec(
        compiler_id="forge.sdapi_qwen_edit",
        endpoint="/sdapi/v1/img2img",
        modes=("img2img", "edit"),
        families=("qwen_image_edit_2509",),
        purpose="Single-source Qwen Image Edit compiler; multi-image API submission remains fail-closed.",
    ),
)

_FAMILY_DEFAULTS: dict[str, dict[str, Any]] = {
    "sd15": {"steps": 20, "cfg_scale": 7.0, "sampler_name": "Euler", "scheduler": "Automatic"},
    "sdxl": {"steps": 20, "cfg_scale": 7.0, "sampler_name": "Euler", "scheduler": "Automatic"},
    "flux": {"steps": 20, "cfg_scale": 1.0, "flux_guidance": 3.5, "sampler_name": "Euler", "scheduler": "Simple"},
    "flux2_klein": {"steps": 4, "cfg_scale": 1.0, "flux_guidance": 1.0, "sampler_name": "Euler", "scheduler": "Beta"},
    "krea2": {"steps": 52, "cfg_scale": 3.5, "sampler_name": "Euler", "scheduler": "Beta"},
    "krea2_turbo": {"steps": 8, "cfg_scale": 1.0, "sampler_name": "Euler", "scheduler": "Beta"},
    "qwen_image": {"steps": 50, "cfg_scale": 4.0, "sampler_name": "Euler", "scheduler": "Beta"},
    "qwen_image_edit_2509": {"steps": 40, "cfg_scale": 4.0, "sampler_name": "Euler", "scheduler": "Beta", "denoising_strength": 1.0},
    "z_image": {"steps": 35, "cfg_scale": 3.5, "sampler_name": "Euler", "scheduler": "Beta"},
    "z_image_turbo": {"steps": 9, "cfg_scale": 1.0, "sampler_name": "Euler", "scheduler": "Beta"},
}


def _normalized_mode(value: Any) -> str:
    mode = str(value or "txt2img").strip().casefold() or "txt2img"
    return {"generate": "txt2img", "image_to_image": "img2img"}.get(mode, mode)


def _selected(value: Any) -> bool:
    return str(value or "").strip().casefold() not in _PROVIDER_DEFAULT_VALUES


def _first(params: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in params and params.get(key) is not None:
            return params.get(key)
    return default


def _float_value(
    value: Any,
    default: float,
    blockers: list[str],
    blocker_id: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        blockers.append(blocker_id)
        resolved = float(default)
    if minimum is not None and resolved < minimum:
        blockers.append(blocker_id)
    if maximum is not None and resolved > maximum:
        blockers.append(blocker_id)
    return resolved


def _int_value(
    value: Any,
    default: int,
    blockers: list[str],
    blocker_id: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        blockers.append(blocker_id)
        resolved = int(default)
    if minimum is not None and resolved < minimum:
        blockers.append(blocker_id)
    if maximum is not None and resolved > maximum:
        blockers.append(blocker_id)
    return resolved


def _bool_value(value: Any, default: bool, blockers: list[str], blocker_id: str) -> bool:
    if value is None or value == "":
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    blockers.append(blocker_id)
    return bool(default)


def _effective_params(job: NeoJob) -> tuple[dict[str, Any], list[str]]:
    params = dict(job.params or {})
    family = str(job.family or "sdxl").strip() or "sdxl"
    defaults = _FAMILY_DEFAULTS.get(family, _FAMILY_DEFAULTS["sdxl"])
    applied: list[str] = []
    for key, value in defaults.items():
        aliases = {
            "sampler_name": ("sampler_name", "sampler"),
            "scheduler": ("scheduler", "scheduler_name"),
            "cfg_scale": ("cfg_scale", "cfg"),
            "denoising_strength": ("denoising_strength", "denoise"),
            "flux_guidance": ("flux_guidance",),
        }.get(key, (key,))
        existing = _first(params, *aliases)
        if not _selected(existing):
            params[key] = value
            applied.append(key)
    return params, applied


def _catalog_names(snapshot: dict[str, Any] | None, key: str) -> list[str]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    values = snapshot.get(key)
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        if isinstance(value, dict):
            name = value.get("name") or value.get("label") or value.get("id")
        else:
            name = value
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _canonical_catalog_value(requested: Any, available: list[str]) -> str:
    text = str(requested or "").strip()
    if not text or not available:
        return text
    folded = text.casefold()
    for item in available:
        if item.casefold() == folded:
            return item
    return text


def _source_values(params: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    primary = _first(params, "source_image", "source_image_path", "init_image")
    if _selected(primary):
        values.append(primary)
    explicit = params.get("source_images")
    if isinstance(explicit, list):
        for value in explicit:
            if _selected(value) and value not in values:
                values.append(value)
    for index in range(2, 9):
        value = _first(params, f"source_image_{index}", f"source_image_path_{index}", f"init_image_{index}")
        if _selected(value) and value not in values:
            values.append(value)
    return values


def _stitch_reference_values(params: dict[str, Any]) -> list[Any]:
    block = params.get("qwen_stitch") if isinstance(params.get("qwen_stitch"), dict) else params.get("image_stitch")
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return []
    values: list[Any] = []
    for group in block.get("groups") or []:
        if not isinstance(group, dict) or group.get("enabled") is False:
            continue
        inputs = group.get("inputs") if isinstance(group.get("inputs"), dict) else {}
        for key in ("image_a", "image_b"):
            value = inputs.get(key)
            if isinstance(value, dict):
                value = value.get("path") or value.get("ref") or value.get("url") or value.get("name")
            if _selected(value) and value not in values:
                values.append(value)
    return values


def _image_stitch_capability(snapshot: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    extensions = snapshot.get("extension_capabilities") if isinstance(snapshot.get("extension_capabilities"), dict) else {}
    detail = extensions.get("image_stitch") if isinstance(extensions.get("image_stitch"), dict) else {}
    modes = {str(item) for item in detail.get("available_modes") or []}
    if not detail.get("available") or (modes and mode not in modes):
        return {}
    return detail


def _setting_requirement_blockers(route: Any, snapshot: dict[str, Any] | None) -> list[str]:
    if not route.required_settings:
        return []
    classification, _intersection = ensure_forge_live_discovery(snapshot or {})
    settings = classification.get("setting_capabilities") if isinstance(classification.get("setting_capabilities"), dict) else {}
    blockers: list[str] = []
    for capability_id in route.required_settings:
        detail = settings.get(capability_id) if isinstance(settings.get(capability_id), dict) else {}
        if not detail.get("available"):
            blockers.append(f"required_setting_not_discovered:{capability_id}")
        elif not detail.get("enabled"):
            blockers.append(f"required_setting_disabled:{capability_id}")
    return blockers


def forge_workflow_compiler_contract_payload() -> dict[str, Any]:
    return {
        "schema_id": FORGE_WORKFLOW_COMPILER_SCHEMA_ID,
        "version": FORGE_WORKFLOW_COMPILER_VERSION,
        "provider_id": "forge",
        "compiler_ids": sorted(FORGE_WORKFLOW_COMPILER_IDS),
        "compilers": [asdict(item) for item in _COMPILER_SPECS],
        "family_defaults": {key: dict(value) for key, value in sorted(_FAMILY_DEFAULTS.items())},
        "policy": {
            "route_authority_selects_compiler": True,
            "translated_bundle_does_not_bypass_route_state": True,
            "modern_families_use_provider_native_sdapi_payloads": True,
            "flux_guidance_maps_to_distilled_cfg_scale": True,
            "multi_image_edit_requires_verified_image_stitch_contract": True,
            "outpaint_canvas_and_mask_are_neo_owned": True,
            "unsupported_modes_fail_closed": True,
        },
    }


def compile_forge_workflow_plan(
    job: NeoJob,
    *,
    loader_translation: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    image_encoder: Any | None = None,
) -> dict[str, Any]:
    """Compile route-specific controls before the final SDAPI payload is built."""

    family = str(job.family or "sdxl").strip() or "sdxl"
    loader = str(job.loader or "checkpoint").strip() or "checkpoint"
    mode = _normalized_mode(job.mode)
    route = resolve_forge_route(family, loader, mode)
    compiler_id = str(route.compiler_id or "")
    blockers: list[str] = []
    warnings: list[str] = []

    if route.state not in {"available", "experimental_available"}:
        blockers.append(f"authority_state:{route.state}")
    if compiler_id not in FORGE_WORKFLOW_COMPILER_IDS:
        blockers.append(f"unsupported_forge_compiler:{compiler_id or 'missing'}")
    if not loader_translation.get("bundle_ready"):
        blockers.extend(str(item) for item in loader_translation.get("bundle_blockers") or [])
    blockers.extend(_setting_requirement_blockers(route, snapshot))

    params, defaults_applied = _effective_params(job)
    params["sampler_name"] = _canonical_catalog_value(
        _first(params, "sampler_name", "sampler", default="Euler"),
        _catalog_names(snapshot, "samplers"),
    )
    params["scheduler"] = _canonical_catalog_value(
        _first(params, "scheduler", "scheduler_name", default="Automatic"),
        _catalog_names(snapshot, "schedulers"),
    )

    endpoint = str(route.endpoint or ("/sdapi/v1/txt2img" if mode == "txt2img" else "/sdapi/v1/img2img"))
    payload_updates: dict[str, Any] = {}
    init_images: list[str] = []
    mask = ""
    outpaint: dict[str, Any] | None = None

    if family in {"flux", "flux2_klein"}:
        guidance = _first(params, "flux_guidance", default=(loader_translation.get("generation_parameters") or {}).get("flux_guidance"))
        if _selected(guidance):
            try:
                payload_updates["distilled_cfg_scale"] = float(guidance)
            except (TypeError, ValueError):
                blockers.append("invalid_flux_guidance")

    source_values = _source_values(params)
    if mode in {"img2img", "inpaint", "outpaint", "edit"}:
        if not source_values:
            blockers.append("missing_source_image")
        elif image_encoder is None:
            blockers.append("missing_image_encoder")
        else:
            try:
                init_images = [image_encoder(source_values[0], label="Source image")]
            except Exception as exc:  # noqa: BLE001 - normalize provider compilation errors.
                blockers.append(f"source_image_error:{exc}")

    stitch_references = [*source_values[1:], *_stitch_reference_values(params)]
    stitch_supported_route = bool(
        (compiler_id == "forge.sdapi_qwen_edit" and mode in {"img2img", "edit"})
        or (family == "flux2_klein" and compiler_id == "forge.sdapi_modern_img2img" and mode == "img2img")
    )
    stitch_metadata: dict[str, Any] | None = None
    if stitch_references:
        if not stitch_supported_route:
            blockers.append("image_stitch_not_supported_for_route")
        elif image_encoder is None:
            blockers.append("missing_image_encoder")
        else:
            stitch_capability = _image_stitch_capability(snapshot, "img2img")
            if not stitch_capability:
                blockers.append("image_stitch_contract_not_verified")
            else:
                encoded_references: list[str] = []
                for index, reference in enumerate(stitch_references, start=1):
                    try:
                        encoded_references.append(image_encoder(reference, label=f"ImageStitch reference {index}"))
                    except Exception as exc:  # noqa: BLE001
                        blockers.append(f"image_stitch_reference_error:{exc}")
                        break
                if encoded_references and not any(item.startswith("image_stitch_reference_error:") for item in blockers):
                    max_side = _int_value(
                        _first(params, "forge_image_stitch_max_dim", "image_stitch_max_dim", default=stitch_capability.get("default_max_side", 1024)),
                        int(stitch_capability.get("default_max_side") or 1024),
                        blockers,
                        "invalid_image_stitch_max_dim",
                        minimum=0,
                        maximum=2048,
                    )
                    script_name = str(stitch_capability.get("script_name") or "ImageStitch Integrated")
                    payload_updates.setdefault("alwayson_scripts", {})[script_name] = {"args": [True, encoded_references, max_side]}
                    stitch_metadata = {
                        "contract": str(stitch_capability.get("contract") or "forge.image_stitch.integrated.v1"),
                        "script_name": script_name,
                        "reference_count": len(encoded_references),
                        "max_side": max_side,
                    }

    if mode == "inpaint":
        raw_mask = _first(params, "mask_image", "mask_image_path", "inpaint_mask")
        if not _selected(raw_mask):
            blockers.append("missing_mask_image")
        elif image_encoder is None:
            blockers.append("missing_image_encoder")
        else:
            try:
                mask = image_encoder(raw_mask, label="Mask image")
            except Exception as exc:  # noqa: BLE001
                blockers.append(f"mask_image_error:{exc}")

    if compiler_id == "forge.sdapi_outpaint":
        raw_source = source_values[0] if source_values else None
        if raw_source is not None:
            try:
                outpaint = compile_forge_outpaint_canvas(
                    raw_source,
                    params,
                    resolution_step=_int_value(
                        _first(params, "forge_resolution_step", "resolution_step", default=64),
                        64,
                        blockers,
                        "invalid_resolution_step",
                        minimum=8,
                        maximum=512,
                    ),
                )
                init_images = [str(outpaint["init_image"])]
                mask = str(outpaint["mask"])
                params["width"] = int((outpaint.get("canvas") or {}).get("width") or 0)
                params["height"] = int((outpaint.get("canvas") or {}).get("height") or 0)
            except Exception as exc:  # noqa: BLE001
                blockers.append(f"outpaint_canvas_error:{exc}")

    if mode in {"img2img", "inpaint", "outpaint", "edit"}:
        payload_updates.update(
            {
                "denoising_strength": _float_value(
                    _first(params, "denoising_strength", "denoise", default=0.75),
                    0.75,
                    blockers,
                    "invalid_denoising_strength",
                    minimum=0.0,
                    maximum=1.0,
                ),
                "resize_mode": _int_value(
                    _first(params, "resize_mode", default=0),
                    0,
                    blockers,
                    "invalid_resize_mode",
                    minimum=0,
                ),
                "include_init_images": False,
            }
        )
    if mask:
        payload_updates["mask"] = mask
    if init_images:
        payload_updates["init_images"] = init_images

    if mode in {"inpaint", "outpaint"}:
        selection_target = str(_first(params, "inpaint_selection_target", default="masked_area") or "masked_area").strip().casefold()
        explicit_invert = _first(params, "inpainting_mask_invert")
        if explicit_invert in {None, ""}:
            invert = 1 if selection_target in {"not_masked_area", "unmasked_area", "outside_mask"} else 0
        else:
            invert = _int_value(
                explicit_invert,
                0,
                blockers,
                "invalid_inpainting_mask_invert",
                minimum=0,
                maximum=1,
            )
        payload_updates.update(
            {
                "mask_blur": _int_value(
                    _first(params, "mask_blur", default=4),
                    4,
                    blockers,
                    "invalid_mask_blur",
                    minimum=0,
                ),
                "inpainting_fill": _int_value(
                    _first(params, "inpainting_fill", default=1),
                    1,
                    blockers,
                    "invalid_inpainting_fill",
                    minimum=0,
                ),
                "inpaint_full_res": _bool_value(
                    _first(params, "inpaint_full_res", default=True),
                    True,
                    blockers,
                    "invalid_inpaint_full_res",
                ),
                "inpaint_full_res_padding": _int_value(
                    _first(params, "inpaint_full_res_padding", default=32),
                    32,
                    blockers,
                    "invalid_inpaint_full_res_padding",
                    minimum=0,
                ),
                "inpainting_mask_invert": invert,
            }
        )

    return {
        "schema_id": FORGE_WORKFLOW_COMPILER_SCHEMA_ID,
        "version": FORGE_WORKFLOW_COMPILER_VERSION,
        "provider_id": "forge",
        "family": family,
        "loader": loader,
        "mode": mode,
        "architecture_id": route.architecture_id,
        "compiler_id": compiler_id,
        "workflow_type": route.workflow_type,
        "parameter_profile": route.parameter_profile,
        "endpoint": endpoint,
        "effective_params": params,
        "defaults_applied": defaults_applied,
        "payload_updates": payload_updates,
        "outpaint": outpaint,
        "builtin_features": {"image_stitch": stitch_metadata} if stitch_metadata else {},
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "executable": not blockers,
        "policy": {
            "authority_state_required": True,
            "loader_bundle_required": True,
            "provider_defaults_do_not_override_explicit_values": True,
            "single_source_qwen_edit_only": False,
            "multi_image_edit_requires_verified_image_stitch": compiler_id == "forge.sdapi_qwen_edit",
            "outpaint_owned_by_neo": compiler_id == "forge.sdapi_outpaint",
        },
    }
