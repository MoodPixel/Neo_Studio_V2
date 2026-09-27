from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref
from neo_app.knowledge.io import paginate, safe_relative
from ._helpers import compact_text, status_to_lifecycle


_TABLES: dict[str, dict[str, str]] = {
    "roleplay.entity": {"table": "rp_entities", "id": "entity_id", "kind": "object", "title": "title", "text": "payload_json"},
    "roleplay.memory_fragment": {"table": "rp_memory_fragments", "id": "fragment_id", "kind": "fragment", "title": "memory_type", "text": "content"},
    "roleplay.canon": {"table": "rp_canon_records", "id": "canon_id", "kind": "fact", "title": "title", "text": "content"},
    "roleplay.edge": {"table": "rp_edges", "id": "edge_id", "kind": "edge", "title": "relation_type", "text": "payload_json"},
    "roleplay.source": {"table": "rp_source_documents", "id": "source_id", "kind": "source", "title": "title", "text": "body_preview"},
}


class RoleplayKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.roleplay"
    adapter_version = "1.0"
    authority_namespaces = tuple(_TABLES)
    primitive_kinds = ("object", "fact", "edge", "fragment", "source")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup", "relationships")

    @property
    def db_path(self) -> Path:
        return self.root_dir / "neo_data" / "roleplay" / "roleplay.sqlite"

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            return None

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _rows(self, namespace: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        spec = _TABLES.get(namespace)
        conn = self._connect()
        if not spec or conn is None:
            return []
        try:
            if not self._table_exists(conn, spec["table"]):
                return []
            rows = conn.execute(f"SELECT * FROM {spec['table']} LIMIT ?", (max(1, min(limit, 5000)),)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _context_allows(self, row: dict[str, Any], context: ContextRef) -> bool:
        wanted_project = context.project_id.strip()
        wanted_scope = context.scope_id.strip()
        project = str(row.get("project_id") or "").strip()
        scope = str(row.get("scope_id") or row.get("source_id") or "").strip()
        if wanted_project and project and wanted_project != project:
            return False
        # Built-in roleplay workspace can see roleplay-native rows. A non-empty
        # explicit non-roleplay scope may only see matching scoped rows.
        if wanted_scope and wanted_scope not in {"general", "roleplay_workspace"} and scope and wanted_scope != scope:
            return False
        return True

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        ctx = self.context(context)
        items = []
        for namespace, spec in _TABLES.items():
            for row in self._rows(namespace):
                if not self._context_allows(row, ctx):
                    continue
                native_id = str(row.get(spec["id"]) or "")
                if not native_id:
                    continue
                items.append({
                    "native_ref": {
                        "adapter_id": self.adapter_id,
                        "authority_namespace": namespace,
                        "native_id": native_id,
                        "resolver_kind": "sqlite_row",
                        "resolver_key": {"path": safe_relative(self.root_dir, self.db_path), "table": spec["table"], "id_key": spec["id"]},
                    },
                    "title": str(row.get(spec["title"]) or native_id),
                    "updated_at": row.get("updated_at") or row.get("created_at") or "",
                    "project_id": row.get("project_id") or "",
                    "scope_id": row.get("scope_id") or "",
                })
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        page, next_cursor, total = paginate(items, cursor, limit)
        return {"ok": True, "status": "ready" if self.db_path.exists() else "not_created", "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _find(self, namespace: str, native_id: str) -> dict[str, Any] | None:
        spec = _TABLES.get(namespace)
        conn = self._connect()
        if not spec or conn is None:
            return None
        try:
            if not self._table_exists(conn, spec["table"]):
                return None
            row = conn.execute(f"SELECT * FROM {spec['table']} WHERE {spec['id']} = ?", (native_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        namespace = str(ref.get("authority_namespace") or "")
        native_id = str(ref.get("native_id") or "")
        spec = _TABLES.get(namespace)
        row = self._find(namespace, native_id)
        ctx = self.context(context)
        if not spec or not row or not self._context_allows(row, ctx):
            return {"ok": False, "status": "missing", "adapter_id": self.adapter_id, "native_id": native_id}
        title = str(row.get(spec["title"]) or native_id)
        payload_json = row.get("payload_json")
        try:
            payload = json.loads(payload_json) if isinstance(payload_json, str) and payload_json else {}
        except Exception:
            payload = {}
        search = compact_text(title, row.get(spec["text"]), row.get("kind"), row.get("canon_type"), row.get("memory_type"), payload, row.get("tags_json"))
        rel = safe_relative(self.root_dir, self.db_path)
        native = make_native_ref(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, resolver_kind="sqlite_row", resolver_key={"path": rel, "table": spec["table"], "id_key": spec["id"]}, native_schema_id="neo.roleplay.memory.sqlite.v1")
        canon_state = "not_applicable"
        role = "roleplay_native_record"
        claim_type = "native_record"
        if namespace == "roleplay.canon":
            status = str(row.get("status") or "").lower()
            canon_state = "accepted" if status in {"canon", "accepted", "approved", "active_canon"} else ("rejected" if status in {"rejected", "deprecated"} else "candidate")
            role = "roleplay_canon_record"
            claim_type = "project_canon"
        elif namespace == "roleplay.memory_fragment":
            role = "roleplay_memory_fragment"
            claim_type = "memory_fragment"
        elif namespace == "roleplay.edge":
            role = "roleplay_relationship"
            claim_type = "relationship"
        revision_token = {"row": row, "table": spec["table"]}
        bundle = build_projection_bundle(
            adapter_id=self.adapter_id,
            authority_namespace=namespace,
            native_id=native_id,
            kind=spec["kind"],
            title=title,
            search_text=search,
            native_ref=native,
            revision_token=revision_token,
            source_locator=SourceLocator(kind="table_row", path=rel, fields={"table": spec["table"], "row_key": native_id}),
            context=ctx,
            evidence_role=role,
            derivation="direct",
            origin="native_store",
            source_integrity="verified",
            claim_type=claim_type,
            epistemic_state="established" if namespace != "roleplay.memory_fragment" else ("inferred" if str(row.get("status") or "").lower() in {"inferred", "candidate"} else "established"),
            canon_state=canon_state,
            canon_domain_ref=str(row.get("project_id") or row.get("scope_id") or "") if namespace == "roleplay.canon" else "",
            lifecycle_state=status_to_lifecycle(row.get("status")),
            temporal={"source_created_at": row.get("created_at") or "", "source_modified_at": row.get("updated_at") or ""},
            entity_hints=[str(row.get("kind") or ""), title],
            relation_hints=[str(row.get("relation_type") or "")] if namespace == "roleplay.edge" else [],
            payload={"row": row, "payload": payload},
        )
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        namespace = str(native.get("authority_namespace") or "")
        native_id = str(native.get("native_id") or ref.get("native_id") or "")
        row = self._find(namespace, native_id)
        ctx_value = ref.get("context") if isinstance(ref.get("context"), dict) else {}
        ctx = self.context(ctx_value)
        if row and not self._context_allows(row, ctx):
            row = None
        return {"ok": bool(row), "status": "verified" if row else "missing", "adapter_id": self.adapter_id, "native_id": native_id, "namespace": namespace, "path": safe_relative(self.root_dir, self.db_path), "record": row or {}}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        q = str(data.get("query") or "").strip().lower()
        wanted = str(data.get("authority_namespace") or "").strip()
        ctx = self.context(data.get("context") if isinstance(data.get("context"), dict) else data)
        items = []
        namespaces = [wanted] if wanted in _TABLES else list(_TABLES)
        for namespace in namespaces:
            spec = _TABLES[namespace]
            for row in self._rows(namespace):
                if not self._context_allows(row, ctx):
                    continue
                hay = compact_text(row.get(spec["id"]), row.get(spec["title"]), row.get(spec["text"]), row.get("payload_json"), row.get("tags_json")).lower()
                if q and q not in hay:
                    continue
                items.append({"authority_namespace": namespace, "native_id": row.get(spec["id"]), "title": row.get(spec["title"]) or row.get(spec["id"]), "status": row.get("status")})
                if len(items) >= max(1, min(int(data.get("limit") or 50), 200)):
                    return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}

    def expand_relationships(self, ref: dict[str, Any], *, relation_filter: list[str] | None = None, max_hops: int = 1) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        native_id = str(native.get("native_id") or "")
        namespace = str(native.get("authority_namespace") or "")
        ctx_value = ref.get("context") if isinstance(ref.get("context"), dict) else {}
        ctx = self.context(ctx_value)
        if namespace in _TABLES and namespace != "roleplay.edge":
            source_row = self._find(namespace, native_id)
            if not source_row or not self._context_allows(source_row, ctx):
                return {"ok": False, "status": "missing", "items": [], "count": 0}
        filters = set(relation_filter or [])
        conn = self._connect()
        if conn is None:
            return {"ok": False, "status": "missing", "items": [], "count": 0}
        try:
            if not self._table_exists(conn, "rp_edges"):
                return {"ok": True, "items": [], "count": 0, "max_hops": 1}
            rows = conn.execute("SELECT * FROM rp_edges WHERE source_id=? OR target_id=?", (native_id, native_id)).fetchall()
            items = [dict(row) for row in rows if not filters or str(row["relation_type"]) in filters]
            return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items), "max_hops": 1}
        finally:
            conn.close()
