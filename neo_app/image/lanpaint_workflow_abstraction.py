from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

from neo_app.image.lanpaint_route_contract import BASE_STAGE_ORDER, ROUTE_FAMILY_ID

SCHEMA_ID = "neo.image.lanpaint_workflow_abstraction.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.image.lanpaint_workflow_abstraction"
ABSTRACTION_STATE = "abstraction_only"

# The base route owns image/mask/spatial/latent/sampling/stitch flow. Family policy
# supplies model, conditioning and VAE handles. Provider compilers bind concrete
# node classes only in later phases.
EXTERNAL_PORTS = (
    {
        "port_id": "family_model",
        "data_type": "MODEL_HANDLE",
        "owner": "family_policy",
        "required": True,
        "consumers": [{"stage_id": "family_model_transform", "port_id": "model"}],
    },
    {
        "port_id": "positive_conditioning",
        "data_type": "CONDITIONING_HANDLE",
        "owner": "family_policy",
        "required": True,
        "consumers": [{"stage_id": "lanpaint_sample", "port_id": "positive"}],
    },
    {
        "port_id": "negative_conditioning",
        "data_type": "CONDITIONING_HANDLE",
        "owner": "family_policy",
        "required": True,
        "consumers": [{"stage_id": "lanpaint_sample", "port_id": "negative"}],
    },
    {
        "port_id": "vae",
        "data_type": "VAE_HANDLE",
        "owner": "family_policy",
        "required": True,
        "consumers": [
            {"stage_id": "latent_encode", "port_id": "vae"},
            {"stage_id": "latent_decode", "port_id": "vae"},
        ],
    },
    {
        "port_id": "sampler_settings",
        "data_type": "SAMPLER_POLICY",
        "owner": "route_contract",
        "required": True,
        "consumers": [{"stage_id": "lanpaint_sample", "port_id": "settings"}],
    },
)


def _port(port_id: str, data_type: str, *, required: bool = True, cardinality: str = "one") -> dict[str, Any]:
    return {
        "port_id": port_id,
        "data_type": data_type,
        "required": required,
        "cardinality": cardinality,
    }


