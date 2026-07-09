from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from neo_app.video.gguf_dual_noise_mapping import resolve_wan22_dual_noise_mapping

PHASE: Final[str] = "V-G4"

GGUF_UNET_LOADER_CANDIDATES: Final[tuple[str, ...]] = (
    "UnetLoaderGGUF",
    "UNETLoaderGGUF",
    "GGUFModelLoader",
    "GGUFLoader",
    "WanVideoModelLoaderGGUF",
    "WanVideoModelLoaderGGUFAdvanced",
)
GGUF_MODEL_FIELD_CANDIDATES: Final[tuple[str, ...]] = (
    "unet_name",
    "gguf_name",
    "model_name",
    "ckpt_name",
    "model",
    "unet",
    "filename",
)
CLIP_LOADER_CANDIDATES: Final[tuple[str, ...]] = ("CLIPLoader",)
CLIP_FIELD_CANDIDATES: Final[tuple[str, ...]] = ("clip_name", "text_encoder_name", "clip_name1", "clip_l", "t5xxl_name")
VAE_LOADER_CANDIDATES: Final[tuple[str, ...]] = ("VAELoader", "VAELoaderGGUF", "VAELoaderKJ")
VAE_FIELD_CANDIDATES: Final[tuple[str, ...]] = ("vae_name", "model_name", "vae")
LORA_LOADER_CANDIDATES: Final[tuple[str, ...]] = ("LoraLoaderModelOnly", "LoraLoader")
LORA_FIELD_CANDIDATES: Final[tuple[str, ...]] = ("lora_name", "model_name", "lora")

