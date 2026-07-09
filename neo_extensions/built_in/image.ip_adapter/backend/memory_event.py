from __future__ import annotations
from copy import deepcopy
from typing import Any
EXTENSION_ID = "image.ip_adapter"

def build_memory_event(*, route: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, params: dict[str, Any] | None = None, outputs: dict[str, Any] | None = None, workflow_summary: str = "", assistant_summary: str = "", replay_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"extension_id": EXTENSION_ID, "extension_type": "built_in", "workspace_app": "reference", "route": deepcopy(route or {}), "assets": deepcopy(assets or {}), "params": deepcopy(params or {}), "outputs": deepcopy(outputs or {}), "workflow_summary": workflow_summary, "assistant_summary": assistant_summary or workflow_summary, "replay_payload": deepcopy(replay_payload or {})}
