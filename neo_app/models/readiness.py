from __future__ import annotations

from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.extensions.registry import check_extension_compatibility, get_extension_records
from neo_app.models.asset_selection import is_explicit_asset_selection
from neo_app.models.registry import (
    MODE_BLOCKING_STATUSES,
    check_model_family_compatibility,
    get_family,
    resolve_loader_contract,
    resolve_parameter_profile,
)
from neo_app.models.schema import ReadinessValidationRequest, ReadinessValidationResult
from neo_app.providers.registry import get_provider_backend_capabilities

ROUTE_GATES = {
    "provider_route",
    "provider_outpaint_route",
    "flux_fill_or_variant_route",
    "flux_fill_or_experimental_route",
    "variant_edit_route",
    "variant_inpaint_route",
    "variant_outpaint_route",
    "video_surface_route",
}

ASSET_ALIASES = {
    "checkpoint": ["checkpoint", "ckpt_name", "model"],
    "diffusion_model": ["diffusion_model", "unet", "model", "model_name"],
    "unet": ["unet", "diffusion_model", "model"],
    "gguf_unet": ["gguf_unet", "unet", "model", "gguf_model"],
    "text_encoder_primary": ["text_encoder_primary", "text_encoder_1", "clip", "clip_name", "qwen_text_encoder", "qwen3_text_encoder"],
    "text_encoder_secondary": ["text_encoder_secondary", "text_encoder_2", "clip_2", "clip_name2"],
    "gguf_text_encoder_primary": ["gguf_text_encoder_primary", "text_encoder_primary", "text_encoder_1", "qwen_text_encoder", "qwen3_text_encoder"],
    "gguf_text_encoder_secondary": ["gguf_text_encoder_secondary", "text_encoder_secondary", "text_encoder_2"],
    "qwen_text_encoder": ["qwen_text_encoder", "text_encoder_primary", "gguf_text_encoder_primary", "text_encoder_1"],
    "qwen3_text_encoder": ["qwen3_text_encoder", "text_encoder_primary", "gguf_text_encoder_primary", "text_encoder_1"],
    "text_encoder_if_required": ["text_encoder_if_required", "text_encoder_primary", "text_encoder_1", "clip", "clip_name", "qwen_text_encoder", "qwen3_text_encoder"],
    "vae_if_required": ["vae_if_required", "vae", "vae_or_ae", "ae_or_vae"],
    "vae": ["vae", "vae_or_ae", "ae_or_vae", "gguf_vae", "gguf_vae_optional"],
    "vae_or_ae": ["vae_or_ae", "vae", "ae_or_vae", "gguf_vae", "gguf_vae_optional"],
    "ae_or_vae": ["ae_or_vae", "vae", "vae_or_ae", "gguf_vae", "gguf_vae_optional"],
    "wan_model": ["wan_model", "diffusion_model", "gguf_unet", "model"],
    "umt5_text_encoder": ["umt5_text_encoder", "text_encoder_primary"],
    "wan_vae": ["wan_vae", "vae", "vae_or_ae"],
    "hunyuan_variant": ["hunyuan_variant", "variant"],
    "hidream_variant": ["hidream_variant", "variant"],
    "api_model": ["api_model", "model"],
    "qwen_mmproj": ["qwen_mmproj", "mmproj"],
}

OPTIONAL_SUFFIXES = ("_optional", "_if_required")


EXPLICIT_COMPONENT_ASSET_ROLES = {
    "diffusion_model",
    "unet",
    "text_encoder_primary",
    "text_encoder_secondary",
    "qwen_text_encoder",
    "qwen3_text_encoder",
    "text_encoder_if_required",
    "vae_if_required",
    "vae",
    "vae_or_ae",
    "ae_or_vae",
}


def _requires_explicit_component_asset(request: ReadinessValidationRequest, role_id: str) -> bool:
    return request.loader in {"diffusion_model", "unet"} and role_id in EXPLICIT_COMPONENT_ASSET_ROLES


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _selected_assets(request: ReadinessValidationRequest) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    assets.update(request.selected_assets or {})
    for key in ("assets", "asset_values", "selected_assets"):
        assets.update(_as_mapping(request.params.get(key)))
    for key, value in request.params.items():
        if key not in assets:
            assets[key] = value
    return assets


