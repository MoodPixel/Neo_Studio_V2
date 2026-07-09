from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from re import sub
from typing import Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
NEO_DATA_DIR: Final[Path] = ROOT_DIR / "neo_data"
IMAGE_OUTPUT_ROOT: Final[Path] = NEO_DATA_DIR / "outputs" / "image"
IMAGE_METADATA_ROOT: Final[Path] = NEO_DATA_DIR / "outputs" / "image_metadata"

IMAGE_OUTPUT_CATEGORIES: Final[tuple[str, ...]] = (
    "generate",
    "img2img",
    "inpaint",
    "outpaint",
    "upscale",
    "edit",
    "batch",
    "uncategorized",
)

MODE_TO_CATEGORY: Final[dict[str, str]] = {
    "generate": "generate",
    "txt2img": "generate",
    "text_to_image": "generate",
    "img2img": "img2img",
    "image_to_image": "img2img",
    "inpaint": "inpaint",
    "outpaint": "outpaint",
    "upscale": "upscale",
    "edit": "edit",
    "batch": "batch",
}


@dataclass(frozen=True)
class ImageOutputPaths:
    """Canonical Neo-owned paths for one image output category."""

    category: str
    output_dir: Path
    metadata_dir: Path

    @property
    def relative_output_dir(self) -> str:
        return _relative_to_root(self.output_dir)

    @property
    def relative_metadata_dir(self) -> str:
        return _relative_to_root(self.metadata_dir)

    def ensure(self) -> "ImageOutputPaths":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        return self

    def image_file(self, filename: str) -> Path:
        return safe_join(self.output_dir, filename)

    def metadata_file(self, stem: str, suffix: str = ".json") -> Path:
        clean_stem = sanitize_path_part(stem).removesuffix(".json") or "output"
        clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return self.metadata_dir / f"{clean_stem}{clean_suffix}"


def sanitize_path_part(value: str | None, fallback: str = "output") -> str:
    """Return a filesystem-safe single path segment."""
    raw = str(value or "").strip()
    cleaned = sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return cleaned or fallback


def normalize_image_output_category(mode_or_category: str | None) -> str:
    key = str(mode_or_category or "generate").strip().lower().replace("-", "_")
    category = MODE_TO_CATEGORY.get(key, key)
    if category not in IMAGE_OUTPUT_CATEGORIES:
        return "generate"
    return category


def get_image_output_paths(mode_or_category: str | None = "generate", *, create: bool = True) -> ImageOutputPaths:
    category = normalize_image_output_category(mode_or_category)
    paths = ImageOutputPaths(
        category=category,
        output_dir=IMAGE_OUTPUT_ROOT / category,
        metadata_dir=IMAGE_METADATA_ROOT / category,
    )
    return paths.ensure() if create else paths


def get_all_image_output_paths(*, create: bool = False) -> dict[str, ImageOutputPaths]:
    return {
        category: get_image_output_paths(category, create=create)
        for category in IMAGE_OUTPUT_CATEGORIES
    }


def output_path_payload(*, create: bool = False) -> dict:
    paths = get_all_image_output_paths(create=create)
    return {
        "schema_version": "neo.image.output_paths.v1",
        "root": _relative_to_root(IMAGE_OUTPUT_ROOT),
        "metadata_root": _relative_to_root(IMAGE_METADATA_ROOT),
        "categories": {
            category: {
                "output_dir": item.relative_output_dir,
                "metadata_dir": item.relative_metadata_dir,
            }
            for category, item in paths.items()
        },
        "rules": [
            "Final image outputs are copied into Neo-owned neo_data/outputs/image folders.",
            "Sidecar metadata is stored under neo_data/outputs/image_metadata.",
            "ComfyUI output paths are treated as source references only, not final storage.",
        ],
    }


def safe_join(base_dir: Path, filename: str) -> Path:
    """Join a file name to a base directory without allowing traversal."""
    safe_name = sanitize_path_part(Path(str(filename or "output.png")).name, fallback="output.png")
    candidate = (base_dir / safe_name).resolve()
    base = base_dir.resolve()
    if base not in candidate.parents and candidate != base:
        raise ValueError(f"Unsafe output path outside Neo_Data: {filename}")
    return candidate


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
