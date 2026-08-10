from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping
import json

from neo_app.image.sampling_guidance_registry import (
    normalize_family,
    normalize_loader,
    normalize_mode,
    resolve_sampling_guidance_capability,
)
from neo_app.image.output_intents import normalize_output_intent_id


SCHEMA = "neo.image.sampling_presets.builtin.v1"
PHASE = "IP-5"
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "models" / "sampling_presets_builtin.json"
USER_PRESET_NAMESPACE = "neo_data/image/sampling_presets"
USER_PRESET_AUTHORING_PHASE = "IP-7"

PROVIDER_DEFAULTS_ID = "provider_defaults"
EMPTY_CLEAN_SLATE_ID = "empty_clean_slate"
DEFAULT_BALANCED_ID = "default_balanced"

PRESET_METADATA_FIELDS = {
    "sampling_preset_id",
    "sampling_preset_entry_id",
    "sampling_preset_source",
    "sampling_preset_application_mode",
    "sampling_preset_read_only",
    "sampling_preset_authoring_template",
    "sampling_preset_context",
    "sampling_preset_applied_values",
    "sampling_preset_validation",
    "sampling_preset_inheritance",
    "sampling_preset_resolution_policy",
    "sampling_preset_denoise_policy",
}


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _normalize_intent(value: Any) -> str:
    return normalize_output_intent_id(value, unknown_passthrough=True)


def _managed_fields(payload: Mapping[str, Any]) -> set[str]:
    return {str(item).strip() for item in (payload.get("managed_fields") or []) if str(item).strip()}


def _selector_values(value: Any, *, dimension: str) -> set[str]:
    if value in (None, "", "*"):
        return {"*"}
    raw_values: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    result: set[str] = set()
    for raw in raw_values:
        if str(raw or "").strip() == "*":
            result.add("*")
        elif dimension == "family":
            result.add(normalize_family(raw))
        elif dimension == "loader":
            result.add(normalize_loader(raw))
        elif dimension == "mode":
            result.add(normalize_mode(raw))
        elif dimension == "intent":
            result.add(_normalize_intent(raw))
        else:
            result.add(_token(raw))
    return result or {"*"}


def _context(
    family: Any,
    *,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    intent: Any = "none",
) -> dict[str, str]:
    capability = resolve_sampling_guidance_capability(
        family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
    )
    return {
        "family": str(capability.get("family") or normalize_family(family)),
        "variant": str(capability.get("variant") or _token(variant)),
        "loader": str(capability.get("loader") or normalize_loader(loader)),
        "mode": str(capability.get("mode") or normalize_mode(mode)),
        "intent": _normalize_intent(intent),
    }


def _entry_match_score(entry: Mapping[str, Any], context: Mapping[str, str]) -> int | None:
    match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
    weights = {"family": 32, "variant": 16, "loader": 8, "mode": 4, "intent": 2}
    score = int(entry.get("priority") or 0) * 1000
    for dimension, weight in weights.items():
        allowed = _selector_values(match.get(dimension, "*"), dimension=dimension)
        value = context.get(dimension, "")
        if "*" in allowed:
            continue
        if value not in allowed:
            return None
        score += weight
    return score