def _has_selected_asset(role_id: str, assets: dict[str, Any]) -> bool:
    keys = [role_id, *ASSET_ALIASES.get(role_id, [])]
    for key in dict.fromkeys(keys):
        value = assets.get(key)
        if isinstance(value, str):
            if is_explicit_asset_selection(value):
                return True
        elif _truthy(value):
            return True
    return False


def _loader_capability(request: ReadinessValidationRequest) -> dict[str, Any]:
    caps = request.backend_capabilities or {}
    if not caps and request.provider_id:
        caps = get_provider_backend_capabilities(request.provider_id)
    return caps or {}


def _loader_payload(backend_capabilities: dict[str, Any], loader: str | None) -> dict[str, Any]:
    if not loader:
        return {}
    return _as_mapping(_as_mapping(backend_capabilities.get("loaders")).get(loader))


def _role_payload(backend_capabilities: dict[str, Any], loader: str | None, role_id: str) -> dict[str, Any]:
    loader_payload = _loader_payload(backend_capabilities, loader)
    roles = _as_mapping(loader_payload.get("roles"))
    for candidate in [role_id, *ASSET_ALIASES.get(role_id, [])]:
        payload = _as_mapping(roles.get(candidate))
        if payload:
            return payload
    return {}


def _role_available(backend_capabilities: dict[str, Any], loader: str | None, role_id: str, assets: dict[str, Any]) -> bool:
    if _has_selected_asset(role_id, assets):
        return True
    role_payload = _role_payload(backend_capabilities, loader, role_id)
    if role_payload.get("available") is True:
        return True
    role_assets = _as_mapping(role_payload.get("assets"))
    return any(_as_list(values) for values in role_assets.values())


def _loader_available(backend_capabilities: dict[str, Any], loader: str | None) -> bool:
    if not loader:
        return False
    loader_payload = _loader_payload(backend_capabilities, loader)
    if not loader_payload:
        return False
    return loader_payload.get("available") is True


def _has_source_image(request: ReadinessValidationRequest, assets: dict[str, Any]) -> bool:
    keys = ["source_image", "init_image", "image", "input_image"]
    return any(_truthy(request.params.get(key)) or _truthy(assets.get(key)) for key in keys)


def _has_mask_image(request: ReadinessValidationRequest, assets: dict[str, Any]) -> bool:
    keys = ["mask_image", "mask", "inpaint_mask"]
    return any(_truthy(request.params.get(key)) or _truthy(assets.get(key)) for key in keys)


def _has_outpaint_padding(request: ReadinessValidationRequest) -> bool:
    padding = _as_mapping(request.params.get("outpaint_padding") or request.params.get("padding"))
    if padding:
        return any(int(padding.get(side) or 0) > 0 for side in ("left", "right", "top", "bottom"))
    return any(int(request.params.get(key) or 0) > 0 for key in ("pad_left", "pad_right", "pad_top", "pad_bottom", "left", "right", "top", "bottom"))


def _has_route_flag(request: ReadinessValidationRequest, gate: str) -> bool:
    routes = _as_mapping(request.params.get("routes"))
    route_flags = _as_mapping(request.params.get("route_flags"))
    return _truthy(request.params.get(gate)) or _truthy(routes.get(gate)) or _truthy(route_flags.get(gate))


