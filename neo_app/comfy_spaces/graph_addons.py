from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.asset_bus import SpaceAssetBusError, resolve_asset_for_comfy
from neo_app.comfy_spaces.graph_anchors import ComfySpaceGraphAnchors, SpaceGraphAnchorDefinition
from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_GRAPH_ADDON_SCHEMA_VERSION = "neo.comfy_space.graph_addon.v1"
SPACE_GRAPH_ADDON_CATALOG_VERSION = "neo.comfy_space.graph_addon_catalog.v1"
SPACE_GRAPH_ADDON_COMPOSITION_VERSION = "neo.comfy_space.graph_addon_composition.v1"

_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")
_SIMPLE_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "BOOL"}
_GRAPH_TYPES = {
    "MODEL", "CONDITIONING", "LATENT", "LATENT_IMAGE", "IMAGE", "MASK", "VAE",
    "CLIP", "TEXT_ENCODER", "TEXT_ENCODER_MODEL", "VIDEO", "VHS_VIDEO", "VIDEO_CLIP",
    "AUDIO", "VHS_AUDIO", "CONTROL_NET",
}

GraphAddonLane = Literal["module", "raw_node"]
GraphAddonControlKind = Literal["number", "integer", "text", "toggle", "select", "asset"]


class SpaceGraphAddonError(ValueError):
    """Raised when a per-run Graph Add-on cannot be proven safe."""

    def __init__(self, message: str, *, code: str = "graph_addon_invalid") -> None:
        super().__init__(message)
        self.code = str(code or "graph_addon_invalid")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _safe_token(value: Any, fallback: str = "addon") -> str:
    text = _TOKEN_RE.sub("_", str(value or "").strip().lower()).strip("._-")
    return text[:96] or fallback


def _normalized_type(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _types_compatible(source_type: str, target_type: str) -> bool:
    source = _normalized_type(source_type)
    target = _normalized_type(target_type)
    if not source or not target:
        return False
    if source == target:
        return True
    aliases = (
        {"LATENT", "LATENT_IMAGE"},
        {"CLIP", "TEXT_ENCODER", "TEXT_ENCODER_MODEL"},
        {"VIDEO", "VHS_VIDEO", "VIDEO_CLIP"},
        {"AUDIO", "VHS_AUDIO"},
    )
    return any(source in group and target in group for group in aliases)


def _parse_input_spec(raw: Any) -> dict[str, Any]:
    type_spec: Any = None
    options: dict[str, Any] = {}
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) and raw:
        type_spec = raw[0]
        if len(raw) > 1 and isinstance(raw[1], Mapping):
            options = dict(raw[1])
    elif isinstance(raw, Mapping):
        item = dict(raw)
        type_spec = item.get("type") or item.get("datatype") or item.get("data_type")
        options = _mapping(item.get("options")) or item
    else:
        type_spec = raw
    combo_spec = isinstance(type_spec, Sequence) and not isinstance(type_spec, (str, bytes, bytearray))
    choices = list(type_spec) if combo_spec else []
    type_name = "COMBO" if combo_spec else str(type_spec or "").strip()
    return {"type": type_name, "choices": choices, "options": options}


def _node_inputs(object_info: Mapping[str, Any], class_type: str) -> list[dict[str, Any]]:
    node = _mapping(object_info.get(class_type))
    root = _mapping(node.get("input"))
    rows: list[dict[str, Any]] = []
    for bucket in ("required", "optional"):
        for field, raw in _mapping(root.get(bucket)).items():
            parsed = _parse_input_spec(raw)
            rows.append({"field": str(field), "required": bucket == "required", **parsed})
    return rows


def _node_outputs(object_info: Mapping[str, Any], class_type: str) -> list[dict[str, Any]]:
    node = _mapping(object_info.get(class_type))
    types = _sequence(node.get("output"))
    names = _sequence(node.get("output_name"))
    return [
        {
            "index": index,
            "type": str(raw_type or "").strip(),
            "name": str(names[index] if index < len(names) else ""),
        }
        for index, raw_type in enumerate(types)
    ]


def _display_name(object_info: Mapping[str, Any], class_type: str) -> str:
    node = _mapping(object_info.get(class_type))
    return str(node.get("display_name") or node.get("name") or class_type)


