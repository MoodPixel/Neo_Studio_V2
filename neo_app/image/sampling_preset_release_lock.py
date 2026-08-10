from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Any, Iterable, Mapping

from neo_app.image.negative_prompt_eligibility import (
    DISABLED_FAMILY,
    DISABLED_ROUTE,
    INACTIVE_CFG,
    resolve_negative_prompt_eligibility,
)
from neo_app.image.output_intents import output_intent_contract
from neo_app.image.sampling_presets import (
    DEFAULT_BALANCED_ID,
    EMPTY_CLEAN_SLATE_ID,
    PROVIDER_DEFAULTS_ID,
    load_builtin_sampling_presets,
    managed_sampling_fields,
    resolve_sampling_preset,
)

SCHEMA = "neo.image.sampling_preset_release_lock.v1"
PHASE = "IP-8"
LOCKED = "locked"
BLOCKED = "blocked"
NOT_APPLICABLE = "not_applicable"


class SamplingPresetReleaseLockError(ValueError):
    """Raised when an Image job violates the IP-8 sampling release contract."""


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _variant_from_params(params: Mapping[str, Any]) -> str:
    for key in ("flux_variant", "variant", "krea2_variant", "z_image_variant", "qwen_variant", "model_variant"):
        value = params.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _selected_model(payload: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    if str(payload.get("model") or "").strip():
        return str(payload.get("model")).strip()
    for key in ("gguf_unet", "gguf_model", "diffusion_model", "model", "unet", "model_name"):
        value = params.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _check(check_id: str, ok: bool, message: str, *, severity: str = "error", details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "ok": bool(ok),
        "status": "locked" if ok else "blocked",
        "severity": "success" if ok else severity,
        "message": message,
        "details": deepcopy(dict(details or {})),
    }


def _selector_values(value: Any) -> list[str]:
    if value in (None, "", "*"):
        return ["*"]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _entry_contexts(entry: Mapping[str, Any]) -> Iterable[dict[str, str]]:
    match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
    families = _selector_values(match.get("family"))
    variants = _selector_values(match.get("variant"))
    loaders = _selector_values(match.get("loader"))
    modes = _selector_values(match.get("mode"))
    intents = _selector_values(match.get("intent"))
    for family, variant, loader, mode, intent in product(families, variants, loaders, modes, intents):
        if "*" in {family, loader, mode}:
            continue
        yield {
            "family": family,
            "variant": "" if variant == "*" else variant,
            "loader": loader,
            "mode": mode,
            "intent": "none" if intent == "*" else intent,
        }


def _expected_negative_state(family: str) -> str | None:
    if family in {"flux", "flux1_fill", "flux2_klein"}:
        return DISABLED_ROUTE
    if family in {"krea2_turbo", "z_image_turbo"}:
        return DISABLED_FAMILY
    return None


def build_sampling_preset_regression_matrix() -> dict[str, Any]:
    """Materialize every concrete built-in Balanced route and record release invariants.

    The matrix is generated from the immutable registry instead of duplicating a
    second preset table in IP-8. Provider Defaults / Clean Slate are covered by
    dedicated release checks because their selectors are intentionally global.
    """

    registry = load_builtin_sampling_presets()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for entry in registry.get("presets") or []:
        if entry.get("preset_id") != DEFAULT_BALANCED_ID:
            continue
        for context in _entry_contexts(entry):
            resolved = resolve_sampling_preset(DEFAULT_BALANCED_ID, **context)
            effective = (resolved.get("entry") or {}).get("values") or {}
            negative = resolve_negative_prompt_eligibility(
                context["family"],
                loader=context["loader"],
                mode=context["mode"],
                variant=context["variant"],
                params=effective,
            )
            row_checks: list[dict[str, Any]] = []
            row_checks.append(_check("resolved", bool(resolved.get("resolved")), "Balanced preset resolves uniquely for this concrete route."))
            if context["mode"] in {"img2img", "edit", "inpaint", "outpaint"}:
                row_checks.append(_check(
                    "source_canvas_owns_resolution",
                    "width" not in effective and "height" not in effective,
                    "Image-conditioned workflows do not carry txt2img width/height into the effective preset.",
                ))
            family = context["family"]
            variant = context["variant"]
            if family == "flux2_klein":
                distilled = "distilled" in variant
                row_checks.append(_check(
                    "klein_base_distilled_isolation",
                    effective.get("steps") == (4 if distilled else 50)
                    and float(effective.get("flux_guidance", -999)) == (1.0 if distilled else 4.0),
                    "Klein Base and Distilled sampling values remain isolated.",
                    details={"variant": variant, "steps": effective.get("steps"), "flux_guidance": effective.get("flux_guidance")},
                ))
            if family == "flux" and context["mode"] in {"inpaint", "outpaint"}:
                is_components = context["loader"] == "diffusion_model" and variant in {"dev", ""}
                expected_guidance = 30.0 if is_components else 3.5
                expected_steps = 50 if is_components else 20
                row_checks.append(_check(
                    "flux_fill_vs_gguf_isolation",
                    float(effective.get("flux_guidance", -999)) == expected_guidance and int(effective.get("steps", -999)) == expected_steps,
                    "FLUX Components Fill and generic GGUF masked routes keep distinct sampling semantics.",
                    details={"loader": context["loader"], "steps": effective.get("steps"), "flux_guidance": effective.get("flux_guidance")},
                ))
            expected_negative = _expected_negative_state(family)
            if expected_negative:
                row_checks.append(_check(
                    "negative_policy_family_lock",
                    negative.get("state") == expected_negative,
                    "Negative-prompt eligibility matches the family/route contract.",
                    details={"expected": expected_negative, "actual": negative.get("state")},
                ))
            ok = all(item["ok"] for item in row_checks)
            row = {
                "entry_id": (resolved.get("entry") or {}).get("entry_id"),
                "context": context,
                "values": deepcopy(effective),
                "negative_state": negative.get("state"),
                "ok": ok,
                "checks": row_checks,
            }
            rows.append(row)
            if not ok:
                failures.append(row)
    return {
        "schema": "neo.image.sampling_preset_regression_matrix.v1",
        "phase": PHASE,
        "row_count": len(rows),
        "failure_count": len(failures),
        "ok": not failures,
        "rows": rows,
        "failures": failures,
    }


def audit_sampling_preset_release_contract() -> dict[str, Any]:
    registry = load_builtin_sampling_presets()
    matrix = build_sampling_preset_regression_matrix()
    intent = output_intent_contract()
    checks: list[dict[str, Any]] = []

    checks.append(_check("regression_matrix", bool(matrix.get("ok")), "All concrete Default · Balanced registry routes pass the IP-8 matrix.", details={"rows": matrix.get("row_count"), "failures": matrix.get("failure_count")}))
    effects_clean = all(not any((item.get("effects") or {}).get(key) for key in ("sampling_overrides", "prompt_additions", "negative_prompt_additions", "style_ids", "lora_ids", "embedding_ids", "extension_overrides")) for item in intent.get("intents") or [])
    checks.append(_check("output_intent_zero_effect", effects_clean, "Output Intent remains metadata-only and cannot mutate sampling/prompt/extension state."))
    checks.append(_check("intent_sampling_rows_disabled", (registry.get("contract") or {}).get("intent_specific_sampling_status") == "disabled_ip6", "Intent-specific sampling rows remain forbidden."))

    unresolved_klein = resolve_sampling_preset(DEFAULT_BALANCED_ID, family="flux2_klein", loader="diffusion_model", mode="txt2img", variant="flux2_klein")
    checks.append(_check("klein_generic_fails_closed", not unresolved_klein.get("resolved"), "Unresolved generic FLUX.2 Klein cannot receive a Balanced few-step/base recipe."))

    provider = resolve_sampling_preset(PROVIDER_DEFAULTS_ID, family="sdxl", loader="checkpoint", mode="txt2img")
    empty = resolve_sampling_preset(EMPTY_CLEAN_SLATE_ID, family="sdxl", loader="checkpoint", mode="txt2img")
    checks.append(_check("provider_defaults_empty", (provider.get("entry") or {}).get("values") == {}, "Provider Defaults contains no preset sampling values."))
    checks.append(_check("clean_slate_empty", (empty.get("entry") or {}).get("values") == {}, "Empty · Clean Slate contains no hidden sampling values."))

    blocked = [item for item in checks if not item["ok"]]
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "status": BLOCKED if blocked else LOCKED,
        "locked": not blocked,
        "blocks_release": bool(blocked),
        "checks": checks,
        "regression_matrix": {"schema": matrix.get("schema"), "row_count": matrix.get("row_count"), "failure_count": matrix.get("failure_count"), "ok": matrix.get("ok")},
        "failures": blocked,
    }


