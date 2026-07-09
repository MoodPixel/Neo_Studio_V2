from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.text_backend import execute_roleplay_text_backend, execute_roleplay_text_backend_stream, resolve_roleplay_text_backend
from neo_app.roleplay.sqlite_store import upsert_scene_setup_memory, upsert_scene_turn_memory, roleplay_sqlite_state_payload
from neo_app.roleplay.runtime import list_runtime_bundles, get_runtime_bundle
from neo_app.roleplay.retrieval import search_retrieval_foundation_payload
from neo_app.roleplay.human_memory import human_memory_prompt_lines, sync_scene_human_memory, build_roleplay_human_scene_packet
from neo_app.roleplay.scene_memory_injection import build_scene_memory_injection_payload, scene_memory_injection_state_payload, scene_memory_injection_contract_payload, load_scene_packet_payload
from neo_app.roleplay.turn_writeback import writeback_scene_turn, turn_writeback_state_payload, archive_scene_runtime_writebacks
from neo_app.control_center.roleplay_controller import roleplay_control_context_payload
from neo_app.roleplay.scene_director_runtime import roleplay_scene_director_preflight_payload, roleplay_scene_director_validate_payload
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot


def _roleplay_runtime_summary(*, scene_id: str = "", prompt: dict[str, Any] | None = None, result: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt = prompt if isinstance(prompt, dict) else {}
    result = result if isinstance(result, dict) else {}
    transcript = result.get("transcript") if isinstance(result.get("transcript"), dict) else (prompt.get("transcript") if isinstance(prompt.get("transcript"), dict) else {})
    assistant_turn = result.get("assistant_turn") if isinstance(result.get("assistant_turn"), dict) else {}
    user_turn = result.get("user_turn") if isinstance(result.get("user_turn"), dict) else {}
    scene_packet = prompt.get("scene_packet") if isinstance(prompt.get("scene_packet"), dict) else (result.get("scene_packet") if isinstance(result.get("scene_packet"), dict) else {})
    payload: dict[str, Any] = {
        "schema_id": "neo.roleplay.runtime_log.summary.pass_m.v1",
        "scene_id": scene_id or prompt.get("scene_id") or result.get("scene_id") or "",
        "active_scene_id": prompt.get("active_scene_id") or "",
        "status": result.get("status") or "",
        "ok": result.get("ok") if "ok" in result else None,
        "runtime_bundle_id": prompt.get("runtime_bundle_id") or assistant_turn.get("runtime_bundle_id") or "",
        "scene_packet_id": scene_packet.get("scene_packet_id") or scene_packet.get("packet_id") or assistant_turn.get("scene_packet_id") or "",
        "message_count": len(prompt.get("messages") or []),
        "turn_count": len(transcript.get("turns") or []) if isinstance(transcript, dict) else 0,
        "user_turn_id": user_turn.get("turn_id") or "",
        "assistant_turn_id": assistant_turn.get("turn_id") or "",
        "assistant_turn_status": assistant_turn.get("status") or "",
        "backend_profile_id": assistant_turn.get("backend_profile_id") or "",
        "retrieval_trace_id": assistant_turn.get("retrieval_trace_id") or ((prompt.get("retrieval") or {}).get("search") or {}).get("trace_id") or "",
        "memory_injection_status": assistant_turn.get("memory_injection_status") or ((prompt.get("scene_memory_injection") or {}).get("status") if isinstance(prompt.get("scene_memory_injection"), dict) else ""),
        "scene_director_validation_status": assistant_turn.get("scene_director_validation_status") or ((result.get("scene_director_validation") or {}).get("status") if isinstance(result.get("scene_director_validation"), dict) else ""),
        "scene_director_warning_count": assistant_turn.get("scene_director_warning_count") or ((result.get("scene_director_validation") or {}).get("warning_count") if isinstance(result.get("scene_director_validation"), dict) else 0),
        "user_message_char_count": len(str(prompt.get("user_message") or user_turn.get("text") or "")),
        "assistant_text_char_count": len(str(assistant_turn.get("text") or "")),
    }
    if extra:
        payload.update(extra)
    return payload


def _log_roleplay_runtime_event(event: str, *, scene_id: str, prompt: dict[str, Any] | None = None, result: dict[str, Any] | None = None, level: str = "INFO", snapshot_name: str | None = None, extra: dict[str, Any] | None = None) -> None:
    try:
        summary = _roleplay_runtime_summary(scene_id=scene_id, prompt=prompt, result=result, extra=extra)
        log_surface_event("roleplay", event, run_id=summary.get("assistant_turn_id") or scene_id, level=level, payload=summary)
        if snapshot_name:
            record_surface_snapshot("roleplay", snapshot_name, summary, run_id=summary.get("assistant_turn_id") or scene_id)
    except Exception:
        pass


def _log_roleplay_runtime_error(message: str, *, scene_id: str = "", prompt: dict[str, Any] | None = None, result: dict[str, Any] | None = None, exc: BaseException | None = None, extra: dict[str, Any] | None = None) -> None:
    try:
        summary = _roleplay_runtime_summary(scene_id=scene_id, prompt=prompt, result=result, extra=extra)
        record_surface_error("roleplay", message, exc=exc, payload=summary, run_id=summary.get("assistant_turn_id") or scene_id or None)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:72] or "scene"


def _scene_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "story_sessions" / "scene_foundation"


def _runtime_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "runtime_bundles"


def _scene_setup_path(scene_id: str = "default") -> Path:
    return _scene_dir() / f"{_slug(scene_id)}.setup.json"


def _scene_transcript_path(scene_id: str = "default") -> Path:
    return _scene_dir() / f"{_slug(scene_id)}.transcript.json"


def ensure_scene_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    _scene_dir().mkdir(parents=True, exist_ok=True)
    _runtime_dir().mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _runtime_bundle_options() -> list[dict[str, Any]]:
    ensure_scene_storage()
    options: list[dict[str, Any]] = []
    for data in list_runtime_bundles():
        bundle_id = str(data.get("bundle_id") or data.get("runtime_bundle_id") or "")
        if not bundle_id:
            continue
        options.append({
            "bundle_id": bundle_id,
            "title": str(data.get("title") or data.get("name") or bundle_id),
            "status": str(data.get("status") or "foundation"),
            "storage_path": str(data.get("storage_path") or ""),
            "counts": data.get("counts") or {},
            "selectable": True,
            "reason": "Runtime bundle compiled by Studio foundation compiler.",
        })
    return options


def load_scene_setup(scene_id: str = "default") -> dict[str, Any]:
    ensure_scene_storage()
    path = _scene_setup_path(scene_id)
    setup = _load_json(path, {})
    if not isinstance(setup, dict) or not setup:
        setup = {
            "scene_id": scene_id,
            "title": "Untitled Scene",
            "premise": "",
            "tone": "Scene-defined",
            "reply_style": "Scene-defined prose",
            "scene_notes": "",
            "narrator_posture": "partner_focus",
            "continuity_mode": "runtime_anchored",
            "runtime_bundle_id": "",
            "scene_packet_id": "",
            "participants": "",
            "memory_scope": "roleplay.scene",
            "scene_rules": "",
            "autosave_checkpoint": False,
            "turn_input_style": "free_typing",
            "created_at": "",
            "updated_at": "",
            "storage_path": _relative_to_root(path),
        }
    setup.setdefault("scene_id", scene_id)
    setup.setdefault("storage_path", _relative_to_root(path))
    return setup


def save_scene_setup_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_scene_storage()
    scene_id = str(payload.get("scene_id") or "default")
    existing = load_scene_setup(scene_id)
    now = _now()
    setup = {
        "scene_id": scene_id,
        "title": str(payload.get("title") or payload.get("scene_title") or existing.get("title") or "Untitled Scene"),
        "premise": str(payload.get("premise") or existing.get("premise") or ""),
        "tone": str(payload.get("tone") or existing.get("tone") or "Scene-defined"),
        "reply_style": str(payload.get("reply_style") or payload.get("style") or existing.get("reply_style") or "Scene-defined prose"),
        "scene_notes": str(payload.get("scene_notes") or payload.get("notes") or existing.get("scene_notes") or ""),
        "narrator_posture": str(payload.get("narrator_posture") or existing.get("narrator_posture") or "partner_focus"),
        "continuity_mode": str(payload.get("continuity_mode") or existing.get("continuity_mode") or "runtime_anchored"),
        "runtime_bundle_id": str(payload.get("runtime_bundle_id") or existing.get("runtime_bundle_id") or ""),
        "scene_packet_id": str(payload.get("scene_packet_id") or payload.get("active_scene_packet_id") or existing.get("scene_packet_id") or ""),
        "participants": str(payload.get("participants") or existing.get("participants") or ""),
        "memory_scope": str(payload.get("memory_scope") or existing.get("memory_scope") or "roleplay.scene"),
        "scene_rules": str(payload.get("scene_rules") or existing.get("scene_rules") or ""),
        "autosave_checkpoint": bool(payload.get("autosave_checkpoint", existing.get("autosave_checkpoint") or False)),
        "turn_input_style": str(payload.get("turn_input_style") or existing.get("turn_input_style") or "free_typing"),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "storage_path": _relative_to_root(_scene_setup_path(scene_id)),
    }
    # If the selected Scene Packet changes before a session has actually started,
    # clear stale session setup so the Roleplay-only setup card appears again.
    #
    # Important: the Scene Packet identity can be either the packet id inside a
    # runtime bundle or the runtime bundle id selected in the UI. First-turn
    # dispatch previously autosaved setup right before sending, compared those
    # two different identifiers, and reset a valid ready session before the
    # backend was contacted. Keep the session when the runtime bundle is the
    # same or when the active packet resolves to the same packet id.
    transcript_path = _scene_transcript_path(scene_id)
    transcript = _load_json(transcript_path, {})
    if isinstance(transcript, dict) and transcript:
        session_setup = transcript.get("session_setup") if isinstance(transcript.get("session_setup"), dict) else {}
        old_packet = str(session_setup.get("scene_packet_id") or "").strip()
        old_runtime = str(session_setup.get("runtime_bundle_id") or "").strip()
        new_runtime = str(setup.get("runtime_bundle_id") or "").strip()
        new_packet_raw = str(setup.get("scene_packet_id") or "").strip()
        active_new_packet = ""
        try:
            active_bundle = get_runtime_bundle(new_runtime) if new_runtime else None
            active_packet = _active_scene_packet_for_setup(setup, active_bundle)
            active_new_packet = str(active_packet.get("scene_packet_id") or active_packet.get("packet_id") or "").strip() if isinstance(active_packet, dict) else ""
        except Exception:
            active_new_packet = ""
        same_runtime = bool(old_runtime and new_runtime and old_runtime == new_runtime)
        same_packet = bool(old_packet and old_packet in {new_packet_raw, new_runtime, active_new_packet})
        should_reset_session = bool(old_packet and not same_runtime and not same_packet and not _scene_has_played_turn(transcript))
        if should_reset_session:
            transcript.pop("session_setup", None)
            transcript["turns"] = [t for t in (transcript.get("turns") or []) if not (isinstance(t, dict) and str(t.get("status") or "").startswith("scene_session_setup"))]
            transcript["updated_at"] = now
            _write_json(transcript_path, transcript)
    _write_json(_scene_setup_path(scene_id), setup)
    try:
        setup["memory_link"] = upsert_scene_setup_memory(setup)
    except Exception as exc:
        setup["memory_link"] = {"status": "error", "error": str(exc)}
    return setup


def load_scene_transcript(scene_id: str = "default") -> dict[str, Any]:
    ensure_scene_storage()
    path = _scene_transcript_path(scene_id)
    transcript = _load_json(path, {})
    if not isinstance(transcript, dict) or not transcript:
        transcript = {
            "scene_id": scene_id,
            "status": "ready",
            "generation_enabled": False,
            "turns": [],
            "storage_path": _relative_to_root(path),
            "created_at": "",
            "updated_at": "",
        }
    transcript.setdefault("scene_id", scene_id)
    transcript.setdefault("turns", [])
    transcript.setdefault("storage_path", _relative_to_root(path))
    transcript["generation_enabled"] = False
    return transcript







def _persist_pending_stream_user_turn(scene_id: str, transcript: dict[str, Any], user_turn: dict[str, Any]) -> dict[str, Any]:
    # Persist the accepted user turn before a long streaming backend call.
    # Local backends can take a long time before first token; this prevents UI
    # refreshes from making the transcript look empty while generation runs.
    turns = transcript.setdefault("turns", [])
    turn_id = str(user_turn.get("turn_id") or "")
    if turn_id and not any(isinstance(t, dict) and str(t.get("turn_id") or "") == turn_id for t in turns):
        pending_turn = dict(user_turn)
        pending_turn["status"] = "submitted_stream_pending"
        turns.append(pending_turn)
    transcript["status"] = "streaming"
    transcript["generation_enabled"] = True
    transcript["updated_at"] = _now()
    _write_json(_scene_transcript_path(scene_id), transcript)
    return transcript


def _replace_pending_stream_user_turn(transcript: dict[str, Any], user_turn: dict[str, Any]) -> dict[str, Any]:
    turn_id = str(user_turn.get("turn_id") or "")
    replaced = False
    next_turns: list[dict[str, Any]] = []
    for turn in transcript.get("turns") or []:
        if isinstance(turn, dict) and turn_id and str(turn.get("turn_id") or "") == turn_id:
            next_turns.append(user_turn)
            replaced = True
        else:
            next_turns.append(turn)
    if not replaced:
        next_turns.append(user_turn)
    transcript["turns"] = next_turns
    return transcript


def _scene_has_played_turn(transcript: dict[str, Any]) -> bool:
    turns = transcript.get("turns") if isinstance(transcript, dict) else []
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        status = str(turn.get("status") or "").lower()
        if role == "assistant" and status in {"generated", "streamed", "saved", "live"}:
            return True
    return False


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
    return payload if isinstance(payload, dict) else {}


def _identity_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    identity = fields.get("identity") if isinstance(fields.get("identity"), dict) else {}
    roleplay_control = fields.get("roleplay_control") if isinstance(fields.get("roleplay_control"), dict) else {}
    return {"identity": identity, "roleplay_control": roleplay_control}


