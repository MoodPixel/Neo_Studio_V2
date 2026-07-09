from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from re import sub
from typing import Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
NEO_DATA_DIR: Final[Path] = ROOT_DIR / "neo_data"
VIDEO_OUTPUT_ROOT: Final[Path] = NEO_DATA_DIR / "outputs" / "video"

VIDEO_OUTPUT_CATEGORIES: Final[tuple[str, ...]] = (
    "txt2vid",
    "img2vid",
    "first_last_frame",
    "multiscene",
    "extend",
    "vid2vid",
    "depth_motion",
    "schedule",
    "audio_video",
    "interpolate",
    "upscale",
    "repair",
    "metadata",
    "previews",
    "frames",
    "source",
    "uncategorized",
)

MODE_TO_CATEGORY: Final[dict[str, str]] = {
    "txt2vid": "txt2vid",
    "text_to_video": "txt2vid",
    "t2v": "txt2vid",
    "generate": "txt2vid",
    "img2vid": "img2vid",
    "image_to_video": "img2vid",
    "i2v": "img2vid",
    "first_last_frame": "first_last_frame",
    "first_last": "first_last_frame",
    "start_end": "first_last_frame",
    "start_end_frame": "first_last_frame",
    "multi_scene": "multiscene",
    "multiscene": "multiscene",
    "extend": "extend",
    "video_extend": "extend",
    "vid2vid": "vid2vid",
    "video_to_video": "vid2vid",
    "v2v": "vid2vid",
    "restyle_video": "vid2vid",
    "depth_motion": "depth_motion",
    "depth_control": "depth_motion",
    "motion_control": "depth_motion",
    "prompt_schedule": "schedule",
    "prompt_scheduling": "schedule",
    "motion_schedule": "schedule",
    "schedule": "schedule",
    "scheduled": "schedule",
    "audio_video": "audio_video",
    "audio": "audio_video",
    "audio_visual": "audio_video",
    "audiovideo": "audio_video",
    "interpolate": "interpolate",
    "interpolation": "interpolate",
    "upscale": "upscale",
    "repair": "repair",
    "metadata": "metadata",
    "preview": "previews",
    "previews": "previews",
    "frames": "frames",
    "source": "source",
}


@dataclass(frozen=True)
class VideoOutputPaths:
    """Canonical Neo-owned paths for one video output category."""

    category: str
    output_dir: Path

    @property
    def relative_output_dir(self) -> str:
        return _relative_to_root(self.output_dir)

    def ensure(self) -> "VideoOutputPaths":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self

    def output_file(self, filename: str) -> Path:
        return safe_join(self.output_dir, filename)


def sanitize_path_part(value: str | None, fallback: str = "output") -> str:
    """Return a filesystem-safe single path segment."""
    raw = str(value or "").strip()
    cleaned = sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return cleaned or fallback


def normalize_video_output_category(mode_or_category: str | None) -> str:
    key = str(mode_or_category or "txt2vid").strip().lower().replace("-", "_")
    category = MODE_TO_CATEGORY.get(key, key)
    if category not in VIDEO_OUTPUT_CATEGORIES:
        return "txt2vid"
    return category


def get_video_output_paths(mode_or_category: str | None = "txt2vid", *, create: bool = True) -> VideoOutputPaths:
    category = normalize_video_output_category(mode_or_category)
    paths = VideoOutputPaths(category=category, output_dir=VIDEO_OUTPUT_ROOT / category)
    return paths.ensure() if create else paths


def get_all_video_output_paths(*, create: bool = False) -> dict[str, VideoOutputPaths]:
    return {category: get_video_output_paths(category, create=create) for category in VIDEO_OUTPUT_CATEGORIES}


def output_path_payload(*, create: bool = False) -> dict:
    paths = get_all_video_output_paths(create=create)
    return {
        "schema_version": "neo.video.output_paths.v1",
        "surface": "video",
        "root": _relative_to_root(VIDEO_OUTPUT_ROOT),
        "categories": {category: {"output_dir": item.relative_output_dir} for category, item in paths.items()},
        "rules": [
            "Final video outputs are copied into Neo-owned neo_data/outputs/video folders.",
            "Video metadata and replay records are stored under neo_data/outputs/video/metadata.",
            "ComfyUI output paths are treated as source references only, not final storage.",
            "Image-related source creation stays owned by the Image surface; Video stores only video run sources and references.",
        ],
    }


def safe_join(base_dir: Path, filename: str) -> Path:
    """Join a file name to a base directory without allowing traversal."""
    safe_name = sanitize_path_part(Path(str(filename or "output.mp4")).name, fallback="output.mp4")
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
