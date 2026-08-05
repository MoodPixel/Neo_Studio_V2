from __future__ import annotations

from typing import Any, Iterable

from neo_app.models.forge_neo_route_catalog import FORGE_ROUTE_CATALOG_SCHEMA_ID
from neo_app.providers.forge_admin import forge_models_for_backend_profile, load_forge_admin_cache
from neo_app.providers.forge_neo_loader_translation import forge_loader_translation_contract_payload
from neo_app.providers.forge_neo_workflow_compilers import forge_workflow_compiler_contract_payload
from neo_app.providers.forge_neo_ux_gating import build_forge_ux_gating_policy
from neo_app.providers.forge_neo_model_classification import build_forge_live_route_intersection, ensure_forge_live_discovery

IMAGE_CAPABILITY_OVERLAY_SCHEMA_ID = "neo.image.capability_overlay.v1"
FORGE_IMAGE_OVERLAY_SCHEMA_ID = "neo.image.capability_overlay.forge.v1"

_CONNECTED_STATES = {"connected", "connected_with_warnings", "online", "ready", "available"}

_FORGE_EXTENSION_CAPABILITIES: dict[str, str] = {
    "image.controlnet": "controlnet",
    "image.controlnet_depth_pack": "controlnet",
    "image.ip_adapter": "ip_adapter",
    "lora_stack": "lora",
    "image.lora_stack": "lora",
    "embeddings_ti": "embeddings",
    "image.embeddings_ti": "embeddings",
    "cfg_fix_dynamic_thresholding": "cfg",
    "image.cfg_fix_dynamic_thresholding": "cfg",
    "image.adetailer": "adetailer_inline",
    "image.high_res_lab": "highres_inline",
    "image.layerdiffuse": "layerdiffuse_inline",
    "image.image_upscale": "image_upscale",
    "image.pid_integrated": "pid_integrated",
    "image.spectrum": "spectrum",
    "image.multidiffusion": "multidiffusion",
    "image.forge_couple": "forge_couple",
    "image.forge_script_bridge": "generic_extension_bridge",
    "image.gguf_loader": "gguf",
    "image.scene_director": "scene_director",
}
_PROVIDER_NEUTRAL_EXTENSIONS = {"wildcards", "style_stack", "image.wildcards", "image.style_stack"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_name(record: Any) -> str:
    if isinstance(record, dict):
        for key in ("name", "title", "model_name", "label", "id", "value"):
            text = str(record.get(key) or "").strip()
            if text:
                return text
    return str(record or "").strip()


def _catalog(records: Iterable[Any], *, kind: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        name = _safe_name(record)
        if not name or name in seen:
            continue
        seen.add(name)
        output.append({"id": name, "name": name, "kind": kind})
    return output


def _setting_value(snapshot: dict[str, Any], *candidate_keys: str) -> Any:
    catalog = _as_dict(snapshot.get("settings_catalog"))
    for setting in _as_list(catalog.get("settings")):
        if not isinstance(setting, dict):
            continue
        key = str(setting.get("key") or "")
        if key in candidate_keys:
            return setting.get("current_value", setting.get("value"))
    return None


def _forge_resolution_policy(snapshot: dict[str, Any]) -> dict[str, Any]:
    explicit = _setting_value(
        snapshot,
        "resolution_step",
        "dimensions_step",
        "dimension_step",
        "ui_resolution_step",
        "img2img_resolution_step",
    )
    step = 64
    source = "forge_neo_default"
    try:
        parsed = int(explicit)
        if 8 <= parsed <= 512:
            step = parsed
            source = "forge_settings"
    except (TypeError, ValueError):
        pass
    return {
        "enabled": True,
        "step": step,
        "minimum": step,
        "maximum": 16384,
        "strategy": "nearest_multiple",
        "source": source,
        "message": f"Forge requires width and height values aligned to {step}-pixel increments.",
    }


def _forge_extension_policy(profile: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capabilities = {
        **_as_dict(profile.get("capability_flags")),
        **_as_dict(profile.get("capabilities")),
        **_as_dict(snapshot.get("capabilities")),
    }
    extension_capabilities = _as_dict(snapshot.get("extension_capabilities"))
    detail_key = {
        "lora": "lora_stack",
        "embeddings": "embeddings_ti",
        "highres_inline": "high_res_lab",
        "image_upscale": "image_upscale",
        "controlnet": "controlnet",
        "adetailer_inline": "adetailer",
        "ip_adapter": "ip_adapter",
        "pid_integrated": "pid_integrated",
        "spectrum": "spectrum",
        "multidiffusion": "multidiffusion",
        "forge_couple": "forge_couple",
        "generic_extension_bridge": "forge_script_bridge",
    }
    policy: dict[str, dict[str, Any]] = {}
    for extension_id in sorted(_PROVIDER_NEUTRAL_EXTENSIONS):
        policy[extension_id] = {
            "allowed": True,
            "mode": "provider_neutral_prompt_transform",
            "reason": "This extension resolves prompts before the Forge provider boundary.",
        }
    for extension_id, capability in sorted(_FORGE_EXTENSION_CAPABILITIES.items()):
        detail = _as_dict(extension_capabilities.get(detail_key.get(capability, "")))
        allowed = bool(detail.get("available", capabilities.get(capability, False)))
        mode = str(detail.get("mode") or ("inline_provider_capability" if allowed else "gated"))
        policy[extension_id] = {
            "allowed": allowed,
            "mode": mode if allowed else "gated",
            "required_capability": capability,
            "contract": str(detail.get("contract") or ""),
            "reason": str(detail.get("reason") or ("Forge capability is available." if allowed else f"Forge does not expose a verified {capability.replace('_', ' ')} mapping in this release.")),
        }
        for scalar_key in ("max_passes", "max_units", "argument_count", "tile_argument_count"):
            if detail.get(scalar_key) is not None:
                policy[extension_id][scalar_key] = detail.get(scalar_key)
        for text_key in ("script_name", "tile_script_name", "tile_contract", "tile_reason"):
            if str(detail.get(text_key) or "").strip():
                policy[extension_id][text_key] = str(detail.get(text_key) or "")
        for bool_key in ("supports_common_prompts", "supports_hires_compatibility", "supports_tile_mode", "tile_runtime_available", "standard_available", "faceid_available", "instantid_available", "supports_codeformer", "supports_gfpgan", "supports_face_restoration", "supports_exact_dimensions", "supports_secondary_upscaler", "supports_upscale_first", "supports_crop_to_fit", "supports_seedvr2", "selected_profile_only", "automatic_provider_fallback"):
            if detail.get(bool_key) is not None:
                policy[extension_id][bool_key] = bool(detail.get(bool_key))
        for map_key in ("pass_slots_by_mode", "unit_slots_by_mode", "models_by_family", "faceid_models_by_family"):
            if isinstance(detail.get(map_key), dict):
                policy[extension_id][map_key] = dict(detail.get(map_key) or {})
        if isinstance(detail.get("available_modes"), list):
            policy[extension_id]["available_modes"] = list(detail.get("available_modes") or [])
        for catalog_key in ("models", "modules", "embeddings", "upscalers", "face_restorers", "pid_models", "vaes", "text_encoders", "methods", "conflicts", "supported_region_modes", "native_supported_region_modes", "tile_upscalers", "tile_supported_region_modes", "faceid_records", "faceid_models", "faceid_preprocessors"):
            if isinstance(detail.get(catalog_key), list):
                policy[extension_id][catalog_key] = list(detail.get(catalog_key) or [])
        if extension_id == "image.forge_script_bridge":
            generic = _as_dict(snapshot.get("generic_extension_bridge"))
            policy[extension_id]["scripts"] = [dict(item) for item in _as_list(generic.get("scripts")) if isinstance(item, dict)]
            policy[extension_id]["extensions"] = [dict(item) for item in _as_list(generic.get("extensions")) if isinstance(item, dict)]
            policy[extension_id]["summary"] = dict(_as_dict(generic.get("summary")))
            policy[extension_id]["execution_policy"] = str(generic.get("execution_policy") or "")
            if isinstance(detail.get("supported_families"), list):
                policy[extension_id]["supported_families"] = list(detail.get("supported_families") or [])
            if isinstance(detail.get("supported_modes"), list):
                policy[extension_id]["supported_modes"] = list(detail.get("supported_modes") or [])
    return policy


def _forge_field_policy(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capabilities = {**_as_dict(profile.get("capability_flags")), **_as_dict(profile.get("capabilities"))}
    policy: dict[str, dict[str, Any]] = {}
    always = {
        "checkpoint",
        "vae",
        "sampler",
        "scheduler",
        "width",
        "height",
        "steps",
        "seed",
        "batch_count",
        "cfg",
        "positive_prompt",
        "negative_prompt",
        "source_image",
        "mask_image",
        "denoise",
    }
    for field_id in always:
        policy[field_id] = {"visible": True, "enabled": True, "reason": "Mapped to the Forge SD API."}
    policy["clip_skip"] = {
        "visible": bool(capabilities.get("clip_skip", True)),
        "enabled": bool(capabilities.get("clip_skip", True)),
        "reason": "Mapped through CLIP_stop_at_last_layers." if capabilities.get("clip_skip", True) else "Forge clip skip is unavailable.",
    }
    policy["latent_capture_mode"] = {
        "visible": False,
        "enabled": False,
        "reason": "Forge Phase 4 has no verified latent-artifact export contract.",
    }
    policy["outpaint_padding"] = {
        "visible": bool(capabilities.get("outpaint", False)),
        "enabled": bool(capabilities.get("outpaint", False)),
        "reason": "Neo expands the canvas and compiles a Forge img2img mask." if capabilities.get("outpaint", False) else "Forge outpaint is unavailable for this profile.",
    }
    policy["mask_grow"] = {
        "visible": False,
        "enabled": False,
        "reason": "Forge exposes mask blur, but not Neo's Comfy mask-grow contract.",
    }
    policy["inpaint_context_mode"] = {
        "visible": False,
        "enabled": False,
        "reason": "Neo's Comfy latent context modes do not map to the Forge SD API.",
    }
    policy["inpaint_selection_target"] = {
        "visible": True,
        "enabled": True,
        "reason": "Mapped to Forge inpainting_mask_invert.",
    }
    return policy


def _forge_overlay(profile: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("profile_id") or "forge_local")
    runtime = _as_dict(profile.get("runtime"))
    status = str(snapshot.get("status") or runtime.get("status") or profile.get("runtime_status") or "not_checked")
    connected = status in _CONNECTED_STATES and snapshot.get("reachable", runtime.get("reachable", False)) is not False
    snapshot_caps = _as_dict(snapshot.get("capabilities"))
    profile_caps = {**_as_dict(profile.get("capability_flags")), **_as_dict(profile.get("capabilities"))}
    enabled_modes = {mode for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit") if bool(profile_caps.get(mode, False))}
    if not any(mode in profile_caps for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit")):
        enabled_modes = {"txt2img", "img2img", "inpaint", "outpaint", "edit"}

    classification, _cached_intersection = ensure_forge_live_discovery(snapshot)
    live_intersection = build_forge_live_route_intersection(classification, enabled_modes=enabled_modes)
    ux_gating = build_forge_ux_gating_policy(
        live_intersection,
        extension_capabilities=_as_dict(snapshot.get("extension_capabilities")),
    )
    modes = list(ux_gating.get("modes") or [])
    selectable_model_names = {
        str(name)
        for route in _as_list(live_intersection.get("routes"))
        if isinstance(route, dict) and route.get("selectable")
        for name in [*_as_list(route.get("exact_models")), *_as_list(route.get("ambiguous_models"))]
        if str(name or "").strip()
    }

    catalogs = forge_models_for_backend_profile(snapshot) if snapshot else _as_dict(runtime.get("models"))
    all_models = _catalog(catalogs.get("models") or snapshot.get("models") or [], kind="checkpoint")
    models = [item for item in all_models if item.get("name") in selectable_model_names]
    diffusion_models = [item for item in _catalog(catalogs.get("diffusion_models") or [], kind="diffusion_model") if item.get("name") in selectable_model_names]
    gguf_models = [item for item in _catalog(catalogs.get("gguf_models") or [], kind="gguf_model") if item.get("name") in selectable_model_names]
    vaes = _catalog(catalogs.get("vaes") or [], kind="vae")
    gguf_vaes = _catalog(catalogs.get("gguf_vaes") or [], kind="gguf_vae")
    text_encoders = _catalog(catalogs.get("text_encoders") or [], kind="text_encoder")
    qwen_text_encoders = _catalog(catalogs.get("qwen_text_encoders") or [], kind="qwen_text_encoder")
    gguf_text_encoders = _catalog(catalogs.get("gguf_text_encoders") or [], kind="gguf_text_encoder")
    gguf_text_encoder_primary = _catalog(catalogs.get("gguf_text_encoder_primary") or [], kind="gguf_text_encoder_primary")
    gguf_text_encoder_secondary = _catalog(catalogs.get("gguf_text_encoder_secondary") or [], kind="gguf_text_encoder_secondary")
    mmproj = _catalog(catalogs.get("mmproj") or [], kind="mmproj")
    samplers = _catalog(catalogs.get("samplers") or snapshot.get("samplers") or [], kind="sampler")
    schedulers = _catalog(catalogs.get("schedulers") or snapshot.get("schedulers") or [], kind="scheduler")
    upscalers = _catalog(catalogs.get("upscalers") or snapshot.get("upscalers") or [], kind="upscaler")
    extension_caps = _as_dict(snapshot.get("extension_capabilities"))
    embedding_names = _as_list(_as_dict(extension_caps.get("embeddings_ti")).get("embeddings"))
    embeddings = _catalog(embedding_names, kind="embedding")

    classified_models = [
        {
            "id": str(item.get("id") or item.get("name") or ""),
            "name": str(item.get("name") or item.get("id") or ""),
            "family": str(item.get("family") or ""),
            "family_candidates": list(item.get("family_candidates") or []),
            "loader_candidates": list(item.get("loader_candidates") or []),
            "format": str(item.get("format") or "unknown"),
            "packaging": str(item.get("packaging") or "unknown"),
            "classification_status": str(item.get("classification_status") or "unclassified"),
            "route_eligible": bool(item.get("route_eligible")),
            "confidence": str(item.get("confidence") or "none"),
            "variant": str(item.get("variant") or ""),
        }
        for item in _as_list(classification.get("models"))
        if isinstance(item, dict) and str(item.get("name") or item.get("id") or "").strip()
    ]

    warnings = [str(item) for item in _as_list(snapshot.get("warnings")) if str(item or "").strip()]
    if not connected:
        warnings.append("Connect/Test this Forge profile in Admin to load live models and sampling catalogs.")
    if not all_models:
        warnings.append("No Forge primary models are cached for this profile.")
    elif not models:
        ready_targets = _as_list(_as_dict(live_intersection.get("diagnostics")).get("compiler_targets_assets_ready"))
        if ready_targets:
            warnings.append("Forge models and required assets were discovered, but their remaining family or workflow routes are still gated.")
        else:
            warnings.append("No discovered Forge model intersects an executable Neo route for the selected profile.")
    if not samplers:
        warnings.append("No Forge samplers are cached for this profile.")
    if not schedulers:
        warnings.append("No Forge schedulers are cached for this profile.")

    generation_ready = bool(
        connected
        and snapshot_caps.get("neo_execution_adapter", profile_caps.get("execution_lifecycle", False))
        and ux_gating.get("ready")
        and models
        and samplers
        and schedulers
    )
    discovered_families = list(_as_dict(live_intersection.get("diagnostics")).get("discovered_families") or [])
    return {
        "schema_id": FORGE_IMAGE_OVERLAY_SCHEMA_ID,
        "overlay_schema_id": IMAGE_CAPABILITY_OVERLAY_SCHEMA_ID,
        "profile_id": profile_id,
        "provider_id": "forge",
        "status": status,
        "connected": connected,
        "generation_ready": generation_ready,
        "message": (
            "Forge Image controls are using the selected profile's live model classification and executable route intersection."
            if generation_ready
            else "Forge Image controls are hidden until route authority and live profile assets intersect."
        ),
        "catalog_scope": "selected_profile",
        "route_support": {
            "schema_id": str(live_intersection.get("schema_id") or ""),
            "authority_schema_id": FORGE_ROUTE_CATALOG_SCHEMA_ID,
            "version": str(live_intersection.get("version") or ""),
            "families": list(ux_gating.get("families") or []),
            "loaders": list(ux_gating.get("loaders") or []),
            "modes": modes,
            "routes": list(ux_gating.get("executable_routes") or []),
            "unsupported_modes": [mode for mode in ("txt2img", "img2img", "inpaint", "outpaint", "edit") if mode not in modes],
        },
        "live_model_classification": classification,
        "live_route_intersection": live_intersection,
        "loader_translation_contract": forge_loader_translation_contract_payload(),
        "workflow_compiler_contract": forge_workflow_compiler_contract_payload(),
        "ux_gating": ux_gating,
        "catalogs": {
            "models": models,
            "classified_models": classified_models,
            "diffusion_models": diffusion_models,
            "gguf_models": gguf_models,
            "vaes": vaes,
            "gguf_vaes": gguf_vaes,
            "text_encoders": text_encoders,
            "qwen_text_encoders": qwen_text_encoders,
            "gguf_text_encoders": gguf_text_encoders,
            "gguf_text_encoder_primary": gguf_text_encoder_primary,
            "gguf_text_encoder_secondary": gguf_text_encoder_secondary,
            "mmproj": mmproj,
            "samplers": samplers,
            "schedulers": schedulers,
            "upscalers": upscalers,
            "embeddings": embeddings,
        },
        "catalog_counts": {
            "models": len(models),
            "classified_models": len(classified_models),
            "diffusion_models": len(diffusion_models),
            "gguf_models": len(gguf_models),
            "vaes": len(vaes),
            "gguf_vaes": len(gguf_vaes),
            "text_encoders": len(text_encoders),
            "qwen_text_encoders": len(qwen_text_encoders),
            "gguf_text_encoders": len(gguf_text_encoders),
            "gguf_text_encoder_primary": len(gguf_text_encoder_primary),
            "gguf_text_encoder_secondary": len(gguf_text_encoder_secondary),
            "mmproj": len(mmproj),
            "samplers": len(samplers),
            "schedulers": len(schedulers),
            "upscalers": len(upscalers),
            "embeddings": len(embeddings),
        },
        "resolution_policy": _forge_resolution_policy(snapshot),
        "field_policy": _forge_field_policy(profile),
        "extension_policy": _forge_extension_policy(profile, snapshot),
        "model_controls": {
            "checkpoint_source": "/sdapi/v1/sd-models",
            "module_source": "/sdapi/v1/sd-modules",
            "module_override_field": "forge_additional_modules",
            "supports_vae_override": bool(vaes),
            "supports_text_encoder_modules": bool(text_encoders),
            "family_selection_policy": "forge_route_authority_intersect_live_selected_profile",
            "family_detection_message": "Phase 2 classifies live primary models and modules conservatively. Only routes with implemented compilers remain selectable.",
            "provider_loader_id": "forge_model_bundle",
            "discovered_families": discovered_families,
        },
        "forge_controls": {
            "restore_faces": {"visible": True, "default": False},
            "tiling": {"visible": True, "default": False},
            "inpainting_fill": {"visible_modes": ["inpaint"], "default": 1},
            "inpaint_full_res": {"visible_modes": ["inpaint"], "default": True},
            "inpaint_full_res_padding": {"visible_modes": ["inpaint"], "default": 32},
            "inpainting_mask_invert": {"visible_modes": ["inpaint"], "default": 0},
        },
        "warnings": list(dict.fromkeys(warnings)),
        "cache": _as_dict(snapshot.get("cache")),
    }


def build_image_capability_overlay(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    profile_id = str(profile.get("profile_id") or "")
    provider_id = str(profile.get("provider_id") or "")
    if not profile_id:
        return {
            "schema_id": IMAGE_CAPABILITY_OVERLAY_SCHEMA_ID,
            "profile_id": "",
            "provider_id": provider_id,
            "status": "missing_config",
            "connected": False,
            "generation_ready": False,
            "message": "No Image backend profile was selected.",
            "catalog_scope": "selected_profile",
            "catalogs": {},
            "resolution_policy": {"enabled": False, "step": 1},
            "field_policy": {},
            "extension_policy": {},
            "warnings": ["Select an Image backend profile."],
        }
    if provider_id == "forge":
        runtime = _as_dict(profile.get("runtime"))
        snapshot = _as_dict(runtime.get("forge_admin")) or _as_dict(load_forge_admin_cache(profile_id))
        return _forge_overlay(profile, snapshot)
    runtime = _as_dict(profile.get("runtime"))
    status = str(runtime.get("status") or profile.get("runtime_status") or "not_checked").strip().lower()
    connected = status in _CONNECTED_STATES and runtime.get("reachable") is not False
    backend_capabilities = _as_dict(profile.get("backend_capabilities")) or _as_dict(runtime.get("backend_capabilities"))
    if provider_id in {"comfyui", "comfyui_portable"}:
        snapshot_ready = bool(
            connected
            and backend_capabilities.get("reachable") is not False
            and backend_capabilities.get("object_info_available") is True
        )
        warnings = [str(item) for item in _as_list(backend_capabilities.get("warnings")) if str(item or "").strip()]
        errors = [str(item) for item in _as_list(backend_capabilities.get("errors")) if str(item or "").strip()]
        if connected and not backend_capabilities:
            warnings.append("Connect/Test completed without a profile-bound Comfy capability snapshot. Reconnect to refresh object_info.")
        if not connected:
            warnings.append("Connect/Test the selected ComfyUI profile to load live node and model capabilities.")
        return {
            "schema_id": IMAGE_CAPABILITY_OVERLAY_SCHEMA_ID,
            "profile_id": profile_id,
            "provider_id": provider_id,
            "status": status,
            "connected": connected,
            "generation_ready": connected,
            "message": (
                "Selected-profile Comfy capabilities are current."
                if snapshot_ready
                else "LanPaint and route-aware extensions remain gated until the selected profile exposes a live object_info snapshot."
            ),
            "catalog_scope": "selected_profile",
            "catalogs": {},
            "resolution_policy": {"enabled": False, "step": 1},
            "field_policy": {},
            "extension_policy": {},
            "backend_capabilities": backend_capabilities,
            "capability_source": str(runtime.get("capability_source") or ""),
            "capability_snapshot_ready": snapshot_ready,
            "warnings": list(dict.fromkeys([*warnings, *errors])),
        }
    return {
        "schema_id": IMAGE_CAPABILITY_OVERLAY_SCHEMA_ID,
        "profile_id": profile_id,
        "provider_id": provider_id,
        "status": str(runtime.get("status") or profile.get("runtime_status") or "not_checked"),
        "connected": True,
        "generation_ready": True,
        "message": "The selected provider uses its existing Image surface contract.",
        "catalog_scope": "provider_default",
        "catalogs": {},
        "resolution_policy": {"enabled": False, "step": 1},
        "field_policy": {},
        "extension_policy": {},
        "warnings": [],
    }
