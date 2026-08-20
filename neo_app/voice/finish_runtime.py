from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import shutil
import subprocess

from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event

from .base_contract import normalize_voice_common_settings
from .output_paths import ROOT_DIR, get_voice_output_paths, resolve_voice_output_file, sanitize_path_part

VOICE_FINISH_PHASE = "VO-R9"
VOICE_FINISH_CAPABILITIES_SCHEMA = "neo.voice.finish_runtime_capabilities.v1"
VOICE_FINISH_JOB_SCHEMA = "neo.voice.finish_runtime_job.v1"
VOICE_FINISH_HISTORY_SCHEMA = "neo.voice.finish_runtime_history.v1"
VOICE_FINISH_METADATA_SCHEMA = "neo.voice.finish_runtime_metadata.v1"

_CURRENT_SOURCE_MODES = {"tts", "voice_clone", "voice_dialogue", "voice_finish", "voice_finish_merge"}
_CURRENT_FINISH_MODES = {"voice_finish", "voice_finish_split", "voice_finish_merge"}
_TERMINAL = {"completed", "failed", "cancelled", "canceled"}
_SUPPORTED_OUTPUT_FORMATS = {"wav", "mp3"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _which(name: str) -> str:
    return str(shutil.which(name) or shutil.which(f"{name}.exe") or "")


def _run_command(command: list[str], *, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=max(5.0, float(timeout)), check=False)


def _available_ffmpeg_filters(ffmpeg: str) -> set[str]:
    if not ffmpeg:
        return set()
    try:
        result = _run_command([ffmpeg, "-hide_banner", "-filters"], timeout=8.0)
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    filters: set[str] = set()
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(("T", ".", "S", "A", "V")):
            filters.add(parts[1])
    # Some builds print the flag column with combinations not covered above.
    for name in ("loudnorm", "silenceremove", "afftdn", "concat"):
        if name in (result.stdout or ""):
            filters.add(name)
    return filters


def voice_finish_capabilities_payload() -> dict[str, Any]:
    ffmpeg = _which("ffmpeg")
    ffprobe = _which("ffprobe")
    filters = _available_ffmpeg_filters(ffmpeg)
    ready = bool(ffmpeg)
    operations = {
        "normalize": {"available": ready and "loudnorm" in filters, "engine": "ffmpeg:loudnorm", "default_target_lufs": -16.0},
        "silence_trim": {"available": ready and "silenceremove" in filters, "engine": "ffmpeg:silenceremove"},
        "noise_cleanup": {"available": ready and "afftdn" in filters, "engine": "ffmpeg:afftdn"},
        "loudness_target": {"available": ready and "loudnorm" in filters, "engine": "ffmpeg:loudnorm", "range_lufs": [-30.0, -5.0]},
        "convert_audio": {"available": ready, "engine": "ffmpeg", "formats": sorted(_SUPPORTED_OUTPUT_FORMATS)},
        "split": {"available": ready and bool(ffprobe), "engine": "ffmpeg+ffprobe", "max_parts": 50},
        "merge": {"available": ready and "concat" in filters, "engine": "ffmpeg:concat", "max_sources": 20},
    }
    return {
        "schema_id": VOICE_FINISH_CAPABILITIES_SCHEMA,
        "phase": VOICE_FINISH_PHASE,
        "surface": "voice",
        "status": "ready" if ready else "processor_missing",
        "ready": ready,
        "provider_independent": True,
        "source_policy": "neo_owned_voice_result_only",
        "result_authority": "shared_generation_job_registry",
        "engine": {
            "id": "ffmpeg",
            "available": ready,
            "ffmpeg_path": ffmpeg,
            "ffprobe_path": ffprobe,
        },
        "operations": operations,
        "supported_output_formats": sorted(_SUPPORTED_OUTPUT_FORMATS),
        "legacy_policy": "VO-V14 /api/voice/finish* remains compatibility-only; current R9 never creates placeholder audio.",
    }


def _registry_record(job_id: str) -> dict[str, Any] | None:
    return get_generation_job_registry().get(job_id, surface="voice")


def _first_output(record: dict[str, Any]) -> dict[str, Any]:
    for item in record.get("outputs") if isinstance(record.get("outputs"), list) else []:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            return dict(item)
    return {}


def _safe_output_path(record: dict[str, Any]) -> Path:
    output = _first_output(record)
    raw = str(output.get("path") or "").strip()
    if not raw:
        raise FileNotFoundError("Voice source result has no output file.")
    return resolve_voice_output_file(raw)


def _resolve_source_job(job_id: str) -> tuple[dict[str, Any], Path]:
    record = _registry_record(str(job_id or "").strip())
    if not isinstance(record, dict):
        raise FileNotFoundError("Voice source result was not found.")
    mode = str(record.get("mode") or "").strip().lower()
    if mode not in _CURRENT_SOURCE_MODES:
        raise ValueError(f"Voice Finish cannot use source mode: {mode or 'unknown'}")
    if str(record.get("status") or "").strip().lower() != "completed":
        raise ValueError("Voice Finish requires a completed source result.")
    return record, _safe_output_path(record)


def _source_recipe(record: dict[str, Any]) -> dict[str, Any]:
    submitted = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    raw_common = submitted.get("common_settings") if isinstance(submitted.get("common_settings"), dict) else runtime.get("common_settings") if isinstance(runtime.get("common_settings"), dict) else {}
    normalized = normalize_voice_common_settings({"common_settings": raw_common})
    common = normalized.get("common_settings") if isinstance(normalized.get("common_settings"), dict) else {}
    provider_controls = submitted.get("provider_controls") if isinstance(submitted.get("provider_controls"), dict) else runtime.get("provider_controls") if isinstance(runtime.get("provider_controls"), dict) else {}
    route = runtime.get("route_snapshot") if isinstance(runtime.get("route_snapshot"), dict) else {}
    reference = runtime.get("reference") if isinstance(runtime.get("reference"), dict) else {}
    profile_asset = runtime.get("profile_asset") if isinstance(runtime.get("profile_asset"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    # Re-finishing a previous R9 child keeps the original generation recipe and source lineage.
    finish = runtime.get("finish") if isinstance(runtime.get("finish"), dict) else {}
    if finish.get("source_recipe") and isinstance(finish.get("source_recipe"), dict):
        inherited = finish["source_recipe"]
        common = inherited.get("common_settings") if isinstance(inherited.get("common_settings"), dict) else common
        provider_controls = inherited.get("provider_controls") if isinstance(inherited.get("provider_controls"), dict) else provider_controls
        route = inherited.get("route_snapshot") if isinstance(inherited.get("route_snapshot"), dict) else route
        reference = inherited.get("reference") if isinstance(inherited.get("reference"), dict) else reference
        profile_asset = inherited.get("profile_asset") if isinstance(inherited.get("profile_asset"), dict) else profile_asset
        dialogue = inherited.get("dialogue") if isinstance(inherited.get("dialogue"), dict) else dialogue
    return {
        "common_settings": dict(common),
        "provider_controls": dict(provider_controls),
        "route_snapshot": dict(route),
        "reference": dict(reference),
        "profile_asset": dict(profile_asset),
        "dialogue": dict(dialogue),
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _finish_output_path(job_id: str, *, output_format: str, label: str = "finished") -> Path:
    output_format = output_format.lower().lstrip(".")
    base = sanitize_path_part(f"{job_id}_{label}", "voice_finish")
    return get_voice_output_paths("finish", create=True).output_file(f"{base}.{output_format}")


def _metadata_path(job_id: str) -> Path:
    return get_voice_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(job_id, 'voice_finish')}.finish.r9.json")


def _audio_codec_args(output_format: str) -> list[str]:
    if output_format == "mp3":
        return ["-c:a", "libmp3lame", "-q:a", "2"]
    return ["-c:a", "pcm_s16le"]


def _error_text(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "FFmpeg processing failed.").strip()
    if len(text) > 2400:
        text = text[-2400:]
    return text


def _validate_lufs(value: Any) -> float | None:
    if value in (None, "", False):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Loudness target must be a number between -30 and -5 LUFS.")
    if number < -30.0 or number > -5.0:
        raise ValueError("Loudness target must be between -30 and -5 LUFS.")
    return round(number, 2)


def _normalize_process_settings(payload: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    operations = capabilities.get("operations") if isinstance(capabilities.get("operations"), dict) else {}
    output_format = str(payload.get("output_format") or payload.get("format") or "wav").lower().strip().lstrip(".")
    if output_format not in _SUPPORTED_OUTPUT_FORMATS:
        raise ValueError("Voice Finish output format must be WAV or MP3.")
    normalize = bool(payload.get("normalize"))
    silence_trim = bool(payload.get("silence_trim") or payload.get("trim_silence"))
    noise_cleanup = bool(payload.get("noise_cleanup"))
    loudness_target = _validate_lufs(payload.get("loudness_target"))
    requested = {
        "normalize": normalize,
        "silence_trim": silence_trim,
        "noise_cleanup": noise_cleanup,
        "loudness_target": loudness_target,
    }
    for key in ("normalize", "silence_trim", "noise_cleanup"):
        if requested[key] and not bool((operations.get(key) or {}).get("available")):
            raise RuntimeError(f"Voice Finish operation '{key}' is unavailable in the installed FFmpeg build.")
    if loudness_target is not None and not bool((operations.get("loudness_target") or {}).get("available")):
        raise RuntimeError("Voice Finish loudness targeting is unavailable in the installed FFmpeg build.")
    if not bool((operations.get("convert_audio") or {}).get("available")):
        raise RuntimeError("FFmpeg is required for current Voice Finish processing.")
    return {**requested, "output_format": output_format}


def _filter_chain(settings: dict[str, Any]) -> list[str]:
    filters: list[str] = []
    if settings.get("silence_trim"):
        filters.append("silenceremove=start_periods=1:start_silence=0.05:start_threshold=-50dB:stop_periods=1:stop_silence=0.10:stop_threshold=-50dB")
    if settings.get("noise_cleanup"):
        filters.append("afftdn=nf=-25")
    loudness = settings.get("loudness_target")
    if loudness is not None or settings.get("normalize"):
        target = float(loudness if loudness is not None else -16.0)
        filters.append(f"loudnorm=I={target:g}:TP=-1.5:LRA=11")
    return filters


def _register_job(*, mode: str, source_records: list[dict[str, Any]], settings: dict[str, Any], operation: str) -> dict[str, Any]:
    registry = get_generation_job_registry()
    source = source_records[0]
    recipe = _source_recipe(source)
    route = recipe.get("route_snapshot") if isinstance(recipe.get("route_snapshot"), dict) else {}
    job_id = f"voice-finish-{uuid4().hex[:12]}"
    source_ids = [str(record.get("job_id") or "") for record in source_records]
    runtime = {
        "phase": VOICE_FINISH_PHASE,
        "route_snapshot": route,
        "common_settings": recipe.get("common_settings") or {},
        "provider_controls": recipe.get("provider_controls") or {},
        "reference": recipe.get("reference") or {},
        "profile_asset": recipe.get("profile_asset") or {},
        "dialogue": recipe.get("dialogue") or {},
        "finish": {
            "schema_id": VOICE_FINISH_JOB_SCHEMA,
            "phase": VOICE_FINISH_PHASE,
            "operation": operation,
            "settings": settings,
            "source_job_id": source_ids[0] if source_ids else "",
            "source_job_ids": source_ids,
            "source_recipe": recipe,
            "provider_independent": True,
            "engine": "ffmpeg_local",
        },
    }
    record = registry.register_queued(
        job_id=job_id,
        surface="voice",
        provider_id="neo_finish",
        profile_id=str(route.get("profile_id") or source.get("profile_id") or ""),
        backend_profile_id=str(route.get("profile_id") or source.get("backend_profile_id") or source.get("profile_id") or ""),
        provider_job_id=job_id,
        local_job_id=job_id,
        backend="ffmpeg_local",
        mode=mode,
        family="audio_finish",
        model="ffmpeg",
        submitted_job={
            "surface": "voice",
            "mode": mode,
            "operation": operation,
            "settings": settings,
            "source_job_id": source_ids[0] if source_ids else "",
            "source_job_ids": source_ids,
            "common_settings": recipe.get("common_settings") or {},
            "provider_controls": recipe.get("provider_controls") or {},
        },
        runtime=runtime,
        output_expectations={"neo_owned": True, "category": "finish"},
        message="Voice Finish job queued.",
    )
    return record


def _finish_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": VOICE_FINISH_JOB_SCHEMA,
        "phase": VOICE_FINISH_PHASE,
        "ok": str(record.get("status") or "").lower() != "failed",
        "job_id": str(record.get("job_id") or ""),
        "status": str(record.get("status") or "unknown"),
        "message": str(record.get("message") or ""),
        "mode": str(record.get("mode") or ""),
        "progress": record.get("progress") if isinstance(record.get("progress"), dict) else {},
        "outputs": record.get("outputs") if isinstance(record.get("outputs"), list) else [],
        "runtime": record.get("runtime") if isinstance(record.get("runtime"), dict) else {},
    }


def _write_metadata(record: dict[str, Any]) -> str:
    path = _metadata_path(str(record.get("job_id") or "voice_finish"))
    payload = {
        "schema_id": VOICE_FINISH_METADATA_SCHEMA,
        "phase": VOICE_FINISH_PHASE,
        "created_at": _now(),
        "job_id": record.get("job_id"),
        "mode": record.get("mode"),
        "status": record.get("status"),
        "runtime": record.get("runtime") if isinstance(record.get("runtime"), dict) else {},
        "outputs": record.get("outputs") if isinstance(record.get("outputs"), list) else [],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return _relative(path)


def process_voice_finish_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source_job_id = str(data.get("source_job_id") or data.get("job_id") or "").strip()
    try:
        source_record, source_path = _resolve_source_job(source_job_id)
    except (FileNotFoundError, ValueError) as exc:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_source", "message": str(exc), "job_id": "", "outputs": []}
    capabilities = voice_finish_capabilities_payload()
    if not capabilities.get("ready"):
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "processor_missing", "message": "FFmpeg is required for current Voice Finish execution.", "job_id": "", "outputs": []}
    try:
        settings = _normalize_process_settings(data, capabilities)
    except (ValueError, RuntimeError) as exc:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_finish_settings", "message": str(exc), "job_id": "", "outputs": []}

    record = _register_job(mode="voice_finish", source_records=[source_record], settings=settings, operation="process")
    registry = get_generation_job_registry()
    job_id = str(record.get("job_id") or "")
    running = registry.mark_running(job_id, surface="voice", message="Voice Finish is processing locally.", runtime={"finish": {"engine": "ffmpeg_local", "settings": settings}}, progress={"percent": 25, "stage": "processing", "label": "Processing Voice Finish"})
    target = _finish_output_path(job_id, output_format=settings["output_format"], label="finished")
    ffmpeg = str((capabilities.get("engine") or {}).get("ffmpeg_path") or "")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_path), "-vn"]
    filters = _filter_chain(settings)
    if filters:
        command += ["-af", ",".join(filters)]
    command += _audio_codec_args(settings["output_format"]) + [str(target)]
    try:
        result = _run_command(command, timeout=float(data.get("timeout_seconds") or 240.0))
    except Exception as exc:
        failed = registry.mark_failed(job_id, surface="voice", message=f"Voice Finish execution failed: {exc}", error=str(exc), runtime={"finish": {"command_failed": True}})
        return _finish_result(failed)
    if result.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        message = _error_text(result)
        failed = registry.mark_failed(job_id, surface="voice", message=f"Voice Finish failed: {message}", error=message, runtime={"finish": {"command_failed": True, "returncode": result.returncode}})
        return _finish_result(failed)

    output = {"path": _relative(target), "format": settings["output_format"], "source": "voice_finish_ffmpeg", "size_bytes": target.stat().st_size}
    completed = registry.mark_completed(job_id, surface="voice", message="Voice Finish completed and saved a non-destructive Neo-owned child output.", outputs=[output], runtime={"finish": {"completed_at": _now(), "command_engine": "ffmpeg", "filters": filters, "source_path": _relative(source_path)}}, progress={"percent": 100, "stage": "completed", "label": "Voice Finish completed"})
    metadata_file = _write_metadata(completed)
    completed = registry.upsert(job_id, surface="voice", updates={"outputs": [{**output, "metadata_file": metadata_file}]})
    log_surface_event("voice", "voice.finish.completed", run_id=job_id, payload={"phase": VOICE_FINISH_PHASE, "source_job_id": source_job_id, "operations": settings, "output": output["path"]})
    return _finish_result(completed)


def _probe_duration(path: Path, ffprobe: str, *, timeout: float = 12.0) -> float:
    result = _run_command([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(_error_text(result))
    try:
        duration = float((result.stdout or "").strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("FFprobe did not return a valid audio duration.") from exc
    if duration <= 0:
        raise RuntimeError("Audio duration must be greater than zero.")
    return duration


def split_voice_finish_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source_job_id = str(data.get("source_job_id") or data.get("job_id") or "").strip()
    try:
        source_record, source_path = _resolve_source_job(source_job_id)
    except (FileNotFoundError, ValueError) as exc:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_source", "message": str(exc), "job_id": "", "outputs": []}
    capabilities = voice_finish_capabilities_payload()
    split_cap = (capabilities.get("operations") or {}).get("split") if isinstance(capabilities.get("operations"), dict) else {}
    if not capabilities.get("ready") or not bool((split_cap or {}).get("available")):
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "split_unavailable", "message": "Voice Finish split requires FFmpeg and FFprobe.", "job_id": "", "outputs": []}
    try:
        parts = max(2, min(int(data.get("parts") or data.get("chunk_count") or 2), int((split_cap or {}).get("max_parts") or 50)))
    except (TypeError, ValueError):
        parts = 2
    output_format = str(data.get("output_format") or "wav").lower().strip().lstrip(".")
    if output_format not in _SUPPORTED_OUTPUT_FORMATS:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_finish_settings", "message": "Split output format must be WAV or MP3.", "job_id": "", "outputs": []}
    ffmpeg = str((capabilities.get("engine") or {}).get("ffmpeg_path") or "")
    ffprobe = str((capabilities.get("engine") or {}).get("ffprobe_path") or "")
    try:
        duration = _probe_duration(source_path, ffprobe)
    except Exception as exc:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "probe_failed", "message": f"Could not inspect Voice source duration: {exc}", "job_id": "", "outputs": []}
    settings = {"parts": parts, "output_format": output_format, "duration_seconds": round(duration, 3)}
    record = _register_job(mode="voice_finish_split", source_records=[source_record], settings=settings, operation="split")
    registry = get_generation_job_registry()
    job_id = str(record.get("job_id") or "")
    registry.mark_running(job_id, surface="voice", message="Splitting Voice output.", progress={"percent": 10, "stage": "splitting", "label": "Splitting Voice output"})
    segment = duration / parts
    outputs: list[dict[str, Any]] = []
    try:
        for index in range(parts):
            start = segment * index
            length = duration - start if index == parts - 1 else segment
            target = _finish_output_path(job_id, output_format=output_format, label=f"part_{index + 1:03d}")
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.6f}", "-i", str(source_path), "-t", f"{max(0.001, length):.6f}", "-vn", *_audio_codec_args(output_format), str(target)]
            result = _run_command(command, timeout=float(data.get("timeout_seconds") or 240.0))
            if result.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
                raise RuntimeError(_error_text(result))
            outputs.append({"path": _relative(target), "format": output_format, "source": "voice_finish_split_ffmpeg", "part_index": index + 1, "part_count": parts, "size_bytes": target.stat().st_size})
            registry.mark_running(job_id, surface="voice", message=f"Split part {index + 1}/{parts} completed.", progress={"percent": min(95, int(((index + 1) / parts) * 90) + 5), "stage": "splitting", "label": f"Split {index + 1}/{parts}"})
    except Exception as exc:
        failed = registry.mark_failed(job_id, surface="voice", message=f"Voice Finish split failed: {exc}", error=str(exc), runtime={"finish": {"partial_outputs": outputs}})
        return _finish_result(failed)
    completed = registry.mark_completed(job_id, surface="voice", message=f"Voice Finish split completed with {len(outputs)} Neo-owned child files.", outputs=outputs, runtime={"finish": {"completed_at": _now(), "source_path": _relative(source_path), "parts": parts}}, progress={"percent": 100, "stage": "completed", "label": "Voice split completed"})
    metadata_file = _write_metadata(completed)
    completed = registry.upsert(job_id, surface="voice", updates={"outputs": [{**item, "metadata_file": metadata_file} for item in outputs]})
    log_surface_event("voice", "voice.finish.split.completed", run_id=job_id, payload={"phase": VOICE_FINISH_PHASE, "source_job_id": source_job_id, "parts": parts})
    return _finish_result(completed)


def merge_voice_finish_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    raw_ids = data.get("source_job_ids") if isinstance(data.get("source_job_ids"), list) else []
    source_job_ids = list(dict.fromkeys(str(item or "").strip() for item in raw_ids if str(item or "").strip()))
    if len(source_job_ids) < 2:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_source", "message": "Select at least two completed Voice results to merge.", "job_id": "", "outputs": []}
    capabilities = voice_finish_capabilities_payload()
    merge_cap = (capabilities.get("operations") or {}).get("merge") if isinstance(capabilities.get("operations"), dict) else {}
    max_sources = int((merge_cap or {}).get("max_sources") or 20)
    if len(source_job_ids) > max_sources:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_source", "message": f"Voice Finish merge supports at most {max_sources} sources.", "job_id": "", "outputs": []}
    if not capabilities.get("ready") or not bool((merge_cap or {}).get("available")):
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "merge_unavailable", "message": "Voice Finish merge requires an FFmpeg build with the concat audio filter.", "job_id": "", "outputs": []}
    records: list[dict[str, Any]] = []
    paths: list[Path] = []
    try:
        for job_id in source_job_ids:
            record, path = _resolve_source_job(job_id)
            records.append(record)
            paths.append(path)
    except (FileNotFoundError, ValueError) as exc:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_source", "message": str(exc), "job_id": "", "outputs": []}
    output_format = str(data.get("output_format") or "wav").lower().strip().lstrip(".")
    if output_format not in _SUPPORTED_OUTPUT_FORMATS:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "invalid_finish_settings", "message": "Merge output format must be WAV or MP3.", "job_id": "", "outputs": []}
    settings = {"source_job_ids": source_job_ids, "output_format": output_format}
    record = _register_job(mode="voice_finish_merge", source_records=records, settings=settings, operation="merge")
    registry = get_generation_job_registry()
    job_id = str(record.get("job_id") or "")
    registry.mark_running(job_id, surface="voice", message="Merging Voice outputs.", progress={"percent": 20, "stage": "merging", "label": "Merging Voice outputs"})
    target = _finish_output_path(job_id, output_format=output_format, label="merged")
    ffmpeg = str((capabilities.get("engine") or {}).get("ffmpeg_path") or "")
    command: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    normalizers = []
    labels = []
    for idx in range(len(paths)):
        normalizers.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{idx}]")
        labels.append(f"[a{idx}]")
    filter_complex = ";".join(normalizers + [f"{''.join(labels)}concat=n={len(paths)}:v=0:a=1[outa]"])
    command += ["-filter_complex", filter_complex, "-map", "[outa]", *_audio_codec_args(output_format), str(target)]
    try:
        result = _run_command(command, timeout=float(data.get("timeout_seconds") or 300.0))
    except Exception as exc:
        failed = registry.mark_failed(job_id, surface="voice", message=f"Voice Finish merge failed: {exc}", error=str(exc))
        return _finish_result(failed)
    if result.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        message = _error_text(result)
        failed = registry.mark_failed(job_id, surface="voice", message=f"Voice Finish merge failed: {message}", error=message)
        return _finish_result(failed)
    output = {"path": _relative(target), "format": output_format, "source": "voice_finish_merge_ffmpeg", "size_bytes": target.stat().st_size}
    completed = registry.mark_completed(job_id, surface="voice", message=f"Merged {len(paths)} Voice outputs into one Neo-owned child output.", outputs=[output], runtime={"finish": {"completed_at": _now(), "source_paths": [_relative(path) for path in paths]}}, progress={"percent": 100, "stage": "completed", "label": "Voice merge completed"})
    metadata_file = _write_metadata(completed)
    completed = registry.upsert(job_id, surface="voice", updates={"outputs": [{**output, "metadata_file": metadata_file}]})
    log_surface_event("voice", "voice.finish.merge.completed", run_id=job_id, payload={"phase": VOICE_FINISH_PHASE, "source_job_ids": source_job_ids, "output": output["path"]})
    return _finish_result(completed)


