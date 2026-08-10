from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

from .constants import EXTENSION_ID
from .support_matrix import normalize_route, route_key

EXECUTION_RECIPE_SCHEMA_ID = "neo.image.adetailer.execution_recipe.v1"
REPLAY_CONTRACT_SCHEMA_ID = "neo.image.adetailer.replay_contract.v1"

_SAMPLING_KEYS = (
    "steps",
    "cfg",
    "denoise",
    "sampler_name",
    "scheduler",
    "guide_size",
    "max_size",
    "noise_mask",
    "force_inpaint",
    "noise_mask_feather",
)
_ROUTE_MATCH_KEYS = ("backend", "family", "loader", "mode")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _fingerprint_payload(value: Mapping[str, Any]) -> str:
    clean = deepcopy(dict(value))
    clean.pop("fingerprint", None)
    clean.pop("fingerprint_algorithm", None)
    return hashlib.sha256(_canonical_json(clean).encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _portable_route(route: Mapping[str, Any] | None, *, state: str = "") -> dict[str, Any]:
    normalized = normalize_route(dict(route or {}))
    return {
        "backend": normalized["backend"],
        "family": normalized["family"],
        "loader": normalized["loader"],
        "mode": normalized["mode"],
        "workspace": normalized["workspace"],
        "subtab": normalized["subtab"],
        "route_key": route_key(dict(route or {})),
        "state": _clean_text(state),
    }


def _effective_sampling(params: Mapping[str, Any], family_preset: Mapping[str, Any]) -> dict[str, Any]:
    values = family_preset.get("effective_values") if isinstance(family_preset.get("effective_values"), Mapping) else {}
    return {
        key: deepcopy(values.get(key) if key in values else params.get(key))
        for key in _SAMPLING_KEYS
    }


def _detector_plan(params: Mapping[str, Any], pass_summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passes: list[dict[str, Any]] = []
    for index, raw in enumerate(pass_summaries):
        item = raw if isinstance(raw, Mapping) else {}
        passes.append({
            "order": index,
            "pass_id": _clean_text(item.get("pass_id")),
            "label": _clean_text(item.get("label")),
            "detector_model": _clean_text(item.get("detector_model")),
            "patch_path": _clean_text(item.get("patch_path")),
            "target_mode": _clean_text(item.get("target_mode")),
            "manual_box_index": item.get("manual_box_index"),
            "sep_target_index": item.get("sep_target_index"),
            "sep_target_total": item.get("sep_target_total"),
        })
    return {
        "provider": _clean_text(params.get("provider") or "ultralytics"),
        "detector_type": _clean_text(params.get("detector_type") or "bbox"),
        "primary_detector": _clean_text(params.get("detector_model")),
        "runtime_passes": passes,
    }


def _graph_signature(patch: Mapping[str, Any]) -> dict[str, Any]:
    invariants = patch.get("graph_invariants") if isinstance(patch.get("graph_invariants"), Mapping) else {}
    lora = patch.get("detailer_lora_branch") if isinstance(patch.get("detailer_lora_branch"), Mapping) else {}
    identity = patch.get("identity_policy") if isinstance(patch.get("identity_policy"), Mapping) else {}
    reapply = identity.get("sampling_reapply") if isinstance(identity.get("sampling_reapply"), Mapping) else {}
    return {
        "patch_path": _clean_text(patch.get("patch_path")),
        "patch_paths": deepcopy(patch.get("patch_paths") or []),
        "runtime_unit_count": int(patch.get("runtime_unit_count") or 0),
        "detailer_node_classes": [
            _clean_text(item.get("patch_path"))
            for item in (patch.get("pass_summaries") or [])
            if isinstance(item, Mapping) and _clean_text(item.get("patch_path"))
        ],
        "lora_loader_strategy": _clean_text(lora.get("strategy")),
        "lora_loader_node_class": _clean_text(lora.get("loader_node_class")),
        "sampling_reapply_classes": deepcopy(reapply.get("cloned_node_classes") or []),
        "output_rewire_ready": bool(invariants.get("ready")),
        "main_sampler_isolated": any(
            isinstance(item, Mapping)
            and item.get("check") == "main_sampler_model_unchanged"
            and item.get("ok") is True
            for item in (invariants.get("checks") or [])
        ),
    }


def _replay_params(
    requested_params: Mapping[str, Any],
    effective_params: Mapping[str, Any],
    family_preset: Mapping[str, Any],
    pass_summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    params = deepcopy(dict(requested_params or {}))
    sampling = _effective_sampling(effective_params, family_preset)
    params.update(sampling)
    # A replay must preserve the exact values that reached FaceDetailer rather
    # than silently resolving a newer family preset revision.
    params["family_preset_mode"] = "manual"

    resolved_by_pass: dict[str, str] = {}
    primary_detector = ""
    for raw in pass_summaries:
        if not isinstance(raw, Mapping):
            continue
        detector = _clean_text(raw.get("detector_model"))
        pass_id = _clean_text(raw.get("pass_id"))
        if detector and not primary_detector:
            primary_detector = detector
        if pass_id and detector:
            resolved_by_pass.setdefault(pass_id, detector)
    raw_passes = params.get("detailer_passes") if isinstance(params.get("detailer_passes"), list) else []
    replay_passes: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_passes):
        item = deepcopy(raw) if isinstance(raw, Mapping) else {}
        pass_id = _clean_text(item.get("id") or ("primary" if index == 0 else f"pass-{index + 1}"))
        if resolved_by_pass.get(pass_id):
            item["detector_model"] = resolved_by_pass[pass_id]
        replay_passes.append(item)
    if replay_passes:
        params["detailer_passes"] = replay_passes
        params["detector_model"] = _clean_text(replay_passes[0].get("detector_model")) or primary_detector or _clean_text(params.get("detector_model"))
    elif primary_detector:
        params["detector_model"] = primary_detector
    params["enabled"] = False
    return params


def build_execution_recipe(
    validation_result: Mapping[str, Any] | None,
    workflow_patch: Mapping[str, Any] | None,
    route: Mapping[str, Any] | None,
) -> dict[str, Any]:
    validation = validation_result if isinstance(validation_result, Mapping) else {}
    patch = workflow_patch if isinstance(workflow_patch, Mapping) else {}
    block = validation.get("block") if isinstance(validation.get("block"), Mapping) else {}
    requested_params = block.get("params") if isinstance(block.get("params"), Mapping) else {}
    effective_params = patch.get("params_used") if isinstance(patch.get("params_used"), Mapping) else requested_params
    support = validation.get("support") if isinstance(validation.get("support"), Mapping) else {}
    family_preset = patch.get("family_preset") if isinstance(patch.get("family_preset"), Mapping) else {}
    model_source = patch.get("detailer_model_source") if isinstance(patch.get("detailer_model_source"), Mapping) else {}
    lora_branch = patch.get("detailer_lora_branch") if isinstance(patch.get("detailer_lora_branch"), Mapping) else {}
    identity_policy = patch.get("identity_policy") if isinstance(patch.get("identity_policy"), Mapping) else {}
    diagnostics = patch.get("prequeue_diagnostics") if isinstance(patch.get("prequeue_diagnostics"), Mapping) else validation.get("prequeue_diagnostics") if isinstance(validation.get("prequeue_diagnostics"), Mapping) else {}
    pass_summaries = patch.get("pass_summaries") if isinstance(patch.get("pass_summaries"), list) else []
    skipped_passes = patch.get("skipped_passes") if isinstance(patch.get("skipped_passes"), list) else []
    applied = bool(patch.get("applied") or patch.get("mutated"))
    route_binding = _portable_route(route or patch.get("route") or {}, state=_clean_text(support.get("state")))
    replay_params = _replay_params(requested_params, effective_params, family_preset, pass_summaries)

    recipe: dict[str, Any] = {
        "schema_id": EXECUTION_RECIPE_SCHEMA_ID,
        "schema_version": 1,
        "extension_id": EXTENSION_ID,
        "source_phase": "Phase 9",
        "applied": applied,
        "status": "applied" if applied else ("gated" if bool(block.get("enabled")) else "disabled"),
        "route": route_binding,
        "model_source": deepcopy(model_source),
        "sampling": {
            "requested_mode": _clean_text(requested_params.get("family_preset_mode") or "auto_family"),
            "replay_mode": "manual",
            "family": _clean_text(family_preset.get("family")),
            "preset_id": _clean_text(family_preset.get("preset_id")),
            "effective_values": _effective_sampling(effective_params, family_preset),
            "value_sources": deepcopy(family_preset.get("value_sources") or {}),
        },
        "detector": _detector_plan(effective_params, pass_summaries),
        "lora_branch": deepcopy(lora_branch),
        "identity_policy": deepcopy(identity_policy),
        "passes": deepcopy(pass_summaries),
        "skipped_passes": deepcopy(skipped_passes),
        "graph_signature": _graph_signature(patch),
        "warnings": {
            "codes": deepcopy(diagnostics.get("warning_codes") or []),
            "identity_claim": _clean_text(identity_policy.get("identity_claim")),
            "acknowledged_codes": [],
            "reconfirmation_required": bool(diagnostics.get("warning_codes")),
        },
        "replay": {
            "schema_id": REPLAY_CONTRACT_SCHEMA_ID,
            "locked": True,
            "auto_enable": False,
            "revalidation_required": True,
            "params": replay_params,
            "route": route_binding,
            "preserves": [
                "model_source",
                "effective_sampling",
                "detector_bindings",
                "detailer_pass_order",
                "lora_scope_and_order",
                "identity_policy",
                "warning_state",
            ],
        },
    }
    recipe["fingerprint_algorithm"] = "sha256_canonical_json"
    recipe["fingerprint"] = _fingerprint_payload(recipe)
    return recipe


def validate_execution_recipe(recipe: Mapping[str, Any] | None) -> dict[str, Any]:
    source = recipe if isinstance(recipe, Mapping) else {}
    errors: list[dict[str, Any]] = []
    if not source:
        return {"applicable": False, "ready": True, "errors": [], "warnings": [], "recipe": {}}
    if source.get("schema_id") != EXECUTION_RECIPE_SCHEMA_ID:
        errors.append({
            "code": "adetailer_replay_recipe_schema_invalid",
            "message": "The saved ADetailer execution recipe uses an unsupported schema.",
            "field": "metadata.execution_recipe.schema_id",
        })
    recorded = _clean_text(source.get("fingerprint"))
    calculated = _fingerprint_payload(source)
    if not recorded or recorded != calculated:
        errors.append({
            "code": "adetailer_replay_recipe_fingerprint_mismatch",
            "message": "The saved ADetailer execution recipe fingerprint does not match its contents.",
            "field": "metadata.execution_recipe.fingerprint",
            "recorded_fingerprint": recorded,
            "calculated_fingerprint": calculated,
        })
    replay = source.get("replay") if isinstance(source.get("replay"), Mapping) else {}
    if replay.get("schema_id") != REPLAY_CONTRACT_SCHEMA_ID or not isinstance(replay.get("params"), Mapping):
        errors.append({
            "code": "adetailer_replay_contract_missing",
            "message": "The saved ADetailer recipe does not contain a complete replay contract.",
            "field": "metadata.execution_recipe.replay",
        })
    return {
        "applicable": True,
        "ready": not errors,
        "errors": errors,
        "warnings": [],
        "recipe": deepcopy(dict(source)),
        "fingerprint": recorded,
        "calculated_fingerprint": calculated,
    }


def _normalized_compare(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def validate_replay_execution_recipe(
    block: Mapping[str, Any] | None,
    *,
    route: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = block if isinstance(block, Mapping) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
    recipe = metadata.get("execution_recipe") if isinstance(metadata.get("execution_recipe"), Mapping) else {}
    base = validate_execution_recipe(recipe)
    if not base.get("applicable") or not base.get("ready"):
        return base

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    recorded_route = recipe.get("route") if isinstance(recipe.get("route"), Mapping) else {}
    current_route = _portable_route(route or {})
    mismatches = {
        key: {"recorded": recorded_route.get(key), "current": current_route.get(key)}
        for key in _ROUTE_MATCH_KEYS
        if _clean_text(recorded_route.get(key)) != _clean_text(current_route.get(key))
    }
    if mismatches:
        errors.append({
            "code": "adetailer_replay_route_mismatch",
            "message": "The saved ADetailer execution recipe belongs to a different backend/family/loader/mode route.",
            "field": "metadata.execution_recipe.route",
            "route_mismatches": mismatches,
        })

    replay = recipe.get("replay") if isinstance(recipe.get("replay"), Mapping) else {}
    expected_params = replay.get("params") if isinstance(replay.get("params"), Mapping) else {}
    actual_params = source.get("params") if isinstance(source.get("params"), Mapping) else {}
    expected_compare = {key: value for key, value in expected_params.items() if key != "enabled"}
    actual_compare = {key: actual_params.get(key) for key in expected_compare}
    if _normalized_compare(expected_compare) != _normalized_compare(actual_compare):
        errors.append({
            "code": "adetailer_replay_recipe_drift",
            "message": "The restored ADetailer settings no longer match the locked execution recipe.",
            "field": "params",
        })

    warning_state = recipe.get("warnings") if isinstance(recipe.get("warnings"), Mapping) else {}
    if warning_state.get("reconfirmation_required"):
        warnings.append({
            "code": "adetailer_replay_warning_reconfirmation_required",
            "message": "The original ADetailer run contained warnings; review them again before re-enabling the restored recipe.",
            "field": "metadata.execution_recipe.warnings",
            "warning_codes": deepcopy(warning_state.get("codes") or []),
        })

    return {
        **base,
        "ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "recorded_route": deepcopy(dict(recorded_route)),
        "current_route": current_route,
        "locked": True,
        "auto_enable": False,
    }


def build_locked_replay_block(block: Mapping[str, Any] | None, recipe: Mapping[str, Any]) -> dict[str, Any]:
    source = deepcopy(dict(block or {}))
    replay = recipe.get("replay") if isinstance(recipe.get("replay"), Mapping) else {}
    params = replay.get("params") if isinstance(replay.get("params"), Mapping) else {}
    source["enabled"] = False
    source["params"] = deepcopy(dict(params))
    source["params"]["enabled"] = False
    source.setdefault("metadata", {})
    if isinstance(source["metadata"], dict):
        source["metadata"].update({
            "source_phase": "Phase 9",
            "execution_recipe": deepcopy(dict(recipe)),
            "execution_recipe_fingerprint": _clean_text(recipe.get("fingerprint")),
            "replay_contract_schema_id": REPLAY_CONTRACT_SCHEMA_ID,
            "replay_recipe_locked": True,
            "revalidation_required": True,
            "ready_to_auto_enable": False,
            "restore_policy": "restore_exact_effective_adetailer_recipe_disabled_then_revalidate_route_nodes_models_detectors_loras_identity_sampling_and_warnings",
        })
    return source
