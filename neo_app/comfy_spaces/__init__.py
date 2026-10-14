"""Neo Comfy Spaces declarative workflow application subsystem.

NCS-1 defines the manifest/trust/storage contract. NCS-2 adds safe Comfy
workflow import/normalization (normal Save JSON preferred; API JSON also accepted), native nested-subgraph flattening with preserved source lineage, graph analysis, candidate binding discovery, and optional
read-only live ``/object_info`` dependency comparison. NCS-3 adds the universal
media-agnostic input-control/output-renderer type system. NCS-4 adds strict
clone-then-bind workflow input injection plus raw output-locator resolution. NCS-5A
adds the durable Space UI contract and Builder draft/service foundation. NCS-5B
adds the visible Admin Comfy Space Builder workspace for import, inspection, draft
authoring, and non-executing preview compilation. NCS-5C adds the one shared
declarative browser renderer used by Builder Preview and reserved for the later
public Comfy Spaces runner. NCS-5D adds the fail-closed publish gate that promotes
validated declarative packages into the Space library without running Comfy. NCS-6 adds
the neutral asset/media bus plus provider-owned Comfy input/output transport. NCS-7 adds
the universal selected-runtime execution authority that revalidates published packages, uses the
shared GPU/job infrastructure, queues Comfy, and collects declared outputs through NCS-6. NCS-8 adds
persistent long-running run recovery plus live graph/node progress backed by Comfy websocket telemetry
with history/queue reconciliation. NCS-9 mounts the real top-level Comfy Spaces library/runner by composing the shared renderer, neutral asset bus, execution runtime, and persistent progress services without introducing a second scheduler or renderer. NCS-10A adds read-only, runtime-schema-backed graph anchor proposals plus a node/subgraph-aware authoring map; authors may confirm anchor metadata and regroup controls without inserting, removing, bypassing, or rewiring Comfy nodes. NCS-10B adds per-run Graph Add-ons: reviewed modules and a fail-closed raw single-transform node lane may now compose real Comfy nodes onto an execution clone only, using confirmed anchors and live runtime socket schemas while leaving the published workflow immutable. NCS-10C adds explicit stage-aware execution boundaries over the prepared clone. NCS-10D adds local Space Variants pinned to one immutable published Space/version/fingerprint; Variants can preserve declarative add-on/stage/value overlays without cloning the base workflow. NCS-11 adds signed declarative conditional/dynamic-control metadata for visibility, enabled state, repeaters, and reference lists without giving UI metadata graph, binding, or JavaScript execution authority. NCS-12 adds persistent selected-runtime dependency health snapshots with node/model inventory fingerprints, package hints, stage-specific readiness, Graph Add-on readiness, and stale/changed-runtime detection while preserving live queue-time preflight as the final authority. NCS-13 adds immutable version lifecycle state, explicit workflow-replacement comparison/migration review, rollback by active-version pointer, migration audit metadata, and explicit Variant migration without silently retargeting ambiguous bindings.

Importer/runtime-probe exports are loaded lazily so the lightweight path contract
can be imported during ``runtime_data`` bootstrap without creating a provider ↔
runtime-data circular import.
"""

