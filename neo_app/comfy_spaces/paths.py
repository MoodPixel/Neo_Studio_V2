from __future__ import annotations

import re
from pathlib import Path
from typing import Final


SPACE_MANIFEST_FILENAME: Final[str] = "space_manifest.json"
SPACE_WORKFLOW_FILENAME: Final[str] = "workflow.json"
SPACE_UI_FILENAME: Final[str] = "space_ui.json"
SPACE_GRAPH_ANCHORS_FILENAME: Final[str] = "graph_anchors.json"
SPACE_STAGES_FILENAME: Final[str] = "stages.json"
SPACE_DYNAMIC_CONTROLS_FILENAME: Final[str] = "dynamic_controls.json"
SPACE_BUILDER_DRAFT_FILENAME: Final[str] = "builder_draft.json"
SPACE_DRAFT_ANALYSIS_FILENAME: Final[str] = "analysis.json"
SPACE_PREVIEW_MANIFEST_FILENAME: Final[str] = "preview_manifest.json"
SPACE_PREVIEW_UI_FILENAME: Final[str] = "preview_ui.json"
SPACE_PREVIEW_DYNAMIC_CONTROLS_FILENAME: Final[str] = "preview_dynamic_controls.json"
SPACE_PUBLICATION_RECEIPT_FILENAME: Final[str] = "publication.json"
SPACE_MIGRATION_FILENAME: Final[str] = "migration.json"
SPACE_REPLACEMENT_PLAN_FILENAME: Final[str] = "replacement_plan.json"
SPACE_VERSION_STATE_FILENAME: Final[str] = "version_state.json"

