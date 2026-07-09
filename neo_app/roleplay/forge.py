from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.schema import RoleplayForgeKind, RoleplayForgeRecord, RoleplayForgeState
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

TEMPLATE_ROOT: Final[Path] = Path(__file__).resolve().parent / "builder_templates"
JSON_TEMPLATE_ROOT: Final[Path] = TEMPLATE_ROOT / "json"
MD_TEMPLATE_ROOT: Final[Path] = TEMPLATE_ROOT / "md"

FORGE_KIND_DEFINITIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("universe", "Universe", "Root cosmology, universal laws, timelines, and multi-world canon."),
    ("world", "World", "A major realm/setting inside a universe."),
    ("region", "Region / Kingdom", "Kingdoms, provinces, territories, and political regions."),
    ("city", "City / Settlement", "Cities, villages, ports, districts, and settlements."),
    ("location", "Location", "Places, rooms, landmarks, scene anchors, and environments."),
    ("character", "Character", "Characters, personas, roles, and participant profiles."),
    ("organization", "Organization", "Groups, houses, clans, companies, courts, guilds, and alliances."),
    ("artifact", "Artifact", "Objects, relics, weapons, tools, props, and meaningful items."),
    ("ritual", "Ritual / Practice", "Rituals, magic methods, techniques, practices, and systems of use."),
    ("cycle", "Cycle / System", "Curses, transformations, cycles, conditions, and world systems."),
    ("creature", "Creature", "Beasts, fauna, hidden beings, sentient species, and monsters."),
    ("legend", "Legend / Canon", "Myths, prophecies, histories, truths, warnings, and lore records."),
    ("scenario", "Scenario", "Story seeds, session hooks, scene premises, and runtime setup records."),
    ("lore", "Lore", "Legacy alias for canon notes. Prefer Legend / Canon for V1-style builder records."),
    ("relationship", "Relationship", "Bonds, rivalries, dynamics, history, and relationship state."),
)

KIND_ALIASES: Final[dict[str, str]] = {
    "faction": "organization",
    "item": "artifact",
    "canon": "legend",
    "lore_canon": "legend",
    "universe_world": "universe",
    "region_kingdom": "region",
    "city_settlement": "city",
    "locations": "location",
    "characters": "character",
    "organizations": "organization",
    "artifacts": "artifact",
    "relationships": "relationship",
}

_VALID_KINDS: Final[set[str]] = {kind_id for kind_id, _, _ in FORGE_KIND_DEFINITIONS}
CANONICAL_TEMPLATE_KINDS: Final[tuple[str, ...]] = (
    "universe", "world", "region", "city", "location", "character", "organization", "artifact",
    "ritual", "cycle", "creature", "legend", "scenario", "relationship",
)

