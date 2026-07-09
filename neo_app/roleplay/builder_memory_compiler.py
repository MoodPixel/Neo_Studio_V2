from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.forge import list_forge_records
from neo_app.roleplay.sandbox_contract import (
    apply_context_to_payload,
    context_json,
    context_scope_id,
    derive_sandbox_context,
)
from neo_app.roleplay.sqlite_store import _connect, _json, ensure_roleplay_memory_schema, roleplay_sqlite_state_payload
from neo_app.roleplay.relationship_schema import normalize_relationship_payload, relationship_participant_ids
from neo_app.roleplay.builder_compiler_profiles import classify_builder_path, compiler_profiles_contract_payload
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root

COMPILER_SCHEMA_ID = "neo.roleplay.builder_memory_compiler.v1"
COMPILER_VERSION = "1.2.0-phase17.5B-first-class-kind-profile-compiler"
COMPILER_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "builder_memory_compiler_contract.json"

_SCALAR_TYPES = (str, int, float, bool)
_MAX_FRAGMENT_CONTENT = 4800
_MAX_FRAGMENT_VALUE = 3200
_IGNORED_PATH_ENDINGS = {"id", "kind", "schema_version", "created_at", "updated_at"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, default: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean(value).lower()).strip("-")
    return cleaned[:80] or default


def _stable_id(*parts: Any, prefix: str = "frag") -> str:
    base = "|".join(_clean(part) for part in parts)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=8).hexdigest()
    readable = _slug(parts[-1] if parts else "", "row")[:42]
    return f"{prefix}:{readable}:{digest}"


def _path_label(path: str) -> str:
    bits = [bit for bit in path.replace("[]", "").split(".") if bit]
    if not bits:
        return "Summary"
    tail = bits[-1]
    return tail.replace("_", " ").replace(" ids", " IDs").title()


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if value == 0:
            return ""
        return str(value)
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, (dict, list)):
                text = _compact_json(item)
            else:
                text = _string_value(item)
            if text:
                pieces.append(text)
        return "\n".join(pieces).strip()
    if isinstance(value, dict):
        text = _compact_json(value)
        return text if text not in ("{}", "[]") else ""
    return str(value).strip()


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value or "")


def _iter_scalar_paths(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"reverse_links", "sandbox_context"}:
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict):
                # Keep small object leaves as one fragment when all children are empty/scalar.
                yield from _iter_scalar_paths(child, path)
            elif isinstance(child, list):
                if all(not isinstance(item, (dict, list)) for item in child):
                    if any(_string_value(item) for item in child):
                        yield path, child
                else:
                    for index, item in enumerate(child):
                        item_path = f"{path}[{index}]"
                        if isinstance(item, dict):
                            # Relationship/object lists are useful as both a compact item and walked fields.
                            item_text = _string_value(item)
                            if item_text:
                                yield item_path, item
                            yield from _iter_scalar_paths(item, item_path)
                        else:
                            item_text = _string_value(item)
                            if item_text:
                                yield item_path, item
            else:
                text = _string_value(child)
                if text and key not in _IGNORED_PATH_ENDINGS:
                    yield path, child
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_scalar_paths(item, f"{prefix}[{index}]")


def _memory_type_for(kind: str, path: str, value: Any) -> str:
    return classify_builder_path(kind, path, value).memory_type


def _priority_for(memory_type: str, path: str, kind: str = "") -> str:
    if kind:
        return classify_builder_path(kind, path).priority
    if memory_type in {"canon_guard", "scene_rule", "relationship_state", "relationship_belief", "romance_boundary", "artifact_rule", "ritual_rule", "system_rule", "danger_rule", "reveal_gate", "hidden_truth", "political_pressure", "scenario_pressure"}:
        return "high"
    if any(token in path.lower() for token in ("summary", "identity", "known_for", "premise", "objective", "stakes")):
        return "high"
    return "normal"


def _fragment_content(title: str, kind: str, path: str, value: Any) -> str:
    value_text = _string_value(value)[:_MAX_FRAGMENT_VALUE]
    heading = _path_label(path)
    lines = [f"{title} [{kind}]", f"{heading} ({path})", value_text]
    return "\n".join(part for part in lines if part).strip()[:_MAX_FRAGMENT_CONTENT]


