from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.schema import ComfySpaceManifest, StrictContractModel
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict

SPACE_STAGE_SCHEMA_VERSION = "neo.comfy_space.stages.v1"
StageRunMode = Literal["all", "stage", "continue"]
StageResourcePolicy = Literal["automatic", "keep_loaded", "free_memory", "unload_models"]
StageSkipBehavior = Literal["do_not_execute"]


class SpaceStageError(ValueError):
    """Raised when a Stage sidecar or execution request is invalid."""


class SpaceStageDefinition(StrictContractModel):
    stage_id: str
    label: str
    description: str = ""
    order: int = 100
    required_stage_ids: list[str] = Field(default_factory=list)
    target_output_ids: list[str] = Field(default_factory=list)
    intermediate_output_ids: list[str] = Field(default_factory=list)
    enabled_by_default: bool = True
    optional: bool = False
    can_run_alone: bool = False
    skip_behavior: StageSkipBehavior = "do_not_execute"
    resource_policy: StageResourcePolicy = "automatic"

    @field_validator("stage_id")
    @classmethod
    def validate_stage_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text or len(text) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in text):
            raise ValueError("stage_id must use lowercase letters, numbers, dot, underscore, or hyphen")
        return text

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("stage label must not be empty")
        return text

    @field_validator("required_stage_ids", "target_output_ids", "intermediate_output_ids")
    @classmethod
    def normalize_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            clean = str(item or "").strip().lower()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            result.append(clean)
        return result

    @model_validator(mode="after")
    def validate_stage(self):
        if self.stage_id in self.required_stage_ids:
            raise ValueError("a stage cannot require itself")
        if not self.target_output_ids:
            raise ValueError("a stage requires at least one target_output_id")
        if self.can_run_alone and self.required_stage_ids:
            raise ValueError("can_run_alone stages cannot require previous stages")
        return self


class SpaceStageSidecar(StrictContractModel):
    schema_version: Literal[SPACE_STAGE_SCHEMA_VERSION] = SPACE_STAGE_SCHEMA_VERSION
    kind: Literal["comfy_space_stages"] = "comfy_space_stages"
    space_id: str
    version: str
    execution_authority: Literal[False] = False
    stages: list[SpaceStageDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stages(self):
        by_id: dict[str, SpaceStageDefinition] = {}
        for stage in self.stages:
            if stage.stage_id in by_id:
                raise ValueError(f"duplicate stage_id: {stage.stage_id}")
            by_id[stage.stage_id] = stage
        for stage in self.stages:
            missing = [item for item in stage.required_stage_ids if item not in by_id]
            if missing:
                raise ValueError(f"stage '{stage.stage_id}' requires unknown stage(s): {', '.join(missing)}")

        # Fail closed on dependency cycles.
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(stage_id: str) -> None:
            if stage_id in visited:
                return
            if stage_id in visiting:
                raise ValueError(f"stage dependency cycle detected at: {stage_id}")
            visiting.add(stage_id)
            for parent in by_id[stage_id].required_stage_ids:
                visit(parent)
            visiting.remove(stage_id)
            visited.add(stage_id)
        for stage_id in by_id:
            visit(stage_id)
        return self


def parse_stage_sidecar(payload: Mapping[str, Any]) -> SpaceStageSidecar:
    if not isinstance(payload, Mapping):
        raise SpaceStageError("Comfy Space Stage sidecar must be a JSON object")
    try:
        return model_from_dict(SpaceStageSidecar, dict(payload))
    except Exception as exc:
        raise SpaceStageError(str(exc)) from exc


def stage_sidecar_payload(sidecar: SpaceStageSidecar) -> dict[str, Any]:
    return model_to_dict(sidecar)


def validate_stages_against_manifest(
    sidecar: SpaceStageSidecar | Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
) -> dict[str, Any]:
    from neo_app.comfy_spaces.contract import parse_space_manifest

    parsed = sidecar if isinstance(sidecar, SpaceStageSidecar) else parse_stage_sidecar(sidecar)
    manifest_model = manifest if isinstance(manifest, ComfySpaceManifest) else parse_space_manifest(dict(manifest))
    errors: list[dict[str, str]] = []
    if parsed.space_id != manifest_model.space_id:
        errors.append({"code": "stage_space_id_mismatch", "message": "Stage sidecar space_id does not match manifest"})
    if parsed.version != manifest_model.version:
        errors.append({"code": "stage_version_mismatch", "message": "Stage sidecar version does not match manifest"})
    output_ids = {item.output_id for item in manifest_model.outputs}
    for stage in parsed.stages:
        for output_id in [*stage.target_output_ids, *stage.intermediate_output_ids]:
            if output_id not in output_ids:
                errors.append({
                    "code": "unknown_stage_output",
                    "message": f"Stage '{stage.stage_id}' references unknown output_id '{output_id}'.",
                })
    return {"valid": not errors, "errors": errors, "stage_count": len(parsed.stages)}


def ordered_stage_ids(sidecar: SpaceStageSidecar) -> list[str]:
    by_id = {item.stage_id: item for item in sidecar.stages}
    indegree = {item.stage_id: len(item.required_stage_ids) for item in sidecar.stages}
    children: dict[str, set[str]] = {item.stage_id: set() for item in sidecar.stages}
    for item in sidecar.stages:
        for parent in item.required_stage_ids:
            children.setdefault(parent, set()).add(item.stage_id)
    ready = sorted((sid for sid, count in indegree.items() if count == 0), key=lambda sid: (by_id[sid].order, sid))
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for child in children.get(current, set()):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=lambda sid: (by_id[sid].order, sid))
    return result


