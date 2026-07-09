from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping

try:
    from .capability_registry import output_modes_map
except Exception:  # pragma: no cover - direct script/test execution fallback
    from capability_registry import output_modes_map

try:
    from .asset_library import build_asset_manifest, ASSET_CONTRACT_VERSION
    from .editor_export import build_editor_export_preset, EDITOR_EXPORT_CONTRACT_VERSION
except Exception:  # pragma: no cover - direct script/test execution fallback
    from asset_library import build_asset_manifest, ASSET_CONTRACT_VERSION
    from editor_export import build_editor_export_preset, EDITOR_EXPORT_CONTRACT_VERSION

EXTENSION_ID = "image.layerdiffuse"
OUTPUT_CONTRACT_VERSION = "layerdiffuse-output-metadata-contract-v1"
METADATA_BLOCK_KEY = "_neo_external_extensions"

OUTPUT_TYPE_TO_FILENAME = {
    "rgba_image": "asset_rgba.png",
    "rgb_image": "asset_rgb.png",
    "alpha_mask": "asset_alpha.png",
    "preview_image": "preview.jpg",
    "composited_image": "composited.png",
}

OUTPUT_TYPE_TO_ROLE = {
    "rgba_image": "primary",
    "rgb_image": "sidecar",
    "alpha_mask": "mask",
    "preview_image": "preview",
    "composited_image": "primary",
}

MODE_OUTPUTS = output_modes_map()

OUTPUT_TYPE_TO_FILENAME.update({
    "background_image": "background.png",
})
OUTPUT_TYPE_TO_ROLE.update({
    "background_image": "primary",
})


SAVE_FLAG_BY_OUTPUT = {
    "rgba_image": "save_rgba",
    "rgb_image": "save_rgb",
    "alpha_mask": "save_alpha",
    "preview_image": None,
    "composited_image": None,
    "background_image": None,
}

VALID_OUTPUT_POLICIES = {"preview", "new_run", "append", "replace"}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_run_id(value: Any) -> str:
    text = str(value or "layerdiffuse_run").strip().replace("\\", "/")
    text = text.split("/")[-1]
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
    return safe or "layerdiffuse_run"


def _posix_join(*parts: Any) -> str:
    clean = [str(p).strip("/") for p in parts if p is not None and str(p).strip("/")]
    return str(PurePosixPath(*clean)) if clean else ""


@dataclass
class LayerDiffuseOutputItem:
    type: str
    role: str
    filename: str
    relative_path: str
    required: bool = True
    save: bool = True
    present: bool = False
    source_key: str | None = None
    disabled_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LayerDiffuseOutputBundle:
    extension_id: str = EXTENSION_ID
    contract_version: str = OUTPUT_CONTRACT_VERSION
    run_id: str = "layerdiffuse_run"
    mode: str = "transparent_asset"
    output_policy: str = "new_run"
    base_dir: str = "layerdiffuse_outputs"
    outputs: list[LayerDiffuseOutputItem] = field(default_factory=list)
    metadata_filename: str = "metadata.json"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["valid"] = self.valid
        data["outputs"] = [item.to_dict() for item in self.outputs]
        return data


def expected_output_types(mode: str, decode_mode: str | None = None) -> list[str]:
    """Return expected LayerDiffuse output roles for a mode.

    decode_mode can narrow/expand the expected sidecars without hiding the raw user state.
    """
    output_types = list(MODE_OUTPUTS.get(mode or "transparent_asset", MODE_OUTPUTS["transparent_asset"]))
    if decode_mode == "preview_only":
        return [item for item in output_types if item in {"preview_image", "composited_image", "background_image"}]
    if decode_mode == "rgba" and "rgb_image" in output_types:
        output_types.remove("rgb_image")
    if decode_mode == "split" and "alpha_mask" not in output_types:
        output_types.append("alpha_mask")
    return output_types


