from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATALOG_SCHEMA_ID = "neo.models.catalog.v1"
FOLDER_RULES_SCHEMA_ID = "neo.models.folder_rules.v1"
CATEGORY_MAP_SCHEMA_ID = "neo.models.category_map.v1"
MODEL_PACKS_SCHEMA_ID = "neo.models.packs.v1"
WORKSPACE_REQUIREMENTS_SCHEMA_ID = "neo.models.workspace_requirements.v1"

SUPPORTED_DOMAINS = {"image", "video", "llm", "utility", "voice"}
SUPPORTED_SOURCE_MODES = {"static_file", "discover_files", "manual_only"}
SUPPORTED_PROVIDERS = {"huggingface", "civitai", "manual", "local", "url"}


@dataclass(slots=True)
class ManifestValidationResult:
    """Compact validation result used by Admin Model Guide payloads."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings)}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_category_map(category_map: dict[str, Any]) -> ManifestValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if category_map.get("schema_id") != CATEGORY_MAP_SCHEMA_ID:
        errors.append(f"category_map.schema_id must be {CATEGORY_MAP_SCHEMA_ID}")
    creative = category_map.get("creative_categories")
    if not isinstance(creative, dict) or not creative:
        errors.append("category_map.creative_categories must be a non-empty object")
    else:
        for category, tags in creative.items():
            if not _is_non_empty_string(category):
                errors.append("category_map contains an empty creative category id")
            if not isinstance(tags, list):
                errors.append(f"category_map creative category '{category}' tags must be a list")
    technical_types = category_map.get("technical_types")
    if not isinstance(technical_types, list) or not technical_types:
        warnings.append("category_map.technical_types is empty; install target validation will be weaker")
    return ManifestValidationResult(ok=not errors, errors=errors, warnings=warnings)


def validate_folder_rules(folder_rules: dict[str, Any]) -> ManifestValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if folder_rules.get("schema_id") != FOLDER_RULES_SCHEMA_ID:
        errors.append(f"folder_rules.schema_id must be {FOLDER_RULES_SCHEMA_ID}")
    backends = folder_rules.get("backends")
    if not isinstance(backends, dict) or not backends:
        errors.append("folder_rules.backends must be a non-empty object")
    else:
        for backend_id, rules in backends.items():
            if not _is_non_empty_string(backend_id):
                errors.append("folder_rules contains an empty backend id")
            if not isinstance(rules, dict) or not rules:
                warnings.append(f"folder_rules backend '{backend_id}' has no model type routes")
    extensions = folder_rules.get("allowed_extensions")
    if not isinstance(extensions, dict) or not extensions:
        warnings.append("folder_rules.allowed_extensions is empty; file validation will be weaker")
    return ManifestValidationResult(ok=not errors, errors=errors, warnings=warnings)



def validate_model_packs(
    model_packs: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> ManifestValidationResult:
    """Validate the public recommended model packs manifest."""

    errors: list[str] = []
    warnings: list[str] = []
    if model_packs.get("schema_id") != MODEL_PACKS_SCHEMA_ID:
        errors.append(f"model_packs.schema_id must be {MODEL_PACKS_SCHEMA_ID}")
    packs = model_packs.get("packs")
    if not isinstance(packs, list) or not packs:
        errors.append("model_packs.packs must be a non-empty list")
        packs = []

    catalog_ids = {
        str(record.get("id"))
        for record in _as_list(_as_dict(catalog).get("records"))
        if isinstance(record, dict) and _is_non_empty_string(record.get("id"))
    }
    seen_pack_ids: set[str] = set()
    for index, pack in enumerate(packs):
        prefix = f"packs[{index}]"
        if not isinstance(pack, dict):
            errors.append(f"{prefix} must be an object")
            continue
        pack_id = pack.get("id")
        if not _is_non_empty_string(pack_id):
            errors.append(f"{prefix}.id is required")
            pack_id = f"<missing-{index}>"
        elif str(pack_id) in seen_pack_ids:
            errors.append(f"Duplicate model pack id: {pack_id}")
        seen_pack_ids.add(str(pack_id))
        for key in ("display_name", "category", "base_model"):
            if not _is_non_empty_string(pack.get(key)):
                errors.append(f"{prefix}.{key} is required")
        if str(pack.get("category") or "") not in SUPPORTED_DOMAINS:
            errors.append(f"{prefix}.category '{pack.get('category')}' is not supported")
        items = pack.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{prefix}.items must be a non-empty list")
            items = []
        for item_index, item in enumerate(items):
            item_prefix = f"{prefix}.items[{item_index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_prefix} must be an object")
                continue
            catalog_id = item.get("catalog_id")
            if not _is_non_empty_string(catalog_id):
                errors.append(f"{item_prefix}.catalog_id is required")
            elif catalog_ids and str(catalog_id) not in catalog_ids:
                errors.append(f"{item_prefix}.catalog_id '{catalog_id}' does not exist in model_catalog.records")
            if not _is_non_empty_string(item.get("role")):
                warnings.append(f"{item_prefix}.role is empty")
            if "required" not in item:
                warnings.append(f"{item_prefix}.required not declared; defaulting to required")
    return ManifestValidationResult(ok=not errors, errors=errors, warnings=warnings)



def validate_workspace_requirements(
    workspace_requirements: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
    model_packs: dict[str, Any] | None = None,
) -> ManifestValidationResult:
    """Validate workspace-to-model requirement mappings."""

    errors: list[str] = []
    warnings: list[str] = []
    if workspace_requirements.get("schema_id") != WORKSPACE_REQUIREMENTS_SCHEMA_ID:
        errors.append(f"workspace_requirements.schema_id must be {WORKSPACE_REQUIREMENTS_SCHEMA_ID}")
    workspaces = workspace_requirements.get("workspaces")
    if not isinstance(workspaces, list) or not workspaces:
        errors.append("workspace_requirements.workspaces must be a non-empty list")
        workspaces = []

    catalog_ids = {
        str(record.get("id"))
        for record in _as_list(_as_dict(catalog).get("records"))
        if isinstance(record, dict) and _is_non_empty_string(record.get("id"))
    }
    pack_ids = {
        str(pack.get("id"))
        for pack in _as_list(_as_dict(model_packs).get("packs"))
        if isinstance(pack, dict) and _is_non_empty_string(pack.get("id"))
    }
    seen_workspace_ids: set[str] = set()
    for index, workspace in enumerate(workspaces):
        prefix = f"workspaces[{index}]"
        if not isinstance(workspace, dict):
            errors.append(f"{prefix} must be an object")
            continue
        workspace_id = workspace.get("id")
        if not _is_non_empty_string(workspace_id):
            errors.append(f"{prefix}.id is required")
            workspace_id = f"<missing-{index}>"
        elif str(workspace_id) in seen_workspace_ids:
            errors.append(f"Duplicate workspace requirement id: {workspace_id}")
        seen_workspace_ids.add(str(workspace_id))
        for key in ("surface_id", "display_name", "backend", "base_model"):
            if not _is_non_empty_string(workspace.get(key)):
                errors.append(f"{prefix}.{key} is required")
        for pack_id in _as_list(workspace.get("model_pack_ids")):
            if not _is_non_empty_string(pack_id):
                errors.append(f"{prefix}.model_pack_ids contains an empty value")
            elif pack_ids and str(pack_id) not in pack_ids:
                errors.append(f"{prefix}.model_pack_ids '{pack_id}' does not exist in recommended_packs.packs")
        for key in ("required_catalog_ids", "optional_catalog_ids"):
            for catalog_id in _as_list(workspace.get(key)):
                if not _is_non_empty_string(catalog_id):
                    errors.append(f"{prefix}.{key} contains an empty value")
                elif catalog_ids and str(catalog_id) not in catalog_ids:
                    errors.append(f"{prefix}.{key} '{catalog_id}' does not exist in model_catalog.records")
        if not _as_list(workspace.get("model_pack_ids")) and not _as_list(workspace.get("required_catalog_ids")):
            warnings.append(f"{prefix} has no model_pack_ids or required_catalog_ids")
        triggers = _as_dict(workspace.get("triggers"))
        if not triggers:
            warnings.append(f"{prefix}.triggers is empty; workspace matching will rely on explicit id only")
        guide_filter = _as_dict(workspace.get("guide_filter"))
        if not guide_filter:
            warnings.append(f"{prefix}.guide_filter is empty; UI deep-link filtering will be weaker")
    return ManifestValidationResult(ok=not errors, errors=errors, warnings=warnings)

def validate_model_catalog(
    catalog: dict[str, Any],
    *,
    folder_rules: dict[str, Any] | None = None,
    category_map: dict[str, Any] | None = None,
) -> ManifestValidationResult:
    """Validate the public model catalog without requiring optional dependencies.

    This deliberately checks the contract Neo depends on, not the full JSON Schema
    draft. Keeping validation dependency-free keeps first-run startup lighter.
    """

    errors: list[str] = []
    warnings: list[str] = []
    if catalog.get("schema_id") != CATALOG_SCHEMA_ID:
        errors.append(f"model_catalog.schema_id must be {CATALOG_SCHEMA_ID}")
    catalog_meta = _as_dict(catalog.get("catalog"))
    if not _is_non_empty_string(catalog_meta.get("id")):
        errors.append("model_catalog.catalog.id is required")
    if not _is_non_empty_string(catalog_meta.get("display_name")):
        errors.append("model_catalog.catalog.display_name is required")

    records = catalog.get("records")
    if not isinstance(records, list) or not records:
        errors.append("model_catalog.records must be a non-empty list")
        records = []

    technical_types = set(_as_list(_as_dict(category_map).get("technical_types")))
    creative_categories = set(_as_dict(_as_dict(category_map).get("creative_categories")).keys())
    backend_rules = _as_dict(_as_dict(folder_rules).get("backends"))
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue
        record_id = record.get("id")
        if not _is_non_empty_string(record_id):
            errors.append(f"{prefix}.id is required")
            record_id = f"<missing-{index}>"
        elif record_id in seen_ids:
            errors.append(f"Duplicate model record id: {record_id}")
        seen_ids.add(str(record_id))

        for key in ("display_name", "base_model", "model_type"):
            if not _is_non_empty_string(record.get(key)):
                errors.append(f"{prefix}.{key} is required")
        category = str(record.get("category") or "")
        if category not in SUPPORTED_DOMAINS:
            errors.append(f"{prefix}.category '{category}' is not supported")
        source_mode = str(record.get("source_mode") or "")
        if source_mode not in SUPPORTED_SOURCE_MODES:
            errors.append(f"{prefix}.source_mode '{source_mode}' is not supported")
        source = _as_dict(record.get("source"))
        provider = str(source.get("provider") or "")
        if provider not in SUPPORTED_PROVIDERS:
            errors.append(f"{prefix}.source.provider '{provider}' is not supported")
        if source_mode == "static_file" and not _is_non_empty_string(source.get("filename")):
            errors.append(f"{prefix}.source.filename is required for static_file records")
        if source_mode == "discover_files" and provider == "huggingface" and not _is_non_empty_string(source.get("repo")):
            warnings.append(f"{prefix} is a Hugging Face discovery placeholder without repo")
        if source_mode == "discover_files" and provider == "civitai" and not (_is_non_empty_string(source.get("model_id")) or _is_non_empty_string(source.get("version_id"))):
            warnings.append(f"{prefix} is a Civitai discovery placeholder without model/version id")

        install = _as_dict(record.get("install"))
        target_type = str(install.get("target_type") or record.get("model_type") or "")
        if not target_type:
            errors.append(f"{prefix}.install.target_type is required")
        if technical_types and target_type not in technical_types:
            warnings.append(f"{prefix}.install.target_type '{target_type}' is not listed in category_map.technical_types")
        backend_targets = [str(item) for item in _as_list(install.get("backend_targets")) if str(item).strip()]
        for backend_id in backend_targets:
            backend_map = _as_dict(backend_rules.get(backend_id))
            if backend_map and target_type not in backend_map:
                warnings.append(f"{prefix} target_type '{target_type}' has no folder rule for backend '{backend_id}'")

        ui = _as_dict(record.get("ui"))
        categories = [str(item) for item in _as_list(ui.get("creative_categories")) if str(item).strip()]
        if not categories:
            warnings.append(f"{prefix}.ui.creative_categories is empty")
        for creative_category in categories:
            if creative_categories and creative_category not in creative_categories:
                warnings.append(f"{prefix}.ui.creative_categories contains unknown category '{creative_category}'")
    return ManifestValidationResult(ok=not errors, errors=errors, warnings=warnings)
