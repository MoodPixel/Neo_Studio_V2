from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sqlite_store import _connect, ensure_roleplay_memory_schema, roleplay_sqlite_state_payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned[:72] or "runtime-bundle"




def _clean(value: Any) -> str:
    return str(value or "").strip()


def _flatten_runtime_record_context(record: dict[str, Any], *, max_chars: int = 1200) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
    if not isinstance(payload, dict):
        return _clean(record.get("body") or record.get("summary"))[:max_chars]
    parts: list[str] = []
    def add(label: str, value: Any) -> None:
        text = _clean(value)
        if text:
            parts.append(f"{label}: {text}")
    add("summary", record.get("body") or payload.get("summary") or record.get("summary"))
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    def walk(value: Any, path: str = "") -> None:
        if len("\n".join(parts)) >= max_chars:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"links", "meta", "sandbox_context", "memory_hints"}:
                    continue
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            text = ", ".join(_clean(x) for x in value if _clean(x))
            add(path.replace("_", " "), text)
        else:
            add(path.replace("_", " "), value)
    walk(fields, "fields")
    text = "\n".join(parts)
    return (text[: max_chars - 1].rstrip() + "…") if len(text) > max_chars else text

def _runtime_dir() -> Path:
    return ROLEPLAY_DATA_ROOT / "runtime_bundles"


