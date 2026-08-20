from __future__ import annotations

"""Modern/lightweight Scene Director subject-structure authority.

This module is intentionally prompt-only.  It does not move masks, rewrite user
regional prompts, add sampler passes, or alter LoRA ownership.  It derives a
small scene-level subject-count bridge plus warning-only prompt/box conflict
metadata for modern models that already understand rich semantic prompts.
"""

from copy import deepcopy
import re
from typing import Any

SUBJECT_AUTHORITY_SCHEMA = "neo.image.scene_director.modern_subject_authority.v1"

DEFAULT_CONTRACTS = {
    "enabled": True,
    "use_node_auto_prompts": False,
    "count_contract": "exactly {count} visible subjects, one complete subject per character region, every assigned character region occupied",
    "subject_contract": "exactly one complete visible subject inside this assigned region, separate from neighboring subjects",
    "negative_contract": "fewer than {count} visible subjects, more than {count} visible subjects, missing assigned subject region, merged subjects, shared limbs, fused faces",
}

_CHARACTER_ROLES = {"person", "subject", "character", "main_subject"}
_DIRECTION_PATTERNS = {
    "left": (
        r"\b(?:standing|sitting|seated|positioned|located|placed|walking|posed)\s+(?:on|at|to)\s+the\s+left\b",
        r"\bon\s+the\s+left(?:\s+side)?\b(?!\s+of\b)",
        r"\bleft[-\s]side(?:d)?\s+(?:subject|person|character|man|woman)\b",
    ),
    "right": (
        r"\b(?:standing|sitting|seated|positioned|located|placed|walking|posed)\s+(?:on|at|to)\s+the\s+right\b",
        r"\bon\s+the\s+right(?:\s+side)?\b(?!\s+of\b)",
        r"\bright[-\s]side(?:d)?\s+(?:subject|person|character|man|woman)\b",
    ),
    "center": (
        r"\b(?:standing|sitting|seated|positioned|located|placed|walking|posed)\s+(?:in|at)\s+the\s+cent(?:er|re)\b",
        r"\bin\s+the\s+cent(?:er|re)\b",
        r"\bcent(?:er|re)[-\s](?:subject|person|character|man|woman)\b",
    ),
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _render_contract(template: Any, count: int) -> str:
    text = _text(template)
    if not text:
        return ""
    try:
        return text.format(count=count)
    except Exception:
        return text.replace("{count}", str(count))


def _append_unique_clause(prompt: str, clause: str) -> str:
    base = _text(prompt).rstrip(" ,;.")
    extra = _text(clause).rstrip(" ,;.")
    if not extra:
        return base
    if extra.casefold() in base.casefold():
        return base
    return f"{base}, {extra}" if base else extra


def _role(region: dict[str, Any]) -> str:
    value = _text(region.get("type") or region.get("role") or region.get("region_role") or "object").lower().replace("-", "_").replace(" ", "_")
    return "character" if value in _CHARACTER_ROLES else value


def is_character_region(region: Any) -> bool:
    return isinstance(region, dict) and _role(region) == "character"


def _region_active(region: dict[str, Any]) -> bool:
    return region.get("enabled", True) is not False and region.get("visible", True) is not False


def _pixel_rect(region: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
    try:
        x = float(bbox.get("x", 0.0) or 0.0)
        y = float(bbox.get("y", 0.0) or 0.0)
        w = float(bbox.get("w", 1.0) or 1.0)
        h = float(bbox.get("h", 1.0) or 1.0)
    except Exception:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    if abs(x) <= 1.0 and abs(w) <= 1.0:
        left = int(round(width * x))
        right = int(round(width * (x + w)))
    else:
        left = int(round(x))
        right = int(round(x + w))
    if abs(y) <= 1.0 and abs(h) <= 1.0:
        top = int(round(height * y))
        bottom = int(round(height * (y + h)))
    else:
        top = int(round(y))
        bottom = int(round(y + h))
    left = max(0, min(max(0, width - 1), left))
    top = max(0, min(max(0, height - 1), top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right - left, bottom - top


def _mask_horizontal_position(region: dict[str, Any], width: int, height: int) -> str:
    x, _y, rw, _rh = _pixel_rect(region, width, height)
    center = (float(x) + (float(rw) / 2.0)) / max(1.0, float(width))
    if center <= 0.42:
        return "left"
    if center >= 0.58:
        return "right"
    return "center"


def _prompt_direction(prompt: Any) -> str:
    text = _text(prompt).lower()
    if not text:
        return ""
    hits: set[str] = set()
    for direction, patterns in _DIRECTION_PATTERNS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            hits.add(direction)
    return next(iter(hits)) if len(hits) == 1 else ""


def _subject_gender(prompt: Any) -> tuple[str, str]:
    """Return conservative (gender, maturity) facts from explicit prompt words only."""
    text = _text(prompt).lower()
    if not text:
        return "unknown", "unknown"

    female_adult = bool(re.search(r"\b(?:adult\s+)?(?:woman|female|lady)\b", text))
    male_adult = bool(re.search(r"\b(?:adult\s+)?(?:man|male|gentleman|guy)\b", text))
    female_child = bool(re.search(r"\b(?:girl|female\s+child)\b", text))
    male_child = bool(re.search(r"\b(?:boy|male\s+child)\b", text))

    female = female_adult or female_child
    male = male_adult or male_child
    if female == male:
        return "unknown", "unknown"
    if female:
        return "female", "adult" if female_adult and not female_child else ("child" if female_child and not female_adult else "unknown")
    return "male", "adult" if male_adult and not male_child else ("child" if male_child and not male_adult else "unknown")


def _class_summary(character_regions: list[dict[str, Any]]) -> dict[str, Any]:
    facts = []
    for region in character_regions:
        gender, maturity = _subject_gender(region.get("prompt"))
        facts.append({
            "region_id": str(region.get("id") or ""),
            "label": str(region.get("label") or region.get("id") or ""),
            "gender": gender,
            "maturity": maturity,
        })

    known_gender = [row for row in facts if row["gender"] in {"male", "female"}]
    inferred_text = ""
    all_gender_known = bool(facts) and len(known_gender) == len(facts)
    if all_gender_known:
        male = sum(1 for row in facts if row["gender"] == "male")
        female = sum(1 for row in facts if row["gender"] == "female")
        all_adult = all(row["maturity"] == "adult" for row in facts)
        if male == len(facts):
            inferred_text = "all declared subjects are adult men" if all_adult else "all declared subjects are male"
        elif female == len(facts):
            inferred_text = "all declared subjects are adult women" if all_adult else "all declared subjects are female"
        else:
            parts = []
            if male:
                parts.append(f"{male} male subject{'s' if male != 1 else ''}")
            if female:
                parts.append(f"{female} female subject{'s' if female != 1 else ''}")
            inferred_text = "declared cast: " + " and ".join(parts)
    return {
        "all_gender_known": all_gender_known,
        "inferred_text": inferred_text,
        "regions": facts,
    }


def build_modern_subject_authority(
    regions: Any,
    *,
    contracts: Any = None,
    canvas_width: int = 1024,
    canvas_height: int = 1024,
) -> dict[str, Any]:
    rows = [deepcopy(item) for item in (regions if isinstance(regions, list) else []) if isinstance(item, dict)]
    character_regions = [row for row in rows if _region_active(row) and is_character_region(row)]
    count = len(character_regions)
    raw_contracts = contracts if isinstance(contracts, dict) else {}
    merged_contracts = {**DEFAULT_CONTRACTS, **raw_contracts}
    enabled = merged_contracts.get("enabled") is not False and count > 0

    count_contract = _render_contract(merged_contracts.get("count_contract"), count) if enabled else ""
    subject_contract = _render_contract(merged_contracts.get("subject_contract"), count) if enabled else ""
    negative_contract = _render_contract(merged_contracts.get("negative_contract"), count) if enabled else ""
    class_summary = _class_summary(character_regions)

    global_bridge = count_contract
    if global_bridge and not re.search(r"\b(?:no|without)\s+(?:additional|extra)\s+(?:visible\s+)?subjects?\b", global_bridge, flags=re.IGNORECASE):
        global_bridge = _append_unique_clause(global_bridge, "no additional visible subjects")
    if class_summary.get("inferred_text"):
        global_bridge = _append_unique_clause(global_bridge, str(class_summary["inferred_text"]))

    conflicts: list[dict[str, Any]] = []
    region_positions: dict[str, str] = {}
    for index, region in enumerate(character_regions):
        region_id = str(region.get("id") or f"scene_region_{index + 1}")
        mask_position = _mask_horizontal_position(region, max(64, int(canvas_width)), max(64, int(canvas_height)))
        region_positions[region_id] = mask_position
        prompt_direction = _prompt_direction(region.get("prompt"))
        if prompt_direction and prompt_direction != mask_position:
            conflicts.append({
                "code": "prompt_direction_vs_mask_position",
                "region_id": region_id,
                "label": str(region.get("label") or region_id),
                "mask_position": mask_position,
                "prompt_direction": prompt_direction,
                "bbox": deepcopy(region.get("bbox") or {}),
                "message": (
                    f"{region.get('label') or region_id} is masked on the {mask_position}, but its regional prompt explicitly places the subject on the {prompt_direction}. "
                    "The mask remains authoritative; Neo will not rewrite the prompt automatically."
                ),
            })

    return {
        "schema": SUBJECT_AUTHORITY_SCHEMA,
        "enabled": enabled,
        "character_region_count": count,
        "inferred_subject_count": count,
        "contracts": {
            "count_contract": count_contract,
            "subject_contract": subject_contract,
            "negative_contract": negative_contract,
        },
        "global_bridge": global_bridge,
        "global_negative_bridge": negative_contract,
        "regional_subject_contract": subject_contract,
        "class_inference": class_summary,
        "region_positions": region_positions,
        "directional_conflicts": conflicts,
        "prompt_conflict_count": len(conflicts),
        "sanitizer_policy": "warning_only_no_silent_prompt_rewrite",
    }


def merge_subject_authority_prompt(prompt: Any, bridge: Any) -> str:
    """Merge structural subject authority into one coherent provider text prompt.

    IMG-SD1D deliberately keeps this as text composition rather than a second
    conditioning lane. Modern semantic image encoders (Krea/Qwen-style, Klein,
    Z-Image) behave more consistently when scene/style and cast structure are
    encoded together, and inpaint wrappers retain their conditioning metadata
    because the existing provider text source remains the one upstream lane.
    """
    return _append_unique_clause(_text(prompt), _text(bridge))


def compile_regional_subject_prompt(prompt: Any, subject_authority: Any, *, region: Any) -> str:
    base = _text(prompt)
    authority = subject_authority if isinstance(subject_authority, dict) else {}
    if not authority.get("enabled") or not is_character_region(region):
        return base
    return _append_unique_clause(base, str(authority.get("regional_subject_contract") or ""))


__all__ = [
    "SUBJECT_AUTHORITY_SCHEMA",
    "DEFAULT_CONTRACTS",
    "build_modern_subject_authority",
    "merge_subject_authority_prompt",
    "compile_regional_subject_prompt",
    "is_character_region",
]
