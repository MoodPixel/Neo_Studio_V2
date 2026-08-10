from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Final

from neo_app.video.output_records import load_video_output_record
from neo_app.video.route_matrix import VIDEO_ROUTES

VIDEO_OUTPUT_INSPECTOR_SCHEMA_VERSION: Final[str] = "neo.video.output_inspector.v1"
VIDEO_FINISH_CATEGORIES: Final[frozenset[str]] = frozenset({"interpolate", "upscale", "repair", "source"})
VIDEO_SOURCE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("source_image", "Source image"),
    ("source_image_name", "Source image name"),
    ("first_image", "First frame"),
    ("first_image_name", "First frame name"),
    ("last_image", "Last frame"),
    ("last_image_name", "Last frame name"),
    ("relative_path", "Source video"),
    ("source_video_path", "Source video"),
    ("original_filename", "Original filename"),
    ("depth_map", "Depth map"),
    ("motion_reference", "Motion reference"),
    ("control_image", "Control image"),
    ("audio_path", "Audio source"),
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _active_file(outputs: dict[str, Any], bucket: str, active_key: str) -> dict[str, Any]:
    items = [item for item in _list(outputs.get(bucket)) if isinstance(item, dict)]
    active_id = _text(outputs.get(active_key))
    if active_id:
        match = next((item for item in items if _text(item.get("file_id")) == active_id), None)
        if match:
            return deepcopy(match)
    return deepcopy(items[0]) if items else {}


def _record_summary(record: dict[str, Any], *, missing: bool = False) -> dict[str, Any]:
    outputs = _dict(record.get("outputs"))
    active_file = _active_file(outputs, "files", "active_file_id")
    category = _text(record.get("category")) or "unknown"
    finish = _dict(record.get("finish"))
    operation = _text(finish.get("operation") or record.get("finish_operation"))
    return {
        "result_id": _text(record.get("result_id")),
        "status": _text(record.get("status")) or ("missing" if missing else "unknown"),
        "category": category,
        "route_id": _text(record.get("route_id")),
        "family": _text(record.get("family")),
        "loader": _text(record.get("loader")),
        "generation_type": _text(record.get("generation_type")),
        "created_at": _text(record.get("created_at")),
        "operation": operation or category,
        "filename": _text(active_file.get("filename")),
        "file_id": _text(active_file.get("file_id")),
        "missing": bool(missing),
    }


def _load_record_with(loader: Callable[[str], Any], result_id: str) -> dict[str, Any] | None:
    if not result_id:
        return None
    payload = loader(result_id)
    if isinstance(payload, dict) and isinstance(payload.get("record"), dict):
        return payload["record"]
    if isinstance(payload, dict) and payload.get("result_id"):
        return payload
    return None


def _lineage_payload(record: dict[str, Any], loader: Callable[[str], Any], *, max_depth: int = 12) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    missing_refs: list[str] = []
    visited: set[str] = set()
    current = record

    for _ in range(max_depth):
        result_id = _text(current.get("result_id"))
        if result_id and result_id in visited:
            break
        if result_id:
            visited.add(result_id)
        chain.append(_record_summary(current))

        lineage = _dict(current.get("lineage"))
        parent_id = _text(lineage.get("parent_result_id"))
        source_id = _text(lineage.get("source_result_id"))
        next_id = parent_id or source_id
        if not next_id or next_id == result_id:
            break
        if next_id in visited:
            break
        loaded = _load_record_with(loader, next_id)
        if not loaded:
            missing_refs.append(next_id)
            chain.append(_record_summary({"result_id": next_id}, missing=True))
            break
        current = loaded

    # Stored relationships point from child to parent/source. The Inspector reads
    # most naturally from the generation root toward the selected output.
    chain.reverse()
    current_lineage = _dict(record.get("lineage"))
    return {
        "depth": max(0, len([item for item in chain if not item.get("missing")]) - 1),
        "chain": chain,
        "parent_result_id": _text(current_lineage.get("parent_result_id")),
        "source_result_id": _text(current_lineage.get("source_result_id")),
        "source_file_id": _text(current_lineage.get("source_file_id")),
        "relationship": _text(current_lineage.get("relationship")),
        "missing_refs": missing_refs,
    }