def voice_finish_job_payload(job_id: str) -> dict[str, Any]:
    record = _registry_record(job_id)
    if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_FINISH_MODES:
        return {"schema_id": VOICE_FINISH_JOB_SCHEMA, "phase": VOICE_FINISH_PHASE, "ok": False, "status": "missing", "job_id": job_id, "outputs": []}
    return _finish_result(record)


def voice_finish_history_payload(limit: int = 50, *, status: str | None = None) -> dict[str, Any]:
    registry = get_generation_job_registry()
    requested = max(1, min(int(limit or 50), 200))
    listing = registry.list_recent(surface="voice", limit=300)
    items: list[dict[str, Any]] = []
    for summary in listing.get("items") or []:
        if not isinstance(summary, dict):
            continue
        record = registry.get(summary.get("job_id"), surface="voice")
        if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_FINISH_MODES:
            continue
        if status and str(record.get("status") or "").lower() != str(status).lower():
            continue
        runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
        finish = runtime.get("finish") if isinstance(runtime.get("finish"), dict) else {}
        outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
        items.append({
            "job_id": str(record.get("job_id") or ""),
            "mode": str(record.get("mode") or ""),
            "status": str(record.get("status") or ""),
            "message": str(record.get("message") or ""),
            "created_at": str(record.get("created_at") or ""),
            "completed_at": str(record.get("completed_at") or ""),
            "operation": str(finish.get("operation") or ""),
            "source_job_id": str(finish.get("source_job_id") or ""),
            "source_job_ids": list(finish.get("source_job_ids") or []),
            "settings": finish.get("settings") if isinstance(finish.get("settings"), dict) else {},
            "outputs": outputs,
            "output_count": len(outputs),
            "terminal": str(record.get("status") or "").lower() in _TERMINAL,
        })
        if len(items) >= requested:
            break
    return {
        "schema_id": VOICE_FINISH_HISTORY_SCHEMA,
        "phase": VOICE_FINISH_PHASE,
        "surface": "voice",
        "authority": "shared_generation_job_registry",
        "count": len(items),
        "items": items,
        "filters": {"status": status or ""},
    }
