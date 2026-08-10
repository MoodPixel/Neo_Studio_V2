from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .constants import CFG_SAFETY_CAP

SCHEMA_ID = "neo.image.adetailer.family_preset.v1"
AUTO_FAMILY = "auto_family"
MANUAL = "manual"
LEGACY_MANUAL = "legacy_manual"
VALID_MODES = {AUTO_FAMILY, MANUAL, LEGACY_MANUAL}

FAMILY_ALIASES = {
    "sd1.5": "sd15",
    "sd_1_5": "sd15",
    "stable_diffusion_1_5": "sd15",
    "sd_xl": "sdxl",
    "stable_diffusion_xl": "sdxl",
    "flux1": "flux",
    "flux_1": "flux",
    "flux.1": "flux",
    "qwen": "qwen_image",
    "qwen_image_base": "qwen_image",
    "qwen_rapid": "qwen_rapid_aio",
    "qwen_image_rapid": "qwen_rapid_aio",
    "qwen_2509": "qwen_image_edit_2509",
    "qwen_image_edit": "qwen_image_edit_2509",
    "qwen_2511": "qwen_image_edit_2511",
    "zimage": "z_image",
    "zimage_turbo": "z_image_turbo",
    "krea_2": "krea2",
    "krea2_raw": "krea2",
    "krea_2_turbo": "krea2_turbo",
}

