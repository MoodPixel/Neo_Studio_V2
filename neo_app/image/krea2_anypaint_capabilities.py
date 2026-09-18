from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

SCHEMA_ID = "neo.image.krea2_anypaint_capabilities.v1"
AUTHORITY = "neo_app.image.krea2_anypaint_capabilities"
NODE_REPO = "alexw5702-afk/krea2-anypaint"
MODEL_PAGE = "yijunwang2/krea2-anypaint"
ADAPTER_BASENAME = "krea2_anypaint_rank32.safetensors"
SUPPORTED_FAMILY = "krea2_turbo"
SUPPORTED_LOADER = "diffusion_model"
SUPPORTED_MODES = ("inpaint", "outpaint")

REQUIRED_NODE_SIGNATURES: dict[str, dict[str, tuple[str, ...]]] = {
    "Krea2AnyPaintPrepare": {
        "required": (
            "source",
            "left",
            "top",
            "right",
            "bottom",
            "reference_max_edge",
            "boundary_redraw_px",
        ),
        "optional": ("generated_mask",),
    },
    "Krea2AnyPaintEncode": {
        "required": (
            "clip",
            "prompt",
            "vae",
            "semantic_reference",
            "known_image",
            "keep_mask",
            "vlm_reference",
        ),
        "optional": (),
    },
    "Krea2AnyPaintModelPatch": {
        "required": ("model", "kv_cache"),
        "optional": (),
    },
}

CORE_NODE_CLASSES = (
    "LoraLoaderModelOnly",
    "CLIPLoader",
    "VAELoader",
    "KSampler",
    "VAEDecode",
)

DISCOVERY_NODE_CLASSES = tuple(REQUIRED_NODE_SIGNATURES) + CORE_NODE_CLASSES + ("UNETLoader", "LoraLoader")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _node_inputs(object_info: Mapping[str, Any], node_name: str) -> dict[str, set[str]]:
    node = _mapping(object_info.get(node_name))
    inputs = _mapping(node.get("input"))
    required = set(str(key) for key in _mapping(inputs.get("required")).keys())
    optional = set(str(key) for key in _mapping(inputs.get("optional")).keys())
    return {"required": required, "optional": optional, "all": required | optional}


def _choices_from_node(object_info: Mapping[str, Any], node_name: str, *field_names: str) -> list[str]:
    node = _mapping(object_info.get(node_name))
    inputs = _mapping(node.get("input"))
    required = _mapping(inputs.get("required"))
    optional = _mapping(inputs.get("optional"))
    values: list[str] = []
    seen: set[str] = set()
    for field_name in field_names:
        spec = required.get(field_name, optional.get(field_name))
        if not isinstance(spec, (list, tuple)) or not spec:
            continue
        choices = spec[0]
        if not isinstance(choices, (list, tuple)):
            continue
        for item in choices:
            text = str(item or "").strip()
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                values.append(text)
    return values


def _portable_basename(value: str) -> str:
    clean = str(value or "").replace("\\", "/").strip()
    return PurePosixPath(clean).name.casefold() if clean else ""


