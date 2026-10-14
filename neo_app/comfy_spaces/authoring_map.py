from __future__ import annotations

from collections import defaultdict
import hashlib
import re
from typing import Any, Mapping, Sequence


SPACE_AUTHORING_MAP_SCHEMA_VERSION = "neo.comfy_space.authoring_map.v1"
_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_token(value: Any, fallback: str = "section") -> str:
    text = _TOKEN_RE.sub("_", str(value or "").strip().lower()).strip("._-")
    return text[:127] or fallback




def _stable_group_section_id(prefix: str, group_key: str) -> str:
    raw = f"{prefix}_{group_key}"
    readable = _safe_token(raw)[:108].rstrip("._-") or prefix
    digest = hashlib.sha256(str(group_key).encode("utf-8")).hexdigest()[:10]
    return f"{readable}_{digest}"

def _humanize(value: Any, fallback: str = "Section") -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    text = " ".join(part for part in text.split() if part)
    return text.title() if text else fallback


def _subgraph_title(candidate: Mapping[str, Any], *, duplicate_names: set[str] | None = None) -> str:
    name = str(candidate.get("subgraph_definition_name") or "").strip()
    path = [str(item) for item in list(candidate.get("subgraph_path") or []) if str(item)]
    if name:
        if path and name in (duplicate_names or set()):
            return f"{name} · {' / '.join(path)}"
        return name
    if path:
        return f"Subgraph {' / '.join(path)}"
    return "Main Workflow"


def _node_title(candidate: Mapping[str, Any], node_index: Mapping[str, Mapping[str, Any]]) -> str:
    node_id = str(candidate.get("node_id") or "")
    node = _mapping(node_index.get(node_id))
    title = str(node.get("title") or "").strip()
    class_type = str(candidate.get("class_type") or node.get("class_type") or "Node").strip()
    source_id = str(candidate.get("source_node_id") or node_id or "?")
    if title and title != class_type:
        return f"{title} · {class_type} #{source_id}"
    return f"{class_type} #{source_id}"


def _semantic_title(candidate: Mapping[str, Any]) -> str:
    role = str(candidate.get("node_role") or "processing_parameter").strip()
    titles = {
        "media_source": "Source Media",
        "model_loader": "Models",
        "model_tuning": "Model Tuning",
        "sampler": "Sampling",
        "generation_shape": "Generation",
        "media_transform": "Transforms",
        "parameter_source": "Parameters",
        "prompt_conditioning": "Prompting",
        "optional_toggle": "Optional Controls",
        "presentation_utility": "Presentation Utilities",
        "output_utility": "Output Settings",
        "processing_parameter": "Processing",
    }
    return titles.get(role, _humanize(role, "Processing"))


