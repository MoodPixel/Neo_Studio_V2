from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping

EXTENSION_ID = "image.layerdiffuse"
ASSET_CONTRACT_VERSION = "layerdiffuse-asset-library-contract-v1"
ASSET_KIND_BY_OUTPUT_TYPE = {
    "rgba_image": "transparent_rgba",
    "rgb_image": "rgb_sidecar",
    "alpha_mask": "alpha_mask",
    "preview_image": "preview",
    "composited_image": "composited_image",
}
REUSABLE_OUTPUT_TYPES = {"rgba_image", "alpha_mask", "rgb_image", "composited_image"}
PRIMARY_ASSET_OUTPUT_TYPES = {"rgba_image", "composited_image"}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_text(value: Any, fallback: str = "layerdiffuse") -> str:
    text = str(value or fallback).strip().replace("\\", "/")
    text = text.split("/")[-1]
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
    return safe or fallback


def _posix_join(*parts: Any) -> str:
    clean = [str(p).strip("/") for p in parts if p is not None and str(p).strip("/")]
    return str(PurePosixPath(*clean)) if clean else ""


@dataclass
class LayerDiffuseAssetRecord:
    asset_id: str
    extension_id: str = EXTENSION_ID
    contract_version: str = ASSET_CONTRACT_VERSION
    kind: str = "transparent_rgba"
    role: str = "primary"
    mode: str = "transparent_asset"
    source_output_type: str = "rgba_image"
    relative_path: str = ""
    preview_path: str | None = None
    alpha_path: str | None = None
    rgb_path: str | None = None
    metadata_path: str | None = None
    reusable_as: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    visible: bool = True
    save: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _asset_reuse_targets(output_type: str, mode: str) -> list[str]:
    targets = []
    if output_type == "rgba_image":
        targets.extend(["foreground_source", "overlay", "image_layer", "after_effects_asset"])
    elif output_type == "alpha_mask":
        targets.extend(["mask", "matte", "after_effects_luma_matte"])
    elif output_type == "rgb_image":
        targets.extend(["rgb_reference", "manual_composite_source"])
    elif output_type == "composited_image":
        targets.extend(["image_output", "poster_composite", "new_run_source"])
    if mode == "overlay_fx" and "overlay" not in targets:
        targets.append("overlay")
    return targets


def _asset_tags(output_type: str, mode: str) -> list[str]:
    tags = ["layerdiffuse", mode, ASSET_KIND_BY_OUTPUT_TYPE.get(output_type, output_type)]
    if output_type == "rgba_image":
        tags.append("transparent_png")
    if output_type == "alpha_mask":
        tags.append("alpha")
    if mode == "overlay_fx":
        tags.append("overlay_fx")
    return tags


