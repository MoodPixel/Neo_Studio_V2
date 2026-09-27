from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import CONTRACT_VERSION, EVIDENCE_VERSION, PROJECTION_VERSION, REFERENCE_VERSION
from .legacy import map_legacy_policy_fields
from .registry import NativeKnowledgeAdapterRegistry, build_builtin_adapter_registry

ROOT_DIR = Path(__file__).resolve().parents[2]


class NativeKnowledgeService:
    """Reference-first access to Neo-native knowledge authorities.

    This is intentionally not a retrieval planner. NKB-6 exposes authority,
    projection, hydration, validation and structured lookup. NKB-8 decides which
    adapters to query for an Assistant turn and how to fuse/rerank candidates.
    """

    def __init__(self, root_dir: Path = ROOT_DIR, registry: NativeKnowledgeAdapterRegistry | None = None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.registry = registry or build_builtin_adapter_registry(self.root_dir)

    def status(self, *, deep: bool = False) -> dict[str, Any]:
        registry = self.registry.status(deep=deep)
        return {
            "schema_id": "neo.knowledge.native_service.status.v1",
            "phase": "NKB-6",
            "status": registry.get("status") or "ready",
            "contract": {
                "contract_version": CONTRACT_VERSION,
                "reference_version": REFERENCE_VERSION,
                "evidence_version": EVIDENCE_VERSION,
                "projection_version": PROJECTION_VERSION,
            },
            "registry": registry,
            "deep": bool(deep),
            "endpoints": [
                "/api/memory/native-knowledge/status",
                "/api/memory/native-knowledge/adapters",
                "/api/memory/native-knowledge/enumerate",
                "/api/memory/native-knowledge/changes",
                "/api/memory/native-knowledge/project",
                "/api/memory/native-knowledge/resolve",
                "/api/memory/native-knowledge/validate",
                "/api/memory/native-knowledge/citation",
                "/api/memory/native-knowledge/lookup",
                "/api/memory/native-knowledge/relationships",
                "/api/memory/native-knowledge/legacy-mapping",
            ],
            "policy": "Native stores remain authoritative. This service returns references/evidence and never promotes generated content to canon by execution success.",
        }

    def adapters(self) -> dict[str, Any]:
        return self.registry.describe()

    def _adapter_id(self, payload: dict[str, Any]) -> str:
        ref = payload.get("native_ref") if isinstance(payload.get("native_ref"), dict) else payload
        adapter_id = str(payload.get("adapter_id") or ref.get("adapter_id") or "").strip()
        return adapter_id

    def _adapter(self, payload: dict[str, Any]):
        adapter_id = self._adapter_id(payload)
        adapter = self.registry.get(adapter_id)
        if adapter is None:
            return None, {"ok": False, "status": "unknown_adapter", "adapter_id": adapter_id, "available": [item.adapter_id for item in self.registry.adapters()]}
        return adapter, None

    def enumerate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        context = data.get("context") if isinstance(data.get("context"), dict) else data.get("identity") if isinstance(data.get("identity"), dict) else {}
        return adapter.enumerate_authorities(context, cursor=str(data.get("cursor") or ""), limit=int(data.get("limit") or 100))

    def enumerate_changes(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        return adapter.enumerate_changes(since=str(data.get("since") or ""), cursor=str(data.get("cursor") or ""), limit=int(data.get("limit") or 100), context=context)

    def project(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        native_ref = data.get("native_ref") if isinstance(data.get("native_ref"), dict) else data
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        return adapter.project(native_ref, context)

    def resolve(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        return adapter.resolve(data)

    def validate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        return adapter.validate(data)

    def citation(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        evidence = data.get("evidence_ref") if isinstance(data.get("evidence_ref"), dict) else data
        return adapter.resolve_citation(evidence)

    def lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        return adapter.structured_lookup(data)

    def relationships(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        adapter, error = self._adapter(data)
        if error: return error
        relation_filter = data.get("relation_filter") if isinstance(data.get("relation_filter"), list) else None
        return adapter.expand_relationships(data, relation_filter=relation_filter, max_hops=int(data.get("max_hops") or 1))

    def legacy_mapping(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "schema_id": "neo.knowledge.legacy_mapping.v1", **map_legacy_policy_fields(payload or {})}


@lru_cache(maxsize=1)
def get_native_knowledge_service() -> NativeKnowledgeService:
    return NativeKnowledgeService(ROOT_DIR)
