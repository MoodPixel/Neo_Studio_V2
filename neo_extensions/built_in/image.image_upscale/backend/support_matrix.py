"""Phase D support matrix for the Image Upscale built-in extension.

Image Upscale is node-based, but it is not SDXL/Flux/Qwen/checkpoint/GGUF
based. It builds a standalone Comfy image utility graph from an existing
source image. Support is therefore decided by provider + required image nodes,
with optional controls gated independently. It is global across Image-tab
model families, loaders, workflow modes, and image subtabs because it consumes
an already-existing image rather than the active generation architecture.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .constants import (
    ACTIVE_ROUTE_STATES,
    DISCOVERED_MODES,
    EXTENSION_ID,
    GATED_ROUTE_STATES,
    IMAGE_WORKSPACE,
    OPTIONAL_NODE_GROUPS,
    PHASE,
    PROVIDER_GATED,
    REQUIRED_COMFY_NODES,
    SUPPORTED_COMFY_BACKENDS,
    UNSUPPORTED,
    VALID_ROUTE_STATES,
    WORKSPACE_APP,
)

_DATA_PATH = Path(__file__).with_name("support_matrix_data.json")

BACKEND_ALIASES = {
    "comfy": "comfyui",
    "comfy_ui": "comfyui",
    "comfyui-local": "comfyui",
    "comfyui_portable": "comfyui_portable",
    "portable_comfyui": "comfyui_portable",
    "automatic1111": "a1111",
    "sd_webui": "a1111",
}
FAMILY_ALIASES = {
    "sd": "sd15",
    "sd1": "sd15",
    "sd1_5": "sd15",
    "sd1.5": "sd15",
    "stable_diffusion": "sd15",
    "stable_diffusion_xl": "sdxl",
    "sd_xl": "sdxl",
    "qwen": "qwen_image",
    "qwen_image_edit": "qwen_image",
    "qwen_rapid": "qwen_rapid_aio",
    "qwen_rapid_aio": "qwen_rapid_aio",
    "qwen_rapid_aio_checkpoint": "qwen_rapid_aio",
    "qwen_rapid_aio_gguf": "qwen_rapid_aio",
    "qwen-rapid-aio": "qwen_rapid_aio",
    "qwen-image": "qwen_image",
    "qwen_image_edit_2509": "qwen_image_edit_2509",
    "qwen-image-edit-2509": "qwen_image_edit_2509",
    "qwen_2509": "qwen_image_edit_2509",
    "zimage": "z_image",
    "z-image": "z_image",
    "zimage_turbo": "z_image_turbo",
    "z-image-turbo": "z_image_turbo",
    "z_image_turbo": "z_image_turbo",
    "flux_klein": "flux2_klein",
    "flux2_klein": "flux2_klein",
    "flux_2_klein": "flux2_klein",
    "hunyuan": "hunyuan_image",
    "wan": "wan_image",
}
LOADER_ALIASES = {
    "ckpt": "checkpoint",
    "safetensors": "checkpoint",
    "checkpoint_loader": "checkpoint",
    "checkpoint_aio": "checkpoint_aio",
    "aio_checkpoint": "checkpoint_aio",
    "checkpointloader": "checkpoint",
    "diffusion": "diffusion_model",
    "diffusionmodel": "diffusion_model",
    "unet": "diffusion_model",
    "gguf_loader": "gguf",
    "ggufloader": "gguf",
}
MODE_ALIASES = {
    "text_to_image": "txt2img",
    "text2image": "txt2img",
    "t2i": "txt2img",
    "image_to_image": "img2img",
    "i2i": "img2img",
    "repair": "inpaint",
    "mask": "inpaint",
    "upscale": "image_upscale",
    "finish_upscale": "image_upscale",
}
SUBTAB_ALIASES = {
    "finnish": "finish",
    "finishing": "finish",
    "finish_pass": "finish",
    "generate": "generations",
    "output": "results",
    "outputs": "results",
}


def _clean(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_backend(value: Any) -> str:
    text = _clean(value, "comfyui")
    return BACKEND_ALIASES.get(text, text or "comfyui")


def normalize_family(value: Any) -> str:
    text = _clean(value, "unknown")
    return FAMILY_ALIASES.get(text, text or "unknown")


def normalize_loader(value: Any) -> str:
    text = _clean(value, "unknown")
    return LOADER_ALIASES.get(text, text or "unknown")


def normalize_mode(value: Any) -> str:
    text = _clean(value, "image_upscale")
    return MODE_ALIASES.get(text, text or "image_upscale")


def normalize_workspace(value: Any) -> str:
    return _clean(value, IMAGE_WORKSPACE) or IMAGE_WORKSPACE


def normalize_subtab(value: Any) -> str:
    text = _clean(value, WORKSPACE_APP)
    return SUBTAB_ALIASES.get(text, text or WORKSPACE_APP)


def normalize_route(route: dict[str, Any] | None = None, **overrides: Any) -> dict[str, str]:
    source = dict(route or {})
    source.update({key: value for key, value in overrides.items() if value is not None})
    return {
        "backend": normalize_backend(source.get("backend") or source.get("provider") or source.get("provider_id")),
        "family": normalize_family(source.get("family") or source.get("model_family")),
        "loader": normalize_loader(source.get("loader") or source.get("loader_type") or source.get("model_loader")),
        "mode": normalize_mode(source.get("workflow_mode") or source.get("mode") or source.get("generation_mode")),
        "workspace": normalize_workspace(source.get("workspace") or source.get("surface") or IMAGE_WORKSPACE),
        "subtab": normalize_subtab(source.get("workspace_app") or source.get("workspace_subtab") or source.get("subtab") or WORKSPACE_APP),
    }


@lru_cache(maxsize=1)
def load_support_matrix() -> dict[str, Any]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _rows_by_key() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {
        (row["backend"], row["family"], row["loader"], row["workflow_mode"]): row
        for row in load_support_matrix().get("rows", [])
    }


def route_key(route: dict[str, Any] | None = None, **overrides: Any) -> str:
    norm = normalize_route(route, **overrides)
    return f"{norm['backend']}:{norm['family']}:{norm['loader']}:{norm['mode']}"


def reason_text(reason_code: str | None = None, state: str | None = None) -> str:
    reasons = load_support_matrix().get("reasons", {})
    if reason_code and reason_code in reasons:
        return reasons[reason_code]
    if state == PROVIDER_GATED:
        return reasons.get("non_comfy_provider_gated", "Provider is gated for Image Upscale.")
    if state == UNSUPPORTED:
        return reasons.get("finish_mount_only", "Image Upscale is not supported on this route.")
    return reasons.get("unknown_route", "No Image Upscale support reason was declared.")


def _workspace_overlay(normalized: dict[str, str], require_finish_subtab: bool) -> tuple[str | None, str | None]:
    if normalized["workspace"] != IMAGE_WORKSPACE:
        return UNSUPPORTED, "not_image_workspace"
    if not require_finish_subtab:
        return None, None
    info = load_support_matrix().get("workspace_subtabs", {}).get(normalized["subtab"])
    if not info:
        return UNSUPPORTED, "finish_mount_only"
    if normalized["subtab"] != WORKSPACE_APP:
        return info.get("state", UNSUPPORTED), info.get("reason_code", "finish_mount_only")
    return None, None


def support_for_route(route: dict[str, Any] | None = None, *, require_finish_subtab: bool = True, **overrides: Any) -> dict[str, Any]:
    normalized = normalize_route(route, **overrides)
    rows = _rows_by_key()
    row = rows.get((normalized["backend"], normalized["family"], normalized["loader"], normalized["mode"]))
    if row is None:
        if normalized["backend"] in SUPPORTED_COMFY_BACKENDS:
            state, reason_code = "available", "comfy_utility_graph_available"
        else:
            state, reason_code = PROVIDER_GATED, "non_comfy_provider_gated"
        row = {
            "backend": normalized["backend"],
            "family": normalized["family"],
            "loader": normalized["loader"],
            "workflow_mode": normalized["mode"],
            "state": state,
            "reason_code": reason_code,
            "normal_ui": "visible_selectable" if state in ACTIVE_ROUTE_STATES else "hidden",
            "diagnostic_ui": "visible_selectable" if state in ACTIVE_ROUTE_STATES else "visible_disabled",
            "queue_allowed": state in ACTIVE_ROUTE_STATES,
            "workflow_patch_allowed": False,
            "parameter_profile": "image_upscale_finish" if state in ACTIVE_ROUTE_STATES else "diagnostic_only",
            "required_nodes": list(REQUIRED_COMFY_NODES) if state in ACTIVE_ROUTE_STATES else [],
            "optional_node_groups": list(OPTIONAL_NODE_GROUPS) if state in ACTIVE_ROUTE_STATES else [],
            "family_restricted": False,
            "loader_restricted": False,
            "must_not_fallback": True,
        }
    state = row.get("state", PROVIDER_GATED)
    if state not in VALID_ROUTE_STATES:
        state = PROVIDER_GATED
    reason_code = row.get("reason_code") or "unknown_route"

    overlay_state, overlay_reason = _workspace_overlay(normalized, require_finish_subtab)
    if overlay_state:
        # P8.6: Image-tab subtab overlay may widen Comfy utility availability,
        # but it must never promote a non-Comfy/provider-gated backend.
        if normalized["backend"] in SUPPORTED_COMFY_BACKENDS or overlay_state == UNSUPPORTED:
            state = overlay_state
            reason_code = overlay_reason or reason_code

    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "route": normalized,
        "route_key": route_key(normalized),
        "state": state,
        "reason_code": reason_code,
        "reason": reason_text(reason_code, state),
        "normal_ui": "visible_selectable" if state in ACTIVE_ROUTE_STATES else ("hidden" if state == UNSUPPORTED else "hidden"),
        "diagnostic_ui": "visible_selectable" if state in ACTIVE_ROUTE_STATES else ("hidden" if state == UNSUPPORTED else "visible_disabled"),
        "queue_allowed": state in ACTIVE_ROUTE_STATES,
        "workflow_patch_allowed": False,
        "parameter_profile": "image_upscale_finish" if state in ACTIVE_ROUTE_STATES else ("hidden" if state == UNSUPPORTED else "diagnostic_only"),
        "required_nodes": list(row.get("required_nodes") or (REQUIRED_COMFY_NODES if state in ACTIVE_ROUTE_STATES else [])),
        "optional_node_groups": list(row.get("optional_node_groups") or (OPTIONAL_NODE_GROUPS if state in ACTIVE_ROUTE_STATES else [])),
        "family_restricted": False,
        "loader_restricted": False,
        "model_family_independent": True,
        "mode_independent": True,
        "must_not_fallback": True,
    }


def route_state(route: dict[str, Any] | None = None, **overrides: Any) -> str:
    return support_for_route(route, **overrides)["state"]


def route_reason(route: dict[str, Any] | None = None, **overrides: Any) -> str:
    return support_for_route(route, **overrides)["reason"]


def is_route_active(route: dict[str, Any] | None = None, **overrides: Any) -> bool:
    return route_state(route, **overrides) in ACTIVE_ROUTE_STATES


def node_gate_status(available_nodes: list[str] | set[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    nodes = {str(node) for node in (available_nodes or [])}
    missing_required = [node for node in REQUIRED_COMFY_NODES if node not in nodes]
    optional_groups: dict[str, dict[str, Any]] = {}
    for group, required in OPTIONAL_NODE_GROUPS.items():
        missing = [node for node in required if node not in nodes]
        if group == "model_upscale":
            missing_reason = "missing_optional_model_nodes"
        elif group == "codeformer_restore":
            missing_reason = "missing_optional_codeformer_nodes"
        elif group == "seedvr2_experimental":
            missing_reason = "missing_optional_seedvr2_nodes"
        elif group == "seedvr2_rgba":
            missing_reason = "missing_optional_seedvr2_rgba_nodes"
        else:
            missing_reason = "missing_required_nodes"
        optional_groups[group] = {
            "state": "available" if not missing else "provider_gated",
            "ready": not missing,
            "required_nodes": list(required),
            "missing_nodes": missing,
            "reason_code": None if not missing else missing_reason,
            "reason": "Optional path is available." if not missing else reason_text(missing_reason),
        }
    return {
        "extension_id": EXTENSION_ID,
        "required_ready": not missing_required,
        "state": "available" if not missing_required else PROVIDER_GATED,
        "missing_required_nodes": missing_required,
        "required_nodes": list(REQUIRED_COMFY_NODES),
        "optional_groups": optional_groups,
        "whole_extension_gated": bool(missing_required),
        "reason_code": None if not missing_required else "missing_required_nodes",
        "reason": "Required Image Upscale nodes are available." if not missing_required else reason_text("missing_required_nodes"),
    }


def support_with_nodes(route: dict[str, Any] | None = None, available_nodes: list[str] | set[str] | tuple[str, ...] | None = None, **overrides: Any) -> dict[str, Any]:
    support = support_for_route(route, **overrides)
    gate = node_gate_status(available_nodes)
    support["node_status"] = gate
    if support["state"] in ACTIVE_ROUTE_STATES and not gate["required_ready"]:
        support["state"] = PROVIDER_GATED
        support["reason_code"] = "missing_required_nodes"
        support["reason"] = reason_text("missing_required_nodes", PROVIDER_GATED)
        support["queue_allowed"] = False
        support["normal_ui"] = "hidden"
        support["diagnostic_ui"] = "visible_disabled"
        support["parameter_profile"] = "diagnostic_only"
    return support


def manifest_route_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for row in load_support_matrix().get("rows", []):
        states[f"{row['backend']}:{row['family']}:{row['loader']}:{row['workflow_mode']}:finish"] = row.get("state", PROVIDER_GATED)
    states["*:non_comfy:*:*:finish"] = PROVIDER_GATED
    states["*:image:*:*:non_finish"] = UNSUPPORTED
    return states


def support_summary() -> dict[str, Any]:
    data = load_support_matrix()
    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "source_phase": data.get("source_phase"),
        "support_basis": data.get("support_basis"),
        "state_counts": data.get("state_counts", {}),
        "route_count": len(data.get("rows", [])),
        "available_backends": list(SUPPORTED_COMFY_BACKENDS),
        "required_nodes": list(REQUIRED_COMFY_NODES),
        "optional_node_groups": {key: list(value) for key, value in OPTIONAL_NODE_GROUPS.items()},
        "family_restricted": False,
        "loader_restricted": False,
        "mode_restricted": False,
        "runtime_activation": False,
        "queue_route_activation": False,
        "workflow_graph_mutation": False,
    }
