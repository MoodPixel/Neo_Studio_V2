from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef
from neo_app.knowledge.io import paginate, read_json, resolve_inside, safe_relative
from ._helpers import compact_text, json_sidecar_projection, status_to_lifecycle


class ImageKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.image"
    adapter_version = "1.0"
    authority_namespaces = ("image.output", "image.result", "image.asset", "image.lineage")
    primitive_kinds = ("asset", "event", "edge")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup", "relationships")

    @property
    def metadata_root(self) -> Path:
        return self.root_dir / "neo_data" / "outputs" / "image_metadata"

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.metadata_root.exists(): return []
        rows = []
        for path in self.metadata_root.rglob("*.json"):
            data = read_json(path, default=None)
            schema = str((data or {}).get("schema_version") or (data or {}).get("schema_id") or "") if isinstance(data, dict) else ""
            if isinstance(data, dict) and schema.startswith("neo.image.output") and data.get("result_id"):
                rows.append((path, data))
        rows.sort(key=lambda pair: str(pair[1].get("created_at") or ""), reverse=True)
        return rows

    def _authorities_for_record(self, path: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
        result_id = str(record.get("result_id") or "")
        files = ((record.get("outputs") or {}).get("files") or []) if isinstance(record.get("outputs"), dict) else []
        items = []
        for file_record in files:
            if not isinstance(file_record, dict): continue
            native_id = str(file_record.get("neo_output_id") or "").strip()
            if native_id:
                items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "image.output", "native_id": native_id, "resolver_kind": "sidecar", "resolver_key": {"path": safe_relative(self.root_dir, path), "result_id": result_id, "file_id": str(file_record.get("file_id") or "")}}, "title": str(file_record.get("filename") or native_id), "updated_at": record.get("created_at") or ""})
        if not items:
            items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "image.result", "native_id": result_id, "resolver_kind": "sidecar", "resolver_key": {"path": safe_relative(self.root_dir, path)}}, "title": str(record.get("assistant_summary") or result_id), "updated_at": record.get("created_at") or ""})
        return items

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        items = []
        for path, record in self._records(): items.extend(self._authorities_for_record(path, record))
        page, next_cursor, total = paginate(items, cursor, limit)
        return {"ok": True, "status": "ready" if self.metadata_root.exists() else "not_created", "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _find(self, native_id: str, resolver_path: str = "") -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
        paths = []
        if resolver_path:
            p = resolve_inside(self.root_dir, resolver_path)
            if p and p.exists(): paths.append(p)
        paths.extend(path for path, _ in self._records() if path not in paths)
        for path in paths:
            record = read_json(path, default=None)
            if not isinstance(record, dict): continue
            if str(record.get("result_id") or "") == native_id: return path, record, None
            files = ((record.get("outputs") or {}).get("files") or []) if isinstance(record.get("outputs"), dict) else []
            match = next((item for item in files if isinstance(item, dict) and str(item.get("neo_output_id") or "") == native_id), None)
            if match: return path, record, match
        return None, None, None

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        native_id = str(ref.get("native_id") or ""); resolver = ref.get("resolver_key") if isinstance(ref.get("resolver_key"), dict) else {}
        path, record, file_record = self._find(native_id, str(resolver.get("path") or ""))
        if not path or not record: return {"ok": False, "status": "missing", "adapter_id": self.adapter_id, "native_id": native_id}
        prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
        model = record.get("model") if isinstance(record.get("model"), dict) else {}
        lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
        namespace = "image.output" if file_record else "image.result"
        payload = {"record": record, "file": file_record or {}}
        title = str((file_record or {}).get("filename") or record.get("assistant_summary") or native_id)
        search = compact_text(record.get("assistant_summary"), prompt.get("effective_positive") or prompt.get("positive"), prompt.get("negative"), model, lineage, title)
        bundle = json_sidecar_projection(root_dir=self.root_dir, adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, kind="asset" if file_record else "event", title=title, record=payload, path=path, search_text=search, evidence_role="native_execution_record", context=self.context(context), native_schema_id=str(record.get("schema_version") or "neo.image.output.v1"), execution_state=str(((record.get("job") or {}).get("status") if isinstance(record.get("job"), dict) else "") or "unknown"), lifecycle_state=status_to_lifecycle(((record.get("job") or {}).get("status") if isinstance(record.get("job"), dict) else "")), entity_hints=[str(model.get("family") or ""), str(model.get("model") or "")], relation_hints=["lineage.derived_from"] if any(lineage.values()) else [])
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        native_id = str(native.get("native_id") or ref.get("native_id") or ""); resolver = native.get("resolver_key") if isinstance(native.get("resolver_key"), dict) else {}
        path, record, file_record = self._find(native_id, str(resolver.get("path") or ""))
        return {"ok": bool(record), "status": "verified" if record else "missing", "adapter_id": self.adapter_id, "native_id": native_id, "path": safe_relative(self.root_dir, path) if path else "", "record": record or {}, "file": file_record or {}}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}; q = str(data.get("query") or "").lower().strip(); items = []
        for path, record in self._records():
            prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}; model = record.get("model") if isinstance(record.get("model"), dict) else {}
            hay = compact_text(record.get("result_id"), record.get("assistant_summary"), prompt.get("positive"), prompt.get("effective_positive"), model).lower()
            if q and q not in hay: continue
            items.append({"native_id": record.get("result_id"), "title": record.get("assistant_summary") or record.get("result_id"), "path": safe_relative(self.root_dir, path), "model": model})
            if len(items) >= max(1, min(int(data.get("limit") or 50), 200)): break
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}

    def expand_relationships(self, ref: dict[str, Any], *, relation_filter: list[str] | None = None, max_hops: int = 1) -> dict[str, Any]:
        resolved = self.resolve(ref)
        if not resolved.get("ok"): return {"ok": False, "status": "missing", "items": [], "count": 0}
        record = resolved["record"]; lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}; source_id = resolved.get("native_id")
        items = []
        for field in ("parent_output_id", "source_output_id", "root_output_id"):
            target = str(lineage.get(field) or "").strip()
            if target and target != source_id: items.append({"relation_type": "lineage.derived_from" if field != "root_output_id" else "lineage.rooted_in", "source_id": source_id, "target_id": target, "field": field})
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items), "max_hops": 1}
