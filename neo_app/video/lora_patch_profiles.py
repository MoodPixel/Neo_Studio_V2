from __future__ import annotations

from copy import deepcopy
from typing import Any

PATCH_PROFILE_SCHEMA_VERSION = "neo.video.lora_patch_profile.v1"
PROFILE_OWNER = "compiler"

VALID_LOADER_TYPES = {
    "model_only",
    "model_clip",
    "model_only_multi_branch",
    "provider_specific",
}
VALID_TARGETS = {"all", "high", "low"}


def _clean_ref(value: Any) -> list[Any] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    node_id = str(value[0] or "").strip()
    if not node_id:
        return None
    index = value[1]
    if isinstance(index, str) and index.strip().isdigit():
        index = int(index.strip())
    if not isinstance(index, int) or index < 0:
        return None
    return [node_id, index]


def _clean_consumers(values: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or item.get("node") or "").strip()
        input_name = str(item.get("input") or item.get("input_name") or "").strip()
        if not node_id or not input_name:
            continue
        key = (node_id, input_name)
        if key in seen:
            continue
        seen.add(key)
        result.append({"node_id": node_id, "input": input_name})
    return result


def build_patch_branch(
    *,
    target: str,
    model_ref: list[Any] | tuple[Any, ...],
    model_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    """Build one compiler-owned graph anchor.

    The compiler supplies exact refs from the graph it just created. This helper
    does not inspect a workflow or infer family-specific node IDs.
    """
    normalized_target = str(target or "all").strip().lower()
    if normalized_target not in VALID_TARGETS:
        raise ValueError(f"Unsupported Video LoRA patch target: {normalized_target}")
    clean_model_ref = _clean_ref(model_ref)
    clean_model_consumers = _clean_consumers(model_consumers)
    if not clean_model_ref:
        raise ValueError("Video LoRA patch branch requires a valid model_ref.")
    if not clean_model_consumers:
        raise ValueError("Video LoRA patch branch requires at least one model consumer.")

    clean_clip_ref = _clean_ref(clip_ref)
    clean_clip_consumers = _clean_consumers(clip_consumers)
    branch = {
        "target": normalized_target,
        "model_ref": clean_model_ref,
        "model_consumers": clean_model_consumers,
        "clip_ref": clean_clip_ref or [],
        "clip_consumers": clean_clip_consumers,
    }
    return branch


def build_single_model_lora_patch_profile(
    *,
    route_id: str,
    compiler: str,
    model_ref: list[Any] | tuple[Any, ...],
    model_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    loader_type: str = "model_only",
    loader_node_class: str = "LoraLoaderModelOnly",
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    validated: bool = False,
    notes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized_loader_type = str(loader_type or "model_only").strip().lower()
    if normalized_loader_type not in VALID_LOADER_TYPES:
        raise ValueError(f"Unsupported Video LoRA loader type: {normalized_loader_type}")
    branch = build_patch_branch(
        target="all",
        model_ref=model_ref,
        model_consumers=model_consumers,
        clip_ref=clip_ref,
        clip_consumers=clip_consumers,
    )
    return {
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "owner": PROFILE_OWNER,
        "route_id": str(route_id or "").strip(),
        "compiler": str(compiler or "").strip(),
        "loader_type": normalized_loader_type,
        "loader_node_class": str(loader_node_class or "").strip(),
        "allow_generic_lora_loader_fallback": False,
        "targets": ["all"],
        "target_map": {"all": ["all"]},
        "branches": [branch],
        "insertion_policy": "after_declared_model_ref_before_declared_consumers",
        "validated": bool(validated),
        "notes": [str(item) for item in (notes or []) if str(item).strip()],
    }


def build_multi_branch_lora_patch_profile(
    *,
    route_id: str,
    compiler: str,
    high_model_ref: list[Any] | tuple[Any, ...],
    high_model_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    low_model_ref: list[Any] | tuple[Any, ...],
    low_model_consumers: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    loader_type: str = "model_only_multi_branch",
    loader_node_class: str = "LoraLoaderModelOnly",
    validated: bool = False,
    notes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized_loader_type = str(loader_type or "model_only_multi_branch").strip().lower()
    if normalized_loader_type not in VALID_LOADER_TYPES:
        raise ValueError(f"Unsupported Video LoRA loader type: {normalized_loader_type}")
    high = build_patch_branch(
        target="high",
        model_ref=high_model_ref,
        model_consumers=high_model_consumers,
    )
    low = build_patch_branch(
        target="low",
        model_ref=low_model_ref,
        model_consumers=low_model_consumers,
    )
    if high["model_ref"] == low["model_ref"]:
        raise ValueError("Multi-branch Video LoRA profile requires distinct high/low model refs.")
    return {
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "owner": PROFILE_OWNER,
        "route_id": str(route_id or "").strip(),
        "compiler": str(compiler or "").strip(),
        "loader_type": normalized_loader_type,
        "loader_node_class": str(loader_node_class or "").strip(),
        "allow_generic_lora_loader_fallback": False,
        "targets": ["all", "high", "low"],
        "target_map": {
            "all": ["high", "low"],
            "high": ["high"],
            "low": ["low"],
        },
        "branches": [high, low],
        "insertion_policy": "after_declared_model_ref_before_declared_consumers",
        "validated": bool(validated),
        "notes": [str(item) for item in (notes or []) if str(item).strip()],
    }


def attach_lora_patch_profile(compiled: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Attach profile metadata without changing the compiled Comfy workflow."""
    result = dict(compiled or {})
    result["lora_patch_profile"] = deepcopy(profile)
    return result


def lora_patch_profile_summary(profile: dict[str, Any] | None) -> dict[str, Any]:
    data = profile if isinstance(profile, dict) else {}
    branches = data.get("branches") if isinstance(data.get("branches"), list) else []
    return {
        "schema_version": str(data.get("schema_version") or ""),
        "owner": str(data.get("owner") or ""),
        "route_id": str(data.get("route_id") or ""),
        "compiler": str(data.get("compiler") or ""),
        "loader_type": str(data.get("loader_type") or ""),
        "loader_node_class": str(data.get("loader_node_class") or ""),
        "targets": [str(item) for item in data.get("targets", [])] if isinstance(data.get("targets"), list) else [],
        "branch_count": len(branches),
        "validated": bool(data.get("validated", False)),
    }
