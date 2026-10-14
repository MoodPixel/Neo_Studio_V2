from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_GRAPH_ANCHOR_SCHEMA_VERSION = "neo.comfy_space.graph_anchor.v1"
SPACE_GRAPH_ANCHOR_ANALYSIS_VERSION = "neo.comfy_space.graph_anchor_analysis.v1"

_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")

GraphAnchorRole = Literal[
    "MODEL",
    "POSITIVE_CONDITIONING",
    "NEGATIVE_CONDITIONING",
    "CONDITIONING",
    "LATENT",
    "IMAGE",
    "MASK",
    "VAE",
    "TEXT_ENCODER",
    "VIDEO",
    "AUDIO",
]
GraphAnchorAttachmentMode = Literal[
    "insert_between",
    "replace_input_source",
    "consume_upstream_output",
    "terminal_output",
]
GraphAnchorConfidence = Literal["high", "medium"]


def _safe_token(value: Any, fallback: str = "anchor") -> str:
    text = _TOKEN_RE.sub("_", str(value or "").strip().lower()).strip("._-")
    return text[:127] or fallback


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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

    if isinstance(type_spec, Sequence) and not isinstance(type_spec, (str, bytes, bytearray)):
        type_name = "COMBO"
    else:
        type_name = str(type_spec or "").strip()
    return {"type": type_name, "options": options}


def _input_socket_type(object_info: Mapping[str, Any], class_type: str, field: str) -> str:
    node = _mapping(object_info.get(class_type))
    input_root = _mapping(node.get("input"))
    for bucket in ("required", "optional"):
        specs = _mapping(input_root.get(bucket))
        if field in specs:
            return str(_parse_input_spec(specs[field]).get("type") or "").strip()
    return ""


def _output_socket_type(object_info: Mapping[str, Any], class_type: str, index: int) -> str:
    node = _mapping(object_info.get(class_type))
    outputs = node.get("output") if isinstance(node.get("output"), Sequence) and not isinstance(node.get("output"), (str, bytes, bytearray)) else []
    if index < 0 or index >= len(outputs):
        return ""
    return str(outputs[index] or "").strip()


def _output_socket_name(object_info: Mapping[str, Any], class_type: str, index: int) -> str:
    node = _mapping(object_info.get(class_type))
    names = node.get("output_name") if isinstance(node.get("output_name"), Sequence) and not isinstance(node.get("output_name"), (str, bytes, bytearray)) else []
    if index < 0 or index >= len(names):
        return ""
    return str(names[index] or "").strip()


