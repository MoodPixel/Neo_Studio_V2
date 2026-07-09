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
        "triggers": [],
        "keywords": [],
        "negative_keywords": [],
        "default_strength": 0.8,
        "min_strength": 0.6,
        "max_strength": 1.0,
        "base_model": "",
        "style_category": "",
        "notes": "",
        "caution_notes": "",
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
    for key in ("triggers", "keywords", "negative_keywords", "preview_images", "preview_urls", "catalog_match_keys"):
        base[key] = _clean_list(base.get(key))
    base["prompt_options"] = normalize_prompt_options(base.get("prompt_options"))
    if not isinstance(base.get("remote_source"), dict):
        base["remote_source"] = {}
    if not isinstance(base.get("field_sources"), dict):
        base["field_sources"] = {}
    base["default_strength"] = max(-4.0, min(4.0, _float(base.get("default_strength"), 0.8)))
    base["min_strength"] = max(-4.0, min(4.0, _float(base.get("min_strength"), 0.6)))
    base["max_strength"] = max(-4.0, min(4.0, _float(base.get("max_strength"), 1.0)))
    if base["min_strength"] > base["max_strength"]:
        base["min_strength"], base["max_strength"] = base["max_strength"], base["min_strength"]
    base["enabled"] = base.get("enabled") is not False
    now = utc_now_iso()
    base["created"] = str(base.get("created") or now)
    base["updated"] = str(base.get("updated") or now)
    if not base.get("preview_image") and base["preview_images"]:
        base["preview_image"] = base["preview_images"][0]
    return base


def record_from_comfy_lora_name(name: str) -> dict[str, Any]:
    text = str(name or "").strip()
    record = empty_lora_record(stable_record_id(f"comfy:{text}"))
    record.update({
        "name": text,
        "catalog_name": text,
        "file": text,
        "source": "comfy_lora_loader",
        "category": "from Comfy",
        "base_model": "Base unknown",
        "notes": "Loaded from Comfy LoraLoader.lora_name. Use CivitAI Pull to enrich triggers, prompts, and previews.",
        "metadata_status": "catalog_only",
        "field_sources": {"name": "comfy:LoraLoader.lora_name", "catalog_name": "comfy:LoraLoader.lora_name"},
    })
    return normalize_record(record)
