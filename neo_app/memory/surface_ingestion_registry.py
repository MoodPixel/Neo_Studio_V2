from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from neo_app.context_identity import builtin_scope_for_surface, normalize_identity_id, resolve_canonical_identity
from neo_app.surfaces.registry import list_surfaces

SURFACE_INGESTION_REGISTRY_SCHEMA_ID = "neo.memory.surface_ingestion_registry.phase8.v1"
SURFACE_MEMORY_EVENT_SCHEMA_ID = "neo.memory.surface_event.phase8.v1"

_SUCCESS_STATES = {"completed", "complete", "success", "succeeded", "ready", "saved", "indexed", "generated", "compiled", "exported"}
_NON_DURABLE_STATES = {"failed", "error", "cancelled", "canceled", "aborted", "invalid", "running", "active", "queued", "pending"}


@dataclass(frozen=True, slots=True)
class SurfaceIngestionAdapter:
    surface_id: str
    label: str
    batch_method: str = ""
    live_enabled: bool = True
    live_event_prefixes: tuple[str, ...] = ()
    memory_type: str = "surface_task_history"
    object_type: str = "surface_task"
    producer_mode: str = "registry_live"
    policy: str = "successful_useful_only"
    notes: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


# The registry is intentionally explicit. The Surface Manifest still owns which
# surfaces exist; this registry owns only memory-ingestion capability for them.
_ADAPTERS: dict[str, SurfaceIngestionAdapter] = {
    "image": SurfaceIngestionAdapter(
        "image", "Image", "ingest_image", True,
        ("image.generation.", "image.result.", "image.job."),
        "image_generation_metadata", "image_task",
    ),
    "video": SurfaceIngestionAdapter(
        "video", "Video", "ingest_video", True,
        ("video.generation.", "video.result.", "video.finish."),
        "video_replay_metadata", "video_task",
    ),
    "voice": SurfaceIngestionAdapter(
        "voice", "Voice", "ingest_voice", True,
        ("voice.preview.", "voice.render.", "voice.dialogue.", "voice.job."),
        "voice_replay_metadata", "voice_task",
    ),
    "prompt_captioning": SurfaceIngestionAdapter(
        "prompt_captioning", "Prompt + Captioning", "ingest_prompt_captioning", True,
        ("prompt_captioning.prompt.", "prompt_captioning.caption.", "prompt_captioning.asset."),
        "prompt_captioning_output", "prompt_captioning_task",
    ),
    "roleplay": SurfaceIngestionAdapter(
        "roleplay", "Roleplay", "ingest_roleplay", False,
        ("roleplay.",), "roleplay_fragment", "roleplay_memory",
        producer_mode="sandbox_replay",
        policy="roleplay_authoritative_db_only",
        notes="Roleplay canon/universe writes remain authoritative in the Roleplay DB; registry replay imports sandboxed rows into Unified Memory.",
    ),
    "assistant": SurfaceIngestionAdapter(
        "assistant", "Assistant", "ingest_projects", True,
        ("assistant.task.", "assistant.memory."),
        "assistant_task_history", "assistant_task",
        policy="explicit_substantial_tasks_only",
        notes="Normal chat/chatter is not auto-ingested. Only explicitly emitted substantial task or memory events are eligible.",
    ),
    "board": SurfaceIngestionAdapter(
        "board", "Board", "", True,
        ("board.asset.pinned", "board.asset.sent_to_surface", "board.workflow."),
        "board_activity", "board_item",
        notes="No batch replay exists yet; Board can emit live registry events when its runtime surface is promoted.",
    ),
    "music": SurfaceIngestionAdapter(
        "music", "Music", "", True,
        ("music.result.", "music.project."),
        "music_activity", "music_task",
        notes="Future-ready registry contract; no current batch importer.",
    ),
}

_LAST_RESULTS: dict[str, dict[str, Any]] = {}

_ALIAS_MAP = {
    "prompt": "prompt_captioning",
    "caption": "prompt_captioning",
    "captioning": "prompt_captioning",
    "projects": "assistant",
    "project": "assistant",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Any, length: int = 24) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _remember_last_result(surface_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "surface_id": surface_id,
        "status": payload.get("status") or "unknown",
        "ok": payload.get("ok"),
        "reason": payload.get("reason") or "",
        "event_id": payload.get("event_id") or "",
        "fragment_id": payload.get("fragment_id") or "",
        "updated_at": _now(),
    }
    _LAST_RESULTS[surface_id] = clean
    return payload


