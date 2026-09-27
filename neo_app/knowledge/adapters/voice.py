from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref
from neo_app.knowledge.io import json_records, paginate, read_json, safe_relative
from ._helpers import compact_text, record_revision_token, status_to_lifecycle


class VoiceKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.voice"
    adapter_version = "1.0"
    authority_namespaces = ("voice.job", "voice.profile", "voice.replay")
    primitive_kinds = ("event", "object", "asset", "fragment")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup",)

    @property
    def job_history(self) -> Path:
        preferred = self.root_dir / "neo_data" / "outputs" / "voice" / "history" / "voice_jobs.v9.json"
        legacy = self.root_dir / "neo_data" / "outputs" / "voice" / "history" / "voice_jobs.v7.json"
        return preferred if preferred.exists() or not legacy.exists() else legacy

    @property
    def profile_index(self) -> Path:
        preferred = self.root_dir / "neo_data" / "outputs" / "voice" / "profiles" / "voice_profiles.v7.json"
        legacy = self.root_dir / "neo_data" / "outputs" / "voice" / "profiles" / "voice_profiles.v6.json"
        return preferred if preferred.exists() or not legacy.exists() else legacy

    @property
    def replay_dir(self) -> Path:
        return self.root_dir / "neo_data" / "outputs" / "voice" / "metadata"

    def _replays(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.replay_dir.exists():
            return []
        rows: list[tuple[Path, dict[str, Any]]] = []
        for path in self.replay_dir.glob("*.replay.v15.json"):
            data = read_json(path, default=None)
            if isinstance(data, dict) and data.get("replay_id"):
                rows.append((path, data))
        rows.sort(key=lambda pair: str(pair[1].get("created_at") or ""), reverse=True)
        return rows

    def _jobs(self) -> list[dict[str, Any]]:
        return json_records(self.job_history, keys=("jobs", "records", "items"))

    def _profiles(self) -> list[dict[str, Any]]:
        return json_records(self.profile_index, keys=("profiles", "records", "items"))

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        items = []
        for record in self._jobs():
            job_id = str(record.get("job_id") or "")
            if job_id: items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "voice.job", "native_id": job_id, "resolver_kind": "database_record", "resolver_key": {"path": safe_relative(self.root_dir, self.job_history), "id_key": "job_id"}}, "title": str(((record.get("script_snapshot") or {}).get("title") if isinstance(record.get("script_snapshot"), dict) else "") or f"Voice {job_id}"), "updated_at": record.get("updated_at") or record.get("created_at") or ""})
        for record in self._profiles():
            profile_id = str(record.get("profile_id") or "")
            if profile_id: items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "voice.profile", "native_id": profile_id, "resolver_kind": "database_record", "resolver_key": {"path": safe_relative(self.root_dir, self.profile_index), "id_key": "profile_id"}}, "title": str(record.get("name") or profile_id), "updated_at": record.get("updated_at") or record.get("created_at") or ""})
        for path, record in self._replays():
            replay_id = str(record.get("replay_id") or "")
            if replay_id: items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "voice.replay", "native_id": replay_id, "resolver_kind": "sidecar", "resolver_key": {"path": safe_relative(self.root_dir, path)}}, "title": str(record.get("memory_summary") or replay_id), "updated_at": record.get("created_at") or ""})
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        page, next_cursor, total = paginate(items, cursor, limit)
        status = "ready" if self.job_history.exists() or self.profile_index.exists() or self.replay_dir.exists() else "not_created"
        return {"ok": True, "status": status, "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _find(self, namespace: str, native_id: str) -> tuple[Path, dict[str, Any] | None]:
        if namespace == "voice.profile":
            return self.profile_index, next((item for item in self._profiles() if str(item.get("profile_id") or "") == native_id), None)
        if namespace == "voice.replay":
            for path, item in self._replays():
                if str(item.get("replay_id") or "") == native_id:
                    return path, item
            return self.replay_dir / f"{native_id}.replay.v15.json", None
        return self.job_history, next((item for item in self._jobs() if str(item.get("job_id") or "") == native_id), None)

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        namespace = str(ref.get("authority_namespace") or "voice.job"); native_id = str(ref.get("native_id") or "")
        path, record = self._find(namespace, native_id)
        if not record: return {"ok": False, "status": "missing", "adapter_id": self.adapter_id, "native_id": native_id}
        is_profile = namespace == "voice.profile"; is_replay = namespace == "voice.replay"; script = record.get("script_snapshot") if isinstance(record.get("script_snapshot"), dict) else {}
        if is_replay and isinstance(record.get("reuse_payload"), dict):
            reuse = record.get("reuse_payload") or {}
            script = {"title": reuse.get("script_title") or "", "text": reuse.get("script") or "", "delivery_notes": reuse.get("delivery_notes") or ""}
        title = str(record.get("name") or script.get("title") or record.get("memory_summary") or f"Voice {native_id}")
        search = compact_text(title, script.get("text"), script.get("delivery_notes"), record.get("family"), record.get("model_id"), record.get("voice_source"), record.get("default_params") or record.get("params"))
        rel = safe_relative(self.root_dir, path)
        native = make_native_ref(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, resolver_kind="sidecar" if is_replay else "database_record", resolver_key={"path": rel, "id_key": "replay_id" if is_replay else ("profile_id" if is_profile else "job_id")}, native_schema_id=str(record.get("schema_id") or ("neo.voice.replay_metadata.v15" if is_replay else ("neo.voice.profile.v7" if is_profile else "neo.voice.job.v12"))))
        bundle = build_projection_bundle(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, kind="object" if is_profile else ("fragment" if is_replay else "event"), title=title, search_text=search, native_ref=native, revision_token=record_revision_token(record, path=path), source_locator=SourceLocator(kind="record_key", path=rel, fields={"record_key": native_id, "id_key": "replay_id" if is_replay else ("profile_id" if is_profile else "job_id")}), context=self.context(context), evidence_role="native_profile_record" if is_profile else ("native_replay_record" if is_replay else "native_execution_record"), derivation="direct", origin="native_store", source_integrity="verified", claim_type="native_configuration" if is_profile else ("replay_evidence" if is_replay else "native_execution"), epistemic_state="established", execution_state="not_applicable" if (is_profile or is_replay) else str(record.get("status") or "unknown"), lifecycle_state=status_to_lifecycle(record.get("status")), entity_hints=[str(record.get("family") or ""), str(record.get("model_id") or "")], payload=record)
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        namespace = str(native.get("authority_namespace") or "voice.job"); native_id = str(native.get("native_id") or ref.get("native_id") or "")
        path, record = self._find(namespace, native_id)
        return {"ok": bool(record), "status": "verified" if record else "missing", "adapter_id": self.adapter_id, "native_id": native_id, "namespace": namespace, "path": safe_relative(self.root_dir, path), "record": record or {}}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}; q = str(data.get("query") or "").lower().strip(); namespace = str(data.get("authority_namespace") or "")
        items = []
        pools = [("voice.job", self._jobs()), ("voice.profile", self._profiles()), ("voice.replay", [item for _, item in self._replays()])]
        for ns, records in pools:
            if namespace and ns != namespace: continue
            id_key = "profile_id" if ns == "voice.profile" else ("replay_id" if ns == "voice.replay" else "job_id")
            for record in records:
                script = record.get("script_snapshot") if isinstance(record.get("script_snapshot"), dict) else {}
                if ns == "voice.replay" and isinstance(record.get("reuse_payload"), dict):
                    reuse = record.get("reuse_payload") or {}
                    script = {"title": reuse.get("script_title") or "", "text": reuse.get("script") or ""}
                hay = compact_text(record.get(id_key), record.get("name"), record.get("memory_summary"), script.get("title"), script.get("text"), record.get("family"), record.get("model_id"), record.get("backend_settings")).lower()
                if q and q not in hay: continue
                items.append({"authority_namespace": ns, "native_id": record.get(id_key), "title": record.get("name") or script.get("title") or record.get("memory_summary") or record.get(id_key), "status": record.get("status")})
                if len(items) >= max(1, min(int(data.get("limit") or 50), 200)): break
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}
