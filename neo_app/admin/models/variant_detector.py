from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
import re

GGUF_QUANT_PATTERNS = [
    "Q2_K",
    "Q3_K_S",
    "Q3_K_M",
    "Q3_K_L",
    "Q4_0",
    "Q4_K_S",
    "Q4_K_M",
    "Q5_0",
    "Q5_K_S",
    "Q5_K_M",
    "Q6_K",
    "Q8_0",
    "F16",
    "BF16",
]

GGUF_QUALITY_LABELS = {
    "Q2_K": "Small / low memory",
    "Q3_K_S": "Small",
    "Q3_K_M": "Small balanced",
    "Q3_K_L": "Small higher quality",
    "Q4_0": "Balanced",
    "Q4_K_S": "Balanced smaller",
    "Q4_K_M": "Best balance",
    "Q5_0": "Higher quality",
    "Q5_K_S": "Higher quality smaller",
    "Q5_K_M": "Higher quality",
    "Q6_K": "Large / high quality",
    "Q8_0": "Very large / high quality",
    "F16": "Full precision",
    "BF16": "Full precision",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def file_extension(path_or_name: str) -> str:
    name = _clean(path_or_name)
    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    return suffix


def detect_gguf_quant(filename: str, quant_patterns: list[str] | None = None) -> str:
    """Detect common GGUF quant strings from a filename."""

    patterns = quant_patterns or GGUF_QUANT_PATTERNS
    name = _clean(filename).upper()
    for quant in sorted([_clean(item).upper() for item in patterns if _clean(item)], key=len, reverse=True):
        if re.search(rf"(^|[^A-Z0-9]){re.escape(quant)}([^A-Z0-9]|$)", name):
            return quant
    return ""


def variant_label_for_file(filename: str, *, variant_detection: str = "", quant_patterns: list[str] | None = None) -> dict[str, Any]:
    """Return normalized variant metadata for a remote/local model file."""

    detection = _clean(variant_detection).lower()
    extension = file_extension(filename)
    quant = detect_gguf_quant(filename, quant_patterns) if detection == "gguf_quant" or extension == ".gguf" else ""
    variant_id = quant or PurePosixPath(filename.replace("\\", "/")).name
    label = quant or PurePosixPath(filename.replace("\\", "/")).name
    quality = GGUF_QUALITY_LABELS.get(quant, "")
    return {
        "variant_id": variant_id,
        "variant_label": label,
        "variant_detection": detection or ("gguf_quant" if extension == ".gguf" else "filename"),
        "quant": quant,
        "quality_hint": quality,
        "extension": extension,
    }


def recommended_variant_match(variant: dict[str, Any], recommended_variants: list[Any]) -> bool:
    recommended = {_clean(item).upper() for item in _as_list(recommended_variants) if _clean(item)}
    if not recommended:
        return False
    quant = _clean(variant.get("quant")).upper()
    variant_id = _clean(variant.get("variant_id")).upper()
    label = _clean(variant.get("variant_label")).upper()
    return bool({quant, variant_id, label} & recommended)
