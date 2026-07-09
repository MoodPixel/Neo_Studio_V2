from __future__ import annotations

from typing import Any

from .metadata import replay_payload_from_block

def replay_payload(payload: dict[str, Any]) -> dict[str, Any]:
    block = (payload.get("extensions") or {}).get("lora_stack")
    if not block:
        return {"extensions": {}}
    replay = replay_payload_from_block(block)
    return {"extensions": {"lora_stack": replay["payload"]}}