def _json_records(directory: Path, limit: int | None = None) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    paths = sorted(directory.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if limit is not None:
        paths = paths[:limit]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except Exception:
            continue
        data.setdefault("record_id", path.stem)
        data.setdefault("storage_path", _relative_to_root(path))
        records.append(data)
    return records




def _parse_json_value(raw: Any, fallback: Any = None) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _sqlite_memory_fragments(record_ids: list[str] | None = None, scope_id: str = "", limit: int | None = 120) -> list[dict[str, Any]]:
    """Read compiled Builder memory fragments from SQLite for runtime bundles.

    Older runtime bundles only scanned neo_data/roleplay/memory_fragments/*.json,
    but Builder compile writes authoritative memory to rp_memory_fragments. This
    bridges Compile -> Runtime so readiness counts reflect actual compiled memory.
    """
    ensure_roleplay_memory_schema()
    record_id_set = {str(item).strip() for item in (record_ids or []) if str(item).strip()}
    clean_scope = str(scope_id or "").strip()
    lim = max(1, min(int(limit or 120), 5000))
    where = ["status NOT IN ('superseded','archived')"]
    params: list[Any] = []
    if record_id_set:
        placeholders = ",".join("?" for _ in record_id_set)
        where.append(f"source_id IN ({placeholders})")
        params.extend(sorted(record_id_set))
    elif clean_scope:
        where.append("(source_id = ? OR universe_id = ? OR world_id = ? OR region_id = ? OR city_id = ? OR location_id = ? OR payload_json LIKE ? OR sandbox_json LIKE ?)")
        params.extend([clean_scope, clean_scope, clean_scope, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", f"%{clean_scope}%"])
    sql = f"""
        SELECT fragment_id, namespace, source_type, source_id, memory_type, status,
               content, tags_json, payload_json, memory_scope, universe_id, world_id,
               region_id, city_id, location_id, updated_at, created_at
        FROM rp_memory_fragments
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN source_id = ? THEN 0 ELSE 1 END,
            updated_at DESC, created_at DESC
        LIMIT ?
    """
    params.extend([clean_scope, lim])
    out: list[dict[str, Any]] = []
    try:
        with _connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    for row in rows:
        payload = _parse_json_value(row.get("payload_json"), {}) or {}
        out.append({
            "fragment_id": str(row.get("fragment_id") or ""),
            "namespace": str(row.get("namespace") or ""),
            "source_type": str(row.get("source_type") or ""),
            "source_id": str(row.get("source_id") or ""),
            "memory_type": str(row.get("memory_type") or ""),
            "status": str(row.get("status") or ""),
            "content": str(row.get("content") or ""),
            "tags": _parse_json_value(row.get("tags_json"), []) or [],
            "payload": payload if isinstance(payload, dict) else {},
            "scope_id": str((payload if isinstance(payload, dict) else {}).get("scope_id") or row.get("memory_scope") or ""),
            "universe_id": str(row.get("universe_id") or ""),
            "world_id": str(row.get("world_id") or ""),
            "region_id": str(row.get("region_id") or ""),
            "city_id": str(row.get("city_id") or ""),
            "location_id": str(row.get("location_id") or ""),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        })
    return out


def ensure_runtime_storage() -> None:
    ensure_roleplay_foundation(write_manifest=True)
    _runtime_dir().mkdir(parents=True, exist_ok=True)


def _bundle_path(bundle_id: str) -> Path:
    return _runtime_dir() / f"{_slug(bundle_id)}.json"


def _read_bundle(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("bundle_id", path.stem)
            data.setdefault("storage_path", _relative_to_root(path))
            return data
    except Exception:
        return None
    return None


def list_runtime_bundles() -> list[dict[str, Any]]:
    ensure_runtime_storage()
    bundles = [_read_bundle(path) for path in sorted(_runtime_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)]
    return [bundle for bundle in bundles if bundle]


def get_runtime_bundle(bundle_id: str) -> dict[str, Any] | None:
    ensure_runtime_storage()
    clean = _slug(bundle_id)
    path = _bundle_path(clean)
    if path.exists():
        return _read_bundle(path)
    for bundle in list_runtime_bundles():
        if str(bundle.get("bundle_id") or "") == bundle_id:
            return bundle
    return None


def _records_for_kinds(kinds: list[str], limit: int | None = None) -> list[dict[str, Any]]:
    entity_root = ROLEPLAY_DATA_ROOT / "entities"
    records: list[dict[str, Any]] = []
    if kinds:
        for kind in kinds:
            records.extend(_json_records(entity_root / kind, limit=None))
    else:
        records.extend(_json_records(entity_root, limit=None))
    records = sorted(records, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    if limit is not None:
        records = records[:limit]
    return records


def _source_records(project_id: str = "", limit: int | None = None) -> list[dict[str, Any]]:
    records = _json_records(ROLEPLAY_DATA_ROOT / "source_documents", limit=None)
    if project_id:
        records = [record for record in records if str(record.get("project_id") or "") == project_id]
    if limit is not None:
        records = records[:limit]
    return records


def runtime_compile_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_storage()
    payload = payload or {}
    now = _now()
    title = str(payload.get("title") or payload.get("bundle_title") or "Roleplay Runtime Bundle").strip() or "Roleplay Runtime Bundle"
    project_id = str(payload.get("project_id") or "").strip()
    include_kinds = payload.get("include_kinds") or []
    if isinstance(include_kinds, str):
        include_kinds = [item.strip() for item in include_kinds.split(",") if item.strip()]
    if not isinstance(include_kinds, list):
        include_kinds = []
    max_entities = int(payload.get("max_entities") or 80)
    max_sources = int(payload.get("max_sources") or 24)
    max_memory_fragments = int(payload.get("max_memory_fragments") or 120)
    record_ids = payload.get("record_ids") or []
    if isinstance(record_ids, str):
        record_ids = [item.strip() for item in record_ids.split(",") if item.strip()]
    if not isinstance(record_ids, list):
        record_ids = []
    record_id_set = {str(item).strip() for item in record_ids if str(item).strip()}
    scope_type = str(payload.get("scope_type") or "").strip()
    scope_id = str(payload.get("scope_id") or "").strip()

    forge_records = _records_for_kinds([str(item) for item in include_kinds], None if record_id_set else max_entities)
    if record_id_set:
        forge_records = [item for item in forge_records if str(item.get("record_id") or item.get("id") or "") in record_id_set]
        forge_records = forge_records[:max_entities]
    sources = _source_records(project_id, max_sources)
    storylines = _json_records(ROLEPLAY_DATA_ROOT / "storylines", limit=20)
    sessions = _json_records(ROLEPLAY_DATA_ROOT / "story_sessions", limit=20)
    checkpoints = _json_records(ROLEPLAY_DATA_ROOT / "story_checkpoints", limit=20)
    memory_fragments = _sqlite_memory_fragments(list(record_id_set), scope_id, max_memory_fragments)
    if not memory_fragments:
        memory_fragments = _json_records(ROLEPLAY_DATA_ROOT / "memory_fragments", limit=max_memory_fragments)
    sqlite_state = roleplay_sqlite_state_payload()
    engine_bridge = roleplay_engine_bridge_state()

    bundle_id = str(payload.get("bundle_id") or f"runtime_{_slug(title)}_{uuid.uuid4().hex[:8]}")
    path = _bundle_path(bundle_id)
    scene_packet = {
        "packet_id": f"scene_packet_{uuid.uuid4().hex[:8]}",
        "title": title,
        "project_id": project_id,
        "summary": str(payload.get("summary") or "Runtime packet compiled from Forge, Studio, Stories, and Roleplay memory rows."),
        "generation_enabled": True,
        "binding_ready": True,
        "memory_injection_enabled": True,
        "vector_search_enabled": True,
        "reranker_enabled": True,
    }
    bundle = {
        "schema_id": "neo.roleplay.runtime.bundle.v1",
        "version": "1.0.0-runtime-bundle",
        "bundle_id": bundle_id,
        "title": title,
        "status": "compiled",
        "project_id": project_id,
        "created_at": now,
        "updated_at": now,
        "storage_path": _relative_to_root(path),
        "compile_request": {
            "include_kinds": include_kinds,
            "record_ids": sorted(record_id_set),
            "scope_type": scope_type,
            "scope_id": scope_id,
            "max_entities": max_entities,
            "max_sources": max_sources,
            "max_memory_fragments": max_memory_fragments,
        },
        "counts": {
            "forge_records": len(forge_records),
            "sources": len(sources),
            "storylines": len(storylines),
            "story_sessions": len(sessions),
            "story_checkpoints": len(checkpoints),
            "memory_fragments": len(memory_fragments),
            "sqlite_entities": int((sqlite_state.get("table_counts") or {}).get("rp_entities") or 0),
            "sqlite_memory_fragments": int((sqlite_state.get("table_counts") or {}).get("rp_memory_fragments") or 0),
        },
        "included_entities": [
            {
                "record_id": str(item.get("record_id") or item.get("id") or ""),
                "kind": str(item.get("kind") or ""),
                "title": str(item.get("title") or item.get("label") or item.get("display_label") or item.get("record_id") or ""),
                "status": str(item.get("status") or (item.get("payload") or {}).get("meta", {}).get("status") or "draft"),
                "summary": str(item.get("body") or item.get("summary") or (item.get("payload") or {}).get("summary") or ""),
                "content": _flatten_runtime_record_context(item, max_chars=1200),
                "storage_path": item.get("storage_path") or "",
            }
            for item in forge_records
        ],
        "included_sources": [
            {"source_id": str(item.get("source_id") or item.get("record_id") or ""), "project_id": str(item.get("project_id") or ""), "title": str(item.get("title") or item.get("record_id") or ""), "source_type": str(item.get("source_type") or "text"), "body_preview": str(item.get("body_preview") or item.get("body") or "")[:280], "storage_path": item.get("storage_path") or ""}
            for item in sources
        ],
        "included_storylines": [
            {"storyline_id": str(item.get("storyline_id") or item.get("record_id") or ""), "title": str(item.get("title") or item.get("record_id") or ""), "status": str(item.get("status") or "foundation"), "storage_path": item.get("storage_path") or ""}
            for item in storylines
        ],
        "included_story_sessions": [
            {"session_id": str(item.get("session_id") or item.get("record_id") or ""), "storyline_id": str(item.get("storyline_id") or ""), "title": str(item.get("title") or item.get("record_id") or ""), "status": str(item.get("status") or "draft"), "storage_path": item.get("storage_path") or ""}
            for item in sessions
        ],
        "included_story_checkpoints": [
            {"checkpoint_id": str(item.get("checkpoint_id") or item.get("record_id") or ""), "storyline_id": str(item.get("storyline_id") or ""), "session_id": str(item.get("session_id") or ""), "title": str(item.get("title") or item.get("record_id") or ""), "status": str(item.get("status") or "foundation"), "storage_path": item.get("storage_path") or ""}
            for item in checkpoints
        ],
        "memory_snapshot": {
            "sqlite": sqlite_state,
            "fragment_count": len(memory_fragments),
            "fragments_preview": memory_fragments[:16],
            "scope_type": scope_type,
            "scope_id": scope_id,
        },
        "engine_snapshot": {
            "source": "admin",
            "ready": bool(engine_bridge.get("ready")),
            "text_bridge_ready": bool(engine_bridge.get("text_bridge_ready")),
            "embedding_provider": (engine_bridge.get("embedding_profiles") or {}).get("active_provider_id") or "",
            "embedding_model_path": (engine_bridge.get("embedding_profiles") or {}).get("active_model_path") or "",
            "reranker_provider": (engine_bridge.get("reranker_profiles") or {}).get("active_provider_id") or "",
            "reranker_model_path": (engine_bridge.get("reranker_profiles") or {}).get("active_model_path") or "",
            "vector_store": engine_bridge.get("vector_store") or {},
            "runtime_defaults": engine_bridge.get("runtime_defaults") or {},
            "retrieval_defaults": engine_bridge.get("retrieval_defaults") or {},
        },
        "scene_packet": scene_packet,
        "active_features": [
            "scene_generation_context",
            "runtime_bundle_binding",
            "memory_snapshot",
            "admin_engine_snapshot",
            "semantic_retrieval_ready",
        ],
        "deferred_features": [
            "streaming_generation_context_patching",
            "chroma_collection_export",
            "automatic_checkpoint_branching",
        ],
    }
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "schema_id": "neo.roleplay.runtime.compile.v1",
        "surface_id": "roleplay",
        "tab_id": "studio",
        "status": "compiled",
        "bundle": bundle,
        "runtime": runtime_state_payload(),
    }


def runtime_state_payload() -> dict[str, Any]:
    ensure_runtime_storage()
    bundles = list_runtime_bundles()
    latest = bundles[0] if bundles else {}
    return {
        "schema_id": "neo.roleplay.runtime.state.v1",
        "version": "1.0.0-runtime-bundle",
        "surface_id": "roleplay",
        "status": "foundation",
        "ready": True,
        "bundle_root": _relative_to_root(_runtime_dir()),
        "bundle_count": len(bundles),
        "bundles": [
            {
                "bundle_id": str(item.get("bundle_id") or ""),
                "title": str(item.get("title") or item.get("bundle_id") or ""),
                "status": str(item.get("status") or "foundation"),
                "project_id": str(item.get("project_id") or ""),
                "created_at": str(item.get("created_at") or ""),
                "storage_path": str(item.get("storage_path") or ""),
                "counts": item.get("counts") or {},
                "selectable": True,
            }
            for item in bundles
        ],
        "latest_bundle_id": str(latest.get("bundle_id") or ""),
        "compile_ready": True,
        "scene_binding_ready": bool(bundles),
        "deferred_steps": ["streaming_generation", "external_embedding_execution", "external_reranker_execution", "chroma_collection_write", "automatic_scene_memory_injection"],
    }


def runtime_bundle_payload(bundle_id: str) -> dict[str, Any]:
    bundle = get_runtime_bundle(bundle_id)
    return {
        "schema_id": "neo.roleplay.runtime.bundle.read.v1",
        "surface_id": "roleplay",
        "status": "found" if bundle else "missing",
        "bundle": bundle or {},
    }
