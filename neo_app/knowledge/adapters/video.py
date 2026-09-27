from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef
from neo_app.knowledge.io import paginate, read_json, resolve_inside, safe_relative
from ._helpers import compact_text, json_sidecar_projection, status_to_lifecycle


class VideoKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.video"
    adapter_version = "1.0"
    authority_namespaces = ("video.output", "video.asset", "video.lineage")
    primitive_kinds = ("event", "asset", "edge")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup", "relationships")

    @property
    def metadata_dir(self) -> Path:
        return self.root_dir / "neo_data" / "outputs" / "video" / "metadata"

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.metadata_dir.exists():
            return []
        rows: list[tuple[Path, dict[str, Any]]] = []
        for path in self.metadata_dir.glob("*.json"):
            data = read_json(path, default=None)
            if isinstance(data, dict) and str(data.get("schema_version") or data.get("schema_id") or "").startswith("neo.video.output") and data.get("result_id"):
                rows.append((path, data))
        rows.sort(key=lambda pair: str(pair[1].get("updated_at") or pair[1].get("created_at") or ""), reverse=True)
        return rows

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        rows = self._records()
        page, next_cursor, total = paginate(rows, cursor, limit)
        items = [{"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "video.output", "native_id": str(r.get("result_id")), "resolver_kind": "sidecar", "resolver_key": {"path": safe_relative(self.root_dir, p)}}, "title": str(r.get("assistant_summary") or r.get("result_id")), "updated_at": r.get("updated_at") or r.get("created_at") or ""} for p, r in page]
        return {"ok": True, "status": "ready" if self.metadata_dir.exists() else "not_created", "adapter_id": self.adapter_id, "items": items, "count": len(items), "total": total, "next_cursor": next_cursor}

    def _find(self, native_id: str, resolver_path: str = "") -> tuple[Path | None, dict[str, Any] | None]:
        if resolver_path:
            path = resolve_inside(self.root_dir, resolver_path)
            if path and path.exists():
                data = read_json(path, default=None)
                if isinstance(data, dict) and str(data.get("result_id") or "") == native_id:
                    return path, data
        candidate = self.metadata_dir / f"{Path(native_id).name}.json"
        if candidate.exists():
            data = read_json(candidate, default=None)
            if isinstance(data, dict):
                return candidate, data
        for path, record in self._records():
            if str(record.get("result_id") or "") == native_id:
                return path, record
        return None, None

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        native_id = str(ref.get("native_id") or "")
        resolver = ref.get("resolver_key") if isinstance(ref.get("resolver_key"), dict) else {}
        path, record = self._find(native_id, str(resolver.get("path") or ""))
        if not path or not record:
            return {"ok": False, "status": "missing", "adapter_id": self.adapter_id, "native_id": native_id}
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
        search = compact_text(record.get("assistant_summary"), record.get("prompt"), record.get("negative_prompt"), record.get("family"), record.get("route_id"), source, lineage)
        bundle = json_sidecar_projection(root_dir=self.root_dir, adapter_id=self.adapter_id, authority_namespace="video.output", native_id=native_id, kind="event", title=str(record.get("assistant_summary") or f"Video {native_id}"), record=record, path=path, search_text=search, evidence_role="native_execution_record", context=self.context(context), native_schema_id=str(record.get("schema_version") or "neo.video.output.v7"), execution_state=str(record.get("status") or "unknown"), lifecycle_state=status_to_lifecycle(record.get("status")), entity_hints=[str(record.get("family") or ""), str(record.get("route_id") or "")], relation_hints=["lineage.derived_from"] if any(lineage.values()) else [])
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        native_id = str(native.get("native_id") or ref.get("native_id") or "")
        resolver = native.get("resolver_key") if isinstance(native.get("resolver_key"), dict) else {}
        path, record = self._find(native_id, str(resolver.get("path") or ""))
        return {"ok": bool(record), "status": "verified" if record else "missing", "adapter_id": self.adapter_id, "native_id": native_id, "path": safe_relative(self.root_dir, path) if path else "", "record": record or {}}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}; q = str(data.get("query") or "").lower().strip(); category = str(data.get("category") or "").strip()
        items = []
        for path, record in self._records():
            if category and str(record.get("category") or "") != category: continue
            hay = compact_text(record.get("result_id"), record.get("assistant_summary"), record.get("prompt"), record.get("family"), record.get("route_id")).lower()
            if q and q not in hay: continue
            items.append({"native_id": record.get("result_id"), "title": record.get("assistant_summary") or record.get("result_id"), "path": safe_relative(self.root_dir, path), "status": record.get("status"), "category": record.get("category")})
            if len(items) >= max(1, min(int(data.get("limit") or 50), 200)): break
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}

    def expand_relationships(self, ref: dict[str, Any], *, relation_filter: list[str] | None = None, max_hops: int = 1) -> dict[str, Any]:
        resolved = self.resolve(ref)
        if not resolved.get("ok"): return {"ok": False, "status": "missing", "items": [], "count": 0}
        record = resolved["record"]; lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
        items = []
        for field in ("parent_result_id", "source_result_id"):
            target = str(lineage.get(field) or "").strip()
            if target: items.append({"relation_type": "lineage.derived_from", "source_id": record.get("result_id"), "target_id": target, "field": field})
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items), "max_hops": 1}
