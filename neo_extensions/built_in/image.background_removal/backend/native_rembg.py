from __future__ import annotations

import importlib.metadata
import io
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageFilter

from .constants import NATIVE_MODEL_IDS, SAM_MODEL_VARIANTS


def native_rembg_status() -> dict[str, Any]:
    try:
        import rembg  # type: ignore  # noqa: F401
    except Exception as exc:
        return {
            "available": False,
            "package": "rembg",
            "version": "",
            "models": list(NATIVE_MODEL_IDS),
            "providers": [],
            "reason": f"Optional rembg runtime is unavailable: {exc}",
            "interactive_sam": {"available": False, "session_model": "sam", "variants": list(SAM_MODEL_VARIANTS), "reason": "rembg is unavailable"},
        }
    version = ""
    try:
        version = importlib.metadata.version("rembg")
    except Exception:
        pass
    providers: list[str] = []
    try:
        import onnxruntime as ort  # type: ignore

        providers = [str(item) for item in ort.get_available_providers()]
    except Exception:
        pass
    return {
        "available": True,
        "package": "rembg",
        "version": version,
        "models": list(NATIVE_MODEL_IDS),
        "providers": providers,
        "reason": "",
        "interactive_sam": {"available": True, "session_model": "sam", "variants": list(SAM_MODEL_VARIANTS), "reason": ""},
    }


def _providers_for_mode(mode: str, available: list[str]) -> list[str] | None:
    selected = str(mode or "AUTO").strip().upper()
    if selected == "CPU":
        return ["CPUExecutionProvider"]
    if selected == "CUDA":
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if "ROCMExecutionProvider" in available:
        return ["ROCMExecutionProvider", "CPUExecutionProvider"]
    return None


def _refine_alpha(alpha: Image.Image, *, threshold: float, expand: int, feather: int) -> Image.Image:
    mask = alpha.convert("L")
    if threshold > 0:
        cutoff = max(0, min(255, int(round(float(threshold) * 255))))
        mask = mask.point(lambda value: 255 if value >= cutoff else 0)
    amount = int(expand or 0)
    if amount:
        size = min(255, max(3, abs(amount) * 2 + 1))
        if size % 2 == 0:
            size += 1
        mask = mask.filter(ImageFilter.MaxFilter(size) if amount > 0 else ImageFilter.MinFilter(size))
    if int(feather or 0) > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=int(feather)))
    return mask


def run_native_rembg(
    source_path: Path,
    *,
    settings: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    status = native_rembg_status()
    if not status.get("available"):
        raise RuntimeError(status.get("reason") or "Native rembg is unavailable.")

    from rembg import new_session, remove  # type: ignore

    model_name = str(settings.get("native_model") or settings.get("resolved_model") or "isnet-general-use").strip()
    if model_name not in NATIVE_MODEL_IDS:
        raise ValueError(f"Unsupported native rembg model: {model_name}")
    providers = _providers_for_mode(str(settings.get("native_provider") or "AUTO"), list(status.get("providers") or []))
    try:
        session = new_session(model_name, providers=providers) if providers else new_session(model_name)
    except TypeError:
        session = new_session(model_name)

    source_bytes = source_path.read_bytes()
    kwargs = {
        "session": session,
        "alpha_matting": bool(settings.get("native_alpha_matting", False)),
        "post_process_mask": bool(settings.get("native_post_process_mask", False)),
    }
    if kwargs["alpha_matting"]:
        kwargs.update({
            "alpha_matting_foreground_threshold": int(settings.get("native_foreground_threshold") or 240),
            "alpha_matting_background_threshold": int(settings.get("native_background_threshold") or 10),
            "alpha_matting_erode_size": int(settings.get("native_erode_size") or 10),
        })
    output_bytes = remove(source_bytes, **kwargs)
    with Image.open(io.BytesIO(output_bytes)) as image:
        rgba = image.convert("RGBA")
        alpha = _refine_alpha(
            rgba.getchannel("A"),
            threshold=float(settings.get("mask_threshold") or 0.0),
            expand=int(settings.get("mask_expand") or 0),
            feather=int(settings.get("mask_feather") or 0),
        )
        rgba.putalpha(alpha)
        output_root.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:12]
        foreground_path = output_root / f"NeoStudioBackgroundRemovedNative_{token}.png"
        mask_path = output_root / f"NeoStudioBackgroundMaskNative_{token}.png"
        rgba.save(foreground_path, format="PNG")
        if settings.get("save_mask", True):
            alpha.save(mask_path, format="PNG")

    outputs = [{
        "kind": "image",
        "filename": foreground_path.name,
        "path": str(foreground_path),
        "role": "foreground",
        "metadata": {
            "background_removal_role": "foreground",
            "engine": "native_rembg",
            "native_model": model_name,
        },
    }]
    if settings.get("save_mask", True):
        outputs.append({
            "kind": "image",
            "filename": mask_path.name,
            "path": str(mask_path),
            "role": "mask",
            "metadata": {
                "background_removal_role": "mask",
                "engine": "native_rembg",
                "native_model": model_name,
            },
        })
    return {
        "outputs": outputs,
        "engine": "native_rembg",
        "model": model_name,
        "runtime": status,
        "notes": [
            f"Native rembg fallback executed with {model_name}.",
            "The ONNX model is downloaded by rembg on first use into its configured model cache.",
        ],
    }
