from __future__ import annotations

from pydantic import BaseModel, Field


class RoleplayChildView(BaseModel):
    view_id: str
    display_name: str
    description: str = ""
    slot: str = ""
    fields: list[str] = Field(default_factory=list)


class RoleplayShellSection(BaseModel):
    section_id: str
    title: str
    description: str = ""
    slot: str
    fields: list[str] = Field(default_factory=list)
    child_views: list[RoleplayChildView] = Field(default_factory=list)


class RoleplayTabBase(BaseModel):
    tab_id: str
    display_name: str
    description: str = ""
    sections: list[RoleplayShellSection] = Field(default_factory=list)


class RoleplaySurfaceBaseContract(BaseModel):
    surface_id: str = "roleplay"
    version: str = "0.1.0-foundation"
    schema_id: str = "neo.roleplay.base.v1"
    status: str = "foundation"
    data_root: str = "neo_data/roleplay"
    tabs: list[RoleplayTabBase] = Field(default_factory=list)
    backend_profile_surfaces: list[str] = Field(default_factory=list)
    backend_profile_provider_ids: list[str] = Field(default_factory=list)
    memory_namespaces: list[str] = Field(default_factory=list)
    memory_events: list[str] = Field(default_factory=list)
    deferred_features: list[str] = Field(default_factory=list)


class RoleplayFoundationDirectory(BaseModel):
    directory_id: str
    path: str
    exists: bool = False


class RoleplayFoundationState(BaseModel):
    schema_id: str = "neo.roleplay.foundation.v1"
    version: str = "0.1.0-foundation"
    surface_id: str = "roleplay"
    status: str = "foundation"
    ready: bool = False
    data_root: str = "neo_data/roleplay"
    manifest_path: str = "neo_data/roleplay/foundation_manifest.json"
    sqlite_path: str = "neo_data/roleplay/roleplay.sqlite"
    created_at: str = ""
    checked_at: str = ""
    directories: list[RoleplayFoundationDirectory] = Field(default_factory=list)
    missing_directories: list[str] = Field(default_factory=list)
    deferred_features: list[str] = Field(default_factory=lambda: [
        "sqlite_schema",
        "forge_record_writes",
        "scene_generation",
        "runtime_bundle_compile",
        "retrieval_queries",
        "story_checkpoints",
        "memory_writeback",
    ])



class RoleplayForgeKind(BaseModel):
    kind_id: str
    display_name: str
    description: str = ""
    storage_path: str = ""
    record_count: int = 0


class RoleplayForgeRecord(BaseModel):
    record_id: str
    kind: str
    title: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)
    markdown: str = ""
    created_at: str = ""
    updated_at: str = ""
    storage_path: str = ""


class RoleplayForgeSQLitePlaceholder(BaseModel):
    planned: bool = True
    path: str = "neo_data/roleplay/roleplay.sqlite"
    status: str = "deferred"
    tables: list[str] = Field(default_factory=lambda: [
        "rp_entities",
        "rp_entity_versions",
        "rp_edges",
        "rp_memory_fragments",
        "rp_shared_memories",
        "rp_relationship_state",
        "rp_continuity_rows",
        "rp_retrieval_traces",
        "rp_turn_summaries",
        "rp_story_checkpoints",
    ])


class RoleplayForgeState(BaseModel):
    schema_id: str = "neo.roleplay.forge.v1"
    version: str = "0.1.0-forge-foundation"
    surface_id: str = "roleplay"
    tab_id: str = "forge"
    status: str = "foundation"
    ready: bool = True
    active_kind: str = "character"
    builder_fields: list[str] = Field(default_factory=lambda: ["kind", "title", "body", "tags"])
    kinds: list[RoleplayForgeKind] = Field(default_factory=list)
    records: list[RoleplayForgeRecord] = Field(default_factory=list)
    templates_by_kind: dict = Field(default_factory=dict)
    active_template: dict = Field(default_factory=dict)
    hierarchy: dict = Field(default_factory=dict)
    inspector: dict = Field(default_factory=dict)
    sqlite: RoleplayForgeSQLitePlaceholder = Field(default_factory=RoleplayForgeSQLitePlaceholder)
    deferred_features: list[str] = Field(default_factory=lambda: [
        "sqlite_sync",
        "relationship_graph_sync",
        "record_validation_rules",
        "canon_compile_hooks",
        "memory_fragment_writeback",
    ])


