from __future__ import annotations

from typing import Any

from neo_app.project_workspace import active_project_payload, create_project_surface_action, project_workspace_asset_tray_payload, add_project_timeline_event
from .job_service import voice_job_payload
from .replay_memory import voice_replay_payload
from .output_paths import sanitize_path_part

VOICE_PROJECT_HANDOFF_SCHEMA = "neo.voice.project_handoff.v16"
VOICE_PROJECT_ASSET_TRAY_SCHEMA = "neo.voice.project_asset_tray.v16"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_output_path(job: dict[str, Any], replay: dict[str, Any] | None = None) -> str:
    if job.get("output_file"):
        return str(job.get("output_file") or "")
    if job.get("final_output"):
        return str(job.get("final_output") or "")
    output_obj = _as_dict((replay or {}).get("output_file_object"))
    if output_obj.get("path"):
        return str(output_obj.get("path") or "")
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    for item in outputs.get("files") if isinstance(outputs.get("files"), list) else []:
        if isinstance(item, dict) and item.get("path"):
            return str(item.get("path") or "")
    return ""


def _handoff_content(job: dict[str, Any], replay: dict[str, Any], *, output_path: str, replay_path: str) -> str:
    script = _as_dict(replay.get("script_fragment")) or _as_dict(job.get("script_snapshot"))
    backend = _as_dict(replay.get("backend_settings"))
    voice_source = _as_dict(replay.get("voice_source")) or _as_dict(job.get("voice_source"))
    lines = [
        f"Voice job {job.get('job_id', '')} is ready for project use.",
        "",
        f"Job type: {job.get('job_type', '')}",
        f"Status: {job.get('status', '')}",
        f"Backend: {backend.get('runtime') or job.get('runtime') or ''} / {backend.get('family') or job.get('family') or ''}",
        f"Voice source: {voice_source.get('type') or 'built_in'}",
    ]
    if output_path:
        lines.append(f"Output file: {output_path}")
    if replay_path:
        lines.append(f"Replay metadata: {replay_path}")
    if script:
        lines.extend(["", f"Script title: {script.get('title') or ''}", f"Language: {script.get('language') or 'en'}"])
        excerpt = str(script.get("text_excerpt") or script.get("text") or "").strip()
        if excerpt:
            lines.extend(["", "Script excerpt:", excerpt[:700]])
    speaker_manifest = _as_dict(replay.get("speaker_manifest")) or _as_dict(job.get("speaker_manifest"))
    if speaker_manifest:
        lines.extend(["", f"Speakers: {speaker_manifest.get('speaker_count') or len(speaker_manifest.get('speakers') or [])}"])
    return "\n".join(lines).strip()


def _load_voice_job(job_id: str) -> dict[str, Any]:
    payload = voice_job_payload(job_id)
    if not payload.get("ok"):
        return {"ok": False, "status": payload.get("status") or "missing_job", "job_id": job_id}
    return {"ok": True, "job": _as_dict(payload.get("job"))}