def build_output_bundle(
    raw_state: Mapping[str, Any] | None,
    effective_state: Mapping[str, Any] | None,
    *,
    run_id: str | None = None,
    base_dir: str = "layerdiffuse_outputs",
    produced_files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a transparent output bundle contract for LayerDiffuse.

    This does not touch files. It declares where outputs should be saved and records
    which outputs were actually produced when produced_files is supplied by the output collector.
    """
    raw = dict(raw_state or {})
    effective = dict(effective_state or {})
    mode = str(effective.get("mode") or raw.get("mode") or "transparent_asset")
    decode_mode = str(effective.get("decode_mode") or raw.get("decode_mode") or "rgba")
    output_policy = str(effective.get("output_policy") or raw.get("output_policy") or "new_run")
    output_policy = output_policy if output_policy in VALID_OUTPUT_POLICIES else "new_run"
    safe_run_id = _safe_run_id(run_id or effective.get("run_id") or raw.get("run_id") or mode)
    root = _posix_join(base_dir, safe_run_id)
    produced = dict(produced_files or {})

    warnings: list[str] = []
    errors: list[str] = []
    items: list[LayerDiffuseOutputItem] = []

    for output_type in expected_output_types(mode, decode_mode):
        flag = SAVE_FLAG_BY_OUTPUT.get(output_type)
        save_enabled = True if flag is None else _truthy(raw.get(flag), True)
        role = OUTPUT_TYPE_TO_ROLE.get(output_type, "sidecar")
        filename = OUTPUT_TYPE_TO_FILENAME.get(output_type, f"{output_type}.png")
        path = produced.get(output_type) or _posix_join(root, filename)
        present = output_type in produced
        disabled_reason = None
        if not save_enabled:
            disabled_reason = f"{flag}=false" if flag else "save disabled"
        if output_policy == "preview" and role not in {"preview"}:
            disabled_reason = "preview output policy does not persist final/sidecar files"
            save_enabled = False
        items.append(
            LayerDiffuseOutputItem(
                type=output_type,
                role=role,
                filename=filename,
                relative_path=path,
                required=role in {"primary", "mask", "preview"},
                save=save_enabled,
                present=present,
                source_key=output_type,
                disabled_reason=disabled_reason,
            )
        )

    if output_policy == "replace" and not (raw.get("replace_target_id") or effective.get("replace_target_id")):
        errors.append("replace output policy requires replace_target_id before save metadata can target an output")
    if not _truthy(raw.get("save_metadata"), True):
        warnings.append("save_metadata=false; metadata bundle is declared but persistence may be skipped")

    bundle = LayerDiffuseOutputBundle(
        run_id=safe_run_id,
        mode=mode,
        output_policy=output_policy,
        base_dir=root,
        outputs=items,
        warnings=warnings,
        errors=errors,
    )
    return bundle.to_dict()


def build_metadata_block(
    raw_state: Mapping[str, Any] | None,
    effective_state: Mapping[str, Any] | None,
    workflow_patch: Mapping[str, Any] | None = None,
    *,
    run_id: str | None = None,
    base_dir: str = "layerdiffuse_outputs",
    produced_files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    raw = dict(raw_state or {})
    effective = dict(effective_state or {})
    patch = dict(workflow_patch or {})
    output_bundle = build_output_bundle(raw, effective, run_id=run_id, base_dir=base_dir, produced_files=produced_files)
    asset_manifest = build_asset_manifest(output_bundle, raw, effective)
    editor_export = build_editor_export_preset(output_bundle, asset_manifest, raw, effective)
    return {
        METADATA_BLOCK_KEY: {
            EXTENSION_ID: {
                "output_contract_version": OUTPUT_CONTRACT_VERSION,
                "enabled": _truthy(raw.get("enabled"), False),
                "active": _truthy(effective.get("active", effective.get("effective_enabled")), False),
                "mode": effective.get("mode") or raw.get("mode") or "transparent_asset",
                "workflow_template": effective.get("workflow_template"),
                "patch_strategy": effective.get("patch_strategy") or patch.get("strategy"),
                "output_policy": effective.get("output_policy") or raw.get("output_policy") or "new_run",
                "decode_mode": effective.get("decode_mode") or raw.get("decode_mode") or "rgba",
                "raw_state": raw,
                "effective_state": effective,
                "workflow_patch": patch,
                "output_bundle": output_bundle,
                "asset_contract_version": ASSET_CONTRACT_VERSION,
                "asset_manifest": asset_manifest,
                "editor_export_contract_version": EDITOR_EXPORT_CONTRACT_VERSION,
                "editor_export_preset": editor_export,
                "hidden_mutations_allowed": False,
                "visible": True,
            }
        }
    }


def validate_output_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    data = dict(bundle or {})
    if data.get("extension_id") != EXTENSION_ID:
        errors.append("output bundle extension_id mismatch")
    if data.get("contract_version") != OUTPUT_CONTRACT_VERSION:
        errors.append("output bundle contract_version mismatch")
    outputs = data.get("outputs") or []
    if not isinstance(outputs, list) or not outputs:
        errors.append("output bundle must contain at least one output item")
    for item in outputs:
        if not item.get("type"):
            errors.append("output item missing type")
        if not item.get("role"):
            errors.append(f"output item {item.get('type') or '<unknown>'} missing role")
        if item.get("save") and not item.get("relative_path"):
            errors.append(f"output item {item.get('type')} is saveable but missing relative_path")
        if item.get("role") == "mask" and item.get("type") != "alpha_mask":
            warnings.append(f"mask role should normally use alpha_mask, got {item.get('type')}")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "EXTENSION_ID",
    "OUTPUT_CONTRACT_VERSION",
    "expected_output_types",
    "build_output_bundle",
    "build_metadata_block",
    "validate_output_bundle",
    "build_asset_manifest",
    "ASSET_CONTRACT_VERSION",
    "build_editor_export_preset",
    "EDITOR_EXPORT_CONTRACT_VERSION",
]
