from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from neo_app.comfy_spaces.dependency_health import load_space_runtime_health
from neo_app.comfy_spaces.execution_runtime import (
    SpaceExecutionRuntimeError,
    _load_published_space_package,
    list_space_runs,
)
from neo_app.comfy_spaces.graph_addons import build_graph_addon_catalog
from neo_app.comfy_spaces.paths import comfy_spaces_library_root, comfy_space_version_root
from neo_app.comfy_spaces.runtime_probe import SpaceRuntimeProbeError, probe_comfy_profile_object_info
from neo_app.comfy_spaces.versioning import list_space_versions, resolve_active_space_version
from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.profiles import get_backend_profile_payload, list_backend_profiles


SPACE_SURFACE_SCHEMA_VERSION = "neo.comfy_space.surface.v1"
SPACE_SURFACE_SERVICE_VERSION = "neo.comfy_space.surface_service.v2"
_COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}
_PRESENTATION_KINDS = {"icon", "thumbnail"}


class ComfySpaceSurfaceError(ValueError):
    """Raised when the public Comfy Spaces surface cannot load a safe published package."""


def _version_key(value: str) -> tuple[Any, ...]:
    """Sort semantic-ish versions without adding a packaging dependency.

    Published Space versions already pass the strict manifest semver validator, so
    this key only needs stable ordering for the public library. Pre-releases sort
    before the corresponding final release.
    """

    text = str(value or "0.0.0").strip()
    core, _, prerelease = text.partition("-")
    core = core.split("+", 1)[0]
    numbers: list[int] = []
    for part in core.split(".")[:3]:
        try:
            numbers.append(int(part))
        except (TypeError, ValueError):
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    # final > prerelease for the same numeric core
    return (*numbers, 1 if not prerelease else 0, prerelease)


def _runtime_profiles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in list_backend_profiles():
        provider_id = str(profile.get("provider_id") or "").strip().lower()
        if provider_id not in _COMFY_PROVIDER_IDS or profile.get("enabled") is False:
            continue
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), Mapping) else {}
        rows.append({
            "profile_id": str(profile.get("profile_id") or ""),
            "display_name": str(profile.get("display_name") or profile.get("profile_id") or "ComfyUI"),
            "provider_id": provider_id,
            "status": str(runtime.get("status") or profile.get("status") or "not_checked"),
            "connected": str(runtime.get("status") or "").lower() in {
                "connected", "connected_with_warnings", "online", "ready", "available"
            },
        })
    return rows


def _resolve_default_runtime(manifest_payload: Mapping[str, Any], runtime_profiles: list[dict[str, Any]]) -> str:
    runtime = manifest_payload.get("runtime") if isinstance(manifest_payload.get("runtime"), Mapping) else {}
    preferred = str(runtime.get("preferred_profile_id") or "").strip()
    known = {str(item.get("profile_id") or "") for item in runtime_profiles}
    if preferred and preferred in known:
        return preferred

    defaults = get_backend_profile_payload().get("defaults", {}) or {}
    for key in ("comfy_spaces", "image", "video"):
        candidate = str(defaults.get(key) or "").strip()
        if candidate and candidate in known:
            return candidate

    connected = next((item for item in runtime_profiles if item.get("connected")), None)
    if connected:
        return str(connected.get("profile_id") or "")
    return str(runtime_profiles[0].get("profile_id") or "") if runtime_profiles else ""




def _public_health_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    base = payload.get("base") if isinstance(payload.get("base"), Mapping) else {}
    runtime_inventory = payload.get("runtime_inventory") if isinstance(payload.get("runtime_inventory"), Mapping) else {}
    return {
        "runtime_profile_id": str(payload.get("runtime_profile_id") or ""),
        "status": str(payload.get("status") or "not_checked"),
        "ready": bool(payload.get("ready")),
        "stale": bool(payload.get("stale")),
        "stale_reason": str(payload.get("stale_reason") or ""),
        "checked_at": str(payload.get("checked_at") or ""),
        "runtime_changed_since_previous": bool(payload.get("runtime_changed_since_previous")),
        "missing_node_count": len(base.get("missing_node_classes") or []),
        "missing_model_count": len(base.get("missing_models") or []),
        "node_count": int(runtime_inventory.get("node_count") or 0),
        "model_catalog_count": int(runtime_inventory.get("model_catalog_count") or 0),
    }


