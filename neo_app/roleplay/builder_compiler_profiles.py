from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from neo_app.roleplay.builder_kind_templates import KIND_TEMPLATE_REQUIREMENTS, first_class_builder_templates_contract_payload
from neo_app.roleplay.forge import CANONICAL_TEMPLATE_KINDS, HIERARCHY
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root

PROFILE_VERSION: Final[str] = "17.5B-first-class-compiler-profiles-v1"
PROFILE_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "first_class_builder_compiler_profiles_contract.json"


@dataclass(frozen=True)
class CompileClassification:
    kind: str
    memory_type: str
    scene_category: str
    priority: str
    semantic_role: str
    strong_profile: bool
    matched_rule: str
    target_memory_types: tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _contains(path: str, *tokens: str) -> bool:
    p = path.lower()
    return any(token in p for token in tokens)


# Ordered rules. First match wins. Keep these deterministic: they are compiler behavior, not prompt vibes.
KIND_COMPILER_PROFILES: Final[dict[str, dict[str, Any]]] = {
    "universe": {
        "default_memory_type": "universe_law",
        "scene_category": "universe_context",
        "rules": [
            ("canon_guard", "global canon guard", ("law", "rule", "forbidden", "taboo", "limits", "canon_guard", "suppressed")),
            ("timeline_event", "cosmic chronology", ("timeline", "era", "chronology", "turning_point", "origin", "cycle")),
            ("reveal_gate", "cosmic reveal gate", ("hidden", "truth", "reveal", "secret", "prophecy")),
            ("universe_law", "cosmology/system law", ("cosmology", "cross_world", "world_order", "myth", "symbolic", "scope")),
        ],
    },
    "world": {
        "default_memory_type": "world_lore",
        "scene_category": "world_context",
        "rules": [
            ("canon_guard", "world canon guard", ("law", "rule", "forbidden", "taboo", "access_rules", "power_access", "magic_law")),
            ("timeline_event", "world chronology", ("calendar", "timeline", "era", "history", "turning_points")),
            ("world_lore", "geography/environment", ("geography", "environment", "climate", "terrain", "waters", "routes", "resource")),
            ("world_lore", "society/culture", ("society", "culture", "faith", "economy", "language", "peoples", "species")),
            ("reveal_gate", "world hidden truth", ("myths_truths", "hidden", "suppressed", "who_preserves")),
        ],
    },
    "region": {
        "default_memory_type": "region_pressure",
        "scene_category": "region_context",
        "rules": [
            ("canon_guard", "regional law/guard", ("law", "taboo", "restriction", "entry_conditions", "checkpoint")),
            ("political_pressure", "regional politics", ("governance", "ruling", "political", "diplomacy", "conflict", "rebellion", "claims")),
            ("region_pressure", "travel/border pressure", ("border", "travel", "security", "fortification", "military", "risk")),
            ("world_lore", "regional lore/culture", ("culture", "faith", "economy", "mythic", "hidden_legacy", "oral")),
        ],
    },
    "city": {
        "default_memory_type": "city_context",
        "scene_category": "city_context",
        "rules": [
            ("scene_rule", "city access/safety rule", ("curfew", "entry", "restricted", "danger_zones", "guarded", "safe_zones")),
            ("city_context", "city layout", ("layout", "district", "market", "notable_areas", "infrastructure", "hidden_paths")),
            ("political_pressure", "city control", ("governance", "guard", "watch", "tax", "justice", "factions", "oversight")),
            ("city_context", "local culture", ("society", "culture", "fashion", "food", "nightlife", "outsider")),
            ("reveal_gate", "city rumor/hidden truth", ("rumor", "hidden", "suppressed", "secret", "mythic")),
        ],
    },
    "location": {
        "default_memory_type": "location_context",
        "scene_category": "location_context",
        "rules": [
            ("scene_rule", "location access rule", ("access", "restricted", "entry", "locked", "guarded", "safe", "danger", "hazard")),
            ("location_context", "scene environment", ("sensory", "atmosphere", "layout", "lighting", "sound", "smell", "weather", "terrain")),
            ("callback_anchor", "location callback", ("callback", "recurring", "omen", "anchor")),
            ("reveal_gate", "location secret", ("hidden", "secret", "truth", "suppressed", "reveal")),
        ],
    },
    "character": {
        "default_memory_type": "character_profile",
        "scene_category": "character_context",
        "rules": [
            ("self_belief", "character belief", ("belief", "worldview", "moral", "faith", "self_justification")),
            ("character_profile", "character identity/presence", ("identity", "appearance", "age_timeline", "personality", "speech", "goals", "wounds", "skills", "family")),
            ("canon_guard", "character boundary/secret guard", ("taboo", "forbidden", "roleplay_dos_and_donts", "secrets", "hidden_truth")),
            ("relationship_state", "character relationship context", ("relationship", "romance", "friendship", "rivalry", "attachment")),
            ("callback_anchor", "character callback", ("memory_anchor", "sensory_anchor", "callback_anchor", "recurring")),
        ],
    },
    "organization": {
        "default_memory_type": "organization_lore",
        "scene_category": "organization_context",
        "rules": [
            ("political_pressure", "organization agenda/conflict", ("agenda", "mission", "political", "rival", "enemy", "pressure", "influence", "control")),
            ("organization_lore", "organization structure", ("hierarchy", "membership", "recruitment", "resources", "territory", "public", "hidden")),
            ("canon_guard", "organization taboo/law", ("taboo", "law", "forbidden", "restriction", "oath", "rule")),
            ("relationship_state", "organization relationships", ("allies", "rivals", "enemies", "members", "local_role")),
        ],
    },
    "artifact": {
        "default_memory_type": "artifact_lore",
        "scene_category": "artifact_context",
        "rules": [
            ("artifact_rule", "artifact power/limit rule", ("function", "effect", "use", "limits", "drawback", "cost", "activation", "safety")),
            ("canon_guard", "artifact restriction/curse", ("law", "restriction", "forbidden", "curse", "danger", "taboo")),
            ("artifact_lore", "artifact provenance/ownership", ("origin", "provenance", "ownership", "holder", "classification", "state")),
            ("scene_rule", "artifact scene trigger", ("scene_utility", "trigger", "roleplay", "best_scene", "use_notes")),
            ("reveal_gate", "artifact hidden truth", ("hidden", "secret", "truth", "public_hidden")),
        ],
    },
    "ritual": {
        "default_memory_type": "ritual_lore",
        "scene_category": "ritual_context",
        "rules": [
            ("ritual_rule", "ritual requirements/procedure", ("requirements", "conditions", "activation", "procedure", "steps", "transmission")),
            ("forbidden_rule", "ritual forbidden practice", ("forbidden", "taboo", "law", "ethics", "restriction")),
            ("canon_guard", "ritual cost/consequence", ("cost", "risk", "consequence", "danger", "failure")),
            ("ritual_lore", "ritual tradition/effect", ("tradition", "source", "function", "effects", "school", "classification")),
            ("reveal_gate", "ritual hidden truth", ("hidden", "secret", "truth", "public_hidden")),
        ],
    },
    "cycle": {
        "default_memory_type": "system_rule",
        "scene_category": "system_context",
        "rules": [
            ("recurrence_anchor", "cycle recurrence", ("trigger", "cadence", "onset", "recurrence", "season", "phase", "cycle")),
            ("system_rule", "system stages/effects", ("stages", "progression", "effects", "outcomes", "safeguards", "resistance")),
            ("canon_guard", "system law/ethics", ("law", "ethics", "cultural_handling", "forbidden", "taboo")),
            ("timeline_event", "system chronology", ("timeline", "historical", "era", "event")),
            ("reveal_gate", "system hidden truth", ("hidden", "secret", "truth", "public_hidden")),
        ],
    },
    "creature": {
        "default_memory_type": "creature_lore",
        "scene_category": "creature_context",
        "rules": [
            ("danger_rule", "creature danger behavior", ("danger", "threat", "attack", "weakness", "hazard", "risk")),
            ("creature_lore", "creature habitat/physicality", ("habitat", "range", "appearance", "physicality", "diet", "behavior", "instinct")),
            ("scene_rule", "creature scene use", ("scene", "utility", "role_in_world", "society_scene")),
            ("world_lore", "creature myth/system trait", ("magic", "special_traits", "public_hidden", "myth", "hidden")),
        ],
    },
    "legend": {
        "default_memory_type": "legend_lore",
        "scene_category": "legend_context",
        "rules": [
            ("hidden_truth", "legend hidden version", ("hidden", "truth", "secret", "suppressed", "contradiction")),
            ("reveal_gate", "legend reveal gate", ("reveal", "who_knows", "keepers", "suppressors", "evidence", "fragments")),
            ("foreshadowing_anchor", "legend foreshadowing", ("omen", "prophecy", "foreshadow", "symbol", "motif")),
            ("legend_lore", "public legend texture", ("public", "story", "cultural", "scope", "linked_entities", "consequences")),
        ],
    },
    "scenario": {
        "default_memory_type": "scenario_pressure",
        "scene_category": "scenario_context",
        "rules": [
            ("scene_rule", "scenario constraint", ("constraint", "forbidden", "allowed_escalation", "violence", "intimacy", "canon_guard")),
            ("scenario_pressure", "premise/stakes pressure", ("premise", "objective", "stakes", "failure", "success", "why_now", "immediate_pressure")),
            ("reveal_gate", "scenario secret/reveal", ("hidden", "secret", "twist", "reveal", "misdirection")),
            ("relationship_state", "scenario relationship pressure", ("relationship", "romance", "tension", "cast_roles")),
        ],
    },
    "relationship": {
        "default_memory_type": "relationship_state",
        "scene_category": "relationship_context",
        "rules": [
            ("romance_boundary", "relationship intimacy/consent boundary", ("romance_boundaries", "intimacy", "consent", "forbidden_escalations", "allowed_tone")),
            ("relationship_belief", "relationship emotional logic", ("emotional_logic", "trust", "attachment", "jealousy", "comfort", "repair", "conflict_language")),
            ("relationship_state", "relationship history/current state", ("history", "participants", "current_state", "dynamic", "bond", "turning_points")),
            ("callback_anchor", "relationship callback", ("callback", "shared_wounds", "quiet_moment", "memory", "anchor")),
            ("reveal_gate", "relationship secret/reveal", ("secret", "lie", "hidden", "who_knows")),
        ],
    },
}

