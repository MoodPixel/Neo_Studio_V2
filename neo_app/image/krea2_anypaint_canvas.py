from __future__ import annotations

from math import ceil
from typing import Any, Mapping

from neo_app.image.outpaint_contract import normalize_outpaint_payload

ALIGNMENT = 16
SCHEMA_ID = "neo.image.krea2_anypaint_canvas.v1"
AUTHORITY = "neo_app.image.krea2_anypaint_canvas"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    alignment = max(1, int(alignment or ALIGNMENT))
    value = max(1, int(value or 0))
    return max(alignment, int(ceil(value / alignment) * alignment))


def _source_size(params: Mapping[str, Any], *, default_width: int, default_height: int) -> tuple[int, int, bool, str]:
    source_width = max(0, _int(params.get("source_image_width"), 0))
    source_height = max(0, _int(params.get("source_image_height"), 0))
    if source_width and source_height:
        return source_width, source_height, True, "source_image_metadata"
    # Fallback exists only so UI/compiler diagnostics can be deterministic before
    # the source preview has reported dimensions. It is not claimed as physical
    # source truth and parameter-integrity will therefore keep the boundary
    # unverified instead of blocking on an invented size.
    return max(1, int(default_width or 1024)), max(1, int(default_height or 1024)), False, "requested_size_fallback"


def build_krea2_anypaint_canvas_contract(
    params: Mapping[str, Any] | None,
    *,
    mode: str,
    default_width: int = 1024,
    default_height: int = 1024,
) -> dict[str, Any]:
    values = dict(params or {})
    source_width, source_height, source_known, source_size_source = _source_size(
        values,
        default_width=default_width,
        default_height=default_height,
    )

    normalized_mode = str(mode or values.get("mode") or "inpaint").strip().lower()
    if normalized_mode == "outpaint":
        payload = normalize_outpaint_payload(values, default_width=source_width, default_height=source_height)
        padding_payload = payload.get("padding") if isinstance(payload.get("padding"), Mapping) else {}
    else:
        payload = {}
        padding_payload = {}

    padding = {
        "left": max(0, _int(padding_payload.get("left"), 0)),
        "top": max(0, _int(padding_payload.get("top"), 0)),
        "right": max(0, _int(padding_payload.get("right"), 0)),
        "bottom": max(0, _int(padding_payload.get("bottom"), 0)),
    }
    raw_width = source_width + padding["left"] + padding["right"]
    raw_height = source_height + padding["top"] + padding["bottom"]
    final_width = align_up(raw_width)
    final_height = align_up(raw_height)
    extra_right = final_width - raw_width
    extra_bottom = final_height - raw_height

    effective_padding = dict(padding)
    effective_padding["right"] += extra_right
    effective_padding["bottom"] += extra_bottom

    return {
        "schema_id": SCHEMA_ID,
        "authority": AUTHORITY,
        "mode": normalized_mode,
        "alignment": ALIGNMENT,
        "source_size": {
            "width": source_width,
            "height": source_height,
            "known": source_known,
            "source": source_size_source,
        },
        "source_resize": {
            "enabled": False,
            "policy": "preserve_original_source_pixels",
        },
        "requested_padding": padding,
        "raw_canvas": {
            "width": raw_width,
            "height": raw_height,
        },
        "alignment_delta": {
            "right": extra_right,
            "bottom": extra_bottom,
        },
        "effective_padding": effective_padding,
        "final_size": {
            "width": final_width,
            "height": final_height,
        },
        "source_placement": {
            "x": padding["left"],
            "y": padding["top"],
            "width": source_width,
            "height": source_height,
        },
        "authoritative": source_known,
        "runtime_note": (
            "Final canvas is authoritative from source metadata and AnyPaint 16px alignment."
            if source_known
            else "Source dimensions were not available at submission time; final canvas is a prediction only and must be verified at runtime."
        ),
    }


def anypaint_canvas_matches(field: str, observed: Any, contract: Mapping[str, Any] | None) -> bool:
    if field not in {"width", "height"} or not isinstance(contract, Mapping):
        return False
    if contract.get("authoritative") is not True:
        return False
    final_size = contract.get("final_size") if isinstance(contract.get("final_size"), Mapping) else {}
    expected = _int(final_size.get(field), 0)
    return expected > 0 and _int(observed, -1) == expected


def anypaint_canvas_transform_reason(field: str, contract: Mapping[str, Any] | None) -> str:
    if not isinstance(contract, Mapping):
        return "intentional AnyPaint canvas transform"
    source = contract.get("source_size") if isinstance(contract.get("source_size"), Mapping) else {}
    padding = contract.get("requested_padding") if isinstance(contract.get("requested_padding"), Mapping) else {}
    raw = contract.get("raw_canvas") if isinstance(contract.get("raw_canvas"), Mapping) else {}
    final_size = contract.get("final_size") if isinstance(contract.get("final_size"), Mapping) else {}
    if field == "width":
        return (
            "intentional AnyPaint canvas width: source "
            f"{_int(source.get('width'))} + left {_int(padding.get('left'))} + right {_int(padding.get('right'))} "
            f"= raw {_int(raw.get('width'))}, aligned to {ALIGNMENT}px = {_int(final_size.get('width'))}"
        )
    return (
        "intentional AnyPaint canvas height: source "
        f"{_int(source.get('height'))} + top {_int(padding.get('top'))} + bottom {_int(padding.get('bottom'))} "
        f"= raw {_int(raw.get('height'))}, aligned to {ALIGNMENT}px = {_int(final_size.get('height'))}"
    )


__all__ = [
    "ALIGNMENT",
    "AUTHORITY",
    "SCHEMA_ID",
    "align_up",
    "build_krea2_anypaint_canvas_contract",
    "anypaint_canvas_matches",
    "anypaint_canvas_transform_reason",
]
