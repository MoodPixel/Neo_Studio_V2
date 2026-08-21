from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT_DIR / "neo_app" / "models" / "model_family_manifest.json"
SCHEMA_ID = "neo.image.component_topology.v1"

# Public format is intentionally distinct from the internal loading strategy.
FORMAT_BY_LOADER = {
    "checkpoint": "safetensors",
    "checkpoint_aio": "safetensors",
    "diffusion_model": "safetensors",
    "unet": "safetensors",
    "gguf": "gguf",
    "api_model": "api",
}
LOAD_STRATEGY_BY_LOADER = {
    "checkpoint": "checkpoint",
    "checkpoint_aio": "checkpoint_aio",
    "diffusion_model": "split_components",
    "unet": "split_components",
    "gguf": "gguf_components",
    "api_model": "api_model",
}

PRIMARY_MODEL_ROLES = {
    "checkpoint",
    "qwen_rapid_aio_checkpoint",
    "diffusion_model",
    "unet",
    "gguf_unet",
    "wan_model",
    "ideogram4_main_model",
    "ideogram4_main_model_gguf",
    "api_model",
    "provider_wan_model",
    "provider_hunyuan_model",
    "provider_hidream_model",
}

NON_COMPONENT_ROLES = {
    "clip_skip_optional",
    "flux_guidance",
    "krea2_clip_loader",
    "wan_task",
    "hunyuan_variant",
    "hidream_variant",
    "turbo_mode_optional",
}

