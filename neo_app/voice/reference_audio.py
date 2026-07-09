from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import contextlib
import json
import math
import wave

from .output_paths import ROOT_DIR, get_voice_output_paths, sanitize_path_part

VOICE_REFERENCE_SCHEMA = "neo.voice.reference_audio.v6"
VOICE_REFERENCE_QC_SCHEMA = "neo.voice.reference_qc.v6"
SUPPORTED_REFERENCE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".aac"}
REFERENCE_INDEX = ROOT_DIR / "neo_data" / "outputs" / "voice" / "metadata" / "voice_reference_audio.v6.json"
MAX_REFERENCE_BYTES = 80 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_index() -> list[dict[str, Any]]:
    if not REFERENCE_INDEX.exists():
        return []
    try:
        data = json.loads(REFERENCE_INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(items: list[dict[str, Any]]) -> None:
    REFERENCE_INDEX.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_INDEX.write_text(json.dumps(items[-250:], indent=2), encoding="utf-8")


def _store_reference_record(record: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in _read_index() if item.get("reference_id") != record.get("reference_id")]
    items.append(record)
    _write_index(items)
    return record


def reference_record(reference_id: str) -> dict[str, Any] | None:
    safe_id = sanitize_path_part(reference_id, "")
    return next((item for item in _read_index() if item.get("reference_id") == safe_id), None)


def _resolve_reference_path(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise FileNotFoundError("Reference audio path is empty")
    candidate = (ROOT_DIR / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    reference_root = get_voice_output_paths("reference", create=True).output_dir.resolve()
    if reference_root not in candidate.parents and candidate != reference_root:
        raise ValueError("Reference audio must be stored under neo_data/outputs/voice/reference")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError("Reference audio file not found")
    return candidate


def _wav_stats(path: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    with contextlib.closing(wave.open(str(path), "rb")) as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        sampwidth = wav.getsampwidth()
        duration = frames / float(sample_rate or 1)
        stats.update({
            "duration_seconds": round(duration, 3),
            "sample_rate": sample_rate,
            "channels": channels,
            "sample_width_bytes": sampwidth,
            "frame_count": frames,
        })
        # Small, bounded QC read so huge references do not hurt the UI.
        read_frames = min(frames, sample_rate * 30 if sample_rate else frames)
        raw = wav.readframes(int(read_frames))
        if sampwidth == 2 and raw:
            import struct
            sample_count = len(raw) // 2
            values = struct.unpack("<" + "h" * sample_count, raw[: sample_count * 2])
            if values:
                peak = max(abs(v) for v in values) / 32768.0
                rms = math.sqrt(sum(float(v) * float(v) for v in values) / len(values)) / 32768.0
                clipped = sum(1 for v in values if abs(v) >= 32700)
                stats.update({
                    "peak_level": round(peak, 4),
                    "rms_level": round(rms, 4),
                    "clipped_sample_ratio": round(clipped / len(values), 6),
                })
    return stats


def analyze_reference_audio_path(path: str | Path, *, reference_id: str | None = None, transcript: str | None = None) -> dict[str, Any]:
    file_path = _resolve_reference_path(str(path))
    suffix = file_path.suffix.lower()
    size_bytes = file_path.stat().st_size
    stats: dict[str, Any] = {
        "duration_seconds": None,
        "sample_rate": None,
        "channels": None,
        "format": suffix.lstrip("."),
        "size_bytes": size_bytes,
        "analysis_depth": "container_basic",
    }
    warnings: list[str] = []
    recommendations: list[str] = []
    if suffix == ".wav":
        try:
            stats.update(_wav_stats(file_path))
            stats["analysis_depth"] = "wav_header_and_pcm_qc"
        except Exception as exc:
            warnings.append(f"WAV QC failed: {exc}")
    else:
        warnings.append("Deep QC is limited for this file type until FFmpeg-backed analysis is added.")

    duration = stats.get("duration_seconds")
    if isinstance(duration, (int, float)):
        if duration < 3:
            warnings.append("Reference audio is very short; clone quality may be weak.")
            recommendations.append("Use a clean 10–30 second single-speaker reference when possible.")
        elif duration > 90:
            warnings.append("Reference audio is long; later clone adapters may crop or sample it.")
            recommendations.append("Keep the clearest 10–45 seconds for faster zero-shot cloning.")
    else:
        recommendations.append("Use WAV when possible so Neo can inspect duration, sample rate, channels, clipping, and rough noise levels.")

    if stats.get("channels") and int(stats.get("channels") or 1) > 1:
        recommendations.append("Mono reference audio is usually safer for voice cloning.")
    if stats.get("sample_rate") and int(stats.get("sample_rate") or 0) < 16000:
        warnings.append("Sample rate is below 16 kHz; quality may suffer.")
    if stats.get("clipped_sample_ratio") and float(stats.get("clipped_sample_ratio") or 0) > 0.001:
        warnings.append("Possible clipping detected in the reference audio.")
    if stats.get("rms_level") is not None and float(stats.get("rms_level") or 0) < 0.005:
        warnings.append("Reference audio appears very quiet or mostly silent.")

    transcript_text = str(transcript or "").strip()
    qc_status = "usable_with_warnings" if warnings else "usable"
    payload = {
        "schema_id": VOICE_REFERENCE_QC_SCHEMA,
        "surface": "voice",
        "reference_id": sanitize_path_part(reference_id or file_path.stem, file_path.stem),
        "status": qc_status,
        "created_at": _now(),
        "path": _relative_to_root(file_path),
        "filename": file_path.name,
        "stats": stats,
        "warnings": warnings,
        "recommendations": recommendations or ["Reference file staged. Use Preview to test clone behavior before a full render."],
        "transcript_provided": bool(transcript_text),
        "transcript": transcript_text[:4000],
        "multi_speaker_check": "manual_review_required",
    }
    meta_paths = get_voice_output_paths("metadata", create=True)
    qc_file = meta_paths.output_file(f"{payload['reference_id']}.reference_qc.v6.json")
    qc_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["metadata_file"] = _relative_to_root(qc_file)
    return payload


async def store_reference_upload(file: Any, *, transcript: str | None = None, label: str | None = None) -> dict[str, Any]:
    original = Path(str(getattr(file, "filename", "reference.wav") or "reference.wav")).name
    suffix = Path(original).suffix.lower() or ".wav"
    if suffix not in SUPPORTED_REFERENCE_EXTENSIONS:
        raise ValueError(f"Unsupported reference audio format: {suffix}")
    data = await file.read()
    if not data:
        raise ValueError("Reference audio upload is empty")
    if len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("Reference audio is too large for this lane")
    reference_id = f"voice_ref_{uuid4().hex[:12]}"
    reference_paths = get_voice_output_paths("reference", create=True)
    stored_name = f"{reference_id}_{sanitize_path_part(Path(original).stem, 'reference')}{suffix}"
    path = reference_paths.output_file(stored_name)
    path.write_bytes(data)
    qc = analyze_reference_audio_path(path, reference_id=reference_id, transcript=transcript)
    record = {
        "schema_id": VOICE_REFERENCE_SCHEMA,
        "surface": "voice",
        "reference_id": reference_id,
        "label": str(label or Path(original).stem or reference_id),
        "original_filename": original,
        "stored_filename": stored_name,
        "path": _relative_to_root(path),
        "url": f"/api/voice/output-file?path={_relative_to_root(path)}",
        "created_at": _now(),
        "qc": qc,
        "status": qc.get("status") or "staged",
    }
    return _store_reference_record(record)


def analyze_reference_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    reference_id = str(data.get("reference_id") or "").strip()
    transcript = str(data.get("transcript") or "").strip()
    record = reference_record(reference_id) if reference_id else None
    path = str(data.get("path") or (record or {}).get("path") or "")
    qc = analyze_reference_audio_path(path, reference_id=reference_id or (record or {}).get("reference_id"), transcript=transcript)
    if record:
        record["qc"] = qc
        record["status"] = qc.get("status") or record.get("status")
        record["updated_at"] = _now()
        _store_reference_record(record)
    else:
        file_path = _resolve_reference_path(path)
        record = {
            "schema_id": VOICE_REFERENCE_SCHEMA,
            "surface": "voice",
            "reference_id": qc.get("reference_id"),
            "label": file_path.stem,
            "original_filename": file_path.name,
            "stored_filename": file_path.name,
            "path": _relative_to_root(file_path),
            "url": f"/api/voice/output-file?path={_relative_to_root(file_path)}",
            "created_at": _now(),
            "qc": qc,
            "status": qc.get("status") or "analyzed",
        }
        _store_reference_record(record)
    return {"ok": True, "status": qc.get("status") or "analyzed", "reference": record, "qc": qc}


def reference_history_payload(limit: int = 50) -> dict[str, Any]:
    items = list(reversed(_read_index()))[: max(1, min(int(limit or 50), 200))]
    return {"schema_id": "neo.voice.reference_history.v6", "surface": "voice", "count": len(items), "references": items}
