from __future__ import annotations

from copy import deepcopy
from typing import Any

MEMORY_POLICY_SCHEMA_ID = "neo.memory.policies.v1"
MEMORY_POLICY_VERSION = "0.8.0-retention-automation"

RETENTION_SCOPES = ("temporary", "session", "project", "long_term", "system", "canon", "draft")
MEMORY_STATES = ("active", "draft", "canon", "deprecated", "conflicting", "archived")
VISIBILITY_LEVELS = ("user_visible", "expert", "internal", "private_project", "roleplay_only")
TRUST_LEVELS = ("confirmed", "inferred", "draft", "conflicting", "deprecated", "system", "mixed")
IMPORTANCE_LEVELS = ("low", "normal", "high", "critical")
APPROVAL_STATES = ("not_required", "pending", "approved", "rejected")


RETENTION_AUTOMATION_SCHEMA_ID = "neo.memory.retention_automation.v1"
RETENTION_ACTIONS = (
    "archive_temporary",
    "deprecate_stale_external",
    "mark_for_review",
    "refresh_source",
    "keep",
)

_DECAY_POLICY_RULES: dict[str, dict[str, Any]] = {
    "none": {
        "label": "No automatic decay",
        "default_ttl_days": None,
        "candidate_after_days": None,
        "recommended_action": "keep",
        "notes": "Source-backed/canon/system memory should not decay automatically.",
    },
    "hash_refresh": {
        "label": "Hash refresh",
        "default_ttl_days": None,
        "candidate_after_days": 30,
        "recommended_action": "refresh_source",
        "notes": "Source-backed files should be rescanned by hash instead of archived.",
    },
    "soft_decay": {
        "label": "Soft decay",
        "default_ttl_days": 90,
        "candidate_after_days": 90,
        "recommended_action": "mark_for_review",
        "notes": "Project/session memories become review candidates when old or low-value, not automatically removed.",
    },
    "scene_decay": {
        "label": "Roleplay scene decay",
        "default_ttl_days": 45,
        "candidate_after_days": 45,
        "recommended_action": "mark_for_review",
        "notes": "Roleplay scene state can be reviewed, consolidated, canonized, or archived after scene aging.",
    },
    "external_stale_recheck": {
        "label": "External stale recheck",
        "default_ttl_days": 14,
        "candidate_after_days": 14,
        "recommended_action": "deprecate_stale_external",
        "notes": "External/internet facts are lower-trust and should be rechecked or deprecated when stale.",
    },
    "summary_refresh": {
        "label": "Summary refresh",
        "default_ttl_days": 180,
        "candidate_after_days": 180,
        "recommended_action": "mark_for_review",
        "notes": "Durable summaries should be reviewed periodically, but originals remain auditable.",
    },
}


