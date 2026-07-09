from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo_app.project_workspace import (
    active_project_payload,
    create_project_surface_action,
    project_workspace_asset_tray_payload,
    add_project_timeline_event,
)
from neo_app.video.output_records import (
    build_assistant_summary,
    load_video_output_record,
    sanitize_path_part,
)
from neo_app.video.replay_memory import video_replay_metadata_payload

VIDEO_PROJECT_HANDOFF_SCHEMA_VERSION = "neo.video.project_handoff.v23"
VIDEO_PROJECT_ASSET_TRAY_SCHEMA_VERSION = "neo.video.project_asset_tray.v23"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _relative_file_path(record: dict[str, Any]) -> str:
    outputs = _as_dict(record.get("outputs"))
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    active_id = str(outputs.get("active_file_id") or "")
    active = next((item for item in files if isinstance(item, dict) and item.get("file_id") == active_id), None)
    if not active and files:
        active = next((item for item in files if isinstance(item, dict)), None)
    if isinstance(active, dict):
        return str(active.get("path") or active.get("url") or "")
    return ""


def _handoff_content(record: dict[str, Any], replay: dict[str, Any]) -> str:
    params = _as_dict(record.get("parameters"))
    prompt = str(record.get("prompt") or "").strip()
    negative = str(record.get("negative_prompt") or "").strip()
    lines = [
        build_assistant_summary(record),
        "",
        f"Result ID: {record.get('result_id', '')}",
        f"Route: {record.get('route_id', '')}",
        f"Category: {record.get('category', '')}",
        f"Status: {record.get('status', '')}",
    ]
    if params:
        lines.append(f"Parameters: {json.dumps(params, ensure_ascii=False, sort_keys=True)}")
    if prompt:
        lines.extend(["", "Prompt:", prompt])
    if negative:
        lines.extend(["", "Negative Prompt:", negative])
    replay_path = replay.get("record", {}).get("replay_metadata_path") if isinstance(replay.get("record"), dict) else ""
    if replay_path:
        lines.extend(["", f"Replay Metadata: {replay_path}"])
    return "\n".join(lines).strip()


def build_video_project_handoff_payload(result_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the project action payload used to hand a Video result to Project Workspace.

    V23 stores references/context only. It does not copy binary video assets and it
    never publishes outside local Neo project storage.
    """
    data = payload if isinstance(payload, dict) else {}
    loaded = load_video_output_record(result_id)
    if not loaded.get("ok"):
        return loaded
    replay = video_replay_metadata_payload(result_id)
    record = _as_dict(replay.get("record")) or _as_dict(loaded.get("record"))
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    result_id_clean = sanitize_path_part(str(record.get("result_id") or result_id), "video")
    video_path = str(data.get("path") or _relative_file_path(record) or record.get("record_path") or "")
    title = str(data.get("title") or f"Video result · {result_id_clean}")
    content = str(data.get("content") or data.get("summary") or _handoff_content(record, replay))
    tags = data.get("tags") if isinstance(data.get("tags"), list) else ["video", str(record.get("category") or "result"), str(record.get("family") or "")]
    action_payload = {
        "project_id": project_id,
        "action_type": "send_to_project",
        "source_surface": "video",
        "target_surface": "project_workspace",
        "resource_type": "video_result",
        "title": title,
        "content": content,
        "path": video_path,
        "ref_id": result_id_clean,
        "tags": [tag for tag in tags if tag],
        "metadata": {
            "schema_version": VIDEO_PROJECT_HANDOFF_SCHEMA_VERSION,
            "phase": "V23",
            "result_id": result_id_clean,
            "route_id": record.get("route_id") or "",
            "category": record.get("category") or "",
            "status": record.get("status") or "",
            "record_path": record.get("record_path") or "",
            "replay_metadata_path": record.get("replay_metadata_path") or "",
            "active_file_path": video_path,
            "lineage": record.get("lineage") if isinstance(record.get("lineage"), dict) else {},
        },
    }
    return {"ok": True, "schema_version": VIDEO_PROJECT_HANDOFF_SCHEMA_VERSION, "project_id": project_id, "result_id": result_id_clean, "record": record, "action_payload": action_payload, "replay_metadata": replay.get("replay_metadata")}


def send_video_result_to_project_payload(result_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    built = build_video_project_handoff_payload(result_id, payload)
    if not built.get("ok"):
        return built
    action_result = create_project_surface_action(built["action_payload"])
    event = add_project_timeline_event({
        "project_id": built.get("project_id") or "general",
        "event_type": "video.project_handoff.attached",
        "surface": "video",
        "title": f"Video result attached · {built.get('result_id', '')}",
        "summary": f"Video result {built.get('result_id', '')} was attached to the project asset tray.",
        "resource_type": "video_result",
        "ref_id": built.get("result_id") or "",
        "metadata": {
            "schema_version": VIDEO_PROJECT_HANDOFF_SCHEMA_VERSION,
            "phase": "V23",
            "project_action_id": (action_result.get("action") or {}).get("action_id", ""),
            "handoff_id": (action_result.get("result") or {}).get("handoff", {}).get("handoff_id", "") if isinstance(action_result.get("result"), dict) else "",
        },
    }).get("event")
    tray = video_project_asset_tray_payload(str(built.get("project_id") or ""), limit=50)
    return {
        "ok": True,
        "schema_version": VIDEO_PROJECT_HANDOFF_SCHEMA_VERSION,
        "phase": "V23",
        "result_id": built.get("result_id"),
        "project_id": built.get("project_id"),
        "action_payload": built.get("action_payload"),
        "project_action": action_result,
        "timeline_event": event,
        "asset_tray": tray,
        "policy": "V23 stores Video result references/context in Project Workspace. Binary assets remain in Neo-owned video outputs.",
    }


def video_project_asset_tray_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    tray = project_workspace_asset_tray_payload(project_id, limit=limit)
    links = tray.get("links") if isinstance(tray.get("links"), list) else []
    handoffs = tray.get("handoffs") if isinstance(tray.get("handoffs"), list) else []
    timeline = tray.get("timeline") if isinstance(tray.get("timeline"), list) else []
    video_links = [item for item in links if isinstance(item, dict) and (item.get("surface") == "video" or item.get("resource_type") == "video_result")]
    video_handoffs = [item for item in handoffs if isinstance(item, dict) and (item.get("source_surface") == "video" or item.get("resource_type") == "video_result")]
    video_timeline = [item for item in timeline if isinstance(item, dict) and (item.get("surface") == "video" or str(item.get("event_type") or "").startswith("video."))]
    return {
        "ok": True,
        "schema_version": VIDEO_PROJECT_ASSET_TRAY_SCHEMA_VERSION,
        "phase": "V23",
        "project_id": tray.get("project_id"),
        "active_project_id": tray.get("active_project_id"),
        "project": tray.get("project") if isinstance(tray.get("project"), dict) else {},
        "video_links": video_links[: max(1, int(limit or 30))],
        "video_handoffs": video_handoffs[: max(1, int(limit or 30))],
        "video_timeline": video_timeline[: max(1, int(limit or 30))],
        "summary": {
            "video_link_count": len(video_links),
            "video_handoff_count": len(video_handoffs),
            "video_timeline_count": len(video_timeline),
            "total_link_count": (tray.get("summary") or {}).get("link_count", 0) if isinstance(tray.get("summary"), dict) else 0,
        },
        "project_asset_tray": tray,
        "policy": "Video Project Asset Tray is a filtered view over Project Workspace links, handoffs, and timeline events from the Video surface.",
    }
