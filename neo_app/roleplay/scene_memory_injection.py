from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema
from neo_app.roleplay.scene_packet_categories import SCENE_PACKET_CATEGORY_ORDER, SECTION_LABELS

PHASE12_SCHEMA_ID: Final[str] = "neo.roleplay.scene_memory_injection.v1"
PHASE12_VERSION: Final[str] = "1.0.0-phase12-scene-chat-memory-injection"
PHASE12_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_memory_injection_contract.json"
PHASE12_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_memory_injection_state.json"
PHASE12_TRACE_DIR: Final[Path] = ROLEPLAY_DATA_ROOT / "scene_memory_injection_traces"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()




def _safe_filename_part(value: Any, *, fallback: str = "item", limit: int = 96) -> str:
    """Return a Windows/macOS/Linux safe filename segment.

    Scene ids can contain namespace separators such as ':' (for example
    default_human_scene:default). Windows rejects ':' in file names, so trace
    writes must sanitize ids before using them as file paths.
    """
    text = _clean(value) or fallback
    text = re.sub(r'[<>:"/\\|?*]+', '_', text)
    text = re.sub(r'\s+', '_', text).strip(' ._')
    if not text:
        text = fallback
    reserved = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
    }
    if text.upper() in reserved:
        text = f'{text}_id'
    return text[: max(16, int(limit or 96))]

def _read_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default




def _is_human_scene_packet_id(value: Any) -> bool:
    return _clean(value).lower().startswith("human_scene:")


def _non_human_packet_where(extra: str = "") -> str:
    suffix = f" AND {extra}" if extra else ""
    return f"packet_id NOT LIKE 'human_scene:%'{suffix}"


