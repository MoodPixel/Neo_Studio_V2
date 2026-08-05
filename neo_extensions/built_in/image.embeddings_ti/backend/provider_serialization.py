from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KNOWN_SUFFIXES = (".pt", ".safetensors", ".bin")
_COMFY_PREFIX_RE = re.compile(r"^embedding\s*:\s*", re.IGNORECASE)
_WEIGHTED_WRAPPER_RE = re.compile(r"^\(\s*(.*?)\s*:\s*([-+]?\d+(?:\.\d+)?)\s*\)$")


def _format_strength(value: Any, default: float = 1.0) -> str:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        strength = default
    return f"{strength:.4f}".rstrip("0").rstrip(".") or "0"


def _unwrap_weighted(value: str) -> str:
    match = _WEIGHTED_WRAPPER_RE.match(value.strip())
    return match.group(1).strip() if match else value.strip()


def clean_embedding_catalog_name(value: Any) -> str:
    """Return the portable selected-provider catalog name.

    Absolute paths are reduced to their filename. Relative provider catalog names
    remain portable so the library can still show the selected provider's exact
    choice without exposing its model root.
    """

    if isinstance(value, dict):
        value = (
            value.get("catalog_name")
            or value.get("asset_name")
            or value.get("token")
            or value.get("name")
            or value.get("file")
            or value.get("id")
        )
    text = _unwrap_weighted(str(value or "").strip()).replace("\\", "/")
    text = _COMFY_PREFIX_RE.sub("", text).strip()
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        text = Path(text).name
    while text.startswith("./"):
        text = text[2:]
    return text.strip()


def embedding_asset_name(value: Any) -> str:
    """Return the canonical provider-neutral textual-inversion trigger name."""

    catalog_name = clean_embedding_catalog_name(value)
    if not catalog_name:
        return ""
    file_name = catalog_name.split("/")[-1].strip()
    lowered = file_name.casefold()
    for suffix in KNOWN_SUFFIXES:
        if lowered.endswith(suffix):
            file_name = file_name[: -len(suffix)]
            break
    return file_name.strip()


def embedding_identity_keys(value: Any) -> set[str]:
    catalog_name = clean_embedding_catalog_name(value)
    asset_name = embedding_asset_name(value)
    keys: set[str] = set()
    for item in (catalog_name, asset_name):
        text = str(item or "").strip().casefold()
        if not text:
            continue
        keys.add(text)
        keys.add(f"embedding:{text}")
        path = Path(text)
        keys.add(path.name.casefold())
        keys.add(path.stem.casefold())
    return {key for key in keys if key}


def _name_boundary_pattern(name: str, *, prefixed: bool) -> re.Pattern[str]:
    escaped = re.escape(name)
    prefix = r"embedding\s*:\s*" if prefixed else ""
    return re.compile(rf"(?<![A-Za-z0-9_.-]){prefix}{escaped}(?![A-Za-z0-9_.-])", re.IGNORECASE)


def prompt_contains_embedding(prompt: str, value: Any) -> bool:
    """Detect weighted/unweighted Forge and Comfy forms of one embedding.

    The check is identity-specific instead of extracting every plain prompt word,
    which avoids treating unrelated prompt text as an embedding catalog.
    """

    text = str(prompt or "")
    name = embedding_asset_name(value)
    if not text or not name:
        return False
    return bool(_name_boundary_pattern(name, prefixed=True).search(text) or _name_boundary_pattern(name, prefixed=False).search(text))


def render_provider_embedding_token(provider_id: str, value: Any, strength: Any = 1.0) -> str:
    name = embedding_asset_name(value)
    if not name:
        return ""
    provider = str(provider_id or "comfyui").strip().casefold()
    base = name if provider == "forge" else f"embedding:{name}"
    try:
        amount = float(strength)
    except (TypeError, ValueError):
        amount = 1.0
    return base if abs(amount - 1.0) < 1e-6 else f"({base}:{_format_strength(amount)})"


def render_provider_embedding_binding(provider_id: str, value: Any, strength: Any = 1.0) -> dict[str, Any]:
    provider = str(provider_id or "comfyui").strip().casefold()
    catalog_name = clean_embedding_catalog_name(value)
    name = embedding_asset_name(value)
    rendered = render_provider_embedding_token(provider, value, strength)
    try:
        normalized_strength = float(strength)
    except (TypeError, ValueError):
        normalized_strength = 1.0
    return {
        "provider_id": provider,
        "catalog_name": catalog_name,
        "asset_name": name,
        "strength": normalized_strength,
        "rendered_token": rendered,
        "serialization": "plain_trigger_compile_time" if provider == "forge" else "comfy_embedding_prefix_compile_time",
        "prompt_mutation": "compile_time_only",
        "visible_prompt_mutation": False,
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
    }
