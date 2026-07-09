from __future__ import annotations

import hashlib
import json
import logging
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from neo_app.runtime_data import SURFACE_RUNTIME_LOG_DIRECTORIES
except Exception:  # pragma: no cover - defensive import fallback for standalone tooling
    SURFACE_RUNTIME_LOG_DIRECTORIES = (
        "logs/app",
        "logs/image",
        "logs/image/runs",
        "logs/video",
        "logs/video/runs",
        "logs/voice",
        "logs/voice/runs",
        "logs/prompt_captioning",
        "logs/prompt_captioning/runs",
        "logs/roleplay",
        "logs/roleplay/runs",
        "logs/assistant",
        "logs/assistant/runs",
        "logs/admin",
        "logs/admin/runs",
        "logs/admin/index_jobs",
        "logs/board",
        "logs/board/runs",
        "logs/memory",
        "logs/memory/runs",
        "logs/backends",
        "logs/backends/runs",
        "logs/extensions",
        "logs/extensions/runs",
    )

ROOT_DIR = Path(__file__).resolve().parents[2]
NEO_DATA_DIR = ROOT_DIR / "neo_data"
LOG_ROOT = NEO_DATA_DIR / "logs"
IMAGE_LOG_ROOT = LOG_ROOT / "image"
IMAGE_RUN_LOG_ROOT = IMAGE_LOG_ROOT / "runs"
CONSOLE_LOG_PATH = LOG_ROOT / "neo_console.log"
SERVER_LOG_PATH = LOG_ROOT / "neo_server.log"
ERROR_LOG_PATH = LOG_ROOT / "neo_error.log"
GENERATION_LOG_PATH = LOG_ROOT / "neo_generation.log"
EVENT_LOG_PATH = IMAGE_LOG_ROOT / "image_runtime_events.jsonl"
LAST_WORKFLOW_PATH = IMAGE_LOG_ROOT / "neo_last_workflow.json"
LAST_PAYLOAD_PATH = IMAGE_LOG_ROOT / "neo_last_payload.json"
LAST_ERROR_PATH = IMAGE_LOG_ROOT / "neo_last_generation_error.txt"
LAST_QUEUE_REQUEST_PATH = IMAGE_LOG_ROOT / "neo_last_queue_request.json"
LAST_COMFY_PROMPT_PATH = IMAGE_LOG_ROOT / "neo_last_comfy_prompt.json"

_LOGGER_NAME = "neo.runtime"
_CONFIGURED = False

# Pass L: surface-generic runtime logging. These surfaces match the runtime-data
# folder contract from Pass K while still allowing safe extension roots later.
KNOWN_SURFACE_LOG_IDS = tuple(
    rel.split("/", 1)[1]
    for rel in SURFACE_RUNTIME_LOG_DIRECTORIES
    if rel.startswith("logs/") and "/" not in rel.removeprefix("logs/")
)

