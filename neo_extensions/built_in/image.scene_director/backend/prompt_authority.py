"""Shared prompt-authority rules for the V054 Scene Director route.

The UI, payload bridge, workflow patcher, and Comfy node must agree on who owns
the canvas prompt.  Keeping this contract in a small dependency-free module
also makes legacy payload normalization and regression tests deterministic.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

PROMPT_AUTHORITY_GLOBAL_CONTEXT = "global_context"
PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY = "scene_director_only"
PROMPT_AUTHORITIES = {
    PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
}

_ALIASES = {
    "global": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "global_and_scene_director": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "global_context_plus_scene_director": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "global_prompt": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "context": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "balanced": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "default": PROMPT_AUTHORITY_GLOBAL_CONTEXT,
    "scene": PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    "scene_director": PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    "scene_only": PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    "regional_only": PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    "local_only": PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clamp_float(value: Any, default: float = 0.35) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(0.0, min(2.0, parsed))


def normalize_prompt_authority(value: Any, default: str = PROMPT_AUTHORITY_GLOBAL_CONTEXT) -> str:
    """Return one of the two supported prompt ownership modes."""

    fallback = default if default in PROMPT_AUTHORITIES else PROMPT_AUTHORITY_GLOBAL_CONTEXT
    raw = _text(value).lower().replace("-", "_").replace(" ", "_")
    normalized = _ALIASES.get(raw, raw)
    return normalized if normalized in PROMPT_AUTHORITIES else fallback


def prompt_authority_label(value: Any) -> str:
    mode = normalize_prompt_authority(value)
    if mode == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        return "Scene Director only"
    return "Global context + Scene Director structure"


def compact_prompt_context(value: Any, max_chars: int = 420, max_items: int = 18) -> str:
    """Make a short, deduplicated context suffix for regional branches."""

    text = _text(value)
    if not text:
        return ""
    chunks: list[str] = []
    seen: set[str] = set()
    for raw_chunk in text.replace("\n", ",").replace(";", ",").split(","):
        chunk = " ".join(raw_chunk.split()).strip()
        if not chunk:
            continue
        key = chunk.casefold()
        if key in seen:
            continue
        seen.add(key)
        chunks.append(chunk)
        if len(chunks) >= max_items:
            break
    compact = ", ".join(chunks) or " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    clipped = compact[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return clipped or compact[:max_chars].rstrip()


def build_prompt_authority_contract(
    raw: Mapping[str, Any] | None = None,
    *,
    global_positive: Any = "",
    global_negative: Any = "",
    style_positive: Any = "",
    region_context_weight: Any = None,
) -> dict[str, Any]:
    """Build the canonical contract consumed by every V054 execution path."""

    source = raw if isinstance(raw, Mapping) else {}
    mode = normalize_prompt_authority(
        source.get("prompt_authority") or source.get("scene_director_prompt_authority")
    )
    routing = source.get("global_context_routing") if isinstance(source.get("global_context_routing"), Mapping) else {}
    region_context = source.get("region_context") if isinstance(source.get("region_context"), Mapping) else {}
    positive_allowed = routing.get("positive", source.get("global_context_route_positive", True)) is not False
    negative_allowed = routing.get("negative", source.get("global_context_route_negative", True)) is not False
    style_allowed = routing.get("style", source.get("global_context_route_style", True)) is not False
    configured_weight = region_context.get("weight") if region_context.get("weight") is not None else region_context_weight
    weight = _clamp_float(configured_weight, 0.35)
    requested_mode = _text(region_context.get("mode") or source.get("region_context_mode") or "global_and_style").lower()
    if requested_mode not in {"off", "global_only", "style_only", "global_and_style"}:
        requested_mode = "global_and_style"

    positive_context = _text(global_positive) if positive_allowed else ""
    style_context = _text(style_positive) if style_allowed else ""
    if requested_mode == "global_only":
        regional_context = positive_context
    elif requested_mode == "style_only":
        regional_context = style_context
    elif requested_mode == "off":
        regional_context = ""
    else:
        regional_context = ", ".join(item for item in (positive_context, style_context) if item)
    regional_context = compact_prompt_context(regional_context)
    enabled = mode == PROMPT_AUTHORITY_GLOBAL_CONTEXT and bool(region_context.get("enabled", True)) and bool(regional_context)
    if requested_mode == "off":
        enabled = False

    return {
        "schema": "neo.image.scene_director.prompt_authority.v1",
        "mode": mode,
        "label": prompt_authority_label(mode),
        "global_context_enabled": mode == PROMPT_AUTHORITY_GLOBAL_CONTEXT,
        "global_prompt_excluded": mode == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
        "regional_context_enabled": enabled,
        "regional_context_mode": requested_mode,
        "regional_context_weight": weight,
        "regional_context_position": "suffix",
        "regional_context": regional_context if enabled else "",
        "negative_context_enabled": mode == PROMPT_AUTHORITY_GLOBAL_CONTEXT and negative_allowed,
        "source": "neo_core_prompts",
    }


def apply_prompt_authority_to_scene_graph(scene_graph: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """Annotate a V054 graph and suppress global conditioning when requested."""

    graph = deepcopy(dict(scene_graph)) if isinstance(scene_graph, Mapping) else {}
    global_data = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    global_data = deepcopy(global_data)
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    metadata = deepcopy(metadata)
    normalized_contract = dict(contract) if isinstance(contract, Mapping) else build_prompt_authority_contract()
    mode = normalize_prompt_authority(normalized_contract.get("mode"))
    normalized_contract["mode"] = mode
    normalized_contract["label"] = prompt_authority_label(mode)
    normalized_contract["global_context_enabled"] = mode == PROMPT_AUTHORITY_GLOBAL_CONTEXT
    normalized_contract["global_prompt_excluded"] = mode == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY

    if mode == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        global_data["prompt"] = ""
        global_data["negative"] = ""
        global_data["regional_context"] = ""
        global_data["regional_context_enabled"] = False
        metadata["global_prompt_excluded"] = True
    else:
        explicit_context = _text(global_data.get("regional_context"))
        global_data["regional_context"] = explicit_context or _text(normalized_contract.get("regional_context"))
        global_data["regional_context_enabled"] = bool(
            normalized_contract.get("regional_context_enabled") and global_data["regional_context"]
        )
        global_data["regional_context_weight"] = _clamp_float(
            global_data.get("regional_context_weight"),
            _clamp_float(normalized_contract.get("regional_context_weight"), 0.35),
        )
        metadata["global_prompt_excluded"] = False

    global_data["prompt_authority"] = mode
    metadata["prompt_authority"] = mode
    metadata["prompt_authority_contract"] = deepcopy(normalized_contract)
    graph["global"] = global_data
    graph["metadata"] = metadata
    return graph

