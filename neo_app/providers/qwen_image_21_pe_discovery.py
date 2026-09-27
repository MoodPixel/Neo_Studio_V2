from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

DISCOVERY_SCHEMA_ID = "neo.prompt_captioning.qwen_image_21_pe.discovery.v1"
ENGINE_ID = "qwen_image_21_pe"
NODE_REPO = "benjiyaya/ComfyUI-Qwen-Image-2.1-Prompt-Enhancer"
T2I_NODE = "QwenImage21_T2IPromptRewrite"
EDIT_NODE = "QwenImage21_EditPromptRewrite"
NEO_IMAGE_INPUT_NODE = "NeoPromptCaptionImageInput"
NEO_RESULT_OUTPUT_NODE = "NeoQwenImage21PromptEnhancerOutput"

KNOWN_T2I_FILENAME = "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"
KNOWN_EDIT_FILENAME = "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"

_T2I_REQUIRED_INPUTS = ("clip", "prompt")
_EDIT_REQUIRED_INPUTS = ("clip", "prompt", *(f"image_{i}" for i in range(1, 11)))
_T2I_EXPECTED_OUTPUTS = ("positive_prompt", "negative_prompt", "wh_ratio", "thinking", "parse_ok")
_EDIT_EXPECTED_OUTPUTS = ("positive_prompt", "negative_prompt", "wh_ratio", "ratio_follow", "thinking", "parse_ok")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _input_sections(node: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _mapping(node)
    inputs = _mapping(payload.get("input"))
    return _mapping(inputs.get("required")), _mapping(inputs.get("optional"))


def _input_names(node: Mapping[str, Any] | None) -> set[str]:
    required, optional = _input_sections(node)
    return {str(key) for key in required} | {str(key) for key in optional}


def _option_values(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item) for item in first]
    return []


