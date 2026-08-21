from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event, Thread
from typing import Any
from urllib import error, request
from uuid import uuid4
import json
import os
import shutil
import time

from .download_planner import build_download_plan
from .huggingface_disk_preflight import build_huggingface_snapshot_disk_preflight
from .huggingface_snapshot_installer import (
    HuggingFaceSnapshotInstallError,
    build_huggingface_snapshot_install_request,
    install_huggingface_snapshot,
)
from .huggingface_snapshot_probe import probe_huggingface_snapshot_install_request
from .model_paths import load_model_paths

ROOT_DIR = Path(__file__).resolve().parents[3]
DOWNLOADS_ROOT = ROOT_DIR / "neo_data" / "downloads"
DOWNLOAD_JOBS_PATH = DOWNLOADS_ROOT / "download_jobs.json"

DOWNLOAD_JOBS_SCHEMA_ID = "neo.admin.models.download_jobs.v1"
DOWNLOAD_START_SCHEMA_ID = "neo.admin.models.download_start.v1"
DOWNLOAD_CANCEL_SCHEMA_ID = "neo.admin.models.download_cancel.v1"
DOWNLOAD_JOB_SCHEMA_ID = "neo.admin.models.download_job.v1"
PHASE_ID = "phase8_download_manager"
USER_AGENT = "Neo-Studio-Model-Guide/phase8"
DEFAULT_CHUNK_SIZE = 1024 * 1024

_CANCEL_EVENTS: dict[str, Event] = {}
_JOB_THREADS: dict[str, Thread] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _normalized_fs_path(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))
    except Exception:
        return os.path.normcase(os.path.normpath(text))


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _safe_job_id(value: Any = "") -> str:
    text = _clean(value)
    if text and all(ch.isalnum() or ch in {"-", "_"} for ch in text):
        return text[:96]
    return uuid4().hex


def _filename_from_path(path: str) -> str:
    return PurePosixPath(_clean(path).replace("\\", "/")).name


def _safe_filename(filename: str) -> str:
    name = _filename_from_path(filename)
    if name in {"", ".", ".."}:
        return ""
    if "/" in name or "\\" in name or "\x00" in name:
        return ""
    return name


def _path_is_windows_absolute(path: str) -> bool:
    return bool(PureWindowsPath(path).drive)


def _rooted_path(path: str) -> Path:
    text = _clean(path)
    if not text:
        return ROOT_DIR
    candidate = Path(text)
    if not candidate.is_absolute() and not _path_is_windows_absolute(text):
        candidate = ROOT_DIR / candidate
    return candidate


def _configured_download_roots() -> dict[str, Path]:
    paths = load_model_paths(create=True)
    download = _as_dict(paths.get("download"))
    return {
        "tmp": _rooted_path(_clean(download.get("temp_root")) or "neo_data/downloads/tmp"),
        "completed": _rooted_path(_clean(download.get("completed_root")) or "neo_data/downloads/completed"),
        "failed": _rooted_path(_clean(download.get("failed_root")) or "neo_data/downloads/failed"),
    }


def _ensure_download_roots() -> dict[str, Path]:
    roots = _configured_download_roots()
    DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    return roots


def _default_store() -> dict[str, Any]:
    return {
        "schema_id": DOWNLOAD_JOBS_SCHEMA_ID,
        "version": "0.8.0-phase8",
        "created_at": _now(),
        "updated_at": _now(),
        "policy": "Download jobs are local runtime state stored under neo_data/downloads. Tokens are never persisted.",
        "jobs": [],
    }


