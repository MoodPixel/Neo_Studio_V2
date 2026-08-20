from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.providers.profiles import get_backend_profile
from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error

from .adapter_client import voice_provider_routing_payload
from .base_contract import normalize_voice_common_settings
from .provider_controls import normalize_voice_provider_controls
from .generation_runtime import (
    VOICE_PROVIDER_DEFAULT,
    VoiceGenerationRuntimeError,
    _COMPLETED_STATUSES,
    _FAILED_STATUSES,
    _PENDING_STATUSES,
    _error_message,
    _json_safe,
    _persist_audio_output,
    _profile_runtime_config,
    _progress_from_payload,
    _provider_identity,
    _provider_job_id,
    _resolve_catalog_selection,
    _status_from_payload,
    poll_voice_generation_request,
    submit_voice_generation_request,
)
from .output_paths import ROOT_DIR, get_voice_output_paths, resolve_voice_output_file, sanitize_path_part
from .reference_audio import (
    _store_reference_record,
    analyze_reference_payload,
    reference_history_payload,
    reference_record,
    store_reference_upload,
)

VOICE_REFERENCE_PHASE = "VO-R6"
VOICE_REFERENCE_ASSET_SCHEMA = "neo.voice.reference_asset.v1"
VOICE_REFERENCE_LIST_SCHEMA = "neo.voice.references.v1"
VOICE_REFERENCE_DETAIL_SCHEMA = "neo.voice.reference_detail.v1"
VOICE_REFERENCE_ATTESTATION_SCHEMA = "neo.voice.reference_attestation.v1"
VOICE_CLONE_RUNTIME_SCHEMA = "neo.voice.clone_runtime.v1"
VOICE_CLONE_JOB_SCHEMA = "neo.voice.clone_job.v1"
VOICE_CLONE_PROVIDER_REQUEST_SCHEMA = "neo.voice.provider_clone_request.v1"
VOICE_CLONE_METADATA_SCHEMA = "neo.voice.clone_metadata.v1"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _reference_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("path") or "").strip()
    if not raw:
        return None
    try:
        path = resolve_voice_output_file(raw)
    except (FileNotFoundError, ValueError):
        return None
    reference_root = get_voice_output_paths("reference", create=True).output_dir.resolve()
    resolved = path.resolve()
    if resolved != reference_root and reference_root not in resolved.parents:
        return None
    return resolved


def _rights(record: dict[str, Any]) -> dict[str, Any]:
    block = record.get("rights_attestation") if isinstance(record.get("rights_attestation"), dict) else {}
    return {
        "schema_id": VOICE_REFERENCE_ATTESTATION_SCHEMA,
        "confirmed": block.get("confirmed") is True,
        "basis": str(block.get("basis") or "user_confirmation"),
        "confirmed_at": str(block.get("confirmed_at") or ""),
        "policy": "User confirms they are authorized to use this reference voice for cloning.",
    }


