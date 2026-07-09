from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

SCHEMA_ID: Final[str] = "neo.runtime.progress_watchdog.v25_4"

RUNNING_STATUSES: Final[set[str]] = {"queued", "running", "importing"}
RECOVERABLE_STATUSES: Final[set[str]] = {
    "saved_in_comfy_only",
    "import_failed",
    "completed_no_outputs_recoverable",
}
TERMINAL_STATUSES: Final[set[str]] = {"completed", "completed_with_warnings", "failed", "cancelled"} | RECOVERABLE_STATUSES

DEFAULT_POLICY: Final[dict[str, Any]] = {
    "schema_id": SCHEMA_ID,
    "surface": "image",
    "poll_fetch_timeout_ms": 45000,
    "finalization_floor_percent": 88,
    "finalization_stall_ms": 120000,
    "long_running_notice_ms": 900000,
    "hard_watchdog_ms": 2700000,
    "same_progress_epsilon": 0.5,
    "recoverable_statuses": sorted(RECOVERABLE_STATUSES),
    "running_statuses": sorted(RUNNING_STATUSES),
    "terminal_statuses": sorted(TERMINAL_STATUSES),
    "frontend_actions": {
        "poll_timeout": "detach_with_recovery",
        "finalization_stall": "detach_with_recovery",
        "long_running": "warn_keep_polling",
        "recoverable_status": "show_recover_button",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def progress_watchdog_policy(*, surface: str = "image", profile_id: str = "", job_id: str = "") -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    policy["surface"] = str(surface or "image")
    policy["profile_id"] = str(profile_id or "")
    policy["job_id"] = str(job_id or "")
    policy["created_at"] = _now()
    return policy


def recovery_endpoint_for_job(*, surface: str = "image", profile_id: str = "", job_id: str = "") -> str:
    if str(surface or "image") != "image" or not profile_id or not job_id:
        return ""
    return f"/api/image/jobs/{profile_id}/{job_id}/recover"


def watchdog_recovery_payload(
    *,
    surface: str = "image",
    profile_id: str = "",
    job_id: str = "",
    reason: str = "progress_watchdog",
    message: str = "Progress watchdog stopped waiting for a final poll response.",
) -> dict[str, Any]:
    endpoint = recovery_endpoint_for_job(surface=surface, profile_id=profile_id, job_id=job_id)
    return {
        "schema_id": SCHEMA_ID,
        "surface": str(surface or "image"),
        "profile_id": str(profile_id or ""),
        "job_id": str(job_id or ""),
        "reason": str(reason or "progress_watchdog"),
        "message": str(message or ""),
        "recoverable": bool(endpoint),
        "recovery_endpoint": endpoint,
        "label": "Progress watchdog stopped waiting — recovery available" if endpoint else "Progress watchdog stopped waiting",
        "created_at": _now(),
    }


def attach_progress_watchdog(
    payload: dict[str, Any],
    *,
    surface: str = "image",
    profile_id: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """Attach V25.4 watchdog policy metadata to a provider poll/generate payload.

    The frontend owns live timing decisions because only the browser knows when a
    fetch stalls, websocket progress goes quiet, or the UI sits near finalization.
    The backend still publishes one policy object so all image job responses carry
    the same recoverable-state contract.
    """

    output = dict(payload or {})
    runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
    resolved_job_id = str(job_id or output.get("job_id") or "")
    resolved_profile = str(profile_id or output.get("profile_id") or "")
    runtime["progress_watchdog"] = progress_watchdog_policy(surface=surface, profile_id=resolved_profile, job_id=resolved_job_id)
    output["runtime"] = runtime
    if str(output.get("status") or "") in RECOVERABLE_STATUSES:
        output.setdefault(
            "neo_recovery",
            watchdog_recovery_payload(
                surface=surface,
                profile_id=resolved_profile,
                job_id=resolved_job_id,
                reason=str(output.get("status") or "recoverable_status"),
                message=str(output.get("message") or "Image output needs recovery."),
            ),
        )
    return output