def _read_store() -> dict[str, Any]:
    if not DOWNLOAD_JOBS_PATH.exists():
        return _default_store()
    try:
        payload = json.loads(DOWNLOAD_JOBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_store()
    if not isinstance(payload, dict):
        return _default_store()
    if payload.get("schema_id") != DOWNLOAD_JOBS_SCHEMA_ID:
        payload["schema_id"] = DOWNLOAD_JOBS_SCHEMA_ID
    if not isinstance(payload.get("jobs"), list):
        payload["jobs"] = []
    return payload


def _write_store(store: dict[str, Any]) -> None:
    DOWNLOAD_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = _now()
    DOWNLOAD_JOBS_PATH.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def _redact_job(job: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(job)
    clean.pop("token", None)
    clean.pop("headers", None)
    source = _as_dict(clean.get("source"))
    for key in list(source.keys()):
        if "token" in key.lower() or "authorization" in key.lower():
            source.pop(key, None)
    clean["source"] = source
    return clean


def _upsert_job(job: dict[str, Any]) -> dict[str, Any]:
    store = _read_store()
    job = _redact_job(job)
    jobs = [_as_dict(item) for item in _as_list(store.get("jobs"))]
    replaced = False
    for index, existing in enumerate(jobs):
        if existing.get("job_id") == job.get("job_id"):
            jobs[index] = job
            replaced = True
            break
    if not replaced:
        jobs.append(job)
    store["jobs"] = jobs[-250:]
    _write_store(store)
    return job


def _update_job(job_id: str, **changes: Any) -> dict[str, Any] | None:
    store = _read_store()
    jobs = [_as_dict(item) for item in _as_list(store.get("jobs"))]
    updated: dict[str, Any] | None = None
    for job in jobs:
        if job.get("job_id") != job_id:
            continue
        job.update(changes)
        job["updated_at"] = _now()
        updated = _redact_job(job)
        job.clear()
        job.update(updated)
        break
    if updated is not None:
        store["jobs"] = jobs
        _write_store(store)
    return updated


def _get_job(job_id: str) -> dict[str, Any] | None:
    for job in _as_list(_read_store().get("jobs")):
        if _as_dict(job).get("job_id") == job_id:
            return _redact_job(_as_dict(job))
    return None


def _sanitize_download_plan(plan: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(plan)
    clean.pop("token", None)
    for section_key in ("source", "file", "target"):
        section = _as_dict(clean.get(section_key))
        for key in list(section.keys()):
            if "token" in key.lower() or "authorization" in key.lower():
                section.pop(key, None)
        clean[section_key] = section
    return clean


def _validate_plan_for_download(plan: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not bool(plan.get("ok")):
        errors.append("download_plan_not_ready")
    source = _as_dict(plan.get("source"))
    target = _as_dict(plan.get("target"))
    file_info = _as_dict(plan.get("file"))
    provider = _clean_lower(source.get("provider"))
    if provider not in {"huggingface", "civitai"}:
        errors.append(f"unsupported_download_provider:{provider or 'unknown'}")
    if not _clean(source.get("download_url")):
        errors.append("download_url_missing")
    if not _safe_filename(file_info.get("filename")):
        errors.append("safe_filename_missing")
    if not _clean(target.get("folder_path")) or not _clean(target.get("final_path")):
        errors.append("target_path_missing")
    if _as_dict(source).get("requires_token") and not _clean(payload.get("token")):
        warnings.append("source_requires_token_but_no_session_token_was_supplied")
    return not errors, warnings, errors


def _plan_from_start_payload(payload: dict[str, Any]) -> dict[str, Any]:
    plan = _as_dict(payload.get("plan"))
    if plan:
        return plan
    plan_payload = _as_dict(payload.get("plan_payload")) or payload
    return build_download_plan(plan_payload)


def _job_from_plan(plan: dict[str, Any], payload: dict[str, Any], *, status: str) -> dict[str, Any]:
    job_id = _safe_job_id(payload.get("job_id"))
    source = _as_dict(plan.get("source"))
    target = _as_dict(plan.get("target"))
    file_info = _as_dict(plan.get("file"))
    size_bytes = _safe_int(file_info.get("size_bytes"), default=0)
    stamp = _now()
    filename = _safe_filename(file_info.get("filename"))
    roots = _ensure_download_roots()
    tmp_path = roots["tmp"] / f"{job_id}.{filename}.part"
    return {
        "schema_id": DOWNLOAD_JOB_SCHEMA_ID,
        "phase": PHASE_ID,
        "job_id": job_id,
        "status": status,
        "created_at": stamp,
        "updated_at": stamp,
        "catalog_id": _clean(plan.get("catalog_id")),
        "display_name": _clean(_as_dict(plan.get("record")).get("display_name")),
        "provider": _clean_lower(source.get("provider")),
        "source": {
            "provider": _clean_lower(source.get("provider")),
            "download_url": _clean(source.get("download_url")),
            "source_url": _clean(source.get("source_url")),
            "repo": _clean(source.get("repo")),
            "revision": _clean(source.get("revision")),
            "model_id": _clean(source.get("model_id")),
            "version_id": _clean(source.get("version_id")),
            "file_id": _clean(source.get("file_id")),
            "requires_token": bool(source.get("requires_token")),
        },
        "file": {
            "filename": filename,
            "extension": _clean_lower(file_info.get("extension")),
            "size_bytes": size_bytes,
            "hashes": _as_dict(file_info.get("hashes")),
        },
        "target": {
            "backend": _clean_lower(target.get("backend")),
            "target_type": _clean_lower(target.get("target_type")),
            "folder_path": _clean(target.get("folder_path")),
            "final_path": _clean(target.get("final_path")),
        },
        "paths": {
            "tmp_path": str(tmp_path),
            "final_path": _clean(target.get("final_path")),
        },
        "progress": {
            "bytes_downloaded": 0,
            "size_bytes": size_bytes,
            "percent": 0,
        },
        "warnings": sorted(set([str(item) for item in _as_list(plan.get("warnings"))])),
        "errors": [],
        "plan": _sanitize_download_plan(plan),
        "policy": {
            "confirmed": bool(payload.get("confirmed")),
            "dry_run": bool(payload.get("dry_run")),
            "overwrite": bool(payload.get("overwrite")),
            "tokens_saved": False,
            "remote_metadata_saved": False,
            "preview_images_saved": False,
        },
    }


def _job_from_snapshot_request(
    request_payload: dict[str, Any],
    payload: dict[str, Any],
    *,
    status: str,
    disk_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job_id = _safe_job_id(payload.get("job_id"))
    source = _as_dict(request_payload.get("source"))
    install = _as_dict(request_payload.get("install"))
    cache = _as_dict(request_payload.get("cache"))
    backend_targets = [_clean_lower(item) for item in _as_list(install.get("backend_targets")) if _clean(item)]
    expected_size_mb = _safe_int(install.get("expected_size_mb"), default=0)
    expected_size_bytes = expected_size_mb * 1024 * 1024 if expected_size_mb > 0 else 0
    stamp = _now()
    return {
        "schema_id": DOWNLOAD_JOB_SCHEMA_ID,
        "phase": "phase4_5_4_huggingface_snapshot_disk_preflight",
        "job_id": job_id,
        "job_kind": "repository_snapshot",
        "installer": "huggingface_snapshot",
        "status": status,
        "created_at": stamp,
        "updated_at": stamp,
        "catalog_id": _clean(request_payload.get("catalog_id")),
        "display_name": _clean(request_payload.get("display_name")),
        "provider": "huggingface",
        "source": {
            "provider": "huggingface",
            "repo": _clean(source.get("repo")),
            "revision": _clean(source.get("revision")) or "main",
            "source_url": _clean(source.get("source_url")),
            "requires_token": bool(source.get("requires_token")),
        },
        "file": {
            "filename": "",
            "extension": "",
            "size_bytes": expected_size_bytes,
            "hashes": {},
        },
        "target": {
            "backend": backend_targets[0] if backend_targets else "",
            "target_type": "hf_cache",
            "folder_path": _clean(cache.get("hub_cache")),
            "final_path": "",
        },
        "paths": {
            "cache_dir": _clean(cache.get("hub_cache")),
            "snapshot_path": "",
            "final_path": "",
        },
        "progress": {
            "bytes_downloaded": 0,
            "size_bytes": expected_size_bytes,
            "percent": 0,
            "indeterminate": True,
            "stage": "planned" if status == "planned" else "queued",
        },
        "warnings": sorted(set([str(item) for item in _as_list(request_payload.get("warnings"))])),
        "errors": [],
        "snapshot_request": deepcopy(request_payload),
        "disk_preflight": deepcopy(_as_dict(disk_preflight)),
        "policy": {
            "confirmed": bool(payload.get("confirmed")),
            "dry_run": bool(payload.get("dry_run")),
            "overwrite": False,
            "tokens_saved": False,
            "remote_metadata_saved": False,
            "preview_images_saved": False,
            "huggingface_owns_cache_layout": True,
            "uses_local_dir": False,
            "cancel_mode": "cooperative_after_current_huggingface_transfer",
            "disk_preflight": True,
            "disk_preflight_fail_closed": True,
            "disk_preflight_recheck_before_transfer": True,
            "authoritative_installed_probe": True,
        },
    }


def _snapshot_worker(job_id: str, *, token: str = "") -> None:
    job = _get_job(job_id)
    if not job:
        return
    cancel_event = _CANCEL_EVENTS.setdefault(job_id, Event())
    start_time = time.time()
    try:
        if cancel_event.is_set():
            raise InterruptedError("snapshot_install_cancelled_before_start")
        _update_job(
            job_id,
            status="preparing",
            started_at=_now(),
            progress={**_as_dict(job.get("progress")), "indeterminate": True, "stage": "checking_disk"},
        )
        fresh_preflight = build_huggingface_snapshot_disk_preflight(_as_dict(job.get("snapshot_request")))
        _update_job(job_id, disk_preflight=fresh_preflight)
        if not bool(fresh_preflight.get("ok")):
            _update_job(
                job_id,
                status="failed",
                failed_at=_now(),
                errors=[str(item) for item in _as_list(fresh_preflight.get("errors"))] or ["disk_space_check_failed"],
                error_message=_clean(fresh_preflight.get("message")) or "Disk-space preflight failed before Hugging Face transfer.",
                progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "failed"},
            )
            return
        if cancel_event.is_set():
            raise InterruptedError("snapshot_install_cancelled_before_transfer")
        _update_job(
            job_id,
            status="preparing",
            progress={**_as_dict(job.get("progress")), "indeterminate": True, "stage": "preparing"},
        )
        _update_job(
            job_id,
            status="downloading",
            progress={**_as_dict(job.get("progress")), "indeterminate": True, "stage": "downloading"},
        )
        result = install_huggingface_snapshot(_as_dict(job.get("snapshot_request")), token=token)
        snapshot_path = _clean(_as_dict(result.get("cache")).get("snapshot_path"))
        source_result = _as_dict(result.get("source"))
        elapsed = max(time.time() - start_time, 0.001)
        if cancel_event.is_set():
            _update_job(
                job_id,
                status="cancelled",
                cancelled_at=_now(),
                paths={**_as_dict(job.get("paths")), "snapshot_path": snapshot_path},
                warnings=sorted(set([*_as_list(job.get("warnings")), "cancel_requested_after_huggingface_transfer_started_snapshot_may_remain_cached"])),
                errors=["snapshot_install_cancelled"],
                progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "cancelled", "elapsed_seconds": round(elapsed, 3)},
            )
            return
        _update_job(
            job_id,
            status="verifying",
            paths={**_as_dict(job.get("paths")), "snapshot_path": snapshot_path},
            progress={**_as_dict(job.get("progress")), "indeterminate": True, "stage": "verifying", "elapsed_seconds": round(elapsed, 3)},
        )
        snapshot_probe = probe_huggingface_snapshot_install_request(_as_dict(job.get("snapshot_request")))
        probe_state = _clean_lower(snapshot_probe.get("state"))
        probe_snapshot_path = _clean(_as_dict(snapshot_probe.get("cache")).get("snapshot_path"))
        if probe_state != "installed":
            probe_errors = [str(item) for item in _as_list(snapshot_probe.get("errors")) if str(item).strip()]
            _update_job(
                job_id,
                status="failed",
                failed_at=_now(),
                paths={**_as_dict(job.get("paths")), "snapshot_path": probe_snapshot_path or snapshot_path},
                errors=probe_errors or [f"huggingface_snapshot_probe_{probe_state or 'failed'}"],
                error_message=_clean(snapshot_probe.get("message")) or "The downloaded Hugging Face snapshot did not pass installed-state verification.",
                verification=snapshot_probe,
                progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "failed", "elapsed_seconds": round(elapsed, 3)},
            )
            return
        if probe_snapshot_path and snapshot_path and _normalized_fs_path(probe_snapshot_path) != _normalized_fs_path(snapshot_path):
            _update_job(
                job_id,
                status="failed",
                failed_at=_now(),
                paths={**_as_dict(job.get("paths")), "snapshot_path": probe_snapshot_path},
                errors=["huggingface_snapshot_verification_path_mismatch"],
                error_message="Hugging Face returned one snapshot path, but the authoritative cache ref resolved to a different path.",
                verification=snapshot_probe,
                progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "failed", "elapsed_seconds": round(elapsed, 3)},
            )
            return
        resolved_revision = _clean(_as_dict(snapshot_probe.get("source")).get("resolved_revision")) or _clean(source_result.get("resolved_revision"))
        _update_job(
            job_id,
            status="completed",
            completed_at=_now(),
            source={**_as_dict(job.get("source")), "resolved_revision": resolved_revision},
            paths={**_as_dict(job.get("paths")), "snapshot_path": probe_snapshot_path or snapshot_path},
            progress={
                **_as_dict(job.get("progress")),
                "bytes_downloaded": _safe_int(_as_dict(job.get("progress")).get("size_bytes"), 0),
                "percent": 100,
                "indeterminate": False,
                "stage": "completed",
                "eta_seconds": 0,
                "elapsed_seconds": round(elapsed, 3),
            },
            metrics={"elapsed_seconds": round(elapsed, 3)},
            verification=snapshot_probe,
        )
    except InterruptedError:
        _update_job(job_id, status="cancelled", cancelled_at=_now(), errors=["snapshot_install_cancelled"], progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "cancelled"})
    except HuggingFaceSnapshotInstallError as exc:
        _update_job(job_id, status="failed", failed_at=_now(), errors=[exc.code], error_message=exc.message, progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "failed"})
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        _update_job(job_id, status="failed", failed_at=_now(), errors=[f"huggingface_snapshot_worker_failed:{type(exc).__name__}"], error_message="Hugging Face snapshot installation failed unexpectedly.", progress={**_as_dict(job.get("progress")), "indeterminate": False, "stage": "failed"})
    finally:
        _CANCEL_EVENTS.pop(job_id, None)
        _JOB_THREADS.pop(job_id, None)


