from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

LORA_TAG_RE = re.compile(r"<lora:([^:>]+)(?::([^>]+))?>", re.IGNORECASE)
MODEL_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")


def format_strength(value: Any, default: float = 0.8) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    number = max(-4.0, min(4.0, number))
    return f"{number:.4f}".rstrip("0").rstrip(".") or "0"


def clean_lora_catalog_name(value: Any) -> str:
    """Return a portable provider catalog value without absolute path leakage."""

    if isinstance(value, dict):
        value = value.get("catalog_name") or value.get("name") or value.get("file") or value.get("id")
    text = str(value or "").replace("\\", "/").strip()
    match = LORA_TAG_RE.search(text)
    if match:
        text = match.group(1).strip()
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        text = PurePosixPath(text).name
    while text.startswith("./"):
        text = text[2:]
    return text


def forge_lora_name(value: Any) -> str:
    """Render the extra-network name used by Forge's ``<lora:...>`` parser."""

    text = clean_lora_catalog_name(value)
    if not text:
        return ""
    name = PurePosixPath(text).name
    lowered = name.casefold()
    for suffix in MODEL_SUFFIXES:
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def lora_identity_keys(value: Any) -> set[str]:
    """Return path/name/extension-insensitive keys used only for deduplication."""

    text = clean_lora_catalog_name(value)
    if not text:
        return set()
    path = PurePosixPath(text)
    name = path.name
    stem = name
    lowered = name.casefold()
    for suffix in MODEL_SUFFIXES:
        if lowered.endswith(suffix):
            stem = name[: -len(suffix)]
            break
    no_suffix_path = str(path.with_suffix("")) if path.suffix else str(path)
    return {
        item.casefold()
        for item in (text, str(path), name, stem, no_suffix_path, forge_lora_name(text))
        if str(item or "").strip()
    }


def prompt_lora_identity_keys(prompt: str) -> set[str]:
    keys: set[str] = set()
    for match in LORA_TAG_RE.finditer(str(prompt or "")):
        keys.update(lora_identity_keys(match.group(1)))
    return keys


def render_forge_lora_tag(name: Any, strength: Any = 0.8) -> str:
    clean = forge_lora_name(name)
    return f"<lora:{clean}:{format_strength(strength)}>" if clean else ""


def render_provider_lora_binding(
    provider_id: str,
    name: Any,
    strength: Any = 0.8,
    *,
    provider_catalog_name: Any = None,
) -> dict[str, Any]:
    """Describe provider-specific execution without mutating canonical stack state."""

    provider = str(provider_id or "").strip().casefold()
    catalog_name = clean_lora_catalog_name(name)
    exact_provider_name = str(provider_catalog_name or "").strip()
    if provider == "forge":
        rendered = render_forge_lora_tag(catalog_name, strength)
        return {
            "provider_id": "forge",
            "mode": "positive_prompt_extra_network",
            "catalog_name": catalog_name,
            "portable_catalog_name": catalog_name,
            "provider_catalog_name": forge_lora_name(catalog_name),
            "rendered": rendered,
            "prompt_mutation": "compile_time_only",
        }
    if provider in {"comfyui", "comfyui_portable"}:
        return {
            "provider_id": provider,
            "mode": "workflow_loader",
            "catalog_name": catalog_name,
            "portable_catalog_name": catalog_name,
            "provider_catalog_name": exact_provider_name,
            "rendered": exact_provider_name or catalog_name,
            "catalog_binding_required": True,
            "strength_model": float(format_strength(strength)),
            "strength_clip": float(format_strength(strength)),
            "prompt_mutation": "none",
        }
    return {
        "provider_id": provider,
        "mode": "unsupported",
        "catalog_name": catalog_name,
        "portable_catalog_name": catalog_name,
        "provider_catalog_name": exact_provider_name,
        "rendered": "",
        "prompt_mutation": "none",
    }
