from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID
from .payload_schema import normalize_block


def build_replay_payload(raw: Any) -> dict[str, Any]:
    block = normalize_block(raw)
    had_masks = bool(block.get("params", {}).get("mask_mapping"))
    block["enabled"] = False
    block["params"]["tile_enabled"] = False
    block["params"]["mask_mapping"] = []
    block.setdefault("metadata", {})
    block["metadata"].update({
        "revalidation_required": True,
        "restore_policy": "restore_disabled_until_live_forge_couple_schema_route_masks_and_tile_revalidate",
        "mask_reupload_required": had_masks,
        "tile_reenable_required": True,
    })
    return {"extensions": {EXTENSION_ID: deepcopy(block)}}