def _anypaint_lora_candidates(object_info: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for node_name in ("LoraLoaderModelOnly", "LoraLoader"):
        for item in _choices_from_node(object_info, node_name, "lora_name"):
            if _portable_basename(item) != ADAPTER_BASENAME.casefold():
                continue
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                names.append(item)
    return names


def _qwen3vl_4b_candidates(object_info: Mapping[str, Any]) -> list[str]:
    values = _choices_from_node(object_info, "CLIPLoader", "clip_name")
    result: list[str] = []
    for item in values:
        normalized = item.casefold().replace("_", "-")
        if "qwen3" in normalized and "vl" in normalized and "4b" in normalized:
            result.append(item)
    return result


def _qwen_image_vae_candidates(object_info: Mapping[str, Any]) -> list[str]:
    values = _choices_from_node(object_info, "VAELoader", "vae_name")
    result: list[str] = []
    for item in values:
        normalized = item.casefold().replace("-", "_")
        if "qwen" in normalized and "image" in normalized and "vae" in normalized:
            result.append(item)
    return result


def _diffusion_model_catalog(object_info: Mapping[str, Any]) -> list[str]:
    return _choices_from_node(object_info, "UNETLoader", "unet_name", "model_name")


def _issue(code: str, message: str, *, field: str = "") -> dict[str, str]:
    payload = {"code": code, "message": message}
    if field:
        payload["field"] = field
    return payload


def inspect_krea2_anypaint_capabilities(
    object_info: Mapping[str, Any] | None,
    *,
    provider_id: str = "comfyui",
    reachable: bool = True,
    error: str = "",
) -> dict[str, Any]:
    """Discover Krea 2 AnyPaint as an optional Comfy capability.

    Phase 1 is discovery/readiness only. It intentionally does not enable an
    execution engine or compile an AnyPaint graph.
    """

    info = _mapping(object_info)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    remediation: list[str] = []

    node_checks: dict[str, Any] = {}
    for node_name, signature in REQUIRED_NODE_SIGNATURES.items():
        present = isinstance(info.get(node_name), Mapping)
        inputs = _node_inputs(info, node_name) if present else {"required": set(), "optional": set(), "all": set()}
        missing_required = [name for name in signature["required"] if name not in inputs["required"]]
        missing_optional = [name for name in signature["optional"] if name not in inputs["all"]]
        signature_ok = present and not missing_required and not missing_optional
        node_checks[node_name] = {
            "present": present,
            "signature_ok": signature_ok,
            "required_inputs": list(signature["required"]),
            "optional_inputs": list(signature["optional"]),
            "discovered_required_inputs": sorted(inputs["required"]),
            "discovered_optional_inputs": sorted(inputs["optional"]),
            "missing_required_inputs": missing_required,
            "missing_optional_inputs": missing_optional,
        }
        if not present:
            blockers.append(_issue("missing_anypaint_node", f"Missing required AnyPaint node: {node_name}.", field=node_name))
        elif not signature_ok:
            missing = missing_required + missing_optional
            blockers.append(_issue("incompatible_anypaint_node_signature", f"{node_name} is installed but its live input contract is missing: {', '.join(missing)}.", field=node_name))

    core_nodes = {node_name: isinstance(info.get(node_name), Mapping) for node_name in CORE_NODE_CLASSES}
    for node_name, present in core_nodes.items():
        if not present:
            blockers.append(_issue("missing_core_node", f"AnyPaint requires Comfy node {node_name}.", field=node_name))

    adapter_candidates = _anypaint_lora_candidates(info)
    if not adapter_candidates:
        blockers.append(_issue("missing_anypaint_adapter", f"Required AnyPaint adapter LoRA was not found: {ADAPTER_BASENAME}.", field="adapter_lora"))

    qwen3vl_candidates = _qwen3vl_4b_candidates(info)
    if not qwen3vl_candidates:
        blockers.append(_issue("missing_qwen3vl_4b", "No Qwen3-VL-4B text encoder candidate was found in CLIPLoader.", field="text_encoder"))

    qwen_vae_candidates = _qwen_image_vae_candidates(info)
    if not qwen_vae_candidates:
        blockers.append(_issue("missing_qwen_image_vae", "No Qwen Image VAE candidate was found in VAELoader.", field="vae"))

    diffusion_models = _diffusion_model_catalog(info)
    if not diffusion_models:
        blockers.append(_issue("missing_diffusion_model_catalog", "ComfyUI did not advertise any UNETLoader diffusion models for the Krea 2 Turbo route.", field="model"))
    else:
        warnings.append(_issue(
            "krea2_turbo_model_runtime_validation",
            "Phase 1 does not classify custom Krea 2 Turbo model filenames. The exact selected model will be validated against the explicit Krea 2 Turbo route at execution integration time.",
            field="model",
        ))

    if not reachable:
        blockers.insert(0, _issue("backend_unreachable", error or "ComfyUI /object_info is unavailable.", field="backend"))
        remediation.append("Connect/Test the selected ComfyUI profile, then refresh backend capabilities.")

    if any(item["code"] in {"missing_anypaint_node", "incompatible_anypaint_node_signature"} for item in blockers):
        remediation.append(f"Install or update {NODE_REPO} in ComfyUI/custom_nodes, restart ComfyUI, then refresh Neo backend capabilities.")
    if any(item["code"] == "missing_anypaint_adapter" for item in blockers):
        remediation.append(f"Place {ADAPTER_BASENAME} in ComfyUI/models/loras and refresh the Comfy model catalog.")
    if any(item["code"] == "missing_qwen3vl_4b" for item in blockers):
        remediation.append("Install a Qwen3-VL-4B Krea 2 text encoder visible to CLIPLoader(type=krea2).")
    if any(item["code"] == "missing_qwen_image_vae" for item in blockers):
        remediation.append("Install qwen_image_vae.safetensors or another Qwen Image VAE exposed by VAELoader.")

    capability_ready = not blockers
    if capability_ready:
        status = "ready_for_integration"
    elif any(item["code"] == "backend_unreachable" for item in blockers):
        status = "blocked_backend_unavailable"
    elif any(item["code"] in {"missing_anypaint_node", "incompatible_anypaint_node_signature", "missing_core_node"} for item in blockers):
        status = "blocked_missing_nodes"
    elif any(item["code"] == "missing_anypaint_adapter" for item in blockers):
        status = "blocked_missing_adapter"
    else:
        status = "blocked_missing_components"

    return {
        "schema_id": SCHEMA_ID,
        "authority": AUTHORITY,
        "phase": "phase4_standalone_compiler_ready",
        "provider_id": str(provider_id or "comfyui"),
        "status": status,
        "checked": True,
        "capability_ready": capability_ready,
        "execution_enabled": capability_ready,
        "supported_route": {
            "family": SUPPORTED_FAMILY,
            "loader": SUPPORTED_LOADER,
            "modes": list(SUPPORTED_MODES),
            "execution_phase": "standalone_compiler_phase4",
        },
        "node_pack": {
            "repo": NODE_REPO,
            "model_page": MODEL_PAGE,
            "required_nodes": list(REQUIRED_NODE_SIGNATURES),
            "nodes": node_checks,
            "core_nodes": core_nodes,
        },
        "adapter": {
            "required_basename": ADAPTER_BASENAME,
            "candidates": adapter_candidates,
            "available": bool(adapter_candidates),
        },
        "components": {
            "qwen3vl_4b_candidates": qwen3vl_candidates,
            "qwen_image_vae_candidates": qwen_vae_candidates,
            "diffusion_model_catalog_count": len(diffusion_models),
            "diffusion_model_catalog_sample": diffusion_models[:25],
        },
        "blockers": blockers,
        "warnings": warnings,
        "remediation": list(dict.fromkeys(remediation)),
        "notes": [
            "Phase 4 routes AnyPaint through its own standalone compiler module; Native Krea, Native Inpaint, LanPaint, and Krea 2 Identity Edit remain separate compiler authorities.",
            "The upstream AnyPaint graph uses model-only LoRA loading, Krea2AnyPaintPrepare, Krea2AnyPaintEncode, Krea2AnyPaintModelPatch, KSampler, and raw VAE decode without a final source composite.",
        ],
    }


__all__ = [
    "ADAPTER_BASENAME",
    "AUTHORITY",
    "DISCOVERY_NODE_CLASSES",
    "NODE_REPO",
    "REQUIRED_NODE_SIGNATURES",
    "SCHEMA_ID",
    "SUPPORTED_FAMILY",
    "SUPPORTED_LOADER",
    "SUPPORTED_MODES",
    "inspect_krea2_anypaint_capabilities",
]