def _nearest_generation_record(record: dict[str, Any], loader: Callable[[str], Any], *, max_depth: int = 12) -> dict[str, Any]:
    current = record
    visited: set[str] = set()
    for _ in range(max_depth):
        category = _text(current.get("category"))
        family = _text(current.get("family"))
        generation_type = _text(current.get("generation_type"))
        if category not in VIDEO_FINISH_CATEGORIES and family != "finish" and generation_type != "source_video":
            return current
        result_id = _text(current.get("result_id"))
        if result_id:
            if result_id in visited:
                break
            visited.add(result_id)
        lineage = _dict(current.get("lineage"))
        next_id = _text(lineage.get("parent_result_id") or lineage.get("source_result_id"))
        if not next_id or next_id in visited:
            break
        loaded = _load_record_with(loader, next_id)
        if not loaded:
            break
        current = loaded
    return record


def _provider_id(record: dict[str, Any]) -> str:
    backend = _dict(record.get("backend"))
    route_id = _text(record.get("route_id"))
    explicit = _text(record.get("provider_id") or backend.get("provider_id"))
    if explicit:
        return explicit
    if route_id.startswith("xai_grok."):
        return "xai_grok"
    profile_id = _text(backend.get("profile_id"))
    if profile_id.startswith("video.xai_grok"):
        return "xai_grok"
    return "comfyui" if profile_id or route_id else ""


def _model_payload(record: dict[str, Any]) -> dict[str, Any]:
    params = _dict(record.get("parameters"))
    keys = (
        "model", "model_name", "unet_name", "gguf_name", "rapid_aio_model",
        "high_noise_model", "low_noise_model", "clip_name", "clip_name1", "clip_name2",
        "text_encoder", "vae_name", "video_lora_model", "dit_model", "vae_model",
    )
    return {key: params.get(key) for key in keys if params.get(key) not in (None, "", "provider_default", "automatic")}


def _parameter_highlights(params: dict[str, Any]) -> list[dict[str, Any]]:
    labels = (
        ("width", "Width"), ("height", "Height"), ("frames", "Frames"), ("fps", "FPS"),
        ("duration_seconds", "Duration"), ("steps", "Steps"), ("guidance", "Guidance"),
        ("seed", "Seed"), ("sampler", "Sampler"), ("scheduler", "Scheduler"),
        ("vram_profile", "VRAM profile"), ("performance_profile", "Performance profile"),
        ("output_format", "Output format"), ("resolution", "Resolution"),
        ("aspect_ratio", "Aspect ratio"), ("target_preset", "Target preset"),
        ("fps_multiplier", "FPS multiplier"), ("method", "Method"), ("engine", "Engine"),
    )
    return [{"key": key, "label": label, "value": params.get(key)} for key, label in labels if params.get(key) not in (None, "")]


def _source_payload(record: dict[str, Any]) -> dict[str, Any]:
    source = _dict(record.get("source"))
    lineage = _dict(record.get("lineage"))
    params = _dict(record.get("parameters"))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, label: str, value: Any, *, name: Any = "", result_id: Any = "", file_id: Any = "") -> None:
        text = _text(value)
        name_text = _text(name)
        if not text and not name_text and not _text(result_id):
            return
        dedupe = (kind, text or name_text or _text(result_id))
        if dedupe in seen:
            return
        seen.add(dedupe)
        items.append({
            "kind": kind,
            "label": label,
            "value": text,
            "name": name_text,
            "result_id": _text(result_id),
            "file_id": _text(file_id),
        })

    for key, label in VIDEO_SOURCE_FIELDS:
        add(key, label, source.get(key), name=source.get(f"{key}_name"))

    source_images = _list(source.get("source_images"))
    for index, item in enumerate(source_images, start=1):
        if isinstance(item, dict):
            add("source_image", f"Source image {index}", item.get("path") or item.get("url") or item.get("source_image"), name=item.get("name") or item.get("filename"))
        else:
            add("source_image", f"Source image {index}", item)

    segments = _list(params.get("segments"))
    for index, segment in enumerate(segments, start=1):
        if isinstance(segment, dict):
            add("multiscene_image", f"MultiScene image {index}", segment.get("image"), name=segment.get("image_name"))

    add("lineage_source", "Source result", "", result_id=lineage.get("source_result_id"), file_id=lineage.get("source_file_id"))
    if lineage.get("parent_result_id") and lineage.get("parent_result_id") != lineage.get("source_result_id"):
        add("lineage_parent", "Parent result", "", result_id=lineage.get("parent_result_id"), file_id=lineage.get("source_file_id"))

    return {"items": items, "raw": deepcopy(source)}


