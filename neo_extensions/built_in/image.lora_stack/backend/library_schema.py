from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_record_id(value: str) -> str:
    text = str(value or "").strip() or "unknown_lora"
    digest = sha1(text.casefold().encode("utf-8", errors="ignore")).hexdigest()[:16]
    stem = Path(text.replace("\\", "/")).stem or "lora"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)[:64].strip("._-") or "lora"
    return f"{safe}_{digest}"


def normalize_catalog_path(value: Any) -> str:
    """Normalize one LoRA catalog/path value into a portable catalog path.

    Provider-relative folder structure is preserved because it is meaningful to
    Neo's folder filters and because duplicate filenames can legitimately exist
    in different LoRA subfolders. Absolute machine-local paths are collapsed to
    the basename so server filesystem roots never become portable identities.
    """

    if isinstance(value, dict):
        value = (
            value.get("catalog_name")
            or value.get("rel")
            or value.get("file")
            or value.get("name")
            or value.get("id")
        )
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        return Path(text).name
    while text.startswith("./"):
        text = text[2:]
    parts = [part.strip() for part in text.split("/") if part.strip() and part.strip() != "."]
    return "/".join(parts)


def canonical_lora_identity(value: Any, catalog_value: Any | None = None) -> str:
    """Return one durable case-insensitive LoRA identity.

    This helper intentionally supports both LoRA-library generations:

    * ``canonical_lora_identity(catalog_value)`` returns the legacy portable
      path identity (folder-sensitive, provider-neutral).
    * ``canonical_lora_identity(provider_id, catalog_value)`` returns the
      provider-aware durable identity used by the newer library store/catalog
      bridge, for example ``comfyui:people/alex.safetensors``.

    Keeping both call forms is required because recovery/startup compatibility
    code still imports the one-argument contract while newer persistence code
    requires provider namespacing.
    """

    if catalog_value is None:
        return normalize_catalog_path(value).casefold()

    provider = str(value or "").strip().casefold()
    identity = normalize_catalog_path(catalog_value).casefold()
    if provider and identity:
        return f"{provider}:{identity}"
    return identity or provider


def canonical_record_id(value: Any, provider_id: str = "") -> str:
    """Build a stable record id from a portable LoRA identity.

    Newer library-store/catalog-bridge revisions import this helper directly.
    The optional provider namespace prevents identically named assets from two
    providers from colliding while remaining backward compatible with callers
    that pass only the catalog value. A record dict may be passed directly.
    """

    provider = str(provider_id or "").strip().casefold()
    if isinstance(value, dict):
        if not provider:
            provider = str(value.get("provider_id") or "").strip().casefold()
        identity = canonical_lora_identity(value)
    else:
        identity = canonical_lora_identity(value)
    key = f"{provider}:{identity}" if provider and identity else (identity or provider or "unknown_lora")
    return stable_record_id(key)


def _clean_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = values.replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(values, (list, tuple, set)):
        raw = list(values)
    else:
        raw = [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def lora_folder_facets(value: Any) -> tuple[str, str]:
    """Return main/sub folder labels from a portable provider LoRA path.

    Examples:
      Krea2/Characters/hero.safetensors -> (Krea2, Characters)
      Krea2/Styles/Cinematic/foo.safetensors -> (Krea2, Styles / Cinematic)
      hero.safetensors -> (Root, "")

    Absolute backend paths are intentionally collapsed to Root because browser
    records must not expose machine-specific folder structure outside the
    provider-relative LoRA catalog identity.
    """
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return "Root", ""
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        return "Root", ""
    while text.startswith("./"):
        text = text[2:]
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) <= 1:
        return "Root", ""
    main = parts[0]
    sub = " / ".join(parts[1:-1]) if len(parts) > 2 else ""
    return main or "Root", sub


