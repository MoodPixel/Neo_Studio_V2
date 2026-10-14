from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import math
import re
from typing import Any, Mapping, Sequence

from neo_app.comfy_spaces.authoring_map import build_authoring_map
from neo_app.comfy_spaces.graph_anchors import analyze_graph_anchors
from neo_app.comfy_spaces.subgraph_ir import (
    ComfySubgraphNormalizationError,
    flatten_editor_subgraphs,
)
from neo_app.comfy_spaces.type_system import (
    SPACE_TYPE_SYSTEM_SCHEMA_VERSION,
    resolve_input_type_descriptor,
    resolve_output_type_descriptor,
    type_system_capabilities,
)


SPACE_ANALYSIS_SCHEMA_VERSION = "neo.comfy_space.analysis.v1"

_LINK_INDEX_RE = re.compile(r"^\d+$")
_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")

_MODEL_FIELD_TYPES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ckpt_name", "checkpoint_name"), "checkpoint"),
    (("unet_name", "diffusion_model", "diffusion_name"), "diffusion_model"),
    (("vae_name",), "vae"),
    (("clip_name", "clip_name1", "clip_name2", "clip_name3", "clip_name4", "text_encoder_name"), "text_encoder"),
    (("lora_name",), "lora"),
    (("control_net_name", "controlnet_name"), "controlnet"),
    (("upscale_model", "upscale_model_name"), "upscale_model"),
    (("style_model_name",), "style_model"),
    (("gligen_name",), "gligen"),
    (("model_name", "model_file", "model_path"), "model"),
)

_MEDIA_HISTORY_OUTPUTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("saveimage", "previewimage", "imageoutput", "imagesave"), "image", "images"),
    (("videocombine", "savevideo", "videooutput", "vhs_video"), "video", "gifs"),
    (("saveaudio", "audiooutput", "vhs_audio"), "audio", "audio"),
    (("savetext", "textoutput", "showtext"), "text", "text"),
)

_PROMPT_FIELDS = {
    "prompt",
    "text",
    "positive",
    "positive_prompt",
    "negative",
    "negative_prompt",
    "instruction",
    "edit_prompt",
}
_NUMERIC_CREATION_FIELDS = {
    "seed",
    "noise_seed",
    "steps",
    "cfg",
    "cfg_scale",
    "denoise",
    "width",
    "height",
    "batch_size",
    "duration",
    "frames",
    "frame_rate",
    "fps",
    "strength",
}
_CHOICE_CREATION_FIELDS = {"sampler_name", "scheduler", "sampler", "aspect_ratio"}
_FILEISH_FIELDS = {"file", "filename", "path", "filepath", "source", "input_file"}

# NCS-5E.2: recommendation policy is deliberately node-aware. These tokens do
# not hide a candidate; they only influence whether Neo proposes exposing it in
# the generated Space UI by default. Every detected candidate remains reviewable.
_PRESENTATION_NODE_TOKENS = (
    "addlabel", "label", "annotation", "annotate", "note", "markdown",
    "display", "debug", "previewany", "showtext",
)
_OUTPUT_UTILITY_NODE_TOKENS = ("saveimage", "savevideo", "saveaudio", "savetext", "previewimage")
_RUNTIME_TUNING_FIELDS = {
    "device", "dtype", "weight_dtype", "precision", "provider", "backend",
    "filename_prefix", "filename", "output_path", "save_path",
}
_SYSTEM_PROMPT_FIELDS = {"system_prompt", "system", "system_message"}