HIERARCHY: Final[dict[str, dict[str, Any]]] = {
    "universe": {"display_name": "Universe", "entry_label": "Build a Universe", "lane": "setting", "scope_tier": "root", "parent_kinds": [], "recommended_child_kinds": ["world"]},
    "world": {"display_name": "World", "entry_label": "Build a World", "lane": "setting", "scope_tier": "world", "parent_kinds": ["universe"], "recommended_child_kinds": ["region", "city", "location", "character"]},
    "region": {"display_name": "Region / Kingdom", "entry_label": "Build a Region", "lane": "setting", "scope_tier": "regional", "parent_kinds": ["world"], "recommended_child_kinds": ["city", "location", "character", "organization"]},
    "city": {"display_name": "City / Settlement", "entry_label": "Build a City", "lane": "setting", "scope_tier": "city", "parent_kinds": ["region", "world"], "recommended_child_kinds": ["location", "character", "organization", "scenario"]},
    "location": {"display_name": "Location", "entry_label": "Build a Location", "lane": "setting", "scope_tier": "scene_anchor", "parent_kinds": ["city", "region", "world", "universe"], "recommended_child_kinds": ["character", "organization", "artifact", "scenario"]},
    "character": {"display_name": "Character", "entry_label": "Build a Character", "lane": "people", "scope_tier": "person", "parent_kinds": ["world", "region", "city", "location"], "recommended_child_kinds": ["scenario"]},
    "organization": {"display_name": "Organization", "entry_label": "Build an Organization", "lane": "institutions", "scope_tier": "group", "parent_kinds": ["universe", "world", "region", "city", "location"], "recommended_child_kinds": ["character", "artifact", "scenario"]},
    "artifact": {"display_name": "Artifact", "entry_label": "Build an Artifact", "lane": "arcana", "scope_tier": "object", "parent_kinds": ["world", "region", "city", "location", "character", "organization"], "recommended_child_kinds": ["scenario"]},
    "ritual": {"display_name": "Ritual / Practice", "entry_label": "Build a Ritual", "lane": "arcana", "scope_tier": "practice", "parent_kinds": ["world", "region", "location", "organization", "artifact"], "recommended_child_kinds": ["artifact", "cycle", "scenario"]},
    "cycle": {"display_name": "Cycle / System", "entry_label": "Build a Cycle", "lane": "systems", "scope_tier": "system", "parent_kinds": ["universe", "world", "region", "character"], "recommended_child_kinds": ["scenario", "legend"]},
    "creature": {"display_name": "Creature", "entry_label": "Build a Creature", "lane": "beings", "scope_tier": "species", "parent_kinds": ["world", "region", "location"], "recommended_child_kinds": ["scenario"]},
    "legend": {"display_name": "Legend / Canon", "entry_label": "Build a Legend", "lane": "canon", "scope_tier": "lore", "parent_kinds": ["universe", "world", "region", "city", "character", "organization"], "recommended_child_kinds": ["scenario"]},
    "scenario": {"display_name": "Scenario", "entry_label": "Build a Story Scenario", "lane": "story", "scope_tier": "runtime_seed", "parent_kinds": ["world", "region", "city", "location", "character", "organization"], "recommended_child_kinds": []},
    "lore": {"display_name": "Lore", "entry_label": "Build Lore / Canon", "lane": "canon", "scope_tier": "legacy", "parent_kinds": [], "recommended_child_kinds": []},
    "relationship": {"display_name": "Relationship", "entry_label": "Build a Relationship", "lane": "people", "scope_tier": "dynamic", "parent_kinds": ["character"], "recommended_child_kinds": []},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:72] or "record"


def _kind_dir(kind: str) -> Path:
    return ROLEPLAY_DATA_ROOT / "entities" / kind


def _record_path(kind: str, record_id: str) -> Path:
    return _kind_dir(kind) / f"{record_id}.json"


def _normalise_kind(kind: str | None) -> str:
    candidate = (kind or "character").strip().lower()
    candidate = KIND_ALIASES.get(candidate, candidate)
    return candidate if candidate in _VALID_KINDS else "character"


def template_kind(kind: str | None) -> str:
    clean = _normalise_kind(kind)
    if clean in CANONICAL_TEMPLATE_KINDS:
        return clean
    if clean == "relationship":
        return "relationship"
    if clean == "lore":
        return "legend"
    return clean


def ensure_forge_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    for kind_id, _, _ in FORGE_KIND_DEFINITIONS:
        _kind_dir(kind_id).mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def load_builder_template(kind: str | None) -> dict[str, Any]:
    clean = _normalise_kind(kind)
    tkind = template_kind(clean)
    payload = _read_json(JSON_TEMPLATE_ROOT / f"{tkind}.template.json")
    if not payload:
        payload = {
            "id": "",
            "kind": clean,
            "schema_version": 1,
            "source_container_id": "",
            "label": "",
            "display_label": "",
            "summary": "",
            "canon_status": "primary_canon",
            "visibility": "author_private",
            "tags": [],
            "tone_tags": [],
            "links": {"scope": {}, "related": {}, "reverse_links": {"strategy": "derived", "materialized": {}}},
            "fields": {"notes": {"overview": ""}},
            "memory_hints": {},
            "meta": {"status": "draft"},
        }
    payload = deepcopy(payload)
    payload["kind"] = clean
    markdown = ""
    try:
        markdown = (MD_TEMPLATE_ROOT / f"{tkind}.template.md").read_text(encoding="utf-8")
    except Exception:
        markdown = f"# {HIERARCHY.get(clean, {}).get('display_name', clean.title())}\n\n## Summary\n\n"
    return {
        "kind": clean,
        "template_kind": tkind,
        "display_name": HIERARCHY.get(clean, {}).get("display_name", clean.title()),
        "json_template_payload": payload,
        "md_template_text": markdown,
        "field_paths": _field_paths(payload),
        "hierarchy": HIERARCHY.get(clean, {}),
    }


