from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.control_center.service import NeoControlCenter
from neo_app.control_center.prompt_contracts import (
    get_prompt_contract,
    render_prompt_contract_block,
    resolve_roleplay_contract_id,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"
ROLEPLAY_DB_PATH = ROOT_DIR / "neo_data" / "roleplay" / "roleplay.sqlite"
ROLEPLAY_SCENE_DIR = ROOT_DIR / "neo_data" / "roleplay" / "story_sessions" / "scene_foundation"

ROLEPLAY_CC_PHASE = "M7"
ROLEPLAY_CC_SCHEMA_ID = "neo.roleplay.control_center.v1"
ROLEPLAY_CC_CONTRACT_ID = "roleplay_scene_turn_v1"

ROLEPLAY_MEMORY_LANES = [
    "active_scene_packet",
    "player_control_contract",
    "canon_locks",
    "scene_state",
    "character_state",
    "relationship_state",
    "unresolved_threads",
    "recent_turn_writebacks",
    "scoped_memory_fragments",
]


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


def _clean_text(value: Any, *, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text




def _is_polluted_roleplay_context(value: Any) -> bool:
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
        r"\bresponse\s+is\s+blocked\s+by\s+(?:a\s+)?guardrail\b",
        r"\bRoughly,\s*my\s+turn\s*:",
        r"\bI\s+await\s+(?:the\s+)?(?:reply|response)\s+from\b",
        r"\bNeo\s+Studio\s+Roleplay\s+Scene\s+Engine\b",
        r"(?:^|\n)\s*[—-]\s*Neo\s+Studio\b",
        r"\b(?:the\s+)?conversation\s+ended\s+abruptly\b",
        r"\bNext\s+scene\s*:",
        r"\b(?:the\s+)?next\s+scene\s+will\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:72] or "scene"


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_@#'’.:-]{2,}", text or "")[:120]]


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


@dataclass(slots=True)
class RoleplayControlRequest:
    message: str = ""
    scene_id: str = "default"
    runtime_bundle_id: str = ""
    scene_packet_id: str = ""
    project_id: str = "roleplay"
    universe_id: str = ""
    world_id: str = ""
    location_id: str = ""
    player_character_id: str = ""
    player_character_name: str = ""
    npc_character_ids: list[str] = field(default_factory=list)
    surface: str = "roleplay"
    memory_limit: int = 10
    backend_profile_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "RoleplayControlRequest":
        payload = payload or {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        npc_raw = payload.get("npc_character_ids") or payload.get("npc_ids") or []
        if isinstance(npc_raw, str):
            npc_ids = [item.strip() for item in re.split(r"[,\n]+", npc_raw) if item.strip()]
        elif isinstance(npc_raw, list):
            npc_ids = [str(item).strip() for item in npc_raw if str(item).strip()]
        else:
            npc_ids = []
        return cls(
            message=str(payload.get("message") or payload.get("user_turn") or payload.get("query") or ""),
            scene_id=str(payload.get("scene_id") or "default"),
            runtime_bundle_id=str(payload.get("runtime_bundle_id") or ""),
            scene_packet_id=str(payload.get("scene_packet_id") or payload.get("active_scene_packet_id") or ""),
            project_id=str(payload.get("project_id") or "roleplay"),
            universe_id=str(payload.get("universe_id") or ""),
            world_id=str(payload.get("world_id") or ""),
            location_id=str(payload.get("location_id") or ""),
            player_character_id=str(payload.get("player_character_id") or payload.get("player_id") or ""),
            player_character_name=str(payload.get("player_character_name") or payload.get("player_name") or ""),
            npc_character_ids=npc_ids,
            surface="roleplay",
            memory_limit=max(3, min(int(payload.get("memory_limit") or 10), 40)),
            backend_profile_id=str(payload.get("backend_profile_id") or payload.get("profile_id") or ""),
            metadata=metadata,
        )


class RoleplayControlCenter:
    """Roleplay-specific Scene Director / Control Center foundation.

    M7 does not replace the Roleplay scene engine. It creates the missing control
    layer that balances active scene packet, player-role boundaries, canon locks,
    character state, relevant memory, validation rules, and writeback intent before
    the backend LLM performs the scene.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, roleplay_db_path: Path = ROLEPLAY_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.roleplay_db_path = Path(roleplay_db_path)
        self.shared = NeoControlCenter(self.db_path)

    def _connect_memory(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_roleplay(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.roleplay_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def status(self) -> dict[str, Any]:
        shared = self.shared.status()
        roleplay_ready = self.roleplay_db_path.exists()
        table_counts: dict[str, int] = {}
        recent: list[dict[str, Any]] = []
        if roleplay_ready:
            try:
                with self._connect_roleplay() as conn:
                    for table in [
                        "rp_scene_memory_packets", "rp_memory_fragments", "rp_character_states",
                        "rp_relationship_state", "rp_unresolved_threads", "rp_turn_writebacks",
                    ]:
                        try:
                            table_counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                        except Exception:
                            table_counts[table] = 0
            except Exception:
                roleplay_ready = False
        try:
            with self._connect_memory() as conn:
                rows = conn.execute(
                    """
                    SELECT trace_id, surface, project_id, scope_id, intent, status, created_at, metadata_json
                    FROM neo_control_center_traces
                    WHERE controller = 'roleplay'
                    ORDER BY created_at DESC
                    LIMIT 8
                    """
                ).fetchall()
                recent = [dict(row) | {"metadata": _safe_json(row["metadata_json"], {})} for row in rows]
                for item in recent:
                    item.pop("metadata_json", None)
        except Exception:
            pass
        return {
            "schema_id": ROLEPLAY_CC_SCHEMA_ID,
            "phase": ROLEPLAY_CC_PHASE,
            "status": "ready" if shared.get("status") == "ready" and roleplay_ready else "missing_dependency",
            "label": "Roleplay Control Center / Scene Director Engine",
            "shared_control_center": {"status": shared.get("status"), "phase": shared.get("phase")},
            "roleplay_db_ready": roleplay_ready,
            "roleplay_table_counts": table_counts,
            "recent_traces": recent,
            "policy": {
                "llm_role": "performer",
                "control_center_role": "scene_director_continuity_supervisor_context_balancer",
                "send_all_memory": False,
                "player_control_boundary_required": True,
                "canon_and_scene_state_preferred_over_inference": True,
                "npc_private_knowledge_must_be_gated": True,
            },
            "endpoints": {
                "status": "/api/roleplay/control-center/status",
                "plan": "/api/roleplay/control-center/plan",
                "context": "/api/roleplay/control-center/context",
                "traces": "/api/roleplay/control-center/traces",
            },
        }

    def plan(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        request = RoleplayControlRequest.from_payload(payload)
        setup = self._load_scene_setup(request.scene_id)
        scene_packet = self._resolve_scene_packet(request, setup)
        scope = self._resolve_scope(request, setup, scene_packet)
        intent = self._resolve_intent(request)
        contract_id = resolve_roleplay_contract_id(intent)
        shared_payload = {
            "controller": "roleplay",
            "user_input": request.message,
            "surface": "roleplay",
            "project_id": scope.get("project_id") or "roleplay",
            "scope_type": "scene",
            "scope_id": scope.get("scene_id") or request.scene_id,
            "scope_key": scope.get("scene_packet_id") or scope.get("runtime_bundle_id") or request.scene_id,
            "intent": intent,
            "backend_profile_id": request.backend_profile_id,
            "prompt_contract_id": contract_id,
            "memory_limit": request.memory_limit,
            "metadata": {
                **request.metadata,
                "roleplay_cc_phase": ROLEPLAY_CC_PHASE,
                "runtime_bundle_id": scope.get("runtime_bundle_id") or "",
                "scene_packet_id": scope.get("scene_packet_id") or "",
                "player_character_id": scope.get("player_character_id") or "",
            },
        }
        shared_plan = self.shared.plan(shared_payload, persist=persist)
        trace = shared_plan.get("trace") if isinstance(shared_plan.get("trace"), dict) else {}
        roleplay_context = self._build_roleplay_context(request, setup, scene_packet, scope)
        roleplay_plan = {
            "schema_id": ROLEPLAY_CC_SCHEMA_ID,
            "phase": ROLEPLAY_CC_PHASE,
            "status": "planned",
            "controller": "roleplay",
            "trace_id": trace.get("trace_id"),
            "intent": intent,
            "scene_id": scope.get("scene_id"),
            "runtime_bundle_id": scope.get("runtime_bundle_id"),
            "scene_packet_id": scope.get("scene_packet_id"),
            "scope": scope,
            "memory_lanes": ROLEPLAY_MEMORY_LANES,
            "roleplay_context": roleplay_context,
            "prompt_contract": self._prompt_contract(scope, intent),
            "context_brief": self._build_context_brief(request, setup, scene_packet, scope, roleplay_context),
            "validation_plan": self._validation_plan(scope),
            "writeback_plan": self._writeback_plan(scope),
            "shared_trace": trace,
        }
        if persist and trace.get("trace_id"):
            self._merge_trace_metadata(trace.get("trace_id"), {"roleplay_control_center": {k: v for k, v in roleplay_plan.items() if k not in {"shared_trace"}}})
        return {"ok": True, "status": "planned", "plan": roleplay_plan, "trace": trace}

    def context(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        planned = self.plan(payload, persist=persist)
        plan = planned.get("plan") or {}
        brief = plan.get("context_brief") if isinstance(plan.get("context_brief"), dict) else {}
        prompt_block = str(brief.get("prompt_block") or "").strip()
        return {
            "ok": True,
            "status": "ready",
            "schema_id": ROLEPLAY_CC_SCHEMA_ID,
            "phase": ROLEPLAY_CC_PHASE,
            "trace_id": plan.get("trace_id"),
            "prompt_block": prompt_block,
            "messages": [{"role": "system", "content": prompt_block}] if prompt_block else [],
            "plan": plan,
            "diagnostics": {
                "scene_id": plan.get("scene_id"),
                "runtime_bundle_id": plan.get("runtime_bundle_id"),
                "scene_packet_id": plan.get("scene_packet_id"),
                "intent": plan.get("intent"),
                "context_items": ((plan.get("roleplay_context") or {}).get("item_count") if isinstance(plan.get("roleplay_context"), dict) else 0),
                "contract_id": (plan.get("prompt_contract") or {}).get("contract_id"),
                "policy": "Roleplay Control Center builds compact Scene Director brief before backend generation.",
            },
        }

    def list_traces(self, *, limit: int = 25, scene_id: str | None = None, scope_id: str | None = None) -> dict[str, Any]:
        clauses = ["controller = 'roleplay'"]
        params: list[Any] = []
        if scene_id:
            clauses.append("scope_id = ?")
            params.append(scene_id)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        with self._connect_memory() as conn:
            rows = conn.execute(
                f"""
                SELECT trace_id, controller, surface, project_id, scope_id, intent, status, created_at, metadata_json
                FROM neo_control_center_traces
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, max(1, min(int(limit or 25), 100))),
            ).fetchall()
        traces = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _safe_json(item.pop("metadata_json", "{}"), {})
            traces.append(item)
        return {"ok": True, "schema_id": ROLEPLAY_CC_SCHEMA_ID, "phase": ROLEPLAY_CC_PHASE, "traces": traces, "count": len(traces)}

    def _load_scene_setup(self, scene_id: str) -> dict[str, Any]:
        setup = _load_json(ROLEPLAY_SCENE_DIR / f"{_slug(scene_id)}.setup.json", {})
        if not isinstance(setup, dict):
            setup = {}
        setup.setdefault("scene_id", scene_id)
        return setup

    def _resolve_scene_packet(self, request: RoleplayControlRequest, setup: dict[str, Any]) -> dict[str, Any]:
        packet_id = request.scene_packet_id or str(setup.get("scene_packet_id") or "")
        if not packet_id:
            return {}
        try:
            with self._connect_roleplay() as conn:
                row = conn.execute("SELECT * FROM rp_scene_memory_packets WHERE packet_id = ? ORDER BY updated_at DESC LIMIT 1", (packet_id,)).fetchone()
                if row:
                    item = dict(row)
                    item["payload"] = _safe_json(item.get("payload_json"), {})
                    item["canon_locks"] = _safe_json(item.get("canon_locks_json"), [])
                    item["character_knowledge"] = _safe_json(item.get("character_knowledge_json"), [])
                    item["relationship_state"] = _safe_json(item.get("relationship_state_json"), {})
                    item["unresolved_threads"] = _safe_json(item.get("unresolved_threads_json"), [])
                    item["continuity_warnings"] = _safe_json(item.get("continuity_warnings_json"), [])
                    item.pop("payload_json", None)
                    return item
        except Exception as exc:
            return {"packet_id": packet_id, "status": "packet_lookup_error", "error": str(exc)}
        return {"packet_id": packet_id, "status": "missing"}

    def _resolve_scope(self, request: RoleplayControlRequest, setup: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
        instructions = payload.get("model_instructions") if isinstance(payload.get("model_instructions"), dict) else {}
        player_ids = instructions.get("player_character_ids") if isinstance(instructions.get("player_character_ids"), list) else []
        player_id = request.player_character_id or payload.get("player_character_id") or instructions.get("player_character_id") or (player_ids[0] if player_ids else "")
        player_name = request.player_character_name or payload.get("player_character_name") or instructions.get("player_character_name") or self._character_name_from_packet(payload, str(player_id or ""))
        # Runtime packets built by older code could mix an id from one
        # character with the scenario default name from another. Repair the
        # pair generically from the active packet roster before building
        # control-center scope.
        matched_id = self._character_id_from_packet(payload, str(player_name or ""))
        if matched_id and player_id and matched_id != str(player_id):
            player_id = matched_id
        if matched_id and not player_id:
            player_id = matched_id
        if player_id and not player_name:
            player_name = self._character_name_from_packet(payload, str(player_id))
        npc_ids = request.npc_character_ids or payload.get("npc_character_ids") or instructions.get("npc_character_ids") or []
        if isinstance(npc_ids, list) and player_id:
            npc_ids = [item for item in npc_ids if str(item) != str(player_id)]
        requested_scene = str(request.scene_id or setup.get("scene_id") or "").strip()
        packet_scene = str(payload.get("scene_id") or payload.get("scenario_id") or packet.get("scene_id") or "").strip()
        scene_id = packet_scene if requested_scene.lower() in {"", "default"} and packet_scene else (requested_scene or packet_scene or "default")
        packet_title = str(payload.get("title") or packet.get("title") or "").strip()
        setup_title = str(setup.get("title") or "").strip()
        scene_title = packet_title if setup_title.lower() in {"", "untitled scene"} and packet_title else (setup_title or packet_title or "Untitled Scene")
        return {
            "surface": "roleplay",
            "project_id": request.project_id or packet.get("project_id") or payload.get("project_id") or "roleplay",
            "scene_id": scene_id,
            "runtime_bundle_id": request.runtime_bundle_id or setup.get("runtime_bundle_id") or "",
            "scene_packet_id": request.scene_packet_id or setup.get("scene_packet_id") or packet.get("packet_id") or payload.get("scene_packet_id") or "",
            "universe_id": request.universe_id or packet.get("universe_id") or payload.get("universe_id") or "",
            "world_id": request.world_id or packet.get("world_id") or payload.get("world_id") or "",
            "location_id": request.location_id or packet.get("location_id") or payload.get("location_id") or "",
            "player_character_id": player_id or "",
            "player_character_name": player_name or "",
            "npc_character_ids": npc_ids if isinstance(npc_ids, list) else [],
            "memory_scope": setup.get("memory_scope") or packet.get("memory_scope") or "roleplay.scene",
            "scene_title": scene_title,
        }

    def _character_name_from_packet(self, payload: dict[str, Any], character_id: str) -> str:
        wanted = _clean_text(character_id, limit=120)
        if not wanted:
            return ""
        rows = payload.get("character_context") if isinstance(payload.get("character_context"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _clean_text(row.get("source_id"), limit=120) != wanted:
                continue
            return _clean_text(row.get("title") or row.get("display_label"), limit=120)
        return ""

    def _character_id_from_packet(self, payload: dict[str, Any], character_name: str) -> str:
        wanted = _clean_text(character_name, limit=120).lower()
        if not wanted:
            return ""
        rows = payload.get("character_context") if isinstance(payload.get("character_context"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = _clean_text(row.get("source_id"), limit=120)
            names = [row.get("title"), row.get("display_label"), source_id]
            payload_row = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            names.extend([payload_row.get("display_label"), payload_row.get("label")])
            if any(_clean_text(name, limit=120).lower() == wanted for name in names if _clean_text(name, limit=120)):
                return source_id
        return ""

    def _resolve_intent(self, request: RoleplayControlRequest) -> str:
        text = request.message.strip().lower()
        if not text:
            return "roleplay.scene_continue"
        if any(term in text for term in ["summarize", "what is loaded", "canon", "packet", "before we start"]):
            return "roleplay.canon_summary"
        if any(term in text for term in ["continue", "next", "respond", "says", "asks", "does"]):
            return "roleplay.scene_turn"
        return "roleplay.scene_turn"

    def _build_roleplay_context(self, request: RoleplayControlRequest, setup: dict[str, Any], packet: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if packet:
            items.extend(self._packet_items(packet))
        items.extend(self._character_state_items(scope, limit=8))
        items.extend(self._relationship_items(scope, limit=6))
        items.extend(self._thread_items(scope, limit=6))
        items.extend(self._writeback_items(scope, limit=6))
        items.extend(self._fragment_items(request, scope, limit=request.memory_limit))
        # Keep deterministic priority: packet/control facts first, then state, then retrieved context.
        for idx, item in enumerate(items):
            item.setdefault("rank", idx + 1)
        return {
            "status": "ready",
            "item_count": len(items),
            "items": items[: max(8, request.memory_limit + 16)],
            "policy": "Context is selected by lane; full universe memory remains in SQLite/retrieval, not dumped into prompt.",
        }

    def _packet_items(self, packet: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
        def add(lane: str, title: str, content: Any, priority: float = 0.95) -> None:
            text = _clean_text(content, limit=1000)
            if text:
                items.append({"lane": lane, "title": title, "content": text, "priority": priority, "source": "scene_packet", "trust": "confirmed"})
        add("active_scene_packet", "Packet title", packet.get("title") or payload.get("title"), 1.0)
        add("scene_state", "Emotional tone", packet.get("emotional_tone") or payload.get("emotional_tone"), 0.9)
        for key in ["summary", "premise", "location", "situation", "core_conflict"]:
            add("scene_state", key.replace("_", " ").title(), payload.get(key), 0.92)
        canon = packet.get("canon_locks") if isinstance(packet.get("canon_locks"), list) else []
        for idx, entry in enumerate(canon[:10], 1):
            add("canon_locks", f"Canon lock {idx}", entry, 1.0)
        knowledge = packet.get("character_knowledge") if isinstance(packet.get("character_knowledge"), list) else []
        for idx, entry in enumerate(knowledge[:8], 1):
            add("character_state", f"Character knowledge {idx}", entry, 0.9)
        unresolved = packet.get("unresolved_threads") if isinstance(packet.get("unresolved_threads"), list) else []
        for idx, entry in enumerate(unresolved[:8], 1):
            add("unresolved_threads", f"Unresolved thread {idx}", entry, 0.82)
        warnings = packet.get("continuity_warnings") if isinstance(packet.get("continuity_warnings"), list) else []
        for idx, entry in enumerate(warnings[:6], 1):
            add("continuity", f"Continuity warning {idx}", entry, 0.88)
        return items

    def _scope_where(self, scope: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for col in ["project_id", "universe_id", "world_id", "location_id", "session_id", "branch_id"]:
            value = str(scope.get(col) or "").strip()
            if value:
                clauses.append(f"({col} = ? OR {col} = '')")
                params.append(value)
        return (" AND ".join(clauses) if clauses else "1=1"), params

    def _active_authored_scene_id(self, scope: dict[str, Any]) -> str:
        scene_id = str((scope or {}).get("scene_id") or "").strip()
        if scene_id and scene_id.lower() != "default" and not scene_id.lower().startswith("human_scene:"):
            return scene_id
        return ""

    def _is_default_scene_context_item(self, text: Any, *, active_scene_id: str = "") -> bool:
        if not active_scene_id:
            return False
        value = str(text or "").strip()
        if not value:
            return False
        return bool(re.search(r"\bscene:default\b|human_scene:default|\bUntitled\s+Scene\b", value, flags=re.IGNORECASE))

    def _character_state_items(self, scope: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        try:
            where, params = self._scope_where(scope)
            with self._connect_roleplay() as conn:
                rows = conn.execute(
                    f"""
                    SELECT character_id, display_name, current_emotion, goals_json, boundaries_json, trust_level, payload_json, updated_at
                    FROM rp_character_states
                    WHERE {where}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            items = []
            active_scene_id = self._active_authored_scene_id(scope)
            for row in rows:
                title = row["display_name"] or row["character_id"]
                goals = _safe_json(row["goals_json"], [])
                boundaries = _safe_json(row["boundaries_json"], [])
                content = f"{title} emotion={row['current_emotion'] or 'unspecified'} goals={goals} boundaries={boundaries} trust={row['trust_level']}"
                if self._is_default_scene_context_item(title, active_scene_id=active_scene_id) or self._is_default_scene_context_item(content, active_scene_id=active_scene_id):
                    continue
                if _is_polluted_roleplay_context(content):
                    continue
                items.append({"lane": "character_state", "title": title, "content": _clean_text(content, limit=900), "source": "rp_character_states", "trust": row["trust_level"] or "inferred", "priority": 0.82})
            return items
        except Exception:
            return []

    def _relationship_items(self, scope: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        try:
            where, params = self._scope_where(scope)
            with self._connect_roleplay() as conn:
                rows = conn.execute(
                    f"""
                    SELECT character_a_id, character_b_id, relationship_type, state_label, payload_json, updated_at
                    FROM rp_relationship_state
                    WHERE {where}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            return [
                {"lane": "relationship_state", "title": f"{r['character_a_id']} ↔ {r['character_b_id']}", "content": _clean_text(f"{r['relationship_type']} / {r['state_label']} / {_safe_json(r['payload_json'], {})}", limit=900), "source": "rp_relationship_state", "trust": "state", "priority": 0.78}
                for r in rows
            ]
        except Exception:
            return []

    def _thread_items(self, scope: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        try:
            active_scene_id = self._active_authored_scene_id(scope)
            if active_scene_id:
                where, params = "scene_id = ?", [active_scene_id]
            else:
                where, params = self._scope_where(scope)
            with self._connect_roleplay() as conn:
                rows = conn.execute(
                    f"""
                    SELECT title, thread_type, status, priority, content, updated_at
                    FROM rp_unresolved_threads
                    WHERE {where} AND status != 'closed'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            items = []
            for r in rows:
                content = f"{r['thread_type']} / {r['priority']}: {r['content']}"
                title = r["title"] or r["thread_type"]
                if self._is_default_scene_context_item(title, active_scene_id=active_scene_id) or self._is_default_scene_context_item(content, active_scene_id=active_scene_id):
                    continue
                if _is_polluted_roleplay_context(content) or _is_polluted_roleplay_context(title):
                    continue
                items.append({"lane": "unresolved_threads", "title": title, "content": _clean_text(content, limit=900), "source": "rp_unresolved_threads", "trust": "open_thread", "priority": 0.75})
            return items
        except Exception:
            return []

    def _writeback_items(self, scope: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        try:
            scene_id = str(scope.get("scene_id") or "")
            with self._connect_roleplay() as conn:
                rows = conn.execute(
                    """
                    SELECT summary, status, writeback_json, created_at
                    FROM rp_turn_writebacks
                    WHERE scene_id = ? AND status NOT IN ('archived','rejected','skipped_polluted_or_nonfinal')
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (scene_id, limit),
                ).fetchall()
            items = []
            for r in rows:
                content = r["summary"] or _safe_json(r["writeback_json"], {})
                if _is_polluted_roleplay_context(content):
                    continue
                items.append({"lane": "recent_turn_writebacks", "title": r["status"] or "turn_writeback", "content": _clean_text(content, limit=900), "source": "rp_turn_writebacks", "trust": "recent_state", "priority": 0.72})
            return items
        except Exception:
            return []

    def _fragment_items(self, request: RoleplayControlRequest, scope: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        query_tokens = set(_tokens(request.message))
        try:
            active_scene_id = self._active_authored_scene_id(scope)
            where, params = self._scope_where(scope)
            if active_scene_id:
                where = f"({where}) AND COALESCE(source_record_id, '') NOT LIKE 'scene:default:%' AND COALESCE(source_id, '') NOT LIKE 'scene:default:%' AND COALESCE(source_record_id, '') NOT LIKE 'human_scene:default%' AND COALESCE(source_id, '') NOT LIKE 'human_scene:default%'"
            with self._connect_roleplay() as conn:
                rows = conn.execute(
                    f"""
                    SELECT fragment_id, title, memory_type, content, source_record_kind, source_record_id, salience, confidence, updated_at
                    FROM rp_memory_fragments
                    WHERE {where} AND status NOT IN ('deleted', 'superseded', 'archived', 'rejected', 'skipped_polluted_or_nonfinal')
                    ORDER BY salience DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (*params, max(limit * 4, 20)),
                ).fetchall()
            scored = []
            for r in rows:
                text = f"{r['title']} {r['content']}"
                overlap = len(query_tokens.intersection(set(_tokens(text)))) if query_tokens else 0
                score = float(r["salience"] or 0.5) + (overlap * 0.08)
                scored.append((score, r))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            items = []
            for score, r in scored[:limit]:
                content = r["content"] or ""
                title = r["title"] or r["fragment_id"]
                source_id = r["source_record_id"] or ""
                if self._is_default_scene_context_item(source_id, active_scene_id=active_scene_id) or self._is_default_scene_context_item(title, active_scene_id=active_scene_id) or self._is_default_scene_context_item(content, active_scene_id=active_scene_id):
                    continue
                if _is_polluted_roleplay_context(content) or _is_polluted_roleplay_context(title):
                    continue
                if "Scene ready. You are playing:" in content or "Scene generation is deferred" in content:
                    continue
                items.append({
                    "lane": "scoped_memory_fragments",
                    "title": title,
                    "content": _clean_text(content, limit=900),
                    "source": "rp_memory_fragments",
                    "source_record_id": r["source_record_id"],
                    "source_record_kind": r["source_record_kind"],
                    "trust": "compiled_memory",
                    "priority": round(score, 3),
                    "memory_type": r["memory_type"],
                })
            return items
        except Exception:
            return []

    def _build_context_brief(self, request: RoleplayControlRequest, setup: dict[str, Any], packet: dict[str, Any], scope: dict[str, Any], roleplay_context: dict[str, Any]) -> dict[str, Any]:
        player = scope.get("player_character_name") or scope.get("player_character_id") or "the user-selected player character"
        npc_ids = scope.get("npc_character_ids") or []
        npc_line = ", ".join(str(x) for x in npc_ids) if npc_ids else "NPCs, environment, and non-player characters from the active packet"
        contract = self._prompt_contract(scope, self._resolve_intent(request))
        contract_block = render_prompt_contract_block(contract, context={
            "scene_id": scope.get("scene_id"),
            "scene_packet_id": scope.get("scene_packet_id"),
            "player_character_id": scope.get("player_character_id"),
        })
        lines = [
            "## Neo Roleplay Control Center Brief",
            "The backend LLM is the performer. The Control Center is the scene director, continuity supervisor, and context balancer.",
            "Use this brief as higher-priority roleplay guidance, not as a lore dump.",
            "",
            contract_block,
            "",
            "## Active scene",
            f"Scene id: {scope.get('scene_id') or 'default'}",
            f"Scene title: {scope.get('scene_title') or setup.get('title') or packet.get('title') or 'Untitled Scene'}",
            f"Runtime bundle: {scope.get('runtime_bundle_id') or 'none'}",
            f"Scene packet: {scope.get('scene_packet_id') or 'none'}",
            f"Universe: {scope.get('universe_id') or 'not specified'}",
            f"World: {scope.get('world_id') or 'not specified'}",
            f"Location: {scope.get('location_id') or 'not specified'}",
            "",
            "## Character control contract",
            f"User/player controls: {player}",
            f"Assistant/model controls: {npc_line}",
            "Do not write the player character's dialogue, thoughts, feelings, decisions, or physical actions unless the user explicitly requests co-writing.",
            "Write narration and NPC dialogue only. Use character names for dialogue when characters speak.",
            "",
            "## Canon grounding rules",
            "Treat Active Scene Packet, canon locks, scene state, and recent transcript as confirmed.",
            "If a detail is not specified, do not invent it. Mark it unknown or leave it unstated.",
            "Do not invent appearances, injuries, facial expressions, physical positions, relationship status, or who holds an object unless present in the packet/state/user turn.",
            "Use private NPC knowledge only to shape behavior; do not reveal it unless the scene state allows it.",
            "",
            "## Response format",
            "Output only the in-scene reply. Do not output XML, markdown report cards, analysis fields, or meta labels such as Response, Brief immersive situation, Character Name, Scene description, Dialogue, Additional notes, or Character control rules.",
            "Use only these lanes when needed:",
            "Narration:",
            "<brief observable environment/action beat>",
            "",
            "Assistant-Controlled Character Name:",
            '"<dialogue>"',
            "",
            "## Selected context lanes",
        ]
        items = roleplay_context.get("items") if isinstance(roleplay_context.get("items"), list) else []
        for idx, item in enumerate(items[:18], 1):
            lane = item.get("lane") or "memory"
            title = item.get("title") or "Untitled"
            content = item.get("content") or ""
            trust = item.get("trust") or "memory"
            lines.append(f"{idx}. [{lane} / {trust}] {title}: {_clean_text(content, limit=520)}")
        if not items:
            lines.append("No scoped roleplay context was selected. Stay anchored to Scene setup and ask for clarification if needed.")
        prompt_block = "\n".join(lines).strip()
        return {
            "strategy": "scene_director_compact_brief",
            "prompt_block": prompt_block,
            "line_count": len(lines),
            "item_count": len(items),
            "max_memory_dump_policy": "Do not send full universe memory; send selected scene-director lanes only.",
        }

    def _prompt_contract(self, scope: dict[str, Any], intent: str) -> dict[str, Any]:
        contract_id = resolve_roleplay_contract_id(intent)
        contract = get_prompt_contract(contract_id, fallback=ROLEPLAY_CC_CONTRACT_ID)
        contract["phase"] = ROLEPLAY_CC_PHASE
        contract["intent"] = intent
        contract["scope"] = {k: scope.get(k) for k in ["scene_id", "scene_packet_id", "universe_id", "world_id", "location_id"]}
        return contract

    def _validation_plan(self, scope: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "planned",
            "checks": [
                "does_not_speak_for_player_character",
                "does_not_invent_appearance_or_injury",
                "does_not_move_absent_characters",
                "does_not_contradict_scene_packet",
                "uses_narration_plus_character_dialogue_lanes",
                "does_not_reveal_private_knowledge_without_scene_permission",
            ],
            "review_required_for": ["canon_change", "relationship_state_change", "character_secret_reveal", "player_character_action"],
        }

    def _writeback_plan(self, scope: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "planned_only",
            "low_risk_auto_write": ["turn_summary", "scene_event_candidate", "unresolved_thread_candidate"],
            "review_required": ["canon_fact_change", "relationship_state_change", "character_knowledge_change"],
            "target_tables": ["rp_turn_writebacks", "neo_memory_events", "neo_memory_fragments"],
            "scope": {k: scope.get(k) for k in ["scene_id", "scene_packet_id", "universe_id", "world_id", "location_id"]},
        }

    def _merge_trace_metadata(self, trace_id: str, extra: dict[str, Any]) -> None:
        try:
            with self._connect_memory() as conn:
                row = conn.execute("SELECT metadata_json FROM neo_control_center_traces WHERE trace_id = ?", (trace_id,)).fetchone()
                if not row:
                    return
                current = _safe_json(row["metadata_json"], {})
                if not isinstance(current, dict):
                    current = {}
                current.update(extra)
                conn.execute("UPDATE neo_control_center_traces SET metadata_json = ? WHERE trace_id = ?", (_json(current), trace_id))
        except Exception:
            return


_ROLEPLAY_CC: RoleplayControlCenter | None = None


def get_roleplay_control_center() -> RoleplayControlCenter:
    global _ROLEPLAY_CC
    if _ROLEPLAY_CC is None:
        _ROLEPLAY_CC = RoleplayControlCenter(DEFAULT_DB_PATH)
    return _ROLEPLAY_CC


def roleplay_control_status_payload() -> dict[str, Any]:
    return get_roleplay_control_center().status()


def roleplay_control_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_roleplay_control_center().plan(payload or {}, persist=True)


def roleplay_control_context_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_roleplay_control_center().context(payload or {}, persist=True)


def roleplay_control_traces_payload(limit: int = 25, scene_id: str | None = None, scope_id: str | None = None) -> dict[str, Any]:
    return get_roleplay_control_center().list_traces(limit=limit, scene_id=scene_id, scope_id=scope_id)