def _literal_control(row: Mapping[str, Any]) -> dict[str, Any] | None:
    field = str(row.get("field") or "")
    type_name = _normalized_type(row.get("type"))
    options = _mapping(row.get("options"))
    choices = list(row.get("choices") or [])
    required = bool(row.get("required"))
    if choices or type_name == "COMBO":
        if not choices:
            return None
        default = options.get("default")
        if default not in choices:
            default = choices[0] if choices else None
        return {
            "control_id": field,
            "label": field.replace("_", " ").strip().title(),
            "kind": "select",
            "required": required,
            "default": default,
            "options": choices,
            "runtime_catalog": True,
        }
    if type_name in {"INT"}:
        return {
            "control_id": field,
            "label": field.replace("_", " ").strip().title(),
            "kind": "integer",
            "required": required,
            "default": options.get("default"),
            "minimum": options.get("min"),
            "maximum": options.get("max"),
            "step": options.get("step"),
        }
    if type_name in {"FLOAT"}:
        return {
            "control_id": field,
            "label": field.replace("_", " ").strip().title(),
            "kind": "number",
            "required": required,
            "default": options.get("default"),
            "minimum": options.get("min"),
            "maximum": options.get("max"),
            "step": options.get("step"),
        }
    if type_name in {"BOOLEAN", "BOOL"}:
        return {
            "control_id": field,
            "label": field.replace("_", " ").strip().title(),
            "kind": "toggle",
            "required": required,
            "default": bool(options.get("default", False)),
        }
    if type_name == "STRING":
        return {
            "control_id": field,
            "label": field.replace("_", " ").strip().title(),
            "kind": "text",
            "required": required,
            "default": options.get("default", ""),
            "multiline": bool(options.get("multiline")),
        }
    return None


class SpaceGraphAddonControl(StrictContractModel):
    control_id: str
    label: str
    kind: GraphAddonControlKind
    required: bool = False
    default: Any = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    options: list[Any] = Field(default_factory=list)
    runtime_catalog: bool = False
    asset_kind: Literal["image", "mask", "video", "audio", "file"] | None = None
    multiline: bool = False


class SpaceGraphAddonModuleDefinition(StrictContractModel):
    schema_version: Literal[SPACE_GRAPH_ADDON_SCHEMA_VERSION] = SPACE_GRAPH_ADDON_SCHEMA_VERSION
    module_id: str
    version: str
    label: str
    description: str
    lane: Literal["module"] = "module"
    declarative_only: Literal[True] = True
    generated_python: Literal[False] = False
    generated_javascript: Literal[False] = False
    required_anchor_roles: list[str] = Field(default_factory=list)
    exclusive_anchor_roles: list[str] = Field(default_factory=list)
    runtime_node_classes: list[str] = Field(default_factory=list)
    controls: list[SpaceGraphAddonControl] = Field(default_factory=list)
    compatible: bool = False
    compatibility_message: str = ""
    resolved_runtime: dict[str, Any] = Field(default_factory=dict)


class SpaceGraphAddonRequest(StrictContractModel):
    schema_version: Literal[SPACE_GRAPH_ADDON_SCHEMA_VERSION] = SPACE_GRAPH_ADDON_SCHEMA_VERSION
    instance_id: str
    lane: GraphAddonLane
    module_id: str = ""
    module_version: str = ""
    anchor_ids: dict[str, str] = Field(default_factory=dict)
    raw_node_class: str = ""
    raw_anchor_id: str = ""
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, value: str) -> str:
        clean = _safe_token(value)
        if clean != str(value or "").strip().lower():
            raise ValueError("instance_id must already be a safe lowercase Neo id")
        return clean

    @model_validator(mode="after")
    def validate_lane_contract(self):
        if self.lane == "module":
            if not self.module_id or not self.module_version:
                raise ValueError("module Graph Add-ons require module_id and module_version")
            if self.raw_node_class or self.raw_anchor_id:
                raise ValueError("module Graph Add-ons cannot carry raw-node fields")
        else:
            if not self.raw_node_class or not self.raw_anchor_id:
                raise ValueError("raw-node Graph Add-ons require raw_node_class and raw_anchor_id")
            if self.module_id or self.module_version or self.anchor_ids:
                raise ValueError("raw-node Graph Add-ons cannot carry module fields")
        return self


class SpaceGraphAddonCatalog(StrictContractModel):
    schema_version: Literal[SPACE_GRAPH_ADDON_CATALOG_VERSION] = SPACE_GRAPH_ADDON_CATALOG_VERSION
    space_id: str
    version: str
    runtime_profile_id: str
    declarative_only: Literal[True] = True
    per_run_only: Literal[True] = True
    base_space_mutated: Literal[False] = False
    anchors: list[dict[str, Any]] = Field(default_factory=list)
    modules: list[SpaceGraphAddonModuleDefinition] = Field(default_factory=list)
    raw_nodes: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_anchor_count: int = Field(default=0, ge=0)
    ready_module_count: int = Field(default=0, ge=0)
    ready_raw_node_count: int = Field(default=0, ge=0)


class SpaceGraphAddonSchemaBundle(StrictContractModel):
    schema_version: Literal[SPACE_GRAPH_ADDON_SCHEMA_VERSION] = SPACE_GRAPH_ADDON_SCHEMA_VERSION
    request: SpaceGraphAddonRequest
    module: SpaceGraphAddonModuleDefinition


