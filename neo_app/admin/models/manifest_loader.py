from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from .manifest_schema import (
    validate_category_map,
    validate_folder_rules,
    validate_model_catalog,
    validate_model_packs,
    validate_workspace_requirements,
)

ROOT_DIR = Path(__file__).resolve().parents[3]
MANIFEST_DIR = ROOT_DIR / "neo_manifests" / "models"
MODEL_CATALOG_PATH = MANIFEST_DIR / "model_catalog.json"
FOLDER_RULES_PATH = MANIFEST_DIR / "folder_rules.json"
CATEGORY_MAP_PATH = MANIFEST_DIR / "category_map.json"
MODEL_PACKS_PATH = MANIFEST_DIR / "recommended_packs.json"
WORKSPACE_REQUIREMENTS_PATH = MANIFEST_DIR / "workspace_requirements.json"
SCHEMA_PATH = MANIFEST_DIR / "model_catalog.schema.json"


class ModelManifestError(RuntimeError):
    """Raised when a required model manifest file is missing or invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ModelManifestError(f"Model manifest file not found: {path.relative_to(ROOT_DIR)}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelManifestError(f"Invalid JSON in {path.relative_to(ROOT_DIR)}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelManifestError(f"Model manifest file must contain a JSON object: {path.relative_to(ROOT_DIR)}")
    return payload


@lru_cache(maxsize=1)
def load_model_catalog() -> dict[str, Any]:
    return _read_json(MODEL_CATALOG_PATH)


@lru_cache(maxsize=1)
def load_folder_rules() -> dict[str, Any]:
    return _read_json(FOLDER_RULES_PATH)


@lru_cache(maxsize=1)
def load_category_map() -> dict[str, Any]:
    return _read_json(CATEGORY_MAP_PATH)


@lru_cache(maxsize=1)
def load_model_catalog_schema() -> dict[str, Any]:
    return _read_json(SCHEMA_PATH)


@lru_cache(maxsize=1)
def load_model_packs() -> dict[str, Any]:
    return _read_json(MODEL_PACKS_PATH)


@lru_cache(maxsize=1)
def load_workspace_requirements() -> dict[str, Any]:
    return _read_json(WORKSPACE_REQUIREMENTS_PATH)


def validate_loaded_manifests() -> dict[str, Any]:
    """Validate all Phase 1 manifest files and return a UI-safe payload."""

    category_map = load_category_map()
    folder_rules = load_folder_rules()
    catalog = load_model_catalog()
    model_packs = load_model_packs()
    workspace_requirements = load_workspace_requirements()
    category_result = validate_category_map(category_map)
    folder_result = validate_folder_rules(folder_rules)
    catalog_result = validate_model_catalog(catalog, folder_rules=folder_rules, category_map=category_map)
    packs_result = validate_model_packs(model_packs, catalog=catalog)
    workspace_result = validate_workspace_requirements(workspace_requirements, catalog=catalog, model_packs=model_packs)
    errors = [*category_result.errors, *folder_result.errors, *catalog_result.errors, *packs_result.errors, *workspace_result.errors]
    warnings = [*category_result.warnings, *folder_result.warnings, *catalog_result.warnings, *packs_result.warnings, *workspace_result.warnings]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "files": {
            "catalog": str(MODEL_CATALOG_PATH.relative_to(ROOT_DIR)),
            "folder_rules": str(FOLDER_RULES_PATH.relative_to(ROOT_DIR)),
            "category_map": str(CATEGORY_MAP_PATH.relative_to(ROOT_DIR)),
            "schema": str(SCHEMA_PATH.relative_to(ROOT_DIR)),
            "model_packs": str(MODEL_PACKS_PATH.relative_to(ROOT_DIR)),
            "workspace_requirements": str(WORKSPACE_REQUIREMENTS_PATH.relative_to(ROOT_DIR)),
        },
    }


def clear_model_manifest_cache() -> None:
    """Test/helper hook for reloading manifests without restarting Python."""

    load_model_catalog.cache_clear()
    load_folder_rules.cache_clear()
    load_category_map.cache_clear()
    load_model_catalog_schema.cache_clear()
    load_model_packs.cache_clear()
    load_workspace_requirements.cache_clear()