def _start_huggingface_snapshot_install(payload: dict[str, Any]) -> dict[str, Any]:
    request_payload = build_huggingface_snapshot_install_request(payload)
    errors = [str(item) for item in _as_list(request_payload.get("errors"))]
    warnings = [str(item) for item in _as_list(request_payload.get("warnings"))]
    disk_preflight = build_huggingface_snapshot_disk_preflight(request_payload) if bool(request_payload.get("ok")) else {}
    warnings.extend(str(item) for item in _as_list(_as_dict(disk_preflight).get("warnings")))
    if disk_preflight and not bool(disk_preflight.get("ok")):
        errors.extend(str(item) for item in _as_list(disk_preflight.get("errors")))
    if not bool(payload.get("confirmed")):
        errors.append("download_confirmation_required")
    if not bool(request_payload.get("ok")) or errors:
        status = "insufficient_disk_space" if "insufficient_disk_space" in errors else "needs_attention"
        return {
            "schema_id": DOWNLOAD_START_SCHEMA_ID,
            "ok": False,
            "status": status,
            "phase": "phase4_5_4_huggingface_snapshot_disk_preflight",
            "snapshot_request": request_payload,
            "disk_preflight": disk_preflight,
            "warnings": sorted(set(warnings)),
            "errors": sorted(set(errors)),
            "capabilities": {
                "download_manager": True,
                "repository_snapshot_install": True,
                "huggingface_snapshot_download": True,
                "disk_preflight": True,
                "requires_confirmation": True,
                "stores_tokens": False,
            },
        }

    dry_run = bool(payload.get("dry_run"))
    job = _job_from_snapshot_request(
        request_payload,
        payload,
        status="planned" if dry_run else "queued",
        disk_preflight=disk_preflight,
    )
    _upsert_job(job)
    if not dry_run:
        token = _clean(payload.get("token"))
        cancel_event = Event()
        _CANCEL_EVENTS[job["job_id"]] = cancel_event
        thread = Thread(target=_snapshot_worker, kwargs={"job_id": job["job_id"], "token": token}, daemon=True)
        _JOB_THREADS[job["job_id"]] = thread
        thread.start()

    return {
        "schema_id": DOWNLOAD_START_SCHEMA_ID,
        "ok": True,
        "status": job["status"],
        "phase": "phase4_5_4_huggingface_snapshot_disk_preflight",
        "job": _redact_job(job),
        "warnings": job.get("warnings", []),
        "errors": [],
        "capabilities": {
            "download_manager": True,
            "repository_snapshot_install": True,
            "huggingface_snapshot_download": True,
            "background_jobs": True,
            "cancel": "cooperative",
            "retry": False,
            "stores_tokens": False,
            "disk_preflight": True,
            "disk_preflight_recheck_before_transfer": True,
            "authoritative_installed_probe": True,
        },
    }