def _validate_registry(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unexpected sampling preset registry schema: {payload.get('schema')!r}")
    if payload.get("phase") != PHASE:
        raise ValueError(f"Unexpected sampling preset registry phase: {payload.get('phase')!r}")

    managed = _managed_fields(payload)
    if not managed:
        raise ValueError("Sampling preset registry must declare managed_fields.")

    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    intent_sampling_status = str(contract.get("intent_specific_sampling_status") or "")

    entry_ids: set[str] = set()
    for raw in payload.get("presets") or []:
        if not isinstance(raw, dict):
            raise ValueError("Sampling preset entries must be objects.")
        entry_id = str(raw.get("entry_id") or "").strip()
        preset_id = str(raw.get("preset_id") or "").strip()
        if not entry_id or not preset_id:
            raise ValueError("Sampling preset entries require entry_id and preset_id.")
        if entry_id in entry_ids:
            raise ValueError(f"Duplicate sampling preset entry_id: {entry_id}")
        entry_ids.add(entry_id)
        if raw.get("source") != "built_in" or raw.get("immutable") is not True:
            raise ValueError(f"Built-in sampling preset {entry_id!r} must be source=built_in and immutable=true.")
        values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
        foreign = sorted(set(values) - managed)
        if foreign:
            raise ValueError(f"Built-in sampling preset {entry_id!r} contains non-sampling fields: {foreign}")

        application_mode = str(raw.get("application_mode") or "replace_sampling_fields")
        if application_mode not in {"replace_sampling_fields", "delegate_provider", "clean_slate"}:
            raise ValueError(f"Built-in sampling preset {entry_id!r} has unsupported application_mode: {application_mode!r}")

        inherit = raw.get("inherit")
        if inherit is not None and not isinstance(inherit, dict):
            raise ValueError(f"Built-in sampling preset {entry_id!r} inherit must be an object when present.")
        drop_fields = {str(item).strip() for item in (raw.get("drop_fields") or []) if str(item).strip()}
        foreign_drop = sorted(drop_fields - managed)
        if foreign_drop:
            raise ValueError(f"Built-in sampling preset {entry_id!r} drops non-sampling fields: {foreign_drop}")

        match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
        if intent_sampling_status == "disabled_ip6":
            intent_values = _selector_values(match.get("intent", "*"), dimension="intent")
            if intent_values != {"*"}:
                raise ValueError(
                    f"IP-6 keeps Output Intent metadata-only; sampling preset {entry_id!r} cannot be intent-specific."
                )

        if preset_id == DEFAULT_BALANCED_ID:
            families = _selector_values(match.get("family", "*"), dimension="family")
            modes = _selector_values(match.get("mode", "*"), dimension="mode")
            if "*" in families:
                raise ValueError("IP-5 Default · Balanced entries must be family-scoped; no global numeric fallback is allowed.")
            if "*" in modes:
                raise ValueError("IP-5 Default · Balanced entries must be workflow-scoped; no global workflow fallback is allowed.")
            allowed_modes = {"txt2img", "img2img", "edit", "inpaint", "outpaint"}
            if not modes.issubset(allowed_modes):
                raise ValueError(f"IP-5 Default · Balanced contains unsupported workflow selector(s): {sorted(modes - allowed_modes)}")
            image_modes = modes & {"img2img", "edit", "inpaint", "outpaint"}
            if image_modes and application_mode == "replace_sampling_fields":
                if not isinstance(inherit, dict):
                    raise ValueError(f"IP-5 workflow override {entry_id!r} must inherit a family base preset.")
                parent_mode = normalize_mode(inherit.get("mode") or "")
                if parent_mode != "txt2img":
                    raise ValueError(f"IP-5 workflow override {entry_id!r} must inherit from txt2img family base values.")
                if "width" in values or "height" in values:
                    raise ValueError(f"IP-5 workflow override {entry_id!r} must not hardcode width/height; source/canvas resolution owns image workflows.")
                if not {"width", "height"}.issubset(drop_fields):
                    raise ValueError(f"IP-5 workflow override {entry_id!r} must drop inherited width/height.")


@lru_cache(maxsize=1)
def load_builtin_sampling_presets() -> dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    _validate_registry(payload)
    return payload


def managed_sampling_fields() -> set[str]:
    return set(_managed_fields(load_builtin_sampling_presets()))


def _select_raw_sampling_preset(preset_id: Any, context: Mapping[str, str]) -> tuple[int, dict[str, Any]] | None:
    logical_id = _token(preset_id)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in load_builtin_sampling_presets().get("presets") or []:
        if _token(raw.get("preset_id")) != logical_id:
            continue
        score = _entry_match_score(raw, context)
        if score is not None:
            candidates.append((score, deepcopy(raw)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], str(item[1].get("entry_id") or "")), reverse=True)
    top_score = candidates[0][0]
    tied = [entry for score, entry in candidates if score == top_score]
    if len(tied) > 1:
        raise ValueError(
            "Ambiguous built-in sampling preset resolution for "
            f"{logical_id!r}: {[entry.get('entry_id') for entry in tied]}"
        )
    return top_score, candidates[0][1]


