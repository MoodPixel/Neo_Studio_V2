
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.control_center.roleplay_controller import roleplay_control_context_payload, roleplay_control_status_payload

ROOT_DIR = Path(__file__).resolve().parents[2]
ROLEPLAY_DB_PATH = ROOT_DIR / "neo_data" / "roleplay" / "roleplay.sqlite"
TRACE_DIR = ROLEPLAY_DATA_ROOT / "scene_director" / "traces"

SCENE_DIRECTOR_PHASE = "M15"
SCENE_DIRECTOR_SCHEMA_ID = "neo.roleplay.scene_director_runtime.v1"

PLAYER_CONTROL_VERBS = (
    "says", "said", "asks", "asked", "thinks", "thought", "feels", "felt", "decides", "decided",
    "steps", "stepped", "moves", "moved", "reaches", "reached", "grabs", "grabbed", "nods", "nodded",
    "smiles", "smiled", "frowns", "frowned", "remembers", "remembered", "realizes", "realized",
)
UNSPECIFIED_RISK_TERMS = (
    "bruised face", "blue hair", "face partly obscured", "anger is palpable", "appears afraid",
    "looks scared", "looks terrified", "holding the", "fluttering around", "crowded café", "busy lounge",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _clean(value: Any, *, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _slug(value: Any) -> str:
    text = str(value or "default")
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower()).strip("-")[:72] or "default"


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _connect_roleplay() -> sqlite3.Connection:
    conn = sqlite3.connect(ROLEPLAY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _latest_trace_files(limit: int = 8) -> list[dict[str, Any]]:
    if not TRACE_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(TRACE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, min(limit, 50))]:
        data = _load_json(path, {})
        rows.append({
            "trace_id": data.get("trace_id") or path.stem,
            "scene_id": data.get("scene_id") or "",
            "intent": data.get("intent") or "",
            "status": data.get("status") or "",
            "created_at": data.get("created_at") or "",
            "storage_path": _relative_to_root(path),
        })
    return rows


def roleplay_scene_director_status_payload() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    roleplay_ready = ROLEPLAY_DB_PATH.exists()
    counts: dict[str, int] = {}
    if roleplay_ready:
        try:
            with _connect_roleplay() as conn:
                for table in [
                    "rp_scene_memory_packets", "rp_memory_fragments", "rp_character_states",
                    "rp_relationship_state", "rp_unresolved_threads", "rp_turn_writebacks",
                ]:
                    counts[table] = _table_count(conn, table)
        except Exception:
            roleplay_ready = False
    control_status = roleplay_control_status_payload()
    return {
        "schema_id": SCENE_DIRECTOR_SCHEMA_ID,
        "phase": SCENE_DIRECTOR_PHASE,
        "status": "ready" if roleplay_ready and control_status.get("status") in {"ready", "missing_dependency"} else "missing_dependency",
        "label": "Roleplay Scene Director Runtime",
        "roleplay_db_ready": roleplay_ready,
        "roleplay_table_counts": counts,
        "control_center_status": control_status.get("status"),
        "policy": {
            "llm_role": "performer_pilot",
            "scene_director_role": "runtime_control_center_copilot",
            "memory_role": "continuity_library_and_world_state",
            "send_full_universe_prompt": False,
            "validate_before_save": True,
            "player_control_boundary_required": True,
            "dialogue_lanes_required_for_scene_turns": True,
        },
        "recent_traces": _latest_trace_files(limit=8),
    }


def _extract_context_items(control_context: dict[str, Any]) -> list[dict[str, Any]]:
    plan = control_context.get("plan") if isinstance(control_context.get("plan"), dict) else {}
    rctx = plan.get("roleplay_context") if isinstance(plan.get("roleplay_context"), dict) else {}
    items = rctx.get("items") if isinstance(rctx.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _confirmed_context_text(control_context: dict[str, Any]) -> str:
    items = _extract_context_items(control_context)
    selected = []
    for item in items[:28]:
        if str(item.get("trust") or "").lower() in {"confirmed", "state", "retrieved", "memory"} or item.get("lane"):
            selected.append(f"{item.get('lane') or 'context'} | {item.get('title') or 'item'}: {_clean(item.get('content'), limit=650)}")
    return "\n".join(f"- {line}" for line in selected if line.strip())


def _resolve_player(control_context: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    plan = control_context.get("plan") if isinstance(control_context.get("plan"), dict) else {}
    scope = plan.get("scope") if isinstance(plan.get("scope"), dict) else {}
    return {
        "player_character_id": str(payload.get("player_character_id") or scope.get("player_character_id") or "").strip(),
        "player_character_name": str(payload.get("player_character_name") or scope.get("player_character_name") or "").strip(),
    }


def _roleplay_output_rules(intent: str) -> list[str]:
    if "canon_summary" in intent or "summary" in intent:
        return [
            "Use factual summary mode, not cinematic narration.",
            "Sections required: Confirmed packet facts, Not specified, Canon constraints.",
            "If a detail is not explicit in the context, list it under Not specified instead of inventing it.",
        ]
    return [
        "Use immersive roleplay mode with clear lanes.",
        "Required lanes: Narration: and assistant-controlled character dialogue labels when the assistant-controlled character speaks.",
        "Do not use generic Assistant voice in-scene.",
        "Do not output XML, markdown reports, analysis cards, or fields named Response, Brief immersive situation, Character Name, Scene description, Dialogue, Additional notes, or Character control rules.",
        "Advance one scene beat only; do not resolve major mysteries early.",
    ]


def _build_director_prompt(control_context: dict[str, Any], payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    plan = control_context.get("plan") if isinstance(control_context.get("plan"), dict) else {}
    scope = plan.get("scope") if isinstance(plan.get("scope"), dict) else {}
    intent = str(plan.get("intent") or payload.get("intent") or "roleplay.scene_turn")
    player = _resolve_player(control_context, payload)
    contract = plan.get("prompt_contract") if isinstance(plan.get("prompt_contract"), dict) else {}
    validation_plan = plan.get("validation_plan") if isinstance(plan.get("validation_plan"), dict) else {}
    context_text = _confirmed_context_text(control_context)
    player_label = player.get("player_character_name") or player.get("player_character_id") or "the user-controlled character"
    npc_ids = scope.get("npc_character_ids") if isinstance(scope.get("npc_character_ids"), list) else []
    lines = [
        "# Neo Roleplay Scene Director Runtime",
        f"Phase: {SCENE_DIRECTOR_PHASE}",
        "Role split: Neo Control Center is the scene director/copilot. The backend LLM is the performer/pilot.",
        "Do not dump or improvise the whole universe. Perform only from the active director brief and confirmed context.",
        "",
        "## Active runtime scope",
        f"- Scene: {scope.get('scene_title') or scope.get('scene_id') or payload.get('scene_id') or 'unknown'}",
        f"- Scene ID: {scope.get('scene_id') or payload.get('scene_id') or 'default'}",
        f"- Runtime bundle: {scope.get('runtime_bundle_id') or payload.get('runtime_bundle_id') or 'not specified'}",
        f"- Scene packet: {scope.get('scene_packet_id') or payload.get('scene_packet_id') or 'not specified'}",
        f"- Universe: {scope.get('universe_id') or 'not specified'}",
        f"- World: {scope.get('world_id') or 'not specified'}",
        f"- Location: {scope.get('location_id') or 'not specified'}",
        "",
        "## Character control contract",
        f"- User/player controls: {player_label}",
        f"- Model may control NPCs/environment only: {', '.join(str(x) for x in npc_ids) if npc_ids else 'NPCs listed by packet/scene state only'}",
        f"- Never write {player_label}'s dialogue, thoughts, feelings, decisions, or physical actions unless explicit co-writing is enabled.",
        "- Private NPC knowledge may shape subtext, but must not be revealed unless the scene state allows it.",
        "",
        "## Output rules",
        *[f"- {rule}" for rule in _roleplay_output_rules(intent)],
        "",
        "## Anti-hallucination rules",
        "- Do not invent appearances, injuries, facial expressions, exact positions, relationships, object possession, or location details.",
        "- If a detail is not explicit in confirmed context, leave it unstated or mark it as not specified.",
        "- Use canon/state context as constraints, not as prose to copy wholesale.",
        "",
        "## Active prompt contract",
        f"- Contract: {contract.get('contract_id') or 'roleplay_scene_turn_v1'}",
        f"- Validation checks: {', '.join(validation_plan.get('checks') or contract.get('validation_checks') or []) or 'standard roleplay validation'}",
        "",
        "## Confirmed context available to performer",
        context_text or "- No confirmed context items were selected; use setup and scene packet only, and mark missing details as unknown.",
    ]
    meta = {
        "intent": intent,
        "scope": scope,
        "player": player,
        "contract_id": contract.get("contract_id") or "",
        "context_item_count": len(_extract_context_items(control_context)),
    }
    return "\n".join(lines).strip(), meta


def roleplay_scene_director_preflight_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    ensure_roleplay_foundation(write_manifest=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    control_context = payload.get("control_context") if isinstance(payload.get("control_context"), dict) else None
    if not control_context:
        control_payload = {k: v for k, v in payload.items() if k != "control_context"}
        control_context = roleplay_control_context_payload(control_payload)
    prompt_block, meta = _build_director_prompt(control_context or {}, payload)
    trace_id = f"scene-director-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    trace = {
        "schema_id": SCENE_DIRECTOR_SCHEMA_ID,
        "phase": SCENE_DIRECTOR_PHASE,
        "trace_id": trace_id,
        "status": "ready",
        "created_at": _now(),
        "scene_id": payload.get("scene_id") or meta.get("scope", {}).get("scene_id") or "default",
        "intent": meta.get("intent") or "",
        "control_trace_id": (control_context or {}).get("trace_id") or ((control_context or {}).get("plan") or {}).get("trace_id"),
        "runtime_bundle_id": meta.get("scope", {}).get("runtime_bundle_id") or payload.get("runtime_bundle_id") or "",
        "scene_packet_id": meta.get("scope", {}).get("scene_packet_id") or payload.get("scene_packet_id") or "",
        "meta": meta,
        "prompt_preview": prompt_block[:5000],
    }
    path = TRACE_DIR / f"{trace_id}.json"
    _write_json(path, trace)
    return {
        "ok": True,
        "schema_id": SCENE_DIRECTOR_SCHEMA_ID,
        "phase": SCENE_DIRECTOR_PHASE,
        "status": "ready",
        "trace_id": trace_id,
        "prompt_block": prompt_block,
        "control_context": control_context,
        "diagnostics": {**meta, "storage_path": _relative_to_root(path)},
    }


def _context_allows_term(term: str, context_text: str) -> bool:
    return term.lower() in context_text.lower()


def _validation_player_names(player_name: str, context_text: str) -> list[str]:
    names: list[str] = []
    for value in [player_name]:
        for part in re.split(r"\s*(?:,|\band\b|&|/)\s*", str(value or "")):
            cleaned = part.strip(" .,!?:;\"'")
            if cleaned and cleaned not in names:
                names.append(cleaned)
    for match in re.finditer(r"(?:User/player controls|User controls|User-controlled character lock):\s*([^\n.]+)", context_text, flags=re.IGNORECASE):
        for part in re.split(r"\s*(?:,|\band\b|&|/)\s*", match.group(1)):
            cleaned = part.strip(" .,!?:;\"'")
            if cleaned and cleaned.lower() not in {"not specified", "the user-controlled character"} and cleaned not in names:
                names.append(cleaned[:80])
    return names[:8]


def _meta_template_label_leak(text: str) -> str:
    blocked = [
        "<response>", "</response>", "**Response**", "Brief immersive situation", "Character Name:",
        "Scene description:", "Dialogue:", "Additional notes:", "Character control rules:",
    ]
    lowered = text.lower()
    for label in blocked:
        if label.lower() in lowered:
            return label
    return ""


def roleplay_scene_director_validate_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    text = str(payload.get("assistant_text") or payload.get("text") or "")
    prompt_block = str(payload.get("prompt_block") or "")
    control_context = payload.get("control_context") if isinstance(payload.get("control_context"), dict) else {}
    context_text = prompt_block + "\n" + _confirmed_context_text(control_context)
    player_name = str(payload.get("player_character_name") or ((_resolve_player(control_context, payload)).get("player_character_name")) or "").strip()
    intent = str(payload.get("intent") or ((control_context.get("plan") or {}).get("intent") if isinstance(control_context.get("plan"), dict) else "") or "roleplay.scene_turn")
    warnings: list[dict[str, Any]] = []
    lowered = text.lower()
    for controlled_name in _validation_player_names(player_name, context_text):
        aliases = [controlled_name]
        first = controlled_name.split()[0].strip()
        if first and first.lower() != controlled_name.lower():
            aliases.append(first)
        for alias in aliases:
            label_pattern = re.compile(rf"(^|\n)\s*{re.escape(alias)}\s*:", re.IGNORECASE)
            action_pattern = re.compile(rf"\b{re.escape(alias)}\b[^\n]{{0,110}}\b({'|'.join(PLAYER_CONTROL_VERBS)})\b", re.IGNORECASE)
            speech_pattern = re.compile(rf"\b{re.escape(alias)}\b[^\n]{{0,80}}\b(?:says|said|asks|asked|whispers|murmurs|replies|speaks)\b", re.IGNORECASE)
            if label_pattern.search(text) or action_pattern.search(text) or speech_pattern.search(text):
                warnings.append({"code": "possible_player_control", "severity": "high", "message": f"Response may control user-controlled character '{controlled_name}' via '{alias}'."})
                break
    leaked_label = _meta_template_label_leak(text)
    if leaked_label:
        warnings.append({"code": "meta_template_leak", "severity": "high", "message": f"Response leaked template/meta label: {leaked_label}"})
    for term in UNSPECIFIED_RISK_TERMS:
        if term in lowered and not _context_allows_term(term, context_text):
            warnings.append({"code": "unspecified_detail", "severity": "medium", "message": f"Response mentions '{term}' but it was not found in selected context."})
    if "canon_summary" not in intent and "summary" not in intent:
        has_narration = bool(re.search(r"(^|\n)\s*Narration\s*:", text, re.IGNORECASE))
        has_dialogue_label = bool(re.search(r"(^|\n)\s*[A-Z][A-Za-z0-9 _'-]{1,40}\s*:", text))
        if not has_narration and not has_dialogue_label:
            warnings.append({"code": "missing_roleplay_lanes", "severity": "low", "message": "Scene turn does not use clear Narration/NPC dialogue lanes."})
    if "assistant" in lowered[:240] and "assistant:" in lowered[:240]:
        warnings.append({"code": "assistant_voice_leak", "severity": "medium", "message": "Response may use assistant/meta voice instead of scene lanes."})
    status = "passed" if not any(w.get("severity") == "high" for w in warnings) else "needs_review"
    return {
        "ok": True,
        "schema_id": SCENE_DIRECTOR_SCHEMA_ID,
        "phase": SCENE_DIRECTOR_PHASE,
        "status": status,
        "warning_count": len(warnings),
        "warnings": warnings,
        "policy": "Validation is advisory in M15; future phases can repair/regenerate before saving.",
    }


def roleplay_scene_director_trace_payload(limit: int = 20) -> dict[str, Any]:
    return {"ok": True, "schema_id": SCENE_DIRECTOR_SCHEMA_ID, "phase": SCENE_DIRECTOR_PHASE, "traces": _latest_trace_files(limit=limit), "count": len(_latest_trace_files(limit=limit))}