def _current_reference(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    qc = record.get("qc") if isinstance(record.get("qc"), dict) else {}
    rights = _rights(record)
    path = _reference_path(record)
    qc_status = str(qc.get("status") or record.get("status") or "unknown").strip().lower()
    qc_usable = qc_status in {"usable", "usable_with_warnings"}
    clone_ready = bool(path and rights["confirmed"] and qc_usable)
    return {
        "schema_id": VOICE_REFERENCE_ASSET_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "surface": "voice",
        "reference_id": str(record.get("reference_id") or ""),
        "label": str(record.get("label") or record.get("original_filename") or record.get("reference_id") or "Reference audio"),
        "original_filename": str(record.get("original_filename") or ""),
        "stored_filename": str(record.get("stored_filename") or (path.name if path else "")),
        "path": str(record.get("path") or (_relative(path) if path else "")),
        "playback_url": f"/api/voice/output-file?path={str(record.get('path') or '')}" if path else "",
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "status": "clone_ready" if clone_ready else ("rights_required" if path and qc_usable and not rights["confirmed"] else "not_ready"),
        "file_available": bool(path),
        "clone_ready": clone_ready,
        "rights_attestation": rights,
        "qc": qc,
        "transcript": str(qc.get("transcript") or ""),
        "legacy_schema_id": str(record.get("schema_id") or ""),
    }


async def store_current_reference_upload(
    file: Any,
    *,
    transcript: str | None = None,
    label: str | None = None,
    rights_confirmed: bool = False,
    rights_basis: str | None = None,
) -> dict[str, Any]:
    if rights_confirmed is not True:
        raise ValueError("Confirm that you are authorized to use this reference voice before uploading it for cloning.")
    record = await store_reference_upload(file, transcript=transcript, label=label)
    record["rights_attestation"] = {
        "schema_id": VOICE_REFERENCE_ATTESTATION_SCHEMA,
        "confirmed": True,
        "basis": str(rights_basis or "user_confirmation"),
        "confirmed_at": str(record.get("created_at") or ""),
        "phase": VOICE_REFERENCE_PHASE,
    }
    record["current_phase"] = VOICE_REFERENCE_PHASE
    _store_reference_record(record)
    current = _current_reference(record)
    return {
        "schema_id": VOICE_REFERENCE_DETAIL_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "ok": True,
        "status": current.get("status") if current else "not_ready",
        "reference": current,
    }


def attest_reference_payload(reference_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    if data.get("rights_confirmed") is not True:
        raise ValueError("Reference authorization confirmation is required.")
    record = reference_record(reference_id)
    if not isinstance(record, dict):
        raise FileNotFoundError(reference_id)
    if not _reference_path(record):
        raise FileNotFoundError("Reference audio file is missing from Neo-owned storage.")
    record["rights_attestation"] = {
        "schema_id": VOICE_REFERENCE_ATTESTATION_SCHEMA,
        "confirmed": True,
        "basis": str(data.get("rights_basis") or "user_confirmation"),
        "confirmed_at": str(data.get("confirmed_at") or datetime.now(timezone.utc).isoformat()),
        "phase": VOICE_REFERENCE_PHASE,
    }
    record["current_phase"] = VOICE_REFERENCE_PHASE
    _store_reference_record(record)
    current = _current_reference(record)
    return {"schema_id": VOICE_REFERENCE_DETAIL_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": True, "status": current.get("status") if current else "not_ready", "reference": current}


def analyze_current_reference_payload(reference_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    result = analyze_reference_payload({"reference_id": reference_id, "transcript": str(data.get("transcript") or "")})
    current = _current_reference(result.get("reference") if isinstance(result.get("reference"), dict) else reference_record(reference_id))
    return {
        "schema_id": VOICE_REFERENCE_DETAIL_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "ok": True,
        "status": current.get("status") if current else "not_ready",
        "reference": current,
        "qc": current.get("qc") if current else {},
    }


def current_reference_payload(reference_id: str) -> dict[str, Any]:
    current = _current_reference(reference_record(reference_id))
    if not current:
        return {"schema_id": VOICE_REFERENCE_DETAIL_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "missing_reference", "reference_id": reference_id}
    return {"schema_id": VOICE_REFERENCE_DETAIL_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": True, "status": current["status"], "reference": current}


def current_references_payload(limit: int = 50) -> dict[str, Any]:
    legacy = reference_history_payload(limit=max(1, min(int(limit or 50), 200)))
    items = []
    for record in legacy.get("references") or []:
        current = _current_reference(record if isinstance(record, dict) else None)
        if current:
            items.append(current)
    return {
        "schema_id": VOICE_REFERENCE_LIST_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "surface": "voice",
        "authority": "neo_owned_reference_store",
        "count": len(items),
        "items": items,
    }


def _clone_result(record: dict[str, Any] | None) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    return {
        "schema_id": VOICE_CLONE_RUNTIME_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "ok": str(record.get("status") or "") == "completed",
        "surface": "voice",
        "mode": "voice_clone",
        "job_id": str(record.get("job_id") or ""),
        "status": str(record.get("status") or "missing"),
        "message": str(record.get("message") or ""),
        "profile_id": str(record.get("profile_id") or ""),
        "provider_id": str(record.get("provider_id") or ""),
        "provider_job_id": str(record.get("provider_job_id") or ""),
        "progress": record.get("progress") if isinstance(record.get("progress"), dict) else runtime.get("progress") if isinstance(runtime.get("progress"), dict) else {},
        "outputs": outputs,
        "output_file": outputs[0].get("path") if outputs and isinstance(outputs[0], dict) else "",
        "reference": runtime.get("reference") if isinstance(runtime.get("reference"), dict) else {},
        "runtime": runtime,
        "error": str(record.get("error") or ""),
    }


def _clone_path(profile: dict[str, Any]) -> str:
    runtime = profile.get("voice_runtime") if isinstance(profile.get("voice_runtime"), dict) else {}
    clone = runtime.get("reference_clone") if isinstance(runtime.get("reference_clone"), dict) else {}
    return str(clone.get("clone_path") or runtime.get("clone_path") or runtime.get("generate_path") or "/api/voice/render").strip() or "/api/voice/render"


def _provider_request_body(
    *,
    job_id: str,
    common: dict[str, Any],
    provider_controls: dict[str, Any],
    resolved_model: str,
    resolved_voice: str,
    profile_id: str,
    provider_id: str,
    family: str,
    reference: dict[str, Any],
) -> dict[str, Any]:
    path = resolve_voice_output_file(str(reference.get("path") or ""))
    return {
        "schema_id": VOICE_CLONE_PROVIDER_REQUEST_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "surface": "voice",
        "mode": "voice_clone",
        "job_type": "clone",
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
        "provider_controls": provider_controls,
        "reference_id": reference.get("reference_id") or "",
        "reference_audio": {
            "reference_id": reference.get("reference_id") or "",
            "neo_path": reference.get("path") or "",
            "local_path": str(path),
            "filename": reference.get("stored_filename") or path.name,
            "transcript": reference.get("transcript") or "",
            "qc_status": (reference.get("qc") or {}).get("status") if isinstance(reference.get("qc"), dict) else "",
            "authorization_confirmed": (reference.get("rights_attestation") or {}).get("confirmed") is True if isinstance(reference.get("rights_attestation"), dict) else False,
            "transport": "neo_owned_local_path",
        },
        "voice_source": {
            "type": "reference_clone",
            "reference_id": reference.get("reference_id") or "",
            "reference_audio": str(path),
        },
        "params": {
            "speaking_rate": common.get("speaking_rate", 1.0),
            "output_format": common.get("output_format") or "wav",
            "split_long_text": bool(common.get("split_long_text")),
            "max_chunk_chars": int(common.get("max_chunk_chars") or 650),
            "punctuation_cleanup": bool(common.get("punctuation_cleanup", True)),
            "provider_controls": provider_controls,
        },
    }


def generate_voice_clone_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    requested_profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or "").strip()
    routing = voice_provider_routing_payload(requested_profile_id or None)
    if routing.get("routing_ready") is not True:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "invalid_profile", "message": str((routing.get("errors") or ["Voice provider routing is unavailable."])[0]), "profile_id": requested_profile_id, "outputs": []}

    profile_id, provider_id, family = _provider_identity(routing)
    capabilities = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    if capabilities.get("voice_clone") is not True or capabilities.get("reference_audio") is not True:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "clone_not_supported", "message": "The selected Voice backend profile does not advertise both voice cloning and reference-audio capability.", "profile_id": profile_id, "provider_id": provider_id, "outputs": []}
    health = routing.get("health") if isinstance(routing.get("health"), dict) else {}
    if health.get("reachable") is not True:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "blocked_backend_not_connected", "message": str(health.get("message") or "Connect the selected Voice backend before cloning."), "profile_id": profile_id, "provider_id": provider_id, "outputs": [], "health": health}

    validation = normalize_voice_common_settings(data, require_script=True)
    if validation.get("status") != "valid":
        message = "; ".join(str(item.get("message") or item.get("code") or "Invalid Voice setting") for item in validation.get("errors") or []) or "Voice common settings are invalid."
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "invalid_common_settings", "message": message, "profile_id": profile_id, "provider_id": provider_id, "validation": validation, "outputs": []}
    common = validation.get("common_settings") if isinstance(validation.get("common_settings"), dict) else {}

    reference_id = str(data.get("reference_id") or ((data.get("reference") or {}).get("reference_id") if isinstance(data.get("reference"), dict) else "") or "").strip()
    reference_payload = current_reference_payload(reference_id)
    reference = reference_payload.get("reference") if isinstance(reference_payload.get("reference"), dict) else None
    if not reference:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "missing_reference", "message": "Select a staged Voice reference before cloning.", "profile_id": profile_id, "provider_id": provider_id, "outputs": []}
    if reference.get("clone_ready") is not True:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "reference_not_ready", "message": "The selected reference is not clone-ready. Confirm authorization and resolve reference QC/file issues first.", "profile_id": profile_id, "provider_id": provider_id, "reference": reference, "outputs": []}

    try:
        resolved_model = _resolve_catalog_selection(common.get("model_id") or VOICE_PROVIDER_DEFAULT, routing.get("models"), label="model")
        resolved_voice = _resolve_catalog_selection(common.get("voice_id") or VOICE_PROVIDER_DEFAULT, routing.get("voices"), label="voice")
    except VoiceGenerationRuntimeError as exc:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "invalid_provider_selection", "message": str(exc), "profile_id": profile_id, "provider_id": provider_id, "reference": reference, "outputs": []}

    profile = get_backend_profile(profile_id)
    if not isinstance(profile, dict) or profile.get("enabled", True) is False:
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "invalid_profile", "message": "Selected Voice backend profile is unavailable.", "profile_id": profile_id, "provider_id": provider_id, "outputs": []}

    provider_control_validation = normalize_voice_provider_controls(profile, data.get("provider_controls"), mode="voice_clone")
    if provider_control_validation.get("status") != "valid":
        message = "; ".join(item.get("message") or item.get("code") or "Invalid provider control" for item in provider_control_validation.get("errors") or [])
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": "VO-R8", "ok": False, "status": "invalid_provider_controls", "message": message or "Voice provider controls are invalid.", "profile_id": profile_id, "provider_id": provider_id, "reference": reference, "provider_control_validation": provider_control_validation, "outputs": []}
    provider_controls = provider_control_validation.get("provider_controls") if isinstance(provider_control_validation.get("provider_controls"), dict) else {}

    profile_asset_id = str(data.get("voice_profile_asset_id") or data.get("profile_asset_id") or "").strip()
    profile_asset_lineage = None
    batch_lineage = dict(data.get("batch_lineage") or {}) if isinstance(data.get("batch_lineage"), dict) else {}
    if profile_asset_id:
        from .profile_assets import VoiceProfileAssetError, voice_profile_asset_lineage
        try:
            profile_asset_lineage = voice_profile_asset_lineage(profile_asset_id, applied_backend_profile_id=profile_id)
        except VoiceProfileAssetError as exc:
            return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "status": "missing_profile_asset", "message": str(exc), "profile_id": profile_id, "provider_id": provider_id, "reference": reference, "outputs": []}

    job_id = f"voice_clone_{uuid4().hex[:12]}"
    provider_request = _provider_request_body(job_id=job_id, common=common, provider_controls=provider_controls, resolved_model=resolved_model, resolved_voice=resolved_voice, profile_id=profile_id, provider_id=provider_id, family=family, reference=reference)
    registry = get_generation_job_registry()
    runtime = {
        "schema_id": VOICE_CLONE_JOB_SCHEMA,
        "phase": VOICE_REFERENCE_PHASE,
        "route_snapshot": {"profile_id": profile_id, "provider_id": provider_id, "family": family, "model_id": resolved_model, "voice_id": resolved_voice},
        "common_settings": common,
        "provider_controls": provider_controls,
        "reference": reference,
        "profile_asset": profile_asset_lineage or {},
        "batch": batch_lineage,
        "provider_request": _json_safe(provider_request),
        "progress": {"percent": 5, "stage": "queued", "label": "Queued for Voice clone generation"},
    }
    registry.register_queued(
        job_id=job_id,
        surface="voice",
        provider_id=provider_id,
        profile_id=profile_id,
        backend_profile_id=profile_id,
        provider_job_id=job_id,
        local_job_id=job_id,
        backend="voice_adapter",
        mode="voice_clone",
        family=family,
        loader="adapter_api",
        model=resolved_model,
        submitted_job={"surface": "voice", "mode": "voice_clone", "profile_id": profile_id, "common_settings": common, "provider_controls": provider_controls, "reference_id": reference_id, "voice_profile_asset_id": profile_asset_id, "batch_lineage": batch_lineage},
        runtime=runtime,
        output_expectations={"kind": "audio", "neo_owned_copy_required": True, "format": common.get("output_format") or "wav", "reference_id": reference_id},
        message="Voice clone generation queued.",
    )
    log_surface_event("voice", "voice.clone.queued", run_id=job_id, payload={"phase": VOICE_REFERENCE_PHASE, "profile_id": profile_id, "provider_id": provider_id, "reference_id": reference_id})

    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    provider_runtime = _profile_runtime_config(profile)
    clone_path = _clone_path(profile)
    try:
        remote = submit_voice_generation_request(profile, provider_request) if clone_path == provider_runtime["generate_path"] else _submit_clone_path(profile, clone_path, provider_request)
        return _handle_clone_provider_response(job_id=job_id, record_runtime=runtime, remote=remote, profile=profile, profile_id=profile_id, provider_id=provider_id, family=family, common=common, reference=reference, resolved_model=resolved_model, resolved_voice=resolved_voice, base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        message = f"Voice clone provider generation failed: {exc}"
        record = registry.mark_failed(job_id, surface="voice", message=message, error=str(exc), runtime={"error_type": exc.__class__.__name__, "reference": reference})
        record_surface_error("voice", message, exc=exc, payload={"phase": VOICE_REFERENCE_PHASE, "job_id": job_id, "provider_id": provider_id, "reference_id": reference_id}, run_id=job_id)
        return _clone_result(record)


def _submit_clone_path(profile: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    # Import lazily to keep the current adapter HTTP contract centralized.
    from .generation_runtime import _http_provider_request
    return _http_provider_request(profile, path, payload)


def _handle_clone_provider_response(
    *,
    job_id: str,
    record_runtime: dict[str, Any],
    remote: dict[str, Any],
    profile: dict[str, Any],
    profile_id: str,
    provider_id: str,
    family: str,
    common: dict[str, Any],
    reference: dict[str, Any],
    resolved_model: str,
    resolved_voice: str,
    base_url: str,
) -> dict[str, Any]:
    registry = get_generation_job_registry()
    provider_runtime = _profile_runtime_config(profile)
    provider_controls = record_runtime.get("provider_controls") if isinstance(record_runtime.get("provider_controls"), dict) else {}
    metadata = {"schema_id": VOICE_CLONE_METADATA_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "job_id": job_id, "profile_id": profile_id, "provider_id": provider_id, "family": family, "model_id": resolved_model, "voice_id": resolved_voice, "common_settings": common, "provider_controls": provider_controls, "reference": reference, "profile_asset": record_runtime.get("profile_asset") if isinstance(record_runtime.get("profile_asset"), dict) else {}, "batch": record_runtime.get("batch") if isinstance(record_runtime.get("batch"), dict) else {}}
    if remote.get("kind") == "audio":
        content_type = str(remote.get("content_type") or "")
        direct_format = content_type.rsplit("/", 1)[-1] if "/" in content_type else common.get("output_format") or "wav"
        outputs = _persist_audio_output(job_id=job_id, provider_payload={}, direct_audio=remote.get("audio_bytes"), direct_format=direct_format, base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata=metadata)
        record = registry.mark_completed(job_id, surface="voice", message="Voice clone completed and audio was saved into Neo-owned storage.", outputs=outputs, runtime={"provider_response_kind": "audio", "reference": reference, "phase": VOICE_REFERENCE_PHASE}, progress={"percent": 100, "stage": "completed", "label": "Voice clone completed"})
        return _clone_result(record)

    provider_payload = remote.get("payload") if isinstance(remote.get("payload"), dict) else {}
    provider_status = _status_from_payload(provider_payload)
    outputs = _persist_audio_output(job_id=job_id, provider_payload=provider_payload, direct_audio=None, direct_format="", base_url=base_url, timeout=provider_runtime["timeout_seconds"], requested_format=common.get("output_format") or "wav", metadata={**metadata, "provider_status": provider_status})
    if outputs:
        record = registry.mark_completed(job_id, surface="voice", message="Voice clone completed and provider audio was imported into Neo-owned storage.", outputs=outputs, runtime={"provider_status": provider_status or "completed", "provider_response": _json_safe(provider_payload), "reference": reference, "phase": VOICE_REFERENCE_PHASE}, progress={"percent": 100, "stage": "completed", "label": "Voice clone completed"})
        return _clone_result(record)

    external_id = _provider_job_id(provider_payload)
    if provider_status in _FAILED_STATUSES:
        message = _error_message(provider_payload)
        record = registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload), "reference": reference})
        return _clone_result(record)
    if external_id and (provider_status in _PENDING_STATUSES or provider_status not in _COMPLETED_STATUSES):
        progress = _progress_from_payload(provider_payload, 12)
        record = registry.upsert(job_id, surface="voice", updates={"provider_job_id": external_id, "status": "queued" if provider_status in {"", "queued", "pending", "submitted", "accepted"} else "running", "message": "Voice clone submitted to provider.", "runtime": {**record_runtime, "provider_status": provider_status or "submitted", "provider_response": _json_safe(provider_payload), "reference": reference, "progress": progress}, "progress": progress})
        return _clone_result(record)

    message = "Voice clone provider completed the request but did not return retrievable audio. Neo did not create a placeholder output."
    record = registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status or "unknown", "provider_response": _json_safe(provider_payload), "reference": reference})
    return _clone_result(record)


