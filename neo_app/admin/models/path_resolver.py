from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .manifest_loader import load_folder_rules, load_model_catalog
from .model_paths import load_model_paths

RESOLVER_SCHEMA_ID = "neo.admin.models.path_resolver.v1"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _is_windows_absolute(path: str) -> bool:
    return bool(PureWindowsPath(path).drive)


def _is_absolute(path: str) -> bool:
    clean = path.replace("\\", "/")
    return clean.startswith("/") or _is_windows_absolute(path)


def _safe_relative_subdir(subdir: str) -> tuple[bool, list[str], str]:
    clean = _clean(subdir).replace("\\", "/").strip("/")
    if not clean:
        return False, [], "empty_subdir"
    if _is_absolute(clean):
        return False, [], "absolute_subdir_not_allowed"
    parts = [part for part in clean.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return False, parts, "path_traversal_not_allowed"
    return True, parts, "ok"


def _join_path(root: str, parts: list[str]) -> str:
    root = _clean(root)
    if not root:
        return ""
    trimmed = root.rstrip("\\/")
    if not parts:
        return trimmed
    separator = "\\" if ("\\" in root or _is_windows_absolute(root)) else "/"
    return trimmed + separator + separator.join(parts)


def _strip_model_root_prefix(parts: list[str]) -> list[str]:
    if parts and parts[0].lower() == "models":
        return parts[1:]
    return parts


def _find_catalog_record(catalog_id: str) -> dict[str, Any] | None:
    catalog = load_model_catalog()
    for record in _as_list(catalog.get("records")):
        if isinstance(record, dict) and str(record.get("id") or "") == catalog_id:
            return record
    return None


def _placeholder_root(placeholder: str, backend_id: str, model_paths: dict[str, Any]) -> tuple[str, str]:
    backends = _as_dict(model_paths.get("backends"))
    local_llm = _as_dict(backends.get("local_llm"))
    backend = _as_dict(backends.get(backend_id))
    placeholder = placeholder.strip("{}")
    if placeholder == "user_llm_models_root" and backend_id == "koboldcpp" and _clean(backend.get("models_root")):
        return _clean(backend.get("models_root")), "backends.koboldcpp.models_root"
    if placeholder in backend:
        return _clean(backend.get(placeholder)), f"backends.{backend_id}.{placeholder}"
    if placeholder in local_llm:
        return _clean(local_llm.get(placeholder)), f"backends.local_llm.{placeholder}"
    return "", placeholder


def resolve_model_target(
    *,
    backend_id: str,
    target_type: str,
    catalog_id: str = "",
    explicit_subdir: str = "",
    model_paths: dict[str, Any] | None = None,
    folder_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve backend + target_type to a user-local model folder.

    This is intentionally read-only. It does not create folders and does not
    check whether paths exist on disk. Later phases can add scanning/downloads.
    """

    backend_id = _clean(backend_id).lower()
    target_type = _clean(target_type).lower()
    catalog_id = _clean(catalog_id)
    model_paths = model_paths or load_model_paths(create=False)
    folder_rules = folder_rules or load_folder_rules()
    warnings: list[str] = []
    errors: list[str] = []

    if catalog_id and not target_type:
        record = _find_catalog_record(catalog_id)
        if record:
            install = _as_dict(record.get("install"))
            target_type = _clean(install.get("target_type") or record.get("model_type")).lower()
        else:
            errors.append(f"Unknown model catalog id: {catalog_id}")

    backend_rules = _as_dict(_as_dict(folder_rules.get("backends")).get(backend_id))
    if not backend_rules:
        errors.append(f"Unknown backend folder rule: {backend_id or '<missing>'}")

    rule_subdir = _clean(explicit_subdir or backend_rules.get(target_type))
    if not rule_subdir:
        errors.append(f"No folder rule for backend '{backend_id}' and target_type '{target_type}'")

    backends = _as_dict(model_paths.get("backends"))
    backend_paths = _as_dict(backends.get(backend_id))
    backend_enabled = bool(backend_paths.get("enabled", True))
    if not backend_paths and backend_id:
        warnings.append(f"No local path config exists for backend '{backend_id}'")

    root = ""
    root_key = ""
    path_parts: list[str] = []
    placeholder = rule_subdir.startswith("{") and rule_subdir.endswith("}")
    if placeholder:
        root, root_key = _placeholder_root(rule_subdir, backend_id, model_paths)
        path_parts = []
        if not root:
            errors.append(f"Missing path setting for {root_key}")
    else:
        safe, parts, reason = _safe_relative_subdir(rule_subdir)
        if not safe:
            errors.append(f"Unsafe folder rule for backend '{backend_id}' target '{target_type}': {reason}")
            parts = []
        if backend_id in {"comfyui", "forge"}:
            models_root = _clean(backend_paths.get("models_root"))
            root_path = _clean(backend_paths.get("root"))
            if models_root:
                root = models_root
                root_key = f"backends.{backend_id}.models_root"
                path_parts = _strip_model_root_prefix(parts)
            elif root_path:
                root = root_path
                root_key = f"backends.{backend_id}.root"
                path_parts = parts
                warnings.append(f"Using {root_key}; setting models_root gives more precise folder resolution.")
            else:
                errors.append(f"Missing model path setting for backend '{backend_id}'. Set models_root under Admin → Models → Paths.")
        elif backend_id in {"koboldcpp"}:
            root = _clean(backend_paths.get("models_root"))
            root_key = f"backends.{backend_id}.models_root"
            path_parts = _strip_model_root_prefix(parts)
            if not root:
                errors.append(f"Missing model path setting for {root_key}")
        else:
            root = _clean(backend_paths.get("models_root") or backend_paths.get("root"))
            root_key = f"backends.{backend_id}.models_root"
            path_parts = _strip_model_root_prefix(parts)
            if not root and backend_id:
                errors.append(f"Missing model path setting for backend '{backend_id}'")

    resolved_path = _join_path(root, path_parts)
    allowed_extensions = _as_dict(folder_rules.get("allowed_extensions")).get(target_type, [])
    status = "ready" if not errors and resolved_path else "needs_path"
    return {
        "schema_id": RESOLVER_SCHEMA_ID,
        "status": status,
        "ok": status == "ready",
        "phase": "phase2_paths_folder_resolver",
        "backend": backend_id,
        "backend_enabled": backend_enabled,
        "catalog_id": catalog_id,
        "target_type": target_type,
        "rule_subdir": rule_subdir,
        "root_key": root_key,
        "root": root,
        "relative_parts": path_parts,
        "resolved_path": resolved_path,
        "allowed_extensions": list(allowed_extensions) if isinstance(allowed_extensions, list) else [],
        "warnings": warnings,
        "errors": errors,
        "policy": {
            "read_only": True,
            "creates_folders": False,
            "stores_paths_in_repo": False,
            "runtime_store": "neo_data/config/model_paths.json",
        },
    }


def resolve_model_catalog_targets(catalog_id: str, *, model_paths: dict[str, Any] | None = None) -> dict[str, Any]:
    record = _find_catalog_record(_clean(catalog_id))
    if not record:
        return {
            "schema_id": "neo.admin.models.catalog_target_resolution.v1",
            "status": "not_found",
            "ok": False,
            "catalog_id": catalog_id,
            "targets": [],
            "errors": [f"Unknown model catalog id: {catalog_id}"],
        }
    install = _as_dict(record.get("install"))
    target_type = _clean(install.get("target_type") or record.get("model_type")).lower()
    backends = [_clean(item).lower() for item in _as_list(install.get("backend_targets")) if _clean(item)]
    targets = [
        resolve_model_target(
            backend_id=backend,
            target_type=target_type,
            catalog_id=str(record.get("id") or catalog_id),
            model_paths=model_paths,
        )
        for backend in backends
    ]
    return {
        "schema_id": "neo.admin.models.catalog_target_resolution.v1",
        "status": "ready" if targets else "no_backend_targets",
        "ok": bool(targets),
        "phase": "phase2_paths_folder_resolver",
        "catalog_id": catalog_id,
        "record": {
            "id": record.get("id"),
            "display_name": record.get("display_name"),
            "category": record.get("category"),
            "base_model": record.get("base_model"),
            "model_type": record.get("model_type"),
        },
        "targets": targets,
    }


def admin_model_resolve_target_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _as_dict(payload)
    catalog_id = _clean(payload.get("catalog_id"))
    backend_id = _clean(payload.get("backend") or payload.get("backend_id")).lower()
    target_type = _clean(payload.get("target_type")).lower()
    explicit_subdir = _clean(payload.get("subdir") or payload.get("rule_subdir"))
    if catalog_id and not backend_id and not target_type:
        return resolve_model_catalog_targets(catalog_id)
    return resolve_model_target(
        backend_id=backend_id,
        target_type=target_type,
        catalog_id=catalog_id,
        explicit_subdir=explicit_subdir,
    )
