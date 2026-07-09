from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Final

from neo_app.roleplay.builder_compiler_profiles import KIND_COMPILER_PROFILES, scene_category_for_kind, target_memory_types_for_kind
from neo_app.roleplay.forge import CANONICAL_TEMPLATE_KINDS, HIERARCHY
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root

PHASE17_5C_VERSION: Final[str] = "17.5C-scene-packet-categories-all-kinds-v1"
PHASE17_5C_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "scene_packet_categories_contract.json"

# Ordered scene-packet sections. These are prompt packet lanes, not UI-only labels.
SCENE_PACKET_CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "universe_context",
    "world_context",
    "region_context",
    "city_context",
    "location_context",
    "character_context",
    "relationship_context",
    "organization_context",
    "artifact_context",
    "ritual_context",
    "system_context",
    "creature_context",
    "legend_context",
    "scenario_context",
    "canon_guards",
    "reveal_gates",
    "callback_anchors",
    "continuity_rows",
    "retrieved_memory",
)

KIND_TO_PACKET_CATEGORY: Final[dict[str, str]] = {
    "universe": "universe_context",
    "world": "world_context",
    "region": "region_context",
    "city": "city_context",
    "location": "location_context",
    "character": "character_context",
    "relationship": "relationship_context",
    "organization": "organization_context",
    "artifact": "artifact_context",
    "ritual": "ritual_context",
    "cycle": "system_context",
    "creature": "creature_context",
    "legend": "legend_context",
    "scenario": "scenario_context",
}

MEMORY_TYPE_TO_PACKET_CATEGORY: Final[dict[str, str]] = {
    "universe_law": "universe_context",
    "world_lore": "world_context",
    "region_pressure": "region_context",
    "city_context": "city_context",
    "location_context": "location_context",
    "character_profile": "character_context",
    "self_belief": "character_context",
    "relationship_state": "relationship_context",
    "relationship_belief": "relationship_context",
    "romance_boundary": "relationship_context",
    "organization_lore": "organization_context",
    "political_pressure": "organization_context",
    "artifact_lore": "artifact_context",
    "artifact_rule": "artifact_context",
    "ritual_lore": "ritual_context",
    "ritual_rule": "ritual_context",
    "forbidden_rule": "ritual_context",
    "system_rule": "system_context",
    "recurrence_anchor": "system_context",
    "creature_lore": "creature_context",
    "danger_rule": "creature_context",
    "legend_lore": "legend_context",
    "hidden_truth": "legend_context",
    "foreshadowing_anchor": "legend_context",
    "scenario_pressure": "scenario_context",
    "scene_rule": "canon_guards",
    "canon_guard": "canon_guards",
    "reveal_gate": "reveal_gates",
    "callback_anchor": "callback_anchors",
    "continuity_rule": "continuity_rows",
    "timeline_event": "world_context",
}

SECTION_LABELS: Final[dict[str, str]] = {
    "universe_context": "Universe context",
    "world_context": "World context",
    "region_context": "Region / kingdom context",
    "city_context": "City / settlement context",
    "location_context": "Location context",
    "character_context": "Character context",
    "relationship_context": "Relationship context",
    "organization_context": "Organization context",
    "artifact_context": "Artifact context",
    "ritual_context": "Ritual / practice context",
    "system_context": "Cycle / system context",
    "creature_context": "Creature context",
    "legend_context": "Legend context",
    "scenario_context": "Scenario context",
    "canon_guards": "Canon guards",
    "reveal_gates": "Reveal gates",
    "callback_anchors": "Callback anchors",
    "continuity_rows": "Continuity rows",
    "retrieved_memory": "Other retrieved memory",
}