# Any exact memory types that should be treated as important in the write path.
HIGH_PRIORITY_MEMORY_TYPES: Final[set[str]] = {
    "canon_guard", "scene_rule", "relationship_state", "relationship_belief", "romance_boundary",
    "artifact_rule", "ritual_rule", "forbidden_rule", "system_rule", "danger_rule",
    "reveal_gate", "hidden_truth", "political_pressure", "scenario_pressure",
}


def target_memory_types_for_kind(kind: str) -> tuple[str, ...]:
    template_profile = KIND_TEMPLATE_REQUIREMENTS.get(_clean(kind).lower(), {})
    targets = template_profile.get("memory_targets") if isinstance(template_profile, dict) else []
    compiler_profile = KIND_COMPILER_PROFILES.get(_clean(kind).lower(), {})
    default_type = _clean(compiler_profile.get("default_memory_type"))
    rules = compiler_profile.get("rules") if isinstance(compiler_profile.get("rules"), list) else []
    values: list[str] = []
    for value in list(targets or []) + ([default_type] if default_type else []) + [rule[0] for rule in rules if rule]:
        clean = _clean(value)
        if clean and clean not in values:
            values.append(clean)
    return tuple(values or ["semantic_fact"])


def scene_category_for_kind(kind: str) -> str:
    clean = _clean(kind).lower()
    profile = KIND_COMPILER_PROFILES.get(clean, {})
    return _clean(profile.get("scene_category")) or _clean(KIND_TEMPLATE_REQUIREMENTS.get(clean, {}).get("scene_category")) or "generic_context"


