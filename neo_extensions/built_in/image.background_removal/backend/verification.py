from __future__ import annotations

from pathlib import Path
from typing import Any

from .workflow import FOREGROUND_PREFIX, MASK_PREFIX


def _resolve_path(root_dir: Path, value: Any) -> Path:
    raw = str(value or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = root_dir / path
    return path.resolve()


def _provider_role(output: dict[str, Any], index: int, *, save_mask: bool) -> str:
    name = str(output.get("filename") or output.get("path") or "").casefold()
    if FOREGROUND_PREFIX.casefold() in name:
        return "foreground_rgba"
    if MASK_PREFIX.casefold() in name:
        return "alpha_mask"
    if index == 0:
        return "foreground_rgba"
    if save_mask and index == 1:
        return "alpha_mask"
    return "image"


def _inspect_image(path: Path, *, role: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "role": role,
        "exists": path.exists() and path.is_file(),
        "valid": False,
        "format": "",
        "mode": "",
        "width": 0,
        "height": 0,
        "alpha_min": None,
        "alpha_max": None,
        "mask_min": None,
        "mask_max": None,
        "errors": [],
        "warnings": [],
    }
    if not result["exists"]:
        result["errors"].append("Persisted output file is missing.")
        return result
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            image.load()
            result["format"] = str(image.format or path.suffix.lstrip(".")).upper()
            result["mode"] = str(image.mode or "")
            result["width"], result["height"] = [int(v) for v in image.size]
            if role == "foreground_rgba":
                rgba = image.convert("RGBA")
                alpha = rgba.getchannel("A")
                alpha_min, alpha_max = alpha.getextrema()
                result["alpha_min"] = int(alpha_min)
                result["alpha_max"] = int(alpha_max)
                native_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
                if result["format"] != "PNG":
                    result["errors"].append("Transparent foreground was not persisted as PNG.")
                if not native_alpha:
                    result["errors"].append("Transparent foreground has no native alpha channel.")
                if int(alpha_max) <= 0:
                    result["errors"].append("Foreground alpha is completely empty.")
                if int(alpha_min) >= 255:
                    result["errors"].append("Foreground alpha is completely opaque; background removal was not preserved.")
                result["valid"] = not result["errors"]
            elif role == "alpha_mask":
                mask = image.convert("L")
                mask_min, mask_max = mask.getextrema()
                result["mask_min"] = int(mask_min)
                result["mask_max"] = int(mask_max)
                if result["format"] != "PNG":
                    result["warnings"].append("Alpha mask was not persisted as PNG.")
                if int(mask_max) <= 0:
                    result["errors"].append("Alpha mask is completely empty.")
                elif int(mask_min) == int(mask_max):
                    result["warnings"].append("Alpha mask is flat; inspect the subject extraction before reuse.")
                result["valid"] = not result["errors"]
            else:
                result["valid"] = True
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"Output image could not be inspected: {exc}")
    return result


def verify_background_removal_outputs(
    *,
    root_dir: Path,
    provider_outputs: list[dict[str, Any]] | None,
    persisted_files: list[dict[str, Any]] | None,
    save_mask: bool,
) -> dict[str, Any]:
    """Verify BiRefNet outputs after Neo persistence and annotate file roles.

    Comfy may return multiple SaveImage outputs. The workflow records the RGBA
    foreground first and the optional grayscale mask second; filename prefixes
    are used when available so the contract remains stable if output ordering
    changes in a later Comfy release.
    """
    provider_items = [item for item in (provider_outputs or []) if isinstance(item, dict)]
    files = [item for item in (persisted_files or []) if isinstance(item, dict)]
    inspections: list[dict[str, Any]] = []
    foreground_found = False
    mask_found = False

    for index, file_record in enumerate(files):
        provider_output = provider_items[index] if index < len(provider_items) else {}
        role = _provider_role(provider_output, index, save_mask=save_mask)
        if role == "foreground_rgba":
            foreground_found = True
        elif role == "alpha_mask":
            mask_found = True
        file_record["role"] = role
        file_record.setdefault("metadata", {})["background_removal_role"] = role
        inspection = _inspect_image(_resolve_path(root_dir, file_record.get("path")), role=role)
        file_record["metadata"]["background_removal_verification"] = {
            key: value for key, value in inspection.items() if key not in {"path", "errors", "warnings"}
        }
        inspections.append(inspection)

    errors: list[str] = []
    warnings: list[str] = []
    for item in inspections:
        label = "foreground" if item.get("role") == "foreground_rgba" else ("mask" if item.get("role") == "alpha_mask" else "output")
        errors.extend([f"{label}: {message}" for message in item.get("errors") or []])
        warnings.extend([f"{label}: {message}" for message in item.get("warnings") or []])
    if not foreground_found:
        errors.append("Transparent foreground output was not found.")
    if save_mask and not mask_found:
        warnings.append("Alpha-mask output was requested but was not found.")

    status = "failed" if errors else ("warning" if warnings else "passed")
    return {
        "schema_version": "neo.image.background_removal_verification.v1",
        "status": status,
        "ok": not errors,
        "foreground_found": foreground_found,
        "mask_requested": bool(save_mask),
        "mask_found": mask_found,
        "files": inspections,
        "errors": errors,
        "warnings": warnings,
        "policy": "verify_rgba_png_and_optional_mask_after_neo_persistence",
    }
