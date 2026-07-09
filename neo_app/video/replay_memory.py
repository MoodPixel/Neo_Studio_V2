from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Final

from neo_app.memory.surface_ingestion import DEFAULT_MEMORY_DB, UnifiedMemoryWriter
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import (
    VIDEO_OUTPUT_RECORD_SCHEMA_VERSION,
    build_assistant_summary,
    build_video_replay_payload,
    list_video_output_records,
    load_video_output_record,
)

VIDEO_REPLAY_METADATA_SCHEMA_VERSION: Final[str] = "neo.video.replay_metadata.v22"
VIDEO_MEMORY_EXPORT_SCHEMA_VERSION: Final[str] = "neo.video.memory_export.v22"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, indent=2, ensure_ascii=False, sort_keys=True)


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _metadata_path(result_id: str) -> Path:
    return get_video_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(result_id, 'video')}.json")


def _replay_sidecar_path(result_id: str) -> Path:
    return get_video_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(result_id, 'video')}.replay.v22.json")


def _memory_sidecar_path(result_id: str) -> Path:
    return get_video_output_paths("metadata", create=True).output_file(f"{sanitize_path_part(result_id, 'video')}.memory_export.v22.json")


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text.replace("\x00", "").strip()[:limit]


def _asset_list(record: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    for key in ["source_image", "first_image", "last_image", "relative_path", "source_video"]:
        value = source.get(key)
        if value:
            assets.append({"role": key, "path": str(value), "neo_owned": str(value).startswith("neo_data/outputs/video")})
    segments = source.get("segments") if isinstance(source.get("segments"), list) else []
    for index, segment in enumerate(segments, start=1):
        if isinstance(segment, dict) and (segment.get("image") or segment.get("image_name")):
            assets.append({"role": f"segment_{index}", "path": str(segment.get("image") or segment.get("image_name") or ""), "neo_owned": str(segment.get("image") or "").startswith("neo_data/outputs/video")})
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    for bucket in ["files", "previews"]:
        for item in outputs.get(bucket) or []:
            if isinstance(item, dict):
                assets.append({"role": item.get("role") or bucket, "path": item.get("path") or "", "file_id": item.get("file_id") or "", "neo_owned": str(item.get("path") or "").startswith("neo_data/outputs/video")})
    return assets


def _event_type_for_record(record: dict[str, Any]) -> str:
    category = str(record.get("category") or "txt2vid")
    status = str(record.get("status") or "unknown").lower()
    if status == "failed":
        return "video.generation.failed"
    if status == "queued":
        return "video.generation.queued"
    if status == "compiled":
        return "video.generation.compiled"
    if category in {"interpolate", "upscale", "repair"}:
        return f"video.finish.{category}"
    if category in {"extend", "vid2vid", "depth_motion", "schedule", "audio_video", "first_last_frame", "multiscene"}:
        return f"video.generation.{category}"
    return "video.generation.completed"


def build_video_replay_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical V22 replay object from a Video ledger record."""
    params = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    replay_payload = record.get("replay_payload") if isinstance(record.get("replay_payload"), dict) else build_video_replay_payload(record)
    summary = record.get("assistant_summary") or build_assistant_summary(record)
    memory_summary = {
        "title": f"Video {record.get('result_id', '')} · {record.get('category', '')}",
        "summary": summary,
        "event_type": _event_type_for_record(record),
        "importance": "high" if record.get("category") in {"vid2vid", "depth_motion", "audio_video", "multiscene"} else "normal",
        "facts": [
            f"Video result {record.get('result_id')} uses route {record.get('route_id', '')}.",
            f"Video result {record.get('result_id')} category is {record.get('category', '')}.",
            f"Video result {record.get('result_id')} status is {record.get('status', '')}.",
        ],
    }
    if params.get("width") and params.get("height"):
        memory_summary["facts"].append(f"Video result {record.get('result_id')} resolution is {params.get('width')}x{params.get('height')}.")
    if params.get("frames") and params.get("fps"):
        memory_summary["facts"].append(f"Video result {record.get('result_id')} timing is {params.get('frames')} frames at {params.get('fps')} fps.")
    return {
        "schema_version": VIDEO_REPLAY_METADATA_SCHEMA_VERSION,
        "surface": "video",
        "phase": "V22",
        "created_at": utc_now_iso(),
        "result": {
            "result_id": record.get("result_id") or "",
            "status": record.get("status") or "unknown",
            "category": record.get("category") or "txt2vid",
            "route_id": record.get("route_id") or "",
            "family": record.get("family") or "wan22",
            "loader": record.get("loader") or "unet",
            "generation_type": record.get("generation_type") or "txt2vid",
        },
        "prompt": {
            "positive": record.get("prompt") or "",
            "negative": record.get("negative_prompt") or "",
        },
        "parameters": params,
        "profile": record.get("profile") if isinstance(record.get("profile"), dict) else {},
        "source_assets": _asset_list(record),
        "outputs": record.get("outputs") if isinstance(record.get("outputs"), dict) else {"files": [], "previews": []},
        "lineage": record.get("lineage") if isinstance(record.get("lineage"), dict) else {},
        "backend": record.get("backend") if isinstance(record.get("backend"), dict) else {},
        "replay_payload": replay_payload,
        "memory_summary": memory_summary,
        "reuse_hint": {
            "description": "Use replay_payload to restore the Video UI route, prompt, parameters, and source references.",
            "safe_to_replay_without_sources": not any(asset.get("role") in {"source_image", "first_image", "last_image", "relative_path", "source_video"} for asset in _asset_list(record)),
            "requires_neo_owned_sources": True,
        },
    }


def upgrade_video_record_to_v22(record: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """Attach canonical replay metadata and memory export hints to a Video result record."""
    result_id = sanitize_path_part(str(record.get("result_id") or "video"), "video")
    metadata = build_video_replay_metadata(record)
    sidecar = _replay_sidecar_path(result_id)
    sidecar.write_text(_json_dumps(metadata), encoding="utf-8")
    record["schema_version"] = VIDEO_OUTPUT_RECORD_SCHEMA_VERSION
    record["replay_payload"] = metadata["replay_payload"]
    record["replay_metadata"] = metadata
    record["memory_export"] = {
        "schema_version": VIDEO_MEMORY_EXPORT_SCHEMA_VERSION,
        "status": "pending",
        "last_exported_at": "",
        "sidecar_path": "",
        "event_ids": [],
        "object_ids": [],
        "fragment_ids": [],
        "fact_ids": [],
    }
    record["phase"] = "V22"
    record["updated_at"] = utc_now_iso()
    record.setdefault("rules", [])
    if "V22 replay metadata is the canonical Video re-use and memory handoff object." not in record["rules"]:
        record["rules"].append("V22 replay metadata is the canonical Video re-use and memory handoff object.")
    if "V22 memory export writes confirmed Video events into unified SQLite memory without requiring embeddings." not in record["rules"]:
        record["rules"].append("V22 memory export writes confirmed Video events into unified SQLite memory without requiring embeddings.")
    record["replay_metadata_path"] = _relative_to_root(sidecar)
    if persist:
        _metadata_path(result_id).write_text(_json_dumps(record), encoding="utf-8")
    return record


def video_replay_metadata_payload(result_id: str) -> dict[str, Any]:
    loaded = load_video_output_record(result_id)
    if not loaded.get("ok"):
        return loaded
    record = upgrade_video_record_to_v22(dict(loaded["record"]), persist=True)
    return {"ok": True, "schema_version": VIDEO_REPLAY_METADATA_SCHEMA_VERSION, "result_id": record["result_id"], "replay_metadata": record["replay_metadata"], "record": record}


def export_video_record_to_memory(record: dict[str, Any], *, db_path: Path = DEFAULT_MEMORY_DB) -> dict[str, Any]:
    """Write a Video result record into unified memory as event/object/fragment/facts."""
    record = upgrade_video_record_to_v22(dict(record), persist=False)
    metadata = record["replay_metadata"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    writer = UnifiedMemoryWriter(conn)
    try:
        project_id = writer.upsert_project(project_id="video", label="Video", surface="video", project_type="surface", description="Video generation, finish, replay, and output memory sandbox.", metadata={"schema_version": VIDEO_MEMORY_EXPORT_SCHEMA_VERSION})
        root_scope = writer.upsert_scope(surface="video", project_id=project_id, scope_type="root", scope_key="video", label="Video Root")
        category = str(record.get("category") or "txt2vid")
        scope_id = writer.upsert_scope(surface="video", project_id=project_id, scope_type="video_category", scope_key=category, label=f"Video · {category}", parent_scope_id=root_scope, metadata={"route_id": record.get("route_id")})
        event_id = writer.upsert_event(
            surface="video",
            project_id=project_id,
            scope_id=scope_id,
            source_type="video_output_record",
            source_id=str(record.get("result_id") or ""),
            event_type=metadata["memory_summary"]["event_type"],
            title=metadata["memory_summary"]["title"],
            summary=metadata["memory_summary"]["summary"],
            payload=metadata,
            metadata={"record_path": record.get("record_path") or "", "replay_metadata_path": record.get("replay_metadata_path") or "", "category": category, "route_id": record.get("route_id")},
            importance=metadata["memory_summary"].get("importance", "normal"),
            trust_level="confirmed",
            created_at=record.get("created_at") or metadata.get("created_at"),
        )
        object_id = writer.upsert_object(
            surface="video",
            project_id=project_id,
            scope_id=scope_id,
            object_type="video_result",
            object_key=str(record.get("result_id") or ""),
            label=metadata["memory_summary"]["title"],
            summary=metadata["memory_summary"]["summary"],
            attributes=metadata,
            metadata={"source_event_id": event_id, "category": category},
        )
        fragment_id = writer.upsert_fragment(
            surface="video",
            project_id=project_id,
            scope_id=scope_id,
            source_type="video_output_record",
            source_id=str(record.get("result_id") or ""),
            memory_type="video_replay_metadata",
            title=metadata["memory_summary"]["title"],
            content=_json_dumps(metadata),
            summary=metadata["memory_summary"]["summary"],
            priority=0.75,
            confidence=0.95,
            trust_level="confirmed",
            metadata={"source_event_id": event_id, "object_id": object_id},
        )
        fact_ids: list[str] = []
        for fact in metadata["memory_summary"].get("facts", []):
            fid = writer.upsert_fact(surface="video", project_id=project_id, scope_id=scope_id, subject_id=object_id, predicate="has_video_result_fact", object_value=str(record.get("result_id") or ""), statement=fact, fact_type="video_result", source_event_id=event_id, confidence=0.9, trust_level="confirmed", metadata={"category": category})
            if fid:
                fact_ids.append(fid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    export = {
        "schema_version": VIDEO_MEMORY_EXPORT_SCHEMA_VERSION,
        "status": "exported",
        "exported_at": utc_now_iso(),
        "db_path": _relative_to_root(db_path),
        "result_id": record.get("result_id") or "",
        "event_ids": [event_id],
        "object_ids": [object_id],
        "fragment_ids": [fragment_id] if fragment_id else [],
        "fact_ids": fact_ids,
    }
    sidecar = _memory_sidecar_path(str(record.get("result_id") or "video"))
    sidecar.write_text(_json_dumps(export), encoding="utf-8")
    record["memory_export"] = {**export, "sidecar_path": _relative_to_root(sidecar)}
    record["updated_at"] = utc_now_iso()
    _metadata_path(str(record.get("result_id") or "video")).write_text(_json_dumps(record), encoding="utf-8")
    return {"ok": True, "export": record["memory_export"], "record": record}


def video_memory_export_payload(result_id: str | None = None, *, limit: int = 50, db_path: Path = DEFAULT_MEMORY_DB) -> dict[str, Any]:
    if result_id:
        loaded = load_video_output_record(result_id)
        if not loaded.get("ok"):
            return loaded
        exported = export_video_record_to_memory(loaded["record"], db_path=db_path)
        return {"ok": True, "schema_version": VIDEO_MEMORY_EXPORT_SCHEMA_VERSION, "mode": "single", "exports": [exported["export"]], "record": exported["record"]}
    records_payload = list_video_output_records(limit=limit)
    exports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for record in records_payload.get("records", []):
        try:
            exports.append(export_video_record_to_memory(record, db_path=db_path)["export"])
        except Exception as exc:  # noqa: BLE001 - export should continue for other records.
            errors.append({"result_id": str(record.get("result_id") or ""), "error": str(exc)[:800]})
    return {"ok": not errors, "schema_version": VIDEO_MEMORY_EXPORT_SCHEMA_VERSION, "mode": "batch", "count": len(exports), "exports": exports, "errors": errors}
