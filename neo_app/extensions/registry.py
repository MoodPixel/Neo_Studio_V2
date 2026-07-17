
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.extensions.schema import (
    VALID_EXTENSION_RUNTIME_PERMISSIONS,
    ExtensionCompatibilityRequest,
    ExtensionManifest,
    ExtensionMountTarget,
    ExtensionRecord,
)
from neo_app.extensions.runtime import (
    infer_workspace_apps,
    normalize_workflow_mode,
    normalize_workspace_app,
    normalize_extension_ui_contract,
    partition_workspace_extension_records,
    validate_extension_asset_paths,
    validate_mount_targets,
    validate_workspace_apps,
)
from neo_app.core.pydantic_compat import model_from_dict, model_from_json, model_to_dict

ROOT_DIR = Path(__file__).resolve().parents[2]
BUILT_IN_DIR = ROOT_DIR / "neo_extensions" / "built_in"
INSTALLED_DIR = ROOT_DIR / "neo_extensions" / "installed"
DISABLED_DIR = ROOT_DIR / "neo_extensions" / "disabled"
DATA_EXTENSIONS_DIR = ROOT_DIR / "neo_data" / "extensions" / "image"
EXTENSION_REGISTRY_PATH = ROOT_DIR / "neo_data" / "extensions" / "registry" / "image_extensions.json"
SUPPORTED_EXTENSION_SURFACES = {"image", "video", "text", "voice", "music"}
STATE_PATH = ROOT_DIR / "neo_data" / "user" / "extension_state.json"
MANIFEST_FILENAMES = ("extension_manifest.json", "neo_extension.json", "manifest.json")


def _normalize_surface_id(surface_id: str | None = None) -> str:
    surface = (surface_id or "image").strip().lower()
    if surface not in SUPPORTED_EXTENSION_SURFACES:
        raise ValueError(f"Unsupported extension surface: {surface}")
    return surface


def _data_extensions_dir(surface_id: str | None = None) -> Path:
    surface = _normalize_surface_id(surface_id)
    if surface == "image":
        return DATA_EXTENSIONS_DIR
    return ROOT_DIR / "neo_data" / "extensions" / surface




def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)

def _extension_registry_path(surface_id: str | None = None) -> Path:
    surface = _normalize_surface_id(surface_id)
    if surface == "image":
        return EXTENSION_REGISTRY_PATH
    return ROOT_DIR / "neo_data" / "extensions" / "registry" / f"{surface}_extensions.json"

# These built-ins remain available to internal capability/readiness logic, but
# they are not user-facing extension cards. They were placeholder/contract-only
# items and should not appear in Image workspace panels or Admin extension controls.
UI_HIDDEN_BUILT_IN_EXTENSION_IDS = {
    "image.controlnet_depth_pack",
    "image.gguf_loader",
}

DEFAULT_BUILT_IN_ENABLED_EXTENSIONS = [
    "cfg_fix_dynamic_thresholding",
    "image.gguf_loader",
    "image.image_upscale",
    "image.background_removal",
    "image.high_res_lab",
    "lora_stack",
    "embeddings_ti",
    "style_stack",
    "wildcards",
    "image.layerdiffuse",
    "video.vram_profile_advisor",
    "video.size_timing_presets",
    "video.output_recorder",
    "video.finish_interpolation",
    "video.finish_upscale",
    "video.finish_repair",
    "video.depth_motion_control",
    "video.prompt_motion_schedule",
    "video.audio_video",
]

