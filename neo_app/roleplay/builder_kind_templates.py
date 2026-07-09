from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.forge import (
    CANONICAL_TEMPLATE_KINDS,
    HIERARCHY,
    JSON_TEMPLATE_ROOT,
    MD_TEMPLATE_ROOT,
    builder_templates_by_kind,
    load_builder_template,
)
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation

FIRST_CLASS_TEMPLATE_VERSION: Final[str] = "17.5A"

# Required sections define whether a kind is now a first-class Builder lane.
# The Phase 17.5B compiler profiles will use these same section names as mapping anchors.
KIND_TEMPLATE_REQUIREMENTS: Final[dict[str, dict[str, Any]]] = {
    "universe": {
        "scene_category": "universe_context",
        "memory_targets": ["universe_law", "canon_guard", "timeline_event", "world_lore"],
        "required_sections": ["identity", "cosmology", "core_laws", "timeline", "world_scope", "truth_layers", "rich_authoring"],
        "required_scope_keys": ["universe_id"],
    },
    "world": {
        "scene_category": "world_context",
        "memory_targets": ["world_lore", "canon_guard", "timeline_event", "system_rule"],
        "required_sections": ["identity", "calendar_chronology", "geography_environment", "governance_law_diplomacy", "society_institutions", "faith_magic_craft", "myths_truths", "rich_authoring"],
        "required_scope_keys": ["universe_id"],
    },
    "region": {
        "scene_category": "region_context",
        "memory_targets": ["region_pressure", "world_lore", "political_pressure", "canon_guard"],
        "required_sections": ["identity", "placement_scope", "governance_ruling_power", "geography_places", "politics_law_diplomacy", "society_culture_education", "mythic_hidden_legacy", "rich_authoring"],
        "required_scope_keys": ["world_id", "parent_region_id", "capital_city_id"],
    },
    "city": {
        "scene_category": "city_context",
        "memory_targets": ["city_context", "location_context", "scene_rule", "political_pressure"],
        "required_sections": ["identity", "placement_access", "governance_control", "layout_districts", "access_safety_restrictions", "society_local_culture", "rumors_truths", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "parent_city_id"],
    },
    "location": {
        "scene_category": "location_context",
        "memory_targets": ["location_context", "scene_rule", "hazard_fact", "reveal_gate"],
        "required_sections": ["identity", "placement_scope", "access_entry", "spatial_layout", "atmosphere_sensory", "rules_behavior_logic", "hazards_pressure", "public_hidden_truth", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "parent_location_id"],
    },
    "character": {
        "scene_category": "character_context",
        "memory_targets": ["character_profile", "self_belief", "relationship_state", "callback_anchor"],
        "required_sections": ["identity", "placement_world_graph", "age_timeline", "appearance_presence", "personality_behavior_speech", "beliefs_morality", "goals_desire_fear_wounds", "secrets_lies_hidden_truth", "story_roleplay_use", "rich_authoring"],
        "required_scope_keys": ["origin_world_id", "current_world_id", "current_region_id", "current_city_id", "current_location_id"],
    },
    "organization": {
        "scene_category": "organization_context",
        "memory_targets": ["organization_lore", "faction_lore", "political_pressure", "relationship_state"],
        "required_sections": ["identity", "placement_scope", "leadership_structure", "beliefs_doctrine_mission", "public_hidden_truth", "membership_recruitment", "resources_assets_territory", "allies_rivals_enemies", "local_role_influence_pressure", "story_roleplay_use", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id"],
    },
    "artifact": {
        "scene_category": "artifact_context",
        "memory_targets": ["artifact_rule", "artifact_lore", "canon_guard", "scene_rule"],
        "required_sections": ["identity", "classification_state", "placement_ownership", "origin_provenance", "appearance_presence", "function_effects_use", "law_safety_restriction", "dangers_pressure", "public_hidden_truth", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id", "current_holder_character_id"],
    },
    "ritual": {
        "scene_category": "ritual_context",
        "memory_targets": ["ritual_rule", "ritual_lore", "forbidden_rule", "canon_guard"],
        "required_sections": ["identity", "classification_school_state", "placement_source_tradition", "function_effects", "requirements_conditions", "activation_procedure", "risks_costs_consequences", "law_ethics_restriction", "users_access_transmission", "public_hidden_truth", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id", "organization_id"],
    },
    "cycle": {
        "scene_category": "system_context",
        "memory_targets": ["system_rule", "recurrence_anchor", "timeline_event", "canon_guard"],
        "required_sections": ["identity", "scope_reach", "affected_targets", "trigger_cadence_onset", "stages_progression", "effects_outcomes", "safeguards_resistance_management", "law_ethics_cultural_handling", "public_hidden_truth", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id"],
    },
    "creature": {
        "scene_category": "creature_context",
        "memory_targets": ["creature_lore", "danger_rule", "world_lore", "scene_rule"],
        "required_sections": ["identity", "placement_habitat_range", "sentience_social_pattern", "physicality_appearance_presence", "diet_behavior_instinct", "danger_threat_utility", "magic_system_special_traits", "public_hidden_truth", "role_in_world_society_scene", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id"],
    },
    "legend": {
        "scene_category": "legend_context",
        "memory_targets": ["legend_lore", "hidden_truth", "reveal_gate", "foreshadowing_anchor"],
        "required_sections": ["identity", "scope_placement_anchor", "public_hidden_versions", "believers_suppressors_keepers", "linked_entities_forces", "consequences_stakes", "cultural_role_function", "evidence_fragments_contradictions", "public_story_texture", "scene_utility", "rich_authoring"],
        "required_scope_keys": ["world_id", "region_id", "city_id", "location_id"],
    },
    "scenario": {
        "scene_category": "scenario_context",
        "memory_targets": ["scenario_pressure", "scene_rule", "canon_guard", "reveal_gate"],
        "required_sections": ["identity", "placement_scene_anchor", "premise_objective_stakes", "opening_state_trigger_beat", "cast_roles_pov", "forces_in_play", "tone_emotional_logic_pressure", "constraints_rules_boundaries", "hidden_layer_secrets_twist", "scene_runtime_use", "rich_authoring"],
        "required_scope_keys": ["universe_id", "world_id", "region_id", "city_id", "location_id"],
    },
    "relationship": {
        "scene_category": "relationship_context",
        "memory_targets": ["relationship_state", "relationship_belief", "romance_boundary", "callback_anchor"],
        "required_sections": ["identity", "participants", "history", "emotional_logic", "romance_boundaries", "secrets_lies_hidden_truth", "current_state", "scene_use", "rich_authoring"],
        "required_scope_keys": ["origin_world_id", "current_world_id", "current_region_id", "current_city_id", "current_location_id"],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_report(name: str, payload: dict[str, Any]) -> None:
    ROLEPLAY_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (ROLEPLAY_DATA_ROOT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _scope_keys(payload: dict[str, Any]) -> set[str]:
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    scope = links.get("scope") if isinstance(links.get("scope"), dict) else {}
    return {str(key) for key in scope.keys()}


def validate_first_class_template(kind: str) -> dict[str, Any]:
    template_kind = str(kind or "").strip().lower()
    req = KIND_TEMPLATE_REQUIREMENTS.get(template_kind, {})
    json_path = JSON_TEMPLATE_ROOT / f"{template_kind}.template.json"
    md_path = MD_TEMPLATE_ROOT / f"{template_kind}.template.md"
    payload = _read_json(json_path)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    missing_sections = [section for section in req.get("required_sections", []) if section not in fields]
    present_scope_keys = _scope_keys(payload)
    missing_scope_keys = [key for key in req.get("required_scope_keys", []) if key not in present_scope_keys]
    missing_top = [key for key in ["id", "kind", "label", "summary", "links", "fields", "memory_hints", "meta"] if key not in payload]
    status = "ready" if json_path.exists() and md_path.exists() and not missing_sections and not missing_top else "needs_attention"
    return {
        "kind": template_kind,
        "display_name": HIERARCHY.get(template_kind, {}).get("display_name", template_kind.title()),
        "status": status,
        "json_template": _relative_to_root(json_path) if json_path.exists() else "missing",
        "md_template": _relative_to_root(md_path) if md_path.exists() else "missing",
        "scene_category": req.get("scene_category", "generic_context"),
        "memory_targets": req.get("memory_targets", []),
        "required_sections": req.get("required_sections", []),
        "present_sections": sorted(fields.keys()),
        "missing_sections": missing_sections,
        "required_scope_keys": req.get("required_scope_keys", []),
        "present_scope_keys": sorted(present_scope_keys),
        "missing_scope_keys": missing_scope_keys,
        "missing_top_level_keys": missing_top,
        "field_path_count": len(load_builder_template(template_kind).get("field_paths", [])) if json_path.exists() else 0,
    }


def first_class_builder_templates_contract_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    entries = [validate_first_class_template(kind) for kind in CANONICAL_TEMPLATE_KINDS]
    ready = [entry for entry in entries if entry.get("status") == "ready"]
    payload = {
        "schema_id": "neo.roleplay.builder_templates.first_class.contract.v1",
        "phase": FIRST_CLASS_TEMPLATE_VERSION,
        "status": "ready" if len(ready) == len(entries) else "ready_with_template_gaps",
        "generated_at": _now(),
        "rule": "All canonical Forge Builder kinds are first-class template targets. Generic fallback is allowed only as an emergency compatibility path.",
        "template_count": len(entries),
        "ready_template_count": len(ready),
        "canonical_kinds": list(CANONICAL_TEMPLATE_KINDS),
        "templates": entries,
    }
    if write_report:
        _write_report("first_class_builder_template_contract.json", payload)
    return payload


def first_class_builder_templates_state_payload() -> dict[str, Any]:
    templates = builder_templates_by_kind()
    contract = first_class_builder_templates_contract_payload(write_report=False)
    by_kind = {}
    for kind in CANONICAL_TEMPLATE_KINDS:
        template = templates.get(kind, {})
        by_kind[kind] = {
            "display_name": template.get("display_name") or HIERARCHY.get(kind, {}).get("display_name", kind.title()),
            "template_kind": template.get("template_kind"),
            "field_path_count": len(template.get("field_paths", [])),
            "hierarchy": template.get("hierarchy", {}),
            "profile": KIND_TEMPLATE_REQUIREMENTS.get(kind, {}),
        }
    return {
        "schema_id": "neo.roleplay.builder_templates.first_class.state.v1",
        "phase": FIRST_CLASS_TEMPLATE_VERSION,
        "status": contract.get("status"),
        "generated_at": _now(),
        "template_root": _relative_to_root(JSON_TEMPLATE_ROOT.parent),
        "template_count": len(by_kind),
        "ready_template_count": contract.get("ready_template_count"),
        "templates_by_kind": by_kind,
        "contract": contract,
    }


def ensure_first_class_builder_templates_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    # Phase 17.5A intentionally does not overwrite existing rich templates.
    # It verifies all canonical lanes exist and writes the manifest used by 17.5B compiler profiles.
    payload = payload or {}
    write_report = bool(payload.get("write_report", True))
    contract = first_class_builder_templates_contract_payload(write_report=write_report)
    missing = [entry for entry in contract.get("templates", []) if entry.get("status") != "ready"]
    return {
        "schema_id": "neo.roleplay.builder_templates.first_class.ensure.v1",
        "phase": FIRST_CLASS_TEMPLATE_VERSION,
        "status": "ready" if not missing else "ready_with_template_gaps",
        "message": "First-class Builder template contract verified. Existing templates were not overwritten.",
        "missing_or_incomplete": missing,
        "contract": contract,
    }
