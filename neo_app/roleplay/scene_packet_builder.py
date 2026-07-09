from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.runtime_retrieval_lane import run_runtime_retrieval_payload, runtime_retrieval_lane_state_payload
from neo_app.roleplay.scene_packet_categories import (
    SCENE_PACKET_CATEGORY_ORDER,
    SECTION_LABELS,
    category_for_kind,
    category_for_memory_type,
    empty_scene_packet_categories,
    scene_packet_categories_state_payload,
)
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema, rebuild_roleplay_memory_search_documents
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

PHASE11_SCHEMA_ID: Final[str] = "neo.roleplay.scene_packet_builder.v1"
PHASE11_VERSION: Final[str] = "1.0.0-phase11-scene-packet-builder"
PHASE11_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_packet_builder_contract.json"
PHASE11_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_packet_builder_state.json"
PHASE11_PACKET_DIR: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_packets"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 12, minimum: int = 1, maximum: int = 80) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _split_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("\n", ",").split(",")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = _clean(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _load_latest_packet_summary(conn: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            "SELECT packet_id, scene_id, scope_id, title, payload_json, updated_at FROM rp_scene_memory_packets ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = _read_json(row["payload_json"], {}) or {}
    return {
        "packet_id": row["packet_id"],
        "scene_id": row["scene_id"],
        "scope_id": row["scope_id"],
        "title": row["title"],
        "updated_at": row["updated_at"],
        "counts": payload.get("counts") or {},
    }




def _flatten_payload_context(payload: dict[str, Any], *, max_chars: int = 2200) -> str:
    """Build a compact, canon-preserving text block from nested Forge payload fields.

    Scene packets need actual authoring fields, not just labels. This helper keeps
    the model anchored to authored content while avoiding a giant raw JSON dump.
    """
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []

    def add(label: str, value: Any) -> None:
        text = _clean(value)
        if text:
            parts.append(f"{label}: {text}")

    add("summary", payload.get("summary"))
    add("display_label", payload.get("display_label") or payload.get("label"))
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    tone_tags = payload.get("tone_tags") if isinstance(payload.get("tone_tags"), list) else []
    if tags:
        add("tags", ", ".join(_clean(x) for x in tags if _clean(x)))
    if tone_tags:
        add("tone", ", ".join(_clean(x) for x in tone_tags if _clean(x)))

    skip_keys = {"id", "kind", "schema_version", "source_container_id", "links", "meta", "sandbox_context", "memory_hints"}

    def walk(value: Any, path: str = "") -> None:
        if len("\n".join(parts)) >= max_chars:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = _clean(key)
                if key_text in skip_keys:
                    continue
                walk(child, f"{path}.{key_text}" if path else key_text)
            return
        if isinstance(value, list):
            clean_items = [_clean(item) for item in value if _clean(item)]
            if clean_items:
                add(path.replace("_", " "), ", ".join(clean_items[:12]))
            return
        add(path.replace("_", " "), value)

    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    walk(fields, "fields")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text