DEFAULT_STATE = {
    "enabled_extensions": DEFAULT_BUILT_IN_ENABLED_EXTENSIONS.copy(),
    "removed_extensions": [],
    "extension_runtime_approvals": {},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_extension_dir_name(value: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (value or "").strip()).strip("._-")
    return candidate or "image_extension"


def _ensure_install_registry(surface_id: str | None = None) -> dict[str, Any]:
    surface = _normalize_surface_id(surface_id)
    registry_path = _extension_registry_path(surface)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    if not registry_path.exists():
        data = {"schema_version": f"neo.{surface}.extensions.registry.v1", "surface": surface, "extensions": {}}
        registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {"schema_version": f"neo.{surface}.extensions.registry.v1", "surface": surface, "extensions": {}}
    data.setdefault("schema_version", f"neo.{surface}.extensions.registry.v1")
    data.setdefault("surface", surface)
    data.setdefault("extensions", {})
    return data


def _write_install_registry(data: dict[str, Any], surface_id: str | None = None) -> None:
    registry_path = _extension_registry_path(surface_id or data.get("surface") or "image")
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _extension_source_dirs() -> list[Path]:
    dirs = [BUILT_IN_DIR, INSTALLED_DIR] + [_data_extensions_dir(surface) for surface in sorted(SUPPORTED_EXTENSION_SURFACES)]
    return [item for item in dirs if item.exists()]


def _extension_origin_for_manifest_path(path: Path) -> str:
    try:
        path.resolve().relative_to(BUILT_IN_DIR.resolve())
        return "built_in"
    except Exception:
        return "external"


def _finalize_manifest_defaults(manifest: ExtensionManifest, origin: str | None = None) -> ExtensionManifest:
    if origin:
        # Folder origin is authoritative. User-installed/external packages may not self-declare as built-in.
        manifest.extension_origin = origin
    if not manifest.workflow_modes and manifest.subtabs:
        manifest.workflow_modes = [normalize_workflow_mode(item) or item for item in manifest.subtabs]
    if not manifest.workspace_apps:
        manifest.workspace_apps = infer_workspace_apps(manifest.workflow_modes, manifest.subtabs)
    if not manifest.mount_targets and manifest.mount_slots:
        targets = []
        for slot in manifest.mount_slots:
            parts = slot.split('.')
            workflow_mode = normalize_workflow_mode(parts[1]) if len(parts) > 2 else None
            targets.append({
                "surface": manifest.surface,
                "workflow_mode": workflow_mode,
                "workspace_app": infer_workspace_apps([workflow_mode] if workflow_mode else manifest.workflow_modes, [workflow_mode] if workflow_mode else manifest.subtabs)[0] if infer_workspace_apps([workflow_mode] if workflow_mode else manifest.workflow_modes, [workflow_mode] if workflow_mode else manifest.subtabs) else None,
                "slot": slot,
            })
        try:
            manifest.mount_targets = [ExtensionMountTarget(**target) for target in targets]
        except Exception:
            pass
    return manifest


def _find_manifest_path(root: Path) -> Path | None:
    for filename in MANIFEST_FILENAMES:
        direct = root / filename
        if direct.exists() and direct.is_file():
            return direct
    children = [child for child in root.iterdir() if child.is_dir()] if root.exists() else []
    if len(children) == 1:
        for filename in MANIFEST_FILENAMES:
            nested = children[0] / filename
            if nested.exists() and nested.is_file():
                return nested
    return None


def _read_manifest_from_path(path: Path) -> ExtensionManifest:
    manifest = model_from_json(ExtensionManifest, path.read_text(encoding="utf-8"))
    return _finalize_manifest_defaults(manifest, _extension_origin_for_manifest_path(path))


def _normalize_installed_extension_tree(source_root: Path, source_label: str, surface_id: str | None = None) -> tuple[Path, ExtensionManifest]:
    expected_surface = _normalize_surface_id(surface_id)
    manifest_path = _find_manifest_path(source_root)
    if manifest_path is None:
        raise ValueError("Extension manifest not found. Expected extension_manifest.json, neo_extension.json, or manifest.json.")
    manifest = _read_manifest_from_path(manifest_path)
    if manifest.surface != expected_surface:
        raise ValueError(f"Only {expected_surface} extensions can be installed here. Manifest surface is {manifest.surface!r}.")
    extension_id = _safe_extension_dir_name(manifest.id)
    data_dir = _data_extensions_dir(expected_surface)
    target = data_dir / extension_id
    if target.exists():
        raise ValueError(f"Extension already installed: {manifest.id}")
    data_dir.mkdir(parents=True, exist_ok=True)
    tree_root = manifest_path.parent
    shutil.copytree(tree_root, target)
    canonical_manifest = target / "extension_manifest.json"
    if canonical_manifest.name != manifest_path.name:
        canonical_manifest.write_text(json.dumps(model_to_dict(manifest), indent=2), encoding="utf-8")
    registry = _ensure_install_registry(expected_surface)
    registry["extensions"][manifest.id] = {
        "extension_id": manifest.id,
        "name": manifest.name,
        "source": source_label,
        "version": manifest.version,
        "enabled": True,
        "install_path": str(target.relative_to(ROOT_DIR)),
        "installed_at": _now_iso(),
    }
    _write_install_registry(registry, expected_surface)
    state = _ensure_state()
    enabled = set(state.get("enabled_extensions", []))
    enabled.add(manifest.id)
    state["enabled_extensions"] = sorted(enabled)
    if manifest.id in state.get("removed_extensions", []):
        state["removed_extensions"] = [item for item in state["removed_extensions"] if item != manifest.id]
    _write_state(state)
    return target, manifest


def _ensure_state() -> dict[str, Any]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps(DEFAULT_STATE, indent=2), encoding="utf-8")
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = DEFAULT_STATE.copy()
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    data.setdefault("enabled_extensions", [])
    data.setdefault("removed_extensions", [])
    data.setdefault("default_seed_migrations", [])
    data.setdefault("extension_runtime_approvals", {})
    migration_id = "v25_9_20_p6_1_background_removal"
    if migration_id not in data["default_seed_migrations"]:
        extension_id = "image.background_removal"
        if extension_id not in data["removed_extensions"] and extension_id not in data["enabled_extensions"]:
            data["enabled_extensions"].append(extension_id)
        data["default_seed_migrations"].append(migration_id)
        _write_state(data)
    return data


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def requested_extension_runtime_permissions(manifest: ExtensionManifest) -> list[str]:
    """Return explicit plus safely inferred executable capabilities.

    Inference keeps older external manifests reviewable without granting them
    trust. New manifests should declare the complete list under
    ``runtime.requested_permissions``.
    """
    requested = {str(item).strip() for item in (manifest.runtime.requested_permissions or []) if str(item).strip()}
    entrypoints = manifest.entrypoints or {}
    bundle = manifest.asset_bundle
    if entrypoints.get("ui") or bundle.js or bundle.html:
        requested.add("custom_ui")
    if entrypoints.get("runtime") or entrypoints.get("backend.runtime"):
        requested.add("backend_routes")
    if entrypoints.get("workflow_patch") or entrypoints.get("backend.workflow_patch"):
        requested.add("workflow_patch")
    return sorted(requested)


