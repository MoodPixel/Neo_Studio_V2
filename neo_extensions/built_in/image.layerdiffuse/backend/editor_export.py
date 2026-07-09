from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping

EXTENSION_ID = "image.layerdiffuse"
EDITOR_EXPORT_CONTRACT_VERSION = "layerdiffuse-editor-export-contract-v1"

SUPPORTED_EXPORT_PRESETS = {
    "after_effects_png_bundle",
    "transparent_overlay_pack",
    "thumbnail_poster_asset_pack",
}

EXPORT_PRESET_LABELS = {
    "after_effects_png_bundle": "After Effects PNG Bundle",
    "transparent_overlay_pack": "Transparent Overlay Pack",
    "thumbnail_poster_asset_pack": "Thumbnail / Poster Asset Pack",
}

OUTPUT_TO_EXPORT_ROLE = {
    "rgba_image": "rgba_asset",
    "rgb_image": "rgb_reference",
    "alpha_mask": "alpha_matte",
    "preview_image": "preview",
    "composited_image": "composited_output",
}


def _safe_text(value: Any, fallback: str = "layerdiffuse_export") -> str:
    text = str(value or fallback).strip().replace("\\", "/")
    text = text.split("/")[-1]
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
    return safe or fallback


def _posix_join(*parts: Any) -> str:
    clean = [str(p).strip("/") for p in parts if p is not None and str(p).strip("/")]
    return str(PurePosixPath(*clean)) if clean else ""


@dataclass
class EditorExportFile:
    type: str
    role: str
    source_path: str
    export_name: str
    required: bool = False
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EditorExportPreset:
    extension_id: str = EXTENSION_ID
    contract_version: str = EDITOR_EXPORT_CONTRACT_VERSION
    preset: str = "after_effects_png_bundle"
    label: str = "After Effects PNG Bundle"
    run_id: str = "layerdiffuse_run"
    mode: str = "transparent_asset"
    export_root: str = "exports/layerdiffuse"
    files: list[EditorExportFile] = field(default_factory=list)
    manifest_filename: str = "editor_export_manifest.json"
    ae_import_notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    hidden_export_allowed: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["valid"] = self.valid
        data["files"] = [item.to_dict() for item in self.files]
        data["manifest_path"] = _posix_join(self.export_root, self.manifest_filename)
        return data


def _default_export_preset(mode: str, raw_state: Mapping[str, Any]) -> str:
    requested = str(raw_state.get("editor_export_preset") or raw_state.get("export_preset") or "").strip()
    if requested in SUPPORTED_EXPORT_PRESETS:
        return requested
    if mode == "overlay_fx":
        return "transparent_overlay_pack"
    if mode in {"foreground_on_background", "background_aware_blend"}:
        return "thumbnail_poster_asset_pack"
    return "after_effects_png_bundle"


def _export_filename(output_type: str, source_path: str) -> str:
    suffix = str(PurePosixPath(source_path).suffix or ".png")
    return {
        "rgba_image": f"asset_rgba{suffix if suffix.lower() == '.png' else '.png'}",
        "rgb_image": f"asset_rgb{suffix if suffix.lower() in {'.png', '.jpg', '.jpeg'} else '.png'}",
        "alpha_mask": "asset_alpha.png",
        "preview_image": "preview.jpg",
        "composited_image": "composited.png",
    }.get(output_type, f"{_safe_text(output_type, 'asset')}{suffix}")


def _ae_notes(preset: str, has_alpha: bool, has_rgba: bool) -> list[str]:
    notes = [
        "Import the RGBA PNG directly into After Effects as straight alpha unless your project requires premultiplied interpretation.",
        "Keep the RGB and alpha sidecars linked to the metadata manifest for manual matte repair or luma matte workflows.",
        "Do not overwrite the original LayerDiffuse output paths; this preset is an export view over visible saved outputs.",
    ]
    if preset == "transparent_overlay_pack":
        notes.append("Use Screen/Add/Normal blend modes depending on the overlay design; alpha PNG remains the primary reusable asset.")
    if preset == "thumbnail_poster_asset_pack":
        notes.append("Use composited output as the poster base and RGBA/alpha sidecars as editable foreground assets when available.")
    if not has_alpha:
        notes.append("Alpha mask sidecar was not declared; matte refinement will depend on the RGBA asset only.")
    if not has_rgba:
        notes.append("RGBA asset was not declared; this export may be preview/composite-only.")
    return notes


