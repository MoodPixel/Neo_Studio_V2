from __future__ import annotations

from typing import Any

from neo_app.control_center.action_planner import plan_control_center_actions
from neo_app.internet.service import internet_access_status_payload, run_internet_access_payload
from neo_app.memory.service import get_memory_service
from neo_app.operator.contracts import (
    OPERATOR_PLAN_SCHEMA,
    execution_proof,
    make_execution_receipt,
    normalize_action_request,
)
from neo_app.tool_ledger import record_tool_ledger_event, tool_ledger_status_payload
from neo_app.tool_registry import annotate_action_with_tool_policy, tool_registry_status_payload

OPERATOR_SCHEMA_VERSION = "neo.operator.v1"
OPERATOR_RUNTIME_VERSION = "0.5.0"
OPERATOR_PHASE = "13"


def _permission_summary(actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "safe_read_actions": sum(1 for item in actions if not item.get("requires_confirmation") and item.get("status") != "blocked"),
        "confirmation_required": sum(1 for item in actions if item.get("requires_confirmation") and item.get("status") != "blocked"),
        "blocked": sum(1 for item in actions if item.get("status") == "blocked"),
    }


def _ledger(action: dict[str, Any], *, request: dict[str, Any], status: str, confirmed: bool, result_summary: str, blocked: bool = False, result: dict[str, Any] | None = None) -> str:
    try:
        recorded = record_tool_ledger_event({
            "actor": str(request.get("actor") or "operator"),
            "surface": str(request.get("surface") or "assistant"),
            "intent": str(request.get("intent") or "assistant.act"),
            "status": status,
            "blocked": blocked,
            "confirmed": confirmed,
            "action": action,
            "payload": action.get("payload") or {},
            "result_summary": result_summary,
            "memory_trace_id": str((result or {}).get("trace_id") or ""),
            "metadata": {
                "phase": OPERATOR_PHASE,
                "source": "operator.execution",
                "request_id": request.get("request_id"),
                "control_center_trace_id": request.get("trace_id") or "",
            },
        })
        return str(((recorded or {}).get("event") or {}).get("ledger_id") or "")
    except Exception:
        return ""


def operator_status_payload() -> dict[str, Any]:
    internet = internet_access_status_payload()
    tools = tool_registry_status_payload()
    return {
        "schema_id": "neo.operator.status.v1",
        "status": "ready",
        "label": "Neo Operator",
        "runtime_version": OPERATOR_RUNTIME_VERSION,
        "phase": OPERATOR_PHASE,
        "role": "permission_confirmation_execution_ledger",
        # text remains accepted only through the compatibility adapter. It is no
        # longer interpreted by Operator itself.
        "input_modes": ["structured_action_request", "text_compatibility_adapter", "text", "voice_transcribed_text"],
        "output_modes": ["execution_plan", "execution_receipts", "execution_proof"],
        "permission_policy": {
            "local_read": "tool_registry",
            "memory_retrieve": "tool_registry",
            "memory_index": "confirmation_required",
            "write_actions": "tool_registry",
            "internet": internet.get("mode") or "disabled",
            "external_actions": internet.get("permission_policy", {}).get("default", "disabled"),
            "tool_profile": tools.get("active_profile_id"),
        },
        "capabilities": [
            "structured_action_intake",
            "tool_permission_gating",
            "confirmation_enforcement",
            "fail_closed_execution_dispatch",
            "execution_receipts",
            "tool_execution_ledger",
            "legacy_text_adapter_via_control_center",
        ],
        "removed_capabilities": [
            "general_human_intent_detection",
            "source_selection_by_operator_intent",
            "general_answer_planning",
        ],
        "tool_execution_ledger": tool_ledger_status_payload(),
        "internet_access": {
            "status": internet.get("status"),
            "mode": internet.get("mode"),
            "provider_count": (internet.get("capabilities") or {}).get("provider_count", 0),
            "enabled_provider_count": (internet.get("capabilities") or {}).get("enabled_provider_count", 0),
        },
        "tool_registry": tools,
        "policy": "Assistant/Control Center decides what needs doing. Operator only checks permissions/confirmation, executes registered actions, and records proof in the execution ledger.",
    }


