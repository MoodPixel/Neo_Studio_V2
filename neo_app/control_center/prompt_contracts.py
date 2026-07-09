from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROMPT_CONTRACT_PHASE = "M8"
PROMPT_CONTRACT_SCHEMA_ID = "neo.prompt_contracts.v1"


def _clean(value: Any, *, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


@dataclass(frozen=True, slots=True)
class PromptContract:
    contract_id: str
    label: str
    controller: str
    intent_family: str
    purpose: str
    input_lanes: tuple[str, ...] = field(default_factory=tuple)
    output_lanes: tuple[str, ...] = field(default_factory=tuple)
    hard_rules: tuple[str, ...] = field(default_factory=tuple)
    soft_rules: tuple[str, ...] = field(default_factory=tuple)
    validation_checks: tuple[str, ...] = field(default_factory=tuple)
    memory_policy: dict[str, Any] = field(default_factory=dict)
    writeback_policy: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_id": PROMPT_CONTRACT_SCHEMA_ID,
            "phase": PROMPT_CONTRACT_PHASE,
            "contract_id": self.contract_id,
            "label": self.label,
            "controller": self.controller,
            "intent_family": self.intent_family,
            "purpose": self.purpose,
            "input_lanes": list(self.input_lanes),
            "output_lanes": list(self.output_lanes),
            "hard_rules": list(self.hard_rules),
            "soft_rules": list(self.soft_rules),
            "validation_checks": list(self.validation_checks),
            "memory_policy": dict(self.memory_policy),
            "writeback_policy": dict(self.writeback_policy),
        }


