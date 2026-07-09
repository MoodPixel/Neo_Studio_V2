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


ALLOWED_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".bmp": "bmp",
}

MAX_IMAGE_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass(slots=True)
class StoredImageUpload:
    original_filename: str
    stored_filename: str
    path: Path
    suffix: str
    detected_type: str
    size_bytes: int
    extension_repaired: bool = False


class ImageUploadValidationError(ValueError):
    """Raised when an Image Tab upload should be rejected with a clean message."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return None


def canonical_image_suffix_for_type(detected_type: str) -> str:
    """Return Neo's preferred extension for a sniffed image type."""

    if detected_type == "jpeg":
        return ".jpg"
    if detected_type in {"png", "webp", "bmp"}:
        return f".{detected_type}"
    return ".png"


def _verify_with_pillow(data: bytes) -> None:
    if Image is None:
        return
    try:
        with Image.open(BytesIO(data)) as image:  # type: ignore[union-attr]
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageUploadValidationError("Upload is not a valid image file.") from exc


async def validate_and_store_image_upload(
    file,
    *,
    target_dir: Path,
    prefix: str,
    default_filename: str,
    label: str,
    max_bytes: int = MAX_IMAGE_UPLOAD_BYTES,
    repair_extension_mismatch: bool = False,
) -> StoredImageUpload:
    """Validate an Image Tab upload and store it under a Neo-owned folder.

    Validation is intentionally local and conservative:
    - normal uploads keep the PNG/JPG/WEBP/BMP extension/content match check
    - extensionless/generic drag payloads may fall back to header-sniffed type
    - payload must be non-empty and below the configured size cap
    - file bytes must look like an image, with Pillow verification when available
    """

    original = Path(getattr(file, "filename", None) or default_filename).name
    raw_suffix = Path(original).suffix.lower()
    fallback_suffix = Path(default_filename).suffix.lower() or ".png"

    data = await file.read(max_bytes + 1)
    if not data:
        raise ImageUploadValidationError(f"The {label} image upload is empty.")
    if len(data) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise ImageUploadValidationError(f"The {label} image is too large. Max allowed: {max_mb} MB.", status_code=413)

    detected_type = _detect_image_type(data)
    if detected_type is None:
        raise ImageUploadValidationError("Upload is not a valid image file.")

    suffix = raw_suffix or fallback_suffix
    extension_repaired = False
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        # Some Neo/Comfy-generated images may drag from Windows Explorer with an
        # empty or generic filename/MIME even when the payload bytes are a real
        # PNG/JPEG/WEBP/BMP. Trust the already-sniffed image header in that case
        # and store under a canonical extension instead of rejecting a valid
        # generated output as an unsupported format.
        suffix = canonical_image_suffix_for_type(detected_type)
        extension_repaired = True
    else:
        expected_type = ALLOWED_IMAGE_EXTENSIONS[suffix]
        if detected_type != expected_type:
            # Source/reference/video source uploads are user-facing creative inputs.
            # Neo/Comfy/cloud outputs can be mislabeled (for example JPEG bytes
            # saved as .png), so trust the sniffed image content and repair the
            # stored extension instead of blocking the workflow. Masks remain
            # strict unless their caller explicitly opts into repair.
            repairable_label = str(label or "").strip().lower() in {
                "source",
                "reference",
                "video source",
                "layerdiffuse source",
                "layerdiffuse foreground",
                "layerdiffuse background",
            }
            if not repair_extension_mismatch and not repairable_label:
                raise ImageUploadValidationError(
                    f"The {label} image content does not match its file extension."
                )
            suffix = canonical_image_suffix_for_type(detected_type)
            extension_repaired = True
    _verify_with_pillow(data)

    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{prefix}_{uuid4().hex[:12]}{suffix}"
    target = target_dir / safe_name
    target.write_bytes(data)
    return StoredImageUpload(
        original_filename=original,
        stored_filename=safe_name,
        path=target,
        suffix=suffix,
        detected_type=detected_type,
        size_bytes=len(data),
        extension_repaired=extension_repaired,
    )
