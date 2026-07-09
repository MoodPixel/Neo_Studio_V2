from __future__ import annotations

from typing import Any


def _safe_memory_service():
    try:
        from neo_app.memory.service import get_memory_service
        return get_memory_service()
    except Exception:
        return None


def memory_health_payload() -> dict[str, Any]:
    service = _safe_memory_service()
    if service is None:
        return {"ok": False, "backend": "unavailable", "semantic_available": False, "notes": ["Memory Engine service is not available."]}
    try:
        capabilities = service.capabilities()
        status = service.memory_engine_status() if hasattr(service, "memory_engine_status") else {}
        return {
            "ok": True,
            "backend": "memory_engine",
            "semantic_available": bool(getattr(capabilities, "semantic_search_enabled", False)),
            "vector_store": (status.get("vector_store") or {}) if isinstance(status, dict) else {},
            "stats": (status.get("stats") or {}) if isinstance(status, dict) else {},
            "notes": list(getattr(capabilities, "notes", []) or []),
        }
    except Exception as exc:
        return {"ok": False, "backend": "error", "semantic_available": False, "notes": [str(exc)]}


def record_assistant_capture(record: dict[str, Any]) -> dict[str, Any]:
    """Write an Assistant memory capture into the centralized memory event store.

    Assistant keeps local JSON for UX/history. Memory Engine remains the central
    retrieval/indexing layer.
    """
    service = _safe_memory_service()
    if service is None:
        return {"ok": False, "message": "Memory Engine service is not available."}
    try:
        text = str(record.get("text") or "").strip()
        event = {
            "namespace": record.get("namespace") or "assistant",
            "surface": "assistant",
            "subtab": "memory",
            "source": "assistant",
            "event_type": "assistant.memory.manual_capture",
            "title": record.get("title") or "Assistant memory capture",
            "summary": text[:1200],
            "project_id": record.get("project_id") or None,
            "tags": [tag for tag in ["assistant", "manual_capture", record.get("project_id")] if tag],
            "payload": {
                "capture_id": record.get("capture_id") or "",
                "session_id": record.get("session_id") or "",
                "text": text,
                "source": record.get("source") or "assistant_manual_capture",
            },
            "importance": "normal",
            "should_embed": True,
        }
        return service.record_event(event)
    except Exception as exc:
        return {"ok": False, "message": f"Memory Engine write failed: {exc}"}


def resolve_assistant_memory_profile(requested: str = "smart", message: str = "") -> str:
    """Map simple Assistant choices to richer Memory Engine profiles."""
    profile = str(requested or "smart").strip().lower()
    text = str(message or "").lower()
    if profile == "fast":
        return "fast"
    if any(token in text for token in ("code", "repo", "file", "route", "function", "test", "css", "javascript", "python", "where is", "which file")):
        return "code_audit"
    if any(token in text for token in ("admin", "memory engine", "backend", "provider", "extension", "surface", "control center", "health")):
        return "admin_diagnostic"
    if profile == "deep":
        return "deep"
    return "assistant_project"


def retrieve_assistant_memory_engine(
    query: str = "",
    project_id: str = "",
    *,
    profile: str = "smart",
    limit: int = 8,
    semantic: bool = True,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    service = _safe_memory_service()
    if service is None:
        return {"ok": False, "results": [], "backend_used": "unavailable", "notes": ["Memory Engine service is not available."]}
    query = str(query or "").strip()
    memory_profile = resolve_assistant_memory_profile(profile, query)
    request = {
        "query": query,
        "profile": memory_profile,
        "consumer": "assistant_context_pack",
        "limit": limit,
        "semantic": bool(semantic),
        "project_id": project_id or "general",
    }
    if sources:
        request["sources"] = [str(source) for source in sources if str(source or "").strip()]
    try:
        result = service.retrieve(request)
        result = dict(result or {})
        result["assistant_requested_profile"] = profile
        result["memory_engine_profile"] = memory_profile
        result["assistant_scope_sources"] = request.get("sources") or []
        return result
    except Exception as exc:
        return {"ok": False, "results": [], "backend_used": "error", "notes": [str(exc)], "memory_engine_profile": memory_profile}


def search_assistant_memory(query: str = "", project_id: str = "", *, limit: int = 8, semantic: bool = True) -> dict[str, Any]:
    """Legacy event search bridge. Prefer retrieve_assistant_memory_engine()."""
    service = _safe_memory_service()
    if service is None:
        return {"ok": False, "results": [], "backend_used": "unavailable", "notes": ["Memory Engine service is not available."]}
    query = str(query or "").strip()
    try:
        payload = {"query": query, "namespace": None, "surface": None, "limit": limit, "semantic": semantic}
        result = service.search(payload)
        results = []
        for item in result.get("results", []) if isinstance(result, dict) else []:
            event = item.get("event") if isinstance(item, dict) else {}
            if project_id and event.get("project_id") not in {project_id, None, ""}:
                continue
            results.append(item)
            if len(results) >= limit:
                break
        result = dict(result or {})
        result["results"] = results
        return result
    except Exception as exc:
        return {"ok": False, "results": [], "backend_used": "error", "notes": [str(exc)]}
