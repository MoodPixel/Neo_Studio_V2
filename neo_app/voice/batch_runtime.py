from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import csv
import io
import json
import re

from neo_app.runtime.job_registry import get_generation_job_registry
from neo_app.services.runtime_debug_logs import log_surface_event

from .adapter_client import voice_provider_routing_payload
from .base_contract import VOICE_COMMON_DEFAULTS, normalize_voice_common_settings
from .dialogue_runtime import generate_voice_dialogue_payload, poll_voice_dialogue_payload, voice_dialogue_capabilities_payload
from .generation_runtime import generate_voice_payload, poll_voice_generation_payload
from .output_paths import ROOT_DIR, get_voice_output_paths, sanitize_path_part
from .profile_assets import apply_voice_profile_asset_payload
from .reference_clone_runtime import current_reference_payload, generate_voice_clone_payload, poll_voice_clone_payload

VOICE_BATCH_PHASE = "VO-R11"
VOICE_BATCH_SCHEMA = "neo.voice.batch_runtime.v1"
VOICE_BATCH_ITEM_SCHEMA = "neo.voice.batch_item.v1"
VOICE_BATCH_HISTORY_SCHEMA = "neo.voice.batch_history.v1"
VOICE_BATCH_MODE = "voice_batch"
VOICE_BATCH_MAX_ITEMS = 200
VOICE_BATCH_MAX_SOURCE_CHARS = 1_000_000
VOICE_BATCH_MAX_SCRIPT_CHARS = 100_000
VOICE_BATCH_MAX_CONCURRENCY = 4
VOICE_BATCH_IMPORT_TYPES = ("txt", "md", "csv", "json", "srt")

_HISTORY_FILE = ROOT_DIR / "neo_data" / "outputs" / "voice" / "batch" / "voice_batches.r11.json"
_TERMINAL = {"completed", "failed", "cancelled", "canceled", "missing"}
_RUNNING = {"queued", "running", "pending", "submitted", "accepted", "processing", "in_progress"}
_ITEM_TERMINAL = {"completed", "failed", "cancelled", "canceled", "skipped_empty_script"}
_ITEM_COMMON_FIELDS = {
    "language", "model_id", "voice_id", "speaking_rate", "output_format",
    "split_long_text", "max_chunk_chars", "punctuation_cleanup",
}


class VoiceBatchRuntimeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _batch_dir() -> Path:
    out = get_voice_output_paths("batch", create=True)
    return Path(out.output_dir)


def _manifest_path(batch_id: str) -> Path:
    return _batch_dir() / f"{sanitize_path_part(batch_id, 'voice_batch')}.batch.r11.json"


def _read_history() -> list[dict[str, Any]]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _write_history(items: list[dict[str, Any]]) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(items[-200:], indent=2, ensure_ascii=False), encoding="utf-8")


