from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator

from neo_app.comfy_spaces.graph_addons import build_graph_addon_catalog
from neo_app.comfy_spaces.graph_analyzer import analyze_comfy_workflow
from neo_app.comfy_spaces.paths import comfy_space_dependency_health_path, comfy_space_dependency_health_version_root
from neo_app.comfy_spaces.runtime_probe import SpaceRuntimeProbeError, probe_comfy_profile_object_info
from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.comfy_spaces.stages import prune_workflow_to_output_nodes
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION = "neo.comfy_space.dependency_health.v1"
SPACE_DEPENDENCY_HEALTH_COLLECTION_VERSION = "neo.comfy_space.dependency_health_collection.v1"
DEFAULT_HEALTH_TTL_SECONDS = 15 * 60
HealthStatus = Literal["ready", "blocked", "degraded", "unreachable", "not_checked"]


class SpaceDependencyHealthError(RuntimeError):
    """Raised when dependency health cannot be safely read or refreshed."""

    def __init__(self, message: str, *, code: str = "dependency_health_error") -> None:
        super().__init__(message)
        self.code = str(code or "dependency_health_error")


class ComfySpaceDependencyHealthSnapshot(StrictContractModel):
    schema_version: Literal[SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION] = SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION
    kind: Literal["comfy_space_dependency_health"] = "comfy_space_dependency_health"
    space_id: str
    version: str
    runtime_profile_id: str
    checked_at: str
    status: HealthStatus
    ready: bool = False
    stale: bool = False
    stale_reason: str = ""
    age_seconds: float = Field(default=0.0, ge=0.0)
    runtime_inventory: dict[str, Any] = Field(default_factory=dict)
    base: dict[str, Any] = Field(default_factory=dict)
    addons: dict[str, Any] = Field(default_factory=dict)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    package_hints: list[dict[str, Any]] = Field(default_factory=list)
    runtime_changed_since_previous: bool = False
    previous_runtime_inventory_fingerprint: str = ""
    queue_time_recheck_required: Literal[True] = True
    automatic_dependency_install: Literal[False] = False

    @field_validator("space_id", "runtime_profile_id")
    @classmethod
    def non_empty_ids(cls, value: str, info) -> str:
        text = str(value or "").strip().lower() if info.field_name == "space_id" else str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text