SECTION_PURPOSES: Final[dict[str, str]] = {
    "universe_context": "Cosmology, cross-world laws, global timelines, and universe-scale canon.",
    "world_context": "World-level setting, society, environment, magic/tech rules, and global lore.",
    "region_context": "Kingdom politics, borders, travel pressure, diplomacy, and regional conflict.",
    "city_context": "Settlement layout, districts, local culture, curfew, safety, and street-level pressure.",
    "location_context": "Immediate scene geography, access rules, hazards, sensory grounding, and hidden paths.",
    "character_context": "NPC/player profiles, speech, wounds, beliefs, abilities, secrets, and behavior rules.",
    "relationship_context": "Relationship dynamics, romantic boundaries, trust, repair patterns, and shared history.",
    "organization_context": "Factions, agendas, hierarchy, influence, public/private faces, and political pressure.",
    "artifact_context": "Object powers, limits, costs, ownership, curses, and scene triggers.",
    "ritual_context": "Procedures, requirements, taboo practices, costs, failure outcomes, and tradition.",
    "system_context": "Recurring cycles, stages, phase rules, social/magic systems, and recurrence anchors.",
    "creature_context": "Creature behavior, danger logic, habitat, weaknesses, and mythic role.",
    "legend_context": "Public myths, hidden truths, reveal gates, omens, and foreshadowing.",
    "scenario_context": "Scene premise, objectives, stakes, pressure, cast roles, and opening beats.",
    "canon_guards": "Hard rules, boundaries, and constraints the model must not violate.",
    "reveal_gates": "Secrets and truths that require staged reveal timing.",
    "callback_anchors": "Recurring phrases, symbols, sensory anchors, omens, and memory callbacks.",
    "continuity_rows": "Prior continuity, unresolved threads, and tracked scene/state changes.",
    "retrieved_memory": "Useful records that do not fit a stronger packet section.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def category_for_kind(kind: str) -> str:
    clean = _clean(kind).lower()
    return KIND_TO_PACKET_CATEGORY.get(clean) or scene_category_for_kind(clean) or "retrieved_memory"


def category_for_memory_type(memory_type: str, kind: str = "", scene_category: str = "") -> str:
    clean_scene_category = _clean(scene_category)
    if clean_scene_category in SCENE_PACKET_CATEGORY_ORDER:
        return clean_scene_category
    clean_memory_type = _clean(memory_type).lower()
    if clean_memory_type in MEMORY_TYPE_TO_PACKET_CATEGORY:
        return MEMORY_TYPE_TO_PACKET_CATEGORY[clean_memory_type]
    clean_kind = _clean(kind).lower()
    if clean_kind:
        return category_for_kind(clean_kind)
    return "retrieved_memory"


def empty_scene_packet_categories() -> dict[str, list[dict[str, Any]]]:
    return {category: [] for category in SCENE_PACKET_CATEGORY_ORDER}


def scene_packet_categories_contract_payload(*, write_report: bool = True) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for kind in CANONICAL_TEMPLATE_KINDS:
        category = category_for_kind(kind)
        profile = KIND_COMPILER_PROFILES.get(kind, {})
        entries.append({
            "kind": kind,
            "display_name": HIERARCHY.get(kind, {}).get("display_name", kind.title()),
            "status": "strong" if category in SCENE_PACKET_CATEGORY_ORDER else "missing_category",
            "scene_packet_category": category,
            "default_memory_type": profile.get("default_memory_type", "semantic_fact"),
            "target_memory_types": list(target_memory_types_for_kind(kind)),
        })
    strong_count = sum(1 for entry in entries if entry.get("status") == "strong")
    payload = {
        "schema_id": "neo.roleplay.scene_packet_categories.contract.v1",
        "phase": PHASE17_5C_VERSION,
        "status": "ready" if strong_count == len(entries) else "ready_with_category_gaps",
        "generated_at": _now(),
        "rule": "Every canonical Forge Builder kind must land in a first-class Scene Packet category. Generic retrieved_memory is fallback-only.",
        "category_order": list(SCENE_PACKET_CATEGORY_ORDER),
        "section_labels": SECTION_LABELS,
        "section_purposes": SECTION_PURPOSES,
        "kind_categories": entries,
        "memory_type_categories": MEMORY_TYPE_TO_PACKET_CATEGORY,
        "strong_category_count": strong_count,
        "canonical_kind_count": len(entries),
    }
    if write_report:
        PHASE17_5C_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE17_5C_CONTRACT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def scene_packet_categories_state_payload() -> dict[str, Any]:
    contract = scene_packet_categories_contract_payload(write_report=False)
    return {
        "schema_id": "neo.roleplay.scene_packet_categories.state.v1",
        "phase": PHASE17_5C_VERSION,
        "status": contract.get("status"),
        "generated_at": _now(),
        "contract_path": _relative_to_root(PHASE17_5C_CONTRACT_PATH),
        "category_count": len(SCENE_PACKET_CATEGORY_ORDER),
        "kind_count": len(contract.get("kind_categories") or []),
        "categories": [
            {"key": key, "label": SECTION_LABELS.get(key, key), "purpose": SECTION_PURPOSES.get(key, "")}
            for key in SCENE_PACKET_CATEGORY_ORDER
        ],
        "contract": contract,
    }


def ensure_scene_packet_categories_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    contract = scene_packet_categories_contract_payload(write_report=bool(payload.get("write_report", True)))
    missing = [entry for entry in contract.get("kind_categories", []) if entry.get("status") != "strong"]
    return {
        "schema_id": "neo.roleplay.scene_packet_categories.ensure.v1",
        "phase": PHASE17_5C_VERSION,
        "status": "ready" if not missing else "ready_with_category_gaps",
        "message": "First-class Scene Packet categories verified for all canonical Builder kinds.",
        "missing_categories": missing,
        "contract": contract,
    }
