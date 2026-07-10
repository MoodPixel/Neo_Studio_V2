from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .variant_detector import file_extension, recommended_variant_match, variant_label_for_file
from .category_normalizer import normalize_base_model, normalize_creative_categories

DISCOVERY_SCHEMA_ID = "neo.admin.models.remote_file_discovery.v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _lower_list(value: Any) -> list[str]:
    return [_clean(item).lower() for item in _as_list(value) if _clean(item)]


def _normalize_path(value: Any) -> str:
    return _clean(value).replace("\\", "/").lstrip("/")


def _filename_from_path(path: str) -> str:
    return PurePosixPath(_normalize_path(path)).name


def _row_path(row: dict[str, Any]) -> str:
    for key in ("path", "rfilename", "filename", "name"):
        value = _clean(row.get(key))
        if value:
            return _normalize_path(value)
    return ""


def _row_size(row: dict[str, Any]) -> int | None:
    for key in ("size", "size_bytes"):
        value = row.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    lfs = _as_dict(row.get("lfs"))
    size = lfs.get("size")
    if isinstance(size, int) and size >= 0:
        return size
    return None


def _is_file_row(row: dict[str, Any]) -> bool:
    row_type = _clean(row.get("type")).lower()
    if row_type in {"directory", "dir", "folder"}:
        return False
    path = _row_path(row)
    if not path:
        return False
    # Hugging Face tree rows commonly use type=file. Sibling rows may omit type.
    return bool(PurePosixPath(path).suffix)


def _matches_patterns(path: str, patterns: list[str]) -> bool:
    lowered = path.lower()
    return any(pattern and pattern in lowered for pattern in patterns)


def normalize_remote_file_rows(rows: list[Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict) or not _is_file_row(raw):
            continue
        path = _row_path(raw)
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        provider_metadata = _as_dict(raw.get("metadata"))
        files.append({
            "path": path,
            "filename": _filename_from_path(path),
            "extension": file_extension(path),
            "size_bytes": _row_size(raw),
            "source_row_type": _clean(raw.get("type")) or "file",
            "metadata": {
                "blob_id": raw.get("oid") or raw.get("blob_id") or raw.get("sha") or "",
                "lfs": _as_dict(raw.get("lfs")),
                "download_url": raw.get("download_url") or raw.get("downloadUrl") or "",
                "source_url": raw.get("source_url") or raw.get("sourceUrl") or "",
                "file_id": raw.get("file_id") or raw.get("id") or "",
                "version_id": raw.get("version_id") or raw.get("modelVersionId") or "",
                "version_name": raw.get("version_name") or "",
                "base_model": raw.get("base_model") or raw.get("baseModel") or "",
                "primary": bool(raw.get("primary")),
                "provider_file_type": raw.get("civitai_file_type") or raw.get("file_type") or raw.get("fileType") or "",
                "hashes": _as_dict(raw.get("hashes")),
                "provider_metadata": provider_metadata,
                "preview_urls": _as_list(provider_metadata.get("preview_urls")),
            },
        })
    return files


def discover_remote_files_for_record(record: dict[str, Any], rows: list[Any], *, provider: str = "huggingface") -> dict[str, Any]:
    """Filter remote source rows using a model manifest record's file rules."""

    file_rules = _as_dict(record.get("file_rules"))
    source = _as_dict(record.get("source"))
    ui = _as_dict(record.get("ui"))
    include_extensions = set(_lower_list(file_rules.get("include_extensions")))
    include_patterns = _lower_list(file_rules.get("include_patterns"))
    exclude_patterns = _lower_list(file_rules.get("exclude_patterns"))
    quant_patterns = [_clean(item).upper() for item in _as_list(file_rules.get("quant_patterns")) if _clean(item)]
    source_path = _normalize_path(source.get("path"))
    variant_detection = _clean(file_rules.get("variant_detection"))
    recommended_variants = _as_list(ui.get("recommended_variants"))

    normalized = normalize_remote_file_rows(rows)
    variants: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for item in normalized:
        path = _normalize_path(item.get("path"))
        lowered = path.lower()
        if source_path and not lowered.startswith(source_path.lower().rstrip("/") + "/") and lowered != source_path.lower():
            skipped.append({"path": path, "reason": "outside_source_path"})
            continue
        if include_extensions and _clean(item.get("extension")).lower() not in include_extensions:
            skipped.append({"path": path, "reason": "extension_not_allowed"})
            continue
        if include_patterns and not _matches_patterns(path, include_patterns):
            skipped.append({"path": path, "reason": "missing_include_pattern"})
            continue
        if exclude_patterns and _matches_patterns(path, exclude_patterns):
            skipped.append({"path": path, "reason": "excluded_pattern"})
            continue
        variant = variant_label_for_file(
            str(item.get("filename") or path),
            variant_detection=variant_detection,
            quant_patterns=quant_patterns or None,
        )
        ui_categories = _as_list(ui.get("creative_categories"))
        remote_hint_tags = [
            *ui_categories,
            *_as_list(ui.get("badges")),
            _as_dict(item.get("metadata")).get("provider_file_type"),
            _as_dict(item.get("metadata")).get("base_model"),
            variant.get("variant"),
            variant.get("quality_label"),
        ]
        normalized_categories = normalize_creative_categories(remote_hint_tags) or [str(value) for value in ui_categories if str(value).strip()]
        normalized_base_model = normalize_base_model(record.get("base_model"), remote_values=[_as_dict(item.get("metadata")).get("base_model"), item.get("base_model")])
        variants.append({
            **item,
            **variant,
            "provider": provider,
            "catalog_id": record.get("id"),
            "display_name": record.get("display_name"),
            "model_type": record.get("model_type"),
            "base_model": normalized_base_model,
            "recommended": recommended_variant_match(variant, recommended_variants),
            "install": _as_dict(record.get("install")),
            "normalized": {
                "schema_id": "neo.admin.models.variant_normalization.v1",
                "domain": record.get("category"),
                "base_model": normalized_base_model,
                "model_type": record.get("model_type"),
                "technical_type": _as_dict(record.get("install")).get("target_type") or record.get("model_type"),
                "provider": provider,
                "creative_categories": sorted(set(str(value) for value in normalized_categories if str(value).strip())),
                "variant": variant.get("variant"),
                "recommended": recommended_variant_match(variant, recommended_variants),
            },
            "remote_only": True,
        })
    return {
        "schema_id": DISCOVERY_SCHEMA_ID,
        "status": "ready",
        "provider": provider,
        "catalog_id": record.get("id"),
        "record": {
            "id": record.get("id"),
            "display_name": record.get("display_name"),
            "category": record.get("category"),
            "base_model": record.get("base_model"),
            "model_type": record.get("model_type"),
            "source_mode": record.get("source_mode"),
        },
        "file_rules": file_rules,
        "summary": {
            "source_file_count": len(normalized),
            "variant_count": len(variants),
            "skipped_count": len(skipped),
            "recommended_count": sum(1 for item in variants if item.get("recommended")),
        },
        "variants": sorted(variants, key=lambda item: (not bool(item.get("recommended")), str(item.get("filename") or "").lower())),
        "skipped": skipped[:100],
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "downloads": False,
        },
    }
