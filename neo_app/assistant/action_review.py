from __future__ import annotations

from typing import Any

from neo_app.control_center.action_planner import plan_control_center_actions
from neo_app.operator.service import operator_status_payload, plan_operator_actions, run_operator_actions
from neo_app.tool_registry import tool_registry_status_payload
from neo_app.tool_ledger import record_tool_ledger_event, tool_ledger_status_payload

ASSISTANT_ACTION_REVIEW_SCHEMA = "neo.assistant.action_review.v1"
ASSISTANT_ACTION_REVIEW_VERSION = "0.3.0"
ASSISTANT_ACTION_REVIEW_PHASE = "13"


def _action_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    read_only = [a for a in actions if not a.get("requires_confirmation") and a.get("status") != "blocked"]
    gated = [a for a in actions if a.get("requires_confirmation") and a.get("status") != "blocked"]
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
        "phase": ASSISTANT_ACTION_REVIEW_PHASE,
        "operator_status": operator.get("status"),
        "permission_policy": operator.get("permission_policy") or {},
        "tool_registry": tools,
        "capabilities": [
            "assistant_control_center_action_planning",
            "structured_operator_handoff",
            "operator_permission_gating",
            "read_only_action_preview",
            "confirmation_required_execution",
            "execution_receipt_proof",
            "tool_execution_ledger",
        ],
        "tool_execution_ledger": tool_ledger_status_payload(),
        "policy": "Assistant/Control Center decides which actions are needed. Neo Operator receives the structured request and only gates, confirms, executes, and records proof.",
    }


def _control_center_action_request(data: dict[str, Any], command: str) -> dict[str, Any]:
    if isinstance(data.get("action_request"), dict):
        return data.get("action_request") or {}
    if isinstance(data.get("actions"), list):
        return plan_control_center_actions({
            **data,
            "command": command,
            "actions": data.get("actions"),
            "intent": data.get("intent") or "assistant.act",
        }, compatibility_read_fallback=False)
    return plan_control_center_actions({
        **data,
        "command": command,
        "profile": data.get("profile") or data.get("retrieval_profile"),
    }, compatibility_read_fallback=True)


def plan_assistant_action_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    command = str(data.get("command") or data.get("message") or data.get("text") or "").strip()
    action_request = _control_center_action_request(data, command)
    plan = plan_operator_actions({**data, "action_request": action_request, "actor": "assistant"})
    actions = list(plan.get("actions") or [])
    try:
        record_tool_ledger_event({
            "actor": "assistant",
            "surface": "assistant",
            "intent": action_request.get("intent"),
            "status": "planned",
            "tool_id": "assistant.action_review",
            "tool_label": "Assistant Action Review",
            "category": "assistant",
            "action_type": "assistant_action_review_plan",
            "risk_level": "read_only",
            "payload": {"request_id": action_request.get("request_id"), "actions": actions},
            "result_summary": f"Assistant/Control Center prepared {len(actions)} structured action(s) for Operator",
            "metadata": {"phase": ASSISTANT_ACTION_REVIEW_PHASE, "source": "assistant.action_review.plan"},
        })
    except Exception:
        pass
    return {
        "ok": True,
        "schema_id": ASSISTANT_ACTION_REVIEW_SCHEMA,
        "review_version": ASSISTANT_ACTION_REVIEW_VERSION,
        "status": "planned" if actions else "no_actions",
        "command": command,
        "intent": action_request.get("intent"),
        "confidence": action_request.get("confidence"),
        "retrieval_profile": action_request.get("retrieval_profile"),
        "sources": action_request.get("sources") or [],
        "action_request": action_request,
        "actions": actions,
        "action_summary": _action_summary(actions),
        "permission_summary": plan.get("permission_summary") or {},
        "operator_plan": plan,
        "review_policy": {
            "planner": "assistant_control_center",
            "executor": "neo_operator",
            "source": "central_tool_registry",
            "active_profile_id": (plan.get("tool_registry") or {}).get("active_profile_id"),
            "safe_read_actions": "can_run_without_confirmation_when_registry_allows",
            "confirmation_required_actions": "must_be_confirmed_when_registry_marks_confirm",
            "blocked_actions": "cannot_execute_until_a_registered_tool_and_permission_exist",
        },
    }


def run_assistant_action_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    command = str(data.get("command") or data.get("message") or data.get("text") or "").strip()
    action_request = _control_center_action_request(data, command)
    execute_confirmed = bool(data.get("execute_confirmed") or data.get("confirm"))
    result = run_operator_actions({
        **data,
        "action_request": action_request,
        "actor": "assistant",
        "execute_confirmed": execute_confirmed,
        "confirm": execute_confirmed,
    })
    plan = result.get("plan") or {}
    actions = list(plan.get("actions") or [])
    return {
        "ok": bool(result.get("ok")),
        "schema_id": "neo.assistant.action_review.run.v1",
        "review_version": ASSISTANT_ACTION_REVIEW_VERSION,
        "status": result.get("status") or "completed",
        "command": command,
        "execute_confirmed": execute_confirmed,
        "intent": action_request.get("intent"),
        "retrieval_profile": action_request.get("retrieval_profile"),
        "action_request": action_request,
        "actions": actions,
        "action_summary": _action_summary(actions),
        "executed_actions": result.get("executed_actions") or [],
        "blocked_actions": result.get("blocked_actions") or [],
        "failed_actions": result.get("failed_actions") or [],
        "execution_receipts": result.get("execution_receipts") or [],
        "execution_proof": result.get("execution_proof") or {},
        "results": ((result.get("retrieval") or {}).get("results") or []),
        "retrieval_trace_id": (result.get("retrieval") or {}).get("trace_id"),
        "response_text": result.get("response_text") or "",
        "operator_result": result,
    }
