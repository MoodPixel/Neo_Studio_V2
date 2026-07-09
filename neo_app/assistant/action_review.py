from __future__ import annotations

from typing import Any

from neo_app.operator.service import operator_status_payload, plan_operator_command, run_operator_command
from neo_app.tool_registry import tool_registry_status_payload
from neo_app.tool_ledger import record_tool_ledger_event, tool_ledger_status_payload

ASSISTANT_ACTION_REVIEW_SCHEMA = "neo.assistant.action_review.v1"
ASSISTANT_ACTION_REVIEW_VERSION = "0.2.0"


def _action_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    read_only = [a for a in actions if not a.get("requires_confirmation") and a.get("status") != "blocked"]
    gated = [a for a in actions if a.get("requires_confirmation")]
    blocked = [a for a in actions if a.get("status") == "blocked"]
    return {
        "total": len(actions),
        "read_only_count": len(read_only),
        "confirmation_required_count": len(gated),
        "blocked_count": len(blocked),
        "risk_levels": sorted({str(a.get("risk_level") or "low") for a in actions}),
        "action_types": sorted({str(a.get("action_type") or "unknown") for a in actions}),
    }


def action_review_status_payload() -> dict[str, Any]:
    operator = operator_status_payload()
    tools = tool_registry_status_payload()
    return {
        "ok": True,
        "schema_id": "neo.assistant.action_review.status.v1",
        "status": "ready",
        "runtime_version": ASSISTANT_ACTION_REVIEW_VERSION,
        "operator_status": operator.get("status"),
        "permission_policy": operator.get("permission_policy") or {},
        "tool_registry": tools,
        "capabilities": [
            "assistant_action_planning",
            "operator_permission_gating",
            "read_only_action_preview",
            "confirmation_required_execution",
            "memory_trace_visibility",
            "central_tool_registry",
            "permission_profiles",
            "tool_execution_ledger",
        ],
        "tool_execution_ledger": tool_ledger_status_payload(),
        "policy": "Assistant actions are planned through Neo Operator and governed by the central Tool Registry permission profile before execution.",
    }


def plan_assistant_action_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    command = str(data.get("command") or data.get("message") or data.get("text") or "").strip()
    plan = plan_operator_command({
        "command": command,
        "profile": data.get("profile") or data.get("retrieval_profile"),
        "sources": data.get("sources"),
        "limit": data.get("limit") or 8,
        "provider_type": data.get("provider_type"),
    })
    actions = list(plan.get("actions") or [])
    try:
        record_tool_ledger_event({
            "actor": "assistant",
            "surface": "assistant",
            "intent": plan.get("intent"),
            "status": "planned",
            "tool_id": "assistant.action_review",
            "tool_label": "Assistant Action Review",
            "category": "assistant",
            "action_type": "assistant_action_review_plan",
            "risk_level": "read_only",
            "payload": {"command": command, "actions": actions},
            "result_summary": f"Assistant planned {len(actions)} action(s) through Neo Operator",
            "metadata": {"phase": "25", "source": "assistant.action_review.plan"},
        })
    except Exception:
        pass
    return {
        "ok": True,
        "schema_id": ASSISTANT_ACTION_REVIEW_SCHEMA,
        "review_version": ASSISTANT_ACTION_REVIEW_VERSION,
        "status": "planned",
        "command": command,
        "intent": plan.get("intent"),
        "confidence": plan.get("confidence"),
        "retrieval_profile": plan.get("retrieval_profile"),
        "sources": plan.get("sources") or [],
        "actions": actions,
        "action_summary": _action_summary(actions),
        "permission_summary": plan.get("permission_summary") or {},
        "operator_plan": plan,
        "review_policy": {
            "source": "central_tool_registry",
            "active_profile_id": (plan.get("tool_registry") or {}).get("active_profile_id"),
            "safe_read_actions": "can_run_without_confirmation_when_registry_allows",
            "confirmation_required_actions": "must_be_confirmed_when_registry_marks_confirm",
            "blocked_actions": "cannot_run_until_Admin_changes_tool_permission_profile",
        },
    }


def run_assistant_action_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    command = str(data.get("command") or data.get("message") or data.get("text") or "").strip()
    execute_confirmed = bool(data.get("execute_confirmed") or data.get("confirm"))
    result = run_operator_command({
        "command": command,
        "profile": data.get("profile") or data.get("retrieval_profile"),
        "sources": data.get("sources"),
        "limit": data.get("limit") or 8,
        "execute_confirmed": execute_confirmed,
        "confirm": execute_confirmed,
        "force": data.get("force", False),
        "provider_type": data.get("provider_type"),
    })
    plan = result.get("plan") or {}
    actions = list(plan.get("actions") or [])
    try:
        record_tool_ledger_event({
            "actor": "assistant",
            "surface": "assistant",
            "intent": plan.get("intent"),
            "status": "planned",
            "tool_id": "assistant.action_review",
            "tool_label": "Assistant Action Review",
            "category": "assistant",
            "action_type": "assistant_action_review_plan",
            "risk_level": "read_only",
            "payload": {"command": command, "actions": actions},
            "result_summary": f"Assistant planned {len(actions)} action(s) through Neo Operator",
            "metadata": {"phase": "25", "source": "assistant.action_review.plan"},
        })
    except Exception:
        pass
    return {
        "ok": True,
        "schema_id": "neo.assistant.action_review.run.v1",
        "review_version": ASSISTANT_ACTION_REVIEW_VERSION,
        "status": result.get("status") or "completed",
        "command": command,
        "execute_confirmed": execute_confirmed,
        "intent": plan.get("intent"),
        "retrieval_profile": plan.get("retrieval_profile"),
        "actions": actions,
        "action_summary": _action_summary(actions),
        "executed_actions": result.get("executed_actions") or [],
        "blocked_actions": result.get("blocked_actions") or [],
        "results": result.get("results") or [],
        "retrieval_trace_id": (result.get("retrieval") or {}).get("trace_id"),
        "response_text": result.get("response_text") or "",
        "operator_result": result,
    }
