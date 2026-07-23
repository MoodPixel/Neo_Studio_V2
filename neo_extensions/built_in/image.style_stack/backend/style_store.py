"""V1-compatible CSV storage helpers for the Style Stack extension.

Canonical V2 runtime path:
    neo_data/extensions/image/style_stack/generation_styles.csv

Bundled default source:
    neo_extensions/built_in/image.style_stack/assets/default_generation_styles.csv

The bundled CSV is read-only application content. The runtime CSV is the user's
editable library. Phase 6B keeps those authorities separate while synchronizing
new or safely updated bundled defaults into existing installations.

CSV contract:
    name,prompt,negative_prompt
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Sequence

CANONICAL_CSV_RELATIVE_PATH = "neo_data/extensions/image/style_stack/generation_styles.csv"
BUNDLED_DEFAULT_CSV_RELATIVE_PATH = "neo_extensions/built_in/image.style_stack/assets/default_generation_styles.csv"
STYLE_STACK_DATA_RELATIVE_DIR = "neo_data/extensions/image/style_stack"
IMPORTS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/imports"
EXPORTS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/exports"
BACKUPS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/backups"
SYNC_STATE_RELATIVE_PATH = "neo_data/extensions/image/style_stack/bundled_runtime_sync_state.json"
LEGACY_CSV_RELATIVE_PATHS = (
    "neo_data/generation_styles.csv",
    "generation_styles.csv",
)
CSV_FIELDS = ("name", "prompt", "negative_prompt")
ENCODING_FALLBACK = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DEFAULT_WRITE_ENCODING = "utf-8-sig"
SYNC_STATE_SCHEMA_VERSION = "neo.image.style_stack.bundled_runtime_sync.v1"
SYNC_RESULT_SCHEMA_VERSION = "neo.image.style_stack.bundled_runtime_sync_result.v1"


@dataclass(frozen=True)
class StyleStoreResult:
    """Operation result returned to API and UI layers."""

    ok: bool
    styles: list[dict[str, str]]
    path: str
    message: str = ""
    encoding: str | None = None
    sync: dict[str, Any] = field(default_factory=dict)


def resolve_repo_root(root: str | Path | None = None) -> Path:
    """Resolve the Neo repository/runtime root without machine-specific paths."""

    if root is not None:
        return Path(root).expanduser().resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "neo_data").exists() and (parent / "neo_extensions").exists():
            return parent
    return here.parents[4]


def canonical_csv_path(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / CANONICAL_CSV_RELATIVE_PATH


def bundled_default_csv_path(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / BUNDLED_DEFAULT_CSV_RELATIVE_PATH


def sync_state_path(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / SYNC_STATE_RELATIVE_PATH


def imports_dir(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / IMPORTS_RELATIVE_DIR


def exports_dir(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / EXPORTS_RELATIVE_DIR


def backups_dir(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / BACKUPS_RELATIVE_DIR


def _normalise_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def normalise_style(row: dict[str, object]) -> dict[str, str]:
    """Return one canonical style row using the V1 CSV field names."""

    return {
        "name": _normalise_cell(row.get("name")),
        "prompt": _normalise_cell(row.get("prompt")),
        "negative_prompt": _normalise_cell(row.get("negative_prompt")),
    }


def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    last_error: Exception | None = None
    for encoding in ENCODING_FALLBACK:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return path.read_text(encoding=DEFAULT_WRITE_ENCODING), DEFAULT_WRITE_ENCODING


def _parse_styles_from_text(text: str) -> list[dict[str, str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample else csv.excel
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    if reader.fieldnames is None:
        return []

    missing = [field for field in CSV_FIELDS if field not in reader.fieldnames]
    if missing:
        raise ValueError(
            f"Style Stack CSV is missing required field(s): {', '.join(missing)}. "
            f"Required header: {','.join(CSV_FIELDS)}"
        )

    styles: list[dict[str, str]] = []
    for row in reader:
        style = normalise_style(row)
        if not style["name"] and not style["prompt"] and not style["negative_prompt"]:
            continue
        if not style["name"]:
            continue
        styles.append(style)
    return styles


def read_styles_csv(path: str | Path) -> tuple[list[dict[str, str]], str]:
    """Read a style CSV and return ``(styles, encoding_used)``."""

    csv_path = Path(path).expanduser().resolve()
    text, encoding = _read_text_with_fallback(csv_path)
    return _parse_styles_from_text(text), encoding


def _dedupe_styles(styles: Iterable[dict[str, object]]) -> list[dict[str, str]]:
    """Dedupe by exact style name while preserving last-definition updates."""

    ordered_names: list[str] = []
    by_name: dict[str, dict[str, str]] = {}
    for raw in styles:
        style = normalise_style(raw)
        name = style["name"]
        if not name:
            continue
        if name not in by_name:
            ordered_names.append(name)
        by_name[name] = style
    return [by_name[name] for name in ordered_names]


def write_styles_csv(path: str | Path, styles: Sequence[dict[str, object]]) -> Path:
    """Atomically write styles using the canonical CSV field order."""

    csv_path = Path(path).expanduser().resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    clean_styles = _dedupe_styles(styles)

    with NamedTemporaryFile("w", encoding=DEFAULT_WRITE_ENCODING, newline="", delete=False, dir=csv_path.parent) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=list(CSV_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for style in clean_styles:
            writer.writerow(style)
        tmp_path = Path(tmp.name)

    tmp_path.replace(csv_path)
    return csv_path


def find_legacy_csv(root: str | Path | None = None) -> Path | None:
    base = resolve_repo_root(root)
    for rel in LEGACY_CSV_RELATIVE_PATHS:
        candidate = base / rel
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _relative_to_root(path: Path, root: str | Path | None = None) -> str:
    base = resolve_repo_root(root)
    try:
        return path.resolve().relative_to(base).as_posix()
    except (OSError, ValueError):
        return path.name


def _style_map(styles: Sequence[dict[str, object]]) -> dict[str, dict[str, str]]:
    return {style["name"]: style for style in _dedupe_styles(styles)}


def _styles_digest(styles: Sequence[dict[str, object]]) -> str:
    payload = json.dumps(_dedupe_styles(styles), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_sync_state() -> dict[str, Any]:
    return {
        "schema_version": SYNC_STATE_SCHEMA_VERSION,
        "bundled_digest": "",
        "bundled_styles": {},
        "tombstones": [],
        "last_sync": {},
    }


def _load_sync_state(root: str | Path | None = None) -> tuple[dict[str, Any], bool]:
    path = sync_state_path(root)
    if not path.exists():
        return _default_sync_state(), False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Style Stack sync state must be an object")
        state = _default_sync_state()
        state.update(payload)
        if not isinstance(state.get("bundled_styles"), dict):
            state["bundled_styles"] = {}
        if not isinstance(state.get("tombstones"), list):
            state["tombstones"] = []
        return state, False
    except (OSError, ValueError, json.JSONDecodeError):
        backups_dir(root).mkdir(parents=True, exist_ok=True)
        recovery = backups_dir(root) / f"bundled_runtime_sync_state.corrupt.{_utc_timestamp()}.json"
        try:
            shutil.copy2(path, recovery)
        except OSError:
            pass
        return _default_sync_state(), True


def _write_sync_state(state: dict[str, Any], root: str | Path | None = None) -> Path:
    path = sync_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["schema_version"] = SYNC_STATE_SCHEMA_VERSION
    with NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    return path


def _update_tombstones(
    root: str | Path | None = None,
    *,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
) -> set[str]:
    state, _ = _load_sync_state(root)
    tombstones = {_normalise_cell(name) for name in state.get("tombstones", []) if _normalise_cell(name)}
    tombstones.update(_normalise_cell(name) for name in add if _normalise_cell(name))
    tombstones.difference_update(_normalise_cell(name) for name in remove if _normalise_cell(name))
    state["tombstones"] = sorted(tombstones, key=str.casefold)
    _write_sync_state(state, root)
    return tombstones


def _backup_runtime_before_sync(target: Path, root: str | Path | None = None) -> Path:
    directory = backups_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / f"generation_styles.before_bundled_sync.{_utc_timestamp()}.csv"
    shutil.copy2(target, backup)
    return backup


def ensure_generation_styles_file(
    root: str | Path | None = None,
    *,
    seed_from: str | Path | None = None,
    create_empty: bool = True,
) -> Path:
    """Ensure the canonical runtime style CSV exists.

    Existing non-empty runtime libraries remain user-owned. Bundled/runtime
    synchronization is performed separately by ``load_generation_styles``.
    """

    target = canonical_csv_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    imports_dir(root).mkdir(parents=True, exist_ok=True)
    exports_dir(root).mkdir(parents=True, exist_ok=True)
    backups_dir(root).mkdir(parents=True, exist_ok=True)

    if target.exists():
        bundled_default = bundled_default_csv_path(root)
        if bundled_default.exists() and bundled_default.is_file():
            try:
                current_styles, _ = read_styles_csv(target)
                bundled_styles, _ = read_styles_csv(bundled_default)
            except Exception:
                current_styles, bundled_styles = [], []
            if not current_styles and bundled_styles:
                backup = backups_dir(root) / "generation_styles.empty_runtime_before_bundled_seed.csv"
                try:
                    shutil.copy2(target, backup)
                except OSError:
                    pass
                shutil.copy2(bundled_default, target)
        return target

    source: Path | None = Path(seed_from).expanduser().resolve() if seed_from else None
    if source and source.exists():
        shutil.copy2(source, target)
        return target

    bundled_default = bundled_default_csv_path(root)
    if bundled_default.exists() and bundled_default.is_file():
        shutil.copy2(bundled_default, target)
        return target

    legacy_source = find_legacy_csv(root)
    if legacy_source and legacy_source.exists():
        shutil.copy2(legacy_source, target)
        return target

    if create_empty:
        write_styles_csv(target, [])
        return target

    raise FileNotFoundError(f"Style Stack CSV not found at {target}")


def synchronize_bundled_generation_styles(
    root: str | Path | None = None,
    *,
    runtime_path: str | Path | None = None,
) -> dict[str, Any]:
    """Non-destructively synchronize bundled defaults into the runtime library.

    Rules:
    - new bundled names are appended to the user-owned runtime library;
    - an untouched bundled row may receive a later bundled prompt update;
    - user-edited rows with matching names always win;
    - runtime-only custom rows are preserved;
    - bundled rows deleted through Neo are tombstoned and stay deleted;
    - a runtime backup is written before every sync mutation.
    """

    target = Path(runtime_path).expanduser().resolve() if runtime_path else ensure_generation_styles_file(root)
    bundled_path = bundled_default_csv_path(root)
    runtime_styles, runtime_encoding = read_styles_csv(target)

    if not bundled_path.exists() or not bundled_path.is_file():
        return {
            "schema_version": SYNC_RESULT_SCHEMA_VERSION,
            "status": "bundled_default_unavailable",
            "runtime_count": len(runtime_styles),
            "bundled_count": 0,
            "added": 0,
            "updated_defaults": 0,
            "preserved_overrides": 0,
            "runtime_only": len(runtime_styles),
            "tombstoned": 0,
            "sync_applied": False,
            "backup_created": False,
            "backup_path": "",
            "runtime_encoding": runtime_encoding,
        }

    bundled_styles, bundled_encoding = read_styles_csv(bundled_path)
    state, state_recovered = _load_sync_state(root)
    previous_bundled = {
        str(name): normalise_style(style)
        for name, style in (state.get("bundled_styles") or {}).items()
        if str(name).strip() and isinstance(style, dict)
    }
    tombstones = {_normalise_cell(name) for name in state.get("tombstones", []) if _normalise_cell(name)}

    runtime_styles = _dedupe_styles(runtime_styles)
    bundled_styles = _dedupe_styles(bundled_styles)
    runtime_by_name = _style_map(runtime_styles)
    bundled_by_name = _style_map(bundled_styles)

    # A manually re-added or imported bundled style is an explicit restoration.
    restored_names = tombstones.intersection(runtime_by_name)
    if restored_names:
        tombstones.difference_update(restored_names)

    merged = [dict(style) for style in runtime_styles]
    index_by_name = {style["name"]: index for index, style in enumerate(merged)}
    added = 0
    updated_defaults = 0
    preserved_overrides = 0

    for bundled_style in bundled_styles:
        name = bundled_style["name"]
        if name in tombstones:
            continue
        runtime_style = runtime_by_name.get(name)
        if runtime_style is None:
            index_by_name[name] = len(merged)
            merged.append(dict(bundled_style))
            runtime_by_name[name] = dict(bundled_style)
            added += 1
            continue

        previous_style = previous_bundled.get(name)
        if previous_style is not None and runtime_style == previous_style and runtime_style != bundled_style:
            merged[index_by_name[name]] = dict(bundled_style)
            runtime_by_name[name] = dict(bundled_style)
            updated_defaults += 1
        elif runtime_style != bundled_style:
            preserved_overrides += 1

    runtime_changed = merged != runtime_styles
    backup_path = ""
    if runtime_changed:
        backup = _backup_runtime_before_sync(target, root)
        backup_path = _relative_to_root(backup, root)
        write_styles_csv(target, merged)

    bundled_digest = _styles_digest(bundled_styles)
    previous_digest = str(state.get("bundled_digest") or "")
    runtime_names = {style["name"] for style in merged}
    bundled_names = set(bundled_by_name)
    result = {
        "schema_version": SYNC_RESULT_SCHEMA_VERSION,
        "status": "synchronized" if runtime_changed else "already_synchronized",
        "runtime_count": len(merged),
        "bundled_count": len(bundled_styles),
        "added": added,
        "updated_defaults": updated_defaults,
        "preserved_overrides": preserved_overrides,
        "runtime_only": len(runtime_names - bundled_names),
        "tombstoned": len(tombstones.intersection(bundled_names)),
        "sync_applied": runtime_changed,
        "backup_created": bool(backup_path),
        "backup_path": backup_path,
        "state_created": not bool(previous_digest or previous_bundled),
        "state_recovered": state_recovered,
        "bundled_changed": bool(previous_digest and previous_digest != bundled_digest),
        "runtime_encoding": runtime_encoding,
        "bundled_encoding": bundled_encoding,
    }

    state.update({
        "schema_version": SYNC_STATE_SCHEMA_VERSION,
        "bundled_digest": bundled_digest,
        "bundled_styles": bundled_by_name,
        "tombstones": sorted(tombstones, key=str.casefold),
        "last_sync": {
            **result,
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    })
    _write_sync_state(state, root)
    return result


def load_generation_styles(root: str | Path | None = None) -> StyleStoreResult:
    path = ensure_generation_styles_file(root)
    sync = synchronize_bundled_generation_styles(root, runtime_path=path)
    styles, encoding = read_styles_csv(path)
    return StyleStoreResult(ok=True, styles=styles, path=str(path), encoding=encoding, sync=sync)


def save_generation_styles(
    styles: Sequence[dict[str, object]],
    root: str | Path | None = None,
    *,
    track_bundled_removals: bool = False,
) -> StyleStoreResult:
    """Save the runtime library while maintaining bundled deletion tombstones."""

    current_result = load_generation_styles(root)
    path = Path(current_result.path)
    current = current_result.styles
    next_styles = _dedupe_styles(styles)

    bundled_path = bundled_default_csv_path(root)
    bundled_names: set[str] = set()
    if bundled_path.exists() and bundled_path.is_file():
        bundled_names = {style["name"] for style in read_styles_csv(bundled_path)[0]}

    current_names = {style["name"] for style in current}
    next_names = {style["name"] for style in next_styles}
    removed_bundled = (current_names - next_names).intersection(bundled_names) if track_bundled_removals else set()
    restored_bundled = next_names.intersection(bundled_names)
    _update_tombstones(root, add=removed_bundled, remove=restored_bundled)

    write_styles_csv(path, next_styles)
    sync = synchronize_bundled_generation_styles(root, runtime_path=path)
    loaded, encoding = read_styles_csv(path)
    return StyleStoreResult(ok=True, styles=loaded, path=str(path), message="styles_saved", encoding=encoding, sync=sync)


def upsert_generation_style(style: dict[str, object], root: str | Path | None = None) -> StyleStoreResult:
    clean = normalise_style(style)
    if not clean["name"]:
        raise ValueError("Style name is required")
    current = load_generation_styles(root).styles
    found = False
    next_styles: list[dict[str, str]] = []
    for existing in current:
        if existing["name"] == clean["name"]:
            next_styles.append(clean)
            found = True
        else:
            next_styles.append(existing)
    if not found:
        next_styles.append(clean)
    return save_generation_styles(next_styles, root)


def delete_generation_style(name: str, root: str | Path | None = None) -> StyleStoreResult:
    target_name = _normalise_cell(name)
    if not target_name:
        raise ValueError("Style name is required")
    current = load_generation_styles(root).styles
    next_styles = [style for style in current if style["name"] != target_name]
    result = save_generation_styles(next_styles, root, track_bundled_removals=True)
    removed = len(current) - len(next_styles)
    return StyleStoreResult(
        ok=True,
        styles=result.styles,
        path=result.path,
        message="style_deleted" if removed else "style_not_found",
        encoding=result.encoding,
        sync=result.sync,
    )


def _unique_duplicate_name(existing_names: set[str], original_name: str) -> str:
    base = f"{original_name} Copy"
    if base not in existing_names:
        return base
    index = 2
    while f"{base} {index}" in existing_names:
        index += 1
    return f"{base} {index}"


def duplicate_generation_style(name: str, root: str | Path | None = None) -> StyleStoreResult:
    target_name = _normalise_cell(name)
    if not target_name:
        raise ValueError("Style name is required")
    current = load_generation_styles(root).styles
    existing_names = {style["name"] for style in current}
    for style in current:
        if style["name"] == target_name:
            copy_style = dict(style)
            copy_style["name"] = _unique_duplicate_name(existing_names, target_name)
            return save_generation_styles([*current, copy_style], root)
    raise ValueError(f"Style not found: {target_name}")


def import_generation_styles_csv(
    source_csv: str | Path,
    root: str | Path | None = None,
    *,
    mode: str = "merge",
) -> StyleStoreResult:
    """Import a CSV into the canonical runtime store."""

    source = Path(source_csv).expanduser().resolve()
    imported, source_encoding = read_styles_csv(source)
    ensure_generation_styles_file(root)

    imports_dir(root).mkdir(parents=True, exist_ok=True)
    archive_name = source.name if source.name.lower().endswith(".csv") else "generation_styles_import.csv"
    shutil.copy2(source, imports_dir(root) / archive_name)

    if mode == "replace":
        next_styles = imported
        track_bundled_removals = True
    elif mode == "merge":
        current = load_generation_styles(root).styles
        next_styles = _dedupe_styles([*current, *imported])
        track_bundled_removals = False
    else:
        raise ValueError("Import mode must be 'merge' or 'replace'")

    result = save_generation_styles(next_styles, root, track_bundled_removals=track_bundled_removals)
    return StyleStoreResult(
        ok=True,
        styles=result.styles,
        path=result.path,
        message=f"styles_imported:{len(imported)}:{mode}:source_encoding={source_encoding}",
        encoding=result.encoding,
        sync=result.sync,
    )


def export_generation_styles_path(root: str | Path | None = None, *, filename: str = "generation_styles_export.csv") -> Path:
    """Write a normalized export copy and return its path."""

    current = load_generation_styles(root).styles
    target = exports_dir(root) / filename
    write_styles_csv(target, current)
    return target
