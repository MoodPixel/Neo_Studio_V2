"""Wildcards payload schema for Phase F.

Phase F makes the frontend submit a canonical ``extensions.payloads.wildcards``
block with image jobs. Runtime seeded prompt resolution still starts in Phase G,
so this module focuses on safe normalization and diagnostics only.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

EXTENSION_ID = "wildcards"
PAYLOAD_VERSION = 1
DEFAULT_MAX_PASSES = 24
DEFAULT_WILDCARDS_PAYLOAD: dict[str, Any] = {
    "enabled": True,
    "version": PAYLOAD_VERSION,
    "inputs": {
        "root": "",
        "selected_token": "",
        "target": "positive_prompt",
        "source_positive": "",
        "source_negative": "",
    },
    "params": {
        "auto_resolve": True,
        "use_seed": True,
        "preview_count": 3,
        "queue_count": 3,
        "variant_offset": 0,
        "max_passes": DEFAULT_MAX_PASSES,
    },
    "assets": {
        "tokens": [],
        "token_labels": [],
    },
    "metadata": {
        "source": "image.generations.prompt.wildcards",
        "prompt_only": True,
        "resolution_policy": "seeded_replace_tokens",
        "resolution_order": "before_style_stack",
        "payload_submit_status": "implemented_phase_f",
        "runtime_resolution_status": "applied_phase_g",
    },
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if value is None:
        return default
    return bool(value)


def _as_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(number, max_value))


def _clean_token(value: Any) -> str:
    token = str(value or "").strip()
    if token.startswith("__") and token.endswith("__") and len(token) > 4:
        token = token[2:-2]
    return token.strip().strip("/")


def _token_label(token: str) -> str:
    clean = _clean_token(token)
    return f"__{clean}__" if clean else ""


def default_wildcards_payload() -> dict[str, Any]:
    """Return a copy of the canonical Phase F payload."""

    return deepcopy(DEFAULT_WILDCARDS_PAYLOAD)


def normalize_wildcards_payload(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize a Phase F frontend payload without resolving prompt text."""

    source = dict(payload or {})
    inputs = source.get("inputs") if isinstance(source.get("inputs"), Mapping) else {}
    params = source.get("params") if isinstance(source.get("params"), Mapping) else {}
    assets = source.get("assets") if isinstance(source.get("assets"), Mapping) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}

    raw_tokens = assets.get("tokens") if isinstance(assets.get("tokens"), list) else []
    tokens: list[str] = []
    for item in [inputs.get("selected_token"), *raw_tokens]:
        token = _clean_token(item)
        if token and token not in tokens:
            tokens.append(token)

    target = str(inputs.get("target") or "positive_prompt")
    if target == "negative":
        target = "negative_prompt"
    if target not in {"positive_prompt", "negative_prompt"}:
        target = "positive_prompt"

    normalized = default_wildcards_payload()
    normalized["enabled"] = _as_bool(source.get("enabled"), True)
    normalized["version"] = PAYLOAD_VERSION
    normalized["inputs"] = {
        "root": str(inputs.get("root") or ""),
        "selected_token": _clean_token(inputs.get("selected_token")),
        "target": target,
        "source_positive": str(inputs.get("source_positive") or ""),
        "source_negative": str(inputs.get("source_negative") or ""),
    }
    normalized["params"] = {
        "auto_resolve": _as_bool(params.get("auto_resolve"), True),
        "use_seed": _as_bool(params.get("use_seed"), True),
        "preview_count": _as_int(params.get("preview_count"), 3, 1, 10),
        "queue_count": _as_int(params.get("queue_count"), 3, 1, 50),
        "variant_offset": _as_int(params.get("variant_offset"), 0, 0, 1000000),
        "max_passes": _as_int(params.get("max_passes"), DEFAULT_MAX_PASSES, 1, 48),
    }
    normalized["assets"] = {
        "tokens": tokens,
        "token_labels": [_token_label(token) for token in tokens],
    }
    normalized["metadata"] = {
        **DEFAULT_WILDCARDS_PAYLOAD["metadata"],
        **dict(metadata),
        "prompt_only": True,
        "payload_submit_status": "implemented_phase_f",
        "runtime_resolution_status": "applied_phase_g",
        "payload_schema": "neo_extensions.built_in.image.wildcards.backend.payload_schema:normalize_wildcards_payload",
    }
    return normalized


def phase_f_payload_status(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return Phase F diagnostics for tests and frontend payload previews."""

    normalized = normalize_wildcards_payload(payload)
    return {
        "extension_id": EXTENSION_ID,
        "phase": "G-backend-resolver-hook",
        "canonical_key": "extensions.payloads.wildcards",
        "enabled": bool(normalized.get("enabled")),
        "payload_version": normalized.get("version"),
        "tokens": list(normalized.get("assets", {}).get("tokens", [])),
        "prompt_only": True,
        "runtime_resolution": True,
        "payload_submit_status": "implemented_phase_f",
        "next_phase": "H-prompt-extension-order",
    }


# Backward-compatible alias retained for earlier phase tests.
def phase_b_payload_status(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status = phase_f_payload_status(payload)
    status["phase_b_compat"] = True
    status["runtime_resolution"] = False
    return status
