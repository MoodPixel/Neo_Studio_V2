from __future__ import annotations

"""Permission-gated loader for user-installed extension runtime entrypoints.

The loader is intentionally generic: Neo Base never imports an extension by id.
External packages declare local entrypoints, request narrow capabilities, and are
loaded only after Admin grants a version-bound approval.
"""

from importlib import util as importlib_util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping

from fastapi import APIRouter, FastAPI

from neo_app.extensions.registry import (
    extension_runtime_permission_granted,
    get_extension,
    get_extension_records,
)

RUNTIME_ENTRYPOINT_KEYS = ("runtime", "backend.runtime")
WORKFLOW_ENTRYPOINT_KEYS = ("workflow_patch", "backend.workflow_patch")
EXTERNAL_RUNTIME_CONTRACT_VERSION = "neo.extension.runtime.v1"

_MODULE_CACHE: dict[tuple[str, str, str], ModuleType] = {}
_RUNTIME_STATUS: list[dict[str, Any]] = []


def _clean_relative_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("/")


def _safe_error(exc: Exception, record: Any) -> str:
    message = str(exc)
    install_path = str(Path(record.install_path).resolve())
    return message.replace(install_path, "<extension-root>").replace(install_path.replace("\\", "/"), "<extension-root>")


def _declared_python_assets(record: Any) -> set[str]:
    bundle = getattr(record.manifest, "asset_bundle", None)
    return {_clean_relative_path(item) for item in (getattr(bundle, "python", []) or []) if _clean_relative_path(item)}


