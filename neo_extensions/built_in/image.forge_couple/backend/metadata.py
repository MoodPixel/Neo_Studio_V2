from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .advanced import mapping_covers_canvas, normalize_mapping
from .constants import CONTRACT, EXTENSION_ID, EXTENSION_NAME, PHASE, VERSION
from .mask import normalize_mask_mapping, redact_mask_mapping
from .payload_schema import normalize_block


def build_output_metadata(raw: Any, *, runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    block = normalize_block(raw)
    params = deepcopy(block["params"])
    runtime = dict(runtime or {})
    enabled = bool(block.get("enabled"))
    mode = str(params.get("mode") or "Basic")
    masks = normalize_mask_mapping(params.get("mask_mapping"))
    if masks:
        params["mask_mapping"] = redact_mask_mapping(masks)
    if not enabled:
        summary = "ForgeCouple was disabled."
    elif mode == "Advanced":
        mapping = normalize_mapping(params.get("advanced_mapping"))
        summary = f"ForgeCouple Advanced applied: {len(mapping)} mapped regions; full-canvas coverage {'verified' if mapping_covers_canvas(mapping) else 'not verified'}."
    elif mode == "Mask":
        summary = f"ForgeCouple Mask applied: {len(masks)} binary mask layer(s), global effect {str(params.get('background') or 'None').lower()}."
    else:
        summary = (
            f"ForgeCouple Basic applied: {params['direction'].lower()} regions, "
            f"global effect {params['background'].lower()}, separator "
            f"{'newline' if not params['separator'] else repr(params['separator'])}."
        )
    if enabled and params.get("tile_enabled"):
        summary += f" Tile Mode staged {params.get('tile_columns')}×{params.get('tile_rows')} tiles at threshold {params.get('tile_threshold')}."
    replay_block = deepcopy(block)
    replay_block["enabled"] = False
    replay_block["params"]["tile_enabled"] = False
    replay_block["params"]["mask_mapping"] = []
    replay_block.setdefault("metadata", {})["mask_reupload_required"] = bool(masks)
    return {
        "extension_id": EXTENSION_ID,
        "name": EXTENSION_NAME,
        "version": VERSION,
        "phase": PHASE,
        "contract": CONTRACT,
        "enabled": enabled,
        "params": params,
        "runtime": runtime,
        "assistant_summary": summary,
        "replay_payload": {"extensions": {EXTENSION_ID: replay_block}},
    }
