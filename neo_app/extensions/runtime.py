from __future__ import annotations

from copy import deepcopy
from typing import Any

from neo_app.extensions.schema import VALID_IMAGE_WORKSPACE_APPS, VALID_ROUTE_STATES

WORKFLOW_MODE_ALIASES = {
    "txt2img": "generate",
    "generate": "generate",
    "img2img": "img2img",
    "inpaint": "inpaint",
    "outpaint": "outpaint",
    "edit": "edit",
    "upscale": "upscale",
    "batch": "batch",
    "history": "history",
}

WORKFLOW_TO_DEFAULT_WORKSPACE_APP = {
    "generate": "generations",
    "txt2img": "generations",
    "img2img": "reference",
    "inpaint": "reference",
    "outpaint": "reference",
    "edit": "reference",
    "upscale": "finish",
    "batch": "assets",
    "history": "results",
}

DETAIL_MODES = {"compact", "guided", "expert"}


WORKSPACE_APP_ALIASES = {
    "generation": "generations",
    "generations": "generations",
    "asset": "assets",
    "assets": "assets",
    "reference": "reference",
    "finish": "finish",
    "results": "results",
}


def normalize_workspace_app(app: str | None) -> str | None:
    if not app:
        return None
    value = app.strip().lower()
    return WORKSPACE_APP_ALIASES.get(value, value)


def extension_ui_mount_kind(origin: str | None) -> str:
    """Return where the extension should appear in a workspace UI.

    Built-ins are first-class tools and render directly in their target workspace.
    External/user-installed extensions render inside the workspace Extension section.
    """
    return "direct_workspace" if (origin or "external") == "built_in" else "extension_section"


def extension_matches_workspace(
    manifest: dict[str, Any],
    *,
    surface: str = "image",
    workspace_app: str | None = None,
    workflow_mode: str | None = None,
    route_state: str | None = None,
) -> bool:
    if manifest.get("surface") != surface:
        return False
    canonical_app = normalize_workspace_app(workspace_app)
    mode = normalize_workflow_mode(workflow_mode)
    workspace_apps = [normalize_workspace_app(item) for item in (manifest.get("workspace_apps") or [])]
    workflow_modes = [normalize_workflow_mode(item) for item in (manifest.get("workflow_modes") or manifest.get("subtabs") or [])]
    if canonical_app and workspace_apps and canonical_app not in workspace_apps:
        return False
    if mode and workflow_modes and mode not in workflow_modes:
        return False
    targets = manifest.get("mount_targets") or []
    if targets:
        matched_target = False
        for target in targets:
            target_app = normalize_workspace_app(target.get("workspace_app"))
            target_mode = normalize_workflow_mode(target.get("workflow_mode"))
            target_states = target.get("route_states") or []
            if canonical_app and target_app and target_app != canonical_app:
                continue
            if mode and target_mode and target_mode != mode:
                continue
            if route_state and target_states and route_state not in target_states:
                continue
            matched_target = True
            break
        if not matched_target:
            return False
    return True