# Pass P: runtime logs are for debugging evidence, not permanent content storage.
# Generic surface logs redact credentials, summarize high-risk creative/chat text,
# clamp large payloads, and filter tail previews. Image debug wrappers keep their
# historical full snapshots for Comfy graph debugging until Image gets its own
# explicit debug-depth toggle.
REDACTED_VALUE = "[REDACTED]"
CONTENT_REDACTED_VALUE = "[CONTENT_REDACTED]"
SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "cookie",
    "session_cookie",
    "credential",
    "credentials",
    "auth_header",
)
HIGH_RISK_CONTENT_KEYS = {
    "prompt",
    "negative_prompt",
    "system_prompt",
    "user_prompt",
    "source_text",
    "input_text",
    "output_text",
    "text",
    "caption",
    "reply",
    "transcript",
    "dialogue",
    "conversation",
    "messages",
    "chat_history",
    "scene_text",
    "story_text",
    "script",
    "client_brief",
    "brief_text",
}
MAX_LOG_STRING_CHARS = 1600
MAX_CONTENT_PREVIEW_CHARS = 0
MAX_LOG_LIST_ITEMS = 80
MAX_LOG_DICT_ITEMS = 120
MAX_LOG_DEPTH = 7
SECRET_TEXT_PATTERNS = (
    re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*'),
    re.compile(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|password|secret|client[_-]?secret|private[_-]?key)\s*[:=]\s*)([^\s,;"\']+)'),
    re.compile(r'(?i)("(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|password|secret|client[_-]?secret|private[_-]?key|token)"\s*:\s*")[^"]+(")'),
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (set, tuple)):
            return [_safe_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _safe_jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_safe_jsonable(v) for v in value]
        return repr(value)


def _normalized_key(key: Any) -> str:
    return str(key or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: Any) -> bool:
    lowered = _normalized_key(key)
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _is_high_risk_content_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    if normalized in HIGH_RISK_CONTENT_KEYS:
        return True
    return any(normalized.endswith(f"_{item}") or normalized.startswith(f"{item}_") for item in HIGH_RISK_CONTENT_KEYS)


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _content_summary(value: Any, *, key: Any | None = None) -> dict[str, Any]:
    text = str(value or "")
    summary: dict[str, Any] = {
        "redacted": CONTENT_REDACTED_VALUE,
        "content_redacted": True,
        "char_count": len(text),
        "sha256_12": _text_digest(text),
    }
    if key is not None:
        summary["field"] = str(key)
    if MAX_CONTENT_PREVIEW_CHARS > 0 and text:
        summary["preview"] = _redact_text_for_logs(text[:MAX_CONTENT_PREVIEW_CHARS])
    return summary


def _redact_text_for_logs(value: str) -> str:
    text = str(value or "")
    # Bearer token
    text = SECRET_TEXT_PATTERNS[0].sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}", text)
    # key=value / key: value token forms
    text = SECRET_TEXT_PATTERNS[1].sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}", text)
    # JSON string value forms
    text = SECRET_TEXT_PATTERNS[2].sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}{m.group(2)}", text)
    return text


def _truncate_log_string(value: str) -> str | dict[str, Any]:
    safe_text = _redact_text_for_logs(value)
    if len(safe_text) <= MAX_LOG_STRING_CHARS:
        return safe_text
    return {
        "truncated": True,
        "char_count": len(safe_text),
        "sha256_12": _text_digest(safe_text),
        "preview": safe_text[:MAX_LOG_STRING_CHARS] + "…",
    }


def sanitize_surface_log_payload(
    value: Any,
    *,
    key_context: Any | None = None,
    depth: int = 0,
    allow_content: bool = False,
) -> Any:
    """Return a JSON-safe privacy-filtered payload for generic surface logs.

    Rules:
    - credential-like keys are redacted;
    - creative/chat/story text keys are summarized by count + digest unless
      ``allow_content`` is explicitly set;
    - deep/huge payloads are clamped so logs stay useful and don't become a
      second storage system.
    """
    if _is_sensitive_key(key_context):
        return REDACTED_VALUE
    if _is_high_risk_content_key(key_context) and not allow_content:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return _content_summary(value, key=key_context)
        if isinstance(value, (list, tuple, set)):
            return {
                "redacted": CONTENT_REDACTED_VALUE,
                "content_redacted": True,
                "field": str(key_context),
                "item_count": len(value),
                "type": type(value).__name__,
            }
        if isinstance(value, dict):
            return {
                "redacted": CONTENT_REDACTED_VALUE,
                "content_redacted": True,
                "field": str(key_context),
                "key_count": len(value),
                "type": "dict",
                "keys": sorted(str(key) for key in value.keys())[:30],
            }

    if depth >= MAX_LOG_DEPTH:
        return {"truncated": True, "reason": "max_depth", "type": type(value).__name__}

    if isinstance(value, dict):
        items = list(value.items())
        limited = items[:MAX_LOG_DICT_ITEMS]
        sanitized = {
            str(key): sanitize_surface_log_payload(item, key_context=key, depth=depth + 1, allow_content=allow_content)
            for key, item in limited
        }
        if len(items) > MAX_LOG_DICT_ITEMS:
            sanitized["_neo_log_truncated_keys"] = len(items) - MAX_LOG_DICT_ITEMS
        return sanitized
    if isinstance(value, list):
        items = value[:MAX_LOG_LIST_ITEMS]
        sanitized_list = [sanitize_surface_log_payload(item, key_context=key_context, depth=depth + 1, allow_content=allow_content) for item in items]
        if len(value) > MAX_LOG_LIST_ITEMS:
            sanitized_list.append({"truncated": True, "remaining_items": len(value) - MAX_LOG_LIST_ITEMS})
        return sanitized_list
    if isinstance(value, tuple):
        return sanitize_surface_log_payload(list(value), key_context=key_context, depth=depth, allow_content=allow_content)
    if isinstance(value, set):
        return sanitize_surface_log_payload(sorted(value, key=lambda item: repr(item)), key_context=key_context, depth=depth, allow_content=allow_content)
    if isinstance(value, str):
        return _truncate_log_string(value)
    return _safe_jsonable(value)


