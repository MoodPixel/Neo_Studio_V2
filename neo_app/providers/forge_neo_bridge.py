from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

FORGE_BRIDGE_PROTOCOL_VERSION = "1.0"
FORGE_BRIDGE_SCHEMA_ID = "neo.provider.forge_bridge.v1"
FORGE_BRIDGE_MODES = {"auto", "standard", "required"}


def forge_bridge_mode(profile: dict[str, Any] | None) -> str:
    profile = profile if isinstance(profile, dict) else {}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    value = str(connection.get("bridge_mode") or "auto").strip().casefold()
    return value if value in FORGE_BRIDGE_MODES else "auto"


def forge_bridge_token(profile: dict[str, Any] | None) -> str:
    profile = profile if isinstance(profile, dict) else {}
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    env_name = str(connection.get("bridge_token_env") or "NEO_FORGE_BRIDGE_TOKEN").strip()
    return str(os.getenv(env_name) or "").strip() if env_name else ""


def forge_bridge_headers(profile: dict[str, Any] | None) -> dict[str, str]:
    token = forge_bridge_token(profile)
    return {"X-Neo-Bridge-Token": token} if token else {}


@dataclass(frozen=True, slots=True)
class ForgeBridgeDecision:
    mode: str
    available: bool
    required: bool
    use_bridge: bool
    fallback_allowed: bool
    message: str
    handshake: dict[str, Any]


def decide_forge_bridge(
    profile: dict[str, Any] | None,
    *,
    handshake: dict[str, Any] | None = None,
) -> ForgeBridgeDecision:
    mode = forge_bridge_mode(profile)
    payload = handshake if isinstance(handshake, dict) else {}
    available = bool(payload.get("ok") and str(payload.get("protocol_version") or "").split(".", 1)[0] == FORGE_BRIDGE_PROTOCOL_VERSION.split(".", 1)[0])
    required = mode == "required"
    use_bridge = available and mode != "standard"
    fallback_allowed = mode != "required"
    if mode == "standard":
        message = "Forge Bridge is disabled for this profile; standard SDAPI lifecycle is selected."
    elif available:
        message = "Forge Bridge is available and selected for job-specific lifecycle tracking."
    elif required:
        message = "Forge Bridge is required by this profile but was not detected."
    else:
        message = "Forge Bridge was not detected; Neo will use the standard SDAPI lifecycle."
    return ForgeBridgeDecision(
        mode=mode,
        available=available,
        required=required,
        use_bridge=use_bridge,
        fallback_allowed=fallback_allowed,
        message=message,
        handshake=payload,
    )


def bridge_snapshot_payload(
    profile: dict[str, Any] | None,
    *,
    handshake: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    settings_schema: dict[str, Any] | None = None,
    status: str = "not_installed",
    message: str = "",
    error: str = "",
) -> dict[str, Any]:
    decision = decide_forge_bridge(profile, handshake=handshake)
    handshake = handshake if isinstance(handshake, dict) else {}
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    settings_schema = settings_schema if isinstance(settings_schema, dict) else {}
    return {
        "schema_id": FORGE_BRIDGE_SCHEMA_ID,
        "mode": decision.mode,
        "status": status,
        "available": decision.available,
        "selected": decision.use_bridge,
        "required": decision.required,
        "fallback_allowed": decision.fallback_allowed,
        "message": message or decision.message,
        "error": error,
        "protocol_version": str(handshake.get("protocol_version") or ""),
        "bridge_version": str(handshake.get("bridge_version") or ""),
        "forge_version": str(handshake.get("forge_version") or ""),
        "identity": str(handshake.get("identity") or ""),
        "capabilities": capabilities,
        "settings_schema": settings_schema,
        "endpoints": handshake.get("endpoints") if isinstance(handshake.get("endpoints"), list) else [],
    }
