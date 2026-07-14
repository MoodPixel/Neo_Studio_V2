"""Metadata helpers for the Image Upscale built-in extension.

Phase J6 hardens the output-side contract used by the queue route, Output
Inspector, replay, and Assistant/memory readiness. This extension is a
standalone Finish utility, so metadata must describe source assets and the
Comfy utility graph instead of pretending a prompt/compiler route was used.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_NAME, EXTENSION_TYPE, WORKSPACE_APP
from .payload_schema import replay_payload_from_block, validate_replay_payload


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _mode_label(params: dict[str, Any]) -> str:
    engine = _clean(params.get("upscale_engine") or params.get("image_upscale_engine") or "basic").lower()
    if engine == "seedvr2":
        return "SeedVR2 experimental"
    return "model upscale" if _clean(params.get("upscale_model") or params.get("image_upscale_model")) else "interpolation upscale"


def _scale_label(params: dict[str, Any]) -> str:
    scale = params.get("scale", params.get("image_upscale_scale", ""))
    return _clean(scale)


def _restore_label(params: dict[str, Any]) -> str:
    restore = _clean(params.get("restore_assist") or params.get("image_upscale_restore_assist") or "off").lower()
    return "CodeFormer" if restore == "codeformer" else "off"


def _alpha_label(params: dict[str, Any]) -> str:
    if _clean(params.get("upscale_engine") or params.get("image_upscale_engine") or "basic").lower() != "seedvr2":
        return ""
    if params.get("seedvr2_alpha_route_applied") or params.get("_neo_seedvr2_alpha_route_applied"):
        return "RGBA preserved"
    mode = _clean(params.get("seedvr2_alpha_mode") or "auto").lower()
    if mode == "discard":
        return "transparency discarded"
    return "RGB source"


def build_assistant_summary(params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, *, queued_count: int | None = None) -> str:
    clean_params = params or {}
    clean_assets = assets or {}
    count = queued_count
    if count is None:
        images = clean_assets.get("source_images") if isinstance(clean_assets.get("source_images"), list) else []
        count = len(images) if images else None
    prefix = "Image Upscale queued"
    if count:
        prefix += f" {count} image{'s' if count != 1 else ''}"
    mode = _mode_label(clean_params)
    scale = _scale_label(clean_params)
    summary = f"{prefix} using {mode}"
    if scale:
        summary += f" at {scale}x"
    restore = _restore_label(clean_params)
    if restore == "CodeFormer":
        summary += " with CodeFormer restore"
    alpha = _alpha_label(clean_params)
    if alpha:
        summary += f" with {alpha}"
    summary += "."
    return summary


def build_workflow_summary(params: dict[str, Any] | None = None, notes: list[str] | None = None) -> str:
    clean_notes = [str(note).strip() for note in (notes or []) if str(note or "").strip()]
    if clean_notes:
        return "; ".join(clean_notes)
    return build_assistant_summary(params)


def build_image_upscale_extension_usage(
    *,
    params: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    node_status: dict[str, Any] | None = None,
    gated_reason: str = "",
) -> dict[str, Any]:
    clean_params = deepcopy(params or {})
    clean_route = deepcopy(route or {})
    return {
        "extension_id": EXTENSION_ID,
        "name": EXTENSION_NAME,
        "label": "Image Upscale",
        "origin": EXTENSION_TYPE,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "status": "enabled" if not gated_reason else "gated",
        "enabled": not bool(gated_reason),
        "scale": clean_params.get("scale") or clean_params.get("image_upscale_scale"),
        "upscale_engine": clean_params.get("upscale_engine") or clean_params.get("image_upscale_engine") or "basic",
        "upscaler": clean_params.get("seedvr2_dit_model") if (clean_params.get("upscale_engine") == "seedvr2") else (clean_params.get("upscale_model") or clean_params.get("image_upscale_model") or "Interpolation only"),
        "restore_assist": clean_params.get("restore_assist") or clean_params.get("image_upscale_restore_assist") or "off",
        "alpha_mode": clean_params.get("seedvr2_alpha_mode") or "",
        "alpha_route_applied": bool(clean_params.get("seedvr2_alpha_route_applied") or clean_params.get("_neo_seedvr2_alpha_route_applied")),
        "route": clean_route,
        "route_state": clean_route.get("route_state"),
        "node_status": deepcopy(node_status or {}),
        "gated_reason": gated_reason,
        "assistant_summary": build_assistant_summary(clean_params),
    }


def build_image_upscale_metadata(
    *,
    route: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    payload_block: dict[str, Any] | None = None,
    workflow_summary: str = "",
    gated_reason: str = "",
    node_status: dict[str, Any] | None = None,
    compile_notes: list[str] | None = None,
) -> dict[str, Any]:
    clean_params = deepcopy(params or {})
    clean_assets = deepcopy(assets or {})
    clean_route = deepcopy(route or {})
    replay_payload = replay_payload_from_block(payload_block)
    assistant_summary = build_assistant_summary(clean_params, clean_assets)
    workflow_text = workflow_summary or build_workflow_summary(clean_params, compile_notes)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": clean_route,
        "assets": clean_assets,
        "params": clean_params,
        "outputs": deepcopy(outputs or {}),
        "workflow_summary": workflow_text,
        "assistant_summary": assistant_summary,
        "replay_payload": replay_payload,
        "replay_readiness": validate_replay_payload(replay_payload, require_source=False),
        "node_status": deepcopy(node_status or {}),
        "gated_reason": gated_reason,
        "output_inspector": {
            "title": "Image · Image Upscale",
            "chips": build_output_inspector_chips(clean_params, clean_assets, clean_route, node_status=node_status),
        },
    }


def build_output_inspector_chips(
    params: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    *,
    node_status: dict[str, Any] | None = None,
) -> list[str]:
    clean_params = params or {}
    clean_assets = assets or {}
    clean_route = route or {}
    source_images = clean_assets.get("source_images") if isinstance(clean_assets.get("source_images"), list) else []
    chips = [
        "Status · Queued",
        f"Mode · {_mode_label(clean_params)}",
    ]
    scale = _scale_label(clean_params)
    if scale:
        chips.append(f"Scale · {scale}x")
    resize = _clean(clean_params.get("resize_method") or clean_params.get("image_upscale_resize_method"))
    if resize:
        chips.append(f"Resize · {resize}")
    engine = _clean(clean_params.get("upscale_engine") or clean_params.get("image_upscale_engine") or "basic")
    if engine == "seedvr2":
        chips.append("Engine · SeedVR2 experimental")
        dit = _clean(clean_params.get("seedvr2_dit_model"))
        vae = _clean(clean_params.get("seedvr2_vae_model"))
        if dit:
            chips.append(f"SeedVR2 DiT · {dit}")
        if vae:
            chips.append(f"SeedVR2 VAE · {vae}")
        if clean_params.get("seedvr2_resolution"):
            chips.append(f"SeedVR2 short edge · {clean_params.get('seedvr2_resolution')}px")
        alpha = _alpha_label(clean_params)
        if alpha:
            chips.append(f"Alpha · {alpha}")
        source_mode = _clean(clean_params.get("seedvr2_source_image_mode"))
        if source_mode:
            chips.append(f"Source mode · {source_mode}")
        if clean_params.get("seedvr2_alpha_route_applied") or clean_params.get("_neo_seedvr2_alpha_route_applied"):
            chips.append("Format · PNG")
    else:
        upscaler = _clean(clean_params.get("upscale_model") or clean_params.get("image_upscale_model"))
        chips.append(f"Upscaler · {upscaler or 'Interpolation only'}")
    restore = _restore_label(clean_params)
    chips.append(f"Restore · {restore}")
    if source_images:
        chips.append(f"Sources · {len(source_images)}")
    backend = _clean(clean_route.get("backend") or clean_route.get("provider_id"))
    if backend:
        chips.append(f"Backend · {backend}")
    if node_status and node_status.get("ready") is not None:
        chips.append(f"Node readiness · {'ready' if node_status.get('ready') else 'blocked'}")
    return chips


def build_image_upscale_memory_event(
    *,
    route: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    workflow_summary: str = "",
    assistant_summary: str = "",
    replay_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": deepcopy(route or {}),
        "assets": deepcopy(assets or {}),
        "params": deepcopy(params or {}),
        "outputs": deepcopy(outputs or {}),
        "workflow_summary": workflow_summary or build_workflow_summary(params or {}),
        "assistant_summary": assistant_summary or build_assistant_summary(params or {}, assets or {}),
        "replay_payload": deepcopy(replay_payload or {}),
        "replay_readiness": validate_replay_payload(replay_payload or {}, require_source=False),
    }


def memory_event_shape() -> dict[str, Any]:
    return build_image_upscale_memory_event()