def poll_voice_clone_payload(job_id: str) -> dict[str, Any]:
    registry = get_generation_job_registry()
    record = registry.get(job_id, surface="voice")
    if not isinstance(record, dict) or str(record.get("mode") or "").lower() != "voice_clone":
        return {"schema_id": VOICE_CLONE_RUNTIME_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "ok": False, "job_id": job_id, "status": "missing", "message": "Voice clone job was not found.", "outputs": []}
    if str(record.get("status") or "").lower() in {"completed", "failed", "cancelled", "canceled"}:
        return _clone_result(record)

    profile_id = str(record.get("profile_id") or "").strip()
    profile = get_backend_profile(profile_id)
    if not isinstance(profile, dict) or profile.get("enabled", True) is False:
        return _clone_result(registry.mark_failed(job_id, surface="voice", message="Voice backend profile is unavailable while polling clone generation.", error="invalid_profile"))
    provider_job_id = str(record.get("provider_job_id") or "").strip()
    if not provider_job_id or provider_job_id == job_id:
        return _clone_result(registry.mark_failed(job_id, surface="voice", message="Voice clone provider did not return an asynchronous job id and no audio output was available.", error="missing_provider_job_id"))

    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    base_url = str(connection.get("base_url") or "").strip()
    provider_runtime = _profile_runtime_config(profile)
    submitted = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
    common = submitted.get("common_settings") if isinstance(submitted.get("common_settings"), dict) else {}
    provider_controls = submitted.get("provider_controls") if isinstance(submitted.get("provider_controls"), dict) else {}
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    route = runtime.get("route_snapshot") if isinstance(runtime.get("route_snapshot"), dict) else {}
    reference = runtime.get("reference") if isinstance(runtime.get("reference"), dict) else {}
    try:
        remote = poll_voice_generation_request(profile, provider_job_id)
        return _handle_clone_poll_response(record=record, remote=remote, profile=profile, common=common, reference=reference, route=route, base_url=base_url, timeout=provider_runtime["timeout_seconds"])
    except Exception as exc:  # noqa: BLE001
        from urllib.error import HTTPError
        message = f"Voice clone poll failed: {exc}"
        if isinstance(exc, HTTPError) and 400 <= int(getattr(exc, "code", 0) or 0) < 500:
            failed = registry.mark_failed(job_id, surface="voice", message=message, error=str(exc), runtime={"error_type": exc.__class__.__name__, "provider_job_id": provider_job_id, "reference": reference})
            return _clone_result(failed)
        previous_progress = record.get("progress") if isinstance(record.get("progress"), dict) else {}
        previous_percent = int(previous_progress.get("percent") or 15)
        previous_poll = record.get("poll_state") if isinstance(record.get("poll_state"), dict) else {}
        retry_count = int(previous_poll.get("poll_error_count") or 0) + 1
        progress = {"percent": max(10, min(95, previous_percent)), "stage": "poll_retry", "label": "Voice clone provider status temporarily unavailable; retrying"}
        running = registry.mark_running(job_id, surface="voice", message=message, runtime={"last_poll_error": str(exc), "last_poll_error_type": exc.__class__.__name__, "provider_job_id": provider_job_id, "reference": reference}, progress=progress, poll_state={"provider_job_id": provider_job_id, "provider_status": "poll_retry", "poll_error_count": retry_count})
        return _clone_result(running)


