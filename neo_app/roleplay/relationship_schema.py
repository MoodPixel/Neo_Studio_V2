from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root

RELATIONSHIP_SCHEMA_ID = "neo.roleplay.relationship.schema.v1"
RELATIONSHIP_SCHEMA_VERSION = "1.0.0-phase4-relationship-schema"
RELATIONSHIP_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "relationship_schema_contract.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [_clean(item) for item in parsed if _clean(item)]
        except Exception:
            pass
        return [_clean(part) for part in value.split(",") if _clean(part)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                candidate = item.get("character_id") or item.get("id") or item.get("target_id") or item.get("record_id")
                if _clean(candidate):
                    out.append(_clean(candidate))
            elif _clean(item) and _clean(item) != "[object Object]":
                out.append(_clean(item))
        return list(dict.fromkeys(out))
    return []


def _relationship_template() -> dict[str, Any]:
    template_path = Path(__file__).resolve().parent / "builder_templates" / "json" / "relationship.template.json"
    try:
        parsed = json.loads(template_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {"kind": "relationship", "fields": {}, "links": {"scope": {}, "related": {}}}


def relationship_participant_ids(payload: dict[str, Any]) -> list[str]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    participants = fields.get("participants") if isinstance(fields.get("participants"), dict) else {}
    ids = []
    for key in ("character_a_id", "character_b_id"):
        if _clean(participants.get(key)):
            ids.append(_clean(participants.get(key)))
    ids.extend(_list(participants.get("other_character_ids")))
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    related = links.get("related") if isinstance(links.get("related"), dict) else {}
    ids.extend(_list(related.get("character_ids")))
    return list(dict.fromkeys([item for item in ids if item]))


def _legacy_character_relationship_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract best-effort values from old character-shaped relationship records."""
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    identity = fields.get("identity") if isinstance(fields.get("identity"), dict) else {}
    story = fields.get("story_roleplay_use") if isinstance(fields.get("story_roleplay_use"), dict) else {}
    romance_history = fields.get("family_romance_social_history") if isinstance(fields.get("family_romance_social_history"), dict) else {}
    secrets = fields.get("secrets_lies_hidden_truth") if isinstance(fields.get("secrets_lies_hidden_truth"), dict) else {}
    goals = fields.get("goals_desire_fear_wounds") if isinstance(fields.get("goals_desire_fear_wounds"), dict) else {}
    personality = fields.get("personality_behavior_speech") if isinstance(fields.get("personality_behavior_speech"), dict) else {}
    return {
        "identity": {
            "relationship_type": _clean(identity.get("relationship_type") or identity.get("designation")) or "relationship",
            "dynamic": _clean(personality.get("attachment_style") or identity.get("role_tier")),
            "public_label": _clean(identity.get("public_identity_label")),
            "known_for": _clean(identity.get("known_for")),
        },
        "history": {
            "bond_origin": _clean(romance_history.get("romance_history")),
            "shared_wounds": _clean(goals.get("wounds_fears_triggers")),
            "unresolved_conflict": _clean(story.get("conflict_hooks")),
        },
        "emotional_logic": {
            "attachment_pattern": _clean(personality.get("attachment_style")),
            "conflict_language": _clean(personality.get("conflict_style")),
            "repair_pattern": _clean(personality.get("coping_style")),
        },
        "secrets_lies_hidden_truth": {
            "shared_secrets": _clean(secrets.get("secrets")),
            "lies_between_them": _clean(secrets.get("lies_they_tell") or secrets.get("lies_they_believe")),
            "who_knows_the_truth": _clean(secrets.get("who_knows_what")),
        },
        "scene_use": {
            "romance_hooks": _clean(story.get("romance_hooks")),
            "conflict_hooks": _clean(story.get("conflict_hooks")),
            "betrayal_hooks": _clean(story.get("betrayal_hooks")),
            "roleplay_dos_and_donts": _clean(story.get("roleplay_dos_and_donts")),
        },
    }


def normalize_relationship_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical relationship payload while preserving old data.

    This accepts both the new Phase 4 relationship schema and the earlier accidental
    character-shaped relationship records. It never deletes unknown author data; it
    stores unmapped legacy fields under fields.rich_authoring.internal_notes as a
    compact migration note.
    """
    if not isinstance(payload, dict):
        payload = {}
    original = deepcopy(payload)
    template = _relationship_template()
    result = deepcopy(template)

    # Preserve standard envelope first.
    for key in ("id", "source_container_id", "label", "display_label", "summary", "canon_status", "visibility", "tags", "tone_tags"):
        if key in payload:
            result[key] = deepcopy(payload.get(key))
    result["kind"] = "relationship"
    result["schema_version"] = max(int(payload.get("schema_version") or 1), 1)

    # Merge links.
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    for branch in ("scope", "related"):
        if isinstance(links.get(branch), dict):
            result.setdefault("links", {}).setdefault(branch, {}).update(deepcopy(links.get(branch)))

    # Merge canonical fields when already present.
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    legacy = _legacy_character_relationship_fields(payload)
    for section, values in legacy.items():
        if isinstance(values, dict):
            result.setdefault("fields", {}).setdefault(section, {}).update({k: v for k, v in values.items() if _clean(v)})
    for section, values in fields.items():
        if section in result.get("fields", {}) and isinstance(values, dict) and isinstance(result["fields"].get(section), dict):
            result["fields"][section].update(deepcopy(values))
        elif section not in {"identity", "placement_world_graph", "age_timeline", "appearance_presence", "personality_behavior_speech", "beliefs_morality", "goals_desire_fear_wounds", "family_romance_social_history", "secrets_lies_hidden_truth", "skills_abilities_power", "affiliations_possessions_systems", "relationships", "story_roleplay_use"}:
            result.setdefault("fields", {})[section] = deepcopy(values)

    # Participants from canonical fields, related links, or legacy relationship list.
    participants = result.setdefault("fields", {}).setdefault("participants", {})
    participant_ids = relationship_participant_ids(result)
    participant_ids.extend(_list(fields.get("relationships")))
    participant_ids = list(dict.fromkeys([item for item in participant_ids if item]))
    if not _clean(participants.get("character_a_id")) and participant_ids:
        participants["character_a_id"] = participant_ids[0]
    if not _clean(participants.get("character_b_id")) and len(participant_ids) > 1:
        participants["character_b_id"] = participant_ids[1]
    extra = participant_ids[2:] if len(participant_ids) > 2 else []
    participants["other_character_ids"] = list(dict.fromkeys(_list(participants.get("other_character_ids")) + extra))
    char_ids = relationship_participant_ids(result)
    result.setdefault("links", {}).setdefault("related", {})["character_ids"] = char_ids

    # Reasonable labels.
    identity = result["fields"].setdefault("identity", {})
    if not _clean(identity.get("relationship_type")):
        identity["relationship_type"] = "relationship"
    if not _clean(result.get("display_label")):
        result["display_label"] = result.get("label") or identity.get("public_label") or "Relationship"
    if not _clean(result.get("label")):
        result["label"] = result.get("display_label") or "Relationship"
    if not _clean(result.get("summary")):
        bits = [identity.get("relationship_type"), identity.get("dynamic"), identity.get("known_for")]
        result["summary"] = " · ".join([_clean(bit) for bit in bits if _clean(bit)])

    # Memory hints: preserve and ensure relationship anchor slot.
    hints = payload.get("memory_hints") if isinstance(payload.get("memory_hints"), dict) else {}
    result["memory_hints"].update(deepcopy(hints))
    result["memory_hints"].setdefault("relationship_anchors", [])

    # Preserve full legacy shape for audit if record was character-shaped.
    old_kind = _clean(original.get("kind"))
    if old_kind and old_kind != "relationship":
        note = f"Migrated from legacy {old_kind} shaped relationship payload during Phase 4."
        result["fields"].setdefault("rich_authoring", {})["internal_notes"] = note
        result["fields"]["rich_authoring"]["author_only_notes"] = json.dumps({"legacy_payload_kind": old_kind}, ensure_ascii=False)
    result.setdefault("meta", {}).update(deepcopy(payload.get("meta") if isinstance(payload.get("meta"), dict) else {}))
    result["meta"]["relationship_schema_version"] = RELATIONSHIP_SCHEMA_VERSION
    result["meta"].setdefault("updated_at", _now())
    return result


def relationship_schema_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    contract = {
        "schema_id": RELATIONSHIP_SCHEMA_ID,
        "version": RELATIONSHIP_SCHEMA_VERSION,
        "phase": "Phase 4 — Relationship Schema Fix",
        "generated_at": _now(),
        "status": "implemented",
        "purpose": "Give relationship records their own V2 schema instead of reusing the character template.",
        "canonical_sections": ["identity", "participants", "history", "emotional_logic", "romance_boundaries", "secrets_lies_hidden_truth", "current_state", "scene_use", "rich_authoring"],
        "compiler_behavior": "Relationship records compile into relationship_state fragments, participant graph edges, and rp_relationship_state rows.",
        "migration_behavior": "Old character-shaped relationship records are normalized without deleting unknown author data.",
        "api_endpoints": {
            "contract": "GET /api/roleplay/relationship/schema-contract",
            "normalize": "POST /api/roleplay/relationship/normalize-record",
            "repair_all": "POST /api/roleplay/relationship/repair-all",
        },
        "storage": {
            "template": "neo_app/roleplay/builder_templates/json/relationship.template.json",
            "records": "neo_data/roleplay/entities/relationship",
            "state_table": "rp_relationship_state",
        },
        "next_required_phase": "Phase 5 — Novel System Memory Path",
    }
    if write_report:
        RELATIONSHIP_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RELATIONSHIP_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract


def normalize_relationship_record_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    record = payload.get("record") if isinstance(payload.get("record"), dict) else payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    normalized = normalize_relationship_payload(record)
    return {
        "schema_id": "neo.roleplay.relationship.normalize.v1",
        "status": "normalized",
        "relationship": normalized,
        "participant_ids": relationship_participant_ids(normalized),
    }


def repair_all_relationship_records_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", False))
    from neo_app.roleplay.forge import list_forge_records, save_forge_record
    repaired = []
    errors = []
    for record in list_forge_records("relationship"):
        try:
            data = model_to_dict(record)
            normalized = normalize_relationship_payload(data.get("payload") if isinstance(data.get("payload"), dict) else {})
            if not dry_run:
                save_forge_record({"kind": "relationship", "record_id": data.get("record_id"), "payload": normalized, "markdown": data.get("markdown", "")})
            repaired.append({"record_id": data.get("record_id"), "title": data.get("title"), "participant_ids": relationship_participant_ids(normalized)})
        except Exception as exc:
            errors.append({"record_id": getattr(record, "record_id", ""), "error": str(exc)})
    return {
        "schema_id": "neo.roleplay.relationship.repair_all.v1",
        "status": "repaired" if not errors else "partial",
        "dry_run": dry_run,
        "count": len(repaired),
        "error_count": len(errors),
        "repaired": repaired,
        "errors": errors,
        "contract": relationship_schema_contract_payload(write_report=True),
    }