def parse_graph_addon_requests(payload: Sequence[Mapping[str, Any]] | None) -> list[SpaceGraphAddonRequest]:
    rows: list[SpaceGraphAddonRequest] = []
    seen: set[str] = set()
    for raw in list(payload or []):
        try:
            item = model_from_dict(SpaceGraphAddonRequest, dict(raw))
        except Exception as exc:
            raise SpaceGraphAddonError(f"Invalid Graph Add-on request: {exc}", code="graph_addon_request_invalid") from exc
        if item.instance_id in seen:
            raise SpaceGraphAddonError(f"Duplicate Graph Add-on instance_id: {item.instance_id}", code="graph_addon_instance_duplicate")
        seen.add(item.instance_id)
        rows.append(item)
    if len(rows) > 12:
        raise SpaceGraphAddonError("A single run may use at most 12 Graph Add-ons.", code="graph_addon_limit_exceeded")
    return rows


def _anchor_map(sidecar: ComfySpaceGraphAnchors | None) -> dict[str, SpaceGraphAnchorDefinition]:
    return {item.anchor_id: item for item in (sidecar.anchors if sidecar is not None else [])}


def _anchors_by_role(sidecar: ComfySpaceGraphAnchors | None) -> dict[str, list[SpaceGraphAnchorDefinition]]:
    rows: dict[str, list[SpaceGraphAnchorDefinition]] = {}
    for anchor in (sidecar.anchors if sidecar is not None else []):
        rows.setdefault(anchor.semantic_role, []).append(anchor)
    return rows


def _field_by_type(inputs: Sequence[Mapping[str, Any]], socket_type: str, *, name_hints: Sequence[str] = ()) -> str:
    target = _normalized_type(socket_type)
    candidates = [row for row in inputs if _normalized_type(row.get("type")) == target]
    hints = [str(item).casefold() for item in name_hints]
    if hints:
        hinted = [row for row in candidates if any(hint in str(row.get("field") or "").casefold() for hint in hints)]
        if len(hinted) == 1:
            return str(hinted[0].get("field") or "")
    return str(candidates[0].get("field") or "") if len(candidates) == 1 else ""


def _find_controlnet_runtime(object_info: Mapping[str, Any]) -> dict[str, Any] | None:
    loader_candidates: list[dict[str, Any]] = []
    apply_candidates: list[dict[str, Any]] = []
    image_loader_candidates: list[dict[str, Any]] = []
    for class_type in sorted(str(key) for key in object_info):
        inputs = _node_inputs(object_info, class_type)
        outputs = _node_outputs(object_info, class_type)
        output_types = [_normalized_type(row.get("type")) for row in outputs]
        input_types = [_normalized_type(row.get("type")) for row in inputs]
        if "CONTROL_NET" in output_types:
            combo = next((row for row in inputs if _normalized_type(row.get("type")) == "COMBO" and "control" in str(row.get("field") or "").casefold()), None)
            if combo:
                loader_candidates.append({"class_type": class_type, "model_field": str(combo["field"]), "model_options": list(combo.get("choices") or [])})
        if output_types.count("CONDITIONING") >= 2 and "CONTROL_NET" in input_types and "IMAGE" in input_types and input_types.count("CONDITIONING") >= 2:
            positive = _field_by_type(inputs, "CONDITIONING", name_hints=("positive",))
            negative = _field_by_type(inputs, "CONDITIONING", name_hints=("negative",))
            control_net = _field_by_type(inputs, "CONTROL_NET")
            image = _field_by_type(inputs, "IMAGE")
            by_field = {str(row.get("field") or ""): row for row in inputs}
            if positive and negative and positive != negative and control_net and image:
                apply_candidates.append({
                    "class_type": class_type,
                    "positive_field": positive,
                    "negative_field": negative,
                    "control_net_field": control_net,
                    "image_field": image,
                    "strength_field": "strength" if "strength" in by_field else "",
                    "start_field": "start_percent" if "start_percent" in by_field else ("start" if "start" in by_field else ""),
                    "end_field": "end_percent" if "end_percent" in by_field else ("end" if "end" in by_field else ""),
                })
        if "IMAGE" in output_types:
            image_combo = next((row for row in inputs if str(row.get("field") or "").casefold() == "image" and (row.get("choices") or _mapping(row.get("options")).get("image_upload"))), None)
            if image_combo:
                image_loader_candidates.append({"class_type": class_type, "image_field": str(image_combo["field"])})
    if not loader_candidates or not apply_candidates or not image_loader_candidates:
        return None
    def prefer(rows: list[dict[str, Any]], names: Sequence[str]) -> dict[str, Any]:
        for wanted in names:
            for row in rows:
                if row["class_type"] == wanted:
                    return row
        return rows[0]
    loader = prefer(loader_candidates, ("ControlNetLoader",))
    apply = prefer(apply_candidates, ("ControlNetApplyAdvanced", "ControlNetApply"))
    image_loader = prefer(image_loader_candidates, ("LoadImage",))
    return {"loader": loader, "apply": apply, "image_loader": image_loader}


