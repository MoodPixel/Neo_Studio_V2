from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from neo_app.image.sampling_guidance_registry import resolve_sampling_guidance_capability

SCHEMA = "neo.image.negative_prompt_eligibility.v1"
PHASE = "IP-2"

ACTIVE = "ACTIVE"
WEAK = "WEAK"
INACTIVE_CFG = "INACTIVE_CFG"
DISABLED_FAMILY = "DISABLED_FAMILY"
DISABLED_ROUTE = "DISABLED_ROUTE"
PROFILE_CONTROLLED = "PROFILE_CONTROLLED"

TERMINAL_DISABLED_STATES = {INACTIVE_CFG, DISABLED_FAMILY, DISABLED_ROUTE}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _variant_from_params(params: Mapping[str, Any]) -> str:
    for key in (
        "flux_variant",
        "variant",
        "krea2_variant",
        "z_image_variant",
        "qwen_variant",
        "model_variant",
    ):
        value = params.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _selected_model(model_name: Any, params: Mapping[str, Any]) -> str:
    if str(model_name or "").strip():
        return str(model_name).strip()
    for key in (
        "gguf_unet",
        "gguf_model",
        "diffusion_model",
        "model",
        "unet",
        "model_name",
    ):
        value = params.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _activation_value(
    policy: Mapping[str, Any],
    params: Mapping[str, Any],
) -> tuple[str | None, float | None, Any]:
    aliases = [str(item).strip() for item in (policy.get("activation_aliases") or []) if str(item).strip()]
    field = str(policy.get("activation_field") or "").strip()

    # Explicit semantic aliases (for example Qwen true_cfg) take priority over
    # the current provider-compatibility field (cfg). This lets the UI migrate
    # to clearer labels without making stale cfg values authoritative again.
    ordered_fields = [*aliases]
    if field and field not in ordered_fields:
        ordered_fields.append(field)

    for candidate in ordered_fields:
        if candidate not in params:
            continue
        raw = params.get(candidate)
        parsed = _as_float(raw)
        if parsed is not None:
            return candidate, parsed, raw
        if raw not in (None, ""):
            return candidate, None, raw
    return None, None, None


