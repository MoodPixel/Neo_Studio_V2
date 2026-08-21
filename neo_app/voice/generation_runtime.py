from __future__ import annotations

import base64
import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from urllib import request
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error

from .base_contract import normalize_voice_common_settings
from .job_service import build_voice_chunk_plan
from .output_paths import ROOT_DIR, get_voice_output_paths, sanitize_path_part
from .provider_routing import VOICE_PROVIDER_DEFAULT
from .provider_controls import normalize_voice_provider_controls
from .adapter_client import voice_provider_routing_payload
from neo_app.providers.profiles import get_backend_profile

VOICE_GENERATION_RUNTIME_SCHEMA = "neo.voice.generation_runtime.v1"
VOICE_GENERATION_JOB_SCHEMA = "neo.voice.generation_job.v1"
VOICE_PROVIDER_REQUEST_SCHEMA = "neo.voice.provider_generation_request.v1"
VOICE_GENERATION_METADATA_SCHEMA = "neo.voice.generation_metadata.v1"
VOICE_GENERATION_PHASE = "VO-R4"

_PENDING_STATUSES = {"queued", "pending", "running", "processing", "in_progress", "submitted", "accepted"}
_COMPLETED_STATUSES = {"completed", "complete", "done", "finished", "success", "succeeded", "ready"}
_FAILED_STATUSES = {"failed", "error", "errored", "cancelled", "canceled", "rejected", "expired"}