def _extension_payload(record: dict[str, Any]) -> dict[str, Any]:
    extensions = _dict(record.get("extensions"))
    used = [_text(item) for item in _list(extensions.get("used")) if _text(item)]
    payloads = _dict(extensions.get("payloads"))
    finish = _dict(record.get("finish"))
    finish_extension = _text(finish.get("extension_id"))
    if finish_extension and finish_extension not in used:
        used.append(finish_extension)
    items = []
    for extension_id in used:
        items.append({
            "extension_id": extension_id,
            "label": extension_id.replace("video.", "").replace("_", " ").title(),
            "payload": deepcopy(_dict(payloads.get(extension_id))),
        })
    return {
        "recorded": bool(items),
        "used": items,
        "payloads": deepcopy(payloads),
        "note": "Only extensions recorded in result metadata are shown as executed. Installed-but-unused extensions are intentionally excluded.",
    }


def _prompt_payload(record: dict[str, Any]) -> dict[str, Any]:
    params = _dict(record.get("parameters"))
    prompt_events = _list(params.get("prompt_events"))
    motion_events = _list(params.get("motion_events"))
    return {
        "positive": _text(record.get("prompt")),
        "negative": _text(record.get("negative_prompt")),
        "schedule": {
            "prompt_events": deepcopy(prompt_events),
            "motion_events": deepcopy(motion_events),
            "schedule_mode": _text(params.get("schedule_mode")),
        },
        "audio": {
            "audio_prompt": _text(params.get("audio_prompt")),
            "dialogue_prompt": _text(params.get("dialogue_prompt")),
            "soundscape_prompt": _text(params.get("soundscape_prompt")),
            "audio_mode": _text(params.get("audio_mode")),
        },
    }


def _local_path_exists(value: Any) -> bool | None:
    text = _text(value)
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    path = Path(text)
    if not path.is_absolute():
        root = Path(__file__).resolve().parents[2]
        path = root / path
    try:
        return path.exists()
    except OSError:
        return False


def _replay_validation(base: dict[str, Any]) -> dict[str, Any]:
    route_id = _text(base.get("route_id"))
    family = _text(base.get("family"))
    loader = _text(base.get("loader"))
    generation_type = _text(base.get("generation_type"))
    backend = _dict(base.get("backend"))
    profile_id = _text(backend.get("profile_id"))
    provider_id = _provider_id(base)
    reasons: list[str] = []
    warnings: list[str] = []
    route_status = "unknown"
    route_known = False

    if provider_id == "xai_grok" or route_id.startswith("xai_grok."):
        route_known = generation_type in {"txt2vid", "img2vid"}
        route_status = "provider_route" if route_known else "unsupported"
        if not profile_id:
            reasons.append("The saved cloud result does not identify a Video backend profile.")
    else:
        route = next((item for item in VIDEO_ROUTES if item.route_id == route_id), None)
        if route is None:
            route = next((item for item in VIDEO_ROUTES if item.family == family and item.loader == loader and item.generation_type == generation_type), None)
        if route:
            route_known = True
            route_status = route.status
            if route.status not in {"enabled", "experimental"}:
                reasons.append(f"The saved route is currently {route.status} and cannot be staged as a runnable route.")
        else:
            reasons.append("The saved generation route is not present in the current canonical Video route matrix.")

    source = _dict(base.get("source"))
    source_candidates = [source.get("source_image"), source.get("first_image"), source.get("last_image"), source.get("relative_path"), source.get("source_video_path")]
    missing_local = [str(value) for value in source_candidates if _local_path_exists(value) is False]
    if missing_local:
        warnings.append(f"{len(missing_local)} saved local source reference(s) are no longer present; restage them before generating.")

    if not profile_id:
        warnings.append("No backend profile id was recorded; Neo will keep the currently selected Video backend when loading this recipe.")

    loadable = route_known and not reasons
    return {
        "loadable": loadable,
        "route_known": route_known,
        "route_status": route_status,
        "backend_profile_id": profile_id,
        "provider_id": provider_id,
        "reasons": reasons,
        "warnings": warnings,
        "execution_policy": "Loading stages the recipe only. Generate remains gated by the current backend, model catalog, source readiness, and extension compatibility checks.",
    }


def _replay_payload(base: dict[str, Any]) -> dict[str, Any]:
    replay_metadata = _dict(base.get("replay_metadata"))
    payload = _dict(replay_metadata.get("replay_payload")) or _dict(replay_metadata.get("payload"))
    if not payload:
        payload = _dict(base.get("replay_payload"))
    return deepcopy(payload)


