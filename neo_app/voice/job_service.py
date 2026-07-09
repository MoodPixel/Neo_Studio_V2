from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import re
import wave
import struct

from .output_paths import get_voice_output_paths, resolve_voice_output_file, sanitize_path_part
from .adapter_client import default_voice_profile, is_fish_selection, is_kokoro_selection, voice_backend_tier, voice_capabilities_payload, voice_health_payload, voice_remote_post_payload
from .reference_audio import reference_record, analyze_reference_audio_path
from .profile_store import resolve_voice_profile
from .export_service import export_voice_output, voice_export_history_payload
from .replay_memory import attach_replay_memory_to_job, voice_memory_events_payload, voice_replay_history_payload, voice_replay_payload
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot

VOICE_JOB_SCHEMA = "neo.voice.job.v12"
VOICE_CHUNK_PLAN_SCHEMA = "neo.voice.chunk_plan.v12"
VOICE_RENDER_METADATA_SCHEMA = "neo.voice.render_metadata.v12"
ROOT = Path(__file__).resolve().parents[2]
JOB_HISTORY = ROOT / "neo_data" / "outputs" / "voice" / "history" / "voice_jobs.v9.json"
LEGACY_JOB_HISTORY = ROOT / "neo_data" / "outputs" / "voice" / "history" / "voice_jobs.v7.json"


