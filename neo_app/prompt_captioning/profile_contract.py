from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

PROFILE_SCHEMA_VERSION = "prompt_captioning.profile.v1"
MANIFEST_PATH = Path(__file__).with_name("profile_manifest.json")

_SHARED_DIMENSIONS = ("purpose", "visual_treatment", "grounding", "analysis_scope", "output_format")
_TASK_DIMENSIONS = ("target_media", "prompt_task", "edit_intent", "preservation_policy", "motion_profile", "camera_behavior")
_CUSTOM_DIMENSIONS = {
    "purpose",
    "visual_treatment",
    "output_format",
    "edit_intent",
    "preservation_policy",
    "motion_profile",
    "camera_behavior",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any) -> str:
    return _clean(value).lower().replace("/", "_").replace("-", "_").replace(" ", "_")


@lru_cache(maxsize=1)
def _manifest_cached() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not data.get("schema_version"):
        raise ValueError("Prompt/Captioning profile manifest is invalid or missing schema_version.")
    return data


def get_profile_manifest() -> dict[str, Any]:
    """Return the canonical Prompt/Captioning profile manifest.

    The JSON manifest is the single source of truth for option IDs, labels,
    defaults, help text, surface applicability, and legacy aliases. Callers get
    a deep copy so runtime mutation cannot corrupt the cached definition.
    """
    return deepcopy(_manifest_cached())


def get_profile_manifest_payload() -> dict[str, Any]:
    manifest = get_profile_manifest()
    return {
        "ok": True,
        "manifest": manifest,
        "schema_version": manifest.get("schema_version") or "",
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
    }


def _dimension_entries(manifest: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
    shared = _dict(manifest.get("shared_dimensions"))
    task = _dict(manifest.get("task_dimensions"))
    entries = shared.get(dimension) if dimension in shared else task.get(dimension)
    return [item for item in (entries or []) if isinstance(item, dict) and _clean(item.get("id"))]


def _dimension_ids(manifest: dict[str, Any], dimension: str) -> set[str]:
    return {_clean(item.get("id")) for item in _dimension_entries(manifest, dimension)}


def _aliases(manifest: dict[str, Any], dimension: str) -> dict[str, str]:
    raw = _dict(_dict(manifest.get("legacy_aliases")).get(dimension))
    return {_slug(key): _clean(value) for key, value in raw.items() if _clean(value)}


def resolve_profile_surface(tool_id: str = "", payload: dict[str, Any] | None = None, explicit_surface: str = "") -> str:
    explicit = _clean(explicit_surface)
    if explicit in {"prompt_studio", "caption_studio", "batch_dataset", "batch_library"}:
        return explicit
    payload = _dict(payload)
    tool = _clean(tool_id or payload.get("tool") or payload.get("tool_id"))
    inputs = _dict(payload.get("inputs"))
    params = _dict(payload.get("params"))
    if tool == "batch_captioning":
        workflow = _slug(inputs.get("workflow_mode") or params.get("workflow_mode"))
        if workflow in {"library", "save_to_library", "save_library", "library_caption"}:
            return "batch_library"
        return "batch_dataset"
    if tool in {"image_captioning", "result_image_captioning", "caption_studio"}:
        return "caption_studio"
    return "prompt_studio"


def _nested_caption_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(payload.get("params")).get("caption_settings"))


def _first(*values: Any) -> str:
    for value in values:
        text = _clean(value)
        if text:
            return text
    return ""


