from __future__ import annotations

import secrets
from typing import Any

MAX_SEED = 2_147_483_647


def coerce_seed(value: Any, *, default: int = -1) -> int:
    """Return an integer seed sentinel/seed value from UI/provider input."""
    try:
        return int(value)
    except Exception:
        return default


def generate_seed() -> int:
    """Generate a backend-safe positive seed value.

    ComfyUI accepts larger values for some samplers, but using a 31-bit value keeps
    the core contract portable across Comfy/A1111/Forge-style backends.
    """
    return secrets.randbelow(MAX_SEED) + 1


def normalize_image_seed_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve random seed before a provider queues the job.

    V1 behaved reliably because random seeds were made concrete before execution.
    V2 now mirrors that: the UI may request seed=-1, but core stores both the
    request sentinel and the actual seed before provider compile/run/persistence.
    """
    normalized = dict(params or {})
    raw_seed = normalized.get("seed", -1)
    requested_seed = normalized.get("requested_seed", raw_seed)
    requested_seed_int = coerce_seed(requested_seed, default=coerce_seed(raw_seed, default=-1))
    seed_int = coerce_seed(raw_seed, default=requested_seed_int)

    if requested_seed_int < 0 or seed_int < 0:
        actual_seed = generate_seed()
        normalized["requested_seed"] = requested_seed_int
        normalized["seed"] = actual_seed
        normalized["actual_seed"] = actual_seed
        normalized["seed_mode"] = "random_resolved"
    else:
        normalized["requested_seed"] = requested_seed_int
        normalized["seed"] = seed_int
        normalized["actual_seed"] = seed_int
        normalized["seed_mode"] = "fixed"
    return normalized
