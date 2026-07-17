from __future__ import annotations

"""Trusted Neo-owned image asset bridges for approved external extensions."""

from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from neo_app.image.upload_validation import validate_and_store_image_upload


EXTENSION_IMAGE_ASSET_ROLES = {
    "source_image",
    "mask_image",
    "background_image",
    "depth_image",
    "light_map_image",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_existing_input(asset_id: str, root: Path, role: str) -> Path:
    safe_name = Path(asset_id).name
    if not safe_name or safe_name != asset_id:
        raise ValueError(f"Invalid {role} asset id.")
    base = root.resolve()
    path = (base / safe_name).resolve()
    if base not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Neo-owned {role} asset was not found.")
    return path


def resolve_external_extension_image_asset(
    descriptor: Mapping[str, Any],
    *,
    source_dir: Path,
    mask_dir: Path,
    output_record_loader: Callable[..., dict[str, Any]],
    output_file_resolver: Callable[[str, str], Path],
) -> dict[str, Any]:
    """Resolve a public asset id to one trusted internal path.

    The returned path is intentionally an internal service value. External
    routers must remove it from API responses and persisted job context.
    """
    data = _mapping(descriptor)
    role = _clean_text(data.get("role"))
    if role not in EXTENSION_IMAGE_ASSET_ROLES:
        raise ValueError(f"Unsupported external extension image role: {role or 'empty'}")
    source_mode = _clean_text(data.get("source_mode")) or "upload"

    if role == "source_image" and source_mode in {"selected_result", "current_preview"}:
        result_id = _clean_text(data.get("result_id"))
        if not result_id:
            raise ValueError(f"{source_mode} requires a Neo result id.")
        record = output_record_loader(result_id)
        outputs = _mapping(record.get("outputs"))
        files = [item for item in (outputs.get("files") or []) if isinstance(item, Mapping)]
        file_id = _clean_text(data.get("file_id")) or _clean_text(outputs.get("active_file"))
        if not file_id and files:
            file_id = _clean_text(files[0].get("file_id"))
        if not file_id:
            raise FileNotFoundError(f"Neo result '{result_id}' has no image output file.")
        path = output_file_resolver(result_id, file_id).resolve()
        return {
            "role": role,
            "source_mode": source_mode,
            "asset_id": f"{result_id}:{file_id}",
            "result_id": result_id,
            "file_id": file_id,
            "filename": path.name,
            "path": path,
            "url": f"/api/image/output-file?result_id={quote(result_id, safe='')}&file_id={quote(file_id, safe='')}",
            "storage": "neo_data/outputs/image",
        }

    asset_id = _clean_text(data.get("asset_id"))
    if not asset_id:
        raise ValueError(f"{role} requires a Neo staged asset id.")
    target_dir = mask_dir if role == "mask_image" else source_dir
    path = _safe_existing_input(asset_id, target_dir, role)
    is_mask = role == "mask_image"
    return {
        "role": role,
        "source_mode": "upload",
        "asset_id": asset_id,
        "filename": path.name,
        "path": path,
        "url": f"/api/image/{'mask' if is_mask else 'source'}-file/{quote(asset_id, safe='')}",
        "storage": "neo_data/inputs/image_masks" if is_mask else "neo_data/inputs/image",
    }


async def stage_external_extension_image_upload(
    file: Any,
    *,
    role: str,
    source_dir: Path,
    mask_dir: Path,
) -> dict[str, Any]:
    """Validate and stage one extension upload under Neo-owned input storage."""
    clean_role = _clean_text(role)
    if clean_role not in EXTENSION_IMAGE_ASSET_ROLES:
        raise ValueError(f"Unsupported external extension image role: {clean_role or 'empty'}")
    is_mask = clean_role == "mask_image"
    stored = await validate_and_store_image_upload(
        file,
        target_dir=mask_dir if is_mask else source_dir,
        prefix=f"external_{clean_role}",
        default_filename="mask.png" if is_mask else "source.png",
        label="mask" if is_mask else "source",
        repair_extension_mismatch=not is_mask,
    )
    return {
        "role": clean_role,
        "source_mode": "upload",
        "asset_id": stored.stored_filename,
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "path": stored.path,
        "url": f"/api/image/{'mask' if is_mask else 'source'}-file/{quote(stored.stored_filename, safe='')}",
        "storage": "neo_data/inputs/image_masks" if is_mask else "neo_data/inputs/image",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "extension_repaired": stored.extension_repaired,
        "validation": "image_upload_safety_v1",
    }


__all__ = [
    "EXTENSION_IMAGE_ASSET_ROLES",
    "resolve_external_extension_image_asset",
    "stage_external_extension_image_upload",
]
