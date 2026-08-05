from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

FORGE_OUTPAINT_SCHEMA_ID = "neo.provider.forge_outpaint_canvas.v1"
FORGE_OUTPAINT_VERSION = "1.0.0"


def _int_value(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = 8192) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(maximum, resolved))


def _decode_data_uri(value: str) -> Image.Image:
    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise ValueError("Invalid source image data URI.") from exc
    if not header.startswith("data:image/"):
        raise ValueError("Outpaint source must be an image data URI.")
    try:
        raw = base64.b64decode(encoded)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - normalize provider-facing image errors.
        raise ValueError("Unable to decode the outpaint source image.") from exc


def _load_image(value: Any) -> tuple[Image.Image, str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Forge outpaint requires a source image.")
    if text.startswith("data:image/"):
        return _decode_data_uri(text), "data_uri"
    path = Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"Outpaint source image does not exist: {path.name or text}")
    try:
        return Image.open(path).convert("RGB"), "neo_owned_file"
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unable to read outpaint source image: {path.name}") from exc


def _encode_png(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _padding(params: dict[str, Any]) -> dict[str, int]:
    raw = params.get("outpaint_padding")
    values: dict[str, Any] = raw if isinstance(raw, dict) else {}
    scalar = raw if isinstance(raw, (int, float, str)) and str(raw).strip() else None

    def read(side: str) -> int:
        aliases = (
            f"outpaint_{side}",
            f"outpaint_padding_{side}",
            f"padding_{side}",
            side,
        )
        for key in aliases:
            if key in params and params.get(key) not in {None, ""}:
                return _int_value(params.get(key))
            if key in values and values.get(key) not in {None, ""}:
                return _int_value(values.get(key))
        return _int_value(scalar) if scalar is not None else 0

    return {side: read(side) for side in ("left", "right", "top", "bottom")}


def _edge_expand(image: Image.Image, *, left: int, right: int, top: int, bottom: int) -> Image.Image:
    width, height = image.size
    canvas = Image.new("RGB", (width + left + right, height + top + bottom))
    canvas.paste(image, (left, top))

    if top:
        canvas.paste(image.crop((0, 0, width, 1)).resize((width, top)), (left, 0))
    if bottom:
        canvas.paste(image.crop((0, height - 1, width, height)).resize((width, bottom)), (left, top + height))
    if left:
        canvas.paste(image.crop((0, 0, 1, height)).resize((left, height)), (0, top))
    if right:
        canvas.paste(image.crop((width - 1, 0, width, height)).resize((right, height)), (left + width, top))

    if left and top:
        canvas.paste(Image.new("RGB", (left, top), image.getpixel((0, 0))), (0, 0))
    if right and top:
        canvas.paste(Image.new("RGB", (right, top), image.getpixel((width - 1, 0))), (left + width, 0))
    if left and bottom:
        canvas.paste(Image.new("RGB", (left, bottom), image.getpixel((0, height - 1))), (0, top + height))
    if right and bottom:
        canvas.paste(Image.new("RGB", (right, bottom), image.getpixel((width - 1, height - 1))), (left + width, top + height))
    return canvas


def compile_forge_outpaint_canvas(
    source: Any,
    params: dict[str, Any],
    *,
    resolution_step: int = 64,
) -> dict[str, Any]:
    """Build a Forge img2img-compatible expanded canvas and mask.

    The generated mask is white in the synthesized area and black in the
    protected source interior. A configurable overlap band lets Forge blend the
    seam while keeping the center of the source image stable.
    """

    image, source_kind = _load_image(source)
    requested = _padding(params)
    if not any(requested.values()):
        raise ValueError("Forge outpaint requires at least one positive padding side.")

    step = _int_value(resolution_step, 64, minimum=8, maximum=512)
    requested_width = image.width + requested["left"] + requested["right"]
    requested_height = image.height + requested["top"] + requested["bottom"]
    aligned_width = max(step, ((requested_width + step - 1) // step) * step)
    aligned_height = max(step, ((requested_height + step - 1) // step) * step)
    alignment_right = aligned_width - requested_width
    alignment_bottom = aligned_height - requested_height
    actual = dict(requested)
    actual["right"] += alignment_right
    actual["bottom"] += alignment_bottom

    canvas = _edge_expand(image, **actual)
    overlap = _int_value(
        params.get("outpaint_overlap", params.get("outpaint_mask_overlap", 32)),
        32,
        minimum=0,
        maximum=max(image.width, image.height),
    )
    max_overlap = max(0, min(image.width // 2, image.height // 2) - 1)
    overlap = min(overlap, max_overlap)

    source_left = actual["left"]
    source_top = actual["top"]
    protected_left = source_left + (overlap if actual["left"] else 0)
    protected_top = source_top + (overlap if actual["top"] else 0)
    protected_right = source_left + image.width - (overlap if actual["right"] else 0)
    protected_bottom = source_top + image.height - (overlap if actual["bottom"] else 0)

    mask = Image.new("L", canvas.size, 255)
    if protected_right > protected_left and protected_bottom > protected_top:
        ImageDraw.Draw(mask).rectangle(
            (protected_left, protected_top, protected_right - 1, protected_bottom - 1),
            fill=0,
        )

    return {
        "schema_id": FORGE_OUTPAINT_SCHEMA_ID,
        "version": FORGE_OUTPAINT_VERSION,
        "source_kind": source_kind,
        "source_size": {"width": image.width, "height": image.height},
        "requested_padding": requested,
        "actual_padding": actual,
        "alignment_padding": {"right": alignment_right, "bottom": alignment_bottom},
        "requested_canvas": {"width": requested_width, "height": requested_height},
        "canvas": {"width": canvas.width, "height": canvas.height},
        "overlap": overlap,
        "init_image": _encode_png(canvas),
        "mask": _encode_png(mask),
        "policy": {
            "mask_white_is_generation_area": True,
            "source_interior_is_protected": True,
            "edge_pixels_seed_expanded_canvas": True,
            "alignment_extends_right_and_bottom": True,
            "absolute_source_paths_are_not_serialized": True,
        },
    }
