from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

PHASE: Final[str] = "V-G11"
SCHEMA_VERSION: Final[str] = "neo.video.sage_attention_adapter.vg11"

SAGE_NODE_ALIASES: Final[tuple[str, ...]] = (
    "PathchSageAttentionKJ",
    "PatchSageAttentionKJ",
    "Patch Sage Attention KJ",
    "SageAttentionKJ",
)
SAGE_MODE_FIELDS: Final[tuple[str, ...]] = ("sage_attention", "mode", "attention_mode", "sage_mode")
MODEL_FIELDS: Final[tuple[str, ...]] = ("model",)
VALID_TARGETS: Final[set[str]] = {"both", "high", "low", "high_noise", "low_noise"}
PREFERRED_SAGE_MODES: Final[tuple[str, ...]] = (
    "sageattn_qk_int8_pv_fp16_triton",
    "sageattn_qk_int8_pv_fp16_cuda",
    "sageattn_qk_int8_pv_fp8_cuda",
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_target(value: str | None) -> str:
    key = str(value or "both").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in VALID_TARGETS:
        return "both"
    return "high" if key == "high_noise" else "low" if key == "low_noise" else key


def _input_groups(object_info: dict[str, Any], class_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    optional = inputs.get("optional", {}) if isinstance(inputs, dict) else {}
    return (required if isinstance(required, dict) else {}, optional if isinstance(optional, dict) else {})


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    required, optional = _input_groups(object_info, class_type)
    for group in (required, optional):
        spec = group.get(input_name)
        if isinstance(spec, list) and spec:
            values = spec[0]
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _first_default(spec: Any) -> Any:
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
        return spec[1]["default"]
    if isinstance(spec, list) and spec and isinstance(spec[0], list) and spec[0]:
        return spec[0][0]
    if isinstance(spec, list) and spec and isinstance(spec[0], str):
        # Comfy type-only specs such as ["MODEL"] should not become values.
        return None
    return None


def _find_node_class(object_info: dict[str, Any]) -> str:
    if not isinstance(object_info, dict) or not object_info:
        return "PathchSageAttentionKJ"
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for alias in SAGE_NODE_ALIASES:
        exact = folded.get(alias.casefold())
        if exact:
            return exact
    normalized = {str(key).casefold().replace(" ", "").replace("_", ""): str(key) for key in object_info.keys()}
    for alias in SAGE_NODE_ALIASES:
        hit = normalized.get(alias.casefold().replace(" ", "").replace("_", ""))
        if hit:
            return hit
    return ""


def _find_input_field(object_info: dict[str, Any], class_type: str, candidates: tuple[str, ...], fallback: str) -> str:
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    folded = {str(key).casefold(): str(key) for key in known.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _available_modes(object_info: dict[str, Any], class_type: str, mode_field: str) -> list[str]:
    values = _combo_values(object_info, class_type, mode_field)
    if values:
        return values
    # Try common aliases when the selected field was a fallback.
    for field in SAGE_MODE_FIELDS:
        if field == mode_field:
            continue
        values = _combo_values(object_info, class_type, field)
        if values:
            return values
    return []


def _select_mode(requested: str | None, available: list[str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    key = str(requested or "auto").strip()
    if not key or key.lower() == "auto":
        if available:
            for preferred in PREFERRED_SAGE_MODES:
                if preferred in available:
                    return preferred, warnings
            return available[0], warnings
        return PREFERRED_SAGE_MODES[0], warnings
    if available and key not in available:
        warnings.append(f"Selected Sage Attention mode is not listed by ComfyUI /object_info: {key}")
    return key, warnings


def build_sage_attention_node_inputs(
    object_info: dict[str, Any],
    class_type: str,
    *,
    model_link: list[Any],
    mode: str,
    model_field: str = "model",
    mode_field: str = "sage_attention",
) -> dict[str, Any]:
    required, optional = _input_groups(object_info, class_type)
    known = {**required, **optional}
    inputs: dict[str, Any] = {model_field: model_link, mode_field: mode}
    for name, spec in required.items():
        if name in inputs:
            continue
        default = _first_default(spec)
        if default is not None:
            inputs[name] = default
    return inputs


@dataclass(frozen=True)
class SageAttentionBranch:
    role: str
    node_id: str
    source_model_link: list[Any]
    output_model_link: list[Any]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SageAttentionAdapterPlan:
    enabled: bool
    class_type: str
    model_field: str
    mode_field: str
    mode: str
    target: str
    available_modes: tuple[str, ...]
    branches: tuple[SageAttentionBranch, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def queue_ready(self) -> bool:
        return (not self.enabled) or not self.errors

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": PHASE,
            "enabled": self.enabled,
            "class_type": self.class_type,
            "model_field": self.model_field,
            "mode_field": self.mode_field,
            "mode": self.mode,
            "target": self.target,
            "available_modes": list(self.available_modes),
            "branches": [branch.payload() for branch in self.branches],
            "graph_mutations": [
                {
                    "type": "insert_model_patch",
                    "adapter": "sage_attention_kj",
                    "role": branch.role,
                    "node_id": branch.node_id,
                    "source_model_link": branch.source_model_link,
                    "output_model_link": branch.output_model_link,
                }
                for branch in self.branches
            ],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "queue_ready": self.queue_ready,
            "rules": [
                "Sage Attention is optional and is inserted after GGUF/Video-LoRA model patching, before ModelSamplingSD3.",
                "WAN dual-noise routes need separate Sage nodes for the high-noise and low-noise model branches when target=both.",
            ],
        }


def build_wan22_sage_attention_plan(
    *,
    object_info: dict[str, Any] | None,
    enable_sage_attention: bool = False,
    sage_attention_mode: str | None = "auto",
    sage_attention_target: str | None = "both",
    high_model_link: list[Any] | None = None,
    low_model_link: list[Any] | None = None,
) -> SageAttentionAdapterPlan:
    info = object_info or {}
    enabled = _as_bool(enable_sage_attention)
    target = _normalize_target(sage_attention_target)
    class_type = _find_node_class(info)
    model_field = _find_input_field(info, class_type, MODEL_FIELDS, "model") if class_type else "model"
    mode_field = _find_input_field(info, class_type, SAGE_MODE_FIELDS, "sage_attention") if class_type else "sage_attention"
    available = _available_modes(info, class_type, mode_field) if class_type else []
    mode, mode_warnings = _select_mode(sage_attention_mode, available)
    warnings: list[str] = list(mode_warnings)
    errors: list[str] = []
    branches: list[SageAttentionBranch] = []

    if not enabled:
        return SageAttentionAdapterPlan(False, class_type or "", model_field, mode_field, mode, target, tuple(available), tuple(), tuple(), tuple())

    if not class_type or (info and class_type not in info):
        errors.append("Sage Attention is enabled, but the KJ Sage Attention node is not visible in ComfyUI /object_info.")
    if info and available and mode not in available:
        errors.append(f"Selected Sage Attention mode is not available in this Comfy install: {mode}")
    if not available:
        warnings.append("ComfyUI /object_info did not expose Sage Attention mode values; Neo will compile with the selected/default mode.")
    warnings.append("Sage Attention is experimental; if output is black or unstable, disable it and retest the base GGUF route.")

    high_link = list(high_model_link or ["129:95", 0])
    low_link = list(low_model_link or ["129:96", 0])
    if target in {"both", "high"}:
        branches.append(SageAttentionBranch("high_noise", "9101", high_link, ["9101", 0]))
    if target in {"both", "low"}:
        branches.append(SageAttentionBranch("low_noise", "9102", low_link, ["9102", 0]))

    if not branches:
        errors.append("Sage Attention target did not resolve to any WAN model branch.")

    return SageAttentionAdapterPlan(
        True,
        class_type or "PathchSageAttentionKJ",
        model_field,
        mode_field,
        mode,
        target,
        tuple(available),
        tuple(branches),
        tuple(dict.fromkeys(warnings)),
        tuple(dict.fromkeys(errors)),
    )