def _controlnet_module(sidecar: ComfySpaceGraphAnchors | None, object_info: Mapping[str, Any]) -> SpaceGraphAddonModuleDefinition:
    by_role = _anchors_by_role(sidecar)
    runtime = _find_controlnet_runtime(object_info)
    anchors_ready = bool(by_role.get("POSITIVE_CONDITIONING") and by_role.get("NEGATIVE_CONDITIONING"))
    model_options = list(_mapping((runtime or {}).get("loader")).get("model_options") or [])
    compatible = bool(runtime and anchors_ready and model_options)
    if not sidecar or not sidecar.anchors:
        message = "This Space has no confirmed Graph Anchors. Confirm conditioning anchors in Builder and republish the Space."
    elif not anchors_ready:
        message = "ControlNet requires confirmed Positive Conditioning and Negative Conditioning anchors."
    elif not runtime:
        message = "The selected ComfyUI runtime does not expose a compatible ControlNet loader/apply/image-loader node set."
    elif not model_options:
        message = "The selected runtime exposes compatible ControlNet nodes but no ControlNet models in the live runtime catalog."
    else:
        message = "Ready on this runtime. The add-on exists only on the per-run execution clone."
    controls = [
        SpaceGraphAddonControl(control_id="control_image", label="Control image", kind="asset", required=True, asset_kind="image"),
        SpaceGraphAddonControl(control_id="controlnet_name", label="ControlNet model", kind="select", required=True, options=model_options, runtime_catalog=True),
        SpaceGraphAddonControl(control_id="strength", label="Strength", kind="number", required=False, default=1.0, minimum=0.0, maximum=10.0, step=0.01),
        SpaceGraphAddonControl(control_id="start_percent", label="Start", kind="number", required=False, default=0.0, minimum=0.0, maximum=1.0, step=0.01),
        SpaceGraphAddonControl(control_id="end_percent", label="End", kind="number", required=False, default=1.0, minimum=0.0, maximum=1.0, step=0.01),
    ]
    return SpaceGraphAddonModuleDefinition(
        module_id="neo.controlnet.conditioning",
        version="1.0.0",
        label="ControlNet",
        description="Temporarily insert a runtime-native ControlNet path into confirmed positive and negative conditioning edges.",
        required_anchor_roles=["POSITIVE_CONDITIONING", "NEGATIVE_CONDITIONING"],
        exclusive_anchor_roles=["POSITIVE_CONDITIONING", "NEGATIVE_CONDITIONING"],
        runtime_node_classes=[str(_mapping((runtime or {}).get(key)).get("class_type") or "") for key in ("image_loader", "loader", "apply") if _mapping((runtime or {}).get(key)).get("class_type")],
        controls=controls,
        compatible=compatible,
        compatibility_message=message,
        resolved_runtime=runtime or {},
    )


def _raw_literal_controls(object_info: Mapping[str, Any], class_type: str, connection_field: str) -> tuple[list[dict[str, Any]], bool]:
    controls: list[dict[str, Any]] = []
    for row in _node_inputs(object_info, class_type):
        field = str(row.get("field") or "")
        if field == connection_field:
            continue
        control = _literal_control(row)
        if control is None:
            if bool(row.get("required")):
                return [], False
            continue
        controls.append(control)
    return controls, True


def _raw_node_catalog(sidecar: ComfySpaceGraphAnchors | None, object_info: Mapping[str, Any]) -> list[dict[str, Any]]:
    anchors = [anchor for anchor in (sidecar.anchors if sidecar else []) if anchor.attachment_mode == "insert_between" and anchor.target is not None]
    rows: list[dict[str, Any]] = []
    for class_type in sorted(str(key) for key in object_info):
        inputs = _node_inputs(object_info, class_type)
        outputs = _node_outputs(object_info, class_type)
        placements: list[dict[str, Any]] = []
        for anchor in anchors:
            socket = str(anchor.evidence.socket_type or anchor.evidence.target_socket_type or anchor.evidence.source_socket_type or "")
            compatible_inputs = [row for row in inputs if _types_compatible(socket, str(row.get("type") or ""))]
            compatible_outputs = [row for row in outputs if _types_compatible(str(row.get("type") or ""), socket)]
            if len(compatible_inputs) != 1 or len(compatible_outputs) != 1:
                continue
            connection_field = str(compatible_inputs[0].get("field") or "")
            controls, safe = _raw_literal_controls(object_info, class_type, connection_field)
            if not safe:
                continue
            placements.append({
                "anchor_id": anchor.anchor_id,
                "semantic_role": anchor.semantic_role,
                "socket_type": socket,
                "connection_field": connection_field,
                "output_index": int(compatible_outputs[0].get("index") or 0),
                "controls": controls,
            })
        if placements:
            rows.append({
                "class_type": class_type,
                "display_name": _display_name(object_info, class_type),
                "lane": "raw_node",
                "safe_single_transform": True,
                "placements": placements,
            })
    return rows


