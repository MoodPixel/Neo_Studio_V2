from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def memory_project_qa_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    """Read-only QA sweep for Phase 41 Memory + Project regression checks."""
    from neo_app.admin.engine import admin_engine_state_payload
    from neo_app.assistant.context_pack import build_context_pack
    from neo_app.project_workspace import (
        active_project_payload,
        project_workspace_status_payload,
        project_workspace_context_payload,
        project_workspace_records_for_index,
        project_package_builder_payload,
        project_surface_actions_payload,
    )

    active = active_project_payload()
    resolved_project_id = str(project_id or active.get("active_project_id") or "general")
    admin = admin_engine_state_payload()
    workspace_status = project_workspace_status_payload()
    workspace_context = project_workspace_context_payload(resolved_project_id, limit=limit)
    package_preflight = project_package_builder_payload(resolved_project_id)
    surface_actions = project_surface_actions_payload(resolved_project_id, limit=limit)
    index_rows = project_workspace_records_for_index(limit=limit)

    context_pack = build_context_pack(project_id=resolved_project_id, message="Phase 41 QA regression check", retrieval_profile="fast")
    diagnostics = context_pack.get("diagnostics") if isinstance(context_pack, dict) else {}

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, label: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"check_id": check_id, "label": label, "ok": bool(ok), "severity": severity, "detail": detail})

    add_check(
        "admin_engine_ready",
        "Admin Memory Engine reports ready",
        admin.get("status") == "ready" and (admin.get("readiness") or {}).get("ready") is True,
        f"status={admin.get('status')} readiness.ready={(admin.get('readiness') or {}).get('ready')}",
    )
    add_check(
        "workspace_ready",
        "Project Workspace reports ready",
        workspace_status.get("status") == "ready",
        f"status={workspace_status.get('status')} projects={workspace_status.get('project_count')}",
    )
    add_check(
        "context_pack_project_pin",
        "Assistant context pack stays pinned to requested project",
        diagnostics.get("project_id") == resolved_project_id,
        f"requested={resolved_project_id} resolved={diagnostics.get('project_id')}",
    )
    add_check(
        "workspace_fallback_visible",
        "Workspace fallback is explicit when requested workspace is missing",
        "fallback_used" in workspace_context and "requested_project_id" in workspace_context,
        f"requested={workspace_context.get('requested_project_id')} fallback={workspace_context.get('fallback_used')}",
        severity="warning",
    )
    add_check(
        "package_preflight_available",
        "Package Builder preflight is available",
        bool(package_preflight.get("ok")) and isinstance(package_preflight.get("preflight"), dict),
        f"checks={_safe_count((package_preflight.get('preflight') or {}).get('checks'))}",
    )
    add_check(
        "surface_actions_available",
        "Cross-surface action ledger is readable",
        bool(surface_actions.get("ok")) and isinstance(surface_actions.get("actions"), list),
        f"actions={_safe_count(surface_actions.get('actions'))}",
    )
    add_check(
        "project_index_rows_available",
        "Project records expose index rows for Memory Engine",
        isinstance(index_rows, list),
        f"sample_rows={len(index_rows)}",
    )

    failed = [check for check in checks if not check.get("ok") and check.get("severity") == "error"]
    warnings = [check for check in checks if not check.get("ok") and check.get("severity") == "warning"]
    return {
        "ok": not failed,
        "schema_id": "neo.memory_project.qa.v1",
        "status": "ready" if not failed else "needs_repair",
        "checked_at": _now(),
        "project_id": resolved_project_id,
        "active_project_id": active.get("active_project_id"),
        "summary": {
            "check_count": len(checks),
            "failed_count": len(failed),
            "warning_count": len(warnings),
            "workspace_project_count": workspace_status.get("project_count", 0),
            "workspace_context_count": workspace_status.get("context_count", 0),
            "workspace_link_count": workspace_status.get("link_count", 0),
            "surface_action_count": workspace_status.get("surface_action_count", 0),
            "project_index_sample_count": len(index_rows),
        },
        "checks": checks,
        "diagnostics": {
            "admin_status": admin.get("status"),
            "workspace_status": workspace_status.get("status"),
            "context_pack_project_id": diagnostics.get("project_id"),
            "workspace_requested_project_id": workspace_context.get("requested_project_id"),
            "workspace_fallback_used": workspace_context.get("fallback_used"),
            "package_preflight_status": package_preflight.get("status"),
        },
        "policy": "Phase 41 QA is read-only by default. Repair only refreshes local Memory Engine indexes; it never deletes, publishes, or executes external tools.",
    }


def memory_project_regression_repair_payload(project_id: str = "") -> dict[str, Any]:
    """Safe repair hook: refresh local Memory Engine indexes and return QA state."""
    index_results: dict[str, Any] = {}
    try:
        from neo_app.memory.service import get_memory_service
        service = get_memory_service()
        index_results["project_workspace"] = service.index_source("project_workspace", force=True, limit=5)
        index_results["assistant_memory"] = service.index_source("assistant_memory", force=True, limit=5)
        try:
            index_results["system_records"] = service.index_source("system_records", force=False, limit=5)
        except Exception as exc:
            index_results["system_records"] = {"ok": False, "status": "skipped", "message": str(exc)[:300]}
    except Exception as exc:
        index_results["memory_engine"] = {"ok": False, "status": "repair_failed", "message": str(exc)[:500]}
    qa = memory_project_qa_payload(project_id)
    return {
        "ok": qa.get("ok", False),
        "schema_id": "neo.memory_project.qa_repair.v1",
        "status": "ready" if qa.get("ok") else "needs_repair",
        "repaired_at": _now(),
        "project_id": qa.get("project_id"),
        "index_results": index_results,
        "qa": qa,
        "policy": "Safe local index refresh only. No files are deleted and no external actions are performed.",
    }
