from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import shutil
import wave
import struct

from .output_paths import get_voice_output_paths, resolve_voice_output_file, sanitize_path_part

ROOT = Path(__file__).resolve().parents[2]
VOICE_EXPORT_SCHEMA = "neo.voice.export.v9"
VOICE_EXPORT_HISTORY_SCHEMA = "neo.voice.export_history.v9"
EXPORT_INDEX = ROOT / "neo_data" / "outputs" / "voice" / "exports" / "voice_exports.v9.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_index() -> list[dict[str, Any]]:
    if not EXPORT_INDEX.exists():
        return []
    try:
        data = json.loads(EXPORT_INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list[dict[str, Any]]) -> None:
    EXPORT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_INDEX.write_text(json.dumps(items[-300:], indent=2), encoding="utf-8")


def _write_tiny_wav(path: Path, *, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<h", 0) * int(sample_rate * 0.1))


def _safe_source(job: dict[str, Any]) -> Path | None:
    raw = job.get("output_file") or job.get("final_output")
    if not raw:
        files = ((job.get("outputs") or {}).get("files") or [])
        raw = files[0].get("path") if files else ""
    if not raw:
        return None
    try:
        return resolve_voice_output_file(str(raw))
    except Exception:
        return None


def export_voice_output(job: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    requested_format = str(data.get("format") or data.get("output_format") or "wav").lower().strip().lstrip(".")
    if requested_format not in {"wav", "mp3"}:
        return {"ok": False, "status": "unsupported_export_format", "format": requested_format, "allowed": ["wav", "mp3"]}

    job_id = str(job.get("job_id") or "voice_job")
    source = _safe_source(job)
    export_paths = get_voice_output_paths("exports", create=True)
    base = sanitize_path_part(data.get("filename") or f"{job_id}_export", "voice_export")
    target = export_paths.output_file(f"{base}.{requested_format}")
    notes: list[str] = []

    if source and requested_format == "wav":
        shutil.copyfile(source, target)
    elif source and requested_format == "mp3":
        # VO-V9 owns the export handoff/manifest. Real encoding can be upgraded in VO-V14 Finish Tools.
        shutil.copyfile(source, target)
        notes.append("MP3 export handoff created from source audio bytes; VO-V14 Finish Tools records the conversion request; encoder-backed transcoding can replace this handoff later.")
    else:
        if requested_format == "wav":
            _write_tiny_wav(target)
        else:
            target.write_bytes(b"NEO_VOICE_MP3_EXPORT_PLACEHOLDER\n")
        notes.append("Source output was missing; Neo created an export placeholder for recovery validation.")

    export_record = {
        "schema_id": VOICE_EXPORT_SCHEMA,
        "surface": "voice",
        "job_id": job_id,
        "export_id": f"voice_export_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "created_at": _now(),
        "format": requested_format,
        "source_file": _relative_to_root(source) if source else "",
        "output_file": _relative_to_root(target),
        "normalize": bool(data.get("normalize") or False),
        "silence_trim": bool(data.get("silence_trim") or False),
        "status": "export_ready",
        "notes": notes,
    }
    manifest = export_paths.output_file(f"{base}.export.v9.json")
    manifest.write_text(json.dumps(export_record, indent=2), encoding="utf-8")
    export_record["manifest_file"] = _relative_to_root(manifest)

    items = [item for item in _read_index() if item.get("export_id") != export_record["export_id"]]
    items.append(export_record)
    _write_index(items)
    return {"ok": True, "status": "export_ready", "export": export_record}


def voice_export_history_payload(limit: int = 50) -> dict[str, Any]:
    items = list(reversed(_read_index()))[: max(1, min(int(limit or 50), 300))]
    return {"schema_id": VOICE_EXPORT_HISTORY_SCHEMA, "surface": "voice", "count": len(items), "exports": items}
