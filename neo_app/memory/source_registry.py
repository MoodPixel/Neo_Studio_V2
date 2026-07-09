from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCE_REGISTRY_SCHEMA_ID = "neo.memory.source_registry.v1"
SOURCE_REGISTRY_VERSION = "0.9.1-assistant-scope-primary-project-workspace-compat"


@dataclass(frozen=True)
class MemorySourceDefinition:
    source_id: str
    label: str
    source_type: str
    root_path: str
    enabled: bool = True
    index_policy: str = "hash_update"
    visibility: str = "expert"
    trust_level: str = "confirmed"
    priority: int = 50
    description: str = ""
    extensions: tuple[str, ...] = ()
    include_paths: tuple[str, ...] = ()
    exclude_dirs: tuple[str, ...] = ()
    max_file_bytes: int = 180_000

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["extensions"] = list(self.extensions)
        payload["include_paths"] = list(self.include_paths)
        payload["exclude_dirs"] = list(self.exclude_dirs)
        payload["max_file_bytes"] = self.max_file_bytes
        path = ROOT_DIR / self.root_path
        payload["exists"] = path.exists()
        payload["absolute_path"] = str(path)
        return payload


DEFAULT_MEMORY_SOURCES: tuple[MemorySourceDefinition, ...] = (
    MemorySourceDefinition(
        source_id="system_records",
        label="System Records",
        source_type="markdown_records",
        root_path="neo_system_records",
        visibility="expert",
        trust_level="confirmed",
        priority=95,
        description="Neo source-of-truth records, architecture decisions, changelog, and implementation rules.",
        extensions=(".md",),
    ),
    MemorySourceDefinition(
        source_id="neo_codebase",
        label="Neo Codebase",
        source_type="codebase",
        root_path=".",
        enabled=True,
        visibility="expert",
        trust_level="confirmed",
        priority=90,
        description="Neo application code, extension code, UI source, and tests for code-aware Assistant/Memory Engine retrieval.",
        extensions=(".py", ".js", ".css"),
        include_paths=("neo_app", "neo_extensions", "neo_ui", "tests"),
        exclude_dirs=("__pycache__", ".pytest_cache", "cache", "disabled", "installed", "neo_data", ".git"),
        max_file_bytes=260_000,
    ),

    MemorySourceDefinition(
        source_id="project_workspace",
        label="Legacy Project Workspace",
        source_type="legacy_project_workspace",
        root_path="neo_data/projects",
        visibility="user_visible",
        trust_level="confirmed",
        priority=88,
        description="Legacy Admin delivery/project workspace compatibility data. Assistant Scope is the primary internal project/context model; this source is explicit-only for Assistant context packs.",
        extensions=(".json", ".md", ".txt"),
    ),
    MemorySourceDefinition(
        source_id="assistant_memory",
        label="Assistant Memory",
        source_type="assistant_context",
        root_path="neo_data/assistant",
        visibility="user_visible",
        trust_level="confirmed",
        priority=80,
        description="Assistant captures, sessions, projects, and surface context.",
        extensions=(".json", ".md", ".txt"),
    ),
    MemorySourceDefinition(
        source_id="roleplay_memory",
        label="Roleplay Memory",
        source_type="roleplay_runtime",
        root_path="neo_data/roleplay",
        visibility="roleplay_only",
        trust_level="mixed",
        priority=80,
        description="Roleplay SQLite/runtime memory, human scene packets, character state, canon records, scenes, checkpoints, and continuity state.",
        extensions=(".json", ".md", ".txt", ".sqlite"),
    ),
    MemorySourceDefinition(
        source_id="prompt_libraries",
        label="Prompt Libraries",
        source_type="creator_library",
        root_path="neo_data/prompt_captioning",
        visibility="user_visible",
        trust_level="confirmed",
        priority=70,
        description="Prompt, caption, keyword, and creator library records.",
        extensions=(".json", ".md", ".txt"),
    ),
    MemorySourceDefinition(
        source_id="extension_manifests",
        label="Extension Manifests",
        source_type="extension_manifest",
        root_path="neo_extensions",
        visibility="expert",
        trust_level="confirmed",
        priority=70,
        description="Extension manifests and UI contracts.",
        extensions=(".json", ".py", ".md"),
    ),
    MemorySourceDefinition(
        source_id="admin_config",
        label="Admin Config",
        source_type="admin_config",
        root_path="neo_data/admin",
        visibility="expert",
        trust_level="confirmed",
        priority=75,
        description="Admin-owned configuration, Memory Engine settings, vector store, and retrieval defaults.",
        extensions=(".json", ".md", ".txt"),
    ),


    MemorySourceDefinition(
        source_id="memory_consolidation",
        label="Memory Consolidation",
        source_type="memory_summary",
        root_path="neo_data/memory/consolidated",
        visibility="expert",
        trust_level="confirmed",
        priority=85,
        description="Durable Memory Engine summaries created from reviewed chunk groups, session clusters, and roleplay/assistant consolidation passes.",
        extensions=(".json", ".md", ".txt"),
    ),

    MemorySourceDefinition(
        source_id="internet_external",
        label="External Internet/API Context",
        source_type="external_context",
        root_path="neo_data/admin/internet_access.json",
        enabled=False,
        visibility="expert",
        trust_level="mixed",
        priority=45,
        description="Optional external context metadata captured from Admin-approved internet/API access. External facts can become stale and should be rechecked.",
        extensions=(".json", ".md", ".txt"),
    ),
    MemorySourceDefinition(
        source_id="surface_blueprints",
        label="Surface Blueprints",
        source_type="surface_blueprint",
        root_path="neo_app/surfaces",
        visibility="expert",
        trust_level="confirmed",
        priority=65,
        description="Surface registry and blueprint definitions for tab anatomy.",
        extensions=(".py", ".json", ".md"),
    ),
)


def memory_source_definitions(include_disabled: bool = True) -> list[dict[str, Any]]:
    sources = DEFAULT_MEMORY_SOURCES if include_disabled else tuple(source for source in DEFAULT_MEMORY_SOURCES if source.enabled)
    return [source.to_dict() for source in sources]


def get_memory_source(source_id: str) -> dict[str, Any] | None:
    for source in DEFAULT_MEMORY_SOURCES:
        if source.source_id == source_id:
            return source.to_dict()
    return None


def memory_source_registry_payload(include_disabled: bool = True) -> dict[str, Any]:
    sources = memory_source_definitions(include_disabled=include_disabled)
    enabled = [source for source in sources if source.get("enabled")]
    return {
        "schema_id": SOURCE_REGISTRY_SCHEMA_ID,
        "version": SOURCE_REGISTRY_VERSION,
        "status": "ready",
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "enabled_count": len(enabled),
            "existing_count": sum(1 for source in sources if source.get("exists")),
        },
        "policy": "Admin Memory Engine owns source registration. Consumers retrieve through the unified Memory Engine API instead of reading source roots directly.",
    }