_STAGE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "source_image": {
        "category": "asset_input",
        "required": True,
        "inputs": [],
        "outputs": [_port("image", "IMAGE")],
        "responsibility": "Expose the Neo-owned source image to the base inpaint graph.",
    },
    "mask_image": {
        "category": "asset_input",
        "required": True,
        "inputs": [],
        "outputs": [_port("mask", "MASK")],
        "responsibility": "Expose the Neo-owned inpaint mask to the base graph.",
    },
    "crop_context": {
        "category": "spatial",
        "required": True,
        "inputs": [_port("image", "IMAGE"), _port("mask", "MASK")],
        "outputs": [
            _port("crop_image", "IMAGE"),
            _port("crop_mask", "MASK"),
            _port("crop_geometry", "CROP_GEOMETRY"),
        ],
        "responsibility": "Extract masked context and retain geometry required to restore and composite the patch.",
    },
    "processing_resize": {
        "category": "spatial",
        "required": True,
        "inputs": [_port("image", "IMAGE"), _port("mask", "MASK")],
        "outputs": [
            _port("process_image", "IMAGE"),
            _port("process_mask", "MASK"),
            _port("process_geometry", "PROCESS_GEOMETRY"),
        ],
        "responsibility": "Normalize the crop and mask to the route policy processing dimensions.",
    },
    "sampling_mask_refine": {
        "category": "mask",
        "required": True,
        "inputs": [_port("mask", "MASK")],
        "outputs": [_port("sampling_mask", "MASK")],
        "responsibility": "Prepare the mask used to control latent noise and LanPaint sampling.",
    },
    "latent_encode": {
        "category": "latent",
        "required": True,
        "inputs": [_port("pixels", "IMAGE"), _port("vae", "VAE_HANDLE")],
        "outputs": [_port("latent", "LATENT")],
        "responsibility": "Encode the processing image through the family-selected VAE.",
    },
    "latent_noise_mask": {
        "category": "latent",
        "required": True,
        "inputs": [_port("latent", "LATENT"), _port("mask", "MASK")],
        "outputs": [_port("masked_latent", "LATENT")],
        "responsibility": "Attach the refined sampling mask to the encoded latent.",
    },
    "family_model_transform": {
        "category": "model_transform",
        "required": False,
        "inputs": [_port("model", "MODEL_HANDLE")],
        "outputs": [_port("sample_model", "MODEL_HANDLE")],
        "responsibility": "Provide the family-owned insertion point for LoRA and optional model transforms before sampling.",
        "bypass": {
            "allowed": True,
            "input_port": "model",
            "output_port": "sample_model",
            "preserves_type": True,
        },
    },
    "lanpaint_sample": {
        "category": "sampling",
        "required": True,
        "inputs": [
            _port("model", "MODEL_HANDLE"),
            _port("positive", "CONDITIONING_HANDLE"),
            _port("negative", "CONDITIONING_HANDLE"),
            _port("latent", "LATENT"),
            _port("settings", "SAMPLER_POLICY"),
        ],
        "outputs": [_port("sampled_latent", "LATENT")],
        "responsibility": "Run the LanPaint sampling contract over the masked latent.",
    },
    "latent_decode": {
        "category": "decode",
        "required": True,
        "inputs": [_port("latent", "LATENT"), _port("vae", "VAE_HANDLE")],
        "outputs": [_port("process_image", "IMAGE")],
        "responsibility": "Decode the sampled latent through the same family-selected VAE.",
    },
    "restore_crop_size": {
        "category": "spatial",
        "required": True,
        "inputs": [_port("image", "IMAGE"), _port("crop_geometry", "CROP_GEOMETRY")],
        "outputs": [_port("restored_patch", "IMAGE")],
        "responsibility": "Restore the generated processing image to its source crop dimensions.",
    },
    "stitch_mask_refine": {
        "category": "mask",
        "required": True,
        "inputs": [_port("mask", "MASK"), _port("crop_geometry", "CROP_GEOMETRY")],
        "outputs": [_port("stitch_mask", "MASK")],
        "responsibility": "Prepare and restore the blend mask used for source-space compositing.",
    },
    "stitch_composite": {
        "category": "composite",
        "required": True,
        "inputs": [
            _port("destination", "IMAGE"),
            _port("source_patch", "IMAGE"),
            _port("mask", "MASK"),
            _port("crop_geometry", "CROP_GEOMETRY"),
        ],
        "outputs": [_port("image", "IMAGE")],
        "responsibility": "Composite the restored generated patch into the untouched source image.",
    },
    "output_handoff": {
        "category": "output",
        "required": True,
        "inputs": [_port("image", "IMAGE")],
        "outputs": [_port("final_image", "IMAGE")],
        "responsibility": "Publish the composited image to Neo output, metadata and lineage handling.",
    },
}


def _edge(
    source_stage: str,
    source_port: str,
    target_stage: str,
    target_port: str,
    *,
    edge_id: str | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id or f"{source_stage}.{source_port}->{target_stage}.{target_port}",
        "kind": "data",
        "required": True,
        "source": {"stage_id": source_stage, "port_id": source_port},
        "target": {"stage_id": target_stage, "port_id": target_port},
    }


BASE_DATA_EDGES = (
    _edge("source_image", "image", "crop_context", "image"),
    _edge("mask_image", "mask", "crop_context", "mask"),
    _edge("crop_context", "crop_image", "processing_resize", "image"),
    _edge("crop_context", "crop_mask", "processing_resize", "mask"),
    _edge("processing_resize", "process_mask", "sampling_mask_refine", "mask"),
    _edge("processing_resize", "process_image", "latent_encode", "pixels"),
    _edge("latent_encode", "latent", "latent_noise_mask", "latent"),
    _edge("sampling_mask_refine", "sampling_mask", "latent_noise_mask", "mask"),
    _edge("latent_noise_mask", "masked_latent", "lanpaint_sample", "latent"),
    _edge("family_model_transform", "sample_model", "lanpaint_sample", "model"),
    _edge("lanpaint_sample", "sampled_latent", "latent_decode", "latent"),
    _edge("latent_decode", "process_image", "restore_crop_size", "image"),
    _edge("crop_context", "crop_geometry", "restore_crop_size", "crop_geometry"),
    _edge("processing_resize", "process_mask", "stitch_mask_refine", "mask"),
    _edge("crop_context", "crop_geometry", "stitch_mask_refine", "crop_geometry"),
    _edge("source_image", "image", "stitch_composite", "destination"),
    _edge("restore_crop_size", "restored_patch", "stitch_composite", "source_patch"),
    _edge("stitch_mask_refine", "stitch_mask", "stitch_composite", "mask"),
    _edge("crop_context", "crop_geometry", "stitch_composite", "crop_geometry"),
    _edge("stitch_composite", "image", "output_handoff", "image"),
)


