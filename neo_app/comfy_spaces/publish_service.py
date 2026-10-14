from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import uuid4

from neo_app.comfy_spaces.binding_engine import validate_space_bindings
from neo_app.comfy_spaces.builder_service import (
    SpaceBuilderError,
    compile_builder_draft,
    load_builder_draft,
)
from neo_app.comfy_spaces.stages import (
    parse_stage_sidecar,
    stage_sidecar_payload,
    validate_stages_against_manifest,
)
from neo_app.comfy_spaces.dynamic_controls import (
    dynamic_controls_payload,
    parse_dynamic_controls,
    validate_dynamic_controls,
)
from neo_app.comfy_spaces.graph_anchors import (
    graph_anchor_sidecar_payload,
    parse_graph_anchor_sidecar,
)
from neo_app.comfy_spaces.contract import (
    load_space_manifest,
    manifest_payload,
    parse_space_manifest,
    validate_space_package,
    workflow_sha256,
)
from neo_app.comfy_spaces.paths import (
    SPACE_GRAPH_ANCHORS_FILENAME,
    SPACE_STAGES_FILENAME,
    SPACE_DYNAMIC_CONTROLS_FILENAME,
    SPACE_MANIFEST_FILENAME,
    SPACE_PUBLICATION_RECEIPT_FILENAME,
    SPACE_MIGRATION_FILENAME,
    SPACE_UI_FILENAME,
    SPACE_WORKFLOW_FILENAME,
    comfy_space_draft_analysis_path,
    comfy_space_draft_root,
    comfy_space_draft_workflow_path,
    comfy_space_graph_anchors_path,
    comfy_space_stages_path,
    comfy_space_dynamic_controls_path,
    comfy_space_manifest_path,
    comfy_space_publication_receipt_path,
    comfy_space_migration_path,
    comfy_space_ui_path,
    comfy_space_version_root,
    comfy_space_workflow_path,
)
from neo_app.comfy_spaces.publish_schema import (
    SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION,
    ComfySpacePublicationReceipt,
)
from neo_app.comfy_spaces.versioning import (
    activate_space_version,
    load_replacement_plan,
    migration_audit_from_plan,
    replacement_publish_guard,
)
from neo_app.comfy_spaces.space_ui import (
    load_space_ui,
    space_ui_payload,
    validate_space_ui_against_manifest,
)
from neo_app.core.pydantic_compat import model_to_dict


SPACE_PUBLISH_GATE_SCHEMA_VERSION = "neo.comfy_space.publish_gate.v1"
SPACE_PUBLISH_SERVICE_VERSION = "neo.comfy_space.publish_service.v1"
_PRESENTATION_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif"}