def resolve_stage_execution(
    sidecar: SpaceStageSidecar,
    *,
    run_mode: str = "all",
    stage_id: str = "",
    enabled_stage_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    mode = str(run_mode or "all").strip().lower()
    if mode not in {"all", "stage", "continue"}:
        raise SpaceStageError(f"Unsupported stage run mode: {run_mode}")
    by_id = {item.stage_id: item for item in sidecar.stages}
    ordered = ordered_stage_ids(sidecar)

    requested_enabled = {str(item).strip().lower() for item in (enabled_stage_ids or []) if str(item).strip()}
    if mode == "all":
        selected = [sid for sid in ordered if (sid in requested_enabled if requested_enabled else by_id[sid].enabled_by_default or not by_id[sid].optional)]
        # Required stages are always pulled in if a selected stage depends on them.
        changed = True
        while changed:
            changed = False
            for sid in list(selected):
                for parent in by_id[sid].required_stage_ids:
                    if parent not in selected:
                        selected.append(parent)
                        changed = True
        selected = [sid for sid in ordered if sid in set(selected)]
    else:
        clean_stage = str(stage_id or "").strip().lower()
        if clean_stage not in by_id:
            raise SpaceStageError(f"Unknown stage_id: {clean_stage or '(empty)'}")
        stage = by_id[clean_stage]
        selected = [clean_stage]
        if mode == "stage" and stage.required_stage_ids and not stage.can_run_alone:
            # Run Stage is intentionally explicit: dependencies must be included in the execution slice.
            required: set[str] = set()
            def collect(sid: str) -> None:
                for parent in by_id[sid].required_stage_ids:
                    if parent not in required:
                        required.add(parent)
                        collect(parent)
            collect(clean_stage)
            selected = [sid for sid in ordered if sid in required or sid == clean_stage]

    targets: list[str] = []
    intermediates: list[str] = []
    for sid in selected:
        stage = by_id[sid]
        for output_id in stage.target_output_ids:
            if output_id not in targets:
                targets.append(output_id)
        for output_id in stage.intermediate_output_ids:
            if output_id not in intermediates:
                intermediates.append(output_id)
    resource_policy = "automatic"
    if len(selected) == 1:
        resource_policy = by_id[selected[0]].resource_policy
    return {
        "run_mode": mode,
        "requested_stage_id": str(stage_id or "").strip().lower(),
        "selected_stage_ids": selected,
        "target_output_ids": targets,
        "intermediate_output_ids": intermediates,
        "resource_policy": resource_policy,
    }


def prune_workflow_to_output_nodes(workflow: Mapping[str, Any], target_node_ids: Sequence[str]) -> dict[str, Any]:
    graph = {str(key): deepcopy(value) for key, value in workflow.items() if isinstance(value, Mapping)}
    known = set(graph)
    targets = [str(item) for item in target_node_ids if str(item) in known]
    if not targets:
        raise SpaceStageError("Stage execution has no valid target workflow nodes")

    def linked_nodes(value: Any) -> set[str]:
        result: set[str] = set()
        if isinstance(value, Mapping):
            for child in value.values():
                result.update(linked_nodes(child))
        elif isinstance(value, (list, tuple)):
            if len(value) == 2 and str(value[0]) in known and isinstance(value[1], int):
                result.add(str(value[0]))
            else:
                for child in value:
                    result.update(linked_nodes(child))
        return result

    keep: set[str] = set()
    stack = list(targets)
    while stack:
        node_id = stack.pop()
        if node_id in keep:
            continue
        keep.add(node_id)
        node = graph.get(node_id) or {}
        inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
        stack.extend(linked_nodes(inputs) - keep)
    return {node_id: graph[node_id] for node_id in graph if node_id in keep}