def _stage_spec(stage_id: str, ordinal: int) -> dict[str, Any]:
    definition = deepcopy(_STAGE_DEFINITIONS[stage_id])
    return {
        "stage_id": stage_id,
        "role_id": stage_id,
        "ordinal": ordinal,
        **definition,
        "binding": {
            "state": "unbound",
            "provider_id": None,
            "family_id": None,
            "loader_id": None,
            "compiler_id": None,
            "node_class": None,
        },
    }


def lanpaint_workflow_abstraction_fingerprint(abstraction: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(abstraction))
    payload.pop("validation", None)
    payload.pop("abstraction_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def topological_stage_order(abstraction: Mapping[str, Any]) -> list[str]:
    stages = [str(item.get("stage_id") or "") for item in abstraction.get("stages", []) if isinstance(item, Mapping)]
    stage_set = set(stages)
    indegree = {stage_id: 0 for stage_id in stages}
    adjacency = {stage_id: [] for stage_id in stages}
    for edge in abstraction.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        source = str((edge.get("source") or {}).get("stage_id") or "")
        target = str((edge.get("target") or {}).get("stage_id") or "")
        if source not in stage_set or target not in stage_set:
            continue
        adjacency[source].append(target)
        indegree[target] += 1

    ordinal = {stage_id: index for index, stage_id in enumerate(stages)}
    ready = sorted((stage_id for stage_id, degree in indegree.items() if degree == 0), key=ordinal.get)
    result: list[str] = []
    while ready:
        stage_id = ready.pop(0)
        result.append(stage_id)
        for target in sorted(adjacency[stage_id], key=ordinal.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=ordinal.get)
    return result


def _port_index(stage: Mapping[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("port_id") or ""): dict(item)
        for item in stage.get(key, [])
        if isinstance(item, Mapping) and item.get("port_id")
    }


def validate_lanpaint_workflow_abstraction(abstraction: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def error(field: str, message: str) -> None:
        issues.append({"level": "error", "field": field, "message": message})

    if abstraction.get("schema_id") != SCHEMA_ID:
        error("schema_id", f"Expected {SCHEMA_ID}.")
    if abstraction.get("schema_version") != SCHEMA_VERSION:
        error("schema_version", f"Expected schema version {SCHEMA_VERSION}.")
    if abstraction.get("authority") != AUTHORITY:
        error("authority", f"Expected {AUTHORITY}.")
    if abstraction.get("route_family_id") != ROUTE_FAMILY_ID:
        error("route_family_id", f"Expected {ROUTE_FAMILY_ID}.")
    if abstraction.get("abstraction_state") != ABSTRACTION_STATE:
        error("abstraction_state", f"Expected {ABSTRACTION_STATE}.")

    stage_order = list(abstraction.get("stage_order") or [])
    if tuple(stage_order) != BASE_STAGE_ORDER:
        error("stage_order", "Stage order must match the Phase 1 base route contract exactly.")

    stages = [item for item in abstraction.get("stages", []) if isinstance(item, Mapping)]
    stage_ids = [str(item.get("stage_id") or "") for item in stages]
    if stage_ids != list(BASE_STAGE_ORDER):
        error("stages", "Stages must appear once in canonical base-stage order.")
    if len(set(stage_ids)) != len(stage_ids):
        error("stages", "Duplicate stage ids are not allowed.")

    stage_by_id = {str(item.get("stage_id") or ""): item for item in stages}
    for expected_ordinal, stage_id in enumerate(BASE_STAGE_ORDER):
        stage = stage_by_id.get(stage_id)
        if stage is None:
            error(f"stages.{stage_id}", "Required logical stage is missing.")
            continue
        if stage.get("role_id") != stage_id:
            error(f"stages.{stage_id}.role_id", "Base logical role must remain identical to the canonical stage id.")
        if stage.get("ordinal") != expected_ordinal:
            error(f"stages.{stage_id}.ordinal", "Stage ordinal does not match canonical order.")
        binding = stage.get("binding") or {}
        if binding.get("state") != "unbound":
            error(f"stages.{stage_id}.binding.state", "Phase 2 bindings must remain unbound.")
        for key in ("provider_id", "family_id", "loader_id", "compiler_id", "node_class"):
            if binding.get(key) not in (None, ""):
                error(f"stages.{stage_id}.binding.{key}", "Concrete provider/family/compiler/node bindings are forbidden in Phase 2.")

    external_ids = {str(item.get("port_id") or "") for item in abstraction.get("external_ports", []) if isinstance(item, Mapping)}
    if external_ids != {str(item["port_id"]) for item in EXTERNAL_PORTS}:
        error("external_ports", "External family/route port identities do not match the canonical interface.")

    incoming: set[tuple[str, str]] = set()
    for index, edge in enumerate(abstraction.get("edges", [])):
        if not isinstance(edge, Mapping):
            error(f"edges.{index}", "Edge entries must be objects.")
            continue
        source = edge.get("source") or {}
        target = edge.get("target") or {}
        source_stage = str(source.get("stage_id") or "")
        target_stage = str(target.get("stage_id") or "")
        source_port = str(source.get("port_id") or "")
        target_port = str(target.get("port_id") or "")
        if source_stage not in stage_by_id:
            error(f"edges.{index}.source.stage_id", "Edge source stage is unknown.")
            continue
        if target_stage not in stage_by_id:
            error(f"edges.{index}.target.stage_id", "Edge target stage is unknown.")
            continue
        source_ports = _port_index(stage_by_id[source_stage], "outputs")
        target_ports = _port_index(stage_by_id[target_stage], "inputs")
        if source_port not in source_ports:
            error(f"edges.{index}.source.port_id", "Edge source port is not declared by the source stage.")
            continue
        if target_port not in target_ports:
            error(f"edges.{index}.target.port_id", "Edge target port is not declared by the target stage.")
            continue
        if source_ports[source_port].get("data_type") != target_ports[target_port].get("data_type"):
            error(f"edges.{index}", "Edge data types do not match.")
        key = (target_stage, target_port)
        if key in incoming:
            error(f"edges.{index}.target", "A base input port may have only one stage-to-stage producer.")
        incoming.add(key)

    external_consumers = {
        (str(consumer.get("stage_id") or ""), str(consumer.get("port_id") or ""))
        for port in abstraction.get("external_ports", [])
        if isinstance(port, Mapping)
        for consumer in port.get("consumers", [])
        if isinstance(consumer, Mapping)
    }
    for stage_id, stage in stage_by_id.items():
        for port_id, port in _port_index(stage, "inputs").items():
            if port.get("required") and (stage_id, port_id) not in incoming and (stage_id, port_id) not in external_consumers:
                error(f"stages.{stage_id}.inputs.{port_id}", "Required input has no stage or external producer.")

    topo = topological_stage_order(abstraction)
    if topo != list(BASE_STAGE_ORDER):
        error("edges", "Graph must be acyclic and topologically consistent with canonical stage order.")

    transform = stage_by_id.get("family_model_transform") or {}
    bypass = transform.get("bypass") or {}
    if not (bypass.get("allowed") is True and bypass.get("input_port") == "model" and bypass.get("output_port") == "sample_model" and bypass.get("preserves_type") is True):
        error("stages.family_model_transform.bypass", "The optional family transform must provide a type-preserving bypass.")

    insertion = abstraction.get("insertion_points") or {}
    model_transform = insertion.get("pre_sampler_model_transform") or {}
    if model_transform.get("stage_id") != "family_model_transform":
        error("insertion_points.pre_sampler_model_transform", "LoRA/family transforms must use the canonical pre-sampler insertion stage.")

    execution = abstraction.get("execution") or {}
    expected_execution = {
        "enabled": False,
        "selectable": False,
        "compiler_id": None,
        "workflow_type": None,
        "state": ABSTRACTION_STATE,
    }
    for key, expected in expected_execution.items():
        if execution.get(key) != expected:
            error(f"execution.{key}", f"Phase 2 execution field must remain {expected!r}.")

    return issues


def lanpaint_workflow_abstraction_template() -> dict[str, Any]:
    abstraction: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "route_family_id": ROUTE_FAMILY_ID,
        "abstraction_state": ABSTRACTION_STATE,
        "stage_order": list(BASE_STAGE_ORDER),
        "stages": [_stage_spec(stage_id, ordinal) for ordinal, stage_id in enumerate(BASE_STAGE_ORDER)],
        "edges": deepcopy(list(BASE_DATA_EDGES)),
        "external_ports": deepcopy(list(EXTERNAL_PORTS)),
        "insertion_points": {
            "pre_sampler_model_transform": {
                "stage_id": "family_model_transform",
                "owner": "family_policy",
                "supports_lora_stack": True,
                "supports_optional_family_transforms": True,
                "bypass_required": True,
            }
        },
        "compiler_interface": {
            "binding_state": "unbound",
            "concrete_node_classes": {},
            "provider_bindings": {},
            "family_bindings": {},
            "loader_bindings": {},
            "compiler_id": None,
            "workflow_type": None,
        },
        "invariants": [
            {
                "id": "source_and_mask_are_required",
                "description": "The route requires one Neo-owned source image and one mask.",
            },
            {
                "id": "untouched_pixels_preserved_by_source_composite",
                "description": "The final image is composited into the original source rather than replacing the full frame implicitly.",
            },
            {
                "id": "same_family_vae_encode_decode",
                "description": "Latent encode and decode consume the same family-selected VAE handle.",
            },
            {
                "id": "model_transforms_precede_lanpaint_sampling",
                "description": "LoRA and optional family model transforms occur only at the pre-sampler insertion point.",
            },
            {
                "id": "output_dimensions_follow_source_space",
                "description": "Restore and stitch stages return the generated patch to source-image geometry.",
            },
            {
                "id": "base_graph_has_no_concrete_node_classes",
                "description": "Concrete Comfy node classes are family/provider compiler bindings, not base-route identities.",
            },
        ],
        "execution": {
            "enabled": False,
            "selectable": False,
            "compiler_id": None,
            "workflow_type": None,
            "state": ABSTRACTION_STATE,
            "reason": "Phase 2 defines only the family-neutral logical graph and node-role interface.",
        },
    }
    issues = validate_lanpaint_workflow_abstraction(abstraction)
    abstraction["validation"] = {
        "ok": not any(item.get("level") == "error" for item in issues),
        "issues": issues,
        "topological_stage_order": topological_stage_order(abstraction),
        "concrete_bindings_present": False,
        "execution_ready": False,
    }
    abstraction["abstraction_fingerprint"] = lanpaint_workflow_abstraction_fingerprint(abstraction)
    return abstraction


def normalize_lanpaint_workflow_abstraction(value: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a supplied Phase 2 abstraction without activating or binding it.

    The canonical topology is immutable in Phase 2. This helper accepts a full
    abstraction for audit/tamper detection; missing input returns the canonical
    template.
    """

    if value is None:
        abstraction = lanpaint_workflow_abstraction_template()
        return abstraction, list(abstraction["validation"]["issues"])

    abstraction = deepcopy(dict(value))
    issues = validate_lanpaint_workflow_abstraction(abstraction)
    abstraction["validation"] = {
        "ok": not any(item.get("level") == "error" for item in issues),
        "issues": issues,
        "topological_stage_order": topological_stage_order(abstraction),
        "concrete_bindings_present": any(
            any((stage.get("binding") or {}).get(key) not in (None, "") for key in ("provider_id", "family_id", "loader_id", "compiler_id", "node_class"))
            for stage in abstraction.get("stages", [])
            if isinstance(stage, Mapping)
        ),
        "execution_ready": False,
    }
    abstraction["abstraction_fingerprint"] = lanpaint_workflow_abstraction_fingerprint(abstraction)
    return abstraction, issues


def stage_role_index(abstraction: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = abstraction or lanpaint_workflow_abstraction_template()
    return {
        str(stage.get("role_id") or ""): deepcopy(dict(stage))
        for stage in payload.get("stages", [])
        if isinstance(stage, Mapping) and stage.get("role_id")
    }


def external_port_index(abstraction: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = abstraction or lanpaint_workflow_abstraction_template()
    return {
        str(port.get("port_id") or ""): deepcopy(dict(port))
        for port in payload.get("external_ports", [])
        if isinstance(port, Mapping) and port.get("port_id")
    }


def concrete_binding_values(abstraction: Mapping[str, Any]) -> Iterable[Any]:
    for stage in abstraction.get("stages", []):
        if not isinstance(stage, Mapping):
            continue
        binding = stage.get("binding") or {}
        for key in ("provider_id", "family_id", "loader_id", "compiler_id", "node_class"):
            yield binding.get(key)