def _inherit_context(context: Mapping[str, str], inherit: Mapping[str, Any]) -> dict[str, str]:
    parent = dict(context)
    for dimension in ("family", "variant", "loader", "mode", "intent"):
        if dimension not in inherit:
            continue
        raw = inherit.get(dimension)
        if dimension == "family":
            parent[dimension] = normalize_family(raw)
        elif dimension == "loader":
            parent[dimension] = normalize_loader(raw)
        elif dimension == "mode":
            parent[dimension] = normalize_mode(raw)
        elif dimension == "intent":
            parent[dimension] = _normalize_intent(raw)
        else:
            parent[dimension] = _token(raw)
    return parent


def _materialize_sampling_preset_entry(
    entry: Mapping[str, Any],
    context: Mapping[str, str],
    *,
    trail: tuple[str, ...] = (),
) -> dict[str, Any]:
    materialized = deepcopy(dict(entry))
    entry_id = str(materialized.get("entry_id") or "")
    if entry_id in trail:
        raise ValueError(f"Sampling preset inheritance cycle detected: {[*trail, entry_id]}")

    local_values = deepcopy(materialized.get("values") if isinstance(materialized.get("values"), dict) else {})
    inherit = materialized.get("inherit") if isinstance(materialized.get("inherit"), dict) else None
    drop_fields = [str(item).strip() for item in (materialized.get("drop_fields") or []) if str(item).strip()]
    if not inherit:
        materialized["local_values"] = deepcopy(local_values)
        materialized["inheritance"] = {
            "inherited": False,
            "chain": [entry_id] if entry_id else [],
            "drop_fields": drop_fields,
        }
        return materialized

    parent_preset_id = _token(inherit.get("preset_id") or materialized.get("preset_id"))
    parent_context = _inherit_context(context, inherit)
    parent_selected = _select_raw_sampling_preset(parent_preset_id, parent_context)
    if parent_selected is None:
        raise ValueError(
            f"Sampling preset {entry_id!r} inherits from unavailable parent "
            f"{parent_preset_id!r} with context {parent_context}."
        )
    _parent_score, parent_raw = parent_selected
    parent = _materialize_sampling_preset_entry(parent_raw, parent_context, trail=(*trail, entry_id))
    effective_values = deepcopy(parent.get("values") if isinstance(parent.get("values"), dict) else {})
    for field in drop_fields:
        effective_values.pop(field, None)
    effective_values.update(local_values)
    parent_chain = list((parent.get("inheritance") or {}).get("chain") or [])
    materialized["local_values"] = deepcopy(local_values)
    materialized["values"] = effective_values
    materialized["inheritance"] = {
        "inherited": True,
        "parent_preset_id": parent_preset_id,
        "parent_entry_id": parent.get("entry_id"),
        "parent_context": parent_context,
        "chain": [*parent_chain, entry_id],
        "drop_fields": drop_fields,
    }
    return materialized


