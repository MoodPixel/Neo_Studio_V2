from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Final

PHASE: Final[str] = "V-G8"
SCHEMA_VERSION: Final[str] = "neo.video.video_lora_adapter.vg8"
LIGHTX2V_MODE: Final[str] = "lightx2v_4step"
NORMAL_MODE: Final[str] = "normal"
OFF_MODE: Final[str] = "off"

VALID_TARGETS: Final[set[str]] = {"both", "high", "low", "high_noise", "low_noise"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, fallback: float = 1.0) -> float:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_mode(mode: str | None, *, enable_lightx2v: bool = False, enable_video_lora: bool = False) -> str:
    key = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "lightx2v": LIGHTX2V_MODE,
        "lightning": LIGHTX2V_MODE,
        "lightning_fast": LIGHTX2V_MODE,
        "4step": LIGHTX2V_MODE,
        "4_step": LIGHTX2V_MODE,
        "lora": NORMAL_MODE,
        "video_lora": NORMAL_MODE,
        "standard": NORMAL_MODE,
        "regular": NORMAL_MODE,
        "none": OFF_MODE,
        "disabled": OFF_MODE,
        "false": OFF_MODE,
    }
    if enable_lightx2v:
        return LIGHTX2V_MODE
    if key in aliases:
        return aliases[key]
    if enable_video_lora:
        return NORMAL_MODE
    return OFF_MODE


def _normalize_target(value: str | None) -> str:
    key = str(value or "both").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in VALID_TARGETS:
        return "both"
    return "high" if key == "high_noise" else "low" if key == "low_noise" else key


def _model_visible(model_name: str, available: list[str]) -> bool:
    if not model_name or not available:
        return True
    visible = {str(item).casefold() for item in available}
    return model_name.casefold() in visible