def evaluate_sampling_preset_release_lock(payload: Mapping[str, Any]) -> dict[str, Any]:
    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return {"schema": SCHEMA, "phase": PHASE, "status": NOT_APPLICABLE, "locked": True, "blocks_generation": False, "checks": []}

    params = dict(prepared.get("params") or {})
    preset_id = _token(params.get("sampling_preset_id"))
    if not preset_id:
        return {
            "schema": SCHEMA,
            "phase": PHASE,
            "status": NOT_APPLICABLE,
            "locked": True,
            "blocks_generation": False,
            "message": "No sampling preset was selected; legacy/manual Image jobs remain outside the IP-8 preset gate.",
            "checks": [],
        }

    checks: list[dict[str, Any]] = []
    validation = params.get("sampling_preset_validation") if isinstance(params.get("sampling_preset_validation"), dict) else {}
    checks.append(_check(
        "preset_submission_valid",
        not bool(validation.get("blocks_generation")),
        str(validation.get("message") or "Sampling preset submission passed validation."),
        details={"status": validation.get("status"), "missing": validation.get("missing") or []},
    ))

    managed = managed_sampling_fields()
    if preset_id == PROVIDER_DEFAULTS_ID:
        explicit = sorted(field for field in managed if field in params and params.get(field) not in (None, ""))
        checks.append(_check(
            "provider_defaults_preserve_explicit_values",
            True,
            "Provider Defaults preserves explicit user sampling values and delegates only missing fields to the provider/compiler.",
            details={"explicit_fields": explicit},
        ))

    if preset_id == EMPTY_CLEAN_SLATE_ID:
        applied = list(params.get("sampling_preset_applied_values") or [])
        checks.append(_check("clean_slate_no_hidden_values", not applied, "Clean Slate contributes zero preset values; only explicitly entered manual values may remain.", details={"applied_values": applied}))

    intent_resolution = params.get("output_intent_resolution") if isinstance(params.get("output_intent_resolution"), dict) else {}
    checks.append(_check(
        "output_intent_no_mutation",
        not (intent_resolution.get("mutated_fields") or []),
        "Output Intent did not mutate any sampling or creative fields.",
        details={"mutated_fields": intent_resolution.get("mutated_fields") or []},
    ))

    eligibility = params.get("negative_prompt_eligibility") if isinstance(params.get("negative_prompt_eligibility"), dict) else {}
    if eligibility:
        should_send = bool(eligibility.get("should_send_negative_prompt"))
        effective = str(params.get("effective_negative_prompt") or "")
        user = str(params.get("negative_prompt_input") or "")
        negative_ok = (should_send and effective == user) or ((not should_send) and effective == "")
        checks.append(_check(
            "negative_effective_state_consistent",
            negative_ok,
            "Effective negative text matches IP-2 eligibility while the user-authored draft remains retained.",
            details={"state": eligibility.get("state"), "should_send": should_send, "user_text_present": bool(user), "effective_text_present": bool(effective)},
        ))

    mode = str(prepared.get("mode") or "")
    if preset_id == DEFAULT_BALANCED_ID and mode in {"img2img", "edit", "inpaint", "outpaint"}:
        applied = set(params.get("sampling_preset_applied_values") or [])
        checks.append(_check("image_workflow_resolution_owner", not ({"width", "height"} & applied), "Image-conditioned Balanced presets leave source/expanded-canvas resolution ownership to the workflow."))

    blocked = [item for item in checks if not item["ok"]]
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "status": BLOCKED if blocked else LOCKED,
        "locked": not blocked,
        "blocks_generation": bool(blocked),
        "preset_id": preset_id,
        "family": prepared.get("family"),
        "variant": _variant_from_params(params),
        "loader": prepared.get("loader"),
        "mode": prepared.get("mode"),
        "model_name": _selected_model(prepared, params),
        "checks": checks,
        "failures": blocked,
    }