def _latest_scene_packet_id_for_runtime(runtime_bundle_id: str) -> str:
    clean = _clean(runtime_bundle_id)
    if not clean:
        return ""
    try:
        with _connect() as conn:
            if not _table_exists(conn, "rp_runtime_presets"):
                return ""
            row = conn.execute(
                """
                SELECT latest_scene_packet_id
                FROM rp_runtime_presets
                WHERE runtime_bundle_id = ? AND latest_scene_packet_id != ''
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean,),
            ).fetchone()
            return _clean(row["latest_scene_packet_id"] if row else "")
    except Exception:
        return ""

def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        if not _table_exists(conn, table):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _packet_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    payload = _read_json(row["payload_json"], {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("scene_packet_id", row["packet_id"])
    payload.setdefault("packet_id", row["packet_id"])
    payload.setdefault("scene_id", row["scene_id"])
    payload.setdefault("scope_id", row["scope_id"])
    payload.setdefault("title", row["title"])
    payload.setdefault("updated_at", row["updated_at"])
    return payload


def load_scene_packet_payload(*, scene_packet_id: str = "", scene_id: str = "default", scope_id: str = "", runtime_bundle_id: str = "") -> dict[str, Any] | None:
    """Load the active authored/runtime Scene Packet from SQLite.

    Human-scene memory packets share the same table for historical reasons, but
    they are continuity state, not the authoritative Scene Packet. Scene Chat
    must not select them as Active Scene Packet Memory.
    """
    runtime_packet_id = _latest_scene_packet_id_for_runtime(runtime_bundle_id or scope_id)
    requested_packet = _clean(scene_packet_id)
    packet_candidates = []
    if requested_packet and not _is_human_scene_packet_id(requested_packet):
        packet_candidates.append(requested_packet)
    if runtime_packet_id and runtime_packet_id not in packet_candidates:
        packet_candidates.append(runtime_packet_id)
    with _connect() as conn:
        if not _table_exists(conn, "rp_scene_memory_packets"):
            return None
        for packet_id in packet_candidates:
            row = conn.execute(
                "SELECT packet_id, scene_id, scope_id, title, payload_json, updated_at FROM rp_scene_memory_packets WHERE packet_id = ? AND packet_id NOT LIKE 'human_scene:%' ORDER BY updated_at DESC LIMIT 1",
                (packet_id,),
            ).fetchone()
            if row:
                return _packet_from_row(row)
        clean_scene_id = _clean(scene_id) or "default"
        row = conn.execute(
            "SELECT packet_id, scene_id, scope_id, title, payload_json, updated_at FROM rp_scene_memory_packets WHERE scene_id = ? AND packet_id NOT LIKE 'human_scene:%' ORDER BY updated_at DESC LIMIT 1",
            (clean_scene_id,),
        ).fetchone()
        if row:
            return _packet_from_row(row)
        clean_scope = _clean(scope_id)
        if clean_scope:
            row = conn.execute(
                "SELECT packet_id, scene_id, scope_id, title, payload_json, updated_at FROM rp_scene_memory_packets WHERE scope_id = ? AND packet_id NOT LIKE 'human_scene:%' ORDER BY updated_at DESC LIMIT 1",
                (clean_scope,),
            ).fetchone()
            if row:
                return _packet_from_row(row)
        row = conn.execute(
            "SELECT packet_id, scene_id, scope_id, title, payload_json, updated_at FROM rp_scene_memory_packets WHERE packet_id NOT LIKE 'human_scene:%' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return _packet_from_row(row)




def _packet_has_context_sections(packet: dict[str, Any] | None) -> bool:
    if not isinstance(packet, dict) or not packet:
        return False
    return any(bool(packet.get(section)) for section in SCENE_PACKET_CATEGORY_ORDER)


def _authoritative_scene_id(packet: dict[str, Any] | None, fallback: str = "default") -> str:
    if not isinstance(packet, dict) or not packet:
        return _clean(fallback) or "default"
    scene_id = _clean(packet.get("scene_id") or packet.get("scenario_id"))
    if scene_id and scene_id.lower() != "default" and not scene_id.lower().startswith("human_scene:"):
        return scene_id
    rows = packet.get("scenario_context") if isinstance(packet.get("scenario_context"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = _clean(row.get("source_id") or row.get("id"))
        if source_id:
            return source_id
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        payload_id = _clean(payload.get("id"))
        if payload_id:
            return payload_id
    return _clean(fallback) or "default"


def _packet_from_runtime_bundle(runtime_bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(runtime_bundle, dict) or not runtime_bundle:
        return None
    packet = runtime_bundle.get("scene_packet")
    if isinstance(packet, dict) and packet:
        packet.setdefault("scene_packet_id", packet.get("packet_id") or runtime_bundle.get("bundle_id") or "runtime_bundle_scene_packet")
        packet.setdefault("source", "runtime_bundle.scene_packet")
        return packet
    return None


def _compact_text(value: Any, limit: int = 1200) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _character_name_for_id(packet: dict[str, Any], character_id: str) -> str:
    wanted = _clean(character_id)
    if not wanted or not isinstance(packet, dict):
        return ""
    for row in packet.get("character_context") or []:
        if not isinstance(row, dict):
            continue
        if _clean(row.get("source_id")) != wanted:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return _clean(row.get("title") or row.get("display_label") or payload.get("display_label") or payload.get("label"))
    return ""


def _character_id_for_name(packet: dict[str, Any], character_name: str) -> str:
    wanted = _clean(character_name).lower()
    if not wanted or not isinstance(packet, dict):
        return ""
    for row in packet.get("character_context") or []:
        if not isinstance(row, dict):
            continue
        source_id = _clean(row.get("source_id"))
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        names = [row.get("title"), row.get("display_label"), source_id, payload.get("display_label"), payload.get("label")]
        if any(_clean(name).lower() == wanted for name in names if _clean(name)):
            return source_id
    return ""


def _repair_packet_control_identity(packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(packet, dict) or not packet:
        return packet
    instructions = packet.get("model_instructions") if isinstance(packet.get("model_instructions"), dict) else {}
    player_name = _clean(packet.get("player_character_name") or instructions.get("player_character_name"))
    player_id = _clean(packet.get("player_character_id") or instructions.get("player_character_id"))
    matched_id = _character_id_for_name(packet, player_name) if player_name else ""
    if matched_id and player_id and matched_id != player_id:
        player_id = matched_id
    if not player_id and matched_id:
        player_id = matched_id
    if player_id and not player_name:
        player_name = _character_name_for_id(packet, player_id)
    if player_id:
        packet["player_character_id"] = player_id
        ids = instructions.get("player_character_ids") if isinstance(instructions.get("player_character_ids"), list) else []
        if player_id not in ids:
            ids = [player_id]
        instructions["player_character_ids"] = ids
        instructions["player_character_id"] = player_id
    if player_name:
        packet["player_character_name"] = player_name
        instructions["player_character_name"] = player_name
    npc_ids = packet.get("npc_character_ids") if isinstance(packet.get("npc_character_ids"), list) else []
    if player_id and npc_ids:
        packet["npc_character_ids"] = [item for item in npc_ids if _clean(item) != player_id]
    instr_npc = instructions.get("npc_character_ids") if isinstance(instructions.get("npc_character_ids"), list) else []
    if player_id and instr_npc:
        instructions["npc_character_ids"] = [item for item in instr_npc if _clean(item) != player_id]
    if instructions:
        packet["model_instructions"] = instructions
    return packet


def _character_identity_rows(packet: dict[str, Any]) -> list[str]:
    """Return compact character identity/control rows for prompt anchoring.

    This is schema-driven and generic: names, pronouns, gender, and control lanes
    are copied from the active Scene Packet records instead of hardcoded lore.
    """
    if not isinstance(packet, dict):
        return []
    instructions = packet.get("model_instructions") if isinstance(packet.get("model_instructions"), dict) else {}
    player_ids = {str(x).strip() for x in (instructions.get("player_character_ids") or []) if str(x).strip()}
    npc_ids = {str(x).strip() for x in (instructions.get("npc_character_ids") or []) if str(x).strip()}
    rows: list[str] = []
    for row in packet.get("character_context") or []:
        if not isinstance(row, dict):
            continue
        source_id = _clean(row.get("source_id"))
        title = _clean(row.get("title") or source_id)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        identity = fields.get("identity") if isinstance(fields.get("identity"), dict) else {}
        roleplay_control = fields.get("roleplay_control") if isinstance(fields.get("roleplay_control"), dict) else {}
        gender = _clean(identity.get("gender"))
        pronouns = _clean(identity.get("pronouns"))
        public_label = _clean(identity.get("public_identity_label") or title)
        control = "user-controlled" if source_id in player_ids or roleplay_control.get("assistant_must_not_control") is True else "assistant-controlled" if source_id in npc_ids or roleplay_control.get("assistant_may_control") is True else _clean(roleplay_control.get("control_lane")) or "scene-cast"
        bits = [public_label]
        if source_id:
            bits.append(f"id={source_id}")
        if pronouns:
            bits.append(f"pronouns={pronouns}")
        if gender:
            bits.append(f"gender={gender}")
        if control:
            bits.append(f"control={control}")
        rows.append("- " + "; ".join(bits))
    return rows


def _section_lines(packet: dict[str, Any], section: str, label: str, *, limit: int = 4, text_limit: int = 420) -> list[str]:
    rows = packet.get(section) if isinstance(packet.get(section), list) else []
    out: list[str] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("title") or row.get("source_id") or row.get("memory_type") or label)
        content = _compact_text(row.get("content") or row.get("summary") or row.get("text") or "", text_limit)
        source = _clean(row.get("source_id") or row.get("source_table") or "")
        if content:
            suffix = f" [{source}]" if source else ""
            out.append(f"- {label}: {title}{suffix} — {content}")
    return out


def scene_packet_prompt_lines(packet: dict[str, Any] | None, *, limit_per_section: int = 4, text_limit: int = 420, max_lines: int = 72) -> list[str]:
    if not isinstance(packet, dict) or not packet:
        return []
    lines: list[str] = []
    title = _clean(packet.get("title") or packet.get("scene_packet_id") or "Scene packet")
    lines.append(f"Active Scene Packet: {title}")
    lines.append(f"Packet id: {_clean(packet.get('scene_packet_id') or packet.get('packet_id')) or 'unknown'}")
    trace = packet.get("retrieval_trace") if isinstance(packet.get("retrieval_trace"), dict) else {}
    if trace.get("query") or trace.get("trace_id"):
        lines.append(f"Retrieval trace: {_clean(trace.get('trace_id')) or 'none'} · query: {_compact_text(trace.get('query'), 300)}")
    instructions = packet.get("model_instructions") if isinstance(packet.get("model_instructions"), dict) else {}
    if instructions:
        lines.append("Model control rules from packet:")
        for key in ["player_control", "npc_control", "forbidden_behavior", "tone"]:
            value = _clean(instructions.get(key))
            if value:
                lines.append(f"- {key.replace('_', ' ').title()}: {_compact_text(value, 600)}")
        player_ids = instructions.get("player_character_ids") if isinstance(instructions.get("player_character_ids"), list) else []
        if player_ids:
            lines.append("- Player-controlled character ids: " + ", ".join(_clean(x) for x in player_ids if _clean(x)))
    identity_rows = _character_identity_rows(packet)
    if identity_rows:
        lines.append("Character identity/control roster:")
        lines.extend(identity_rows[:12])
    section_map = [(section, SECTION_LABELS.get(section, section.replace("_", " ").title())) for section in SCENE_PACKET_CATEGORY_ORDER]
    for section, label in section_map:
        section_rows = _section_lines(packet, section, label, limit=limit_per_section, text_limit=text_limit)
        if section_rows:
            lines.append(f"{label} context:")
            lines.extend(section_rows)
    return lines[:max(12, int(max_lines or 72))]


def scene_memory_injection_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE12_SCHEMA_ID,
        "version": PHASE12_VERSION,
        "status": "active",
        "purpose": "Inject the active Phase 11 Scene Packet into Scene Chat prompts and expose proof of which scoped memory was used.",
        "endpoints": {
            "contract": "/api/roleplay/scene-memory-injection/contract",
            "state": "/api/roleplay/scene-memory-injection/state",
            "preview": "/api/roleplay/scene-memory-injection/preview",
        },
        "prompt_sections": [
            "active scene packet id",
            "model-control rules",
            "universe/world/region/city/location context",
            "character/relationship/organization context",
            "artifact/ritual/system/creature/legend/scenario context",
            "canon guards",
            "reveal gates",
            "callback anchors",
            "continuity rows",
            "retrieved memory",
        ],
        "locked_rules": [
            "Scene Chat must include the active Scene Packet when available.",
            "User/player-controlled characters remain under user control.",
            "The assistant turn stores scene_packet_id and injection proof metadata.",
            "If no Scene Packet exists, Scene Chat may fall back to setup/runtime/retrieval but must report packet_missing.",
        ],
    }
    if write_report:
        _write_json(PHASE12_CONTRACT_PATH, payload)
    return payload


def scene_memory_injection_state_payload(*, scene_id: str = "default", scene_packet_id: str = "", scope_id: str = "", write_report: bool = False) -> dict[str, Any]:
    packet = load_scene_packet_payload(scene_packet_id=scene_packet_id, scene_id=scene_id, scope_id=scope_id, runtime_bundle_id=scope_id)
    with _connect() as conn:
        counts = {
            "scene_packets": _table_count(conn, "rp_scene_memory_packets"),
            "retrieval_traces": _table_count(conn, "rp_retrieval_traces"),
            "memory_fragments": _table_count(conn, "rp_memory_fragments"),
            "continuity_rows": _table_count(conn, "rp_continuity_rows"),
        }
    packet_counts = packet.get("counts") if isinstance(packet, dict) and isinstance(packet.get("counts"), dict) else {}
    payload = {
        "schema_id": "neo.roleplay.scene_memory_injection.state.v1",
        "version": PHASE12_VERSION,
        "status": "active" if packet else "packet_missing",
        "ready": bool(packet),
        "scene_id": scene_id,
        "scene_packet_id": _clean((packet or {}).get("scene_packet_id") or (packet or {}).get("packet_id")),
        "title": _clean((packet or {}).get("title")),
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "counts": counts,
        "packet_counts": packet_counts,
        "preview_lines": scene_packet_prompt_lines(packet, limit_per_section=2)[:20] if packet else [],
        "reason": "Scene Chat will inject the active Scene Packet." if packet else "No Scene Packet found for this scene/scope yet. Build one in Studio > Runtime first.",
    }
    if write_report:
        _write_json(PHASE12_STATE_PATH, payload)
    return payload


def build_scene_memory_injection_payload(*, setup: dict[str, Any], request_payload: dict[str, Any], runtime_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    scene_id = _clean(request_payload.get("scene_id") or setup.get("scene_id") or "default") or "default"
    explicit_packet_id = _clean(
        request_payload.get("scene_packet_id")
        or request_payload.get("active_scene_packet_id")
        or setup.get("scene_packet_id")
        or ""
    )
    runtime_bundle_id = _clean(request_payload.get("runtime_bundle_id") or setup.get("runtime_bundle_id") or ((runtime_bundle or {}).get("bundle_id") if isinstance(runtime_bundle, dict) else ""))
    if _is_human_scene_packet_id(explicit_packet_id):
        explicit_packet_id = ""
    if not explicit_packet_id:
        explicit_packet_id = _latest_scene_packet_id_for_runtime(runtime_bundle_id)
    scope_id = _clean(request_payload.get("scope_id") or runtime_bundle_id or setup.get("memory_scope") or scene_id)
    stored_packet = load_scene_packet_payload(scene_packet_id=explicit_packet_id, scene_id=scene_id, scope_id=scope_id, runtime_bundle_id=runtime_bundle_id)
    runtime_packet = _packet_from_runtime_bundle(runtime_bundle)
    # Runtime bundles carry a small placeholder scene_packet. Do not let that
    # override the real packet persisted by Runtime > Build Scene Packet.
    if _packet_has_context_sections(stored_packet):
        packet = stored_packet
    elif _packet_has_context_sections(runtime_packet):
        packet = runtime_packet
    else:
        packet = stored_packet or runtime_packet
    packet = _repair_packet_control_identity(packet) if packet else packet
    try:
        limit_per_section = max(1, min(6, int(request_payload.get("scene_packet_limit_per_section") or 4)))
    except Exception:
        limit_per_section = 4
    try:
        text_limit = max(160, min(700, int(request_payload.get("scene_packet_text_limit") or 420)))
    except Exception:
        text_limit = 420
    try:
        max_lines = max(18, min(96, int(request_payload.get("scene_packet_max_lines") or 72)))
    except Exception:
        max_lines = 72
    lines = scene_packet_prompt_lines(packet, limit_per_section=limit_per_section, text_limit=text_limit, max_lines=max_lines) if packet else []
    active_scene_id = _authoritative_scene_id(packet, scene_id)
    state = scene_memory_injection_state_payload(scene_id=active_scene_id, scene_packet_id=_clean((packet or {}).get("scene_packet_id") or explicit_packet_id), scope_id=scope_id, write_report=True)
    trace = {
        "schema_id": "neo.roleplay.scene_memory_injection.trace.v1",
        "version": PHASE12_VERSION,
        "created_at": _now(),
        "scene_id": active_scene_id,
        "requested_scene_id": scene_id,
        "status": "injected" if packet else "packet_missing",
        "scene_packet_id": _clean((packet or {}).get("scene_packet_id") or (packet or {}).get("packet_id")),
        "line_count": len(lines),
        "sections": {key: len(packet.get(key) or []) for key in SCENE_PACKET_CATEGORY_ORDER} if packet else {},
        "context_caps": {"limit_per_section": limit_per_section if packet else 0, "text_limit": text_limit if packet else 0, "max_lines": max_lines if packet else 0},
        "state": state,
    }
    if trace["scene_packet_id"]:
        safe_scene_id = _safe_filename_part(active_scene_id, fallback="scene")
        safe_packet_id = _safe_filename_part(trace["scene_packet_id"], fallback="packet")
        trace_name = f"{safe_scene_id}_{safe_packet_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
        trace["trace_file"] = trace_name
        try:
            _write_json(PHASE12_TRACE_DIR / trace_name, trace)
        except OSError as exc:
            # Trace writing is diagnostic-only. It must never break Scene Chat
            # generation or streaming. Keep the error visible in the returned
            # trace payload so the UI/debug panel can show it later.
            trace["trace_write_status"] = "failed"
            trace["trace_write_error"] = str(exc)
        else:
            trace["trace_write_status"] = "written"
    return {
        "schema_id": "neo.roleplay.scene_memory_injection.preview.v1",
        "status": trace["status"],
        "ready": bool(packet),
        "scene_id": active_scene_id,
        "requested_scene_id": scene_id,
        "scene_packet": packet or {},
        "lines": lines,
        "trace": trace,
        "state": state,
    }
