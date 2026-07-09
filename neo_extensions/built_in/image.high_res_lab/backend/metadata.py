from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_TYPE, WORKSPACE_APP
from .replay import replay_payload


def _block_from_validation(validation_result: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    block = validation_result.get("block") if isinstance(validation_result.get("block"), dict) else {}
    return deepcopy(block)


def _param_chip_value(params: dict[str, Any], key: str, fallback: str = "") -> str:
    value = params.get(key)
    if value in (None, ""):
        return fallback
    return str(value)


def assistant_summary_from_payload(block: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None) -> str:
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    if not block.get("enabled"):
        reason = metadata.get("reason") or patch.get("reason") or "disabled"
        return f"Image · High-Res Lab disabled/gated: {reason}."
    mode = _param_chip_value(params, "mode", "latent")
    strategy = _param_chip_value(params, "strategy", "standard")
    scale = _param_chip_value(params, "scale", "")
    denoise = _param_chip_value(params, "denoise", "")
    steps = _param_chip_value(params, "steps", "")
    cfg = _param_chip_value(params, "cfg", "")
    tile = " with tiled VAE" if params.get("tiled_vae") else ""
    if patch.get("applied"):
        target = ""
        if patch.get("target_width") and patch.get("target_height"):
            target = f" → {patch.get('target_width')}×{patch.get('target_height')}"
        return f"Image · High-Res Lab applied {strategy}/{mode} high-res refine at {scale}x{target} with {steps} steps, denoise {denoise}, CFG {cfg}{tile}."
    return f"Image · High-Res Lab validated but did not mutate the workflow ({strategy}/{mode}, {scale}x, denoise {denoise})."


def inspector_summary_from_payload(block: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None, validation_result: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    node_status = validation_result.get("node_status") if isinstance(validation_result.get("node_status"), dict) else patch.get("node_status") if isinstance(patch.get("node_status"), dict) else {}
    applied = bool(patch.get("applied") or patch.get("mutated"))
    status = "Applied" if block.get("enabled") and applied else "Validated" if block.get("enabled") else "Disabled / gated"
    reason = metadata.get("reason") or patch.get("reason") or validation_result.get("reason") or ""
    chips = [
        f"Status · {status}",
        f"Strategy · {_param_chip_value(params, 'strategy', 'standard')}",
        f"Mode · {_param_chip_value(params, 'mode', 'latent')}",
        f"Scale · {_param_chip_value(params, 'scale', '—')}x",
        f"Steps · {_param_chip_value(params, 'steps', '—')}",
        f"Denoise · {_param_chip_value(params, 'denoise', '—')}",
        f"CFG · {_param_chip_value(params, 'cfg', '—')}",
    ]
    if params.get("tiled_vae") is not None:
        chips.append(f"Tiled VAE · {'on' if params.get('tiled_vae') else 'off'}")
    if params.get("upscaler"):
        chips.append(f"Upscaler · {params.get('upscaler')}")
    if patch.get("target_width") and patch.get("target_height"):
        chips.append(f"Target · {patch.get('target_width')}×{patch.get('target_height')}")
    if applied:
        chips.append("Workflow patched")
    if node_status.get("ready") is not None:
        chips.append(f"Node readiness · {'ready' if node_status.get('ready') else 'blocked'}")
    return {
        "title": "Image · High-Res Lab",
        "status": status,
        "chips": chips,
        "reason": reason,
        "assistant_summary": assistant_summary_from_payload(block, workflow_patch=patch),
    }


def memory_readiness_shape(
    *,
    route: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    replay_payload: dict[str, Any] | None = None,
    workflow_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": deepcopy(route or {}),
        "assets": deepcopy(assets or {}),
        "params": deepcopy(params or {}),
        "outputs": deepcopy(outputs or {}),
        "workflow_summary": "High-Res Lab second-pass refine workflow patch applied." if patch.get("applied") else "High-Res Lab did not mutate workflow.",
        "assistant_summary": assistant_summary_from_payload({"enabled": bool(params), "params": params or {}}, workflow_patch=patch),
        "replay_payload": deepcopy(replay_payload or {}),
    }


def build_output_extension_metadata(validation_result: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    block = _block_from_validation(validation_result)
    params = deepcopy(block.get("params") or {}) if isinstance(block.get("params"), dict) else {}
    assets = deepcopy(block.get("assets") or {}) if isinstance(block.get("assets"), dict) else {}
    patch = deepcopy(workflow_patch or {})
    applied = bool(patch.get("applied"))
    summary_text = assistant_summary_from_payload(block, workflow_patch=patch)
    usage = {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "label": "Image · High-Res Lab",
        "workspace_app": WORKSPACE_APP,
        "enabled": bool(block.get("enabled")),
        "status": "applied" if applied else "validated" if block.get("enabled") else "disabled_gated",
        "workflow_patch_applied": applied,
        "route": deepcopy(route or {}),
        "route_state": ((route or {}).get("route_state") or ((block.get("metadata") or {}).get("route_state") if isinstance(block.get("metadata"), dict) else "")),
        "params": params,
        "assets": assets,
        "high_res_mode": params.get("mode"),
        "scale": params.get("scale"),
        "steps": params.get("steps"),
        "denoise": params.get("denoise"),
        "cfg": params.get("cfg"),
        "tiled_vae": params.get("tiled_vae"),
        "upscaler": params.get("upscaler"),
        "strategy": params.get("strategy"),
        "target_width": patch.get("target_width"),
        "target_height": patch.get("target_height"),
        "target_size_source": patch.get("target_size_source"),
        "preview_source_only": patch.get("preview_source_only"),
        "node_status": deepcopy(validation_result.get("node_status") or {}),
        "optional_capabilities": deepcopy((validation_result.get("node_status") or {}).get("optional_capabilities") or {}),
        "assistant_summary": summary_text,
    }
    if patch.get("reason"):
        usage["reason"] = patch.get("reason")
    replay_event = replay_payload(block, route=route)
    safe_replay = deepcopy((replay_event.get("replay_payload") or {}).get("extensions", {}).get(EXTENSION_ID, {}))
    memory_event = memory_readiness_shape(route=route, params=params, assets=assets, replay_payload={"extensions": {EXTENSION_ID: safe_replay}}, workflow_patch=patch)
    return {
        "used": [usage] if block else [],
        "payloads": {EXTENSION_ID: block},
        "workflow_patches": [patch] if patch else [],
        "validation": {EXTENSION_ID: deepcopy(validation_result)},
        "replay_payloads": {EXTENSION_ID: safe_replay},
        "assistant_summary": summary_text,
        "assistant_summaries": {EXTENSION_ID: summary_text},
        "inspector": {EXTENSION_ID: inspector_summary_from_payload(block, workflow_patch=patch, validation_result=validation_result)},
        "memory_events": {EXTENSION_ID: memory_event},
    }


def output_metadata_preview(validation_result: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = build_output_extension_metadata(validation_result, workflow_patch={"applied": False, "phase": "M"}, route=route)
    return {"extensions": metadata}
