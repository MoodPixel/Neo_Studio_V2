from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib import parse
import os

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML is optional during source-only checks.
    yaml = None


COMFY_PROVIDER_IDS = {"comfyui", "comfyui_portable"}
COMFY_MODEL_PATH_SCHEMA = "neo.providers.comfy_model_paths.v1"
COMFY_MODEL_FILE_SCAN_SCHEMA = "neo.providers.comfy_model_files.v1"
COMFY_EXTRA_MODEL_PATH_SCHEMA = "neo.providers.comfy_extra_model_paths.v1"
SERVER_PATH_POLICY = "absolute_paths_server_side_only"
DEFAULT_MODEL_FILE_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_path(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    return "" if "\x00" in text else text


def _path(value: Any) -> Path | None:
    text = _clean_path(value)
    if not text:
        return None
    try:
        return Path(text).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None


def _path_key(path: Path | None) -> str:
    return str(path or "").replace("\\", "/").casefold()


def _models_candidate(value: Any, source: str) -> dict[str, Any] | None:
    models_root = _path(value)
    if not models_root:
        return None
    comfy_root = models_root.parent if models_root.name.casefold() == "models" else None
    return {"models_root": models_root, "comfy_root": comfy_root, "source": source}


def _comfy_root_candidates(value: Any, source: str) -> list[dict[str, Any]]:
    root = _path(value)
    if not root:
        return []
    if root.name.casefold() == "models":
        return [{"models_root": root, "comfy_root": root.parent, "source": source}]

    candidates = [
        {"models_root": root / "models", "comfy_root": root, "source": source},
        {"models_root": root / "ComfyUI" / "models", "comfy_root": root / "ComfyUI", "source": source},
    ]
    # A portable wrapper may already be the ComfyUI application root. Keeping
    # both conventional layouts lets existence checks select the real one
    # without a machine-specific directory assumption.
    return candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        key = _path_key(candidate.get("models_root"))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _existing_directory(path: Path | None) -> bool:
    try:
        return bool(path and path.exists() and path.is_dir())
    except OSError:
        return False


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = _path_key(path)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _node_manager_authority_roots(settings: Mapping[str, Any]) -> list[Path]:
    """Return every local Comfy root already owned by Node Manager settings.

    Portable installs may save the wrapper as ``comfy_root_path`` while the
    ``custom_nodes_path`` points inside ``<wrapper>/ComfyUI/custom_nodes``. The
    inner root remains authoritative for native models; the wrapper remains
    authoritative for a sibling ``extra_model_paths.yaml``. Keep both.
    """

    roots: list[Path] = []
    custom_nodes = _path(settings.get("custom_nodes_path"))
    if custom_nodes:
        inner_root = custom_nodes.parent if custom_nodes.name.casefold() == "custom_nodes" else custom_nodes
        roots.append(inner_root)
        if inner_root.name.casefold() == "comfyui":
            roots.append(inner_root.parent)
    configured_root = _path(settings.get("comfy_root_path"))
    if configured_root:
        roots.append(configured_root)
        roots.append(configured_root / "ComfyUI")
    return _dedupe_paths(roots)


def _node_manager_root_candidates(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    custom_nodes = _path(settings.get("custom_nodes_path"))
    custom_nodes_root = (
        custom_nodes.parent
        if custom_nodes and custom_nodes.name.casefold() == "custom_nodes"
        else custom_nodes
    )
    for root in _node_manager_authority_roots(settings):
        source = "node_manager_custom_nodes" if custom_nodes_root and _path_key(root) == _path_key(custom_nodes_root) else "node_manager_comfy_root"
        candidates.extend(_comfy_root_candidates(root, source))
    return candidates


def resolve_comfy_model_paths(
    backend_details: Mapping[str, Any] | None,
    *,
    model_paths: Mapping[str, Any] | None = None,
    node_manager_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach one server-only Comfy filesystem resolution snapshot.

    The resolver is deliberately pure: callers load local Admin/Node Manager
    settings and pass them in. This keeps catalog requests read-only and makes
    the path authority reusable by every Comfy-backed extension.
    """

    details = dict(_mapping(backend_details))
    provider_id = str(details.get("provider_id") or "").strip()
    if provider_id and provider_id not in COMFY_PROVIDER_IDS:
        return details

    connection = _mapping(details.get("connection"))
    runtime = _mapping(details.get("runtime"))
    configured_backends = _mapping(_mapping(model_paths).get("backends"))
    configured_comfy = _mapping(configured_backends.get("comfyui"))
    admin_enabled = configured_comfy.get("enabled", True) is not False

    configured_models_root = _clean_path(configured_comfy.get("models_root")) if admin_enabled else ""
    configured_comfy_root = _clean_path(configured_comfy.get("root")) if admin_enabled else ""
    if configured_models_root:
        details["configured_models_root"] = configured_models_root
    if configured_comfy_root:
        details["configured_comfy_root"] = configured_comfy_root

    candidates: list[dict[str, Any]] = []
    if configured_models_root:
        candidate = _models_candidate(configured_models_root, "admin_models_paths")
        if candidate:
            candidates.append(candidate)
    if configured_comfy_root:
        candidates.extend(_comfy_root_candidates(configured_comfy_root, "admin_comfy_root"))

    for source, value in (
        ("profile_models_root", details.get("models_root")),
        ("profile_connection_models_root", connection.get("models_root")),
        ("profile_runtime_models_root", runtime.get("models_root")),
    ):
        candidate = _models_candidate(value, source)
        if candidate:
            candidates.append(candidate)

    for source, value in (
        ("profile_comfy_root", details.get("comfy_root")),
        ("profile_comfy_root_path", details.get("comfy_root_path")),
        ("profile_comfyui_path", details.get("comfyui_path")),
        ("profile_connection_comfy_root", connection.get("comfy_root")),
        ("profile_runtime_comfy_root", runtime.get("comfy_root")),
        ("profile_portable_path", details.get("portable_path")),
        ("profile_connection_portable_path", connection.get("portable_path")),
        ("profile_runtime_portable_path", runtime.get("portable_path")),
    ):
        candidates.extend(_comfy_root_candidates(value, source))

    node_manager = _mapping(node_manager_settings)
    candidates.extend(_node_manager_root_candidates(node_manager))
    candidates = _dedupe_candidates(candidates)

    authority_roots = _dedupe_paths([
        candidate_root
        for candidate in candidates
        for candidate_root in (candidate.get("comfy_root"),)
        if isinstance(candidate_root, Path)
    ] + _node_manager_authority_roots(node_manager))
    if authority_roots:
        details["_comfy_path_authority"] = {
            "schema_version": COMFY_MODEL_PATH_SCHEMA,
            "path_policy": SERVER_PATH_POLICY,
            "comfy_roots": [str(root) for root in authority_roots],
        }

    selected = next(
        (candidate for candidate in candidates if _existing_directory(candidate.get("models_root"))),
        candidates[0] if candidates else None,
    )
    resolved_models_root = selected.get("models_root") if selected else None
    resolved_comfy_root = selected.get("comfy_root") if selected else None
    source = str(selected.get("source") or "") if selected else ""

    if resolved_models_root:
        details["resolved_models_root"] = str(resolved_models_root)
        details["models_root"] = str(resolved_models_root)
    if resolved_comfy_root:
        details["resolved_comfy_root"] = str(resolved_comfy_root)
        if not _clean_path(details.get("comfy_root")):
            details["comfy_root"] = str(resolved_comfy_root)
    if source:
        details["models_root_source"] = source

    details["comfy_model_path_resolution"] = {
        "schema_version": COMFY_MODEL_PATH_SCHEMA,
        "path_policy": SERVER_PATH_POLICY,
        "models_root_source": source,
        "models_root_available": _existing_directory(resolved_models_root),
        "comfy_root_available": _existing_directory(resolved_comfy_root),
        "admin_models_root_configured": bool(configured_models_root),
        "admin_comfy_root_configured": bool(configured_comfy_root),
        "candidate_count": len(candidates),
        "authority_root_count": len(authority_roots),
    }
    return details


def _dedupe_names(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        name = str(value or "").strip().replace("\\", "/")
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _split_folder_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_split_folder_values(item))
        return result
    text = str(value).strip()
    if not text:
        return []
    return [line.strip() for line in text.replace(";", "\n").splitlines() if line.strip()]


def _candidate_extra_model_yaml_paths(details: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    connection = _mapping(details.get("connection"))
    runtime = _mapping(details.get("runtime"))
    authority = _mapping(details.get("_comfy_path_authority"))

    for raw in (
        os.environ.get("COMFYUI_EXTRA_MODEL_PATHS"),
        os.environ.get("COMFYUI_EXTRA_MODEL_PATHS_YAML"),
        details.get("extra_model_paths_yaml"),
        details.get("extra_model_paths"),
        connection.get("extra_model_paths_yaml"),
        connection.get("extra_model_paths"),
        runtime.get("extra_model_paths_yaml"),
        runtime.get("extra_model_paths"),
    ):
        for value in _split_folder_values(raw):
            path = _path(value)
            if path:
                paths.append(path)

    roots: list[Path] = []
    for raw_root in (
        details.get("resolved_comfy_root"),
        details.get("configured_comfy_root"),
        details.get("comfy_root"),
        details.get("comfy_root_path"),
        details.get("portable_path"),
        connection.get("comfy_root"),
        connection.get("comfy_root_path"),
        connection.get("portable_path"),
        runtime.get("comfy_root"),
        runtime.get("comfy_root_path"),
        runtime.get("portable_path"),
    ):
        root = _path(raw_root)
        if root:
            roots.append(root)
    for raw_root in authority.get("comfy_roots") if isinstance(authority.get("comfy_roots"), list) else []:
        root = _path(raw_root)
        if root:
            roots.append(root)

    for models_key in ("resolved_models_root", "configured_models_root", "models_root"):
        models_root = _path(details.get(models_key))
        if models_root and models_root.name.casefold() == "models":
            roots.append(models_root.parent)

    for root in _dedupe_paths(roots):
        paths.extend([
            root / "extra_model_paths.yaml",
            root / "extra_model_paths.yml",
            root / "ComfyUI" / "extra_model_paths.yaml",
            root / "ComfyUI" / "extra_model_paths.yml",
        ])
        # Conservative portable-wrapper recovery: only the YAML candidate is
        # considered, never an arbitrary parent models directory.
        if root.name.casefold() == "comfyui":
            paths.extend([
                root.parent / "extra_model_paths.yaml",
                root.parent / "extra_model_paths.yml",
            ])

    return _dedupe_paths(paths)

def _load_extra_model_paths_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    if yaml is not None:
        try:
            payload = yaml.safe_load(text)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    # Minimal fallback for Comfy's common mapping + block-string shape.
    root: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    current_block_key = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":"):
            current = root.setdefault(stripped[:-1].strip(), {})
            current_block_key = ""
            continue
        if current is None or ":" not in stripped:
            if current is not None and current_block_key:
                current[current_block_key] = f"{current.get(current_block_key, '')}\n{stripped}".strip()
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "|":
            current[key] = ""
            current_block_key = key
        else:
            current[key] = value.strip('"\'')
            current_block_key = ""
    return root


def _extra_model_folders(yaml_path: Path, categories: set[str]) -> list[Path]:
    payload = _load_extra_model_paths_yaml(yaml_path)
    folders: list[Path] = []
    for raw_group in payload.values():
        group = _mapping(raw_group)
        if not group:
            continue
        base = _path(group.get("base_path")) or yaml_path.parent
        if not base.is_absolute():
            base = yaml_path.parent / base
        for key, raw_value in group.items():
            normalized_key = str(key or "").strip().casefold().replace("-", "_")
            if normalized_key not in categories:
                continue
            for folder_value in _split_folder_values(raw_value):
                folder = _path(folder_value)
                if not folder:
                    continue
                if not folder.is_absolute():
                    folder = base / folder
                folders.append(folder)
    return folders



def resolve_comfy_extra_model_folders(
    backend_details: Mapping[str, Any] | None,
    *,
    categories: set[str] | tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Resolve existing ``extra_model_paths`` folders using shared path authority.

    Absolute paths remain internal ``Path`` objects. Diagnostics are deliberately
    path-free so callers may expose only the counts at a public API boundary.
    """

    details = _mapping(backend_details)
    normalized_categories = {
        str(item or "").strip().casefold().replace("-", "_")
        for item in categories
        if str(item or "").strip()
    }
    candidates = _candidate_extra_model_yaml_paths(details)
    config_files_found = 0
    configured: list[Path] = []
    for yaml_path in candidates:
        try:
            exists = yaml_path.exists() and yaml_path.is_file()
        except OSError:
            exists = False
        if not exists:
            continue
        config_files_found += 1
        configured.extend(_extra_model_folders(yaml_path, normalized_categories))

    folders = _dedupe_paths(configured)
    existing_folder_count = sum(1 for folder in folders if _existing_directory(folder))
    return {
        "folders": folders,
        "diagnostics": {
            "schema_version": COMFY_EXTRA_MODEL_PATH_SCHEMA,
            "path_policy": SERVER_PATH_POLICY,
            "config_candidates": len(candidates),
            "config_files_found": config_files_found,
            "configured_folder_count": len(folders),
            "existing_folder_count": existing_folder_count,
            "category_count": len(normalized_categories),
        },
    }

def _scan_model_folder(folder: Path, suffixes: set[str]) -> list[str]:
    if not _existing_directory(folder):
        return []
    try:
        files = [item for item in folder.rglob("*") if item.is_file() and item.suffix.casefold() in suffixes]
    except OSError:
        return []
    values: list[str] = []
    for file_path in sorted(files, key=lambda item: str(item).casefold()):
        try:
            values.append(file_path.relative_to(folder).as_posix())
        except ValueError:
            values.append(file_path.name)
    return _dedupe_names(values)


def discover_comfy_model_files(
    backend_details: Mapping[str, Any] | None,
    *,
    folder_names: tuple[str, ...] | list[str],
    extra_model_categories: set[str] | tuple[str, ...] | list[str] = (),
    suffixes: set[str] | None = None,
) -> dict[str, Any]:
    """Scan configured Comfy folders without creating a saved catalog.

    Results contain loader-relative names only. Absolute paths are used during
    the server-side scan and never appear in diagnostics or model values.
    """

    details = _mapping(backend_details)
    allowed_suffixes = {str(item).casefold() for item in (suffixes or DEFAULT_MODEL_FILE_SUFFIXES)}
    safe_folder_names = [
        name for name in (str(item or "").strip().replace("\\", "/") for item in folder_names)
        if name and not name.startswith("/") and ".." not in Path(name).parts
    ]
    models_root = next(
        (
            candidate
            for candidate in (
                _path(details.get("resolved_models_root")),
                _path(details.get("models_root")),
                _path(details.get("configured_models_root")),
            )
            if _existing_directory(candidate)
        ),
        None,
    )

    direct_models: list[str] = []
    direct_folder_count = 0
    if models_root:
        for folder_name in safe_folder_names:
            folder = models_root / folder_name
            if _existing_directory(folder):
                direct_folder_count += 1
            direct_models.extend(_scan_model_folder(folder, allowed_suffixes))

    categories = {
        str(item or "").strip().casefold().replace("-", "_")
        for item in extra_model_categories
        if str(item or "").strip()
    }
    extra_models: list[str] = []
    extra_resolution = resolve_comfy_extra_model_folders(details, categories=categories) if categories else {
        "folders": [],
        "diagnostics": {
            "config_files_found": 0,
            "existing_folder_count": 0,
        },
    }
    for folder in extra_resolution.get("folders", []):
        if isinstance(folder, Path):
            extra_models.extend(_scan_model_folder(folder, allowed_suffixes))

    direct_models = _dedupe_names(direct_models)
    extra_models = _dedupe_names(extra_models)
    return {
        "models": _dedupe_names(direct_models + extra_models),
        "sources": {
            "models_root": direct_models,
            "extra_model_paths": extra_models,
        },
        "diagnostics": {
            "schema_version": COMFY_MODEL_FILE_SCAN_SCHEMA,
            "path_policy": SERVER_PATH_POLICY,
            "models_root_source": str(details.get("models_root_source") or ""),
            "models_root_available": bool(models_root),
            "direct_folder_count": direct_folder_count,
            "direct_file_count": len(direct_models),
            "extra_config_files_found": int(extra_resolution.get("diagnostics", {}).get("config_files_found") or 0),
            "extra_folder_count": int(extra_resolution.get("diagnostics", {}).get("existing_folder_count") or 0),
            "extra_file_count": len(extra_models),
        },
    }


def _model_names_from_endpoint_payload(payload: object) -> list[str]:
    candidates: list[object] = []
    if isinstance(payload, list):
        candidates.extend(payload)
    elif isinstance(payload, dict):
        for key in ("models", "files", "items", "names"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    values: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path")
        else:
            raw = item
        values.append(str(raw or ""))
    return _dedupe_names(values)


def _registered_folder_names(payload: object) -> list[str]:
    candidates: list[object] = []
    if isinstance(payload, list):
        candidates.extend(payload)
    elif isinstance(payload, dict):
        for key in ("folders", "folder_names", "models", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    return _dedupe_names([str(item) for item in candidates if isinstance(item, str)])


def query_comfy_model_folders(
    provider: Any,
    folder_names: tuple[str, ...] | list[str],
    *,
    timeout_seconds: float,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Query selected registered Comfy folders with isolated failures."""

    requested = _dedupe_names([str(item) for item in folder_names])
    errors: dict[str, str] = {}
    try:
        registered = _registered_folder_names(provider._get_json("/models", timeout=timeout_seconds))
    except Exception as exc:  # noqa: BLE001 - older Comfy builds may lack the index.
        registered = []
        errors["models_index"] = type(exc).__name__
    registered_by_fold = {name.casefold(): name for name in registered}
    selected = [registered_by_fold[name.casefold()] for name in requested if name.casefold() in registered_by_fold]
    if not registered:
        selected = requested

    folders: dict[str, list[str]] = {}
    for folder_name in selected:
        key = folder_name.casefold()
        try:
            payload = provider._get_json(f"/models/{parse.quote(folder_name, safe='')}", timeout=timeout_seconds)
            folders[key] = _model_names_from_endpoint_payload(payload)
        except Exception as exc:  # noqa: BLE001 - preserve other registered folders.
            errors[key] = type(exc).__name__
    return folders, {
        "schema_version": "neo.providers.comfy_registered_model_folders.v1",
        "path_policy": SERVER_PATH_POLICY,
        "index_available": bool(registered),
        "registered_folder_count": len(registered),
        "queried_folder_count": len(selected),
        "error_codes": errors,
    }
