from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from .output_paths import ROOT_DIR, get_voice_output_paths, sanitize_path_part

VOICE_REPLAY_SCHEMA = "neo.voice.replay_metadata.v15"
VOICE_MEMORY_EVENT_SCHEMA = "neo.voice.memory_event.v15"
VOICE_MEMORY_EXPORT_SCHEMA = "neo.voice.memory_export.v15"
VOICE_REPLAY_HISTORY_SCHEMA = "neo.voice.replay_history.v15"
VOICE_MEMORY_EVENT_INDEX = ROOT_DIR / "neo_data" / "outputs" / "voice" / "history" / "voice_memory_events.v15.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_index(path: Path = VOICE_MEMORY_EVENT_INDEX) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list[dict[str, Any]], path: Path = VOICE_MEMORY_EVENT_INDEX) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items[-600:], indent=2), encoding="utf-8")


def _first_output_file(job: dict[str, Any]) -> str:
    if job.get("output_file"):
        return str(job.get("output_file") or "")
    if job.get("final_output"):
        return str(job.get("final_output") or "")
    files = ((job.get("outputs") or {}).get("files") or []) if isinstance(job.get("outputs"), dict) else []
    for item in files:
        if isinstance(item, dict) and item.get("path"):
            return str(item.get("path") or "")
    return ""


def _script_fragment(job: dict[str, Any], max_chars: int = 500) -> dict[str, Any]:
    snapshot = job.get("script_snapshot") if isinstance(job.get("script_snapshot"), dict) else {}
    text = str(snapshot.get("text") or "")
    return {
        "title": str(snapshot.get("title") or ""),
        "language": str(snapshot.get("language") or "en"),
        "delivery_notes": str(snapshot.get("delivery_notes") or ""),
        "text_excerpt": text[:max_chars],
        "char_count": len(text),
        "word_count": len(text.split()),
        "truncated": len(text) > max_chars,
    }


def build_voice_replay_metadata(job: dict[str, Any]) -> dict[str, Any]:
    """Create a compact, reusable replay object for a completed Voice job.

    This is intentionally not a Project handoff. VO-V15 stores enough information
    for Assistant/Memory to say “reuse that last narration voice/settings” while
    avoiding binary duplication or timeline/project references.
    """
    output_file = _first_output_file(job)
    saved_profile = job.get("saved_voice_profile") if isinstance(job.get("saved_voice_profile"), dict) else {}
    reference = job.get("reference_audio") if isinstance(job.get("reference_audio"), dict) else {}
    replay_id = f"voice_replay_{sanitize_path_part(job.get('job_id'), 'job')}"
    return {
        "schema_id": VOICE_REPLAY_SCHEMA,
        "surface": "voice",
        "phase": "VO-V15",
        "replay_id": replay_id,
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "created_at": _now(),
        "backend_settings": {
            "profile_id": job.get("profile_id") or "",
            "runtime": job.get("runtime") or "chatterbox",
            "family": job.get("family") or "chatterbox_turbo",
            "model_id": job.get("model_id") or job.get("family") or "chatterbox_turbo",
            "backend_adapter": job.get("backend_adapter") or {},
        },
        "voice_source": job.get("voice_source") or {"type": "built_in", "voice_id": "provider_default"},
        "voice_profile_fact": {
            "profile_id": saved_profile.get("profile_id") or (job.get("voice_source") or {}).get("saved_profile_id") or "",
            "name": saved_profile.get("name") or (job.get("voice_source") or {}).get("saved_profile_name") or "",
            "source_type": saved_profile.get("source_type") or (job.get("voice_source") or {}).get("type") or "built_in",
            "reference_id": saved_profile.get("reference_id") or (job.get("voice_source") or {}).get("reference_id") or reference.get("reference_id") or "",
        },
        "script_fragment": _script_fragment(job),
        "params": job.get("params") or {},
        "dialogue_plan": job.get("dialogue_plan") or {},
        "speaker_manifest": job.get("speaker_manifest") or {},
        "chunk_plan_summary": {
            "schema_id": (job.get("chunk_plan") or {}).get("schema_id", ""),
            "chunk_count": (job.get("chunk_plan") or {}).get("chunk_count", 0),
            "strategy": (job.get("chunk_plan") or {}).get("strategy", ""),
        },
        "output_file_object": {
            "path": output_file,
            "format": "wav" if output_file.lower().endswith(".wav") else ("mp3" if output_file.lower().endswith(".mp3") else ""),
            "metadata_file": job.get("metadata_file") or "",
            "exports": job.get("exports") or [],
            "finish_records": job.get("finish_records") or [],
        },
        "reuse_payload": {
            "script_title": (job.get("script_snapshot") or {}).get("title") or "",
            "script": (job.get("script_snapshot") or {}).get("text") or "",
            "language": (job.get("script_snapshot") or {}).get("language") or "en",
            "delivery_notes": (job.get("script_snapshot") or {}).get("delivery_notes") or "",
            "family": job.get("family") or "chatterbox_turbo",
            "model_id": job.get("model_id") or job.get("family") or "chatterbox_turbo",
            "runtime": job.get("runtime") or "chatterbox",
            "voice_source": job.get("voice_source") or {"type": "built_in", "voice_id": "provider_default"},
            "params": job.get("params") or {},
            "speaker_manifest": job.get("speaker_manifest") or {},
        },
        "memory_summary": f"Voice {job.get('job_type') or 'job'} {job.get('job_id') or ''} used {(job.get('voice_source') or {}).get('type', 'built_in')} on {job.get('runtime') or 'chatterbox'} / {job.get('family') or 'chatterbox_turbo'}.",
        "non_goals": ["No binary copy", "No project asset handoff", "No timeline link"],
    }


