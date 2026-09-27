from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref
from neo_app.knowledge.io import json_records, paginate, safe_relative
from ._helpers import compact_text, record_revision_token, status_to_lifecycle


KINDS: dict[str, tuple[str, str]] = {
    "saved_prompts": ("saved_prompts.json", "prompt_id"),
    "prompt_history": ("prompt_history.json", "history_id"),
    "prompt_presets": ("prompt_presets.json", "preset_id"),
    "characters": ("character_library.json", "character_id"),
    "saved_captions": ("saved_captions.json", "caption_id"),
    "caption_history": ("caption_history.json", "history_id"),
    "caption_presets": ("caption_presets.json", "preset_id"),
    "caption_components": ("caption_components.json", "component_id"),
    "caption_batch_results": ("caption_batch_results.json", "batch_id"),
    "handoff_history": ("handoff_history.json", "handoff_id"),
    "result_metadata": ("result_metadata.json", "metadata_id"),
    "qpe_history": ("qwen_image_21_pe_history.json", "result_id"),
}


class PromptCaptioningKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.prompt_captioning"
    adapter_version = "1.0"
    authority_namespaces = tuple(f"prompt_captioning.{kind}" for kind in KINDS)
    primitive_kinds = ("object", "fragment", "event", "asset")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup",)

    @property
    def data_dir(self) -> Path:
        return self.root_dir / "neo_data" / "prompt_captioning"

    def _path(self, kind: str) -> Path:
        return self.data_dir / KINDS[kind][0]

    def _records(self, kind: str) -> list[dict[str, Any]]:
        return json_records(self._path(kind), keys=("records", "items", "results"))

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        items = []
        for kind, (_, id_key) in KINDS.items():
            for record in self._records(kind):
                native_id = str(record.get(id_key) or "").strip()
                if not native_id: continue
                items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": f"prompt_captioning.{kind}", "native_id": native_id, "resolver_kind": "database_record", "resolver_key": {"path": safe_relative(self.root_dir, self._path(kind)), "kind": kind, "id_key": id_key}}, "title": self._title(kind, record, native_id), "updated_at": record.get("updated_at") or record.get("created_at") or ""})
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        page, next_cursor, total = paginate(items, cursor, limit)
        return {"ok": True, "status": "ready" if self.data_dir.exists() else "not_created", "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _title(self, kind: str, record: dict[str, Any], native_id: str) -> str:
        return str(record.get("name") or record.get("title") or record.get("tool_id") or record.get("caption") or record.get("output_text") or native_id)[:240]

    def _find(self, namespace: str, native_id: str) -> tuple[str, Path, str, dict[str, Any] | None]:
        kind = namespace.removeprefix("prompt_captioning.")
        if kind not in KINDS: return kind, self.data_dir / "missing.json", "id", None
        path = self._path(kind); id_key = KINDS[kind][1]
        return kind, path, id_key, next((item for item in self._records(kind) if str(item.get(id_key) or "") == native_id), None)

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        namespace = str(ref.get("authority_namespace") or ""); native_id = str(ref.get("native_id") or "")
        kind, path, id_key, record = self._find(namespace, native_id)
        if not record: return {"ok": False, "status": "missing", "adapter_id": self.adapter_id, "native_id": native_id}
        title = self._title(kind, record, native_id)
        search = compact_text(title, record.get("positive"), record.get("negative"), record.get("prompt"), record.get("caption"), record.get("source_text"), record.get("input_text"), record.get("output_text"), record.get("style"), record.get("tags"), record.get("notes"))
        rel = safe_relative(self.root_dir, path)
        native = make_native_ref(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, resolver_kind="database_record", resolver_key={"path": rel, "kind": kind, "id_key": id_key}, native_schema_id=str(record.get("schema_version") or record.get("schema_id") or ""))
        role = "user_curated_prompt" if kind == "saved_prompts" else "user_curated_caption" if kind == "saved_captions" else "native_result_record" if kind in {"result_metadata", "qpe_history"} else "native_history_record"
        derivation = "generated" if kind in {"result_metadata", "qpe_history", "caption_batch_results"} else "direct"
        bundle = build_projection_bundle(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, kind="fragment" if "caption" in kind or "prompt" in kind else "event", title=title, search_text=search, native_ref=native, revision_token=record_revision_token(record, path=path), source_locator=SourceLocator(kind="record_key", path=rel, fields={"record_key": native_id, "id_key": id_key}), context=self.context(context), evidence_role=role, derivation=derivation, origin="native_store", source_integrity="verified", claim_type="generated_content" if derivation == "generated" else "saved_native_content", epistemic_state="generated" if derivation == "generated" else "established", execution_state=str(record.get("status") or "not_applicable") if derivation == "generated" else "not_applicable", lifecycle_state=status_to_lifecycle(record.get("status")), payload=record)
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        namespace = str(native.get("authority_namespace") or ""); native_id = str(native.get("native_id") or ref.get("native_id") or "")
        kind, path, _, record = self._find(namespace, native_id)
        return {"ok": bool(record), "status": "verified" if record else "missing", "adapter_id": self.adapter_id, "native_id": native_id, "kind": kind, "path": safe_relative(self.root_dir, path), "record": record or {}}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}; q = str(data.get("query") or "").lower().strip(); wanted = str(data.get("kind") or "").strip(); items = []
        kinds = [wanted] if wanted in KINDS else list(KINDS)
        for kind in kinds:
            id_key = KINDS[kind][1]
            for record in self._records(kind):
                hay = compact_text(self._title(kind, record, ""), record.get("positive"), record.get("prompt"), record.get("caption"), record.get("source_text"), record.get("output_text"), record.get("tags")).lower()
                if q and q not in hay: continue
                items.append({"kind": kind, "authority_namespace": f"prompt_captioning.{kind}", "native_id": record.get(id_key), "title": self._title(kind, record, str(record.get(id_key) or ""))})
                if len(items) >= max(1, min(int(data.get("limit") or 50), 200)): break
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}
