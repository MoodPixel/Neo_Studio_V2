from __future__ import annotations

from neo_app.roleplay.schema import RoleplayChildView, RoleplayShellSection, RoleplaySurfaceBaseContract, RoleplayTabBase


BACKEND_PROFILE_SURFACES = [
    "roleplay",
    "text",
    "prompt_captioning",
    "assistant",
]

BACKEND_PROFILE_PROVIDER_IDS = [
    "koboldcpp",
    "openai_compatible_text",
    "ollama",
    "local_gguf_text",
    "local_gguf_vision",
]

MEMORY_NAMESPACES = [
    "global",
    "roleplay",
    "roleplay.project",
    "roleplay.scene",
    "roleplay.story",
    "roleplay.character",
    "roleplay.canon",
    "roleplay.memory",
    "roleplay.runtime",
    "roleplay.forge",
    "roleplay.shared",
    "roleplay.retrieval",
    "roleplay.continuity",
    "roleplay.provenance",
]

MEMORY_EVENTS = [
    "roleplay.surface.opened",
    "roleplay.tab.changed",
    "roleplay.base.loaded",
    "roleplay.foundation.checked",
    "roleplay.studio.state.loaded",
    "roleplay.studio.project.created",
    "roleplay.studio.source.saved",
    "roleplay.text_backend.bridge.checked",
    "roleplay.scene.state.loaded",
    "roleplay.scene.setup.saved",
    "roleplay.scene.transcript.placeholder_added",
    "roleplay.scene.turn.executed",
    "roleplay.scene.memory.linked",
    "roleplay.stories.state.loaded",
    "roleplay.storyline.created",
    "roleplay.story_session.created",
    "roleplay.story_checkpoint.created",
    "roleplay.story.resume.restored",
    "roleplay.story_checkpoint.restored",
    "roleplay.memory.state.loaded",
    "roleplay.memory.foundation.synced",
    "roleplay.retrieval.state.loaded",
    "roleplay.retrieval.trace.placeholder_created",
]

DEFERRED_FEATURES = [
    "streaming_scene_generation",
    "scene_runtime_bundle_binding",
    "assistant_surface_handoff",
    "runtime_bundle_compile",
    "vector_index_writes",
    "reranker_execution",
    "retrieval_queries",
    "advanced_checkpoint_branching",
    "semantic_memory_writeback",
]


def _child(view_id: str, display_name: str, description: str, slot: str, fields: list[str] | None = None) -> RoleplayChildView:
    return RoleplayChildView(
        view_id=view_id,
        display_name=display_name,
        description=description,
        slot=slot,
        fields=fields or [],
    )


def _section(section_id: str, title: str, description: str, slot: str, fields: list[str] | None = None, child_views: list[RoleplayChildView] | None = None) -> RoleplayShellSection:
    return RoleplayShellSection(
        section_id=section_id,
        title=title,
        description=description,
        slot=slot,
        fields=fields or [],
        child_views=child_views or [],
    )


def _forge_tab() -> RoleplayTabBase:
    return RoleplayTabBase(
        tab_id="forge",
        display_name="Forge",
        description="Builder for roleplay records, entities, canon inputs, and inspection.",
        sections=[
            _section("builder_rail", "Builder rail", "Record-type navigation for Roleplay builder entities.", "roleplay.forge.builder_rail", ["character", "location", "faction", "item", "lore", "relationship"]),
            _section("builder", "Builder", "Structured form surface for Roleplay records.", "roleplay.forge.builder", ["record_kind", "record_title", "record_body", "tags"]),
            _section("records", "Builder records", "Saved builder records and reusable canon inputs.", "roleplay.forge.records", ["record_list", "filters", "selected_record"]),
            _section(
                "inspector",
                "Inspector",
                "Diagnostics and storage inspection for Forge records.",
                "roleplay.forge.inspector",
                ["diagnostics", "storage_status"],
                [
                    _child("inspector", "Inspector", "Human-readable record diagnostics.", "roleplay.forge.inspector.summary", ["record_summary", "validation_status"]),
                    _child("sqlite", "SQLite", "Read-only SQLite inspection.", "roleplay.forge.inspector.sqlite", ["tables", "row_counts"]),
                ],
            ),
        ],
    )


