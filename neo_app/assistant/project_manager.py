from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neo_app.project_workspace import (
    active_project_payload,
    get_project_workspace,
    list_project_briefs,
    list_project_context,
    list_project_deliverables,
    list_project_links,
    list_project_milestones,
    list_project_review_items,
    list_project_surface_actions,
    list_project_timeline,
    project_activity_intelligence_payload,
    project_delivery_dashboard_payload,
    project_package_builder_payload,
    project_workspace_asset_tray_payload,
    project_review_queue_payload,
)

SCHEMA_ID = "neo.assistant.project_manager.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, fallback: str = "") -> str:
    return str(value if value is not None else fallback).strip()


def _clip(value: Any, limit: int = 220) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _record_ref(kind: str, record: dict[str, Any], index: int = 1) -> dict[str, Any]:
    id_keys = {
        "project": "project_id",
        "milestone": "milestone_id",
        "deliverable": "deliverable_id",
        "review": "review_id",
        "brief": "brief_id",
        "timeline": "event_id",
        "context": "context_id",
        "link": "link_id",
        "handoff": "handoff_id",
        "surface_action": "action_id",
        "package": "package_id",
    }
    key = id_keys.get(kind, "id")
    record_id = _text(record.get(key) or record.get("id") or f"{kind}_{index}", f"{kind}_{index}")
    title = _text(record.get("title") or record.get("name") or record.get("event_type") or record_id, record_id)
    return {
        "ref": f"project:{kind}:{record_id}",
        "kind": kind,
        "record_id": record_id,
        "title": title,
        "status": _text(record.get("status") or record.get("review_state") or record.get("event_type") or ""),
        "surface": _text(record.get("surface") or record.get("source_surface") or "project_workspace"),
        "timestamp": _text(record.get("updated_at") or record.get("created_at") or record.get("time") or ""),
        "summary": _clip(record.get("summary") or record.get("description") or record.get("content_preview") or record.get("text") or record.get("notes") or ""),
    }


