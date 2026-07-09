from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


CONTROL_CENTER_SCHEMA_ID = "neo.control_center.foundation.v1"
CONTROL_CENTER_PHASE = "M5"

ROLEPLAY_KEYWORDS = {
    "roleplay", "scene", "canon", "character", "npc", "player", "universe", "world", "lore", "dialogue",
    "ren", "kael", "mira", "vow", "registry", "omega", "alpha", "beta",
}
IMAGE_KEYWORDS = {"image", "prompt", "negative", "sampler", "seed", "cfg", "lora", "model", "generate", "photo", "style"}
CAPTION_KEYWORDS = {"caption", "tag", "captioning", "batch caption", "image description"}
PROMPT_KEYWORDS = {"prompt", "preset", "keyword", "negative prompt", "source text", "prompt studio"}
ASSISTANT_KEYWORDS = {"assistant", "project", "workspace", "brief", "client", "advice", "workflow"}


@dataclass(slots=True)
class ControlCenterRequest:
    controller: str = "assistant"
    user_input: str = ""
    surface: str | None = None
    project_id: str | None = None
    scope_id: str | None = None
    scope_type: str | None = None
    scope_key: str | None = None
    intent: str | None = None
    backend_profile_id: str | None = None
    prompt_contract_id: str | None = None
    memory_limit: int = 8
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ControlCenterRequest":
        payload = payload or {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return cls(
            controller=str(payload.get("controller") or "assistant"),
            user_input=str(payload.get("user_input") or payload.get("message") or payload.get("query") or ""),
            surface=(str(payload.get("surface")) if payload.get("surface") else None),
            project_id=(str(payload.get("project_id")) if payload.get("project_id") else None),
            scope_id=(str(payload.get("scope_id")) if payload.get("scope_id") else None),
            scope_type=(str(payload.get("scope_type")) if payload.get("scope_type") else None),
            scope_key=(str(payload.get("scope_key")) if payload.get("scope_key") else None),
            intent=(str(payload.get("intent")) if payload.get("intent") else None),
            backend_profile_id=(str(payload.get("backend_profile_id")) if payload.get("backend_profile_id") else None),
            prompt_contract_id=(str(payload.get("prompt_contract_id")) if payload.get("prompt_contract_id") else None),
            memory_limit=max(1, min(int(payload.get("memory_limit") or 8), 50)),
            metadata=metadata,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _clean_text(value: Any, *, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_@#'’.-]{2,}", text or "")[:80]]


class NeoControlCenter:
    """Shared Phase M5 orchestration foundation.

    This class does not generate LLM output yet. It builds and records the control
    trace that later Assistant and Roleplay controllers will use: intent, scope,
    memory query plan, balanced context, prompt contract choice, validation shell,
    and memory writeback plan. It is deliberately SQLite-first and additive.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='neo_control_center_traces'"
            ).fetchone() is not None
            total = 0
            recent: list[dict[str, Any]] = []
            if table_exists:
                total = int(conn.execute("SELECT COUNT(*) FROM neo_control_center_traces").fetchone()[0])
                rows = conn.execute(
                    """
                    SELECT trace_id, controller, surface, project_id, scope_id, intent, status, created_at, metadata_json
                    FROM neo_control_center_traces
                    ORDER BY created_at DESC
                    LIMIT 8
                    """
                ).fetchall()
                recent = [self._trace_summary(row) for row in rows]
        return {
            "schema_id": CONTROL_CENTER_SCHEMA_ID,
            "phase": CONTROL_CENTER_PHASE,
            "status": "ready" if table_exists else "missing_schema",
            "label": "Shared Control Center Foundation",
            "trace_table": "neo_control_center_traces",
            "trace_count": total,
            "recent_traces": recent,
            "controllers": [
                {"id": "assistant", "label": "Assistant Control Center", "status": "foundation"},
                {"id": "roleplay", "label": "Roleplay Control Center", "status": "foundation"},
                {"id": "image", "label": "Image Workspace Control", "status": "foundation"},
                {"id": "prompt_captioning", "label": "Prompt/Captioning Control", "status": "foundation"},
            ],
            "policy": "M5 records orchestration traces only. It does not bypass memory, force huge prompts, or call the backend LLM yet.",
            "endpoints": {
                "status": "/api/control-center/status",
                "plan": "/api/control-center/plan",
                "traces": "/api/control-center/traces",
                "trace_detail": "/api/control-center/traces/{trace_id}",
            },
        }

    def plan(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        request = ControlCenterRequest.from_payload(payload)
        stamp = _now()
        intent = self._resolve_intent(request)
        scope = self._resolve_scope(request, intent)
        memory_plan = self._build_memory_query_plan(request, intent, scope)
        selected_context = self._select_context(memory_plan, limit=request.memory_limit)
        prompt_contract = self._resolve_prompt_contract(request, intent, scope)
        validation = self._build_validation_plan(request, intent, scope, selected_context)
        writeback_plan = self._build_writeback_plan(request, intent, scope)
        trace_id = f"control_{stamp.replace('-', '').replace(':', '').replace('.', '')}_{uuid4().hex[:8]}"
        trace = {
            "trace_id": trace_id,
            "schema_id": CONTROL_CENTER_SCHEMA_ID,
            "phase": CONTROL_CENTER_PHASE,
            "controller": request.controller,
            "surface": scope.get("surface") or "global",
            "project_id": scope.get("project_id"),
            "scope_id": scope.get("scope_id"),
            "intent": intent,
            "user_input": request.user_input,
            "scope": scope,
            "memory_query_plan": memory_plan,
            "selected_context": selected_context,
            "prompt_contract_id": prompt_contract.get("contract_id") or "",
            "prompt_contract": prompt_contract,
            "backend_profile_id": request.backend_profile_id or "",
            "validation": validation,
            "writeback_plan": writeback_plan,
            "status": "planned",
            "created_at": stamp,
            "metadata": {
                **request.metadata,
                "request_hash": _hash_text(request.user_input),
                "persisted": bool(persist),
                "control_policy": "Memory retrieves, Control Center decides, backend performs, validator checks, memory evolves.",
            },
        }
        if persist:
            self.record_trace(trace)
        return {"status": "planned", "trace": trace}

    def record_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        stamp = str(trace.get("created_at") or _now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_control_center_traces (
                    trace_id, controller, surface, project_id, scope_id, intent, user_input,
                    memory_sources_json, selected_context_json, prompt_contract_id, backend_profile_id,
                    validation_json, writeback_plan_json, status, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.get("trace_id"),
                    trace.get("controller") or "assistant",
                    trace.get("surface") or "global",
                    trace.get("project_id"),
                    trace.get("scope_id"),
                    trace.get("intent") or "",
                    trace.get("user_input") or "",
                    _json(trace.get("memory_query_plan") or {}),
                    _json(trace.get("selected_context") or {}),
                    trace.get("prompt_contract_id") or "",
                    trace.get("backend_profile_id") or "",
                    _json(trace.get("validation") or {}),
                    _json(trace.get("writeback_plan") or {}),
                    trace.get("status") or "planned",
                    stamp,
                    _json({
                        **(trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}),
                        "scope": trace.get("scope") or {},
                        "prompt_contract": trace.get("prompt_contract") or {},
                        "schema_id": trace.get("schema_id") or CONTROL_CENTER_SCHEMA_ID,
                        "phase": trace.get("phase") or CONTROL_CENTER_PHASE,
                    }),
                ),
            )
        return {"status": "recorded", "trace_id": trace.get("trace_id")}

    def list_traces(self, *, limit: int = 25, controller: str | None = None, surface: str | None = None) -> dict[str, Any]:
        limit = max(1, min(int(limit or 25), 100))
        clauses: list[str] = []
        params: list[Any] = []
        if controller:
            clauses.append("controller = ?")
            params.append(controller)
        if surface:
            clauses.append("surface = ?")
            params.append(surface)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT trace_id, controller, surface, project_id, scope_id, intent, status, created_at, metadata_json
                FROM neo_control_center_traces
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return {"status": "ok", "traces": [self._trace_summary(row) for row in rows], "count": len(rows)}

    def trace_detail(self, trace_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM neo_control_center_traces WHERE trace_id = ?", (trace_id,)).fetchone()
        if row is None:
            return {"status": "not_found", "trace_id": trace_id}
        detail = dict(row)
        return {
            "status": "ok",
            "trace": {
                "trace_id": detail.get("trace_id"),
                "controller": detail.get("controller"),
                "surface": detail.get("surface"),
                "project_id": detail.get("project_id"),
                "scope_id": detail.get("scope_id"),
                "intent": detail.get("intent"),
                "user_input": detail.get("user_input"),
                "memory_query_plan": _safe_json_loads(detail.get("memory_sources_json"), {}),
                "selected_context": _safe_json_loads(detail.get("selected_context_json"), {}),
                "prompt_contract_id": detail.get("prompt_contract_id"),
                "backend_profile_id": detail.get("backend_profile_id"),
                "validation": _safe_json_loads(detail.get("validation_json"), {}),
                "writeback_plan": _safe_json_loads(detail.get("writeback_plan_json"), {}),
                "status": detail.get("status"),
                "created_at": detail.get("created_at"),
                "metadata": _safe_json_loads(detail.get("metadata_json"), {}),
            },
        }

    def _resolve_intent(self, request: ControlCenterRequest) -> str:
        if request.intent:
            return request.intent
        text = request.user_input.lower()
        if request.controller == "roleplay" or any(word in text for word in ROLEPLAY_KEYWORDS):
            if any(word in text for word in ("summarize", "explain", "canon", "what happened", "recap")):
                return "roleplay.canon_question"
            if any(word in text for word in ("continue", "says", "asks", "walks", "looks", "touches")):
                return "roleplay.scene_turn"
            return "roleplay.scene_context"
        if any(word in text for word in IMAGE_KEYWORDS):
            return "workspace.image_advice"
        if any(word in text for word in CAPTION_KEYWORDS):
            return "workspace.caption_advice"
        if any(word in text for word in PROMPT_KEYWORDS):
            return "workspace.prompt_advice"
        if any(word in text for word in ASSISTANT_KEYWORDS):
            return "assistant.workspace_advice"
        return "assistant.general"

    def _resolve_scope(self, request: ControlCenterRequest, intent: str) -> dict[str, Any]:
        surface = request.surface
        if not surface:
            if intent.startswith("roleplay"):
                surface = "roleplay"
            elif "image" in intent:
                surface = "image"
            elif "caption" in intent or "prompt" in intent:
                surface = "prompt_captioning"
            else:
                surface = "assistant"
        scope = {
            "surface": surface,
            "project_id": request.project_id,
            "scope_id": request.scope_id,
            "scope_type": request.scope_type or ("scene" if surface == "roleplay" else "project"),
            "scope_key": request.scope_key,
            "sandbox_policy": "strict_surface_project_scope",
        }
        if not scope.get("scope_id"):
            scope["scope_id"] = self._lookup_scope_id(scope)
        return scope

    def _lookup_scope_id(self, scope: dict[str, Any]) -> str | None:
        clauses = ["surface = ?"]
        params: list[Any] = [scope.get("surface") or "global"]
        if scope.get("project_id"):
            clauses.append("project_id = ?")
            params.append(scope.get("project_id"))
        if scope.get("scope_type"):
            clauses.append("scope_type = ?")
            params.append(scope.get("scope_type"))
        if scope.get("scope_key"):
            clauses.append("scope_key = ?")
            params.append(scope.get("scope_key"))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT scope_id FROM neo_memory_scopes WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return str(row[0]) if row else None

    def _build_memory_query_plan(self, request: ControlCenterRequest, intent: str, scope: dict[str, Any]) -> dict[str, Any]:
        tokens = _tokenize(request.user_input)
        required_lanes = ["recent_context", "surface_memory"]
        if intent.startswith("roleplay"):
            required_lanes = ["canon_memory", "scene_state", "character_memory", "relationship_memory", "recent_turns"]
        elif "image" in intent:
            required_lanes = ["image_generation_metadata", "successful_settings", "prompt_patterns"]
        elif "prompt" in intent or "caption" in intent:
            required_lanes = ["saved_outputs", "instructions", "keyword_patterns"]
        return {
            "query": request.user_input,
            "tokens": tokens,
            "surface": scope.get("surface"),
            "project_id": scope.get("project_id"),
            "scope_id": scope.get("scope_id"),
            "lanes": required_lanes,
            "retrieval_order": ["metadata_filter", "scope_filter", "keyword_search", "sqlite_vector_search", "rerank_shortlist", "control_center_selection"],
            "embedding_required_now": True,
            "rerank_required_now": True,
            "policy": "M9 uses hybrid retrieval + reranker as advisory selection. Control Center still decides what reaches the prompt.",
        }

    def _select_context(self, memory_plan: dict[str, Any], *, limit: int) -> dict[str, Any]:
        # M9 path: use unified hybrid retrieval when available. This keeps the
        # Control Center as the selector/decision layer while Memory Retrieval
        # handles metadata filters, keyword recall, SQLite vector recall, and
        # reranking. Fallback preserves M5 behavior if the retrieval engine is
        # unavailable during development.
        try:
            from neo_app.memory.retrieval_engine import UnifiedMemoryRetrievalEngine

            profile = "roleplay_runtime" if str(memory_plan.get("surface") or "") == "roleplay" else "assistant_project"
            retrieval = UnifiedMemoryRetrievalEngine(self.db_path).retrieve({
                "query": memory_plan.get("query") or "",
                "surface": memory_plan.get("surface") or "",
                "project_id": memory_plan.get("project_id") or "",
                "scope_id": memory_plan.get("scope_id") or "",
                "profile": profile,
                "consumer": "control_center",
                "limit": limit,
                "candidate_limit": max(limit * 4, 24),
                "rerank_top": min(limit, 12),
                "semantic": True,
                "rerank": True,
            })
            context_items = []
            for row in retrieval.get("results") or []:
                context_items.append({
                    "fragment_id": row.get("fragment_id"),
                    "surface": row.get("surface"),
                    "project_id": row.get("project_id"),
                    "scope_id": row.get("scope_id"),
                    "memory_type": row.get("memory_type"),
                    "title": row.get("title"),
                    "content_preview": _clean_text(row.get("content") or row.get("snippet"), max_chars=500),
                    "importance": row.get("priority"),
                    "trust_level": row.get("trust_level"),
                    "source_id": row.get("source_id"),
                    "score": row.get("score"),
                    "retrieval_type": row.get("retrieval_type"),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                })
            safety = self._apply_safety_guard(memory_plan, context_items, source_id=retrieval.get("trace_id") or "")
            safe_items = safety.get("accepted_items") if isinstance(safety.get("accepted_items"), list) else context_items
            return {
                "selection_mode": "m9_hybrid_retrieval_rerank",
                "item_count": len(safe_items),
                "items": safe_items,
                "retrieval_trace_id": retrieval.get("trace_id"),
                "retrieval_diagnostics": {
                    "backend_used": retrieval.get("backend_used"),
                    "semantic": retrieval.get("semantic"),
                    "reranker": retrieval.get("reranker"),
                    "stats": retrieval.get("stats"),
                },
                "safety_guard": {k: safety.get(k) for k in ("status", "violation_count", "rejected_count", "accepted_count")},
                "budget_policy": {
                    "phase": "M9+M12",
                    "send_all_memory": False,
                    "description": "Control Center receives a compact, reranked memory brief after M12 sandbox validation.",
                },
            }
        except Exception as exc:
            rows = self._query_memory_fragments(memory_plan, limit=limit)
            context_items = []
            for row in rows:
                metadata = _safe_json_loads(row.get("metadata_json"), {})
                context_items.append({
                    "fragment_id": row.get("fragment_id"),
                    "surface": row.get("surface"),
                    "project_id": row.get("project_id"),
                    "scope_id": row.get("scope_id"),
                    "memory_type": row.get("memory_type"),
                    "title": row.get("title"),
                    "content_preview": _clean_text(row.get("content"), max_chars=500),
                    "importance": row.get("priority"),
                    "trust_level": row.get("trust_level"),
                    "source_id": row.get("source_id"),
                    "metadata": metadata,
                })
            safety = self._apply_safety_guard(memory_plan, context_items, source_id="fallback_sqlite")
            safe_items = safety.get("accepted_items") if isinstance(safety.get("accepted_items"), list) else context_items
            return {
                "selection_mode": "scoped_sqlite_keyword_preview_fallback",
                "item_count": len(safe_items),
                "items": safe_items,
                "fallback_error": str(exc)[:500],
                "safety_guard": {k: safety.get(k) for k in ("status", "violation_count", "rejected_count", "accepted_count")},
                "budget_policy": {
                    "phase": CONTROL_CENTER_PHASE + "+M12",
                    "send_all_memory": False,
                    "description": "Control Center selected fallback SQLite context then applied M12 sandbox validation.",
                },
            }


    def _apply_safety_guard(self, memory_plan: dict[str, Any], context_items: list[dict[str, Any]], *, source_id: str = "") -> dict[str, Any]:
        try:
            from neo_app.memory.safety_guard import MemorySafetyGuard

            return MemorySafetyGuard(self.db_path).validate_context({
                "surface": memory_plan.get("surface") or "global",
                "project_id": memory_plan.get("project_id") or None,
                "scope_id": memory_plan.get("scope_id") or None,
                "source_type": "control_center_context_selection",
                "source_id": source_id or "control_center",
                "items": context_items,
                "allow_cross_surface": bool(memory_plan.get("allow_cross_surface")),
                "allow_cross_project": bool(memory_plan.get("allow_cross_project")),
                "allow_scope_expansion": bool(memory_plan.get("allow_scope_expansion")),
            })
        except Exception as exc:
            return {"ok": True, "status": "guard_unavailable", "error": str(exc)[:400], "accepted_items": context_items, "violation_count": 0, "accepted_count": len(context_items), "rejected_count": 0}

    def _query_memory_fragments(self, plan: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        clauses = ["status = 'active'"]
        where_params: list[Any] = []
        surface = plan.get("surface")
        if surface:
            clauses.append("surface = ?")
            where_params.append(surface)
        if plan.get("project_id"):
            clauses.append("project_id = ?")
            where_params.append(plan.get("project_id"))
        if plan.get("scope_id"):
            clauses.append("scope_id = ?")
            where_params.append(plan.get("scope_id"))
        tokens = [t for t in plan.get("tokens") or [] if len(t) >= 3][:8]
        score_expr = "0"
        score_params: list[Any] = []
        if tokens:
            token_clauses = []
            score_parts = []
            for token in tokens:
                like = f"%{token}%"
                token_clauses.append("(lower(content) LIKE ? OR lower(title) LIKE ?)")
                where_params.extend([like, like])
                score_parts.append("CASE WHEN lower(content) LIKE ? OR lower(title) LIKE ? THEN 1 ELSE 0 END")
                score_params.extend([like, like])
            clauses.append("(" + " OR ".join(token_clauses) + ")")
            score_expr = " + ".join(score_parts)
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    f"""
                    SELECT *, ({score_expr}) AS match_score
                    FROM neo_memory_fragments
                    WHERE {' AND '.join(clauses)}
                    ORDER BY match_score DESC, priority DESC, created_at DESC
                    LIMIT ?
                    """,
                    (*score_params, *where_params, limit),
                ).fetchall()
                if not rows and tokens:
                    fallback_clauses = [clause for clause in clauses if "lower(content) LIKE" not in clause and "lower(title) LIKE" not in clause]
                    # Surface/project/scope filters still apply. This fallback gives the
                    # Control Center a visible scoped context preview even when the
                    # user's wording is vague and M9 semantic retrieval is not active yet.
                    fallback_params = [
                        param for idx, param in enumerate(where_params)
                        if idx < len(where_params) - (len(tokens) * 2)
                    ]
                    rows = conn.execute(
                        f"""
                        SELECT *, 0 AS match_score
                        FROM neo_memory_fragments
                        WHERE {' AND '.join(fallback_clauses)}
                        ORDER BY priority DESC, created_at DESC
                        LIMIT ?
                        """,
                        (*fallback_params, limit),
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        return [dict(row) for row in rows]

    def _resolve_prompt_contract(self, request: ControlCenterRequest, intent: str, scope: dict[str, Any]) -> dict[str, Any]:
        if request.prompt_contract_id:
            contract_id = request.prompt_contract_id
        elif intent.startswith("roleplay"):
            contract_id = "roleplay_control_center_foundation_v1"
        elif intent.startswith("workspace.image"):
            contract_id = "assistant_image_workspace_advice_foundation_v1"
        elif intent.startswith("workspace.prompt") or intent.startswith("workspace.caption"):
            contract_id = "assistant_prompt_caption_workspace_foundation_v1"
        else:
            contract_id = "assistant_workspace_control_foundation_v1"
        return {
            "contract_id": contract_id,
            "phase": CONTROL_CENTER_PHASE,
            "status": "foundation_stub",
            "description": "M5 chooses the contract lane; M8 will implement full prompt contract rendering and validation rules.",
            "output_lanes": ["context_brief", "backend_instruction", "validation_plan"],
        }

    def _build_validation_plan(self, request: ControlCenterRequest, intent: str, scope: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        checks = ["scope_not_mixed", "memory_sources_visible", "context_budget_visible"]
        if intent.startswith("roleplay"):
            checks.extend(["player_character_not_controlled", "no_unspecified_appearance_invention", "canon_not_contradicted", "private_npc_knowledge_not_revealed_unless_allowed"])
        else:
            checks.extend(["answer_uses_relevant_project_memory_only", "uncertainty_stated_when_memory_missing"])
        return {
            "phase": CONTROL_CENTER_PHASE,
            "status": "planned",
            "checks": checks,
            "context_item_count": context.get("item_count", 0),
            "policy": "M5 records validation intent. Enforcement begins in Assistant/Roleplay-specific Control Center phases.",
        }

    def _build_writeback_plan(self, request: ControlCenterRequest, intent: str, scope: dict[str, Any]) -> dict[str, Any]:
        planned = []
        if intent.startswith("roleplay"):
            planned = ["scene_event_candidate", "character_state_candidate", "timeline_candidate"]
        elif intent.startswith("workspace"):
            planned = ["workflow_preference_candidate", "successful_setting_candidate", "project_pattern_candidate"]
        else:
            planned = ["assistant_interaction_candidate"]
        return {
            "phase": CONTROL_CENTER_PHASE,
            "status": "planned_not_applied",
            "planned_memory_types": planned,
            "requires_review_for": ["canon_change", "relationship_change", "user_preference_change", "cross_project_memory"],
            "policy": "M5 never writes inferred facts automatically from the model response; it only plans writeback lanes.",
        }

    def _trace_summary(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        metadata = _safe_json_loads(data.get("metadata_json"), {})
        return {
            "trace_id": data.get("trace_id"),
            "controller": data.get("controller"),
            "surface": data.get("surface"),
            "project_id": data.get("project_id"),
            "scope_id": data.get("scope_id"),
            "intent": data.get("intent"),
            "status": data.get("status"),
            "created_at": data.get("created_at"),
            "phase": metadata.get("phase") or CONTROL_CENTER_PHASE,
        }