_DEFAULT_POLICY_BY_SOURCE: dict[str, dict[str, Any]] = {
    "system_records": {
        "retention_scope": "system",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "critical",
        "approval_state": "not_required",
        "decay_policy": "none",
        "ttl_days": None,
        "write_policy": "hash_update",
        "notes": "System records are high-trust Neo source-of-truth memory.",
    },
    "neo_codebase": {
        "retention_scope": "system",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "not_required",
        "decay_policy": "hash_refresh",
        "ttl_days": None,
        "write_policy": "hash_update",
        "notes": "Codebase memory is source-derived and updated by file hash.",
    },

    "project_workspace": {
        "retention_scope": "project",
        "memory_state": "active",
        "visibility": "user_visible",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "approved",
        "decay_policy": "soft_decay",
        "ttl_days": None,
        "write_policy": "legacy_creator_project_workspace_compatibility",
        "notes": "Project Workspace memory is legacy Admin delivery compatibility data. Assistant Scope is primary for internal project/context memory, and Assistant context packs include this source only on explicit request.",
    },
    "assistant_memory": {
        "retention_scope": "project",
        "memory_state": "active",
        "visibility": "user_visible",
        "trust_level": "confirmed",
        "importance": "normal",
        "approval_state": "approved",
        "decay_policy": "soft_decay",
        "ttl_days": None,
        "write_policy": "user_or_assistant_capture",
        "notes": "Assistant captures become searchable only after explicit save/context writeback.",
    },
    "roleplay_memory": {
        "retention_scope": "project",
        "memory_state": "draft",
        "visibility": "roleplay_only",
        "trust_level": "inferred",
        "importance": "normal",
        "approval_state": "not_required",
        "decay_policy": "scene_decay",
        "ttl_days": None,
        "write_policy": "roleplay_state_packet",
        "notes": "Roleplay scene state is not canon unless marked canon/confirmed by Roleplay tools.",
    },
    "prompt_libraries": {
        "retention_scope": "long_term",
        "memory_state": "active",
        "visibility": "user_visible",
        "trust_level": "confirmed",
        "importance": "normal",
        "approval_state": "not_required",
        "decay_policy": "none",
        "ttl_days": None,
        "write_policy": "library_file",
        "notes": "Creator libraries are reusable long-term knowledge.",
    },
    "extension_manifests": {
        "retention_scope": "system",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "not_required",
        "decay_policy": "hash_refresh",
        "ttl_days": None,
        "write_policy": "manifest_hash_update",
        "notes": "Extension contracts are system-level memory.",
    },
    "admin_config": {
        "retention_scope": "system",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "not_required",
        "decay_policy": "hash_refresh",
        "ttl_days": None,
        "write_policy": "admin_owned",
        "notes": "Admin Memory Engine settings are control-plane memory.",
    },


    "memory_consolidation": {
        "retention_scope": "long_term",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "approved",
        "decay_policy": "summary_refresh",
        "ttl_days": None,
        "write_policy": "admin_reviewed_consolidation",
        "notes": "Consolidated summaries preserve durable meaning while original chunks remain auditable.",
    },

    "internet_external": {
        "retention_scope": "temporary",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "mixed",
        "importance": "low",
        "approval_state": "pending",
        "decay_policy": "external_stale_recheck",
        "ttl_days": 14,
        "write_policy": "admin_permissioned_external_context",
        "notes": "External internet/API context is optional, lower-trust, and should be rechecked before relying on time-sensitive facts.",
    },
    "surface_blueprints": {
        "retention_scope": "system",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "confirmed",
        "importance": "high",
        "approval_state": "not_required",
        "decay_policy": "hash_refresh",
        "ttl_days": None,
        "write_policy": "surface_contract",
        "notes": "Surface blueprints describe canonical tab anatomy.",
    },
}

_POLICY_SCORE = {
    "memory_state": {"canon": 1.0, "active": 0.86, "draft": 0.48, "conflicting": 0.18, "deprecated": 0.08, "archived": 0.22},
    "trust_level": {"system": 1.0, "confirmed": 0.9, "mixed": 0.6, "inferred": 0.55, "draft": 0.38, "conflicting": 0.14, "deprecated": 0.05},
    "retention_scope": {"system": 1.0, "canon": 0.96, "long_term": 0.86, "project": 0.72, "session": 0.5, "draft": 0.42, "temporary": 0.28},
    "importance": {"critical": 1.0, "high": 0.82, "normal": 0.55, "low": 0.22},
    "approval_state": {"approved": 1.0, "not_required": 0.84, "pending": 0.36, "rejected": 0.0},
}


def default_memory_policy(source_id: str | None = None) -> dict[str, Any]:
    return deepcopy(_DEFAULT_POLICY_BY_SOURCE.get(str(source_id or ""), {
        "retention_scope": "project",
        "memory_state": "active",
        "visibility": "expert",
        "trust_level": "inferred",
        "importance": "normal",
        "approval_state": "not_required",
        "decay_policy": "soft_decay",
        "ttl_days": None,
        "write_policy": "source_default",
        "notes": "Default memory policy for unclassified sources.",
    }))