def _public_health_matrix(space_id: str, version: str, runtime_profiles: list[dict[str, Any]], *, root_dir: Path | str | None = None) -> dict[str, Any]:
    items = [
        _public_health_summary(load_space_runtime_health(space_id, version, runtime_profile_id=str(item.get("profile_id") or ""), root_dir=root_dir))
        for item in runtime_profiles
        if str(item.get("profile_id") or "").strip()
    ]
    return {
        "schema_version": "neo.comfy_space.dependency_health_summary.v1",
        "space_id": space_id,
        "version": version,
        "items": items,
        "count": len(items),
    }

def _public_card(bundle: Mapping[str, Any], *, versions: list[str] | None = None, active_version: str = "", latest_version: str = "", root_dir: Path | str | None = None) -> dict[str, Any]:
    manifest = bundle["manifest"]
    payload = model_to_dict(manifest)
    presentation = payload.get("presentation") if isinstance(payload.get("presentation"), Mapping) else {}
    runtime_profiles = _runtime_profiles()
    version = str(payload.get("version") or "")
    space_id = str(payload.get("space_id") or "")
    thumbnail = str(presentation.get("thumbnail") or "").strip()
    icon = str(presentation.get("icon") or "").strip()
    default_runtime_profile_id = _resolve_default_runtime(payload, runtime_profiles)
    dependency_health = load_space_runtime_health(space_id, version, runtime_profile_id=default_runtime_profile_id, root_dir=root_dir) if default_runtime_profile_id else {"status": "not_checked", "ready": False, "stale": True}
    return {
        "space_id": space_id,
        "name": str(payload.get("name") or space_id),
        "version": version,
        "active_version": str(active_version or version),
        "latest_version": str(latest_version or ((versions or [version])[0] if (versions or [version]) else version)),
        "versions": list(versions or [version]),
        "rollback_available": len(list(versions or [version])) > 1,
        "description": str(payload.get("description") or ""),
        "category": str(presentation.get("category") or "Workflow"),
        "tags": [str(item) for item in presentation.get("tags") or [] if str(item).strip()],
        "icon_url": f"/api/comfy-spaces/library/{space_id}/{version}/presentation/icon" if icon else "",
        "thumbnail_url": f"/api/comfy-spaces/library/{space_id}/{version}/presentation/thumbnail" if thumbnail else "",
        "input_count": len(payload.get("inputs") or []),
        "output_count": len(payload.get("outputs") or []),
        "preferred_runtime_profile_id": str((payload.get("runtime") or {}).get("preferred_profile_id") or ""),
        "default_runtime_profile_id": default_runtime_profile_id,
        "dependency_health": {
            "runtime_profile_id": default_runtime_profile_id,
            "status": str(dependency_health.get("status") or "not_checked"),
            "ready": bool(dependency_health.get("ready")),
            "stale": bool(dependency_health.get("stale")),
            "checked_at": str(dependency_health.get("checked_at") or ""),
        },
        "runtime_profile_count": len(runtime_profiles),
        "status": "published",
        "ready_to_open": True,
    }


def _version_dirs(space_root: Path) -> list[Path]:
    versions_root = space_root / "versions"
    if not versions_root.is_dir():
        return []
    return [item for item in versions_root.iterdir() if item.is_dir()]