def canonical_surface_id(value: Any) -> str:
    clean = normalize_identity_id(value)
    return _ALIAS_MAP.get(clean, clean)


def get_surface_ingestion_adapter(surface_id: Any) -> SurfaceIngestionAdapter | None:
    return _ADAPTERS.get(canonical_surface_id(surface_id))


def registered_surface_ingestion_adapters() -> list[SurfaceIngestionAdapter]:
    return list(_ADAPTERS.values())


def _manifest_surface_ids() -> set[str]:
    try:
        return {str(surface.surface_id) for surface in list_surfaces(include_disabled=True)}
    except Exception:
        return set(_ADAPTERS)


def surface_ingestion_registry_status() -> dict[str, Any]:
    manifest_ids = _manifest_surface_ids()
    adapters = []
    for adapter in registered_surface_ingestion_adapters():
        item = adapter.payload()
        item["manifest_registered"] = adapter.surface_id in manifest_ids
        item["batch_supported"] = bool(adapter.batch_method)
        item["live_supported"] = bool(adapter.live_enabled)
        adapters.append(item)
    unsupported = sorted(surface for surface in manifest_ids if surface not in _ADAPTERS and surface not in {"admin"})
    return {
        "schema_id": SURFACE_INGESTION_REGISTRY_SCHEMA_ID,
        "phase": "8",
        "status": "ready",
        "registered_count": len(adapters),
        "adapters": adapters,
        "unsupported_manifest_surfaces": unsupported,
        "last_results": {key: dict(value) for key, value in sorted(_LAST_RESULTS.items())},
        "policy": {
            "source_of_truth": "Unified Memory SQLite",
            "batch_replay": "Legacy surface files/DBs are compatibility replay sources behind registry adapters.",
            "live_events": "Only successful/useful events become searchable history automatically.",
            "failures": "Failed/transient UI events are not promoted into searchable durable fragments by this registry.",
            "roleplay": "Roleplay remains sandbox-owned and is imported only through its sandbox-aware replay adapter.",
            "durable_promotion": "Phase 9 evaluates successful history separately for durable candidates; repeated low-risk patterns may promote, while preferences/project decisions/contradictions/cross-project/canon-sensitive changes remain review-gated.",
        },
    }


def execute_registered_batch_ingestion(ingestor: Any, surfaces: Iterable[str] | None = None, *, limit: int | None = None) -> dict[str, Any]:
    """Dispatch legacy/batch ingestion through the Phase 8 registry.

    The ingestor object supplies the historical implementation methods. This
    keeps M3 replay compatibility while removing hard-coded surface ownership
    from ``SurfaceMemoryIngestor.run``.
    """
    requested = list(surfaces or ["assistant", "image", "prompt_captioning", "roleplay", "video", "voice"])
    executed: list[str] = []
    unsupported: list[str] = []
    duplicate_surfaces: set[str] = set()
    seen: set[str] = set()
    for raw in requested:
        surface_id = canonical_surface_id(raw)
        if surface_id in seen:
            duplicate_surfaces.add(surface_id)
            continue
        seen.add(surface_id)
        adapter = get_surface_ingestion_adapter(surface_id)
        if not adapter or not adapter.batch_method:
            unsupported.append(surface_id or str(raw))
            continue
        method = getattr(ingestor, adapter.batch_method, None)
        if not callable(method):
            unsupported.append(surface_id)
            continue
        if adapter.batch_method == "ingest_projects":
            method()
        else:
            method(limit=limit)
        executed.append(surface_id)
        _remember_last_result(surface_id, {"ok": True, "status": "batch_replayed", "reason": adapter.batch_method})
    return {
        "schema_id": SURFACE_INGESTION_REGISTRY_SCHEMA_ID,
        "requested": [canonical_surface_id(item) for item in requested],
        "executed": executed,
        "unsupported": unsupported,
        "deduplicated_requests": sorted(duplicate_surfaces),
    }


def _event_prefix_allowed(adapter: SurfaceIngestionAdapter, event_type: str) -> bool:
    if not adapter.live_event_prefixes:
        return False
    return any(event_type == prefix or event_type.startswith(prefix) for prefix in adapter.live_event_prefixes)


