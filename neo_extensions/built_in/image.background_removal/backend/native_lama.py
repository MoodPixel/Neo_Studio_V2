"""Pinned LaMa FP32 adapter. White removes; unmasked pixels and alpha are retained."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from neo_app.image.onnx_runtime import open_session, runtime_status

MODEL_ID = "carve-lama-fp32-opset17"
REVISION = "c3c0c9e468934d62e79c329e35d82dd09ff8c444"
SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
SIZE_BYTES = 208044816
MODEL_URL = f"https://huggingface.co/Carve/LaMa-ONNX/resolve/{REVISION}/lama_fp32.onnx"
MODEL_RELATIVE_PATH = "neo_data/models/lama/lama_fp32.onnx"
TILE = 512
MAX_PIXELS = 16_777_216
PROVIDERS = ("CPUExecutionProvider", "CUDAExecutionProvider", "DmlExecutionProvider")


def model_path(root_dir: Path) -> Path:
    configured = os.environ.get("NEO_LAMA_MODEL_PATH", "").strip()
    return Path(configured).expanduser().resolve() if configured else root_dir / MODEL_RELATIVE_PATH


def status(root_dir: Path) -> dict:
    runtime = runtime_status()
    path = model_path(root_dir)
    installed = path.is_file()
    return {**runtime, "model_id": MODEL_ID, "installed": installed,
            "providers": [provider for provider in runtime["providers"] if provider in PROVIDERS],
            "model_location": str(path), "model_url": MODEL_URL,
            "sha256": SHA256, "license": "Apache-2.0",
            "ready_to_try": installed and runtime["available"],
            "execution_verified": False,
            "reason": runtime["reason"] or ("" if installed else "Install the pinned LaMa model; see guides/01_IMAGE/image2_fast_fill.md.")}


def verify_model(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError("LaMa model is missing. Run scripts/install_lama_fast_fill.py, then refresh Fast Fill.")
    if path.stat().st_size != SIZE_BYTES:
        raise RuntimeError("LaMa model size mismatch. Install the pinned lama_fp32.onnx artifact.")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != SHA256:
        raise RuntimeError("LaMa SHA-256 mismatch. The file is not the supported export.")


def validate_contract(session) -> str:
    def matches(actual, expected):
        return len(actual) == 4 and actual[0] in (1, "batch") and list(actual[1:]) == expected[1:]

    inputs = {item.name: item for item in session.get_inputs()}
    for name, shape in {"image": [1, 3, TILE, TILE], "mask": [1, 1, TILE, TILE]}.items():
        node = inputs.get(name)
        if node is None or node.type != "tensor(float)" or not matches(node.shape, shape):
            raise RuntimeError(f"Unsupported LaMa input contract: {name} must be float32 {shape}.")
    outputs = session.get_outputs()
    if len(inputs) != 2 or len(outputs) != 1 or not matches(outputs[0].shape, [1, 3, TILE, TILE]):
        raise RuntimeError("Unsupported LaMa output contract.")
    # The pinned export returns floats in pixel units (0–255), not normalized RGB.
    if outputs[0].type != "tensor(float)":
        raise RuntimeError("Unsupported LaMa output dtype.")
    return outputs[0].name


def normalize_options(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Fast Fill settings must be an object.")
    result = {"provider": str(raw.get("provider", "CPUExecutionProvider"))}
    if result["provider"] not in PROVIDERS:
        raise ValueError("Fast Fill supports CPU, CUDA, or DirectML providers only.")
    for name, default, minimum, maximum in [("overlap", 128, 32, 256), ("feather", 0, 0, 32), ("grow", 0, 0, 64)]:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
        result[name] = value
    return result


def tile_starts(length: int, overlap: int) -> list[int]:
    last = max(0, length - TILE)
    return sorted(set([*range(0, last + 1, TILE - overlap), last]))


def fill_pixels(source, mask, session, options: dict):
    """No source resizing; feather expands the effective editable region."""
    import numpy as np
    from PIL import Image, ImageFilter

    if source.size != mask.size:
        raise ValueError("Mask dimensions must exactly match the source. Repaint on this source; masks are never stretched.")
    width, height = source.size
    if width * height > MAX_PIXELS:
        raise ValueError("Fast Fill supports up to 16 megapixels per image.")
    rgb = np.array(source.convert("RGB"))
    # Use visible luminance; transparent white must not remove content.
    mask_rgba = np.array(mask.convert("RGBA"))
    luminance = np.array(mask.convert("L"))
    binary = (luminance >= 128) & (mask_rgba[:, :, 3] >= 128)
    if not binary.any():
        raise ValueError("The removal mask is empty. Paint white over the object.")
    if binary.all():
        raise ValueError("The removal mask covers the entire image. Leave some background for LaMa to use.")
    effective = Image.fromarray(binary.astype("uint8") * 255)
    if options["grow"]:
        effective = effective.filter(ImageFilter.MaxFilter(2 * options["grow"] + 1))
    if options["feather"]:
        effective = effective.filter(ImageFilter.GaussianBlur(options["feather"]))
        # Feather outward without weakening the originally selected object.
        effective = Image.fromarray(np.maximum(np.array(effective), binary.astype("uint8") * 255))
    alpha = np.array(effective).astype("float32") / 255
    removal = alpha > 0
    if removal.all():
        raise ValueError("Grow/feather leaves no background context. Reduce it or paint a smaller mask.")
    if session is None:
        raise RuntimeError("LaMa session is unavailable.")
    output_name = validate_contract(session)
    total = np.zeros((height, width, 3), dtype="float32")
    weights = np.zeros((height, width), dtype="float32")
    ramp = np.minimum(np.arange(TILE) + 1, TILE - np.arange(TILE)).astype("float32")
    window = np.minimum(ramp / options["overlap"], 1)
    blend = window[:, None] * window[None, :]
    count = 0
    for y in tile_starts(height, options["overlap"]):
        for x in tile_starts(width, options["overlap"]):
            h, w = min(TILE, height - y), min(TILE, width - x)
            area = removal[y:y+h, x:x+w]
            if not area.any():
                continue
            image_tile = np.pad(rgb[y:y+h, x:x+w], ((0, TILE-h), (0, TILE-w), (0, 0)), mode="edge")
            mask_tile = np.pad(area, ((0, TILE-h), (0, TILE-w)), mode="edge")
            values = session.run([output_name], {
                "image": np.ascontiguousarray(image_tile.transpose(2, 0, 1)[None], dtype="float32") / 255,
                "mask": np.ascontiguousarray(mask_tile[None, None], dtype="float32"),
            })[0]
            if values.shape != (1, 3, TILE, TILE) or not np.isfinite(values).all():
                raise RuntimeError("LaMa returned invalid pixels or dimensions.")
            pixels = np.clip(values[0].transpose(1, 2, 0)[:h, :w], 0, 255)
            weighted = blend[:h, :w] * area
            total[y:y+h, x:x+w] += pixels * weighted[:, :, None]
            weights[y:y+h, x:x+w] += weighted
            count += 1
    generated = total / np.maximum(weights[:, :, None], 1e-12)
    output = rgb.copy()
    mixed = np.rint(generated * alpha[:, :, None] + rgb * (1 - alpha[:, :, None])).astype("uint8")
    output[removal] = mixed[removal]
    result = Image.fromarray(output)
    if "A" in source.getbands() or "transparency" in source.info:
        result.putalpha(source.convert("RGBA").getchannel("A"))
    return result, effective, count


def run(source_path: Path, mask_path: Path, *, root_dir: Path, output_root: Path, settings: dict) -> dict:
    from PIL import Image, ImageOps

    options = normalize_options(settings)
    path = model_path(root_dir)
    verify_model(path)
    session, runtime = open_session(path, options["provider"])
    with Image.open(source_path) as opened, Image.open(mask_path) as mask:
        source = ImageOps.exif_transpose(opened)
        result, effective, count = fill_pixels(source, ImageOps.exif_transpose(mask), session, options)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path, mask_output = output_root / "fast_fill.png", output_root / "fast_fill_mask.png"
    result.save(output_path)
    effective.save(mask_output)
    evidence = {"schema": "neo.image.fast_fill.v1", "engine": "native_lama", "model_id": MODEL_ID,
                "revision": REVISION, "sha256": SHA256, "runtime_version": runtime["version"],
                "requested_provider": options["provider"], "actual_providers": session.get_providers(),
                "execution_verified": True, "fallback_used": False, "tile_count": count,
                "tile_size": TILE, "width": result.width, "height": result.height,
                "mask_convention": "white_removes", "preserves_source_alpha": True, **options}
    outputs = [{"kind": "image", "path": str(file), "filename": file.name, "role": role,
                "metadata": {"fast_fill": evidence, "background_removal_role": role}}
               for file, role in [(output_path, "filled_image"), (mask_output, "mask")]]
    return {"outputs": outputs, "runtime": evidence}
