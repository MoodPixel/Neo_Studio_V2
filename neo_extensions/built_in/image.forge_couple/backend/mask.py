from __future__ import annotations

import base64
from io import BytesIO
from math import isfinite
from typing import Any, Mapping

from PIL import Image


def _weight(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not isfinite(number):
        number = default
    return max(0.0, min(5.0, number))


def _mask_ref(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("data_uri", "data_url", "image", "mask", "value", "ref"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
        return ""
    return str(value or "").strip()


def normalize_mask_mapping(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw[:32]:
        if not isinstance(item, Mapping):
            continue
        mask = _mask_ref(item.get("mask"))
        if not mask:
            continue
        normalized.append({"mask": mask, "weight": _weight(item.get("weight"), 1.0)})
    return normalized


def mask_mapping_errors(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)) or not raw:
        return ["ForgeCouple Mask mode requires at least one saved mask layer."]
    errors: list[str] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"Mask layer {index} is not a valid mask object.")
            continue
        mask = _mask_ref(item.get("mask"))
        if not mask:
            errors.append(f"Mask layer {index} has no image data.")
        try:
            weight = float(item.get("weight", 1.0))
        except (TypeError, ValueError):
            errors.append(f"Mask layer {index} weight is not numeric.")
            continue
        if not isfinite(weight) or not 0.0 <= weight <= 5.0:
            errors.append(f"Mask layer {index} weight must stay between 0.0 and 5.0.")
    return errors


def _forge_mask_base64(value: Any) -> str:
    """Return ForgeCouple's API-native raw base64 image value.

    Neo's shared Forge image encoder returns data URIs, while upstream
    ForgeCouple decodes the ``mask`` field directly with ``base64.b64decode``.
    Strip the transport prefix here so the dedicated adapter matches the
    native extension contract without changing the shared encoder.
    """
    text = str(value or "").strip()
    if text.startswith("data:image/") and ";base64," in text:
        return text.split(";base64,", 1)[1].strip()
    return text


def compile_mask_mapping(raw: Any, *, image_encoder) -> list[dict[str, Any]]:
    compiled: list[dict[str, Any]] = []
    for index, item in enumerate(normalize_mask_mapping(raw), start=1):
        encoded = image_encoder(item["mask"], label=f"ForgeCouple mask layer {index}")
        compiled.append({
            "mask": _forge_mask_base64(encoded),
            "weight": float(item["weight"]),
        })
    return compiled


def redact_mask_mapping(raw: Any) -> list[dict[str, Any]]:
    """Return metadata-safe mask descriptors without embedding image bytes."""
    return [
        {"index": index, "weight": float(item["weight"]), "mask_present": True}
        for index, item in enumerate(normalize_mask_mapping(raw), start=1)
    ]


def mask_union_coverage(raw: Any, *, sample_size: int = 192) -> float | None:
    """Measure the union of inspectable binary mask data URIs.

    Returns ``None`` when any layer cannot be decoded locally. This keeps path
    references out of the public contract while allowing Neo-owned session masks
    to receive server-side full-canvas validation before Forge submission.
    """
    mapping = normalize_mask_mapping(raw)
    if not mapping:
        return 0.0
    sample_size = max(16, min(512, int(sample_size)))
    union = bytearray(sample_size * sample_size)
    for item in mapping:
        value = str(item.get("mask") or "")
        if not value.startswith("data:image/") or ";base64," not in value:
            return None
        try:
            encoded = value.split(";base64,", 1)[1]
            if len(encoded) > 48 * 1024 * 1024:
                return None
            payload = base64.b64decode(encoded, validate=True)
            if len(payload) > 36 * 1024 * 1024:
                return None
            with Image.open(BytesIO(payload)) as image:
                image = image.convert("RGBA").resize((sample_size, sample_size), Image.Resampling.NEAREST)
                pixels = image.tobytes()
                for index in range(sample_size * sample_size):
                    offset = index * 4
                    red, green, blue, alpha = pixels[offset:offset + 4]
                    if alpha > 20 and (red + green + blue) >= 600:
                        union[index] = 1
        except Exception:
            return None
    return sum(union) / len(union)
