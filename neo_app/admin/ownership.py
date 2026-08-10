from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

CANONICAL_MEMORY_NAMESPACES: Final[tuple[str, ...]] = (
    "global",
    "image",
    "video",
    "voice",
    "prompt_captioning",
    "roleplay",
    "assistant",
    "board",
    "admin",
)

MEMORY_ENGINE_SECTIONS: Final[tuple[str, ...]] = (
    "overview",
    "sources",
    "text_bridge",
    "embeddings",
    "reranker",
    "vector_store",
    "retrieval",
    "indexing",
    "index_jobs",
    "memory_jobs",
    "diagnostics",
)

MEMORY_ENGINE_GENERATION_KEYS: Final[tuple[str, ...]] = (
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
)

ADMIN_OWNERSHIP_CONTRACT: Final[dict[str, dict[str, Any]]] = {
    "memory": {
        "owner": "admin.memory",
        "responsibilities": [
            "memory_records",
            "review_and_approval",
            "policies",
            "conflicts_and_canon",
            "consolidation",
            "retention",
            "citations",
            "memory_lifecycle",
        ],
        "must_not_own": ["embedding_models", "rerankers", "vector_store", "generation_sampling"],
    },
    "memory_engine": {
        "owner": "admin.memory_engine",
        "responsibilities": [
            "memory_sources",
            "text_bridge",
            "embedding_models",
            "rerankers",
            "vector_store",
            "retrieval_profiles",
            "indexing",
            "memory_jobs_compatibility",
            "engine_diagnostics",
        ],
        "must_not_own": ["generation_sampling", "memory_record_governance", "delivery_project_management"],
    },
    "backends": {
        "owner": "admin.backends",
        "responsibilities": ["providers", "backend_profiles", "generation_sampling", "surface_backend_defaults"],
        "must_not_own": ["memory_governance", "memory_indexing", "delivery_project_management"],
    },
    "models": {
        "owner": "admin.models",
        "responsibilities": ["model_catalog", "installed_model_inventory", "model_source_discovery", "download_planning", "model_packs", "workspace_requirements"],
        "must_not_own": ["backend_profile_editing", "generation_sampling", "memory_retrieval_assignment", "memory_governance"],
    },
    "projects": {
        "owner": "admin.delivery_projects",
        "responsibilities": [
            "client_and_delivery_projects",
            "briefs",
            "linked_assets",
            "timeline",
            "milestones",
            "deliverables",
            "reviews",
            "approvals",
            "packages",
            "memory_links_readout",
        ],
        "must_not_own": ["assistant_scope_editing", "independent_memory_indexing", "memory_engine_configuration"],
    },
    "assistant_operator": {
        "owner": "admin.assistant_operator",
        "responsibilities": [
            "orchestration_diagnostics",
            "scope_readout",
            "control_center_traces",
            "tool_permissions",
            "operator_execution",
            "execution_ledger",
            "voice_input_bridge",
            "internet_api_permissions",
        ],
        "must_not_own": ["normal_scope_editing", "generation_sampling", "memory_engine_configuration"],
    },
}


def _merge_unique(existing: Any, required: tuple[str, ...] | list[str]) -> list[str]:
    values: list[str] = []
    for item in list(required) + list(existing or []):
        value = str(item or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def canonical_memory_engine_resources(global_config: dict[str, Any] | None) -> dict[str, Any]:
    global_config = global_config if isinstance(global_config, dict) else {}
    raw = global_config.get("memory_engine_resources")
    if not isinstance(raw, dict) or not raw:
        raw = global_config.get("engine_resources") if isinstance(global_config.get("engine_resources"), dict) else {}
    if raw.get("compatibility_alias_of") and not raw.get("status_endpoint"):
        raw = {}
    canonical = {
        "status_endpoint": "/api/admin/engine/state",
        "owner": "admin.memory_engine",
        "data_root": "neo_data/admin/engine",
        "vector_store_root": "neo_data/vector_store",
        "sections": list(MEMORY_ENGINE_SECTIONS),
        "policy": "Memory Engine owns shared retrieval/index infrastructure. Generation sampling belongs to Admin → Backends; memory records/governance belong to Admin → Memory.",
        "label": "Memory Engine",
    }
    canonical.update({key: value for key, value in raw.items() if key not in {"sections", "compatibility_alias_of", "deprecated"}})
    canonical["sections"] = list(MEMORY_ENGINE_SECTIONS)
    canonical["owner"] = "admin.memory_engine"
    return canonical


def derive_memory_controls(config: dict[str, Any]) -> dict[str, Any]:
    existing = config.get("memory_controls") if isinstance(config.get("memory_controls"), dict) else {}
    memory_resources = ((config.get("global") or {}).get("memory_resources") or {})
    return {
        "status_endpoint": existing.get("status_endpoint") or "/api/memory/status",
        "events_endpoint": existing.get("events_endpoint") or "/api/memory/events",
        "search_endpoint": existing.get("search_endpoint") or "/api/memory/search",
        "actions": list(existing.get("actions") or ["view_status", "record_event", "list_events", "search_events", "check_optional_dependencies"]),
        "required_layer": existing.get("required_layer") or "sqlite",
        "optional_layers": list(existing.get("optional_layers") or memory_resources.get("optional_dependencies") or []),
        "namespaces": _merge_unique(memory_resources.get("namespaces_enabled"), CANONICAL_MEMORY_NAMESPACES),
        "derived_from": "global.memory_resources",
        "compatibility_descriptor": True,
    }


def normalize_admin_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the Phase 2 canonical Admin config without breaking legacy readers.

    Raw config keeps only one Memory Engine source of truth. This normalizer
    expands the historical ``global.engine_resources`` and ``memory_controls``
    shapes for callers that still expect them while declaring their canonical
    owners explicitly.
    """
    normalized = deepcopy(config or {})
    global_config = normalized.setdefault("global", {})
    memory_resources = global_config.setdefault("memory_resources", {})
    memory_resources["namespaces_enabled"] = _merge_unique(memory_resources.get("namespaces_enabled"), CANONICAL_MEMORY_NAMESPACES)

    memory_engine_resources = canonical_memory_engine_resources(global_config)
    global_config["memory_engine_resources"] = memory_engine_resources
    global_config["engine_resources"] = {
        **memory_engine_resources,
        "compatibility_alias_of": "global.memory_engine_resources",
        "deprecated": True,
    }

    normalized["memory_controls"] = derive_memory_controls(normalized)
    normalized["ownership_contract"] = deepcopy(ADMIN_OWNERSHIP_CONTRACT)
    normalized["config_contract"] = {
        "schema_id": "neo.admin.ownership_config.phase2.v1",
        "memory_engine_canonical_path": "global.memory_engine_resources",
        "memory_resources_canonical_path": "global.memory_resources",
        "legacy_memory_engine_alias": "global.engine_resources",
        "legacy_memory_controls_mode": "derived_compatibility_descriptor",
        "generation_sampling_owner": "admin.backends",
        "delivery_project_owner": "admin.delivery_projects",
        "assistant_scope_owner": "assistant.surface",
    }
    return normalized