def _is_searchable_event(adapter: SurfaceIngestionAdapter, event: dict[str, Any]) -> tuple[bool, str]:
    event_type = str(event.get("event_type") or "").strip().lower()
    status = str(event.get("status") or event.get("state") or "").strip().lower()
    if not adapter.live_enabled:
        return False, "live_ingestion_disabled"
    if not event_type or not _event_prefix_allowed(adapter, event_type):
        return False, "event_type_not_registered"
    if status in _NON_DURABLE_STATES:
        return False, f"non_durable_status:{status}"
    if status and status not in _SUCCESS_STATES:
        return False, f"unrecognized_status:{status}"
    # Assistant chat is deliberately stricter to avoid turning casual chatter
    # into searchable long-term task history before Phase 9 writeback policy.
    if adapter.surface_id == "assistant" and not (
        event_type.startswith("assistant.task.") or event_type.startswith("assistant.memory.")
    ):
        return False, "assistant_event_not_explicitly_durable"
    return True, "successful_useful_event"


def _safe_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_text(value: Any, limit: int = 8000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = str(value)
    return text.replace("\x00", "").strip()[:limit]


def _redacted_payload(event: dict[str, Any]) -> dict[str, Any]:
    blocked = {"api_key", "apikey", "password", "secret", "token", "authorization", "base64", "bytes", "binary"}
    keep = {
        "surface_id", "scope_id", "project_id", "event_type", "status", "source_id", "title", "summary",
        "prompt", "negative_prompt", "output_text", "caption", "model", "model_id", "provider_id", "backend_profile_id",
        "route_id", "family", "loader", "category", "job_id", "result_id", "metadata_id", "settings", "parameters", "params",
        "result", "outputs", "source", "assets", "identity", "created_at", "updated_at", "memory_type", "object_type",
    }
    clean: dict[str, Any] = {}
    for key, value in event.items():
        key_l = str(key).lower()
        if key_l in blocked or any(term in key_l for term in ("password", "secret", "api_key", "authorization")):
            continue
        if key not in keep:
            continue
        if isinstance(value, str) and len(value) > 12000:
            clean[key] = value[:12000]
        elif isinstance(value, (dict, list)):
            encoded = _safe_text(value, 16000)
            try:
                clean[key] = json.loads(encoded)
            except Exception:
                clean[key] = encoded
        else:
            clean[key] = value
    return clean


def _event_text(event: dict[str, Any]) -> tuple[str, str]:
    summary = _safe_text(event.get("summary") or event.get("output_text") or event.get("caption") or "", 4000)
    parts: list[str] = []
    prompt = _safe_text(event.get("prompt"), 5000)
    negative = _safe_text(event.get("negative_prompt"), 2400)
    if prompt:
        parts.append(f"Prompt: {prompt}")
    if negative:
        parts.append(f"Negative: {negative}")
    if summary:
        parts.append(f"Result: {summary}")
    for key, label in (("model", "Model"), ("model_id", "Model"), ("provider_id", "Provider"), ("backend_profile_id", "Backend"), ("route_id", "Route"), ("family", "Family"), ("category", "Category")):
        value = _safe_text(event.get(key), 500)
        if value and f"{label}: {value}" not in parts:
            parts.append(f"{label}: {value}")
    settings = event.get("settings") or event.get("parameters") or event.get("params")
    if isinstance(settings, dict) and settings:
        parts.append("Settings: " + _safe_text(settings, 6000))
    if not parts:
        parts.append(_safe_text(event.get("title") or event.get("event_type") or "Surface task completed", 1000))
    content = "\n".join(part for part in parts if part).strip()
    return summary or content[:1200], content


def _fact_specs(surface_id: str, event: dict[str, Any], source_label: str) -> list[tuple[str, str, str]]:
    facts: list[tuple[str, str, str]] = []
    model = _safe_text(event.get("model") or event.get("model_id"), 500)
    if model:
        facts.append(("used_model", model, f"{source_label} used model {model}."))
    settings = _safe_mapping(event.get("settings") or event.get("parameters") or event.get("params"))
    seed = settings.get("seed") if settings else None
    if seed not in (None, "", -1, "-1"):
        facts.append(("used_seed", str(seed), f"{source_label} used seed {seed}."))
    if surface_id == "image":
        cfg = settings.get("cfg") or settings.get("cfg_scale") if settings else None
        if cfg not in (None, ""):
            facts.append(("used_cfg", str(cfg), f"{source_label} used CFG {cfg}."))
    if surface_id == "video":
        route = _safe_text(event.get("route_id"), 500)
        if route:
            facts.append(("used_video_route", route, f"{source_label} used video route {route}."))
    if surface_id == "voice":
        profile = _safe_mapping(event.get("result")).get("voice_profile") or _safe_mapping(event.get("source")).get("profile_id")
        if profile:
            facts.append(("used_voice_profile", str(profile), f"{source_label} used voice profile {profile}."))
    if surface_id == "prompt_captioning":
        tool = _safe_text(_safe_mapping(event.get("result")).get("tool_id") or _safe_mapping(event.get("metadata")).get("tool_id"), 500)
        if tool:
            facts.append(("used_prompt_captioning_tool", tool, f"{source_label} used Prompt/Captioning tool {tool}."))
    return facts


def ingest_surface_memory_event(
    surface_id: str,
    event: dict[str, Any] | None,
    *,
    db_path: Path | str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Ingest one successful/useful surface event into Unified Memory.

    This is intentionally synchronous and lightweight. Phase 10 moves expensive
    replay/embedding work to background jobs; Phase 8 only writes deterministic
    SQLite event/object/fact/fragment rows.
    """
    data = dict(event or {})
    surface = canonical_surface_id(surface_id or data.get("surface_id") or data.get("surface"))
    adapter = get_surface_ingestion_adapter(surface)
    if not adapter:
        return _remember_last_result(surface or "unknown", {"ok": False, "schema_id": SURFACE_MEMORY_EVENT_SCHEMA_ID, "status": "unsupported_surface", "surface_id": surface})
    allowed, reason = _is_searchable_event(adapter, data)
    if not allowed:
        return _remember_last_result(surface, {"ok": True, "schema_id": SURFACE_MEMORY_EVENT_SCHEMA_ID, "status": "skipped", "surface_id": surface, "reason": reason, "searchable": False})

    from neo_app.memory.surface_ingestion import DEFAULT_MEMORY_DB, UnifiedMemoryWriter  # lazy to avoid registry/compat circularity

    target = Path(db_path) if db_path is not None else DEFAULT_MEMORY_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    identity_payload = data.get("identity") if isinstance(data.get("identity"), dict) else data
    identity = resolve_canonical_identity(
        identity_payload,
        surface_id=surface,
        scope_id=data.get("scope_id") or builtin_scope_for_surface(surface, default="general"),
        project_id=data.get("project_id"),
        legacy_project_is_scope=False,
        source="surface_ingestion_registry_live",
    )
    memory_filter = identity.memory_filter()
    if identity.project_id:
        storage_surface = memory_filter.get("surface") or surface
        storage_project = memory_filter.get("project_id") or identity.project_id
    elif surface == "assistant":
        storage_surface = memory_filter.get("surface") or "assistant"
        storage_project = memory_filter.get("project_id") or "assistant:general"
    else:
        # Surface history is stored under its surface namespace even when the
        # Phase 1 compatibility alias table predates a newly registered surface
        # such as Board/Music. Canonical scope identity remains in provenance.
        storage_surface = surface
        storage_project = surface
    source_id = _safe_text(data.get("source_id") or data.get("result_id") or data.get("job_id") or data.get("metadata_id"), 1000)
    if not source_id:
        source_id = f"{surface}:{_hash(_redacted_payload(data), 24)}"
    event_type = _safe_text(data.get("event_type"), 500)
    title = _safe_text(data.get("title") or f"{adapter.label} task · {source_id}", 500)
    summary, content = _event_text(data)
    payload = _redacted_payload(data)
    source_type = f"surface_registry:{surface}"
    memory_type = _safe_text(data.get("memory_type") or adapter.memory_type, 300)
    object_type = _safe_text(data.get("object_type") or adapter.object_type, 300)

    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    writer = UnifiedMemoryWriter(conn)
    event_id = object_id = fragment_id = ""
    fact_ids: list[str] = []
    try:
        canonical_meta = identity.as_dict()
        project_label = identity.project_id or adapter.label
        writer.upsert_project(
            project_id=storage_project,
            label=project_label,
            surface=storage_surface,
            project_type="delivery_project" if identity.project_id else "surface",
            description=f"{adapter.label} unified surface memory.",
            metadata={"canonical_identity": canonical_meta, "phase8_registry": True, "compatibility_project_id": storage_project},
        )
        scope_key = identity.scope_id or builtin_scope_for_surface(surface, default=surface) or surface
        scope_id = writer.upsert_scope(
            surface=storage_surface,
            project_id=storage_project,
            scope_type="canonical_scope",
            scope_key=scope_key,
            label=scope_key,
            metadata={"canonical_identity": canonical_meta, "phase8_registry": True},
        )
        metadata = {
            "schema_id": SURFACE_MEMORY_EVENT_SCHEMA_ID,
            "phase": "8",
            "registry_adapter": adapter.surface_id,
            "canonical_identity": canonical_meta,
            "ingestion_reason": reason,
            "content_hash": _hash({"summary": summary, "content": content}, 32),
        }
        event_id = writer.upsert_event(
            surface=storage_surface,
            project_id=storage_project,
            scope_id=scope_id,
            source_type=source_type,
            source_id=source_id,
            event_type=event_type,
            title=title,
            summary=summary,
            payload=payload,
            metadata=metadata,
            importance="normal",
            confidence=0.95,
            trust_level="confirmed",
            created_at=_safe_text(data.get("created_at"), 100) or None,
        )
        object_id = writer.upsert_object(
            surface=storage_surface,
            project_id=storage_project,
            scope_id=scope_id,
            object_type=object_type,
            object_key=source_id,
            label=title,
            summary=summary,
            attributes={"event_type": event_type, "status": data.get("status") or "completed", "source_id": source_id},
            metadata={**metadata, "source_event_id": event_id},
            confidence=0.95,
        )
        fragment_id = writer.upsert_fragment(
            surface=storage_surface,
            project_id=storage_project,
            scope_id=scope_id,
            source_type=source_type,
            source_id=source_id,
            memory_type=memory_type,
            title=title,
            content=content,
            summary=summary,
            priority=0.72,
            confidence=0.92,
            trust_level="confirmed",
            metadata={**metadata, "source_event_id": event_id, "object_id": object_id},
        ) or ""
        for predicate, object_value, statement in _fact_specs(surface, data, title):
            fact_id = writer.upsert_fact(
                surface=storage_surface,
                project_id=storage_project,
                scope_id=scope_id,
                subject_id=object_id,
                predicate=predicate,
                object_value=object_value,
                statement=statement,
                fact_type=f"{surface}_task_fact",
                source_event_id=event_id,
                confidence=0.9,
                trust_level="confirmed",
                metadata=metadata,
            )
            if fact_id:
                fact_ids.append(fact_id)
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    durable_writeback: dict[str, Any] = {"ok": True, "status": "skipped", "reason": "commit_disabled"}
    if commit:
        try:
            from neo_app.memory.writeback_engine import MemoryWritebackEngine

            durable_writeback = MemoryWritebackEngine(target).capture_surface_event({
                **data,
                "surface_id": storage_surface or surface,
                "scope_id": identity.scope_id,
                "project_id": storage_project or identity.project_id or None,
                "canonical_project_id": identity.project_id or None,
                "source_id": source_id,
                "history_fragment_id": fragment_id,
                "identity": identity.as_dict(),
            })
        except Exception as exc:
            # Memory evolution must never turn a successful creative task into a
            # failed task. Phase 9 diagnostics carry the writeback error instead.
            durable_writeback = {"ok": False, "status": "writeback_error", "error": str(exc)[:500]}
    return _remember_last_result(surface, {
        "ok": True,
        "schema_id": SURFACE_MEMORY_EVENT_SCHEMA_ID,
        "status": "ingested",
        "searchable": True,
        "surface_id": surface,
        "identity": identity.as_dict(),
        "storage": {"surface": storage_surface, "project_id": storage_project},
        "event_id": event_id,
        "object_id": object_id,
        "fragment_id": fragment_id,
        "fact_ids": fact_ids,
        "durable_writeback": durable_writeback,
        "reason": reason,
    })
