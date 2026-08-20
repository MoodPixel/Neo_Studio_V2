from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import re
import shutil
import subprocess

from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event

from .adapter_client import voice_provider_routing_payload
from .base_contract import VOICE_COMMON_DEFAULTS, normalize_voice_common_settings
from .generation_runtime import generate_voice_payload, poll_voice_generation_payload
from .output_paths import ROOT_DIR, get_voice_output_paths, resolve_voice_output_file, sanitize_path_part
from .profile_assets import apply_voice_profile_asset_payload
from .reference_clone_runtime import current_reference_payload, generate_voice_clone_payload, poll_voice_clone_payload

VOICE_DIALOGUE_PHASE = "VO-R10"
VOICE_DIALOGUE_PLAN_SCHEMA = "neo.voice.dialogue_plan.v1"
VOICE_DIALOGUE_CAPABILITIES_SCHEMA = "neo.voice.dialogue_capabilities.v1"
VOICE_DIALOGUE_JOB_SCHEMA = "neo.voice.dialogue_runtime_job.v1"
VOICE_DIALOGUE_METADATA_SCHEMA = "neo.voice.dialogue_runtime_metadata.v1"
VOICE_DIALOGUE_MODE = "voice_dialogue"
VOICE_DIALOGUE_MAX_SPEAKERS = 16
VOICE_DIALOGUE_MAX_TURNS = 200
VOICE_DIALOGUE_MAX_SCRIPT_CHARS = 100_000

_TERMINAL = {"completed", "failed", "cancelled", "canceled", "missing"}
_RUNNING = {"queued", "running", "pending", "submitted", "accepted", "processing", "in_progress"}


class VoiceDialogueRuntimeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _which(name: str) -> str:
    return str(shutil.which(name) or shutil.which(f"{name}.exe") or "")


def _speaker_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip("[]:")
    return text[:64] or "Narrator"


def _speaker_id(name: str) -> str:
    return sanitize_path_part(name.lower().replace(" ", "_"), "speaker")


def parse_voice_dialogue_script(text: str) -> dict[str, Any]:
    raw = str(text or "")
    if len(raw) > VOICE_DIALOGUE_MAX_SCRIPT_CHARS:
        raise VoiceDialogueRuntimeError(f"Dialogue script exceeds {VOICE_DIALOGUE_MAX_SCRIPT_CHARS} characters.")
    turns: list[dict[str, Any]] = []
    speakers: dict[str, dict[str, Any]] = {}
    current = "Narrator"
    buffer: list[str] = []

    def ensure(name: str) -> None:
        if name in speakers:
            return
        if len(speakers) >= VOICE_DIALOGUE_MAX_SPEAKERS:
            raise VoiceDialogueRuntimeError(f"Dialogue supports at most {VOICE_DIALOGUE_MAX_SPEAKERS} speakers in VO-R10.")
        speakers[name] = {"speaker_id": _speaker_id(name), "name": name, "turn_count": 0, "word_count": 0}

    def flush() -> None:
        nonlocal buffer
        value = "\n".join(part.strip() for part in buffer if part.strip()).strip()
        buffer = []
        if not value:
            return
        if len(turns) >= VOICE_DIALOGUE_MAX_TURNS:
            raise VoiceDialogueRuntimeError(f"Dialogue supports at most {VOICE_DIALOGUE_MAX_TURNS} turns in VO-R10.")
        ensure(current)
        turn = {
            "turn_id": f"turn_{len(turns) + 1:03d}",
            "index": len(turns),
            "speaker": current,
            "speaker_id": speakers[current]["speaker_id"],
            "text": value,
            "char_count": len(value),
            "word_count": len(value.split()),
            "status": "planned",
        }
        speakers[current]["turn_count"] += 1
        speakers[current]["word_count"] += turn["word_count"]
        turns.append(turn)

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        bracket = re.match(r"^\[([^\]]{1,64})\]\s*$", line)
        colon = re.match(r"^([A-Za-z0-9_ .'-]{1,64})\s*:\s*(.+)$", line)
        if bracket:
            flush()
            current = _speaker_name(bracket.group(1))
            ensure(current)
            continue
        if colon:
            flush()
            current = _speaker_name(colon.group(1))
            ensure(current)
            buffer.append(colon.group(2).strip())
            continue
        buffer.append(line)
    flush()
    if not turns and raw.strip():
        current = "Narrator"
        buffer = [raw.strip()]
        flush()
    return {
        "schema_id": VOICE_DIALOGUE_PLAN_SCHEMA,
        "phase": VOICE_DIALOGUE_PHASE,
        "strategy": "speaker_blocks_and_colon_lines",
        "speaker_count": len(speakers),
        "turn_count": len(turns),
        "speakers": list(speakers.values()),
        "turns": turns,
        "limits": {"max_speakers": VOICE_DIALOGUE_MAX_SPEAKERS, "max_turns": VOICE_DIALOGUE_MAX_TURNS, "max_script_chars": VOICE_DIALOGUE_MAX_SCRIPT_CHARS},
    }