def prepare_sampling_preset_release_lock_payload(payload: Mapping[str, Any], *, raise_on_block: bool = True) -> dict[str, Any]:
    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return prepared
    report = evaluate_sampling_preset_release_lock(prepared)
    params = dict(prepared.get("params") or {})
    params["sampling_preset_release_lock"] = report
    prepared["params"] = params
    if raise_on_block and report.get("blocks_generation"):
        messages = [str(item.get("message") or item.get("id")) for item in report.get("failures") or []]
        raise SamplingPresetReleaseLockError("Sampling preset release lock blocked generation: " + " | ".join(messages))
    return prepared


def sampling_preset_release_lock_contract() -> dict[str, Any]:
    audit = audit_sampling_preset_release_contract()
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "status": audit.get("status"),
        "locked": audit.get("locked"),
        "matrix": audit.get("regression_matrix"),
        "job_boundary": "after IP-2 negative eligibility and before provider validation/compile",
        "legacy_no_preset_policy": "not_applicable_non_blocking",
        "fail_closed_conditions": [
            "unavailable_or_incomplete_selected_preset",
            "Provider Defaults retains stale managed values",
            "Clean Slate gains hidden preset values",
            "Output Intent mutates sampling/creative state",
            "negative effective state contradicts IP-2 eligibility",
            "image workflow Balanced preset carries txt2img width/height",
        ],
        "runtime_proof_policy": "contract_and_payload_invariants_only; no GPU quality proof is fabricated",
    }
