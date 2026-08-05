from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
import json


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "models" / "sampling_guidance_capabilities.json"
SCHEMA = "neo.image.sampling_guidance_capabilities.v1"
PHASE = "IP-1"

MODE_ALIASES = {
    "generate": "txt2img",
    "text_to_image": "txt2img",
    "image_to_image": "img2img",
}

FAMILY_ALIASES = {
    "sd1.5": "sd15",
    "sd_1_5": "sd15",
    "stable_diffusion_1_5": "sd15",
    "sd_xl": "sdxl",
    "flux1": "flux",
    "flux_1": "flux",
    "flux.1": "flux",
    "klein": "flux2_klein",
    "flux2": "flux2_klein",
    "flux_2_klein": "flux2_klein",
    "krea2_raw": "krea2",
    "krea2_base": "krea2",
    "zimage": "z_image",
    "zimage_turbo": "z_image_turbo",
    "qwen": "qwen_image",
    "qwen_2509": "qwen_image_edit_2509",
    "qwen_2511": "qwen_image_edit_2511",
}


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def normalize_family(value: Any) -> str:
    token = _token(value)
    return FAMILY_ALIASES.get(token, token)


def normalize_mode(value: Any) -> str:
    token = _token(value or "txt2img")
    return MODE_ALIASES.get(token, token)


def normalize_loader(value: Any) -> str:
    return _token(value)


@lru_cache(maxsize=1)
def load_sampling_guidance_registry() -> dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unexpected sampling/guidance registry schema: {payload.get('schema')!r}")
    return payload


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _model_suggests_flux1_krea(model_name: Any) -> bool:
    return "krea" in str(model_name or "").casefold()


def _normalize_variant(family: str, variant: Any, model_name: Any = None) -> str:
    requested = _token(variant)
    registry = load_sampling_guidance_registry()
    family_payload = (registry.get("families") or {}).get(family) or {}
    variants = family_payload.get("variants") or {}

    if family == "flux" and not requested:
        return "krea_dev" if _model_suggests_flux1_krea(model_name) else "dev"

    for variant_id, payload in variants.items():
        aliases = {_token(variant_id)} | {_token(v) for v in (payload.get("aliases") or [])}
        if requested and requested in aliases:
            return variant_id

    if requested:
        return requested
    if family == "flux":
        return "dev"
    return ""


def _route_override_matches(override: dict[str, Any], *, loader: str, mode: str, variant: str) -> bool:
    match = override.get("match") if isinstance(override, dict) else None
    if not isinstance(match, dict):
        return False
    if match.get("loader") and normalize_loader(match.get("loader")) != loader:
        return False
    modes = {normalize_mode(value) for value in (match.get("modes") or [])}
    if modes and mode not in modes:
        return False
    variants = {_token(value) for value in (match.get("variants") or [])}
    if variants and _token(variant) not in variants:
        return False
    return True


def _unknown_capability(*, family: str, loader: str, mode: str, variant: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "known_family": False,
        "resolved": False,
        "family": family,
        "variant": variant,
        "loader": loader,
        "mode": mode,
        "visible": False,
        "route_context_supported": False,
        "sampling": {
            "sampler_control": "provider_profile",
            "scheduler_control": "provider_profile",
            "steps_control": "provider_profile",
            "sampler_cfg": {"kind": "provider_profile", "field": None, "ui_label": None},
        },
        "guidance": {"kind": "provider_profile", "field": None, "ui_label": None},
        "negative_prompt": {
            "policy": "profile_controlled",
            "activation_field": None,
            "hard_min_exclusive": None,
            "weak_below": None,
            "graph_semantics": "unknown_family_fail_closed",
        },
        "resolution_policy": "provider_profile",
        "denoise_policy": "provider_profile",
        "warnings": ["Unknown family: capability semantics fail closed to provider/profile ownership."],
    }