def _handle_clone_poll_response(*, record: dict[str, Any], remote: dict[str, Any], profile: dict[str, Any], common: dict[str, Any], reference: dict[str, Any], route: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    registry = get_generation_job_registry()
    job_id = str(record.get("job_id") or "")
    record_runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    provider_controls = record_runtime.get("provider_controls") if isinstance(record_runtime.get("provider_controls"), dict) else {}
    metadata = {"schema_id": VOICE_CLONE_METADATA_SCHEMA, "phase": VOICE_REFERENCE_PHASE, "job_id": job_id, "profile_id": record.get("profile_id") or "", "provider_id": record.get("provider_id") or "", "family": record.get("family") or "", "model_id": record.get("model") or "", "voice_id": route.get("voice_id") or "", "common_settings": common, "provider_controls": provider_controls, "reference": reference, "profile_asset": record_runtime.get("profile_asset") if isinstance(record_runtime.get("profile_asset"), dict) else {}, "batch": record_runtime.get("batch") if isinstance(record_runtime.get("batch"), dict) else {}}
    if remote.get("kind") == "audio":
        content_type = str(remote.get("content_type") or "")
        direct_format = content_type.rsplit("/", 1)[-1] if "/" in content_type else common.get("output_format") or "wav"
        outputs = _persist_audio_output(job_id=job_id, provider_payload={}, direct_audio=remote.get("audio_bytes"), direct_format=direct_format, base_url=base_url, timeout=timeout, requested_format=common.get("output_format") or "wav", metadata=metadata)
        return _clone_result(registry.mark_completed(job_id, surface="voice", message="Voice clone completed and audio was saved into Neo-owned storage.", outputs=outputs, runtime={"provider_status": "completed", "reference": reference, "phase": VOICE_REFERENCE_PHASE}, progress={"percent": 100, "stage": "completed", "label": "Voice clone completed"}))

    provider_payload = remote.get("payload") if isinstance(remote.get("payload"), dict) else {}
    provider_status = _status_from_payload(provider_payload)
    outputs = _persist_audio_output(job_id=job_id, provider_payload=provider_payload, direct_audio=None, direct_format="", base_url=base_url, timeout=timeout, requested_format=common.get("output_format") or "wav", metadata={**metadata, "provider_status": provider_status})
    if outputs:
        return _clone_result(registry.mark_completed(job_id, surface="voice", message="Voice clone completed and provider audio was imported into Neo-owned storage.", outputs=outputs, runtime={"provider_status": provider_status or "completed", "provider_response": _json_safe(provider_payload), "reference": reference, "phase": VOICE_REFERENCE_PHASE}, progress={"percent": 100, "stage": "completed", "label": "Voice clone completed"}))
    if provider_status in _FAILED_STATUSES:
        message = _error_message(provider_payload)
        return _clone_result(registry.mark_failed(job_id, surface="voice", message=message, error=message, runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload), "reference": reference}))
    if provider_status in _COMPLETED_STATUSES:
        message = "Voice clone provider reported completion without retrievable audio. Neo did not create a placeholder output."
        return _clone_result(registry.mark_failed(job_id, surface="voice", message=message, error="completed_without_audio", runtime={"provider_status": provider_status, "provider_response": _json_safe(provider_payload), "reference": reference}))

    previous = record.get("progress") if isinstance(record.get("progress"), dict) else {}
    progress = _progress_from_payload(provider_payload, int(previous.get("percent") or 15) + 5)
    running = registry.mark_running(job_id, surface="voice", message=str(provider_payload.get("message") or "Voice clone generation is running."), runtime={"provider_status": provider_status or "running", "provider_response": _json_safe(provider_payload), "reference": reference}, progress=progress, poll_state={"provider_job_id": str(record.get("provider_job_id") or ""), "provider_status": provider_status or "running"})
    return _clone_result(running)