def classify_builder_path(kind: str, path: str, value: Any | None = None) -> CompileClassification:
    clean_kind = _clean(kind).lower() or "unknown"
    clean_path = _clean(path).lower()
    profile = KIND_COMPILER_PROFILES.get(clean_kind)
    target_types = target_memory_types_for_kind(clean_kind)
    scene_category = scene_category_for_kind(clean_kind)
    if not profile:
        return CompileClassification(
            kind=clean_kind,
            memory_type="semantic_fact",
            scene_category=scene_category,
            priority="normal",
            semantic_role="generic fallback",
            strong_profile=False,
            matched_rule="fallback.semantic_fact",
            target_memory_types=target_types,
        )

    # Universal overrides that should beat kind defaults.
    if _contains(clean_path, "callback_anchor", "callback_anchors", "memory_anchor", "sensory_anchor"):
        memory_type, role, matched = "callback_anchor", "callback/anchor", "universal.callback_anchor"
    elif _contains(clean_path, "reveal_gating", "reveal_order", "truth_reveal", "who_knows", "hidden_truth"):
        memory_type, role, matched = "reveal_gate", "reveal/knowledge gate", "universal.reveal_gate"
    elif _contains(clean_path, "taboo", "forbidden", "canon_guard"):
        memory_type, role, matched = "canon_guard", "canon/safety guard", "universal.canon_guard"
    elif _contains(clean_path, "summary", "identity", "known_for", "tagline"):
        memory_type = _clean(profile.get("default_memory_type")) or "semantic_fact"
        role, matched = "identity/summary", "universal.identity"
    else:
        memory_type = _clean(profile.get("default_memory_type")) or "semantic_fact"
        role = "default profile memory"
        matched = f"{clean_kind}.default"
        for rule_memory_type, rule_role, tokens in profile.get("rules", []):
            if _contains(clean_path, *tokens):
                memory_type = _clean(rule_memory_type) or memory_type
                role = _clean(rule_role) or role
                matched = f"{clean_kind}.{memory_type}"
                break

    priority = "high" if memory_type in HIGH_PRIORITY_MEMORY_TYPES or _contains(clean_path, "summary", "identity", "known_for", "premise", "objective", "stakes") else "normal"
    return CompileClassification(
        kind=clean_kind,
        memory_type=memory_type,
        scene_category=scene_category,
        priority=priority,
        semantic_role=role,
        strong_profile=True,
        matched_rule=matched,
        target_memory_types=target_types,
    )