from neo_app.comfy_spaces.asset_schema import (
    SPACE_ASSET_BUS_SCHEMA_VERSION,
    SPACE_ASSET_RECORD_SCHEMA_VERSION,
    ComfySpaceAssetRecord,
    SpaceAssetBindingResolutionReport,
    SpaceAssetCleanupReport,
    SpaceAssetCollectionReport,
    SpaceAssetIngestReport,
)
from neo_app.comfy_spaces.builder_schema import (
    SPACE_BUILDER_DRAFT_SCHEMA_VERSION,
    ComfySpaceBuilderDraft,
)
from neo_app.comfy_spaces.dynamic_controls import (
    SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION,
    ComfySpaceDynamicControls,
    SpaceDynamicBehavior,
    SpaceDynamicCollection,
    SpaceDynamicCondition,
    SpaceDynamicControlsError,
    SpaceDynamicPredicate,
    dynamic_controls_payload,
    evaluate_dynamic_condition,
    evaluate_dynamic_controls,
    load_dynamic_controls,
    parse_dynamic_controls,
    validate_dynamic_controls,
)
from neo_app.comfy_spaces.binding_engine import (
    SPACE_ASSET_REFERENCE_TYPE,
    SPACE_BINDING_ENGINE_SCHEMA_VERSION,
    SpaceAssetReference,
    SpaceBindingError,
    bind_space_inputs,
    binding_engine_capabilities,
    resolve_space_outputs,
    validate_space_bindings,
)
from neo_app.comfy_spaces.contract import (
    SpaceContractError,
    load_space_manifest,
    manifest_payload,
    parse_space_manifest,
    validate_space_package,
    workflow_sha256,
)
from neo_app.comfy_spaces.execution_schema import (
    SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION,
    SpaceExecutionIssue,
    SpaceExecutionOutputRecord,
    SpaceExecutionPreflightReport,
    SpaceExecutionRunResult,
    SpaceRunProgressEvent,
    SpaceRunProgressSnapshot,
)
from neo_app.comfy_spaces.authoring_map import (
    SPACE_AUTHORING_MAP_SCHEMA_VERSION,
    authoring_map_capabilities,
    build_authoring_map,
)
from neo_app.comfy_spaces.graph_anchors import (
    SPACE_GRAPH_ANCHOR_ANALYSIS_VERSION,
    SPACE_GRAPH_ANCHOR_SCHEMA_VERSION,
    ComfySpaceGraphAnchors,
    SpaceGraphAnchorDefinition,
    analyze_graph_anchors,
    graph_anchor_capabilities,
    parse_graph_anchor_sidecar,
)
from neo_app.comfy_spaces.graph_analyzer import (
    SPACE_ANALYSIS_SCHEMA_VERSION,
    SpaceWorkflowAnalysisError,
    analyze_comfy_workflow,
    normalize_comfy_api_workflow,
    normalize_comfy_workflow,
)
from neo_app.comfy_spaces.paths import (
    COMFY_SPACE_RUNTIME_DIRECTORIES,
    SPACE_BUILDER_DRAFT_FILENAME,
    SPACE_DRAFT_ANALYSIS_FILENAME,
    SPACE_DYNAMIC_CONTROLS_FILENAME,
    SPACE_MANIFEST_FILENAME,
    SPACE_GRAPH_ANCHORS_FILENAME,
    SPACE_PREVIEW_DYNAMIC_CONTROLS_FILENAME,
    SPACE_PREVIEW_MANIFEST_FILENAME,
    SPACE_PREVIEW_UI_FILENAME,
    SPACE_PUBLICATION_RECEIPT_FILENAME,
    SPACE_MIGRATION_FILENAME,
    SPACE_REPLACEMENT_PLAN_FILENAME,
    SPACE_VERSION_STATE_FILENAME,
    SPACE_UI_FILENAME,
    SPACE_WORKFLOW_FILENAME,
    comfy_space_asset_record_path,
    comfy_space_asset_storage_dir,
    comfy_space_builder_draft_path,
    comfy_space_draft_analysis_path,
    comfy_space_draft_preview_dynamic_controls_path,
    comfy_space_draft_preview_manifest_path,
    comfy_space_draft_preview_ui_path,
    comfy_space_draft_root,
    comfy_space_draft_workflow_path,
    comfy_space_import_root,
    comfy_space_dynamic_controls_path,
    comfy_space_dependency_health_path,
    comfy_space_dependency_health_version_root,
    comfy_space_graph_anchors_path,
    comfy_space_manifest_path,
    comfy_space_publication_receipt_path,
    comfy_space_migration_path,
    comfy_space_version_state_path,
    comfy_space_draft_replacement_plan_path,
    comfy_space_root,
    comfy_space_version_root,
    comfy_space_ui_path,
    comfy_space_workflow_path,
    comfy_spaces_assets_root,
    comfy_space_input_assets_root,
    comfy_space_output_assets_root,
    comfy_spaces_drafts_root,
    comfy_spaces_dependency_health_root,
    comfy_spaces_imports_root,
    comfy_spaces_library_root,
    comfy_spaces_runtime_root,
    comfy_spaces_variants_root,
    comfy_space_variant_path,
)
from neo_app.comfy_spaces.publish_schema import (
    SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION,
    ComfySpacePublicationReceipt,
)
from neo_app.comfy_spaces.space_ui import (
    SPACE_UI_SCHEMA_VERSION,
    ComfySpaceUI,
    SpaceUIContractError,
    load_space_ui,
    parse_space_ui,
    space_ui_payload,
    validate_space_ui_against_manifest,
)
from neo_app.comfy_spaces.subgraph_ir import (
    SUBGRAPH_IR_SCHEMA_VERSION,
)
from neo_app.comfy_spaces.type_system import (
    SPACE_TYPE_SYSTEM_SCHEMA_VERSION,
    SpaceInputTypeDescriptor,
    SpaceOutputTypeDescriptor,
    resolve_input_type_descriptor,
    resolve_output_type_descriptor,
    type_system_capabilities,
)
from neo_app.comfy_spaces.schema import (
    SPACE_MANIFEST_SCHEMA_VERSION,
    SPACE_RUNTIME_CONTRACT_VERSION,
    SPACE_TRUST_CONTRACT_VERSION,
    ComfySpaceManifest,
)