def resolve_negative_prompt_eligibility(
    family: Any,
    *,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve whether a negative prompt is executable for one Image route.

    IP-2 consumes IP-1 capability semantics and evaluates the current numeric
    guidance state. It never chooses sampler/step/resolution presets.
    """

    values = dict(params or {})
    capability = resolve_sampling_guidance_capability(
        family,
        loader=loader,
        mode=mode,
        variant=variant or _variant_from_params(values),
        model_name=_selected_model(model_name, values),
    )
    policy = dict(capability.get("negative_prompt") or {})
    policy_name = str(policy.get("policy") or "profile_controlled")
    guidance = dict(capability.get("guidance") or {})

    activation_field, activation_value, activation_raw = _activation_value(policy, values)
    hard_min = _as_float(policy.get("hard_min_exclusive"))
    weak_below = _as_float(policy.get("weak_below"))

    state = PROFILE_CONTROLLED
    should_send = True
    severity = "info"
    reason_code = "profile_controls_negative_prompt"
    message = "Negative prompt behavior is controlled by the selected provider or model profile."

    if policy_name == "disabled_by_family":
        state = DISABLED_FAMILY
        should_send = False
        severity = "muted"
        reason_code = "negative_prompt_disabled_by_family"
        message = "This model family does not execute classifier-free negative prompting on this route."
    elif policy_name == "disabled_by_route":
        state = DISABLED_ROUTE
        should_send = False
        severity = "muted"
        reason_code = "negative_prompt_disabled_by_route"
        message = "This route does not execute a True-CFG negative branch; model guidance is a separate control."
    elif policy_name == "cfg_gated":
        if activation_field is None:
            # Capability says the route supports negatives, but the request did
            # not provide the activation value. Do not invent a numeric preset in
            # IP-2. Preserve execution and let provider/default resolution own it.
            state = ACTIVE
            should_send = True
            severity = "info"
            reason_code = "activation_value_not_submitted"
            label = str(guidance.get("ui_label") or policy.get("activation_field") or "CFG")
            message = f"Negative prompting is supported. {label} was not explicitly submitted, so provider/default resolution remains authoritative."
        elif activation_value is None:
            state = ACTIVE
            should_send = True
            severity = "warning"
            reason_code = "activation_value_unparseable"
            message = f"Negative prompting is supported, but {activation_field} could not be parsed; the user value is retained rather than silently disabled."
        elif hard_min is not None and activation_value <= hard_min:
            state = INACTIVE_CFG
            should_send = False
            severity = "muted"
            reason_code = "cfg_not_above_hard_threshold"
            label = str(guidance.get("ui_label") or activation_field or "CFG")
            message = f"Negative prompting is inactive because {label} must be greater than {hard_min:g}."
        elif weak_below is not None and activation_value < weak_below:
            state = WEAK
            should_send = True
            severity = "warning"
            reason_code = "cfg_in_weak_negative_range"
            label = str(guidance.get("ui_label") or activation_field or "CFG")
            message = f"Negative prompting is active, but {label} below {weak_below:g} may produce only weak negative influence."
        else:
            state = ACTIVE
            should_send = True
            severity = "success"
            reason_code = "negative_prompt_active"
            label = str(guidance.get("ui_label") or activation_field or "CFG")
            message = f"Negative prompting is active through {label}."

    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "state": state,
        "severity": severity,
        "reason_code": reason_code,
        "message": message,
        "should_send_negative_prompt": should_send,
        "should_disable_ui": state in TERMINAL_DISABLED_STATES,
        "should_warn_ui": state in {WEAK, PROFILE_CONTROLLED},
        "user_text_retained": True,
        "family": capability.get("family"),
        "variant": capability.get("variant"),
        "loader": capability.get("loader"),
        "mode": capability.get("mode"),
        "known_family": capability.get("known_family"),
        "route_context_supported": capability.get("route_context_supported"),
        "guidance_kind": guidance.get("kind"),
        "guidance_field": guidance.get("field"),
        "guidance_label": guidance.get("ui_label"),
        "negative_prompt_policy": policy_name,
        "activation_field": activation_field or policy.get("activation_field"),
        "activation_value": activation_value,
        "activation_raw": activation_raw,
        "hard_min_exclusive": hard_min,
        "weak_below": weak_below,
        "graph_semantics": policy.get("graph_semantics"),
    }


def prepare_negative_prompt_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with an effective negative prompt and retained user input.

    The top-level ``negative_prompt`` becomes the value provider compilers may
    execute. The untouched user value is stored in ``params.negative_prompt_input``
    so disabled routes never destroy authoring state or replay provenance.
    """

    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return prepared

    params = dict(prepared.get("params") or {})
    prior_eligibility = params.get("negative_prompt_eligibility")
    has_prior_internal_state = isinstance(prior_eligibility, dict)
    submitted_negative = prepared.get("negative_prompt")

    if has_prior_internal_state and submitted_negative in (None, "") and "negative_prompt_input" in params:
        user_negative = str(params.get("negative_prompt_input") or "")
    else:
        user_negative = str(submitted_negative or "")

    variant = _variant_from_params(params)
    model_name = _selected_model(prepared.get("model"), params)
    eligibility = resolve_negative_prompt_eligibility(
        prepared.get("family"),
        loader=prepared.get("loader"),
        mode=prepared.get("mode"),
        variant=variant,
        model_name=model_name,
        params=params,
    )
    effective_negative = user_negative if eligibility["should_send_negative_prompt"] else ""

    params["negative_prompt_input"] = user_negative
    params["effective_negative_prompt"] = effective_negative
    params["negative_prompt_eligibility"] = eligibility
    params["negative_prompt_suppressed"] = bool(user_negative and not eligibility["should_send_negative_prompt"])
    prepared["params"] = params
    prepared["negative_prompt"] = effective_negative
    return prepared


def negative_prompt_eligibility_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "states": [ACTIVE, WEAK, INACTIVE_CFG, DISABLED_FAMILY, DISABLED_ROUTE, PROFILE_CONTROLLED],
        "hard_execution_rule": "cfg-gated negative prompting is inactive at <= 1.0",
        "weak_ux_rule": "cfg-gated negative prompting remains active but warns for > 1.0 and < 1.5",
        "retention_rule": "user negative text is retained even when the effective provider negative is empty",
        "authority": "IP-1 capability registry + IP-2 eligibility engine",
        "final_release_lock_phase": "IP-8",
        "inspector_owner": "neo_app.image.sampling_preset_inspector",
    }
