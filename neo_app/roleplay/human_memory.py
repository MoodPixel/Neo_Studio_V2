from __future__ import annotations

import re
from typing import Any

from neo_app.roleplay.sqlite_store import (
    ensure_roleplay_memory_schema,
    get_roleplay_human_scene_packet,
    roleplay_human_memory_rows,
    roleplay_sqlite_state_payload,
    upsert_roleplay_human_scene_packet,
)

HUMAN_MEMORY_SCHEMA_ID = "neo.roleplay.human_memory.v1"
HUMAN_MEMORY_VERSION = "1.0.0-human-roleplay-memory"

_EMOTION_KEYWORDS = {
    "tender": ["soft", "gentle", "warm", "care", "comfort", "close", "safe"],
    "tense": ["argue", "anger", "angry", "fight", "threat", "fear", "panic", "danger", "cold"],
    "romantic_tension": ["kiss", "touch", "blush", "desire", "longing", "jealous", "bond", "mate"],
    "melancholy": ["sad", "grief", "hurt", "loss", "lonely", "miss", "broken"],
    "mystery": ["secret", "unknown", "hidden", "whisper", "shadow", "suspect"],
    "resolve": ["promise", "protect", "trust", "stay", "choose", "vow"],
}




def _is_polluted_roleplay_text(value: Any) -> bool:
    text = str(value or "")
    if not text.strip():
        return False
    patterns = (
        r"\[\s*End\s+Scene\s*\]",
        r"\bthe\s+scene\s+ended\b",
        r"\banother\s+session\b",
        r"(?:^|\n|\s)(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}(?:['’]s)?\s+Response\s*(?:\*\*)?:",
        r"(?:^|\n|\s)(?:#{1,6}\s*)?(?:Next\s+Beat|Summary|Assistant\s+turn|Scene\s+input)\s*:",
        r"\[\s*content\s+redacted",
        r"\bthe\s+scene\s+referenc(?:es|ed)\s+\w+\b",
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
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def _compact(value: Any, limit: int = 800) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_people(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;/]+|\s+and\s+|\s+&\s+", text)
    names = []
    for part in parts:
        clean = part.strip(" -•\t")
        if clean and clean.lower() not in {"user", "assistant", "npc", "narrator"}:
            names.append(clean[:80])
    return list(dict.fromkeys(names))[:12]


def infer_emotional_tone(*texts: Any) -> dict[str, Any]:
    joined = "\n".join(str(text or "") for text in texts).lower()
    scores: dict[str, int] = {}
    for tone, words in _EMOTION_KEYWORDS.items():
        scores[tone] = sum(joined.count(word) for word in words)
    top = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    label = top[0][0] if top and top[0][1] > 0 else "steady_continuity"
    return {
        "label": label,
        "scores": scores,
        "confidence": min(1.0, round((top[0][1] if top else 0) / 8.0, 3)),
    }


def extract_unresolved_threads(text: str, *, scene_id: str = "default") -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    triggers = ("?", "promise", "secret", "later", "must", "need to", "find", "remember", "don’t forget", "don't forget", "unresolved", "why")
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) < 12:
            continue
        lower = clean.lower()
        if any(trigger in lower for trigger in triggers):
            threads.append({
                "title": _compact(clean, 90),
                "content": _compact(clean, 360),
                "thread_type": "question" if "?" in clean else "open_hook",
                "priority": "high" if any(word in lower for word in ("secret", "promise", "must", "remember")) else "normal",
                "status": "open",
                "source_scene_id": scene_id,
            })
    return threads[:12]


def extract_canon_locks(setup: dict[str, Any], runtime_bundle: dict[str, Any] | None = None) -> list[str]:
    locks: list[str] = []
    if setup.get("premise"):
        locks.append(f"Premise: {_compact(setup.get('premise'), 240)}")
    if setup.get("scene_rules"):
        locks.append(f"Scene rule: {_compact(setup.get('scene_rules'), 240)}")
    if setup.get("continuity_mode"):
        locks.append(f"Continuity mode: {setup.get('continuity_mode')}")
    bundle = runtime_bundle if isinstance(runtime_bundle, dict) else {}
    scene_packet = bundle.get("scene_packet") if isinstance(bundle.get("scene_packet"), dict) else {}
    if scene_packet.get("summary"):
        locks.append(f"Runtime summary: {_compact(scene_packet.get('summary'), 280)}")
    return locks[:12]