def extension_runtime_permission_status(
    manifest: ExtensionManifest,
    *,
    origin: str | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested = requested_extension_runtime_permissions(manifest)
    invalid = sorted(set(requested).difference(VALID_EXTENSION_RUNTIME_PERMISSIONS))
    effective_origin = origin or manifest.extension_origin or "external"
    if effective_origin == "built_in":
        approved = [item for item in requested if item in VALID_EXTENSION_RUNTIME_PERMISSIONS]
        return {
            "contract_version": manifest.runtime.contract_version,
            "requested": requested,
            "approved": approved,
            "missing": [],
            "invalid": invalid,
            "version": manifest.version,
            "approved_version": manifest.version,
            "version_matches": True,
            "ready": not invalid,
            "restart_required": False,
            "origin_policy": "repo_trusted_builtin",
        }

    current_state = state or _ensure_state()
    approvals = current_state.get("extension_runtime_approvals") or {}
    approval = approvals.get(manifest.id) if isinstance(approvals, dict) else None
    approval = approval if isinstance(approval, dict) else {}
    approved_version = str(approval.get("version") or "")
    version_matches = bool(approved_version and approved_version == str(manifest.version))
    approved_values = approval.get("permissions") if version_matches else []
    approved = sorted({str(item) for item in (approved_values or []) if str(item) in requested})
    missing = sorted(set(requested).difference(approved))
    restart_permissions = {"backend_routes", "workflow_patch", "result_write"}
    return {
        "contract_version": manifest.runtime.contract_version,
        "requested": requested,
        "approved": approved,
        "missing": missing,
        "invalid": invalid,
        "version": manifest.version,
        "approved_version": approved_version or None,
        "version_matches": version_matches,
        "ready": not missing and not invalid,
        "restart_required": bool(set(approved).intersection(restart_permissions)),
        "origin_policy": "external_version_bound_approval",
    }


def extension_runtime_permission_granted(extension_id: str, permission: str) -> bool:
    record = get_extension(extension_id)
    if record is None or not record.enabled:
        return False
    status = extension_runtime_permission_status(record.manifest, origin=record.origin)
    return permission in status.get("approved", []) and permission not in status.get("invalid", [])


def approve_extension_runtime_permissions(
    extension_id: str,
    permissions: list[str] | None = None,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    record = get_extension(extension_id)
    if record is None:
        return {"ok": False, "errors": [f"Unknown extension: {extension_id}"]}
    if record.origin == "built_in":
        return {"ok": False, "errors": ["Built-in extensions use repository trust and do not need runtime approval."]}
    if version and str(version) != str(record.manifest.version):
        return {"ok": False, "errors": ["Extension version changed. Refresh Admin and review the current permissions before approval."]}
    requested = requested_extension_runtime_permissions(record.manifest)
    selected = requested if permissions is None else sorted({str(item) for item in permissions})
    invalid = sorted(set(selected).difference(requested).union(set(selected).difference(VALID_EXTENSION_RUNTIME_PERMISSIONS)))
    if invalid:
        return {"ok": False, "errors": [f"Permissions were not requested by this extension: {', '.join(invalid)}"]}
    state = _ensure_state()
    approvals = state.setdefault("extension_runtime_approvals", {})
    approvals[extension_id] = {
        "version": record.manifest.version,
        "permissions": selected,
        "approved_at": _now_iso(),
    }
    _write_state(state)
    status = extension_runtime_permission_status(record.manifest, origin=record.origin, state=state)
    return {
        "ok": True,
        "extension_id": extension_id,
        "runtime_permissions": status,
        "restart_required": status.get("restart_required", False),
        "payload": get_extension_payload(),
    }


def revoke_extension_runtime_permissions(extension_id: str) -> dict[str, Any]:
    record = get_extension(extension_id)
    if record is None:
        return {"ok": False, "errors": [f"Unknown extension: {extension_id}"]}
    if record.origin == "built_in":
        return {"ok": False, "errors": ["Built-in repository trust cannot be revoked through external-extension approvals."]}
    state = _ensure_state()
    approvals = state.setdefault("extension_runtime_approvals", {})
    approvals.pop(extension_id, None)
    _write_state(state)
    return {
        "ok": True,
        "extension_id": extension_id,
        "runtime_permissions": extension_runtime_permission_status(record.manifest, origin=record.origin, state=state),
        "restart_required": True,
        "payload": get_extension_payload(),
    }


def _manifest_paths() -> list[Path]:
    paths: list[Path] = []
    for directory in _extension_source_dirs():
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = _find_manifest_path(child)
            if manifest_path is not None:
                paths.append(manifest_path)
    return sorted(paths)


def _load_manifests() -> dict[str, ExtensionManifest]:
    manifests: dict[str, ExtensionManifest] = {}
    manifest_origins: dict[str, str] = {}
    for path in _manifest_paths():
        try:
            manifest = _read_manifest_from_path(path)
            origin = manifest.extension_origin or _extension_origin_for_manifest_path(path)
            existing_origin = manifest_origins.get(manifest.id)
            if existing_origin == "built_in" and origin != "built_in":
                # Repo-shipped built-ins are authoritative for duplicate ids.
                # External/user-installed copies may be tracked separately by install registry,
                # but must not shadow the built-in runtime record.
                continue
            if existing_origin and existing_origin != "built_in" and origin == "built_in":
                manifests[manifest.id] = manifest
                manifest_origins[manifest.id] = origin
                continue
            manifests[manifest.id] = manifest
            manifest_origins[manifest.id] = origin
        except Exception:
            # Invalid manifests are skipped in phase 7; later phases can expose broken records.
            continue
    return manifests


def _manifest_path_for_id(extension_id: str) -> Path | None:
    """Return a manifest path without letting one invalid manifest break the registry.

    Earlier registry code re-read every discovered manifest inside a generator. If one
    installed extension had a schema error, /api/extensions could crash even though
    _load_manifests() had already skipped invalid manifests. This helper preserves
    the skip-invalid behavior for Admin/Image visibility.
    """
    for path in _manifest_paths():
        try:
            manifest = _read_manifest_from_path(path)
        except Exception:
            continue
        if manifest.id == extension_id:
            return path
    return None


def _record_for(manifest: ExtensionManifest, manifests: dict[str, ExtensionManifest], state: dict[str, Any]) -> ExtensionRecord:
    errors: list[str] = []
    warnings: list[str] = []
    origin = manifest.extension_origin or "external"
    errors.extend(validate_workspace_apps(manifest.workspace_apps, surface=manifest.surface))
    errors.extend(validate_mount_targets([item if hasattr(item, "dict") else item for item in manifest.mount_targets], surface=manifest.surface))
    errors.extend(validate_extension_asset_paths(model_to_dict(manifest)))
    invalid_runtime_permissions = sorted(set(requested_extension_runtime_permissions(manifest)).difference(VALID_EXTENSION_RUNTIME_PERMISSIONS))
    if invalid_runtime_permissions:
        errors.append(f"Unknown external runtime permission(s): {', '.join(invalid_runtime_permissions)}")
    removed = manifest.id in state.get("removed_extensions", [])
    enabled = manifest.id in state.get("enabled_extensions", []) and not removed
    missing_parents = [dep for dep in manifest.depends_on if dep not in manifests]
    disabled_parents = [dep for dep in manifest.depends_on if dep in manifests and dep not in state.get("enabled_extensions", [])]
    if missing_parents:
        errors.append(f"Missing dependency extension(s): {', '.join(missing_parents)}")
    if disabled_parents:
        warnings.append(f"Dependency extension(s) disabled: {', '.join(disabled_parents)}")

    status = "enabled" if enabled else "disabled"
    if removed:
        status = "removed"
    elif missing_parents:
        status = "parent_missing"
    elif enabled and disabled_parents:
        status = "missing_requirements"

    manifest_path = _manifest_path_for_id(manifest.id)
    return ExtensionRecord(
        manifest=manifest,
        status=status,
        enabled=enabled and not missing_parents and not disabled_parents and not errors,
        install_path=str(manifest_path.parent if manifest_path else INSTALLED_DIR / manifest.id),
        origin=origin,
        errors=errors,
        warnings=warnings,
    )


def get_extension_records(include_removed: bool = False) -> list[ExtensionRecord]:
    state = _ensure_state()
    manifests = _load_manifests()
    records = [_record_for(manifest, manifests, state) for manifest in manifests.values()]
    if not include_removed:
        records = [record for record in records if record.status != "removed"]
    return sorted(records, key=lambda record: (record.manifest.surface, record.manifest.id))


def _admin_extension_contract_fields(record: ExtensionRecord) -> dict[str, Any]:
    """Return Admin-facing extension state without mixing workflow apply state.

    Admin pages need registry/global enablement. Workspace panels separately decide
    whether an extension is applied to a specific generation payload. Keeping both
    fields explicit prevents the Admin UI from showing workflow Disabled chips for
    globally enabled extensions.
    """
    manifest = record.manifest
    mount_slots = list(manifest.mount_slots or [])
    mount_targets = [model_to_dict(target) for target in (manifest.mount_targets or [])]
    provider_graph_mutation = bool(manifest.ui_schema.get("provider_graph_mutation", False))
    workflow_entrypoint = manifest.entrypoints.get("workflow_patch") or manifest.entrypoints.get("backend.workflow_patch")
    if workflow_entrypoint or manifest.required_nodes:
        provider_graph_mutation = True
    if manifest.id in {"style_stack", "wildcards"}:
        provider_graph_mutation = False
    prompt_only = not provider_graph_mutation and not workflow_entrypoint
    runtime_permissions = extension_runtime_permission_status(manifest, origin=record.origin)
    return {
        "id": manifest.id,
        "name": manifest.name,
        "surface": manifest.surface,
        "origin": record.origin,
        "status": record.status,
        "registry_enabled": bool(record.enabled),
        "workflow_enabled": False,
        "enabled": bool(record.enabled),
        "prompt_only": prompt_only,
        "provider_graph_mutation": provider_graph_mutation,
        "mount_slots": mount_slots,
        "mount_targets": mount_targets,
        "supported_backends": list(manifest.supported_backends or []),
        "workspace_apps": list(manifest.workspace_apps or []),
        "workflow_modes": list(manifest.workflow_modes or []),
        "runtime_permissions": runtime_permissions,
        "warnings": list(record.warnings or []),
        "errors": list(record.errors or []),
    }


def _record_with_ui_contract(record: ExtensionRecord, detail_mode: str = "guided") -> dict[str, Any]:
    data = model_to_dict(record)
    data.update(_admin_extension_contract_fields(record))
    data["ui_contract"] = normalize_extension_ui_contract(model_to_dict(record.manifest), detail_mode)
    return data


def is_ui_hidden_extension_id(extension_id: str | None) -> bool:
    return str(extension_id or "").strip() in UI_HIDDEN_BUILT_IN_EXTENSION_IDS


def _ui_visible_records(records: list[ExtensionRecord]) -> list[ExtensionRecord]:
    return [record for record in records if not is_ui_hidden_extension_id(record.manifest.id)]


def get_extension_ui_contract_payload(extension_id: str | None = None, detail_mode: str = "guided") -> dict[str, Any]:
    records = get_extension_records(include_removed=True)
    contracts = []
    for record in records:
        if extension_id and record.manifest.id != extension_id:
            continue
        if is_ui_hidden_extension_id(record.manifest.id):
            continue
        contracts.append(normalize_extension_ui_contract(model_to_dict(record.manifest), detail_mode))
    return {
        "schema_version": "neo.extension.ui_contract.payload.v1",
        "detail_mode": detail_mode,
        "extension_id": extension_id,
        "contracts": contracts,
        "ui_hidden_extension_ids": sorted(UI_HIDDEN_BUILT_IN_EXTENSION_IDS),
        "ui_hidden_policy": "contract_only_built_ins_are_hidden_from_workspace_and_admin_ui",
    }


def get_extension_payload() -> dict[str, Any]:
    all_records = get_extension_records()
    records = _ui_visible_records(all_records)
    enabled_extensions = [record.manifest.id for record in records if record.enabled]
    disabled_extensions = [record.manifest.id for record in records if not record.enabled]
    surface_counts: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = surface_counts.setdefault(record.manifest.surface, {"total": 0, "registry_enabled": 0, "registry_disabled": 0})
        bucket["total"] += 1
        if record.enabled:
            bucket["registry_enabled"] += 1
        else:
            bucket["registry_disabled"] += 1
    return {
        "schema_version": "neo.admin.extensions.payload.v1",
        "extension_runtime_version": "0.3.0",
        "ui_contract_version": "neo.extension.ui_contract.v1",
        "admin_data_contract_version": "neo.admin.extensions.contract.v1",
        "built_in_dir": _display_path(BUILT_IN_DIR),
        "installed_dir": _display_path(INSTALLED_DIR),
        "data_extensions_dir": _display_path(ROOT_DIR / "neo_data" / "extensions"),
        "install_registry_path": _display_path(ROOT_DIR / "neo_data" / "extensions" / "registry"),
        "state_path": _display_path(STATE_PATH),
        "extensions": [_record_with_ui_contract(record) for record in records],
        "built_in_extensions": [_record_with_ui_contract(record) for record in records if record.origin == "built_in"],
        "external_extensions": [_record_with_ui_contract(record) for record in records if record.origin == "external"],
        "enabled_extensions": enabled_extensions,
        "disabled_extensions": disabled_extensions,
        "surface_counts": surface_counts,
        "ui_hidden_extension_ids": sorted(UI_HIDDEN_BUILT_IN_EXTENSION_IDS),
        "ui_hidden_policy": "contract_only_built_ins_are_hidden_from_workspace_and_admin_ui",
        "state_fields": {
            "registry_enabled_field": "registry_enabled",
            "workflow_enabled_field": "workflow_enabled",
            "enabled_alias": "enabled",
            "admin_status_source": "neo_data/user/extension_state.json",
        },
    }


def get_extension(extension_id: str) -> ExtensionRecord | None:
    for record in get_extension_records(include_removed=True):
        if record.manifest.id == extension_id:
            return record
    return None


def get_surface_extension_payload(
    surface_id: str,
    subtab_id: str | None = None,
    workspace_app: str | None = None,
    workflow_mode: str | None = None,
) -> dict[str, Any]:
    records = []
    mode = normalize_workflow_mode(workflow_mode or subtab_id)
    app = normalize_workspace_app(workspace_app)
    for record in get_extension_records():
        manifest = record.manifest
        if manifest.surface != surface_id:
            continue
        if subtab_id and manifest.subtabs and subtab_id not in manifest.subtabs and mode not in manifest.workflow_modes:
            continue
        if app and manifest.workspace_apps and app not in [normalize_workspace_app(item) for item in manifest.workspace_apps]:
            continue
        records.append(_record_with_ui_contract(record))
    records = [record for record in records if not is_ui_hidden_extension_id(record.get("id") or record.get("manifest", {}).get("id"))]
    partition = partition_workspace_extension_records(records, surface=surface_id, workspace_app=app, workflow_mode=mode)
    enabled_extensions = [record.get("id") or record.get("manifest", {}).get("id") for record in records if record.get("registry_enabled") is True]
    disabled_extensions = [record.get("id") or record.get("manifest", {}).get("id") for record in records if record.get("registry_enabled") is not True]
    return {
        "schema_version": "neo.admin.surface.extensions.payload.v1",
        "admin_data_contract_version": "neo.admin.extensions.contract.v1",
        "surface": surface_id,
        "subtab": subtab_id,
        "workspace_app": app,
        "workflow_mode": mode,
        "extensions": records,
        "built_in_direct": partition["built_in_direct"],
        "external_section": partition["external_section"],
        "enabled_extensions": enabled_extensions,
        "disabled_extensions": disabled_extensions,
        "ui_hidden_extension_ids": sorted(UI_HIDDEN_BUILT_IN_EXTENSION_IDS),
        "ui_hidden_policy": "contract_only_built_ins_are_hidden_from_workspace_and_admin_ui",
        "state_fields": {
            "registry_enabled_field": "registry_enabled",
            "workflow_enabled_field": "workflow_enabled",
            "enabled_alias": "enabled",
        },
    }


def set_extension_enabled(extension_id: str, enabled: bool) -> dict[str, Any]:
    state = _ensure_state()
    manifests = _load_manifests()
    if extension_id not in manifests:
        return {"ok": False, "errors": [f"Unknown extension: {extension_id}"]}
    if extension_id in state["removed_extensions"]:
        state["removed_extensions"].remove(extension_id)
    enabled_set = set(state["enabled_extensions"])
    if enabled:
        enabled_set.add(extension_id)
    else:
        enabled_set.discard(extension_id)
        # Disable children too so dependency chains do not stay half-lit.
        for manifest in manifests.values():
            if extension_id in manifest.depends_on:
                enabled_set.discard(manifest.id)
    state["enabled_extensions"] = sorted(enabled_set)
    _write_state(state)
    return {"ok": True, "extension_id": extension_id, "enabled": enabled, "payload": get_extension_payload()}


def remove_extension(extension_id: str) -> dict[str, Any]:
    state = _ensure_state()
    manifests = _load_manifests()
    if extension_id not in manifests:
        return {"ok": False, "errors": [f"Unknown extension: {extension_id}"]}
    state["enabled_extensions"] = [item for item in state["enabled_extensions"] if item != extension_id]
    if extension_id not in state["removed_extensions"]:
        state["removed_extensions"].append(extension_id)
    approvals = state.setdefault("extension_runtime_approvals", {})
    if isinstance(approvals, dict):
        approvals.pop(extension_id, None)
    _write_state(state)
    return {"ok": True, "extension_id": extension_id, "status": "removed", "payload": get_extension_payload()}



def install_extension_from_github(repo_url: str, branch: str | None = None, surface_id: str | None = "image") -> dict[str, Any]:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return {"ok": False, "errors": ["GitHub repository URL is required."]}
    with tempfile.TemporaryDirectory(prefix="neo_ext_git_") as tmp:
        clone_dir = Path(tmp) / "repo"
        command = ["git", "clone", "--depth", "1"]
        if branch:
            command.extend(["--branch", branch])
        command.extend([repo_url, str(clone_dir)])
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        except FileNotFoundError:
            return {"ok": False, "errors": ["Git is not installed or not available on PATH."]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "errors": ["Git clone timed out."]}
        if completed.returncode != 0:
            return {"ok": False, "errors": [completed.stderr.strip() or completed.stdout.strip() or "Git clone failed."]}
        try:
            target, manifest = _normalize_installed_extension_tree(clone_dir, "github", surface_id)
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)]}
        registry = _ensure_install_registry(manifest.surface)
        registry["extensions"].setdefault(manifest.id, {})["repo_url"] = repo_url
        if branch:
            registry["extensions"][manifest.id]["branch"] = branch
        _write_install_registry(registry, manifest.surface)
        return {"ok": True, "extension_id": manifest.id, "surface": manifest.surface, "install_path": str(target.relative_to(ROOT_DIR)), "payload": get_extension_payload()}


