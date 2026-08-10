from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Iterable, Mapping
from uuid import uuid4

from neo_app.image.lanpaint_capabilities import PHASE8_STATE, evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_policies import COMPLETE_POLICY_STATE, PLACEHOLDER_POLICY_STATE
from neo_app.image.lanpaint_family_adapter import (
    PHASE13_STATE,
    adapter_asset_candidates,
    adapter_snapshot,
    resolve_lanpaint_family_adapter,
    lanpaint_family_adapter_registry,
)
from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.krea2_contract import check_krea2_compatibility, resolve_krea2_variant
from neo_app.image.lanpaint_route_contract import ROUTE_FAMILY_ID, SUPPORTED_MODES, normalize_lanpaint_route_contract
from neo_app.image.lanpaint_ui_state import PHASE7_STATE, normalize_lanpaint_ui_state
from neo_app.image.lanpaint_replay import PHASE11_STATE, refresh_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile
from neo_app.image.lanpaint_capability_discovery import build_lanpaint_discovery_contract
from neo_app.image.lanpaint_workflow_abstraction import (
    lanpaint_workflow_abstraction_template,
    stage_role_index,
)

SCHEMA_ID = "neo.image.lanpaint_comfy_compiler_plan.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.providers.comfy_workflows.lanpaint"
COMPILER_ID = "comfy.lanpaint.family_aware.v1"
WORKFLOW_TYPE = "image.inpaint.lanpaint"
COMPILER_STATE = "binding_only"
PHASE5_GRAPH_STATE = "krea2_turbo_gguf_graph_enabled"
PHASE5_VARIANT = "crop_stitch_v1"
PHASE6_LORA_STATE = "krea2_turbo_gguf_lanpaint_model_only_experimental"
PHASE7_UI_STATE = PHASE7_STATE
PHASE8_CAPABILITY_STATE = PHASE8_STATE
PHASE14_STATE = "existing_route_parity_stabilization"
SUPPORTED_PROVIDERS = ("comfyui", "comfyui_portable")

# ComfyProvider exposes only a safe object_info slice to provider compilers. These
# nodes are required for capability/signature validation before a graph compiler
# is allowed to emit a prompt in a later phase.
LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES = (
    "LoadImage",
    "ImageToMask",
    "InvertMask",
    "CLIPLoader",
    "CLIPLoaderGGUF",
    "ClipLoaderGGUF",
    "CLIPTextEncode",
    "ConditioningZeroOut",
    "VAELoader",
    "UNETLoader",
    "DiffusionModelLoader",
    "LoadDiffusionModel",
    "UnetLoaderGGUF",
    "LoaderGGUF",
    "LoraLoader",
    "LoraLoaderModelOnly",
    "ModelSamplingAuraFlow",
    "EmptyLatentImage",
    "KSampler",
    "EmptyFlux2LatentImage",
    "RandomNoise",
    "KSamplerSelect",
    "Ideogram4Scheduler",
    "DualModelGuider",
    "SamplerCustomAdvanced",
    "LanPaint_SamplerCustomAdvanced",
    "CropByMask",
    "ImageResizeKJv2",
    "GrowMaskWithBlur",
    "VAEEncode",
    "SetLatentNoiseMask",
    "DifferentialDiffusionAdvanced",
    "LanPaint_KSampler",
    "VAEDecode",
    "ImageCompositeMasked",
    "PreviewImage",
)

# Phase 22.1 derives family-specific discovery requirements from the active
# adapter registry. The base tuple remains for shared compiler nodes and
# backwards-compatible audit imports; the public scope is the deterministic
# union and cannot silently fall behind newly onboarded families.
LANPAINT_OBJECT_INFO_NODE_CLASSES = tuple(build_lanpaint_discovery_contract(
    lanpaint_family_adapter_registry("comfyui"),
    base_node_classes=LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES,
)["required_node_classes"])

# Minimal signatures only. Optional/custom-node fields may differ across versions,
# so Phase 4 validates the execution-critical input boundary and leaves richer
# widget handling to the concrete graph-emission phase.
_REQUIRED_NODE_INPUTS: dict[str, tuple[str, ...]] = {
    "LoadImage": ("image",),
    "ImageToMask": ("image", "channel"),
    "CLIPLoader": ("clip_name", "type"),
    "CLIPLoaderGGUF": ("clip_name", "type"),
    "ClipLoaderGGUF": ("clip_name", "type"),
    "CLIPTextEncode": ("clip", "text"),
    "ConditioningZeroOut": ("conditioning",),
    "VAELoader": ("vae_name",),
    "UNETLoader": ("unet_name", "weight_dtype"),
    "DiffusionModelLoader": ("model_name",),
    "LoadDiffusionModel": ("diffusion_model_name",),
    "UnetLoaderGGUF": ("unet_name",),
    "LoaderGGUF": ("gguf_name",),
    "LoraLoader": ("model", "clip", "lora_name", "strength_model", "strength_clip"),
    "LoraLoaderModelOnly": ("model", "lora_name", "strength_model"),
    "ModelSamplingAuraFlow": ("model", "shift"),
    "EmptyLatentImage": ("width", "height", "batch_size"),
    "KSampler": ("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "EmptyFlux2LatentImage": ("width", "height", "batch_size"),
    "RandomNoise": ("noise_seed",),
    "KSamplerSelect": ("sampler_name",),
    "Ideogram4Scheduler": ("steps", "width", "height", "mu", "std"),
    "DualModelGuider": ("model", "positive", "model_negative", "negative", "cfg"),
    "SamplerCustomAdvanced": ("noise", "guider", "sampler", "sigmas", "latent_image"),
    "LanPaint_SamplerCustomAdvanced": (
        "noise", "guider", "sampler", "sigmas", "latent_image",
        "LanPaint_NumSteps", "LanPaint_Lambda", "LanPaint_StepSize",
        "LanPaint_Beta", "LanPaint_Friction", "LanPaint_PromptMode",
        "LanPaint_EarlyStop", "LanPaint_Info", "LanPaint_InnerThreshold",
        "LanPaint_InnerPatience",
    ),
    "CropByMask": ("image", "mask", "padding"),
    "ImageResizeKJv2": ("image", "width", "height", "upscale_method"),
    "GrowMaskWithBlur": ("mask", "expand", "blur_radius"),
    "VAEEncode": ("pixels", "vae"),
    "SetLatentNoiseMask": ("samples", "mask"),
    "DifferentialDiffusionAdvanced": ("model", "samples", "mask", "multiplier"),
    "LanPaint_KSampler": (
        "model",
        "positive",
        "negative",
        "latent_image",
        "seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "denoise",
        "LanPaint_NumSteps",
        "LanPaint_PromptMode",
        "Inpainting_mode",
    ),
    "VAEDecode": ("samples", "vae"),
    "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"),
    "PreviewImage": ("images",),
}


# Phase 5 emits the submitted graph and therefore validates the richer runtime
# signatures that are actually serialized. Keeping this separate preserves the
# historical Phase 4 binding-only contract while failing closed for stale custom
# node versions at the runnable-graph boundary.
_PHASE5_GRAPH_REQUIRED_NODE_INPUTS: dict[str, tuple[str, ...]] = {
    "LoadImage": ("image",),
    "ImageToMask": ("image", "channel"),
    "CLIPLoader": ("clip_name", "type", "device"),
    "CLIPTextEncode": ("clip", "text"),
    "ConditioningZeroOut": ("conditioning",),
    "VAELoader": ("vae_name",),
    "CropByMask": ("image", "mask", "padding"),
    "ImageResizeKJv2": (
        "image", "width", "height", "upscale_method", "keep_proportion",
        "pad_color", "crop_position", "divisible_by",
    ),
    "GrowMaskWithBlur": (
        "mask", "expand", "incremental_expandrate", "tapered_corners",
        "flip_input", "blur_radius", "lerp_alpha", "decay_factor",
    ),
    "VAEEncode": ("pixels", "vae"),
    "SetLatentNoiseMask": ("samples", "mask"),
    "DifferentialDiffusionAdvanced": ("model", "samples", "mask", "multiplier"),
    "LanPaint_KSampler": (
        "model", "positive", "negative", "latent_image", "seed", "steps",
        "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps",
        "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode",
    ),
    "VAEDecode": ("samples", "vae"),
    "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"),
    "PreviewImage": ("images",),
}