_LAZY_GRAPH_ADDON_EXPORTS = {
    "SPACE_GRAPH_ADDON_CATALOG_VERSION",
    "SPACE_GRAPH_ADDON_COMPOSITION_VERSION",
    "SPACE_GRAPH_ADDON_SCHEMA_VERSION",
    "SpaceGraphAddonError",
    "SpaceGraphAddonModuleDefinition",
    "SpaceGraphAddonRequest",
    "build_graph_addon_catalog",
    "compose_graph_addons",
    "parse_graph_addon_requests",
}

_LAZY_ASSET_BUS_EXPORTS = {
    "SPACE_ASSET_BUS_SERVICE_VERSION",
    "SpaceAssetBusError",
    "asset_bus_capabilities",
    "asset_content_path",
    "asset_content_url",
    "asset_public_payload",
    "asset_reference",
    "cleanup_expired_assets",
    "collect_comfy_artifact",
    "delete_asset",
    "ingest_asset_bytes",
    "ingest_asset_stream",
    "ingest_local_asset",
    "list_assets",
    "load_asset_record",
    "prepare_space_asset_bindings",
    "resolve_asset_for_comfy",
    "resolve_space_asset_inputs",
}

_LAZY_IMPORTER_EXPORTS = {
    "SPACE_IMPORT_RECORD_SCHEMA_VERSION",
    "SpaceWorkflowImportError",
    "import_comfy_workflow_file",
    "import_comfy_workflow_payload",
    "load_workflow_json",
}
_LAZY_BUILDER_EXPORTS = {
    "SPACE_BUILDER_SERVICE_VERSION",
    "SpaceBuilderError",
    "builder_service_capabilities",
    "compile_builder_draft",
    "create_builder_draft_from_import",
    "load_builder_draft",
    "save_builder_draft",
}

_LAZY_BUILDER_WORKSPACE_EXPORTS = {
    "SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION",
    "SpaceBuilderWorkspaceError",
    "builder_workspace_capabilities",
    "builder_workspace_payload",
    "check_builder_workspace_publish_readiness",
    "compile_builder_workspace_preview",
    "create_builder_workspace_from_workflow",
    "create_replacement_workspace_from_workflow",
    "confirm_builder_replacement_review",
    "list_builder_drafts",
    "publish_builder_workspace",
    "save_builder_workspace",
}