# Logical family roles -> stable UI component slots.  The slot is the important
# contract: the selected file format never decides whether an encoder/VAE/MMProj
# selector exists.  The active family/loader/mode topology does.
ROLE_SPECS: dict[str, dict[str, Any]] = {
    "vae_optional": {"field_id": "vae", "label": "VAE override", "catalog": "vaes", "required": False},
    "vae": {"field_id": "vae", "label": "VAE", "catalog": "vaes"},
    "vae_or_ae": {"field_id": "vae", "label": "VAE / AE", "catalog": "vaes"},
    "ae_or_vae": {"field_id": "vae", "label": "AE / VAE", "catalog": "vaes"},
    "sd3_vae": {"field_id": "vae", "label": "SD3 VAE", "catalog": "vaes"},
    "qwen_image_vae": {"field_id": "vae", "label": "Qwen Image VAE", "catalog": "vaes"},
    "wan_vae": {"field_id": "vae", "label": "Wan VAE", "catalog": "vaes"},
    "flux2_vae": {"field_id": "vae", "label": "Flux 2 VAE", "catalog": "vaes"},
    "vae_if_required": {"field_id": "vae", "label": "VAE / AE", "catalog": "vaes", "required": False, "conditional": True},

    "text_encoder_primary": {"field_id": "text_encoder_1", "label": "Text Encoder 1", "catalog": "text_encoders"},
    "text_encoder_secondary": {"field_id": "text_encoder_2", "label": "Text Encoder 2", "catalog": "text_encoders"},
    "gguf_text_encoder_primary": {"field_id": "text_encoder_1", "label": "Text Encoder 1", "catalog": "gguf_text_encoder_primary"},
    "gguf_text_encoder_secondary": {"field_id": "text_encoder_2", "label": "Text Encoder 2", "catalog": "gguf_text_encoder_secondary"},
    "qwen_text_encoder": {"field_id": "qwen_text_encoder", "label": "Qwen Text Encoder", "catalog": "qwen_text_encoders"},
    "qwen3_text_encoder": {"field_id": "qwen3_text_encoder", "label": "Qwen3 Text Encoder", "catalog": "qwen_text_encoders"},
    "qwen3vl_4b_text_encoder": {"field_id": "qwen3vl_text_encoder", "label": "Qwen3-VL-4B Text Encoder", "catalog": "text_encoders"},
    "mistral3_text_encoder": {"field_id": "text_encoder_1", "label": "Mistral3 Text Encoder", "catalog": "text_encoders"},
    "umt5_text_encoder": {"field_id": "text_encoder_1", "label": "UMT5 Text Encoder", "catalog": "text_encoders"},
    "anima_qwen3_06b_text_encoder": {"field_id": "text_encoder_1", "label": "Qwen3 0.6B Text Encoder", "catalog": "text_encoders"},
    "ideogram4_qwen3_vl_text_encoder": {"field_id": "text_encoder_1", "label": "Qwen3-VL Text Encoder", "catalog": "text_encoders"},
    "text_encoder_if_required": {"field_id": "text_encoder_1", "label": "Text Encoder", "catalog": "text_encoders", "required": False, "conditional": True},

    "sd3_clip_l": {"field_id": "text_encoder_1", "label": "CLIP-L", "catalog": "text_encoders"},
    "sd3_clip_g": {"field_id": "text_encoder_2", "label": "CLIP-G", "catalog": "text_encoders"},
    "sd3_t5xxl": {"field_id": "text_encoder_3", "label": "T5XXL", "catalog": "text_encoders"},
    "hidream_clip_l": {"field_id": "text_encoder_1", "label": "CLIP-L", "catalog": "text_encoders"},
    "hidream_clip_g": {"field_id": "text_encoder_2", "label": "CLIP-G", "catalog": "text_encoders"},
    "hidream_t5xxl": {"field_id": "text_encoder_3", "label": "T5XXL", "catalog": "text_encoders"},
    "hidream_llama_3_1_8b": {"field_id": "text_encoder_4", "label": "Llama 3.1 8B Text Encoder", "catalog": "text_encoders"},

    "mmproj_optional": {"field_id": "qwen_mmproj", "label": "MMProj", "catalog": "mmproj", "required": False},
    "qwen_mmproj_optional": {"field_id": "qwen_mmproj", "label": "Qwen MMProj", "catalog": "mmproj", "required": False},
    "qwen_mmproj": {"field_id": "qwen_mmproj", "label": "Qwen MMProj", "catalog": "mmproj"},

    "ideogram4_unconditional_model": {"field_id": "ideogram4_unconditional_model", "label": "Unconditional Model", "catalog": "diffusion_models"},
    "ideogram4_unconditional_model_gguf": {"field_id": "ideogram4_unconditional_model", "label": "Unconditional Model", "catalog": "gguf_models"},
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _families() -> list[dict[str, Any]]:
    return [row for row in (_manifest().get("families") or []) if isinstance(row, dict)]


def _mode_state(family: Mapping[str, Any], loader: str, mode: str) -> str:
    loader_states = _mapping(_mapping(family.get("loader_mode_support")).get(loader))
    return str(loader_states.get(mode) or _mapping(family.get("mode_support")).get(mode) or "unsupported")


def _mmproj_required(mode_state: str) -> bool:
    return "mmproj_required" in str(mode_state or "").lower()


def _component_from_role(role: str, *, loader: str, mode_state: str) -> dict[str, Any] | None:
    spec = ROLE_SPECS.get(role)
    if spec is None:
        return None
    component = {
        "role_id": role,
        "field_id": spec["field_id"],
        "label": spec["label"],
        "catalog": spec.get("catalog", ""),
        "required": bool(spec.get("required", True)),
        "conditional": bool(spec.get("conditional", False)),
        "kind": "asset",
    }
    # MMProj is route-driven, never format-driven. A role that is normally
    # optional becomes required only when the active loader/mode contract says so.
    if component["field_id"] == "qwen_mmproj" and _mmproj_required(mode_state):
        component["required"] = True
        component["conditional"] = False
    # Native Krea2 deliberately keeps its Qwen3-VL encoder in the normal
    # text-encoder catalog even when the transformer itself is GGUF.
    if role == "qwen3vl_4b_text_encoder":
        component["catalog"] = "text_encoders"
    # A generic text encoder role follows the active artifact family unless a
    # family-specific role explicitly declares a native encoder above.
    if loader == "gguf" and role in {"text_encoder_primary", "text_encoder_secondary"}:
        component["catalog"] = "gguf_text_encoder_primary" if role.endswith("primary") else "gguf_text_encoder_secondary"
    return component


def resolve_component_topology(family: Mapping[str, Any], loader: str, mode: str) -> dict[str, Any]:
    family_id = str(family.get("family_id") or "")
    required_roles = list(_mapping(family.get("required_roles")).get(loader) or [])
    mode_state = _mode_state(family, loader, mode)
    components: list[dict[str, Any]] = []
    unmapped_roles: list[str] = []
    seen_fields: dict[str, int] = {}

    for role in required_roles:
        if role in PRIMARY_MODEL_ROLES or role in NON_COMPONENT_ROLES:
            continue
        component = _component_from_role(role, loader=loader, mode_state=mode_state)
        if component is None:
            unmapped_roles.append(role)
            continue
        field_id = component["field_id"]
        if field_id in seen_fields:
            # Preserve a stronger requirement/role label if two logical aliases
            # map to the same visible slot.
            current = components[seen_fields[field_id]]
            current["required"] = bool(current.get("required") or component.get("required"))
            current.setdefault("role_aliases", []).append(role)
            continue
        seen_fields[field_id] = len(components)
        components.append(component)

    artifact_format = FORMAT_BY_LOADER.get(loader, "unknown")
    load_strategy = LOAD_STRATEGY_BY_LOADER.get(loader, loader or "unknown")
    bundled = load_strategy in {"checkpoint", "checkpoint_aio"} and not components
    return {
        "schema": SCHEMA_ID,
        "family": family_id,
        "loader": loader,
        "mode": mode,
        "mode_state": mode_state,
        "artifact_format": artifact_format,
        "load_strategy": load_strategy,
        "bundled": bundled,
        "required_roles": required_roles,
        "components": components,
        "unmapped_component_roles": unmapped_roles,
        "policy": {
            "format_does_not_define_component_topology": True,
            "mmproj_is_route_driven": True,
            "safetensors_may_require_external_components": True,
            "quantization_does_not_change_loader_contract": True,
        },
    }


def build_image_component_topology_payload() -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for family in _families():
        if "image" not in (family.get("surfaces") or []):
            continue
        loaders = list(family.get("supported_loaders") or [])
        modes = set(family.get("supported_modes") or [])
        for loader_modes in _mapping(family.get("loader_mode_support")).values():
            modes.update(_mapping(loader_modes).keys())
        for loader in loaders:
            for mode in sorted(modes):
                entry = resolve_component_topology(family, loader, mode)
                entries[f"{family.get('family_id')}:{loader}:{mode}"] = entry
    return {
        "schema": SCHEMA_ID,
        "entries": entries,
        "public_formats": ["safetensors", "gguf"],
        "internal_load_strategies": sorted(set(LOAD_STRATEGY_BY_LOADER.values())),
        "policy": {
            "public_format_is_not_loader_strategy": True,
            "component_visibility_comes_from_route_roles": True,
            "quantized_safetensors_remain_safetensors": True,
            "gguf_does_not_own_text_encoder_visibility": True,
        },
    }