def _redact_for_surface_logs(value: Any) -> Any:
    """Compatibility alias for Pass L tests and older imports."""
    return sanitize_surface_log_payload(value)


def surface_log_privacy_policy_payload() -> dict[str, Any]:
    return {
        "schema_id": "neo.surface_runtime_logs.privacy.pass_p.v1",
        "enabled": True,
        "generic_surface_logs_redacted": True,
        "credentials_redacted": True,
        "content_fields_summarized": True,
        "tail_previews_privacy_filtered": True,
        "payload_size_clamped": True,
        "image_debug_compatibility_full_snapshots": True,
        "redacted_value": REDACTED_VALUE,
        "content_redacted_value": CONTENT_REDACTED_VALUE,
        "max_string_chars": MAX_LOG_STRING_CHARS,
        "max_list_items": MAX_LOG_LIST_ITEMS,
        "max_dict_items": MAX_LOG_DICT_ITEMS,
        "max_depth": MAX_LOG_DEPTH,
        "sensitive_key_fragments": list(SENSITIVE_KEY_FRAGMENTS),
        "high_risk_content_keys": sorted(HIGH_RISK_CONTENT_KEYS),
        "policy": "Generic surface logs store runtime evidence and safe summaries, not full private/session content. Image debug wrappers preserve existing Comfy debugging artifacts for compatibility.",
    }


def _safe_log_message(message: str) -> str:
    safe = _redact_text_for_logs(str(message or ""))
    if len(safe) > 800:
        safe = safe[:800] + "…"
    return safe


def ensure_log_dirs() -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    for rel in SURFACE_RUNTIME_LOG_DIRECTORIES:
        if rel.startswith("logs/"):
            (NEO_DATA_DIR / rel).mkdir(parents=True, exist_ok=True)
    IMAGE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    IMAGE_RUN_LOG_ROOT.mkdir(parents=True, exist_ok=True)


def configure_runtime_logging() -> logging.Logger:
    """Configure Neo runtime logging under ``neo_data/logs``.

    The root file remains for compatibility, while structured surface events are
    now owned by ``neo_data/logs/<surface>/``.
    """
    global _CONFIGURED
    ensure_log_dirs()
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    # Keep runtime generation logs file-owned. Propagating to the root logger
    # made every image event appear twice and leaked provider/debug chatter into
    # the launcher console.
    logger.propagate = False
    if not _CONFIGURED:
        generation_handler = logging.FileHandler(GENERATION_LOG_PATH, encoding="utf-8")
        generation_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logger.addHandler(generation_handler)
        _CONFIGURED = True
    return logger



