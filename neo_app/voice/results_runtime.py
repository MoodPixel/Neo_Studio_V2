from __future__ import annotations

import mimetypes
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event

from .base_contract import VOICE_COMMON_DEFAULTS, VOICE_COMMON_FIELD_IDS, normalize_voice_common_settings
from .output_paths import ROOT_DIR, resolve_voice_output_file

VOICE_RESULTS_SCHEMA = "neo.voice.results.v1"
VOICE_RESULT_DETAIL_SCHEMA = "neo.voice.result_detail.v1"
VOICE_RESULT_REPLAY_SCHEMA = "neo.voice.result_replay.v1"
VOICE_RESULT_FOLDER_SCHEMA = "neo.voice.result_folder.v1"
VOICE_RESULTS_PHASE = "VO-R11"

_TERMINAL = {"completed", "failed", "cancelled", "canceled"}
_CURRENT_RESULT_MODES = {"tts", "voice_clone", "voice_dialogue", "voice_finish", "voice_finish_split", "voice_finish_merge"}


def _record(job_id: str) -> dict[str, Any] | None:
    return get_generation_job_registry().get(job_id, surface="voice")


def _runtime(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("runtime") if isinstance(record.get("runtime"), dict) else {}


def _submitted(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}


def _provider_controls(record: dict[str, Any]) -> dict[str, Any]:
    submitted = record.get("submitted_job") if isinstance(record.get("submitted_job"), dict) else {}
    runtime = _runtime(record)
    raw = submitted.get("provider_controls") if isinstance(submitted.get("provider_controls"), dict) else runtime.get("provider_controls") if isinstance(runtime.get("provider_controls"), dict) else {}
    return dict(raw)


def _common_settings(record: dict[str, Any]) -> dict[str, Any]:
    submitted = _submitted(record)
    runtime = _runtime(record)
    raw = submitted.get("common_settings") if isinstance(submitted.get("common_settings"), dict) else runtime.get("common_settings") if isinstance(runtime.get("common_settings"), dict) else {}
    normalized = normalize_voice_common_settings({"common_settings": raw})
    return normalized.get("common_settings") if isinstance(normalized.get("common_settings"), dict) else dict(VOICE_COMMON_DEFAULTS)


def _route_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime(record)
    route = runtime.get("route_snapshot") if isinstance(runtime.get("route_snapshot"), dict) else {}
    return {
        "profile_id": str(route.get("profile_id") or record.get("profile_id") or ""),
        "provider_id": str(route.get("provider_id") or record.get("provider_id") or ""),
        "family": str(route.get("family") or record.get("family") or ""),
        "model_id": str(route.get("model_id") or record.get("model") or ""),
        "voice_id": str(route.get("voice_id") or ""),
    }


def _first_output(record: dict[str, Any]) -> dict[str, Any]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    for item in outputs:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            return dict(item)
    return {}


def _resolve_output(record: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    output = _first_output(record)
    raw_path = str(output.get("path") or "").strip()
    if not raw_path:
        return output, None
    try:
        return output, resolve_voice_output_file(raw_path)
    except (FileNotFoundError, ValueError):
        return output, None


def _file_payload(record: dict[str, Any]) -> dict[str, Any]:
    output, path = _resolve_output(record)
    raw_path = str(output.get("path") or "")
    fmt = str(output.get("format") or (path.suffix.lstrip(".") if path else "") or "").lower()
    mime = mimetypes.guess_type(path.name if path else raw_path)[0] or (f"audio/{fmt}" if fmt else "application/octet-stream")
    size = path.stat().st_size if path and path.exists() else 0
    return {
        "available": bool(path),
        "path": raw_path,
        "format": fmt,
        "mime_type": mime,
        "size_bytes": size,
        "source": str(output.get("source") or ""),
        "metadata_file": str(output.get("metadata_file") or ""),
        "playback_endpoint": "/api/voice/output-file",
        "download_endpoint": f"/api/voice/results/{record.get('job_id')}/download" if path else "",
        "folder_endpoint": f"/api/voice/results/{record.get('job_id')}/open-folder" if path else "",
    }


def _script_preview(script: str, limit: int = 220) -> str:
    text = " ".join(str(script or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _result_item(record: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    common = _common_settings(record)
    provider_controls = _provider_controls(record)
    route = _route_snapshot(record)
    file_info = _file_payload(record)
    status = str(record.get("status") or "unknown")
    progress = record.get("progress") if isinstance(record.get("progress"), dict) else {}
    runtime = _runtime(record)
    mode = str(record.get("mode") or "tts").strip().lower() or "tts"
    reference = runtime.get("reference") if isinstance(runtime.get("reference"), dict) else {}
    profile_asset = runtime.get("profile_asset") if isinstance(runtime.get("profile_asset"), dict) else {}
    finish = runtime.get("finish") if isinstance(runtime.get("finish"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    batch = runtime.get("batch") if isinstance(runtime.get("batch"), dict) else {}
    result_kind = {
        "voice_clone": "reference_clone",
        "voice_dialogue": "dialogue",
        "voice_finish": "finish",
        "voice_finish_split": "finish_split",
        "voice_finish_merge": "finish_merge",
    }.get(mode, "tts")
    item: dict[str, Any] = {
        "schema_id": VOICE_RESULT_DETAIL_SCHEMA if detailed else VOICE_RESULTS_SCHEMA,
        "phase": VOICE_RESULTS_PHASE,
        "surface": "voice",
        "job_id": str(record.get("job_id") or ""),
        "mode": mode,
        "result_kind": result_kind,
        "status": status,
        "terminal": status.lower() in _TERMINAL,
        "playable": file_info["available"] and status.lower() == "completed",
        "message": str(record.get("message") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "completed_at": str(record.get("completed_at") or ""),
        "profile_id": route["profile_id"],
        "provider_id": route["provider_id"],
        "family": route["family"],
        "model_id": route["model_id"],
        "voice_id": route["voice_id"],
        "language": common.get("language") or "en",
        "script_preview": _script_preview(common.get("script") or ""),
        "script_char_count": len(str(common.get("script") or "")),
        "output_format": (file_info["format"] if mode.startswith("voice_finish") else common.get("output_format")) or file_info["format"] or "",
        "progress": progress,
        "output": file_info,
        "reference_id": str(reference.get("reference_id") or ""),
        "voice_profile_asset_id": str(profile_asset.get("asset_id") or ""),
        "voice_profile_asset_name": str(profile_asset.get("name") or ""),
        "batch_id": str(batch.get("batch_id") or ""),
        "batch_item_id": str(batch.get("item_id") or ""),
        "batch_item_title": str(batch.get("item_title") or ""),
    }
    if detailed:
        item["inspector"] = {
            "generation": {
                "profile_id": route["profile_id"],
                "provider_id": route["provider_id"],
                "provider_job_id": str(record.get("provider_job_id") or ""),
                "family": route["family"],
                "model_id": route["model_id"],
                "voice_id": route["voice_id"],
                "status": status,
                "progress": progress,
                "created_at": str(record.get("created_at") or ""),
                "completed_at": str(record.get("completed_at") or ""),
            },
            "script": {
                "text": common.get("script") or "",
                "language": common.get("language") or "en",
                "char_count": len(str(common.get("script") or "")),
                "word_count": len(str(common.get("script") or "").split()),
            },
            "parameters": {field: common.get(field) for field in VOICE_COMMON_FIELD_IDS if field != "script"},
            "provider_controls": provider_controls,
            "profile_asset": {
                "active": bool(profile_asset.get("asset_id")),
                "asset_id": str(profile_asset.get("asset_id") or ""),
                "name": str(profile_asset.get("name") or ""),
                "source_backend_profile_id": str(profile_asset.get("source_backend_profile_id") or ""),
                "applied_backend_profile_id": str(profile_asset.get("applied_backend_profile_id") or route["profile_id"]),
                "application_mode": str(profile_asset.get("application_mode") or ""),
                "source_kind": str(profile_asset.get("source_kind") or ""),
                "reference_id": str(profile_asset.get("reference_id") or ""),
            },
            "reference": {
                "active": bool(reference.get("reference_id")),
                "reference_id": str(reference.get("reference_id") or ""),
                "label": str(reference.get("label") or ""),
                "path": str(reference.get("path") or ""),
                "qc_status": str((reference.get("qc") or {}).get("status") or "") if isinstance(reference.get("qc"), dict) else "",
                "rights_confirmed": bool((reference.get("rights_attestation") or {}).get("confirmed")) if isinstance(reference.get("rights_attestation"), dict) else False,
            },
            "output": file_info,
            "lineage": {
                "job_id": str(record.get("job_id") or ""),
                "provider_job_id": str(record.get("provider_job_id") or ""),
                "local_job_id": str(record.get("local_job_id") or ""),
                "route_snapshot": route,
                "provider_status": str(runtime.get("provider_status") or ""),
                "storage": record.get("storage") if isinstance(record.get("storage"), dict) else {},
                "source_job_id": str(finish.get("source_job_id") or ""),
                "source_job_ids": list(finish.get("source_job_ids") or []),
            },
            "dialogue": {
                "active": mode == "voice_dialogue" or bool(dialogue.get("plan")),
                "phase": str(dialogue.get("phase") or ""),
                "plan": dialogue.get("plan") if isinstance(dialogue.get("plan"), dict) else {},
                "assignments": dialogue.get("assignments") if isinstance(dialogue.get("assignments"), list) else [],
                "turn_jobs": dialogue.get("turn_jobs") if isinstance(dialogue.get("turn_jobs"), list) else [],
                "metadata_file": str(dialogue.get("metadata_file") or ""),
                "stitch_engine": str(dialogue.get("stitch_engine") or ""),
            },
            "batch": {
                "active": bool(batch.get("batch_id")),
                "phase": str(batch.get("phase") or ""),
                "batch_id": str(batch.get("batch_id") or ""),
                "batch_name": str(batch.get("batch_name") or ""),
                "parent_job_id": str(batch.get("parent_job_id") or ""),
                "item_id": str(batch.get("item_id") or ""),
                "item_index": batch.get("item_index"),
                "item_title": str(batch.get("item_title") or ""),
                "attempt": batch.get("attempt"),
                "dialogue_parent_job_id": str(batch.get("dialogue_parent_job_id") or ""),
                "dialogue_turn_id": str(batch.get("dialogue_turn_id") or ""),
            },
            "finish": {
                "active": mode.startswith("voice_finish"),
                "phase": str(finish.get("phase") or ""),
                "operation": str(finish.get("operation") or ""),
                "settings": finish.get("settings") if isinstance(finish.get("settings"), dict) else {},
                "source_job_id": str(finish.get("source_job_id") or ""),
                "source_job_ids": list(finish.get("source_job_ids") or []),
                "provider_independent": bool(finish.get("provider_independent")),
                "engine": str(finish.get("engine") or ""),
            },
            "events": record.get("events") if isinstance(record.get("events"), list) else [],
        }
        item["replay"] = build_voice_result_replay_payload(record)
    return item


def voice_results_payload(limit: int = 50, *, status: str | None = None) -> dict[str, Any]:
    registry = get_generation_job_registry()
    requested_limit = max(1, min(int(limit or 50), 200))
    # The shared Voice registry can contain historical/non-TTS modes. Read the
    # bounded Voice window first, then apply the current TTS/clone filter so a newer
    # compatibility job cannot hide an older current TTS result at small limits.
    listing = registry.list_recent(surface="voice", limit=200)
    items: list[dict[str, Any]] = []
    for summary in listing.get("items") or []:
        if not isinstance(summary, dict):
            continue
        record = registry.get(summary.get("job_id"), surface="voice")
        if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_RESULT_MODES:
            continue
        if status and str(record.get("status") or "").lower() != str(status).lower():
            continue
        items.append(_result_item(record))
        if len(items) >= requested_limit:
            break
    return {
        "schema_id": VOICE_RESULTS_SCHEMA,
        "phase": VOICE_RESULTS_PHASE,
        "surface": "voice",
        "authority": "shared_generation_job_registry",
        "count": len(items),
        "items": items,
        "filters": {"status": status or ""},
    }


def voice_result_payload(job_id: str) -> dict[str, Any]:
    record = _record(job_id)
    if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_RESULT_MODES:
        return {"schema_id": VOICE_RESULT_DETAIL_SCHEMA, "phase": VOICE_RESULTS_PHASE, "ok": False, "status": "missing_result", "job_id": job_id}
    return {"ok": True, "status": "result_ready", "result": _result_item(record, detailed=True)}


def build_voice_result_replay_payload(record: dict[str, Any]) -> dict[str, Any]:
    common = _common_settings(record)
    provider_controls = _provider_controls(record)
    route = _route_snapshot(record)
    portable = {field: common.get(field) for field in VOICE_COMMON_FIELD_IDS if field not in {"model_id", "voice_id"}}
    portable["model_id"] = "provider_default"
    portable["voice_id"] = "provider_default"
    runtime = _runtime(record)
    reference = runtime.get("reference") if isinstance(runtime.get("reference"), dict) else {}
    dialogue = runtime.get("dialogue") if isinstance(runtime.get("dialogue"), dict) else {}
    batch = runtime.get("batch") if isinstance(runtime.get("batch"), dict) else {}
    mode = str(record.get("mode") or "tts").strip().lower() or "tts"
    replay_source_mode = mode
    if mode.startswith("voice_finish"):
        finish = runtime.get("finish") if isinstance(runtime.get("finish"), dict) else {}
        source_recipe = finish.get("source_recipe") if isinstance(finish.get("source_recipe"), dict) else {}
        inherited_dialogue = source_recipe.get("dialogue") if isinstance(source_recipe.get("dialogue"), dict) else {}
        if inherited_dialogue.get("plan"):
            dialogue = inherited_dialogue
            replay_source_mode = "voice_dialogue"
        else:
            replay_source_mode = "voice_clone" if str(reference.get("reference_id") or "") else "tts"
    return {
        "schema_id": VOICE_RESULT_REPLAY_SCHEMA,
        "phase": VOICE_RESULTS_PHASE,
        "source_job_id": str(record.get("job_id") or ""),
        "source_profile_id": route["profile_id"],
        "source_mode": replay_source_mode,
        "source_result_mode": mode,
        "reference_id": str(reference.get("reference_id") or "") if replay_source_mode == "voice_clone" else "",
        "dialogue": {
            "active": replay_source_mode == "voice_dialogue",
            "plan": dialogue.get("plan") if isinstance(dialogue.get("plan"), dict) else {},
            "assignments": dialogue.get("assignments") if isinstance(dialogue.get("assignments"), list) else [],
            "speaker_map": _submitted(record).get("speaker_map") if isinstance(_submitted(record).get("speaker_map"), dict) else {},
            "replay_policy": "speaker_sources_are_revalidated_against_current_selected_backend_never_auto_switch",
        },
        "voice_profile_asset_id": str((runtime.get("profile_asset") or {}).get("asset_id") or "") if isinstance(runtime.get("profile_asset"), dict) else "",
        "batch": batch,
        "batch_replay_policy": "batch_lineage_is_inspector_only; replay_restores_the_generation_recipe_without_reopening_or_rerouting_the_batch",
        "voice_profile_asset_replay_policy": "result_lineage_only_profile_asset_is_never_auto_applied_or_used_to_switch_backend",
        "reference_replay_policy": "reference_asset_is_provider_independent_but_current_profile_must_advertise_clone_and_reference_audio",
        "exact_common_settings": common,
        "portable_common_settings": portable,
        "exact_provider_controls": provider_controls,
        "portable_provider_controls": {},
        "provider_controls_replay_policy": "restore_only_when_active_backend_profile_matches_source_profile_else_clear",
        "selection_policy": "reuse_model_and_voice_only_when_active_profile_matches_source_profile_else_provider_default",
        "profile_switch_policy": "never_auto_switch_backend_profile",
    }


def voice_result_replay_payload(job_id: str) -> dict[str, Any]:
    record = _record(job_id)
    if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_RESULT_MODES:
        return {"schema_id": VOICE_RESULT_REPLAY_SCHEMA, "phase": VOICE_RESULTS_PHASE, "ok": False, "status": "missing_result", "job_id": job_id}
    replay = build_voice_result_replay_payload(record)
    return {"ok": True, "status": "replay_ready", "replay": replay}


def voice_result_output_path(job_id: str) -> Path:
    record = _record(job_id)
    if not isinstance(record, dict) or str(record.get("mode") or "").lower() not in _CURRENT_RESULT_MODES:
        raise FileNotFoundError(job_id)
    _output, path = _resolve_output(record)
    if not path:
        raise FileNotFoundError(job_id)
    return path


def open_voice_result_folder_payload(job_id: str) -> dict[str, Any]:
    try:
        path = voice_result_output_path(job_id)
    except FileNotFoundError:
        return {"schema_id": VOICE_RESULT_FOLDER_SCHEMA, "phase": VOICE_RESULTS_PHASE, "ok": False, "status": "missing_output", "job_id": job_id}
    folder = path.parent.resolve()
    root = (ROOT_DIR / "neo_data" / "outputs" / "voice").resolve()
    if root not in folder.parents and folder != root:
        return {"schema_id": VOICE_RESULT_FOLDER_SCHEMA, "phase": VOICE_RESULTS_PHASE, "ok": False, "status": "unsafe_output_folder", "job_id": job_id}
    try:
        if platform.system() == "Windows":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        opened = True
        message = "Voice result folder opened."
    except Exception as exc:  # noqa: BLE001 - local desktop integration is best effort.
        opened = False
        message = f"Could not open folder automatically: {exc}"
    try:
        relative = folder.relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        relative = folder.as_posix()
    log_surface_event("voice", "voice.result.open_folder", run_id=job_id, payload={"phase": VOICE_RESULTS_PHASE, "opened": opened, "folder": relative})
    return {
        "schema_id": VOICE_RESULT_FOLDER_SCHEMA,
        "phase": VOICE_RESULTS_PHASE,
        "ok": opened,
        "status": "folder_opened" if opened else "folder_ready",
        "job_id": job_id,
        "folder": relative,
        "message": message,
    }