def normalize_memory_policy(source_id: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = default_memory_policy(source_id)
    overrides = overrides or {}
    for key in ("retention_scope", "memory_state", "visibility", "trust_level", "importance", "approval_state", "decay_policy", "ttl_days", "write_policy", "notes"):
        if key in overrides and overrides.get(key) not in (None, ""):
            policy[key] = overrides.get(key)
    if policy.get("retention_scope") not in RETENTION_SCOPES:
        policy["retention_scope"] = default_memory_policy(source_id).get("retention_scope")
    if policy.get("memory_state") not in MEMORY_STATES:
        policy["memory_state"] = default_memory_policy(source_id).get("memory_state")
    if policy.get("visibility") not in VISIBILITY_LEVELS:
        policy["visibility"] = default_memory_policy(source_id).get("visibility")
    if policy.get("trust_level") not in TRUST_LEVELS:
        policy["trust_level"] = default_memory_policy(source_id).get("trust_level")
    if policy.get("importance") not in IMPORTANCE_LEVELS:
        policy["importance"] = default_memory_policy(source_id).get("importance")
    if policy.get("approval_state") not in APPROVAL_STATES:
        policy["approval_state"] = default_memory_policy(source_id).get("approval_state")
    policy["source_id"] = str(source_id or "unknown")
    policy["policy_score"] = memory_policy_score(policy)
    return policy


def memory_policy_score(policy: dict[str, Any]) -> float:
    parts = {
        "memory_state": _POLICY_SCORE["memory_state"].get(str(policy.get("memory_state") or "active"), 0.5),
        "trust_level": _POLICY_SCORE["trust_level"].get(str(policy.get("trust_level") or "inferred"), 0.5),
        "retention_scope": _POLICY_SCORE["retention_scope"].get(str(policy.get("retention_scope") or "project"), 0.5),
        "importance": _POLICY_SCORE["importance"].get(str(policy.get("importance") or "normal"), 0.5),
        "approval_state": _POLICY_SCORE["approval_state"].get(str(policy.get("approval_state") or "not_required"), 0.75),
    }
    score = (parts["memory_state"] * 0.28) + (parts["trust_level"] * 0.25) + (parts["retention_scope"] * 0.16) + (parts["importance"] * 0.18) + (parts["approval_state"] * 0.13)
    return round(max(0.0, min(1.0, score)), 6)



def decay_policy_rules_payload() -> dict[str, Any]:
    return {
        "schema_id": RETENTION_AUTOMATION_SCHEMA_ID,
        "version": MEMORY_POLICY_VERSION,
        "status": "ready",
        "allowed_actions": list(RETENTION_ACTIONS),
        "rules": deepcopy(_DECAY_POLICY_RULES),
        "policy": "Decay/retention automation is advisory and review-gated. Canon/system/source-backed memory is preserved; low-trust, stale, temporary, and external memory is surfaced for Admin review before mutation.",
    }


def retention_rule_for_policy(policy: dict[str, Any]) -> dict[str, Any]:
    decay_policy = str((policy or {}).get("decay_policy") or "soft_decay")
    rule = deepcopy(_DECAY_POLICY_RULES.get(decay_policy) or _DECAY_POLICY_RULES["soft_decay"])
    ttl = (policy or {}).get("ttl_days")
    if ttl not in (None, ""):
        try:
            rule["default_ttl_days"] = int(ttl)
            rule["candidate_after_days"] = int(ttl)
        except Exception:
            pass
    rule["decay_policy"] = decay_policy
    return rule


def memory_policy_payload() -> dict[str, Any]:
    return {
        "schema_id": MEMORY_POLICY_SCHEMA_ID,
        "version": MEMORY_POLICY_VERSION,
        "status": "ready",
        "allowed": {
            "retention_scopes": list(RETENTION_SCOPES),
            "memory_states": list(MEMORY_STATES),
            "visibility_levels": list(VISIBILITY_LEVELS),
            "trust_levels": list(TRUST_LEVELS),
            "importance_levels": list(IMPORTANCE_LEVELS),
            "approval_states": list(APPROVAL_STATES),
        },
        "source_defaults": {source_id: normalize_memory_policy(source_id) for source_id in sorted(_DEFAULT_POLICY_BY_SOURCE)},
        "retention_automation": decay_policy_rules_payload(),
        "policy": "Memory Engine policy metadata controls retrieval trust, canon/draft handling, visibility, retention, and low-confidence filtering. SQLite chunks remain authoritative; Chroma mirrors policy metadata when available.",
    }