class RoleplayStudioProject(BaseModel):
    project_id: str
    title: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    storage_path: str = ""


class RoleplayStudioSource(BaseModel):
    source_id: str
    project_id: str = ""
    title: str
    source_type: str = "text"
    body_preview: str = ""
    created_at: str = ""
    updated_at: str = ""
    storage_path: str = ""


class RoleplayStudioRuntimePlaceholder(BaseModel):
    planned: bool = True
    status: str = "deferred"
    bundle_root: str = "neo_data/roleplay/runtime_bundles"
    compile_ready: bool = False
    deferred_steps: list[str] = Field(default_factory=lambda: [
        "source_chunking",
        "canon_merge",
        "retrieval_index",
        "runtime_bundle_compile",
        "scene_engine_binding",
    ])


class RoleplayStudioEnginePlaceholder(BaseModel):
    status: str = "foundation"
    profile_surfaces: list[str] = Field(default_factory=lambda: ["roleplay", "text", "prompt_captioning", "assistant"])
    provider_ids: list[str] = Field(default_factory=lambda: [
        "koboldcpp",
        "openai_compatible_text",
        "ollama",
        "local_gguf_text",
        "local_gguf_vision",
    ])
    fallback_order: list[str] = Field(default_factory=lambda: [
        "roleplay",
        "text",
        "prompt_captioning",
        "assistant",
    ])


class RoleplayStudioState(BaseModel):
    schema_id: str = "neo.roleplay.studio.v1"
    version: str = "0.1.0-studio-foundation"
    surface_id: str = "roleplay"
    tab_id: str = "studio"
    status: str = "foundation"
    ready: bool = True
    active_view: str = "guide"
    child_views: list[str] = Field(default_factory=lambda: [
        "guide",
        "project",
        "source",
        "advanced",
        "libraries",
        "compile",
        "runtime",
        "inspector",
    ])
    projects: list[RoleplayStudioProject] = Field(default_factory=list)
    sources: list[RoleplayStudioSource] = Field(default_factory=list)
    guide: dict = Field(default_factory=dict)
    advanced: dict = Field(default_factory=dict)
    libraries: dict = Field(default_factory=dict)
    compile: dict = Field(default_factory=dict)
    runtime: RoleplayStudioRuntimePlaceholder = Field(default_factory=RoleplayStudioRuntimePlaceholder)
    engine: RoleplayStudioEnginePlaceholder = Field(default_factory=RoleplayStudioEnginePlaceholder)
    inspector: dict = Field(default_factory=dict)
    deferred_features: list[str] = Field(default_factory=lambda: [
        "assistant_surface_handoff",
        "source_chunking",
        "library_import_export",
        "runtime_bundle_compile",
        "admin_engine_bridge",
        "scene_binding",
    ])



class RoleplayStorylineRecord(BaseModel):
    storyline_id: str
    title: str
    premise: str = ""
    arc: str = ""
    beats: str = ""
    status: str = "foundation"
    created_at: str = ""
    updated_at: str = ""
    storage_path: str = ""


class RoleplayStoriesState(BaseModel):
    schema_id: str = "neo.roleplay.stories.v1"
    version: str = "0.1.0-stories-foundation"
    surface_id: str = "roleplay"
    tab_id: str = "stories"
    status: str = "foundation"
    ready: bool = True
    active_view: str = "workspace"
    child_views: list[str] = Field(default_factory=lambda: [
        "workspace",
        "storyline",
        "archive",
        "inspector",
    ])
    archive_child_views: list[str] = Field(default_factory=lambda: ["stories", "roleplay", "canon"])
    inspector_child_views: list[str] = Field(default_factory=lambda: ["summary", "continuity", "provenance"])
    storylines: list[RoleplayStorylineRecord] = Field(default_factory=list)
    workspace: dict = Field(default_factory=dict)
    storyline: dict = Field(default_factory=dict)
    archive: dict = Field(default_factory=dict)
    inspector: dict = Field(default_factory=dict)
    deferred_features: list[str] = Field(default_factory=lambda: [
        "workspace_draft_editing",
        "story_session_resume",
        "archive_search",
        "summary_generation",
        "continuity_checks",
        "provenance_tracing",
        "story_checkpoints",
    ])
