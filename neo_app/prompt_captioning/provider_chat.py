from __future__ import annotations

from typing import Any

from .providers_comfy_llamacpp import run_chat as run_comfy_llamacpp_chat
from .providers_koboldcpp import run_chat as run_koboldcpp_chat


def run_chat(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_id = str((profile or {}).get("provider_id") or "").strip()
    if provider_id == "comfy_llamacpp":
        return run_comfy_llamacpp_chat(profile, messages, params)
    return run_koboldcpp_chat(profile, messages, params)


__all__ = ["run_chat"]
