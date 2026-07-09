from __future__ import annotations

from copy import deepcopy
from typing import Any

from .payload_schema import EXTENSION_ID
from .patch_profile import build_lora_patch_profile, normalize_lora_patch_profile, profile_metadata
from .validation import validate_and_normalize_payload

PHASE = "L6"
LORA_LOADER_NODE = "LoraLoader"
LORA_MODEL_ONLY_NODE = "LoraLoaderModelOnly"

MODEL_CLIP_STRATEGIES = {
    "lora_loader_model_clip_chain",
    "lora_loader_model_clip_consumer_rewire",
}
MODEL_ONLY_STRATEGIES = {
    "lora_loader_model_only_chain",
    "lora_loader_model_only_consumer_rewire",
}
PROVIDER_SPECIFIC_STRATEGY = "provider_specific"
NO_PATCH_STRATEGY = "none"
SUPPORTED_PATCH_STRATEGIES = MODEL_CLIP_STRATEGIES | MODEL_ONLY_STRATEGIES | {PROVIDER_SPECIFIC_STRATEGY, NO_PATCH_STRATEGY}
STRATEGY_DEFAULT_NODE_CLASS = {
    "lora_loader_model_clip_chain": LORA_LOADER_NODE,
    "lora_loader_model_clip_consumer_rewire": LORA_LOADER_NODE,
    "lora_loader_model_only_chain": LORA_MODEL_ONLY_NODE,
    "lora_loader_model_only_consumer_rewire": LORA_MODEL_ONLY_NODE,
}


def _next_graph_id(workflow: dict[str, Any], preferred: int | str | None = None) -> str:
    if preferred is not None:
        candidate = str(preferred)
        if candidate not in workflow:
            return candidate
    numeric_ids: list[int] = []
    for key in workflow:
        try:
            numeric_ids.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str((max(numeric_ids) if numeric_ids else 0) + 1)


def _copy_ref(ref: list[Any] | tuple[Any, ...] | None, fallback: list[Any]) -> list[Any]:
    if isinstance(ref, (list, tuple)) and len(ref) >= 2:
        index = ref[1]
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        return [str(ref[0]), index]
    return deepcopy(fallback)


def _node_names(available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None) -> set[str]:
    if isinstance(available_nodes, dict):
        return {str(key) for key in available_nodes.keys()}
    if isinstance(available_nodes, (set, list, tuple)):
        return {str(item) for item in available_nodes}
    return set()


def _loader_node_available(
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None,
    loader_node_class: str | None = None,
) -> bool:
    names = _node_names(available_nodes)
    # Some unit-level callers do not have Comfy object_info. Treat None as unknown,
    # but an explicit empty set from the provider means object_info did not expose it.
    if available_nodes is None:
        return True
    return str(loader_node_class or LORA_LOADER_NODE) in names


def _target_applies_to_base(row: dict[str, Any]) -> bool:
    return str(row.get("target") or "both") in {"both", "base"}


def _global_base_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if str(row.get("apply_to") or "global") == "global" and _target_applies_to_base(row)
    ]


def _refs_equal(current: Any, expected: list[Any]) -> bool:
    return current == expected or (isinstance(current, (list, tuple)) and list(current) == expected)


def _patch_clip_encode_nodes(
    graph: dict[str, Any],
    *,
    original_clip_ref: list[Any],
    patched_clip_ref: list[Any],
    skip_node_ids: set[str] | None = None,
) -> list[str]:
    patched_nodes: list[str] = []
    skip_node_ids = set(skip_node_ids or set())
    if not original_clip_ref or not patched_clip_ref:
        return patched_nodes
    for node_id, node in graph.items():
        node_key = str(node_id)
        if node_key in skip_node_ids or not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        current = inputs.get("clip")
        # Family-specific encoders such as CLIPTextEncodeFlux and
        # TextEncodeQwenImageEditPlus still expose a clip input. Patch by
        # contract instead of by class name so LoRA Stack can follow the
        # active compiler graph without family fallback assumptions.
        if _refs_equal(current, original_clip_ref):
            inputs["clip"] = deepcopy(patched_clip_ref)
            patched_nodes.append(str(node_id))
    return patched_nodes


