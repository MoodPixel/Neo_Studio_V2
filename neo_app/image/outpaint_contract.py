from __future__ import annotations

from math import sqrt
from typing import Any


OUTPAINT_PADDING_KEYS = ("left", "right", "top", "bottom")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def _pick(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _has_outpaint_source_resolution_policy(params: dict[str, Any]) -> bool:
    keys = {
        "outpaint_source_resolution",
        "outpaint_source_resolution_mode",
        "outpaint_source_max_long_edge",
        "outpaint_source_max_megapixels",
        "outpaint_working_width",
        "outpaint_working_height",
        "source_image_width",
        "source_image_height",
        "outpaint_source_width",
        "outpaint_source_height",
    }
    return any(params.get(key) not in (None, "", 0) for key in keys)


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _multiple_of(value: int, multiple: int = 8) -> int:
    clean = max(multiple, int(value or multiple))
    return max(multiple, int(round(clean / multiple) * multiple))


def normalize_outpaint_source_resolution(params: dict[str, Any] | None, *, default_width: int = 1024, default_height: int = 1024) -> dict[str, Any]:
    """Normalize the UI's outpaint working-copy policy.

    The policy is backend-neutral: UI can expose it for every outpaint route,
    while heavy Comfy compilers can use the resolved working size before
    ImagePadForOutpaint. Raw source dimensions are metadata only; the working
    size is what should drive latent/canvas math.
    """

    params = dict(params or {})
    nested = _as_dict(params.get("outpaint_source_resolution"))
    source_nested = _as_dict(nested.get("source_size"))
    working_nested = _as_dict(nested.get("working_size"))
    raw_mode = str(_pick(nested, "mode", default=_pick(params, "outpaint_source_resolution_mode", default="auto")) or "auto").strip().lower()
    mode_aliases = {
        "original": "keep_original",
        "keep-original": "keep_original",
        "keeporiginal": "keep_original",
        "full": "keep_original",
        "full_res": "keep_original",
        "resize": "auto",
        "recommended": "auto",
    }
    mode = mode_aliases.get(raw_mode, raw_mode)
    if mode not in {"auto", "keep_original", "custom"}:
        mode = "auto"

    max_long_edge = max(512, _int_value(_pick(nested, "max_long_edge", default=_pick(params, "outpaint_source_max_long_edge", default=1536)), 1536))
    max_megapixels = max(0.25, _float_value(_pick(nested, "max_megapixels", default=_pick(params, "outpaint_source_max_megapixels", default=4.0)), 4.0))

    source_width = max(0, _int_value(_pick(source_nested, "width", default=_pick(params, "source_image_width", "outpaint_source_width", default=0)), 0))
    source_height = max(0, _int_value(_pick(source_nested, "height", default=_pick(params, "source_image_height", "outpaint_source_height", default=0)), 0))

    default_width = max(64, _int_value(default_width, 1024))
    default_height = max(64, _int_value(default_height, 1024))
    if mode == "keep_original" and source_width and source_height:
        working_width, working_height = source_width, source_height
    else:
        requested_working_width = _pick(working_nested, "width", default=_pick(params, "outpaint_working_width", default=None))
        requested_working_height = _pick(working_nested, "height", default=_pick(params, "outpaint_working_height", default=None))
        base_w = max(64, _int_value(requested_working_width, source_width or default_width))
        base_h = max(64, _int_value(requested_working_height, source_height or default_height))
        scale = 1.0
        long_edge = max(base_w, base_h)
        total_pixels = max(1, base_w * base_h)
        if mode in {"auto", "custom"}:
            scale = min(scale, float(max_long_edge) / float(long_edge)) if long_edge > max_long_edge else scale
            max_pixels = max_megapixels * 1_000_000.0
            if total_pixels > max_pixels:
                scale = min(scale, sqrt(max_pixels / float(total_pixels)))
        working_width = _multiple_of(max(64, int(round(base_w * scale))), 8)
        working_height = _multiple_of(max(64, int(round(base_h * scale))), 8)

    source_pixels = source_width * source_height if source_width and source_height else 0
    working_pixels = working_width * working_height
    scale_ratio = 1.0
    if source_width and source_height:
        scale_ratio = min(1.0, working_width / max(1, source_width), working_height / max(1, source_height))

    return {
        "mode": mode,
        "max_long_edge": int(max_long_edge),
        "max_megapixels": float(max_megapixels),
        "source_size": {"width": int(source_width), "height": int(source_height), "megapixels": round(source_pixels / 1_000_000.0, 3) if source_pixels else 0},
        "working_size": {"width": int(working_width), "height": int(working_height), "megapixels": round(working_pixels / 1_000_000.0, 3)},
        "scale_ratio": round(float(scale_ratio), 4),
        "applies_working_copy": mode != "keep_original",
        "reason": "Neo uses a model-safe working copy for outpaint before optional upscale/restore." if mode != "keep_original" else "Neo keeps the source at original resolution for this outpaint run.",
    }

def normalize_outpaint_payload(params: dict[str, Any] | None, *, default_width: int = 1024, default_height: int = 1024) -> dict[str, Any]:
    """Return the Phase 12.19 backend-neutral outpaint payload.

    V2 accepts the new nested contract and the older flat UI fields. Providers
    should compile from this normalized object instead of treating outpaint as a
    loose img2img variant.
    """

    params = dict(params or {})
    padding_payload = _as_dict(params.get("padding") or params.get("outpaint_padding"))
    mask_payload = _as_dict(params.get("mask") or params.get("outpaint_mask"))
    final_size_payload = _as_dict(params.get("final_size") or params.get("outpaint_final_size"))

    padding = {
        "left": max(0, _int_value(_pick(padding_payload, "left", default=_pick(params, "outpaint_left", "pad_left", "left", default=0)))),
        "right": max(0, _int_value(_pick(padding_payload, "right", default=_pick(params, "outpaint_right", "pad_right", "right", default=0)))),
        "top": max(0, _int_value(_pick(padding_payload, "top", default=_pick(params, "outpaint_top", "pad_top", "top", default=0)))),
        "bottom": max(0, _int_value(_pick(padding_payload, "bottom", default=_pick(params, "outpaint_bottom", "pad_bottom", "bottom", default=0)))),
    }

    source_image = _pick(params, "source_image", "source_image_path", "init_image", "image", "source_image_url", "source_url", default="")
    if isinstance(source_image, dict):
        source_image = _pick(source_image, "path", "file", "filename", "url", "source_id", default="")

    # Feather is the soft edge for the generated expansion mask. Blur is kept as
    # an explicit mask-contract field for backends that need a second blur pass.
    feather = max(0, _int_value(_pick(mask_payload, "feather", default=_pick(params, "outpaint_feather", "feather", default=16))))
    blur = max(0, _int_value(_pick(mask_payload, "blur", default=_pick(params, "outpaint_blur", "outpaint_mask_blur", "mask_blur", default=8))))
    auto_generate = mask_payload.get("auto_generate")
    if auto_generate is None:
        auto_generate = params.get("outpaint_auto_mask", True)

    include_source_resolution = _has_outpaint_source_resolution_policy(params)
    source_resolution = normalize_outpaint_source_resolution(params, default_width=default_width, default_height=default_height) if include_source_resolution else {}
    working_size = _as_dict(source_resolution.get("working_size"))
    base_width = _int_value(_pick(working_size, "width", default=_pick(params, "base_width", "width", default=default_width)), default_width)
    base_height = _int_value(_pick(working_size, "height", default=_pick(params, "base_height", "height", default=default_height)), default_height)
    final_width = _int_value(final_size_payload.get("width"), base_width + padding["left"] + padding["right"])
    final_height = _int_value(final_size_payload.get("height"), base_height + padding["top"] + padding["bottom"])

    payload = {
        "mode": "outpaint",
        "source_image": str(source_image or "").strip(),
        "padding": padding,
        "mask": {
            "auto_generate": bool(auto_generate),
            "feather": feather,
            "blur": blur,
        },
        "final_size": {
            "width": max(64, final_width),
            "height": max(64, final_height),
        },
    }
    if include_source_resolution:
        payload["source_resolution"] = source_resolution
    return payload


def outpaint_padding_total(payload: dict[str, Any]) -> int:
    padding = _as_dict(payload.get("padding"))
    return sum(max(0, _int_value(padding.get(side), 0)) for side in OUTPAINT_PADDING_KEYS)


def flatten_outpaint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility fields for existing compiler internals/logs."""

    padding = _as_dict(payload.get("padding"))
    mask = _as_dict(payload.get("mask"))
    final_size = _as_dict(payload.get("final_size"))
    return {
        "outpaint_left": max(0, _int_value(padding.get("left"), 0)),
        "outpaint_right": max(0, _int_value(padding.get("right"), 0)),
        "outpaint_top": max(0, _int_value(padding.get("top"), 0)),
        "outpaint_bottom": max(0, _int_value(padding.get("bottom"), 0)),
        "outpaint_feather": max(0, _int_value(mask.get("feather"), 16)),
        "outpaint_blur": max(0, _int_value(mask.get("blur"), 8)),
        "final_width": max(64, _int_value(final_size.get("width"), 1024)),
        "final_height": max(64, _int_value(final_size.get("height"), 1024)),
    }