def _field_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict):
                paths.extend(_field_paths(child, path))
            else:
                paths.append(path)
    return paths


def builder_templates_by_kind() -> dict[str, Any]:
    return {kind_id: load_builder_template(kind_id) for kind_id, _, _ in FORGE_KIND_DEFINITIONS}


def _read_record(path: Path) -> RoleplayForgeRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RoleplayForgeRecord(**payload)
    except Exception:
        return None


def list_forge_records(kind: str | None = None) -> list[RoleplayForgeRecord]:
    ensure_forge_storage()
    kinds = [_normalise_kind(kind)] if kind else [kind_id for kind_id, _, _ in FORGE_KIND_DEFINITIONS]
    records: list[RoleplayForgeRecord] = []
    for kind_id in kinds:
        for path in sorted(_kind_dir(kind_id).glob("*.json")):
            record = _read_record(path)
            if record:
                records.append(record)
    return sorted(records, key=lambda item: item.updated_at or item.created_at, reverse=True)


def forge_kinds() -> list[RoleplayForgeKind]:
    ensure_forge_storage()
    kinds: list[RoleplayForgeKind] = []
    for kind_id, display_name, description in FORGE_KIND_DEFINITIONS:
        path = _kind_dir(kind_id)
        kinds.append(
            RoleplayForgeKind(
                kind_id=kind_id,
                display_name=display_name,
                description=description,
                storage_path=_relative_to_root(path),
                record_count=len(list(path.glob("*.json"))),
            )
        )
    return kinds


def _tags_from(value: Any) -> list[str]:
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    return []


