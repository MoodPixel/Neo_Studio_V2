from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from neo_app.context_identity import CanonicalContextIdentity, resolve_canonical_identity
from neo_app.memory.consolidation_engine import UnifiedMemoryConsolidationEngine
from neo_app.memory.surface_ingestion import DEFAULT_MEMORY_DB, UnifiedMemoryWriter
from neo_app.memory.unified_schema import ensure_unified_memory_schema

ROOT_DIR = Path(__file__).resolve().parents[2]
PROJECT_BRAIN_INGESTION_SCHEMA_ID = "neo.memory.project_brain_ingestion.phase7.v1"
PROJECT_BRAIN_INGESTION_PHASE = "7"
PROJECT_BRAIN_SOURCE_PREFIX = "assistant_project_brain"


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _hash(value: Any, length: int = 64) -> str:
    text = value if isinstance(value, str) else _json(value)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _text(value: Any, limit: int = 24000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = _json(value)
    return text.replace("\x00", "").strip()[:limit]


def _chunks(text: str, *, max_chars: int = 6000) -> list[str]:
    """Deterministically split extracted project knowledge without extra deps."""
    text = _text(text, 24000)
    if not text:
        return []
    paragraphs = [part.strip() for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text]
    out: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                out.append(current.strip())
                current = ""
            for start in range(0, len(paragraph), max_chars):
                piece = paragraph[start : start + max_chars].strip()
                if piece:
                    out.append(piece)
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > max_chars and current:
            out.append(current.strip())
            current = paragraph
        else:
            current = candidate
    if current:
        out.append(current.strip())
    return out[:16]


def _identity(
    *,
    project_id: str = "general",
    surface: str = "assistant",
    identity: dict[str, Any] | CanonicalContextIdentity | None = None,
) -> CanonicalContextIdentity:
    if isinstance(identity, CanonicalContextIdentity):
        return identity
    if isinstance(identity, dict) and identity:
        return resolve_canonical_identity(
            {"identity": identity, "project_id": project_id, "surface": surface},
            legacy_project_is_scope=True,
            source="project_brain_ingestion",
        )
    return resolve_canonical_identity(
        {"project_id": project_id, "surface": surface},
        legacy_project_is_scope=True,
        source="project_brain_ingestion",
    )


class ProjectBrainIngestionService:
    """Canonical write boundary for Assistant Scope / Project Brain knowledge.

    Legacy Assistant JSON remains a UX/compatibility projection in Phase 7. This
    service writes the retrieval-authoritative representation into the Unified
    Memory SQLite schema with canonical scope/surface/project provenance.
    """

    def __init__(self, *, root_dir: Path = ROOT_DIR, db_path: Path = DEFAULT_MEMORY_DB) -> None:
        self.root_dir = Path(root_dir)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        return conn

    @staticmethod
    def storage_identity(identity: CanonicalContextIdentity) -> dict[str, str]:
        compatibility = identity.memory_filter()
        surface = str(compatibility.get("surface") or identity.surface_id or "assistant")
        project_id = str(compatibility.get("project_id") or identity.project_id or "")
        # Canonical Assistant scope remains first-class provenance even while the
        # existing surface pseudo-project IDs remain compatibility storage keys.
        scope_id = str(identity.scope_id or compatibility.get("scope_id") or "general")
        return {"surface": surface, "project_id": project_id, "scope_id": scope_id}

    def _ensure_registry(self, conn: sqlite3.Connection, identity: CanonicalContextIdentity, storage: dict[str, str]) -> None:
        writer = UnifiedMemoryWriter(conn)
        storage_project = storage.get("project_id") or ""
        if not storage_project:
            return
        # Surface ingestion already owns compatibility projects such as `image`,
        # `video`, and `prompt_captioning`. Project Brain must never relabel or
        # re-type those registry rows just to attach Scope provenance.
        existing = conn.execute("SELECT project_id FROM neo_memory_projects WHERE project_id=?", (storage_project,)).fetchone()
        if existing is not None:
            return
        writer.upsert_project(
            project_id=storage_project,
            label=identity.project_id or identity.scope_id or storage_project,
            surface=storage.get("surface") or "assistant",
            project_type="delivery_project" if identity.project_id else "assistant_scope_compat",
            description="Canonical Project Brain memory routing target.",
            metadata={
                "phase": PROJECT_BRAIN_INGESTION_PHASE,
                "canonical_identity": identity.as_dict(),
                "canonical_scope_id": identity.scope_id,
                "canonical_project_id": identity.project_id or "",
                "compatibility_storage": storage,
            },
        )

    @staticmethod
    def _source_existing(conn: sqlite3.Connection, *, source_type: str, source_id: str, memory_type: str, content_hash: str) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM neo_memory_fragments
            WHERE source_type=? AND source_id=? AND memory_type=? AND content_hash=? AND status='active'
            LIMIT 1
            """,
            (source_type, source_id, memory_type, content_hash),
        ).fetchone()
        return row is not None

    @staticmethod
    def _retire_old_source_fragments(conn: sqlite3.Connection, *, source_type: str, source_id: str, memory_type: str, content_hash: str) -> int:
        rows = conn.execute(
            """
            SELECT fragment_id FROM neo_memory_fragments
            WHERE source_type=? AND source_id=? AND memory_type=? AND content_hash<>? AND status='active'
            """,
            (source_type, source_id, memory_type, content_hash),
        ).fetchall()
        if not rows:
            return 0
        ids = [str(row[0]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE neo_memory_fragments SET status='superseded', updated_at=datetime('now') WHERE fragment_id IN ({placeholders})",
            ids,
        )
        try:
            conn.executemany("DELETE FROM neo_memory_fragments_fts WHERE fragment_id=?", [(item,) for item in ids])
        except sqlite3.OperationalError:
            pass
        return len(ids)

    def ingest_text(
        self,
        *,
        project_id: str = "general",
        surface: str = "assistant",
        identity: dict[str, Any] | CanonicalContextIdentity | None = None,
        source_type: str,
        source_id: str,
        memory_type: str,
        event_type: str,
        title: str,
        content: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
        priority: float = 0.72,
        confidence: float = 0.95,
        trust_level: str = "confirmed",
        facts: Iterable[dict[str, Any] | str] | None = None,
    ) -> dict[str, Any]:
        content = _text(content)
        if not content:
            return {"ok": False, "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID, "status": "empty", "fragment_ids": []}
        ident = _identity(project_id=project_id, surface=surface, identity=identity)
        storage = self.storage_identity(ident)
        source_type = _text(source_type, 160) or PROJECT_BRAIN_SOURCE_PREFIX
        raw_source_id = _text(source_id, 600) or f"{source_type}:{_hash(content, 24)}"
        # UnifiedMemoryWriter IDs intentionally omit project/scope. Namespace the
        # source ID here so identical documents/snapshots in different client
        # projects or Assistant scopes can never overwrite each other's rows.
        source_namespace = str(ident.project_id or ident.scope_id or storage.get("project_id") or "general")
        source_id = f"{source_namespace}:{raw_source_id}"
        memory_type = _text(memory_type, 160) or "project_knowledge"
        content_hash = _hash({"surface": storage["surface"], "source_type": source_type, "source_id": source_id, "memory_type": memory_type, "content": content}, 32)
        meta = {
            **(metadata or {}),
            "raw_source_id": raw_source_id,
            "source_namespace": source_namespace,
            "phase": PROJECT_BRAIN_INGESTION_PHASE,
            "canonical_identity": ident.as_dict(),
            "canonical_scope_id": ident.scope_id,
            "canonical_project_id": ident.project_id or "",
            "source_content_hash": _hash(content, 64),
            "compatibility_storage": storage,
        }
        with self._connect() as conn:
            self._ensure_registry(conn, ident, storage)
            writer = UnifiedMemoryWriter(conn)
            existed = self._source_existing(
                conn,
                source_type=source_type,
                source_id=source_id,
                memory_type=memory_type,
                content_hash=content_hash,
            )
            superseded = self._retire_old_source_fragments(
                conn,
                source_type=source_type,
                source_id=source_id,
                memory_type=memory_type,
                content_hash=content_hash,
            )
            event_id = writer.upsert_event(
                surface=storage["surface"],
                project_id=storage["project_id"] or None,
                scope_id=storage["scope_id"] or None,
                source_type=source_type,
                source_id=source_id,
                event_type=event_type,
                title=title,
                summary=summary or content[:1200],
                payload={"content": content, "metadata": metadata or {}},
                metadata=meta,
                importance="high" if priority >= 0.82 else "normal",
                confidence=confidence,
                trust_level=trust_level,
            )
            fragment_id = writer.upsert_fragment(
                surface=storage["surface"],
                project_id=storage["project_id"] or None,
                scope_id=storage["scope_id"] or None,
                source_type=source_type,
                source_id=source_id,
                memory_type=memory_type,
                title=title,
                content=content,
                summary=summary or content[:1200],
                priority=priority,
                confidence=confidence,
                trust_level=trust_level,
                metadata={**meta, "source_event_id": event_id},
            )
            fact_ids: list[str] = []
            for fact in list(facts or [])[:24]:
                if isinstance(fact, dict):
                    statement = _text(fact.get("statement"), 3000)
                    predicate = str(fact.get("predicate") or "has_project_brain_fact")
                    fact_type = str(fact.get("fact_type") or memory_type)
                    object_value = _text(fact.get("object_value"), 1000)
                    fact_confidence = float(fact.get("confidence") or confidence)
                else:
                    statement = _text(fact, 3000)
                    predicate = "has_project_brain_fact"
                    fact_type = memory_type
                    object_value = ""
                    fact_confidence = confidence
                if not statement:
                    continue
                fid = writer.upsert_fact(
                    surface=storage["surface"],
                    project_id=storage["project_id"] or None,
                    scope_id=storage["scope_id"] or None,
                    statement=statement,
                    predicate=predicate,
                    object_value=object_value,
                    fact_type=fact_type,
                    source_event_id=event_id,
                    confidence=fact_confidence,
                    trust_level=trust_level,
                    metadata=meta,
                )
                if fid:
                    fact_ids.append(fid)
            conn.commit()
        return {
            "ok": True,
            "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID,
            "phase": PROJECT_BRAIN_INGESTION_PHASE,
            "status": "deduplicated" if existed else "ingested",
            "deduplicated": existed,
            "superseded_fragment_count": superseded,
            "identity": ident.as_dict(),
            "storage": storage,
            "event_id": event_id,
            "fragment_ids": [fragment_id] if fragment_id else [],
            "fact_ids": fact_ids,
            "source_type": source_type,
            "source_id": source_id,
            "memory_type": memory_type,
            "content_hash": content_hash,
        }

    def ingest_scope_knowledge(self, record: dict[str, Any], *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        text = _text(record.get("text"))
        title = _text(record.get("title"), 300) or "Assistant scope knowledge"
        context_id = str(record.get("context_id") or f"context:{_hash(text, 24)}")
        kind = str(record.get("kind") or "project_knowledge")
        return self.ingest_text(
            project_id=str(record.get("project_id") or "general"),
            surface=str(record.get("surface") or "assistant"),
            identity=identity,
            source_type="assistant_scope_knowledge",
            source_id=context_id,
            memory_type=kind,
            event_type="assistant.scope_knowledge.saved",
            title=title,
            content=text,
            summary=text[:1200],
            priority=0.78,
            metadata={
                "context_id": context_id,
                "session_id": record.get("session_id") or "",
                "tags": list(record.get("tags") or []),
                "legacy_source": record.get("source") or "assistant_context_import",
            },
            facts=[{
                "statement": f"Saved scope knowledge '{title}': {text[:1400]}",
                "predicate": "has_scope_knowledge",
                "fact_type": "scope_knowledge",
                "confidence": 0.92,
            }],
        )

    def ingest_manual_capture(self, record: dict[str, Any], *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        text = _text(record.get("text"))
        capture_id = str(record.get("capture_id") or f"capture:{_hash(text, 24)}")
        title = _text(record.get("title"), 300) or "Assistant memory capture"
        return self.ingest_text(
            project_id=str(record.get("project_id") or "general"),
            surface=str(record.get("surface_id") or record.get("surface") or "assistant"),
            identity=identity,
            source_type="assistant_manual_capture",
            source_id=capture_id,
            memory_type="manual_memory_capture",
            event_type="assistant.memory.manual_capture",
            title=title,
            content=text,
            summary=text[:1200],
            priority=0.86,
            metadata={"capture_id": capture_id, "session_id": record.get("session_id") or ""},
            facts=[{
                "statement": text[:2400],
                "predicate": "user_saved_memory",
                "fact_type": "manual_memory_capture",
                "confidence": 0.98,
            }],
        )

    def ingest_snapshot(self, record: dict[str, Any], *, content: str, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        content = _text(content)
        source_hash = _hash({"surface": record.get("surface"), "content": content}, 64)
        facts: list[dict[str, Any]] = []
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        params = payload.get("imageDraft") or payload.get("videoDraft") or payload.get("voiceDraft") or payload.get("promptCaptioning") or {}
        if isinstance(params, dict):
            for key in ("family", "loader", "model", "checkpoint", "width", "height", "steps", "cfg", "seed", "sampler", "positive_prompt", "negative_prompt"):
                if params.get(key) not in (None, "", [], {}):
                    facts.append({
                        "statement": f"Captured {record.get('surface') or 'surface'} setting {key}: {_text(params.get(key), 900)}",
                        "predicate": f"captured_{key}",
                        "fact_type": "live_surface_snapshot",
                        "object_value": _text(params.get(key), 900),
                        "confidence": 0.98,
                    })
        return self.ingest_text(
            project_id=str(record.get("project_id") or "general"),
            surface=str(record.get("surface") or "assistant"),
            identity=identity,
            source_type="assistant_project_brain_snapshot",
            source_id=f"snapshot:{record.get('surface') or 'assistant'}:{source_hash}",
            memory_type="live_surface_snapshot",
            event_type="assistant.project_brain.snapshot",
            title=str(record.get("title") or "Project Brain snapshot"),
            content=content,
            summary=str(record.get("summary") or content[:1200]),
            priority=0.82,
            metadata={"snapshot_id": record.get("snapshot_id") or "", "source_hash": source_hash},
            facts=facts,
        )

    def ingest_metadata_records(
        self,
        index: dict[str, Any],
        *,
        identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        records = [item for item in (index.get("records") or []) if isinstance(item, dict)]
        ingested: list[dict[str, Any]] = []
        deduplicated = 0
        for row in records:
            row_surface = str(row.get("surface") or index.get("surface") or "assistant")
            source_path = str(row.get("path") or row.get("file") or f"row:{_hash(row, 24)}")
            model = _text(row.get("model"), 300)
            summary = _text(row.get("summary"), 3000)
            content = "\n".join(part for part in [
                f"Surface: {row_surface}",
                f"File: {row.get('file') or ''}",
                f"Model: {model}" if model else "",
                f"Created: {row.get('created_at') or ''}" if row.get("created_at") else "",
                f"Summary: {summary}" if summary else "",
            ] if part).strip()
            facts = []
            if model:
                facts.append({
                    "statement": f"Indexed {row_surface} output {row.get('file') or source_path} used model {model}.",
                    "predicate": "used_model",
                    "fact_type": "generation_metadata",
                    "object_value": model,
                    "confidence": 0.95,
                })
            result = self.ingest_text(
                project_id=str(index.get("project_id") or "general"),
                surface=row_surface,
                identity=identity,
                source_type="assistant_project_brain_metadata",
                source_id=source_path,
                memory_type=f"{row_surface}_generation_metadata" if row_surface not in {"assistant", "global"} else "project_metadata",
                event_type="assistant.project_brain.metadata_indexed",
                title=f"Indexed {row_surface} metadata · {row.get('file') or Path(source_path).name}",
                content=content or _json(row),
                summary=summary or content[:1200],
                priority=0.69,
                metadata={"index_id": index.get("index_id") or "", "source_path": source_path, "record": row},
                facts=facts,
            )
            if result.get("deduplicated"):
                deduplicated += 1
            ingested.append(result)
        return {
            "ok": True,
            "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID,
            "status": "ingested",
            "record_count": len(records),
            "ingested_count": sum(1 for item in ingested if item.get("ok")),
            "deduplicated_count": deduplicated,
            "fragment_ids": [fid for item in ingested for fid in item.get("fragment_ids", [])],
            "items": ingested,
        }

    def ingest_document(
        self,
        record: dict[str, Any],
        *,
        extracted_text: str,
        extraction: dict[str, Any] | None = None,
        identity: dict[str, Any] | None = None,
        file_content_hash: str = "",
    ) -> dict[str, Any]:
        text = _text(extracted_text)
        if not text:
            return {
                "ok": True,
                "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID,
                "status": "stored_only",
                "fragment_ids": [],
                "reason": (extraction or {}).get("reason") or "no_extractable_text",
            }
        content_hash = file_content_hash or _hash(text, 64)
        chunks = _chunks(text)
        items: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            items.append(self.ingest_text(
                project_id=str(record.get("project_id") or "general"),
                surface=str(record.get("surface") or "assistant"),
                identity=identity,
                source_type="assistant_project_brain_upload",
                source_id=f"project_upload:{content_hash}:chunk:{index}",
                memory_type="uploaded_project_document",
                event_type="assistant.project_brain.document_ingested",
                title=f"{record.get('filename') or 'Project document'} · part {index + 1}/{len(chunks)}",
                content=chunk,
                summary=chunk[:1200],
                priority=0.84,
                metadata={
                    "upload_id": record.get("upload_id") or "",
                    "filename": record.get("filename") or "",
                    "stored_path": record.get("stored_path") or "",
                    "mime_type": record.get("mime_type") or "",
                    "file_content_hash": content_hash,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "extraction": extraction or {},
                },
            ))
        return {
            "ok": True,
            "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID,
            "status": "ingested",
            "file_content_hash": content_hash,
            "chunk_count": len(chunks),
            "fragment_ids": [fid for item in items for fid in item.get("fragment_ids", [])],
            "deduplicated_count": sum(1 for item in items if item.get("deduplicated")),
            "items": items,
        }

    def status(
        self,
        *,
        project_id: str = "general",
        surface: str = "assistant",
        identity: dict[str, Any] | CanonicalContextIdentity | None = None,
    ) -> dict[str, Any]:
        ident = _identity(project_id=project_id, surface=surface, identity=identity)
        storage = self.storage_identity(ident)
        with self._connect() as conn:
            clauses = ["status='active'", "scope_id=?"]
            params: list[Any] = [storage["scope_id"]]
            if storage["surface"]:
                clauses.append("surface=?")
                params.append(storage["surface"])
            if storage["project_id"]:
                clauses.append("project_id=?")
                params.append(storage["project_id"])
            where = " AND ".join(clauses)
            fragment_count = int(conn.execute(f"SELECT COUNT(*) FROM neo_memory_fragments WHERE {where}", params).fetchone()[0])
            fact_count = int(conn.execute(f"SELECT COUNT(*) FROM neo_memory_facts WHERE status='active' AND scope_id=?" + (" AND surface=?" if storage["surface"] else "") + (" AND project_id=?" if storage["project_id"] else ""), params).fetchone()[0])
            queued_embeddings = int(conn.execute(f"SELECT COUNT(*) FROM neo_memory_fragments WHERE {where} AND embedding_status!='indexed'", params).fetchone()[0])
            source_rows = conn.execute(
                f"SELECT source_type, COUNT(*) AS count FROM neo_memory_fragments WHERE {where} GROUP BY source_type ORDER BY count DESC",
                params,
            ).fetchall()
        return {
            "ok": True,
            "schema_id": PROJECT_BRAIN_INGESTION_SCHEMA_ID,
            "phase": PROJECT_BRAIN_INGESTION_PHASE,
            "identity": ident.as_dict(),
            "storage": storage,
            "counts": {
                "active_fragments": fragment_count,
                "active_facts": fact_count,
                "queued_embeddings": queued_embeddings,
            },
            "sources": {str(row[0]): int(row[1]) for row in source_rows},
            "policy": "Unified Memory is retrieval-authoritative. Legacy Project Brain JSON is a compatibility projection.",
        }

    def consolidate(
        self,
        *,
        project_id: str = "general",
        surface: str = "assistant",
        identity: dict[str, Any] | CanonicalContextIdentity | None = None,
        max_groups: int = 12,
    ) -> dict[str, Any]:
        ident = _identity(project_id=project_id, surface=surface, identity=identity)
        storage = self.storage_identity(ident)
        engine = UnifiedMemoryConsolidationEngine(self.db_path)
        return engine.run({
            "surface": storage["surface"],
            "project_id": storage["project_id"] or None,
            "scope_id": storage["scope_id"] or None,
            "include_existing": True,
            "min_group_size": 2,
            "max_groups": max_groups,
            "summary_item_limit": 16,
            "archive_originals": False,
        })


def get_project_brain_ingestion_service(*, root_dir: Path = ROOT_DIR, db_path: Path = DEFAULT_MEMORY_DB) -> ProjectBrainIngestionService:
    return ProjectBrainIngestionService(root_dir=root_dir, db_path=db_path)