# Provider-owned stage bindings. Family-owned model/conditioning/VAE bindings are
# resolved separately from the Phase 3 family policy.
_BASE_STAGE_BINDING_TEMPLATES: dict[str, dict[str, Any]] = {
    "source_image": {
        "binding_kind": "provider_asset_adapter",
        "node_chain": ["LoadImage"],
        "output_port": "IMAGE",
    },
    "mask_image": {
        "binding_kind": "provider_mask_adapter",
        "node_chain": ["LoadImage", "ImageToMask"],
        "output_port": "MASK",
        "notes": ["Neo supplies a separate mask image; the provider adapter converts its red channel to MASK."],
    },
    "crop_context": {
        "binding_kind": "base_graph_node",
        "node_chain": ["CropByMask"],
    },
    "processing_resize": {
        "binding_kind": "base_graph_node",
        "node_chain": ["ImageResizeKJv2"],
    },
    "sampling_mask_refine": {
        "binding_kind": "base_graph_node",
        "node_chain": ["GrowMaskWithBlur"],
    },
    "latent_encode": {
        "binding_kind": "base_graph_node",
        "node_chain": ["VAEEncode"],
    },
    "latent_noise_mask": {
        "binding_kind": "base_graph_node",
        "node_chain": ["SetLatentNoiseMask"],
    },
    "family_model_transform": {
        "binding_kind": "family_transform_pipeline",
        "node_chain": [],
        "bypass_allowed": True,
    },
    "lanpaint_sample": {
        "binding_kind": "base_graph_node",
        "node_chain": ["LanPaint_KSampler"],
    },
    "latent_decode": {
        "binding_kind": "base_graph_node",
        "node_chain": ["VAEDecode"],
    },
    "restore_crop_size": {
        "binding_kind": "base_graph_node",
        "node_chain": ["ImageResizeKJv2"],
        "notes": ["The concrete graph must restore the decoded patch to CropByMask geometry."],
    },
    "stitch_mask_refine": {
        "binding_kind": "base_graph_node",
        "node_chain": ["GrowMaskWithBlur"],
        "notes": ["The concrete graph must return the stitch mask to source-crop geometry before compositing."],
    },
    "stitch_composite": {
        "binding_kind": "base_graph_node",
        "node_chain": ["ImageCompositeMasked"],
    },
    "output_handoff": {
        "binding_kind": "provider_output_adapter",
        "node_chain": ["PreviewImage"],
        "notes": ["PreviewImage is a provider handoff sink, not a family custom-node dependency."],
    },
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _fingerprint_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    payload.pop("plan_fingerprint", None)
    payload.pop("validation", None)
    return payload


def lanpaint_comfy_compile_plan_fingerprint(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(_fingerprint_payload(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _input_names(payload: Mapping[str, Any] | None) -> set[str]:
    values = _mapping(payload)
    names: set[str] = set()
    for key in ("required", "optional", "all"):
        raw = values.get(key)
        if isinstance(raw, (list, tuple, set)):
            names.update(str(item) for item in raw)
    return names


def _loader_role(
    backend_capabilities: Mapping[str, Any],
    loader_id: str,
    role_id: str,
) -> dict[str, Any]:
    loaders = _mapping(backend_capabilities.get("loaders"))
    loader = _mapping(loaders.get(loader_id))
    roles = _mapping(loader.get("roles"))
    return _mapping(roles.get(role_id))


def _available_node_names(backend_capabilities: Mapping[str, Any]) -> set[str]:
    node_map = _mapping(backend_capabilities.get("object_info_node_inputs"))
    names = set(str(name) for name in node_map)
    for loader in _mapping(backend_capabilities.get("loaders")).values():
        loader_map = _mapping(loader)
        for role in _mapping(loader_map.get("roles")).values():
            role_map = _mapping(role)
            if role_map.get("available") and role_map.get("backend_node"):
                names.add(str(role_map["backend_node"]))
    return names


def _select_role_node(
    backend_capabilities: Mapping[str, Any],
    *,
    loader_id: str,
    role_id: str,
    accepted: Iterable[str],
    preferred: str | None = None,
    require_role_available: bool = False,
) -> tuple[str | None, str, list[str]]:
    accepted_nodes = [str(item) for item in accepted if str(item)]
    role = _loader_role(backend_capabilities, loader_id, role_id)
    available_names = _available_node_names(backend_capabilities)
    diagnostics: list[str] = []

    if role:
        backend_node = str(role.get("backend_node") or "").strip()
        if role.get("available") and backend_node in accepted_nodes:
            return backend_node, "live_loader_role", diagnostics
        if require_role_available and not role.get("available"):
            diagnostics.extend(str(item) for item in role.get("notes") or [])
            diagnostics.append(f"Backend role {loader_id}.{role_id} is unavailable.")
            return None, "missing_required_loader_role", diagnostics

    fallback_candidates = list(dict.fromkeys(candidate for candidate in [preferred, *accepted_nodes] if candidate and candidate in available_names))
    if len(fallback_candidates) > 1:
        diagnostics.append(
            f"Multiple supported nodes are installed for {loader_id}.{role_id}: {', '.join(fallback_candidates)}. Live loader-role discovery must select one."
        )
        return None, "ambiguous_node", diagnostics
    if fallback_candidates:
        candidate = fallback_candidates[0]
        if require_role_available:
            diagnostics.append(
                f"{candidate} exists, but the architecture-specific role {loader_id}.{role_id} was not proven available."
            )
            return None, "unproven_architecture_role", diagnostics
        return candidate, "live_node_fallback", diagnostics

    return None, "missing_node", diagnostics


def _resolve_external_bindings(
    adapter: Mapping[str, Any],
    backend_capabilities: Mapping[str, Any],
    *,
    loader_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    loaders = _mapping(adapter.get("loaders"))
    model_policy = _mapping(loaders.get("model"))
    text_policy = _mapping(loaders.get("text_encoder"))
    vae_policy = _mapping(loaders.get("vae"))
    slots = _mapping(_mapping(adapter.get("assets")).get("slots"))

    model_role_id = str(model_policy.get("role_id") or ("gguf_unet" if loader_id == "gguf" else "diffusion_model"))
    accepted_loader_nodes = list(model_policy.get("accepted_node_classes") or [])
    preferred_loader = str(model_policy.get("preferred_node_class") or "") or None
    model_loader, model_source, model_notes = _select_role_node(
        backend_capabilities,
        loader_id=loader_id,
        role_id=model_role_id,
        accepted=accepted_loader_nodes,
        preferred=preferred_loader,
    )
    if not model_loader:
        ambiguous = model_source == "ambiguous_node"
        blockers.append({
            "code": "ambiguous_model_loader" if ambiguous else "missing_model_loader",
            "field": f"loaders.{loader_id}.roles.{model_role_id}",
            "message": (
                f"Multiple supported {loader_id} diffusion-model loaders were discovered without an authoritative live role selection."
                if ambiguous else f"No supported {loader_id} diffusion-model loader was discovered for this LanPaint route."
            ),
            "accepted_node_classes": accepted_loader_nodes,
            "notes": model_notes,
        })

    text_role_id = str(text_policy.get("role_id") or "text_encoder_primary")
    accepted_text_nodes = list(text_policy.get("accepted_node_classes") or ["CLIPLoader"])
    preferred_text_node = str(text_policy.get("preferred_node_class") or accepted_text_nodes[0])
    text_node, text_source, text_notes = _select_role_node(
        backend_capabilities,
        loader_id=loader_id,
        role_id=text_role_id,
        accepted=accepted_text_nodes,
        preferred=preferred_text_node,
        require_role_available=True,
    )
    if not text_node:
        blockers.append({
            "code": "missing_family_text_encoder_loader",
            "field": f"loaders.{loader_id}.roles.{text_role_id}",
            "message": f"The selected backend did not prove a compatible {text_policy.get('clip_type') or 'family'} text-encoder loader.",
            "required_clip_type": text_policy.get("clip_type"),
            "notes": text_notes,
        })

    vae_role_id = str(vae_policy.get("role_id") or "vae_or_ae")
    vae_nodes = list(vae_policy.get("accepted_node_classes") or ["VAELoader"])
    vae_node, vae_source, vae_notes = _select_role_node(
        backend_capabilities,
        loader_id=loader_id,
        role_id=vae_role_id,
        accepted=vae_nodes,
        preferred=vae_nodes[0],
    )
    if not vae_node:
        blockers.append({
            "code": "missing_family_vae_loader",
            "field": f"loaders.{loader_id}.roles.{vae_role_id}",
            "message": "The selected backend did not expose the family VAE loader role.",
            "notes": vae_notes,
        })

    conditioning = _mapping(adapter.get("conditioning"))
    positive = _mapping(conditioning.get("positive"))
    negative = _mapping(conditioning.get("negative"))
    return {
        "family_model": {
            "node_class": model_loader,
            "resolution_source": model_source,
            "loader_id": loader_id,
            "loader_role_id": model_role_id,
            "asset_role_id": str(_mapping(slots.get("model")).get("role_id") or model_role_id),
            "output_type": model_policy.get("output_type") or "MODEL",
        },
        "text_encoder": {
            "node_class": text_node,
            "resolution_source": text_source,
            "loader_role_id": text_role_id,
            "required_clip_type": text_policy.get("clip_type"),
            "asset_role_id": str(_mapping(slots.get("text_encoder")).get("role_id") or text_role_id),
            "output_type": text_policy.get("output_type") or "CLIP",
        },
        "vae": {
            "node_class": vae_node,
            "resolution_source": vae_source,
            "loader_role_id": vae_role_id,
            "asset_role_id": str(_mapping(slots.get("vae")).get("role_id") or vae_role_id),
            "output_type": vae_policy.get("output_type") or "VAE",
        },
        "positive_conditioning": {
            "node_class": positive.get("node_class"),
            "policy": positive.get("positive_conditioning_policy"),
            "source": positive.get("source"),
            "output_type": "CONDITIONING",
        },
        "negative_conditioning": {
            "node_class": negative.get("node_class"),
            "policy": negative.get("negative_conditioning_policy"),
            "source": negative.get("source"),
            "output_type": "CONDITIONING",
        },
    }, blockers


def _resolve_transform_bindings(
    adapter: Mapping[str, Any],
    *,
    lora_stack_enabled: bool,
) -> list[dict[str, Any]]:
    transforms: list[dict[str, Any]] = []
    lora = _mapping(adapter.get("lora"))
    pipeline = _mapping(adapter.get("model_transforms"))
    for item in pipeline.get("ordered_transforms") or []:
        transform = _mapping(item)
        transform_id = str(transform.get("transform_id") or "")
        if transform_id == "neo_lora_stack":
            enabled = bool(lora_stack_enabled)
            transforms.append({
                "transform_id": transform_id,
                "enabled": enabled,
                "conditional": True,
                "node_class": lora.get("loader_node_class") if enabled else None,
                "repeat_for_each_enabled_row": enabled and bool(lora.get("allow_multiple")),
                "strategy": lora.get("injection_strategy"),
                "compatibility_key": lora.get("compatibility_key"),
                "compatibility_engine_independent": True,
                "bypass_when_disabled": True,
            })
        elif transform_id == "differential_diffusion_advanced":
            transforms.append({
                "transform_id": transform_id,
                "enabled": True,
                "conditional": False,
                "node_class": "DifferentialDiffusionAdvanced",
                "context_inputs": list(transform.get("context_inputs") or []),
                "bypass_when_disabled": False,
            })
        elif transform_id == "model_sampling_aura_flow":
            transforms.append({
                "transform_id": transform_id,
                "enabled": True,
                "conditional": False,
                "node_class": "ModelSamplingAuraFlow",
                "inputs": deepcopy(_mapping(transform.get("inputs"))),
                "bypass_when_disabled": False,
            })
        elif transform_id == "model_sampling_sd3":
            transforms.append({
                "transform_id": transform_id,
                "enabled": True,
                "conditional": False,
                "node_class": "ModelSamplingSD3",
                "inputs": deepcopy(_mapping(transform.get("inputs"))),
                "bypass_when_disabled": False,
            })
        else:
            transforms.append({
                "transform_id": transform_id,
                "enabled": bool(transform.get("required")),
                "conditional": not bool(transform.get("required")),
                "node_class": None,
                "resolution_state": "unresolved_family_transform",
            })
    return transforms


def _resolve_stage_bindings(
    *,
    provider_id: str,
    family_id: str,
    loader_id: str,
    transform_bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stages = stage_role_index(lanpaint_workflow_abstraction_template())
    resolved: list[dict[str, Any]] = []
    for stage_id in lanpaint_workflow_abstraction_template()["stage_order"]:
        stage = stages[stage_id]
        binding = deepcopy(_BASE_STAGE_BINDING_TEMPLATES[stage_id])
        if stage_id == "family_model_transform":
            binding["transforms"] = deepcopy(transform_bindings)
            binding["node_chain"] = [
                str(item.get("node_class"))
                for item in transform_bindings
                if item.get("enabled") and item.get("node_class")
            ]
        resolved.append({
            "stage_id": stage_id,
            "role_id": stage.get("role_id"),
            "ordinal": stage.get("ordinal"),
            "required": bool(stage.get("required")),
            "binding": {
                "state": "bound_template",
                "provider_id": provider_id,
                "family_id": family_id,
                "loader_id": loader_id,
                "compiler_id": COMPILER_ID,
                **binding,
            },
        })
    return resolved


def _required_nodes_from_plan(
    stage_bindings: Iterable[Mapping[str, Any]],
    external_bindings: Mapping[str, Any],
) -> list[str]:
    nodes: list[str] = []
    for stage in stage_bindings:
        binding = _mapping(stage.get("binding"))
        for node in binding.get("node_chain") or []:
            if node:
                nodes.append(str(node))
    for binding in external_bindings.values():
        node = _mapping(binding).get("node_class")
        if node:
            nodes.append(str(node))
    return list(dict.fromkeys(nodes))


def _evaluate_node_capabilities(
    required_nodes: Iterable[str],
    backend_capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    object_info = _mapping(backend_capabilities.get("object_info_node_inputs"))
    available_names = _available_node_names(backend_capabilities)
    missing: list[str] = []
    signature_mismatches: list[dict[str, Any]] = []
    verified: list[str] = []
    role_only: list[str] = []

    for node in required_nodes:
        if node not in available_names:
            missing.append(node)
            continue
        node_inputs = _mapping(object_info.get(node))
        if node_inputs:
            required_inputs = set(_REQUIRED_NODE_INPUTS.get(node, ()))
            live_inputs = _input_names(node_inputs)
            missing_inputs = sorted(required_inputs - live_inputs)
            if missing_inputs:
                signature_mismatches.append({
                    "node_class": node,
                    "missing_inputs": missing_inputs,
                    "declared_inputs": sorted(live_inputs),
                })
            else:
                verified.append(node)
        else:
            # Loader roles can prove node existence but not the detailed signature.
            role_only.append(node)

    blockers: list[dict[str, Any]] = []
    if missing:
        blockers.append({
            "code": "missing_required_nodes",
            "message": "The selected Comfy profile is missing required LanPaint compiler nodes.",
            "nodes": sorted(missing),
        })
    if signature_mismatches:
        blockers.append({
            "code": "incompatible_node_signatures",
            "message": "One or more installed nodes do not expose the inputs required by the LanPaint compiler contract.",
            "nodes": deepcopy(signature_mismatches),
        })

    return {
        "available_node_classes": sorted(available_names),
        "required_node_classes": sorted(set(str(item) for item in required_nodes)),
        "verified_node_classes": sorted(verified),
        "role_only_node_classes": sorted(role_only),
        "missing_node_classes": sorted(missing),
        "signature_mismatches": signature_mismatches,
        "blockers": blockers,
    }


_CUSTOM_NODE_PACK_BY_CLASS = {
    "LanPaint_KSampler": "LanPaint",
    "CropByMask": "comfyui-inpainteasy",
    "ImageResizeKJv2": "ComfyUI-KJNodes",
    "GrowMaskWithBlur": "ComfyUI-KJNodes",
    "UnetLoaderGGUF": "ComfyUI-GGUF",
    "LoaderGGUF": "ComfyUI-GGUF",
}


def _missing_custom_node_packs(missing_nodes: Iterable[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for node in missing_nodes:
        pack = _CUSTOM_NODE_PACK_BY_CLASS.get(str(node))
        if pack:
            grouped.setdefault(pack, []).append(str(node))
    return [
        {"pack_id": pack, "missing_node_classes": sorted(nodes)}
        for pack, nodes in sorted(grouped.items())
    ]


def validate_lanpaint_comfy_compile_plan(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    payload = _mapping(plan)
    issues: list[dict[str, Any]] = []
    if payload.get("schema_id") != SCHEMA_ID:
        issues.append({"level": "error", "field": "schema_id", "message": f"Expected {SCHEMA_ID}."})
    if payload.get("authority") != AUTHORITY:
        issues.append({"level": "error", "field": "authority", "message": f"Expected {AUTHORITY}."})
    if payload.get("route_family_id") != ROUTE_FAMILY_ID:
        issues.append({"level": "error", "field": "route_family_id", "message": f"Expected {ROUTE_FAMILY_ID}."})

    identity = _mapping(payload.get("identity"))
    compiler = _mapping(payload.get("compiler"))
    expected_workflow_type = f"image.{identity.get('mode') or 'inpaint'}.lanpaint"
    if compiler.get("compiler_id") != COMPILER_ID or compiler.get("workflow_type") != expected_workflow_type:
        issues.append({"level": "error", "field": "compiler", "message": "Compiler identity does not match the LanPaint masked-edit boundary."})
    if compiler.get("state") != COMPILER_STATE:
        issues.append({"level": "error", "field": "compiler.state", "message": f"Expected {COMPILER_STATE}."})
    if compiler.get("graph_emitted") is not False or compiler.get("backend_prompt") is not None:
        issues.append({"level": "error", "field": "compiler", "message": "Phase 4 must not emit a runnable Comfy graph."})

    execution = _mapping(payload.get("execution"))
    if execution.get("enabled") is not False or execution.get("selectable") is not False:
        issues.append({"level": "error", "field": "execution", "message": "Phase 4 compiler plans must remain non-selectable and execution-disabled."})

    if identity.get("provider_id") not in SUPPORTED_PROVIDERS:
        issues.append({"level": "error", "field": "identity.provider_id", "message": "Only local Comfy providers may bind this compiler."})
    if identity.get("mode") not in SUPPORTED_MODES or identity.get("engine") != "lanpaint":
        issues.append({"level": "error", "field": "identity", "message": "The compiler boundary accepts LanPaint inpaint/outpaint routes only."})

    stage_bindings = payload.get("stage_bindings") or []
    expected_order = lanpaint_workflow_abstraction_template()["stage_order"]
    actual_order = [str(_mapping(item).get("stage_id") or "") for item in stage_bindings]
    if payload.get("family_policy_state") == COMPLETE_POLICY_STATE and actual_order != expected_order:
        issues.append({"level": "error", "field": "stage_bindings", "message": "Stage binding order diverges from the Phase 2 abstraction."})
    if payload.get("family_policy_state") != COMPLETE_POLICY_STATE and stage_bindings:
        issues.append({"level": "error", "field": "stage_bindings", "message": "Incomplete family policies must not receive concrete Comfy stage bindings."})

    if payload.get("family_policy_state") == COMPLETE_POLICY_STATE:
        if not _mapping(payload.get("external_bindings")).get("family_model"):
            issues.append({"level": "error", "field": "external_bindings", "message": "Complete family policies require external model/conditioning/VAE bindings."})
    return issues


def build_lanpaint_comfy_compile_plan(
    route_contract: Mapping[str, Any] | None,
    backend_capabilities: Mapping[str, Any] | None = None,
    *,
    lora_stack_enabled: bool = False,
) -> dict[str, Any]:
    """Bind a resolved LanPaint family route to Comfy node roles without graph emission.

    Phase 4 owns provider/family/loader binding and live capability diagnostics.
    It deliberately stops before node-id allocation, prompt construction, provider
    dispatch, UI route exposure, or queue execution.
    """

    capabilities = _mapping(backend_capabilities)
    adapter = resolve_lanpaint_family_adapter(route_contract)
    identity = deepcopy(_mapping(adapter.get("identity")))
    provider_id = str(identity.get("provider_id") or "")
    family_id = str(identity.get("family") or "")
    loader_id = str(identity.get("loader") or "")
    policy_meta = _mapping(adapter.get("policy"))
    policy_state = str(policy_meta.get("policy_status") or policy_meta.get("resolution_state") or "missing_policy")

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if provider_id not in SUPPORTED_PROVIDERS:
        blockers.append({
            "code": "unsupported_provider",
            "message": "LanPaint compiler binding currently supports ComfyUI and ComfyUI Portable only.",
            "provider_id": provider_id,
        })
    if policy_meta.get("resolution_state") != "resolved_policy_only" or policy_state != COMPLETE_POLICY_STATE:
        blockers.append({
            "code": "family_policy_not_complete",
            "message": "A dedicated complete LanPaint family policy is required before Comfy node binding.",
            "family": family_id,
            "resolution_state": policy_meta.get("resolution_state"),
        })

    reachable = bool(capabilities.get("reachable"))
    object_info_available = bool(capabilities.get("object_info_available"))
    if not reachable:
        blockers.append({
            "code": "backend_unreachable",
            "message": "The selected Comfy profile must be reachable before LanPaint capability binding can be validated.",
        })
    if not object_info_available:
        blockers.append({
            "code": "object_info_required",
            "message": "Live Comfy /object_info discovery is required before LanPaint graph compilation.",
        })

    external_bindings: dict[str, Any] = {}
    stage_bindings: list[dict[str, Any]] = []
    transform_bindings: list[dict[str, Any]] = []
    capability_evaluation: dict[str, Any] = {
        "available_node_classes": [],
        "required_node_classes": [],
        "verified_node_classes": [],
        "role_only_node_classes": [],
        "missing_node_classes": [],
        "signature_mismatches": [],
        "blockers": [],
    }

    if policy_state == COMPLETE_POLICY_STATE:
        external_bindings, external_blockers = _resolve_external_bindings(
            adapter,
            capabilities,
            loader_id=loader_id,
        )
        blockers.extend(external_blockers)
        transform_bindings = _resolve_transform_bindings(adapter, lora_stack_enabled=lora_stack_enabled)
        stage_bindings = _resolve_stage_bindings(
            provider_id=provider_id,
            family_id=family_id,
            loader_id=loader_id,
            transform_bindings=transform_bindings,
        )
        required_nodes = _required_nodes_from_plan(stage_bindings, external_bindings)
        capability_evaluation = _evaluate_node_capabilities(required_nodes, capabilities)
        blockers.extend(capability_evaluation["blockers"])

    # Keep duplicate diagnostics deterministic while preserving first occurrence.
    deduped_blockers: list[dict[str, Any]] = []
    seen_blockers: set[str] = set()
    for blocker in blockers:
        key = json.dumps(blocker, sort_keys=True, ensure_ascii=False)
        if key not in seen_blockers:
            seen_blockers.add(key)
            deduped_blockers.append(blocker)

    binding_complete = bool(
        policy_state == COMPLETE_POLICY_STATE
        and stage_bindings
        and external_bindings
        and not any(item.get("code") in {"missing_model_loader", "ambiguous_model_loader", "missing_family_text_encoder_loader", "missing_family_vae_loader"} for item in deduped_blockers)
    )
    capability_compatible = bool(reachable and object_info_available and not capability_evaluation["blockers"] and not deduped_blockers)

    plan: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "route_family_id": ROUTE_FAMILY_ID,
        "identity": identity,
        "route_key": identity.get("route_key"),
        "source_contract_fingerprint": _mapping(adapter.get("policy")).get("policy_fingerprint"),
        "workflow_abstraction_fingerprint": lanpaint_workflow_abstraction_template().get("abstraction_fingerprint"),
        "family_policy_state": policy_state,
        "family_policy": {
            "policy_id": policy_meta.get("policy_id"),
            "policy_fingerprint": policy_meta.get("policy_fingerprint"),
            "resolution_state": policy_meta.get("resolution_state"),
        },
        "family_adapter": {
            "schema_id": adapter.get("schema_id"),
            "adapter_id": identity.get("adapter_id"),
            "adapter_fingerprint": adapter.get("adapter_fingerprint"),
            "binding_state": _mapping(adapter.get("binding")).get("state"),
            "graph_profile": _mapping(adapter.get("binding")).get("graph_profile"),
        },
        "compiler": {
            "compiler_id": COMPILER_ID,
            "workflow_type": f"image.{identity.get('mode') or 'inpaint'}.lanpaint",
            "state": COMPILER_STATE,
            "graph_emitted": False,
            "backend_prompt": None,
            "node_ids_allocated": False,
            "provider_dispatch_registered": False,
            "ui_route_registered": False,
        },
        "external_bindings": external_bindings,
        "stage_bindings": stage_bindings,
        "transform_bindings": transform_bindings,
        "capability_evaluation": {
            "provider_id": provider_id,
            "reachable": reachable,
            "object_info_available": object_info_available,
            **capability_evaluation,
            "binding_complete": binding_complete,
            "capability_compatible": capability_compatible,
        },
        "diagnostics": {
            "blockers": deduped_blockers,
            "warnings": warnings,
            "missing_custom_node_packs": _missing_custom_node_packs(capability_evaluation["missing_node_classes"]),
            "next_action": "Complete the first family graph emitter in Phase 5 after all blockers are cleared.",
        },
        "execution": {
            "enabled": False,
            "selectable": False,
            "execution_ready": False,
            "state": COMPILER_STATE,
            "compiler_id": COMPILER_ID,
            "workflow_type": f"image.{identity.get('mode') or 'inpaint'}.lanpaint",
            "reason": "Phase 4 binds the provider/compiler boundary but does not emit or dispatch a runnable Comfy graph.",
        },
    }
    validation_issues = validate_lanpaint_comfy_compile_plan(plan)
    plan["validation"] = {
        "ok": not any(item.get("level") == "error" for item in validation_issues),
        "issues": validation_issues,
        "binding_complete": binding_complete,
        "capability_compatible": capability_compatible,
        "graph_compile_ready_for_next_phase": bool(binding_complete and capability_compatible and not deduped_blockers),
        "execution_ready": False,
    }
    plan["plan_fingerprint"] = lanpaint_comfy_compile_plan_fingerprint(plan)
    return plan


def _param(params: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _image_name_value(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]


def _source_image_name(params: Mapping[str, Any]) -> str:
    for key in ("comfy_source_image_name", "source_image_name", "source_image", "source_image_path", "source_image_url", "init_image", "image"):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _mask_image_name(params: Mapping[str, Any]) -> str:
    for key in ("comfy_mask_image_name", "mask_image_name", "mask_image", "mask_image_path", "inpaint_mask", "mask"):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _active_lora_rows(extensions: Any, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return executable LoRA rows, independent from the LanPaint engine.

    The current Image submit-state snapshot is authoritative when present. This
    prevents saved rows, an old workflow binding, or replay metadata from making
    LoRA a hard dependency of plain LanPaint. API callers without a UI snapshot
    keep the legacy explicit payload behavior.
    """
    submit_state = params.get("_neo_extension_state") if isinstance(params, Mapping) else None
    submit_extensions = submit_state.get("extensions") if isinstance(submit_state, Mapping) and isinstance(submit_state.get("extensions"), Mapping) else None
    lora_submit = submit_extensions.get("lora_stack") if isinstance(submit_extensions, Mapping) else None
    if isinstance(lora_submit, Mapping):
        if lora_submit.get("enabled") is not True or lora_submit.get("workflow_applied") is not True:
            return []
        # Current UI state is authoritative and must explicitly opt in. Legacy
        # snapshots without this v2 field are configuration-only.
        if lora_submit.get("execution_requested") is not True:
            return []

    payload = extensions if isinstance(extensions, Mapping) else {}
    candidates = [payload.get("lora_stack")]
    for container_key in ("extensions", "payloads"):
        container = payload.get(container_key)
        if isinstance(container, Mapping):
            candidates.append(container.get("lora_stack"))
    for block in candidates:
        if not isinstance(block, Mapping) or not bool(block.get("enabled")):
            continue
        params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
        rows = params.get("loras") if isinstance(params.get("loras"), list) else []
        active = [dict(row) for row in rows if isinstance(row, Mapping) and bool(row.get("enabled", True)) and str(row.get("name") or row.get("lora_name") or "").strip()]
        if active:
            return active
    return []


def _base_graph_lora_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return only active rows that Phase 6 may inject into the base model path.

    Regional rows remain saved for later region-owned passes, and finish-only
    rows remain saved for the finishing pipeline. They must not make the base
    LanPaint graph require ``LoraLoaderModelOnly`` when no base-pass row exists.
    """

    return [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("apply_to") or "global") == "global"
        and str(row.get("target") or "both") in {"both", "base"}
    ]


def _node_inputs(backend_capabilities: Mapping[str, Any], node_class: str) -> set[str]:
    info = _mapping(_mapping(backend_capabilities.get("object_info_node_inputs")).get(node_class))
    return _input_names(info)


def _optional_input(
    inputs: dict[str, Any],
    backend_capabilities: Mapping[str, Any],
    node_class: str,
    input_name: str,
    value: Any,
) -> None:
    if input_name in _node_inputs(backend_capabilities, node_class):
        inputs[input_name] = value


def _role_asset_choices(backend_capabilities: Mapping[str, Any], loader_id: str, role_id: str) -> list[str]:
    role = _loader_role(backend_capabilities, loader_id, role_id)
    assets = _mapping(role.get("assets"))
    choices: list[str] = []
    for values in assets.values():
        if isinstance(values, (list, tuple, set)):
            choices.extend(str(item) for item in values if str(item))
    return list(dict.fromkeys(choices))


def _normalize_asset_name(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _verify_selected_asset(
    validation: ProviderValidationResult,
    backend_capabilities: Mapping[str, Any],
    *,
    loader_id: str,
    role_id: str,
    label: str,
    selected: str,
) -> dict[str, Any]:
    choices = _role_asset_choices(backend_capabilities, loader_id, role_id)
    selected_key = _normalize_asset_name(selected)
    choice_keys = {_normalize_asset_name(item) for item in choices}
    if choices and selected_key not in choice_keys:
        validation.errors.append(f"{label} is not advertised by the selected Comfy profile: {selected}")
        validation.ok = False
        state = "blocked_missing_model"
    elif choices:
        state = "verified_live_catalog"
    else:
        validation.warnings.append(f"{label} could not be verified against live object_info choices; Comfy will perform final prompt validation.")
        state = "unverified_live_catalog"
    return {
        "role_id": role_id,
        "selected": selected,
        "state": state,
        "catalog_count": len(choices),
    }


def _validate_phase5_graph_signatures(
    validation: ProviderValidationResult,
    backend_capabilities: Mapping[str, Any],
    *,
    require_invert_mask: bool = False,
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    required = dict(_PHASE5_GRAPH_REQUIRED_NODE_INPUTS)
    if require_invert_mask:
        required["InvertMask"] = ("mask",)
    node_map = _mapping(backend_capabilities.get("object_info_node_inputs"))
    for node_class, expected_inputs in required.items():
        if node_class not in node_map:
            mismatches.append({
                "node_class": node_class,
                "state": "missing_node",
                "missing_inputs": list(expected_inputs),
            })
            continue
        declared = _node_inputs(backend_capabilities, node_class)
        missing_inputs = [name for name in expected_inputs if name not in declared]
        if missing_inputs:
            mismatches.append({
                "node_class": node_class,
                "state": "incompatible_signature",
                "missing_inputs": missing_inputs,
                "declared_inputs": sorted(declared),
            })
    if mismatches:
        validation.ok = False
        for item in mismatches:
            if item["state"] == "missing_node":
                message = f"LanPaint Phase 5 requires Comfy node {item['node_class']}, but it is not installed/exposed."
            else:
                message = (
                    f"LanPaint Phase 5 requires a newer compatible {item['node_class']} signature; "
                    f"missing inputs: {', '.join(item['missing_inputs'])}."
                )
            if message not in validation.errors:
                validation.errors.append(message)
    return mismatches


def _lanpaint_route_request(provider_id: str, params: Mapping[str, Any], *, loader_id: str = "gguf", mode: str = "inpaint") -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": provider_id,
            "family": "krea2_turbo",
            "loader": loader_id,
            "mode": str(mode or "inpaint"),
            "engine": "lanpaint",
            "variant": PHASE5_VARIANT,
        },
        "crop_policy": {
            "padding_px": _param(params, "lanpaint_crop_padding", "crop_padding"),
            "processing_size": {
                "width": _param(params, "lanpaint_processing_width"),
                "height": _param(params, "lanpaint_processing_height"),
            },
            "resize_method": _param(params, "lanpaint_resize_method"),
        },
        "mask_policy": {
            "sampling": {
                "expand_px": _param(params, "lanpaint_sampling_mask_expand"),
                "blur_radius": _param(params, "lanpaint_sampling_mask_blur"),
            },
            "stitch": {
                "expand_px": _param(params, "lanpaint_stitch_mask_expand"),
                "blur_radius": _param(params, "lanpaint_stitch_mask_blur"),
            },
        },
        "sampler_policy": {
            "steps": _param(params, "lanpaint_steps"),
            "cfg": _param(params, "lanpaint_cfg"),
            "sampler_name": _param(params, "lanpaint_sampler"),
            "scheduler": _param(params, "lanpaint_scheduler"),
            "denoise": _param(params, "lanpaint_denoise"),
            "lanpaint_thinking_steps": _param(params, "lanpaint_thinking_steps"),
            "prompt_mode": _param(params, "lanpaint_prompt_mode"),
        },
        "stitch_policy": {
            "resize_method": _param(params, "lanpaint_stitch_resize_method"),
        },
    }


def _phase5_blocker_messages(plan: Mapping[str, Any]) -> list[str]:
    diagnostics = _mapping(plan.get("diagnostics"))
    messages: list[str] = []
    for blocker in diagnostics.get("blockers") or []:
        item = _mapping(blocker)
        message = str(item.get("message") or item.get("code") or "LanPaint capability validation failed.")
        nodes = item.get("nodes")
        if isinstance(nodes, list) and nodes:
            if all(isinstance(node, str) for node in nodes):
                message += f" Missing/incompatible nodes: {', '.join(str(node) for node in nodes)}."
        notes = item.get("notes")
        if isinstance(notes, list) and notes:
            message += " " + " ".join(str(note) for note in notes)
        messages.append(message)
    return list(dict.fromkeys(messages))


def compile_lanpaint_krea2_turbo_gguf_inpaint(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Emit the parity-stabilized Krea 2 Turbo LanPaint graph.

    The legacy function name is retained for compatibility, but Phase 14 binds
    both GGUF and safetensors/component loaders to the same Krea-owned
    DifferentialDiffusionAdvanced crop/stitch topology. Loader selection is
    exact and capability-gated; the Qwen3-VL encoder remains native.
    """

    params = dict(job.params or {})
    runtime_mode = str(route.mode or job.mode or "inpaint").strip().lower()
    loader_id = str(route.loader or job.loader or "gguf").strip()
    if loader_id not in {"gguf", "diffusion_model"}:
        validation.errors.append(f"Krea 2 Turbo LanPaint does not support loader {loader_id!r}.")
        validation.ok = False
    for replay_error in validate_lanpaint_replay_request(
        params, provider_id=provider_id, family="krea2_turbo", loader=loader_id, mode=runtime_mode, engine="lanpaint"
    ):
        validation.errors.append(replay_error)
        validation.ok = False

    ui_state = normalize_lanpaint_ui_state(
        params,
        provider_id=provider_id,
        family=route.family or job.family or "krea2_turbo",
        loader=loader_id,
        mode=route.mode or job.mode or "inpaint",
        engine=params.get("inpaint_engine") or "lanpaint",
    )
    # The nested Phase 7 state is the replay/ownership contract. Resolved flat
    # fields remain the compatibility bridge for the existing Phase 5/6 graph
    # compiler and are never sourced from unsupported routes.
    params.update({
        key: value
        for key, value in dict(ui_state.get("flat_params") or {}).items()
        if value not in (None, "")
    })
    backend = dict(backend_capabilities or {})
    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if not source_name:
        validation.errors.append("LanPaint inpaint requires Image 1 / a Comfy source image name after provider handoff.")
        validation.ok = False
    if not mask_name:
        validation.errors.append("LanPaint inpaint requires a Comfy mask image name after provider handoff.")
        validation.ok = False

    active_loras = _active_lora_rows(job.extensions, params)
    base_graph_loras = _base_graph_lora_rows(active_loras)

    model_name = str(
        job.model
        or (
            _param(params, "gguf_model", "gguf_unet", "model", "model_name", default="")
            if loader_id == "gguf"
            else _param(params, "diffusion_model", "unet", "model", "model_name", default="")
        )
    )
    if not model_name:
        loader_label = "GGUF" if loader_id == "gguf" else "safetensors/component"
        validation.errors.append(f"Krea 2 Turbo LanPaint requires an explicitly selected {loader_label} diffusion transformer.")
        validation.ok = False
    text_encoder = str(require_explicit_asset_selection(
        validation,
        "Krea 2 Qwen3-VL-4B text encoder",
        params.get("qwen3vl_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    ))
    vae = str(require_explicit_asset_selection(
        validation,
        "Krea 2 Qwen Image VAE",
        params.get("vae"), params.get("vae_or_ae"), params.get("ae"),
    ))

    variant = resolve_krea2_variant(route.family or job.family or "krea2_turbo", model_name)
    compatibility = check_krea2_compatibility("krea2_turbo", model_name, text_encoder, vae, loader=loader_id)
    if variant != "turbo":
        validation.errors.append("LanPaint Phase 5 is locked to the Krea 2 Turbo family; RAW/Base model selections are not compiled through this route.")
        validation.ok = False
    if compatibility.compatible is False:
        validation.errors.append(compatibility.message)
        validation.ok = False
    elif compatibility.compatible is None and compatibility.message:
        validation.warnings.append(compatibility.message)

    route_contract, contract_issues = normalize_lanpaint_route_contract(_lanpaint_route_request(provider_id, params, loader_id=loader_id, mode=runtime_mode))
    for issue in contract_issues:
        message = str(issue.get("message") or "LanPaint route contract validation failed.")
        if issue.get("level") == "error":
            validation.errors.append(message)
            validation.ok = False
        else:
            validation.warnings.append(message)

    plan = build_lanpaint_comfy_compile_plan(route_contract, backend, lora_stack_enabled=bool(base_graph_loras))
    for message in _phase5_blocker_messages(plan):
        if message not in validation.errors:
            validation.errors.append(message)
            validation.ok = False

    adapter = resolve_lanpaint_family_adapter(route_contract)
    spatial = _mapping(adapter.get("spatial"))
    crop = _mapping(spatial.get("crop"))
    processing_size = _mapping(crop.get("processing_size"))
    mask_policy = _mapping(spatial.get("mask"))
    sampling_mask = _mapping(mask_policy.get("sampling"))
    stitch_mask = _mapping(mask_policy.get("stitch"))
    sampler_policy = _mapping(_mapping(adapter.get("sampler")).get("defaults"))
    stitch_policy = _mapping(spatial.get("stitch"))
    adapter_slots = _mapping(_mapping(adapter.get("assets")).get("slots"))

    model_asset_label = "Krea 2 Turbo GGUF transformer" if loader_id == "gguf" else "Krea 2 Turbo safetensors/component transformer"
    selected_assets = [
        _verify_selected_asset(
            validation, backend, loader_id=loader_id,
            role_id=str(_mapping(adapter_slots.get("model")).get("role_id") or ("gguf_unet" if loader_id == "gguf" else "diffusion_model")),
            label=model_asset_label, selected=model_name,
        ),
        _verify_selected_asset(validation, backend, loader_id=loader_id, role_id=str(_mapping(adapter_slots.get("text_encoder")).get("role_id") or "krea2_clip_loader"), label="Qwen3-VL-4B Krea 2 text encoder", selected=text_encoder),
        _verify_selected_asset(validation, backend, loader_id=loader_id, role_id=str(_mapping(adapter_slots.get("vae")).get("role_id") or "qwen_image_vae"), label="Qwen Image VAE", selected=vae),
    ]

    selection_target_raw = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").strip().lower()
    invert_mask = selection_target_raw in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"}
    capability_report = evaluate_lanpaint_route_capabilities(
        backend,
        provider_id=provider_id,
        family=route.family or job.family or "krea2_turbo",
        loader=loader_id,
        mode=route.mode or job.mode or "inpaint",
        engine="lanpaint",
        selected_assets={"model": model_name, "text_encoder": text_encoder, "vae": vae},
        require_invert_mask=invert_mask,
        require_model_only_lora=bool(base_graph_loras),
    )
    for blocker in capability_report.get("blockers", []):
        message = f"LanPaint capability gate [{blocker.get('code') or 'blocked'}]: {blocker.get('message') or 'Route requirements are unavailable.'}"
        if message not in validation.errors:
            validation.errors.append(message)
            validation.ok = False
    for warning in capability_report.get("warnings", []):
        message = f"LanPaint capability notice [{warning.get('code') or 'notice'}]: {warning.get('message') or ''}".strip()
        if message not in validation.warnings:
            validation.warnings.append(message)
    phase5_signature_mismatches = _validate_phase5_graph_signatures(
        validation, backend, require_invert_mask=invert_mask
    )

    ui_state = deepcopy(ui_state)
    ui_state["capability"] = deepcopy(capability_report)
    ui_state["route"]["route_state"] = capability_report.get("status")
    ui_state["route"]["selectable"] = bool(capability_report.get("selectable"))
    ui_state["route"]["capability_checked"] = bool(capability_report.get("discovery", {}).get("checked"))
    ui_state["route"]["capability_fingerprint"] = capability_report.get("capability_fingerprint")
    ui_state["validation"]["capability_ok"] = bool(capability_report.get("executable"))
    ui_state_fingerprint_payload = deepcopy(ui_state)
    ui_state_fingerprint_payload.pop("state_fingerprint", None)
    ui_state["state_fingerprint"] = hashlib.sha256(
        json.dumps(ui_state_fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647
    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""

    padding = int(_param(params, "lanpaint_crop_padding", "crop_padding", default=crop.get("padding_px") if crop.get("padding_px") is not None else 152))
    process_width = int(_param(params, "lanpaint_processing_width", default=processing_size.get("width") if processing_size.get("width") is not None else 768))
    process_height = int(_param(params, "lanpaint_processing_height", default=processing_size.get("height") if processing_size.get("height") is not None else 768))
    resize_method = str(_param(params, "lanpaint_resize_method", default=crop.get("resize_method") or "lanczos"))
    sample_expand = int(_param(params, "lanpaint_sampling_mask_expand", default=sampling_mask.get("expand_px") if sampling_mask.get("expand_px") is not None else 0))
    sample_blur = float(_param(params, "lanpaint_sampling_mask_blur", default=sampling_mask.get("blur_radius") if sampling_mask.get("blur_radius") is not None else 0.0))
    stitch_expand = int(_param(params, "lanpaint_stitch_mask_expand", default=stitch_mask.get("expand_px") if stitch_mask.get("expand_px") is not None else 0))
    stitch_blur = float(_param(params, "lanpaint_stitch_mask_blur", default=stitch_mask.get("blur_radius") if stitch_mask.get("blur_radius") is not None else 0.0))
    steps = int(_param(params, "steps", "lanpaint_steps", default=sampler_policy.get("steps") if sampler_policy.get("steps") is not None else 8))
    cfg = float(_param(params, "cfg", "lanpaint_cfg", default=sampler_policy.get("cfg") if sampler_policy.get("cfg") is not None else 1.0))
    sampler_name = str(_param(params, "sampler", "lanpaint_sampler", default=sampler_policy.get("sampler_name") or "euler"))
    scheduler = str(_param(params, "scheduler", "lanpaint_scheduler", default=sampler_policy.get("scheduler") or "simple"))
    denoise = float(_param(params, "denoise", "lanpaint_denoise", default=sampler_policy.get("denoise") if sampler_policy.get("denoise") is not None else 1.0))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    thinking_steps = int(_param(params, "lanpaint_thinking_steps", default=sampler_policy.get("lanpaint_thinking_steps") if sampler_policy.get("lanpaint_thinking_steps") is not None else 10))
    requested_prompt_mode = str(_param(params, "lanpaint_prompt_mode", default=sampler_policy.get("prompt_mode") or "image_first")).strip().lower().replace(" ", "_")
    prompt_mode = "Prompt First" if requested_prompt_mode in {"prompt_first", "prompt"} else "Image First"
    restore_method = str(_param(params, "lanpaint_stitch_resize_method", default=stitch_policy.get("resize_method") or resize_method))

    workflow: dict[str, Any] = {}
    node_roles: dict[str, str] = {}

    def add(node_id: int, class_type: str, inputs: dict[str, Any], role_id: str) -> None:
        workflow[str(node_id)] = {"class_type": class_type, "inputs": inputs}
        node_roles[str(node_id)] = role_id

    external = _mapping(plan.get("external_bindings"))
    model_loader = str(_mapping(external.get("family_model")).get("node_class") or "")
    loader_policy = _mapping(_mapping(adapter.get("loaders")).get("model"))
    loader_input_keys = _mapping(loader_policy.get("input_keys"))
    model_input_key = str(loader_input_keys.get(model_loader) or ("gguf_name" if model_loader == "LoaderGGUF" else "unet_name"))

    if validation.ok:
        add(1, "LoadImage", {"image": source_name}, "source_image")
        add(2, "LoadImage", {"image": mask_name}, "mask_image")
        add(3, "ImageToMask", {"image": ["2", 0], "channel": "red"}, "mask_image_to_mask")
        mask_ref: list[Any] = ["3", 0]
        next_id = 4
        if invert_mask:
            add(next_id, "InvertMask", {"mask": mask_ref}, "mask_target_inversion")
            mask_ref = [str(next_id), 0]
            next_id += 1
        crop_id = next_id
        add(crop_id, "CropByMask", {"image": ["1", 0], "mask": mask_ref, "padding": padding}, "crop_context")
        next_id += 1

        resize_inputs: dict[str, Any] = {
            "image": [str(crop_id), 0],
            "width": process_width,
            "height": process_height,
            "upscale_method": resize_method,
            "keep_proportion": "resize",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 2,
            "mask": [str(crop_id), 1],
        }
        _optional_input(resize_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        processing_resize_id = next_id
        add(processing_resize_id, "ImageResizeKJv2", resize_inputs, "processing_resize")
        next_id += 1

        sample_grow_inputs: dict[str, Any] = {
            "mask": [str(processing_resize_id), 3],
            "expand": sample_expand,
            "incremental_expandrate": 0.0,
            "tapered_corners": True,
            "flip_input": False,
            "blur_radius": sample_blur,
            "lerp_alpha": 1.0,
            "decay_factor": 1.0,
        }
        _optional_input(sample_grow_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(sampling_mask.get("fill_holes", False)))
        sample_mask_id = next_id
        add(sample_mask_id, "GrowMaskWithBlur", sample_grow_inputs, "sampling_mask_refine")
        next_id += 1

        vae_loader_id = next_id
        add(vae_loader_id, "VAELoader", {"vae_name": vae}, "family_vae")
        next_id += 1
        latent_encode_id = next_id
        add(latent_encode_id, "VAEEncode", {"pixels": [str(processing_resize_id), 0], "vae": [str(vae_loader_id), 0]}, "latent_encode")
        next_id += 1
        noise_mask_id = next_id
        add(noise_mask_id, "SetLatentNoiseMask", {"samples": [str(latent_encode_id), 0], "mask": [str(sample_mask_id), 0]}, "latent_noise_mask")
        next_id += 1

        model_loader_id = next_id
        model_loader_inputs = {model_input_key: model_name}
        model_loader_inputs.update(_mapping(loader_policy.get("default_inputs")))
        add(model_loader_id, model_loader, model_loader_inputs, "family_model_loader")
        next_id += 1
        clip_inputs: dict[str, Any] = {"clip_name": text_encoder, "type": "krea2", "device": "default"}
        clip_loader_id = next_id
        add(clip_loader_id, "CLIPLoader", clip_inputs, "family_text_encoder")
        next_id += 1
        positive_id = next_id
        add(positive_id, "CLIPTextEncode", {"text": effective_prompt, "clip": [str(clip_loader_id), 0]}, "positive_conditioning")
        next_id += 1
        negative_id = next_id
        add(negative_id, "ConditioningZeroOut", {"conditioning": [str(positive_id), 0]}, "negative_conditioning")
        next_id += 1

        differential_id = next_id
        add(differential_id, "DifferentialDiffusionAdvanced", {
            "model": [str(model_loader_id), 0],
            "samples": [str(noise_mask_id), 0],
            "mask": [str(sample_mask_id), 0],
            "multiplier": 1.0,
        }, "family_model_transform")
        next_id += 1
        latent_ref = [str(differential_id), 1]
        if batch_count > 1:
            repeat_id = next_id
            add(repeat_id, "RepeatLatentBatch", {"samples": list(latent_ref), "amount": batch_count}, "latent_batch_repeat")
            latent_ref = [str(repeat_id), 0]
            next_id += 1
        sampler_id = next_id
        add(sampler_id, "LanPaint_KSampler", {
            "model": [str(differential_id), 0],
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "positive": [str(positive_id), 0],
            "negative": [str(negative_id), 0],
            "latent_image": latent_ref,
            "denoise": denoise,
            "LanPaint_NumSteps": thinking_steps,
            "LanPaint_PromptMode": prompt_mode,
            "LanPaint_Info": "LanPaint KSampler.",
            "Inpainting_mode": "🖼️ Image Inpainting",
        }, "lanpaint_sample")
        next_id += 1
        decode_id = next_id
        add(decode_id, "VAEDecode", {"samples": [str(sampler_id), 0], "vae": [str(vae_loader_id), 0]}, "latent_decode")
        next_id += 1

        restore_inputs: dict[str, Any] = {
            "image": [str(decode_id), 0],
            "width": [str(crop_id), 4],
            "height": [str(crop_id), 5],
            "upscale_method": restore_method,
            "keep_proportion": "stretch",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 2,
            "mask": [str(sample_mask_id), 0],
        }
        _optional_input(restore_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        restore_id = next_id
        add(restore_id, "ImageResizeKJv2", restore_inputs, "restore_crop_size")
        next_id += 1

        stitch_grow_inputs: dict[str, Any] = {
            "mask": [str(restore_id), 3],
            "expand": stitch_expand,
            "incremental_expandrate": 0.0,
            "tapered_corners": True,
            "flip_input": False,
            "blur_radius": stitch_blur,
            "lerp_alpha": 1.0,
            "decay_factor": 1.0,
        }
        _optional_input(stitch_grow_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(stitch_mask.get("fill_holes", False)))
        stitch_mask_id = next_id
        add(stitch_mask_id, "GrowMaskWithBlur", stitch_grow_inputs, "stitch_mask_refine")
        next_id += 1
        composite_id = next_id
        add(composite_id, "ImageCompositeMasked", {
            "destination": ["1", 0],
            "source": [str(restore_id), 0],
            "x": [str(crop_id), 2],
            "y": [str(crop_id), 3],
            "resize_source": False,
            "mask": [str(stitch_mask_id), 0],
        }, "stitch_composite")
        next_id += 1
        output_id = next_id
        add(output_id, "PreviewImage", {"images": [str(composite_id), 0]}, "output_handoff")
    else:
        sampler_id = 0
        output_id = 0

    lora_route = {
        "backend": provider_id,
        "provider_id": provider_id,
        "family": "krea2_turbo",
        "loader": loader_id,
        "workflow_mode": runtime_mode,
        "mode": runtime_mode,
        "engine": "lanpaint",
        "route_key": f"krea2_turbo:{loader_id}:{runtime_mode}:lanpaint",
        "route_state": "experimental_available",
    }
    lora_patch_profile = build_lora_patch_profile(
        route=lora_route,
        model_ref=[str(model_loader_id), 0] if validation.ok else None,
        clip_ref=None,
        sampler_node_id=str(sampler_id or ""),
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly",
        requires_model=True,
        requires_clip=False,
        source="neo_app.providers.comfy_workflows.lanpaint.phase6",
        strategy="lora_loader_model_only_consumer_rewire",
        patch_model_consumers=True,
        patch_clip_consumers=False,
        validated=False,
        notes=[
            f"Krea 2 Turbo {loader_id} {runtime_mode} with engine=lanpaint.",
            "Rewire the selected model consumer before DifferentialDiffusionAdvanced; do not patch Krea 2 CLIP conditioning.",
            "Physical Comfy validation is required before promotion from experimental_available.",
        ],
    )

    controls = {
        "crop_padding": padding,
        "processing_size": {"width": process_width, "height": process_height},
        "resize_method": resize_method,
        "restore_resize_method": restore_method,
        "sampling_mask": {"expand": sample_expand, "blur": sample_blur},
        "stitch_mask": {"expand": stitch_expand, "blur": stitch_blur},
        "steps": steps,
        "cfg": cfg,
        "sampler": sampler_name,
        "scheduler": scheduler,
        "denoise": denoise,
        "thinking_steps": thinking_steps,
        "prompt_mode": prompt_mode,
    }
    actual_params = {
        **params,
        "inpaint_engine": "lanpaint",
        "workflow_type": WORKFLOW_TYPE,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "batch_count": batch_count,
        "source_image_name": source_name,
        "mask_image_name": mask_name,
        "gguf_model": model_name if loader_id == "gguf" else "",
        "diffusion_model": model_name if loader_id == "diffusion_model" else "",
        "qwen3vl_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "text_encoder_2": "",
        "vae": vae,
        "krea2_variant": "turbo",
        "clip_type": "krea2",
        "steps": steps,
        "cfg": cfg,
        "denoise": denoise,
        "sampler": sampler_name,
        "scheduler": scheduler,
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "lanpaint_route": {
            "route_family_id": ROUTE_FAMILY_ID,
            "route_key": _mapping(adapter.get("identity")).get("route_key"),
            "engine": "lanpaint",
            "family": "krea2_turbo",
            "loader": loader_id,
            "variant": PHASE5_VARIANT,
            "policy_id": _mapping(adapter.get("policy")).get("policy_id"),
            "compiler_id": COMPILER_ID,
            "graph_state": PHASE5_GRAPH_STATE if loader_id == "gguf" else PHASE14_STATE,
        },
        "lanpaint_controls": controls,
        "lanpaint_ui_state": deepcopy(ui_state),
        "lanpaint_ui_state_fingerprint": ui_state.get("state_fingerprint"),
        "_neo_lanpaint_phase7_ui_state": PHASE7_UI_STATE,
        "_neo_lanpaint_phase8_capability_state": PHASE8_CAPABILITY_STATE,
        "_neo_lanpaint_phase11_state": PHASE11_STATE,
        "lanpaint_capability_report": deepcopy(capability_report),
        "lanpaint_capability_fingerprint": capability_report.get("capability_fingerprint"),
        "lanpaint_contract_fingerprint": route_contract.get("contract_fingerprint"),
        "lanpaint_compile_plan_fingerprint": plan.get("plan_fingerprint"),
        "lanpaint_family_adapter": adapter_snapshot(adapter),
        "lanpaint_family_adapter_id": _mapping(adapter.get("identity")).get("adapter_id"),
        "lanpaint_family_adapter_fingerprint": adapter.get("adapter_fingerprint"),
        "_neo_lanpaint_phase13_state": PHASE13_STATE,
        "_neo_lanpaint_phase14_state": PHASE14_STATE,
        "lanpaint_loader_parity_group": f"krea2_turbo:{runtime_mode}:lanpaint",
        "lanpaint_node_roles": node_roles,
        "lanpaint_selected_assets": selected_assets,
        "lanpaint_phase5_signature_mismatches": phase5_signature_mismatches,
        "lanpaint_mask_target": "not_masked_area" if invert_mask else "masked_area",
        "_neo_sampler_node_id": str(sampler_id or ""),
        "_neo_lanpaint_phase5_graph": bool(validation.ok),
        "_neo_lanpaint_phase6_lora_state": PHASE6_LORA_STATE,
        "_neo_lanpaint_lora_execution": "model_only_experimental" if base_graph_loras else "inactive",
        "_neo_lora_patch_profile": lora_patch_profile,
        "lanpaint_lora_route": lora_route,
        "lanpaint_lora_mode": "model_only",
        "lanpaint_lora_requested_rows": deepcopy(active_loras),
        "lanpaint_lora_base_graph_rows": deepcopy(base_graph_loras),
        "lanpaint_lora_deferred_rows": [
            deepcopy(row) for row in active_loras if row not in base_graph_loras
        ],
    }

    actual_params = refresh_lanpaint_replay_contract(
        actual_params,
        provider_id=provider_id,
        workflow_prompt=workflow,
    )

    return CompiledJob(
        provider_id=provider_id,
        compile_status="compiled" if validation.ok else "mock_compiled",
        backend_payload={
            "provider_id": provider_id,
            "backend": "comfyui",
            "base_url": base_url,
            "validation": model_to_dict(validation),
            "prompt": workflow,
            "client_id": f"neo-studio-v2-{uuid4().hex[:8]}",
            "actual_params": actual_params,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(),
            "capabilities": capabilities,
            "backend_capabilities": backend,
            "lanpaint_compile_plan": plan,
            "lanpaint_route_capabilities": deepcopy(capability_report),
            "prompt_conditioning": conditioning,
            "phase_notes": [
                f"LanPaint Phase 14 uses the parity-stabilized Krea 2 Turbo {loader_id} crop/resize/stitch workflow as a provider-owned API graph.",
                "Native Krea 2 inpaint remains unchanged and is selected whenever inpaint_engine is native or omitted.",
                "The selected transformer is paired with native CLIPLoader(type=krea2) Qwen3-VL-4B conditioning and Qwen Image VAE.",
                "DifferentialDiffusionAdvanced consumes the sampled mask and returns both the patched model and latent used by LanPaint_KSampler.",
                "The decoded patch and mask are restored to CropByMask geometry before source-space compositing, preserving original output dimensions.",
                "LoRA Stack may patch either loader route only through the compiler-owned LoraLoaderModelOnly consumer-rewire profile; Krea 2 CLIP conditioning remains untouched.",
                "Phase 7 records one route-aware LanPaint UI state block with requested values, resolved family-policy values, value authority, badges, route activity, and a replay fingerprint.",
                "Phase 8 evaluates live node classes, node signatures, loader roles, Krea CLIP support, required model catalogs, selected assets, and optional model-only LoRA support before graph emission.",
                "Other extension patchers remain excluded from the LanPaint topology until they declare their own engine-aware support.",
            ],
        },
    )


# Phase 14 public name; the GGUF-specific symbol remains as a compatibility alias.
compile_lanpaint_krea2_turbo_inpaint = compile_lanpaint_krea2_turbo_gguf_inpaint

__all__ = [
    "AUTHORITY",
    "COMPILER_ID",
    "COMPILER_STATE",
    "LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES",
    "LANPAINT_OBJECT_INFO_NODE_CLASSES",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_PROVIDERS",
    "WORKFLOW_TYPE",
    "PHASE5_GRAPH_STATE",
    "PHASE5_VARIANT",
    "PHASE6_LORA_STATE",
    "PHASE7_UI_STATE",
    "PHASE8_CAPABILITY_STATE",
    "PHASE14_STATE",
    "build_lanpaint_comfy_compile_plan",
    "compile_lanpaint_krea2_turbo_inpaint",
    "compile_lanpaint_krea2_turbo_gguf_inpaint",
    "lanpaint_comfy_compile_plan_fingerprint",
    "validate_lanpaint_comfy_compile_plan",
]
