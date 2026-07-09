from neo_app.models.registry import (
    get_family,
    get_model_family_payload,
    get_surface_families,
    get_loader_types,
    get_loader_contract,
    get_loader_contracts,
    get_parameter_profile,
    get_parameter_profiles,
    resolve_parameter_profile,
    check_model_family_compatibility,
)


def validate_readiness(*args, **kwargs):
    # Lazy import keeps provider registry -> Comfy provider -> compile router from
    # circularly importing readiness while package-level route_matrix imports are
    # being resolved.
    from neo_app.models.readiness import validate_readiness as _validate_readiness
    return _validate_readiness(*args, **kwargs)
