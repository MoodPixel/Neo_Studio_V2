"""LayerDiffuse extension capability registry.

Phase A scope:
- centralize supported/planned LayerDiffuse modes
- keep legacy mode IDs stable
- expose readiness/status metadata for UI, validation, and later workflow phases
- do not mutate Neo core workflows or execute ComfyUI directly
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "image.layerdiffuse"
CAPABILITY_REGISTRY_VERSION = "layerdiffuse-capability-registry-v1"

MODE_STATUS_READY = "ready"
MODE_STATUS_BLOCKED_WORKFLOW = "blocked_missing_verified_workflow"
MODE_STATUS_EXPERIMENTAL = "experimental_requires_validation"

SUPPORTED_WORKFLOWS = ["txt2img", "img2img"]
SUPPORTED_MODEL_FAMILIES = [
    "sdxl",
    "sd",
    "sd15",
    "sd1.5",
    "sd_1_5",
    "sd1_5",
    "sdxl_sd",
    "sdxl/sd",
    "sdxl_sd_family",
]
MODEL_FAMILY_ALIASES = {
    "sdxl_sd": "sdxl",
    "sdxl/sd": "sdxl",
    "sdxl_sd_family": "sdxl",
    "sdxl-base": "sdxl",
    "sdxl_base": "sdxl",
    "sd": "sd15",
    "sd15": "sd15",
    "sd1.5": "sd15",
    "sd_1_5": "sd15",
    "sd1_5": "sd15",
}
WARN_MODEL_FAMILIES = ["flux", "qwen", "qwen-image", "zimage", "unknown", ""]
SUPPORTED_OUTPUT_POLICIES = ["preview", "new_run", "append", "replace"]
SUPPORTED_DECODE_MODES = ["rgba", "split", "preview_only"]
SUPPORTED_SOURCE_TYPES = ["prompt", "selected_image", "upload", "previous_output"]

REQUIRED_NODE_NAMES = [
    "LayeredDiffusionApply",
    "LayeredDiffusionJointApply",
    "LayeredDiffusionCondApply",
    "LayeredDiffusionCondJointApply",
    "LayeredDiffusionDiffApply",
    "LayeredDiffusionDecode",
    "LayeredDiffusionDecodeRGBA",
    "LayeredDiffusionDecodeSplit",
]

# Keep the original extension modes intact and add planned upstream capability modes as blocked/experimental.
# Later phases must replace blocked template IDs with verified exported ComfyUI workflow mappings before execution.
MODE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "transparent_asset": {
        "label": "Transparent Asset",
        "description": "Prompt-driven transparent RGBA asset generation.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "transparent_asset_sdxl.json", "sd15": "transparent_asset_sd15.json"},
        "fallback_template": "transparent_asset_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "rgba",
        "requires_prompt": True,
        "requires": [],
        "source_policy": ["prompt"],
        "output_policy": ["new_run", "append", "preview", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl", "sd15"],
        "workflows": ["txt2img"],
        "outputs": ["rgba_image", "alpha_mask", "preview_image"],
        "executable": True,
    },
    "rgb_alpha_split": {
        "label": "RGB + Alpha Split",
        "description": "Prompt-driven transparent asset with RGB and alpha sidecars.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "transparent_asset_split_sdxl.json"},
        "fallback_template": "transparent_asset_split_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "split",
        "requires_prompt": True,
        "requires": [],
        "source_policy": ["prompt"],
        "output_policy": ["new_run", "append", "preview", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["txt2img"],
        "outputs": ["rgba_image", "rgb_image", "alpha_mask", "preview_image"],
        "executable": True,
    },
    "foreground_on_background": {
        "label": "Foreground on Background",
        "description": "Prompt + background image -> foreground layer designed for that scene.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "foreground_on_background_sdxl.json"},
        "fallback_template": "foreground_on_background_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "rgba",
        "requires_prompt": True,
        "requires": ["background_image_id"],
        "source_policy": ["prompt", "selected_image", "upload", "previous_output"],
        "output_policy": ["new_run", "append", "preview", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["txt2img", "img2img"],
        "outputs": ["rgba_image", "alpha_mask", "preview_image"],
        "executable": True,
    },
    "background_aware_blend": {
        "label": "Background-Aware Blend",
        "description": "Foreground + background -> coherent blended composite.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "background_aware_blend_sdxl.json"},
        "fallback_template": "background_aware_blend_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "preview_only",
        "requires_prompt": True,
        "requires": ["foreground_image_id", "background_image_id"],
        "source_policy": ["selected_image", "upload", "previous_output"],
        "output_policy": ["preview", "new_run", "append", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["txt2img", "img2img"],
        "outputs": ["blended_image", "preview_image", "alpha_mask"],
        "executable": True,
    },
    "extract_foreground": {
        "label": "Extract Foreground",
        "description": "Composite/source image + known background -> extracted RGBA foreground.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "extract_foreground_from_composite_sdxl.json"},
        "fallback_template": "extract_foreground_from_composite_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "split",
        "requires_prompt": False,
        "requires": ["source_image_id", "background_image_id"],
        "source_policy": ["selected_image", "upload", "previous_output"],
        "output_policy": ["preview", "new_run", "append", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["txt2img", "img2img"],
        "outputs": ["rgba_image", "rgb_image", "alpha_mask", "preview_image"],
        "executable": True,
    },
    "overlay_fx": {
        "label": "Transparent Overlay FX",
        "description": "Prompt-driven semi-transparent overlay assets for editing/compositing.",
        "status": MODE_STATUS_READY,
        "template": {"sdxl": "overlay_fx_transparent_sdxl.json"},
        "fallback_template": "overlay_fx_transparent_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "split",
        "requires_prompt": True,
        "requires": [],
        "source_policy": ["prompt"],
        "output_policy": ["new_run", "append", "preview", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["txt2img"],
        "outputs": ["rgba_image", "rgb_image", "alpha_mask", "preview_image"],
        "executable": True,
    },
    "extract_background": {
        "label": "Extract Background",
        "description": "Composite/source image + known foreground -> extracted clean background.",
        "status": MODE_STATUS_BLOCKED_WORKFLOW,
        "template": {},
        "fallback_template": "extract_background_from_composite_sdxl.json",
        "strategy": "sidecar_run",
        "decode_mode": "preview_only",
        "requires_prompt": False,
        "requires": ["source_image_id", "foreground_image_id"],
        "source_policy": ["selected_image", "upload", "previous_output"],
        "output_policy": ["preview", "new_run", "append", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["img2img"],
        "outputs": ["background_image", "preview_image"],
        "executable": False,
        "blocked_reason": "requires_verified_background_extraction_export",
    },
    "generate_fg_from_bg": {
        "label": "Generate FG from BG",
        "description": "Background image + prompt -> generated foreground and blended preview.",
        "status": MODE_STATUS_BLOCKED_WORKFLOW,
        "template": {},
        "fallback_template": "generate_foreground_from_background_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "rgba",
        "requires_prompt": True,
        "requires": ["background_image_id"],
        "source_policy": ["selected_image", "upload", "previous_output"],
        "output_policy": ["preview", "new_run", "append", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["img2img"],
        "outputs": ["rgba_image", "alpha_mask", "composited_image", "preview_image"],
        "executable": False,
        "blocked_reason": "requires_verified_generate_foreground_from_background_export",
    },
    "generate_bg_from_fg": {
        "label": "Generate BG from FG",
        "description": "Foreground image + prompt -> generated background and blended preview.",
        "status": MODE_STATUS_BLOCKED_WORKFLOW,
        "template": {},
        "fallback_template": "generate_background_from_foreground_sdxl.json",
        "strategy": "replace_workflow",
        "decode_mode": "preview_only",
        "requires_prompt": True,
        "requires": ["foreground_image_id"],
        "source_policy": ["selected_image", "upload", "previous_output"],
        "output_policy": ["preview", "new_run", "append", "replace"],
        "batch_policy": "force_1",
        "model_families": ["sdxl"],
        "workflows": ["img2img"],
        "outputs": ["background_image", "composited_image", "preview_image"],
        "executable": False,
        "blocked_reason": "requires_verified_generate_background_from_foreground_export",
    },
    "joint_bg_fg_blend_sd15": {
        "label": "Joint BG + FG + Blend (SD1.5)",
        "description": "SD1.5 joint generation route for background, foreground, and blended result.",
        "status": MODE_STATUS_EXPERIMENTAL,
        "template": {},
        "fallback_template": "joint_bg_fg_blend_sd15.json",
        "strategy": "replace_workflow",
        "decode_mode": "split",
        "requires_prompt": True,
        "requires": [],
        "source_policy": ["prompt"],
        "output_policy": ["preview", "new_run", "append"],
        "batch_policy": "blocked_until_batch_rule_defined",
        "model_families": ["sd15"],
        "workflows": ["txt2img"],
        "outputs": ["background_image", "rgba_image", "rgb_image", "alpha_mask", "composited_image", "preview_image"],
        "executable": False,
        "blocked_reason": "requires_sd15_joint_workflow_batch_policy_and_verified_export",
    },
}

MODE_ALIASES = {
    "transparent_rgba": "transparent_asset",
    "rgba": "transparent_asset",
    "transparent_asset_sdxl": "transparent_asset",
    "split": "rgb_alpha_split",
    "rgb_alpha": "rgb_alpha_split",
    "foreground_from_background": "generate_fg_from_bg",
    "background_from_foreground": "generate_bg_from_fg",
    "extract_bg": "extract_background",
    "joint_bg_fg_blend": "joint_bg_fg_blend_sd15",
}


def normalize_mode_id(mode: Any, default: str = "transparent_asset") -> str:
    key = str(mode or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = MODE_ALIASES.get(key, key)
    return key if key in MODE_CAPABILITIES else default


def get_mode_capability(mode: Any) -> dict[str, Any]:
    return deepcopy(MODE_CAPABILITIES[normalize_mode_id(mode)])


def mode_config_map() -> dict[str, dict[str, Any]]:
    return deepcopy(MODE_CAPABILITIES)


def mode_templates_map() -> dict[str, str]:
    return {mode: cfg.get("fallback_template") for mode, cfg in MODE_CAPABILITIES.items() if cfg.get("fallback_template")}


def mode_requirements_map() -> dict[str, dict[str, Any]]:
    return {
        mode: {
            "requires_prompt": bool(cfg.get("requires_prompt")),
            "required_images": list(cfg.get("requires") or []),
            "recommended_decode": cfg.get("decode_mode"),
            "outputs_expected": list(cfg.get("outputs") or []),
            "patch_strategy": cfg.get("strategy"),
            "status": cfg.get("status"),
            "blocked_reason": cfg.get("blocked_reason"),
            "model_families": list(cfg.get("model_families") or []),
            "workflows": list(cfg.get("workflows") or []),
            "batch_policy": cfg.get("batch_policy"),
            "executable": bool(cfg.get("executable")),
        }
        for mode, cfg in MODE_CAPABILITIES.items()
    }


def output_modes_map() -> dict[str, list[str]]:
    return {mode: list(cfg.get("outputs") or []) for mode, cfg in MODE_CAPABILITIES.items()}


def ui_mode_options() -> list[dict[str, Any]]:
    return [
        {
            "id": mode,
            "label": cfg.get("label", mode),
            "description": cfg.get("description", ""),
            "status": cfg.get("status"),
            "blocked_reason": cfg.get("blocked_reason"),
            "requires": list(cfg.get("requires") or []),
            "requires_prompt": bool(cfg.get("requires_prompt")),
            "decode_mode": cfg.get("decode_mode"),
            "batch_policy": cfg.get("batch_policy"),
            "model_families": list(cfg.get("model_families") or []),
            "workflows": list(cfg.get("workflows") or []),
        }
        for mode, cfg in MODE_CAPABILITIES.items()
    ]


def capability_registry_payload() -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "version": CAPABILITY_REGISTRY_VERSION,
        "modes": deepcopy(MODE_CAPABILITIES),
        "mode_aliases": dict(MODE_ALIASES),
        "supported_workflows": list(SUPPORTED_WORKFLOWS),
        "supported_model_families": list(SUPPORTED_MODEL_FAMILIES),
        "supported_source_types": list(SUPPORTED_SOURCE_TYPES),
        "supported_output_policies": list(SUPPORTED_OUTPUT_POLICIES),
        "supported_decode_modes": list(SUPPORTED_DECODE_MODES),
        "required_nodes": list(REQUIRED_NODE_NAMES),
        "hidden_mutations_allowed": False,
    }