def _legacy_value(payload: dict[str, Any], dimension: str) -> str:
    inputs = _dict(payload.get("inputs"))
    params = _dict(payload.get("params"))
    settings = _nested_caption_settings(payload)
    metadata = _dict(payload.get("metadata"))
    if dimension == "purpose":
        return _first(inputs.get("purpose"), inputs.get("target_use"), params.get("purpose"), settings.get("purpose"), settings.get("target_use"), metadata.get("purpose"))
    if dimension == "visual_treatment":
        return _first(inputs.get("visual_treatment"), inputs.get("output_style"), inputs.get("style"), params.get("visual_treatment"), settings.get("visual_treatment"), settings.get("output_style"))
    if dimension == "grounding":
        return _first(inputs.get("grounding"), params.get("grounding"), settings.get("grounding"), metadata.get("grounding"))
    if dimension == "analysis_scope":
        return _first(inputs.get("analysis_scope"), params.get("analysis_scope"), params.get("caption_mode"), settings.get("analysis_scope"), settings.get("caption_mode"), params.get("component_type"), settings.get("component_type"))
    if dimension == "output_format":
        return _first(inputs.get("output_format"), params.get("output_format"), settings.get("output_format"), inputs.get("caption_style"), settings.get("caption_style"))
    if dimension == "target_media":
        return _first(inputs.get("target_media"), params.get("target_media"), metadata.get("target_media"))
    if dimension == "prompt_task":
        return _first(inputs.get("prompt_task"), params.get("prompt_task"), metadata.get("prompt_task"))
    if dimension == "edit_intent":
        return _first(inputs.get("edit_intent"), params.get("edit_intent"), metadata.get("edit_intent"))
    if dimension == "preservation_policy":
        return _first(inputs.get("preservation_policy"), params.get("preservation_policy"), metadata.get("preservation_policy"))
    if dimension == "motion_profile":
        return _first(inputs.get("motion_profile"), params.get("motion_profile"), metadata.get("motion_profile"))
    if dimension == "camera_behavior":
        return _first(inputs.get("camera_behavior"), params.get("camera_behavior"), metadata.get("camera_behavior"))
    return ""


def _normalize_dimension(
    manifest: dict[str, Any],
    dimension: str,
    requested: Any,
    fallback: str,
) -> tuple[str, str, bool]:
    raw = _clean(requested)
    ids = _dimension_ids(manifest, dimension)
    aliases = _aliases(manifest, dimension)
    if not raw:
        return fallback, "", False
    slug = _slug(raw)
    if slug in ids:
        return slug, "", False
    if slug in aliases and aliases[slug] in ids:
        return aliases[slug], "", True
    if dimension in _CUSTOM_DIMENSIONS and "custom" in ids:
        return "custom", raw, True
    return fallback, raw, True


def normalize_profile(
    profile: dict[str, Any] | None = None,
    *,
    payload: dict[str, Any] | None = None,
    tool_id: str = "",
    surface: str = "",
) -> dict[str, Any]:
    """Normalize the canonical profile while preserving legacy intent.

    P23 keeps legacy payload fields readable while deriving one authoritative
    profile alongside them. P23.3 activates that profile in Prompt Studio while
    preserving replay/preset compatibility for older records.
    """
    manifest = get_profile_manifest()
    payload = _dict(payload)
    requested = _dict(profile or payload.get("profile"))
    resolved_surface = resolve_profile_surface(tool_id, payload, surface or _clean(requested.get("surface")))
    defaults = deepcopy(_dict(_dict(manifest.get("surface_defaults")).get(resolved_surface)))
    if not defaults:
        defaults = deepcopy(_dict(_dict(manifest.get("surface_defaults")).get("caption_studio")))

    normalized: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "surface": resolved_surface,
    }
    migration: dict[str, Any] = {"used_legacy_aliases": [], "unmapped_values": {}}

    for dimension in (*_SHARED_DIMENSIONS, *_TASK_DIMENSIONS):
        fallback = _clean(defaults.get(dimension))
        legacy_value = _legacy_value(payload, dimension)
        if resolved_surface == "prompt_studio":
            legacy_style = _slug(_dict(payload.get("inputs")).get("style"))
            prompt_format_from_style = {
                "sdxl_tags": "sd_tags",
                "sd_tags": "sd_tags",
                "descriptive": "natural_prompt",
                "natural": "natural_prompt",
                "natural_prompt": "natural_prompt",
                "hybrid": "hybrid_prompt",
                "structured": "structured_prompt",
            }
            if dimension == "output_format" and legacy_style in prompt_format_from_style:
                legacy_value = prompt_format_from_style[legacy_style]
            elif dimension == "visual_treatment" and legacy_style in prompt_format_from_style:
                legacy_value = ""
        candidate = _first(requested.get(dimension), legacy_value, fallback)
        value, custom_value, migrated = _normalize_dimension(manifest, dimension, candidate, fallback)
        if value:
            normalized[dimension] = value
        if custom_value:
            normalized[f"custom_{dimension}"] = custom_value
            if value != "custom":
                migration["unmapped_values"][dimension] = custom_value
        if migrated and candidate:
            migration["used_legacy_aliases"].append({"dimension": dimension, "source": candidate, "resolved": value})

    # Free-text profile extensions are preserved without forcing a new taxonomy.
    for key in (
        "custom_purpose",
        "custom_visual_treatment",
        "custom_output_format",
        "custom_edit_intent",
        "custom_preservation_policy",
        "custom_motion_profile",
        "custom_camera_behavior",
        "trigger_token",
    ):
        explicit = _clean(requested.get(key))
        if explicit:
            normalized[key] = explicit

    locked = [str(item) for item in (defaults.get("locked") or []) if str(item).strip()]
    if locked:
        normalized["locked"] = locked
        for dimension in locked:
            if _clean(defaults.get(dimension)):
                normalized[dimension] = _clean(defaults.get(dimension))
                normalized.pop(f"custom_{dimension}", None)

    # Task consistency is authoritative. It prevents a video task from carrying
    # stale image target metadata and vice versa.
    task_entries = {item.get("id"): item for item in _dimension_entries(manifest, "prompt_task")}
    task = _dict(task_entries.get(normalized.get("prompt_task")))
    if task.get("target_media"):
        normalized["target_media"] = _clean(task.get("target_media"))
    if task.get("output_format"):
        normalized["output_format"] = _clean(task.get("output_format"))
        normalized.pop("custom_output_format", None)

    migration["used_legacy_aliases"] = [
        item for index, item in enumerate(migration["used_legacy_aliases"])
        if item not in migration["used_legacy_aliases"][:index]
    ]
    migration["migrated"] = bool(migration["used_legacy_aliases"] or migration["unmapped_values"])
    return {"profile": normalized, "migration": migration}