def _voice_runtime_summary(job: dict[str, Any] | None, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    job = job if isinstance(job, dict) else {}
    script = job.get("script_snapshot") if isinstance(job.get("script_snapshot"), dict) else {}
    outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    payload: dict[str, Any] = {
        "schema_id": "neo.voice.runtime_log.summary.pass_m.v1",
        "job_id": job.get("job_id") or "",
        "job_type": job.get("job_type") or "",
        "status": job.get("status") or "",
        "family": job.get("family") or "",
        "model_id": job.get("model_id") or "",
        "runtime": job.get("runtime") or "",
        "profile_id": job.get("profile_id") or "",
        "script_title": script.get("title") or "",
        "script_language": script.get("language") or "",
        "script_char_count": len(str(script.get("text") or "")),
        "chunk_count": len(((job.get("chunk_plan") or {}).get("chunks") or [])) if isinstance(job.get("chunk_plan"), dict) else 0,
        "dialogue_turn_count": len(((job.get("dialogue_plan") or {}).get("turns") or [])) if isinstance(job.get("dialogue_plan"), dict) else 0,
        "output_file": job.get("output_file") or job.get("final_output") or "",
        "metadata_file": job.get("metadata_file") or "",
        "output_file_count": len(files),
        "backend_reachable": bool(((job.get("backend") or {}) if isinstance(job.get("backend"), dict) else {}).get("reachable")),
    }
    if extra:
        payload.update(extra)
    return payload


def _log_voice_runtime_event(event: str, job: dict[str, Any] | None, *, level: str = "INFO", extra: dict[str, Any] | None = None, snapshot: bool = True) -> None:
    try:
        summary = _voice_runtime_summary(job, extra=extra)
        run_id = summary.get("job_id") or None
        log_surface_event("voice", event, run_id=run_id, level=level, payload=summary)
        if snapshot:
            record_surface_snapshot("voice", "neo_last_job", summary, run_id=run_id)
    except Exception:
        pass


def _log_voice_runtime_error(message: str, job: dict[str, Any] | None = None, *, exc: BaseException | None = None, extra: dict[str, Any] | None = None) -> None:
    try:
        summary = _voice_runtime_summary(job, extra=extra)
        record_surface_error("voice", message, exc=exc, payload=summary, run_id=summary.get("job_id") or None)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_silent_wav(path: Path, *, seconds: float = 0.35, sample_rate: int = 16000) -> None:
    """Create a valid Neo-owned WAV placeholder for UI/runtime validation.

    Real synthesis is delegated to the configured Voice backend. When a backend does
    not return files yet, this keeps the Voice UI honest: the job/manifest/output
    pipeline works without pretending audio quality has been produced locally.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = max(1, int(sample_rate * max(0.05, seconds)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        silence = struct.pack("<h", 0)
        wav.writeframes(silence * frame_count)


def _write_preview_wav(path: Path, *, seconds: float = 0.35, sample_rate: int = 16000) -> None:
    _write_silent_wav(path, seconds=seconds, sample_rate=sample_rate)


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def build_voice_chunk_plan(text: str, *, max_chars: int = 650) -> dict[str, Any]:
    """Plan stable script chunks for full render jobs.

    Sentence boundaries are preferred. Very long sentences are hard-split so a weak
    backend does not receive unbounded text. This is deterministic and stored in the
    render manifest for replay/recovery.
    """
    max_chars = max(160, min(int(max_chars or 650), 2400))
    chunks: list[dict[str, Any]] = []
    buffer = ""
    cursor = 0

    def push(value: str) -> None:
        nonlocal cursor
        cleaned = value.strip()
        if not cleaned:
            return
        chunk_id = f"chunk_{len(chunks) + 1:03d}"
        start = cursor
        cursor += len(cleaned)
        chunks.append({
            "chunk_id": chunk_id,
            "index": len(chunks),
            "text": cleaned,
            "char_count": len(cleaned),
            "word_count": len(cleaned.split()),
            "status": "planned",
            "start_char": start,
            "end_char": cursor,
        })

    for sentence in _split_sentences(text):
        while len(sentence) > max_chars:
            split_at = sentence.rfind(" ", 0, max_chars)
            if split_at < max(80, int(max_chars * 0.45)):
                split_at = max_chars
            part = sentence[:split_at].strip()
            sentence = sentence[split_at:].strip()
            if buffer:
                push(buffer)
                buffer = ""
            push(part)
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            push(buffer)
            buffer = sentence
    if buffer:
        push(buffer)
    if not chunks and str(text or "").strip():
        push(str(text).strip()[:max_chars])
    return {
        "schema_id": VOICE_CHUNK_PLAN_SCHEMA,
        "strategy": "sentence_boundary_with_hard_split",
        "max_chars": max_chars,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }



DIALOGUE_PLAN_SCHEMA = "neo.voice.dialogue_plan.v12"
DIALOGUE_RENDER_METADATA_SCHEMA = "neo.voice.dialogue_render_metadata.v12"


def parse_dialogue_script(text: str) -> dict[str, Any]:
    """Parse simple VO-V12 speaker blocks.

    Supported forms:
    [Speaker]
    Line text...

    Speaker: Line text...

    Plain text becomes a Narrator turn. The parser is deterministic and does not
    infer emotions/actions; it only assigns provided text to speakers.
    """
    turns: list[dict[str, Any]] = []
    speakers: dict[str, dict[str, Any]] = {}
    current = "Narrator"
    buffer: list[str] = []

    def clean_name(value: str) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip().strip("[]:")
        return value[:64] or "Narrator"

    def ensure_speaker(name: str) -> None:
        if name not in speakers:
            speakers[name] = {"speaker_id": sanitize_path_part(name.lower().replace(" ", "_"), "speaker"), "name": name, "turn_count": 0, "word_count": 0}

    def flush() -> None:
        nonlocal buffer
        text_value = "\n".join(part.strip() for part in buffer if part.strip()).strip()
        if not text_value:
            buffer = []
            return
        ensure_speaker(current)
        turn = {
            "turn_id": f"turn_{len(turns) + 1:03d}",
            "index": len(turns),
            "speaker": current,
            "speaker_id": speakers[current]["speaker_id"],
            "text": text_value,
            "char_count": len(text_value),
            "word_count": len(text_value.split()),
            "status": "planned",
        }
        speakers[current]["turn_count"] += 1
        speakers[current]["word_count"] += turn["word_count"]
        turns.append(turn)
        buffer = []

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        bracket = re.match(r"^\[([^\]]{1,64})\]\s*$", line)
        colon = re.match(r"^([A-Za-z0-9_ .'-]{1,64})\s*:\s*(.+)$", line)
        if bracket:
            flush()
            current = clean_name(bracket.group(1))
            ensure_speaker(current)
            continue
        if colon:
            flush()
            current = clean_name(colon.group(1))
            ensure_speaker(current)
            buffer.append(colon.group(2).strip())
            continue
        buffer.append(line)
    flush()
    if not turns and str(text or "").strip():
        current = "Narrator"
        buffer = [str(text).strip()]
        flush()
    return {
        "schema_id": DIALOGUE_PLAN_SCHEMA,
        "strategy": "speaker_blocks_and_colon_lines",
        "speaker_count": len(speakers),
        "turn_count": len(turns),
        "speakers": list(speakers.values()),
        "turns": turns,
    }


def build_dialogue_speaker_manifest(dialogue_plan: dict[str, Any], speaker_map: dict[str, Any] | None = None, default_voice_source: dict[str, Any] | None = None) -> dict[str, Any]:
    speaker_map = speaker_map if isinstance(speaker_map, dict) else {}
    default_voice_source = default_voice_source if isinstance(default_voice_source, dict) else {"type": "built_in", "voice_id": "provider_default"}
    assignments: list[dict[str, Any]] = []
    for speaker in dialogue_plan.get("speakers", []):
        name = str(speaker.get("name") or "Narrator")
        specific = speaker_map.get(name) or speaker_map.get(speaker.get("speaker_id")) or {}
        if not isinstance(specific, dict):
            specific = {}
        voice_source = specific.get("voice_source") if isinstance(specific.get("voice_source"), dict) else None
        if not voice_source:
            voice_source = {**default_voice_source, **{k: v for k, v in specific.items() if k in {"type", "voice_id", "saved_profile_id", "reference_id", "reference_audio"}}}
        assignments.append({
            "speaker": name,
            "speaker_id": speaker.get("speaker_id"),
            "voice_source": voice_source,
            "params": specific.get("params") if isinstance(specific.get("params"), dict) else {},
            "language": specific.get("language") or "",
        })
    return {
        "schema_id": "neo.voice.speaker_manifest.v12",
        "assignment_rule": "speaker_name_or_id_exact_match_else_default_voice_source",
        "assignments": assignments,
    }


def _write_dialogue_metadata(job: dict[str, Any], final_file: Path) -> Path:
    meta_paths = get_voice_output_paths("metadata", create=True)
    meta_path = meta_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_dialogue')}.dialogue.v12.json")
    sidecar = {
        "schema_id": DIALOGUE_RENDER_METADATA_SCHEMA,
        "surface": "voice",
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "created_at": _now(),
        "updated_at": job.get("updated_at"),
        "backend": job.get("backend"),
        "family": job.get("family"),
        "model_id": job.get("model_id"),
        "runtime": job.get("runtime"),
        "script_snapshot": job.get("script_snapshot"),
        "dialogue_plan": job.get("dialogue_plan"),
        "speaker_manifest": job.get("speaker_manifest"),
        "turn_outputs": job.get("turn_outputs"),
        "params": job.get("params"),
        "final_output": _relative_to_root(final_file),
        "render_state": job.get("render_state"),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return meta_path


def _attach_dialogue_outputs(job: dict[str, Any], *, source: str = "neo_dialogue_turn_stub") -> dict[str, Any]:
    render_paths = get_voice_output_paths("render", create=True)
    chunk_paths = get_voice_output_paths("chunks", create=True)
    turn_outputs: list[dict[str, Any]] = []
    sample_rate = 16000
    for turn in job.get("dialogue_plan", {}).get("turns", []):
        turn_file = chunk_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_dialogue')}_{sanitize_path_part(turn.get('turn_id'), 'turn')}_{sanitize_path_part(turn.get('speaker'), 'speaker')}.wav")
        seconds = max(0.25, min(10.0, turn.get("word_count", 1) / 2.8))
        _write_silent_wav(turn_file, seconds=seconds, sample_rate=sample_rate)
        turn["status"] = "rendered_stub"
        turn["output_file"] = _relative_to_root(turn_file)
        turn_outputs.append({
            "turn_id": turn.get("turn_id"),
            "index": turn.get("index"),
            "speaker": turn.get("speaker"),
            "speaker_id": turn.get("speaker_id"),
            "status": turn.get("status"),
            "path": _relative_to_root(turn_file),
            "format": "wav",
            "source": source,
        })
    final_file = render_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_dialogue')}.wav")
    total_seconds = max(0.35, min(90.0, sum(max(0.25, turn.get("word_count", 1) / 2.8) for turn in job.get("dialogue_plan", {}).get("turns", []))))
    _write_silent_wav(final_file, seconds=total_seconds, sample_rate=sample_rate)
    job["turn_outputs"] = turn_outputs
    job["render_state"] = "dialogue_stitched" if turn_outputs else "empty_dialogue_script"
    job["final_output"] = _relative_to_root(final_file)
    job["output_file"] = _relative_to_root(final_file)
    meta_path = _write_dialogue_metadata(job, final_file)
    job["metadata_file"] = _relative_to_root(meta_path)
    job["outputs"] = {
        "category": render_paths.category,
        "output_dir": render_paths.relative_output_dir,
        "files": [{"kind": "dialogue_final_audio", "format": "wav", "path": _relative_to_root(final_file), "source": source, "playback_endpoint": "/api/voice/output-file"}],
        "turns": turn_outputs,
        "metadata": {"path": _relative_to_root(meta_path), "schema_id": DIALOGUE_RENDER_METADATA_SCHEMA},
    }
    attach_replay_memory_to_job(job)
    return job

def _write_preview_metadata(job: dict[str, Any], output_file: Path) -> Path:
    meta_paths = get_voice_output_paths("metadata", create=True)
    meta_path = meta_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_preview')}.preview.v11.json")
    sidecar = {
        "schema_id": "neo.voice.preview_metadata.v11",
        "surface": "voice",
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "created_at": _now(),
        "backend": job.get("backend"),
        "family": job.get("family"),
        "model_id": job.get("model_id"),
        "runtime": job.get("runtime"),
        "voice_source": job.get("voice_source"),
        "script_snapshot": job.get("script_snapshot"),
        "params": job.get("params"),
        "output_file": _relative_to_root(output_file),
        "preview_state": job.get("preview_state"),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return meta_path


def _write_render_metadata(job: dict[str, Any], final_file: Path) -> Path:
    meta_paths = get_voice_output_paths("metadata", create=True)
    meta_path = meta_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_render')}.render.v11.json")
    sidecar = {
        "schema_id": VOICE_RENDER_METADATA_SCHEMA,
        "surface": "voice",
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "created_at": _now(),
        "updated_at": job.get("updated_at"),
        "backend": job.get("backend"),
        "family": job.get("family"),
        "model_id": job.get("model_id"),
        "runtime": job.get("runtime"),
        "voice_source": job.get("voice_source"),
        "script_snapshot": job.get("script_snapshot"),
        "chunk_plan": job.get("chunk_plan"),
        "chunk_outputs": job.get("chunk_outputs"),
        "params": job.get("params"),
        "final_output": _relative_to_root(final_file),
        "render_state": job.get("render_state"),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return meta_path


def _attach_preview_output(job: dict[str, Any], *, source: str = "neo_preview_stub") -> dict[str, Any]:
    preview_paths = get_voice_output_paths("preview", create=True)
    file_path = preview_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_preview')}.wav")
    _write_preview_wav(file_path)
    meta_path = _write_preview_metadata(job, file_path)
    job["outputs"] = {
        "category": preview_paths.category,
        "output_dir": preview_paths.relative_output_dir,
        "files": [{"kind": "preview_audio", "format": "wav", "path": _relative_to_root(file_path), "source": source, "playback_endpoint": "/api/voice/output-file"}],
        "metadata": {"path": _relative_to_root(meta_path), "schema_id": "neo.voice.preview_metadata.v11"},
    }
    job["output_file"] = _relative_to_root(file_path)
    job["metadata_file"] = _relative_to_root(meta_path)
    job["preview_state"] = "current"
    attach_replay_memory_to_job(job)
    return job


def _attach_render_outputs(job: dict[str, Any], *, source: str = "neo_chunked_render_stub") -> dict[str, Any]:
    render_paths = get_voice_output_paths("render", create=True)
    chunk_paths = get_voice_output_paths("chunks", create=True)
    chunks = job.get("chunk_plan", {}).get("chunks", [])
    chunk_outputs: list[dict[str, Any]] = []
    sample_rate = 16000
    for chunk in chunks:
        chunk_file = chunk_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_render')}_{chunk.get('chunk_id')}.wav")
        seconds = max(0.25, min(8.0, chunk.get("word_count", 1) / 2.8))
        _write_silent_wav(chunk_file, seconds=seconds, sample_rate=sample_rate)
        chunk["status"] = "rendered_stub"
        chunk["output_file"] = _relative_to_root(chunk_file)
        chunk_outputs.append({
            "chunk_id": chunk.get("chunk_id"),
            "index": chunk.get("index"),
            "status": chunk.get("status"),
            "path": _relative_to_root(chunk_file),
            "format": "wav",
            "source": source,
        })
    final_file = render_paths.output_file(f"{sanitize_path_part(job.get('job_id'), 'voice_render')}.wav")
    total_seconds = max(0.35, min(60.0, sum(max(0.25, (chunk.get("word_count", 1) / 2.8)) for chunk in chunks)))
    _write_silent_wav(final_file, seconds=total_seconds, sample_rate=sample_rate)
    job["chunk_outputs"] = chunk_outputs
    job["render_state"] = "stitched" if chunk_outputs else "empty_script"
    job["final_output"] = _relative_to_root(final_file)
    job["output_file"] = _relative_to_root(final_file)
    meta_path = _write_render_metadata(job, final_file)
    job["metadata_file"] = _relative_to_root(meta_path)
    job["outputs"] = {
        "category": render_paths.category,
        "output_dir": render_paths.relative_output_dir,
        "files": [{"kind": "final_render_audio", "format": "wav", "path": _relative_to_root(final_file), "source": source, "playback_endpoint": "/api/voice/output-file"}],
        "chunks": chunk_outputs,
        "metadata": {"path": _relative_to_root(meta_path), "schema_id": VOICE_RENDER_METADATA_SCHEMA},
    }
    attach_replay_memory_to_job(job)
    return job


def _read_jobs() -> list[dict[str, Any]]:
    source = JOB_HISTORY if JOB_HISTORY.exists() else LEGACY_JOB_HISTORY
    if not source.exists():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_jobs(jobs: list[dict[str, Any]]) -> None:
    JOB_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    JOB_HISTORY.write_text(json.dumps(jobs[-300:], indent=2), encoding="utf-8")


def _job_transition(job: dict[str, Any], status: str, message: str | None = None) -> dict[str, Any]:
    job["status"] = status
    job["updated_at"] = _now()
    history = job.setdefault("state_history", [])
    history.append({"status": status, "at": job["updated_at"], "message": message or ""})
    queue = job.setdefault("queue_state", {"schema_id": "neo.voice.queue_state.v9"})
    queue["state"] = status
    queue["updated_at"] = job["updated_at"]
    if status in {"completed", "preview_ready", "render_ready", "clone_render_ready", "export_ready"} or status.endswith("_ready_backend_guarded") or status.endswith("_ready_with_backend_warning"):
        queue["terminal"] = True
        queue["recoverable"] = "warning" in status or "guarded" in status
    elif status in {"failed", "cancelled", "chunk_retry_ready", "missing_reference_audio", "clone_unsupported_for_backend"}:
        queue["terminal"] = status in {"failed", "cancelled", "clone_unsupported_for_backend"}
        queue["recoverable"] = True
    else:
        queue["terminal"] = False
        queue["recoverable"] = False
    return job


def _queue_actions(job: dict[str, Any]) -> list[str]:
    status = str(job.get("status") or "")
    actions = ["view", "reuse_settings"]
    if status not in {"completed", "cancelled", "failed"}:
        actions.append("cancel")
    if job.get("output_file"):
        actions.extend(["play", "open_folder", "copy_path", "export_wav", "export_mp3", "finish", "normalize", "silence_trim", "convert_audio", "split_chunks", "merge_chunks", "replay", "memory_export", "delete"])
    if job.get("job_type") in {"render", "clone"} and (job.get("chunk_outputs") or job.get("chunk_plan")):
        actions.append("retry_failed_chunk")
    if job.get("job_type") == "dialogue" and (job.get("turn_outputs") or job.get("dialogue_plan")):
        actions.append("retry_dialogue_turn")
    if status in {"failed", "cancelled", "render_ready_backend_guarded", "preview_ready_backend_guarded", "chunk_retry_ready", "dialogue_ready_backend_guarded"}:
        actions.append("retry_job")
    return sorted(set(actions))


def _hydrate_job_runtime(job: dict[str, Any]) -> dict[str, Any]:
    queue = job.setdefault("queue_state", {"schema_id": "neo.voice.queue_state.v9"})
    queue.setdefault("state", job.get("status") or "unknown")
    queue.setdefault("position", 0)
    queue.setdefault("recoverable", job.get("status") in {"failed", "cancelled", "chunk_retry_ready"})
    job["available_actions"] = _queue_actions(job)
    job.setdefault("recovery", {"schema_id": "neo.voice.recovery.v9", "can_retry_job": "retry_job" in job["available_actions"], "can_retry_chunk": "retry_failed_chunk" in job["available_actions"]})
    job.setdefault("export_state", {"schema_id": "neo.voice.export_state.v14", "formats": ["wav", "mp3"], "finish_formats": ["wav", "mp3"], "ready": bool(job.get("output_file"))})
    job.setdefault("finish_state", {"schema_id": "neo.voice.finish_state.v14", "ready": bool(job.get("output_file")), "operations": ["normalize", "silence_trim", "noise_cleanup", "loudness_target", "convert_audio", "split_chunks", "merge_chunks"]})
    if job.get("output_file"):
        job.setdefault("replay_metadata", {"schema_id": "neo.voice.replay_metadata.v15", "phase": "VO-V15", "ready": False})
        job.setdefault("memory_export", {"schema_id": "neo.voice.memory_export.v15", "phase": "VO-V15", "status": "pending_replay"})
    return job


def _store_job(job: dict[str, Any]) -> dict[str, Any]:
    job = _hydrate_job_runtime(job)
    jobs = [item for item in _read_jobs() if item.get("job_id") != job.get("job_id")]
    jobs.append(job)
    _write_jobs(jobs)
    _log_voice_runtime_event("voice.job.stored", job, snapshot=True)
    return job


def build_voice_job_payload(payload: dict[str, Any] | None, *, job_type: str) -> dict[str, Any]:
    data = payload or {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    script = str(data.get("script") or data.get("script_body") or params.get("script") or "").strip()
    profile_id = str(data.get("profile_id") or params.get("profile_id") or "")
    backend_profile = default_voice_profile(profile_id or None)
    profile_defaults = backend_profile.get("generation_defaults") if isinstance(backend_profile, dict) and isinstance(backend_profile.get("generation_defaults"), dict) else {}
    raw_family = str(data.get("family") or params.get("family") or profile_defaults.get("model_family") or "chatterbox_turbo")
    family_aliases = {"chatterbox": "chatterbox_turbo", "kokoro": "kokoro_preview", "fish_speech": "fish_hq", "fish": "fish_hq"}
    family = family_aliases.get(raw_family, raw_family)
    runtime = str(data.get("runtime") or params.get("runtime") or (backend_profile or {}).get("provider_id") or "chatterbox")
    model_id = str(data.get("model_id") or params.get("model_id") or family)
    output_paths = get_voice_output_paths("preview" if job_type == "preview" else "render", create=True)
    job_id = f"voice_{job_type}_{uuid4().hex[:12]}"
    health = voice_health_payload(profile_id or None)
    caps = voice_capabilities_payload(profile_id or None, family=family, runtime=runtime)
    status = "adapter_ready" if health.get("reachable") else "guarded_backend_not_connected"
    max_chunk_chars = int(params.get("max_chunk_chars") or data.get("max_chunk_chars") or 650)
    normalized_job_type = "clone" if str(job_type).lower() in {"clone", "clone_voice", "reference_clone"} else job_type
    chunk_plan = build_voice_chunk_plan(script, max_chars=max_chunk_chars) if normalized_job_type in {"render", "clone"} else None
    voice_source = data.get("voice_source") or params.get("voice_source") or {"type": data.get("voice_source_type") or "built_in", "voice_id": data.get("voice_id") or "provider_default"}
    if not isinstance(voice_source, dict):
        voice_source = {"type": "built_in", "voice_id": "provider_default"}
    saved_profile = None
    if voice_source.get("type") == "saved_profile":
        saved_profile_id = str(voice_source.get("saved_profile_id") or data.get("saved_profile_id") or "").strip()
        saved_profile = resolve_voice_profile(saved_profile_id) if saved_profile_id else None
        if saved_profile:
            voice_source = dict(voice_source)
            voice_source.update({
                "saved_profile_id": saved_profile.get("profile_id"),
                "saved_profile_name": saved_profile.get("name"),
                "profile_source_type": saved_profile.get("source_type"),
                "voice_id": voice_source.get("voice_id") or (saved_profile.get("voice_source") or {}).get("voice_id") or "provider_default",
                "reference_id": voice_source.get("reference_id") or saved_profile.get("reference_id") or "",
                "reference_audio": voice_source.get("reference_audio") or saved_profile.get("reference_audio") or "",
                "reference_qc": voice_source.get("reference_qc") or saved_profile.get("reference_qc"),
            })
            if not data.get("language") and saved_profile.get("language"):
                data = dict(data)
                data["language"] = saved_profile.get("language")
            params = {**(saved_profile.get("default_params") or {}), **params}
    reference_payload = None
    if voice_source.get("type") == "reference_clone" or voice_source.get("reference_id"):
        reference_id = str(voice_source.get("reference_id") or data.get("reference_id") or "").strip()
        reference_payload = reference_record(reference_id) if reference_id else None
        if reference_payload and not voice_source.get("reference_audio"):
            voice_source["reference_audio"] = reference_payload.get("path")
    job = {
        "schema_id": VOICE_JOB_SCHEMA,
        "job_id": job_id,
        "surface": "voice",
        "job_type": normalized_job_type,
        "status": status,
        "created_at": _now(),
        "updated_at": _now(),
        "profile_id": profile_id or health.get("profile_id") or "",
        "backend": health,
        "capabilities": caps,
        "family": family,
        "model_id": model_id,
        "runtime": runtime,
        "voice_source": voice_source,
        "script_snapshot": {
            "title": str(data.get("script_title") or params.get("script_title") or ""),
            "language": str(data.get("language") or params.get("language") or "en"),
            "text": script,
            "delivery_notes": str(data.get("delivery_notes") or params.get("delivery_notes") or ""),
        },
        "runtime_workspace": {"schema_id": "neo.voice.runtime_workspace.v12", "preview_stale_rule": "script/source/delivery changes stale the preview", "sections": ["script", "voice_source", "reference_clone", "delivery", "preview", "parameters", "chunk_plan", "queue", "history", "recovery", "export", "finish_tools", "normalize", "silence_trim", "convert_audio", "split_chunks", "merge_chunks", "replay_metadata", "memory_export", "hq_backend_controls", "dialogue_speaker_blocks", "speaker_mapping"], "backend_adapter": "kokoro_low_end" if is_kokoro_selection(backend_profile, family, runtime) else ("fish_hq_advanced" if is_fish_selection(backend_profile, family, runtime) else "voice_default")},
        "queue_state": {"schema_id": "neo.voice.queue_state.v9", "state": "queued", "position": 0, "terminal": False, "recoverable": False, "actions": ["cancel", "view"]},
        "backend_adapter": {"schema_id": "neo.voice.backend_adapter.v12", "provider_id": runtime, "family": family, "tier": voice_backend_tier(backend_profile, family, runtime), "label": "Kokoro Preview" if is_kokoro_selection(backend_profile, family, runtime) else ("Fish Speech HQ" if is_fish_selection(backend_profile, family, runtime) else caps.get("backend_label", "")), "clone_supported": bool(caps.get("support_flags", {}).get("supports_cloning")), "reference_supported": bool(caps.get("support_flags", {}).get("supports_reference_audio")), "advanced_hq": is_fish_selection(backend_profile, family, runtime), "warnings": ["higher_vram_expected", "slower_startup", "advanced_setup"] if is_fish_selection(backend_profile, family, runtime) else []},
        "state_history": [{"status": "queued", "at": _now(), "message": "Voice job created and queued."}],
        "recovery": {"schema_id": "neo.voice.recovery.v9", "can_retry_job": False, "can_retry_chunk": normalized_job_type in {"render", "clone"}, "retry_policy": "Retry creates new deterministic output records instead of overwriting previous files."},
        "export_state": {"schema_id": "neo.voice.export_state.v14", "formats": ["wav", "mp3"], "finish_formats": ["wav", "mp3"], "ready": False},
        "reference_audio": reference_payload,
        "saved_voice_profile": saved_profile,
        "chunk_plan": chunk_plan,
        "preview_state": "pending" if normalized_job_type == "preview" else "not_preview",
        "render_state": "planned" if normalized_job_type in {"render", "clone"} else "not_render",
        "params": params,
        "outputs": {"category": output_paths.category, "output_dir": output_paths.relative_output_dir, "files": []},
        "message": "Voice jobs use deterministic manifests and Neo-owned outputs. Kokoro uses a lightweight preview/render-only adapter lane; Fish Speech uses a guarded HQ/advanced adapter lane; VO-V13 keeps speaker-block dialogue manifests and batch/script import manifests; VO-V14 adds finish/post-processing manifests; VO-V15 adds replay sidecars and memory event exports.",
    }
    _log_voice_runtime_event("voice.job.created", job)
    return _store_job(job)


def preview_voice_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    job = build_voice_job_payload(payload, job_type="preview")
    if job["backend"].get("reachable"):
        remote = voice_remote_post_payload("/api/voice/preview", payload or {}, job.get("profile_id") or None)
        job["remote_submission"] = remote
        if remote.get("ok"):
            _job_transition(job, "preview_ready")
            job["message"] = "Preview request submitted to the configured Voice backend. Neo created a local playable preview record."
        else:
            _job_transition(job, "preview_ready_with_backend_warning")
            job["message"] = "Voice backend was reachable but did not return a preview response. Neo created a local preview record for UI playback validation."
    else:
        _job_transition(job, "preview_ready_backend_guarded")
        job["message"] = "Voice backend is not connected. Neo created a local preview record so the Quick Preview UI can be validated."
    _attach_preview_output(job)
    job["updated_at"] = _now()
    _log_voice_runtime_event("voice.preview.completed", job)
    return _store_job(job)


def render_voice_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    job = build_voice_job_payload(payload, job_type="render")
    if job["backend"].get("reachable"):
        remote = voice_remote_post_payload("/api/voice/render", payload or {}, job.get("profile_id") or None)
        job["remote_submission"] = remote
        if remote.get("ok"):
            _job_transition(job, "render_ready")
            job["message"] = "Render request submitted to the configured Voice backend. Neo stored chunk plan, final output reference, and manifest."
        else:
            _job_transition(job, "render_ready_with_backend_warning")
            job["message"] = "Voice backend was reachable but did not return a render response. Neo created chunked render records for UI and recovery validation."
    else:
        _job_transition(job, "render_ready_backend_guarded")
        job["message"] = "Voice backend is not connected. Neo created chunked render records so the full render pipeline can be validated."
    _attach_render_outputs(job)
    job["updated_at"] = _now()
    _log_voice_runtime_event("voice.render.completed", job)
    return _store_job(job)



def dialogue_voice_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    profile_id = str(data.get("profile_id") or params.get("profile_id") or "").strip()
    family = str(data.get("family") or params.get("family") or "").strip()
    runtime = str(data.get("runtime") or params.get("runtime") or "").strip()
    selected_profile = default_voice_profile(profile_id or None)
    caps = voice_capabilities_payload(profile_id or None, family=family or None, runtime=runtime or None)
    if is_kokoro_selection(selected_profile, family, runtime) or not caps.get("support_flags", {}).get("supports_dialogue"):
        job = build_voice_job_payload(data, job_type="dialogue")
        _job_transition(job, "dialogue_unsupported_for_backend")
        job["message"] = "Dialogue / Multi-speaker is not supported by the selected backend family. Select Chatterbox, Fish Speech HQ, or Custom TTS for dialogue workflows."
        job["updated_at"] = _now()
        _log_voice_runtime_event("voice.dialogue.blocked", job, level="WARNING")
        return _store_job(job)
    job = build_voice_job_payload(data, job_type="dialogue")
    script = job.get("script_snapshot", {}).get("text") or ""
    dialogue_plan = parse_dialogue_script(script)
    speaker_map = data.get("speaker_map") if isinstance(data.get("speaker_map"), dict) else (params.get("speaker_map") if isinstance(params.get("speaker_map"), dict) else {})
    speaker_manifest = build_dialogue_speaker_manifest(dialogue_plan, speaker_map, job.get("voice_source"))
    job["dialogue_plan"] = dialogue_plan
    job["speaker_manifest"] = speaker_manifest
    job["chunk_plan"] = None
    job["render_state"] = "dialogue_planned"
    job["runtime_workspace"]["dialogue_lane"] = {"schema_id": "neo.voice.dialogue_lane.v12", "speaker_count": dialogue_plan.get("speaker_count", 0), "turn_count": dialogue_plan.get("turn_count", 0), "render_rule": "Render each speaker turn with mapped voice source, then stitch into one combined output."}
    if job["backend"].get("reachable"):
        remote = voice_remote_post_payload("/api/voice/dialogue", data, job.get("profile_id") or None)
        job["remote_submission"] = remote
        _job_transition(job, "dialogue_render_ready" if remote.get("ok") else "dialogue_ready_with_backend_warning")
    else:
        _job_transition(job, "dialogue_ready_backend_guarded")
    job["message"] = "Dialogue lane created speaker blocks, speaker-to-profile assignments, per-turn output records, and a combined stitched WAV reference. Live voice quality depends on the configured backend."
    _attach_dialogue_outputs(job)
    job["updated_at"] = _now()
    _log_voice_runtime_event("voice.dialogue.completed", job)
    return _store_job(job)

def clone_voice_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source = data.get("voice_source") if isinstance(data.get("voice_source"), dict) else {}
    reference_id = str(source.get("reference_id") or data.get("reference_id") or "").strip()
    reference = reference_record(reference_id) if reference_id else None
    selected_profile_id = str(data.get("profile_id") or (data.get("params") or {}).get("profile_id") or "").strip()
    selected_profile = default_voice_profile(selected_profile_id or None)
    raw_family = str(data.get("family") or (data.get("params") or {}).get("family") or "")
    selected_runtime = str(data.get("runtime") or (data.get("params") or {}).get("runtime") or (selected_profile or {}).get("provider_id") or "")
    if is_kokoro_selection(selected_profile, raw_family, selected_runtime):
        job = build_voice_job_payload(data, job_type="clone")
        _job_transition(job, "clone_unsupported_for_backend")
        job["message"] = "Reference Clone is not supported by the Kokoro low-end adapter. Select Chatterbox or Fish Speech HQ for clone workflows."
        job["updated_at"] = _now()
        _log_voice_runtime_event("voice.clone.blocked", job, level="WARNING")
        return _store_job(job)
    if not reference:
        job = build_voice_job_payload(data, job_type="clone")
        _job_transition(job, "missing_reference_audio")
        job["message"] = "Reference Clone requires a staged reference audio file before preview or full clone render."
        job["updated_at"] = _now()
        _log_voice_runtime_event("voice.clone.blocked", job, level="WARNING")
        return _store_job(job)
    data = dict(data)
    source = dict(source)
    source.update({"type": "reference_clone", "reference_id": reference_id, "reference_audio": reference.get("path"), "qc_status": (reference.get("qc") or {}).get("status")})
    data["voice_source"] = source
    job = build_voice_job_payload(data, job_type="clone")
    job["reference_audio"] = reference
    if job["backend"].get("reachable"):
        remote = voice_remote_post_payload("/api/voice/render", data, job.get("profile_id") or None)
        job["remote_submission"] = remote
        _job_transition(job, "clone_render_ready" if remote.get("ok") else "clone_ready_with_backend_warning")
    else:
        _job_transition(job, "clone_ready_backend_guarded")
    job["message"] = "Reference Clone lane created a clone render manifest using staged reference audio and QC metadata. Backend synthesis is used when available; Neo-owned placeholder outputs validate the lane when guarded."
    _attach_render_outputs(job, source="neo_reference_clone_stub")
    job["updated_at"] = _now()
    _log_voice_runtime_event("voice.clone.completed", job)
    return _store_job(job)



def voice_job_payload(job_id: str) -> dict[str, Any]:
    job = next((item for item in _read_jobs() if item.get("job_id") == job_id), None)
    if not job:
        return {"ok": False, "status": "missing_job", "job_id": job_id}
    return {"ok": True, "status": job.get("status") or "unknown", "job": _hydrate_job_runtime(job)}


def cancel_voice_job_payload(job_id: str) -> dict[str, Any]:
    jobs = _read_jobs()
    for job in jobs:
        if job.get("job_id") == job_id:
            _job_transition(job, "cancelled", "Cancel requested from Voice queue.")
            job["queue_state"]["cancelled_at"] = job["updated_at"]
            _write_jobs(jobs)
            _log_voice_runtime_event("voice.job.cancelled", job, level="WARNING")
            return {"ok": True, "status": "cancelled", "job": _hydrate_job_runtime(job)}
    return {"ok": False, "status": "missing_job", "job_id": job_id}


def retry_voice_chunk_payload(job_id: str, chunk_id: str) -> dict[str, Any]:
    jobs = _read_jobs()
    for job in jobs:
        if job.get("job_id") != job_id:
            continue
        chunks = job.get("chunk_plan", {}).get("chunks", [])
        chunk = next((item for item in chunks if item.get("chunk_id") == chunk_id), None)
        if not chunk:
            return {"ok": False, "status": "missing_chunk", "job_id": job_id, "chunk_id": chunk_id}
        chunk["status"] = "retry_ready_stub"
        chunk["retried_at"] = _now()
        chunk_paths = get_voice_output_paths("chunks", create=True)
        chunk_file = chunk_paths.output_file(f"{sanitize_path_part(job_id, 'voice_render')}_{sanitize_path_part(chunk_id, 'chunk')}_retry.wav")
        _write_silent_wav(chunk_file, seconds=max(0.25, chunk.get("word_count", 1) / 2.8))
        chunk["output_file"] = _relative_to_root(chunk_file)
        for output in job.get("chunk_outputs", []):
            if output.get("chunk_id") == chunk_id:
                output["status"] = chunk["status"]
                output["path"] = _relative_to_root(chunk_file)
        _job_transition(job, "chunk_retry_ready", f"Chunk {chunk_id} was retried and a replacement output was created.")
        _write_jobs(jobs)
        _log_voice_runtime_event("voice.chunk.retry_completed", job, extra={"chunk_id": chunk_id})
        return {"ok": True, "status": "chunk_retry_ready", "job": _hydrate_job_runtime(job), "chunk": chunk}
    return {"ok": False, "status": "missing_job", "job_id": job_id}


def voice_history_payload(limit: int = 50, job_type: str | None = None, status: str | None = None) -> dict[str, Any]:
    jobs = [_hydrate_job_runtime(item) for item in _read_jobs()]
    if job_type:
        jobs = [item for item in jobs if item.get("job_type") == job_type]
    if status:
        jobs = [item for item in jobs if item.get("status") == status]
    jobs = list(reversed(jobs))[: max(1, min(int(limit or 50), 300))]
    return {"schema_id": "neo.voice.history.v9", "surface": "voice", "phase": "VO-V9", "count": len(jobs), "jobs": jobs, "filters": {"job_type": job_type or "", "status": status or ""}}


def voice_queue_payload(limit: int = 50) -> dict[str, Any]:
    jobs = [_hydrate_job_runtime(item) for item in _read_jobs()]
    active = [item for item in jobs if not item.get("queue_state", {}).get("terminal") and item.get("status") not in {"cancelled", "failed"}]
    recent = list(reversed(jobs))[: max(1, min(int(limit or 50), 300))]
    return {
        "schema_id": "neo.voice.queue.v9",
        "surface": "voice",
        "phase": "VO-V9",
        "active_count": len(active),
        "recent_count": len(recent),
        "queue": active,
        "recent": recent,
        "actions": ["cancel", "retry_job", "retry_failed_chunk", "export_wav", "export_mp3", "reuse_settings", "delete"],
    }


def reuse_voice_job_settings_payload(job_id: str) -> dict[str, Any]:
    job = next((item for item in _read_jobs() if item.get("job_id") == job_id), None)
    if not job:
        return {"ok": False, "status": "missing_job", "job_id": job_id}
    script = job.get("script_snapshot") or {}
    payload = {
        "schema_id": "neo.voice.reuse_settings.v9",
        "surface": "voice",
        "source_job_id": job_id,
        "script_title": script.get("title") or "",
        "script": script.get("text") or "",
        "language": script.get("language") or "en",
        "delivery_notes": script.get("delivery_notes") or "",
        "family": job.get("family") or "chatterbox_turbo",
        "model_id": job.get("model_id") or job.get("family") or "chatterbox_turbo",
        "runtime": job.get("runtime") or "chatterbox",
        "voice_source": job.get("voice_source") or {"type": "built_in", "voice_id": "provider_default"},
        "params": job.get("params") or {},
        "speaker_manifest": job.get("speaker_manifest") or {},
        "dialogue_plan": job.get("dialogue_plan") or {},
    }
    return {"ok": True, "status": "settings_ready", "settings": payload}


def retry_voice_job_payload(job_id: str) -> dict[str, Any]:
    reused = reuse_voice_job_settings_payload(job_id)
    if not reused.get("ok"):
        return reused
    settings = dict(reused.get("settings") or {})
    source_job = next((item for item in _read_jobs() if item.get("job_id") == job_id), {})
    job_type = source_job.get("job_type") or "render"
    settings["parent_job_id"] = job_id
    if job_type == "preview":
        new_job = preview_voice_payload(settings)
    elif job_type == "clone":
        new_job = clone_voice_payload(settings)
    elif job_type == "dialogue":
        new_job = dialogue_voice_payload(settings)
    else:
        new_job = render_voice_payload(settings)
    new_job["recovery_of"] = job_id
    new_job["recovery"] = {"schema_id": "neo.voice.recovery.v9", "source_job_id": job_id, "mode": "retry_job", "status": "retry_created"}
    _log_voice_runtime_event("voice.job.retry_created", new_job, extra={"source_job_id": job_id})
    return {"ok": True, "status": "retry_created", "source_job_id": job_id, "job": _store_job(new_job)}


def export_voice_job_payload(job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    jobs = _read_jobs()
    for job in jobs:
        if job.get("job_id") == job_id:
            result = export_voice_output(job, payload or {})
            if result.get("ok"):
                exports = job.setdefault("exports", [])
                exports.append(result.get("export"))
                job["export_state"] = {"schema_id": "neo.voice.export_state.v14", "ready": True, "last_export": result.get("export"), "formats": ["wav", "mp3"], "finish_formats": ["wav", "mp3"]}
                _job_transition(job, "export_ready", "Voice output export created.")
                _write_jobs(jobs)
                _log_voice_runtime_event("voice.export.completed", job, extra={"export": result.get("export")})
            elif result.get("ok") is False:
                _log_voice_runtime_error("Voice export failed.", job, extra={"result": result})
            return result
    return {"ok": False, "status": "missing_job", "job_id": job_id}


def open_voice_job_folder_payload(job_id: str) -> dict[str, Any]:
    job = next((item for item in _read_jobs() if item.get("job_id") == job_id), None)
    if not job:
        return {"ok": False, "status": "missing_job", "job_id": job_id}
    raw = job.get("output_file") or job.get("final_output") or ""
    try:
        output_file = resolve_voice_output_file(raw)
    except Exception:
        return {"ok": False, "status": "missing_output_file", "job_id": job_id}
    return {"ok": True, "status": "folder_ready", "job_id": job_id, "folder": _relative_to_root(output_file.parent), "output_file": _relative_to_root(output_file)}


def delete_voice_job_payload(job_id: str, *, delete_files: bool = False) -> dict[str, Any]:
    jobs = _read_jobs()
    kept = []
    deleted = None
    removed_files: list[str] = []
    for job in jobs:
        if job.get("job_id") == job_id:
            deleted = job
            if delete_files:
                for raw in [job.get("output_file"), job.get("final_output"), job.get("metadata_file")]:
                    if not raw:
                        continue
                    try:
                        path = resolve_voice_output_file(str(raw))
                        path.unlink(missing_ok=True)
                        removed_files.append(_relative_to_root(path))
                    except Exception:
                        pass
            continue
        kept.append(job)
    if not deleted:
        return {"ok": False, "status": "missing_job", "job_id": job_id}
    _write_jobs(kept)
    return {"ok": True, "status": "deleted", "job_id": job_id, "removed_files": removed_files}



def voice_job_replay_payload(job_id: str) -> dict[str, Any]:
    job = next((item for item in _read_jobs() if item.get("job_id") == job_id), None)
    if not job:
        return {"ok": False, "status": "missing_job", "job_id": job_id}
    result = voice_replay_payload(job, write_if_missing=True)
    if result.get("ok"):
        # Persist replay pointers if this was created on demand.
        replay = result.get("replay") or {}
        event = result.get("memory_event") or {}
        job["replay_metadata"] = {
            "schema_id": "neo.voice.replay_metadata.v15",
            "phase": "VO-V15",
            "replay_id": replay.get("replay_id"),
            "path": replay.get("path"),
            "memory_event_id": event.get("event_id") or (job.get("replay_metadata") or {}).get("memory_event_id"),
        }
        job["memory_export"] = {
            "schema_id": "neo.voice.memory_export.v15",
            "phase": "VO-V15",
            "status": "indexed",
            "event_id": event.get("event_id") or (job.get("memory_export") or {}).get("event_id"),
            "replay_file": replay.get("path"),
        }
        _store_job(job)
    result["job_id"] = job_id
    return result


def voice_memory_exports_payload(limit: int = 50, job_type: str | None = None) -> dict[str, Any]:
    return voice_memory_events_payload(limit=limit, job_type=job_type)


def voice_replays_payload(limit: int = 50) -> dict[str, Any]:
    return voice_replay_history_payload(limit=limit)

def voice_exports_payload(limit: int = 50) -> dict[str, Any]:
    return voice_export_history_payload(limit=limit)
