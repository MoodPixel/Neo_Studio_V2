from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
from uuid import uuid4

from neo_app.comfy_spaces.binding_engine import validate_space_bindings
from neo_app.comfy_spaces.builder_schema import (
    SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
    ComfySpaceBuilderDraft,
    SpaceBuilderAnchorDraft,
    SpaceBuilderIdentity,
    SpaceBuilderInputDraft,
    SpaceBuilderOutputDraft,
    SpaceBuilderProvenance,
    SpaceBuilderSectionDraft,
    SpaceBuilderStageDraft,
)
from neo_app.comfy_spaces.contract import (
    manifest_payload,
    parse_space_manifest,
    workflow_sha256,
)
from neo_app.comfy_spaces.graph_analyzer import SPACE_ANALYSIS_SCHEMA_VERSION
from neo_app.comfy_spaces.dynamic_controls import (
    SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION,
    ComfySpaceDynamicControls,
    dynamic_controls_payload,
    validate_dynamic_controls,
)
from neo_app.comfy_spaces.stages import (
    SpaceStageDefinition,
    SpaceStageSidecar,
    SPACE_STAGE_SCHEMA_VERSION,
    stage_sidecar_payload,
    validate_stages_against_manifest,
)
from neo_app.comfy_spaces.graph_anchors import (
    SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
    compile_confirmed_graph_anchors,
    graph_anchor_sidecar_payload,
)
from neo_app.comfy_spaces.importer import SPACE_IMPORT_RECORD_SCHEMA_VERSION
from neo_app.comfy_spaces.paths import (
    SPACE_BUILDER_DRAFT_FILENAME,
    SPACE_DRAFT_ANALYSIS_FILENAME,
    SPACE_WORKFLOW_FILENAME,
    comfy_space_builder_draft_path,
    comfy_space_draft_analysis_path,
    comfy_space_draft_preview_manifest_path,
    comfy_space_draft_preview_ui_path,
    comfy_space_draft_preview_dynamic_controls_path,
    comfy_space_draft_root,
    comfy_space_draft_workflow_path,
    comfy_space_import_root,
)
from neo_app.comfy_spaces.schema import ComfySpaceManifest
from neo_app.comfy_spaces.space_ui import (
    SPACE_UI_SCHEMA_VERSION,
    ComfySpaceUI,
    SpaceUIInputPresentation,
    SpaceUILayout,
    SpaceUIOutputPresentation,
    SpaceUISection,
    space_ui_payload,
    validate_space_ui_against_manifest,
)
from neo_app.comfy_spaces.type_system import (
    SpaceInputTypeDescriptor,
    SpaceOutputTypeDescriptor,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_BUILDER_SERVICE_VERSION = "neo.comfy_space.builder_service.v1"
_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")
_ASSET_KINDS = {"image", "mask", "video", "audio", "file"}
_ALLOWED_INPUT_CONTROLS = {
    "text": {"text_input", "text_area"},
    "integer": {"integer_input", "slider", "seed_input"},
    "number": {"number_input", "slider"},
    "boolean": {"toggle"},
    "choice": {"select", "search_select", "model_select"},
    "json": {"json_editor"},
    "image": {"image_upload"},
    "mask": {"mask_upload"},
    "video": {"video_upload"},
    "audio": {"audio_upload"},
    "file": {"file_upload"},
}
_ALLOWED_OUTPUT_RENDERERS = {
    "text": {"text_viewer"},
    "integer": {"numeric_value"},
    "number": {"numeric_value"},
    "boolean": {"boolean_value"},
    "choice": {"choice_value"},
    "json": {"json_viewer"},
    "image": {"image_preview"},
    "mask": {"mask_preview"},
    "video": {"video_player"},
    "audio": {"audio_player"},
    "file": {"file_download"},
}


class SpaceBuilderError(ValueError):
    """Raised when Builder draft provenance or compile invariants are violated."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceBuilderError(f"Unable to read {label}: {path}") from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _clear_preview_files(draft_id: str, root_dir: Path | str | None) -> None:
    for path in (
        comfy_space_draft_preview_manifest_path(draft_id, root_dir),
        comfy_space_draft_preview_ui_path(draft_id, root_dir),
        comfy_space_draft_preview_dynamic_controls_path(draft_id, root_dir),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SpaceBuilderError(f"Unable to invalidate stale Builder preview: {path}") from exc


def _validate_analysis_matches_workflow(workflow: Mapping[str, Any], analysis: Mapping[str, Any]) -> None:
    graph = _mapping(analysis.get("graph"))
    summary = _mapping(analysis.get("summary"))
    expected_counts = Counter(
        str(_mapping(node).get("class_type") or "").strip()
        for node in workflow.values()
        if isinstance(node, Mapping)
    )
    expected_counts.pop("", None)
    actual_counts = {str(key): int(value) for key, value in _mapping(graph.get("class_type_counts")).items()}
    if int(summary.get("node_count") or 0) != len(workflow) or actual_counts != dict(sorted(expected_counts.items())):
        raise SpaceBuilderError("Staged workflow analysis does not match the staged workflow graph")


def _safe_token(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = _TOKEN_RE.sub("_", text).strip("._-")
    return text[:128] or fallback


def _humanize(value: Any, fallback: str) -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    text = " ".join(part for part in text.split() if part)
    return text.title() if text else fallback


def _unique_id(base: str, seen: set[str]) -> str:
    clean = _safe_token(base, "item")
    candidate = clean
    suffix = 2
    while candidate in seen:
        tail = f"_{suffix}"
        candidate = f"{clean[: max(1, 128 - len(tail))]}{tail}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _parse_draft(payload: Mapping[str, Any]) -> ComfySpaceBuilderDraft:
    try:
        return model_from_dict(ComfySpaceBuilderDraft, dict(payload))
    except Exception as exc:
        raise SpaceBuilderError(str(exc)) from exc


def _load_import(import_id: str, root_dir: Path | str | None) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    import_root = comfy_space_import_root(import_id, root_dir)
    record = _read_json(import_root / "import_record.json", label="Comfy Space import record")
    workflow = _read_json(import_root / SPACE_WORKFLOW_FILENAME, label="staged Comfy workflow")
    analysis = _read_json(import_root / "analysis.json", label="staged Comfy analysis")

    if not isinstance(record, dict) or record.get("schema_version") != SPACE_IMPORT_RECORD_SCHEMA_VERSION:
        raise SpaceBuilderError("Import record is missing or uses an unsupported schema version")
    if not isinstance(workflow, dict):
        raise SpaceBuilderError("Staged Comfy workflow root must be a JSON object")
    if not isinstance(analysis, dict) or analysis.get("schema_version") != SPACE_ANALYSIS_SCHEMA_VERSION:
        raise SpaceBuilderError("Staged workflow analysis is missing or uses an unsupported schema version")
    if str(record.get("import_id") or "").strip().lower() != str(import_id or "").strip().lower():
        raise SpaceBuilderError("Import record id does not match its staging location")
    if str(record.get("analysis_schema_version") or "") != SPACE_ANALYSIS_SCHEMA_VERSION:
        raise SpaceBuilderError("Import record analysis schema does not match the staged analysis")
    _validate_analysis_matches_workflow(workflow, analysis)

    expected_canonical = str(record.get("canonical_workflow_sha256") or "").strip().lower()
    actual_canonical = _canonical_json_sha256(workflow)
    if not expected_canonical or expected_canonical != actual_canonical:
        raise SpaceBuilderError("Staged workflow no longer matches the import record canonical hash")
    return import_root, record, workflow, analysis


def _default_anchor_drafts(analysis: Mapping[str, Any]) -> list[SpaceBuilderAnchorDraft]:
    root = _mapping(analysis.get("graph_anchors"))
    drafts: list[SpaceBuilderAnchorDraft] = []
    for raw in list(root.get("proposals") or []):
        item = _mapping(raw)
        anchor_id = str(item.get("anchor_id") or "").strip().lower()
        semantic_role = str(item.get("semantic_role") or "").strip()
        if not anchor_id or not semantic_role:
            continue
        drafts.append(
            SpaceBuilderAnchorDraft(
                anchor_id=anchor_id,
                semantic_role=semantic_role,
                confirmed=False,
            )
        )
    return drafts


def _default_input_drafts(analysis: Mapping[str, Any]) -> list[SpaceBuilderInputDraft]:
    candidates = list(_mapping(analysis.get("candidates")).get("inputs") or [])
    drafts: list[SpaceBuilderInputDraft] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(candidates):
        item = _mapping(raw)
        candidate_id = str(item.get("candidate_id") or "").strip().lower()
        if not candidate_id:
            continue
        input_id = _unique_id(str(item.get("suggested_input_id") or item.get("field") or "input"), seen_ids)
        descriptor = _mapping(item.get("type_system"))
        kind = str(item.get("kind") or "json").strip().lower()
        help_text = str(_mapping(descriptor.get("constraints")).get("tooltip") or "").strip()
        if not help_text:
            reasons = [str(reason).strip() for reason in list(item.get("reasons") or []) if str(reason).strip()]
            help_text = "; ".join(reasons[:2])
        required_by_node = item.get("required_by_node")
        drafts.append(
            SpaceBuilderInputDraft(
                candidate_id=candidate_id,
                enabled=bool(item.get("default_exposed", item.get("recommended"))),
                input_id=input_id,
                label=_humanize(item.get("field"), "Input"),
                help_text=help_text,
                section_id="main",
                order=(index + 1) * 10,
                required=bool(required_by_node) if isinstance(required_by_node, bool) else False,
                multiplicity="single",
                default_mode="none" if kind in _ASSET_KINDS else "template",
            )
        )
    return drafts


def _default_node_sections(analysis: Mapping[str, Any], inputs: list[SpaceBuilderInputDraft]) -> list[SpaceBuilderSectionDraft]:
    """Organize controls by Comfy visual groups or functional roles, without rewiring nodes."""
    authoring = _mapping(analysis.get("authoring_map"))
    mode = str(authoring.get("recommended_starting_mode") or "semantic_role")
    groups = list(_mapping(authoring.get("groups")).get(mode) or [])
    suggestions = _mapping(authoring.get("candidate_sections"))
    if not groups:
        return [SpaceBuilderSectionDraft(section_id="main", title="Main", order=10)]
    sections: list[SpaceBuilderSectionDraft] = []
    mapped_ids: set[str] = set()
    group_order: dict[str, int] = {}
    for idx, raw in enumerate(groups):
        group = _mapping(raw)
        section_id = str(group.get("section_id") or "")
        if not section_id:
            continue
        sections.append(SpaceBuilderSectionDraft(
            section_id=section_id,
            title=str(group.get("title") or "Settings"),
            description=("ComfyUI canvas group · settings follow graph dependencies."
                         if mode == "workflow_group" else "Functional controls grouped by graph role."),
            order=(idx + 1) * 10,
            collapsible=True,
        ))
        mapped_ids.add(section_id)
        group_order[section_id] = idx
    fallback = False
    # Re-order controls by node dependency within each group. Keep all candidates.
    execution_nodes = list(_mapping(analysis.get("graph")).get("topological_order") or [])
    positions = {str(node): i for i, node in enumerate(execution_nodes)}
    candidates = {str(candidate.get("candidate_id")): candidate for candidate in list(_mapping(analysis.get("candidates")).get("inputs") or [])}
    for original_index, item in enumerate(inputs):
        mapped = _mapping(_mapping(suggestions.get(item.candidate_id)).get(mode))
        section_id = str(mapped.get("section_id") or "")
        if section_id not in mapped_ids:
            section_id = "main"
            fallback = True
        item.section_id = section_id
        candidate = _mapping(candidates.get(item.candidate_id))
        item.order = (positions.get(str(candidate.get("node_id") or ""), len(positions)) * 1000 + original_index)
    if fallback or not sections:
        sections.insert(0, SpaceBuilderSectionDraft(section_id="main", title="Other Settings", order=5))
    return sections


def _default_output_drafts(analysis: Mapping[str, Any]) -> list[SpaceBuilderOutputDraft]:
    candidates = list(_mapping(analysis.get("candidates")).get("outputs") or [])
    drafts: list[SpaceBuilderOutputDraft] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(candidates):
        item = _mapping(raw)
        candidate_id = str(item.get("candidate_id") or "").strip().lower()
        if not candidate_id:
            continue
        output_id = _unique_id(str(item.get("suggested_output_id") or item.get("kind") or "output"), seen_ids)
        enabled = index == 0
        output_name = str(item.get("output_name") or item.get("field") or item.get("kind") or "Result")
        drafts.append(
            SpaceBuilderOutputDraft(
                candidate_id=candidate_id,
                enabled=enabled,
                output_id=output_id,
                label=_humanize(output_name, "Result"),
                order=(index + 1) * 10,
                collection="first",
                primary=enabled,
            )
        )
    return drafts


def _default_stage_drafts(analysis: Mapping[str, Any]) -> list[SpaceBuilderStageDraft]:
    outputs = _default_output_drafts(analysis)
    enabled = [item.output_id for item in outputs if item.enabled]
    if not enabled and outputs:
        enabled = [outputs[0].output_id]
    if not enabled:
        return []
    return [SpaceBuilderStageDraft(
        stage_id="full", label="Full Workflow", order=10,
        target_output_ids=enabled, enabled_by_default=True, optional=False, can_run_alone=True,
    )]


def create_builder_draft_from_import(
    import_id: str,
    *,
    root_dir: Path | str | None = None,
    draft_id: str | None = None,
    space_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Freeze one staged NCS-2 import into an independent NCS-5A Builder draft.

    The draft owns copies of normalized ``workflow.json`` and ``analysis.json``.
    Nothing is executed, published, installed, or written into Extensions.
    """

    import_root, record, workflow, analysis = _load_import(import_id, root_dir)
    clean_draft_id = str(draft_id or uuid4().hex).strip().lower()
    draft_root = comfy_space_draft_root(clean_draft_id, root_dir)
    if draft_root.exists():
        raise SpaceBuilderError(f"Comfy Space Builder draft already exists: {clean_draft_id}")
    draft_root.parent.mkdir(parents=True, exist_ok=True)
    temp_root = draft_root.parent / f".{draft_root.name}.{uuid4().hex}.tmp"
    temp_root.mkdir(parents=False, exist_ok=False)

    try:
        workflow_path_temp = temp_root / SPACE_WORKFLOW_FILENAME
        analysis_path_temp = temp_root / SPACE_DRAFT_ANALYSIS_FILENAME
        shutil.copyfile(import_root / SPACE_WORKFLOW_FILENAME, workflow_path_temp)
        shutil.copyfile(import_root / "analysis.json", analysis_path_temp)

        frozen_workflow = _read_json(workflow_path_temp, label="frozen Builder workflow")
        frozen_analysis = _read_json(analysis_path_temp, label="frozen Builder analysis")
        frozen_canonical = _canonical_json_sha256(frozen_workflow)
        if frozen_canonical != str(record.get("canonical_workflow_sha256") or "").strip().lower():
            raise SpaceBuilderError("Frozen Builder workflow does not match staged import provenance")
        _validate_analysis_matches_workflow(frozen_workflow, frozen_analysis)

        source_name = str(record.get("source_name") or "workflow.json")
        source_stem = Path(source_name).stem
        resolved_space_id = _safe_token(space_id or source_stem, "comfy_space")
        resolved_name = str(name or _humanize(source_stem, "Comfy Space")).strip()
        now = _now_iso()

        default_inputs = _default_input_drafts(analysis)
        default_sections = _default_node_sections(analysis, default_inputs)
        draft = ComfySpaceBuilderDraft(
            draft_id=clean_draft_id,
            created_at=now,
            updated_at=now,
            revision=1,
            identity=SpaceBuilderIdentity(space_id=resolved_space_id, name=resolved_name),
            provenance=SpaceBuilderProvenance(
                import_id=str(record.get("import_id") or import_id),
                source_name=source_name,
                source_sha256=str(record.get("source_sha256") or ""),
                import_canonical_workflow_sha256=str(record.get("canonical_workflow_sha256") or ""),
                frozen_workflow_sha256=workflow_sha256(workflow_path_temp),
                analysis_sha256=_canonical_json_sha256(frozen_analysis),
                analysis_schema_version=str(frozen_analysis.get("schema_version") or SPACE_ANALYSIS_SCHEMA_VERSION),
                runtime_profile_id=str(record.get("runtime_profile_id") or ""),
                object_info_used=bool(record.get("object_info_used")),
            ),
            sections=default_sections,
            anchors=_default_anchor_drafts(analysis),
            stages=_default_stage_drafts(analysis),
            inputs=default_inputs,
            outputs=_default_output_drafts(analysis),
        )
        _atomic_write_json(temp_root / SPACE_BUILDER_DRAFT_FILENAME, model_to_dict(draft))
        temp_root.replace(draft_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    draft_path = comfy_space_builder_draft_path(clean_draft_id, root_dir)
    workflow_path = comfy_space_draft_workflow_path(clean_draft_id, root_dir)
    analysis_path = comfy_space_draft_analysis_path(clean_draft_id, root_dir)
    return {
        "ok": True,
        "schema_version": SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
        "service_version": SPACE_BUILDER_SERVICE_VERSION,
        "draft_id": clean_draft_id,
        "draft_root": str(draft_root),
        "draft_path": str(draft_path),
        "workflow_path": str(workflow_path),
        "analysis_path": str(analysis_path),
        "revision": draft.revision,
        "execution_performed": False,
        "published": False,
        "draft": model_to_dict(draft),
    }


def load_builder_draft(
    draft_id: str,
    *,
    root_dir: Path | str | None = None,
    verify_frozen_files: bool = True,
) -> ComfySpaceBuilderDraft:
    draft_path = comfy_space_builder_draft_path(draft_id, root_dir)
    payload = _read_json(draft_path, label="Comfy Space Builder draft")
    if not isinstance(payload, dict):
        raise SpaceBuilderError("Comfy Space Builder draft must be a JSON object")
    draft = _parse_draft(payload)
    if draft.draft_id != str(draft_id).strip().lower():
        raise SpaceBuilderError("Builder draft id does not match its storage location")
    if verify_frozen_files:
        _verify_frozen_provenance(draft, root_dir=root_dir)
    return draft


def save_builder_draft(
    draft_id: str,
    payload: Mapping[str, Any],
    *,
    root_dir: Path | str | None = None,
    expected_revision: int | None = None,
) -> ComfySpaceBuilderDraft:
    """Persist authoring state without allowing provenance/file authority changes.

    ``expected_revision`` gives NCS-5B an optimistic-concurrency seam so two
    Builder tabs cannot silently overwrite each other. A successful edit always
    invalidates previously compiled preview files.
    """

    current = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
    if expected_revision is not None and expected_revision != current.revision:
        raise SpaceBuilderError(
            f"Builder draft revision conflict: expected {expected_revision}, current {current.revision}"
        )
    candidate_payload = deepcopy(dict(payload))
    candidate_payload["draft_id"] = current.draft_id
    candidate_payload["created_at"] = current.created_at
    candidate_payload["updated_at"] = _now_iso()
    candidate_payload["revision"] = current.revision + 1
    candidate_payload["provenance"] = model_to_dict(current.provenance)
    candidate_payload["execution_performed"] = False
    candidate_payload["human_review_required"] = True
    draft = _parse_draft(candidate_payload)
    _clear_preview_files(draft_id, root_dir)
    _atomic_write_json(comfy_space_builder_draft_path(draft_id, root_dir), model_to_dict(draft))
    return draft


def _verify_frozen_provenance(
    draft: ComfySpaceBuilderDraft,
    *,
    root_dir: Path | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow_path = comfy_space_draft_workflow_path(draft.draft_id, root_dir)
    analysis_path = comfy_space_draft_analysis_path(draft.draft_id, root_dir)
    workflow = _read_json(workflow_path, label="frozen Builder workflow")
    analysis = _read_json(analysis_path, label="frozen Builder analysis")
    if not isinstance(workflow, dict):
        raise SpaceBuilderError("Frozen Builder workflow root must be a JSON object")
    if not isinstance(analysis, dict):
        raise SpaceBuilderError("Frozen Builder analysis root must be a JSON object")
    if workflow_sha256(workflow_path) != draft.provenance.frozen_workflow_sha256:
        raise SpaceBuilderError("Frozen Builder workflow hash changed after draft creation")
    if _canonical_json_sha256(workflow) != draft.provenance.import_canonical_workflow_sha256:
        raise SpaceBuilderError("Frozen Builder workflow no longer matches import provenance")
    if _canonical_json_sha256(analysis) != draft.provenance.analysis_sha256:
        raise SpaceBuilderError("Frozen Builder analysis hash changed after draft creation")
    if str(analysis.get("schema_version") or "") != draft.provenance.analysis_schema_version:
        raise SpaceBuilderError("Frozen Builder analysis schema no longer matches provenance")
    _validate_analysis_matches_workflow(workflow, analysis)
    return workflow, analysis


def _candidate_indexes(analysis: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    root = _mapping(analysis.get("candidates"))
    inputs = {
        str(_mapping(item).get("candidate_id") or "").strip().lower(): _mapping(item)
        for item in list(root.get("inputs") or [])
        if str(_mapping(item).get("candidate_id") or "").strip()
    }
    outputs = {
        str(_mapping(item).get("candidate_id") or "").strip().lower(): _mapping(item)
        for item in list(root.get("outputs") or [])
        if str(_mapping(item).get("candidate_id") or "").strip()
    }
    return inputs, outputs


def _manifest_default(draft: SpaceBuilderInputDraft, candidate: Mapping[str, Any]) -> Any:
    if draft.default_mode == "none":
        return None
    if draft.default_mode == "custom":
        return deepcopy(draft.default)
    if str(candidate.get("kind") or "").strip().lower() in _ASSET_KINDS:
        return None
    return deepcopy(candidate.get("current_value"))


def _compile_dependencies(analysis: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    dependency_root = _mapping(analysis.get("dependencies"))
    warnings: list[dict[str, str]] = []
    custom_nodes: list[dict[str, Any]] = []
    seen_classes: set[tuple[str, str]] = set()
    for raw in list(dependency_root.get("node_requirements") or []):
        item = _mapping(raw)
        if str(item.get("origin") or "") == "core":
            continue
        class_type = str(item.get("class_type") or "").strip()
        if not class_type:
            continue
        package = str(item.get("package_hint") or "").strip() or None
        key = (class_type, package or "")
        if key in seen_classes:
            continue
        seen_classes.add(key)
        custom_nodes.append({"class_type": class_type, "package": package, "required": True})

    models: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    portability = list(_mapping(analysis.get("portability")).get("issues") or [])
    nonportable_model_sources = {
        (str(_mapping(item).get("node_id") or ""), str(_mapping(item).get("field") or ""))
        for item in portability
        if str(_mapping(item).get("code") or "") == "absolute_machine_path"
    }
    for raw in list(dependency_root.get("model_requirements") or []):
        item = _mapping(raw)
        requirement_id = str(item.get("requirement_id") or "").strip().lower()
        if not requirement_id or requirement_id in seen_models:
            continue
        seen_models.add(requirement_id)
        source = (str(item.get("node_id") or ""), str(item.get("field") or ""))
        configured_name = str(item.get("configured_name") or "").strip() or None
        if source in nonportable_model_sources or item.get("portable") is False:
            configured_name = None
            warnings.append(
                {
                    "code": "nonportable_model_name_omitted",
                    "message": f"Model requirement '{requirement_id}' keeps its source locator but omits a machine-specific configured path in preview manifest.",
                }
            )
        models.append(
            {
                "requirement_id": requirement_id,
                "model_type": str(item.get("model_type") or "model"),
                "configured_name": configured_name,
                "source_node_id": source[0] or None,
                "source_field": source[1] or None,
                "required": True,
            }
        )
    return {"custom_nodes": custom_nodes, "models": models}, warnings


def compile_builder_draft(
    draft_id: str,
    *,
    root_dir: Path | str | None = None,
    write_preview: bool = True,
) -> dict[str, Any]:
    """Compile one Builder draft into manifest + durable UI preview contracts.

    Compilation reads only frozen draft data, never runs Comfy, and never writes
    to the published Space library. NCS-5D owns publication as a separate gate
    that recompiles from this saved-draft authority rather than trusting previews.
    """

    draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=False)
    if write_preview:
        _clear_preview_files(draft.draft_id, root_dir)
    workflow, analysis = _verify_frozen_provenance(draft, root_dir=root_dir)
    input_candidates, output_candidates = _candidate_indexes(analysis)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    graph_anchor_sidecar, graph_anchor_issues = compile_confirmed_graph_anchors(
        space_id=draft.identity.space_id,
        version=draft.identity.version,
        anchor_drafts=[model_to_dict(item) for item in draft.anchors],
        analysis=analysis,
    )
    errors.extend(graph_anchor_issues)

    stage_sidecar = SpaceStageSidecar(
        space_id=draft.identity.space_id,
        version=draft.identity.version,
        stages=[SpaceStageDefinition(**model_to_dict(item)) for item in draft.stages],
    )

    dynamic_controls = ComfySpaceDynamicControls(
        space_id=draft.identity.space_id,
        version=draft.identity.version,
        behaviors=[deepcopy(item) for item in draft.dynamic_behaviors],
        collections=[deepcopy(item) for item in draft.dynamic_collections],
    )

    graph_nodes = {
        str(_mapping(node).get("node_id") or ""): _mapping(node)
        for node in list(_mapping(analysis.get("graph")).get("nodes") or [])
    }
    manifest_inputs: list[dict[str, Any]] = []
    ui_inputs: list[SpaceUIInputPresentation] = []
    for item in draft.inputs:
        if not item.enabled:
            continue
        candidate = input_candidates.get(item.candidate_id)
        if candidate is None:
            errors.append(
                {"code": "unknown_input_candidate", "message": f"Draft input '{item.input_id}' references missing analysis candidate '{item.candidate_id}'."}
            )
            continue
        try:
            descriptor = model_from_dict(SpaceInputTypeDescriptor, _mapping(candidate.get("type_system")))
        except Exception as exc:
            errors.append(
                {"code": "invalid_input_descriptor", "message": f"Draft input '{item.input_id}' has invalid frozen type descriptor: {exc}"}
            )
            continue
        control = item.control_override or descriptor.control
        kind = str(candidate.get("kind") or descriptor.kind)
        if control not in _ALLOWED_INPUT_CONTROLS.get(kind, set()):
            errors.append(
                {
                    "code": "incompatible_input_control",
                    "message": f"Input '{item.input_id}' kind '{kind}' cannot use control '{control}'.",
                }
            )
            continue
        if item.multiplicity == "multiple" and not descriptor.supports_multiple:
            errors.append(
                {
                    "code": "unsupported_input_multiplicity",
                    "message": f"Input '{item.input_id}' does not support multiple values.",
                }
            )
            continue
        constraints = item.constraints_override or descriptor.constraints
        options = item.options_override or descriptor.options
        manifest_inputs.append(
            {
                "input_id": item.input_id,
                "kind": kind,
                "required": item.required,
                "multiplicity": item.multiplicity,
                "default": _manifest_default(item, candidate),
                "binding": {
                    "node_id": str(candidate.get("node_id") or ""),
                    "field": str(candidate.get("field") or ""),
                },
                "ui": {
                    "label": item.label,
                    "help_text": item.help_text,
                    "group": item.section_id,
                    "order": item.order,
                    "advanced": item.advanced,
                    "hidden": False,
                    "renderer_hint": control,
                },
            }
        )
        ui_inputs.append(
            SpaceUIInputPresentation(
                input_id=item.input_id,
                control=control,
                label=item.label,
                help_text=item.help_text,
                section_id=item.section_id,
                order=item.order,
                advanced=item.advanced,
                semantic_role=descriptor.semantic_role,
                source_node_id=str(candidate.get("node_id") or "")[:128],
                source_node_label=str(_mapping(graph_nodes.get(str(candidate.get("node_id") or ""))).get("title") or candidate.get("class_type") or "Node")[:160],
                model_type=descriptor.model_type,
                accepted_mime_types=descriptor.accepted_mime_types,
                supports_multiple=descriptor.supports_multiple,
                constraints=constraints,
                options=options,
            )
        )

    manifest_outputs: list[dict[str, Any]] = []
    ui_outputs: list[SpaceUIOutputPresentation] = []
    for item in draft.outputs:
        if not item.enabled:
            continue
        candidate = output_candidates.get(item.candidate_id)
        if candidate is None:
            errors.append(
                {"code": "unknown_output_candidate", "message": f"Draft output '{item.output_id}' references missing analysis candidate '{item.candidate_id}'."}
            )
            continue
        try:
            descriptor = model_from_dict(SpaceOutputTypeDescriptor, _mapping(candidate.get("type_system")))
        except Exception as exc:
            errors.append(
                {"code": "invalid_output_descriptor", "message": f"Draft output '{item.output_id}' has invalid frozen type descriptor: {exc}"}
            )
            continue
        binding: dict[str, Any] = {"node_id": str(candidate.get("node_id") or "")}
        if candidate.get("field") is not None:
            binding["field"] = str(candidate.get("field"))
        if candidate.get("output_index") is not None:
            binding["output_index"] = int(candidate.get("output_index"))
        renderer = item.renderer_override or descriptor.renderer
        kind = str(candidate.get("kind") or descriptor.kind)
        if renderer not in _ALLOWED_OUTPUT_RENDERERS.get(kind, set()):
            errors.append(
                {
                    "code": "incompatible_output_renderer",
                    "message": f"Output '{item.output_id}' kind '{kind}' cannot use renderer '{renderer}'.",
                }
            )
            continue
        manifest_outputs.append(
            {
                "output_id": item.output_id,
                "kind": kind,
                "binding": binding,
                "collection": item.collection,
                "primary": item.primary,
                "label": item.label,
            }
        )
        ui_outputs.append(
            SpaceUIOutputPresentation(
                output_id=item.output_id,
                renderer=renderer,
                label=item.label,
                order=item.order,
                previewable=descriptor.previewable,
                downloadable=descriptor.downloadable,
            )
        )

    dependencies, dependency_warnings = _compile_dependencies(analysis)
    warnings.extend(dependency_warnings)
    if errors:
        return {
            "ok": False,
            "schema_version": SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
            "service_version": SPACE_BUILDER_SERVICE_VERSION,
            "draft_id": draft.draft_id,
            "revision": draft.revision,
            "execution_performed": False,
            "published": False,
            "manifest": None,
            "ui": None,
            "graph_anchors": None,
            "stages": None,
            "dynamic_controls": None,
            "binding_validation": None,
            "ui_validation": None,
            "errors": errors,
            "warnings": warnings,
        }

    manifest_payload_candidate = {
        "space_id": draft.identity.space_id,
        "name": draft.identity.name,
        "version": draft.identity.version,
        "description": draft.identity.description,
        "status": "draft",
        "author": model_to_dict(draft.identity.author),
        "presentation": model_to_dict(draft.identity.presentation),
        "workflow": {
            "format": "comfy_api_json",
            "path": SPACE_WORKFLOW_FILENAME,
            "sha256": workflow_sha256(comfy_space_draft_workflow_path(draft.draft_id, root_dir)),
            "immutable_template": True,
            "mutation_policy": "clone_then_bind",
        },
        "runtime": {
            "engine": "comfyui",
            "runtime_selection": "compatible_profile",
            "preferred_profile_id": draft.provenance.runtime_profile_id or None,
            "shared_queue": True,
            "dependency_preflight_required": True,
        },
        "inputs": manifest_inputs,
        "outputs": manifest_outputs,
        "dependencies": dependencies,
    }
    try:
        manifest: ComfySpaceManifest = parse_space_manifest(manifest_payload_candidate)
    except Exception as exc:
        errors.append({"code": "manifest_compile_failed", "message": str(exc)})
        return {
            "ok": False,
            "schema_version": SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
            "service_version": SPACE_BUILDER_SERVICE_VERSION,
            "draft_id": draft.draft_id,
            "revision": draft.revision,
            "execution_performed": False,
            "published": False,
            "manifest": None,
            "ui": None,
            "graph_anchors": None,
            "stages": None,
            "dynamic_controls": None,
            "binding_validation": None,
            "ui_validation": None,
            "errors": errors,
            "warnings": warnings,
        }

    ui = ComfySpaceUI(
        space_id=manifest.space_id,
        version=manifest.version,
        layout=SpaceUILayout(
            submit_label=draft.submit_label,
            results_title=draft.results_title,
            sections=[
                SpaceUISection(
                    section_id=item.section_id,
                    title=item.title,
                    description=item.description,
                    order=item.order,
                    advanced=item.advanced,
                    collapsible=item.collapsible,
                    default_collapsed=item.default_collapsed,
                )
                for item in draft.sections
            ],
        ),
        inputs=ui_inputs,
        outputs=ui_outputs,
    )

    binding_validation = validate_space_bindings(workflow, manifest)
    ui_validation = validate_space_ui_against_manifest(ui, manifest)
    stage_validation = validate_stages_against_manifest(stage_sidecar, manifest) if manifest.outputs else {"valid": True, "errors": [], "stage_count": len(stage_sidecar.stages), "deferred_no_outputs": True}
    dynamic_validation = validate_dynamic_controls(dynamic_controls, manifest, ui, stage_sidecar)
    if not binding_validation.get("valid"):
        for issue in list(binding_validation.get("errors") or []):
            issue_map = _mapping(issue)
            errors.append(
                {
                    "code": str(issue_map.get("code") or "binding_validation_failed"),
                    "message": str(issue_map.get("message") or "Compiled binding validation failed"),
                }
            )
    if not ui_validation.get("valid"):
        errors.extend(list(ui_validation.get("errors") or []))
    if not stage_validation.get("valid"):
        errors.extend(list(stage_validation.get("errors") or []))
    if not dynamic_validation.get("valid"):
        errors.extend(list(dynamic_validation.get("errors") or []))
    warnings.extend(list(dynamic_validation.get("warnings") or []))

    ok = not errors
    manifest_dict = manifest_payload(manifest)
    ui_dict = space_ui_payload(ui)
    preview_paths: dict[str, str] = {}
    if ok and write_preview:
        manifest_path = comfy_space_draft_preview_manifest_path(draft.draft_id, root_dir)
        ui_path = comfy_space_draft_preview_ui_path(draft.draft_id, root_dir)
        dynamic_path = comfy_space_draft_preview_dynamic_controls_path(draft.draft_id, root_dir)
        _atomic_write_json(manifest_path, manifest_dict)
        _atomic_write_json(ui_path, ui_dict)
        _atomic_write_json(dynamic_path, dynamic_controls_payload(dynamic_controls))
        preview_paths = {"manifest": str(manifest_path), "ui": str(ui_path), "dynamic_controls": str(dynamic_path)}

    portability = _mapping(analysis.get("portability"))
    dependencies_state = _mapping(analysis.get("dependencies"))
    return {
        "ok": ok,
        "schema_version": SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
        "ui_schema_version": SPACE_UI_SCHEMA_VERSION,
        "service_version": SPACE_BUILDER_SERVICE_VERSION,
        "draft_id": draft.draft_id,
        "revision": draft.revision,
        "execution_performed": False,
        "published": False,
        "preview_written": bool(ok and write_preview),
        "preview_paths": preview_paths,
        "manifest": manifest_dict,
        "ui": ui_dict,
        "graph_anchors": graph_anchor_sidecar_payload(graph_anchor_sidecar),
        "graph_anchor_schema_version": SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
        "stages": stage_sidecar_payload(stage_sidecar),
        "stage_schema_version": SPACE_STAGE_SCHEMA_VERSION,
        "stage_validation": stage_validation,
        "dynamic_controls": dynamic_controls_payload(dynamic_controls),
        "dynamic_controls_schema_version": SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION,
        "dynamic_controls_validation": dynamic_validation,
        "binding_validation": binding_validation,
        "ui_validation": ui_validation,
        "publish_readiness_hints": {
            "has_output": bool(manifest.outputs),
            "analysis_ok": bool(analysis.get("ok")),
            "portability_ready": bool(portability.get("ready")),
            "dependency_preflight_state": str(dependencies_state.get("preflight_state") or "not_checked"),
            "human_review_required": True,
            "draft_revision": draft.revision,
            "publish_gate_implemented": True,
            "confirmed_graph_anchor_count": len(graph_anchor_sidecar.anchors),
        },
        "errors": errors,
        "warnings": warnings,
    }


def builder_service_capabilities() -> dict[str, Any]:
    return {
        "service_version": SPACE_BUILDER_SERVICE_VERSION,
        "draft_schema_version": SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
        "ui_schema_version": SPACE_UI_SCHEMA_VERSION,
        "creates_draft_from_ncs2_import": True,
        "freezes_workflow_and_analysis": True,
        "verifies_provenance_hashes": True,
        "optimistic_draft_revisioning": True,
        "stale_preview_invalidation": True,
        "compiles_preview_manifest": True,
        "compiles_preview_ui": True,
        "compiles_dynamic_controls": True,
        "dynamic_controls_declarative_only": True,
        "space_manifest_v1_unchanged": True,
        "workflow_execution_implemented": False,
        "asset_transport_implemented": False,
        "publish_gate_implemented": True,
        "publish_gate_owner": "neo.comfy_space.publish_service.v1",
        "admin_builder_workspace_implemented": True,
        "all_detected_candidates_retained": True,
        "candidate_exposure_recommendations_advisory": True,
        "node_aware_control_recommendations": True,
        "recommendation_policy": "node_aware_v2",
        "runtime_model_catalog_resolution": True,
        "runtime_model_catalog_authority": "comfy_object_info_combo",
        "graph_anchor_analysis": True,
        "graph_anchor_schema_version": SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
        "graph_anchor_author_confirmation": True,
        "graph_anchor_auto_confirmation": False,
        "node_aware_authoring_map": True,
        "section_grouping_modes": ["manual", "workflow_group", "semantic_role", "source_node", "source_subgraph"],
        "sections_change_execution": False,
        "stage_authoring": True,
        "stage_schema_version": SPACE_STAGE_SCHEMA_VERSION,
        "stage_execution_implemented": True,
        "graph_mutation_implemented": False,
        "public_space_runner_implemented": False,
    }
