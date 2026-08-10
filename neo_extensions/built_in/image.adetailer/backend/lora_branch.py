from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Mapping

from neo_extensions.built_in.lora_stack.backend.catalog_bridge import resolve_exact_provider_catalog_name
from neo_extensions.built_in.lora_stack.backend.patch_profile import normalize_lora_patch_profile, profile_metadata
from neo_extensions.built_in.lora_stack.backend.payload_schema import normalize_lora_rows

from .model_source import DEDICATED_CHECKPOINT_SOURCE, GENERATION_MODEL_SOURCE, normalize_model_source
from .route_contract import clean_comfy_ref

LORA_BRANCH_SCHEMA_ID = "neo.image.adetailer.lora_branch.v1"
INHERIT_ALL = "inherit_all"
INHERIT_SELECTED = "inherit_selected"
INHERIT_NONE = "inherit_none"
VALID_INHERITANCE = {INHERIT_ALL, INHERIT_SELECTED, INHERIT_NONE}
MAX_DETAILER_LORAS = 8
LORA_LOADER_NODE = "LoraLoader"
LORA_MODEL_ONLY_NODE = "LoraLoaderModelOnly"
MODEL_CLIP_STRATEGIES = {
    "lora_loader_model_clip_chain",
    "lora_loader_model_clip_consumer_rewire",
}
MODEL_ONLY_STRATEGIES = {
    "lora_loader_model_only_chain",
    "lora_loader_model_only_consumer_rewire",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _clamp_strength(value: Any, default: float = 0.8) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(-4.0, min(4.0, number)), 4)


def normalize_lora_inheritance(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "all": INHERIT_ALL,
        "inherit": INHERIT_ALL,
        "inherit_generation": INHERIT_ALL,
        "selected": INHERIT_SELECTED,
        "some": INHERIT_SELECTED,
        "none": INHERIT_NONE,
        "off": INHERIT_NONE,
        "do_not_inherit": INHERIT_NONE,
    }
    resolved = aliases.get(text, text or INHERIT_ALL)
    return resolved if resolved in VALID_INHERITANCE else INHERIT_ALL


def normalize_lora_uid_list(value: Any) -> list[str]:
    raw: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    elif isinstance(value, str):
        raw = [item for chunk in value.splitlines() for item in chunk.split(",")]
    else:
        raw = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        uid = _clean_text(item)
        if not uid or uid in seen:
            continue
        seen.add(uid)
        result.append(uid)
    return result[:64]


def _safe_catalog_name(value: Any) -> str:
    text = _clean_text(value).replace("\\", "/")
    if not text or text.startswith(("/", "//")) or (len(text) > 1 and text[1] == ":"):
        return ""
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    return PurePosixPath(*parts).as_posix()


