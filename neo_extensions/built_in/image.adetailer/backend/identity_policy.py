from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .lora_branch import MODEL_ONLY_STRATEGIES
from .model_source import DEDICATED_CHECKPOINT_SOURCE, GENERATION_MODEL_SOURCE, normalize_model_source

IDENTITY_POLICY_SCHEMA_ID = "neo.image.adetailer.qwen_edit_identity_policy.v1"

IDENTITY_NONE = "none"
IDENTITY_DETAILER_LORA = "detailer_identity_lora"
IDENTITY_DEDICATED_MODEL = "dedicated_detailer_model"
VALID_IDENTITY_MODES = {IDENTITY_NONE, IDENTITY_DETAILER_LORA, IDENTITY_DEDICATED_MODEL}

QWEN_EDIT_2509 = "qwen_image_edit_2509"
QWEN_EDIT_2511 = "qwen_image_edit_2511"
QWEN_EDIT_FAMILIES = {QWEN_EDIT_2509, QWEN_EDIT_2511}

REVISION_ROUTE = "route_family"
REVISION_BOTH = "both"
VALID_IDENTITY_LORA_REVISIONS = {REVISION_ROUTE, REVISION_BOTH, QWEN_EDIT_2509, QWEN_EDIT_2511}


def _clean(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_identity_protection(value: Any) -> str:
    token = _clean(value)
    aliases = {
        "": IDENTITY_NONE,
        "off": IDENTITY_NONE,
        "disabled": IDENTITY_NONE,
        "no_protection": IDENTITY_NONE,
        "identity_lora": IDENTITY_DETAILER_LORA,
        "detailer_lora": IDENTITY_DETAILER_LORA,
        "lora": IDENTITY_DETAILER_LORA,
        "dedicated": IDENTITY_DEDICATED_MODEL,
        "dedicated_model": IDENTITY_DEDICATED_MODEL,
        "dedicated_checkpoint": IDENTITY_DEDICATED_MODEL,
    }
    resolved = aliases.get(token, token or IDENTITY_NONE)
    return resolved if resolved in VALID_IDENTITY_MODES else IDENTITY_NONE


def normalize_identity_lora_revision(value: Any) -> str:
    token = _clean(value)
    aliases = {
        "": REVISION_ROUTE,
        "auto": REVISION_ROUTE,
        "current": REVISION_ROUTE,
        "current_route": REVISION_ROUTE,
        "route": REVISION_ROUTE,
        "2509": QWEN_EDIT_2509,
        "qwen_2509": QWEN_EDIT_2509,
        "2511": QWEN_EDIT_2511,
        "qwen_2511": QWEN_EDIT_2511,
        "all": REVISION_BOTH,
        "either": REVISION_BOTH,
    }
    resolved = aliases.get(token, token or REVISION_ROUTE)
    return resolved if resolved in VALID_IDENTITY_LORA_REVISIONS else REVISION_ROUTE


def normalize_qwen_edit_family(value: Any) -> str:
    token = _clean(value)
    aliases = {
        "qwen_2509": QWEN_EDIT_2509,
        "qwen_image_edit": QWEN_EDIT_2509,
        "qwen_image_edit_plus_2509": QWEN_EDIT_2509,
        "qwen_2511": QWEN_EDIT_2511,
        "qwen_image_edit_plus_2511": QWEN_EDIT_2511,
    }
    return aliases.get(token, token)


def _route_family(route: Mapping[str, Any] | None) -> str:
    source = route if isinstance(route, Mapping) else {}
    return normalize_qwen_edit_family(source.get("family") or source.get("model_family"))


def _error(code: str, message: str, *, field: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "field": field, **extra}


def _warning(code: str, message: str, *, field: str = "identity_protection", **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "field": field, **extra}


def validate_qwen_edit_identity_policy(
    params: Mapping[str, Any] | None,
    *,
    route: Mapping[str, Any] | None,
    model_source: Mapping[str, Any] | None,
    lora_branch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_params = params if isinstance(params, Mapping) else {}
    family = _route_family(route)
    applicable = family in QWEN_EDIT_FAMILIES
    requested = normalize_identity_protection(source_params.get("identity_protection"))
    declared_revision = normalize_identity_lora_revision(source_params.get("identity_lora_revision"))
    source_plan = model_source if isinstance(model_source, Mapping) else {}
    source = normalize_model_source(source_plan.get("source") or source_params.get("model_source"))
    lora_plan = lora_branch if isinstance(lora_branch, Mapping) else {}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not applicable:
        if requested != IDENTITY_NONE:
            errors.append(_error(
                "adetailer_identity_policy_route_unsupported",
                "Qwen Edit identity protection controls can only be used on Qwen Image Edit 2509 or 2511 routes.",
                field="identity_protection",
                family=family,
            ))
        return {
            "schema_id": IDENTITY_POLICY_SCHEMA_ID,
            "ready": not errors,
            "applicable": False,
            "family": family,
            "requested": requested,
            "effective": IDENTITY_NONE,
            "status": "not_applicable",
            "identity_claim": "none",
            "sampling_reapply_required": False,
            "declared_lora_revision": declared_revision,
            "compatibility_evidence": "not_applicable",
            "errors": errors,
            "warnings": warnings,
        }

    effective = requested
    status = "identity_risk_warning"
    identity_claim = "not_guaranteed"
    model_only_lora_branch_active = bool(
        source == GENERATION_MODEL_SOURCE
        and str(lora_plan.get("strategy") or "") in MODEL_ONLY_STRATEGIES
        and isinstance(lora_plan.get("apply_rows"), list)
        and lora_plan.get("apply_rows")
    )
    sampling_reapply_required = model_only_lora_branch_active
    compatibility_evidence = "none"

    if requested == IDENTITY_NONE:
        warning_code = (
            "adetailer_qwen_edit_2509_identity_drift_risk"
            if family == QWEN_EDIT_2509
            else "adetailer_qwen_edit_2511_identity_not_guaranteed"
        )
        warning_text = (
            "Native Qwen Image Edit 2509 FaceDetailer can change facial identity. Select a compatible detailer-only identity LoRA or a dedicated SDXL/SD1.5 detailer model when identity consistency matters."
            if family == QWEN_EDIT_2509
            else "Qwen Image Edit 2511 improves character consistency, but FaceDetailer identity preservation is still not guaranteed. Use a compatible detailer-only identity LoRA or a dedicated detailer model for critical identity work."
        )
        warnings.append(_warning(warning_code, warning_text))
        if model_only_lora_branch_active:
            warnings.append(_warning(
                "adetailer_qwen_edit_model_only_lora_sampling_reapply",
                "The isolated Qwen Edit LoRA branch will reapply the compiler-owned model-sampling wrappers, but no identity-preservation claim is active.",
                field="detailer_lora",
            ))
    elif requested == IDENTITY_DETAILER_LORA:
        status = "lora_assisted_experimental"
        compatibility_evidence = "user_declared_revision_and_live_catalog_binding"
        if source != GENERATION_MODEL_SOURCE:
            errors.append(_error(
                "adetailer_identity_lora_requires_generation_model",
                "The Qwen Edit identity-LoRA path requires Detailer model source = Use generation model.",
                field="model_source",
                model_source=source,
            ))
        direct_rows = lora_plan.get("direct_rows") if isinstance(lora_plan.get("direct_rows"), list) else []
        apply_rows = lora_plan.get("apply_rows") if isinstance(lora_plan.get("apply_rows"), list) else []
        direct_names = {str(row.get("name") or "").casefold() for row in direct_rows if isinstance(row, Mapping)}
        applied_direct = [
            row for row in apply_rows
            if isinstance(row, Mapping)
            and (
                str(row.get("source") or "") == "adetailer_direct"
                or str(row.get("portable_catalog_name") or row.get("name") or "").casefold() in direct_names
            )
        ]
        if not source_params.get("detailer_lora_enabled") or not str(source_params.get("detailer_lora") or "").strip():
            errors.append(_error(
                "adetailer_identity_lora_missing",
                "Identity protection is set to Detailer identity LoRA, but no detailer-only LoRA is selected.",
                field="detailer_lora",
            ))
        elif not direct_rows or not applied_direct:
            errors.append(_error(
                "adetailer_identity_lora_not_bound",
                "The selected identity LoRA was not resolved into the isolated ADetailer branch.",
                field="detailer_lora",
            ))

        revision_matches = declared_revision in {REVISION_ROUTE, REVISION_BOTH, family}
        if not revision_matches:
            errors.append(_error(
                "adetailer_identity_lora_revision_mismatch",
                "The declared identity-LoRA revision does not match the active Qwen Image Edit route.",
                field="identity_lora_revision",
                route_family=family,
                declared_revision=declared_revision,
            ))

        strategy = str(lora_plan.get("strategy") or "")
        loader_node_class = str(lora_plan.get("loader_node_class") or "")
        if strategy not in MODEL_ONLY_STRATEGIES or loader_node_class != "LoraLoaderModelOnly":
            errors.append(_error(
                "adetailer_qwen_identity_lora_loader_policy_invalid",
                "Qwen Edit identity LoRAs must use the compiler-owned model-only LoRA branch before Qwen model-sampling patches are reapplied.",
                field="detailer_lora",
                strategy=strategy,
                loader_node_class=loader_node_class,
            ))

        sampling_reapply_required = True
        identity_claim = "user_selected_lora_assistance_not_a_guarantee"
        warnings.append(_warning(
            "adetailer_identity_lora_compatibility_user_declared",
            "Neo verified the selected file against the live Comfy LoRA catalog and checked the declared Qwen revision, but it cannot prove the LoRA's identity-preservation quality without the exact model card and physical image validation.",
            field="identity_lora_revision",
            declared_revision=declared_revision,
        ))
    elif requested == IDENTITY_DEDICATED_MODEL:
        status = "dedicated_model_fallback"
        compatibility_evidence = "phase2_dedicated_checkpoint_contract"
        identity_claim = "separate_model_fallback_not_a_guarantee"
        if source != DEDICATED_CHECKPOINT_SOURCE:
            errors.append(_error(
                "adetailer_identity_dedicated_model_source_mismatch",
                "Identity protection is set to Dedicated detailer model, but Detailer model source is not a dedicated checkpoint.",
                field="model_source",
                model_source=source,
            ))
        warnings.append(_warning(
            "adetailer_dedicated_model_identity_validation_required",
            "A dedicated SDXL/SD1.5 detailer avoids native Qwen Edit resampling, but facial identity still requires visual validation and conservative denoise settings.",
        ))

    return {
        "schema_id": IDENTITY_POLICY_SCHEMA_ID,
        "ready": not errors,
        "applicable": True,
        "family": family,
        "requested": requested,
        "effective": effective,
        "status": status,
        "identity_claim": identity_claim,
        "sampling_reapply_required": sampling_reapply_required,
        "declared_lora_revision": declared_revision,
        "compatibility_evidence": compatibility_evidence,
        "model_source": source,
        "lora_loader_strategy": str(lora_plan.get("strategy") or ""),
        "lora_loader_node_class": str(lora_plan.get("loader_node_class") or ""),
        "errors": errors,
        "warnings": warnings,
    }


def public_identity_policy_metadata(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    source = plan if isinstance(plan, Mapping) else {}
    return {
        "schema_id": IDENTITY_POLICY_SCHEMA_ID,
        "ready": bool(source.get("ready", True)),
        "applicable": bool(source.get("applicable")),
        "family": str(source.get("family") or ""),
        "requested": normalize_identity_protection(source.get("requested")),
        "effective": normalize_identity_protection(source.get("effective")),
        "status": str(source.get("status") or ""),
        "identity_claim": str(source.get("identity_claim") or "not_guaranteed"),
        "sampling_reapply_required": bool(source.get("sampling_reapply_required")),
        "declared_lora_revision": normalize_identity_lora_revision(source.get("declared_lora_revision")),
        "compatibility_evidence": str(source.get("compatibility_evidence") or ""),
        "model_source": str(source.get("model_source") or ""),
        "lora_loader_strategy": str(source.get("lora_loader_strategy") or ""),
        "lora_loader_node_class": str(source.get("lora_loader_node_class") or ""),
        "sampling_reapply": deepcopy(source.get("sampling_reapply") or {}),
    }