def _first_matching(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def _input_groups(object_info: dict[str, Any], class_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    optional = inputs.get("optional", {}) if isinstance(inputs, dict) else {}
    return (required if isinstance(required, dict) else {}, optional if isinstance(optional, dict) else {})


def build_lora_node_inputs(
    object_info: dict[str, Any],
    class_type: str,
    lora_field: str,
    lora_name: str,
    model_link: list[Any],
    *,
    strength: float = 1.0,
) -> dict[str, Any]:
    """Build a model-only LoRA node payload across common Comfy LoRA node schemas."""
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    inputs: dict[str, Any] = {"model": model_link, lora_field: lora_name}
    strength_value = max(-2.0, min(_float(strength, 1.0), 2.0))
    for candidate in ("strength_model", "model_strength", "strength"):
        if candidate in known:
            inputs[candidate] = strength_value
            break
    if "strength_clip" in known:
        # We are intentionally not routing CLIP through this WAN video adapter; keep clip neutral.
        inputs["strength_clip"] = 0.0
    for name, spec in required.items():
        if name in inputs or name in {"clip", "conditioning"}:
            continue
        if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
            inputs[name] = spec[1]["default"]
        elif isinstance(spec, list) and spec and isinstance(spec[0], list) and spec[0]:
            inputs[name] = spec[0][0]
    return inputs


@dataclass(frozen=True)
class VideoLoraBranch:
    role: str
    node_id: str
    lora_name: str
    strength_model: float
    source_model_link: list[Any]
    output_model_link: list[Any]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VideoLoraAdapterPlan:
    enabled: bool
    mode: str
    target: str
    lora_loader: str
    lora_field: str
    branches: tuple[VideoLoraBranch, ...]
    sampling_overrides: dict[str, Any]
    selected: dict[str, Any]
    available_lora_count: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def queue_ready(self) -> bool:
        return self.enabled and not self.errors or not self.enabled

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": PHASE,
            "enabled": self.enabled,
            "mode": self.mode,
            "target": self.target,
            "lora_loader": self.lora_loader,
            "lora_field": self.lora_field,
            "branches": [branch.payload() for branch in self.branches],
            "sampling_overrides": dict(self.sampling_overrides),
            "selected": dict(self.selected),
            "available_lora_count": self.available_lora_count,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "queue_ready": self.queue_ready,
        }


def build_wan22_video_lora_plan(
    *,
    object_info: dict[str, Any] | None,
    adapter_bindings: dict[str, Any],
    enable_video_lora: bool = False,
    video_lora_mode: str | None = None,
    video_lora_model: str | None = None,
    video_lora_strength: float | None = None,
    video_lora_target: str | None = None,
    enable_lightx2v: bool = False,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
    high_noise_lora_strength: float | None = None,
    low_noise_lora_strength: float | None = None,
) -> VideoLoraAdapterPlan:
    info = object_info or {}
    classes = adapter_bindings.get("classes", {}) if isinstance(adapter_bindings, dict) else {}
    fields = adapter_bindings.get("fields", {}) if isinstance(adapter_bindings, dict) else {}
    models = adapter_bindings.get("models", {}) if isinstance(adapter_bindings, dict) else {}
    available_models = adapter_bindings.get("available_models", {}) if isinstance(adapter_bindings, dict) else {}
    lora_values = [str(item) for item in (available_models.get("lora", []) if isinstance(available_models, dict) else [])]
    lora_loader = str(classes.get("lora_loader") or "LoraLoaderModelOnly")
    lora_field = str(fields.get("lora_name") or "lora_name")
    mode = _normalize_mode(video_lora_mode, enable_lightx2v=enable_lightx2v, enable_video_lora=enable_video_lora)
    target = _normalize_target(video_lora_target)
    warnings: list[str] = []
    errors: list[str] = []
    branches: list[VideoLoraBranch] = []
    selected: dict[str, Any] = {
        "video_lora_model": video_lora_model or "",
        "video_lora_strength": _float(video_lora_strength, 0.8),
        "high_noise_lora": high_noise_lora or models.get("high_noise_lora", ""),
        "low_noise_lora": low_noise_lora or models.get("low_noise_lora", ""),
        "high_noise_lora_strength": _float(high_noise_lora_strength, 1.0),
        "low_noise_lora_strength": _float(low_noise_lora_strength, 1.0),
    }

    if mode == OFF_MODE:
        return VideoLoraAdapterPlan(False, OFF_MODE, target, lora_loader, lora_field, tuple(), {}, selected, len(lora_values), tuple(), tuple())

    if lora_loader not in info and info:
        errors.append(f"Video LoRA is enabled, but the LoRA loader class is not visible in /object_info: {lora_loader}")
    if not lora_values:
        warnings.append("ComfyUI /object_info did not expose a LoRA dropdown; Neo will compile with selected/fallback LoRA names.")

    if mode == LIGHTX2V_MODE:
        selected_high = str(selected["high_noise_lora"] or _first_matching(lora_values, ("high_noise", "high-noise", "highnoise", "lightx2v"), "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"))
        selected_low = str(selected["low_noise_lora"] or _first_matching(lora_values, ("low_noise", "low-noise", "lownoise", "lightx2v"), "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"))
        selected["high_noise_lora"] = selected_high
        selected["low_noise_lora"] = selected_low
        if lora_values and not _model_visible(selected_high, lora_values):
            errors.append(f"Selected high-noise LightX2V LoRA is not visible to ComfyUI: {selected_high}")
        if lora_values and not _model_visible(selected_low, lora_values):
            errors.append(f"Selected low-noise LightX2V LoRA is not visible to ComfyUI: {selected_low}")
        branches.extend([
            VideoLoraBranch("high_noise", "129:101", selected_high, _float(selected["high_noise_lora_strength"], 1.0), ["129:95", 0], ["129:101", 0]),
            VideoLoraBranch("low_noise", "129:102", selected_low, _float(selected["low_noise_lora_strength"], 1.0), ["129:96", 0], ["129:102", 0]),
        ])
        return VideoLoraAdapterPlan(
            True,
            LIGHTX2V_MODE,
            "both",
            lora_loader,
            lora_field,
            tuple(branches),
            {"steps": 4, "guidance": 1.0, "split_step": 2, "reason": "LightX2V 4-step mode"},
            selected,
            len(lora_values),
            tuple(dict.fromkeys(warnings)),
            tuple(dict.fromkeys(errors)),
        )

    selected_model = str(video_lora_model or _first_matching(lora_values, ("wan", "video", "motion", "lora"), ""))
    selected["video_lora_model"] = selected_model
    if not selected_model:
        errors.append("Normal Video LoRA is enabled, but no video_lora_model was selected.")
    if lora_values and selected_model and not _model_visible(selected_model, lora_values):
        errors.append(f"Selected Video LoRA is not visible to ComfyUI: {selected_model}")
    strength = _float(video_lora_strength, 0.8)
    if target in {"both", "high"}:
        branches.append(VideoLoraBranch("high_noise", "9001", selected_model, strength, ["129:95", 0], ["9001", 0]))
    if target in {"both", "low"}:
        branches.append(VideoLoraBranch("low_noise", "9002", selected_model, strength, ["129:96", 0], ["9002", 0]))
    return VideoLoraAdapterPlan(True, NORMAL_MODE, target, lora_loader, lora_field, tuple(branches), {}, selected, len(lora_values), tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(errors)))
