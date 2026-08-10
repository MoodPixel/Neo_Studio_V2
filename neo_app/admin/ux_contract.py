from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

ADMIN_UX_SCHEMA_ID: Final[str] = "neo.admin.ux_consolidation.phase12.v1"

# Visible Admin areas intentionally keep their historical route ids so saved UI
# state, links, and older integrations continue to work. The contract separates
# the user-facing label from the compatibility route.
ADMIN_UX_AREAS: Final[dict[str, dict[str, Any]]] = {
    "memory": {
        "label": "Memory",
        "owner": "admin.memory",
        "purpose": "Memory content governance, review, citations, conflict/canon, consolidation, and retention.",
        "child_tabs": ["inspector", "search", "policies", "durable_review", "conflicts", "consolidation", "retention", "diagnostics"],
        "must_not_duplicate": ["embedding_configuration", "vector_store_configuration", "assistant_scope_editing", "project_brain_rebuild"],
        "related": {"memory_engine": "Infrastructure", "assistant": "Memory Lens", "projects": "Linked memory readout"},
    },
    "engine": {
        "label": "Memory Engine",
        "owner": "admin.memory_engine",
        "purpose": "Retrieval/index infrastructure, embeddings, reranker, vector store, sources, and unified background jobs.",
        "child_tabs": ["bridge", "embeddings", "vector", "index_jobs", "chroma", "retrieval_profiles", "sources", "diagnostics"],
        "must_not_duplicate": ["memory_search_citations", "durable_memory_review", "generation_sampling", "assistant_scope_editing"],
        "related": {"memory": "Governance", "backends": "Generation sampling"},
    },
    "projects": {
        "label": "Delivery Projects",
        "owner": "admin.delivery_projects",
        "purpose": "Client/work delivery records, assets, briefs, timeline, milestones, deliverables, approvals, and packages.",
        "child_tabs": ["workspaces", "asset_tray", "briefs", "timeline", "milestones_delivery", "review_approval", "packages", "project_memory", "diagnostics"],
        "must_not_duplicate": ["assistant_scope_editing", "project_brain_ingestion", "memory_index_repair"],
        "related": {"assistant": "Scopes", "memory": "Memory governance", "memory_engine": "Memory infrastructure"},
    },
    "assistant_operator": {
        "label": "Assistant / Operator",
        "owner": "admin.assistant_operator",
        "purpose": "Orchestration diagnostics, read-only Scope visibility, permissions, traces, and execution ledger.",
        "child_tabs": ["assistant_workspaces", "operator", "voice_input", "internet_api", "control_center", "tools_permissions", "execution_ledger", "diagnostics"],
        "must_not_duplicate": ["assistant_scope_activation", "assistant_scope_editing", "memory_governance", "memory_engine_configuration"],
        "related": {"assistant": "Normal Assistant + Scopes", "memory": "Durable review", "memory_engine": "Retrieval infrastructure"},
    },
}

ADMIN_SUBTAB_ALIASES: Final[dict[str, str]] = {
    "memory_engine": "engine",
    "delivery_projects": "projects",
    "project_workspace": "projects",
    "assistant": "assistant_operator",
    "operator": "assistant_operator",
}

ADMIN_CHILD_TAB_ALIASES: Final[dict[str, dict[str, str]]] = {
    "memory": {
        "review": "durable_review",
        "writeback": "durable_review",
        "durable_memory": "durable_review",
    },
    "engine": {
        "background_jobs": "index_jobs",
        "jobs": "index_jobs",
        "memory_jobs": "index_jobs",
    },
    "projects": {
        "delivery_workspace": "workspaces",
        "linked_memory": "project_memory",
        "memory": "project_memory",
    },
    "assistant_operator": {
        "scope_readout": "assistant_workspaces",
        "assistant_scopes": "assistant_workspaces",
        "traces": "control_center",
        "ledger": "execution_ledger",
    },
}


def resolve_admin_subtab(value: str | None) -> str:
    raw = str(value or "overview").strip().lower()
    return ADMIN_SUBTAB_ALIASES.get(raw, raw)


def resolve_admin_child_tab(area: str, value: str | None) -> str:
    resolved_area = resolve_admin_subtab(area)
    raw = str(value or "").strip().lower()
    aliases = ADMIN_CHILD_TAB_ALIASES.get(resolved_area, {})
    candidate = aliases.get(raw, raw)
    allowed = ADMIN_UX_AREAS.get(resolved_area, {}).get("child_tabs", [])
    if candidate in allowed:
        return candidate
    return str(allowed[0]) if allowed else candidate


def admin_ux_contract_payload() -> dict[str, Any]:
    return {
        "schema_id": ADMIN_UX_SCHEMA_ID,
        "phase": 12,
        "status": "active",
        "policy": "Admin configures, governs, and inspects shared systems without duplicating normal Assistant or surface-owned workflow controls.",
        "areas": deepcopy(ADMIN_UX_AREAS),
        "compatibility": {
            "subtab_aliases": deepcopy(ADMIN_SUBTAB_ALIASES),
            "child_tab_aliases": deepcopy(ADMIN_CHILD_TAB_ALIASES),
            "saved_state_policy": "Historical route ids remain readable; visible labels follow canonical ownership language.",
        },
        "ownership_links": [
            {"from": "memory", "to": "engine", "reason": "Open infrastructure settings instead of duplicating them in Memory."},
            {"from": "engine", "to": "memory", "reason": "Open Search + Citations or durable review instead of duplicating governance in Memory Engine."},
            {"from": "projects", "to": "assistant", "reason": "Assistant Scope editing/activation belongs on the Assistant surface."},
            {"from": "projects", "to": "memory", "reason": "Delivery Projects shows linked memory read-only; memory governance belongs to Admin Memory."},
            {"from": "assistant_operator", "to": "assistant", "reason": "Admin Scope Readout is diagnostic only; normal Scope management belongs to Assistant."},
        ],
        "admin_boundary": {
            "memory": "governance",
            "memory_engine": "infrastructure",
            "delivery_projects": "delivery_management",
            "assistant_operator": "orchestration_and_permissions",
            "assistant_surface": "normal_assistant_work_and_scope_management",
        },
    }