def _base_tags(kind: str, record_tags: list[str], path: str, memory_type: str) -> list[str]:
    tags = [kind, memory_type]
    tags.extend(record_tags or [])
    if path.startswith("fields."):
        bits = path.split(".")
        if len(bits) > 1:
            tags.append(bits[1])
    if path.startswith("memory_hints."):
        tags.append("memory_hints")
    # stable order without duplicates
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        clean = _clean(tag)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def compile_builder_record_fragments(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    kind = _clean(record.get("kind") or payload.get("kind") or "unknown") or "unknown"
    if kind == "relationship":
        payload = normalize_relationship_payload(payload)
    record_id = _clean(record.get("record_id") or payload.get("id"))
    title = _clean(record.get("title") or payload.get("display_label") or payload.get("label") or record_id) or record_id
    summary = _clean(record.get("body") or payload.get("summary"))
    tags = record.get("tags") if isinstance(record.get("tags"), list) else payload.get("tags") if isinstance(payload.get("tags"), list) else []
    context = derive_sandbox_context(payload, record_id=record_id, kind=kind)
    scope_id = context_scope_id(context)

    fragments: list[dict[str, Any]] = []

    def add(path: str, value: Any, *, forced_type: str | None = None, label: str | None = None) -> None:
        text_value = _string_value(value)
        if not text_value:
            return
        classification = classify_builder_path(kind, path, value)
        memory_type = forced_type or classification.memory_type
        fragment_id = _stable_id(record_id, kind, path, text_value[:120], prefix="forge")
        content = _fragment_content(title, kind, path, value)
        fragment_tags = _base_tags(kind, tags, path, memory_type)
        for extra_tag in (classification.scene_category, classification.semantic_role, classification.matched_rule):
            if extra_tag and extra_tag not in fragment_tags:
                fragment_tags.append(extra_tag)
        fragments.append({
            "fragment_id": fragment_id,
            "namespace": f"roleplay.{kind}.{memory_type}",
            "source_type": "forge_record",
            "source_id": record_id,
            "memory_type": memory_type,
            "status": "compiled_builder_record",
            "content": content,
            "tags": fragment_tags,
            "payload": {
                "compiler_schema_id": COMPILER_SCHEMA_ID,
                "compiler_version": COMPILER_VERSION,
                "kind": kind,
                "title": title,
                "path": path,
                "path_label": label or _path_label(path),
                "raw_value": value,
                "scope_id": scope_id,
                "source_record_id": record_id,
                "source_record_kind": kind,
                "priority": _priority_for(memory_type, path, kind),
                "scene_category": classification.scene_category,
                "semantic_role": classification.semantic_role,
                "matched_compiler_rule": classification.matched_rule,
                "strong_compiler_profile": classification.strong_profile,
                "target_memory_types": list(classification.target_memory_types),
                "sandbox_context": context,
            },
        })

    add("summary", summary or title, label="Record summary")
    for root_key in ("fields", "memory_hints"):
        root_value = payload.get(root_key)
        if isinstance(root_value, (dict, list)):
            for path, value in _iter_scalar_paths(root_value, root_key):
                add(path, value)
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    # Compact link fragments help retrieval explain graph placement without requiring payload inspection.
    scope_links = links.get("scope") if isinstance(links.get("scope"), dict) else {}
    related_links = links.get("related") if isinstance(links.get("related"), dict) else {}
    if scope_links:
        add("links.scope", {k: v for k, v in scope_links.items() if v}, forced_type="semantic_fact", label="Scope links")
    if related_links:
        non_empty_related = {k: v for k, v in related_links.items() if v}
        if non_empty_related:
            add("links.related", non_empty_related, forced_type="semantic_fact", label="Related links")
    # Safety: avoid duplicate fragments from duplicate scalar paths/list objects.
    unique: dict[str, dict[str, Any]] = {}
    for fragment in fragments:
        unique[fragment["fragment_id"]] = fragment
    return {
        "record_id": record_id,
        "kind": kind,
        "title": title,
        "scope_id": scope_id,
        "sandbox_context": context,
        "fragments": list(unique.values()),
        "fragment_count": len(unique),
    }


def _edges_from_payload(record_id: str, kind: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    edges: list[dict[str, Any]] = []

    def add(target_id: Any, relation_type: str, target_kind: str = "") -> None:
        target = _clean(target_id)
        if not target or target == record_id:
            return
        edges.append({
            "edge_id": _stable_id(record_id, relation_type, target, prefix="edge"),
            "source_id": record_id,
            "target_id": target,
            "relation_type": relation_type,
            "source_kind": kind,
            "target_kind": target_kind,
            "weight": 1.0,
            "payload": {"source": "builder_memory_compiler", "relation_type": relation_type},
        })

    scope = links.get("scope") if isinstance(links.get("scope"), dict) else {}
    for key, value in scope.items():
        if key.endswith("_id") and value:
            add(value, key.replace("_id", ""), key.replace("_id", ""))
    related = links.get("related") if isinstance(links.get("related"), dict) else {}
    for key, values in related.items():
        target_kind = key[:-4] if key.endswith("_ids") else key
        if isinstance(values, list):
            for value in values:
                add(value, f"related_{target_kind}", target_kind)
        elif values:
            add(values, f"related_{target_kind}", target_kind)
    if kind == "relationship":
        payload = normalize_relationship_payload(payload)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    participants = fields.get("participants") if isinstance(fields.get("participants"), dict) else {}
    for key in ("character_a_id", "character_b_id"):
        add(participants.get(key), key.replace("_id", ""), "character")
    for value in participants.get("other_character_ids") or []:
        add(value, "participant", "character")
    for value in relationship_participant_ids(payload):
        add(value, "participant", "character")
    return list({edge["edge_id"]: edge for edge in edges}.values())


def upsert_compiled_builder_record_memory(record: dict[str, Any], *, delete_existing: bool = True) -> dict[str, Any]:
    """Compile a Forge Builder record into granular SQLite memory rows.

    This is Phase 3's core path. SQLite remains authoritative; vector/Chroma
    indexing stays separate and can be run after compile.
    """
    ensure_roleplay_memory_schema()
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    kind = _clean(record.get("kind") or payload.get("kind") or "unknown") or "unknown"
    if kind == "relationship":
        payload = normalize_relationship_payload(payload)
        record = dict(record)
        record["payload"] = payload
    record_id = _clean(record.get("record_id") or payload.get("id"))
    if not record_id:
        raise ValueError("record_id is required")
    title = _clean(record.get("title") or payload.get("display_label") or payload.get("label") or record_id) or record_id
    status = _clean(payload.get("canon_status") or (payload.get("meta", {}).get("status") if isinstance(payload.get("meta"), dict) else "") or "draft")
    tags = record.get("tags") if isinstance(record.get("tags"), list) else payload.get("tags") if isinstance(payload.get("tags"), list) else []
    now = _now()
    compiled = compile_builder_record_fragments(record)
    sandbox_context = compiled["sandbox_context"]
    scope_id = compiled["scope_id"]
    source_path = _clean(record.get("storage_path"))
    payload = apply_context_to_payload(payload, sandbox_context)
    edges = _edges_from_payload(record_id, kind, payload)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_entities(entity_id, kind, title, status, scope_id, tags_json, payload_json, source_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                kind=excluded.kind,
                title=excluded.title,
                status=excluded.status,
                scope_id=excluded.scope_id,
                tags_json=excluded.tags_json,
                payload_json=excluded.payload_json,
                source_path=excluded.source_path,
                updated_at=excluded.updated_at
            """,
            (record_id, kind, title, status, scope_id, _json(tags), _json(payload), source_path, _clean(record.get("created_at")) or now, _clean(record.get("updated_at")) or now),
        )
        _apply_sandbox_update(conn, "rp_entities", "entity_id", record_id, sandbox_context)
        conn.execute(
            """
            INSERT INTO rp_entity_versions(version_id, entity_id, kind, payload_json, created_at, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_stable_id(record_id, now, prefix="entity-version"), record_id, kind, _json(payload), now, "Phase 3 builder compile snapshot"),
        )
        if delete_existing:
            old_fragment_ids = [str(row[0]) for row in conn.execute("SELECT fragment_id FROM rp_memory_fragments WHERE source_type='forge_record' AND source_id=?", (record_id,)).fetchall()]
            conn.execute("DELETE FROM rp_memory_fragments WHERE source_type='forge_record' AND source_id=?", (record_id,))
            for fragment_id in old_fragment_ids:
                conn.execute("DELETE FROM rp_vector_index WHERE source_table='rp_memory_fragments' AND source_id=?", (fragment_id,))
        for fragment in compiled["fragments"]:
            conn.execute(
                """
                INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fragment_id) DO UPDATE SET
                    namespace=excluded.namespace,
                    source_type=excluded.source_type,
                    source_id=excluded.source_id,
                    memory_type=excluded.memory_type,
                    status=excluded.status,
                    content=excluded.content,
                    tags_json=excluded.tags_json,
                    payload_json=excluded.payload_json,
                    vector_status=excluded.vector_status,
                    updated_at=excluded.updated_at
                """,
                (
                    fragment["fragment_id"],
                    fragment["namespace"],
                    fragment["source_type"],
                    fragment["source_id"],
                    fragment["memory_type"],
                    fragment["status"],
                    fragment["content"],
                    _json(fragment.get("tags") or []),
                    _json(fragment.get("payload") or {}),
                    "not_indexed",
                    now,
                    now,
                ),
            )
            _apply_sandbox_update(conn, "rp_memory_fragments", "fragment_id", fragment["fragment_id"], sandbox_context)
        conn.execute("DELETE FROM rp_edges WHERE source_id=?", (record_id,))
        for edge in edges:
            conn.execute(
                """
                INSERT INTO rp_edges(edge_id, source_id, target_id, relation_type, source_kind, target_kind, weight, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO UPDATE SET
                    target_id=excluded.target_id,
                    relation_type=excluded.relation_type,
                    source_kind=excluded.source_kind,
                    target_kind=excluded.target_kind,
                    weight=excluded.weight,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (edge["edge_id"], edge["source_id"], edge["target_id"], edge["relation_type"], edge["source_kind"], edge["target_kind"], edge["weight"], _json(edge["payload"]), now, now),
            )
        _upsert_auxiliary_state_rows(conn, record, compiled, now)
        conn.commit()
    return {
        "schema_id": COMPILER_SCHEMA_ID,
        "status": "compiled",
        "record_id": record_id,
        "entity_id": record_id,
        "kind": kind,
        "title": title,
        "scope_id": scope_id,
        "sandbox_context": sandbox_context,
        "fragment_count": int(compiled.get("fragment_count") or 0),
        "edge_count": len(edges),
        "vector_status": "not_indexed",
        "next_step": "Run /api/roleplay/retrieval/index-roleplay-memory to index compiled fragments.",
    }


def _apply_sandbox_update(conn: sqlite3.Connection, table: str, key_column: str, key_value: str, context: dict[str, str]) -> None:
    conn.execute(
        f"""
        UPDATE {table}
        SET project_id=?, sandbox_id=?, universe_id=?, world_id=?, region_id=?, city_id=?, location_id=?,
            source_record_id=?, source_record_kind=?, canon_snapshot_id=?, storyline_id=?, session_id=?, branch_id=?,
            memory_scope=?, promotion_scope=?, sandbox_json=?
        WHERE {key_column}=?
        """,
        (
            context.get("project_id", ""), context.get("sandbox_id", ""), context.get("universe_id", ""), context.get("world_id", ""),
            context.get("region_id", ""), context.get("city_id", ""), context.get("location_id", ""), context.get("source_record_id", ""),
            context.get("source_record_kind", ""), context.get("canon_snapshot_id", ""), context.get("storyline_id", ""),
            context.get("session_id", ""), context.get("branch_id", ""), context.get("memory_scope", "builder_record"),
            context.get("promotion_scope", "draft"), context_json(context), key_value,
        ),
    )


def _upsert_auxiliary_state_rows(conn: sqlite3.Connection, record: dict[str, Any], compiled: dict[str, Any], now: str) -> None:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    kind = compiled.get("kind") or payload.get("kind") or ""
    record_id = compiled.get("record_id") or payload.get("id") or ""
    context = compiled.get("sandbox_context") or {}
    scope_id = compiled.get("scope_id") or context_scope_id(context)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    title = compiled.get("title") or record_id
    if kind == "character":
        state_id = f"character:{record_id}:profile"
        goals = _string_value(fields.get("goals_desire_fear_wounds") or {})
        boundaries = _string_value((fields.get("story_roleplay_use") or {}).get("roleplay_dos_and_donts") if isinstance(fields.get("story_roleplay_use"), dict) else "")
        conn.execute(
            """
            INSERT INTO rp_character_states(state_id, character_id, scope_id, display_name, current_emotion, emotional_vector_json, goals_json, boundaries_json, payload_json, trust_level, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_id) DO UPDATE SET scope_id=excluded.scope_id, display_name=excluded.display_name, goals_json=excluded.goals_json, boundaries_json=excluded.boundaries_json, payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (state_id, record_id, scope_id, title, "", "{}", _json([goals] if goals else []), _json([boundaries] if boundaries else []), _json({"source_record_id": record_id, "compiled": True}), "builder_compile", now),
        )
        _apply_sandbox_update(conn, "rp_character_states", "state_id", state_id, context)
    if kind == "relationship":
        payload = normalize_relationship_payload(payload)
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        participants = fields.get("participants") if isinstance(fields.get("participants"), dict) else {}
        char_a = _clean(participants.get("character_a_id"))
        char_b = _clean(participants.get("character_b_id"))
        rel_type = _clean((fields.get("identity") or {}).get("relationship_type") if isinstance(fields.get("identity"), dict) else "") or "relationship"
        if char_a or char_b:
            state_id = f"relationship:{record_id}:state"
            state_payload = {
                "source_record_id": record_id,
                "relationship_schema_version": "phase4",
                "participants": participants,
                "identity": fields.get("identity") or {},
                "history": fields.get("history") or {},
                "emotional_logic": fields.get("emotional_logic") or {},
                "romance_boundaries": fields.get("romance_boundaries") or {},
                "current_state": fields.get("current_state") or {},
                "scene_use": fields.get("scene_use") or {},
                "compiled": True,
            }
            conn.execute(
                """
                INSERT INTO rp_relationship_state(state_id, character_a_id, character_b_id, relationship_type, state_label, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state_id) DO UPDATE SET character_a_id=excluded.character_a_id, character_b_id=excluded.character_b_id, relationship_type=excluded.relationship_type, state_label=excluded.state_label, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (state_id, char_a, char_b, rel_type, title, _json(state_payload), now),
            )
            _apply_sandbox_update(conn, "rp_relationship_state", "state_id", state_id, context)


def compile_builder_record_memory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    record_id = _clean(payload.get("record_id") or payload.get("entity_id") or payload.get("id"))
    kind = _clean(payload.get("kind"))
    record_payload = payload.get("record") if isinstance(payload.get("record"), dict) else None
    if not record_payload:
        for record in list_forge_records(kind or None):
            data = model_to_dict(record)
            if _clean(data.get("record_id")) == record_id:
                record_payload = data
                break
    if not record_payload:
        raise ValueError(f"Forge record not found: {record_id or '(missing record_id)'}")
    result = upsert_compiled_builder_record_memory(record_payload, delete_existing=bool(payload.get("delete_existing", True)))
    return {
        "schema_id": "neo.roleplay.memory.compile_builder_record.v1",
        "status": result.get("status"),
        "compile": result,
        "sqlite": roleplay_sqlite_state_payload(),
    }


def compile_all_builder_records_memory_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    payload = payload or {}
    kind = _clean(payload.get("kind")) or None
    records = [model_to_dict(record) for record in list_forge_records(kind)]
    compiled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total_fragments = 0
    total_edges = 0
    for record in records:
        try:
            result = upsert_compiled_builder_record_memory(record, delete_existing=bool(payload.get("delete_existing", True)))
            compiled.append(result)
            total_fragments += int(result.get("fragment_count") or 0)
            total_edges += int(result.get("edge_count") or 0)
        except Exception as exc:
            errors.append({"record_id": _clean(record.get("record_id")), "kind": _clean(record.get("kind")), "error": str(exc)})
    return {
        "schema_id": "neo.roleplay.memory.compile_all_builder_records.v1",
        "status": "compiled" if not errors else "partial",
        "kind": kind or "all",
        "record_count": len(records),
        "compiled_count": len(compiled),
        "error_count": len(errors),
        "fragment_count": total_fragments,
        "edge_count": total_edges,
        "compiled": compiled[:100],
        "errors": errors,
        "sqlite": roleplay_sqlite_state_payload(),
        "next_step": "Run vector indexing, then test Runtime retrieval before Scene Chat.",
    }


def builder_memory_compiler_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    contract = {
        "schema_id": COMPILER_SCHEMA_ID,
        "version": COMPILER_VERSION,
        "phase": "Phase 17.5B — First-Class Builder Compiler Profiles",
        "generated_at": _now(),
        "status": "implemented",
        "purpose": "Compile full nested Forge Builder JSON into granular, sandbox-scoped SQLite memory fragments.",
        "inputs": ["Forge Builder records", "payload.fields", "payload.memory_hints", "payload.links.scope", "payload.links.related"],
        "outputs": ["rp_entities", "rp_entity_versions", "rp_edges", "rp_memory_fragments", "rp_character_states", "rp_relationship_state"],
        "fragment_types": sorted({memory_type for profile in compiler_profiles_contract_payload(write_report=False).get("profiles", []) for memory_type in profile.get("target_memory_types", [])}),
        "sandboxing": "Every entity and fragment receives Phase 2 sandbox columns and sandbox_json.",
        "vector_policy": "Compiled rows are marked not_indexed. Vector/Chroma indexing remains an explicit follow-up step.",
        "api_endpoints": {
            "contract": "GET /api/roleplay/memory/builder-compiler-contract",
            "compile_one": "POST /api/roleplay/memory/compile-builder-record",
            "compile_all": "POST /api/roleplay/memory/compile-all-builder-records",
        },
        "sqlite": roleplay_sqlite_state_payload(),
        "relationship_schema": "Phase 4 relationship records are normalized before compile and written into rp_relationship_state.",
        "compiler_profiles": compiler_profiles_contract_payload(write_report=False),
        "next_required_phase": "17.5C — Scene packet categories for all kinds",
    }
    if write_report:
        COMPILER_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMPILER_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract
