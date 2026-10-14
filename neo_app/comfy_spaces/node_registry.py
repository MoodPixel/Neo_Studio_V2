"""Read-only per-Comfy-runtime node discovery snapshots for Space Builder.

The live ComfyUI ``/object_info`` response is the source of truth. Snapshots
are convenience diagnostics, never binding, import, or queue-time authority.
Only bounded metadata is retained; model-choice arrays and executable code are
not persisted. Sync never installs or imports custom-node Python packages.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from neo_app.comfy_spaces.paths import comfy_spaces_runtime_root, _safe_token, _SAFE_ID_RE
from neo_app.comfy_spaces.runtime_probe import SpaceRuntimeProbeError, probe_comfy_profile_object_info


NODE_REGISTRY_SCHEMA_VERSION = "neo.comfy_space.node_registry.v1"
_MAX_CLASSES = 12000
_MAX_CHECKS = 512


class NodeRegistryError(ValueError):
    """Invalid node-registry request or stored snapshot."""


class NodeRegistryUnavailable(RuntimeError):
    """Selected ComfyUI runtime could not provide a live node inventory."""


def _profile_id(value: Any) -> str:
    try:
        return _safe_token(str(value or ""), label="runtime_profile_id", pattern=_SAFE_ID_RE, lowercase=True)
    except ValueError as exc:
        raise NodeRegistryError(str(exc)) from exc


def _snapshot_path(profile_id: str, root_dir: Path | str | None = None) -> Path:
    return comfy_spaces_runtime_root(root_dir) / "node_registry" / f"{_profile_id(profile_id)}.json"


def _requested_classes(classes: Any) -> list[str]:
    if classes is None:
        return []
    if not isinstance(classes, list) or len(classes) > _MAX_CHECKS:
        raise NodeRegistryError(f"required_classes must be a list of up to {_MAX_CHECKS} node class names.")
    result: list[str] = []
    for item in classes:
        if not isinstance(item, str) or not item.strip() or len(item) > 256 or any(ord(c) < 32 for c in item):
            raise NodeRegistryError("required_classes contains an invalid node class name.")
        if item not in result:
            result.append(item)
    return result


def _read_snapshot(profile_id: str, root_dir: Path | str | None = None) -> dict[str, Any] | None:
    path = _snapshot_path(profile_id, root_dir)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 4_000_000:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != NODE_REGISTRY_SCHEMA_VERSION
            or data.get("runtime_profile_id") != _profile_id(profile_id)
            or not isinstance(data.get("node_classes"), list)
            or len(data["node_classes"]) > _MAX_CLASSES
            or any(not isinstance(item, str) or len(item) > 256 for item in data["node_classes"])
        ):
            return None
        return data
    except (OSError, ValueError, TypeError):
        # A stale/corrupt diagnostic cache must never block import or publishing.
        return None


def _structural_inputs(value: Any) -> dict[str, dict[str, str]]:
    """Keep widget/socket types, not large model-choice lists or local defaults."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for section in ("required", "optional", "hidden"):
        fields = value.get(section)
        if not isinstance(fields, dict):
            continue
        result[section] = {}
        for field, descriptor in fields.items():
            if not isinstance(field, str):
                continue
            first = descriptor[0] if isinstance(descriptor, list) and descriptor else descriptor
            if isinstance(first, str):
                kind = first[:128]
            elif isinstance(first, list):
                kind = "COMBO"  # Model catalog values intentionally omitted.
            else:
                kind = type(first).__name__
            result[section][field] = kind
    return result


def _check_required(classes: list[str], required: list[str]) -> dict[str, Any]:
    loaded = set(classes)
    missing = [item for item in required if item not in loaded]
    return {
        "requested_count": len(required),
        "available_classes": [item for item in required if item in loaded],
        "missing_classes": missing,
        "all_required_loaded": len(missing) == 0,
    }


def node_registry_status(
    runtime_profile_id: str, *, required_classes: Any = None, root_dir: Path | str | None = None
) -> dict[str, Any]:
    """Read last locally synchronized inventory; never claim live readiness."""
    profile_id = _profile_id(runtime_profile_id)
    required = _requested_classes(required_classes)
    saved = _read_snapshot(profile_id, root_dir)
    if not saved:
        return {
            "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
            "ok": True, "runtime_profile_id": profile_id,
            "snapshot_available": False, "live_checked": False,
            "status": "not_synced", "node_count": 0,
            "required": {"requested_count": len(required), "available_classes": [], "missing_classes": [], "all_required_loaded": False},
            "execution_authority": False,
        }
    return {
        "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
        "ok": True, "runtime_profile_id": profile_id,
        "snapshot_available": True, "live_checked": False,
        "status": "cached_not_live", "synced_at": saved.get("synced_at", ""),
        "node_count": saved.get("node_count", 0),
        "fingerprint": saved.get("fingerprint", ""),
        "required": _check_required(saved["node_classes"], required),
        "execution_authority": False,
    }


def refresh_node_registry(
    runtime_profile_id: str, *, required_classes: Any = None, root_dir: Path | str | None = None
) -> dict[str, Any]:
    """Query exactly one selected Comfy runtime and persist bounded discovery metadata.

    Import/preflight still queries live object_info; this registry is never a
    fallback schema source and cannot make unloaded custom nodes available.
    """
    profile_id = _profile_id(runtime_profile_id)
    required = _requested_classes(required_classes)
    try:
        object_info, probe = probe_comfy_profile_object_info(profile_id)
    except SpaceRuntimeProbeError as exc:
        raise NodeRegistryUnavailable(str(exc)) from exc
    if not isinstance(object_info, Mapping) or not object_info:
        raise NodeRegistryUnavailable(f"ComfyUI profile '{profile_id}' returned no loaded node definitions.")
    if len(object_info) > _MAX_CLASSES:
        raise NodeRegistryUnavailable("ComfyUI returned too many node definitions to safely inventory.")

    classes = sorted(key for key, info in object_info.items() if isinstance(key, str) and isinstance(info, dict) and len(key) <= 256)
    # A node signature is metadata only. Do not retain huge model-choice arrays,
    # absolute paths, importable code, or Comfy credentials in the snapshot.
    digest = hashlib.sha256()
    for node_class in classes:
        info = object_info[node_class]
        digest.update(node_class.encode("utf-8")); digest.update(b"\0")
        for key in ("input", "output", "output_name", "output_is_list", "category"):
            value = info.get(key)
            if key == "input":
                value = _structural_inputs(value)
            digest.update(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")); digest.update(b"\0")

    old = _read_snapshot(profile_id, root_dir)
    before = set(old.get("node_classes", [])) if old else set()
    current = set(classes)
    added = sorted(current - before) if old else []
    removed = sorted(before - current) if old else []
    snapshot = {
        "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
        "runtime_profile_id": profile_id,
        "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "node_count": len(classes),
        "node_classes": classes,
        "fingerprint": digest.hexdigest(),
        "source": "comfyui_live_object_info",
        "execution_authority": False,
    }
    target = _snapshot_path(profile_id, root_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
        "ok": True, "runtime_profile_id": profile_id,
        "snapshot_available": True, "live_checked": True,
        "status": "synced_live", "synced_at": snapshot["synced_at"],
        "node_count": len(classes), "fingerprint": snapshot["fingerprint"],
        "new_classes": added, "removed_classes": removed,
        "required": _check_required(classes, required),
        "runtime_reachable": bool(probe.get("reachable")),
        "source": "comfyui_live_object_info",
        "execution_authority": False,
        "note": "This is discovery evidence only. Import and execution revalidate against live ComfyUI schemas.",
    }
