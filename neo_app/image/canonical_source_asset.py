"""Canonical validation and resolution for Image source handoffs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib import parse

from .upload_validation import ALLOWED_IMAGE_EXTENSIONS, _detect_image_type

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore


SCHEMA_ID = "neo.image.canonical_source_asset.v1"


class CanonicalSourceAssetError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _allowed_roots(root_dir: Path) -> list[Path]:
    return [
        (root_dir / "neo_data" / "outputs" / "image").resolve(),
        (root_dir / "neo_data" / "inputs" / "image").resolve(),
    ]


def _path_from_output_url(url: str) -> Path | None:
    parsed = parse.urlparse(url)
    if parsed.path != "/api/image/output-file":
        return None
    query = parse.parse_qs(parsed.query)
    result_id = _text(*(query.get("result_id") or []))
    file_id = _text(*(query.get("file_id") or []))
    if not result_id or not file_id:
        raise CanonicalSourceAssetError("source_output_url_incomplete", "Saved-output source URL is missing result_id or file_id.")
    try:
        from .output_service import resolve_output_file

        return resolve_output_file(result_id, file_id).resolve()
    except Exception as exc:
        raise CanonicalSourceAssetError("source_output_not_found", "The selected saved output no longer exists in Neo_Data.") from exc


def _resolve_path(source: dict[str, Any], root_dir: Path) -> tuple[Path, str]:
    result_id = _text(source.get("result_id"))
    file_id = _text(source.get("file_id"), source.get("output_id"))
    if result_id and file_id:
        try:
            from .output_service import resolve_output_file

            return resolve_output_file(result_id, file_id).resolve(), "result_file_identity"
        except Exception:
            pass

    url = _text(source.get("url"), source.get("view_url"), source.get("source_image_url"))
    if url:
        output_path = _path_from_output_url(url)
        if output_path is not None:
            return output_path, "output_file_url"
        parsed = parse.urlparse(url)
        if parsed.path.startswith("/api/image/source-file/"):
            name = Path(parsed.path).name
            return (root_dir / "neo_data" / "inputs" / "image" / name).resolve(), "source_file_url"

    raw_path = _text(source.get("path"), source.get("saved_path"), source.get("source_image_path"))
    if not raw_path:
        raise CanonicalSourceAssetError("source_ref_missing", "The selected output has no reusable Neo source reference.")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root_dir / candidate
    return candidate.resolve(), "neo_path"


def _dimensions(path: Path, data: bytes) -> tuple[int, int]:
    if Image is None:
        return 0, 0
    try:
        with Image.open(path) as image:  # type: ignore[union-attr]
            image.verify()
        with Image.open(path) as image:  # type: ignore[union-attr]
            return int(image.width), int(image.height)
    except Exception as exc:
        raise CanonicalSourceAssetError("source_image_invalid", "The selected source is not a readable image.") from exc


def resolve_canonical_source_asset(source: dict[str, Any] | None, *, root_dir: str | Path | None = None) -> dict[str, Any]:
    """Resolve one Neo-owned source into a provider-neutral, validated asset."""
    record = source if isinstance(source, dict) else {}
    if root_dir is None:
        from .output_service import ROOT_DIR

        root = Path(ROOT_DIR).resolve()
    else:
        root = Path(root_dir).resolve()
    path, resolution = _resolve_path(record, root)
    if not _inside(path, _allowed_roots(root)):
        raise CanonicalSourceAssetError("source_outside_neo_storage", "The selected source is outside Neo-owned Image storage.")
    if not path.exists() or not path.is_file():
        raise CanonicalSourceAssetError("source_file_unreadable", "The selected source file is missing or unreadable.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CanonicalSourceAssetError("source_file_unreadable", "The selected source file is missing or unreadable.") from exc
    detected_type = _detect_image_type(data)
    if detected_type is None or path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise CanonicalSourceAssetError("source_type_unsupported", "The selected source must be PNG, JPEG, WEBP, or BMP.")
    width, height = _dimensions(path, data)
    if width <= 0 or height <= 0:
        raise CanonicalSourceAssetError("source_dimensions_invalid", "The selected source has invalid image dimensions.")
    stable_seed = "|".join([
        _text(record.get("result_id")),
        _text(record.get("file_id"), record.get("output_id")),
        str(path.relative_to(root)),
    ])
    stable_id = "source_" + hashlib.sha256(stable_seed.encode("utf-8")).hexdigest()[:24]
    return {
        "schema": SCHEMA_ID,
        "source_id": stable_id,
        "result_id": _text(record.get("result_id")),
        "file_id": _text(record.get("file_id"), record.get("output_id")),
        "filename": path.name,
        "path": str(path),
        "url": _text(record.get("url"), record.get("view_url"), record.get("source_image_url")),
        "width": width,
        "height": height,
        "detected_type": detected_type,
        "size_bytes": len(data),
        "storage": "neo_owned_image",
        "resolution": resolution,
        "provider_upload_ready": True,
        "validated": True,
    }