def _load_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    if not entity_id:
        return None
    try:
        row = conn.execute("SELECT * FROM rp_entities WHERE entity_id = ?", (entity_id,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = _read_json(row["payload_json"], {}) or {}
    content = _flatten_payload_context(payload, max_chars=2400)
    return {
        "entity_id": row["entity_id"],
        "kind": row["kind"],
        "title": row["title"],
        "scope_id": row["scope_id"],
        "status": row["status"],
        "summary": payload.get("summary") or payload.get("display_label") or payload.get("label") or row["title"],
        "content": content or (payload.get("summary") or payload.get("display_label") or payload.get("label") or row["title"]),
        "payload": payload,
    }




def _first_character_name_for_id(selected: list[dict[str, Any]], character_id: str) -> str:
    wanted = _clean(character_id)
    if not wanted:
        return ""
    for entity in selected:
        if _clean(entity.get("entity_id")) != wanted:
            continue
        payload = entity.get("payload") if isinstance(entity.get("payload"), dict) else {}
        return _clean(payload.get("display_label") or payload.get("label") or entity.get("title"))
    return ""


def _character_id_for_name(selected: list[dict[str, Any]], character_name: str) -> str:
    wanted = _clean(character_name).lower()
    if not wanted:
        return ""
    for entity in selected:
        if _clean(entity.get("kind")).lower() != "character":
            continue
        payload = entity.get("payload") if isinstance(entity.get("payload"), dict) else {}
        names = [
            payload.get("display_label"),
            payload.get("label"),
            entity.get("title"),
            entity.get("entity_id"),
        ]
        if any(_clean(name).lower() == wanted for name in names if _clean(name)):
            return _clean(entity.get("entity_id"))
    return ""


def _normalize_player_identity(selected: list[dict[str, Any]], payload: dict[str, Any], contract: dict[str, Any]) -> tuple[list[str], str]:
    """Resolve player ids/names without mixing different characters.

    Runtime UI payloads may provide selected ids, while scenario contracts provide
    authored defaults. The packet must never combine a selected id from one
    character with a fallback name from another. Explicit session payload wins;
    otherwise use the scenario default id/name as a pair.
    """
    explicit_ids = _split_ids(payload.get("player_character_ids") or payload.get("user_character_ids") or "")
    explicit_name = _clean(payload.get("player_character_name") or payload.get("user_character_name"))
    authored_id = _clean(contract.get("default_player_character_id"))
    authored_name = _clean(contract.get("default_player_character_name"))

    if explicit_ids:
        ids = explicit_ids
        # If a name was explicitly supplied and points to a different loaded
        # character, trust the explicit name/id pair only when they match.
        if explicit_name:
            matched_id = _character_id_for_name(selected, explicit_name)
            if matched_id and matched_id not in ids:
                ids = [matched_id]
            name = explicit_name
        else:
            name = _first_character_name_for_id(selected, ids[0])
        return ids, name or _first_character_name_for_id(selected, ids[0])

    if authored_id:
        return [authored_id], authored_name or _first_character_name_for_id(selected, authored_id)
    if explicit_name:
        matched_id = _character_id_for_name(selected, explicit_name)
        return ([matched_id] if matched_id else []), explicit_name
    return [], ""


def _scenario_control_contract(selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the authored scenario control-center contract when present.

    Forge scenario records can declare who the player controls and which lanes the
    backend may perform. The Scene Packet must preserve that contract as structured
    data, not only as flattened scenario prose; otherwise Scene Chat falls back to
    generic NPC behavior and may speak for the player character.
    """
    for entity in selected:
        if _clean(entity.get("kind")).lower() != "scenario":
            continue
        payload = entity.get("payload") if isinstance(entity.get("payload"), dict) else {}
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        contract = fields.get("control_center_contract") if isinstance(fields.get("control_center_contract"), dict) else {}
        if contract:
            return contract
    return {}

def _load_selected_entities(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity_id in ids:
        entity = _load_entity(conn, entity_id)
        if entity:
            rows.append(entity)
    return rows


def _extract_entity_ids_from_payload(payload: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "universe_ids": _split_ids(payload.get("universe_ids") or payload.get("universe_id")),
        "world_ids": _split_ids(payload.get("world_ids") or payload.get("world_id")),
        "region_ids": _split_ids(payload.get("region_ids") or payload.get("region_id")),
        "city_ids": _split_ids(payload.get("city_ids") or payload.get("city_id")),
        "location_ids": _split_ids(payload.get("location_ids") or payload.get("location_id")),
        "character_ids": _split_ids(payload.get("character_ids") or payload.get("cast_character_ids") or payload.get("cast_ids")),
        "organization_ids": _split_ids(payload.get("organization_ids") or payload.get("organization_id")),
        "artifact_ids": _split_ids(payload.get("artifact_ids") or payload.get("artifact_id")),
        "ritual_ids": _split_ids(payload.get("ritual_ids") or payload.get("ritual_id")),
        "cycle_ids": _split_ids(payload.get("cycle_ids") or payload.get("cycle_id") or payload.get("system_ids") or payload.get("system_id")),
        "creature_ids": _split_ids(payload.get("creature_ids") or payload.get("creature_id")),
        "legend_ids": _split_ids(payload.get("legend_ids") or payload.get("legend_id")),
        "scenario_ids": _split_ids(payload.get("scenario_ids") or payload.get("scenario_id")),
        "relationship_ids": _split_ids(payload.get("relationship_ids") or payload.get("relationship_id")),
    }


def _result_text(item: dict[str, Any]) -> str:
    return _clean(item.get("content") or item.get("snippet") or item.get("text") or "")


def _result_kind(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return _clean(payload.get("source_record_kind") or payload.get("kind") or item.get("kind") or item.get("table") or "memory")


def _classify_retrieved_memory(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = empty_scene_packet_categories()
    for item in results:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        memory_type = _clean(payload.get("memory_type") or item.get("memory_type") or "")
        kind = _result_kind(item).lower()
        scene_category = _clean(payload.get("scene_category") or item.get("scene_category") or "")
        target_category = category_for_memory_type(memory_type, kind, scene_category)
        if target_category not in groups:
            target_category = "retrieved_memory"
        normalized = {
            "title": _clean(item.get("title") or item.get("result_id") or "memory"),
            "content": _result_text(item)[:1800],
            "source_table": _clean(item.get("table") or item.get("source_table") or ""),
            "source_id": _clean(item.get("source_id") or item.get("result_id") or ""),
            "scope_id": _clean(item.get("scope_id") or payload.get("scope_id") or ""),
            "score": item.get("score") or item.get("combined_score") or item.get("rerank_score") or 0,
            "memory_type": memory_type,
            "source_record_kind": kind,
            "scene_category": target_category,
            "semantic_role": _clean(payload.get("semantic_role") or item.get("semantic_role") or ""),
            "matched_compiler_rule": _clean(payload.get("matched_compiler_rule") or item.get("matched_compiler_rule") or ""),
            "lanes": item.get("lanes") or [item.get("lane") or "retrieval"],
        }
        groups[target_category].append(normalized)
    return groups

def _selected_context_rows(selected: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = empty_scene_packet_categories()
    for entity in selected:
        kind = _clean(entity.get("kind")).lower()
        category = category_for_kind(kind)
        if category not in groups:
            category = "retrieved_memory"
        row = {
            "title": _clean(entity.get("title") or entity.get("entity_id")),
            "content": _clean(entity.get("content") or entity.get("summary"))[:2200],
            "source_table": "rp_entities",
            "source_id": _clean(entity.get("entity_id")),
            "scope_id": _clean(entity.get("scope_id")),
            "score": 1.0,
            "memory_type": f"selected_{kind}",
            "source_record_kind": kind,
            "scene_category": category,
            "lanes": ["selected_entity"],
            "payload": entity.get("payload") if isinstance(entity.get("payload"), dict) else {},
        }
        groups[category].append(row)
    return groups

def _merge_grouped_context(a: dict[str, list[dict[str, Any]]], b: dict[str, list[dict[str, Any]]], limit_per_group: int = 24) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    ordered_keys = list(SCENE_PACKET_CATEGORY_ORDER) + [key for key in sorted(set(a) | set(b)) if key not in SCENE_PACKET_CATEGORY_ORDER]
    for key in ordered_keys:
        rows = list(a.get(key) or []) + list(b.get(key) or [])
        seen: set[str] = set()
        clean: list[dict[str, Any]] = []
        for row in rows:
            dedupe = f"{row.get('source_table')}::{row.get('source_id')}::{row.get('title')}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            clean.append(row)
        out[key] = clean[:limit_per_group]
    return out


def _persist_scene_packet(packet: dict[str, Any]) -> None:
    now = _now()
    with _connect() as conn:
        columns = _table_columns(conn, "rp_scene_memory_packets")
        conn.execute(
            """
            INSERT INTO rp_scene_memory_packets(packet_id, scene_id, scope_id, title, emotional_tone, relationship_state_json, character_knowledge_json, canon_locks_json, unresolved_threads_json, continuity_warnings_json, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(packet_id) DO UPDATE SET
                scene_id=excluded.scene_id,
                scope_id=excluded.scope_id,
                title=excluded.title,
                emotional_tone=excluded.emotional_tone,
                relationship_state_json=excluded.relationship_state_json,
                character_knowledge_json=excluded.character_knowledge_json,
                canon_locks_json=excluded.canon_locks_json,
                unresolved_threads_json=excluded.unresolved_threads_json,
                continuity_warnings_json=excluded.continuity_warnings_json,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                packet["scene_packet_id"],
                packet.get("scene_id") or "default",
                packet.get("scope_id") or packet.get("sandbox_id") or "",
                packet.get("title") or "Scene packet",
                (packet.get("model_instructions") or {}).get("tone") or "scene_ready",
                _json(packet.get("relationship_context") or []),
                _json(packet.get("character_context") or []),
                _json(packet.get("canon_guards") or []),
                _json(packet.get("callback_anchors") or []),
                _json(packet.get("continuity_rows") or []),
                _json(packet),
                now,
                now,
            ),
        )
        optional_updates: dict[str, Any] = {
            "sandbox_id": packet.get("sandbox_id") or packet.get("scope_id") or "",
            "project_id": packet.get("project_id") or "",
            "world_id": packet.get("world_id") or "",
            "region_id": packet.get("region_id") or "",
            "city_id": packet.get("city_id") or "",
            "source_record_id": packet.get("scenario_id") or packet.get("scene_id") or "",
            "source_record_kind": "scene_packet",
            "memory_scope": "scene_packet",
            "promotion_scope": packet.get("promotion_scope") or "runtime",
            "sandbox_json": _json(packet.get("sandbox") or {}),
        }
        for col, val in optional_updates.items():
            if col in columns:
                conn.execute(f"UPDATE rp_scene_memory_packets SET {col} = ? WHERE packet_id = ?", (val, packet["scene_packet_id"]))
        conn.commit()
    PHASE11_PACKET_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(PHASE11_PACKET_DIR / f"{packet['scene_packet_id']}.json", packet)


def scene_packet_builder_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE11_SCHEMA_ID,
        "version": PHASE11_VERSION,
        "status": "active",
        "purpose": "Build explicit Scene packets from selected records, runtime retrieval rows, continuity state, and model-control instructions before Scene Chat generation.",
        "endpoints": {
            "contract": "/api/roleplay/scene-packet/contract",
            "state": "/api/roleplay/scene-packet/state",
            "build": "/api/roleplay/scene-packet/build",
        },
        "packet_sections": list(SCENE_PACKET_CATEGORY_ORDER) + [
            "selected_entities",
            "model_instructions",
            "retrieval_trace",
        ],
        "section_labels": SECTION_LABELS,
        "scene_packet_categories": scene_packet_categories_state_payload(write_report=False),
        "locked_rules": [
            "Scene Chat consumes the packet; it must not silently pull unscoped memory.",
            "User-controlled characters are declared in model_instructions and should not be spoken for by the model.",
            "Selected entities are pinned into the packet before retrieved memory.",
            "Every packet persists to rp_scene_memory_packets and neo_data/roleplay/scene_packets/.",
        ],
    }
    if write_report:
        _write_json(PHASE11_CONTRACT_PATH, payload)
    return payload


def scene_packet_builder_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    with _connect() as conn:
        counts = {
            "scene_packets": _table_count(conn, "rp_scene_memory_packets"),
            "retrieval_traces": _table_count(conn, "rp_retrieval_traces"),
            "entities": _table_count(conn, "rp_entities"),
            "memory_fragments": _table_count(conn, "rp_memory_fragments"),
            "search_documents": _table_count(conn, "rp_memory_search_documents"),
        }
        latest_packet = _load_latest_packet_summary(conn)
    runtime_retrieval = runtime_retrieval_lane_state_payload(write_report=False)
    payload = {
        "schema_id": "neo.roleplay.scene_packet_builder.state.v1",
        "version": PHASE11_VERSION,
        "status": "active",
        "ready": counts["entities"] > 0 or counts["memory_fragments"] > 0 or counts["search_documents"] > 0,
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "packet_dir": _relative_to_root(PHASE11_PACKET_DIR),
        "counts": counts,
        "latest_packet": latest_packet,
        "runtime_retrieval_ready": bool(runtime_retrieval.get("ready")),
        "ui": {
            "primary_action": "Build scene packet",
            "preview_fields": ["all canonical record categories", "selected records", "retrieval query", "user role", "npc control", "packet json"],
            "category_count": len(SCENE_PACKET_CATEGORY_ORDER),
        },
    }
    if write_report:
        _write_json(PHASE11_STATE_PATH, payload)
    return payload


def build_scene_packet_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    if bool(payload.get("rebuild_search")):
        rebuild_roleplay_memory_search_documents(limit=_safe_int(payload.get("search_rebuild_limit"), default=20000, maximum=50000))

    ids = _extract_entity_ids_from_payload(payload)
    all_ids = (
        ids["universe_ids"] + ids["world_ids"] + ids["region_ids"] + ids["city_ids"] + ids["location_ids"]
        + ids["character_ids"] + ids["organization_ids"] + ids["artifact_ids"] + ids["ritual_ids"]
        + ids["cycle_ids"] + ids["creature_ids"] + ids["legend_ids"] + ids["scenario_ids"] + ids["relationship_ids"]
    )
    with _connect() as conn:
        selected_entities = _load_selected_entities(conn, all_ids)

    query = _clean(payload.get("query") or payload.get("retrieval_query") or " ".join([_clean(x.get("title")) for x in selected_entities]) or "scene context")
    scope_id = _clean(payload.get("scope_id") or payload.get("sandbox_id") or "")
    limit = _safe_int(payload.get("limit"), default=8, maximum=24)
    mode = _clean(payload.get("mode") or "hybrid") or "hybrid"
    run_retrieval = payload.get("run_retrieval", True) is not False
    retrieval_search: dict[str, Any] = {"results": [], "trace_id": ""}
    if run_retrieval and query:
        retrieval_response = run_runtime_retrieval_payload({
            "query": query,
            "scope_id": scope_id,
            "mode": mode,
            "limit": limit,
            "candidate_limit": _safe_int(payload.get("candidate_limit"), default=max(limit * 2, 16), maximum=48),
            "rerank_candidate_limit": _safe_int(payload.get("rerank_candidate_limit"), default=min(max(limit, 6), 10), maximum=16),
            "rerank": payload.get("rerank", True),
            "rebuild_search": False,
            "memory_types": payload.get("memory_types") or "entities,memory_fragments,shared_memories,continuity,turn_summaries,story_checkpoints",
        })
        retrieval_search = retrieval_response.get("search") or {}

    selected_groups = _selected_context_rows(selected_entities)
    retrieved_groups = _classify_retrieved_memory(retrieval_search.get("results") or [])
    groups = _merge_grouped_context(selected_groups, retrieved_groups, limit_per_group=_safe_int(payload.get("limit_per_section"), default=24, maximum=80))

    now = _now()
    scene_id = _clean(payload.get("scene_id") or "default") or "default"
    packet_id = _clean(payload.get("scene_packet_id") or f"scene_packet_{uuid.uuid4().hex[:10]}")
    scenario_contract = _scenario_control_contract(selected_entities)
    player_character_ids, player_character_name = _normalize_player_identity(selected_entities, payload, scenario_contract)
    authored_npc_ids = scenario_contract.get("assistant_controlled_character_ids") if isinstance(scenario_contract.get("assistant_controlled_character_ids"), list) else []
    npc_character_ids = _split_ids(payload.get("npc_character_ids") or payload.get("assistant_controlled_character_ids") or authored_npc_ids)
    if not npc_character_ids:
        npc_character_ids = [cid for cid in ids["character_ids"] if cid not in set(player_character_ids)]
    else:
        npc_character_ids = [cid for cid in npc_character_ids if cid not in set(player_character_ids)]
    packet = {
        "schema_id": PHASE11_SCHEMA_ID,
        "version": PHASE11_VERSION,
        "scene_packet_id": packet_id,
        "packet_id": packet_id,
        "scene_id": scene_id,
        "title": _clean(payload.get("title") or f"Scene Packet · {scene_id}"),
        "status": "built",
        "created_at": now,
        "updated_at": now,
        "project_id": _clean(payload.get("project_id")),
        "sandbox_id": _clean(payload.get("sandbox_id") or scope_id),
        "scope_id": scope_id,
        "world_id": ids["world_ids"][0] if ids["world_ids"] else _clean(payload.get("world_id")),
        "region_id": ids["region_ids"][0] if ids["region_ids"] else _clean(payload.get("region_id")),
        "city_id": ids["city_ids"][0] if ids["city_ids"] else _clean(payload.get("city_id")),
        "scenario_id": ids["scenario_ids"][0] if ids["scenario_ids"] else _clean(payload.get("scenario_id")),
        "focus_entity_ids": all_ids,
        "player_character_id": player_character_ids[0] if player_character_ids else "",
        "player_character_name": player_character_name,
        "npc_character_ids": npc_character_ids,
        "control_center_contract": scenario_contract,
        "selected_entities": selected_entities,
        "scene_packet_category_order": list(SCENE_PACKET_CATEGORY_ORDER),
        "section_labels": SECTION_LABELS,
        "universe_context": groups.get("universe_context", []),
        "world_context": groups.get("world_context", []),
        "region_context": groups.get("region_context", []),
        "city_context": groups.get("city_context", []),
        "location_context": groups.get("location_context", []),
        "character_context": groups.get("character_context", []),
        "relationship_context": groups.get("relationship_context", []),
        "organization_context": groups.get("organization_context", []),
        "artifact_context": groups.get("artifact_context", []),
        "ritual_context": groups.get("ritual_context", []),
        "system_context": groups.get("system_context", []),
        "creature_context": groups.get("creature_context", []),
        "legend_context": groups.get("legend_context", []),
        "scenario_context": groups.get("scenario_context", []),
        "canon_guards": groups.get("canon_guards", []),
        "reveal_gates": groups.get("reveal_gates", []),
        "callback_anchors": groups.get("callback_anchors", []),
        "continuity_rows": groups.get("continuity_rows", []),
        "retrieved_memory": groups.get("retrieved_memory", []),
        "retrieval_trace": {
            "trace_id": retrieval_search.get("trace_id") or "",
            "query": query,
            "mode": mode,
            "result_count": len(retrieval_search.get("results") or []),
            "diagnostics": retrieval_search.get("diagnostics") or {},
        },
        "model_instructions": {
            "player_control": _clean(payload.get("player_control") or "User controls the player character(s). Do not write their dialogue, thoughts, or actions."),
            "npc_control": _clean(payload.get("npc_control") or "Model controls narrator and non-player characters only."),
            "player_character_ids": player_character_ids,
            "player_character_id": player_character_ids[0] if player_character_ids else "",
            "player_character_name": player_character_name,
            "npc_character_ids": npc_character_ids,
            "assistant_controlled_lanes": scenario_contract.get("assistant_controlled_lanes") if isinstance(scenario_contract.get("assistant_controlled_lanes"), list) else [],
            "forbidden_player_control": scenario_contract.get("forbidden_player_control") if isinstance(scenario_contract.get("forbidden_player_control"), list) else [],
            "unknown_detail_policy": _clean(scenario_contract.get("unknown_detail_policy") or "If a detail is not explicit in the packet, scene state, transcript, or user turn, leave it unstated."),
            "tone": _clean(payload.get("tone") or payload.get("scene_tone") or "canon-aware, emotionally grounded, continuity-safe"),
            "forbidden_behavior": _clean(payload.get("forbidden_behavior") or scenario_contract.get("forbidden_escalations") or "Do not contradict canon guards, reveal gated secrets early, introduce unsupported intimate/conflict allegations, or override user POV."),
        },
        "sandbox": {
            "project_id": _clean(payload.get("project_id")),
            "sandbox_id": _clean(payload.get("sandbox_id") or scope_id),
            "scope_id": scope_id,
            "promotion_scope": _clean(payload.get("promotion_scope") or "runtime"),
            "memory_scope": "scene_packet",
        },
        "counts": {
            "selected_entities": len(selected_entities),
            "retrieved_results": len(retrieval_search.get("results") or []),
            **{category: len(groups.get(category, [])) for category in SCENE_PACKET_CATEGORY_ORDER},
        },
    }
    _persist_scene_packet(packet)
    return {
        "schema_id": "neo.roleplay.scene_packet_builder.response.v1",
        "status": "built",
        "scene_packet": packet,
        "scene_packet_builder": scene_packet_builder_state_payload(write_report=True),
    }
