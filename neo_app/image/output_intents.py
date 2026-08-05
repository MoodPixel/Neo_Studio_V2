from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
import json

from neo_app.image.sampling_guidance_registry import (
    normalize_family,
    normalize_loader,
    normalize_mode,
    resolve_sampling_guidance_capability,
)


SCHEMA = "neo.image.output_intents.builtin.v1"
RESOLUTION_SCHEMA = "neo.image.output_intent_resolution.v1"
PHASE = "IP-6"
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "models" / "output_intents_builtin.json"

NONE_INTENT_ID = "none"
REALISTIC_INTENT_ID = "realistic"
ANIME_ILLUSTRATION_INTENT_ID = "anime_illustration"

_EFFECT_FIELDS = {
    "sampling_overrides": dict,
    "prompt_additions": list,
    "negative_prompt_additions": list,
    "style_ids": list,
    "lora_ids": list,
    "embedding_ids": list,
    "extension_overrides": dict,
}


def _token(value: Any) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_").replace("/", "_")
    return "_".join(part for part in raw.split("_") if part)


def _effects_empty(effects: Mapping[str, Any]) -> bool:
    return all(not effects.get(field) for field in _EFFECT_FIELDS)


def _validate_registry(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unexpected output-intent registry schema: {payload.get('schema')!r}")
    if payload.get("phase") != PHASE:
        raise ValueError(f"Unexpected output-intent registry phase: {payload.get('phase')!r}")

    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    if contract.get("sampling_mutation") != "forbidden_ip6":
        raise ValueError("IP-6 output intents must forbid sampling mutation.")

    ids: set[str] = set()
    aliases: dict[str, str] = {}
    for raw in payload.get("intents") or []:
        if not isinstance(raw, dict):
            raise ValueError("Output-intent entries must be objects.")
        entry_id = str(raw.get("entry_id") or "").strip()
        intent_id = _token(raw.get("intent_id"))
        if not entry_id or not intent_id:
            raise ValueError("Output-intent entries require entry_id and intent_id.")
        if intent_id in ids:
            raise ValueError(f"Duplicate output intent id: {intent_id}")
        ids.add(intent_id)
        if raw.get("source") != "built_in" or raw.get("immutable") is not True:
            raise ValueError(f"Built-in output intent {intent_id!r} must be immutable and source=built_in.")

        effects = raw.get("effects") if isinstance(raw.get("effects"), dict) else {}
        if set(effects) != set(_EFFECT_FIELDS):
            raise ValueError(
                f"Output intent {intent_id!r} must declare the exact IP-6 effect fields: {sorted(_EFFECT_FIELDS)}"
            )
        for field, expected_type in _EFFECT_FIELDS.items():
            if not isinstance(effects.get(field), expected_type):
                raise ValueError(f"Output intent {intent_id!r} effect {field!r} has the wrong type.")
        if not _effects_empty(effects):
            raise ValueError(
                f"Output intent {intent_id!r} attempts a creative/sampling mutation. IP-6 intents are metadata-only."
            )

        for candidate in [intent_id, *(raw.get("aliases") or [])]:
            alias = _token(candidate)
            if not alias:
                continue
            previous = aliases.get(alias)
            if previous and previous != intent_id:
                raise ValueError(f"Output-intent alias {alias!r} is ambiguous between {previous!r} and {intent_id!r}.")
            aliases[alias] = intent_id

    required = {NONE_INTENT_ID, REALISTIC_INTENT_ID, ANIME_ILLUSTRATION_INTENT_ID}
    missing = sorted(required - ids)
    if missing:
        raise ValueError(f"IP-6 output-intent registry is missing required intents: {missing}")


@lru_cache(maxsize=1)
def load_builtin_output_intents() -> dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    _validate_registry(payload)
    return payload


@lru_cache(maxsize=1)
def _intent_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for raw in load_builtin_output_intents().get("intents") or []:
        intent_id = _token(raw.get("intent_id"))
        for candidate in [intent_id, *(raw.get("aliases") or [])]:
            token = _token(candidate)
            if token:
                aliases[token] = intent_id
    return aliases


def normalize_output_intent_id(value: Any, *, unknown_passthrough: bool = False) -> str:
    token = _token(value)
    if not token:
        return NONE_INTENT_ID
    canonical = _intent_alias_map().get(token)
    if canonical:
        return canonical
    return token if unknown_passthrough else NONE_INTENT_ID


def _intent_entry(intent_id: str) -> dict[str, Any] | None:
    canonical = normalize_output_intent_id(intent_id, unknown_passthrough=True)
    for raw in load_builtin_output_intents().get("intents") or []:
        if _token(raw.get("intent_id")) == canonical:
            return deepcopy(raw)
    return None


def _context(
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> dict[str, Any]:
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
        "known_family": bool(capability.get("known_family")),
        "route_context_supported": bool(capability.get("route_context_supported", True)),
    }


def resolve_output_intent(
    intent_id: Any,
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> dict[str, Any]:
    requested = _token(intent_id) or NONE_INTENT_ID
    canonical = normalize_output_intent_id(requested, unknown_passthrough=True)
    entry = _intent_entry(canonical)
    context = _context(family=family, loader=loader, mode=mode, variant=variant, model_name=model_name)

    if entry is None:
        none_entry = _intent_entry(NONE_INTENT_ID)
        return {
            "schema": RESOLUTION_SCHEMA,
            "phase": PHASE,
            "resolved": False,
            "requested_intent": requested,
            "effective_intent": NONE_INTENT_ID,
            "state": "unknown_neutralized",
            "entry_id": (none_entry or {}).get("entry_id"),
            "label": (none_entry or {}).get("label", "None"),
            "source": "built_in",
            "immutable": True,
            "context": context,
            "effects": deepcopy((none_entry or {}).get("effects") or {}),
            "mutated_fields": [],
            "blocks_generation": False,
            "warnings": [f"Unknown output intent {requested!r}; Neo neutralized it to None without changing prompts or sampling values."],
        }

    effects = deepcopy(entry.get("effects") or {})
    return {
        "schema": RESOLUTION_SCHEMA,
        "phase": PHASE,
        "resolved": True,
        "requested_intent": requested,
        "effective_intent": str(entry.get("intent_id") or NONE_INTENT_ID),
        "state": str(entry.get("state") or "advisory_only"),
        "entry_id": entry.get("entry_id"),
        "label": entry.get("label"),
        "description": entry.get("description"),
        "source": entry.get("source"),
        "immutable": bool(entry.get("immutable")),
        "context": context,
        "effects": effects,
        "mutated_fields": [],
        "blocks_generation": False,
        "warnings": [],
    }


def list_output_intents(
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in load_builtin_output_intents().get("intents") or []:
        resolved = resolve_output_intent(
            raw.get("intent_id"),
            family=family,
            loader=loader,
            mode=mode,
            variant=variant,
            model_name=model_name,
        )
        items.append({
            "intent_id": resolved.get("effective_intent"),
            "entry_id": resolved.get("entry_id"),
            "label": resolved.get("label"),
            "description": resolved.get("description"),
            "state": resolved.get("state"),
            "source": resolved.get("source"),
            "immutable": resolved.get("immutable"),
            "sampling_mutation": False,
            "prompt_mutation": False,
            "context": deepcopy(resolved.get("context") or {}),
        })
    return items


def apply_output_intent(
    params: Mapping[str, Any],
    intent_id: Any,
    *,
    family: Any,
    loader: Any = "",
    mode: Any = "txt2img",
    variant: Any = "",
    model_name: Any = "",
) -> dict[str, Any]:
    """Record output intent without mutating any creative or sampling controls."""

    clean = deepcopy(dict(params or {}))
    resolved = resolve_output_intent(
        intent_id,
        family=family,
        loader=loader,
        mode=mode,
        variant=variant,
        model_name=model_name,
    )
    clean.pop("sampling_intent", None)
    clean.pop("image_intent", None)
    clean["output_intent"] = str(resolved.get("effective_intent") or NONE_INTENT_ID)
    clean["output_intent_resolution"] = resolved
    return clean


def prepare_output_intent_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Image output intent before sampling presets are resolved.

    IP-6 is intentionally metadata-only. This function may canonicalize intent
    fields and attach resolution metadata, but it must not alter prompts,
    negative prompts, extensions, or any sampling field.
    """

    prepared = deepcopy(dict(payload or {}))
    if str(prepared.get("surface") or "").strip().casefold() != "image":
        return prepared

    params = deepcopy(dict(prepared.get("params") or {}))
    requested = params.get("output_intent")
    if requested in (None, ""):
        requested = params.get("sampling_intent")
    if requested in (None, ""):
        requested = params.get("image_intent")
    if requested in (None, ""):
        requested = NONE_INTENT_ID

    variant = str(params.get("variant") or params.get("flux_variant") or params.get("krea2_variant") or "")
    model_name = str(
        prepared.get("model")
        or params.get("model")
        or params.get("diffusion_model")
        or params.get("gguf_model")
        or params.get("gguf_unet")
        or ""
    )
    prepared["params"] = apply_output_intent(
        params,
        requested,
        family=prepared.get("family"),
        loader=prepared.get("loader"),
        mode=prepared.get("mode"),
        variant=variant,
        model_name=model_name,
    )
    return prepared


def output_intent_contract() -> dict[str, Any]:
    payload = load_builtin_output_intents()
    return {
        "schema": payload.get("schema"),
        "phase": PHASE,
        "version": payload.get("version"),
        "contract": deepcopy(payload.get("contract") or {}),
        "intents": [
            {
                "intent_id": item.get("intent_id"),
                "entry_id": item.get("entry_id"),
                "label": item.get("label"),
                "description": item.get("description", ""),
                "aliases": list(item.get("aliases") or []),
                "state": item.get("state"),
                "immutable": bool(item.get("immutable")),
                "effects": deepcopy(item.get("effects") or {}),
            }
            for item in payload.get("intents") or []
        ],
        "sampling_mutation_status": "forbidden_ip6",
        "ui_status": "selector_active_ip7",
        "final_release_lock_phase": "IP-8",
        "ui_phase": "IP-7",
    }