def normalize_detailer_lora_row(row: Mapping[str, Any] | None, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    if not _as_bool(row.get("enabled"), True):
        return None
    name = _safe_catalog_name(row.get("portable_catalog_name") or row.get("name") or row.get("lora_name"))
    if not name:
        return None
    model_strength = _clamp_strength(row.get("strength_model", row.get("strength", 0.8)))
    clip_strength = _clamp_strength(row.get("strength_clip", row.get("strength", model_strength)), model_strength)
    return {
        "uid": _clean_text(row.get("uid")) or f"detailer_lora_{index + 1}",
        "enabled": True,
        "name": name,
        "strength_model": model_strength,
        "strength_clip": clip_strength,
        "trigger": _clean_text(row.get("trigger") or row.get("trigger_text")),
        "source": _clean_text(row.get("source")) or "adetailer_direct",
    }


def normalize_detailer_lora_rows(rows: Any, *, singular_params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    raw_rows = list(rows) if isinstance(rows, list) else []
    singular = singular_params if isinstance(singular_params, Mapping) else {}
    if _as_bool(singular.get("detailer_lora_enabled"), False) and _clean_text(singular.get("detailer_lora")):
        raw_rows.insert(0, {
            "uid": "adetailer_primary_lora",
            "enabled": True,
            "name": singular.get("detailer_lora"),
            "strength_model": singular.get("detailer_lora_strength_model", 0.8),
            "strength_clip": singular.get("detailer_lora_strength_clip", singular.get("detailer_lora_strength_model", 0.8)),
            "trigger": singular.get("detailer_lora_trigger", ""),
            "source": "adetailer_direct",
        })
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, str]] = set()
    for index, raw in enumerate(raw_rows[:MAX_DETAILER_LORAS]):
        item = normalize_detailer_lora_row(raw, index)
        if not item:
            continue
        key = (item["name"].casefold(), item["strength_model"], item["strength_clip"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _extension_block(payload: Any, extension_id: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    if isinstance(payload.get(extension_id), Mapping):
        return dict(payload.get(extension_id) or {})
    payloads = payload.get("payloads")
    if isinstance(payloads, Mapping) and isinstance(payloads.get(extension_id), Mapping):
        return dict(payloads.get(extension_id) or {})
    extensions = payload.get("extensions")
    if isinstance(extensions, Mapping) and isinstance(extensions.get(extension_id), Mapping):
        return dict(extensions.get(extension_id) or {})
    return {}


def extract_lora_stack_rows(payload: Any) -> list[dict[str, Any]]:
    block = _extension_block(payload, "lora_stack")
    if not _as_bool(block.get("enabled"), False):
        return []
    params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
    return normalize_lora_rows(params.get("loras") if isinstance(params.get("loras"), list) else [])


def _global_base_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("apply_to") == "global" and row.get("target") in {"base", "both"}]


def _global_finish_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("apply_to") != "global" or row.get("target") != "finish":
            continue
        result.append({
            "uid": _clean_text(row.get("uid")) or f"finish_{len(result) + 1}",
            "enabled": True,
            "name": _safe_catalog_name(row.get("name")),
            "strength_model": _clamp_strength(row.get("strength", 0.8)),
            "strength_clip": _clamp_strength(row.get("strength", 0.8)),
            "trigger": "",
            "source": "lora_stack_finish",
        })
    return [row for row in result if row.get("name")]


def _selected_generation_rows(rows: list[dict[str, Any]], selected_uids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    by_uid = {_clean_text(row.get("uid")): row for row in _global_base_rows(rows) if _clean_text(row.get("uid"))}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    for uid in selected_uids:
        row = by_uid.get(uid)
        if not row:
            missing.append(uid)
            continue
        strength = _clamp_strength(row.get("strength", 0.8))
        selected.append({
            "uid": uid,
            "enabled": True,
            "name": _safe_catalog_name(row.get("name")),
            "strength_model": strength,
            "strength_clip": strength,
            "trigger": "",
            "source": "lora_stack_selected_generation",
        })
    return [row for row in selected if row.get("name")], missing


def _object_info(available_nodes: Any) -> Mapping[str, Any]:
    if not isinstance(available_nodes, Mapping):
        return {}
    nested = available_nodes.get("object_info")
    return nested if isinstance(nested, Mapping) else available_nodes


def _loader_contract(available_nodes: Any, node_class: str) -> tuple[bool, set[str], list[str]]:
    info = _object_info(available_nodes)
    if not info:
        return available_nodes is None, set(), []
    schema = info.get(node_class)
    if not isinstance(schema, Mapping):
        return False, set(), []
    input_block = schema.get("input") if isinstance(schema.get("input"), Mapping) else schema
    names: set[str] = set()
    choices: list[str] = []
    for section_name in ("required", "optional"):
        section = input_block.get(section_name) if isinstance(input_block, Mapping) and isinstance(input_block.get(section_name), Mapping) else {}
        names.update(str(key) for key in section)
        raw = section.get("lora_name")
        if raw is None:
            continue
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if isinstance(first, Mapping):
            first = first.get("choices") or first.get("values") or first.get("options") or []
        if isinstance(first, (list, tuple, set)):
            choices.extend(_clean_text(item).replace("\\", "/") for item in first if _clean_text(item))
    return True, names, list(dict.fromkeys(choices))


def _route_prompt_context(route: Mapping[str, Any] | None) -> dict[str, str]:
    source = route if isinstance(route, Mapping) else {}
    actual = source.get("actual_params") if isinstance(source.get("actual_params"), Mapping) else {}
    params = source.get("params") if isinstance(source.get("params"), Mapping) else {}
    containers = (actual, params, source)

    def first(keys: tuple[str, ...]) -> str:
        for container in containers:
            for key in keys:
                value = _clean_text(container.get(key))
                if value:
                    return value
        return ""

    return {
        "positive": first(("prompt", "positive_prompt", "positive", "main_prompt", "text")),
        "negative": first(("negative_prompt", "negative", "negative_text", "main_negative_prompt")),
    }


def _strategy_for_branch(
    *,
    model_source: str,
    route: Mapping[str, Any] | None,
    lora_patch_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if model_source == DEDICATED_CHECKPOINT_SOURCE:
        return {
            "valid": True,
            "strategy": "lora_loader_model_clip_chain",
            "loader_node_class": LORA_LOADER_NODE,
            "requires_clip": True,
            "profile": {"source": "adetailer_dedicated_checkpoint", "required": False},
            "compiler_profile": {},
        }
    profile_result = normalize_lora_patch_profile(dict(lora_patch_profile) if isinstance(lora_patch_profile, Mapping) else None, route=dict(route or {}))
    profile = profile_result.get("profile") if isinstance(profile_result.get("profile"), Mapping) else {}
    strategy = _clean_text(profile.get("strategy"))
    loader = _clean_text(profile.get("loader_node_class"))
    if profile_result.get("valid") and strategy in MODEL_CLIP_STRATEGIES | MODEL_ONLY_STRATEGIES:
        return {
            "valid": True,
            "strategy": strategy,
            "loader_node_class": loader,
            "requires_clip": strategy in MODEL_CLIP_STRATEGIES,
            "profile": profile_metadata(profile_result),
            "compiler_profile": deepcopy(profile),
        }
    route_data = route if isinstance(route, Mapping) else {}
    family = _clean_text(route_data.get("family") or route_data.get("model_family")).lower()
    loader_id = _clean_text(route_data.get("loader") or route_data.get("loader_type")).lower()
    if family in {"sdxl", "sd15"} and loader_id == "checkpoint":
        return {
            "valid": True,
            "strategy": "lora_loader_model_clip_chain",
            "loader_node_class": LORA_LOADER_NODE,
            "requires_clip": True,
            "profile": {
                "schema_version": "neo.image.lora_stack.patch_profile.v2",
                "source": "adetailer_checkpoint_compatibility",
                "valid": True,
                "required": False,
                "reason": "checkpoint_compatibility",
            },
            "compiler_profile": {"model_ref": ["1", 0], "clip_ref": ["1", 1]},
        }
    return {
        "valid": False,
        "strategy": strategy or "none",
        "loader_node_class": loader,
        "requires_clip": False,
        "profile": profile_metadata(profile_result),
        "compiler_profile": deepcopy(profile),
        "reason": _clean_text(profile_result.get("reason")) or "missing_lora_patch_profile",
    }


def _dedupe_apply_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for row in rows:
        key = (str(row.get("name") or "").casefold(), float(row.get("strength_model") or 0), float(row.get("strength_clip") or 0))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(row))
    return result


def validate_detailer_lora_branch(
    params: Mapping[str, Any] | None,
    *,
    payload: Any,
    route: Mapping[str, Any] | None,
    available_nodes: Any,
    model_source: Mapping[str, Any] | None,
    lora_patch_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_params = params if isinstance(params, Mapping) else {}
    source_plan = model_source if isinstance(model_source, Mapping) else {}
    source = normalize_model_source(source_plan.get("source") or source_params.get("model_source"))
    requested_inheritance = normalize_lora_inheritance(source_params.get("lora_inheritance"))
    effective_inheritance = requested_inheritance
    selected_uids = normalize_lora_uid_list(source_params.get("inherit_lora_uids"))
    stack_rows = extract_lora_stack_rows(payload)
    base_rows = _global_base_rows(stack_rows)
    finish_rows = _global_finish_rows(stack_rows)
    direct_rows = normalize_detailer_lora_rows(source_params.get("detailer_loras"), singular_params=source_params)
    selected_rows, missing_selected = _selected_generation_rows(stack_rows, selected_uids)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    direct_requested = _as_bool(source_params.get("detailer_lora_enabled"), False)
    direct_name = _clean_text(source_params.get("detailer_lora"))
    if direct_requested and not direct_name:
        errors.append({
            "code": "adetailer_detailer_lora_missing",
            "field": "detailer_lora",
            "message": "Detailer-only LoRA is enabled but no LoRA model is selected.",
        })
    elif direct_requested and not direct_rows:
        errors.append({
            "code": "adetailer_detailer_lora_invalid",
            "field": "detailer_lora",
            "message": "The selected detailer-only LoRA name is unsafe or invalid and cannot be executed.",
        })

    if source == DEDICATED_CHECKPOINT_SOURCE and requested_inheritance != INHERIT_NONE:
        effective_inheritance = INHERIT_NONE
        warnings.append({
            "code": "adetailer_dedicated_lora_inheritance_forced_none",
            "field": "lora_inheritance",
            "message": "A dedicated detailer checkpoint owns an isolated model branch; generation LoRAs are not inherited into that checkpoint.",
        })

    if effective_inheritance == INHERIT_SELECTED and not selected_uids:
        errors.append({
            "code": "adetailer_selected_lora_uids_missing",
            "field": "inherit_lora_uids",
            "message": "Select at least one active generation LoRA when ADetailer inheritance is set to selected.",
        })
    if missing_selected:
        errors.append({
            "code": "adetailer_selected_lora_uid_not_found",
            "field": "inherit_lora_uids",
            "message": "One or more selected generation LoRAs are no longer present in the active LoRA Stack.",
            "missing_uids": missing_selected,
        })

    inherited_apply_rows = selected_rows if effective_inheritance == INHERIT_SELECTED else []
    apply_rows = _dedupe_apply_rows(inherited_apply_rows + finish_rows + direct_rows)
    strategy = _strategy_for_branch(model_source=source, route=route, lora_patch_profile=lora_patch_profile)
    loader_node_class = _clean_text(strategy.get("loader_node_class"))
    requires_clip = bool(strategy.get("requires_clip"))

    if apply_rows and not strategy.get("valid"):
        errors.append({
            "code": "adetailer_lora_patch_profile_invalid",
            "field": "detailer_loras",
            "message": "The active compiler did not provide a valid LoRA loader strategy for the isolated ADetailer branch.",
            "reason": strategy.get("reason"),
        })

    catalog_bindings: list[dict[str, Any]] = []
    bound_rows: list[dict[str, Any]] = []
    if apply_rows and strategy.get("valid"):
        node_available, input_names, catalog_choices = _loader_contract(available_nodes, loader_node_class)
        required_inputs = {"model", "lora_name", "strength_model"}
        if requires_clip:
            required_inputs.update({"clip", "strength_clip"})
        if not node_available:
            errors.append({
                "code": "adetailer_lora_loader_missing",
                "field": "detailer_loras",
                "message": f"Comfy object_info did not expose {loader_node_class}; the ADetailer LoRA branch cannot be built.",
                "loader_node_class": loader_node_class,
            })
        elif input_names and not required_inputs.issubset(input_names):
            errors.append({
                "code": "adetailer_lora_loader_signature_invalid",
                "field": "detailer_loras",
                "message": f"Comfy {loader_node_class} is missing required inputs for the selected ADetailer LoRA strategy.",
                "missing_inputs": sorted(required_inputs - input_names),
            })
        elif isinstance(available_nodes, Mapping) and not catalog_choices:
            errors.append({
                "code": "adetailer_lora_catalog_unavailable",
                "field": "detailer_loras",
                "message": f"Comfy {loader_node_class} did not publish its lora_name catalog; exact ADetailer LoRA binding cannot be proven.",
            })
        else:
            for row in apply_rows:
                portable_name = str(row.get("name") or "")
                if catalog_choices:
                    binding = resolve_exact_provider_catalog_name(portable_name, catalog_choices)
                else:
                    binding = {
                        "schema_version": "neo.image.lora_stack.catalog_binding.v1",
                        "portable_catalog_name": portable_name,
                        "provider_catalog_name": portable_name,
                        "status": "resolved_unverified_legacy_context",
                        "match_mode": "legacy_no_live_catalog",
                        "catalog_count": 0,
                        "candidate_provider_names": [portable_name],
                        "verified": False,
                        "reason": "No live provider catalog was supplied to this lower-level caller.",
                    }
                binding = {**binding, "uid": row.get("uid"), "source": row.get("source"), "loader_node_class": loader_node_class}
                catalog_bindings.append(binding)
                if not str(binding.get("status") or "").startswith("resolved"):
                    errors.append({
                        "code": "adetailer_lora_not_accepted_by_provider",
                        "field": "detailer_loras",
                        "message": f"The selected ADetailer LoRA '{portable_name}' is not accepted by the active Comfy {loader_node_class} catalog.",
                        "catalog_binding": binding,
                    })
                    continue
                bound = deepcopy(row)
                bound["portable_catalog_name"] = str(binding.get("portable_catalog_name") or portable_name)
                bound["provider_catalog_name"] = str(binding.get("provider_catalog_name") or portable_name)
                bound["catalog_binding"] = deepcopy(binding)
                bound_rows.append(bound)

    prompt_context = _route_prompt_context(route)
    # Re-encode whenever a model+CLIP branch is created and also whenever
    # generation LoRA inheritance is rebuilt from the compiler base contract.
    # Upstream LoRA Stack may have rewired the original CLIPTextEncode nodes in
    # place, so merely reusing the base contract's positive/negative node refs
    # would leak the live generation LoRA CLIP into inherit_none/selected modes.
    requires_prompt_reencode = bool(
        requires_clip
        and (bound_rows or (source == GENERATION_MODEL_SOURCE and effective_inheritance != INHERIT_ALL))
    )
    if requires_prompt_reencode:
        use_main_prompt = bool(source_params.get("use_main_prompt", True))
        enabled_passes = [item for item in source_params.get("detailer_passes", []) if isinstance(item, Mapping) and item.get("enabled", True)]
        if not enabled_passes:
            enabled_passes = [source_params]
        for index, item in enumerate(enabled_passes):
            positive = _clean_text(item.get("positive_prompt")) or (prompt_context["positive"] if use_main_prompt else "")
            if not positive:
                errors.append({
                    "code": "adetailer_lora_branch_positive_prompt_missing",
                    "field": f"detailer_passes[{index}].positive_prompt",
                    "message": "The selected model+CLIP ADetailer LoRA branch requires a positive prompt to re-encode with its patched CLIP.",
                })

    inherited_runtime_count = len(base_rows) if effective_inheritance == INHERIT_ALL else len(inherited_apply_rows)
    anchor = "dedicated_checkpoint" if source == DEDICATED_CHECKPOINT_SOURCE else ("live_generation_model" if effective_inheritance == INHERIT_ALL else "compiler_base_model")
    requested = bool(apply_rows or effective_inheritance != INHERIT_ALL or finish_rows or direct_rows)
    return {
        "schema_id": LORA_BRANCH_SCHEMA_ID,
        "ready": not errors,
        "requested": requested,
        "model_source": source,
        "inheritance_requested": requested_inheritance,
        "inheritance_effective": effective_inheritance,
        "inherit_lora_uids": selected_uids,
        "anchor": anchor,
        "strategy": strategy.get("strategy"),
        "loader_node_class": loader_node_class,
        "requires_clip": requires_clip,
        "requires_prompt_reencode": requires_prompt_reencode,
        "patch_profile": deepcopy(strategy.get("profile") or {}),
        "compiler_model_ref": clean_comfy_ref((strategy.get("compiler_profile") or {}).get("model_ref")) or [],
        "compiler_clip_ref": clean_comfy_ref((strategy.get("compiler_profile") or {}).get("clip_ref")) or [],
        "generation_stack_rows": deepcopy(base_rows),
        "inherited_runtime_count": inherited_runtime_count,
        "selected_generation_rows": deepcopy(selected_rows),
        "finish_rows": deepcopy(finish_rows),
        "direct_rows": deepcopy(direct_rows),
        "apply_rows": deepcopy(bound_rows),
        "catalog_bindings": deepcopy(catalog_bindings),
        "prompt_context": prompt_context,
        "errors": errors,
        "warnings": warnings,
    }


def prompt_text_for_lora_runtime_unit(plan: Mapping[str, Any] | None, params: Mapping[str, Any] | None, unit: Mapping[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    source = plan if isinstance(plan, Mapping) else {}
    shared = params if isinstance(params, Mapping) else {}
    current = unit if isinstance(unit, Mapping) else {}
    context = source.get("prompt_context") if isinstance(source.get("prompt_context"), Mapping) else {}
    use_main = bool(shared.get("use_main_prompt", True))
    local_positive = _clean_text(current.get("positive_prompt"))
    local_negative = _clean_text(current.get("negative_prompt"))
    positive = local_positive or (_clean_text(context.get("positive")) if use_main else "")
    negative = local_negative or (_clean_text(context.get("negative")) if use_main else "")
    triggers: list[str] = []
    seen_triggers: set[str] = set()
    trigger_rows = source.get("apply_rows") if isinstance(source.get("apply_rows"), list) else source.get("lora_rows") if isinstance(source.get("lora_rows"), list) else []
    for row in trigger_rows:
        trigger = _clean_text(row.get("trigger")) if isinstance(row, Mapping) else ""
        folded = trigger.casefold()
        if not trigger or folded in seen_triggers:
            continue
        seen_triggers.add(folded)
        triggers.append(trigger)
    if triggers:
        positive = ", ".join([item for item in (positive, *triggers) if item])
    return positive, negative, {
        "positive_source": "local" if local_positive else ("main" if positive else "missing"),
        "negative_source": "local" if local_negative else ("main" if negative else "empty"),
        "policy": "reencode_with_detailer_lora_clip",
        "trigger_texts": triggers,
        "trigger_count": len(triggers),
    }


def validate_sampling_reapply_contract(
    workflow: Mapping[str, Any] | None,
    *,
    plan: Mapping[str, Any] | None,
    route_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_plan = plan if isinstance(plan, Mapping) else {}
    required = bool(source_plan.get("sampling_reapply_required"))
    contract = route_contract if isinstance(route_contract, Mapping) else {}
    sampling = contract.get("model_sampling") if isinstance(contract.get("model_sampling"), Mapping) else {}
    source_ids = [str(item) for item in sampling.get("source_node_ids", []) if str(item).strip()] if isinstance(sampling.get("source_node_ids"), list) else []
    errors: list[dict[str, Any]] = []
    if not required:
        return {"ready": True, "required": False, "source_node_ids": source_ids, "errors": []}
    sampling_state = str(sampling.get("state") or "")
    if sampling_state == "passthrough":
        model_ref = clean_comfy_ref((contract.get("refs") or {}).get("model")) if isinstance(contract.get("refs"), Mapping) else None
        if not model_ref:
            errors.append({
                "code": "adetailer_identity_sampling_passthrough_model_missing",
                "field": "identity_protection",
                "message": "The Qwen Edit passthrough route did not publish a direct MODEL reference for the identity-LoRA branch.",
            })
        return {
            "ready": not errors,
            "required": True,
            "state": sampling_state,
            "source_node_ids": [],
            "pre_sampling_model_ref": model_ref,
            "passthrough": True,
            "errors": errors,
        }
    if sampling_state != "patched" or not source_ids:
        errors.append({
            "code": "adetailer_identity_sampling_lineage_missing",
            "field": "identity_protection",
            "message": "The Qwen Edit identity-LoRA route did not publish a patched model-sampling lineage to reapply after the LoRA.",
        })
    previous_ref: list[Any] | None = None
    for index, node_id in enumerate(source_ids):
        node = workflow.get(node_id) if isinstance(workflow, Mapping) else None
        inputs = node.get("inputs") if isinstance(node, Mapping) and isinstance(node.get("inputs"), Mapping) else {}
        model_input = clean_comfy_ref(inputs.get("model"))
        if not isinstance(node, Mapping) or not str(node.get("class_type") or "").strip():
            errors.append({
                "code": "adetailer_identity_sampling_node_missing",
                "field": "identity_protection",
                "message": f"Qwen Edit model-sampling node {node_id} is missing from the active graph.",
                "node_id": node_id,
            })
            continue
        if not model_input:
            errors.append({
                "code": "adetailer_identity_sampling_model_input_missing",
                "field": "identity_protection",
                "message": f"Qwen Edit model-sampling node {node_id} has no explicit MODEL input.",
                "node_id": node_id,
            })
        if index > 0 and previous_ref and model_input != previous_ref:
            errors.append({
                "code": "adetailer_identity_sampling_lineage_disconnected",
                "field": "identity_protection",
                "message": "The Qwen Edit model-sampling nodes are not a single ordered MODEL lineage.",
                "node_id": node_id,
            })
        previous_ref = [node_id, 0]
    return {
        "ready": not errors,
        "required": required,
        "source_node_ids": source_ids,
        "pre_sampling_model_ref": clean_comfy_ref(((workflow.get(source_ids[0]) or {}).get("inputs") or {}).get("model")) if isinstance(workflow, Mapping) and source_ids else None,
        "errors": errors,
    }


def _next_node_id(graph: Mapping[str, Any], fallback: str = "1") -> str:
    numeric = [int(str(key)) for key in graph if str(key).isdigit()]
    return str((max(numeric) + 1) if numeric else int(fallback))


def _reapply_model_sampling_lineage(
    graph: dict[str, Any],
    next_id: str,
    *,
    route_contract: Mapping[str, Any],
    model_ref: list[Any],
) -> dict[str, Any]:
    sampling = route_contract.get("model_sampling") if isinstance(route_contract.get("model_sampling"), Mapping) else {}
    source_ids = [str(item) for item in sampling.get("source_node_ids", []) if str(item).strip()]
    current_ref = deepcopy(model_ref)
    cloned_ids: list[str] = []
    lineage: list[dict[str, Any]] = []
    current_id = str(next_id)
    for source_id in source_ids:
        source_node = graph.get(source_id)
        if not isinstance(source_node, Mapping):
            raise ValueError(f"missing_model_sampling_node:{source_id}")
        cloned = deepcopy(dict(source_node))
        inputs = cloned.get("inputs") if isinstance(cloned.get("inputs"), dict) else {}
        cloned["inputs"] = inputs
        inputs["model"] = deepcopy(current_ref)
        graph[current_id] = cloned
        lineage.append({
            "source_node_id": source_id,
            "cloned_node_id": current_id,
            "class_type": str(cloned.get("class_type") or ""),
            "input_model_ref": deepcopy(current_ref),
            "output_model_ref": [current_id, 0],
        })
        current_ref = [current_id, 0]
        cloned_ids.append(current_id)
        current_id = _next_node_id(graph, current_id)
    return {
        "workflow": graph,
        "next_id": current_id,
        "model_ref": current_ref,
        "node_ids": cloned_ids,
        "lineage": lineage,
    }


def apply_detailer_lora_branch(
    workflow: dict[str, Any],
    next_id: str,
    *,
    plan: Mapping[str, Any],
    model_source_refs: Mapping[str, Any],
    base_contract_refs: Mapping[str, Any] | None = None,
    route_contract: Mapping[str, Any] | None = None,
    identity_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    graph = workflow
    source_refs = model_source_refs if isinstance(model_source_refs, Mapping) else {}
    base_refs = base_contract_refs if isinstance(base_contract_refs, Mapping) else {}
    inheritance = normalize_lora_inheritance(plan.get("inheritance_effective"))
    model_source = normalize_model_source(plan.get("model_source"))
    if model_source == DEDICATED_CHECKPOINT_SOURCE or inheritance == INHERIT_ALL:
        start_refs = source_refs
    else:
        start_refs = base_refs

    identity = identity_policy if isinstance(identity_policy, Mapping) else {}
    sampling_reapply_required = bool(identity.get("sampling_reapply_required"))
    sampling_gate = validate_sampling_reapply_contract(graph, plan=identity, route_contract=route_contract)
    if sampling_reapply_required and sampling_gate.get("ready"):
        pre_sampling_ref = clean_comfy_ref(sampling_gate.get("pre_sampling_model_ref"))
        compiler_model_ref = clean_comfy_ref(plan.get("compiler_model_ref"))
        if inheritance == INHERIT_ALL:
            start_refs = {**dict(start_refs), "model": pre_sampling_ref or compiler_model_ref or clean_comfy_ref(start_refs.get("model"))}
        else:
            start_refs = {**dict(start_refs), "model": compiler_model_ref or pre_sampling_ref or clean_comfy_ref(start_refs.get("model"))}

    model_ref = clean_comfy_ref(start_refs.get("model")) or []
    clip_ref = clean_comfy_ref(start_refs.get("clip")) or []
    positive_ref = clean_comfy_ref(start_refs.get("positive")) or clean_comfy_ref(source_refs.get("positive")) or []
    negative_ref = clean_comfy_ref(start_refs.get("negative")) or clean_comfy_ref(source_refs.get("negative")) or []
    vae_ref = clean_comfy_ref(source_refs.get("vae")) or clean_comfy_ref(start_refs.get("vae")) or []
    rows = plan.get("apply_rows") if isinstance(plan.get("apply_rows"), list) else []
    node_class = _clean_text(plan.get("loader_node_class"))
    requires_clip = bool(plan.get("requires_clip"))
    node_ids: list[str] = []
    current_id = str(next_id)

    for row in rows:
        node_id = current_id
        inputs: dict[str, Any] = {
            "model": deepcopy(model_ref),
            "lora_name": _clean_text(row.get("provider_catalog_name") or row.get("name")),
            "strength_model": float(row.get("strength_model") or 0.0),
        }
        if requires_clip:
            inputs["clip"] = deepcopy(clip_ref)
            inputs["strength_clip"] = float(row.get("strength_clip") or 0.0)
        graph[node_id] = {"class_type": node_class, "inputs": inputs}
        model_ref = [node_id, 0]
        if requires_clip:
            clip_ref = [node_id, 1]
        node_ids.append(node_id)
        numeric_ids = [int(str(key)) for key in graph if str(key).isdigit()]
        current_id = str((max(numeric_ids) if numeric_ids else int(node_id)) + 1)

    sampling_reapply = {
        "required": sampling_reapply_required,
        "ready": bool(sampling_gate.get("ready", True)),
        "source_node_ids": deepcopy(sampling_gate.get("source_node_ids") or []),
        "pre_sampling_model_ref": deepcopy(sampling_gate.get("pre_sampling_model_ref") or []),
        "cloned_node_ids": [],
        "lineage": [],
        "final_model_ref": deepcopy(model_ref),
        "errors": deepcopy(sampling_gate.get("errors") or []),
    }
    if sampling_reapply_required:
        if not sampling_gate.get("ready"):
            raise ValueError("adetailer_identity_sampling_reapply_contract_invalid")
        if sampling_gate.get("source_node_ids"):
            reapplied = _reapply_model_sampling_lineage(
                graph,
                current_id,
                route_contract=route_contract if isinstance(route_contract, Mapping) else {},
                model_ref=model_ref,
            )
            graph = reapplied["workflow"]
            current_id = str(reapplied["next_id"])
            model_ref = deepcopy(reapplied["model_ref"])
            node_ids.extend(reapplied["node_ids"])
            sampling_reapply.update({
                "cloned_node_ids": deepcopy(reapplied["node_ids"]),
                "lineage": deepcopy(reapplied["lineage"]),
                "final_model_ref": deepcopy(model_ref),
            })
        else:
            sampling_reapply.update({
                "passthrough": True,
                "final_model_ref": deepcopy(model_ref),
            })

    metadata = public_lora_branch_metadata({
        **dict(plan),
        "starting_model_ref": clean_comfy_ref(start_refs.get("model")) or [],
        "starting_clip_ref": clean_comfy_ref(start_refs.get("clip")) or [],
        "patched_model_ref": deepcopy(model_ref),
        "patched_clip_ref": deepcopy(clip_ref),
        "node_ids": node_ids,
        "applied": bool(node_ids),
        "sampling_reapply": sampling_reapply,
    })
    return {
        "workflow": graph,
        "next_id": current_id,
        "model_ref": model_ref,
        "clip_ref": clip_ref,
        "vae_ref": vae_ref,
        "positive_ref": positive_ref,
        "negative_ref": negative_ref,
        "node_ids": node_ids,
        "requires_prompt_reencode": bool(plan.get("requires_prompt_reencode")),
        "sampling_reapply": sampling_reapply,
        "lora_branch": metadata,
    }


def public_lora_branch_metadata(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    source = plan if isinstance(plan, Mapping) else {}
    rows = source.get("apply_rows") if isinstance(source.get("apply_rows"), list) else []
    return {
        "schema_id": LORA_BRANCH_SCHEMA_ID,
        "ready": bool(source.get("ready", True)),
        "requested": bool(source.get("requested")),
        "applied": bool(source.get("applied")),
        "model_source": _clean_text(source.get("model_source")),
        "inheritance_requested": normalize_lora_inheritance(source.get("inheritance_requested")),
        "inheritance_effective": normalize_lora_inheritance(source.get("inheritance_effective")),
        "anchor": _clean_text(source.get("anchor")),
        "strategy": _clean_text(source.get("strategy")),
        "loader_node_class": _clean_text(source.get("loader_node_class")),
        "requires_clip": bool(source.get("requires_clip")),
        "requires_prompt_reencode": bool(source.get("requires_prompt_reencode")),
        "inherited_runtime_count": int(source.get("inherited_runtime_count") or 0),
        "selected_generation_count": len(source.get("selected_generation_rows") or []),
        "finish_lora_count": len(source.get("finish_rows") or []),
        "direct_lora_count": len(source.get("direct_rows") or []),
        "applied_lora_count": len(rows),
        "lora_names": [_clean_text(row.get("portable_catalog_name") or row.get("name")).rsplit("/", 1)[-1] for row in rows],
        "lora_rows": [
            {
                "uid": _clean_text(row.get("uid")),
                "name": _clean_text(row.get("portable_catalog_name") or row.get("name")),
                "provider_catalog_name": _clean_text(row.get("provider_catalog_name")),
                "strength_model": row.get("strength_model"),
                "strength_clip": row.get("strength_clip"),
                "source": _clean_text(row.get("source")),
                "trigger": _clean_text(row.get("trigger")),
            }
            for row in rows
        ],
        "catalog_bindings": deepcopy(source.get("catalog_bindings") or []),
        "node_ids": [str(item) for item in source.get("node_ids", [])],
        "starting_model_ref": deepcopy(source.get("starting_model_ref") or []),
        "starting_clip_ref": deepcopy(source.get("starting_clip_ref") or []),
        "patched_model_ref": deepcopy(source.get("patched_model_ref") or []),
        "patched_clip_ref": deepcopy(source.get("patched_clip_ref") or []),
        "compiler_model_ref": deepcopy(source.get("compiler_model_ref") or []),
        "compiler_clip_ref": deepcopy(source.get("compiler_clip_ref") or []),
        "sampling_reapply": deepcopy(source.get("sampling_reapply") or {}),
        "errors": deepcopy(source.get("errors") or []),
        "warnings": deepcopy(source.get("warnings") or []),
    }
