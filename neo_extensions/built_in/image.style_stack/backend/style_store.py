"""V1-compatible CSV storage helpers for the Style Stack extension.

Phase C implements the durable style library only. It intentionally does not
register API routes, mutate prompts, or patch provider workflow graphs.

Canonical V2 runtime path:
    neo_data/extensions/image/style_stack/generation_styles.csv

Bundled default seed path:
    neo_extensions/built_in/image.style_stack/assets/default_generation_styles.csv

CSV contract:
    name,prompt,negative_prompt

The loader keeps the V1 encoding fallback because existing user style libraries
may be Windows/ANSI encoded. The bundled migration CSV currently requires cp1252.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Sequence

CANONICAL_CSV_RELATIVE_PATH = "neo_data/extensions/image/style_stack/generation_styles.csv"
BUNDLED_DEFAULT_CSV_RELATIVE_PATH = "neo_extensions/built_in/image.style_stack/assets/default_generation_styles.csv"
STYLE_STACK_DATA_RELATIVE_DIR = "neo_data/extensions/image/style_stack"
IMPORTS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/imports"
EXPORTS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/exports"
BACKUPS_RELATIVE_DIR = "neo_data/extensions/image/style_stack/backups"
LEGACY_CSV_RELATIVE_PATHS = (
    "neo_data/generation_styles.csv",
    "generation_styles.csv",
)
CSV_FIELDS = ("name", "prompt", "negative_prompt")
ENCODING_FALLBACK = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DEFAULT_WRITE_ENCODING = "utf-8-sig"


@dataclass(frozen=True)
class StyleStoreResult:
    """Small operation result used by route layers in later phases."""

    ok: bool
    styles: list[dict[str, str]]
    path: str
    message: str = ""
    encoding: str | None = None


def resolve_repo_root(root: str | Path | None = None) -> Path:
    """Resolve the Neo repo root for storage operations.

    Tests and future route handlers can pass an explicit root. Without one, this
    walks up from the extension backend until a directory containing ``neo_data``
    and ``neo_extensions`` is found.
    """

    if root is not None:
        return Path(root).expanduser().resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "neo_data").exists() and (parent / "neo_extensions").exists():
            return parent
    # Fallback for unusual packaged runs: extension root is
    # <repo>/neo_extensions/built_in/image.style_stack/backend/style_store.py
    return here.parents[4]


def canonical_csv_path(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / CANONICAL_CSV_RELATIVE_PATH


def bundled_default_csv_path(root: str | Path | None = None) -> Path:
    return resolve_repo_root(root) / BUNDLED_DEFAULT_CSV_RELATIVE_PATH


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
            # Empty style names cannot be addressed by update/delete/chips.
            continue
        styles.append(style)
    return styles


def read_styles_csv(path: str | Path) -> tuple[list[dict[str, str]], str]:
    """Read a style CSV from ``path`` and return ``(styles, encoding_used)``."""

    csv_path = Path(path).expanduser().resolve()
    text, encoding = _read_text_with_fallback(csv_path)
    return _parse_styles_from_text(text), encoding


def _dedupe_styles(styles: Iterable[dict[str, object]]) -> list[dict[str, str]]:
    """Dedupe by name while preserving the last definition for updates/imports."""

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


def ensure_generation_styles_file(
    root: str | Path | None = None,
    *,
    seed_from: str | Path | None = None,
    create_empty: bool = True,
) -> Path:
    """Ensure the canonical V2 style CSV exists.

    Priority:
    1. keep existing canonical runtime file so user edits are preserved
    2. copy an explicit seed file
    3. copy the bundled built-in extension default CSV
    4. copy a legacy V1-style file discovered in the repo
    5. create an empty canonical CSV with the required header
    """

    target = canonical_csv_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    imports_dir(root).mkdir(parents=True, exist_ok=True)
    exports_dir(root).mkdir(parents=True, exist_ok=True)
    backups_dir(root).mkdir(parents=True, exist_ok=True)

    if target.exists():
        # Phase N UI/load repair: earlier migration builds could leave an empty
        # runtime CSV in neo_data. Because neo_data is preserved between updates,
        # that empty file would block the bundled default seed forever. Preserve
        # non-empty user libraries, but repair empty/header-only runtime files
        # from the bundled extension default when available.
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


def load_generation_styles(root: str | Path | None = None) -> StyleStoreResult:
    path = ensure_generation_styles_file(root)
    styles, encoding = read_styles_csv(path)
    return StyleStoreResult(ok=True, styles=styles, path=str(path), encoding=encoding)


def save_generation_styles(styles: Sequence[dict[str, object]], root: str | Path | None = None) -> StyleStoreResult:
    path = ensure_generation_styles_file(root)
    write_styles_csv(path, styles)
    loaded, encoding = read_styles_csv(path)
    return StyleStoreResult(ok=True, styles=loaded, path=str(path), message="styles_saved", encoding=encoding)


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
    result = save_generation_styles(next_styles, root)
    removed = len(current) - len(next_styles)
    return StyleStoreResult(
        ok=True,
        styles=result.styles,
        path=result.path,
        message="style_deleted" if removed else "style_not_found",
        encoding=result.encoding,
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
    """Import a CSV into the canonical store.

    ``mode='merge'`` keeps current rows and overwrites matching names from the
    import. ``mode='replace'`` writes only the imported rows.
    """

    source = Path(source_csv).expanduser().resolve()
    imported, source_encoding = read_styles_csv(source)
    target = ensure_generation_styles_file(root)

    imports_dir(root).mkdir(parents=True, exist_ok=True)
    archive_name = source.name if source.name.lower().endswith(".csv") else "generation_styles_import.csv"
    shutil.copy2(source, imports_dir(root) / archive_name)

    if mode == "replace":
        next_styles = imported
    elif mode == "merge":
        current = load_generation_styles(root).styles
        next_styles = _dedupe_styles([*current, *imported])
    else:
        raise ValueError("Import mode must be 'merge' or 'replace'")

    write_styles_csv(target, next_styles)
    loaded, encoding = read_styles_csv(target)
    return StyleStoreResult(
        ok=True,
        styles=loaded,
        path=str(target),
        message=f"styles_imported:{len(imported)}:{mode}:source_encoding={source_encoding}",
        encoding=encoding,
    )


def export_generation_styles_path(root: str | Path | None = None, *, filename: str = "generation_styles_export.csv") -> Path:
    """Write a normalized export copy and return its path."""

    current = load_generation_styles(root).styles
    target = exports_dir(root) / filename
    write_styles_csv(target, current)
    return target
