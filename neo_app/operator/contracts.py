from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

ACTION_REQUEST_SCHEMA = "neo.control_center.action_request.v1"
OPERATOR_PLAN_SCHEMA = "neo.operator.execution_plan.v1"
EXECUTION_RECEIPT_SCHEMA = "neo.operator.execution_receipt.v1"
EXECUTION_PROOF_SCHEMA = "neo.operator.execution_proof.v1"

_READ_ACTIONS = {"memory_retrieve", "memory_inspect", "surface_hint", "guide_current_tab", "ask_for_input"}
_WRITE_ACTIONS = {"memory_index", "memory_review", "save_project_knowledge", "roleplay_memory_sync"}
_EXTERNAL_ACTIONS = {"internet_research"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def action_effect_class(action_type: str, explicit: str = "") -> str:
    value = str(explicit or "").strip().lower()
    if value in {"read", "write", "external", "advisory"}:
        return value
    action_type = str(action_type or "").strip()
    if action_type in _READ_ACTIONS:
        return "read" if action_type not in {"surface_hint", "ask_for_input", "guide_current_tab"} else "advisory"
    if action_type in _WRITE_ACTIONS:
        return "write"
    if action_type in _EXTERNAL_ACTIONS:
        return "external"
    return "write"


def normalize_action(action: dict[str, Any] | None, *, requested_by: str = "control_center") -> dict[str, Any]:
    raw = deepcopy(action) if isinstance(action, dict) else {}
    action_type = str(raw.get("action_type") or raw.get("type") or "unsupported_action").strip() or "unsupported_action"
    action_id = str(raw.get("action_id") or f"op_act_{uuid4().hex[:12]}")
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    effect_class = action_effect_class(action_type, str(raw.get("effect_class") or ""))
    return {
        **raw,
        "action_id": action_id,
        "action_type": action_type,
        "label": str(raw.get("label") or raw.get("title") or action_type.replace("_", " ").title()),
        "risk_level": str(raw.get("risk_level") or raw.get("risk") or ("read_only" if effect_class in {"read", "advisory"} else "medium")),
        "effect_class": effect_class,
        "requires_confirmation": bool(raw.get("requires_confirmation", effect_class in {"write", "external"})),
        "payload": payload,
        "status": str(raw.get("status") or "planned"),
        "requested_by": str(raw.get("requested_by") or requested_by),
    }


def normalize_action_request(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    nested = data.get("action_request") if isinstance(data.get("action_request"), dict) else data
    raw_actions = nested.get("actions") if isinstance(nested.get("actions"), list) else []
    requested_by = str(nested.get("requested_by") or data.get("requested_by") or "control_center")
    return {
        "schema_id": ACTION_REQUEST_SCHEMA,
        "request_id": str(nested.get("request_id") or f"actreq_{uuid4().hex[:14]}"),
        "created_at": str(nested.get("created_at") or _now()),
        "requested_by": requested_by,
        "actor": str(nested.get("actor") or data.get("actor") or "assistant"),
        "surface": str(nested.get("surface") or nested.get("surface_id") or data.get("surface") or "assistant"),
        "scope_id": str(nested.get("scope_id") or data.get("scope_id") or "general"),
        "project_id": str(nested.get("project_id") or data.get("project_id") or ""),
        "trace_id": str(nested.get("trace_id") or data.get("trace_id") or ""),
        "intent": str(nested.get("intent") or data.get("intent") or "assistant.act"),
        "command": str(nested.get("command") or data.get("command") or ""),
        "actions": [normalize_action(item, requested_by=requested_by) for item in raw_actions if isinstance(item, dict)],
        "metadata": nested.get("metadata") if isinstance(nested.get("metadata"), dict) else {},
    }


def make_execution_receipt(
    action: dict[str, Any],
    *,
    status: str,
    success: bool,
    confirmed: bool,
    result: dict[str, Any] | None = None,
    reason: str = "",
    ledger_id: str = "",
) -> dict[str, Any]:
    effect_class = action_effect_class(str(action.get("action_type") or ""), str(action.get("effect_class") or ""))
    return {
        "schema_id": EXECUTION_RECEIPT_SCHEMA,
        "receipt_id": f"receipt_{uuid4().hex[:14]}",
        "created_at": _now(),
        "action_id": str(action.get("action_id") or ""),
        "action_type": str(action.get("action_type") or "unknown"),
        "tool_id": str(action.get("tool_id") or ""),
        "status": str(status or "unknown"),
        "success": bool(success),
        "confirmed": bool(confirmed),
        "effect_class": effect_class,
        "claimable_success": bool(success and effect_class in {"write", "external"}),
        "reason": str(reason or ""),
        "ledger_id": str(ledger_id or ""),
        "result": result if isinstance(result, dict) else {},
    }


def execution_proof(receipts: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = [item for item in (receipts or []) if isinstance(item, dict)]
    successful = [item for item in rows if bool(item.get("success"))]
    blocked = [item for item in rows if str(item.get("status") or "").lower() == "blocked"]
    failed = [item for item in rows if str(item.get("status") or "").lower() in {"failed", "error"}]
    claimable = [item for item in successful if bool(item.get("claimable_success"))]
    mutating_or_external = [item for item in rows if str(item.get("effect_class") or "") in {"write", "external"}]
    required_effects_succeeded = bool(mutating_or_external) and all(bool(item.get("success")) for item in mutating_or_external)
    return {
        "schema_id": EXECUTION_PROOF_SCHEMA,
        "receipt_count": len(rows),
        "successful_receipt_count": len(successful),
        "blocked_receipt_count": len(blocked),
        "failed_receipt_count": len(failed),
        "claimable_success_count": len(claimable),
        "claimable_success": bool(claimable),
        "all_actions_succeeded": bool(rows) and len(successful) == len(rows),
        "all_required_effects_succeeded": required_effects_succeeded,
        "successful_action_types": [str(item.get("action_type") or "") for item in successful],
        "claimable_action_types": [str(item.get("action_type") or "") for item in claimable],
        "receipt_ids": [str(item.get("receipt_id") or "") for item in rows if item.get("receipt_id")],
    }
