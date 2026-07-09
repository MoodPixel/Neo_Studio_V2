from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

PROMPT_CONDITIONING_MODES = {"raw", "soft_clamp", "balanced"}
PROMPT_WEIGHT_PATTERN = re.compile(r"[\(\[]\s*([^\(\)\[\]]+?)\s*:\s*(-?\d*\.?\d+)\s*[\)\]]")

UI_MODE_ALIASES = {
    "off": "raw",
    "none": "raw",
    "raw": "raw",
    "soft": "soft_clamp",
    "soft_clamp": "soft_clamp",
    "strict": "balanced",
    "balanced": "balanced",
}


@dataclass(frozen=True)
class PromptConditioningResult:
    mode: str
    original: str
    effective: str
    changed: bool
    weighted_tags: int
    clamped_tags: int
    min_weight: float | None
    max_weight: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_prompt_conditioning_mode(value: Any) -> str:
    raw = str(value or "raw").strip().lower().replace("-", "_") or "raw"
    return UI_MODE_ALIASES.get(raw, "raw")


def display_prompt_conditioning_mode(value: Any) -> str:
    mode = normalize_prompt_conditioning_mode(value)
    if mode == "soft_clamp":
        return "Soft Clamp"
    if mode == "balanced":
        return "Balanced"
    return "Raw"


def _mode_limits(mode: str) -> tuple[float | None, float | None]:
    normalized = normalize_prompt_conditioning_mode(mode)
    if normalized == "soft_clamp":
        return 0.4, 1.6
    if normalized == "balanced":
        return 0.5, 1.45
    return None, None


def condition_prompt_text(text: Any, mode: Any) -> PromptConditioningResult:
    original = str(text or "")
    normalized = normalize_prompt_conditioning_mode(mode)
    min_weight, max_weight = _mode_limits(normalized)
    weighted_tags = 0
    clamped_tags = 0

    if not original or normalized == "raw" or min_weight is None or max_weight is None:
        return PromptConditioningResult(
            mode=normalized,
            original=original,
            effective=original,
            changed=False,
            weighted_tags=len(PROMPT_WEIGHT_PATTERN.findall(original)),
            clamped_tags=0,
            min_weight=min_weight,
            max_weight=max_weight,
        )

    def repl(match: re.Match[str]) -> str:
        nonlocal weighted_tags, clamped_tags
        weighted_tags += 1
        body = str(match.group(1) or "").strip()
        if normalized == "balanced":
            body = " ".join(body.split())
        try:
            weight = float(match.group(2) or 1.0)
        except Exception:
            weight = 1.0
        clamped = max(min_weight, min(max_weight, weight))
        if abs(clamped - weight) > 0.000001:
            clamped_tags += 1
        normalized_weight = f"{clamped:.2f}".rstrip("0").rstrip(".")
        return f"({body}:{normalized_weight})"

    effective = PROMPT_WEIGHT_PATTERN.sub(repl, original)
    if normalized == "balanced":
        effective = re.sub(r"\s*,\s*,+", ", ", effective)
        effective = re.sub(r"\s{2,}", " ", effective)
        effective = effective.strip(" ,")

    return PromptConditioningResult(
        mode=normalized,
        original=original,
        effective=effective,
        changed=effective != original,
        weighted_tags=weighted_tags,
        clamped_tags=clamped_tags,
        min_weight=min_weight,
        max_weight=max_weight,
    )


def condition_prompt_pair(positive: Any, negative: Any, mode: Any) -> dict[str, Any]:
    positive_result = condition_prompt_text(positive, mode)
    negative_result = condition_prompt_text(negative, mode)
    return {
        "mode": normalize_prompt_conditioning_mode(mode),
        "display_mode": display_prompt_conditioning_mode(mode),
        "positive": positive_result.to_dict(),
        "negative": negative_result.to_dict(),
        "changed": positive_result.changed or negative_result.changed,
        "weighted_tags": positive_result.weighted_tags + negative_result.weighted_tags,
        "clamped_tags": positive_result.clamped_tags + negative_result.clamped_tags,
        "effective_positive": positive_result.effective,
        "effective_negative": negative_result.effective,
        "original_positive": positive_result.original,
        "original_negative": negative_result.original,
    }
