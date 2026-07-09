from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.models.schema import (
    LoaderContract,
    LoaderContractResolution,
    LoaderType,
    ParameterProfile,
    ParameterProfileResolution,
    ModelFamily,
    ModelFamilyCompatibilityRequest,
    ModelFamilyCompatibilityResult,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT_DIR / "neo_app" / "models" / "model_family_manifest.json"

MODE_BLOCKING_STATUSES = {"unsupported"}
MODE_GATED_STATUSES = {"gated", "provider_required", "variant_required", "mmproj_required", "experimental", "planned"}


@lru_cache(maxsize=1)
def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_loader_types() -> list[LoaderType]:
    return [LoaderType(**item) for item in _manifest().get("loader_types", [])]


@lru_cache(maxsize=1)
def get_loader_contracts() -> list[LoaderContract]:
    return [LoaderContract(**item) for item in _manifest().get("loader_contracts", [])]


def get_loader_contract(loader_id: str) -> LoaderContract | None:
    return next((contract for contract in get_loader_contracts() if contract.loader_id == loader_id), None)


@lru_cache(maxsize=1)
def get_parameter_profiles() -> list[ParameterProfile]:
    return [ParameterProfile(**item) for item in _manifest().get("parameter_profiles", [])]


def get_parameter_profile(profile_id: str) -> ParameterProfile | None:
    return next((profile for profile in get_parameter_profiles() if profile.profile_id == profile_id), None)


def _field_applies_to_mode(field: dict, mode: str | None) -> bool:
    modes = field.get("modes") or []
    return not modes or not mode or mode in modes


def _dedupe_fields(fields: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for field in fields:
        selected[field["field_id"]] = field
    return list(selected.values())


def resolve_parameter_profile(family: ModelFamily, loader: str | None, mode: str | None = None) -> ParameterProfileResolution:
    errors: list[str] = []
    warnings: list[str] = []
    selected: ParameterProfile | None = None

    for profile_id in family.parameter_profiles:
        profile = get_parameter_profile(profile_id)
        if profile is None:
            warnings.append(f"Family {family.family_id} references missing parameter profile {profile_id}.")
            continue
        family_match = not profile.families or family.family_id in profile.families
        loader_match = not loader or not profile.loaders or loader in profile.loaders
        mode_match = not mode or not profile.modes or mode in profile.modes
        if family_match and loader_match and mode_match:
            selected = profile
            break

    if selected is None:
        errors.append(f"No parameter profile matched family={family.family_id}, loader={loader}, mode={mode}.")
        return ParameterProfileResolution(family=family.family_id, loader=loader, mode=mode, errors=errors, warnings=warnings)

    fields = [
        model_to_dict(field)
        for field in [*selected.shared_fields, *selected.family_fields]
        if _field_applies_to_mode(model_to_dict(field), mode)
    ]
    fields = _dedupe_fields(fields)

    return ParameterProfileResolution(
        profile_id=selected.profile_id,
        family=family.family_id,
        loader=loader,
        mode=mode,
        fields=fields,
        hidden_fields=list(selected.hidden_fields),
        disabled_fields=list(selected.disabled_fields),
        readiness_fields=list(selected.readiness_fields),
        readiness_gates=_selected_readiness_gates(family, mode),
        errors=errors,
        warnings=warnings,
    )


@lru_cache(maxsize=1)
def get_families() -> list[ModelFamily]:
    return [ModelFamily(**item) for item in _manifest().get("families", [])]


def get_family(family_id: str) -> ModelFamily | None:
    return next((family for family in get_families() if family.family_id == family_id), None)


def get_surface_families(surface: str) -> list[ModelFamily]:
    return [family for family in get_families() if surface in family.surfaces]


def get_model_family_payload(surface: str | None = None) -> dict:
    from neo_app.models.physical_validation import physical_validation_payload
    from neo_app.models.route_matrix import ROUTE_STATES, ROUTE_STATE_UI_POLICY, list_model_backend_routes
    from neo_app.models.route_regression_lock import regression_lock_payload

    families = get_surface_families(surface) if surface else get_families()
    route_rows = list_model_backend_routes()
    if surface == "image":
        visible_family_ids = {
            str(row.get("family"))
            for row in route_rows
            if (row.get("ui_policy") or {}).get("visible_in_normal_ui") and row.get("state") in {"available", "experimental_available"}
        }
        # V25.9.18 route honesty: normal Image UI only shows families with at
        # least one real selectable image route. Provider-gated video families
        # such as Wan/Hunyuan stay in diagnostics/route matrix but not in the
        # everyday Image Model Family dropdown.
        if visible_family_ids:
            families = [family for family in families if family.family_id in visible_family_ids]
    return {
        "registry_version": _manifest().get("registry_version", "0.1.0"),
        "loader_contract_version": _manifest().get("loader_contract_version", "0.1.0"),
        "parameter_profile_version": _manifest().get("parameter_profile_version", "0.1.0"),
        "families": [model_to_dict(family) for family in families],
        "loader_types": [model_to_dict(loader) for loader in get_loader_types()],
        "loader_contracts": [model_to_dict(contract) for contract in get_loader_contracts()],
        "parameter_profiles": [model_to_dict(profile) for profile in get_parameter_profiles()],
        "policy": {
            "family_is_not_loader": True,
            "loader_is_backend_neutral": True,
            "gguf_is_loader_extension": True,
            "core_declares_contract_provider_validates_runtime": True,
            "mode_support_is_readiness_intent_not_runtime_enablement": True,
            "parameter_profiles_are_selected_by_family_loader_mode": True,
            "readiness_validator_blocks_invalid_routes_before_provider_compile": True,
            "route_matrix_is_backend_family_loader_mode_source_of_truth": True,
        },
        "route_states": list(ROUTE_STATES),
        "route_state_ui_policy": ROUTE_STATE_UI_POLICY,
        "route_matrix": route_rows,
        "physical_validation": physical_validation_payload(),
        "route_regression_lock": regression_lock_payload(),
    }


def _loader_meta(loader_id: str | None) -> LoaderType | None:
    if not loader_id:
        return None
    return next((loader for loader in get_loader_types() if loader.loader_id == loader_id), None)


def _mode_status(family: ModelFamily, mode: str | None, loader: str | None = None) -> str | None:
    if not mode:
        return None
    if loader:
        loader_modes = family.loader_mode_support.get(loader) or {}
        if mode in loader_modes:
            return loader_modes.get(mode)
    return family.mode_support.get(mode)


def _selected_required_roles(family: ModelFamily, loader: str | None) -> list[str]:
    if not loader:
        return []
    return list(family.required_roles.get(loader, []))


def resolve_loader_contract(family: ModelFamily, loader: str | None, mode: str | None = None) -> LoaderContractResolution | None:
    if not loader:
        return None

    contract = get_loader_contract(loader)
    if contract is None:
        return LoaderContractResolution(
            loader=loader,
            backend_neutral=False,
            provider_translation_required=True,
            errors=[f"No loader contract registered for loader {loader}."],
        )

    selected_required_roles = _selected_required_roles(family, loader) or list(contract.required_roles)
    optional_roles = list(contract.optional_roles)
    allowed_roles = list(dict.fromkeys([*contract.required_roles, *contract.optional_roles, *contract.allowed_roles]))

    if mode:
        override = contract.mode_role_overrides.get(mode, {})
        selected_required_roles = list(dict.fromkeys([*selected_required_roles, *override.get("required_roles", [])]))
        optional_roles = list(dict.fromkeys([*optional_roles, *override.get("optional_roles", [])]))
        allowed_roles = list(dict.fromkeys([*allowed_roles, *override.get("allowed_roles", [])]))

    unknown_roles = [role for role in selected_required_roles if role not in allowed_roles]
    errors = [f"Loader contract {loader} does not allow role {role}." for role in unknown_roles]

    role_definitions = {
        role: model_to_dict(definition)
        for role, definition in contract.role_definitions.items()
        if role in set([*selected_required_roles, *optional_roles, *allowed_roles])
    }

    return LoaderContractResolution(
        loader=loader,
        backend_neutral=contract.backend_neutral,
        provider_translation_required=contract.provider_translation_required,
        required_roles=selected_required_roles,
        optional_roles=optional_roles,
        allowed_roles=allowed_roles,
        role_definitions=role_definitions,
        errors=errors,
    )


def _selected_backend_requirements(family: ModelFamily, backend: str | None, loader: str | None) -> list[str]:
    if not backend or not loader:
        return []
    return list(family.backend_requirements.get(backend, {}).get(loader, []))


def _selected_readiness_gates(family: ModelFamily, mode: str | None) -> list[str]:
    if not mode:
        return []
    return list(family.readiness_gates.get(mode, []))


def check_model_family_compatibility(payload: dict) -> dict:
    request = ModelFamilyCompatibilityRequest(**payload)
    family = get_family(request.family)
    errors: list[str] = []
    warnings: list[str] = []
    required_extensions: list[str] = []

    if family is None:
        errors.append(f"Unknown model family: {request.family}")
        result = ModelFamilyCompatibilityResult(
            ok=False,
            family=request.family,
            loader=request.loader,
            backend=request.backend,
            mode=request.mode,
            errors=errors,
        )
        return model_to_dict(result)

    if request.surface not in family.surfaces:
        errors.append(f"Family {family.family_id} is not registered for surface {request.surface}.")

    mode_status = _mode_status(family, request.mode, request.loader)
    if request.mode:
        if mode_status in MODE_BLOCKING_STATUSES:
            errors.append(f"Mode {request.mode} is marked {mode_status} for family {family.family_id}.")
        elif mode_status in MODE_GATED_STATUSES:
            warnings.append(f"Mode {request.mode} is marked {mode_status} for family {family.family_id}; readiness/provider validation is required before runtime execution.")
        elif mode_status is None and family.supported_modes and request.mode not in family.supported_modes:
            warnings.append(f"Mode {request.mode} is not declared by family {family.family_id}; an extension/provider may still add support later.")

    if request.backend and request.backend not in family.supported_backends:
        errors.append(f"Backend {request.backend} is not declared for family {family.family_id}.")

    loader_resolution = None
    if request.loader:
        if request.loader not in family.supported_loaders:
            errors.append(f"Loader {request.loader} is not supported by family {family.family_id}.")
        loader_meta = _loader_meta(request.loader)
        if loader_meta and loader_meta.requires_extension and loader_meta.extension_id:
            required_extensions.append(loader_meta.extension_id)
            if loader_meta.extension_id not in family.required_extensions:
                warnings.append(f"Loader {request.loader} requires {loader_meta.extension_id}; family did not list it explicitly.")

        loader_resolution = resolve_loader_contract(family, request.loader, request.mode)
        if loader_resolution:
            errors.extend(loader_resolution.errors)
            warnings.extend(loader_resolution.warnings)
            if not loader_resolution.backend_neutral:
                errors.append(f"Loader {request.loader} is not marked backend-neutral.")

    required_extensions.extend([ext for ext in family.required_extensions if ext not in required_extensions])
    loader_contract_payload = model_to_dict(loader_resolution) if loader_resolution else {}
    parameter_resolution = resolve_parameter_profile(family, request.loader or family.default_loader, request.mode)
    errors.extend(parameter_resolution.errors)
    warnings.extend(parameter_resolution.warnings)
    parameter_profile_payload = model_to_dict(parameter_resolution)

    result = ModelFamilyCompatibilityResult(
        ok=not errors,
        family=family.family_id,
        loader=request.loader,
        backend=request.backend,
        mode=request.mode,
        mode_status=mode_status,
        runtime_profiles=family.runtime_profiles,
        parameter_profiles=family.parameter_profiles,
        selected_parameter_profile=parameter_resolution.profile_id,
        parameter_profile=parameter_profile_payload,
        required_roles=_selected_required_roles(family, request.loader),
        optional_roles=loader_contract_payload.get("optional_roles", []),
        allowed_roles=loader_contract_payload.get("allowed_roles", []),
        loader_contract=loader_contract_payload,
        backend_requirements=_selected_backend_requirements(family, request.backend, request.loader),
        readiness_gates=_selected_readiness_gates(family, request.mode),
        errors=errors,
        warnings=warnings,
        required_extensions=required_extensions,
    )
    return model_to_dict(result)
