from __future__ import annotations

from typing import Any, Mapping

GENERIC_MTMD_HANDLER_ID = "__neo_generic_mtmd__"
GENERIC_MTMD_HANDLER_LABEL = "Generic MTMD (Neo)"


def _fold(value: str) -> str:
    return str(value or "").casefold().replace("_", "-")


def infer_model_family(model: str) -> str:
    """Best-effort family hint used only for safe handler routing.

    The runtime does not pretend filename inference is authoritative model
    metadata. It is deliberately conservative: known dedicated families map to
    their dedicated handler preferences; ToriiGate/Qwen2-VL maps to generic
    MTMD because the third-party Comfy wrapper does not expose a Qwen2-VL
    handler.
    """
    folded = _fold(model)
    if "toriigate" in folded:
        return "qwen2-vl"
    if ("qwen2-vl" in folded or "qwen2vl" in folded) and "qwen2.5" not in folded and "qwen25" not in folded:
        return "qwen2-vl"
    if "qwen3" in folded and "vl" in folded:
        return "qwen3-vl"
    if ("qwen2.5" in folded or "qwen25" in folded) and "vl" in folded:
        return "qwen2.5-vl"
    if "minicpm" in folded and "4.6" in folded:
        return "minicpm-v4.6"
    if "minicpm" in folded and "4.5" in folded:
        return "minicpm-v4.5"
    if "minicpm" in folded:
        return "minicpm"
    if "glm-4.6v" in folded or "glm4.6v" in folded:
        return "glm-4.6v"
    if "glm-4.1v" in folded:
        return "glm-4.1v"
    if "lfm2.5" in folded:
        return "lfm2.5-vl"
    if "lfm2" in folded:
        return "lfm2-vl"
    if "step3" in folded:
        return "step3-vl"
    if "gemma4" in folded or "gemma-4" in folded:
        return "gemma4"
    if "gemma3" in folded or "gemma-3" in folded:
        return "gemma3"
    if "moondream" in folded:
        return "moondream2"
    if "nano" in folded and "llava" in folded:
        return "nanollava"
    if "llava" in folded:
        return "llava"
    return "unknown"



def vision_model_score(name: str) -> int:
    folded = _fold(name)
    family = infer_model_family(name)
    score = 0
    if family != "unknown":
        score += 20
    if "vl" in folded:
        score += 4
    if "vision" in folded:
        score += 4
    if "toriigate" in folded:
        score += 12
    return score


def pick_auto_vision_model(models: list[str] | tuple[str, ...]) -> str:
    values = [str(v).strip() for v in models if str(v).strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    ranked = sorted(((vision_model_score(name), index, name) for index, name in enumerate(values)), key=lambda item: (-item[0], item[1]))
    if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][2]
    return ""


def handler_preferences_for_model(model: str) -> list[str]:
    family = infer_model_family(model)
    return {
        "qwen3-vl": ["Qwen3-VL", "Qwen3-VL-Thinking"],
        "qwen2.5-vl": ["Qwen2.5-VL"],
        "minicpm-v4.6": ["MiniCPM-v4.6", "MiniCPM-v4.6-Thinking"],
        "minicpm-v4.5": ["MiniCPM-v4.5", "MiniCPM-v4.5-Thinking"],
        "minicpm": ["MiniCPM-v2.6", "MiniCPM-v4.5", "MiniCPM-v4.6"],
        "glm-4.6v": ["GLM-4.6V", "GLM-4.6V-Thinking"],
        "glm-4.1v": ["GLM-4.1V-Thinking"],
        "lfm2.5-vl": ["LFM2.5-VL"],
        "lfm2-vl": ["LFM2-VL"],
        "step3-vl": ["Step3-VL"],
        "gemma4": ["Gemma4"],
        "gemma3": ["Gemma3"],
        "moondream2": ["Moondream2"],
        "nanollava": ["nanoLLaVA"],
        "llava": ["LLaVA-1.6", "LLaVA-1.5", "llama3-Vision-Alpha"],
        # qwen2-vl intentionally has no dedicated handler in the currently
        # supported third-party Comfy wrapper. Generic MTMD is preferred.
        "qwen2-vl": [],
    }.get(family, [])


def resolve_caption_route(
    *,
    model: str,
    available_handlers: list[str] | tuple[str, ...],
    explicit_handler: str = "",
    generic_mtmd_available: bool = False,
) -> dict[str, Any]:
    """Resolve a caption handler without unsafe one-handler guessing."""
    handlers = [str(v).strip() for v in available_handlers if str(v).strip()]
    usable = [v for v in handlers if v.casefold() not in {"none", "null", "off"}]
    explicit = str(explicit_handler or "").strip()
    family = infer_model_family(model)

    if explicit:
        if explicit not in handlers:
            return {
                "mode": "blocked",
                "ready": False,
                "family": family,
                "handler": explicit,
                "label": explicit,
                "reason": "selected_handler_missing",
            }
        return {
            "mode": "dedicated",
            "ready": True,
            "family": family,
            "handler": explicit,
            "label": explicit,
            "reason": "explicit_handler",
        }

    for preference in handler_preferences_for_model(model):
        if preference in usable:
            return {
                "mode": "dedicated",
                "ready": True,
                "family": family,
                "handler": preference,
                "label": f"Auto → {preference}",
                "reason": "dedicated_match",
            }

    if generic_mtmd_available:
        return {
            "mode": "generic_mtmd",
            "ready": True,
            "family": family,
            "handler": GENERIC_MTMD_HANDLER_ID,
            "label": "Auto → Generic MTMD",
            "reason": "generic_mtmd_fallback",
        }

    return {
        "mode": "blocked",
        "ready": False,
        "family": family,
        "handler": "",
        "label": "No compatible vision route",
        "reason": "no_compatible_handler",
    }


__all__ = [
    "GENERIC_MTMD_HANDLER_ID",
    "GENERIC_MTMD_HANDLER_LABEL",
    "handler_preferences_for_model",
    "infer_model_family",
    "resolve_caption_route",
    "pick_auto_vision_model",
    "vision_model_score",
]
