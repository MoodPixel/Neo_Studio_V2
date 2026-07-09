from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from re import sub
from typing import Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
NEO_DATA_DIR: Final[Path] = ROOT_DIR / "neo_data"
VOICE_OUTPUT_ROOT: Final[Path] = NEO_DATA_DIR / "outputs" / "voice"

VOICE_OUTPUT_CATEGORIES: Final[tuple[str, ...]] = (
    "preview",
    "render",
    "chunks",
    "profiles",
    "reference",
    "metadata",
    "history",
    "exports",
    "batch",
    "finish",
    "uncategorized",
)

MODE_TO_CATEGORY: Final[dict[str, str]] = {
    "preview": "preview",
    "quick_preview": "preview",
    "generate": "render",
    "speech": "render",
    "render": "render",
    "chunks": "chunks",
    "chunk": "chunks",
    "profiles": "profiles",
    "profile": "profiles",
    "voice_profile": "profiles",
    "reference": "reference",
    "reference_audio": "reference",
    "clone": "reference",
    "metadata": "metadata",
    "history": "history",
    "exports": "exports",
    "export": "exports",
    "batch": "batch",
    "batch_import": "batch",
    "script_import": "batch",
    "finish": "finish",
}


@dataclass(frozen=True)
class VoiceOutputPaths:
    """Canonical Neo-owned paths for one Voice output category."""

    category: str
    output_dir: Path

    @property
    def relative_output_dir(self) -> str:
        return _relative_to_root(self.output_dir)

    def ensure(self) -> "VoiceOutputPaths":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self

    def output_file(self, filename: str) -> Path:
        return safe_join(self.output_dir, filename)


def sanitize_path_part(value: str | None, fallback: str = "output") -> str:
    raw = str(value or "").strip()
    cleaned = sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return cleaned or fallback


def normalize_voice_output_category(mode_or_category: str | None) -> str:
    key = str(mode_or_category or "render").strip().lower().replace("-", "_")
    category = MODE_TO_CATEGORY.get(key, key)
    if category not in VOICE_OUTPUT_CATEGORIES:
        return "render"
    return category


def get_voice_output_paths(mode_or_category: str | None = "render", *, create: bool = True) -> VoiceOutputPaths:
    category = normalize_voice_output_category(mode_or_category)
    paths = VoiceOutputPaths(category=category, output_dir=VOICE_OUTPUT_ROOT / category)
    return paths.ensure() if create else paths


def get_all_voice_output_paths(*, create: bool = False) -> dict[str, VoiceOutputPaths]:
    return {category: get_voice_output_paths(category, create=create) for category in VOICE_OUTPUT_CATEGORIES}


def output_path_payload(*, create: bool = False) -> dict:
    paths = get_all_voice_output_paths(create=create)
    return {
        "schema_version": "neo.voice.output_paths.v0",
        "surface": "voice",
        "root": _relative_to_root(VOICE_OUTPUT_ROOT),
        "categories": {category: {"output_dir": item.relative_output_dir} for category, item in paths.items()},
        "rules": [
            "Final Voice outputs stay under Neo-owned neo_data/outputs/voice folders.",
            "Reference audio is copied into neo_data/outputs/voice/reference before later runtime phases use it.",
            "Preview, render chunks, profiles, metadata, history, and exports are separated for recovery and replay.",
            "Voice runtime will use dedicated TTS adapters; ComfyUI is not the primary Voice backend layer.",
            "VO-V15 replay sidecars are written under metadata and memory event indexes are written under history for Control Center ingestion.",
        ],
    }


def safe_join(base_dir: Path, filename: str) -> Path:
    safe_name = sanitize_path_part(Path(str(filename or "output.wav")).name, fallback="output.wav")
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


def resolve_voice_output_file(path_value: str) -> Path:
    """Resolve a Neo-owned Voice output file path for playback/download."""
    raw = Path(str(path_value or ""))
    candidate = raw if raw.is_absolute() else (ROOT_DIR / raw)
    resolved = candidate.resolve()
    root = VOICE_OUTPUT_ROOT.resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError("Voice output file must live under neo_data/outputs/voice")
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(str(path_value))
    return resolved
