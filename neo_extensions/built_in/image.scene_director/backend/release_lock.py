from __future__ import annotations

"""SD-28.7 release-lock invariants for Scene Director.

The release lock is deliberately graph-contract based.  It does not claim GPU
spatial isolation; it prevents a compiled Scene Director graph from escaping the
architecture validated by SD-28.1 through SD-28.6.
"""

from copy import deepcopy
from typing import Any

from .execution_strategy import (
    ENGINE_CLASSIC_V054,
    ENGINE_LIGHTWEIGHT_REGIONAL,
    MODERN_FAMILY_LOADERS,
    MODERN_LIGHTWEIGHT_FAMILIES,
    SUPPORTED_EXECUTION_MODES,
    resolve_scene_director_execution_strategy,
)

RELEASE_LOCK_PHASE = "SD-28.7"
RELEASE_LOCK_SCHEMA = "neo.image.scene_director.release_lock.v1"

_FORBIDDEN_MODERN_INSERTIONS = {
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustom",
    "SamplerCustomAdvanced",
    "LoraLoader",
    "LoraLoaderModelOnly",
    "NeoSceneDirectorV054",
}


def _check(check_id: str, ok: bool, detail: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "id": check_id,
        "ok": bool(ok),
        "blocking": bool(blocking),
        "level": "ok" if ok else ("blocker" if blocking else "warning"),
        "detail": str(detail),
    }


def _node_classes_for_ids(workflow: dict[str, Any], node_ids: list[Any]) -> list[str]:
    classes: list[str] = []
    for node_id in node_ids:
        node = workflow.get(str(node_id))
        if isinstance(node, dict):
            class_type = str(node.get("class_type") or "")
            if class_type:
                classes.append(class_type)
    return classes


def _normalized_route(strategy: dict[str, Any]) -> dict[str, str]:
    route = strategy.get("route") if isinstance(strategy.get("route"), dict) else {}
    return {
        "backend": str(route.get("backend") or ""),
        "family": str(route.get("family") or strategy.get("family") or ""),
        "loader": str(route.get("loader") or strategy.get("loader") or ""),
        "mode": str(route.get("mode") or strategy.get("mode") or ""),
    }


