from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from neo_app.image.output_paths import IMAGE_METADATA_ROOT, IMAGE_OUTPUT_ROOT, ROOT_DIR, safe_join

SETTINGS_DIR = ROOT_DIR / "neo_data" / "settings" / "image"
SETTINGS_PATH = SETTINGS_DIR / "output_settings.json"
DEFAULT_CATEGORY = "Uncategorized"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_INDEX_RE = re.compile(r"^(?P<prefix>.+?)_(?P<index>\d{2,8})(?:_|$)")


def category_display_name(name: str | None) -> str:
    text = re.sub(r"\s+", " ", str(name or DEFAULT_CATEGORY).strip())
    return text or DEFAULT_CATEGORY


def category_slug(name: str | None) -> str:
    text = category_display_name(name).lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    return text or "uncategorized"


def _dedupe_categories(values: Iterable[str] | None) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        display = category_display_name(value)
        key = display.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(display)
    return rows or [DEFAULT_CATEGORY]


def default_image_output_settings() -> dict[str, Any]:
    return {
        "output_root": str(IMAGE_OUTPUT_ROOT),
        "metadata_root": str(IMAGE_METADATA_ROOT),
        "categories": [DEFAULT_CATEGORY],
        "selected_category": DEFAULT_CATEGORY,
        "filename_prefix": "NeoStudio",
        "filename_padding": 4,
        "cleanup_backend_native_outputs": True,
    }


def load_image_output_settings() -> dict[str, Any]:
    defaults = default_image_output_settings()
    data: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    categories = _dedupe_categories(data.get("categories") if isinstance(data.get("categories"), list) else defaults["categories"])
    selected = category_display_name(data.get("selected_category") or categories[0])
    if selected.casefold() not in {item.casefold() for item in categories}:
        categories.append(selected)
    try:
        padding = max(2, min(8, int(data.get("filename_padding", defaults["filename_padding"]))))
    except Exception:
        padding = defaults["filename_padding"]
    return {
        **defaults,
        "categories": categories,
        "selected_category": selected,
        "filename_prefix": category_slug(data.get("filename_prefix") or defaults["filename_prefix"]),
        "filename_padding": padding,
        "cleanup_backend_native_outputs": bool(data.get("cleanup_backend_native_outputs", True)),
    }


def save_image_output_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_image_output_settings()
    categories = _dedupe_categories(payload.get("categories") if isinstance(payload.get("categories"), list) else current["categories"])
    selected = category_display_name(payload.get("selected_category") or current["selected_category"])
    if selected.casefold() not in {item.casefold() for item in categories}:
        categories.append(selected)
    try:
        padding = max(2, min(8, int(payload.get("filename_padding", current["filename_padding"]))))
    except Exception:
        padding = current["filename_padding"]
    settings = {
        **current,
        "categories": categories,
        "selected_category": selected,
        "filename_prefix": category_slug(payload.get("filename_prefix") or current["filename_prefix"]),
        "filename_padding": padding,
        "cleanup_backend_native_outputs": bool(payload.get("cleanup_backend_native_outputs", current.get("cleanup_backend_native_outputs", True))),
    }
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)
    ensure_output_settings_dirs(settings)
    return settings


def add_image_output_category(name: str, settings_patch: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_image_output_settings()
    patch = settings_patch or {}
    display = category_display_name(name)
    categories = list(settings.get("categories") or [])
    if display.casefold() not in {item.casefold() for item in categories}:
        categories.append(display)
    return save_image_output_settings({**settings, **patch, "categories": categories, "selected_category": display})


def output_category_dir(settings: dict[str, Any] | None = None, category_name: str | None = None) -> Path:
    data = settings or load_image_output_settings()
    category = category_display_name(category_name or data.get("selected_category"))
    return safe_join(IMAGE_OUTPUT_ROOT, category_slug(category)).with_suffix("")


def metadata_category_dir(settings: dict[str, Any] | None = None, category_name: str | None = None) -> Path:
    data = settings or load_image_output_settings()
    category = category_display_name(category_name or data.get("selected_category"))
    return safe_join(IMAGE_METADATA_ROOT, category_slug(category)).with_suffix("")


def ensure_output_settings_dirs(settings: dict[str, Any] | None = None) -> dict[str, str]:
    data = settings or load_image_output_settings()
    out = output_category_dir(data)
    meta = metadata_category_dir(data)
    out.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    return {"output_dir": _relative_to_root(out), "metadata_dir": _relative_to_root(meta)}


def next_category_index(folder: Path, prefix: str, padding: int) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    max_index = 0
    lowered = category_slug(prefix).lower()
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTS:
            continue
        match = _INDEX_RE.match(path.stem)
        if not match:
            continue
        if category_slug(match.group("prefix")).lower() != lowered:
            continue
        try:
            max_index = max(max_index, int(match.group("index")))
        except Exception:
            pass
    return max_index + 1


def settings_response(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    data = settings or load_image_output_settings()
    dirs = ensure_output_settings_dirs(data)
    return {
        **data,
        **dirs,
        "output_root": _relative_to_root(IMAGE_OUTPUT_ROOT),
        "metadata_root": _relative_to_root(IMAGE_METADATA_ROOT),
        "rules": [
            "Neo owns final Image outputs under neo_data/outputs/image.",
            "Backend native output files are temporary source refs and may be cleaned after persistence.",
            "Folder category names are stored in Neo settings, not browser localStorage.",
        ],
    }


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
