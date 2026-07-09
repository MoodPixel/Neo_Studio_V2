from __future__ import annotations

from copy import deepcopy
from typing import Any

from .field_sources import merge_field_sources
from .prompt_options import add_civitai_prompt_option

MERGE_MODES = {"fill_missing", "smart_merge", "overwrite_selected", "previews_only"}
LIST_FIELDS = {"triggers", "keywords", "negative_keywords", "preview_images", "preview_urls", "prompt_options"}
SCALAR_FIELDS = {"base_model", "category", "style_category", "notes", "caution_notes", "example_prompt", "preview_image", "default_strength", "min_strength", "max_strength"}


def _unique(values: list[Any]) -> list[str]:
    seen: list[str] = []
    keys: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in keys:
            continue
        keys.add(key)
        seen.append(text)
    return seen


def _merge_prompt_options(existing: list[Any], incoming: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in list(existing or []) + list(incoming or []):
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or f"Prompt Option {len(result) + 1}").strip()
        prompt = str(option.get("prompt") or "").strip()
        if not prompt:
            continue
        key = f"{name.casefold()}|{prompt.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "prompt": prompt})
    return result


def _has_value(value: Any) -> bool:
    return value not in (None, "", [])


def merge_record(existing: dict[str, Any], incoming: dict[str, Any], *, mode: str = "fill_missing", selected_fields: list[str] | None = None) -> dict[str, Any]:
    mode = mode if mode in MERGE_MODES else "fill_missing"
    selected = set(selected_fields or [])
    result = deepcopy(existing or {})
    incoming = deepcopy(incoming or {})
    incoming_source = str(((incoming.get("remote_source") or {}).get("provider") if isinstance(incoming.get("remote_source"), dict) else "") or "civitai")
    source_label = f"remote:{incoming_source}"

    if mode == "previews_only":
        previews = _unique((result.get("preview_images") or []) + (incoming.get("preview_images") or []) + (incoming.get("preview_urls") or []))
        result["preview_images"] = previews
        if not result.get("preview_image") and previews:
            result["preview_image"] = previews[0]
        result["field_sources"] = merge_field_sources(result, {"preview_images": previews, "preview_image": result.get("preview_image")}, source_label, ["preview_images", "preview_image"])
        if incoming.get("remote_source"):
            result["remote_source"] = {**(result.get("remote_source") or {}), **incoming.get("remote_source", {})}
        return result

    for key, value in incoming.items():
        if key in {"id", "kind", "field_sources"}:
            continue
        if mode == "overwrite_selected" and key not in selected and key not in {"remote_source"}:
            continue
        if key in {"triggers", "keywords", "negative_keywords", "preview_images", "preview_urls"}:
            current = result.get(key) or []
            if mode == "fill_missing" and current:
                continue
            result[key] = _unique((current if mode == "smart_merge" else []) + (value or [])) if mode in {"smart_merge", "overwrite_selected"} else _unique(value or [])
            continue
        if key == "prompt_options":
            if mode == "fill_missing" and result.get("prompt_options"):
                continue
            result[key] = _merge_prompt_options(result.get(key) if mode == "smart_merge" else [], value or [])
            continue
        if key == "example_prompt" and _has_value(value):
            if mode == "smart_merge" and result.get("example_prompt") and str(result.get("example_prompt")).strip().casefold() != str(value).strip().casefold():
                add_civitai_prompt_option(result, str(value), name="CivitAI Prompt")
                continue
        if key == "notes" and mode == "smart_merge" and result.get("notes") and value and str(value).strip() not in str(result.get("notes")):
            result["notes"] = f"{result['notes']}\n\n{value}"
            continue
        if mode == "fill_missing" and _has_value(result.get(key)):
            continue
        if _has_value(value) or key == "remote_source":
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = {**result.get(key, {}), **value}
            else:
                result[key] = value

    touched_fields = [key for key, value in incoming.items() if key != "field_sources" and _has_value(value)]
    result["field_sources"] = merge_field_sources(result, incoming, source_label, touched_fields)
    return result
