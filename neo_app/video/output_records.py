from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import video_backend_profile_payload
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, safe_join, sanitize_path_part
from neo_app.video.route_matrix import normalize_video_generation_type
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot

VIDEO_OUTPUT_RECORD_SCHEMA_VERSION: Final[str] = "neo.video.output.v7"  # V22 keeps the V7 ledger schema and adds replay-memory sidecar semantics.
VIDEO_RESULT_IMPORT_SCHEMA_VERSION: Final[str] = "neo.video.result_import.vg9"
VIDEO_OUTPUT_EXTENSIONS: Final[tuple[str, ...]] = (".webm", ".mp4", ".mov", ".mkv", ".gif")
VIDEO_PREVIEW_EXTENSIONS: Final[tuple[str, ...]] = (".jpg", ".jpeg", ".png", ".webp")


def _video_log_payload(record: dict[str, Any] | None = None, *, result: dict[str, Any] | None = None, request: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    result = result if isinstance(result, dict) else {}
    request = request if isinstance(request, dict) else {}
    backend = record.get("backend") if isinstance(record.get("backend"), dict) else (result.get("backend") if isinstance(result.get("backend"), dict) else {})
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else (result.get("parameters") if isinstance(result.get("parameters"), dict) else {})
    payload: dict[str, Any] = {
        "schema_id": "neo.video.runtime_log.summary.pass_m.v1",
        "result_id": record.get("result_id") or result.get("result_id") or request.get("result_id") or "",
        "status": record.get("status") or result.get("status") or ("failed" if result.get("ok") is False else "unknown"),
        "category": record.get("category") or "",
        "route_id": record.get("route_id") or result.get("route_id") or request.get("route_id") or "",
        "family": record.get("family") or request.get("family") or "",
        "loader": record.get("loader") or request.get("loader") or "",
        "generation_type": record.get("generation_type") or request.get("generation_type") or request.get("mode") or "",
        "prompt_char_count": len(str(record.get("prompt") or request.get("prompt") or "")),
        "negative_prompt_char_count": len(str(record.get("negative_prompt") or request.get("negative_prompt") or "")),
        "parameter_keys": sorted(str(key) for key in parameters.keys()) if isinstance(parameters, dict) else [],
        "profile_id": (backend.get("profile_id") if isinstance(backend, dict) else "") or request.get("profile_id") or "",
        "prompt_id": (backend.get("prompt_id") if isinstance(backend, dict) else "") or result.get("prompt_id") or "",
        "queued": bool(result.get("queued")),
        "dry_run": bool(result.get("dry_run") or request.get("dry_run")),
        "record_path": record.get("record_path") or "",
        "error_count": len(record.get("errors") if isinstance(record.get("errors"), list) else []),
        "warning_count": len(record.get("warnings") if isinstance(record.get("warnings"), list) else []),
    }
    if extra:
        payload.update(extra)
    return payload


def _log_video_runtime_event(event: str, *, run_id: str | None = None, level: str = "INFO", payload: dict[str, Any] | None = None, snapshot_name: str | None = None) -> None:
    try:
        log_surface_event("video", event, run_id=run_id, level=level, payload=payload or {})
        if snapshot_name:
            record_surface_snapshot("video", snapshot_name, payload or {}, run_id=run_id)
    except Exception:
        pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_label(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _run_timing_payload(*, queued_at: str = "", completed_at: str = "") -> dict[str, Any]:
    start = _parse_utc_iso(queued_at)
    end = _parse_utc_iso(completed_at)
    elapsed_seconds = round((end - start).total_seconds(), 3) if start and end else None
    return {
        "schema_version": "neo.video.run_timing.vg13_8",
        "queued_at": queued_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed_seconds,
        "elapsed_label": _elapsed_label(elapsed_seconds) if elapsed_seconds is not None else "",
    }


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _result_id(prefix: str = "video") -> str:
    return f"{sanitize_path_part(prefix, 'video')}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _metadata_path(result_id: str) -> Path:
    return get_video_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(result_id, 'video')}.json")


def _record_paths() -> list[Path]:
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    return sorted(metadata_dir.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)


def _guess_mime_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".gif": "image/gif",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".json": "application/json",
    }.get(suffix, "application/octet-stream")