def _normalized_type(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _types_compatible(source_type: str, target_type: str) -> bool:
    source = _normalized_type(source_type)
    target = _normalized_type(target_type)
    if not source or not target:
        return False
    if source == target:
        return True
    if "*" in {source, target, "ANY"}:
        return True
    aliases = {
        "VIDEO": {"VIDEO", "VHS_VIDEO", "VIDEO_CLIP"},
        "AUDIO": {"AUDIO", "VHS_AUDIO"},
        "LATENT": {"LATENT", "LATENT_IMAGE"},
        "TEXT_ENCODER": {"CLIP", "TEXT_ENCODER", "TEXT_ENCODER_MODEL"},
    }
    for values in aliases.values():
        if source in values and target in values:
            return True
    return False


def _semantic_role(socket_type: str, *, target_field: str = "", output_name: str = "") -> GraphAnchorRole | None:
    socket = _normalized_type(socket_type)
    field = str(target_field or "").strip().casefold()
    name = str(output_name or "").strip().casefold()

    if socket == "CONDITIONING":
        if "negative" in field or "negative" in name:
            return "NEGATIVE_CONDITIONING"
        if "positive" in field or "positive" in name:
            return "POSITIVE_CONDITIONING"
        return "CONDITIONING"
    if socket == "MODEL":
        return "MODEL"
    if socket in {"LATENT", "LATENT_IMAGE"}:
        return "LATENT"
    if socket == "IMAGE":
        return "IMAGE"
    if socket == "MASK":
        return "MASK"
    if socket == "VAE":
        return "VAE"
    if socket in {"CLIP", "TEXT_ENCODER", "TEXT_ENCODER_MODEL"}:
        return "TEXT_ENCODER"
    if socket in {"VIDEO", "VHS_VIDEO", "VIDEO_CLIP"}:
        return "VIDEO"
    if socket in {"AUDIO", "VHS_AUDIO"}:
        return "AUDIO"
    return None


def _lineage(node_id: str, node: Mapping[str, Any]) -> dict[str, Any]:
    meta = _mapping(node.get("_meta"))
    return {
        "canonical_node_id": str(node_id),
        "source_node_id": str(meta.get("source_node_id") or node_id),
        "subgraph_path": [str(item) for item in list(meta.get("subgraph_path") or [])],
        "subgraph_depth": int(meta.get("subgraph_depth") or 0),
        "subgraph_definition_id": str(meta.get("subgraph_definition_id") or ""),
        "subgraph_definition_name": str(meta.get("subgraph_definition_name") or ""),
        "node_title": str(meta.get("title") or node.get("class_type") or "").strip(),
        "class_type": str(node.get("class_type") or "").strip(),
    }


class SpaceGraphAnchorEndpoint(StrictContractModel):
    canonical_node_id: str
    source_node_id: str
    subgraph_path: list[str] = Field(default_factory=list)
    subgraph_depth: int = Field(default=0, ge=0)
    subgraph_definition_id: str = ""
    subgraph_definition_name: str = ""
    node_title: str = ""
    class_type: str
    output_index: int | None = Field(default=None, ge=0)
    input_field: str | None = None


class SpaceGraphAnchorEvidence(StrictContractModel):
    runtime_schema_used: bool = True
    detection_method: str = "runtime_socket_topology"
    source_socket_type: str = ""
    target_socket_type: str = ""
    socket_type: str
    socket_types_compatible: bool = True
    runtime_profile_id: str = ""


class SpaceGraphAnchorDefinition(StrictContractModel):
    anchor_id: str
    semantic_role: GraphAnchorRole
    attachment_mode: GraphAnchorAttachmentMode
    source: SpaceGraphAnchorEndpoint
    target: SpaceGraphAnchorEndpoint | None = None
    evidence: SpaceGraphAnchorEvidence
    confidence: GraphAnchorConfidence = "high"
    confirmed: bool = True
    author_confirmation_required: bool = True
    ambiguous_role: bool = False
    role_candidate_count: int = Field(default=1, ge=1)

    @field_validator("anchor_id")
    @classmethod
    def validate_anchor_id(cls, value: str) -> str:
        clean = _safe_token(value)
        if clean != str(value or "").strip().lower():
            raise ValueError("anchor_id must already be a safe lowercase Neo id")
        return clean


class ComfySpaceGraphAnchors(StrictContractModel):
    schema_version: Literal[SPACE_GRAPH_ANCHOR_SCHEMA_VERSION] = SPACE_GRAPH_ANCHOR_SCHEMA_VERSION
    kind: Literal["comfy_space_graph_anchors"] = "comfy_space_graph_anchors"
    space_id: str
    version: str
    declarative_only: Literal[True] = True
    topology_mutation_authority: Literal[False] = False
    execution_authority: Literal[False] = False
    anchors: list[SpaceGraphAnchorDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_anchor_ids(self):
        seen: set[str] = set()
        for item in self.anchors:
            if item.anchor_id in seen:
                raise ValueError(f"duplicate graph anchor id: {item.anchor_id}")
            seen.add(item.anchor_id)
            if not item.confirmed:
                raise ValueError("published graph anchor sidecar may contain only author-confirmed anchors")
        return self


def _stable_anchor_id(raw: str) -> str:
    digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:12]
    readable = _safe_token(raw)[:110].rstrip("._-") or "anchor"
    return f"{readable}.{digest}"


def _edge_anchor_id(role: str, source_id: str, output_index: int, target_id: str, target_field: str) -> str:
    return _stable_anchor_id(f"{role}.{source_id}.{output_index}.to.{target_id}.{target_field}")


def _terminal_anchor_id(role: str, node_id: str, output_index: int) -> str:
    return _stable_anchor_id(f"{role}.{node_id}.{output_index}.terminal")


def analyze_graph_anchors(
    graph: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    object_info: Mapping[str, Any] | None,
    runtime_profile_id: str = "",
    terminal_node_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Propose typed graph attachment points without mutating workflow topology.

    NCS-10A is intentionally runtime-schema-backed. When ``/object_info`` is not
    available Neo does not infer a graph anchor from class names or screen labels.
    The authoring map can still operate from source lineage, but topology changes
    remain blocked until Comfy socket semantics are proven.
    """

    info = _mapping(object_info)
    proposals: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    terminal_ids = {str(item) for item in terminal_node_ids}

    if info:
        for raw_edge in edges:
            edge = _mapping(raw_edge)
            source_id = str(edge.get("source_node_id") or "")
            target_id = str(edge.get("target_node_id") or "")
            target_field = str(edge.get("target_field") or "")
            try:
                output_index = int(edge.get("source_output_index"))
            except (TypeError, ValueError):
                continue
            source_node = _mapping(graph.get(source_id))
            target_node = _mapping(graph.get(target_id))
            if not source_node or not target_node:
                continue
            source_class = str(source_node.get("class_type") or "")
            target_class = str(target_node.get("class_type") or "")
            source_type = _output_socket_type(info, source_class, output_index)
            target_type = _input_socket_type(info, target_class, target_field)
            if not source_type or not target_type:
                continue
            if not _types_compatible(source_type, target_type):
                warnings.append(
                    {
                        "code": "graph_anchor_socket_type_mismatch",
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                        "target_field": target_field,
                        "source_socket_type": source_type,
                        "target_socket_type": target_type,
                        "message": (
                            f"Neo did not propose an anchor for {source_id}:{output_index} → {target_id}.{target_field} "
                            f"because live Comfy socket types disagree ({source_type} vs {target_type})."
                        ),
                    }
                )
                continue
            role = _semantic_role(target_type, target_field=target_field, output_name=_output_socket_name(info, source_class, output_index))
            if role is None:
                continue
            proposal = {
                "anchor_id": _edge_anchor_id(role, source_id, output_index, target_id, target_field),
                "semantic_role": role,
                "attachment_mode": "insert_between",
                "source": {**_lineage(source_id, source_node), "output_index": output_index, "input_field": None},
                "target": {**_lineage(target_id, target_node), "output_index": None, "input_field": target_field},
                "evidence": {
                    "runtime_schema_used": True,
                    "detection_method": "runtime_socket_topology",
                    "source_socket_type": source_type,
                    "target_socket_type": target_type,
                    "socket_type": target_type,
                    "socket_types_compatible": True,
                    "runtime_profile_id": str(runtime_profile_id or ""),
                },
                "confidence": "high",
                "confirmed": False,
                "author_confirmation_required": True,
            }
            proposals.append(proposal)

        # Terminal typed outputs are useful future add-on/output attachment points
        # even when there is no downstream consumer edge to insert into.
        for node_id in sorted(terminal_ids):
            node = _mapping(graph.get(node_id))
            if not node:
                continue
            class_type = str(node.get("class_type") or "")
            runtime_node = _mapping(info.get(class_type))
            outputs = runtime_node.get("output") if isinstance(runtime_node.get("output"), Sequence) and not isinstance(runtime_node.get("output"), (str, bytes, bytearray)) else []
            for index, raw_type in enumerate(outputs):
                socket_type = str(raw_type or "").strip()
                role = _semantic_role(socket_type, output_name=_output_socket_name(info, class_type, index))
                if role is None:
                    continue
                proposals.append(
                    {
                        "anchor_id": _terminal_anchor_id(role, node_id, index),
                        "semantic_role": role,
                        "attachment_mode": "terminal_output",
                        "source": {**_lineage(node_id, node), "output_index": index, "input_field": None},
                        "target": None,
                        "evidence": {
                            "runtime_schema_used": True,
                            "detection_method": "runtime_terminal_socket",
                            "source_socket_type": socket_type,
                            "target_socket_type": "",
                            "socket_type": socket_type,
                            "socket_types_compatible": True,
                            "runtime_profile_id": str(runtime_profile_id or ""),
                        },
                        "confidence": "high",
                        "confirmed": False,
                        "author_confirmation_required": True,
                    }
                )

    # De-duplicate stable IDs then annotate role ambiguity after the full pass.
    deduped: dict[str, dict[str, Any]] = {}
    for item in proposals:
        deduped.setdefault(str(item["anchor_id"]), item)
    proposals = list(deduped.values())
    role_counts = Counter(str(item.get("semantic_role") or "") for item in proposals)
    for item in proposals:
        count = max(1, role_counts[str(item.get("semantic_role") or "")])
        item["role_candidate_count"] = count
        item["ambiguous_role"] = count > 1

    proposals.sort(
        key=lambda item: (
            str(item.get("semantic_role") or ""),
            str(_mapping(item.get("source")).get("canonical_node_id") or ""),
            int(_mapping(item.get("source")).get("output_index") or 0),
            str(_mapping(item.get("target")).get("canonical_node_id") or ""),
            str(_mapping(item.get("target")).get("input_field") or ""),
        )
    )

    by_role: dict[str, list[str]] = {}
    for item in proposals:
        by_role.setdefault(str(item["semantic_role"]), []).append(str(item["anchor_id"]))

    return {
        "schema_version": SPACE_GRAPH_ANCHOR_ANALYSIS_VERSION,
        "contract_schema_version": SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
        "read_only": True,
        "workflow_mutated": False,
        "runtime_schema_required": True,
        "runtime_schema_available": bool(info),
        "runtime_profile_id": str(runtime_profile_id or ""),
        "proposal_count": len(proposals),
        "ambiguous_role_count": sum(1 for ids in by_role.values() if len(ids) > 1),
        "roles": sorted(by_role),
        "by_role": by_role,
        "proposals": proposals,
        "warnings": warnings,
        "status": "ready" if info else "not_checked",
    }


def parse_graph_anchor_sidecar(payload: Mapping[str, Any]) -> ComfySpaceGraphAnchors:
    return model_from_dict(ComfySpaceGraphAnchors, dict(payload))


def graph_anchor_sidecar_payload(sidecar: ComfySpaceGraphAnchors) -> dict[str, Any]:
    return model_to_dict(sidecar)


def compile_confirmed_graph_anchors(
    *,
    space_id: str,
    version: str,
    anchor_drafts: Sequence[Mapping[str, Any]],
    analysis: Mapping[str, Any],
) -> tuple[ComfySpaceGraphAnchors, list[dict[str, str]]]:
    """Compile author confirmation state against immutable frozen proposals."""

    proposal_root = _mapping(analysis.get("graph_anchors"))
    proposal_index = {
        str(_mapping(item).get("anchor_id") or ""): _mapping(item)
        for item in list(proposal_root.get("proposals") or [])
        if str(_mapping(item).get("anchor_id") or "")
    }
    issues: list[dict[str, str]] = []
    compiled: list[SpaceGraphAnchorDefinition] = []
    seen: set[str] = set()

    for raw in anchor_drafts:
        item = _mapping(raw)
        anchor_id = str(item.get("anchor_id") or "").strip().lower()
        if not anchor_id or not bool(item.get("confirmed")):
            continue
        if anchor_id in seen:
            issues.append({"code": "duplicate_confirmed_graph_anchor", "message": f"Graph anchor '{anchor_id}' is confirmed more than once."})
            continue
        seen.add(anchor_id)
        proposal = proposal_index.get(anchor_id)
        if proposal is None:
            issues.append({"code": "unknown_confirmed_graph_anchor", "message": f"Confirmed graph anchor '{anchor_id}' no longer exists in frozen analysis."})
            continue
        draft_role = str(item.get("semantic_role") or "")
        proposal_role = str(proposal.get("semantic_role") or "")
        if draft_role and draft_role != proposal_role:
            issues.append({"code": "graph_anchor_role_mismatch", "message": f"Graph anchor '{anchor_id}' role changed from '{proposal_role}' to '{draft_role}'."})
            continue
        definition_payload = deepcopy(proposal)
        definition_payload["confirmed"] = True
        try:
            compiled.append(model_from_dict(SpaceGraphAnchorDefinition, definition_payload))
        except Exception as exc:
            issues.append({"code": "invalid_confirmed_graph_anchor", "message": f"Graph anchor '{anchor_id}' is invalid: {exc}"})

    sidecar = ComfySpaceGraphAnchors(space_id=space_id, version=version, anchors=compiled)
    return sidecar, issues


def graph_anchor_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
        "analysis_schema_version": SPACE_GRAPH_ANCHOR_ANALYSIS_VERSION,
        "status": "ready",
        "topology_read_only": True,
        "runtime_socket_schema_authority": "comfy_object_info",
        "author_confirmation_required": True,
        "auto_confirmation": False,
        "supports_nested_subgraph_lineage": True,
        "attachment_modes": ["insert_between", "replace_input_source", "consume_upstream_output", "terminal_output"],
        "semantic_roles": [
            "MODEL", "POSITIVE_CONDITIONING", "NEGATIVE_CONDITIONING", "CONDITIONING",
            "LATENT", "IMAGE", "MASK", "VAE", "TEXT_ENCODER", "VIDEO", "AUDIO",
        ],
        "graph_mutation_implemented": True,
        "graph_mutation_scope": "per_run_execution_clone_only",
        "graph_addons_implemented": True,
        "stage_execution_implemented": False,
    }
