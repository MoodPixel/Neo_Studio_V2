from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.h3.gguf_lora_capability.v1"
GGUF_MODEL_LOADERS: Final[tuple[str, ...]] = (
    "UnetLoaderGGUF",
    "UNETLoaderGGUF",
    "UnetLoaderGGUFAdvanced",
)
MODEL_ONLY_LORA_LOADER: Final[str] = "LoraLoaderModelOnly"


def _inputs(node: dict[str, Any]) -> dict[str, Any]:
    spec = node.get("input") if isinstance(node.get("input"), dict) else {}
    merged: dict[str, Any] = {}
    for group in ("required", "optional"):
        values = spec.get(group)
        if isinstance(values, dict):
            merged.update(values)
    return merged


def _accepts_model(spec: Any) -> bool:
    if isinstance(spec, str):
        return spec.upper() == "MODEL"
    if isinstance(spec, (list, tuple)) and spec:
        first = spec[0]
        if isinstance(first, str):
            return first.upper() == "MODEL"
        if isinstance(first, (list, tuple)):
            return any(str(item).upper() == "MODEL" for item in first)
    return False


def _produces_model(node: dict[str, Any]) -> bool:
    outputs = node.get("output")
    if isinstance(outputs, str):
        outputs = [outputs]
    return isinstance(outputs, (list, tuple)) and any(str(item).upper() == "MODEL" for item in outputs)


def validate_h3_gguf_lora_topology(
    route_id: str,
    bindings: dict[str, Any] | None,
    object_info: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prove the live H3 GGUF MODEL -> ModelOnly LoRA contract.

    UNET routes are already validated by the earlier H3 gate. GGUF routes must
    never inherit that permission from family-level matching or fallback class
    names; both live node schemas are required.
    """
    route_id = str(route_id or "")
    loader = route_id.split(".")[1] if route_id.count(".") >= 2 else ""
    if loader != "gguf":
        return {
            "schema_version": SCHEMA_VERSION,
            "loader": loader,
            "validated": loader == "unet",
            "evidence": "prior_h3_unet_regression",
        }

    info = object_info if isinstance(object_info, dict) else {}
    classes = bindings.get("classes") if isinstance(bindings, dict) and isinstance(bindings.get("classes"), dict) else {}
    model_loader = str(classes.get("model_loader") or "")
    lora_loader = str(classes.get("lora") or "")

    if model_loader not in GGUF_MODEL_LOADERS or model_loader not in info:
        raise ValueError("MiniMax H3 GGUF LoRA requires a live supported GGUF model loader; fallback class names are not accepted.")
    if lora_loader != MODEL_ONLY_LORA_LOADER or lora_loader not in info:
        raise ValueError("MiniMax H3 GGUF LoRA requires live LoraLoaderModelOnly; generic LoraLoader is not accepted.")
    if not _produces_model(info[model_loader]):
        raise ValueError(f"MiniMax H3 GGUF loader {model_loader} does not advertise a MODEL output in /object_info.")
    if not _accepts_model(_inputs(info[lora_loader]).get("model")):
        raise ValueError("LoraLoaderModelOnly does not advertise a compatible MODEL input in /object_info.")

    return {
        "schema_version": SCHEMA_VERSION,
        "loader": "gguf",
        "validated": True,
        "evidence": "live_object_info_schema",
        "model_loader": model_loader,
        "model_output": "MODEL",
        "lora_loader": lora_loader,
        "lora_input": "MODEL",
        "generic_fallback_allowed": False,
        "object_info_snapshot": {
            "model_loader": deepcopy(info[model_loader]),
            "lora_loader": deepcopy(info[lora_loader]),
        },
    }
