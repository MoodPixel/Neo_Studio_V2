from __future__ import annotations

import math
from typing import Any, Mapping

from .constants import TILE_REGION_MODES


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled", ""}:
            return False
    return default


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))


def _integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    return int(round(_number(value, float(default), float(minimum), float(maximum))))


def normalize_tile_params(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "tile_enabled": _boolean(source.get("tile_enabled"), False),
        "tile_columns": _integer(source.get("tile_columns"), 2, 1, 64),
        "tile_rows": _integer(source.get("tile_rows"), 2, 1, 64),
        "tile_threshold": _number(source.get("tile_threshold"), 0.75, 0.0, 1.0),
        "tile_subject_replacement": str(source.get("tile_subject_replacement") or ""),
        "tile_debug": _boolean(source.get("tile_debug"), False),
        "tile_upscaler": str(source.get("tile_upscaler") or "None").strip() or "None",
        "tile_save_to_extras": _boolean(source.get("tile_save_to_extras"), False),
        "tile_scale_factor": _number(source.get("tile_scale_factor"), 2.0, 1.0, 8.0),
        "tile_overlap": _integer(source.get("tile_overlap"), 64, 0, 2048),
        "tile_final_width": _integer(source.get("tile_final_width"), -1, -1, 65536),
        "tile_final_height": _integer(source.get("tile_final_height"), -1, -1, 65536),
    }


def calculate_tile_grid(
    *,
    tile_width: int,
    tile_height: int,
    source_width: int,
    source_height: int,
    scale_factor: float,
    overlap: int,
) -> dict[str, int]:
    tile_width = max(1, int(tile_width))
    tile_height = max(1, int(tile_height))
    source_width = max(1, int(source_width))
    source_height = max(1, int(source_height))
    scale_factor = max(1.0, float(scale_factor))
    overlap = max(0, int(overlap))
    stride_x = tile_width - overlap
    stride_y = tile_height - overlap
    if stride_x <= 0 or stride_y <= 0:
        raise ValueError("Tile overlap must be smaller than the tile width and height.")
    final_width = int(source_width * scale_factor)
    final_height = int(source_height * scale_factor)
    columns = max(1, math.ceil((final_width - overlap) / stride_x))
    rows = max(1, math.ceil((final_height - overlap) / stride_y))
    return {
        "final_width": final_width,
        "final_height": final_height,
        "columns": columns,
        "rows": rows,
    }


def subject_replacement_errors(value: Any) -> list[str]:
    text = str(value or "")
    errors: list[str] = []
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            errors.append(f"Tile subject replacement line {index} must use 'replacement: source, source'.")
            continue
        goal, sources = line.split(":", 1)
        if not goal.strip() or not any(token.strip() for token in sources.split(",")):
            errors.append(f"Tile subject replacement line {index} must contain a replacement and at least one source tag.")
    return errors


def tile_errors(params: Mapping[str, Any], *, mode: str, region_mode: str = "") -> list[str]:
    normalized = normalize_tile_params(params)
    if not normalized["tile_enabled"]:
        return []
    errors: list[str] = []
    if mode != "img2img":
        errors.append("ForgeCouple Tile Mode is available only for Img2Img.")
    resolved_region_mode = str(region_mode or params.get("mode") or "Basic")
    if resolved_region_mode not in TILE_REGION_MODES:
        errors.append("ForgeCouple Mask + Tile is not API-verified in FC3. Use Basic or Advanced regions for Tile Mode.")
    if normalized["tile_columns"] * normalized["tile_rows"] < 2:
        errors.append("ForgeCouple Tile Mode requires at least two total tiles.")
    errors.extend(subject_replacement_errors(normalized["tile_subject_replacement"]))
    return errors


def is_compatible_tile_script(name: Any) -> bool:
    """Return True only for the selectable tiler verified for FC3.

    ForgeCouple assigns prompts to tiles but does not perform the tile loop.
    FC3 therefore fails closed to Forge's built-in/selectable SD Upscale script
    instead of accepting any arbitrary selectable script.
    """
    normalized = " ".join(str(name or "").replace("_", " ").replace("-", " ").casefold().split())
    return normalized in {"sd upscale", "sd upscale script"}