# These are ADetailer crop-sampling profiles, not base-generation presets.
# Route-owned fields deliberately inherit the compiler sampler when the model
# family has multiple official or distilled sampling variants.
_FAMILY_PRESETS: dict[str, dict[str, Any]] = {
    "sdxl": {
        "preset_id": "sdxl_balanced_v1",
        "name": "SDXL Balanced",
        "status": "available",
        "values": {
            "steps": 16,
            "cfg": 5.5,
            "denoise": 0.25,
            "sampler_name": "dpmpp_2m_sde",
            "scheduler": "karras",
            "guide_size": 768,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": [],
        "notes": ["Balanced SDXL crop repair; conservative denoise preserves identity and composition."],
    },
    "sd15": {
        "preset_id": "sd15_balanced_v1",
        "name": "SD 1.5 Balanced",
        "status": "experimental",
        "values": {
            "steps": 16,
            "cfg": 6.0,
            "denoise": 0.28,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "guide_size": 768,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": [],
        "notes": ["Experimental SD 1.5 crop profile; physical visual parity remains required."],
    },
    "qwen_image": {
        "preset_id": "qwen_image_route_owned_v1",
        "name": "Qwen Image Route-Owned",
        "status": "experimental_available",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.24,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "notes": ["Sampler, scheduler, steps and CFG follow the compiler-owned Qwen route."],
    },
    "qwen_rapid_aio": {
        "preset_id": "qwen_rapid_low_step_v1",
        "name": "Qwen Rapid Low-Step",
        "status": "experimental_available",
        "values": {
            "steps": 4,
            "cfg": 1.0,
            "denoise": 0.24,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": [],
        "notes": ["Low-step distilled profile. Do not replace it with SDXL CFG or step defaults."],
    },
    "flux": {
        "preset_id": "flux_route_owned_v1",
        "name": "FLUX.1 Route-Owned",
        "status": "experimental_available",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.24,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "sampler_name", "scheduler"],
        "notes": ["FLUX keeps route-owned sampling while ADetailer CFG remains low."],
    },
    "z_image": {
        "preset_id": "z_image_route_owned_v1",
        "name": "Z-Image Base Route-Owned",
        "status": "experimental_available",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.24,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "notes": ["Z-Image Base inherits the exact compiler sampler profile."],
    },
    "z_image_turbo": {
        "preset_id": "z_image_turbo_low_step_v1",
        "name": "Z-Image Turbo Low-Step",
        "status": "experimental_available",
        "values": {
            "steps": 9,
            "cfg": 1.0,
            "denoise": 0.22,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": [],
        "notes": ["Turbo profile stays within the validated 4–9 step low-CFG range."],
    },
    "qwen_image_edit_2509": {
        "preset_id": "qwen_edit_2509_identity_safe_v1",
        "name": "Qwen Edit 2509 Identity-Safe",
        "status": "experimental_available",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.22,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 20,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "warnings": ["Native Qwen Edit detailing may drift identity unless a compatible detailer-only identity LoRA is active."],
        "notes": ["Conservative denoise and wider feathering reduce crop seams and identity drift."],
    },
    "qwen_image_edit_2511": {
        "preset_id": "qwen_edit_2511_identity_safe_v1",
        "name": "Qwen Edit 2511 Identity-Safe",
        "status": "experimental_available",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.20,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 20,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "warnings": ["Identity preservation is recommended through a compatible detailer-only LoRA."],
        "notes": ["2511 receives a slightly lower repair denoise than 2509."],
    },
    "krea2": {
        "preset_id": "krea2_raw_route_owned_v1",
        "name": "Krea 2 RAW Route-Owned",
        "status": "experimental_available",
        "values": {
            "steps": 52,
            "cfg": 3.5,
            "denoise": 0.22,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "notes": ["Krea 2 RAW keeps its compiler-selected sampler settings and uses conservative local repair strength."],
    },
    "krea2_turbo": {
        "preset_id": "krea2_turbo_route_owned_v1",
        "name": "Krea 2 Turbo Route-Owned",
        "status": "experimental_available",
        "values": {
            "steps": 8,
            "cfg": 1.0,
            "denoise": 0.20,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "notes": ["Krea 2 Turbo keeps its low-step, low-CFG compiler settings during local repair."],
    },
    "hidream": {
        "preset_id": "hidream_route_owned_v1",
        "name": "HiDream Route-Owned",
        "status": "scaffolded_gated",
        "values": {
            "steps": 20,
            "cfg": 1.0,
            "denoise": 0.24,
            "sampler_name": "euler",
            "scheduler": "simple",
            "guide_size": 1024,
            "max_size": 1024,
            "noise_mask": True,
            "force_inpaint": True,
            "noise_mask_feather": 16,
        },
        "route_owned": ["steps", "cfg", "sampler_name", "scheduler"],
        "notes": ["HiDream has a preset definition, but its route remains unavailable until a compatible repair path is enabled."],
    },
}


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def normalize_family_id(value: Any) -> str:
    token = _token(value)
    return FAMILY_ALIASES.get(token, token)


def normalize_family_preset_mode(value: Any) -> str:
    token = _token(value)
    aliases = {
        "": AUTO_FAMILY,
        "auto": AUTO_FAMILY,
        "family": AUTO_FAMILY,
        "family_auto": AUTO_FAMILY,
        "recommended": AUTO_FAMILY,
        "custom": MANUAL,
        "legacy": LEGACY_MANUAL,
    }
    resolved = aliases.get(token, token)
    return resolved if resolved in VALID_MODES else AUTO_FAMILY


def family_preset_registry() -> dict[str, Any]:
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "modes": [AUTO_FAMILY, MANUAL, LEGACY_MANUAL],
        "profiles": deepcopy(_FAMILY_PRESETS),
        "family_count": len(_FAMILY_PRESETS),
        "unknown_family_policy": "fail_closed_no_sdxl_fallback",
        "route_owned_policy": "read_exact_compiler_sampler_inputs_then_use_profile_fallback_only_when_missing",
    }


def _route_family(route: Mapping[str, Any] | None) -> str:
    route_data = route if isinstance(route, Mapping) else {}
    return normalize_family_id(route_data.get("family") or route_data.get("model_family"))


def _effective_family(route: Mapping[str, Any] | None, model_source: Mapping[str, Any] | None) -> tuple[str, str]:
    source = str((model_source or {}).get("source") or "generation_model").strip().lower()
    if source == "dedicated_checkpoint":
        return normalize_family_id((model_source or {}).get("family")), "dedicated_detailer_family"
    return _route_family(route), "generation_route_family"


def _object_info(available_nodes: Any) -> Mapping[str, Any] | None:
    if not isinstance(available_nodes, Mapping):
        return None
    nested = available_nodes.get("object_info")
    return nested if isinstance(nested, Mapping) else available_nodes


def _choices(available_nodes: Any, node_class: str, input_name: str) -> list[str] | None:
    info = _object_info(available_nodes)
    if not isinstance(info, Mapping):
        return None
    schema = info.get(node_class)
    if not isinstance(schema, Mapping):
        return None
    input_schema = schema.get("input") if isinstance(schema.get("input"), Mapping) else {}
    for section_name in ("required", "optional"):
        section = input_schema.get(section_name) if isinstance(input_schema.get(section_name), Mapping) else {}
        raw = section.get(input_name)
        if raw is None:
            continue
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if isinstance(first, Mapping):
            first = first.get("choices") or first.get("values") or first.get("options") or []
        if isinstance(first, (list, tuple, set)):
            return [str(item) for item in first if str(item).strip()]
        return []
    return None


def _canonical_choice(value: Any, choices: list[str] | None) -> tuple[str, str]:
    requested = str(value or "").strip()
    if choices is None:
        return requested, "unchecked"
    by_folded = {item.casefold(): item for item in choices}
    match = by_folded.get(requested.casefold())
    return (match, "accepted") if match else (requested, "rejected")


def resolve_family_preset_plan(
    params: Mapping[str, Any] | None,
    *,
    route: Mapping[str, Any] | None,
    model_source: Mapping[str, Any] | None,
    available_nodes: Any = None,
) -> dict[str, Any]:
    source = params if isinstance(params, Mapping) else {}
    mode = normalize_family_preset_mode(source.get("family_preset_mode"))
    family, family_source = _effective_family(route, model_source)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if mode in {MANUAL, LEGACY_MANUAL}:
        return {
            "schema_id": SCHEMA_ID,
            "ready": True,
            "mode": mode,
            "family": family,
            "family_source": family_source,
            "preset_id": "manual" if mode == MANUAL else "legacy_manual",
            "name": "Manual" if mode == MANUAL else "Legacy manual compatibility",
            "profile": {},
            "errors": [],
            "warnings": [],
            "unknown_family_policy": "manual_values_allowed_without_family_fallback",
        }

    profile = deepcopy(_FAMILY_PRESETS.get(family) or {})
    if not family:
        errors.append({
            "code": "adetailer_family_preset_family_missing",
            "field": "family_preset_mode",
            "message": "Automatic ADetailer sampling requires an explicit route or dedicated detailer family.",
        })
    elif not profile:
        errors.append({
            "code": "adetailer_family_preset_missing",
            "field": "family_preset_mode",
            "message": f"No ADetailer family preset is registered for {family!r}; Neo will not fall back to SDXL settings.",
            "family": family,
        })

    if profile:
        values = profile.get("values") if isinstance(profile.get("values"), Mapping) else {}
        route_owned = set(profile.get("route_owned") or [])
        for key, node_input in (("sampler_name", "sampler_name"), ("scheduler", "scheduler")):
            if key in route_owned:
                continue
            choices = _choices(available_nodes, "FaceDetailer", node_input)
            canonical, status = _canonical_choice(values.get(key), choices)
            if status == "rejected":
                errors.append({
                    "code": f"adetailer_family_preset_{key}_unsupported",
                    "field": key,
                    "message": f"The active FaceDetailer node does not accept preset {key} {values.get(key)!r}.",
                    "family": family,
                    "preset_id": profile.get("preset_id"),
                })
            elif status == "accepted" and canonical != values.get(key):
                values[key] = canonical
            elif status == "unchecked":
                warnings.append({
                    "code": f"adetailer_family_preset_{key}_catalog_unchecked",
                    "field": key,
                    "message": f"FaceDetailer did not publish {key} choices; the family preset value will be submitted exactly.",
                })
        profile["values"] = dict(values)
        for message in profile.get("warnings") or []:
            warnings.append({
                "code": "adetailer_family_preset_family_warning",
                "field": "family_preset_mode",
                "message": str(message),
                "family": family,
                "preset_id": profile.get("preset_id"),
            })

    return {
        "schema_id": SCHEMA_ID,
        "ready": not errors,
        "mode": mode,
        "family": family,
        "family_source": family_source,
        "preset_id": str(profile.get("preset_id") or ""),
        "name": str(profile.get("name") or family),
        "profile": profile,
        "errors": errors,
        "warnings": warnings,
        "unknown_family_policy": "fail_closed_no_sdxl_fallback",
    }


def _node_inputs(workflow: Mapping[str, Any], node_id: Any) -> Mapping[str, Any]:
    node = workflow.get(str(node_id)) if isinstance(workflow, Mapping) else None
    inputs = node.get("inputs") if isinstance(node, Mapping) and isinstance(node.get("inputs"), Mapping) else {}
    return inputs


def _number(value: Any, fallback: float, *, integer: bool = False) -> int | float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(fallback)
    return int(round(parsed)) if integer else parsed


def materialize_family_preset(
    plan: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    workflow: Mapping[str, Any],
    contract: Mapping[str, Any],
    sampler_name_override: str | None = None,
    scheduler_override: str | None = None,
    available_nodes: Any = None,
) -> dict[str, Any]:
    requested = deepcopy(dict(params or {}))
    mode = normalize_family_preset_mode(plan.get("mode"))
    sampler = contract.get("sampler") if isinstance(contract.get("sampler"), Mapping) else {}
    sampler_inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), Mapping) else {}
    sampler_node_id = sampler.get("node_id")
    live_inputs = _node_inputs(workflow, sampler_node_id)
    route_values = {
        "steps": live_inputs.get(str(sampler_inputs.get("steps") or "steps")),
        "cfg": live_inputs.get(str(sampler_inputs.get("cfg") or "cfg")),
        "sampler_name": sampler_name_override or live_inputs.get(str(sampler_inputs.get("sampler_name") or "sampler_name")),
        "scheduler": scheduler_override or live_inputs.get(str(sampler_inputs.get("scheduler") or "scheduler")),
        "denoise": live_inputs.get(str(sampler_inputs.get("denoise") or "denoise")),
    }
    sources: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if mode == LEGACY_MANUAL:
        effective = deepcopy(requested)
        effective["steps"] = int(_number(requested.get("steps"), 20, integer=True))
        effective["cfg"] = float(requested.get("cfg") if requested.get("cfg") is not None else CFG_SAFETY_CAP)
        effective["denoise"] = float(_number(requested.get("denoise"), 0.35))
        effective["sampler_name"] = str(requested.get("sampler_name") or route_values.get("sampler_name") or "euler")
        effective["scheduler"] = str(requested.get("scheduler") or route_values.get("scheduler") or "normal")
        effective["guide_size"] = int(_number(requested.get("guide_size"), 512, integer=True))
        effective["max_size"] = int(_number(requested.get("max_size"), 1024, integer=True))
        effective["noise_mask"] = bool(requested.get("noise_mask", True))
        effective["force_inpaint"] = bool(requested.get("force_inpaint", True))
        effective["noise_mask_feather"] = int(_number(requested.get("noise_mask_feather"), requested.get("mask_blur", 4), integer=True))
        sources = {key: "legacy_payload" for key in ("steps", "cfg", "denoise", "sampler_name", "scheduler", "guide_size", "max_size", "noise_mask", "force_inpaint", "noise_mask_feather")}
    elif mode == MANUAL:
        effective = deepcopy(requested)
        effective["steps"] = int(_number(requested.get("steps") if requested.get("steps") is not None else route_values.get("steps"), 20, integer=True))
        cfg_value = requested.get("cfg") if requested.get("cfg") is not None else route_values.get("cfg")
        effective["cfg"] = float(_number(cfg_value, 1.0))
        effective["denoise"] = float(_number(requested.get("denoise"), 0.25))
        effective["sampler_name"] = str(requested.get("sampler_name") or route_values.get("sampler_name") or "euler")
        effective["scheduler"] = str(requested.get("scheduler") or route_values.get("scheduler") or "normal")
        effective["guide_size"] = int(_number(requested.get("guide_size"), 768, integer=True))
        effective["max_size"] = int(_number(requested.get("max_size"), 1024, integer=True))
        effective["noise_mask"] = bool(requested.get("noise_mask", True))
        effective["force_inpaint"] = bool(requested.get("force_inpaint", True))
        effective["noise_mask_feather"] = int(_number(requested.get("noise_mask_feather"), 16, integer=True))
        sources = {key: "manual" for key in ("steps", "denoise", "guide_size", "max_size", "noise_mask", "force_inpaint", "noise_mask_feather")}
        sources.update({"cfg": "manual" if requested.get("cfg") is not None else "route", "sampler_name": "manual" if requested.get("sampler_name") else "route", "scheduler": "manual" if requested.get("scheduler") else "route"})
    else:
        profile = plan.get("profile") if isinstance(plan.get("profile"), Mapping) else {}
        values = profile.get("values") if isinstance(profile.get("values"), Mapping) else {}
        route_owned = set(profile.get("route_owned") or [])
        effective = deepcopy(requested)
        for key in ("steps", "cfg", "denoise", "sampler_name", "scheduler", "guide_size", "max_size", "noise_mask", "force_inpaint", "noise_mask_feather"):
            fallback = values.get(key)
            if key in route_owned:
                route_value = route_values.get(key)
                value = route_value if route_value not in (None, "") else fallback
                sources[key] = "route" if route_value not in (None, "") else "preset_fallback"
                if route_value in (None, ""):
                    warnings.append({
                        "code": "adetailer_family_preset_route_value_missing",
                        "field": key,
                        "message": f"The compiler sampler did not expose {key}; the {plan.get('preset_id')} fallback was used.",
                    })
            else:
                value = fallback
                sources[key] = "family_preset"
            effective[key] = value
        effective["steps"] = int(_number(effective.get("steps"), 20, integer=True))
        effective["cfg"] = float(_number(effective.get("cfg"), 1.0))
        effective["denoise"] = float(_number(effective.get("denoise"), 0.25))
        effective["guide_size"] = int(_number(effective.get("guide_size"), 768, integer=True))
        effective["max_size"] = int(_number(effective.get("max_size"), 1024, integer=True))
        effective["noise_mask"] = bool(effective.get("noise_mask", True))
        effective["force_inpaint"] = bool(effective.get("force_inpaint", True))
        effective["noise_mask_feather"] = int(_number(effective.get("noise_mask_feather"), 16, integer=True))
        effective["sampler_name"] = str(effective.get("sampler_name") or "euler")
        effective["scheduler"] = str(effective.get("scheduler") or "normal")

    if int(effective.get("max_size") or 0) < int(effective.get("guide_size") or 0):
        effective["max_size"] = int(effective.get("guide_size") or 1024)
        warnings.append({
            "code": "adetailer_family_preset_max_size_raised",
            "field": "max_size",
            "message": "ADetailer max size was raised to match guide size.",
        })

    for key in ("sampler_name", "scheduler"):
        choices = _choices(available_nodes, "FaceDetailer", key)
        canonical, status = _canonical_choice(effective.get(key), choices)
        if status == "rejected":
            errors.append({
                "code": f"adetailer_family_preset_effective_{key}_unsupported",
                "field": key,
                "message": f"The active FaceDetailer node does not accept effective {key} {effective.get(key)!r}.",
            })
        elif status == "accepted":
            effective[key] = canonical

    return {
        "schema_id": SCHEMA_ID,
        "ready": not errors,
        "mode": mode,
        "family": str(plan.get("family") or ""),
        "family_source": str(plan.get("family_source") or ""),
        "preset_id": str(plan.get("preset_id") or ("manual" if mode == MANUAL else "legacy_manual")),
        "name": str(plan.get("name") or "Manual"),
        "status": str((plan.get("profile") or {}).get("status") or "manual"),
        "effective_params": effective,
        "effective_values": {key: deepcopy(effective.get(key)) for key in ("steps", "cfg", "denoise", "sampler_name", "scheduler", "guide_size", "max_size", "noise_mask", "force_inpaint", "noise_mask_feather")},
        "value_sources": sources,
        "route_values": route_values,
        "notes": deepcopy((plan.get("profile") or {}).get("notes") or []),
        "warnings": deepcopy(list(plan.get("warnings") or [])) + warnings,
        "errors": deepcopy(list(plan.get("errors") or [])) + errors,
        "unknown_family_policy": "fail_closed_no_sdxl_fallback",
    }


def public_family_preset_metadata(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    source = plan if isinstance(plan, Mapping) else {}
    return {
        "schema_id": SCHEMA_ID,
        "ready": bool(source.get("ready")),
        "mode": str(source.get("mode") or ""),
        "family": str(source.get("family") or ""),
        "family_source": str(source.get("family_source") or ""),
        "preset_id": str(source.get("preset_id") or ""),
        "name": str(source.get("name") or ""),
        "status": str(source.get("status") or (source.get("profile") or {}).get("status") or ""),
        "effective_values": deepcopy(source.get("effective_values") or {}),
        "value_sources": deepcopy(source.get("value_sources") or {}),
        "notes": deepcopy(source.get("notes") or (source.get("profile") or {}).get("notes") or []),
        "warnings": deepcopy(source.get("warnings") or []),
        "unknown_family_policy": str(source.get("unknown_family_policy") or "fail_closed_no_sdxl_fallback"),
    }
