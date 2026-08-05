from __future__ import annotations

from typing import Any

from neo_app.models.forge_neo_route_catalog import resolve_forge_route

FORGE_UX_GATING_SCHEMA_ID = "neo.provider.forge_ux_gating.v1"
FORGE_UX_GATING_VERSION = "1.1.0"

_MODE_ORDER = ("txt2img", "img2img", "inpaint", "outpaint", "edit")
_CLASSIC_FAMILIES = {"sd15", "sdxl"}


def forge_route_key(family: str, loader: str, mode: str) -> str:
    return f"{str(family or '').strip()}::{str(loader or '').strip()}::{str(mode or '').strip()}"


def _route_field_policy(family: str, mode: str) -> dict[str, dict[str, Any]]:
    image_conditioned = mode in {"img2img", "inpaint", "outpaint", "edit"}
    masked = mode in {"inpaint", "outpaint"}
    return {
        "source_image": {
            "visible": image_conditioned,
            "enabled": image_conditioned,
            "reason": "The active Forge workflow consumes a source image." if image_conditioned else "The active Forge txt2img workflow does not consume a source image.",
        },
        "mask_image": {
            "visible": mode == "inpaint",
            "enabled": mode == "inpaint",
            "reason": "The active Forge inpaint workflow consumes an explicit mask." if mode == "inpaint" else "This Forge workflow has no explicit mask input.",
        },
        "denoise": {
            "visible": image_conditioned,
            "enabled": image_conditioned,
            "reason": "Mapped to Forge denoising_strength." if image_conditioned else "Denoise is not part of txt2img.",
        },
        "clip_skip": {
            "visible": family in _CLASSIC_FAMILIES,
            "enabled": family in _CLASSIC_FAMILIES,
            "reason": "Mapped to CLIP_stop_at_last_layers for classic SD routes." if family in _CLASSIC_FAMILIES else "Clip skip is hidden for modern Forge model families.",
        },
        "flux_guidance": {
            "visible": family in {"flux", "flux2_klein"},
            "enabled": family in {"flux", "flux2_klein"},
            "reason": "Mapped to distilled_cfg_scale." if family in {"flux", "flux2_klein"} else "Flux guidance is not used by this family.",
        },
        "outpaint_padding": {
            "visible": mode == "outpaint",
            "enabled": mode == "outpaint",
            "reason": "Neo expands the source canvas for the active Forge outpaint route." if mode == "outpaint" else "Outpaint padding is hidden outside outpaint.",
        },
        "mask_blur": {
            "visible": masked,
            "enabled": masked,
            "reason": "Mapped to Forge mask_blur." if masked else "Mask blur is hidden outside masked workflows.",
        },
        "mask_grow": {
            "visible": False,
            "enabled": False,
            "reason": "Forge has no verified mapping for Neo's Comfy mask-grow field.",
        },
        "inpaint_selection_target": {
            "visible": masked,
            "enabled": masked,
            "reason": "Mapped to Forge inpainting_mask_invert." if masked else "Selection target is hidden outside masked workflows.",
        },
        "inpaint_context_mode": {
            "visible": False,
            "enabled": False,
            "reason": "Comfy latent context modes do not map to Forge SDAPI.",
        },
        "latent_capture_mode": {
            "visible": False,
            "enabled": False,
            "reason": "Forge has no verified latent-artifact export contract.",
        },
        "gguf_clip_mode": {
            "visible": False,
            "enabled": False,
            "reason": "Forge owns encoder/module layout through the selected model bundle; Comfy GGUF layout controls do not apply.",
        },
        "gguf_clip_type": {
            "visible": False,
            "enabled": False,
            "reason": "Forge owns encoder/module type resolution through the selected model bundle.",
        },
    }


def _route_control_policy(family: str, mode: str, *, image_stitch_available: bool = False) -> dict[str, Any]:
    image_conditioned = mode in {"img2img", "inpaint", "outpaint", "edit"}
    masked = mode in {"inpaint", "outpaint"}
    stitch_route = bool(image_stitch_available and ((family == "qwen_image_edit_2509" and mode in {"img2img", "edit"}) or (family == "flux2_klein" and mode == "img2img")))
    return {
        "source_panel": image_conditioned,
        "mask_panel": mode == "inpaint",
        "instruction_panel": family == "qwen_image_edit_2509" and mode in {"img2img", "edit"},
        "multi_source_panel": False,
        "stitch_images": stitch_route,
        "negative_prompt": True,
        "restore_faces": family in _CLASSIC_FAMILIES,
        "tiling": family in _CLASSIC_FAMILIES,
        "forge_inpaint_controls": family in _CLASSIC_FAMILIES and masked,
        "outpaint_canvas_controls": mode == "outpaint",
        "prompt_conditioning": True,
        "family_loader_mode_selectors": True,
    }