class SpaceWorkflowAnalysisError(ValueError):
    """Raised when an imported Comfy workflow cannot be normalized safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_token(value: Any, fallback: str = "value") -> str:
    text = str(value or "").strip().lower()
    text = _TOKEN_RE.sub("_", text).strip("._-")
    return text[:128] or fallback


def _analysis_value(value: Any, *, max_string: int = 1024, max_collection_items: int = 50) -> Any:
    """Keep reports useful without copying arbitrarily large inline payloads."""
    if isinstance(value, str):
        if len(value) <= max_string:
            return value
        return {
            "value_type": "string",
            "preview": value[:max_string],
            "truncated": True,
            "length": len(value),
        }
    if isinstance(value, list):
        if len(value) <= max_collection_items:
            return deepcopy(value)
        return {
            "value_type": "list",
            "preview": deepcopy(value[:max_collection_items]),
            "truncated": True,
            "length": len(value),
        }
    if isinstance(value, dict):
        if len(value) <= max_collection_items:
            return deepcopy(value)
        keys = list(value)[:max_collection_items]
        return {
            "value_type": "object",
            "preview": {key: deepcopy(value[key]) for key in keys},
            "truncated": True,
            "size": len(value),
        }
    return deepcopy(value)


def _is_absolute_machine_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text.startswith("/") or _WINDOWS_ABSOLUTE_RE.match(text))


def _looks_like_api_graph(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    seen = 0
    for node in value.values():
        if not isinstance(node, Mapping):
            return False
        if not str(node.get("class_type") or "").strip():
            return False
        seen += 1
    return seen > 0


def _editor_link_record(raw_link: Any) -> dict[str, Any] | None:
    if isinstance(raw_link, Mapping):
        item = dict(raw_link)
        required = ("id", "origin_id", "origin_slot", "target_id", "target_slot")
        if not all(key in item for key in required):
            return None
        return {
            "id": str(item["id"]),
            "origin_id": str(item["origin_id"]),
            "origin_slot": item["origin_slot"],
            "target_id": str(item["target_id"]),
            "target_slot": item["target_slot"],
            "type": item.get("type"),
        }
    if isinstance(raw_link, Sequence) and not isinstance(raw_link, (str, bytes, bytearray)) and len(raw_link) >= 5:
        return {
            "id": str(raw_link[0]),
            "origin_id": str(raw_link[1]),
            "origin_slot": raw_link[2],
            "target_id": str(raw_link[3]),
            "target_slot": raw_link[4],
            "type": raw_link[5] if len(raw_link) > 5 else None,
        }
    return None


def _editor_input_order(object_info: Mapping[str, Any] | None, class_type: str) -> list[str]:
    node = _mapping(_mapping(object_info).get(class_type))
    input_root = _mapping(node.get("input"))
    order_root = _mapping(node.get("input_order"))
    names: list[str] = []
    for bucket in ("required", "optional"):
        ordered = order_root.get(bucket)
        if isinstance(ordered, Sequence) and not isinstance(ordered, (str, bytes, bytearray)):
            bucket_names = [str(name) for name in ordered]
        else:
            bucket_names = [str(name) for name in _mapping(input_root.get(bucket)).keys()]
        for name in bucket_names:
            if name not in names:
                names.append(name)
    return names


def _editor_widget_spec_is_serializable(raw_spec: Any) -> bool:
    type_spec: Any = raw_spec
    options: Mapping[str, Any] = {}
    if isinstance(raw_spec, Sequence) and not isinstance(raw_spec, (str, bytes, bytearray)) and raw_spec:
        type_spec = raw_spec[0]
        if len(raw_spec) > 1 and isinstance(raw_spec[1], Mapping):
            options = raw_spec[1]
    elif isinstance(raw_spec, Mapping):
        type_spec = raw_spec.get("type") or raw_spec.get("datatype") or raw_spec.get("data_type")
        options = _mapping(raw_spec.get("options")) or raw_spec

    if isinstance(type_spec, Sequence) and not isinstance(type_spec, (str, bytes, bytearray)):
        return True
    type_name = str(type_spec or "").strip().upper()
    if type_name in {"INT", "FLOAT", "STRING", "BOOLEAN", "NUMBER", "COMBO"}:
        return True
    return "default" in options or bool(options.get("image_upload") or options.get("video_upload") or options.get("audio_upload"))


def _editor_widget_names_from_object_info(
    object_info: Mapping[str, Any] | None,
    class_type: str,
    *,
    linked_names: set[str],
) -> list[str]:
    node = _mapping(_mapping(object_info).get(class_type))
    input_root = _mapping(node.get("input"))
    raw_specs: dict[str, Any] = {}
    for bucket in ("required", "optional"):
        raw_specs.update(_mapping(input_root.get(bucket)))
    return [
        name
        for name in _editor_input_order(object_info, class_type)
        if name not in linked_names and name in raw_specs and _editor_widget_spec_is_serializable(raw_specs[name])
    ]


def _editor_wrap_literal(value: Any) -> Any:
    # Comfy API arrays are reserved for node links. The frontend wraps literal
    # array-valued widgets so the backend can distinguish them from [node, slot].
    if isinstance(value, list):
        return {"__value__": deepcopy(value)}
    return deepcopy(value)


def _editor_named_widget_values(node: Mapping[str, Any]) -> dict[str, Any]:
    named = node.get("widgets_values_named")
    if isinstance(named, Mapping):
        return {str(key): deepcopy(value) for key, value in named.items()}
    values = node.get("widgets_values")
    if isinstance(values, Mapping):
        return {str(key): deepcopy(value) for key, value in values.items()}
    return {}


def _editor_positional_widget_names(
    node: Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None,
    class_type: str,
) -> list[str]:
    inputs = node.get("inputs") if isinstance(node.get("inputs"), list) else []
    linked_names = {
        str(item.get("name") or "")
        for item in inputs
        if isinstance(item, Mapping) and item.get("link") is not None and str(item.get("name") or "")
    }
    explicit: list[str] = []
    for item in inputs:
        if not isinstance(item, Mapping) or item.get("link") is not None:
            continue
        widget = item.get("widget")
        if not isinstance(widget, Mapping):
            continue
        name = str(widget.get("name") or item.get("name") or "").strip()
        if name and name not in explicit:
            explicit.append(name)
    if explicit:
        return explicit
    return _editor_widget_names_from_object_info(object_info, class_type, linked_names=linked_names)


def _editor_map_positional_widgets(
    node: Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None,
    class_type: str,
    node_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = node.get("widgets_values")
    if not isinstance(values, list) or not values:
        return {}, []

    names = _editor_positional_widget_names(node, object_info=object_info, class_type=class_type)
    if not names:
        info = _mapping(object_info)
        if info and class_type not in info:
            raise SpaceWorkflowAnalysisError(
                f"Normal ComfyUI workflow node {node_id} uses node type {class_type}, but that node type is not loaded in the selected ComfyUI runtime. "
                "Open/check this workflow in that ComfyUI runtime and install or enable the custom-node pack that provides the node, then retry Runtime-aware analysis. "
                "Neo will not guess legacy positional widget names for a missing custom node."
            )
        raise SpaceWorkflowAnalysisError(
            f"Normal ComfyUI workflow node {node_id} ({class_type}) stores legacy positional widget values without names. "
            "Select a Comfy runtime and enable Runtime-aware analysis so Neo can use ComfyUI's live /object_info node schema, re-save the workflow in a current ComfyUI build, or use an API export for this workflow."
        )

    mapped: dict[str, Any] = {}
    warnings: list[dict[str, Any]] = []
    index = 0
    control_values = {"fixed", "increment", "decrement", "randomize"}
    for name in names:
        if index >= len(values):
            break
        mapped[name] = _editor_wrap_literal(values[index])
        index += 1
        if name in {"seed", "noise_seed"} and index < len(values) and values[index] in control_values:
            index += 1

    tail = values[index:]
    def _ignorable_tail_value(value: Any) -> bool:
        return (isinstance(value, str) and (value in control_values or value == "")) or value is None

    ignorable_tail = all(_ignorable_tail_value(value) for value in tail)
    if tail and not ignorable_tail:
        warnings.append(
            {
                "code": "editor_widget_tail_unmapped",
                "node_id": node_id,
                "class_type": class_type,
                "message": f"Neo mapped {len(mapped)} named widget values for node {node_id} but ignored {len(tail)} extra saved UI widget value(s). Enable Runtime-aware analysis for the strongest custom-node mapping.",
            }
        )
    return mapped, warnings


def _editor_visual_groups(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract ComfyUI canvas group membership as untrusted presentation hints.

    Membership is positional (the same geometry ComfyUI uses for its groups).
    We never alter graph links or allow group metadata to authorize execution.
    """
    raw_nodes = [node for node in (raw.get("nodes") or []) if isinstance(node, Mapping)]
    membership: dict[str, tuple[float, str]] = {}
    result: list[dict[str, Any]] = []

    def pair(value: Any) -> tuple[float, float] | None:
        if isinstance(value, Mapping):
            value = [value.get("0", value.get("x")), value.get("1", value.get("y"))]
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            x, y = float(value[0]), float(value[1])
        except (ValueError, TypeError, OverflowError):
            return None
        if not all(math.isfinite(z) for z in (x, y)):
            return None
        return x, y

    for index, raw_group in enumerate((raw.get("groups") or [])[:256] if isinstance(raw.get("groups"), list) else []):
        if not isinstance(raw_group, Mapping):
            continue
        title = str(raw_group.get("title") or "").strip()[:150]
        box = raw_group.get("bounding")
        if not title or not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        try:
            x, y, width, height = [float(n) for n in box[:4]]
        except (ValueError, TypeError, OverflowError):
            continue
        if not all(math.isfinite(n) for n in (x, y, width, height)) or width <= 0 or height <= 0:
            continue
        area = width * height
        key = f"native_{index + 1}"
        member_ids: list[str] = []
        for node in raw_nodes:
            node_id = str(node.get("id") or "")
            position = pair(node.get("pos"))
            size = pair(node.get("size")) or (0.0, 0.0)
            if not node_id or position is None:
                continue
            cx, cy = position[0] + size[0] / 2, position[1] + size[1] / 2
            if x <= cx <= x + width and y <= cy <= y + height:
                previous = membership.get(node_id)
                # Innermost group wins for nested/overlapping group boxes.
                if previous is None or area < previous[0]:
                    membership[node_id] = (area, key)
        result.append({"group_key": key, "title": title, "source_node_ids": member_ids})

    for node_id, (_, key) in membership.items():
        next(group for group in result if group["group_key"] == key)["source_node_ids"].append(node_id)
    return [group for group in result if group["source_node_ids"]]


