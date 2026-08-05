from __future__ import annotations

"""SD-28.7 compatibility proxy for the existing Scene Director payload schema.

The large payload normalizer remains frozen on disk. This proxy delegates its
public API while correcting only strategy-dependent metadata that changed when
modern routes stopped requiring NeoSceneDirectorV054 and Krea2/FLUX.2 Klein gained family-specific model-side regional LoRA adapters.
"""

from copy import deepcopy
from importlib import util as importlib_util
from pathlib import Path
import sys
from typing import Any

from .execution_strategy import ENGINE_LIGHTWEIGHT_REGIONAL, resolve_scene_director_execution_strategy
from .inspector import build_preflight_inspector

_LEGACY_MODULE_NAME = f"{__package__}._payload_schema_legacy"
_LEGACY_MODULE = None


def _legacy_module():
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None:
        return _LEGACY_MODULE
    existing = sys.modules.get(_LEGACY_MODULE_NAME)
    if existing is not None:
        _LEGACY_MODULE = existing
        return existing
    path = Path(__file__).with_name("payload_schema.py")
    spec = importlib_util.spec_from_file_location(_LEGACY_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Scene Director payload schema from {path}.")
    module = importlib_util.module_from_spec(spec)
    sys.modules[_LEGACY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    _LEGACY_MODULE = module
    return module


def _postprocess(normalized: dict[str, Any], route: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(normalized)
    strategy = resolve_scene_director_execution_strategy(route or {})
    if strategy.get("engine") != ENGINE_LIGHTWEIGHT_REGIONAL:
        return result
    extension_id = str(getattr(_legacy_module(), "EXTENSION_ID", "image.scene_director"))
    block = (result.get("extensions") or {}).get(extension_id)
    if not isinstance(block, dict):
        return result
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    warnings = []
    family = str(strategy.get("family") or "")
    for warning in metadata.get("warnings") or []:
        text = str(warning)
        if text == "Scene Director SD/SD1.5 route is experimental in V2.":
            if family in {"krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"}:
                text = "Scene Director lightweight regional route is release-locked in SD-28.7 for Krea2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo; regional LoRA still requires per-run runtime proof."
            else:
                text = "Scene Director lightweight regional route is experimental in SD-28.7 pending family-specific runtime validation."
        warnings.append(text)
    metadata["warnings"] = warnings
    decision = metadata.get("node_decision") if isinstance(metadata.get("node_decision"), dict) else {}
    if isinstance(decision.get("node_status"), dict):
        metadata["node_status"] = deepcopy(decision["node_status"])
    metadata["execution_engine"] = ENGINE_LIGHTWEIGHT_REGIONAL
    metadata["execution_strategy"] = deepcopy(strategy)
    regional_lora = strategy.get("regional_lora") if isinstance(strategy.get("regional_lora"), dict) else {}
    metadata["regional_lora_execution"] = str(regional_lora.get("mode") or "adapter_gated")
    metadata["regional_lora_implementation_state"] = str(regional_lora.get("implementation_state") or "planned_gated")
    metadata["regional_lora_runtime_gpu_proven"] = bool(regional_lora.get("runtime_gpu_proven"))
    metadata["release_lock_phase"] = "SD-28.7"
    block["metadata"] = metadata
    metadata["inspector_debug_ui"] = build_preflight_inspector(block=block, strategy=strategy)
    block["metadata"] = metadata
    result["extensions"][extension_id] = block
    return result


def normalize_scene_director_payload(
    payload: Any,
    *,
    route: dict[str, Any] | None = None,
    node_status: Any = None,
    object_info: Any = None,
):
    normalized = _legacy_module().normalize_scene_director_payload(
        payload,
        route=route,
        node_status=node_status,
        object_info=object_info,
    )
    return _postprocess(normalized, route)


def normalize_block(payload: Any, *, route: dict[str, Any] | None = None, object_info: Any = None):
    normalized = normalize_scene_director_payload(payload, route=route, object_info=object_info)
    extension_id = str(getattr(_legacy_module(), "EXTENSION_ID", "image.scene_director"))
    block = normalized["extensions"][extension_id]
    notes: list[dict[str, Any]] = []
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    if metadata.get("gated_reason") and not metadata.get("workflow_patch_allowed"):
        notes.append({"extension_id": extension_id, "level": "warning", "field": "workflow_patch_allowed", "message": str(metadata.get("gated_reason"))})
    if metadata.get("reason") == "no_active_regions":
        notes.append({"extension_id": extension_id, "level": "warning", "field": "inputs.regions", "message": "Scene Director enabled but no active regions are available."})
    for message in metadata.get("warnings") or []:
        notes.append({"extension_id": extension_id, "level": "info", "field": "metadata.warnings", "message": str(message)})
    return block, notes


def __getattr__(name: str):
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


__all__ = ["normalize_scene_director_payload", "normalize_block"]
