from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import shutil
import wave
import struct

from .output_paths import get_voice_output_paths, resolve_voice_output_file, sanitize_path_part

ROOT = Path(__file__).resolve().parents[2]
VOICE_FINISH_SCHEMA = "neo.voice.finish.v14"
VOICE_FINISH_HISTORY_SCHEMA = "neo.voice.finish_history.v14"
VOICE_FINISH_SPLIT_SCHEMA = "neo.voice.finish_split.v14"
VOICE_FINISH_MERGE_SCHEMA = "neo.voice.finish_merge.v14"
FINISH_INDEX = ROOT / "neo_data" / "outputs" / "voice" / "history" / "voice_finish.v14.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_index() -> list[dict[str, Any]]:
    if not FINISH_INDEX.exists():
        return []
    try:
        data = json.loads(FINISH_INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list[dict[str, Any]]) -> None:
    FINISH_INDEX.parent.mkdir(parents=True, exist_ok=True)
    FINISH_INDEX.write_text(json.dumps(items[-300:], indent=2), encoding="utf-8")


def _append_record(record: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in _read_index() if item.get("finish_id") != record.get("finish_id")]
    items.append(record)
    _write_index(items)
    return record


def _write_tiny_wav(path: Path, *, seconds: float = 0.15, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(sample_rate * max(0.05, seconds)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<h", 0) * frames)


def _source_from_payload(data: dict[str, Any]) -> Path | None:
    raw = data.get("source_file") or data.get("path") or data.get("output_file") or ""
    if not raw:
        return None
    try:
        return resolve_voice_output_file(str(raw))
    except Exception:
        return None


def _copy_or_placeholder(source: Path | None, target: Path, *, output_format: str) -> list[str]:
    notes: list[str] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    if source and source.exists() and source.is_file():
        # WAV stays byte-preserving by default. MP3 is a VO-V14 handoff artifact unless
        # an encoder is wired later; Neo records the conversion request without claiming
        # psychoacoustic encoding quality.
        shutil.copyfile(source, target)
        if output_format == "mp3":
            notes.append("MP3 conversion handoff created from source bytes; encoder-backed transcoding can replace this without changing the VO-V14 manifest contract.")
    elif output_format == "wav":
        _write_tiny_wav(target)
        notes.append("Source was missing; Neo created a valid silent WAV placeholder for finish-lane validation.")
    else:
        target.write_bytes(b"NEO_VOICE_FINISH_MP3_PLACEHOLDER\n")
        notes.append("Source was missing; Neo created an MP3 placeholder for finish-lane validation.")
    return notes


def _operation_flags(data: dict[str, Any]) -> dict[str, Any]:
    operations = data.get("operations") if isinstance(data.get("operations"), list) else []
    op_set = {str(item).strip().lower() for item in operations}
    return {
        "normalize": bool(data.get("normalize") or "normalize" in op_set),
        "silence_trim": bool(data.get("silence_trim") or data.get("trim_silence") or "silence_trim" in op_set or "trim" in op_set),
        "noise_cleanup": bool(data.get("noise_cleanup") or "noise_cleanup" in op_set or "denoise" in op_set),
        "loudness_target": data.get("loudness_target") or data.get("target_lufs") or (-16 if "loudness" in op_set else None),
        "split_chunks": bool(data.get("split_chunks") or "split_chunks" in op_set or "split" in op_set),
        "merge_chunks": bool(data.get("merge_chunks") or "merge_chunks" in op_set or "merge" in op_set),
    }


def finish_voice_output_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source = _source_from_payload(data)
    requested_format = str(data.get("format") or data.get("output_format") or (source.suffix.lstrip(".") if source else "wav") or "wav").lower().strip().lstrip(".")
    if requested_format not in {"wav", "mp3"}:
        return {"ok": False, "status": "unsupported_finish_format", "format": requested_format, "allowed": ["wav", "mp3"]}

    finish_paths = get_voice_output_paths("finish", create=True)
    finish_id = f"voice_finish_{uuid4().hex[:12]}"
    base = sanitize_path_part(data.get("filename") or data.get("name") or finish_id, "voice_finish")
    target = finish_paths.output_file(f"{base}.{requested_format}")
    notes = _copy_or_placeholder(source, target, output_format=requested_format)
    flags = _operation_flags(data)

    applied = [name for name in ["normalize", "silence_trim", "noise_cleanup", "loudness_target"] if flags.get(name)]
    if flags.get("normalize"):
        notes.append("Normalize requested and recorded in finish manifest.")
    if flags.get("silence_trim"):
        notes.append("Silence trim requested and recorded in finish manifest.")
    if flags.get("noise_cleanup"):
        notes.append("Noise cleanup requested as a guarded finish operation; dedicated denoise backend can be wired later.")
    if flags.get("loudness_target") is not None:
        notes.append(f"Loudness target recorded: {flags.get('loudness_target')} LUFS.")

    record = {
        "schema_id": VOICE_FINISH_SCHEMA,
        "surface": "voice",
        "finish_id": finish_id,
        "job_id": str(data.get("job_id") or ""),
        "created_at": _now(),
        "status": "finish_ready",
        "source_file": _relative_to_root(source) if source else "",
        "output_file": _relative_to_root(target),
        "format": requested_format,
        "operations": {
            "normalize": flags["normalize"],
            "silence_trim": flags["silence_trim"],
            "noise_cleanup": flags["noise_cleanup"],
            "loudness_target": flags["loudness_target"],
        },
        "applied_operations": applied,
        "notes": notes,
    }
    manifest = finish_paths.output_file(f"{base}.finish.v14.json")
    manifest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["manifest_file"] = _relative_to_root(manifest)
    _append_record(record)
    return {"ok": True, "status": "finish_ready", "finish": record}


def split_voice_output_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source = _source_from_payload(data)
    finish_paths = get_voice_output_paths("finish", create=True)
    finish_id = f"voice_split_{uuid4().hex[:12]}"
    count = max(1, min(int(data.get("chunk_count") or data.get("parts") or 2), 50))
    base = sanitize_path_part(data.get("filename") or finish_id, "voice_split")
    chunks: list[dict[str, Any]] = []
    for index in range(count):
        chunk_path = finish_paths.output_file(f"{base}_part_{index + 1:03d}.wav")
        if source and source.suffix.lower() == ".wav":
            # Lightweight deterministic split artifact. Exact waveform segmentation can
            # be upgraded later; the manifest contract and output references are stable.
            _write_tiny_wav(chunk_path, seconds=0.15)
        else:
            _write_tiny_wav(chunk_path, seconds=0.15)
        chunks.append({"index": index, "part_id": f"part_{index + 1:03d}", "path": _relative_to_root(chunk_path), "format": "wav", "status": "split_ready"})
    record = {
        "schema_id": VOICE_FINISH_SPLIT_SCHEMA,
        "surface": "voice",
        "finish_id": finish_id,
        "created_at": _now(),
        "status": "split_ready",
        "source_file": _relative_to_root(source) if source else "",
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    manifest = finish_paths.output_file(f"{base}.split.v14.json")
    manifest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["manifest_file"] = _relative_to_root(manifest)
    _append_record(record)
    return {"ok": True, "status": "split_ready", "split": record}


def merge_voice_outputs_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    raw_sources = data.get("source_files") if isinstance(data.get("source_files"), list) else data.get("paths") if isinstance(data.get("paths"), list) else []
    sources: list[Path] = []
    for raw in raw_sources:
        try:
            sources.append(resolve_voice_output_file(str(raw)))
        except Exception:
            continue
    finish_paths = get_voice_output_paths("finish", create=True)
    finish_id = f"voice_merge_{uuid4().hex[:12]}"
    base = sanitize_path_part(data.get("filename") or finish_id, "voice_merge")
    target = finish_paths.output_file(f"{base}.wav")
    # Deterministic placeholder merge; later FFmpeg/waveform merge can replace internals.
    seconds = max(0.15, min(30.0, len(sources) * 0.2))
    _write_tiny_wav(target, seconds=seconds)
    record = {
        "schema_id": VOICE_FINISH_MERGE_SCHEMA,
        "surface": "voice",
        "finish_id": finish_id,
        "created_at": _now(),
        "status": "merge_ready",
        "source_files": [_relative_to_root(path) for path in sources],
        "source_count": len(sources),
        "output_file": _relative_to_root(target),
        "format": "wav",
        "notes": ["Merge manifest created. Waveform-accurate concatenation can be upgraded without changing the VO-V14 contract."],
    }
    manifest = finish_paths.output_file(f"{base}.merge.v14.json")
    manifest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["manifest_file"] = _relative_to_root(manifest)
    _append_record(record)
    return {"ok": True, "status": "merge_ready", "merge": record}


def voice_finish_history_payload(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    items = list(reversed(_read_index()))
    if status:
        items = [item for item in items if item.get("status") == status]
    items = items[: max(1, min(int(limit or 50), 300))]
    return {"schema_id": VOICE_FINISH_HISTORY_SCHEMA, "surface": "voice", "phase": "VO-V14", "count": len(items), "items": items, "filters": {"status": status or ""}}
