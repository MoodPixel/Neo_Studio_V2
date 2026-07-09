from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

SURFACE_ID = "prompt_captioning"
WORKSPACE_APP = "neo_studio"
METADATA_SCHEMA_VERSION = "prompt_captioning.result_metadata.v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: Any, limit: int = 260) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text




def _asset_label(assets: dict[str, Any]) -> str:
    value = assets.get("image") or assets.get("source_image") or assets.get("result_image") or ""
    if not value and isinstance(assets.get("images"), list) and assets.get("images"):
        first = assets["images"][0]
        if isinstance(first, dict):
            value = first.get("asset_ref") or first.get("path") or first.get("filename") or ""
        else:
            value = first
    return str(value or "")


def _output_text(outputs: dict[str, Any]) -> str:
    for key in ("prompt", "caption", "output_prompt", "output_caption", "output_text", "text", "partial_text"):
        value = str(outputs.get(key) or "").strip()
        if value:
            return value
    return ""

def normalize_route(provider_id: str = "", backend_profile_id: str = "", model: str = "", route_state: str = "available", reason: str = "") -> dict[str, Any]:
    return {
        "provider_id": provider_id or "",
        "backend_profile_id": backend_profile_id or "",
        "model": model or "",
        "route_state": route_state or "available",
        "reason": reason or "",
    }


def build_replay_payload(payload: dict[str, Any] | None, outputs: dict[str, Any] | None = None, reuse_mode: str = "rerun") -> dict[str, Any]:
    clean = payload if isinstance(payload, dict) else {}
    return {
        "schema_version": "prompt_captioning.replay_payload.v1",
        "surface_id": SURFACE_ID,
        "workspace": SURFACE_ID,
        "mode": clean.get("mode") or "",
        "tool": clean.get("tool") or clean.get("tool_id") or "",
        "inputs": clean.get("inputs") if isinstance(clean.get("inputs"), dict) else {},
        "params": clean.get("params") if isinstance(clean.get("params"), dict) else {},
        "assets": clean.get("assets") if isinstance(clean.get("assets"), dict) else {},
        "metadata": clean.get("metadata") if isinstance(clean.get("metadata"), dict) else {},
        "outputs": outputs if isinstance(outputs, dict) else {},
        "reuse_mode": reuse_mode,
    }


def build_assistant_summary(tool_id: str, outputs: dict[str, Any] | None, route: dict[str, Any] | None = None, assets: dict[str, Any] | None = None) -> str:
    outputs = outputs if isinstance(outputs, dict) else {}
    route = route if isinstance(route, dict) else {}
    assets = assets if isinstance(assets, dict) else {}
    text = outputs.get("text") or outputs.get("prompt") or outputs.get("caption") or ""
    provider = route.get("provider_id") or route.get("backend_profile_id") or "local"
    asset_note = ""
    if assets.get("image") or assets.get("source_image") or assets.get("images"):
        asset_note = " using image assets"
    return f"{tool_id or 'tool'} produced `{_compact_text(text, 120)}` via {provider}{asset_note}."


def build_result_metadata(
    *,
    tool_id: str,
    mode: str,
    payload: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    workflow_summary: str = "",
    assistant_summary: str = "",
    event_type: str = "",
    gated_reason: str = "",
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    outputs = outputs if isinstance(outputs, dict) else {}
    route = route if isinstance(route, dict) else {}
    assets = assets if isinstance(assets, dict) else (payload.get("assets") if isinstance(payload.get("assets"), dict) else {})
    params = params if isinstance(params, dict) else (payload.get("params") if isinstance(payload.get("params"), dict) else {})
    route_state = route.get("route_state") or ("provider_gated" if gated_reason else "available")
    route.setdefault("route_state", route_state)
    if gated_reason:
        route.setdefault("reason", gated_reason)
    summary = assistant_summary or build_assistant_summary(tool_id, outputs, route, assets)
    replay_payload = build_replay_payload(payload, outputs)
    asset_label = _asset_label(assets)
    output_text = _output_text(outputs)
    return {
        "metadata_id": f"pcmeta_{uuid4().hex[:12]}",
        "schema_version": METADATA_SCHEMA_VERSION,
        "created_at": now_iso(),
        "surface_id": SURFACE_ID,
        "tool_id": tool_id or payload.get("tool") or payload.get("tool_id") or "",
        "mode": mode or payload.get("mode") or "",
        "workspace_app": WORKSPACE_APP,
        "event_type": event_type or f"prompt_captioning.{mode or 'tool'}.{tool_id or 'ran'}",
        "route": route,
        "backend_profile_id": route.get("backend_profile_id") or "",
        "provider_id": route.get("provider_id") or "",
        "model": route.get("model") or "",
        "route_state": route.get("route_state") or route_state,
        "assets": assets,
        "source_image": asset_label,
        "source_image_name": asset_label.replace("\\", "/").split("/")[-1] if asset_label else "",
        "params": params,
        "prompt_text": str((payload.get("inputs") or {}).get("source_text") or (payload.get("inputs") or {}).get("prompt") or ""),
        "caption_instruction": str((payload.get("inputs") or {}).get("caption_instruction") or ""),
        "output_text": output_text,
        "outputs": outputs,
        "gated_reason": gated_reason or route.get("reason") or "",
        "workflow_summary": workflow_summary,
        "assistant_summary": summary,
        "replay_payload": replay_payload,
    }


def build_handoff_metadata(event: dict[str, Any] | None = None, client_mutation: dict[str, Any] | None = None) -> dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    client_mutation = client_mutation if isinstance(client_mutation, dict) else {}
    target_workspace = client_mutation.get("target_workspace") or event.get("target_workspace") or ""
    target_field = client_mutation.get("target_field") or event.get("target_field") or ""
    mode = client_mutation.get("mode") or event.get("mode") or ""
    return build_result_metadata(
        tool_id="cross_tab_handoff",
        mode="handoff",
        payload={
            "workspace": SURFACE_ID,
            "mode": "handoff",
            "tool": "cross_tab_handoff",
            "inputs": {"text": client_mutation.get("text") or event.get("text_preview") or ""},
            "params": {"handoff_mode": mode},
            "assets": client_mutation.get("assets") or event.get("assets") or {},
            "metadata": event,
        },
        outputs={"target_workspace": target_workspace, "target_field": target_field, "handoff_mode": mode},
        route=normalize_route(route_state="available"),
        workflow_summary=f"Sent Prompt/Captioning output to {target_workspace}.{target_field} using {mode} mode.",
        assistant_summary=f"Prompt/Captioning handoff sent content to {target_workspace}.{target_field} with {mode} mode.",
        event_type="prompt_captioning.handoff.sent",
    )