def _gate_ready(gate: str, request: ReadinessValidationRequest, backend_capabilities: dict[str, Any], assets: dict[str, Any]) -> tuple[bool, str | None]:
    loader = request.loader
    if gate == "source_image":
        return _has_source_image(request, assets), "Missing source image."
    if gate == "mask_image":
        return _has_mask_image(request, assets), "Missing mask image."
    if gate == "outpaint_padding":
        return _has_outpaint_padding(request), "Missing outpaint padding."
    if gate == "qwen_mmproj":
        return _role_available(backend_capabilities, loader, "qwen_mmproj", assets), "Missing Qwen mmproj."
    if gate == "dual_text_encoders":
        primary = _role_available(backend_capabilities, loader, "text_encoder_primary", assets) or _role_available(backend_capabilities, loader, "gguf_text_encoder_primary", assets)
        variant = str(request.params.get("flux_variant") or request.params.get("variant") or assets.get("flux_variant") or "").strip().lower().replace(" ", "_").replace("-", "_")
        if (request.family == "flux2_klein") or (request.family == "flux" and variant in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}):
            return primary, "Missing Qwen3 text encoder for FLUX.2 [klein]."
        secondary = _role_available(backend_capabilities, loader, "text_encoder_secondary", assets) or _role_available(backend_capabilities, loader, "gguf_text_encoder_secondary", assets)
        return primary and secondary, "Missing dual text encoders."
    if gate == "flux_guidance":
        has_value = _truthy(request.params.get("flux_guidance")) or _truthy(assets.get("flux_guidance"))
        has_backend = _role_available(backend_capabilities, loader, "flux_guidance", assets)
        return has_value and has_backend, "Missing Flux guidance value or backend support."
    if gate in {"model", "qwen_text_encoder", "qwen3_text_encoder", "ae_or_vae", "vae_or_ae", "variant", "wan_task", "edit_instruction"}:
        if gate == "model":
            if loader in {"diffusion_model", "unet"}:
                return _has_selected_asset("diffusion_model", assets) or _has_selected_asset("unet", assets), "Select an installed diffusion model."
            return any(_role_available(backend_capabilities, loader, role, assets) for role in ("checkpoint", "diffusion_model", "unet", "gguf_unet", "api_model", "wan_model")), "Missing model asset."
        if gate == "variant":
            return (
                _truthy(request.params.get("variant"))
                or _truthy(assets.get("variant"))
                or _has_selected_asset("hunyuan_variant", assets)
                or _has_selected_asset("hidream_variant", assets)
            ), "Missing selected variant."
        if gate == "wan_task":
            return _truthy(request.params.get("wan_task")), "Missing Wan task."
        if gate == "edit_instruction":
            return _truthy(request.params.get("edit_instruction")) or _truthy(request.params.get("prompt")), "Missing edit instruction."
        if _requires_explicit_component_asset(request, gate):
            return _has_selected_asset(gate, assets), f"Select an installed {gate.replace('_', ' ')}."
        return _role_available(backend_capabilities, loader, gate, assets), f"Missing {gate}."
    if gate in ROUTE_GATES:
        return _has_route_flag(request, gate), f"Missing provider/variant route flag: {gate}."
    return _has_selected_asset(gate, assets) or _role_available(backend_capabilities, loader, gate, assets) or _has_route_flag(request, gate), f"Missing readiness gate: {gate}."


