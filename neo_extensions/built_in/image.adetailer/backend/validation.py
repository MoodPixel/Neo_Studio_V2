from __future__ import annotations

from typing import Any

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, PHASE
from .node_discovery import node_gate_for_support
from .payload_schema import normalize_block
from .model_source import validate_detailer_model_source_selection
from .lora_branch import validate_detailer_lora_branch
from .family_presets import resolve_family_preset_plan
from .identity_policy import validate_qwen_edit_identity_policy
from .support_matrix import support_for_route
from .diagnostics import refresh_prequeue_diagnostics, validate_critical_node_signatures
from .execution_recipe import validate_replay_execution_recipe


def _block_enabled(block: dict[str, Any]) -> bool:
    return bool(block.get("enabled") or block.get("params", {}).get("enabled") or block.get("inputs", {}).get("enabled"))


def _validation_item(level: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"extension_id": EXTENSION_ID, "level": level, "code": code, "message": message, **extra}


def validate_and_normalize_payload(payload: Any, *, route: dict[str, Any] | None = None, available_nodes: Any = None, lora_patch_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    block = normalize_block(payload)
    raw_support = support_for_route(route)
    support = node_gate_for_support(raw_support, available_nodes)
    enabled = _block_enabled(block)
    params = block.get("params", {})
    node_status = support.get("node_status", {})
    normalization = block.get("metadata", {}).get("normalization", {})
    model_source = validate_detailer_model_source_selection(
        params,
        route=route,
        available_nodes=available_nodes,
        detailer_passes=params.get("detailer_passes"),
    )
    lora_branch = validate_detailer_lora_branch(
        params,
        payload=payload,
        route=route,
        available_nodes=available_nodes,
        model_source=model_source,
        lora_patch_profile=lora_patch_profile,
    )

    identity_policy = validate_qwen_edit_identity_policy(
        params,
        route=route,
        model_source=model_source,
        lora_branch=lora_branch,
    )

    family_preset = resolve_family_preset_plan(
        params,
        route=route,
        model_source=model_source,
        available_nodes=available_nodes,
    )
    node_contract = validate_critical_node_signatures(
        available_nodes,
        params=params,
        model_source=model_source,
        lora_branch=lora_branch,
    )
    replay_contract = validate_replay_execution_recipe(block, route=route or {})

    validations: list[dict[str, Any]] = []
    warnings = list(normalization.get("warnings", []))
    ignored_params = list(normalization.get("ignored_params", []))
    clamped_params = list(normalization.get("clamped_params", []))

    if ignored_params:
        validations.append(_validation_item(
            "warning",
            "adetailer_stale_fields_removed",
            "Ignored ADetailer fields were removed from the clean payload.",
            ignored_params=ignored_params,
        ))
    if clamped_params:
        validations.append(_validation_item(
            "warning",
            "adetailer_params_clamped",
            "One or more ADetailer numeric values were clamped to safe V1-compatible limits.",
            clamped_params=clamped_params,
        ))
    for warning in warnings:
        validations.append(_validation_item(
            "warning",
            f"adetailer_{warning}",
            f"ADetailer payload normalization warning: {warning}.",
        ))

    pass_count = int(normalization.get("detailer_pass_count") or 0)
    enabled_pass_count = int(normalization.get("enabled_detailer_pass_count") or 0)
    if pass_count > 1:
        validations.append(_validation_item(
            "info",
            "adetailer_multi_pass_payload_ready",
            "ADetailer multi-pass payload was normalized as first-class runtime data.",
            detailer_pass_count=pass_count,
            enabled_detailer_pass_count=enabled_pass_count,
            primary_runtime_pass_id=normalization.get("primary_runtime_pass_id"),
        ))

    if enabled and enabled_pass_count == 0:
        validations.append(_validation_item(
            "error",
            "adetailer_no_enabled_detailer_passes",
            "ADetailer is requested but all detailer passes are disabled; the request is blocked instead of silently queueing the base image.",
            detailer_pass_count=pass_count, blocked=True, ok=False, stage="pass_payload",
            remediation="Enable at least one valid ADetailer pass or turn ADetailer off before queueing.",
        ))

    if not enabled:
        validations.append(_validation_item(
            "info",
            "adetailer_not_requested",
            "ADetailer was not requested for this run; no runtime workflow mutation is allowed.",
            route_state=support["state"],
        ))
    elif support["state"] in ACTIVE_ROUTE_STATES and node_status.get("ready"):
        validations.append(_validation_item(
            "info",
            "adetailer_payload_ready",
            "ADetailer payload is clean and route/node readiness permits a later workflow patch phase.",
            route_state=support["state"],
            node_status="ready",
        ))
    elif raw_support["state"] in ACTIVE_ROUTE_STATES and not node_status.get("ready"):
        validations.append(_validation_item(
            "error", support.get("reason_code", "nodes_missing"),
            support.get("reason", "Required ADetailer nodes are not available."),
            route_state=support["state"], pre_node_state=support.get("pre_node_state"),
            missing_required=node_status.get("missing_required", []), blocked=True, ok=False, stage="node_inventory",
            remediation="Install or update Impact Pack/Impact Subpack, restart ComfyUI, and reconnect Neo to refresh /object_info.",
        ))
    else:
        validations.append(_validation_item(
            "error", f"adetailer_{support['state']}",
            support.get("reason", "ADetailer route is gated or unsupported."),
            route_state=support["state"], reason=support.get("reason"), blocked=True, ok=False, stage="route_support",
            remediation="Choose an exact family/loader/mode route marked Available or Experimental Available for ADetailer.",
        ))

    # Saved model-source selections may remain in a disabled ADetailer draft.
    # Validate and expose the plan, but do not emit blocking execution errors until
    # the extension is actually requested for this run.
    if enabled:
        for issue in model_source.get("warnings", []):
            validations.append(_validation_item(
                "warning",
                str(issue.get("code") or "adetailer_model_source_warning"),
                str(issue.get("message") or "ADetailer model-source warning."),
                field=issue.get("field"),
                blocked=False,
                ok=True,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))
        for issue in model_source.get("errors", []):
            validations.append(_validation_item(
                "error",
                str(issue.get("code") or "adetailer_model_source_invalid"),
                str(issue.get("message") or "ADetailer model-source selection is invalid."),
                field=issue.get("field"),
                blocked=True,
                ok=False,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))

    if enabled:
        for issue in lora_branch.get("warnings", []):
            validations.append(_validation_item(
                "warning",
                str(issue.get("code") or "adetailer_lora_branch_warning"),
                str(issue.get("message") or "ADetailer LoRA branch warning."),
                field=issue.get("field"),
                blocked=False,
                ok=True,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))
        for issue in lora_branch.get("errors", []):
            validations.append(_validation_item(
                "error",
                str(issue.get("code") or "adetailer_lora_branch_invalid"),
                str(issue.get("message") or "ADetailer LoRA branch selection is invalid."),
                field=issue.get("field"),
                blocked=True,
                ok=False,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))

    if enabled:
        for issue in identity_policy.get("warnings", []):
            validations.append(_validation_item(
                "warning",
                str(issue.get("code") or "adetailer_identity_policy_warning"),
                str(issue.get("message") or "ADetailer identity-policy warning."),
                field=issue.get("field"),
                blocked=False,
                ok=True,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))
        for issue in identity_policy.get("errors", []):
            validations.append(_validation_item(
                "error",
                str(issue.get("code") or "adetailer_identity_policy_invalid"),
                str(issue.get("message") or "ADetailer identity-policy selection is invalid."),
                field=issue.get("field"),
                blocked=True,
                ok=False,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))


    if enabled:
        for issue in node_contract.get("warnings", []):
            validations.append(_validation_item(
                "warning", str(issue.get("code") or "adetailer_node_signatures_unchecked"),
                str(issue.get("message") or "ADetailer node-signature validation warning."),
                field=issue.get("field"), blocked=False, ok=True, stage="node_signatures", remediation=issue.get("remediation"),
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field", "level", "blocked", "ok", "stage", "remediation", "extension_id"}},
            ))
        for issue in node_contract.get("errors", []):
            validations.append(_validation_item(
                "error", str(issue.get("code") or "adetailer_node_signature_invalid"),
                str(issue.get("message") or "ADetailer node-signature validation failed."),
                field=issue.get("field"), blocked=True, ok=False, stage="node_signatures", remediation=issue.get("remediation"),
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field", "level", "blocked", "ok", "stage", "remediation", "extension_id"}},
            ))

    if enabled and replay_contract.get("applicable"):
        for issue in replay_contract.get("warnings", []):
            validations.append(_validation_item(
                "warning",
                str(issue.get("code") or "adetailer_replay_recipe_warning"),
                str(issue.get("message") or "ADetailer replay-recipe warning."),
                field=issue.get("field"),
                blocked=False,
                ok=True,
                stage="replay_recipe",
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field", "stage", "blocked", "ok", "level"}},
            ))
        for issue in replay_contract.get("errors", []):
            validations.append(_validation_item(
                "error",
                str(issue.get("code") or "adetailer_replay_recipe_invalid"),
                str(issue.get("message") or "ADetailer replay recipe is invalid."),
                field=issue.get("field"),
                blocked=True,
                ok=False,
                stage="replay_recipe",
                remediation="Reload the saved output recipe without editing it, or start a new ADetailer configuration for the current route.",
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field", "stage", "blocked", "ok", "level", "remediation"}},
            ))

    if enabled:
        for issue in family_preset.get("warnings", []):
            validations.append(_validation_item(
                "warning",
                str(issue.get("code") or "adetailer_family_preset_warning"),
                str(issue.get("message") or "ADetailer family-preset warning."),
                field=issue.get("field"),
                blocked=False,
                ok=True,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))
        for issue in family_preset.get("errors", []):
            validations.append(_validation_item(
                "error",
                str(issue.get("code") or "adetailer_family_preset_invalid"),
                str(issue.get("message") or "ADetailer family-preset selection is invalid."),
                field=issue.get("field"),
                blocked=True,
                ok=False,
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field"}},
            ))

    runtime_ready = enabled and enabled_pass_count > 0 and support["state"] in ACTIVE_ROUTE_STATES and bool(node_status.get("ready")) and bool(model_source.get("ready")) and bool(lora_branch.get("ready")) and bool(identity_policy.get("ready")) and bool(family_preset.get("ready")) and bool(node_contract.get("ready")) and bool(replay_contract.get("ready", True))
    workflow_patch_allowed = runtime_ready and bool(support.get("workflow_patch_allowed"))
    active_patch_data_allowed = workflow_patch_allowed

    result = {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "skeleton_only": False,
        "multi_pass_payload_ready": bool(normalization.get("multi_pass_payload_ready")),
        "enabled": enabled,
        "runtime_ready": runtime_ready,
        "workflow_patch_allowed": workflow_patch_allowed,
        "workflow_patch_ready_for_later_phase": False,
        "active_patch_data_allowed": active_patch_data_allowed,
        "block": block,
        "params": params,
        "derived": normalization,
        "support": support,
        "raw_support": raw_support,
        "node_status": node_status,
        "model_source": model_source,
        "lora_branch": lora_branch,
        "identity_policy": identity_policy,
        "family_preset": family_preset,
        "node_contract": node_contract,
        "replay_contract": replay_contract,
        "validation": validations,
    }
    refresh_prequeue_diagnostics(result, runtime={"phase": "payload_preflight", "graph_mutation_started": False})
    return result
