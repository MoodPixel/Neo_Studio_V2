from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from uuid import uuid4
import ast
import hashlib
import json
import re

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.extensions.runtime import extension_memory_event_contract

from .optional_semantic import optional_status
from .source_registry import memory_source_registry_payload, get_memory_source, memory_source_definitions
from .retrieval_profiles import get_retrieval_profile, retrieval_profiles_payload
from .policies import memory_policy_payload, normalize_memory_policy, decay_policy_rules_payload
from .vector_store import vector_store_status, upsert_chroma_chunks, query_chroma_chunks
from .schema import MemoryCapabilityStatus, MemoryEvent, MemoryQuery, MemorySearchResult
from .store_sqlite import SQLiteMemoryStore
from .surface_ingestion import run_surface_memory_ingestion
from .consolidation_engine import UnifiedMemoryConsolidationEngine
from .retrieval_engine import UnifiedMemoryRetrievalEngine
from .observability import MemoryObservabilityEngine
from .writeback_engine import MemoryWritebackEngine
from .safety_guard import MemorySafetyGuard
from neo_app.control_center import NeoControlCenter
from neo_app.control_center.prompt_contracts import prompt_contract_status_payload
from neo_app.control_center.trace_review import ControlCenterTraceReviewEngine

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"


def _tool_registry_status_safe() -> dict:
    try:
        from neo_app.tool_registry import tool_registry_status_payload
        return tool_registry_status_payload()
    except Exception as exc:  # pragma: no cover - defensive diagnostics only
        return {"status": "unavailable", "error": str(exc)[:400]}


def _tool_ledger_status_safe() -> dict:
    try:
        from neo_app.tool_ledger import tool_ledger_status_payload
        return tool_ledger_status_payload()
    except Exception as exc:  # pragma: no cover - defensive diagnostics only
        return {"status": "unavailable", "error": str(exc)[:400]}


