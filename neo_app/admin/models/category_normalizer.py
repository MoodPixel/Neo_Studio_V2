from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
from typing import Any

from .manifest_loader import load_category_map, load_model_catalog

NORMALIZATION_SCHEMA_ID = "neo.admin.models.category_normalization.v1"
FILTER_SCHEMA_ID = "neo.admin.models.advanced_filter.v1"

_WORD_RE = re.compile(r"[^a-z0-9]+")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _slug(value: Any) -> str:
    text = _clean(value).lower().replace("_", " ").replace("-", " ")
    text = _WORD_RE.sub(" ", text)
    return " ".join(text.split())


def _unique_sorted(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        token = _clean(value)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return sorted(out, key=lambda item: item.lower())


def _lower_set(values: Any) -> set[str]:
    return {_slug(item) for item in _as_list(values) if _slug(item)}


def _category_lookup(category_map: dict[str, Any] | None = None) -> dict[str, str]:
    data = _as_dict(category_map) or load_category_map()
    creative = _as_dict(data.get("creative_categories"))
    lookup: dict[str, str] = {}
    for category, aliases in creative.items():
        category_id = _clean(category)
        if not category_id:
            continue
        lookup[_slug(category_id)] = category_id
        for alias in _as_list(aliases):
            alias_key = _slug(alias)
            if alias_key:
                lookup[alias_key] = category_id
    return lookup


def normalize_creative_categories(
    *sources: Any,
    category_map: dict[str, Any] | None = None,
) -> list[str]:
    """Map manifest/remote tags into Neo creative category ids.

    Remote source tags are hints only. Exact category ids and aliases from
    ``category_map.json`` are normalized to a controlled UI category list.
    """

    lookup = _category_lookup(category_map)
    matched: list[str] = []
    for source in sources:
        for raw in _as_list(source):
            token = _slug(raw)
            if not token:
                continue
            if token in lookup:
                matched.append(lookup[token])
                continue
            # Fuzzy containment is only used for multi-word remote tags such as
            # "anime character" or "cinematic lighting". It never creates new
            # category ids outside the controlled map.
            for alias, category_id in lookup.items():
                if alias and len(alias) >= 4 and (alias in token or token in alias):
                    matched.append(category_id)
    return _unique_sorted(matched)


def normalize_base_model(value: Any, *, remote_values: Any = None, category_map: dict[str, Any] | None = None) -> str:
    data = _as_dict(category_map) or load_category_map()
    allowed = [_clean(item) for item in _as_list(data.get("base_models")) if _clean(item)]
    allowed_slugs = {_slug(item): item for item in allowed}
    candidates = [_clean(value), *[_clean(item) for item in _as_list(remote_values)]]
    for candidate in candidates:
        token = _slug(candidate)
        if not token:
            continue
        if token in allowed_slugs:
            return allowed_slugs[token]
        if "sdxl" in token or "sd xl" in token or "stable diffusion xl" in token:
            return "sdxl"
        if "sd15" in token or "sd 1 5" in token or "stable diffusion 1 5" in token:
            return "sd15"
        if "flux" in token:
            return "flux"
        if "pony" in token:
            return "pony"
        if "wan" in token:
            return "wan"
        if "hunyuan" in token:
            return "hunyuan"
        if "qwen" in token:
            return "qwen"
    return _clean(value) or "unknown"


def _remote_metadata_tags(remote_metadata: dict[str, Any] | None = None) -> list[str]:
    data = _as_dict(remote_metadata)
    metadata = _as_dict(data.get("metadata")) if data else {}
    tags = [*_as_list(metadata.get("tags")), *_as_list(data.get("tags"))]
    for version in _as_list(data.get("versions")):
        if isinstance(version, dict):
            tags.extend(_as_list(version.get("trained_words")))
            if version.get("base_model"):
                tags.append(version.get("base_model"))
    return _unique_sorted(tags)


def normalize_record(record: dict[str, Any], *, remote_metadata: dict[str, Any] | None = None, category_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a catalog record copy with controlled normalized filter fields."""

    item = deepcopy(record)
    ui = _as_dict(item.get("ui"))
    source = _as_dict(item.get("source"))
    install = _as_dict(item.get("install"))
    remote_tags = _remote_metadata_tags(remote_metadata)
    manifest_categories = _as_list(ui.get("creative_categories"))
    badges = _as_list(ui.get("badges"))
    all_hint_tags = [*manifest_categories, *badges, *remote_tags]
    creative_categories = normalize_creative_categories(all_hint_tags, category_map=category_map)
    if not creative_categories and manifest_categories:
        # Preserve manifest ids even if a future category map is temporarily stale.
        creative_categories = _unique_sorted(manifest_categories)

    provider = _clean(source.get("provider")) or "unknown"
    backend_targets = _unique_sorted(install.get("backend_targets"))
    base_model = normalize_base_model(item.get("base_model"), remote_values=remote_tags, category_map=category_map)
    normalized = {
        "schema_id": NORMALIZATION_SCHEMA_ID,
        "domain": _clean(item.get("category")) or "unknown",
        "base_model": base_model,
        "model_type": _clean(item.get("model_type")) or "unknown",
        "technical_type": _clean(install.get("target_type")) or _clean(item.get("model_type")) or "unknown",
        "provider": provider,
        "source_mode": _clean(item.get("source_mode")) or "unknown",
        "backend_targets": backend_targets,
        "creative_categories": creative_categories,
        "badges": _unique_sorted(badges),
        "remote_tags": remote_tags,
        "recommended": bool(ui.get("recommended")),
        "dynamic_source": item.get("source_mode") == "discover_files",
        "remote_metadata_attached": bool(remote_metadata),
        "filter_group": _clean(ui.get("filter_group")),
    }
    filter_tokens = [
        item.get("id"), item.get("display_name"), normalized["domain"], normalized["base_model"],
        normalized["model_type"], normalized["technical_type"], normalized["provider"], normalized["source_mode"],
        *_as_list(normalized["creative_categories"]), *_as_list(normalized["badges"]), *remote_tags,
    ]
    normalized["filter_tokens"] = _unique_sorted(filter_tokens)
    item["normalized"] = normalized
    return item


def normalize_records(records: list[dict[str, Any]], *, category_map: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [normalize_record(record, category_map=category_map) for record in records]


def build_filter_options(records: list[dict[str, Any]], *, category_map: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = [record if _as_dict(record.get("normalized")) else normalize_record(record, category_map=category_map) for record in records]

    def collect(key: str) -> list[str]:
        values: list[Any] = []
        for record in normalized:
            value = _as_dict(record.get("normalized")).get(key)
            if isinstance(value, list):
                values.extend(value)
            else:
                values.append(value)
        return _unique_sorted(values)

    backend_counts: Counter[str] = Counter()
    creative_counts: Counter[str] = Counter()
    for record in normalized:
        data = _as_dict(record.get("normalized"))
        backend_counts.update(_as_list(data.get("backend_targets")))
        creative_counts.update(_as_list(data.get("creative_categories")))

    return {
        "schema_id": "neo.admin.models.filter_options.v1",
        "domains": collect("domain"),
        "base_models": collect("base_model"),
        "model_types": collect("model_type"),
        "technical_types": collect("technical_type"),
        "providers": collect("provider"),
        "source_modes": collect("source_mode"),
        "creative_categories": collect("creative_categories"),
        "badges": collect("badges"),
        "backend_targets": collect("backend_targets"),
        "boolean_flags": ["recommended", "dynamic_source"],
        "counts": {
            "creative_categories": dict(sorted(creative_counts.items())),
            "backend_targets": dict(sorted(backend_counts.items())),
        },
    }


def _filter_values(filters: dict[str, Any], *keys: str) -> set[str]:
    values: list[Any] = []
    for key in keys:
        value = filters.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    return _lower_set(values)


def _matches_any(record_values: Any, wanted: set[str]) -> bool:
    if not wanted:
        return True
    values = _lower_set(record_values if isinstance(record_values, list) else [record_values])
    return bool(values & wanted)


def record_matches_filters(record: dict[str, Any], filters: dict[str, Any], *, category_map: dict[str, Any] | None = None) -> bool:
    item = record if _as_dict(record.get("normalized")) else normalize_record(record, category_map=category_map)
    data = _as_dict(item.get("normalized"))
    if not _matches_any(data.get("domain"), _filter_values(filters, "domain", "category")):
        return False
    if not _matches_any(data.get("base_model"), _filter_values(filters, "base_model", "base_models")):
        return False
    if not _matches_any(data.get("model_type"), _filter_values(filters, "model_type", "model_types")):
        return False
    if not _matches_any(data.get("technical_type"), _filter_values(filters, "technical_type", "technical_types")):
        return False
    if not _matches_any(data.get("provider"), _filter_values(filters, "provider", "providers")):
        return False
    if not _matches_any(data.get("source_mode"), _filter_values(filters, "source_mode", "source_modes")):
        return False
    if not _matches_any(data.get("creative_categories"), _filter_values(filters, "creative_category", "creative_categories")):
        return False
    if not _matches_any(data.get("backend_targets"), _filter_values(filters, "backend", "backend_target", "backend_targets")):
        return False

    if "recommended" in filters and filters.get("recommended") is not None:
        if bool(data.get("recommended")) is not bool(filters.get("recommended")):
            return False
    if "dynamic_source" in filters and filters.get("dynamic_source") is not None:
        if bool(data.get("dynamic_source")) is not bool(filters.get("dynamic_source")):
            return False

    search = _slug(filters.get("search"))
    if search:
        haystack = " ".join(_slug(token) for token in _as_list(data.get("filter_tokens")))
        search_terms = [term for term in search.split() if term]
        if search not in haystack and not all(term in haystack for term in search_terms):
            return False
    return True


def filter_catalog_records(
    records: list[dict[str, Any]],
    filters: dict[str, Any] | None = None,
    *,
    category_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _as_dict(filters)
    normalized = normalize_records(records, category_map=category_map)
    matches = [record for record in normalized if record_matches_filters(record, data, category_map=category_map)]
    return {
        "schema_id": FILTER_SCHEMA_ID,
        "status": "ready",
        "filters": data,
        "summary": {
            "total_count": len(normalized),
            "match_count": len(matches),
        },
        "filter_options": build_filter_options(normalized, category_map=category_map),
        "records": matches,
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "downloads": False,
        },
    }


def admin_model_filter_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = load_model_catalog()
    category_map = load_category_map()
    records = [item for item in _as_list(catalog.get("records")) if isinstance(item, dict)]
    filters = _as_dict(payload).get("filters") if "filters" in _as_dict(payload) else _as_dict(payload)
    return filter_catalog_records(records, _as_dict(filters), category_map=category_map)
