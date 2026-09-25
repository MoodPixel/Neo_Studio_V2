from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.h3.vdn_capability.v1"

VDN_NODE_CANDIDATES: Final[tuple[str, ...]] = (
    "ApplyVDNH3",
    "ApplyVDNH3Advanced",
)
VDN_PRIMARY_NODE: Final[str] = "ApplyVDNH3"
VDN_ADVANCED_NODE: Final[str] = "ApplyVDNH3Advanced"

# The upstream ComfyUI port exposes stage directories through the
# `vdn_checkpoint` combo. Neo never guesses filesystem paths itself; the live
# /object_info catalog remains authoritative.
VDN_CHECKPOINT_FIELD: Final[str] = "vdn_checkpoint"


@dataclass(frozen=True)
class VDNStageInfo:
    name: str
    family: str
    precision: str
    recommended_steps: int | None
    turbo_adapter: bool
    int8_convrot: bool
    quality_mode: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _inputs(node: dict[str, Any] | None) -> dict[str, Any]:
    entry = node if isinstance(node, dict) else {}
    spec = entry.get("input") if isinstance(entry.get("input"), dict) else {}
    merged: dict[str, Any] = {}
    for group in ("required", "optional"):
        values = spec.get(group)
        if isinstance(values, dict):
            merged.update(values)
    return merged


def _combo_values(node: dict[str, Any] | None, field: str) -> list[str]:
    spec = _inputs(node).get(field)
    if isinstance(spec, (list, tuple)) and spec:
        values = spec[0]
        if isinstance(values, (list, tuple)):
            return [str(item) for item in values if str(item)]
    return []


def _actual_class(object_info: dict[str, Any] | None, expected: str) -> str:
    info = object_info if isinstance(object_info, dict) else {}
    folded = {str(key).casefold(): str(key) for key in info}
    return folded.get(expected.casefold(), "")


def classify_vdn_stage(name: str) -> VDNStageInfo:
    raw = str(name or "").strip()
    key = raw.casefold().replace("-", "_").replace(" ", "_")
    int8_convrot = "int8" in key and "convrot" in key

    if "stage_dmd" in key or "dmd" in key:
        family = "dmd"
        recommended_steps = 8
        turbo_adapter = True
        quality_mode = "fast"
    elif "stage_b" in key or "step_2000" in key:
        family = "stage_b"
        recommended_steps = 50
        turbo_adapter = False
        quality_mode = "quality"
    else:
        family = "unknown"
        recommended_steps = None
        turbo_adapter = False
        quality_mode = "custom"

    precision = "int8_convrot" if int8_convrot else "bf16_or_native"
    return VDNStageInfo(
        name=raw,
        family=family,
        precision=precision,
        recommended_steps=recommended_steps,
        turbo_adapter=turbo_adapter,
        int8_convrot=int8_convrot,
        quality_mode=quality_mode,
    )


def classify_h3_base_model(name: str) -> dict[str, Any]:
    raw = str(name or "").strip()
    key = raw.casefold().replace("-", "_").replace(" ", "_")
    suffix = raw.rsplit(".", 1)[-1].casefold() if "." in raw else ""
    return {
        "name": raw,
        "format": "gguf" if suffix == "gguf" else "safetensors" if suffix == "safetensors" else "unknown",
        "int8_convrot": "int8" in key and "convrot" in key,
        "pruned": "pruned" in key,
        "fl2va": any(token in key for token in ("fl2va", "fl2v")),
        "ref2va": any(token in key for token in ("ref2va", "ref2v")),
    }


def _preferred_stage(stages: list[VDNStageInfo]) -> str:
    # For consumer GPUs Neo prefers the released 8-step DMD route, and among
    # equivalent DMD stages prefers INT8 ConvRot. This is a recommendation only;
    # every live stage remains selectable.
    ranking = sorted(
        stages,
        key=lambda item: (
            0 if item.family == "dmd" and item.int8_convrot else
            1 if item.family == "dmd" else
            2 if item.family == "stage_b" else
            3,
            item.name.casefold(),
        ),
    )
    return ranking[0].name if ranking else ""