def _store_manifest(batch: dict[str, Any]) -> dict[str, Any]:
    batch = dict(batch)
    batch["updated_at"] = _now()
    path = _manifest_path(str(batch.get("batch_id") or "voice_batch"))
    batch["manifest_file"] = _relative(path)
    path.write_text(json.dumps(batch, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    history = [item for item in _read_history() if item.get("batch_id") != batch.get("batch_id")]
    history.append({
        "schema_id": VOICE_BATCH_HISTORY_SCHEMA,
        "batch_id": batch.get("batch_id"),
        "name": batch.get("name"),
        "status": batch.get("status"),
        "profile_id": batch.get("profile_id"),
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "item_count": len(batch.get("items") or []),
        "completed_count": batch.get("summary", {}).get("completed_count", 0),
        "failed_count": batch.get("summary", {}).get("failed_count", 0),
        "manifest_file": batch.get("manifest_file"),
    })
    _write_history(history)
    return batch


def _load_manifest(batch_id: str) -> dict[str, Any] | None:
    path = _manifest_path(batch_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _detect_format(filename: str, declared: str) -> str:
    raw = str(declared or "").strip().lower().lstrip(".")
    if raw == "markdown":
        raw = "md"
    if raw in VOICE_BATCH_IMPORT_TYPES:
        return raw
    suffix = Path(str(filename or "")).suffix.lower().lstrip(".")
    return suffix if suffix in VOICE_BATCH_IMPORT_TYPES else "txt"


def _normalize_mode(value: Any, default: str = "tts") -> str:
    raw = str(value or default or "tts").strip().lower()
    aliases = {
        "render": "tts", "preview": "tts", "generate": "tts", "speech": "tts",
        "clone": "voice_clone", "reference_clone": "voice_clone",
        "dialogue": "voice_dialogue", "multi_speaker": "voice_dialogue", "multispeaker": "voice_dialogue",
    }
    raw = aliases.get(raw, raw)
    if raw not in {"tts", "voice_clone", "voice_dialogue"}:
        raise VoiceBatchRuntimeError(f"Unsupported batch item mode '{raw}'.")
    return raw


def _safe_speaker_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _item_from_dict(raw: dict[str, Any], index: int, source_name: str, *, default_mode: str) -> dict[str, Any]:
    script = str(raw.get("script") or raw.get("text") or raw.get("body") or raw.get("content") or "").strip()
    if len(script) > VOICE_BATCH_MAX_SCRIPT_CHARS:
        raise VoiceBatchRuntimeError(f"Batch item {index + 1} exceeds {VOICE_BATCH_MAX_SCRIPT_CHARS} script characters.")
    title = str(raw.get("title") or raw.get("name") or raw.get("id") or f"Script {index + 1}").strip()[:120]
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    common_overrides: dict[str, Any] = {}
    for key in _ITEM_COMMON_FIELDS:
        if key in raw:
            common_overrides[key] = raw[key]
        elif key in params:
            common_overrides[key] = params[key]
    mode = _normalize_mode(raw.get("mode") or raw.get("job_type") or params.get("mode") or params.get("job_type"), default_mode)
    warnings: list[str] = []
    if any(key in raw or key in params for key in ("profile_id", "backend_profile_id", "provider_id", "runtime", "family")):
        warnings.append("Per-item backend/provider routing fields were ignored. VO-R11 uses one selected backend profile for the whole batch.")
    if raw.get("provider_controls") or params.get("provider_controls"):
        warnings.append("Imported provider_controls were ignored. VO-R11 accepts provider controls only from the current trusted batch submission defaults.")
    return {
        "schema_id": VOICE_BATCH_ITEM_SCHEMA,
        "item_id": f"item_{index + 1:03d}",
        "index": index,
        "title": title or f"Script {index + 1}",
        "script": script,
        "mode": mode,
        "common_overrides": common_overrides,
        "profile_asset_id": str(raw.get("profile_asset_id") or raw.get("voice_profile_asset_id") or "").strip(),
        "reference_id": str(raw.get("reference_id") or "").strip(),
        "speaker_map": _safe_speaker_map(raw.get("speaker_map") or raw.get("speaker_mapping")),
        "source_name": source_name,
        "srt_timing": str(raw.get("srt_timing") or ""),
        "char_count": len(script),
        "word_count": len(script.split()),
        "status": "imported" if script else "skipped_empty_script",
        "attempt": 0,
        "child_job_id": "",
        "message": "",
        "warnings": warnings,
    }


def _parse_source(source: dict[str, Any], *, default_mode: str, start_index: int) -> dict[str, Any]:
    filename = str(source.get("filename") or source.get("name") or "inline.txt")
    fmt = _detect_format(filename, str(source.get("format") or source.get("type") or ""))
    content = str(source.get("content") or source.get("text") or source.get("script") or "")
    if len(content) > VOICE_BATCH_MAX_SOURCE_CHARS:
        raise VoiceBatchRuntimeError(f"Batch source '{filename}' exceeds {VOICE_BATCH_MAX_SOURCE_CHARS} characters.")
    rows: list[dict[str, Any]] = []
    if fmt in {"txt", "md"}:
        rows = [{"title": Path(filename).stem or "Script", "script": content, "mode": source.get("mode") or default_mode}]
    elif fmt == "csv":
        rows = [dict(row) for row in csv.DictReader(io.StringIO(content)) if isinstance(row, dict)]
    elif fmt == "json":
        parsed = json.loads(content or "null")
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("scripts") or parsed.get("batch") or [parsed]
        if not isinstance(parsed, list):
            parsed = [parsed]
        rows = [dict(item) if isinstance(item, dict) else {"script": str(item or "")} for item in parsed]
    elif fmt == "srt":
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        for block in re.split(r"\n\s*\n", normalized) if normalized else []:
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if lines and re.fullmatch(r"\d+", lines[0]):
                lines = lines[1:]
            timing = ""
            if lines and "-->" in lines[0]:
                timing, lines = lines[0], lines[1:]
            text = " ".join(lines).strip()
            if text:
                rows.append({"title": f"SRT Cue {len(rows)+1}", "script": text, "mode": source.get("mode") or default_mode, "srt_timing": timing})
    items = [_item_from_dict(row, start_index + idx, filename, default_mode=default_mode) for idx, row in enumerate(rows)]
    return {"filename": filename, "format": fmt, "item_count": len(items), "items": items}


def _normalize_default_common(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("common_settings") if isinstance(payload.get("common_settings"), dict) else payload.get("default_common_settings") if isinstance(payload.get("default_common_settings"), dict) else {}
    merged = {**VOICE_COMMON_DEFAULTS, **raw, "script": ""}
    validation = normalize_voice_common_settings({"common_settings": merged})
    common = validation.get("common_settings") if isinstance(validation.get("common_settings"), dict) else dict(VOICE_COMMON_DEFAULTS)
    common["script"] = ""
    return common


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0) + counts.get("cancelled", 0) + counts.get("canceled", 0)
    skipped = counts.get("skipped_empty_script", 0)
    terminal = completed + failed + skipped
    total = len(items)
    return {
        "item_count": total,
        "completed_count": completed,
        "failed_count": failed,
        "skipped_count": skipped,
        "running_count": sum(counts.get(key, 0) for key in _RUNNING),
        "pending_count": counts.get("ready", 0) + counts.get("imported", 0) + counts.get("retry_pending", 0),
        "terminal_count": terminal,
        "percent": int((terminal / max(1, total)) * 100) if total else 0,
        "by_status": counts,
    }


def voice_batch_capabilities_payload(profile_id: str | None = None) -> dict[str, Any]:
    routing = voice_provider_routing_payload(profile_id=profile_id or None)
    caps = routing.get("capabilities") if isinstance(routing.get("capabilities"), dict) else {}
    health = routing.get("health") if isinstance(routing.get("health"), dict) else {}
    dialogue_caps = voice_dialogue_capabilities_payload(profile_id=profile_id or None) if routing.get("routing_ready") else {}
    tts = routing.get("routing_ready") is True and caps.get("tts") is True and health.get("reachable") is True
    clone = tts and caps.get("voice_clone") is True and caps.get("reference_audio") is True
    dialogue = tts and dialogue_caps.get("ready") is True
    reasons: list[str] = []
    if routing.get("routing_ready") is not True:
        reasons.extend(str(item) for item in routing.get("errors") or ["Selected Voice backend profile is invalid."])
    elif health.get("reachable") is not True:
        reasons.append(str(health.get("message") or "Connect the selected Voice backend before running a batch."))
    elif caps.get("tts") is not True:
        reasons.append("Selected Voice backend does not support current TTS generation.")
    return {
        "schema_id": "neo.voice.batch_capabilities.v1",
        "phase": VOICE_BATCH_PHASE,
        "surface": "voice",
        "profile_id": str((routing.get("profile") or {}).get("profile_id") or profile_id or ""),
        "provider_id": str((routing.get("profile") or {}).get("provider_id") or ""),
        "ready": bool(tts),
        "orchestrator": "neo_current_child_runtimes",
        "native_provider_batch_required": False,
        "modes": {"tts": bool(tts), "voice_clone": bool(clone), "voice_dialogue": bool(dialogue)},
        "import_types": list(VOICE_BATCH_IMPORT_TYPES),
        "max_items": VOICE_BATCH_MAX_ITEMS,
        "max_concurrency": VOICE_BATCH_MAX_CONCURRENCY,
        "reasons": reasons,
        "dialogue": dialogue_caps,
    }


def import_voice_batch_runtime_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    default_mode = _normalize_mode(data.get("default_mode") or data.get("mode") or "tts")
    sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    if not sources:
        sources = [{
            "filename": data.get("filename") or data.get("name") or "inline.txt",
            "format": data.get("format") or data.get("type") or "txt",
            "content": data.get("content") or data.get("text") or data.get("script") or "",
            "mode": default_mode,
        }]
    items: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    try:
        for source in sources:
            parsed = _parse_source(source if isinstance(source, dict) else {"content": str(source)}, default_mode=default_mode, start_index=len(items))
            items.extend(parsed.pop("items"))
            source_summaries.append(parsed)
            if len(items) > VOICE_BATCH_MAX_ITEMS:
                raise VoiceBatchRuntimeError(f"VO-R11 supports at most {VOICE_BATCH_MAX_ITEMS} items per batch.")
    except (VoiceBatchRuntimeError, ValueError, json.JSONDecodeError) as exc:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "invalid_batch_source", "message": str(exc), "items": []}
    if not items:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "empty_batch", "message": "Batch import produced no items.", "items": []}
    batch_id = f"voice_batch_{uuid4().hex[:12]}"
    concurrency = max(1, min(int(data.get("concurrency") or 1), VOICE_BATCH_MAX_CONCURRENCY))
    batch = {
        "schema_id": VOICE_BATCH_SCHEMA,
        "phase": VOICE_BATCH_PHASE,
        "surface": "voice",
        "batch_id": batch_id,
        "name": str(data.get("name") or data.get("title") or "Voice Batch").strip()[:120] or "Voice Batch",
        "status": "imported",
        "profile_id": str(data.get("profile_id") or data.get("backend_profile_id") or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "default_mode": default_mode,
        "default_common_settings": _normalize_default_common(data),
        "default_reference_id": str(data.get("reference_id") or data.get("default_reference_id") or "").strip(),
        "default_profile_asset_id": str(data.get("voice_profile_asset_id") or data.get("profile_asset_id") or data.get("default_profile_asset_id") or "").strip(),
        "default_speaker_map": _safe_speaker_map(data.get("speaker_map") or data.get("default_speaker_map")),
        "provider_controls": data.get("provider_controls") if isinstance(data.get("provider_controls"), dict) else {},
        "concurrency": concurrency,
        "import_sources": source_summaries,
        "items": items,
        "summary": _summary(items),
        "message": "Batch imported. Run it with one selected Voice backend profile; imported rows cannot override provider routing.",
    }
    batch = _store_manifest(batch)
    log_surface_event("voice", "voice.batch.imported", run_id=batch_id, payload={"phase": VOICE_BATCH_PHASE, "item_count": len(items), "default_mode": default_mode})
    return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": True, "status": "imported", "batch": batch}


def _batch_lineage(batch: dict[str, Any], item: dict[str, Any], *, parent_job_id: str) -> dict[str, Any]:
    return {
        "schema_id": "neo.voice.batch_lineage.v1",
        "phase": VOICE_BATCH_PHASE,
        "batch_id": str(batch.get("batch_id") or ""),
        "batch_name": str(batch.get("name") or ""),
        "parent_job_id": parent_job_id,
        "item_id": str(item.get("item_id") or ""),
        "item_index": int(item.get("index") or 0),
        "item_title": str(item.get("title") or ""),
        "attempt": int(item.get("attempt") or 0),
    }


def _merge_item_common(batch: dict[str, Any], item: dict[str, Any], *, profile_id: str) -> tuple[dict[str, Any], str, str]:
    common = {**VOICE_COMMON_DEFAULTS, **(batch.get("default_common_settings") or {})}
    profile_asset_id = str(item.get("profile_asset_id") or batch.get("default_profile_asset_id") or "").strip()
    reference_id = str(item.get("reference_id") or batch.get("default_reference_id") or "").strip()
    if profile_asset_id:
        applied = apply_voice_profile_asset_payload(profile_asset_id, {"backend_profile_id": profile_id})
        if applied.get("ok") is not True:
            raise VoiceBatchRuntimeError(str(applied.get("message") or f"Voice Profile Asset '{profile_asset_id}' could not be applied."))
        asset_common = applied.get("common_settings") if isinstance(applied.get("common_settings"), dict) else {}
        common.update({key: value for key, value in asset_common.items() if key != "script"})
        if not reference_id:
            reference_id = str(applied.get("reference_id") or "")
    common.update(item.get("common_overrides") if isinstance(item.get("common_overrides"), dict) else {})
    common["script"] = str(item.get("script") or "")
    validation = normalize_voice_common_settings({"common_settings": common}, require_script=True)
    if validation.get("status") != "valid":
        message = "; ".join(str(entry.get("message") or entry.get("code") or "Invalid common setting") for entry in validation.get("errors") or [])
        raise VoiceBatchRuntimeError(message or "Batch item common settings are invalid.")
    return dict(validation.get("common_settings") or {}), profile_asset_id, reference_id


def _provider_controls_for(batch: dict[str, Any], mode: str) -> dict[str, Any]:
    root = batch.get("provider_controls") if isinstance(batch.get("provider_controls"), dict) else {}
    if mode in {"tts", "voice_clone"} and isinstance(root.get(mode), dict):
        return dict(root.get(mode) or {})
    if mode == "tts" and root and not any(key in root for key in ("tts", "voice_clone")):
        return dict(root)
    if mode == "voice_dialogue":
        return {
            "tts": dict(root.get("tts") or {}) if isinstance(root.get("tts"), dict) else {},
            "voice_clone": dict(root.get("voice_clone") or {}) if isinstance(root.get("voice_clone"), dict) else {},
        }
    return {}


def _submit_item(batch: dict[str, Any], item: dict[str, Any], *, profile_id: str, parent_job_id: str) -> dict[str, Any]:
    item = dict(item)
    item["attempt"] = int(item.get("attempt") or 0) + 1
    mode = _normalize_mode(item.get("mode") or batch.get("default_mode") or "tts")
    common, profile_asset_id, reference_id = _merge_item_common(batch, item, profile_id=profile_id)
    lineage = _batch_lineage(batch, item, parent_job_id=parent_job_id)
    payload: dict[str, Any] = {
        "profile_id": profile_id,
        "common_settings": common,
        "voice_profile_asset_id": profile_asset_id,
        "batch_lineage": lineage,
    }
    if mode == "voice_clone":
        if not reference_id:
            raise VoiceBatchRuntimeError("Clone batch item needs an R6 reference_id or a Voice Profile Asset with a clone-ready reference.")
        reference = current_reference_payload(reference_id)
        ref = reference.get("reference") if isinstance(reference.get("reference"), dict) else {}
        if not ref or ref.get("clone_ready") is not True:
            raise VoiceBatchRuntimeError(f"Reference '{reference_id}' is not current R6 clone-ready.")
        payload["reference_id"] = reference_id
        payload["provider_controls"] = _provider_controls_for(batch, "voice_clone")
        child = generate_voice_clone_payload(payload)
    elif mode == "voice_dialogue":
        payload["speaker_map"] = item.get("speaker_map") if isinstance(item.get("speaker_map"), dict) and item.get("speaker_map") else batch.get("default_speaker_map") if isinstance(batch.get("default_speaker_map"), dict) else {}
        payload["provider_controls"] = _provider_controls_for(batch, "voice_dialogue")
        child = generate_voice_dialogue_payload(payload)
    else:
        payload["provider_controls"] = _provider_controls_for(batch, "tts")
        child = generate_voice_payload(payload)
    status = str(child.get("status") or "failed").lower()
    item.update({
        "mode": mode,
        "profile_asset_id": profile_asset_id,
        "reference_id": reference_id,
        "status": status if status in _TERMINAL | _RUNNING else "failed",
        "child_job_id": str(child.get("job_id") or ""),
        "message": str(child.get("message") or ""),
        "output_file": str(child.get("output_file") or ""),
        "last_submitted_at": _now(),
    })
    if not item["child_job_id"] and item["status"] not in {"completed"}:
        item["status"] = "failed"
    return item


def _poll_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    child_job_id = str(item.get("child_job_id") or "")
    if not child_job_id:
        item["status"] = "failed"
        item["message"] = item.get("message") or "Batch item has no child job ID."
        return item
    mode = str(item.get("mode") or "tts")
    child = poll_voice_clone_payload(child_job_id) if mode == "voice_clone" else poll_voice_dialogue_payload(child_job_id) if mode == "voice_dialogue" else poll_voice_generation_payload(child_job_id)
    item["status"] = str(child.get("status") or item.get("status") or "running").lower()
    item["message"] = str(child.get("message") or item.get("message") or "")
    item["output_file"] = str(child.get("output_file") or item.get("output_file") or "")
    return item


def _parent_result(record: dict[str, Any] | None, batch: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing", "message": "Voice Batch not found.", "batch": batch or {}}
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    current_batch = batch or (runtime.get("batch") if isinstance(runtime.get("batch"), dict) else {})
    summary = current_batch.get("summary") if isinstance(current_batch.get("summary"), dict) else _summary(current_batch.get("items") or [])
    return {
        "schema_id": VOICE_BATCH_SCHEMA,
        "phase": VOICE_BATCH_PHASE,
        "ok": str(record.get("status") or "") == "completed" and int(summary.get("failed_count") or 0) == 0,
        "surface": "voice",
        "job_id": str(record.get("job_id") or current_batch.get("batch_id") or ""),
        "batch_id": str(current_batch.get("batch_id") or record.get("job_id") or ""),
        "status": str(current_batch.get("status") or record.get("status") or "missing"),
        "message": str(record.get("message") or current_batch.get("message") or ""),
        "profile_id": str(record.get("profile_id") or current_batch.get("profile_id") or ""),
        "provider_id": str(record.get("provider_id") or ""),
        "progress": record.get("progress") if isinstance(record.get("progress"), dict) else {},
        "summary": summary,
        "batch": current_batch,
    }


def _save_parent_batch(record: dict[str, Any], batch: dict[str, Any], *, message: str, running: bool = True) -> dict[str, Any]:
    registry = get_generation_job_registry()
    batch["summary"] = _summary(batch.get("items") or [])
    batch["message"] = message
    batch = _store_manifest(batch)
    summary = batch["summary"]
    progress = {"percent": min(99, int(summary.get("percent") or 0)), "stage": "batch_queue", "label": f"Voice Batch {summary.get('terminal_count', 0)}/{summary.get('item_count', 0)}"}
    if running:
        return registry.mark_running(str(record.get("job_id") or batch.get("batch_id")), surface="voice", message=message, runtime={"batch": batch}, progress=progress, poll_state={"completed": summary.get("completed_count", 0), "failed": summary.get("failed_count", 0), "item_count": summary.get("item_count", 0)})
    return registry.upsert(str(record.get("job_id") or batch.get("batch_id")), surface="voice", updates={"runtime": {"batch": batch}, "message": message, "progress": progress})


def _finalize_if_terminal(record: dict[str, Any], batch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = get_generation_job_registry()
    batch["summary"] = _summary(batch.get("items") or [])
    summary = batch["summary"]
    if summary.get("terminal_count") != summary.get("item_count"):
        batch["status"] = "running"
        updated = _save_parent_batch(record, batch, message="Voice Batch is running.", running=True)
        return updated, batch
    if summary.get("completed_count", 0) == 0 and summary.get("failed_count", 0) > 0:
        batch["status"] = "failed"
        batch = _store_manifest(batch)
        failed = registry.mark_failed(str(record.get("job_id") or batch.get("batch_id")), surface="voice", message="Voice Batch failed; no item completed successfully.", error="batch_all_items_failed", runtime={"batch": batch})
        return failed, batch
    batch["status"] = "completed_with_failures" if summary.get("failed_count", 0) else "completed"
    batch = _store_manifest(batch)
    completed = registry.mark_completed(str(record.get("job_id") or batch.get("batch_id")), surface="voice", message="Voice Batch completed with item failures." if summary.get("failed_count", 0) else "Voice Batch completed.", outputs=[], runtime={"batch": batch}, progress={"percent": 100, "stage": "completed", "label": "Voice Batch completed"})
    return completed, batch


def _refresh_and_dispatch(record: dict[str, Any], batch: dict[str, Any], *, target_item_ids: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    items = [dict(item) for item in batch.get("items") or []]
    for idx, item in enumerate(items):
        if str(item.get("status") or "").lower() in _RUNNING:
            items[idx] = _poll_item(item)
    active = sum(1 for item in items if str(item.get("status") or "").lower() in _RUNNING)
    concurrency = max(1, min(int(batch.get("concurrency") or 1), VOICE_BATCH_MAX_CONCURRENCY))
    profile_id = str(record.get("profile_id") or batch.get("profile_id") or "")
    for idx, item in enumerate(items):
        if active >= concurrency:
            break
        status = str(item.get("status") or "imported").lower()
        if status not in {"imported", "ready", "retry_pending"}:
            continue
        if target_item_ids is not None and str(item.get("item_id") or "") not in target_item_ids:
            continue
        if not str(item.get("script") or "").strip():
            item["status"] = "skipped_empty_script"
            items[idx] = item
            continue
        try:
            items[idx] = _submit_item(batch, item, profile_id=profile_id, parent_job_id=str(record.get("job_id") or batch.get("batch_id") or ""))
        except Exception as exc:  # noqa: BLE001
            item["attempt"] = int(item.get("attempt") or 0) + 1
            item["status"] = "failed"
            item["message"] = str(exc)
            item["last_submitted_at"] = _now()
            items[idx] = item
        if str(items[idx].get("status") or "").lower() in _RUNNING:
            active += 1
    batch["items"] = items
    return _finalize_if_terminal(record, batch)


def run_voice_batch_payload(batch_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    batch = _load_manifest(batch_id)
    if not batch:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing_batch", "message": "Voice Batch not found.", "batch_id": batch_id}
    profile_id = str(data.get("profile_id") or data.get("backend_profile_id") or batch.get("profile_id") or "").strip()
    capabilities = voice_batch_capabilities_payload(profile_id or None)
    if capabilities.get("ready") is not True:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "batch_not_ready", "message": "; ".join(capabilities.get("reasons") or ["Voice Batch is unavailable."]), "batch_id": batch_id, "capabilities": capabilities}
    profile_id = str(capabilities.get("profile_id") or profile_id)
    if data.get("concurrency") not in (None, ""):
        batch["concurrency"] = max(1, min(int(data.get("concurrency") or 1), VOICE_BATCH_MAX_CONCURRENCY))
    if isinstance(data.get("provider_controls"), dict):
        batch["provider_controls"] = data.get("provider_controls")
    if isinstance(data.get("common_settings"), dict):
        batch["default_common_settings"] = _normalize_default_common(data)
    batch["profile_id"] = profile_id
    registry = get_generation_job_registry()
    existing = registry.get(batch_id, surface="voice")
    if isinstance(existing, dict) and str(existing.get("mode") or "") == VOICE_BATCH_MODE:
        if str(existing.get("status") or "").lower() in _TERMINAL:
            result = _parent_result(existing, batch)
            result["rerun_policy"] = "terminal_batch_is_immutable_use_retry_item_or_import_new_batch"
            result["message"] = result.get("message") or "This Voice Batch is terminal. Retry failed items individually or import a new batch."
            return result
        record = existing
    else:
        route = voice_provider_routing_payload(profile_id=profile_id)
        profile = route.get("profile") if isinstance(route.get("profile"), dict) else {}
        batch["status"] = "running"
        for item in batch.get("items") or []:
            if item.get("status") == "imported":
                item["status"] = "ready"
        record = registry.register_queued(
            job_id=batch_id, surface="voice", provider_id=str(profile.get("provider_id") or ""), profile_id=profile_id, backend_profile_id=profile_id,
            provider_job_id=batch_id, local_job_id=batch_id, backend="voice_batch_orchestrator", mode=VOICE_BATCH_MODE,
            family=str(profile.get("family") or ""), loader="current_voice_child_runtimes", model="batch",
            submitted_job={"surface": "voice", "mode": VOICE_BATCH_MODE, "profile_id": profile_id, "batch_id": batch_id, "concurrency": batch.get("concurrency") or 1},
            runtime={"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "batch": batch, "progress": {"percent": 1, "stage": "batch_queue", "label": "Voice Batch queued"}},
            output_expectations={"kind": "batch_manifest", "audio_children_in_shared_registry": True, "item_count": len(batch.get("items") or [])},
            message="Voice Batch queued.",
        )
    record, batch = _refresh_and_dispatch(record, batch)
    log_surface_event("voice", "voice.batch.run", run_id=batch_id, payload={"phase": VOICE_BATCH_PHASE, "profile_id": profile_id, "status": batch.get("status"), "item_count": len(batch.get("items") or [])})
    return _parent_result(record, batch)


def poll_voice_batch_payload(batch_id: str) -> dict[str, Any]:
    batch = _load_manifest(batch_id)
    registry = get_generation_job_registry()
    record = registry.get(batch_id, surface="voice")
    if not batch or not isinstance(record, dict) or str(record.get("mode") or "") != VOICE_BATCH_MODE:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing", "message": "Current Voice Batch job not found.", "batch_id": batch_id}
    if str(record.get("status") or "").lower() in _TERMINAL and all(str(item.get("status") or "").lower() in _ITEM_TERMINAL for item in batch.get("items") or []):
        return _parent_result(record, batch)
    record, batch = _refresh_and_dispatch(record, batch)
    return _parent_result(record, batch)


def voice_batch_runtime_payload(batch_id: str) -> dict[str, Any]:
    batch = _load_manifest(batch_id)
    if not batch:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing_batch", "batch_id": batch_id}
    record = get_generation_job_registry().get(batch_id, surface="voice")
    if isinstance(record, dict) and str(record.get("mode") or "") == VOICE_BATCH_MODE:
        return _parent_result(record, batch)
    return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": True, "status": batch.get("status") or "imported", "batch_id": batch_id, "batch": batch, "summary": batch.get("summary") or _summary(batch.get("items") or [])}


def voice_batch_runtime_history_payload(limit: int = 50) -> dict[str, Any]:
    requested = max(1, min(int(limit or 50), 200))
    items = list(reversed(_read_history()))[:requested]
    return {"schema_id": VOICE_BATCH_HISTORY_SCHEMA, "phase": VOICE_BATCH_PHASE, "surface": "voice", "count": len(items), "items": items, "authority": "r11_batch_manifests_plus_shared_child_job_registry"}


def retry_voice_batch_runtime_item_payload(batch_id: str, item_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    batch = _load_manifest(batch_id)
    if not batch:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing_batch", "batch_id": batch_id}
    items = [dict(item) for item in batch.get("items") or []]
    target = next((item for item in items if str(item.get("item_id") or "") == str(item_id or "")), None)
    if not target:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing_item", "batch_id": batch_id, "item_id": item_id}
    if str(target.get("status") or "").lower() not in {"failed", "cancelled", "canceled", "missing"}:
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "item_not_retryable", "message": "Only failed/cancelled batch items can be retried.", "batch_id": batch_id, "item_id": item_id}
    target["status"] = "retry_pending"
    target["child_job_id"] = ""
    target["message"] = "Retry queued."
    batch["items"] = [target if item.get("item_id") == target.get("item_id") else item for item in items]
    registry = get_generation_job_registry()
    record = registry.get(batch_id, surface="voice")
    if not isinstance(record, dict):
        return {"schema_id": VOICE_BATCH_SCHEMA, "phase": VOICE_BATCH_PHASE, "ok": False, "status": "missing_batch_job", "message": "Batch parent job is missing; run the batch again instead.", "batch_id": batch_id}
    record = registry.mark_running(batch_id, surface="voice", message=f"Retrying batch item {item_id}.", runtime={"batch": batch}, progress={"percent": int((batch.get("summary") or {}).get("percent") or 0), "stage": "retry", "label": f"Retrying {item_id}"})
    record, batch = _refresh_and_dispatch(record, batch, target_item_ids={str(item_id)})
    log_surface_event("voice", "voice.batch.item.retried", run_id=batch_id, payload={"phase": VOICE_BATCH_PHASE, "item_id": item_id, "status": target.get("status")})
    return _parent_result(record, batch)
