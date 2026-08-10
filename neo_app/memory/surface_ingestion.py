from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .unified_schema import ensure_unified_memory_schema, unified_memory_schema_status
from neo_app.context_identity import builtin_scope_for_surface, resolve_canonical_identity

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_DB = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"
DEFAULT_ROLEPLAY_DB = ROOT_DIR / "neo_data" / "roleplay" / "roleplay.sqlite"
M3_SCHEMA_ID = "neo.memory.surface_ingestion.phase_m3.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _hash(value: Any, *, length: int = 16) -> str:
    text = value if isinstance(value, str) else _json_dumps(value)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _safe_text(value: Any, *, limit: int = 12000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        text = _json_dumps(value)
    else:
        text = str(value)
    text = text.replace("\x00", "").strip()
    return text[:limit]


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _token_estimate(text: str) -> int:
    return max(1, int(len(text or "") / 4)) if text else 0


def _records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        records = value.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        projects = value.get("projects")
        if isinstance(projects, list):
            return [item for item in projects if isinstance(item, dict)]
    return []


class UnifiedMemoryWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        ensure_unified_memory_schema(conn)
        self.conn.row_factory = sqlite3.Row

    def upsert_project(self, *, project_id: str, label: str, surface: str, project_type: str = "surface", description: str = "", metadata: dict[str, Any] | None = None) -> str:
        stamp = _now()
        clean = project_id or f"{surface}:default"
        self.conn.execute(
            """
            INSERT INTO neo_memory_projects (project_id, label, surface, project_type, status, description, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                label=excluded.label,
                surface=excluded.surface,
                project_type=excluded.project_type,
                description=excluded.description,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (clean, label or clean, surface or "global", project_type or "surface", description or "", _json_dumps(metadata or {}), stamp, stamp),
        )
        return clean

    def upsert_scope(self, *, surface: str, project_id: str | None, scope_type: str, scope_key: str, label: str = "", parent_scope_id: str | None = None, path: list[Any] | None = None, metadata: dict[str, Any] | None = None) -> str:
        stamp = _now()
        scope_key = str(scope_key or "default")
        scope_type = str(scope_type or "default")
        scope_id = f"{surface}:{project_id or 'global'}:{scope_type}:{_hash(scope_key, length=12)}"
        self.conn.execute(
            """
            INSERT INTO neo_memory_scopes (scope_id, surface, project_id, scope_type, scope_key, parent_scope_id, label, path_json, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(surface, project_id, scope_type, scope_key) DO UPDATE SET
                label=excluded.label,
                parent_scope_id=excluded.parent_scope_id,
                path_json=excluded.path_json,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (scope_id, surface or "global", project_id, scope_type, scope_key, parent_scope_id, label or scope_key, _json_dumps(path or []), _json_dumps(metadata or {}), stamp, stamp),
        )
        row = self.conn.execute(
            "SELECT scope_id FROM neo_memory_scopes WHERE surface=? AND project_id IS ? AND scope_type=? AND scope_key=?",
            (surface or "global", project_id, scope_type, scope_key),
        ).fetchone()
        return str(row[0]) if row else scope_id

    def upsert_event(self, *, surface: str, project_id: str | None, scope_id: str | None, source_type: str, source_id: str, event_type: str, title: str, summary: str = "", payload: Any = None, metadata: dict[str, Any] | None = None, importance: str = "normal", confidence: float = 1.0, trust_level: str = "confirmed", created_at: str | None = None) -> str:
        payload_json = _json_dumps(payload or {})
        content_hash = _hash({"surface": surface, "source_type": source_type, "source_id": source_id, "event_type": event_type, "payload": payload}, length=32)
        event_id = f"ev_{_hash(surface + source_type + source_id + event_type, length=24)}"
        stamp = created_at or _now()
        self.conn.execute(
            """
            INSERT INTO neo_memory_events (memory_event_id, source_event_id, surface, project_id, scope_id, source_type, source_id, event_type, title, summary, payload_json, metadata_json, importance, confidence, trust_level, retention_state, created_at, updated_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(memory_event_id) DO UPDATE SET
                project_id=excluded.project_id,
                scope_id=excluded.scope_id,
                title=excluded.title,
                summary=excluded.summary,
                payload_json=excluded.payload_json,
                metadata_json=excluded.metadata_json,
                importance=excluded.importance,
                confidence=excluded.confidence,
                trust_level=excluded.trust_level,
                updated_at=excluded.updated_at,
                content_hash=excluded.content_hash
            """,
            (event_id, source_id, surface, project_id, scope_id, source_type, source_id, event_type, _safe_text(title, limit=500), _safe_text(summary, limit=4000), payload_json, _json_dumps(metadata or {}), importance, float(confidence), trust_level, stamp, _now(), content_hash),
        )
        return event_id

    def upsert_object(self, *, surface: str, project_id: str | None, scope_id: str | None, object_type: str, object_key: str, label: str = "", summary: str = "", attributes: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None, confidence: float = 1.0) -> str:
        stamp = _now()
        object_id = f"obj_{_hash(surface + (project_id or '') + object_type + object_key, length=24)}"
        self.conn.execute(
            """
            INSERT INTO neo_memory_objects (object_id, surface, project_id, scope_id, object_type, object_key, label, summary, attributes_json, metadata_json, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(surface, project_id, object_type, object_key) DO UPDATE SET
                scope_id=excluded.scope_id,
                label=excluded.label,
                summary=excluded.summary,
                attributes_json=excluded.attributes_json,
                metadata_json=excluded.metadata_json,
                confidence=excluded.confidence,
                updated_at=excluded.updated_at
            """,
            (object_id, surface, project_id, scope_id, object_type, object_key, label or object_key, _safe_text(summary, limit=4000), _json_dumps(attributes or {}), _json_dumps(metadata or {}), float(confidence), stamp, stamp),
        )
        row = self.conn.execute(
            "SELECT object_id FROM neo_memory_objects WHERE surface=? AND project_id IS ? AND object_type=? AND object_key=?",
            (surface, project_id, object_type, object_key),
        ).fetchone()
        return str(row[0]) if row else object_id

    def upsert_fact(self, *, surface: str, project_id: str | None, scope_id: str | None, statement: str, predicate: str = "observed", subject_id: str | None = None, object_value: str = "", object_id: str | None = None, fact_type: str = "observation", source_event_id: str | None = None, confidence: float = 0.75, trust_level: str = "inferred", metadata: dict[str, Any] | None = None) -> str | None:
        statement = _safe_text(statement, limit=3000)
        if not statement:
            return None
        stamp = _now()
        content_hash = _hash({"surface": surface, "project_id": project_id, "scope_id": scope_id, "statement": statement, "predicate": predicate}, length=32)
        fact_id = f"fact_{content_hash[:24]}"
        self.conn.execute(
            """
            INSERT INTO neo_memory_facts (fact_id, surface, project_id, scope_id, subject_id, predicate, object_value, object_id, fact_type, statement, source_event_id, confidence, trust_level, status, valid_from, valid_to, metadata_json, created_at, updated_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?)
            ON CONFLICT(fact_id) DO UPDATE SET
                scope_id=excluded.scope_id,
                statement=excluded.statement,
                source_event_id=excluded.source_event_id,
                confidence=excluded.confidence,
                trust_level=excluded.trust_level,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (fact_id, surface, project_id, scope_id, subject_id, predicate, _safe_text(object_value, limit=1000), object_id, fact_type, statement, source_event_id, float(confidence), trust_level, stamp, _json_dumps(metadata or {}), stamp, stamp, content_hash),
        )
        return fact_id

    def upsert_fragment(self, *, surface: str, project_id: str | None, scope_id: str | None, source_type: str, source_id: str, memory_type: str, title: str, content: str, summary: str = "", priority: float = 0.5, confidence: float = 0.75, trust_level: str = "inferred", metadata: dict[str, Any] | None = None, embedding_status: str = "queued") -> str | None:
        content = _safe_text(content, limit=24000)
        if not content:
            return None
        stamp = _now()
        content_hash = _hash({"surface": surface, "source_type": source_type, "source_id": source_id, "memory_type": memory_type, "content": content}, length=32)
        fragment_id = f"frag_{_hash(surface + source_type + source_id + memory_type + content_hash, length=24)}"
        self.conn.execute(
            """
            INSERT INTO neo_memory_fragments (fragment_id, surface, project_id, scope_id, source_type, source_id, memory_type, title, content, summary, token_estimate, priority, confidence, trust_level, status, metadata_json, created_at, updated_at, content_hash, embedding_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                project_id=excluded.project_id,
                scope_id=excluded.scope_id,
                title=excluded.title,
                content=excluded.content,
                summary=excluded.summary,
                token_estimate=excluded.token_estimate,
                priority=excluded.priority,
                confidence=excluded.confidence,
                trust_level=excluded.trust_level,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at,
                content_hash=excluded.content_hash,
                embedding_status=excluded.embedding_status
            """,
            (fragment_id, surface, project_id, scope_id, source_type, source_id, memory_type, _safe_text(title, limit=500), content, _safe_text(summary, limit=4000), _token_estimate(content), float(priority), float(confidence), trust_level, _json_dumps(metadata or {}), stamp, stamp, content_hash, embedding_status),
        )
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO neo_memory_fragments_fts (fragment_id, surface, project_id, scope_id, title, content, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fragment_id, surface, project_id or "", scope_id or "", _safe_text(title, limit=500), content, _safe_text(summary, limit=4000)),
            )
        except sqlite3.OperationalError:
            pass
        return fragment_id


class SurfaceMemoryIngestor:
    def __init__(self, root_dir: Path = ROOT_DIR, db_path: Path = DEFAULT_MEMORY_DB) -> None:
        self.root_dir = root_dir
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.writer = UnifiedMemoryWriter(self.conn)
        self.report: dict[str, Any] = {
            "schema_id": M3_SCHEMA_ID,
            "created_at": _now(),
            "status": "running",
            "surfaces": {},
            "errors": [],
        }

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def _surface_report(self, surface: str) -> dict[str, Any]:
        return self.report["surfaces"].setdefault(surface, {"events": 0, "objects": 0, "facts": 0, "fragments": 0, "sources": []})

    def _add_error(self, surface: str, message: str) -> None:
        self.report["errors"].append({"surface": surface, "error": str(message)[:800], "created_at": _now()})

    def _surface_identity(self, surface: str):
        scope_id = builtin_scope_for_surface(surface, default="")
        return resolve_canonical_identity(
            {"surface_id": surface, "scope_id": scope_id or "general"},
            legacy_project_is_scope=True,
            source="surface_ingestion",
        )

    def ingest_projects(self) -> None:
        surface = "assistant"
        out = self._surface_report(surface)
        project_id = self.writer.upsert_project(project_id="assistant", label="Assistant", surface="assistant", project_type="assistant", description="Assistant built-in workspace memory sandbox.")
        root_scope = self.writer.upsert_scope(surface="assistant", project_id=project_id, scope_type="root", scope_key="assistant", label="Assistant Root")
        for rel_path in ["neo_data/assistant/assistant_projects_index.json", "neo_data/projects/project_workspace_index.json"]:
            path = self.root_dir / rel_path
            data = _read_json(path, {})
            for item in _records_from_json(data):
                pid = str(item.get("project_id") or item.get("id") or _hash(item))
                label = str(item.get("name") or item.get("label") or pid)
                desc = str(item.get("description") or item.get("notes") or "")
                p_surface = "assistant" if "assistant" in rel_path else "project"
                ptype = str(item.get("type") or "workspace")
                self.writer.upsert_project(project_id=f"{p_surface}:{pid}", label=label, surface=p_surface, project_type=ptype, description=desc, metadata={"source_path": rel_path, "raw": item})
                scope_id = self.writer.upsert_scope(surface=p_surface, project_id=f"{p_surface}:{pid}", scope_type="project", scope_key=pid, label=label, parent_scope_id=root_scope, metadata={"source_path": rel_path})
                event_id = self.writer.upsert_event(surface=p_surface, project_id=f"{p_surface}:{pid}", scope_id=scope_id, source_type="project_index", source_id=f"{rel_path}:{pid}", event_type="project.indexed", title=f"Project indexed: {label}", summary=desc, payload=item, metadata={"source_path": rel_path}, importance="normal", trust_level="confirmed", created_at=item.get("created_at"))
                obj_id = self.writer.upsert_object(surface=p_surface, project_id=f"{p_surface}:{pid}", scope_id=scope_id, object_type="project", object_key=pid, label=label, summary=desc, attributes=item, metadata={"source_event_id": event_id, "source_path": rel_path})
                self.writer.upsert_fact(surface=p_surface, project_id=f"{p_surface}:{pid}", scope_id=scope_id, subject_id=obj_id, predicate="has_project_index", object_value=pid, statement=f"Project {label} is available in Neo memory routing.", fact_type="project_state", source_event_id=event_id, trust_level="confirmed")
                out["events"] += 1; out["objects"] += 1; out["facts"] += 1
            out["sources"].append(rel_path)

    def ingest_image(self, *, limit: int | None = None) -> None:
        surface = "image"
        out = self._surface_report(surface)
        identity = self._surface_identity(surface)
        project_id = self.writer.upsert_project(project_id="image", label="Image", surface="image", project_type="surface", description="Image generation metadata memory sandbox.", metadata={"canonical_identity": identity.as_dict(), "compatibility_project_id": "image"})
        root_scope = self.writer.upsert_scope(surface="image", project_id=project_id, scope_type="root", scope_key="image", label="Image Root", metadata={"canonical_identity": identity.as_dict()})
        paths = sorted((self.root_dir / "neo_data" / "runtime" / "image_jobs").glob("*.json"))
        last_payload = self.root_dir / "neo_data" / "logs" / "image" / "neo_last_payload.json"
        if last_payload.exists():
            paths.append(last_payload)
        if limit:
            paths = paths[: max(0, limit)]
        for path in paths:
            try:
                data = _read_json(path, {}) or {}
                ctx = data.get("context") if isinstance(data.get("context"), dict) else data
                if "backend_payload" in data and isinstance(data.get("backend_payload"), dict):
                    ctx = data.get("backend_payload") or {}
                    ctx = {**ctx, "metadata": data.get("metadata") or {}}
                job_id = str(ctx.get("job_id") or ctx.get("id") or path.stem)
                mode = str(ctx.get("mode") or ctx.get("subtab") or ctx.get("actual_params", {}).get("mode") or "image")
                prompt = _safe_text(ctx.get("prompt") or ctx.get("positive_prompt") or ctx.get("actual_params", {}).get("prompt") or ctx.get("actual_params", {}).get("positive_prompt") or "", limit=6000)
                negative = _safe_text(ctx.get("negative_prompt") or ctx.get("actual_params", {}).get("negative_prompt") or "", limit=3000)
                scope_id = self.writer.upsert_scope(surface="image", project_id=project_id, scope_type="mode", scope_key=mode, label=f"Image {mode}", parent_scope_id=root_scope, metadata={"mode": mode})
                title = f"Image job {job_id} · {mode}"
                summary_parts = []
                if prompt:
                    summary_parts.append(f"Prompt: {prompt[:360]}")
                if negative:
                    summary_parts.append(f"Negative: {negative[:220]}")
                summary = "\n".join(summary_parts)
                event_id = self.writer.upsert_event(surface="image", project_id=project_id, scope_id=scope_id, source_type="image_job_context", source_id=_rel(path), event_type="image.generation.metadata", title=title, summary=summary, payload=data, metadata={"source_path": _rel(path), "mode": mode, "job_id": job_id}, importance="normal", trust_level="confirmed", created_at=ctx.get("created_at"))
                obj_id = self.writer.upsert_object(surface="image", project_id=project_id, scope_id=scope_id, object_type="image_job", object_key=job_id, label=title, summary=summary, attributes={"mode": mode, "provider_id": ctx.get("provider_id"), "backend_profile_id": ctx.get("backend_profile_id"), "profile_id": ctx.get("profile_id")}, metadata={"source_event_id": event_id, "source_path": _rel(path)})
                if prompt:
                    self.writer.upsert_fact(surface="image", project_id=project_id, scope_id=scope_id, subject_id=obj_id, predicate="uses_prompt", object_value=prompt[:500], statement=f"Image job {job_id} used prompt pattern: {prompt[:700]}", fact_type="creative_pattern", source_event_id=event_id, confidence=0.8, trust_level="confirmed")
                    out["facts"] += 1
                fragment = "\n".join([part for part in [summary, f"Provider: {ctx.get('provider_id') or ''}", f"Backend: {ctx.get('backend_profile_id') or ctx.get('profile_id') or ''}"] if part.strip()])
                if fragment:
                    self.writer.upsert_fragment(surface="image", project_id=project_id, scope_id=scope_id, source_type="image_job_context", source_id=_rel(path), memory_type="image_generation_metadata", title=title, content=fragment, summary=summary, priority=0.55, confidence=0.85, trust_level="confirmed", metadata={"source_event_id": event_id, "mode": mode, "job_id": job_id})
                    out["fragments"] += 1
                out["events"] += 1; out["objects"] += 1
            except Exception as exc:
                self._add_error(surface, f"{path}: {exc}")
        out["sources"].append("neo_data/runtime/image_jobs/*.json")
        out["sources"].append("neo_data/logs/image/neo_last_payload.json")

    def ingest_prompt_captioning(self, *, limit: int | None = None) -> None:
        surface = "prompt_captioning"
        out = self._surface_report(surface)
        identity = self._surface_identity(surface)
        project_id = self.writer.upsert_project(project_id="prompt_captioning", label="Prompt + Captioning", surface=surface, project_type="surface", description="Prompt Studio and Caption Studio generated output memory sandbox.", metadata={"canonical_identity": identity.as_dict(), "compatibility_project_id": "prompt_captioning"})
        root_scope = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="root", scope_key=surface, label="Prompt + Captioning Root", metadata={"canonical_identity": identity.as_dict()})
        sources = [
            ("result_metadata", self.root_dir / "neo_data" / "prompt_captioning" / "result_metadata.json"),
            ("caption_history", self.root_dir / "neo_data" / "prompt_captioning" / "caption_history.json"),
            ("saved_captions", self.root_dir / "neo_data" / "prompt_captioning" / "saved_captions.json"),
            ("caption_batch_results", self.root_dir / "neo_data" / "prompt_captioning" / "caption_batch_results.json"),
        ]
        for source_type, path in sources:
            records = _records_from_json(_read_json(path, {}))
            if limit:
                records = records[: max(0, limit)]
            for idx, rec in enumerate(records):
                try:
                    rec_id = str(rec.get("metadata_id") or rec.get("history_id") or rec.get("caption_id") or rec.get("batch_id") or f"{path.stem}:{idx}")
                    mode = str(rec.get("mode") or rec.get("caption_mode") or rec.get("tool_id") or source_type)
                    category = str(rec.get("category") or rec.get("workflow_mode") or mode or "default")
                    scope_id = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="category", scope_key=category, label=f"{surface} · {category}", parent_scope_id=root_scope, metadata={"source_type": source_type, "category": category})
                    caption = _safe_text(rec.get("caption") or rec.get("output") or rec.get("caption_output") or "", limit=10000)
                    prompt = _safe_text(rec.get("prompt") or rec.get("source_text") or rec.get("final_prompt") or "", limit=10000)
                    title = str(rec.get("name") or rec.get("title") or f"{source_type} {rec_id}")
                    summary = caption[:700] or prompt[:700] or _safe_text(rec.get("summary") or "", limit=700)
                    event_id = self.writer.upsert_event(surface=surface, project_id=project_id, scope_id=scope_id, source_type=source_type, source_id=f"{_rel(path)}:{rec_id}", event_type=f"prompt_captioning.{source_type}", title=title, summary=summary, payload=rec, metadata={"source_path": _rel(path), "category": category, "mode": mode}, importance="normal", trust_level="confirmed", created_at=rec.get("created_at"))
                    obj_type = "caption" if caption else "prompt_output"
                    obj_id = self.writer.upsert_object(surface=surface, project_id=project_id, scope_id=scope_id, object_type=obj_type, object_key=rec_id, label=title, summary=summary, attributes={"mode": mode, "category": category, "source_image": rec.get("source_image")}, metadata={"source_event_id": event_id, "source_path": _rel(path)})
                    if caption:
                        self.writer.upsert_fact(surface=surface, project_id=project_id, scope_id=scope_id, subject_id=obj_id, predicate="has_caption", object_value=caption[:500], statement=f"Caption output saved in {category}: {caption[:900]}", fact_type="creative_output", source_event_id=event_id, confidence=0.85, trust_level="confirmed")
                        out["facts"] += 1
                    if prompt:
                        self.writer.upsert_fact(surface=surface, project_id=project_id, scope_id=scope_id, subject_id=obj_id, predicate="has_prompt", object_value=prompt[:500], statement=f"Prompt output saved in {category}: {prompt[:900]}", fact_type="creative_output", source_event_id=event_id, confidence=0.85, trust_level="confirmed")
                        out["facts"] += 1
                    content = "\n".join([part for part in [f"Category: {category}", f"Mode: {mode}", f"Caption: {caption}" if caption else "", f"Prompt: {prompt}" if prompt else "", f"Source image: {rec.get('source_image') or ''}"] if part.strip()])
                    if content:
                        self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type=source_type, source_id=f"{_rel(path)}:{rec_id}", memory_type="caption_output" if caption else "prompt_output", title=title, content=content, summary=summary, priority=0.55, confidence=0.85, trust_level="confirmed", metadata={"source_event_id": event_id, "category": category, "mode": mode})
                        out["fragments"] += 1
                    # Batch results carry nested captions; ingest compact nested fragments too.
                    if source_type == "caption_batch_results" and isinstance(rec.get("results"), list):
                        for ridx, result in enumerate(rec.get("results")[:50]):
                            if not isinstance(result, dict):
                                continue
                            rcap = _safe_text(result.get("caption") or "", limit=8000)
                            if not rcap:
                                continue
                            self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type="caption_batch_result_item", source_id=f"{rec_id}:{ridx}", memory_type="caption_output", title=f"Batch caption {rec_id} item {ridx + 1}", content=f"File: {result.get('file') or result.get('image') or ''}\nCaption: {rcap}", summary=rcap[:700], priority=0.5, confidence=0.8, trust_level="confirmed", metadata={"source_event_id": event_id, "batch_id": rec_id, "category": category})
                            out["fragments"] += 1
                    out["events"] += 1; out["objects"] += 1
                except Exception as exc:
                    self._add_error(surface, f"{path}: {exc}")
            out["sources"].append(_rel(path))

    def ingest_roleplay(self, *, limit: int | None = None) -> None:
        surface = "roleplay"
        out = self._surface_report(surface)
        role_db = self.root_dir / "neo_data" / "roleplay" / "roleplay.sqlite"
        if not role_db.exists():
            self._add_error(surface, f"missing {role_db}")
            return
        identity = self._surface_identity(surface)
        project_id = self.writer.upsert_project(project_id="roleplay", label="Roleplay", surface=surface, project_type="surface", description="Roleplay canon, scene, character, timeline, and runtime memory sandbox.", metadata={"canonical_identity": identity.as_dict(), "compatibility_project_id": "roleplay"})
        root_scope = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="root", scope_key="roleplay", label="Roleplay Root", metadata={"canonical_identity": identity.as_dict()})
        rconn = sqlite3.connect(str(role_db)); rconn.row_factory = sqlite3.Row
        try:
            rows = rconn.execute("SELECT * FROM rp_memory_fragments ORDER BY updated_at DESC").fetchall()
            if limit:
                rows = rows[: max(0, limit)]
            scope_cache: dict[str, str] = {}
            for row in rows:
                item = dict(row)
                scope_key = item.get("universe_id") or item.get("world_id") or item.get("sandbox_id") or item.get("session_id") or item.get("memory_scope") or "roleplay"
                if scope_key not in scope_cache:
                    scope_cache[scope_key] = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="roleplay_scope", scope_key=scope_key, label=f"Roleplay · {scope_key}", parent_scope_id=root_scope, metadata={"source": "rp_memory_fragments"})
                scope_id = scope_cache[scope_key]
                source_id = str(item.get("fragment_id") or item.get("source_id") or _hash(item))
                title = str(item.get("title") or item.get("source_record_id") or item.get("source_id") or source_id)
                content = _safe_text(item.get("content") or "", limit=24000)
                if not content:
                    continue
                event_id = self.writer.upsert_event(surface=surface, project_id=project_id, scope_id=scope_id, source_type="rp_memory_fragment", source_id=source_id, event_type="roleplay.memory.fragment", title=title, summary=content[:700], payload=item, metadata={"roleplay_db": _rel(role_db), "memory_scope": item.get("memory_scope"), "promotion_scope": item.get("promotion_scope")}, importance=str(item.get("priority") or "normal"), confidence=float(item.get("confidence") or 0.75), trust_level="confirmed" if str(item.get("status") or "").lower() in {"active", "primary_canon", "compiled", "candidate_runtime"} else "inferred", created_at=item.get("created_at"))
                self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type="rp_memory_fragment", source_id=source_id, memory_type=str(item.get("memory_type") or "roleplay_fragment"), title=title, content=content, summary=content[:700], priority=float(item.get("salience") or 0.55), confidence=float(item.get("confidence") or 0.75), trust_level="confirmed", metadata={"source_event_id": event_id, "sandbox": item.get("sandbox_json"), "tags": _read_json_text(item.get("tags_json"), [])})
                out["events"] += 1; out["fragments"] += 1
            packet_rows = []
            try:
                packet_rows = rconn.execute("SELECT * FROM rp_scene_memory_packets ORDER BY updated_at DESC").fetchall()
            except Exception:
                packet_rows = []
            for row in packet_rows[: (limit or len(packet_rows))]:
                item = dict(row)
                packet_id = str(item.get("packet_id") or _hash(item))
                scope_key = item.get("sandbox_id") or item.get("scope_id") or item.get("world_id") or "roleplay_scene_packets"
                scope_id = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="scene_packet", scope_key=scope_key, label=f"Scene Packets · {scope_key}", parent_scope_id=root_scope, metadata={"source": "rp_scene_memory_packets"})
                title = str(item.get("title") or packet_id)
                payload = _read_json_text(item.get("payload_json"), item)
                content = _safe_text(payload, limit=24000)
                event_id = self.writer.upsert_event(surface=surface, project_id=project_id, scope_id=scope_id, source_type="rp_scene_memory_packet", source_id=packet_id, event_type="roleplay.scene_packet", title=title, summary=content[:700], payload=item, metadata={"roleplay_db": _rel(role_db), "packet_id": packet_id}, importance="high", trust_level="confirmed", created_at=item.get("created_at"))
                self.writer.upsert_object(surface=surface, project_id=project_id, scope_id=scope_id, object_type="scene_packet", object_key=packet_id, label=title, summary=content[:700], attributes=item, metadata={"source_event_id": event_id})
                self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type="rp_scene_memory_packet", source_id=packet_id, memory_type="scene_packet", title=title, content=content, summary=content[:700], priority=0.9, confidence=0.9, trust_level="confirmed", metadata={"source_event_id": event_id, "packet_id": packet_id})
                out["events"] += 1; out["objects"] += 1; out["fragments"] += 1
        except Exception as exc:
            self._add_error(surface, str(exc))
        finally:
            rconn.close()
        out["sources"].append(_rel(role_db))


    def ingest_voice(self, *, limit: int | None = None) -> None:
        surface = "voice"
        out = self._surface_report(surface)
        identity = self._surface_identity(surface)
        project_id = self.writer.upsert_project(project_id="voice", label="Voice", surface=surface, project_type="surface", description="Voice render, replay, profile, and output memory sandbox.", metadata={"canonical_identity": identity.as_dict(), "compatibility_project_id": "voice"})
        root_scope = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="root", scope_key="voice", label="Voice Root", metadata={"canonical_identity": identity.as_dict()})
        voice_memory_index = self.root_dir / "neo_data" / "outputs" / "voice" / "history" / "voice_memory_events.v15.json"
        records = _records_from_json(_read_json(voice_memory_index, []))
        if limit:
            records = records[: max(0, limit)]
        for item in records:
            try:
                event_id_raw = str(item.get("event_id") or item.get("job_id") or _hash(item))
                job_id = str(item.get("job_id") or event_id_raw)
                job_type = str(item.get("job_type") or "voice")
                replay = item.get("replay") if isinstance(item.get("replay"), dict) else item
                script_fragment = replay.get("script_fragment") if isinstance(replay.get("script_fragment"), dict) else item.get("script_fragment") if isinstance(item.get("script_fragment"), dict) else {}
                voice_profile = replay.get("voice_profile_fact") if isinstance(replay.get("voice_profile_fact"), dict) else item.get("voice_profile_fact") if isinstance(item.get("voice_profile_fact"), dict) else {}
                backend = replay.get("backend_settings") if isinstance(replay.get("backend_settings"), dict) else item.get("backend_settings") if isinstance(item.get("backend_settings"), dict) else {}
                output_obj = replay.get("output_file_object") if isinstance(replay.get("output_file_object"), dict) else item.get("output_file_object") if isinstance(item.get("output_file_object"), dict) else {}
                summary = _safe_text(item.get("memory_summary") or replay.get("memory_summary") or script_fragment.get("text") or "", limit=4000)
                scope_id = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="voice_job_type", scope_key=job_type, label=f"Voice · {job_type}", parent_scope_id=root_scope, metadata={"job_type": job_type})
                title = f"Voice job {job_id} · {job_type}"
                event_id = self.writer.upsert_event(surface=surface, project_id=project_id, scope_id=scope_id, source_type="voice_memory_event", source_id=event_id_raw, event_type=str(item.get("event_type") or "voice.render.completed"), title=title, summary=summary, payload=item, metadata={"source_path": _rel(voice_memory_index), "job_id": job_id, "job_type": job_type}, importance="normal", trust_level="confirmed", created_at=item.get("created_at"))
                object_id = self.writer.upsert_object(surface=surface, project_id=project_id, scope_id=scope_id, object_type="voice_job", object_key=job_id, label=title, summary=summary, attributes={"job_type": job_type, "voice_profile": voice_profile, "backend": backend, "output": output_obj}, metadata={"source_event_id": event_id})
                content = "\n".join(part for part in [summary, f"Voice profile: {_safe_text(voice_profile, limit=2000)}" if voice_profile else "", f"Backend: {_safe_text(backend, limit=2000)}" if backend else "", f"Output: {_safe_text(output_obj, limit=1500)}" if output_obj else ""] if part)
                fragment_id = self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type="voice_memory_event", source_id=event_id_raw, memory_type="voice_replay_metadata", title=title, content=content or title, summary=summary, priority=0.7, confidence=0.9, trust_level="confirmed", metadata={"source_event_id": event_id, "object_id": object_id, "job_id": job_id})
                model = str(backend.get("model_id") or backend.get("model") or voice_profile.get("model_id") or "")
                if model:
                    self.writer.upsert_fact(surface=surface, project_id=project_id, scope_id=scope_id, subject_id=object_id, predicate="used_model", object_value=model, statement=f"Voice job {job_id} used model {model}.", fact_type="voice_result", source_event_id=event_id, confidence=0.9, trust_level="confirmed")
                    out["facts"] += 1
                out["events"] += 1; out["objects"] += 1
                if fragment_id:
                    out["fragments"] += 1
            except Exception as exc:
                self._add_error(surface, f"voice event {item.get('event_id') or item.get('job_id')}: {exc}")
        out["sources"].append(_rel(voice_memory_index))

    def ingest_video(self, *, limit: int | None = None) -> None:
        surface = "video"
        out = self._surface_report(surface)
        try:
            from neo_app.video.replay_memory import build_video_replay_metadata, upgrade_video_record_to_v22
            from neo_app.video.output_records import VIDEO_OUTPUT_RECORD_SCHEMA_VERSION
        except Exception as exc:  # pragma: no cover - defensive optional surface import
            self._add_error(surface, f"Video replay metadata import failed: {exc}")
            return
        identity = self._surface_identity(surface)
        project_id = self.writer.upsert_project(project_id="video", label="Video", surface="video", project_type="surface", description="Video generation, finish, replay, and output memory sandbox.", metadata={"canonical_identity": identity.as_dict(), "compatibility_project_id": "video"})
        root_scope = self.writer.upsert_scope(surface="video", project_id=project_id, scope_type="root", scope_key="video", label="Video Root", metadata={"canonical_identity": identity.as_dict()})
        metadata_dir = self.root_dir / "neo_data" / "outputs" / "video" / "metadata"
        paths = sorted(metadata_dir.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True) if metadata_dir.exists() else []
        paths = [path for path in paths if not path.name.endswith((".replay.v22.json", ".memory_export.v22.json"))]
        if limit:
            paths = paths[: max(0, limit)]
        for path in paths:
            try:
                record = _read_json(path, {}) or {}
                if record.get("schema_version") != VIDEO_OUTPUT_RECORD_SCHEMA_VERSION:
                    continue
                if not record.get("result_id"):
                    continue
                record = upgrade_video_record_to_v22(record, persist=True)
                metadata = build_video_replay_metadata(record)
                result_id = str(record.get("result_id") or path.stem)
                category = str(record.get("category") or "txt2vid")
                scope_id = self.writer.upsert_scope(surface=surface, project_id=project_id, scope_type="video_category", scope_key=category, label=f"Video · {category}", parent_scope_id=root_scope, metadata={"route_id": record.get("route_id")})
                event_id = self.writer.upsert_event(surface=surface, project_id=project_id, scope_id=scope_id, source_type="video_output_record", source_id=result_id, event_type=metadata["memory_summary"]["event_type"], title=metadata["memory_summary"]["title"], summary=metadata["memory_summary"]["summary"], payload=metadata, metadata={"source_path": _rel(path), "category": category, "route_id": record.get("route_id")}, importance=metadata["memory_summary"].get("importance", "normal"), trust_level="confirmed", created_at=record.get("created_at"))
                obj_id = self.writer.upsert_object(surface=surface, project_id=project_id, scope_id=scope_id, object_type="video_result", object_key=result_id, label=metadata["memory_summary"]["title"], summary=metadata["memory_summary"]["summary"], attributes=metadata, metadata={"source_event_id": event_id, "source_path": _rel(path)})
                frag_id = self.writer.upsert_fragment(surface=surface, project_id=project_id, scope_id=scope_id, source_type="video_output_record", source_id=result_id, memory_type="video_replay_metadata", title=metadata["memory_summary"]["title"], content=_json_dumps(metadata), summary=metadata["memory_summary"]["summary"], priority=0.75, confidence=0.95, trust_level="confirmed", metadata={"source_event_id": event_id, "object_id": obj_id})
                for fact in metadata["memory_summary"].get("facts", []):
                    self.writer.upsert_fact(surface=surface, project_id=project_id, scope_id=scope_id, subject_id=obj_id, predicate="has_video_result_fact", object_value=result_id, statement=str(fact), fact_type="video_result", source_event_id=event_id, confidence=0.9, trust_level="confirmed", metadata={"category": category})
                    out["facts"] += 1
                out["events"] += 1; out["objects"] += 1
                if frag_id:
                    out["fragments"] += 1
            except Exception as exc:
                self._add_error(surface, f"{path.name}: {exc}")
        out["sources"].append(_rel(metadata_dir))

    def run(self, *, surfaces: Iterable[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        from .surface_ingestion_registry import execute_registered_batch_ingestion, surface_ingestion_registry_status

        try:
            registry_run = execute_registered_batch_ingestion(self, surfaces, limit=limit)
            self.report["registry"] = {**surface_ingestion_registry_status(), "run": registry_run}
            for unsupported in registry_run.get("unsupported", []):
                self._add_error(str(unsupported), "No registered batch ingestion adapter for this surface.")
            self.conn.commit()
            self.report["status"] = "completed" if not self.report.get("errors") else "completed_with_warnings"
        except Exception as exc:
            self.conn.rollback()
            self.report["status"] = "failed"
            self.report["errors"].append({"surface": "global", "error": str(exc), "created_at": _now()})
        self.report["finished_at"] = _now()
        self.report["unified_schema"] = unified_memory_schema_status(self.conn)
        self.report["totals"] = {
            "events": sum(int(v.get("events", 0)) for v in self.report["surfaces"].values()),
            "objects": sum(int(v.get("objects", 0)) for v in self.report["surfaces"].values()),
            "facts": sum(int(v.get("facts", 0)) for v in self.report["surfaces"].values()),
            "fragments": sum(int(v.get("fragments", 0)) for v in self.report["surfaces"].values()),
        }
        return self.report


def _read_json_text(text: Any, default: Any = None) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str) or not text.strip():
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def run_surface_memory_ingestion(*, root_dir: Path = ROOT_DIR, db_path: Path = DEFAULT_MEMORY_DB, surfaces: Iterable[str] | None = None, limit: int | None = None, write_report: bool = True) -> dict[str, Any]:
    ingestor = SurfaceMemoryIngestor(root_dir=root_dir, db_path=db_path)
    try:
        report = ingestor.run(surfaces=surfaces, limit=limit)
    finally:
        ingestor.close()
    if write_report:
        out_dir = root_dir / "neo_data" / "memory" / "audits"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "m3_surface_memory_ingestion.json"
        md_path = out_dir / "m3_surface_memory_ingestion.md"
        json_path.write_text(_json_dumps(report), encoding="utf-8")
        md_path.write_text(render_surface_ingestion_report(report), encoding="utf-8")
        report["report_paths"] = {"json": _rel(json_path), "markdown": _rel(md_path)}
    return report


def render_surface_ingestion_report(report: dict[str, Any]) -> str:
    lines = [
        "# Phase M3 — Surface Memory Ingestion Report",
        "",
        f"Status: `{report.get('status')}`",
        f"Created: `{report.get('created_at')}`",
        f"Finished: `{report.get('finished_at', '')}`",
        "",
        "## Totals",
        "",
        "| Type | Count |",
        "|---|---:|",
    ]
    totals = report.get("totals") or {}
    for key in ["events", "objects", "facts", "fragments"]:
        lines.append(f"| {key} | {int(totals.get(key, 0))} |")
    lines += ["", "## Surfaces", "", "| Surface | Events | Objects | Facts | Fragments | Sources |", "|---|---:|---:|---:|---:|---|"]
    for surface, data in sorted((report.get("surfaces") or {}).items()):
        sources = ", ".join(str(item) for item in (data.get("sources") or [])[:8])
        lines.append(f"| {surface} | {data.get('events', 0)} | {data.get('objects', 0)} | {data.get('facts', 0)} | {data.get('fragments', 0)} | {sources} |")
    errors = report.get("errors") or []
    lines += ["", "## Warnings / Errors", ""]
    if not errors:
        lines.append("No ingestion warnings were recorded.")
    else:
        for err in errors[:50]:
            lines.append(f"- `{err.get('surface')}` — {err.get('error')}")
    lines += ["", "## Notes", "", "- Phase M3 is additive and idempotent.", "- It converts existing surface metadata into unified SQLite memory events, objects, facts, and fragments.", "- Embeddings remain queued; Phase M3 does not require embedding/reranker models to be active.", "- Chroma remains an optional semantic mirror and is not used as the source of truth."]
    return "\n".join(lines).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Neo Phase M3 surface memory ingestion")
    parser.add_argument("--surface", action="append", dest="surfaces", help="Surface to ingest. May be repeated. Defaults to assistant,image,prompt_captioning,roleplay,video,voice through the Phase 8 registry.")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-source limit for smoke tests.")
    parser.add_argument("--db", type=Path, default=DEFAULT_MEMORY_DB, help="Unified memory SQLite path.")
    args = parser.parse_args(argv)
    report = run_surface_memory_ingestion(db_path=args.db, surfaces=args.surfaces, limit=args.limit, write_report=True)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("status") in {"completed", "completed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