def install_extension_from_zip(zip_path: Path, surface_id: str | None = "image") -> dict[str, Any]:
    if not zip_path.exists():
        return {"ok": False, "errors": ["ZIP file does not exist."]}
    with tempfile.TemporaryDirectory(prefix="neo_ext_zip_") as tmp:
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for member in archive.infolist():
                    if member.filename.startswith(("/", "\\")) or ".." in Path(member.filename).parts:
                        return {"ok": False, "errors": ["ZIP contains unsafe paths."]}
                archive.extractall(extract_dir)
        except zipfile.BadZipFile:
            return {"ok": False, "errors": ["Invalid ZIP file."]}
        try:
            target, manifest = _normalize_installed_extension_tree(extract_dir, "zip", surface_id)
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "extension_id": manifest.id, "surface": manifest.surface, "install_path": str(target.relative_to(ROOT_DIR)), "payload": get_extension_payload()}


def update_extension(extension_id: str) -> dict[str, Any]:
    manifest_record = get_extension(extension_id)
    surface = manifest_record.manifest.surface if manifest_record else "image"
    registry = _ensure_install_registry(surface)
    item = registry.get("extensions", {}).get(extension_id)
    if not item:
        return {"ok": False, "errors": [f"Extension is not tracked by the surface install registry: {extension_id}"]}
    if item.get("source") != "github" or not item.get("repo_url"):
        return {"ok": False, "errors": ["Only GitHub-installed extensions can be updated automatically."]}
    existing_path = ROOT_DIR / item.get("install_path", "")
    backup_path = existing_path.with_name(existing_path.name + ".backup")
    if not existing_path.exists():
        return {"ok": False, "errors": ["Installed extension folder is missing."]}
    if backup_path.exists():
        shutil.rmtree(backup_path)
    existing_path.rename(backup_path)
    try:
        result = install_extension_from_github(item["repo_url"], item.get("branch"), surface)
        if not result.get("ok"):
            if existing_path.exists():
                shutil.rmtree(existing_path)
            backup_path.rename(existing_path)
            return result
        shutil.rmtree(backup_path, ignore_errors=True)
        return result
    except Exception as exc:
        if existing_path.exists():
            shutil.rmtree(existing_path, ignore_errors=True)
        if backup_path.exists():
            backup_path.rename(existing_path)
        return {"ok": False, "errors": [str(exc)]}