def build_roleplay_human_scene_packet(*, setup: dict[str, Any], transcript: dict[str, Any], user_message: str = "", runtime_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    scene_id = str(setup.get("scene_id") or transcript.get("scene_id") or "default")
    recent_turns = []
    for turn in (transcript.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        status = str(turn.get("status") or "")
        text = str(turn.get("text") or "")
        if status in {"scene_turn_blocked", "guardrail_blocked", "stream_guardrail_blocked", "scene_control_updated", "scene_session_setup_ready", "scene_session_setup_required"}:
            continue
        if _is_polluted_roleplay_text(text):
            continue
        recent_turns.append(turn)
    recent_turns = recent_turns[-12:]
    recent_text = "\n".join(str(turn.get("text") or "") for turn in recent_turns)
    tone = infer_emotional_tone(setup.get("tone"), setup.get("premise"), setup.get("scene_notes"), recent_text, user_message)
    participants = _split_people(setup.get("participants"))
    character_knowledge = []
    if participants:
        for name in participants:
            character_knowledge.append({
                "character_id": name,
                "subject_id": scene_id,
                "knowledge_type": "scene_participant",
                "content": f"{name} is active in scene '{setup.get('title') or scene_id}'.",
                "visibility": "scene_visible",
                "canon_status": "draft",
            })
    if setup.get("premise"):
        character_knowledge.append({
            "character_id": "scene",
            "subject_id": scene_id,
            "knowledge_type": "premise_awareness",
            "content": _compact(setup.get("premise"), 500),
            "visibility": "scene_visible",
            "canon_status": "draft",
        })
    combined = "\n".join([str(setup.get("scene_notes") or ""), recent_text, user_message])
    unresolved = extract_unresolved_threads(combined, scene_id=scene_id)
    packet = {
        "schema_id": HUMAN_MEMORY_SCHEMA_ID,
        "version": HUMAN_MEMORY_VERSION,
        "scene_id": scene_id,
        "scope_id": str(setup.get("runtime_bundle_id") or setup.get("memory_scope") or scene_id),
        "runtime_bundle_id": str(setup.get("runtime_bundle_id") or ""),
        "title": str(setup.get("title") or "Untitled Scene"),
        "emotional_tone": tone["label"],
        "emotional_vector": tone,
        "relationship_state": {
            "participants": participants,
            "state_label": tone["label"],
            "confidence": tone["confidence"],
            "basis": "scene setup + recent transcript + current input",
        },
        "character_knowledge": character_knowledge,
        "canon_locks": extract_canon_locks(setup, runtime_bundle),
        "unresolved_threads": unresolved,
        "continuity_warnings": [],
        "active_goals": ["preserve scene continuity", "respect character knowledge boundaries", "carry unresolved hooks forward"],
        "boundaries": ["do not reveal secrets a character should not know", "prefer recent transcript over older memory when conflicts appear"],
        "recent_turn_count": len(recent_turns),
    }
    return packet


def human_memory_prompt_lines(packet: dict[str, Any]) -> list[str]:
    if not isinstance(packet, dict) or not packet:
        return []
    lines = [
        f"Human memory tone: {packet.get('emotional_tone') or 'steady_continuity'}",
        "Memory rule: preserve character knowledge boundaries; do not let every character know every secret by default.",
    ]
    participants = ((packet.get("relationship_state") or {}).get("participants") or []) if isinstance(packet.get("relationship_state"), dict) else []
    if participants:
        lines.append("Active participants: " + ", ".join(str(item) for item in participants[:8]))
    canon = packet.get("canon_locks") if isinstance(packet.get("canon_locks"), list) else []
    if canon:
        lines.append("Canon/continuity locks:")
        lines.extend(f"- {_compact(item, 360)}" for item in canon[:6])
    threads = packet.get("unresolved_threads") if isinstance(packet.get("unresolved_threads"), list) else []
    if threads:
        # Do not inject raw auto-generated hook text into the roleplay prompt.
        # Local models can copy those memory-management phrases into dialogue.
        # Give only a compact operational hint; the actual story details should
        # come from the active packet, setup, and transcript.
        lines.append(f"Open continuity hooks tracked: {min(len(threads), 6)}. Carry them forward only if the current user turn or Scene Packet raises them directly.")
    return lines[:24]


def sync_scene_human_memory(setup: dict[str, Any], transcript: dict[str, Any], *, user_message: str = "", runtime_bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    packet = build_roleplay_human_scene_packet(setup=setup, transcript=transcript, user_message=user_message, runtime_bundle=runtime_bundle)
    link = upsert_roleplay_human_scene_packet(packet)
    return {"schema_id": "neo.roleplay.human_memory.sync_scene.v1", "status": link.get("status") or "linked", "packet": packet, "link": link}


def roleplay_human_memory_state_payload(scene_id: str = "default") -> dict[str, Any]:
    sqlite_state = ensure_roleplay_memory_schema()
    table_counts = sqlite_state.get("table_counts") or {}
    return {
        "schema_id": HUMAN_MEMORY_SCHEMA_ID,
        "version": HUMAN_MEMORY_VERSION,
        "surface_id": "roleplay",
        "status": "active",
        "ready": bool(sqlite_state.get("ready")),
        "scene_id": scene_id,
        "sqlite": sqlite_state,
        "counts": {
            "scene_packets": int(table_counts.get("rp_scene_memory_packets") or 0),
            "character_states": int(table_counts.get("rp_character_states") or 0),
            "character_knowledge": int(table_counts.get("rp_character_knowledge") or 0),
            "unresolved_threads": int(table_counts.get("rp_unresolved_threads") or 0),
            "relationship_states": int(table_counts.get("rp_relationship_state") or 0),
        },
        "current_scene_packet": get_roleplay_human_scene_packet(scene_id),
        "memory_types": ["canon", "scene", "relationship", "emotion", "world", "continuity", "style", "boundary", "checkpoint", "branch", "unresolved_thread", "character_knowledge"],
        "policy": "Roleplay keeps specialized human/state memory locally, then publishes searchable summaries to the central Memory Engine when roleplay_memory is indexed.",
    }


def roleplay_human_memory_index_rows(limit: int = 300) -> list[dict[str, Any]]:
    return roleplay_human_memory_rows(limit=limit)