def build_authoring_map(
    input_candidates: Sequence[Mapping[str, Any]],
    graph_nodes: Sequence[Mapping[str, Any]],
    *,
    topological_order: Sequence[str] = (),
    editor_groups: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build deterministic section suggestions without changing workflow execution."""

    subgraph_name_paths: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for raw_candidate in input_candidates:
        candidate = _mapping(raw_candidate)
        name = str(candidate.get("subgraph_definition_name") or "").strip()
        path = tuple(str(item) for item in list(candidate.get("subgraph_path") or []) if str(item))
        if name and path:
            subgraph_name_paths[name].add(path)
    duplicate_subgraph_names = {name for name, paths in subgraph_name_paths.items() if len(paths) > 1}

    node_index = {
        str(_mapping(node).get("node_id") or ""): _mapping(node)
        for node in graph_nodes
        if str(_mapping(node).get("node_id") or "")
    }
    modes = ("semantic_role", "source_node", "source_subgraph", "workflow_group")
    editor_node_groups: dict[str, tuple[str, str]] = {}
    for group in editor_groups:
        key, title = str(group.get("group_key") or ""), str(group.get("title") or "").strip()
        if not key or not title:
            continue
        for node_id in group.get("source_node_ids") or []:
            editor_node_groups[str(node_id)] = (key, title)
    groups: dict[str, dict[str, dict[str, Any]]] = {mode: {} for mode in modes}
    candidate_map: dict[str, dict[str, Any]] = {}

    for raw_candidate in input_candidates:
        candidate = _mapping(raw_candidate)
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            continue
        node_id = str(candidate.get("node_id") or "")
        source_node_id = str(candidate.get("source_node_id") or node_id)
        subgraph_path = [str(item) for item in list(candidate.get("subgraph_path") or [])]
        semantic_key = str(candidate.get("node_role") or "processing_parameter")
        # Group by the actual subgraph *instance path*, not definition id. The
        # same reusable definition may be instantiated multiple times for
        # different passes and those controls must remain independently authorable.
        subgraph_key = "/".join(subgraph_path) if subgraph_path else "root"
        node_key = "/".join([*subgraph_path, source_node_id]) if subgraph_path else source_node_id

        # Top-level group boxes may enclose reusable subgraph instances.
        # Their flattened children inherit that visible group from the instance.
        visual_source_id = subgraph_path[0] if subgraph_path else source_node_id
        group_key, group_title = editor_node_groups.get(visual_source_id, ("", ""))
        if not group_key:
            group_key = f"ungrouped_{semantic_key}"
            group_title = _semantic_title(candidate)

        suggestions = {
            "semantic_role": {
                "section_id": _safe_token(f"role_{semantic_key}"),
                "title": _semantic_title(candidate),
                "group_key": semantic_key,
            },
            "source_node": {
                "section_id": _stable_group_section_id("node", node_key),
                "title": _node_title(candidate, node_index),
                "group_key": node_key,
            },
            "workflow_group": {
                "section_id": _stable_group_section_id("canvas", group_key),
                "title": group_title,
                "group_key": group_key,
            },
            "source_subgraph": {
                "section_id": _stable_group_section_id("subgraph", subgraph_key),
                "title": _subgraph_title(candidate, duplicate_names=duplicate_subgraph_names),
                "group_key": subgraph_key,
            },
        }
        candidate_map[candidate_id] = suggestions

        for mode, suggestion in suggestions.items():
            section_id = suggestion["section_id"]
            group = groups[mode].setdefault(
                section_id,
                {
                    "section_id": section_id,
                    "title": suggestion["title"],
                    "group_key": suggestion["group_key"],
                    "candidate_ids": [],
                    "source_node_ids": [],
                    "subgraph_paths": [],
                },
            )
            group["candidate_ids"].append(candidate_id)
            if source_node_id and source_node_id not in group["source_node_ids"]:
                group["source_node_ids"].append(source_node_id)
            if subgraph_path and subgraph_path not in group["subgraph_paths"]:
                group["subgraph_paths"].append(subgraph_path)

    # The graph is a DAG in accepted imports. The queue-time node order, not
    # alphabetical labels or JSON dictionary insertion, drives authoring order.
    execution_index = {str(node_id): index for index, node_id in enumerate(topological_order)}
    for mode_groups in groups.values():
        for group in mode_groups.values():
            canonical_ids = [str(_mapping(raw).get("node_id") or "") for raw in input_candidates
                             if str(_mapping(raw).get("candidate_id") or "") in group["candidate_ids"]]
            group["execution_index"] = min((execution_index.get(node_id, len(execution_index)) for node_id in canonical_ids), default=len(execution_index))

    output_groups: dict[str, list[dict[str, Any]]] = {}
    for mode in modes:
        rows = list(groups[mode].values())
        rows.sort(key=lambda item: (item["execution_index"], str(item.get("title") or "").casefold(), str(item.get("section_id") or "")))
        for order, row in enumerate(rows, start=1):
            row["order"] = order * 10
            row["candidate_count"] = len(row.get("candidate_ids") or [])
        output_groups[mode] = rows

    preferred_mode = "workflow_group" if editor_node_groups else "semantic_role"
    return {
        "schema_version": SPACE_AUTHORING_MAP_SCHEMA_VERSION,
        "read_only": True,
        "workflow_mutated": False,
        "section_state_changes_execution": False,
        "available_grouping_modes": ["manual", *modes],
        "recommended_starting_mode": preferred_mode,
        "candidate_sections": candidate_map,
        "groups": output_groups,
        "summary": {
            "candidate_count": len(candidate_map),
            "semantic_group_count": len(output_groups["semantic_role"]),
            "source_node_group_count": len(output_groups["source_node"]),
            "source_subgraph_group_count": len(output_groups["source_subgraph"]),
            "workflow_group_count": len(output_groups["workflow_group"]),
        },
    }


def authoring_map_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_AUTHORING_MAP_SCHEMA_VERSION,
        "status": "ready",
        "grouping_modes": ["manual", "workflow_group", "semantic_role", "source_node", "source_subgraph"],
        "sections_are_presentation_only": True,
        "section_disable_bypasses_nodes": False,
        "graph_mutation_implemented": False,
    }