def _route_state_mode_aliases(mode: str | None) -> list[str]:
    value = (mode or "").strip()
    if not value:
        return ["*"]
    aliases = [value]
    if value == "generate":
        aliases.append("txt2img")
    elif value == "txt2img":
        aliases.append("generate")
    return list(dict.fromkeys(aliases))


def _route_state_backend_aliases(backend: str | None) -> list[str]:
    value = (backend or "").strip()
    aliases = [value] if value else []
    if value in {"comfy", "comfyui", "comfyui_portable"}:
        aliases.extend(["comfyui", "comfyui_portable"])
    return list(dict.fromkeys([item for item in aliases if item]))


def resolve_extension_manifest_route_state(
    route_states: dict[str, str] | None,
    *,
    backend: str | None = None,
    family: str | None = None,
    loader: str | None = None,
    workflow_mode: str | None = None,
    workspace_app: str | None = None,
) -> str | None:
    """Resolve an extension manifest route-state declaration with V25.9.20 Pass J keys.

    Manifests in Neo use a mix of old short keys (family:loader:mode),
    backend-prefixed keys (backend:family:loader:mode), and workspace-level
    finish keys (backend:*:*:*:finish).  Pass J keeps backward compatibility
    while making compatibility checks honor the backend-prefixed matrix.
    """
    states = route_states or {}
    if not states:
        return None
    backends = _route_state_backend_aliases(backend) or ["*"]
    families = [str(family or "*").strip() or "*", "*"]
    loaders = [str(loader or "*").strip() or "*", "*"]
    modes = _route_state_mode_aliases(workflow_mode) + ["*"]
    apps = [normalize_workspace_app(workspace_app), "*"]
    candidates: list[str] = []
    for b in backends:
        for f in families:
            for l in loaders:
                for m in modes:
                    candidates.append(f"{b}:{f}:{l}:{m}")
                    for app in apps:
                        if app and app != "*":
                            candidates.append(f"{b}:{f}:{l}:{m}:{app}")
    for f in families:
        for l in loaders:
            for m in modes:
                candidates.append(f"{f}:{l}:{m}")
    for m in modes:
        candidates.append(m)
    for app in apps:
        if app and app != "*":
            candidates.append(app)
    candidates.append("*")
    for key in dict.fromkeys(candidates):
        if key in states:
            return states[key]
    return None