def resolve_sampling_preset(
    preset_id: Any,
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    intent: Any = "none",
) -> dict[str, Any]:
    logical_id = _token(preset_id)
    context = _context(
        family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    selected = _select_raw_sampling_preset(logical_id, context)
    top_score: int | None = None
    if selected is None:
        # IP-7 user presets live outside the immutable built-in registry. Resolve
        # them lazily so built-in import/validation stays side-effect free.
        from neo_app.image.user_sampling_presets import resolve_user_sampling_preset

        entry = resolve_user_sampling_preset(
            logical_id,
            family=context.get("family"),
            loader=context.get("loader"),
            mode=context.get("mode"),
            variant=context.get("variant"),
            model_name=model_name,
        )
        if entry is None:
            return {
                "schema": SCHEMA,
                "phase": PHASE,
                "authoring_phase": USER_PRESET_AUTHORING_PHASE,
                "resolved": False,
                "preset_id": logical_id,
                "context": context,
                "entry": None,
                "warnings": ["No built-in or user sampling preset entry matches this route context."],
            }
    else:
        top_score, raw_entry = selected
        entry = _materialize_sampling_preset_entry(raw_entry, context)
    capability = resolve_sampling_guidance_capability(
        context.get("family"),
        loader=context.get("loader"),
        mode=context.get("mode"),
        variant=context.get("variant"),
        model_name=model_name,
    )
    entry["resolution_policy"] = capability.get("resolution_policy")
    entry["denoise_policy"] = capability.get("denoise_policy")
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "authoring_phase": USER_PRESET_AUTHORING_PHASE,
        "resolved": True,
        "preset_id": logical_id,
        "context": context,
        "match_score": top_score,
        "entry": entry,
        "warnings": [],
    }

def list_available_sampling_presets(
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    intent: Any = "none",
) -> list[dict[str, Any]]:
    logical_ids = sorted({_token(item.get("preset_id")) for item in load_builtin_sampling_presets().get("presets") or []})
    try:
        from neo_app.image.user_sampling_presets import list_user_sampling_presets
        logical_ids.extend(
            str(item.get("preset_id") or "")
            for item in list_user_sampling_presets().get("presets", [])
            if str(item.get("preset_id") or "")
        )
    except Exception:  # noqa: BLE001 - catalog listing must not break built-ins on malformed user storage.
        pass
    logical_ids = sorted(set(logical_ids))
    available: list[dict[str, Any]] = []
    for preset_id in logical_ids:
        resolved = resolve_sampling_preset(
            preset_id,
            family=family,
            loader=loader,
            mode=mode,
            variant=variant,
            model_name=model_name,
            intent=intent,
        )
        if not resolved.get("resolved"):
            continue
        entry = resolved["entry"]
        available.append({
            "preset_id": preset_id,
            "entry_id": entry.get("entry_id"),
            "label": entry.get("label"),
            "description": entry.get("description"),
            "category": entry.get("category"),
            "source": entry.get("source"),
            "immutable": bool(entry.get("immutable")),
            "application_mode": entry.get("application_mode"),
            "authoring_template": bool(entry.get("authoring_template")),
            "resolution_policy": entry.get("resolution_policy"),
            "denoise_policy": entry.get("denoise_policy"),
            "inheritance": deepcopy(entry.get("inheritance") or {}),
            "context": deepcopy(resolved.get("context") or {}),
        })
    return available


def _clear_managed_values(params: Mapping[str, Any]) -> dict[str, Any]:
    clean = deepcopy(dict(params or {}))
    for key in managed_sampling_fields():
        clean.pop(key, None)
    return clean


def _preset_metadata(entry: Mapping[str, Any], context: Mapping[str, str]) -> dict[str, Any]:
    values = entry.get("values") if isinstance(entry.get("values"), dict) else {}
    return {
        "sampling_preset_id": str(entry.get("preset_id") or ""),
        "sampling_preset_entry_id": str(entry.get("entry_id") or ""),
        "sampling_preset_source": str(entry.get("source") or "built_in"),
        "sampling_preset_application_mode": str(entry.get("application_mode") or "replace_sampling_fields"),
        "sampling_preset_read_only": bool(entry.get("immutable", True)),
        "sampling_preset_authoring_template": bool(entry.get("authoring_template", False)),
        "sampling_preset_context": deepcopy(dict(context)),
        "sampling_preset_applied_values": sorted(values),
        "sampling_preset_inheritance": deepcopy(entry.get("inheritance") or {}),
        "sampling_preset_resolution_policy": entry.get("resolution_policy"),
        "sampling_preset_denoise_policy": entry.get("denoise_policy"),
    }


