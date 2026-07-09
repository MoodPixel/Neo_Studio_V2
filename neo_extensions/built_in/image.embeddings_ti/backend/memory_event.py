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


def build_memory_event(
    *,
    route: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    workflow_summary: str = "",
    assistant_summary: str = "",
    replay_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return memory_readiness_shape(
        route=route,
        assets=assets,
        params=params,
        outputs=outputs,
        replay_payload=replay_payload,
        workflow_summary=workflow_summary,
        assistant_summary=assistant_summary,
    )


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
        "schema_version": "neo.embeddings_ti.memory_event.v1",
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
        "embedding_tokens": deepcopy(shape.get("embedding_tokens") if isinstance(shape.get("embedding_tokens"), list) else []),
        "embedding_count": int(shape.get("embedding_count") or 0),
        "target_groups": deepcopy(shape.get("target_groups") if isinstance(shape.get("target_groups"), dict) else {}),
    }


def build_memory_event_payload_from_readiness(
    readiness: dict[str, Any] | None = None,
    *,
    result_id: str = "",
    outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert the readiness shape into a MemoryService event payload.

    Assistant features are still not implemented here; this only prepares the
    stable event payload Neo can persist later.
    """
    shape = normalize_memory_event_shape(readiness)
    route = shape["route"]
    summary = shape.get("assistant_summary") or shape.get("workflow_summary") or "Embeddings/TI workflow state recorded."
    output_files = outputs if isinstance(outputs, list) else []
    items = (shape.get("params") or {}).get("items") if isinstance(shape.get("params"), dict) else []
    tokens = [str(item.get("token") or item.get("name") or "") for item in items if isinstance(item, dict) and str(item.get("token") or item.get("name") or "").strip()] if isinstance(items, list) else []
    if not tokens:
        tokens = [str(token) for token in shape.get("embedding_tokens", []) if str(token or "").strip()]
    return {
        "namespace": f"extension:{EXTENSION_ID}",
        "surface": "image",
        "subtab": shape.get("subtab") or route.get("workflow_mode") or "generate",
        "source": "extension",
        "event_type": "extension_workflow_used",
        "title": "Embeddings/TI workflow used",
        "summary": summary,
        "extension_id": EXTENSION_ID,
        "family": route.get("family") or None,
        "loader": route.get("loader") or None,
        "tags": [tag for tag in ["image", "extension", "embeddings_ti", "textual_inversion", route.get("backend"), route.get("family"), route.get("loader"), route.get("workflow_mode")] if tag],
        "payload": {
            "schema_version": "neo.embeddings_ti.memory_payload.v1",
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
            "embedding_tokens": tokens,
        },
        "importance": "normal",
        "should_embed": True,
    }