def resolve_sampling_guidance_capability(
    family: Any,
    *,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> dict[str, Any]:
    """Resolve Image sampling/guidance semantics without choosing numeric presets.

    IP-1 deliberately does not decide route availability, best sampler defaults,
    or whether a negative prompt is active for a particular numeric CFG value.
    Those responsibilities stay with the route matrix, IP-4/IP-5 presets, and
    the later IP-2 eligibility engine respectively.
    """

    registry = load_sampling_guidance_registry()
    family_id = normalize_family(family)
    loader_id = normalize_loader(loader)
    mode_id = normalize_mode(mode)
    family_payload = deepcopy((registry.get("families") or {}).get(family_id) or {})
    variant_id = _normalize_variant(family_id, variant, model_name)

    if not family_payload:
        return _unknown_capability(family=family_id, loader=loader_id, mode=mode_id, variant=variant_id)

    resolved = deepcopy(family_payload)
    variant_payload = (family_payload.get("variants") or {}).get(variant_id)
    if isinstance(variant_payload, dict):
        resolved = _deep_merge(resolved, {k: v for k, v in variant_payload.items() if k not in {"aliases"}})

    matched_overrides: list[dict[str, Any]] = []
    for override in family_payload.get("route_overrides") or []:
        if _route_override_matches(override, loader=loader_id, mode=mode_id, variant=variant_id):
            payload = {key: value for key, value in override.items() if key != "match"}
            resolved = _deep_merge(resolved, payload)
            matched_overrides.append(deepcopy(override.get("match") or {}))

    declared_loaders = {normalize_loader(value) for value in (family_payload.get("loaders") or [])}
    declared_modes = {normalize_mode(value) for value in (family_payload.get("modes") or [])}
    route_context_supported = (not loader_id or loader_id in declared_loaders) and mode_id in declared_modes

    resolution_policy = (resolved.get("resolution") or {}).get(mode_id, "provider_profile")
    denoise_policy = (resolved.get("denoise") or {}).get(mode_id, "provider_profile")
    warnings: list[str] = []
    if loader_id and loader_id not in declared_loaders:
        warnings.append(f"Loader {loader_id!r} is outside this family's declared capability semantics.")
    if mode_id not in declared_modes:
        warnings.append(f"Mode {mode_id!r} is outside this family's declared capability semantics.")
    if variant_id and family_payload.get("variants") and variant_id not in (family_payload.get("variants") or {}):
        warnings.append(f"Variant {variant_id!r} is not registered for family {family_id!r}; base family semantics were used.")

    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "known_family": True,
        "resolved": True,
        "family": family_id,
        "display_name": family_payload.get("display_name") or family_id,
        "variant": variant_id,
        "loader": loader_id,
        "mode": mode_id,
        "visible": bool(family_payload.get("visible", True)),
        "route_context_supported": route_context_supported,
        "route_availability_owned_elsewhere": True,
        "sampling": deepcopy(resolved.get("sampling") or {}),
        "guidance": deepcopy(resolved.get("guidance") or {}),
        "negative_prompt": deepcopy(resolved.get("negative_prompt") or {}),
        "resolution_policy": resolution_policy,
        "denoise_policy": denoise_policy,
        "route_semantic": resolved.get("route_semantic"),
        "matched_route_overrides": matched_overrides,
        "source_contracts": list(resolved.get("source_contracts") or []),
        "warnings": warnings,
    }


def list_sampling_guidance_families(*, visible_only: bool = False) -> list[str]:
    families = load_sampling_guidance_registry().get("families") or {}
    if not visible_only:
        return sorted(families)
    return sorted(family for family, payload in families.items() if bool((payload or {}).get("visible", True)))


def registry_contract() -> dict[str, Any]:
    payload = load_sampling_guidance_registry()
    return {
        "schema": payload.get("schema"),
        "phase": payload.get("phase"),
        "version": payload.get("version"),
        "purpose": payload.get("purpose"),
        "global_contract": deepcopy(payload.get("global_contract") or {}),
        "family_count": len(payload.get("families") or {}),
    }
