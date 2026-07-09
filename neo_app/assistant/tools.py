from __future__ import annotations

from typing import Any

from neo_app.assistant.contracts import is_blocked_tool_id, contract_lock_payload
from neo_app.assistant.context_pack import build_context_pack
from neo_app.assistant.store import assistant_search_payload, save_context_item_payload, save_surface_context_payload
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot
from neo_app.tool_registry import effective_tool_registry_payload, tool_registry_status_payload

SAFE_TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "tool_id": "preview_context_pack",
        "name": "Preview context pack",
        "description": "Show the exact project/thread/memory context Assistant would inject.",
        "safe": True,
        "requires_confirmation": False,
    },
    {
        "tool_id": "search_assistant_context",
        "name": "Search assistant context",
        "description": "Search saved Assistant chats, projects, captures, and context cards.",
        "safe": True,
        "requires_confirmation": False,
    },
    {
        "tool_id": "save_project_knowledge",
        "name": "Save project knowledge",
        "description": "Save pasted text as project knowledge for future Assistant context packs.",
        "safe": True,
        "requires_confirmation": False,
    },
    {
        "tool_id": "attach_surface_context",
        "name": "Attach surface context",
        "description": "Attach a snapshot from an implemented Neo surface to Assistant/project context.",
        "safe": True,
        "requires_confirmation": False,
    },
    {
        "tool_id": "guide_current_tab",
        "name": "Guide current tab",
        "description": "Generate a lightweight guidance card for the current Neo surface/subtab.",
        "safe": True,
        "requires_confirmation": False,
    },
]

LOCKED_OUT_OF_ASSISTANT_TOOLS = [
    "local command execution",
    "automatic patch apply",
    "external connector actions",
    "destructive file operations",
]
DEFERRED_TO_WAVE4 = LOCKED_OUT_OF_ASSISTANT_TOOLS


def _tool_log_summary(tool_id: str, args: dict[str, Any] | None = None, result: dict[str, Any] | None = None, *, status: str = "") -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    result = result if isinstance(result, dict) else {}
    context_item = result.get("context_item") if isinstance(result.get("context_item"), dict) else {}
    handoff = result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
    return {
        "tool_id": str(tool_id or ""),
        "status": str(status or ""),
        "project_id": str(args.get("project_id") or result.get("project_id") or context_item.get("project_id") or "general"),
        "session_id": str(args.get("session_id") or result.get("session_id") or ""),
        "surface": str(args.get("surface") or handoff.get("surface") or "assistant"),
        "arg_keys": sorted(args.keys()),
        "result_ok": result.get("ok") if "ok" in result else None,
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
    }


def _safe_log_tool_event(event: str, *, run_id: str = "", payload: dict[str, Any] | None = None, level: str = "INFO") -> None:
    try:
        log_surface_event("assistant", event, run_id=run_id or None, level=level, payload=payload or {})
    except Exception:
        pass



def tool_catalog_payload() -> dict[str, Any]:
    registry = effective_tool_registry_payload()
    return {"ok": True, "tools": SAFE_TOOL_CATALOG, "deferred": LOCKED_OUT_OF_ASSISTANT_TOOLS, "lock": contract_lock_payload(), "tool_registry": registry, "registry_status": tool_registry_status_payload()}


def _tool(tool_id: str) -> dict[str, Any] | None:
    return next((tool for tool in SAFE_TOOL_CATALOG if tool.get("tool_id") == tool_id), None)


def tool_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    tool_id = str(payload.get("tool_id") or "").strip()
    tool = _tool(tool_id)
    if is_blocked_tool_id(tool_id):
        try:
            record_surface_error("assistant", "Unsafe Assistant tool preview blocked.", payload={"tool_id": tool_id}, run_id=tool_id or "assistant_tool")
        except Exception:
            pass
        raise ValueError("Unsafe Assistant tool is locked out")
    if not tool:
        try:
            record_surface_error("assistant", "Unknown Assistant tool preview requested.", payload={"tool_id": tool_id}, run_id=tool_id or "assistant_tool")
        except Exception:
            pass
        raise ValueError("Unknown or unsafe Assistant tool")
    result = {
        "ok": True,
        "tool": tool,
        "preview": {
            "safe": True,
            "will_execute": tool_id,
            "destructive": False,
            "summary": tool.get("description") or tool_id,
            "contract_version": contract_lock_payload()["contract_version"],
        },
    }
    summary = _tool_log_summary(tool_id, payload, result, status="previewed")
    _safe_log_tool_event("assistant.tool.previewed", run_id=tool_id or "assistant_tool", payload=summary)
    try:
        record_surface_snapshot("assistant", "neo_last_tool_call.json", {"summary": summary}, run_id=tool_id or "assistant_tool")
    except Exception:
        pass
    return result


