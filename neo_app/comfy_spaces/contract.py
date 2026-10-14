from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_from_dict, model_to_dict
from neo_app.comfy_spaces.schema import ComfySpaceManifest


class SpaceContractError(ValueError):
    """Raised when a Comfy Space package violates the declarative contract."""


def parse_space_manifest(payload: dict[str, Any]) -> ComfySpaceManifest:
    if not isinstance(payload, dict):
        raise SpaceContractError("Comfy Space manifest must be a JSON object")
    try:
        return model_from_dict(ComfySpaceManifest, payload)
    except Exception as exc:  # Pydantic error text is useful to callers/admin later.
        raise SpaceContractError(str(exc)) from exc


def load_space_manifest(path: Path | str) -> ComfySpaceManifest:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceContractError(f"Unable to read Comfy Space manifest: {manifest_path}") from exc
    return parse_space_manifest(payload)


def manifest_payload(manifest: ComfySpaceManifest) -> dict[str, Any]:
    return model_to_dict(manifest)


def workflow_sha256(path: Path | str) -> str:
    workflow_path = Path(path)
    digest = hashlib.sha256()
    try:
        with workflow_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SpaceContractError(f"Unable to read Comfy workflow: {workflow_path}") from exc
    return digest.hexdigest()


def validate_space_package(manifest_path: Path | str) -> dict[str, Any]:
    """Validate the NCS-1 package boundary without executing Comfy or extension code.

    The workflow is treated as opaque JSON data in this phase. NCS-2 will add
    graph/node analysis and live Comfy dependency discovery.
    """

    path = Path(manifest_path).resolve()
    manifest = load_space_manifest(path)
    package_root = path.parent
    workflow_path = (package_root / manifest.workflow.path).resolve()

    try:
        workflow_path.relative_to(package_root)
    except ValueError as exc:
        raise SpaceContractError("Workflow path escapes the Comfy Space package") from exc

    if not workflow_path.is_file():
        raise SpaceContractError(f"Workflow file is missing: {manifest.workflow.path}")

    try:
        workflow_payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceContractError(f"Workflow is not readable JSON: {manifest.workflow.path}") from exc
    if not isinstance(workflow_payload, dict):
        raise SpaceContractError("Comfy API workflow root must be a JSON object")

    actual_sha = workflow_sha256(workflow_path)
    expected_sha = manifest.workflow.sha256
    if expected_sha and expected_sha.lower() != actual_sha:
        raise SpaceContractError("Workflow SHA-256 does not match the manifest")

    return {
        "ok": True,
        "schema_version": manifest.schema_version,
        "space_id": manifest.space_id,
        "version": manifest.version,
        "status": manifest.status,
        "package_root": str(package_root),
        "workflow_path": str(workflow_path),
        "workflow_sha256": actual_sha,
        "input_count": len(manifest.inputs),
        "output_count": len(manifest.outputs),
        "custom_node_requirement_count": len(manifest.dependencies.custom_nodes),
        "model_requirement_count": len(manifest.dependencies.models),
        "declarative_only": manifest.trust.declarative_only,
        "neo_code_execution_allowed": False,
        "graph_analysis_deferred": True,
        "runtime_execution_deferred": True,
    }
