from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import importlib.util
import os
import re
import shutil
import tempfile

from neo_app.memory.service import get_memory_service
from neo_app.operator.service import plan_operator_command, run_operator_command
from neo_app.internet.service import internet_access_status_payload

VOICE_INPUT_SCHEMA_VERSION = "neo.voice.input.v1"
VOICE_INPUT_RUNTIME_VERSION = "0.1.0"
ROOT_DIR = Path(__file__).resolve().parents[2]
VOICE_INBOX = ROOT_DIR / "neo_data" / "voice" / "input"
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4", ".aac"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _dependency_status() -> dict[str, Any]:
    return {
        "faster_whisper": bool(importlib.util.find_spec("faster_whisper")),
        "whisper": bool(importlib.util.find_spec("whisper")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def _configured_model_path(explicit: str | None = None) -> str:
    candidate = str(explicit or os.environ.get("NEO_WHISPER_MODEL_PATH") or os.environ.get("NEO_TRANSCRIBER_MODEL_PATH") or "").strip()
    return candidate


def voice_input_status_payload() -> dict[str, Any]:
    deps = _dependency_status()
    model_path = _configured_model_path()
    model_ready = bool(model_path and (Path(model_path).exists() or not Path(model_path).is_absolute()))
    transcriber_ready = bool((deps["faster_whisper"] or deps["whisper"]) and model_path)
    internet = internet_access_status_payload()
    return {
        "schema_id": "neo.voice.input.status.v1",
        "status": "ready" if transcriber_ready else "ready_for_setup",
        "label": "Voice Input",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "input_modes": ["audio_file", "transcript_text"],
        "operator_bridge": "enabled",
        "output_modes": ["transcribed_text", "operator_plan", "operator_result"],
        "voice_output": "not_enabled",
        "internet": internet.get("mode") or "disabled",
        "dependencies": deps,
        "model": {
            "configured": bool(model_path),
            "path": model_path,
            "ready": model_ready,
            "source": "environment_or_payload",
        },
        "permission_policy": {
            "microphone": "frontend_future_permission",
            "audio_file_upload": "allowed_local_only",
            "transcription": "local_only",
            "operator_run": "permission_gated_by_operator",
            "voice_output": "planned",
            "internet": internet.get("mode") or "disabled",
        },
        "capabilities": [
            "audio_file_to_text_when_transcriber_configured",
            "manual_transcript_to_operator",
            "voice_as_text_command_bridge",
            "operator_permission_gating",
            "voice_input_memory_writeback",
        ],
        "policy": "Voice input is only an input layer. Transcribed speech becomes text and is routed through Neo Operator; voice output is not enabled; optional internet/API access remains controlled by Admin and Operator permissions.",
    }


def _save_upload_bytes(filename: str, data: bytes) -> Path:
    VOICE_INBOX.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        suffix = ".audio"
    path = VOICE_INBOX / f"voice_input_{uuid4().hex[:12]}{suffix}"
    path.write_bytes(data)
    return path


def _transcribe_with_faster_whisper(audio_path: Path, *, model_path: str, language: str | None = None) -> dict[str, Any]:
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_path, device="auto", compute_type="auto")
    segments, info = model.transcribe(str(audio_path), language=language or None)
    text_parts = []
    segment_payload = []
    for segment in segments:
        text = getattr(segment, "text", "") or ""
        text_parts.append(text.strip())
        segment_payload.append({
            "start": getattr(segment, "start", None),
            "end": getattr(segment, "end", None),
            "text": text.strip(),
        })
    return {
        "ok": True,
        "backend": "faster_whisper",
        "text": _normalize_text(" ".join(text_parts)),
        "language": getattr(info, "language", language or None),
        "duration": getattr(info, "duration", None),
        "segments": segment_payload[:80],
    }


def _transcribe_with_whisper(audio_path: Path, *, model_path: str, language: str | None = None) -> dict[str, Any]:
    import whisper  # type: ignore

    model = whisper.load_model(model_path)
    result = model.transcribe(str(audio_path), language=language or None)
    return {
        "ok": True,
        "backend": "whisper",
        "text": _normalize_text(result.get("text") or ""),
        "language": result.get("language") or language,
        "segments": result.get("segments") or [],
    }


def transcribe_audio_path(audio_path: str | Path, *, language: str | None = None, model_path: str | None = None) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        return {"ok": False, "status": "missing_audio_file", "text": "", "audio_path": str(path)}
    deps = _dependency_status()
    configured_model = _configured_model_path(model_path)
    if not configured_model:
        return {
            "ok": False,
            "status": "transcriber_not_configured",
            "text": "",
            "audio_path": str(path),
            "dependencies": deps,
            "setup_hint": "Set NEO_WHISPER_MODEL_PATH or pass model_path. Voice input remains ready for manual transcript text without a model.",
        }
    try:
        if deps["faster_whisper"]:
            result = _transcribe_with_faster_whisper(path, model_path=configured_model, language=language)
        elif deps["whisper"]:
            result = _transcribe_with_whisper(path, model_path=configured_model, language=language)
        else:
            return {
                "ok": False,
                "status": "transcriber_dependency_missing",
                "text": "",
                "audio_path": str(path),
                "dependencies": deps,
                "setup_hint": "Install faster-whisper or whisper, then configure a local model path.",
            }
    except Exception as exc:  # pragma: no cover - depends on local model/runtime
        return {
            "ok": False,
            "status": "transcription_failed",
            "text": "",
            "audio_path": str(path),
            "error": str(exc),
            "dependencies": deps,
        }
    result.update({
        "schema_id": "neo.voice.input.transcription.v1",
        "status": "transcribed" if result.get("ok") else result.get("status", "failed"),
        "audio_path": str(path),
        "created_at": _now(),
    })
    return result


def _record_voice_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        memory = get_memory_service()
        event = memory.record_event({
            "namespace": "voice",
            "surface": "assistant",
            "source": "voice_input",
            "event_type": "voice.input.processed",
            "title": "Voice input processed",
            "summary": str(payload.get("text") or payload.get("status") or "Voice input processed")[:900],
            "tags": ["voice", "operator", str(payload.get("status") or "ready")],
            "payload": payload,
            "importance": "normal",
            "should_embed": True,
        })
        return event.get("event")
    except Exception:
        return None


def prepare_voice_input_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    transcript_text = _normalize_text(data.get("transcript_text") or data.get("text") or data.get("command") or "")
    transcription: dict[str, Any] | None = None
    if not transcript_text and data.get("audio_path"):
        transcription = transcribe_audio_path(data.get("audio_path"), language=data.get("language"), model_path=data.get("model_path"))
        transcript_text = _normalize_text(transcription.get("text") or "")
    status = "ready" if transcript_text else "needs_transcript"
    plan = plan_operator_command({"command": transcript_text, "profile": data.get("profile"), "sources": data.get("sources"), "limit": data.get("limit")}) if transcript_text else None
    result = {
        "ok": bool(transcript_text),
        "schema_id": "neo.voice.input.prepare.v1",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "status": status,
        "transcript_text": transcript_text,
        "transcription": transcription,
        "operator_plan": plan,
        "policy": "Voice is treated as text input before reaching Neo Operator.",
    }
    result["memory_event"] = _record_voice_event({"status": status, "text": transcript_text, "transcription": transcription, "operator_plan": plan})
    return result


def run_voice_input_operator_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    prepared = prepare_voice_input_payload(data)
    if not prepared.get("ok"):
        return {
            "ok": False,
            "schema_id": "neo.voice.input.operator_run.v1",
            "status": "needs_transcript",
            "prepared": prepared,
            "operator_result": None,
        }
    operator_result = run_operator_command({
        "command": prepared.get("transcript_text") or "",
        "profile": data.get("profile"),
        "sources": data.get("sources"),
        "limit": data.get("limit"),
        "execute_confirmed": bool(data.get("execute_confirmed") or data.get("confirm")),
        "index_limit": data.get("index_limit"),
    })
    return {
        "ok": True,
        "schema_id": "neo.voice.input.operator_run.v1",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "status": operator_result.get("status") or "completed",
        "transcript_text": prepared.get("transcript_text"),
        "prepared": prepared,
        "operator_result": operator_result,
        "policy": "Operator permissions still apply to voice-derived commands.",
    }


async def transcribe_uploaded_audio_payload(upload_file: Any, *, language: str | None = None, model_path: str | None = None, run_operator: bool = False, execute_confirmed: bool = False) -> dict[str, Any]:
    filename = getattr(upload_file, "filename", "audio.wav") or "audio.wav"
    data = await upload_file.read()
    path = _save_upload_bytes(filename, data)
    transcription = transcribe_audio_path(path, language=language, model_path=model_path)
    if run_operator and transcription.get("text"):
        operator_result = run_operator_command({"command": transcription.get("text"), "execute_confirmed": execute_confirmed})
    else:
        operator_result = None
    payload = {
        "ok": bool(transcription.get("text")),
        "schema_id": "neo.voice.input.upload_transcribe.v1",
        "status": "transcribed" if transcription.get("text") else transcription.get("status", "needs_setup"),
        "audio_path": str(path),
        "transcription": transcription,
        "transcript_text": transcription.get("text") or "",
        "operator_result": operator_result,
    }
    payload["memory_event"] = _record_voice_event({"status": payload["status"], "text": payload["transcript_text"], "audio_path": str(path), "operator_result": operator_result})
    return payload