def empty_lora_record(record_id: str = "") -> dict[str, Any]:
    return {
        "id": record_id,
        "kind": "lora",
        "file": "",
        "rel": "",
        "name": "",
        "catalog_name": "",
        "source": "manual",
        "category": "",
        "folder_category": "",
        "folder_subcategory": "",
        "triggers": [],
        "keywords": [],
        "negative_keywords": [],
        "default_strength": 0.8,
        "min_strength": 0.6,
        "max_strength": 1.0,
        "base_model": "",
        "style_category": "",
        "notes": "",
        "user_notes": "",
        "caution_notes": "",
        "civitai_url": "",
        "example_prompt": "",
        "prompt_options": [],
        "preview_image": "",
        "preview_images": [],
        "preview_urls": [],
        "remote_source": {},
        "field_sources": {},
        "metadata_status": "unknown",
        "metadata_resolution": {},
        "catalog_available": False,
        "catalog_source": "",
        "provider_id": "",
        "provider_label": "",
        "provider_catalog_name": "",
        "canonical_identity": "",
        "catalog_match_keys": [],
        "hash": "",
        "enabled": True,
        "created": "",
        "updated": "",
    }


def normalize_prompt_options(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        if isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("text") or "").strip()
            name = str(item.get("name") or item.get("label") or f"Prompt Option {index + 1}").strip()
        else:
            prompt = str(item or "").strip()
            name = f"Prompt Option {index + 1}"
        if not prompt:
            continue
        key = f"{name.casefold()}|{prompt.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name or f"Prompt Option {len(out) + 1}", "prompt": prompt})
    return out


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    record = record or {}
    name = str(record.get("name") or record.get("catalog_name") or record.get("file") or "").strip()
    record_id = str(record.get("id") or "").strip() or stable_record_id(str(record.get("file") or name))
    base = empty_lora_record(record_id)
    for key in base:
        if key in record:
            base[key] = record[key]
    base["id"] = record_id
    base["kind"] = "lora"
    base["name"] = name or Path(str(base.get("file") or "")).name or record_id
    if not base.get("catalog_name"):
        base["catalog_name"] = base["name"]
    facet_source = str(base.get("rel") or base.get("catalog_name") or base.get("name") or "")
    folder_category, folder_subcategory = lora_folder_facets(facet_source)
    base["folder_category"] = folder_category
    base["folder_subcategory"] = folder_subcategory
    base["user_notes"] = str(base.get("user_notes") or "").strip()
    base["civitai_url"] = str(base.get("civitai_url") or "").strip()
    for key in ("triggers", "keywords", "negative_keywords", "preview_images", "preview_urls", "catalog_match_keys"):
        base[key] = _clean_list(base.get(key))
    base["prompt_options"] = normalize_prompt_options(base.get("prompt_options"))
    if not isinstance(base.get("remote_source"), dict):
        base["remote_source"] = {}
    remote_url = str(base.get("remote_source", {}).get("url") or "").strip()
    if not base["civitai_url"] and remote_url:
        base["civitai_url"] = remote_url
    elif base["civitai_url"] and not remote_url:
        base["remote_source"] = {**base.get("remote_source", {}), "url": base["civitai_url"]}
    if not isinstance(base.get("field_sources"), dict):
        base["field_sources"] = {}
    base["default_strength"] = _float(base.get("default_strength"), 0.8)
    base["min_strength"] = _float(base.get("min_strength"), 0.6)
    base["max_strength"] = _float(base.get("max_strength"), 1.0)
    if base["min_strength"] > base["max_strength"]:
        base["min_strength"], base["max_strength"] = base["max_strength"], base["min_strength"]
    base["provider_id"] = str(base.get("provider_id") or "").strip().casefold()
    base["provider_label"] = str(base.get("provider_label") or "").strip()
    provider_catalog_name = str(base.get("provider_catalog_name") or "").strip()
    if not provider_catalog_name:
        provider_catalog_name = str(base.get("catalog_name") or base.get("name") or "").strip()
    base["provider_catalog_name"] = provider_catalog_name
    portable_identity = canonical_lora_identity(base.get("catalog_name") or base.get("name") or base.get("file") or "")
    base["canonical_identity"] = (
        canonical_lora_identity(base["provider_id"], base.get("catalog_name") or base.get("name") or base.get("file") or "")
        if base["provider_id"]
        else portable_identity
    )
    base["enabled"] = base.get("enabled") is not False
    now = utc_now_iso()
    base["created"] = str(base.get("created") or now)
    base["updated"] = str(base.get("updated") or now)
    if not base.get("preview_image") and base["preview_images"]:
        base["preview_image"] = base["preview_images"][0]
    return base