def _convert_comfy_editor_workflow(
    raw: Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    original_source_node_count = len(raw.get("nodes") or []) if isinstance(raw.get("nodes"), list) else 0
    try:
        flattened_raw, subgraph_ir = flatten_editor_subgraphs(raw)
    except ComfySubgraphNormalizationError as exc:
        raise SpaceWorkflowAnalysisError(str(exc)) from exc

    nodes = flattened_raw.get("nodes")
    links = flattened_raw.get("links", [])
    if not isinstance(nodes, list):
        raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow JSON must contain a top-level nodes array.")
    if not isinstance(links, list):
        raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow JSON links must be an array.")
    if not nodes:
        raise SpaceWorkflowAnalysisError("Comfy workflow is empty.")

    node_map: dict[str, dict[str, Any]] = {}
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow contains a non-object node entry.")
        node_id = str(raw_node.get("id") if raw_node.get("id") is not None else "").strip()
        if not node_id:
            raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow contains a node without an id.")
        if node_id in node_map:
            raise SpaceWorkflowAnalysisError(f"Normal ComfyUI workflow contains duplicate node id {node_id}.")
        node_map[node_id] = deepcopy(dict(raw_node))

    link_map: dict[str, dict[str, Any]] = {}
    for raw_link in links:
        parsed = _editor_link_record(raw_link)
        if parsed is None:
            raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow contains an unsupported link record.")
        link_map[parsed["id"]] = parsed

    skipped: set[str] = set()
    skipped_details: list[dict[str, Any]] = []
    primitive_ids: set[str] = set()
    for node_id, node in node_map.items():
        class_type = str(node.get("type") or "").strip()
        mode = node.get("mode", 0)
        if class_type == "PrimitiveNode":
            primitive_ids.add(node_id)
            skipped.add(node_id)
            skipped_details.append({"node_id": node_id, "type": class_type, "reason": "virtual_primitive"})
            continue
        if class_type.lower() in {"note", "reroute"}:
            skipped.add(node_id)
            skipped_details.append({"node_id": node_id, "type": class_type, "reason": "frontend_only"})
            continue
        if mode == 2:
            skipped.add(node_id)
            skipped_details.append({"node_id": node_id, "type": class_type, "reason": "muted"})
            continue
        if mode == 4:
            touching = any(link["origin_id"] == node_id or link["target_id"] == node_id for link in link_map.values())
            if touching:
                raise SpaceWorkflowAnalysisError(
                    f"Normal ComfyUI workflow node {node_id} ({class_type or 'unknown'}) is bypassed. "
                    "Neo will not guess bypass rewiring from saved editor JSON. Un-bypass the node before importing, or use ComfyUI's API export for this workflow."
                )
            skipped.add(node_id)
            skipped_details.append({"node_id": node_id, "type": class_type, "reason": "bypassed_unconnected"})

    warnings: list[dict[str, Any]] = []
    canonical: dict[str, dict[str, Any]] = {}
    named_widget_nodes = 0
    positional_widget_nodes = 0
    schema_assisted_nodes = 0

    for node_id, node in node_map.items():
        if node_id in skipped:
            continue
        class_type = str(node.get("type") or "").strip()
        if not class_type:
            raise SpaceWorkflowAnalysisError(f"Normal ComfyUI workflow node {node_id} is missing its type.")

        api_inputs: dict[str, Any] = {}
        named_widgets = _editor_named_widget_values(node)
        if named_widgets:
            named_widget_nodes += 1
            known_names = set(_editor_input_order(object_info, class_type)) if _mapping(object_info).get(class_type) else set()
            for name, value in named_widgets.items():
                if name == "control_after_generate" or name.startswith("control_after_generate"):
                    continue
                if known_names and name not in known_names:
                    continue
                api_inputs[name] = _editor_wrap_literal(value)
        else:
            positional, positional_warnings = _editor_map_positional_widgets(
                node,
                object_info=object_info,
                class_type=class_type,
                node_id=node_id,
            )
            if positional:
                positional_widget_nodes += 1
                if _mapping(object_info).get(class_type):
                    schema_assisted_nodes += 1
                api_inputs.update(positional)
            warnings.extend(positional_warnings)

        editor_inputs = node.get("inputs") if isinstance(node.get("inputs"), list) else []
        for input_index, raw_input in enumerate(editor_inputs):
            if not isinstance(raw_input, Mapping):
                continue
            input_name = str(raw_input.get("name") or "").strip()
            if not input_name:
                continue
            link_id = raw_input.get("link")
            if link_id is None:
                if input_name not in api_inputs and "value" in raw_input:
                    api_inputs[input_name] = _editor_wrap_literal(raw_input.get("value"))
                continue
            link = link_map.get(str(link_id))
            if link is None:
                raise SpaceWorkflowAnalysisError(f"Normal ComfyUI workflow node {node_id}.{input_name} references missing link {link_id}.")
            if link["target_id"] != node_id:
                raise SpaceWorkflowAnalysisError(
                    f"Normal ComfyUI workflow link {link_id} target does not match node {node_id}.{input_name}."
                )
            source_id = link["origin_id"]
            source = node_map.get(source_id)
            if source is None:
                raise SpaceWorkflowAnalysisError(f"Normal ComfyUI workflow link {link_id} references missing source node {source_id}.")

            if source_id in primitive_ids:
                literal = None
                literal_found = False
                if input_name in named_widgets:
                    literal = named_widgets[input_name]
                    literal_found = True
                else:
                    source_named = _editor_named_widget_values(source)
                    if "value" in source_named:
                        literal = source_named["value"]
                        literal_found = True
                    else:
                        source_values = source.get("widgets_values")
                        if isinstance(source_values, list) and source_values:
                            literal = source_values[0]
                            literal_found = True
                if not literal_found:
                    raise SpaceWorkflowAnalysisError(
                        f"Neo could not resolve PrimitiveNode {source_id} feeding {node_id}.{input_name}. "
                        "Re-save the workflow in current ComfyUI or use API export for this workflow."
                    )
                api_inputs[input_name] = _editor_wrap_literal(literal)
                continue

            if source_id in skipped:
                # Matches Comfy's API exporter for muted/removed nodes: inputs
                # connected to nodes absent from the final prompt are removed.
                api_inputs.pop(input_name, None)
                warnings.append(
                    {
                        "code": "editor_link_to_skipped_node_removed",
                        "node_id": node_id,
                        "field": input_name,
                        "source_node_id": source_id,
                        "message": f"Input {node_id}.{input_name} was connected to a frontend-only or muted node and was omitted from the canonical prompt.",
                    }
                )
                continue

            try:
                origin_slot = int(link["origin_slot"])
            except (TypeError, ValueError) as exc:
                raise SpaceWorkflowAnalysisError(f"Normal ComfyUI workflow link {link_id} has an invalid origin slot.") from exc
            api_inputs[input_name] = [source_id, origin_slot]

        properties = _mapping(node.get("properties"))
        meta: dict[str, Any] = {"title": str(node.get("title") or class_type)}
        for source_key, target_key in (("cnr_id", "cnr_id"), ("aux_id", "aux_id"), ("ver", "ver")):
            value = properties.get(source_key)
            if value not in (None, ""):
                meta[target_key] = deepcopy(value)
        lineage = _mapping(node.get("_neo_subgraph"))
        if lineage:
            meta["source_node_id"] = str(lineage.get("source_node_id") or node_id)
            meta["subgraph_path"] = [str(item) for item in list(lineage.get("scope_path") or [])]
            meta["subgraph_depth"] = int(lineage.get("subgraph_depth") or 0)
            if str(lineage.get("definition_id") or ""):
                meta["subgraph_definition_id"] = str(lineage.get("definition_id"))
            if str(lineage.get("definition_name") or ""):
                meta["subgraph_definition_name"] = str(lineage.get("definition_name"))

        canonical[node_id] = {
            "inputs": api_inputs,
            "class_type": class_type,
            "_meta": meta,
        }

    # Final fail-closed sweep: canonical node links may only reference nodes that
    # survived conversion. This mirrors Comfy's own prompt export cleanup.
    for node in canonical.values():
        for field, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in canonical:
                node["inputs"].pop(field, None)

    if not canonical:
        raise SpaceWorkflowAnalysisError("Normal ComfyUI workflow did not contain any executable nodes after conversion.")

    raw_version = raw.get("version")
    version_text = str(raw_version) if raw_version is not None else "unknown"
    return canonical, {
        "source_shape": f"editor_workflow_v{version_text}",
        "wrapper_removed": False,
        "converted_from_editor": True,
        "editor_version": raw_version,
        "canonical_node_count": len(canonical),
        "source_node_count": original_source_node_count,
        "flattened_source_node_count": len(node_map),
        "subgraph_ir": deepcopy(subgraph_ir),
        "editor_groups": _editor_visual_groups(raw),
        "skipped_nodes": skipped_details,
        "named_widget_node_count": named_widget_nodes,
        "positional_widget_node_count": positional_widget_nodes,
        "schema_assisted_widget_node_count": schema_assisted_nodes,
        "conversion_warnings": warnings,
    }


def normalize_comfy_workflow(
    payload: Any,
    *,
    object_info: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return Neo's canonical Comfy API graph from normal Save JSON or API JSON.

    Normal ComfyUI Save/workflow JSON is the preferred user import format. API
    graphs remain accepted for compatibility. Editor-format conversion is purely
    structural and never executes nodes.
    """

    if not isinstance(payload, Mapping):
        raise SpaceWorkflowAnalysisError("Comfy workflow JSON must be an object.")

    raw = dict(payload)
    source_shape = "direct_api_graph"
    graph: Any = raw
    if _looks_like_api_graph(raw):
        graph = raw
    elif _looks_like_api_graph(raw.get("prompt")):
        graph = raw["prompt"]
        source_shape = "prompt_wrapper"
    elif _looks_like_api_graph(raw.get("workflow")):
        graph = raw["workflow"]
        source_shape = "workflow_wrapper"
    elif isinstance(raw.get("nodes"), list):
        return _convert_comfy_editor_workflow(raw, object_info=object_info)
    elif not raw:
        raise SpaceWorkflowAnalysisError("Comfy workflow is empty.")
    else:
        raise SpaceWorkflowAnalysisError(
            "Neo could not recognize this ComfyUI workflow JSON. Choose a normal ComfyUI Save/workflow JSON or an API-format workflow JSON."
        )

    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_node in graph.items():
        node_id = str(raw_node_id).strip()
        if not node_id:
            raise SpaceWorkflowAnalysisError("Comfy workflow contains an empty node id.")
        if not isinstance(raw_node, Mapping):
            raise SpaceWorkflowAnalysisError(f"Comfy node {node_id} must be an object.")
        class_type = str(raw_node.get("class_type") or "").strip()
        if not class_type:
            raise SpaceWorkflowAnalysisError(f"Comfy node {node_id} is missing class_type.")
        inputs = raw_node.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise SpaceWorkflowAnalysisError(f"Comfy node {node_id} inputs must be an object.")
        node = deepcopy(dict(raw_node))
        node["class_type"] = class_type
        node["inputs"] = deepcopy(dict(inputs))
        normalized[node_id] = node

    return normalized, {
        "source_shape": source_shape,
        "wrapper_removed": source_shape != "direct_api_graph",
        "converted_from_editor": False,
        "canonical_node_count": len(normalized),
        "conversion_warnings": [],
    }


def normalize_comfy_api_workflow(
    payload: Any,
    *,
    object_info: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Backward-compatible alias for the broader Comfy workflow normalizer."""

    return normalize_comfy_workflow(payload, object_info=object_info)


def _link(value: Any, node_ids: set[str]) -> tuple[str, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 2:
        return None
    source_id = str(value[0]).strip()
    raw_index = value[1]
    if source_id not in node_ids:
        return None
    if isinstance(raw_index, bool):
        return None
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    if index < 0 or str(raw_index).strip() != str(index):
        # Accept normal ints and digit strings, reject floats like 0.5.
        if not (isinstance(raw_index, str) and _LINK_INDEX_RE.fullmatch(raw_index.strip())):
            return None
    return source_id, index


def _parse_input_spec(raw: Any) -> dict[str, Any]:
    type_spec: Any = None
    options: dict[str, Any] = {}
    if isinstance(raw, (list, tuple)) and raw:
        type_spec = raw[0]
        if len(raw) > 1 and isinstance(raw[1], Mapping):
            options = dict(raw[1])
    elif isinstance(raw, Mapping):
        item = dict(raw)
        type_spec = item.get("type") or item.get("datatype") or item.get("data_type")
        if isinstance(item.get("options"), Mapping):
            options = dict(item["options"])
        else:
            options = {k: v for k, v in item.items() if k not in {"type", "datatype", "data_type"}}
    else:
        type_spec = raw

    choices: list[Any] = []
    type_name = ""
    if isinstance(type_spec, (list, tuple)):
        choices = list(type_spec)
        type_name = "COMBO"
    elif type_spec is not None:
        type_name = str(type_spec).strip()
    return {"type": type_name, "choices": choices, "options": options}


def _node_input_specs(object_info: Mapping[str, Any] | None, class_type: str) -> dict[str, dict[str, Any]]:
    info = _mapping(object_info)
    node = _mapping(info.get(class_type))
    input_root = _mapping(node.get("input"))
    specs: dict[str, dict[str, Any]] = {}
    for bucket in ("required", "optional"):
        for field, raw_spec in _mapping(input_root.get(bucket)).items():
            spec = _parse_input_spec(raw_spec)
            spec["required"] = bucket == "required"
            specs[str(field)] = spec
    return specs


def _output_specs(object_info: Mapping[str, Any] | None, class_type: str) -> list[dict[str, Any]]:
    node = _mapping(_mapping(object_info).get(class_type))
    outputs = node.get("output") if isinstance(node.get("output"), (list, tuple)) else []
    names = node.get("output_name") if isinstance(node.get("output_name"), (list, tuple)) else []
    result: list[dict[str, Any]] = []
    for index, raw_type in enumerate(outputs):
        type_name = str(raw_type or "").strip()
        result.append(
            {
                "index": index,
                "type": type_name,
                "name": str(names[index] if index < len(names) else "").strip(),
            }
        )
    return result


def _module_origin(object_info: Mapping[str, Any] | None, class_type: str) -> dict[str, Any]:
    node = _mapping(_mapping(object_info).get(class_type))
    python_module = str(node.get("python_module") or "").strip()
    category = str(node.get("category") or "").strip()
    if not python_module:
        origin = "unknown"
        package_hint = ""
    elif python_module == "nodes" or python_module.startswith("comfy_extras."):
        origin = "core"
        package_hint = "ComfyUI"
    elif python_module.startswith("custom_nodes."):
        origin = "custom"
        rest = python_module[len("custom_nodes.") :]
        package_hint = rest.split(".", 1)[0].strip()
    else:
        origin = "runtime_module"
        package_hint = python_module.split(".", 1)[0]
    return {
        "origin": origin,
        "python_module": python_module,
        "package_hint": package_hint,
        "category": category,
    }


def _model_type_for_field(field: str) -> str | None:
    lowered = str(field or "").strip().lower()
    for fields, model_type in _MODEL_FIELD_TYPES:
        if lowered in fields:
            return model_type
    if lowered.endswith("_model_name") or lowered.endswith("_model_file"):
        return "model"
    return None


def _field_looks_like_media_upload(class_type: str, field: str) -> str | None:
    cls = class_type.casefold()
    fld = field.casefold()
    if "mask" in cls and fld in {"image", "mask", "filename", "file"}:
        return "mask"
    if ("loadimage" in cls or "imageinput" in cls) and fld in {"image", "filename", "file"}:
        return "image"
    if ("video" in cls or "vhs_loadvideo" in cls) and fld in {"video", "filename", "file", "path"}:
        return "video"
    if "audio" in cls and ("load" in cls or "input" in cls) and fld in {"audio", "filename", "file", "path"}:
        return "audio"
    return None


def _kind_for_input(class_type: str, field: str, value: Any, spec: Mapping[str, Any]) -> str:
    media_kind = _field_looks_like_media_upload(class_type, field)
    if media_kind:
        return media_kind
    if _model_type_for_field(field):
        return "choice"
    fld = field.casefold()
    type_name = str(spec.get("type") or "").upper()
    choices = spec.get("choices") if isinstance(spec.get("choices"), list) else []
    if choices:
        return "choice"
    if type_name in {"STRING", "PROMPT"}:
        return "file" if fld in _FILEISH_FIELDS or fld.endswith("_path") or fld.endswith("_file") else "text"
    if type_name in {"INT", "INTEGER"}:
        return "integer"
    if type_name in {"FLOAT", "NUMBER", "DOUBLE"}:
        return "number"
    if type_name in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if type_name == "IMAGE":
        return "image"
    if type_name == "MASK":
        return "mask"
    if type_name == "VIDEO":
        return "video"
    if type_name == "AUDIO":
        return "audio"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "file" if fld in _FILEISH_FIELDS or fld.endswith("_path") or fld.endswith("_file") else "text"
    return "json"


def _node_input_role(
    class_type: str,
    field: str,
    kind: str,
    model_type: str | None,
    *,
    category: str = "",
) -> str:
    cls = class_type.casefold().replace("_", "")
    fld = field.casefold()
    cat = category.casefold()
    if _field_looks_like_media_upload(class_type, field):
        return "media_source"
    if model_type:
        return "model_loader"
    if "lora" in cls and fld.startswith("strength"):
        return "model_tuning"
    if any(token in cls for token in _PRESENTATION_NODE_TOKENS):
        return "presentation_utility"
    if any(token in cls for token in _OUTPUT_UTILITY_NODE_TOKENS) or cls.startswith("save"):
        return "output_utility"
    if "sampler" in cls or "sampling" in cat:
        return "sampler"
    if "latent" in cls and fld in {"width", "height", "batch_size", "frames"}:
        return "generation_shape"
    if any(token in cls for token in ("resize", "rescale", "upscale", "crop")):
        return "media_transform"
    if cls.startswith("primitive") or "primitive" in cls:
        return "parameter_source"
    if any(token in cls for token in ("textencode", "cliptext", "prompt", "textgenerate", "conditioning")):
        return "prompt_conditioning"
    if kind == "boolean":
        return "optional_toggle"
    return "processing_parameter"


def _input_score(
    class_type: str,
    field: str,
    kind: str,
    model_type: str | None,
    *,
    category: str = "",
    source_node: bool = False,
) -> tuple[int, list[str], str]:
    """Return a node-aware exposure score without removing any candidate.

    NCS-5E.2 intentionally separates *detection* from *recommendation*. A low
    score means "do not expose by default", never "hide from Builder".
    """

    cls = class_type.casefold()
    fld = field.casefold()
    role = _node_input_role(class_type, field, kind, model_type, category=category)
    reasons: list[str] = [f"node role: {role.replace('_', ' ')}"]
    score = 35

    if role == "media_source":
        score = 98
        reasons.append("source media loader input")
    elif role == "model_loader":
        score = 82
        reasons.append(f"model selector ({model_type})")
    elif fld in _SYSTEM_PROMPT_FIELDS:
        score = 45
        reasons.append("system-level prompt override")
    elif fld in {"prompt", "positive", "positive_prompt", "negative", "negative_prompt", "instruction", "edit_prompt"} or (
        "prompt" in fld and fld not in _SYSTEM_PROMPT_FIELDS
    ):
        score = 96 if role != "presentation_utility" else 45
        reasons.append("prompt-like text field")
    elif fld == "text":
        if role == "prompt_conditioning":
            score = 94
            reasons.append("text input on a prompt/conditioning node")
        elif role == "parameter_source":
            score = 72
            reasons.append("primitive text source")
        else:
            score = 40
            reasons.append("generic text field on a non-prompt node")
    elif role == "model_tuning":
        score = 74
        reasons.append("model/LoRA strength control")
    elif fld in _NUMERIC_CREATION_FIELDS:
        if role in {"presentation_utility", "output_utility"}:
            score = 40
            reasons.append("creation-like field belongs to presentation/output utility")
        else:
            score = 84
            reasons.append("common generation control")
    elif fld in _CHOICE_CREATION_FIELDS:
        score = 80
        reasons.append("common generation choice")
    elif role == "parameter_source" and fld in {"value", "text", "number", "integer", "float", "string"}:
        score = 72
        reasons.append("primitive parameter source")
    elif kind == "boolean":
        score = 58
        reasons.append("toggle-compatible value")
    else:
        reasons.append("literal node input")

    # Node/field semantics can veto a superficially attractive field name.
    if role == "presentation_utility":
        score = min(score, 45)
        reasons.append("presentation/annotation node is optional by default")
    if role == "output_utility" and fld not in {"quality", "format"}:
        score = min(score, 45)
        reasons.append("output utility setting is optional by default")
    if fld in _SYSTEM_PROMPT_FIELDS:
        score = min(score, 45)
    if fld in _RUNTIME_TUNING_FIELDS or fld.endswith("_device") or fld.endswith("_dtype"):
        score = min(score, 45)
        reasons.append("runtime/output implementation setting")
    if "save" in cls and fld in {"filename_prefix", "filename", "path"}:
        score = min(score, 40)
        reasons.append("output naming/path setting")
    if fld.startswith("_neo") or fld.startswith("internal_"):
        score = min(score, 5)
        reasons.append("internal-looking field")

    # Primitive source nodes exist specifically to inject a literal into the
    # graph, so a source primitive deserves a modest recommendation boost.
    if source_node and role == "parameter_source" and score < 60:
        score = 64
        reasons.append("source primitive feeds the workflow")

    return score, reasons, role


def _normalize_model_locator(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/+", "/", text)
    return text.strip("/")


def _resolve_runtime_model_choice(configured_name: str, choices: Sequence[Any]) -> dict[str, Any]:
    """Resolve a saved model locator against Comfy's live combo choices.

    Exact live combo values are authoritative. For normal Save workflows we also
    accept a *unique* normalized path or basename match because saved workflows
    can retain an old/extra relative folder prefix while the selected runtime
    exposes the same file under its current model-root-relative name. Ambiguous
    basename matches fail closed instead of guessing.
    """

    configured = str(configured_name or "").strip()
    catalog = [str(item).strip() for item in choices if isinstance(item, str) and str(item).strip()]
    if not catalog:
        return {
            "availability": None,
            "runtime_resolved_name": None,
            "resolution_method": "catalog_unavailable",
            "resolution_candidates": [],
        }
    if configured in catalog:
        return {
            "availability": True,
            "runtime_resolved_name": configured,
            "resolution_method": "exact",
            "resolution_candidates": [configured],
        }

    configured_norm = _normalize_model_locator(configured)
    normalized = {choice: _normalize_model_locator(choice) for choice in catalog}
    path_matches = [choice for choice, norm in normalized.items() if norm == configured_norm]
    if len(path_matches) == 1:
        return {
            "availability": True,
            "runtime_resolved_name": path_matches[0],
            "resolution_method": "normalized_path",
            "resolution_candidates": path_matches,
        }

    folded = configured_norm.casefold()
    casefold_matches = [choice for choice, norm in normalized.items() if norm.casefold() == folded]
    if len(casefold_matches) == 1:
        return {
            "availability": True,
            "runtime_resolved_name": casefold_matches[0],
            "resolution_method": "normalized_casefold_path",
            "resolution_candidates": casefold_matches,
        }

    basename = configured_norm.rsplit("/", 1)[-1].casefold()
    basename_matches = [
        choice for choice, norm in normalized.items()
        if norm.rsplit("/", 1)[-1].casefold() == basename
    ]
    if len(basename_matches) == 1:
        return {
            "availability": True,
            "runtime_resolved_name": basename_matches[0],
            "resolution_method": "unique_basename",
            "resolution_candidates": basename_matches,
        }
    if len(basename_matches) > 1:
        return {
            "availability": False,
            "runtime_resolved_name": None,
            "resolution_method": "ambiguous_basename",
            "resolution_candidates": basename_matches[:25],
        }
    return {
        "availability": False,
        "runtime_resolved_name": None,
        "resolution_method": "not_in_runtime_catalog",
        "resolution_candidates": [],
    }


def _kind_for_output_type(type_name: str) -> str | None:
    upper = str(type_name or "").strip().upper()
    if upper == "IMAGE":
        return "image"
    if upper == "MASK":
        return "mask"
    if upper in {"VIDEO", "VHS_VIDEO", "VIDEO_CLIP"}:
        return "video"
    if upper in {"AUDIO", "VHS_AUDIO"}:
        return "audio"
    if upper in {"STRING", "TEXT"}:
        return "text"
    if upper in {"INT", "INTEGER"}:
        return "integer"
    if upper in {"FLOAT", "NUMBER"}:
        return "number"
    if upper in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if upper in {"FILE", "FILENAME", "FILENAMES", "VHS_FILENAMES"}:
        return "file"
    return None


def _history_output_hint(class_type: str) -> tuple[str, str] | None:
    folded = class_type.casefold().replace("_", "")
    for tokens, kind, field in _MEDIA_HISTORY_OUTPUTS:
        if any(token in folded for token in tokens):
            return kind, field
    return None


def _candidate_id(node_id: str, class_type: str, field: str) -> str:
    return _safe_token(f"{class_type}.{field}.{node_id}")


def _output_candidate_id(node_id: str, class_type: str, suffix: str) -> str:
    return _safe_token(f"{class_type}.{suffix}.{node_id}", fallback=f"output_{node_id}")


def _graph_topology(node_ids: set[str], edges: list[dict[str, Any]]) -> dict[str, Any]:
    downstream: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    upstream: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        src = edge["source_node_id"]
        dst = edge["target_node_id"]
        if dst not in downstream[src]:
            downstream[src].add(dst)
            upstream[dst].add(src)
            indegree[dst] += 1

    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    topo: list[str] = []
    degrees = dict(indegree)
    while queue:
        node_id = queue.popleft()
        topo.append(node_id)
        for target in sorted(downstream[node_id]):
            degrees[target] -= 1
            if degrees[target] == 0:
                queue.append(target)
    cyclic = len(topo) != len(node_ids)

    # Connected components are useful to flag accidental orphan branches while
    # preserving valid multi-output graphs.
    neighbors = {node_id: set(upstream[node_id]) | set(downstream[node_id]) for node_id in node_ids}
    components: list[list[str]] = []
    unseen = set(node_ids)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: list[str] = []
        unseen.remove(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    return {
        "upstream": {key: sorted(value) for key, value in upstream.items()},
        "downstream": {key: sorted(value) for key, value in downstream.items()},
        "topological_order": topo if not cyclic else [],
        "has_cycle": cyclic,
        "component_count": len(components),
        "components": components,
        "source_nodes": sorted(node_id for node_id in node_ids if not upstream[node_id]),
        "terminal_nodes": sorted(node_id for node_id in node_ids if not downstream[node_id]),
    }


def analyze_comfy_workflow(
    payload: Any,
    *,
    object_info: Mapping[str, Any] | None = None,
    runtime_profile_id: str = "",
    runtime_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze a Comfy API workflow as data and return a versioned report.

    No nodes are imported or executed. ``object_info`` is optional; when omitted,
    structural graph analysis still completes and dependency readiness is marked
    ``not_checked`` rather than guessed.
    """

    graph, normalization = normalize_comfy_workflow(payload, object_info=object_info)
    node_ids = set(graph)
    info = _mapping(object_info)
    object_info_available = bool(info)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    warnings.extend(deepcopy(normalization.get("conversion_warnings") or []))
    edges: list[dict[str, Any]] = []

    # Detect link-looking values whose source is absent separately from ordinary
    # two-item constants. Numeric/string first elements are most likely Comfy links.
    for target_id, node in graph.items():
        for field, value in node["inputs"].items():
            resolved = _link(value, node_ids)
            if resolved:
                source_id, output_index = resolved
                edges.append(
                    {
                        "source_node_id": source_id,
                        "source_output_index": output_index,
                        "target_node_id": target_id,
                        "target_field": str(field),
                    }
                )
                continue
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and isinstance(value[0], (str, int))
                and isinstance(value[1], int)
                and str(value[0]).strip()
                and str(value[0]).strip() not in node_ids
            ):
                errors.append(
                    {
                        "code": "dangling_link",
                        "node_id": target_id,
                        "field": str(field),
                        "message": f"Node {target_id}.{field} references missing node {value[0]}.",
                    }
                )

    topology = _graph_topology(node_ids, edges)
    if topology["has_cycle"]:
        errors.append({"code": "graph_cycle", "message": "Comfy workflow graph contains a cycle."})
    if topology["component_count"] > 1:
        warnings.append(
            {
                "code": "disconnected_components",
                "message": f"Workflow contains {topology['component_count']} disconnected graph components.",
            }
        )

    class_counts = Counter(str(node["class_type"]) for node in graph.values())
    required_classes = sorted(class_counts)
    available_classes = sorted(item for item in required_classes if item in info) if object_info_available else []
    missing_classes = sorted(item for item in required_classes if item not in info) if object_info_available else []

    nodes: list[dict[str, Any]] = []
    input_candidates: list[dict[str, Any]] = []
    model_requirements: list[dict[str, Any]] = []
    output_candidates: list[dict[str, Any]] = []
    missing_models: list[dict[str, Any]] = []
    portability_issues: list[dict[str, Any]] = []

    edge_by_target = {(edge["target_node_id"], edge["target_field"]): edge for edge in edges}

    for node_id in sorted(graph, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else x)):
        node = graph[node_id]
        class_type = node["class_type"]
        specs = _node_input_specs(info, class_type) if object_info_available else {}
        origin = _module_origin(info, class_type) if object_info_available else {
            "origin": "unknown",
            "python_module": "",
            "package_hint": "",
            "category": "",
        }
        linked_inputs: list[dict[str, Any]] = []
        literal_inputs: list[str] = []

        for field, value in node["inputs"].items():
            edge = edge_by_target.get((node_id, str(field)))
            if edge:
                linked_inputs.append(
                    {
                        "field": str(field),
                        "source_node_id": edge["source_node_id"],
                        "source_output_index": edge["source_output_index"],
                    }
                )
                if object_info_available:
                    source_outputs = _output_specs(info, graph[edge["source_node_id"]]["class_type"])
                    if source_outputs and edge["source_output_index"] >= len(source_outputs):
                        errors.append(
                            {
                                "code": "invalid_output_index",
                                "node_id": node_id,
                                "field": str(field),
                                "message": (
                                    f"Node {node_id}.{field} requests output {edge['source_output_index']} from "
                                    f"node {edge['source_node_id']}, but live object_info exposes only {len(source_outputs)} output(s)."
                                ),
                            }
                        )
                continue

            literal_inputs.append(str(field))
            spec = specs.get(str(field), {})
            kind = _kind_for_input(class_type, str(field), value, spec)
            model_type = _model_type_for_field(str(field))
            score, reasons, node_role = _input_score(
                class_type,
                str(field),
                kind,
                model_type,
                category=str(origin.get("category") or ""),
                source_node=node_id in topology["source_nodes"],
            )
            choices = list(spec.get("choices") or [])
            node_meta = _mapping(node.get("_meta"))
            candidate = {
                "candidate_id": _candidate_id(node_id, class_type, str(field)),
                "suggested_input_id": _safe_token(str(field), fallback=f"input_{node_id}"),
                "node_id": node_id,
                "source_node_id": str(node_meta.get("source_node_id") or node_id),
                "subgraph_path": [str(item) for item in list(node_meta.get("subgraph_path") or [])],
                "subgraph_depth": int(node_meta.get("subgraph_depth") or 0),
                "subgraph_definition_id": str(node_meta.get("subgraph_definition_id") or ""),
                "subgraph_definition_name": str(node_meta.get("subgraph_definition_name") or ""),
                "class_type": class_type,
                "field": str(field),
                "kind": kind,
                "model_type": model_type,
                "current_value": _analysis_value(value),
                "required_by_node": bool(spec.get("required")) if spec else None,
                "score": score,
                "node_role": node_role,
                "recommendation_policy": "node_aware_v2",
                # Builder visibility and exposure are deliberately separate. Every
                # detected candidate remains reviewable; Neo only proposes whether
                # it should be exposed in the generated Space UI by default.
                "builder_visibility": "always",
                "default_exposed": score >= 60,
                "recommended": score >= 60,
                "reasons": reasons,
                "comfy_type": str(spec.get("type") or ""),
                "choice_count": len(choices),
                "choices_preview": deepcopy(choices[:100]),
                "constraints": {
                    key: deepcopy(spec.get("options", {}).get(key))
                    for key in ("min", "max", "step", "default", "multiline", "tooltip")
                    if key in _mapping(spec.get("options"))
                },
            }
            candidate["type_system"] = resolve_input_type_descriptor(candidate)
            input_candidates.append(candidate)

            if (kind == "file" or model_type) and _is_absolute_machine_path(value):
                portability_issues.append(
                    {
                        "code": "absolute_machine_path",
                        "node_id": node_id,
                        "field": str(field),
                        "kind": kind,
                        "message": f"Node {node_id}.{field} uses a machine-specific absolute path and must be remapped before Space publication.",
                    }
                )

            if model_type and isinstance(value, str) and value.strip():
                resolution = (
                    _resolve_runtime_model_choice(value.strip(), choices)
                    if object_info_available and class_type in info
                    else {
                        "availability": None,
                        "runtime_resolved_name": None,
                        "resolution_method": "not_checked",
                        "resolution_candidates": [],
                    }
                )
                availability = resolution["availability"]
                requirement = {
                    "requirement_id": _safe_token(f"{model_type}.{node_id}.{field}"),
                    "node_id": node_id,
                    "class_type": class_type,
                    "field": str(field),
                    "model_type": model_type,
                    "configured_name": value.strip(),
                    "availability": availability,
                    "runtime_resolved_name": resolution.get("runtime_resolved_name"),
                    "resolution_method": resolution.get("resolution_method"),
                    "resolution_candidates": deepcopy(resolution.get("resolution_candidates") or []),
                    "catalog_count": len(choices),
                    "catalog_source": "object_info_combo" if choices else ("object_info_no_combo" if object_info_available else "not_checked"),
                    "portable": not _is_absolute_machine_path(value),
                }
                model_requirements.append(requirement)
                if availability is False:
                    missing_models.append(requirement)
                    if requirement.get("resolution_method") == "ambiguous_basename":
                        warnings.append(
                            {
                                "code": "ambiguous_runtime_model_name",
                                "node_id": node_id,
                                "field": str(field),
                                "message": (
                                    f"Model '{value.strip()}' matches multiple files exposed by the selected ComfyUI runtime; "
                                    "Neo will not guess which runtime model should be used."
                                ),
                            }
                        )
                elif availability is True and requirement.get("runtime_resolved_name") and requirement.get("runtime_resolved_name") != value.strip():
                    warnings.append(
                        {
                            "code": "runtime_model_name_resolved",
                            "node_id": node_id,
                            "field": str(field),
                            "message": (
                                f"Model '{value.strip()}' resolves to runtime catalog entry "
                                f"'{requirement['runtime_resolved_name']}' via {requirement.get('resolution_method')}."
                            ),
                        }
                    )

        object_outputs = _output_specs(info, class_type) if object_info_available else []
        output_node = bool(_mapping(info.get(class_type)).get("output_node")) if object_info_available else False
        node_meta = _mapping(node.get("_meta"))
        node_record = {
            "node_id": node_id,
            "source_node_id": str(node_meta.get("source_node_id") or node_id),
            "subgraph_path": [str(item) for item in list(node_meta.get("subgraph_path") or [])],
            "subgraph_depth": int(node_meta.get("subgraph_depth") or 0),
            "subgraph_definition_id": str(node_meta.get("subgraph_definition_id") or ""),
            "subgraph_definition_name": str(node_meta.get("subgraph_definition_name") or ""),
            "class_type": class_type,
            "title": str(node_meta.get("title") or "").strip(),
            "origin": origin["origin"],
            "python_module": origin["python_module"],
            "package_hint": origin["package_hint"],
            "category": origin["category"],
            "literal_inputs": sorted(literal_inputs),
            "linked_inputs": linked_inputs,
            "upstream_node_ids": topology["upstream"].get(node_id, []),
            "downstream_node_ids": topology["downstream"].get(node_id, []),
            "terminal": node_id in topology["terminal_nodes"],
            "output_node": output_node,
            "outputs": object_outputs,
            "runtime_node_available": class_type in info if object_info_available else None,
        }
        nodes.append(node_record)

        terminal_or_output = node_record["terminal"] or output_node
        if terminal_or_output:
            for output in object_outputs:
                kind = _kind_for_output_type(output["type"])
                if not kind:
                    continue
                score = 82 + (8 if output_node else 0) + (5 if kind in {"image", "video", "audio"} else 0)
                output_candidates.append(
                    {
                        "candidate_id": _output_candidate_id(node_id, class_type, f"output_{output['index']}"),
                        "node_id": node_id,
                        "source_node_id": str(node_meta.get("source_node_id") or node_id),
                        "subgraph_path": [str(item) for item in list(node_meta.get("subgraph_path") or [])],
                        "subgraph_depth": int(node_meta.get("subgraph_depth") or 0),
                        "subgraph_definition_id": str(node_meta.get("subgraph_definition_id") or ""),
                        "subgraph_definition_name": str(node_meta.get("subgraph_definition_name") or ""),
                        "class_type": class_type,
                        "kind": kind,
                        "output_index": output["index"],
                        "field": None,
                        "comfy_type": output["type"],
                        "output_name": output["name"],
                        "score": min(score, 100),
                        "reason": "live output socket on a terminal/output node",
                        "binding_source": "node_output",
                    }
                )

            history_hint = _history_output_hint(class_type)
            if history_hint:
                kind, history_field = history_hint
                duplicate = any(item["node_id"] == node_id and item["kind"] == kind for item in output_candidates)
                if not duplicate:
                    output_candidates.append(
                        {
                            "candidate_id": _output_candidate_id(node_id, class_type, history_field),
                            "node_id": node_id,
                            "source_node_id": str(node_meta.get("source_node_id") or node_id),
                            "subgraph_path": [str(item) for item in list(node_meta.get("subgraph_path") or [])],
                            "subgraph_depth": int(node_meta.get("subgraph_depth") or 0),
                            "subgraph_definition_id": str(node_meta.get("subgraph_definition_id") or ""),
                            "subgraph_definition_name": str(node_meta.get("subgraph_definition_name") or ""),
                            "class_type": class_type,
                            "kind": kind,
                            "output_index": None,
                            "field": history_field,
                            "comfy_type": "",
                            "output_name": history_field,
                            "score": 96 if output_node else 90,
                            "reason": "known Comfy history/output-node artifact field",
                            "binding_source": "history_field",
                        }
                    )

            # If object_info is unavailable, still give the Builder a conservative
            # history-field candidate for well-known save/preview node families.
            if not object_info_available:
                history_hint = _history_output_hint(class_type)
                if history_hint:
                    kind, history_field = history_hint
                    if not any(item["node_id"] == node_id and item["field"] == history_field for item in output_candidates):
                        output_candidates.append(
                            {
                                "candidate_id": _output_candidate_id(node_id, class_type, history_field),
                                "node_id": node_id,
                                "source_node_id": str(node_meta.get("source_node_id") or node_id),
                                "subgraph_path": [str(item) for item in list(node_meta.get("subgraph_path") or [])],
                                "subgraph_depth": int(node_meta.get("subgraph_depth") or 0),
                                "subgraph_definition_id": str(node_meta.get("subgraph_definition_id") or ""),
                                "subgraph_definition_name": str(node_meta.get("subgraph_definition_name") or ""),
                                "class_type": class_type,
                                "kind": kind,
                                "output_index": None,
                                "field": history_field,
                                "comfy_type": "",
                                "output_name": history_field,
                                "score": 88,
                                "reason": "class-name output heuristic; verify against a live runtime before publishing",
                                "binding_source": "history_field_heuristic",
                            }
                        )

    input_candidates.sort(key=lambda item: (-item["score"], item["node_id"], item["field"]))
    suggested_ids = Counter(item["suggested_input_id"] for item in input_candidates)
    for item in input_candidates:
        if suggested_ids[item["suggested_input_id"]] > 1:
            item["suggested_input_id"] = _safe_token(
                f"{item['suggested_input_id']}_{item['node_id']}",
                fallback=f"input_{item['node_id']}",
            )

    output_candidates.sort(key=lambda item: (-item["score"], item["node_id"], item.get("output_index") or -1))
    for item in output_candidates:
        item["type_system"] = resolve_output_type_descriptor(item)
    for index, item in enumerate(output_candidates):
        base = item["kind"] if index else f"final_{item['kind']}"
        item["suggested_output_id"] = _safe_token(base, fallback=f"output_{item['node_id']}")
    if output_candidates:
        output_candidates[0]["suggested_primary"] = True
        for item in output_candidates[1:]:
            item["suggested_primary"] = False

    if not output_candidates:
        warnings.append(
            {
                "code": "no_output_candidate",
                "message": "Neo did not detect a user-facing output candidate. The Space Builder will require manual output mapping.",
            }
        )

    # NCS-10A: graph anchors are topology-read-only proposals derived from live
    # Comfy socket schemas plus canonical graph topology. They never rewire the
    # workflow during analysis. The authoring map separately proposes form
    # grouping from source-node/subgraph lineage; section state has no execution
    # authority.
    graph_anchors = analyze_graph_anchors(
        graph,
        edges,
        object_info=info if object_info_available else None,
        runtime_profile_id=runtime_profile_id,
        terminal_node_ids=topology["terminal_nodes"],
    )
    warnings.extend(deepcopy(graph_anchors.get("warnings") or []))
    authoring_map = build_authoring_map(
        input_candidates, nodes, topological_order=topology["topological_order"],
        editor_groups=normalization.get("editor_groups") or [],
    )

    runtime_probe_payload = _mapping(runtime_probe)
    unresolved_models = [item for item in model_requirements if item.get("availability") is None]
    if not object_info_available:
        preflight_state = "not_checked"
    elif missing_classes or missing_models:
        preflight_state = "blocked"
    elif unresolved_models:
        # A selected runtime was inspected, but one or more model loaders did not
        # expose a usable live combo catalog. Treat that as unchecked rather than
        # pretending the dependency is ready. Publication can warn; execution must
        # preflight again against the target runtime.
        preflight_state = "not_checked"
    else:
        preflight_state = "ready"

    custom_nodes: list[dict[str, Any]] = []
    if object_info_available:
        for class_type in required_classes:
            origin = _module_origin(info, class_type)
            custom_nodes.append(
                {
                    "class_type": class_type,
                    "available": class_type in info,
                    **origin,
                }
            )
    else:
        custom_nodes = [
            {
                "class_type": class_type,
                "available": None,
                "origin": "unknown",
                "python_module": "",
                "package_hint": "",
                "category": "",
            }
            for class_type in required_classes
        ]

    return {
        "schema_version": SPACE_ANALYSIS_SCHEMA_VERSION,
        "ok": not errors,
        "analysis_kind": "comfy_api_workflow",
        "execution_performed": False,
        "workflow_mutated": False,
        "normalization": normalization,
        "summary": {
            "node_count": len(graph),
            "edge_count": len(edges),
            "class_type_count": len(required_classes),
            "component_count": topology["component_count"],
            "source_node_count": len(topology["source_nodes"]),
            "terminal_node_count": len(topology["terminal_nodes"]),
            "input_candidate_count": len(input_candidates),
            "recommended_input_count": sum(1 for item in input_candidates if item["recommended"]),
            "node_aware_recommendation_count": sum(1 for item in input_candidates if item.get("recommendation_policy") == "node_aware_v2"),
            "output_candidate_count": len(output_candidates),
            "model_requirement_count": len(model_requirements),
            "runtime_resolved_model_count": sum(1 for item in model_requirements if item.get("availability") is True),
            "graph_anchor_proposal_count": int(graph_anchors.get("proposal_count") or 0),
            "graph_anchor_ambiguous_role_count": int(graph_anchors.get("ambiguous_role_count") or 0),
            "authoring_semantic_group_count": int(_mapping(authoring_map.get("summary")).get("semantic_group_count") or 0),
            "authoring_source_node_group_count": int(_mapping(authoring_map.get("summary")).get("source_node_group_count") or 0),
            "authoring_source_subgraph_group_count": int(_mapping(authoring_map.get("summary")).get("source_subgraph_group_count") or 0),
        },
        "graph": {
            "source_node_ids": topology["source_nodes"],
            "terminal_node_ids": topology["terminal_nodes"],
            "topological_order": topology["topological_order"],
            "has_cycle": topology["has_cycle"],
            "component_count": topology["component_count"],
            "components": topology["components"],
            "edges": edges,
            "nodes": nodes,
            "class_type_counts": dict(sorted(class_counts.items())),
        },
        "candidates": {
            "inputs": input_candidates,
            "outputs": output_candidates,
        },
        "graph_anchors": graph_anchors,
        "authoring_map": authoring_map,
        "type_system": {
            **type_system_capabilities(),
            "schema_version": SPACE_TYPE_SYSTEM_SCHEMA_VERSION,
            "typed_input_count": len(input_candidates),
            "typed_output_count": len(output_candidates),
            "all_detected_candidates_typed": True,
        },
        "dependencies": {
            "runtime_profile_id": str(runtime_profile_id or ""),
            "object_info_available": object_info_available,
            "object_info_node_count": len(info),
            "required_node_classes": required_classes,
            "available_node_classes": available_classes,
            "missing_node_classes": missing_classes,
            "node_requirements": custom_nodes,
            "model_requirements": model_requirements,
            "missing_models": missing_models,
            "unresolved_models": unresolved_models,
            "preflight_state": preflight_state,
            "runtime_probe": runtime_probe_payload,
        },
        "portability": {
            "ready": not portability_issues,
            "issues": portability_issues,
        },
        "builder_handoff": {
            "ready": not errors and not portability_issues,
            "auto_binding_is_advisory": True,
            "requires_human_review": True,
            "note": "NCS-2 proposes bindings and NCS-3 supplies native control/result descriptors. NCS-5 still requires Builder review; no manifest is auto-published here.",
        },
        "errors": errors,
        "warnings": warnings,
    }