class SpacePublishError(ValueError):
    """Raised when publication cannot safely evaluate or promote a Builder draft."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpacePublishError(f"Unable to read {label}: {path}") from exc


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SpacePublishError(f"Unable to hash publication file: {path}") from exc
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _load_verified_analysis(draft_id: str, root_dir: Path | str | None) -> dict[str, Any]:
    # load_builder_draft(... verify=True) has already compared the frozen analysis
    # hash against draft provenance. Read the same frozen file here for gate-only
    # portability/dependency evidence; never refresh or mutate it during publish.
    payload = _read_json(comfy_space_draft_analysis_path(draft_id, root_dir), label="frozen Builder analysis")
    if not isinstance(payload, dict):
        raise SpacePublishError("Frozen Builder analysis must be a JSON object")
    return payload


def _referenced_presentation_assets(manifest: Mapping[str, Any]) -> list[str]:
    presentation = _mapping(manifest.get("presentation"))
    assets: list[str] = []
    for key in ("icon", "thumbnail"):
        value = str(presentation.get(key) or "").strip()
        if value and value not in assets:
            assets.append(value)
    return assets


def _presentation_asset_checks(
    draft_id: str,
    manifest: Mapping[str, Any],
    *,
    root_dir: Path | str | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    draft_root = comfy_space_draft_root(draft_id, root_dir).resolve()
    for relative in _referenced_presentation_assets(manifest):
        if Path(relative).suffix.lower() not in _PRESENTATION_IMAGE_EXTENSIONS:
            blockers.append(_issue("presentation_asset_type_forbidden", f"Presentation assets must use a safe image format: {relative}"))
            continue
        source = draft_root / relative
        if source.is_symlink():
            blockers.append(_issue("presentation_asset_symlink", f"Presentation asset may not be a symlink: {relative}"))
            continue
        candidate = source.resolve()
        try:
            candidate.relative_to(draft_root)
        except ValueError:
            blockers.append(_issue("presentation_asset_path_escape", f"Presentation asset escapes the Builder draft package: {relative}"))
            continue
        if not candidate.is_file():
            blockers.append(_issue("presentation_asset_missing", f"Presentation asset is missing from the Builder draft: {relative}"))
    return blockers, warnings


def evaluate_builder_draft_publish_readiness(
    draft_id: str,
    *,
    root_dir: Path | str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Evaluate NCS-5D publication requirements without writing the library.

    Publication intentionally recompiles from the saved Builder draft rather than
    trusting ``preview_manifest.json`` / ``preview_ui.json``. This guarantees the
    gate evaluates the authoritative saved revision and frozen provenance.
    """

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    compiled: dict[str, Any] | None = None
    published_manifest: dict[str, Any] | None = None
    ui_payload: dict[str, Any] | None = None
    graph_anchors_payload: dict[str, Any] | None = None
    stages_payload: dict[str, Any] | None = None
    dynamic_controls_payload_value: dict[str, Any] | None = None

    try:
        draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
    except (SpaceBuilderError, ValueError) as exc:
        blockers.append(_issue("draft_provenance_invalid", str(exc)))
        return {
            "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
            "service_version": SPACE_PUBLISH_SERVICE_VERSION,
            "draft_id": str(draft_id or "").strip().lower(),
            "ready": False,
            "published": False,
            "execution_performed": False,
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "manifest": None,
            "ui": None,
            "graph_anchors": None,
            "stages": None,
            "dynamic_controls": None,
            "target": None,
        }

    checks.append({"check": "frozen_provenance", "status": "ready", "message": "Frozen Builder workflow/analysis provenance is intact."})
    if expected_revision is not None and expected_revision != draft.revision:
        blockers.append(
            _issue(
                "revision_conflict",
                f"Builder draft revision conflict: expected {expected_revision}, current {draft.revision}",
            )
        )
    else:
        checks.append({"check": "saved_revision", "status": "ready", "message": f"Saved draft revision {draft.revision} is authoritative."})

    try:
        compiled = compile_builder_draft(draft.draft_id, root_dir=root_dir, write_preview=False)
    except (SpaceBuilderError, ValueError) as exc:
        blockers.append(_issue("compile_failed", str(exc)))
        compiled = None

    if not compiled or not compiled.get("ok"):
        for raw in list((compiled or {}).get("errors") or []):
            item = _mapping(raw)
            blockers.append(_issue(str(item.get("code") or "compile_validation_failed"), str(item.get("message") or "Builder compile validation failed")))
    else:
        checks.append({"check": "compile", "status": "ready", "message": "Manifest/UI compile validation passed."})
        for raw in list(compiled.get("warnings") or []):
            item = _mapping(raw)
            warnings.append(_issue(str(item.get("code") or "compile_warning"), str(item.get("message") or "Builder compile warning")))

        manifest_candidate = deepcopy(_mapping(compiled.get("manifest")))
        manifest_candidate["status"] = "published"
        try:
            manifest_model = parse_space_manifest(manifest_candidate)
            published_manifest = manifest_payload(manifest_model)
        except Exception as exc:  # manifest enforces at least one output for published state
            blockers.append(_issue("published_manifest_invalid", str(exc)))
        try:
            ui_payload = deepcopy(_mapping(compiled.get("ui")))
            ui_validation = validate_space_ui_against_manifest(ui_payload, published_manifest or manifest_candidate)
            if not ui_validation.get("valid"):
                for raw in list(ui_validation.get("errors") or []):
                    item = _mapping(raw)
                    blockers.append(_issue(str(item.get("code") or "ui_validation_failed"), str(item.get("message") or "Space UI validation failed")))
            else:
                checks.append({"check": "ui_references", "status": "ready", "message": "Space UI references match published manifest IDs."})
        except Exception as exc:
            blockers.append(_issue("ui_validation_failed", str(exc)))

        try:
            graph_anchors_payload = deepcopy(_mapping(compiled.get("graph_anchors")))
            graph_anchor_model = parse_graph_anchor_sidecar(graph_anchors_payload)
            if graph_anchor_model.space_id != draft.identity.space_id or graph_anchor_model.version != draft.identity.version:
                raise SpacePublishError("Compiled graph anchor sidecar identity/version does not match the Builder draft")
            checks.append(
                {
                    "check": "graph_anchors",
                    "status": "ready",
                    "message": f"Graph anchor sidecar validated with {len(graph_anchor_model.anchors)} author-confirmed anchor(s).",
                }
            )
        except Exception as exc:
            blockers.append(_issue("graph_anchor_validation_failed", str(exc)))

        try:
            stages_payload = deepcopy(_mapping(compiled.get("stages")))
            stage_model = parse_stage_sidecar(stages_payload)
            stage_check = validate_stages_against_manifest(stage_model, published_manifest or manifest_candidate)
            if not stage_check.get("valid"):
                raise SpacePublishError("; ".join(item.get("message", "Stage validation failed") for item in stage_check.get("errors") or []))
            checks.append({"check": "stages", "status": "ready", "message": f"Stage sidecar validated with {len(stage_model.stages)} execution stage(s)."})
        except Exception as exc:
            blockers.append(_issue("stage_validation_failed", str(exc)))

        try:
            dynamic_controls_payload_value = deepcopy(_mapping(compiled.get("dynamic_controls")))
            dynamic_model = parse_dynamic_controls(dynamic_controls_payload_value)
            dynamic_check = validate_dynamic_controls(dynamic_model, published_manifest or manifest_candidate, ui_payload or {}, stage_model if 'stage_model' in locals() else None)
            if not dynamic_check.get("valid"):
                raise SpacePublishError("; ".join(item.get("message", "Dynamic control validation failed") for item in dynamic_check.get("errors") or []))
            checks.append({"check": "dynamic_controls", "status": "ready", "message": f"Dynamic controls validated with {len(dynamic_model.behaviors)} behavior rule(s) and {len(dynamic_model.collections)} collection(s)."})
            for item in dynamic_check.get("warnings") or []:
                warnings.append(_issue(str(item.get("code") or "dynamic_controls_warning"), str(item.get("message") or "Dynamic controls warning")))
        except Exception as exc:
            blockers.append(_issue("dynamic_controls_validation_failed", str(exc)))

    analysis: dict[str, Any] = {}
    try:
        analysis = _load_verified_analysis(draft.draft_id, root_dir)
    except SpacePublishError as exc:
        blockers.append(_issue("analysis_unreadable", str(exc)))

    if analysis:
        if analysis.get("ok") is not True:
            blockers.append(_issue("analysis_not_ready", "Frozen workflow analysis contains structural errors."))
        else:
            checks.append({"check": "analysis", "status": "ready", "message": "Frozen workflow analysis is structurally valid."})

        portability = _mapping(analysis.get("portability"))
        if portability.get("ready") is not True:
            issues = list(portability.get("issues") or [])
            if issues:
                for raw in issues:
                    item = _mapping(raw)
                    blockers.append(_issue(str(item.get("code") or "portability_blocker"), str(item.get("message") or "Unresolved portability blocker")))
            else:
                blockers.append(_issue("portability_blocked", "Frozen workflow portability is not ready for publication."))
        else:
            checks.append({"check": "portability", "status": "ready", "message": "No unresolved portability blocker is present."})

        dependencies = _mapping(analysis.get("dependencies"))
        preflight_state = str(dependencies.get("preflight_state") or "not_checked")
        if preflight_state == "blocked":
            for class_type in list(dependencies.get("missing_node_classes") or []):
                blockers.append(_issue("missing_custom_node", f"Selected runtime is missing required node class: {class_type}"))
            for raw in list(dependencies.get("missing_models") or []):
                item = _mapping(raw)
                name = str(item.get("configured_name") or item.get("requirement_id") or "required model")
                blockers.append(_issue("missing_model", f"Selected runtime is missing required model: {name}"))
            if not list(dependencies.get("missing_node_classes") or []) and not list(dependencies.get("missing_models") or []):
                blockers.append(_issue("dependency_preflight_blocked", "Selected runtime dependency preflight is blocked."))
        elif preflight_state == "ready":
            checks.append({"check": "dependencies", "status": "ready", "message": "Frozen selected-runtime dependency preflight was ready."})
        else:
            warnings.append(
                _issue(
                    "dependency_preflight_not_checked",
                    "Dependencies were not live-checked during import. Publication is portable, but the runtime must preflight dependencies before execution.",
                )
            )
            checks.append({"check": "dependencies", "status": "review", "message": "Dependency state is not known-blocked; execution-time preflight remains required."})

    if published_manifest is not None:
        trust = _mapping(published_manifest.get("trust"))
        trust_ok = (
            trust.get("declarative_only") is True
            and trust.get("neo_python_execution") is False
            and trust.get("neo_javascript_execution") is False
            and trust.get("automatic_dependency_install") is False
        )
        if not trust_ok:
            blockers.append(_issue("trust_contract_invalid", "Published Space trust contract would allow forbidden Neo execution/install behavior."))
        else:
            checks.append({"check": "trust", "status": "ready", "message": "Declarative-only trust contract forbids Neo code execution and automatic dependency installation."})

        asset_blockers, asset_warnings = _presentation_asset_checks(draft.draft_id, published_manifest, root_dir=root_dir)
        blockers.extend(asset_blockers)
        warnings.extend(asset_warnings)
        if not asset_blockers:
            checks.append({"check": "presentation_assets", "status": "ready", "message": "Referenced presentation assets are package-local and available."})

        workflow_path = comfy_space_draft_workflow_path(draft.draft_id, root_dir)
        expected_hash = str(_mapping(published_manifest.get("workflow")).get("sha256") or "")
        actual_hash = workflow_sha256(workflow_path)
        if expected_hash != actual_hash:
            blockers.append(_issue("workflow_hash_mismatch", "Published manifest workflow hash does not match the frozen Builder workflow."))
        else:
            checks.append({"check": "workflow_hash", "status": "ready", "message": "Published workflow hash matches frozen provenance."})

    replacement_guard = replacement_publish_guard(draft.draft_id, root_dir=root_dir)
    if replacement_guard.get("is_replacement"):
        if replacement_guard.get("ready"):
            checks.append({"check": "replacement_review", "status": "ready", "message": "Workflow replacement diff/migration review was explicitly confirmed."})
        for item in list(replacement_guard.get("blockers") or []):
            blockers.append(_issue(str(item.get("code") or "replacement_review_blocked"), str(item.get("message") or "Workflow replacement review is blocked.")))
        for item in list(replacement_guard.get("warnings") or []):
            warnings.append(_issue(str(item.get("code") or "replacement_review_warning"), str(item.get("message") or "Workflow replacement review warning.")))

    target = {
        "space_id": draft.identity.space_id,
        "version": draft.identity.version,
        "version_root": str(comfy_space_version_root(draft.identity.space_id, draft.identity.version, root_dir)),
    }
    if comfy_space_version_root(draft.identity.space_id, draft.identity.version, root_dir).exists():
        blockers.append(
            _issue(
                "version_already_published",
                f"Space '{draft.identity.space_id}' version '{draft.identity.version}' already exists. Bump the version instead of overwriting a published package.",
            )
        )
    else:
        checks.append({"check": "target_version", "status": "ready", "message": "Target Space version is not already published."})

    return {
        "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
        "service_version": SPACE_PUBLISH_SERVICE_VERSION,
        "draft_id": draft.draft_id,
        "revision": draft.revision,
        "space_id": draft.identity.space_id,
        "version": draft.identity.version,
        "ready": not blockers,
        "published": False,
        "execution_performed": False,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "manifest": published_manifest,
        "ui": ui_payload,
        "graph_anchors": graph_anchors_payload,
        "stages": stages_payload,
        "dynamic_controls": dynamic_controls_payload_value,
        "target": target,
    }