def record_from_provider_lora_name(
    name: str,
    *,
    provider_id: str = "comfyui",
    catalog_source: str = "",
    provider_label: str = "",
) -> dict[str, Any]:
    """Build a path-free LoRA library record from a provider catalog name.

    The provider catalog value remains the canonical loader/tag value. Neo stores
    only the portable name and provider metadata; absolute backend paths are never
    copied into the browser-facing record.
    """

    text = str(name or "").replace("\\", "/").strip()
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":/"):
        text = Path(text).name
    while text.startswith("./"):
        text = text[2:]
    provider = str(provider_id or "comfyui").strip().casefold()
    source = str(catalog_source or "").strip()
    if not source:
        source = "forge:extra_network_lora" if provider == "forge" else "comfy:LoraLoader.lora_name"
    label = str(provider_label or "").strip() or ("Forge Neo" if provider == "forge" else "ComfyUI")
    source_id = "forge_lora_catalog" if provider == "forge" else "comfy_lora_loader"
    record = empty_lora_record(stable_record_id(f"{provider}:{text}"))
    record.update({
        "name": text,
        "catalog_name": text,
        "file": text,
        "source": source_id,
        "category": f"from {label}",
        "base_model": "Base unknown",
        "notes": f"Loaded from the selected {label} LoRA catalog. Use CivitAI Pull to enrich triggers, prompts, and previews.",
        "metadata_status": "catalog_only",
        "catalog_available": True,
        "catalog_source": source,
        "provider_id": provider,
        "provider_label": label,
        "field_sources": {"name": source, "catalog_name": source},
    })
    return normalize_record(record)


def record_from_comfy_lora_name(name: str) -> dict[str, Any]:
    """Backward-compatible Comfy record constructor."""

    return record_from_provider_lora_name(name, provider_id="comfyui")


def browser_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-facing record without backend filesystem locations."""

    normalized = normalize_record(record)
    file_value = str(normalized.get("file") or "").replace("\\", "/").strip()
    catalog_name = str(normalized.get("catalog_name") or normalized.get("name") or "").replace("\\", "/").strip()
    if catalog_name and (catalog_name.startswith("/") or (len(catalog_name) > 2 and catalog_name[1:3] == ":/")):
        catalog_name = Path(catalog_name).name
        normalized["catalog_name"] = catalog_name
        normalized["name"] = catalog_name
    if file_value:
        path = Path(file_value)
        if path.is_absolute() or ":/" in file_value:
            normalized["file"] = catalog_name or path.name
    resolution = normalized.get("metadata_resolution") if isinstance(normalized.get("metadata_resolution"), dict) else {}
    normalized["metadata_resolution"] = {
        "ok": bool(resolution.get("ok")),
        "source": str(resolution.get("source") or ""),
        "status": str(resolution.get("status") or normalized.get("metadata_status") or ""),
        "path_policy": "absolute_paths_server_side_only",
    }
    remote_resolution = normalized.get("remote_metadata_resolution")
    if isinstance(remote_resolution, dict):
        normalized["remote_metadata_resolution"] = {
            "ok": bool(remote_resolution.get("ok")),
            "source": str(remote_resolution.get("source") or ""),
            "error": str(remote_resolution.get("error") or ""),
        }
    return normalized
