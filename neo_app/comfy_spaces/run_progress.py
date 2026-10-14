from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from neo_app.providers.comfy_execution_transport import (
    ComfyExecutionTransportError,
    stream_comfy_prompt_events,
)
from neo_app.runtime.job_registry import GenerationJobRegistry, get_generation_job_registry


SPACE_RUN_PROGRESS_SCHEMA_VERSION = "neo.comfy_space.run_progress.v1"
SPACE_RUN_PROGRESS_SERVICE_VERSION = "neo.comfy_space.run_progress_service.v1"
_SURFACE = "comfy_spaces"
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled"}
_MAX_PROGRESS_EVENTS = 160


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _registry(root_dir: Path | str | None = None) -> GenerationJobRegistry:
    return get_generation_job_registry() if root_dir is None else GenerationJobRegistry(root_dir=root_dir)


def _node_links(value: Any, known_nodes: set[str]) -> set[str]:
    deps: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            deps.update(_node_links(item, known_nodes))
        return deps
    if isinstance(value, (list, tuple)):
        # Canonical Comfy links use [node_id, output_index]. Avoid treating
        # ordinary numeric/string arrays as links unless the first item is an
        # actual node id and the second is an integer output slot.
        if len(value) == 2 and str(value[0]) in known_nodes and isinstance(value[1], int):
            deps.add(str(value[0]))
            return deps
        for item in value:
            deps.update(_node_links(item, known_nodes))
    return deps


def _node_sort_key(node_id: str) -> tuple[int, int | str]:
    text = str(node_id)
    return (0, int(text)) if text.isdigit() else (1, text)