def compiler_profiles_contract_payload(*, write_report: bool = True) -> dict[str, Any]:
    template_contract = first_class_builder_templates_contract_payload(write_report=False)
    entries: list[dict[str, Any]] = []
    for kind in CANONICAL_TEMPLATE_KINDS:
        profile = KIND_COMPILER_PROFILES.get(kind, {})
        rules = profile.get("rules") if isinstance(profile.get("rules"), list) else []
        entries.append({
            "kind": kind,
            "display_name": HIERARCHY.get(kind, {}).get("display_name", kind.title()),
            "status": "strong" if profile else "missing_profile",
            "scene_category": scene_category_for_kind(kind),
            "default_memory_type": profile.get("default_memory_type", "semantic_fact"),
            "target_memory_types": list(target_memory_types_for_kind(kind)),
            "rule_count": len(rules),
            "rules": [
                {"memory_type": rule[0], "semantic_role": rule[1], "tokens": list(rule[2])}
                for rule in rules
            ],
        })
    strong_count = sum(1 for entry in entries if entry.get("status") == "strong")
    payload = {
        "schema_id": "neo.roleplay.builder_compiler_profiles.contract.v1",
        "phase": PROFILE_VERSION,
        "status": "ready" if strong_count == len(entries) else "ready_with_profile_gaps",
        "generated_at": _now(),
        "rule": "Every canonical Forge Builder kind has a first-class compiler profile. Generic fallback is emergency-only.",
        "template_contract_status": template_contract.get("status"),
        "canonical_kind_count": len(entries),
        "strong_profile_count": strong_count,
        "profiles": entries,
    }
    if write_report:
        PROFILE_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_CONTRACT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def compiler_profiles_state_payload() -> dict[str, Any]:
    contract = compiler_profiles_contract_payload(write_report=False)
    return {
        "schema_id": "neo.roleplay.builder_compiler_profiles.state.v1",
        "phase": PROFILE_VERSION,
        "status": contract.get("status"),
        "generated_at": _now(),
        "contract_path": _relative_to_root(PROFILE_CONTRACT_PATH),
        "profiles_by_kind": {entry["kind"]: entry for entry in contract.get("profiles", [])},
        "contract": contract,
    }


def ensure_compiler_profiles_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    contract = compiler_profiles_contract_payload(write_report=bool(payload.get("write_report", True)))
    missing = [profile for profile in contract.get("profiles", []) if profile.get("status") != "strong"]
    return {
        "schema_id": "neo.roleplay.builder_compiler_profiles.ensure.v1",
        "phase": PROFILE_VERSION,
        "status": "ready" if not missing else "ready_with_profile_gaps",
        "message": "First-class Builder compiler profiles verified. Existing memory rows are not modified until records are recompiled.",
        "missing_profiles": missing,
        "contract": contract,
    }