def guide_for_surface(surface: str = "assistant", subtab: str = "") -> dict[str, Any]:
    surface = (surface or "assistant").strip() or "assistant"
    subtab = (subtab or "").strip()
    guides = {
        "image": ["Check provider route state first.", "Confirm model/family compatibility.", "Use Assistant to explain failed validation before changing parameters."],
        "prompt_captioning": ["Use Prompt Builder for text-only profiles.", "Captioning requires explicit Vision + Caption flags or detected KoboldCpp runtime vision support.", "Send useful captions/prompts back to Assistant as project knowledge."],
        "roleplay": ["Keep canon/project memory separate from temporary scene text.", "Use Assistant to summarize continuity before long scenes.", "Save stable facts to project memory, not every draft line."],
        "admin": ["Memory Engine owns retrieval, embeddings, and memory health.", "Assistant reads/writes through Memory Engine instead of duplicating memory controls."],
        "assistant": ["Attach current tab context before asking for help.", "Preview the context pack when memory behavior looks suspicious.", "Use project knowledge for stable client/project facts."],
    }
    bullets = guides.get(surface, ["Attach a short summary of the current workflow.", "Ask Assistant for next steps, risk checks, or implementation phases."])
    return {"surface": surface, "subtab": subtab, "bullets": bullets, "title": f"{surface.replace('_', ' ').title()} guide"}


def execute_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    tool_id = str(payload.get("tool_id") or "").strip()
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    run_id = tool_id or "assistant_tool"
    _safe_log_tool_event("assistant.tool.execution_started", run_id=run_id, payload=_tool_log_summary(tool_id, args, status="started"))
    try:
        if is_blocked_tool_id(tool_id):
            raise ValueError("Unsafe Assistant tool is locked out")
        if not _tool(tool_id):
            raise ValueError("Unknown or unsafe Assistant tool")
        if tool_id == "preview_context_pack":
            result = build_context_pack(
                session_id=str(args.get("session_id") or payload.get("session_id") or ""),
                project_id=str(args.get("project_id") or payload.get("project_id") or "general"),
                message=str(args.get("message") or payload.get("message") or ""),
                retrieval_profile=str(args.get("retrieval_profile") or payload.get("retrieval_profile") or "smart"),
            )
        elif tool_id == "search_assistant_context":
            result = assistant_search_payload(query=str(args.get("query") or payload.get("query") or ""), project_id=str(args.get("project_id") or payload.get("project_id") or ""))
        elif tool_id == "save_project_knowledge":
            result = save_context_item_payload(args or payload)
        elif tool_id == "attach_surface_context":
            result = save_surface_context_payload(args or payload)
        elif tool_id == "guide_current_tab":
            guide = guide_for_surface(str(args.get("surface") or payload.get("surface") or "assistant"), str(args.get("subtab") or payload.get("subtab") or ""))
            result = {"ok": True, "guide": guide}
        else:
            raise ValueError("Assistant tool is not implemented")
        summary = _tool_log_summary(tool_id, args or payload, result, status="executed")
        _safe_log_tool_event("assistant.tool.executed", run_id=run_id, payload=summary)
        try:
            record_surface_snapshot("assistant", "neo_last_tool_call.json", {"summary": summary}, run_id=run_id)
        except Exception:
            pass
        return result
    except Exception as exc:
        summary = _tool_log_summary(tool_id, args or payload, status="error")
        try:
            record_surface_error("assistant", "Assistant tool execution failed.", exc=exc, payload=summary, run_id=run_id)
        except Exception:
            pass
        raise