def _payload_from_save_input(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    raw_payload = payload.get("payload")
    if not isinstance(raw_payload, dict):
        payload_json = payload.get("payload_json")
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                parsed = json.loads(payload_json)
                if isinstance(parsed, dict):
                    raw_payload = parsed
            except Exception:
                raw_payload = None
    if isinstance(raw_payload, dict):
        result = deepcopy(raw_payload)
    else:
        result = deepcopy(load_builder_template(kind)["json_template_payload"])
    result["kind"] = kind
    title = str(payload.get("title") or payload.get("record_title") or result.get("label") or "").strip()
    body = str(payload.get("body") or payload.get("record_body") or result.get("summary") or "")
    if title:
        result["label"] = title
        result.setdefault("display_label", title)
        if not result.get("display_label"):
            result["display_label"] = title
    if body:
        result["summary"] = body
    if payload.get("tags") is not None:
        result["tags"] = _tags_from(payload.get("tags"))
    return result


def save_forge_record(payload: dict[str, Any]) -> RoleplayForgeRecord:
    ensure_forge_storage()
    kind = _normalise_kind(str(payload.get("kind") or payload.get("record_kind") or "character"))
    builder_payload = _payload_from_save_input(payload, kind)
    if kind == "relationship":
        try:
            from neo_app.roleplay.relationship_schema import normalize_relationship_payload
            builder_payload = normalize_relationship_payload(builder_payload)
        except Exception:
            pass
    title = str(builder_payload.get("label") or builder_payload.get("display_label") or payload.get("title") or "Untitled Roleplay Record").strip() or "Untitled Roleplay Record"
    body = str(builder_payload.get("summary") or payload.get("body") or "")
    now = _now()
    record_id = str(payload.get("record_id") or builder_payload.get("id") or f"{_slug(title)}-{uuid.uuid4().hex[:8]}")
    builder_payload["id"] = record_id
    existing = _read_record(_record_path(kind, record_id))
    tags = _tags_from(builder_payload.get("tags"))
    markdown = str(payload.get("markdown") or load_builder_template(kind).get("md_template_text") or "")
    record = RoleplayForgeRecord(
        record_id=record_id,
        kind=kind,
        title=title,
        body=body,
        tags=tags,
        payload=builder_payload,
        markdown=markdown,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        storage_path=_relative_to_root(_record_path(kind, record_id)),
    )
    path = _record_path(kind, record_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model_to_dict(record), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def delete_forge_record_payload(record_id: str, kind: str | None = None) -> dict[str, Any]:
    ensure_forge_storage()
    clean_record_id = str(record_id or "").strip()
    if not clean_record_id:
        return {
            "schema_id": "neo.roleplay.forge.record.delete.v1",
            "status": "error",
            "message": "record_id is required",
            "deleted": False,
            "forge": forge_state_payload(kind),
        }
    search_kinds = [_normalise_kind(kind)] if kind else [kind_id for kind_id, _, _ in FORGE_KIND_DEFINITIONS]
    deleted_path = None
    deleted_kind = None
    for kind_id in search_kinds:
        path = _record_path(kind_id, clean_record_id)
        if path.exists():
            deleted_path = path
            deleted_kind = kind_id
            path.unlink()
            try:
                from neo_app.roleplay.sqlite_store import delete_forge_record_memory
                delete_forge_record_memory(clean_record_id)
            except Exception:
                pass
            break
    return {
        "schema_id": "neo.roleplay.forge.record.delete.v1",
        "status": "deleted" if deleted_path else "not_found",
        "deleted": bool(deleted_path),
        "record_id": clean_record_id,
        "kind": deleted_kind or _normalise_kind(kind),
        "storage_path": _relative_to_root(deleted_path) if deleted_path else "",
        "forge": forge_state_payload(deleted_kind or kind),
    }


def forge_state_payload(active_kind: str | None = None) -> dict[str, Any]:
    kind = _normalise_kind(active_kind)
    records = list_forge_records(kind=None)
    templates = builder_templates_by_kind()
    state = RoleplayForgeState(
        active_kind=kind,
        kinds=forge_kinds(),
        records=records,
        templates_by_kind=templates,
        active_template=templates.get(kind, {}),
        hierarchy=HIERARCHY,
        inspector={
            "storage_root": _relative_to_root(ROLEPLAY_DATA_ROOT / "entities"),
            "record_count": len(records),
            "selected_kind": kind,
            "selected_kind_record_count": len([record for record in records if record.kind == kind]),
            "phase": "forge_schema_forms",
            "template_count": len(templates),
        },
    )
    state.sqlite.path = _relative_to_root(ROLEPLAY_SQLITE_PATH)
    return model_to_dict(state)


def save_forge_record_payload(payload: dict[str, Any]) -> dict[str, Any]:
    record = save_forge_record(payload)
    memory_sync = {"status": "deferred"}
    try:
        from neo_app.roleplay.sqlite_store import upsert_forge_record_memory
        memory_sync = upsert_forge_record_memory(model_to_dict(record))
    except Exception as exc:
        memory_sync = {"status": "error", "error": str(exc)}
    return {
        "schema_id": "neo.roleplay.forge.record.write.v1",
        "status": "saved",
        "record": model_to_dict(record),
        "memory_sync": memory_sync,
        "forge": forge_state_payload(record.kind),
    }


def forge_template_payload(kind: str | None = None) -> dict[str, Any]:
    template = load_builder_template(kind)
    return {
        "schema_id": "neo.roleplay.forge.template.v1",
        "surface_id": "roleplay",
        "tab_id": "forge",
        "status": "ready",
        "template": template,
    }
