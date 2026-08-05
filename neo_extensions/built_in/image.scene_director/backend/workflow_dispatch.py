from __future__ import annotations

"""Scene Director workflow dispatcher release-locked in SD-28.7.

This module is intentionally thin. Classic SDXL/SD1.5 routes are delegated to
Neo's existing, frozen V054 workflow patcher. Modern Krea2/Klein/Z-Image routes
are delegated to the lightweight masked-conditioning compiler. The legacy file
remains untouched on disk and is loaded lazily under a private module name only
when a classic route actually needs it.
"""

from copy import deepcopy
from importlib import util as importlib_util
from pathlib import Path
import sys
from typing import Any

from .execution_strategy import ENGINE_CLASSIC_V054, ENGINE_LIGHTWEIGHT_REGIONAL, resolve_scene_director_execution_strategy
from .lightweight_regional import apply_lightweight_regional_prompt_patch
from .release_lock import evaluate_scene_director_release_lock
from .inspector import build_scene_director_inspector

DISPATCH_PHASE = "SD-28.7"
DISPATCH_SCHEMA = "neo.image.scene_director.workflow_dispatch.v1"
_LEGACY_MODULE_NAME = f"{__package__}._workflow_patch_v054_legacy"
_LEGACY_MODULE = None


def _legacy_module():
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None:
        return _LEGACY_MODULE
    existing = sys.modules.get(_LEGACY_MODULE_NAME)
    if existing is not None:
        _LEGACY_MODULE = existing
        return existing
    legacy_path = Path(__file__).with_name("workflow_patch.py")
    spec = importlib_util.spec_from_file_location(_LEGACY_MODULE_NAME, legacy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load frozen Scene Director V054 patcher from {legacy_path}.")
    module = importlib_util.module_from_spec(spec)
    sys.modules[_LEGACY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    _LEGACY_MODULE = module
    return module


def _disabled_result(
    workflow: dict[str, Any],
    *,
    strategy: dict[str, Any],
    model_ref: Any,
    clip_ref: Any,
    sampler_node_id: Any,
) -> dict[str, Any]:
    model = list(model_ref) if isinstance(model_ref, (list, tuple)) else ["1", 0]
    clip = list(clip_ref) if isinstance(clip_ref, (list, tuple)) else ["2", 0]
    reason = str(strategy.get("reason") or "Scene Director route is not executable.")
    return {
        "workflow": dict(workflow or {}),
        "workflow_patch": {
            "extension_id": "image.scene_director",
            "phase": DISPATCH_PHASE,
            "patch_type": "scene_director_dispatch_gated",
            "applied": False,
            "mutated": False,
            "engine": strategy.get("engine"),
            "scene_director_execution_strategy": strategy,
            "sampler_node_id": str(sampler_node_id),
            "workflow_patch_allowed": False,
            "reason": reason,
        },
        "validation": {
            "extension_id": "image.scene_director",
            "enabled": False,
            "ok": True,
            "block": {},
            "validation": [{
                "extension_id": "image.scene_director",
                "level": "warning",
                "field": "route",
                "code": "scene_director_dispatch_gated",
                "message": reason,
            }],
            "route_state": strategy.get("status"),
            "workflow_patch_allowed": False,
            "can_emit_workflow_patch": False,
            "node_status": {},
        },
        "model_ref": model,
        "clip_ref": clip,
        "mutated": False,
        "changed": False,
        "extension_id": "image.scene_director",
        "phase": DISPATCH_PHASE,
    }


def _finalize_release_result(
    result: dict[str, Any],
    *,
    before_workflow: dict[str, Any],
    route: dict[str, Any] | None,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    finalized = deepcopy(result)
    release_lock = evaluate_scene_director_release_lock(
        before_workflow=before_workflow,
        result=finalized,
        route=route,
        strategy=strategy,
    )

    patch = finalized.get("workflow_patch") if isinstance(finalized.get("workflow_patch"), dict) else {}
    validation = finalized.get("validation") if isinstance(finalized.get("validation"), dict) else {}

    if not release_lock.get("allow_output"):
        # Release-lock failures fail closed to the exact provider graph received by
        # Scene Director.  Never try a V054/global-LoRA/finish-pass fallback.
        finalized["workflow"] = deepcopy(before_workflow)
        finalized["mutated"] = False
        finalized["changed"] = False
        # Restore output references to the pre-Scene-Director provider graph so a
        # release-blocked result cannot expose refs to discarded nodes.
        if patch.get("previous_model_ref") is not None:
            finalized["model_ref"] = deepcopy(patch.get("previous_model_ref"))
            patch["patched_model_ref"] = deepcopy(patch.get("previous_model_ref"))
        if patch.get("clip_ref") is not None:
            finalized["clip_ref"] = deepcopy(patch.get("clip_ref"))
        if patch.get("previous_positive_ref") is not None:
            finalized["positive_ref"] = deepcopy(patch.get("previous_positive_ref"))
            patch["patched_positive_ref"] = deepcopy(patch.get("previous_positive_ref"))
        if patch.get("previous_negative_ref") is not None:
            finalized["negative_ref"] = deepcopy(patch.get("previous_negative_ref"))
            patch["patched_negative_ref"] = deepcopy(patch.get("previous_negative_ref"))
        patch["release_lock_attempted_nodes_added"] = deepcopy(patch.get("nodes_added") or [])
        patch["nodes_added"] = []
        patch["node"] = None
        patch["node_class"] = None
        patch["applied"] = False
        patch["mutated"] = False
        patch["workflow_patch_allowed"] = False
        patch["release_lock_blocked"] = True
        if patch.get("scene_director_regional_lora_applied"):
            patch["scene_director_regional_lora_applied"] = False
            patch["scene_director_regional_lora_status"] = "release_lock_blocked"
        if isinstance(patch.get("scene_director_lightweight_regional_prompt"), dict):
            patch["scene_director_lightweight_regional_prompt"]["status"] = "release_lock_blocked"
        patch["reason"] = str(release_lock.get("reason") or "SD-28.7 release lock blocked graph output.")
        validation["workflow_patch_allowed"] = False
        validation["can_emit_workflow_patch"] = False
        validation.setdefault("errors", []).append({
            "extension_id": "image.scene_director",
            "level": "error",
            "field": "release_lock",
            "code": "scene_director_release_lock_blocked",
            "message": patch["reason"],
        })
        validation.setdefault("validation", []).append(validation["errors"][-1])

    patch["scene_director_release_lock"] = deepcopy(release_lock)
    finalized["scene_director_release_lock"] = deepcopy(release_lock)
    finalized["workflow_patch"] = patch
    finalized["validation"] = validation

    inspector = build_scene_director_inspector(
        validation=validation,
        workflow_patch=patch,
        release_lock=release_lock,
    )
    patch["inspector_debug_ui"] = deepcopy(inspector)
    patch["scene_director_release_inspector"] = deepcopy(inspector)
    validation["inspector_debug_ui"] = deepcopy(inspector)
    validation["scene_director_release_lock"] = deepcopy(release_lock)
    finalized["workflow_patch"] = patch
    finalized["validation"] = validation
    finalized["inspector_debug_ui"] = deepcopy(inspector)
    finalized["scene_director_release_inspector"] = deepcopy(inspector)
    finalized["phase"] = DISPATCH_PHASE
    return finalized


def apply_scene_director_patch(
    workflow: dict[str, Any],
    *,
    payload: Any,
    route: dict[str, Any] | None,
    available_nodes: Any,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    **kwargs: Any,
) -> dict[str, Any]:
    strategy = resolve_scene_director_execution_strategy(route or {})
    engine = str(strategy.get("engine") or "unsupported")
    if engine == ENGINE_CLASSIC_V054:
        result = _legacy_module().apply_scene_director_patch(
            workflow,
            payload=payload,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_ref,
            clip_ref=clip_ref,
            sampler_node_id=sampler_node_id,
            **kwargs,
        )
    elif engine == ENGINE_LIGHTWEIGHT_REGIONAL:
        result = apply_lightweight_regional_prompt_patch(
            workflow,
            payload=payload,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_ref,
            clip_ref=clip_ref,
            sampler_node_id=sampler_node_id,
            **kwargs,
        )
    else:
        result = _disabled_result(
            workflow,
            strategy=strategy,
            model_ref=model_ref,
            clip_ref=clip_ref,
            sampler_node_id=sampler_node_id,
        )
    return _finalize_release_result(
        result,
        before_workflow=dict(workflow or {}),
        route=route,
        strategy=strategy,
    )


def __getattr__(name: str):
    """Preserve legacy imports of helper symbols from backend.workflow_patch."""
    if name in globals():
        return globals()[name]
    return getattr(_legacy_module(), name)


def __dir__() -> list[str]:
    names = set(globals())
    try:
        names.update(dir(_legacy_module()))
    except Exception:
        pass
    return sorted(names)


__all__ = ["DISPATCH_PHASE", "DISPATCH_SCHEMA", "apply_scene_director_patch"]
