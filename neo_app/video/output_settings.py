from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from neo_app.video.output_paths import ROOT_DIR, VIDEO_OUTPUT_ROOT, get_video_output_paths, safe_join

SETTINGS_DIR = ROOT_DIR / "neo_data" / "settings" / "video"
SETTINGS_PATH = SETTINGS_DIR / "output_settings.json"
DEFAULT_CATEGORY = "Uncategorized"
_VIDEO_EXTS = {".webm", ".mp4", ".mov", ".mkv", ".gif"}
_REPLAY_EXTS = {".replay.v22.json", ".memory_export.v22.json"}


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


def default_video_output_settings() -> dict[str, Any]:
    return {
        "output_root": _relative_to_root(VIDEO_OUTPUT_ROOT),
        "metadata_root": _relative_to_root(get_video_output_paths("metadata").output_dir),
        "categories": [DEFAULT_CATEGORY],
        "selected_category": DEFAULT_CATEGORY,
        "filename_prefix": "NeoStudio",
        "filename_padding": 4,
        "cleanup_backend_native_outputs": True,
    }


def load_video_output_settings() -> dict[str, Any]:
    defaults = default_video_output_settings()
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


def save_video_output_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_video_output_settings()
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


def add_video_output_category(name: str, settings_patch: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_video_output_settings()
    patch = settings_patch or {}
    display = category_display_name(name)
    categories = list(settings.get("categories") or [])
    if display.casefold() not in {item.casefold() for item in categories}:
        categories.append(display)
    return save_video_output_settings({**settings, **patch, "categories": categories, "selected_category": display})


def output_category_dir(settings: dict[str, Any] | None = None, category_name: str | None = None) -> Path:
    data = settings or load_video_output_settings()
    category = category_display_name(category_name or data.get("selected_category"))
    return safe_join(VIDEO_OUTPUT_ROOT, category_slug(category)).with_suffix("")


def metadata_category_dir(settings: dict[str, Any] | None = None) -> Path:
    return get_video_output_paths("metadata", create=True).output_dir


def ensure_output_settings_dirs(settings: dict[str, Any] | None = None) -> dict[str, str]:
    data = settings or load_video_output_settings()
    out = output_category_dir(data)
    meta = metadata_category_dir(data)
    out.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    return {"output_dir": _relative_to_root(out), "metadata_dir": _relative_to_root(meta)}


def settings_response(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    data = settings or load_video_output_settings()
    dirs = ensure_output_settings_dirs(data)
    return {
        **data,
        **dirs,
        "output_root": _relative_to_root(VIDEO_OUTPUT_ROOT),
        "metadata_root": _relative_to_root(get_video_output_paths("metadata").output_dir),
        "rules": [
            "Neo owns final Video outputs under neo_data/outputs/video.",
            "Backend native output files are temporary source refs and may be cleaned after persistence.",
            "Folder category names are stored in Neo settings, not browser localStorage.",
        ],
    }


def _safe_tree_size(folder: Path, *, predicate=None) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    if not folder.exists():
        return total_bytes, total_files
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if predicate and not predicate(path):
            continue
        try:
            total_bytes += path.stat().st_size
            total_files += 1
        except Exception:
            pass
    return total_bytes, total_files


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} B"
    return f"{size:.1f} {unit}"


def video_replay_storage_summary() -> dict[str, Any]:
    outputs_root = VIDEO_OUTPUT_ROOT.resolve()
    metadata_root = get_video_output_paths("metadata", create=True).output_dir.resolve()

    output_bytes, output_files = _safe_tree_size(outputs_root, predicate=lambda p: p.parent != metadata_root and p.suffix.lower() in _VIDEO_EXTS)
    metadata_bytes, metadata_files = _safe_tree_size(metadata_root, predicate=lambda p: p.name.endswith('.json') and not any(p.name.endswith(ext) for ext in _REPLAY_EXTS))
    replay_bytes, replay_files = _safe_tree_size(metadata_root, predicate=lambda p: any(p.name.endswith(ext) for ext in _REPLAY_EXTS))

    orphan_files: list[dict[str, Any]] = []
    records_scanned = 0
    if metadata_root.exists():
        for record_path in metadata_root.glob('*.json'):
            name = record_path.name
            if any(name.endswith(ext) for ext in _REPLAY_EXTS):
                base = name.split('.replay.v22.json')[0] if name.endswith('.replay.v22.json') else name.split('.memory_export.v22.json')[0]
                if not (metadata_root / f"{base}.json").exists():
                    orphan_files.append({
                        "path": _relative_to_root(record_path),
                        "bytes": record_path.stat().st_size if record_path.exists() else 0,
                        "reason": "Replay sidecar is missing its base metadata record.",
                    })
            else:
                records_scanned += 1

    orphan_bytes = sum(int(item.get('bytes') or 0) for item in orphan_files)
    return {
        "schema_version": "neo.video.replay_storage.v1",
        "ok": True,
        "roots": {
            "outputs": _relative_to_root(outputs_root),
            "metadata": _relative_to_root(metadata_root),
        },
        "usage": {
            "outputs": {"bytes": output_bytes, "files": output_files, "display": _format_bytes(output_bytes)},
            "metadata": {"bytes": metadata_bytes, "files": metadata_files, "display": _format_bytes(metadata_bytes)},
            "replay": {"bytes": replay_bytes, "files": replay_files, "display": _format_bytes(replay_bytes)},
            "total_bytes": output_bytes + metadata_bytes + replay_bytes,
            "total_display": _format_bytes(output_bytes + metadata_bytes + replay_bytes),
        },
        "records": {
            "metadata_records_scanned": records_scanned,
            "replay_sidecars": replay_files,
        },
        "cleanup_candidates": {
            "orphan_replay_files": orphan_files,
            "orphan_replay_bytes": orphan_bytes,
            "orphan_replay_display": _format_bytes(orphan_bytes),
        },
        "retention_policy": {
            "metadata": "keep_forever_unless_result_deleted",
            "outputs": "delete_only_when_user_deletes_saved_output",
            "replay": "delete_orphans_only_from_storage_manager_or_delete_result",
            "backend_native_outputs": "controlled_by_results_save_details_cleanup_toggle",
        },
    }


def cleanup_video_replay_storage(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    action = str(data.get("action") or "delete_orphan_replay_sidecars").strip().lower()
    if action not in {"delete_orphan_replay_sidecars"}:
        return {"ok": False, "action": action, "deleted": [], "errors": ["Unsupported cleanup action."]}
    summary = video_replay_storage_summary()
    deleted: list[str] = []
    errors: list[str] = []
    for item in summary.get("cleanup_candidates", {}).get("orphan_replay_files", []):
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        path = (ROOT_DIR / raw_path).resolve()
        try:
            if not path.exists() or not path.is_file():
                continue
            path.unlink()
            deleted.append(raw_path)
        except Exception as exc:
            errors.append(f"{raw_path}: {exc}")
    return {"ok": not errors, "action": action, "deleted": deleted, "errors": errors, "summary": video_replay_storage_summary()}


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except Exception:
        return path.as_posix()