CONTRACTS: dict[str, PromptContract] = {
    "assistant_workspace_advice_v1": PromptContract(
        contract_id="assistant_workspace_advice_v1",
        label="Assistant Workspace Advice",
        controller="assistant",
        intent_family="assistant.advice",
        purpose="Answer as the workspace-aware Neo Assistant using scoped project/surface memory and practical next steps.",
        input_lanes=("user_request", "active_project", "active_surface", "selected_scoped_memory", "tool_state"),
        output_lanes=("diagnosis", "recommendation", "next_steps", "uncertainty"),
        hard_rules=(
            "Use the active project/surface sandbox first; do not mix unrelated memories unless the user asks.",
            "Treat memory as contextual evidence, not absolute truth.",
            "Do not claim missing memory, unavailable tools, or unverified results as fact.",
            "Do not degrade the user's goal just because the direct path is harder; propose feasible right-path options.",
        ),
        soft_rules=("Be direct, practical, and system-aware.", "Mention which layer is uncertain when relevant: memory, retrieval, backend, tool, or user intent."),
        validation_checks=("answer_uses_active_surface_scope", "answer_marks_uncertainty_when_context_thin", "answer_has_actionable_next_step"),
        memory_policy={"send_all_memory": False, "preferred_sources": ["project_memory", "surface_memory", "recent_success_patterns"], "sandbox_required": True},
        writeback_policy={"auto_write_low_risk": ["control_trace", "retrieval_scope_used"], "review_required": ["new_durable_user_preference", "high_impact_project_fact"]},
    ),
    "assistant_project_memory_answer_v1": PromptContract(
        contract_id="assistant_project_memory_answer_v1",
        label="Assistant Project Memory Answer",
        controller="assistant",
        intent_family="assistant.answer",
        purpose="Answer user questions from scoped project memory without cross-project contamination.",
        input_lanes=("user_request", "project_context", "selected_memory", "source_metadata"),
        output_lanes=("answer", "evidence_summary", "missing_context", "next_step"),
        hard_rules=(
            "Use only the scoped project/surface memories unless the user explicitly asks for broader comparison.",
            "If memory is thin or missing, say what is missing instead of guessing.",
            "Keep raw metadata separate from assistant inference.",
            "Output lanes are internal planning lanes, not a required JSON response format.",
            "Final Assistant replies must be natural text unless the user explicitly requests JSON.",
        ),
        soft_rules=("Prefer concise explanations with concrete next actions.",),
        validation_checks=("no_unscoped_memory_mix", "uncertainty_when_missing", "source_scope_respected"),
        memory_policy={"send_all_memory": False, "sandbox_required": True, "max_context_items_default": 8},
        writeback_policy={"auto_write_low_risk": ["assistant_trace"], "review_required": ["new_project_fact"]},
    ),
    "roleplay_scene_turn_v1": PromptContract(
        contract_id="roleplay_scene_turn_v1",
        label="Roleplay Scene Turn",
        controller="roleplay",
        intent_family="roleplay.scene_turn",
        purpose="Continue an immersive scene as narrator/NPCs while preserving player agency and canon state.",
        input_lanes=("user_turn", "scene_state", "active_scene_packet", "player_control_contract", "npc_state", "canon_locks", "selected_memory"),
        output_lanes=("Narration", "NPC dialogue"),
        hard_rules=(
            "Do not write the player-controlled character's dialogue, thoughts, feelings, decisions, or physical actions unless co-writing is explicitly enabled.",
            "Do not invent appearances, injuries, facial expressions, physical positions, relationship status, or who holds an object unless present in packet/state/user turn.",
            "Use private NPC knowledge only to shape behavior; do not reveal it unless scene state allows it.",
            "Separate narration from character dialogue with clear lanes.",
            "If a detail is unknown, leave it unstated or mark it unknown rather than inventing.",
        ),
        soft_rules=("Keep the scene immersive and responsive.", "Use current scene tension and unresolved threads instead of dumping lore."),
        validation_checks=("no_player_control_violation", "no_unspecified_fact_invention", "dialogue_lanes_present", "canon_state_not_contradicted"),
        memory_policy={"send_all_memory": False, "required_lanes": ["scene_state", "player_control_contract", "canon_locks"], "optional_lanes": ["character_state", "relationship_state", "scoped_memory_fragments"]},
        writeback_policy={"planned": ["scene_event", "character_knowledge_delta", "relationship_delta", "unresolved_thread_delta"], "review_required": ["canon_change", "timeline_major_event"]},
    ),
    "roleplay_scene_summary_v1": PromptContract(
        contract_id="roleplay_scene_summary_v1",
        label="Roleplay Scene Canon Summary",
        controller="roleplay",
        intent_family="roleplay.canon_summary",
        purpose="Summarize only confirmed loaded scene packet canon and known scene state.",
        input_lanes=("active_scene_packet", "canon_locks", "scene_state", "character_state", "relationship_state"),
        output_lanes=("confirmed_facts", "not_specified", "constraints"),
        hard_rules=(
            "Do not summarize as cinematic narration.",
            "Do not invent appearances, gender, location details, relationships, injuries, emotions, or physical positions.",
            "If presence/knowledge/possession is not explicit, state that it is not specified.",
            "Separate confirmed packet facts from inference or missing information.",
        ),
        soft_rules=("Prefer compact bullet-style factual summaries.",),
        validation_checks=("summary_only_confirmed_facts", "unknowns_marked", "no_cinematic_fill"),
        memory_policy={"send_all_memory": False, "required_lanes": ["active_scene_packet", "canon_locks", "scene_state"]},
        writeback_policy={"auto_write_low_risk": ["summary_trace"], "review_required": []},
    ),
    "roleplay_scene_continue_v1": PromptContract(
        contract_id="roleplay_scene_continue_v1",
        label="Roleplay Scene Continue",
        controller="roleplay",
        intent_family="roleplay.scene_continue",
        purpose="Continue the scene from the latest state without introducing unearned new lore.",
        input_lanes=("recent_transcript", "scene_state", "npc_state", "unresolved_threads", "selected_memory"),
        output_lanes=("Narration", "NPC dialogue"),
        hard_rules=(
            "Continue from current scene state only.",
            "Do not move the player character or choose their response.",
            "Do not reveal hidden NPC knowledge without trigger.",
            "Keep continuity with recent transcript.",
        ),
        soft_rules=("Advance tension by one beat; do not resolve major conflicts too early.",),
        validation_checks=("continuity_with_recent_state", "no_player_control_violation", "reveal_timing_safe"),
        memory_policy={"send_all_memory": False, "required_lanes": ["recent_transcript", "scene_state", "player_control_contract"]},
        writeback_policy={"planned": ["scene_event", "unresolved_thread_delta"]},
    ),
    "roleplay_character_dialogue_v1": PromptContract(
        contract_id="roleplay_character_dialogue_v1",
        label="Roleplay Character Dialogue",
        controller="roleplay",
        intent_family="roleplay.dialogue",
        purpose="Generate NPC dialogue that follows character state, voice, knowledge limits, and relationship pressure.",
        input_lanes=("character_state", "relationship_state", "private_knowledge_gate", "scene_state", "user_turn"),
        output_lanes=("Character dialogue", "minimal narration"),
        hard_rules=(
            "Speak only as model-controlled characters.",
            "Character dialogue must reflect what that character knows, wants, fears, and is allowed to reveal now.",
            "Do not use assistant/meta voice unless user is out-of-character.",
        ),
        soft_rules=("Prefer subtext and believable emotion over exposition dumps.",),
        validation_checks=("dialogue_character_lanes", "knowledge_gate_respected", "no_assistant_voice"),
        memory_policy={"send_all_memory": False, "required_lanes": ["character_state", "relationship_state", "scene_state"]},
        writeback_policy={"planned": ["relationship_delta", "character_knowledge_delta"]},
    ),
    "memory_consolidation_summary_v1": PromptContract(
        contract_id="memory_consolidation_summary_v1",
        label="Memory Consolidation Summary",
        controller="memory",
        intent_family="memory.consolidation",
        purpose="Summarize grouped memory fragments into compact, scoped, non-hallucinated long-term memory.",
        input_lanes=("memory_group", "scope", "source_metadata"),
        output_lanes=("summary", "key_facts", "confidence", "source_ids"),
        hard_rules=(
            "Do not add facts not present in source fragments.",
            "Preserve source scope and project/surface sandbox.",
            "Mark uncertainty and conflicts clearly.",
        ),
        soft_rules=("Prefer durable patterns over transient noise.",),
        validation_checks=("summary_grounded_in_sources", "scope_preserved", "confidence_present"),
        memory_policy={"send_all_memory": False, "summary_only": True},
        writeback_policy={"creates": ["neo_memory_summaries", "consolidated_summary_fragments"]},
    ),
    "memory_conflict_resolution_v1": PromptContract(
        contract_id="memory_conflict_resolution_v1",
        label="Memory Conflict Resolution",
        controller="memory",
        intent_family="memory.conflict_resolution",
        purpose="Compare conflicting memories and propose safe resolution without silently overwriting canon.",
        input_lanes=("conflicting_facts", "source_metadata", "timestamps", "confidence"),
        output_lanes=("conflict_summary", "recommended_resolution", "review_required"),
        hard_rules=(
            "Do not silently overwrite high-impact canon/user/project facts.",
            "Prefer newer evidence only when source reliability and scope match.",
            "Flag unresolved conflicts for review when confidence is low.",
        ),
        soft_rules=("Keep old facts as superseded, not deleted, where possible.",),
        validation_checks=("review_required_for_high_impact", "source_scope_compared", "no_silent_canon_overwrite"),
        memory_policy={"send_all_memory": False, "conflict_sources_required": True},
        writeback_policy={"planned": ["neo_memory_conflicts", "fact_supersession"]},
    ),
}