def write_console_status(message: str) -> None:
    """Append a human-level launcher/status line to neo_console.log.

    This file mirrors the clean startup output shown in the terminal. Runtime
    provider/generation noise belongs in neo_generation.log and uvicorn/server
    noise belongs in neo_server.log.
    """
    ensure_log_dirs()
    with CONSOLE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(str(message).rstrip() + "\n")


def log_file_payload() -> dict[str, str]:
    ensure_log_dirs()
    return {
        "console_log": display_path(CONSOLE_LOG_PATH),
        "server_log": display_path(SERVER_LOG_PATH),
        "error_log": display_path(ERROR_LOG_PATH),
        "generation_log": display_path(GENERATION_LOG_PATH),
    }


def runtime_logger() -> logging.Logger:
    return configure_runtime_logging()


def safe_run_id(value: str | None = None) -> str:
    raw = str(value or "").strip() or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    allowed = []
    for ch in raw:
        allowed.append(ch if ch.isalnum() or ch in {"-", "_", "."} else "_")
    return "".join(allowed)[:140]


def safe_surface_id(value: str | None) -> str:
    raw = str(value or "app").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = []
    for ch in raw:
        allowed.append(ch if ch.isalnum() or ch in {"_", "."} else "_")
    surface_id = "".join(allowed).strip("._")[:80]
    return surface_id or "app"


def _safe_snapshot_filename(name: str | None, *, default_suffix: str = ".json") -> str:
    raw = str(name or "snapshot").strip() or "snapshot"
    raw = raw.replace("\\", "/").split("/")[-1]
    if raw in {".", ".."}:
        raw = "snapshot"
    safe = safe_run_id(raw)
    if not Path(safe).suffix:
        safe = f"{safe}{default_suffix}"
    return safe


def surface_log_root(surface_id: str | None) -> Path:
    ensure_log_dirs()
    path = LOG_ROOT / safe_surface_id(surface_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "runs").mkdir(parents=True, exist_ok=True)
    return path