def build_graph_addon_catalog(
    *,
    space_id: str,
    version: str,
    runtime_profile_id: str,
    graph_anchors: ComfySpaceGraphAnchors | None,
    object_info: Mapping[str, Any],
) -> dict[str, Any]:
    modules = [_controlnet_module(graph_anchors, object_info)]
    raw_nodes = _raw_node_catalog(graph_anchors, object_info)
    anchors = []
    for anchor in (graph_anchors.anchors if graph_anchors else []):
        source = anchor.source
        target = anchor.target
        anchors.append({
            "anchor_id": anchor.anchor_id,
            "semantic_role": anchor.semantic_role,
            "attachment_mode": anchor.attachment_mode,
            "socket_type": anchor.evidence.socket_type,
            "source_label": source.node_title or source.class_type or source.canonical_node_id,
            "source_node_id": source.source_node_id,
            "source_subgraph_path": list(source.subgraph_path),
            "target_label": (target.node_title or target.class_type or target.canonical_node_id) if target else "",
            "target_node_id": target.source_node_id if target else "",
            "target_input_field": target.input_field if target else "",
        })
    catalog = SpaceGraphAddonCatalog(
        space_id=str(space_id or "").strip().lower(),
        version=str(version or "").strip(),
        runtime_profile_id=str(runtime_profile_id or "").strip(),
        anchors=anchors,
        modules=modules,
        raw_nodes=raw_nodes,
        confirmed_anchor_count=len(graph_anchors.anchors if graph_anchors else []),
        ready_module_count=sum(1 for item in modules if item.compatible),
        ready_raw_node_count=len(raw_nodes),
    )
    return model_to_dict(catalog)


def _validate_anchor_edge(workflow: Mapping[str, Any], anchor: SpaceGraphAnchorDefinition) -> None:
    if anchor.attachment_mode != "insert_between" or anchor.target is None:
        raise SpaceGraphAddonError(f"Anchor {anchor.anchor_id} is not an insert-between edge.", code="graph_addon_anchor_mode_invalid")
    source_id = str(anchor.source.canonical_node_id)
    output_index = int(anchor.source.output_index if anchor.source.output_index is not None else 0)
    target_id = str(anchor.target.canonical_node_id)
    target_field = str(anchor.target.input_field or "")
    target = _mapping(workflow.get(target_id))
    inputs = _mapping(target.get("inputs"))
    current = inputs.get(target_field)
    if not (isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)) and len(current) >= 2):
        raise SpaceGraphAddonError(f"Confirmed anchor target is no longer connected: {anchor.anchor_id}", code="graph_addon_anchor_stale")
    if str(current[0]) != source_id or int(current[1]) != output_index:
        raise SpaceGraphAddonError(f"Confirmed anchor edge changed since publication: {anchor.anchor_id}", code="graph_addon_anchor_stale")


def _node_id(instance_id: str, suffix: str, workflow: Mapping[str, Any]) -> str:
    seed = f"{instance_id}.{suffix}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    base = f"neo_addon_{_safe_token(instance_id)}_{_safe_token(suffix)}_{digest}"
    if base in workflow:
        raise SpaceGraphAddonError(f"Graph Add-on node id collision: {base}", code="graph_addon_node_collision")
    return base


def _addon_meta(instance: SpaceGraphAddonRequest, *, module_id: str, class_type: str) -> dict[str, Any]:
    return {
        "title": f"Neo Add-on · {class_type}",
        "neo_graph_addon": True,
        "addon_instance_id": instance.instance_id,
        "addon_lane": instance.lane,
        "addon_module_id": module_id,
        "source_node_id": f"addon:{instance.instance_id}:{class_type}",
        "subgraph_path": ["@neo_graph_addons", instance.instance_id],
        "subgraph_depth": 1,
        "subgraph_definition_id": "",
        "subgraph_definition_name": "Neo Graph Add-on",
    }