def build_video_output_inspector(record: dict[str, Any], *, record_loader: Callable[[str], Any] | None = None) -> dict[str, Any]:
    if not isinstance(record, dict) or not _text(record.get("result_id")):
        return {"ok": False, "schema_version": VIDEO_OUTPUT_INSPECTOR_SCHEMA_VERSION, "error": "A Video output record with result_id is required."}

    loader = record_loader or load_video_output_record
    outputs = _dict(record.get("outputs"))
    params = _dict(record.get("parameters"))
    backend = _dict(record.get("backend"))
    active_file = _active_file(outputs, "files", "active_file_id")
    active_preview = _active_file(outputs, "previews", "active_preview_id")
    lineage = _lineage_payload(record, loader)
    base = _nearest_generation_record(record, loader)
    replay_validation = _replay_validation(base)
    replay_payload = _replay_payload(base)
    base_result_id = _text(base.get("result_id"))
    current_result_id = _text(record.get("result_id"))

    inspector = {
        "result_id": current_result_id,
        "status": _text(record.get("status")) or "unknown",
        "category": _text(record.get("category")) or "unknown",
        "created_at": _text(record.get("created_at")),
        "updated_at": _text(record.get("updated_at")),
        "assistant_summary": _text(record.get("assistant_summary")),
        "media": {
            "active_file": active_file,
            "active_preview": active_preview,
            "files": deepcopy([item for item in _list(outputs.get("files")) if isinstance(item, dict)]),
            "previews": deepcopy([item for item in _list(outputs.get("previews")) if isinstance(item, dict)]),
            "playback_ready": bool(active_file.get("url")),
        },
        "generation": {
            "provider_id": _provider_id(record),
            "backend_profile_id": _text(backend.get("profile_id")),
            "route_id": _text(record.get("route_id")),
            "family": _text(record.get("family")),
            "loader": _text(record.get("loader")),
            "generation_type": _text(record.get("generation_type")),
            "category": _text(record.get("category")),
            "status": _text(record.get("status")),
            "models": _model_payload(record),
            "run_timing": deepcopy(_dict(record.get("run_timing"))),
            "profile": deepcopy(_dict(record.get("profile"))),
        },
        "prompts": _prompt_payload(record),
        "parameters": {
            "highlights": _parameter_highlights(params),
            "all": deepcopy(params),
        },
        "sources": _source_payload(record),
        "extensions": _extension_payload(record),
        "lineage": lineage,
        "replay": {
            "base_result_id": base_result_id,
            "uses_ancestor_recipe": base_result_id != current_result_id,
            "base_category": _text(base.get("category")),
            "base_route_id": _text(base.get("route_id")),
            "payload": replay_payload,
            "validation": replay_validation,
            "base_generation": {
                "summary": {
                    "provider_id": _provider_id(base),
                    "backend_profile_id": _text(_dict(base.get("backend")).get("profile_id")),
                    "route_id": _text(base.get("route_id")),
                    "family": _text(base.get("family")),
                    "loader": _text(base.get("loader")),
                    "generation_type": _text(base.get("generation_type")),
                    "category": _text(base.get("category")),
                    "status": _text(base.get("status")),
                    "models": _model_payload(base),
                    "run_timing": deepcopy(_dict(base.get("run_timing"))),
                },
                "prompts": _prompt_payload(base),
                "parameters": {
                    "highlights": _parameter_highlights(_dict(base.get("parameters"))),
                    "all": deepcopy(_dict(base.get("parameters"))),
                },
                "sources": _source_payload(base),
                "extensions": _extension_payload(base),
            },
        },
        "diagnostics": {
            "errors": deepcopy(_list(record.get("errors"))),
            "warnings": deepcopy(_list(record.get("warnings"))),
            "import_status": deepcopy(_dict(record.get("import_status"))),
            "finish": deepcopy(_dict(record.get("finish"))),
            "output_metadata": deepcopy(_dict(record.get("output_metadata"))),
            "memory_export": deepcopy(_dict(record.get("memory_export"))),
        },
        "expert": {
            "record_schema_version": _text(record.get("schema_version")),
            "replay_schema_version": _text(_dict(record.get("replay_metadata")).get("schema_version")),
            "record": deepcopy(record),
        },
    }
    return {"ok": True, "schema_version": VIDEO_OUTPUT_INSPECTOR_SCHEMA_VERSION, "inspector": inspector}


def video_output_inspector_payload(result_id: str) -> dict[str, Any]:
    loaded = load_video_output_record(result_id)
    if not loaded.get("ok") or not isinstance(loaded.get("record"), dict):
        return {
            "ok": False,
            "schema_version": VIDEO_OUTPUT_INSPECTOR_SCHEMA_VERSION,
            "result_id": _text(result_id),
            "error": loaded.get("error") or "Video result could not be loaded.",
        }
    return build_video_output_inspector(loaded["record"])
