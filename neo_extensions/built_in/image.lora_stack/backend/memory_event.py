from __future__ import annotations

from copy import deepcopy
from typing import Any

from .metadata import EXTENSION_ID, EXTENSION_TYPE, WORKSPACE_APP, memory_readiness_shape


def _clean_route(route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    mode = route.get("workflow_mode") or route.get("mode") or "generate"
    return {
        "backend": route.get("backend") or route.get("provider") or route.get("provider_id") or "",
        "family": route.get("family") or "",
        "loader": route.get("loader") or "",
        "mode": mode,
        "workflow_mode": mode,
        "workspace_app": route.get("workspace_app") or WORKSPACE_APP,
        "route_state": route.get("route_state") or route.get("state") or "unknown",
    }


def memory_event(
    route: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    outputs: dict[str, Any] | list[dict[str, Any]] | None = None,
    *,
    validation: list[dict[str, Any]] | None = None,
    replay_payload: dict[str, Any] | None = None,
    workflow_summary: str = "",
    assistant_summary: str = "",
) -> dict[str, Any]:
    return memory_readiness_shape(
        route=route,
        assets=assets,
        params=params,
        outputs={"files": outputs} if isinstance(outputs, list) else outputs,
        validation=validation,
        replay_payload=replay_payload,
        workflow_summary=workflow_summary,
        assistant_summary=assistant_summary,
    )


def normalize_memory_event_shape(shape: dict[str, Any] | None = None) -> dict[str, Any]:
    shape = shape if isinstance(shape, dict) else {}
    route = _clean_route(shape.get("route") if isinstance(shape.get("route"), dict) else {})
    params = deepcopy(shape.get("params") if isinstance(shape.get("params"), dict) else {})
    assets = deepcopy(shape.get("assets") if isinstance(shape.get("assets"), dict) else {})
    outputs = deepcopy(shape.get("outputs") if isinstance(shape.get("outputs"), dict) else {})
    return {
        "schema_version": "neo.lora_stack.memory_event.v1",
        "event_type": "extension_workflow_used",
        "namespace": f"extension:{EXTENSION_ID}",
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "surface": "image",
        "subtab": shape.get("subtab") or route.get("workflow_mode") or route.get("mode") or "generate",
        "route": route,
        "assets": assets,
        "params": params,
        "outputs": outputs,
        "workflow_summary": str(shape.get("workflow_summary") or ""),
        "assistant_summary": str(shape.get("assistant_summary") or ""),
        "validation": deepcopy(shape.get("validation") if isinstance(shape.get("validation"), list) else []),
        "replay_payload": deepcopy(shape.get("replay_payload") if isinstance(shape.get("replay_payload"), dict) else {}),
        "restore_policy": shape.get("restore_policy") or ((shape.get("replay_payload") or {}).get("restore_policy") if isinstance(shape.get("replay_payload"), dict) else "revalidate_before_enable"),
        "revalidation_required": True,
        "revalidate_keys": deepcopy(shape.get("revalidate_keys") if isinstance(shape.get("revalidate_keys"), list) else ((shape.get("replay_payload") or {}).get("revalidate_keys") if isinstance(shape.get("replay_payload"), dict) else [])),
    }


def build_memory_event_payload_from_readiness(
    readiness: dict[str, Any] | None = None,
    *,
    result_id: str = "",
    outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert the LoRA Stack memory-readiness shape into a MemoryService event payload.

    The returned dict is accepted by MemoryEvent but keeps the original readiness
    shape inside payload for future Assistant replay/explanation workflows.
    """
    shape = normalize_memory_event_shape(readiness)
    route = shape["route"]
    summary = shape.get("assistant_summary") or shape.get("workflow_summary") or "LoRA Stack workflow state recorded."
    output_files = outputs if isinstance(outputs, list) else []
    loras = (shape.get("params") or {}).get("loras") if isinstance(shape.get("params"), dict) else []
    names = [str(row.get("name") or "") for row in loras if isinstance(row, dict) and str(row.get("name") or "").strip()] if isinstance(loras, list) else []
    return {
        "namespace": f"extension:{EXTENSION_ID}",
        "surface": "image",
        "subtab": shape.get("subtab") or route.get("workflow_mode") or "generate",
        "source": "extension",
        "event_type": "extension_workflow_used",
        "title": "LoRA Stack workflow used",
        "summary": summary,
        "extension_id": EXTENSION_ID,
        "family": route.get("family") or None,
        "loader": route.get("loader") or None,
        "tags": [tag for tag in ["image", "extension", "lora_stack", route.get("backend"), route.get("family"), route.get("loader"), route.get("workflow_mode")] if tag],
        "payload": {
            "schema_version": "neo.lora_stack.memory_payload.v1",
            "result_id": result_id,
            "route": route,
            "assets": shape.get("assets") or {},
            "params": shape.get("params") or {},
            "outputs": output_files or shape.get("outputs") or {},
            "workflow_summary": shape.get("workflow_summary") or "",
            "assistant_summary": summary,
            "validation": shape.get("validation") or [],
            "replay_payload": shape.get("replay_payload") or {},
            "restore_policy": shape.get("restore_policy") or "revalidate_before_enable",
            "revalidation_required": True,
            "revalidate_keys": shape.get("revalidate_keys") or [],
            "lora_names": names,
        },
        "importance": "normal",
        "should_embed": True,
    }
