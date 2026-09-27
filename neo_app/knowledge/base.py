from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .contracts import ADAPTER_API_VERSION, ContextRef, make_context


class NativeKnowledgeAdapter(ABC):
    adapter_id = "neo.unknown"
    adapter_version = "1.0"
    authority_namespaces: tuple[str, ...] = ()
    primitive_kinds: tuple[str, ...] = ()
    query_capabilities: tuple[str, ...] = ("enumerate", "project", "resolve", "validate", "citation")

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir).resolve()

    def describe_adapter(self) -> dict[str, Any]:
        return {
            "schema_id": ADAPTER_API_VERSION,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "supported_contract_versions": ["neo.knowledge.contract.v1"],
            "authority_namespaces": list(self.authority_namespaces),
            "primitive_kinds": list(self.primitive_kinds),
            "query_capabilities": list(self.query_capabilities),
            "status": "ready",
        }

    def context(self, value: dict[str, Any] | ContextRef | None = None) -> ContextRef:
        return make_context(value)

    @abstractmethod
    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        raise NotImplementedError

    def enumerate_changes(self, *, since: str = "", cursor: str = "", limit: int = 100, context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        result = self.enumerate_authorities(context, cursor=cursor, limit=limit)
        if not since:
            result["since"] = ""
            return result
        items = []
        for item in result.get("items", []):
            changed_at = str(item.get("updated_at") or item.get("created_at") or "")
            if not changed_at or changed_at >= since:
                items.append(item)
        result["items"] = items
        result["count"] = len(items)
        result["since"] = since
        return result

    @abstractmethod
    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def validate(self, ref: dict[str, Any]) -> dict[str, Any]:
        resolved = self.resolve(ref)
        status = "verified" if resolved.get("ok") else (resolved.get("status") or "missing")
        if status not in {"verified", "present", "stale", "missing", "unavailable"}:
            status = "present" if resolved.get("ok") else "missing"
        return {"ok": bool(resolved.get("ok")), "status": status, "adapter_id": self.adapter_id, "native_ref": ref.get("native_ref") or ref}

    def resolve_citation(self, evidence_ref: dict[str, Any]) -> dict[str, Any]:
        locator = evidence_ref.get("source_locator") if isinstance(evidence_ref.get("source_locator"), dict) else {}
        if not locator:
            native = evidence_ref.get("native_ref") if isinstance(evidence_ref.get("native_ref"), dict) else {}
            resolver = native.get("resolver_key") if isinstance(native.get("resolver_key"), dict) else {}
            locator = {"kind": native.get("resolver_kind") or "native", **resolver}
        return {"ok": bool(locator), "adapter_id": self.adapter_id, "locator": locator}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "adapter_id": self.adapter_id, "items": [], "count": 0, "status": "not_supported"}

    def expand_relationships(self, ref: dict[str, Any], *, relation_filter: list[str] | None = None, max_hops: int = 1) -> dict[str, Any]:
        return {"ok": True, "adapter_id": self.adapter_id, "items": [], "count": 0, "status": "not_supported", "max_hops": max(0, min(int(max_hops or 1), 1))}

    def status(self, *, deep: bool = False) -> dict[str, Any]:
        payload = self.describe_adapter()
        payload["authority_status"] = "lazy"
        payload["authority_count_hint"] = None
        if not deep:
            return payload
        try:
            sample = self.enumerate_authorities(limit=1)
            payload["authority_status"] = sample.get("status") or "ready"
            payload["authority_count_hint"] = sample.get("total") if sample.get("total") is not None else sample.get("count", 0)
        except Exception as exc:
            payload["status"] = "degraded"
            payload["authority_status"] = "unavailable"
            payload["error"] = str(exc)[:400]
        return payload