def discover_vdn_h3_capability(object_info: dict[str, Any] | None) -> dict[str, Any]:
    info = object_info if isinstance(object_info, dict) else {}
    primary = _actual_class(info, VDN_PRIMARY_NODE)
    advanced = _actual_class(info, VDN_ADVANCED_NODE)
    selected = primary or advanced
    node = info.get(selected, {}) if selected else {}
    inputs = _inputs(node)
    checkpoint_values = _combo_values(node, VDN_CHECKPOINT_FIELD)
    stages = [classify_vdn_stage(value) for value in checkpoint_values]

    controls = {
        name: name in inputs
        for name in (
            "vdn_checkpoint",
            "apply_turbo_adapter",
            "strength",
            "lora_mode",
            "branch_weights",
            "retain_buffers",
            "attention_backend",
            "verbose",
            "stage_b_strength",
            "turbo_strength",
            "window_radius",
            "window_chunk",
            "anchor_frames",
            "text_state",
            "linear_branch",
            "fast_kernels",
        )
    }

    ready = bool(selected and controls["vdn_checkpoint"] and checkpoint_values)
    warnings: list[str] = []
    if not selected:
        warnings.append("ComfyUI-VDN-H3 is not visible in live /object_info.")
    elif not controls["vdn_checkpoint"]:
        warnings.append("The discovered VDN node does not expose the expected vdn_checkpoint input.")
    elif not checkpoint_values:
        warnings.append("VDN node is installed but no stage directories are visible in its live vdn_checkpoint catalog.")

    if stages and not any(stage.int8_convrot for stage in stages):
        warnings.append("No INT8 ConvRot VDN stage is currently visible; BF16/native VDN stages remain available.")

    return {
        "schema_version": SCHEMA_VERSION,
        "installed": bool(selected),
        "ready": ready,
        "classes": {
            "selected": selected,
            "primary": primary,
            "advanced": advanced,
        },
        "controls": controls,
        "catalogs": {
            "vdn_checkpoints": checkpoint_values,
            "stages": [stage.payload() for stage in stages],
            "int8_convrot_stages": [stage.name for stage in stages if stage.int8_convrot],
            "dmd_stages": [stage.name for stage in stages if stage.family == "dmd"],
            "stage_b_stages": [stage.name for stage in stages if stage.family == "stage_b"],
        },
        "recommended": {
            "vdn_checkpoint": _preferred_stage(stages),
            "lora_mode": "merge",
            "branch_weights": "auto",
            "attention_backend": "grouped",
            "strength": 1.0,
            "fast_kernels": False,
        },
        "compatibility": {
            "h3_safetensors": True,
            "h3_int8_convrot": True,
            "h3_fl2va": True,
            "h3_ref2va": True,
            # Keep GGUF disabled until Neo has a tested topology proving the
            # VDN runtime patch behaves correctly on a quantized GGUF MODEL.
            "h3_gguf": False,
            "external_h3_speed_lora_with_dmd": False,
            "spectrum_stack": False,
            "block_cache_stack": False,
            "sage_stack": None,
        },
        "warnings": warnings,
    }


def validate_vdn_h3_selection(
    *,
    capability: dict[str, Any],
    base_model_name: str,
    vdn_checkpoint: str,
    apply_turbo_adapter: bool,
    external_speed_lora_count: int = 0,
) -> list[str]:
    errors: list[str] = []
    if not bool(capability.get("ready")):
        errors.append("VDN-H3 is not ready: install the compatible ComfyUI node and at least one VDN stage checkpoint.")
        return errors

    base = classify_h3_base_model(base_model_name)
    if base["format"] == "gguf":
        errors.append("Neo does not enable VDN-H3 on MiniMax H3 GGUF yet; use a safetensors/UNET H3 base until the GGUF topology is validated.")

    catalog = capability.get("catalogs") if isinstance(capability.get("catalogs"), dict) else {}
    live = {str(item).casefold(): str(item) for item in (catalog.get("vdn_checkpoints") or [])}
    selected = live.get(str(vdn_checkpoint or "").casefold())
    if not selected:
        errors.append("Selected VDN checkpoint is not present in the live vdn_checkpoint catalog.")
        return errors

    stage = classify_vdn_stage(selected)
    if stage.family == "dmd" and not apply_turbo_adapter:
        errors.append("VDN DMD stage requires apply_turbo_adapter=true for the released 8-step route.")
    if stage.family == "stage_b" and apply_turbo_adapter:
        errors.append("VDN Stage-B quality checkpoint should not enable the DMD/Turbo adapter.")
    if stage.family == "dmd" and external_speed_lora_count > 0:
        errors.append("VDN DMD includes its own Turbo adapter; external H3 speed/Turbo LoRAs must not be stacked with it.")
    return errors


def build_vdn_h3_patch_inputs(
    *,
    capability: dict[str, Any],
    vdn_checkpoint: str,
    apply_turbo_adapter: bool,
    strength: float = 1.0,
    branch_weights: str = "auto",
    attention_backend: str = "grouped",
    verbose: bool = False,
) -> dict[str, Any]:
    controls = capability.get("controls") if isinstance(capability.get("controls"), dict) else {}
    values: dict[str, Any] = {
        "vdn_checkpoint": vdn_checkpoint,
        "apply_turbo_adapter": bool(apply_turbo_adapter),
        "strength": max(0.0, min(2.0, float(strength))),
        "lora_mode": "merge",
        "branch_weights": branch_weights if branch_weights in {"auto", "stream", "cache_gpu"} else "auto",
        "retain_buffers": "auto",
        "attention_backend": attention_backend if attention_backend in {"grouped", "flex"} else "grouped",
        "verbose": bool(verbose),
    }
    return {key: value for key, value in values.items() if controls.get(key, False)}
