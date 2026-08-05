from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from neo_app.image.sampling_guidance_registry import resolve_sampling_guidance_capability

SCHEMA = "neo.image.sampling_preset_inspector.v1"
PHASE = "IP-8"


def _variant(params: Mapping[str, Any]) -> str:
    for key in ("flux_variant", "variant", "krea2_variant", "z_image_variant", "qwen_variant", "model_variant"):
        value = params.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _model(payload: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    for value in (
        payload.get("model"), params.get("gguf_unet"), params.get("gguf_model"), params.get("diffusion_model"), params.get("model"), params.get("model_name")
    ):
        if str(value or "").strip():
            return str(value).strip()
    return ""


def build_sampling_preset_inspector(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = deepcopy(dict(payload or {}))
    params = dict(data.get("params") or {})
    variant = _variant(params)
    model_name = _model(data, params)
    capability = resolve_sampling_guidance_capability(
        data.get("family"), loader=data.get("loader"), mode=data.get("mode"), variant=variant, model_name=model_name
    ) if str(data.get("surface") or "").casefold() == "image" else {}
    guidance = capability.get("guidance") if isinstance(capability.get("guidance"), dict) else {}
    validation = params.get("sampling_preset_validation") if isinstance(params.get("sampling_preset_validation"), dict) else {}
    eligibility = params.get("negative_prompt_eligibility") if isinstance(params.get("negative_prompt_eligibility"), dict) else {}
    intent = params.get("output_intent_resolution") if isinstance(params.get("output_intent_resolution"), dict) else {}
    lock = params.get("sampling_preset_release_lock") if isinstance(params.get("sampling_preset_release_lock"), dict) else {}
    inheritance = params.get("sampling_preset_inheritance") if isinstance(params.get("sampling_preset_inheritance"), dict) else {}

    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "proof_level": "authoritative_payload_contract",
        "runtime_quality_proof": False,
        "panels": {
            "route": {
                "family": capability.get("family") or data.get("family"),
                "variant": capability.get("variant") or variant,
                "loader": capability.get("loader") or data.get("loader"),
                "workflow": capability.get("mode") or data.get("mode"),
                "model_name": model_name,
                "known_family": capability.get("known_family"),
                "route_context_supported": capability.get("route_context_supported"),
            },
            "preset": {
                "preset_id": params.get("sampling_preset_id"),
                "entry_id": params.get("sampling_preset_entry_id"),
                "source": params.get("sampling_preset_source"),
                "application_mode": params.get("sampling_preset_application_mode"),
                "read_only": params.get("sampling_preset_read_only"),
                "authoring_template": params.get("sampling_preset_authoring_template"),
                "applied_fields": list(params.get("sampling_preset_applied_values") or []),
                "validation_status": validation.get("status"),
                "complete": validation.get("complete"),
                "blocks_generation": validation.get("blocks_generation"),
                "missing": list(validation.get("missing") or []),
            },
            "inheritance": {
                "inherited": bool(inheritance.get("inherited")),
                "parent_preset_id": inheritance.get("parent_preset_id"),
                "parent_entry_id": inheritance.get("parent_entry_id"),
                "chain": list(inheritance.get("chain") or []),
                "dropped_fields": list(inheritance.get("drop_fields") or []),
            },
            "sampling_semantics": {
                "resolution_policy": params.get("sampling_preset_resolution_policy") or capability.get("resolution_policy"),
                "denoise_policy": params.get("sampling_preset_denoise_policy") or capability.get("denoise_policy"),
                "guidance_kind": guidance.get("kind"),
                "guidance_field": guidance.get("field"),
                "guidance_label": guidance.get("ui_label"),
                "effective_sampling": {
                    key: params.get(key)
                    for key in ("sampler", "scheduler", "steps", "cfg", "true_cfg", "flux_guidance", "guidance", "model_guidance", "width", "height", "denoise", "seed")
                    if params.get(key) not in (None, "")
                },
            },
            "output_intent": {
                "requested": intent.get("requested_intent"),
                "effective": intent.get("effective_intent") or params.get("output_intent"),
                "state": intent.get("state"),
                "mutated_fields": list(intent.get("mutated_fields") or []),
                "sampling_mutation": False,
            },
            "negative_prompt": {
                "state": eligibility.get("state"),
                "policy": eligibility.get("negative_prompt_policy"),
                "activation_field": eligibility.get("activation_field"),
                "activation_value": eligibility.get("activation_value"),
                "should_send": eligibility.get("should_send_negative_prompt"),
                "suppressed": bool(params.get("negative_prompt_suppressed")),
                "user_text_present": bool(params.get("negative_prompt_input")),
                "effective_text_present": bool(params.get("effective_negative_prompt")),
            },
            "release_lock": {
                "status": lock.get("status"),
                "locked": lock.get("locked"),
                "blocks_generation": lock.get("blocks_generation"),
                "failed_checks": [item.get("id") for item in (lock.get("failures") or [])],
            },
        },
        "notes": [
            "Inspector reports resolved contracts and payload state only.",
            "It does not claim GPU visual quality, leakage, or model-output proof without a real runtime generation.",
        ],
    }


def prepare_sampling_preset_inspector_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return prepared
    params = dict(prepared.get("params") or {})
    params["sampling_preset_inspector"] = build_sampling_preset_inspector({**prepared, "params": params})
    prepared["params"] = params
    return prepared


def sampling_preset_inspector_contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "panels": ["route", "preset", "inheritance", "sampling_semantics", "output_intent", "negative_prompt", "release_lock"],
        "proof_level": "authoritative_payload_contract",
        "runtime_quality_proof": False,
        "privacy": "negative prompt text is never copied into the inspector; presence booleans only",
    }