_LAZY_PUBLISH_EXPORTS = {
    "SPACE_PUBLISH_GATE_SCHEMA_VERSION",
    "SPACE_PUBLISH_SERVICE_VERSION",
    "SpacePublishError",
    "evaluate_builder_draft_publish_readiness",
    "publish_builder_draft",
    "publish_service_capabilities",
}

_LAZY_EXECUTION_RUNTIME_EXPORTS = {
    "SPACE_EXECUTION_RUNTIME_SERVICE_VERSION",
    "SpaceExecutionRuntimeError",
    "cancel_space_run",
    "execution_runtime_capabilities",
    "get_space_run",
    "list_space_runs",
    "poll_space_run",
    "preflight_published_space",
    "recover_space_runs",
    "space_run_progress",
    "start_space_run",
}

_LAZY_RUN_PROGRESS_EXPORTS = {
    "SPACE_RUN_PROGRESS_SCHEMA_VERSION",
    "SPACE_RUN_PROGRESS_SERVICE_VERSION",
    "build_space_graph_plan",
    "ensure_space_run_monitor",
    "get_space_run_progress",
    "progress_runtime_capabilities",
    "recover_space_run_monitors",
    "shutdown_space_run_monitors",
}

_LAZY_RUNTIME_PROBE_EXPORTS = {
    "SPACE_RUNTIME_PROBE_SCHEMA_VERSION",
    "SpaceRuntimeProbeError",
    "probe_comfy_profile_object_info",
}


_LAZY_DEPENDENCY_HEALTH_EXPORTS = {
    "SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION",
    "SPACE_DEPENDENCY_HEALTH_COLLECTION_VERSION",
    "DEFAULT_HEALTH_TTL_SECONDS",
    "ComfySpaceDependencyHealthSnapshot",
    "SpaceDependencyHealthCollection",
    "SpaceDependencyHealthError",
    "build_runtime_inventory",
    "dependency_health_capabilities",
    "list_space_runtime_health",
    "load_space_runtime_health",
    "refresh_space_runtime_health",
}


_LAZY_VARIANT_EXPORTS = {
    "SPACE_VARIANT_SCHEMA_VERSION",
    "SPACE_VARIANT_COLLECTION_SCHEMA_VERSION",
    "ComfySpaceVariant",
    "SpaceVariantCollection",
    "SpaceVariantError",
    "SpaceVariantPresentation",
    "SpaceVariantStageDefaults",
    "create_space_variant",
    "delete_space_variant",
    "list_space_variants",
    "load_space_variant",
    "resolve_space_variant",
    "variant_capabilities",
    "variant_payload",
}



_LAZY_VERSIONING_EXPORTS = {
    "SPACE_VERSION_STATE_SCHEMA_VERSION",
    "SPACE_REPLACEMENT_PLAN_SCHEMA_VERSION",
    "SPACE_MIGRATION_AUDIT_SCHEMA_VERSION",
    "SPACE_VERSIONING_SERVICE_VERSION",
    "SpaceVersioningError",
    "SpaceVersionState",
    "SpaceReplacementPlan",
    "SpaceReplacementMapping",
    "SpaceMigrationAudit",
    "activate_space_version",
    "rollback_space_version",
    "resolve_active_space_version",
    "list_space_versions",
    "create_workflow_replacement_draft",
    "load_replacement_plan",
    "confirm_replacement_review",
    "replacement_publish_guard",
    "migrate_space_variant",
    "versioning_capabilities",
}


_LAZY_SURFACE_EXPORTS = {
    "SPACE_SURFACE_SCHEMA_VERSION",
    "SPACE_SURFACE_SERVICE_VERSION",
    "ComfySpaceSurfaceError",
    "list_published_spaces",
    "load_public_space",
    "presentation_asset_path",
    "runtime_graph_addon_catalog",
    "runtime_option_catalogs",
    "surface_capabilities",
}