def _scene_character_roster_from_packet(packet: dict[str, Any] | None, runtime_bundle: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build a numbered Roleplay character roster from active packet/runtime data.

    This is schema-driven and generic. It never hardcodes lore or character names.
    """
    packet = packet if isinstance(packet, dict) else {}
    instructions = packet.get("model_instructions") if isinstance(packet.get("model_instructions"), dict) else {}
    player_ids = {str(x).strip() for x in _safe_list(instructions.get("player_character_ids")) if str(x).strip()}
    npc_ids = {str(x).strip() for x in _safe_list(instructions.get("npc_character_ids")) if str(x).strip()}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_record(source: dict[str, Any]) -> None:
        payload = _record_payload(source)
        kind = str(source.get("kind") or payload.get("kind") or "").strip().lower()
        if kind and kind != "character":
            return
        source_id = str(source.get("source_id") or source.get("record_id") or payload.get("id") or payload.get("record_id") or "").strip()
        title = str(source.get("title") or payload.get("display_label") or payload.get("label") or source_id or "").strip()
        if not title:
            return
        key = (source_id or title).casefold()
        if key in seen:
            return
        seen.add(key)
        identity_parts = _identity_from_payload(payload)
        identity = identity_parts["identity"]
        roleplay_control = identity_parts["roleplay_control"]
        pronouns = str(identity.get("pronouns") or source.get("pronouns") or "").strip()
        gender = str(identity.get("gender") or source.get("gender") or "").strip()
        descriptor = str(identity.get("public_identity_label") or identity.get("tagline") or source.get("summary") or source.get("content") or "").strip()
        control_hint = "scenario_default"
        if source_id in player_ids or roleplay_control.get("assistant_must_not_control") is True:
            default_control = "user"
        elif source_id in npc_ids or roleplay_control.get("assistant_may_control") is True:
            default_control = "neo"
        else:
            default_control = "neo"
            control_hint = str(roleplay_control.get("control_lane") or "scene_cast")
        rows.append({
            "number": len(rows) + 1,
            "character_id": source_id,
            "name": title,
            "pronouns": pronouns,
            "gender": gender,
            "descriptor": _compact_text(descriptor, 180),
            "default_control": default_control,
            "control_hint": control_hint,
        })

    for row in _safe_list(packet.get("character_context")):
        if isinstance(row, dict):
            add_record(row)
    if not rows and isinstance(runtime_bundle, dict):
        for row in _safe_list(runtime_bundle.get("included_entities")):
            if isinstance(row, dict):
                add_record(row)
    # If runtime rows were compact, hydrate character records from file-backed Forge entities.
    for row in list(rows):
        if row.get("pronouns") or not row.get("character_id"):
            continue
        entity_path = ROLEPLAY_DATA_ROOT / "entities" / "character" / f"{row['character_id']}.json"
        data = _load_json(entity_path, {})
        if isinstance(data, dict) and data:
            payload = _record_payload(data)
            identity_parts = _identity_from_payload(payload)
            identity = identity_parts["identity"]
            row["pronouns"] = str(identity.get("pronouns") or "").strip()
            row["gender"] = str(identity.get("gender") or "").strip()
            row["descriptor"] = _compact_text(str(identity.get("public_identity_label") or identity.get("tagline") or row.get("descriptor") or ""), 180)
    for idx, row in enumerate(rows, start=1):
        row["number"] = idx
    return rows


def _active_scene_packet_for_setup(setup: dict[str, Any], runtime_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    packet_id = str(setup.get("scene_packet_id") or "").strip()
    scene_id = str(setup.get("scene_id") or "default")
    scope_id = str(setup.get("memory_scope") or setup.get("runtime_bundle_id") or "").strip()
    packet = load_scene_packet_payload(scene_packet_id=packet_id, scene_id=scene_id, scope_id=scope_id, runtime_bundle_id=str(setup.get("runtime_bundle_id") or "")) or {}
    if _scene_character_roster_from_packet(packet):
        return packet
    if isinstance(runtime_bundle, dict):
        runtime_packet = runtime_bundle.get("scene_packet") if isinstance(runtime_bundle.get("scene_packet"), dict) else {}
        if runtime_packet:
            return runtime_packet
    return packet


def _session_setup_from_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    setup = transcript.get("session_setup") if isinstance(transcript, dict) and isinstance(transcript.get("session_setup"), dict) else {}
    return setup.copy() if isinstance(setup, dict) else {}


def _session_setup_status(setup: dict[str, Any], transcript: dict[str, Any], roster: list[dict[str, Any]], packet_id: str = "") -> dict[str, Any]:
    played = _scene_has_played_turn(transcript)
    session = _session_setup_from_transcript(transcript)
    session_packet_id = str(session.get("scene_packet_id") or "").strip()
    active_packet_id = str(packet_id or session_packet_id or "").strip()
    session_runtime_id = str(session.get("runtime_bundle_id") or "").strip()
    setup_runtime_id = str(setup.get("runtime_bundle_id") or "").strip() if isinstance(setup, dict) else ""
    same_runtime = bool(session_runtime_id and setup_runtime_id and session_runtime_id == setup_runtime_id)
    ready = str(session.get("status") or "") == "ready" and bool(session.get("user_controls"))
    # Scene Memory Injection may expose a transient human-memory packet id while
    # the session setup stores the authored/runtime packet id. That drift must
    # not invalidate an already-ready session if the selected runtime bundle is
    # unchanged; otherwise the first real RP turn is skipped before backend dispatch.
    if active_packet_id and session_packet_id and active_packet_id != session_packet_id and not played and not same_runtime:
        ready = False
        session = {}
    if ready and same_runtime and session_packet_id:
        active_packet_id = session_packet_id
    needs_setup = bool(roster) and not played and not ready
    stage = "ready" if ready else "select_characters"
    if session.get("pending_user_control_numbers") and not ready:
        stage = "select_mode"
    return {
        "schema_id": "neo.roleplay.scene.session_setup.v1",
        "status": "ready" if ready else ("needs_setup" if needs_setup else "not_required"),
        "stage": stage,
        "needs_setup": needs_setup,
        "played": played,
        "scene_packet_id": active_packet_id,
        "roster": roster,
        "roster_count": len(roster),
        "user_controls": session.get("user_controls") or [],
        "neo_controls": session.get("neo_controls") or [],
        "control_mode": session.get("control_mode") or "strict",
        "active_user_character_name": session.get("active_user_character_name") or ((session.get("user_controls") or [""])[0] if session.get("user_controls") else ""),
        "continuation_context": transcript.get("continuation_context") if isinstance(transcript.get("continuation_context"), dict) else {},
    }


def _parse_roster_numbers(text: str, roster: list[dict[str, Any]]) -> list[int]:
    raw = str(text or "").strip()
    if not raw:
        return []
    values: list[int] = []
    for part in re.split(r"\s*(?:,|/|&|and)\s*", raw, flags=re.IGNORECASE):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            num = int(part)
            if 1 <= num <= len(roster) and num not in values:
                values.append(num)
    return values


def _parse_control_mode(text: str) -> str:
    value = str(text or "").strip().lower()
    if value in {"1", "strict", "strict mode"}:
        return "strict"
    if value in {"2", "moderate", "moderate mode"}:
        return "moderate"
    return ""


def _session_setup_message(status: dict[str, Any]) -> str:
    roster = status.get("roster") or []
    if status.get("stage") == "select_mode":
        selected = ", ".join(status.get("user_controls") or status.get("pending_user_controls") or []) or "selected character/s"
        return (
            f"You are playing: {selected}\n\n"
            "Choose control mode:\n"
            "1. Strict Mode — Neo will not write dialogue, actions, thoughts, feelings, or decisions for your characters.\n"
            "2. Moderate Mode — Neo may lightly reference your characters' existing position/state, but will not make decisions or speak for them."
        )
    lines = ["Scene packet loaded.", "", "Available characters:"]
    for row in roster:
        bits = [f"{row.get('number')}. {row.get('name')}"]
        if row.get("pronouns"):
            bits.append(str(row.get("pronouns")))
        elif row.get("gender"):
            bits.append(str(row.get("gender")))
        if row.get("default_control") == "user":
            bits.append("scenario default: user")
        lines.append(" — ".join(bits))
    lines.extend(["", "Which character/s will you play? Type number/s, for example: 2 or 2,3."])
    return "\n".join(lines)


def update_scene_session_setup_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_scene_storage()
    scene_id = str((payload or {}).get("scene_id") or "default")
    setup = load_scene_setup(scene_id)
    transcript = load_scene_transcript(scene_id)
    runtime_bundle_id = str((payload or {}).get("runtime_bundle_id") or setup.get("runtime_bundle_id") or "")
    bundle = get_runtime_bundle(runtime_bundle_id) if runtime_bundle_id else None
    packet = _active_scene_packet_for_setup(setup, bundle)
    packet_id = str(packet.get("scene_packet_id") or packet.get("packet_id") or setup.get("scene_packet_id") or runtime_bundle_id or "")
    roster = _scene_character_roster_from_packet(packet, bundle)
    if not roster:
        return {"ok": False, "status": "no_scene_characters", "error": "No characters were found in the active Scene Packet.", "scene_id": scene_id}
    numbers = payload.get("user_control_numbers") or payload.get("character_numbers") or []
    if isinstance(numbers, str):
        selected_numbers = _parse_roster_numbers(numbers, roster)
    elif isinstance(numbers, list):
        selected_numbers = []
        for item in numbers:
            try:
                num = int(item)
            except Exception:
                continue
            if 1 <= num <= len(roster) and num not in selected_numbers:
                selected_numbers.append(num)
    else:
        selected_numbers = []
    if not selected_numbers:
        selected_numbers = [int(row.get("number") or 0) for row in roster if row.get("default_control") == "user"]
    selected_rows = [row for row in roster if int(row.get("number") or 0) in selected_numbers]
    if not selected_rows:
        return {"ok": False, "status": "missing_user_characters", "error": "Choose at least one character number to play.", "scene_id": scene_id, "session_setup": _session_setup_status(setup, transcript, roster, packet_id)}
    mode = str((payload or {}).get("control_mode") or "strict").strip().lower()
    if mode not in {"strict", "moderate"}:
        mode = "strict"
    user_names = [str(row.get("name") or "").strip() for row in selected_rows if str(row.get("name") or "").strip()]
    neo_names = [str(row.get("name") or "").strip() for row in roster if str(row.get("name") or "").strip() and str(row.get("name") or "").strip() not in user_names]
    now = _now()
    session_setup = {
        "schema_id": "neo.roleplay.scene.session_setup.v1",
        "status": "ready",
        "scene_packet_id": packet_id,
        "runtime_bundle_id": runtime_bundle_id,
        "user_controls": user_names,
        "neo_controls": neo_names,
        "control_mode": mode,
        "active_user_character_name": user_names[0] if user_names else "",
        "roster": roster,
        "created_at": transcript.get("session_setup", {}).get("created_at") if isinstance(transcript.get("session_setup"), dict) else now,
        "updated_at": now,
    }
    transcript["session_setup"] = session_setup
    if not transcript.get("created_at"):
        transcript["created_at"] = now
    transcript["updated_at"] = now
    transcript["status"] = "session_ready" if not _scene_has_played_turn(transcript) else "live"
    system_text = (
        "Scene ready.\n\n"
        f"You are playing: {', '.join(user_names)}\n"
        f"Neo controls: {', '.join(neo_names) or 'environment only'}\n"
        f"Control mode: {mode.title()}\n\n"
        "Start with your first scene beat."
    )
    turns = [t for t in transcript.get("turns") or [] if not (isinstance(t, dict) and str(t.get("status") or "") in {"scene_session_setup_prompt", "scene_session_setup_ready"})]
    turns.append({
        "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        "role": "system",
        "display_role": "Scene Setup",
        "text": system_text,
        "created_at": now,
        "status": "scene_session_setup_ready",
        "runtime_bundle_id": runtime_bundle_id,
        "scene_packet_id": packet_id,
    })
    transcript["turns"] = turns
    _write_json(_scene_transcript_path(scene_id), transcript)
    return {"ok": True, "status": "scene_session_ready", "scene_id": scene_id, "assistant_turn": turns[-1] if turns else {}, "session_setup": _session_setup_status(setup, transcript, roster, packet_id), "transcript": transcript}


def _session_control_directives(transcript: dict[str, Any]) -> dict[str, Any]:
    session = _session_setup_from_transcript(transcript)
    if str(session.get("status") or "") != "ready":
        return {"assistant_controls": [], "user_controls": [], "raw": ""}
    return {"assistant_controls": session.get("neo_controls") or [], "user_controls": session.get("user_controls") or [], "control_mode": session.get("control_mode") or "strict", "raw": "session_setup"}


def _active_user_display_name(transcript: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    explicit = str(payload.get("active_user_character_name") or payload.get("user_character_name") or "").strip()
    session = _session_setup_from_transcript(transcript)
    allowed = [str(x).strip() for x in session.get("user_controls") or [] if str(x).strip()]
    if explicit and (not allowed or explicit in allowed):
        return explicit
    return allowed[0] if allowed else "User"


def _assistant_display_name(transcript: dict[str, Any], directives: dict[str, Any] | None = None) -> str:
    controls = _scene_controlured_names(directives, "assistant_controls")
    if controls:
        return controls[0]
    session = _session_setup_from_transcript(transcript)
    neo = [str(x).strip() for x in session.get("neo_controls") or [] if str(x).strip()]
    return neo[0] if neo else "Neo"


def _scene_context_window_from_bridge(bridge: dict[str, Any]) -> int:
    profile = bridge.get("active_profile") if isinstance(bridge, dict) else {}
    candidates: list[Any] = []
    if isinstance(profile, dict):
        candidates.extend([
            profile.get("context_window_tokens"), profile.get("context_tokens"), profile.get("context_size"), profile.get("n_ctx"),
        ])
        raw = profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}
        candidates.extend([raw.get("context_window_tokens"), raw.get("context_size"), raw.get("n_ctx")])
        runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
        candidates.extend([runtime.get("context_window_tokens"), runtime.get("context_size"), runtime.get("n_ctx")])
    for item in candidates:
        try:
            value = int(item)
        except Exception:
            continue
        if value > 0:
            return value
    return 8192


def _estimate_scene_context_status(setup: dict[str, Any], transcript: dict[str, Any], bridge: dict[str, Any], injection_state: dict[str, Any] | None = None) -> dict[str, Any]:
    turns = transcript.get("turns") if isinstance(transcript.get("turns"), list) else []
    text = "\n".join(str(t.get("text") or "") for t in turns if isinstance(t, dict))
    setup_text = "\n".join(str(setup.get(k) or "") for k in ("title", "premise", "scene_notes", "scene_rules"))
    preview = "\n".join(str(x) for x in ((injection_state or {}).get("preview_lines") or [])[:20])
    continuation = transcript.get("continuation_context") if isinstance(transcript.get("continuation_context"), dict) else {}
    continuation_text = str(continuation.get("summary") or "")
    used_chars = len(text) + len(setup_text) + len(preview) + len(continuation_text)
    estimated_tokens = max(1, int(used_chars / 4))
    context_window = _scene_context_window_from_bridge(bridge)
    pct = min(1.0, estimated_tokens / max(1, context_window))
    return {
        "schema_id": "neo.roleplay.scene.context_budget.v1",
        "context_window_tokens": context_window,
        "estimated_tokens": estimated_tokens,
        "used_chars": used_chars,
        "percent": round(pct * 100, 1),
        "status": "near_limit" if pct >= 0.8 else ("watch" if pct >= 0.6 else "ok"),
        "needs_continuation_session": pct >= 0.8,
        "turn_count": len(turns),
    }


def _transcript_continuation_summary(turns: list[dict[str, Any]], limit: int = 18) -> str:
    rows: list[str] = []
    for turn in turns[-limit:]:
        if not isinstance(turn, dict):
            continue
        status = str(turn.get("status") or "")
        if status.startswith("scene_session_setup") or status in {"scene_turn_blocked", "stream_error", "generation_error"}:
            continue
        label = str(turn.get("display_role") or turn.get("role") or "turn").strip()
        text = _compact_text(turn.get("text") or "", 360)
        if text:
            rows.append(f"{label}: {text}")
    return "\n".join(rows)[-3600:]


def start_scene_continuation_session_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_scene_storage()
    payload = payload or {}
    scene_id = str(payload.get("scene_id") or "default")
    transcript = load_scene_transcript(scene_id)
    if not _scene_has_played_turn(transcript):
        return {"ok": False, "status": "scene_not_started", "error": "Start the scene before creating a continuation session.", "scene_id": scene_id}
    now = _now()
    archive_dir = _scene_dir() / "continuations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{_slug(scene_id)}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
    _write_json(archive_path, transcript)
    summary = _transcript_continuation_summary(transcript.get("turns") or [])
    setup = transcript.get("session_setup") if isinstance(transcript.get("session_setup"), dict) else {}
    continuation_context = {
        "status": "active",
        "summary": summary,
        "source_turn_count": len(transcript.get("turns") or []),
        "source_archive_path": _relative_to_root(archive_path),
        "created_at": now,
    }
    new_transcript = {
        "scene_id": scene_id,
        "status": "continuation_ready",
        "generation_enabled": True,
        "turns": [{
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            "role": "system",
            "display_role": "Scene Continuation",
            "text": "Continuation session started from the previous transcript summary.",
            "created_at": now,
            "status": "scene_continuation_started",
        }],
        "session_setup": setup,
        "continuation_context": continuation_context,
        "storage_path": _relative_to_root(_scene_transcript_path(scene_id)),
        "created_at": now,
        "updated_at": now,
    }
    _write_json(_scene_transcript_path(scene_id), new_transcript)
    return {"ok": True, "status": "continuation_session_started", "scene_id": scene_id, "transcript": new_transcript, "archive_path": _relative_to_root(archive_path), "continuation_context": continuation_context}


def _compact_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _runtime_context_lines(bundle: dict[str, Any] | None) -> list[str]:
    if not isinstance(bundle, dict) or not bundle:
        return ["No runtime bundle is selected. Stay anchored to the Scene setup only."]
    lines: list[str] = []
    lines.append(f"Runtime bundle: {bundle.get('title') or bundle.get('bundle_id') or 'Untitled runtime bundle'}")
    scene_packet = bundle.get("scene_packet") if isinstance(bundle.get("scene_packet"), dict) else {}
    if scene_packet.get("summary"):
        lines.append(f"Scene packet summary: {_compact_text(scene_packet.get('summary'), 900)}")
    counts = bundle.get("counts") if isinstance(bundle.get("counts"), dict) else {}
    if counts:
        lines.append("Bundle counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for label, key in (("Entity", "included_entities"), ("Source", "included_sources"), ("Storyline", "included_storylines")):
        items = bundle.get(key) if isinstance(bundle.get(key), list) else []
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("label") or item.get("record_id") or item.get("source_id") or item.get("storyline_id") or "Untitled"
            summary = item.get("content") or item.get("body") or item.get("summary") or item.get("premise") or ""
            lines.append(f"{label}: {title} — {_compact_text(summary, 700)}")
    return lines[:28]


def _memory_context_lines(retrieval: dict[str, Any]) -> list[str]:
    search = retrieval.get("search") if isinstance(retrieval, dict) else {}
    results = search.get("results") if isinstance(search, dict) else []
    lines: list[str] = []
    for result in (results or [])[:10]:
        if not isinstance(result, dict):
            continue
        table = result.get("table") or "memory"
        title = result.get("title") or result.get("result_id") or "Untitled"
        content = result.get("content") or ""
        lines.append(f"[{table}] {title}: {_compact_text(content, 650)}")
    return lines





def _char_budget_lines(lines: list[str], *, max_chars: int = 14000) -> tuple[list[str], dict[str, Any]]:
    """Keep local text-backend prompts inside a safe request size.

    Scene packets can contain hundreds of scoped fragments. KoboldCpp/local
    servers may drop oversized streaming requests, which appears in the UI as a
    generic network error. This trims from the end while preserving the highest
    priority sections placed first in build_scene_turn_prompt_payload().
    """
    try:
        safe_max = max(6000, min(32000, int(max_chars or 14000)))
    except Exception:
        safe_max = 14000
    out: list[str] = []
    used = 0
    omitted = 0
    for line in lines:
        text = str(line or "")
        cost = len(text) + 1
        if used + cost > safe_max:
            omitted += 1
            continue
        out.append(text)
        used += cost
    if omitted:
        out.append(f"[Context budget note: {omitted} lower-priority context lines were omitted to keep the local backend request stable.]")
    return out, {"max_chars": safe_max, "used_chars": used, "omitted_lines": omitted, "input_lines": len(lines), "output_lines": len(out)}


def _memory_engine_context_lines(query: str, *, limit: int = 6) -> tuple[list[str], dict[str, Any]]:
    """Retrieve central Memory Engine context for Roleplay runtime.

    Roleplay keeps its specialized SQLite state, but the central Memory Engine is
    the cross-surface retrieval layer. This helper is intentionally optional: if
    the roleplay_memory source has not been indexed yet, Scene generation still
    works from local Roleplay memory.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return [], {"status": "skipped", "reason": "empty_query"}
    try:
        from neo_app.memory.service import get_memory_service
        result = get_memory_service().retrieve({
            "query": clean_query,
            "profile": "roleplay_runtime",
            "consumer": "roleplay.scene",
            "sources": ["roleplay_memory", "system_records"],
            "limit": limit,
        })
    except Exception as exc:
        return [], {"status": "error", "error": str(exc)}
    lines: list[str] = []
    for item in (result.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("source_path") or "Memory Engine result"
        source = item.get("source_path") or item.get("source_id") or "memory"
        content = item.get("snippet") or item.get("summary") or item.get("content") or ""
        lines.append(f"[{source}] {title}: {_compact_text(content, 520)}")
    return lines, {k: v for k, v in result.items() if k not in {"results", "profile_config"}}




def _turn_focus_name(text: str, payload: dict[str, Any] | None = None) -> str:
    """Infer an explicit single-character performance focus from the user turn.

    This catches instructions like "continue as {Character Name} only" so the backend does
    not start puppeting every character in the scene. It is intentionally narrow:
    it strengthens control boundaries only when the user asks for a named lane.
    """
    payload = payload or {}
    explicit = str(payload.get("assistant_character_name") or payload.get("assistant_focus_character") or payload.get("npc_focus_character") or "").strip()
    if explicit:
        return explicit[:80]
    m = re.search(r"\b(?:continue|respond|write|reply|act)\s+as\s+([A-Z][A-Za-z0-9 _'-]{1,50}?)(?:\s+only|[.!?]|$)", str(text or ""), re.IGNORECASE)
    if not m:
        return ""
    name = re.sub(r"\s+", " ", m.group(1)).strip(" .,!?:;\"'")
    return name[:80]


def _split_control_names(raw: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(raw or "")).strip(" .,!?:;\"'")
    if not text:
        return []
    parts = [p.strip(" .,!?:;\"'") for p in re.split(r"\s*(?:,|\band\b|&|/)\s*", text) if p.strip()]
    names: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\b(?:only|and others|others|environment|npc|npcs)\b", "", part, flags=re.IGNORECASE).strip(" .,!?:;\"'")
        if cleaned and cleaned.lower() not in {"none", "no one"} and cleaned not in names:
            names.append(cleaned[:80])
    return names


def _scene_control_directives_from_text(text: str) -> dict[str, Any]:
    """Parse explicit scene-control directives from user/setup text.

    This is intentionally conservative. It only reads direct control statements
    such as "Assistant controls {Character Name} only" and "User controls {Character A} and
    {Character B}" so those rules can override authored default packets for the
    current scene without changing canon records.
    """
    value = str(text or "")
    directives: dict[str, Any] = {"assistant_controls": [], "user_controls": [], "raw": ""}
    assistant_match = re.search(
        r"\b(?:assistant|model|ai)\s+controls?\s+(.+?)(?:\s+only)?(?:\.|\n|$)",
        value,
        flags=re.IGNORECASE,
    )
    user_match = re.search(
        r"\b(?:user|player|human)\s+controls?\s+(.+?)(?:\.|\n|$)",
        value,
        flags=re.IGNORECASE,
    )
    if assistant_match:
        directives["assistant_controls"] = _split_control_names(assistant_match.group(1))
    if user_match:
        directives["user_controls"] = _split_control_names(user_match.group(1))
    if directives["assistant_controls"] or directives["user_controls"]:
        directives["raw"] = value.strip()
    return directives


def _merge_scene_control_directives(*items: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {"assistant_controls": [], "user_controls": [], "raw": ""}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("assistant_controls", "user_controls"):
            raw = item.get(key) or []
            if isinstance(raw, str):
                values = _split_control_names(raw)
            elif isinstance(raw, list):
                values = [str(x).strip() for x in raw if str(x).strip()]
            else:
                values = []
            for value in values:
                if value and value not in merged[key]:
                    merged[key].append(value[:80])
        if item.get("raw"):
            merged["raw"] = str(item.get("raw") or "")[:500]
    return merged


def _is_scene_control_directive_only(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    parsed = _scene_control_directives_from_text(value)
    if not (parsed.get("assistant_controls") or parsed.get("user_controls")):
        return False
    cleaned = re.sub(r"\b(?:assistant|model|ai)\s+controls?\s+.+?(?:\.|\n|$)", " ", value, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b(?:user|player|human)\s+controls?\s+.+?(?:\.|\n|$)", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\bdo\s+not\s+write\s+.+?(?:\.|\n|$)", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;\"'")
    return not cleaned


def _scene_control_prompt_lines(directives: dict[str, Any]) -> list[str]:
    assistant_controls = [str(x).strip() for x in directives.get("assistant_controls") or [] if str(x).strip()]
    user_controls = [str(x).strip() for x in directives.get("user_controls") or [] if str(x).strip()]
    if not assistant_controls and not user_controls:
        return []
    lines = [
        "Current scene control override:",
        "- This override is higher priority than scenario defaults for the current chat session.",
    ]
    if assistant_controls:
        joined = ", ".join(assistant_controls)
        lines.append(f"- Assistant/model controls ONLY: {joined}.")
        lines.append(f"- The reply may include dialogue/actions for {joined} only, plus neutral environment narration.")
    if user_controls:
        joined = ", ".join(user_controls)
        lines.append(f"- User/player controls: {joined}.")
        lines.append(f"- Never write dialogue, thoughts, feelings, decisions, facial expressions, body movement, or physical actions for {joined}.")
    lines.extend([
        "- Do not output meta templates, XML, markdown report sections, or labels such as Response, Character Name, Scene description, Dialogue, Additional notes, or Character control rules.",
        "- Output only an in-scene reply using: Narration: then the assistant-controlled character name as a dialogue label when they speak.",
    ])
    return lines


def _control_update_ack_text(directives: dict[str, Any]) -> str:
    assistant_controls = ", ".join(directives.get("assistant_controls") or []) or "not specified"
    user_controls = ", ".join(directives.get("user_controls") or []) or "not specified"
    return (
        "Scene control updated.\n\n"
        f"Assistant controls: {assistant_controls}\n"
        f"User controls: {user_controls}\n\n"
        "Send the next scene beat when ready."
    )




def _scene_controlured_names(directives: dict[str, Any] | None, key: str) -> list[str]:
    if not isinstance(directives, dict):
        return []
    return [str(x).strip() for x in directives.get(key) or [] if str(x).strip()]


def _unwrap_full_quoted_narrative(text: str) -> tuple[str, bool]:
    """Remove accidental full-response quotes around narrative prose.

    Some local backends wrap the entire scene beat in quotation marks. That
    causes quote-protection to treat narration as dialogue and lets user-owned
    character descriptions slip through. This helper is generic and only unwraps
    when the quoted body looks like multi-sentence prose, not a short spoken
    line.
    """
    value = str(text or "").strip()
    if len(value) < 2:
        return value, False
    quote_pairs = (("\"", "\""), ("“", "”"), ("'", "'"))
    for left, right in quote_pairs:
        if value.startswith(left) and value.endswith(right):
            inner = value[len(left):-len(right)].strip()
            sentence_count = len(re.findall(r"[.!?](?:\s+|$)", inner))
            has_narrative_punctuation = bool(re.search(r"\b(?:Narration|Scene|Description)\s*:", inner, flags=re.IGNORECASE))
            if sentence_count >= 2 or has_narrative_punctuation or len(inner) > 220:
                return inner, True
    return value, False


META_LEAK_SECTION_HEADERS = (
    "Character control",
    "Character controls",
    "Character control rules",
    "Scene Packet Summary",
    "Scene Summary",
    "Scene State",
    "Character States",
    "Next Scene Beat",
    "Next Beat",
    "Response",
    "Brief immersive situation/environment beat",
    "Character Name",
    "Scene description",
    "Dialogue",
    "Additional notes",
    "Assistant turn",
    "Assistant reply",
    "Assistant response",
    "User turn",
    "User input",
    "Scene input",
    "Model response",
    "Summary",
    "Continuity Summary",
    "Scene",
)


def _meta_leak_header_pattern() -> str:
    escaped = "|".join(re.escape(x) for x in META_LEAK_SECTION_HEADERS)
    return rf"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?(?:{escaped})(?:\*\*)?\s*:"


def _strip_meta_leak_sections(text: str) -> tuple[str, bool]:
    """Remove leaked prompt/report sections from backend scene output.

    This is non-generative and generic. It keeps narrative text before the first
    leaked section header and drops the instruction/packet/debug tail.
    """
    value = str(text or "")
    if not value.strip():
        return "", False
    pattern = _meta_leak_header_pattern()
    match = re.search(pattern, value, flags=re.IGNORECASE)
    if not match:
        return value.strip(), False
    return value[: match.start()].strip(), True


UNSUPPORTED_ESCALATION_PATTERNS = (
    r"\bcan't\s+go\s+back\b",
    r"\bcannot\s+go\s+back\b",
    r"\blet\s+me\s+go\b",
    r"\bwe\s+(?:are|were)\s+over\b",
    r"\bthis\s+is\s+over\b",
    r"\bcheat(?:ed|ing)?\b",
    r"\bbetray(?:ed|al|ing)?\b",
    r"\bpregnan(?:t|cy)\b",
    r"\bassault(?:ed|ing)?\b",
    r"\bkilled?\b",
    r"\bdied?\b",
)


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r'"[^"\n]*"', str(text or ""))]


def _inside_any_span(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _repair_context_allows(pattern: str, grounding_text: str, user_message: str) -> bool:
    haystack = f"{user_message}\n{grounding_text}".lower()
    try:
        return bool(re.search(pattern, haystack, flags=re.IGNORECASE))
    except re.error:
        return False


def _has_unsupported_scene_escalation(text: str, grounding_text: str = "", user_message: str = "") -> bool:
    """Return True when the backend invents a major dramatic turn.

    The patterns are generic risk categories, not lore. They are only blocked
    when the same kind of claim is absent from the user turn and confirmed
    packet/context text supplied to the repair layer.
    """
    value = str(text or "")
    for pattern in UNSUPPORTED_ESCALATION_PATTERNS:
        if re.search(pattern, value, flags=re.IGNORECASE) and not _repair_context_allows(pattern, grounding_text, user_message):
            return True
    return False


def _strip_unsupported_escalation_sentences(text: str, grounding_text: str = "", user_message: str = "") -> tuple[str, bool]:
    """Remove unsupported escalation sentences without inserting canned RP text.

    This repair is deliberately non-generative. If every sentence is unsafe, the
    caller should ask the backend for a fresh turn instead of showing a
    hardcoded fallback line.
    """
    value = str(text or "").strip()
    if not value:
        return "", False
    parts = re.split(r"(?<=[.!?])\s+", value)
    kept: list[str] = []
    removed = False
    for part in parts:
        sentence = part.strip()
        if not sentence:
            continue
        if _has_unsupported_scene_escalation(sentence, grounding_text=grounding_text, user_message=user_message):
            removed = True
            continue
        kept.append(sentence)
    return " ".join(kept).strip(), removed


def _scene_retry_messages(prompt: dict[str, Any], reason: str = "") -> list[dict[str, str]]:
    """Build a one-shot retry prompt using generic guardrail instructions only."""
    messages = [dict(m) for m in (prompt.get("messages") or []) if isinstance(m, dict)]
    retry_note = (
        "Retry the last assistant turn. The prior draft was rejected by scene guardrails. "
        "Write fresh prose/dialogue for the assistant-controlled character only. "
        "Ground the beat in the active packet/setup and the latest user turn. "
        "Do not add unsupported major scene changes. Do not write dialogue, thoughts, feelings, decisions, or actions for user-controlled characters. "
        "Do not use canned fallback wording, templates, labels, XML, or report sections."
    )
    if messages and str(messages[0].get("role") or "") == "system":
        messages[0]["content"] = f"{messages[0].get('content') or ''}\n\nScene retry instruction:\n{retry_note}".strip()
    else:
        messages.insert(0, {"role": "system", "content": f"Scene retry instruction:\n{retry_note}"})
    return messages


def _needs_scene_generation_retry(warnings: list[dict[str, Any]]) -> bool:
    return any(str(w.get("code") or "") == "requires_backend_regeneration" for w in warnings or [])


SCENE_TURN_BLOCKED_TEXT = "[Scene turn blocked by guardrails. Regenerate the turn or adjust the scene/control setup.]"


def _is_guardrail_block_text(text: str) -> bool:
    value = str(text or "").strip().lower()
    return value.startswith("[scene turn blocked by guardrails")


def _has_scene_status_leak(text: str) -> bool:
    """Detect backend output that mixed internal recovery/status text into RP.

    This is generic and non-generative. It blocks/debug-cleans status leaks
    without replacing them with authored scene prose.
    """
    value = str(text or "")
    if not value.strip():
        return False
    status_patterns = (
        r"\[\s*Response\s+not\s+generated[^\]]*\]",
        r"\[\s*Send\s+the\s+next\s+scene\s+beat\s+when\s+ready\.?\s*\]",
        r"\[\s*Scene\s+turn\s+blocked\s+by\s+guardrails[^\]]*\]",
        r"\[\s*Continue\s+the\s+scene\s+from\s+the\s+current\s+transcript\s+state[^\]]*\]",
        r"current\s+context\s+due\s+to\s+the\s+user\s+error",
        r"\b(?:response|reply|turn)\s+is\s+blocked\s+by\s+(?:a\s+)?(?:sudden\s+)?guardrail\b",
        r"\bblocked\s+by\s+(?:a\s+)?(?:sudden\s+)?guardrail\b",
        r"(?:^|\n|\s)continue\s+as\s+[A-Z][A-Za-z0-9 _'-]{1,80}\s+only\s*[.!?:]?(?:\s|$)",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:Assistant|Model|AI|User|Player|Human|Scene)\s+(?:turn|reply|response|input)(?:\*\*)?\s*:",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:Next\s+Beat|Next\s+Scene\s+Beat)(?:\*\*)?\s*:",
        r"\[\s*content\s+redacted[^\]]*\]",
        r"\bcontent\s+redacted\b",
        r"\bthe\s+scene\s+referenc(?:es|ed)\s+\w+\b",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?Summary(?:\*\*)?\s*$",
        r"\bRoughly,\s*my\s+turn\s*:",
        r"\bI\s+await\s+(?:the\s+)?(?:reply|response)\s+from\b",
        r"\bNeo\s+Studio\s+Roleplay\s+Scene\s+Engine\b",
        r"(?:^|\n)\s*[—-]\s*Neo\s+Studio\b",
        r"\b(?:the\s+)?conversation\s+ended\s+abruptly\b",
        r"\bNext\s+scene\s*:",
        r"\b(?:the\s+)?next\s+scene\s+will\b",
        r"\bHope\s+this\s+helps\b",
        r"\bI['’]?ll\s+do\s+my\s+best\b",
        r"\bI\s+will\s+do\s+my\s+best\b",
        r"\bcaptur(?:e|ing)\s+[A-Z][A-Za-z0-9 _'-]{1,80}\s+voice\s+accurately\b",
        r"\bcaptur(?:e|ing)\s+the\s+(?:character|speaker)['’]?s\s+voice\b",
        r"\bglad\s+to\s+help\s+with\s+(?:the\s+)?(?:roleplay|scene)\b",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in status_patterns)





def _is_polluted_scene_memory_text(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    if _is_guardrail_block_text(value) or _has_scene_status_leak(value):
        return True
    patterns = (
        r"\[\s*End\s+Scene\s*\]",
        r"\bthe\s+scene\s+ended\b",
        r"\banother\s+session\b",
        r"(?:^|\n)\s*(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}(?:['’]s)?\s+Response\s*(?:\*\*)?:",
        r"(?:^|\n)\s*(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}\s+Response\s*\(from\s+the\s+user\)\s*(?:\*\*)?:",
        r"\bfrom\s+his\s+computer\s+screen\b",
        r"\bdata\s+upload\b",
        r"\bbe\s+a\s+bit\s+more\s+patient\s+please\b",
        r"\bRoughly,\s*my\s+turn\s*:",
        r"\bI\s+await\s+(?:the\s+)?(?:reply|response)\s+from\b",
        r"\bNeo\s+Studio\s+Roleplay\s+Scene\s+Engine\b",
        r"(?:^|\n)\s*[—-]\s*Neo\s+Studio\b",
        r"\b(?:the\s+)?conversation\s+ended\s+abruptly\b",
        r"\bNext\s+scene\s*:",
        r"\b(?:the\s+)?next\s+scene\s+will\b",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def _authoritative_scene_id_from_packet(packet: dict[str, Any] | None) -> str:
    if not isinstance(packet, dict) or not packet:
        return ""
    scene_id = str(packet.get("scene_id") or packet.get("scenario_id") or "").strip()
    if scene_id and scene_id.lower() != "default" and not scene_id.lower().startswith("human_scene:"):
        return scene_id
    scenario_rows = packet.get("scenario_context") if isinstance(packet.get("scenario_context"), list) else []
    for row in scenario_rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or row.get("id") or "").strip()
        if source_id:
            return source_id
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        payload_id = str(payload.get("id") or "").strip()
        if payload_id:
            return payload_id
    return ""


def _authoritative_scene_title_from_packet(packet: dict[str, Any] | None) -> str:
    if not isinstance(packet, dict) or not packet:
        return ""
    scenario_rows = packet.get("scenario_context") if isinstance(packet.get("scenario_context"), list) else []
    for row in scenario_rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if title:
            return title
    return str(packet.get("title") or "").strip()


def _authoritative_scene_premise_from_packet(packet: dict[str, Any] | None) -> str:
    if not isinstance(packet, dict) or not packet:
        return ""
    scenario_rows = packet.get("scenario_context") if isinstance(packet.get("scenario_context"), list) else []
    for row in scenario_rows:
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or row.get("summary") or "")
        # The scene packet builder stores flattened scenario fields. Keep the
        # first useful chunk as the canonical premise anchor.
        if content.strip():
            return _compact_text(content, 1200)
    return _compact_text(packet.get("summary") or "", 1200)

def _packet_character_id_name_map(packet: dict[str, Any] | None, runtime_bundle: dict[str, Any] | None = None) -> dict[str, str]:
    """Map character ids to display names from the active packet/runtime."""
    mapping: dict[str, str] = {}
    for row in _scene_character_roster_from_packet(packet if isinstance(packet, dict) else {}, runtime_bundle):
        cid = str(row.get("character_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if cid and name:
            mapping[cid] = name
    # Scene packets may only carry ids in focus/opening fields. Hydrate names
    # from Forge entity files when compact packet rows do not include them.
    packet_fields = (packet or {}).get("fields") if isinstance((packet or {}).get("fields"), dict) else {}
    candidate_ids: set[str] = set()
    for section_name, key in (("cast_roles_pov", "focus_character_ids"), ("confirmed_opening_state", "present_at_opening"), ("control_center_contract", "assistant_controlled_character_ids")):
        section = packet_fields.get(section_name) if isinstance(packet_fields.get(section_name), dict) else {}
        candidate_ids.update(str(x).strip() for x in _safe_list(section.get(key)) if str(x).strip())
    for cid in candidate_ids:
        if cid in mapping:
            continue
        entity_path = ROLEPLAY_DATA_ROOT / "entities" / "character" / f"{cid}.json"
        data = _load_json(entity_path, {})
        payload = _record_payload(data)
        name = str(payload.get("display_label") or payload.get("label") or "").strip()
        if name:
            mapping[cid] = name
    return mapping


def _ordered_packet_character_names(packet: dict[str, Any] | None, runtime_bundle: dict[str, Any] | None = None) -> list[str]:
    """Return scenario-preferred character order using generic packet fields."""
    packet = packet if isinstance(packet, dict) else {}
    id_to_name = _packet_character_id_name_map(packet, runtime_bundle)
    fields = packet.get("fields") if isinstance(packet.get("fields"), dict) else {}
    candidates: list[str] = []
    paths = [
        ("cast_roles_pov", "focus_character_ids"),
        ("confirmed_opening_state", "present_at_opening"),
        ("control_center_contract", "assistant_controlled_character_ids"),
    ]
    for section_name, key in paths:
        section = fields.get(section_name) if isinstance(fields.get(section_name), dict) else {}
        for raw in _safe_list(section.get(key)):
            raw_text = str(raw or "").strip()
            name = id_to_name.get(raw_text) or raw_text
            if name and name not in candidates:
                candidates.append(name)
    # Fall back to roster order.
    for row in _scene_character_roster_from_packet(packet, runtime_bundle):
        name = str(row.get("name") or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    return candidates


def _prioritize_assistant_controls_for_packet(directives: dict[str, Any] | None, packet: dict[str, Any] | None, runtime_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reorder Neo-controlled characters using scenario focus/opening order.

    This is generic: it uses packet ids/order, never story-specific names. It
    prevents a secondary NPC that happens to appear first in runtime rows from
    becoming the default reply lane when the scene focus indicates another Neo
    character should answer first.
    """
    data = dict(directives or {})
    assistant_controls = _scene_controlured_names(data, "assistant_controls")
    if len(assistant_controls) <= 1:
        return data
    preferred = _ordered_packet_character_names(packet, runtime_bundle)
    preferred_lc = [p.casefold() for p in preferred]
    ordered: list[str] = []
    assistant_by_lc = {name.casefold(): name for name in assistant_controls}
    for preferred_name in preferred:
        matched = assistant_by_lc.get(preferred_name.casefold())
        if matched and matched not in ordered:
            ordered.append(matched)
    for name in assistant_controls:
        if name not in ordered:
            ordered.append(name)
    if ordered != assistant_controls:
        data["assistant_controls"] = ordered
        data["assistant_primary"] = ordered[0]
    elif ordered:
        data.setdefault("assistant_primary", ordered[0])
    return data




def _control_name_aliases(names: list[str]) -> dict[str, str]:
    """Return alias -> canonical display name for active scene controls."""
    aliases: dict[str, str] = {}
    for name in names or []:
        clean = str(name or "").strip()
        if not clean:
            continue
        aliases[clean.casefold()] = clean
        first = clean.split()[0].strip()
        if first and len(first) >= 3:
            aliases.setdefault(first.casefold(), clean)
    return aliases


def _addressed_assistant_control(user_message: str, assistant_controls: list[str]) -> str:
    """Pick the Neo-controlled character directly addressed by the latest user turn."""
    text = str(user_message or "")
    if not text.strip() or not assistant_controls:
        return ""
    aliases = _control_name_aliases(assistant_controls)
    for alias, canonical in sorted(aliases.items(), key=lambda row: (-len(row[0]), row[0])):
        if re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE):
            return canonical
    return ""


def _prioritize_assistant_controls_for_turn(directives: dict[str, Any] | None, user_message: str = "") -> dict[str, Any]:
    """Let explicit user address override generic packet speaker order."""
    data = dict(directives or {})
    assistant_controls = _scene_controlured_names(data, "assistant_controls")
    addressed = _addressed_assistant_control(user_message, assistant_controls)
    if not addressed:
        if assistant_controls:
            data.setdefault("assistant_primary", assistant_controls[0])
        return data
    ordered = [addressed] + [name for name in assistant_controls if name.casefold() != addressed.casefold()]
    data["assistant_controls"] = ordered
    data["assistant_primary"] = addressed
    data["assistant_primary_source"] = "latest_user_address"
    return data

def _assistant_speaker_from_text(text: str, directives: dict[str, Any] | None) -> str:
    """Detect the displayed speaker from a clean assistant-controlled lane."""
    value = str(text or "").strip()
    if not value:
        return ""
    for name in _scene_controlured_names(directives, "assistant_controls"):
        pattern = re.compile(rf"^\s*(?:\*\*)?{re.escape(name)}(?:\s*(?:\([^)]*\)|['’]s\s+Response|\s+Response))?\s*(?:\*\*)?\s*:", re.IGNORECASE)
        if pattern.search(value):
            return name
    return ""


def _strip_leading_assistant_speaker_label(text: str, speaker: str) -> tuple[str, bool]:
    """Remove a duplicate leading speaker label when the UI already displays it."""
    value = str(text or "").strip()
    name = str(speaker or "").strip()
    if not value or not name:
        return value, False
    pattern = re.compile(rf"^\s*(?:\*\*)?{re.escape(name)}(?:\s*(?:\([^)]*\)|['’]s\s+Response|\s+Response))?\s*(?:\*\*)?\s*:\s*", re.IGNORECASE)
    new_value, count = pattern.subn("", value, count=1)
    return new_value.strip(), bool(count)

def _scene_stop_sequences(directives: dict[str, Any] | None) -> list[str]:
    """Return generic backend stop strings for roleplay boundaries.

    Inspired by RP frontends such as SillyTavern, this keeps the backend from
    opening new speaker lanes or leaking prompt-section headers. It is built
    from active control names, never from story lore.
    """
    stops: list[str] = ["Continue as ", "\nAssistant turn:", "\nAssistant reply:", "\nAssistant response:", "\nScene input:", "\n### Next Beat:", "\nNext Beat:", "[Content redacted"]
    user_controls = _scene_controlured_names(directives, "user_controls")
    assistant_controls = _scene_controlured_names(directives, "assistant_controls")
    primary = str((directives or {}).get("assistant_primary") or "").strip() or (assistant_controls[0] if assistant_controls else "")
    for name in user_controls:
        for label in {name, name.split()[0].strip() if name.split() else ""}:
            if not label:
                continue
            stops.extend([f"\n{label}:", f"\n**{label}:**", f"\n**{label}**:"])
    for name in assistant_controls:
        # Stop if the backend tries to open a different Neo-controlled speaker
        # lane after the selected response lane. The UI can still choose another
        # speaker on a later turn; one generation must stay single-lane.
        if primary and name.casefold() == primary.casefold():
            continue
        for label in {name, name.split()[0].strip() if name.split() else ""}:
            if not label:
                continue
            stops.extend([f"\n{label}:", f"\n**{label}:**", f"\n**{label}**:"])
    for header in META_LEAK_SECTION_HEADERS:
        stops.extend([f"\n{header}:", f"\n**{header}:**", f"\n**{header}**:"])
    stops.extend(["</response>", "<response>", "\nUser:", "\nHuman:", "\nPlayer:"])
    clean: list[str] = []
    for stop in stops:
        if stop and stop not in clean:
            clean.append(stop)
    return clean[:48]


def _collapse_repeated_scene_blocks(text: str) -> tuple[str, bool]:
    """Collapse exact repeated backend blocks without generating replacement text."""
    value = str(text or "").strip()
    if not value:
        return "", False
    blocks = [b.strip() for b in re.split(r"\n{2,}", value) if b.strip()]
    if len(blocks) > 1:
        kept: list[str] = []
        removed = False
        previous = ""
        seen: set[str] = set()
        for block in blocks:
            normalized = re.sub(r"\s+", " ", block).strip().lower()
            if normalized == previous or normalized in seen:
                removed = True
                continue
            kept.append(block)
            seen.add(normalized)
            previous = normalized
        if removed:
            return "\n\n".join(kept).strip(), True
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if len(lines) > 1:
        kept_lines: list[str] = []
        removed = False
        last = ""
        for line in lines:
            normalized = re.sub(r"\s+", " ", line).strip().lower()
            if normalized == last:
                removed = True
                continue
            kept_lines.append(line)
            last = normalized
        if removed:
            return "\n".join(kept_lines).strip(), True
    return value, False


def _strip_labelled_dialogue_narration(text: str, assistant_controls: list[str]) -> tuple[str, bool]:
    """Remove narration accidentally placed inside an assistant dialogue quote.

    Some local RP models output: **Char:** "The rain falls. Char's voice is soft.
    I am here." A label/colon quote should contain only spoken words. This
    strips generic narration/self-description from that quoted payload while
    preserving the remaining spoken sentence(s).
    """
    value = str(text or "")
    if not value.strip() or not assistant_controls:
        return value.strip(), False
    removed_any = False
    body_terms = r"words?|voice|tone|expression|eyes|face|posture|body|hands|shoulders|gaze|breath"
    def repl(match: re.Match) -> str:
        nonlocal removed_any
        label = re.sub(r"\s*:\s*\*\*\s*$", "**", match.group(1)).strip()
        label = re.sub(r"\s*:\s*$", "", label).strip()
        label = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
        quote = match.group(2).strip()
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", quote) if p.strip()]
        kept: list[str] = []
        for part in parts:
            is_narrative = False
            if re.search(r"\b(?:rain|room|street|station|glass|wind|light|shadow|air|silence)\b", part, flags=re.IGNORECASE) and not re.search(r"\b(?:I|I'm|I’m|we|you|me|my|our)\b", part, flags=re.IGNORECASE):
                is_narrative = True
            for name in assistant_controls:
                aliases = [name]
                first = name.split()[0].strip() if name.split() else ""
                if first and first.lower() != name.lower():
                    aliases.append(first)
                for alias in aliases:
                    if re.search(rf"\b{re.escape(alias)}(?:'s)?\s+(?:{body_terms})\b", part, flags=re.IGNORECASE):
                        is_narrative = True
            if is_narrative:
                removed_any = True
                continue
            kept.append(part)
        if not kept:
            removed_any = True
            return ""
        return f'{label}: "{" ".join(kept)}"'
    for name in assistant_controls:
        pattern = re.compile(rf"((?:\*\*)?{re.escape(name)}\s*(?:\([^)]*\))?(?:\s*:\s*\*\*|(?:\*\*)?\s*:))\s*[\"“]([^\"”]+)[\"”]", re.IGNORECASE | re.DOTALL)
        value = pattern.sub(repl, value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value, removed_any


def _strip_user_controlled_sentences(text: str, user_controls: list[str], primary: str = "") -> tuple[str, bool]:
    if not user_controls:
        return str(text or "").strip(), False
    pieces = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    kept: list[str] = []
    removed = False
    quote_spans = _quoted_spans(str(text or ""))
    # Sentence-level offset approximation for quote protection.
    cursor = 0
    user_label_patterns = [re.compile(rf"^\s*(?:\*\*)?{re.escape(name)}(?:\*\*)?\s*:", re.IGNORECASE) for name in user_controls]
    user_subject_patterns = []
    body_state_terms = (
        r"posture|shoulders?|hands?|eyes?|face|expression|voice|breath|hair|body|stance|gaze|tears?|tone|mouth|jaw|arms?|fingers?"
    )
    state_verbs = (
        r"is|are|was|were|remains?|looks?|seems?|stands?|sits?|steps?|moves?|turns?|watches?|waits?|breathes?|"
        r"speaks?|says?|asks?|replies?|thinks?|feels?|decides?|hunches?|shakes?|tenses?|cries?|weeps?|glances?|stares?"
    )
    for name in user_controls:
        first = name.split()[0].strip()
        aliases = [name] + ([first] if first and first.lower() != name.lower() else [])
        for alias in aliases:
            escaped = re.escape(alias)
            user_subject_patterns.extend([
                # User-owned character as sentence/clause subject: "Ren stands...", "... Ren remains..."
                re.compile(rf"(?:^|[.;!?]\s+|,\s*){escaped}\s+(?:{state_verbs})\b", re.IGNORECASE),
                # Possessive state/body description anywhere outside dialogue: "Ren's posture..."
                re.compile(rf"\b{escaped}(?:'s)\s+(?:{body_state_terms})\b[^.!?]{{0,180}}", re.IGNORECASE),
                # Appositive state/body clause: "Ren, his posture tense..."
                re.compile(rf"\b{escaped}\b\s*,\s*(?:his|her|their|the)\s+(?:{body_state_terms})\b[^.!?]{{0,180}}", re.IGNORECASE),
                # Appositive adjective/body-state clause: "Ren, hunched under..."
                re.compile(rf"\b{escaped}\b\s*,\s*(?:hunched|tense|shaken|shaking|crying|watchful|silent|quiet|still|waiting|trembling|angry|afraid|distressed|red-eyed)\b[^.!?]{{0,160}}", re.IGNORECASE),
            ])
    for sentence in pieces:
        stripped = sentence.strip()
        if not stripped:
            cursor += len(sentence) + 1
            continue
        # Dialogue from assistant-controlled character may address user names;
        # never remove a whole assistant quote just because it says the name.
        if primary and re.match(rf"^\s*{re.escape(primary)}\s*:\s*\"", stripped, flags=re.IGNORECASE):
            kept.append(stripped)
            cursor += len(sentence) + 1
            continue
        outside_quotes = re.sub(r'"[^"\n]*"', '""', stripped)
        should_remove = any(p.search(outside_quotes) for p in user_label_patterns + user_subject_patterns)
        if should_remove:
            removed = True
        else:
            kept.append(stripped)
        cursor += len(sentence) + 1
    return " ".join(kept).strip(), removed


def _scene_post_history_instruction(directives: dict[str, Any] | None) -> str:
    """Final near-generation instruction, similar to RP frontend post-history notes.

    Main/system prompts can be diluted by long context. This short block is
    placed after transcript history so the active lane and output shape are the
    last instructions the model sees. It is generic and derives names from the
    current scene-control contract.
    """
    assistant_controls = _scene_controlured_names(directives, "assistant_controls")
    user_controls = _scene_controlured_names(directives, "user_controls")
    primary = str((directives or {}).get("assistant_primary") or "").strip() or (assistant_controls[0] if assistant_controls else "the assistant-controlled character")
    lines = [
        "Post-history scene instruction:",
        f"Write the next reply through {primary} only.",
        "Use one reply only from one Neo-controlled speaker. Do not repeat the same paragraph, line, or speaker label.",
        "Use clean in-scene prose. Do not output reports, summaries, packet text, analysis, or setup notes.",
        "Use the Character identity lock above for names, gender, and pronouns; do not guess pronouns.",
        "Use at most one speaker label for the assistant-controlled character; do not restart the same reply with repeated labels.",
        "Do not place narration inside dialogue quotation marks; quoted text must be spoken words only.",
        "Do not copy or quote this instruction block. Do not write 'Assistant turn', 'Scene input', or 'Continue as ... only' in the reply.",
    ]
    control_mode = str((directives or {}).get("control_mode") or "strict").strip().lower()
    if user_controls:
        lines.append(f"User-controlled characters are: {', '.join(user_controls)}.")
        if control_mode == "moderate":
            lines.append("Moderate control mode: you may reference user-controlled characters only as continuity already established by the user; do not speak for them, decide for them, or add new actions/emotions.")
        else:
            lines.append("Strict control mode: do not add new dialogue, actions, body language, emotions, thoughts, or decisions for user-controlled characters.")
    return "\n".join(lines)


def _scene_quality_prompt_lines(directives: dict[str, Any] | None, scene_packet_lines: list[str] | None = None) -> list[str]:
    assistant_controls = _scene_controlured_names(directives, "assistant_controls")
    primary = str((directives or {}).get("assistant_primary") or "").strip() or (assistant_controls[0] if assistant_controls else "the assistant-controlled character")
    has_packet = bool(scene_packet_lines)
    lines = [
        "Scene quality contract:",
        f"- Write one grounded scene beat through {primary} only.",
        "- Use natural prose and dialogue, not report/template output.",
        "- Prefer 2–5 concise paragraphs: one observable action/environment beat, then character dialogue when useful.",
        "- Do not merely restate the user turn. Respond to it with the assistant-controlled character's choice, restraint, question, or offer.",
        "- Use names/pronouns/identity exactly as provided by packet/setup. If pronouns are not specified, use the character name instead of guessing.",
        "- Do not repeat the assistant speaker label or generate multiple alternate replies in one turn.",
        "- Do not invent a breakup, betrayal, confession, injury, new relationship fact, or solved mystery unless confirmed by the user turn or packet.",
    ]
    if has_packet:
        lines.append("- Ground the reply in the active packet/setup details that are relevant to this exact turn; do not copy the packet as a list.")
    else:
        lines.append("- If packet context is missing, keep the beat conservative and ask for/leave room for the user's next action instead of inventing canon.")
    return lines


def _scene_character_identity_map(scene_packet: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """Extract generic character identity facts from the active Scene Packet.

    The map is data-driven from Forge character records. It is used for prompt
    anchoring and generic pronoun repair; it never embeds universe-specific lore.
    """
    packet = scene_packet if isinstance(scene_packet, dict) else {}
    out: dict[str, dict[str, str]] = {}
    for row in packet.get("character_context") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        identity = fields.get("identity") if isinstance(fields.get("identity"), dict) else {}
        name = str(identity.get("public_identity_label") or payload.get("display_label") or payload.get("label") or row.get("title") or "").strip()
        if not name:
            continue
        out[name] = {
            "pronouns": str(identity.get("pronouns") or "").strip(),
            "gender": str(identity.get("gender") or "").strip(),
            "source_id": str(row.get("source_id") or payload.get("id") or "").strip(),
        }
    return out


def _scene_identity_lock_lines(scene_packet: dict[str, Any] | None, directives: dict[str, Any] | None = None) -> list[str]:
    identities = _scene_character_identity_map(scene_packet)
    if not identities:
        return []
    assistant_controls = set(_scene_controlured_names(directives, "assistant_controls"))
    user_controls = set(_scene_controlured_names(directives, "user_controls"))
    lines = [
        "Character identity lock:",
        "- Use each character's listed pronouns/gender exactly. If missing, use the character name instead of guessing.",
    ]
    for name, info in identities.items():
        control = "assistant-controlled" if name in assistant_controls else "user-controlled" if name in user_controls else "scene-cast"
        bits = [name]
        if info.get("pronouns"):
            bits.append(f"pronouns={info['pronouns']}")
        if info.get("gender"):
            bits.append(f"gender={info['gender']}")
        bits.append(f"control={control}")
        lines.append("- " + "; ".join(bits))
    return lines


def _pronoun_parts(pronouns: str) -> dict[str, str]:
    clean = str(pronouns or "").strip().lower().replace(" ", "")
    if clean in {"he/him", "he/him/his", "he"}:
        return {"subject": "he", "object": "him", "poss_adj": "his", "poss_pronoun": "his"}
    if clean in {"she/her", "she/her/hers", "she"}:
        return {"subject": "she", "object": "her", "poss_adj": "her", "poss_pronoun": "hers"}
    if clean in {"they/them", "they/them/theirs", "they"}:
        return {"subject": "they", "object": "them", "poss_adj": "their", "poss_pronoun": "theirs"}
    if clean in {"it/its", "it"}:
        return {"subject": "it", "object": "it", "poss_adj": "its", "poss_pronoun": "its"}
    return {}


def _cap_like(replacement: str, original: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _repair_primary_pronouns(text: str, primary: str, identity_map: dict[str, dict[str, str]] | None) -> tuple[str, bool]:
    """Repair obvious self-pronoun drift for the assistant-controlled character.

    This only runs when one primary assistant character is known and that
    character's pronouns are present in the packet. It is generic: it corrects
    pronoun words to the packet-provided pronoun set, not to any hardcoded lore.
    """
    value = str(text or "")
    if not value.strip() or not primary or not isinstance(identity_map, dict):
        return value.strip(), False
    info = identity_map.get(primary) or {}
    parts = _pronoun_parts(info.get("pronouns") or "")
    if not parts:
        return value.strip(), False
    target_subject = parts["subject"]
    target_object = parts["object"]
    target_poss = parts["poss_adj"]
    if target_subject == "she":
        wrong_subjects = ("he", "they")
        wrong_object_poss = ("him", "his", "their", "them")
    elif target_subject == "he":
        wrong_subjects = ("she", "they")
        wrong_object_poss = ("her", "hers", "their", "them")
    elif target_subject == "they":
        wrong_subjects = ("he", "she")
        wrong_object_poss = ("him", "his", "her", "hers")
    else:
        wrong_subjects = ("he", "she", "they")
        wrong_object_poss = ("him", "his", "her", "hers", "their", "them")
    # Only repair sentences that clearly discuss the primary character/self-lane.
    sentence_parts = re.split(r"(?<=[.!?])\s+", value)
    changed = False
    repaired: list[str] = []
    body_terms = r"voice|tone|expression|eyes|face|posture|body|hands|shoulders|gaze|breath|words|body language"
    primary_context = False
    for sentence in sentence_parts:
        original = sentence
        explicit_primary = bool(re.search(rf"\b{re.escape(primary)}(?:'s)?\b|\b{re.escape(primary.split()[0])}(?:'s)?\b", sentence, flags=re.IGNORECASE)) if primary.split() else False
        explicit_primary = explicit_primary or bool(re.match(r"^\s*(?:\*\*)?" + re.escape(primary) + r"(?:\s*\([^)]*\))?(?:\*\*)?\s*:", sentence, flags=re.IGNORECASE))
        # After an explicit assistant-character lane/name, local models often use
        # the wrong pronoun in following self-description sentences. Keep the
        # repair scope active until another named speaker lane appears.
        if re.match(r"^\s*(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}(?:\s*\([^)]*\))?(?:\*\*)?\s*:", sentence) and not explicit_primary:
            primary_context = False
        scope_hint = explicit_primary or primary_context
        if scope_hint:
            for wrong in wrong_subjects:
                sentence = re.sub(rf"\b{wrong}\b", lambda m: _cap_like(target_subject, m.group(0)), sentence, flags=re.IGNORECASE)
            for wrong in wrong_object_poss:
                # Body/voice nouns need possessive adjective; other uses get object form.
                sentence = re.sub(rf"\b{wrong}\b(?=\s+(?:{body_terms})\b)", lambda m: _cap_like(target_poss, m.group(0)), sentence, flags=re.IGNORECASE)
                sentence = re.sub(rf"\b{wrong}\b", lambda m: _cap_like(target_object if wrong in {"him", "her", "them"} else target_poss, m.group(0)), sentence, flags=re.IGNORECASE)
        changed = changed or (sentence != original)
        if explicit_primary:
            primary_context = True
        repaired.append(sentence)
    return " ".join(x.strip() for x in repaired if x.strip()).strip(), changed




def _normalize_response_style_speaker_labels(text: str, assistant_controls: list[str]) -> tuple[str, bool]:
    """Normalize backend labels like "Name's Response:" to a normal speaker label."""
    value = str(text or "")
    if not value.strip() or not assistant_controls:
        return value.strip(), False
    changed = False
    alias_map = _control_name_aliases(assistant_controls)
    for alias, canonical in sorted(alias_map.items(), key=lambda row: (-len(row[0]), row[0])):
        pattern = re.compile(
            rf"(^|\n)\s*(?:\*\*)?{re.escape(alias)}(?:'s)?\s+Response(?:\s*\([^)]*\))?(?:\*\*)?\s*:\s*",
            re.IGNORECASE,
        )
        value, count = pattern.subn(lambda m, c=canonical: f"{m.group(1)}{c}: ", value)
        if count:
            changed = True
    value, count = re.subn(r"(^|\n)\s*(?:\*\*)?(?:Assistant|Model|AI)\s+Response(?:\*\*)?\s*:\s*", r"\1", value, flags=re.IGNORECASE)
    changed = changed or bool(count)
    return value.strip(), changed

def _normalize_assistant_speaker_labels(text: str, assistant_controls: list[str]) -> tuple[str, bool]:
    value = str(text or "")
    if not value.strip() or not assistant_controls:
        return value.strip(), False
    changed = False
    for name in assistant_controls:
        pattern = re.compile(rf"(?:\*\*)?{re.escape(name)}(?:\s*(?:\([^)]*\)|['’]s\s+Response|\s+Response))?\s*(?:\*\*)?\s*:", re.IGNORECASE)
        def repl(match: re.Match, n=name) -> str:
            nonlocal changed
            if match.group(0) != f"{n}:":
                changed = True
            return f"{n}:"
        value = pattern.sub(repl, value)
    return value.strip(), changed


def _collapse_repeated_assistant_lanes(text: str, assistant_controls: list[str]) -> tuple[str, bool]:
    value = str(text or "").strip()
    if not value or not assistant_controls:
        return value, False
    changed = False
    for name in assistant_controls:
        # Split every time the backend opens the same assistant lane. If it opens
        # the same lane repeatedly in one generation, keep the first lane only.
        label = re.compile(rf"(?=(?:^|\n|\s)(?:\*\*)?{re.escape(name)}\s*(?:\([^)]*\))?\s*(?:\*\*)?\s*:)", re.IGNORECASE)
        starts = [m.start() for m in label.finditer(value)]
        if len(starts) <= 1:
            continue
        first_start = starts[0]
        second_start = starts[1]
        prefix = value[:first_start].strip()
        first_lane = value[first_start:second_start].strip()
        value = (prefix + "\n" + first_lane if prefix else first_lane).strip()
        changed = True
    value, normalized = _normalize_assistant_speaker_labels(value, assistant_controls)
    changed = changed or normalized
    return value.strip(), changed



def _strip_prompt_boundary_tail(text: str) -> tuple[str, bool]:
    """Drop leaked prompt-boundary tail text from backend output.

    Local backends sometimes echo near-generation scaffolding such as
    "Continue as X only" or "Assistant turn:". Those strings are generic prompt
    boundary markers, not roleplay content. Keep any valid prose before the
    first marker, but let the caller decide whether a guarded retry is needed.
    """
    value = str(text or "")
    if not value.strip():
        return "", False
    patterns = (
        r"(?:^|\n|\s)continue\s+as\s+[A-Z][A-Za-z0-9 _'-]{1,80}\s+only\s*[.!?:]?(?:\s|$)",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:Assistant|Model|AI|User|Player|Human|Scene)\s+(?:turn|reply|response|input)(?:\*\*)?\s*:",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:Next\s+Beat|Next\s+Scene\s+Beat)(?:\*\*)?\s*:",
        r"\[\s*content\s+redacted[^\]]*\]",
        r"\bcontent\s+redacted\b",
        r"\bthe\s+scene\s+referenc(?:es|ed)\s+\w+\b",
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:Summary|Continuity\s+Summary)(?:\*\*)?\s*(?:\n|$)",
    )
    first: int | None = None
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match and (first is None or match.start() < first):
            first = match.start()
    if first is None:
        return value.strip(), False
    return value[:first].strip(), True


def _enforce_single_assistant_speaker_lane(text: str, assistant_controls: list[str], primary: str = "") -> tuple[str, bool, str]:
    """Keep one Neo-controlled speaker lane per assistant turn.

    This is a display/persistence boundary, not story generation. If a backend
    starts a second assistant-controlled speaker label in one turn, keep the
    selected/first lane and drop the rest so a single UI display role cannot be
    followed by another character label in the body.
    """
    value = str(text or "").strip()
    if not value or not assistant_controls:
        return value, False, ""
    label_matches: list[tuple[int, int, str]] = []
    for name in assistant_controls:
        pattern = re.compile(rf"(?:^|\n)\s*(?:\*\*)?{re.escape(name)}\s*(?:\([^)]*\))?\s*(?:\*\*)?\s*:", re.IGNORECASE)
        for match in pattern.finditer(value):
            label_matches.append((match.start(), match.end(), name))
    if not label_matches:
        return value, False, ""
    label_matches.sort(key=lambda row: row[0])
    target = str(primary or "").strip()
    if not target or target not in assistant_controls:
        target = label_matches[0][2]
    target_indices = [i for i, row in enumerate(label_matches) if row[2].casefold() == target.casefold()]
    if not target_indices and target:
        # The backend may begin with unlabeled prose for the selected speaker and
        # then open a different Neo-controlled label. Keep the unlabeled prefix
        # only if it exists; otherwise report the mismatched lane so the caller
        # can regenerate through the addressed/selected speaker.
        first_label_start = label_matches[0][0]
        prefix = value[:first_label_start].strip()
        if prefix:
            return prefix, True, target
        return "", True, label_matches[0][2]
    chosen_index = target_indices[0] if target_indices else 0
    chosen_start = label_matches[chosen_index][0]
    next_start = label_matches[chosen_index + 1][0] if chosen_index + 1 < len(label_matches) else len(value)
    prefix = value[:chosen_start].strip()
    lane = value[chosen_start:next_start].strip()
    # If prose before the chosen label exists, keep it only when the chosen lane
    # is the first label. Otherwise the prefix likely belongs to another lane.
    if chosen_index == 0 and prefix:
        cleaned = f"{prefix}\n{lane}".strip()
    else:
        cleaned = lane
    changed = cleaned.strip() != value.strip() or len({row[2].casefold() for row in label_matches}) > 1 or len(label_matches) > 1
    return cleaned.strip(), changed, target

def _repair_grounding_text(prompt: dict[str, Any], user_message: str = "") -> str:
    setup = prompt.get("setup") if isinstance(prompt.get("setup"), dict) else {}
    lines: list[str] = [
        str(user_message or ""),
        str(setup.get("premise") or ""),
        str(setup.get("scene_notes") or ""),
        str(setup.get("scene_rules") or ""),
    ]
    inj = prompt.get("scene_memory_injection") if isinstance(prompt.get("scene_memory_injection"), dict) else {}
    lines.extend(str(x) for x in (inj.get("lines") or []) if str(x).strip())
    bundle = prompt.get("runtime_bundle") if isinstance(prompt.get("runtime_bundle"), dict) else {}
    lines.extend(_runtime_context_lines(bundle)[:18])
    for turn in _history_turns_for_prompt(prompt.get("transcript") if isinstance(prompt.get("transcript"), dict) else {}, limit=8):
        lines.append(str(turn.get("text") or ""))
    return "\n".join(lines)[:12000]


def _strip_explicit_user_character_lanes(text: str, user_controls: list[str]) -> tuple[str, bool]:
    value = str(text or "")
    if not value.strip() or not user_controls:
        return value.strip(), False
    removed = False
    for name in user_controls:
        lane_pattern = re.compile(
            rf"(?:^|\n|\s)(?:\*\*)?{re.escape(name)}(?:\*\*)?\s*:\s*.*?(?=(?:\n\s*(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{{1,60}}(?:\*\*)?\s*:)|(?:\n\s*(?:[-*]\s*)?(?:\*\*)?(?:Character control|Scene Packet Summary|Scene Summary|Next Scene Beat)(?:\*\*)?\s*:)|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        value, count = lane_pattern.subn(" ", value)
        if count:
            removed = True
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value, removed


def _clean_streamed_scene_text(text: str, directives: dict[str, Any] | None, user_message: str = "", grounding_text: str = "", identity_map: dict[str, dict[str, str]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Repair common backend drift before persisting/displaying a Scene turn.

    This repair is generic: it does not know any specific universe lore. It
    preserves assistant-controlled prose/dialogue, strips control/meta leakage,
    removes user-character puppeting, and replaces unsupported major dramatic
    escalations with a conservative scene beat.
    """
    original = str(text or "").strip()
    warnings: list[dict[str, Any]] = []
    if not original:
        return original, warnings
    cleaned = original
    cleaned, collapsed_repeats = _collapse_repeated_scene_blocks(cleaned)
    if collapsed_repeats:
        warnings.append({"code": "collapsed_repeated_scene_blocks", "severity": "medium", "message": "Collapsed repeated backend scene blocks."})
    cleaned, unwrapped_full_quote = _unwrap_full_quoted_narrative(cleaned)
    if unwrapped_full_quote:
        warnings.append({"code": "unwrapped_full_response_quote", "severity": "medium", "message": "Removed accidental full-response quotation wrapper before scene validation."})
    cleaned, early_boundary_tail = _strip_prompt_boundary_tail(cleaned)
    if early_boundary_tail:
        warnings.append({"code": "removed_prompt_boundary_tail", "severity": "high", "message": "Removed leaked prompt-boundary text from backend output."})
    if _has_scene_status_leak(cleaned) or _is_guardrail_block_text(cleaned):
        warnings.append({"code": "removed_scene_status_leak", "severity": "high", "message": "Removed internal scene/retry/status text from backend output."})
        warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output contained internal recovery/status text; request a fresh generated turn."})
        return "", warnings
    control_ack = re.search(r"\bScene control updated\.\s*Send the next scene beat when ready\.\s*$", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if control_ack:
        cleaned = cleaned[: control_ack.start()].rstrip()
        warnings.append({"code": "removed_control_ack_echo", "severity": "medium", "message": "Removed echoed scene-control acknowledgement from backend output."})
    cleaned = re.sub(r"\bScene control updated\.\s*", "", cleaned, flags=re.IGNORECASE).strip()
    meta_leak = bool(re.search(r"</?response>|" + _meta_leak_header_pattern(), cleaned, flags=re.IGNORECASE))
    cleaned = re.sub(r"</?response>\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned, stripped_meta_tail = _strip_meta_leak_sections(cleaned)
    if stripped_meta_tail:
        meta_leak = True
        warnings.append({"code": "removed_prompt_or_packet_leak", "severity": "high", "message": "Removed leaked prompt/control/packet section from backend output."})
        warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output leaked prompt/control/packet sections; request a fresh generated turn."})
    cleaned, stripped_boundary_tail = _strip_prompt_boundary_tail(cleaned)
    if stripped_boundary_tail:
        meta_leak = True
        warnings.append({"code": "removed_prompt_boundary_tail", "severity": "high", "message": "Removed leaked prompt-boundary text from backend output."})

    assistant_controls = _scene_controlured_names(directives, "assistant_controls")
    user_controls = _scene_controlured_names(directives, "user_controls")
    primary = str((directives or {}).get("assistant_primary") or "").strip() or (assistant_controls[0] if assistant_controls else _turn_focus_name(user_message, {}))
    cleaned, response_label_normalized = _normalize_response_style_speaker_labels(cleaned, assistant_controls)
    if response_label_normalized:
        warnings.append({"code": "normalized_response_style_speaker_label", "severity": "medium", "message": "Converted report-style speaker labels to normal scene speaker labels."})
    cleaned, normalized_labels = _normalize_assistant_speaker_labels(cleaned, assistant_controls)
    if normalized_labels:
        warnings.append({"code": "normalized_assistant_speaker_label", "severity": "low", "message": "Normalized backend speaker label formatting."})
    cleaned, single_lane_enforced, selected_lane = _enforce_single_assistant_speaker_lane(cleaned, assistant_controls, primary)
    if single_lane_enforced:
        if selected_lane and primary and selected_lane.casefold() != primary.casefold():
            warnings.append({"code": "assistant_lane_mismatch", "severity": "high", "message": "Backend answered through a different Neo-controlled character than the active addressed lane."})
            warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Request a fresh turn through the addressed assistant-controlled character."})
            return "", warnings
        if selected_lane:
            primary = selected_lane
        warnings.append({"code": "enforced_single_assistant_speaker_lane", "severity": "high", "message": "Kept one Neo-controlled speaker lane and removed additional assistant lanes."})
    cleaned, collapsed_assistant_lanes = _collapse_repeated_assistant_lanes(cleaned, assistant_controls)
    if collapsed_assistant_lanes:
        warnings.append({"code": "collapsed_repeated_assistant_lanes", "severity": "high", "message": "Collapsed repeated assistant speaker lanes into one scene reply."})
    cleaned, pronoun_repaired = _repair_primary_pronouns(cleaned, primary, identity_map)
    if pronoun_repaired:
        warnings.append({"code": "repaired_primary_character_pronouns", "severity": "medium", "message": "Corrected assistant-character pronouns from packet identity data."})
    cleaned, label_narration_removed = _strip_labelled_dialogue_narration(cleaned, assistant_controls)
    if label_narration_removed:
        warnings.append({"code": "removed_labelled_dialogue_narration", "severity": "medium", "message": "Removed narration that the backend placed inside a character dialogue label."})

    pre_lane_removed = False
    if user_controls:
        cleaned, pre_lane_removed = _strip_explicit_user_character_lanes(cleaned, user_controls)
        if pre_lane_removed:
            warnings.append({"code": "removed_user_character_lane", "severity": "high", "message": "Removed an explicit user-controlled character dialogue/narration lane from backend output."})
            warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output opened a user-controlled character lane; request a fresh generated turn."})

    # Keep assistant-controlled lanes intact instead of collapsing them to the
    # first quote. The earlier repair was too aggressive and made good prose
    # become one generic line.
    if primary:
        label_pat = re.compile(rf"(?:^|\n)\s*{re.escape(primary)}\s*:\s*(.+?)(?=\n\s*[A-Z][A-Za-z0-9 _'-]{{1,50}}\s*:|\Z)", re.IGNORECASE | re.DOTALL)
        m = label_pat.search(cleaned)
        if m and meta_leak:
            body = m.group(1).strip()
            cleaned = f"{primary}: {body}".strip()
            warnings.append({"code": "assistant_lane_preserved_from_template", "severity": "medium", "message": "Preserved assistant-controlled lane after removing template/meta wrapper."})
        elif not m:
            q = re.search(r'"([^"\n]{1,360})"', cleaned)
            if q and meta_leak:
                cleaned = f'{primary}: "{q.group(1).strip()}"'
                warnings.append({"code": "assistant_quote_relabelled", "severity": "medium", "message": "Relabelled first dialogue quote under the assistant-controlled character after template/meta output."})

    if user_controls:
        # Remove relative clauses that smuggle state/action onto a user-owned
        # character while still allowing the assistant character to address or
        # glance toward them. Example: a relative clause after a user-controlled
        # name is reduced to the name only.
        for name in user_controls:
            aliases = [name]
            first = str(name).split()[0].strip()
            if first and first.lower() != str(name).lower():
                aliases.append(first)
            for alias in aliases:
                cleaned = re.sub(
                    rf"\b{re.escape(alias)}\b\s*,\s*who\s+[^.!?]+",
                    alias,
                    cleaned,
                    flags=re.IGNORECASE,
                )
        cleaned, lane_removed = _strip_explicit_user_character_lanes(cleaned, user_controls)
        stripped, removed = _strip_user_controlled_sentences(cleaned, user_controls, primary=primary)
        removed = removed or lane_removed
        if removed:
            # After removing an explicit user-character state sentence, strip
            # orphan body/emotion pronoun sentences that usually continue the
            # same puppeting. This is generic and only runs after a confirmed
            # user-character removal in the same backend reply.
            stripped = re.sub(
                r"(?:^|(?<=[.!?])\s+)(?:His|Her|Their|his|her|their)\s+(?:\w+\s+){0,3}(?:eyes|face|posture|shoulders|hands|voice|breath|hair|expression)\b[^.!?]*(?:[.!?]|$)",
                "",
                stripped,
            ).strip()
        if removed:
            cleaned = stripped
            warnings.append({"code": "removed_user_character_puppeting", "severity": "high", "message": "Removed narration/action text for user-controlled character(s)."})
            warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output wrote user-controlled character state/action/dialogue; request a fresh generated turn."})

    if _has_unsupported_scene_escalation(cleaned, grounding_text=grounding_text, user_message=user_message):
        stripped, removed_escalation = _strip_unsupported_escalation_sentences(cleaned, grounding_text=grounding_text, user_message=user_message)
        if removed_escalation:
            cleaned = stripped
            warnings.append({"code": "removed_unsupported_scene_escalation", "severity": "high", "message": "Removed unsupported major scene escalation from backend output."})
        if not cleaned.strip():
            warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output became empty after safety repair; request a fresh generated turn."})

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    if not cleaned:
        warnings.append({"code": "requires_backend_regeneration", "severity": "high", "message": "Backend output was empty after repair; request a fresh generated turn."})
        return "", warnings
    return cleaned, warnings



def _normalize_turn_input_style(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in {"free_typing", "choice_assist", "hybrid"} else "free_typing"


def _choice_assist_enabled(payload: dict[str, Any], setup: dict[str, Any]) -> bool:
    style = _normalize_turn_input_style(payload.get("turn_input_style") or setup.get("turn_input_style"))
    return style in {"choice_assist", "hybrid"}


def _choice_assist_actor(transcript: dict[str, Any], payload: dict[str, Any]) -> str:
    name = str((payload or {}).get("active_user_character_name") or "").strip()
    if name:
        return name
    setup = transcript.get("session_setup") if isinstance(transcript.get("session_setup"), dict) else {}
    user_controls = setup.get("user_controls") if isinstance(setup.get("user_controls"), list) else []
    for item in user_controls:
        if str(item or "").strip():
            return str(item).strip()
    return "Your character"


def _clean_choice_context_text(text: str) -> str:
    value = str(text or "")
    if _has_scene_status_leak(value) or _is_guardrail_block_text(value):
        return ""
    value, _ = _strip_prompt_boundary_tail(value)
    value, _ = _strip_meta_leak_sections(value)
    value = re.sub(r"\[\s*content\s+redacted[^\]]*\]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    if _has_scene_status_leak(value) or _is_guardrail_block_text(value):
        return ""
    return value[:1600]


def _last_dialogue_or_question(text: str) -> str:
    value = _clean_choice_context_text(text)
    quoted = re.findall(r'"([^"\n]{8,220})"', value)
    if quoted:
        return quoted[-1].strip()
    questions = re.findall(r"([^.!?]{8,220}\?)", value)
    if questions:
        return questions[-1].strip()
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", value) if len(p.strip()) >= 10]
    return parts[-1][:220] if parts else ""


def _choice_prompt(label: str, line: str) -> tuple[str, str]:
    return label.strip(), re.sub(r"\s+", " ", line).strip()


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        candidates.extend(part.strip() for idx, part in enumerate(parts) if idx % 2 == 1)
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(candidate[start:end + 1])
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return None


def _extract_choice_options_from_partial_json(raw: str) -> dict[str, Any] | None:
    """Recover completed Choice Assist objects from truncated local-backend JSON.

    Local backends can hit max_tokens in the middle of the final option. This
    parser deliberately accepts only completed objects that contain both label
    and prompt string fields. It never invents missing option text.
    """
    value = str(raw or "")
    if not value.strip():
        return None
    parsed = _extract_json_object(value)
    if isinstance(parsed, dict):
        return parsed

    options: list[dict[str, str]] = []
    # Prefer fully balanced option objects inside the options array. This also
    # ignores extra keys such as action/intent without trusting them.
    for match in re.finditer(r"\{[^{}]*?\}", value, flags=re.DOTALL):
        chunk = match.group(0)
        try:
            data = json.loads(chunk)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        label = str(data.get("label") or data.get("title") or "").strip()
        prompt = str(data.get("prompt") or data.get("text") or data.get("line") or "").strip()
        if label and prompt:
            options.append({"label": label, "prompt": prompt})
        if len(options) >= 4:
            break
    if options:
        return {"options": options, "partial_recovered": True}

    # Last-resort regex for outputs that are JSON-like but not fully balanced.
    for match in re.finditer(
        r'"label"\s*:\s*"(?P<label>(?:\\.|[^"\\])*)"\s*,\s*"prompt"\s*:\s*"(?P<prompt>(?:\\.|[^"\\])*)"',
        value,
        flags=re.DOTALL,
    ):
        try:
            label = json.loads('"' + match.group("label") + '"')
            prompt = json.loads('"' + match.group("prompt") + '"')
        except Exception:
            continue
        if str(label).strip() and str(prompt).strip():
            options.append({"label": str(label).strip(), "prompt": str(prompt).strip()})
        if len(options) >= 4:
            break
    if options:
        return {"options": options, "partial_recovered": True}
    return None


def _choice_context_transcript_lines(transcript: dict[str, Any], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for turn in _history_turns_for_prompt(transcript, limit=limit):
        role = str(turn.get("display_role") or turn.get("role") or "Turn").strip()
        text = _clean_choice_context_text(str(turn.get("text") or ""))
        if not text:
            continue
        lines.append(f"{role}: {text[:900]}")
    return lines


def _sanitize_choice_action_text(text: str, *, actor: str, blocked_names: list[str]) -> str:
    value = _clean_choice_context_text(text)
    value = re.sub(r"^(?:[-*\d.)\s]+)", "", value).strip()
    value = re.sub(r"\b(?:Assistant|Model|AI|User|Player|Human)\s+(?:turn|reply|response|input)\b\s*:.*$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:Next\s+Beat|Summary|Scene)\s*:.*$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    for name in blocked_names:
        if re.search(rf"^\s*(?:\*\*)?{re.escape(name)}(?:\*\*)?\s*:", value, flags=re.IGNORECASE):
            return ""
    if len(value) > 260:
        value = value[:260].rsplit(" ", 1)[0].strip()
    return value.strip(' \t\n"')


def _normalize_choice_assist_options(raw_options: Any, *, actor: str, transcript: dict[str, Any], control_mode: str) -> list[dict[str, str]]:
    if not isinstance(raw_options, list):
        return []
    neo_controls = [str(x).strip() for x in ((transcript.get("session_setup") or {}).get("neo_controls") or []) if str(x).strip()]
    blocked_names = [name for name in neo_controls if name.casefold() != actor.casefold()]
    actions: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_options, start=1):
        if isinstance(item, dict):
            prompt_text = str(item.get("prompt") or item.get("text") or item.get("line") or "").strip()
            label = str(item.get("label") or item.get("title") or "").strip()
        else:
            prompt_text = str(item or "").strip()
            label = ""
        clean_prompt = _sanitize_choice_action_text(prompt_text, actor=actor, blocked_names=blocked_names)
        if not clean_prompt:
            continue
        if any(re.search(rf"\b{re.escape(name)}\b\s+(?:says|said|asks|asked|moves|looks|feels|thinks|responds)\b", clean_prompt, flags=re.IGNORECASE) for name in blocked_names):
            continue
        key = clean_prompt.casefold()
        if key in seen:
            continue
        seen.add(key)
        if not label:
            label = "Choice " + str(len(actions) + 1)
        actions.append({
            "id": f"choice_{len(actions) + 1}",
            "label": label[:60],
            "prompt": clean_prompt,
            "intent": "scene_turn",
            "source": "roleplay_choice_assist_backend",
            "actor": actor,
            "mode": control_mode,
        })
        if len(actions) >= 4:
            break
    return actions


def _build_choice_assist_actions(
    *,
    payload: dict[str, Any],
    setup: dict[str, Any],
    transcript: dict[str, Any],
    assistant_text: str,
) -> list[dict[str, str]]:
    """Generate Choice Assist options from the active scene context.

    Suggestions are a separate backend task, not static templates and not story
    transcript content. If clean JSON cannot be produced, return no choices
    with a warning instead of reusing canned options.
    """
    if not _choice_assist_enabled(payload, setup):
        return []
    actor = _choice_assist_actor(transcript, payload)
    session = transcript.get("session_setup") if isinstance(transcript.get("session_setup"), dict) else {}
    control_mode = str(session.get("control_mode") or payload.get("control_mode") or "strict").strip().lower() or "strict"
    latest_reply = _clean_choice_context_text(assistant_text)
    if not latest_reply:
        transcript["suggested_actions_warning"] = "Choice Assist skipped because the latest assistant reply was empty after cleanup."
        return []
    history_lines = _choice_context_transcript_lines(transcript, limit=8)
    neo_controls = [str(x).strip() for x in (session.get("neo_controls") or []) if str(x).strip()]
    user_controls = [str(x).strip() for x in (session.get("user_controls") or []) if str(x).strip()]
    def _choice_messages(force_compact: bool = False) -> list[dict[str, str]]:
        system_note = (
            "You generate next-turn options for a live roleplay scene. "
            "Return only JSON with shape {\"options\":[{\"label\":\"...\",\"prompt\":\"...\"}]}. "
            "Each prompt must be text the user can send as their controlled character. "
            "Do not write the assistant-controlled characters' actions, dialogue, thoughts, or narration. "
            "Do not include summaries, analysis, markdown headings, explanations, action fields, or extra keys. "
            "Make each option specific to the latest assistant reply and current scene, not generic."
        )
        if force_compact:
            system_note += " Use very compact labels and prompts. Return valid complete JSON only."
        return [
            {"role": "system", "content": system_note},
            {
                "role": "user",
                "content": (
                    f"User-controlled character for suggestions: {actor}\n"
                    f"All user-controlled characters: {', '.join(user_controls) or actor}\n"
                    f"Neo-controlled characters: {', '.join(neo_controls) or 'environment only'}\n"
                    f"Control mode: {control_mode}\n\n"
                    "Recent transcript:\n" + ("\n".join(history_lines[-8:]) or "(none)") + "\n\n"
                    f"Latest Neo reply:\n{latest_reply}\n\n"
                    "Generate exactly 4 distinct next-turn options for the user. "
                    "Options may ask, refuse, reveal, deflect, move, or challenge, but must remain in the user's character lane."
                ),
            },
        ]

    def _run_choice_backend(force_compact: bool = False, max_tokens: int = 360) -> tuple[list[dict[str, str]], str, str]:
        result = execute_roleplay_text_backend(
            profile_id=str((payload or {}).get("profile_id") or "") or None,
            messages=_choice_messages(force_compact=force_compact),
            max_tokens=max_tokens,
            temperature=0.62 if force_compact else 0.72,
            top_p=0.9 if force_compact else 0.92,
            timeout_seconds=120,
            stop=["\n\nAssistant", "\n\nScene", "\n###"],
        )
        raw = str(result.get("text") or "").strip()
        if not result.get("ok") or not raw:
            return [], raw, str(result.get("error") or result.get("status") or "Choice Assist backend returned no options.")
        parsed = _extract_choice_options_from_partial_json(raw) or {}
        actions = _normalize_choice_assist_options(parsed.get("options"), actor=actor, transcript=transcript, control_mode=control_mode)
        return actions, raw, ""

    actions, raw, error = _run_choice_backend(force_compact=False, max_tokens=360)
    if len(actions) < 4:
        retry_actions, retry_raw, retry_error = _run_choice_backend(force_compact=True, max_tokens=420)
        if len(retry_actions) >= len(actions):
            actions, raw, error = retry_actions, retry_raw, retry_error
    if not actions:
        transcript["suggested_actions_warning"] = error or "Choice Assist could not parse clean scene-specific options from the backend response."
        transcript["suggested_actions_raw_preview"] = raw[:500]
        return []
    transcript.pop("suggested_actions_warning", None)
    transcript.pop("suggested_actions_raw_preview", None)
    return actions[:4]

def _attach_choice_assist_to_transcript(
    *,
    payload: dict[str, Any],
    setup: dict[str, Any],
    transcript: dict[str, Any],
    assistant_turn: dict[str, Any],
    assistant_text: str,
) -> list[dict[str, str]]:
    actions = _build_choice_assist_actions(payload=payload, setup=setup, transcript=transcript, assistant_text=assistant_text)
    style = _normalize_turn_input_style(payload.get("turn_input_style") or setup.get("turn_input_style"))
    if actions:
        assistant_turn["suggested_actions"] = actions
        transcript["suggested_actions"] = actions
        transcript["suggested_actions_updated_at"] = _now()
        transcript["turn_input_style"] = style
        transcript.pop("suggested_actions_warning", None)
    else:
        transcript["suggested_actions"] = []
        transcript["turn_input_style"] = style
    return actions

def _history_turns_for_prompt(transcript: dict[str, Any], *, limit: int = 16) -> list[dict[str, Any]]:
    turns = []
    for turn in (transcript.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        status = str(turn.get("status") or "")
        # Control acknowledgements are UI state, not narrative history. Including
        # them makes the backend echo "Scene control updated" in later replies.
        if status == "scene_control_updated":
            continue
        if str(turn.get("role") or "") == "system" and status.startswith("scene_control"):
            continue
        text = str(turn.get("text") or "")
        if _is_scene_control_directive_only(text):
            continue
        if _is_guardrail_block_text(text) or _has_scene_status_leak(text) or _is_polluted_scene_memory_text(text):
            continue
        if status in {"scene_turn_blocked", "guardrail_blocked", "stream_guardrail_blocked"}:
            continue
        turns.append(turn)
    return turns[-limit:]

def _persist_scene_control_directive(transcript: dict[str, Any], directives: dict[str, Any]) -> dict[str, Any]:
    current = transcript.get("scene_control_overrides") if isinstance(transcript.get("scene_control_overrides"), dict) else {}
    merged = _merge_scene_control_directives(current, directives)
    if merged.get("assistant_controls") or merged.get("user_controls"):
        transcript["scene_control_overrides"] = merged
        transcript["updated_at"] = _now()
    return merged


def _scene_control_safety_lines(user_message: str, payload: dict[str, Any], setup: dict[str, Any], scene_packet: dict[str, Any], directives: dict[str, Any] | None = None) -> list[str]:
    lines: list[str] = [
        "Scene control hard rules:",
        "- Do not write dialogue, thoughts, feelings, decisions, or physical actions for user-controlled characters.",
        "- Do not invent cheating, sexual betrayal, explicit sexual events, pregnancy, assault, injury, or relationship reversals unless the user turn or active packet explicitly says so.",
        "- Keep new dramatic claims reversible and grounded in the packet; prefer partial truth, pressure, and observable action over soap-opera reveals.",
        "- Do not output meta templates, XML, markdown reports, analysis cards, or fields named Response, Character Name, Scene description, Dialogue, Additional notes, or Character control rules.",
    ]
    directives = directives if isinstance(directives, dict) else {}
    override_lines = _scene_control_prompt_lines(directives)
    if override_lines:
        lines.extend(override_lines)
    focus_name = _turn_focus_name(user_message, payload)
    if focus_name:
        lines.extend([
            f"- This turn is single-lane performance: write as {focus_name} only.",
            f"- {focus_name} may speak/act. Other named characters may be observed or addressed, but do not give them new dialogue/actions/thoughts unless the user supplied those actions.",
        ])
    instructions = scene_packet.get("model_instructions") if isinstance(scene_packet.get("model_instructions"), dict) else {}
    player_name = str(payload.get("player_character_name") or setup.get("player_character_name") or instructions.get("player_character_name") or scene_packet.get("player_character_name") or "").strip()
    player_ids = instructions.get("player_character_ids") if isinstance(instructions.get("player_character_ids"), list) else []
    forbidden = instructions.get("forbidden_player_control") if isinstance(instructions.get("forbidden_player_control"), list) else []
    if player_name or player_ids:
        lines.append(f"- User-controlled character lock: {player_name or ', '.join(str(x) for x in player_ids)}. Never puppet this character.")
    for item in forbidden[:8]:
        if str(item).strip():
            lines.append(f"- Forbidden player control: {str(item).strip()}")
    return lines

def build_scene_turn_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    scene_id = str(payload.get("scene_id") or "default")
    setup = load_scene_setup(scene_id)
    transcript = load_scene_transcript(scene_id)
    user_message = str(payload.get("message") or payload.get("user_turn") or "").strip()
    if not user_message and payload.get("continue_scene"):
        user_message = "[Continue the scene from the current transcript state. Do not repeat the last reply.]"
    scene_control_directives = _merge_scene_control_directives(
        _session_control_directives(transcript),
        transcript.get("scene_control_overrides") if isinstance(transcript.get("scene_control_overrides"), dict) else None,
        _scene_control_directives_from_text(str((payload or {}).get("scene_control") or "")),
        _scene_control_directives_from_text(user_message),
    )
    runtime_bundle_id = str(payload.get("runtime_bundle_id") or setup.get("runtime_bundle_id") or "")
    bundle = get_runtime_bundle(runtime_bundle_id) if runtime_bundle_id else None
    scope = runtime_bundle_id or scene_id
    # Scene Chat must reach the text backend quickly. The active Scene Packet is
    # already the authoritative runtime context, so per-turn live retrieval is
    # opt-in. Running keyword/vector retrieval on every send can block before
    # KoboldCpp is contacted, which looks like a dead send in the UI.
    live_retrieval_enabled = bool(
        payload.get("enable_live_retrieval")
        or payload.get("live_retrieval")
        or payload.get("run_retrieval")
    )
    if live_retrieval_enabled:
        try:
            retrieval = search_retrieval_foundation_payload({
                "query": user_message or setup.get("premise") or setup.get("title") or scene_id,
                "scope_id": scope,
                "memory_types": ["entities", "memory_fragments", "shared_memories", "continuity", "turn_summaries", "story_checkpoints"],
                "limit": int(payload.get("memory_limit") or 6),
                "source": "scene_turn_execution",
            })
        except Exception as exc:
            retrieval = {
                "ok": False,
                "status": "live_retrieval_error",
                "error": str(exc),
                "search": {"results": [], "trace_id": ""},
            }
    else:
        retrieval = {
            "ok": True,
            "status": "skipped",
            "reason": "live_retrieval_disabled_scene_packet_first",
            "search": {"results": [], "trace_id": ""},
        }
    scene_memory_injection = build_scene_memory_injection_payload(setup=setup, request_payload=payload or {}, runtime_bundle=bundle)
    active_scene_packet = scene_memory_injection.get("scene_packet") if isinstance(scene_memory_injection.get("scene_packet"), dict) else {}
    active_scene_packet_id = str(active_scene_packet.get("scene_packet_id") or active_scene_packet.get("packet_id") or "").strip()
    active_scene_id = _authoritative_scene_id_from_packet(active_scene_packet) or scene_id
    packet_title = _authoritative_scene_title_from_packet(active_scene_packet)
    packet_premise = _authoritative_scene_premise_from_packet(active_scene_packet)
    setup_title = str(setup.get("title") or "").strip()
    active_title = packet_title if packet_title and setup_title.lower() in {"", "untitled scene"} else (setup_title or packet_title or "Scene")
    active_premise = str(setup.get("premise") or "").strip() or packet_premise
    active_setup = {**setup, "scene_id": active_scene_id, "title": active_title, "premise": active_premise, "scene_packet_id": active_scene_packet_id or setup.get("scene_packet_id") or ""}
    recent_turns = _history_turns_for_prompt(transcript, limit=12)
    system_sections = [
        "You are Neo Studio Roleplay Scene Engine.",
        "Write the next assistant reply for the active roleplay scene.",
        "Stay in character and preserve continuity. Do not mention backend systems, memory rows, runtime bundles, or this instruction unless the user explicitly asks out of character.",
        "Respect the Scene setup, Active Scene Packet Memory, runtime context, and memory context. If details conflict, prefer explicit Scene Packet rows and recent transcript.",
        "Do not invent physical descriptions, gender, relationship roles, locations, or scene facts that are not present in the packet/setup. If context is thin, say what is known and ask for the next beat.",
        "Never introduce sudden cheating/sexual betrayal, explicit sexual events, relationship reversals, injuries, or emotional states unless they are explicitly present in the user turn or active packet.",
        "Keep the reply useful for collaborative RP: vivid, direct, and easy for the user to answer.",
        "When user-controlled characters are present in the user turn, treat their supplied actions as input state only. Do not restate, expand, diagnose, move, emote, or describe them further.",
        "If a single assistant-controlled character is specified, reply through that character only. Do not add narrator analysis or report-card structure.",
        "Use names/pronouns/identity exactly as provided by packet/setup. If pronouns are not specified, use the character name instead of guessing.",
        "",
        f"Scene title: {active_title}",
        f"Premise: {_compact_text(active_premise, 1200)}",
        f"Tone: {setup.get('tone') or 'Scene-defined'}",
        f"Reply style: {setup.get('reply_style') or 'Scene-defined prose'}",
        f"Narrator posture: {setup.get('narrator_posture') or 'partner_focus'}",
        f"Continuity mode: {setup.get('continuity_mode') or 'runtime_anchored'}",
    ]
    continuation_context = transcript.get("continuation_context") if isinstance(transcript.get("continuation_context"), dict) else {}
    if continuation_context.get("summary"):
        system_sections.extend(["", "Continuation summary from previous session:", _compact_text(continuation_context.get("summary"), 3600)])
    control_override_lines = _scene_control_prompt_lines(scene_control_directives)
    if control_override_lines:
        system_sections.extend(["", *control_override_lines])
    if setup.get("scene_notes"):
        system_sections.append(f"Scene notes: {_compact_text(setup.get('scene_notes'), 1200)}")
    if setup.get("scene_rules"):
        system_sections.append(f"Scene rules: {_compact_text(setup.get('scene_rules'), 1200)}")
    runtime_lines = _runtime_context_lines(bundle)
    memory_lines = _memory_context_lines(retrieval)
    human_memory_packet = build_roleplay_human_scene_packet(setup=active_setup, transcript=transcript, user_message=user_message, runtime_bundle=bundle)
    human_memory_lines = human_memory_prompt_lines(human_memory_packet)
    memory_engine_enabled = bool(payload.get("enable_memory_engine") or payload.get("memory_engine"))
    if memory_engine_enabled:
        engine_memory_lines, engine_memory = _memory_engine_context_lines(user_message or active_premise or active_title or active_scene_id, limit=int(payload.get("memory_engine_limit") or 4))
    else:
        engine_memory_lines, engine_memory = [], {"status": "skipped", "reason": "memory_engine_disabled_scene_packet_first"}
    try:
        roleplay_control_context = roleplay_control_context_payload({
            **(payload or {}),
            "message": user_message,
            "scene_id": active_scene_id,
            "runtime_bundle_id": runtime_bundle_id,
            "scene_packet_id": active_scene_packet_id,
            "scene_title": active_title,
            "player_character_name": ", ".join(scene_control_directives.get("user_controls") or []) or payload.get("player_character_name") or setup.get("player_character_name") or "",
            "npc_character_ids": scene_control_directives.get("assistant_controls") or payload.get("npc_character_ids") or [],
            "metadata": {"source": "scene_turn_prompt", "control_center_injection": True, "scene_control_overrides": scene_control_directives},
        })
    except Exception as exc:
        roleplay_control_context = {"ok": False, "status": "roleplay_control_center_error", "error": str(exc), "prompt_block": ""}
    control_prompt_block = str(roleplay_control_context.get("prompt_block") or "").strip()
    try:
        scene_director_runtime = roleplay_scene_director_preflight_payload({
            **(payload or {}),
            "message": user_message,
            "scene_id": active_scene_id,
            "runtime_bundle_id": runtime_bundle_id,
            "scene_packet_id": active_scene_packet_id,
            "scene_title": active_title,
            "control_context": roleplay_control_context,
        })
    except Exception as exc:
        scene_director_runtime = {"ok": False, "status": "scene_director_error", "error": str(exc), "prompt_block": ""}
    director_prompt_block = str(scene_director_runtime.get("prompt_block") or "").strip()
    if control_prompt_block:
        system_sections.extend(["", control_prompt_block])
    else:
        system_sections.extend(["", "Roleplay Control Center Brief:", "Control Center context was not available. Fall back to Scene Packet and setup, and do not invent unspecified facts."])
    if director_prompt_block:
        system_sections.extend(["", director_prompt_block])
    else:
        system_sections.extend(["", "Roleplay Scene Director Runtime:", "Scene Director preflight was unavailable. Keep player-control boundaries, canon locks, and unknown-detail rules active."])
    scene_control_directives = _prioritize_assistant_controls_for_packet(scene_control_directives, active_scene_packet, bundle)
    scene_control_directives = _prioritize_assistant_controls_for_turn(scene_control_directives, user_message)
    identity_lock_lines = _scene_identity_lock_lines(active_scene_packet, scene_control_directives)
    if identity_lock_lines:
        system_sections.extend(["", *identity_lock_lines])
    control_safety_lines = _scene_control_safety_lines(user_message, payload or {}, setup, active_scene_packet, scene_control_directives)
    if control_safety_lines:
        system_sections.extend(["", *control_safety_lines])
    scene_packet_lines = scene_memory_injection.get("lines") or []
    quality_lines = _scene_quality_prompt_lines(scene_control_directives, scene_packet_lines)
    if quality_lines:
        system_sections.extend(["", *quality_lines])
    if scene_packet_lines:
        system_sections.extend(["", "Active Scene Packet Memory:", *scene_packet_lines])
    else:
        system_sections.extend(["", "Active Scene Packet Memory:", "No active Scene Packet was found. Use Scene setup, runtime context, and retrieved memory only; do not invent missing canon."])
    if runtime_lines:
        system_sections.extend(["", "Runtime context:", *runtime_lines])
    if memory_lines:
        system_sections.extend(["", "Relevant memory context:", *memory_lines])
    if human_memory_lines:
        system_sections.extend(["", "Human continuity memory:", *human_memory_lines])
    if engine_memory_lines:
        system_sections.extend(["", "Central Memory Engine context:", *engine_memory_lines])
    budgeted_system_sections, prompt_budget = _char_budget_lines(
        system_sections,
        max_chars=int(payload.get("prompt_context_max_chars") or 14000),
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": "\n".join(budgeted_system_sections).strip()}]
    for turn in recent_turns:
        role = str(turn.get("role") or "user")
        if role not in {"system", "assistant", "user"}:
            role = "assistant" if role in {"model", "npc"} else "user"
        if role == "system":
            role = "assistant"
        content = str(turn.get("text") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    if user_message:
        messages.append({"role": "user", "content": user_message})
    post_history_instruction = _scene_post_history_instruction(scene_control_directives)
    if post_history_instruction:
        messages.append({"role": "system", "content": post_history_instruction})
    prompt_result = {"schema_id": "neo.roleplay.scene.prompt.v1", "scene_id": scene_id, "active_scene_id": active_scene_id, "runtime_bundle_id": runtime_bundle_id, "messages": messages, "prompt_budget": prompt_budget, "retrieval": retrieval, "memory_engine": engine_memory, "human_memory": human_memory_packet, "roleplay_control_center": roleplay_control_context, "scene_director_runtime": scene_director_runtime, "scene_memory_injection": scene_memory_injection, "scene_packet": scene_memory_injection.get("scene_packet") or {}, "runtime_bundle": bundle or {}, "setup": active_setup, "raw_setup": setup, "transcript": transcript, "user_message": user_message, "scene_control_directives": scene_control_directives, "stop_sequences": _scene_stop_sequences(scene_control_directives)}
    _log_roleplay_runtime_event("roleplay.packet.build.completed", scene_id=scene_id, prompt=prompt_result, snapshot_name="neo_last_scene_packet")
    return prompt_result




def _handle_session_setup_chat_input(scene_id: str, user_message: str, prompt: dict[str, Any], transcript: dict[str, Any], user_turn: dict[str, Any]) -> dict[str, Any] | None:
    setup = prompt.get("setup") if isinstance(prompt.get("setup"), dict) else load_scene_setup(scene_id)
    bundle = prompt.get("runtime_bundle") if isinstance(prompt.get("runtime_bundle"), dict) else None
    packet = prompt.get("scene_packet") if isinstance(prompt.get("scene_packet"), dict) else {}
    roster = _scene_character_roster_from_packet(packet, bundle)
    packet_id = str(packet.get("scene_packet_id") or packet.get("packet_id") or setup.get("scene_packet_id") or setup.get("runtime_bundle_id") or "")
    status = _session_setup_status(setup, transcript, roster, packet_id)
    if status.get("played") or status.get("status") == "ready" or not status.get("needs_setup"):
        return None
    now = _now()
    # Roleplay setup is controlled by the dedicated UI card, not chat prompts.
    # A message typed before setup is complete should not be saved as story
    # content and should not create another transcript setup prompt. Return one
    # clean system notice only for this request.
    transcript["status"] = "session_setup"
    transcript["generation_enabled"] = False
    transcript["updated_at"] = now
    _write_json(_scene_transcript_path(scene_id), transcript)
    assistant_turn = {
        "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        "role": "system",
        "display_role": "Scene Setup",
        "text": "Complete the Scene setup card first, then start the Roleplay session.",
        "created_at": now,
        "status": "scene_session_setup_required",
        "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
        "scene_packet_id": packet_id,
    }
    return {"ok": True, "schema_id": "neo.roleplay.scene.turn.v1", "status": assistant_turn["status"], "scene_id": scene_id, "user_turn": {}, "assistant_turn": assistant_turn, "execution": {"status": assistant_turn["status"], "backend_skipped": True}, "prompt": {"schema_id": prompt.get("schema_id"), "message_count": len(prompt.get("messages") or []), "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": ""}, "retrieval": prompt.get("retrieval") or {}, "memory_engine": prompt.get("memory_engine") or {}, "human_memory": prompt.get("human_memory") or {}, "turn_writeback": {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "roleplay_control_center": prompt.get("roleplay_control_center") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": {"status": "skipped_session_setup", "warning_count": 0, "warnings": []}, "scene_packet": prompt.get("scene_packet") or {}, "prompt_budget": prompt.get("prompt_budget") or {}, "transcript": transcript}



def execute_scene_turn_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_scene_storage()
    prompt = build_scene_turn_prompt_payload(payload or {})
    scene_id = prompt["scene_id"]
    _log_roleplay_runtime_event("roleplay.scene_turn.started", scene_id=scene_id, prompt=prompt)
    user_message = prompt.get("user_message") or ""
    if not user_message:
        _log_roleplay_runtime_error("Scene turn message is required unless continue_scene is true.", scene_id=scene_id, prompt=prompt)
        raise ValueError("Scene turn message is required unless continue_scene is true.")
    transcript = load_scene_transcript(scene_id)
    now = _now()
    if not transcript.get("created_at"):
        transcript["created_at"] = now
    user_turn = {"turn_id": f"turn-{uuid.uuid4().hex[:10]}", "role": "user", "display_role": _active_user_display_name(transcript, payload or {}), "text": user_message, "created_at": now, "status": "submitted_live", "runtime_bundle_id": prompt.get("runtime_bundle_id") or ""}
    if _is_scene_control_directive_only(user_message):
        directives = _persist_scene_control_directive(transcript, prompt.get("scene_control_directives") or _scene_control_directives_from_text(user_message))
        assistant_turn = {
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            "role": "system",
            "text": _control_update_ack_text(directives),
            "created_at": _now(),
            "status": "scene_control_updated",
            "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
            "backend_profile_id": "",
            "retrieval_trace_id": "",
            "scene_packet_id": ((prompt.get("scene_packet") or {}).get("scene_packet_id") or (prompt.get("scene_packet") or {}).get("packet_id") or ""),
            "memory_injection_status": ((prompt.get("scene_memory_injection") or {}).get("status") or ""),
            "scene_director_validation_status": "skipped_control_update",
            "scene_director_warning_count": 0,
        }
        transcript.setdefault("turns", []).extend([user_turn, assistant_turn])
        transcript["updated_at"] = _now()
        transcript["status"] = "live"
        transcript["generation_enabled"] = True
        transcript["last_execution"] = {"ok": True, "status": "scene_control_updated", "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "scene_control_overrides": directives}
        _write_json(_scene_transcript_path(scene_id), transcript)
        result = {"ok": True, "schema_id": "neo.roleplay.scene.turn.v1", "status": "scene_control_updated", "scene_id": scene_id, "user_turn": user_turn, "assistant_turn": assistant_turn, "execution": {"status": "scene_control_updated", "backend_skipped": True}, "prompt": {"schema_id": prompt.get("schema_id"), "message_count": len(prompt.get("messages") or []), "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": ""}, "retrieval": prompt.get("retrieval") or {}, "memory_engine": prompt.get("memory_engine") or {}, "human_memory": prompt.get("human_memory") or {}, "turn_writeback": {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "roleplay_control_center": prompt.get("roleplay_control_center") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": {"status": "skipped_control_update", "warning_count": 0, "warnings": []}, "scene_packet": prompt.get("scene_packet") or {}, "prompt_budget": prompt.get("prompt_budget") or {}, "transcript": transcript}
        _log_roleplay_runtime_event("roleplay.scene_turn.completed", scene_id=scene_id, prompt=prompt, result=result, snapshot_name="neo_last_scene_turn")
        return result
    session_setup_result = _handle_session_setup_chat_input(scene_id, user_message, prompt, transcript, user_turn)
    if session_setup_result:
        _log_roleplay_runtime_event("roleplay.scene_turn.blocked", scene_id=scene_id, prompt=prompt, result=session_setup_result, level="WARNING", snapshot_name="neo_last_scene_turn")
        return session_setup_result
    exec_result = execute_roleplay_text_backend(
        profile_id=str((payload or {}).get("profile_id") or "") or None,
        messages=prompt["messages"],
        max_tokens=int((payload or {}).get("max_tokens") or 0) or 320,
        temperature=(payload or {}).get("temperature"),
        top_p=(payload or {}).get("top_p"),
        stop=prompt.get("stop_sequences") or [],
    )
    assistant_text = str(exec_result.get("text") or "").strip()
    if not assistant_text:
        assistant_text = f"[Scene generation failed: {exec_result.get('error') or exec_result.get('status') or 'unknown backend error'}]"
    grounding_text = _repair_grounding_text(prompt, user_message)
    identity_map = _scene_character_identity_map(prompt.get("scene_packet") or {})
    assistant_text, repair_warnings = _clean_streamed_scene_text(assistant_text, prompt.get("scene_control_directives") or {}, user_message, grounding_text, identity_map=identity_map)
    if _needs_scene_generation_retry(repair_warnings):
        retry_result = execute_roleplay_text_backend(
            profile_id=str((payload or {}).get("profile_id") or "") or None,
            messages=_scene_retry_messages(prompt, "guardrail_repair_empty"),
            max_tokens=int((payload or {}).get("max_tokens") or 0) or 320,
            temperature=(payload or {}).get("temperature"),
            top_p=(payload or {}).get("top_p"),
            stop=prompt.get("stop_sequences") or [],
        )
        retry_text = str(retry_result.get("text") or "").strip()
        retry_cleaned, retry_warnings = _clean_streamed_scene_text(retry_text, prompt.get("scene_control_directives") or {}, user_message, grounding_text, identity_map=identity_map)
        if retry_cleaned and not _needs_scene_generation_retry(retry_warnings) and not _is_guardrail_block_text(retry_cleaned) and not _has_scene_status_leak(retry_cleaned):
            assistant_text = retry_cleaned
            exec_result = retry_result
            repair_warnings.append({"code": "backend_regenerated_guarded_turn", "severity": "medium", "message": "Regenerated the scene turn after guardrail repair removed the prior draft."})
            repair_warnings.extend(retry_warnings)
        else:
            assistant_text = SCENE_TURN_BLOCKED_TEXT
            exec_result = {**exec_result, "ok": False, "status": "scene_turn_blocked"}
            repair_warnings.extend(retry_warnings)
            repair_warnings.append({"code": "scene_turn_blocked_after_retry", "severity": "high", "message": "Retry also failed guardrails; no roleplay fallback prose was inserted."})
    director_validation = roleplay_scene_director_validate_payload({
        "assistant_text": assistant_text,
        "control_context": prompt.get("roleplay_control_center") or {},
        "prompt_block": (prompt.get("scene_director_runtime") or {}).get("prompt_block") or "",
        "intent": ((prompt.get("roleplay_control_center") or {}).get("plan") or {}).get("intent") if isinstance((prompt.get("roleplay_control_center") or {}).get("plan"), dict) else "",
    })
    if repair_warnings:
        director_validation.setdefault("warnings", []).extend(repair_warnings)
        director_validation["warning_count"] = len(director_validation.get("warnings") or [])
        if any(w.get("severity") == "high" for w in repair_warnings):
            director_validation["status"] = "repaired"
    display_role = _assistant_speaker_from_text(assistant_text, prompt.get("scene_control_directives") or {}) or _assistant_display_name(transcript, prompt.get("scene_control_directives") or {})
    if exec_result.get("ok"):
        assistant_text, _ = _strip_leading_assistant_speaker_label(assistant_text, display_role)
    assistant_turn = {
        "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        "role": "assistant" if exec_result.get("ok") else "system",
        "display_role": display_role if exec_result.get("ok") else "System",
        "text": assistant_text,
        "created_at": _now(),
        "status": "generated" if exec_result.get("ok") else ("scene_turn_blocked" if _is_guardrail_block_text(assistant_text) else "generation_error"),
        "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
        "backend_profile_id": exec_result.get("active_profile_id") or (exec_result.get("bridge") or {}).get("active_profile_id") or "",
        "retrieval_trace_id": ((prompt.get("retrieval") or {}).get("search") or {}).get("trace_id") or "",
        "scene_packet_id": ((prompt.get("scene_packet") or {}).get("scene_packet_id") or (prompt.get("scene_packet") or {}).get("packet_id") or ""),
        "memory_injection_status": ((prompt.get("scene_memory_injection") or {}).get("status") or ""),
        "scene_director_validation_status": director_validation.get("status") or "",
        "scene_director_warning_count": director_validation.get("warning_count") or 0,
    }
    transcript = _replace_pending_stream_user_turn(transcript, user_turn)
    suggested_actions = _attach_choice_assist_to_transcript(payload=payload or {}, setup=prompt.get("setup") or {}, transcript=transcript, assistant_turn=assistant_turn, assistant_text=assistant_text) if exec_result.get("ok") else []
    transcript.setdefault("turns", []).append(assistant_turn)
    transcript["updated_at"] = _now()
    transcript["status"] = "live" if exec_result.get("ok") else "generation_error"
    transcript["generation_enabled"] = True
    transcript["last_execution"] = {"ok": bool(exec_result.get("ok")), "status": exec_result.get("status") or "unknown", "active_profile_id": assistant_turn.get("backend_profile_id") or "", "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": assistant_turn.get("retrieval_trace_id") or "", "scene_packet_id": assistant_turn.get("scene_packet_id") or "", "memory_injection_status": assistant_turn.get("memory_injection_status") or "", "error": exec_result.get("error") or "", "scene_director_validation": director_validation}
    _write_json(_scene_transcript_path(scene_id), transcript)
    try:
        transcript["memory_link"] = upsert_scene_turn_memory(scene_id, transcript.get("turns") or [])
    except Exception as exc:
        transcript["memory_link"] = {"status": "error", "error": str(exc)}
    try:
        transcript["human_memory_link"] = sync_scene_human_memory(prompt.get("setup") or {}, transcript, user_message=user_message, runtime_bundle=prompt.get("runtime_bundle") or {})
    except Exception as exc:
        transcript["human_memory_link"] = {"status": "error", "error": str(exc)}
    try:
        transcript["turn_writeback"] = writeback_scene_turn(scene_id=str(prompt.get("active_scene_id") or scene_id), setup=prompt.get("setup") or {}, user_turn=user_turn, assistant_turn=assistant_turn, prompt=prompt, transcript=transcript)
    except Exception as exc:
        transcript["turn_writeback"] = {"status": "error", "error": str(exc)}
    _write_json(_scene_transcript_path(scene_id), transcript)
    result = {"ok": bool(exec_result.get("ok")), "schema_id": "neo.roleplay.scene.turn.v1", "status": "generated" if exec_result.get("ok") else ("scene_turn_blocked" if _is_guardrail_block_text(assistant_text) else "generation_error"), "scene_id": scene_id, "user_turn": user_turn, "assistant_turn": assistant_turn, "execution": {k: v for k, v in exec_result.items() if k != "response"}, "prompt": {"schema_id": prompt.get("schema_id"), "message_count": len(prompt.get("messages") or []), "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": assistant_turn.get("retrieval_trace_id") or ""}, "retrieval": prompt.get("retrieval") or {}, "memory_engine": prompt.get("memory_engine") or {}, "human_memory": prompt.get("human_memory") or {}, "turn_writeback": transcript.get("turn_writeback") or {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "roleplay_control_center": prompt.get("roleplay_control_center") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": director_validation, "scene_packet": prompt.get("scene_packet") or {}, "prompt_budget": prompt.get("prompt_budget") or {}, "turn_input_style": _normalize_turn_input_style((payload or {}).get("turn_input_style") or (prompt.get("setup") or {}).get("turn_input_style")), "suggested_actions": suggested_actions, "suggested_actions_warning": "", "transcript": transcript}
    _log_roleplay_runtime_event("roleplay.scene_turn.completed" if result.get("ok") else "roleplay.scene_turn.blocked", scene_id=scene_id, prompt=prompt, result=result, level="INFO" if result.get("ok") else "WARNING", snapshot_name="neo_last_scene_turn")
    return result




def stream_scene_turn_event_dicts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Execute a Scene turn and emit streaming event dictionaries.

    The transcript is persisted at the end of the stream. If generation fails
    after the user turn is accepted, an error/system turn is written so the UI can
    recover instead of losing context.
    """
    ensure_scene_storage()
    raw_payload = payload or {}
    initial_scene_id = str(raw_payload.get("scene_id") or "default")
    yield {"type": "status", "status": "preparing_prompt", "scene_id": initial_scene_id, "message": "Preparing Scene Packet prompt…"}
    prompt = build_scene_turn_prompt_payload(raw_payload)
    scene_id = prompt["scene_id"]
    user_message = prompt.get("user_message") or ""
    if not user_message:
        yield {"type": "error", "status": "missing_message", "error": "Scene turn message is required unless continue_scene is true.", "scene_id": scene_id}
        return

    transcript = load_scene_transcript(scene_id)
    now = _now()
    if not transcript.get("created_at"):
        transcript["created_at"] = now
    user_turn = {
        "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        "role": "user",
        "display_role": _active_user_display_name(transcript, payload or {}),
        "text": user_message,
        "created_at": now,
        "status": "submitted_stream",
        "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
    }
    yield {"type": "start", "schema_id": "neo.roleplay.scene.turn_stream.v1", "scene_id": scene_id, "user_turn": user_turn, "prompt": {"message_count": len(prompt.get("messages") or []), "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": ((prompt.get("retrieval") or {}).get("search") or {}).get("trace_id") or "", "scene_packet_id": ((prompt.get("scene_packet") or {}).get("scene_packet_id") or (prompt.get("scene_packet") or {}).get("packet_id") or ""), "memory_injection_status": ((prompt.get("scene_memory_injection") or {}).get("status") or ""), "prompt_budget": prompt.get("prompt_budget") or {}}}
    yield {"type": "user_turn", "scene_id": scene_id, "turn": user_turn}
    if _is_scene_control_directive_only(user_message):
        directives = _persist_scene_control_directive(transcript, prompt.get("scene_control_directives") or _scene_control_directives_from_text(user_message))
        assistant_turn = {
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            "role": "system",
            "text": _control_update_ack_text(directives),
            "created_at": _now(),
            "status": "scene_control_updated",
            "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
            "backend_profile_id": "",
            "retrieval_trace_id": "",
            "scene_packet_id": ((prompt.get("scene_packet") or {}).get("scene_packet_id") or (prompt.get("scene_packet") or {}).get("packet_id") or ""),
            "memory_injection_status": ((prompt.get("scene_memory_injection") or {}).get("status") or ""),
            "scene_director_validation_status": "skipped_control_update",
            "scene_director_warning_count": 0,
            "streaming": False,
        }
        transcript.setdefault("turns", []).extend([user_turn, assistant_turn])
        transcript["updated_at"] = _now()
        transcript["status"] = "live"
        transcript["generation_enabled"] = True
        transcript["last_execution"] = {"ok": True, "status": "scene_control_updated", "streaming": False, "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "scene_control_overrides": directives}
        _write_json(_scene_transcript_path(scene_id), transcript)
        yield {"type": "done", "ok": True, "schema_id": "neo.roleplay.scene.turn_stream.v1", "status": "scene_control_updated", "scene_id": scene_id, "assistant_turn": assistant_turn, "transcript": transcript, "execution": {"status": "scene_control_updated", "backend_skipped": True}, "turn_writeback": {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": {"status": "skipped_control_update", "warning_count": 0, "warnings": []}, "scene_packet": prompt.get("scene_packet") or {}}
        return
    session_setup_result = _handle_session_setup_chat_input(scene_id, user_message, prompt, transcript, user_turn)
    if session_setup_result:
        assistant_turn = session_setup_result.get("assistant_turn") or {}
        yield {"type": "token", "scene_id": scene_id, "text": assistant_turn.get("text") or ""}
        yield {"type": "done", "ok": True, "schema_id": "neo.roleplay.scene.turn_stream.v1", "status": session_setup_result.get("status") or "scene_session_setup", "scene_id": scene_id, "assistant_turn": assistant_turn, "transcript": session_setup_result.get("transcript") or transcript, "execution": {"status": session_setup_result.get("status") or "scene_session_setup", "backend_skipped": True}, "turn_writeback": {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": {"status": "skipped_session_setup", "warning_count": 0, "warnings": []}, "scene_packet": prompt.get("scene_packet") or {}}
        return

    transcript = _persist_pending_stream_user_turn(scene_id, transcript, user_turn)

    assistant_text_parts: list[str] = []
    backend_status = "streaming"
    backend_error = ""
    backend_profile_id = ""
    backend_meta: dict[str, Any] = {}
    for event in execute_roleplay_text_backend_stream(
        profile_id=str((payload or {}).get("profile_id") or "") or None,
        messages=prompt["messages"],
        max_tokens=int((payload or {}).get("max_tokens") or 0) or 320,
        temperature=(payload or {}).get("temperature"),
        top_p=(payload or {}).get("top_p"),
        stop=prompt.get("stop_sequences") or [],
    ):
        event_type = str(event.get("type") or "")
        if event_type == "backend_start":
            backend_profile_id = str(event.get("active_profile_id") or "")
            backend_meta = {k: v for k, v in event.items() if k != "type"}
            yield {"type": "backend_start", "scene_id": scene_id, **backend_meta}
        elif event_type == "token":
            token = str(event.get("text") or "")
            if token:
                assistant_text_parts.append(token)
        elif event_type == "backend_done":
            backend_status = str(event.get("status") or "stream_complete")
            backend_profile_id = str(event.get("active_profile_id") or backend_profile_id or "")
            backend_meta.update({k: v for k, v in event.items() if k != "type"})
        elif event_type == "error":
            backend_status = str(event.get("status") or "stream_error")
            backend_error = str(event.get("error") or "Unknown streaming backend error.")
            backend_profile_id = str(event.get("active_profile_id") or backend_profile_id or "")
            backend_meta.update({k: v for k, v in event.items() if k != "type"})
            yield {"type": "error", "scene_id": scene_id, "status": backend_status, "error": backend_error, "scene_director_validation": director_validation}
            break

    assistant_text = "".join(assistant_text_parts).strip()
    ok = bool(assistant_text) and not backend_error
    if not assistant_text:
        assistant_text = f"[Scene streaming failed: {backend_error or backend_status or 'empty backend response'}]"
    grounding_text = _repair_grounding_text(prompt, user_message)
    identity_map = _scene_character_identity_map(prompt.get("scene_packet") or {})
    assistant_text, repair_warnings = _clean_streamed_scene_text(assistant_text, prompt.get("scene_control_directives") or {}, user_message, grounding_text, identity_map=identity_map)
    if _needs_scene_generation_retry(repair_warnings):
        retry_parts: list[str] = []
        retry_status = "streaming_retry"
        retry_error = ""
        for retry_event in execute_roleplay_text_backend_stream(
            profile_id=str((payload or {}).get("profile_id") or "") or None,
            messages=_scene_retry_messages(prompt, "guardrail_repair_empty"),
            max_tokens=int((payload or {}).get("max_tokens") or 0) or 320,
            temperature=(payload or {}).get("temperature"),
            top_p=(payload or {}).get("top_p"),
            stop=prompt.get("stop_sequences") or [],
        ):
            retry_type = str(retry_event.get("type") or "")
            if retry_type == "token":
                retry_parts.append(str(retry_event.get("text") or ""))
            elif retry_type == "backend_done":
                retry_status = str(retry_event.get("status") or retry_status)
            elif retry_type == "error":
                retry_status = str(retry_event.get("status") or "stream_retry_error")
                retry_error = str(retry_event.get("error") or "Unknown retry backend error.")
                break
        retry_text = "".join(retry_parts).strip()
        retry_cleaned, retry_warnings = _clean_streamed_scene_text(retry_text, prompt.get("scene_control_directives") or {}, user_message, grounding_text, identity_map=identity_map)
        if retry_cleaned and not _needs_scene_generation_retry(retry_warnings) and not _is_guardrail_block_text(retry_cleaned) and not _has_scene_status_leak(retry_cleaned):
            assistant_text = retry_cleaned
            backend_status = retry_status or backend_status
            backend_error = retry_error or backend_error
            repair_warnings.append({"code": "backend_regenerated_guarded_turn", "severity": "medium", "message": "Regenerated the scene turn after guardrail repair removed the prior draft."})
            repair_warnings.extend(retry_warnings)
        else:
            assistant_text = SCENE_TURN_BLOCKED_TEXT
            backend_status = "scene_turn_blocked"
            backend_error = retry_error or backend_error
            repair_warnings.extend(retry_warnings)
            repair_warnings.append({"code": "scene_turn_blocked_after_retry", "severity": "high", "message": "Retry also failed guardrails; no roleplay fallback prose was inserted."})
    blocked_by_guardrail = _is_guardrail_block_text(assistant_text)
    ok = bool(assistant_text) and not backend_error and not blocked_by_guardrail
    display_role = _assistant_speaker_from_text(assistant_text, prompt.get("scene_control_directives") or {}) or _assistant_display_name(transcript, prompt.get("scene_control_directives") or {})
    if ok:
        assistant_text, _ = _strip_leading_assistant_speaker_label(assistant_text, display_role)
    if assistant_text:
        yield {"type": "token", "scene_id": scene_id, "text": assistant_text}
    director_validation = roleplay_scene_director_validate_payload({
        "assistant_text": assistant_text,
        "control_context": prompt.get("roleplay_control_center") or {},
        "prompt_block": (prompt.get("scene_director_runtime") or {}).get("prompt_block") or "",
        "intent": ((prompt.get("roleplay_control_center") or {}).get("plan") or {}).get("intent") if isinstance((prompt.get("roleplay_control_center") or {}).get("plan"), dict) else "",
    })
    if repair_warnings:
        director_validation.setdefault("warnings", []).extend(repair_warnings)
        director_validation["warning_count"] = len(director_validation.get("warnings") or [])
        if any(w.get("severity") == "high" for w in repair_warnings):
            director_validation["status"] = "repaired"
    assistant_turn = {
        "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        "role": "assistant" if ok else "system",
        "display_role": display_role if ok else "System",
        "text": assistant_text,
        "created_at": _now(),
        "status": "streamed" if ok else ("scene_turn_blocked" if _is_guardrail_block_text(assistant_text) else "stream_error"),
        "runtime_bundle_id": prompt.get("runtime_bundle_id") or "",
        "backend_profile_id": backend_profile_id,
        "retrieval_trace_id": ((prompt.get("retrieval") or {}).get("search") or {}).get("trace_id") or "",
        "scene_packet_id": ((prompt.get("scene_packet") or {}).get("scene_packet_id") or (prompt.get("scene_packet") or {}).get("packet_id") or ""),
        "memory_injection_status": ((prompt.get("scene_memory_injection") or {}).get("status") or ""),
        "scene_director_validation_status": director_validation.get("status") or "",
        "scene_director_warning_count": director_validation.get("warning_count") or 0,
        "streaming": False,
    }
    transcript = _replace_pending_stream_user_turn(transcript, user_turn)
    suggested_actions = _attach_choice_assist_to_transcript(payload=payload or {}, setup=prompt.get("setup") or {}, transcript=transcript, assistant_turn=assistant_turn, assistant_text=assistant_text) if ok else []
    transcript.setdefault("turns", []).append(assistant_turn)
    transcript["updated_at"] = _now()
    transcript["status"] = "live" if ok else "generation_error"
    transcript["generation_enabled"] = True
    transcript["last_execution"] = {"ok": ok, "status": backend_status, "streaming": False, "active_profile_id": backend_profile_id, "runtime_bundle_id": prompt.get("runtime_bundle_id") or "", "retrieval_trace_id": assistant_turn.get("retrieval_trace_id") or "", "scene_packet_id": assistant_turn.get("scene_packet_id") or "", "memory_injection_status": assistant_turn.get("memory_injection_status") or "", "error": backend_error, "scene_director_validation": director_validation}
    _write_json(_scene_transcript_path(scene_id), transcript)
    try:
        transcript["memory_link"] = upsert_scene_turn_memory(scene_id, transcript.get("turns") or [])
    except Exception as exc:
        transcript["memory_link"] = {"status": "error", "error": str(exc)}
    try:
        transcript["human_memory_link"] = sync_scene_human_memory(prompt.get("setup") or {}, transcript, user_message=user_message, runtime_bundle=prompt.get("runtime_bundle") or {})
    except Exception as exc:
        transcript["human_memory_link"] = {"status": "error", "error": str(exc)}
    try:
        transcript["turn_writeback"] = writeback_scene_turn(scene_id=str(prompt.get("active_scene_id") or scene_id), setup=prompt.get("setup") or {}, user_turn=user_turn, assistant_turn=assistant_turn, prompt=prompt, transcript=transcript)
    except Exception as exc:
        transcript["turn_writeback"] = {"status": "error", "error": str(exc)}
    _write_json(_scene_transcript_path(scene_id), transcript)
    yield {"type": "done", "ok": ok, "schema_id": "neo.roleplay.scene.turn_stream.v1", "status": "streamed" if ok else "stream_error", "scene_id": scene_id, "assistant_turn": assistant_turn, "transcript": transcript, "execution": backend_meta, "turn_writeback": transcript.get("turn_writeback") or {}, "scene_memory_injection": prompt.get("scene_memory_injection") or {}, "scene_director_runtime": prompt.get("scene_director_runtime") or {}, "scene_director_validation": director_validation, "scene_packet": prompt.get("scene_packet") or {}, "turn_input_style": _normalize_turn_input_style((payload or {}).get("turn_input_style") or (prompt.get("setup") or {}).get("turn_input_style")), "suggested_actions": suggested_actions, "suggested_actions_warning": ""}


def append_scene_transcript_placeholder(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_scene_storage()
    scene_id = str(payload.get("scene_id") or "default")
    message = str(payload.get("message") or payload.get("user_turn") or "").strip()
    transcript = load_scene_transcript(scene_id)
    now = _now()
    if not transcript.get("created_at"):
        transcript["created_at"] = now
    if message:
        transcript.setdefault("turns", []).append({
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            "role": "user",
            "text": message,
            "created_at": now,
            "status": "captured_placeholder",
        })
        transcript.setdefault("turns", []).append({
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            "role": "system",
            "text": "Scene generation is deferred. This placeholder confirms the transcript capture path only.",
            "created_at": now,
            "status": "generation_deferred",
        })
    transcript["updated_at"] = now
    transcript["status"] = "placeholder"
    transcript["generation_enabled"] = False
    _write_json(_scene_transcript_path(scene_id), transcript)
    try:
        transcript["memory_link"] = upsert_scene_turn_memory(scene_id, transcript.get("turns") or [])
    except Exception as exc:
        transcript["memory_link"] = {"status": "error", "error": str(exc)}
    return transcript


def clear_scene_transcript_payload(scene_id: str = "default") -> dict[str, Any]:
    ensure_scene_storage()
    now = _now()
    transcript = {
        "scene_id": scene_id,
        "status": "placeholder",
        "generation_enabled": False,
        "turns": [],
        "storage_path": _relative_to_root(_scene_transcript_path(scene_id)),
        "created_at": now,
        "updated_at": now,
    }
    _write_json(_scene_transcript_path(scene_id), transcript)
    try:
        transcript["runtime_memory_reset"] = archive_scene_runtime_writebacks(scene_id)
    except Exception as exc:
        transcript["runtime_memory_reset"] = {"status": "error", "error": str(exc)}
    return transcript


def scene_state_payload(profile_id: str | None = None, scene_id: str = "default") -> dict[str, Any]:
    ensure_scene_storage()
    bridge = resolve_roleplay_text_backend(profile_id)
    setup = load_scene_setup(scene_id)
    transcript = load_scene_transcript(scene_id)
    runtime_options = _runtime_bundle_options()
    sqlite_state = roleplay_sqlite_state_payload()
    selected_runtime_bundle_id = str(setup.get("runtime_bundle_id") or "")
    scene_injection_state = scene_memory_injection_state_payload(scene_id=scene_id, scene_packet_id=setup.get("scene_packet_id") or selected_runtime_bundle_id, scope_id=setup.get("memory_scope") or selected_runtime_bundle_id or scene_id)
    active_bundle = get_runtime_bundle(selected_runtime_bundle_id) if selected_runtime_bundle_id else {}
    active_packet = _active_scene_packet_for_setup(setup, active_bundle)
    character_roster = _scene_character_roster_from_packet(active_packet, active_bundle)
    active_packet_id = str(active_packet.get("scene_packet_id") or active_packet.get("packet_id") or setup.get("scene_packet_id") or selected_runtime_bundle_id or "")
    session_setup_state = _session_setup_status(setup, transcript, character_roster, active_packet_id)
    if session_setup_state.get("needs_setup"):
        # Scene session setup is now owned by the Roleplay UI setup card.
        # Do not append setup prompts into the story transcript; those prompts
        # polluted chat history and could leak into backend generation. The UI
        # reads session_setup_state and renders the card outside transcript.
        transcript["status"] = "session_setup"
        transcript["generation_enabled"] = False
    context_budget = _estimate_scene_context_status(setup, transcript, bridge, scene_injection_state)
    turn_writeback_state = turn_writeback_state_payload(scene_id=scene_id, limit=12)
    return {
        "schema_id": "neo.roleplay.scene.v1",
        "version": "1.0.0-scene-execution",
        "surface_id": "roleplay",
        "tab_id": "scene",
        "status": "active",
        "ready": bool(bridge.get("ready")),
        "active_view": "setup",
        "scene_id": scene_id,
        "setup": {
            "status": "ready",
            "scene_id": setup.get("scene_id") or scene_id,
            "title": setup.get("title") or "Untitled Scene",
            "runtime_bundle_id": selected_runtime_bundle_id,
            "scene_packet_id": setup.get("scene_packet_id") or "",
            "runtime_bundle_selection": "active" if selected_runtime_bundle_id else "available",
            "runtime_bundle_options": runtime_options,
            "runtime_bundle_selectable": bool(runtime_options),
            "premise": setup.get("premise") or "",
            "tone": setup.get("tone") or "Scene-defined",
            "reply_style": setup.get("reply_style") or "Scene-defined prose",
            "scene_notes": setup.get("scene_notes") or "",
            "narrator_posture": setup.get("narrator_posture") or "partner_focus",
            "continuity_mode": setup.get("continuity_mode") or "runtime_anchored",
            "autosave_checkpoint": bool(setup.get("autosave_checkpoint") or False),
            "turn_input_style": setup.get("turn_input_style") or "free_typing",
            "participants": setup.get("participants") or "",
            "memory_scope": setup.get("memory_scope") or "roleplay.scene",
            "scene_rules": setup.get("scene_rules") or "",
            "text_backend_ready": bool(bridge.get("ready")),
            "active_profile_id": bridge.get("active_profile_id") or "",
            "storage_path": setup.get("storage_path") or _relative_to_root(_scene_setup_path(scene_id)),
        },
        "chat": {
            "status": transcript.get("status") or "live_ready",
            "generation_enabled": bool(bridge.get("ready")),
            "execution_enabled": bool(bridge.get("ready")),
            "reason": "Scene turn execution is active through the shared text backend. Streaming and non-streaming turns are available; retrieval can use keyword or semantic search from Roleplay retrieval endpoints.",
            "transcript": transcript,
            "turn_count": len(transcript.get("turns") or []),
            "storage_path": transcript.get("storage_path") or _relative_to_root(_scene_transcript_path(scene_id)),
        },
        "memory_link": {
            "status": "active",
            "sqlite_ready": bool(sqlite_state.get("ready")),
            "table_counts": sqlite_state.get("table_counts") or {},
            "last_setup_link": setup.get("memory_link") or {},
            "last_transcript_link": transcript.get("memory_link") or {},
            "writeback_enabled": True,
            "vector_indexing_enabled": True,
        },
        "runtime_bundle_selector": {
            "status": "bound" if selected_runtime_bundle_id else "ready",
            "selected_bundle_id": selected_runtime_bundle_id,
            "options": runtime_options,
            "option_count": len(runtime_options),
            "binding_enabled": bool(runtime_options),
            "binding_available": bool(runtime_options),
            "active_bundle": active_bundle,
            "active_summary": "No Scene packet is active yet." if not selected_runtime_bundle_id else f"Selected Scene packet: {selected_runtime_bundle_id}",
            "state_summary": f"Posture · {setup.get('narrator_posture') or 'partner_focus'}  Continuity · {setup.get('continuity_mode') or 'runtime_anchored'}  Cast ids · {len([x for x in str(setup.get('participants') or '').split(',') if x.strip()])}",
            "continuity_summary": "Focus stack · none",
            "memory_preview": f"Memory link · continuity rows {int((sqlite_state.get('table_counts') or {}).get('rp_continuity_rows') or 0)} · turn summaries {int((sqlite_state.get('table_counts') or {}).get('rp_turn_summaries') or 0)}",
            "reason": "Runtime bundle compile is active. Scene execution can use the selected bundle; semantic retrieval is available through the Roleplay retrieval engine.",
        },
        "scene_memory_injection": scene_injection_state,
        "session_setup": session_setup_state,
        "context_budget": context_budget,
        "turn_writeback": turn_writeback_state,
        "text_backend": bridge,
        "active_features": [
            "non_streaming_scene_turn_execution",
            "streaming_scene_turn_execution",
            "runtime_bundle_binding",
            "checkpoint_save",
            "checkpoint_restore",
            "story_resume",
            "keyword_retrieval_context",
            "scene_packet_memory_injection",
            "memory_injection_proof",
            "turn_writeback_continuity",
        ],
        "deferred_features": [
            "long_context_summarization",
            "continuity_contradiction_resolution",
        ],
    }
