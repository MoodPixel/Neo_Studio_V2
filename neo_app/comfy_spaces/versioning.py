from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.builder_schema import (
    ComfySpaceBuilderDraft,
    SpaceBuilderAnchorDraft,
    SpaceBuilderInputDraft,
    SpaceBuilderOutputDraft,
    SpaceBuilderSectionDraft,
    SpaceBuilderStageDraft,
)
from neo_app.comfy_spaces.builder_service import (
    SpaceBuilderError,
    create_builder_draft_from_import,
    load_builder_draft,
    save_builder_draft,
)
from neo_app.comfy_spaces.contract import load_space_manifest
from neo_app.comfy_spaces.graph_addons import SpaceGraphAddonRequest
from neo_app.comfy_spaces.paths import (
    SPACE_MIGRATION_FILENAME,
    comfy_space_draft_replacement_plan_path,
    comfy_space_graph_anchors_path,
    comfy_space_import_root,
    comfy_space_manifest_path,
    comfy_space_migration_path,
    comfy_space_publication_receipt_path,
    comfy_space_root,
    comfy_space_stages_path,
    comfy_space_ui_path,
    comfy_space_variant_path,
    comfy_space_version_root,
    comfy_space_version_state_path,
    comfy_space_workflow_path,
    comfy_spaces_variants_root,
)
from neo_app.comfy_spaces.publish_schema import ComfySpacePublicationReceipt
from neo_app.comfy_spaces.schema import StrictContractModel
from neo_app.comfy_spaces.space_ui import load_space_ui
from neo_app.comfy_spaces.stages import parse_stage_sidecar
from neo_app.comfy_spaces.dynamic_controls import parse_dynamic_controls
from neo_app.comfy_spaces.graph_anchors import parse_graph_anchor_sidecar
from neo_app.comfy_spaces.variants import (
    ComfySpaceVariant,
    SpaceVariantError,
    create_space_variant,
    load_space_variant,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_VERSION_STATE_SCHEMA_VERSION = "neo.comfy_space.version_state.v1"
SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION = "neo.comfy_space.replacement_plan.v1"
SPACE_MIGRATION_AUDIT_SCHEMA_VERSION = "neo.comfy_space.migration_audit.v1"
SPACE_VERSIONING_SERVICE_VERSION = "neo.comfy_space.versioning_service.v1"

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class SpaceVersioningError(ValueError):
    def __init__(self, message: str, *, code: str = "space_versioning_invalid") -> None:
        super().__init__(message)
        self.code = str(code or "space_versioning_invalid")


class SpaceVersionActivation(StrictContractModel):
    version: str
    activated_at: str
    reason: Literal["publish", "rollback", "manual"] = "manual"
    previous_version: str = ""

    @field_validator("version", "previous_version")
    @classmethod
    def validate_version(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if info.field_name == "previous_version" and not text:
            return ""
        if not _VERSION_RE.fullmatch(text):
            raise ValueError(f"{info.field_name} must be semantic version text")
        return text


class SpaceVersionState(StrictContractModel):
    schema_version: Literal[SPACE_VERSION_STATE_SCHEMA_VERSION] = SPACE_VERSION_STATE_SCHEMA_VERSION
    kind: Literal["comfy_space_version_state"] = "comfy_space_version_state"
    space_id: str
    active_version: str
    updated_at: str
    activation_history: list[SpaceVersionActivation] = Field(default_factory=list)

    @field_validator("space_id")
    @classmethod
    def validate_space_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SAFE_ID_RE.fullmatch(text):
            raise ValueError("space_id is invalid")
        return text

    @field_validator("active_version")
    @classmethod
    def validate_active_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("active_version must be semantic version text")
        return text


MigrationStatus = Literal["mapped", "ambiguous", "unmapped", "review", "compatible", "incompatible", "dropped"]


class SpaceReplacementCandidate(StrictContractModel):
    candidate_id: str
    score: int = Field(ge=0, le=100)
    reason: str
    node_id: str = ""
    source_node_id: str = ""
    class_type: str = ""
    field: str = ""
    kind: str = ""
    subgraph_path: list[str] = Field(default_factory=list)


class SpaceReplacementMapping(StrictContractModel):
    mapping_type: Literal["input", "output", "anchor", "stage", "dynamic", "variant", "graph"]
    source_id: str
    status: MigrationStatus
    auto_applied: bool = False
    selected_candidate_id: str = ""
    candidates: list[SpaceReplacementCandidate] = Field(default_factory=list)
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class SpaceReplacementPlan(StrictContractModel):
    schema_version: Literal[SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION] = SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION
    kind: Literal["comfy_space_replacement_plan"] = "comfy_space_replacement_plan"
    plan_id: str
    draft_id: str
    space_id: str
    from_version: str
    to_version: str
    created_at: str
    updated_at: str
    source_publication_fingerprint: str
    old_workflow_sha256: str
    new_workflow_sha256: str
    review_confirmed: bool = False
    reviewed_at: str = ""
    reviewed_draft_revision: int = Field(default=0, ge=0)
    graph_diff: dict[str, Any] = Field(default_factory=dict)
    mappings: list[SpaceReplacementMapping] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    execution_performed: Literal[False] = False
    published_package_mutated: Literal[False] = False

    @field_validator("plan_id", "draft_id", "space_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        text = str(value or "").strip().lower()
        if not _SAFE_ID_RE.fullmatch(text):
            raise ValueError(f"{info.field_name} is invalid")
        return text

    @field_validator("from_version", "to_version")
    @classmethod
    def validate_versions(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("replacement versions must be semantic version text")
        return text


class SpaceMigrationAudit(StrictContractModel):
    schema_version: Literal[SPACE_MIGRATION_AUDIT_SCHEMA_VERSION] = SPACE_MIGRATION_AUDIT_SCHEMA_VERSION
    kind: Literal["comfy_space_migration_audit"] = "comfy_space_migration_audit"
    space_id: str
    from_version: str
    to_version: str
    published_at: str
    replacement_plan_sha256: str
    review_confirmed: Literal[True] = True
    mapping_summary: dict[str, Any] = Field(default_factory=dict)
    graph_diff: dict[str, Any] = Field(default_factory=dict)
    execution_authority: Literal[False] = False
    binding_authority: Literal[False] = False


class SpaceVersionCollection(StrictContractModel):
    schema_version: Literal[SPACE_VERSION_STATE_SCHEMA_VERSION] = SPACE_VERSION_STATE_SCHEMA_VERSION
    ok: Literal[True] = True
    space_id: str
    active_version: str
    latest_version: str
    versions: list[dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpaceVersioningError(f"{label} is unavailable.", code="space_versioning_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceVersioningError(f"Unable to read {label}.", code="space_versioning_invalid") from exc
    if not isinstance(payload, dict):
        raise SpaceVersioningError(f"{label} must be a JSON object.", code="space_versioning_invalid")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(value: str) -> tuple[Any, ...]:
    text = str(value or "0.0.0").strip()
    core, _, prerelease = text.partition("-")
    core = core.split("+", 1)[0]
    nums: list[int] = []
    for part in core.split(".")[:3]:
        try:
            nums.append(int(part))
        except (TypeError, ValueError):
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (*nums, 1 if not prerelease else 0, prerelease)


def _publication_fingerprint(space_id: str, version: str, *, root_dir: Path | str | None = None) -> str:
    raw = _read_json(comfy_space_publication_receipt_path(space_id, version, root_dir), label="publication receipt")
    try:
        receipt = model_from_dict(ComfySpacePublicationReceipt, raw)
    except Exception as exc:
        raise SpaceVersioningError(f"Invalid publication receipt: {exc}", code="space_version_invalid") from exc
    if receipt.space_id != str(space_id or "").strip().lower() or receipt.version != str(version or "").strip():
        raise SpaceVersioningError("Publication receipt identity mismatch.", code="space_version_invalid")
    return _canonical_sha(model_to_dict(receipt))


def _published_versions(space_id: str, *, root_dir: Path | str | None = None) -> list[str]:
    root = comfy_space_root(space_id, root_dir) / "versions"
    if not root.is_dir():
        return []
    versions: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or not _VERSION_RE.fullmatch(child.name):
            continue
        manifest_path = comfy_space_manifest_path(space_id, child.name, root_dir)
        receipt_path = comfy_space_publication_receipt_path(space_id, child.name, root_dir)
        if not manifest_path.is_file() or not receipt_path.is_file():
            continue
        try:
            manifest = load_space_manifest(manifest_path)
            if manifest.status != "published" or manifest.space_id != str(space_id).strip().lower() or manifest.version != child.name:
                continue
            _publication_fingerprint(space_id, child.name, root_dir=root_dir)
            receipt = model_from_dict(ComfySpacePublicationReceipt, _read_json(receipt_path, label="publication receipt"))
            if receipt.migration_sha256:
                migration_path = comfy_space_migration_path(space_id, child.name, root_dir)
                if not migration_path.is_file():
                    continue
                audit = model_from_dict(SpaceMigrationAudit, _read_json(migration_path, label="migration audit"))
                if audit.space_id != str(space_id).strip().lower() or audit.to_version != child.name:
                    continue
                if _canonical_sha(model_to_dict(audit)) != receipt.migration_sha256:
                    continue
        except Exception:
            continue
        versions.append(child.name)
    return sorted(versions, key=_version_key, reverse=True)


def load_space_version_state(space_id: str, *, root_dir: Path | str | None = None) -> SpaceVersionState | None:
    path = comfy_space_version_state_path(space_id, root_dir)
    if not path.is_file():
        return None
    try:
        return model_from_dict(SpaceVersionState, _read_json(path, label="Space version state"))
    except Exception as exc:
        raise SpaceVersioningError(f"Invalid Space version state: {exc}", code="space_version_state_invalid") from exc


def resolve_active_space_version(space_id: str, *, root_dir: Path | str | None = None) -> str:
    versions = _published_versions(space_id, root_dir=root_dir)
    if not versions:
        return ""
    try:
        state = load_space_version_state(space_id, root_dir=root_dir)
    except SpaceVersioningError:
        state = None
    if state and state.active_version in versions:
        return state.active_version
    return versions[0]


def activate_space_version(
    space_id: str,
    version: str,
    *,
    reason: Literal["publish", "rollback", "manual"] = "manual",
    expected_active_version: str | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower()
    clean_version = str(version or "").strip()
    if not _SAFE_ID_RE.fullmatch(clean_space):
        raise SpaceVersioningError("Invalid space_id.")
    if not _VERSION_RE.fullmatch(clean_version):
        raise SpaceVersioningError("Invalid version.")
    versions = _published_versions(clean_space, root_dir=root_dir)
    if clean_version not in versions:
        raise SpaceVersioningError("Target version is not a valid immutable published Space version.", code="space_version_missing")
    current = resolve_active_space_version(clean_space, root_dir=root_dir)
    if expected_active_version is not None and str(expected_active_version or "").strip() != current:
        raise SpaceVersioningError(
            f"Active-version conflict: expected {expected_active_version}, current {current or 'none'}.",
            code="space_active_version_conflict",
        )
    history: list[SpaceVersionActivation] = []
    try:
        existing = load_space_version_state(clean_space, root_dir=root_dir)
    except SpaceVersioningError:
        existing = None
    if existing:
        history = list(existing.activation_history)
    if not history or history[-1].version != clean_version or current != clean_version:
        history.append(SpaceVersionActivation(version=clean_version, activated_at=_now_iso(), reason=reason, previous_version=current))
    history = history[-100:]
    state = SpaceVersionState(space_id=clean_space, active_version=clean_version, updated_at=_now_iso(), activation_history=history)
    _atomic_write_json(comfy_space_version_state_path(clean_space, root_dir), model_to_dict(state))
    return {
        "ok": True,
        "schema_version": SPACE_VERSION_STATE_SCHEMA_VERSION,
        "space_id": clean_space,
        "active_version": clean_version,
        "previous_version": current,
        "reason": reason,
        "published_package_mutated": False,
    }


def rollback_space_version(
    space_id: str,
    version: str,
    *,
    confirm_rollback: bool,
    expected_active_version: str | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    if confirm_rollback is not True:
        raise SpaceVersioningError("Explicit rollback confirmation is required.", code="space_rollback_confirmation_required")
    target = str(version or "").strip()
    current = resolve_active_space_version(space_id, root_dir=root_dir)
    if current == target:
        return {
            "ok": True,
            "schema_version": SPACE_VERSION_STATE_SCHEMA_VERSION,
            "space_id": str(space_id or "").strip().lower(),
            "active_version": target,
            "previous_version": current,
            "reason": "rollback",
            "published_package_mutated": False,
            "already_active": True,
        }
    return activate_space_version(
        space_id,
        target,
        reason="rollback",
        expected_active_version=expected_active_version,
        root_dir=root_dir,
    )


def list_space_versions(space_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower()
    versions = _published_versions(clean_space, root_dir=root_dir)
    active = resolve_active_space_version(clean_space, root_dir=root_dir)
    rows: list[dict[str, Any]] = []
    for version in versions:
        manifest = load_space_manifest(comfy_space_manifest_path(clean_space, version, root_dir))
        receipt = _read_json(comfy_space_publication_receipt_path(clean_space, version, root_dir), label="publication receipt")
        migration_path = comfy_space_migration_path(clean_space, version, root_dir)
        migration = _read_json(migration_path, label="migration audit") if migration_path.is_file() else None
        rows.append({
            "version": version,
            "active": version == active,
            "published_at": str(receipt.get("published_at") or ""),
            "workflow_sha256": str((manifest.workflow.sha256 or "")),
            "replaces_version": str((migration or {}).get("from_version") or ""),
            "migration_review_confirmed": bool((migration or {}).get("review_confirmed")),
        })
    payload = SpaceVersionCollection(
        space_id=clean_space,
        active_version=active,
        latest_version=versions[0] if versions else "",
        versions=rows,
        count=len(rows),
    )
    return model_to_dict(payload)


def _node_lineage(node_id: str, workflow: Mapping[str, Any]) -> dict[str, Any]:
    node = dict(workflow.get(str(node_id)) or {})
    meta = dict(node.get("_meta") or {})
    return {
        "node_id": str(node_id),
        "source_node_id": str(meta.get("source_node_id") or node_id),
        "subgraph_path": [str(item) for item in list(meta.get("subgraph_path") or [])],
        "subgraph_definition_id": str(meta.get("subgraph_definition_id") or ""),
        "class_type": str(node.get("class_type") or ""),
        "title": str(meta.get("title") or ""),
    }


def _input_candidates_for_old(old_input: Any, old_workflow: Mapping[str, Any], new_analysis: Mapping[str, Any]) -> list[SpaceReplacementCandidate]:
    binding = old_input.binding
    old_lineage = _node_lineage(binding.node_id, old_workflow)
    rows: list[SpaceReplacementCandidate] = []
    for raw in list(dict(new_analysis.get("candidates") or {}).get("inputs") or []):
        item = dict(raw or {})
        score = 0
        reasons: list[str] = []
        if str(item.get("field") or "") == binding.field:
            score += 35; reasons.append("same input field")
        if str(item.get("class_type") or "") == old_lineage["class_type"]:
            score += 35; reasons.append("same Comfy node class")
        if str(item.get("source_node_id") or "") == old_lineage["source_node_id"]:
            score += 15; reasons.append("same source node lineage")
        if [str(x) for x in list(item.get("subgraph_path") or [])] == old_lineage["subgraph_path"]:
            score += 10; reasons.append("same subgraph instance path")
        if str(item.get("kind") or "") == str(old_input.kind):
            score += 5; reasons.append("same Space value kind")
        if str(item.get("node_id") or "") == str(binding.node_id) and str(item.get("field") or "") == binding.field:
            score = max(score, 98); reasons.append("same canonical node binding")
        if score < 35:
            continue
        rows.append(SpaceReplacementCandidate(
            candidate_id=str(item.get("candidate_id") or ""), score=min(score, 100), reason="; ".join(reasons),
            node_id=str(item.get("node_id") or ""), source_node_id=str(item.get("source_node_id") or ""),
            class_type=str(item.get("class_type") or ""), field=str(item.get("field") or ""), kind=str(item.get("kind") or ""),
            subgraph_path=[str(x) for x in list(item.get("subgraph_path") or [])],
        ))
    return sorted(rows, key=lambda row: (-row.score, row.candidate_id))


def _output_candidates_for_old(old_output: Any, old_workflow: Mapping[str, Any], new_analysis: Mapping[str, Any]) -> list[SpaceReplacementCandidate]:
    binding = old_output.binding
    old_lineage = _node_lineage(binding.node_id, old_workflow)
    rows: list[SpaceReplacementCandidate] = []
    for raw in list(dict(new_analysis.get("candidates") or {}).get("outputs") or []):
        item = dict(raw or {})
        score = 0; reasons: list[str] = []
        if str(item.get("class_type") or "") == old_lineage["class_type"]:
            score += 35; reasons.append("same Comfy node class")
        if str(item.get("source_node_id") or "") == old_lineage["source_node_id"]:
            score += 15; reasons.append("same source node lineage")
        if [str(x) for x in list(item.get("subgraph_path") or [])] == old_lineage["subgraph_path"]:
            score += 10; reasons.append("same subgraph instance path")
        if str(item.get("kind") or "") == str(old_output.kind):
            score += 15; reasons.append("same output kind")
        item_index = item.get("output_index")
        if binding.output_index is not None and item_index == binding.output_index:
            score += 20; reasons.append("same output socket index")
        if binding.field is not None and str(item.get("field") or "") == str(binding.field):
            score += 20; reasons.append("same output field")
        if str(item.get("node_id") or "") == str(binding.node_id):
            score += 10; reasons.append("same canonical node")
        if score < 35:
            continue
        rows.append(SpaceReplacementCandidate(
            candidate_id=str(item.get("candidate_id") or ""), score=min(score, 100), reason="; ".join(reasons),
            node_id=str(item.get("node_id") or ""), source_node_id=str(item.get("source_node_id") or ""),
            class_type=str(item.get("class_type") or ""), field=str(item.get("field") or ""), kind=str(item.get("kind") or ""),
            subgraph_path=[str(x) for x in list(item.get("subgraph_path") or [])],
        ))
    return sorted(rows, key=lambda row: (-row.score, row.candidate_id))


def _mapping_from_candidates(mapping_type: Literal["input", "output"], source_id: str, rows: list[SpaceReplacementCandidate]) -> SpaceReplacementMapping:
    if not rows:
        return SpaceReplacementMapping(mapping_type=mapping_type, source_id=source_id, status="unmapped", reason="No compatible candidate was found in the replacement workflow.")
    top = rows[0].score
    tied = [row for row in rows if row.score == top]
    if top >= 75 and len(tied) == 1:
        return SpaceReplacementMapping(
            mapping_type=mapping_type, source_id=source_id, status="mapped", auto_applied=True,
            selected_candidate_id=rows[0].candidate_id, candidates=rows[:8],
            reason=f"Unique high-confidence mapping ({top}/100).",
        )
    return SpaceReplacementMapping(
        mapping_type=mapping_type, source_id=source_id, status="ambiguous", auto_applied=False,
        candidates=rows[:8], reason="Multiple or low-confidence candidates require author review.",
    )


def _graph_diff(old_workflow: Mapping[str, Any], new_workflow: Mapping[str, Any]) -> dict[str, Any]:
    old_ids = {str(k) for k in old_workflow}
    new_ids = {str(k) for k in new_workflow}
    old_classes = {str(k): str(dict(v or {}).get("class_type") or "") for k, v in old_workflow.items()}
    new_classes = {str(k): str(dict(v or {}).get("class_type") or "") for k, v in new_workflow.items()}
    common = old_ids & new_ids
    changed_class = sorted(node_id for node_id in common if old_classes.get(node_id) != new_classes.get(node_id))
    old_class_counts: dict[str, int] = {}
    new_class_counts: dict[str, int] = {}
    for value in old_classes.values(): old_class_counts[value] = old_class_counts.get(value, 0) + 1
    for value in new_classes.values(): new_class_counts[value] = new_class_counts.get(value, 0) + 1
    classes = sorted(set(old_class_counts) | set(new_class_counts))
    class_delta = [
        {"class_type": cls, "before": old_class_counts.get(cls, 0), "after": new_class_counts.get(cls, 0), "delta": new_class_counts.get(cls, 0)-old_class_counts.get(cls, 0)}
        for cls in classes if old_class_counts.get(cls, 0) != new_class_counts.get(cls, 0)
    ]
    return {
        "old_node_count": len(old_ids), "new_node_count": len(new_ids),
        "added_canonical_node_ids": sorted(new_ids-old_ids), "removed_canonical_node_ids": sorted(old_ids-new_ids),
        "changed_class_node_ids": changed_class, "class_count_delta": class_delta,
    }


def _old_ui_maps(ui: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return ({item.input_id: item for item in ui.inputs}, {item.output_id: item for item in ui.outputs})


def _candidate_draft_map(draft: ComfySpaceBuilderDraft) -> tuple[dict[str, SpaceBuilderInputDraft], dict[str, SpaceBuilderOutputDraft]]:
    return ({item.candidate_id: item for item in draft.inputs}, {item.candidate_id: item for item in draft.outputs})


def _anchor_mappings(old_anchors: Any, new_analysis: Mapping[str, Any]) -> list[SpaceReplacementMapping]:
    new_rows = [dict(item or {}) for item in list(dict(new_analysis.get("graph_anchors") or {}).get("proposals") or [])]
    mappings: list[SpaceReplacementMapping] = []
    for old in list(getattr(old_anchors, "anchors", []) or []):
        candidates: list[SpaceReplacementCandidate] = []
        for item in new_rows:
            if str(item.get("semantic_role") or "") != old.semantic_role:
                continue
            source = dict(item.get("source") or {})
            target = dict(item.get("target") or {})
            score = 25; reasons=["same semantic role"]
            if str(source.get("source_node_id") or "") == old.source.source_node_id:
                score += 30; reasons.append("same source lineage")
            if [str(x) for x in list(source.get("subgraph_path") or [])] == list(old.source.subgraph_path):
                score += 15; reasons.append("same source subgraph path")
            if str(source.get("class_type") or "") == old.source.class_type:
                score += 10; reasons.append("same source class")
            if old.target is not None:
                if str(target.get("source_node_id") or "") == old.target.source_node_id:
                    score += 10; reasons.append("same target lineage")
                if str(target.get("input_field") or "") == str(old.target.input_field or ""):
                    score += 10; reasons.append("same target field")
            candidates.append(SpaceReplacementCandidate(
                candidate_id=str(item.get("anchor_id") or ""), score=min(score,100), reason="; ".join(reasons),
                node_id=str(source.get("canonical_node_id") or ""), source_node_id=str(source.get("source_node_id") or ""),
                class_type=str(source.get("class_type") or ""), field=str((target or {}).get("input_field") or ""), kind=old.semantic_role,
                subgraph_path=[str(x) for x in list(source.get("subgraph_path") or [])],
            ))
        candidates.sort(key=lambda row: (-row.score, row.candidate_id))
        if candidates and candidates[0].score >= 70 and len([x for x in candidates if x.score == candidates[0].score]) == 1:
            mappings.append(SpaceReplacementMapping(mapping_type="anchor", source_id=old.anchor_id, status="mapped", auto_applied=True, selected_candidate_id=candidates[0].candidate_id, candidates=candidates[:8], reason=f"Unique high-confidence anchor mapping ({candidates[0].score}/100).", details={"semantic_role": old.semantic_role}))
        elif candidates:
            mappings.append(SpaceReplacementMapping(mapping_type="anchor", source_id=old.anchor_id, status="ambiguous", candidates=candidates[:8], reason="Anchor candidates require author review.", details={"semantic_role": old.semantic_role}))
        else:
            mappings.append(SpaceReplacementMapping(mapping_type="anchor", source_id=old.anchor_id, status="unmapped", reason="No replacement anchor with the same semantic role was found.", details={"semantic_role": old.semantic_role}))
    return mappings


def _apply_migrated_configuration(
    draft: ComfySpaceBuilderDraft,
    *, old_manifest: Any, old_ui: Any, old_stages: Any, old_dynamic: Any, old_anchors: Any,
    mappings: list[SpaceReplacementMapping], new_analysis: Mapping[str, Any], target_version: str,
) -> ComfySpaceBuilderDraft:
    payload = model_to_dict(draft)
    payload["identity"] = {
        **payload["identity"],
        "space_id": old_manifest.space_id,
        "name": old_manifest.name,
        "version": target_version,
        "description": old_manifest.description,
        "author": model_to_dict(old_manifest.author),
        "presentation": model_to_dict(old_manifest.presentation),
    }
    payload["submit_label"] = old_ui.layout.submit_label
    payload["results_title"] = old_ui.layout.results_title
    # Retain historical presentation for mapped controls, but retain new
    # source-node sections for newly discovered/unmapped replacement inputs.
    # Otherwise the replacement draft can reference sections that were just
    # discarded, making an otherwise safe workflow replacement unsaveable.
    previous_sections = [model_to_dict(item) for item in old_ui.layout.sections]
    previous_ids = {row["section_id"] for row in previous_sections}
    added_sections = [row for row in payload.get("sections") or [] if row["section_id"] not in previous_ids]
    payload["sections"] = previous_sections + added_sections

    input_map = {row.source_id: row for row in mappings if row.mapping_type == "input" and row.status == "mapped" and row.selected_candidate_id}
    output_map = {row.source_id: row for row in mappings if row.mapping_type == "output" and row.status == "mapped" and row.selected_candidate_id}
    anchor_map = {row.source_id: row for row in mappings if row.mapping_type == "anchor" and row.status == "mapped" and row.selected_candidate_id}
    old_ui_inputs, old_ui_outputs = _old_ui_maps(old_ui)

    new_inputs = [dict(item) for item in payload.get("inputs") or []]
    by_candidate = {str(item.get("candidate_id") or ""): item for item in new_inputs}
    for old in old_manifest.inputs:
        row = input_map.get(old.input_id)
        if not row: continue
        target = by_candidate.get(row.selected_candidate_id)
        if not target: continue
        uirow = old_ui_inputs.get(old.input_id)
        target.update({
            "enabled": True, "input_id": old.input_id, "label": uirow.label if uirow else old.ui.label,
            "help_text": uirow.help_text if uirow else old.ui.help_text,
            "section_id": uirow.section_id if uirow else "main", "order": uirow.order if uirow else old.ui.order,
            "advanced": uirow.advanced if uirow else old.ui.advanced, "required": old.required,
            "multiplicity": old.multiplicity, "default_mode": "none" if old.kind in {"image","mask","video","audio","file"} else ("custom" if old.default is not None else "template"),
            "default": deepcopy(old.default), "control_override": (uirow.control if uirow else None),
            "constraints_override": model_to_dict(uirow.constraints) if uirow else None,
            "options_override": model_to_dict(uirow.options) if uirow else None,
        })
    payload["inputs"] = new_inputs

    new_outputs = [dict(item) for item in payload.get("outputs") or []]
    for item in new_outputs:
        item["enabled"] = False; item["primary"] = False
    by_candidate_out = {str(item.get("candidate_id") or ""): item for item in new_outputs}
    for old in old_manifest.outputs:
        row = output_map.get(old.output_id)
        if not row: continue
        target = by_candidate_out.get(row.selected_candidate_id)
        if not target: continue
        uirow = old_ui_outputs.get(old.output_id)
        target.update({"enabled": True, "output_id": old.output_id, "label": uirow.label if uirow else old.label, "order": uirow.order if uirow else 100, "collection": old.collection, "primary": old.primary, "renderer_override": (uirow.renderer if uirow else None)})
    payload["outputs"] = new_outputs

    # Confirm only anchors that received unique high-confidence migration evidence.
    anchors = [dict(item) for item in payload.get("anchors") or []]
    confirmed_by_new = {row.selected_candidate_id: row for row in anchor_map.values()}
    semantic_by_old = {item.anchor_id: item.semantic_role for item in list(old_anchors.anchors or [])}
    for item in anchors:
        row = confirmed_by_new.get(str(item.get("anchor_id") or ""))
        if row:
            item["confirmed"] = True
            item["semantic_role"] = semantic_by_old.get(row.source_id, item.get("semantic_role"))
    payload["anchors"] = anchors

    mapped_output_ids = set(output_map)
    migrated_stage_ids: set[str] = set()
    migrated_stages: list[dict[str, Any]] = []
    if old_stages is not None:
        for stage in old_stages.stages:
            refs = set(stage.target_output_ids) | set(stage.intermediate_output_ids)
            if refs.issubset(mapped_output_ids):
                migrated_stages.append({
                    "stage_id": stage.stage_id, "label": stage.label, "description": stage.description, "order": stage.order,
                    "required_stage_ids": list(stage.required_stage_ids), "target_output_ids": list(stage.target_output_ids),
                    "intermediate_output_ids": list(stage.intermediate_output_ids), "enabled_by_default": stage.enabled_by_default,
                    "optional": stage.optional, "can_run_alone": stage.can_run_alone, "resource_policy": stage.resource_policy,
                })
                migrated_stage_ids.add(stage.stage_id)
        migrated_stages = [row for row in migrated_stages if set(row.get("required_stage_ids") or []).issubset(migrated_stage_ids)]
        migrated_stage_ids = {str(row.get("stage_id") or "") for row in migrated_stages}
    if migrated_stages:
        payload["stages"] = migrated_stages

    mapped_input_ids = set(input_map)
    section_ids = {str(item.get("section_id") or "") for item in payload.get("sections") or []}
    behaviors: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []
    if old_dynamic is not None:
        for behavior in old_dynamic.behaviors:
            valid = True
            if behavior.target_type == "input" and behavior.target_id not in mapped_input_ids: valid = False
            if behavior.target_type == "section" and behavior.target_id not in section_ids: valid = False
            for pred in behavior.condition.predicates:
                if pred.source == "input" and pred.ref not in mapped_input_ids: valid = False
                if pred.source == "stage_enabled" and pred.ref not in migrated_stage_ids: valid = False
            if valid: behaviors.append(model_to_dict(behavior))
        for collection in old_dynamic.collections:
            if collection.input_id in mapped_input_ids: collections.append(model_to_dict(collection))
    payload["dynamic_behaviors"] = behaviors
    payload["dynamic_collections"] = collections
    return model_from_dict(ComfySpaceBuilderDraft, payload)


def _variant_compatibility(space_id: str, from_version: str, *, anchor_map: Mapping[str, str], mapped_inputs: set[str], mapped_stages: set[str], root_dir: Path | str | None = None) -> list[SpaceReplacementMapping]:
    root = comfy_spaces_variants_root(root_dir)
    rows: list[SpaceReplacementMapping] = []
    if not root.is_dir(): return rows
    for path in sorted(root.glob("*.json")):
        try:
            variant = load_space_variant(path.stem, root_dir=root_dir, require_ready=False)
        except Exception:
            continue
        if variant.base_space_id != space_id or variant.base_version != from_version:
            continue
        missing: list[str] = []
        for addon in variant.graph_addons:
            ids = list(addon.anchor_ids.values()) if addon.lane == "module" else [addon.raw_anchor_id]
            for anchor_id in ids:
                if anchor_id and anchor_id not in anchor_map: missing.append(f"anchor:{anchor_id}")
        for stage_id in variant.stage_defaults.enabled_stage_ids:
            if stage_id not in mapped_stages: missing.append(f"stage:{stage_id}")
        if variant.stage_defaults.preferred_stage_id and variant.stage_defaults.preferred_stage_id not in mapped_stages:
            missing.append(f"stage:{variant.stage_defaults.preferred_stage_id}")
        for input_id in variant.saved_values:
            if input_id not in mapped_inputs: missing.append(f"input:{input_id}")
        rows.append(SpaceReplacementMapping(mapping_type="variant", source_id=variant.variant_id, status="compatible" if not missing else "incompatible", auto_applied=False, reason="Variant can be explicitly migrated to the replacement version." if not missing else "Variant references migration targets that were not preserved.", details={"missing": sorted(set(missing)), "variant_name": variant.name}))
    return rows


def create_workflow_replacement_draft(
    *, space_id: str, from_version: str, to_version: str, import_id: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    clean_space = str(space_id or "").strip().lower(); source_version=str(from_version or "").strip(); target_version=str(to_version or "").strip()
    if not _SAFE_ID_RE.fullmatch(clean_space): raise SpaceVersioningError("Invalid space_id.")
    if not _VERSION_RE.fullmatch(source_version) or not _VERSION_RE.fullmatch(target_version): raise SpaceVersioningError("Replacement versions must be semantic version text.")
    if comfy_space_version_root(clean_space, target_version, root_dir).exists():
        raise SpaceVersioningError("Target replacement version already exists; published versions are immutable.", code="space_version_exists")
    old_manifest = load_space_manifest(comfy_space_manifest_path(clean_space, source_version, root_dir))
    if old_manifest.status != "published": raise SpaceVersioningError("Replacement source must be a published version.")
    old_ui = load_space_ui(comfy_space_ui_path(clean_space, source_version, root_dir))
    old_workflow = _read_json(comfy_space_workflow_path(clean_space, source_version, root_dir), label="published workflow")
    old_stages = parse_stage_sidecar(_read_json(comfy_space_stages_path(clean_space, source_version, root_dir), label="stage sidecar")) if comfy_space_stages_path(clean_space, source_version, root_dir).is_file() else None
    old_dynamic = parse_dynamic_controls(_read_json(comfy_space_version_root(clean_space, source_version, root_dir)/"dynamic_controls.json", label="dynamic controls")) if (comfy_space_version_root(clean_space, source_version, root_dir)/"dynamic_controls.json").is_file() else None
    old_anchors = parse_graph_anchor_sidecar(_read_json(comfy_space_graph_anchors_path(clean_space, source_version, root_dir), label="graph anchors")) if comfy_space_graph_anchors_path(clean_space, source_version, root_dir).is_file() else None

    import_root = comfy_space_import_root(import_id, root_dir)
    new_workflow = _read_json(import_root / "workflow.json", label="replacement workflow")
    new_analysis = _read_json(import_root / "analysis.json", label="replacement analysis")
    created = create_builder_draft_from_import(import_id, root_dir=root_dir, space_id=clean_space, name=old_manifest.name)
    draft = load_builder_draft(created["draft_id"], root_dir=root_dir)

    mappings: list[SpaceReplacementMapping] = []
    for old in old_manifest.inputs:
        mappings.append(_mapping_from_candidates("input", old.input_id, _input_candidates_for_old(old, old_workflow, new_analysis)))
    for old in old_manifest.outputs:
        mappings.append(_mapping_from_candidates("output", old.output_id, _output_candidates_for_old(old, old_workflow, new_analysis)))
    if old_anchors is not None:
        mappings.extend(_anchor_mappings(old_anchors, new_analysis))

    input_mapped = {row.source_id for row in mappings if row.mapping_type == "input" and row.status == "mapped"}
    output_mapped = {row.source_id for row in mappings if row.mapping_type == "output" and row.status == "mapped"}
    anchor_map = {row.source_id: row.selected_candidate_id for row in mappings if row.mapping_type == "anchor" and row.status == "mapped" and row.selected_candidate_id}
    mapped_stage_ids: set[str] = set()
    if old_stages is not None:
        for stage in old_stages.stages:
            refs = set(stage.target_output_ids) | set(stage.intermediate_output_ids)
            status: MigrationStatus = "mapped" if refs.issubset(output_mapped) else "review"
            if status == "mapped": mapped_stage_ids.add(stage.stage_id)
            mappings.append(SpaceReplacementMapping(mapping_type="stage", source_id=stage.stage_id, status=status, auto_applied=status=="mapped", reason="Stage outputs preserved." if status=="mapped" else "Stage references outputs that were not uniquely mapped."))
    if old_dynamic is not None:
        for behavior in old_dynamic.behaviors:
            refs_ok = True
            if behavior.target_type == "input" and behavior.target_id not in input_mapped: refs_ok=False
            for pred in behavior.condition.predicates:
                if pred.source=="input" and pred.ref not in input_mapped: refs_ok=False
                if pred.source=="stage_enabled" and pred.ref not in mapped_stage_ids: refs_ok=False
            mappings.append(SpaceReplacementMapping(mapping_type="dynamic", source_id=behavior.behavior_id, status="mapped" if refs_ok else "review", auto_applied=refs_ok, reason="Dynamic behavior references preserved IDs." if refs_ok else "Dynamic behavior references migration targets that need review."))
        for collection in old_dynamic.collections:
            ok = collection.input_id in input_mapped
            mappings.append(SpaceReplacementMapping(mapping_type="dynamic", source_id=collection.collection_id, status="mapped" if ok else "review", auto_applied=ok, reason="Dynamic collection input preserved." if ok else "Dynamic collection input was not uniquely mapped."))
    mappings.extend(_variant_compatibility(clean_space, source_version, anchor_map=anchor_map, mapped_inputs=input_mapped, mapped_stages=mapped_stage_ids, root_dir=root_dir))

    migrated = _apply_migrated_configuration(draft, old_manifest=old_manifest, old_ui=old_ui, old_stages=old_stages, old_dynamic=old_dynamic, old_anchors=old_anchors or type("Empty", (), {"anchors": []})(), mappings=mappings, new_analysis=new_analysis, target_version=target_version)
    saved = save_builder_draft(draft.draft_id, model_to_dict(migrated), root_dir=root_dir, expected_revision=draft.revision)

    ambiguous = sum(1 for row in mappings if row.status == "ambiguous")
    unmapped = sum(1 for row in mappings if row.status == "unmapped")
    review = sum(1 for row in mappings if row.status in {"ambiguous","unmapped","review","incompatible"})
    graph_diff = _graph_diff(old_workflow, new_workflow)
    plan = SpaceReplacementPlan(
        plan_id=f"replace_{uuid4().hex[:20]}", draft_id=saved.draft_id, space_id=clean_space, from_version=source_version, to_version=target_version,
        created_at=_now_iso(), updated_at=_now_iso(), source_publication_fingerprint=_publication_fingerprint(clean_space, source_version, root_dir=root_dir),
        old_workflow_sha256=_file_sha(comfy_space_workflow_path(clean_space, source_version, root_dir)), new_workflow_sha256=_file_sha(import_root / "workflow.json"),
        graph_diff=graph_diff, mappings=mappings,
        summary={"mapped": sum(1 for row in mappings if row.status in {"mapped","compatible"}), "ambiguous": ambiguous, "unmapped": unmapped, "review_required": review, "auto_applied": sum(1 for row in mappings if row.auto_applied), "variant_review_count": sum(1 for row in mappings if row.mapping_type=="variant")},
    )
    _atomic_write_json(comfy_space_draft_replacement_plan_path(saved.draft_id, root_dir), model_to_dict(plan))
    return {"ok": True, "schema_version": SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION, "draft_id": saved.draft_id, "plan": model_to_dict(plan), "draft": model_to_dict(saved), "execution_performed": False, "published_package_mutated": False}


def load_replacement_plan(draft_id: str, *, root_dir: Path | str | None = None) -> SpaceReplacementPlan | None:
    path = comfy_space_draft_replacement_plan_path(draft_id, root_dir)
    if not path.is_file(): return None
    try:
        return model_from_dict(SpaceReplacementPlan, _read_json(path, label="replacement plan"))
    except Exception as exc:
        raise SpaceVersioningError(f"Invalid replacement plan: {exc}", code="space_replacement_plan_invalid") from exc


def confirm_replacement_review(draft_id: str, *, confirm_review: bool, root_dir: Path | str | None = None) -> dict[str, Any]:
    if confirm_review is not True:
        raise SpaceVersioningError("Explicit replacement-review confirmation is required.", code="space_replacement_review_confirmation_required")
    plan = load_replacement_plan(draft_id, root_dir=root_dir)
    if plan is None:
        raise SpaceVersioningError("Builder draft is not a workflow-replacement draft.", code="space_replacement_plan_missing")
    # Re-check the source fingerprint so review cannot approve a plan built from a changed base receipt.
    current = _publication_fingerprint(plan.space_id, plan.from_version, root_dir=root_dir)
    if current != plan.source_publication_fingerprint:
        raise SpaceVersioningError("The replacement source publication changed after comparison; create a new replacement plan.", code="space_replacement_source_changed")
    current_draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
    plan.review_confirmed = True; plan.reviewed_at = _now_iso(); plan.reviewed_draft_revision = current_draft.revision; plan.updated_at = _now_iso()
    _atomic_write_json(comfy_space_draft_replacement_plan_path(draft_id, root_dir), model_to_dict(plan))
    return {"ok": True, "schema_version": SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION, "plan": model_to_dict(plan), "execution_performed": False}


def replacement_publish_guard(draft_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    plan = load_replacement_plan(draft_id, root_dir=root_dir)
    if plan is None:
        return {"is_replacement": False, "ready": True, "plan": None, "blockers": [], "warnings": []}
    blockers: list[dict[str,str]]=[]; warnings: list[dict[str,str]]=[]
    if not plan.review_confirmed:
        blockers.append({"code":"replacement_review_required","message":"Workflow replacement diff/migration review must be explicitly confirmed before publication."})
    else:
        current_draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
        if plan.reviewed_draft_revision != current_draft.revision:
            blockers.append({"code":"replacement_review_stale","message":"Builder draft changed after replacement review confirmation; review the migration diff again before publication."})
    try:
        fingerprint = _publication_fingerprint(plan.space_id, plan.from_version, root_dir=root_dir)
        if fingerprint != plan.source_publication_fingerprint:
            blockers.append({"code":"replacement_source_changed","message":"Source publication changed after the replacement comparison was created."})
    except SpaceVersioningError as exc:
        blockers.append({"code":exc.code,"message":str(exc)})
    unresolved = [row for row in plan.mappings if row.status in {"ambiguous","unmapped","review","incompatible"}]
    if unresolved:
        warnings.append({"code":"replacement_has_review_items","message":f"Replacement plan contains {len(unresolved)} item(s) that were not auto-migrated. Explicit review confirms the author handled or intentionally dropped them in Builder."})
    return {"is_replacement": True, "ready": not blockers, "plan": model_to_dict(plan), "blockers": blockers, "warnings": warnings}


def migration_audit_from_plan(plan: SpaceReplacementPlan, *, published_at: str) -> SpaceMigrationAudit:
    if not plan.review_confirmed:
        raise SpaceVersioningError("Cannot publish migration audit before review confirmation.")
    return SpaceMigrationAudit(space_id=plan.space_id, from_version=plan.from_version, to_version=plan.to_version, published_at=published_at, replacement_plan_sha256=_canonical_sha(model_to_dict(plan)), mapping_summary=deepcopy(plan.summary), graph_diff=deepcopy(plan.graph_diff))


def migrate_space_variant(
    variant_id: str,
    *, target_version: str, replacement_draft_id: str, confirm_migration: bool,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    if confirm_migration is not True:
        raise SpaceVersioningError("Explicit Variant migration confirmation is required.", code="space_variant_migration_confirmation_required")
    plan = load_replacement_plan(replacement_draft_id, root_dir=root_dir)
    if plan is None or not plan.review_confirmed:
        raise SpaceVersioningError("Replacement plan must exist and be review-confirmed before migrating a Variant.", code="space_variant_migration_plan_not_ready")
    if str(target_version or "").strip() != plan.to_version:
        raise SpaceVersioningError("Variant migration target must match the replacement plan target version.")
    variant = load_space_variant(variant_id, root_dir=root_dir, require_ready=False)
    if variant.base_space_id != plan.space_id or variant.base_version != plan.from_version:
        raise SpaceVersioningError("Variant is not pinned to the replacement plan source version.", code="space_variant_migration_base_mismatch")
    anchor_map = {row.source_id: row.selected_candidate_id for row in plan.mappings if row.mapping_type=="anchor" and row.status=="mapped" and row.selected_candidate_id}
    preserved_inputs = {row.source_id for row in plan.mappings if row.mapping_type=="input" and row.status=="mapped"}
    preserved_stages = {row.source_id for row in plan.mappings if row.mapping_type=="stage" and row.status=="mapped"}
    addons: list[dict[str,Any]]=[]
    for addon in variant.graph_addons:
        data=model_to_dict(addon)
        if addon.lane=="module":
            translated={}
            for role, anchor_id in addon.anchor_ids.items():
                if anchor_id not in anchor_map:
                    raise SpaceVersioningError(f"Variant Graph Add-on anchor cannot migrate: {anchor_id}", code="space_variant_migration_incompatible")
                translated[role]=anchor_map[anchor_id]
            data["anchor_ids"]=translated
        else:
            if addon.raw_anchor_id not in anchor_map:
                raise SpaceVersioningError(f"Variant raw-node anchor cannot migrate: {addon.raw_anchor_id}", code="space_variant_migration_incompatible")
            data["raw_anchor_id"]=anchor_map[addon.raw_anchor_id]
        addons.append(data)
    if any(key not in preserved_inputs for key in variant.saved_values):
        raise SpaceVersioningError("Variant saved values reference inputs that were not preserved by the replacement plan.", code="space_variant_migration_incompatible")
    enabled=[sid for sid in variant.stage_defaults.enabled_stage_ids if sid in preserved_stages]
    if len(enabled)!=len(variant.stage_defaults.enabled_stage_ids):
        raise SpaceVersioningError("Variant stage defaults reference stages that were not preserved.", code="space_variant_migration_incompatible")
    preferred=variant.stage_defaults.preferred_stage_id
    if preferred and preferred not in preserved_stages:
        raise SpaceVersioningError("Variant preferred stage was not preserved.", code="space_variant_migration_incompatible")
    result=create_space_variant(
        name=f"{variant.name} · v{plan.to_version}", description=variant.description,
        base_space_id=plan.space_id, base_version=plan.to_version, graph_addons=addons,
        enabled_stage_ids=enabled, preferred_run_mode=variant.stage_defaults.preferred_run_mode,
        preferred_stage_id=preferred, saved_values=variant.saved_values,
        badge_label=variant.presentation.badge_label, description_override=variant.presentation.description_override,
        root_dir=root_dir,
    )
    return {"ok": True, "schema_version": SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION, "source_variant_id": variant.variant_id, "migrated_variant": result.get("variant") or result, "source_variant_mutated": False}


def versioning_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION,
        "service_version": SPACE_VERSIONING_SERVICE_VERSION,
        "immutable_published_versions": True,
        "active_version_pointer": True,
        "rollback_by_pointer": True,
        "workflow_replacement_comparison": True,
        "graph_source_lineage_diff": True,
        "binding_migration": True,
        "ambiguous_binding_auto_retarget": False,
        "anchor_migration": True,
        "stage_migration": True,
        "dynamic_control_migration": True,
        "variant_compatibility_review": True,
        "explicit_variant_migration": True,
        "variant_in_place_retarget": False,
        "replacement_review_required_before_publish": True,
        "published_package_mutation": False,
    }