def __getattr__(name: str):
    if name in _LAZY_GRAPH_ADDON_EXPORTS:
        from neo_app.comfy_spaces import graph_addons as _graph_addons

        return getattr(_graph_addons, name)
    if name in _LAZY_ASSET_BUS_EXPORTS:
        from neo_app.comfy_spaces import asset_bus as _asset_bus

        return getattr(_asset_bus, name)
    if name in _LAZY_BUILDER_EXPORTS:
        from neo_app.comfy_spaces import builder_service as _builder_service

        return getattr(_builder_service, name)
    if name in _LAZY_BUILDER_WORKSPACE_EXPORTS:
        from neo_app.comfy_spaces import builder_workspace as _builder_workspace

        return getattr(_builder_workspace, name)
    if name in _LAZY_PUBLISH_EXPORTS:
        from neo_app.comfy_spaces import publish_service as _publish_service

        return getattr(_publish_service, name)
    if name in _LAZY_IMPORTER_EXPORTS:
        from neo_app.comfy_spaces import importer as _importer

        return getattr(_importer, name)
    if name in _LAZY_EXECUTION_RUNTIME_EXPORTS:
        from neo_app.comfy_spaces import execution_runtime as _execution_runtime

        return getattr(_execution_runtime, name)
    if name in _LAZY_RUN_PROGRESS_EXPORTS:
        from neo_app.comfy_spaces import run_progress as _run_progress

        return getattr(_run_progress, name)
    if name in _LAZY_RUNTIME_PROBE_EXPORTS:
        from neo_app.comfy_spaces import runtime_probe as _runtime_probe

        return getattr(_runtime_probe, name)
    if name in _LAZY_DEPENDENCY_HEALTH_EXPORTS:
        from neo_app.comfy_spaces import dependency_health as _dependency_health

        return getattr(_dependency_health, name)
    if name in _LAZY_VARIANT_EXPORTS:
        from neo_app.comfy_spaces import variants as _variants

        return getattr(_variants, name)
    if name in _LAZY_VERSIONING_EXPORTS:
        from neo_app.comfy_spaces import versioning as _versioning

        return getattr(_versioning, name)
    if name in _LAZY_SURFACE_EXPORTS:
        from neo_app.comfy_spaces import surface_service as _surface_service

        return getattr(_surface_service, name)
    raise AttributeError(name)