def partition_workspace_extension_records(
    records: list[dict[str, Any]],
    *,
    surface: str = "image",
    workspace_app: str | None = None,
    workflow_mode: str | None = None,
    route_state: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    direct: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for record in records:
        manifest = record.get("manifest") or {}
        if not record.get("enabled"):
            continue
        if not extension_matches_workspace(
            manifest,
            surface=surface,
            workspace_app=workspace_app,
            workflow_mode=workflow_mode,
            route_state=route_state,
        ):
            continue
        origin = record.get("origin") or manifest.get("extension_origin") or "external"
        if extension_ui_mount_kind(origin) == "direct_workspace":
            direct.append(record)
        else:
            external.append(record)
    return {"built_in_direct": direct, "external_section": external}


def validate_extension_asset_paths(manifest: dict[str, Any]) -> list[str]:
    """Validate declared extension-owned UI/runtime files are local relative paths.

    This keeps future built-in and external extension JS/CSS/HTML/Python inside the
    extension folder instead of teaching extensions to patch core files.
    """
    errors: list[str] = []
    bundle = manifest.get("asset_bundle") or {}
    entrypoints = manifest.get("entrypoints") or {}
    values: list[tuple[str, str]] = []
    for group, paths in bundle.items():
        if isinstance(paths, list):
            values.extend((f"asset_bundle.{group}", str(item)) for item in paths)
    for key, value in entrypoints.items():
        values.append((f"entrypoints.{key}", str(value)))
    for key, raw in values:
        path = raw.replace("\\", "/").strip()
        if not path:
            continue
        if path.startswith(('/', 'http://', 'https://')) or '..' in [part for part in path.split('/') if part]:
            errors.append(f"{key} must be a local extension-relative path: {raw}")
        if path.startswith(('neo_app/', 'neo_system_records/', 'scripts/', 'tests/', 'neo_data/')):
            errors.append(f"{key} must not point into core or data folders: {raw}")
    return errors


def normalize_detail_mode(mode: str | None) -> str:
    value = (mode or "guided").strip().lower()
    return value if value in DETAIL_MODES else "guided"


def normalize_workflow_mode(mode: str | None) -> str | None:
    if not mode:
        return None
    value = mode.strip().lower()
    return WORKFLOW_MODE_ALIASES.get(value, value)


def infer_workspace_apps(workflow_modes: list[str] | None = None, subtabs: list[str] | None = None) -> list[str]:
    modes = workflow_modes or subtabs or []
    apps: list[str] = []
    for mode in modes:
        app = WORKFLOW_TO_DEFAULT_WORKSPACE_APP.get(normalize_workflow_mode(mode) or mode)
        if app and app not in apps:
            apps.append(app)
    return apps


def validate_workspace_apps(workspace_apps: list[str], *, surface: str = "image") -> list[str]:
    if surface != "image":
        return []
    errors = []
    for app in workspace_apps:
        canonical = normalize_workspace_app(app)
        if canonical not in VALID_IMAGE_WORKSPACE_APPS:
            errors.append(f"Unknown Image workspace app: {app}.")
    return errors


def validate_mount_targets(mount_targets: list[Any], *, surface: str = "image") -> list[str]:
    errors: list[str] = []
    for target in mount_targets:
        data = target if isinstance(target, dict) else getattr(target, "__dict__", {})
        target_surface = data.get("surface") or surface
        if target_surface != surface:
            errors.append(f"Mount target surface {target_surface} does not match extension surface {surface}.")
        workspace_app = data.get("workspace_app")
        if workspace_app:
            errors.extend(validate_workspace_apps([workspace_app], surface=surface))
        for state in data.get("route_states") or []:
            if state not in VALID_ROUTE_STATES:
                errors.append(f"Unknown route state in mount target: {state}.")
        slot = data.get("slot")
        if not slot:
            errors.append("Mount target is missing slot.")
    return errors


def render_extension_field(field: dict[str, Any], detail_mode: str = "guided") -> dict[str, Any]:
    """Return a display-safe field descriptor for extension UI renderers.

    This does not render DOM. It centralizes Compact/Guided/Expert information policy so
    built-in and external extensions obey the same display rules.
    """
    mode = normalize_detail_mode(detail_mode)
    visible = {
        "id": field.get("id"),
        "label": field.get("compact_label") if mode == "compact" and field.get("compact_label") else field.get("label"),
        "type": field.get("type", "text"),
        "visible_when": deepcopy(field.get("visible_when", {})),
    }
    if mode == "guided":
        help_text = field.get("guided_help") or field.get("help_text")
        if help_text:
            visible["help_text"] = help_text
    elif mode == "expert":
        help_text = field.get("guided_help") or field.get("help_text")
        if help_text:
            visible["help_text"] = help_text
        if field.get("expert_text"):
            visible["expert_text"] = field["expert_text"]
        if field.get("payload_path"):
            visible["payload_path"] = field["payload_path"]
    return visible



EXTENSION_UI_CONTRACT_VERSION = "neo.extension.ui_contract.v1"
ALLOWED_EXTENSION_UI_COMPONENTS = {
    "action_bar",
    "asset_picker",
    "diagnostic_panel",
    "empty_state",
    "field_grid",
    "library_browser",
    "result_preview",
    "source_picker",
    "status_bar",
}
ALLOWED_EXTENSION_PANEL_TYPES = {
    "advanced_panel",
    "finishing_tool",
    "library_tool",
    "reference_tool",
    "workflow_tool",
}


def _clean_text(value: Any, fallback: str = "") -> str:
    return str(value if value is not None else fallback).strip()


def _safe_identifier(value: Any, fallback: str = "extension_panel") -> str:
    raw = _clean_text(value, fallback).lower().replace(" ", "_").replace(".", "_").replace("-", "_")
    safe = "".join(char for char in raw if char.isalnum() or char == "_").strip("_")
    return safe or fallback


def infer_extension_panel_type(manifest: dict[str, Any]) -> str:
    ui_schema = manifest.get("ui_schema") if isinstance(manifest.get("ui_schema"), dict) else {}
    declared = _clean_text(ui_schema.get("panel_type") or ui_schema.get("type")).lower()
    if declared in ALLOWED_EXTENSION_PANEL_TYPES:
        return declared
    workspace_apps = {normalize_workspace_app(item) for item in (manifest.get("workspace_apps") or [])}
    if "finish" in workspace_apps:
        return "finishing_tool"
    if "reference" in workspace_apps:
        return "reference_tool"
    if "assets" in workspace_apps:
        return "library_tool"
    return "workflow_tool"


def _normalize_ui_field(field: Any, detail_mode: str = "guided") -> dict[str, Any] | None:
    if isinstance(field, str):
        data = {"id": _safe_identifier(field, "field"), "label": field, "type": "text"}
    elif isinstance(field, dict):
        data = dict(field)
    else:
        return None
    if not _clean_text(data.get("id")):
        data["id"] = _safe_identifier(data.get("label") or data.get("name") or "field", "field")
    if not _clean_text(data.get("label")):
        data["label"] = data["id"].replace("_", " ").title()
    return render_extension_field(data, detail_mode)


def _normalize_ui_fields(fields: Any, detail_mode: str = "guided") -> list[dict[str, Any]]:
    if isinstance(fields, dict):
        # Accept legacy grouped field dictionaries without forcing manifest rewrites.
        candidate = fields.get("items") or fields.get("fields") or []
    else:
        candidate = fields
    if not isinstance(candidate, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in candidate:
        field = _normalize_ui_field(item, detail_mode)
        if field:
            normalized.append(field)
    return normalized


def _normalize_ui_panels(ui_schema: dict[str, Any], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_panels = ui_schema.get("panels") if isinstance(ui_schema, dict) else []
    panels: list[dict[str, Any]] = []
    if isinstance(raw_panels, list):
        for index, panel in enumerate(raw_panels):
            if isinstance(panel, str):
                panels.append({
                    "id": _safe_identifier(panel, f"panel_{index + 1}"),
                    "title": panel,
                    "component": "field_grid",
                    "fields": [],
                })
                continue
            if not isinstance(panel, dict):
                continue
            component = _clean_text(panel.get("component") or panel.get("component_type") or "field_grid")
            if component not in ALLOWED_EXTENSION_UI_COMPONENTS:
                component = "field_grid"
            panel_fields = _normalize_ui_fields(panel.get("fields"), "guided") if panel.get("fields") else []
            title = _clean_text(panel.get("title") or panel.get("label"), f"Panel {index + 1}")
            panels.append({
                "id": _safe_identifier(panel.get("id") or title, f"panel_{index + 1}"),
                "title": title,
                "description": _clean_text(panel.get("description") or panel.get("help_text")),
                "component": component,
                "fields": panel_fields,
            })
    if not panels and fields:
        panels.append({
            "id": "settings",
            "title": "Settings",
            "description": "Extension controls rendered through Neo shared UI components.",
            "component": "field_grid",
            "fields": fields,
        })
    return panels


def normalize_extension_ui_contract(manifest: dict[str, Any], detail_mode: str = "guided") -> dict[str, Any]:
    """Build the shared UI contract consumed by Admin and future surface renderers.

    Legacy manifests can keep their existing ui_schema keys. This function adapts those
    declarations into a stable contract so extensions render through NeoUI components
    instead of hand-built one-off HTML islands.
    """
    mode = normalize_detail_mode(detail_mode)
    ui_schema = manifest.get("ui_schema") if isinstance(manifest.get("ui_schema"), dict) else {}
    fields = _normalize_ui_fields(ui_schema.get("fields"), mode)
    panels = _normalize_ui_panels(ui_schema, fields)
    mount_targets = manifest.get("mount_targets") or []
    mount_slots = manifest.get("mount_slots") or []
    validation = validate_extension_ui_contract(manifest)
    return {
        "schema_version": EXTENSION_UI_CONTRACT_VERSION,
        "extension_id": manifest.get("id"),
        "surface": manifest.get("surface"),
        "name": manifest.get("name") or manifest.get("id"),
        "panel_type": infer_extension_panel_type(manifest),
        "detail_mode": mode,
        "mount": {
            "workspace_apps": [normalize_workspace_app(item) or item for item in (manifest.get("workspace_apps") or [])],
            "workflow_modes": [normalize_workflow_mode(item) or item for item in (manifest.get("workflow_modes") or manifest.get("subtabs") or [])],
            "slots": mount_slots,
            "targets": mount_targets,
        },
        "components": sorted(ALLOWED_EXTENSION_UI_COMPONENTS),
        "panels": panels,
        "actions": ui_schema.get("actions") if isinstance(ui_schema.get("actions"), list) else [],
        "visibility_policy": {
            "compact": "primary controls only",
            "guided": "creator-facing controls with help text",
            "expert": "diagnostics and payload paths may be shown",
        },
        "validation": validation,
    }


def validate_extension_ui_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    ui_schema = manifest.get("ui_schema") if isinstance(manifest.get("ui_schema"), dict) else {}
    if not _clean_text(manifest.get("id")):
        errors.append("Extension manifest is missing id.")
    if not _clean_text(manifest.get("surface")):
        errors.append("Extension manifest is missing surface.")
    if not (manifest.get("mount_targets") or manifest.get("mount_slots") or manifest.get("workspace_apps")):
        warnings.append("No mount target, mount slot, or workspace app declared.")
    panel_type = infer_extension_panel_type(manifest)
    if panel_type not in ALLOWED_EXTENSION_PANEL_TYPES:
        errors.append(f"Unsupported extension panel type: {panel_type}.")
    fields = ui_schema.get("fields")
    if fields is None and not ui_schema.get("panels"):
        warnings.append("No shared UI fields or panels declared; renderer will show a standard empty state.")
    for field in _normalize_ui_fields(fields or [], "expert"):
        if not _clean_text(field.get("id")):
            errors.append("UI field is missing id.")
        if not _clean_text(field.get("label")):
            errors.append(f"UI field {field.get('id') or '<unknown>'} is missing label.")
    legacy_markers = [key for key in ui_schema if str(key).lower() in {"phase", "skeleton_only", "v1_parity", "migration_note"}]
    if legacy_markers:
        warnings.append("Legacy implementation markers remain in ui_schema and should stay hidden from normal user UI.")
    return {"ok": not errors, "errors": errors, "warnings": warnings}

def build_extension_payload_block(
    extension_id: str,
    *,
    enabled: bool = True,
    version: int | str = 1,
    inputs: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "extensions": {
            extension_id: {
                "enabled": bool(enabled),
                "version": version,
                "inputs": inputs or {},
                "params": params or {},
                "assets": assets or {},
                "metadata": metadata or {},
            }
        }
    }


def validate_extension_payload_block(block: dict[str, Any], *, extension_id: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    extensions = block.get("extensions")
    if not isinstance(extensions, dict):
        errors.append("Extension payload must contain an extensions object.")
        return {"ok": False, "errors": errors}
    targets = [extension_id] if extension_id else list(extensions)
    for ext_id in targets:
        item = extensions.get(ext_id)
        if not isinstance(item, dict):
            errors.append(f"Extension payload missing block for {ext_id}.")
            continue
        for key in ("enabled", "version", "inputs", "params", "assets", "metadata"):
            if key not in item:
                errors.append(f"Extension payload {ext_id} missing key: {key}.")
        if item.get("enabled") is False and any(item.get(key) for key in ("inputs", "params", "assets")):
            errors.append(f"Disabled extension payload {ext_id} must not carry active inputs, params, or assets.")
    return {"ok": not errors, "errors": errors}


def extension_memory_event_contract(
    extension_id: str,
    route: dict[str, Any] | None = None,
    *,
    assets: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    outputs: list[dict[str, Any]] | None = None,
    summary: str = "",
    assistant_summary: str = "",
) -> dict[str, Any]:
    """Return the stable Assistant-ready memory event shape for extension workflows."""
    clean_route = route or {}
    text = assistant_summary or summary
    return {
        "event_type": "extension_workflow_used",
        "extension_id": extension_id,
        "namespace": f"extension:{extension_id}",
        "workspace_app": clean_route.get("workspace_app"),
        "route": clean_route,
        "assets": assets or [],
        "params": params or {},
        "outputs": outputs or [],
        "summary": summary or text,
        "assistant_summary": text,
    }
