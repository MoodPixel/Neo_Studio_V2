"""ControlNet family capability registry.

Phase 6 keeps the family-aware selector and intent-bound model filtering, then
adds Krea 2 RAW Canny through the NK2E in-context adapter alongside the existing
Depth, Composition / Silhouette, and Turbo OpenPose adapters without leaking one
adapter's nodes, settings, or LoRA catalog into another intent. The registry remains backend-owned; preprocessors,
installed LoRAs, and custom nodes may satisfy runtime requirements but cannot
invent a Neo product capability.

The registry separates product capability from runtime discovery. A preprocessor,
LoRA, or custom node existing on disk does not itself create a Neo capability.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_ID = "neo.image.controlnet.capability_registry.v1"
SCHEMA_VERSION = 1
EXTENSION_ID = "image.controlnet"
REGISTRY_PATH = Path(__file__).with_name("capability_registry_data.json")

IMPLEMENTED = "implemented"
PLANNED = "planned"
UNSUPPORTED = "unsupported"
KNOWN_CAPABILITY_STATES = {IMPLEMENTED, PLANNED, UNSUPPORTED}
ACTIVE_MATURITY_STATES = {"available", "experimental_available"}

BACKEND_ALIASES = {
    "comfy": "comfyui",
    "comfyui_local": "comfyui",
    "comfyui_portable": "comfyui_portable",
}
MODE_ALIASES = {
    "txt2img": "generate",
    "text_to_image": "generate",
    "image_to_image": "img2img",
}


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_backend(value: Any) -> str:
    raw = _clean(value)
    return BACKEND_ALIASES.get(raw, raw)


def normalize_mode(value: Any) -> str:
    raw = _clean(value)
    return MODE_ALIASES.get(raw, raw)


def normalize_family(value: Any) -> str:
    return _clean(value).replace("-", "_").replace(" ", "_")


def normalize_loader(value: Any) -> str:
    return _clean(value).replace("-", "_")


def normalize_task(value: Any) -> str:
    raw = _clean(value)
    return raw if raw in {"map_control", "inpaint_control", "outpaint_control"} else "map_control"


def _ensure_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def validate_registry_payload(payload: dict[str, Any]) -> list[str]:
    """Return structural registry errors without depending on jsonschema."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["registry payload must be an object"]
    if payload.get("schema_id") != SCHEMA_ID:
        errors.append(f"schema_id must be {SCHEMA_ID}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("extension_id") != EXTENSION_ID:
        errors.append(f"extension_id must be {EXTENSION_ID}")
    if payload.get("behavioral_activation") is not True:
        errors.append("ControlNet capability registry must be behaviorally active (behavioral_activation=true)")

    intents = payload.get("intents")
    families = payload.get("families")
    if not isinstance(intents, dict) or not intents:
        errors.append("intents must be a non-empty object")
        intents = {}
    if not isinstance(families, dict) or not families:
        errors.append("families must be a non-empty object")
        families = {}

    intent_ids = set(intents)
    for intent_id, record in intents.items():
        if not isinstance(record, dict):
            errors.append(f"intent {intent_id} must be an object")
            continue
        if not str(record.get("label") or "").strip():
            errors.append(f"intent {intent_id} needs a label")
        preprocessors = _ensure_list(record.get("preprocessors"))
        if not preprocessors:
            errors.append(f"intent {intent_id} needs preprocessors")
        if record.get("default_preprocessor") not in preprocessors:
            errors.append(f"intent {intent_id} default_preprocessor must be in preprocessors")
        model_bindings = record.get("model_bindings")
        if model_bindings is not None and not isinstance(model_bindings, dict):
            errors.append(f"intent {intent_id} model_bindings must be an object")
        elif isinstance(model_bindings, dict):
            for adapter, binding in model_bindings.items():
                if not isinstance(binding, dict):
                    errors.append(f"intent {intent_id} model binding {adapter} must be an object")
                    continue
                for key in ("selection_kind", "selector_label", "catalog_source", "catalog_kind", "compatibility_mode"):
                    if not str(binding.get(key) or "").strip():
                        errors.append(f"intent {intent_id} model binding {adapter} needs {key}")
                if not isinstance(binding.get("strict"), bool):
                    errors.append(f"intent {intent_id} model binding {adapter} strict must be boolean")
                if binding.get("compatibility_mode") not in {"route_catalog", "catalog_membership", "filename_tokens"}:
                    errors.append(f"intent {intent_id} model binding {adapter} has unsupported compatibility_mode")
                for key in ("required_all_tokens", "required_any_tokens", "exclude_tokens"):
                    if key in binding and not isinstance(binding.get(key), list):
                        errors.append(f"intent {intent_id} model binding {adapter} {key} must be a list")

    for family_id, record in families.items():
        if not isinstance(record, dict):
            errors.append(f"family {family_id} must be an object")
            continue
        groups = {
            IMPLEMENTED: _ensure_list(record.get("implemented_intents")),
            PLANNED: _ensure_list(record.get("planned_intents")),
            UNSUPPORTED: _ensure_list(record.get("unsupported_intents")),
        }
        seen: dict[str, str] = {}
        for state, ids in groups.items():
            for intent_id in ids:
                if intent_id not in intent_ids:
                    errors.append(f"family {family_id} references unknown intent {intent_id}")
                previous = seen.get(intent_id)
                if previous and previous != state:
                    errors.append(f"family {family_id} intent {intent_id} appears in both {previous} and {state}")
                seen[intent_id] = state
        routes = _ensure_list(record.get("routes"))
        if groups[IMPLEMENTED] and not routes:
            errors.append(f"family {family_id} has implemented intents but no executable route records")
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                errors.append(f"family {family_id} route {index} must be an object")
                continue
            for key in ("backends", "loaders", "modes", "tasks", "required_node_roles", "settings"):
                if not isinstance(route.get(key), list):
                    errors.append(f"family {family_id} route {index} {key} must be a list")
            route_intents = route.get("intents")
            if route_intents is not None:
                if not isinstance(route_intents, list) or not route_intents:
                    errors.append(f"family {family_id} route {index} intents must be a non-empty list when declared")
                else:
                    for intent_id in route_intents:
                        if intent_id not in groups[IMPLEMENTED]:
                            errors.append(f"family {family_id} route {index} intent {intent_id} must be listed in implemented_intents")
            if not str(route.get("adapter") or "").strip():
                errors.append(f"family {family_id} route {index} needs adapter")
            if route.get("maturity") not in ACTIVE_MATURITY_STATES:
                errors.append(f"family {family_id} route {index} maturity must be available or experimental_available")
    return errors


@lru_cache(maxsize=1)
def _load_registry_cached() -> dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    errors = validate_registry_payload(payload)
    if errors:
        raise ValueError("Invalid ControlNet capability registry: " + "; ".join(errors))
    return payload


def load_registry() -> dict[str, Any]:
    return deepcopy(_load_registry_cached())


def capability_registry_payload() -> dict[str, Any]:
    return load_registry()


def family_capability(family: Any) -> dict[str, Any]:
    key = normalize_family(family)
    registry = _load_registry_cached()
    record = (registry.get("families") or {}).get(key)
    if not isinstance(record, dict):
        return {
            "family": key,
            "known": False,
            "label": key or "Unknown",
            "implemented_intents": [],
            "planned_intents": [],
            "unsupported_intents": [],
            "routes": [],
            "max_active_units": None,
        }
    return {"family": key, "known": True, **deepcopy(record)}


def intent_state(family: Any, intent: Any) -> str:
    record = family_capability(family)
    intent_id = normalize_family(intent)
    if intent_id in record.get("implemented_intents", []):
        return IMPLEMENTED
    if intent_id in record.get("planned_intents", []):
        return PLANNED
    if intent_id in record.get("unsupported_intents", []):
        return UNSUPPORTED
    return UNSUPPORTED


def _route_matches(route: dict[str, Any], *, backend: str, loader: str, mode: str, task: str, method: str = "") -> bool:
    if backend not in _ensure_list(route.get("backends")):
        return False
    if loader not in _ensure_list(route.get("loaders")):
        return False
    if mode not in _ensure_list(route.get("modes")):
        return False
    if task not in _ensure_list(route.get("tasks")):
        return False
    route_method = _clean(route.get("method"))
    if route_method and route_method != _clean(method):
        return False
    return True


def _route_supports_intent(route: dict[str, Any] | None, intent: Any, family_intents: list[str] | None = None) -> bool:
    route_data = route if isinstance(route, dict) else {}
    intent_id = normalize_family(intent)
    declared = _ensure_list(route_data.get("intents"))
    if declared:
        return intent_id in {normalize_family(item) for item in declared}
    return intent_id in {normalize_family(item) for item in (family_intents or [])}


def _route_for_intent(resolved: dict[str, Any] | None, intent: Any) -> dict[str, Any]:
    data = resolved if isinstance(resolved, dict) else {}
    family_intents = list(data.get("implemented_intents") or [])
    for route in data.get("matching_routes") or []:
        if isinstance(route, dict) and _route_supports_intent(route, intent, family_intents):
            return route
    return {}


def resolve_route_capability(
    *,
    family: Any,
    loader: Any,
    mode: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
) -> dict[str, Any]:
    """Resolve static Neo implementation truth for a route.

    This intentionally does not inspect live Comfy nodes or model catalogs. Runtime
    availability remains a separate discovery layer for Phase 2+.
    """
    fam = family_capability(family)
    normalized = {
        "backend": normalize_backend(backend),
        "family": normalize_family(family),
        "loader": normalize_loader(loader),
        "mode": normalize_mode(mode),
        "task": normalize_task(task),
        "method": _clean(method),
    }
    matches = [
        deepcopy(route)
        for route in fam.get("routes", [])
        if _route_matches(
            route,
            backend=normalized["backend"],
            loader=normalized["loader"],
            mode=normalized["mode"],
            task=normalized["task"],
            method=normalized["method"],
        )
    ]
    family_implemented = list(fam.get("implemented_intents") or [])
    matched_intents: list[str] = []
    for intent_id in family_implemented:
        if any(_route_supports_intent(route, intent_id, family_implemented) for route in matches):
            matched_intents.append(intent_id)
    implemented = bool(matches and matched_intents)
    return {
        **normalized,
        "known_family": bool(fam.get("known")),
        "implemented": implemented,
        "implementation_state": IMPLEMENTED if implemented else UNSUPPORTED,
        "implemented_intents": matched_intents if implemented else [],
        "planned_intents": list(fam.get("planned_intents") or []),
        "unsupported_intents": list(fam.get("unsupported_intents") or []),
        "max_active_units": fam.get("max_active_units"),
        "matching_routes": matches,
        "registry_behavioral_activation": True,
    }


def implemented_intent_options(
    *,
    family: Any,
    loader: Any,
    mode: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
) -> list[dict[str, Any]]:
    """Return UI-ready implemented options for the active declared route."""
    resolved = resolve_route_capability(
        family=family,
        loader=loader,
        mode=mode,
        task=task,
        backend=backend,
        method=method,
    )
    if not resolved.get("implemented"):
        return []
    registry = _load_registry_cached()
    intent_map = registry.get("intents") or {}
    options: list[dict[str, Any]] = []
    for intent_id in resolved.get("implemented_intents") or []:
        meta = intent_map.get(intent_id) if isinstance(intent_map.get(intent_id), dict) else {}
        route = _route_for_intent(resolved, intent_id)
        options.append({
            "id": intent_id,
            "label": meta.get("label") or intent_id,
            "state": IMPLEMENTED,
            "default_preprocessor": meta.get("default_preprocessor"),
            "preprocessors": list(meta.get("preprocessors") or []),
            "adapter": str(route.get("adapter") or ""),
            "maturity": str(route.get("maturity") or "available"),
            "settings": list(route.get("settings") or []),
            "required_node_roles": list(route.get("required_node_roles") or []),
            "catalog_kind": str(route.get("catalog_kind") or "controlnet"),
        })
    return options



INTENT_ALIASES = {
    "normal": "normalbae",
    "normal_bae": "normalbae",
    "dwpose": "openpose",
    "open_pose": "openpose",
    "pose": "openpose",
    "hed": "softedge",
    "soft_edge": "softedge",
    "lineart_anime": "lineart_anime",
    "anime_lineart": "lineart_anime",
}


def normalize_intent(value: Any) -> str:
    raw = normalize_family(value)
    return INTENT_ALIASES.get(raw, raw)


def control_intent_from_unit(unit: dict[str, Any] | None) -> str:
    """Resolve a ControlNet product intent from a normalized/legacy unit.

    Old payloads may persist ``unit=auto`` while carrying the actual map type in
    ``preprocessor``. Phase 2 never treats ``auto`` as a capability; it resolves
    it to the concrete preprocessor intent or returns an empty string.
    """
    source = unit if isinstance(unit, dict) else {}
    raw = normalize_intent(source.get("unit"))
    if raw in {"", "auto", "none"}:
        raw = normalize_intent(source.get("preprocessor"))
    return "" if raw in {"", "auto", "none"} else raw


def _portable_catalog_name(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _catalog_key(value: Any) -> str:
    return _portable_catalog_name(value).casefold()


def _dedupe_catalog(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        portable = _portable_catalog_name(value)
        key = portable.casefold()
        if not portable or key in seen:
            continue
        seen.add(key)
        result.append(portable)
    return result


def _catalog_tokens(value: Any) -> set[str]:
    # ``filename_tokens`` is deliberately basename-only. Folder names such as
    # "Edit and Control Loras" must not make an unrelated LoRA look like a
    # Control LoRA merely because it is stored under a shared control folder.
    portable = _portable_catalog_name(value)
    filename = portable.rsplit("/", 1)[-1]
    return set(re.findall(r"[a-z0-9]+", filename.casefold()))


def _default_model_binding(route: dict[str, Any] | None) -> dict[str, Any]:
    route_data = route if isinstance(route, dict) else {}
    adapter = str(route_data.get("adapter") or "").strip()
    catalog_kind = str(route_data.get("catalog_kind") or "controlnet").strip() or "controlnet"
    if adapter == "qwen_2511_pose_transfer":
        selection_kind = "pose_lora_bundle"
        selector_label = "Pose LoRA"
    elif "lora" in catalog_kind.casefold():
        selection_kind = "control_lora"
        selector_label = "Control LoRA"
    else:
        selection_kind = "controlnet_model"
        selector_label = "Model"
    return {
        "adapter": adapter,
        "catalog_kind": catalog_kind,
        "catalog_source": "controlnet.model_inputs",
        "selection_kind": selection_kind,
        "selector_label": selector_label,
        "compatibility_mode": "route_catalog",
        "strict": False,
        "intent_filtered": False,
        "required_all_tokens": [],
        "required_any_tokens": [],
        "exclude_tokens": [],
    }


def intent_model_binding_from_resolved(resolved: dict[str, Any] | None, intent: Any) -> dict[str, Any]:
    """Resolve the model/LoRA binding for one intent + active execution adapter.

    Specialized shared catalogs (currently Krea2 models/loras) may declare a
    strict intent-specific filename contract in the registry. Dedicated
    ControlNet catalogs default to route-catalog behavior and are not guessed
    from filenames.
    """
    data = resolved if isinstance(resolved, dict) else {}
    intent_id = normalize_intent(intent)
    route = _route_for_intent(data, intent_id)
    binding = _default_model_binding(route)
    registry = _load_registry_cached()
    intent_meta = (registry.get("intents") or {}).get(intent_id)
    declared = (intent_meta.get("model_bindings") or {}).get(binding.get("adapter")) if isinstance(intent_meta, dict) and isinstance(intent_meta.get("model_bindings"), dict) else None
    if isinstance(declared, dict):
        binding.update(deepcopy(declared))
        binding["intent_filtered"] = binding.get("compatibility_mode") != "route_catalog"
    binding["intent"] = intent_id
    return binding


def filter_compatible_models(values: list[Any], binding: dict[str, Any] | None) -> list[str]:
    """Filter a provider catalog according to a resolved intent binding."""
    catalog = _dedupe_catalog(values)
    policy = binding if isinstance(binding, dict) else {}
    mode = str(policy.get("compatibility_mode") or "route_catalog").strip().lower()
    if mode in {"route_catalog", "catalog_membership"}:
        return catalog
    if mode != "filename_tokens":
        return [] if policy.get("strict") else catalog
    required_all = {_clean(item) for item in policy.get("required_all_tokens") or [] if _clean(item)}
    required_any = {_clean(item) for item in policy.get("required_any_tokens") or [] if _clean(item)}
    excluded = {_clean(item) for item in policy.get("exclude_tokens") or [] if _clean(item)}
    result: list[str] = []
    for value in catalog:
        tokens = _catalog_tokens(value)
        if required_all and not required_all.issubset(tokens):
            continue
        if required_any and not tokens.intersection(required_any):
            continue
        if excluded and tokens.intersection(excluded):
            continue
        result.append(value)
    return result


def _catalog_values_for_binding(node_status: dict[str, Any] | None, binding: dict[str, Any]) -> list[str]:
    status = node_status if isinstance(node_status, dict) else {}
    source = str(binding.get("catalog_source") or "").strip()
    if source == "krea2_control.lora_models":
        krea = status.get("krea2_control") if isinstance(status.get("krea2_control"), dict) else {}
        return _dedupe_catalog(list(krea.get("lora_models") or []))
    if source == "krea2_control_plus.lora_models":
        krea = status.get("krea2_control_plus") if isinstance(status.get("krea2_control_plus"), dict) else {}
        return _dedupe_catalog(list(krea.get("lora_models") or []))
    if source == "krea2_ostris.lora_models":
        krea = status.get("krea2_ostris") if isinstance(status.get("krea2_ostris"), dict) else {}
        return _dedupe_catalog(list(krea.get("lora_models") or []))
    if source == "krea2_nk2e.lora_models":
        krea = status.get("krea2_nk2e") if isinstance(status.get("krea2_nk2e"), dict) else {}
        return _dedupe_catalog(list(krea.get("lora_models") or []))
    if binding.get("selection_kind") == "pose_lora_bundle":
        return []
    model_inputs = status.get("model_inputs") if isinstance(status.get("model_inputs"), dict) else {}
    values: list[Any] = []
    for rows in model_inputs.values():
        if isinstance(rows, list):
            values.extend(rows)
    return _dedupe_catalog(values)


def resolve_intent_model_catalog(
    *,
    family: Any,
    loader: Any,
    mode: Any,
    intent: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_route_capability(
        family=family, loader=loader, mode=mode, task=task, backend=backend, method=method
    )
    intent_id = normalize_intent(intent)
    binding = intent_model_binding_from_resolved(resolved, intent_id)
    raw_catalog = _catalog_values_for_binding(node_status, binding)
    compatible = filter_compatible_models(raw_catalog, binding)
    checked = bool((node_status or {}).get("object_info_present"))
    return {
        "intent": intent_id,
        "binding": binding,
        "catalog_checked": checked,
        "catalog_count": len(raw_catalog),
        "compatible_count": len(compatible),
        "compatible_models": compatible,
    }


def validate_model_selection_for_route(
    selected: Any,
    *,
    family: Any,
    loader: Any,
    mode: Any,
    intent: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a selected control model against the resolved intent binding.

    Strict intent filtering is only enforced once live object_info has been
    checked. This avoids inventing compatibility when the provider catalog is
    unavailable while still blocking ordinary Krea style/character LoRAs once
    the shared models/loras enum is known.
    """
    model = _portable_catalog_name(selected)
    catalog = resolve_intent_model_catalog(
        family=family, loader=loader, mode=mode, intent=intent, task=task, backend=backend, method=method, node_status=node_status
    )
    binding = catalog.get("binding") if isinstance(catalog.get("binding"), dict) else {}
    if not model:
        return {**catalog, "model": model, "valid": False, "status": "missing", "reason": "No control model is selected."}
    if not binding.get("strict") or not catalog.get("catalog_checked"):
        return {**catalog, "model": model, "valid": True, "status": "unverified" if not catalog.get("catalog_checked") else "route_catalog", "reason": "No strict intent-specific model filter applies at this boundary."}
    model_key = _catalog_key(model)
    raw_catalog = _catalog_values_for_binding(node_status, binding)
    catalog_keys = {_catalog_key(item) for item in raw_catalog}
    if model_key not in catalog_keys:
        return {
            **catalog,
            "model": model,
            "valid": False,
            "status": "missing_from_catalog",
            "reason": "Selected control model is not present in the active provider catalog.",
        }
    compatible_keys = {_catalog_key(item) for item in catalog.get("compatible_models") or []}
    if model_key in compatible_keys:
        return {**catalog, "model": model, "valid": True, "status": "compatible", "reason": "Selected control model matches the active intent binding."}
    return {
        **catalog,
        "model": model,
        "valid": False,
        "status": "incompatible",
        "reason": f"Selected control model is installed but is not compatible with the active {catalog.get('intent') or 'ControlNet'} intent binding.",
    }


def sanitize_unit_for_route(
    unit: dict[str, Any] | None,
    *,
    family: Any,
    loader: Any,
    mode: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy whose intent/preprocessor cannot escape route capability.

    The first implemented option is the deterministic fallback. Unknown or
    unsupported routes deliberately return ``valid=False`` with no fallback.
    """
    source = deepcopy(unit) if isinstance(unit, dict) else {}
    options = implemented_intent_options(
        family=family, loader=loader, mode=mode, task=task, backend=backend, method=method
    )
    allowed = [str(item.get("id") or "") for item in options if str(item.get("id") or "")]
    requested = control_intent_from_unit(source)
    if not allowed:
        changed = bool(source.get("unit") or normalize_intent(source.get("preprocessor")) not in {"", "none"})
        source["unit"] = ""
        source["preprocessor"] = "none"
        source["pose_method"] = "controlnet"
        return {"unit": source, "valid": False, "changed": changed, "requested_intent": requested, "allowed_intents": []}
    selected = requested if requested in allowed else allowed[0]
    meta = next((item for item in options if item.get("id") == selected), {})
    preprocessors = list(meta.get("preprocessors") or [])
    default_preprocessor = str(meta.get("default_preprocessor") or selected)
    current_preprocessor = normalize_intent(source.get("preprocessor"))
    preprocessor = current_preprocessor if current_preprocessor in preprocessors else default_preprocessor
    changed = source.get("unit") != selected or source.get("preprocessor") != preprocessor
    source["unit"] = selected
    source["preprocessor"] = preprocessor
    if selected != "openpose":
        source["pose_method"] = "controlnet"

    model_binding_status: dict[str, Any] | None = None
    selected_model = _portable_catalog_name(source.get("model"))
    if selected_model and isinstance(node_status, dict):
        model_binding_status = validate_model_selection_for_route(
            selected_model,
            family=family,
            loader=loader,
            mode=mode,
            intent=selected,
            task=task,
            backend=backend,
            method=method,
            node_status=node_status,
        )
        if model_binding_status.get("catalog_checked") and not model_binding_status.get("valid"):
            source["model"] = ""
            changed = True

    return {
        "unit": source,
        "valid": True,
        "changed": changed,
        "requested_intent": requested,
        "selected_intent": selected,
        "allowed_intents": allowed,
        "default_preprocessor": default_preprocessor,
        "model_binding_status": deepcopy(model_binding_status) if model_binding_status else None,
    }


def runtime_intent_readiness(
    *,
    resolved: dict[str, Any] | None,
    intent: Any,
    family: Any,
    task: Any = "map_control",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve runtime readiness for the adapter that owns one control intent."""
    status = node_status if isinstance(node_status, dict) else {}
    checked = bool(status.get("object_info_present"))
    route = _route_for_intent(resolved, intent)
    adapter = str(route.get("adapter") or "")
    if not checked:
        return {"checked": False, "ready": True, "state": "unchecked", "adapter": adapter, "reason": "Live backend node discovery has not been checked yet."}

    if adapter == "krea2_native_control_lora":
        item = status.get("krea2_control") if isinstance(status.get("krea2_control"), dict) else {}
        ready = bool(item.get("available"))
        reason = "Krea2ControlLoRALoader, Krea2ControlImageEncode, and Krea2ControlApply are required."
    elif adapter == "krea2_control_plus":
        item = status.get("krea2_control_plus") if isinstance(status.get("krea2_control_plus"), dict) else {}
        ready = bool(item.get("available"))
        reason = "Krea2ControlPlusLoRALoader, Krea2ControlPlusImageEncode, and Krea2ControlPlusApply are required."
    elif adapter == "krea2_ostris_openpose":
        item = status.get("krea2_ostris") if isinstance(status.get("krea2_ostris"), dict) else {}
        ready = bool(item.get("available"))
        reason = "TextEncodeKrea2OstrisEdit, Krea2OstrisEditModelPatch, and LoraLoaderModelOnly are required."
    elif adapter == "krea2_nk2e_canny":
        item = status.get("krea2_nk2e") if isinstance(status.get("krea2_nk2e"), dict) else {}
        ready = bool(item.get("available"))
        reason = "NK2EInContextModelNode, NK2ESetReferenceNode, LoraLoaderModelOnly, and VAEEncode are required."
    else:
        fallback = runtime_route_readiness(
            family=family, task=task, method=method, node_status=status, object_info=object_info
        )
        return {**fallback, "adapter": adapter}
    return {"checked": True, "ready": ready, "state": "available" if ready else "provider_gated", "adapter": adapter, "reason": reason}


def runtime_route_readiness(
    *,
    family: Any,
    task: Any = "map_control",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve route-level runtime readiness without creating capabilities.

    ``checked=False`` means the backend has not supplied node discovery yet; in
    that state Phase 2 keeps statically implemented options visible. A concrete
    missing-node result hides them and reports ``provider_gated``.
    """
    status = node_status if isinstance(node_status, dict) else {}
    family_id = normalize_family(family)
    task_id = normalize_task(task)
    method_id = _clean(method)
    checked = bool(status.get("object_info_present"))
    if not checked:
        return {"checked": False, "ready": True, "state": "unchecked", "reason": "Live backend node discovery has not been checked yet."}

    ready = True
    reason = "Runtime contract is available."
    if family_id in {"krea2", "krea2_turbo"}:
        ready = bool((status.get("krea2_control") or {}).get("available"))
        reason = "Krea2ControlLoRALoader, Krea2ControlImageEncode, and Krea2ControlApply are required."
    elif family_id == "flux2_klein":
        ready = bool((status.get("flux2_klein") or {}).get("available"))
        reason = "FLUX.2 Klein ControlNet loader/apply nodes are required."
    elif family_id == "flux":
        ready = bool((status.get("flux") or {}).get("available"))
        reason = "Flux ControlNet loader/apply nodes are required."
    elif family_id in {"qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509"}:
        qwen = status.get("qwen") or {}
        if task_id == "map_control":
            ready = bool(qwen.get("instantx_available") or status.get("base_available"))
            reason = "Qwen map control needs InstantX/native or standard ControlNet loader/apply nodes."
        else:
            ready = bool(qwen.get("diffsynth_available") or qwen.get("instantx_available"))
            reason = "Qwen mask/canvas control needs DiffSynth or InstantX ControlNet nodes."
    elif family_id == "qwen_image_edit_2511" and method_id == "qwen_transfer":
        preprocessors = status.get("preprocessors") if isinstance(status.get("preprocessors"), dict) else {}
        pose_nodes = preprocessors.get("openpose") if isinstance(preprocessors.get("openpose"), list) else []
        names = set(str(key) for key in (object_info or {}).keys()) if isinstance(object_info, dict) else set()
        ready = bool(pose_nodes and "LoraLoaderModelOnly" in names)
        reason = "Qwen 2511 Pose Transfer needs DWPose/OpenPose preprocessing and LoraLoaderModelOnly."
    elif family_id in {"sdxl", "sd15"}:
        ready = bool(status.get("base_available"))
        reason = "Standard ControlNetLoader and ControlNetApply/Advanced nodes are required."
    else:
        ready = False
        reason = "No runtime adapter is declared for this family."
    return {"checked": True, "ready": ready, "state": "available" if ready else "provider_gated", "reason": reason}


def resolve_ui_capability(
    *,
    family: Any,
    loader: Any,
    mode: Any,
    task: Any = "map_control",
    backend: Any = "comfyui",
    method: Any = "",
    node_status: dict[str, Any] | None = None,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Phase 2 selector/gating payload for one active route."""
    resolved = resolve_route_capability(
        family=family, loader=loader, mode=mode, task=task, backend=backend, method=method
    )
    options = implemented_intent_options(
        family=family, loader=loader, mode=mode, task=task, backend=backend, method=method
    )

    def _with_model_binding(option: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(option)
        intent_id = str(option.get("id") or "")
        catalog = resolve_intent_model_catalog(
            family=family, loader=loader, mode=mode, intent=intent_id, task=task, backend=backend, method=method, node_status=node_status
        )
        readiness = runtime_intent_readiness(
            resolved=resolved, intent=intent_id, family=family, task=task, method=method, node_status=node_status, object_info=object_info
        )
        enriched["model_binding"] = deepcopy(catalog.get("binding") or {})
        enriched["model_catalog_checked"] = bool(catalog.get("catalog_checked"))
        enriched["model_catalog_count"] = int(catalog.get("catalog_count") or 0)
        enriched["compatible_model_count"] = int(catalog.get("compatible_count") or 0)
        enriched["compatible_models"] = list(catalog.get("compatible_models") or [])
        enriched["runtime"] = readiness
        return enriched

    bound_options = [_with_model_binding(option) for option in options]
    visible = [option for option in bound_options if resolved.get("implemented") and (option.get("runtime") or {}).get("ready")]
    checked = bool((node_status or {}).get("object_info_present"))
    runtime = {
        "checked": checked,
        "ready": bool(visible) if checked else bool(bound_options),
        "state": "available" if visible else ("unchecked" if not checked else "provider_gated"),
        "reason": "At least one implemented ControlNet intent is runtime-ready." if visible else ("Live backend node discovery has not been checked yet." if not checked else "No implemented ControlNet intent has all required runtime nodes."),
    }
    return {
        "schema_version": "neo.image.controlnet.capability_ui.v6",
        "behavioral_activation": True,
        "route": {key: resolved.get(key) for key in ("backend", "family", "loader", "mode", "task", "method")},
        "known_family": bool(resolved.get("known_family")),
        "implemented": bool(resolved.get("implemented")),
        "implementation_state": resolved.get("implementation_state"),
        "options": visible,
        "static_options": bound_options,
        "planned_intents": list(resolved.get("planned_intents") or []),
        "unsupported_intents": list(resolved.get("unsupported_intents") or []),
        "max_active_units": resolved.get("max_active_units"),
        "matching_routes": deepcopy(resolved.get("matching_routes") or []),
        "runtime": runtime,
        "reason": runtime.get("reason") if resolved.get("implemented") else "No implemented ControlNet adapter matches this route.",
    }

def audit_summary() -> dict[str, Any]:
    registry = _load_registry_cached()
    families = registry.get("families") or {}
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "behavioral_activation": True,
        "family_count": len(families),
        "implemented_family_count": sum(1 for item in families.values() if item.get("implemented_intents")),
        "planned_intent_count": sum(len(item.get("planned_intents") or []) for item in families.values()),
        "implemented_intent_assignments": sum(len(item.get("implemented_intents") or []) for item in families.values()),
        "audit_finding_count": len(registry.get("audit_findings") or []),
        "unknown_family_fallback": ((registry.get("ui_policy") or {}).get("unknown_family_fallback") or "empty"),
    }


__all__ = [
    "ACTIVE_MATURITY_STATES",
    "EXTENSION_ID",
    "IMPLEMENTED",
    "INTENT_ALIASES",
    "KNOWN_CAPABILITY_STATES",
    "PLANNED",
    "REGISTRY_PATH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "UNSUPPORTED",
    "audit_summary",
    "capability_registry_payload",
    "family_capability",
    "implemented_intent_options",
    "control_intent_from_unit",
    "filter_compatible_models",
    "intent_model_binding_from_resolved",
    "intent_state",
    "load_registry",
    "normalize_backend",
    "normalize_family",
    "normalize_intent",
    "normalize_loader",
    "normalize_mode",
    "normalize_task",
    "resolve_route_capability",
    "resolve_intent_model_catalog",
    "resolve_ui_capability",
    "runtime_intent_readiness",
    "runtime_route_readiness",
    "sanitize_unit_for_route",
    "validate_model_selection_for_route",
    "validate_registry_payload",
]