__all__ = [
    "SPACE_ASSET_BUS_SCHEMA_VERSION",
    "SPACE_ASSET_BUS_SERVICE_VERSION",
    "SPACE_ASSET_RECORD_SCHEMA_VERSION",
    "SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION",
    "SPACE_RUN_PROGRESS_SCHEMA_VERSION",
    "SPACE_RUN_PROGRESS_SERVICE_VERSION",
    "COMFY_SPACE_RUNTIME_DIRECTORIES",
    "SPACE_BUILDER_DRAFT_FILENAME",
    "SPACE_BUILDER_DRAFT_SCHEMA_VERSION",
    "SPACE_BUILDER_SERVICE_VERSION",
    "SPACE_DRAFT_ANALYSIS_FILENAME",
    "SPACE_DYNAMIC_CONTROLS_FILENAME",
    "SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION",
    "SPACE_ANALYSIS_SCHEMA_VERSION",
    "SPACE_AUTHORING_MAP_SCHEMA_VERSION",
    "SPACE_GRAPH_ADDON_CATALOG_VERSION",
    "SPACE_GRAPH_ADDON_COMPOSITION_VERSION",
    "SPACE_GRAPH_ADDON_SCHEMA_VERSION",
    "SPACE_GRAPH_ANCHOR_ANALYSIS_VERSION",
    "SPACE_GRAPH_ANCHOR_SCHEMA_VERSION",
    "SPACE_DEPENDENCY_HEALTH_SCHEMA_VERSION",
    "SPACE_DEPENDENCY_HEALTH_COLLECTION_VERSION",
    "DEFAULT_HEALTH_TTL_SECONDS",
    "SPACE_VARIANT_SCHEMA_VERSION",
    "SPACE_VARIANT_COLLECTION_SCHEMA_VERSION",
    "SUBGRAPH_IR_SCHEMA_VERSION",
    "SPACE_ASSET_REFERENCE_TYPE",
    "SPACE_BINDING_ENGINE_SCHEMA_VERSION",
    "SPACE_IMPORT_RECORD_SCHEMA_VERSION",
    "SPACE_MANIFEST_FILENAME",
    "SPACE_GRAPH_ANCHORS_FILENAME",
    "SPACE_PREVIEW_DYNAMIC_CONTROLS_FILENAME",
    "SPACE_PREVIEW_MANIFEST_FILENAME",
    "SPACE_PREVIEW_UI_FILENAME",
    "SPACE_PUBLICATION_RECEIPT_FILENAME",
    "SPACE_PUBLICATION_RECEIPT_SCHEMA_VERSION",
    "SPACE_PUBLISH_GATE_SCHEMA_VERSION",
    "SPACE_PUBLISH_SERVICE_VERSION",
    "SPACE_MANIFEST_SCHEMA_VERSION",
    "SPACE_RUNTIME_CONTRACT_VERSION",
    "SPACE_RUNTIME_PROBE_SCHEMA_VERSION",
    "SPACE_TRUST_CONTRACT_VERSION",
    "SPACE_TYPE_SYSTEM_SCHEMA_VERSION",
    "SPACE_UI_FILENAME",
    "SPACE_UI_SCHEMA_VERSION",
    "SPACE_WORKFLOW_FILENAME",
    "ComfySpaceAssetRecord",
    "ComfySpaceBuilderDraft",
    "ComfySpaceDynamicControls",
    "ComfySpaceGraphAnchors",
    "ComfySpaceManifest",
    "ComfySpacePublicationReceipt",
    "ComfySpaceUI",
    "SpaceDynamicBehavior",
    "SpaceDynamicCollection",
    "SpaceDynamicCondition",
    "SpaceDynamicControlsError",
    "SpaceDynamicPredicate",
    "SpaceGraphAnchorDefinition",
    "SpaceGraphAddonError",
    "SpaceGraphAddonModuleDefinition",
    "SpaceGraphAddonRequest",
    "ComfySpaceDependencyHealthSnapshot",
    "SpaceDependencyHealthCollection",
    "SpaceDependencyHealthError",
    "ComfySpaceVariant",
    "SpaceVariantCollection",
    "SpaceVariantError",
    "SpaceVariantPresentation",
    "SpaceVariantStageDefaults",
    "SpaceAssetBindingResolutionReport",
    "SpaceAssetCleanupReport",
    "SpaceAssetCollectionReport",
    "SpaceAssetIngestReport",
    "SpaceAssetReference",
    "SpaceAssetBusError",
    "SpaceBindingError",
    "SpaceBuilderError",
    "SpaceContractError",
    "SpacePublishError",
    "SpaceUIContractError",
    "SpaceInputTypeDescriptor",
    "SpaceExecutionIssue",
    "SpaceExecutionOutputRecord",
    "SpaceExecutionPreflightReport",
    "SpaceExecutionRunResult",
    "SpaceOutputTypeDescriptor",
    "SpaceRuntimeProbeError",
    "SpaceWorkflowAnalysisError",
    "SpaceWorkflowImportError",
    "analyze_comfy_workflow",
    "asset_bus_capabilities",
    "asset_content_path",
    "asset_content_url",
    "asset_public_payload",
    "asset_reference",
    "bind_space_inputs",
    "binding_engine_capabilities",
    "builder_service_capabilities",
    "cleanup_expired_assets",
    "collect_comfy_artifact",
    "comfy_space_asset_record_path",
    "comfy_space_asset_storage_dir",
    "comfy_space_builder_draft_path",
    "comfy_space_draft_analysis_path",
    "comfy_space_draft_preview_dynamic_controls_path",
    "comfy_space_draft_preview_manifest_path",
    "comfy_space_draft_preview_ui_path",
    "comfy_space_draft_root",
    "comfy_space_draft_workflow_path",
    "comfy_space_import_root",
    "comfy_space_manifest_path",
    "comfy_space_publication_receipt_path",
    "comfy_space_root",
    "comfy_space_version_root",
    "comfy_space_ui_path",
    "comfy_space_workflow_path",
    "comfy_spaces_assets_root",
    "comfy_space_input_assets_root",
    "comfy_space_output_assets_root",
    "comfy_spaces_drafts_root",
    "comfy_spaces_imports_root",
    "comfy_spaces_library_root",
    "comfy_spaces_runtime_root",
    "comfy_spaces_variants_root",
    "comfy_space_variant_path",
    "compile_builder_draft",
    "create_builder_draft_from_import",
    "delete_asset",
    "import_comfy_workflow_file",
    "ingest_asset_bytes",
    "ingest_asset_stream",
    "ingest_local_asset",
    "import_comfy_workflow_payload",
    "list_assets",
    "load_asset_record",
    "load_builder_draft",
    "load_space_manifest",
    "load_space_ui",
    "load_workflow_json",
    "dynamic_controls_payload",
    "evaluate_dynamic_condition",
    "evaluate_dynamic_controls",
    "load_dynamic_controls",
    "manifest_payload",
    "normalize_comfy_api_workflow",
    "normalize_comfy_workflow",
    "parse_dynamic_controls",
    "parse_space_manifest",
    "prepare_space_asset_bindings",
    "parse_space_ui",
    "probe_comfy_profile_object_info",
    "resolve_asset_for_comfy",
    "resolve_space_asset_inputs",
    "resolve_input_type_descriptor",
    "resolve_output_type_descriptor",
    "validate_dynamic_controls",
    "validate_space_bindings",
    "validate_space_ui_against_manifest",
    "validate_space_package",
    "type_system_capabilities",
    "workflow_sha256",
    "SPACE_EXECUTION_RUNTIME_SERVICE_VERSION",
    "SpaceExecutionRuntimeError",
    "cancel_space_run",
    "execution_runtime_capabilities",
    "get_space_run",
    "list_space_runs",
    "poll_space_run",
    "preflight_published_space",
    "recover_space_runs",
    "space_run_progress",
    "start_space_run",
    "build_space_graph_plan",
    "ensure_space_run_monitor",
    "get_space_run_progress",
    "progress_runtime_capabilities",
    "recover_space_run_monitors",
    "shutdown_space_run_monitors",
    "SpaceRunProgressEvent",
    "SpaceRunProgressSnapshot",
    "SPACE_SURFACE_SCHEMA_VERSION",
    "SPACE_SURFACE_SERVICE_VERSION",
    "ComfySpaceSurfaceError",
    "list_published_spaces",
    "load_public_space",
    "presentation_asset_path",
    "runtime_graph_addon_catalog",
    "runtime_option_catalogs",
    "surface_capabilities",
    "build_graph_addon_catalog",
    "compose_graph_addons",
    "parse_graph_addon_requests",
    "create_space_variant",
    "delete_space_variant",
    "list_space_variants",
    "load_space_variant",
    "resolve_space_variant",
    "variant_capabilities",
    "variant_payload",
    "analyze_graph_anchors",
    "authoring_map_capabilities",
    "build_authoring_map",
    "graph_anchor_capabilities",
    "parse_graph_anchor_sidecar",
    "comfy_space_dynamic_controls_path",
    "comfy_space_dependency_health_path",
    "comfy_space_dependency_health_version_root",
    "comfy_spaces_dependency_health_root",
    "build_runtime_inventory",
    "dependency_health_capabilities",
    "list_space_runtime_health",
    "load_space_runtime_health",
    "refresh_space_runtime_health",
    "comfy_space_graph_anchors_path",
]
