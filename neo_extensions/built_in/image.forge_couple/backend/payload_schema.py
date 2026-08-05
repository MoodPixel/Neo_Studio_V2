from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .advanced import normalize_mapping
from .constants import (
    BACKGROUNDS,
    COMMON_PARSERS,
    DEFAULT_PARAMS,
    DIRECTIONS,
    EXTENSION_ID,
    PHASE,
    REGION_MODES,
    VERSION,
)
from .mask import compile_mask_mapping, normalize_mask_mapping
from .tile import normalize_tile_params


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _extract_block(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    if isinstance(raw.get(EXTENSION_ID), Mapping):
        return dict(raw[EXTENSION_ID])
    for key in ("payloads", "extensions"):
        nested = raw.get(key)
        if isinstance(nested, Mapping) and isinstance(nested.get(EXTENSION_ID), Mapping):
            return dict(nested[EXTENSION_ID])
    return dict(raw)


def default_block() -> dict[str, Any]:
    return {
        "enabled": False,
        "version": VERSION,
        "inputs": {},
        "params": deepcopy(DEFAULT_PARAMS),
        "assets": {},
        "metadata": {
            "phase": PHASE,
            "provider": "forge",
            "prompt_authority": "neo_core_positive_prompt",
            "native_runtime_required": True,
        },
    }


def normalize_block(raw: Any) -> dict[str, Any]:
    source = _extract_block(raw)
    params_source = source.get("params") if isinstance(source.get("params"), Mapping) else {}
    params = deepcopy(DEFAULT_PARAMS)
    params.update(dict(params_source))

    mode = str(params.get("mode") or "Basic").strip().title()
    params["mode"] = mode if mode in REGION_MODES else "Basic"
    direction = str(params.get("direction") or "Horizontal").strip().title()
    params["direction"] = direction if direction in DIRECTIONS else "Horizontal"
    background = str(params.get("background") or "None").strip().title()
    params["background"] = background if background in BACKGROUNDS else "None"
    parser = str(params.get("common_parser") or "{ }").strip()
    params["common_parser"] = parser if parser in COMMON_PARSERS else "{ }"
    params["separator"] = str(params.get("separator") or "")
    params["disable_hr"] = _as_bool(params.get("disable_hr"), True)
    params["common_debug"] = _as_bool(params.get("common_debug"), False)
    params["def_in_prompt"] = _as_bool(params.get("def_in_prompt"), True)
    params["background_weight"] = _number(params.get("background_weight"), 0.5, 0.1, 1.5)
    params["advanced_mapping"] = normalize_mapping(params.get("advanced_mapping"))
    params["mask_mapping"] = normalize_mask_mapping(params.get("mask_mapping"))
    params.update(normalize_tile_params(params))

    block = default_block()
    block["enabled"] = _as_bool(source.get("enabled"), False)
    block["version"] = str(source.get("version") or VERSION)
    block["inputs"] = dict(source.get("inputs") or {}) if isinstance(source.get("inputs"), Mapping) else {}
    block["params"] = params
    block["assets"] = dict(source.get("assets") or {}) if isinstance(source.get("assets"), Mapping) else {}
    metadata = dict(source.get("metadata") or {}) if isinstance(source.get("metadata"), Mapping) else {}
    block["metadata"] = {**block["metadata"], **metadata}
    return block


def separator_value(params: Mapping[str, Any]) -> str:
    return str(params.get("separator") or "")


def split_prompt(prompt: Any, separator: Any = "") -> list[str]:
    text = str(prompt or "")
    token = str(separator or "").replace("\\n", "\n").replace("\\t", " ")
    if not token.strip():
        token = "\n"
    return [chunk.strip() for chunk in text.split(token)]


def required_prompt_lines(params: Mapping[str, Any]) -> int:
    mode = str(params.get("mode") or "Basic")
    if mode == "Advanced":
        return len(normalize_mapping(params.get("advanced_mapping")))
    if mode == "Mask":
        mask_count = len(normalize_mask_mapping(params.get("mask_mapping")))
        return mask_count + int(str(params.get("background") or "None") != "None")
    return 3 if str(params.get("background") or "None") != "None" else 2


def _tile_slots(params: Mapping[str, Any]) -> list[Any]:
    tile = normalize_tile_params(params)
    if not tile["tile_enabled"]:
        return [None, None, None, None, None, None]
    return [
        True,
        int(tile["tile_columns"]),
        int(tile["tile_rows"]),
        float(tile["tile_threshold"]),
        str(tile["tile_subject_replacement"] or ""),
        bool(tile["tile_debug"]),
    ]


def compile_basic_args(raw: Any) -> list[Any]:
    block = normalize_block(raw)
    params = block["params"]
    background = str(params.get("background") or "None")
    background_weight = float(params.get("background_weight") or 0.5)
    return [
        True,
        bool(params.get("disable_hr", True)),
        "Basic",
        str(params.get("separator") or ""),
        str(params.get("direction") or "Horizontal"),
        background,
        background_weight,
        None,
        str(params.get("common_parser") or "{ }"),
        bool(params.get("common_debug", False)),
        bool(params.get("def_in_prompt", True)),
        *_tile_slots(params),
    ]


def compile_advanced_args(raw: Any) -> list[Any]:
    block = normalize_block(raw)
    params = block["params"]
    return [
        True,
        bool(params.get("disable_hr", True)),
        "Advanced",
        str(params.get("separator") or ""),
        str(params.get("direction") or "Horizontal"),
        "None",
        float(params.get("background_weight") or 0.5),
        normalize_mapping(params.get("advanced_mapping")),
        str(params.get("common_parser") or "{ }"),
        bool(params.get("common_debug", False)),
        bool(params.get("def_in_prompt", True)),
        *_tile_slots(params),
    ]


def compile_mask_args(raw: Any, *, image_encoder) -> list[Any]:
    block = normalize_block(raw)
    params = block["params"]
    background = str(params.get("background") or "None")
    return [
        True,
        bool(params.get("disable_hr", True)),
        "Mask",
        str(params.get("separator") or ""),
        None,
        background,
        float(params.get("background_weight") or 0.5) if background != "None" else None,
        compile_mask_mapping(params.get("mask_mapping"), image_encoder=image_encoder),
        str(params.get("common_parser") or "{ }"),
        bool(params.get("common_debug", False)),
        bool(params.get("def_in_prompt", True)),
        *_tile_slots(params),
    ]


def compile_args(raw: Any, *, image_encoder=None) -> list[Any]:
    block = normalize_block(raw)
    mode = block["params"]["mode"]
    if mode == "Advanced":
        return compile_advanced_args(block)
    if mode == "Mask":
        if image_encoder is None:
            raise ValueError("ForgeCouple Mask mode requires an image encoder.")
        return compile_mask_args(block, image_encoder=image_encoder)
    return compile_basic_args(block)
