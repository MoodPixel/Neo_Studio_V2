from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageOps


SOURCE_VISIBILITY_MASK_SCHEMA = "neo.image.source_visibility_mask.v1"
SUPPORTED_FILL_MODES = {"black", "white", "mid_gray"}
FILL_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "mid_gray": (127, 127, 127),
}


class SourceVisibilityMaskError(ValueError):
    """Raised when a visibility mask cannot be applied safely."""


@dataclass(frozen=True)
class SourceVisibilityMaskResult:
    output_path: Path
    metadata: dict[str, Any]


def _first_present(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def normalize_source_visibility_mask(raw: Any) -> dict[str, Any]:
    """Normalize a direct-source or Stitch-input visibility-mask payload.

    White mask pixels are hidden. Black mask pixels preserve the source. The
    output is flattened to RGB before backend handoff because model image tensors
    do not have a portable alpha-channel contract across Neo's Comfy workflows.
    """

    values = raw if isinstance(raw, Mapping) else {}
    reference = _first_present(values, "path", "ref", "mask", "url", "filename", "name", "file")
    name = _first_present(values, "name", "filename", "file", "path", "ref", "url")
    fill_mode = str(_first_present(values, "fill_mode", "fill", "background") or "black").strip().lower()
    if fill_mode not in SUPPORTED_FILL_MODES:
        fill_mode = "black"
    enabled = _as_bool(values.get("enabled"), bool(reference)) and bool(reference)
    return {
        "schema": str(values.get("schema") or SOURCE_VISIBILITY_MASK_SCHEMA),
        "enabled": enabled,
        "ref": str(reference or "").strip(),
        "path": str(values.get("path") or "").strip(),
        "url": str(values.get("url") or "").strip(),
        "name": str(name or "").strip(),
        "preview_url": str(values.get("preview_url") or values.get("url") or "").strip(),
        "fill_mode": fill_mode,
        "white_hides": True,
    }


def source_visibility_mask_reference(raw: Any) -> str:
    return str(normalize_source_visibility_mask(raw).get("ref") or "").strip()


def _stable_output_name(source_path: Path, mask_path: Path, fill_mode: str) -> str:
    source_stat = source_path.stat()
    mask_stat = mask_path.stat()
    digest = sha256(
        "|".join(
            [
                str(source_path.resolve()),
                str(source_stat.st_mtime_ns),
                str(source_stat.st_size),
                str(mask_path.resolve()),
                str(mask_stat.st_mtime_ns),
                str(mask_stat.st_size),
                fill_mode,
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in source_path.stem)[:48] or "source"
    return f"visibility_{safe_stem}_{digest}.png"


def apply_source_visibility_mask(
    source_path: Path | str,
    mask_path: Path | str,
    *,
    output_dir: Path | str,
    fill_mode: str = "black",
) -> SourceVisibilityMaskResult:
    """Flatten a source image with a user-painted visibility mask.

    White mask pixels are replaced by the selected neutral fill. Black mask
    pixels keep the original source. Dimensions must match exactly so a stale or
    unrelated mask can never be silently stretched over another image.
    """

    source = Path(source_path).expanduser()
    mask = Path(mask_path).expanduser()
    output_root = Path(output_dir).expanduser()
    normalized_fill = str(fill_mode or "black").strip().lower()
    if normalized_fill not in SUPPORTED_FILL_MODES:
        normalized_fill = "black"
    if not source.exists() or not source.is_file():
        raise SourceVisibilityMaskError(f"Source image does not exist: {source}")
    if not mask.exists() or not mask.is_file():
        raise SourceVisibilityMaskError(f"Visibility mask does not exist: {mask}")

    try:
        with Image.open(source) as source_image, Image.open(mask) as mask_image:
            source_rgb = source_image.convert("RGB")
            mask_l = mask_image.convert("L")
            if source_rgb.size != mask_l.size:
                raise SourceVisibilityMaskError(
                    f"Visibility mask dimensions {mask_l.width}x{mask_l.height} do not match source dimensions {source_rgb.width}x{source_rgb.height}."
                )
            keep_mask = ImageOps.invert(mask_l)
            neutral = Image.new("RGB", source_rgb.size, FILL_COLORS[normalized_fill])
            flattened = Image.composite(source_rgb, neutral, keep_mask)
            output_root.mkdir(parents=True, exist_ok=True)
            output_path = output_root / _stable_output_name(source, mask, normalized_fill)
            flattened.save(output_path, format="PNG", optimize=True)
    except SourceVisibilityMaskError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SourceVisibilityMaskError(f"Could not apply source visibility mask: {exc}") from exc

    metadata = {
        "schema": SOURCE_VISIBILITY_MASK_SCHEMA,
        "applied": True,
        "source_name": source.name,
        "mask_name": mask.name,
        "output_name": output_path.name,
        "output_path": str(output_path),
        "width": source_rgb.width,
        "height": source_rgb.height,
        "fill_mode": normalized_fill,
        "white_hides": True,
        "alpha_contract": "flattened_rgb",
    }
    return SourceVisibilityMaskResult(output_path=output_path, metadata=metadata)