def build_forge_ux_gating_policy(
    live_intersection: dict[str, Any] | None,
    *,
    extension_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_intersection = live_intersection if isinstance(live_intersection, dict) else {}
    extension_capabilities = extension_capabilities if isinstance(extension_capabilities, dict) else {}
    image_stitch_available = bool((extension_capabilities.get("image_stitch") or {}).get("available"))
    raw_routes = live_intersection.get("routes") if isinstance(live_intersection.get("routes"), list) else []
    selectable = [item for item in raw_routes if isinstance(item, dict) and item.get("selectable")]

    executable_routes: list[dict[str, Any]] = []
    for item in selectable:
        family = str(item.get("family") or "").strip()
        loader = str(item.get("loader") or "").strip()
        mode = str(item.get("mode") or "").strip()
        if not family or not loader or not mode:
            continue
        authority = resolve_forge_route(family, loader, mode)
        executable_routes.append(
            {
                "route_key": forge_route_key(family, loader, mode),
                "family": family,
                "loader": loader,
                "mode": mode,
                "state": authority.state,
                "compiler_id": authority.compiler_id,
                "workflow_type": authority.workflow_type,
                "parameter_profile": authority.parameter_profile,
                "architecture_id": authority.architecture_id,
                "requires": list(authority.requires),
                "required_module_roles": list(authority.required_module_roles),
                "optional_module_roles": list(authority.optional_module_roles),
                "provider_loader_id": authority.provider_loader_id,
                "reason": authority.reason,
                "exact_models": sorted({str(value) for value in item.get("exact_models") or [] if str(value or "").strip()}, key=str.casefold),
                "ambiguous_models": sorted({str(value) for value in item.get("ambiguous_models") or [] if str(value or "").strip()}, key=str.casefold),
                "field_policy": _route_field_policy(family, mode),
                "control_policy": _route_control_policy(family, mode, image_stitch_available=image_stitch_available),
            }
        )

    executable_routes.sort(key=lambda item: (item["family"], item["loader"], _MODE_ORDER.index(item["mode"]) if item["mode"] in _MODE_ORDER else 99))
    families = sorted({item["family"] for item in executable_routes})
    loaders = sorted({item["loader"] for item in executable_routes})
    modes = [mode for mode in _MODE_ORDER if any(item["mode"] == mode for item in executable_routes)]
    family_loaders = {
        family: sorted({item["loader"] for item in executable_routes if item["family"] == family})
        for family in families
    }
    route_modes = {
        f"{family}::{loader}": [
            mode
            for mode in _MODE_ORDER
            if any(item["family"] == family and item["loader"] == loader and item["mode"] == mode for item in executable_routes)
        ]
        for family in families
        for loader in family_loaders.get(family, [])
    }
    route_policies = {item["route_key"]: {"field_policy": item["field_policy"], "control_policy": item["control_policy"]} for item in executable_routes}

    return {
        "schema_id": FORGE_UX_GATING_SCHEMA_ID,
        "version": FORGE_UX_GATING_VERSION,
        "provider_id": "forge",
        "ready": bool(executable_routes),
        "executable_routes": executable_routes,
        "families": families,
        "loaders": loaders,
        "modes": modes,
        "family_loaders": family_loaders,
        "route_modes": route_modes,
        "route_policies": route_policies,
        "empty_state": {
            "title": "No executable Forge image route",
            "message": "Connect or refresh the selected Forge profile, then install a model and required modules that match an implemented Neo compiler.",
        },
        "policy": {
            "selected_profile_only": True,
            "normal_ui_exposes_executable_routes_only": True,
            "static_matrix_fallback": False,
            "family_fallback": "first_executable_family",
            "loader_fallback": "first_executable_loader_for_family",
            "mode_fallback": "first_executable_mode_for_family_loader",
            "stale_selection_policy": "coerce_after_overlay_refresh",
            "empty_intersection_policy": "hide_route_selectors_and_block_generation",
            "diagnostic_routes_never_enter_normal_selectors": True,
            "forge_image_stitch_requires_verified_builtin_contract": True,
        },
    }