def publish_builder_draft(
    draft_id: str,
    *,
    expected_revision: int,
    confirm_publish: bool,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically promote one validated saved draft into the Space library.

    This copies only declarative package data. It never submits Comfy, transports
    generation assets, installs dependencies, or generates executable Neo code.
    """

    if not confirm_publish:
        raise SpacePublishError("Explicit publish confirmation is required")
    try:
        expected_revision = int(expected_revision)
    except (TypeError, ValueError) as exc:
        raise SpacePublishError("expected_revision must be an integer") from exc

    gate = evaluate_builder_draft_publish_readiness(
        draft_id,
        root_dir=root_dir,
        expected_revision=expected_revision,
    )
    if not gate.get("ready"):
        return {
            "ok": False,
            "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
            "service_version": SPACE_PUBLISH_SERVICE_VERSION,
            "published": False,
            "execution_performed": False,
            "gate": gate,
            "publication": None,
        }

    draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
    if draft.revision != expected_revision:
        gate["ready"] = False
        gate["blockers"].append(
            _issue(
                "revision_conflict",
                f"Builder draft revision changed after readiness evaluation: expected {expected_revision}, current {draft.revision}",
            )
        )
        return {
            "ok": False,
            "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
            "service_version": SPACE_PUBLISH_SERVICE_VERSION,
            "published": False,
            "execution_performed": False,
            "gate": gate,
            "publication": None,
        }
    manifest_payload_final = deepcopy(_mapping(gate.get("manifest")))
    ui_payload_final = deepcopy(_mapping(gate.get("ui")))
    graph_anchors_payload_final = deepcopy(_mapping(gate.get("graph_anchors")))
    stages_payload_final = deepcopy(_mapping(gate.get("stages")))
    dynamic_controls_payload_final = deepcopy(_mapping(gate.get("dynamic_controls")))
    replacement_plan = load_replacement_plan(draft_id, root_dir=root_dir)
    target_root = comfy_space_version_root(draft.identity.space_id, draft.identity.version, root_dir)
    if target_root.exists():
        # Recheck immediately before staging/rename; readiness is advisory until
        # the write path confirms the target is still unused.
        gate["ready"] = False
        gate["blockers"].append(_issue("version_already_published", "Target Space version appeared after readiness evaluation."))
        return {
            "ok": False,
            "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
            "service_version": SPACE_PUBLISH_SERVICE_VERSION,
            "published": False,
            "execution_performed": False,
            "gate": gate,
            "publication": None,
        }

    versions_root = target_root.parent
    versions_root.mkdir(parents=True, exist_ok=True)
    temp_root = versions_root / f".{target_root.name}.{uuid4().hex}.publish.tmp"
    temp_root.mkdir(parents=False, exist_ok=False)

    try:
        source_workflow = comfy_space_draft_workflow_path(draft.draft_id, root_dir)
        staged_workflow = temp_root / SPACE_WORKFLOW_FILENAME
        shutil.copyfile(source_workflow, staged_workflow)
        _write_json(temp_root / SPACE_MANIFEST_FILENAME, manifest_payload_final)
        _write_json(temp_root / SPACE_UI_FILENAME, ui_payload_final)
        _write_json(temp_root / SPACE_GRAPH_ANCHORS_FILENAME, graph_anchors_payload_final)
        _write_json(temp_root / SPACE_STAGES_FILENAME, stages_payload_final)
        _write_json(temp_root / SPACE_DYNAMIC_CONTROLS_FILENAME, dynamic_controls_payload_final)
        migration_audit = None
        migration_payload = None
        publication_time = _now_iso()
        if replacement_plan is not None:
            migration_audit = migration_audit_from_plan(replacement_plan, published_at=publication_time)
            migration_payload = model_to_dict(migration_audit)
            _write_json(temp_root / SPACE_MIGRATION_FILENAME, migration_payload)

        draft_root = comfy_space_draft_root(draft.draft_id, root_dir).resolve()
        for relative in _referenced_presentation_assets(manifest_payload_final):
            source_path = draft_root / relative
            if source_path.is_symlink():
                raise SpacePublishError(f"Presentation asset became a symlink before publication: {relative}")
            source_asset = source_path.resolve()
            try:
                source_asset.relative_to(draft_root)
            except ValueError as exc:
                raise SpacePublishError(f"Presentation asset escapes the Builder draft package: {relative}") from exc
            destination_asset = temp_root / relative
            destination_asset.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_asset, destination_asset)

        staged_manifest_path = temp_root / SPACE_MANIFEST_FILENAME
        package_validation = validate_space_package(staged_manifest_path)
        staged_manifest = load_space_manifest(staged_manifest_path)
        staged_ui = load_space_ui(temp_root / SPACE_UI_FILENAME)
        staged_graph_anchors = parse_graph_anchor_sidecar(
            _read_json(temp_root / SPACE_GRAPH_ANCHORS_FILENAME, label="staged graph anchors")
        )
        if staged_graph_anchors.space_id != staged_manifest.space_id or staged_graph_anchors.version != staged_manifest.version:
            raise SpacePublishError("Staged graph anchor sidecar does not match published Space identity/version")
        staged_stages = parse_stage_sidecar(_read_json(temp_root / SPACE_STAGES_FILENAME, label="staged stages"))
        stage_validation = validate_stages_against_manifest(staged_stages, staged_manifest)
        if not stage_validation.get("valid"):
            raise SpacePublishError("Staged stage sidecar does not match the published manifest")
        staged_dynamic_controls = parse_dynamic_controls(_read_json(temp_root / SPACE_DYNAMIC_CONTROLS_FILENAME, label="staged dynamic controls"))
        dynamic_validation = validate_dynamic_controls(staged_dynamic_controls, staged_manifest, staged_ui, staged_stages)
        if not dynamic_validation.get("valid"):
            raise SpacePublishError("Staged dynamic-control sidecar does not match the published Space contracts")
        ui_validation = validate_space_ui_against_manifest(staged_ui, staged_manifest)
        staged_workflow_payload = _read_json(staged_workflow, label="staged published workflow")
        binding_validation = validate_space_bindings(staged_workflow_payload, staged_manifest)
        if not package_validation.get("ok") or not ui_validation.get("valid") or not binding_validation.get("valid"):
            raise SpacePublishError("Staged Space package failed final package/UI/binding validation")
        if staged_manifest.status != "published":
            raise SpacePublishError("Staged Space manifest did not retain published lifecycle status")

        analysis = _load_verified_analysis(draft.draft_id, root_dir)
        dependencies = _mapping(analysis.get("dependencies"))
        receipt = ComfySpacePublicationReceipt(
            space_id=staged_manifest.space_id,
            version=staged_manifest.version,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            published_at=publication_time,
            manifest_sha256=_canonical_json_sha256(manifest_payload(staged_manifest)),
            workflow_sha256=_file_sha256(staged_workflow),
            ui_sha256=_canonical_json_sha256(space_ui_payload(staged_ui)),
            graph_anchors_sha256=_canonical_json_sha256(graph_anchor_sidecar_payload(staged_graph_anchors)),
            stages_sha256=_canonical_json_sha256(stage_sidecar_payload(staged_stages)),
            dynamic_controls_sha256=_canonical_json_sha256(dynamic_controls_payload(staged_dynamic_controls)),
            migration_sha256=_canonical_json_sha256(migration_payload) if migration_payload else "",
            replaces_version=replacement_plan.from_version if replacement_plan is not None else "",
            source_workflow_sha256=draft.provenance.frozen_workflow_sha256,
            source_analysis_sha256=draft.provenance.analysis_sha256,
            dependency_preflight_state=str(dependencies.get("preflight_state") or "not_checked"),
            dependency_preflight_checked=bool(dependencies.get("object_info_available")),
        )
        _write_json(temp_root / SPACE_PUBLICATION_RECEIPT_FILENAME, model_to_dict(receipt))

        # Final readback before promotion catches partial/corrupt staging writes.
        readback_receipt = _read_json(temp_root / SPACE_PUBLICATION_RECEIPT_FILENAME, label="publication receipt")
        if str(readback_receipt.get("schema_version") or "") != SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION:
            raise SpacePublishError("Publication receipt schema readback failed")
        latest_draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
        if latest_draft.revision != expected_revision:
            raise SpacePublishError(
                f"Builder draft revision changed during publication: expected {expected_revision}, current {latest_draft.revision}"
            )
        if target_root.exists():
            raise SpacePublishError("Target Space version was created concurrently; refusing overwrite")
        temp_root.rename(target_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    # Version state is mutable lifecycle metadata only; published packages remain immutable.
    try:
        activate_space_version(draft.identity.space_id, draft.identity.version, reason="publish", root_dir=root_dir)
    except Exception as exc:
        # The immutable package is already safely published. Active-version resolution
        # falls back to highest valid semver if lifecycle metadata cannot be updated.
        gate.setdefault("warnings", []).append(_issue("active_version_state_update_failed", f"Published successfully, but active-version metadata could not be updated: {exc}"))

    publication = {
        "space_id": draft.identity.space_id,
        "version": draft.identity.version,
        "version_root": str(target_root),
        "manifest_path": str(comfy_space_manifest_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "workflow_path": str(comfy_space_workflow_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "ui_path": str(comfy_space_ui_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "graph_anchors_path": str(comfy_space_graph_anchors_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "stages_path": str(comfy_space_stages_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "dynamic_controls_path": str(comfy_space_dynamic_controls_path(draft.identity.space_id, draft.identity.version, root_dir)),
        "migration_path": str(comfy_space_migration_path(draft.identity.space_id, draft.identity.version, root_dir)) if replacement_plan is not None else "",
        "replaces_version": replacement_plan.from_version if replacement_plan is not None else "",
        "receipt_path": str(comfy_space_publication_receipt_path(draft.identity.space_id, draft.identity.version, root_dir)),
    }
    return {
        "ok": True,
        "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
        "service_version": SPACE_PUBLISH_SERVICE_VERSION,
        "published": True,
        "execution_performed": False,
        "generated_neo_code": False,
        "automatic_dependency_install": False,
        "gate": gate,
        "publication": publication,
        "receipt": _read_json(Path(publication["receipt_path"]), label="published receipt"),
    }


def publish_service_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_PUBLISH_GATE_SCHEMA_VERSION,
        "service_version": SPACE_PUBLISH_SERVICE_VERSION,
        "publication_receipt_schema_version": SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION,
        "publish_gate_implemented": True,
        "recompiles_saved_draft": True,
        "trusts_preview_files": False,
        "requires_expected_revision": True,
        "requires_explicit_confirmation": True,
        "blocks_existing_version_overwrite": True,
        "replacement_review_required": True,
        "publishes_migration_audit": True,
        "activates_new_immutable_version": True,
        "atomic_version_promotion": True,
        "final_staged_package_validation": True,
        "publishes_graph_anchor_sidecar": True,
        "graph_anchor_sidecar_author_confirmed_only": True,
        "graph_anchor_sidecar_receipt_hashed": True,
        "publishes_dynamic_controls_sidecar": True,
        "dynamic_controls_receipt_hashed": True,
        "dynamic_controls_generated_javascript": False,
        "blocks_portability_issues": True,
        "blocks_known_dependency_failures": True,
        "allows_dependency_not_checked_with_warning": True,
        "copies_executable_neo_code": False,
        "automatic_dependency_install": False,
        "workflow_execution_implemented": False,
        "asset_transport_implemented": False,
        "public_space_runner_implemented": False,
    }