def voice_dialogue_capabilities_payload(profile_id: str | None = None) -> dict[str, Any]:
    routing = voice_provider_routing_payload(profile_id=profile_id)
    capabilities = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    ffmpeg = _which("ffmpeg")
    profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
    backend_ready = bool(routing.get("routing_ready") is True and (routing.get("health") or {}).get("reachable") is True)
    provider_dialogue = bool(capabilities.get("dialogue") is True)
    ready = bool(backend_ready and provider_dialogue and ffmpeg)
    reasons: list[str] = []
    if routing.get("routing_ready") is not True:
        reasons.append("Select a valid Voice backend profile.")
    elif (routing.get("health") or {}).get("reachable") is not True:
        reasons.append("Connect the selected Voice backend.")
    if not provider_dialogue:
        reasons.append("The selected Voice backend does not advertise dialogue capability.")
    if not ffmpeg:
        reasons.append("FFmpeg is required to stitch real per-turn audio into a Dialogue result.")
    return {
        "schema_id": VOICE_DIALOGUE_CAPABILITIES_SCHEMA,
        "phase": VOICE_DIALOGUE_PHASE,
        "surface": "voice",
        "status": "ready" if ready else "gated",
        "ready": ready,
        "profile_id": str(profile.get("profile_id") or profile_id or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "family": str(profile.get("family") or ""),
        "provider_dialogue": provider_dialogue,
        "backend_reachable": backend_ready,
        "ffmpeg_available": bool(ffmpeg),
        "ffmpeg_path": ffmpeg,
        "speaker_sources": ["built_in", "profile_asset", "reference_clone"],
        "limits": {"max_speakers": VOICE_DIALOGUE_MAX_SPEAKERS, "max_turns": VOICE_DIALOGUE_MAX_TURNS, "max_script_chars": VOICE_DIALOGUE_MAX_SCRIPT_CHARS},
        "reasons": reasons,
        "policy": {
            "backend": "one_selected_backend_profile_for_entire_dialogue_never_auto_switch",
            "turn_execution": "reuse_current_tts_and_reference_clone_child_runtimes",
            "combined_output": "real_ffmpeg_stitch_only_no_placeholder_audio",
            "batch": "out_of_scope",
        },
    }


def _catalog_ids(block: Any) -> set[str]:
    if not isinstance(block, dict):
        return set()
    return {str(item.get("id") or "").strip() for item in (block.get("items") or []) if isinstance(item, dict) and str(item.get("id") or "").strip()}


def _validate_catalog_value(block: dict[str, Any] | None, value: Any, label: str) -> str:
    selected = str(value or "provider_default").strip() or "provider_default"
    if selected == "provider_default":
        return selected
    if selected not in _catalog_ids(block):
        raise VoiceDialogueRuntimeError(f"Speaker {label} '{selected}' is not available for the selected Voice backend profile.")
    return selected


def _mapping_for_speaker(speaker: dict[str, Any], speaker_map: dict[str, Any]) -> dict[str, Any]:
    name = str(speaker.get("name") or "Narrator")
    sid = str(speaker.get("speaker_id") or _speaker_id(name))
    raw = speaker_map.get(name)
    if raw is None:
        raw = speaker_map.get(sid)
    return dict(raw) if isinstance(raw, dict) else {}


def _base_common(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("common_settings") if isinstance(payload.get("common_settings"), dict) else payload.get("common") if isinstance(payload.get("common"), dict) else {}
    data = dict(raw)
    data["script"] = ""
    normalized = normalize_voice_common_settings({"common_settings": data}, require_script=False)
    return dict(normalized.get("common_settings") or VOICE_COMMON_DEFAULTS)


def _normalize_speaker_assignments(plan: dict[str, Any], payload: dict[str, Any], routing: dict[str, Any]) -> list[dict[str, Any]]:
    speaker_map = payload.get("speaker_map") if isinstance(payload.get("speaker_map"), dict) else payload.get("speaker_mapping") if isinstance(payload.get("speaker_mapping"), dict) else {}
    base_common = _base_common(payload)
    profile_id = str((routing.get("profile") or {}).get("profile_id") or "")
    batch_lineage = dict(payload.get("batch_lineage") or {}) if isinstance(payload.get("batch_lineage"), dict) else {}
    caps = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    assignments: list[dict[str, Any]] = []
    for speaker in plan.get("speakers") or []:
        mapping = _mapping_for_speaker(speaker, speaker_map)
        source_type = str(mapping.get("source_type") or mapping.get("type") or "built_in").strip().lower()
        if source_type not in {"built_in", "profile_asset", "reference_clone"}:
            raise VoiceDialogueRuntimeError(f"Speaker '{speaker.get('name')}' has unsupported source_type '{source_type}'.")
        common = dict(base_common)
        common["script"] = ""
        reference_id = ""
        profile_asset_id = ""
        turn_mode = "tts"
        warnings: list[str] = []

        if source_type == "built_in":
            common["model_id"] = _validate_catalog_value(routing.get("models"), mapping.get("model_id") or common.get("model_id"), "model")
            common["voice_id"] = _validate_catalog_value(routing.get("voices"), mapping.get("voice_id") or common.get("voice_id"), "voice")
            if mapping.get("language"):
                common["language"] = str(mapping.get("language"))[:32]
            if mapping.get("speaking_rate") not in (None, ""):
                common["speaking_rate"] = max(0.5, min(2.0, float(mapping.get("speaking_rate"))))
        elif source_type == "profile_asset":
            profile_asset_id = str(mapping.get("asset_id") or mapping.get("profile_asset_id") or "").strip()
            if not profile_asset_id:
                raise VoiceDialogueRuntimeError(f"Speaker '{speaker.get('name')}' needs a Voice Profile Asset.")
            applied = apply_voice_profile_asset_payload(profile_asset_id, {"backend_profile_id": profile_id})
            if applied.get("ok") is not True:
                raise VoiceDialogueRuntimeError(f"Voice Profile Asset '{profile_asset_id}' could not be applied to speaker '{speaker.get('name')}'.")
            asset_common = applied.get("common_settings") if isinstance(applied.get("common_settings"), dict) else {}
            common.update({key: value for key, value in asset_common.items() if key != "script"})
            reference_id = str(applied.get("reference_id") or "")
            if reference_id:
                if caps.get("voice_clone") is not True or caps.get("reference_audio") is not True:
                    raise VoiceDialogueRuntimeError(f"Speaker '{speaker.get('name')}' profile asset requires reference cloning, which the selected backend does not support.")
                turn_mode = "voice_clone"
            warnings.extend(str(item) for item in (applied.get("warnings") or []))
        else:
            if caps.get("voice_clone") is not True or caps.get("reference_audio") is not True:
                raise VoiceDialogueRuntimeError(f"Speaker '{speaker.get('name')}' requires reference cloning, which the selected backend does not support.")
            reference_id = str(mapping.get("reference_id") or "").strip()
            if not reference_id:
                raise VoiceDialogueRuntimeError(f"Speaker '{speaker.get('name')}' needs an authorized reference asset.")
            detail = current_reference_payload(reference_id)
            reference = detail.get("reference") if isinstance(detail.get("reference"), dict) else None
            if not reference or reference.get("clone_ready") is not True:
                raise VoiceDialogueRuntimeError(f"Reference '{reference_id}' for speaker '{speaker.get('name')}' is not current R6 clone-ready.")
            common["model_id"] = _validate_catalog_value(routing.get("models"), mapping.get("model_id") or common.get("model_id"), "model")
            common["voice_id"] = _validate_catalog_value(routing.get("voices"), mapping.get("voice_id") or common.get("voice_id"), "voice")
            turn_mode = "voice_clone"

        assignments.append({
            "speaker": str(speaker.get("name") or "Narrator"),
            "speaker_id": str(speaker.get("speaker_id") or "speaker"),
            "source_type": source_type,
            "mode": turn_mode,
            "common_settings": common,
            "profile_asset_id": profile_asset_id,
            "reference_id": reference_id,
            "warnings": warnings,
        })
    return assignments


def _assignment_lookup(assignments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in assignments:
        out[str(item.get("speaker_id") or "")] = item
        out[str(item.get("speaker") or "")] = item
    return out


def _turn_provider_controls(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    root = payload.get("provider_controls") if isinstance(payload.get("provider_controls"), dict) else {}
    if isinstance(root.get(mode), dict):
        return dict(root[mode])
    # Backward/simple caller: a flat block is treated as TTS-only.
    if mode == "tts" and root and not any(key in root for key in ("tts", "voice_clone")):
        return dict(root)
    return {}


def _register_parent(*, plan: dict[str, Any], assignments: list[dict[str, Any]], payload: dict[str, Any], routing: dict[str, Any]) -> dict[str, Any]:
    profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
    job_id = f"voice_dialogue_{uuid4().hex[:12]}"
    registry = get_generation_job_registry()
    base_common = _base_common(payload)
    base_common["script"] = str(payload.get("script") or (payload.get("common_settings") or {}).get("script") or "")
    batch_lineage = payload.get("batch_lineage") if isinstance(payload.get("batch_lineage"), dict) else {}
    runtime = {
        "schema_id": VOICE_DIALOGUE_JOB_SCHEMA,
        "phase": VOICE_DIALOGUE_PHASE,
        "route_snapshot": {
            "profile_id": str(profile.get("profile_id") or ""),
            "provider_id": str(profile.get("provider_id") or ""),
            "family": str(profile.get("family") or ""),
            "model_id": str(base_common.get("model_id") or "provider_default"),
            "voice_id": str(base_common.get("voice_id") or "provider_default"),
        },
        "common_settings": base_common,
        "batch": batch_lineage,
        "dialogue": {
            "schema_id": VOICE_DIALOGUE_JOB_SCHEMA,
            "phase": VOICE_DIALOGUE_PHASE,
            "plan": plan,
            "assignments": assignments,
            "turn_jobs": [],
            "stitch_engine": "ffmpeg_concat",
            "provider_controls_policy": "global_mode_scoped_r8_controls_revalidated_by_each_child_runtime",
        },
        "progress": {"percent": 1, "stage": "planning", "label": "Preparing Dialogue turns"},
    }
    return registry.register_queued(
        job_id=job_id,
        surface="voice",
        provider_id=str(profile.get("provider_id") or ""),
        profile_id=str(profile.get("profile_id") or ""),
        backend_profile_id=str(profile.get("profile_id") or ""),
        provider_job_id=job_id,
        local_job_id=job_id,
        backend="voice_dialogue_orchestrator",
        mode=VOICE_DIALOGUE_MODE,
        family=str(profile.get("family") or ""),
        loader="current_voice_child_runtimes",
        model=str(base_common.get("model_id") or "provider_default"),
        submitted_job={
            "surface": "voice",
            "mode": VOICE_DIALOGUE_MODE,
            "profile_id": str(profile.get("profile_id") or ""),
            "common_settings": base_common,
            "dialogue_script": base_common.get("script") or "",
            "speaker_map": payload.get("speaker_map") if isinstance(payload.get("speaker_map"), dict) else payload.get("speaker_mapping") if isinstance(payload.get("speaker_mapping"), dict) else {},
            "provider_controls": payload.get("provider_controls") if isinstance(payload.get("provider_controls"), dict) else {},
            "batch_lineage": batch_lineage,
        },
        runtime=runtime,
        output_expectations={"kind": "audio", "format": "wav", "neo_owned_copy_required": True, "dialogue_turn_count": plan.get("turn_count") or 0},
        message="Voice Dialogue queued.",
    )


def _child_result_status(result: dict[str, Any]) -> str:
    return str(result.get("status") or "").strip().lower()


def _submit_turn(parent: dict[str, Any], turn: dict[str, Any], assignment: dict[str, Any], provider_controls: dict[str, Any]) -> dict[str, Any]:
    common = dict(assignment.get("common_settings") or {})
    common["script"] = str(turn.get("text") or "")
    parent_runtime = parent.get("runtime") if isinstance(parent.get("runtime"), dict) else {}
    parent_batch = parent_runtime.get("batch") if isinstance(parent_runtime.get("batch"), dict) else {}
    child_batch = dict(parent_batch)
    if child_batch:
        child_batch.update({"dialogue_parent_job_id": str(parent.get("job_id") or ""), "dialogue_turn_id": str(turn.get("turn_id") or ""), "dialogue_speaker": str(turn.get("speaker") or "")})
    payload = {
        "profile_id": str(parent.get("profile_id") or ""),
        "common_settings": common,
        "provider_controls": provider_controls,
        "voice_profile_asset_id": str(assignment.get("profile_asset_id") or ""),
        "batch_lineage": child_batch,
    }
    if assignment.get("mode") == "voice_clone":
        payload["reference_id"] = str(assignment.get("reference_id") or "")
        child = generate_voice_clone_payload(payload)
    else:
        child = generate_voice_payload(payload)
    return {
        "turn_id": str(turn.get("turn_id") or ""),
        "index": int(turn.get("index") or 0),
        "speaker": str(turn.get("speaker") or "Narrator"),
        "speaker_id": str(turn.get("speaker_id") or "speaker"),
        "mode": str(assignment.get("mode") or "tts"),
        "source_type": str(assignment.get("source_type") or "built_in"),
        "profile_asset_id": str(assignment.get("profile_asset_id") or ""),
        "reference_id": str(assignment.get("reference_id") or ""),
        "child_job_id": str(child.get("job_id") or ""),
        "status": str(child.get("status") or "failed"),
        "message": str(child.get("message") or ""),
        "output_file": str(child.get("output_file") or ""),
    }


def _safe_turn_output(child_job_id: str) -> Path:
    record = get_generation_job_registry().get(child_job_id, surface="voice")
    if not isinstance(record, dict) or str(record.get("status") or "").lower() != "completed":
        raise VoiceDialogueRuntimeError(f"Dialogue child job '{child_job_id}' is not completed.")
    for output in record.get("outputs") if isinstance(record.get("outputs"), list) else []:
        if isinstance(output, dict) and output.get("path"):
            return resolve_voice_output_file(str(output["path"]))
    raise VoiceDialogueRuntimeError(f"Dialogue child job '{child_job_id}' has no retrievable Neo-owned audio.")


def _metadata_path(job_id: str) -> Path:
    return get_voice_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(job_id, 'voice_dialogue')}.dialogue.r10.json")


def _stitch_dialogue(parent: dict[str, Any]) -> dict[str, Any]:
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise VoiceDialogueRuntimeError("FFmpeg is required to stitch Dialogue audio.")
    runtime = parent.get("runtime") if isinstance(parent.get("runtime"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    turn_jobs = dialogue.get("turn_jobs") if isinstance(dialogue.get("turn_jobs"), list) else []
    if not turn_jobs:
        raise VoiceDialogueRuntimeError("Dialogue has no rendered turns to stitch.")
    paths = [_safe_turn_output(str(item.get("child_job_id") or "")) for item in turn_jobs]
    output = get_voice_output_paths("render", create=True).output_file(f"{sanitize_path_part(parent.get('job_id'), 'voice_dialogue')}.wav")
    concat_file = get_voice_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(parent.get('job_id'), 'voice_dialogue')}.concat.r10.txt")
    concat_lines: list[str] = []
    for path in paths:
        # FFmpeg concat demuxer accepts forward-slash paths on Windows. Escaping
        # apostrophes separately avoids nested quote syntax hazards in Python.
        escaped_path = path.as_posix().replace("'", "'\\''")
        concat_lines.append(f"file '{escaped_path}'")
    concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-vn", "-c:a", "pcm_s16le", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    try:
        concat_file.unlink(missing_ok=True)
    except Exception:
        pass
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 44:
        error = (result.stderr or result.stdout or "FFmpeg dialogue stitching failed.").strip()
        raise VoiceDialogueRuntimeError(error[-2400:])
    turn_outputs = []
    for item, path in zip(turn_jobs, paths):
        turn_outputs.append({**item, "status": "completed", "output_file": _relative(path)})
    metadata = {
        "schema_id": VOICE_DIALOGUE_METADATA_SCHEMA,
        "phase": VOICE_DIALOGUE_PHASE,
        "surface": "voice",
        "job_id": str(parent.get("job_id") or ""),
        "profile_id": str(parent.get("profile_id") or ""),
        "provider_id": str(parent.get("provider_id") or ""),
        "created_at": str(parent.get("created_at") or ""),
        "completed_at": _now(),
        "plan": dialogue.get("plan") or {},
        "assignments": dialogue.get("assignments") or [],
        "turn_jobs": turn_outputs,
        "combined_output": _relative(output),
        "stitch_engine": "ffmpeg_concat_pcm_s16le",
        "policy": "real_child_audio_only_no_placeholder_turns_or_combined_audio",
    }
    metadata_path = _metadata_path(str(parent.get("job_id") or ""))
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    output_record = {
        "kind": "voice_dialogue",
        "path": _relative(output),
        "format": "wav",
        "mime_type": "audio/wav",
        "source": "neo_voice_dialogue_r10",
        "metadata_file": _relative(metadata_path),
        "playback_endpoint": "/api/voice/output-file",
    }
    return {"output": output_record, "turn_outputs": turn_outputs, "metadata_file": _relative(metadata_path)}


def _parent_result(record: dict[str, Any] | None) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    return {
        "schema_id": VOICE_DIALOGUE_JOB_SCHEMA,
        "phase": VOICE_DIALOGUE_PHASE,
        "ok": str(record.get("status") or "") == "completed",
        "surface": "voice",
        "job_id": str(record.get("job_id") or ""),
        "status": str(record.get("status") or "missing"),
        "message": str(record.get("message") or ""),
        "profile_id": str(record.get("profile_id") or ""),
        "provider_id": str(record.get("provider_id") or ""),
        "progress": record.get("progress") if isinstance(record.get("progress"), dict) else {},
        "outputs": outputs,
        "output_file": str(outputs[0].get("path") or "") if outputs and isinstance(outputs[0], dict) else "",
        "dialogue": dialogue,
        "error": str(record.get("error") or ""),
    }


def _update_parent_turns(parent: dict[str, Any], turn_jobs: list[dict[str, Any]], *, message: str) -> dict[str, Any]:
    registry = get_generation_job_registry()
    completed = sum(1 for item in turn_jobs if str(item.get("status") or "").lower() == "completed")
    total = max(1, len(turn_jobs))
    percent = min(94, 5 + int((completed / total) * 84))
    return registry.mark_running(
        str(parent.get("job_id") or ""), surface="voice", message=message,
        runtime={"dialogue": {"turn_jobs": turn_jobs, "completed_turns": completed, "turn_count": total}},
        progress={"percent": percent, "stage": "turn_generation", "label": f"Dialogue turns {completed}/{total}"},
        poll_state={"completed_turns": completed, "turn_count": total},
    )


def generate_voice_dialogue_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or "").strip()
    routing = voice_provider_routing_payload(profile_id=profile_id or None)
    capabilities = voice_dialogue_capabilities_payload(profile_id=profile_id or None)
    if capabilities.get("ready") is not True:
        return {"schema_id": VOICE_DIALOGUE_JOB_SCHEMA, "phase": VOICE_DIALOGUE_PHASE, "ok": False, "status": "dialogue_not_ready", "message": "; ".join(capabilities.get("reasons") or ["Dialogue is unavailable."]), "profile_id": profile_id, "outputs": [], "capabilities": capabilities}
    script = str(data.get("script") or (data.get("common_settings") or {}).get("script") or "")
    if not script.strip():
        return {"schema_id": VOICE_DIALOGUE_JOB_SCHEMA, "phase": VOICE_DIALOGUE_PHASE, "ok": False, "status": "invalid_dialogue_script", "message": "Dialogue script is required.", "profile_id": profile_id, "outputs": []}
    try:
        plan = parse_voice_dialogue_script(script)
        if plan.get("turn_count", 0) < 1:
            raise VoiceDialogueRuntimeError("Dialogue script contains no renderable turns.")
        assignments = _normalize_speaker_assignments(plan, data, routing)
    except (VoiceDialogueRuntimeError, ValueError) as exc:
        return {"schema_id": VOICE_DIALOGUE_JOB_SCHEMA, "phase": VOICE_DIALOGUE_PHASE, "ok": False, "status": "invalid_speaker_mapping", "message": str(exc), "profile_id": profile_id, "outputs": []}

    parent = _register_parent(plan=plan, assignments=assignments, payload={**data, "script": script}, routing=routing)
    registry = get_generation_job_registry()
    lookup = _assignment_lookup(assignments)
    turn_jobs: list[dict[str, Any]] = []
    for turn in plan.get("turns") or []:
        assignment = lookup.get(str(turn.get("speaker_id") or "")) or lookup.get(str(turn.get("speaker") or ""))
        if not assignment:
            failed = registry.mark_failed(parent["job_id"], surface="voice", message=f"Dialogue speaker mapping disappeared for {turn.get('speaker')}", error="missing_speaker_assignment")
            return _parent_result(failed)
        controls = _turn_provider_controls(data, str(assignment.get("mode") or "tts"))
        child = _submit_turn(parent, turn, assignment, controls)
        turn_jobs.append(child)
        if _child_result_status(child) in {"failed", "cancelled", "canceled", "missing"} or not child.get("child_job_id"):
            failed = registry.mark_failed(parent["job_id"], surface="voice", message=f"Dialogue turn {turn.get('turn_id')} failed: {child.get('message') or child.get('status')}", error="dialogue_child_failed", runtime={"dialogue": {"turn_jobs": turn_jobs, "failed_turn_id": turn.get("turn_id")}})
            return _parent_result(failed)
        parent = _update_parent_turns(parent, turn_jobs, message=f"Submitted Dialogue turn {len(turn_jobs)}/{plan.get('turn_count') or len(turn_jobs)}.")

    if all(_child_result_status(item) == "completed" for item in turn_jobs):
        try:
            stitched = _stitch_dialogue(parent)
        except Exception as exc:  # noqa: BLE001
            failed = registry.mark_failed(parent["job_id"], surface="voice", message=f"Dialogue stitching failed: {exc}", error=str(exc), runtime={"dialogue": {"turn_jobs": turn_jobs, "stitch_failed": True}})
            return _parent_result(failed)
        completed = registry.mark_completed(parent["job_id"], surface="voice", message="Voice Dialogue completed with real per-turn audio and a Neo-owned combined output.", outputs=[stitched["output"]], runtime={"dialogue": {"turn_jobs": stitched["turn_outputs"], "metadata_file": stitched["metadata_file"], "completed_at": _now()}}, progress={"percent": 100, "stage": "completed", "label": "Voice Dialogue completed"})
        log_surface_event("voice", "voice.dialogue.completed", run_id=parent["job_id"], payload={"phase": VOICE_DIALOGUE_PHASE, "turn_count": len(turn_jobs), "speaker_count": plan.get("speaker_count")})
        return _parent_result(completed)

    running = registry.mark_running(parent["job_id"], surface="voice", message="Voice Dialogue is waiting for provider turn jobs.", runtime={"dialogue": {"turn_jobs": turn_jobs}}, progress={"percent": max(8, int(sum(1 for item in turn_jobs if _child_result_status(item) == 'completed') / max(1, len(turn_jobs)) * 85)), "stage": "turn_generation", "label": "Voice Dialogue turn generation"}, poll_state={"turn_job_ids": [item.get("child_job_id") for item in turn_jobs]})
    log_surface_event("voice", "voice.dialogue.submitted", run_id=parent["job_id"], payload={"phase": VOICE_DIALOGUE_PHASE, "turn_count": len(turn_jobs), "speaker_count": plan.get("speaker_count")})
    return _parent_result(running)


def poll_voice_dialogue_payload(job_id: str) -> dict[str, Any]:
    registry = get_generation_job_registry()
    parent = registry.get(job_id, surface="voice")
    if not isinstance(parent, dict) or str(parent.get("mode") or "") != VOICE_DIALOGUE_MODE:
        return {"schema_id": VOICE_DIALOGUE_JOB_SCHEMA, "phase": VOICE_DIALOGUE_PHASE, "ok": False, "status": "missing", "message": "Voice Dialogue job not found.", "job_id": job_id, "outputs": []}
    if str(parent.get("status") or "").lower() in _TERMINAL:
        return _parent_result(parent)
    runtime = parent.get("runtime") if isinstance(parent.get("runtime"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    turn_jobs = [dict(item) for item in (dialogue.get("turn_jobs") or []) if isinstance(item, dict)]
    if not turn_jobs:
        return _parent_result(registry.mark_failed(job_id, surface="voice", message="Voice Dialogue has no child turn jobs.", error="missing_turn_jobs"))

    updated: list[dict[str, Any]] = []
    for item in turn_jobs:
        status = str(item.get("status") or "").lower()
        child_job_id = str(item.get("child_job_id") or "")
        if status in _RUNNING:
            child_result = poll_voice_clone_payload(child_job_id) if item.get("mode") == "voice_clone" else poll_voice_generation_payload(child_job_id)
            item["status"] = str(child_result.get("status") or status)
            item["message"] = str(child_result.get("message") or item.get("message") or "")
            item["output_file"] = str(child_result.get("output_file") or item.get("output_file") or "")
        updated.append(item)
        if str(item.get("status") or "").lower() in {"failed", "cancelled", "canceled", "missing"}:
            failed = registry.mark_failed(job_id, surface="voice", message=f"Dialogue turn {item.get('turn_id')} failed: {item.get('message') or item.get('status')}", error="dialogue_child_failed", runtime={"dialogue": {"turn_jobs": updated + turn_jobs[len(updated):], "failed_turn_id": item.get("turn_id")}})
            return _parent_result(failed)

    if all(str(item.get("status") or "").lower() == "completed" for item in updated):
        parent = registry.upsert(job_id, surface="voice", updates={"runtime": {"dialogue": {"turn_jobs": updated}}})
        try:
            stitched = _stitch_dialogue(parent)
        except Exception as exc:  # noqa: BLE001
            failed = registry.mark_failed(job_id, surface="voice", message=f"Dialogue stitching failed: {exc}", error=str(exc), runtime={"dialogue": {"turn_jobs": updated, "stitch_failed": True}})
            return _parent_result(failed)
        completed = registry.mark_completed(job_id, surface="voice", message="Voice Dialogue completed with real per-turn audio and a Neo-owned combined output.", outputs=[stitched["output"]], runtime={"dialogue": {"turn_jobs": stitched["turn_outputs"], "metadata_file": stitched["metadata_file"], "completed_at": _now()}}, progress={"percent": 100, "stage": "completed", "label": "Voice Dialogue completed"})
        log_surface_event("voice", "voice.dialogue.completed", run_id=job_id, payload={"phase": VOICE_DIALOGUE_PHASE, "turn_count": len(updated)})
        return _parent_result(completed)

    running = _update_parent_turns(parent, updated, message="Voice Dialogue turn generation is running.")
    return _parent_result(running)
