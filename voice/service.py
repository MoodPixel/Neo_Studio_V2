from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import importlib.util
import json
import os
import re
import shutil
import tempfile

from neo_app.memory.service import get_memory_service
from neo_app.assistant.action_review import plan_assistant_action_review, run_assistant_action_review
from neo_app.internet.service import internet_access_status_payload

VOICE_INPUT_SCHEMA_VERSION = "neo.voice.input.v1"
VOICE_INPUT_RUNTIME_VERSION = "0.1.0"
ROOT_DIR = Path(__file__).resolve().parents[2]
VOICE_INBOX = ROOT_DIR / "neo_data" / "voice" / "input"
VOICE_INPUT_CONFIG_PATH = ROOT_DIR / "neo_data" / "admin" / "voice_input" / "asr_config.json"
VOICE_INPUT_CONFIG_SCHEMA_ID = "neo.voice.input.asr_config.v1"
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".aac", ".wma", ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _dependency_status() -> dict[str, Any]:
    return {
        "faster_whisper": bool(importlib.util.find_spec("faster_whisper")),
        "whisper": bool(importlib.util.find_spec("whisper")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
    }


def _default_voice_input_config() -> dict[str, Any]:
    return {
        "schema_id": VOICE_INPUT_CONFIG_SCHEMA_ID,
        "provider": "auto",
        "model_path": "",
        "language": "",
        "device": "auto",
        "compute_type": "auto",
        "word_timestamps": True,
    }


def voice_input_config_payload() -> dict[str, Any]:
    config = _default_voice_input_config()
    try:
        if VOICE_INPUT_CONFIG_PATH.exists():
            stored = json.loads(VOICE_INPUT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key in ("provider", "model_path", "language", "device", "compute_type", "word_timestamps"):
                    if key in stored:
                        config[key] = stored[key]
    except Exception:
        pass
    config["provider"] = str(config.get("provider") or "auto").strip().lower()
    if config["provider"] not in {"auto", "faster_whisper", "whisper"}:
        config["provider"] = "auto"
    config["model_path"] = str(config.get("model_path") or "").strip()
    config["language"] = str(config.get("language") or "").strip()
    config["device"] = str(config.get("device") or "auto").strip().lower() or "auto"
    if config["device"] not in {"auto", "cpu", "cuda"}:
        config["device"] = "auto"
    config["compute_type"] = str(config.get("compute_type") or "auto").strip() or "auto"
    config["word_timestamps"] = bool(config.get("word_timestamps", True))
    config["path"] = str(VOICE_INPUT_CONFIG_PATH)
    return config


def update_voice_input_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    current = voice_input_config_payload()
    provider = str(data.get("provider", current.get("provider") or "auto") or "auto").strip().lower()
    if provider not in {"auto", "faster_whisper", "whisper"}:
        raise ValueError("Voice transcriber provider must be auto, faster_whisper, or whisper.")
    device = str(data.get("device", current.get("device") or "auto") or "auto").strip().lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("Voice transcriber device must be auto, cpu, or cuda.")
    config = {
        "schema_id": VOICE_INPUT_CONFIG_SCHEMA_ID,
        "provider": provider,
        "model_path": str(data.get("model_path", current.get("model_path") or "") or "").strip(),
        "language": str(data.get("language", current.get("language") or "") or "").strip(),
        "device": device,
        "compute_type": str(data.get("compute_type", current.get("compute_type") or "auto") or "auto").strip() or "auto",
        "word_timestamps": bool(data.get("word_timestamps", current.get("word_timestamps", True))),
    }
    VOICE_INPUT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = VOICE_INPUT_CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(VOICE_INPUT_CONFIG_PATH)
    return voice_input_config_payload()


def _configured_model_settings(explicit: str | None = None) -> dict[str, Any]:
    config = voice_input_config_payload()
    explicit_path = str(explicit or "").strip()
    env_path = str(os.environ.get("NEO_WHISPER_MODEL_PATH") or os.environ.get("NEO_TRANSCRIBER_MODEL_PATH") or "").strip()
    if explicit_path:
        path, source = explicit_path, "payload"
    elif str(config.get("model_path") or "").strip():
        path, source = str(config.get("model_path") or "").strip(), "admin_config"
    else:
        path, source = env_path, "environment" if env_path else "unconfigured"
    return {**config, "model_path": path, "source": source}


def _configured_model_path(explicit: str | None = None) -> str:
    return str(_configured_model_settings(explicit).get("model_path") or "")


def voice_input_status_payload() -> dict[str, Any]:
    deps = _dependency_status()
    settings = _configured_model_settings()
    model_path = str(settings.get("model_path") or "")
    model_ready = bool(model_path and (Path(model_path).exists() or not Path(model_path).is_absolute()))
    requested_provider = str(settings.get("provider") or "auto")
    provider_ready = (requested_provider in {"auto", "faster_whisper"} and deps["faster_whisper"]) or (requested_provider in {"auto", "whisper"} and deps["whisper"])
    transcriber_ready = bool(provider_ready and model_ready)
    internet = internet_access_status_payload()
    return {
        "schema_id": "neo.voice.input.status.v1",
        "status": "ready" if transcriber_ready else "ready_for_setup",
        "label": "Voice Input",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "input_modes": ["audio_file", "transcript_text"],
        "operator_bridge": "enabled",
        "operator_bridge_mode": "assistant_control_center_to_structured_operator",
        "output_modes": ["transcribed_text", "action_review_plan", "operator_plan", "operator_result"],
        "voice_output": "not_enabled",
        "internet": internet.get("mode") or "disabled",
        "dependencies": deps,
        "model": {
            "configured": bool(model_path),
            "path": model_path,
            "ready": model_ready,
            "source": settings.get("source") or "unconfigured",
            "provider": requested_provider,
            "device": settings.get("device") or "auto",
            "compute_type": settings.get("compute_type") or "auto",
            "language": settings.get("language") or "",
            "word_timestamps": bool(settings.get("word_timestamps", True)),
        },
        "config": settings,
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
            "assistant_audio_media_attachment_transcription",
            "timestamped_segment_evidence",
            "optional_word_timestamps",
            "manual_transcript_to_assistant_action_planner",
            "voice_as_text_command_bridge",
            "structured_operator_permission_gating",
            "voice_input_memory_writeback",
        ],
        "policy": "Voice input is only an input layer. Transcribed speech becomes text; Assistant/Control Center plans any executable action, then Operator applies permission/confirmation gates and execution proof. Voice output is not enabled.",
    }


def _save_upload_bytes(filename: str, data: bytes) -> Path:
    VOICE_INBOX.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        suffix = ".audio"
    path = VOICE_INBOX / f"voice_input_{uuid4().hex[:12]}{suffix}"
    path.write_bytes(data)
    return path


def _word_payload(word: Any) -> dict[str, Any]:
    return {
        "start": getattr(word, "start", None) if not isinstance(word, dict) else word.get("start"),
        "end": getattr(word, "end", None) if not isinstance(word, dict) else word.get("end"),
        "word": str((getattr(word, "word", "") if not isinstance(word, dict) else word.get("word")) or "").strip(),
        "probability": getattr(word, "probability", None) if not isinstance(word, dict) else word.get("probability"),
    }


def _transcribe_with_faster_whisper(
    audio_path: Path,
    *,
    model_path: str,
    language: str | None = None,
    device: str = "auto",
    compute_type: str = "auto",
    word_timestamps: bool = False,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel  # type: ignore

    effective_compute_type = "default" if str(compute_type or "auto").strip().lower() in {"", "auto"} else str(compute_type).strip()
    model = WhisperModel(
        model_path,
        device=device or "auto",
        compute_type=effective_compute_type,
        local_files_only=True,
    )
    segments, info = model.transcribe(str(audio_path), language=language or None, word_timestamps=bool(word_timestamps))
    text_parts: list[str] = []
    segment_payload: list[dict[str, Any]] = []
    word_count = 0
    for segment in segments:
        text = getattr(segment, "text", "") or ""
        text_parts.append(text.strip())
        words = [_word_payload(word) for word in (getattr(segment, "words", None) or [])] if word_timestamps else []
        words = [word for word in words if word.get("word")]
        word_count += len(words)
        segment_payload.append({
            "start": getattr(segment, "start", None),
            "end": getattr(segment, "end", None),
            "text": text.strip(),
            "avg_logprob": getattr(segment, "avg_logprob", None),
            "no_speech_prob": getattr(segment, "no_speech_prob", None),
            "words": words,
        })
    return {
        "ok": True,
        "backend": "faster_whisper",
        "text": _normalize_text(" ".join(text_parts)),
        "language": getattr(info, "language", language or None),
        "duration": getattr(info, "duration", None),
        "segments": segment_payload,
        "word_count": word_count,
        "device": device or "auto",
        "compute_type": effective_compute_type,
    }


def _transcribe_with_whisper(
    audio_path: Path,
    *,
    model_path: str,
    language: str | None = None,
    device: str = "auto",
    word_timestamps: bool = False,
) -> dict[str, Any]:
    import whisper  # type: ignore

    kwargs: dict[str, Any] = {}
    if device and device != "auto":
        kwargs["device"] = device
    model = whisper.load_model(model_path, **kwargs)
    result = model.transcribe(str(audio_path), language=language or None, word_timestamps=bool(word_timestamps))
    segments = []
    word_count = 0
    for raw in result.get("segments") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        words = [dict(word) for word in (item.get("words") or []) if isinstance(word, dict)] if word_timestamps else []
        item["words"] = words
        word_count += len(words)
        segments.append(item)
    return {
        "ok": True,
        "backend": "whisper",
        "text": _normalize_text(result.get("text") or ""),
        "language": result.get("language") or language,
        "segments": segments,
        "word_count": word_count,
        "device": device or "auto",
        "compute_type": "native",
    }


def transcribe_audio_path(
    audio_path: str | Path,
    *,
    language: str | None = None,
    model_path: str | None = None,
    word_timestamps: bool | None = None,
    max_segments: int | None = 80,
) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        return {"ok": False, "status": "missing_audio_file", "text": "", "audio_path": str(path)}
    deps = _dependency_status()
    settings = _configured_model_settings(model_path)
    configured_model = str(settings.get("model_path") or "")
    effective_language = str(language or settings.get("language") or "").strip() or None
    effective_words = bool(settings.get("word_timestamps", True)) if word_timestamps is None else bool(word_timestamps)
    provider = str(settings.get("provider") or "auto").strip().lower()
    device = str(settings.get("device") or "auto")
    compute_type = str(settings.get("compute_type") or "auto")
    if not configured_model:
        return {
            "ok": False,
            "status": "transcriber_not_configured",
            "text": "",
            "audio_path": str(path),
            "dependencies": deps,
            "setup_hint": "Configure the local ASR model under Admin -> Assistant / Operator -> Voice Input, set NEO_WHISPER_MODEL_PATH, or pass model_path.",
        }
    try:
        if provider in {"auto", "faster_whisper"} and deps["faster_whisper"]:
            result = _transcribe_with_faster_whisper(
                path, model_path=configured_model, language=effective_language, device=device,
                compute_type=compute_type, word_timestamps=effective_words,
            )
        elif provider in {"auto", "whisper"} and deps["whisper"]:
            result = _transcribe_with_whisper(
                path, model_path=configured_model, language=effective_language, device=device, word_timestamps=effective_words,
            )
        else:
            return {
                "ok": False,
                "status": "transcriber_dependency_missing",
                "text": "",
                "audio_path": str(path),
                "dependencies": deps,
                "provider": provider,
                "setup_hint": "Install faster-whisper (recommended) or whisper, then configure a local model path.",
            }
    except Exception as exc:  # pragma: no cover - depends on local model/runtime
        return {
            "ok": False,
            "status": "transcription_failed",
            "text": "",
            "audio_path": str(path),
            "error": str(exc),
            "dependencies": deps,
            "provider": provider,
            "model_path": configured_model,
        }
    segments = result.get("segments") if isinstance(result.get("segments"), list) else []
    total_segments = len(segments)
    if max_segments is not None:
        try:
            limit = max(0, int(max_segments))
        except Exception:
            limit = 80
        if limit:
            result["segments"] = segments[:limit]
        else:
            result["segments"] = []
    result.update({
        "schema_id": "neo.voice.input.transcription.v1",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "status": "transcribed" if result.get("ok") else result.get("status", "failed"),
        "audio_path": str(path),
        "created_at": _now(),
        "provider": provider,
        "model_path": configured_model,
        "model_source": settings.get("source") or "",
        "device": result.get("device") or device,
        "compute_type": result.get("compute_type") or compute_type,
        "word_timestamps": effective_words,
        "segment_count": total_segments,
        "segments_returned": len(result.get("segments") or []),
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
    action_review = plan_assistant_action_review({"command": transcript_text, "profile": data.get("profile"), "sources": data.get("sources"), "limit": data.get("limit")}) if transcript_text else None
    structured_plan = (action_review or {}).get("operator_plan") if isinstance(action_review, dict) else None
    plan = ({**structured_plan, "schema_id": "neo.operator.plan.v1", "intent": (action_review or {}).get("intent"), "confidence": (action_review or {}).get("confidence"), "retrieval_profile": (action_review or {}).get("retrieval_profile"), "sources": (action_review or {}).get("sources") or [], "compatibility_adapter": "voice_assistant_action_review"} if isinstance(structured_plan, dict) else None)
    result = {
        "ok": bool(transcript_text),
        "schema_id": "neo.voice.input.prepare.v1",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "status": status,
        "transcript_text": transcript_text,
        "transcription": transcription,
        "action_review_plan": action_review,
        "operator_plan": plan,
        "policy": "Voice is treated as text input. Assistant/Control Center decides requested actions before Operator receives a structured execution plan.",
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
    action_result = run_assistant_action_review({
        "command": prepared.get("transcript_text") or "",
        "profile": data.get("profile"),
        "sources": data.get("sources"),
        "limit": data.get("limit"),
        "execute_confirmed": bool(data.get("execute_confirmed") or data.get("confirm")),
        "index_limit": data.get("index_limit"),
    })
    structured_operator_result = action_result.get("operator_result") or {}
    operator_result = {**structured_operator_result, "schema_id": "neo.operator.run.v1", "compatibility_adapter": "voice_assistant_action_review"}
    return {
        "ok": True,
        "schema_id": "neo.voice.input.operator_run.v1",
        "runtime_version": VOICE_INPUT_RUNTIME_VERSION,
        "status": operator_result.get("status") or "completed",
        "transcript_text": prepared.get("transcript_text"),
        "prepared": prepared,
        "action_review_result": action_result,
        "operator_result": operator_result,
        "policy": "Assistant/Control Center plans voice-derived actions; Operator permissions, confirmation, receipts, and ledger proof still apply.",
    }


async def transcribe_uploaded_audio_payload(upload_file: Any, *, language: str | None = None, model_path: str | None = None, run_operator: bool = False, execute_confirmed: bool = False) -> dict[str, Any]:
    filename = getattr(upload_file, "filename", "audio.wav") or "audio.wav"
    data = await upload_file.read()
    path = _save_upload_bytes(filename, data)
    transcription = transcribe_audio_path(path, language=language, model_path=model_path)
    if run_operator and transcription.get("text"):
        action_result = run_assistant_action_review({"command": transcription.get("text"), "execute_confirmed": execute_confirmed})
        structured_operator_result = action_result.get("operator_result") or {}
        operator_result = {**structured_operator_result, "schema_id": "neo.operator.run.v1", "compatibility_adapter": "voice_assistant_action_review"}
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
