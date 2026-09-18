from __future__ import annotations

from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.route_lora_capability.v1"
GGUF_LOADERS: Final[tuple[str, ...]] = ("UnetLoaderGGUF", "UNETLoaderGGUF", "UnetLoaderGGUFAdvanced")
MODEL_ONLY_LOADER: Final[str] = "LoraLoaderModelOnly"


def _inputs(node: dict[str, Any]) -> dict[str, Any]:
    root = node.get("input") if isinstance(node.get("input"), dict) else {}
    result: dict[str, Any] = {}
    for key in ("required", "optional"):
        if isinstance(root.get(key), dict):
            result.update(root[key])
    return result


def _model_socket(spec: Any) -> bool:
    if isinstance(spec, str):
        return spec.upper() == "MODEL"
    return isinstance(spec, (list, tuple)) and bool(spec) and str(spec[0]).upper() == "MODEL"


def validate_ltx_route_lora_topology(
    route_id: str,
    bindings: dict[str, Any] | None,
    object_info: dict[str, Any] | None,
) -> dict[str, Any]:
    route_id = str(route_id or "")
    parts = route_id.split(".")
    loader = parts[1] if len(parts) > 2 else ""
    mode = parts[2] if len(parts) > 2 else ""
    if loader not in {"unet", "gguf"}:
        raise ValueError(f"LTX Video LoRA has no compiler-owned topology for loader {loader or '<unknown>'!r}.")
    info = object_info if isinstance(object_info, dict) else {}
    classes = bindings.get("classes") if isinstance(bindings, dict) and isinstance(bindings.get("classes"), dict) else {}
    lora_class = next((str(key) for key in info if str(key).casefold() == MODEL_ONLY_LOADER.casefold()), "")
    if lora_class != MODEL_ONLY_LOADER or not _model_socket(_inputs(info.get(lora_class, {})).get("model")):
        raise ValueError("LTX Video LoRA requires live LoraLoaderModelOnly with a MODEL input; generic fallback is forbidden.")
    model_loader = str(classes.get("model_loader") or "")
    evidence = "compiler_anchor_and_live_model_only"
    if loader == "gguf":
        if model_loader not in GGUF_LOADERS or model_loader not in info:
            raise ValueError("LTX GGUF LoRA requires a live supported GGUF model loader; fallback or non-GGUF loader classes are rejected.")
        outputs = info[model_loader].get("output")
        if isinstance(outputs, str):
            outputs = [outputs]
        if not isinstance(outputs, (list, tuple)) or not any(str(value).upper() == "MODEL" for value in outputs):
            raise ValueError(f"LTX GGUF loader {model_loader} does not advertise a MODEL output in /object_info.")
        evidence = "live_gguf_model_to_model_only_schema"
    return {
        "schema_version": SCHEMA_VERSION,
        "route_id": route_id,
        "loader": loader,
        "mode": mode,
        "validated": True,
        "evidence": evidence,
        "model_loader": model_loader,
        "lora_loader": MODEL_ONLY_LOADER,
        "generic_fallback_allowed": False,
    }
