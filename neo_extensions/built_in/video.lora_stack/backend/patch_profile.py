from __future__ import annotations

from copy import deepcopy
from typing import Any

from .support_matrix import (
    SUPPORTED,
    allowed_targets,
    required_loader_nodes,
    required_loader_type,
    support_for_route,
)

PATCH_PROFILE_SCHEMA_VERSION = "neo.video.lora_patch_profile.v1"
PROFILE_OWNER = "compiler"


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


def _clean_consumers(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
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


def _route_id(route: str | dict[str, Any] | None) -> str:
    if isinstance(route, str):
        return route.strip()
    if isinstance(route, dict):
        return str(route.get("route_id") or route.get("id") or "").strip()
    return ""


def _normalize_branch(branch: dict[str, Any] | None) -> dict[str, Any]:
    data = branch if isinstance(branch, dict) else {}
    return {
        "target": str(data.get("target") or "all").strip().lower(),
        "model_ref": _clean_ref(data.get("model_ref")) or [],
        "model_consumers": _clean_consumers(data.get("model_consumers")),
        "clip_ref": _clean_ref(data.get("clip_ref")) or [],
        "clip_consumers": _clean_consumers(data.get("clip_consumers")),
    }


def normalize_lora_patch_profile(
    profile: dict[str, Any] | None,
    *,
    route: str | dict[str, Any] | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    data = profile if isinstance(profile, dict) else {}
    expected_route_id = _route_id(route)
    embedded_route_id = str(data.get("route_id") or "").strip()
    route_id = expected_route_id or embedded_route_id
    support = support_for_route(route_id, backend=backend)

    branches = [
        _normalize_branch(item)
        for item in (data.get("branches") if isinstance(data.get("branches"), list) else [])
        if isinstance(item, dict)
    ]

    normalized = {
        "schema_version": str(data.get("schema_version") or PATCH_PROFILE_SCHEMA_VERSION),
        "owner": str(data.get("owner") or "").strip(),
        "route_id": route_id,
        "compiler": str(data.get("compiler") or "").strip(),
        "loader_type": str(data.get("loader_type") or "").strip().lower(),
        "loader_node_class": str(data.get("loader_node_class") or "").strip(),
        "allow_generic_lora_loader_fallback": bool(data.get("allow_generic_lora_loader_fallback", False)),
        "targets": [str(item).strip().lower() for item in data.get("targets", []) if str(item).strip()] if isinstance(data.get("targets"), list) else [],
        "target_map": deepcopy(data.get("target_map")) if isinstance(data.get("target_map"), dict) else {},
        "branches": branches,
        "insertion_policy": str(data.get("insertion_policy") or "").strip(),
        "validated": bool(data.get("validated", False)),
        "notes": [str(item) for item in data.get("notes", []) if str(item).strip()] if isinstance(data.get("notes"), list) else [],
    }

    errors: list[str] = []
    if not data:
        errors.append("missing_patch_profile")
    if normalized["schema_version"] != PATCH_PROFILE_SCHEMA_VERSION:
        errors.append("unsupported_patch_profile_schema")
    if normalized["owner"] != PROFILE_OWNER:
        errors.append("patch_profile_owner_must_be_compiler")
    if expected_route_id and embedded_route_id and expected_route_id != embedded_route_id:
        errors.append("patch_profile_route_mismatch")
    if not normalized["route_id"]:
        errors.append("patch_profile_route_missing")
    if not normalized["compiler"]:
        errors.append("patch_profile_compiler_missing")

    if support.get("state") != SUPPORTED:
        errors.append(f"route_not_lora_supported:{support.get('state') or 'blocked'}")

    expected_loader_type = required_loader_type(route_id, backend=backend)
    if normalized["loader_type"] != expected_loader_type:
        errors.append(f"loader_type_mismatch:{expected_loader_type}")

    required_nodes = set(required_loader_nodes(route_id, backend=backend))
    if required_nodes and normalized["loader_node_class"] not in required_nodes:
        errors.append("loader_node_class_mismatch")
    if normalized["allow_generic_lora_loader_fallback"]:
        errors.append("generic_lora_loader_fallback_forbidden")

    expected_targets = set(allowed_targets(route_id, backend=backend))
    declared_targets = set(normalized["targets"])
    if declared_targets != expected_targets:
        errors.append("patch_profile_targets_mismatch")

    branch_targets = [str(branch.get("target") or "") for branch in branches]
    branch_target_set = set(branch_targets)
    if len(branch_targets) != len(branch_target_set):
        errors.append("duplicate_patch_profile_branch_target")

    if expected_targets == {"all"}:
        if branch_target_set != {"all"}:
            errors.append("single_model_profile_requires_all_branch")
    elif expected_targets == {"all", "high", "low"}:
        if branch_target_set != {"high", "low"}:
            errors.append("multi_branch_profile_requires_high_and_low_branches")
        if normalized["target_map"] != {
            "all": ["high", "low"],
            "high": ["high"],
            "low": ["low"],
        }:
            errors.append("multi_branch_target_map_invalid")

    requires_clip = normalized["loader_type"] == "model_clip"
    for index, branch in enumerate(branches):
        if not branch["model_ref"]:
            errors.append(f"branch_{index + 1}_model_ref_missing")
        if not branch["model_consumers"]:
            errors.append(f"branch_{index + 1}_model_consumers_missing")
        if requires_clip and not branch["clip_ref"]:
            errors.append(f"branch_{index + 1}_clip_ref_missing")
        if requires_clip and not branch["clip_consumers"]:
            errors.append(f"branch_{index + 1}_clip_consumers_missing")

    if normalized["loader_type"] == "model_only_multi_branch" and len(branches) == 2:
        if branches[0]["model_ref"] == branches[1]["model_ref"]:
            errors.append("multi_branch_model_refs_must_be_distinct")

    return {
        "valid": not errors,
        "errors": errors,
        "reason": "ok" if not errors else ",".join(errors),
        "route_support": support,
        "profile": normalized,
    }


def validate_profile_against_workflow(
    profile: dict[str, Any] | None,
    workflow: dict[str, Any] | None,
    *,
    route: str | dict[str, Any] | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    result = normalize_lora_patch_profile(profile, route=route, backend=backend)
    graph = workflow if isinstance(workflow, dict) else {}
    errors = list(result.get("errors") or [])
    normalized = result.get("profile") if isinstance(result.get("profile"), dict) else {}

    if not graph:
        errors.append("workflow_missing")
    else:
        for branch in normalized.get("branches", []):
            if not isinstance(branch, dict):
                continue
            target = str(branch.get("target") or "all")
            model_ref = branch.get("model_ref") if isinstance(branch.get("model_ref"), list) else []
            if model_ref and str(model_ref[0]) not in graph:
                errors.append(f"{target}:model_ref_node_missing")
            for consumer in branch.get("model_consumers", []):
                node_id = str(consumer.get("node_id") or "")
                input_name = str(consumer.get("input") or "")
                node = graph.get(node_id)
                if not isinstance(node, dict):
                    errors.append(f"{target}:model_consumer_node_missing:{node_id}")
                    continue
                inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                if inputs.get(input_name) != model_ref:
                    errors.append(f"{target}:model_consumer_ref_mismatch:{node_id}.{input_name}")

            clip_ref = branch.get("clip_ref") if isinstance(branch.get("clip_ref"), list) else []
            if clip_ref and str(clip_ref[0]) not in graph:
                errors.append(f"{target}:clip_ref_node_missing")
            for consumer in branch.get("clip_consumers", []):
                node_id = str(consumer.get("node_id") or "")
                input_name = str(consumer.get("input") or "")
                node = graph.get(node_id)
                if not isinstance(node, dict):
                    errors.append(f"{target}:clip_consumer_node_missing:{node_id}")
                    continue
                inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                if inputs.get(input_name) != clip_ref:
                    errors.append(f"{target}:clip_consumer_ref_mismatch:{node_id}.{input_name}")

    return {
        **result,
        "valid": not errors,
        "errors": errors,
        "reason": "ok" if not errors else ",".join(errors),
        "workflow_validated": bool(graph),
    }


def profile_is_usable(
    profile: dict[str, Any] | None,
    *,
    route: str | dict[str, Any] | None = None,
    backend: str | None = None,
    workflow: dict[str, Any] | None = None,
) -> bool:
    if workflow is not None:
        return bool(validate_profile_against_workflow(profile, workflow, route=route, backend=backend).get("valid"))
    return bool(normalize_lora_patch_profile(profile, route=route, backend=backend).get("valid"))


def branches_for_target(profile: dict[str, Any] | None, target: str) -> list[dict[str, Any]]:
    data = profile if isinstance(profile, dict) else {}
    requested = str(target or "all").strip().lower()
    target_map = data.get("target_map") if isinstance(data.get("target_map"), dict) else {}
    branch_names = target_map.get(requested)
    if not isinstance(branch_names, list):
        return []
    wanted = {str(item) for item in branch_names}
    return [
        deepcopy(branch)
        for branch in data.get("branches", [])
        if isinstance(branch, dict) and str(branch.get("target") or "") in wanted
    ]


def profile_metadata(result: dict[str, Any] | None) -> dict[str, Any]:
    data = result if isinstance(result, dict) else {}
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    return {
        "schema_version": PATCH_PROFILE_SCHEMA_VERSION,
        "valid": bool(data.get("valid")),
        "reason": str(data.get("reason") or ""),
        "errors": [str(item) for item in data.get("errors", [])] if isinstance(data.get("errors"), list) else [],
        "owner": str(profile.get("owner") or ""),
        "route_id": str(profile.get("route_id") or ""),
        "compiler": str(profile.get("compiler") or ""),
        "loader_type": str(profile.get("loader_type") or ""),
        "loader_node_class": str(profile.get("loader_node_class") or ""),
        "targets": deepcopy(profile.get("targets") or []),
        "branch_count": len(profile.get("branches") or []) if isinstance(profile.get("branches"), list) else 0,
        "validated": bool(profile.get("validated", False)),
        "workflow_validated": bool(data.get("workflow_validated", False)),
    }
