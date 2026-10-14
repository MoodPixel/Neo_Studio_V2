from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from pathlib import Path
from typing import Any

from neo_app.knowledge.source_contract import build_source_document, file_sha256

AUDIO_MEDIA_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".webm",
}
VIDEO_MEDIA_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpeg", ".mpg",
}
MEDIA_SUFFIXES = AUDIO_MEDIA_SUFFIXES | VIDEO_MEDIA_SUFFIXES


def _seconds_label(value: Any) -> str:
    try:
        seconds = max(0.0, float(value or 0.0))
    except Exception:
        seconds = 0.0
    whole = int(seconds)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"status": "unavailable", "ffprobe": False, "streams": []}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries",
                "format=duration,format_name,bit_rate:stream=index,codec_type,codec_name,duration,channels,sample_rate,width,height,r_frame_rate",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        payload = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        return {
            "status": "ready" if result.returncode == 0 else "error",
            "ffprobe": True,
            "returncode": int(result.returncode),
            "format": fmt,
            "streams": streams,
            "stderr": str(result.stderr or "")[-1000:],
        }
    except Exception as exc:
        return {"status": "error", "ffprobe": True, "streams": [], "error": str(exc)[:1000]}


def _media_kind(path: Path, media_type: str = "", probe: dict[str, Any] | None = None) -> str:
    mime = str(media_type or mimetypes.guess_type(path.name)[0] or "").lower()
    streams = (probe or {}).get("streams") if isinstance((probe or {}).get("streams"), list) else []
    if mime.startswith("video/") or any(str(item.get("codec_type") or "") == "video" for item in streams if isinstance(item, dict)):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    suffix = path.suffix.lower()
    if suffix in VIDEO_MEDIA_SUFFIXES and suffix not in AUDIO_MEDIA_SUFFIXES:
        return "video"
    return "audio"


def _confidence_from_segment(segment: dict[str, Any]) -> float | None:
    words = segment.get("words") if isinstance(segment.get("words"), list) else []
    probabilities: list[float] = []
    for item in words:
        if not isinstance(item, dict) or item.get("probability") is None:
            continue
        try:
            probabilities.append(float(item.get("probability")))
        except Exception:
            pass
    if probabilities:
        return max(0.0, min(1.0, sum(probabilities) / len(probabilities)))
    return None


