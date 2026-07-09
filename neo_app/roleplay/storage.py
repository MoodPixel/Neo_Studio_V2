from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.schema import RoleplayFoundationDirectory, RoleplayFoundationState

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
ROLEPLAY_DATA_ROOT: Final[Path] = ROOT_DIR / "neo_data" / "roleplay"
ROLEPLAY_FOUNDATION_MANIFEST: Final[Path] = ROLEPLAY_DATA_ROOT / "foundation_manifest.json"
ROLEPLAY_SQLITE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "roleplay.sqlite"

ROLEPLAY_FOUNDATION_DIRECTORIES: Final[tuple[str, ...]] = (
    "entities",
    "source_documents",
    "drafts",
    "helper_outputs",
    "canon_records",
    "memory_fragments",
    "relationships",
    "shared_memories",
    "runtime_bundles",
    "packages",
    "imports",
    "exports",
    "projects",
    "retrieval",
    "storylines",
    "story_sessions",
    "story_checkpoints",
    "story_drafts",
    "story_snapshots",
    "story_branches",
    "cloud_sync",
    "package_registry",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_existing_manifest() -> dict:
    if not ROLEPLAY_FOUNDATION_MANIFEST.exists():
        return {}
    try:
        return json.loads(ROLEPLAY_FOUNDATION_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_manifest(payload: dict) -> None:
    ROLEPLAY_FOUNDATION_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ROLEPLAY_FOUNDATION_MANIFEST.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_roleplay_foundation(*, write_manifest: bool = True) -> RoleplayFoundationState:
    """Create/check the Neo-owned Roleplay V2 data foundation.

    Phase 2 creates folders and a foundation manifest. Phase 8D can layer the
    Roleplay SQLite memory schema onto this same Neo-owned root without changing
    the foundation directory contract.
    """

    ROLEPLAY_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    existing_manifest = _read_existing_manifest()
    created_at = str(existing_manifest.get("created_at") or _now())
    checked_at = _now()

    directories: list[RoleplayFoundationDirectory] = []
    for directory_name in ROLEPLAY_FOUNDATION_DIRECTORIES:
        path = ROLEPLAY_DATA_ROOT / directory_name
        path.mkdir(parents=True, exist_ok=True)
        directories.append(
            RoleplayFoundationDirectory(
                directory_id=directory_name,
                path=_relative_to_root(path),
                exists=path.exists() and path.is_dir(),
            )
        )

    missing = [item.directory_id for item in directories if not item.exists]
    state = RoleplayFoundationState(
        created_at=created_at,
        checked_at=checked_at,
        data_root=_relative_to_root(ROLEPLAY_DATA_ROOT),
        manifest_path=_relative_to_root(ROLEPLAY_FOUNDATION_MANIFEST),
        sqlite_path=_relative_to_root(ROLEPLAY_SQLITE_PATH),
        directories=directories,
        missing_directories=missing,
        ready=not missing,
    )

    if write_manifest:
        _write_manifest(model_to_dict(state))

    return state


def roleplay_foundation_payload(*, write_manifest: bool = True) -> dict:
    return model_to_dict(ensure_roleplay_foundation(write_manifest=write_manifest))