def _requirement_group(label: str, alternatives: Iterable[str]) -> dict[str, Any]:
    values = [str(value).strip() for value in alternatives if str(value).strip()]
    return {"label": label, "alternatives": list(dict.fromkeys(values))}


def _manual_requirement_groups(capability: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    sampling = capability.get("sampling") if isinstance(capability.get("sampling"), dict) else {}
    guidance = capability.get("guidance") if isinstance(capability.get("guidance"), dict) else {}
    groups: list[dict[str, Any]] = []
    profile_unknown = False

    control_fields = (
        ("Sampler", "sampler_control", "sampler"),
        ("Scheduler", "scheduler_control", "scheduler"),
        ("Steps", "steps_control", "steps"),
    )
    for label, control_key, field in control_fields:
        control = str(sampling.get(control_key) or "provider_profile")
        if control == "selectable":
            groups.append(_requirement_group(label, [field]))
        elif control == "provider_profile":
            profile_unknown = True

    guidance_kind = str(guidance.get("kind") or "provider_profile")
    guidance_field = str(guidance.get("field") or "").strip()
    guidance_aliases = [str(item).strip() for item in (guidance.get("aliases") or []) if str(item).strip()]

    sampler_cfg = sampling.get("sampler_cfg") if isinstance(sampling.get("sampler_cfg"), dict) else {}
    sampler_cfg_kind = str(sampler_cfg.get("kind") or "provider_profile")
    sampler_cfg_field = str(sampler_cfg.get("field") or "").strip()
    guidance_owned_fields = {guidance_field, *guidance_aliases} - {""}
    if sampler_cfg_kind == "selectable" and sampler_cfg_field and sampler_cfg_field not in guidance_owned_fields:
        groups.append(_requirement_group(str(sampler_cfg.get("ui_label") or "Sampler CFG"), [sampler_cfg_field]))
    elif sampler_cfg_kind == "provider_profile":
        profile_unknown = True
    if guidance_kind in {"classic_cfg", "true_cfg", "embedded_guidance"} and guidance_field:
        groups.append(_requirement_group(str(guidance.get("ui_label") or "Guidance"), [*guidance_aliases, guidance_field]))
    elif guidance_kind == "provider_profile":
        profile_unknown = True

    resolution_policy = str(capability.get("resolution_policy") or "provider_profile")
    if resolution_policy == "explicit_canvas":
        groups.append(_requirement_group("Width", ["width"]))
        groups.append(_requirement_group("Height", ["height"]))
    elif resolution_policy == "provider_profile":
        profile_unknown = True

    denoise_policy = str(capability.get("denoise_policy") or "provider_profile")
    if "strength_controlled" in denoise_policy:
        groups.append(_requirement_group("Denoise", ["denoise"]))
    elif denoise_policy == "provider_profile":
        profile_unknown = True

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        key = tuple(sorted(group.get("alternatives") or []))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(group)
    return unique, profile_unknown


def validate_sampling_preset_submission(
    params: Mapping[str, Any],
    *,
    preset_id: Any,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    intent: Any = "none",
) -> dict[str, Any]:
    resolved = resolve_sampling_preset(
        preset_id,
        family=family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    if not resolved.get("resolved"):
        return {
            "schema": "neo.image.sampling_preset_validation.v1",
            "phase": PHASE,
            "status": "unknown_preset",
            "complete": False,
            "blocks_generation": True,
            "message": "Sampling preset is unavailable for the selected model route.",
            "missing": [],
            "preset_id": _token(preset_id),
            "context": deepcopy(resolved.get("context") or {}),
        }

    entry = resolved["entry"]
    application_mode = str(entry.get("application_mode") or "replace_sampling_fields")
    if application_mode == "delegate_provider":
        return {
            "schema": "neo.image.sampling_preset_validation.v1",
            "phase": PHASE,
            "status": "provider_delegated",
            "complete": True,
            "blocks_generation": False,
            "message": "Sampling values are intentionally delegated to the selected provider/compiler.",
            "missing": [],
            "preset_id": entry.get("preset_id"),
            "entry_id": entry.get("entry_id"),
            "context": deepcopy(resolved.get("context") or {}),
        }

    capability = resolve_sampling_guidance_capability(
        family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
    )
    groups, profile_unknown = _manual_requirement_groups(capability)
    if application_mode == "clean_slate" and (not capability.get("known_family") or profile_unknown):
        return {
            "schema": "neo.image.sampling_preset_validation.v1",
            "phase": PHASE,
            "status": "incomplete_profile_requirements_unknown",
            "complete": False,
            "blocks_generation": True,
            "message": "Sampling settings are incomplete: Clean Slate cannot silently borrow unresolved provider/profile sampling defaults. Choose Provider Defaults or a model-specific preset.",
            "missing": ["provider/profile sampling requirements"],
            "preset_id": entry.get("preset_id"),
            "entry_id": entry.get("entry_id"),
            "context": deepcopy(resolved.get("context") or {}),
        }

    missing: list[str] = []
    raw = dict(params or {})
    for group in groups:
        alternatives = list(group.get("alternatives") or [])
        if not any(raw.get(key) not in (None, "") for key in alternatives):
            missing.append(str(group.get("label") or "/".join(alternatives)))

    complete = not missing
    return {
        "schema": "neo.image.sampling_preset_validation.v1",
        "phase": PHASE,
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "blocks_generation": not complete,
        "message": (
            "Sampling settings are complete."
            if complete
            else "Sampling settings are incomplete: " + ", ".join(missing) + "."
        ),
        "missing": missing,
        "required_groups": groups,
        "preset_id": entry.get("preset_id"),
        "entry_id": entry.get("entry_id"),
        "context": deepcopy(resolved.get("context") or {}),
    }


def apply_sampling_preset(
    params: Mapping[str, Any],
    preset_id: Any,
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    intent: Any = "none",
) -> dict[str, Any]:
    """Apply a built-in preset to authoring params without touching non-sampling state."""

    resolved = resolve_sampling_preset(
        preset_id,
        family=family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    if not resolved.get("resolved"):
        raise KeyError(f"Sampling preset unavailable: {preset_id}")
    entry = resolved["entry"]
    clean = _clear_managed_values(params)
    values = entry.get("values") if isinstance(entry.get("values"), dict) else {}
    clean.update(deepcopy(values))
    clean.update(_preset_metadata(entry, resolved.get("context") or {}))
    clean["sampling_preset_validation"] = validate_sampling_preset_submission(
        clean,
        preset_id=entry.get("preset_id"),
        family=family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    return clean


def prepare_sampling_preset_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one Image payload at the job boundary.

    Presets are authoring tools, not queue-time authorities. Selection-time preset
    application may seed fields, but the values present in the submitted payload
    are the final user truth and are never replaced or stripped here.
    """

    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return prepared
    params = dict(prepared.get("params") or {})
    preset_id = _token(params.get("sampling_preset_id"))
    if not preset_id:
        return prepared

    variant = str(params.get("variant") or params.get("flux_variant") or params.get("krea2_variant") or "")
    model_name = str(prepared.get("model") or params.get("model") or params.get("diffusion_model") or params.get("gguf_model") or params.get("gguf_unet") or "")
    intent = params.get("output_intent") or params.get("sampling_intent") or "none"
    resolved = resolve_sampling_preset(
        preset_id,
        family=prepared.get("family"),
        loader=prepared.get("loader"),
        mode=prepared.get("mode"),
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    if not resolved.get("resolved"):
        params["sampling_preset_validation"] = validate_sampling_preset_submission(
            params,
            preset_id=preset_id,
            family=prepared.get("family"),
            loader=prepared.get("loader"),
            mode=prepared.get("mode"),
            variant=variant,
            model_name=model_name,
            intent=intent,
        )
        prepared["params"] = params
        return prepared

    entry = resolved["entry"]
    application_mode = str(entry.get("application_mode") or "replace_sampling_fields")
    # Parameter Truth: presets may fill values that are genuinely missing, but they
    # never replace or strip explicit values already present in the submission.
    if application_mode == "replace_sampling_fields":
        values = entry.get("values") if isinstance(entry.get("values"), dict) else {}
        for key, value in values.items():
            if params.get(key) in (None, ""):
                params[key] = deepcopy(value)
    # delegate_provider and clean_slate preserve every explicit submitted field.

    params.update(_preset_metadata(entry, resolved.get("context") or {}))
    params["sampling_preset_validation"] = validate_sampling_preset_submission(
        params,
        preset_id=entry.get("preset_id"),
        family=prepared.get("family"),
        loader=prepared.get("loader"),
        mode=prepared.get("mode"),
        variant=variant,
        model_name=model_name,
        intent=intent,
    )
    prepared["params"] = params
    return prepared


def sampling_preset_contract() -> dict[str, Any]:
    payload = load_builtin_sampling_presets()
    return {
        "schema": payload.get("schema"),
        "phase": PHASE,
        "authoring_phase": USER_PRESET_AUTHORING_PHASE,
        "version": payload.get("version"),
        "resolution_key": (payload.get("contract") or {}).get("resolution_key"),
        "managed_fields": sorted(managed_sampling_fields()),
        "built_in_presets": [
            {
                "preset_id": item.get("preset_id"),
                "entry_id": item.get("entry_id"),
                "label": item.get("label"),
                "description": item.get("description", ""),
                "category": item.get("category", "defaults"),
                "source": item.get("source", "built_in"),
                "application_mode": item.get("application_mode"),
                "authoring_template": bool(item.get("authoring_template")),
                "immutable": bool(item.get("immutable")),
                "priority": int(item.get("priority") or 0),
                "match": deepcopy(item.get("match") or {}),
                "inherit": deepcopy(item.get("inherit") or {}),
                "drop_fields": list(item.get("drop_fields") or []),
                "values": deepcopy(item.get("values") or {}),
            }
            for item in payload.get("presets") or []
        ],
        "user_preset_namespace": USER_PRESET_NAMESPACE,
        "user_preset_api_endpoint": "/api/ui-presets/image_sampling",
        "user_preset_authoring_status": "active_ip7",
        "user_preset_context_scope": ["family", "variant", "loader", "workflow"],
        "user_preset_output_intent_policy": "separate_not_captured",
        "numeric_family_defaults_status": "default_balanced_family_bases_ip4_workflow_overrides_ip5",
        "workflow_overrides_status": "active_inherited_workflow_overrides_ip5",
        "quality_fast_status": "deferred_until_validated",
        "output_intent_layer_phase": "IP-6",
        "output_intent_sampling_mutation_status": (payload.get("contract") or {}).get("intent_specific_sampling_status"),
        "ui_status": "preset_authoring_active_ip7",
        "final_release_lock_phase": "IP-8",
        "inspector_phase": "IP-8",
        "release_lock_owner": "neo_app.image.sampling_preset_release_lock",
        "inspector_owner": "neo_app.image.sampling_preset_inspector",
        "live_payload_integration_phase": "IR-3",
        "browser_submission_fields": ["sampling_preset_id", "output_intent"],
        "manual_submission_policy": "omit_sampling_preset_id",
        "workspace_user_preset_submission_policy": "captured_values_without_builtin_sampling_authority",
        "prepared_job_context_policy": "authoritative_neojob_used_for_provider_registry_and_context",
    }