def prepare_media_source(
    path: Path | str,
    *,
    language: str | None = None,
    model_path: str | None = None,
) -> dict[str, Any]:
    """Normalize audio or a video's audio track into the NeoSourceDocument contract.

    ASR authority remains ``neo_app.voice.service``. This adapter only turns that
    transcription result into source blocks with timestamp provenance so Assistant
    and Project ingestion can share the same downstream fragment/retrieval path.
    """
    source_path = Path(path).resolve()
    suffix = source_path.suffix.lower()
    media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    try:
        content_hash = file_sha256(source_path)
    except Exception:
        content_hash = ""

    probe = _probe_media(source_path)
    media_kind = _media_kind(source_path, media_type, probe)
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    audio_streams = [item for item in streams if isinstance(item, dict) and str(item.get("codec_type") or "") == "audio"]
    video_streams = [item for item in streams if isinstance(item, dict) and str(item.get("codec_type") or "") == "video"]

    # If ffprobe positively establishes that a video has no audio, avoid invoking
    # ASR and record the limitation honestly. When ffprobe is unavailable, let the
    # Voice transcription authority attempt decoding itself.
    if media_kind == "video" and probe.get("status") == "ready" and not audio_streams:
        return build_source_document(
            path=source_path,
            source_name=source_path.name,
            suffix=suffix,
            media_type=media_type,
            blocks=[],
            parser_id="voice_transcription",
            parser_version="neo.voice.input.v1",
            parser_mode="asr",
            status="stored_only",
            content_hash=content_hash,
            attempts=[{"parser_id": "voice_transcription", "status": "stored_only", "reason": "media_has_no_audio_stream"}],
            warnings=["Video visual content was not inspected; ACI-4 only indexes transcribable audio from video files."],
            capabilities={
                "media": True,
                "media_kind": media_kind,
                "audio_track": False,
                "video_track": bool(video_streams),
                "visual_track_indexed": False,
                "transcript": False,
                "timestamped_segments": False,
            },
            metadata={
                "source_label": "Media",
                "media_kind": media_kind,
                "probe": probe,
                "transcription_status": "media_has_no_audio_stream",
            },
        )

    # Lazy import avoids making the source contract depend on the Memory/Project
    # service import graph at module-import time. Voice remains the sole ASR authority.
    from neo_app.voice.service import transcribe_audio_path

    transcription = transcribe_audio_path(
        source_path,
        language=language,
        model_path=model_path,
        word_timestamps=True,
        max_segments=None,
    )
    segments = transcription.get("segments") if isinstance(transcription.get("segments"), list) else []
    blocks: list[dict[str, Any]] = []
    for index, raw in enumerate(segments, start=1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start = raw.get("start")
        end = raw.get("end")
        label = f"{_seconds_label(start)}–{_seconds_label(end)}"
        locator = {
            "kind": "media_time_range",
            "start_seconds": start,
            "end_seconds": end,
            "label": label,
            "segment": index,
            "source_kind": media_kind,
        }
        speaker = str(raw.get("speaker") or "").strip()
        if speaker:
            locator["speaker"] = speaker
        blocks.append({
            "kind": "transcript_segment",
            "text": text,
            "heading_path": ["Transcript"],
            "locator": locator,
            "confidence": _confidence_from_segment(raw),
            "metadata": {
                "segment_index": index,
                "words": list(raw.get("words") or []),
                "avg_logprob": raw.get("avg_logprob"),
                "no_speech_prob": raw.get("no_speech_prob"),
                "speaker": speaker,
            },
        })

    transcript_text = str(transcription.get("text") or "").strip()
    if not blocks and transcript_text:
        blocks.append({
            "kind": "transcript",
            "text": transcript_text,
            "heading_path": ["Transcript"],
            "locator": {
                "kind": "media_time_range",
                "start_seconds": 0.0,
                "end_seconds": transcription.get("duration"),
                "label": "full transcript",
                "source_kind": media_kind,
            },
            "metadata": {},
        })

    backend = str(transcription.get("backend") or "voice_transcription")
    transcribed = bool(transcription.get("ok") and (blocks or transcript_text))
    status = "extracted" if transcribed else ("stored_only" if str(transcription.get("status") or "").startswith("transcriber_") else "error")
    warnings: list[str] = []
    if media_kind == "video":
        warnings.append("ACI-4 indexed the video's audio track only; visual frames were not inspected by this source adapter.")
    if not transcribed and transcription.get("setup_hint"):
        warnings.append(str(transcription.get("setup_hint")))

    return build_source_document(
        path=source_path,
        source_name=source_path.name,
        suffix=suffix,
        media_type=media_type,
        blocks=blocks,
        parser_id=f"voice_transcription:{backend}",
        parser_version=str(transcription.get("runtime_version") or "neo.voice.input.v1"),
        parser_mode="asr",
        status=status,
        content_hash=content_hash,
        attempts=[{
            "parser_id": f"voice_transcription:{backend}",
            "status": str(transcription.get("status") or status),
            "model": str(transcription.get("model_path") or ""),
        }],
        warnings=warnings,
        capabilities={
            "media": True,
            "media_kind": media_kind,
            "audio_track": bool(audio_streams) if probe.get("status") == "ready" else True,
            "video_track": bool(video_streams) if probe.get("status") == "ready" else media_kind == "video",
            "visual_track_indexed": False,
            "transcript": bool(blocks),
            "timestamped_segments": bool(segments),
            "word_timestamps": any(bool((item or {}).get("words")) for item in segments if isinstance(item, dict)),
            "speaker_labels": any(bool(str((item or {}).get("speaker") or "").strip()) for item in segments if isinstance(item, dict)),
        },
        metadata={
            "source_label": "Media",
            "media_kind": media_kind,
            "language": transcription.get("language"),
            "duration_seconds": transcription.get("duration") or (probe.get("format") or {}).get("duration"),
            "segment_count": len(segments),
            "word_count": int(transcription.get("word_count") or 0),
            "asr_backend": backend,
            "asr_model": str(transcription.get("model_path") or ""),
            "asr_device": str(transcription.get("device") or ""),
            "asr_compute_type": str(transcription.get("compute_type") or ""),
            "transcription_status": str(transcription.get("status") or ""),
            "transcription_error": str(transcription.get("error") or "")[:1000],
            "probe": probe,
        },
    )
