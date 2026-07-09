from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.runtime_data import ROOT_DIR

SCHEMA_ID = "neo.runtime.generation_job_registry.v25_2"
INDEX_SCHEMA_ID = "neo.runtime.generation_job_registry_index.v25_2"
DEFAULT_STORAGE_RELATIVE = Path("neo_data") / "runtime" / "jobs"
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled"}
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_DEFAULT_REGISTRY: "GenerationJobRegistry | None" = None
_DEFAULT_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_job_id(value: Any) -> str:
    cleaned = _SAFE_ID_RE.sub("_", str(value or "").strip())
    return cleaned[:180] or f"job_{uuid4().hex[:10]}"


def safe_surface(value: Any) -> str:
    cleaned = _SAFE_ID_RE.sub("_", str(value or "global").strip().lower())
    return cleaned[:80] or "global"


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return deepcopy(value)


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _compact_backend_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep enough compile data for recovery/debugging without inventing a new format.

    The registry is a runtime store, so a full Comfy prompt graph is acceptable. Heavy
    binary/source fields are not expected here; JSON serialization still guards the write.
    """
    payload = payload if isinstance(payload, dict) else {}
    if not payload:
        return {}
    allowed = {
        "provider_id",
        "backend",
        "base_url",
        "validation",
        "prompt",
        "client_id",
        "actual_params",
        "runtime_progress_source",
        "compile_route",
        "capabilities",
        "prompt_conditioning",
        "extensions",
        "debug_run_id",
        "debug_log_paths",
    }
    return {key: _json_copy(value) for key, value in payload.items() if key in allowed}


class GenerationJobRegistry:
    """File-backed runtime registry for generation jobs across surfaces.

    Provider instances are allowed to be short-lived. This registry keeps the job
    contract, provider job id, runtime metadata, output expectations, and status in
    `neo_data/runtime/jobs/` so image/video polling can recover after a new adapter
    instance is created.
    """

    def __init__(self, root_dir: Path | str | None = None) -> None:
        self.root_dir = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
        self.storage_root = self.root_dir / DEFAULT_STORAGE_RELATIVE
        self._lock = threading.RLock()

    def _surface_dir(self, surface: Any) -> Path:
        return self.storage_root / safe_surface(surface)

    def _path(self, job_id: Any, surface: Any = "global") -> Path:
        return self._surface_dir(surface) / f"{safe_job_id(job_id)}.json"

    def _iter_paths(self) -> list[Path]:
        if not self.storage_root.exists():
            return []
        return sorted(self.storage_root.glob("*/*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        record = self.normalize(record)
        path = self._path(record.get("job_id"), record.get("surface"))
        path.parent.mkdir(parents=True, exist_ok=True)
        record["storage"] = {
            "root": str(self.storage_root),
            "path": str(path),
            "relative_path": str(path.relative_to(self.root_dir)) if path.is_relative_to(self.root_dir) else str(path),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
        return record

    def normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        record = dict(record or {})
        now = utc_now()
        job_id = str(record.get("job_id") or record.get("provider_job_id") or record.get("local_job_id") or "").strip()
        if not job_id:
            job_id = f"job_{uuid4().hex[:10]}"
        record["schema_id"] = SCHEMA_ID
        record["job_id"] = job_id
        record["provider_job_id"] = str(record.get("provider_job_id") or job_id)
        record["local_job_id"] = str(record.get("local_job_id") or record.get("submitted_job_id") or job_id)
        record["surface"] = safe_surface(record.get("surface") or "global")
        record["profile_id"] = str(record.get("profile_id") or record.get("backend_profile_id") or "")
        record["backend_profile_id"] = str(record.get("backend_profile_id") or record.get("profile_id") or "")
        record["provider_id"] = str(record.get("provider_id") or "")
        record["backend"] = str(record.get("backend") or "")
        record["mode"] = str(record.get("mode") or "")
        record["family"] = str(record.get("family") or "")
        record["loader"] = str(record.get("loader") or "")
        record["model"] = str(record.get("model") or "")
        record["status"] = str(record.get("status") or "queued")
        record["message"] = str(record.get("message") or "")
        record["created_at"] = str(record.get("created_at") or now)
        record["updated_at"] = now
        record.setdefault("submitted_at", record.get("created_at") or now)
        record.setdefault("started_at", "")
        record.setdefault("completed_at", "")
        record.setdefault("client_id", "")
        record.setdefault("submitted_job", {})
        record.setdefault("compiled_backend_payload", {})
        record.setdefault("runtime", {})
        record.setdefault("progress", {})
        record.setdefault("output_expectations", {})
        record.setdefault("outputs", [])
        record.setdefault("import_state", {"status": "not_started", "attempts": 0, "errors": []})
        record.setdefault("control", {"cancel_requested": False, "pause_requested": False})
        record.setdefault("events", [])
        return _json_copy(record)

    def get(self, job_id: Any, *, surface: Any | None = None) -> dict[str, Any] | None:
        job_id = safe_job_id(job_id)
        with self._lock:
            if surface:
                path = self._path(job_id, surface)
                if path.exists():
                    try:
                        return json.loads(path.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        return None
            for path in self._iter_paths():
                if path.stem == job_id:
                    try:
                        return json.loads(path.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        return None
        return None

    def upsert(self, job_id: Any, *, surface: Any = "global", updates: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            existing = self.get(job_id, surface=surface) or self.get(job_id) or {"job_id": str(job_id or ""), "surface": surface}
            merged = _deep_merge(existing, updates or {})
            return self._write(merged)

    def register_queued(
        self,
        *,
        job_id: Any,
        surface: Any,
        provider_id: str,
        profile_id: str = "",
        backend_profile_id: str = "",
        provider_job_id: str = "",
        local_job_id: str = "",
        backend: str = "",
        mode: str = "",
        family: str = "",
        loader: str = "",
        model: str = "",
        client_id: str = "",
        submitted_job: dict[str, Any] | None = None,
        compiled_backend_payload: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
        output_expectations: dict[str, Any] | None = None,
        message: str = "Queued.",
    ) -> dict[str, Any]:
        now = utc_now()
        runtime_payload = _json_copy(runtime or {})
        return self.upsert(
            job_id,
            surface=surface,
            updates={
                "job_id": str(job_id or provider_job_id or local_job_id or ""),
                "provider_job_id": str(provider_job_id or job_id or ""),
                "local_job_id": str(local_job_id or job_id or ""),
                "surface": surface,
                "provider_id": provider_id,
                "profile_id": profile_id or backend_profile_id,
                "backend_profile_id": backend_profile_id or profile_id,
                "backend": backend,
                "mode": mode,
                "family": family,
                "loader": loader,
                "model": model,
                "client_id": client_id,
                "status": "queued",
                "message": message,
                "submitted_at": now,
                "started_at": now,
                "submitted_job": _json_copy(submitted_job or {}),
                "compiled_backend_payload": _compact_backend_payload(compiled_backend_payload),
                "runtime": runtime_payload,
                "progress": runtime_payload.get("progress") if isinstance(runtime_payload.get("progress"), dict) else {},
                "output_expectations": _json_copy(output_expectations or {}),
                "events": [{"event": "queued", "at": now, "message": message}],
            },
        )

    def mark_running(
        self,
        job_id: Any,
        *,
        surface: Any | None = None,
        message: str = "Running.",
        runtime: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        poll_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        now = utc_now()
        merged_runtime = _deep_merge(existing.get("runtime") if isinstance(existing.get("runtime"), dict) else {}, runtime or {})
        if progress:
            merged_runtime["progress"] = _json_copy(progress)
        return self.upsert(
            job_id,
            surface=existing.get("surface") or surface or "global",
            updates={
                "status": "running",
                "message": message,
                "runtime": merged_runtime,
                "progress": _json_copy(progress or merged_runtime.get("progress") or {}),
                "poll_state": _json_copy(poll_state or {}),
                "events": list(existing.get("events") or []) + [{"event": "running", "at": now, "message": message}],
            },
        )

    def mark_completed(
        self,
        job_id: Any,
        *,
        surface: Any | None = None,
        message: str = "Completed.",
        outputs: list[dict[str, Any]] | None = None,
        runtime: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        now = utc_now()
        merged_runtime = _deep_merge(existing.get("runtime") if isinstance(existing.get("runtime"), dict) else {}, runtime or {})
        if progress:
            merged_runtime["progress"] = _json_copy(progress)
        return self.upsert(
            job_id,
            surface=existing.get("surface") or surface or "global",
            updates={
                "status": "completed",
                "message": message,
                "completed_at": now,
                "runtime": merged_runtime,
                "progress": _json_copy(progress or merged_runtime.get("progress") or {}),
                "outputs": _json_copy(outputs or []),
                "events": list(existing.get("events") or []) + [{"event": "completed", "at": now, "message": message, "output_count": len(outputs or [])}],
            },
        )

    def mark_output_import_state(
        self,
        job_id: Any,
        *,
        surface: Any | None = None,
        status: str,
        message: str = "",
        result_id: str = "",
        outputs: list[dict[str, Any]] | None = None,
        errors: list[str] | None = None,
        recoverable: bool | None = None,
        recovery_endpoint: str = "",
        increment_attempts: bool = False,
    ) -> dict[str, Any]:
        """Update the Neo-owned output import state for a provider job.

        Provider completion and Neo_Data persistence are two separate phases. This
        helper records whether Comfy/native outputs were imported, are recoverable,
        or failed to copy so polling no longer treats a backend-completed job as a
        normal Neo-completed result when files only exist in the backend folder.
        """
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        now = utc_now()
        previous = existing.get("import_state") if isinstance(existing.get("import_state"), dict) else {}
        previous_errors = previous.get("errors") if isinstance(previous.get("errors"), list) else []
        next_errors = [*previous_errors, *[str(item) for item in (errors or []) if str(item)]]
        attempts = int(previous.get("attempts") or 0) + (1 if increment_attempts else 0)
        import_state = {
            "schema_id": "neo.runtime.output_import_state.v25_3",
            "status": str(status or "unknown"),
            "message": str(message or ""),
            "updated_at": now,
            "attempts": attempts,
            "result_id": str(result_id or previous.get("result_id") or ""),
            "errors": next_errors[-20:],
            "output_count": len(outputs or []),
            "recoverable": bool(previous.get("recoverable") if recoverable is None else recoverable),
            "recovery_endpoint": str(recovery_endpoint or previous.get("recovery_endpoint") or ""),
        }
        updates: dict[str, Any] = {
            "import_state": import_state,
            "events": list(existing.get("events") or []) + [{
                "event": "output_import_state",
                "at": now,
                "status": import_state["status"],
                "message": import_state["message"],
                "output_count": import_state["output_count"],
            }],
        }
        if outputs is not None:
            updates["outputs"] = _json_copy(outputs)
        if status in {"imported", "completed", "neo_saved"}:
            updates["status"] = "completed"
            updates["message"] = message or existing.get("message") or "Output imported into Neo_Data."
        elif status in {"import_failed", "saved_in_comfy_only", "completed_no_outputs_recoverable"}:
            updates["status"] = str(status)
            updates["message"] = message or existing.get("message") or "Output import needs recovery."
        elif status == "importing":
            updates["status"] = "importing"
            updates["message"] = message or "Importing backend output into Neo_Data."
        return self.upsert(job_id, surface=existing.get("surface") or surface or "global", updates=updates)

    def mark_failed(
        self,
        job_id: Any,
        *,
        surface: Any | None = None,
        message: str = "Failed.",
        error: str = "",
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        now = utc_now()
        return self.upsert(
            job_id,
            surface=existing.get("surface") or surface or "global",
            updates={
                "status": "failed",
                "message": message,
                "error": error or message,
                "completed_at": now,
                "runtime": _deep_merge(existing.get("runtime") if isinstance(existing.get("runtime"), dict) else {}, runtime or {}),
                "events": list(existing.get("events") or []) + [{"event": "failed", "at": now, "message": message}],
            },
        )

    def mark_cancelled(self, job_id: Any, *, surface: Any | None = None, message: str = "Cancelled.", runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        now = utc_now()
        control = existing.get("control") if isinstance(existing.get("control"), dict) else {}
        control["cancel_requested"] = True
        return self.upsert(
            job_id,
            surface=existing.get("surface") or surface or "global",
            updates={
                "status": "cancelled",
                "message": message,
                "completed_at": now,
                "control": control,
                "runtime": _deep_merge(existing.get("runtime") if isinstance(existing.get("runtime"), dict) else {}, runtime or {}),
                "events": list(existing.get("events") or []) + [{"event": "cancelled", "at": now, "message": message}],
            },
        )

    def request_cancel(self, job_id: Any, *, surface: Any | None = None) -> dict[str, Any]:
        existing = self.get(job_id, surface=surface) or {"job_id": str(job_id or ""), "surface": surface or "global"}
        control = existing.get("control") if isinstance(existing.get("control"), dict) else {}
        control["cancel_requested"] = True
        return self.upsert(job_id, surface=existing.get("surface") or surface or "global", updates={"control": control, "message": "Cancel requested."})

    def upsert_from_provider_result(
        self,
        *,
        job: dict[str, Any] | None,
        result: dict[str, Any],
        profile_id: str = "",
        provider_id: str = "",
    ) -> dict[str, Any]:
        job = job if isinstance(job, dict) else {}
        result = result if isinstance(result, dict) else {}
        job_id = str(result.get("job_id") or job.get("job_id") or "")
        if not job_id:
            job_id = f"job_{uuid4().hex[:10]}"
        surface = job.get("surface") or "global"
        existing = self.get(job_id, surface=surface) or self.get(job_id) or {}

        def pick(field: str, *values: Any) -> Any:
            for value in values:
                if value not in (None, "", [], {}):
                    return value
            return existing.get(field) or ""

        status = str(result.get("status") or existing.get("status") or "queued")
        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        existing_runtime = existing.get("runtime") if isinstance(existing.get("runtime"), dict) else {}
        merged_runtime = _deep_merge(existing_runtime, runtime)
        outputs = result.get("outputs") if isinstance(result.get("outputs"), list) else existing.get("outputs") if isinstance(existing.get("outputs"), list) else []
        submitted_job = _json_copy(job) if job else existing.get("submitted_job") or {}
        updates = {
            "job_id": job_id,
            "surface": pick("surface", surface),
            "profile_id": pick("profile_id", profile_id, job.get("profile_id"), job.get("backend_profile_id")),
            "backend_profile_id": pick("backend_profile_id", profile_id, job.get("backend_profile_id"), job.get("profile_id")),
            "provider_id": pick("provider_id", provider_id, result.get("provider_id"), job.get("provider_id")),
            "provider_job_id": pick("provider_job_id", job_id),
            "local_job_id": pick("local_job_id", job.get("job_id"), job_id),
            "mode": pick("mode", job.get("mode")),
            "family": pick("family", job.get("family")),
            "loader": pick("loader", job.get("loader")),
            "model": pick("model", job.get("model")),
            "client_id": pick("client_id", result.get("client_id"), runtime.get("client_id")),
            "status": status,
            "message": result.get("message") or existing.get("message") or "",
            "submitted_job": submitted_job,
            "runtime": _json_copy(merged_runtime),
            "progress": merged_runtime.get("progress") if isinstance(merged_runtime.get("progress"), dict) else existing.get("progress") if isinstance(existing.get("progress"), dict) else {},
            "outputs": _json_copy(outputs),
        }
        if status in _TERMINAL_STATUSES:
            updates["completed_at"] = utc_now()
        return self.upsert(job_id, surface=surface, updates=updates)

    def summary(self, job_id: Any, *, surface: Any | None = None) -> dict[str, Any]:
        record = self.get(job_id, surface=surface) or {}
        if not record:
            return {"schema_id": SCHEMA_ID, "ok": False, "job_id": str(job_id or ""), "status": "missing", "storage_root": str(self.storage_root)}
        storage = record.get("storage") if isinstance(record.get("storage"), dict) else {}
        return {
            "schema_id": SCHEMA_ID,
            "ok": True,
            "job_id": record.get("job_id") or "",
            "surface": record.get("surface") or "",
            "profile_id": record.get("profile_id") or "",
            "provider_id": record.get("provider_id") or "",
            "provider_job_id": record.get("provider_job_id") or "",
            "local_job_id": record.get("local_job_id") or "",
            "status": record.get("status") or "",
            "message": record.get("message") or "",
            "updated_at": record.get("updated_at") or "",
            "client_id": record.get("client_id") or "",
            "output_count": len(record.get("outputs") or []),
            "storage": storage,
            "progress": record.get("progress") if isinstance(record.get("progress"), dict) else {},
            "import_state": record.get("import_state") if isinstance(record.get("import_state"), dict) else {},
            "control": record.get("control") if isinstance(record.get("control"), dict) else {},
        }

    def list_recent(self, *, surface: str | None = None, limit: int = 50) -> dict[str, Any]:
        paths = self._iter_paths()
        items: list[dict[str, Any]] = []
        for path in paths:
            if surface and path.parent.name != safe_surface(surface):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items.append(self.summary(record.get("job_id"), surface=record.get("surface")))
            if len(items) >= max(1, int(limit)):
                break
        return {"schema_id": INDEX_SCHEMA_ID, "storage_root": str(self.storage_root), "count": len(items), "items": items}


def get_generation_job_registry(root_dir: Path | str | None = None) -> GenerationJobRegistry:
    global _DEFAULT_REGISTRY
    if root_dir is not None:
        return GenerationJobRegistry(root_dir)
    with _DEFAULT_LOCK:
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = GenerationJobRegistry(ROOT_DIR)
        return _DEFAULT_REGISTRY