def _download_headers(provider: str, token: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if token:
        if provider == "civitai":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _safe_remove(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


def _copy_or_move_completed_file(tmp_path: Path, final_path_text: str, *, overwrite: bool) -> None:
    final_path = Path(final_path_text)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if final_path.exists() and not overwrite:
        raise FileExistsError(f"Target file already exists: {final_path}")
    if final_path.exists() and overwrite:
        final_path.unlink()
    shutil.move(str(tmp_path), str(final_path))


def _download_worker(job_id: str, *, token: str = "", overwrite: bool = False, timeout: int = 60) -> None:
    job = _get_job(job_id)
    if not job:
        return
    source = _as_dict(job.get("source"))
    paths = _as_dict(job.get("paths"))
    progress = _as_dict(job.get("progress"))
    download_url = _clean(source.get("download_url"))
    tmp_path = Path(_clean(paths.get("tmp_path")))
    final_path = _clean(paths.get("final_path"))
    provider = _clean_lower(source.get("provider"))
    cancel_event = _CANCEL_EVENTS.setdefault(job_id, Event())
    start_time = time.time()
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        _update_job(job_id, status="downloading", started_at=_now())
        req = request.Request(download_url, headers=_download_headers(provider, token))
        with request.urlopen(req, timeout=max(5, int(timeout))) as response:  # noqa: S310 - confirmed user model download
            size_header = response.headers.get("Content-Length")
            expected_size = _safe_int(size_header, default=_safe_int(progress.get("size_bytes"), 0))
            downloaded = 0
            with tmp_path.open("wb") as handle:
                while True:
                    if cancel_event.is_set():
                        raise InterruptedError("download_cancelled")
                    chunk = response.read(DEFAULT_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    elapsed = max(time.time() - start_time, 0.001)
                    speed = downloaded / elapsed if downloaded else 0
                    remaining = max(expected_size - downloaded, 0) if expected_size else 0
                    eta_seconds = int(remaining / speed) if speed and remaining else 0
                    percent = int((downloaded / expected_size) * 100) if expected_size else 0
                    _update_job(
                        job_id,
                        progress={
                            "bytes_downloaded": downloaded,
                            "size_bytes": expected_size,
                            "percent": min(max(percent, 0), 100),
                            "speed_bytes_per_second": round(speed, 2),
                            "eta_seconds": eta_seconds,
                            "elapsed_seconds": round(elapsed, 3),
                        },
                    )
        if cancel_event.is_set():
            raise InterruptedError("download_cancelled")
        _copy_or_move_completed_file(tmp_path, final_path, overwrite=overwrite)
        elapsed = max(time.time() - start_time, 0.001)
        final_size = Path(final_path).stat().st_size if Path(final_path).exists() else _safe_int(progress.get("size_bytes"), 0)
        average_speed = final_size / elapsed if final_size else 0
        _update_job(
            job_id,
            status="completed",
            completed_at=_now(),
            progress={
                "bytes_downloaded": final_size,
                "size_bytes": final_size,
                "percent": 100,
                "speed_bytes_per_second": round(average_speed, 2),
                "eta_seconds": 0,
                "elapsed_seconds": round(elapsed, 3),
            },
            metrics={"elapsed_seconds": round(elapsed, 3), "average_speed_bytes_per_second": round(average_speed, 2)},
        )
    except InterruptedError:
        _safe_remove(tmp_path)
        _update_job(job_id, status="cancelled", cancelled_at=_now(), errors=["download_cancelled"])
    except Exception as exc:
        _safe_remove(tmp_path)
        _update_job(job_id, status="failed", failed_at=_now(), errors=[str(exc)])
    finally:
        _CANCEL_EVENTS.pop(job_id, None)
        _JOB_THREADS.pop(job_id, None)


def start_model_download(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    if bool(data.get("snapshot_install")):
        return _start_huggingface_snapshot_install(data)
    plan = _plan_from_start_payload(data)
    ok, warnings, errors = _validate_plan_for_download(plan, data)
    if not bool(data.get("confirmed")):
        errors.append("download_confirmation_required")
    if not ok or errors:
        return {
            "schema_id": DOWNLOAD_START_SCHEMA_ID,
            "ok": False,
            "status": "needs_attention",
            "phase": PHASE_ID,
            "plan": _sanitize_download_plan(plan),
            "warnings": sorted(set(warnings)),
            "errors": sorted(set(errors)),
            "capabilities": {
                "download_manager": True,
                "actual_downloads": True,
                "requires_confirmation": True,
                "stores_tokens": False,
            },
        }

    overwrite = bool(data.get("overwrite"))
    dry_run = bool(data.get("dry_run"))
    job = _job_from_plan(plan, data, status="planned" if dry_run else "queued")
    job["warnings"] = sorted(set([*job.get("warnings", []), *warnings]))

    final_path = Path(_clean(_as_dict(job.get("paths")).get("final_path")))
    if final_path.exists() and not overwrite:
        job["status"] = "blocked"
        job["errors"] = ["target_file_already_exists"]
        _upsert_job(job)
        return {
            "schema_id": DOWNLOAD_START_SCHEMA_ID,
            "ok": False,
            "status": "blocked",
            "phase": PHASE_ID,
            "job": job,
            "errors": ["target_file_already_exists"],
            "warnings": job.get("warnings", []),
        }

    _upsert_job(job)
    if not dry_run:
        token = _clean(data.get("token"))
        timeout = min(max(_safe_int(data.get("timeout_seconds"), 60), 5), 3600)
        cancel_event = Event()
        _CANCEL_EVENTS[job["job_id"]] = cancel_event
        thread = Thread(target=_download_worker, kwargs={"job_id": job["job_id"], "token": token, "overwrite": overwrite, "timeout": timeout}, daemon=True)
        _JOB_THREADS[job["job_id"]] = thread
        thread.start()

    return {
        "schema_id": DOWNLOAD_START_SCHEMA_ID,
        "ok": True,
        "status": job["status"],
        "phase": PHASE_ID,
        "job": _redact_job(job),
        "warnings": job.get("warnings", []),
        "errors": [],
        "capabilities": {
            "download_manager": True,
            "actual_downloads": True,
            "background_jobs": True,
            "cancel": True,
            "retry": False,
            "stores_tokens": False,
        },
    }


def list_model_download_jobs(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    status_filter = _clean_lower(data.get("status"))
    catalog_id = _clean(data.get("catalog_id"))
    jobs = [_redact_job(_as_dict(item)) for item in _as_list(_read_store().get("jobs"))]
    if status_filter:
        jobs = [job for job in jobs if _clean_lower(job.get("status")) == status_filter]
    if catalog_id:
        jobs = [job for job in jobs if _clean(job.get("catalog_id")) == catalog_id]
    jobs = list(reversed(jobs))
    return {
        "schema_id": DOWNLOAD_JOBS_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "phase": PHASE_ID,
        "store": {
            "exists": DOWNLOAD_JOBS_PATH.exists(),
            "path": _display_path(DOWNLOAD_JOBS_PATH),
            "policy": "local_only_gitignored_neo_data",
        },
        "jobs": jobs,
        "summary": {
            "count": len(jobs),
            "active_count": sum(1 for job in jobs if job.get("status") in {"queued", "preparing", "downloading", "verifying", "cancelling"}),
            "completed_count": sum(1 for job in jobs if job.get("status") == "completed"),
            "failed_count": sum(1 for job in jobs if job.get("status") == "failed"),
        },
        "capabilities": {
            "download_manager": True,
            "actual_downloads": True,
            "background_jobs": True,
            "cancel": True,
            "tokens_saved": False,
        },
    }


def get_model_download_job(job_id: str) -> dict[str, Any]:
    safe_id = _safe_job_id(job_id)
    job = _get_job(safe_id)
    if not job:
        return {
            "schema_id": DOWNLOAD_JOB_SCHEMA_ID,
            "ok": False,
            "status": "not_found",
            "phase": PHASE_ID,
            "job_id": safe_id,
            "errors": ["download_job_not_found"],
        }
    return {
        "schema_id": DOWNLOAD_JOB_SCHEMA_ID,
        "ok": True,
        "status": job.get("status"),
        "phase": PHASE_ID,
        "job": job,
    }


def cancel_model_download(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    job_id = _safe_job_id(data.get("job_id"))
    job = _get_job(job_id)
    if not job:
        return {
            "schema_id": DOWNLOAD_CANCEL_SCHEMA_ID,
            "ok": False,
            "status": "not_found",
            "phase": PHASE_ID,
            "job_id": job_id,
            "errors": ["download_job_not_found"],
        }
    if job.get("status") not in {"queued", "preparing", "downloading", "verifying"}:
        return {
            "schema_id": DOWNLOAD_CANCEL_SCHEMA_ID,
            "ok": False,
            "status": "not_cancellable",
            "phase": PHASE_ID,
            "job": job,
            "errors": [f"download_job_status_not_cancellable:{job.get('status')}"],
        }
    event = _CANCEL_EVENTS.get(job_id)
    if event:
        event.set()
        changes: dict[str, Any] = {"status": "cancelling", "cancel_requested_at": _now()}
        if job.get("job_kind") == "repository_snapshot":
            changes["warnings"] = sorted(set([*_as_list(job.get("warnings")), "huggingface_snapshot_cancel_is_cooperative_transfer_may_finish_before_worker_stops"]))
        updated = _update_job(job_id, **changes) or job
    else:
        updated = _update_job(job_id, status="cancelled", cancelled_at=_now(), errors=["download_cancelled_before_worker_started"]) or job
    return {
        "schema_id": DOWNLOAD_CANCEL_SCHEMA_ID,
        "ok": True,
        "status": updated.get("status"),
        "phase": PHASE_ID,
        "job": updated,
    }


def admin_model_download_jobs_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return list_model_download_jobs(payload)


def admin_model_download_job_payload(job_id: str) -> dict[str, Any]:
    return get_model_download_job(job_id)


def admin_model_download_start_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return start_model_download(payload)


def admin_model_download_cancel_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return cancel_model_download(payload)