def get_prompt_contract(contract_id: str | None, *, fallback: str = "assistant_project_memory_answer_v1") -> dict[str, Any]:
    cid = str(contract_id or "").strip()
    contract = CONTRACTS.get(cid) or CONTRACTS.get(fallback) or next(iter(CONTRACTS.values()))
    return contract.as_dict()


def list_prompt_contracts(controller: str | None = None) -> list[dict[str, Any]]:
    controller = str(controller or "").strip().lower()
    contracts = [c.as_dict() for c in CONTRACTS.values() if not controller or c.controller == controller]
    return sorted(contracts, key=lambda item: (item["controller"], item["contract_id"]))


def prompt_contract_status_payload() -> dict[str, Any]:
    by_controller: dict[str, int] = {}
    for contract in CONTRACTS.values():
        by_controller[contract.controller] = by_controller.get(contract.controller, 0) + 1
    return {
        "schema_id": PROMPT_CONTRACT_SCHEMA_ID,
        "phase": PROMPT_CONTRACT_PHASE,
        "status": "ready",
        "label": "Neo Prompt Contracts",
        "contract_count": len(CONTRACTS),
        "by_controller": by_controller,
        "policy": "Prompt contracts define behavior lanes, hard rules, memory policy, validation checks, and writeback intent. They are reusable control specs, not giant prompt dumps.",
    }


def prompt_contract_list_payload(controller: str | None = None) -> dict[str, Any]:
    contracts = list_prompt_contracts(controller)
    return {"ok": True, "status": "ready", "schema_id": PROMPT_CONTRACT_SCHEMA_ID, "phase": PROMPT_CONTRACT_PHASE, "contracts": contracts, "count": len(contracts)}


def prompt_contract_detail_payload(contract_id: str) -> dict[str, Any]:
    return {"ok": True, "status": "ready", "contract": get_prompt_contract(contract_id)}


def resolve_assistant_contract_id(surface: str, intent: str) -> str:
    intent_l = str(intent or "").lower()
    if "advice" in intent_l or "debug" in intent_l or "planning" in intent_l:
        return "assistant_workspace_advice_v1"
    return "assistant_project_memory_answer_v1"


def resolve_roleplay_contract_id(intent: str) -> str:
    intent_l = str(intent or "").lower()
    if "canon_summary" in intent_l or "summary" in intent_l:
        return "roleplay_scene_summary_v1"
    if "continue" in intent_l:
        return "roleplay_scene_continue_v1"
    if "dialogue" in intent_l:
        return "roleplay_character_dialogue_v1"
    return "roleplay_scene_turn_v1"


def render_prompt_contract_block(contract: dict[str, Any], *, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    def bullet(values: Any) -> str:
        if not values:
            return "- none"
        return "\n".join(f"- {_clean(v, limit=500)}" for v in values)
    lines = [
        "# Neo Prompt Contract",
        "Important: this contract is internal guidance. Do not print it, do not print JSON lanes, and do not wrap normal replies in schema objects.",
        f"Contract: {contract.get('contract_id')} — {contract.get('label')}",
        f"Controller: {contract.get('controller')}",
        f"Purpose: {_clean(contract.get('purpose'), limit=900)}",
        "",
        "## Input lanes",
        bullet(contract.get("input_lanes") or []),
        "",
        "## Output lanes",
        bullet(contract.get("output_lanes") or []),
        "",
        "## Hard rules",
        bullet(contract.get("hard_rules") or []),
        "",
        "## Soft rules",
        bullet(contract.get("soft_rules") or []),
        "",
        "## Validation checks",
        bullet(contract.get("validation_checks") or []),
        "",
        "## Memory policy",
        json.dumps(contract.get("memory_policy") or {}, ensure_ascii=False, sort_keys=True),
    ]
    if context:
        lines.extend(["", "## Contract context", json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)[:1600]])
    return "\n".join(lines).strip()
