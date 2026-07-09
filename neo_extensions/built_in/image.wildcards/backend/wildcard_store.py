"""V2 Wildcards library store.

Phase C migrates Neo V1 wildcard file discovery/loading into the V2 built-in
extension data path. This module is deliberately storage-only: it does not
resolve prompt text or mutate generation jobs. Runtime seeded resolution starts
in Phase G.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

EXTENSION_ID = "wildcards"
CANONICAL_LIBRARY_ROOT = Path("neo_data/extensions/image/wildcards/library")
LEGACY_IMPORT_ROOT = Path("neo_library_data/wildcards")
SUPPORTED_WILDCARD_EXTENSIONS = (".txt", ".json", ".yaml", ".yml")
TEXT_FILE_COMMENT_PREFIX = "#"


@dataclass(frozen=True)
class WildcardLibraryEntry:
    """Normalized representation of one wildcard file."""

    token: str
    label: str
    relative_path: str
    count: int = 0
    extension: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class WildcardValues:
    """Loaded values for one wildcard token."""

    token: str
    label: str
    relative_path: str
    count: int
    values: list[str]
    extension: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _repo_root_from_here() -> Path:
    # neo_extensions/built_in/image.wildcards/backend/wildcard_store.py
    return Path(__file__).resolve().parents[4]


def _coerce_repo_root(repo_root: str | Path | None = None) -> Path:
    return Path(repo_root).expanduser().resolve() if repo_root else _repo_root_from_here()


def default_wildcard_root(repo_root: str | Path | None = None, *, create: bool = True) -> Path:
    """Return the V2 canonical wildcard library root."""

    root = _coerce_repo_root(repo_root) / CANONICAL_LIBRARY_ROOT
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def legacy_wildcard_root(repo_root: str | Path | None = None, *, create: bool = False) -> Path:
    """Return the legacy V1-style wildcard root used for optional imports."""

    root = _coerce_repo_root(repo_root) / LEGACY_IMPORT_ROOT
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_wildcard_root(
    raw: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    create: bool = True,
) -> Path:
    """Resolve a user/custom wildcard root.

    Blank values resolve to the V2 canonical root. Relative values resolve from
    the repository root so extension state remains portable across machines.
    """

    value = str(raw or "").strip()
    if not value:
        return default_wildcard_root(repo_root, create=create)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = _coerce_repo_root(repo_root) / candidate
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def is_supported_wildcard_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_WILDCARD_EXTENSIONS


def wildcard_token_for_path(root: str | Path, fp: str | Path) -> str:
    """Return the V1-compatible token for a wildcard file path."""

    root_path = Path(root).resolve()
    file_path = Path(fp).resolve()
    rel = file_path.relative_to(root_path)
    return str(rel.with_suffix("")).replace("\\", "/").strip("/")


def _clean_value(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        item = _clean_value(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def read_txt_values(fp: str | Path) -> list[str]:
    """Read a V1-compatible .txt wildcard file."""

    values: list[str] = []
    for line in Path(fp).read_text(encoding="utf-8", errors="ignore").splitlines():
        item = line.strip()
        if item and not item.startswith(TEXT_FILE_COMMENT_PREFIX):
            values.append(item)
    return _dedupe_preserve_order(values)


def _values_from_mapping(payload: dict[Any, Any]) -> list[str]:
    items = payload.get("items")
    if isinstance(items, list):
        return _dedupe_preserve_order(_clean_value(item) for item in items)
    values: list[str] = []
    for value in payload.values():
        if isinstance(value, list):
            values.extend(_clean_value(item) for item in value)
        else:
            values.append(_clean_value(value))
    return _dedupe_preserve_order(values)


def read_json_values(fp: str | Path) -> list[str]:
    """Read V1-compatible .json wildcard shapes."""

    payload = json.loads(Path(fp).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return _dedupe_preserve_order(_clean_value(item) for item in payload)
    if isinstance(payload, dict):
        return _values_from_mapping(payload)
    return []


def read_yaml_values(fp: str | Path) -> list[str]:
    """Read V1-compatible YAML wildcard shapes when PyYAML is available."""

    try:
        import yaml  # type: ignore
    except Exception:
        return []
    try:
        payload = yaml.safe_load(Path(fp).read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        return _dedupe_preserve_order(_clean_value(item) for item in payload)
    if isinstance(payload, dict):
        return _values_from_mapping(payload)
    return []


def load_wildcard_values_file(fp: str | Path) -> list[str]:
    """Load values from .txt, .json, .yaml, or .yml wildcard files."""

    path = Path(fp)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return read_txt_values(path)
    if suffix == ".json":
        return read_json_values(path)
    if suffix in {".yaml", ".yml"}:
        return read_yaml_values(path)
    return []


def list_wildcard_files(
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> list[WildcardLibraryEntry]:
    """List supported wildcard files under a root with V1-compatible tokens."""

    wildcard_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    entries: list[WildcardLibraryEntry] = []
    for fp in sorted(wildcard_root.rglob("*")):
        if not fp.is_file() or not is_supported_wildcard_file(fp):
            continue
        try:
            token = wildcard_token_for_path(wildcard_root, fp)
        except ValueError:
            continue
        try:
            count = len(load_wildcard_values_file(fp))
        except Exception:
            count = 0
        entries.append(
            WildcardLibraryEntry(
                token=token,
                label=f"__{token}__",
                relative_path=str(fp.relative_to(wildcard_root)).replace("\\", "/"),
                count=count,
                extension=fp.suffix.lower(),
            )
        )
    return entries


def find_wildcard_file(
    token: str,
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> Path | None:
    """Find the first supported wildcard file for a token."""

    clean_token = str(token or "").strip().strip("/").replace("\\", "/")
    if not clean_token or ".." in Path(clean_token).parts:
        return None
    wildcard_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    for suffix in SUPPORTED_WILDCARD_EXTENSIONS:
        candidate = (wildcard_root / clean_token).with_suffix(suffix)
        try:
            candidate.resolve().relative_to(wildcard_root)
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_wildcard_values(
    token: str,
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> WildcardValues:
    """Load values for one wildcard token."""

    clean_token = str(token or "").strip().strip("/").replace("\\", "/")
    candidate = find_wildcard_file(clean_token, root, repo_root=repo_root)
    if candidate is None:
        return WildcardValues(
            token=clean_token,
            label=f"__{clean_token}__" if clean_token else "",
            relative_path="",
            count=0,
            values=[],
            extension="",
        )
    wildcard_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    values = load_wildcard_values_file(candidate)
    return WildcardValues(
        token=clean_token,
        label=f"__{clean_token}__",
        relative_path=str(candidate.relative_to(wildcard_root)).replace("\\", "/"),
        count=len(values),
        values=values,
        extension=candidate.suffix.lower(),
    )


def save_wildcard_values_file(
    token: str,
    values: Iterable[str],
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    extension: str = ".txt",
) -> Path:
    """Save values to a wildcard file, defaulting to simple .txt format."""

    clean_token = str(token or "").strip().strip("/").replace("\\", "/")
    if not clean_token or ".." in Path(clean_token).parts:
        raise ValueError("Wildcard token is required and must stay inside the library root.")
    suffix = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    if suffix not in SUPPORTED_WILDCARD_EXTENSIONS:
        raise ValueError(f"Unsupported wildcard extension: {suffix}")
    wildcard_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    target = (wildcard_root / clean_token).with_suffix(suffix)
    try:
        target.resolve().relative_to(wildcard_root)
    except ValueError as exc:
        raise ValueError("Wildcard token escapes the library root.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _dedupe_preserve_order(_clean_value(item) for item in values)
    if suffix == ".json":
        target.write_text(json.dumps({"items": cleaned}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        target.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
    return target


def ensure_wildcard_library(
    repo_root: str | Path | None = None,
    *,
    create_gitkeep: bool = True,
) -> Path:
    """Ensure the V2 wildcard library folder exists."""

    root = default_wildcard_root(repo_root, create=True)
    if create_gitkeep:
        gitkeep = root / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")
    return root


def _copy_supported_files(source_root: Path, target_root: Path) -> int:
    copied = 0
    for fp in sorted(source_root.rglob("*")):
        if not fp.is_file() or not is_supported_wildcard_file(fp):
            continue
        rel = fp.relative_to(source_root)
        target = target_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, target)
        copied += 1
    return copied


def _clear_supported_files(root: Path) -> int:
    removed = 0
    for fp in sorted(root.rglob("*")):
        if fp.is_file() and is_supported_wildcard_file(fp):
            fp.unlink()
            removed += 1
    return removed


def import_wildcard_pack(
    source: str | Path,
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    mode: str = "merge",
) -> dict[str, object]:
    """Import a wildcard directory or .zip pack into the V2 library."""

    source_path = Path(source).expanduser().resolve()
    target_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    replace = str(mode or "merge").lower() == "replace"
    removed = _clear_supported_files(target_root) if replace else 0
    imported = 0

    if source_path.is_dir():
        imported = _copy_supported_files(source_path, target_root)
    elif source_path.is_file() and source_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(source_path, "r") as zf:
            for member in zf.infolist():
                member_path = Path(member.filename)
                if member.is_dir() or member_path.is_absolute() or ".." in member_path.parts:
                    continue
                if member_path.suffix.lower() not in SUPPORTED_WILDCARD_EXTENSIONS:
                    continue
                target = target_root / member_path
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                imported += 1
    else:
        raise ValueError("Wildcard import source must be a directory or .zip file.")

    return {
        "ok": True,
        "mode": "replace" if replace else "merge",
        "root": str(target_root),
        "source": str(source_path),
        "removed": removed,
        "imported": imported,
        "entries": [entry.to_dict() for entry in list_wildcard_files(target_root)],
    }


def export_wildcard_pack(
    root: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    target_zip: str | Path | None = None,
) -> Path:
    """Export supported wildcard files to a zip pack."""

    wildcard_root = resolve_wildcard_root(root, repo_root=repo_root, create=True)
    if target_zip is None:
        target = _coerce_repo_root(repo_root) / "neo_data" / "exports" / "wildcards_pack.zip"
    else:
        target = Path(target_zip).expanduser()
        if not target.is_absolute():
            target = _coerce_repo_root(repo_root) / target
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(wildcard_root.rglob("*")):
            if fp.is_file() and is_supported_wildcard_file(fp):
                zf.write(fp, arcname=str(fp.relative_to(wildcard_root)).replace("\\", "/"))
    return target.resolve()


def phase_c_store_status(repo_root: str | Path | None = None) -> dict[str, object]:
    """Return the Phase C implementation status for docs/tests/diagnostics."""

    root = ensure_wildcard_library(repo_root)
    return {
        "extension_id": EXTENSION_ID,
        "phase": "C-wildcard-store-migration",
        "implemented": True,
        "canonical_library_root": str(root),
        "legacy_import_root": str(legacy_wildcard_root(repo_root)),
        "supported_extensions": list(SUPPORTED_WILDCARD_EXTENSIONS),
        "runtime_resolution": False,
        "entries": [entry.to_dict() for entry in list_wildcard_files(root)],
    }


# Backward-compatible skeleton diagnostic used by Phase B tests.
def phase_b_store_status() -> dict[str, object]:
    return {
        "extension_id": EXTENSION_ID,
        "phase": "C-wildcard-store-migration",
        "implemented": True,
        "canonical_library_root": str(CANONICAL_LIBRARY_ROOT),
        "supported_extensions": list(SUPPORTED_WILDCARD_EXTENSIONS),
        "runtime_resolution": False,
    }
