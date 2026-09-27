from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import NativeKnowledgeAdapter
from .adapters import (
    RoleplayKnowledgeAdapter,
    VideoKnowledgeAdapter,
    ImageKnowledgeAdapter,
    VoiceKnowledgeAdapter,
    PromptCaptioningKnowledgeAdapter,
    CodeKnowledgeAdapter,
    DocumentationKnowledgeAdapter,
)


class NativeKnowledgeAdapterRegistry:
    """Process-local registry for built-in Neo authority adapters.

    Registration contains adapter objects only. Native data remains in the owning
    subsystem; this registry never mirrors or migrates authority records.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self._adapters: dict[str, NativeKnowledgeAdapter] = {}

    def register(self, adapter: NativeKnowledgeAdapter, *, replace: bool = False) -> None:
        adapter_id = str(adapter.adapter_id or "").strip()
        if not adapter_id:
            raise ValueError("Native Knowledge Adapter requires adapter_id")
        if adapter_id in self._adapters and not replace:
            raise ValueError(f"Native Knowledge Adapter already registered: {adapter_id}")
        self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> NativeKnowledgeAdapter | None:
        return self._adapters.get(str(adapter_id or "").strip())

    def require(self, adapter_id: str) -> NativeKnowledgeAdapter:
        adapter = self.get(adapter_id)
        if adapter is None:
            raise KeyError(f"Unknown Native Knowledge Adapter: {adapter_id}")
        return adapter

    def adapters(self) -> list[NativeKnowledgeAdapter]:
        return [self._adapters[key] for key in sorted(self._adapters)]

    def describe(self) -> dict[str, Any]:
        items = [adapter.describe_adapter() for adapter in self.adapters()]
        return {
            "schema_id": "neo.knowledge.adapter_registry.v1",
            "status": "ready",
            "count": len(items),
            "adapters": items,
            "policy": "Registry routes references to native authorities. It does not own or duplicate native records.",
        }

    def status(self, *, deep: bool = False) -> dict[str, Any]:
        items = [adapter.status(deep=deep) for adapter in self.adapters()]
        return {
            "schema_id": "neo.knowledge.adapter_registry.status.v1",
            "status": "ready" if all(item.get("status") in {"ready", "not_created"} for item in items) else "degraded",
            "count": len(items),
            "adapters": items,
            "deep": bool(deep),
        }


def build_builtin_adapter_registry(root_dir: Path) -> NativeKnowledgeAdapterRegistry:
    registry = NativeKnowledgeAdapterRegistry(root_dir)
    for adapter_cls in (
        RoleplayKnowledgeAdapter,
        VideoKnowledgeAdapter,
        ImageKnowledgeAdapter,
        VoiceKnowledgeAdapter,
        PromptCaptioningKnowledgeAdapter,
        CodeKnowledgeAdapter,
        DocumentationKnowledgeAdapter,
    ):
        registry.register(adapter_cls(root_dir))
    return registry