def _scene_tab() -> RoleplayTabBase:
    return RoleplayTabBase(
        tab_id="scene",
        display_name="Scene",
        description="Scene setup and chat with shared text backend generation, semantic retrieval, and streaming support.",
        sections=[
            _section("setup", "Scene setup", "Runtime bundle selector, participants, memory scope, and scene constraints.", "roleplay.scene.setup", ["scene_title", "runtime_bundle", "participants", "memory_scope", "scene_rules"]),
            _section("chat", "Scene chat", "Transcript lane for live non-streaming Roleplay turns using the shared text backend.", "roleplay.scene.chat", ["transcript", "user_turn", "backend_profile", "generation_active", "checkpoint"]),
        ],
    )


def _stories_tab() -> RoleplayTabBase:
    return RoleplayTabBase(
        tab_id="stories",
        display_name="Stories",
        description="Story workspace for storylines, archives, continuity, and provenance.",
        sections=[
            _section("workspace", "Workspace", "Active writing and story-session workspace.", "roleplay.stories.workspace", ["active_session", "draft", "notes"]),
            _section("storyline", "Storyline", "Storyline records, arcs, beats, and chapter flow.", "roleplay.stories.storyline", ["storyline_id", "arc", "beats", "status"]),
            _section(
                "archive",
                "Archive",
                "Archive browser for stories, roleplay sessions, and canon.",
                "roleplay.stories.archive",
                ["archive_filters", "archive_results"],
                [
                    _child("stories", "Stories", "Story outputs and drafts.", "roleplay.stories.archive.stories", ["story_records"]),
                    _child("roleplay", "Roleplay", "Roleplay sessions and transcript records.", "roleplay.stories.archive.roleplay", ["session_records"]),
                    _child("canon", "Canon", "Canon records and source-of-truth notes.", "roleplay.stories.archive.canon", ["canon_records"]),
                ],
            ),
            _section(
                "inspector",
                "Inspector",
                "Story diagnostics for summary, continuity, and provenance.",
                "roleplay.stories.inspector",
                ["summary_status", "continuity_status", "provenance_status"],
                [
                    _child("summary", "Summary", "Current story/session summary.", "roleplay.stories.inspector.summary", ["summary"]),
                    _child("continuity", "Continuity", "Continuity checks and contradictions.", "roleplay.stories.inspector.continuity", ["continuity_rows"]),
                    _child("provenance", "Provenance", "Source trace and memory provenance.", "roleplay.stories.inspector.provenance", ["source_trace"]),
                ],
            ),
        ],
    )


def _studio_tab() -> RoleplayTabBase:
    return RoleplayTabBase(
        tab_id="studio",
        display_name="Studio",
        description="Roleplay project, source, advanced defaults, libraries, compile, runtime, and inspector workspace.",
        sections=[
            _section("guide", "Guide", "Roleplay V2 guide and onboarding contract.", "roleplay.studio.guide", ["guide_status"]),
            _section("project", "Project", "Project selector and project metadata shell.", "roleplay.studio.project", ["project_id", "project_name", "project_status"]),
            _section("source", "Source", "Source document intake and canon extraction shell.", "roleplay.studio.source", ["source_text", "source_documents"]),
            _section("advanced", "Advanced", "Shared Scene defaults and author steering controls. Assist moved to Assistant surface.", "roleplay.studio.advanced", ["generation_defaults", "author_notes"]),
            _section("libraries", "Libraries", "Reusable libraries, packages, registry, and records.", "roleplay.studio.libraries", ["library_records"]),
            _section("compile", "Compile", "Runtime bundle compile shell.", "roleplay.studio.compile", ["compile_inputs", "compile_status"]),
            _section("runtime", "Runtime", "Runtime bundle preview shell.", "roleplay.studio.runtime", ["runtime_bundle", "runtime_manifest"]),
            _section("inspector", "Inspector", "Studio diagnostics and contract preview. Engine/reranker/embeddings are read from Admin.", "roleplay.studio.inspector", ["contract_preview", "diagnostics", "admin_engine_bridge"]),
        ],
    )


def get_roleplay_surface_base_contract() -> RoleplaySurfaceBaseContract:
    return RoleplaySurfaceBaseContract(
        tabs=[_forge_tab(), _scene_tab(), _stories_tab(), _studio_tab()],
        backend_profile_surfaces=BACKEND_PROFILE_SURFACES,
        backend_profile_provider_ids=BACKEND_PROFILE_PROVIDER_IDS,
        memory_namespaces=MEMORY_NAMESPACES,
        memory_events=MEMORY_EVENTS,
        deferred_features=DEFERRED_FEATURES,
    )
