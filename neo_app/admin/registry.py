from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import json

ADMIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ADMIN_DIR / "admin_config.json"


@lru_cache(maxsize=1)
def get_admin_config() -> dict[str, Any]:
    """Load the V2 Admin control tower config."""
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get_admin_payload() -> dict[str, Any]:
    config = get_admin_config()
    return {
        "admin_version": config.get("admin_version"),
        "global": config.get("global", {}),
        "surfaces": config.get("surfaces", {}),
        "extension_controls": config.get("extension_controls", {}),
        "provider_controls": config.get("provider_controls", {}),
    }


def get_surface_admin(surface_id: str) -> dict[str, Any] | None:
    config = get_admin_config()
    surface = config.get("surfaces", {}).get(surface_id)
    if surface is None:
        return None
    return {"surface_id": surface_id, **surface}