class SpaceDependencyHealthCollection(StrictContractModel):
    schema_version: Literal[SPACE_DEPENDENCY_HEALTH_COLLECTION_VERSION] = SPACE_DEPENDENCY_HEALTH_COLLECTION_VERSION
    ok: Literal[True] = True
    space_id: str
    version: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _canonical_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _input_specs(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = _mapping(node.get("input"))
    rows: list[dict[str, Any]] = []
    for bucket in ("required", "optional"):
        for field, raw in _mapping(root.get(bucket)).items():
            type_spec: Any = raw
            options: dict[str, Any] = {}
            if isinstance(raw, (list, tuple)) and raw:
                type_spec = raw[0]
                if len(raw) > 1 and isinstance(raw[1], Mapping):
                    options = dict(raw[1])
            combo_spec = isinstance(type_spec, (list, tuple))
            choices = list(type_spec) if combo_spec else []
            type_name = "COMBO" if combo_spec else str(type_spec or "")
            rows.append({"field": str(field), "type": type_name, "choices": choices, "options": options, "required": bucket == "required"})
    return rows


def _looks_like_model_field(field: str) -> bool:
    name = str(field or "").casefold()
    tokens = ("ckpt", "checkpoint", "unet", "vae", "clip", "lora", "control_net", "controlnet", "model_name", "diffusion_model", "text_encoder")
    return any(token in name for token in tokens)


def build_runtime_inventory(object_info: Mapping[str, Any]) -> dict[str, Any]:
    info = _mapping(object_info)
    node_schema_rows: list[dict[str, Any]] = []
    model_catalogs: list[dict[str, Any]] = []
    package_rows: list[dict[str, str]] = []
    for class_type in sorted(str(key) for key in info):
        node = _mapping(info.get(class_type))
        inputs = _input_specs(node)
        outputs = [str(item or "") for item in (node.get("output") if isinstance(node.get("output"), (list, tuple)) else [])]
        node_schema_rows.append({
            "class_type": class_type,
            "inputs": [{"field": row["field"], "type": row["type"], "required": row["required"]} for row in inputs],
            "outputs": outputs,
        })
        for row in inputs:
            if row["type"] == "COMBO" and _looks_like_model_field(row["field"]):
                choices = [str(item) for item in row["choices"]]
                model_catalogs.append({
                    "class_type": class_type,
                    "field": row["field"],
                    "count": len(choices),
                    "fingerprint": _canonical_hash(choices),
                })
        module = str(node.get("python_module") or node.get("module") or "").strip()
        category = str(node.get("category") or "").strip()
        if module:
            package_rows.append({"class_type": class_type, "python_module": module, "category": category})

    node_fingerprint = _canonical_hash(node_schema_rows)
    model_catalog_fingerprint = _canonical_hash(model_catalogs)
    fingerprint = _canonical_hash({"node": node_fingerprint, "models": model_catalog_fingerprint})
    return {
        "fingerprint": fingerprint,
        "node_schema_fingerprint": node_fingerprint,
        "model_catalog_fingerprint": model_catalog_fingerprint,
        "node_count": len(info),
        "model_catalog_count": len(model_catalogs),
        "model_choice_count": sum(int(item["count"]) for item in model_catalogs),
        "node_classes": sorted(str(key) for key in info),
        "model_catalogs": model_catalogs,
        "package_modules": package_rows,
    }


def _dependency_summary(analysis: Mapping[str, Any]) -> dict[str, Any]:
    deps = _mapping(analysis.get("dependencies"))
    missing_models = [dict(item) for item in deps.get("missing_models") or [] if isinstance(item, Mapping)]
    unresolved_models = [dict(item) for item in deps.get("unresolved_models") or [] if isinstance(item, Mapping)]
    missing_nodes = [str(item) for item in deps.get("missing_node_classes") or []]
    state = str(deps.get("preflight_state") or "not_checked")
    status: HealthStatus = "ready" if state == "ready" else "blocked" if state == "blocked" else "degraded"
    return {
        "status": status,
        "ready": status == "ready",
        "preflight_state": state,
        "required_node_classes": [str(item) for item in deps.get("required_node_classes") or []],
        "missing_node_classes": missing_nodes,
        "model_requirements": [dict(item) for item in deps.get("model_requirements") or [] if isinstance(item, Mapping)],
        "missing_models": missing_models,
        "unresolved_models": unresolved_models,
        "node_requirements": [dict(item) for item in deps.get("node_requirements") or [] if isinstance(item, Mapping)],
    }


def _package_hints(manifest: Any, base: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_class: dict[str, dict[str, Any]] = {}
    for item in getattr(getattr(manifest, "dependencies", None), "custom_nodes", []) or []:
        by_class[str(item.class_type)] = {
            "class_type": str(item.class_type),
            "package": str(item.package or ""),
            "version_spec": str(item.version_spec or ""),
            "required": bool(item.required),
            "source": "published_manifest",
        }
    for item in base.get("node_requirements") or []:
        if not isinstance(item, Mapping):
            continue
        class_type = str(item.get("class_type") or "")
        if not class_type:
            continue
        row = by_class.setdefault(class_type, {"class_type": class_type, "package": "", "version_spec": "", "required": True, "source": "runtime_object_info"})
        if not row.get("package"):
            row["package"] = str(item.get("package_hint") or item.get("python_module") or "")
        if row.get("source") != "published_manifest":
            row["source"] = "runtime_object_info"
    return [by_class[key] for key in sorted(by_class)]


def _stage_health(manifest: Any, workflow: Mapping[str, Any], stages: Any, object_info: Mapping[str, Any], runtime_profile_id: str, runtime_probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    if stages is None:
        return []
    output_nodes = {str(item.output_id): str(item.binding.node_id) for item in manifest.outputs}
    rows: list[dict[str, Any]] = []
    for stage in stages.stages:
        target_nodes = [output_nodes[item] for item in stage.target_output_ids if item in output_nodes]
        try:
            sliced = prune_workflow_to_output_nodes(workflow, target_nodes)
            analysis = analyze_comfy_workflow(sliced, object_info=object_info, runtime_profile_id=runtime_profile_id, runtime_probe=runtime_probe)
            dep = _dependency_summary(analysis)
            rows.append({
                "stage_id": stage.stage_id,
                "label": stage.label,
                "status": dep["status"],
                "ready": dep["ready"],
                "node_count": len(sliced),
                "target_output_ids": list(stage.target_output_ids),
                "missing_node_classes": dep["missing_node_classes"],
                "missing_models": dep["missing_models"],
                "unresolved_models": dep["unresolved_models"],
            })
        except Exception as exc:  # fail closed into health evidence; queue-time authority still rechecks.
            rows.append({
                "stage_id": stage.stage_id,
                "label": stage.label,
                "status": "blocked",
                "ready": False,
                "node_count": 0,
                "target_output_ids": list(stage.target_output_ids),
                "missing_node_classes": [],
                "missing_models": [],
                "unresolved_models": [],
                "error": str(exc),
            })
    return rows


def _addon_health(space_id: str, version: str, runtime_profile_id: str, graph_anchors: Any, object_info: Mapping[str, Any]) -> dict[str, Any]:
    catalog = build_graph_addon_catalog(
        space_id=space_id,
        version=version,
        runtime_profile_id=runtime_profile_id,
        graph_anchors=graph_anchors,
        object_info=object_info,
    )
    modules = []
    for item in catalog.get("modules") or []:
        row = dict(item)
        modules.append({
            "module_id": str(row.get("module_id") or ""),
            "version": str(row.get("version") or ""),
            "label": str(row.get("label") or ""),
            "ready": bool(row.get("compatible")),
            "status": "ready" if row.get("compatible") else "blocked",
            "message": str(row.get("compatibility_message") or ""),
            "runtime_node_classes": [str(value) for value in row.get("runtime_node_classes") or []],
            "required_anchor_roles": [str(value) for value in row.get("required_anchor_roles") or []],
        })
    return {
        "confirmed_anchor_count": int(catalog.get("confirmed_anchor_count") or 0),
        "ready_module_count": int(catalog.get("ready_module_count") or 0),
        "ready_raw_node_count": int(catalog.get("ready_raw_node_count") or 0),
        "modules": modules,
    }


def _write_snapshot(snapshot: ComfySpaceDependencyHealthSnapshot, *, root_dir: Path | str | None = None) -> None:
    path = comfy_space_dependency_health_path(snapshot.space_id, snapshot.version, snapshot.runtime_profile_id, root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(model_to_dict(snapshot), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _read_snapshot(space_id: str, version: str, runtime_profile_id: str, *, root_dir: Path | str | None = None) -> ComfySpaceDependencyHealthSnapshot | None:
    path = comfy_space_dependency_health_path(space_id, version, runtime_profile_id, root_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return model_from_dict(ComfySpaceDependencyHealthSnapshot, payload)
    except Exception as exc:
        raise SpaceDependencyHealthError(f"Stored dependency health snapshot is invalid: {exc}", code="dependency_health_snapshot_invalid") from exc


def _with_staleness(snapshot: ComfySpaceDependencyHealthSnapshot, *, ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS) -> dict[str, Any]:
    payload = model_to_dict(snapshot)
    checked = _parse_time(snapshot.checked_at)
    age = max(0.0, (datetime.now(timezone.utc) - checked).total_seconds()) if checked else float(ttl_seconds + 1)
    stale = age > max(1, int(ttl_seconds))
    payload["age_seconds"] = round(age, 3)
    payload["stale"] = stale
    payload["stale_reason"] = "Health evidence is older than the refresh window." if stale else ""
    if stale and payload.get("status") == "ready":
        payload["status"] = "degraded"
        payload["ready"] = False
    return payload


def refresh_space_runtime_health(space_id: str, version: str, *, runtime_profile_id: str, root_dir: Path | str | None = None) -> dict[str, Any]:
    from neo_app.comfy_spaces.execution_runtime import _load_published_space_package

    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    clean_runtime = str(runtime_profile_id or "").strip()
    if not clean_space or not clean_version or not clean_runtime:
        raise SpaceDependencyHealthError("space_id, version, and runtime_profile_id are required", code="dependency_health_request_invalid")
    try:
        package = _load_published_space_package(clean_space, clean_version, root_dir=root_dir)
    except Exception as exc:
        raise SpaceDependencyHealthError(str(exc), code="dependency_health_space_invalid") from exc

    previous = _read_snapshot(clean_space, clean_version, clean_runtime, root_dir=root_dir)
    try:
        object_info, probe = probe_comfy_profile_object_info(clean_runtime)
    except SpaceRuntimeProbeError as exc:
        snapshot = ComfySpaceDependencyHealthSnapshot(
            space_id=clean_space,
            version=clean_version,
            runtime_profile_id=clean_runtime,
            checked_at=_now_iso(),
            status="unreachable",
            ready=False,
            stale=False,
            stale_reason="",
            runtime_inventory={},
            base={"status": "unreachable", "ready": False, "error": str(exc)},
            addons={"modules": [], "ready_module_count": 0, "ready_raw_node_count": 0},
            stages=[],
            package_hints=[],
            runtime_changed_since_previous=False,
            previous_runtime_inventory_fingerprint=str(_mapping(getattr(previous, "runtime_inventory", {})).get("fingerprint") or "") if previous else "",
        )
        _write_snapshot(snapshot, root_dir=root_dir)
        return _with_staleness(snapshot)

    inventory = build_runtime_inventory(object_info)
    manifest = package["manifest"]
    workflow = package["workflow"]
    analysis = analyze_comfy_workflow(workflow, object_info=object_info, runtime_profile_id=clean_runtime, runtime_probe=probe)
    base = _dependency_summary(analysis)
    addons = _addon_health(clean_space, clean_version, clean_runtime, package.get("graph_anchors"), object_info)
    stages = _stage_health(manifest, workflow, package.get("stages"), object_info, clean_runtime, probe)
    hints = _package_hints(manifest, base)

    status: HealthStatus = "ready" if base["ready"] else "blocked" if base["status"] == "blocked" else "degraded"
    previous_fp = str(_mapping(getattr(previous, "runtime_inventory", {})).get("fingerprint") or "") if previous else ""
    changed = bool(previous_fp and previous_fp != inventory["fingerprint"])
    snapshot = ComfySpaceDependencyHealthSnapshot(
        space_id=clean_space,
        version=clean_version,
        runtime_profile_id=clean_runtime,
        checked_at=_now_iso(),
        status=status,
        ready=status == "ready",
        stale=False,
        runtime_inventory=inventory,
        base=base,
        addons=addons,
        stages=stages,
        package_hints=hints,
        runtime_changed_since_previous=changed,
        previous_runtime_inventory_fingerprint=previous_fp,
    )
    _write_snapshot(snapshot, root_dir=root_dir)
    return _with_staleness(snapshot)


def _not_checked_snapshot(space_id: str, version: str, runtime_profile_id: str, *, reason: str, snapshot_error: str = "") -> dict[str, Any]:
    return {
            "schema_version": SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION,
            "kind": "comfy_space_dependency_health",
            "space_id": str(space_id or "").strip().lower(),
            "version": str(version or "").strip(),
            "runtime_profile_id": str(runtime_profile_id or "").strip(),
            "checked_at": "",
            "status": "not_checked",
            "ready": False,
            "stale": True,
            "stale_reason": reason,
            "age_seconds": 0.0,
            "runtime_inventory": {},
            "base": {"status": "not_checked", "ready": False},
            "addons": {"modules": [], "ready_module_count": 0, "ready_raw_node_count": 0},
            "stages": [],
            "package_hints": [],
            "runtime_changed_since_previous": False,
            "previous_runtime_inventory_fingerprint": "",
            "queue_time_recheck_required": True,
            "automatic_dependency_install": False,
            **({"snapshot_error": snapshot_error} if snapshot_error else {}),
        }


def load_space_runtime_health(space_id: str, version: str, *, runtime_profile_id: str, root_dir: Path | str | None = None, ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    clean_runtime = str(runtime_profile_id or "").strip().lower()
    try:
        snapshot = _read_snapshot(clean_space, clean_version, clean_runtime, root_dir=root_dir)
    except SpaceDependencyHealthError as exc:
        # Persistent health is advisory evidence, never execution authority. A corrupt
        # cache entry must not make an otherwise valid published Space unloadable.
        return _not_checked_snapshot(clean_space, clean_version, clean_runtime, reason="Stored dependency health evidence is invalid. Refresh Health to rebuild it.", snapshot_error=str(exc))
    if snapshot is None:
        return _not_checked_snapshot(clean_space, clean_version, clean_runtime, reason="No persistent compatibility snapshot exists for this Space/runtime yet.")
    return _with_staleness(snapshot, ttl_seconds=ttl_seconds)



def list_space_runtime_health(space_id: str, version: str, *, root_dir: Path | str | None = None, runtime_profile_ids: list[str] | None = None, ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS) -> dict[str, Any]:
    ids = [str(item) for item in (runtime_profile_ids or []) if str(item).strip()]
    if not ids:
        base = comfy_space_dependency_health_version_root(space_id, version, root_dir)
        if base.is_dir():
            ids = [item.stem for item in base.glob("*.json") if item.is_file()]
    items = [load_space_runtime_health(space_id, version, runtime_profile_id=item, root_dir=root_dir, ttl_seconds=ttl_seconds) for item in sorted(set(ids))]
    return model_to_dict(SpaceDependencyHealthCollection(space_id=str(space_id or "").strip().lower(), version=str(version or "").strip(), items=items, count=len(items)))


def dependency_health_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION,
        "persistent_runtime_compatibility_snapshots": True,
        "runtime_inventory_fingerprints": True,
        "node_package_hints": True,
        "model_catalog_health": True,
        "stage_specific_health": True,
        "graph_addon_readiness": True,
        "stale_by_age": True,
        "changed_runtime_detection": True,
        "queue_time_live_recheck_required": True,
        "automatic_dependency_install": False,
        "default_ttl_seconds": DEFAULT_HEALTH_TTL_SECONDS,
    }