_OPTIONAL_DEFAULT_FIELDS: Final[tuple[str, ...]] = (
    "weight_dtype",
    "dtype",
    "device",
    "load_device",
    "offload_device",
    "quantization",
)
_CONNECTION_TYPES: Final[set[str]] = {"MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "VIDEO"}


@dataclass(frozen=True)
class LoaderNodeSpec:
    class_type: str
    model_field: str
    model_name: str
    inputs: dict[str, Any]
    available_models: tuple[str, ...]
    warnings: tuple[str, ...]
    defaults_added: tuple[str, ...]

    def node(self, title: str) -> dict[str, Any]:
        return {"class_type": self.class_type, "inputs": dict(self.inputs), "_meta": {"title": title}}

    def payload(self) -> dict[str, Any]:
        return {
            "class_type": self.class_type,
            "model_field": self.model_field,
            "model_name": self.model_name,
            "inputs": dict(self.inputs),
            "available_model_count": len(self.available_models),
            "available_models": list(self.available_models),
            "warnings": list(self.warnings),
            "defaults_added": list(self.defaults_added),
        }


@dataclass(frozen=True)
class WanGgufLoaderAdapterPlan:
    gguf_loader: str
    gguf_model_field: str
    clip_loader: str
    clip_field: str
    vae_loader: str
    vae_field: str
    lora_loader: str
    lora_field: str
    high_noise_model: str
    low_noise_model: str
    clip_name: str
    vae_name: str
    high_noise_lora: str
    low_noise_lora: str
    available_models: dict[str, list[str]]
    diagnostics: tuple[str, ...]
    dual_noise_mapping: dict[str, Any]
    high_node: LoaderNodeSpec
    low_node: LoaderNodeSpec

    @property
    def ready(self) -> bool:
        return bool(self.gguf_loader and self.gguf_model_field)

    def payload(self) -> dict[str, Any]:
        return {
            "phase": PHASE,
            "ready": self.ready,
            "classes": {
                "gguf_loader": self.gguf_loader,
                "clip_loader": self.clip_loader,
                "vae_loader": self.vae_loader,
                "lora_loader": self.lora_loader,
            },
            "fields": {
                "gguf_model": self.gguf_model_field,
                "clip_name": self.clip_field,
                "vae_name": self.vae_field,
                "lora_name": self.lora_field,
            },
            "models": {
                "high_noise_model": self.high_noise_model,
                "low_noise_model": self.low_noise_model,
                "clip_name": self.clip_name,
                "vae_name": self.vae_name,
                "high_noise_lora": self.high_noise_lora,
                "low_noise_lora": self.low_noise_lora,
            },
            "available_model_counts": {key: len(value) for key, value in self.available_models.items()},
            "available_models": {key: list(value) for key, value in self.available_models.items()},
            "adapter": {
                "schema_version": "neo.video.gguf_loader_adapter.vg4",
                "phase": PHASE,
                "strategy": "object_info_schema_aware_gguf_model_loaders_with_forced_wan_clip_loader",
                "dual_noise_mapping": self.dual_noise_mapping,
                "roles": {
                    "high_noise": self.high_node.payload(),
                    "low_noise": self.low_node.payload(),
                },
                "diagnostics": list(self.diagnostics),
            },
        }


def _normalize_token(value: str) -> str:
    return str(value or "").casefold().replace(" ", "").replace("_", "").replace("-", "")


def _class_exists(object_info: dict[str, Any], *candidates: str) -> str | None:
    nodes = {str(key): str(key) for key in (object_info or {}).keys()}
    folded = {_normalize_token(key): value for key, value in nodes.items()}
    for candidate in candidates:
        found = folded.get(_normalize_token(candidate))
        if found:
            return found
    for candidate in candidates:
        needle = _normalize_token(candidate)
        fuzzy = next((node for node in nodes if needle and needle in _normalize_token(node)), None)
        if fuzzy:
            return fuzzy
    return None


def _node_entry(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    if not isinstance(object_info, dict):
        return {}
    entry = object_info.get(class_type, {})
    return entry if isinstance(entry, dict) else {}


def _input_group(object_info: dict[str, Any], class_type: str, group: str) -> dict[str, Any]:
    entry = _node_entry(object_info, class_type)
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    values = inputs.get(group, {}) if isinstance(inputs, dict) else {}
    return values if isinstance(values, dict) else {}


def _all_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    return {**_input_group(object_info, class_type, "required"), **_input_group(object_info, class_type, "optional")}


def _field_name(inputs: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {_normalize_token(key): str(key) for key in inputs.keys()}
    for candidate in candidates:
        found = folded.get(_normalize_token(candidate))
        if found:
            return found
    for candidate in candidates:
        needle = _normalize_token(candidate)
        fuzzy = next((str(key) for key in inputs if needle and needle in _normalize_token(str(key))), None)
        if fuzzy:
            return fuzzy
    return fallback


def _combo_values_from_spec(spec: Any) -> list[str]:
    if isinstance(spec, list) and spec:
        first = spec[0]
        if isinstance(first, list):
            return [str(item) for item in first]
    if isinstance(spec, dict):
        for key in ("values", "options", "choices"):
            values = spec.get(key)
            if isinstance(values, list):
                return [str(item) for item in values]
    return []


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    for inputs in (_input_group(object_info, class_type, "required"), _input_group(object_info, class_type, "optional")):
        values = _combo_values_from_spec(inputs.get(input_name))
        if values:
            return values
    return []


def _all_combo_values(object_info: dict[str, Any], class_type: str) -> list[str]:
    values: list[str] = []
    for inputs in (_input_group(object_info, class_type, "required"), _input_group(object_info, class_type, "optional")):
        for spec in inputs.values():
            for item in _combo_values_from_spec(spec):
                if item not in values:
                    values.append(item)
    return values


def _spec_default(spec: Any) -> Any:
    if isinstance(spec, list):
        if len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
            return spec[1]["default"]
        if spec and isinstance(spec[0], list) and spec[0]:
            return spec[0][0]
        if spec and isinstance(spec[0], str):
            kind = spec[0].upper()
            if kind in _CONNECTION_TYPES:
                return None
            if kind in {"BOOLEAN", "BOOL"}:
                return False
            if kind == "INT":
                return 0
            if kind == "FLOAT":
                return 0.0
            if kind in {"STRING", "TEXT"}:
                return ""
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        for key in ("values", "options", "choices"):
            values = spec.get(key)
            if isinstance(values, list) and values:
                return values[0]
    return None


def _first_matching(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def _discover_model_values(object_info: dict[str, Any], class_type: str, field_name: str) -> list[str]:
    values = _combo_values(object_info, class_type, field_name)
    if values:
        return values
    return _all_combo_values(object_info, class_type)


def _validate_model(role: str, model_name: str, available: list[str]) -> tuple[str, ...]:
    if not available:
        return (f"{role}: ComfyUI /object_info did not expose a GGUF model list; using supplied/fallback model name.",)
    if model_name not in available:
        return (f"{role}: selected GGUF model '{model_name}' is not in the loader dropdown list returned by ComfyUI.",)
    return ()


def build_loader_inputs(
    object_info: dict[str, Any],
    class_type: str,
    model_field: str,
    model_name: str,
    *,
    role: str,
) -> LoaderNodeSpec:
    required = _input_group(object_info, class_type, "required")
    optional = _input_group(object_info, class_type, "optional")
    inputs: dict[str, Any] = {model_field: model_name}
    defaults_added: list[str] = []
    warnings: list[str] = []

    for name, spec in required.items():
        if name == model_field:
            continue
        default = _spec_default(spec)
        if default is None:
            warnings.append(f"{role}: required GGUF loader input '{name}' has no safe default; node may need manual support.")
            continue
        inputs[name] = default
        defaults_added.append(name)

    for name in _OPTIONAL_DEFAULT_FIELDS:
        actual = _field_name(optional, (name,), "") if optional else ""
        if not actual or actual in inputs:
            continue
        default = _spec_default(optional.get(actual))
        if default is not None:
            inputs[actual] = default
            defaults_added.append(actual)

    available = _discover_model_values(object_info, class_type, model_field)
    warnings.extend(_validate_model(role, model_name, available))
    return LoaderNodeSpec(
        class_type=class_type,
        model_field=model_field,
        model_name=model_name,
        inputs=inputs,
        available_models=tuple(available),
        warnings=tuple(warnings),
        defaults_added=tuple(defaults_added),
    )


def build_wan22_gguf_loader_plan(
    object_info: dict[str, Any] | None,
    *,
    fallback_models: dict[str, str],
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
) -> WanGgufLoaderAdapterPlan:
    info = object_info or {}
    diagnostics: list[str] = []

    gguf_loader = _class_exists(info, *GGUF_UNET_LOADER_CANDIDATES) or GGUF_UNET_LOADER_CANDIDATES[0]
    clip_loader = _class_exists(info, *CLIP_LOADER_CANDIDATES) or "CLIPLoader"
    vae_loader = _class_exists(info, *VAE_LOADER_CANDIDATES) or "VAELoader"
    lora_loader = _class_exists(info, *LORA_LOADER_CANDIDATES) or "LoraLoaderModelOnly"

    gguf_inputs = _all_inputs(info, gguf_loader)
    clip_inputs = _all_inputs(info, clip_loader)
    vae_inputs = _all_inputs(info, vae_loader)
    lora_inputs = _all_inputs(info, lora_loader)

    gguf_field = _field_name(gguf_inputs, GGUF_MODEL_FIELD_CANDIDATES, "unet_name")
    clip_field = _field_name(clip_inputs, CLIP_FIELD_CANDIDATES, "clip_name")
    vae_field = _field_name(vae_inputs, VAE_FIELD_CANDIDATES, "vae_name")
    lora_field = _field_name(lora_inputs, LORA_FIELD_CANDIDATES, "lora_name")

    gguf_values = _discover_model_values(info, gguf_loader, gguf_field)
    clip_values = _discover_model_values(info, clip_loader, clip_field)
    vae_values = _discover_model_values(info, vae_loader, vae_field)
    lora_values = _discover_model_values(info, lora_loader, lora_field)

    if gguf_loader not in info:
        diagnostics.append("GGUF loader class was not present in /object_info; adapter used fallback class UnetLoaderGGUF.")
    if not gguf_values:
        diagnostics.append("GGUF loader was found but no visible GGUF model dropdown values were exposed by /object_info.")

    mapping = resolve_wan22_dual_noise_mapping(
        gguf_values,
        fallback_high_noise_model=fallback_models["high_noise"],
        fallback_low_noise_model=fallback_models["low_noise"],
        requested_high_noise_model=high_noise_model,
        requested_low_noise_model=low_noise_model,
    )
    selected_high = mapping.high_noise_model
    selected_low = mapping.low_noise_model
    selected_clip = clip_name or _first_matching(clip_values, ("umt5", "t5", "wan"), fallback_models["clip"])
    selected_vae = vae_name or _first_matching(vae_values, ("wan_2.1", "wan2.1", "wan_2.2", "wan2.2", "wan"), fallback_models["vae"])
    selected_high_lora = high_noise_lora or _first_matching(lora_values, ("high_noise", "high-noise", "highnoise", "lightx2v"), fallback_models["high_noise_lora"])
    selected_low_lora = low_noise_lora or _first_matching(lora_values, ("low_noise", "low-noise", "lownoise", "lightx2v"), fallback_models["low_noise_lora"])

    diagnostics.extend(mapping.diagnostics)
    high_node = build_loader_inputs(info, gguf_loader, gguf_field, selected_high, role="high_noise")
    low_node = build_loader_inputs(info, gguf_loader, gguf_field, selected_low, role="low_noise")
    diagnostics.extend(high_node.warnings)
    diagnostics.extend(low_node.warnings)

    return WanGgufLoaderAdapterPlan(
        gguf_loader=gguf_loader,
        gguf_model_field=gguf_field,
        clip_loader=clip_loader,
        clip_field=clip_field,
        vae_loader=vae_loader,
        vae_field=vae_field,
        lora_loader=lora_loader,
        lora_field=lora_field,
        high_noise_model=selected_high,
        low_noise_model=selected_low,
        clip_name=selected_clip,
        vae_name=selected_vae,
        high_noise_lora=selected_high_lora,
        low_noise_lora=selected_low_lora,
        available_models={"gguf": gguf_values, "clip": clip_values, "vae": vae_values, "lora": lora_values},
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        dual_noise_mapping=mapping.payload(),
        high_node=high_node,
        low_node=low_node,
    )