class VoiceGenerationRuntimeError(RuntimeError):
    pass


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _profile_runtime_config(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    runtime = profile.get("voice_runtime") if isinstance(profile.get("voice_runtime"), dict) else {}
    return {
        "generate_path": str(runtime.get("generate_path") or "/api/voice/render").strip() or "/api/voice/render",
        "poll_path_template": str(runtime.get("poll_path_template") or "/api/voice/jobs/{provider_job_id}").strip() or "/api/voice/jobs/{provider_job_id}",
        "timeout_seconds": float(runtime.get("timeout_seconds") or ((profile.get("connection") or {}).get("timeout_seconds") if isinstance(profile.get("connection"), dict) else 30) or 30),
    }


def _provider_identity(routing: dict[str, Any]) -> tuple[str, str, str]:
    profile = routing.get("profile") if isinstance(routing.get("profile"), dict) else {}
    profile_id = str(profile.get("profile_id") or "").strip()
    provider_id = str(profile.get("provider_id") or "").strip()
    family = str(profile.get("family") or "").strip()
    return profile_id, provider_id, family


def _catalog_ids(block: Any) -> set[str]:
    if not isinstance(block, dict):
        return set()
    items = block.get("items") if isinstance(block.get("items"), list) else []
    return {str(item.get("id") or "").strip() for item in items if isinstance(item, dict) and str(item.get("id") or "").strip()}


def _resolve_catalog_selection(selection: str, block: dict[str, Any] | None, *, label: str) -> str:
    block = block if isinstance(block, dict) else {}
    selected = str(selection or VOICE_PROVIDER_DEFAULT).strip() or VOICE_PROVIDER_DEFAULT
    valid = _catalog_ids(block)
    if selected != VOICE_PROVIDER_DEFAULT and selected not in valid:
        raise VoiceGenerationRuntimeError(f"Selected {label} '{selected}' is not available for the active Voice backend profile.")
    if selected == VOICE_PROVIDER_DEFAULT:
        resolved = str(block.get("resolved_default_id") or block.get("default_id") or VOICE_PROVIDER_DEFAULT).strip()
        return resolved or VOICE_PROVIDER_DEFAULT
    return selected


def _status_from_payload(payload: dict[str, Any] | None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    for key in ("status", "state", "job_status", "phase"):
        value = str(payload.get(key) or "").strip().lower()
        if value:
            return value
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    for key in ("status", "state"):
        value = str(job.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _provider_job_id(payload: dict[str, Any] | None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    candidates = [
        payload.get("provider_job_id"), payload.get("request_id"), payload.get("job_id"), payload.get("id"),
    ]
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    candidates.extend([job.get("provider_job_id"), job.get("request_id"), job.get("job_id"), job.get("id")])
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _progress_from_payload(payload: dict[str, Any] | None, fallback: int = 15) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    progress = payload.get("progress")
    if isinstance(progress, dict):
        raw = progress.get("percent", progress.get("percentage", progress.get("value")))
        try:
            percent = max(0, min(100, int(float(raw))))
        except (TypeError, ValueError):
            percent = fallback
        return {
            "percent": percent,
            "stage": str(progress.get("stage") or progress.get("status") or "generating"),
            "label": str(progress.get("label") or progress.get("message") or "Voice generation in progress"),
        }
    for key in ("progress_percent", "percent", "percentage"):
        try:
            percent = max(0, min(100, int(float(payload.get(key)))))
            return {"percent": percent, "stage": "generating", "label": "Voice generation in progress"}
        except (TypeError, ValueError):
            continue
    return {"percent": fallback, "stage": "generating", "label": "Voice generation in progress"}


def _error_message(payload: dict[str, Any] | None, fallback: str = "Voice provider generation failed.") -> str:
    payload = payload if isinstance(payload, dict) else {}
    for key in ("error", "detail", "message", "reason"):
        value = payload.get(key)
        if isinstance(value, dict):
            for nested in ("message", "detail", "error"):
                if value.get(nested):
                    return str(value[nested])
        if value:
            return str(value)
    return fallback


def _extract_audio_base64(payload: dict[str, Any] | None) -> tuple[bytes | None, str]:
    payload = payload if isinstance(payload, dict) else {}
    candidates: list[tuple[Any, str]] = [
        (payload.get("audio_base64"), str(payload.get("format") or payload.get("output_format") or "")),
        (payload.get("base64"), str(payload.get("format") or payload.get("output_format") or "")),
    ]
    audio = payload.get("audio")
    if isinstance(audio, dict):
        candidates.extend([
            (audio.get("base64"), str(audio.get("format") or audio.get("extension") or "")),
            (audio.get("data"), str(audio.get("format") or audio.get("extension") or "")),
        ])
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([
            (data.get("audio_base64"), str(data.get("format") or "")),
            (data.get("base64"), str(data.get("format") or "")),
        ])
    for value, fmt in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        encoded = value.strip()
        if encoded.startswith("data:") and ";base64," in encoded:
            header, encoded = encoded.split(";base64,", 1)
            if not fmt and "/" in header:
                fmt = header.rsplit("/", 1)[-1]
        try:
            return base64.b64decode(encoded, validate=False), fmt
        except Exception:
            continue
    return None, ""


def _output_candidates(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    payload = payload if isinstance(payload, dict) else {}
    candidates: list[dict[str, str]] = []

    def add(path: Any = None, url: Any = None, fmt: Any = None) -> None:
        path_text = str(path or "").strip()
        url_text = str(url or "").strip()
        fmt_text = str(fmt or "").strip()
        if path_text or url_text:
            candidates.append({"path": path_text, "url": url_text, "format": fmt_text})

    add(payload.get("output_file") or payload.get("output_path") or payload.get("path"), payload.get("audio_url") or payload.get("output_url") or payload.get("url"), payload.get("format") or payload.get("output_format"))
    audio = payload.get("audio")
    if isinstance(audio, dict):
        add(audio.get("path") or audio.get("file"), audio.get("url"), audio.get("format") or audio.get("extension"))
    data = payload.get("data")
    if isinstance(data, dict):
        add(data.get("output_file") or data.get("path"), data.get("audio_url") or data.get("url"), data.get("format"))
    for key in ("outputs", "files"):
        values = payload.get(key) if isinstance(payload.get(key), list) else []
        for item in values:
            if isinstance(item, dict):
                add(item.get("path") or item.get("file"), item.get("url"), item.get("format") or item.get("extension"))
            elif item:
                text = str(item)
                add(url=text if text.startswith(("http://", "https://")) else None, path=None if text.startswith(("http://", "https://")) else text)
    return candidates


def _safe_remote_url(base_url: str, candidate: str) -> str:
    resolved = urljoin(base_url.rstrip("/") + "/", str(candidate or "").strip())
    base = urlparse(base_url)
    target = urlparse(resolved)
    if target.scheme not in {"http", "https"}:
        raise VoiceGenerationRuntimeError("Voice provider output URL must use HTTP or HTTPS.")
    if base.hostname and target.hostname and base.hostname.lower() != target.hostname.lower():
        raise VoiceGenerationRuntimeError("Voice provider output URL points to a different host and was blocked.")
    return resolved


def _download_audio(url: str, *, timeout: float) -> tuple[bytes, str]:
    req = request.Request(url, method="GET", headers={"Accept": "audio/*,application/octet-stream,*/*"})
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - URL is restricted to configured provider host.
        data = response.read()
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if not data:
        raise VoiceGenerationRuntimeError("Voice provider output URL returned an empty file.")
    ext = mimetypes.guess_extension(content_type) or ""
    return data, ext


def _normalize_extension(value: str, fallback: str = "wav") -> str:
    text = str(value or "").strip().lower().lstrip(".")
    aliases = {"mpeg": "mp3", "audio/mpeg": "mp3", "audio/wav": "wav", "wave": "wav", "x-wav": "wav"}
    text = aliases.get(text, text)
    if "/" in text:
        text = aliases.get(text, text.rsplit("/", 1)[-1])
    return text if text in {"wav", "mp3", "flac", "ogg", "m4a", "aac", "webm"} else fallback


def _write_metadata(job_id: str, payload: dict[str, Any]) -> str:
    paths = get_voice_output_paths("metadata", create=True)
    is_clone = str(payload.get("schema_id") or "").startswith("neo.voice.clone_metadata") or str(payload.get("mode") or "").strip().lower() == "voice_clone"
    suffix = "clone.r6.json" if is_clone else "generation.r4.json"
    path = paths.output_file(f"{sanitize_path_part(job_id, 'voice_generation')}.{suffix}")
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _persist_audio_output(
    *,
    job_id: str,
    provider_payload: dict[str, Any] | None,
    direct_audio: bytes | None,
    direct_format: str,
    base_url: str,
    timeout: float,
    requested_format: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}
    render_paths = get_voice_output_paths("render", create=True)
    audio_bytes = direct_audio
    fmt = direct_format
    source = "provider_http_audio"

    if audio_bytes is None:
        audio_bytes, base64_format = _extract_audio_base64(provider_payload)
        if audio_bytes is not None:
            fmt = base64_format or fmt
            source = "provider_base64"

    source_path: Path | None = None
    if audio_bytes is None:
        for candidate in _output_candidates(provider_payload):
            raw_path = str(candidate.get("path") or "").strip()
            raw_url = str(candidate.get("url") or "").strip()
            fmt = candidate.get("format") or fmt
            if raw_path:
                local = Path(raw_path)
                if not local.is_absolute():
                    local = (ROOT_DIR / local).resolve()
                if local.exists() and local.is_file():
                    source_path = local
                    source = "provider_local_path"
                    if not fmt:
                        fmt = local.suffix.lstrip(".")
                    break
                if raw_path.startswith(("http://", "https://", "/")):
                    raw_url = raw_path
            if raw_url:
                safe_url = _safe_remote_url(base_url, raw_url)
                audio_bytes, url_ext = _download_audio(safe_url, timeout=timeout)
                fmt = fmt or url_ext.lstrip(".")
                source = "provider_output_url"
                break

    if audio_bytes is None and source_path is None:
        return []

    extension = _normalize_extension(fmt, fallback=_normalize_extension(requested_format, "wav"))
    target = render_paths.output_file(f"{sanitize_path_part(job_id, 'voice_generation')}.{extension}")
    if source_path is not None:
        shutil.copy2(source_path, target)
    else:
        target.write_bytes(audio_bytes or b"")
    if not target.exists() or target.stat().st_size <= 0:
        raise VoiceGenerationRuntimeError("Voice provider audio could not be persisted into Neo-owned storage.")

    try:
        relative = target.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        relative = target.as_posix()
    metadata_file = _write_metadata(job_id, {**metadata, "output_file": relative, "output_source": source})
    return [{
        "kind": "voice_audio",
        "format": extension,
        "path": relative,
        "source": source,
        "playback_endpoint": "/api/voice/output-file",
        "metadata_file": metadata_file,
    }]


def _provider_request_body(*, job_id: str, common: dict[str, Any], provider_controls: dict[str, Any], resolved_model: str, resolved_voice: str, profile_id: str, provider_id: str, family: str) -> dict[str, Any]:
    chunk_plan = build_voice_chunk_plan(common.get("script") or "", max_chars=int(common.get("max_chunk_chars") or 650)) if common.get("split_long_text") else None
    return {
        "schema_id": VOICE_PROVIDER_REQUEST_SCHEMA,
        "phase": VOICE_GENERATION_PHASE,
        "surface": "voice",
        "mode": "tts",
        "job_id": job_id,
        "profile_id": profile_id,
        "provider_id": provider_id,
        "family": family,
        "script": common.get("script") or "",
        "script_body": common.get("script") or "",
        "text": common.get("script") or "",
        "language": common.get("language") or "en",
        "model_id": resolved_model,
        "model": resolved_model,
        "voice_id": resolved_voice,
        "voice": resolved_voice,
        "speaking_rate": common.get("speaking_rate", 1.0),
        "output_format": common.get("output_format") or "wav",
        "split_long_text": bool(common.get("split_long_text")),
        "max_chunk_chars": int(common.get("max_chunk_chars") or 650),
        "punctuation_cleanup": bool(common.get("punctuation_cleanup", True)),
        "chunk_plan": chunk_plan,
        "provider_controls": provider_controls,
        "params": {
            "speaking_rate": common.get("speaking_rate", 1.0),
            "output_format": common.get("output_format") or "wav",
            "split_long_text": bool(common.get("split_long_text")),
            "max_chunk_chars": int(common.get("max_chunk_chars") or 650),
            "punctuation_cleanup": bool(common.get("punctuation_cleanup", True)),
            "provider_controls": provider_controls,
        },
    }


def _http_provider_request(profile: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    if not base_url:
        raise VoiceGenerationRuntimeError("Voice backend base URL is not configured.")
    runtime = _profile_runtime_config(profile)
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json,audio/*,application/octet-stream"}, method="POST")
    with request.urlopen(req, timeout=float(runtime["timeout_seconds"])) as response:  # noqa: S310 - configured local/provider endpoint.
        raw = response.read()
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("audio/") or content_type == "application/octet-stream":
        return {"ok": True, "kind": "audio", "audio_bytes": raw, "content_type": content_type, "status": "completed"}
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        body = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise VoiceGenerationRuntimeError(f"Voice backend returned a non-JSON response ({content_type or 'unknown content type'}).") from exc
    return {"ok": True, "kind": "json", "payload": body if isinstance(body, dict) else {"data": body}, "content_type": content_type}


def submit_voice_generation_request(profile: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    runtime = _profile_runtime_config(profile)
    return _http_provider_request(profile, runtime["generate_path"], payload)


def poll_voice_generation_request(profile: dict[str, Any], provider_job_id: str) -> dict[str, Any]:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    if not base_url:
        raise VoiceGenerationRuntimeError("Voice backend base URL is not configured.")
    runtime = _profile_runtime_config(profile)
    path = runtime["poll_path_template"].replace("{provider_job_id}", str(provider_job_id)).replace("{job_id}", str(provider_job_id))
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    req = request.Request(url, headers={"Accept": "application/json,audio/*,application/octet-stream"}, method="GET")
    with request.urlopen(req, timeout=float(runtime["timeout_seconds"])) as response:  # noqa: S310 - configured local/provider endpoint.
        raw = response.read()
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("audio/") or content_type == "application/octet-stream":
        return {"ok": True, "kind": "audio", "audio_bytes": raw, "content_type": content_type, "status": "completed"}
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        body = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise VoiceGenerationRuntimeError(f"Voice backend poll returned a non-JSON response ({content_type or 'unknown content type'}).") from exc
    return {"ok": True, "kind": "json", "payload": body if isinstance(body, dict) else {"data": body}, "content_type": content_type}


def _result_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    return {
        "schema_id": VOICE_GENERATION_RUNTIME_SCHEMA,
        "phase": VOICE_GENERATION_PHASE,
        "ok": str(record.get("status") or "") == "completed",
        "surface": "voice",
        "job_id": record.get("job_id") or "",
        "status": record.get("status") or "missing",
        "message": record.get("message") or "",
        "profile_id": record.get("profile_id") or "",
        "provider_id": record.get("provider_id") or "",
        "provider_job_id": record.get("provider_job_id") or "",
        "progress": record.get("progress") if isinstance(record.get("progress"), dict) else runtime.get("progress") if isinstance(runtime.get("progress"), dict) else {},
        "outputs": outputs,
        "output_file": outputs[0].get("path") if outputs and isinstance(outputs[0], dict) else "",
        "runtime": runtime,
        "error": record.get("error") or "",
    }


def generate_voice_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    requested_profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or "").strip()
    routing = voice_provider_routing_payload(requested_profile_id or None)
    if routing.get("routing_ready") is not True:
        return {
            "schema_id": VOICE_GENERATION_RUNTIME_SCHEMA,
            "phase": VOICE_GENERATION_PHASE,
            "ok": False,
            "status": "invalid_profile",
            "message": str((routing.get("errors") or ["Voice provider routing is unavailable."])[0]),
            "profile_id": requested_profile_id,
            "outputs": [],
        }

    profile_id, provider_id, family = _provider_identity(routing)
    validation = normalize_voice_common_settings(data, require_script=True)
    if validation.get("status") != "valid":
        message = "; ".join(str(item.get("message") or item.get("code") or "Invalid Voice setting") for item in validation.get("errors") or []) or "Voice common settings are invalid."
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "invalid_common_settings", "message": message, "profile_id": profile_id, "provider_id": provider_id, "validation": validation, "outputs": []}

    capabilities = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    if capabilities.get("tts") is not True:
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "tts_not_supported", "message": "The selected Voice backend profile does not advertise TTS generation capability.", "profile_id": profile_id, "provider_id": provider_id, "outputs": []}
    health = routing.get("health") if isinstance(routing.get("health"), dict) else {}
    if health.get("reachable") is not True:
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "blocked_backend_not_connected", "message": str(health.get("message") or "Connect the selected Voice backend before generating audio."), "profile_id": profile_id, "provider_id": provider_id, "outputs": [], "health": health}

    common = validation.get("common_settings") if isinstance(validation.get("common_settings"), dict) else {}
    try:
        resolved_model = _resolve_catalog_selection(common.get("model_id") or VOICE_PROVIDER_DEFAULT, routing.get("models"), label="model")
        resolved_voice = _resolve_catalog_selection(common.get("voice_id") or VOICE_PROVIDER_DEFAULT, routing.get("voices"), label="voice")
    except VoiceGenerationRuntimeError as exc:
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "invalid_provider_selection", "message": str(exc), "profile_id": profile_id, "provider_id": provider_id, "outputs": []}

    profile = get_backend_profile(profile_id)
    if not isinstance(profile, dict) or profile.get("enabled", True) is False:
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "invalid_profile", "message": "Selected Voice backend profile is unavailable.", "profile_id": profile_id, "provider_id": provider_id, "outputs": []}

    provider_control_validation = normalize_voice_provider_controls(profile, data.get("provider_controls"), mode="tts", model_id=resolved_model)
    if provider_control_validation.get("status") != "valid":
        message = "; ".join(item.get("message") or item.get("code") or "Invalid provider control" for item in provider_control_validation.get("errors") or [])
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": "VO-R8", "ok": False, "status": "invalid_provider_controls", "message": message or "Voice provider controls are invalid.", "profile_id": profile_id, "provider_id": provider_id, "provider_control_validation": provider_control_validation, "outputs": []}
    provider_controls = provider_control_validation.get("provider_controls") if isinstance(provider_control_validation.get("provider_controls"), dict) else {}

    profile_asset_id = str(data.get("voice_profile_asset_id") or data.get("profile_asset_id") or "").strip()
    profile_asset_lineage = None
    batch_lineage = dict(data.get("batch_lineage") or {}) if isinstance(data.get("batch_lineage"), dict) else {}
    if profile_asset_id:
        from .profile_assets import VoiceProfileAssetError, voice_profile_asset_lineage
        try:
            profile_asset_lineage = voice_profile_asset_lineage(profile_asset_id, applied_backend_profile_id=profile_id)
        except VoiceProfileAssetError as exc:
            return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "status": "missing_profile_asset", "message": str(exc), "profile_id": profile_id, "provider_id": provider_id, "outputs": []}

    job_id = f"voice_gen_{uuid4().hex[:12]}"
    provider_request = _provider_request_body(job_id=job_id, common=common, provider_controls=provider_controls, resolved_model=resolved_model, resolved_voice=resolved_voice, profile_id=profile_id, provider_id=provider_id, family=family)
    registry = get_generation_job_registry()
    runtime = {
        "schema_id": VOICE_GENERATION_JOB_SCHEMA,
        "phase": VOICE_GENERATION_PHASE,
        "route_snapshot": {"profile_id": profile_id, "provider_id": provider_id, "family": family, "model_id": resolved_model, "voice_id": resolved_voice},
        "common_settings": common,
        "provider_controls": provider_controls,
        "profile_asset": profile_asset_lineage or {},
        "batch": batch_lineage,
        "provider_request": _json_safe(provider_request),
        "progress": {"percent": 5, "stage": "queued", "label": "Queued for Voice generation"},
    }
    registry.register_queued(job_id=job_id, surface="voice", provider_id=provider_id, profile_id=profile_id, backend_profile_id=profile_id, provider_job_id=job_id, local_job_id=job_id, backend="voice_adapter", mode="tts", family=family, loader="adapter_api", model=resolved_model, submitted_job={"surface": "voice", "mode": "tts", "profile_id": profile_id, "common_settings": common, "provider_controls": provider_controls, "voice_profile_asset_id": profile_asset_id, "batch_lineage": batch_lineage}, runtime=runtime, output_expectations={"kind": "audio", "neo_owned_copy_required": True, "format": common.get("output_format") or "wav"}, message="Voice generation queued.")
    log_surface_event("voice", "voice.generation.queued", run_id=job_id, payload={"phase": VOICE_GENERATION_PHASE, "profile_id": profile_id, "provider_id": provider_id, "model_id": resolved_model, "voice_id": resolved_voice})

    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    provider_runtime = _profile_runtime_config(profile)
    try:
        remote = submit_voice_generation_request(profile, provider_request)
        if remote.get("kind") == "audio":
            content_type = str(remote.get("content_type") or "")
            direct_format = content_type.rsplit("/", 1)[-1] if "/" in content_type else common.get("output_format") or "wav"
            outputs = _persist_audio_output(job_id=job_id, provider_payload={}, direct_audio=remote.get("audio_bytes"), direct_format=direct_format, base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata={"schema_id": VOICE_GENERATION_METADATA_SCHEMA, "phase": VOICE_GENERATION_PHASE, "job_id": job_id, "profile_id": profile_id, "provider_id": provider_id, "family": family, "model_id": resolved_model, "voice_id": resolved_voice, "common_settings": common, "provider_controls": provider_controls, "profile_asset": profile_asset_lineage or {}, "batch": batch_lineage})
            record = registry.mark_completed(job_id, surface="voice", message="Voice generation completed and audio was saved into Neo-owned storage.", outputs=outputs, runtime={"provider_response_kind": "audio"}, progress={"percent": 100, "stage": "completed", "label": "Voice generation completed"})
            log_surface_event("voice", "voice.generation.completed", run_id=job_id, payload={"phase": VOICE_GENERATION_PHASE, "output_count": len(outputs), "provider_id": provider_id})
            return _result_from_record(record)

        provider_payload = remote.get("payload") if isinstance(remote.get("payload"), dict) else {}
        provider_status = _status_from_payload(provider_payload)
        outputs = _persist_audio_output(job_id=job_id, provider_payload=provider_payload, direct_audio=None, direct_format="", base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata={"schema_id": VOICE_GENERATION_METADATA_SCHEMA, "phase": VOICE_GENERATION_PHASE, "job_id": job_id, "profile_id": profile_id, "provider_id": provider_id, "family": family, "model_id": resolved_model, "voice_id": resolved_voice, "common_settings": common, "provider_controls": provider_controls, "profile_asset": profile_asset_lineage or {}, "batch": batch_lineage, "provider_status": provider_status})
        if outputs:
            record = registry.mark_completed(job_id, surface="voice", message="Voice generation completed and provider audio was imported into Neo-owned storage.", outputs=outputs, runtime={"provider_status": provider_status or "completed", "provider_response": _json_safe(provider_payload)}, progress={"percent": 100, "stage": "completed", "label": "Voice generation completed"})
            log_surface_event("voice", "voice.generation.completed", run_id=job_id, payload={"phase": VOICE_GENERATION_PHASE, "output_count": len(outputs), "provider_id": provider_id})
            return _result_from_record(record)

        external_id = _provider_job_id(provider_payload)
        if provider_status in _FAILED_STATUSES:
            message = _error_message(provider_payload)
            record = registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload)})
            record_surface_error("voice", message, payload={"phase": VOICE_GENERATION_PHASE, "job_id": job_id, "provider_id": provider_id}, run_id=job_id)
            return _result_from_record(record)
        if external_id and (provider_status in _PENDING_STATUSES or provider_status not in _COMPLETED_STATUSES):
            record = registry.upsert(job_id, surface="voice", updates={"provider_job_id": external_id, "status": "queued" if provider_status in {"", "queued", "pending", "submitted", "accepted"} else "running", "message": "Voice generation submitted to provider.", "runtime": {**runtime, "provider_status": provider_status or "submitted", "provider_response": _json_safe(provider_payload), "progress": _progress_from_payload(provider_payload, 12)}, "progress": _progress_from_payload(provider_payload, 12)})
            return _result_from_record(record)

        message = "Voice provider completed the request but did not return retrievable audio. Neo did not create a placeholder output."
        record = registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status or "unknown", "provider_response": _json_safe(provider_payload)})
        record_surface_error("voice", message, payload={"phase": VOICE_GENERATION_PHASE, "job_id": job_id, "provider_id": provider_id}, run_id=job_id)
        return _result_from_record(record)
    except Exception as exc:  # noqa: BLE001 - runtime must normalize provider failures.
        message = f"Voice provider generation failed: {exc}"
        record = registry.mark_failed(job_id, surface="voice", message=message, error=str(exc), runtime={"error_type": exc.__class__.__name__})
        record_surface_error("voice", message, exc=exc, payload={"phase": VOICE_GENERATION_PHASE, "job_id": job_id, "provider_id": provider_id}, run_id=job_id)
        return _result_from_record(record)


def poll_voice_generation_payload(job_id: str) -> dict[str, Any]:
    registry = get_generation_job_registry()
    record = registry.get(job_id, surface="voice")
    if not record:
        return {"schema_id": VOICE_GENERATION_RUNTIME_SCHEMA, "phase": VOICE_GENERATION_PHASE, "ok": False, "job_id": job_id, "status": "missing", "message": "Voice generation job was not found.", "outputs": []}
    status = str(record.get("status") or "").lower()
    if status in {"completed", "failed", "cancelled", "canceled"}:
        return _result_from_record(record)

    profile_id = str(record.get("profile_id") or "").strip()
    profile = get_backend_profile(profile_id)
    if not isinstance(profile, dict) or profile.get("enabled", True) is False:
        failed = registry.mark_failed(job_id, surface="voice", message="Voice backend profile is unavailable while polling.", error="invalid_profile")
        return _result_from_record(failed)

    provider_job_id = str(record.get("provider_job_id") or "").strip()
    if not provider_job_id or provider_job_id == job_id:
        failed = registry.mark_failed(job_id, surface="voice", message="Voice provider did not return an asynchronous job id and no audio output was available.", error="missing_provider_job_id")
        return _result_from_record(failed)

    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    provider_runtime = _profile_runtime_config(profile)
    submitted = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
    common = submitted.get("common_settings") if isinstance(submitted.get("common_settings"), dict) else {}
    provider_controls = submitted.get("provider_controls") if isinstance(submitted.get("provider_controls"), dict) else {}
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    route = runtime.get("route_snapshot") if isinstance(runtime.get("route_snapshot"), dict) else {}
    try:
        remote = poll_voice_generation_request(profile, provider_job_id)
        if remote.get("kind") == "audio":
            content_type = str(remote.get("content_type") or "")
            direct_format = content_type.rsplit("/", 1)[-1] if "/" in content_type else common.get("output_format") or "wav"
            outputs = _persist_audio_output(job_id=job_id, provider_payload={}, direct_audio=remote.get("audio_bytes"), direct_format=direct_format, base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata={"schema_id": VOICE_GENERATION_METADATA_SCHEMA, "phase": VOICE_GENERATION_PHASE, "job_id": job_id, "profile_id": profile_id, "provider_id": record.get("provider_id") or "", "family": record.get("family") or "", "model_id": record.get("model") or "", "voice_id": route.get("voice_id") or "", "common_settings": common, "provider_controls": provider_controls, "profile_asset": runtime.get("profile_asset") if isinstance(runtime.get("profile_asset"), dict) else {}, "batch": runtime.get("batch") if isinstance(runtime.get("batch"), dict) else {}})
            completed = registry.mark_completed(job_id, surface="voice", message="Voice generation completed and audio was saved into Neo-owned storage.", outputs=outputs, runtime={"provider_status": "completed"}, progress={"percent": 100, "stage": "completed", "label": "Voice generation completed"})
            return _result_from_record(completed)

        provider_payload = remote.get("payload") if isinstance(remote.get("payload"), dict) else {}
        provider_status = _status_from_payload(provider_payload)
        outputs = _persist_audio_output(job_id=job_id, provider_payload=provider_payload, direct_audio=None, direct_format="", base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata={"schema_id": VOICE_GENERATION_METADATA_SCHEMA, "phase": VOICE_GENERATION_PHASE, "job_id": job_id, "profile_id": profile_id, "provider_id": record.get("provider_id") or "", "family": record.get("family") or "", "model_id": record.get("model") or "", "voice_id": route.get("voice_id") or "", "common_settings": common, "provider_controls": provider_controls, "profile_asset": runtime.get("profile_asset") if isinstance(runtime.get("profile_asset"), dict) else {}, "batch": runtime.get("batch") if isinstance(runtime.get("batch"), dict) else {}, "provider_status": provider_status})
        if outputs:
            completed = registry.mark_completed(job_id, surface="voice", message="Voice generation completed and provider audio was imported into Neo-owned storage.", outputs=outputs, runtime={"provider_status": provider_status or "completed", "provider_response": _json_safe(provider_payload)}, progress={"percent": 100, "stage": "completed", "label": "Voice generation completed"})
            return _result_from_record(completed)
        if provider_status in _FAILED_STATUSES:
            message = _error_message(provider_payload)
            failed = registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload)})
            return _result_from_record(failed)
        if provider_status in _COMPLETED_STATUSES:
            message = "Voice provider reported completion without retrievable audio. Neo did not create a placeholder output."
            failed = registry.mark_failed(job_id, surface="voice", message=message, error="completed_without_audio", runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload)})
            return _result_from_record(failed)

        progress = _progress_from_payload(provider_payload, int((record.get("progress") or {}).get("percent") or 15) + 5 if isinstance(record.get("progress"), dict) else 20)
        running = registry.mark_running(job_id, surface="voice", message=str(provider_payload.get("message") or "Voice generation is running."), runtime={"provider_status": provider_status or "running", "provider_response": _json_safe(provider_payload)}, progress=progress, poll_state={"provider_job_id": provider_job_id, "provider_status": provider_status or "running"})
        return _result_from_record(running)
    except Exception as exc:  # noqa: BLE001 - normalize terminal vs transient provider poll failures.
        message = f"Voice generation poll failed: {exc}"
        if isinstance(exc, HTTPError) and 400 <= int(getattr(exc, "code", 0) or 0) < 500:
            failed = registry.mark_failed(job_id, surface="voice", message=message, error=str(exc), runtime={"error_type": exc.__class__.__name__, "provider_job_id": provider_job_id})
            record_surface_error("voice", message, exc=exc, payload={"phase": VOICE_GENERATION_PHASE, "job_id": job_id, "provider_job_id": provider_job_id, "terminal_poll_error": True}, run_id=job_id)
            return _result_from_record(failed)

        previous_progress = record.get("progress") if isinstance(record.get("progress"), dict) else {}
        previous_percent = int(previous_progress.get("percent") or 15)
        previous_poll = record.get("poll_state") if isinstance(record.get("poll_state"), dict) else {}
        retry_count = int(previous_poll.get("poll_error_count") or 0) + 1
        progress = {"percent": max(10, min(95, previous_percent)), "stage": "poll_retry", "label": "Voice provider status temporarily unavailable; retrying"}
        running = registry.mark_running(
            job_id,
            surface="voice",
            message=message,
            runtime={"last_poll_error": str(exc), "last_poll_error_type": exc.__class__.__name__, "provider_job_id": provider_job_id},
            progress=progress,
            poll_state={"provider_job_id": provider_job_id, "provider_status": "poll_retry", "poll_error_count": retry_count},
        )
        record_surface_error("voice", message, exc=exc, payload={"phase": VOICE_GENERATION_PHASE, "job_id": job_id, "provider_job_id": provider_job_id, "recoverable_poll_error": True, "poll_error_count": retry_count}, run_id=job_id)
        return _result_from_record(running)


def voice_generation_jobs_payload(limit: int = 50) -> dict[str, Any]:
    registry = get_generation_job_registry()
    listing = registry.list_recent(surface="voice", limit=max(1, min(int(limit or 50), 200)))
    return {
        "schema_id": "neo.voice.generation_jobs.v1",
        "phase": VOICE_GENERATION_PHASE,
        "surface": "voice",
        "count": listing.get("count") or 0,
        "jobs": listing.get("items") or [],
    }
