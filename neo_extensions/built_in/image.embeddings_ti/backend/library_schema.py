from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from .provider_serialization import clean_embedding_catalog_name, embedding_asset_name

SUPPORTED_EXTENSIONS = {".pt", ".safetensors", ".bin"}
SCHEMA_VERSION = "neo.embeddings_ti.library.v3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_record_id(value: str) -> str:
    text = str(value or "").strip() or "unknown_embedding"
    digest = sha1(text.casefold().encode("utf-8", errors="ignore")).hexdigest()[:16]
    stem = Path(text.replace("\\", "/")).stem or "embedding"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)[:64].strip("._-") or "embedding"
    return f"{safe}_{digest}"


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


def token_for_name(name: str) -> str:
    return embedding_asset_name(name)


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


def empty_embedding_record(record_id: str = "") -> dict[str, Any]:
    return {
        "id": record_id,
        "kind": "embedding_ti",
        "file": "",
        "rel": "",
        "name": "",
        "catalog_name": "",
        "token": "",
        "source": "manual",
        "provider_id": "",
        "provider_label": "",
        "category": "",
        "base_model": "unknown",
        "trigger_words": [],
        "keywords": [],
        "negative_keywords": [],
        "notes": "",
        "caution_notes": "",
        "example_prompt": "",
        "prompt_options": [],
        "default_target": "negative_prompt",
        "default_strength": 1.0,
        "preview_image": "",
        "preview_images": [],
        "preview_urls": [],
        "remote_source": {},
        "civitai_url": "",
        "field_sources": {},
        "metadata_status": "unknown",
        "metadata_resolution": {},
        "catalog_available": False,
        "catalog_source": "",
        "catalog_match_keys": [],
        "hash": "",
        "enabled": True,
        "created": "",
        "updated": "",
    }


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    record = record or {}
    file_path = str(record.get("file") or record.get("path") or "").strip()
    name = str(record.get("name") or record.get("catalog_name") or Path(file_path).stem or record.get("id") or "").strip()
    token = token_for_name(record.get("asset_name") or record.get("token") or name or file_path)
    record_id = str(record.get("id") or "").strip() or stable_record_id(file_path or token or name)
    base = empty_embedding_record(record_id)
    for key in base:
        if key in record:
            base[key] = record[key]
    base["id"] = record_id
    base["kind"] = "embedding_ti"
    base["file"] = file_path or str(base.get("file") or "")
    base["name"] = name or Path(str(base.get("file") or "")).stem or token or record_id
    base["catalog_name"] = clean_embedding_catalog_name(base.get("catalog_name") or base.get("file") or base["name"]) or base["name"]
    base["token"] = token or token_for_name(base["name"])
    base["asset_name"] = base["token"]
    for key in ("trigger_words", "keywords", "negative_keywords", "preview_images", "preview_urls", "catalog_match_keys"):
        base[key] = _clean_list(base.get(key))
    base["prompt_options"] = normalize_prompt_options(base.get("prompt_options"))
    for key in ("remote_source", "field_sources", "metadata_resolution"):
        if not isinstance(base.get(key), dict):
            base[key] = {}
    base["default_strength"] = max(0.0, min(2.0, _float(base.get("default_strength"), 1.0)))
    if base.get("default_target") not in {"positive_prompt", "negative_prompt", "both", "finish_positive", "finish_negative"}:
        # Negative embeddings are common; keep this as the safe default.
        base["default_target"] = "negative_prompt"
    base["enabled"] = base.get("enabled") is not False
    now = utc_now_iso()
    base["created"] = str(base.get("created") or now)
    base["updated"] = str(base.get("updated") or now)
    if not base.get("preview_image") and base.get("preview_images"):
        base["preview_image"] = base["preview_images"][0]
    if not base.get("example_prompt") and base.get("trigger_words"):
        base["example_prompt"] = ", ".join(base["trigger_words"][:6])
    return base


def record_from_path(path: str | Path, *, root: str | Path | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Path(path)
    rel = ""
    if root:
        try:
            rel = str(p.relative_to(Path(root)))
        except ValueError:
            rel = p.name
    record = {
        "id": stable_record_id(str(p)),
        "name": p.stem,
        "catalog_name": rel.replace("\\", "/") if rel else p.name,
        "token": token_for_name(p.stem),
        "file": str(p),
        "rel": rel,
        "source": "local_scan",
        "provider_label": "local",
        "metadata_status": "scanned",
        "field_sources": {"name": "local:filename", "token": "local:filename", "file": "local:scan"},
    }
    if metadata:
        record.update({key: value for key, value in metadata.items() if value not in (None, "", [])})
    return normalize_record(record)


def record_from_catalog_name(
    name: str,
    *,
    provider_id: str = "comfyui",
    provider_label: str = "",
    catalog_source: str = "",
) -> dict[str, Any]:
    text = clean_embedding_catalog_name(name)
    provider = str(provider_id or "comfyui").strip().casefold()
    label = str(provider_label or "").strip() or ("Forge Neo" if provider == "forge" else "ComfyUI")
    source = str(catalog_source or "").strip() or ("forge:sdapi_embeddings" if provider == "forge" else "comfy:embeddings")
    record = empty_embedding_record(stable_record_id(f"{provider}_embedding:{text}"))
    record.update({
        "name": embedding_asset_name(text) or text,
        "catalog_name": text,
        "token": token_for_name(text),
        "file": text,
        "source": "provider_embedding_catalog",
        "provider_id": provider,
        "provider_label": label,
        "category": f"from {label}",
        "base_model": "unknown",
        "notes": f"Loaded from the selected {label} embedding catalog. Use CivitAI Pull or Save metadata to enrich this record.",
        "metadata_status": "catalog_only",
        "catalog_available": True,
        "catalog_source": source,
        "field_sources": {"name": source, "catalog_name": source, "token": source},
    })
    return normalize_record(record)


def browser_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a browser payload without backend model roots or local paths."""

    normalized = normalize_record(record)
    catalog_name = clean_embedding_catalog_name(normalized.get("catalog_name") or normalized.get("file") or normalized.get("name"))
    file_value = str(normalized.get("file") or "").replace("\\", "/")
    if file_value.startswith("/") or (len(file_value) > 2 and file_value[1:3] == ":/"):
        normalized["file"] = catalog_name or Path(file_value).name
    elif file_value:
        normalized["file"] = clean_embedding_catalog_name(file_value) or catalog_name
    normalized["catalog_name"] = catalog_name or normalized.get("name") or normalized.get("token") or ""
    normalized["token"] = token_for_name(normalized.get("token") or normalized.get("name") or normalized["catalog_name"])
    normalized["asset_name"] = normalized["token"]
    normalized["rel"] = str(normalized.get("rel") or "").replace("\\", "/")
    resolution = normalized.get("metadata_resolution") if isinstance(normalized.get("metadata_resolution"), dict) else {}
    normalized["metadata_resolution"] = {
        "ok": bool(resolution.get("ok")),
        "source": str(resolution.get("source") or ""),
        "status": str(resolution.get("status") or normalized.get("metadata_status") or ""),
        "path_policy": "absolute_paths_server_side_only",
    }
    return normalized