def plan_operator_actions(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = normalize_action_request(payload or {})
    planned: list[dict[str, Any]] = []
    for raw in request.get("actions") or []:
        action = annotate_action_with_tool_policy(raw)
        if not action.get("tool_id"):
            action["status"] = "blocked"
            action["reason"] = action.get("reason") or "unregistered_action_type"
            action["requires_confirmation"] = False
        planned.append(action)
        _ledger(
            action,
            request=request,
            status="blocked" if action.get("status") == "blocked" else "planned",
            confirmed=False,
            blocked=action.get("status") == "blocked",
            result_summary=(action.get("reason") or f"Structured action planned: {action.get('action_type')}")[:1200],
        )
    return {
        "ok": True,
        "schema_id": OPERATOR_PLAN_SCHEMA,
        "operator_version": OPERATOR_RUNTIME_VERSION,
        "status": "planned" if planned else "no_actions",
        "action_request": request,
        "request_id": request.get("request_id"),
        "intent": request.get("intent"),
        "command": request.get("command"),
        "actions": planned,
        "permission_summary": _permission_summary(planned),
        "tool_registry": tool_registry_status_payload(),
        "policy": "Operator did not infer or add actions. It only applied registry permission policy to the structured Control Center request.",
    }


def _execute_registered_action(action: dict[str, Any], data: dict[str, Any]) -> tuple[str, bool, dict[str, Any], str]:
    action_type = str(action.get("action_type") or "")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    memory = get_memory_service()
    if action_type == "memory_retrieve":
        result = memory.retrieve({**payload, "consumer": "operator_execution"})
        compact = {
            "trace_id": result.get("trace_id"),
            "result_count": len(result.get("results") or []),
            "results": result.get("results") or [],
        }
        return "completed", True, compact, f"Retrieved {compact['result_count']} memory result(s)"
    if action_type == "memory_index":
        source_id = str(payload.get("source_id") or "system_records")
        result = memory.index_source(source_id, force=bool(data.get("force", False)), limit=data.get("index_limit"))
        compact = {
            "source_id": source_id,
            "status": result.get("status"),
            "indexed_documents": int(result.get("indexed_documents") or 0),
            "indexed_chunks": int(result.get("indexed_chunks") or 0),
        }
        success = str(result.get("status") or "").lower() not in {"failed", "error", "blocked"}
        return ("completed" if success else "failed"), success, compact, f"Indexed {compact['indexed_documents']} document(s) / {compact['indexed_chunks']} chunk(s)"
    if action_type == "internet_research":
        result = run_internet_access_payload({
            "query": payload.get("query") or (payload.get("internet_plan") or {}).get("query") or data.get("command") or "",
            "provider_type": data.get("provider_type") or (payload.get("internet_plan") or {}).get("provider_type") or "search_api",
            "execute_confirmed": bool(data.get("execute_confirmed") or data.get("confirm")),
        })
        status = str(result.get("status") or "unknown")
        success = bool(result.get("ok")) and status not in {"blocked", "failed", "error"}
        return ("completed" if success else ("blocked" if status == "blocked" else "failed")), success, result, f"Internet/API context status: {status}"
    if action_type in {"surface_hint", "guide_current_tab", "ask_for_input"}:
        return "completed", True, payload, str(action.get("label") or "Advisory action completed")
    return "failed", False, {}, "No registered Operator executor exists for this action type"


def run_operator_actions(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    plan = plan_operator_actions(data)
    request = plan.get("action_request") or {}
    execute_confirmed = bool(data.get("execute_confirmed") or data.get("confirm"))
    receipts: list[dict[str, Any]] = []
    executed_actions: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    retrieval = None
    external_context = None

    for action in plan.get("actions") or []:
        if action.get("status") == "blocked":
            reason = str(action.get("reason") or "blocked_by_tool_permission_profile")
            ledger_id = _ledger(action, request=request, status="blocked", confirmed=False, blocked=True, result_summary=reason)
            receipt = make_execution_receipt(action, status="blocked", success=False, confirmed=False, reason=reason, ledger_id=ledger_id)
            receipts.append(receipt)
            blocked_actions.append({**action, "status": "blocked", "reason": reason})
            continue
        if action.get("requires_confirmation") and not execute_confirmed:
            reason = "confirmation_required"
            ledger_id = _ledger(action, request=request, status="blocked", confirmed=False, blocked=True, result_summary="Blocked until confirmation")
            receipt = make_execution_receipt(action, status="blocked", success=False, confirmed=False, reason=reason, ledger_id=ledger_id)
            receipts.append(receipt)
            blocked_actions.append({**action, "status": "blocked", "reason": reason})
            continue
        try:
            status, success, result, summary = _execute_registered_action(action, data)
        except Exception as exc:
            status, success, result, summary = "failed", False, {"error": str(exc)}, f"Execution failed: {exc}"
        blocked = status == "blocked"
        ledger_id = _ledger(action, request=request, status="executed" if success else status, confirmed=bool(action.get("requires_confirmation") and execute_confirmed), blocked=blocked, result_summary=summary, result=result)
        receipt = make_execution_receipt(
            action,
            status=status,
            success=success,
            confirmed=bool(action.get("requires_confirmation") and execute_confirmed),
            result=result,
            reason="" if success else summary,
            ledger_id=ledger_id,
        )
        receipts.append(receipt)
        compact = {
            "action_id": action.get("action_id"),
            "action_type": action.get("action_type"),
            "tool_id": action.get("tool_id"),
            "status": status,
            **(result if isinstance(result, dict) else {}),
            "receipt_id": receipt.get("receipt_id"),
            "ledger_id": ledger_id,
        }
        if success:
            executed_actions.append(compact)
        elif blocked:
            blocked_actions.append({**action, "status": "blocked", "reason": summary})
        else:
            failed_actions.append({**action, "status": "failed", "reason": summary})
        if action.get("action_type") == "memory_retrieve" and success:
            retrieval = result
        if action.get("action_type") == "internet_research":
            external_context = result

    proof = execution_proof(receipts)
    if failed_actions:
        status = "completed_with_failures"
    elif blocked_actions:
        status = "completed_with_blocked_actions"
    elif receipts:
        status = "completed"
    else:
        status = "no_actions"
    return {
        "ok": not bool(failed_actions),
        "schema_id": "neo.operator.execution_run.v1",
        "operator_version": OPERATOR_RUNTIME_VERSION,
        "status": status,
        "plan": plan,
        "action_request": request,
        "executed_actions": executed_actions,
        "blocked_actions": blocked_actions,
        "failed_actions": failed_actions,
        "execution_receipts": receipts,
        "execution_proof": proof,
        "retrieval": retrieval,
        "external_context": external_context,
        "response_text": _execution_response_text(proof, blocked_actions, failed_actions),
    }


def _execution_response_text(proof: dict[str, Any], blocked: list[dict[str, Any]], failed: list[dict[str, Any]]) -> str:
    if not proof.get("receipt_count"):
        return "No executable Operator action was requested."
    parts = [f"Executed {proof.get('successful_receipt_count', 0)} action(s)."]
    if blocked:
        parts.append(f"{len(blocked)} action(s) were blocked or still require confirmation.")
    if failed:
        parts.append(f"{len(failed)} action(s) failed.")
    if proof.get("claimable_success"):
        parts.append("Execution receipt proof is available for completed external/write action(s).")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Compatibility adapters
# ---------------------------------------------------------------------------
# These routes/functions remain readable for old Voice/Admin/UI callers. The
# text-to-action understanding happens in Control Center, then Operator receives
# the resulting structured request exactly like every Phase 13 caller.


def plan_operator_command(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    action_request = plan_control_center_actions(data, compatibility_read_fallback=True)
    plan = plan_operator_actions({**data, "action_request": action_request})
    return {
        **plan,
        "schema_id": "neo.operator.plan.v1",
        "status": "planned" if action_request.get("actions") else "needs_input" if action_request.get("intent") == "empty" else "no_actions",
        "command": action_request.get("command") or "",
        "intent": action_request.get("intent") or "general",
        "confidence": action_request.get("confidence") or 0.0,
        "matched_terms": action_request.get("matched_terms") or [],
        "retrieval_profile": action_request.get("retrieval_profile") or "smart",
        "sources": action_request.get("sources") or [],
        "compatibility_adapter": "control_center_action_planner",
    }


def _record_legacy_operator_memory_event(plan: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        memory = get_memory_service()
        event = memory.record_event({
            "namespace": "operator",
            "surface": "assistant",
            "source": "assistant",
            "event_type": "operator.compatibility_command.ran",
            "title": "Operator compatibility command ran",
            "summary": str(result.get("response_text") or "Operator compatibility command ran")[:900],
            "tags": ["operator", "compatibility", str(plan.get("intent") or "general")],
            "payload": {
                "command": plan.get("command"),
                "intent": plan.get("intent"),
                "request_id": (result.get("action_request") or {}).get("request_id"),
                "execution_proof": result.get("execution_proof") or {},
            },
            "importance": "normal",
            "should_embed": True,
        })
        return event.get("event")
    except Exception:
        return None


def run_operator_command(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    legacy_plan = plan_operator_command(data)
    structured = run_operator_actions({**data, "action_request": legacy_plan.get("action_request") or {}})
    event = _record_legacy_operator_memory_event(legacy_plan, structured)
    results = []
    retrieval = structured.get("retrieval") or {}
    for item in retrieval.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title") or item.get("source_path") or "Memory result",
            "source_id": item.get("source_id"),
            "source_path": item.get("source_path"),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "score": item.get("score"),
            "retrieval_type": item.get("retrieval_type"),
            "snippet": item.get("snippet") or item.get("summary") or "",
        })
    return {
        **structured,
        "schema_id": "neo.operator.run.v1",
        "plan": legacy_plan,
        "results": results,
        "memory_event": event,
        "compatibility_adapter": "control_center_action_planner",
    }
