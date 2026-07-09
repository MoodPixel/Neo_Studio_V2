from __future__ import annotations

from copy import deepcopy
from typing import Any

from .support_matrix import graph_patch_strategy, normalize_workflow_mode, route_support

PATCH_PROFILE_SCHEMA_VERSION = "neo.image.lora_stack.patch_profile.v1"
DEFAULT_LOADER_NODE_CLASS = "LoraLoader"
MODEL_ONLY_LOADER_NODE_CLASS = "LoraLoaderModelOnly"
MODEL_ONLY_STRATEGIES = {"lora_loader_model_only_chain", "lora_loader_model_only_consumer_rewire"}
MODEL_CLIP_STRATEGIES = {"lora_loader_model_clip_chain", "lora_loader_model_clip_consumer_rewire"}
NO_LOADER_STRATEGIES = {"provider_specific", "none"}


def _default_loader_for_strategy(strategy: str, fallback: str | None = DEFAULT_LOADER_NODE_CLASS) -> str:
    if strategy in MODEL_ONLY_STRATEGIES:
        return MODEL_ONLY_LOADER_NODE_CLASS
    if strategy in MODEL_CLIP_STRATEGIES:
        return DEFAULT_LOADER_NODE_CLASS
    if strategy in NO_LOADER_STRATEGIES:
        return str(fallback or "")
    return str(fallback or DEFAULT_LOADER_NODE_CLASS)


def _clean_ref(ref: Any) -> list[Any] | None:
    if not isinstance(ref, (list, tuple)) or len(ref) < 2:
        return None
    node_id = str(ref[0]).strip()
    if not node_id:
        return None
    index = ref[1]
    if isinstance(index, str):
        if not index.strip():
            return None
        if index.strip().isdigit():
            index = int(index.strip())
    if not isinstance(index, int):
        return None
    return [node_id, index]