def _coerce_control(control: Mapping[str, Any], raw: Any, *, field: str) -> Any:
    kind = str(control.get("kind") or "")
    required = bool(control.get("required"))
    if raw is None:
        raw = control.get("default")
    if raw is None and required:
        raise SpaceGraphAddonError(f"Required Graph Add-on control is missing: {field}", code="graph_addon_control_missing")
    if raw is None:
        return None
    if kind == "select":
        options = list(control.get("options") or [])
        if raw not in options:
            raise SpaceGraphAddonError(f"Graph Add-on value is not in the live runtime catalog: {field}", code="graph_addon_catalog_value_invalid")
        return raw
    if kind == "integer":
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise SpaceGraphAddonError(f"Graph Add-on control must be an integer: {field}", code="graph_addon_control_type_invalid") from exc
    elif kind == "number":
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise SpaceGraphAddonError(f"Graph Add-on control must be numeric: {field}", code="graph_addon_control_type_invalid") from exc
    elif kind == "toggle":
        if isinstance(raw, bool):
            value = raw
        elif isinstance(raw, int) and raw in {0, 1}:
            value = bool(raw)
        else:
            raise SpaceGraphAddonError(
                f"Graph Add-on control must be a boolean: {field}",
                code="graph_addon_control_type_invalid",
            )
    elif kind == "text":
        value = str(raw)
    else:
        return raw
    minimum = control.get("minimum")
    maximum = control.get("maximum")
    if kind in {"integer", "number"}:
        if minimum is not None and value < minimum:
            raise SpaceGraphAddonError(f"Graph Add-on control is below its runtime minimum: {field}", code="graph_addon_control_range_invalid")
        if maximum is not None and value > maximum:
            raise SpaceGraphAddonError(f"Graph Add-on control is above its runtime maximum: {field}", code="graph_addon_control_range_invalid")
    return value


