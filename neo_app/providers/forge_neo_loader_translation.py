from __future__ import annotations

import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from neo_app.models.forge_neo_route_catalog import (
    FORGE_PROVIDER_LOADER_ID,
    forge_route_authority_payload,
    resolve_forge_route,
)
from neo_app.providers.forge_neo_model_classification import ensure_forge_live_discovery
from neo_app.providers.forge_neo_workflow_compilers import FORGE_WORKFLOW_COMPILER_IDS
from neo_app.providers.schema import NeoJob

FORGE_LOADER_TRANSLATION_SCHEMA_ID = "neo.provider.forge_loader_translation.v1"
FORGE_LOADER_TRANSLATION_VERSION = "1.1.0"

_PROVIDER_DEFAULT_VALUES = {"", "provider_default", "automatic", "auto", "none", "null"}
_SELECTABLE_AUTHORITY_STATES = {"available", "experimental_available"}
_HASH_SUFFIX_RE = re.compile(r"\s*\[[0-9a-f]{6,64}\]\s*$", re.IGNORECASE)

PRIMARY_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "checkpoint": ("checkpoint", "sd_model_checkpoint", "model"),
    "diffusion_model": ("diffusion_model", "unet", "model", "checkpoint"),
    "gguf": ("gguf_unet", "gguf_model", "diffusion_model", "model"),
    "checkpoint_aio": ("qwen_rapid_aio_checkpoint", "checkpoint_aio", "checkpoint", "model"),
    "nunchaku": ("nunchaku_model", "diffusion_model", "model"),
    "api_model": ("api_model", "model"),
    "unet": ("unet", "diffusion_model", "model"),
}

ROLE_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "vae": ("vae", "vae_name", "qwen_image_vae", "vae_or_ae", "ae_or_vae"),
    "vae_or_ae": ("vae_or_ae", "ae_or_vae", "vae", "vae_name", "ae"),
    "ae_or_vae": ("ae_or_vae", "vae_or_ae", "vae", "vae_name", "ae"),
    "qwen_image_vae": ("qwen_image_vae", "vae", "vae_name", "vae_or_ae", "ae_or_vae"),
    "text_encoder_primary": (
        "text_encoder_primary",
        "text_encoder_1",
        "clip_l",
        "clip",
        "gguf_text_encoder_primary",
    ),
    "text_encoder_secondary": (
        "text_encoder_secondary",
        "text_encoder_2",
        "t5",
        "t5xxl",
        "gguf_text_encoder_secondary",
    ),
    "qwen_text_encoder": (
        "qwen_text_encoder",
        "text_encoder_primary",
        "text_encoder_1",
        "gguf_text_encoder_primary",
    ),
    "qwen3_text_encoder": (
        "qwen3_text_encoder",
        "qwen_text_encoder",
        "text_encoder_primary",
        "text_encoder_1",
        "gguf_text_encoder_primary",
    ),
    "qwen3vl_4b_text_encoder": (
        "qwen3vl_4b_text_encoder",
        "qwen_text_encoder",
        "text_encoder_primary",
        "text_encoder_1",
        "gguf_text_encoder_primary",
    ),
    "mmproj": ("qwen_mmproj_optional", "mmproj", "mmproj_model"),
}

RUNTIME_SETTING_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "CLIP_stop_at_last_layers": ("clip_skip", "CLIP_stop_at_last_layers"),
}

GENERATION_PARAMETER_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "flux_guidance": ("flux_guidance", "guidance", "cfg_scale", "cfg"),
}