def profile_option(manifest: dict[str, Any], dimension: str, option_id: str) -> dict[str, Any]:
    wanted = _clean(option_id)
    return deepcopy(next((item for item in _dimension_entries(manifest, dimension) if _clean(item.get("id")) == wanted), {}))


def grounding_invariants(grounding: str) -> list[str]:
    """Hard visual-fidelity rules shared by all image-aware tasks.

    These are policy invariants, not UI option definitions; the selectable profile
    IDs and descriptions still come only from profile_manifest.json.
    """
    common = [
        "Do not invent names, relationships, occupations, brands, specific locations, or off-frame details.",
        "Do not assert nationality, ethnicity, exact age, or identity unless explicitly supplied by the user.",
        "Treat readable text as factual only when it is actually legible; otherwise omit it or mark it uncertain.",
        "When visual evidence is ambiguous, omit the claim instead of guessing.",
    ]
    if grounding == "strict":
        return ["Use only directly visible or explicitly supplied facts.", *common]
    if grounding == "balanced":
        return ["Stay grounded in visible facts; cautious broad visual interpretation is allowed when clearly supported.", *common]
    return [
        "Creative treatment may change presentation language, but concrete source facts must remain grounded.",
        *common,
    ]


def build_profile_instruction_blocks(profile: dict[str, Any], user_instruction: str = "", *, surface: str = "") -> dict[str, Any]:
    """Compile profile metadata into reusable instruction blocks.

    P23.2 consumes these blocks as the active instruction hierarchy for
    single-image Caption Studio and both Batch Captioning workflows.
    """
    manifest = get_profile_manifest()
    clean_profile = normalize_profile(profile, surface=surface or _clean(_dict(profile).get("surface"))).get("profile") or {}
    blocks: list[dict[str, str]] = []
    grounding = _clean(clean_profile.get("grounding") or "balanced")
    blocks.append({"kind": "grounding", "text": " ".join(grounding_invariants(grounding))})
    instruction = _clean(user_instruction)
    if instruction:
        blocks.append({
            "kind": "user_instruction",
            "text": "The user's instruction is the highest task-specific directive. Do not expand beyond its requested scope: " + instruction,
        })
    for dimension in ("purpose", "analysis_scope", "visual_treatment", "output_format"):
        option = profile_option(manifest, dimension, _clean(clean_profile.get(dimension)))
        if option.get("description"):
            blocks.append({"kind": dimension, "text": _clean(option.get("description"))})
    return {"profile": clean_profile, "blocks": blocks}