def _project_workspace_status_safe() -> dict:
    try:
        from neo_app.project_workspace import project_workspace_status_payload
        return project_workspace_status_payload()
    except Exception as exc:  # pragma: no cover - defensive diagnostics only
        return {"status": "unavailable", "error": str(exc)[:400]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _safe_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        clean = line.strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip() or fallback
    return fallback


def _chunk_markdown(text: str, *, max_chars: int = 2200) -> list[dict[str, Any]]:
    lines = text.splitlines()
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    start_line = 1
    title = "Overview"

    def flush(end_line: int) -> None:
        nonlocal current, start_line, title
        content = "\n".join(current).strip()
        if content:
            chunks.append({"title": title, "content": content, "start_line": start_line, "end_line": end_line})
        current = []

    for idx, line in enumerate(lines, start=1):
        clean = line.strip()
        is_heading = clean.startswith("#")
        if is_heading and current:
            flush(idx - 1)
            start_line = idx
        if not current:
            start_line = idx
        if is_heading:
            title = clean.lstrip("#").strip() or title
        current.append(line)
        if sum(len(part) + 1 for part in current) >= max_chars:
            flush(idx)
            start_line = idx + 1
    if current:
        flush(len(lines) or 1)
    return chunks or [{"title": "Document", "content": text.strip(), "start_line": 1, "end_line": len(lines) or 1}]


_CODE_SECTION_RE = re.compile(r"^(?:async\s+)?function\s+([A-Za-z0-9_$]+)|^(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(?[^=]*\)?\s*=>|^([A-Za-z0-9_.$]+)\s*=\s*function\b|^\s*([.#]?[A-Za-z0-9_-][^{]{0,120})\s*\{")
_CSS_BLOCK_RE = re.compile(r"([^{}]+)\{")


def _line_no_from_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _slice_lines(lines: list[str], start_line: int, end_line: int) -> str:
    return "\n".join(lines[max(0, start_line - 1): max(start_line - 1, end_line)]).strip()


def _fallback_code_chunks(text: str, *, title: str, max_lines: int = 90) -> list[dict[str, Any]]:
    lines = text.splitlines()
    chunks: list[dict[str, Any]] = []
    for start in range(1, len(lines) + 1, max_lines):
        end = min(len(lines), start + max_lines - 1)
        content = _slice_lines(lines, start, end)
        if content:
            chunks.append({"title": f"{title} lines {start}-{end}", "content": content, "start_line": start, "end_line": end, "symbol_type": "block"})
    return chunks or [{"title": title, "content": text.strip(), "start_line": 1, "end_line": len(lines) or 1, "symbol_type": "file"}]


def _chunk_python_code(text: str, *, path: Path) -> list[dict[str, Any]]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _fallback_code_chunks(text, title=path.name)
    candidates: list[tuple[int, int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = int(getattr(node, "lineno", 1) or 1)
            end = int(getattr(node, "end_lineno", start) or start)
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            candidates.append((start, end, kind, getattr(node, "name", kind)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    chunks: list[dict[str, Any]] = []
    module_header = []
    for line in lines[:80]:
        if line.startswith("class ") or line.startswith("def ") or line.startswith("async def "):
            break
        module_header.append(line)
    if "\n".join(module_header).strip():
        chunks.append({"title": f"{path.name} module overview", "content": "\n".join(module_header).strip(), "start_line": 1, "end_line": len(module_header), "symbol_type": "module"})
    for start, end, kind, name in candidates:
        effective_end = end
        if kind == "class" and (end - start) > 80:
            effective_end = start + 80
        elif kind == "function" and (end - start) > 180:
            effective_end = start + 180
        content = _slice_lines(lines, start, effective_end)
        if content:
            chunks.append({"title": f"{kind} {name}", "content": content, "start_line": start, "end_line": effective_end, "symbol_type": kind, "symbol_name": name})
    return chunks or _fallback_code_chunks(text, title=path.name)


def _chunk_js_code(text: str, *, path: Path) -> list[dict[str, Any]]:
    lines = text.splitlines()
    matches: list[tuple[int, str]] = []
    for match in _CODE_SECTION_RE.finditer(text):
        name = next((group for group in match.groups() if group), "section")
        start = _line_no_from_offset(text, match.start())
        if start not in [m[0] for m in matches]:
            matches.append((start, str(name).strip()))
    matches = sorted(matches, key=lambda item: item[0])[:260]
    chunks: list[dict[str, Any]] = []
    for idx, (start, name) in enumerate(matches):
        next_start = matches[idx + 1][0] if idx + 1 < len(matches) else len(lines) + 1
        end = min(len(lines), max(start, next_start - 1, start + 8))
        if end - start > 160:
            end = start + 159
        content = _slice_lines(lines, start, end)
        if content:
            chunks.append({"title": f"JS {name}", "content": content, "start_line": start, "end_line": end, "symbol_type": "javascript", "symbol_name": name})
    return chunks or _fallback_code_chunks(text, title=path.name)


def _chunk_css_code(text: str, *, path: Path) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for match in _CSS_BLOCK_RE.finditer(text):
        selector = " ".join(match.group(1).split())[-120:]
        if selector and not selector.startswith("/*"):
            starts.append((_line_no_from_offset(text, match.start()), selector))
    starts = sorted(starts, key=lambda item: item[0])[:260]
    chunks: list[dict[str, Any]] = []
    for idx, (start, selector) in enumerate(starts):
        next_start = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines) + 1
        end = min(len(lines), max(start, next_start - 1, start + 2))
        if end - start > 120:
            end = start + 119
        content = _slice_lines(lines, start, end)
        if content:
            chunks.append({"title": f"CSS {selector}", "content": content, "start_line": start, "end_line": end, "symbol_type": "css", "symbol_name": selector})
    return chunks or _fallback_code_chunks(text, title=path.name)


def _chunk_code_file(text: str, *, path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _chunk_python_code(text, path=path)
    if suffix == ".js":
        return _chunk_js_code(text, path=path)
    if suffix == ".css":
        return _chunk_css_code(text, path=path)
    return _fallback_code_chunks(text, title=path.name)


def _code_source_kind(path: Path) -> str:
    parts = set(path.parts)
    if "tests" in parts:
        return "test"
    if "neo_extensions" in parts:
        return "extension_code"
    if "neo_ui" in parts:
        return "ui_contract"
    if path.suffix.lower() in {".js", ".css"}:
        return "frontend"
    return "application_code"


_IMPORTANCE_SCORE = {"low": 0.2, "normal": 0.45, "high": 0.78, "critical": 1.0}
_TRUST_SCORE = {"deprecated": 0.05, "draft": 0.35, "inferred": 0.55, "confirmed": 0.82, "system": 1.0}


def _normalize_score(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _recency_score(updated_at: str | None) -> float:
    if not updated_at:
        return 0.25
    try:
        stamp = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (_now_dt() - stamp).total_seconds() / 86400.0)
        return round(1.0 / (1.0 + (age_days / 90.0)), 6)
    except Exception:
        return 0.25


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _importance_score(item: dict[str, Any]) -> float:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    importance = str(metadata.get("importance") or item.get("importance") or "normal").lower()
    trust = str(item.get("trust_level") or metadata.get("trust_level") or "confirmed").lower()
    policy_score = _normalize_score(item.get("policy_score"), 0.5)
    base = _IMPORTANCE_SCORE.get(importance, _IMPORTANCE_SCORE["normal"])
    trust_bonus = _TRUST_SCORE.get(trust, 0.6) * 0.25
    return round(max(0.0, min(1.0, base * 0.45 + trust_bonus + policy_score * 0.30)), 6)


def _apply_memory_policy(source_id: str, item: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(item or {})
    metadata = merged.get("metadata") if isinstance(merged.get("metadata"), dict) else {}
    policy_overrides: dict[str, Any] = {}
    if isinstance(metadata.get("memory_policy"), dict):
        policy_overrides.update(metadata.get("memory_policy") or {})
    if overrides:
        policy_overrides.update(overrides)
    for key in ("retention_scope", "memory_state", "visibility", "trust_level", "importance", "approval_state"):
        if merged.get(key) not in (None, ""):
            policy_overrides.setdefault(key, merged.get(key))
    policy = normalize_memory_policy(source_id, policy_overrides)
    merged["policy"] = policy
    merged["retention_scope"] = policy["retention_scope"]
    merged["memory_state"] = policy["memory_state"]
    merged["visibility"] = policy["visibility"]
    merged["trust_level"] = policy["trust_level"]
    merged["importance"] = policy["importance"]
    merged["approval_state"] = policy["approval_state"]
    merged["policy_score"] = policy["policy_score"]
    return merged


def _policy_allowed(item: dict[str, Any], *, include_drafts: bool, include_deprecated: bool, include_conflicts: bool, require_approved: bool) -> bool:
    state = str(item.get("memory_state") or "active")
    trust = str(item.get("trust_level") or "confirmed")
    approval = str(item.get("approval_state") or "not_required")
    if state == "draft" and not include_drafts:
        return False
    if state in {"deprecated", "archived"} and not include_deprecated:
        return False
    if state == "conflicting" and not include_conflicts:
        return False
    if trust in {"deprecated", "conflicting"} and not include_conflicts:
        return False
    if require_approved and approval not in {"approved", "not_required"}:
        return False
    if approval == "rejected":
        return False
    return True


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in results:
        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id:
            continue
        existing = merged.get(chunk_id)
        if not existing:
            merged[chunk_id] = dict(item)
            continue
        existing_types = set(str(existing.get("retrieval_type") or "").split("+"))
        incoming_type = str(item.get("retrieval_type") or "unknown")
        existing_types.add(incoming_type)
        existing["retrieval_type"] = "+".join(sorted(t for t in existing_types if t))
        existing["keyword_score"] = max(_normalize_score(existing.get("keyword_score")), _normalize_score(item.get("keyword_score")))
        existing["vector_score"] = max(_normalize_score(existing.get("vector_score")), _normalize_score(item.get("vector_score")))
        existing["score"] = max(_normalize_score(existing.get("score")), _normalize_score(item.get("score")))
        if not existing.get("content") and item.get("content"):
            existing["content"] = item.get("content")
    return list(merged.values())



class MemoryService:
    """Global V2 memory service.

    Required layer: SQLite event memory.
    Optional layer: Chroma + sentence-transformers semantic memory. Codebase memory
    indexing keeps SQLite chunks authoritative and mirrors vectors when configured.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.store = SQLiteMemoryStore(db_path)
        self.consolidation_engine = UnifiedMemoryConsolidationEngine(db_path)
        self.control_center = NeoControlCenter(db_path)
        self.retrieval_engine = UnifiedMemoryRetrievalEngine(db_path)
        self.observability_engine = MemoryObservabilityEngine(db_path, root_dir=ROOT_DIR)
        self.writeback_engine = MemoryWritebackEngine(db_path)
        self.safety_guard = MemorySafetyGuard(db_path)
        self.control_center_trace_review = ControlCenterTraceReviewEngine(db_path)

    def capabilities(self) -> MemoryCapabilityStatus:
        return MemoryCapabilityStatus(**optional_status())


    def source_registry(self, include_disabled: bool = True) -> dict:
        payload = memory_source_registry_payload(include_disabled=include_disabled)
        stamp = _now()
        for source in payload.get("sources", []):
            self.store.upsert_source(source, updated_at=stamp)
        payload["indexed_sources"] = self.store.list_sources()
        payload["stats"] = self.store.document_stats()
        return payload

    def memory_engine_status(self) -> dict:
        capabilities = model_to_dict(self.capabilities())
        sources = self.source_registry(include_disabled=True)
        return {
            "schema_id": "neo.memory.engine.status.v1",
            "status": "ready",
            "label": "Memory Engine",
            "capabilities": capabilities,
            "sources": sources,
            "vector_store": vector_store_status(),
            "stats": self.store.document_stats(),
            "unified_schema": self.store.unified_schema_status(),
            "surface_ingestion": {
                "status": "ready",
                "phase": "M3",
                "run_endpoint": "/api/memory/surface-ingestion/run",
                "report_paths": {
                    "json": "neo_data/memory/audits/m3_surface_memory_ingestion.json",
                    "markdown": "neo_data/memory/audits/m3_surface_memory_ingestion.md",
                },
                "policy": "Surface ingestion is additive/idempotent and stores SQLite memory first; embeddings are queued later.",
            },
            "retrieval_profiles": retrieval_profiles_payload(),
            "retrieval_rerank": self.retrieval_rerank_status(),
            "memory_writeback": self.writeback_status(),
            "memory_safety": self.safety_status(),
            "memory_policies": self.memory_policies(),
            "recent_traces": self.store.list_retrieval_traces(limit=5),
            "inspector": {
                "status": "ready",
                "chunks_endpoint": "/api/memory/inspect/chunks",
                "review_endpoint": "/api/memory/inspect/review",
                "trace_detail_endpoint": "/api/memory/inspect/retrieval-traces/{trace_id}",
                "allowed_actions": ["mark_canon", "mark_draft", "approve", "reject", "deprecate", "flag_conflict", "archive", "restore_active"],
            },
            "conflict_resolver": {
                "status": "ready",
                "groups_endpoint": "/api/memory/conflicts",
                "group_detail_endpoint": "/api/memory/conflicts/{group_id}",
                "resolve_endpoint": "/api/memory/conflicts/resolve",
                "canon_endpoint": "/api/memory/canon",
                "allowed_actions": ["promote_canonical", "mark_all_canon", "deprecate_others", "flag_group_conflict", "archive_group", "mark_draft", "restore_active"],
            },
            "control_center": self.control_center.status(),
            "prompt_contracts": prompt_contract_status_payload(),
            "consolidation": {
                "status": "ready",
                "phase": "M4",
                "legacy_plan_endpoint": "/api/memory/consolidation/plan",
                "legacy_run_endpoint": "/api/memory/consolidation/run",
                "unified_status_endpoint": "/api/memory/unified-consolidation/status",
                "unified_plan_endpoint": "/api/memory/unified-consolidation/plan",
                "unified_run_endpoint": "/api/memory/unified-consolidation/run",
                "summary_source_id": "memory_consolidation",
                "allowed_actions": ["create_summary", "create_summary_archive_originals"],
                "policy": "Phase M4 writes deterministic SQLite summaries to neo_memory_summaries and mirrors them as consolidated_summary fragments. Legacy chunk consolidation remains available for older indexed docs.",
                "unified_status": self.consolidation_engine.status(),
            },
            "retention_automation": {
                "status": "ready",
                "plan_endpoint": "/api/memory/retention/plan",
                "run_endpoint": "/api/memory/retention/run",
                "rules_endpoint": "/api/memory/retention/rules",
                "allowed_actions": ["archive_temporary", "deprecate_stale_external", "mark_for_review", "keep"],
                "policy": "Retention automation is advisory and Admin-reviewed. It never deletes memory and never silently mutates canon/system/source-backed memory.",
            },
            "assistant_source_grounding": {
                "status": "ready",
                "endpoint": "/api/assistant/source-grounded-answer",
                "policy": "Assistant source-grounded answers use Memory Search UX citations and should state uncertainty when evidence is missing.",
            },
            "assistant_action_review": {
                "status": "ready",
                "endpoints": ["/api/assistant/action-review/status", "/api/assistant/action-review/plan", "/api/assistant/action-review/run"],
                "policy": "Assistant action plans expose planned actions, permission gates, read-only labels, blocked actions, and Operator execution results before/after any confirmed action.",
            },
            "tool_registry": _tool_registry_status_safe(),
            "tool_execution_ledger": _tool_ledger_status_safe(),
            "project_workspace": {
                **_project_workspace_status_safe(),
                "index_endpoint": "/api/projects/workspace/index",
                "context_endpoint": "/api/projects/workspace/context",
                "active_endpoint": "/api/projects/workspace/active",
                "brief_save_endpoint": "/api/projects/workspace/brief/save",
                "brief_export_endpoint": "/api/projects/workspace/brief/export",
            },
            "search_ux": {
                "status": "ready",
                "search_endpoint": "/api/memory/search-ux",
                "citation_endpoint": "/api/memory/citations/{chunk_id}",
                "source_viewer_endpoint": "/api/memory/source-viewer",
                "related_endpoint": "/api/memory/related-chunks/{chunk_id}",
                "compare_endpoint": "/api/memory/consolidation/compare/{chunk_id}",
                "policy": "Search UX is read-only. Source citations are restricted to files inside the Neo workspace or indexed chunk content.",
            },
            "policy": "SQLite document/chunk storage is authoritative. Chroma is a semantic mirror when configured in Admin Memory Engine.",
        }


    def health_dashboard(self) -> dict:
        """Memory Health + Diagnostics dashboard payload for Admin.

        This is read-only diagnostics. It does not index, mutate, or call external
        providers. The goal is to make Neo's memory brain visible and debuggable.
        """
        stats = self.store.document_stats()
        diagnostics = self.store.diagnostics_snapshot(stale_days=14, trace_limit=12)
        vector = vector_store_status()
        policies = self.memory_policies()
        profiles = self.retrieval_profiles()

        try:
            from neo_app.operator.service import operator_status_payload
            operator = operator_status_payload()
        except Exception as exc:  # pragma: no cover - defensive diagnostics only
            operator = {"status": "unavailable", "error": str(exc)[:400]}
        try:
            from neo_app.voice.service import voice_input_status_payload
            voice = voice_input_status_payload()
        except Exception as exc:  # pragma: no cover
            voice = {"status": "unavailable", "error": str(exc)[:400]}
        try:
            from neo_app.internet.service import internet_access_status_payload
            internet = internet_access_status_payload()
        except Exception as exc:  # pragma: no cover
            internet = {"status": "unavailable", "error": str(exc)[:400]}
        try:
            from neo_app.roleplay.human_memory import roleplay_human_memory_state_payload
            roleplay = roleplay_human_memory_state_payload()
        except Exception as exc:  # pragma: no cover
            roleplay = {"status": "unavailable", "error": str(exc)[:400]}
        project_workspace = _project_workspace_status_safe()

        source_health = diagnostics.get("source_health") or []
        ready_sources = [item for item in source_health if item.get("status") == "ready"]
        stale_sources = [item for item in source_health if item.get("status") == "stale"]
        missing_index_sources = [item for item in source_health if item.get("status") == "needs_index"]
        policy_alerts = diagnostics.get("policy_alerts") or []
        trace_quality = diagnostics.get("retrieval_quality") or {}
        internet_mode = internet.get("mode") or "disabled"
        vector_status = vector.get("status") or vector.get("mode") or "unknown"

        checks = [
            {"id": "sources_indexed", "label": "Indexed sources", "status": "ready" if ready_sources else "needs_index", "detail": f"{len(ready_sources)} ready · {len(missing_index_sources)} need indexing · {len(stale_sources)} stale"},
            {"id": "vector_store", "label": "Vector store", "status": "ready" if vector_status in {"ready", "available"} else vector_status, "detail": vector.get("policy") or vector.get("reason") or "SQLite remains authoritative."},
            {"id": "retrieval_traces", "label": "Retrieval traces", "status": "ready" if trace_quality.get("recent_trace_count") else "quiet", "detail": f"{trace_quality.get('recent_trace_count', 0)} recent trace(s), {trace_quality.get('recent_rejected_count', 0)} rejected candidate(s)"},
            {"id": "memory_policies", "label": "Memory policies", "status": "needs_review" if policy_alerts else "ready", "detail": f"{len(policy_alerts)} policy alert bucket(s)"},
            {"id": "operator", "label": "Neo Operator", "status": operator.get("status") or "unknown", "detail": f"Input modes: {', '.join(operator.get('input_modes') or [])}"},
            {"id": "voice_input", "label": "Voice input", "status": voice.get("status") or "unknown", "detail": "Transcriber configured" if (voice.get("model") or {}).get("configured") else "Ready for transcript text; local transcriber needs setup"},
            {"id": "internet_access", "label": "Optional Internet/API", "status": internet_mode, "detail": f"Mode: {internet_mode}. Provider count: {(internet.get('capabilities') or {}).get('enabled_provider_count', 0)}/{(internet.get('capabilities') or {}).get('provider_count', 0)}"},
            {"id": "roleplay_continuity", "label": "Roleplay continuity", "status": roleplay.get("status") or "unknown", "detail": f"Scene packets: {(roleplay.get('counts') or {}).get('scene_memory_packets', 0)} · Character states: {(roleplay.get('counts') or {}).get('character_states', 0)}"},
            {"id": "project_workspace", "label": "Project Workspace", "status": project_workspace.get("status") or "unknown", "detail": f"Active: {project_workspace.get('active_project_id') or 'general'} · Projects: {project_workspace.get('project_count', 0)} · Context: {project_workspace.get('context_count', 0)}"},
        ]
        return {
            "schema_id": "neo.memory.health_dashboard.v1",
            "status": "ready",
            "label": "Memory Health + Diagnostics",
            "summary": {
                "document_count": stats.get("document_count", 0),
                "chunk_count": stats.get("chunk_count", 0),
                "embedding_ref_count": stats.get("embedding_ref_count", 0),
                "ready_source_count": len(ready_sources),
                "stale_source_count": len(stale_sources),
                "needs_index_source_count": len(missing_index_sources),
                "recent_trace_count": trace_quality.get("recent_trace_count", 0),
                "policy_alert_count": len(policy_alerts),
                "internet_mode": internet_mode,
                "operator_status": operator.get("status"),
                "voice_input_status": voice.get("status"),
                "active_project_id": project_workspace.get("active_project_id"),
                "project_workspace_count": project_workspace.get("project_count", 0),
            },
            "checks": checks,
            "source_health": source_health,
            "vector_store": vector,
            "retrieval_profiles": profiles,
            "retrieval_quality": trace_quality,
            "recent_retrieval_traces": diagnostics.get("recent_retrieval_traces") or [],
            "event_counts_by_namespace": diagnostics.get("event_counts_by_namespace") or {},
            "recent_events": diagnostics.get("recent_events") or [],
            "policy_counts": diagnostics.get("policy_counts") or [],
            "policy_alerts": policy_alerts,
            "operator": operator,
            "voice_input": voice,
            "internet_access": internet,
            "roleplay_continuity": roleplay,
            "project_workspace": project_workspace,
            "policy": "Diagnostics are read-only. Indexing and external actions remain confirmation-gated through Admin and Neo Operator.",
        }

    def retrieval_profiles(self) -> dict:
        payload = retrieval_profiles_payload()
        payload["recent_traces"] = self.store.list_retrieval_traces(limit=8)
        payload["stats"] = self.store.document_stats()
        return payload

    def retrieval_traces(self, limit: int = 20) -> dict:
        return {
            "schema_id": "neo.memory.retrieval_traces.v1",
            "status": "ready",
            "traces": self.store.list_retrieval_traces(limit=limit),
        }

    def run_surface_ingestion(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        surfaces = data.get("surfaces") or data.get("surface")
        if isinstance(surfaces, str):
            surfaces = [surfaces]
        elif not isinstance(surfaces, list):
            surfaces = None
        limit = data.get("limit")
        try:
            limit = int(limit) if limit not in (None, "") else None
        except Exception:
            limit = None
        return run_surface_memory_ingestion(surfaces=surfaces, limit=limit, write_report=bool(data.get("write_report", True)))

    def memory_policies(self) -> dict:
        payload = memory_policy_payload()
        payload["stats"] = self.store.document_stats()
        payload["counts"] = self.store.list_memory_policies()
        return payload

    def update_memory_policy(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        chunk_id = str(data.get("chunk_id") or "").strip()
        if not chunk_id:
            return {"ok": False, "status": "missing_chunk_id"}
        return self.store.update_chunk_policy(chunk_id, data.get("policy") or data)


    def inspect_chunks(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        result = self.store.inspect_chunks(
            query=str(data.get("query") or ""),
            source_id=data.get("source_id") or None,
            memory_state=data.get("memory_state") or None,
            trust_level=data.get("trust_level") or None,
            approval_state=data.get("approval_state") or None,
            visibility=data.get("visibility") or None,
            limit=int(data.get("limit") or 25),
            offset=int(data.get("offset") or 0),
        )
        return {
            "ok": True,
            "schema_id": "neo.memory.inspector.chunks.v1",
            "status": "ready",
            "filters": {k: v for k, v in data.items() if k in {"query", "source_id", "memory_state", "trust_level", "approval_state", "visibility", "limit", "offset"}},
            **result,
            "policy": "Inspector is review-only until a policy action is explicitly requested.",
        }

    def inspect_chunk_detail(self, chunk_id: str) -> dict:
        item = self.store.get_chunk_detail(chunk_id)
        if not item:
            return {"ok": False, "status": "missing_chunk", "chunk_id": chunk_id}
        return {
            "ok": True,
            "schema_id": "neo.memory.inspector.chunk_detail.v1",
            "status": "ready",
            "chunk": item,
            "review_actions": ["mark_canon", "mark_draft", "approve", "reject", "deprecate", "flag_conflict", "archive", "restore_active"],
        }

    def inspect_retrieval_trace(self, trace_id: str) -> dict:
        trace = self.store.get_retrieval_trace(trace_id)
        if not trace:
            return {"ok": False, "status": "missing_trace", "trace_id": trace_id}
        return {
            "ok": True,
            "schema_id": "neo.memory.inspector.retrieval_trace.v1",
            "status": "ready",
            "trace": trace,
        }

    def search_ux(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        query = str(data.get("query") or "").strip()
        profile = str(data.get("profile") or "smart").strip() or "smart"
        result = self.retrieve({
            **data,
            "query": query,
            "profile": profile,
            "consumer": str(data.get("consumer") or "memory_search_ux"),
            "limit": max(1, min(int(data.get("limit") or 12), 30)),
        })
        enriched = []
        for item in result.get("results") or []:
            citation = self._citation_payload_from_chunk(item)
            enriched.append({**item, "citation": citation, "viewer_endpoint": citation.get("viewer_endpoint")})
        result["schema_id"] = "neo.memory.search_ux.v1"
        result["search_ux_version"] = "source-citation-viewer.v1"
        result["results"] = enriched
        result["citation_policy"] = "Every result returns chunk ID, source path, line range when available, and a source viewer endpoint."
        return result

    def _citation_payload_from_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        chunk_id = str(chunk.get("chunk_id") or "")
        source_path = str(chunk.get("source_path") or "")
        start = chunk.get("start_line")
        end = chunk.get("end_line") or start
        label = source_path or chunk_id
        if start:
            label = f"{label}:{start}-{end}"
        return {
            "chunk_id": chunk_id,
            "title": chunk.get("title") or "Memory chunk",
            "source_id": chunk.get("source_id") or "unknown",
            "source_path": source_path,
            "start_line": start,
            "end_line": end,
            "label": label,
            "viewer_endpoint": f"/api/memory/citations/{chunk_id}" if chunk_id else "",
            "trust_level": chunk.get("trust_level"),
            "memory_state": chunk.get("memory_state"),
            "approval_state": chunk.get("approval_state"),
        }

    def citation_viewer(self, chunk_id: str) -> dict:
        detail = self.store.get_chunk_detail(chunk_id)
        if not detail:
            return {"ok": False, "status": "missing_chunk", "chunk_id": chunk_id}
        return self.source_viewer({"chunk_id": chunk_id})

    def source_viewer(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        chunk = None
        chunk_id = str(data.get("chunk_id") or "").strip()
        if chunk_id:
            chunk = self.store.get_chunk_detail(chunk_id)
            if not chunk:
                return {"ok": False, "status": "missing_chunk", "chunk_id": chunk_id}
        source_path = str(data.get("source_path") or (chunk or {}).get("source_path") or "").strip()
        start_line = int(data.get("start_line") or (chunk or {}).get("start_line") or 1)
        end_line = int(data.get("end_line") or (chunk or {}).get("end_line") or start_line)
        context = max(0, min(int(data.get("context_lines") or 8), 40))
        viewer = {
            "ok": True,
            "schema_id": "neo.memory.source_viewer.v1",
            "status": "ready",
            "chunk": {k: v for k, v in (chunk or {}).items() if k != "content"} if chunk else None,
            "citation": self._citation_payload_from_chunk(chunk or {"source_path": source_path, "start_line": start_line, "end_line": end_line}),
            "source_path": source_path,
            "requested_range": {"start_line": start_line, "end_line": end_line, "context_lines": context},
            "source_available": False,
            "lines": [],
            "fallback_content": "",
            "policy": "Viewer only reads files inside the Neo workspace. Generated/virtual memory falls back to stored chunk content.",
        }
        if source_path:
            try:
                candidate = (ROOT_DIR / source_path).resolve()
                root = ROOT_DIR.resolve()
                if candidate.exists() and candidate.is_file() and (candidate == root or root in candidate.parents):
                    lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                    lo = max(1, start_line - context)
                    hi = min(len(lines), max(end_line, start_line) + context)
                    viewer["source_available"] = True
                    viewer["resolved_path"] = _safe_rel(candidate)
                    viewer["line_count"] = len(lines)
                    viewer["lines"] = [{"line": idx, "text": lines[idx - 1], "highlight": start_line <= idx <= max(end_line, start_line)} for idx in range(lo, hi + 1)]
                    return viewer
            except Exception as exc:
                viewer["source_error"] = str(exc)[:500]
        if chunk:
            content_lines = str(chunk.get("content") or "").splitlines()
            viewer["fallback_content"] = chunk.get("content") or ""
            viewer["lines"] = [{"line": idx + 1, "text": text, "highlight": True} for idx, text in enumerate(content_lines[:240])]
        return viewer

    def related_chunks(self, chunk_id: str, limit: int = 8) -> dict:
        result = self.store.related_chunks(chunk_id, limit=limit)
        return {
            "ok": True,
            "schema_id": "neo.memory.related_chunks.v1",
            "status": "ready",
            **result,
            "policy": "Related chunks are suggestions based on source path/title proximity. Review before applying policy actions.",
        }

    def compare_consolidation_summary(self, chunk_id: str) -> dict:
        result = self.store.consolidation_sources_for_summary(chunk_id)
        result.setdefault("schema_id", "neo.memory.consolidation.compare.v1")
        result.setdefault("status", "ready" if result.get("ok") else result.get("status", "unavailable"))
        result["policy"] = "Comparison is read-only and preserves source chunk IDs for auditability."
        return result


    def review_memory_chunk(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        chunk_id = str(data.get("chunk_id") or "").strip()
        action = str(data.get("action") or "").strip()
        note = str(data.get("note") or "").strip()
        if not chunk_id:
            return {"ok": False, "status": "missing_chunk_id"}
        action_policies = {
            "mark_canon": {"retention_scope": "canon", "memory_state": "canon", "trust_level": "confirmed", "importance": "high", "approval_state": "approved"},
            "mark_draft": {"retention_scope": "draft", "memory_state": "draft", "trust_level": "draft", "importance": "normal", "approval_state": "pending"},
            "approve": {"approval_state": "approved", "trust_level": "confirmed"},
            "reject": {"approval_state": "rejected", "trust_level": "deprecated", "importance": "low"},
            "deprecate": {"memory_state": "deprecated", "trust_level": "deprecated", "importance": "low", "approval_state": "rejected"},
            "flag_conflict": {"memory_state": "conflicting", "trust_level": "conflicting", "approval_state": "pending", "importance": "high"},
            "archive": {"memory_state": "archived", "importance": "low"},
            "restore_active": {"memory_state": "active", "approval_state": "approved"},
        }
        policy = dict(data.get("policy") or {})
        if action:
            if action not in action_policies:
                return {"ok": False, "status": "unknown_action", "action": action, "allowed_actions": sorted(action_policies)}
            policy = {**action_policies[action], **policy}
        if not policy:
            return {"ok": False, "status": "missing_policy_or_action"}
        updated = self.store.update_chunk_policy(chunk_id, policy)
        if updated.get("ok"):
            try:
                self.record_event({
                    "namespace": "memory",
                    "surface": "admin",
                    "source": "admin",
                    "event_type": "memory.chunk.reviewed",
                    "title": "Memory chunk reviewed",
                    "summary": f"{action or 'policy_update'} applied to {chunk_id}.",
                    "tags": ["memory", "inspector", action or "policy"],
                    "payload": {"chunk_id": chunk_id, "action": action, "policy": policy, "note": note},
                    "importance": "normal",
                    "should_embed": False,
                })
            except Exception:
                pass
            updated["chunk"] = self.store.get_chunk_detail(chunk_id)
            updated["action"] = action or "policy_update"
        return updated



    def conflict_groups(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        groups = self.store.list_conflict_groups(
            query=str(data.get("query") or ""),
            source_id=data.get("source_id") or None,
            limit=int(data.get("limit") or 25),
        )
        return {
            "ok": True,
            "schema_id": "neo.memory.conflicts.v1",
            "status": "ready",
            "conflict_version": "canon-manager.v1",
            "filters": {k: v for k, v in data.items() if k in {"query", "source_id", "limit"}},
            **groups,
            "policy": "Conflict detection is heuristic. Resolution requires explicit Admin action and writes an audit event.",
        }

    def conflict_group_detail(self, group_id: str) -> dict:
        group = self.store.get_conflict_group(group_id)
        if not group:
            return {"ok": False, "status": "missing_group", "group_id": group_id}
        return {
            "ok": True,
            "schema_id": "neo.memory.conflict_group.v1",
            "status": "ready",
            "group": group,
            "resolution_actions": ["promote_canonical", "mark_all_canon", "deprecate_others", "flag_group_conflict", "archive_group", "mark_draft", "restore_active"],
        }

    def resolve_conflict(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        result = self.store.resolve_conflict_group(
            group_id=str(data.get("group_id") or "").strip() or None,
            chunk_ids=data.get("chunk_ids") or [],
            action=str(data.get("action") or ""),
            canonical_chunk_id=str(data.get("canonical_chunk_id") or ""),
            note=str(data.get("note") or ""),
        )
        if result.get("ok"):
            try:
                self.record_event({
                    "namespace": "memory",
                    "surface": "admin",
                    "source": "admin",
                    "event_type": "memory.conflict.resolved",
                    "title": "Memory conflict resolved",
                    "summary": f"{result.get('action')} applied to {result.get('updated_count', 0)} memory chunk(s).",
                    "tags": ["memory", "conflict", "canon", str(result.get("action") or "resolution")],
                    "payload": {"request": data, "result": {k: v for k, v in result.items() if k != "updated"}},
                    "importance": "high",
                    "should_embed": False,
                })
            except Exception:
                pass
        return result

    def canon_manager(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        items = self.store.list_canon_manager_items(
            source_id=data.get("source_id") or None,
            include_candidates=bool(data.get("include_candidates", True)),
            query=str(data.get("query") or ""),
            limit=int(data.get("limit") or 50),
        )
        return {
            "ok": True,
            "schema_id": "neo.memory.canon_manager.v1",
            "status": "ready",
            "filters": {k: v for k, v in data.items() if k in {"query", "source_id", "include_candidates", "limit"}},
            **items,
            "review_actions": ["promote_to_canon", "mark_draft", "flag_conflict", "deprecate"],
            "policy": "Canon Manager promotes memory intentionally. Draft and inferred memories are not canon until reviewed.",
        }

    def promote_canon(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        chunk_id = str(data.get("chunk_id") or "").strip()
        if not chunk_id:
            return {"ok": False, "status": "missing_chunk_id"}
        result = self.review_memory_chunk({"chunk_id": chunk_id, "action": "mark_canon", "note": str(data.get("note") or "Canon Manager promotion.")})
        if result.get("ok"):
            result["schema_id"] = "neo.memory.canon_promote.v1"
            result["status"] = "promoted"
        return result



    def retention_rules(self) -> dict:
        return decay_policy_rules_payload()

    def retention_plan(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        plan = self.store.list_retention_candidates(
            source_id=data.get("source_id") or None,
            query=str(data.get("query") or ""),
            max_age_days=int(data.get("max_age_days") or 30),
            limit=int(data.get("limit") or 50),
        )
        return {
            "ok": True,
            "schema_id": "neo.memory.retention.plan.v1",
            "status": "ready",
            "retention_version": "decay-retention.v1",
            "filters": {k: v for k, v in data.items() if k in {"query", "source_id", "max_age_days", "limit"}},
            "rules": decay_policy_rules_payload().get("rules", {}),
            **plan,
            "policy": "Retention plans are advisory. Admin must explicitly run an action; canon/system/source-backed memories are protected from silent archive/deprecation.",
        }

    def run_retention(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        action = str(data.get("action") or "mark_for_review").strip()
        chunk_ids = [str(item).strip() for item in (data.get("chunk_ids") or []) if str(item or "").strip()]
        if not chunk_ids and data.get("candidate_action"):
            plan = self.retention_plan({
                "source_id": data.get("source_id"),
                "query": data.get("query") or "",
                "max_age_days": int(data.get("max_age_days") or 30),
                "limit": int(data.get("limit") or 100),
            })
            candidate_action = str(data.get("candidate_action") or "")
            chunk_ids = [item.get("chunk_id") for item in plan.get("candidates") or [] if item.get("recommended_action") == candidate_action and item.get("chunk_id")]
        result = self.store.apply_retention_action(chunk_ids=chunk_ids, action=action, note=str(data.get("note") or ""))
        if result.get("ok"):
            try:
                self.record_event({
                    "namespace": "memory",
                    "surface": "admin",
                    "source": "admin",
                    "event_type": "memory.retention.action_applied",
                    "title": "Memory retention action applied",
                    "summary": f"{result.get('action')} applied to {result.get('updated_count', 0)} memory chunk(s).",
                    "tags": ["memory", "retention", "decay", str(result.get("action") or "review")],
                    "payload": {"request": data, "result": {k: v for k, v in result.items() if k != "updated"}},
                    "importance": "normal",
                    "should_embed": False,
                })
            except Exception:
                pass
        result.setdefault("schema_id", "neo.memory.retention.run.v1")
        return result


    def unified_consolidation_status(self) -> dict:
        return self.consolidation_engine.status()

    def unified_consolidation_plan(self, payload: dict[str, Any] | None = None) -> dict:
        return self.consolidation_engine.plan(payload or {})

    def run_unified_consolidation(self, payload: dict[str, Any] | None = None) -> dict:
        result = self.consolidation_engine.run(payload or {})
        if result.get("ok") and result.get("status") == "completed":
            try:
                self.record_event({
                    "namespace": "memory",
                    "surface": "admin",
                    "source": "admin",
                    "event_type": "memory.unified_consolidation.completed",
                    "title": "Unified memory consolidation completed",
                    "summary": f"Created {result.get('created_count', 0)} unified memory summarie(s).",
                    "tags": ["memory", "consolidation", "phase_m4", "unified_schema"],
                    "payload": {"result": {k: v for k, v in result.items() if k not in {"created"}}},
                    "importance": "high",
                    "should_embed": False,
                })
            except Exception:
                pass
        return result

    def consolidation_plan(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        groups = self.store.list_consolidation_candidates(
            source_id=data.get("source_id") or None,
            query=str(data.get("query") or ""),
            min_group_size=int(data.get("min_group_size") or 2),
            limit=int(data.get("limit") or 25),
        )
        return {
            "ok": True,
            "schema_id": "neo.memory.consolidation.plan.v1",
            "status": "ready",
            "consolidation_version": "summary-manager.v1",
            "filters": {k: v for k, v in data.items() if k in {"query", "source_id", "min_group_size", "limit"}},
            **groups,
            "policy": "Consolidation plans are read-only. Summaries require explicit Admin run action; originals remain auditable and are not archived unless requested.",
        }

    def run_consolidation(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        action = str(data.get("action") or "create_summary").strip()
        allowed = {"create_summary", "create_summary_archive_originals"}
        if action not in allowed:
            return {"ok": False, "status": "unknown_action", "action": action, "allowed_actions": sorted(allowed)}
        chunk_ids = [str(item).strip() for item in (data.get("chunk_ids") or []) if str(item or "").strip()]
        if not chunk_ids and data.get("group_id"):
            plan = self.consolidation_plan({"source_id": data.get("source_id"), "query": data.get("query") or "", "limit": 100})
            for group in plan.get("groups") or []:
                if group.get("group_id") == data.get("group_id"):
                    chunk_ids = group.get("chunk_ids") or []
                    break
        result = self.store.create_consolidated_summary(
            chunk_ids=chunk_ids,
            title=str(data.get("title") or "Consolidated memory summary"),
            note=str(data.get("note") or ""),
            archive_originals=(action == "create_summary_archive_originals" or bool(data.get("archive_originals"))),
        )
        if result.get("ok"):
            try:
                self.record_event({
                    "namespace": "memory",
                    "surface": "admin",
                    "source": "admin",
                    "event_type": "memory.consolidation.summary_created",
                    "title": "Memory summary created",
                    "summary": f"Consolidated {result.get('source_chunk_count', 0)} memory chunk(s) into {result.get('summary_chunk_id')}.",
                    "tags": ["memory", "consolidation", "summary"],
                    "payload": {"request": data, "result": {k: v for k, v in result.items() if k not in {"summary", "updated_originals"}}},
                    "importance": "high",
                    "should_embed": False,
                })
            except Exception:
                pass
        result.setdefault("schema_id", "neo.memory.consolidation.run.v1")
        return result

    def index_source(self, source_id: str, *, force: bool = False, limit: int | None = None) -> dict:
        source = get_memory_source(source_id)
        if not source:
            return {"ok": False, "status": "unknown_source", "source_id": source_id, "indexed_documents": 0, "indexed_chunks": 0}
        if source_id == "system_records":
            return self.index_system_records(force=force, limit=limit)
        if source_id == "neo_codebase":
            return self.index_codebase(force=force, limit=limit)
        if source_id == "assistant_memory":
            return self.index_assistant_memory(force=force, limit=limit)
        if source_id == "roleplay_memory":
            return self.index_roleplay_memory(force=force, limit=limit)
        if source_id == "project_workspace":
            return self.index_project_workspace(force=force, limit=limit)
        self.store.upsert_source(source, updated_at=_now())
        return {"ok": True, "status": "registered", "source_id": source_id, "indexed_documents": 0, "indexed_chunks": 0, "note": "This source is registered for a later dedicated indexer phase."}

    def index_codebase(self, *, force: bool = False, limit: int | None = None) -> dict:
        source = get_memory_source("neo_codebase") or {}
        self.store.upsert_source(source, updated_at=_now())
        root = ROOT_DIR / str(source.get("root_path") or ".")
        include_paths = source.get("include_paths") or ["neo_app", "neo_extensions", "neo_ui", "tests"]
        extensions = {str(ext).lower() for ext in (source.get("extensions") or [".py", ".js", ".css"])}
        exclude_dirs = {str(item) for item in (source.get("exclude_dirs") or [])}
        max_file_bytes = int(source.get("max_file_bytes") or 260_000)
        files: list[Path] = []
        for include in include_paths:
            base = (ROOT_DIR / str(include)).resolve()
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                if any(part in exclude_dirs for part in path.parts):
                    continue
                try:
                    if path.stat().st_size > max_file_bytes:
                        continue
                except OSError:
                    continue
                files.append(path)
        files = sorted(set(files), key=lambda item: _safe_rel(item))
        if limit:
            files = files[: max(0, int(limit))]
        stamp = _now()
        total_chunks = 0
        indexed_docs: list[dict[str, Any]] = []
        chroma_chunks: list[dict[str, Any]] = []
        embedding_refs: list[dict[str, Any]] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = _safe_rel(path)
            digest = _hash_text(text)
            doc_id = f"neo_codebase:{_hash_text(rel)[:20]}"
            kind = _code_source_kind(path)
            title = f"{kind}: {rel}"
            document = {
                "document_id": doc_id,
                "source_id": "neo_codebase",
                "source_path": rel,
                "title": title,
                "source_type": kind,
                "content_hash": digest,
                "status": "indexed",
                "visibility": source.get("visibility") or "expert",
                "trust_level": source.get("trust_level") or "confirmed",
                "metadata": {"file_name": path.name, "suffix": path.suffix.lower(), "code_kind": kind},
                "updated_at": stamp,
                "indexed_at": stamp,
            }
            document = _apply_memory_policy(document.get("source_id"), document)
            self.store.upsert_document(document)
            chunks = []
            for idx, raw_chunk in enumerate(_chunk_code_file(text, path=path)):
                content = raw_chunk.get("content") or ""
                if not content.strip():
                    continue
                symbol_name = raw_chunk.get("symbol_name") or raw_chunk.get("title") or path.stem
                symbol_type = raw_chunk.get("symbol_type") or kind
                chunk_hash = _hash_text(f"{rel}:{idx}:{content}")
                chunk = {
                    "chunk_id": f"code:{_hash_text(rel + ':' + str(idx))[:12]}:{chunk_hash[:16]}",
                    "document_id": doc_id,
                    "source_id": "neo_codebase",
                    "chunk_index": idx,
                    "title": raw_chunk.get("title") or title,
                    "content": content,
                    "summary": content[:320].replace("\n", " "),
                    "tags": ["neo_codebase", kind, path.suffix.lower().lstrip(".")],
                    "source_path": rel,
                    "start_line": raw_chunk.get("start_line"),
                    "end_line": raw_chunk.get("end_line"),
                    "content_hash": chunk_hash,
                    "visibility": document["visibility"],
                    "trust_level": document["trust_level"],
                    "searchable_text": " ".join([title, str(raw_chunk.get("title") or ""), str(symbol_name), str(symbol_type), rel, content]),
                    "metadata": {"document_title": title, "code_kind": kind, "symbol_type": symbol_type, "symbol_name": symbol_name, "suffix": path.suffix.lower()},
                    "updated_at": stamp,
                }
                chunk = _apply_memory_policy(chunk.get("source_id"), chunk)
                chunks.append(chunk)
            self.store.replace_document_chunks(doc_id, chunks)
            chroma_chunks.extend(chunks)
            total_chunks += len(chunks)
            indexed_docs.append({"document_id": doc_id, "source_path": rel, "title": title, "chunk_count": len(chunks), "code_kind": kind})
        vector_status = {"status": "skipped", "reason": "no_chunks"}
        if chroma_chunks:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                texts = [chunk["content"] for chunk in chroma_chunks]
                emb = embed_texts(texts, allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors and len(vectors) == len(chroma_chunks):
                    vector_status = upsert_chroma_chunks(chroma_chunks, vectors, source_id="neo_codebase")
                    model_id = str(emb.get("model_id") or emb.get("mode") or "local_hash_embeddings")
                    for chunk, vector in zip(chroma_chunks, vectors):
                        embedding_refs.append({
                            "embedding_id": f"emb:{chunk['chunk_id']}",
                            "chunk_id": chunk["chunk_id"],
                            "source_id": chunk["source_id"],
                            "model_id": model_id,
                            "dimension": len(vector),
                            "vector_store": vector_status.get("store") or "sqlite_reference",
                            "collection_name": vector_status.get("collection") or "",
                            "indexed_at": stamp,
                            "metadata": {"fallback_used": emb.get("fallback_used"), "mode": emb.get("mode")},
                        })
                    self.store.upsert_embedding_refs(embedding_refs)
            except Exception as exc:
                vector_status = {"status": "fallback_indexed", "reason": "embedding_or_chroma_error", "error": str(exc)[:800]}
        return {
            "ok": True,
            "schema_id": "neo.memory.index_result.v1",
            "status": "indexed",
            "source_id": "neo_codebase",
            "root": _safe_rel(root),
            "indexed_documents": len(indexed_docs),
            "indexed_chunks": total_chunks,
            "embedding_refs": len(embedding_refs),
            "vector_store": vector_status,
            "documents": indexed_docs[:120],
            "stats": self.store.document_stats(),
            "policy": "Codebase chunks are stored in SQLite and optionally mirrored to Chroma through Admin Memory Engine settings.",
        }

    def index_system_records(self, *, force: bool = False, limit: int | None = None) -> dict:
        source = get_memory_source("system_records") or {}
        self.store.upsert_source(source, updated_at=_now())
        root = ROOT_DIR / str(source.get("root_path") or "neo_system_records")
        if not root.exists():
            return {"ok": False, "status": "missing_root", "source_id": "system_records", "root": _safe_rel(root), "indexed_documents": 0, "indexed_chunks": 0}
        docs = sorted(path for path in root.rglob("*.md") if path.is_file())
        if limit:
            docs = docs[: max(0, int(limit))]
        total_chunks = 0
        indexed_docs = []
        embedding_refs: list[dict[str, Any]] = []
        chroma_chunks: list[dict[str, Any]] = []
        stamp = _now()
        for path in docs:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            rel = _safe_rel(path)
            digest = _hash_text(text)
            doc_id = f"system_records:{_hash_text(rel)[:20]}"
            title = _markdown_title(text, path.stem.replace("_", " ").title())
            document = {
                "document_id": doc_id,
                "source_id": "system_records",
                "source_path": rel,
                "title": title,
                "source_type": "markdown_records",
                "content_hash": digest,
                "status": "indexed",
                "visibility": source.get("visibility") or "expert",
                "trust_level": source.get("trust_level") or "confirmed",
                "metadata": {"folder": _safe_rel(path.parent), "file_name": path.name},
                "updated_at": stamp,
                "indexed_at": stamp,
            }
            document = _apply_memory_policy(document.get("source_id"), document)
            self.store.upsert_document(document)
            chunks = []
            for idx, raw_chunk in enumerate(_chunk_markdown(text)):
                content = raw_chunk.get("content") or ""
                chunk_hash = _hash_text(f"{rel}:{idx}:{content}")
                chunk = {
                    "chunk_id": f"chunk:{chunk_hash[:24]}",
                    "document_id": doc_id,
                    "source_id": "system_records",
                    "chunk_index": idx,
                    "title": raw_chunk.get("title") or title,
                    "content": content,
                    "summary": content[:260].replace("\n", " "),
                    "tags": ["system_records", path.parent.name.lower()],
                    "source_path": rel,
                    "start_line": raw_chunk.get("start_line"),
                    "end_line": raw_chunk.get("end_line"),
                    "content_hash": chunk_hash,
                    "visibility": document["visibility"],
                    "trust_level": document["trust_level"],
                    "searchable_text": " ".join([title, raw_chunk.get("title") or "", content, rel]),
                    "metadata": {"document_title": title, "folder": path.parent.name},
                    "updated_at": stamp,
                }
                chunk = _apply_memory_policy(chunk.get("source_id"), chunk)
                chunks.append(chunk)
            self.store.replace_document_chunks(doc_id, chunks)
            chroma_chunks.extend(chunks)
            total_chunks += len(chunks)
            indexed_docs.append({"document_id": doc_id, "source_path": rel, "title": title, "chunk_count": len(chunks)})
        vector_status = {"status": "skipped", "reason": "no_chunks"}
        if chroma_chunks:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                texts = [chunk["content"] for chunk in chroma_chunks]
                emb = embed_texts(texts, allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors and len(vectors) == len(chroma_chunks):
                    vector_status = upsert_chroma_chunks(chroma_chunks, vectors, source_id="system_records")
                    model_id = str(emb.get("model_id") or emb.get("mode") or "local_hash_embeddings")
                    for chunk, vector in zip(chroma_chunks, vectors):
                        embedding_refs.append({
                            "embedding_id": f"emb:{chunk['chunk_id']}",
                            "chunk_id": chunk["chunk_id"],
                            "source_id": chunk["source_id"],
                            "model_id": model_id,
                            "dimension": len(vector),
                            "vector_store": vector_status.get("store") or "sqlite_reference",
                            "collection_name": vector_status.get("collection") or "",
                            "indexed_at": stamp,
                            "metadata": {"fallback_used": emb.get("fallback_used"), "mode": emb.get("mode")},
                        })
                    self.store.upsert_embedding_refs(embedding_refs)
            except Exception as exc:
                vector_status = {"status": "fallback_indexed", "reason": "embedding_or_chroma_error", "error": str(exc)[:800]}
        return {
            "ok": True,
            "schema_id": "neo.memory.index_result.v1",
            "status": "indexed",
            "source_id": "system_records",
            "root": _safe_rel(root),
            "indexed_documents": len(indexed_docs),
            "indexed_chunks": total_chunks,
            "embedding_refs": len(embedding_refs),
            "vector_store": vector_status,
            "documents": indexed_docs[:80],
            "stats": self.store.document_stats(),
        }


    def index_assistant_memory(self, *, force: bool = False, limit: int | None = None) -> dict:
        """Index Assistant projects, sessions, captures, and context cards.

        Phase 9 makes Assistant a real Memory Engine consumer. Its local JSON files
        stay useful for UI/history, but this index creates searchable chunks for
        /api/memory/retrieve so context packs can pull Assistant memory through the
        same hybrid pipeline as records and codebase memory.
        """
        source = get_memory_source("assistant_memory") or {}
        self.store.upsert_source(source, updated_at=_now())
        root = ROOT_DIR / str(source.get("root_path") or "neo_data/assistant")
        if not root.exists():
            return {"ok": True, "schema_id": "neo.memory.index_result.v1", "status": "missing_root", "source_id": "assistant_memory", "root": _safe_rel(root), "indexed_documents": 0, "indexed_chunks": 0, "stats": self.store.document_stats()}
        files = [path for path in sorted(root.rglob("*"), key=lambda item: (item.stat().st_mtime if item.exists() else 0, _safe_rel(item)), reverse=True) if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt"}]
        if limit:
            files = files[: max(0, int(limit))]
        stamp = _now()
        indexed_docs: list[dict[str, Any]] = []
        chroma_chunks: list[dict[str, Any]] = []
        embedding_refs: list[dict[str, Any]] = []
        total_chunks = 0
        for path in files:
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = _safe_rel(path)
            folder = path.parent.name
            title = path.stem
            text = raw.strip()
            tags = ["assistant_memory", folder]
            metadata: dict[str, Any] = {"file_name": path.name, "folder": folder, "suffix": path.suffix.lower()}
            try:
                parsed = json.loads(raw) if path.suffix.lower() == ".json" else None
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                title = str(parsed.get("title") or parsed.get("name") or parsed.get("context_id") or parsed.get("capture_id") or parsed.get("session_id") or path.stem)
                project_id = str(parsed.get("project_id") or "")
                if project_id:
                    tags.append(f"project:{project_id}")
                    metadata["project_id"] = project_id
                kind = str(parsed.get("kind") or parsed.get("source") or folder)
                if kind:
                    tags.append(kind)
                    metadata["kind"] = kind
                if parsed.get("text"):
                    text = str(parsed.get("text") or "")
                elif parsed.get("summary"):
                    text = str(parsed.get("summary") or "")
                elif isinstance(parsed.get("messages"), list):
                    parts = []
                    for msg in parsed.get("messages")[-30:]:
                        if isinstance(msg, dict):
                            role = msg.get("role") or "message"
                            body = msg.get("text") or msg.get("content") or ""
                            if body:
                                parts.append(f"{role}: {body}")
                    text = "\n".join(parts) or json.dumps(parsed, ensure_ascii=False, default=str)
                else:
                    text = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
            digest = _hash_text(raw)
            doc_id = f"assistant_memory:{_hash_text(rel)[:20]}"
            document = {
                "document_id": doc_id,
                "source_id": "assistant_memory",
                "source_path": rel,
                "title": title,
                "source_type": f"assistant_{folder}",
                "content_hash": digest,
                "status": "indexed",
                "visibility": source.get("visibility") or "user_visible",
                "trust_level": source.get("trust_level") or "confirmed",
                "metadata": metadata,
                "updated_at": stamp,
                "indexed_at": stamp,
            }
            document = _apply_memory_policy(document.get("source_id"), document)
            self.store.upsert_document(document)
            chunks = _chunk_markdown(text, max_chars=2200) if path.suffix.lower() in {".md", ".txt"} else _chunk_markdown(text, max_chars=1800)
            stored_chunks: list[dict[str, Any]] = []
            for idx, raw_chunk in enumerate(chunks):
                content = str(raw_chunk.get("content") or "").strip()
                if not content:
                    continue
                chunk_hash = _hash_text(f"{rel}:{idx}:{content}")
                chunk = {
                    "chunk_id": f"assistant:{_hash_text(rel + ':' + str(idx))[:12]}:{chunk_hash[:16]}",
                    "document_id": doc_id,
                    "source_id": "assistant_memory",
                    "chunk_index": idx,
                    "title": raw_chunk.get("title") or title,
                    "content": content,
                    "summary": content[:320].replace("\n", " "),
                    "tags": tags,
                    "source_path": rel,
                    "start_line": raw_chunk.get("start_line"),
                    "end_line": raw_chunk.get("end_line"),
                    "content_hash": chunk_hash,
                    "visibility": document["visibility"],
                    "trust_level": document["trust_level"],
                    "searchable_text": " ".join([title, rel, " ".join(tags), content]),
                    "metadata": metadata,
                    "updated_at": stamp,
                }
                chunk = _apply_memory_policy(chunk.get("source_id"), chunk)
                stored_chunks.append(chunk)
            self.store.replace_document_chunks(doc_id, stored_chunks)
            chroma_chunks.extend(stored_chunks)
            total_chunks += len(stored_chunks)
            indexed_docs.append({"document_id": doc_id, "source_path": rel, "title": title, "chunk_count": len(stored_chunks)})
        vector_status = {"status": "skipped", "reason": "no_chunks"}
        if chroma_chunks:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                emb = embed_texts([chunk["content"] for chunk in chroma_chunks], allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors and len(vectors) == len(chroma_chunks):
                    vector_status = upsert_chroma_chunks(chroma_chunks, vectors, source_id="assistant_memory")
                    model_id = str(emb.get("model_id") or emb.get("mode") or "local_hash_embeddings")
                    for chunk, vector in zip(chroma_chunks, vectors):
                        embedding_refs.append({
                            "embedding_id": f"emb:{chunk['chunk_id']}",
                            "chunk_id": chunk["chunk_id"],
                            "source_id": chunk["source_id"],
                            "model_id": model_id,
                            "dimension": len(vector),
                            "vector_store": vector_status.get("store") or "sqlite_reference",
                            "collection_name": vector_status.get("collection") or "",
                            "indexed_at": stamp,
                            "metadata": {"fallback_used": emb.get("fallback_used"), "mode": emb.get("mode")},
                        })
                    self.store.upsert_embedding_refs(embedding_refs)
            except Exception as exc:
                vector_status = {"status": "fallback_indexed", "reason": "embedding_or_chroma_error", "error": str(exc)[:800]}
        return {
            "ok": True,
            "schema_id": "neo.memory.index_result.v1",
            "status": "indexed",
            "source_id": "assistant_memory",
            "root": _safe_rel(root),
            "indexed_documents": len(indexed_docs),
            "indexed_chunks": total_chunks,
            "embedding_refs": len(embedding_refs),
            "vector_store": vector_status,
            "documents": indexed_docs[:80],
            "stats": self.store.document_stats(),
            "policy": "Assistant local JSON remains the UI/history layer. Memory Engine chunks are the retrieval layer.",
        }


    def index_project_workspace(self, *, force: bool = False, limit: int | None = None) -> dict:
        """Index creator project workspaces into the central Memory Engine.

        Project Workspace is the shared project layer across Assistant, Roleplay,
        Image, Prompting/Captioning, and future surfaces. SQLite chunks are the
        source of truth; Chroma is only an optional semantic mirror.
        """
        source = get_memory_source("project_workspace") or {}
        self.store.upsert_source(source, updated_at=_now())
        stamp = _now()
        try:
            from neo_app.project_workspace import project_workspace_records_for_index
        except Exception as exc:
            return {"ok": False, "schema_id": "neo.memory.index_result.v1", "status": "import_error", "source_id": "project_workspace", "error": str(exc), "indexed_documents": 0, "indexed_chunks": 0, "stats": self.store.document_stats()}
        rows = project_workspace_records_for_index(limit=limit)
        indexed_docs: list[dict[str, Any]] = []
        chroma_chunks: list[dict[str, Any]] = []
        embedding_refs: list[dict[str, Any]] = []
        total_chunks = 0
        for idx, row in enumerate(rows):
            project_id = str(row.get("project_id") or "general")
            kind = str(row.get("kind") or "workspace")
            title = str(row.get("title") or f"Project {project_id}").strip() or project_id
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            row_id = str(payload.get("context_id") or payload.get("link_id") or payload.get("project_id") or f"row-{idx}")
            rel = f"neo_data/projects/{project_id}/{kind}/{row_id}.json"
            digest = _hash_text(json.dumps(payload or row, ensure_ascii=False, sort_keys=True, default=str) + text)
            doc_id = f"project_workspace:{_hash_text(project_id + ':' + kind + ':' + row_id)[:22]}"
            metadata = {"project_id": project_id, "kind": kind, "memory_namespace": f"project:{project_id}", **({"payload": payload} if payload else {})}
            document = {
                "document_id": doc_id,
                "source_id": "project_workspace",
                "source_path": rel,
                "title": title,
                "source_type": f"project_{kind}",
                "content_hash": digest,
                "status": "indexed",
                "visibility": source.get("visibility") or "user_visible",
                "trust_level": source.get("trust_level") or "confirmed",
                "metadata": metadata,
                "updated_at": stamp,
                "indexed_at": stamp,
            }
            document = _apply_memory_policy(document.get("source_id"), document)
            self.store.upsert_document(document)
            stored_chunks: list[dict[str, Any]] = []
            for chunk_idx, raw_chunk in enumerate(_chunk_markdown(text, max_chars=1800)):
                content = str(raw_chunk.get("content") or "").strip()
                if not content:
                    continue
                chunk_hash = _hash_text(f"{project_id}:{kind}:{row_id}:{chunk_idx}:{content}")
                tags = ["project_workspace", f"project:{project_id}", kind]
                chunk = {
                    "chunk_id": f"project:{_hash_text(project_id + ':' + kind + ':' + row_id + ':' + str(chunk_idx))[:12]}:{chunk_hash[:16]}",
                    "document_id": doc_id,
                    "source_id": "project_workspace",
                    "chunk_index": chunk_idx,
                    "title": raw_chunk.get("title") or title,
                    "content": content,
                    "summary": content[:320].replace("\n", " "),
                    "tags": tags,
                    "source_path": rel,
                    "start_line": raw_chunk.get("start_line"),
                    "end_line": raw_chunk.get("end_line"),
                    "content_hash": chunk_hash,
                    "visibility": document["visibility"],
                    "trust_level": document["trust_level"],
                    "searchable_text": " ".join([title, rel, " ".join(tags), content]),
                    "metadata": metadata,
                    "updated_at": stamp,
                }
                chunk = _apply_memory_policy(chunk.get("source_id"), chunk)
                stored_chunks.append(chunk)
            self.store.replace_document_chunks(doc_id, stored_chunks)
            chroma_chunks.extend(stored_chunks)
            total_chunks += len(stored_chunks)
            indexed_docs.append({"document_id": doc_id, "source_path": rel, "title": title, "project_id": project_id, "kind": kind, "chunk_count": len(stored_chunks)})
        vector_status = {"status": "skipped", "reason": "no_chunks"}
        if chroma_chunks:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                emb = embed_texts([chunk["content"] for chunk in chroma_chunks], allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors and len(vectors) == len(chroma_chunks):
                    vector_status = upsert_chroma_chunks(chroma_chunks, vectors, source_id="project_workspace")
                    model_id = str(emb.get("model_id") or emb.get("mode") or "local_hash_embeddings")
                    for chunk, vector in zip(chroma_chunks, vectors):
                        embedding_refs.append({
                            "embedding_id": f"emb:{chunk['chunk_id']}",
                            "chunk_id": chunk["chunk_id"],
                            "source_id": chunk["source_id"],
                            "model_id": model_id,
                            "dimension": len(vector),
                            "vector_store": vector_status.get("store") or "sqlite_reference",
                            "collection_name": vector_status.get("collection") or "",
                            "indexed_at": stamp,
                            "metadata": {"fallback_used": emb.get("fallback_used"), "mode": emb.get("mode")},
                        })
                    self.store.upsert_embedding_refs(embedding_refs)
            except Exception as exc:
                vector_status = {"status": "fallback_indexed", "reason": "embedding_or_chroma_error", "error": str(exc)[:800]}
        return {
            "ok": True,
            "schema_id": "neo.memory.index_result.v1",
            "status": "indexed",
            "source_id": "project_workspace",
            "indexed_documents": len(indexed_docs),
            "indexed_chunks": total_chunks,
            "embedding_refs": len(embedding_refs),
            "vector_store": vector_status,
            "documents": indexed_docs[:80],
            "stats": self.store.document_stats(),
            "policy": "Project Workspace memory is indexed as project-scoped context. Workspace JSON remains the creator-facing source of truth.",
        }


    def index_roleplay_memory(self, *, force: bool = False, limit: int | None = None) -> dict:
        """Index Roleplay's specialized human/state memory into the central Memory Engine.

        Roleplay keeps SQLite as the authoritative state store. This indexer publishes
        searchable chunks from scene packets, emotional continuity, character
        knowledge, unresolved threads, and memory fragments so Assistant/Memory
        Engine retrieval can reason over Roleplay context without reading random
        tables directly.
        """
        source = get_memory_source("roleplay_memory") or {}
        self.store.upsert_source(source, updated_at=_now())
        stamp = _now()
        try:
            from neo_app.roleplay.human_memory import roleplay_human_memory_index_rows
            from neo_app.roleplay.sqlite_store import roleplay_sqlite_state_payload
        except Exception as exc:
            return {"ok": False, "schema_id": "neo.memory.index_result.v1", "status": "import_error", "source_id": "roleplay_memory", "error": str(exc), "indexed_documents": 0, "indexed_chunks": 0, "stats": self.store.document_stats()}
        rows = roleplay_human_memory_index_rows(limit=limit or 500)
        if limit:
            rows = rows[: max(0, int(limit))]
        indexed_docs: list[dict[str, Any]] = []
        chroma_chunks: list[dict[str, Any]] = []
        embedding_refs: list[dict[str, Any]] = []
        total_chunks = 0
        for idx, row in enumerate(rows):
            table = str(row.get("table") or "roleplay_memory")
            row_id = str(row.get("packet_id") or row.get("state_id") or row.get("knowledge_id") or row.get("thread_id") or row.get("fragment_id") or f"row-{idx}")
            scene_id = str(row.get("scene_id") or row.get("scope_id") or "roleplay")
            title = str(row.get("title") or row.get("display_name") or row.get("knowledge_type") or row.get("thread_type") or row_id)
            content_parts = [title, str(row.get("emotional_tone") or row.get("current_emotion") or ""), str(row.get("content") or "")]
            try:
                payload = json.loads(str(row.get("payload_json") or "{}"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                for key in ("canon_locks", "unresolved_threads", "continuity_warnings", "character_knowledge"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        content_parts.extend(json.dumps(item, ensure_ascii=False, default=str) for item in value[:12])
                    elif value:
                        content_parts.append(str(value))
            content = "\n".join(part for part in content_parts if str(part or "").strip()).strip()
            if not content:
                continue
            source_path = f"roleplay_sqlite:{table}:{row_id}"
            doc_id = f"roleplay_memory:{_hash_text(source_path)[:20]}"
            digest = _hash_text(json.dumps(row, ensure_ascii=False, default=str))
            metadata = {"table": table, "row_id": row_id, "scene_id": scene_id, "scope_id": row.get("scope_id") or "", "memory_kind": "human_roleplay_state"}
            document = {
                "document_id": doc_id,
                "source_id": "roleplay_memory",
                "source_path": source_path,
                "title": title,
                "source_type": table,
                "content_hash": digest,
                "status": "indexed",
                "visibility": source.get("visibility") or "roleplay_only",
                "trust_level": "inferred" if table in {"rp_character_states", "rp_scene_memory_packets"} else "draft",
                "metadata": metadata,
                "updated_at": str(row.get("updated_at") or stamp),
                "indexed_at": stamp,
            }
            document = _apply_memory_policy(document.get("source_id"), document, {"memory_state": "draft", "trust_level": document.get("trust_level") or "inferred"})
            self.store.upsert_document(document)
            raw_chunks = _chunk_markdown(content, max_chars=1600)
            stored_chunks: list[dict[str, Any]] = []
            for cidx, raw_chunk in enumerate(raw_chunks):
                chunk_content = str(raw_chunk.get("content") or "").strip()
                if not chunk_content:
                    continue
                chunk_hash = _hash_text(f"{source_path}:{cidx}:{chunk_content}")
                chunk = {
                    "chunk_id": f"roleplay:{_hash_text(source_path + ':' + str(cidx))[:12]}:{chunk_hash[:16]}",
                    "document_id": doc_id,
                    "source_id": "roleplay_memory",
                    "chunk_index": cidx,
                    "title": raw_chunk.get("title") or title,
                    "content": chunk_content,
                    "summary": chunk_content[:320].replace("\n", " "),
                    "tags": ["roleplay_memory", table, scene_id],
                    "source_path": source_path,
                    "start_line": raw_chunk.get("start_line"),
                    "end_line": raw_chunk.get("end_line"),
                    "content_hash": chunk_hash,
                    "visibility": document["visibility"],
                    "trust_level": document["trust_level"],
                    "searchable_text": " ".join([title, table, scene_id, source_path, chunk_content]),
                    "metadata": metadata,
                    "updated_at": document["updated_at"],
                }
                chunk = _apply_memory_policy(chunk.get("source_id"), chunk)
                stored_chunks.append(chunk)
            self.store.replace_document_chunks(doc_id, stored_chunks)
            chroma_chunks.extend(stored_chunks)
            total_chunks += len(stored_chunks)
            indexed_docs.append({"document_id": doc_id, "source_path": source_path, "title": title, "chunk_count": len(stored_chunks)})
        vector_status = {"status": "skipped", "reason": "no_chunks"}
        if chroma_chunks:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                emb = embed_texts([chunk["content"] for chunk in chroma_chunks], allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors and len(vectors) == len(chroma_chunks):
                    vector_status = upsert_chroma_chunks(chroma_chunks, vectors, source_id="roleplay_memory")
                    model_id = str(emb.get("model_id") or emb.get("mode") or "local_hash_embeddings")
                    for chunk, vector in zip(chroma_chunks, vectors):
                        embedding_refs.append({
                            "embedding_id": f"emb:{chunk['chunk_id']}",
                            "chunk_id": chunk["chunk_id"],
                            "source_id": chunk["source_id"],
                            "model_id": model_id,
                            "dimension": len(vector),
                            "vector_store": vector_status.get("store") or "sqlite_reference",
                            "collection_name": vector_status.get("collection") or "",
                            "indexed_at": stamp,
                            "metadata": {"fallback_used": emb.get("fallback_used"), "mode": emb.get("mode")},
                        })
                    self.store.upsert_embedding_refs(embedding_refs)
            except Exception as exc:
                vector_status = {"status": "fallback_indexed", "reason": "embedding_or_chroma_error", "error": str(exc)[:800]}
        return {
            "ok": True,
            "schema_id": "neo.memory.index_result.v1",
            "status": "indexed",
            "source_id": "roleplay_memory",
            "indexed_documents": len(indexed_docs),
            "indexed_chunks": total_chunks,
            "embedding_refs": len(embedding_refs),
            "vector_store": vector_status,
            "roleplay_sqlite": roleplay_sqlite_state_payload(),
            "documents": indexed_docs[:80],
            "stats": self.store.document_stats(),
            "policy": "Roleplay SQLite remains authoritative. Memory Engine indexes searchable human/state summaries for Assistant and cross-surface retrieval.",
        }

    def retrieve(self, payload: dict[str, Any] | None = None) -> dict:
        data = payload or {}
        query = str(data.get("query") or "").strip()
        requested_profile = str(data.get("profile") or "smart").strip() or "smart"
        profile_config = get_retrieval_profile(requested_profile)
        profile = profile_config["profile_id"]
        consumer = str(data.get("consumer") or "assistant").strip() or "assistant"
        sources = data.get("sources")
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list) or not sources:
            sources = list(profile_config.get("sources") or ["system_records"])
        sources = [str(item) for item in sources if str(item or "").strip()]
        limit = max(1, min(int(data.get("limit") or 12), 50))
        candidate_limit = min(200, max(limit, limit * int(profile_config.get("candidate_multiplier") or 4)))
        keyword_weight = _normalize_score(data.get("keyword_weight", profile_config.get("keyword_weight", 0.45)))
        vector_weight = _normalize_score(data.get("vector_weight", profile_config.get("vector_weight", 0.35)))
        recency_weight = _normalize_score(data.get("recency_weight", profile_config.get("recency_weight", 0.08)))
        importance_weight = _normalize_score(data.get("importance_weight", profile_config.get("importance_weight", 0.12)))
        min_score = _normalize_score(data.get("min_score", profile_config.get("min_score", 0.15)))
        rerank_enabled = bool(data.get("rerank", profile_config.get("rerank", True)))
        semantic_enabled = bool(data.get("semantic", profile_config.get("semantic", True)))
        low_confidence_rejection = bool(data.get("low_confidence_rejection", profile_config.get("low_confidence_rejection", True)))
        include_drafts = bool(data.get("include_drafts", profile_config.get("include_drafts", False)))
        include_deprecated = bool(data.get("include_deprecated", profile_config.get("include_deprecated", False)))
        include_conflicts = bool(data.get("include_conflicts", profile_config.get("include_conflicts", False)))
        require_approved = bool(data.get("require_approved", profile_config.get("require_approved", True)))

        keyword_results = self.store.search_chunks_keyword(query, sources=sources, limit=candidate_limit)
        for item in keyword_results:
            item["keyword_score"] = _normalize_score(item.get("score"), 0.1)
            item["vector_score"] = 0.0

        vector_results: list[dict[str, Any]] = []
        vector_status: dict[str, Any] = {"status": "skipped", "reason": "semantic_disabled" if not semantic_enabled else "no_query"}
        if semantic_enabled and query:
            try:
                from neo_app.admin.semantic_engine import embed_texts
                emb = embed_texts([query], allow_fallback=True)
                vectors = emb.get("vectors") or []
                if vectors:
                    all_vector_status = []
                    for source_id in sources:
                        status = query_chroma_chunks(vectors[0], source_id=source_id, limit=max(limit, candidate_limit // max(1, len(sources))))
                        all_vector_status.append({k: v for k, v in status.items() if k != "results"})
                        for item in status.get("results") or []:
                            item["keyword_score"] = 0.0
                            item["vector_score"] = _normalize_score(item.get("score"), 0.1)
                            vector_results.append(item)
                    vector_status = {"status": "ready", "model_id": emb.get("model_id") or emb.get("mode"), "fallback_used": emb.get("fallback_used"), "sources": all_vector_status, "result_count": len(vector_results)}
                else:
                    vector_status = {"status": "skipped", "reason": "no_query_vector", "mode": emb.get("mode")}
            except Exception as exc:
                vector_status = {"status": "failed", "reason": "semantic_query_error", "error": str(exc)[:700]}

        vector_ids = [str(item.get("chunk_id")) for item in vector_results if item.get("chunk_id")]
        if vector_ids:
            hydrated = {item["chunk_id"]: item for item in self.store.get_chunks_by_ids(vector_ids, query=query)}
            for item in vector_results:
                stored = hydrated.get(str(item.get("chunk_id")))
                if stored:
                    vector_score = _normalize_score(item.get("vector_score"), _normalize_score(item.get("score")))
                    stored["vector_score"] = vector_score
                    stored["keyword_score"] = _normalize_score(stored.get("keyword_score"), 0.0)
                    stored["score"] = vector_score
                    stored["retrieval_type"] = "vector"
                    vector_results[vector_results.index(item)] = stored

        raw_candidates = _dedupe_results(keyword_results + vector_results)
        policy_rejected = [item for item in raw_candidates if not _policy_allowed(item, include_drafts=include_drafts, include_deprecated=include_deprecated, include_conflicts=include_conflicts, require_approved=require_approved)]
        candidates = [item for item in raw_candidates if _policy_allowed(item, include_drafts=include_drafts, include_deprecated=include_deprecated, include_conflicts=include_conflicts, require_approved=require_approved)]
        for item in candidates:
            keyword_score = _normalize_score(item.get("keyword_score"), _normalize_score(item.get("score")))
            vector_score = _normalize_score(item.get("vector_score"), 0.0)
            recency = _recency_score(item.get("updated_at"))
            importance = _importance_score(item)
            combined = (keyword_score * keyword_weight) + (vector_score * vector_weight) + (recency * recency_weight) + (importance * importance_weight)
            item["keyword_score"] = round(keyword_score, 6)
            item["vector_score"] = round(vector_score, 6)
            item["recency_score"] = round(recency, 6)
            item["importance_score"] = round(importance, 6)
            item["score"] = round(max(0.0, min(1.0, combined)), 6)
            item["score_components"] = {
                "keyword": item["keyword_score"],
                "vector": item["vector_score"],
                "recency": item["recency_score"],
                "importance": item["importance_score"],
            }

        candidates.sort(key=lambda item: item.get("score") or 0, reverse=True)
        rejected = []
        accepted = []
        for item in candidates:
            if low_confidence_rejection and _normalize_score(item.get("score")) < min_score:
                rejected.append({k: v for k, v in item.items() if k not in {"content"}} | {"rejection_reason": "below_min_score"})
            else:
                accepted.append(item)
        reranker_status = {"status": "skipped"}
        backend_parts = ["fts_keyword" if any((item.get("retrieval_type") == "fts_keyword") for item in keyword_results) else "sqlite_keyword"]
        if vector_results:
            backend_parts.append("chroma_vector")
        if rerank_enabled and accepted:
            try:
                from neo_app.admin.semantic_engine import rerank_results
                top_n = limit
                reranked = rerank_results(query, accepted[:candidate_limit], top_n=top_n, allow_fallback=True)
                accepted = reranked.get("results") or accepted[:limit]
                reranker_status = {k: v for k, v in reranked.items() if k != "results"}
                backend_parts.append("reranker")
            except Exception as exc:
                reranker_status = {"status": "failed", "error": str(exc)[:600]}
                accepted = accepted[:limit]
        else:
            accepted = accepted[:limit]

        results = []
        for item in accepted[:limit]:
            clean = dict(item)
            content = clean.get("content") or ""
            clean["snippet"] = content[:420].replace("\n", " ")
            results.append(clean)
        trace = {
            "trace_id": uuid4().hex,
            "query": query,
            "consumer": consumer,
            "profile": profile,
            "sources": sources,
            "results": [{k: v for k, v in item.items() if k not in {"content"}} for item in results],
            "created_at": _now(),
            "metadata": {
                "backend_used": "+".join(backend_parts),
                "profile_config": profile_config,
                "weights": {"keyword": keyword_weight, "vector": vector_weight, "recency": recency_weight, "importance": importance_weight},
                "min_score": min_score,
                "semantic": vector_status,
                "reranker": reranker_status,
                "candidate_count": len(candidates), "policy_rejected_count": len(policy_rejected),
                "accepted_count": len(accepted),
                "rejected_count": len(rejected) + len(policy_rejected),
                "rejected_preview": rejected[:12],
                "policy_rejected_count": len(policy_rejected),
                "policy_rejected_preview": [{k: v for k, v in item.items() if k not in {"content"}} for item in policy_rejected[:12]],
                "policy_filters": {"include_drafts": include_drafts, "include_deprecated": include_deprecated, "include_conflicts": include_conflicts, "require_approved": require_approved},
            },
        }
        self.store.write_retrieval_trace(trace)
        return {
            "ok": True,
            "schema_id": "neo.memory.retrieve.v1",
            "retrieval_version": "hybrid.v2",
            "status": "ready",
            "query": query,
            "profile": profile,
            "profile_config": profile_config,
            "consumer": consumer,
            "sources": sources,
            "backend_used": trace["metadata"]["backend_used"],
            "semantic": vector_status,
            "reranker": reranker_status,
            "memory_policy_filters": {"include_drafts": include_drafts, "include_deprecated": include_deprecated, "include_conflicts": include_conflicts, "require_approved": require_approved, "policy_rejected_count": len(policy_rejected)},
            "low_confidence_rejection": {"enabled": low_confidence_rejection, "min_score": min_score, "rejected_count": len(rejected), "rejected_preview": rejected[:8]},
            "results": results,
            "trace_id": trace["trace_id"],
            "stats": {"result_count": len(results), "candidate_count": len(candidates), "policy_rejected_count": len(policy_rejected), "keyword_candidate_count": len(keyword_results), "vector_candidate_count": len(vector_results)},
        }


    def retrieval_rerank_status(self) -> dict:
        return self.retrieval_engine.status()

    def index_unified_embeddings(self, payload: dict[str, Any] | None = None) -> dict:
        return self.retrieval_engine.index_embeddings(payload or {})

    def retrieve_unified(self, payload: dict[str, Any] | None = None) -> dict:
        return self.retrieval_engine.retrieve(payload or {})

    def control_center_status(self) -> dict:
        return self.control_center.status()

    def control_center_plan(self, payload: dict[str, Any] | None = None) -> dict:
        return self.control_center.plan(payload or {}, persist=True)

    def control_center_trace_list(self, payload: dict[str, Any] | None = None) -> dict:
        payload = payload or {}
        return self.control_center.list_traces(
            limit=int(payload.get("limit") or 25),
            controller=payload.get("controller"),
            surface=payload.get("surface"),
        )

    def control_center_trace_detail(self, trace_id: str) -> dict:
        return self.control_center.trace_detail(trace_id)


    def writeback_status(self) -> dict:
        return self.writeback_engine.status()

    def writeback_plan(self, payload: dict[str, Any] | None = None) -> dict:
        return self.writeback_engine.plan(payload or {})

    def run_writeback(self, payload: dict[str, Any] | None = None) -> dict:
        return self.writeback_engine.run(payload or {})

    def review_writeback(self, payload: dict[str, Any] | None = None) -> dict:
        return self.writeback_engine.review(payload or {})


    def safety_status(self) -> dict:
        return self.safety_guard.status()

    def safety_rules(self, payload: dict[str, Any] | None = None) -> dict:
        return self.safety_guard.rules(payload or {})

    def safety_validate_context(self, payload: dict[str, Any] | None = None) -> dict:
        return self.safety_guard.validate_context(payload or {})

    def safety_validate_writeback(self, payload: dict[str, Any] | None = None) -> dict:
        return self.safety_guard.validate_writeback(payload or {})

    def safety_audit(self, payload: dict[str, Any] | None = None) -> dict:
        return self.safety_guard.audit(payload or {})

    def safety_violations(self, payload: dict[str, Any] | None = None) -> dict:
        return self.safety_guard.violations(payload or {})


    def control_center_review_status(self) -> dict:
        return self.control_center_trace_review.status()

    def control_center_review_dashboard(self, payload: dict[str, Any] | None = None) -> dict:
        return self.control_center_trace_review.dashboard(payload or {})

    def control_center_review_trace_detail(self, trace_id: str) -> dict:
        return self.control_center_trace_review.trace_detail(trace_id)

    def control_center_review_record(self, payload: dict[str, Any] | None = None) -> dict:
        return self.control_center_trace_review.record_review(payload or {})


    def observability_status(self) -> dict:
        return self.observability_engine.status()

    def observability_snapshot(self, payload: dict[str, Any] | None = None) -> dict:
        return self.observability_engine.snapshot(payload or {})

    def observability_memory(self, payload: dict[str, Any] | None = None) -> dict:
        return self.observability_engine.inspect_memory(payload or {})

    def observability_retrieval(self, payload: dict[str, Any] | None = None) -> dict:
        return self.observability_engine.inspect_retrieval(payload or {})

    def observability_control_center(self, payload: dict[str, Any] | None = None) -> dict:
        return self.observability_engine.inspect_control_center(payload or {})

    def observability_roleplay_scene(self, payload: dict[str, Any] | None = None) -> dict:
        return self.observability_engine.inspect_roleplay_scene(payload or {})

    def record_event(self, event_payload: dict[str, Any] | MemoryEvent) -> dict:
        if isinstance(event_payload, MemoryEvent):
            event = event_payload
        else:
            normalized = dict(event_payload or {})
            # Phase 11.4.1 guard: frontend/backend telemetry may send compact
            # events without a human title. Derive one from event_type instead
            # of throwing a Pydantic validation error.
            if not normalized.get("title"):
                event_type = normalized.get("event_type") or "neo.event"
                normalized["title"] = str(event_type).replace(".", " ").replace("_", " ").title()
            event = MemoryEvent(**normalized)
        stored = self.store.write_event(event)
        return {
            "ok": True,
            "event": model_to_dict(stored),
            "semantic_queued": bool(stored.should_embed and self.capabilities().semantic_search_enabled),
        }


    def record_image_output_workflow(self, record: dict[str, Any]) -> dict:
        """Record an Assistant-ready memory event for a persisted Image workflow.

        This is a contract helper for future Assistant access. It keeps output-sidecar
        memory consistent for base workflows and extension-assisted workflows.
        """
        record = record if isinstance(record, dict) else {}
        route = record.get("route") if isinstance(record.get("route"), dict) else {}
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
        extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
        summary = str(record.get("assistant_summary") or "Image workflow completed.")
        event_payload = {
            "namespace": "image",
            "surface": "image",
            "subtab": record.get("subtab") or route.get("mode") or "generate",
            "source": "base",
            "event_type": "image.workflow.generated",
            "title": "Image workflow generated",
            "summary": summary,
            "provider_id": (record.get("job") or {}).get("provider_id") if isinstance(record.get("job"), dict) else None,
            "family": route.get("family"),
            "loader": route.get("loader"),
            "tags": [tag for tag in ["image", route.get("backend"), route.get("family"), route.get("loader"), route.get("mode")] if tag],
            "payload": {
                "result_id": record.get("result_id") or "",
                "route": route,
                "input_assets": source.get("input_assets") if isinstance(source.get("input_assets"), list) else [],
                "extensions": extensions,
                "outputs": outputs,
                "replay_payload": record.get("replay_payload") if isinstance(record.get("replay_payload"), dict) else {},
                "assistant_summary": summary,
            },
            "importance": "normal",
            "should_embed": True,
        }
        return self.record_event(event_payload)

    def build_extension_workflow_memory_events(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Build extension-specific memory event payloads from an Image output sidecar.

        Phase K: prefer extension-authored memory readiness blocks stored in
        ``extensions.memory_events``. Those blocks can contain richer replay and
        validation context than the generic manifest contract. Fall back to the
        generic runtime contract for older extensions.
        """
        record = record if isinstance(record, dict) else {}
        route = record.get("route") if isinstance(record.get("route"), dict) else {}
        extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        outputs = (record.get("outputs") or {}).get("files", []) if isinstance(record.get("outputs"), dict) else []
        events: list[dict[str, Any]] = []
        emitted: set[str] = set()

        memory_events = extensions.get("memory_events") if isinstance(extensions.get("memory_events"), dict) else {}
        for ext_id, readiness in memory_events.items():
            ext_id = str(ext_id or "")
            if not ext_id or not isinstance(readiness, dict):
                continue
            if ext_id == "lora_stack":
                from neo_extensions.built_in.lora_stack.backend.memory_event import build_memory_event_payload_from_readiness

                events.append(
                    build_memory_event_payload_from_readiness(
                        readiness,
                        result_id=str(record.get("result_id") or ""),
                        outputs=outputs if isinstance(outputs, list) else [],
                    )
                )
                emitted.add(ext_id)
                continue
            # Generic extension-authored readiness block. Keep it compact but
            # preserve the richer readiness block under payload.readiness.
            assistant_summary = str(readiness.get("assistant_summary") or record.get("assistant_summary") or "")
            event = extension_memory_event_contract(
                ext_id,
                readiness.get("route") if isinstance(readiness.get("route"), dict) else route,
                assets=source.get("input_assets") if isinstance(source.get("input_assets"), list) else [],
                params=readiness.get("params") if isinstance(readiness.get("params"), dict) else {},
                outputs=outputs if isinstance(outputs, list) else [],
                assistant_summary=assistant_summary,
            )
            event["payload"] = {"readiness": readiness, "result_id": record.get("result_id") or ""}
            events.append(event)
            emitted.add(ext_id)

        for item in extensions.get("used", []) if isinstance(extensions.get("used"), list) else []:
            ext_id = ""
            if isinstance(item, dict):
                ext_id = str(item.get("extension_id") or "")
            elif isinstance(item, str):
                ext_id = item
            if not ext_id or ext_id in emitted:
                continue
            payload = extension_memory_event_contract(
                ext_id,
                route,
                assets=source.get("input_assets") if isinstance(source.get("input_assets"), list) else [],
                params=(extensions.get("payloads") or {}).get(ext_id, {}) if isinstance(extensions.get("payloads"), dict) else {},
                outputs=outputs if isinstance(outputs, list) else [],
                assistant_summary=str(record.get("assistant_summary") or ""),
            )
            events.append(payload)
        return events

    def record_extension_workflow_memory_events(self, record: dict[str, Any]) -> dict:
        """Persist extension-specific workflow memory events for an output record."""
        events = self.build_extension_workflow_memory_events(record)
        stored = [self.record_event(event) for event in events]
        return {"ok": True, "count": len(stored), "events": stored}

    def list_events(self, namespace: str | None = None, surface: str | None = None, limit: int = 20) -> dict:
        events = self.store.list_events(namespace=namespace, surface=surface, limit=limit)
        return {
            "ok": True,
            "backend": "sqlite",
            "events": [model_to_dict(event) for event in events],
        }

    def search(self, query_payload: dict[str, Any] | MemoryQuery) -> dict:
        query = query_payload if isinstance(query_payload, MemoryQuery) else MemoryQuery(**query_payload)
        capabilities = self.capabilities()
        # Phase 8 fallback: semantic requests transparently fall back to SQLite text search
        # until Chroma + sentence-transformers are installed and wired in a later phase.
        events = self.store.search_events(
            query=query.query,
            namespace=query.namespace,
            surface=query.surface,
            limit=query.limit,
        )
        results = [model_to_dict(MemorySearchResult(event=event, backend="sqlite")) for event in events]
        return {
            "ok": True,
            "requested_semantic": query.semantic,
            "semantic_available": capabilities.semantic_search_enabled,
            "backend_used": "sqlite",
            "results": results,
            "notes": capabilities.notes if query.semantic and not capabilities.semantic_search_enabled else [],
        }


@lru_cache(maxsize=1)
def get_memory_service() -> MemoryService:
    return MemoryService()