def _first(params: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in params and params.get(key) is not None:
            return params.get(key)
    return default


def _selected(value: Any) -> bool:
    return str(value or "").strip().casefold() not in _PROVIDER_DEFAULT_VALUES


def _portable_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # PurePath follows the host OS. Test both path styles so Windows backend
    # records remain private when Neo runs on POSIX and vice versa.
    candidates = [
        PurePath(text).name,
        PureWindowsPath(text).name,
        PurePosixPath(text).name,
    ]
    return min((item for item in candidates if item), key=len, default=text)


def _identity_token(value: Any) -> str:
    name = _HASH_SUFFIX_RE.sub("", _portable_name(value)).strip().casefold()
    return name.replace("\\", "/")


def _record_tokens(record: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("id", "name", "title", "model_name", "filename"):
        token = _identity_token(record.get(key))
        if token:
            tokens.add(token)
            stem = token.rsplit(".", 1)[0] if "." in token else token
            if stem:
                tokens.add(stem)
    return tokens


def _request_tokens(value: Any) -> set[str]:
    token = _identity_token(value)
    if not token:
        return set()
    tokens = {token}
    if "." in token:
        tokens.add(token.rsplit(".", 1)[0])
    return tokens


def _matching_record(records: list[dict[str, Any]], requested: Any) -> dict[str, Any] | None:
    requested_tokens = _request_tokens(requested)
    if not requested_tokens:
        return None
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for record in records:
        tokens = _record_tokens(record)
        if tokens & requested_tokens:
            exact.append(record)
        elif any(req in candidate or candidate in req for req in requested_tokens for candidate in tokens):
            partial.append(record)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return sorted(exact, key=lambda item: str(item.get("name") or item.get("id") or "").casefold())[0]
    if len(partial) == 1:
        return partial[0]
    return None


def _classification_inventory(snapshot: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    classification, _intersection = ensure_forge_live_discovery(snapshot)
    has_inventory = bool(
        snapshot.get("reachable")
        or snapshot.get("model_classification")
        or snapshot.get("models")
        or snapshot.get("modules")
        or classification.get("models")
        or classification.get("modules")
    )
    return classification, has_inventory


def _primary_request(job: NeoJob, params: dict[str, Any], loader: str) -> tuple[str, str]:
    if _selected(job.model):
        return str(job.model).strip(), "job.model"
    explicit_overrides = params.get("override_settings") if isinstance(params.get("override_settings"), dict) else {}
    override_model = explicit_overrides.get("sd_model_checkpoint")
    if _selected(override_model):
        return str(override_model).strip(), "override_settings.sd_model_checkpoint"
    for key in PRIMARY_INPUT_ALIASES.get(loader, ("model",)):
        value = params.get(key)
        if _selected(value):
            return str(value).strip(), f"params.{key}"
    return "", ""


def _role_request(params: dict[str, Any], role: str) -> tuple[str, str]:
    for key in ROLE_INPUT_ALIASES.get(role, (role,)):
        value = params.get(key)
        if _selected(value):
            return str(value).strip(), f"params.{key}"
    return "", ""


def _generic_module_requests(params: dict[str, Any]) -> list[tuple[str, str]]:
    values: Any = None
    source = ""
    explicit_overrides = params.get("override_settings") if isinstance(params.get("override_settings"), dict) else {}
    for key, candidate in (
        ("override_settings.forge_additional_modules", explicit_overrides.get("forge_additional_modules")),
        ("params.forge_additional_modules", params.get("forge_additional_modules")),
        ("params.additional_modules", params.get("additional_modules")),
        ("params.modules", params.get("modules")),
    ):
        if candidate is not None and candidate != "" and candidate != []:
            values = candidate
            source = key
            break
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    if not isinstance(values, list):
        return []
    return [(str(item).strip(), source) for item in values if _selected(item)]


def _route_compatible_model(record: dict[str, Any], family: str, loader: str) -> tuple[bool, str]:
    loader_candidates = {str(item) for item in record.get("loader_candidates") or []}
    if loader not in loader_candidates:
        return False, "loader_mismatch"
    exact_family = str(record.get("family") or "") == family and bool(record.get("route_eligible"))
    if exact_family:
        return True, "exact_family_match"
    candidates = {
        str(item.get("family") or "")
        for item in record.get("family_candidates") or []
        if isinstance(item, dict)
    }
    if record.get("classification_status") == "ambiguous" and family in candidates:
        return True, "ambiguous_family_match"
    return False, "family_mismatch"


def _safe_primary_record(record: dict[str, Any] | None, requested: str, source: str, match_state: str) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    resolved_name = _portable_name(record.get("name") or record.get("title") or record.get("model_name") or requested)
    return {
        "neo_role": "primary_model",
        "backend_key": "sd_model_checkpoint",
        "requested": _portable_name(requested),
        "request_source": source,
        "resolved_id": _portable_name(record.get("id") or resolved_name),
        "resolved_name": resolved_name,
        "format": str(record.get("format") or "unknown"),
        "packaging": str(record.get("packaging") or "unknown"),
        "classified_family": str(record.get("family") or ""),
        "classification_status": str(record.get("classification_status") or "unverified"),
        "match_state": match_state,
        "verified": bool(record),
    }


def _safe_module_record(
    record: dict[str, Any] | None,
    *,
    role: str,
    requested: str,
    source: str,
    required: bool,
    match_state: str,
) -> dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    resolved_name = _portable_name(record.get("name") or record.get("model_name") or record.get("filename") or requested)
    return {
        "neo_role": role,
        "provider_role": "additional_module",
        "backend_key": "forge_additional_modules",
        "requested": _portable_name(requested),
        "request_source": source,
        "resolved_id": _portable_name(record.get("id") or resolved_name),
        "resolved_name": resolved_name,
        "format": str(record.get("format") or "unknown"),
        "module_kind": str(record.get("module_kind") or "module"),
        "classified_roles": [str(item) for item in record.get("roles") or []],
        "required": required,
        "match_state": match_state,
        "verified": bool(record),
    }


def forge_loader_translation_contract_payload() -> dict[str, Any]:
    authority = forge_route_authority_payload()
    loaders: dict[str, dict[str, Any]] = {}
    for family in authority.get("families") or []:
        family_id = str(family.get("family_id") or "")
        for loader in family.get("loaders") or []:
            loader_id = str(loader.get("loader_id") or "")
            key = f"{family_id}:{loader_id}"
            loaders[key] = {
                "family": family_id,
                "architecture_id": str(family.get("architecture_id") or ""),
                "neo_loader": loader_id,
                "provider_loader_id": str(loader.get("provider_loader_id") or FORGE_PROVIDER_LOADER_ID),
                "model_formats": list(loader.get("model_formats") or []),
                "primary_model_role": str(loader.get("primary_model_role") or "primary_model"),
                "required_module_roles": list(loader.get("required_module_roles") or []),
                "optional_module_roles": list(loader.get("optional_module_roles") or []),
                "neo_role_translation": dict(loader.get("neo_role_translation") or {}),
            }
    return {
        "schema_id": FORGE_LOADER_TRANSLATION_SCHEMA_ID,
        "version": FORGE_LOADER_TRANSLATION_VERSION,
        "provider_id": "forge",
        "provider_loader_id": FORGE_PROVIDER_LOADER_ID,
        "backend_fields": {
            "primary_model": "override_settings.sd_model_checkpoint",
            "additional_modules": "override_settings.forge_additional_modules",
            "clip_skip": "override_settings.CLIP_stop_at_last_layers",
        },
        "primary_input_aliases": {key: list(values) for key, values in PRIMARY_INPUT_ALIASES.items()},
        "module_role_input_aliases": {key: list(values) for key, values in ROLE_INPUT_ALIASES.items()},
        "runtime_setting_input_aliases": {key: list(values) for key, values in RUNTIME_SETTING_INPUT_ALIASES.items()},
        "generation_parameter_input_aliases": {key: list(values) for key, values in GENERATION_PARAMETER_INPUT_ALIASES.items()},
        "loaders": loaders,
        "policy": {
            "provider_native_model_bundle": True,
            "gguf_is_primary_model_only": True,
            "nunchaku_is_not_gguf": True,
            "required_module_roles_need_explicit_selection": True,
            "translation_does_not_enable_compilers": True,
            "live_inventory_names_are_portable": True,
            "absolute_paths_are_not_serialized": True,
        },
    }


def translate_forge_loader_bundle(
    job: NeoJob,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = dict(job.params or {})
    family = str(job.family or "sdxl").strip() or "sdxl"
    loader = str(job.loader or "checkpoint").strip() or "checkpoint"
    mode = str(job.mode or "txt2img").strip().casefold() or "txt2img"
    mode = {"generate": "txt2img", "image_to_image": "img2img"}.get(mode, mode)
    authority = resolve_forge_route(family, loader, mode)
    classification, has_live_inventory = _classification_inventory(snapshot)
    models = [item for item in classification.get("models") or [] if isinstance(item, dict)]
    modules = [item for item in classification.get("modules") or [] if isinstance(item, dict)]

    warnings: list[str] = []
    bundle_blockers: list[str] = []
    execution_blockers: list[str] = []

    requested_primary, primary_source = _primary_request(job, params, loader)
    primary_match = _matching_record(models, requested_primary) if requested_primary else None
    primary_state = "missing"
    if not requested_primary:
        bundle_blockers.append("missing_primary_model_selection")
    elif primary_match is not None:
        compatible, primary_state = _route_compatible_model(primary_match, family, loader)
        if not compatible:
            bundle_blockers.append(f"primary_model_{primary_state}")
        elif primary_state == "ambiguous_family_match":
            warnings.append("The selected classic checkpoint is ambiguous between SD 1.5 and SDXL; Neo preserves the explicit family route without fabricating stronger architecture evidence.")
        if str(primary_match.get("packaging") or "") == "nunchaku_svdq":
            bundle_blockers.append("nunchaku_primary_model_requires_separate_provider_contract")
        model_format = str(primary_match.get("format") or "unknown")
        if authority.model_formats and model_format not in set(authority.model_formats):
            bundle_blockers.append("primary_model_format_mismatch")
    elif has_live_inventory:
        primary_state = "not_discovered"
        bundle_blockers.append("primary_model_not_discovered_in_selected_profile")
    else:
        primary_state = "unverified_passthrough"
        warnings.append("No live Forge inventory was supplied; the portable primary-model name is passed through for legacy/offline compilation diagnostics.")

    primary_model = _safe_primary_record(primary_match, requested_primary, primary_source, primary_state)

    additional_modules: list[dict[str, Any]] = []
    used_names: set[str] = set()
    required_roles = list(authority.required_module_roles)
    optional_roles = list(authority.optional_module_roles)
    for role in [*required_roles, *optional_roles]:
        requested, source = _role_request(params, role)
        required = role in required_roles
        if not requested:
            if required:
                bundle_blockers.append(f"missing_required_module_selection:{role}")
            continue
        record = _matching_record(modules, requested)
        match_state = "exact_role_match"
        if record is not None:
            classified_roles = {str(item) for item in record.get("roles") or []}
            if role not in classified_roles:
                match_state = "role_mismatch"
                if required:
                    bundle_blockers.append(f"module_role_mismatch:{role}")
                else:
                    warnings.append(f"Optional module selected for {role} was discovered but did not classify for that role.")
        elif has_live_inventory:
            match_state = "not_discovered"
            if required:
                bundle_blockers.append(f"required_module_not_discovered:{role}")
            else:
                warnings.append(f"Optional module selected for {role} was not discovered in the selected Forge profile.")
        else:
            match_state = "unverified_passthrough"
            warnings.append(f"Module selected for {role} was translated without live Forge inventory verification.")
        safe = _safe_module_record(
            record,
            role=role,
            requested=requested,
            source=source,
            required=required,
            match_state=match_state,
        )
        resolved_key = _identity_token(safe.get("resolved_name"))
        if resolved_key and resolved_key not in used_names:
            additional_modules.append(safe)
            used_names.add(resolved_key)

    for requested, source in _generic_module_requests(params):
        record = _matching_record(modules, requested)
        match_state = "discovered_generic_module" if record is not None else "not_discovered" if has_live_inventory else "unverified_passthrough"
        if record is None and has_live_inventory:
            warnings.append("A generic Forge additional module selection was not discovered in the selected profile.")
        safe = _safe_module_record(
            record,
            role="additional_module",
            requested=requested,
            source=source,
            required=False,
            match_state=match_state,
        )
        resolved_key = _identity_token(safe.get("resolved_name"))
        if resolved_key and resolved_key not in used_names:
            additional_modules.append(safe)
            used_names.add(resolved_key)

    runtime_settings: dict[str, Any] = {}
    for backend_key, aliases in RUNTIME_SETTING_INPUT_ALIASES.items():
        value = _first(params, *aliases)
        if value not in {None, ""}:
            runtime_settings[backend_key] = value

    generation_parameters: dict[str, Any] = {}
    translated_targets = set(authority.neo_role_translation.values())
    if "generation_parameter" in translated_targets:
        for canonical_key, aliases in GENERATION_PARAMETER_INPUT_ALIASES.items():
            value = _first(params, *aliases)
            if value not in {None, ""}:
                generation_parameters[canonical_key] = value

    compiler_ready = bool(
        authority.state in _SELECTABLE_AUTHORITY_STATES
        and authority.compiler_id
        and authority.compiler_id in FORGE_WORKFLOW_COMPILER_IDS
    )
    if authority.state not in _SELECTABLE_AUTHORITY_STATES:
        execution_blockers.append(f"authority_state:{authority.state}")
    if not authority.compiler_id:
        execution_blockers.append("forge_compiler_not_implemented")
    elif authority.compiler_id not in FORGE_WORKFLOW_COMPILER_IDS:
        execution_blockers.append(f"unsupported_forge_compiler:{authority.compiler_id}")

    override_settings: dict[str, Any] = {}
    if primary_model.get("resolved_name"):
        override_settings["sd_model_checkpoint"] = primary_model["resolved_name"]
    module_names = [str(item.get("resolved_name") or "") for item in additional_modules if str(item.get("resolved_name") or "").strip()]
    if module_names:
        override_settings["forge_additional_modules"] = module_names
    if "CLIP_stop_at_last_layers" in runtime_settings:
        override_settings["CLIP_stop_at_last_layers"] = runtime_settings["CLIP_stop_at_last_layers"]

    bundle_ready = not bundle_blockers
    return {
        "schema_id": FORGE_LOADER_TRANSLATION_SCHEMA_ID,
        "version": FORGE_LOADER_TRANSLATION_VERSION,
        "provider_id": "forge",
        "provider_loader_id": authority.provider_loader_id or FORGE_PROVIDER_LOADER_ID,
        "family": family,
        "architecture_id": authority.architecture_id,
        "loader": loader,
        "mode": mode,
        "authority_state": authority.state,
        "compiler_id": authority.compiler_id,
        "primary_model": primary_model,
        "additional_modules": additional_modules,
        "runtime_settings": runtime_settings,
        "generation_parameters": generation_parameters,
        "override_settings": override_settings,
        "required_module_roles": required_roles,
        "optional_module_roles": optional_roles,
        "neo_role_translation": dict(authority.neo_role_translation),
        "bundle_ready": bundle_ready,
        "compiler_ready": compiler_ready,
        "executable": bool(bundle_ready and compiler_ready),
        "bundle_blockers": list(dict.fromkeys(bundle_blockers)),
        "execution_blockers": list(dict.fromkeys(execution_blockers)),
        "blockers": list(dict.fromkeys([*bundle_blockers, *execution_blockers])),
        "warnings": list(dict.fromkeys(warnings)),
        "inventory_verified": has_live_inventory,
        "policy": {
            "translation_requires_route_owned_compiler": True,
            "required_roles_need_explicit_selection": True,
            "gguf_primary_model_only": loader == "gguf",
            "provider_override_settings_are_portable": True,
        },
    }
