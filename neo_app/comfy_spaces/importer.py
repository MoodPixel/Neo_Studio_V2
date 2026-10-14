from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from neo_app.comfy_spaces.graph_analyzer import (
    SPACE_ANALYSIS_SCHEMA_VERSION,
    SpaceWorkflowAnalysisError,
    analyze_comfy_workflow,
    normalize_comfy_workflow,
)
from neo_app.comfy_spaces.paths import comfy_space_import_root
from neo_app.comfy_spaces.runtime_probe import probe_comfy_profile_object_info


SPACE_IMPORT_RECORD_SCHEMA_VERSION = "neo.comfy_space.import.v1"


class SpaceWorkflowImportError(ValueError):
    """Raised when a workflow cannot be safely staged as a Comfy Space import."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def load_workflow_json(path: Path | str) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceWorkflowImportError(f"Unable to read Comfy workflow JSON: {source.name}") from exc


def import_comfy_workflow_payload(
    payload: Any,
    *,
    source_name: str = "workflow.json",
    root_dir: Path | str | None = None,
    import_id: str | None = None,
    object_info: Mapping[str, Any] | None = None,
    runtime_profile_id: str = "",
    runtime_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage a normal ComfyUI Save workflow or API workflow plus analysis.

    Source JSON is preserved for audit/debugging; ``workflow.json`` contains the
    canonical direct API graph used by later Neo contracts. Normal editor
    subgraphs are expanded only in that execution copy while source/subgraph
    lineage remains in analysis metadata. No Space manifest is generated and
    nothing is executed or published during import.
    """

    try:
        canonical_graph, normalization = normalize_comfy_workflow(payload, object_info=object_info)
        # Analyze the original editor source, not only its normalized API graph:
        # Comfy canvas groups and subgraph presentation are authoring metadata
        # that would otherwise be lost in the canonical execution-only graph.
        analysis = analyze_comfy_workflow(
            payload,
            object_info=object_info,
            runtime_profile_id=runtime_profile_id,
            runtime_probe=runtime_probe,
        )
    except SpaceWorkflowAnalysisError as exc:
        raise SpaceWorkflowImportError(str(exc)) from exc

    clean_import_id = str(import_id or uuid4().hex).strip().lower()
    try:
        import_root = comfy_space_import_root(clean_import_id, root_dir)
    except ValueError as exc:
        raise SpaceWorkflowImportError(str(exc)) from exc
    if import_root.exists() and any(import_root.iterdir()):
        raise SpaceWorkflowImportError(f"Comfy Space import id already exists: {clean_import_id}")
    import_root.mkdir(parents=True, exist_ok=True)

    original = deepcopy(payload)
    source_filename = str(source_name or "workflow.json").replace("\\", "/").split("/")[-1].strip() or "workflow.json"
    source_path = import_root / "source_workflow.json"
    workflow_path = import_root / "workflow.json"
    analysis_path = import_root / "analysis.json"
    record_path = import_root / "import_record.json"

    _write_json(source_path, original)
    _write_json(workflow_path, canonical_graph)
    _write_json(analysis_path, analysis)

    record = {
        "schema_version": SPACE_IMPORT_RECORD_SCHEMA_VERSION,
        "import_id": clean_import_id,
        "created_at": _now_iso(),
        "source_name": source_filename,
        "source_shape": normalization["source_shape"],
        "source_format": "comfy_save" if normalization.get("converted_from_editor") else "comfy_api",
        "converted_from_editor": bool(normalization.get("converted_from_editor")),
        "normalization": deepcopy(normalization),
        "source_sha256": _json_sha256(original),
        "canonical_workflow_sha256": _json_sha256(canonical_graph),
        "analysis_schema_version": SPACE_ANALYSIS_SCHEMA_VERSION,
        "runtime_profile_id": str(runtime_profile_id or ""),
        "object_info_used": bool(object_info),
        "execution_performed": False,
        "published": False,
        "manifest_created": False,
        "files": {
            "source_workflow": "source_workflow.json",
            "workflow": "workflow.json",
            "analysis": "analysis.json",
        },
    }
    _write_json(record_path, record)

    return {
        "ok": analysis["ok"],
        "import_id": clean_import_id,
        "import_root": str(import_root),
        "workflow_path": str(workflow_path),
        "analysis_path": str(analysis_path),
        "record_path": str(record_path),
        "record": record,
        "normalization": deepcopy(normalization),
        "analysis": analysis,
    }


def import_comfy_workflow_file(
    source_path: Path | str,
    *,
    root_dir: Path | str | None = None,
    import_id: str | None = None,
    runtime_profile_id: str = "",
    use_live_object_info: bool = False,
) -> dict[str, Any]:
    source = Path(source_path)
    payload = load_workflow_json(source)
    object_info: dict[str, Any] | None = None
    runtime_probe: dict[str, Any] | None = None
    clean_runtime_profile_id = str(runtime_profile_id or "").strip()
    if use_live_object_info and not clean_runtime_profile_id:
        raise SpaceWorkflowImportError("runtime_profile_id is required when live object_info analysis is requested.")
    # NCS-5E.1: when a runtime was selected, use its read-only Comfy node schema
    # automatically. This is the reliable path for normal Save workflows and
    # custom-node positional widgets, and avoids a separate expert-only toggle.
    if clean_runtime_profile_id:
        try:
            object_info, runtime_probe = probe_comfy_profile_object_info(clean_runtime_profile_id)
        except Exception as exc:  # noqa: BLE001
            raise SpaceWorkflowImportError(str(exc)) from exc
    return import_comfy_workflow_payload(
        payload,
        source_name=source.name,
        root_dir=root_dir,
        import_id=import_id,
        object_info=object_info,
        runtime_profile_id=clean_runtime_profile_id,
        runtime_probe=runtime_probe,
    )