def surface_run_log_dir(surface_id: str | None, run_id: str | None) -> Path:
    root = surface_log_root(surface_id)
    path = root / "runs" / safe_run_id(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def surface_event_log_path(surface_id: str | None) -> Path:
    surface = safe_surface_id(surface_id)
    return surface_log_root(surface) / f"{surface}_runtime_events.jsonl"


def surface_last_error_path(surface_id: str | None) -> Path:
    surface = safe_surface_id(surface_id)
    if surface == "image":
        return LAST_ERROR_PATH
    return surface_log_root(surface) / "neo_last_error.txt"


def run_log_dir(run_id: str | None) -> Path:
    # Compatibility wrapper: historical image run directory helper.
    ensure_log_dirs()
    path = IMAGE_RUN_LOG_ROOT / safe_run_id(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    ensure_log_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_safe_jsonable(payload), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    ensure_log_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_log_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": _now_iso(), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_safe_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")


def log_surface_event(surface_id: str, event: str, *, run_id: str | None = None, level: str = "INFO", payload: dict[str, Any] | None = None) -> dict[str, str | None]:
    """Append a structured event to ``neo_data/logs/<surface>/``.

    This is the surface-generic replacement for the image-only event writer.
    It writes both the surface event stream and the per-run event stream when a
    run/job/session id is supplied.
    """
    surface = safe_surface_id(surface_id)
    safe_level = str(level or "INFO").upper()
    safe_run = safe_run_id(run_id) if run_id else None
    sanitized_payload = sanitize_surface_log_payload(payload or {})
    logger = runtime_logger()
    message = f"{surface}.{event} run_id={safe_run or '-'}"
    getattr(logger, safe_level.lower(), logger.info)(message)

    event_path = surface_event_log_path(surface)
    record = {
        "surface_id": surface,
        "event": str(event or "event"),
        "level": safe_level,
        "run_id": safe_run,
        "payload": sanitized_payload,
    }
    append_jsonl(event_path, record)
    run_events_path: Path | None = None
    if safe_run:
        run_events_path = surface_run_log_dir(surface, safe_run) / "events.jsonl"
        append_jsonl(run_events_path, {**record, "run_id": safe_run})
    return {
        "surface_id": surface,
        "event_log": display_path(event_path),
        "run_events": display_path(run_events_path) if run_events_path else None,
    }


def record_surface_snapshot(surface_id: str, name: str, payload: Any, *, run_id: str | None = None, redact: bool = True) -> dict[str, str | None]:
    """Write a latest snapshot and, when available, a per-run snapshot."""
    surface = safe_surface_id(surface_id)
    filename = _safe_snapshot_filename(name)
    snapshot_payload = sanitize_surface_log_payload(payload) if redact else _safe_jsonable(payload)
    latest_path = surface_log_root(surface) / filename
    write_json(latest_path, snapshot_payload)
    run_path: Path | None = None
    if run_id:
        run_path = surface_run_log_dir(surface, run_id) / filename
        write_json(run_path, snapshot_payload)
    log_surface_event(surface, "snapshot_recorded", run_id=run_id, payload={"snapshot": filename, "latest_path": display_path(latest_path)})
    return {
        "surface_id": surface,
        "latest_path": display_path(latest_path),
        "run_path": display_path(run_path) if run_path else None,
    }


def record_surface_error(
    surface_id: str,
    message: str,
    *,
    exc: BaseException | None = None,
    payload: dict[str, Any] | None = None,
    run_id: str | None = None,
    filename: str | None = None,
) -> dict[str, str | None]:
    """Write a privacy-safer error text file for any surface."""
    surface = safe_surface_id(surface_id)
    safe_run = safe_run_id(run_id) if run_id else None
    sanitized_payload = sanitize_surface_log_payload(payload or {})
    safe_message = _safe_log_message(message)
    text_lines = [f"[{_now_iso()}] {safe_message}", f"Surface: {surface}"]
    if safe_run:
        text_lines.append(f"Run ID: {safe_run}")
    if exc is not None:
        text_lines.append(f"Exception: {type(exc).__name__}: {_safe_log_message(str(exc))}")
        text_lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    if payload:
        text_lines.append("Payload:")
        text_lines.append(json.dumps(sanitized_payload, indent=2, ensure_ascii=False, sort_keys=True))
    text = "\n".join(text_lines)

    latest_path = surface_log_root(surface) / _safe_snapshot_filename(filename, default_suffix=".txt") if filename else surface_last_error_path(surface)
    write_text(latest_path, text)
    run_path: Path | None = None
    if safe_run:
        run_path = surface_run_log_dir(surface, safe_run) / _safe_snapshot_filename(filename or "error", default_suffix=".txt")
        write_text(run_path, text)
    log_surface_event(surface, "error", run_id=safe_run, level="ERROR", payload={"message": safe_message, "has_exception": exc is not None})
    return {
        "surface_id": surface,
        "latest_error": display_path(latest_path),
        "run_error": display_path(run_path) if run_path else None,
    }


def _file_payload(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": display_path(path),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _directory_payload(path: Path, *, file_limit: int = 10) -> dict[str, Any]:
    files = []
    if path.exists():
        files = sorted((p for p in path.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:file_limit]
    return {
        "name": path.name,
        "path": display_path(path),
        "file_count": sum(1 for p in path.iterdir() if p.is_file()) if path.exists() else 0,
        "latest_files": [_file_payload(p) for p in files],
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None,
    }


def _tail_text_file(path: Path, *, lines: int = 200, redact: bool = True) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    limit = max(1, min(int(lines or 200), 1000))
    try:
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except TypeError:  # Python versions before Path.read_text(errors=...) support.
        text_lines = path.open("r", encoding="utf-8", errors="replace").read().splitlines()
    tail = text_lines[-limit:]
    return [_redact_text_for_logs(line) for line in tail] if redact else tail


def _safe_tail_filename(value: str | None, *, fallback: str) -> str:
    if not value:
        return fallback
    raw = str(value).replace("\\", "/").split("/")[-1].strip()
    if raw in {"", ".", ".."}:
        return fallback
    return _safe_snapshot_filename(raw, default_suffix=Path(fallback).suffix or ".log")


def latest_surface_log_payload(surface_id: str) -> dict[str, Any]:
    """Return read-only metadata for one surface log root."""
    surface = safe_surface_id(surface_id)
    root = surface_log_root(surface)
    runs_root = root / "runs"
    latest_runs = []
    if runs_root.exists():
        latest_runs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    latest_files = sorted((p for p in root.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    event_log = surface_event_log_path(surface)
    last_error = surface_last_error_path(surface)
    nested_dirs = sorted((p for p in root.iterdir() if p.is_dir() and p.name != "runs"), key=lambda p: p.name)
    recent_runs = [_directory_payload(path, file_limit=6) for path in latest_runs]
    return {
        "schema_id": "neo.surface_runtime_logs.surface_payload.pass_n.v1",
        "surface_id": surface,
        "privacy_policy": surface_log_privacy_policy_payload(),
        "privacy_filtered": True,
        "log_root": display_path(root),
        "surface_endpoint": f"/api/debug/logs/{surface}",
        "tail_endpoint": f"/api/debug/logs/{surface}/tail",
        "event_log": display_path(event_log),
        "event_log_exists": event_log.exists(),
        "event_log_file": _file_payload(event_log) if event_log.exists() else None,
        "last_error": display_path(last_error),
        "last_error_exists": last_error.exists(),
        "last_error_file": _file_payload(last_error) if last_error.exists() else None,
        "latest_files": [_file_payload(path) for path in latest_files],
        "nested_directories": [_directory_payload(path) for path in nested_dirs],
        "recent_runs": recent_runs,
        # Compatibility with Pass L tests/UI.
        "runs": [display_path(path) for path in latest_runs],
    }


def surface_log_tail_payload(surface_id: str, *, lines: int = 200, file_name: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Return a read-only tail view for a surface log file.

    By default this tails ``<surface>_runtime_events.jsonl``. Supplying a
    ``run_id`` tails a file inside ``runs/<run_id>/``. ``file_name`` is basename
    sanitized so callers cannot traverse outside the chosen surface log root.
    """
    surface = safe_surface_id(surface_id)
    default_filename = surface_event_log_path(surface).name if not run_id else "events.jsonl"
    safe_filename = _safe_tail_filename(file_name, fallback=default_filename)
    if run_id:
        safe_run = safe_run_id(run_id)
        base = surface_run_log_dir(surface, safe_run)
        target = base / safe_filename
    else:
        safe_run = None
        base = surface_log_root(surface)
        target = base / safe_filename
    target = target.resolve()
    base_resolved = base.resolve()
    if base_resolved not in {target, *target.parents}:
        raise ValueError("Requested log file is outside the surface log root.")
    safe_lines = max(1, min(int(lines or 200), 1000))
    return {
        "ok": target.exists() and target.is_file(),
        "schema_id": "neo.surface_runtime_logs.tail.pass_n.v1",
        "read_only": True,
        "privacy_filtered": True,
        "privacy_policy": surface_log_privacy_policy_payload(),
        "surface_id": surface,
        "run_id": safe_run,
        "file": safe_filename,
        "path": display_path(target),
        "exists": target.exists() and target.is_file(),
        "line_limit": safe_lines,
        "lines": _tail_text_file(target, lines=safe_lines),
    }


def all_surface_logs_payload() -> dict[str, Any]:
    """Return read-only recursive metadata for all known surface log roots."""
    ensure_log_dirs()
    known = list(dict.fromkeys(KNOWN_SURFACE_LOG_IDS or ()))
    if LOG_ROOT.exists():
        for path in sorted(LOG_ROOT.iterdir()):
            if path.is_dir() and path.name not in known:
                known.append(path.name)
    root_files = sorted((p for p in LOG_ROOT.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:40] if LOG_ROOT.exists() else []
    return {
        "root": display_path(LOG_ROOT),
        "schema_id": "neo.surface_runtime_logs.discovery.pass_n.v1",
        "surface_generic_logging": True,
        "privacy_policy": surface_log_privacy_policy_payload(),
        "privacy_filtered": True,
        "recursive_log_discovery": True,
        "read_only": True,
        "surfaces_endpoint": "/api/debug/logs",
        "surface_count": len(known),
        "root_files": [_file_payload(path) for path in root_files],
        "surfaces": {surface: latest_surface_log_payload(surface) for surface in known},
    }


def log_image_event(event: str, *, run_id: str | None = None, level: str = "INFO", payload: dict[str, Any] | None = None) -> None:
    # Compatibility wrapper: keep the historical image event shape exactly the
    # same while the generic service handles other surfaces.
    logger = runtime_logger()
    message = f"image.{event} run_id={safe_run_id(run_id) if run_id else '-'}"
    getattr(logger, level.lower(), logger.info)(message)
    append_jsonl(EVENT_LOG_PATH, {"event": event, "level": level.upper(), "run_id": safe_run_id(run_id) if run_id else None, "payload": payload or {}})
    if run_id:
        append_jsonl(run_log_dir(run_id) / "events.jsonl", {"event": event, "level": level.upper(), "payload": payload or {}})


def record_compiled_workflow(*, run_id: str, provider_id: str, backend_payload: dict[str, Any]) -> dict[str, str]:
    """Persist the compiled Comfy graph and V2 payload snapshot.

    File names intentionally mirror the V1 debug artifacts while adding per-run
    folders:
      - neo_last_workflow.json
      - neo_last_payload.json
      - neo_last_generation_error.txt
    """
    folder = run_log_dir(run_id)
    prompt = backend_payload.get("prompt") or backend_payload.get("workflow") or {}
    compile_route = backend_payload.get("compile_route") or {}
    metadata = {
        "provider_id": provider_id,
        "run_id": safe_run_id(run_id),
        "client_id": backend_payload.get("client_id"),
        "workflow_type": (backend_payload.get("actual_params") or {}).get("workflow_type"),
        "family": (backend_payload.get("actual_params") or {}).get("family") or compile_route.get("family"),
        "loader": (backend_payload.get("actual_params") or {}).get("loader") or compile_route.get("loader"),
        "mode": (backend_payload.get("actual_params") or {}).get("mode") or compile_route.get("mode"),
        "compile_route": compile_route,
        "logged_at": _now_iso(),
    }
    workflow_payload = {"metadata": metadata, "prompt": prompt}
    payload_snapshot = {"metadata": metadata, "backend_payload": backend_payload}
    workflow_path = folder / "compiled_workflow.json"
    payload_path = folder / "backend_payload.json"
    write_json(workflow_path, workflow_payload)
    write_json(payload_path, payload_snapshot)
    debug_paths = {"workflow_path": str(workflow_path), "payload_path": str(payload_path)}
    extensions = backend_payload.get("extensions") if isinstance(backend_payload.get("extensions"), dict) else {}
    contamination_reports = extensions.get("contamination_reports") if isinstance(extensions.get("contamination_reports"), dict) else {}
    layer_report = contamination_reports.get("image.layerdiffuse") if isinstance(contamination_reports.get("image.layerdiffuse"), dict) else None
    if layer_report:
        contamination_path = folder / "graph_contamination_report.json"
        write_json(contamination_path, layer_report)
        debug_paths["graph_contamination_report_path"] = str(contamination_path)
    write_json(LAST_WORKFLOW_PATH, workflow_payload)
    write_json(LAST_PAYLOAD_PATH, payload_snapshot)
    log_image_event("compiled_workflow", run_id=run_id, payload={"workflow_path": display_path(workflow_path), "payload_path": display_path(payload_path), "graph_contamination_report_path": display_path(Path(debug_paths["graph_contamination_report_path"])) if debug_paths.get("graph_contamination_report_path") else "", "workflow_type": metadata.get("workflow_type")})
    return debug_paths


def record_queue_payload(*, run_id: str, request_payload: dict[str, Any], response_payload: dict[str, Any] | None = None) -> None:
    folder = run_log_dir(run_id)
    write_json(folder / "queue_request.json", request_payload)
    write_json(LAST_QUEUE_REQUEST_PATH, request_payload)
    # Debug convenience: this is the exact Comfy prompt graph submitted to /prompt.
    # It lets extension migrations prove whether a node was queued, not only compiled.
    prompt_graph = request_payload.get("prompt") if isinstance(request_payload, dict) else None
    if isinstance(prompt_graph, dict):
        write_json(LAST_COMFY_PROMPT_PATH, {"metadata": {"run_id": run_id, "logged_at": _now_iso()}, "prompt": prompt_graph})
    if response_payload is not None:
        write_json(folder / "queue_response.json", response_payload)
    log_image_event("queue_payload", run_id=run_id, payload={"has_response": response_payload is not None})


def record_poll_payload(*, run_id: str, job_id: str, source: str, payload: Any) -> None:
    folder = run_log_dir(run_id)
    safe_source = safe_run_id(source)
    write_json(folder / f"poll_{safe_source}.json", {"job_id": job_id, "source": source, "payload": payload, "logged_at": _now_iso()})
    log_image_event("poll_payload", run_id=run_id, payload={"job_id": job_id, "source": source})


def record_generation_error(*, run_id: str | None, message: str, exc: BaseException | None = None, payload: dict[str, Any] | None = None) -> None:
    text_lines = [f"[{_now_iso()}] {message}"]
    if exc is not None:
        text_lines.append(f"Exception: {type(exc).__name__}: {_safe_log_message(str(exc))}")
        text_lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    if payload:
        text_lines.append("Payload:")
        text_lines.append(json.dumps(_safe_jsonable(payload), indent=2, ensure_ascii=False, sort_keys=True))
    text = "\n".join(text_lines)
    write_text(LAST_ERROR_PATH, text)
    if run_id:
        write_text(run_log_dir(run_id) / "generation_error.txt", text)
    log_image_event("generation_error", run_id=run_id, level="ERROR", payload={"message": message, "has_exception": exc is not None})


def latest_log_payload() -> dict[str, Any]:
    ensure_log_dirs()
    latest_runs = []
    if IMAGE_RUN_LOG_ROOT.exists():
        latest_runs = sorted((p for p in IMAGE_RUN_LOG_ROOT.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    return {
        "log_root": display_path(LOG_ROOT),
        "image_log_root": display_path(IMAGE_LOG_ROOT),
        "console_log": display_path(CONSOLE_LOG_PATH),
        "server_log": display_path(SERVER_LOG_PATH),
        "error_log": display_path(ERROR_LOG_PATH),
        "generation_log": display_path(GENERATION_LOG_PATH),
        "event_log": display_path(EVENT_LOG_PATH),
        "last_workflow": display_path(LAST_WORKFLOW_PATH),
        "last_payload": display_path(LAST_PAYLOAD_PATH),
        "last_error": display_path(LAST_ERROR_PATH),
        "runs": [display_path(p) for p in latest_runs],
        "surface_generic_logging": True,
        "privacy_policy": surface_log_privacy_policy_payload(),
        "surface_log_payload": latest_surface_log_payload("image"),
    }

