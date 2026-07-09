from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import csv
import io
import json
import re

from .output_paths import get_voice_output_paths, sanitize_path_part

ROOT = Path(__file__).resolve().parents[2]
BATCH_SCHEMA = "neo.voice.batch_import.v13"
BATCH_ITEM_SCHEMA = "neo.voice.batch_item.v13"
BATCH_RENDER_SCHEMA = "neo.voice.batch_render.v13"
BATCH_HISTORY = ROOT / "neo_data" / "outputs" / "voice" / "history" / "voice_batches.v13.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _batch_dir(create: bool = True):
    return get_voice_output_paths("batch", create=create)


def _read_batches() -> list[dict[str, Any]]:
    if not BATCH_HISTORY.exists():
        return []
    try:
        data = json.loads(BATCH_HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_batches(batches: list[dict[str, Any]]) -> None:
    BATCH_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    BATCH_HISTORY.write_text(json.dumps(batches[-200:], indent=2), encoding="utf-8")


def _store_batch(batch: dict[str, Any]) -> dict[str, Any]:
    batches = [item for item in _read_batches() if item.get("batch_id") != batch.get("batch_id")]
    batches.append(batch)
    _write_batches(batches)
    return batch


def _manifest_path(batch_id: str) -> Path:
    return _batch_dir(create=True).output_file(f"{sanitize_path_part(batch_id, 'voice_batch')}.batch.v13.json")


def _save_manifest(batch: dict[str, Any]) -> dict[str, Any]:
    path = _manifest_path(str(batch.get("batch_id") or "voice_batch"))
    path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    batch["manifest_file"] = _relative_to_root(path)
    return batch


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _detect_format(filename: str = "", declared: str = "") -> str:
    declared = str(declared or "").strip().lower().lstrip(".")
    if declared in {"txt", "md", "markdown", "csv", "json", "srt"}:
        return "md" if declared == "markdown" else declared
    suffix = Path(str(filename or "")).suffix.lower().lstrip(".")
    if suffix in {"txt", "md", "csv", "json", "srt"}:
        return suffix
    return "txt"


def _item_from_dict(raw: dict[str, Any], index: int, source_name: str) -> dict[str, Any]:
    script = str(raw.get("script") or raw.get("text") or raw.get("body") or raw.get("content") or "").strip()
    title = str(raw.get("title") or raw.get("name") or raw.get("id") or f"Script {index + 1}").strip()
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    voice_source = raw.get("voice_source") if isinstance(raw.get("voice_source"), dict) else None
    if not voice_source and raw.get("voice_id"):
        voice_source = {"type": "built_in", "voice_id": str(raw.get("voice_id"))}
    if not voice_source and raw.get("saved_profile_id"):
        voice_source = {"type": "saved_profile", "saved_profile_id": str(raw.get("saved_profile_id"))}
    return {
        "schema_id": BATCH_ITEM_SCHEMA,
        "item_id": f"item_{index + 1:03d}",
        "index": index,
        "title": title[:120] or f"Script {index + 1}",
        "script": script,
        "language": str(raw.get("language") or params.get("language") or "en"),
        "job_type": str(raw.get("job_type") or params.get("job_type") or "render"),
        "family": str(raw.get("family") or params.get("family") or ""),
        "runtime": str(raw.get("runtime") or params.get("runtime") or ""),
        "profile_id": str(raw.get("profile_id") or params.get("profile_id") or ""),
        "voice_source": voice_source or {},
        "params": params,
        "source_name": source_name,
        "char_count": len(script),
        "word_count": len(script.split()),
        "status": "imported" if script else "empty_script",
    }


def _parse_plain(content: str, *, filename: str, fmt: str) -> list[dict[str, Any]]:
    text = str(content or "").strip()
    title = Path(filename).stem if filename else ("Markdown Script" if fmt == "md" else "Text Script")
    return [_item_from_dict({"title": title, "script": text}, 0, filename or f"inline.{fmt}")]


def _parse_csv(content: str, *, filename: str) -> list[dict[str, Any]]:
    stream = io.StringIO(str(content or ""))
    reader = csv.DictReader(stream)
    items: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if not isinstance(row, dict):
            continue
        items.append(_item_from_dict(row, index, filename or "inline.csv"))
    return items


def _parse_json(content: str, *, filename: str) -> list[dict[str, Any]]:
    raw = json.loads(str(content or "null"))
    if isinstance(raw, dict):
        candidate = raw.get("items") or raw.get("scripts") or raw.get("batch") or [raw]
    else:
        candidate = raw
    if not isinstance(candidate, list):
        candidate = [candidate]
    items: list[dict[str, Any]] = []
    for index, item in enumerate(candidate):
        if isinstance(item, dict):
            items.append(_item_from_dict(item, index, filename or "inline.json"))
        else:
            items.append(_item_from_dict({"title": f"Script {index + 1}", "script": str(item or "")}, index, filename or "inline.json"))
    return items


def _parse_srt(content: str, *, filename: str) -> list[dict[str, Any]]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", text) if text else []
    items: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if re.fullmatch(r"\d+", lines[0]):
            lines = lines[1:]
        timing = ""
        if lines and "-->" in lines[0]:
            timing = lines[0]
            lines = lines[1:]
        script = " ".join(lines).strip()
        if not script:
            continue
        index = len(items)
        item = _item_from_dict({"title": f"SRT Cue {index + 1}", "script": script}, index, filename or "inline.srt")
        item["srt_timing"] = timing
        items.append(item)
    if not items and text:
        items.append(_item_from_dict({"title": Path(filename).stem or "SRT Script", "script": text}, 0, filename or "inline.srt"))
    return items


def parse_voice_batch_source(source: dict[str, Any]) -> dict[str, Any]:
    filename = str(source.get("filename") or source.get("name") or "inline.txt")
    fmt = _detect_format(filename, str(source.get("format") or source.get("type") or ""))
    content = str(source.get("content") or source.get("text") or source.get("script") or "")
    try:
        if fmt in {"txt", "md"}:
            items = _parse_plain(content, filename=filename, fmt=fmt)
        elif fmt == "csv":
            items = _parse_csv(content, filename=filename)
        elif fmt == "json":
            items = _parse_json(content, filename=filename)
        elif fmt == "srt":
            items = _parse_srt(content, filename=filename)
        else:
            items = _parse_plain(content, filename=filename, fmt="txt")
        status = "parsed"
        error = ""
    except Exception as exc:
        items = []
        status = "parse_failed"
        error = str(exc)
    return {"filename": filename, "format": fmt, "status": status, "error": error, "item_count": len(items), "items": items}


def build_output_name(pattern: str, *, batch: dict[str, Any], item: dict[str, Any], job: dict[str, Any] | None = None) -> str:
    pattern = str(pattern or "{batch}_{index}_{title}").strip() or "{batch}_{index}_{title}"
    values = {
        "batch": sanitize_path_part(batch.get("name") or batch.get("batch_id") or "voice_batch", "voice_batch"),
        "batch_id": sanitize_path_part(batch.get("batch_id"), "voice_batch"),
        "index": f"{int(item.get('index') or 0) + 1:03d}",
        "title": sanitize_path_part(item.get("title"), "script"),
        "voice": sanitize_path_part((item.get("voice_source") or {}).get("voice_id") or (item.get("voice_source") or {}).get("saved_profile_id") or "voice", "voice"),
        "date": _now()[:10],
        "job_id": sanitize_path_part((job or {}).get("job_id"), "job"),
    }
    try:
        return sanitize_path_part(pattern.format(**values), "voice_batch_output")
    except Exception:
        return sanitize_path_part(f"{values['batch']}_{values['index']}_{values['title']}", "voice_batch_output")


def import_voice_batch_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    if not sources:
        sources = [{"filename": data.get("filename") or data.get("name") or "inline.txt", "format": data.get("format") or data.get("type") or "txt", "content": data.get("content") or data.get("text") or data.get("script") or ""}]
    parsed_sources = [parse_voice_batch_source(source if isinstance(source, dict) else {"content": str(source)}) for source in sources]
    base_items: list[dict[str, Any]] = []
    for parsed in parsed_sources:
        for item in parsed.get("items", []):
            item = dict(item)
            item["index"] = len(base_items)
            item["item_id"] = f"item_{len(base_items) + 1:03d}"
            item["output_name"] = ""
            base_items.append(item)
    default_params = data.get("default_params") if isinstance(data.get("default_params"), dict) else {}
    default_voice_source = data.get("default_voice_source") if isinstance(data.get("default_voice_source"), dict) else {}
    batch_id = f"voice_batch_{uuid4().hex[:12]}"
    batch: dict[str, Any] = {
        "schema_id": BATCH_SCHEMA,
        "surface": "voice",
        "phase": "VO-V13",
        "batch_id": batch_id,
        "name": str(data.get("name") or data.get("title") or "Voice Batch").strip() or "Voice Batch",
        "created_at": _now(),
        "updated_at": _now(),
        "status": "imported" if base_items else "empty_import",
        "import_sources": [{k: v for k, v in parsed.items() if k != "items"} for parsed in parsed_sources],
        "default_job_type": str(data.get("job_type") or default_params.get("job_type") or "render"),
        "default_params": default_params,
        "default_voice_source": default_voice_source,
        "output_naming_pattern": str(data.get("output_naming_pattern") or "{batch}_{index}_{title}"),
        "item_count": len(base_items),
        "items": base_items,
        "queue_state": {"schema_id": "neo.voice.batch_queue_state.v13", "state": "imported", "position": 0, "terminal": False, "recoverable": False},
        "rendered_jobs": [],
        "failed_items": [],
        "message": "Batch import parsed scripts into Neo-owned Voice batch items. Rendering is handled by /api/voice/batch/{batch_id}/render.",
    }
    for item in batch["items"]:
        item["params"] = {**default_params, **(item.get("params") or {})}
        if default_voice_source and not item.get("voice_source"):
            item["voice_source"] = dict(default_voice_source)
        item["output_name"] = build_output_name(batch["output_naming_pattern"], batch=batch, item=item)
    batch = _save_manifest(batch)
    _store_batch(batch)
    return {"ok": True, "status": batch["status"], "batch": batch}


def _item_render_payload(batch: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    params = {**(batch.get("default_params") or {}), **(item.get("params") or {})}
    return {
        "script_title": item.get("title") or "Batch Script",
        "script": item.get("script") or "",
        "language": item.get("language") or params.get("language") or "en",
        "family": item.get("family") or params.get("family") or "",
        "runtime": item.get("runtime") or params.get("runtime") or "",
        "profile_id": item.get("profile_id") or params.get("profile_id") or "",
        "voice_source": item.get("voice_source") or batch.get("default_voice_source") or {"type": "built_in", "voice_id": "provider_default"},
        "params": params,
        "batch_id": batch.get("batch_id"),
        "batch_item_id": item.get("item_id"),
        "output_name": item.get("output_name"),
    }


def render_voice_batch_payload(batch_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    batches = _read_batches()
    batch = next((item for item in batches if item.get("batch_id") == batch_id), None)
    if not batch:
        return {"ok": False, "status": "missing_batch", "batch_id": batch_id}
    from .job_service import dialogue_voice_payload, preview_voice_payload, render_voice_payload
    requested_items = data.get("item_ids") if isinstance(data.get("item_ids"), list) else []
    job_type_override = str(data.get("job_type") or "").strip()
    rendered: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    batch["queue_state"] = {"schema_id": "neo.voice.batch_queue_state.v13", "state": "running", "position": 0, "terminal": False, "recoverable": False}
    for item in batch.get("items", []):
        if requested_items and item.get("item_id") not in requested_items:
            continue
        if not item.get("script"):
            item["status"] = "skipped_empty_script"
            failed.append({"item_id": item.get("item_id"), "status": item["status"]})
            continue
        job_type = job_type_override or str(item.get("job_type") or batch.get("default_job_type") or "render")
        job_payload = _item_render_payload(batch, item)
        try:
            if job_type == "preview":
                job = preview_voice_payload(job_payload)
            elif job_type == "dialogue":
                job = dialogue_voice_payload(job_payload)
            else:
                job = render_voice_payload(job_payload)
            item["status"] = "job_created"
            item["job_id"] = job.get("job_id")
            item["job_status"] = job.get("status")
            rendered.append({"item_id": item.get("item_id"), "job_id": job.get("job_id"), "status": job.get("status"), "output_file": job.get("output_file")})
        except Exception as exc:
            item["status"] = "render_failed"
            item["error"] = str(exc)
            failed.append({"item_id": item.get("item_id"), "status": "render_failed", "error": str(exc)})
    batch["rendered_jobs"] = (batch.get("rendered_jobs") or []) + rendered
    batch["failed_items"] = failed
    batch["updated_at"] = _now()
    batch["status"] = "batch_render_ready" if rendered and not failed else ("batch_render_ready_with_failures" if rendered else "batch_render_failed")
    batch["queue_state"] = {"schema_id": "neo.voice.batch_queue_state.v13", "state": batch["status"], "position": 0, "terminal": True, "recoverable": bool(failed)}
    batch["render_manifest"] = {"schema_id": BATCH_RENDER_SCHEMA, "rendered_count": len(rendered), "failed_count": len(failed), "rendered_jobs": rendered, "failed_items": failed}
    batch = _save_manifest(batch)
    _store_batch(batch)
    return {"ok": bool(rendered), "status": batch["status"], "batch": batch, "rendered_jobs": rendered, "failed_items": failed}


def voice_batch_payload(batch_id: str) -> dict[str, Any]:
    batch = next((item for item in _read_batches() if item.get("batch_id") == batch_id), None)
    if not batch:
        return {"ok": False, "status": "missing_batch", "batch_id": batch_id}
    return {"ok": True, "status": batch.get("status") or "unknown", "batch": batch}


def voice_batch_history_payload(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    batches = list(reversed(_read_batches()))
    if status:
        batches = [item for item in batches if item.get("status") == status]
    batches = batches[: max(1, min(int(limit or 50), 200))]
    return {"schema_id": "neo.voice.batch_history.v13", "surface": "voice", "phase": "VO-V13", "count": len(batches), "batches": batches, "filters": {"status": status or ""}}


def retry_voice_batch_item_payload(batch_id: str, item_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    data["item_ids"] = [item_id]
    return render_voice_batch_payload(batch_id, data)
