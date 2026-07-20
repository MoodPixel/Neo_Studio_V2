from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Mapping


def _portable_comfy_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    return str(PurePosixPath(text))


def record_qwen_stitch_comfy_handoff(
    prompt_graph: Mapping[str, Any] | None,
    stitch_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach resolved Comfy LoadImage names to compiled Stitch metadata.

    The provider preflight mutates LoadImage nodes after uploading Neo-owned
    files into Comfy's input folder. This function records the post-upload
    names without exposing local filesystem paths in the job metadata.
    """

    result = deepcopy(dict(stitch_metadata or {}))
    graph = prompt_graph if isinstance(prompt_graph, Mapping) else {}
    handoff_groups: list[dict[str, Any]] = []
    all_ready = True
    for group in result.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        if isinstance(group, dict):
            group["raw_input_names"] = [
                PurePosixPath(str(value or "").replace("\\", "/")).name
                for value in (group.get("raw_input_names") or [])
            ]
        nodes = group.get("workflow_nodes") if isinstance(group.get("workflow_nodes"), Mapping) else {}
        resolved: dict[str, str] = {}
        source_refs: dict[str, str] = {}
        for side, node_key in (("image_a", "load_image_a"), ("image_b", "load_image_b")):
            node_id = str(nodes.get(node_key) or "")
            node = graph.get(node_id) if node_id else None
            inputs = node.get("inputs") if isinstance(node, Mapping) and isinstance(node.get("inputs"), Mapping) else {}
            comfy_name = _portable_comfy_name(inputs.get("image"))
            if comfy_name:
                resolved[side] = comfy_name
            else:
                all_ready = False
            raw_name = (group.get("raw_input_names") or [""] * 2)[0 if side == "image_a" else 1]
            source_refs[side] = PurePosixPath(str(raw_name or "").replace("\\", "/")).name if raw_name else ""
        handoff_groups.append({
            "id": str(group.get("id") or ""),
            "output_lane": int(group.get("output_lane") or 0),
            "resolved_comfy_inputs": resolved,
            "source_refs": source_refs,
            "ready": len(resolved) == 2,
        })
    result["provider_handoff"] = {
        "status": "ready" if all_ready and len(handoff_groups) == len(result.get("groups") or []) else "incomplete",
        "asset_boundary": "neo_data_source_upload_to_comfy_input",
        "groups": handoff_groups,
    }
    return result