# Runtime-only roots. These are expanded by neo_app.runtime_data during normal
# first-run/bootstrap creation. They intentionally live outside neo_extensions.
COMFY_SPACE_RUNTIME_DIRECTORIES: Final[tuple[str, ...]] = (
    "comfy_spaces/spaces",
    "comfy_spaces/drafts",
    "comfy_spaces/imports",
    "comfy_spaces/exports",
    "comfy_spaces/assets",
    "comfy_spaces/variants",
    "comfy_spaces/presets",
    "comfy_spaces/health",
    "inputs/comfy_spaces",
    "outputs/comfy_spaces",
    "runtime/jobs/comfy_spaces",
    "logs/comfy_spaces",
    "logs/comfy_spaces/runs",
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SAFE_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _root(root_dir: Path | str | None = None) -> Path:
    return Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parents[2]


def _safe_token(value: str, *, label: str, pattern: re.Pattern[str], lowercase: bool = False) -> str:
    text = str(value or "").strip()
    if lowercase:
        text = text.lower()
    if not pattern.fullmatch(text):
        raise ValueError(f"Invalid {label}: {value!r}")
    return text


def comfy_spaces_runtime_root(root_dir: Path | str | None = None) -> Path:
    return _root(root_dir) / "neo_data" / "comfy_spaces"


def comfy_spaces_library_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "spaces"


def comfy_space_root(space_id: str, root_dir: Path | str | None = None) -> Path:
    safe_space_id = _safe_token(space_id, label="space_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_library_root(root_dir) / safe_space_id


def comfy_space_version_root(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    safe_version = _safe_token(version, label="space version", pattern=_SAFE_VERSION_RE)
    return comfy_space_root(space_id, root_dir) / "versions" / safe_version


def comfy_space_manifest_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_MANIFEST_FILENAME


def comfy_space_workflow_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_WORKFLOW_FILENAME


def comfy_space_ui_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_UI_FILENAME


def comfy_space_graph_anchors_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_GRAPH_ANCHORS_FILENAME


def comfy_space_stages_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_STAGES_FILENAME


def comfy_space_dynamic_controls_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_DYNAMIC_CONTROLS_FILENAME


def comfy_space_publication_receipt_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_PUBLICATION_RECEIPT_FILENAME


def comfy_space_migration_path(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_version_root(space_id, version, root_dir) / SPACE_MIGRATION_FILENAME


def comfy_space_version_state_path(space_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_root(space_id, root_dir) / SPACE_VERSION_STATE_FILENAME


def comfy_space_draft_replacement_plan_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_REPLACEMENT_PLAN_FILENAME



def comfy_spaces_dependency_health_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "health"


def comfy_space_dependency_health_version_root(space_id: str, version: str, root_dir: Path | str | None = None) -> Path:
    safe_space_id = _safe_token(space_id, label="space_id", pattern=_SAFE_ID_RE, lowercase=True)
    safe_version = _safe_token(version, label="space version", pattern=_SAFE_VERSION_RE)
    return comfy_spaces_dependency_health_root(root_dir) / safe_space_id / safe_version


def comfy_space_dependency_health_path(space_id: str, version: str, runtime_profile_id: str, root_dir: Path | str | None = None) -> Path:
    safe_runtime = _safe_token(runtime_profile_id, label="runtime_profile_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_space_dependency_health_version_root(space_id, version, root_dir) / f"{safe_runtime}.json"


def comfy_spaces_variants_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "variants"


def comfy_space_variant_path(variant_id: str, root_dir: Path | str | None = None) -> Path:
    safe_variant_id = _safe_token(variant_id, label="variant_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_variants_root(root_dir) / f"{safe_variant_id}.json"


def comfy_spaces_presets_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "presets"


def comfy_space_preset_path(preset_id: str, root_dir: Path | str | None = None) -> Path:
    safe_preset_id = _safe_token(preset_id, label="preset_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_presets_root(root_dir) / f"{safe_preset_id}.json"


def comfy_spaces_assets_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "assets"


def comfy_space_asset_record_path(asset_id: str, root_dir: Path | str | None = None) -> Path:
    safe_asset_id = _safe_token(asset_id, label="asset_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_assets_root(root_dir) / f"{safe_asset_id}.json"


def comfy_space_input_assets_root(root_dir: Path | str | None = None) -> Path:
    return _root(root_dir) / "neo_data" / "inputs" / "comfy_spaces"


def comfy_space_output_assets_root(root_dir: Path | str | None = None) -> Path:
    return _root(root_dir) / "neo_data" / "outputs" / "comfy_spaces"


def comfy_space_asset_storage_dir(asset_id: str, *, direction: str, root_dir: Path | str | None = None) -> Path:
    safe_asset_id = _safe_token(asset_id, label="asset_id", pattern=_SAFE_ID_RE, lowercase=True)
    clean_direction = str(direction or "").strip().lower()
    if clean_direction == "input":
        base = comfy_space_input_assets_root(root_dir)
    elif clean_direction in {"output", "intermediate"}:
        base = comfy_space_output_assets_root(root_dir)
    else:
        raise ValueError(f"Invalid asset direction: {direction!r}")
    return base / safe_asset_id


def comfy_spaces_drafts_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "drafts"


def comfy_space_draft_root(draft_id: str, root_dir: Path | str | None = None) -> Path:
    safe_draft_id = _safe_token(draft_id, label="draft_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_drafts_root(root_dir) / safe_draft_id


def comfy_space_builder_draft_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_BUILDER_DRAFT_FILENAME


def comfy_space_draft_workflow_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_WORKFLOW_FILENAME


def comfy_space_draft_analysis_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_DRAFT_ANALYSIS_FILENAME


def comfy_space_draft_preview_manifest_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_PREVIEW_MANIFEST_FILENAME


def comfy_space_draft_preview_ui_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_PREVIEW_UI_FILENAME


def comfy_space_draft_preview_dynamic_controls_path(draft_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_space_draft_root(draft_id, root_dir) / SPACE_PREVIEW_DYNAMIC_CONTROLS_FILENAME


def comfy_spaces_imports_root(root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "imports"


def comfy_space_import_root(import_id: str, root_dir: Path | str | None = None) -> Path:
    safe_import_id = _safe_token(import_id, label="import_id", pattern=_SAFE_ID_RE, lowercase=True)
    return comfy_spaces_imports_root(root_dir) / safe_import_id

