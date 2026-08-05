from __future__ import annotations

from typing import Any, Mapping

from .advanced import mapping_covers_canvas, mapping_errors, normalize_mapping
from .constants import EXTENSION_ID, SUPPORTED_FAMILIES, SUPPORTED_MODES
from .mask import mask_mapping_errors, mask_union_coverage, normalize_mask_mapping
from .payload_schema import normalize_block, required_prompt_lines, split_prompt
from .tile import tile_errors


def validate_payload(
    raw: Any,
    *,
    prompt: Any = "",
    family: str = "",
    mode: str = "txt2img",
    capability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    block = normalize_block(raw)
    params = block["params"]
    errors: list[str] = []
    warnings: list[str] = []
    capability = capability if isinstance(capability, Mapping) else {}
    region_mode = str(params.get("mode") or "Basic")
    chunks = split_prompt(prompt, params.get("separator"))
    mapping = normalize_mapping(params.get("advanced_mapping"))
    mask_mapping = normalize_mask_mapping(params.get("mask_mapping"))

    if block.get("enabled"):
        if region_mode not in {"Basic", "Advanced", "Mask"}:
            errors.append("Neo ForgeCouple Phase 3 supports Basic, Advanced, and Mask modes.")
        if family and family not in SUPPORTED_FAMILIES:
            errors.append(f"ForgeCouple Phase 3 supports SD1.5/SDXL only; {family} is gated.")
        if mode not in SUPPORTED_MODES:
            errors.append(f"ForgeCouple Phase 3 does not support the {mode} route.")
        if capability and not capability.get("available"):
            errors.append(str(capability.get("reason") or "ForgeCouple native script capability is unavailable."))
        if any(not chunk for chunk in chunks):
            errors.append("ForgeCouple prompt regions cannot be empty. Remove trailing or repeated separators.")

        if region_mode == "Advanced":
            errors.extend(mapping_errors(mapping))
            if len(chunks) != len(mapping):
                errors.append(f"ForgeCouple Advanced mode requires one prompt region per mapping; found {len(chunks)} prompts and {len(mapping)} mappings.")
            if not mapping_errors(mapping) and not mapping_covers_canvas(mapping):
                errors.append("ForgeCouple Advanced mapping must cover the entire canvas without gaps.")
            if any(float(item[4]) == 0.0 for item in mapping):
                warnings.append("One or more Advanced regions have zero weight and may contribute no conditioning.")
        elif region_mode == "Mask":
            errors.extend(mask_mapping_errors(params.get("mask_mapping")))
            needed = required_prompt_lines(params)
            if len(chunks) != needed:
                errors.append(
                    f"ForgeCouple Mask mode requires exactly one prompt region per saved mask"
                    f"{' plus one Global Effect line' if params.get('background') != 'None' else ''}; "
                    f"found {len(chunks)} prompts and {len(mask_mapping)} masks."
                )
            coverage = mask_union_coverage(params.get("mask_mapping"))
            if str(params.get("background") or "None") == "None":
                if coverage is None:
                    errors.append("ForgeCouple Mask mode could not verify full-canvas coverage from the submitted session masks.")
                elif coverage < 0.999:
                    errors.append(f"ForgeCouple Mask layers cover only {coverage * 100:.1f}% of the canvas. Cover the full canvas or enable Global Effect.")
            if any(float(item.get("weight", 1.0)) == 0.0 for item in mask_mapping):
                warnings.append("One or more Mask layers have zero weight and may contribute no conditioning.")
        else:
            needed = required_prompt_lines(params)
            if len(chunks) < needed:
                errors.append(f"ForgeCouple Basic mode requires at least {needed} positive-prompt regions; found {len(chunks)}.")
            if params.get("background") == "None" and params.get("background_weight") not in (None, 0.5):
                warnings.append("Global Effect weight is ignored while Global Effect is None.")

        errors.extend(tile_errors(params, mode=mode, region_mode=region_mode))
        if params.get("tile_enabled") and region_mode == "Mask" and not mask_mapping:
            errors.append("ForgeCouple Tile Mode cannot derive tile inclusion without saved Mask layers.")

    return {
        "extension_id": EXTENSION_ID,
        "ok": not errors,
        "block": block,
        "errors": errors,
        "warnings": warnings,
        "derived": {
            "mode": region_mode,
            "region_count": len(chunks),
            "required_region_count": required_prompt_lines(params),
            "mapping_count": len(mapping),
            "mask_count": len(mask_mapping),
            "mask_coverage_ratio": mask_union_coverage(params.get("mask_mapping")) if region_mode == "Mask" else None,
            "mapping_covers_canvas": mapping_covers_canvas(mapping) if region_mode == "Advanced" and not mapping_errors(mapping) else None,
            "tile_enabled": bool(params.get("tile_enabled")),
            "tile_count": int(params.get("tile_columns") or 0) * int(params.get("tile_rows") or 0) if params.get("tile_enabled") else 0,
            "prompt_authority": "neo_core_positive_prompt",
        },
    }


def validate_basic_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible Phase 1 import retained for downstream callers/tests."""
    return validate_payload(*args, **kwargs)