def _node_outputs(node: Mapping[str, Any] | None) -> list[str]:
    payload = _mapping(node)
    for key in ("output_name", "output_names"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
    return []


def _basename(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    return PurePosixPath(normalized).name.casefold()


def classify_qwen_image_21_pe_model(value: str) -> str | None:
    """Classify only explicit Qwen Image 2.1 prompt-enhancer filenames.

    Generic qwen/qwen3.5/9b markers are intentionally insufficient. The role
    must carry the PE + T2I/I2I identity so normal Qwen text encoders cannot be
    mistaken for a prompt-enhancer checkpoint.
    """
    name = _basename(value)
    if not name:
        return None
    collapsed = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    q21_pe = "qwen" in collapsed and "image" in collapsed and ("2_1" in collapsed or "21" in collapsed) and "pe" in collapsed
    if not q21_pe:
        return None
    tokens = set(collapsed.split("_"))
    if "t2i" in tokens or collapsed.endswith("pe_t2i_int8_convrot_safetensors"):
        return "t2i"
    if "i2i" in tokens or "edit" in tokens or collapsed.endswith("pe_i2i_int8_convrot_safetensors"):
        return "edit"
    return None


def _model_records(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw in values or []:
        if isinstance(raw, Mapping):
            name = str(raw.get("name") or raw.get("filename") or "").strip()
            source = str(raw.get("source") or "profile_model_catalog").strip() or "profile_model_catalog"
        else:
            name = str(raw or "").strip()
            source = "explicit_text_encoder_catalog"
        if not name:
            continue
        key = name.casefold()
        record = records.setdefault(key, {"name": name, "sources": []})
        if source not in record["sources"]:
            record["sources"].append(source)
    return list(records.values())


def _clip_loader_catalog(object_info: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    node = _mapping(object_info.get("CLIPLoader"))
    required, optional = _input_sections(node)
    inputs = {**optional, **required}
    names: list[str] = []
    for key in ("clip_name", "clip_name1", "text_encoder_name"):
        for item in _option_values(inputs.get(key)):
            if item not in names:
                names.append(item)
    types = _option_values(inputs.get("type"))
    return names, types


def _node_contract(
    object_info: Mapping[str, Any],
    node_name: str,
    required_inputs: Iterable[str],
    expected_outputs: Iterable[str],
) -> dict[str, Any]:
    node = _mapping(object_info.get(node_name))
    present = bool(node)
    names = _input_names(node)
    required_inputs = tuple(required_inputs)
    missing_inputs = [item for item in required_inputs if item not in names]
    outputs = _node_outputs(node)
    missing_outputs = [item for item in expected_outputs if outputs and item not in outputs]
    return {
        "class_type": node_name,
        "present": present,
        "contract_ready": bool(present and not missing_inputs and not missing_outputs),
        "inputs": sorted(names),
        "required_inputs": list(required_inputs),
        "missing_inputs": missing_inputs,
        "outputs": outputs,
        "expected_outputs": list(expected_outputs),
        "missing_outputs": missing_outputs,
    }


def _candidate_records(records: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    known = KNOWN_T2I_FILENAME if role == "t2i" else KNOWN_EDIT_FILENAME
    result: list[dict[str, Any]] = []
    for record in records:
        if classify_qwen_image_21_pe_model(record["name"]) != role:
            continue
        result.append({
            **record,
            "role": role,
            "exact_known_filename": _basename(record["name"]) == known.casefold(),
        })
    return sorted(result, key=lambda item: (not item["exact_known_filename"], item["name"].casefold()))


def discover_qwen_image_21_pe(
    object_info: Mapping[str, Any] | None,
    *,
    text_encoders: Iterable[Any] | None = None,
    reachable: bool = True,
    error: str = "",
) -> dict[str, Any]:
    info = _mapping(object_info)
    object_info_available = bool(info)
    clip_names, clip_types = _clip_loader_catalog(info)

    merged_records = _model_records(text_encoders)
    known_keys = {item["name"].casefold() for item in merged_records}
    for name in clip_names:
        if name.casefold() not in known_keys:
            merged_records.append({"name": name, "sources": ["CLIPLoader.object_info"]})
            known_keys.add(name.casefold())

    t2i_models = _candidate_records(merged_records, "t2i")
    edit_models = _candidate_records(merged_records, "edit")

    t2i_node = _node_contract(info, T2I_NODE, _T2I_REQUIRED_INPUTS, _T2I_EXPECTED_OUTPUTS)
    edit_node = _node_contract(info, EDIT_NODE, _EDIT_REQUIRED_INPUTS, _EDIT_EXPECTED_OUTPUTS)
    image_bridge = _node_contract(info, NEO_IMAGE_INPUT_NODE, ("image_data_uri",), ("image",))
    result_bridge = _node_contract(
        info,
        NEO_RESULT_OUTPUT_NODE,
        ("positive_prompt", "negative_prompt", "wh_ratio", "ratio_follow", "parse_ok"),
        (),
    )

    qwen_image_type_ready = any(str(item).strip().casefold() == "qwen_image" for item in clip_types)
    core_cliploader_ready = bool(info.get("CLIPLoader")) and qwen_image_type_ready
    neo_image_bridge_ready = bool(image_bridge["contract_ready"])
    neo_result_bridge_ready = bool(result_bridge["contract_ready"])

    t2i_dependency_ready = bool(reachable and object_info_available and core_cliploader_ready and t2i_node["contract_ready"] and t2i_models)
    edit_dependency_ready = bool(reachable and object_info_available and core_cliploader_ready and edit_node["contract_ready"] and edit_models and neo_image_bridge_ready)
    t2i_execution_ready = bool(t2i_dependency_ready and neo_result_bridge_ready)
    edit_execution_ready = bool(edit_dependency_ready and neo_result_bridge_ready)

    t2i_blockers: list[str] = []
    edit_blockers: list[str] = []
    if not reachable:
        t2i_blockers.append("ComfyUI runtime is not reachable for QPE discovery.")
        edit_blockers.append("ComfyUI runtime is not reachable for QPE discovery.")
    elif not object_info_available:
        t2i_blockers.append("ComfyUI /object_info is unavailable; QPE node contracts cannot be verified.")
        edit_blockers.append("ComfyUI /object_info is unavailable; QPE node contracts cannot be verified.")
    if object_info_available and not core_cliploader_ready:
        message = "CLIPLoader(type=qwen_image) is not available on this ComfyUI runtime."
        t2i_blockers.append(message)
        edit_blockers.append(message)
    if not t2i_node["contract_ready"]:
        t2i_blockers.append(f"{T2I_NODE} is missing or its live socket/output contract is incompatible.")
    if not edit_node["contract_ready"]:
        edit_blockers.append(f"{EDIT_NODE} is missing or its live 1-10 reference socket/output contract is incompatible.")
    if not t2i_models:
        t2i_blockers.append("No Qwen Image 2.1 PE T2I text-encoder checkpoint was discovered.")
    if not edit_models:
        edit_blockers.append("No Qwen Image 2.1 PE I2I/Edit text-encoder checkpoint was discovered.")
    if not neo_image_bridge_ready:
        edit_blockers.append(f"{NEO_IMAGE_INPUT_NODE} is missing from ComfyUI.")
    if not neo_result_bridge_ready:
        terminal_message = f"{NEO_RESULT_OUTPUT_NODE} is missing from the connected ComfyUI backend; recopy Neo Studio's updated bundled neo_prompt_captioning node and restart ComfyUI."
        t2i_blockers.append(terminal_message)
        edit_blockers.append(terminal_message)

    next_actions: list[str] = []
    if not t2i_node["present"] or not edit_node["present"]:
        next_actions.append(f"Install/update {NODE_REPO} through Admin -> Extensions -> Node Manager, then restart ComfyUI and Connect/Test again.")
    if not t2i_models or not edit_models:
        next_actions.append("Place the role-specific Qwen Image 2.1 PE checkpoints under ComfyUI/models/text_encoders and reconnect/test the Comfy profile.")
    if not neo_image_bridge_ready:
        next_actions.append("Copy Neo Studio's bundled neo_prompt_captioning node folder into ComfyUI/custom_nodes and restart ComfyUI.")
    if not neo_result_bridge_ready:
        next_actions.append("Recopy Neo Studio's updated bundled neo_prompt_captioning node into ComfyUI/custom_nodes, restart ComfyUI, then Connect/Test again.")

    warnings = [error] if error else []
    if t2i_node["present"] and not t2i_node["contract_ready"]:
        warnings.append(f"{T2I_NODE} was detected but does not match Neo's QPE contract.")
    if edit_node["present"] and not edit_node["contract_ready"]:
        warnings.append(f"{EDIT_NODE} was detected but does not match Neo's 10-reference QPE Edit contract.")

    return {
        "schema_id": DISCOVERY_SCHEMA_ID,
        "engine_id": ENGINE_ID,
        "node_repo": NODE_REPO,
        "reachable": bool(reachable),
        "object_info_available": object_info_available,
        "core_cliploader_ready": core_cliploader_ready,
        "qwen_image_clip_type_ready": qwen_image_type_ready,
        "clip_loader_types": clip_types,
        "t2i_node_ready": bool(t2i_node["contract_ready"]),
        "edit_node_ready": bool(edit_node["contract_ready"]),
        "t2i_model_candidates": [item["name"] for item in t2i_models],
        "edit_model_candidates": [item["name"] for item in edit_models],
        "neo_image_bridge_ready": neo_image_bridge_ready,
        "neo_result_bridge_ready": neo_result_bridge_ready,
        "t2i_dependency_ready": t2i_dependency_ready,
        "edit_dependency_ready": edit_dependency_ready,
        "t2i_execution_ready": t2i_execution_ready,
        "edit_execution_ready": edit_execution_ready,
        "nodes": {
            "clip_loader": {
                "class_type": "CLIPLoader",
                "present": bool(info.get("CLIPLoader")),
                "qwen_image_type_ready": qwen_image_type_ready,
                "types": clip_types,
            },
            "t2i": t2i_node,
            "edit": edit_node,
        },
        "models": {
            "text_encoder_count": len(merged_records),
            "t2i_candidates": t2i_models,
            "edit_candidates": edit_models,
            "known_t2i_filename": KNOWN_T2I_FILENAME,
            "known_edit_filename": KNOWN_EDIT_FILENAME,
            "folder": "ComfyUI/models/text_encoders/",
        },
        "bridges": {
            "neo_image_input": image_bridge,
            "neo_result_output": result_bridge,
        },
        "readiness": {
            "t2i_dependency_ready": t2i_dependency_ready,
            "edit_dependency_ready": edit_dependency_ready,
            "t2i_execution_ready": t2i_execution_ready,
            "edit_execution_ready": edit_execution_ready,
            "t2i_blockers": t2i_blockers,
            "edit_blockers": edit_blockers,
            "next_actions": next_actions,
        },
        "license_notice": {
            "wrapper_code": "MIT",
            "model_weights": "Qwen Research License",
            "note": "The custom-node license does not change the model-weight license.",
        },
        "warnings": warnings,
    }


__all__ = [
    "DISCOVERY_SCHEMA_ID",
    "ENGINE_ID",
    "NODE_REPO",
    "T2I_NODE",
    "EDIT_NODE",
    "NEO_IMAGE_INPUT_NODE",
    "NEO_RESULT_OUTPUT_NODE",
    "KNOWN_T2I_FILENAME",
    "KNOWN_EDIT_FILENAME",
    "classify_qwen_image_21_pe_model",
    "discover_qwen_image_21_pe",
]