def _patch_model_consumer_nodes(
    graph: dict[str, Any],
    *,
    original_model_ref: list[Any],
    patched_model_ref: list[Any],
    skip_node_ids: set[str] | None = None,
) -> list[str]:
    patched_nodes: list[str] = []
    skip_node_ids = set(skip_node_ids or set())
    if not original_model_ref or not patched_model_ref:
        return patched_nodes
    for node_id, node in graph.items():
        node_key = str(node_id)
        if node_key in skip_node_ids or not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        current = inputs.get("model")
        if _refs_equal(current, original_model_ref):
            inputs["model"] = deepcopy(patched_model_ref)
            patched_nodes.append(node_key)
    return patched_nodes


def _normalize_patch_strategy(profile: dict[str, Any]) -> str:
    strategy = str(profile.get("strategy") or "").strip() or NO_PATCH_STRATEGY
    if strategy in SUPPORTED_PATCH_STRATEGIES:
        return strategy
    return strategy


def _strategy_requires_clip(strategy: str, profile: dict[str, Any]) -> bool:
    if strategy in MODEL_ONLY_STRATEGIES:
        return False
    return bool(profile.get("requires_clip", True))


def _strategy_loader_node_class(strategy: str, profile: dict[str, Any]) -> str:
    return str(profile.get("loader_node_class") or STRATEGY_DEFAULT_NODE_CLASS.get(strategy) or LORA_LOADER_NODE)


def _strategy_kind(strategy: str) -> str:
    if strategy in MODEL_CLIP_STRATEGIES:
        return "model_clip"
    if strategy in MODEL_ONLY_STRATEGIES:
        return "model_only"
    if strategy == PROVIDER_SPECIFIC_STRATEGY:
        return "provider_specific"
    return "none"


def _build_lora_node_inputs(
    *,
    strategy: str,
    current_model_ref: list[Any],
    current_clip_ref: list[Any],
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[Any], list[Any]]:
    strength = float(row.get("strength", 0.8))
    if strategy in MODEL_ONLY_STRATEGIES:
        return (
            {
                "model": deepcopy(current_model_ref),
                "lora_name": str(row.get("name") or ""),
                "strength_model": strength,
            },
            [],
            deepcopy(current_clip_ref),
        )
    return (
        {
            "model": deepcopy(current_model_ref),
            "clip": deepcopy(current_clip_ref),
            "lora_name": str(row.get("name") or ""),
            "strength_model": strength,
            "strength_clip": strength,
        },
        [],
        [],
    )


def build_workflow_patch_summary(
    *,
    validation_result: dict[str, Any],
    applied_rows: list[dict[str, Any]] | None = None,
    lora_node_ids: list[str] | None = None,
    previous_model_ref: list[Any] | None = None,
    previous_clip_ref: list[Any] | None = None,
    patched_model_ref: list[Any] | None = None,
    patched_clip_ref: list[Any] | None = None,
    sampler_node_id: str | None = None,
    sampler_model_input: str = "model",
    patched_clip_encode_nodes: list[str] | None = None,
    patched_model_consumer_nodes: list[str] | None = None,
    patch_profile: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    route = validation_result.get("route") if isinstance(validation_result, dict) else {}
    applied_rows = deepcopy(applied_rows or [])
    lora_node_ids = list(lora_node_ids or [])
    profile_meta = profile_metadata(patch_profile)
    strategy = str(profile_meta.get("strategy") or "")
    loader_node_class = profile_meta.get("loader_node_class") or STRATEGY_DEFAULT_NODE_CLASS.get(strategy) or LORA_LOADER_NODE
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "applied": bool(applied_rows and lora_node_ids),
        "node": loader_node_class if applied_rows else "",
        "node_class": loader_node_class if applied_rows else "",
        "node_ids": lora_node_ids,
        "lora_count": len(applied_rows),
        "lora_names": [str(row.get("name") or "") for row in applied_rows],
        "previous_model_ref": deepcopy(previous_model_ref or []),
        "previous_clip_ref": deepcopy(previous_clip_ref or []),
        "patched_model_ref": deepcopy(patched_model_ref or previous_model_ref or []),
        "patched_clip_ref": deepcopy(patched_clip_ref or previous_clip_ref or []),
        "sampler_node_id": str(sampler_node_id or ""),
        "sampler_model_input": str(sampler_model_input or "model"),
        "patch_profile": profile_meta,
        "patch_profile_required": bool(profile_meta.get("required")),
        "patch_profile_source": str(profile_meta.get("source") or ""),
        "patch_strategy": strategy,
        "patch_strategy_kind": _strategy_kind(strategy),
        "patch_strategy_supported": strategy in SUPPORTED_PATCH_STRATEGIES,
        "patched_clip_encode_nodes": list(patched_clip_encode_nodes or []),
        "patched_model_consumer_nodes": list(patched_model_consumer_nodes or []),
        "route": deepcopy(route or {}),
        "reason": reason,
    }


