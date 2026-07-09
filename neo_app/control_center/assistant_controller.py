from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.assistant.contracts import clamp_retrieval_profile, normalize_surface_id, trim_text
from neo_app.assistant.store import assistant_profile, get_project, list_projects
from neo_app.control_center.service import NeoControlCenter
from neo_app.control_center.prompt_contracts import (
    get_prompt_contract,
    render_prompt_contract_block,
    resolve_assistant_contract_id,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"

ASSISTANT_CC_PHASE = "M6"
ASSISTANT_CC_SCHEMA_ID = "neo.assistant.control_center.v1"
ASSISTANT_CC_CONTRACT_ID = "assistant_project_memory_answer_v1"

SURFACE_HINTS: dict[str, set[str]] = {
    "image": {"image", "photo", "generate", "lora", "sampler", "seed", "cfg", "negative", "checkpoint", "model", "portrait"},
    "prompt_captioning": {"prompt", "caption", "captioning", "keyword", "tag", "negative prompt", "source text", "batch caption"},
    "roleplay": {"roleplay", "scene", "canon", "universe", "world", "character", "npc", "dialogue", "kael", "ren", "mira", "vow", "registry"},
    "assistant": {"assistant", "project", "workspace", "workflow", "client", "advice", "plan", "debug", "fix"},
    "admin": {"admin", "backend", "embedding", "reranker", "chroma", "memory engine", "provider", "profile"},
}

SURFACE_MEMORY_LANES: dict[str, list[str]] = {
    "image": ["image_generation_metadata", "prompt_patterns", "successful_settings", "model_settings", "failure_patterns"],
    "prompt_captioning": ["saved_outputs", "caption_outputs", "prompt_patterns", "keyword_patterns", "instruction_patterns"],
    "roleplay": ["roleplay_project_memory", "canon_memory", "scene_state", "character_memory", "timeline_memory"],
    "assistant": ["project_memory", "assistant_captures", "workspace_context", "surface_handoffs", "recent_assistant_thread"],
    "admin": ["system_records", "admin_config", "memory_engine_health", "backend_profiles", "diagnostics"],
}


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


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_@#'’.:-]{2,}", text or "")[:80]]


@dataclass(slots=True)
class AssistantControlRequest:
    message: str = ""
    project_id: str = ""
    session_id: str = ""
    surface: str = "assistant"
    active_surface: str = ""
    retrieval_profile: str = "smart"
    memory_limit: int = 8
    backend_profile_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "AssistantControlRequest":
        payload = payload or {}
        profile = assistant_profile()
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        message = str(payload.get("message") or payload.get("text") or payload.get("query") or "")
        project_id = str(payload.get("project_id") or profile.get("default_project_id") or "general")
        surface = str(payload.get("surface") or payload.get("active_surface") or "").strip()
        return cls(
            message=message,
            project_id=project_id or "general",
            session_id=str(payload.get("session_id") or ""),
            surface=normalize_surface_id(surface, default="assistant"),
            active_surface=normalize_surface_id(payload.get("active_surface") or surface, default="assistant"),
            retrieval_profile=clamp_retrieval_profile(payload.get("retrieval_profile") or profile.get("retrieval_profile") or "smart"),
            memory_limit=max(1, min(int(payload.get("memory_limit") or 8), 40)),
            backend_profile_id=str(payload.get("backend_profile_id") or payload.get("profile_id") or ""),
            metadata=metadata,
        )


class AssistantControlCenter:
    """Assistant-specific M6 control layer.

    The shared M5 Control Center can plan generic traces. This M6 wrapper applies
    Assistant rules: project/surface sandboxing, assistant memory lanes, compact
    workspace briefs, and diagnostics that can be injected before Assistant LLM
    generation. It does not replace the Memory Engine or model provider.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.shared = NeoControlCenter(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def status(self) -> dict[str, Any]:
        shared = self.shared.status()
        with self._connect() as conn:
            trace_count = 0
            recent: list[dict[str, Any]] = []
            try:
                trace_count = int(conn.execute("SELECT COUNT(*) FROM neo_control_center_traces WHERE controller = 'assistant'").fetchone()[0])
                rows = conn.execute(
                    """
                    SELECT trace_id, surface, project_id, scope_id, intent, status, created_at, metadata_json
                    FROM neo_control_center_traces
                    WHERE controller = 'assistant'
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
            "schema_id": ASSISTANT_CC_SCHEMA_ID,
            "phase": ASSISTANT_CC_PHASE,
            "status": "ready" if shared.get("status") == "ready" else shared.get("status"),
            "label": "Assistant Control Center",
            "shared_control_center": {"status": shared.get("status"), "phase": shared.get("phase")},
            "trace_count": trace_count,
            "recent_traces": recent,
            "policy": {
                "assistant_is_brain": True,
                "sandbox_memory_by_surface_project": True,
                "no_cross_project_memory_by_default": True,
                "send_all_memory": False,
                "llm_role": "performer_and_reasoner",
                "control_center_role": "workspace_director_and_context_balancer",
            },
            "endpoints": {
                "status": "/api/assistant/control-center/status",
                "plan": "/api/assistant/control-center/plan",
                "context": "/api/assistant/control-center/context",
                "traces": "/api/assistant/control-center/traces",
            },
        }

    def plan(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        request = AssistantControlRequest.from_payload(payload)
        surface = self._resolve_surface(request)
        project = get_project(request.project_id) or {"project_id": request.project_id, "name": request.project_id or "General", "type": "general"}
        intent = self._resolve_intent(request, surface)
        contract_id = resolve_assistant_contract_id(surface, intent)
        shared_payload = {
            "controller": "assistant",
            "user_input": request.message,
            "surface": surface,
            "project_id": request.project_id,
            "scope_type": "project",
            "scope_key": request.project_id,
            "intent": intent,
            "backend_profile_id": request.backend_profile_id,
            "prompt_contract_id": contract_id,
            "memory_limit": request.memory_limit,
            "metadata": {
                **request.metadata,
                "assistant_cc_phase": ASSISTANT_CC_PHASE,
                "active_surface": request.active_surface,
                "retrieval_profile": request.retrieval_profile,
            },
        }
        shared_plan = self.shared.plan(shared_payload, persist=persist)
        trace = shared_plan.get("trace") if isinstance(shared_plan.get("trace"), dict) else {}
        assistant_plan = {
            "schema_id": ASSISTANT_CC_SCHEMA_ID,
            "phase": ASSISTANT_CC_PHASE,
            "status": "planned",
            "controller": "assistant",
            "trace_id": trace.get("trace_id"),
            "intent": intent,
            "surface": surface,
            "active_surface": request.active_surface,
            "project": self._project_summary(project),
            "retrieval_profile": request.retrieval_profile,
            "memory_lanes": SURFACE_MEMORY_LANES.get(surface, SURFACE_MEMORY_LANES["assistant"]),
            "selected_context": trace.get("selected_context") or {},
            "prompt_contract": self._prompt_contract(surface, intent),
            "context_brief": self._build_context_brief(request, surface, project, trace),
            "validation_plan": self._assistant_validation_plan(surface, intent),
            "writeback_plan": self._assistant_writeback_plan(request, surface),
            "shared_trace": trace,
        }
        if persist and trace.get("trace_id"):
            self._merge_trace_metadata(trace.get("trace_id"), {"assistant_control_center": {k: v for k, v in assistant_plan.items() if k not in {"shared_trace"}}})
        return {"ok": True, "status": "planned", "plan": assistant_plan, "trace": trace}

    def context(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        plan_payload = self.plan(payload, persist=persist)
        plan = plan_payload.get("plan") or {}
        context_brief = plan.get("context_brief") if isinstance(plan.get("context_brief"), dict) else {}
        prompt_block = str(context_brief.get("prompt_block") or "").strip()
        return {
            "ok": True,
            "status": "ready",
            "schema_id": ASSISTANT_CC_SCHEMA_ID,
            "phase": ASSISTANT_CC_PHASE,
            "trace_id": plan.get("trace_id"),
            "prompt_block": prompt_block,
            "messages": [{"role": "system", "content": prompt_block}] if prompt_block else [],
            "plan": plan,
            "diagnostics": {
                "surface": plan.get("surface"),
                "project_id": (plan.get("project") or {}).get("project_id"),
                "intent": plan.get("intent"),
                "selected_context_count": ((plan.get("selected_context") or {}).get("item_count") if isinstance(plan.get("selected_context"), dict) else 0),
                "contract_id": (plan.get("prompt_contract") or {}).get("contract_id"),
                "retrieval_profile": plan.get("retrieval_profile"),
                "policy": "Assistant Control Center builds compact workspace brief before the backend LLM call.",
            },
        }

    def list_traces(self, *, limit: int = 25, project_id: str | None = None, surface: str | None = None) -> dict[str, Any]:
        clauses = ["controller = 'assistant'"]
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if surface:
            clauses.append("surface = ?")
            params.append(surface)
        with self._connect() as conn:
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
        return {"ok": True, "status": "ok", "traces": traces, "count": len(traces)}

    def record_generation_result(self, trace_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if not trace_id:
            return {"ok": False, "status": "missing_trace_id"}
        result_meta = {
            "generation_result": {
                "recorded_at": _now(),
                "ok": bool(result.get("ok")),
                "status": result.get("status") or "unknown",
                "backend_profile_id": result.get("backend_profile_id") or "",
                "provider_id": result.get("provider_id") or "",
                "model": result.get("model") or "",
                "output_chars": len(str(result.get("text") or result.get("reply") or "")),
                "validation": self._post_generation_validation(result),
            }
        }
        self._merge_trace_metadata(trace_id, result_meta)
        return {"ok": True, "status": "recorded", "trace_id": trace_id, "metadata": result_meta}

    def _resolve_surface(self, request: AssistantControlRequest) -> str:
        explicit = normalize_surface_id(request.surface or request.active_surface, default="")
        if explicit and explicit != "assistant":
            return explicit if explicit in SURFACE_MEMORY_LANES else "assistant"
        text = request.message.lower()
        best = ("assistant", 0)
        for surface, hints in SURFACE_HINTS.items():
            score = sum(1 for hint in hints if hint in text)
            if score > best[1]:
                best = (surface, score)
        return best[0]

    def _resolve_intent(self, request: AssistantControlRequest, surface: str) -> str:
        text = request.message.lower()
        if any(word in text for word in ("debug", "fix", "error", "not working", "broken")):
            return f"assistant.{surface}.debug"
        if any(word in text for word in ("plan", "phase", "roadmap", "implement", "architecture")):
            return f"assistant.{surface}.planning"
        if any(word in text for word in ("summarize", "recap", "what happened")):
            return f"assistant.{surface}.summary"
        if any(word in text for word in ("improve", "better", "optimize", "settings", "suggest")):
            return f"assistant.{surface}.advice"
        return f"assistant.{surface}.answer"

    def _project_summary(self, project: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": project.get("project_id") or "general",
            "name": project.get("name") or "General",
            "type": project.get("type") or "general",
            "description": _clean_text(project.get("description"), limit=500),
            "notes_preview": _clean_text(project.get("notes"), limit=500),
        }

    def _prompt_contract(self, surface: str, intent: str) -> dict[str, Any]:
        contract_id = resolve_assistant_contract_id(surface, intent)
        contract = get_prompt_contract(contract_id, fallback=ASSISTANT_CC_CONTRACT_ID)
        contract["surface"] = surface
        contract["intent"] = intent
        contract["phase"] = ASSISTANT_CC_PHASE
        return contract

    def _build_context_brief(self, request: AssistantControlRequest, surface: str, project: dict[str, Any], shared_trace: dict[str, Any]) -> dict[str, Any]:
        selected = shared_trace.get("selected_context") if isinstance(shared_trace.get("selected_context"), dict) else {}
        items = selected.get("items") if isinstance(selected.get("items"), list) else []
        context_lines = []
        for idx, item in enumerate(items[: request.memory_limit], start=1):
            title = item.get("title") or item.get("fragment_id") or f"Memory {idx}"
            lane = item.get("memory_type") or "memory"
            preview = _clean_text(item.get("content_preview") or item.get("summary") or item.get("content"), limit=420)
            if preview:
                context_lines.append(f"[{idx}] ({lane}) {title}: {preview}")
        if not context_lines:
            context_lines.append("No scoped memory matched strongly. Use current message and project context; state uncertainty where needed.")
        project_summary = self._project_summary(project)
        contract = self._prompt_contract(surface, shared_trace.get("intent") or "assistant.answer")
        contract_block = render_prompt_contract_block(contract, context={"surface": surface, "project_id": project_summary.get("project_id")})
        prompt_block = "\n".join([
            "# Neo Assistant Control Center Brief",
            f"Phase: {ASSISTANT_CC_PHASE}",
            f"Active surface sandbox: {surface}",
            f"Active project: {project_summary.get('name')} ({project_summary.get('project_id')})",
            f"Project type: {project_summary.get('type')}",
            f"User request: {trim_text(request.message, 2200)}",
            "",
            contract_block,
            "",
            "## Project notes",
            project_summary.get("description") or "No project description stored.",
            project_summary.get("notes_preview") or "No project notes stored.",
            "",
            "## Selected scoped memory",
            "\n".join(context_lines),
        ]).strip()
        return {
            "contract_id": ASSISTANT_CC_CONTRACT_ID,
            "prompt_block": prompt_block,
            "selected_context_count": len(items),
            "memory_budget": {"max_items": request.memory_limit, "send_all_memory": False, "strategy": "compact_control_brief"},
        }

    def _assistant_validation_plan(self, surface: str, intent: str) -> dict[str, Any]:
        checks = [
            "answer_uses_active_surface_scope",
            "answer_does_not_claim_missing_memory_as_fact",
            "answer_has_actionable_next_step",
            "answer_marks_uncertainty_when_context_thin",
        ]
        if surface == "roleplay":
            checks += ["does_not_mix_universes", "does_not_override_roleplay_player_control"]
        if surface == "image":
            checks += ["separates_prompt_advice_from_backend_capability", "does_not_invent_generation_success"]
        return {"status": "planned", "intent": intent, "checks": checks, "phase": ASSISTANT_CC_PHASE}

    def _assistant_writeback_plan(self, request: AssistantControlRequest, surface: str) -> dict[str, Any]:
        return {
            "status": "planned_only",
            "phase": ASSISTANT_CC_PHASE,
            "surface": surface,
            "low_risk_auto_write": ["assistant_control_trace", "retrieval_scope_used"],
            "review_required": ["new_durable_user_preference", "cross_project_claim", "high_impact_project_fact"],
            "deferred_to": "M11 Memory Writeback + Evolution",
        }

    def _post_generation_validation(self, result: dict[str, Any]) -> dict[str, Any]:
        text = str(result.get("text") or result.get("reply") or "")
        warnings = []
        if not text.strip():
            warnings.append("empty_output")
        if len(text) > 12000:
            warnings.append("long_output_review_recommended")
        return {"status": "warning" if warnings else "passed", "warnings": warnings, "phase": ASSISTANT_CC_PHASE}

    def _merge_trace_metadata(self, trace_id: str, extra: dict[str, Any]) -> None:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT metadata_json FROM neo_control_center_traces WHERE trace_id = ?", (trace_id,)).fetchone()
                if not row:
                    return
                current = _safe_json(row[0], {})
                if not isinstance(current, dict):
                    current = {}
                current.update(extra)
                conn.execute("UPDATE neo_control_center_traces SET metadata_json = ? WHERE trace_id = ?", (_json(current), trace_id))
        except Exception:
            return


_ASSISTANT_CC: AssistantControlCenter | None = None


def get_assistant_control_center() -> AssistantControlCenter:
    global _ASSISTANT_CC
    if _ASSISTANT_CC is None:
        _ASSISTANT_CC = AssistantControlCenter(DEFAULT_DB_PATH)
    return _ASSISTANT_CC


def assistant_control_status_payload() -> dict[str, Any]:
    return get_assistant_control_center().status()


def assistant_control_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_assistant_control_center().plan(payload or {}, persist=True)


def assistant_control_context_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_assistant_control_center().context(payload or {}, persist=True)


def assistant_control_traces_payload(limit: int = 25, project_id: str | None = None, surface: str | None = None) -> dict[str, Any]:
    return get_assistant_control_center().list_traces(limit=limit, project_id=project_id, surface=surface)