def _check_required_roles(
    request: ReadinessValidationRequest,
    required_roles: list[str],
    backend_capabilities: dict[str, Any],
    assets: dict[str, Any],
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    satisfied: list[str] = []
    for role in required_roles:
        if role.endswith(OPTIONAL_SUFFIXES) or role in {"mmproj_optional", "turbo_mode_optional", "provider_prompt_encoder", "provider_qwen_image_model", "provider_z_image_model", "provider_wan_model", "provider_hunyuan_model", "provider_hidream_model"}:
            continue
        ready = (
            _has_selected_asset(role, assets)
            if _requires_explicit_component_asset(request, role)
            else _role_available(backend_capabilities, request.loader, role, assets)
        )
        if ready:
            satisfied.append(role)
        else:
            blockers.append(f"Missing required loader role: {role}.")
    return blockers, satisfied


def _extension_profile_payload(request: ReadinessValidationRequest) -> dict[str, Any]:
    if not request.loader:
        return {"ok": True, "results": [], "matched_profiles": []}
    subtab = "generate" if request.mode == "txt2img" else request.mode
    extension_ids = list(request.extension_ids)
    if not extension_ids and request.loader == "gguf":
        extension_ids = ["image.gguf_loader"]
    elif not extension_ids:
        return {"ok": True, "results": [], "matched_profiles": []}
    result = check_extension_compatibility({
        "surface": request.surface,
        "subtab": subtab,
        "provider_id": request.provider_id or request.backend,
        "family": request.family,
        "loader": request.loader,
        "extension_ids": extension_ids,
    })
    matched: list[str] = []
    for item in result.get("results", []):
        matched.extend(item.get("capability_profiles", []))
    return {**result, "matched_profiles": list(dict.fromkeys(matched))}


def validate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    request = ReadinessValidationRequest(**payload)
    errors: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    satisfied: list[str] = []
    checks: dict[str, Any] = {}

    family = get_family(request.family)
    if family is None:
        result = ReadinessValidationResult(
            ok=False,
            ready=False,
            family=request.family,
            loader=request.loader,
            mode=request.mode,
            provider_id=request.provider_id,
            backend=request.backend,
            errors=[f"Unknown model family: {request.family}"],
        )
        return model_to_dict(result)

    compatibility = check_model_family_compatibility({
        "surface": request.surface,
        "mode": request.mode,
        "backend": request.backend or request.provider_id,
        "family": request.family,
        "loader": request.loader,
    })
    errors.extend(compatibility.get("errors", []))
    warnings.extend(compatibility.get("warnings", []))
    mode_status = compatibility.get("mode_status")

    if mode_status in MODE_BLOCKING_STATUSES:
        blockers.append(f"Mode {request.mode} is unsupported for family {request.family}.")

    backend_capabilities = _loader_capability(request)
    assets = _selected_assets(request)
    checks["backend_capabilities"] = {
        "provider_id": backend_capabilities.get("provider_id"),
        "backend": backend_capabilities.get("backend"),
        "discovery_status": backend_capabilities.get("discovery_status"),
        "reachable": backend_capabilities.get("reachable"),
        "object_info_available": backend_capabilities.get("object_info_available"),
    }

    if request.loader:
        if not _loader_available(backend_capabilities, request.loader):
            blockers.append(f"Backend does not report loader capability: {request.loader}.")
        else:
            satisfied.append(f"loader:{request.loader}")

    loader_resolution = resolve_loader_contract(family, request.loader, request.mode) if request.loader else None
    required_roles = list(loader_resolution.required_roles if loader_resolution else [])
    role_blockers, role_satisfied = _check_required_roles(request, required_roles, backend_capabilities, assets)
    blockers.extend(role_blockers)
    satisfied.extend(role_satisfied)
    checks["loader_contract"] = model_to_dict(loader_resolution) if loader_resolution else {}

    parameter_resolution = resolve_parameter_profile(family, request.loader or family.default_loader, request.mode)
    errors.extend(parameter_resolution.errors)
    warnings.extend(parameter_resolution.warnings)
    checks["parameter_profile"] = model_to_dict(parameter_resolution)

    gate_ids = list(dict.fromkeys([*compatibility.get("readiness_gates", []), *parameter_resolution.readiness_gates]))
    gate_results: dict[str, Any] = {}
    for gate in gate_ids:
        ready, message = _gate_ready(gate, request, backend_capabilities, assets)
        gate_results[gate] = {"ready": ready, "message": None if ready else message}
        if ready:
            satisfied.append(gate)
        elif message:
            blockers.append(message)

    checks["readiness_gates"] = gate_results

    extension_result = _extension_profile_payload(request)
    checks["extensions"] = extension_result
    extension_errors: list[str] = []
    extension_warnings: list[str] = []
    for item in extension_result.get("results", []):
        extension_errors.extend(item.get("errors", []))
        extension_warnings.extend(item.get("warnings", []))
        if not item.get("enabled", True):
            blockers.append(f"Required extension {item.get('extension_id')} is disabled.")
    warnings.extend(extension_warnings)
    if request.loader == "gguf" and not extension_result.get("matched_profiles"):
        blockers.append("No GGUF extension capability profile matched this family/loader/mode.")
    if extension_errors:
        blockers.extend(extension_errors)

    blockers = list(dict.fromkeys(blockers))
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    satisfied = list(dict.fromkeys(satisfied))

    result = ReadinessValidationResult(
        ok=not errors,
        ready=not errors and not blockers,
        family=request.family,
        loader=request.loader,
        mode=request.mode,
        provider_id=request.provider_id,
        backend=request.backend,
        mode_status=mode_status,
        required_roles=required_roles,
        readiness_gates=gate_ids,
        satisfied=satisfied,
        blockers=blockers,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )
    return model_to_dict(result)
