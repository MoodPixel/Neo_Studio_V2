from __future__ import annotations

from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.wan22.unet_speed_lora.v1"
FAMILY_TOKENS: Final[tuple[str, ...]] = ("wan", "wan2.1", "wan2.2", "wan21", "wan22")
SPEED_TOKENS: Final[tuple[str, ...]] = (
    "lightx2v", "lightning", "turbo", "accelerator", "4step", "4steps", "8step", "8steps", "fast"
)


def is_wan_speed_lora_name(name: str) -> bool:
    text = str(name or "").casefold().replace("_", "-").replace(" ", "-")
    family = any(token.replace(".", "").replace("_", "-") in text.replace(".", "") for token in FAMILY_TOKENS)
    speed = any(token.replace("_", "-") in text for token in SPEED_TOKENS)
    return bool(family and speed)


def wan_speed_lora_candidates(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = str(value or "").strip()
        key = name.casefold()
        if name and key not in seen and is_wan_speed_lora_name(name):
            seen.add(key)
            result.append(name)
    return result


def wan_unet_speed_advisories(rows: list[dict[str, Any]], parameters: dict[str, Any] | None) -> list[str]:
    speed = [row for row in rows if row.get("role") == "speed"]
    if not speed:
        return []
    params = parameters if isinstance(parameters, dict) else {}
    steps = int(params.get("steps") or 0)
    warnings: list[str] = []
    if steps > 8:
        warnings.append("WAN UNET speed/LightX2V LoRA is active above 8 steps. Neo preserves the user value; verify the LoRA author's intended step recipe.")
    if any(not is_wan_speed_lora_name(str(row.get("name") or "")) for row in speed):
        warnings.append("A manually assigned WAN speed row does not match Neo's filename classifier. Manual role selection is allowed; confirm the LoRA is an accelerator.")
    return warnings
