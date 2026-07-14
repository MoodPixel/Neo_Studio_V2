from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
import argparse
import json
import logging
import re
from datetime import datetime, timezone
from urllib import parse
from uuid import uuid4

import uvicorn
import websockets
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse, StreamingResponse
import os
import platform
import subprocess
import tempfile
import shutil
import traceback
from fastapi.staticfiles import StaticFiles

from neo_app.runtime_data import bootstrap_neo_runtime_data
from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.runtime.progress_watchdog import attach_progress_watchdog

BOOTSTRAP_STATE: dict = {
    "ok": False,
    "schema_id": "neo.runtime_data.bootstrap.v1",
    "status": "pending_startup",
    "startup_bootstrap_call": False,
    "bootstrap_call_site": "not_started",
    "fastapi_startup_registered": True,
}

def _run_runtime_data_bootstrap(call_site: str) -> dict:
    """Run Neo's idempotent runtime data bootstrap and retain diagnostics.

    Phase 9 keeps module import side-effect light: importing ``neo_app.main``
    should not create or update user runtime files. Runtime data is created when
    FastAPI starts, or when the explicit setup endpoint is called.
    """

    global BOOTSTRAP_STATE
    payload = bootstrap_neo_runtime_data()
    BOOTSTRAP_STATE = {
        **payload,
        "startup_bootstrap_call": True,
        "bootstrap_call_site": call_site,
        "fastapi_startup_registered": True,
    }
    return BOOTSTRAP_STATE


@asynccontextmanager
async def neo_runtime_lifespan(app: FastAPI):
    """Run first-run runtime data bootstrap when FastAPI starts."""

    _run_runtime_data_bootstrap("fastapi_startup")
    _ensure_legacy_surface_runtime_dirs()
    configure_runtime_logging()
    yield

from neo_app.surfaces.registry import get_surface, get_surface_payload
from neo_app.surfaces.blueprint import surface_blueprint, surface_blueprint_payload
from neo_app.surfaces.module_architecture import module_architecture_status, module_architecture_manifest, module_architecture_audit, modular_surface_contract
from neo_app.surfaces.migration_runtime import surface_migration_runtime_status, surface_migration_runtime_audit, admin_memory_cockpit_migration_status, admin_memory_cockpit_migration_audit, admin_memory_cockpit_action_migration_status, admin_memory_cockpit_action_migration_audit, assistant_surface_slice_migration_status, assistant_surface_slice_migration_audit, assistant_deep_panel_migration_status, assistant_deep_panel_migration_audit, roleplay_surface_slice_migration_status, roleplay_surface_slice_migration_audit, roleplay_scene_director_cockpit_migration_status, roleplay_scene_director_cockpit_migration_audit, roleplay_scene_chat_dispatch_migration_status, roleplay_scene_chat_dispatch_migration_audit, roleplay_transcript_checkpoint_migration_status, roleplay_transcript_checkpoint_migration_audit, roleplay_scene_state_checkpoint_inspector_migration_status, roleplay_scene_state_checkpoint_inspector_migration_audit, roleplay_stories_workspace_migration_status, roleplay_stories_workspace_migration_audit, roleplay_archive_provenance_graph_migration_status, roleplay_archive_provenance_graph_migration_audit, roleplay_compile_runtime_deep_migration_status, roleplay_compile_runtime_deep_migration_audit, roleplay_forge_builder_migration_status, roleplay_forge_builder_migration_audit, roleplay_forge_sqlite_template_inspector_migration_status, roleplay_forge_sqlite_template_inspector_migration_audit, roleplay_forge_advanced_import_scope_migration_status, roleplay_forge_advanced_import_scope_migration_audit
from neo_app.ui.modern_system import modern_ui_system_status, modern_ui_system_audit
from neo_app.admin.registry import get_admin_payload, get_surface_admin
from neo_app.admin.engine import admin_engine_state_payload, update_model_paths, update_retrieval_defaults, update_runtime_defaults
from neo_app.admin.semantic_engine import semantic_engine_state_payload, semantic_engine_test_payload
from neo_app.admin.chroma_collections import chroma_collection_state_payload, export_chroma_collection_payload, export_archive_path, import_chroma_archive_payload
from neo_app.admin.index_jobs import index_job_queue_state_payload, create_index_job_payload, cancel_index_job_payload, read_index_job_log_payload
from neo_app.admin.models.model_catalog_service import admin_model_catalog_payload, admin_model_catalog_summary_payload, admin_model_category_map_payload, admin_model_folder_rules_payload, admin_model_schema_payload, admin_model_paths_state_payload, admin_model_paths_save_payload, admin_model_target_resolution_payload, admin_model_installed_state_payload, admin_model_scan_installed_payload, admin_model_huggingface_metadata_state_payload, admin_model_huggingface_discover_files_state_payload, admin_model_civitai_metadata_state_payload, admin_model_civitai_discover_files_state_payload, admin_model_filter_state_payload, admin_model_download_plan_state_payload, admin_model_download_start_state_payload, admin_model_download_cancel_state_payload, admin_model_download_jobs_state_payload, admin_model_download_job_state_payload, admin_model_packs_state_payload, admin_model_pack_status_state_payload, admin_model_pack_download_plan_state_payload, admin_model_workspace_requirements_state_payload, admin_model_workspace_status_state_payload, admin_model_workspace_download_plan_state_payload
from neo_app.admin.models.model_paths import load_model_paths
from neo_app.admin.image_node_manager import (
    get_node_manager_state,
    save_node_manager_settings,
    scan_node_manager_disk,
    install_node_from_github,
    update_node,
    detect_comfy_python_path,
    validate_comfy_python_path,
    install_node_requirements,
    open_custom_nodes_folder,
)
from neo_app.providers.comfy_provider import ComfyProvider
from neo_app.providers.xai_grok_provider import XaiGrokProvider
from neo_app.image.upload_validation import ImageUploadValidationError, validate_and_store_image_upload
from neo_app.image.provider_errors import normalize_image_provider_error
from neo_app.prompt_captioning.upload_validation import (
    CAPTION_UPLOAD_VALIDATION_SCHEMA,
    CaptionUploadValidationError,
    validate_and_stage_caption_image_upload,
)
from neo_app.image.job_contexts import (
    image_job_context_index,
    load_image_job_context,
    load_recent_image_job_contexts,
    mark_image_job_context,
    prune_image_job_contexts,
    save_image_job_context,
)
from neo_app.providers.schema import NeoJob
from neo_app.providers.registry import (
    compile_job_payload,
    fetch_outputs_payload,
    get_provider,
    get_provider_backend_capabilities,
    get_provider_payload,
    get_surface_provider_payload,
    poll_job_payload,
    run_job_payload,
    validate_job_payload,
)
from neo_app.memory.service import get_memory_service
from neo_app.operator.service import operator_status_payload, plan_operator_command, run_operator_command
from neo_app.voice.service import voice_input_status_payload, prepare_voice_input_payload, run_voice_input_operator_payload, transcribe_uploaded_audio_payload
from neo_app.voice.adapter_client import (
    voice_capabilities_payload,
    voice_capability_controls_payload,
    voice_health_payload,
    voice_models_payload,
    voice_voices_payload,
)
from neo_app.voice.batch_service import (
    import_voice_batch_payload,
    render_voice_batch_payload,
    retry_voice_batch_item_payload,
    voice_batch_history_payload,
    voice_batch_payload,
)
from neo_app.voice.finish_service import (
    finish_voice_output_payload,
    merge_voice_outputs_payload,
    split_voice_output_payload,
    voice_finish_history_payload,
)
from neo_app.voice.job_service import (
    cancel_voice_job_payload,
    clone_voice_payload,
    dialogue_voice_payload,
    delete_voice_job_payload,
    export_voice_job_payload,
    open_voice_job_folder_payload,
    preview_voice_payload,
    render_voice_payload,
    retry_voice_chunk_payload,
    retry_voice_job_payload,
    reuse_voice_job_settings_payload,
    voice_exports_payload,
    voice_history_payload,
    voice_job_payload,
    voice_job_replay_payload,
    voice_memory_exports_payload,
    voice_queue_payload,
    voice_replays_payload,
)
from neo_app.voice.reference_audio import (
    analyze_reference_payload,
    reference_history_payload,
    store_reference_upload,
)
from neo_app.voice.profile_store import (
    create_voice_profile_payload,
    delete_voice_profile_payload,
    update_voice_profile_payload,
    voice_profile_payload,
    voice_profiles_payload,
)
from neo_app.voice.project_handoff import (
    build_voice_project_handoff_payload,
    send_voice_job_to_project_payload,
    voice_project_asset_tray_payload,
)
from neo_app.internet.service import internet_access_status_payload, plan_internet_access_payload, run_internet_access_payload, update_internet_access_policy_payload
from neo_app.providers.profiles import (
    create_backend_profile,
    get_backend_profile,
    get_backend_profile_for_runtime,
    get_backend_profile_for_live_task,
    get_backend_profile_payload,
    list_backend_profiles,
    list_backend_provider_options,
    save_backend_profile,
    save_backend_profile_selection,
    get_backend_profile_selection_payload,
    is_backend_profile_connected_for_task,
    clear_backend_profile_api_key,
    set_default_backend_profile,
    test_backend_profile,
    connect_backend_profile,
    disconnect_backend_profile,
)
from neo_app.prompt_captioning.support_matrix import get_support_matrix as get_prompt_captioning_support_matrix
from neo_app.prompt_captioning.validation import validate_route_payload as validate_prompt_captioning_route_payload, validation_status as prompt_captioning_validation_status
from neo_app.prompt_captioning.payload_contract import normalize_prompt_captioning_payload
from neo_app.prompt_captioning.service import (
    character_records as prompt_captioning_character_records,
    delete_preset as prompt_captioning_delete_preset,
    delete_saved_prompt as prompt_captioning_delete_saved_prompt,
    duplicate_preset as prompt_captioning_duplicate_preset,
    duplicate_saved_prompt as prompt_captioning_duplicate_saved_prompt,
    prompt_history as prompt_captioning_prompt_history,
    prompt_presets as prompt_captioning_prompt_presets,
    prompt_records as prompt_captioning_prompt_records,
    run_prompt_tool as run_prompt_captioning_prompt_tool,
    save_character as prompt_captioning_save_character,
    save_preset as prompt_captioning_save_preset,
    save_prompt as save_prompt_captioning_prompt,
    toggle_preset_favorite as prompt_captioning_toggle_preset_favorite,
    run_caption_tool as run_prompt_captioning_caption_tool,
    save_caption as save_prompt_captioning_caption,
    caption_records as prompt_captioning_caption_records,
    caption_history as prompt_captioning_caption_history,
    caption_presets as prompt_captioning_caption_presets,
    save_caption_preset_record as prompt_captioning_save_caption_preset,
    delete_caption_preset_record as prompt_captioning_delete_caption_preset,
    duplicate_caption_preset_record as prompt_captioning_duplicate_caption_preset,
    toggle_caption_preset_record_favorite as prompt_captioning_toggle_caption_preset_favorite,
    caption_components as prompt_captioning_caption_components,
    save_caption_component_record as prompt_captioning_save_caption_component,
    delete_caption_component_record as prompt_captioning_delete_caption_component,
    duplicate_caption_component_record as prompt_captioning_duplicate_caption_component,
    delete_saved_caption as prompt_captioning_delete_saved_caption,
    duplicate_saved_caption as prompt_captioning_duplicate_saved_caption,
    caption_batch_results as prompt_captioning_caption_batch_results,
    caption_batch_preview as prompt_captioning_caption_batch_preview,
    run_caption_batch as run_prompt_captioning_caption_batch,
    caption_batch_cancel as prompt_captioning_caption_batch_cancel,
    caption_batch_resume as prompt_captioning_caption_batch_resume,
    caption_batch_retry_failed as prompt_captioning_caption_batch_retry_failed,
    caption_batch_status as prompt_captioning_caption_batch_status,
    caption_batch_export_log as prompt_captioning_caption_batch_export_log,
    backend_execution_status as prompt_captioning_backend_execution_status,
    build_reuse_payload as prompt_captioning_build_reuse_payload,
    history_clear as prompt_captioning_history_clear,
    library_duplicate as prompt_captioning_library_duplicate,
    library_export_snapshot as prompt_captioning_library_export_snapshot,
    library_import_snapshot as prompt_captioning_library_import_snapshot,
    library_list as prompt_captioning_library_list,
    library_record as prompt_captioning_library_record,
    library_update as prompt_captioning_library_update,
    build_cross_tab_handoff as prompt_captioning_build_cross_tab_handoff,
    handoff_history as prompt_captioning_handoff_history,
    result_metadata_records as prompt_captioning_result_metadata_records,
    result_metadata_record as prompt_captioning_result_metadata_record,
    replay_payload_for_metadata as prompt_captioning_replay_payload_for_metadata,
    categories as prompt_captioning_categories,
    save_shared_category as prompt_captioning_save_category,
)
from neo_app.prompt_captioning.storage import save_caption_asset, CAPTION_ASSETS_DIR, CAPTION_SINGLE_IMAGES_DIR, CAPTION_BATCH_IMAGES_DIR
from neo_app.prompt_captioning.keyword_browser_v2 import (
    keyword_browser_v2_payload,
    keyword_browser_v2_manager_payload,
    keyword_browser_v2_manager_save_payload,
    keyword_browser_v2_manager_append_payload,
    keyword_browser_v2_manager_create_payload,
    keyword_browser_v2_manager_upload_text_payload,
)
from neo_app.prompt_captioning.character_keyword_browser_v2 import character_keyword_browser_v2_payload
from neo_app.prompt_captioning.assist_tools import (
    assist_bootstrap_payload as prompt_assist_bootstrap_payload,
    tag_assist_generate_payload as prompt_assist_tag_generate_payload,
    tag_assist_save_payload as prompt_assist_tag_save_payload,
    tag_assist_list_payload as prompt_assist_tag_list_payload,
    character_save_payload as prompt_assist_character_save_payload,
    character_list_payload as prompt_assist_character_list_payload,
    build_character_prompt_payload as prompt_assist_character_build_prompt_payload,
    keyword_insert_text_payload as prompt_assist_keyword_insert_text_payload,
    keyword_list_payload as prompt_assist_keyword_list_payload,
    keyword_record_payload as prompt_assist_keyword_record_payload,
    keyword_save_payload as prompt_assist_keyword_save_payload,
    caption_browser_list_payload as prompt_assist_caption_browser_list_payload,
    caption_browser_save_payload as prompt_assist_caption_browser_save_payload,
    caption_browser_send_to_prompt_payload as prompt_assist_caption_browser_send_to_prompt_payload,
)
from neo_app.image.base_contract import create_image_job_draft, get_image_surface_base_contract
from neo_app.image.prompt_library import (
    create_image_prompt_pair,
    delete_image_prompt_pair,
    list_image_prompt_library,
    update_image_prompt_pair,
)
from neo_app.roleplay.base_contract import get_roleplay_surface_base_contract

from neo_app.assistant.chat import run_assistant_chat_turn, stream_assistant_chat_turn_event_dicts
from neo_app.assistant.surface_project_context import surface_project_context_payload
from neo_app.assistant.source_grounded import build_source_grounded_context as assistant_source_grounded_answer_payload
from neo_app.assistant.action_review import action_review_status_payload, plan_assistant_action_review, run_assistant_action_review
from neo_app.assistant.attachments import (
    attachment_path as assistant_attachment_path,
    delete_attachment_record as assistant_delete_attachment_record,
    get_attachment_record as assistant_get_attachment_record,
    list_attachment_records as assistant_list_attachment_records,
    save_attachment_upload as assistant_save_attachment_upload,
)
from neo_app.assistant.store import (
    assistant_bootstrap_payload,
    assistant_search_payload,
    context_pack_preview_payload as assistant_context_pack_preview_payload,
    create_project_payload as assistant_create_project_payload,
    create_session_payload as assistant_create_session_payload,
    delete_project_payload as assistant_delete_project_payload,
    delete_session_payload as assistant_delete_session_payload,
    load_session_payload as assistant_load_session_payload,
    manual_memory_capture_payload as assistant_manual_memory_capture_payload,
    rename_project_payload as assistant_rename_project_payload,
    rename_session_payload as assistant_rename_session_payload,
    save_assistant_profile,
    save_project_payload as assistant_save_project_payload,
    save_session_payload as assistant_save_session_payload,
    clear_session_messages_payload as assistant_clear_session_messages_payload,
    save_context_item_payload as assistant_save_context_item_payload,
    context_items_payload as assistant_context_items_payload,
    save_surface_context_payload as assistant_save_surface_context_payload,
    list_surface_context_payload as assistant_list_surface_context_payload,
)
from neo_app.assistant.tools import tool_catalog_payload as assistant_tool_catalog_payload, tool_preview_payload as assistant_tool_preview_payload, execute_tool_payload as assistant_tool_execute_payload
from neo_app.assistant.project_manager import assistant_project_manager_payload, assistant_project_manager_query
from neo_app.assistant.guides import search_guides as assistant_search_guides
from neo_app.assistant.project_brain import (
    capture_project_state_payload as assistant_capture_project_state_payload,
    index_project_data_payload as assistant_index_project_data_payload,
    project_brain_status_payload as assistant_project_brain_status_payload,
    rebuild_project_brain_payload as assistant_rebuild_project_brain_payload,
    save_project_file_upload as assistant_save_project_file_upload,
)
from neo_app.assistant.brain_workspace import (
    assistant_brain_activate_payload,
    assistant_brain_context_payload,
    assistant_brain_dashboard_payload,
    assistant_brain_status_payload,
    assistant_brain_workspaces_payload,
)
from neo_app.control_center.assistant_controller import (
    assistant_control_context_payload,
    assistant_control_plan_payload,
    assistant_control_status_payload,
    assistant_control_traces_payload,
)
from neo_app.control_center.roleplay_controller import (
    roleplay_control_context_payload,
    roleplay_control_plan_payload,
    roleplay_control_status_payload,
    roleplay_control_traces_payload,
)
from neo_app.roleplay.scene_director_runtime import (
    roleplay_scene_director_status_payload,
    roleplay_scene_director_preflight_payload,
    roleplay_scene_director_validate_payload,
    roleplay_scene_director_trace_payload,
)
from neo_app.control_center.prompt_contracts import (
    prompt_contract_detail_payload,
    prompt_contract_list_payload,
    prompt_contract_status_payload,
)
from neo_app.memory_project_qa import memory_project_qa_payload, memory_project_regression_repair_payload
from neo_app.runtime_hardening import runtime_hardening_payload, runtime_hardening_setup_payload
from neo_app.release_candidate import release_candidate_payload, write_release_candidate_manifest
from neo_app.tool_registry import effective_tool_registry_payload, permission_profiles_payload, set_permission_profile_payload, tool_registry_status_payload
from neo_app.tool_ledger import tool_ledger_status_payload, list_tool_ledger_events, get_tool_ledger_event, record_tool_ledger_event
from neo_app.project_workspace import project_workspace_status_payload, list_project_workspaces, active_project_payload, set_active_project, save_project_workspace, add_project_context, project_workspace_context_payload, link_project_resource, create_cross_surface_handoff, add_project_timeline_event, list_project_timeline, project_workspace_asset_tray_payload, project_activity_intelligence_payload, project_smart_brief_payload, save_project_brief, project_briefs_payload, get_project_brief, export_project_brief, project_brief_versions_payload, compare_project_briefs, restore_project_brief_version, mark_project_brief_final, save_project_milestone, list_project_milestones, save_project_deliverable, list_project_deliverables, project_deliverable_tracker_payload, project_delivery_dashboard_payload, project_client_status_view_payload, export_project_status_report, save_project_review_item, list_project_review_items, project_review_queue_payload, apply_project_review_decision, project_approval_workflow_payload, project_package_builder_payload, build_project_package, create_project_surface_action, list_project_surface_actions, project_surface_actions_payload
from neo_app.roleplay.forge import delete_forge_record_payload, forge_state_payload, forge_template_payload, list_forge_records, save_forge_record_payload
from neo_app.roleplay.forge_importer import forge_import_contract, import_records as forge_import_records_payload, preview_import_records
from neo_app.roleplay.builder_kind_templates import first_class_builder_templates_contract_payload, first_class_builder_templates_state_payload, ensure_first_class_builder_templates_payload
from neo_app.roleplay.builder_compiler_profiles import compiler_profiles_contract_payload, compiler_profiles_state_payload, ensure_compiler_profiles_payload
from neo_app.roleplay.studio import create_studio_project_payload, list_studio_projects, list_studio_sources, save_studio_source_payload, studio_state_payload
from neo_app.roleplay.novel_memory import compile_all_source_documents_payload, compile_source_document_payload, ensure_novel_memory_schema, novel_memory_contract_payload, novel_memory_state_payload
from neo_app.roleplay.source_breakdown import approve_source_breakdown_payload, ensure_source_breakdown_schema, generate_all_source_breakdowns_payload, generate_source_breakdown_payload, list_source_breakdowns_payload, source_breakdown_contract_payload, source_breakdown_state_payload
from neo_app.roleplay.storage import roleplay_foundation_payload
from neo_app.roleplay.text_backend import resolve_roleplay_text_backend
from neo_app.roleplay.scene import append_scene_transcript_placeholder, clear_scene_transcript_payload, execute_scene_turn_payload, save_scene_setup_payload, scene_state_payload, stream_scene_turn_event_dicts, load_scene_setup, load_scene_transcript, update_scene_session_setup_payload, start_scene_continuation_session_payload
from neo_app.roleplay.scene_memory_injection import scene_memory_injection_contract_payload, scene_memory_injection_state_payload, build_scene_memory_injection_payload
from neo_app.roleplay.stories import branch_story_checkpoint, compare_story_checkpoints, create_story_checkpoint_payload, create_story_session_payload, create_storyline_payload, list_storylines, stories_state_payload, list_story_sessions, list_story_checkpoints, list_story_branches, restore_story_checkpoint_to_scene_payload, restore_story_session_to_scene_payload
from neo_app.roleplay.story_checkpoint_restore import story_checkpoint_restore_contract_payload, story_checkpoint_restore_state_payload, ensure_story_checkpoint_restore_schema, capture_active_scene_checkpoint_payload, restore_checkpoint_with_snapshot_payload
from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.roleplay.memory_adapter import roleplay_memory_state_payload, sync_forge_memory_foundation_payload
from neo_app.roleplay.retrieval import create_retrieval_trace_placeholder_payload, index_roleplay_memory_vectors_payload, roleplay_retrieval_state_payload, search_retrieval_foundation_payload, search_retrieval_semantic_payload
from neo_app.roleplay.human_memory import roleplay_human_memory_state_payload, sync_scene_human_memory
from neo_app.roleplay.runtime import runtime_bundle_payload, runtime_compile_payload, runtime_state_payload
from neo_app.roleplay.contradictions import contradiction_state_payload, scan_contradictions_payload, update_contradiction_report_payload
from neo_app.roleplay.provenance import provenance_graph_payload, provenance_state_payload, provenance_trace_payload
from neo_app.roleplay.packages import export_roleplay_package_payload, import_roleplay_package_payload, package_archive_download_path, package_state_payload
from neo_app.roleplay.collaboration import collaboration_state_payload, collaboration_heartbeat_payload, acquire_lock_payload, release_lock_payload, log_activity_payload, resolve_conflict_payload
from neo_app.roleplay.cloud_sync import cloud_sync_state_payload, create_cloud_snapshot_payload, compare_cloud_sync_payload, push_cloud_sync_payload, pull_cloud_sync_payload, update_cloud_sync_settings_payload
from neo_app.roleplay.plugin_registry import registry_state_payload, add_package_to_registry_payload, install_registry_package_payload, toggle_registry_package_payload, export_package_to_registry_payload
from neo_app.roleplay.project_linking import roleplay_project_links_payload, create_roleplay_project_link
from neo_app.roleplay.storage_audit import roleplay_storage_audit_payload, lock_roleplay_storage_contract_payload
from neo_app.roleplay.sandbox_contract import sandbox_contract_payload, ensure_roleplay_sandbox_schema, derive_sandbox_context
from neo_app.roleplay.builder_memory_compiler import builder_memory_compiler_contract_payload, compile_builder_record_memory_payload, compile_all_builder_records_memory_payload
from neo_app.roleplay.relationship_schema import relationship_schema_contract_payload, normalize_relationship_record_payload, repair_all_relationship_records_payload
from neo_app.roleplay.sqlite_upgrade import roleplay_sqlite_upgrade_contract_payload, ensure_roleplay_sqlite_upgrade_schema, roleplay_sqlite_health_payload, rebuild_roleplay_memory_fts_payload
from neo_app.roleplay.embedding_reranker_adapter import roleplay_embedding_reranker_contract_payload, roleplay_embedding_reranker_state_payload, embed_roleplay_texts_payload, rerank_roleplay_results_payload, index_roleplay_search_documents_payload
from neo_app.roleplay.chroma_vector_mirror import roleplay_chroma_mirror_contract_payload, roleplay_chroma_mirror_state_payload, ensure_roleplay_chroma_mirror_schema, mirror_roleplay_vectors_to_chroma_payload, reset_roleplay_chroma_mirror_status_payload
from neo_app.roleplay.provenance_debug_ui import provenance_debug_contract_payload, provenance_debug_state_payload, provenance_debug_dashboard_payload, provenance_debug_inspect_payload, ensure_provenance_debug_schema
from neo_app.roleplay.large_json_io import large_json_io_contract_payload, large_json_io_state_payload, ensure_large_json_io_schema, validate_large_json_records_payload, import_large_json_records_payload, export_large_json_sandbox_payload
from neo_app.roleplay.large_json_test_validation import large_json_test_validation_contract_payload, large_json_test_validation_state_payload, ensure_large_json_test_validation_schema, run_large_json_test_validation_payload
from neo_app.roleplay.regression_tests import roleplay_regression_tests_contract_payload, roleplay_regression_tests_state_payload, ensure_roleplay_regression_tests_schema, run_roleplay_regression_tests_payload
from neo_app.roleplay.scoped_compile_plan import scoped_compile_contract_payload, scoped_compile_state_payload, ensure_scoped_compile_schema, build_compile_plan_payload, execute_compile_plan_payload
from neo_app.roleplay.scope_build_ui import (
    scope_build_contract_payload,
    scope_build_state_payload,
    scope_discovery_debug_payload,
    ensure_scope_build_schema,
    list_scope_build_options,
    linked_records_for_scope,
    preview_scope_build_payload,
    build_scope_memory_runtime_payload,
)
from neo_app.roleplay.runtime_presets import runtime_presets_contract_payload, runtime_presets_state_payload, ensure_runtime_presets_schema, scene_packet_payload_from_preset, build_scene_packet_from_runtime_preset, start_scene_packet_build_job, scene_packet_build_job_status
from neo_app.roleplay.runtime_retrieval_lane import runtime_retrieval_lane_contract_payload, runtime_retrieval_lane_state_payload, run_runtime_retrieval_payload
from neo_app.roleplay.scene_packet_builder import scene_packet_builder_contract_payload, scene_packet_builder_state_payload, build_scene_packet_payload
from neo_app.roleplay.scene_packet_categories import scene_packet_categories_contract_payload, scene_packet_categories_state_payload, ensure_scene_packet_categories_payload
from neo_app.roleplay.retrieval_labels_debug_proof import retrieval_label_debug_contract_payload, retrieval_label_debug_state_payload, ensure_retrieval_label_debug_schema
from neo_app.roleplay.turn_writeback import turn_writeback_contract_payload, turn_writeback_state_payload, ensure_turn_writeback_schema, apply_turn_writeback_payload
from neo_app.image.output_paths import output_path_payload
from neo_app.video.output_paths import get_video_output_paths, output_path_payload as video_output_path_payload, sanitize_path_part as sanitize_video_path_part
from neo_app.video.route_matrix import video_route_matrix_payload, video_route_validation_payload
from neo_app.video.parameter_profiles import video_parameter_profile_payload
from neo_app.video.vram_engine import video_vram_engine_payload, video_vram_preflight_payload
from neo_app.video.performance_profiles import video_performance_profile_payload, video_performance_preflight_payload
from neo_app.video.backend_probe import video_backend_probe_payload
from neo_app.video.runtime_preflight import video_runtime_preflight_payload, wan22_gguf_first_test_preset_payload
from neo_app.video.external_node_manager import video_external_node_manager_payload
from neo_app.video.interpolation_finish import video_interpolation_compile_payload, video_interpolation_generate_payload
from neo_app.video.upscale_finish import video_upscale_compile_payload, video_upscale_generate_payload
from neo_app.video.repair_finish import video_repair_compile_payload, video_repair_generate_payload
from neo_app.video.wan_txt2vid_compiler import video_wan22_txt2vid_compile_payload, video_wan22_txt2vid_generate_payload, video_wan22_img2vid_compile_payload, video_wan22_img2vid_generate_payload
from neo_app.video.wan_gguf_i2v14_compiler import video_wan22_gguf_i2v14_compile_payload, video_wan22_gguf_i2v14_generate_payload
from neo_app.video.wan_rapid_aio_gguf_audit import video_wan22_rapid_aio_gguf_audit_payload
from neo_app.video.wan_rapid_aio_gguf_compiler import video_wan22_rapid_aio_gguf_compile_payload, video_wan22_rapid_aio_gguf_generate_payload
from neo_app.video.ltx_txt2vid_compiler import video_ltx23_txt2vid_compile_payload, video_ltx23_txt2vid_generate_payload
from neo_app.video.ltx_img2vid_compiler import video_ltx23_img2vid_compile_payload, video_ltx23_img2vid_generate_payload
from neo_app.video.first_last_frame_compiler import video_ltx23_first_last_frame_compile_payload, video_ltx23_first_last_frame_generate_payload
from neo_app.video.multiscene_compiler import video_ltx23_multiscene_compile_payload, video_ltx23_multiscene_generate_payload
from neo_app.video.extend_compiler import video_ltx23_extend_compile_payload, video_ltx23_extend_generate_payload
from neo_app.video.vid2vid_compiler import video_ltx23_vid2vid_compile_payload, video_ltx23_vid2vid_generate_payload
from neo_app.video.depth_motion_compiler import video_ltx23_depth_motion_compile_payload, video_ltx23_depth_motion_generate_payload
from neo_app.video.schedule_compiler import video_ltx23_schedule_compile_payload, video_ltx23_schedule_generate_payload
from neo_app.video.audio_video_compiler import video_ltx23_audio_video_compile_payload, video_ltx23_audio_video_generate_payload
from neo_app.video.output_records import list_video_output_records, load_video_output_record, refresh_video_result_from_comfy, register_video_source_upload, video_output_file_path
from neo_app.video.replay_memory import video_replay_metadata_payload, video_memory_export_payload
from neo_app.video.project_handoff import build_video_project_handoff_payload, send_video_result_to_project_payload, video_project_asset_tray_payload
from neo_app.voice.output_paths import output_path_payload as voice_output_path_payload, resolve_voice_output_file
from neo_app.voice.surface_contract import voice_surface_contract_payload
from neo_app.image.seed_utils import normalize_image_seed_params
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.prompt_extensions import apply_prompt_extensions, apply_scene_director_prompt_extension_interop, build_prompt_extension_execution_snapshot
from neo_app.image.output_records import output_record_schema_payload
from neo_app.image.output_settings import (
    add_image_output_category,
    load_image_output_settings,
    save_image_output_settings,
    settings_response as image_output_settings_response,
    output_category_dir,
)
from neo_app.image.output_service import (
    build_image_result_reuse_payload,
    cleanup_image_replay_storage,
    get_image_result,
    get_image_result_metadata,
    image_replay_storage_summary,
    image_results_integrity_guard,
    list_image_results,
    persist_image_outputs,
    resolve_output_file,
    delete_image_result,
    build_image_result_delete_preview,
)
from neo_app.image.output_recovery import (
    enrich_provider_outputs_for_local_recovery,
    image_output_recovery_payload,
    normalize_import_failure_status,
)
from neo_app.services.ui_state import read_ui_state, write_ui_state
from neo_app.services.runtime_debug_logs import (
    ERROR_LOG_PATH,
    SERVER_LOG_PATH,
    all_surface_logs_payload,
    configure_runtime_logging,
    latest_log_payload,
    latest_surface_log_payload,
    log_file_payload,
    log_surface_event,
    record_surface_error,
    record_surface_snapshot,
    safe_run_id,
    surface_log_privacy_policy_payload,
    surface_log_tail_payload,
    write_console_status,
)
from neo_app.services.ui_presets import (
    create_ui_preset,
    delete_ui_preset,
    get_default_ui_preset,
    get_ui_preset,
    list_ui_presets,
    set_default_ui_preset,
    update_ui_preset,
)
from neo_app.models.registry import (
    check_model_family_compatibility,
    get_family,
    get_model_family_payload,
    get_parameter_profiles,
)
from neo_app.models.readiness import validate_readiness

from neo_app.extensions.registry import (
    check_extension_compatibility,
    get_extension,
    get_extension_payload,
    get_extension_ui_contract_payload,
    get_surface_extension_payload,
    install_extension_from_github,
    install_extension_from_zip,
    remove_extension,
    update_extension,
    set_extension_enabled,
)

from neo_extensions.built_in.lora_stack.backend.library_routes import register_lora_stack_library_routes
from neo_extensions.built_in.lora_stack.backend.comfy_metadata import fetch_comfy_lora_metadata
from neo_extensions.built_in.embeddings_ti.backend.library_routes import register_embeddings_ti_library_routes
from neo_extensions.built_in.controlnet.backend.map_routes import register_controlnet_map_routes
from neo_extensions.built_in.ip_adapter.backend.node_routes import register_ip_adapter_node_routes
from neo_extensions.built_in.adetailer.backend.api_routes import register_adetailer_api_routes
from neo_extensions.built_in.image_upscale.backend.api_routes import register_image_upscale_api_routes
from neo_extensions.built_in.background_removal.backend.api_routes import register_background_removal_api_routes
from neo_extensions.built_in.style_stack.backend.api_routes import register_style_stack_api_routes
from neo_extensions.built_in.wildcards.backend.api_routes import register_wildcards_api_routes
try:
    from neo_extensions.installed.final_polish_lab.backend.queue_routes import register_final_polish_lab_api_routes
except Exception:  # pragma: no cover - external extension may be absent during partial installs.
    register_final_polish_lab_api_routes = None
from neo_extensions.built_in.style_stack.backend.metadata import build_output_extension_metadata as build_style_stack_output_extension_metadata
from neo_extensions.built_in.wildcards.backend.metadata import build_output_extension_metadata as build_wildcards_output_extension_metadata

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "neo_app" / "static"
IMAGE_SOURCE_INPUT_DIR = ROOT_DIR / "neo_data" / "inputs" / "image"
IMAGE_MASK_INPUT_DIR = ROOT_DIR / "neo_data" / "inputs" / "image_masks"
IMAGE_LOG_DIR = ROOT_DIR / "neo_data" / "logs" / "image"

SCENE_DIRECTOR_DATA_DIR = ROOT_DIR / "neo_data" / "scene_director"
SCENE_DIRECTOR_PRESET_DIR = SCENE_DIRECTOR_DATA_DIR / "scene_presets"
SCENE_DIRECTOR_IDENTITY_DIR = SCENE_DIRECTOR_DATA_DIR / "identity_profiles"
SCENE_DIRECTOR_LAYOUT_DIR = SCENE_DIRECTOR_DATA_DIR / "region_layout_presets"
SCENE_DIRECTOR_TRAIT_LIBRARY_DIR = SCENE_DIRECTOR_DATA_DIR / "trait_libraries"
SCENE_DIRECTOR_BUILTIN_TRAIT_LIBRARY_DIR = ROOT_DIR / "neo_extensions" / "built_in" / "image.scene_director" / "trait_libraries"


def _ensure_legacy_surface_runtime_dirs() -> None:
    """Create legacy runtime folders only during startup or route execution.

    These folders are user/runtime state and must stay under ``neo_data``; they
    should not be created just because a static tool imports ``neo_app.main``.
    """

    for directory in (
        IMAGE_SOURCE_INPUT_DIR,
        IMAGE_MASK_INPUT_DIR,
        IMAGE_LOG_DIR,
        SCENE_DIRECTOR_PRESET_DIR,
        SCENE_DIRECTOR_IDENTITY_DIR,
        SCENE_DIRECTOR_LAYOUT_DIR,
        SCENE_DIRECTOR_TRAIT_LIBRARY_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)




def _video_runtime_run_id(payload: dict | None, route_id: str) -> str:
    data = payload if isinstance(payload, dict) else {}
    return safe_run_id(data.get("run_id") or data.get("debug_run_id") or data.get("job_id") or data.get("result_id") or f"video_{route_id}_{uuid4().hex[:10]}")


def _video_runtime_payload_summary(payload: dict | None, result: dict | None = None, *, route_id: str = "") -> dict:
    data = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}
    return {
        "schema_id": "neo.video.route_runtime_log.summary.pass_m.v1",
        "route_id": route_id,
        "family": data.get("family") or result.get("family") or "",
        "generation_type": data.get("generation_type") or data.get("mode") or result.get("generation_type") or "",
        "dry_run": bool(data.get("dry_run") or result.get("dry_run")),
        "prompt_char_count": len(str(data.get("prompt") or data.get("positive_prompt") or "")),
        "source_image_present": bool(data.get("source_image") or data.get("image")),
        "source_video_present": bool(data.get("source_video") or data.get("video")),
        "payload_keys": sorted(str(key) for key in data.keys())[:80],
        "result_keys": sorted(str(key) for key in result.keys())[:80],
        "ok": result.get("ok") if "ok" in result else None,
        "queued": bool(result.get("queued")),
        "result_id": result.get("result_id") or "",
        "prompt_id": result.get("prompt_id") or "",
        "error": str(result.get("error") or "")[:500],
    }


def _run_video_runtime_call(route_id: str, event_prefix: str, payload: dict | None, handler) -> dict:
    data = payload if isinstance(payload, dict) else {}
    if str(event_prefix or "").startswith("video.queue") or str(event_prefix or "").startswith("video.finish"):
        _require_backend_connected_for_task(str(data.get("profile_id") or data.get("backend_profile_id") or ""), surface="video", operation="video generation")
    run_id = _video_runtime_run_id(data, route_id)
    log_surface_event("video", f"{event_prefix}.started", run_id=run_id, payload=_video_runtime_payload_summary(data, route_id=route_id))
    try:
        result = handler(data)
        summary = _video_runtime_payload_summary(data, result if isinstance(result, dict) else {}, route_id=route_id)
        record_surface_snapshot("video", "neo_last_compile" if event_prefix.endswith("compile") else "neo_last_payload", summary, run_id=run_id)
        log_surface_event("video", f"{event_prefix}.completed", run_id=run_id, level="INFO" if (not isinstance(result, dict) or result.get("ok") is not False) else "WARNING", payload=summary)
        return result
    except Exception as exc:
        record_surface_error("video", f"Video route failed: {route_id}", exc=exc, payload=_video_runtime_payload_summary(data, route_id=route_id), run_id=run_id)
        raise


def _scene_director_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scene_director_slug(name: str, fallback: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._ -]+", "", str(name or "").strip())
    clean = re.sub(r"\s+", "_", clean).strip("._- ")
    return clean[:80] or fallback


def _scene_director_safe_path(root: Path, name: str, fallback: str) -> Path:
    root = root.resolve()
    path = (root / f"{_scene_director_slug(name, fallback)}.json").resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=400, detail="Invalid Scene Director record name")
    return path


def _scene_director_read_json(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _scene_director_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _scene_director_summary(path: Path, kind: str) -> dict:
    data = _scene_director_read_json(path)
    body_key = "profile" if kind == "identity_profile" else "preset"
    body = data.get(body_key) if isinstance(data.get(body_key), dict) else data
    if not isinstance(body, dict):
        body = {}
    regions = body.get("regions") or body.get("layout") or []
    refs = body.get("reference_images") or []
    return {
        "name": str(data.get("name") or body.get("name") or body.get("profile_name") or path.stem),
        "slug": path.stem,
        "filename": path.name,
        "kind": kind,
        "updated_at": data.get("updated_at") or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "region_count": len(regions) if isinstance(regions, list) else 0,
        "reference_count": len(refs) if isinstance(refs, list) else 0,
        "mode": str(body.get("mode") or body.get("ipadapter_mode") or ""),
    }


def _scene_director_find_path(root: Path, name: str, fallback: str) -> Path | None:
    direct = _scene_director_safe_path(root, name, fallback)
    if direct.exists():
        return direct
    slug = _scene_director_slug(name, fallback)
    for path in root.glob("*.json"):
        if path.stem == slug or path.name == name:
            return path
    return None


def _scene_director_list(root: Path, kind: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "items": [_scene_director_summary(path, kind) for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)]}


def _scene_director_load(root: Path, kind: str, name: str) -> dict:
    path = _scene_director_find_path(root, name, kind)
    if not path:
        raise HTTPException(status_code=404, detail="Scene Director record was not found")
    data = _scene_director_read_json(path)
    body_key = "profile" if kind == "identity_profile" else "preset"
    body = data.get(body_key) if isinstance(data.get(body_key), dict) else data
    return {"ok": True, "name": data.get("name") or path.stem, body_key: body if isinstance(body, dict) else {}, "meta": _scene_director_summary(path, kind)}


def _scene_director_save(root: Path, kind: str, payload: dict) -> dict:
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Scene Director record name is required")
    body_key = "profile" if kind == "identity_profile" else "preset"
    body = (payload or {}).get(body_key) or (payload or {}).get("data") or {}
    if not isinstance(body, dict):
        body = {}
    now = _scene_director_now()
    body = {**body, "name": body.get("name") or name, "version": body.get("version") or 1}
    if kind == "identity_profile":
        body.setdefault("profile_name", name)
        body.setdefault("id", _scene_director_slug(name, "identity_profile"))
        body.setdefault("ipadapter_mode", body.get("mode") or "faceid")
        if not isinstance(body.get("reference_images"), list):
            body["reference_images"] = []
    path = _scene_director_safe_path(root, name, kind)
    _scene_director_write_json(path, {"ok": True, "kind": kind, "version": 1, "name": name, "updated_at": now, body_key: body})
    return {"ok": True, "item": _scene_director_summary(path, kind)}


def _scene_director_delete(root: Path, kind: str, name: str) -> dict:
    path = _scene_director_find_path(root, name, kind)
    if not path:
        raise HTTPException(status_code=404, detail="Scene Director record was not found")
    path.unlink()
    return {"ok": True}


SCENE_DIRECTOR_TRAIT_CATEGORIES = [
    "gender", "ethnicity", "species_race", "build", "skin_tone", "hair",
    "clothing_top", "clothing_bottom", "full_costume", "pose", "expression",
    "accessories", "shoes",
]


def _scene_director_trait_category(value: str) -> str:
    category = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(value or "").strip().lower()).replace(" ", "_")
    aliases = {"race": "species_race", "species": "species_race", "skin": "skin_tone", "top": "clothing_top", "bottom": "clothing_bottom", "costume": "full_costume"}
    category = aliases.get(category, category)
    if category not in SCENE_DIRECTOR_TRAIT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Unsupported Scene Director trait category")
    return category


def _scene_director_trait_file(root: Path, category: str) -> Path:
    return (root / f"{_scene_director_trait_category(category)}.json").resolve()


def _scene_director_read_trait_file(path: Path, category: str, source: str) -> dict:
    data = _scene_director_read_json(path)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    clean_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        prompt_terms = item.get("prompt_terms") if isinstance(item.get("prompt_terms"), list) else []
        custom_terms = item.get("custom_terms") if isinstance(item.get("custom_terms"), list) else []
        label = str(item.get("label") or item.get("name") or item.get("id") or f"Trait {index + 1}").strip()
        item_id = _scene_director_slug(str(item.get("id") or label), f"trait_{index + 1}")
        clean_items.append({
            "id": item_id,
            "label": label,
            "prompt_terms": [str(term).strip() for term in prompt_terms if str(term).strip()],
            "negative_terms": [str(term).strip() for term in (item.get("negative_terms") if isinstance(item.get("negative_terms"), list) else []) if str(term).strip()],
            "custom_terms": [str(term).strip() for term in custom_terms if str(term).strip()],
            "latent_priority": item.get("latent_priority", 0.75),
            "roles": item.get("roles") if isinstance(item.get("roles"), list) else ["character"],
            "source": source,
        })
    return {"schema": "neo.image.scene_director.trait_library.v1", "category": category, "source": source, "items": clean_items}


def _scene_director_trait_libraries() -> dict:
    categories: dict[str, dict] = {}
    for category in SCENE_DIRECTOR_TRAIT_CATEGORIES:
        merged: list[dict] = []
        seen: set[str] = set()
        for root, source in ((SCENE_DIRECTOR_BUILTIN_TRAIT_LIBRARY_DIR, "built_in"), (SCENE_DIRECTOR_TRAIT_LIBRARY_DIR, "user")):
            path = _scene_director_trait_file(root, category)
            if not path.exists():
                continue
            data = _scene_director_read_trait_file(path, category, source)
            for item in data.get("items", []):
                key = str(item.get("id") or item.get("label") or "").lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        categories[category] = {
            "schema": "neo.image.scene_director.trait_library.v1",
            "category": category,
            "label": category.replace("_", " ").title(),
            "items": merged,
            "item_count": len(merged),
            "user_file": str(_scene_director_trait_file(SCENE_DIRECTOR_TRAIT_LIBRARY_DIR, category)),
        }
    return {
        "ok": True,
        "schema": "neo.image.scene_director.trait_libraries.v25_9_8",
        "phase": "V25.9.8",
        "categories": categories,
        "category_order": SCENE_DIRECTOR_TRAIT_CATEGORIES,
        "policy": "Built-in trait library JSON is merged with user-editable neo_data trait library JSON. Character region explicit fields override auto extraction; empty fields fall back to auto extraction.",
    }


def _scene_director_save_trait_item(category: str, payload: dict) -> dict:
    category = _scene_director_trait_category(category)
    data = payload if isinstance(payload, dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else data
    label = str(item.get("label") or item.get("name") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Trait label is required")
    item_id = _scene_director_slug(str(item.get("id") or label), "trait")
    prompt_terms = item.get("prompt_terms") if isinstance(item.get("prompt_terms"), list) else []
    custom = str(item.get("custom") or item.get("custom_text") or "").strip()
    if custom:
        prompt_terms = [*prompt_terms, *[part.strip() for part in re.split(r"[\n,;]+", custom) if part.strip()]]
    clean_item = {
        "id": item_id,
        "label": label,
        "prompt_terms": [str(term).strip() for term in prompt_terms if str(term).strip()],
        "negative_terms": [str(term).strip() for term in (item.get("negative_terms") if isinstance(item.get("negative_terms"), list) else []) if str(term).strip()],
        "latent_priority": item.get("latent_priority", 0.75),
        "roles": item.get("roles") if isinstance(item.get("roles"), list) else ["character"],
    }
    path = _scene_director_trait_file(SCENE_DIRECTOR_TRAIT_LIBRARY_DIR, category)
    existing = _scene_director_read_json(path)
    items = existing.get("items") if isinstance(existing.get("items"), list) else []
    replaced = False
    next_items = []
    for old in items:
        if isinstance(old, dict) and str(old.get("id") or "").lower() == item_id.lower():
            next_items.append(clean_item)
            replaced = True
        elif isinstance(old, dict):
            next_items.append(old)
    if not replaced:
        next_items.append(clean_item)
    _scene_director_write_json(path, {"schema": "neo.image.scene_director.trait_library.v1", "category": category, "source": "user", "items": next_items, "updated_at": _scene_director_now()})
    return {"ok": True, "category": category, "item": clean_item, "replaced": replaced, "file": path.name}



def _uvicorn_log_config(*, dev_mode: bool = False) -> dict:
    """Route uvicorn/server chatter to files by default.

    The launcher terminal should stay product-clean. `--dev` intentionally adds
    console handlers back for FastAPI/Uvicorn debugging.
    """
    formatter = {
        "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    }
    handlers = {
        "server_file": {
            "class": "logging.FileHandler",
            "formatter": "standard",
            "filename": str(SERVER_LOG_PATH),
            "encoding": "utf-8",
        },
        "error_file": {
            "class": "logging.FileHandler",
            "formatter": "standard",
            "filename": str(ERROR_LOG_PATH),
            "encoding": "utf-8",
            "level": "ERROR",
        },
    }
    default_handlers = ["server_file", "error_file"]
    access_handlers = ["server_file"]
    if dev_mode:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stderr",
        }
        default_handlers.append("console")
        access_handlers.append("console")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"standard": formatter},
        "handlers": handlers,
        "loggers": {
            "uvicorn": {"handlers": default_handlers, "level": "INFO" if dev_mode else "WARNING", "propagate": False},
            "uvicorn.error": {"handlers": default_handlers, "level": "INFO" if dev_mode else "WARNING", "propagate": False},
            "uvicorn.access": {"handlers": access_handlers, "level": "INFO", "propagate": False},
            "fastapi": {"handlers": default_handlers, "level": "INFO" if dev_mode else "WARNING", "propagate": False},
        },
    }


def _parse_launch_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Neo Studio V2")
    parser.add_argument("--host", default=os.environ.get("NEO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEO_PORT", "7860")))
    parser.add_argument("--dev", action="store_true", help="Show Uvicorn/access logs in the console and enable reload.")
    parser.add_argument("--verbose", action="store_true", help="Show app startup detail in the console without enabling reload.")
    parser.add_argument("--quiet", action="store_true", help="Only print fatal startup failures from Python launcher.")
    return parser.parse_args()


def _print_startup_banner(args: argparse.Namespace) -> None:
    if args.quiet:
        return
    launcher_already_printed = os.environ.get("NEO_LAUNCHER_PRINTED") == "1"
    logs = log_file_payload()
    lines = [
        "========================================",
        "Neo Studio V2",
        "========================================",
        "",
        "Checking environment...",
        f"Starting Neo Studio at http://{args.host}:{args.port}",
        f"Backend base URL: {os.environ.get('NEO_BACKEND_BASE_URL', 'http://localhost:5001')}",
        f"Console log: {logs['console_log']}",
        f"Server log: {logs['server_log']}",
        f"Error log: {logs['error_log']}",
        f"Generation log: {logs['generation_log']}",
    ]
    if args.dev:
        lines.append("Dev logging: enabled")
    elif args.verbose:
        lines.append("Verbose startup logging: enabled")
    for line in lines:
        if not launcher_already_printed:
            print(line)
        write_console_status(line)


app = FastAPI(title="Neo Studio V2", version="0.11.4.3-live-preview-server-state-phase44-runtime-hardening", lifespan=neo_runtime_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")




@app.get("/api/extensions/scene-director/scene-presets")
def scene_director_list_scene_presets() -> dict:
    return _scene_director_list(SCENE_DIRECTOR_PRESET_DIR, "scene_preset")


@app.get("/api/extensions/scene-director/scene-presets/{preset_name}")
def scene_director_load_scene_preset(preset_name: str) -> dict:
    return _scene_director_load(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", preset_name)


@app.post("/api/extensions/scene-director/scene-presets")
def scene_director_save_scene_preset(payload: dict) -> dict:
    return _scene_director_save(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", payload)


@app.delete("/api/extensions/scene-director/scene-presets/{preset_name}")
def scene_director_delete_scene_preset(preset_name: str) -> dict:
    return _scene_director_delete(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", preset_name)


@app.get("/api/extensions/scene-director/identity-profiles")
def scene_director_list_identity_profiles() -> dict:
    return _scene_director_list(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile")


@app.get("/api/extensions/scene-director/identity-profiles/{profile_name}")
def scene_director_load_identity_profile(profile_name: str) -> dict:
    return _scene_director_load(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", profile_name)


@app.post("/api/extensions/scene-director/identity-profiles")
def scene_director_save_identity_profile(payload: dict) -> dict:
    return _scene_director_save(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", payload)


@app.delete("/api/extensions/scene-director/identity-profiles/{profile_name}")
def scene_director_delete_identity_profile(profile_name: str) -> dict:
    return _scene_director_delete(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", profile_name)


@app.get("/api/extensions/scene-director/region-layout-presets")
def scene_director_list_region_layout_presets() -> dict:
    return _scene_director_list(SCENE_DIRECTOR_LAYOUT_DIR, "region_layout_preset")


@app.get("/api/extensions/scene-director/region-layout-presets/{preset_name}")
def scene_director_load_region_layout_preset(preset_name: str) -> dict:
    return _scene_director_load(SCENE_DIRECTOR_LAYOUT_DIR, "region_layout_preset", preset_name)


@app.post("/api/extensions/scene-director/region-layout-presets")
def scene_director_save_region_layout_preset(payload: dict) -> dict:
    return _scene_director_save(SCENE_DIRECTOR_LAYOUT_DIR, "region_layout_preset", payload)


@app.delete("/api/extensions/scene-director/region-layout-presets/{preset_name}")
def scene_director_delete_region_layout_preset(preset_name: str) -> dict:
    return _scene_director_delete(SCENE_DIRECTOR_LAYOUT_DIR, "region_layout_preset", preset_name)


@app.get("/api/extensions/scene-director/trait-libraries")
def scene_director_list_trait_libraries() -> dict:
    return _scene_director_trait_libraries()


@app.post("/api/extensions/scene-director/trait-libraries/{category}/items")
def scene_director_save_trait_library_item(category: str, payload: dict) -> dict:
    return _scene_director_save_trait_item(category, payload)

# V1-compatible aliases kept so migrated UI snippets and old bookmarks keep working.
@app.get("/api/scene-director/presets")
def scene_director_v1_list_presets() -> dict:
    payload = _scene_director_list(SCENE_DIRECTOR_PRESET_DIR, "scene_preset")
    return {"ok": True, "presets": payload.get("items", [])}


@app.get("/api/scene-director/presets/{preset_name}")
def scene_director_v1_load_preset(preset_name: str) -> dict:
    return _scene_director_load(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", preset_name)


@app.post("/api/scene-director/presets")
def scene_director_v1_save_preset(payload: dict) -> dict:
    return _scene_director_save(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", payload)


@app.delete("/api/scene-director/presets/{preset_name}")
def scene_director_v1_delete_preset(preset_name: str) -> dict:
    return _scene_director_delete(SCENE_DIRECTOR_PRESET_DIR, "scene_preset", preset_name)


@app.get("/api/scene-director/identity-profiles")
def scene_director_v1_list_identity_profiles() -> dict:
    payload = _scene_director_list(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile")
    return {"ok": True, "profiles": payload.get("items", [])}


@app.get("/api/scene-director/identity-profiles/{profile_name}")
def scene_director_v1_load_identity_profile(profile_name: str) -> dict:
    return _scene_director_load(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", profile_name)


@app.post("/api/scene-director/identity-profiles")
def scene_director_v1_save_identity_profile(payload: dict) -> dict:
    return _scene_director_save(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", payload)


@app.delete("/api/scene-director/identity-profiles/{profile_name}")
def scene_director_v1_delete_identity_profile(profile_name: str) -> dict:
    return _scene_director_delete(SCENE_DIRECTOR_IDENTITY_DIR, "identity_profile", profile_name)

@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "app": "Neo Studio V2",
        "phase": "phase-45-full-system-audit-release-candidate",
        "browser_first": True,
    }






@app.get("/api/release-candidate")
def release_candidate() -> dict:
    return release_candidate_payload()


@app.get("/api/runtime/data/bootstrap")
def runtime_data_bootstrap_status() -> dict:
    """Return the latest runtime-data bootstrap diagnostics."""

    return {
        "ok": bool(BOOTSTRAP_STATE.get("ok")),
        "schema_id": BOOTSTRAP_STATE.get("schema_id"),
        "status": BOOTSTRAP_STATE.get("status"),
        "neo_data_root": BOOTSTRAP_STATE.get("neo_data_root"),
        "startup_bootstrap_call": BOOTSTRAP_STATE.get("startup_bootstrap_call") is True,
        "bootstrap_call_site": BOOTSTRAP_STATE.get("bootstrap_call_site"),
        "fastapi_startup_registered": BOOTSTRAP_STATE.get("fastapi_startup_registered") is True,
        "assistant_scope_is_primary": True,
        "legacy_project_workspace_is_compatibility_layer": True,
        "manifest": BOOTSTRAP_STATE.get("manifest"),
        "assistant_scopes": BOOTSTRAP_STATE.get("assistant_scopes"),
        "project_workspace": BOOTSTRAP_STATE.get("project_workspace"),
    }


@app.post("/api/release-candidate/manifest")
def release_candidate_manifest(payload: dict | None = None) -> dict:
    return write_release_candidate_manifest(release_candidate_payload(),)

@app.get("/api/runtime/hardening")
def runtime_hardening() -> dict:
    return runtime_hardening_payload()


@app.post("/api/runtime/hardening/setup")
def runtime_hardening_setup(payload: dict | None = None) -> dict:
    return runtime_hardening_setup_payload(payload or {})

@app.get("/api/debug/image/logs")
def image_debug_logs() -> dict:
    """Return Neo-owned image runtime debug log locations."""
    return latest_log_payload()


@app.get("/api/debug/logs")
def surface_debug_logs() -> dict:
    """Return read-only recursive metadata for Neo surface runtime logs."""
    return all_surface_logs_payload()




@app.get("/api/debug/logs/privacy")
def surface_debug_logs_privacy_policy() -> dict:
    """Return the active runtime log privacy/safety policy. Read-only."""
    return surface_log_privacy_policy_payload()


@app.get("/api/debug/logs/{surface_id}/tail")
def surface_debug_log_tail(
    surface_id: str,
    lines: int = Query(200, ge=1, le=1000),
    file: str | None = Query(None, alias="file"),
    run_id: str | None = Query(None, alias="run_id"),
) -> dict:
    """Return a safe tail of a surface log file. Read-only; no cleanup here."""
    try:
        return surface_log_tail_payload(surface_id, lines=lines, file_name=file, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/debug/logs/{surface_id}")
def surface_debug_log(surface_id: str) -> dict:
    """Return read-only metadata for one Neo surface runtime log root."""
    return latest_surface_log_payload(surface_id)


@app.get("/api/config")
def config() -> dict:
    return {
        "ui_detail_mode": "guided",
        "surface_registry_endpoint": "/api/surfaces",
        "admin_endpoint": "/api/admin",
        "provider_registry_endpoint": "/api/providers",
        "backend_profiles_endpoint": "/api/backend-profiles",
        "extension_registry_endpoint": "/api/extensions",
        "image_base_endpoint": "/api/image/base",
        "model_family_registry_endpoint": "/api/model-families",
    }


@app.get("/api/image/prompt-library")
def image_prompt_library_get(query: str = "") -> dict:
    return list_image_prompt_library(query)


@app.post("/api/image/prompt-library")
def image_prompt_library_create(payload: dict) -> dict:
    result = create_image_prompt_pair(payload if isinstance(payload, dict) else {})
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=", ".join(result.get("errors") or ["Could not save image prompt pair"]))
    return result


@app.put("/api/image/prompt-library/{prompt_pair_id}")
def image_prompt_library_update(prompt_pair_id: str, payload: dict) -> dict:
    result = update_image_prompt_pair(prompt_pair_id, payload if isinstance(payload, dict) else {})
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=", ".join(result.get("errors") or ["Could not update image prompt pair"]))
    return result


@app.delete("/api/image/prompt-library/{prompt_pair_id}")
def image_prompt_library_delete(prompt_pair_id: str) -> dict:
    result = delete_image_prompt_pair(prompt_pair_id)
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=", ".join(result.get("errors") or ["Could not delete image prompt pair"]))
    return result


@app.get("/api/ui-state")
def ui_state_get() -> dict:
    """Read UI state from neo_data instead of browser localStorage."""
    return read_ui_state()


@app.post("/api/ui-state")
def ui_state_post(payload: dict) -> dict:
    """Persist UI state into neo_data/ui_state/ui_state.json."""
    return write_ui_state(payload)


@app.get("/api/ui-presets/{surface}")
def ui_presets_list(surface: str) -> dict:
    try:
        return list_ui_presets(surface)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ui-presets/{surface}")
def ui_presets_create(surface: str, payload: dict) -> dict:
    try:
        return create_ui_preset(surface, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ui-presets/{surface}/default")
def ui_presets_default(surface: str) -> dict:
    try:
        return get_default_ui_preset(surface)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ui-presets/{surface}/{preset_id}")
def ui_presets_get(surface: str, preset_id: str) -> dict:
    try:
        return get_ui_preset(surface, preset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/ui-presets/{surface}/{preset_id}")
def ui_presets_update(surface: str, preset_id: str, payload: dict) -> dict:
    try:
        return update_ui_preset(surface, preset_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/ui-presets/{surface}/{preset_id}")
def ui_presets_delete(surface: str, preset_id: str) -> dict:
    try:
        return delete_ui_preset(surface, preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ui-presets/{surface}/{preset_id}/default")
def ui_presets_make_default(surface: str, preset_id: str) -> dict:
    try:
        return set_default_ui_preset(surface, preset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/api/image/progress/ws")
async def image_progress_ws(websocket: WebSocket):
    """Proxy ComfyUI websocket progress + binary preview frames through Neo.

    V1 used this pattern instead of plain HTTP polling: ComfyUI sends text progress
    events and binary preview frames through /ws?clientId=..., and Neo forwards
    both payload types to the browser. The frontend then parses Comfy's binary
    preview header and displays a blob URL as the live preview.
    """
    await websocket.accept()
    profile_id = str(websocket.query_params.get("profile_id") or "").strip()
    client_id = str(websocket.query_params.get("client_id") or websocket.query_params.get("clientId") or "").strip()
    if not profile_id or not client_id:
        await websocket.send_json({"type": "error", "data": {"message": "profile_id and client_id are required."}})
        await websocket.close(code=1008)
        return
    try:
        provider, _profile = _profile_bound_provider(profile_id)
        base_url = getattr(provider, "base_url", "").rstrip("/")
        if not base_url:
            raise RuntimeError("Backend base URL is missing.")
        parsed = parse.urlparse(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc or parsed.path
        prefix = parsed.path if parsed.netloc else ""
        ws_url = f"{scheme}://{netloc}{prefix}/ws?clientId={parse.quote(client_id)}"
        async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=20) as upstream:
            await websocket.send_json({"type": "proxy_open", "data": {"client_id": client_id, "upstream": ws_url}})
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - websocket route must fail visibly but safely.
        try:
            await websocket.send_json({"type": "error", "data": {"message": f"Live preview proxy failed: {exc}"}})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/api/video/progress/ws")
async def video_progress_ws(websocket: WebSocket):
    """Proxy ComfyUI websocket progress + binary preview frames for Video jobs.

    This mirrors the Image live-preview proxy but gives the Video tab its own
    endpoint so progress bars and preview panels can track queued Comfy prompts.
    """
    await image_progress_ws(websocket)


@app.get("/api/surfaces")
def surfaces() -> dict:
    return get_surface_payload()




@app.get("/api/surfaces/blueprint")
def surfaces_blueprint() -> dict:
    return surface_blueprint_payload(include_disabled=True)


@app.get("/api/surfaces/{surface_id}/blueprint")
def surface_blueprint_route(surface_id: str) -> dict:
    blueprint = surface_blueprint(surface_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail=f"Unknown surface: {surface_id}")
    return blueprint

@app.get("/api/surfaces/{surface_id}")
def surface(surface_id: str) -> dict:
    match = get_surface(surface_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown surface: {surface_id}")
    return model_to_dict(match)


@app.get("/api/surfaces/modules/status")
def surfaces_module_architecture_status() -> dict:
    return module_architecture_status()


@app.get("/api/surfaces/modules/manifest")
def surfaces_module_architecture_manifest() -> dict:
    return module_architecture_manifest()


@app.post("/api/surfaces/modules/audit")
def surfaces_module_architecture_audit() -> dict:
    return module_architecture_audit()


@app.get("/api/surfaces/{surface_id}/module-contract")
def surface_module_contract_route(surface_id: str) -> dict:
    contract = modular_surface_contract(surface_id)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Unknown surface: {surface_id}")
    return contract


@app.get("/api/surfaces/migration/status")
def surface_module_migration_status_route() -> dict:
    return surface_migration_runtime_status()


@app.post("/api/surfaces/migration/audit")
def surface_module_migration_audit_route() -> dict:
    return surface_migration_runtime_audit(write=True)


@app.get("/api/surfaces/migration/admin-memory-cockpit/status")
def admin_memory_cockpit_migration_status_route() -> dict:
    return admin_memory_cockpit_migration_status()


@app.post("/api/surfaces/migration/admin-memory-cockpit/audit")
def admin_memory_cockpit_migration_audit_route() -> dict:
    return admin_memory_cockpit_migration_audit(write=True)




@app.get("/api/surfaces/migration/admin-memory-cockpit/actions/status")
def admin_memory_cockpit_action_migration_status_route() -> dict:
    return admin_memory_cockpit_action_migration_status()


@app.post("/api/surfaces/migration/admin-memory-cockpit/actions/audit")
def admin_memory_cockpit_action_migration_audit_route() -> dict:
    return admin_memory_cockpit_action_migration_audit(write=True)


@app.get("/api/surfaces/migration/assistant-slice/status")
def assistant_surface_slice_migration_status_route() -> dict:
    return assistant_surface_slice_migration_status()


@app.post("/api/surfaces/migration/assistant-slice/audit")
def assistant_surface_slice_migration_audit_route() -> dict:
    return assistant_surface_slice_migration_audit(write=True)



@app.get("/api/surfaces/migration/assistant-deep-panels/status")
def assistant_deep_panel_migration_status_route() -> dict:
    return assistant_deep_panel_migration_status()


@app.post("/api/surfaces/migration/assistant-deep-panels/audit")
def assistant_deep_panel_migration_audit_route() -> dict:
    return assistant_deep_panel_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-slice/status")
def roleplay_surface_slice_migration_status_route() -> dict:
    return roleplay_surface_slice_migration_status()


@app.post("/api/surfaces/migration/roleplay-slice/audit")
def roleplay_surface_slice_migration_audit_route() -> dict:
    return roleplay_surface_slice_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-scene-director-cockpit/status")
def roleplay_scene_director_cockpit_migration_status_route() -> dict:
    return roleplay_scene_director_cockpit_migration_status()


@app.post("/api/surfaces/migration/roleplay-scene-director-cockpit/audit")
def roleplay_scene_director_cockpit_migration_audit_route() -> dict:
    return roleplay_scene_director_cockpit_migration_audit(write=True)


@app.get("/api/surfaces/migration/roleplay-scene-chat-dispatch/status")
def roleplay_scene_chat_dispatch_migration_status_route() -> dict:
    return roleplay_scene_chat_dispatch_migration_status()


@app.post("/api/surfaces/migration/roleplay-scene-chat-dispatch/audit")
def roleplay_scene_chat_dispatch_migration_audit_route() -> dict:
    return roleplay_scene_chat_dispatch_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-transcript-checkpoint/status")
def roleplay_transcript_checkpoint_migration_status_route() -> dict:
    return roleplay_transcript_checkpoint_migration_status()


@app.post("/api/surfaces/migration/roleplay-transcript-checkpoint/audit")
def roleplay_transcript_checkpoint_migration_audit_route() -> dict:
    return roleplay_transcript_checkpoint_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-scene-state-checkpoint-inspector/status")
def roleplay_scene_state_checkpoint_inspector_migration_status_route() -> dict:
    return roleplay_scene_state_checkpoint_inspector_migration_status()


@app.post("/api/surfaces/migration/roleplay-scene-state-checkpoint-inspector/audit")
def roleplay_scene_state_checkpoint_inspector_migration_audit_route() -> dict:
    return roleplay_scene_state_checkpoint_inspector_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-stories-workspace/status")
def roleplay_stories_workspace_migration_status_route() -> dict:
    return roleplay_stories_workspace_migration_status()


@app.post("/api/surfaces/migration/roleplay-stories-workspace/audit")
def roleplay_stories_workspace_migration_audit_route() -> dict:
    return roleplay_stories_workspace_migration_audit(write=True)


@app.get("/api/surfaces/migration/roleplay-archive-provenance-graph/status")
def roleplay_archive_provenance_graph_migration_status_route() -> dict:
    return roleplay_archive_provenance_graph_migration_status()


@app.post("/api/surfaces/migration/roleplay-archive-provenance-graph/audit")
def roleplay_archive_provenance_graph_migration_audit_route() -> dict:
    return roleplay_archive_provenance_graph_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-compile-runtime-deep/status")
def roleplay_compile_runtime_deep_migration_status_route() -> dict:
    return roleplay_compile_runtime_deep_migration_status()


@app.post("/api/surfaces/migration/roleplay-compile-runtime-deep/audit")
def roleplay_compile_runtime_deep_migration_audit_route() -> dict:
    return roleplay_compile_runtime_deep_migration_audit(write=True)



@app.get("/api/surfaces/migration/roleplay-forge-builder/status")
def roleplay_forge_builder_migration_status_route() -> dict:
    return roleplay_forge_builder_migration_status()


@app.post("/api/surfaces/migration/roleplay-forge-builder/audit")
def roleplay_forge_builder_migration_audit_route() -> dict:
    return roleplay_forge_builder_migration_audit(write=True)


@app.get("/api/surfaces/migration/roleplay-forge-sqlite-template-inspector/status")
def roleplay_forge_sqlite_template_inspector_migration_status_route() -> dict:
    return roleplay_forge_sqlite_template_inspector_migration_status()


@app.post("/api/surfaces/migration/roleplay-forge-sqlite-template-inspector/audit")
def roleplay_forge_sqlite_template_inspector_migration_audit_route() -> dict:
    return roleplay_forge_sqlite_template_inspector_migration_audit(write=True)


@app.get("/api/surfaces/migration/roleplay-forge-advanced-import-scope/status")
def roleplay_forge_advanced_import_scope_migration_status_route() -> dict:
    return roleplay_forge_advanced_import_scope_migration_status()


@app.post("/api/surfaces/migration/roleplay-forge-advanced-import-scope/audit")
def roleplay_forge_advanced_import_scope_migration_audit_route() -> dict:
    return roleplay_forge_advanced_import_scope_migration_audit(write=True)

@app.get("/api/ui/modern/status")
def modern_ui_system_status_route() -> dict:
    return modern_ui_system_status()


@app.post("/api/ui/modern/audit")
def modern_ui_system_audit_route() -> dict:
    return modern_ui_system_audit(write=True)




def _admin_control_center_payload() -> dict:
    """Aggregate Admin control-center status without changing surface behavior."""
    admin_payload = get_admin_payload()
    surfaces_payload = get_surface_payload(include_disabled=True)
    providers_payload = get_provider_payload()
    extensions_payload = get_extension_payload()
    engine_payload = admin_engine_state_payload()
    memory_payload = model_to_dict(get_memory_service().capabilities())

    surfaces = surfaces_payload.get("surfaces", [])
    providers = providers_payload.get("providers", [])
    extensions = extensions_payload.get("extensions", [])
    engine_readiness = engine_payload.get("readiness", {}) or {}
    storage_paths = ((admin_payload.get("global") or {}).get("storage_paths") or {})
    admin_surfaces = admin_payload.get("surfaces", {}) or {}

    def _status_counts(items: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _safe_exists(path_value: str | None) -> bool:
        if not path_value:
            return False
        path = Path(path_value)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path.exists()

    storage = [
        {"key": key, "path": value, "exists": _safe_exists(value)}
        for key, value in storage_paths.items()
    ]

    records_root = ROOT_DIR / "neo_system_records"
    record_docs = sorted(records_root.rglob("*.md")) if records_root.exists() else []
    log_root = ROOT_DIR / "neo_data" / "logs"
    logs = sorted(log_root.glob("*.log")) if log_root.exists() else []
    surface_logs = all_surface_logs_payload()

    health_checks = [
        {"id": "admin_config", "label": "Admin config", "status": "ready" if admin_payload.get("admin_version") else "needs attention", "detail": f"Version {admin_payload.get('admin_version', 'unknown')}"},
        {"id": "provider_registry", "label": "Provider registry", "status": "ready" if providers else "needs setup", "detail": f"{len(providers)} provider(s) registered"},
        {"id": "extension_registry", "label": "Extension registry", "status": "ready" if extensions else "needs setup", "detail": f"{len(extensions)} extension record(s)"},
        {"id": "memory", "label": "Memory service", "status": "ready" if memory_payload.get("sqlite") or memory_payload.get("sqlite_available") else "available", "detail": memory_payload.get("mode") or memory_payload.get("status") or "memory status loaded"},
        {"id": "memory_engine", "label": "Memory Engine", "status": "ready" if engine_readiness.get("text_bridge_ready") else "needs setup", "detail": "Shared text bridge, embeddings, reranker, vector store, indexing, and retrieval are controlled here."},
        {"id": "records", "label": "System records", "status": "ready" if records_root.exists() else "missing", "detail": f"{len(record_docs)} markdown record(s)"},
    ]

    attention_statuses = {"missing", "error", "failed", "blocked", "needs setup", "needs attention", "warning"}
    attention_items = [
        {
            "source": "health",
            "label": check.get("label") or check.get("id"),
            "status": check.get("status") or "unknown",
            "detail": check.get("detail") or "",
            "priority": "high" if check.get("status") in {"missing", "error", "failed", "blocked"} else "medium",
        }
        for check in health_checks
        if str(check.get("status") or "").lower() in attention_statuses
    ]

    cockpit_groups = [
        {
            "group_id": "system_health",
            "label": "System Health",
            "description": "Admin config, providers, extensions, records, and runtime health signals.",
            "status": "ready" if not attention_items else "needs attention",
            "anchor": "health",
            "metrics": [f"{len(health_checks)} checks", f"{len(attention_items)} attention"],
        },
        {
            "group_id": "runtime_packaging",
            "label": "Packaging + Runtime",
            "description": "Startup diagnostics, first-run setup, portable paths, writable Neo data folders, and dependency checks.",
            "status": runtime_hardening_payload().get("status") or "ready",
            "anchor": "health",
            "metrics": [f"{runtime_hardening_payload().get('required_failure_count', 0)} blockers", f"{runtime_hardening_payload().get('warning_count', 0)} warnings"],
        },
        {
            "group_id": "release_candidate",
            "label": "Release Candidate",
            "description": "Phase 45 full-system audit, implementation inventory, validation scope, and release-candidate manifest.",
            "status": release_candidate_payload(include_syntax_checks=False).get("status") or "release-candidate",
            "anchor": "health",
            "metrics": [f"{release_candidate_payload(include_syntax_checks=False).get('summary', {}).get('failure_count', 0)} failures", "Phase 45 RC"],
        },
        {
            "group_id": "memory_engine",
            "label": "Memory Engine",
            "description": "Memory service, retrieval profiles, diagnostics, index jobs, and vector controls.",
            "status": "ready" if engine_readiness.get("text_bridge_ready") else "needs setup",
            "anchor": "memory",
            "metrics": [f"{sum(1 for value in engine_readiness.values() if value)}/{len(engine_readiness)} ready", f"{len(((admin_payload.get('global') or {}).get('memory_resources') or {}).get('namespaces_enabled', []))} namespaces"],
        },
        {
            "group_id": "project_workspace",
            "label": "Project Workspace",
            "description": "Legacy Admin compatibility tools: asset tray, briefs, review queue, packages, QA, and historical cross-surface actions. Assistant Scope is primary.",
            "status": project_workspace_status_payload().get("status") or "ready",
            "anchor": "memory",
            "metrics": ["briefs", "reviews", "packages", "QA"],
        },
        {
            "group_id": "assistant_operator",
            "label": "Assistant + Operator",
            "description": "Tool registry, permission profiles, action review, execution ledger, and operator availability.",
            "status": operator_status_payload().get("status") or "ready",
            "anchor": "memory",
            "metrics": [f"{len(effective_tool_registry_payload().get('tools', []))} tools", tool_ledger_status_payload().get("status", "ledger")],
        },
    ]

    admin_cockpit = {
        "schema_id": "neo.admin.cockpit.v1",
        "phase": 43,
        "status": "needs attention" if attention_items else "ready",
        "attention_count": len(attention_items),
        "attention_items": attention_items[:12],
        "groups": cockpit_groups,
        "quick_actions": [
            {"label": "Refresh cockpit", "action": "reloadAdminControlCenter", "safe": True},
            {"label": "Open Memory QA", "target_subtab": "memory", "safe": True},
            {"label": "Open Health", "target_subtab": "health", "safe": True},
            {"label": "Open Engine", "target_subtab": "engine", "safe": True},
        ],
        "layout_policy": "Group status first, place safe refresh/read actions before write actions, and surface attention items before deep settings.",
    }

    surface_controls = []
    for surface in surfaces:
        sid = surface.get("surface_id") or "unknown"
        admin_surface = admin_surfaces.get(sid) or {}
        surface_controls.append({
            "surface_id": sid,
            "display_name": surface.get("display_name") or sid.title(),
            "status": surface.get("status") or "unknown",
            "backends": admin_surface.get("backends", []),
            "admin_sections": admin_surface.get("admin_sections", []),
            "extensions_scope": admin_surface.get("extensions_scope") or sid,
        })

    return {
        "schema_id": "neo.admin.control_center.v1",
        "status": "ready",
        "overview": {
            "surface_count": len(surfaces),
            "surface_status_counts": _status_counts(surfaces),
            "provider_count": len(providers),
            "extension_count": len(extensions),
            "enabled_extension_count": sum(1 for item in extensions if item.get("enabled")),
            "memory_namespaces": ((admin_payload.get("global") or {}).get("memory_resources") or {}).get("namespaces_enabled", []),
            "engine_ready_count": sum(1 for value in engine_readiness.values() if value),
            "engine_check_count": len(engine_readiness),
        },
        "surface_controls": surface_controls,
        "surface_blueprints": surface_blueprint_payload(include_disabled=True),
        "surface_module_architecture": module_architecture_status(),
        "backends": {"providers": providers, "profiles": list_backend_profiles()},
        "models": {"families_endpoint": "/api/model-families", "parameter_profiles_endpoint": "/api/model-families/parameter-profiles", "model_guide": admin_model_catalog_summary_payload()},
        "memory": memory_payload,
        "engine": engine_payload,
        "memory_engine": get_memory_service().memory_engine_status(),
        "memory_diagnostics": get_memory_service().health_dashboard(),
        "memory_observability": get_memory_service().observability_status(),
        "operator": operator_status_payload(),
        "voice_input": voice_input_status_payload(),
        "internet_access": internet_access_status_payload(),
        "tool_registry": effective_tool_registry_payload(),
        "tool_permission_profiles": permission_profiles_payload(),
        "tool_execution_ledger": tool_ledger_status_payload(),
        "project_workspace": project_workspace_status_payload(),
        "project_asset_tray": project_workspace_asset_tray_payload(),
        "project_delivery_dashboard": project_delivery_dashboard_payload(),
        "project_client_status": project_client_status_view_payload(),
        "project_activity_intelligence": project_activity_intelligence_payload(),
        "project_smart_brief": project_smart_brief_payload(),
        "project_briefs": project_briefs_payload(),
        "project_deliverable_tracker": project_deliverable_tracker_payload(),
        "project_review_queue": project_review_queue_payload(),
        "project_approval_workflow": project_approval_workflow_payload(),
        "project_package_builder": project_package_builder_payload(),
        "runtime_hardening": runtime_hardening_payload(),
        "release_candidate": release_candidate_payload(include_syntax_checks=False),
        "memory_project_qa": memory_project_qa_payload(),
        "project_surface_actions": project_surface_actions_payload(),
        "retrieval_profiles": get_memory_service().retrieval_profiles(),
        "libraries": {"storage_paths": storage_paths, "namespaces": ((admin_payload.get("global") or {}).get("memory_resources") or {}).get("namespaces_enabled", [])},
        "storage": {"paths": storage},
        "admin_cockpit": admin_cockpit,
        "health": {"checks": health_checks},
        "logs": {"root": "neo_data/logs", "files": [{"name": item.name, "size": item.stat().st_size} for item in logs[:20]], **surface_logs},
        "records": {"root": "neo_system_records", "doc_count": len(record_docs), "policy": "Update mapped records and changelog first. Create a new record only when no mapped source-of-truth exists."},
    }

@app.get("/api/admin")
def admin() -> dict:
    return get_admin_payload()


@app.get("/api/admin/control-center")
def admin_control_center() -> dict:
    return _admin_control_center_payload()


@app.get("/api/admin/surfaces/{surface_id}")
def admin_surface(surface_id: str) -> dict:
    match = get_surface_admin(surface_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown admin surface: {surface_id}")
    return match


@app.get("/api/admin/models/catalog")
def admin_models_catalog() -> dict:
    return admin_model_catalog_payload()


@app.get("/api/admin/models/folder-rules")
def admin_models_folder_rules() -> dict:
    return admin_model_folder_rules_payload()


@app.get("/api/admin/models/category-map")
def admin_models_category_map() -> dict:
    return admin_model_category_map_payload()


@app.get("/api/admin/models/schema")
def admin_models_schema() -> dict:
    return admin_model_schema_payload()


@app.get("/api/admin/models/paths")
def admin_models_paths() -> dict:
    return admin_model_paths_state_payload()


@app.post("/api/admin/models/paths")
def admin_models_paths_save(payload: dict | None = None) -> dict:
    return admin_model_paths_save_payload(payload)


@app.post("/api/admin/models/resolve-target")
def admin_models_resolve_target(payload: dict | None = None) -> dict:
    return admin_model_target_resolution_payload(payload)


@app.get("/api/admin/models/installed")
def admin_models_installed() -> dict:
    return admin_model_installed_state_payload()


@app.post("/api/admin/models/scan-installed")
def admin_models_scan_installed(payload: dict | None = None) -> dict:
    return admin_model_scan_installed_payload(payload)


@app.post("/api/admin/models/remote/huggingface/metadata")
def admin_models_huggingface_metadata(payload: dict | None = None) -> dict:
    return admin_model_huggingface_metadata_state_payload(payload)


@app.post("/api/admin/models/remote/huggingface/discover-files")
def admin_models_huggingface_discover_files(payload: dict | None = None) -> dict:
    return admin_model_huggingface_discover_files_state_payload(payload)


@app.post("/api/admin/models/remote/civitai/metadata")
def admin_models_civitai_metadata(payload: dict | None = None) -> dict:
    return admin_model_civitai_metadata_state_payload(payload)


@app.post("/api/admin/models/remote/civitai/discover-files")
def admin_models_civitai_discover_files(payload: dict | None = None) -> dict:
    return admin_model_civitai_discover_files_state_payload(payload)


@app.post("/api/admin/models/filter")
def admin_models_filter(payload: dict | None = None) -> dict:
    return admin_model_filter_state_payload(payload)


@app.post("/api/admin/models/download/plan")
def admin_models_download_plan(payload: dict | None = None) -> dict:
    return admin_model_download_plan_state_payload(payload)


@app.post("/api/admin/models/download/start")
def admin_models_download_start(payload: dict | None = None) -> dict:
    return admin_model_download_start_state_payload(payload)


@app.post("/api/admin/models/download/cancel")
def admin_models_download_cancel(payload: dict | None = None) -> dict:
    return admin_model_download_cancel_state_payload(payload)


@app.get("/api/admin/models/download/jobs")
def admin_models_download_jobs() -> dict:
    return admin_model_download_jobs_state_payload()


@app.get("/api/admin/models/download/jobs/{job_id}")
def admin_models_download_job(job_id: str) -> dict:
    return admin_model_download_job_state_payload(job_id)


@app.get("/api/admin/models/packs")
def admin_models_packs() -> dict:
    return admin_model_packs_state_payload()


@app.post("/api/admin/models/packs/status")
def admin_models_pack_status(payload: dict | None = None) -> dict:
    return admin_model_pack_status_state_payload(payload)


@app.post("/api/admin/models/packs/download/plan")
def admin_models_pack_download_plan(payload: dict | None = None) -> dict:
    return admin_model_pack_download_plan_state_payload(payload)



@app.get("/api/admin/models/workspaces")
def admin_models_workspaces() -> dict:
    return admin_model_workspace_requirements_state_payload()


@app.post("/api/admin/models/workspaces/status")
def admin_models_workspace_status(payload: dict | None = None) -> dict:
    return admin_model_workspace_status_state_payload(payload)


@app.post("/api/admin/models/workspaces/download/plan")
def admin_models_workspace_download_plan(payload: dict | None = None) -> dict:
    return admin_model_workspace_download_plan_state_payload(payload)

@app.get("/api/admin/engine/state")
def admin_engine_state() -> dict:
    return admin_engine_state_payload()


@app.post("/api/admin/engine/runtime-defaults")
def admin_engine_runtime_defaults(payload: dict | None = None) -> dict:
    return update_runtime_defaults(payload)


@app.post("/api/admin/engine/retrieval-defaults")
def admin_engine_retrieval_defaults(payload: dict | None = None) -> dict:
    return update_retrieval_defaults(payload)


@app.post("/api/admin/engine/model-paths")
def admin_engine_model_paths(payload: dict | None = None) -> dict:
    return update_model_paths(payload)


@app.get("/api/admin/engine/semantic/state")
def admin_engine_semantic_state() -> dict:
    return semantic_engine_state_payload()


@app.post("/api/admin/engine/semantic/test")
def admin_engine_semantic_test(payload: dict | None = None) -> dict:
    return semantic_engine_test_payload(payload)




@app.get("/api/admin/engine/index-jobs")
def admin_engine_index_jobs() -> dict:
    return index_job_queue_state_payload()


@app.post("/api/admin/engine/index-jobs/create")
def admin_engine_index_job_create(payload: dict | None = None) -> dict:
    try:
        return create_index_job_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/engine/index-jobs/cancel")
def admin_engine_index_job_cancel(payload: dict | None = None) -> dict:
    data = payload or {}
    try:
        return cancel_index_job_payload(str(data.get("job_id") or ""))
    except KeyError:
        raise HTTPException(status_code=404, detail="Index job not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/admin/engine/index-jobs/log")
def admin_engine_index_job_log(job_id: str, tail_lines: int = 200) -> dict:
    return read_index_job_log_payload(job_id, tail_lines=tail_lines)

@app.get("/api/admin/engine/chroma/state")
def admin_engine_chroma_state() -> dict:
    return chroma_collection_state_payload()


@app.post("/api/admin/engine/chroma/export")
def admin_engine_chroma_export(payload: dict | None = None) -> dict:
    return export_chroma_collection_payload(payload)


@app.get("/api/admin/engine/chroma/export/download")
def admin_engine_chroma_export_download(archive: str) -> FileResponse:
    try:
        path = export_archive_path(archive)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Export archive not found")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/admin/engine/chroma/import-path")
def admin_engine_chroma_import_path(payload: dict | None = None) -> dict:
    payload = payload or {}
    archive_path = str(payload.get("archive_path") or "")
    if not archive_path:
        raise HTTPException(status_code=400, detail="archive_path is required")
    try:
        return import_chroma_archive_payload(archive_path, mode=str(payload.get("mode") or "merge_safe"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Import archive not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))




@app.get("/api/roleplay/storage/audit")
def roleplay_storage_audit(write_report: bool = False) -> dict:
    return roleplay_storage_audit_payload(write_report=write_report)


@app.post("/api/roleplay/storage/lock-contract")
def roleplay_storage_lock_contract(payload: dict | None = None) -> dict:
    return lock_roleplay_storage_contract_payload(payload or {})


@app.get("/api/roleplay/memory/sandbox-contract")
def roleplay_memory_sandbox_contract(write_report: bool = False) -> dict:
    return sandbox_contract_payload(write_report=write_report)


@app.post("/api/roleplay/memory/ensure-sandbox-schema")
def roleplay_memory_ensure_sandbox_schema(payload: dict | None = None) -> dict:
    return ensure_roleplay_sandbox_schema()


@app.post("/api/roleplay/memory/derive-sandbox-context")
def roleplay_memory_derive_sandbox_context(payload: dict | None = None) -> dict:
    payload = payload or {}
    record_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    return {
        "schema_id": "neo.roleplay.memory.derived_sandbox_context.v1",
        "status": "derived",
        "context": derive_sandbox_context(record_payload, record_id=str(payload.get("record_id") or ""), kind=str(payload.get("kind") or "")),
    }

@app.get("/api/roleplay/sqlite-upgrade/contract")
def roleplay_sqlite_upgrade_contract(write_report: bool = True) -> dict:
    return roleplay_sqlite_upgrade_contract_payload(write_report=write_report)


@app.post("/api/roleplay/sqlite-upgrade/ensure-schema")
def roleplay_sqlite_upgrade_ensure_schema(payload: dict | None = None) -> dict:
    payload = payload or {}
    return ensure_roleplay_sqlite_upgrade_schema(rebuild_search=bool(payload.get("rebuild_search")))


@app.get("/api/roleplay/sqlite-upgrade/health")
def roleplay_sqlite_upgrade_health(write_report: bool = True) -> dict:
    return roleplay_sqlite_health_payload(write_report=write_report)


@app.post("/api/roleplay/sqlite-upgrade/rebuild-search")
def roleplay_sqlite_upgrade_rebuild_search(payload: dict | None = None) -> dict:
    return rebuild_roleplay_memory_fts_payload(payload or {})



@app.get("/api/roleplay/embedding-reranker/contract")
def roleplay_embedding_reranker_contract(write_report: bool = True) -> dict:
    return roleplay_embedding_reranker_contract_payload(write_report=write_report)


@app.get("/api/roleplay/embedding-reranker/state")
def roleplay_embedding_reranker_state(write_report: bool = True) -> dict:
    return roleplay_embedding_reranker_state_payload(write_report=write_report)


@app.post("/api/roleplay/embedding-reranker/embed")
def roleplay_embedding_reranker_embed(payload: dict | None = None) -> dict:
    return embed_roleplay_texts_payload(payload or {})


@app.post("/api/roleplay/embedding-reranker/rerank")
def roleplay_embedding_reranker_rerank(payload: dict | None = None) -> dict:
    return rerank_roleplay_results_payload(payload or {})


@app.post("/api/roleplay/embedding-reranker/index-search-documents")
def roleplay_embedding_reranker_index_search_documents(payload: dict | None = None) -> dict:
    return index_roleplay_search_documents_payload(payload or {})


@app.get("/api/roleplay/chroma-mirror/contract")
def roleplay_chroma_mirror_contract(write_report: bool = True) -> dict:
    return roleplay_chroma_mirror_contract_payload(write_report=write_report)


@app.get("/api/roleplay/chroma-mirror/state")
def roleplay_chroma_mirror_state(write_report: bool = True) -> dict:
    return roleplay_chroma_mirror_state_payload(write_report=write_report)


@app.post("/api/roleplay/chroma-mirror/ensure-schema")
def roleplay_chroma_mirror_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_roleplay_chroma_mirror_schema()


@app.post("/api/roleplay/chroma-mirror/mirror")
def roleplay_chroma_mirror(payload: dict | None = None) -> dict:
    return mirror_roleplay_vectors_to_chroma_payload(payload or {})


@app.post("/api/roleplay/chroma-mirror/reset-status")
def roleplay_chroma_mirror_reset_status(payload: dict | None = None) -> dict:
    return reset_roleplay_chroma_mirror_status_payload(payload or {})

@app.get("/api/roleplay/runtime-retrieval/contract")
def roleplay_runtime_retrieval_contract(write_report: bool = True) -> dict:
    return runtime_retrieval_lane_contract_payload(write_report=write_report)


@app.get("/api/roleplay/runtime-retrieval/state")
def roleplay_runtime_retrieval_state(write_report: bool = True) -> dict:
    return runtime_retrieval_lane_state_payload(write_report=write_report)


@app.post("/api/roleplay/runtime-retrieval/run")
def roleplay_runtime_retrieval_run(payload: dict | None = None) -> dict:
    result = run_runtime_retrieval_payload(payload or {})
    try:
        search = result.get("search", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.runtime_retrieval.searched",
            "payload": {
                "query": search.get("query"),
                "scope_id": search.get("scope_id"),
                "result_count": search.get("result_count"),
                "candidate_count": search.get("candidate_count"),
                "trace_id": search.get("trace_id"),
                "mode": search.get("mode"),
            },
        })
    except Exception:
        pass
    return result






@app.get("/api/roleplay/retrieval-labels/contract")
def roleplay_retrieval_labels_contract(write_report: bool = True) -> dict:
    return retrieval_label_debug_contract_payload(write_report=write_report)


@app.get("/api/roleplay/retrieval-labels/state")
def roleplay_retrieval_labels_state(write_report: bool = True) -> dict:
    return retrieval_label_debug_state_payload(write_report=write_report)


@app.post("/api/roleplay/retrieval-labels/ensure-schema")
def roleplay_retrieval_labels_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_retrieval_label_debug_schema()



@app.get("/api/roleplay/large-json-test-validation/contract")
def roleplay_large_json_test_validation_contract(write_report: bool = True) -> dict:
    return large_json_test_validation_contract_payload(write_report=write_report)


@app.get("/api/roleplay/large-json-test-validation/state")
def roleplay_large_json_test_validation_state() -> dict:
    return large_json_test_validation_state_payload()


@app.post("/api/roleplay/large-json-test-validation/ensure-schema")
def roleplay_large_json_test_validation_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_large_json_test_validation_schema()


@app.post("/api/roleplay/large-json-test-validation/run")
def roleplay_large_json_test_validation_run(payload: dict | None = None) -> dict:
    return run_large_json_test_validation_payload(payload or {})




@app.get("/api/roleplay/scoped-compile/contract")
def roleplay_scoped_compile_contract(write_report: bool = True) -> dict:
    return scoped_compile_contract_payload(write_report=write_report)


@app.get("/api/roleplay/scoped-compile/state")
def roleplay_scoped_compile_state(write_report: bool = True) -> dict:
    return scoped_compile_state_payload(write_report=write_report)


@app.post("/api/roleplay/scoped-compile/ensure-schema")
def roleplay_scoped_compile_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_scoped_compile_schema()


@app.post("/api/roleplay/scoped-compile/preview-plan")
def roleplay_scoped_compile_preview_plan(payload: dict | None = None) -> dict:
    return build_compile_plan_payload(payload or {})


@app.post("/api/roleplay/scoped-compile/execute-plan")
def roleplay_scoped_compile_execute_plan(payload: dict | None = None) -> dict:
    return execute_compile_plan_payload(payload or {})

@app.get("/api/roleplay/scope-build/contract")
def roleplay_scope_build_contract(write_report: bool = True) -> dict:
    return scope_build_contract_payload(write_report=write_report)


@app.get("/api/roleplay/scope-build/state")
def roleplay_scope_build_state(write_report: bool = True) -> dict:
    return scope_build_state_payload(write_report=write_report)


@app.post("/api/roleplay/scope-build/ensure-schema")
def roleplay_scope_build_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_scope_build_schema()


@app.get("/api/roleplay/scope-build/scopes")
def roleplay_scope_build_scopes(limit: int = 400, scope_type: str = "") -> dict:
    scopes = list_scope_build_options(limit=limit, scope_type=scope_type)
    return {"schema_id": "neo.roleplay.scope_build.scopes.v1", "status": "ready", "scope_type": scope_type or "all", "scope_count": len(scopes), "scopes": scopes}



@app.get("/api/roleplay/scope-build/discovery-debug")
def roleplay_scope_build_discovery_debug() -> dict:
    return scope_discovery_debug_payload()

@app.post("/api/roleplay/scope-build/linked-records")
def roleplay_scope_build_linked_records(payload: dict | None = None) -> dict:
    return linked_records_for_scope(payload or {})


@app.post("/api/roleplay/scope-build/preview")
def roleplay_scope_build_preview(payload: dict | None = None) -> dict:
    return preview_scope_build_payload(payload or {})


@app.post("/api/roleplay/scope-build/build")
def roleplay_scope_build_build(payload: dict | None = None) -> dict:
    return build_scope_memory_runtime_payload(payload or {})


@app.get("/api/roleplay/runtime-presets/contract")
def roleplay_runtime_presets_contract(write_report: bool = True) -> dict:
    return runtime_presets_contract_payload(write_report=write_report)


@app.get("/api/roleplay/runtime-presets/state")
def roleplay_runtime_presets_state(write_report: bool = True) -> dict:
    return runtime_presets_state_payload(write_report=write_report)


@app.post("/api/roleplay/runtime-presets/ensure-schema")
def roleplay_runtime_presets_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_runtime_presets_schema()


@app.post("/api/roleplay/runtime-presets/prepare-scene-packet")
def roleplay_runtime_presets_prepare_scene_packet(payload: dict | None = None) -> dict:
    return scene_packet_payload_from_preset(payload or {})


@app.post("/api/roleplay/runtime-presets/build-scene-packet")
def roleplay_runtime_presets_build_scene_packet(payload: dict | None = None) -> dict:
    return build_scene_packet_from_runtime_preset(payload or {})



@app.post("/api/roleplay/runtime-presets/build-scene-packet-job")
def roleplay_runtime_presets_build_scene_packet_job(payload: dict | None = None) -> dict:
    return start_scene_packet_build_job(payload or {})


@app.get("/api/roleplay/runtime-presets/build-scene-packet-job/{job_id}")
def roleplay_runtime_presets_build_scene_packet_job_status(job_id: str) -> dict:
    return scene_packet_build_job_status(job_id)

@app.get("/api/roleplay/regression-tests/contract")
def roleplay_regression_tests_contract(write_report: bool = True) -> dict:
    return roleplay_regression_tests_contract_payload(write_report=write_report)


@app.get("/api/roleplay/regression-tests/state")
def roleplay_regression_tests_state() -> dict:
    return roleplay_regression_tests_state_payload()


@app.post("/api/roleplay/regression-tests/ensure-schema")
def roleplay_regression_tests_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_roleplay_regression_tests_schema()


@app.post("/api/roleplay/regression-tests/run")
def roleplay_regression_tests_run(payload: dict | None = None) -> dict:
    return run_roleplay_regression_tests_payload(payload or {})

@app.get("/api/roleplay/scene-packet-categories/contract")
def roleplay_scene_packet_categories_contract(write_report: bool = True) -> dict:
    return scene_packet_categories_contract_payload(write_report=write_report)


@app.get("/api/roleplay/scene-packet-categories/state")
def roleplay_scene_packet_categories_state() -> dict:
    return scene_packet_categories_state_payload()


@app.post("/api/roleplay/scene-packet-categories/ensure")
def roleplay_scene_packet_categories_ensure(payload: dict | None = None) -> dict:
    return ensure_scene_packet_categories_payload(payload or {})

@app.get("/api/roleplay/scene-packet/contract")
def roleplay_scene_packet_contract(write_report: bool = True) -> dict:
    return scene_packet_builder_contract_payload(write_report=write_report)


@app.get("/api/roleplay/scene-packet/state")
def roleplay_scene_packet_state(write_report: bool = True) -> dict:
    return scene_packet_builder_state_payload(write_report=write_report)


@app.post("/api/roleplay/scene-packet/build")
def roleplay_scene_packet_build(payload: dict | None = None) -> dict:
    result = build_scene_packet_payload(payload or {})
    try:
        packet = result.get("scene_packet", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene_packet.built",
            "payload": {
                "scene_packet_id": packet.get("scene_packet_id"),
                "scene_id": packet.get("scene_id"),
                "scope_id": packet.get("scope_id"),
                "selected_entities": (packet.get("counts") or {}).get("selected_entities"),
                "retrieved_results": (packet.get("counts") or {}).get("retrieved_results"),
                "trace_id": (packet.get("retrieval_trace") or {}).get("trace_id"),
            },
        })
    except Exception:
        pass
    return result

@app.get("/api/roleplay/engine-bridge/state")
def roleplay_engine_bridge() -> dict:
    return roleplay_engine_bridge_state()


@app.get("/api/roleplay/memory/state")
def roleplay_memory_state() -> dict:
    memory_state = roleplay_memory_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.memory.state.loaded",
            "payload": {
                "sqlite_ready": memory_state.get("sqlite", {}).get("ready"),
                "entity_count": memory_state.get("sqlite", {}).get("table_counts", {}).get("rp_entities", 0),
            },
        })
    except Exception:
        pass
    return memory_state


@app.post("/api/roleplay/memory/sync-foundation")
def roleplay_memory_sync_foundation(payload: dict | None = None) -> dict:
    data = payload or {}
    result = sync_forge_memory_foundation_payload(data.get("kind"))
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.memory.foundation.synced",
            "payload": {
                "kind": result.get("kind"),
                "synced_count": result.get("synced_count"),
                "error_count": result.get("error_count"),
            },
        })
    except Exception:
        pass
    return result






@app.get("/api/roleplay/relationship/schema-contract")
def roleplay_relationship_schema_contract(write_report: bool = False) -> dict:
    return relationship_schema_contract_payload(write_report=write_report)


@app.post("/api/roleplay/relationship/normalize-record")
def roleplay_relationship_normalize_record(payload: dict | None = None) -> dict:
    return normalize_relationship_record_payload(payload or {})


@app.post("/api/roleplay/relationship/repair-all")
def roleplay_relationship_repair_all(payload: dict | None = None) -> dict:
    return repair_all_relationship_records_payload(payload or {})

@app.get("/api/roleplay/memory/builder-compiler-contract")
def roleplay_memory_builder_compiler_contract(write_report: bool = False) -> dict:
    return builder_memory_compiler_contract_payload(write_report=write_report)


@app.post("/api/roleplay/memory/compile-builder-record")
def roleplay_memory_compile_builder_record(payload: dict | None = None) -> dict:
    try:
        result = compile_builder_record_memory_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        compiled = result.get("compile", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.memory.builder_record.compiled",
            "payload": {
                "record_id": compiled.get("record_id"),
                "kind": compiled.get("kind"),
                "fragment_count": compiled.get("fragment_count"),
                "edge_count": compiled.get("edge_count"),
                "scope_id": compiled.get("scope_id"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/memory/compile-all-builder-records")
def roleplay_memory_compile_all_builder_records(payload: dict | None = None) -> dict:
    result = compile_all_builder_records_memory_payload(payload or {})
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.memory.builder_records.compiled_all",
            "payload": {
                "kind": result.get("kind"),
                "record_count": result.get("record_count"),
                "compiled_count": result.get("compiled_count"),
                "fragment_count": result.get("fragment_count"),
                "edge_count": result.get("edge_count"),
                "error_count": result.get("error_count"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/human-memory/state")
def roleplay_human_memory_state(scene_id: str = "default") -> dict:
    result = roleplay_human_memory_state_payload(scene_id=scene_id)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay.human",
            "surface": "roleplay",
            "event_type": "roleplay.human_memory.state.loaded",
            "payload": {"scene_id": scene_id, "counts": result.get("counts") or {}},
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/human-memory/sync-scene")
def roleplay_human_memory_sync_scene(payload: dict | None = None) -> dict:
    payload = payload or {}
    scene_id = str(payload.get("scene_id") or "default")
    setup = load_scene_setup(scene_id)
    transcript = load_scene_transcript(scene_id)
    result = sync_scene_human_memory(setup, transcript, user_message=str(payload.get("user_message") or ""), runtime_bundle={})
    try:
        get_memory_service().record_event({
            "namespace": "roleplay.human",
            "surface": "roleplay",
            "event_type": "roleplay.human_memory.scene.synced",
            "payload": {"scene_id": scene_id, "link": result.get("link") or {}},
        })
    except Exception:
        pass
    return result

@app.get("/api/roleplay/retrieval/state")
def roleplay_retrieval_state() -> dict:
    retrieval = roleplay_retrieval_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.retrieval.state.loaded",
            "payload": retrieval.get("available_memory_rows", {}),
        })
    except Exception:
        pass
    return retrieval


@app.post("/api/roleplay/retrieval/trace-placeholder")
def roleplay_retrieval_trace_placeholder(payload: dict) -> dict:
    result = create_retrieval_trace_placeholder_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.retrieval.trace.placeholder_created",
            "payload": result.get("trace", {}),
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/retrieval/search-foundation")
def roleplay_retrieval_search_foundation(payload: dict) -> dict:
    result = search_retrieval_foundation_payload(payload or {})
    try:
        search = result.get("search", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.retrieval.foundation.searched",
            "payload": {
                "query": search.get("query"),
                "scope_id": search.get("scope_id"),
                "result_count": search.get("result_count"),
                "trace_id": search.get("trace_id"),
            },
        })
    except Exception:
        pass
    return result




@app.post("/api/roleplay/retrieval/index-roleplay-memory")
def roleplay_retrieval_index_roleplay_memory(payload: dict | None = None) -> dict:
    result = index_roleplay_memory_vectors_payload(payload or {})
    try:
        index = result.get("index", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.retrieval.vector.indexed",
            "payload": {
                "scope_id": index.get("scope_id"),
                "indexed_count": index.get("indexed_count"),
                "skipped_count": index.get("skipped_count"),
                "model_id": index.get("model_id"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/retrieval/search-semantic")
def roleplay_retrieval_search_semantic(payload: dict | None = None) -> dict:
    result = search_retrieval_semantic_payload(payload or {})
    try:
        search = result.get("search", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.retrieval.semantic.searched",
            "payload": {
                "query": search.get("query"),
                "scope_id": search.get("scope_id"),
                "result_count": search.get("result_count"),
                "trace_id": search.get("trace_id"),
                "mode": search.get("mode"),
            },
        })
    except Exception:
        pass
    return result




@app.get("/api/roleplay/contradictions/state")
def roleplay_contradictions_state(status: str | None = "open") -> dict:
    result = contradiction_state_payload(include_reports=True, status=status or "open")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.contradictions.state.loaded",
            "payload": result.get("counts", {}),
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/contradictions/scan")
def roleplay_contradictions_scan(payload: dict | None = None) -> dict:
    result = scan_contradictions_payload(payload or {})
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.contradictions.scanned",
            "payload": {
                "scope_id": result.get("scope_id"),
                "created_or_updated_count": result.get("created_or_updated_count"),
                "open_count": (result.get("contradictions") or {}).get("counts", {}).get("open"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/contradictions/update")
def roleplay_contradictions_update(payload: dict | None = None) -> dict:
    try:
        result = update_contradiction_report_payload(payload or {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Contradiction report not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.contradictions.report.updated",
            "payload": {"report_id": result.get("report_id"), "status": result.get("report_status")},
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/package/state")
def roleplay_package_state() -> dict:
    state_payload = package_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.package.state.loaded",
            "payload": {
                "schema_id": state_payload.get("schema_id"),
                "export_count": len(state_payload.get("exports") or []),
                "import_count": len(state_payload.get("imports") or []),
            },
        })
    except Exception:
        pass
    return state_payload


@app.post("/api/roleplay/package/export")
def roleplay_package_export(payload: dict | None = None) -> dict:
    result = export_roleplay_package_payload(payload or {})
    try:
        package = result.get("package", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.package.exported",
            "payload": {
                "package_id": package.get("package_id"),
                "archive": package.get("archive"),
                "size_bytes": package.get("size_bytes"),
                "include_sqlite": package.get("include_sqlite"),
                "include_vector_store": package.get("include_vector_store"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/package/export/download")
def roleplay_package_export_download(archive: str):
    try:
        path = package_archive_download_path(archive)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/roleplay/package/import-path")
def roleplay_package_import_path(payload: dict | None = None) -> dict:
    try:
        result = import_roleplay_package_payload(payload or {})
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.package.imported",
            "payload": {
                "import_id": result.get("import_id"),
                "archive": result.get("archive"),
                "mode": result.get("mode"),
                "written": result.get("written"),
                "skipped": result.get("skipped"),
                "error_count": len(result.get("errors") or []),
            },
        })
    except Exception:
        pass
    return result



@app.get("/api/roleplay/package-registry/state")
def roleplay_package_registry_state() -> dict:
    result = registry_state_payload()
    try:
        get_memory_service().record_event({"namespace": "roleplay", "surface": "roleplay", "event_type": "roleplay.package_registry.state.loaded", "payload": {"counts": result.get("counts")}})
    except Exception:
        pass
    return result


@app.post("/api/roleplay/package-registry/add")
def roleplay_package_registry_add(payload: dict | None = None) -> dict:
    try:
        result = add_package_to_registry_payload(payload or {})
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        item = result.get("item", {})
        get_memory_service().record_event({"namespace": "roleplay", "surface": "roleplay", "event_type": "roleplay.package_registry.package_added", "payload": {"package_id": item.get("package_id"), "archive": item.get("archive")}})
    except Exception:
        pass
    return result


@app.post("/api/roleplay/package-registry/install")
def roleplay_package_registry_install(payload: dict | None = None) -> dict:
    try:
        result = install_registry_package_payload(payload or {})
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        item = result.get("item", {})
        get_memory_service().record_event({"namespace": "roleplay", "surface": "roleplay", "event_type": "roleplay.package_registry.package_installed", "payload": {"package_id": item.get("package_id"), "status": item.get("status")}})
    except Exception:
        pass
    return result


@app.post("/api/roleplay/package-registry/toggle")
def roleplay_package_registry_toggle(payload: dict | None = None) -> dict:
    try:
        result = toggle_registry_package_payload(payload or {})
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        get_memory_service().record_event({"namespace": "roleplay", "surface": "roleplay", "event_type": "roleplay.package_registry.package_toggled", "payload": {"package_id": result.get("package_id"), "enabled": result.get("enabled")}})
    except Exception:
        pass
    return result


@app.post("/api/roleplay/package-registry/export-current")
def roleplay_package_registry_export_current(payload: dict | None = None) -> dict:
    result = export_package_to_registry_payload(payload or {})
    try:
        item = result.get("registry", {}).get("item", {})
        get_memory_service().record_event({"namespace": "roleplay", "surface": "roleplay", "event_type": "roleplay.package_registry.current_exported", "payload": {"package_id": item.get("package_id"), "archive": item.get("archive")}})
    except Exception:
        pass
    return result




@app.get("/api/roleplay/collaboration/state")
def roleplay_collaboration_state() -> dict:
    result = collaboration_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.collaboration.state.loaded",
            "payload": result.get("counts", {}),
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/collaboration/session/heartbeat")
def roleplay_collaboration_heartbeat(payload: dict | None = None) -> dict:
    return collaboration_heartbeat_payload(payload or {})


@app.post("/api/roleplay/collaboration/lock/acquire")
def roleplay_collaboration_lock_acquire(payload: dict | None = None) -> dict:
    try:
        return acquire_lock_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/roleplay/collaboration/lock/release")
def roleplay_collaboration_lock_release(payload: dict | None = None) -> dict:
    return release_lock_payload(payload or {})


@app.post("/api/roleplay/collaboration/activity/log")
def roleplay_collaboration_activity_log(payload: dict | None = None) -> dict:
    return log_activity_payload(payload or {})


@app.post("/api/roleplay/collaboration/conflict/resolve")
def roleplay_collaboration_conflict_resolve(payload: dict | None = None) -> dict:
    try:
        return resolve_conflict_payload(payload or {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Collaboration conflict not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/roleplay/cloud-sync/state")
def roleplay_cloud_sync_state() -> dict:
    return cloud_sync_state_payload()


@app.post("/api/roleplay/cloud-sync/settings")
def roleplay_cloud_sync_settings(payload: dict | None = None) -> dict:
    return update_cloud_sync_settings_payload(payload or {})


@app.post("/api/roleplay/cloud-sync/snapshot")
def roleplay_cloud_sync_snapshot(payload: dict | None = None) -> dict:
    return create_cloud_snapshot_payload(payload or {})


@app.post("/api/roleplay/cloud-sync/compare")
def roleplay_cloud_sync_compare(payload: dict | None = None) -> dict:
    return compare_cloud_sync_payload(payload or {})


@app.post("/api/roleplay/cloud-sync/push")
def roleplay_cloud_sync_push(payload: dict | None = None) -> dict:
    return push_cloud_sync_payload(payload or {})


@app.post("/api/roleplay/cloud-sync/pull")
def roleplay_cloud_sync_pull(payload: dict | None = None) -> dict:
    return pull_cloud_sync_payload(payload or {})




@app.get("/api/roleplay/provenance-debug/contract")
def roleplay_provenance_debug_contract(write_report: bool = True) -> dict:
    return provenance_debug_contract_payload(write_report=write_report)


@app.get("/api/roleplay/provenance-debug/state")
def roleplay_provenance_debug_state(write_report: bool = True) -> dict:
    return provenance_debug_state_payload(write_report=write_report)


@app.post("/api/roleplay/provenance-debug/ensure-schema")
def roleplay_provenance_debug_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_provenance_debug_schema()


@app.post("/api/roleplay/provenance-debug/dashboard")
def roleplay_provenance_debug_dashboard(payload: dict | None = None) -> dict:
    return provenance_debug_dashboard_payload(payload or {})


@app.post("/api/roleplay/provenance-debug/inspect")
def roleplay_provenance_debug_inspect(payload: dict | None = None) -> dict:
    return provenance_debug_inspect_payload(payload or {})



@app.get("/api/roleplay/large-json-io/contract")
def roleplay_large_json_io_contract(write_report: bool = True) -> dict:
    return large_json_io_contract_payload(write_report=write_report)


@app.get("/api/roleplay/large-json-io/state")
def roleplay_large_json_io_state() -> dict:
    return large_json_io_state_payload()


@app.post("/api/roleplay/large-json-io/ensure-schema")
def roleplay_large_json_io_ensure_schema(payload: dict | None = None) -> dict:
    return ensure_large_json_io_schema()


@app.post("/api/roleplay/large-json-io/validate")
def roleplay_large_json_io_validate(payload: dict | None = None) -> dict:
    return validate_large_json_records_payload(payload or {})


@app.post("/api/roleplay/large-json-io/import")
def roleplay_large_json_io_import(payload: dict | None = None) -> dict:
    return import_large_json_records_payload(payload or {})


@app.post("/api/roleplay/large-json-io/export")
def roleplay_large_json_io_export(payload: dict | None = None) -> dict:
    return export_large_json_sandbox_payload(payload or {})

@app.get("/api/roleplay/project-links")
def roleplay_project_links(project_id: str = "", limit: int = 80, source_type: str = "") -> dict:
    return roleplay_project_links_payload(project_id=project_id, limit=limit, source_type=source_type)


@app.post("/api/roleplay/project-link")
def roleplay_project_link(payload: dict | None = None) -> dict:
    try:
        return create_roleplay_project_link(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/api/roleplay/provenance/state")
def roleplay_provenance_state() -> dict:
    result = provenance_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.provenance.state.loaded",
            "payload": result.get("graph_summary", {}),
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/provenance/graph")
def roleplay_provenance_graph(scope_id: str = "", node_type: str = "", limit: int = 250) -> dict:
    result = provenance_graph_payload(scope_id=scope_id, node_type=node_type, limit=limit)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.provenance.graph.loaded",
            "payload": {"scope_id": scope_id, "node_type": node_type, "counts": result.get("counts", {})},
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/provenance/trace")
def roleplay_provenance_trace(payload: dict | None = None) -> dict:
    data = payload or {}
    result = provenance_trace_payload(
        source_table=str(data.get("source_table") or ""),
        source_id=str(data.get("source_id") or ""),
        node_id=str(data.get("node_id") or ""),
    )
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.provenance.trace.loaded",
            "payload": {"node_id": result.get("node_id"), "status": result.get("status"), "counts": result.get("counts", {})},
        })
    except Exception:
        pass
    return result




@app.get("/api/projects/workspace/qa")
def project_workspace_qa(project_id: str = "", limit: int = 30) -> dict:
    return memory_project_qa_payload(project_id=project_id, limit=limit)


@app.post("/api/projects/workspace/qa/repair")
def project_workspace_qa_repair(payload: dict | None = None) -> dict:
    data = payload or {}
    return memory_project_regression_repair_payload(str(data.get("project_id") or ""))


@app.get("/api/projects/workspace/status")
def project_workspace_status() -> dict:
    return project_workspace_status_payload()


@app.get("/api/projects/workspace/list")
def project_workspace_list() -> dict:
    return {"ok": True, "schema_id": "neo.project_workspace.list.v1", "projects": list_project_workspaces(), "active": active_project_payload()}


@app.get("/api/projects/workspace/active")
def project_workspace_active() -> dict:
    return active_project_payload()


@app.post("/api/projects/workspace/active")
def project_workspace_set_active(payload: dict | None = None) -> dict:
    try:
        return set_active_project(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/save")
def project_workspace_save(payload: dict | None = None) -> dict:
    try:
        result = save_project_workspace(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=250)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/context")
def project_workspace_context(project_id: str = "", limit: int = 30) -> dict:
    return project_workspace_context_payload(project_id, limit=limit)


@app.post("/api/projects/workspace/context")
def project_workspace_add_context(payload: dict | None = None) -> dict:
    try:
        result = add_project_context(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=250)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/link")
def project_workspace_link(payload: dict | None = None) -> dict:
    try:
        result = link_project_resource(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=250)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/index")
def project_workspace_index(payload: dict | None = None) -> dict:
    data = payload or {}
    return get_memory_service().index_source("project_workspace", force=bool(data.get("force", True)), limit=data.get("limit"))


@app.post("/api/projects/workspace/handoff")
def project_workspace_handoff(payload: dict | None = None) -> dict:
    try:
        result = create_cross_surface_handoff(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=500)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.handoff.created",
                "title": result.get("handoff", {}).get("title") or "Project handoff",
                "summary": result.get("handoff", {}).get("content_preview") or "Cross-surface project handoff created.",
                "payload": result.get("handoff", {}),
                "tags": ["project_workspace", "handoff"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/timeline")
def project_workspace_timeline(project_id: str = "", limit: int = 50) -> dict:
    return {"ok": True, "schema_id": "neo.project_workspace.timeline.v1", "project_id": project_id or active_project_payload().get("active_project_id"), "timeline": list_project_timeline(project_id, limit=limit)}


@app.post("/api/projects/workspace/timeline")
def project_workspace_add_timeline(payload: dict | None = None) -> dict:
    try:
        result = add_project_timeline_event(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=500)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/asset-tray")
def project_workspace_asset_tray(project_id: str = "", limit: int = 30) -> dict:
    return project_workspace_asset_tray_payload(project_id, limit=limit)


@app.get("/api/projects/workspace/surface-actions")
def project_workspace_surface_actions(project_id: str = "", limit: int = 50, surface: str = "") -> dict:
    return project_surface_actions_payload(project_id, limit=limit, surface=surface)


@app.post("/api/projects/workspace/surface-action")
def project_workspace_surface_action(payload: dict | None = None) -> dict:
    try:
        result = create_project_surface_action(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.surface_action.created",
                "title": (result.get("action") or {}).get("title") or "Project surface action",
                "summary": (result.get("action") or {}).get("content_preview") or "Cross-surface project action created.",
                "payload": result.get("action", {}),
                "tags": ["project_workspace", "surface_action"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/milestones")
def project_workspace_milestones(project_id: str = "", limit: int = 50) -> dict:
    return {"ok": True, "schema_id": "neo.project_workspace.milestones.v1", "project_id": project_id or active_project_payload().get("active_project_id"), "milestones": list_project_milestones(project_id, limit=limit)}


@app.post("/api/projects/workspace/milestones")
def project_workspace_save_milestone(payload: dict | None = None) -> dict:
    try:
        result = save_project_milestone(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=900)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.milestone.updated",
                "title": result.get("milestone", {}).get("title") or "Project milestone updated",
                "summary": result.get("milestone", {}).get("summary") or "Project milestone updated.",
                "payload": result.get("milestone", {}),
                "tags": ["project_workspace", "milestone"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/deliverables")
def project_workspace_deliverables(project_id: str = "", limit: int = 100) -> dict:
    return {"ok": True, "schema_id": "neo.project_workspace.deliverables.v1", "project_id": project_id or active_project_payload().get("active_project_id"), "deliverables": list_project_deliverables(project_id, limit=limit)}


@app.post("/api/projects/workspace/deliverables")
def project_workspace_save_deliverable(payload: dict | None = None) -> dict:
    try:
        result = save_project_deliverable(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=900)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.deliverable.updated",
                "title": result.get("deliverable", {}).get("title") or "Project deliverable updated",
                "summary": result.get("deliverable", {}).get("summary") or "Project deliverable updated.",
                "payload": result.get("deliverable", {}),
                "tags": ["project_workspace", "deliverable"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/deliverable-tracker")
def project_workspace_deliverable_tracker(project_id: str = "", limit: int = 100) -> dict:
    return project_deliverable_tracker_payload(project_id, limit=limit)


@app.get("/api/projects/workspace/activity-intelligence")
def project_workspace_activity_intelligence(project_id: str = "", limit: int = 100) -> dict:
    return project_activity_intelligence_payload(project_id, limit=limit)


@app.get("/api/projects/workspace/smart-brief")
def project_workspace_smart_brief(project_id: str = "", audience: str = "internal", detail: str = "standard", limit: int = 120) -> dict:
    return project_smart_brief_payload(project_id, audience=audience, detail=detail, limit=limit)


@app.get("/api/projects/workspace/review-items")
def project_workspace_review_items(project_id: str = "", status: str = "", review_scope: str = "", limit: int = 100) -> dict:
    return {"ok": True, "schema_id": "neo.project_workspace.review_items.v1", "project_id": project_id or active_project_payload().get("active_project_id"), "review_items": list_project_review_items(project_id, limit=limit, status=status, review_scope=review_scope)}


@app.post("/api/projects/workspace/review-items")
def project_workspace_save_review_item(payload: dict | None = None) -> dict:
    try:
        result = save_project_review_item(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=1400)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.review_item.updated",
                "title": result.get("review_item", {}).get("title") or "Project review item updated",
                "summary": result.get("review_item", {}).get("summary") or "Project review item updated.",
                "payload": result.get("review_item", {}),
                "tags": ["project_workspace", "review_queue", "approval"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/review-queue")
def project_workspace_review_queue(project_id: str = "", limit: int = 100, include_auto: bool = True) -> dict:
    return project_review_queue_payload(project_id, limit=limit, include_auto=include_auto)


@app.post("/api/projects/workspace/review-decision")
def project_workspace_review_decision(payload: dict | None = None) -> dict:
    try:
        result = apply_project_review_decision(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=1400)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.review_decision.applied",
                "title": "Project review decision applied",
                "summary": result.get("review_item", {}).get("decision_notes") or result.get("review_item", {}).get("status") or "Review decision applied.",
                "payload": result,
                "tags": ["project_workspace", "review_decision", "approval"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/approval-workflow")
def project_workspace_approval_workflow(project_id: str = "", limit: int = 100) -> dict:
    return project_approval_workflow_payload(project_id, limit=limit)


@app.get("/api/projects/workspace/package-builder")
def project_workspace_package_builder(project_id: str = "", limit: int = 80) -> dict:
    return project_package_builder_payload(project_id, limit=limit)


@app.post("/api/projects/workspace/package/build")
def project_workspace_build_package(payload: dict | None = None) -> dict:
    try:
        result = build_project_package(payload or {})
        try:
            # Phase 44 hardening: keep the route responsive by recording a compact
            # audit event instead of re-indexing the full Project Workspace payload.
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.package.built",
                "title": result.get("package", {}).get("title") or "Project package built",
                "summary": f"Project package built at {result.get('package_dir') or ''}",
                "payload": {
                    "project_id": result.get("project_id"),
                    "package_id": result.get("package_id"),
                    "package_dir": result.get("package_dir"),
                    "zip_path": result.get("zip_path"),
                    "summary": (result.get("package") or {}).get("summary") or {},
                },
                "tags": ["project_workspace", "package_builder", "delivery_package"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/delivery-dashboard")
def project_workspace_delivery_dashboard(project_id: str = "", audience: str = "client", limit: int = 120) -> dict:
    return project_delivery_dashboard_payload(project_id, audience=audience, limit=limit)


@app.get("/api/projects/workspace/client-status")
def project_workspace_client_status(project_id: str = "", audience: str = "client", detail: str = "standard", limit: int = 120) -> dict:
    return project_client_status_view_payload(project_id, audience=audience, detail=detail, limit=limit)


@app.post("/api/projects/workspace/status-report/export")
def project_workspace_export_status_report(payload: dict | None = None) -> dict:
    try:
        result = export_project_status_report(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=1200)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.status_report.exported",
                "title": "Project status report exported",
                "summary": "Client status view exported from Project Delivery Dashboard.",
                "payload": result,
                "tags": ["project_workspace", "status_report", "delivery_dashboard"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/brief/save")
def project_workspace_save_brief(payload: dict | None = None) -> dict:
    try:
        result = save_project_brief(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
            get_memory_service().record_event({
                "namespace": "project_workspace",
                "surface": "project_workspace",
                "event_type": "project.workspace.brief.saved",
                "title": result.get("brief", {}).get("title") or "Project brief saved",
                "summary": "Project smart brief saved into the active project.",
                "payload": result.get("brief", {}),
                "tags": ["project_workspace", "project_brief"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/workspace/briefs")
def project_workspace_briefs(project_id: str = "", limit: int = 30) -> dict:
    return project_briefs_payload(project_id, limit=limit)


@app.get("/api/projects/workspace/briefs/{brief_id}")
def project_workspace_brief_detail(brief_id: str, project_id: str = "") -> dict:
    try:
        return get_project_brief(project_id, brief_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/projects/workspace/briefs/{brief_id}/versions")
def project_workspace_brief_versions(brief_id: str, project_id: str = "", limit: int = 50) -> dict:
    return project_brief_versions_payload(project_id, brief_id, limit=limit)


@app.post("/api/projects/workspace/briefs/compare")
def project_workspace_brief_compare(payload: dict | None = None) -> dict:
    try:
        return compare_project_briefs(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/briefs/restore")
def project_workspace_brief_restore(payload: dict | None = None) -> dict:
    try:
        result = restore_project_brief_version(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/briefs/finalize")
def project_workspace_brief_finalize(payload: dict | None = None) -> dict:
    try:
        result = mark_project_brief_final(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/workspace/brief/export")
def project_workspace_export_brief(payload: dict | None = None) -> dict:
    try:
        result = export_project_brief(payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/assistant/bootstrap")
def assistant_bootstrap() -> dict:
    return assistant_bootstrap_payload()


@app.get("/api/assistant/search")
def assistant_search(query: str = "", project_id: str = "") -> dict:
    return assistant_search_payload(query=query, project_id=project_id)


@app.post("/api/assistant/profile-save")
def assistant_profile_save(payload: dict) -> dict:
    try:
        return save_assistant_profile(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-create")
def assistant_project_create(payload: dict) -> dict:
    try:
        return assistant_create_project_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-save")
def assistant_project_save(payload: dict) -> dict:
    try:
        return assistant_save_project_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-rename")
def assistant_project_rename(payload: dict) -> dict:
    try:
        return assistant_rename_project_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-delete")
def assistant_project_delete(payload: dict) -> dict:
    try:
        return assistant_delete_project_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/session-create")
def assistant_session_create(payload: dict | None = None) -> dict:
    try:
        return assistant_create_session_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/session-load")
def assistant_session_load(session_id: str) -> dict:
    try:
        return assistant_load_session_payload(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/session-save")
def assistant_session_save(payload: dict) -> dict:
    try:
        return assistant_save_session_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/session-rename")
def assistant_session_rename(payload: dict) -> dict:
    try:
        return assistant_rename_session_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/session-delete")
def assistant_session_delete(payload: dict) -> dict:
    try:
        return assistant_delete_session_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/manual-memory-capture")
def assistant_manual_memory_capture(payload: dict) -> dict:
    try:
        return assistant_manual_memory_capture_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/context-pack-preview")
def assistant_context_pack_preview(session_id: str = "", project_id: str = "", message: str = "", retrieval_profile: str = "smart", surface: str = "") -> dict:
    return assistant_context_pack_preview_payload(session_id=session_id, project_id=project_id, message=message, retrieval_profile=retrieval_profile, surface=surface)


@app.post("/api/assistant/source-grounded-answer")
def assistant_source_grounded_answer(payload: dict | None = None) -> dict:
    return assistant_source_grounded_answer_payload(payload or {})


@app.get("/api/assistant/action-review/status")
def assistant_action_review_status() -> dict:
    return action_review_status_payload()


@app.post("/api/assistant/action-review/plan")
def assistant_action_review_plan(payload: dict | None = None) -> dict:
    return plan_assistant_action_review(payload or {})


@app.post("/api/assistant/action-review/run")
def assistant_action_review_run(payload: dict | None = None) -> dict:
    return run_assistant_action_review(payload or {})



@app.get("/api/assistant/project-manager")
def assistant_project_manager(project_id: str = "", question: str = "", limit: int = 80) -> dict:
    return assistant_project_manager_payload(project_id=project_id, question=question, limit=limit)


@app.post("/api/assistant/project-manager/query")
def assistant_project_manager_query_route(payload: dict | None = None) -> dict:
    try:
        return assistant_project_manager_query(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/assistant/attachments/upload")
async def assistant_attachment_upload(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    project_id: str = Form(""),
    kind: str = Form("auto"),
) -> dict:
    try:
        attachment = await assistant_save_attachment_upload(
            file,
            session_id=session_id or "",
            project_id=project_id or "",
            kind=kind or "auto",
        )
    except ImageUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "attachment": attachment, "attachments": assistant_list_attachment_records(session_id=session_id or "", project_id=project_id or "", limit=50)}


@app.get("/api/assistant/attachments")
def assistant_attachments_list(session_id: str = "", project_id: str = "", limit: int = 50) -> dict:
    return {"ok": True, "attachments": assistant_list_attachment_records(session_id=session_id or "", project_id=project_id or "", limit=limit)}


@app.get("/api/assistant/attachments/{attachment_id}")
def assistant_attachment_file(attachment_id: str) -> FileResponse:
    record = assistant_get_attachment_record(attachment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Assistant attachment not found")
    path = assistant_attachment_path(record)
    if path is None or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Assistant attachment file not found")
    return FileResponse(path, media_type=str(record.get("mime_type") or "application/octet-stream"), filename=str(record.get("filename") or path.name))


@app.delete("/api/assistant/attachments/{attachment_id}")
def assistant_attachment_delete(attachment_id: str) -> dict:
    result = assistant_delete_attachment_record(attachment_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message") or "Assistant attachment not found")
    return result


@app.post("/api/assistant/chat-run")
def assistant_chat_run(payload: dict) -> dict:
    try:
        payload = payload or {}
        has_text = bool(str(payload.get("message") or payload.get("text") or "").strip())
        raw_attachments = payload.get("attachments") or payload.get("attachment_ids") or []
        has_attachments = bool(raw_attachments) if isinstance(raw_attachments, (list, tuple, str)) else False
        if not has_text and not has_attachments:
            return run_assistant_chat_turn(payload)
        live_profile = _require_backend_connected_for_task(str(payload.get("profile_id") or payload.get("backend_profile_id") or ""), surface="assistant", operation="Assistant chat")
        profile_id = str((live_profile or {}).get("profile_id") or payload.get("profile_id") or payload.get("backend_profile_id") or "").strip()
        return run_assistant_chat_turn({
            **payload,
            "profile_id": str(payload.get("profile_id") or profile_id),
            "backend_profile_id": profile_id,
            "_neo_live_backend_profile": live_profile,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))




@app.post("/api/assistant/chat-run-stream")
def assistant_chat_run_stream(payload: dict):
    payload = payload or {}

    def event_stream():
        try:
            has_text = bool(str(payload.get("message") or payload.get("text") or "").strip())
            raw_attachments = payload.get("attachments") or payload.get("attachment_ids") or []
            has_attachments = bool(raw_attachments) if isinstance(raw_attachments, (list, tuple, str)) else False
            if has_text or has_attachments:
                live_profile = _require_backend_connected_for_task(str(payload.get("profile_id") or payload.get("backend_profile_id") or ""), surface="assistant", operation="Assistant chat stream")
                profile_id = str((live_profile or {}).get("profile_id") or payload.get("profile_id") or payload.get("backend_profile_id") or "").strip()
                stream_payload = {
                    **payload,
                    "profile_id": str(payload.get("profile_id") or profile_id),
                    "backend_profile_id": profile_id,
                    "_neo_live_backend_profile": live_profile,
                }
            else:
                stream_payload = payload
            for event in stream_assistant_chat_turn_event_dicts(stream_payload):
                event_type = str(event.get("type") or "message")
                yield f"event: {event_type}\n"
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            error_event = {
                "type": "error",
                "schema_id": "neo.assistant.chat_stream.error.v1",
                "ok": False,
                "status": "server_stream_exception",
                "message": str(exc),
            }
            yield "event: error\n"
            yield "data: " + json.dumps(error_event, ensure_ascii=False) + "\n\n"
            yield "event: done\n"
            yield "data: " + json.dumps({**error_event, "type": "done"}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/assistant/session-clear")
def assistant_session_clear(payload: dict) -> dict:
    try:
        return assistant_clear_session_messages_payload(payload or {})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/brain/status")
def assistant_brain_status() -> dict:
    return assistant_brain_status_payload()


@app.get("/api/assistant/brain/workspaces")
def assistant_brain_workspaces() -> dict:
    return assistant_brain_workspaces_payload()


@app.post("/api/assistant/brain/dashboard")
def assistant_brain_dashboard(payload: dict | None = None) -> dict:
    return assistant_brain_dashboard_payload(payload or {})


@app.post("/api/assistant/brain/context")
def assistant_brain_context(payload: dict | None = None) -> dict:
    return assistant_brain_context_payload(payload or {})


@app.post("/api/assistant/brain/activate")
def assistant_brain_activate(payload: dict | None = None) -> dict:
    return assistant_brain_activate_payload(payload or {})


@app.get("/api/assistant/control-center/status")
def assistant_control_center_status() -> dict:
    return assistant_control_status_payload()


@app.post("/api/assistant/control-center/plan")
def assistant_control_center_plan(payload: dict | None = None) -> dict:
    return assistant_control_plan_payload(payload or {})


@app.post("/api/assistant/control-center/context")
def assistant_control_center_context(payload: dict | None = None) -> dict:
    return assistant_control_context_payload(payload or {})


@app.get("/api/assistant/control-center/traces")
def assistant_control_center_traces(limit: int = 25, project_id: str | None = None, surface: str | None = None) -> dict:
    return assistant_control_traces_payload(limit=limit, project_id=project_id, surface=surface)




@app.get("/api/roleplay/control-center/status")
def roleplay_control_center_status() -> dict:
    return roleplay_control_status_payload()


@app.post("/api/roleplay/control-center/plan")
def roleplay_control_center_plan(payload: dict | None = None) -> dict:
    return roleplay_control_plan_payload(payload or {})


@app.post("/api/roleplay/control-center/context")
def roleplay_control_center_context(payload: dict | None = None) -> dict:
    return roleplay_control_context_payload(payload or {})


@app.get("/api/roleplay/control-center/traces")
def roleplay_control_center_traces(limit: int = 25, scene_id: str | None = None, scope_id: str | None = None) -> dict:
    return roleplay_control_traces_payload(limit=limit, scene_id=scene_id, scope_id=scope_id)


@app.get("/api/roleplay/scene-director/status")
def roleplay_scene_director_status() -> dict:
    return roleplay_scene_director_status_payload()


@app.post("/api/roleplay/scene-director/preflight")
def roleplay_scene_director_preflight(payload: dict | None = None) -> dict:
    return roleplay_scene_director_preflight_payload(payload or {})


@app.post("/api/roleplay/scene-director/validate")
def roleplay_scene_director_validate(payload: dict | None = None) -> dict:
    return roleplay_scene_director_validate_payload(payload or {})


@app.get("/api/roleplay/scene-director/traces")
def roleplay_scene_director_traces(limit: int = 25) -> dict:
    return roleplay_scene_director_trace_payload(limit=limit)

@app.get("/api/prompt-contracts/status")
def prompt_contracts_status() -> dict:
    return prompt_contract_status_payload()


@app.get("/api/prompt-contracts")
def prompt_contracts_list(controller: str | None = None) -> dict:
    return prompt_contract_list_payload(controller=controller)


@app.get("/api/prompt-contracts/{contract_id}")
def prompt_contracts_detail(contract_id: str) -> dict:
    return prompt_contract_detail_payload(contract_id)


@app.post("/api/assistant/context-save")
def assistant_context_save(payload: dict) -> dict:
    try:
        return assistant_save_context_item_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/context-items")
def assistant_context_items(project_id: str = "", session_id: str = "", surface: str = "") -> dict:
    return assistant_context_items_payload(project_id=project_id, session_id=session_id, surface=surface)


@app.get("/api/assistant/surface-context/{surface_id}")
def assistant_surface_project_context_route(surface_id: str, project_id: str = "", session_id: str = "") -> dict:
    return surface_project_context_payload(surface=surface_id, project_id=project_id, session_id=session_id)


@app.post("/api/assistant/surface-context/{surface_id}")
def assistant_surface_project_context_snapshot_route(surface_id: str, payload: dict | None = None) -> dict:
    data = payload or {}
    return surface_project_context_payload(surface=surface_id, project_id=str(data.get("project_id") or ""), session_id=str(data.get("session_id") or ""), payload=data)


@app.post("/api/assistant/surface-context")
def assistant_surface_context(payload: dict) -> dict:
    try:
        return assistant_save_surface_context_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/surface-context")
def assistant_surface_context_list(project_id: str = "", surface: str = "", limit: int = 50) -> dict:
    return assistant_list_surface_context_payload(project_id=project_id, surface=surface, limit=limit)


@app.get("/api/assistant/guides")
def assistant_guides(query: str = "", project_id: str = "general", surface: str = "", limit: int = 12) -> dict:
    return assistant_search_guides(query=query, project_id=project_id, surface=surface, limit=limit)


@app.get("/api/assistant/project-brain/status")
def assistant_project_brain_status(project_id: str = "general", surface: str = "") -> dict:
    return assistant_project_brain_status_payload(project_id=project_id, surface=surface)


@app.post("/api/assistant/project-capture")
def assistant_project_capture(payload: dict | None = None) -> dict:
    try:
        return assistant_capture_project_state_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-index")
def assistant_project_index(payload: dict | None = None) -> dict:
    try:
        return assistant_index_project_data_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-brain-rebuild")
def assistant_project_brain_rebuild(payload: dict | None = None) -> dict:
    try:
        return assistant_rebuild_project_brain_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/project-files/upload")
async def assistant_project_file_upload(
    file: UploadFile = File(...),
    project_id: str = Form("general"),
    surface: str = Form("assistant"),
    session_id: str = Form(""),
) -> dict:
    try:
        return await assistant_save_project_file_upload(file, project_id=project_id or "general", surface=surface or "assistant", session_id=session_id or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/assistant/tool-catalog")
def assistant_tool_catalog() -> dict:
    return assistant_tool_catalog_payload()


@app.post("/api/assistant/tool-preview")
def assistant_tool_preview(payload: dict) -> dict:
    try:
        return assistant_tool_preview_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/assistant/tool-execute")
def assistant_tool_execute(payload: dict) -> dict:
    try:
        return assistant_tool_execute_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/providers")
def providers() -> dict:
    return get_provider_payload()


@app.get("/api/providers/surfaces/{surface_id}")
def surface_providers(surface_id: str) -> dict:
    return get_surface_provider_payload(surface_id)


@app.get("/api/providers/{provider_id}")
def provider(provider_id: str) -> dict:
    match = get_provider(provider_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    return {
        "status": match.status(),
        "capabilities": match.discover_capabilities(),
        "feature_capabilities": match.feature_capability_payload(),
        "backend_capabilities": match.discover_backend_capabilities(),
        "models": match.discover_models(),
    }


@app.get("/api/providers/{provider_id}/backend-capabilities")
def provider_backend_capabilities(provider_id: str) -> dict:
    match = get_provider(provider_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    return get_provider_backend_capabilities(provider_id)


@app.get("/api/providers/{provider_id}/feature-capabilities")
def provider_feature_capabilities(provider_id: str) -> dict:
    match = get_provider(provider_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    return {"provider_id": provider_id, "capabilities": match.feature_capability_payload()}


@app.post("/api/providers/validate-job")
def provider_validate_job(job_payload: dict) -> dict:
    return validate_job_payload(job_payload)


@app.post("/api/providers/compile-job")
def provider_compile_job(job_payload: dict) -> dict:
    return compile_job_payload(job_payload)


@app.post("/api/providers/run-job")
def provider_run_job(job_payload: dict) -> dict:
    return run_job_payload(job_payload)


@app.get("/api/providers/{provider_id}/jobs/{job_id}")
def provider_poll_job(provider_id: str, job_id: str) -> dict:
    return poll_job_payload(provider_id, job_id)


@app.get("/api/providers/{provider_id}/jobs/{job_id}/outputs")
def provider_fetch_outputs(provider_id: str, job_id: str) -> dict:
    return fetch_outputs_payload(provider_id, job_id)


@app.get("/api/runtime/jobs")
def runtime_generation_jobs(surface: str | None = None, limit: int = 50) -> dict:
    """Return the central generation job registry index for diagnostics/recovery."""
    return get_generation_job_registry(ROOT_DIR).list_recent(surface=surface, limit=limit)


@app.get("/api/runtime/jobs/{job_id}")
def runtime_generation_job(job_id: str, surface: str | None = None) -> dict:
    """Return one persisted generation job record without relying on provider memory."""
    registry = get_generation_job_registry(ROOT_DIR)
    record = registry.get(job_id, surface=surface)
    if not record:
        raise HTTPException(status_code=404, detail=f"Runtime generation job not found: {job_id}")
    return record



@app.get("/api/backend-providers")
def backend_provider_options(surface: str | None = None) -> dict:
    return list_backend_provider_options(surface)


@app.get("/api/backend-profiles")
def backend_profiles(surface: str | None = None) -> dict:
    payload = get_backend_profile_payload()
    if surface:
        return {
            "profile_registry_version": payload.get("profile_registry_version"),
            "defaults": payload.get("defaults", {}),
            "profiles": list_backend_profiles(surface),
        }
    return payload


@app.post("/api/backend-profiles/create")
def backend_profile_create(updates: dict) -> dict:
    return create_backend_profile(updates)


@app.get("/api/backend-profiles/selection")
def backend_profile_selection_get() -> dict:
    return get_backend_profile_selection_payload()


@app.post("/api/backend-profiles/selection")
def backend_profile_selection_post(payload: dict) -> dict:
    return save_backend_profile_selection(payload)


@app.get("/api/prompt-captioning/support-matrix")
def prompt_captioning_support_matrix() -> dict:
    return get_prompt_captioning_support_matrix(get_backend_profile_payload())


@app.get("/api/prompt-captioning/backend-status")
def prompt_captioning_backend_status() -> dict:
    return prompt_captioning_backend_execution_status()


@app.post("/api/prompt-captioning/validate-route")
def prompt_captioning_validate_route(payload: dict) -> dict:
    return validate_prompt_captioning_route_payload(payload, get_backend_profile_payload())


@app.post("/api/prompt-captioning/validation-status")
def prompt_captioning_route_validation_status(payload: dict) -> dict:
    return prompt_captioning_validation_status(payload, get_backend_profile_payload())


@app.post("/api/prompt-captioning/normalize-payload")
def prompt_captioning_normalize_payload(payload: dict) -> dict:
    return normalize_prompt_captioning_payload(payload)


@app.post("/api/prompt-captioning/prompt/run")
def prompt_captioning_prompt_run(payload: dict) -> dict:
    metadata = (payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {}
    _require_backend_connected_for_task(str(metadata.get("backend_profile_id") or (payload or {}).get("profile_id") or (payload or {}).get("backend_profile_id") or ""), surface="prompt_captioning", operation="Prompt & Captioning prompt generation")
    return run_prompt_captioning_prompt_tool(payload)


@app.get("/api/prompt-captioning/prompt-records")
def prompt_captioning_prompt_record_list() -> dict:
    return prompt_captioning_prompt_records()


@app.post("/api/prompt-captioning/prompt/save")
def prompt_captioning_prompt_save(payload: dict) -> dict:
    return save_prompt_captioning_prompt(payload)


@app.get("/api/prompt-captioning/prompt-history")
def prompt_captioning_prompt_history_list(limit: int = 25) -> dict:
    return prompt_captioning_prompt_history(limit)


@app.get("/api/prompt-captioning/prompt-presets")
def prompt_captioning_prompt_preset_list(query: str = "", category: str = "") -> dict:
    return prompt_captioning_prompt_presets(query, category)


@app.post("/api/prompt-captioning/prompt-preset/save")
def prompt_captioning_prompt_preset_save(payload: dict) -> dict:
    return prompt_captioning_save_preset(payload)


@app.post("/api/prompt-captioning/prompt-preset/delete")
def prompt_captioning_prompt_preset_delete(payload: dict) -> dict:
    return prompt_captioning_delete_preset(str(payload.get("preset_id") or ""))


@app.post("/api/prompt-captioning/prompt-preset/duplicate")
def prompt_captioning_prompt_preset_duplicate(payload: dict) -> dict:
    return prompt_captioning_duplicate_preset(str(payload.get("preset_id") or ""))


@app.post("/api/prompt-captioning/prompt-preset/favorite")
def prompt_captioning_prompt_preset_favorite(payload: dict) -> dict:
    return prompt_captioning_toggle_preset_favorite(str(payload.get("preset_id") or ""))


@app.get("/api/prompt-captioning/characters")
def prompt_captioning_character_list(query: str = "") -> dict:
    return prompt_captioning_character_records(query)


@app.post("/api/prompt-captioning/character/save")
def prompt_captioning_character_save(payload: dict) -> dict:
    # Wave 2 assist-tools ownership: Character Builder now saves rich prompt fragments
    # under Prompt & Captioning assist storage. The legacy /characters list remains
    # available separately for older Prompt Studio compatibility.
    return prompt_assist_character_save_payload(payload)


@app.get("/api/prompt-captioning/assist/bootstrap")
def prompt_captioning_assist_bootstrap() -> dict:
    return prompt_assist_bootstrap_payload()


@app.post("/api/prompt-captioning/tag-assist/generate")
def prompt_captioning_tag_assist_generate(payload: dict) -> dict:
    return prompt_assist_tag_generate_payload(payload)


@app.post("/api/prompt-captioning/tag-assist/save")
def prompt_captioning_tag_assist_save(payload: dict) -> dict:
    return prompt_assist_tag_save_payload(payload)


@app.get("/api/prompt-captioning/tag-assist/list")
def prompt_captioning_tag_assist_list(query: str = "", category: str = "") -> dict:
    return prompt_assist_tag_list_payload(query, category)


@app.get("/api/prompt-captioning/character/list")
def prompt_captioning_assist_character_list(query: str = "", category: str = "") -> dict:
    return prompt_assist_character_list_payload(query, category)


@app.post("/api/prompt-captioning/character/build-prompt")
def prompt_captioning_assist_character_build_prompt(payload: dict) -> dict:
    return prompt_assist_character_build_prompt_payload(payload)




@app.post("/api/prompt-captioning/keyword-browser-v2/list")
def prompt_captioning_keyword_browser_v2_list(payload: dict) -> dict:
    return keyword_browser_v2_payload(payload or {})


@app.post("/api/prompt-captioning/keyword-browser-v2/manager")
def prompt_captioning_keyword_browser_v2_manager(payload: dict) -> dict:
    return keyword_browser_v2_manager_payload(payload or {})


@app.post("/api/prompt-captioning/keyword-browser-v2/manager/save")
def prompt_captioning_keyword_browser_v2_manager_save(payload: dict) -> dict:
    try:
        return keyword_browser_v2_manager_save_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prompt-captioning/keyword-browser-v2/manager/append")
def prompt_captioning_keyword_browser_v2_manager_append(payload: dict) -> dict:
    try:
        return keyword_browser_v2_manager_append_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prompt-captioning/keyword-browser-v2/manager/create")
def prompt_captioning_keyword_browser_v2_manager_create(payload: dict) -> dict:
    try:
        return keyword_browser_v2_manager_create_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prompt-captioning/keyword-browser-v2/manager/upload-text")
def prompt_captioning_keyword_browser_v2_manager_upload_text(payload: dict) -> dict:
    try:
        return keyword_browser_v2_manager_upload_text_payload(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prompt-captioning/character-keyword-browser-v2/list")
def prompt_captioning_character_keyword_browser_v2_list(payload: dict) -> dict:
    return character_keyword_browser_v2_payload(payload or {})

@app.get("/api/prompt-captioning/keyword/list")
def prompt_captioning_keyword_list(query: str = "", category: str = "", subcategory: str = "", include_builtin: bool = True, library_path: str = "") -> dict:
    return prompt_assist_keyword_list_payload(query, category, subcategory, include_builtin, library_path)


@app.post("/api/prompt-captioning/keyword/list")
def prompt_captioning_keyword_list_post(payload: dict) -> dict:
    """List Keyword Browser records using a JSON body.

    The GET route is kept for compatibility, but Windows drive-root paths can
    be awkward in query strings during local debugging. The UI uses this POST
    route so explicit library paths are passed
    unchanged and can be reported in diagnostics.
    """
    payload = payload or {}
    return prompt_assist_keyword_list_payload(
        str(payload.get("query") or ""),
        str(payload.get("category") or ""),
        str(payload.get("subcategory") or ""),
        bool(payload.get("include_builtin", True)),
        str(payload.get("library_path") or ""),
    )


@app.get("/api/prompt-captioning/keyword/record")
def prompt_captioning_keyword_record(keyword_id: str = "", tid: str = "") -> dict:
    return prompt_assist_keyword_record_payload(keyword_id or tid)


@app.get("/api/prompt-captioning/keyword/insert-text")
def prompt_captioning_keyword_insert_text(keyword_id: str = "", tid: str = "", include_desc: bool = True) -> dict:
    return prompt_assist_keyword_insert_text_payload(keyword_id or tid, include_desc)


@app.post("/api/prompt-captioning/keyword/save")
def prompt_captioning_keyword_save(payload: dict) -> dict:
    return prompt_assist_keyword_save_payload(payload)


@app.get("/api/prompt-captioning/caption-browser/list")
def prompt_captioning_caption_browser_list(query: str = "", category: str = "", include_core: bool = True, sort_by: str = "newest") -> dict:
    return prompt_assist_caption_browser_list_payload(query, category, include_core, sort_by)


@app.post("/api/prompt-captioning/caption-browser/save")
def prompt_captioning_caption_browser_save(payload: dict) -> dict:
    return prompt_assist_caption_browser_save_payload(payload)


@app.post("/api/prompt-captioning/caption-browser/send-to-prompt")
def prompt_captioning_caption_browser_send_to_prompt(payload: dict) -> dict:
    return prompt_assist_caption_browser_send_to_prompt_payload(payload)


@app.post("/api/prompt-captioning/prompt/delete")
def prompt_captioning_prompt_delete(payload: dict) -> dict:
    return prompt_captioning_delete_saved_prompt(str(payload.get("prompt_id") or ""))


@app.post("/api/prompt-captioning/prompt/duplicate")
def prompt_captioning_prompt_duplicate(payload: dict) -> dict:
    return prompt_captioning_duplicate_saved_prompt(str(payload.get("prompt_id") or ""))




@app.post("/api/prompt-captioning/caption/upload-image")
async def prompt_captioning_caption_upload_image(file: UploadFile = File(...)) -> dict:
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Caption image upload requires a file.")
    staged_path: Path | None = None
    try:
        staged = await validate_and_stage_caption_image_upload(
            file,
            target_dir=CAPTION_ASSETS_DIR / "incoming",
            prefix="caption_upload",
            default_filename="caption_image.png",
        )
        staged_path = staged.path
    except CaptionUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    result = save_caption_asset(str(staged_path), staged.original_filename)
    try:
        staged_path.unlink(missing_ok=True)
    except Exception:
        pass
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=(result.get("errors") or ["Caption image upload failed."])[0])
    from neo_app.prompt_captioning.execution import validate_image_asset
    asset_check = validate_image_asset(str(result.get("path") or ""))
    if not asset_check.get("ok"):
        try:
            Path(str(result.get("path") or "")).unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=asset_check.get("error") or "Uploaded file is not a valid image.")
    result["asset_validation"] = asset_check
    result["upload_validation"] = {
        "schema": CAPTION_UPLOAD_VALIDATION_SCHEMA,
        "size_bytes": staged.size_bytes,
        "detected_type": staged.detected_type,
        "original_filename": staged.original_filename,
        "allowed_types": ["png", "jpeg", "webp", "bmp"],
        "max_bytes": 50 * 1024 * 1024,
    }
    result["detected_type"] = staged.detected_type
    result["size_bytes"] = int(result.get("size_bytes") or staged.size_bytes)
    return result


@app.get("/api/prompt-captioning/caption/asset/{filename}")
def prompt_captioning_caption_asset(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    candidate_roots = [CAPTION_ASSETS_DIR, CAPTION_SINGLE_IMAGES_DIR, CAPTION_BATCH_IMAGES_DIR]
    for root in candidate_roots:
        path = root / safe_name
        if path.exists() and path.is_file():
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="Caption asset not found")


@app.post("/api/prompt-captioning/caption/run")
def prompt_captioning_caption_run(payload: dict) -> dict:
    metadata = (payload or {}).get("metadata") if isinstance((payload or {}).get("metadata"), dict) else {}
    _require_backend_connected_for_task(str(metadata.get("backend_profile_id") or (payload or {}).get("profile_id") or (payload or {}).get("backend_profile_id") or ""), surface="prompt_captioning", operation="Prompt & Captioning caption generation")
    return run_prompt_captioning_caption_tool(payload)


@app.get("/api/prompt-captioning/caption-records")
def prompt_captioning_caption_record_list(query: str = "", category: str = "") -> dict:
    return prompt_captioning_caption_records(query, category)


@app.post("/api/prompt-captioning/caption/save")
def prompt_captioning_caption_save(payload: dict) -> dict:
    return save_prompt_captioning_caption(payload)


@app.get("/api/prompt-captioning/categories")
def prompt_captioning_category_list() -> dict:
    return prompt_captioning_categories()


@app.post("/api/prompt-captioning/category/save")
def prompt_captioning_category_save(payload: dict) -> dict:
    return prompt_captioning_save_category(payload)


@app.get("/api/prompt-captioning/caption-history")
def prompt_captioning_caption_history_list(limit: int = 25) -> dict:
    return prompt_captioning_caption_history(limit)


@app.get("/api/prompt-captioning/caption-presets")
def prompt_captioning_caption_preset_list(query: str = "", category: str = "") -> dict:
    return prompt_captioning_caption_presets(query, category)


@app.post("/api/prompt-captioning/caption-preset/save")
def prompt_captioning_caption_preset_save(payload: dict) -> dict:
    return prompt_captioning_save_caption_preset(payload)


@app.post("/api/prompt-captioning/caption-preset/delete")
def prompt_captioning_caption_preset_delete(payload: dict) -> dict:
    return prompt_captioning_delete_caption_preset(str(payload.get("preset_id") or ""))


@app.post("/api/prompt-captioning/caption-preset/duplicate")
def prompt_captioning_caption_preset_duplicate(payload: dict) -> dict:
    return prompt_captioning_duplicate_caption_preset(str(payload.get("preset_id") or ""))


@app.post("/api/prompt-captioning/caption-preset/favorite")
def prompt_captioning_caption_preset_favorite(payload: dict) -> dict:
    return prompt_captioning_toggle_caption_preset_favorite(str(payload.get("preset_id") or ""))


@app.get("/api/prompt-captioning/caption-components")
def prompt_captioning_caption_component_list(query: str = "", component_type: str = "") -> dict:
    return prompt_captioning_caption_components(query, component_type)


@app.post("/api/prompt-captioning/caption-component/save")
def prompt_captioning_caption_component_save(payload: dict) -> dict:
    return prompt_captioning_save_caption_component(payload)


@app.post("/api/prompt-captioning/caption-component/delete")
def prompt_captioning_caption_component_delete(payload: dict) -> dict:
    return prompt_captioning_delete_caption_component(str(payload.get("component_id") or ""))


@app.post("/api/prompt-captioning/caption-component/duplicate")
def prompt_captioning_caption_component_duplicate(payload: dict) -> dict:
    return prompt_captioning_duplicate_caption_component(str(payload.get("component_id") or ""))


@app.post("/api/prompt-captioning/caption/delete")
def prompt_captioning_caption_delete(payload: dict) -> dict:
    return prompt_captioning_delete_saved_caption(str(payload.get("caption_id") or ""))


@app.post("/api/prompt-captioning/caption/duplicate")
def prompt_captioning_caption_duplicate(payload: dict) -> dict:
    return prompt_captioning_duplicate_saved_caption(str(payload.get("caption_id") or ""))


@app.get("/api/prompt-captioning/caption-batch-results")
def prompt_captioning_caption_batch_result_list(limit: int = 25) -> dict:
    return prompt_captioning_caption_batch_results(limit)


@app.post("/api/prompt-captioning/caption/batch-run")
def prompt_captioning_caption_batch_run(payload: dict) -> dict:
    return run_prompt_captioning_caption_batch(payload)




@app.post("/api/prompt-captioning/browse-folder")
def prompt_captioning_browse_folder(payload: dict | None = None) -> dict:
    """Open a native folder picker when Neo runs in a desktop-capable environment.

    Browser directory uploads cannot expose a backend-local folder path, so this route
    intentionally avoids webkitdirectory/file upload behavior and returns a safe
    unavailable response when a native picker cannot be opened.
    """
    try:
        # Tkinter is available in the standard Windows Python used by most Neo Studio
        # local installs. In headless/server contexts this will raise and fall back.
        import tkinter as tk  # type: ignore
        from tkinter import filedialog  # type: ignore

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select folder for Neo Studio batch captioning")
        root.destroy()
        if not selected:
            return {"ok": False, "error": "folder_picker_cancelled", "message": "Folder selection cancelled. Paste the folder path manually if needed."}
        return {"ok": True, "path": str(selected).replace("\\", "/")}
    except Exception as exc:  # noqa: BLE001 - desktop picker is optional
        return {
            "ok": False,
            "error": "folder_picker_unavailable",
            "message": "Native folder picker is not available. Paste the folder path manually.",
            "detail": str(exc),
        }


@app.post("/api/prompt-captioning/caption-batch-preview")
def prompt_captioning_caption_batch_preview_route(payload: dict) -> dict:
    return prompt_captioning_caption_batch_preview(payload)


@app.post("/api/prompt-captioning/caption-batch-start")
def prompt_captioning_caption_batch_start_route(payload: dict) -> dict:
    return run_prompt_captioning_caption_batch(payload)


@app.post("/api/prompt-captioning/caption-batch-cancel")
def prompt_captioning_caption_batch_cancel_route(payload: dict | None = None) -> dict:
    return prompt_captioning_caption_batch_cancel(payload or {})


@app.post("/api/prompt-captioning/caption-batch-resume")
def prompt_captioning_caption_batch_resume_route(payload: dict | None = None) -> dict:
    return prompt_captioning_caption_batch_resume(payload or {})


@app.post("/api/prompt-captioning/caption-batch-retry-failed")
def prompt_captioning_caption_batch_retry_failed_route(payload: dict | None = None) -> dict:
    return prompt_captioning_caption_batch_retry_failed(payload or {})


@app.get("/api/prompt-captioning/caption-batch-status")
def prompt_captioning_caption_batch_status_route(job_id: str = "") -> dict:
    return prompt_captioning_caption_batch_status(job_id)


@app.get("/api/prompt-captioning/caption-batch-export-log")
def prompt_captioning_caption_batch_export_log_route():
    return PlainTextResponse(prompt_captioning_caption_batch_export_log(), media_type="application/json")


@app.get("/api/prompt-captioning/library/snapshot")
def prompt_captioning_library_snapshot() -> dict:
    return prompt_captioning_library_export_snapshot()


@app.post("/api/prompt-captioning/library/import")
def prompt_captioning_library_import(payload: dict) -> dict:
    return prompt_captioning_library_import_snapshot(payload)


@app.get("/api/prompt-captioning/library/list")
def prompt_captioning_library_kind_list(kind: str, query: str = "", category: str = "", limit: int = 200) -> dict:
    return prompt_captioning_library_list(kind, query, category, limit)


@app.get("/api/prompt-captioning/library/record")
def prompt_captioning_library_kind_record(kind: str, record_id: str) -> dict:
    return prompt_captioning_library_record(kind, record_id)


@app.post("/api/prompt-captioning/library/update")
def prompt_captioning_library_kind_update(payload: dict) -> dict:
    kind = str(payload.get("kind") or "")
    return prompt_captioning_library_update(kind, payload)


@app.post("/api/prompt-captioning/library/duplicate")
def prompt_captioning_library_kind_duplicate(payload: dict) -> dict:
    return prompt_captioning_library_duplicate(str(payload.get("kind") or ""), str(payload.get("record_id") or payload.get("id") or ""))


@app.post("/api/prompt-captioning/library/reuse-payload")
def prompt_captioning_library_reuse(payload: dict) -> dict:
    return prompt_captioning_build_reuse_payload(str(payload.get("kind") or ""), str(payload.get("record_id") or payload.get("id") or ""), str(payload.get("target") or "prompt_studio"), str(payload.get("mode") or "replace"))


@app.post("/api/prompt-captioning/history/clear")
def prompt_captioning_history_clear_route(payload: dict) -> dict:
    return prompt_captioning_history_clear(str(payload.get("kind") or ""))


@app.post("/api/prompt-captioning/send-to-workspace")
def prompt_captioning_send_to_workspace(payload: dict) -> dict:
    return prompt_captioning_build_cross_tab_handoff(payload)


@app.get("/api/prompt-captioning/handoff-history")
def prompt_captioning_handoff_history_route(limit: int = 50) -> dict:
    return prompt_captioning_handoff_history(limit)


@app.get("/api/prompt-captioning/result-metadata")
def prompt_captioning_result_metadata_route(limit: int = 100, tool_id: str = "") -> dict:
    return prompt_captioning_result_metadata_records(limit, tool_id)


@app.get("/api/prompt-captioning/result-metadata/{metadata_id}")
def prompt_captioning_result_metadata_record_route(metadata_id: str) -> dict:
    return prompt_captioning_result_metadata_record(metadata_id)


@app.post("/api/prompt-captioning/replay-payload")
def prompt_captioning_replay_payload_route(payload: dict) -> dict:
    return prompt_captioning_replay_payload_for_metadata(str(payload.get("metadata_id") or payload.get("id") or ""))


@app.post("/api/backend-profiles/{profile_id}/save")
def backend_profile_save(profile_id: str, updates: dict) -> dict:
    return save_backend_profile(profile_id, updates)


@app.post("/api/backend-profiles/{profile_id}/test")
def backend_profile_test(profile_id: str) -> dict:
    return test_backend_profile(profile_id)


@app.post("/api/backend-profiles/{profile_id}/clear-api-key")
def backend_profile_clear_api_key(profile_id: str) -> dict:
    return clear_backend_profile_api_key(profile_id)


@app.post("/api/backend-profiles/{profile_id}/connect")
def backend_profile_connect(profile_id: str) -> dict:
    return connect_backend_profile(profile_id)


@app.post("/api/backend-profiles/{profile_id}/disconnect")
def backend_profile_disconnect(profile_id: str) -> dict:
    return disconnect_backend_profile(profile_id)


@app.post("/api/backend-profiles/default")
def backend_profile_default(payload: dict) -> dict:
    return set_default_backend_profile(payload.get("surface", ""), payload.get("profile_id", ""))




@app.get("/api/admin/image/extensions")
def admin_image_extensions() -> dict:
    """Admin Image Extension Manager payload.

    This is a surface-scoped wrapper over the core extension registry so Admin > Image
    can evolve without duplicating extension runtime state.
    """
    payload = get_surface_extension_payload("image")
    registry = get_extension_payload()
    payload.update({
        "manager_version": "0.3.0-a11-contract",
        "admin_data_contract_version": registry.get("admin_data_contract_version"),
        "surface": "image",
        "installed_dir": registry.get("installed_dir"),
        "state_path": registry.get("state_path"),
        "enabled_extensions": [
            record.get("id") or record.get("manifest", {}).get("id")
            for record in payload.get("extensions", [])
            if record.get("registry_enabled") is True
        ],
        "disabled_extensions": [
            record.get("id") or record.get("manifest", {}).get("id")
            for record in payload.get("extensions", [])
            if record.get("registry_enabled") is not True
        ],
        "state_fields": registry.get("state_fields", {}),
        "actions": ["enable", "disable", "install_github", "install_zip", "update", "remove", "view_manifest", "check_compatibility"],
    })
    return payload


@app.post("/api/admin/image/extensions/{extension_id}/enable")
def admin_image_extension_enable(extension_id: str) -> dict:
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != "image":
        raise HTTPException(status_code=404, detail=f"Unknown image extension: {extension_id}")
    return set_extension_enabled(extension_id, True)


@app.post("/api/admin/image/extensions/{extension_id}/disable")
def admin_image_extension_disable(extension_id: str) -> dict:
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != "image":
        raise HTTPException(status_code=404, detail=f"Unknown image extension: {extension_id}")
    return set_extension_enabled(extension_id, False)


@app.post("/api/admin/image/extensions/install-github")
def admin_image_extension_install_github(payload: dict) -> dict:
    result = install_extension_from_github(payload.get("repo_url", ""), payload.get("branch"))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/extensions/install-zip")
async def admin_image_extension_install_zip(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="A .zip extension package is required.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)
    try:
        result = install_extension_from_zip(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/extensions/{extension_id}/update")
def admin_image_extension_update(extension_id: str) -> dict:
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != "image":
        raise HTTPException(status_code=404, detail=f"Unknown image extension: {extension_id}")
    result = update_extension(extension_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/extensions/{extension_id}/remove")
def admin_image_extension_remove(extension_id: str) -> dict:
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != "image":
        raise HTTPException(status_code=404, detail=f"Unknown image extension: {extension_id}")
    return remove_extension(extension_id)


@app.get("/api/admin/image/node-manager/state")
def admin_image_node_manager_state(scan: bool = False) -> dict:
    return get_node_manager_state(scan=scan)


@app.post("/api/admin/image/node-manager/settings")
def admin_image_node_manager_settings(payload: dict) -> dict:
    return save_node_manager_settings(payload)


@app.post("/api/admin/image/node-manager/scan-disk")
def admin_image_node_manager_scan_disk(payload: dict | None = None) -> dict:
    # Scan should honor unsaved form values from the UI. Otherwise users type paths,
    # click Scan Disk, and Neo scans the old saved/empty path.
    if payload:
        save_node_manager_settings(payload)
    return scan_node_manager_disk()


@app.post("/api/admin/image/node-manager/install")
def admin_image_node_manager_install(payload: dict) -> dict:
    result = install_node_from_github(payload.get("repo_url", ""), payload.get("branch"), payload.get("folder_name"))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/node-manager/update")
def admin_image_node_manager_update(payload: dict) -> dict:
    result = update_node(payload.get("folder_name", ""))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/node-manager/detect-python")
def admin_image_node_manager_detect_python(payload: dict | None = None) -> dict:
    payload = payload or {}
    return detect_comfy_python_path(payload.get("comfy_root_path"))


@app.post("/api/admin/image/node-manager/validate-python")
def admin_image_node_manager_validate_python(payload: dict) -> dict:
    result = validate_comfy_python_path(payload.get("python_path", ""))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/node-manager/install-requirements")
def admin_image_node_manager_install_requirements(payload: dict) -> dict:
    result = install_node_requirements(payload.get("folder_name", ""))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/image/node-manager/open-folder")
def admin_image_node_manager_open_folder() -> dict:
    result = open_custom_nodes_folder()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


MEDIA_ADMIN_EXTENSION_SURFACES = {"image", "video", "text", "voice", "music"}


def _require_media_admin_surface(surface_id: str) -> str:
    surface = (surface_id or "").strip().lower()
    if surface not in MEDIA_ADMIN_EXTENSION_SURFACES:
        raise HTTPException(status_code=404, detail=f"Unknown media admin surface: {surface_id}")
    return surface


def _surface_extension_manager_payload(surface_id: str) -> dict:
    surface = _require_media_admin_surface(surface_id)
    payload = get_surface_extension_payload(surface)
    registry = get_extension_payload()
    payload.update({
        "manager_version": "0.3.0-a11-contract",
        "admin_data_contract_version": registry.get("admin_data_contract_version"),
        "surface": surface,
        "installed_dir": registry.get("installed_dir"),
        "data_extensions_dir": f"neo_data/extensions/{surface}",
        "state_path": registry.get("state_path"),
        "enabled_extensions": [
            record.get("id") or record.get("manifest", {}).get("id")
            for record in payload.get("extensions", [])
            if record.get("registry_enabled") is True
        ],
        "disabled_extensions": [
            record.get("id") or record.get("manifest", {}).get("id")
            for record in payload.get("extensions", [])
            if record.get("registry_enabled") is not True
        ],
        "state_fields": registry.get("state_fields", {}),
        "actions": ["enable", "disable", "install_github", "install_zip", "update", "remove", "view_manifest", "check_compatibility"],
    })
    return payload


@app.get("/api/admin/media")
def admin_media_surfaces() -> dict:
    return {
        "schema_version": "neo.admin.media.v1",
        "surfaces": sorted(MEDIA_ADMIN_EXTENSION_SURFACES),
        "shared_managers": {"node_manager": "image"},
        "extension_manager_endpoint": "/api/admin/{surface}/extensions",
    }


@app.get("/api/admin/{surface_id}/extensions")
def admin_surface_extensions(surface_id: str) -> dict:
    return _surface_extension_manager_payload(surface_id)


@app.post("/api/admin/{surface_id}/extensions/install-github")
def admin_surface_extension_install_github(surface_id: str, payload: dict) -> dict:
    surface = _require_media_admin_surface(surface_id)
    result = install_extension_from_github(payload.get("repo_url", ""), payload.get("branch"), surface)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/{surface_id}/extensions/install-zip")
async def admin_surface_extension_install_zip(surface_id: str, file: UploadFile = File(...)) -> dict:
    surface = _require_media_admin_surface(surface_id)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="A .zip extension package is required.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)
    try:
        result = install_extension_from_zip(tmp_path, surface)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/{surface_id}/extensions/{extension_id}/enable")
def admin_surface_extension_enable(surface_id: str, extension_id: str) -> dict:
    surface = _require_media_admin_surface(surface_id)
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != surface:
        raise HTTPException(status_code=404, detail=f"Unknown {surface} extension: {extension_id}")
    return set_extension_enabled(extension_id, True)


@app.post("/api/admin/{surface_id}/extensions/{extension_id}/disable")
def admin_surface_extension_disable(surface_id: str, extension_id: str) -> dict:
    surface = _require_media_admin_surface(surface_id)
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != surface:
        raise HTTPException(status_code=404, detail=f"Unknown {surface} extension: {extension_id}")
    return set_extension_enabled(extension_id, False)


@app.post("/api/admin/{surface_id}/extensions/{extension_id}/update")
def admin_surface_extension_update(surface_id: str, extension_id: str) -> dict:
    surface = _require_media_admin_surface(surface_id)
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != surface:
        raise HTTPException(status_code=404, detail=f"Unknown {surface} extension: {extension_id}")
    result = update_extension(extension_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors", []))
    return result


@app.post("/api/admin/{surface_id}/extensions/{extension_id}/remove")
def admin_surface_extension_remove(surface_id: str, extension_id: str) -> dict:
    surface = _require_media_admin_surface(surface_id)
    match = get_extension(extension_id)
    if match is None or match.manifest.surface != surface:
        raise HTTPException(status_code=404, detail=f"Unknown {surface} extension: {extension_id}")
    return remove_extension(extension_id)


@app.get("/api/extensions")
def extensions() -> dict:
    return get_extension_payload()


@app.get("/api/extensions/surfaces/{surface_id}")
def surface_extensions(surface_id: str, subtab_id: str | None = None, workspace_app: str | None = None, workflow_mode: str | None = None) -> dict:
    return get_surface_extension_payload(surface_id, subtab_id, workspace_app, workflow_mode)




def _lora_catalog_names_for_profile(profile_id: str | None = None) -> list[str]:
    """Return LoRA names from the active Comfy backend's LoraLoader choices."""
    profile_id = (profile_id or "").strip() or str(get_backend_profile_payload().get("defaults", {}).get("image") or "")
    if not profile_id:
        return []
    try:
        provider, _profile = _profile_bound_provider(profile_id)
        models = provider.discover_models()
    except Exception:  # noqa: BLE001 - library browser should still work with saved records offline.
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in {"lora", "loras"}:
            continue
        name = str(item.get("name") or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def _embedding_catalog_names_for_profile(profile_id: str | None = None) -> list[str]:
    """Return Embeddings/TI names from provider model catalogs when available.

    Comfy does not expose a default EmbeddingLoader node like LoRA's LoraLoader,
    so this resolver only consumes explicit provider records whose kind is an
    embedding/textual inversion asset. Local folder scan remains the primary path.
    """
    profile_id = (profile_id or "").strip() or str(get_backend_profile_payload().get("defaults", {}).get("image") or "")
    if not profile_id:
        return []
    try:
        provider, _profile = _profile_bound_provider(profile_id)
        models = provider.discover_models()
    except Exception:  # noqa: BLE001 - library browser should still work with saved records offline.
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict) or str(item.get("kind") or "").casefold() not in {"embedding", "embeddings", "textual_inversion", "textual-inversion", "ti"}:
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def _comfy_catalog_timeout_seconds(value: object) -> float:
    """Use the configured local-backend timeout without letting catalog reads hang forever."""
    try:
        timeout = float(value or 30)
    except (TypeError, ValueError):
        timeout = 30.0
    return max(5.0, min(timeout, 30.0))


_ADETAILER_COMFY_MODEL_FOLDER_KEYS = (
    "ultralytics_bbox",
    "ultralytics_segm",
    "ultralytics",
    "onnx",
    "sams",
    "adetailer",
)


def _adetailer_model_names_from_folder_payload(payload: object) -> list[str]:
    """Normalize Comfy ``/models/<folder>`` responses without guessing names."""

    candidates: list[object] = []
    if isinstance(payload, list):
        candidates.extend(payload)
    elif isinstance(payload, dict):
        for key in ("models", "files", "items", "names"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    names: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path")
        else:
            raw = item
        name = str(raw or "").strip().replace("\\", "/")
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _adetailer_registered_folder_keys(payload: object) -> list[str]:
    candidates: list[object] = []
    if isinstance(payload, list):
        candidates.extend(payload)
    elif isinstance(payload, dict):
        for key in ("folders", "folder_names", "models", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    return [str(item).strip() for item in candidates if isinstance(item, str) and str(item).strip()]


def _adetailer_comfy_model_folders(provider: ComfyProvider, timeout_seconds: float) -> tuple[dict[str, list[str]], dict[str, object]]:
    """Read the detector folders registered by the selected live Comfy process."""

    discovered_keys: list[str] = []
    errors: dict[str, str] = {}
    try:
        discovered_keys = _adetailer_registered_folder_keys(provider._get_json("/models", timeout=timeout_seconds))
    except Exception as exc:  # noqa: BLE001 - an older Comfy may not expose the index route.
        errors["models_index"] = type(exc).__name__

    discovered_by_fold = {key.casefold(): key for key in discovered_keys}
    folder_names = [
        discovered_by_fold[key.casefold()]
        for key in _ADETAILER_COMFY_MODEL_FOLDER_KEYS
        if key.casefold() in discovered_by_fold
    ]
    # Compatibility for Comfy builds that expose /models/<folder> but not /models.
    if not discovered_keys:
        folder_names = list(_ADETAILER_COMFY_MODEL_FOLDER_KEYS)

    folders: dict[str, list[str]] = {}
    for folder_name in folder_names:
        normalized_key = folder_name.casefold()
        try:
            payload = provider._get_json(f"/models/{parse.quote(folder_name, safe='')}", timeout=timeout_seconds)
            folders[normalized_key] = _adetailer_model_names_from_folder_payload(payload)
        except Exception as exc:  # noqa: BLE001 - keep other registered folders usable.
            errors[normalized_key] = type(exc).__name__
    return folders, {
        "index_available": bool(discovered_keys),
        "registered_folder_count": len(discovered_keys),
        "queried_folder_count": len(folder_names),
        "error_codes": errors,
    }


def _controlnet_backend_for_profile(profile_id: str | None = None) -> dict:
    """Return Comfy runtime details for ControlNet map generation.

    The extension owns map generation, but it needs the active Comfy base URL and
    object_info to run the same V1-style Aux preprocessor workflow instead of
    merely echoing the source image back into the generated-map slot.
    """
    profile_id = (profile_id or "").strip() or str(get_backend_profile_payload().get("defaults", {}).get("image") or "")
    profile = get_backend_profile(profile_id) if profile_id else None
    if not profile or profile.get("provider_id") not in {"comfyui", "comfyui_portable"}:
        return {"object_info": {}}
    connection = profile.get("connection", {}) or {}
    runtime = profile.get("runtime", {}) or {}
    base_url = str(connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
    try:
        timeout = float(connection.get("timeout_seconds") or 30)
    except (TypeError, ValueError):
        timeout = 30.0
    object_info = {}
    object_info_error_code = ""
    object_info_timeout_seconds = _comfy_catalog_timeout_seconds(timeout)
    try:
        provider, _profile = _profile_bound_provider(profile_id)
        if isinstance(provider, ComfyProvider):
            object_info = provider._get_json("/object_info", timeout=object_info_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - catalog diagnostics degrade safely while Comfy is offline.
        object_info = {}
        object_info_error_code = type(exc).__name__
    return {
        "profile_id": profile_id,
        "provider_id": profile.get("provider_id"),
        "base_url": base_url,
        "timeout_seconds": timeout,
        "object_info_timeout_seconds": object_info_timeout_seconds,
        "object_info_error_code": object_info_error_code,
        "portable_path": connection.get("portable_path") or "",
        "comfy_root": connection.get("comfy_root") or runtime.get("comfy_root") or "",
        "extra_model_paths_yaml": connection.get("extra_model_paths_yaml") or runtime.get("extra_model_paths_yaml") or "",
        "object_info": object_info if isinstance(object_info, dict) else {},
    }


def _controlnet_object_info_for_profile(profile_id: str | None = None) -> dict:
    return _controlnet_backend_for_profile(profile_id).get("object_info", {})


def _adetailer_backend_for_profile(profile_id: str | None = None) -> dict:
    """Bind ADetailer to Neo's configured models root and live Comfy folders.

    A URL-only Comfy profile cannot reveal its local filesystem root. Neo's
    local Admin Models path setting is therefore the authoritative filesystem
    source when present. Absolute values remain server-side and are redacted by
    the ADetailer API boundary.
    """

    backend = _controlnet_backend_for_profile(profile_id)
    if backend.get("provider_id") not in {"comfyui", "comfyui_portable"}:
        return backend

    try:
        model_paths = load_model_paths(create=False)
    except Exception:  # noqa: BLE001 - model discovery still has live Comfy fallbacks.
        model_paths = {}
    configured_backends = model_paths.get("backends") if isinstance(model_paths.get("backends"), dict) else {}
    configured_comfy = configured_backends.get("comfyui") if isinstance(configured_backends.get("comfyui"), dict) else {}
    if configured_comfy.get("enabled", True) is not False:
        configured_models_root = str(configured_comfy.get("models_root") or "").strip()
        configured_comfy_root = str(configured_comfy.get("root") or "").strip()
        if configured_models_root:
            backend["configured_models_root"] = configured_models_root
            backend["models_root_source"] = "admin_models_paths"
        if configured_comfy_root:
            backend["configured_comfy_root"] = configured_comfy_root
            if not str(backend.get("comfy_root") or "").strip():
                backend["comfy_root"] = configured_comfy_root

    if backend.get("object_info"):
        folder_timeout = min(float(backend.get("object_info_timeout_seconds") or 5.0), 5.0)
        try:
            provider, _profile = _profile_bound_provider(str(backend.get("profile_id") or ""))
            if isinstance(provider, ComfyProvider):
                folders, diagnostics = _adetailer_comfy_model_folders(provider, folder_timeout)
                backend["comfy_model_folders"] = folders
                backend["comfy_model_folder_diagnostics"] = diagnostics
        except Exception as exc:  # noqa: BLE001 - filesystem discovery remains available.
            backend["comfy_model_folder_diagnostics"] = {
                "index_available": False,
                "registered_folder_count": 0,
                "queried_folder_count": 0,
                "error_codes": {"resolver": type(exc).__name__},
            }
    return backend


def _lora_metadata_for_profile(profile_id: str | None, lora_name: str) -> dict:
    """Return safetensors metadata for a Comfy LoRA without requiring a manual local folder path."""
    profile_id = (profile_id or "").strip() or str(get_backend_profile_payload().get("defaults", {}).get("image") or "")
    profile = get_backend_profile(profile_id) if profile_id else None
    if not profile or profile.get("provider_id") not in {"comfyui", "comfyui_portable"}:
        return {"ok": False, "metadata": {}, "error": "Active profile is not a Comfy backend."}
    connection = profile.get("connection", {}) or {}
    base_url = str(connection.get("base_url") or "").rstrip("/")
    timeout = float(connection.get("timeout_seconds") or 5)
    return fetch_comfy_lora_metadata(base_url, lora_name, timeout=timeout)


register_lora_stack_library_routes(app, ROOT_DIR, catalog_resolver=_lora_catalog_names_for_profile, metadata_resolver=_lora_metadata_for_profile)
register_embeddings_ti_library_routes(app, ROOT_DIR, catalog_resolver=_embedding_catalog_names_for_profile)
register_controlnet_map_routes(app, ROOT_DIR, object_info_resolver=_controlnet_object_info_for_profile, backend_resolver=_controlnet_backend_for_profile)
register_ip_adapter_node_routes(app, object_info_resolver=_controlnet_object_info_for_profile, backend_resolver=_controlnet_backend_for_profile)
register_adetailer_api_routes(app, object_info_resolver=_controlnet_object_info_for_profile, backend_resolver=_adetailer_backend_for_profile)
register_style_stack_api_routes(app, ROOT_DIR)
register_wildcards_api_routes(app, ROOT_DIR)



@app.get("/api/extensions/ui-contract")
def extension_ui_contracts(detail_mode: str = "guided") -> dict:
    return get_extension_ui_contract_payload(detail_mode=detail_mode)

@app.get("/api/extensions/{extension_id}/asset/{asset_path:path}")
def extension_asset(extension_id: str, asset_path: str) -> FileResponse:
    """Serve extension-owned UI assets from a validated installed extension folder.

    External Image panels declare HTML/CSS/JS under asset_bundle. The browser cannot
    read those files directly from disk, so the workspace renderer loads them through
    this guarded route. The asset path must be listed in the manifest asset bundle.
    """
    record = get_extension(extension_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown extension: {extension_id}")
    manifest = record.manifest
    allowed: set[str] = set()
    bundle = getattr(manifest, "asset_bundle", None)
    if bundle is not None:
        for group in ("js", "css", "html", "assets"):
            for item in getattr(bundle, group, []) or []:
                allowed.add(str(item).replace("\\", "/").lstrip("/"))
    requested = str(asset_path or "").replace("\\", "/").lstrip("/")
    if not requested or requested not in allowed or ".." in Path(requested).parts:
        raise HTTPException(status_code=404, detail="Extension asset is not declared in the manifest asset bundle.")
    base = Path(record.install_path).resolve()
    path = (base / requested).resolve()
    if base not in path.parents and path != base:
        raise HTTPException(status_code=404, detail="Extension asset path escaped the extension folder.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Extension asset file not found.")
    media_type = "text/plain"
    if path.suffix == ".js":
        media_type = "application/javascript"
    elif path.suffix == ".css":
        media_type = "text/css"
    elif path.suffix in {".html", ".htm"}:
        media_type = "text/html"
    return FileResponse(path, media_type=media_type)


@app.get("/api/extensions/{extension_id}")
def extension(extension_id: str) -> dict:
    match = get_extension(extension_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown extension: {extension_id}")
    return model_to_dict(match)



@app.get("/api/extensions/{extension_id}/ui-contract")
def extension_ui_contract(extension_id: str, detail_mode: str = "guided") -> dict:
    payload = get_extension_ui_contract_payload(extension_id=extension_id, detail_mode=detail_mode)
    if not payload.get("contracts"):
        raise HTTPException(status_code=404, detail=f"Unknown extension: {extension_id}")
    return payload

@app.post("/api/extensions/{extension_id}/enable")
def extension_enable(extension_id: str) -> dict:
    result = set_extension_enabled(extension_id, True)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("errors", []))
    return result


@app.post("/api/extensions/{extension_id}/disable")
def extension_disable(extension_id: str) -> dict:
    result = set_extension_enabled(extension_id, False)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("errors", []))
    return result


@app.post("/api/extensions/{extension_id}/remove")
def extension_remove(extension_id: str) -> dict:
    result = remove_extension(extension_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("errors", []))
    return result


@app.post("/api/extensions/check-compatibility")
def extension_check_compatibility(payload: dict) -> dict:
    return check_extension_compatibility(payload)




def _default_backend_profile_id_for_surface(surface: str) -> str:
    surface = str(surface or "").strip()
    defaults = get_backend_profile_payload().get("defaults", {}) or {}
    candidates = [
        defaults.get(surface),
        defaults.get("text") if surface in {"assistant", "roleplay", "prompt_captioning"} else "",
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def _profile_is_cloud_api(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    return str(profile.get("connection_type") or connection.get("connection_type") or "").strip().lower() == "cloud_api"


def _runtime_status_from_profile(profile: dict | None) -> str:
    if not isinstance(profile, dict):
        return "missing_config"
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    return str(profile.get("runtime_status") or runtime.get("status") or "disconnected").strip().lower()


def _require_backend_connected_for_task(profile_id: str, *, surface: str, operation: str) -> dict:
    pid = str(profile_id or "").strip() or _default_backend_profile_id_for_surface(surface)
    if not pid:
        raise HTTPException(status_code=409, detail=f"Backend not connected. Configure and connect a backend before running {operation}.")
    profile = get_backend_profile_for_runtime(pid) or get_backend_profile(pid)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown backend profile for {operation}: {pid}")
    if profile.get("enabled") is False:
        raise HTTPException(status_code=409, detail=f"Backend profile '{pid}' is disabled. Enable and connect it before running {operation}.")
    # Cloud/API profiles still have provider-side auth validation, but public-preview
    # generation should require an explicit Test/API check instead of silently running
    # from a template profile.
    if not is_backend_profile_connected_for_task(pid):
        status = _runtime_status_from_profile(get_backend_profile(pid) or profile)
        raise HTTPException(status_code=409, detail=f"Backend not connected. Click Connect/Test for '{pid}' before running {operation}. Current status: {status}.")
    live_profile = get_backend_profile_for_live_task(pid) or profile
    status = _runtime_status_from_profile(live_profile)
    runtime = live_profile.get("runtime") if isinstance(live_profile.get("runtime"), dict) else {}
    if status not in {"connected", "online", "ready", "available"} or runtime.get("reachable") is False:
        raise HTTPException(status_code=409, detail=f"Backend profile '{pid}' is {status or 'unreachable'}. Reconnect/test it before running {operation}.")
    return live_profile


def _profile_bound_provider(profile_id: str):
    ui_profile = get_backend_profile(profile_id)
    if ui_profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown backend profile: {profile_id}")
    runtime_profile = get_backend_profile_for_runtime(profile_id) or ui_profile
    connection_type = str(ui_profile.get("connection_type") or (ui_profile.get("connection") or {}).get("connection_type") or "").strip().lower()

    _require_backend_connected_for_task(profile_id, surface=str(ui_profile.get("surface") or "image"), operation=f"{str(ui_profile.get('surface') or 'image').title()} task")
    # Local task routes must use a live task-facing probe, not the passive
    # UI-facing profile list. The Admin listing deliberately shows local profiles
    # as disconnected when auto_connect is off, but generation now additionally
    # requires an explicit Connect/Test in the current server session.
    profile = ui_profile
    if connection_type != "cloud_api":
        live_profile = get_backend_profile_for_live_task(profile_id)
        if live_profile is not None:
            profile = live_profile

    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime"), dict) else {}
    runtime_status = str(profile.get("runtime_status") or runtime.get("status") or "disconnected").strip().lower()
    provider_id = str(profile.get("provider_id") or "").strip()
    # Local backends still require an active/tested connection. Cloud API backends
    # can validate auth/config inside their provider adapter, which gives cleaner
    # missing-key/auth-failed errors and supports env-key setups without fake local
    # connect semantics.
    if connection_type != "cloud_api" and runtime_status in {"disconnected", "offline", "missing_config", "disabled", "error", "unknown"}:
        raise HTTPException(status_code=409, detail=f"Backend profile '{profile_id}' is {runtime_status}. Click Connect/Test before running image tasks.")
    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider for backend profile: {profile.get('provider_id')}")
    if provider_id in {"comfyui", "comfyui_portable"}:
        # Catalog scans are read-only but need the freshest Connect/Test profile
        # because the passive Admin list can lag behind local runtime status and
        # cached model buckets. Unlike queueing, this remains lenient: failures
        # degrade into endpoint/filesystem warnings instead of blocking the UI.
        live_profile = get_backend_profile_for_live_task(profile_id) or profile
        connection = live_profile.get("connection", {}) or {}
        runtime = live_profile.get("runtime", {}) or {}
        base_url = connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188"
        timeout = float(connection.get("timeout_seconds") or 3)
        return ComfyProvider(provider.manifest, base_url=base_url, timeout=timeout), live_profile
    if provider_id == "xai_grok":
        return XaiGrokProvider(provider.manifest, profile=runtime_profile), profile
    return provider, profile


def _profile_model_catalog_provider(profile_id: str):
    """Lenient provider binding for read-only model catalog scans.

    Image Upscale dropdown refreshes should not disappear just because the
    profile runtime status is stale after restart. The queue path remains strict
    through _profile_bound_provider; this resolver only builds a provider adapter
    so the catalog route can query Comfy live and/or scan the configured local
    portable path.
    """
    profile = get_backend_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown backend profile: {profile_id}")
    provider_id = str(profile.get("provider_id") or "").strip()
    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider for backend profile: {profile.get('provider_id')}")
    if provider_id in {"comfyui", "comfyui_portable"}:
        connection = profile.get("connection", {}) or {}
        runtime = profile.get("runtime", {}) or {}
        base_url = connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:8188"
        timeout = float(connection.get("timeout_seconds") or 3)
        return ComfyProvider(provider.manifest, base_url=base_url, timeout=timeout), profile
    if provider_id == "xai_grok":
        runtime_profile = get_backend_profile_for_runtime(profile_id) or profile
        return XaiGrokProvider(provider.manifest, profile=runtime_profile), profile
    if provider_id in {"remove_bg", "clipdrop_remove_bg"}:
        runtime_profile = get_backend_profile_for_runtime(profile_id) or profile
        runtime_provider = get_provider(provider_id, profile=runtime_profile)
        if runtime_provider is None:
            raise HTTPException(status_code=404, detail=f"Unknown commercial background-removal provider: {provider_id}")
        return runtime_provider, profile
    return provider, profile


IMAGE_JOB_CONTEXTS: dict[str, dict] = load_recent_image_job_contexts(ROOT_DIR)
PERSISTED_IMAGE_RESULTS: dict[str, dict] = {}


def _remember_image_job_context(job_id: str, context: dict, *, status: str = "active") -> dict:
    merged = {**(context or {}), "job_id": job_id or (context or {}).get("job_id") or ""}
    IMAGE_JOB_CONTEXTS[merged["job_id"]] = merged
    try:
        persisted = save_image_job_context(ROOT_DIR, merged, status=status)
        if persisted.get("ok") and isinstance(persisted.get("context"), dict):
            IMAGE_JOB_CONTEXTS[merged["job_id"]] = persisted["context"]
            return persisted["context"]
    except Exception:
        # Runtime context persistence must never block generation or polling.
        pass
    return merged


def _load_or_default_image_job_context(job_id: str, fallback: dict) -> dict:
    context = IMAGE_JOB_CONTEXTS.get(job_id)
    if not context:
        try:
            context = load_image_job_context(ROOT_DIR, job_id)
        except Exception:
            context = None
    if not context:
        context = fallback
    if job_id:
        IMAGE_JOB_CONTEXTS[job_id] = context
    return context


def _record_image_upscale_job_context(job_id: str, context: dict) -> None:
    _remember_image_job_context(job_id, context, status="active")


register_image_upscale_api_routes(
    app,
    ROOT_DIR,
    profile_provider_resolver=_profile_bound_provider,
    model_catalog_provider_resolver=_profile_model_catalog_provider,
    context_recorder=_record_image_upscale_job_context,
)


def _record_background_removal_job_context(job_id: str, context: dict) -> None:
    _remember_image_job_context(job_id, context, status="active")


def _persist_background_removal_native_outputs(provider_outputs: list[dict], context: dict) -> dict:
    """Persist synchronous Neo-native remover outputs through the canonical Image ledger."""
    persisted = persist_image_outputs(provider_outputs=provider_outputs, job_context=context)
    record = persisted.record if isinstance(persisted.record, dict) else {}
    outputs = ((record.get("outputs") or {}).get("files") or persisted.files) if isinstance(record, dict) else persisted.files
    try:
        _remember_image_job_context(str(context.get("job_id") or ""), context, status="completed")
    except Exception:
        pass
    return {
        "ok": bool(persisted.ok),
        "result_id": persisted.result_id,
        "record": record,
        "record_path": str(persisted.record_path),
        "outputs": outputs,
        "files": persisted.files,
        "errors": persisted.errors,
    }


register_background_removal_api_routes(
    app,
    ROOT_DIR,
    profile_provider_resolver=_profile_bound_provider,
    model_catalog_provider_resolver=_profile_model_catalog_provider,
    detector_backend_resolver=_adetailer_backend_for_profile,
    context_recorder=_record_background_removal_job_context,
    native_result_persister=_persist_background_removal_native_outputs,
)


def _record_final_polish_lab_job_context(job_id: str, context: dict) -> None:
    _remember_image_job_context(job_id, context, status="staged")


if register_final_polish_lab_api_routes is not None:
    register_final_polish_lab_api_routes(
        app,
        ROOT_DIR,
        context_recorder=_record_final_polish_lab_job_context,
    )



@app.get("/api/image/job-contexts")
def image_job_contexts_payload() -> dict:
    """Return persisted Image job-context index for restart recovery diagnostics."""
    loaded = load_recent_image_job_contexts(ROOT_DIR)
    if loaded:
        IMAGE_JOB_CONTEXTS.update(loaded)
    payload = image_job_context_index(ROOT_DIR)
    payload["memory_loaded_count"] = len(IMAGE_JOB_CONTEXTS)
    payload["retention_days"] = 7
    return payload


@app.post("/api/image/job-contexts/prune")
def image_job_contexts_prune(payload: dict | None = None) -> dict:
    """Prune old persisted Image job contexts without touching outputs or source files."""
    retention_days = 7
    if isinstance(payload, dict) and payload.get("retention_days"):
        try:
            retention_days = max(1, min(90, int(payload.get("retention_days"))))
        except (TypeError, ValueError):
            retention_days = 7
    result = prune_image_job_contexts(ROOT_DIR, retention_days=retention_days)
    IMAGE_JOB_CONTEXTS.clear()
    IMAGE_JOB_CONTEXTS.update(load_recent_image_job_contexts(ROOT_DIR))
    result["memory_loaded_count"] = len(IMAGE_JOB_CONTEXTS)
    return result


@app.post("/api/image/source-image")
async def image_source_image_upload(file: UploadFile = File(...)) -> dict:
    """Store validated workflow source images in Neo_Data and return a reusable ref.

    Img2Img/Inpaint/Outpaint source inputs must be Neo-owned. The provider may
    upload/copy them to backend input folders as temporary handoff later, but the
    UI state and metadata keep the Neo_Data source path as authority.
    """
    try:
        stored = await validate_and_store_image_upload(
            file,
            target_dir=IMAGE_SOURCE_INPUT_DIR,
            prefix="source",
            default_filename="source.png",
            label="source",
            repair_extension_mismatch=True,
        )
    except ImageUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "ok": True,
        "source_id": stored.stored_filename,
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "path": str(stored.path),
        "url": f"/api/image/source-file/{stored.stored_filename}",
        "storage": "neo_data/inputs/image",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "extension_repaired": stored.extension_repaired,
        "validation": "image_upload_safety_v1",
    }


@app.get("/api/image/source-file/{source_id}")
def image_source_image_file(source_id: str) -> FileResponse:
    safe_name = Path(source_id).name
    path = IMAGE_SOURCE_INPUT_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Source image not found")
    return FileResponse(path)


@app.post("/api/video/source-image")
async def video_source_image_upload(file: UploadFile = File(...)) -> dict:
    """Store WAN Img2Vid source images in the Video source folder."""
    try:
        from neo_app.video.output_paths import get_video_output_paths
        stored = await validate_and_store_image_upload(
            file,
            target_dir=get_video_output_paths("source", create=True).output_dir,
            prefix="video_source",
            default_filename="video_source.png",
            label="video source",
            repair_extension_mismatch=True,
        )
    except ImageUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "ok": True,
        "source_id": stored.stored_filename,
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "path": str(stored.path),
        "url": f"/api/video/source-file/{stored.stored_filename}",
        "storage": "neo_data/outputs/video/source",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "extension_repaired": stored.extension_repaired,
        "validation": "video_source_image_upload_v6",
        "comfy_image_name": stored.stored_filename,
    }


@app.get("/api/video/source-file/{source_id}")
def video_source_image_file(source_id: str) -> FileResponse:
    from neo_app.video.output_paths import get_video_output_paths
    safe_name = Path(source_id).name
    path = get_video_output_paths("source", create=True).output_dir / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Video source image not found")
    return FileResponse(path)


@app.post("/api/image/reference-image")
async def image_reference_image_upload(file: UploadFile = File(...)) -> dict:
    """Store validated extension reference images as Neo-owned image inputs.

    IP Adapter and other reference-style extensions need a reusable browser URL, a
    local Neo_Data path for metadata/replay, and a value that can later be copied
    into Comfy's input folder before LoadImage nodes are compiled.
    """
    try:
        stored = await validate_and_store_image_upload(
            file,
            target_dir=IMAGE_SOURCE_INPUT_DIR,
            prefix="reference",
            default_filename="reference.png",
            label="reference",
            repair_extension_mismatch=True,
        )
    except ImageUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "ok": True,
        "asset_id": stored.stored_filename,
        "reference_id": stored.stored_filename,
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "path": str(stored.path),
        "url": f"/api/image/source-file/{stored.stored_filename}",
        "storage": "neo_data/inputs/image",
        "role": "reference",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "extension_repaired": stored.extension_repaired,
        "validation": "image_upload_safety_v1",
    }


@app.post("/api/image/mask-image")
async def image_mask_image_upload(file: UploadFile = File(...)) -> dict:
    """Store validated inpaint masks in Neo_Data and return a reusable ref."""
    try:
        stored = await validate_and_store_image_upload(
            file,
            target_dir=IMAGE_MASK_INPUT_DIR,
            prefix="mask",
            default_filename="mask.png",
            label="mask",
        )
    except ImageUploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "ok": True,
        "mask_id": stored.stored_filename,
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "path": str(stored.path),
        "url": f"/api/image/mask-file/{stored.stored_filename}",
        "storage": "neo_data/inputs/image_masks",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "validation": "image_upload_safety_v1",
    }


@app.get("/api/image/mask-file/{mask_id}")
def image_mask_image_file(mask_id: str) -> FileResponse:
    safe_name = Path(mask_id).name
    path = IMAGE_MASK_INPUT_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Mask image not found")
    return FileResponse(path)




def _normalize_image_source_params(params: dict, runtime_mode: str) -> dict:
    """Resolve Neo source/mask refs into local Neo_Data paths before provider handoff."""
    normalized = dict(params or {})

    def local_from_dir(value: str, folder: Path) -> str:
        safe = Path(value or "").name
        if not safe:
            return ""
        candidate = folder / safe
        return str(candidate) if candidate.exists() and candidate.is_file() else ""

    source = str(normalized.get("source_image") or normalized.get("source_image_path") or normalized.get("init_image") or "").strip()
    source_id = str(normalized.get("source_id") or normalized.get("source_image_id") or "").strip()
    source_url = str(normalized.get("source_image_url") or normalized.get("source_url") or "").strip()
    if not source and source_id:
        source = local_from_dir(source_id, IMAGE_SOURCE_INPUT_DIR)
    if not source and source_url:
        parsed_name = Path(source_url.split("?", 1)[0]).name
        source = local_from_dir(parsed_name, IMAGE_SOURCE_INPUT_DIR) or source_url
    if source.startswith("/api/image/source-file/"):
        source = local_from_dir(source.rsplit("/", 1)[-1], IMAGE_SOURCE_INPUT_DIR) or source

    mask = str(normalized.get("mask_image") or normalized.get("mask_image_path") or normalized.get("inpaint_mask") or "").strip()
    mask_id = str(normalized.get("mask_id") or normalized.get("mask_image_id") or "").strip()
    mask_url = str(normalized.get("mask_image_url") or normalized.get("mask_url") or "").strip()
    if not mask and mask_id:
        mask = local_from_dir(mask_id, IMAGE_MASK_INPUT_DIR)
    if not mask and mask_url:
        parsed_name = Path(mask_url.split("?", 1)[0]).name
        mask = local_from_dir(parsed_name, IMAGE_MASK_INPUT_DIR) or mask_url
    if mask.startswith("/api/image/mask-file/"):
        mask = local_from_dir(mask.rsplit("/", 1)[-1], IMAGE_MASK_INPUT_DIR) or mask

    if source:
        normalized["source_image"] = source
        normalized["source_image_path"] = source
        if not normalized.get("source_image_name"):
            normalized["source_image_name"] = Path(source).name
    if source_url:
        normalized["source_image_url"] = source_url
    if mask:
        normalized["mask_image"] = mask
        normalized["mask_image_path"] = mask
        normalized["inpaint_mask"] = mask
        if not normalized.get("mask_image_name"):
            normalized["mask_image_name"] = Path(mask).name
    if mask_url:
        normalized["mask_image_url"] = mask_url

    def normalize_extra_source_lane(lane: int, aliases: tuple[str, ...]) -> None:
        path_keys = (f"source_image_{lane}", f"source_image_{lane}_path", f"source_image__{lane}") + aliases
        url_key = f"source_image_{lane}_url"
        name_key = f"source_image_{lane}_name"
        value = ""
        for key in path_keys:
            candidate = str(normalized.get(key) or "").strip()
            if candidate:
                value = candidate
                break
        url = str(normalized.get(url_key) or "").strip()
        if not value and url:
            parsed_name = Path(url.split("?", 1)[0]).name
            value = local_from_dir(parsed_name, IMAGE_SOURCE_INPUT_DIR) or url
        if value.startswith("/api/image/source-file/"):
            value = local_from_dir(value.rsplit("/", 1)[-1], IMAGE_SOURCE_INPUT_DIR) or value
        if value:
            normalized[f"source_image_{lane}"] = value
            normalized[f"source_image_{lane}_path"] = value
            normalized[f"source_image__{lane}"] = value
            if not normalized.get(name_key):
                normalized[name_key] = Path(value).name
            if lane == 2 and not normalized.get("reference_image_2_name"):
                normalized["reference_image_2_name"] = normalized.get(name_key) or Path(value).name
            if lane == 3 and not normalized.get("composition_image_name"):
                normalized["composition_image_name"] = normalized.get(name_key) or Path(value).name
        if url:
            normalized[url_key] = url

    normalize_extra_source_lane(2, ("reference_image_2",))
    normalize_extra_source_lane(3, ("composition_image", "reference_image_3"))

    if runtime_mode in {"img2img", "image_to_image", "inpaint", "outpaint"} and not normalized.get("source_image"):
        raise HTTPException(status_code=400, detail=f"{runtime_mode} requires a source image before generation.")
    if runtime_mode == "inpaint" and not normalized.get("mask_image"):
        raise HTTPException(status_code=400, detail="inpaint requires a mask image before generation.")
    return normalized


def _safe_client_generation_error_payload(payload: dict) -> dict:
    allowed: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return {"schema": "neo.image.client_generation_error.v1", "message": str(payload or "")[:1000]}
    for key in (
        "schema", "stage", "error_name", "message", "stack", "timestamp", "active_surface", "active_subtab",
        "active_profile", "image_draft_summary", "request_payload_summary", "http_status",
    ):
        if key in payload:
            allowed[key] = payload.get(key)
    provider_response = payload.get("provider_response")
    if isinstance(provider_response, dict):
        allowed["provider_response"] = {
            key: provider_response.get(key)
            for key in ("status", "message", "provider_id", "profile_id", "job_id", "runtime", "detail", "error")
            if key in provider_response
        }
    return allowed

@app.post("/api/image/client-generation-error")
def image_client_generation_error(payload: dict) -> dict:
    """Persist a browser-side generation failure report for debugging.

    This route is intentionally diagnostic-only. It lets Neo distinguish a frontend
    ReferenceError, a failed /api/image/generate response, and a Comfy queue error
    without asking the user to inspect DevTools first.
    """
    safe_payload = _safe_client_generation_error_payload(payload or {})
    try:
        IMAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        latest_path = IMAGE_LOG_DIR / "neo_client_generation_error_latest.json"
        history_path = IMAGE_LOG_DIR / "neo_client_generation_errors.jsonl"
        latest_path.write_text(json.dumps(safe_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "path": str(latest_path)}


@app.get("/api/image/client-generation-error/latest")
def image_client_generation_error_latest() -> dict:
    latest_path = IMAGE_LOG_DIR / "neo_client_generation_error_latest.json"
    if not latest_path.exists():
        return {"ok": False, "message": "No client generation diagnostic has been saved yet.", "path": str(latest_path)}
    try:
        return {"ok": True, "path": str(latest_path), "diagnostic": json.loads(latest_path.read_text(encoding="utf-8"))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "path": str(latest_path)}

@app.post("/api/image/generate")
def image_generate(payload: dict) -> dict:
    profile_id = payload.get("profile_id") or payload.get("backend_profile_id")
    if not profile_id:
        raise HTTPException(status_code=400, detail=normalize_image_provider_error("profile_id is required", operation="image_generate"))
    try:
        provider, profile = _profile_bound_provider(profile_id)
        job_payload = payload.get("job") or payload
        normalized_params = normalize_image_seed_params(job_payload.get("params") if isinstance(job_payload.get("params"), dict) else {})
        conditioning_mode = normalize_prompt_conditioning_mode(normalized_params.get("prompt_conditioning_mode", normalized_params.get("clamp", "raw")))
        normalized_params = {**normalized_params, "prompt_conditioning_mode": conditioning_mode, "clamp": conditioning_mode}
        requested_subtab = job_payload.get("subtab") or "generate"
        requested_mode = job_payload.get("mode") or requested_subtab or "generate"
        runtime_mode = "txt2img" if requested_mode == "generate" else requested_mode
        if requested_subtab in {"img2img", "inpaint", "outpaint"} and not job_payload.get("mode"):
            runtime_mode = requested_subtab
        prompt_pass_target = "finish" if str(runtime_mode).lower() in {"finish", "refine", "redraw", "hires", "high_res", "high_res_fix"} else "base"
        prompt_extension_merge = apply_prompt_extensions(
            job_payload.get("positive_prompt") or job_payload.get("prompt") or "",
            job_payload.get("negative_prompt") or "",
            job_payload.get("extensions"),
            workflow_mode=runtime_mode,
            pass_target=prompt_pass_target,
            seed=normalized_params.get("seed", job_payload.get("seed", "")),
            repo_root=ROOT_DIR,
        )
        provider_backend_id = profile.get("provider_id") or provider.manifest.provider_id
        prompt_extension_execution = build_prompt_extension_execution_snapshot(
            provider_id=provider_backend_id,
            backend_id=provider_backend_id,
            workflow_mode=runtime_mode,
            pass_target=prompt_pass_target,
            merge_result=prompt_extension_merge,
        )
        if isinstance(prompt_extension_merge.get("extension_metadata"), dict):
            prompt_extension_merge["execution"] = prompt_extension_execution
        scene_director_prompt_extension_interop = apply_scene_director_prompt_extension_interop(
            job_payload.get("extensions"),
            prompt_extension_merge,
        )
        effective_extensions = scene_director_prompt_extension_interop.get("extensions") if isinstance(scene_director_prompt_extension_interop, dict) else job_payload.get("extensions")
        if isinstance(scene_director_prompt_extension_interop, dict):
            prompt_extension_merge["scene_director_interop"] = scene_director_prompt_extension_interop.get("metadata") or {}
        wildcards_output_metadata = build_wildcards_output_extension_metadata(effective_extensions, prompt_extension_merge)
        effective_extensions = _merge_extension_metadata_for_output(effective_extensions, wildcards_output_metadata)
        style_stack_output_metadata = build_style_stack_output_extension_metadata(effective_extensions, prompt_extension_merge)
        effective_extensions = _merge_extension_metadata_for_output(effective_extensions, style_stack_output_metadata)
        merged_positive_prompt = prompt_extension_merge.get("effective_positive") or job_payload.get("positive_prompt") or job_payload.get("prompt") or ""
        merged_negative_prompt = prompt_extension_merge.get("effective_negative") or job_payload.get("negative_prompt") or ""
        conditioning = condition_prompt_pair(
            merged_positive_prompt,
            merged_negative_prompt,
            conditioning_mode,
        )
        normalized_params = {
            **normalized_params,
            "prompt_extension_merge": prompt_extension_merge,
            "prompt_extension_execution": prompt_extension_execution,
            # Runtime-only profile identity lets filesystem bridges distinguish
            # local/shared Comfy profiles from remote URL-only connections.
            "backend_profile_id": str(profile_id),
        }
        normalized_params = _normalize_image_source_params(normalized_params, runtime_mode)
        job_payload = {
            **job_payload,
            "surface": "image",
            "provider_id": profile.get("provider_id"),
            "subtab": requested_subtab,
            # UI workflow tab id may be "generate", but provider runtime mode must be backend-neutral txt2img.
            "mode": runtime_mode,
            "prompt": merged_positive_prompt,
            "positive_prompt": merged_positive_prompt,
            "negative_prompt": merged_negative_prompt,
            "extensions": effective_extensions,
            "params": normalized_params,
            "prompt_conditioning": conditioning,
        }
        result = provider.run_job(model_from_dict(NeoJob, job_payload))
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("schema") == "neo.image.provider_error.v1":
            raise
        raise HTTPException(status_code=exc.status_code, detail=normalize_image_provider_error(exc.detail, operation="image_generate", profile_id=profile_id)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=normalize_image_provider_error(exc, operation="image_generate", profile_id=profile_id)) from exc
    output = model_to_dict(result)
    output["profile_id"] = profile_id
    output["capabilities"] = provider.feature_capability_payload()
    try:
        registry = get_generation_job_registry(ROOT_DIR)
        registry_record = registry.upsert_from_provider_result(
            job={**job_payload, "profile_id": profile_id, "backend_profile_id": profile_id},
            result=output,
            profile_id=profile_id,
            provider_id=profile.get("provider_id") or provider.manifest.provider_id,
        )
        registry_summary = registry.summary(registry_record.get("job_id") or output.get("job_id"), surface=registry_record.get("surface"))
        output["job_registry"] = registry_summary
        runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
        runtime["job_registry"] = registry_summary
        output["runtime"] = runtime
    except Exception:
        # Registry failures must never block generation; logs/provider diagnostics still exist.
        pass
    context = _remember_image_job_context(output["job_id"], _image_job_context(job_payload, profile_id, profile), status="active")
    # Phase J: cloud image providers such as Grok Imagine complete synchronously.
    # Persist completed outputs immediately so the Image tab can show Neo-owned
    # result files without depending on an extra poll or volatile provider memory.
    if output.get("status") == "completed" and output.get("outputs"):
        runtime_params = ((output.get("runtime") or {}).get("actual_params") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_params, dict) and runtime_params:
            context = {**context, "params": {**(context.get("params") if isinstance(context.get("params"), dict) else {}), **runtime_params}}
        runtime_snapshot = ((output.get("runtime") or {}).get("route_snapshot") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_snapshot, dict) and runtime_snapshot:
            context = {**context, "route_snapshot": runtime_snapshot}
        runtime_extensions = ((output.get("runtime") or {}).get("extensions") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_extensions, dict) and runtime_extensions:
            context = {**context, "extensions": runtime_extensions}
        runtime_timing = ((output.get("runtime") or {}).get("run_timing") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_timing, dict) and runtime_timing:
            context = {**context, "run_timing": runtime_timing}
        context = _remember_image_job_context(output["job_id"], context, status="completed")
        output = _attach_persisted_image_outputs(output, context)
    output = attach_progress_watchdog(output, surface="image", profile_id=profile_id, job_id=str(output.get("job_id") or ""))
    return output




@app.get("/api/image/jobs/{profile_id}/{job_id}/preview")
def image_job_live_preview(profile_id: str, job_id: str) -> dict:
    try:
        provider, profile = _profile_bound_provider(profile_id)
        fetcher = getattr(provider, "fetch_live_preview", None)
        if not callable(fetcher):
            return {"ok": False, "profile_id": profile_id, "job_id": job_id, "is_final": False, "message": "No HTTP preview exposed yet for this provider."}
        output = fetcher(job_id)
    except Exception as exc:
        payload = normalize_image_provider_error(exc, operation="image_live_preview", profile_id=profile_id, job_id=job_id)
        payload.update({"is_final": False})
        return payload
    if not isinstance(output, dict):
        output = {"ok": False, "message": "No HTTP preview exposed yet for this provider."}
    output["profile_id"] = profile_id
    output["provider_id"] = output.get("provider_id") or profile.get("provider_id")
    output.setdefault("job_id", job_id)
    output.setdefault("is_final", False)
    return output

@app.get("/api/image/jobs/{profile_id}/{job_id}")
def image_job_status(profile_id: str, job_id: str) -> dict:
    try:
        provider, profile = _profile_bound_provider(profile_id)
        result = provider.poll_job(job_id)
    except Exception as exc:
        return normalize_image_provider_error(exc, operation="image_poll", profile_id=profile_id, job_id=job_id)
    output = model_to_dict(result)
    output["profile_id"] = profile_id
    output["capabilities"] = provider.feature_capability_payload()
    try:
        registry = get_generation_job_registry(ROOT_DIR)
        registry_record = registry.upsert_from_provider_result(
            job={"surface": "image", "profile_id": profile_id, "backend_profile_id": profile_id, "provider_id": profile.get("provider_id") or provider.manifest.provider_id},
            result=output,
            profile_id=profile_id,
            provider_id=profile.get("provider_id") or provider.manifest.provider_id,
        )
        registry_summary = registry.summary(registry_record.get("job_id") or job_id, surface=registry_record.get("surface"))
        output["job_registry"] = registry_summary
        runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
        runtime["job_registry"] = registry_summary
        output["runtime"] = runtime
    except Exception:
        pass
    if output.get("status") == "completed" and output.get("outputs"):
        context = _load_or_default_image_job_context(job_id, {
            "job_id": job_id,
            "profile_id": profile_id,
            "backend_profile_id": profile_id,
            "provider_id": profile.get("provider_id"),
            "subtab": "generate",
            "mode": "txt2img",
        })
        runtime_params = ((output.get("runtime") or {}).get("actual_params") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_params, dict) and runtime_params:
            context = {**context, "params": {**(context.get("params") if isinstance(context.get("params"), dict) else {}), **runtime_params}}
        runtime_snapshot = ((output.get("runtime") or {}).get("route_snapshot") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_snapshot, dict) and runtime_snapshot:
            context = {**context, "route_snapshot": runtime_snapshot}
        runtime_extensions = ((output.get("runtime") or {}).get("extensions") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_extensions, dict) and runtime_extensions:
            context = {**context, "extensions": runtime_extensions}
        runtime_timing = ((output.get("runtime") or {}).get("run_timing") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_timing, dict) and runtime_timing:
            context = {**context, "run_timing": runtime_timing}
        context = _remember_image_job_context(job_id, context, status="completed")
        output = _attach_persisted_image_outputs(output, context)
    output = attach_progress_watchdog(output, surface="image", profile_id=profile_id, job_id=job_id)
    return output


@app.post("/api/image/jobs/{profile_id}/{job_id}/recover")
def image_job_recover_outputs(profile_id: str, job_id: str) -> dict:
    """Retry importing a backend-completed Image job into Neo_Data.

    This is intentionally separate from normal polling: a Comfy job can be finished
    and have files in the backend folder while Neo's persistence handoff failed or
    the frontend stopped at the finalization step. Recovery reuses the durable job
    context/registry and never depends on a still-alive provider instance cache.
    """
    registry = get_generation_job_registry(ROOT_DIR)
    try:
        provider, profile = _profile_bound_provider(profile_id)
        context = _load_or_default_image_job_context(job_id, {
            "job_id": job_id,
            "profile_id": profile_id,
            "backend_profile_id": profile_id,
            "provider_id": profile.get("provider_id"),
            "backend_output_root": _backend_native_output_root(profile),
            "subtab": "generate",
            "mode": "txt2img",
        })
        registry.mark_output_import_state(
            job_id,
            surface="image",
            status="importing",
            message="Retrying image output import into Neo_Data.",
            increment_attempts=False,
        )
        result = provider.poll_job(job_id)
        output = model_to_dict(result)
        output["profile_id"] = profile_id
        output["capabilities"] = provider.feature_capability_payload()
        registry_record = registry.get(job_id, surface="image") or registry.get(job_id) or {}
        registry_outputs = registry_record.get("outputs") if isinstance(registry_record.get("outputs"), list) else []
        if not output.get("outputs") and registry_outputs:
            output["outputs"] = registry_outputs
        runtime_params = ((output.get("runtime") or {}).get("actual_params") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_params, dict) and runtime_params:
            context = {**context, "params": {**(context.get("params") if isinstance(context.get("params"), dict) else {}), **runtime_params}}
        runtime_snapshot = ((output.get("runtime") or {}).get("route_snapshot") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_snapshot, dict) and runtime_snapshot:
            context = {**context, "route_snapshot": runtime_snapshot}
        runtime_extensions = ((output.get("runtime") or {}).get("extensions") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_extensions, dict) and runtime_extensions:
            context = {**context, "extensions": runtime_extensions}
        runtime_timing = ((output.get("runtime") or {}).get("run_timing") or {}) if isinstance(output.get("runtime"), dict) else {}
        if isinstance(runtime_timing, dict) and runtime_timing:
            context = {**context, "run_timing": runtime_timing}
        output["outputs"] = enrich_provider_outputs_for_local_recovery(output.get("outputs") or [], context)
        context = _remember_image_job_context(job_id, context, status="recovering")
        recovered = _attach_persisted_image_outputs(output, context, force_retry=True)
        recovered = attach_progress_watchdog(recovered, surface="image", profile_id=profile_id, job_id=job_id)
        return recovered
    except Exception as exc:  # noqa: BLE001
        try:
            registry.mark_output_import_state(
                job_id,
                surface="image",
                status="import_failed",
                message=f"Image output recovery failed: {exc}",
                errors=[str(exc)],
                recoverable=True,
                recovery_endpoint=f"/api/image/jobs/{profile_id}/{job_id}/recover",
            )
        except Exception:
            pass
        return {
            "ok": False,
            "status": "import_failed",
            "profile_id": profile_id,
            "job_id": job_id,
            "message": f"Image output recovery failed: {exc}",
            "neo_recovery": image_output_recovery_payload(job_id=job_id, profile_id=profile_id, status="import_failed", errors=[str(exc)]),
        }


def _control_image_job(profile_id: str, job_id: str, action: str) -> dict:
    try:
        provider, _profile = _profile_bound_provider(profile_id)
        if action == "cancel":
            result = provider.cancel_job(job_id)
            context = IMAGE_JOB_CONTEXTS.setdefault(job_id, {"job_id": job_id, "profile_id": profile_id})
            context["cancel_requested"] = True
            _remember_image_job_context(job_id, context, status="cancelled")
        elif action == "pause":
            result = provider.pause_job(job_id)
            _remember_image_job_context(job_id, IMAGE_JOB_CONTEXTS.get(job_id, {"job_id": job_id, "profile_id": profile_id}), status="paused")
        elif action == "resume":
            result = provider.resume_job(job_id)
            _remember_image_job_context(job_id, IMAGE_JOB_CONTEXTS.get(job_id, {"job_id": job_id, "profile_id": profile_id}), status="active")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown job control action: {action}")
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=normalize_image_provider_error(exc.detail, operation=f"image_job_{action}", profile_id=profile_id, job_id=job_id)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=normalize_image_provider_error(exc, operation=f"image_job_{action}", profile_id=profile_id, job_id=job_id)) from exc
    output = model_to_dict(result)
    output["profile_id"] = profile_id
    output["capabilities"] = provider.feature_capability_payload()
    output["action"] = action
    try:
        registry_summary = get_generation_job_registry(ROOT_DIR).summary(job_id, surface="image")
        output["job_registry"] = registry_summary
        runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
        runtime["job_registry"] = registry_summary
        output["runtime"] = runtime
    except Exception:
        pass
    return output


@app.post("/api/image/jobs/{profile_id}/{job_id}/cancel")
def image_job_cancel(profile_id: str, job_id: str) -> dict:
    return _control_image_job(profile_id, job_id, "cancel")


@app.post("/api/image/jobs/{profile_id}/{job_id}/pause")
def image_job_pause(profile_id: str, job_id: str) -> dict:
    return _control_image_job(profile_id, job_id, "pause")


@app.post("/api/image/jobs/{profile_id}/{job_id}/resume")
def image_job_resume(profile_id: str, job_id: str) -> dict:
    return _control_image_job(profile_id, job_id, "resume")



def _backend_profile_replay_snapshot(profile: dict) -> dict:
    """Return a secret-free snapshot saved into output params for replay validation."""
    profile = profile if isinstance(profile, dict) else {}
    model_block = profile.get("model") if isinstance(profile.get("model"), dict) else {}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    capabilities = profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}
    return {
        "schema_version": "neo.backend_profile.replay_snapshot.v1",
        "profile_id": str(profile.get("profile_id") or ""),
        "display_name": str(profile.get("display_name") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "provider_label": str(profile.get("provider_label") or profile.get("display_name") or profile.get("provider_id") or ""),
        "surface": str(profile.get("surface") or "image"),
        "connection_type": str(profile.get("connection_type") or ""),
        "auth_mode": str(connection.get("auth_mode") or connection.get("api_key_mode") or ""),
        "api_key_env": str(connection.get("api_key_env") or ""),
        "default_model": str(model_block.get("default_model") or ""),
        "available_models": [str(item) for item in (model_block.get("available_models") or []) if str(item or "").strip()],
        "capabilities": {str(key): bool(value) if isinstance(value, bool) else value for key, value in capabilities.items()},
        "enabled_at_generation": bool(profile.get("enabled", True)),
    }


def _validate_image_result_replay_against_current_profile(record: dict) -> dict:
    """Validate a saved image result against current Admin backend profile state."""
    record = record if isinstance(record, dict) else {}
    binding = record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else {}
    replay_validation = record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) else {}
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    profile_id = str(binding.get("backend_profile_id") or job.get("backend_profile_id") or "")
    expected_provider = str(binding.get("provider_id") or job.get("provider_id") or "")
    expected_model = str(binding.get("model") or (record.get("model") or {}).get("model") if isinstance(record.get("model"), dict) else "")
    checks = []
    profile = get_backend_profile(profile_id) if profile_id else None

    def add(check_id: str, ok: bool, severity: str, message: str, next_action: str = "") -> None:
        checks.append({"check_id": check_id, "ok": bool(ok), "state": "passed" if ok else "failed", "severity": severity, "message": message, "next_action": next_action})

    add("backend_profile_exists", bool(profile), "error", f"Backend profile {profile_id or '(missing)'} is available." if profile else f"Backend profile {profile_id or '(missing)'} is missing.", "Recreate/select a compatible backend profile before replay." if not profile else "")
    if profile:
        add("backend_profile_enabled", profile.get("enabled") is not False, "error", "Backend profile is enabled." if profile.get("enabled") is not False else "Backend profile is disabled.", "Enable the backend profile in Admin > Backends before replay." if profile.get("enabled") is False else "")
        current_provider = str(profile.get("provider_id") or "")
        add("provider_matches_record", current_provider == expected_provider, "error", f"Provider matches saved record ({expected_provider})." if current_provider == expected_provider else f"Provider drift: saved {expected_provider}, current {current_provider}.", "Use the original provider profile or create a compatible replacement." if current_provider != expected_provider else "")
        model_block = profile.get("model") if isinstance(profile.get("model"), dict) else {}
        available_models = [str(item) for item in (model_block.get("available_models") or []) if str(item or "").strip()]
        default_model = str(model_block.get("default_model") or "")
        model_ok = not expected_model or expected_model == default_model or expected_model in available_models
        add("model_available", model_ok, "warning", f"Saved model is available: {expected_model or default_model}." if model_ok else f"Saved model is not listed on the current profile: {expected_model}.", "Choose an available model before replay." if not model_ok else "")
        if str(profile.get("connection_type") or "") == "cloud_api":
            connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
            key_ok = bool(connection.get("api_key_is_configured"))
            message = str(connection.get("api_key_status_message") or ("Cloud API key is configured." if key_ok else "Cloud API key is missing."))
            add("api_key_configured", key_ok, "error", message, "Configure the API key in Admin > Backends." if not key_ok else "")
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    missing_assets = []
    for asset in source.get("input_assets", []) if isinstance(source.get("input_assets"), list) else []:
        if not isinstance(asset, dict):
            continue
        rel = str(asset.get("path") or "").strip()
        if rel:
            path = (ROOT_DIR / rel).resolve()
            if not path.exists():
                missing_assets.append(asset.get("asset_id") or asset.get("label") or rel)
    add("input_assets_available", not missing_assets, "error" if missing_assets else "info", "Saved input/source assets are available." if not missing_assets else f"Missing saved input/source assets: {', '.join(str(item) for item in missing_assets)}", "Restore the missing source/reference files before replay." if missing_assets else "")
    ok = all(item.get("ok") or item.get("severity") in {"info"} for item in checks)
    return {
        "ok": ok,
        "schema_version": "neo.image.replay_live_validation.v1",
        "result_id": str(record.get("result_id") or ""),
        "backend_profile_id": profile_id,
        "provider_id": expected_provider,
        "model": expected_model,
        "state": "ready" if ok else "blocked_or_needs_review",
        "checks": checks,
        "metadata_requirements": replay_validation,
    }


def _image_job_context(job_payload: dict, profile_id: str, profile: dict) -> dict:
    return {
        "job_id": job_payload.get("job_id") or "",
        "profile_id": profile_id,
        "backend_profile_id": profile_id,
        "provider_id": profile.get("provider_id") or job_payload.get("provider_id") or "",
        "backend_output_root": _backend_native_output_root(profile),
        "subtab": job_payload.get("subtab") or "generate",
        "mode": job_payload.get("mode") or "txt2img",
        "prompt": job_payload.get("prompt") or job_payload.get("positive_prompt") or "",
        "positive_prompt": job_payload.get("positive_prompt") or job_payload.get("prompt") or "",
        "negative_prompt": job_payload.get("negative_prompt") or "",
        "prompt_conditioning": job_payload.get("prompt_conditioning") if isinstance(job_payload.get("prompt_conditioning"), dict) else condition_prompt_pair(
            job_payload.get("positive_prompt") or job_payload.get("prompt") or "",
            job_payload.get("negative_prompt") or "",
            (job_payload.get("params") or {}).get("prompt_conditioning_mode", (job_payload.get("params") or {}).get("clamp", "raw")) if isinstance(job_payload.get("params"), dict) else "raw",
        ),
        "params": {
            **(job_payload.get("params") if isinstance(job_payload.get("params"), dict) else {}),
            "backend_profile_id": profile_id,
            "provider_id": profile.get("provider_id") or job_payload.get("provider_id") or "",
            "connection_type": profile.get("connection_type") or "",
            "_neo_backend_profile_snapshot": _backend_profile_replay_snapshot(profile),
        },
        "model": {
            "family": job_payload.get("family") or "",
            "loader": job_payload.get("loader") or "",
            "model": job_payload.get("model") or "",
            "vae": (job_payload.get("params") or {}).get("vae", "") if isinstance(job_payload.get("params"), dict) else "",
        },
        "extensions": _extension_metadata_from_job(job_payload.get("extensions")),
    }



def _backend_native_output_root(profile: dict) -> str:
    connection = profile.get("connection", {}) or {}
    explicit = str(connection.get("output_path") or connection.get("output_root") or "").strip()
    if explicit:
        return explicit
    portable = str(connection.get("portable_path") or "").strip()
    if not portable:
        return ""
    root = Path(portable).expanduser()
    if root.name.lower() == "output":
        return str(root)
    comfy_child = root / "ComfyUI" / "output"
    if comfy_child.exists():
        return str(comfy_child)
    return str(root / "output")

def _merge_extension_metadata_for_output(base: object, extra: object) -> dict:
    merged = _extension_metadata_from_job(base)
    other = _extension_metadata_from_job(extra)
    for key in ("used", "workflow_patches", "validation"):
        current = merged.get(key) if isinstance(merged.get(key), list) else []
        current.extend(other.get(key) or [])
        merged[key] = current
    for key in ("payloads", "replay_payloads", "assistant_summaries", "memory_events"):
        current = merged.get(key) if isinstance(merged.get(key), dict) else {}
        current.update(other.get(key) or {})
        merged[key] = current
    return merged


def _extension_metadata_from_job(extensions: object) -> dict:
    if isinstance(extensions, dict):
        base = {
            "used": [],
            "payloads": {},
            "workflow_patches": [],
            "validation": [],
            "replay_payloads": {},
            "assistant_summaries": {},
            "memory_events": {},
        }
        for key, value in extensions.items():
            if key in base:
                base[key] = value
        if "payloads" not in extensions and "style_stack" in extensions and isinstance(extensions.get("style_stack"), dict):
            base["payloads"]["style_stack"] = extensions.get("style_stack")
        if "payloads" not in extensions and "wildcards" in extensions and isinstance(extensions.get("wildcards"), dict):
            base["payloads"]["wildcards"] = extensions.get("wildcards")
        return base
    if isinstance(extensions, list):
        return {"used": extensions, "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}
    return {"used": [], "payloads": {}, "workflow_patches": [], "validation": [], "replay_payloads": {}, "assistant_summaries": {}, "memory_events": {}}


def _attach_persisted_image_outputs(output: dict, context: dict, *, force_retry: bool = False) -> dict:
    job_id = output.get("job_id") or context.get("job_id") or ""
    profile_id = str(output.get("profile_id") or context.get("profile_id") or context.get("backend_profile_id") or "")
    provider_outputs = enrich_provider_outputs_for_local_recovery(output.get("outputs") or [], context)
    if provider_outputs:
        output["outputs"] = provider_outputs
    if job_id in PERSISTED_IMAGE_RESULTS and not force_retry:
        persisted = PERSISTED_IMAGE_RESULTS[job_id]
    else:
        context = {**context, "job_id": job_id}
        registry = get_generation_job_registry(ROOT_DIR)
        try:
            registry.mark_output_import_state(
                job_id,
                surface="image",
                status="importing",
                message="Importing backend output into Neo_Data.",
                outputs=provider_outputs,
                increment_attempts=True,
            )
        except Exception:
            pass
        persisted_result = persist_image_outputs(provider_outputs=provider_outputs, job_context=context)
        record = persisted_result.record
        try:
            memory_result = get_memory_service().record_image_output_workflow(record)
            event_id = ((memory_result or {}).get("event") or {}).get("event_id", "")
            if event_id:
                record.setdefault("workflow_memory", {})["event_id"] = event_id
                persisted_result.record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            # Memory must never block Image output persistence.
            pass
        persisted = {
            "ok": persisted_result.ok,
            "result_id": persisted_result.result_id,
            "record": record,
            "record_path": str(persisted_result.record_path),
            "files": persisted_result.files,
            "errors": persisted_result.errors,
        }
        # Successful imports are cacheable. Failed/empty imports are not cached so
        # the next poll/recovery attempt can retry instead of freezing a bad state.
        if persisted_result.ok or persisted_result.files:
            PERSISTED_IMAGE_RESULTS[job_id] = persisted
        else:
            PERSISTED_IMAGE_RESULTS.pop(job_id, None)
        try:
            if persisted_result.ok:
                registry.mark_output_import_state(
                    job_id,
                    surface="image",
                    status="imported",
                    message="Image output imported into Neo_Data.",
                    result_id=persisted_result.result_id,
                    outputs=persisted_result.files,
                    recoverable=False,
                )
            else:
                failed_status = normalize_import_failure_status(provider_outputs, persisted_result.files)
                registry.mark_output_import_state(
                    job_id,
                    surface="image",
                    status="saved_in_comfy_only" if failed_status == "import_failed" else failed_status,
                    message="Comfy finished, but Neo could not import all output files into Neo_Data.",
                    result_id=persisted_result.result_id,
                    outputs=provider_outputs,
                    errors=persisted_result.errors,
                    recoverable=True,
                    recovery_endpoint=f"/api/image/jobs/{profile_id}/{job_id}/recover" if profile_id and job_id else "",
                )
        except Exception:
            pass
    output["neo_persisted"] = persisted
    persisted_files = persisted.get("record", {}).get("outputs", {}).get("files", []) if isinstance(persisted.get("record"), dict) else []
    if persisted.get("ok") or persisted_files:
        output["outputs"] = persisted_files or persisted.get("files") or output.get("outputs") or []
        if persisted.get("ok"):
            output["status"] = "completed"
        elif output.get("status") == "completed":
            output["status"] = "completed_with_warnings"
    else:
        failed_status = normalize_import_failure_status(provider_outputs, persisted.get("files") or [])
        output["status"] = "import_failed" if failed_status == "import_failed" else failed_status
        output["outputs"] = provider_outputs
        output["neo_recovery"] = image_output_recovery_payload(
            job_id=job_id,
            profile_id=profile_id,
            provider_outputs=provider_outputs,
            persisted=persisted,
            status="saved_in_comfy_only" if failed_status == "import_failed" else failed_status,
            errors=persisted.get("errors") or [],
        )
        output["message"] = output.get("message") or output["neo_recovery"].get("label") or "Output import needs recovery."
    return output



@app.get("/api/model-families")
def model_families(surface: str | None = None) -> dict:
    return get_model_family_payload(surface=surface)


@app.get("/api/model-families/parameter-profiles")
def model_family_parameter_profiles() -> dict:
    return {"parameter_profiles": [model_to_dict(profile) for profile in get_parameter_profiles()]}


@app.get("/api/model-families/{family_id}")
def model_family(family_id: str) -> dict:
    match = get_family(family_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown model family: {family_id}")
    return model_to_dict(match)


@app.post("/api/model-families/check-compatibility")
def model_family_check_compatibility(payload: dict) -> dict:
    return check_model_family_compatibility(payload)


@app.post("/api/model-families/check-readiness")
def model_family_check_readiness(payload: dict) -> dict:
    return validate_readiness(payload)




@app.get("/api/image/output-paths")
def image_output_paths(create: bool = False) -> dict:
    """Return canonical Neo_Data output folders for Image results."""
    return output_path_payload(create=create)




@app.post("/api/video/source-video/upload")
async def video_source_video_upload(file: UploadFile = File(...), lane: str = Form("finish")) -> dict:
    """Store a browsed/dropped source video as a Neo-owned Video ledger result."""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Video source upload requires a file.")
    original_name = Path(file.filename).name
    suffix = Path(original_name).suffix.lower()
    allowed = {".webm", ".mp4", ".mov", ".mkv", ".gif"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="Video source upload requires MP4, WEBM, MOV, MKV, or GIF.")
    source_dir = get_video_output_paths("source", create=True).output_dir
    clean_stem = sanitize_video_path_part(Path(original_name).stem, "source_video")
    lane_key = sanitize_video_path_part(lane, "finish")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target = source_dir / f"{lane_key}_{clean_stem}_{stamp}{suffix}"
    size = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Video source upload could not be saved: {exc}")
    if size <= 0:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Video source upload is empty.")
    result = register_video_source_upload(target, original_filename=original_name, lane=lane_key, content_type=file.content_type or "")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Video source upload could not be registered.")
    return {
        **result,
        "upload_schema_version": "neo.video.finish.source_video_upload.v25_9_19_phase_10c",
        "filename": target.name,
        "original_filename": original_name,
        "size_bytes": size,
        "lane": lane_key,
    }


@app.get("/api/video/results")
def video_results(limit: int = 50, category: str | None = None) -> dict:
    """List Phase V7 Video output ledger records."""
    return list_video_output_records(limit=limit, category=category)


@app.get("/api/video/results/{result_id}")
def video_result_detail(result_id: str) -> dict:
    """Load one Phase V7/V22 Video output record by result id."""
    return load_video_output_record(result_id)


@app.get("/api/video/results/{result_id}/replay-metadata")
def video_result_replay_metadata(result_id: str) -> dict:
    """Return/upgrade the canonical V22 replay metadata for a Video result."""
    return video_replay_metadata_payload(result_id)


@app.post("/api/video/results/{result_id}/memory-export")
def video_result_memory_export(result_id: str) -> dict:
    """Export one Video result into the unified memory database."""
    return video_memory_export_payload(result_id=result_id)


@app.post("/api/video/memory-export")
def video_memory_export(payload: dict | None = None) -> dict:
    """Export recent Video result replay metadata into unified memory."""
    data = payload if isinstance(payload, dict) else {}
    result_id = str(data.get("result_id") or "").strip() or None
    limit = int(data.get("limit") or 50)
    return video_memory_export_payload(result_id=result_id, limit=limit)




@app.get("/api/video/results/{result_id}/project-handoff")
def video_result_project_handoff_preview(result_id: str, project_id: str = "") -> dict:
    return build_video_project_handoff_payload(result_id, {"project_id": project_id} if project_id else {})


@app.post("/api/video/results/{result_id}/project-handoff")
def video_result_project_handoff(result_id: str, payload: dict | None = None) -> dict:
    try:
        result = send_video_result_to_project_payload(result_id, payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
            get_memory_service().record_event({
                "namespace": "video",
                "surface": "video",
                "event_type": "video.project_handoff.created",
                "title": f"Video result sent to project: {result_id}",
                "summary": "Video result reference/context was handed to Project Workspace.",
                "payload": result,
                "tags": ["video", "project_handoff", "asset_tray"],
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/video/project-asset-tray")
def video_project_asset_tray(project_id: str = "", limit: int = 30) -> dict:
    return video_project_asset_tray_payload(project_id, limit=limit)

@app.post("/api/video/results/{result_id}/refresh")
def video_result_refresh(result_id: str, payload: dict | None = None) -> dict:
    """Best-effort refresh of a queued ComfyUI video result."""
    data = payload if isinstance(payload, dict) else {}
    return refresh_video_result_from_comfy(result_id, profile_id=data.get("profile_id"), timeout=float(data.get("timeout", 3.0) or 3.0))


@app.get("/api/video/output-file")
def video_output_file(result_id: str, file_id: str) -> FileResponse:
    """Serve a Neo-owned video output/preview file for the Video preview panel."""
    path = video_output_file_path(result_id, file_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Video output file not found.")
    return FileResponse(path)


@app.get("/api/video/output-paths")
def video_output_paths(create: bool = False) -> dict:
    """Return canonical Neo_Data output folders for Video results."""
    return video_output_path_payload(create=create)


@app.get("/api/video/route-matrix")
def video_route_matrix() -> dict:
    """Return the Video model-family/loader/generation route matrix."""
    return video_route_matrix_payload()


@app.get("/api/video/route-validation")
def video_route_validation(family: str | None = None, loader: str | None = None, generation_type: str | None = None, mode: str | None = None) -> dict:
    """Validate a requested Video family + loader + generation-type route."""
    return video_route_validation_payload(family=family, loader=loader, generation_type=generation_type or mode)




@app.get("/api/video/parameter-profile")
def video_parameter_profile(family: str | None = None, loader: str | None = None, generation_type: str | None = None, mode: str | None = None, vram_profile: str | None = None) -> dict:
    """Return route-derived Video parameters constrained by the selected VRAM profile."""
    return video_parameter_profile_payload(family=family, loader=loader, generation_type=generation_type or mode, vram_profile=vram_profile)


@app.get("/api/video/backend-probe")
def video_backend_probe(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    mode: str | None = None,
    profile_id: str | None = None,
    timeout: float = 2.0,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    rapid_aio_model: str | None = None,
    rapid_aio_text_encoder: str | None = None,
    rapid_aio_vae: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    enable_lightx2v: bool = False,
    enable_video_lora: bool = False,
    video_lora_mode: str | None = None,
    video_lora_model: str | None = None,
    video_lora_target: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
    performance_profile: str | None = None,
    enable_sage_attention: bool = False,
    sage_attention_mode: str | None = None,
    enable_teacache: bool = False,
    teacache_profile: str | None = None,
    teacache_target: str | None = None,
    enable_cpu_offload: bool = False,
    enable_vae_offload: bool = False,
    enable_block_swap: bool = False,
    block_swap_target: str | None = None,
    block_swap_blocks: int | None = None,
    enable_torch_compile: bool = False,
) -> dict:
    """Probe ComfyUI / ComfyUI Portable readiness for the selected Video route without queueing jobs."""
    return video_backend_probe_payload(
        family=family,
        loader=loader,
        generation_type=generation_type or mode,
        profile_id=profile_id,
        timeout=timeout,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        rapid_aio_model=rapid_aio_model,
        rapid_aio_text_encoder=rapid_aio_text_encoder,
        rapid_aio_vae=rapid_aio_vae,
        clip_name=clip_name,
        vae_name=vae_name,
        enable_lightx2v=enable_lightx2v,
        enable_video_lora=enable_video_lora,
        video_lora_mode=video_lora_mode,
        video_lora_model=video_lora_model,
        video_lora_target=video_lora_target,
        high_noise_lora=high_noise_lora,
        low_noise_lora=low_noise_lora,
        performance_profile=performance_profile,
        enable_sage_attention=enable_sage_attention,
        sage_attention_mode=sage_attention_mode,
        enable_teacache=enable_teacache,
        teacache_profile=teacache_profile,
        teacache_target=teacache_target,
        enable_cpu_offload=enable_cpu_offload,
        enable_vae_offload=enable_vae_offload,
        enable_block_swap=enable_block_swap,
        block_swap_target=block_swap_target,
        block_swap_blocks=block_swap_blocks,
        enable_torch_compile=enable_torch_compile,
    )


@app.get("/api/video/model-discovery")
def video_model_discovery(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    mode: str | None = None,
    profile_id: str | None = None,
    timeout: float = 2.0,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    rapid_aio_model: str | None = None,
    rapid_aio_text_encoder: str | None = None,
    rapid_aio_vae: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
) -> dict:
    """Return Video model dropdown catalogs discovered from ComfyUI /object_info only."""
    probe = video_backend_probe_payload(
        family=family,
        loader=loader,
        generation_type=generation_type or mode,
        profile_id=profile_id,
        timeout=timeout,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        rapid_aio_model=rapid_aio_model,
        rapid_aio_text_encoder=rapid_aio_text_encoder,
        rapid_aio_vae=rapid_aio_vae,
        clip_name=clip_name,
        vae_name=vae_name,
        high_noise_lora=high_noise_lora,
        low_noise_lora=low_noise_lora,
    )
    discovery = probe.get("model_discovery") if isinstance(probe, dict) else None
    if isinstance(discovery, dict):
        return {**discovery, "backend": probe.get("backend", {}), "errors": discovery.get("errors", []) or probe.get("errors", [])}
    return {
        "schema_version": "neo.video.model_discovery.vg6",
        "phase": "V-G6",
        "source": "comfy_object_info",
        "filesystem_scan_used": False,
        "catalog_ready": False,
        "backend": probe.get("backend", {}) if isinstance(probe, dict) else {},
        "errors": probe.get("errors", ["Video model discovery did not return a catalog."]) if isinstance(probe, dict) else ["Video model discovery did not return a catalog."],
        "warnings": probe.get("warnings", []) if isinstance(probe, dict) else [],
        "catalogs": {},
        "options": {},
        "counts": {},
    }



@app.get("/api/video/performance-profile")
def video_performance_profile(family: str | None = None, loader: str | None = None, generation_type: str | None = None, mode: str | None = None, performance_profile: str | None = None, vram_profile: str | None = None, enable_sage_attention: bool = False, enable_teacache: bool = False, teacache_profile: str | None = None, teacache_target: str | None = None, enable_cpu_offload: bool = False, enable_vae_offload: bool = False, enable_block_swap: bool = False, block_swap_target: str | None = None, block_swap_blocks: int | None = None, enable_torch_compile: bool = False) -> dict:
    """Return the V-G13 shared Video Performance Adapter profile contract."""
    return video_performance_profile_payload(
        family=family,
        loader=loader,
        generation_type=generation_type or mode,
        performance_profile=performance_profile,
        vram_profile=vram_profile,
        enable_sage_attention=enable_sage_attention,
        enable_teacache=enable_teacache,
        teacache_profile=teacache_profile,
        teacache_target=teacache_target,
        enable_cpu_offload=enable_cpu_offload,
        enable_vae_offload=enable_vae_offload,
        enable_block_swap=enable_block_swap,
        block_swap_target=block_swap_target,
        block_swap_blocks=block_swap_blocks,
        enable_torch_compile=enable_torch_compile,
    )


@app.post("/api/video/performance-preflight")
def video_performance_preflight(payload: dict | None = None) -> dict:
    """Preflight optimizer intent through the V-G13 Video Performance Adapter Layer without queueing."""
    return video_performance_preflight_payload(payload if isinstance(payload, dict) else {})

@app.get("/api/video/external-node-manager")
def video_external_node_manager(profile_id: str | None = None, timeout: float = 2.0) -> dict:
    """Detect optional ComfyUI external video node packs and classify their guarded lanes."""
    return video_external_node_manager_payload(profile_id=profile_id, timeout=timeout)

@app.get("/api/video/vram-engine")
def video_vram_engine(family: str | None = None, loader: str | None = None, generation_type: str | None = None, mode: str | None = None, vram_profile: str | None = None, width: int | None = None, height: int | None = None, frames: int | None = None, fps: float | None = None, steps: int | None = None, guidance: float | None = None) -> dict:
    """Return the Phase V10 Video VRAM profile engine preflight for the selected route."""
    return video_vram_engine_payload(family=family, loader=loader, generation_type=generation_type, mode=mode, vram_profile=vram_profile, width=width, height=height, frames=frames, fps=fps, steps=steps, guidance=guidance)


@app.post("/api/video/vram-preflight")
def video_vram_preflight(payload: dict | None = None) -> dict:
    """Preflight arbitrary Video compiler payloads through the Phase V10 VRAM engine."""
    return video_vram_preflight_payload(payload if isinstance(payload, dict) else {})


@app.post("/api/video/runtime-preflight")
def video_runtime_preflight(payload: dict | None = None) -> dict:
    """Run V-G7 queue-safe runtime preflight before Video /prompt queueing."""
    return video_runtime_preflight_payload(payload if isinstance(payload, dict) else {})


@app.post("/api/video/wan22-gguf-first-test-preset")
def video_wan22_gguf_first_test_preset(payload: dict | None = None) -> dict:
    """Return the V-G7 safe first local test payload for WAN 2.2 GGUF Img2Vid."""
    return wan22_gguf_first_test_preset_payload(payload if isinstance(payload, dict) else {})


@app.post("/api/video/compile/wan22-txt2vid")
def video_compile_wan22_txt2vid(payload: dict | None = None) -> dict:
    """Compile the WAN 2.2 Txt2Vid ComfyUI workflow without queueing it."""
    data = payload if isinstance(payload, dict) else {}
    loader = str(data.get("loader", "unet") or "unet").lower().replace("-", "_")
    if loader in {"rapid_aio_gguf", "gguf_rapid_aio", "rapid_gguf", "wan_rapid_aio", "wan22_rapid_aio_gguf"}:
        return _run_video_runtime_call("wan22-rapid-aio-gguf-production", "video.compile", data, video_wan22_rapid_aio_gguf_compile_payload)
    return _run_video_runtime_call("wan22-txt2vid", "video.compile", data, video_wan22_txt2vid_compile_payload)


@app.post("/api/video/compile/wan22-img2vid")
def video_compile_wan22_img2vid(payload: dict | None = None) -> dict:
    """Compile a WAN 2.2 Img2Vid ComfyUI workflow without queueing it."""
    data = payload if isinstance(payload, dict) else {}
    loader = str(data.get("loader", "unet") or "unet").lower().replace("-", "_")
    if loader == "gguf":
        return _run_video_runtime_call("wan22-gguf-i2v14", "video.compile", data, video_wan22_gguf_i2v14_compile_payload)
    if loader in {"rapid_aio_gguf", "gguf_rapid_aio", "rapid_gguf", "wan_rapid_aio", "wan22_rapid_aio_gguf"}:
        return _run_video_runtime_call("wan22-rapid-aio-gguf-production", "video.compile", data, video_wan22_rapid_aio_gguf_compile_payload)
    return _run_video_runtime_call("wan22-img2vid", "video.compile", data, video_wan22_img2vid_compile_payload)




@app.post("/api/video/compile/ltx23-txt2vid")
def video_compile_ltx23_txt2vid(payload: dict | None = None) -> dict:
    """Compile the Phase V8 LTX 2.3 Txt2Vid ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-txt2vid", "video.compile", payload, video_ltx23_txt2vid_compile_payload)


@app.post("/api/video/compile/ltx23-img2vid")
def video_compile_ltx23_img2vid(payload: dict | None = None) -> dict:
    """Compile the Phase V9 LTX 2.3 Img2Vid ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-img2vid", "video.compile", payload, video_ltx23_img2vid_compile_payload)



@app.post("/api/video/compile/ltx23-first-last-frame")
def video_compile_ltx23_first_last_frame(payload: dict | None = None) -> dict:
    """Compile the Phase V15 LTX 2.3 First/Last Frame ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-first-last-frame", "video.compile", payload, video_ltx23_first_last_frame_compile_payload)


@app.post("/api/video/compile/ltx23-multiscene")
def video_compile_ltx23_multiscene(payload: dict | None = None) -> dict:
    """Compile the Phase V16 LTX 2.3 Multi-Image / MultiScene ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-multiscene", "video.compile", payload, video_ltx23_multiscene_compile_payload)


@app.post("/api/video/compile/ltx23-extend")
def video_compile_ltx23_extend(payload: dict | None = None) -> dict:
    """Compile the Phase V17 LTX 2.3 Video Extend ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-extend", "video.compile", payload, video_ltx23_extend_compile_payload)


@app.post("/api/video/compile/ltx23-vid2vid")
def video_compile_ltx23_vid2vid(payload: dict | None = None) -> dict:
    """Compile the Phase V18 LTX 2.3 Video-to-Video ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-vid2vid", "video.compile", payload, video_ltx23_vid2vid_compile_payload)


@app.post("/api/video/compile/ltx23-depth-motion")
def video_compile_ltx23_depth_motion(payload: dict | None = None) -> dict:
    """Compile the Phase V19 LTX 2.3 Depth / Motion Control ComfyUI workflow without queueing it."""
    return _run_video_runtime_call("ltx23-depth-motion", "video.compile", payload, video_ltx23_depth_motion_compile_payload)


@app.post("/api/video/compile/ltx23-schedule")
def video_compile_ltx23_schedule(payload: dict | None = None) -> dict:
    """Compile the Phase V20 LTX 2.3 Prompt/Motion Schedule workflow without queueing it."""
    return _run_video_runtime_call("ltx23-schedule", "video.compile", payload, video_ltx23_schedule_compile_payload)


@app.post("/api/video/compile/ltx23-audio-video")
def video_compile_ltx23_audio_video(payload: dict | None = None) -> dict:
    """Compile the Phase V21 LTX 2.3 Audio-Video workflow without queueing it."""
    return _run_video_runtime_call("ltx23-audio-video", "video.compile", payload, video_ltx23_audio_video_compile_payload)


@app.post("/api/video/compile/interpolate")
def video_compile_interpolate(payload: dict | None = None) -> dict:
    """Compile a non-destructive V12 video interpolation Finish workflow."""
    data = dict(payload or {})
    data["dry_run"] = True
    return _run_video_runtime_call("interpolate", "video.compile", data, video_interpolation_compile_payload)


@app.post("/api/video/finish/interpolate")
def video_finish_interpolate(payload: dict | None = None) -> dict:
    """Compile or queue a V12 video interpolation Finish workflow."""
    return _run_video_runtime_call("interpolate", "video.finish", payload, video_interpolation_generate_payload)

@app.post("/api/video/compile/upscale")
def video_compile_upscale(payload: dict | None = None) -> dict:
    """Compile a non-destructive V13 video upscale Finish workflow."""
    data = dict(payload or {})
    data["dry_run"] = True
    return _run_video_runtime_call("upscale", "video.compile", data, video_upscale_compile_payload)


@app.post("/api/video/finish/upscale")
def video_finish_upscale(payload: dict | None = None) -> dict:
    """Compile or queue a V13 video upscale Finish workflow."""
    return _run_video_runtime_call("upscale", "video.finish", payload, video_upscale_generate_payload)


@app.post("/api/video/compile/repair")
def video_compile_repair(payload: dict | None = None) -> dict:
    """Compile a non-destructive V14 video repair/cleanup Finish workflow."""
    data = dict(payload or {})
    data["dry_run"] = True
    return _run_video_runtime_call("repair", "video.compile", data, video_repair_compile_payload)


@app.post("/api/video/finish/repair")
def video_finish_repair(payload: dict | None = None) -> dict:
    """Compile or queue a V14 video repair/cleanup Finish workflow."""
    return _run_video_runtime_call("repair", "video.finish", payload, video_repair_generate_payload)

def _xai_grok_video_generate(data: dict) -> dict:
    profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or "").strip()
    if not profile_id:
        profile_id = _default_backend_profile_id_for_surface("video")
    if not profile_id:
        raise HTTPException(status_code=400, detail="A Video backend profile is required.")
    provider, profile = _profile_bound_provider(profile_id)
    if str(profile.get("surface") or "").strip().lower() != "video":
        raise HTTPException(status_code=400, detail=f"Backend profile '{profile_id}' is not a Video profile.")
    if str(profile.get("provider_id") or "").strip() != "xai_grok":
        raise HTTPException(status_code=400, detail=f"Backend profile '{profile_id}' is not an xAI Grok Video profile.")

    generation_type = str(data.get("generation_type") or data.get("mode") or "txt2vid").strip().lower().replace("-", "_")
    mode = "img2vid" if generation_type in {"img2vid", "image_to_video", "i2v"} else "txt2vid"
    source_image = str(data.get("source_image") or data.get("source_image_path") or "").strip()
    params = {
        "profile_id": profile_id,
        "backend_profile_id": profile_id,
        "model": str(data.get("model") or data.get("model_name") or "").strip(),
        "duration_seconds": data.get("duration_seconds", data.get("duration", 4)),
        "aspect_ratio": str(data.get("aspect_ratio") or "16:9").strip(),
        "resolution": str(data.get("resolution") or "720p").strip(),
        "source_image": source_image,
        "source_image_path": source_image,
        "source_image_name": str(data.get("source_image_name") or "").strip(),
    }
    job = NeoJob(
        job_id=str(data.get("job_id") or "").strip() or None,
        surface="video",
        subtab=mode,
        mode=mode,
        provider_id="xai_grok",
        family="grok_imagine",
        loader="api_model",
        model=params["model"] or None,
        prompt=str(data.get("prompt") or data.get("positive_prompt") or "").strip(),
        negative_prompt=None,
        params=params,
        extensions={},
    )
    result = provider.run_job(job)
    output = model_to_dict(result)
    runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
    persisted = runtime.get("neo_persisted") if isinstance(runtime.get("neo_persisted"), dict) else {}
    output.update({
        "ok": output.get("status") not in {"failed", "cancelled"},
        "queued": output.get("status") in {"queued", "running"},
        "profile_id": profile_id,
        "provider_id": "xai_grok",
        "route_id": f"xai_grok.api_model.{mode}",
        "family": "grok_imagine",
        "loader": "api_model",
        "generation_type": mode,
        "result_id": runtime.get("result_id") or persisted.get("result_id") or "",
        "neo_persisted": persisted,
        "parameters": runtime.get("actual_params") if isinstance(runtime.get("actual_params"), dict) else params,
        "backend": {
            "profile": {"profile_id": profile_id, "provider_id": "xai_grok"},
            "profile_id": profile_id,
            "provider_id": "xai_grok",
            "base_url": str((profile.get("connection") or {}).get("base_url") or "https://api.x.ai/v1"),
        },
    })
    return output


@app.post("/api/video/generate")
def video_generate(payload: dict | None = None) -> dict:
    """Queue the active Video workflow through its selected backend profile."""
    data = payload if isinstance(payload, dict) else {}
    profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or "").strip()
    selected_profile = get_backend_profile(profile_id) if profile_id else None
    if selected_profile and str(selected_profile.get("provider_id") or "").strip() == "xai_grok":
        return _xai_grok_video_generate(data)
    family = str(data.get("family", "wan22") or "wan22").lower().replace("-", "_").replace(".", "")
    generation_type = str(data.get("generation_type", data.get("mode", "txt2vid")) or "txt2vid").lower().replace("-", "_")
    loader = str(data.get("loader", "unet") or "unet").lower().replace("-", "_")
    if loader in {"rapid_aio_gguf", "gguf_rapid_aio", "rapid_gguf", "wan_rapid_aio", "wan22_rapid_aio_gguf"}:
        return _run_video_runtime_call("wan22-rapid-aio-gguf-production", "video.queue", data, video_wan22_rapid_aio_gguf_generate_payload)
    if family in {"ltx23", "ltx", "ltx_23", "ltx2_3"}:
        if generation_type in {"first_last_frame", "first_last", "start_end", "start_end_frame"}:
            return _run_video_runtime_call("ltx23-first-last-frame", "video.queue", data, video_ltx23_first_last_frame_generate_payload)
        if generation_type in {"multiscene", "multi_scene", "multi_image", "multi_image_video"}:
            return _run_video_runtime_call("ltx23-multiscene", "video.queue", data, video_ltx23_multiscene_generate_payload)
        if generation_type in {"extend", "video_extend", "continue", "continue_video"}:
            return _run_video_runtime_call("ltx23-extend", "video.queue", data, video_ltx23_extend_generate_payload)
        if generation_type in {"vid2vid", "video_to_video", "v2v", "restyle_video"}:
            return _run_video_runtime_call("ltx23-vid2vid", "video.queue", data, video_ltx23_vid2vid_generate_payload)
        if generation_type in {"depth_motion", "depth_control", "motion_control", "control_video"}:
            return _run_video_runtime_call("ltx23-depth-motion", "video.queue", data, video_ltx23_depth_motion_generate_payload)
        if generation_type in {"prompt_schedule", "prompt_scheduling", "motion_schedule", "schedule", "scheduled"}:
            return _run_video_runtime_call("ltx23-schedule", "video.queue", data, video_ltx23_schedule_generate_payload)
        if generation_type in {"audio_video", "audio", "audio_visual", "audiovideo"}:
            return _run_video_runtime_call("ltx23-audio-video", "video.queue", data, video_ltx23_audio_video_generate_payload)
        if generation_type in {"img2vid", "image_to_video", "i2v"}:
            return _run_video_runtime_call("ltx23-img2vid", "video.queue", data, video_ltx23_img2vid_generate_payload)
        return _run_video_runtime_call("ltx23-txt2vid", "video.queue", data, video_ltx23_txt2vid_generate_payload)
    if generation_type in {"img2vid", "image_to_video", "i2v"}:
        if loader == "gguf":
            return _run_video_runtime_call("wan22-gguf-i2v14", "video.queue", data, video_wan22_gguf_i2v14_generate_payload)
        if loader in {"rapid_aio_gguf", "gguf_rapid_aio", "rapid_gguf", "wan_rapid_aio", "wan22_rapid_aio_gguf"}:
            return _run_video_runtime_call("wan22-rapid-aio-gguf-production", "video.queue", data, video_wan22_rapid_aio_gguf_generate_payload)
        return _run_video_runtime_call("wan22-img2vid", "video.queue", data, video_wan22_img2vid_generate_payload)
    return _run_video_runtime_call("wan22-txt2vid", "video.queue", data, video_wan22_txt2vid_generate_payload)


@app.get("/api/video/jobs/{profile_id}/{job_id}")
def video_job_status(profile_id: str, job_id: str) -> dict:
    """Poll a provider-owned Video job while preserving the existing Video workspace."""
    provider, profile = _profile_bound_provider(profile_id)
    if str(profile.get("surface") or "").strip().lower() != "video":
        raise HTTPException(status_code=400, detail=f"Backend profile '{profile_id}' is not a Video profile.")
    result = provider.poll_job(job_id)
    output = model_to_dict(result)
    runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
    persisted = runtime.get("neo_persisted") if isinstance(runtime.get("neo_persisted"), dict) else {}
    output.update({
        "ok": output.get("status") not in {"failed", "cancelled"},
        "queued": output.get("status") in {"queued", "running"},
        "profile_id": profile_id,
        "provider_id": str(profile.get("provider_id") or output.get("provider_id") or ""),
        "result_id": runtime.get("result_id") or persisted.get("result_id") or "",
        "neo_persisted": persisted,
        "capabilities": provider.feature_capability_payload(),
    })
    try:
        registry_record = get_generation_job_registry(ROOT_DIR).get(job_id, surface="video") or get_generation_job_registry(ROOT_DIR).get(job_id)
        if registry_record:
            output["job_registry"] = get_generation_job_registry(ROOT_DIR).summary(job_id, surface=registry_record.get("surface"))
    except Exception:
        pass
    return attach_progress_watchdog(output, surface="video", profile_id=profile_id, job_id=job_id)


@app.get("/api/image/replay-storage")
def image_replay_storage_get() -> dict:
    """Return IMG-R10 Image replay/latent storage usage and safe cleanup candidates."""
    return image_replay_storage_summary()


@app.post("/api/image/replay-storage/cleanup")
def image_replay_storage_cleanup(payload: dict | None = None) -> dict:
    """Run safe IMG-R10 replay storage cleanup actions."""
    return cleanup_image_replay_storage(payload if isinstance(payload, dict) else {})


@app.get("/api/image/output-settings")
def image_output_settings_get() -> dict:
    return {"ok": True, "settings": image_output_settings_response(load_image_output_settings())}


@app.post("/api/image/output-settings")
def image_output_settings_post(payload: dict) -> dict:
    return {"ok": True, "settings": image_output_settings_response(save_image_output_settings(payload if isinstance(payload, dict) else {}))}


@app.post("/api/image/output-settings/category")
def image_output_settings_category(payload: dict) -> dict:
    name = str((payload if isinstance(payload, dict) else {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Add a category name first.")
    settings = add_image_output_category(name, payload if isinstance(payload, dict) else {})
    return {"ok": True, "settings": image_output_settings_response(settings)}


@app.post("/api/image/open-output-folder")
def image_open_output_folder(payload: dict | None = None) -> dict:
    settings = load_image_output_settings()
    path = output_category_dir(settings).resolve()
    path.mkdir(parents=True, exist_ok=True)
    neo_root = (ROOT_DIR / "neo_data" / "outputs" / "image").resolve()
    if neo_root not in path.parents and path != neo_root:
        raise HTTPException(status_code=400, detail="Output folder is outside Neo_Data image outputs.")
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "path": str(path), "message": f"Could not open folder automatically: {exc}"}
    return {"ok": True, "path": str(path)}






@app.get("/api/image/output-file")
def image_output_file(result_id: str, file_id: str) -> FileResponse:
    """Serve a persisted image output from Neo_Data."""
    try:
        path = resolve_output_file(result_id, file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path)



@app.get("/api/image/results")
def image_results(category: str | None = None, limit: int = 50, sort: str = "newest") -> dict:
    """List persisted Image results from Neo_Data metadata sidecars."""
    return list_image_results(category=category, limit=limit, sort=sort)


@app.get("/api/image/results-integrity")
def image_results_integrity(selected_result_id: str | None = None, category: str | None = None) -> dict:
    """Validate saved Results references without forcing a reload loop."""
    return image_results_integrity_guard(selected_result_id=selected_result_id, category=category)


@app.get("/api/image/results/{result_id}")
def image_result_detail(result_id: str, category: str | None = None) -> dict:
    """Return a persisted Image result record plus its reuse payload."""
    try:
        return get_image_result(result_id, category=category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/image/result-metadata/{result_id}")
def image_result_metadata(result_id: str, category: str | None = None) -> dict:
    """Return the full sidecar metadata for one persisted Image result."""
    try:
        return get_image_result_metadata(result_id, category=category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/image/results/{result_id}/replay-validation")
def image_result_replay_validation(result_id: str, category: str | None = None) -> dict:
    """Validate a saved Image result against current backend profile state."""
    try:
        record = get_image_result_metadata(result_id, category=category)["metadata"]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _validate_image_result_replay_against_current_profile(record)


@app.get("/api/image/post-output-comfy-bridge/profiles")
def image_post_output_comfy_bridge_profiles() -> dict:
    """List local Image backend profiles that can receive cloud outputs for finish/refine passes."""
    payload = list_backend_profiles()
    profiles = payload.get("profiles") if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
    bridge_profiles = []
    for profile in profiles if isinstance(profiles, list) else []:
        if not isinstance(profile, dict):
            continue
        if profile.get("surface") != "image" or profile.get("enabled") is False:
            continue
        if str(profile.get("connection_type") or "") == "cloud_api":
            continue
        provider_id = str(profile.get("provider_id") or "")
        flags = profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}
        caps = profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}
        bridge_profiles.append({
            "profile_id": profile.get("profile_id") or "",
            "display_name": profile.get("display_name") or profile.get("profile_id") or "Image backend",
            "provider_id": provider_id,
            "connection_type": profile.get("connection_type") or "",
            "supports_adetailer": bool(caps.get("adetailer_inline", flags.get("supports_adetailer_inline", provider_id in {"comfyui", "comfyui_portable"}))),
            "supports_highres": bool(caps.get("highres_inline", flags.get("supports_highres_inline", provider_id in {"comfyui", "comfyui_portable"}))),
            "supports_upscale": True,
        })
    return {"ok": True, "profiles": bridge_profiles, "count": len(bridge_profiles)}


@app.post("/api/image/results/{result_id}/reuse")
def image_result_reuse(result_id: str, category: str | None = None) -> dict:
    """Build a core-safe reuse payload from a persisted Image result."""
    try:
        record = get_image_result_metadata(result_id, category=category)["metadata"]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build_image_result_reuse_payload(record)


@app.get("/api/image/results/{result_id}/delete-preview")
def image_result_delete_preview(result_id: str, category: str | None = None) -> dict:
    """Return a safe cascade-delete manifest before mutating Image output storage."""
    try:
        return build_image_result_delete_preview(result_id, category=category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/image/results/{result_id}")
def image_result_delete(result_id: str, category: str | None = None, cascade: str | None = None) -> dict:
    """Delete one Neo_Data Image result using the output-only/full cascade policy."""
    try:
        return delete_image_result(result_id, category=category, cascade=cascade)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/image/output-record-schema")
def image_output_record_schema() -> dict:
    """Return the core Image output metadata schema, including extension slots."""
    return output_record_schema_payload()

@app.get("/api/image/base")
def image_base() -> dict:
    return model_to_dict(get_image_surface_base_contract())




@app.get("/api/roleplay/base")
def roleplay_base() -> dict:
    contract = model_to_dict(get_roleplay_surface_base_contract())
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.base.loaded",
            "payload": {
                "schema_id": contract.get("schema_id"),
                "version": contract.get("version"),
                "tabs": [tab.get("tab_id") for tab in contract.get("tabs", [])],
                "status": contract.get("status"),
            },
        })
    except Exception:
        pass
    return contract


@app.get("/api/roleplay/foundation")
def roleplay_foundation() -> dict:
    foundation = roleplay_foundation_payload(write_manifest=True)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.foundation.checked",
            "payload": {
                "schema_id": foundation.get("schema_id"),
                "version": foundation.get("version"),
                "ready": foundation.get("ready"),
                "data_root": foundation.get("data_root"),
                "directory_count": len(foundation.get("directories", [])),
                "missing_directories": foundation.get("missing_directories", []),
            },
        })
    except Exception:
        pass
    return foundation






@app.get("/api/roleplay/builder-compiler-profiles/contract")
def roleplay_builder_compiler_profiles_contract(write_report: bool = True) -> dict:
    return compiler_profiles_contract_payload(write_report=write_report)


@app.get("/api/roleplay/builder-compiler-profiles/state")
def roleplay_builder_compiler_profiles_state() -> dict:
    return compiler_profiles_state_payload()


@app.post("/api/roleplay/builder-compiler-profiles/ensure")
def roleplay_builder_compiler_profiles_ensure(payload: dict | None = None) -> dict:
    return ensure_compiler_profiles_payload(payload or {})


@app.get("/api/roleplay/builder-templates/contract")
def roleplay_builder_templates_contract(write_report: bool = True) -> dict:
    return first_class_builder_templates_contract_payload(write_report=write_report)


@app.get("/api/roleplay/builder-templates/state")
def roleplay_builder_templates_state() -> dict:
    return first_class_builder_templates_state_payload()


@app.post("/api/roleplay/builder-templates/ensure")
def roleplay_builder_templates_ensure(payload: dict | None = None) -> dict:
    return ensure_first_class_builder_templates_payload(payload or {})

@app.get("/api/roleplay/forge/state")
def roleplay_forge_state(kind: str | None = None) -> dict:
    forge = forge_state_payload(kind)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.forge.state.loaded",
            "payload": {
                "schema_id": forge.get("schema_id"),
                "version": forge.get("version"),
                "active_kind": forge.get("active_kind"),
                "record_count": len(forge.get("records", [])),
            },
        })
    except Exception:
        pass
    return forge


@app.get("/api/roleplay/forge/records")
def roleplay_forge_records(kind: str | None = None) -> dict:
    records = [model_to_dict(record) for record in list_forge_records(kind)]
    return {
        "schema_id": "neo.roleplay.forge.records.v1",
        "surface_id": "roleplay",
        "tab_id": "forge",
        "status": "foundation",
        "kind": kind or "all",
        "records": records,
        "record_count": len(records),
    }


@app.get("/api/roleplay/forge/template")
def roleplay_forge_template(kind: str | None = None) -> dict:
    return forge_template_payload(kind)




@app.get("/api/roleplay/forge-import/contract")
def roleplay_forge_import_contract() -> dict:
    return forge_import_contract()


@app.post("/api/roleplay/forge-import/preview")
async def roleplay_forge_import_preview(
    file: UploadFile = File(...),
    scope_kind: str = Form(""),
    scope_id: str = Form(""),
    scope_mode: str = Form("apply_active_scope_where_empty"),
    conflict_mode: str = Form("skip_existing"),
) -> dict:
    data = await file.read()
    return preview_import_records(file.filename or "upload", data, scope_kind=scope_kind, scope_id=scope_id, scope_mode=scope_mode, conflict_mode=conflict_mode)


@app.post("/api/roleplay/forge-import/import")
async def roleplay_forge_import_run(
    file: UploadFile = File(...),
    scope_kind: str = Form(""),
    scope_id: str = Form(""),
    scope_mode: str = Form("apply_active_scope_where_empty"),
    conflict_mode: str = Form("skip_existing"),
) -> dict:
    data = await file.read()
    result = forge_import_records_payload(file.filename or "upload", data, scope_kind=scope_kind, scope_id=scope_id, scope_mode=scope_mode, conflict_mode=conflict_mode)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.forge.import.completed",
            "payload": {
                "filename": result.get("filename"),
                "imported_count": result.get("imported_count"),
                "error_count": result.get("error_count"),
            },
        })
    except Exception:
        pass
    return result

@app.post("/api/roleplay/forge/save")
def roleplay_forge_save(payload: dict) -> dict:
    result = save_forge_record_payload(payload)
    try:
        record = result.get("record", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.forge.record.saved",
            "payload": {
                "record_id": record.get("record_id"),
                "kind": record.get("kind"),
                "title": record.get("title"),
                "storage_path": record.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result



@app.post("/api/roleplay/forge/delete")
def roleplay_forge_delete(payload: dict) -> dict:
    result = delete_forge_record_payload(str(payload.get("record_id") or ""), payload.get("kind"))
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.forge.record.deleted",
            "payload": {
                "record_id": result.get("record_id"),
                "kind": result.get("kind"),
                "deleted": result.get("deleted"),
                "storage_path": result.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result



@app.get("/api/roleplay/text-backend/state")
def roleplay_text_backend_state(profile_id: str | None = None) -> dict:
    bridge = resolve_roleplay_text_backend(profile_id)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.text_backend.bridge.checked",
            "payload": {
                "schema_id": bridge.get("schema_id"),
                "ready": bridge.get("ready"),
                "active_profile_id": bridge.get("active_profile_id"),
                "selection_source": bridge.get("selection_source"),
                "profile_count": bridge.get("profile_count"),
            },
        })
    except Exception:
        pass
    return bridge


@app.get("/api/roleplay/scene/state")
def roleplay_scene_state(profile_id: str | None = None, scene_id: str = "default") -> dict:
    scene = scene_state_payload(profile_id, scene_id)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene.state.loaded",
            "payload": {
                "schema_id": scene.get("schema_id"),
                "version": scene.get("version"),
                "ready": scene.get("ready"),
                "scene_id": scene.get("scene_id"),
                "active_profile_id": (scene.get("text_backend") or {}).get("active_profile_id"),
                "turn_count": ((scene.get("chat") or {}).get("turn_count")),
            },
        })
    except Exception:
        pass
    return scene


@app.post("/api/roleplay/scene/setup/save")
def roleplay_scene_setup_save(payload: dict) -> dict:
    setup = save_scene_setup_payload(payload or {})
    scene = scene_state_payload(scene_id=setup.get("scene_id") or "default")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene.setup.saved",
            "payload": {
                "schema_id": scene.get("schema_id"),
                "scene_id": setup.get("scene_id"),
                "title": setup.get("title"),
                "runtime_bundle_id": setup.get("runtime_bundle_id"),
            },
        })
    except Exception:
        pass
    return {"ok": True, "setup": setup, "scene": scene}





@app.get("/api/roleplay/scene-memory-injection/contract")
def roleplay_scene_memory_injection_contract() -> dict:
    return scene_memory_injection_contract_payload(write_report=True)


@app.get("/api/roleplay/scene-memory-injection/state")
def roleplay_scene_memory_injection_state(scene_id: str = "default", scene_packet_id: str = "", scope_id: str = "") -> dict:
    return scene_memory_injection_state_payload(scene_id=scene_id or "default", scene_packet_id=scene_packet_id or "", scope_id=scope_id or "", write_report=True)


@app.post("/api/roleplay/scene-memory-injection/preview")
def roleplay_scene_memory_injection_preview(payload: dict) -> dict:
    payload = payload or {}
    scene_id = str(payload.get("scene_id") or "default")
    setup = load_scene_setup(scene_id)
    return build_scene_memory_injection_payload(setup=setup, request_payload=payload, runtime_bundle=None)


@app.get("/api/roleplay/turn-writeback/contract")
def roleplay_turn_writeback_contract() -> dict:
    return turn_writeback_contract_payload(write_report=True)


@app.get("/api/roleplay/turn-writeback/state")
def roleplay_turn_writeback_state(scene_id: str = "default", limit: int = 20) -> dict:
    return turn_writeback_state_payload(scene_id=scene_id or "default", limit=limit, write_report=True)


@app.post("/api/roleplay/turn-writeback/ensure-schema")
def roleplay_turn_writeback_ensure_schema() -> dict:
    return ensure_turn_writeback_schema()


@app.post("/api/roleplay/turn-writeback/apply")
def roleplay_turn_writeback_apply(payload: dict) -> dict:
    return apply_turn_writeback_payload(payload or {})




@app.post("/api/roleplay/scene/session/setup")
def roleplay_scene_session_setup(payload: dict) -> dict:
    result = update_scene_session_setup_payload(payload or {})
    scene = scene_state_payload(scene_id=result.get("scene_id") or str((payload or {}).get("scene_id") or "default"))
    return {"ok": bool(result.get("ok")), "status": result.get("status") or "unknown", "error": result.get("error") or "", "result": result, "session_setup": result.get("session_setup") or {}, "transcript": result.get("transcript") or {}, "scene": scene}


@app.post("/api/roleplay/scene/session/continue-new")
def roleplay_scene_session_continue_new(payload: dict) -> dict:
    result = start_scene_continuation_session_payload(payload or {})
    scene = scene_state_payload(scene_id=result.get("scene_id") or str((payload or {}).get("scene_id") or "default"))
    return {"ok": bool(result.get("ok")), "status": result.get("status") or "unknown", "error": result.get("error") or "", "result": result, "transcript": result.get("transcript") or {}, "scene": scene}

@app.post("/api/roleplay/scene/turn")
def roleplay_scene_turn(payload: dict) -> dict:
    """Execute one non-streamed Scene turn.

    This endpoint must never fail as a raw HTTP 500 for normal Scene runtime
    errors. Returning structured diagnostics keeps the UI from dropping the
    optimistic chat bubble and tells us whether failure happened before prompt
    build, before backend dispatch, or after generation.
    """
    scene_id = str((payload or {}).get("scene_id") or "default")
    try:
        _require_backend_connected_for_task(str((payload or {}).get("profile_id") or (payload or {}).get("backend_profile_id") or ""), surface="roleplay", operation="Roleplay scene turn")
        result = execute_scene_turn_payload(payload or {})
    except Exception as exc:
        tb = traceback.format_exc(limit=12)
        logging.exception("Roleplay scene turn failed before response")
        scene = scene_state_payload(scene_id=scene_id)
        return {
            "ok": False,
            "status": "scene_turn_exception",
            "message": str(exc) or exc.__class__.__name__,
            "error": str(exc) or exc.__class__.__name__,
            "traceback": tb,
            "phase": "execute_scene_turn_payload",
            "result": {
                "ok": False,
                "status": "scene_turn_exception",
                "scene_id": scene_id,
                "error": str(exc) or exc.__class__.__name__,
            },
            "transcript": {},
            "scene": scene,
        }
    try:
        scene = scene_state_payload(scene_id=result.get("scene_id") or scene_id)
    except Exception as exc:
        tb = traceback.format_exc(limit=12)
        logging.exception("Roleplay scene state refresh failed after turn")
        scene = {
            "schema_id": "neo.roleplay.scene.error_state.v1",
            "status": "state_refresh_error",
            "scene_id": result.get("scene_id") or scene_id,
            "error": str(exc) or exc.__class__.__name__,
            "setup": (result.get("prompt") or {}).get("setup") if isinstance(result.get("prompt"), dict) else {},
            "transcript": result.get("transcript") or {},
        }
        result.setdefault("post_generation_error", {"phase": "scene_state_payload", "error": str(exc), "traceback": tb})
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene.turn.executed",
            "payload": {
                "schema_id": result.get("schema_id"),
                "scene_id": result.get("scene_id"),
                "ok": result.get("ok"),
                "status": result.get("status"),
                "active_profile_id": (result.get("execution") or {}).get("active_profile_id"),
                "retrieval_trace_id": (result.get("prompt") or {}).get("retrieval_trace_id"),
                "turn_count": len(((result.get("transcript") or {}).get("turns") or [])),
            },
        })
    except Exception:
        pass
    return {"ok": bool(result.get("ok")), "status": result.get("status") or "unknown", "error": result.get("error") or "", "result": result, "transcript": result.get("transcript") or {}, "scene": scene}




@app.post("/api/roleplay/scene/turn-stream")
def roleplay_scene_turn_stream(payload: dict):
    def event_stream():
        final_event = {}
        try:
            _require_backend_connected_for_task(str((payload or {}).get("profile_id") or (payload or {}).get("backend_profile_id") or ""), surface="roleplay", operation="Roleplay scene stream")
            for event in stream_scene_turn_event_dicts(payload or {}):
                if event.get("type") == "done":
                    final_event.update(event)
                yield f"event: {event.get('type') or 'message'}\n"
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            error_event = {
                "type": "error",
                "schema_id": "neo.roleplay.scene.turn_stream.error.v1",
                "ok": False,
                "status": "server_stream_exception",
                "error": str(exc),
            }
            yield "event: error\n"
            yield "data: " + json.dumps(error_event, ensure_ascii=False) + "\n\n"
            done_event = {**error_event, "type": "done"}
            yield "event: done\n"
            yield "data: " + json.dumps(done_event, ensure_ascii=False) + "\n\n"
        finally:
            if final_event:
                try:
                    get_memory_service().record_event({
                        "namespace": "roleplay",
                        "surface": "roleplay",
                        "event_type": "roleplay.scene.turn.streamed",
                        "payload": {
                            "schema_id": final_event.get("schema_id"),
                            "scene_id": final_event.get("scene_id"),
                            "ok": final_event.get("ok"),
                            "status": final_event.get("status"),
                            "turn_count": len(((final_event.get("transcript") or {}).get("turns") or [])),
                            "active_profile_id": ((final_event.get("assistant_turn") or {}).get("backend_profile_id") or ""),
                        },
                    })
                except Exception:
                    pass
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/roleplay/scene/transcript/append-placeholder")
def roleplay_scene_transcript_append_placeholder(payload: dict) -> dict:
    transcript = append_scene_transcript_placeholder(payload or {})
    scene = scene_state_payload(scene_id=transcript.get("scene_id") or "default")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene.transcript.placeholder_added",
            "payload": {
                "schema_id": scene.get("schema_id"),
                "scene_id": transcript.get("scene_id"),
                "turn_count": len(transcript.get("turns") or []),
                "generation_enabled": False,
            },
        })
    except Exception:
        pass
    return {"ok": True, "transcript": transcript, "scene": scene}


@app.post("/api/roleplay/scene/transcript/reset")
def roleplay_scene_transcript_reset(payload: dict) -> dict:
    scene_id = str((payload or {}).get("scene_id") or "default")
    transcript = clear_scene_transcript_payload(scene_id)
    scene = scene_state_payload(scene_id=scene_id)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.scene.transcript.reset",
            "payload": {
                "schema_id": scene.get("schema_id"),
                "scene_id": scene_id,
                "generation_enabled": False,
            },
        })
    except Exception:
        pass
    return {"ok": True, "transcript": transcript, "scene": scene}


@app.get("/api/roleplay/stories/state")
def roleplay_stories_state(view: str | None = None) -> dict:
    stories = stories_state_payload(view)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.stories.state.loaded",
            "payload": {
                "schema_id": stories.get("schema_id"),
                "version": stories.get("version"),
                "active_view": stories.get("active_view"),
                "storyline_count": len(stories.get("storylines", [])),
            },
        })
    except Exception:
        pass
    return stories


@app.get("/api/roleplay/storylines")
def roleplay_storylines() -> dict:
    records = [model_to_dict(record) for record in list_storylines()]
    return {
        "schema_id": "neo.roleplay.stories.storylines.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "foundation",
        "storylines": records,
        "storyline_count": len(records),
    }


@app.post("/api/roleplay/storyline/create")
def roleplay_storyline_create(payload: dict) -> dict:
    result = create_storyline_payload(payload or {})
    try:
        storyline = result.get("storyline", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.storyline.created",
            "payload": {
                "storyline_id": storyline.get("storyline_id"),
                "title": storyline.get("title"),
                "status": storyline.get("status"),
                "storage_path": storyline.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result




@app.post("/api/roleplay/story-session/create")
def roleplay_story_session_create(payload: dict) -> dict:
    result = create_story_session_payload(payload or {})
    try:
        session = result.get("session", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_session.created",
            "payload": {
                "session_id": session.get("session_id"),
                "storyline_id": session.get("storyline_id"),
                "status": session.get("status"),
                "storage_path": session.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result




@app.post("/api/roleplay/story-checkpoint/create")
def roleplay_story_checkpoint_create(payload: dict) -> dict:
    result = create_story_checkpoint_payload(payload or {})
    try:
        checkpoint = result.get("checkpoint", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.created",
            "payload": {
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "storyline_id": checkpoint.get("storyline_id"),
                "session_id": checkpoint.get("session_id"),
                "status": checkpoint.get("status"),
                "storage_path": checkpoint.get("storage_path"),
                "memory_link": checkpoint.get("memory_link"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/story-sessions")
def roleplay_story_sessions() -> dict:
    sessions = list_story_sessions()
    return {
        "schema_id": "neo.roleplay.stories.sessions.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "foundation",
        "sessions": sessions,
        "session_count": len(sessions),
    }


@app.get("/api/roleplay/story-checkpoints")
def roleplay_story_checkpoints() -> dict:
    checkpoints = list_story_checkpoints()
    return {
        "schema_id": "neo.roleplay.stories.checkpoints.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "foundation",
        "checkpoints": checkpoints,
        "checkpoint_count": len(checkpoints),
    }


@app.get("/api/roleplay/story-branches")
def roleplay_story_branches() -> dict:
    branches = list_story_branches()
    return {
        "schema_id": "neo.roleplay.stories.branches.v1",
        "surface_id": "roleplay",
        "tab_id": "stories",
        "status": "active",
        "branches": branches,
        "branch_count": len(branches),
    }




@app.get("/api/roleplay/story-checkpoint-restore/contract")
def roleplay_story_checkpoint_restore_contract() -> dict:
    return story_checkpoint_restore_contract_payload(write_report=True)


@app.get("/api/roleplay/story-checkpoint-restore/state")
def roleplay_story_checkpoint_restore_state() -> dict:
    return story_checkpoint_restore_state_payload(write_report=True)


@app.post("/api/roleplay/story-checkpoint-restore/ensure-schema")
def roleplay_story_checkpoint_restore_ensure_schema() -> dict:
    return ensure_story_checkpoint_restore_schema()


@app.post("/api/roleplay/story-checkpoint/capture-active")
def roleplay_story_checkpoint_capture_active(payload: dict) -> dict:
    result = capture_active_scene_checkpoint_payload(payload or {})
    try:
        checkpoint = result.get("checkpoint", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.capture_active",
            "payload": {
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "storyline_id": checkpoint.get("storyline_id"),
                "session_id": checkpoint.get("session_id"),
                "scene_packet_id": checkpoint.get("scene_packet_id"),
                "snapshot": result.get("snapshot"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/story-checkpoint/restore-snapshot")
def roleplay_story_checkpoint_restore_snapshot(payload: dict) -> dict:
    result = restore_checkpoint_with_snapshot_payload(payload or {})
    scene = scene_state_payload(scene_id=result.get("scene_id") or "default")
    stories = stories_state_payload("workspace")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.restore_snapshot",
            "payload": {
                "restore_id": result.get("restore_id"),
                "checkpoint_id": result.get("checkpoint_id"),
                "session_id": result.get("session_id"),
                "scene_id": result.get("scene_id"),
                "packet_restore": result.get("packet_restore"),
            },
        })
    except Exception:
        pass
    return {"ok": True, "restore": result, "scene": scene, "stories": stories}


@app.post("/api/roleplay/story-checkpoint/branch")
def roleplay_story_checkpoint_branch(payload: dict) -> dict:
    result = branch_story_checkpoint(payload or {})
    try:
        branch = result.get("branch", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.branch.created",
            "payload": {
                "branch_id": branch.get("branch_id"),
                "source_checkpoint_id": branch.get("source_checkpoint_id"),
                "session_id": branch.get("session_id"),
                "status": branch.get("status"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/story-checkpoint/diff")
def roleplay_story_checkpoint_diff(payload: dict) -> dict:
    result = compare_story_checkpoints(payload or {})
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.diff.viewed",
            "payload": {
                "left_checkpoint_id": result.get("left_checkpoint_id"),
                "right_checkpoint_id": result.get("right_checkpoint_id"),
                "summary": result.get("summary"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/story-resume")
def roleplay_story_resume(payload: dict) -> dict:
    result = restore_story_session_to_scene_payload(payload or {})
    scene = scene_state_payload(scene_id=result.get("scene_id") or "default")
    stories = stories_state_payload("workspace")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story.resume.restored",
            "payload": {
                "schema_id": result.get("schema_id"),
                "scene_id": result.get("scene_id"),
                "checkpoint_id": result.get("checkpoint_id"),
                "session_id": result.get("session_id"),
                "restore_mode": result.get("restore_mode"),
                "turn_count": len(((result.get("transcript") or {}).get("turns") or [])),
            },
        })
    except Exception:
        pass
    return {"ok": True, "restore": result, "scene": scene, "stories": stories}


@app.post("/api/roleplay/story-checkpoint/restore")
def roleplay_story_checkpoint_restore(payload: dict) -> dict:
    result = restore_checkpoint_with_snapshot_payload(payload or {})
    scene = scene_state_payload(scene_id=result.get("scene_id") or "default")
    stories = stories_state_payload("workspace")
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.story_checkpoint.restored",
            "payload": {
                "schema_id": result.get("schema_id"),
                "scene_id": result.get("scene_id"),
                "checkpoint_id": result.get("checkpoint_id"),
                "session_id": result.get("session_id"),
                "restore_mode": result.get("restore_mode"),
            },
        })
    except Exception:
        pass
    return {"ok": True, "restore": result, "scene": scene, "stories": stories}


@app.get("/api/roleplay/runtime/bundles")
def roleplay_runtime_bundles() -> dict:
    runtime = runtime_state_payload()
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.runtime.state.loaded",
            "payload": {
                "bundle_count": runtime.get("bundle_count"),
                "latest_bundle_id": runtime.get("latest_bundle_id"),
            },
        })
    except Exception:
        pass
    return runtime


@app.get("/api/roleplay/runtime/bundle")
def roleplay_runtime_bundle(bundle_id: str) -> dict:
    return runtime_bundle_payload(bundle_id)


@app.post("/api/roleplay/runtime/compile-foundation")
def roleplay_runtime_compile_foundation(payload: dict | None = None) -> dict:
    result = runtime_compile_payload(payload or {})
    try:
        bundle = result.get("bundle", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.runtime.bundle.compiled",
            "payload": {
                "bundle_id": bundle.get("bundle_id"),
                "title": bundle.get("title"),
                "status": bundle.get("status"),
                "counts": bundle.get("counts"),
                "storage_path": bundle.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result

@app.get("/api/roleplay/studio/state")
def roleplay_studio_state(view: str | None = None, profile_id: str | None = None) -> dict:
    studio = studio_state_payload(view, profile_id)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.studio.state.loaded",
            "payload": {
                "schema_id": studio.get("schema_id"),
                "version": studio.get("version"),
                "active_view": studio.get("active_view"),
                "project_count": len(studio.get("projects", [])),
                "source_count": len(studio.get("sources", [])),
            },
        })
    except Exception:
        pass
    return studio


@app.get("/api/roleplay/studio/projects")
def roleplay_studio_projects() -> dict:
    projects = [model_to_dict(project) for project in list_studio_projects()]
    return {
        "schema_id": "neo.roleplay.studio.projects.v1",
        "surface_id": "roleplay",
        "tab_id": "studio",
        "status": "foundation",
        "projects": projects,
        "project_count": len(projects),
    }


@app.post("/api/roleplay/studio/project/create")
def roleplay_studio_project_create(payload: dict) -> dict:
    result = create_studio_project_payload(payload)
    try:
        project = result.get("project", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.studio.project.created",
            "payload": {
                "project_id": project.get("project_id"),
                "title": project.get("title"),
                "storage_path": project.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/studio/sources")
def roleplay_studio_sources(project_id: str | None = None) -> dict:
    sources = [model_to_dict(source) for source in list_studio_sources(project_id)]
    return {
        "schema_id": "neo.roleplay.studio.sources.v1",
        "surface_id": "roleplay",
        "tab_id": "studio",
        "status": "foundation",
        "project_id": project_id or "all",
        "sources": sources,
        "source_count": len(sources),
    }


@app.post("/api/roleplay/studio/source/save-text")
def roleplay_studio_source_save_text(payload: dict) -> dict:
    result = save_studio_source_payload(payload)
    try:
        source = result.get("source", {})
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.studio.source.saved",
            "payload": {
                "source_id": source.get("source_id"),
                "project_id": source.get("project_id"),
                "title": source.get("title"),
                "storage_path": source.get("storage_path"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/novel/state")
def roleplay_novel_state() -> dict:
    return novel_memory_state_payload()


@app.get("/api/roleplay/novel/memory-contract")
def roleplay_novel_memory_contract() -> dict:
    return novel_memory_contract_payload(write_report=True)


@app.post("/api/roleplay/novel/ensure-schema")
def roleplay_novel_ensure_schema() -> dict:
    return ensure_novel_memory_schema()


@app.post("/api/roleplay/novel/compile-source-document")
def roleplay_novel_compile_source_document(payload: dict) -> dict:
    result = compile_source_document_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.novel.source_document.compiled",
            "payload": {
                "source_id": result.get("source_id"),
                "project_id": result.get("project_id"),
                "chunk_count": result.get("chunk_count"),
                "memory_fragment_count": result.get("memory_fragment_count"),
                "canon_record_count": result.get("canon_record_count"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/novel/compile-all-source-documents")
def roleplay_novel_compile_all_source_documents(payload: dict) -> dict:
    result = compile_all_source_documents_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.novel.source_documents.compiled_all",
            "payload": {
                "project_id": result.get("project_id"),
                "source_count": result.get("source_count"),
                "chunk_count": result.get("chunk_count"),
                "memory_fragment_count": result.get("memory_fragment_count"),
                "canon_record_count": result.get("canon_record_count"),
                "error_count": result.get("error_count"),
            },
        })
    except Exception:
        pass
    return result


@app.get("/api/roleplay/source-breakdown/state")
def roleplay_source_breakdown_state() -> dict:
    return source_breakdown_state_payload()


@app.get("/api/roleplay/source-breakdown/contract")
def roleplay_source_breakdown_contract() -> dict:
    return source_breakdown_contract_payload(write_report=True)


@app.post("/api/roleplay/source-breakdown/ensure-schema")
def roleplay_source_breakdown_ensure_schema() -> dict:
    return ensure_source_breakdown_schema()


@app.post("/api/roleplay/source-breakdown/generate")
def roleplay_source_breakdown_generate(payload: dict) -> dict:
    result = generate_source_breakdown_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.novel.source_breakdown.generated",
            "payload": {
                "source_id": result.get("source_id"),
                "project_id": result.get("project_id"),
                "breakdown_id": result.get("breakdown_id"),
                "candidate_count": result.get("candidate_count"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/source-breakdown/generate-all")
def roleplay_source_breakdown_generate_all(payload: dict) -> dict:
    result = generate_all_source_breakdowns_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.novel.source_breakdown.generated_all",
            "payload": {
                "project_id": result.get("project_id"),
                "source_count": result.get("source_count"),
                "generated_count": result.get("generated_count"),
                "candidate_count": result.get("candidate_count"),
                "error_count": result.get("error_count"),
            },
        })
    except Exception:
        pass
    return result


@app.post("/api/roleplay/source-breakdown/list")
def roleplay_source_breakdown_list(payload: dict) -> dict:
    return list_source_breakdowns_payload(payload)


@app.post("/api/roleplay/source-breakdown/approve-canon")
def roleplay_source_breakdown_approve_canon(payload: dict) -> dict:
    result = approve_source_breakdown_payload(payload)
    try:
        get_memory_service().record_event({
            "namespace": "roleplay",
            "surface": "roleplay",
            "event_type": "roleplay.novel.source_breakdown.approved_canon",
            "payload": {
                "source_id": result.get("source_id"),
                "breakdown_id": result.get("breakdown_id"),
                "approved_count": result.get("approved_count"),
            },
        })
    except Exception:
        pass
    return result

@app.post("/api/image/job-draft")
def image_job_draft(payload: dict) -> dict:
    draft = model_to_dict(create_image_job_draft(payload))
    try:
        get_memory_service().record_event({
            "namespace": "image",
            "surface": "image",
            "event_type": "image.job.draft_created",
            "payload": {
                "subtab": draft.get("subtab"),
                "backend": draft.get("backend"),
                "family": draft.get("family"),
                "loader": draft.get("loader"),
                "status": draft.get("status"),
            },
        })
    except Exception:
        # Memory must never block shell rendering or draft creation.
        pass
    return draft






@app.get("/api/internet/access/status")
def internet_access_status() -> dict:
    return internet_access_status_payload()


@app.post("/api/internet/access/update")
def internet_access_update(payload: dict | None = None) -> dict:
    return update_internet_access_policy_payload(payload or {})


@app.post("/api/internet/access/plan")
def internet_access_plan(payload: dict | None = None) -> dict:
    return plan_internet_access_payload(payload or {})


@app.post("/api/internet/access/run")
def internet_access_run(payload: dict | None = None) -> dict:
    return run_internet_access_payload(payload or {})



@app.get("/api/voice/output-paths")
def voice_output_paths(create: bool = False) -> dict:
    """Return canonical Neo_Data output folders for Voice results."""
    return voice_output_path_payload(create=create)


@app.get("/api/voice/surface-contract")
def voice_surface_contract(create_paths: bool = False) -> dict:
    """Return the VO-V0 Voice surface contract and planned runtime ladder."""
    return voice_surface_contract_payload(create_paths=create_paths)


@app.get("/api/voice/health")
def voice_health(profile_id: str | None = None) -> dict:
    """Probe the configured Voice backend through the Neo adapter contract."""
    return voice_health_payload(profile_id=profile_id)


@app.get("/api/voice/capabilities")
def voice_capabilities(profile_id: str | None = None, family: str | None = None, runtime: str | None = None) -> dict:
    """Return capability-aware Voice controls for the selected backend/family."""
    return voice_capabilities_payload(profile_id=profile_id, family=family, runtime=runtime)


@app.get("/api/voice/capability-controls")
def voice_capability_controls(profile_id: str | None = None, family: str | None = None, runtime: str | None = None) -> dict:
    """Return the VO-V8 UI-only control manifest for capability-aware Voice panels."""
    return voice_capability_controls_payload(profile_id=profile_id, family=family, runtime=runtime)


@app.get("/api/voice/models")
def voice_models(profile_id: str | None = None, family: str | None = None) -> dict:
    """Return backend or contract fallback TTS model options."""
    return voice_models_payload(profile_id=profile_id, family=family)


@app.get("/api/voice/voices")
def voice_voices(profile_id: str | None = None, family: str | None = None) -> dict:
    """Return backend or contract fallback built-in voice options."""
    return voice_voices_payload(profile_id=profile_id, family=family)


@app.post("/api/voice/preview")
def voice_preview(payload: dict | None = None) -> dict:
    return preview_voice_payload(payload or {})


@app.post("/api/voice/render")
def voice_render(payload: dict | None = None) -> dict:
    return render_voice_payload(payload or {})


@app.post("/api/voice/clone")
def voice_clone(payload: dict | None = None) -> dict:
    return clone_voice_payload(payload or {})


@app.post("/api/voice/dialogue")
def voice_dialogue(payload: dict | None = None) -> dict:
    return dialogue_voice_payload(payload or {})


@app.post("/api/voice/reference/upload")
async def voice_reference_upload(file: UploadFile = File(...), transcript: str | None = Form(None), label: str | None = Form(None)) -> dict:
    try:
        record = await store_reference_upload(file, transcript=transcript, label=label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": record.get("status") or "staged", "reference": record}


@app.post("/api/voice/reference/analyze")
def voice_reference_analyze(payload: dict | None = None) -> dict:
    try:
        return analyze_reference_payload(payload or {})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/voice/reference/history")
def voice_reference_history(limit: int = 50) -> dict:
    return reference_history_payload(limit=limit)


@app.get("/api/voice/profiles")
def voice_profiles(limit: int = 200) -> dict:
    return voice_profiles_payload(limit=limit)


@app.post("/api/voice/profiles")
def voice_profile_create(payload: dict | None = None) -> dict:
    return create_voice_profile_payload(payload or {})


@app.get("/api/voice/profiles/{profile_id}")
def voice_profile_get(profile_id: str) -> dict:
    result = voice_profile_payload(profile_id)
    if result.get("ok") is False:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    return result


@app.patch("/api/voice/profiles/{profile_id}")
def voice_profile_update(profile_id: str, payload: dict | None = None) -> dict:
    result = update_voice_profile_payload(profile_id, payload or {})
    if result.get("ok") is False:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    return result


@app.delete("/api/voice/profiles/{profile_id}")
def voice_profile_delete(profile_id: str) -> dict:
    result = delete_voice_profile_payload(profile_id)
    if result.get("ok") is False:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    return result


@app.get("/api/voice/jobs/{job_id}")
def voice_job(job_id: str) -> dict:
    return voice_job_payload(job_id)


@app.get("/api/voice/jobs/{job_id}/replay")
def voice_job_replay(job_id: str) -> dict:
    return voice_job_replay_payload(job_id)


@app.get("/api/voice/replays")
def voice_replays(limit: int = 50) -> dict:
    return voice_replays_payload(limit=limit)


@app.get("/api/voice/memory-events")
def voice_memory_events(limit: int = 50, job_type: str | None = None) -> dict:
    return voice_memory_exports_payload(limit=limit, job_type=job_type)


@app.get("/api/voice/queue")
def voice_queue(limit: int = 50) -> dict:
    return voice_queue_payload(limit=limit)

@app.get("/api/voice/jobs/{job_id}/project-handoff")
def voice_job_project_handoff_preview(job_id: str, project_id: str = "") -> dict:
    return build_voice_project_handoff_payload(job_id, {"project_id": project_id} if project_id else {})


@app.post("/api/voice/jobs/{job_id}/project-handoff")
def voice_job_project_handoff(job_id: str, payload: dict | None = None) -> dict:
    try:
        result = send_voice_job_to_project_payload(job_id, payload or {})
        try:
            get_memory_service().index_source("project_workspace", force=True, limit=700)
            get_memory_service().record_event({"namespace": "voice", "surface": "voice", "event_type": "voice.project_handoff.created", "title": f"Voice output sent to project: {job_id}", "summary": "Voice output reference/context was handed to Project Workspace.", "payload": result, "tags": ["voice", "project_handoff", "asset_tray"]})
        except Exception:
            pass
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/voice/project-asset-tray")
def voice_project_asset_tray(project_id: str = "", limit: int = 30) -> dict:
    return voice_project_asset_tray_payload(project_id, limit=limit)



@app.post("/api/voice/jobs/{job_id}/cancel")
def voice_job_cancel(job_id: str) -> dict:
    return cancel_voice_job_payload(job_id)


@app.post("/api/voice/jobs/{job_id}/retry")
def voice_job_retry(job_id: str) -> dict:
    return retry_voice_job_payload(job_id)


@app.post("/api/voice/jobs/{job_id}/retry_chunk")
def voice_job_retry_chunk(job_id: str, payload: dict | None = None) -> dict:
    data = payload or {}
    return retry_voice_chunk_payload(job_id, str(data.get("chunk_id") or ""))


@app.post("/api/voice/jobs/{job_id}/export")
def voice_job_export(job_id: str, payload: dict | None = None) -> dict:
    return export_voice_job_payload(job_id, payload or {})


@app.get("/api/voice/jobs/{job_id}/reuse-settings")
def voice_job_reuse_settings(job_id: str) -> dict:
    return reuse_voice_job_settings_payload(job_id)


@app.get("/api/voice/jobs/{job_id}/open-folder")
def voice_job_open_folder(job_id: str) -> dict:
    return open_voice_job_folder_payload(job_id)


@app.delete("/api/voice/jobs/{job_id}")
def voice_job_delete(job_id: str, delete_files: bool = False) -> dict:
    return delete_voice_job_payload(job_id, delete_files=delete_files)


@app.get("/api/voice/history")
def voice_history(limit: int = 50, job_type: str | None = None, status: str | None = None) -> dict:
    return voice_history_payload(limit=limit, job_type=job_type, status=status)


@app.get("/api/voice/exports")
def voice_exports(limit: int = 50) -> dict:
    return voice_exports_payload(limit=limit)






@app.post("/api/voice/finish")
def voice_finish(payload: dict | None = None) -> dict:
    return finish_voice_output_payload(payload or {})


@app.post("/api/voice/finish/split")
def voice_finish_split(payload: dict | None = None) -> dict:
    return split_voice_output_payload(payload or {})


@app.post("/api/voice/finish/merge")
def voice_finish_merge(payload: dict | None = None) -> dict:
    return merge_voice_outputs_payload(payload or {})


@app.get("/api/voice/finish/history")
def voice_finish_history(limit: int = 50, status: str | None = None) -> dict:
    return voice_finish_history_payload(limit=limit, status=status)


@app.post("/api/voice/batch/import")
def voice_batch_import(payload: dict | None = None) -> dict:
    return import_voice_batch_payload(payload or {})


@app.get("/api/voice/batch/history")
def voice_batch_history(limit: int = 50, status: str | None = None) -> dict:
    return voice_batch_history_payload(limit=limit, status=status)


@app.get("/api/voice/batch/{batch_id}")
def voice_batch_get(batch_id: str) -> dict:
    return voice_batch_payload(batch_id)


@app.post("/api/voice/batch/{batch_id}/render")
def voice_batch_render(batch_id: str, payload: dict | None = None) -> dict:
    return render_voice_batch_payload(batch_id, payload or {})


@app.post("/api/voice/batch/{batch_id}/retry-item")
def voice_batch_retry_item(batch_id: str, payload: dict | None = None) -> dict:
    data = payload or {}
    return retry_voice_batch_item_payload(batch_id, str(data.get("item_id") or ""), data)


@app.get("/api/voice/output-file")
def voice_output_file(path: str) -> FileResponse:
    try:
        resolved = resolve_voice_output_file(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Voice output file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(resolved))

@app.get("/api/voice/input/status")
def voice_input_status() -> dict:
    return voice_input_status_payload()


@app.post("/api/voice/input/prepare")
def voice_input_prepare(payload: dict | None = None) -> dict:
    return prepare_voice_input_payload(payload or {})


@app.post("/api/voice/input/operator-run")
def voice_input_operator_run(payload: dict | None = None) -> dict:
    return run_voice_input_operator_payload(payload or {})


@app.post("/api/voice/input/transcribe")
async def voice_input_transcribe(audio_file: UploadFile = File(...), language: str | None = None, model_path: str | None = None, run_operator: bool = False, execute_confirmed: bool = False) -> dict:
    return await transcribe_uploaded_audio_payload(audio_file, language=language, model_path=model_path, run_operator=run_operator, execute_confirmed=execute_confirmed)


@app.get("/api/tools/registry")
def tools_registry() -> dict:
    return effective_tool_registry_payload()


@app.get("/api/tools/permission-profiles")
def tools_permission_profiles() -> dict:
    return permission_profiles_payload()


@app.post("/api/tools/permission-profiles/set")
def tools_permission_profiles_set(payload: dict | None = None) -> dict:
    return set_permission_profile_payload(payload or {})


@app.get("/api/tools/status")
def tools_status() -> dict:
    return tool_registry_status_payload()

@app.get("/api/tools/ledger/status")
def tools_ledger_status() -> dict:
    return tool_ledger_status_payload()


@app.get("/api/tools/ledger/events")
def tools_ledger_events(tool_id: str | None = None, category: str | None = None, actor: str | None = None, surface: str | None = None, status: str | None = None, risk_level: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    return list_tool_ledger_events({"tool_id": tool_id, "category": category, "actor": actor, "surface": surface, "status": status, "risk_level": risk_level, "limit": limit, "offset": offset})


@app.get("/api/tools/ledger/events/{ledger_id}")
def tools_ledger_event_detail(ledger_id: str) -> dict:
    return get_tool_ledger_event(ledger_id)


@app.post("/api/tools/ledger/record")
def tools_ledger_record(payload: dict | None = None) -> dict:
    return record_tool_ledger_event(payload or {})

@app.get("/api/operator/status")
def operator_status() -> dict:
    return operator_status_payload()


@app.post("/api/operator/plan")
def operator_plan(payload: dict | None = None) -> dict:
    return plan_operator_command(payload or {})


@app.post("/api/operator/run")
def operator_run(payload: dict | None = None) -> dict:
    return run_operator_command(payload or {})

@app.get("/api/memory/status")
def memory_status() -> dict:
    return get_memory_service().memory_engine_status()


@app.get("/api/memory/capabilities")
def memory_capabilities() -> dict:
    return model_to_dict(get_memory_service().capabilities())


@app.get("/api/memory/sources")
def memory_sources(include_disabled: bool = True) -> dict:
    return get_memory_service().source_registry(include_disabled=include_disabled)


@app.post("/api/memory/index")
def memory_index(payload: dict | None = None) -> dict:
    data = payload or {}
    source_id = str(data.get("source_id") or "system_records")
    return get_memory_service().index_source(source_id, force=bool(data.get("force", False)), limit=data.get("limit"))


@app.post("/api/memory/retrieve")
def memory_retrieve(payload: dict | None = None) -> dict:
    return get_memory_service().retrieve(payload or {})


@app.get("/api/memory/retrieval-profiles")
def memory_retrieval_profiles() -> dict:
    return get_memory_service().retrieval_profiles()


@app.get("/api/memory/policies")
def memory_policies() -> dict:
    return get_memory_service().memory_policies()


@app.post("/api/memory/policies/update")
def memory_policy_update(payload: dict | None = None) -> dict:
    return get_memory_service().update_memory_policy(payload or {})


@app.get("/api/memory/diagnostics")
def memory_diagnostics() -> dict:
    return get_memory_service().health_dashboard()


@app.post("/api/memory/surface-ingestion/run")
def memory_surface_ingestion_run(payload: dict | None = None) -> dict:
    return get_memory_service().run_surface_ingestion(payload or {})

@app.get("/api/memory/retrieval-traces")
def memory_retrieval_traces(limit: int = 20) -> dict:
    return get_memory_service().retrieval_traces(limit=limit)


@app.post("/api/memory/search-ux")
def memory_search_ux(payload: dict | None = None) -> dict:
    return get_memory_service().search_ux(payload or {})


@app.get("/api/memory/citations/{chunk_id}")
def memory_citation_viewer(chunk_id: str) -> dict:
    return get_memory_service().citation_viewer(chunk_id)


@app.get("/api/memory/source-viewer")
def memory_source_viewer(chunk_id: str | None = None, source_path: str | None = None, start_line: int = 1, end_line: int | None = None, context_lines: int = 8) -> dict:
    return get_memory_service().source_viewer({"chunk_id": chunk_id, "source_path": source_path, "start_line": start_line, "end_line": end_line or start_line, "context_lines": context_lines})


@app.get("/api/memory/related-chunks/{chunk_id}")
def memory_related_chunks(chunk_id: str, limit: int = 8) -> dict:
    return get_memory_service().related_chunks(chunk_id, limit=limit)


@app.get("/api/memory/consolidation/compare/{chunk_id}")
def memory_consolidation_compare(chunk_id: str) -> dict:
    return get_memory_service().compare_consolidation_summary(chunk_id)


@app.get("/api/memory/inspect/chunks")
def memory_inspect_chunks(query: str = "", source_id: str | None = None, memory_state: str | None = None, trust_level: str | None = None, approval_state: str | None = None, visibility: str | None = None, limit: int = 25, offset: int = 0) -> dict:
    return get_memory_service().inspect_chunks({
        "query": query,
        "source_id": source_id,
        "memory_state": memory_state,
        "trust_level": trust_level,
        "approval_state": approval_state,
        "visibility": visibility,
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/memory/inspect/chunks/{chunk_id}")
def memory_inspect_chunk_detail(chunk_id: str) -> dict:
    return get_memory_service().inspect_chunk_detail(chunk_id)


@app.post("/api/memory/inspect/review")
def memory_inspect_review(payload: dict | None = None) -> dict:
    return get_memory_service().review_memory_chunk(payload or {})


@app.get("/api/memory/inspect/retrieval-traces/{trace_id}")
def memory_inspect_trace_detail(trace_id: str) -> dict:
    return get_memory_service().inspect_retrieval_trace(trace_id)



@app.get("/api/memory/conflicts")
def memory_conflict_groups(query: str = "", source_id: str | None = None, limit: int = 25) -> dict:
    return get_memory_service().conflict_groups({"query": query, "source_id": source_id, "limit": limit})


@app.get("/api/memory/conflicts/{group_id}")
def memory_conflict_group_detail(group_id: str) -> dict:
    return get_memory_service().conflict_group_detail(group_id)


@app.post("/api/memory/conflicts/resolve")
def memory_conflict_resolve(payload: dict | None = None) -> dict:
    return get_memory_service().resolve_conflict(payload or {})


@app.get("/api/memory/canon")
def memory_canon_manager(query: str = "", source_id: str | None = None, include_candidates: bool = True, limit: int = 50) -> dict:
    return get_memory_service().canon_manager({"query": query, "source_id": source_id, "include_candidates": include_candidates, "limit": limit})


@app.post("/api/memory/canon/promote")
def memory_canon_promote(payload: dict | None = None) -> dict:
    return get_memory_service().promote_canon(payload or {})





@app.get("/api/memory/retention/rules")
def memory_retention_rules() -> dict:
    return get_memory_service().retention_rules()


@app.post("/api/memory/retention/plan")
def memory_retention_plan(payload: dict | None = None) -> dict:
    return get_memory_service().retention_plan(payload or {})


@app.post("/api/memory/retention/run")
def memory_retention_run(payload: dict | None = None) -> dict:
    return get_memory_service().run_retention(payload or {})


@app.get("/api/memory/unified-consolidation/status")
def memory_unified_consolidation_status() -> dict:
    return get_memory_service().unified_consolidation_status()


@app.post("/api/memory/unified-consolidation/plan")
def memory_unified_consolidation_plan(payload: dict | None = None) -> dict:
    return get_memory_service().unified_consolidation_plan(payload or {})


@app.post("/api/memory/unified-consolidation/run")
def memory_unified_consolidation_run(payload: dict | None = None) -> dict:
    return get_memory_service().run_unified_consolidation(payload or {})

@app.get("/api/memory/retrieval-rerank/status")
def memory_retrieval_rerank_status() -> dict:
    return get_memory_service().retrieval_rerank_status()


@app.post("/api/memory/retrieval-rerank/index")
def memory_retrieval_rerank_index(payload: dict | None = None) -> dict:
    return get_memory_service().index_unified_embeddings(payload or {})


@app.post("/api/memory/retrieval-rerank/query")
def memory_retrieval_rerank_query(payload: dict | None = None) -> dict:
    return get_memory_service().retrieve_unified(payload or {})


@app.post("/api/memory/consolidation/plan")
def memory_consolidation_plan(payload: dict | None = None) -> dict:
    return get_memory_service().consolidation_plan(payload or {})


@app.post("/api/memory/consolidation/run")
def memory_consolidation_run(payload: dict | None = None) -> dict:
    return get_memory_service().run_consolidation(payload or {})



@app.get("/api/control-center/review/status")
def control_center_review_status() -> dict:
    return get_memory_service().control_center_review_status()


@app.post("/api/control-center/review/dashboard")
def control_center_review_dashboard(payload: dict | None = None) -> dict:
    return get_memory_service().control_center_review_dashboard(payload or {})


@app.get("/api/control-center/review/traces/{trace_id}")
def control_center_review_trace_detail(trace_id: str) -> dict:
    return get_memory_service().control_center_review_trace_detail(trace_id)


@app.post("/api/control-center/review/decision")
def control_center_review_decision(payload: dict | None = None) -> dict:
    return get_memory_service().control_center_review_record(payload or {})


@app.get("/api/memory/observability/status")
def memory_observability_status() -> dict:
    return get_memory_service().observability_status()


@app.post("/api/memory/observability/snapshot")
def memory_observability_snapshot(payload: dict | None = None) -> dict:
    return get_memory_service().observability_snapshot(payload or {})


@app.post("/api/memory/observability/memory")
def memory_observability_memory(payload: dict | None = None) -> dict:
    return get_memory_service().observability_memory(payload or {})


@app.post("/api/memory/observability/retrieval")
def memory_observability_retrieval(payload: dict | None = None) -> dict:
    return get_memory_service().observability_retrieval(payload or {})


@app.post("/api/memory/observability/control-center")
def memory_observability_control_center(payload: dict | None = None) -> dict:
    return get_memory_service().observability_control_center(payload or {})


@app.post("/api/memory/observability/roleplay-scene")
def memory_observability_roleplay_scene(payload: dict | None = None) -> dict:
    return get_memory_service().observability_roleplay_scene(payload or {})


@app.get("/api/memory/writeback/status")
def memory_writeback_status() -> dict:
    return get_memory_service().writeback_status()


@app.post("/api/memory/writeback/plan")
def memory_writeback_plan(payload: dict | None = None) -> dict:
    return get_memory_service().writeback_plan(payload or {})


@app.post("/api/memory/writeback/run")
def memory_writeback_run(payload: dict | None = None) -> dict:
    return get_memory_service().run_writeback(payload or {})


@app.post("/api/memory/writeback/review")
def memory_writeback_review(payload: dict | None = None) -> dict:
    return get_memory_service().review_writeback(payload or {})


@app.get("/api/memory/safety/status")
def memory_safety_status() -> dict:
    return get_memory_service().safety_status()


@app.post("/api/memory/safety/rules")
def memory_safety_rules(payload: dict | None = None) -> dict:
    return get_memory_service().safety_rules(payload or {})


@app.post("/api/memory/safety/validate-context")
def memory_safety_validate_context(payload: dict | None = None) -> dict:
    return get_memory_service().safety_validate_context(payload or {})


@app.post("/api/memory/safety/validate-writeback")
def memory_safety_validate_writeback(payload: dict | None = None) -> dict:
    return get_memory_service().safety_validate_writeback(payload or {})


@app.post("/api/memory/safety/audit")
def memory_safety_audit(payload: dict | None = None) -> dict:
    return get_memory_service().safety_audit(payload or {})


@app.post("/api/memory/safety/violations")
def memory_safety_violations(payload: dict | None = None) -> dict:
    return get_memory_service().safety_violations(payload or {})


@app.get("/api/control-center/status")
def control_center_status() -> dict:
    return get_memory_service().control_center_status()


@app.post("/api/control-center/plan")
def control_center_plan(payload: dict | None = None) -> dict:
    return get_memory_service().control_center_plan(payload or {})


@app.get("/api/control-center/traces")
def control_center_traces(limit: int = 25, controller: str | None = None, surface: str | None = None) -> dict:
    return get_memory_service().control_center_trace_list({"limit": limit, "controller": controller, "surface": surface})


@app.get("/api/control-center/traces/{trace_id}")
def control_center_trace_detail(trace_id: str) -> dict:
    return get_memory_service().control_center_trace_detail(trace_id)


@app.get("/api/memory/events")
def memory_events(namespace: str | None = None, surface: str | None = None, limit: int = 20) -> dict:
    return get_memory_service().list_events(namespace=namespace, surface=surface, limit=limit)


@app.post("/api/memory/events")
def memory_record_event(event_payload: dict) -> dict:
    # Memory should accept lightweight UI events. Missing titles are normalized
    # inside the memory service so frontend telemetry never crashes Neo.
    return get_memory_service().record_event(event_payload)


@app.post("/api/memory/search")
def memory_search(query_payload: dict) -> dict:
    return get_memory_service().search(query_payload)

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    index_path = STATIC_DIR / "index.html"
    return index_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    args = _parse_launch_args()
    _print_startup_banner(args)
    uvicorn.run(
        "neo_app.main:app",
        host=args.host,
        port=args.port,
        reload=bool(args.dev),
        access_log=True,
        log_level="info" if args.dev else "warning",
        log_config=_uvicorn_log_config(dev_mode=bool(args.dev)),
    )