def build_editor_export_preset(
    output_bundle: Mapping[str, Any] | None,
    asset_manifest: Mapping[str, Any] | None = None,
    raw_state: Mapping[str, Any] | None = None,
    effective_state: Mapping[str, Any] | None = None,
    *,
    preset: str | None = None,
    export_root: str = "exports/layerdiffuse",
) -> dict[str, Any]:
    """Declare editor-friendly export files for LayerDiffuse outputs.

    This is intentionally side-effect free. It does not copy, rename, zip, or write
    files. Neo's export/download layer can consume this contract later.
    """
    bundle = dict(output_bundle or {})
    raw = dict(raw_state or {})
    effective = dict(effective_state or {})
    assets = dict(asset_manifest or {})
    mode = str(bundle.get("mode") or effective.get("mode") or raw.get("mode") or "transparent_asset")
    run_id = _safe_text(bundle.get("run_id") or effective.get("run_id") or raw.get("run_id") or mode, mode)
    selected_preset = preset if preset in SUPPORTED_EXPORT_PRESETS else _default_export_preset(mode, raw)
    root = _posix_join(export_root, run_id, selected_preset)

    warnings: list[str] = []
    errors: list[str] = []
    files: list[EditorExportFile] = []
    outputs = [dict(item) for item in bundle.get("outputs") or [] if isinstance(item, Mapping)]

    if bundle.get("extension_id") not in {None, EXTENSION_ID}:
        errors.append("editor export output_bundle extension_id mismatch")
    if assets and assets.get("extension_id") not in {None, EXTENSION_ID}:
        errors.append("editor export asset_manifest extension_id mismatch")

    for item in outputs:
        if item.get("save") is False:
            continue
        source_path = item.get("relative_path")
        output_type = item.get("type")
        if not output_type or not source_path:
            warnings.append(f"export skipped output without type/path:{output_type or '<unknown>'}")
            continue
        role = OUTPUT_TO_EXPORT_ROLE.get(str(output_type), "sidecar")
        required = role in {"rgba_asset", "alpha_matte", "composited_output"}
        files.append(
            EditorExportFile(
                type=str(output_type),
                role=role,
                source_path=str(source_path),
                export_name=_export_filename(str(output_type), str(source_path)),
                required=required,
                visible=True,
            )
        )

    has_rgba = any(item.role == "rgba_asset" for item in files)
    has_alpha = any(item.role == "alpha_matte" for item in files)
    has_composite = any(item.role == "composited_output" for item in files)

    if selected_preset in {"after_effects_png_bundle", "transparent_overlay_pack"} and not has_rgba:
        warnings.append("editor export has no RGBA asset; export will be limited")
    if selected_preset == "after_effects_png_bundle" and not has_alpha:
        warnings.append("editor export has no alpha matte sidecar")
    if selected_preset == "thumbnail_poster_asset_pack" and not (has_composite or has_rgba):
        errors.append("thumbnail/poster export requires a composited image or RGBA asset")
    if not files:
        errors.append("editor export has no visible files to declare")

    preset_obj = EditorExportPreset(
        preset=selected_preset,
        label=EXPORT_PRESET_LABELS.get(selected_preset, selected_preset),
        run_id=run_id,
        mode=mode,
        export_root=root,
        files=files,
        ae_import_notes=_ae_notes(selected_preset, has_alpha, has_rgba),
        warnings=warnings,
        errors=errors,
    )
    return preset_obj.to_dict()


def validate_editor_export_preset(export: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(export or {})
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("extension_id") != EXTENSION_ID:
        errors.append("editor export extension_id mismatch")
    if data.get("contract_version") != EDITOR_EXPORT_CONTRACT_VERSION:
        errors.append("editor export contract_version mismatch")
    if data.get("hidden_export_allowed") is not False:
        errors.append("editor export must explicitly disallow hidden exports")
    if data.get("preset") not in SUPPORTED_EXPORT_PRESETS:
        errors.append("unsupported editor export preset")
    files = data.get("files") or []
    if not files:
        errors.append("editor export must declare at least one file")
    for item in files:
        if not item.get("visible", True):
            errors.append(f"export file {item.get('export_name') or '<unknown>'} is hidden")
        if not item.get("source_path"):
            errors.append(f"export file {item.get('export_name') or '<unknown>'} missing source_path")
        if not item.get("export_name"):
            errors.append("export file missing export_name")
    if not data.get("manifest_path"):
        warnings.append("editor export manifest_path missing")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "EXTENSION_ID",
    "EDITOR_EXPORT_CONTRACT_VERSION",
    "SUPPORTED_EXPORT_PRESETS",
    "build_editor_export_preset",
    "validate_editor_export_preset",
]