def _file_record(*, result_id: str, file_id: str, path: Path, role: str = "video") -> dict[str, Any]:
    return {
        "file_id": file_id,
        "role": role,
        "filename": path.name,
        "path": _relative_to_root(path),
        "url": f"/api/video/output-file?result_id={sanitize_path_part(result_id, 'video')}&file_id={sanitize_path_part(file_id, 'file')}",
        "mime_type": _guess_mime_type(path.name),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _comfy_history_entry(history: dict[str, Any], prompt_id: str) -> dict[str, Any]:
    if not isinstance(history, dict):
        return {}
    if prompt_id and isinstance(history.get(prompt_id), dict):
        return history[prompt_id]
    if isinstance(history.get("outputs"), dict):
        return history
    # Some Comfy builds/proxies return {"history": {prompt_id: ...}}.
    nested = history.get("history")
    if isinstance(nested, dict):
        if prompt_id and isinstance(nested.get(prompt_id), dict):
            return nested[prompt_id]
        if isinstance(nested.get("outputs"), dict):
            return nested
    return {}


def _comfy_output_candidates(history_entry: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = history_entry.get("outputs") if isinstance(history_entry, dict) else {}
    if not isinstance(outputs, dict):
        return []
    candidates: list[dict[str, Any]] = []
    media_keys = ("videos", "gifs", "animated", "images", "files")
    for node_id, node_output in outputs.items():
        if not isinstance(node_output, dict):
            continue
        for key in media_keys:
            items = node_output.get(key)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if isinstance(item, str):
                    filename = Path(item).name
                    source = {"filename": filename, "subfolder": "", "type": "output"}
                elif isinstance(item, dict):
                    filename = str(item.get("filename") or item.get("name") or item.get("path") or "").strip()
                    if not filename:
                        continue
                    source = dict(item)
                    source["filename"] = Path(filename).name
                    source.setdefault("subfolder", str(item.get("subfolder") or ""))
                    source.setdefault("type", str(item.get("type") or "output"))
                else:
                    continue
                suffix = Path(str(source.get("filename") or "")).suffix.lower()
                role = "video" if suffix in VIDEO_OUTPUT_EXTENSIONS else "preview" if suffix in VIDEO_PREVIEW_EXTENSIONS else ""
                if not role:
                    continue
                candidates.append({
                    "node_id": str(node_id),
                    "output_key": key,
                    "output_index": index,
                    "role": role,
                    "filename": str(source.get("filename") or ""),
                    "subfolder": str(source.get("subfolder") or ""),
                    "type": str(source.get("type") or "output"),
                    "source": source,
                })
    return candidates


def _existing_file_records(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    bucket = "files" if role == "video" else "previews"
    items = outputs.get(bucket) if isinstance(outputs.get(bucket), list) else []
    return [item for item in items if isinstance(item, dict)]


def _download_comfy_output(base_url: str, candidate: dict[str, Any], target_path: Path, *, timeout: float) -> dict[str, Any]:
    params = {
        "filename": candidate.get("filename") or "",
        "subfolder": candidate.get("subfolder") or "",
        "type": candidate.get("type") or "output",
    }
    query = urlencode({key: value for key, value in params.items() if value != ""})
    url = urljoin(base_url.rstrip("/") + "/", f"view?{query}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "NeoStudioVideoResultImport/1.0"})
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local ComfyUI endpoint.
        with target_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    return {
        "source_url": url,
        "target_path": _relative_to_root(target_path),
        "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
    }


def _import_candidates_to_neo(record: dict[str, Any], candidates: list[dict[str, Any]], *, base_url: str, timeout: float) -> dict[str, Any]:
    result_id = str(record.get("result_id") or "video")
    category = str(record.get("category") or "txt2vid")
    output_dir = get_video_output_paths(category, create=True).output_dir
    preview_dir = get_video_output_paths("previews", create=True).output_dir
    imported: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[str] = []
    existing_source_keys = set()
    for item in _existing_file_records(record, "video") + _existing_file_records(record, "preview"):
        source = item.get("comfy_source") if isinstance(item.get("comfy_source"), dict) else {}
        key = (source.get("filename"), source.get("subfolder"), source.get("type"), source.get("node_id"), source.get("output_key"), source.get("output_index"))
        if any(key):
            existing_source_keys.add(key)

    for candidate in candidates:
        source_key = (candidate.get("filename"), candidate.get("subfolder"), candidate.get("type"), candidate.get("node_id"), candidate.get("output_key"), candidate.get("output_index"))
        if source_key in existing_source_keys:
            skipped.append(f"Already imported: {candidate.get('filename')}")
            continue
        role = str(candidate.get("role") or "video")
        dest_dir = output_dir if role == "video" else preview_dir
        safe_name = sanitize_path_part(str(candidate.get("filename") or "output.mp4"), "output.mp4")
        target_name = f"{sanitize_path_part(result_id, 'video')}_{len(imported) + 1:02d}_{safe_name}"
        target = safe_join(dest_dir, target_name)
        if target.exists() and target.stat().st_size > 0:
            skipped.append(f"Target exists: {target.name}")
            continue
        try:
            dl = _download_comfy_output(base_url, candidate, target, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"Import failed for {candidate.get('filename')}: {exc}")
            continue
        file_id = f"video_{len(_existing_file_records(record, 'video')) + 1 + sum(1 for i in imported if i.get('role') == 'video')}" if role == "video" else f"preview_{len(_existing_file_records(record, 'preview')) + 1 + sum(1 for i in imported if i.get('role') == 'preview')}"
        file_rec = _file_record(result_id=result_id, file_id=file_id, path=target, role=role)
        file_rec["comfy_source"] = {
            "node_id": candidate.get("node_id"),
            "output_key": candidate.get("output_key"),
            "output_index": candidate.get("output_index"),
            "filename": candidate.get("filename"),
            "subfolder": candidate.get("subfolder"),
            "type": candidate.get("type"),
        }
        file_rec["imported_at"] = utc_now_iso()
        imported.append({**file_rec, "download": dl})

    return {
        "schema_version": VIDEO_RESULT_IMPORT_SCHEMA_VERSION,
        "source": "comfy_history_view_api",
        "imported_count": len(imported),
        "candidate_count": len(candidates),
        "skipped": skipped,
        "errors": errors,
        "files": imported,
        "imported_at": utc_now_iso(),
    }


def _merge_imported_files(record: dict[str, Any], import_payload: dict[str, Any]) -> None:
    outputs = record.setdefault("outputs", {})
    files = outputs.setdefault("files", [])
    previews = outputs.setdefault("previews", [])
    for item in import_payload.get("files", []) if isinstance(import_payload.get("files"), list) else []:
        if not isinstance(item, dict):
            continue
        clean = {key: value for key, value in item.items() if key != "download"}
        if item.get("role") == "preview":
            previews.append(clean)
        else:
            files.append(clean)
    if files and not outputs.get("active_file_id"):
        outputs["active_file_id"] = files[0].get("file_id") or ""
    if previews and not outputs.get("active_preview_id"):
        outputs["active_preview_id"] = previews[0].get("file_id") or ""


def _status_from_generation_result(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "failed"
    if result.get("queued"):
        return "queued"
    if result.get("dry_run"):
        return "compiled"
    return "completed"


def _route_category(result: dict[str, Any], request: dict[str, Any] | None = None) -> str:
    route_id = str(result.get("route_id") or "")
    generation_type = str((request or {}).get("generation_type") or result.get("request", {}).get("generation_type") or "")
    lowered = route_id.lower()
    normalized_type = normalize_video_generation_type(generation_type)
    raw_type = generation_type.lower().replace("-", "_")
    if "audio_video" in lowered or raw_type in {"audio_video", "audio", "audio_visual", "audiovideo"}:
        return "audio_video"
    if "prompt_schedule" in lowered or "schedule" in lowered or raw_type in {"prompt_schedule", "prompt_scheduling", "motion_schedule", "schedule", "scheduled"}:
        return "schedule"
    if "interpolate" in lowered or raw_type in {"interpolate", "interpolation"}:
        return "interpolate"
    if "upscale" in lowered or raw_type == "upscale":
        return "upscale"
    if "repair" in lowered or raw_type == "repair":
        return "repair"
    if "first_last_frame" in lowered or raw_type in {"first_last_frame", "first_last", "start_end", "start_end_frame"}:
        return "first_last_frame"
    if "depth_motion" in lowered or raw_type in {"depth_motion", "depth_control", "motion_control", "control_video"}:
        return "depth_motion"
    if "vid2vid" in lowered or raw_type in {"vid2vid", "video_to_video", "v2v", "restyle_video"}:
        return "vid2vid"
    if "extend" in lowered or raw_type == "extend":
        return "extend"
    if "multiscene" in lowered or raw_type in {"multi_scene", "multiscene"}:
        return "multiscene"
    if ".img2vid" in lowered or normalized_type == "img2vid":
        return "img2vid"
    return "txt2vid"


def build_video_replay_payload(record: dict[str, Any]) -> dict[str, Any]:
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    finish = record.get("finish") if isinstance(record.get("finish"), dict) else {}
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    output_metadata = record.get("output_metadata") if isinstance(record.get("output_metadata"), dict) else {}
    payload = {
        "surface": "video",
        "route_id": record.get("route_id") or "",
        "family": record.get("family") or "wan22",
        "loader": record.get("loader") or "unet",
        "generation_type": record.get("generation_type") or "txt2vid",
        "category": record.get("category") or "txt2vid",
        "prompt": record.get("prompt") or "",
        "negative_prompt": record.get("negative_prompt") or "",
        **(record.get("parameters") if isinstance(record.get("parameters"), dict) else {}),
        "source_image": ((record.get("source") or {}).get("source_image") if isinstance(record.get("source"), dict) else "") or "",
        "source_image_name": ((record.get("source") or {}).get("source_image_name") if isinstance(record.get("source"), dict) else "") or "",
        "source_video": ((record.get("source") or {}).get("relative_path") if isinstance(record.get("source"), dict) else "") or "",
        "first_image": ((record.get("source") or {}).get("first_image") if isinstance(record.get("source"), dict) else "") or "",
        "last_image": ((record.get("source") or {}).get("last_image") if isinstance(record.get("source"), dict) else "") or "",
        "parent_result_id": lineage.get("parent_result_id") or "",
        "source_result_id": lineage.get("source_result_id") or "",
    }
    if record.get("category") == "interpolate":
        replay_context = output_metadata.get("replay_context") if isinstance(output_metadata.get("replay_context"), dict) else {}
        payload.update({
            "extension_id": finish.get("extension_id") or "video.finish_interpolation",
            "finish_operation": finish.get("operation") or record.get("finish_operation") or "frame_interpolation",
            "method_requested": finish.get("method_requested") or payload.get("method_requested") or "",
            "method_used": finish.get("method_used") or payload.get("method") or "",
            "vram_profile": finish.get("vram_profile") or payload.get("vram_profile") or "",
            "source_fps": finish.get("source_fps") or payload.get("source_fps"),
            "output_fps": finish.get("output_fps") or payload.get("output_fps"),
            "output_fps_policy": finish.get("output_fps_policy") or payload.get("output_fps_policy") or "",
            "fps_multiplier": finish.get("fps_multiplier") or payload.get("fps_multiplier"),
            "extensions_used": extensions.get("used") if isinstance(extensions.get("used"), list) else [],
            "extension_payloads": extensions.get("payloads") if isinstance(extensions.get("payloads"), dict) else {},
            "lineage": lineage,
            "replay_context": replay_context,
        })
    if record.get("category") == "upscale":
        replay_context = output_metadata.get("replay_context") if isinstance(output_metadata.get("replay_context"), dict) else {}
        payload.update({
            "extension_id": finish.get("extension_id") or "video.finish_upscale",
            "finish_operation": finish.get("operation") or record.get("finish_operation") or "upscale",
            "engine": finish.get("engine") or payload.get("engine") or "seedvr2",
            "vram_profile": finish.get("vram_profile") or payload.get("vram_profile") or "",
            "target_preset": finish.get("target_preset") or payload.get("target_preset") or "",
            "resolution": finish.get("resolution") or payload.get("resolution"),
            "max_resolution": finish.get("max_resolution") or payload.get("max_resolution"),
            "dit_model": finish.get("dit_model") or payload.get("dit_model") or "",
            "vae_model": finish.get("vae_model") or payload.get("vae_model") or "",
            "batch_size": finish.get("batch_size") or payload.get("batch_size"),
            "blocks_to_swap": finish.get("blocks_to_swap") or payload.get("blocks_to_swap"),
            "output_fps_policy": finish.get("output_fps_policy") or payload.get("output_fps_policy") or "",
            "preserve_source_fps": finish.get("preserve_source_fps") if "preserve_source_fps" in finish else payload.get("output_fps_policy") == "preserve_source_fps",
            "preserve_audio": finish.get("preserve_audio") if "preserve_audio" in finish else payload.get("preserve_audio"),
            "extensions_used": extensions.get("used") if isinstance(extensions.get("used"), list) else [],
            "extension_payloads": extensions.get("payloads") if isinstance(extensions.get("payloads"), dict) else {},
            "lineage": lineage,
            "replay_context": replay_context,
        })
    return payload


def build_assistant_summary(record: dict[str, Any]) -> str:
    label = f"{record.get('family', 'WAN 2.2')} {record.get('generation_type', 'txt2vid')}"
    params = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    if record.get("category") == "interpolate":
        finish = record.get("finish") if isinstance(record.get("finish"), dict) else {}
        method = finish.get("method_used") or params.get("method") or "RIFE"
        requested = finish.get("method_requested") or params.get("method_requested") or method
        profile = finish.get("vram_profile") or params.get("vram_profile") or "medium"
        parent = ((record.get("lineage") or {}).get("parent_result_id") if isinstance(record.get("lineage"), dict) else "") or "no parent"
        return f"Video result {record.get('result_id')} used Finish frame interpolation, {params.get('fps_multiplier', '?')}× FPS, method {method} requested from {requested}, profile {profile}, parent {parent}, status {record.get('status', 'unknown')}."
    if record.get("category") == "upscale":
        finish = record.get("finish") if isinstance(record.get("finish"), dict) else {}
        engine = finish.get("engine") or params.get("engine") or "seedvr2"
        profile = finish.get("vram_profile") or params.get("vram_profile") or "medium"
        resolution = finish.get("resolution") or params.get("resolution") or "?"
        parent = ((record.get("lineage") or {}).get("parent_result_id") if isinstance(record.get("lineage"), dict) else "") or "no parent"
        return f"Video result {record.get('result_id')} used Finish upscale with {engine}, profile {profile}, target {resolution}, parent {parent}, status {record.get('status', 'unknown')}."
    if record.get("category") == "repair":
        return f"Video result {record.get('result_id')} used Finish repair/cleanup, mode {params.get('mode', '?')}, status {record.get('status', 'unknown')}."
    if record.get("category") == "first_last_frame":
        return f"Video result {record.get('result_id')} used LTX First/Last Frame transition, status {record.get('status', 'unknown')}."
    if record.get("category") == "multiscene":
        return f"Video result {record.get('result_id')} used LTX MultiScene, status {record.get('status', 'unknown')}."
    if record.get("category") == "extend":
        return f"Video result {record.get('result_id')} used LTX Video Extend, status {record.get('status', 'unknown')}."
    if record.get("category") == "vid2vid":
        return f"Video result {record.get('result_id')} used LTX Video-to-Video, status {record.get('status', 'unknown')}."
    if record.get("category") == "depth_motion":
        return f"Video result {record.get('result_id')} used LTX Depth / Motion Control, status {record.get('status', 'unknown')}."
    if record.get("category") == "audio_video":
        return f"Video result {record.get('result_id')} used LTX Audio-Video, status {record.get('status', 'unknown')}."
    if record.get("category") == "schedule":
        return f"Video result {record.get('result_id')} used LTX Prompt/Motion Schedule, status {record.get('status', 'unknown')}."
    size = f"{params.get('width', '?')}x{params.get('height', '?')}"
    timing = f"{params.get('frames', '?')} frames @ {params.get('fps', '?')} fps"
    return f"Video result {record.get('result_id')} used {label}, {size}, {timing}, status {record.get('status', 'unknown')}."


def _merge_dicts(*items: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if value in (None, "", [], {}):
                continue
            merged[key] = value
    return merged


def _interpolation_record_metadata(result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    output_metadata = result.get("output_metadata") if isinstance(result.get("output_metadata"), dict) else {}
    finish = _merge_dicts(output_metadata.get("finish") if isinstance(output_metadata.get("finish"), dict) else {}, result.get("finish") if isinstance(result.get("finish"), dict) else {})
    extensions = _merge_dicts(output_metadata.get("extensions") if isinstance(output_metadata.get("extensions"), dict) else {}, result.get("extensions") if isinstance(result.get("extensions"), dict) else {})
    lineage = _merge_dicts(
        output_metadata.get("lineage") if isinstance(output_metadata.get("lineage"), dict) else {},
        result.get("lineage") if isinstance(result.get("lineage"), dict) else {},
        {
            "parent_result_id": str(request.get("parent_result_id") or ""),
            "source_result_id": str(request.get("source_result_id") or request.get("parent_result_id") or ""),
            "source_file_id": str(request.get("source_file_id") or ""),
        },
    )
    memory_event = _merge_dicts(output_metadata.get("memory_event") if isinstance(output_metadata.get("memory_event"), dict) else {}, result.get("memory_event") if isinstance(result.get("memory_event"), dict) else {})
    return {
        "output_metadata": output_metadata,
        "finish": finish,
        "extensions": extensions,
        "lineage": lineage,
        "memory_event": memory_event,
    }




def _upscale_record_metadata(result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    output_metadata = result.get("output_metadata") if isinstance(result.get("output_metadata"), dict) else {}
    finish = _merge_dicts(output_metadata.get("finish") if isinstance(output_metadata.get("finish"), dict) else {}, result.get("finish") if isinstance(result.get("finish"), dict) else {})
    extensions = _merge_dicts(output_metadata.get("extensions") if isinstance(output_metadata.get("extensions"), dict) else {}, result.get("extensions") if isinstance(result.get("extensions"), dict) else {})
    lineage = _merge_dicts(
        output_metadata.get("lineage") if isinstance(output_metadata.get("lineage"), dict) else {},
        result.get("lineage") if isinstance(result.get("lineage"), dict) else {},
        {
            "parent_result_id": str(request.get("parent_result_id") or ""),
            "source_result_id": str(request.get("source_result_id") or request.get("parent_result_id") or ""),
            "source_file_id": str(request.get("source_file_id") or ""),
        },
    )
    memory_event = _merge_dicts(output_metadata.get("memory_event") if isinstance(output_metadata.get("memory_event"), dict) else {}, result.get("memory_event") if isinstance(result.get("memory_event"), dict) else {})
    return {
        "output_metadata": output_metadata,
        "finish": finish,
        "extensions": extensions,
        "lineage": lineage,
        "memory_event": memory_event,
    }

def register_video_generation_result(result: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create/update Neo's V7 video ledger record for a compile or queued generation.

    V7 does not wait for long ComfyUI video jobs. It records the queue/compile event,
    replay payload, and prompt id immediately. Later refresh/import can attach files.
    """
    request = request if isinstance(request, dict) else {}
    category = _route_category(result, request)
    finish_metadata = _interpolation_record_metadata(result, request) if category == "interpolate" else _upscale_record_metadata(result, request) if category == "upscale" else {}
    route_id = str(result.get("route_id") or request.get("route_id") or "wan22.unet.txt2vid")
    requested_generation_type = str(request.get("generation_type") or result.get("request", {}).get("generation_type") or "")
    generation_type = category if category in {"interpolate", "upscale", "repair", "first_last_frame", "extend", "vid2vid", "depth_motion", "multiscene", "schedule", "audio_video"} else normalize_video_generation_type(requested_generation_type or ("img2vid" if category == "img2vid" else "txt2vid"))
    family = str(request.get("family") or result.get("request", {}).get("family") or route_id.split(".")[0] or "wan22")
    loader = str(request.get("loader") or result.get("request", {}).get("loader") or (route_id.split(".")[1] if "." in route_id else "unet"))
    result_id = str(result.get("result_id") or _result_id(f"{family}_{generation_type}"))
    parameters = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    prompt_text = str(request.get("prompt") or result.get("request", {}).get("prompt") or "")
    negative_prompt = str(request.get("negative_prompt") or result.get("request", {}).get("negative_prompt") or "")
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    backend = result.get("backend") if isinstance(result.get("backend"), dict) else {}
    neo_output = result.get("neo_output") if isinstance(result.get("neo_output"), dict) else {}
    output_dir = get_video_output_paths(category, create=True).output_dir
    preview_dir = get_video_output_paths("previews", create=True).output_dir

    files: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    # V7 records no generated files until Comfy history/import attaches them.
    # Still discover any already-copied matching result files for regression/manual workflows.
    for index, file_path in enumerate(sorted(output_dir.glob(f"{sanitize_path_part(result_id)}*")), start=1):
        if file_path.suffix.lower() in VIDEO_OUTPUT_EXTENSIONS:
            files.append(_file_record(result_id=result_id, file_id=f"video_{index}", path=file_path, role="video"))
    for index, file_path in enumerate(sorted(preview_dir.glob(f"{sanitize_path_part(result_id)}*")), start=1):
        if file_path.suffix.lower() in VIDEO_PREVIEW_EXTENSIONS:
            previews.append(_file_record(result_id=result_id, file_id=f"preview_{index}", path=file_path, role="preview"))

    now = utc_now_iso()
    queued_at = now if result.get("queued") else ""
    record: dict[str, Any] = {
        "schema_version": VIDEO_OUTPUT_RECORD_SCHEMA_VERSION,
        "phase": "V21" if category == "audio_video" else "V20" if category == "schedule" else "V19" if category == "depth_motion" else "V18" if category == "vid2vid" else "V17" if category == "extend" else "V16" if category == "multiscene" else "V15" if category == "first_last_frame" else "V14" if category == "repair" else "V13" if category == "upscale" else "V24.8" if category == "interpolate" and finish_metadata.get("output_metadata") else "V12" if category == "interpolate" else "V7",
        "surface": "video",
        "result_id": result_id,
        "created_at": now,
        "updated_at": now,
        "status": _status_from_generation_result(result),
        "category": category,
        "route_id": route_id,
        "family": family,
        "loader": loader,
        "generation_type": generation_type,
        "prompt": prompt_text,
        "negative_prompt": negative_prompt,
        "parameters": parameters,
        "profile": result.get("profile") if isinstance(result.get("profile"), dict) else {},
        "source": source,
        "run_timing": _run_timing_payload(queued_at=queued_at),
        "backend": {
            "profile_id": ((backend.get("profile") or {}).get("profile_id") if isinstance(backend.get("profile"), dict) else "") or "video.comfyui_portable",
            "base_url": backend.get("base_url") or "",
            "prompt_id": result.get("prompt_id") or "",
            "client_id": result.get("client_id") or ((result.get("prompt_api_payload") or {}).get("client_id") if isinstance(result.get("prompt_api_payload"), dict) else "") or "",
            "queue_response": result.get("queue_response") if isinstance(result.get("queue_response"), dict) else {},
            "metadata_sidecar": neo_output.get("metadata_sidecar") or "",
            "prompt_api_sidecar": neo_output.get("prompt_api_sidecar") or "",
            "runtime_preflight": result.get("runtime_preflight") if isinstance(result.get("runtime_preflight"), dict) else {},
            "queue_error": result.get("queue_error") if isinstance(result.get("queue_error"), dict) else {},
        },
        "outputs": {
            "files": files,
            "previews": previews,
            "active_file_id": files[0]["file_id"] if files else "",
            "active_preview_id": previews[0]["file_id"] if previews else "",
        },
        "lineage": finish_metadata.get("lineage") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("lineage"), dict) else {
            "parent_result_id": str(request.get("parent_result_id") or ""),
            "source_result_id": str(request.get("source_result_id") or ""),
        },
        "finish": finish_metadata.get("finish") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("finish"), dict) else {},
        "extensions": finish_metadata.get("extensions") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("extensions"), dict) else {},
        "output_metadata": finish_metadata.get("output_metadata") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("output_metadata"), dict) else {},
        "memory_event": finish_metadata.get("memory_event") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("memory_event"), dict) else {},
        "finish_operation": (finish_metadata.get("finish") or {}).get("operation") if category in {"interpolate", "upscale"} and isinstance(finish_metadata.get("finish"), dict) else "",
        "errors": [str(result.get("error"))] if result.get("error") else [],
        "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        "replay_payload": {},
        "assistant_summary": "",
        "rules": [
            "V7 records compile/queue events immediately and attaches final files only after they exist in Neo-owned video outputs.",
            "V12 Finish interpolation creates child output records under the interpolate category without modifying the parent video.",
            "V13 Finish upscale creates child output records under the upscale category without modifying the parent video.",
            "V14 Finish repair/cleanup creates child output records under the repair category without modifying the parent video.",
            "V17 Video Extend creates child output records under the extend category without modifying the parent video.",
            "V18 Video-to-Video creates child output records under the vid2vid category without modifying the parent video.",
            "V19 Depth / Motion Control creates child output records under the depth_motion category without modifying the parent video.",
            "V20 Prompt/Motion Schedule creates LTX schedule records under the schedule category with replayable beat metadata.",
            "V21 Audio-Video creates LTX audio-video records under the audio_video category with replayable audio prompt metadata.",
            "V22 adds canonical replay metadata and optional unified-memory export for every Video result.",
            "V-G9 imports finished ComfyUI video outputs from /history + /view into Neo-owned playback folders.",
            "V15 First/Last Frame creates controlled transition records under the first_last_frame category using two source images.",
            "Preview playback uses /api/video/output-file and never assumes ComfyUI native outputs are final storage.",
            "V24.8 records Finish interpolation extension payloads, method requested/used, source FPS, and parent-child lineage on child outputs.",
            "V25.9.19 Phase 10 records SeedVR2 Upscale extension payloads, source lineage, model/VRAM settings, readiness snapshots, replay context, and memory events on child outputs.",
            "Replay payload preserves route, prompt, parameters, source references, and Finish extension context.",
        ],
    }
    record["replay_payload"] = build_video_replay_payload(record)
    record["assistant_summary"] = build_assistant_summary(record)
    path = _metadata_path(result_id)
    record["record_path"] = _relative_to_root(path)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    _log_video_runtime_event(
        "video.result.registered",
        run_id=result_id,
        payload=_video_log_payload(record, result=result, request=request),
        snapshot_name="neo_last_payload",
    )
    return {"ok": True, "result_id": result_id, "record": record, "record_path": str(path)}


def load_video_output_record(result_id: str) -> dict[str, Any]:
    clean = sanitize_path_part(result_id, "video")
    path = _metadata_path(clean)
    if not path.exists():
        return {"ok": False, "error": f"Video result not found: {clean}", "result_id": clean}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Video result could not be read: {exc}", "result_id": clean}
    return {"ok": True, "record": data, "result_id": clean}


def list_video_output_records(*, limit: int = 50, category: str | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in _record_paths():
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if item.get("schema_version") != VIDEO_OUTPUT_RECORD_SCHEMA_VERSION:
            continue
        if category and item.get("category") != category:
            continue
        records.append(item)
        if len(records) >= max(1, min(int(limit or 50), 200)):
            break
    return {
        "ok": True,
        "schema_version": VIDEO_OUTPUT_RECORD_SCHEMA_VERSION,
        "phase": "V22",
        "count": len(records),
        "records": records,
        "active_result_id": records[0]["result_id"] if records else "",
    }



def register_video_source_upload(file_path: Path, *, original_filename: str = "", lane: str = "finish", content_type: str = "") -> dict[str, Any]:
    """Register a dragged/browsed source video as a Neo-owned Video ledger result.

    Finish passes can then use the normal source_result_id/source_file_id contract
    instead of treating browser uploads as anonymous paths. The file must already
    live under neo_data/outputs/video/source.
    """
    path = file_path.resolve()
    video_root = (ROOT_DIR / "neo_data" / "outputs" / "video").resolve()
    if path.suffix.lower() not in VIDEO_OUTPUT_EXTENSIONS:
        return {"ok": False, "error": f"Unsupported video source extension: {path.suffix or 'none'}"}
    if video_root not in path.parents and path != video_root:
        return {"ok": False, "error": "Video source uploads must be stored under neo_data/outputs/video."}
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "Video source upload file does not exist."}

    lane_key = sanitize_path_part(str(lane or "finish"), "finish")
    result_id = _result_id(f"video_source_{lane_key}")
    now = utc_now_iso()
    file_record = _file_record(result_id=result_id, file_id="video_1", path=path, role="video")
    record: dict[str, Any] = {
        "schema_version": VIDEO_OUTPUT_RECORD_SCHEMA_VERSION,
        "source_upload_schema_version": "neo.video.finish.source_video_upload.v25_9_19_phase_10c",
        "phase": "V25.9.19 Phase 10c",
        "surface": "video",
        "result_id": result_id,
        "created_at": now,
        "updated_at": now,
        "status": "completed",
        "category": "source",
        "route_id": "finish.source_video_upload",
        "family": "finish",
        "loader": "browser_upload",
        "generation_type": "source_video",
        "prompt": "Video Finish source upload",
        "negative_prompt": "",
        "parameters": {
            "lane": lane_key,
            "original_filename": original_filename or path.name,
            "content_type": content_type or _guess_mime_type(path.name),
        },
        "profile": {},
        "source": {
            "source_type": "browser_upload",
            "original_filename": original_filename or path.name,
            "lane": lane_key,
            "source_video_path": _relative_to_root(path),
        },
        "run_timing": _run_timing_payload(queued_at=now, completed_at=now),
        "backend": {
            "profile_id": "video.browser_upload",
            "base_url": "",
            "prompt_id": "",
            "client_id": "",
            "queue_response": {},
            "metadata_sidecar": "",
            "prompt_api_sidecar": "",
            "runtime_preflight": {},
            "queue_error": {},
        },
        "outputs": {
            "files": [file_record],
            "previews": [],
            "active_file_id": "video_1",
            "active_preview_id": "",
        },
        "lineage": {
            "parent_result_id": "",
            "source_result_id": result_id,
            "source_file_id": "video_1",
            "source_video_path": _relative_to_root(path),
            "source_kind": "browser_upload",
            "parent_mutation_allowed": False,
        },
        "finish": {},
        "extensions": {},
        "output_metadata": {
            "schema_version": "neo.video.finish.source_video_upload.metadata.v25_9_19_phase_10c",
            "source_video_path": _relative_to_root(path),
            "lane": lane_key,
            "created_at": now,
        },
        "memory_event": {
            "namespace": "video.finish_source_upload",
            "event_type": "video.finish_source.uploaded",
            "title": f"Video source uploaded for {lane_key}",
            "summary": f"Browser upload {original_filename or path.name} was stored as a Neo-owned Video source.",
        },
        "finish_operation": "source_upload",
        "errors": [],
        "warnings": [],
        "replay_payload": {},
        "assistant_summary": "",
        "rules": [
            "V25.9.19 Phase 10c registers browser source videos as Neo-owned ledger results.",
            "Finish Interpolation and SeedVR2 Upscale use source_result_id/source_file_id from this record.",
            "Running a Finish pass still creates a new child output and never mutates this uploaded source.",
        ],
    }
    record["replay_payload"] = build_video_replay_payload(record)
    record["assistant_summary"] = build_assistant_summary(record)
    metadata_path = _metadata_path(result_id)
    record["record_path"] = _relative_to_root(metadata_path)
    metadata_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    _log_video_runtime_event("video.source_upload.registered", run_id=result_id, payload=_video_log_payload(record, extra={"operation": "source_upload", "lane": lane_key}), snapshot_name="neo_last_payload")
    return {"ok": True, "result_id": result_id, "record": record, "record_path": str(metadata_path), "source_video_path": _relative_to_root(path), "source_file_id": "video_1"}

def video_output_file_path(result_id: str, file_id: str) -> Path | None:
    loaded = load_video_output_record(result_id)
    if not loaded.get("ok"):
        return None
    record = loaded.get("record") or {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    candidates = []
    for bucket in ("files", "previews"):
        for item in outputs.get(bucket) or []:
            if isinstance(item, dict) and item.get("file_id") == file_id:
                candidates.append(item)
    if not candidates:
        return None
    rel = str(candidates[0].get("path") or "")
    if not rel:
        return None
    target = (ROOT_DIR / rel).resolve()
    data_root = (ROOT_DIR / "neo_data" / "outputs" / "video").resolve()
    if data_root not in target.parents and target != data_root:
        return None
    return target if target.exists() and target.is_file() else None


def refresh_video_result_from_comfy(result_id: str, *, profile_id: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    """Refresh a queued ComfyUI video result and import completed media into Neo-owned outputs.

    V-G9 makes Comfy history actionable: Neo reads /history/{prompt_id}, scans common
    video/image output buckets, downloads files through Comfy's /view endpoint, and attaches
    the imported files to the Video result ledger for local playback.
    """
    loaded = load_video_output_record(result_id)
    if not loaded.get("ok"):
        return loaded
    record = loaded["record"]
    prompt_id = str(((record.get("backend") or {}).get("prompt_id")) or "")
    if not prompt_id:
        return {"ok": False, "record": record, "error": "No ComfyUI prompt_id is stored for this video result."}
    profile = video_backend_profile_payload(profile_id or ((record.get("backend") or {}).get("profile_id")))
    base_url = profile["connection"]["base_url"]
    try:
        req = Request(urljoin(base_url.rstrip("/") + "/", f"history/{prompt_id}"), headers={"Accept": "application/json", "User-Agent": "NeoStudioVideoResults/1.0"})
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local ComfyUI endpoint.
            history = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        record.setdefault("warnings", []).append(f"ComfyUI history refresh failed: {exc}")
        record.setdefault("import_status", {})["last_error"] = str(exc)
        record["updated_at"] = utc_now_iso()
        _metadata_path(record["result_id"]).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            record_surface_error("video", "ComfyUI history refresh failed.", exc=exc, payload=_video_log_payload(record, extra={"operation": "refresh"}), run_id=record.get("result_id"))
        except Exception:
            pass
        return {"ok": False, "record": record, "error": f"ComfyUI history refresh failed: {exc}"}

    entry = _comfy_history_entry(history, prompt_id)
    candidates = _comfy_output_candidates(entry)
    import_payload = _import_candidates_to_neo(record, candidates, base_url=base_url, timeout=timeout) if candidates else {
        "schema_version": VIDEO_RESULT_IMPORT_SCHEMA_VERSION,
        "source": "comfy_history_view_api",
        "imported_count": 0,
        "candidate_count": 0,
        "skipped": [],
        "errors": [],
        "files": [],
        "imported_at": utc_now_iso(),
    }
    _merge_imported_files(record, import_payload)
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    previews = outputs.get("previews") if isinstance(outputs.get("previews"), list) else []
    has_video = bool(files)
    record.setdefault("backend", {})["history_seen"] = bool(history)
    record.setdefault("backend", {})["history_prompt_id"] = prompt_id
    record["import_status"] = {
        "schema_version": VIDEO_RESULT_IMPORT_SCHEMA_VERSION,
        "history_seen": bool(history),
        "history_entry_seen": bool(entry),
        "candidate_count": len(candidates),
        "imported_count": int(import_payload.get("imported_count") or 0),
        "attached_video_count": len(files),
        "attached_preview_count": len(previews),
        "last_refreshed_at": utc_now_iso(),
        "errors": import_payload.get("errors") if isinstance(import_payload.get("errors"), list) else [],
        "skipped": import_payload.get("skipped") if isinstance(import_payload.get("skipped"), list) else [],
    }
    if has_video:
        record["status"] = "completed"
        timing = record.setdefault("run_timing", _run_timing_payload(queued_at=str(record.get("created_at") or "")))
        if isinstance(timing, dict) and not timing.get("completed_at"):
            queued_at = str(timing.get("queued_at") or record.get("created_at") or "")
            record["run_timing"] = _run_timing_payload(queued_at=queued_at, completed_at=utc_now_iso())
    elif entry and not candidates:
        record["status"] = record.get("status") or "queued"
        note = "ComfyUI history was found, but no video/image output files were available yet. The job may still be running or the SaveVideo node returned an unsupported output key."
        if note not in record.setdefault("warnings", []):
            record["warnings"].append(note)
    elif not entry:
        record["status"] = record.get("status") or "queued"
    record["updated_at"] = utc_now_iso()
    record["replay_payload"] = build_video_replay_payload(record)
    record["assistant_summary"] = build_assistant_summary(record)
    _metadata_path(record["result_id"]).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    _log_video_runtime_event(
        "video.result.refreshed_imported",
        run_id=record.get("result_id"),
        payload=_video_log_payload(record, extra={"history_seen": bool(history), "candidate_count": len(candidates), "imported_count": import_payload.get("imported_count") or 0, "attached_video_count": len(files)}),
        snapshot_name="neo_last_payload",
    )
    return {"ok": True, "record": record, "history": history, "import": import_payload, "import_status": record.get("import_status"), "outputs": record.get("outputs")}