def _resolve_asset_value(raw: Any, *, kind: str, runtime_profile_id: str, root_dir: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise SpaceGraphAddonError("Graph Add-on asset controls require a Neo asset reference.", code="graph_addon_asset_invalid")
    ref = dict(raw)
    if not ref.get("kind"):
        ref["kind"] = kind
    if str(ref.get("kind") or "") != kind:
        raise SpaceGraphAddonError(f"Graph Add-on asset kind must be {kind}.", code="graph_addon_asset_kind_invalid")
    try:
        resolution = resolve_asset_for_comfy(ref, runtime_profile_id=runtime_profile_id, root_dir=root_dir)
    except SpaceAssetBusError as exc:
        raise SpaceGraphAddonError(str(exc), code="graph_addon_asset_transport_failed") from exc
    return str(resolution.get("comfy_value") or ""), resolution


def _controlnet_anchor_for_role(request: SpaceGraphAddonRequest, anchor_map: Mapping[str, SpaceGraphAnchorDefinition], role: str) -> SpaceGraphAnchorDefinition:
    anchor_id = str(request.anchor_ids.get(role) or "")
    if not anchor_id:
        raise SpaceGraphAddonError(f"ControlNet add-on requires a confirmed {role} anchor.", code="graph_addon_anchor_missing")
    anchor = anchor_map.get(anchor_id)
    if anchor is None or anchor.semantic_role != role:
        raise SpaceGraphAddonError(f"Graph Add-on anchor does not match required role {role}.", code="graph_addon_anchor_role_invalid")
    return anchor


def _compose_controlnet(
    workflow: dict[str, Any],
    request: SpaceGraphAddonRequest,
    *,
    anchor_map: Mapping[str, SpaceGraphAnchorDefinition],
    object_info: Mapping[str, Any],
    runtime_profile_id: str,
    root_dir: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if request.module_id != "neo.controlnet.conditioning" or request.module_version != "1.0.0":
        raise SpaceGraphAddonError(f"Unknown Graph Add-on module: {request.module_id}@{request.module_version}", code="graph_addon_module_unknown")
    runtime = _find_controlnet_runtime(object_info)
    if not runtime:
        raise SpaceGraphAddonError("Selected runtime no longer exposes a compatible ControlNet node set.", code="graph_addon_runtime_nodes_missing")
    pos = _controlnet_anchor_for_role(request, anchor_map, "POSITIVE_CONDITIONING")
    neg = _controlnet_anchor_for_role(request, anchor_map, "NEGATIVE_CONDITIONING")
    _validate_anchor_edge(workflow, pos)
    _validate_anchor_edge(workflow, neg)

    loader_spec = _mapping(runtime.get("loader"))
    apply_spec = _mapping(runtime.get("apply"))
    image_spec = _mapping(runtime.get("image_loader"))
    model_options = list(loader_spec.get("model_options") or [])
    controls = {
        "controlnet_name": {"kind": "select", "required": True, "options": model_options},
        "strength": {"kind": "number", "required": False, "default": 1.0, "minimum": 0.0, "maximum": 10.0},
        "start_percent": {"kind": "number", "required": False, "default": 0.0, "minimum": 0.0, "maximum": 1.0},
        "end_percent": {"kind": "number", "required": False, "default": 1.0, "minimum": 0.0, "maximum": 1.0},
    }
    model_name = _coerce_control(controls["controlnet_name"], request.values.get("controlnet_name"), field="controlnet_name")
    strength = _coerce_control(controls["strength"], request.values.get("strength"), field="strength")
    start = _coerce_control(controls["start_percent"], request.values.get("start_percent"), field="start_percent")
    end = _coerce_control(controls["end_percent"], request.values.get("end_percent"), field="end_percent")
    if float(start) > float(end):
        raise SpaceGraphAddonError("ControlNet Start cannot be greater than End.", code="graph_addon_control_range_invalid")
    image_value, asset_resolution = _resolve_asset_value(request.values.get("control_image"), kind="image", runtime_profile_id=runtime_profile_id, root_dir=root_dir)

    image_id = _node_id(request.instance_id, "image", workflow)
    loader_id = _node_id(request.instance_id, "controlnet", workflow)
    apply_id = _node_id(request.instance_id, "apply", workflow)
    image_class = str(image_spec.get("class_type") or "")
    loader_class = str(loader_spec.get("class_type") or "")
    apply_class = str(apply_spec.get("class_type") or "")

    workflow[image_id] = {"class_type": image_class, "inputs": {str(image_spec.get("image_field") or "image"): image_value}, "_meta": _addon_meta(request, module_id=request.module_id, class_type=image_class)}
    workflow[loader_id] = {"class_type": loader_class, "inputs": {str(loader_spec.get("model_field") or "control_net_name"): model_name}, "_meta": _addon_meta(request, module_id=request.module_id, class_type=loader_class)}
    apply_inputs = {
        str(apply_spec.get("positive_field") or "positive"): [str(pos.source.canonical_node_id), int(pos.source.output_index or 0)],
        str(apply_spec.get("negative_field") or "negative"): [str(neg.source.canonical_node_id), int(neg.source.output_index or 0)],
        str(apply_spec.get("control_net_field") or "control_net"): [loader_id, 0],
        str(apply_spec.get("image_field") or "image"): [image_id, 0],
    }
    if apply_spec.get("strength_field"):
        apply_inputs[str(apply_spec["strength_field"])] = strength
    if apply_spec.get("start_field"):
        apply_inputs[str(apply_spec["start_field"])] = start
    if apply_spec.get("end_field"):
        apply_inputs[str(apply_spec["end_field"])] = end
    workflow[apply_id] = {"class_type": apply_class, "inputs": apply_inputs, "_meta": _addon_meta(request, module_id=request.module_id, class_type=apply_class)}

    for anchor, output_index in ((pos, 0), (neg, 1)):
        target = _mapping(workflow.get(str(anchor.target.canonical_node_id)))
        target_inputs = _mapping(target.get("inputs"))
        target_inputs[str(anchor.target.input_field or "")] = [apply_id, output_index]
        target["inputs"] = target_inputs
        workflow[str(anchor.target.canonical_node_id)] = target

    added = [
        {"node_id": image_id, "class_type": image_class, "instance_id": request.instance_id, "module_id": request.module_id},
        {"node_id": loader_id, "class_type": loader_class, "instance_id": request.instance_id, "module_id": request.module_id},
        {"node_id": apply_id, "class_type": apply_class, "instance_id": request.instance_id, "module_id": request.module_id},
    ]
    return added, [asset_resolution]


def _find_raw_catalog_entry(object_info: Mapping[str, Any], sidecar: ComfySpaceGraphAnchors | None, class_type: str, anchor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = next((item for item in _raw_node_catalog(sidecar, object_info) if str(item.get("class_type") or "") == class_type), None)
    if row is None:
        raise SpaceGraphAddonError("Raw Comfy node is not a safe single-transform candidate on this runtime.", code="graph_addon_raw_node_unsafe")
    placement = next((item for item in row.get("placements") or [] if str(item.get("anchor_id") or "") == anchor_id), None)
    if placement is None:
        raise SpaceGraphAddonError("Raw Comfy node is not compatible with the selected confirmed anchor.", code="graph_addon_raw_anchor_incompatible")
    return row, placement


def _compose_raw_node(
    workflow: dict[str, Any],
    request: SpaceGraphAddonRequest,
    *,
    sidecar: ComfySpaceGraphAnchors | None,
    anchor_map: Mapping[str, SpaceGraphAnchorDefinition],
    object_info: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    anchor = anchor_map.get(request.raw_anchor_id)
    if anchor is None:
        raise SpaceGraphAddonError(f"Unknown confirmed Graph Anchor: {request.raw_anchor_id}", code="graph_addon_anchor_unknown")
    _validate_anchor_edge(workflow, anchor)
    _, placement = _find_raw_catalog_entry(object_info, sidecar, request.raw_node_class, request.raw_anchor_id)
    inputs: dict[str, Any] = {
        str(placement.get("connection_field") or ""): [str(anchor.source.canonical_node_id), int(anchor.source.output_index or 0)]
    }
    known_controls = {str(item.get("control_id") or ""): item for item in placement.get("controls") or []}
    unknown = sorted(set(request.values) - set(known_controls))
    if unknown:
        raise SpaceGraphAddonError(f"Raw node request contains unknown literal controls: {', '.join(unknown)}", code="graph_addon_control_unknown")
    for field, control in known_controls.items():
        value = _coerce_control(control, request.values.get(field), field=field)
        if value is not None:
            inputs[field] = value
    node_id = _node_id(request.instance_id, "raw", workflow)
    workflow[node_id] = {
        "class_type": request.raw_node_class,
        "inputs": inputs,
        "_meta": _addon_meta(request, module_id="raw_node", class_type=request.raw_node_class),
    }
    target = _mapping(workflow.get(str(anchor.target.canonical_node_id)))
    target_inputs = _mapping(target.get("inputs"))
    target_inputs[str(anchor.target.input_field or "")] = [node_id, int(placement.get("output_index") or 0)]
    target["inputs"] = target_inputs
    workflow[str(anchor.target.canonical_node_id)] = target
    return [{"node_id": node_id, "class_type": request.raw_node_class, "instance_id": request.instance_id, "module_id": "raw_node"}], []


def compose_graph_addons(
    workflow: Mapping[str, Any],
    *,
    graph_anchors: ComfySpaceGraphAnchors | None,
    object_info: Mapping[str, Any],
    runtime_profile_id: str,
    requests: Sequence[SpaceGraphAddonRequest] | Sequence[Mapping[str, Any]] | None,
    root_dir: Any = None,
) -> dict[str, Any]:
    parsed = list(requests or [])
    if parsed and not isinstance(parsed[0], SpaceGraphAddonRequest):
        parsed = parse_graph_addon_requests(parsed)  # type: ignore[arg-type]
    parsed_requests: list[SpaceGraphAddonRequest] = list(parsed)  # type: ignore[assignment]
    cloned = deepcopy(dict(workflow))
    if not parsed_requests:
        return {
            "schema_version": SPACE_GRAPH_ADDON_COMPOSITION_VERSION,
            "ok": True,
            "workflow": cloned,
            "applied": [],
            "added_nodes": [],
            "asset_resolutions": [],
            "base_space_mutated": False,
            "published_workflow_mutated": False,
        }
    if graph_anchors is None or not graph_anchors.anchors:
        raise SpaceGraphAddonError("This published Space has no confirmed Graph Anchors for per-run composition.", code="graph_addon_anchors_unavailable")
    if not object_info:
        raise SpaceGraphAddonError("Live Comfy /object_info is required for Graph Add-on composition.", code="graph_addon_runtime_schema_unavailable")

    anchor_map = _anchor_map(graph_anchors)
    claimed: dict[str, str] = {}
    added_nodes: list[dict[str, Any]] = []
    asset_resolutions: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for request in parsed_requests:
        claim_ids: list[str]
        if request.lane == "module":
            if request.module_id == "neo.controlnet.conditioning":
                claim_ids = [str(request.anchor_ids.get("POSITIVE_CONDITIONING") or ""), str(request.anchor_ids.get("NEGATIVE_CONDITIONING") or "")]
            else:
                raise SpaceGraphAddonError(f"Unknown Graph Add-on module: {request.module_id}", code="graph_addon_module_unknown")
        else:
            claim_ids = [request.raw_anchor_id]
        for anchor_id in claim_ids:
            if not anchor_id:
                continue
            if anchor_id in claimed:
                raise SpaceGraphAddonError(
                    f"Graph Anchor {anchor_id} is already claimed by add-on {claimed[anchor_id]}; per-run anchor conflicts fail closed.",
                    code="graph_addon_anchor_conflict",
                )
            claimed[anchor_id] = request.instance_id

        if request.lane == "module":
            nodes, assets = _compose_controlnet(
                cloned,
                request,
                anchor_map=anchor_map,
                object_info=object_info,
                runtime_profile_id=runtime_profile_id,
                root_dir=root_dir,
            )
        else:
            nodes, assets = _compose_raw_node(
                cloned,
                request,
                sidecar=graph_anchors,
                anchor_map=anchor_map,
                object_info=object_info,
            )
        added_nodes.extend(nodes)
        asset_resolutions.extend(assets)
        applied.append({
            "instance_id": request.instance_id,
            "lane": request.lane,
            "module_id": request.module_id if request.lane == "module" else "raw_node",
            "raw_node_class": request.raw_node_class if request.lane == "raw_node" else "",
            "claimed_anchor_ids": claim_ids,
            "added_node_ids": [str(item.get("node_id") or "") for item in nodes],
        })

    return {
        "schema_version": SPACE_GRAPH_ADDON_COMPOSITION_VERSION,
        "ok": True,
        "workflow": cloned,
        "applied": applied,
        "added_nodes": added_nodes,
        "asset_resolutions": asset_resolutions,
        "base_space_mutated": False,
        "published_workflow_mutated": False,
    }