def _status_bucket(rows: list[dict[str, Any]], key: str = "status") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = _text(row.get(key), "unknown") or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _build_blockers(deliverables: list[dict[str, Any]], reviews: list[dict[str, Any]], milestones: list[dict[str, Any]], package_blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for item in deliverables:
        status = _text(item.get("status"))
        review_state = _text(item.get("review_state"))
        if status in {"blocked", "revision_needed"} or review_state in {"revision_requested", "blocked"}:
            ref = _record_ref("deliverable", item)
            blockers.append({
                "level": "high",
                "title": item.get("title") or "Deliverable needs attention",
                "reason": f"Deliverable status is {status or 'unknown'} / review state is {review_state or 'unknown'}.",
                "source_ref": ref["ref"],
                "citation": ref,
            })
    for item in reviews:
        status = _text(item.get("status"))
        if status in {"revision_requested", "changes_requested", "blocked"}:
            ref = _record_ref("review", item)
            blockers.append({
                "level": "high",
                "title": item.get("title") or "Review item needs changes",
                "reason": f"Review status is {status}.",
                "source_ref": ref["ref"],
                "citation": ref,
            })
        elif status in {"queued", "pending", "in_review"}:
            ref = _record_ref("review", item)
            blockers.append({
                "level": "medium",
                "title": item.get("title") or "Review is still open",
                "reason": f"Review status is {status}.",
                "source_ref": ref["ref"],
                "citation": ref,
            })
    for item in milestones:
        status = _text(item.get("status"))
        if status in {"blocked", "at_risk"}:
            ref = _record_ref("milestone", item)
            blockers.append({
                "level": "medium",
                "title": item.get("title") or "Milestone needs attention",
                "reason": f"Milestone status is {status}.",
                "source_ref": ref["ref"],
                "citation": ref,
            })
    for item in package_blockers:
        if _text(item.get("level")) != "ok":
            blockers.append({
                "level": _text(item.get("level"), "medium"),
                "title": _text(item.get("label"), "Package blocker"),
                "reason": f"Package builder detected {item.get('count', 0)} item(s).",
                "source_ref": "project:package_builder:preflight",
                "citation": {"ref": "project:package_builder:preflight", "kind": "package_builder", "title": _text(item.get("label"), "Package blocker")},
            })
    # Stable severity ordering.
    order = {"high": 0, "medium": 1, "low": 2, "ok": 3}
    blockers.sort(key=lambda row: (order.get(_text(row.get("level")), 2), _text(row.get("title")).lower()))
    return blockers[:10]


def _build_next_actions(deliverables: list[dict[str, Any]], reviews: list[dict[str, Any]], milestones: list[dict[str, Any]], packages: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if blockers:
        first = blockers[0]
        actions.append({
            "priority": "high",
            "action": f"Resolve blocker: {first.get('title')}",
            "reason": first.get("reason") or "A project record needs attention before clean delivery.",
            "source_ref": first.get("source_ref"),
        })
    ready = [d for d in deliverables if _text(d.get("status")) in {"ready_for_review", "ready", "done"} and _text(d.get("review_state")) not in {"approved", "client_approved"}]
    if ready:
        ref = _record_ref("deliverable", ready[0])
        actions.append({"priority": "high", "action": f"Queue review for {ready[0].get('title') or 'ready deliverable'}", "reason": "A deliverable looks ready but is not approved yet.", "source_ref": ref["ref"]})
    open_milestones = [m for m in milestones if _text(m.get("status")) in {"planned", "active", "in_progress"}]
    if open_milestones:
        ref = _record_ref("milestone", open_milestones[0])
        actions.append({"priority": "normal", "action": f"Update milestone: {open_milestones[0].get('title') or 'open milestone'}", "reason": "Open milestone still needs a current state or completion note.", "source_ref": ref["ref"]})
    pending_reviews = [r for r in reviews if _text(r.get("status")) in {"queued", "pending", "in_review"}]
    if pending_reviews:
        ref = _record_ref("review", pending_reviews[0])
        actions.append({"priority": "normal", "action": f"Finish review: {pending_reviews[0].get('title') or 'pending review'}", "reason": "The review queue still contains pending work.", "source_ref": ref["ref"]})
    approved = [d for d in deliverables if _text(d.get("status")) in {"approved", "delivered", "complete"} or _text(d.get("review_state")) in {"approved", "client_approved"}]
    if approved and not packages:
        ref = _record_ref("deliverable", approved[0])
        actions.append({"priority": "normal", "action": "Build a delivery package", "reason": "Approved deliverables exist and no package is recorded yet.", "source_ref": ref["ref"]})
    if not actions:
        actions.append({"priority": "low", "action": "Add more project context", "reason": "No urgent blockers were detected; richer context will improve summaries and delivery prep.", "source_ref": "project:workspace:summary"})
    return actions[:8]


def _answer_text(project: dict[str, Any], counts: dict[str, int], blockers: list[dict[str, Any]], next_actions: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    project_name = project.get("name") or project.get("project_id") or "Active project"
    lines = [
        f"Project Manager snapshot for {project_name}.",
        f"Status: {project.get('status') or 'active'} · {counts.get('milestones', 0)} milestone(s), {counts.get('deliverables', 0)} deliverable(s), {counts.get('reviews', 0)} review item(s), {counts.get('packages', 0)} package(s).",
    ]
    if blockers:
        lines.append(f"Main blocker: {blockers[0].get('title')} — {blockers[0].get('reason')} [{blockers[0].get('source_ref')}]")
    else:
        lines.append("No critical blocker was detected from current project records.")
    if next_actions:
        action = next_actions[0]
        lines.append(f"Recommended next action: {action.get('action')} — {action.get('reason')} [{action.get('source_ref')}]")
    if citations:
        refs = ", ".join([c.get("ref", "") for c in citations[:5] if c.get("ref")])
        if refs:
            lines.append(f"Grounded refs: {refs}.")
    return "\n".join(lines)


def assistant_project_manager_payload(project_id: str = "", *, question: str = "", limit: int = 80) -> dict[str, Any]:
    """Build a source-grounded project manager snapshot for Assistant UI.

    This is intentionally read-only. It suggests actions and points to project refs,
    but does not execute workspace changes.
    """
    active = active_project_payload()
    resolved_project_id = _text(project_id or active.get("active_project_id") or "general", "general")
    project = get_project_workspace(resolved_project_id) or get_project_workspace("general") or {}

    milestones = list_project_milestones(resolved_project_id, limit=limit)
    deliverables = list_project_deliverables(resolved_project_id, limit=limit)
    reviews = list_project_review_items(resolved_project_id, limit=limit)
    timeline = list_project_timeline(resolved_project_id, limit=limit)
    briefs = list_project_briefs(resolved_project_id, limit=limit)
    context = list_project_context(resolved_project_id, limit=limit)
    links = list_project_links(resolved_project_id, limit=limit)
    surface_actions = list_project_surface_actions(resolved_project_id, limit=limit)
    tray = project_workspace_asset_tray_payload(resolved_project_id, limit=min(limit, 60))
    activity = project_activity_intelligence_payload(resolved_project_id, limit=min(limit, 80))
    review_queue = project_review_queue_payload(resolved_project_id, limit=min(limit, 80), include_auto=True)
    dashboard = project_delivery_dashboard_payload(resolved_project_id, audience="internal", limit=min(limit, 120))
    package_builder = project_package_builder_payload(resolved_project_id, limit=min(limit, 50))
    packages = package_builder.get("recent_packages") or []

    counts = {
        "milestones": len(milestones),
        "deliverables": len(deliverables),
        "reviews": len(reviews),
        "timeline": len(timeline),
        "briefs": len(briefs),
        "context": len(context),
        "links": len(links),
        "surface_actions": len(surface_actions),
        "packages": len(packages),
        "handoffs": int((tray.get("summary") or {}).get("handoff_count") or 0),
    }
    blockers = _build_blockers(deliverables, reviews, milestones, package_builder.get("package_blockers") or [])
    next_actions = _build_next_actions(deliverables, reviews, milestones, packages, blockers)

    citations: list[dict[str, Any]] = [_record_ref("project", project)] if project else []
    citations.extend(_record_ref("deliverable", item, i + 1) for i, item in enumerate(deliverables[:8]))
    citations.extend(_record_ref("milestone", item, i + 1) for i, item in enumerate(milestones[:6]))
    citations.extend(_record_ref("review", item, i + 1) for i, item in enumerate(reviews[:6]))
    citations.extend(_record_ref("brief", item, i + 1) for i, item in enumerate(briefs[:4]))
    citations.extend(_record_ref("timeline", item, i + 1) for i, item in enumerate(timeline[:6]))
    citations.extend(_record_ref("context", item, i + 1) for i, item in enumerate(context[:4]))
    citations.extend(_record_ref("link", item, i + 1) for i, item in enumerate(links[:4]))
    citations.extend(_record_ref("surface_action", item, i + 1) for i, item in enumerate(surface_actions[:4]))
    # De-dupe refs while preserving order.
    seen: set[str] = set()
    citations = [c for c in citations if not (c.get("ref") in seen or seen.add(c.get("ref", "")))]

    status = "blocked" if any(b.get("level") == "high" for b in blockers) else "needs_review" if blockers else "on_track"
    payload = {
        "ok": True,
        "schema_id": SCHEMA_ID,
        "status": status,
        "mode": "assistant_project_manager",
        "policy": "Read-only project manager mode. Suggestions require explicit user action before any workspace change.",
        "generated_at": _now_iso(),
        "project_id": resolved_project_id,
        "active_project_id": active.get("active_project_id"),
        "question": _text(question),
        "project": project,
        "counts": counts,
        "status_buckets": {
            "milestones": _status_bucket(milestones),
            "deliverables": _status_bucket(deliverables),
            "reviews": _status_bucket(reviews),
        },
        "blockers": blockers,
        "next_actions": next_actions,
        "recent_activity": (activity.get("recent_activity") or timeline)[:10],
        "open_reviews": (review_queue.get("review_items") or reviews)[:10],
        "delivery_summary": dashboard.get("summary") or {},
        "package_summary": package_builder.get("summary") or {},
        "source_citations": citations[:40],
        "answer": "",
    }
    payload["answer"] = _answer_text(project, counts, blockers, next_actions, citations)
    return payload


def assistant_project_manager_query(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    return assistant_project_manager_payload(
        _text(data.get("project_id") or ""),
        question=_text(data.get("question") or data.get("prompt") or ""),
        limit=int(data.get("limit") or 80),
    )