def apply_lora_stack_patch(
    workflow: dict[str, Any],
    payload: dict[str, Any] | None,
    route: dict[str, Any] | None = None,
    *,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None = None,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    sampler_model_input: str = "model",
    next_node_id: int | str | None = None,
    lora_patch_profile: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate and patch a Comfy graph with global base-pass LoRA rows.

    L5/L6 uses the upgraded patcher from a single hardcoded LoraLoader branch into a
    strategy dispatcher. Routes may use the standard model+clip LoraLoader path,
    a model-only LoraLoaderModelOnly path, or explicit provider-specific/no-op
    strategies that preserve payload intent without mutating the graph.
    """
    graph = deepcopy(workflow or {})
    validation = validate_and_normalize_payload(payload, route=route, available_nodes=available_nodes)
    route_ctx = validation.get("route") if isinstance(validation.get("route"), dict) else (route or {})
    profile_result = normalize_lora_patch_profile(lora_patch_profile, route=route_ctx)
    if not profile_result.get("valid") and not profile_result.get("required") and model_ref is not None:
        # Backward-compatible bridge for checkpoint compiler tests and any older
        # checkpoint hook caller that passes explicit refs. Non-checkpoint routes
        # that require a compiler profile still gate below instead of guessing.
        legacy_profile = build_lora_patch_profile(
            route=route_ctx,
            model_ref=model_ref,
            clip_ref=clip_ref,
            sampler_node_id=sampler_node_id,
            sampler_model_input=sampler_model_input,
            source="legacy_hook_refs",
            validated=False,
            notes=["Fallback profile built from explicit hook refs; route does not require a compiler-owned profile."],
        )
        profile_result = normalize_lora_patch_profile(legacy_profile, route=route_ctx)
    profile = profile_result.get("profile") if isinstance(profile_result.get("profile"), dict) else {}
    strategy = _normalize_patch_strategy(profile)
    previous_model_ref = _copy_ref(profile.get("model_ref"), _copy_ref(model_ref, []))
    previous_clip_ref = _copy_ref(profile.get("clip_ref"), _copy_ref(clip_ref, []))
    sampler_key = str(profile.get("sampler_node_id") or sampler_node_id or "")
    resolved_sampler_model_input = str(profile.get("sampler_model_input") or sampler_model_input or "model")
    loader_node_class = _strategy_loader_node_class(strategy, profile)

    # Keep profile metadata aligned with the strategy dispatcher even for legacy
    # profiles whose L4 payload did not know about the L5 default node classes.
    if isinstance(profile, dict):
        profile["strategy"] = strategy
        profile["loader_node_class"] = loader_node_class
        profile["requires_clip"] = _strategy_requires_clip(strategy, profile)
        profile_result["profile"] = profile

    def no_patch(reason: str) -> dict[str, Any]:
        patch = build_workflow_patch_summary(
            validation_result=validation,
            previous_model_ref=previous_model_ref,
            previous_clip_ref=previous_clip_ref,
            patched_model_ref=previous_model_ref,
            patched_clip_ref=previous_clip_ref,
            sampler_node_id=sampler_key,
            sampler_model_input=resolved_sampler_model_input,
            patch_profile=profile_result,
            reason=reason,
        )
        return {
            "workflow": graph,
            "model_ref": previous_model_ref,
            "clip_ref": previous_clip_ref,
            "validation": validation,
            "workflow_patch": patch,
            "mutated": False,
            "changed": False,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": (validation.get("route") or {}).get("route_state"),
            "gated_reason": reason,
        }

    if not validation.get("workflow_patch_allowed"):
        metadata = (validation.get("block") or {}).get("metadata") or {}
        return no_patch(str(metadata.get("reason") or "disabled_or_route_gated"))

    if not profile_result.get("valid"):
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile",
            "message": "LoRA Stack workflow patch was gated because the compiler did not emit a valid LoRA patch profile.",
            "reason": str(profile_result.get("reason") or "invalid_lora_patch_profile"),
        })
        validation["workflow_patch_allowed"] = False
        reason = "compiler_lora_patch_profile_required" if profile_result.get("required") else "invalid_lora_patch_profile"
        return no_patch(f"{reason}: {profile_result.get('reason') or 'missing_lora_patch_profile'}")

    if strategy == NO_PATCH_STRATEGY:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.strategy",
            "message": "LoRA Stack profile selected the no-op strategy; payload intent was preserved without graph mutation.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch("strategy_none: LoRA Stack graph patching is disabled for this route")

    if strategy == PROVIDER_SPECIFIC_STRATEGY:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.strategy",
            "message": "LoRA Stack provider-specific strategy is reserved for a dedicated compiler adapter; payload intent was preserved without generic graph mutation.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch("provider_specific_strategy_requires_adapter")

    if strategy not in SUPPORTED_PATCH_STRATEGIES:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.strategy",
            "message": f"LoRA Stack does not recognize patch strategy '{strategy}'.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch(f"unsupported_lora_patch_strategy: {strategy}")

    if loader_node_class not in {LORA_LOADER_NODE, LORA_MODEL_ONLY_NODE}:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.loader_node_class",
            "message": f"LoRA Stack L6 only supports {LORA_LOADER_NODE} and {LORA_MODEL_ONLY_NODE}; {loader_node_class} requires a provider-specific adapter.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch(f"unsupported_loader_node_class_for_l5: {loader_node_class}")

    if strategy in MODEL_CLIP_STRATEGIES and loader_node_class != LORA_LOADER_NODE:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.loader_node_class",
            "message": "Model+clip LoRA strategies require the standard Comfy LoraLoader node.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch(f"loader_strategy_mismatch: {strategy} requires {LORA_LOADER_NODE}")

    if strategy in MODEL_ONLY_STRATEGIES and loader_node_class != LORA_MODEL_ONLY_NODE:
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "lora_patch_profile.loader_node_class",
            "message": "Model-only LoRA strategies require the standard Comfy LoraLoaderModelOnly node.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch(f"loader_strategy_mismatch: {strategy} requires {LORA_MODEL_ONLY_NODE}")

    if not _loader_node_available(available_nodes, loader_node_class):
        validation.setdefault("validation", []).append({
            "level": "warning",
            "field": "available_nodes",
            "message": f"Comfy object_info did not expose {loader_node_class}; LoRA Stack workflow patch was provider-gated.",
        })
        validation["workflow_patch_allowed"] = False
        return no_patch(f"provider_gated: Comfy {loader_node_class} node is not available")

    rows = ((validation.get("block") or {}).get("params") or {}).get("loras") or []
    if not isinstance(rows, list):
        rows = []
    patch_rows = _global_base_rows(rows)
    if not patch_rows:
        deferred = len(rows)
        return no_patch(f"no_global_base_loras: {deferred} row(s) were regional or finish-only and were preserved without mutating the base graph")

    current_model_ref = deepcopy(previous_model_ref)
    current_clip_ref = deepcopy(previous_clip_ref)
    lora_node_ids: list[str] = []
    next_id: int | None = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None

    for row in patch_rows:
        node_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(node_id) + 1
        except (TypeError, ValueError):
            next_id = None
        inputs, _, _ = _build_lora_node_inputs(
            strategy=strategy,
            current_model_ref=current_model_ref,
            current_clip_ref=current_clip_ref,
            row=row,
        )
        graph[node_id] = {
            "class_type": loader_node_class,
            "inputs": inputs,
        }
        current_model_ref = [node_id, 0]
        if strategy in MODEL_CLIP_STRATEGIES:
            current_clip_ref = [node_id, 1]
        lora_node_ids.append(node_id)

    patched_model_nodes: list[str] = []
    if bool(profile.get("patch_model_consumers", True)):
        patched_model_nodes = _patch_model_consumer_nodes(
            graph,
            original_model_ref=previous_model_ref,
            patched_model_ref=current_model_ref,
            skip_node_ids=set(lora_node_ids),
        )
    # Backward-compatible direct sampler patch for checkpoint graphs or callers
    # that still pass a concrete sampler target. The generic model-consumer patch
    # above is what enables Flux/Qwen/Z-Image/HiDream compiler-specific model
    # sampling nodes without hardcoding family fallbacks.
    sampler = graph.get(sampler_key)
    if isinstance(sampler, dict):
        sampler_inputs = sampler.setdefault("inputs", {})
        if isinstance(sampler_inputs, dict) and _refs_equal(sampler_inputs.get(resolved_sampler_model_input), previous_model_ref):
            sampler_inputs[resolved_sampler_model_input] = deepcopy(current_model_ref)
            if sampler_key not in patched_model_nodes:
                patched_model_nodes.append(sampler_key)

    patched_clip_nodes: list[str] = []
    if strategy in MODEL_CLIP_STRATEGIES and bool(profile.get("patch_clip_consumers", True)):
        patched_clip_nodes = _patch_clip_encode_nodes(
            graph,
            original_clip_ref=previous_clip_ref,
            patched_clip_ref=current_clip_ref,
            skip_node_ids=set(lora_node_ids),
        )
    reason = f"Applied ordered global base-pass LoRA stack using {strategy} via {loader_node_class}."
    if len(patch_rows) != len(rows):
        reason += f" {len(rows) - len(patch_rows)} regional/finish row(s) preserved but not graph-patched in Phase L6."
    patch = build_workflow_patch_summary(
        validation_result=validation,
        applied_rows=patch_rows,
        lora_node_ids=lora_node_ids,
        previous_model_ref=previous_model_ref,
        previous_clip_ref=previous_clip_ref,
        patched_model_ref=current_model_ref,
        patched_clip_ref=current_clip_ref,
        sampler_node_id=sampler_key,
        sampler_model_input=resolved_sampler_model_input,
        patch_profile=profile_result,
        patched_clip_encode_nodes=patched_clip_nodes,
        patched_model_consumer_nodes=patched_model_nodes,
        reason=reason,
    )
    patch["applied"] = True

    return {
        "workflow": graph,
        "model_ref": current_model_ref,
        "clip_ref": current_clip_ref,
        "validation": validation,
        "workflow_patch": patch,
        "mutated": True,
        "changed": True,
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "route_state": (validation.get("route") or {}).get("route_state"),
        "gated_reason": "",
    }


def workflow_patch_not_implemented() -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "implemented": True,
        "phase": PHASE,
        "node": LORA_LOADER_NODE,
        "node_classes": [LORA_LOADER_NODE, LORA_MODEL_ONLY_NODE],
        "strategies": sorted(SUPPORTED_PATCH_STRATEGIES),
        "reason": "L6 keeps the L5 strategy patcher and uses family-by-family route matrix enablement for compiler-profile-backed LoRA routes.",
    }