def build_asset_manifest(
    output_bundle: Mapping[str, Any] | None,
    raw_state: Mapping[str, Any] | None = None,
    effective_state: Mapping[str, Any] | None = None,
    *,
    asset_root: str = "neo_library_data/assets/layerdiffuse",
) -> dict[str, Any]:
    """Build asset-library registration records from a LayerDiffuse output bundle.

    This function is intentionally side-effect free. It returns records that Neo's
    asset library can persist when output_policy permits it. The extension never
    writes directly into the asset index from here.
    """
    bundle = dict(output_bundle or {})
    raw = dict(raw_state or {})
    effective = dict(effective_state or {})
    mode = str(bundle.get("mode") or effective.get("mode") or raw.get("mode") or "transparent_asset")
    output_policy = str(bundle.get("output_policy") or effective.get("output_policy") or raw.get("output_policy") or "new_run")
    run_id = _safe_text(bundle.get("run_id") or effective.get("run_id") or mode, mode)
    metadata_path = _posix_join(bundle.get("base_dir") or "layerdiffuse_outputs", bundle.get("metadata_filename") or "metadata.json")
    outputs = [dict(item) for item in bundle.get("outputs") or [] if isinstance(item, Mapping)]

    warnings: list[str] = []
    errors: list[str] = []
    records: list[dict[str, Any]] = []

    if bundle.get("extension_id") not in {None, EXTENSION_ID}:
        errors.append("asset manifest extension_id mismatch")

    if output_policy == "preview":
        warnings.append("preview output policy does not register reusable assets")
    if output_policy == "replace":
        warnings.append("replace output policy updates a visible target; asset registration is sidecar-only unless Neo persists it")

    preview_path = next((item.get("relative_path") for item in outputs if item.get("type") == "preview_image"), None)
    alpha_path = next((item.get("relative_path") for item in outputs if item.get("type") == "alpha_mask"), None)
    rgb_path = next((item.get("relative_path") for item in outputs if item.get("type") == "rgb_image"), None)

    for item in outputs:
        output_type = item.get("type")
        if output_type not in REUSABLE_OUTPUT_TYPES:
            continue
        if not _truthy(item.get("save"), True):
            continue
        if output_policy == "preview":
            continue
        relative_path = item.get("relative_path")
        if not relative_path:
            warnings.append(f"asset output skipped without relative_path:{output_type}")
            continue
        asset_id = f"layerdiffuse_{run_id}_{_safe_text(output_type, 'asset')}"
        record = LayerDiffuseAssetRecord(
            asset_id=asset_id,
            kind=ASSET_KIND_BY_OUTPUT_TYPE.get(output_type, "layerdiffuse_asset"),
            role="primary" if output_type in PRIMARY_ASSET_OUTPUT_TYPES else "sidecar",
            mode=mode,
            source_output_type=output_type,
            relative_path=relative_path,
            preview_path=preview_path,
            alpha_path=alpha_path if output_type != "alpha_mask" else relative_path,
            rgb_path=rgb_path if output_type != "rgb_image" else relative_path,
            metadata_path=metadata_path,
            reusable_as=_asset_reuse_targets(output_type, mode),
            tags=_asset_tags(output_type, mode),
            source={
                "source_type": raw.get("source_type") or effective.get("source_resolved", {}).get("type"),
                "source_image_id": raw.get("source_image_id") or effective.get("source_resolved", {}).get("source_image_id"),
                "background_image_id": raw.get("background_image_id") or effective.get("source_resolved", {}).get("background_image_id"),
                "foreground_image_id": raw.get("foreground_image_id") or effective.get("source_resolved", {}).get("foreground_image_id"),
            },
            policy={
                "output_policy": output_policy,
                "append_registers_assets": output_policy == "append",
                "new_run_registers_assets": output_policy == "new_run",
                "hidden_asset_registration_allowed": False,
            },
            save=True,
            visible=True,
        ).to_dict()
        records.append(record)

    return {
        "extension_id": EXTENSION_ID,
        "contract_version": ASSET_CONTRACT_VERSION,
        "asset_root": asset_root,
        "run_id": run_id,
        "mode": mode,
        "output_policy": output_policy,
        "register_assets": output_policy in {"append", "new_run", "replace"} and bool(records),
        "requires_visible_output_policy": True,
        "hidden_registration_allowed": False,
        "records": records,
        "warnings": warnings,
        "errors": errors,
        "valid": not errors,
    }


def validate_asset_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(manifest or {})
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("extension_id") != EXTENSION_ID:
        errors.append("asset manifest extension_id mismatch")
    if data.get("contract_version") != ASSET_CONTRACT_VERSION:
        errors.append("asset manifest contract_version mismatch")
    if data.get("hidden_registration_allowed") is not False:
        errors.append("asset manifest must explicitly disallow hidden registration")
    records = data.get("records") or []
    if data.get("register_assets") and not records:
        errors.append("register_assets=true but no asset records were declared")
    for record in records:
        if not record.get("asset_id"):
            errors.append("asset record missing asset_id")
        if not record.get("relative_path"):
            errors.append(f"asset record {record.get('asset_id') or '<unknown>'} missing relative_path")
        if not record.get("visible", True):
            errors.append(f"asset record {record.get('asset_id') or '<unknown>'} is hidden")
        if not record.get("reusable_as"):
            warnings.append(f"asset record {record.get('asset_id') or '<unknown>'} has no reuse targets")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "EXTENSION_ID",
    "ASSET_CONTRACT_VERSION",
    "build_asset_manifest",
    "validate_asset_manifest",
]