def list_published_spaces(*, root_dir: Path | str | None = None) -> dict[str, Any]:
    library_root = comfy_spaces_library_root(root_dir)
    cards: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    if library_root.is_dir():
        for space_root in sorted((item for item in library_root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
            valid_versions: list[tuple[str, dict[str, Any]]] = []
            for version_root in _version_dirs(space_root):
                version = version_root.name
                try:
                    bundle = _load_published_space_package(space_root.name, version, root_dir=root_dir)
                    valid_versions.append((version, bundle))
                except (SpaceExecutionRuntimeError, ValueError) as exc:
                    invalid.append({"space_id": space_root.name, "version": version, "message": str(exc)})
            if not valid_versions:
                continue
            valid_versions.sort(key=lambda item: _version_key(item[0]), reverse=True)
            versions = [item[0] for item in valid_versions]
            active_version = resolve_active_space_version(space_root.name, root_dir=root_dir)
            selected = next((item for item in valid_versions if item[0] == active_version), valid_versions[0])
            cards.append(_public_card(selected[1], versions=versions, active_version=selected[0], latest_version=versions[0], root_dir=root_dir))

    runs = list_space_runs(limit=200, active_only=False, root_dir=root_dir)
    by_space: dict[str, list[dict[str, Any]]] = {}
    for item in runs.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        by_space.setdefault(str(item.get("space_id") or ""), []).append(dict(item))
    for card in cards:
        recent = by_space.get(str(card.get("space_id") or ""), [])
        card["recent_run"] = recent[0] if recent else None
        card["active_run"] = next((item for item in recent if str(item.get("status") or "").lower() not in {"completed", "failed", "cancelled", "canceled"}), None)

    return {
        "schema_version": SPACE_SURFACE_SCHEMA_VERSION,
        "service_version": SPACE_SURFACE_SERVICE_VERSION,
        "ok": True,
        "spaces": cards,
        "count": len(cards),
        "invalid_package_count": len(invalid),
        "runtime_profiles": _runtime_profiles(),
        "public_runner_mounted": True,
    }


def load_public_space(space_id: str, version: str | None = None, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    if not clean_space:
        raise ComfySpaceSurfaceError("space_id is required")
    if not clean_version:
        listing = list_published_spaces(root_dir=root_dir)
        card = next((item for item in listing.get("spaces") or [] if item.get("space_id") == clean_space), None)
        if not card:
            raise ComfySpaceSurfaceError(f"Unknown published Comfy Space: {clean_space}")
        clean_version = str(card.get("version") or "")
    try:
        bundle = _load_published_space_package(clean_space, clean_version, root_dir=root_dir)
    except SpaceExecutionRuntimeError as exc:
        raise ComfySpaceSurfaceError(str(exc)) from exc

    manifest = model_to_dict(bundle["manifest"])
    ui = model_to_dict(bundle["ui"])
    runtime_profiles = _runtime_profiles()
    runs = list_space_runs(limit=100, active_only=False, root_dir=root_dir)
    recent_runs = [
        dict(item) for item in runs.get("items") or []
        if isinstance(item, Mapping)
        and str(item.get("space_id") or "") == clean_space
        and str(item.get("version") or "") == clean_version
    ][:20]
    version_info = list_space_versions(clean_space, root_dir=root_dir)
    all_versions = [str(item.get("version") or "") for item in version_info.get("versions") or []]
    card = _public_card(bundle, versions=all_versions or [clean_version], active_version=str(version_info.get("active_version") or clean_version), latest_version=str(version_info.get("latest_version") or clean_version), root_dir=root_dir)
    return {
        "schema_version": SPACE_SURFACE_SCHEMA_VERSION,
        "service_version": SPACE_SURFACE_SERVICE_VERSION,
        "ok": True,
        "space": card,
        "manifest": manifest,
        "ui": ui,
        "stages": model_to_dict(bundle["stages"]) if bundle.get("stages") is not None else None,
        "dynamic_controls": model_to_dict(bundle["dynamic_controls"]) if bundle.get("dynamic_controls") is not None else None,
        "runtime_profiles": runtime_profiles,
        "default_runtime_profile_id": _resolve_default_runtime(manifest, runtime_profiles),
        "dependency_health": _public_health_matrix(clean_space, clean_version, runtime_profiles, root_dir=root_dir),
        "versioning": version_info,
        "recent_runs": recent_runs,
        "workflow_exposed": False,
        "publication_receipt_exposed": False,
        "public_runner_mounted": True,
    }



def runtime_graph_addon_catalog(
    space_id: str,
    version: str,
    *,
    runtime_profile_id: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return per-run Graph Add-ons proven compatible with this Space/runtime.

    The catalog is derived from the immutable published Graph Anchor sidecar plus
    the selected runtime's live /object_info. It never mutates workflow topology.
    """

    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    clean_runtime = str(runtime_profile_id or "").strip()
    if not clean_space or not clean_version or not clean_runtime:
        raise ComfySpaceSurfaceError("space_id, version, and runtime_profile_id are required")
    try:
        bundle = _load_published_space_package(clean_space, clean_version, root_dir=root_dir)
        object_info, runtime_probe = probe_comfy_profile_object_info(clean_runtime)
    except (SpaceExecutionRuntimeError, SpaceRuntimeProbeError) as exc:
        raise ComfySpaceSurfaceError(str(exc)) from exc
    if not runtime_probe.get("object_info_available"):
        raise ComfySpaceSurfaceError("Selected ComfyUI runtime did not provide /object_info for Graph Add-on compatibility.")
    return build_graph_addon_catalog(
        space_id=clean_space,
        version=clean_version,
        runtime_profile_id=clean_runtime,
        graph_anchors=bundle.get("graph_anchors"),
        object_info=object_info,
    )

def _choice_list(raw_spec: Any) -> list[Any]:
    """Extract a Comfy combo catalog from one /object_info input spec."""
    if isinstance(raw_spec, (list, tuple)) and raw_spec:
        first = raw_spec[0]
        if isinstance(first, (list, tuple)):
            return list(first)
    if isinstance(raw_spec, Mapping):
        choices = raw_spec.get("choices")
        if isinstance(choices, (list, tuple)):
            return list(choices)
    return []


def _class_input_spec(object_info: Mapping[str, Any], class_type: str, field: str) -> Any:
    node = object_info.get(class_type) if isinstance(object_info.get(class_type), Mapping) else {}
    inputs = node.get("input") if isinstance(node.get("input"), Mapping) else {}
    for group in ("required", "optional", "hidden"):
        rows = inputs.get(group) if isinstance(inputs.get(group), Mapping) else {}
        if field in rows:
            return rows[field]
    return None




def _normalize_catalog_locator(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text.strip("/")


def _resolve_catalog_configured_value(configured_value: Any, choices: list[Any], *, path_like: bool) -> dict[str, Any]:
    configured = str(configured_value or "").strip()
    catalog = [str(item).strip() for item in choices if isinstance(item, str) and str(item).strip()]
    if not catalog:
        return {
            "configured_available": None,
            "runtime_resolved_value": None,
            "resolution_method": "catalog_unavailable",
            "resolution_candidates": [],
        }
    if not configured:
        return {
            "configured_available": None,
            "runtime_resolved_value": None,
            "resolution_method": "no_configured_value",
            "resolution_candidates": [],
        }
    if configured in catalog:
        return {
            "configured_available": True,
            "runtime_resolved_value": configured,
            "resolution_method": "exact",
            "resolution_candidates": [configured],
        }
    if not path_like:
        return {
            "configured_available": False,
            "runtime_resolved_value": None,
            "resolution_method": "not_in_runtime_catalog",
            "resolution_candidates": [],
        }

    configured_norm = _normalize_catalog_locator(configured)
    normalized = {choice: _normalize_catalog_locator(choice) for choice in catalog}
    path_matches = [choice for choice, norm in normalized.items() if norm == configured_norm]
    if len(path_matches) == 1:
        return {
            "configured_available": True,
            "runtime_resolved_value": path_matches[0],
            "resolution_method": "normalized_path",
            "resolution_candidates": path_matches,
        }
    folded = configured_norm.casefold()
    casefold_matches = [choice for choice, norm in normalized.items() if norm.casefold() == folded]
    if len(casefold_matches) == 1:
        return {
            "configured_available": True,
            "runtime_resolved_value": casefold_matches[0],
            "resolution_method": "normalized_casefold_path",
            "resolution_candidates": casefold_matches,
        }
    basename = configured_norm.rsplit("/", 1)[-1].casefold()
    basename_matches = [
        choice for choice, norm in normalized.items()
        if norm.rsplit("/", 1)[-1].casefold() == basename
    ]
    if len(basename_matches) == 1:
        return {
            "configured_available": True,
            "runtime_resolved_value": basename_matches[0],
            "resolution_method": "unique_basename",
            "resolution_candidates": basename_matches,
        }
    if len(basename_matches) > 1:
        return {
            "configured_available": False,
            "runtime_resolved_value": None,
            "resolution_method": "ambiguous_basename",
            "resolution_candidates": basename_matches[:25],
        }
    return {
        "configured_available": False,
        "runtime_resolved_value": None,
        "resolution_method": "not_in_runtime_catalog",
        "resolution_candidates": [],
    }


def runtime_option_catalogs(
    space_id: str,
    version: str,
    *,
    runtime_profile_id: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    if not str(runtime_profile_id or "").strip():
        raise ComfySpaceSurfaceError("runtime_profile_id is required")
    public = load_public_space(space_id, version, root_dir=root_dir)
    manifest = public["manifest"]
    ui = public["ui"]
    try:
        object_info, probe = probe_comfy_profile_object_info(runtime_profile_id)
    except SpaceRuntimeProbeError as exc:
        raise ComfySpaceSurfaceError(str(exc)) from exc

    workflow = _load_published_space_package(space_id, version, root_dir=root_dir)["workflow"]
    ui_by_id = {str(item.get("input_id") or ""): item for item in ui.get("inputs") or [] if isinstance(item, Mapping)}
    catalogs: dict[str, list[Any]] = {}
    catalog_states: dict[str, dict[str, Any]] = {}
    for item in manifest.get("inputs") or []:
        if not isinstance(item, Mapping):
            continue
        input_id = str(item.get("input_id") or "")
        presentation = ui_by_id.get(input_id, {})
        control = str(presentation.get("control") or "")
        if control not in {"select", "search_select", "model_select"}:
            continue
        binding = item.get("binding") if isinstance(item.get("binding"), Mapping) else {}
        node_id = str(binding.get("node_id") or "")
        field = str(binding.get("field") or "")
        node = workflow.get(node_id) if isinstance(workflow.get(node_id), Mapping) else {}
        class_type = str(node.get("class_type") or "")
        values = _choice_list(_class_input_spec(object_info, class_type, field))
        configured_value = item.get("default")
        resolution = _resolve_catalog_configured_value(
            configured_value,
            values,
            path_like=control == "model_select",
        )
        catalog_states[input_id] = {
            "status": "ready" if values else "unavailable",
            "source": "comfy_object_info",
            "control": control,
            "class_type": class_type,
            "field": field,
            "count": len(values),
            "configured_value": configured_value,
            **resolution,
        }
        if values:
            catalogs[input_id] = deepcopy(values)
    return {
        "schema_version": SPACE_SURFACE_SCHEMA_VERSION,
        "ok": True,
        "space_id": str(space_id),
        "version": str(version),
        "runtime_profile_id": str(runtime_profile_id),
        "runtime_probe": probe,
        "option_catalogs": catalogs,
        "catalog_states": catalog_states,
        "execution_performed": False,
    }


def presentation_asset_path(
    space_id: str,
    version: str,
    kind: str,
    *,
    root_dir: Path | str | None = None,
) -> Path:
    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in _PRESENTATION_KINDS:
        raise ComfySpaceSurfaceError("presentation asset kind must be icon or thumbnail")
    public = load_public_space(space_id, version, root_dir=root_dir)
    presentation = public["manifest"].get("presentation") if isinstance(public["manifest"].get("presentation"), Mapping) else {}
    relative = str(presentation.get(clean_kind) or "").strip().replace("\\", "/")
    if not relative:
        raise ComfySpaceSurfaceError(f"Published Space has no {clean_kind} presentation asset")
    root = comfy_space_version_root(space_id, version, root_dir).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ComfySpaceSurfaceError("Presentation asset path escapes the published Space package") from exc
    if not candidate.is_file():
        raise ComfySpaceSurfaceError(f"Published Space {clean_kind} asset is missing")
    return candidate


def surface_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_SURFACE_SCHEMA_VERSION,
        "service_version": SPACE_SURFACE_SERVICE_VERSION,
        "status": "implemented",
        "top_level_surface": True,
        "published_library": True,
        "shared_renderer": True,
        "neutral_asset_bus": True,
        "universal_execution_runtime": True,
        "live_progress": True,
        "navigation_safe_runs": True,
        "generic_ui_presets": False,
        "generic_model_family_selector": False,
        "generic_main_model_type_selector": False,
        "generic_workflow_mode_selector": False,
        "generate_control": True,
        "pause_control": False,
        "stop_control": True,
        "progress_control": True,
        "live_runtime_catalog_controls": True,
        "runtime_catalog_authority": "comfy_object_info",
        "runtime_catalog_route": "/api/comfy-spaces/runtime/options",
        "runtime_catalog_legacy_route": True,
        "per_run_graph_addons": True,
        "graph_addon_catalog_route": "/api/comfy-spaces/runtime/addons/catalog",
        "dependency_health_route": "/api/comfy-spaces/health",
        "dependency_health_refresh_route": "/api/comfy-spaces/health/refresh",
        "dependency_health_matrix_route": "/api/comfy-spaces/health/matrix",
        "dependency_health_advisory_only": True,
        "graph_addon_module_lane": True,
        "graph_addon_raw_node_lane": True,
        "graph_addon_execution_clone_only": True,
        "space_variants": True,
        "variant_base_version_pinned": True,
        "variant_silent_retargeting": False,
        "public_runner_mounted": True,
    }