def _clean_route(route: dict[str, Any] | None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    backend = str(route.get("backend") or route.get("provider_id") or "comfyui")
    family = str(route.get("family") or "")
    loader = str(route.get("loader") or "")
    mode = normalize_workflow_mode(str(route.get("workflow_mode") or route.get("mode") or "generate"))
    return {
        "backend": backend,
        "family": family,
        "loader": loader,
        "workflow_mode": mode,
        "mode": mode,
        "route_key": str(route.get("route_key") or f"{family}:{loader}:{mode}"),
        "route_state": str(route.get("route_state") or route.get("state") or "unknown"),
    }


def build_lora_patch_profile(
    *,
    route: dict[str, Any] | None = None,
    model_ref: list[Any] | tuple[Any, ...] | None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int | None = None,
    sampler_model_input: str | None = "model",
    loader_node_class: str | None = DEFAULT_LOADER_NODE_CLASS,
    requires_model: bool = True,
    requires_clip: bool = True,
    source: str = "compiler",
    strategy: str | None = None,
    patch_model_consumers: bool = True,
    patch_clip_consumers: bool = True,
    validated: bool = False,
    notes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the compiler-owned LoRA graph patch contract.

    The compiler that creates a Comfy graph owns these refs. LoRA Stack may use
    the profile; it must not invent a family graph shape when a profile is
    required by the route matrix.
    """
    clean_route = _clean_route(route)
    support = route_support(clean_route["backend"], clean_route["family"], clean_route["loader"], clean_route["workflow_mode"])
    cleaned_model_ref = _clean_ref(model_ref)
    cleaned_clip_ref = _clean_ref(clip_ref)
    resolved_requires_model = bool(requires_model if requires_model is not None else support.get("requires_model", True))
    resolved_requires_clip = bool(requires_clip if requires_clip is not None else support.get("requires_clip", True))
    resolved_strategy = str(strategy or support.get("graph_patch") or graph_patch_strategy(clean_route["backend"], clean_route["family"], clean_route["loader"], clean_route["workflow_mode"]) or "none")
    resolved_loader = _default_loader_for_strategy(resolved_strategy, loader_node_class or support.get("loader_node_class") or DEFAULT_LOADER_NODE_CLASS)
    return {
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "source": str(source or "compiler"),
        "route": clean_route,
        "route_key": clean_route["route_key"],
        "strategy": resolved_strategy,
        "loader_node_class": resolved_loader,
        "requires_model": resolved_requires_model,
        "requires_clip": resolved_requires_clip,
        "model_ref": cleaned_model_ref or [],
        "clip_ref": cleaned_clip_ref or [],
        "sampler_node_id": str(sampler_node_id or ""),
        "sampler_model_input": str(sampler_model_input or "model"),
        "patch_model_consumers": bool(patch_model_consumers),
        "patch_clip_consumers": bool(patch_clip_consumers),
        "validated": bool(validated),
        "notes": [str(item) for item in (notes or []) if str(item).strip()],
    }


def normalize_lora_patch_profile(profile: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_route = _clean_route(route)
    support = route_support(clean_route["backend"], clean_route["family"], clean_route["loader"], clean_route["workflow_mode"])
    required = bool(support.get("patch_profile_required"))
    if not isinstance(profile, dict) or not profile:
        return {
            "valid": False,
            "missing": True,
            "required": required,
            "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
            "reason": "missing_lora_patch_profile",
            "route": clean_route,
            "route_support": support,
            "profile": {},
        }

    raw_route = profile.get("route") if isinstance(profile.get("route"), dict) else clean_route
    profile_route = {**clean_route, **_clean_route(raw_route)}
    # Route passed to the patch hook is authoritative; the embedded profile route
    # is diagnostic only. Keep the hook route to avoid stale copied profiles
    # accidentally retargeting another family/loader/mode.
    profile_route.update(clean_route)
    model_ref = _clean_ref(profile.get("model_ref"))
    clip_ref = _clean_ref(profile.get("clip_ref"))
    requires_model = bool(profile.get("requires_model", support.get("requires_model", True)))
    requires_clip = bool(profile.get("requires_clip", support.get("requires_clip", True)))
    strategy = str(profile.get("strategy") or support.get("graph_patch") or graph_patch_strategy(profile_route["backend"], profile_route["family"], profile_route["loader"], profile_route["workflow_mode"]) or "none")
    loader_node_class = _default_loader_for_strategy(strategy, profile.get("loader_node_class") or support.get("loader_node_class") or DEFAULT_LOADER_NODE_CLASS)
    errors: list[str] = []
    if requires_model and not model_ref:
        errors.append("model_ref_missing")
    if requires_clip and not clip_ref:
        errors.append("clip_ref_missing")
    if not loader_node_class:
        errors.append("loader_node_class_missing")
    normalized_profile = {
        "schema_version": str(profile.get("schema_version") or PATCH_PROFILE_SCHEMA_VERSION),
        "source": str(profile.get("source") or "compiler"),
        "route": profile_route,
        "route_key": str(profile.get("route_key") or profile_route["route_key"]),
        "strategy": strategy,
        "loader_node_class": loader_node_class,
        "requires_model": requires_model,
        "requires_clip": requires_clip,
        "model_ref": model_ref or [],
        "clip_ref": clip_ref or [],
        "sampler_node_id": str(profile.get("sampler_node_id") or ""),
        "sampler_model_input": str(profile.get("sampler_model_input") or "model"),
        "patch_model_consumers": bool(profile.get("patch_model_consumers", True)),
        "patch_clip_consumers": bool(profile.get("patch_clip_consumers", True)),
        "validated": bool(profile.get("validated", False)),
        "notes": [str(item) for item in profile.get("notes", [])] if isinstance(profile.get("notes", []), list) else [],
    }
    return {
        "valid": not errors,
        "missing": False,
        "required": required,
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "reason": ",".join(errors) if errors else "ok",
        "errors": errors,
        "route": profile_route,
        "route_support": support,
        "profile": normalized_profile,
    }


def profile_metadata(profile_result: dict[str, Any] | None) -> dict[str, Any]:
    result = profile_result if isinstance(profile_result, dict) else {}
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    return {
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "valid": bool(result.get("valid")),
        "missing": bool(result.get("missing")),
        "required": bool(result.get("required")),
        "reason": str(result.get("reason") or ""),
        "source": str(profile.get("source") or ""),
        "strategy": str(profile.get("strategy") or ""),
        "loader_node_class": str(profile.get("loader_node_class") or ""),
        "requires_model": bool(profile.get("requires_model", True)),
        "requires_clip": bool(profile.get("requires_clip", True)),
        "model_ref": deepcopy(profile.get("model_ref") or []),
        "clip_ref": deepcopy(profile.get("clip_ref") or []),
        "sampler_node_id": str(profile.get("sampler_node_id") or ""),
        "sampler_model_input": str(profile.get("sampler_model_input") or "model"),
        "patch_model_consumers": bool(profile.get("patch_model_consumers", True)),
        "patch_clip_consumers": bool(profile.get("patch_clip_consumers", True)),
    }