def _entrypoint_value(record: Any, keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    entrypoints = record.manifest.entrypoints or {}
    for key in keys:
        value = _clean_relative_path(entrypoints.get(key))
        if value:
            return key, value
    return None, None


def _entrypoint_path(record: Any, keys: tuple[str, ...]) -> tuple[str, Path]:
    key, relative = _entrypoint_value(record, keys)
    if not key or not relative:
        raise ValueError(f"Missing extension entrypoint: {' or '.join(keys)}")
    if relative not in _declared_python_assets(record):
        raise ValueError(f"Entrypoint {key} must also be declared in asset_bundle.python: {relative}")
    base = Path(record.install_path).resolve()
    path = (base / relative).resolve()
    if base not in path.parents or not path.is_file():
        raise ValueError(f"Entrypoint is missing or escaped the extension folder: {relative}")
    return key, path


def _load_entrypoint_module(record: Any, keys: tuple[str, ...]) -> ModuleType:
    _key, path = _entrypoint_path(record, keys)
    cache_key = (record.manifest.id, str(record.manifest.version), str(path))
    cached = _MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    safe_id = "".join(char if char.isalnum() else "_" for char in record.manifest.id)
    package_root = f"neo_external_{safe_id}_{abs(hash((str(record.manifest.version), str(path.parents[1]))))}"
    relative_parts = list(path.relative_to(Path(record.install_path).resolve()).with_suffix("").parts)
    module_name = ".".join([package_root, *relative_parts])
    current_name = package_root
    current_path = Path(record.install_path).resolve()
    for part in relative_parts[:-1]:
        if current_name not in sys.modules:
            package = ModuleType(current_name)
            package.__path__ = [str(current_path)]  # type: ignore[attr-defined]
            package.__package__ = current_name
            sys.modules[current_name] = package
        current_name = f"{current_name}.{part}"
        current_path = current_path / part
    if current_name not in sys.modules:
        package = ModuleType(current_name)
        package.__path__ = [str(current_path)]  # type: ignore[attr-defined]
        package.__package__ = current_name
        sys.modules[current_name] = package
    spec = importlib_util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {path.name}")
    module = importlib_util.module_from_spec(spec)
    backend_dir = str(path.parent)
    inserted = backend_dir not in sys.path
    if inserted:
        sys.path.insert(0, backend_dir)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        if inserted and backend_dir in sys.path:
            sys.path.remove(backend_dir)
    module.__dict__.setdefault("__neo_extension_root__", Path(record.install_path).resolve())
    module.__dict__.setdefault("__neo_extension_id__", record.manifest.id)
    _MODULE_CACHE[cache_key] = module
    return module


def _router_conflicts(app: FastAPI, router: APIRouter) -> list[str]:
    existing: set[tuple[str, str]] = set()
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        for method in getattr(route, "methods", set()) or set():
            existing.add((path, str(method).upper()))
    conflicts: list[str] = []
    for route in router.routes:
        path = str(getattr(route, "path", ""))
        for method in getattr(route, "methods", set()) or set():
            if (path, str(method).upper()) in existing:
                conflicts.append(f"{str(method).upper()} {path}")
    return sorted(set(conflicts))


def _validate_router(record: Any, app: FastAPI, router: Any) -> APIRouter:
    if not isinstance(router, APIRouter):
        raise TypeError("create_extension_router(services) must return a FastAPI APIRouter.")
    allowed_prefix = f"/api/extensions/{record.manifest.id}"
    reserved_segments = {"asset", "ui-contract", "runtime-permissions"}
    invalid_paths: list[str] = []
    for route in router.routes:
        path = str(getattr(route, "path", ""))
        if not path.startswith(f"{allowed_prefix}/"):
            invalid_paths.append(path)
            continue
        first_segment = path[len(allowed_prefix):].lstrip("/").split("/", 1)[0]
        if first_segment in reserved_segments:
            invalid_paths.append(path)
    if invalid_paths:
        raise ValueError(f"External routes must stay in the unreserved namespace below {allowed_prefix}/: {', '.join(invalid_paths)}")
    conflicts = _router_conflicts(app, router)
    if conflicts:
        raise ValueError(f"External routes conflict with existing Neo routes: {', '.join(conflicts)}")
    return router


def register_external_extension_routes(app: FastAPI, services: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Load approved external backend routers during application startup.

    Backend approvals are restart-bound by design. Approving a package while Neo
    is running updates Admin state, but the Python entrypoint is not imported until
    the next application start.
    """
    statuses: list[dict[str, Any]] = []
    for record in get_extension_records():
        _key, entrypoint = _entrypoint_value(record, RUNTIME_ENTRYPOINT_KEYS)
        if record.origin != "external" or not entrypoint:
            continue
        status = {
            "extension_id": record.manifest.id,
            "version": record.manifest.version,
            "entrypoint": entrypoint,
            "contract_version": EXTERNAL_RUNTIME_CONTRACT_VERSION,
            "loaded": False,
        }
        if not record.enabled:
            status["status"] = "disabled"
            statuses.append(status)
            continue
        if not extension_runtime_permission_granted(record.manifest.id, "backend_routes"):
            status["status"] = "approval_required"
            statuses.append(status)
            continue
        try:
            module = _load_entrypoint_module(record, RUNTIME_ENTRYPOINT_KEYS)
            factory = getattr(module, "create_extension_router", None)
            if not callable(factory):
                raise AttributeError("Runtime entrypoint must expose create_extension_router(services).")
            scoped_services = dict(services or {})
            if not extension_runtime_permission_granted(record.manifest.id, "result_write"):
                scoped_services.pop("result_persister", None)
            scoped_services.update({
                "extension_id": record.manifest.id,
                "extension_version": record.manifest.version,
                "extension_root": Path(record.install_path).resolve(),
                "runtime_contract_version": EXTERNAL_RUNTIME_CONTRACT_VERSION,
                "granted_permissions": [
                    permission
                    for permission in ("backend_routes", "result_write")
                    if extension_runtime_permission_granted(record.manifest.id, permission)
                ],
            })
            router = _validate_router(record, app, factory(scoped_services))
            app.include_router(router)
            status.update({"loaded": True, "status": "loaded", "route_count": len(router.routes)})
        except Exception as exc:  # noqa: BLE001 - keep one bad extension from blocking Neo startup.
            status.update({"status": "load_failed", "error": _safe_error(exc, record)})
        statuses.append(status)
    _RUNTIME_STATUS[:] = statuses
    return list(statuses)


def external_extension_runtime_status() -> dict[str, Any]:
    return {
        "schema_version": "neo.extension.runtime.status.v1",
        "contract_version": EXTERNAL_RUNTIME_CONTRACT_VERSION,
        "restart_bound_permissions": ["backend_routes", "workflow_patch", "result_write"],
        "extensions": list(_RUNTIME_STATUS),
    }


def _extension_blocks(extensions: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(extensions, dict):
        return {}
    candidate = extensions.get("extensions") if isinstance(extensions.get("extensions"), dict) else extensions
    return {str(key): value for key, value in candidate.items() if isinstance(value, dict)}


def has_external_workflow_patch_request(extensions: Any) -> bool:
    for extension_id, block in _extension_blocks(extensions).items():
        if not block.get("enabled"):
            continue
        record = get_extension(extension_id)
        if record is None or record.origin != "external":
            continue
        _key, entrypoint = _entrypoint_value(record, WORKFLOW_ENTRYPOINT_KEYS)
        if entrypoint:
            return True
    return False


def apply_external_workflow_patches(
    workflow: dict[str, Any],
    *,
    extensions: Any,
    route: dict[str, Any],
    available_nodes: Any,
    model_ref: Any = None,
    clip_ref: Any = None,
) -> dict[str, Any]:
    """Apply approved generic external late-finish graph adapters in stable order."""
    blocks = _extension_blocks(extensions)
    candidates: list[tuple[int, str, Any, dict[str, Any]]] = []
    for extension_id, block in blocks.items():
        if not block.get("enabled"):
            continue
        record = get_extension(extension_id)
        if record is None or record.origin != "external" or not record.enabled:
            continue
        _key, entrypoint = _entrypoint_value(record, WORKFLOW_ENTRYPOINT_KEYS)
        if entrypoint:
            candidates.append((int(record.manifest.runtime.workflow_priority), extension_id, record, block))
    candidates.sort(key=lambda item: (item[0], item[1]))

    graph = workflow
    patches: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    used: list[Any] = []
    replay_payloads: dict[str, Any] = {}
    assistant_summaries: dict[str, str] = {}
    memory_events: dict[str, Any] = {}

    for _priority, extension_id, record, block in candidates:
        if not extension_runtime_permission_granted(extension_id, "workflow_patch"):
            validation.append({
                "extension_id": extension_id,
                "ok": False,
                "blocked": True,
                "errors": ["external_workflow_patch_approval_required"],
                "warnings": [],
            })
            continue
        try:
            module = _load_entrypoint_module(record, WORKFLOW_ENTRYPOINT_KEYS)
            adapter = getattr(module, "apply_extension_workflow_patch", None)
            if not callable(adapter):
                raise AttributeError("Workflow entrypoint must expose apply_extension_workflow_patch(...).")
            result = adapter(
                graph,
                payload=block,
                route=route,
                available_nodes=available_nodes,
                context={
                    "extension_id": extension_id,
                    "extension_version": record.manifest.version,
                    "extension_root": Path(record.install_path).resolve(),
                    "model_ref": model_ref,
                    "clip_ref": clip_ref,
                    "runtime_contract_version": EXTERNAL_RUNTIME_CONTRACT_VERSION,
                },
            )
            if not isinstance(result, dict) or not isinstance(result.get("workflow"), dict):
                raise TypeError("External workflow adapter must return a mapping containing workflow.")
            graph = result["workflow"]
            model_ref = result.get("model_ref", model_ref)
            clip_ref = result.get("clip_ref", clip_ref)
            patch = result.get("workflow_patch") if isinstance(result.get("workflow_patch"), dict) else {}
            patch.setdefault("extension_id", extension_id)
            patches.append(patch)
            validation_result = result.get("validation") if isinstance(result.get("validation"), dict) else {}
            validation.extend(validation_result.get("validation") or [])
            payloads[extension_id] = result.get("payload") or validation_result.get("block") or block
            used.extend(result.get("used") or [])
            replay_payloads[extension_id] = result.get("replay_payload") or block
            if result.get("assistant_summary"):
                assistant_summaries[extension_id] = str(result["assistant_summary"])
            memory_events[extension_id] = result.get("memory_event") or {}
        except Exception as exc:  # noqa: BLE001 - validation should report an extension failure without crashing base compilation.
            validation.append({
                "extension_id": extension_id,
                "ok": False,
                "blocked": True,
                "errors": ["external_workflow_patch_failed", _safe_error(exc, record)],
                "warnings": [],
            })

    return {
        "workflow": graph,
        "model_ref": model_ref,
        "clip_ref": clip_ref,
        "workflow_patches": patches,
        "validation": validation,
        "payloads": payloads,
        "used": used,
        "replay_payloads": replay_payloads,
        "assistant_summaries": assistant_summaries,
        "memory_events": memory_events,
    }