def build_space_graph_plan(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact execution-progress plan without retaining user values.

    The plan is intentionally descriptive rather than executable. It keeps only
    node ids, class/title labels, dependencies, and a stable topological order so
    NCS-8 can present graph progress after navigation/restart without persisting a
    second copy of the submitted prompt graph in progress state.
    """

    nodes = {str(key): value for key, value in workflow.items() if isinstance(value, Mapping)}
    known = set(nodes)
    deps_by_node: dict[str, set[str]] = {}
    downstream: dict[str, set[str]] = {node_id: set() for node_id in known}
    rows: dict[str, dict[str, Any]] = {}
    for node_id, raw in nodes.items():
        node = dict(raw)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
        deps = _node_links(inputs, known)
        deps.discard(node_id)
        deps_by_node[node_id] = deps
        for dep in deps:
            downstream.setdefault(dep, set()).add(node_id)
        meta = node.get("_meta") if isinstance(node.get("_meta"), Mapping) else {}
        class_type = str(node.get("class_type") or "").strip()
        title = str(meta.get("title") or meta.get("label") or "").strip()
        rows[node_id] = {
            "node_id": node_id,
            "class_type": class_type,
            "title": title,
            "dependencies": sorted(deps, key=_node_sort_key),
        }

    indegree = {node_id: len(deps) for node_id, deps in deps_by_node.items()}
    ready = sorted([node_id for node_id, count in indegree.items() if count == 0], key=_node_sort_key)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in sorted(downstream.get(current) or (), key=_node_sort_key):
            indegree[child] = max(0, indegree[child] - 1)
            if indegree[child] == 0 and child not in ready and child not in order:
                ready.append(child)
                ready.sort(key=_node_sort_key)
    if len(order) < len(nodes):
        # Cycles should already fail Comfy validation, but keeping a deterministic
        # fallback order makes the progress contract robust for unusual custom
        # graph constructs without pretending the remaining order is causal.
        order.extend(sorted((node_id for node_id in nodes if node_id not in order), key=_node_sort_key))

    ordered_rows: list[dict[str, Any]] = []
    for index, node_id in enumerate(order, start=1):
        row = dict(rows[node_id])
        row["order"] = index
        ordered_rows.append(row)
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "node_count": len(ordered_rows),
        "order": order,
        "nodes": ordered_rows,
    }


def _plan_index(plan: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    rows = plan.get("nodes") if isinstance(plan.get("nodes"), list) else []
    by_id: dict[str, dict[str, Any]] = {}
    position: dict[str, int] = {}
    for offset, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        node_id = str(row.get("node_id") or "")
        if not node_id:
            continue
        by_id[node_id] = row
        position[node_id] = int(row.get("order") or offset)
    return by_id, position


def _human_node_label(row: Mapping[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    class_type = str(row.get("class_type") or "").strip()
    if title:
        return title
    lower = class_type.lower()
    if not class_type:
        return "Running workflow node"
    if "sampler" in lower or lower == "ksampler":
        return "Sampling"
    if "vaedecode" in lower or ("decode" in lower and "vae" in lower):
        return "Decoding output"
    if "vaeencode" in lower or ("encode" in lower and "vae" in lower):
        return "Encoding source"
    if any(token in lower for token in ("videocombine", "createvideo", "savevideo", "savewebm", "videoencode")):
        return "Encoding video"
    if any(token in lower for token in ("upscale", "resize", "imagescale")):
        return "Processing image"
    if lower.startswith("load") or lower.endswith("loader") or "loader" in lower:
        return f"Loading {class_type}"
    if lower.startswith("save") or lower.startswith("preview"):
        return "Finalizing output"
    return f"Executing {class_type}"


def _initial_progress(plan: Mapping[str, Any] | None = None, *, message: str = "Preparing Space run.") -> dict[str, Any]:
    node_count = int(_mapping(plan).get("node_count") or 0)
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "sequence": 0,
        "phase": "preparing",
        "percent": 3.0,
        "percent_mode": "phase",
        "message": message,
        "event_type": "preparing",
        "source": "neo_runtime",
        "updated_at": _now(),
        "current_node_id": "",
        "current_display_node_id": "",
        "current_node_class": "",
        "current_node_title": "",
        "current_node_index": 0,
        "node_count": node_count,
        "node_step": 0,
        "node_step_max": 0,
        "completed_node_ids": [],
        "cached_node_ids": [],
        "queue_remaining": None,
        "websocket_connected": False,
        "monitor_state": "idle",
        "recoverable": True,
    }


def progress_snapshot_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    progress = record.get("progress") if isinstance(record.get("progress"), Mapping) else None
    if not progress:
        runtime = record.get("runtime") if isinstance(record.get("runtime"), Mapping) else {}
        progress = runtime.get("progress") if isinstance(runtime.get("progress"), Mapping) else None
    payload = _initial_progress(_mapping(_mapping(record.get("runtime")).get("graph_plan")))
    if progress:
        payload.update(deepcopy(dict(progress)))
    payload["schema_version"] = SPACE_RUN_PROGRESS_SCHEMA_VERSION
    return payload


def _estimate_percent(
    *,
    position: int,
    total: int,
    completed: int,
    step: int = 0,
    step_max: int = 0,
) -> tuple[float, str]:
    if total <= 0:
        return 40.0, "phase"
    before = max(0, min(total, max(position - 1, completed)))
    fraction = 0.0
    mode = "graph_estimate"
    if step_max > 0:
        fraction = max(0.0, min(1.0, float(step) / float(step_max)))
        mode = "node_step"
    raw = (before + fraction) / float(total)
    return round(max(15.0, min(94.0, 15.0 + (raw * 79.0))), 2), mode


def _append_progress_event(runtime: dict[str, Any], snapshot: Mapping[str, Any], *, event_type: str, data: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    existing = runtime.get("progress_events") if isinstance(runtime.get("progress_events"), list) else []
    event = {
        "sequence": int(snapshot.get("sequence") or 0),
        "event_type": str(event_type or "progress"),
        "at": str(snapshot.get("updated_at") or _now()),
        "phase": str(snapshot.get("phase") or "running"),
        "percent": float(snapshot.get("percent") or 0.0),
        "message": str(snapshot.get("message") or ""),
        "current_node_id": str(snapshot.get("current_node_id") or ""),
        "source": str(snapshot.get("source") or "neo_runtime"),
    }
    if data:
        # Persist only small, non-binary progress facts. Never persist arbitrary
        # node output payloads or websocket preview frames in the job registry.
        safe: dict[str, Any] = {}
        for key in ("value", "max", "node", "display_node", "queue_remaining", "nodes"):
            value = data.get(key)
            if key == "nodes" and isinstance(value, list):
                value = [str(item) for item in value[:256]]
            if isinstance(value, (str, int, float, bool, list)) or value is None:
                safe[key] = value
        if safe:
            event["data"] = safe
    return [*existing, event][-_MAX_PROGRESS_EVENTS:]


def record_space_run_progress(
    run_id: str,
    *,
    event_type: str,
    data: Mapping[str, Any] | None = None,
    source: str = "comfy_websocket",
    root_dir: Path | str | None = None,
    force_message: str = "",
    force_phase: str = "",
    force_percent: float | None = None,
    websocket_connected: bool | None = None,
    monitor_state: str = "",
    recoverable: bool | None = None,
) -> dict[str, Any]:
    registry = _registry(root_dir)
    record = registry.get(run_id, surface=_SURFACE)
    if not record:
        return {"ok": False, "run_id": run_id, "reason": "run_not_found"}
    runtime = _mapping(record.get("runtime"))
    plan = _mapping(runtime.get("graph_plan"))
    by_id, positions = _plan_index(plan)
    previous = progress_snapshot_from_record(record)
    payload = dict(data or {})
    event = str(event_type or "progress").strip().lower()
    next_progress = dict(previous)
    next_progress["sequence"] = int(previous.get("sequence") or 0) + 1
    next_progress["event_type"] = event
    next_progress["source"] = str(source or "neo_runtime")
    next_progress["updated_at"] = _now()
    if websocket_connected is not None:
        next_progress["websocket_connected"] = bool(websocket_connected)
    if monitor_state:
        next_progress["monitor_state"] = monitor_state
    if recoverable is not None:
        next_progress["recoverable"] = bool(recoverable)

    completed = {str(item) for item in previous.get("completed_node_ids") or []}
    cached = {str(item) for item in previous.get("cached_node_ids") or []}
    current_node = str(payload.get("node") or payload.get("node_id") or "")
    display_node = str(payload.get("display_node") or payload.get("display_node_id") or "")

    if event == "status":
        status = payload.get("status") if isinstance(payload.get("status"), Mapping) else {}
        exec_info = status.get("exec_info") if isinstance(status.get("exec_info"), Mapping) else {}
        queue_remaining = payload.get("queue_remaining", exec_info.get("queue_remaining"))
        if queue_remaining is not None:
            try:
                next_progress["queue_remaining"] = max(0, int(queue_remaining))
            except (TypeError, ValueError):
                pass
        next_progress["message"] = force_message or str(previous.get("message") or "Waiting for ComfyUI.")
    elif event == "execution_start":
        next_progress.update({
            "phase": "running",
            "percent": max(15.0, float(previous.get("percent") or 0.0)),
            "percent_mode": "phase",
            "message": force_message or "Workflow execution started.",
            "monitor_state": monitor_state or "connected",
            "websocket_connected": True if websocket_connected is None else bool(websocket_connected),
        })
    elif event == "execution_cached":
        cached.update(str(item) for item in payload.get("nodes") or [])
        completed.update(cached)
        total = int(plan.get("node_count") or 0)
        percent = 15.0 + (79.0 * min(len(completed), total) / total) if total else max(20.0, float(previous.get("percent") or 0.0))
        next_progress.update({
            "phase": "running",
            "percent": round(min(94.0, percent), 2),
            "percent_mode": "graph_estimate",
            "message": force_message or (f"Reusing {len(cached)} cached workflow node{'s' if len(cached) != 1 else ''}."),
        })
    elif event == "executing":
        if payload.get("node") is None and "node" in payload:
            next_progress.update({
                "phase": "finalizing",
                "percent": max(95.0, float(previous.get("percent") or 0.0)),
                "percent_mode": "phase",
                "message": force_message or "ComfyUI finished graph execution; finalizing results.",
                "current_node_id": "",
                "current_display_node_id": "",
                "current_node_class": "",
                "current_node_title": "",
                "current_node_index": 0,
                "node_step": 0,
                "node_step_max": 0,
            })
        else:
            row = by_id.get(current_node, {})
            position = int(positions.get(current_node) or 0)
            percent, mode = _estimate_percent(position=position, total=int(plan.get("node_count") or 0), completed=len(completed))
            next_progress.update({
                "phase": "running",
                "percent": max(percent, min(94.0, float(previous.get("percent") or 0.0))),
                "percent_mode": mode,
                "message": force_message or _human_node_label(row or {"class_type": "", "title": ""}),
                "current_node_id": current_node,
                "current_display_node_id": display_node,
                "current_node_class": str(row.get("class_type") or ""),
                "current_node_title": str(row.get("title") or ""),
                "current_node_index": position,
                "node_step": 0,
                "node_step_max": 0,
            })
    elif event == "progress":
        if current_node:
            row = by_id.get(current_node, {})
            position = int(positions.get(current_node) or 0)
        else:
            current_node = str(previous.get("current_node_id") or "")
            row = by_id.get(current_node, {})
            position = int(positions.get(current_node) or previous.get("current_node_index") or 0)
        try:
            step = max(0, int(payload.get("value") or 0))
            step_max = max(0, int(payload.get("max") or 0))
        except (TypeError, ValueError):
            step, step_max = 0, 0
        percent, mode = _estimate_percent(position=position, total=int(plan.get("node_count") or 0), completed=len(completed), step=step, step_max=step_max)
        label = _human_node_label(row)
        if step_max > 0:
            label = f"{label} · {step}/{step_max}"
        next_progress.update({
            "phase": "running",
            "percent": max(percent, min(94.0, float(previous.get("percent") or 0.0))),
            "percent_mode": mode,
            "message": force_message or label,
            "current_node_id": current_node,
            "current_display_node_id": display_node or str(previous.get("current_display_node_id") or ""),
            "current_node_class": str(row.get("class_type") or previous.get("current_node_class") or ""),
            "current_node_title": str(row.get("title") or previous.get("current_node_title") or ""),
            "current_node_index": position,
            "node_step": step,
            "node_step_max": step_max,
        })
    elif event == "progress_state":
        states = payload.get("nodes") if isinstance(payload.get("nodes"), Mapping) else {}
        active: tuple[str, dict[str, Any]] | None = None
        for node_id, raw_state in states.items():
            if not isinstance(raw_state, Mapping):
                continue
            state = str(raw_state.get("state") or "").lower()
            if state in {"finished", "completed", "cached"}:
                completed.add(str(node_id))
                if state == "cached":
                    cached.add(str(node_id))
            if state in {"running", "executing", "in_progress"}:
                active = (str(node_id), dict(raw_state))
        if active:
            node_id, state = active
            row = by_id.get(node_id, {})
            position = int(positions.get(node_id) or 0)
            try:
                step = max(0, int(state.get("value") or 0))
                step_max = max(0, int(state.get("max") or 0))
            except (TypeError, ValueError):
                step, step_max = 0, 0
            percent, mode = _estimate_percent(
                position=position,
                total=int(plan.get("node_count") or 0),
                completed=len(completed),
                step=step,
                step_max=step_max,
            )
            label = _human_node_label(row)
            if step_max > 0:
                label = f"{label} · {step}/{step_max}"
            next_progress.update({
                "phase": "running",
                "percent": max(percent, min(94.0, float(previous.get("percent") or 0.0))),
                "percent_mode": mode,
                "message": force_message or label,
                "current_node_id": node_id,
                "current_display_node_id": str(state.get("display_node_id") or state.get("display_node") or ""),
                "current_node_class": str(row.get("class_type") or state.get("class_type") or ""),
                "current_node_title": str(row.get("title") or ""),
                "current_node_index": position,
                "node_step": step,
                "node_step_max": step_max,
            })
        else:
            total = int(plan.get("node_count") or 0)
            percent = 15.0 + (79.0 * min(len(completed), total) / total) if total else float(previous.get("percent") or 20.0)
            next_progress.update({
                "phase": "running",
                "percent": round(min(94.0, max(float(previous.get("percent") or 0.0), percent)), 2),
                "percent_mode": "graph_estimate",
                "message": force_message or str(previous.get("message") or "Running workflow."),
            })
    elif event == "executed":
        if current_node:
            completed.add(current_node)
        total = int(plan.get("node_count") or 0)
        position = int(positions.get(current_node) or 0)
        percent, mode = _estimate_percent(position=max(1, position + 1), total=total, completed=len(completed))
        next_progress.update({
            "phase": "running",
            "percent": max(percent, min(94.0, float(previous.get("percent") or 0.0))),
            "percent_mode": mode,
            "message": force_message or str(previous.get("message") or "Running workflow."),
        })
    elif event == "execution_success":
        next_progress.update({
            "phase": "finalizing",
            "percent": 97.0,
            "percent_mode": "phase",
            "message": force_message or "ComfyUI finished; collecting declared Space outputs.",
            "current_node_id": "",
            "current_display_node_id": "",
            "current_node_class": "",
            "current_node_title": "",
            "current_node_index": 0,
            "node_step": 0,
            "node_step_max": 0,
        })
    elif event == "execution_error":
        message = str(payload.get("exception_message") or payload.get("exception_type") or force_message or "ComfyUI reported an execution error.")
        next_progress.update({
            "phase": "failed",
            "percent": max(float(previous.get("percent") or 0.0), 1.0),
            "percent_mode": "terminal",
            "message": message,
            "recoverable": False,
        })
    elif event in {"execution_interrupted", "execution_cancelled", "execution_canceled"}:
        next_progress.update({
            "phase": "cancelled",
            "percent": float(previous.get("percent") or 0.0),
            "percent_mode": "terminal",
            "message": force_message or "ComfyUI confirmed the run was interrupted.",
            "recoverable": False,
        })
    elif event == "cancel_requested":
        next_progress.update({
            "phase": "cancel_requested",
            "message": force_message or "Cancellation requested; waiting for ComfyUI to stop safely.",
            "recoverable": True,
        })
    elif event == "completed":
        next_progress.update({
            "phase": "completed",
            "percent": 100.0,
            "percent_mode": "terminal",
            "message": force_message or "Space run completed.",
            "current_node_id": "",
            "current_display_node_id": "",
            "current_node_class": "",
            "current_node_title": "",
            "current_node_index": 0,
            "node_step": 0,
            "node_step_max": 0,
            "recoverable": False,
        })
    elif event == "failed":
        next_progress.update({
            "phase": "failed",
            "percent_mode": "terminal",
            "message": force_message or str(record.get("message") or "Space run failed."),
            "recoverable": False,
        })
    elif event == "cancelled":
        next_progress.update({
            "phase": "cancelled",
            "percent_mode": "terminal",
            "message": force_message or "Space run cancelled.",
            "recoverable": False,
        })
    elif event in {"monitor_connecting", "monitor_reconnecting", "monitor_connected", "monitor_stopped"}:
        state = monitor_state or event.replace("monitor_", "")
        next_progress["monitor_state"] = state
        if event == "monitor_connected":
            next_progress["websocket_connected"] = True
        elif event in {"monitor_reconnecting", "monitor_stopped"}:
            next_progress["websocket_connected"] = False
        if force_message:
            next_progress["message"] = force_message
    elif event == "queue_state":
        state = str(payload.get("state") or "").lower()
        if state == "pending":
            next_progress.update({"phase": "queued", "percent": max(8.0, float(previous.get("percent") or 0.0)), "percent_mode": "phase", "message": force_message or "Waiting in ComfyUI queue."})
        elif state == "running":
            next_progress.update({"phase": "running", "percent": max(15.0, float(previous.get("percent") or 0.0)), "percent_mode": "phase", "message": force_message or str(previous.get("message") or "Running in ComfyUI.")})
        elif state == "awaiting_history":
            next_progress.update({"phase": "finalizing", "percent": max(95.0, float(previous.get("percent") or 0.0)), "percent_mode": "phase", "message": force_message or "Waiting for ComfyUI completion history."})

    if force_phase:
        next_progress["phase"] = force_phase
    if force_percent is not None:
        next_progress["percent"] = max(0.0, min(100.0, float(force_percent)))
    next_progress["completed_node_ids"] = sorted(completed, key=_node_sort_key)
    next_progress["cached_node_ids"] = sorted(cached, key=_node_sort_key)
    next_progress["node_count"] = int(plan.get("node_count") or next_progress.get("node_count") or 0)

    # Do not let ordinary live events move a displayed percentage backwards.
    if event not in {"cancelled", "failed"}:
        next_progress["percent"] = max(float(previous.get("percent") or 0.0), float(next_progress.get("percent") or 0.0))
    next_progress["percent"] = round(max(0.0, min(100.0, float(next_progress.get("percent") or 0.0))), 2)

    runtime["progress"] = deepcopy(next_progress)
    runtime["progress_events"] = _append_progress_event(runtime, next_progress, event_type=event, data=payload)
    registry.upsert(
        run_id,
        surface=_SURFACE,
        updates={"runtime": runtime, "progress": deepcopy(next_progress)},
    )
    return {"ok": True, "run_id": run_id, "progress": next_progress}


def get_space_run_progress(
    run_id: str,
    *,
    after_sequence: int = 0,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    record = _registry(root_dir).get(run_id, surface=_SURFACE)
    if not record:
        return {"ok": False, "run_id": str(run_id or ""), "status": "missing", "progress": {}, "events": []}
    runtime = _mapping(record.get("runtime"))
    snapshot = progress_snapshot_from_record(record)
    events = runtime.get("progress_events") if isinstance(runtime.get("progress_events"), list) else []
    try:
        cursor = max(0, int(after_sequence or 0))
    except (TypeError, ValueError):
        cursor = 0
    filtered = [dict(item) for item in events if isinstance(item, Mapping) and int(item.get("sequence") or 0) > cursor]
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "ok": True,
        "run_id": str(record.get("job_id") or run_id),
        "status": str(record.get("status") or ""),
        "space_id": str(runtime.get("space_id") or ""),
        "version": str(runtime.get("space_version") or ""),
        "runtime_profile_id": str(record.get("profile_id") or ""),
        "prompt_id": str(record.get("provider_job_id") or runtime.get("prompt_id") or ""),
        "progress": snapshot,
        "events": filtered,
        "next_sequence": int(snapshot.get("sequence") or 0),
        "terminal": str(record.get("status") or "").lower() in _TERMINAL_STATUSES,
    }


def list_space_runs(*, limit: int = 50, active_only: bool = False, root_dir: Path | str | None = None) -> dict[str, Any]:
    registry = _registry(root_dir)
    index = registry.list_recent(surface=_SURFACE, limit=max(1, min(int(limit or 50), 200)))
    items: list[dict[str, Any]] = []
    for summary in index.get("items") or []:
        if not isinstance(summary, Mapping):
            continue
        run_id = str(summary.get("job_id") or "")
        record = registry.get(run_id, surface=_SURFACE) or {}
        status = str(record.get("status") or summary.get("status") or "")
        if active_only and status.lower() in _TERMINAL_STATUSES:
            continue
        runtime = _mapping(record.get("runtime"))
        items.append({
            "run_id": run_id,
            "status": status,
            "message": str(record.get("message") or ""),
            "space_id": str(runtime.get("space_id") or ""),
            "version": str(runtime.get("space_version") or ""),
            "runtime_profile_id": str(record.get("profile_id") or ""),
            "prompt_id": str(record.get("provider_job_id") or runtime.get("prompt_id") or ""),
            "updated_at": str(record.get("updated_at") or ""),
            "progress": progress_snapshot_from_record(record),
            "stage_plan": _mapping(runtime.get("stage_plan")),
            "continue_from_run_id": str(runtime.get("continue_from_run_id") or ""),
        })
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "ok": True,
        "count": len(items),
        "active_only": bool(active_only),
        "items": items,
    }


class SpaceRunProgressMonitorManager:
    """Process-local websocket observers backed by the persistent job registry.

    Monitor threads are deliberately replaceable. Persistent truth lives in the
    registry and Comfy history/queue; NCS-8 can therefore reconnect observers
    after page navigation or Neo restart without making websocket liveness the
    execution authority.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._monitors: dict[str, tuple[threading.Thread, threading.Event]] = {}

    @staticmethod
    def _key(run_id: str, root_dir: Path | str | None) -> str:
        root = "default" if root_dir is None else str(Path(root_dir).resolve())
        return f"{root}::{run_id}"

    def ensure(self, run_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
        key = self._key(run_id, root_dir)
        with self._lock:
            current = self._monitors.get(key)
            if current and current[0].is_alive():
                return {"ok": True, "run_id": run_id, "state": "already_monitoring"}
            stop = threading.Event()
            thread = threading.Thread(
                target=self._thread_main,
                name=f"neo-comfy-space-progress-{str(run_id)[-12:]}",
                daemon=True,
                args=(key, run_id, root_dir, stop),
            )
            self._monitors[key] = (thread, stop)
            thread.start()
        return {"ok": True, "run_id": run_id, "state": "monitor_started"}

    def _thread_main(self, key: str, run_id: str, root_dir: Path | str | None, stop: threading.Event) -> None:
        try:
            asyncio.run(self._monitor_loop(run_id, root_dir=root_dir, stop=stop))
        except Exception as exc:  # noqa: BLE001 - observer failure must not fail the provider job.
            record_space_run_progress(
                run_id,
                event_type="monitor_stopped",
                root_dir=root_dir,
                source="neo_monitor",
                force_message=f"Live progress observer stopped; registry polling can still recover the run. {exc}",
                monitor_state="stopped",
                websocket_connected=False,
            )
        finally:
            with self._lock:
                current = self._monitors.get(key)
                if current and current[1] is stop:
                    self._monitors.pop(key, None)

    async def _monitor_loop(self, run_id: str, *, root_dir: Path | str | None, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            registry = _registry(root_dir)
            record = registry.get(run_id, surface=_SURFACE)
            if not record:
                return
            if str(record.get("status") or "").lower() in _TERMINAL_STATUSES:
                record_space_run_progress(run_id, event_type="monitor_stopped", root_dir=root_dir, source="neo_monitor", monitor_state="stopped", websocket_connected=False)
                return
            profile_id = str(record.get("profile_id") or "")
            client_id = str(record.get("client_id") or "")
            prompt_id = str(record.get("provider_job_id") or "")
            if not profile_id or not client_id or not prompt_id:
                record_space_run_progress(
                    run_id,
                    event_type="monitor_stopped",
                    root_dir=root_dir,
                    source="neo_monitor",
                    force_message="Run progress cannot reconnect because runtime lineage is incomplete.",
                    monitor_state="stopped",
                    websocket_connected=False,
                    recoverable=False,
                )
                return

            record_space_run_progress(
                run_id,
                event_type="monitor_connecting" if backoff <= 1.0 else "monitor_reconnecting",
                root_dir=root_dir,
                source="neo_monitor",
                monitor_state="connecting" if backoff <= 1.0 else "reconnecting",
                websocket_connected=False,
            )
            saw_terminal_event = False
            try:
                async for event in stream_comfy_prompt_events(
                    profile_id,
                    client_id=client_id,
                    prompt_id=prompt_id,
                    receive_timeout_seconds=25.0,
                ):
                    if stop.is_set():
                        return
                    event_type = str(event.get("type") or "").lower()
                    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
                    record_space_run_progress(
                        run_id,
                        event_type=event_type,
                        data=data,
                        root_dir=root_dir,
                        source="comfy_websocket",
                        monitor_state="connected",
                        websocket_connected=True,
                    )
                    backoff = 1.0
                    if event_type in {"execution_success", "execution_error", "execution_interrupted", "execution_cancelled", "execution_canceled"}:
                        saw_terminal_event = True
                        break
            except ComfyExecutionTransportError as exc:
                record_space_run_progress(
                    run_id,
                    event_type="monitor_reconnecting",
                    root_dir=root_dir,
                    source="neo_monitor",
                    force_message=f"Live graph progress disconnected; recovering from Comfy history/queue. {exc}",
                    monitor_state="reconnecting",
                    websocket_connected=False,
                    recoverable=True,
                )
            except Exception as exc:  # noqa: BLE001
                record_space_run_progress(
                    run_id,
                    event_type="monitor_reconnecting",
                    root_dir=root_dir,
                    source="neo_monitor",
                    force_message=f"Live graph progress interrupted; recovering from Comfy history/queue. {exc}",
                    monitor_state="reconnecting",
                    websocket_connected=False,
                    recoverable=True,
                )

            # WebSocket is progress telemetry, not completion authority. Reconcile
            # against NCS-7 history/queue after terminal events or disconnects.
            try:
                from neo_app.comfy_spaces.execution_runtime import poll_space_run

                result = await asyncio.to_thread(poll_space_run, run_id, root_dir=root_dir)
                if str(result.get("status") or "").lower() in _TERMINAL_STATUSES:
                    return
            except Exception as exc:  # noqa: BLE001 - keep job recoverable while backend is unavailable.
                record_space_run_progress(
                    run_id,
                    event_type="monitor_reconnecting",
                    root_dir=root_dir,
                    source="neo_monitor",
                    force_message=f"Run remains active; waiting to reconnect to the selected ComfyUI runtime. {exc}",
                    monitor_state="reconnecting",
                    websocket_connected=False,
                    recoverable=True,
                )

            if saw_terminal_event:
                # History can lag a websocket terminal event briefly; retry soon.
                backoff = min(2.0, backoff)
            await asyncio.sleep(backoff)
            backoff = min(15.0, backoff * 2.0)

    def stop_all(self) -> dict[str, Any]:
        with self._lock:
            monitors = list(self._monitors.items())
            self._monitors.clear()
        for _key, (_thread, stop) in monitors:
            stop.set()
        return {"ok": True, "stopped": len(monitors)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            alive = [key for key, (thread, _stop) in self._monitors.items() if thread.is_alive()]
        return {"ok": True, "active_monitor_count": len(alive), "active_monitor_keys": alive}


_MONITORS = SpaceRunProgressMonitorManager()


def ensure_space_run_monitor(run_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    return _MONITORS.ensure(run_id, root_dir=root_dir)


def recover_space_run_monitors(*, root_dir: Path | str | None = None, limit: int = 200) -> dict[str, Any]:
    registry = _registry(root_dir)
    index = registry.list_recent(surface=_SURFACE, limit=max(1, min(int(limit or 200), 500)))
    recovered: list[str] = []
    terminal: list[str] = []
    failed: list[dict[str, str]] = []
    for item in index.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        run_id = str(item.get("job_id") or "")
        if not run_id:
            continue
        record = registry.get(run_id, surface=_SURFACE) or {}
        if str(record.get("status") or "").lower() in _TERMINAL_STATUSES:
            terminal.append(run_id)
            continue
        try:
            from neo_app.comfy_spaces.execution_runtime import poll_space_run

            result = poll_space_run(run_id, root_dir=root_dir)
            if str(result.get("status") or "").lower() in _TERMINAL_STATUSES:
                terminal.append(run_id)
                continue
        except Exception as exc:  # noqa: BLE001
            # Runtime unreachability is precisely why persistent recovery exists;
            # keep the record and start a reconnecting monitor when lineage exists.
            failed.append({"run_id": run_id, "error": str(exc)})
        record = registry.get(run_id, surface=_SURFACE) or record
        if record.get("profile_id") and record.get("client_id") and record.get("provider_job_id"):
            ensure_space_run_monitor(run_id, root_dir=root_dir)
            recovered.append(run_id)
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "ok": True,
        "recovered_count": len(recovered),
        "terminal_count": len(terminal),
        "recovered_run_ids": recovered,
        "warnings": failed,
    }


def shutdown_space_run_monitors() -> dict[str, Any]:
    return _MONITORS.stop_all()


def progress_runtime_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_RUN_PROGRESS_SCHEMA_VERSION,
        "service_version": SPACE_RUN_PROGRESS_SERVICE_VERSION,
        "status": "implemented",
        "persistent_registry": True,
        "cross_navigation_resume": True,
        "startup_recovery": True,
        "comfy_websocket_events": True,
        "history_queue_fallback": True,
        "current_node": True,
        "node_step_progress": True,
        "progress_state_event": True,
        "bounded_event_journal": True,
        "run_list": True,
        "run_progress_snapshot": True,
        "cancel_feedback": True,
        "binary_preview_persistence": False,
        "public_runner_mounted": True,
        "monitor": _MONITORS.status(),
    }