def write_voice_replay_sidecar(job: dict[str, Any]) -> dict[str, Any]:
    metadata_paths = get_voice_output_paths("metadata", create=True)
    replay = build_voice_replay_metadata(job)
    path = metadata_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_job')}.replay.v15.json")
    path.write_text(json.dumps(replay, indent=2), encoding="utf-8")
    replay["path"] = _relative_to_root(path)
    return replay


def build_voice_memory_event(job: dict[str, Any], replay: dict[str, Any] | None = None) -> dict[str, Any]:
    replay = replay or build_voice_replay_metadata(job)
    event_id = f"voice_memory_{sanitize_path_part(job.get('job_id'), 'job')}"
    return {
        "schema_id": VOICE_MEMORY_EVENT_SCHEMA,
        "surface": "voice",
        "phase": "VO-V15",
        "event_id": event_id,
        "event_type": "voice_job_replay_ready",
        "created_at": _now(),
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "replay_id": replay.get("replay_id"),
        "replay_file": replay.get("path") or "",
        "script_fragment": replay.get("script_fragment") or {},
        "voice_profile_fact": replay.get("voice_profile_fact") or {},
        "backend_settings": replay.get("backend_settings") or {},
        "output_file_object": replay.get("output_file_object") or {},
        "memory_summary": replay.get("memory_summary") or "",
        "writeback_policy": "index_event_only_until_shared_memory_control_center_ingests_it",
    }


def index_voice_memory_event(job: dict[str, Any], replay: dict[str, Any] | None = None) -> dict[str, Any]:
    event = build_voice_memory_event(job, replay)
    items = [item for item in _read_index() if item.get("event_id") != event.get("event_id")]
    items.append(event)
    _write_index(items)
    return event


def attach_replay_memory_to_job(job: dict[str, Any]) -> dict[str, Any]:
    replay = write_voice_replay_sidecar(job)
    event = index_voice_memory_event(job, replay)
    job["replay_metadata"] = {
        "schema_id": VOICE_REPLAY_SCHEMA,
        "phase": "VO-V15",
        "replay_id": replay.get("replay_id"),
        "path": replay.get("path"),
        "memory_event_id": event.get("event_id"),
        "memory_event_file": _relative_to_root(VOICE_MEMORY_EVENT_INDEX),
    }
    job["memory_export"] = {
        "schema_id": VOICE_MEMORY_EXPORT_SCHEMA,
        "phase": "VO-V15",
        "status": "indexed",
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "replay_file": replay.get("path"),
    }
    return job


def voice_replay_payload(job: dict[str, Any] | None, *, write_if_missing: bool = True) -> dict[str, Any]:
    if not job:
        return {"ok": False, "status": "missing_job"}
    replay_meta = job.get("replay_metadata") if isinstance(job.get("replay_metadata"), dict) else {}
    replay_path = replay_meta.get("path") or ""
    resolved = ROOT_DIR / replay_path if replay_path else None
    if resolved and resolved.exists():
        try:
            replay = json.loads(resolved.read_text(encoding="utf-8"))
            replay["path"] = replay_path
            return {"ok": True, "status": "replay_ready", "replay": replay, "memory_export": job.get("memory_export") or {}}
        except Exception:
            pass
    if not write_if_missing:
        return {"ok": False, "status": "missing_replay", "job_id": job.get("job_id")}
    replay = write_voice_replay_sidecar(job)
    event = index_voice_memory_event(job, replay)
    return {"ok": True, "status": "replay_created", "replay": replay, "memory_event": event}


def voice_memory_events_payload(limit: int = 50, job_type: str | None = None) -> dict[str, Any]:
    items = _read_index()
    if job_type:
        items = [item for item in items if item.get("job_type") == job_type]
    items = list(reversed(items))[: max(1, min(int(limit or 50), 600))]
    return {
        "schema_id": "neo.voice.memory_events.v15",
        "surface": "voice",
        "phase": "VO-V15",
        "count": len(items),
        "items": items,
        "index_file": _relative_to_root(VOICE_MEMORY_EVENT_INDEX),
        "filters": {"job_type": job_type or ""},
    }


def voice_replay_history_payload(limit: int = 50) -> dict[str, Any]:
    metadata_dir = get_voice_output_paths("metadata", create=True).output_dir
    files = sorted(metadata_dir.glob("*.replay.v15.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for path in files[: max(1, min(int(limit or 50), 300))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "schema_id": data.get("schema_id") or VOICE_REPLAY_SCHEMA,
            "replay_id": data.get("replay_id"),
            "job_id": data.get("job_id"),
            "job_type": data.get("job_type"),
            "created_at": data.get("created_at"),
            "path": _relative_to_root(path),
            "memory_summary": data.get("memory_summary") or "",
        })
    return {"schema_id": VOICE_REPLAY_HISTORY_SCHEMA, "surface": "voice", "phase": "VO-V15", "count": len(items), "items": items}