def evaluate_scene_director_release_lock(
    *,
    before_workflow: dict[str, Any] | None,
    result: dict[str, Any] | None,
    route: dict[str, Any] | None,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate compile-time invariants without inventing runtime GPU proof."""

    before = before_workflow if isinstance(before_workflow, dict) else {}
    result = result if isinstance(result, dict) else {}
    workflow = result.get("workflow") if isinstance(result.get("workflow"), dict) else before
    patch = result.get("workflow_patch") if isinstance(result.get("workflow_patch"), dict) else {}
    resolved = deepcopy(strategy) if isinstance(strategy, dict) else resolve_scene_director_execution_strategy(route or {})
    engine = str(resolved.get("engine") or "unsupported")
    normalized = _normalized_route(resolved)
    mutated = bool(result.get("mutated") or patch.get("mutated") or patch.get("applied"))
    checks: list[dict[str, Any]] = []

    # Unsupported/planned routes are safe only when Scene Director leaves the graph untouched.
    if not resolved.get("execution_enabled"):
        checks.append(_check(
            "gated_route_no_mutation",
            not mutated,
            "Gated/unsupported routes must leave the provider graph unchanged.",
        ))
        blockers = [c for c in checks if c["blocking"] and not c["ok"]]
        return {
            "schema": RELEASE_LOCK_SCHEMA,
            "phase": RELEASE_LOCK_PHASE,
            "status": "blocked" if blockers else "gated_safe",
            "locked": not blockers,
            "allow_output": not blockers,
            "engine": engine,
            "route": normalized,
            "checks": checks,
            "blockers": blockers,
            "warnings": [],
            "gpu_proof_required": False,
            "gpu_proven": False,
            "reason": blockers[0]["detail"] if blockers else "Route is gated and Scene Director did not mutate the graph.",
        }

    checks.append(_check(
        "engine_boundary",
        engine in {ENGINE_CLASSIC_V054, ENGINE_LIGHTWEIGHT_REGIONAL},
        "Executable routes must resolve to an explicit Scene Director engine.",
    ))

    if engine == ENGINE_CLASSIC_V054:
        family = normalized["family"]
        loader = normalized["loader"]
        checks.extend([
            _check("classic_checkpoint_only", loader == "checkpoint", "Classic V054 release routes remain checkpoint-only."),
            _check("classic_family_boundary", family in {"sdxl", "sdxl_sd", "sd", "sd15", "sd1.5", "sd_1_5", "sd1_5", "stable_diffusion_1_5"}, "Classic V054 is frozen to SDXL/SD1.5 families."),
            _check("classic_no_modern_wrapper", "NeoRegionalLoRADelta" not in _node_classes_for_ids(workflow, list(patch.get("nodes_added") or [])), "Classic V054 must not acquire the modern regional-LoRA runtime node."),
        ])
    elif engine == ENGINE_LIGHTWEIGHT_REGIONAL:
        family = normalized["family"]
        loader = normalized["loader"]
        mode = normalized["mode"]
        expected_loader = loader in MODERN_FAMILY_LOADERS.get(family, set())
        exact_route = (
            normalized["backend"] == "comfyui"
            and family in MODERN_LIGHTWEIGHT_FAMILIES
            and expected_loader
            and mode in SUPPORTED_EXECUTION_MODES
        )
        checks.append(_check("modern_exact_route_whitelist", exact_route, "Modern release routes must match an explicit family/loader/mode whitelist."))

        nodes_added = list(patch.get("nodes_added") or [])
        inserted_classes = _node_classes_for_ids(workflow, nodes_added)
        forbidden = sorted(set(inserted_classes).intersection(_FORBIDDEN_MODERN_INSERTIONS))
        checks.append(_check(
            "modern_no_forbidden_insertions",
            not forbidden,
            "Modern Scene Director must not insert samplers, standard LoRA loaders, or V054 nodes."
            + (f" Found: {', '.join(forbidden)}" if forbidden else ""),
        ))

        lora_applied = bool(patch.get("scene_director_regional_lora_applied"))
        wrapper_count = inserted_classes.count("NeoRegionalLoRADelta")
        checks.append(_check(
            "single_regional_lora_wrapper",
            (wrapper_count == 1) if lora_applied else wrapper_count == 0,
            "Regional LoRA execution must use exactly one NeoRegionalLoRADelta wrapper when requested and none otherwise.",
        ))

        proof = patch.get("scene_director_lightweight_runtime_proof") if isinstance(patch.get("scene_director_lightweight_runtime_proof"), dict) else {}
        checks.extend([
            _check("single_sampler_preserved", bool(proof.get("single_sampler_preserved", not mutated)), "The provider sampler count must remain unchanged."),
            _check("sampler_parameters_preserved", bool(proof.get("sampler_parameters_preserved", not mutated)), "Scene Director must not rewrite provider sampler parameters."),
            _check("latent_input_preserved", bool(proof.get("latent_input_unchanged", not mutated)), "Scene Director must preserve the provider latent input."),
            _check("no_global_model_mutation", proof.get("global_model_mutation") is not True, "Regional LoRA must not globally mutate the provider model."),
            _check("no_heavy_sd_repairs", proof.get("heavy_sd_repairs_added") in {False, None}, "Modern routes must not add the classic SD repair chain."),
            _check("no_hidden_repair_samplers", int(proof.get("repair_sampler_nodes_added") or 0) == 0, "Modern routes must not add hidden repair samplers."),
            _check("patch_fallback_policy", (not mutated) or str(patch.get("fallback_policy") or "").startswith("never_fallback"), "Modern release routes must fail closed instead of falling back to V054/global LoRA/finish passes."),
            _check("runtime_graph_contract", bool(proof.get("contract_ok")) if mutated else True, "A mutated lightweight graph must satisfy the SD-28 runtime contract."),
        ])

        # GPU proof is intentionally non-blocking at compile time. It is a per-run inspector state.
        gpu_proven = bool(patch.get("scene_director_regional_lora_runtime_gpu_proven") or proof.get("runtime_gpu_proven"))
        checks.append(_check(
            "gpu_proof_not_fabricated",
            True,
            "Compile-time release lock does not fabricate live GPU isolation proof; the inspector reports proof pending until runtime evidence exists.",
            blocking=False,
        ))
    else:
        gpu_proven = False

    blockers = [c for c in checks if c["blocking"] and not c["ok"]]
    warnings = [c for c in checks if (not c["blocking"]) and not c["ok"]]
    if engine != ENGINE_LIGHTWEIGHT_REGIONAL:
        gpu_proven = False
    status = "blocked" if blockers else "locked"
    return {
        "schema": RELEASE_LOCK_SCHEMA,
        "phase": RELEASE_LOCK_PHASE,
        "status": status,
        "locked": not blockers,
        "allow_output": not blockers,
        "engine": engine,
        "route": normalized,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "gpu_proof_required": bool(engine == ENGINE_LIGHTWEIGHT_REGIONAL and patch.get("scene_director_regional_lora_applied")),
        "gpu_proven": bool(gpu_proven),
        "reason": blockers[0]["detail"] if blockers else "SD-28.7 release invariants satisfied.",
    }


__all__ = [
    "RELEASE_LOCK_PHASE",
    "RELEASE_LOCK_SCHEMA",
    "evaluate_scene_director_release_lock",
]
