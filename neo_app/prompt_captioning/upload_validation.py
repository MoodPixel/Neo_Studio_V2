from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

try:  # Pillow is optional; header sniffing remains the fallback.
    from PIL import Image, UnidentifiedImageError  # type: ignore
except Exception:  # pragma: no cover - depends on local optional dependency state
    Image = None  # type: ignore
    UnidentifiedImageError = Exception  # type: ignore


ALLOWED_CAPTION_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".bmp": "bmp",
}

MAX_CAPTION_IMAGE_UPLOAD_BYTES = 50 * 1024 * 1024
CAPTION_UPLOAD_VALIDATION_SCHEMA = "neo.prompt_captioning.caption_upload_safety.v1"


@dataclass(slots=True)
class StoredCaptionImageUpload:
    original_filename: str
    stored_filename: str
    path: Path
    suffix: str
    detected_type: str
    size_bytes: int


class CaptionUploadValidationError(ValueError):
    """Raised when a Prompt/Captioning caption upload should be rejected cleanly."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _detect_caption_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    return None


def _verify_caption_image_with_pillow(data: bytes) -> None:
    if Image is None:
        return
    try:
        with Image.open(BytesIO(data)) as image:  # type: ignore[union-attr]
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise CaptionUploadValidationError("Caption image upload is not a valid image file.") from exc


async def validate_and_stage_caption_image_upload(
    file,
    *,
    target_dir: Path,
    prefix: str = "caption_upload",
    default_filename: str = "caption_image.png",
    max_bytes: int = MAX_CAPTION_IMAGE_UPLOAD_BYTES,
) -> StoredCaptionImageUpload:
    """Validate a Caption Studio source image upload and stage it locally.

    Validation mirrors the Image Tab safety policy while staying caption-specific:
    - static PNG/JPG/WEBP/BMP only
    - no empty files
    - max 50 MB by default
    - header sniffing with optional Pillow verification
    - extension/content mismatch rejection before Neo saves the asset
    """

    original = Path(getattr(file, "filename", None) or default_filename).name
    suffix = Path(original).suffix.lower() or Path(default_filename).suffix.lower() or ".png"
    if suffix == ".gif":
        raise CaptionUploadValidationError("Caption image upload does not support GIF yet. Use PNG, JPG, WEBP, or BMP.")
    if suffix not in ALLOWED_CAPTION_IMAGE_EXTENSIONS:
        raise CaptionUploadValidationError("Caption image upload requires PNG, JPG, WEBP, or BMP.")

    data = await file.read(max_bytes + 1)
    if not data:
        raise CaptionUploadValidationError("Caption image upload received an empty file.")
    if len(data) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise CaptionUploadValidationError(f"Caption image is too large. Max allowed: {max_mb} MB.", status_code=413)

    detected_type = _detect_caption_image_type(data)
    if detected_type is None:
        raise CaptionUploadValidationError("Caption image upload is not a valid image file.")
    if detected_type == "gif":
        raise CaptionUploadValidationError("Caption image upload does not support GIF yet. Use PNG, JPG, WEBP, or BMP.")
    expected_type = ALLOWED_CAPTION_IMAGE_EXTENSIONS[suffix]
    if detected_type != expected_type:
        raise CaptionUploadValidationError("Caption image content does not match its file extension.")

    _verify_caption_image_with_pillow(data)

    target_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{prefix}_{uuid4().hex[:12]}{suffix}"
    target_path = target_dir / stored_filename
    target_path.write_bytes(data)
    return StoredCaptionImageUpload(
        original_filename=original,
        stored_filename=stored_filename,
        path=target_path,
        suffix=suffix,
        detected_type=detected_type,
        size_bytes=len(data),
    )