def check_extension_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    request = model_from_dict(ExtensionCompatibilityRequest, payload)
    records = get_extension_records()
    selected = request.extension_ids or [record.manifest.id for record in records if record.enabled]
    results = []
    ok = True
    for extension_id in selected:
        record = next((item for item in records if item.manifest.id == extension_id), None)
        if not record:
            ok = False
            results.append({"extension_id": extension_id, "ok": False, "errors": ["Extension is not installed or is removed."]})
            continue
        manifest = record.manifest
        errors = list(record.errors)
        warnings = list(record.warnings)
        if manifest.surface != request.surface:
            errors.append(f"Extension targets surface {manifest.surface}, not {request.surface}.")
        request_mode = normalize_workflow_mode(request.workflow_mode or request.subtab)
        request_workspace = request.workspace_app
        if request.subtab and manifest.subtabs and request.subtab not in manifest.subtabs and request_mode not in manifest.workflow_modes:
            errors.append(f"Extension does not mount into subtab {request.subtab}.")
        if request_mode and manifest.workflow_modes and request_mode not in manifest.workflow_modes:
            errors.append(f"Extension does not support workflow mode {request_mode}.")
        if request_workspace and manifest.workspace_apps and request_workspace not in manifest.workspace_apps:
            errors.append(f"Extension does not mount into workspace app {request_workspace}.")
        declared_state = None
        if request.route_state:
            declared_state = resolve_extension_manifest_route_state(
                manifest.route_states,
                backend=request.provider_id,
                family=request.family,
                loader=request.loader,
                workflow_mode=request_mode,
                workspace_app=request.workspace_app,
            )
            if declared_state and declared_state != request.route_state:
                warnings.append(f"Extension route state declaration is {declared_state}, active route is {request.route_state}.")
        if request.provider_id and manifest.supported_backends and request.provider_id not in manifest.supported_backends:
            errors.append(f"Backend {request.provider_id} is not supported.")
        if request.family and manifest.supported_families and request.family not in manifest.supported_families:
            errors.append(f"Family {request.family} is not supported.")
        if request.loader and manifest.supported_loaders and request.loader not in manifest.supported_loaders:
            errors.append(f"Loader {request.loader} is not supported.")

        matched_profiles = []
        for profile_id, profile in manifest.capability_profiles.items():
            profile_families = list(getattr(profile, "families", []) or [])
            if getattr(profile, "family", None):
                profile_families.append(profile.family)
            profile_loaders = list(getattr(profile, "loaders", []) or [])
            if getattr(profile, "loader", None):
                profile_loaders.append(profile.loader)
            if request.family and profile_families and request.family not in profile_families:
                continue
            if request.loader and profile_loaders and request.loader not in profile_loaders:
                continue
            profile_mode = normalize_workflow_mode(request.workflow_mode or request.subtab)
            route_mode_key = f"{request.family or '*'}:{request.loader or '*'}:{profile_mode}" if profile_mode else None
            if profile_mode and profile.modes and profile_mode not in profile.modes and route_mode_key not in profile.modes:
                continue
            matched_profiles.append(profile_id)

        if (request.family or request.loader) and manifest.capability_profiles and not matched_profiles:
            warnings.append("No capability profile matched the requested family/loader/subtab combination.")

        extension_ok = record.enabled and not errors
        if not extension_ok:
            ok = False
        results.append({
            "extension_id": extension_id,
            "name": manifest.name,
            "ok": extension_ok,
            "enabled": record.enabled,
            "origin": record.origin,
            "extension_type": manifest.extension_type,
            "workspace_apps": manifest.workspace_apps,
            "workflow_modes": manifest.workflow_modes,
            "mount_slots": manifest.mount_slots,
            "mount_targets": [model_to_dict(target) if hasattr(target, "dict") or hasattr(target, "model_dump") else target for target in manifest.mount_targets],
            "capability_profiles": matched_profiles,
            "route_state_declaration": declared_state,
            "errors": errors,
            "warnings": warnings,
        })
    return {"ok": ok, "results": results}