def build_voice_project_handoff_payload(job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    loaded = _load_voice_job(job_id)
    if not loaded.get("ok"):
        return loaded
    job = loaded["job"]
    replay_payload = voice_replay_payload(job, write_if_missing=True)
    replay = replay_payload.get("replay") if isinstance(replay_payload.get("replay"), dict) else {}
    project_id = str(data.get("project_id") or active_project_payload().get("active_project_id") or "general")
    job_id_clean = sanitize_path_part(str(job.get("job_id") or job_id), "voice_job")
    replay_path = str((_as_dict(job.get("replay_metadata"))).get("path") or replay.get("path") or "")
    output_path = str(data.get("path") or _first_output_path(job, replay) or "")
    title = str(data.get("title") or f"Voice output · {job_id_clean}")
    content = str(data.get("content") or data.get("summary") or _handoff_content(job, replay, output_path=output_path, replay_path=replay_path))
    tags = data.get("tags") if isinstance(data.get("tags"), list) else ["voice", str(job.get("job_type") or "output"), str(job.get("family") or "")]
    action_payload = {
        "project_id": project_id,
        "action_type": "send_to_project",
        "source_surface": "voice",
        "target_surface": "project_workspace",
        "resource_type": "voice_output",
        "title": title,
        "content": content,
        "path": output_path,
        "ref_id": job_id_clean,
        "tags": [tag for tag in tags if tag],
        "metadata": {
            "schema_id": VOICE_PROJECT_HANDOFF_SCHEMA,
            "phase": "VO-V16",
            "job_id": job_id_clean,
            "job_type": job.get("job_type") or "",
            "status": job.get("status") or "",
            "runtime": job.get("runtime") or "",
            "family": job.get("family") or "",
            "voice_source": job.get("voice_source") if isinstance(job.get("voice_source"), dict) else {},
            "output_path": output_path,
            "replay_metadata_path": replay_path,
            "memory_event_id": (_as_dict(job.get("memory_export"))).get("event_id", ""),
            "speaker_manifest": replay.get("speaker_manifest") if isinstance(replay.get("speaker_manifest"), dict) else {},
            "policy": "reference_only_no_binary_copy",
        },
    }
    return {"ok": True, "schema_id": VOICE_PROJECT_HANDOFF_SCHEMA, "phase": "VO-V16", "project_id": project_id, "job_id": job_id_clean, "job": job, "replay_metadata": replay, "action_payload": action_payload, "policy": "Reference-only Project Workspace handoff for Voice outputs."}


def send_voice_job_to_project_payload(job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    built = build_voice_project_handoff_payload(job_id, payload)
    if not built.get("ok"):
        return built
    action_result = create_project_surface_action(built["action_payload"])
    event = add_project_timeline_event({
        "project_id": built.get("project_id") or "general",
        "event_type": "voice.project_handoff.attached",
        "surface": "voice",
        "title": f"Voice output attached · {built.get('job_id', '')}",
        "summary": f"Voice job {built.get('job_id', '')} was attached to the project asset tray.",
        "resource_type": "voice_output",
        "ref_id": built.get("job_id") or "",
        "metadata": {"schema_id": VOICE_PROJECT_HANDOFF_SCHEMA, "phase": "VO-V16", "project_action_id": (action_result.get("action") or {}).get("action_id", ""), "replay_metadata_path": (built.get("action_payload") or {}).get("metadata", {}).get("replay_metadata_path", "")},
    }).get("event")
    tray = voice_project_asset_tray_payload(str(built.get("project_id") or ""), limit=50)
    return {"ok": True, "schema_id": VOICE_PROJECT_HANDOFF_SCHEMA, "phase": "VO-V16", "job_id": built.get("job_id"), "project_id": built.get("project_id"), "action_payload": built.get("action_payload"), "project_action": action_result, "timeline_event": event, "asset_tray": tray, "policy": "Reference-only Voice Project handoff complete. Audio binaries remain in neo_data/outputs/voice."}


def voice_project_asset_tray_payload(project_id: str = "", *, limit: int = 30) -> dict[str, Any]:
    tray = project_workspace_asset_tray_payload(project_id, limit=limit)
    links = tray.get("links") if isinstance(tray.get("links"), list) else []
    handoffs = tray.get("handoffs") if isinstance(tray.get("handoffs"), list) else []
    timeline = tray.get("timeline") if isinstance(tray.get("timeline"), list) else []
    voice_links = [item for item in links if isinstance(item, dict) and (item.get("surface") == "voice" or item.get("resource_type") == "voice_output")]
    voice_handoffs = [item for item in handoffs if isinstance(item, dict) and (item.get("source_surface") == "voice" or item.get("resource_type") == "voice_output")]
    voice_timeline = [item for item in timeline if isinstance(item, dict) and (item.get("surface") == "voice" or str(item.get("event_type") or "").startswith("voice."))]
    cap = max(1, int(limit or 30))
    return {"ok": True, "schema_id": VOICE_PROJECT_ASSET_TRAY_SCHEMA, "phase": "VO-V16", "project_id": tray.get("project_id"), "active_project_id": tray.get("active_project_id"), "project": tray.get("project") if isinstance(tray.get("project"), dict) else {}, "voice_links": voice_links[:cap], "voice_handoffs": voice_handoffs[:cap], "voice_timeline": voice_timeline[:cap], "summary": {"voice_link_count": len(voice_links), "voice_handoff_count": len(voice_handoffs), "voice_timeline_count": len(voice_timeline), "total_link_count": (tray.get("summary") or {}).get("link_count", 0) if isinstance(tray.get("summary"), dict) else 0}, "project_asset_tray": tray, "policy": "Voice Project Asset Tray is a filtered Project Workspace view for Voice surface links, handoffs, and timeline events."}
